from logmap import *
import unittest
from io import StringIO
import sys
import time
from contextlib import redirect_stderr


def square(x):
    return x**2


class TestLogMap(unittest.TestCase):
    def setUp(self):
        self.logmap = LogMap()
        self.output = StringIO()
        self.handler_id = logger.add(self.output, format="{message}", level="TRACE")

    def tearDown(self):
        logger.remove(self.handler_id)

    def test_context_manager(self):
        with self.logmap("Test operation"):
            pass
        output = self.output.getvalue()
        self.assertIn("⎾ Test operation", output)
        self.assertIn("⎿ ✔️", output)
        self.assertIn("seconds", output)

    def test_nested_context_manager(self):
        with self.logmap("Outer operation"):
            with self.logmap("Inner operation"):
                pass
        output = self.output.getvalue()
        self.assertIn("⎾ Outer operation", output)
        self.assertIn("￨ ⎾ Inner operation", output)
        self.assertIn("￨ ⎿ ✔️", output)
        self.assertIn("⎿ ✔️", output)

    def test_iteration(self):
        result = list(self.logmap(range(5)))
        self.assertEqual(result, [0, 1, 2, 3, 4])

    def test_iter_progress(self):
        with redirect_stderr(StringIO()) as f:
            list(self.logmap.iter_progress(range(5), desc="Test progress"))
        output = f.getvalue()
        self.assertIn("Test progress", output)
        self.assertIn("5/5", output)

    def test_quiet_mode(self):
        with self.logmap.quiet():
            with self.logmap("Silent operation"):
                self.logmap.log("This should not be printed")
        self.assertEqual(self.output.getvalue(), "")

    def test_imap(self):
        result = list(self.logmap.imap(square, range(5)))
        self.assertEqual(result, [0, 1, 4, 9, 16])

    def test_map(self):
        result = self.logmap.map(square, range(5))
        self.assertEqual(result, [0, 1, 4, 9, 16])

    def test_nap(self):
        start_time = time.time()
        duration = self.logmap.nap(0.1)
        end_time = time.time()
        self.assertLess(duration, 0.1)
        self.assertLess(end_time - start_time, 0.2)
        self.assertIn("Napped for", self.output.getvalue())

    def test_log_levels(self):
        self.logmap.debug("Debug message")
        self.logmap.info("Info message")
        self.logmap.warning("Warning message")
        self.logmap.error("Error message")
        self.logmap.trace("Trace message")
        output = self.output.getvalue()
        self.assertIn("Debug message", output)
        self.assertIn("Info message", output)
        self.assertIn("Warning message", output)
        self.assertIn("Error message", output)
        self.assertIn("Trace message", output)

    def test_combined_usage(self):
        with self.logmap("Outer"):
            with self.logmap("Inner"):
                result = list(self.logmap(range(3)))
        self.assertEqual(result, [0, 1, 2])
        output = self.output.getvalue()
        self.assertIn("⎾ Outer", output)
        self.assertIn("￨ ⎾ Inner", output)
        self.assertIn("￨ ⎿ ✔️", output)
        self.assertIn("⎿ ✔️", output)


if __name__ == "__main__":
    unittest.main()
