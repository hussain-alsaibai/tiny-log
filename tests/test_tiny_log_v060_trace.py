"""W3C trace context: TraceContext, parse_traceparent, inject_traceparent.

These were ported from a stale clone on 2026-11-02. The clone version had a
trace-continuity bug: as_headers() built the header by calling
inject_traceparent() with no trace_id, which minted a fresh trace id. So
every outgoing request landed in a different trace than the log records it was
issued alongside -- the exact opposite of what the class promises.

The first test below is the regression guard for that bug.
"""

from __future__ import annotations

import unittest

import tiny_log
from tiny_log import (
    TraceContext,
    inject_traceparent,
    new_span_id,
    new_w3c_trace_id,
    parse_traceparent,
)


class TestTraceparentParsing(unittest.TestCase):
    def test_parses_valid_traceparent(self):
        value = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        parsed = parse_traceparent(value)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["trace_id"], "4bf92f3577b34da6a3ce929d0e0e4736")
        self.assertEqual(parsed["span_id"], "00f067aa0ba902b7")
        self.assertEqual(parsed["flags"], "01")

    def test_rejects_malformed(self):
        for bad in (
            "garbage",
            "00-tooshort-00f067aa0ba902b7-01",
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7",
            "01-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01-extra",
        ):
            self.assertIsNone(parse_traceparent(bad), "accepted " + bad)

    def test_rejects_all_zero_ids(self):
        # W3C: an all-zero trace-id or parent-id is invalid.
        self.assertIsNone(
            parse_traceparent("00-" + "0" * 32 + "-00f067aa0ba902b7-01")
        )
        self.assertIsNone(
            parse_traceparent("00-4bf92f3577b34da6a3ce929d0e0e4736-" + "0" * 16 + "-01")
        )

    def test_none_and_empty(self):
        self.assertIsNone(parse_traceparent(None))
        self.assertIsNone(parse_traceparent(""))


class TestInject(unittest.TestCase):
    def test_shape_is_w3c(self):
        headers = inject_traceparent()
        self.assertIn("traceparent", headers)
        parts = headers["traceparent"].split("-")
        self.assertEqual(len(parts), 4)
        self.assertEqual(parts[0], "00")
        self.assertEqual(len(parts[1]), 32)
        self.assertEqual(len(parts[2]), 16)
        self.assertEqual(len(parts[3]), 2)

    def test_explicit_trace_id_is_used(self):
        tid = "4bf92f3577b34da6a3ce929d0e0e4736"
        headers = inject_traceparent(trace_id=tid)
        self.assertEqual(headers["traceparent"].split("-")[1], tid)

    def test_tracestate_included_when_valid(self):
        headers = inject_traceparent(tracestate="vendor=1")
        self.assertEqual(headers["tracestate"], "vendor=1")

    def test_tracestate_rejected_when_too_long_or_has_newline(self):
        self.assertNotIn("tracestate", inject_traceparent(tracestate="x" * 513))
        self.assertNotIn("tracestate", inject_traceparent(tracestate="a\nb"))


class TestTraceContext(unittest.TestCase):
    def test_as_headers_keeps_the_same_trace(self):
        """REGRESSION: as_headers() must not mint a new trace id."""
        tc = TraceContext()
        headers = tc.as_headers()
        header_trace_id = headers["traceparent"].split("-")[1]
        self.assertEqual(
            tc.trace_id,
            header_trace_id,
            "as_headers() started a new trace instead of continuing this one",
        )

    def test_as_headers_is_parseable_and_round_trips(self):
        tc = TraceContext()
        parsed = parse_traceparent(tc.as_headers()["traceparent"])
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["trace_id"], tc.trace_id)
        self.assertEqual(parsed["span_id"], tc.span_id)

    def test_new_trace_ids_differ_between_contexts(self):
        self.assertNotEqual(TraceContext().trace_id, TraceContext().trace_id)

    def test_from_headers_continues_the_incoming_trace(self):
        incoming = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        tc = TraceContext.from_headers({"traceparent": incoming})
        self.assertEqual(tc.trace_id, "4bf92f3577b34da6a3ce929d0e0e4736")
        self.assertEqual(tc.parent_span_id, "00f067aa0ba902b7")
        # The outgoing span is new, but the trace is the same one.
        self.assertNotEqual(tc.span_id, "00f067aa0ba902b7")
        self.assertEqual(
            tc.as_headers()["traceparent"].split("-")[1],
            "4bf92f3577b34da6a3ce929d0e0e4736",
        )

    def test_from_headers_accepts_explicit_traceparent(self):
        tc = TraceContext.from_headers(
            traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        )
        self.assertEqual(tc.trace_id, "4bf92f3577b34da6a3ce929d0e0e4736")

    def test_from_headers_with_garbage_mints_a_fresh_trace(self):
        tc = TraceContext.from_headers({"traceparent": "not-a-traceparent"})
        self.assertEqual(len(tc.trace_id), 32)
        self.assertIsNone(tc.parent_span_id)

    def test_from_headers_is_case_insensitive_on_the_key(self):
        tc = TraceContext.from_headers(
            {"Traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"}
        )
        self.assertEqual(tc.trace_id, "4bf92f3577b34da6a3ce929d0e0e4736")

    def test_context_binds_trace_values_to_log_scope(self):
        with TraceContext() as tc:
            ctx = tiny_log.current_context()
            self.assertEqual(ctx["trace_id"], tc.trace_id)
            self.assertEqual(ctx["span_id"], tc.span_id)

    def test_context_is_restored_on_exit(self):
        before = tiny_log.current_context()
        with TraceContext():
            pass
        self.assertEqual(tiny_log.current_context(), before)


class TestIdShapes(unittest.TestCase):
    def test_span_id_is_16_hex(self):
        sid = new_span_id()
        self.assertEqual(len(sid), 16)
        int(sid, 16)

    def test_w3c_trace_id_is_32_hex(self):
        tid = new_w3c_trace_id()
        self.assertEqual(len(tid), 32)
        int(tid, 16)

    def test_display_trace_id_stays_24(self):
        # new_trace_id() is the OpenTelemetry display form and is unchanged.
        self.assertEqual(len(tiny_log.new_trace_id()), 24)


if __name__ == "__main__":
    unittest.main()


class TestChildSpans(unittest.TestCase):
    """A same-level block reports the root as its parent, which makes siblings
    indistinguishable from a chain. child() is the fix; these pin it."""

    def test_child_keeps_trace_and_reparents(self):
        root = TraceContext()
        child = root.child()
        self.assertEqual(child.trace_id, root.trace_id)
        self.assertEqual(child.parent_span_id, root.span_id)
        self.assertNotEqual(child.span_id, root.span_id)

    def test_child_preserves_sampling_flags(self):
        root = TraceContext(flags="00")  # not sampled upstream
        self.assertEqual(root.child().flags, "00")

    def test_child_preserves_tracestate(self):
        root = TraceContext(tracestate="vendor=1")
        self.assertEqual(root.child().tracestate, "vendor=1")

    def test_two_children_are_siblings(self):
        root = TraceContext()
        a, b = root.child(), root.child()
        self.assertEqual(a.parent_span_id, b.parent_span_id)
        self.assertNotEqual(a.span_id, b.span_id)

    def test_grandchild_chains(self):
        root = TraceContext()
        leaf = root.child().child()
        self.assertEqual(leaf.trace_id, root.trace_id)
        self.assertNotEqual(leaf.parent_span_id, root.span_id)

    def test_child_headers_continue_the_same_trace(self):
        root = TraceContext()
        child = root.child()
        parsed = parse_traceparent(child.as_headers()["traceparent"])
        self.assertEqual(parsed["trace_id"], root.trace_id)
        self.assertEqual(parsed["span_id"], child.span_id)

    def test_child_is_not_active_outside_its_block(self):
        root = TraceContext()
        child = root.child()
        with root:
            inside_parent = tiny_log.current_context()["span_id"]
            with child:
                inside_child = tiny_log.current_context()["span_id"]
            after_child = tiny_log.current_context()["span_id"]
        self.assertEqual(inside_parent, root.span_id)
        self.assertEqual(inside_child, child.span_id)
        self.assertEqual(after_child, root.span_id)

    def test_child_accepts_an_explicit_span_id(self):
        root = TraceContext()
        child = root.child(span_id="00f067aa0ba902b7")
        self.assertEqual(child.span_id, "00f067aa0ba902b7")
        self.assertEqual(child.as_headers()["traceparent"].split("-")[2], "00f067aa0ba902b7")
