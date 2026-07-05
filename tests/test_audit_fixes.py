"""Tests for the behaviors introduced by the audit-fix rewrite of logmap.logmap.

Each test class covers one audit finding (B1-B10, D4, D6, etc. — see AUDIT.md).
"""

import asyncio
import io
import json
import logging
import logging.handlers
import sys
import time

import pytest

from logmap import (
    BOTTOM_CHAR,
    DEFAULT_FORMAT,
    LEVELS,
    TOP_CHAR,
    configure,
    get_config,
    logmap,
    pmap,
)

# NOTE: `import logmap.logmap as m` would return the CLASS (the package
# attribute is shadowed by `from .logmap import logmap` in __init__.py), so
# the submodule must be fetched from sys.modules.
_mod = sys.modules["logmap.logmap"]


# ---------------------------------------------------------------------------
# Module-level functions so they're picklable by multiprocess workers.
# ---------------------------------------------------------------------------

def _square(x):
    return x * x


def _raise_on_3(x):
    if x == 3:
        raise RuntimeError("boom")
    return x


@logmap.fn
def _decorated_gen(n):
    for i in range(n):
        yield i


@logmap.fn
async def _decorated_agen(n):
    for i in range(n):
        yield i


@pytest.fixture
def captured_sink():
    """Route logmap output to a StringIO for the duration of the test."""
    buf = io.StringIO()
    configure(sink=buf)
    try:
        yield buf
    finally:
        configure(sink=sys.stderr)


# ---------------------------------------------------------------------------
# B1 — caught exception must not corrupt nesting state
# ---------------------------------------------------------------------------

class TestCaughtExceptionNesting:
    def test_sibling_depth_intact_after_caught_exception(self, captured_sink):
        with logmap("outer") as outer:
            try:
                with logmap("inner"):
                    raise ValueError("boom")
            except ValueError:
                pass
            with logmap("sib") as sib:
                assert sib.num == outer.num + 1

    def test_counter_zero_and_fresh_context_depth_one(self, captured_sink):
        with logmap("outer"):
            try:
                with logmap("inner"):
                    raise ValueError("boom")
            except ValueError:
                pass
        assert _mod._num_logwatches.get() == 0
        with logmap("fresh") as fresh:
            assert fresh.num == 1
        assert _mod._num_logwatches.get() == 0


# ---------------------------------------------------------------------------
# B2 — early exit from progress() must clean up the pbar
# ---------------------------------------------------------------------------

class TestProgressEarlyExit:
    def test_break_resets_pbar_and_later_logs_reach_sink(self, captured_sink):
        with logmap("task") as lm:
            for x in lm.progress([1, 2, 3, 4, 5], progress=False):
                if x == 3:
                    break
            assert lm.pbar is None
            lm.log("after")
        assert "after" in captured_sink.getvalue()

    def test_exception_in_loop_resets_pbar(self, captured_sink):
        with logmap("task") as lm:
            with pytest.raises(RuntimeError, match="mid-loop"):
                for _ in lm.progress([1, 2, 3], progress=False):
                    raise RuntimeError("mid-loop")
            assert lm.pbar is None


# ---------------------------------------------------------------------------
# B3 — concurrent asyncio tasks must have isolated nesting depth
# ---------------------------------------------------------------------------

class TestAsyncioIsolation:
    def test_concurrent_tasks_each_see_depths_1_and_2(self, captured_sink):
        async def task(name, results):
            async with logmap(f"{name}-outer") as lm1:
                await asyncio.sleep(0.01)
                async with logmap(f"{name}-inner") as lm2:
                    await asyncio.sleep(0.01)
                    results[name] = (lm1.num, lm2.num)

        async def main():
            results = {}
            await asyncio.gather(task("A", results), task("B", results))
            return results

        results = asyncio.run(main())
        assert results["A"] == (1, 2)
        assert results["B"] == (1, 2)


# ---------------------------------------------------------------------------
# B4 — configure(sink=None) resets to stderr
# ---------------------------------------------------------------------------

class TestSinkNoneResetsToStderr:
    def test_sink_none_resets_to_stderr(self):
        buf = io.StringIO()
        configure(sink=buf)
        try:
            assert get_config()["sink"] is buf
            configure(sink=None)
            assert get_config()["sink"] is sys.stderr
        finally:
            configure(sink=sys.stderr)


# ---------------------------------------------------------------------------
# B5 — level aliases accepted, unknown levels raise
# ---------------------------------------------------------------------------

class TestLevelHandling:
    def test_warn_alias_emits(self, captured_sink):
        with logmap("t") as lm:
            lm.log("warned msg", level="WARN")
        assert "warned msg" in captured_sink.getvalue()

    def test_fatal_alias_emits_as_critical(self, captured_sink):
        configure(structured=True)
        try:
            with logmap("t") as lm:
                lm.log("fatal msg", level="FATAL")
        finally:
            configure(structured=False)
        line = next(
            l for l in captured_sink.getvalue().splitlines() if "fatal msg" in l
        )
        record = json.loads(line)
        assert record["level"] == "CRITICAL"

    def test_bogus_level_on_log_raises(self, captured_sink):
        with logmap("t") as lm:
            with pytest.raises(ValueError):
                lm.log("x", level="BOGUS")

    def test_bogus_level_at_construction_raises(self):
        with pytest.raises(ValueError):
            logmap("t", level="BOGUS")

    def test_configure_accepts_warn_alias(self, captured_sink):
        configure(level="WARN")
        try:
            assert get_config()["level"] == LEVELS["WARNING"]
            with logmap("t") as lm:
                lm.log("debug msg", level="DEBUG")
                lm.log("warning msg", level="WARNING")
        finally:
            configure(level="DEBUG")
        out = captured_sink.getvalue()
        assert "warning msg" in out
        assert "debug msg" not in out


# ---------------------------------------------------------------------------
# B6 — logging during an active progress bar reaches the sink
# ---------------------------------------------------------------------------

class TestLogDuringProgressBar:
    def test_plain_mode_messages_reach_sink(self, captured_sink):
        with logmap("task") as lm:
            for x in lm.progress([1, 2, 3], progress=False):
                assert lm.pbar is not None
                lm.log(f"processing {x}")
        out = captured_sink.getvalue()
        for x in (1, 2, 3):
            assert f"processing {x}" in out

    def test_structured_mode_messages_reach_sink(self, captured_sink):
        configure(structured=True)
        try:
            with logmap("task") as lm:
                for x in lm.progress([1, 2, 3], progress=False):
                    assert lm.pbar is not None
                    lm.log(f"processing {x}")
        finally:
            configure(structured=False)
        records = [
            json.loads(l)
            for l in captured_sink.getvalue().splitlines()
            if l.strip()
        ]
        msgs = {r["msg"] for r in records}
        for x in (1, 2, 3):
            assert f"processing {x}" in msgs


# ---------------------------------------------------------------------------
# B7 — format strings validated at configure() time
# ---------------------------------------------------------------------------

class TestFormatValidation:
    def test_bogus_placeholder_raises_and_config_unchanged(self):
        before = get_config()["format"]
        with pytest.raises(ValueError):
            configure(format="{bogus}")
        assert get_config()["format"] == before

    def test_level_placeholder_supported(self, captured_sink):
        configure(format="{level} {msg}")
        try:
            with logmap("t") as lm:
                lm.log("leveled msg", level="WARNING")
        finally:
            configure(format=DEFAULT_FORMAT)
        out = captured_sink.getvalue()
        assert "WARNING" in out
        assert "leveled msg" in out


# ---------------------------------------------------------------------------
# B8 — duration/tdesc safe on a never-started instance
# ---------------------------------------------------------------------------

class TestUnstartedDuration:
    def test_duration_is_zero(self):
        assert logmap("x").duration == 0.0

    def test_tdesc_is_string(self):
        desc = logmap("x").tdesc
        assert isinstance(desc, str)
        assert desc


# ---------------------------------------------------------------------------
# B9 — min_seconds_logworthy emits balanced output (or none at all)
# ---------------------------------------------------------------------------

class TestMinSecondsLogworthy:
    def test_fast_task_emits_nothing(self, captured_sink):
        with logmap("speedy", min_seconds_logworthy=10):
            pass
        assert captured_sink.getvalue() == ""

    def test_fast_task_with_inner_log_is_balanced(self, captured_sink):
        with logmap("speedy-chatty", min_seconds_logworthy=10) as lm:
            lm.log("inner msg")
        out = captured_sink.getvalue()
        assert TOP_CHAR in out
        assert "speedy-chatty" in out
        assert "inner msg" in out
        assert BOTTOM_CHAR in out

    def test_slow_task_emits_open_and_close(self, captured_sink):
        with logmap("slowish", min_seconds_logworthy=0.01, precision=3):
            time.sleep(0.05)
        out = captured_sink.getvalue()
        assert TOP_CHAR in out
        assert "slowish" in out
        assert BOTTOM_CHAR in out


# ---------------------------------------------------------------------------
# B10 — @logmap.fn on (async) generator functions
# ---------------------------------------------------------------------------

class TestGeneratorDecorator:
    def test_yields_correct_items_and_balanced_output(self, captured_sink):
        assert list(_decorated_gen(3)) == [0, 1, 2]
        out = captured_sink.getvalue()
        assert "_decorated_gen(3)" in out
        assert TOP_CHAR in out
        assert BOTTOM_CHAR in out
        assert "<generator object" not in out

    def test_early_break_does_not_log_generatorexit(self, captured_sink):
        for x in _decorated_gen(100):
            if x == 2:
                break
        out = captured_sink.getvalue()
        assert "GeneratorExit" not in out

    def test_async_generator_yields_correct_items(self, captured_sink):
        async def collect():
            return [x async for x in _decorated_agen(3)]

        assert asyncio.run(collect()) == [0, 1, 2]
        out = captured_sink.getvalue()
        assert "_decorated_agen(3)" in out
        assert "<async_generator" not in out


# ---------------------------------------------------------------------------
# get_config()
# ---------------------------------------------------------------------------

class TestGetConfig:
    def test_snapshot_reflects_current_config(self, captured_sink):
        cfg = get_config()
        assert set(cfg) == {
            "sink", "level", "format", "logger", "structured", "colorize",
        }
        assert cfg["sink"] is captured_sink
        assert cfg["level"] == LEVELS["DEBUG"]
        assert cfg["format"] == DEFAULT_FORMAT
        assert cfg["logger"] is None
        assert cfg["structured"] is False
        assert isinstance(cfg["colorize"], bool)


# ---------------------------------------------------------------------------
# NO_COLOR / FORCE_COLOR
# ---------------------------------------------------------------------------

class TestColorEnvVars:
    def test_no_color_wins_over_tty(self, monkeypatch):
        monkeypatch.delenv("FORCE_COLOR", raising=False)
        monkeypatch.setenv("NO_COLOR", "1")
        buf = io.StringIO()
        buf.isatty = lambda: True
        try:
            configure(sink=buf)
            assert get_config()["colorize"] is False
        finally:
            monkeypatch.delenv("NO_COLOR", raising=False)
            configure(sink=sys.stderr)

    def test_force_color_wins_over_non_tty(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        buf = io.StringIO()  # isatty() -> False
        try:
            configure(sink=buf)
            assert get_config()["colorize"] is True
        finally:
            monkeypatch.delenv("FORCE_COLOR", raising=False)
            configure(sink=sys.stderr)


# ---------------------------------------------------------------------------
# pmap on_error policy
# ---------------------------------------------------------------------------

class TestPmapOnError:
    def test_default_raise_propagates_serial(self):
        with pytest.raises(RuntimeError, match="boom"):
            pmap(_raise_on_3, [1, 2, 3, 4, 5], num_proc=1, progress=False)

    def test_skip_drops_failing_item_serial(self):
        out = pmap(
            _raise_on_3, [1, 2, 3, 4, 5],
            num_proc=1, progress=False, on_error="skip",
        )
        assert out == [1, 2, 4, 5]

    def test_return_yields_exception_in_place_serial(self):
        out = pmap(
            _raise_on_3, [1, 2, 3, 4, 5],
            num_proc=1, progress=False, on_error="return",
        )
        assert out[0:2] == [1, 2]
        assert isinstance(out[2], RuntimeError)
        assert str(out[2]) == "boom"
        assert out[3:] == [4, 5]

    def test_skip_drops_failing_item_parallel(self):
        out = pmap(
            _raise_on_3, [1, 2, 3, 4, 5],
            num_proc=2, progress=False, on_error="skip",
        )
        assert sorted(out) == [1, 2, 4, 5]

    def test_bogus_on_error_raises_valueerror(self):
        with pytest.raises(ValueError):
            pmap(_square, [1, 2], num_proc=1, progress=False, on_error="bogus")


# ---------------------------------------------------------------------------
# pmap ordered=False
# ---------------------------------------------------------------------------

class TestPmapUnordered:
    def test_unordered_returns_same_multiset(self):
        expected = [i * i for i in range(6)]
        out = pmap(_square, range(6), num_proc=2, ordered=False, progress=False)
        assert sorted(out) == expected


# ---------------------------------------------------------------------------
# D4 — unknown kwargs rejected
# ---------------------------------------------------------------------------

class TestUnknownKwargsRejected:
    def test_typo_kwarg_raises_typeerror(self):
        with pytest.raises(TypeError):
            pmap(_square, [1, 2], num_procs=4, progress=False)


# ---------------------------------------------------------------------------
# exc_info traceback logging
# ---------------------------------------------------------------------------

class TestExcInfo:
    def test_log_exc_info_appends_traceback(self, captured_sink):
        with logmap("t") as lm:
            try:
                raise ValueError("boom")
            except ValueError:
                lm.log("failed", exc_info=True)
        assert "Traceback" in captured_sink.getvalue()

    def test_structured_record_has_traceback_key(self, captured_sink):
        configure(structured=True)
        try:
            with logmap("t") as lm:
                try:
                    raise ValueError("boom")
                except ValueError:
                    lm.log("failed", exc_info=True)
        finally:
            configure(structured=False)
        record = next(
            json.loads(l)
            for l in captured_sink.getvalue().splitlines()
            if "failed" in l
        )
        assert "traceback" in record
        assert "ValueError" in record["traceback"]

    def test_safespace_exc_info_logs_traceback(self, captured_sink):
        with logmap("t") as lm:
            with lm.safespace(ValueError, exc_info=True):
                raise ValueError("swallowed")
        out = captured_sink.getvalue()
        assert "Traceback" in out
        assert "ValueError" in out


# ---------------------------------------------------------------------------
# D6 — stdlib logger bridge forwards task/depth and registers TRACE
# ---------------------------------------------------------------------------

class TestStdlibBridgeExtras:
    def test_records_carry_task_and_depth(self, captured_sink):
        logger = logging.getLogger("logmap.test.audit.extras")
        logger.setLevel(logging.DEBUG)
        handler = logging.handlers.MemoryHandler(capacity=100)
        logger.addHandler(handler)
        try:
            configure(logger=logger)
            with logmap("mytask") as lm:
                lm.info("hello")
            handler.flush()
            record = next(
                r for r in handler.buffer if "hello" in r.getMessage()
            )
            assert record.task == "mytask"
            assert isinstance(record.depth, int)
        finally:
            configure(logger=None)
            logger.removeHandler(handler)

    def test_trace_level_name_registered(self):
        logger = logging.getLogger("logmap.test.audit.trace")
        try:
            configure(logger=logger)
            assert logging.getLevelName(5) == "TRACE"
        finally:
            configure(logger=None)


# ---------------------------------------------------------------------------
# Structured lifecycle events
# ---------------------------------------------------------------------------

class TestStructuredLifecycle:
    def test_start_and_end_events(self, captured_sink):
        configure(structured=True)
        try:
            with logmap("lifecycle-task"):
                pass
        finally:
            configure(structured=False)
        records = [
            json.loads(l)
            for l in captured_sink.getvalue().splitlines()
            if l.strip()
        ]
        start = next(r for r in records if r.get("event") == "start")
        end = next(r for r in records if r.get("event") == "end")
        assert start["msg"] == "lifecycle-task"
        assert isinstance(end["duration"], (int, float))


# ---------------------------------------------------------------------------
# Exception path emits both the error line and the closing line
# ---------------------------------------------------------------------------

class TestExceptionCloseLine:
    def test_error_line_and_close_line_emitted(self, captured_sink):
        with pytest.raises(ValueError):
            with logmap("failing-task"):
                raise ValueError("boom")
        out = captured_sink.getvalue()
        assert "ValueError: boom" in out
        assert BOTTOM_CHAR in out
