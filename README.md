# logmap

A hierarchical context-manager logger with multiprocess mapping.

## Install

```sh
pip install logmap
```

## Usage

```python
from logmap import logmap
```

### Basic

```python
with logmap('testing...'):
    # ... do something ...
    pass
```
```
⎾ testing... @ 2026-07-05 17:39:14,456
⎿ 0 seconds @ 2026-07-05 17:39:14,460
```

### Duration

`lm.nap()` sleeps for a random sub-second interval (handy for demos and tests). Durations are measured with `time.monotonic()`, so they are immune to system clock changes.

```python
with logmap('testing...') as lm:
    naptime = lm.nap()

assert naptime == lm.duration
```
```
⎾ testing... @ 2026-07-05 17:39:15,414
  napping for 0.7 seconds @ 2026-07-05 17:39:15,418
⎿ 0.7 seconds @ 2026-07-05 17:39:16,120
```

### Nested

Nesting indents automatically. `lm.log("msg")` writes at the current depth.

```python
with logmap('outer') as lm:
    with logmap('middle') as lm2:
        with logmap('inner') as lm3:
            lm3.nap()
```
```
⎾ outer @ 2026-07-05 17:39:16,355
  ⎾ middle @ 2026-07-05 17:39:16,358
    ⎾ inner @ 2026-07-05 17:39:16,358
      napping for 0.6 seconds @ 2026-07-05 17:39:16,358
    ⎿ 0.6 seconds @ 2026-07-05 17:39:16,962
  ⎿ 0.6 seconds @ 2026-07-05 17:39:16,963
⎿ 0.6 seconds @ 2026-07-05 17:39:16,963
```

### Parallel map

```python
import random, time

def fn(naptime):
    t = random.random() * naptime / 2
    time.sleep(t)
    return t

with logmap('function mapping') as lm:
    results = lm.map(fn, list(range(5)), num_proc=2)
```
```
⎾ function mapping @ 2026-07-05 17:39:39,764
  mapping fn to 5 objects [2x]: 100%|██████████| 5/5 [00:01<00:00,  4.56it/s]
⎿ 1.1 seconds @ 2026-07-05 17:39:40,865
```

For streaming results as they arrive:

```python
with logmap('function mapping') as lm:
    for res in lm.imap(fn, list(range(5)), num_proc=2):
        lm.log(f'got {res:.2f}')   # emitted as a log line AND shown on the bar
```
```
⎾ function mapping @ 2026-07-05 17:39:51,488
  got 0.00 @ 2026-07-05 17:39:51,583
  got 0.28 @ 2026-07-05 17:39:51,868
  got 0.71 @ 2026-07-05 17:39:52,296
  got 0.77 @ 2026-07-05 17:39:52,642
  got 1.82 @ 2026-07-05 17:39:54,115
  got 1.82: 100%|██████████| 5/5 [00:02<00:00,  1.91it/s]
⎿ 2.6 seconds @ 2026-07-05 17:39:54,118
```

Messages logged while a progress bar is active are emitted as normal log lines — so file, JSON, and stdlib-logger sinks never lose them — and additionally shown as the bar's description.

`lm.run(...)` is the same but discards results — useful when you only care about side effects.

Module-level helpers `pmap`, `pmap_iter`, `pmap_run` provide the same semantics without a `logmap` context:

```python
from logmap import pmap
pmap(fn, items, num_proc=4)
```

Error handling and ordering:

```python
pmap(fn, items, on_error="skip")           # drop items whose call raised
pmap(fn, items, on_error="return")         # yield the exception object in the item's place
pmap(fn, items, ordered=False)             # yield results as they complete (imap_unordered)
```

`on_error="raise"` (the default) propagates the first worker exception. These options work on `pmap`/`pmap_iter`/`pmap_run` and `lm.imap`/`lm.map`/`lm.run` alike. With `shuffle=True`, items are processed in random order (results no longer align with input), and combining `shuffle=True` with `lim=N` processes a random *sample* of N items.

> **Note:** `logmap` uses stdlib `multiprocessing`, so functions passed to parallel map must be picklable (defined at module level — no lambdas or closures).

### Progress bar

`lm.progress(iterable)` wraps any iterable with a progress bar at the current nesting depth:

```python
with logmap('training') as lm:
    for batch in lm.progress(batches, desc='epochs'):
        train(batch)
```

Log lines are written via `tqdm.write()`, so they appear above an active bar instead of garbling it. Breaking out of the loop early is fine — the bar is cleaned up either way.

### Async support

`logmap` works as an async context manager:

```python
async with logmap('fetching') as lm:
    result = await fetch(url)
    lm.log(f'got {len(result)} bytes')
```

Nesting state lives in `contextvars`, so concurrent asyncio tasks are isolated: two tasks each running nested `async with logmap(...)` blocks see their own correct depths rather than interleaving each other's indentation.

### Function decorator

`@logmap.fn` wraps a function in a `logmap` context — logging the call, timing it, and optionally logging the return value:

```python
@logmap.fn
def process(x):
    return x * 2

process(21)
```
```
⎾ process(21) @ 2026-07-05 17:39:26,574
  >>> 42 @ 2026-07-05 17:39:26,578
⎿ 0 seconds @ 2026-07-05 17:39:26,578
```

With options:

```python
@logmap.fn(level="INFO", log_return=False)
def transform(data):
    return data

@logmap.fn(log_args=False)          # hide arguments (e.g. passwords)
def authenticate(token):
    return True
```

Works with async functions, generator functions, and async-generator functions too. For generators, the context stays open across iteration, so the logged duration covers consumption, not just creation:

```python
@logmap.fn
async def fetch(url):
    async with aiohttp.ClientSession() as session:
        resp = await session.get(url)
        return await resp.text()

@logmap.fn
def stream_records(path):
    with open(path) as f:
        yield from f
```

Decorated functions nest naturally with `logmap` contexts and other decorated functions.

### Without a `with` block

```python
# just a logger
lm = logmap('app')
lm.log('ready')
lm.warning('careful')

# explicit lifecycle (equivalent to a `with` block)
lm = logmap('manual').start()
lm.log('doing stuff')
lm.stop()
```

### Catching and logging exceptions

An exception that escapes a `with logmap(...)` block is logged as `Type: message` at ERROR level, followed by the closing line, before propagating. To swallow and log instead, use `lm.safespace(...)`:

```python
with logmap('risky business') as lm:
    with lm.safespace(ValueError):
        raise ValueError('bad input')
    lm.log('still here')
```
```
⎾ risky business @ 2026-07-05 17:40:00,310
  ValueError: bad input @ 2026-07-05 17:40:00,314
  still here @ 2026-07-05 17:40:00,314
⎿ 0 seconds @ 2026-07-05 17:40:00,314
```

`lm.safety` is shorthand for `lm.safespace()`, which catches any `Exception`. Pass `exc_info=True` — to `safespace()` or to any log call inside an `except` block — to append the full traceback:

```python
with logmap('app') as lm:
    try:
        1 / 0
    except ZeroDivisionError:
        lm.error('math failed', exc_info=True)
```
```
⎾ app @ 2026-07-05 17:40:39,145
  math failed
Traceback (most recent call last):
  File "<string>", line 5, in <module>
    1 / 0
    ~~^~~
ZeroDivisionError: division by zero @ 2026-07-05 17:40:39,149
⎿ 0 seconds @ 2026-07-05 17:40:39,149
```

### Configuring output

`logmap` writes to `sys.stderr` by default. Use `configure()` to redirect:

```python
from logmap import configure
import sys

configure(sink=sys.stdout)          # e.g. so it doesn't mingle with stderr diagnostics
configure(sink="run.log")           # path — opened line-buffered
configure(sink=my_stringio)         # any writable stream
configure(sink=None)                # reset to stderr
configure(level="INFO")             # drop TRACE and DEBUG messages
```

Level names accept `WARN` and `FATAL` as aliases for `WARNING` and `CRITICAL`; unknown names raise `ValueError` immediately rather than silently dropping messages.

Custom format strings understand the `{color}`, `{msg}`, `{reset}`, `{cyan}`, `{time}`, and `{level}` placeholders, and are validated at `configure()` time so mistakes surface immediately:

```python
configure(format="{level}: {msg}")

with logmap('deploy') as lm:
    lm.warning('disk almost full')
```
```
DEBUG: ⎾ deploy
WARNING:   disk almost full
DEBUG: ⎿ 0 seconds
```

Colors auto-disable when the sink isn't a TTY (files, StringIO, etc.); the `NO_COLOR` and `FORCE_COLOR` environment variables override that detection.

`get_config()` returns a snapshot of the current configuration (`sink`, `level`, `format`, `logger`, `structured`, `colorize`). Use it rather than poking module globals — `import logmap.logmap` gives you the *class*, which shadows the submodule of the same name.

### Stdlib logging integration

Route all logmap output through a standard library `logging.Logger`:

```python
import logging
from logmap import configure

configure(logger=logging.getLogger("myapp"))
```

This lets you attach any stdlib handler (file, syslog, etc.) and have logmap participate in your application's logging hierarchy. Level integers match stdlib conventions, so filtering works as expected, and the TRACE level name (5) is registered with `logging` automatically. logmap's `depth` and `task` are forwarded as `LogRecord` attributes, so a `Formatter` can use `%(task)s` on records that came from logmap. When a logger is set it takes precedence over `structured=True`. Pass `logger=None` to switch back to direct sink output.

### Structured (JSON) output

For log aggregation or machine parsing, enable JSON-lines mode:

```python
configure(structured=True)

with logmap('outer') as lm:
    lm.log('doing work')
```
```json
{"ts": "2026-07-05T17:39:54.264502", "level": "DEBUG", "msg": "outer", "depth": 1, "task": "outer", "event": "start"}
{"ts": "2026-07-05T17:39:54.267754", "level": "DEBUG", "msg": "doing work", "depth": 1, "task": "outer"}
{"ts": "2026-07-05T17:39:54.268083", "level": "DEBUG", "msg": "0 seconds", "depth": 1, "task": "outer", "event": "end", "duration": 0.0}
```

The `msg` field contains the clean message (no indentation prefix), while `depth` and `task` give you the hierarchical context as structured data. Task open/close lines carry `"event": "start"` / `"end"`, and end lines include the `"duration"` in seconds — so the output is genuinely machine-parseable.

### Silencing

```python
with logmap.quiet():
    with logmap('no output'):       # nothing gets printed
        ...

logmap.disable()                    # global off switch
logmap.enable()                     # back on
```

`with logmap('task', announce=False):` suppresses just the open/close lines while keeping `lm.log()` active. `logmap.verbosity(n)` is a context manager that enables logging when `n` is truthy — handy for forwarding a user's `--verbose` flag.

For tasks only worth logging when slow, set `min_seconds_logworthy`:

```python
with logmap('maybe quick', min_seconds_logworthy=1):
    ...
```

The opening line is deferred: a task that finishes under the threshold without logging anything emits nothing at all, while a slower one (or one that logs) gets both its opening and closing lines — output always stays balanced.

### Thread and task safety

Nesting depth and quiet state are stored in `contextvars`, so they are isolated per thread *and* per asyncio task — threads and concurrent tasks each keep their own indentation, and `quiet()` in one doesn't silence another. Output writes are serialized with a lock to prevent garbled lines.

### Multiprocessing start method

On all non-Windows platforms, `logmap` defaults to the `forkserver` multiprocessing context (Windows uses `spawn`). `fork` deadlocks when the parent process has running threads — the reason CPython 3.14 moved its own Linux default to `forkserver`. You can still pass `context="spawn"` or `context="fork"` explicitly to `lm.map()` / `pmap()` if needed.

### Odds and ends

- `lm.lap()` resets a lap timer; `lm.lap_duration` / `lm.lap_tdesc` give the seconds / human-readable time since the last lap (or since start).
- `lm.duration` / `lm.tdesc` — elapsed seconds / human-readable duration; safe to read at any point (0 before start).
- `logmap('task', precision=2)` controls duration rounding (default: 1 decimal place).
- `lm(iterable)` is shorthand for `lm.progress(iterable)`.
- The box-drawing characters and other constants are exported: `TOP_CHAR` (`⎾`), `BOTTOM_CHAR` (`⎿`), `VERTICAL_CHAR` (a space since v0.3.2), plus `LEVELS` and `DEFAULT_FORMAT`.
- The package ships type hints (`py.typed`).
