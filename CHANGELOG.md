# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-07-05

### Fixed

- Caught exceptions no longer corrupt nesting state. Previously, an exception
  handled by the caller while an outer `logmap` context was still open broke
  depth accounting for the rest of the thread (later contexts got wrong
  indentation and counters drifted negative).
- Breaking out of `lm.progress()` / `lm.imap()` early (via `break`, `return`,
  or an exception) now closes the progress bar. Previously the stale bar
  silently swallowed every subsequent `lm.log()` call.
- Concurrent asyncio tasks no longer corrupt each other's nesting depth or
  quiet state: context-local state moved from `threading.local` to
  `contextvars`, which isolates per thread *and* per task.
- `configure(sink=None)` now resets output to stderr, as its docstring always
  promised.
- Custom `format` strings are validated at `configure()` time and raise a
  clear `ValueError`, instead of raising `KeyError` from inside emit on the
  first log call.
- `lm.duration` / `lm.tdesc` no longer raise `TypeError` on a never-started
  instance; they return 0 until the timer starts.
- `min_seconds_logworthy` no longer produces unbalanced output. The opening
  line is deferred: an under-threshold task with no inner messages emits
  nothing at all, instead of an opening bracket that never closes.
- `@logmap.fn` now handles generator functions correctly: the context spans
  iteration, so the logged duration covers consumption. Previously it timed
  only creation and logged `>>> <generator object ...>`.
- Log lines are written via `tqdm.write()`, so they no longer garble an
  active progress bar sharing the same stream.
- Emit snapshots the output config under the lock, so a concurrent
  `configure()` can no longer produce a line rendered with a torn mix of old
  and new settings.
- Falsy-but-real messages such as `lm.log(0)` are no longer skipped; only
  `None` and `""` are.

### Changed

- **Unknown level names now raise `ValueError`** instead of silently dropping
  the message. `WARN` and `FATAL` are accepted as aliases for `WARNING` and
  `CRITICAL`.
- **The default multiprocessing start method is `forkserver` on all
  non-Windows platforms** (Windows: `spawn`). Previously only macOS used
  `forkserver` and Linux defaulted to `fork`, which deadlocks when the parent
  process has threads. The `context=` parameter remains the escape hatch.
- **`lm.log()` during an active progress bar now also emits a normal log
  line** — file, JSON, and stdlib-logger sinks never lose messages — in
  addition to updating the bar's description. Previously such messages only
  updated the bar and never reached the sink.
- **Exceptions escaping a context are now logged as `Type: message`**
  (previously just `str(e)`, which for `KeyError('x')` was an undiagnosable
  `'x'`), followed by a closing line so the tree stays balanced. `safespace`
  logs the exception type too.
- **`pmap_iter` no longer silently ignores unknown keyword arguments** — a
  typo like `num_procs=8` now raises `TypeError` instead of silently running
  serially.
- **Durations are measured with `time.monotonic()`** instead of
  `time.time()`, making them immune to system clock adjustments.
- `lm.imap` now shares the module-level `DEFAULT_NUM_PROC` worker-count
  default (previously `imap` used `cpu // 2` while `pmap` used `cpu - 2`).

### Added

- `get_config()` — a snapshot of the current output configuration (`sink`,
  `level`, `format`, `logger`, `structured`, `colorize`).
- `{level}` is now a supported format-string placeholder.
- The `NO_COLOR` and `FORCE_COLOR` environment variables are respected when
  deciding whether to colorize output.
- `exc_info=True` on `log()` (and the level helpers) and on `safespace()`
  appends the active exception's traceback.
- `on_error="raise"|"skip"|"return"` and `ordered=False` (imap_unordered)
  options on `pmap`/`pmap_iter`/`pmap_run` and `lm.imap`/`lm.map`/`lm.run`.
- `@logmap.fn` supports generator and async-generator functions.
- Structured mode: task open/close lines carry `"event": "start"`/`"end"`,
  and end lines carry `"duration"`, making JSON output machine-parseable as
  lifecycle events.
- Stdlib logging bridge: `depth`/`task` are forwarded as `LogRecord`
  attributes (usable in a `Formatter` as `%(task)s` when set), the TRACE
  level name is registered with `logging`, and `logger` is documented to take
  precedence over `structured`.
- Type hints throughout the public API, plus a `py.typed` marker.
- This changelog.

## [0.3.2] - 2026-04-26

### Changed

- The vertical indent character is now a space (previously `￨`).

### Added

- Exported the box-drawing constants `TOP_CHAR`, `BOTTOM_CHAR`,
  `VERTICAL_CHAR`.

## [0.3.1] - 2026-04-26

### Added

- `@logmap.fn` decorator: wraps a function call in a logmap context, logging
  call arguments, return value, and duration. Works with async functions.
- Async context-manager support: `async with logmap(...)`.
- `lm.progress(iterable)` as a discoverable alias for `iter_progress`.

## [0.3.0] - 2026-04-26

### Added

- Thread-local nesting state, so concurrent threads don't interleave
  indentation or silence each other; sink writes are lock-guarded.
- `configure(logger=...)` to forward output through stdlib `logging`.
- `configure(structured=True)` for JSON-lines output with `depth`/`task`
  metadata.

### Changed

- Default to `forkserver` on macOS instead of the deprecated `fork` context.
- Consolidated `setup.cfg` + `setup.py` into `pyproject.toml`.

### Fixed

- License classifier corrected (was Apache; the license is GPL-3.0).

## [0.2.0] - 2026-04-19

### Changed

- Replaced loguru with a small direct-emit logger (~4x faster per log call);
  public API unchanged.
- Switched from the `multiprocess` package to stdlib `multiprocessing`.
- `pmap_iter` no longer mutates the caller's input; automatic chunksize.

### Added

- `configure(sink, level, format)` for redirecting output.
- Explicit `start()`/`stop()` lifecycle for use without a `with` block.
- pytest suite and CI (test matrix plus trusted publishing to PyPI).
