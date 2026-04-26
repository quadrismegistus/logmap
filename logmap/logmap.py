"""Hierarchical context-manager logger with multiprocess mapping."""

import asyncio
import functools
import inspect
import json
import multiprocessing as mp
import platform
import random
import sys
import threading
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime

from humanfriendly import format_timespan
from tqdm.auto import tqdm


# ---------------------------------------------------------------------------
# Module config
# ---------------------------------------------------------------------------

def _default_mp_context():
    system = platform.system()
    if system == "Darwin":
        return "forkserver"
    if system == "Windows":
        return "spawn"
    return "fork"


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

# Format string — understood placeholders: {color} {msg} {reset} {cyan} {time}
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
# Thread-local nesting state + shared output config
# ---------------------------------------------------------------------------

class _NestingState(threading.local):
    def __init__(self):
        self.num_logwatches = 0
        self.logwatch_id = 0
        self.is_quiet = False


_nesting = _NestingState()
_lock = threading.Lock()

# Output config — module-level, shared across threads, guarded by _lock
_sink = sys.stderr
_min_level = LEVELS["DEBUG"]
_format = DEFAULT_FORMAT
_opened_file = None
_colorize = False
_logger = None
_structured = False


def _refresh_colorize():
    global _colorize
    try:
        _colorize = bool(_sink.isatty())
    except (AttributeError, ValueError):
        _colorize = False


_refresh_colorize()

_UNSET = object()


def _emit(msg, level="DEBUG", extra=None):
    """Write one formatted log line to the current sink."""
    level = level.upper()
    lvl_int = LEVELS.get(level, 0)
    if lvl_int < _min_level:
        return
    n = datetime.now()

    if _logger is not None:
        clean = extra.get("msg", msg) if extra else msg
        _logger.log(lvl_int, clean)
        return

    if _structured:
        record = {"ts": n.isoformat(), "level": level, "msg": msg}
        if extra:
            record.update(extra)
        line = json.dumps(record, default=str)
    else:
        ts = (f"{n.year:04d}-{n.month:02d}-{n.day:02d} "
              f"{n.hour:02d}:{n.minute:02d}:{n.second:02d},{n.microsecond // 1000:03d}")
        if _colorize:
            line = _format.format(
                color=LEVEL_COLORS.get(level, ""),
                msg=msg, reset=_RESET, cyan=_CYAN, time=ts,
            )
        else:
            line = _format.format(color="", msg=msg, reset="", cyan="", time=ts)

    with _lock:
        _sink.write(line + "\n")


def configure(sink=None, level=None, format=None, logger=_UNSET, structured=None):
    """Reconfigure where logmap writes output.

    Omitted args keep their current value.

    Args:
        sink: writable stream (``sys.stdout``, ``StringIO``, open file),
            file path string (``"run.log"``), or ``None`` to reset to stderr.
        level: level name (``"INFO"``) or int (``20``); messages below the
            threshold are suppressed.
        format: format string with ``{color}``, ``{msg}``, ``{reset}``,
            ``{cyan}``, ``{time}`` placeholders. See :data:`DEFAULT_FORMAT`.
        logger: a :class:`logging.Logger`; when set, output is forwarded via
            ``logger.log()`` instead of writing to the sink directly.
            Pass ``None`` to clear.
        structured: if ``True``, emit JSON-lines output instead of
            human-readable text.  Each line is a JSON object with ``ts``,
            ``level``, ``msg``, ``depth``, and ``task`` keys.
    """
    global _sink, _min_level, _format, _opened_file, _colorize, _logger, _structured
    with _lock:
        if sink is not None:
            if _opened_file is not None:
                try:
                    _opened_file.close()
                except Exception:
                    pass
                _opened_file = None
            if isinstance(sink, str):
                _opened_file = open(sink, "a", encoding="utf-8", buffering=1)
                _sink = _opened_file
            else:
                _sink = sink
            _refresh_colorize()
        if level is not None:
            _min_level = LEVELS[level.upper()] if isinstance(level, str) else int(level)
        if format is not None:
            _format = format
        if logger is not _UNSET:
            _logger = logger
        if structured is not None:
            _structured = bool(structured)


# ---------------------------------------------------------------------------
# Parallel map helpers
# ---------------------------------------------------------------------------

def _pmap_do(inp):
    func, obj, args, kwargs = inp
    return func(obj, *args, **kwargs)


def _auto_chunksize(n_items, num_proc):
    if num_proc <= 1 or n_items <= 0:
        return 1
    return max(1, n_items // (num_proc * 4))


def pmap_iter(
    func,
    objs,
    args=(),
    kwargs=None,
    lim=None,
    num_proc=DEFAULT_NUM_PROC,
    progress=True,
    progress_pos=0,
    desc=None,
    shuffle=False,
    context=CONTEXT,
    chunksize=None,
    **_unused,
):
    """Yield func(obj) for each obj in objs, optionally in parallel.

    Does not mutate the caller's input. Uses ``multiprocessing.Pool(num_proc)``
    when ``num_proc > 1`` and there is more than one item.
    """
    kwargs = dict(kwargs) if kwargs else {}
    args = tuple(args)

    items = list(objs)
    if shuffle:
        items = random.sample(items, k=len(items))
    if lim is not None:
        items = items[:lim]

    n_items = len(items)
    num_cpu = mp.cpu_count()
    if num_proc is None or num_proc < 1:
        num_proc = 1
    if num_proc > num_cpu:
        num_proc = num_cpu
    if num_proc > n_items:
        num_proc = max(1, n_items)

    if not desc:
        desc = f"Mapping {func.__name__}()"
    if num_cpu > 1 and num_proc > 1:
        desc = f"{desc} [x{num_proc}]"

    if num_proc > 1 and n_items > 1:
        payload = ((func, obj, args, kwargs) for obj in items)
        cs = chunksize if chunksize else _auto_chunksize(n_items, num_proc)
        with mp.get_context(context).Pool(num_proc) as pool:
            iterr = pool.imap(_pmap_do, payload, chunksize=cs)
            if progress:
                iterr = tqdm(iterr, total=n_items, desc=desc, position=progress_pos)
            for res in iterr:
                yield res
    else:
        iterr = tqdm(items, desc=desc, position=progress_pos) if progress else items
        for obj in iterr:
            yield func(obj, *args, **kwargs)


def pmap(*a, **kw):
    """List-returning version of :func:`pmap_iter`."""
    return list(pmap_iter(*a, **kw))


def pmap_run(*a, **kw):
    """Exhaust :func:`pmap_iter` for side effects."""
    for _ in pmap_iter(*a, **kw):
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def padmin(xstr, lim=40):
    xstr = str(xstr)
    return xstr + (" " * (lim - len(xstr))) if len(xstr) < lim else xstr[:lim]


def shuffled(l):
    return random.sample(list(l), k=len(l))


def _short_repr(obj, maxlen=80):
    r = repr(obj).replace("\n", " ")
    return r[:maxlen - 3] + "..." if len(r) > maxlen else r


def _format_call(func, args, kwargs, maxlen=60):
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
    def is_quiet(cls):
        return _nesting.is_quiet

    @is_quiet.setter
    def is_quiet(cls, value):
        _nesting.is_quiet = value


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
        was_quiet = _nesting.is_quiet
        _nesting.is_quiet = True
        try:
            yield
        finally:
            _nesting.is_quiet = was_quiet

    @staticmethod
    @contextmanager
    def loud():
        was_quiet = _nesting.is_quiet
        _nesting.is_quiet = False
        try:
            yield
        finally:
            _nesting.is_quiet = was_quiet

    disabled = quiet
    enabled = loud

    @staticmethod
    def enable():
        _nesting.is_quiet = False

    @staticmethod
    def disable():
        _nesting.is_quiet = True

    @staticmethod
    @contextmanager
    def verbosity(level=1):
        was_quiet = _nesting.is_quiet
        _nesting.is_quiet = not level
        try:
            yield
        finally:
            _nesting.is_quiet = was_quiet

    # -- function decorator ---------------------------------------------------

    @staticmethod
    def fn(_func=None, *, level="DEBUG", log_args=True, log_return=True):
        """Decorator that wraps a function call in a logmap context.

        Works with both sync and async functions::

            @logmap.fn
            def process(x): ...

            @logmap.fn(level="INFO")
            async def fetch(url): ...
        """
        def decorator(func):
            if inspect.iscoroutinefunction(func):
                @functools.wraps(func)
                async def wrapper(*args, **kwargs):
                    desc = _format_call(func, args, kwargs) if log_args else func.__qualname__ + "()"
                    async with logmap(desc, level=level) as lm:
                        result = await func(*args, **kwargs)
                        if log_return and result is not None:
                            lm.log(f">>> {_short_repr(result)}")
                        return result
                return wrapper
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                desc = _format_call(func, args, kwargs) if log_args else func.__qualname__ + "()"
                with logmap(desc, level=level) as lm:
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
        name="running task",
        level="DEBUG",
        min_seconds_logworthy=None,
        precision=1,
        announce=True,
    ):
        _nesting.logwatch_id += 1
        self.id = _nesting.logwatch_id
        self.started = None
        self.ended = None
        self.announce = announce
        self.level = level.upper()
        self.task_name = name
        self.min_seconds_logworthy = min_seconds_logworthy
        self.vertical_char = VERTICAL_CHAR
        self.top_char = TOP_CHAR
        self.bottom_char = BOTTOM_CHAR
        self.last_lap = None
        self.pbar = None
        self.num_proc = None
        self.precision = precision
        self.iterated_num = False
        self.num = 0

    # -- is_quiet (instance-level, delegates to thread-local) -----------------

    @property
    def is_quiet(self):
        return _nesting.is_quiet

    @is_quiet.setter
    def is_quiet(self, value):
        _nesting.is_quiet = value

    # -- log ------------------------------------------------------------------

    def log(self, msg, pref=None, inner_pref=True, level=None, linelim=None):
        if _nesting.is_quiet or not msg:
            return
        msg = padmin(msg, linelim) if linelim else msg
        if self.pbar is None:
            prefix = (self.inner_pref if inner_pref else self.pref) if pref is None else pref
            _emit(
                f"{prefix}{msg}",
                level=level or self.level,
                extra={"depth": self.num, "task": self.task_name, "msg": msg},
            )
        else:
            self.set_progress_desc(msg)

    def warning(self, *a, **kw):
        return self.log(*a, **{**kw, "level": "warning"})

    def trace(self, *a, **kw):
        return self.log(*a, **{**kw, "level": "trace"})

    def error(self, *a, **kw):
        return self.log(*a, **{**kw, "level": "error"})

    def info(self, *a, **kw):
        return self.log(*a, **{**kw, "level": "info"})

    def debug(self, *a, **kw):
        return self.log(*a, **{**kw, "level": "debug"})

    # -- iteration ------------------------------------------------------------

    def iter_progress(
        self,
        iterator,
        desc="iterating",
        pref=None,
        position=0,
        total=None,
        progress=True,
        shuffle=False,
        **kwargs,
    ):
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
            disable=not progress or _nesting.is_quiet,
            **kwargs,
        )
        yield from self.pbar
        self.pbar.close()
        self.pbar = None

    def progress(self, iterable, desc="iterating", **kwargs):
        """Iterate with a progress bar at the current nesting depth.

        Alias for :meth:`iter_progress` with a shorter name::

            with logmap("training") as lm:
                for batch in lm.progress(batches, desc="epochs"):
                    ...
        """
        return self.iter_progress(iterable, desc=desc, **kwargs)

    def imap(
        self,
        func,
        objs,
        args=(),
        kwargs=None,
        lim=None,
        num_proc=None,
        desc=None,
        shuffle=False,
        context=CONTEXT,
        progress=True,
        **pmap_kwargs,
    ):
        items = list(objs)
        if lim is not None:
            items = items[:lim]
        if desc is None:
            desc = f"mapping {func.__name__} to {len(items)} objects"

        if num_proc is None:
            num_proc = max(1, mp.cpu_count() // 2)
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

    def map(self, *a, **kw):
        return list(self.imap(*a, **kw))

    def run(self, *a, **kw):
        deque(self.imap(*a, **kw), maxlen=0)

    # -- misc -----------------------------------------------------------------

    def nap(self):
        naptime = round(random.random(), self.precision)
        self.log(f"napping for {naptime} seconds")
        time.sleep(naptime)
        return naptime

    def set_progress_desc(self, desc, pref=None, **kwargs):
        if desc:
            desc = f'{self.inner_pref if pref is None else pref}{desc if desc is not None else ""}'
            self.pbar.set_description(desc, **kwargs)

    # -- timing ---------------------------------------------------------------

    @property
    def tdesc(self):
        return format_timespan(self.duration)

    def lap(self):
        self.last_lap = time.time()

    @property
    def lap_duration(self):
        return time.time() - self.last_lap if self.last_lap else 0

    @property
    def lap_tdesc(self):
        return format_timespan(self.lap_duration)

    @property
    def duration(self):
        return round(
            (self.ended if self.ended else time.time()) - self.started,
            self.precision,
        )

    # -- formatting -----------------------------------------------------------

    @property
    def pref(self):
        return f"{self.vertical_char} " * (self.num - 1)

    @property
    def inner_pref(self):
        return f"{self.vertical_char} " * self.num

    @property
    def desc(self):
        if self.started is None or self.ended is None:
            return f"{self.top_char} {self.task_name}".strip()
        return f"{self.bottom_char} {self.tdesc}".strip()

    def __call__(self, *a, **kw):
        return self.iter_progress(*a, **kw)

    # -- lifecycle ------------------------------------------------------------

    def start(self):
        """Start timing and print the opening line.

        Equivalent to entering a ``with`` block. Returns ``self`` so you can
        chain::

            lm = logmap("task").start()
            lm.log("doing stuff")
            lm.stop()
        """
        if self.started is not None and self.ended is None:
            return self
        self.started = self.last_lap = time.time()
        self.ended = None
        if self.announce or not _nesting.num_logwatches:
            _nesting.num_logwatches += 1
            self.iterated_num = True
        self.num = _nesting.num_logwatches
        if self.announce:
            self.log(self.desc, inner_pref=False)
        return self

    def stop(self, exc_type=None, exc_value=None, traceback=None):
        """Stop timing and print the closing line.

        Equivalent to exiting a ``with`` block. Safe to call more than once;
        after the first call subsequent calls are no-ops.
        """
        if self.started is None or self.ended is not None:
            return
        if exc_type:
            _nesting.logwatch_id = 0
            _nesting.num_logwatches = 0
            self.ended = time.time()
            self.log(f"{exc_type.__name__} {exc_value}", level="error")
        else:
            if self.iterated_num:
                _nesting.num_logwatches -= 1
            self.ended = time.time()
            if (not self.min_seconds_logworthy
                    or self.duration >= self.min_seconds_logworthy):
                if self.announce:
                    self.log(self.desc, inner_pref=False)
            if _nesting.num_logwatches == 0:
                _nesting.logwatch_id = 0

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop(exc_type, exc_value, traceback)

    async def __aenter__(self):
        return self.start()

    async def __aexit__(self, exc_type, exc_value, traceback):
        self.stop(exc_type, exc_value, traceback)

    # -- safe execution -------------------------------------------------------

    @contextmanager
    def safespace(self, exception=Exception, log=True, msg=None, level="error"):
        try:
            yield
        except exception as e:
            if log:
                self.log(str(msg) if msg else str(e), level=level)

    @property
    def safety(self):
        return self.safespace()
