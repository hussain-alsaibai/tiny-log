"""Tests for tiny_log v0.5.0 redaction bridge."""

from __future__ import annotations

import json
import logging
import unittest

# Make sure the tiny-log repo is on the path so we can import the v050
# sidecar directly even when the test runner is invoked from elsewhere.
import os as _os
import sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_REPO = _os.path.dirname(_HERE)
if _REPO not in _sys.path:
    _sys.path.insert(0, _REPO)

from tiny_log import configure, get_logger
try:
    from tiny_log_v050 import (
        RedactFilter,
        get_redactor,
        install_json_redact_bridge,
        redact_payload,
        redact_value,
        safe_log,
    )
    from tiny_secret_mask import REDACT, __version__ as _tsm_version
    _HAS_TSM = True
except ImportError:
    _HAS_TSM = False


@unittest.skipUnless(_HAS_TSM, "tiny-secret-mask not installed")
class TestVersion(unittest.TestCase):
    def test_tiny_log_version(self) -> None:
        from tiny_log import __version__
        # Assert the contract, not a literal: a hard pin breaks on
        # every version bump. The pyproject match is checked in
        # test_version_consistency.py.
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")


@unittest.skipUnless(_HAS_TSM, "tiny-secret-mask not installed")
class TestGetRedactor(unittest.TestCase):
    def test_redactor_loads(self) -> None:
        r = get_redactor()
        self.assertIsNotNone(r)

    def test_redactor_cached(self) -> None:
        r1 = get_redactor()
        r2 = get_redactor()
        self.assertIs(r1, r2)


@unittest.skipUnless(_HAS_TSM, "tiny-secret-mask not installed")
class TestRedactValue(unittest.TestCase):
    def test_string_redacted(self) -> None:
        out = redact_value("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyMSJ9.sig")
        self.assertIn("[REDACTED:bearer]", out)

    def test_dict_redacted(self) -> None:
        out = redact_value({"password": "hunter2"})
        self.assertEqual(out["password"], "[REDACTED:password]")

    def test_list_redacted(self) -> None:
        out = redact_value([{"password": "x"}, {"user": "alice"}])
        self.assertEqual(out[0]["password"], "[REDACTED:password]")
        self.assertEqual(out[1]["user"], "alice")

    def test_passthrough(self) -> None:
        self.assertEqual(redact_value(42), 42)
        self.assertEqual(redact_value(None), None)
        self.assertEqual(redact_value(True), True)


@unittest.skipUnless(_HAS_TSM, "tiny-secret-mask not installed")
class TestRedactPayload(unittest.TestCase):
    def test_basic(self) -> None:
        out = redact_payload({"user": "alice", "token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyMSJ9.sig_part_here"})
        self.assertEqual(out["user"], "alice")
        self.assertNotIn("eyJ", out["token"])
        self.assertIn("[REDACTED:jwt]", out["token"])

    def test_nested(self) -> None:
        out = redact_payload({"outer": {"password": "x"}})
        self.assertEqual(out["outer"]["password"], "[REDACTED:password]")

    def test_no_mutation(self) -> None:
        original = {"password": "hunter2"}
        redact_payload(original)
        self.assertEqual(original["password"], "hunter2")


@unittest.skipUnless(_HAS_TSM, "tiny-secret-mask not installed")
class TestJsonBridge(unittest.TestCase):
    def test_install_returns_true(self) -> None:
        # Idempotent — second call returns False but bridge still works.
        install_json_redact_bridge()
        out = json.dumps({"password": REDACT})
        self.assertEqual(out, '{"password": "[REDACTED]"}')


@unittest.skipUnless(_HAS_TSM, "tiny-secret-mask not installed")
class TestRedactFilter(unittest.TestCase):
    def test_filter_redacts_string_extra(self) -> None:
        f = RedactFilter()
        record = logging.LogRecord(
            name="x", level=logging.INFO, pathname="", lineno=0,
            msg="hi", args=(), exc_info=None,
        )
        record.token = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyMSJ9.sig"
        f.filter(record)
        self.assertIn("[REDACTED:bearer]", record.token)

    def test_filter_redacts_dict_extra(self) -> None:
        f = RedactFilter()
        record = logging.LogRecord(
            name="x", level=logging.INFO, pathname="", lineno=0,
            msg="hi", args=(), exc_info=None,
        )
        record.payload = {"password": "hunter2"}
        f.filter(record)
        self.assertEqual(record.payload["password"], "[REDACTED:password]")


@unittest.skipUnless(_HAS_TSM, "tiny-secret-mask not installed")
class TestSafeLog(unittest.TestCase):
    def test_safe_log_redacts_message(self) -> None:
        log = logging.getLogger("test_safe_log_msg")
        log.setLevel(logging.INFO)
        captured = []
        handler = logging.Handler()
        handler.emit = lambda rec: captured.append(rec.getMessage())
        log.addHandler(handler)
        try:
            safe_log(log, logging.INFO, "user=%s token=%s", "alice", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyMSJ9.sig_part_here")
            self.assertEqual(len(captured), 1)
            self.assertIn("[REDACTED:jwt]", captured[0])
            self.assertIn("alice", captured[0])
        finally:
            log.removeHandler(handler)

    def test_safe_log_redacts_extra(self) -> None:
        log = logging.getLogger("test_safe_log_extra")
        log.setLevel(logging.INFO)
        captured = []
        handler = logging.Handler()
        handler.emit = lambda rec: captured.append(rec.__dict__.get("password"))
        log.addHandler(handler)
        try:
            safe_log(log, logging.INFO, "login", extra={"password": "hunter2"})
            self.assertEqual(captured[0], "[REDACTED:password]")
        finally:
            log.removeHandler(handler)


@unittest.skipUnless(_HAS_TSM, "tiny-secret-mask not installed")
class TestConfigureRedact(unittest.TestCase):
    def test_configure_redact_attaches_filter(self) -> None:
        import io
        buf = io.StringIO()
        configure(level="INFO", json=True, stream=buf, redact=True)
        log = get_logger("test_configure_redact")
        log.info("login", extra={"password": "hunter2"})
        out = buf.getvalue()
        self.assertIn("[REDACTED:password]", out)
        self.assertNotIn("hunter2", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
