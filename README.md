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
⎾ testing... @ 2026-04-19 15:12:55,349
⎿ 0 seconds @ 2026-04-19 15:12:55,349
```

### Duration

```python
with logmap('testing...') as lm:
    naptime = lm.nap()

assert naptime == lm.duration
```
```
⎾ testing... @ 2026-04-19 15:12:55,349
￨ napping for 0.6 seconds @ 2026-04-19 15:12:55,349
⎿ 0.6 seconds @ 2026-04-19 15:12:55,954
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
⎾ outer @ 2026-04-19 15:12:55,954
￨ ⎾ middle @ 2026-04-19 15:12:55,954
￨ ￨ ⎾ inner @ 2026-04-19 15:12:55,954
￨ ￨ ￨ napping for 0.2 seconds @ 2026-04-19 15:12:55,954
￨ ￨ ⎿ 0.2 seconds @ 2026-04-19 15:12:56,159
￨ ⎿ 0.2 seconds @ 2026-04-19 15:12:56,159
⎿ 0.2 seconds @ 2026-04-19 15:12:56,159
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
⎾ function mapping @ 2026-04-19 15:12:56,159
￨ mapping fn to 5 objects [2x]: 100%|██████████| 5/5 [00:02<00:00, 2.31it/s]
⎿ 2.2 seconds @ 2026-04-19 15:12:58,335
```

For streaming results as they arrive:

```python
with logmap('function mapping') as lm:
    for res in lm.imap(fn, list(range(5)), num_proc=2):
        lm.log(f'got {res:.2f}')   # updates the progress bar
```

`lm.run(...)` is the same but discards results — useful when you only care about side effects.

Module-level helpers `pmap`, `pmap_iter`, `pmap_run` provide the same semantics without a `logmap` context:

```python
from logmap import pmap
pmap(fn, items, num_proc=4)
```

> **Note:** `logmap` uses stdlib `multiprocessing`, so functions passed to parallel map must be picklable (defined at module level — no lambdas or closures).

### Progress bar

`lm.progress(iterable)` wraps any iterable with a progress bar at the current nesting depth:

```python
with logmap('training') as lm:
    for batch in lm.progress(batches, desc='epochs'):
        train(batch)
```

### Async support

`logmap` works as an async context manager:

```python
async with logmap('fetching') as lm:
    result = await fetch(url)
    lm.log(f'got {len(result)} bytes')
```

### Function decorator

`@logmap.fn` wraps a function in a `logmap` context — logging the call, timing it, and optionally logging the return value:

```python
@logmap.fn
def process(x):
    return x * 2

process(21)
```
```
⎾ process(21) @ 2026-04-26 12:00:00,000
￨ >>> 42 @ 2026-04-26 12:00:00,001
⎿ 0 seconds @ 2026-04-26 12:00:00,001
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

Works with async functions too:

```python
@logmap.fn
async def fetch(url):
    async with aiohttp.ClientSession() as session:
        resp = await session.get(url)
        return await resp.text()
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

### Redirecting output

`logmap` writes to `sys.stderr` by default. Use `configure()` to redirect:

```python
from logmap import configure
import sys

configure(sink=sys.stdout)          # e.g. so it doesn't mingle with stderr diagnostics
configure(sink="run.log")           # path — opened line-buffered
configure(sink=my_stringio)         # any writable stream
configure(level="INFO")             # drop DEBUG messages
```

Colors auto-disable when the sink isn't a TTY (files, StringIO, etc.).

### Stdlib logging integration

Route all logmap output through a standard library `logging.Logger`:

```python
import logging
from logmap import configure

configure(logger=logging.getLogger("myapp"))
```

This lets you attach any stdlib handler (file, syslog, etc.) and have logmap participate in your application's logging hierarchy. Level integers match stdlib conventions, so filtering works as expected. Pass `logger=None` to switch back to direct sink output.

### Structured (JSON) output

For log aggregation or machine parsing, enable JSON-lines mode:

```python
configure(structured=True)
```

Each line becomes a JSON object:

```json
{"ts": "2026-04-19T15:12:55.349000", "level": "INFO", "msg": "doing work", "depth": 1, "task": "outer"}
```

The `msg` field contains the clean message (no indentation prefix), while `depth` and `task` give you the hierarchical context as structured data.

### Silencing

```python
with logmap.quiet():
    with logmap('no output'):       # nothing gets printed
        ...

logmap.disable()                    # global off switch
logmap.enable()                     # back on
```

`with logmap('task', announce=False):` suppresses just the open/close lines while keeping `lm.log()` active.

### Thread safety

Nesting depth and quiet state are thread-local — multiple threads can use `logmap` concurrently without interleaving indentation or silencing each other. Output writes are serialized with a lock to prevent garbled lines.

### Multiprocessing on macOS

On macOS, `logmap` defaults to the `forkserver` multiprocessing context instead of `fork` (which Python 3.12+ deprecates due to deadlock risk with threads). You can still pass `context="spawn"` or `context="fork"` explicitly to `lm.map()` / `pmap()` if needed.
