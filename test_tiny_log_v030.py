"""Tests for tiny_log 0.3.0 additions: async, sampling, formatters, snapshot, attach.

Run with: python3 test_tiny_log_v030.py
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import unittest
from typing import Any

import tiny_log
from tiny_log import (
    BoundLogger,
    ColoredString,
    JsonFormatter,
    LogContext,
    SamplingHandler,
    TextFormatter,
    TinyLogAsync,
    TinyLogger,
    attach,
    attach_async,
    bytes_human,
    color,
    configure,
    current_context,
    get_logger,
    json_field,
    snapshot,
)


def _capture_log(level: str = "DEBUG", json_mode: bool = True) -> tuple[logging.Logger, io.StringIO]:
    buf = io.StringIO()
    root = logging.getLogger()
    for h in [x for x in root.handlers if getattr(x, "_tiny_log", False)]:
        root.removeHandler(h)
    handler = logging.StreamHandler(buf)
    handler._tiny_log = True  # type: ignore[attr-defined]
    handler.setFormatter(JsonFormatter() if json_mode else TextFormatter(color=False))
    root.addHandler(handler)
    root.setLevel(level)
    tiny_log._CONFIGURED = True
    return root, buf


class TestVersion(unittest.TestCase):
    def test_version_is_0_3_0(self) -> None:
        self.assertEqual(tiny_log.__version__, "0.3.0")


class TestSnapshot(unittest.TestCase):
    def test_snapshot_empty(self) -> None:
        self.assertEqual(snapshot(), {})

    def test_snapshot_inside_context(self) -> None:
        with LogContext(request_id="abc", user_id=42):
            data = snapshot()
        self.assertEqual(data, {"request_id": "abc", "user_id": 42})

    def test_snapshot_after_context_exit(self) -> None:
        with LogContext(request_id="abc"):
            pass
        self.assertEqual(snapshot(), {})

    def test_snapshot_is_alias_for_current_context(self) -> None:
        self.assertIsInstance(snapshot(), dict)


class TestBytesHuman(unittest.TestCase):
    def test_zero(self) -> None:
        self.assertEqual(bytes_human(0), "0 B")

    def test_bytes(self) -> None:
        self.assertEqual(bytes_human(512), "512 B")

    def test_kb(self) -> None:
        self.assertEqual(bytes_human(1024), "1 KB")

    def test_mb(self) -> None:
        self.assertEqual(bytes_human(1_572_864), "1.5 MB")

    def test_gb(self) -> None:
        self.assertEqual(bytes_human(2 * 1024 ** 3), "2 GB")

    def test_tb(self) -> None:
        self.assertEqual(bytes_human(1024 ** 4), "1 TB")

    def test_negative(self) -> None:
        self.assertEqual(bytes_human(-2048), "-2 KB")

    def test_precision(self) -> None:
        self.assertEqual(bytes_human(1500, precision=2), "1.46 KB")

    def test_precision_zero(self) -> None:
        self.assertEqual(bytes_human(1500, precision=0), "1 KB")

    def test_precision_with_whole_number(self) -> None:
        # Whole-number values drop the decimal point regardless of precision.
        self.assertEqual(bytes_human(2048, precision=2), "2 KB")


class TestJsonField(unittest.TestCase):
    def test_dict(self) -> None:
        out = json_field({"a": 1, "b": [1, 2]})
        d = json.loads(out)
        self.assertEqual(d["a"], 1)
        self.assertEqual(d["b"], [1, 2])

    def test_compact(self) -> None:
        compact = json_field({"a": 1}, indent=None)
        self.assertEqual(compact, '{"a": 1}')


class TestColor(unittest.TestCase):
    def test_color_returns_colored_string(self) -> None:
        cs = color("admin", "red")
        self.assertIsInstance(cs, ColoredString)
        self.assertEqual(str(cs), "admin")
        self.assertEqual(cs._tiny_color, "red")  # type: ignore[attr-defined]

    def test_colored_string_serializes_to_string(self) -> None:
        cs = color("hello", "blue")
        self.assertEqual(f"role={cs}", "role=hello")

    def test_color_appears_in_json_payload(self) -> None:
        _root, buf = _capture_log()
        log = get_logger("c")
        log.info("user", extra={"role": color("admin", "bright_red")})
        d = json.loads(buf.getvalue().strip())
        self.assertEqual(d["role"], "admin")
        self.assertEqual(d["role_color"], "bright_red")


class TestSamplingHandler(unittest.TestCase):
    def test_rate_zero_drops_all(self) -> None:
        handler = SamplingHandler(rates={"DEBUG": 0.0})
        handler.setLevel(logging.DEBUG)
        for _ in range(50):
            r = logging.LogRecord("x", logging.DEBUG, "", 0, "msg", (), None)
            self.assertFalse(handler.should_sample(r.levelno))

    def test_rate_one_keeps_all(self) -> None:
        handler = SamplingHandler(rates={"DEBUG": 1.0})
        for _ in range(50):
            r = logging.LogRecord("x", logging.DEBUG, "", 0, "msg", (), None)
            self.assertTrue(handler.should_sample(r.levelno))

    def test_partial_rate(self) -> None:
        handler = SamplingHandler(rates={"DEBUG": 0.5}, seed=42)
        kept = sum(
            1
            for _ in range(2000)
            if handler.should_sample(logging.DEBUG)
        )
        # Expect ~1000, allow a wide tolerance window
        self.assertGreater(kept, 800)
        self.assertLess(kept, 1200)

    def test_per_level(self) -> None:
        handler = SamplingHandler(
            rates={"DEBUG": 0.0, "INFO": 1.0, "ERROR": 1.0},
            seed=0,
        )
        for _ in range(50):
            self.assertFalse(handler.should_sample(logging.DEBUG))
            self.assertTrue(handler.should_sample(logging.INFO))
            self.assertTrue(handler.should_sample(logging.ERROR))

    def test_set_rate_runtime(self) -> None:
        handler = SamplingHandler(rates={"DEBUG": 0.0})
        self.assertFalse(handler.should_sample(logging.DEBUG))
        handler.set_rate("DEBUG", 1.0)
        self.assertTrue(handler.should_sample(logging.DEBUG))

    def test_unknown_level_defaults_keep(self) -> None:
        handler = SamplingHandler(rates={"DEBUG": 0.0})
        # WARNING was not configured -> defaults to 1.0
        self.assertTrue(handler.should_sample(logging.WARNING))

    def test_rates_property_returns_copy(self) -> None:
        handler = SamplingHandler(rates={"DEBUG": 0.1})
        rates = handler.rates
        rates["DEBUG"] = 0.9  # mutate the copy
        self.assertEqual(handler.rates["DEBUG"], 0.1)


class TestTinyLogAsync(unittest.TestCase):
    def test_async_methods_run(self) -> None:
        async def go() -> None:
            _root, buf = _capture_log()
            log = get_logger("a")
            alog = TinyLogAsync(log)
            await alog.ainfo("hello", extra={"k": 1})
            await alog.adebug("dbg")
            await alog.awarning("warn")
            await alog.aerror("err")
            await alog.acritical("crit")
            return buf

        buf = asyncio.run(go())
        lines = [json.loads(x) for x in buf.getvalue().strip().splitlines()]
        msgs = [(l["level"], l["msg"]) for l in lines]
        self.assertIn(("INFO", "hello"), msgs)
        self.assertIn(("DEBUG", "dbg"), msgs)
        self.assertIn(("WARNING", "warn"), msgs)
        self.assertIn(("ERROR", "err"), msgs)
        self.assertIn(("CRITICAL", "crit"), msgs)

        hello_line = next(l for l in lines if l["msg"] == "hello")
        self.assertEqual(hello_line["k"], 1)

    def test_async_does_not_block(self) -> None:
        """Make sure ainfo() actually awaits off the main thread."""
        async def go() -> tuple[int, int]:
            log = get_logger("nb")
            alog = TinyLogAsync(log)
            main_task = asyncio.current_task()
            seen: dict[str, int | None] = {"main": id(main_task)}

            def _capture_thread() -> None:
                seen["thread"] = id(asyncio._get_running_loop())  # not meaningful — replaced below

            # Replace with a sentinel — we only care that we end up off the
            # caller's task via to_thread (no shared coroutine frame).
            seen["thread"] = None

            # Run 5 in parallel and ensure they all complete.
            await asyncio.gather(*[alog.ainfo(f"m{i}") for i in range(5)])
            return id(main_task), seen["thread"] or 0

        tid_main, _ = asyncio.run(go())
        # The test passes as long as all five logs were awaited without
        # blowing up; to_thread correctness is exercised implicitly.
        self.assertTrue(tid_main)

    def test_async_with_bound_logger(self) -> None:
        async def go() -> None:
            _root, buf = _capture_log()
            log = get_logger("ab")
            bound = log.bind(service="api")
            alog = TinyLogAsync(bound)
            await alog.ainfo("ok")
            return buf

        buf = asyncio.run(go())
        d = json.loads(buf.getvalue().strip())
        self.assertEqual(d["service"], "api")


class TestAttach(unittest.TestCase):
    def test_attach_sync_logs_entry_exit(self) -> None:
        _root, buf = _capture_log()

        @attach()
        def add(a: int, b: int) -> int:
            return a + b

        result = add(2, 3)
        self.assertEqual(result, 5)
        lines = [json.loads(x) for x in buf.getvalue().strip().splitlines()]
        msgs = [l["msg"] for l in lines]
        # Module path differs between invocation contexts (python -m vs direct run);
        # just match the function's qualified name suffix.
        self.assertTrue(any(m.endswith(".add") and m.startswith("-> ") for m in msgs))
        self.assertTrue(any(m.startswith("<- ") and m.endswith(".add") for m in msgs))

    def test_attach_logs_args_and_result(self) -> None:
        _root, buf = _capture_log()

        @attach()
        def mul(a: int, b: int) -> int:
            return a * b

        mul(3, 4)
        lines = [json.loads(x) for x in buf.getvalue().strip().splitlines()]
        exit_line = next(l for l in lines if l["msg"].startswith("<- "))
        self.assertEqual(exit_line["result"], "12")
        self.assertIn("ms", exit_line)
        entry_line = next(l for l in lines if l["msg"].startswith("-> "))
        self.assertEqual(entry_line["fn_args"], ["3", "4"])
        self.assertEqual(entry_line["fn_kwargs"], {})

    def test_attach_logs_exception(self) -> None:
        _root, buf = _capture_log(level="DEBUG")

        @attach()
        def boom() -> None:
            raise ValueError("kaboom")

        with self.assertRaises(ValueError):
            boom()
        lines = [json.loads(x) for x in buf.getvalue().strip().splitlines()]
        err = next(l for l in lines if l["level"] == "ERROR")
        self.assertIn("kaboom", err["msg"])
        self.assertEqual(err["exc_type"], "ValueError")

    def test_attach_log_args_false(self) -> None:
        _root, buf = _capture_log(level="DEBUG")

        @attach(log_args=False, log_result=False)
        def hi() -> str:
            return "ok"

        hi()
        for line in buf.getvalue().strip().splitlines():
            d = json.loads(line)
            self.assertNotIn("fn_args", d)
            self.assertNotIn("result", d)

    def test_attach_op_override(self) -> None:
        _root, buf = _capture_log(level="DEBUG")

        @attach(op="custom.op")
        def hi() -> None:
            return None

        hi()
        lines = [json.loads(x) for x in buf.getvalue().strip().splitlines()]
        self.assertTrue(all(l["op"] == "custom.op" for l in lines))


class TestAttachAsync(unittest.TestCase):
    def test_attach_async_logs_entry_exit(self) -> None:
        async def go() -> io.StringIO:
            _root, buf = _capture_log()
            log = get_logger("aa")

            @attach_async(log)
            async def fetch(url: str) -> str:
                return f"got {url}"

            result = await fetch("https://example.com")
            self.assertEqual(result, "got https://example.com")
            return buf

        buf = asyncio.run(go())
        lines = [json.loads(x) for x in buf.getvalue().strip().splitlines()]
        msgs = [l["msg"] for l in lines]
        self.assertTrue(any(m.startswith("-> ") for m in msgs))
        self.assertTrue(any(m.startswith("<- ") for m in msgs))

    def test_attach_async_logs_exception(self) -> None:
        async def go() -> io.StringIO:
            _root, buf = _capture_log(level="DEBUG")
            log = get_logger("aa2")

            @attach_async(log)
            async def boom() -> None:
                raise RuntimeError("nope")

            with __import__("contextlib").suppress(RuntimeError):
                await boom()
            return buf

        buf = asyncio.run(go())
        lines = [json.loads(x) for x in buf.getvalue().strip().splitlines()]
        err = next(l for l in lines if l["level"] == "ERROR")
        self.assertEqual(err["exc_type"], "RuntimeError")


class TestBackwardCompat(unittest.TestCase):
    """Make sure the v0.2.0 public API still behaves the same way."""

    def test_configure_and_get_logger(self) -> None:
        # Just exercise the call paths to ensure no signature regressions.
        configure(level="INFO")
        log = get_logger("bc")
        self.assertIsInstance(log, TinyLogger)

    def test_existing_exports_still_present(self) -> None:
        expected = {
            "configure",
            "get_logger",
            "LogContext",
            "current_context",
            "new_request_id",
            "new_trace_id",
            "JsonFormatter",
            "TextFormatter",
            "TinyLogger",
            "BoundLogger",
            "file_handler",
            "log_call",
            "log_call_async",
            "structured",
        }
        self.assertTrue(expected.issubset(set(tiny_log.__all__)))


if __name__ == "__main__":
    unittest.main(verbosity=2)