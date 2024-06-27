import sys
import random
import time
from functools import wraps
import multiprocessing as mp
from collections import deque
from tqdm import tqdm
from contextlib import contextmanager
from loguru import logger

# Configure loguru
logger.remove()
logger.add(
    sink=sys.stderr,
    format="<level>{message}</level><cyan> @ {time:YYYY-MM-DD HH:mm:ss,SSS}</cyan>",
    level="DEBUG",
)


class LogMap:
    def __init__(self):
        self.level = 0
        self.is_quiet = False
        self.start_times = []
        self.vertical_char = "￨"
        self.top_char = "⎾"
        self.bottom_char = "⎿"
        self._iterator = None
        self._iter_kwargs = {}
        self.pbar = None

    def __call__(self, arg=None, **kwargs):
        if arg is None:
            return self
        elif isinstance(arg, str):
            return self._context_manager(arg, **kwargs)
        else:
            self._iterator = arg
            self._iter_kwargs = kwargs
            return self

    @contextmanager
    def _context_manager(
        self, message, increment_level=True, level="DEBUG", announce=True, **kwargs
    ):
        if announce:
            self.log(f"{self.top_char} {message}", level=level)
        start_time = time.time()
        self.start_times.append(start_time)

        if increment_level:
            self.level += 1

        try:
            yield self
        finally:
            if increment_level:
                self.level -= 1

            duration = time.time() - self.start_times.pop()
            if announce:
                self.log(f"{self.bottom_char} ✔️ {duration:.2f} seconds", level=level)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        pass

    def __iter__(self):
        if self._iterator is None:
            raise ValueError(
                "No iterator set. Use logmap(iterator) before attempting to iterate."
            )
        return self.iter_progress(self._iterator, **self._iter_kwargs)

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
        read_bar_format = "{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}{postfix}]"
        desc = f'{self.inner_pref if pref is None else pref}{desc if desc is not None else "iterating"}'

        if shuffle:
            iterator = list(iterator)
            random.shuffle(iterator)

        self.pbar = tqdm(
            iterator,
            desc=desc,
            position=position,
            total=total,
            bar_format=read_bar_format,
            disable=not progress or self.is_quiet,
            **kwargs,
        )

        try:
            yield from self.pbar
        finally:
            self.pbar.close()
            self.pbar = None

    loop = iter_progress

    def log(self, message, level="DEBUG"):
        if not self.is_quiet:
            indent = f"{self.vertical_char} " * self.level
            if self.pbar is None:
                logger.log(level.upper(), f"{indent}{message}")
            else:
                self.pbar.set_description(f"{indent}{message}")

    @contextmanager
    def quiet(self):
        prev_quiet = self.is_quiet
        self.is_quiet = True
        try:
            yield
        finally:
            self.is_quiet = prev_quiet

    def imap(
        self,
        func,
        objs,
        args=[],
        kwargs={},
        lim=None,
        num_proc=None,
        use_threads=False,
        progress=True,
        progress_pos=0,
        desc=None,
        shuffle=False,
        context="fork",
    ):
        if shuffle:
            random.shuffle(objs)
        if lim:
            objs = objs[:lim]

        num_cpu = mp.cpu_count()
        if num_proc is None:
            num_proc = max(1, num_cpu - 2)
        num_proc = min(num_proc, num_cpu, len(objs))

        if not desc:
            desc = f"Mapping {func.__name__}()"
        if desc and num_cpu > 1:
            desc = f"{desc} [{num_proc}x]"

        if num_proc > 1 and len(objs) > 1:
            objects = [(func, obj, args, kwargs) for obj in objs]

            pool_cls = (
                mp.pool.ThreadPool if use_threads else mp.get_context(context).Pool
            )
            with pool_cls(num_proc) as pool:
                iterr = pool.imap(self._map_do, objects)

                yield from self.iter_progress(
                    iterr,
                    total=len(objects),
                    desc=desc,
                    position=progress_pos,
                    progress=progress,
                )
        else:
            yield from self.iter_progress(
                objs, desc=desc, position=progress_pos, progress=progress
            )

    @staticmethod
    def _map_do(inp):
        func, obj, args, kwargs = inp
        return func(obj, *args, **kwargs)

    def map(self, *args, **kwargs):
        return list(self.imap(*args, **kwargs))

    def map_run(self, *args, **kwargs):
        deque(self.imap(*args, **kwargs), maxlen=0)

    def nap(self, max_duration=1):
        duration = random.uniform(0, max_duration)
        time.sleep(duration)
        self.log(f"Napped for {duration:.2f} seconds")
        return duration

    @property
    def inner_pref(self):
        return f"{self.vertical_char} " * (self.level)

    def warning(self, *args, **kwargs):
        return self.log(*args, **{**kwargs, "level": "warning"})

    def trace(self, *args, **kwargs):
        return self.log(*args, **{**kwargs, "level": "trace"})

    def error(self, *args, **kwargs):
        return self.log(*args, **{**kwargs, "level": "error"})

    def info(self, *args, **kwargs):
        return self.log(*args, **{**kwargs, "level": "info"})

    def debug(self, *args, **kwargs):
        return self.log(*args, **{**kwargs, "level": "debug"})


# Create a single instance of LogMap
logmap = LogMap()
