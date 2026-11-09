"""v0.8.0 — one log call, one set of fields, whichever format you pick.

The JSON formatter emitted every ``extra=`` key. The text formatter emitted
three hardcoded ones (``request_id``, ``user_id``, ``trace_id``) and dropped
the rest on the floor. So:

    log.info("request", extra={"latency_ms": 42})

carried ``latency_ms`` in production JSON logs and lost it on a developer's
terminal. The failure is silent in both directions: nothing raises, and the
field is simply absent from the line you were staring at while debugging.

These tests fail against 0.7.0.
"""

import io
import json
import unittest

import tiny_log
from tiny_log import TextFormatter, configure, get_logger
from tiny_log import logging as _logging


def _record(msg="hello", name="myapp", **extras):
    rec = _logging.LogRecord(name, _logging.INFO, "f.py", 1, msg, (), None)
    for k, v in extras.items():
        setattr(rec, k, v)
    return rec


class TestTextFormatterKeepsExtras(unittest.TestCase):
    def test_arbitrary_extra_is_rendered(self):
        line = TextFormatter(color=False).format(_record(latency_ms=42))
        self.assertIn("latency_ms=42", line)

    def test_string_extra_is_unquoted(self):
        line = TextFormatter(color=False).format(_record(user="alice"))
        self.assertIn("user=alice", line)
        self.assertNotIn("user='alice'", line)

    def test_none_is_distinguishable_from_the_string_none(self):
        line = TextFormatter(color=False).format(_record(cache_hit=None))
        self.assertIn("cache_hit=None", line)

    def test_bool_extra(self):
        line = TextFormatter(color=False).format(_record(hit=True, miss=False))
        self.assertIn("hit=True", line)
        self.assertIn("miss=False", line)

    def test_legacy_inline_keys_still_inline(self):
        line = TextFormatter(color=False).format(_record(request_id="abc"))
        self.assertIn("request_id=abc", line)

    def test_extras_can_be_turned_off(self):
        line = TextFormatter(color=False, extras=False).format(_record(latency_ms=42))
        self.assertNotIn("latency_ms", line)
        self.assertIn("hello", line)


class TestFormatsAgree(unittest.TestCase):
    def test_same_fields_in_json_and_text(self):
        extras = {"latency_ms": 42, "cache_hit": True, "route": "/users/{id}"}
        rec = _record(**extras)

        payload = json.loads(tiny_log.JsonFormatter().format(rec))
        text = TextFormatter(color=False).format(rec)

        for key, value in extras.items():
            self.assertEqual(payload[key], value, f"{key} missing from JSON")
            self.assertIn(f"{key}={value if isinstance(value, str) else repr(value)}", text,
                          f"{key} missing from text")

    def test_end_to_end_through_configure(self):
        buf = io.StringIO()
        configure(level="INFO", json=False, stream=buf, color=False)
        get_logger("myapp").info("login", extra={"user": "alice", "latency_ms": 7})
        out = buf.getvalue()
        self.assertIn("user=alice", out)
        self.assertIn("latency_ms=7", out)

    def test_end_to_end_json_still_has_them(self):
        buf = io.StringIO()
        configure(level="INFO", json=True, stream=buf)
        get_logger("myapp").info("login", extra={"user": "alice", "latency_ms": 7})
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["user"], "alice")
        self.assertEqual(payload["latency_ms"], 7)


class TestNoLeak(unittest.TestCase):
    def test_stdlib_fields_are_not_rendered_as_extras(self):
        line = TextFormatter(color=False).format(_record())
        for internal in ("pathname=", "levelno=", "threadName=", "relativeCreated="):
            self.assertNotIn(internal, line)

    def test_private_attributes_are_skipped(self):
        line = TextFormatter(color=False).format(_record(_internal="x"))
        self.assertNotIn("_internal", line)


if __name__ == "__main__":
    unittest.main()
