import io
import json
import logging
import logging.handlers
import platform
import sys
import threading
import time

import pytest

from logmap import configure, logmap, pmap, pmap_iter, pmap_run


# Module-level functions so they're picklable by multiprocess workers.
def _square(x):
    return x * x


def _add(x, y):
    return x + y


def _raise(x):
    raise RuntimeError("boom")


class TestContextManager:
    def test_started_and_ended_set(self):
        with logmap("task") as lm:
            assert lm.started is not None
            assert lm.ended is None
        assert lm.ended is not None
        assert lm.ended >= lm.started

    def test_duration_is_nonnegative(self):
        with logmap("task") as lm:
            pass
        assert lm.duration >= 0

    def test_duration_reflects_sleep(self):
        with logmap("task", precision=3) as lm:
            time.sleep(0.05)
        assert lm.duration >= 0.04

    def test_task_name_preserved(self):
        with logmap("hello world") as lm:
            assert lm.task_name == "hello world"


class TestNesting:
    def test_nested_levels(self):
        with logmap("outer") as outer:
            outer_num = outer.num
            with logmap("middle") as middle:
                assert middle.num == outer_num + 1
                with logmap("inner") as inner:
                    assert inner.num == outer_num + 2

    def test_depth_restored_after_nested_exit(self):
        with logmap("outer") as outer:
            start_num = outer.num
            with logmap("inner"):
                pass
            # after inner exits, a new nested block should get the same depth again
            with logmap("inner2") as inner2:
                assert inner2.num == start_num + 1

    def test_nested_output_indentation(self, captured_sink):
        with logmap("outer") as lm1:
            lm1.log("a")
            with logmap("middle") as lm2:
                lm2.log("b")
                with logmap("inner") as lm3:
                    lm3.log("c")
        lines = [l for l in captured_sink.getvalue().splitlines() if l.strip()]
        # log() uses inner_pref = vertical-char * num, so depth N -> N verticals
        a_line = next(l for l in lines if l.lstrip("￨ ").startswith("a"))
        b_line = next(l for l in lines if l.lstrip("￨ ").startswith("b"))
        c_line = next(l for l in lines if l.lstrip("￨ ").startswith("c"))
        assert a_line.count("￨") == 1
        assert b_line.count("￨") == 2
        assert c_line.count("￨") == 3


class TestQuiet:
    def test_quiet_context_disables_logging(self):
        assert logmap.is_quiet is False
        with logmap.quiet():
            assert logmap.is_quiet is True
        assert logmap.is_quiet is False

    def test_quiet_restores_on_exception(self):
        assert logmap.is_quiet is False
        with pytest.raises(ValueError):
            with logmap.quiet():
                raise ValueError("x")
        assert logmap.is_quiet is False

    def test_enable_disable_static(self):
        logmap.disable()
        assert logmap.is_quiet is True
        logmap.enable()
        assert logmap.is_quiet is False


class TestSafespace:
    def test_safespace_swallows_matching_exception(self):
        with logmap("task") as lm:
            with lm.safespace(ValueError):
                raise ValueError("ignore me")
        # no exception propagates

    def test_safespace_reraises_other_exceptions(self):
        with pytest.raises(TypeError):
            with logmap("task") as lm:
                with lm.safespace(ValueError):
                    raise TypeError("boom")


class TestLap:
    def test_lap_duration_increases(self):
        with logmap("task") as lm:
            lm.lap()
            time.sleep(0.02)
            d1 = lm.lap_duration
            time.sleep(0.02)
            d2 = lm.lap_duration
            assert d2 >= d1 >= 0.01


class TestMap:
    def test_map_serial(self):
        with logmap("m") as lm:
            out = lm.map(_square, [1, 2, 3, 4], num_proc=1, progress=False)
        assert out == [1, 4, 9, 16]

    def test_map_parallel(self):
        with logmap("m") as lm:
            out = lm.map(_square, list(range(8)), num_proc=2, progress=False)
        assert out == [i * i for i in range(8)]

    def test_imap_yields_generator(self):
        with logmap("m") as lm:
            gen = lm.imap(_square, [1, 2, 3], num_proc=1, progress=False)
            results = list(gen)
        assert results == [1, 4, 9]

    def test_run_exhausts_iterator(self):
        with logmap("m") as lm:
            # should return None and not raise
            result = lm.run(_square, [1, 2, 3], num_proc=1, progress=False)
        assert result is None


class TestPmap:
    def test_pmap_serial(self):
        assert pmap(_square, [1, 2, 3], num_proc=1, progress=False) == [1, 4, 9]

    def test_pmap_parallel(self):
        assert sorted(
            pmap(_square, list(range(6)), num_proc=2, progress=False)
        ) == [i * i for i in range(6)]

    def test_pmap_does_not_mutate_input_on_shuffle(self):
        original = [1, 2, 3, 4, 5]
        snapshot = list(original)
        pmap(_square, original, num_proc=1, shuffle=True, progress=False)
        assert original == snapshot

    def test_pmap_lim_truncates(self):
        out = pmap(_square, [1, 2, 3, 4, 5], lim=3, num_proc=1, progress=False)
        assert len(out) == 3

    def test_pmap_forwards_args_kwargs(self):
        out = pmap(_add, [1, 2, 3], args=(10,), num_proc=1, progress=False)
        assert out == [11, 12, 13]

    def test_pmap_iter_is_lazy(self):
        it = pmap_iter(_square, [1, 2, 3], num_proc=1, progress=False)
        assert not isinstance(it, list)
        assert next(it) == 1

    def test_pmap_run_returns_none(self):
        assert pmap_run(_square, [1, 2, 3], num_proc=1, progress=False) is None

    def test_pmap_empty_input(self):
        assert pmap(_square, [], num_proc=2, progress=False) == []

    def test_pmap_single_item_uses_serial_path(self):
        # num_proc > 1 but only one item -> falls back to serial; should still work
        assert pmap(_square, [7], num_proc=4, progress=False) == [49]


class TestVersion:
    def test_version_attribute_exists(self):
        import logmap as pkg

        assert isinstance(pkg.__version__, str)
        assert pkg.__version__


@pytest.fixture
def captured_sink():
    """Route logmap output to a StringIO for the duration of the test."""
    buf = io.StringIO()
    configure(sink=buf)
    try:
        yield buf
    finally:
        configure(sink=sys.stderr)


class TestConfigure:
    def test_redirects_output_to_custom_sink(self, captured_sink):
        with logmap("redirected"):
            pass
        output = captured_sink.getvalue()
        assert "redirected" in output

    def test_respects_level_filter(self, captured_sink):
        configure(sink=captured_sink, level="WARNING")
        try:
            with logmap("noisy") as lm:
                lm.debug("should be filtered")
                lm.warning("should appear")
        finally:
            configure(sink=captured_sink, level="DEBUG")
        output = captured_sink.getvalue()
        assert "should appear" in output
        assert "should be filtered" not in output

    def test_writes_to_file_path(self, tmp_path, captured_sink):
        log_file = tmp_path / "run.log"
        configure(sink=str(log_file))
        try:
            with logmap("to-file"):
                pass
        finally:
            configure(sink=captured_sink)
        assert "to-file" in log_file.read_text()


class TestStandaloneUsage:
    def test_bare_log_without_with_block(self, captured_sink):
        lm = logmap("standalone")
        lm.log("a message")
        assert "a message" in captured_sink.getvalue()

    def test_start_stop_lifecycle(self, captured_sink):
        lm = logmap("manual").start()
        assert lm.started is not None
        assert lm.ended is None
        lm.log("in the middle")
        time.sleep(0.01)
        lm.stop()
        assert lm.ended is not None
        assert lm.duration >= 0
        output = captured_sink.getvalue()
        assert "manual" in output
        assert "in the middle" in output

    def test_start_is_idempotent(self, captured_sink):
        lm = logmap("once").start()
        first_started = lm.started
        lm.start()  # should not reset timer
        assert lm.started == first_started
        lm.stop()

    def test_stop_is_idempotent(self, captured_sink):
        lm = logmap("once").start()
        lm.stop()
        first_ended = lm.ended
        lm.stop()  # no-op
        assert lm.ended == first_ended

    def test_stop_without_start_is_safe(self):
        lm = logmap("never-started")
        lm.stop()  # should not raise
        assert lm.ended is None


class TestThreadSafety:
    def test_nesting_depth_is_per_thread(self, captured_sink):
        """Two threads running logmap contexts should each get depth 1."""
        depths = {}
        barrier = threading.Barrier(2)

        def worker(name):
            with logmap(name) as lm:
                barrier.wait(timeout=5)
                depths[name] = lm.num

        t1 = threading.Thread(target=worker, args=("thread-a",))
        t2 = threading.Thread(target=worker, args=("thread-b",))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        assert depths["thread-a"] == 1
        assert depths["thread-b"] == 1

    def test_quiet_is_per_thread(self):
        """quiet() in one thread should not silence another thread."""
        results = {}
        barrier = threading.Barrier(2)

        def quiet_thread():
            with logmap.quiet():
                barrier.wait(timeout=5)
                results["quiet"] = logmap.is_quiet

        def loud_thread():
            barrier.wait(timeout=5)
            results["loud"] = logmap.is_quiet

        t1 = threading.Thread(target=quiet_thread)
        t2 = threading.Thread(target=loud_thread)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        assert results["quiet"] is True
        assert results["loud"] is False


class TestMpContext:
    def test_macos_does_not_use_fork(self):
        from logmap.logmap import CONTEXT
        if platform.system() == "Darwin":
            assert CONTEXT != "fork"

    def test_explicit_context_override(self):
        with logmap("m") as lm:
            out = lm.map(_square, [2, 3], num_proc=1, context="spawn", progress=False)
        assert out == [4, 9]


class TestStdlibLogging:
    def test_configure_logger_receives_messages(self, captured_sink):
        logger = logging.getLogger("logmap.test")
        logger.setLevel(logging.DEBUG)
        handler = logging.handlers.MemoryHandler(capacity=100)
        logger.addHandler(handler)
        try:
            configure(logger=logger)
            with logmap("stdlib") as lm:
                lm.info("hello from logmap")
            handler.flush()
            messages = [r.getMessage() for r in handler.buffer]
            assert any("hello from logmap" in m for m in messages)
        finally:
            configure(logger=None)
            logger.removeHandler(handler)

    def test_configure_logger_respects_level(self, captured_sink):
        logger = logging.getLogger("logmap.test.level")
        logger.setLevel(logging.DEBUG)
        handler = logging.handlers.MemoryHandler(capacity=100)
        logger.addHandler(handler)
        try:
            configure(logger=logger, level="WARNING")
            with logmap("filtered") as lm:
                lm.debug("should be filtered")
                lm.warning("should appear")
            handler.flush()
            messages = [r.getMessage() for r in handler.buffer]
            assert not any("should be filtered" in m for m in messages)
            assert any("should appear" in m for m in messages)
        finally:
            configure(logger=None, level="DEBUG")
            logger.removeHandler(handler)

    def test_clear_logger_resumes_sink_output(self, captured_sink):
        logger = logging.getLogger("logmap.test.clear")
        configure(logger=logger)
        configure(logger=None)
        with logmap("back to sink") as lm:
            lm.log("visible")
        assert "visible" in captured_sink.getvalue()


class TestLoud:
    def test_loud_context_enables_logging(self):
        logmap.disable()
        try:
            with logmap.loud():
                assert logmap.is_quiet is False
            assert logmap.is_quiet is True
        finally:
            logmap.enable()

    def test_loud_restores_on_exception(self):
        logmap.disable()
        try:
            with pytest.raises(ValueError):
                with logmap.loud():
                    raise ValueError("x")
            assert logmap.is_quiet is True
        finally:
            logmap.enable()


class TestVerbosity:
    def test_verbosity_1_enables_logging(self):
        logmap.disable()
        try:
            with logmap.verbosity(level=1):
                assert logmap.is_quiet is False
            assert logmap.is_quiet is True
        finally:
            logmap.enable()

    def test_verbosity_0_disables_logging(self):
        with logmap.verbosity(level=0):
            assert logmap.is_quiet is True
        assert logmap.is_quiet is False


class TestColorizedOutput:
    def test_colorized_branch(self):
        import importlib
        _mod = importlib.import_module("logmap.logmap")
        buf = io.StringIO()
        buf.isatty = lambda: True
        old_colorize = _mod._colorize
        configure(sink=buf)
        _mod._colorize = True
        try:
            with logmap("color-test") as lm:
                lm.log("colored")
            output = buf.getvalue()
            assert "\033[" in output
            assert "colored" in output
        finally:
            _mod._colorize = old_colorize
            configure(sink=sys.stderr)


class TestConfigureFileSwitch:
    def test_reconfigure_closes_previous_file(self, tmp_path, captured_sink):
        file1 = tmp_path / "a.log"
        file2 = tmp_path / "b.log"
        configure(sink=str(file1))
        with logmap("first"):
            pass
        configure(sink=str(file2))
        with logmap("second"):
            pass
        configure(sink=captured_sink)
        assert "first" in file1.read_text()
        assert "second" in file2.read_text()


class TestPadmin:
    def test_pads_short_string(self):
        from logmap.logmap import padmin
        result = padmin("hi", 10)
        assert len(result) == 10
        assert result.startswith("hi")

    def test_truncates_long_string(self):
        from logmap.logmap import padmin
        result = padmin("abcdefghij", 5)
        assert result == "abcde"

    def test_exact_length_unchanged(self):
        from logmap.logmap import padmin
        result = padmin("abcde", 5)
        assert result == "abcde"


class TestPmapEdgeCases:
    def test_num_proc_none_defaults_to_one(self):
        out = pmap(_square, [2, 3], num_proc=None, progress=False)
        assert sorted(out) == [4, 9]

    def test_num_proc_zero_defaults_to_one(self):
        out = pmap(_square, [2, 3], num_proc=0, progress=False)
        assert sorted(out) == [4, 9]

    def test_num_proc_exceeds_cpu_count(self):
        import multiprocessing as mp
        huge = mp.cpu_count() + 100
        out = pmap(_square, [2, 3], num_proc=huge, progress=False)
        assert sorted(out) == [4, 9]


class TestLevelMethods:
    def test_trace_writes_at_trace_level(self, captured_sink):
        configure(sink=captured_sink, level="TRACE")
        try:
            with logmap("t") as lm:
                lm.trace("trace msg")
            assert "trace msg" in captured_sink.getvalue()
        finally:
            configure(level="DEBUG")

    def test_error_writes_at_error_level(self, captured_sink):
        with logmap("t") as lm:
            lm.error("error msg")
        assert "error msg" in captured_sink.getvalue()


class TestImapEdgeCases:
    def test_imap_with_lim(self):
        with logmap("m") as lm:
            out = lm.map(_square, [1, 2, 3, 4, 5], lim=3, num_proc=1, progress=False)
        assert len(out) == 3

    def test_imap_default_num_proc(self):
        with logmap("m") as lm:
            out = lm.map(_square, [1, 2, 3], progress=False)
        assert sorted(out) == [1, 4, 9]


class TestLapTdesc:
    def test_lap_tdesc_is_string(self):
        with logmap("t") as lm:
            lm.lap()
            time.sleep(0.01)
            desc = lm.lap_tdesc
            assert isinstance(desc, str)
            assert len(desc) > 0


class TestCallable:
    def test_call_delegates_to_iter_progress(self, captured_sink):
        with logmap("t") as lm:
            items = list(lm([1, 2, 3], progress=False))
        assert items == [1, 2, 3]


class TestSafetyProperty:
    def test_safety_swallows_exception(self):
        with logmap("t") as lm:
            with lm.safety:
                raise ValueError("caught")

    def test_safety_lets_non_exception_through(self):
        with pytest.raises(KeyboardInterrupt):
            with logmap("t") as lm:
                with lm.safety:
                    raise KeyboardInterrupt


class TestStructuredOutput:
    def test_structured_emits_valid_json(self, captured_sink):
        configure(sink=captured_sink, structured=True)
        try:
            with logmap("json-test") as lm:
                lm.info("structured message")
            lines = [l for l in captured_sink.getvalue().splitlines() if l.strip()]
            for line in lines:
                record = json.loads(line)
                assert "ts" in record
                assert "level" in record
                assert "msg" in record
        finally:
            configure(structured=False)

    def test_structured_includes_depth_and_task(self, captured_sink):
        configure(sink=captured_sink, structured=True)
        try:
            with logmap("my-task") as lm:
                lm.info("hello")
            lines = captured_sink.getvalue().splitlines()
            info_line = next(l for l in lines if "hello" in l)
            record = json.loads(info_line)
            assert record["task"] == "my-task"
            assert "depth" in record
        finally:
            configure(structured=False)

    def test_structured_msg_is_clean(self, captured_sink):
        """In structured mode, msg should be the clean message (no indentation prefix)."""
        configure(sink=captured_sink, structured=True)
        try:
            with logmap("task") as lm:
                lm.info("clean text")
            lines = captured_sink.getvalue().splitlines()
            info_line = next(l for l in lines if "clean text" in l)
            record = json.loads(info_line)
            assert record["msg"] == "clean text"
        finally:
            configure(structured=False)

    def test_structured_off_by_default(self, captured_sink):
        with logmap("plain") as lm:
            lm.log("not json")
        output = captured_sink.getvalue()
        with pytest.raises(json.JSONDecodeError):
            json.loads(output.splitlines()[0])
