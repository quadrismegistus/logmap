"""Hierarchical context-manager logger with multiprocess mapping."""

import multiprocessing as mp
import random
import sys
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime

from humanfriendly import format_timespan
from tqdm.auto import tqdm


# ---------------------------------------------------------------------------
# Module config
# ---------------------------------------------------------------------------

CONTEXT = "fork"

_cpu = mp.cpu_count()
DEFAULT_NUM_PROC = 1 if _cpu <= 1 else (2 if _cpu <= 3 else _cpu - 2)

NUM_LOGWATCHES = 0
LOGWATCH_ID = 0

# Level dispatch — int values follow stdlib logging / loguru convention
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

# Mutable module state (managed by configure())
_sink = sys.stderr
_min_level = LEVELS["DEBUG"]
_format = DEFAULT_FORMAT
_opened_file = None
_colorize = False  # cached isatty() for _sink; refreshed by configure()


def _refresh_colorize():
    global _colorize
    try:
        _colorize = bool(_sink.isatty())
    except (AttributeError, ValueError):
        _colorize = False


_refresh_colorize()


def _emit(msg, level="DEBUG"):
    """Write one formatted log line to the current sink."""
    level = level.upper()
    if LEVELS.get(level, 0) < _min_level:
        return
    n = datetime.now()
    ts = (f"{n.year:04d}-{n.month:02d}-{n.day:02d} "
          f"{n.hour:02d}:{n.minute:02d}:{n.second:02d},{n.microsecond // 1000:03d}")
    if _colorize:
        line = _format.format(
            color=LEVEL_COLORS.get(level, ""),
            msg=msg, reset=_RESET, cyan=_CYAN, time=ts,
        )
    else:
        line = _format.format(color="", msg=msg, reset="", cyan="", time=ts)
    _sink.write(line + "\n")


def configure(sink=None, level=None, format=None):
    """Reconfigure where logmap writes output.

    Omitted args keep their current value.

    Args:
        sink: writable stream (``sys.stdout``, ``StringIO``, open file),
            file path string (``"run.log"``), or ``None`` to reset to stderr.
        level: level name (``"INFO"``) or int (``20``); messages below the
            threshold are suppressed.
        format: format string with ``{color}``, ``{msg}``, ``{reset}``,
            ``{cyan}``, ``{time}`` placeholders. See :data:`DEFAULT_FORMAT`.
    """
    global _sink, _min_level, _format, _opened_file
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


# ---------------------------------------------------------------------------
# Parallel map helpers
# ---------------------------------------------------------------------------

def _pmap_do(inp):
    func, obj, args, kwargs = inp
    return func(obj, *args, **kwargs)


def _auto_chunksize(n_items, num_proc):
    if num_proc <= 1 or n_items <= 0:
        return 1
    # aim for ~4 chunks per worker so progress updates stay smooth
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

    Does not mutate the caller's input. Uses ``multiprocess.Pool(num_proc)``
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


# ---------------------------------------------------------------------------
# The logmap class
# ---------------------------------------------------------------------------

class logmap:
    """Monitor and log the duration of a task, with hierarchical indentation.

    Typical use is as a context manager::

        with logmap("step") as lm:
            lm.log("doing work")

    but it can also be used standalone (just a logger) or with explicit
    :meth:`start` / :meth:`stop` calls.
    """

    is_quiet = False

    # -- global enable/disable ------------------------------------------------

    @staticmethod
    @contextmanager
    def quiet():
        was_quiet = logmap.is_quiet
        logmap.is_quiet = True
        try:
            yield
        finally:
            logmap.is_quiet = was_quiet

    @staticmethod
    @contextmanager
    def loud():
        was_quiet = logmap.is_quiet
        logmap.is_quiet = False
        try:
            yield
        finally:
            logmap.is_quiet = was_quiet

    disabled = quiet
    enabled = loud

    @staticmethod
    def enable():
        logmap.is_quiet = False

    @staticmethod
    def disable():
        logmap.is_quiet = True

    @staticmethod
    @contextmanager
    def verbosity(level=1):
        was_quiet = logmap.is_quiet
        logmap.is_quiet = not level
        try:
            yield
        finally:
            logmap.is_quiet = was_quiet

    # -- init -----------------------------------------------------------------

    def __init__(
        self,
        name="running task",
        level="DEBUG",
        min_seconds_logworthy=None,
        precision=1,
        announce=True,
    ):
        global LOGWATCH_ID
        LOGWATCH_ID += 1
        self.id = LOGWATCH_ID
        self.started = None
        self.ended = None
        self.announce = announce
        self.level = level.upper()
        self.task_name = name
        self.min_seconds_logworthy = min_seconds_logworthy
        self.vertical_char = "￨"
        self.top_char = "⎾"
        self.bottom_char = "⎿"
        self.last_lap = None
        self.pbar = None
        self.num_proc = None
        self.precision = precision
        self.iterated_num = False
        # default depth so log()/pref work before start()/__enter__ is called
        self.num = 0

    # -- log ------------------------------------------------------------------

    def log(self, msg, pref=None, inner_pref=True, level=None, linelim=None):
        if self.is_quiet or not msg:
            return
        msg = padmin(msg, linelim) if linelim else msg
        if self.pbar is None:
            prefix = (self.inner_pref if inner_pref else self.pref) if pref is None else pref
            _emit(f"{prefix}{msg}", level=level or self.level)
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
            disable=not progress or self.is_quiet,
            **kwargs,
        )
        yield from self.pbar
        self.pbar.close()
        self.pbar = None

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

        Equivalent to entering a ``with`` block. Returns ``self`` so you can chain::

            lm = logmap("task").start()
            lm.log("doing stuff")
            lm.stop()
        """
        global NUM_LOGWATCHES
        if self.started is not None and self.ended is None:
            return self  # already running; idempotent
        self.started = self.last_lap = time.time()
        self.ended = None
        if self.announce or not NUM_LOGWATCHES:
            NUM_LOGWATCHES += 1
            self.iterated_num = True
        self.num = NUM_LOGWATCHES
        if self.announce:
            self.log(self.desc, inner_pref=False)
        return self

    def stop(self, exc_type=None, exc_value=None, traceback=None):
        """Stop timing and print the closing line.

        Equivalent to exiting a ``with`` block. Safe to call more than once;
        after the first call subsequent calls are no-ops.
        """
        global NUM_LOGWATCHES, LOGWATCH_ID
        if self.started is None or self.ended is not None:
            return
        if exc_type:
            LOGWATCH_ID = 0
            NUM_LOGWATCHES = 0
            self.ended = time.time()
            self.log(f"{exc_type.__name__} {exc_value}", level="error")
        else:
            if self.iterated_num:
                NUM_LOGWATCHES -= 1
            self.ended = time.time()
            if (not self.min_seconds_logworthy
                    or self.duration >= self.min_seconds_logworthy):
                if self.announce:
                    self.log(self.desc, inner_pref=False)
            if NUM_LOGWATCHES == 0:
                LOGWATCH_ID = 0

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc_value, traceback):
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
