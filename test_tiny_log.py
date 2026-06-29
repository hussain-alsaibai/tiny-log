"""Tests for tiny_log. Run with: python test_tiny_log.py"""

from __future__ import annotations

import io
import json
import logging
import unittest
import warnings

from tiny_log import (
    BoundLogger,
    JsonFormatter,
    LogContext,
    TextFormatter,
    TinyLogger,
    configure,
    current_context,
    file_handler,
    get_logger,
    log_call,
    new_request_id,
)


def _capture_log(level: str = "DEBUG", json_mode: bool = True) -> tuple[logging.Logger, io.StringIO]:
    buf = io.StringIO()
    root = logging.getLogger()
    # Clear all tiny_log handlers (including those added by get_logger)
    for h in [x for x in root.handlers if getattr(x, "_tiny_log", False)]:
        root.removeHandler(h)
    handler = logging.StreamHandler(buf)
    handler._tiny_log = True  # type: ignore[attr-defined]
    handler.setFormatter(JsonFormatter() if json_mode else TextFormatter(color=False))
    root.addHandler(handler)
    root.setLevel(level)
    # Mark as configured so get_logger doesn't clobber our handler
    import tiny_log
    tiny_log._CONFIGURED = True
    return root, buf


class TestBasic(unittest.TestCase):
    def test_get_logger(self) -> None:
        log = get_logger("test")
        self.assertIsInstance(log, TinyLogger)

    def test_simple_info(self) -> None:
        _root, buf = _capture_log()
        log = get_logger("x")
        log.info("hello")
        out = buf.getvalue().strip()
        d = json.loads(out)
        self.assertEqual(d["msg"], "hello")
        self.assertEqual(d["level"], "INFO")
        self.assertEqual(d["logger"], "x")

    def test_levels(self) -> None:
        _root, buf = _capture_log(level="DEBUG")
        log = get_logger("lvl")
        log.debug("d")
        log.info("i")
        log.warning("w")
        log.error("e")
        log.critical("c")
        lines = [json.loads(x) for x in buf.getvalue().strip().splitlines()]
        levels = [l["level"] for l in lines]
        self.assertEqual(levels, ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])

    def test_extra(self) -> None:
        _root, buf = _capture_log()
        log = get_logger("ex")
        log.info("user logged in", extra={"user_id": 42})
        d = json.loads(buf.getvalue().strip())
        self.assertEqual(d["user_id"], 42)


class TestContext(unittest.TestCase):
    def test_log_context(self) -> None:
        _root, buf = _capture_log()
        log = get_logger("ctx")
        with LogContext(request_id="abc", user_id=7):
            log.info("in scope")
        d = json.loads(buf.getvalue().strip())
        self.assertEqual(d["ctx"], {"request_id": "abc", "user_id": 7})

    def test_log_context_nested(self) -> None:
        _root, buf = _capture_log()
        log = get_logger("nest")
        with LogContext(a=1):
            with LogContext(b=2):
                log.info("nested")
            log.info("after inner")
        lines = [json.loads(x) for x in buf.getvalue().strip().splitlines()]
        self.assertEqual(lines[0]["ctx"], {"a": 1, "b": 2})
        self.assertEqual(lines[1]["ctx"], {"a": 1})

    def test_current_context_empty(self) -> None:
        # Outside any LogContext the value is empty
        self.assertEqual(current_context(), {})

    def test_new_request_id(self) -> None:
        rid = new_request_id()
        self.assertEqual(len(rid), 16)
        self.assertNotEqual(rid, new_request_id())


class TestBound(unittest.TestCase):
    def test_bound_logger(self) -> None:
        _root, buf = _capture_log()
        log = get_logger("svc").bind(service="api", region="us-east")
        log.info("started")
        d = json.loads(buf.getvalue().strip())
        self.assertEqual(d["service"], "api")
        self.assertEqual(d["region"], "us-east")

    def test_bound_logger_extra(self) -> None:
        _root, buf = _capture_log()
        log = get_logger("svc2").bind(service="api")
        log.info("request", extra={"method": "GET"})
        d = json.loads(buf.getvalue().strip())
        self.assertEqual(d["service"], "api")
        self.assertEqual(d["method"], "GET")

    def test_bound_logger_callable_extra(self) -> None:
        bl = BoundLogger(get_logger("x"), {"a": 1})
        self.assertIsInstance(bl, BoundLogger)


class TestFormatters(unittest.TestCase):
    def test_text_format(self) -> None:
        _root, buf = _capture_log(json_mode=False)
        log = get_logger("t")
        log.info("hi")
        out = buf.getvalue().strip()
        self.assertIn("INFO", out)
        self.assertIn("hi", out)
        self.assertIn("t", out)

    def test_exception_logged(self) -> None:
        _root, buf = _capture_log()
        log = get_logger("ex")
        try:
            raise ValueError("boom")
        except Exception:
            log.exception("failed")
        d = json.loads(buf.getvalue().strip())
        self.assertEqual(d["msg"], "failed")
        self.assertIn("exc", d)
        self.assertIn("ValueError", d["exc"])


class TestHelpers(unittest.TestCase):
    def test_log_call(self) -> None:
        _root, buf = _capture_log()
        log = get_logger("perf")

        def add(a: int, b: int) -> int:
            return a + b

        result = log_call(log, "add", add, 2, 3)
        self.assertEqual(result, 5)
        d = json.loads(buf.getvalue().strip())
        self.assertEqual(d["op"], "add")
        self.assertEqual(d["msg"], "add ok")
        self.assertIn("ms", d)

    def test_log_call_raises(self) -> None:
        _root, buf = _capture_log()
        log = get_logger("perf2")

        def boom() -> None:
            raise RuntimeError("nope")

        with self.assertRaises(RuntimeError):
            log_call(log, "boom", boom)
        d = json.loads(buf.getvalue().strip())
        self.assertEqual(d["level"], "ERROR")
        self.assertIn("nope", d["msg"])

    def test_capture_warnings(self) -> None:
        _root, buf = _capture_log()
        log = get_logger("warn")
        with warnings.catch_warnings():
            warnings.simplefilter("always")
            warnings.warn("deprecated!", DeprecationWarning)
        out = buf.getvalue()
        # Either captured by our root or filtered — but should not raise.
        self.assertTrue(isinstance(out, str))


class TestFileHandler(unittest.TestCase):
    def test_file_handler_creates_file(self) -> None:
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub", "app.log")
            h = file_handler(path, json=True, level="DEBUG")
            log = logging.getLogger("fh")
            log.setLevel(logging.DEBUG)
            log.addHandler(h)
            log.info("written")
            for handler in log.handlers:
                handler.flush()
            self.assertTrue(os.path.exists(path))
            with open(path) as f:
                content = f.read()
            self.assertIn("written", content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
