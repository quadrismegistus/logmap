# logmap — Repository Audit

**Date:** 2026-07-05
**Version audited:** 0.3.2 (commit `26b4359`, `main`, clean working tree)
**Method:** Full read of source, tests, packaging, and CI config; test suite run (86/86 pass in 0.58s on Python 3.14.3); every bug listed under "Confirmed bugs" was reproduced with a standalone script.

> **Resolution (2026-07-05, v0.4.0):** All findings below have been addressed — confirmed bugs B1–B11, design concerns D1–D11, documentation issues, packaging/CI items P1–P7, test gaps, and the feature ideas — with three exceptions: the license is unchanged (author's call), `requires-python` stays `>=3.9` (the audit only suggested considering a bump), and B11's shadowing is documented + mitigated via `get_config()` rather than a breaking rename. Each original repro script was re-run against the fixed code and shows correct behavior. See `CHANGELOG.md` for the change list.

---

## Summary

The package is small, readable, and well-tested for its happy paths. The most serious problems cluster around **state management when things go wrong**: a caught exception permanently corrupts nesting depth, breaking out of a progress loop silently swallows all subsequent log messages, and the thread-local nesting state does not isolate concurrent asyncio tasks even though async support is an advertised feature. There are also several doc/behavior contradictions and packaging-metadata issues worth fixing before the next release.

| # | Finding | Severity |
|---|---------|----------|
| B1 | Caught exception inside a nested context corrupts global nesting state | High |
| B2 | Early exit from `progress()`/`imap()` leaves stale pbar; later `log()` calls are silently lost | High |
| B3 | Concurrent asyncio tasks corrupt each other's nesting depth | High |
| B4 | `configure(sink=None)` does not reset to stderr, contradicting its docstring | Medium |
| B5 | Unknown level names (`"WARN"`, typos) silently drop messages | Medium |
| B6 | Messages logged while a progress bar is active never reach the sink (structured/file modes lose data) | Medium |
| B7 | Custom `format` strings with unrecognized placeholders raise `KeyError` on every emit | Medium |
| B8 | `duration`/`tdesc` raise `TypeError` on a never-started instance | Low |
| B9 | `min_seconds_logworthy` suppresses only the closing line, leaving unbalanced output | Low |
| B10 | `@logmap.fn` on generator functions times nothing and logs `>>> <generator object …>` | Low |
| B11 | `logmap.logmap` (the submodule) is shadowed by the class in package namespace | Low |

Sections below: [Confirmed bugs](#confirmed-bugs) · [Design & robustness concerns](#design--robustness-concerns) · [Documentation issues](#documentation-issues) · [Packaging & CI](#packaging--ci) · [Test gaps](#test-gaps) · [Feature ideas](#feature-ideas)

---

## Confirmed bugs

### B1. Caught exception corrupts nesting state — **High**

`logmap/logmap.py:663-668` — when `stop()` sees an exception, it zeroes the thread-local counters unconditionally:

```python
if exc_type:
    _nesting.logwatch_id = 0
    _nesting.num_logwatches = 0
```

This assumes the exception unwinds the whole stack. If user code catches it while still inside an outer `logmap` context, the depth accounting is destroyed for the rest of the thread's lifetime — and the outer context's own `stop()` then decrements the counter to **-1**:

```python
with logmap("outer") as outer:          # outer.num == 1
    try:
        with logmap("inner"):
            raise ValueError()
    except ValueError:
        pass
    with logmap("sibling") as sib:
        print(sib.num)                  # 1 — expected 2
# after outer exits: num_logwatches == -1
with logmap("next-top") as nt:
    print(nt.num)                       # 0 — expected 1
```

Observed exactly as above. Every subsequent context on the thread gets the wrong indentation, and since `@logmap.fn` routes exceptions through this same path, any decorated function whose exception is caught by the caller triggers it.

**Fix:** on the exception path, restore the counter the same way the normal path does (decrement if `self.iterated_num`) instead of zeroing both globals. The `logwatch_id = 0` reset serves no purpose (see D9).

### B2. Early exit from `progress()` leaves a stale pbar; subsequent logs are silently lost — **High**

`logmap/logmap.py:506-517` — `iter_progress` only cleans up when the iterable is exhausted:

```python
yield from self.pbar
self.pbar.close()
self.pbar = None
```

If the consumer `break`s, `return`s, or an exception propagates, the generator is closed at the `yield` and both cleanup lines are skipped. `self.pbar` stays set, so every later `lm.log(...)` takes the pbar branch in `log()` (`logmap/logmap.py:462-470`) and becomes a `set_description` call on a dead bar instead of a log line:

```python
with logmap("task") as lm:
    for x in lm.progress([1, 2, 3, 4, 5]):
        if x == 3:
            break
    lm.log("IMPORTANT MESSAGE")   # never reaches the sink — verified
```

**Fix:** wrap in `try/finally`:

```python
try:
    yield from self.pbar
finally:
    self.pbar.close()
    self.pbar = None
```

The same applies to `imap()`, which delegates to `iter_progress`.

### B3. Concurrent asyncio tasks corrupt each other's nesting depth — **High**

`logmap/logmap.py:82-89` stores nesting state in `threading.local`, but all asyncio tasks in an event loop share one thread. The async context-manager support added in v0.3.1 (`__aenter__`/`__aexit__`, `logmap/logmap.py:686-690`) therefore breaks as soon as two tasks run concurrently:

```python
async def task(name):
    async with logmap(name) as lm:        # A: num=1,  B: num=2
        await asyncio.sleep(0.01)
        async with logmap(name + "-in") as lm2:   # A: num=3, B: num=4
            ...

await asyncio.gather(task("A"), task("B"))
```

Observed: `{'A': (1, 3), 'B': (2, 4)}` — each task should see `(1, 2)`. Indentation interleaves and, combined with B1's decrement logic, depths drift.

**Fix:** use `contextvars.ContextVar` instead of `threading.local`. Context variables are both thread-local *and* task-local, so the existing thread-safety tests keep passing while async isolation becomes correct. (`is_quiet` should move too, so `logmap.quiet()` in one task doesn't silence a concurrent one.)

### B4. `configure(sink=None)` does not reset to stderr — **Medium**

The docstring (`logmap/logmap.py:154-155`) promises: *"sink: … or `None` to reset to stderr"*. The implementation (`logmap/logmap.py:169`) uses `if sink is not None:` — `None` means "keep current sink", and there is no way to express "reset to stderr" other than passing `sys.stderr` explicitly. Verified: after `configure(sink=buf); configure(sink=None)` the sink is still the `StringIO`.

**Fix:** either adopt the `_UNSET` sentinel already used for `logger` (making `None` mean reset) or fix the docstring. The sentinel approach matches the documented contract and is symmetric with `logger=None`.

### B5. Unknown level names silently drop messages — **Medium**

`logmap/logmap.py:118`: `lvl_int = LEVELS.get(level, 0)` maps any unrecognized name to 0, which is below every threshold, so the message vanishes without a trace. `lm.log("...", level="WARN")` (a natural typo for `WARNING`) prints nothing. Verified.

**Fix:** raise `KeyError`/`ValueError` on unknown levels, or at minimum fall back to `DEBUG` rather than "below everything". Accepting the common aliases `WARN` and `FATAL` would also be cheap.

### B6. Messages logged during an active progress bar never reach the sink — **Medium**

`logmap/logmap.py:462-470` — while `self.pbar` is set, `log()` reroutes *every* message (including `warning()`/`error()`) into the tqdm bar description. The bar writes to stderr; the configured sink, file, stdlib logger, and structured-JSON stream get nothing. Verified in structured mode: 0 of 3 messages logged inside a `progress()` loop reached the sink. With `progress=False` the disabled bar still exists, so the messages are lost entirely, not even displayed.

**Fix:** always `_emit` the message (so files/JSON/logger receive it), and *additionally* update the bar description when one is active. Use `tqdm.write()` for the terminal case to avoid garbling the bar.

### B7. Custom format strings with unknown placeholders raise `KeyError` on every emit — **Medium**

`configure(format=...)` documents the five understood placeholders, but nothing validates the string; a user writing `"{level} {msg}"` (a very natural guess) gets a `KeyError: 'level'` from deep inside `_emit` on the first log call. Verified.

**Fix:** validate at `configure()` time by test-formatting once, so the error surfaces immediately with a clear message; and/or supply `level` as a legitimate placeholder — it is surprising that the level *isn't* available to the format string.

### B8. `duration` / `tdesc` raise `TypeError` before `start()` — **Low**

`logmap/logmap.py:606-611`: `time.time() - self.started` with `started=None`. The standalone-logger usage shown in the README (`lm = logmap('app'); lm.log('ready')`) never starts the timer, so touching `lm.duration` on such an instance crashes with `TypeError: unsupported operand type(s) for -: 'float' and 'NoneType'`. Verified.

**Fix:** return `0` (or `None`) when `self.started is None`.

### B9. `min_seconds_logworthy` produces unbalanced output — **Low**

`logmap/logmap.py:673-676` suppresses the closing `⎿` line for fast tasks, but the opening `⎾` line was already printed at `start()`. Verified: a sub-threshold task emits exactly one line — an opening bracket that never closes, which reads as a still-running task and breaks the visual tree. The parameter is also undocumented in the README.

**Fix options:** buffer the opening line and flush it only if the task turns out logworthy (changes streaming behavior), or suppress neither and document that the option only affects the close line, or deprecate it.

### B10. `@logmap.fn` mishandles generator functions — **Low**

`logmap/logmap.py:394-416` handles coroutine functions but not generator functions. Decorating a generator times only its *creation* (always "0 seconds") and logs `>>> <generator object gen at 0x…>` as the return value. Verified.

**Fix:** detect `inspect.isgeneratorfunction` and either wrap with a generator that keeps the context open across iteration, or skip return-value logging and document the limitation.

### B11. Package attribute shadows the submodule — **Low**

`logmap/__init__.py:3` (`from .logmap import logmap`) rebinds the package attribute `logmap.logmap` from the submodule to the class. As a result `import logmap.logmap as m` yields the *class*, and idioms like monkeypatching `logmap.logmap._sink` in tests fail with `AttributeError: type object 'logmap' has no attribute '_sink'` (encountered while writing the repro scripts; the repo's own tests work around it with `importlib.import_module`). Long-established API, so probably not worth renaming — but worth documenting, and internal module-level state could be exposed through explicit functions (`get_config()`) instead.

---

## Design & robustness concerns

*Not bugs with a one-line repro, but real risks or inconsistencies found by inspection.*

- **D1 — `fork` start method on Linux** (`logmap/logmap.py:25-34`). The Darwin fix in v0.3.0 stopped at macOS; Linux still defaults to `fork`, which deadlocks when the parent has running threads (the very reason CPython moved *its* Linux default to `forkserver` in 3.14, and deprecated fork-with-threads in 3.12). Since logmap itself is thread-aware and holds a module lock, consider defaulting Linux to `forkserver` too (keeping the `context=` escape hatch).
- **D2 — wall-clock timing** (`logmap/logmap.py:646,667`). Durations use `time.time()`, which jumps with NTP adjustments and DST-less clock changes. `time.monotonic()` is the correct tool for elapsed time; `datetime.now()` can stay for the displayed timestamp.
- **D3 — config reads outside the lock** (`logmap/logmap.py:115-142`). `_emit` reads `_min_level`, `_format`, `_colorize`, `_structured`, `_logger` without holding `_lock` (only the final write is locked). A concurrent `configure()` can produce a line rendered with a torn mix of old/new settings. Benign in CPython today, but cheap to fix by snapshotting config under the lock.
- **D4 — `pmap_iter` swallows unknown kwargs** (`logmap/logmap.py:220`). The `**_unused` catch-all means a typo like `pmap(f, xs, num_procs=8)` silently runs serially instead of raising `TypeError`. Recommend removing it (or emitting a warning) — its original reason (forwarding from `imap`) no longer requires it.
- **D5 — inconsistent parallelism defaults.** Module-level `DEFAULT_NUM_PROC` is `cpu-2` (`logmap/logmap.py:36-37`) while `imap()` defaults to `cpu//2` (`logmap/logmap.py:550-551`). On a 16-core machine, `pmap(f, xs)` uses 14 workers but `lm.map(f, xs)` uses 8. Pick one default.
- **D6 — stdlib-logger bridge loses information** (`logmap/logmap.py:123-126`). When `configure(logger=...)` is set: `structured=True` is silently ignored; the `depth`/`task` metadata is dropped (could be forwarded via `logger.log(..., extra=...)`); and TRACE-level records render as `Level 5` unless the app calls `logging.addLevelName(5, "TRACE")` itself.
- **D7 — cross-instance bar garbling.** While one instance's tqdm bar is live on stderr, another instance's `_emit` writes raw lines to the same stream, corrupting the bar's rendering. `tqdm.write()` (or `tqdm.external_write_mode()`) exists for exactly this.
- **D8 — `safespace` hides too much** (`logmap/logmap.py:694-704`). It defaults to catching bare `Exception` and logs only `str(e)` — no exception type, no traceback. `str(e)` for something like `KeyError('x')` logs just `'x'`, which is nearly undiagnosable. Include the type name (as `stop()`'s error path already does) and consider an `exc_info`/traceback option.
- **D9 — dead state.** `self.id` / `_nesting.logwatch_id` (`logmap/logmap.py:428-429`) are written and reset but never read. Remove, or use them for something (e.g. structured-output correlation ids).
- **D10 — falsy messages skipped.** `log()`'s `if _nesting.is_quiet or not msg` (`logmap/logmap.py:459`) means `lm.log(0)` prints nothing. Use `msg is None or msg == ""` if the intent is only to skip empties.
- **D11 — `shuffle` + `lim` interaction** (`logmap/logmap.py:230-234`). Shuffling happens *before* truncation, so `pmap(f, xs, lim=10, shuffle=True)` processes a random sample rather than the first 10 shuffled. Plausibly intended (sampling), but worth a docstring sentence; also note that with `shuffle=True` results no longer align with input order.

---

## Documentation issues

- **README sample outputs are stale for v0.3.2.** The Duration, Nested, and Parallel-map examples (README lines ~39, 54-60, 78, 134) show `￨` as the indentation character, but v0.3.2 changed `VERTICAL_CHAR` to a space (`logmap/logmap.py:54`). Actual output no longer matches the README.
- **`configure` docstring vs behavior** — see B4.
- **Undocumented public surface:** `min_seconds_logworthy`, `precision`, `verbosity()`, `lap()`/`lap_duration`/`lap_tdesc`, `safespace`/`safety`, `nap()` (used in an example, never explained), `log(..., linelim=)`, the `lm(...)` call alias for `iter_progress`, and the exported `TOP_CHAR`/`BOTTOM_CHAR`/`VERTICAL_CHAR` constants (the stated point of v0.3.2). A short API reference section would cover this.
- **No CHANGELOG.** Version history currently lives only in commit messages; a `CHANGELOG.md` would help PyPI users, especially with behavior changes like the v0.3.2 indent-char switch.

---

## Packaging & CI

- **P1 — setuptools floor too low for the license format.** `pyproject.toml` uses the PEP 639 SPDX string form (`license = "GPL-3.0-only"`), which setuptools accepts from v77. The declared `requires = ["setuptools>=64"]` permits build environments that will reject the field. Bump to `setuptools>=77`.
- **P2 — Python version coverage is behind.** CI matrix and classifiers stop at 3.12; 3.13 and 3.14 are both released (and the local dev venv is already 3.14.3, where the suite passes). Add 3.13/3.14 to both workflow matrices and the classifiers. Also: Python 3.9 reached EOL in October 2025 — consider `requires-python = ">=3.10"` at the next minor bump.
- **P3 — no Windows CI.** The code special-cases Windows (`spawn` context, `logmap/logmap.py:29-30`) and the classifiers claim OS-independence, but no Windows leg exists. Add `windows-latest` to the tests matrix.
- **P4 — `.python-version` pins 3.12.0** while local development happens on 3.14. The repo's own `.gitignore` template notes that libraries usually *don't* commit this file; either drop it or keep it current.
- **P5 — `requirements.txt` duplicates `pyproject.toml` dependencies.** Two sources of truth that can drift. For a library, drop `requirements.txt` (or reduce it to `-e .`).
- **P6 — `publish.yml` `workflow_dispatch` path is broken.** The `github-release` job runs `gh release upload "${{ github.ref_name }}"`; on a manual dispatch from `main`, `ref_name` is `main`, not a release tag, so the job fails after already publishing to PyPI. Either drop `workflow_dispatch`, or gate the `github-release` job on `github.event_name == 'release'`.
- **P7 — no coverage in CI.** A `.coverage` file exists locally, so coverage is being measured by hand; wiring `pytest --cov` plus a reporter into `tests.yml` would make it visible.

---

## Test gaps

The suite (86 tests) covers happy paths well. Missing, in rough priority order:

1. **Caught-exception nesting** — would have caught B1: catch an exception from an inner context, assert the outer depth is intact.
2. **Early exit from `progress()`/`imap()`** — would have caught B2: break mid-loop, assert `lm.pbar is None` and later logs reach the sink.
3. **Concurrent asyncio tasks** — would have caught B3: `asyncio.gather` two nested contexts, assert each sees depths (1, 2). Existing async tests are sequential only.
4. **Worker exceptions in `pmap`** — `tests/test_logmap.py:36-37` defines a `_raise` helper that no test uses; the behavior when a mapped function raises in a worker is untested.
5. **Custom `format` strings** — no test configures a non-default format (would have caught B7).
6. **`configure(sink=None)` semantics** (B4), **unknown level names** (B5), and **logging during an active bar in structured mode** (B6).

---

## Feature ideas

- **`contextvars`-based state** — the proper fix for B3, and makes `quiet()` task-scoped under asyncio.
- **Type hints + `py.typed`** — the API is small; annotating it is a day's work and makes the library friendlier in typed codebases.
- **`NO_COLOR` / `FORCE_COLOR` support** — colorization currently keys only off `isatty()`; honoring these conventions is a few lines in `_refresh_colorize`.
- **Structured-mode lifecycle events** — open/close lines currently emit `"msg": "⎾ outer"`; emitting `{"event": "start"|"end", "task": ..., "duration": ...}` would make JSON output genuinely machine-parseable.
- **Error-handling policy for `pmap`** — today one raising worker aborts the whole map; an `on_error="raise"|"skip"|"return"` option is a common need.
- **`imap_unordered` option** — for maps where completion order doesn't matter, `pool.imap_unordered` gives better throughput with a one-line change.
- **Traceback logging** — `exc_info=True`-style option on `log()`/`safespace` (see D8).
- **License consideration** — GPL-3.0-only on a small utility library prevents use from non-GPL-compatible codebases; if broad adoption is a goal, MIT/Apache-2.0/LGPL are the usual choices for this category. Entirely the author's call.

---

## Repro scripts

The scripts used to confirm B1–B10 live outside the repo (session scratchpad) and are trivially reconstructible from the snippets above. All were run against `logmap` 0.3.2 with Python 3.14.3 from the project venv.
