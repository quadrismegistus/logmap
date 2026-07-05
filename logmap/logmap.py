"""Hierarchical context-manager logger with multiprocess mapping."""

from __future__ import annotations

import contextvars
import functools
import inspect
import json
import logging
import multiprocessing as mp
import os
import platform
import random
import sys
import threading
import time
import traceback as _traceback
from collections import deque
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Callable, Iterable, Iterator

from humanfriendly import format_timespan
from tqdm.auto import tqdm


# ---------------------------------------------------------------------------
# Module config
# ---------------------------------------------------------------------------

def _default_mp_context() -> str:
    # "fork" deadlocks when the parent process has threads — the reason
    # CPython 3.14 moved its own Linux default to forkserver. Windows only
    # supports spawn.
    return "spawn" if platform.system() == "Windows" else "forkserver"


CONTEXT = _default_mp_context()

_cpu = mp.cpu_count()
DEFAULT_NUM_PROC = 1 if _cpu <= 1 else (2 if _cpu <= 3 else _cpu - 2)

# Level dispatch — int values follow stdlib logging convention
LEVELS = {
    "TRACE": 5,
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}

_LEVEL_ALIASES = {"WARN": "WARNING", "FATAL": "CRITICAL"}


def _normalize_level(level: str) -> str:
    name = level.upper()
    name = _LEVEL_ALIASES.get(name, name)
    if name not in LEVELS:
        raise ValueError(
            f"unknown log level {level!r}; expected one of {sorted(LEVELS)} "
            f"(aliases: {sorted(_LEVEL_ALIASES)})"
        )
    return name


# ANSI color codes used in output
_RESET = "\033[0m"
_CYAN = "\033[0;36m"
TOP_CHAR = "⎾"
BOTTOM_CHAR = "⎿"
VERTICAL_CHAR = " "

LEVEL_COLORS = {
    "TRACE":    "\033[0;36m",
    "DEBUG":    "\033[1;34m",
    "INFO":     "\033[1;32m",
    "WARNING":  "\033[1;33m",
    "ERROR":    "\033[1;31m",
    "CRITICAL": "\033[1;35m",
}

# Format string — understood placeholders: {color} {msg} {reset} {cyan} {time} {level}
DEFAULT_FORMAT = "{color}{msg}{reset}{cyan} @ {time}{reset}"

# Legacy palette used by iter_progress's tqdm bar_format
COLORS = {
    "default": "\033[0;39m",
    "light-blue": "\033[0;34m",
    "light-cyan": "\033[0;36m",
    "light-yellow": "\033[0;33m",
    "light-magenta": "\033[0;35m",
}


# ---------------------------------------------------------------------------
# Context-local nesting state + shared output config
# ---------------------------------------------------------------------------

# ContextVars are isolated per thread AND per asyncio task, so concurrent
# tasks in one event loop each see their own nesting depth / quiet flag.
_num_logwatches: contextvars.ContextVar = contextvars.ContextVar(
    "logmap_num_logwatches", default=0
)
_is_quiet: contextvars.ContextVar = contextvars.ContextVar(
    "logmap_is_quiet", default=False
)

_lock = threading.Lock()

# Output config — module-level, shared across threads, guarded by _lock
_sink = sys.stderr
_min_level = LEVELS["DEBUG"]
_format = DEFAULT_FORMAT
_opened_file = None
_colorize = False
_logger = None
_structured = False


def _refresh_colorize() -> None:
    global _colorize
    if os.environ.get("NO_COLOR"):
        _colorize = False
        return
    if os.environ.get("FORCE_COLOR"):
        _colorize = True
        return
    try:
        _colorize = bool(_sink.isatty())
    except (AttributeError, ValueError):
        _colorize = False


_refresh_colorize()

_UNSET = object()


def _emit(msg: str, level: str = "DEBUG", extra: dict | None = None) -> None:
    """Write one formatted log line to the current sink."""
    level = _normalize_level(level)
    lvl_int = LEVELS[level]
    with _lock:
        min_level, logger, structured = _min_level, _logger, _structured
        fmt, colorize = _format, _colorize
    if lvl_int < min_level:
        return

    if logger is not None:
        clean = extra.get("msg", msg) if extra else msg
        # "msg" would collide with a LogRecord attribute; the rest
        # (depth/task/event/duration) are safe as record attributes.
        log_extra = {k: v for k, v in extra.items() if k != "msg"} if extra else None
        logger.log(lvl_int, clean, extra=log_extra)
        return

    n = datetime.now()
    if structured:
        record = {"ts": n.isoformat(), "level": level, "msg": msg}
        if extra:
            record.update(extra)
        line = json.dumps(record, default=str)
    else:
        ts = (f"{n.year:04d}-{n.month:02d}-{n.day:02d} "
              f"{n.hour:02d}:{n.minute:02d}:{n.second:02d},{n.microsecond // 1000:03d}")
        if colorize:
            line = fmt.format(
                color=LEVEL_COLORS.get(level, ""),
                msg=msg, reset=_RESET, cyan=_CYAN, time=ts, level=level,
            )
        else:
            line = fmt.format(color="", msg=msg, reset="", cyan="", time=ts, level=level)

    # tqdm.write clears any active progress bars before writing, so log
    # lines don't garble a bar that shares the stream.
    with _lock:
        tqdm.write(line, file=_sink)


def configure(sink=_UNSET, level=None, format=None, logger=_UNSET, structured=None):
    """Reconfigure where logmap writes output.

    Omitted args keep their current value.

    Args:
        sink: writable stream (``sys.stdout``, ``StringIO``, open file),
            file path string (``"run.log"``), or ``None`` to reset to stderr.
        level: level name (``"INFO"``, aliases ``"WARN"``/``"FATAL"``
            accepted) or int (``20``); messages below the threshold are
            suppressed. Unknown names raise :class:`ValueError`.
        format: format string with ``{color}``, ``{msg}``, ``{reset}``,
            ``{cyan}``, ``{time}``, ``{level}`` placeholders — validated
            immediately; anything else raises :class:`ValueError`.
            See :data:`DEFAULT_FORMAT`.
        logger: a :class:`logging.Logger`; when set, output is forwarded via
            ``logger.log()`` (with ``depth``/``task`` attached as record
            attributes) instead of writing to the sink directly, and takes
            precedence over ``structured``. Pass ``None`` to clear.
        structured: if ``True``, emit JSON-lines output instead of
            human-readable text.  Each line is a JSON object with ``ts``,
            ``level``, ``msg``, ``depth``, and ``task`` keys; task
            open/close lines also carry ``event`` (``"start"``/``"end"``)
            and, on end, ``duration``.
    """
    global _sink, _min_level, _format, _opened_file, _colorize, _logger, _structured
    if format is not None:
        try:
            format.format(color="", msg="", reset="", cyan="", time="", level="")
        except (KeyError, IndexError) as e:
            raise ValueError(
                f"invalid format string {format!r}: only {{color}}, {{msg}}, "
                f"{{reset}}, {{cyan}}, {{time}}, {{level}} placeholders are "
                f"supported ({e!r})"
            ) from None
    if level is not None and isinstance(level, str):
        level = LEVELS[_normalize_level(level)]
    with _lock:
        if sink is not _UNSET:
            if _opened_file is not None:
                try:
                    _opened_file.close()
                except Exception:
                    pass
                _opened_file = None
            if sink is None:
                _sink = sys.stderr
            elif isinstance(sink, str):
                _opened_file = open(sink, "a", encoding="utf-8", buffering=1)
                _sink = _opened_file
            else:
                _sink = sink
            _refresh_colorize()
        if level is not None:
            _min_level = int(level)
        if format is not None:
            _format = format
        if logger is not _UNSET:
            _logger = logger
            if logger is not None and logging.getLevelName(LEVELS["TRACE"]).startswith("Level"):
                logging.addLevelName(LEVELS["TRACE"], "TRACE")
        if structured is not None:
            _structured = bool(structured)


def get_config() -> dict:
    """Return a snapshot of the current output configuration."""
    with _lock:
        return {
            "sink": _sink,
            "level": _min_level,
            "format": _format,
            "logger": _logger,
            "structured": _structured,
            "colorize": _colorize,
        }


# ---------------------------------------------------------------------------
# Parallel map helpers
# ---------------------------------------------------------------------------

class _PmapError:
    """Wrapper carrying a worker exception back to the parent process."""

    __slots__ = ("exc",)

    def __init__(self, exc: BaseException):
        self.exc = exc


def _pmap_do(inp):
    func, obj, args, kwargs = inp
    return func(obj, *args, **kwargs)


def _pmap_do_safe(inp):
    func, obj, args, kwargs = inp
    try:
        return func(obj, *args, **kwargs)
    except Exception as exc:
        return _PmapError(exc)


def _auto_chunksize(n_items: int, num_proc: int) -> int:
    if num_proc <= 1 or n_items <= 0:
        return 1
    return max(1, n_items // (num_proc * 4))


def pmap_iter(
    func: Callable,
    objs: Iterable,
    args: tuple = (),
    kwargs: dict | None = None,
    lim: int | None = None,
    num_proc: int | None = DEFAULT_NUM_PROC,
    progress: bool = True,
    progress_pos: int = 0,
    desc: str | None = None,
    shuffle: bool = False,
    context: str = CONTEXT,
    chunksize: int | None = None,
    ordered: bool = True,
    on_error: str = "raise",
) -> Iterator:
    """Yield func(obj) for each obj in objs, optionally in parallel.

    Does not mutate the caller's input. Uses ``multiprocessing.Pool(num_proc)``
    when ``num_proc > 1`` and there is more than one item.

    Args:
        shuffle: process items in random order. Note that results then no
            longer align with input order, and combined with ``lim`` this
            processes a random *sample* (shuffling happens before
            truncation).
        ordered: if ``False``, yield results as they complete
            (``imap_unordered``) rather than in input order.
        on_error: what to do when ``func`` raises for an item —
            ``"raise"`` (default) propagates the exception, ``"skip"``
            drops the item, ``"return"`` yields the exception object in
            the item's place.
    """
    # Validate and snapshot everything eagerly, so errors surface at call
    # time rather than on first iteration of the returned generator.
    if on_error not in ("raise", "skip", "return"):
        raise ValueError(f"on_error must be 'raise', 'skip', or 'return', got {on_error!r}")
    kwargs = dict(kwargs) if kwargs else {}
    args = tuple(args)

    items = list(objs)
    if shuffle:
        items = random.sample(items, k=len(items))
    if lim is not None:
        items = items[:lim]

    num_cpu = mp.cpu_count()
    if num_proc is None or num_proc < 1:
        num_proc = 1
    if num_proc > num_cpu:
        num_proc = num_cpu
    if num_proc > len(items):
        num_proc = max(1, len(items))

    if not desc:
        desc = f"Mapping {func.__name__}()"
    if num_cpu > 1 and num_proc > 1:
        desc = f"{desc} [x{num_proc}]"

    return _pmap_iter_run(
        func, items, args, kwargs, num_proc, progress, progress_pos,
        desc, context, chunksize, ordered, on_error,
    )


def _pmap_iter_run(
    func, items, args, kwargs, num_proc, progress, progress_pos,
    desc, context, chunksize, ordered, on_error,
) -> Iterator:
    n_items = len(items)
    if num_proc > 1 and n_items > 1:
        worker = _pmap_do if on_error == "raise" else _pmap_do_safe
        payload = ((func, obj, args, kwargs) for obj in items)
        cs = chunksize if chunksize else _auto_chunksize(n_items, num_proc)
        with mp.get_context(context).Pool(num_proc) as pool:
            mapper = pool.imap if ordered else pool.imap_unordered
            iterr = mapper(worker, payload, chunksize=cs)
            if progress:
                iterr = tqdm(iterr, total=n_items, desc=desc, position=progress_pos)
            for res in iterr:
                if isinstance(res, _PmapError):
                    if on_error == "skip":
                        continue
                    yield res.exc
                else:
                    yield res
    else:
        iterr = tqdm(items, desc=desc, position=progress_pos) if progress else items
        for obj in iterr:
            if on_error == "raise":
                yield func(obj, *args, **kwargs)
            else:
                try:
                    yield func(obj, *args, **kwargs)
                except Exception as exc:
                    if on_error == "return":
                        yield exc


def pmap(*a, **kw) -> list:
    """List-returning version of :func:`pmap_iter`."""
    return list(pmap_iter(*a, **kw))


def pmap_run(*a, **kw) -> None:
    """Exhaust :func:`pmap_iter` for side effects."""
    for _ in pmap_iter(*a, **kw):
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def padmin(xstr: Any, lim: int = 40) -> str:
    xstr = str(xstr)
    return xstr + (" " * (lim - len(xstr))) if len(xstr) < lim else xstr[:lim]


def shuffled(items: Iterable) -> list:
    items = list(items)
    return random.sample(items, k=len(items))


def _short_repr(obj: Any, maxlen: int = 80) -> str:
    r = repr(obj).replace("\n", " ")
    return r[:maxlen - 3] + "..." if len(r) > maxlen else r


def _format_call(func: Callable, args: tuple, kwargs: dict, maxlen: int = 60) -> str:
    name = func.__qualname__
    fn_args = list(args)
    try:
        code = func.__code__
        if code.co_varnames and code.co_varnames[0] in ("self", "cls"):
            fn_args = fn_args[1:]
    except AttributeError:
        pass
    parts = [_short_repr(a, 20) for a in fn_args]
    parts.extend(f"{k}={_short_repr(v, 20)}" for k, v in kwargs.items())
    params = ", ".join(parts)
    if len(params) > maxlen:
        params = params[:maxlen - 3] + "..."
    return f"{name}({params})"


# ---------------------------------------------------------------------------
# The logmap class
# ---------------------------------------------------------------------------

class _LogmapMeta(type):
    @property
    def is_quiet(cls) -> bool:
        return _is_quiet.get()

    @is_quiet.setter
    def is_quiet(cls, value: bool) -> None:
        _is_quiet.set(bool(value))


class logmap(metaclass=_LogmapMeta):
    """Monitor and log the duration of a task, with hierarchical indentation.

    Typical use is as a context manager::

        with logmap("step") as lm:
            lm.log("doing work")

    but it can also be used standalone (just a logger) or with explicit
    :meth:`start` / :meth:`stop` calls.
    """

    # -- global enable/disable ------------------------------------------------

    @staticmethod
    @contextmanager
    def quiet():
        token = _is_quiet.set(True)
        try:
            yield
        finally:
            _is_quiet.reset(token)

    @staticmethod
    @contextmanager
    def loud():
        token = _is_quiet.set(False)
        try:
            yield
        finally:
            _is_quiet.reset(token)

    disabled = quiet
    enabled = loud

    @staticmethod
    def enable() -> None:
        _is_quiet.set(False)

    @staticmethod
    def disable() -> None:
        _is_quiet.set(True)

    @staticmethod
    @contextmanager
    def verbosity(level: int = 1):
        """Context manager: logging on when ``level`` is truthy, off otherwise."""
        token = _is_quiet.set(not level)
        try:
            yield
        finally:
            _is_quiet.reset(token)

    # -- function decorator ---------------------------------------------------

    @staticmethod
    def fn(_func=None, *, level="DEBUG", log_args=True, log_return=True):
        """Decorator that wraps a function call in a logmap context.

        Works with sync, async, generator, and async-generator functions::

            @logmap.fn
            def process(x): ...

            @logmap.fn(level="INFO")
            async def fetch(url): ...

        For (async) generator functions the context stays open across
        iteration, so the logged duration covers consumption, not just
        creation.
        """
        def decorator(func):
            def describe(args, kwargs):
                return (_format_call(func, args, kwargs) if log_args
                        else func.__qualname__ + "()")

            if inspect.isasyncgenfunction(func):
                @functools.wraps(func)
                async def wrapper(*args, **kwargs):
                    async with logmap(describe(args, kwargs), level=level):
                        async for item in func(*args, **kwargs):
                            yield item
                return wrapper
            if inspect.iscoroutinefunction(func):
                @functools.wraps(func)
                async def wrapper(*args, **kwargs):
                    async with logmap(describe(args, kwargs), level=level) as lm:
                        result = await func(*args, **kwargs)
                        if log_return and result is not None:
                            lm.log(f">>> {_short_repr(result)}")
                        return result
                return wrapper
            if inspect.isgeneratorfunction(func):
                @functools.wraps(func)
                def wrapper(*args, **kwargs):
                    with logmap(describe(args, kwargs), level=level) as lm:
                        result = yield from func(*args, **kwargs)
                        if log_return and result is not None:
                            lm.log(f">>> {_short_repr(result)}")
                        return result
                return wrapper

            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                with logmap(describe(args, kwargs), level=level) as lm:
                    result = func(*args, **kwargs)
                    if log_return and result is not None:
                        lm.log(f">>> {_short_repr(result)}")
                    return result
            return wrapper
        if _func is not None:
            return decorator(_func)
        return decorator

    # -- init -----------------------------------------------------------------

    def __init__(
        self,
        name: str = "running task",
        level: str = "DEBUG",
        min_seconds_logworthy: float | None = None,
        precision: int = 1,
        announce: bool = True,
    ):
        self.started: float | None = None
        self.ended: float | None = None
        self.announce = announce
        self.level = _normalize_level(level)
        self.task_name = name
        self.min_seconds_logworthy = min_seconds_logworthy
        self.vertical_char = VERTICAL_CHAR
        self.top_char = TOP_CHAR
        self.bottom_char = BOTTOM_CHAR
        self.last_lap: float | None = None
        self.pbar = None
        self.num_proc: int | None = None
        self.precision = precision
        self.iterated_num = False
        self.num = 0
        self._pending_open = False

    # -- is_quiet (instance-level, delegates to context-local) ----------------

    @property
    def is_quiet(self) -> bool:
        return _is_quiet.get()

    @is_quiet.setter
    def is_quiet(self, value: bool) -> None:
        _is_quiet.set(bool(value))

    # -- log ------------------------------------------------------------------

    def log(
        self,
        msg: Any,
        pref: str | None = None,
        inner_pref: bool = True,
        level: str | None = None,
        linelim: int | None = None,
        exc_info: bool = False,
    ) -> None:
        """Log a message at the current nesting depth.

        Args:
            exc_info: if ``True`` (only meaningful inside an ``except``
                block), append the active exception's traceback.
        """
        if level is not None:
            level = _normalize_level(level)
        if _is_quiet.get() or msg is None or msg == "":
            return
        self._flush_pending_open()
        msg = padmin(msg, linelim) if linelim else msg
        tb = _traceback.format_exc().rstrip() if exc_info else None
        prefix = (self.inner_pref if inner_pref else self.pref) if pref is None else pref
        extra = {"depth": self.num, "task": self.task_name, "msg": msg}
        if tb:
            extra["traceback"] = tb
        text = f"{prefix}{msg}" + (f"\n{tb}" if tb else "")
        _emit(text, level=level or self.level, extra=extra)
        # A live progress bar also shows the message as its description,
        # preserving the pre-0.4 behavior on top of the emitted line.
        if self.pbar is not None:
            self.set_progress_desc(msg)

    def warning(self, *a, **kw) -> None:
        return self.log(*a, **{**kw, "level": "warning"})

    def trace(self, *a, **kw) -> None:
        return self.log(*a, **{**kw, "level": "trace"})

    def error(self, *a, **kw) -> None:
        return self.log(*a, **{**kw, "level": "error"})

    def info(self, *a, **kw) -> None:
        return self.log(*a, **{**kw, "level": "info"})

    def debug(self, *a, **kw) -> None:
        return self.log(*a, **{**kw, "level": "debug"})

    # -- iteration ------------------------------------------------------------

    def iter_progress(
        self,
        iterator: Iterable,
        desc: str = "iterating",
        pref: str | None = None,
        position: int = 0,
        total: int | None = None,
        progress: bool = True,
        shuffle: bool = False,
        **kwargs,
    ) -> Iterator:
        bar_format = "%s{l_bar}%s{bar}%s{r_bar}" % (
            LEVEL_COLORS.get(self.level, ""),
            COLORS["light-cyan"],
            COLORS["light-cyan"],
        )
        desc = f'{self.inner_pref if pref is None else pref}{desc if desc is not None else "iterating"}'
        self.pbar = tqdm(
            shuffled(iterator) if shuffle else iterator,
            desc=desc,
            position=position,
            total=total,
            bar_format=bar_format,
            disable=not progress or _is_quiet.get(),
            **kwargs,
        )
        try:
            yield from self.pbar
        finally:
            self.pbar.close()
            self.pbar = None

    def progress(self, iterable: Iterable, desc: str = "iterating", **kwargs) -> Iterator:
        """Iterate with a progress bar at the current nesting depth.

        Alias for :meth:`iter_progress` with a shorter name::

            with logmap("training") as lm:
                for batch in lm.progress(batches, desc="epochs"):
                    ...
        """
        return self.iter_progress(iterable, desc=desc, **kwargs)

    def imap(
        self,
        func: Callable,
        objs: Iterable,
        args: tuple = (),
        kwargs: dict | None = None,
        lim: int | None = None,
        num_proc: int | None = None,
        desc: str | None = None,
        shuffle: bool = False,
        context: str = CONTEXT,
        progress: bool = True,
        **pmap_kwargs,
    ) -> Iterator:
        items = list(objs)
        if lim is not None:
            items = items[:lim]
        if desc is None:
            desc = f"mapping {func.__name__} to {len(items)} objects"

        if num_proc is None:
            num_proc = DEFAULT_NUM_PROC
        num_proc = max(1, min(num_proc, mp.cpu_count()))
        if num_proc > 1:
            desc = f"{desc} [{num_proc}x]"
        self.num_proc = num_proc
        iterr = pmap_iter(
            func,
            items,
            args=args,
            kwargs=kwargs,
            num_proc=num_proc,
            desc=None,
            shuffle=shuffle,
            context=context,
            progress=False,
            **pmap_kwargs,
        )
        yield from self.iter_progress(iterr, desc=desc, total=len(items), progress=progress)

    def map(self, *a, **kw) -> list:
        return list(self.imap(*a, **kw))

    def run(self, *a, **kw) -> None:
        deque(self.imap(*a, **kw), maxlen=0)

    # -- misc -----------------------------------------------------------------

    def nap(self) -> float:
        naptime = round(random.random(), self.precision)
        self.log(f"napping for {naptime} seconds")
        time.sleep(naptime)
        return naptime

    def set_progress_desc(self, desc: str, pref: str | None = None, **kwargs) -> None:
        if desc and self.pbar is not None:
            desc = f'{self.inner_pref if pref is None else pref}{desc}'
            self.pbar.set_description(desc, **kwargs)

    # -- timing ---------------------------------------------------------------

    @property
    def tdesc(self) -> str:
        return format_timespan(self.duration)

    def lap(self) -> None:
        self.last_lap = time.monotonic()

    @property
    def lap_duration(self) -> float:
        return time.monotonic() - self.last_lap if self.last_lap is not None else 0.0

    @property
    def lap_tdesc(self) -> str:
        return format_timespan(self.lap_duration)

    @property
    def duration(self) -> float:
        if self.started is None:
            return 0.0
        end = self.ended if self.ended is not None else time.monotonic()
        return round(end - self.started, self.precision)

    # -- formatting -----------------------------------------------------------

    @property
    def pref(self) -> str:
        return f"{self.vertical_char} " * max(self.num - 1, 0)

    @property
    def inner_pref(self) -> str:
        return f"{self.vertical_char} " * self.num

    @property
    def desc(self) -> str:
        if self.started is None or self.ended is None:
            return f"{self.top_char} {self.task_name}".strip()
        return f"{self.bottom_char} {self.tdesc}".strip()

    def __call__(self, *a, **kw) -> Iterator:
        return self.iter_progress(*a, **kw)

    # -- boundary lines (open ⎾ / close ⎿) -------------------------------------

    def _log_boundary(self, event: str) -> None:
        if _is_quiet.get():
            return
        if event == "start":
            line = f"{self.top_char} {self.task_name}".strip()
            clean = self.task_name
        else:
            line = f"{self.bottom_char} {self.tdesc}".strip()
            clean = self.tdesc
        extra = {"depth": self.num, "task": self.task_name, "msg": clean, "event": event}
        if event == "end":
            extra["duration"] = self.duration
        _emit(f"{self.pref}{line}", level=self.level, extra=extra)

    def _flush_pending_open(self) -> None:
        if self._pending_open:
            self._pending_open = False
            self._log_boundary("start")

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> "logmap":
        """Start timing and print the opening line.

        Equivalent to entering a ``with`` block. Returns ``self`` so you can
        chain::

            lm = logmap("task").start()
            lm.log("doing stuff")
            lm.stop()

        When ``min_seconds_logworthy`` is set, the opening line is deferred
        until the first inner message (or until the task proves logworthy at
        :meth:`stop`), so fast tasks emit nothing rather than an unbalanced
        opening line.
        """
        if self.started is not None and self.ended is None:
            return self
        self.started = self.last_lap = time.monotonic()
        self.ended = None
        if self.announce or not _num_logwatches.get():
            _num_logwatches.set(_num_logwatches.get() + 1)
            self.iterated_num = True
        self.num = _num_logwatches.get()
        if self.announce:
            if self.min_seconds_logworthy:
                self._pending_open = True
            else:
                self._log_boundary("start")
        return self

    def stop(self, exc_type=None, exc_value=None, traceback=None) -> None:
        """Stop timing and print the closing line.

        Equivalent to exiting a ``with`` block. Safe to call more than once;
        after the first call subsequent calls are no-ops.
        """
        if self.started is None or self.ended is not None:
            return
        self.ended = time.monotonic()
        if self.iterated_num:
            _num_logwatches.set(max(0, _num_logwatches.get() - 1))
            self.iterated_num = False
        # GeneratorExit is normal control flow for abandoned generators
        # (e.g. breaking out of a decorated generator), not an error.
        if exc_type is not None and exc_type is not GeneratorExit:
            self._flush_pending_open()
            self.log(f"{exc_type.__name__}: {exc_value}", level="error")
            if self.announce:
                self._log_boundary("end")
            return
        if not self.announce:
            return
        logworthy = (not self.min_seconds_logworthy
                     or self.duration >= self.min_seconds_logworthy)
        if logworthy:
            self._flush_pending_open()
            self._log_boundary("end")
        elif not self._pending_open:
            # The opening line already went out (an inner message flushed
            # it), so emit the closing line regardless to keep the tree
            # balanced.
            self._log_boundary("end")
        else:
            self._pending_open = False

    def __enter__(self) -> "logmap":
        return self.start()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.stop(exc_type, exc_value, traceback)

    async def __aenter__(self) -> "logmap":
        return self.start()

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        self.stop(exc_type, exc_value, traceback)

    # -- safe execution -------------------------------------------------------

    @contextmanager
    def safespace(
        self,
        exception=Exception,
        log: bool = True,
        msg: str | None = None,
        level: str = "error",
        exc_info: bool = False,
    ):
        try:
            yield
        except exception as e:
            if log:
                text = str(msg) if msg else f"{type(e).__name__}: {e}"
                self.log(text, level=level, exc_info=exc_info)

    @property
    def safety(self):
        return self.safespace()
