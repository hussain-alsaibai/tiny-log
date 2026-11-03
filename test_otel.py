"""Tests for tiny_log.otel (OpenTelemetry-compatible trace context)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

# Make the local checkout importable
sys.path.insert(0, str(Path(__file__).parent))

from tiny_log import configure as log_configure, get_logger
from otel import (  # noqa: I001
    ContextVarsProvider,
    TraceContextFilter,
    TraceContextProvider,
    bind_trace_context,
    clear_trace_context,
    configure,
    current_trace_context,
    get_default_filter,
    reset_trace_context,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_trace_state():
    """Ensure each test starts with empty trace context."""
    clear_trace_context()
    yield
    clear_trace_context()


class FakeProvider:
    """Implements the TraceContextProvider protocol for testing."""

    def __init__(self, trace_id: str = "fake-trace", span_id: str = "fake-span"):
        self._trace_id = trace_id
        self._span_id = span_id

    def current(self) -> dict:
        return {"trace_id": self._trace_id, "span_id": self._span_id}


class FailingProvider:
    """Provider whose current() raises — the filter should not kill logging."""

    def current(self) -> dict:
        raise RuntimeError("provider is down")


# ---------------------------------------------------------------------------
# ContextVarsProvider
# ---------------------------------------------------------------------------


def test_contextvars_provider_empty():
    ctx = ContextVarsProvider().current()
    assert ctx == {"trace_id": None, "span_id": None}


def test_bind_and_read_trace_context():
    tokens = bind_trace_context(trace_id="abc-123", span_id="def-456")
    try:
        ctx = current_trace_context()
        assert ctx["trace_id"] == "abc-123"
        assert ctx["span_id"] == "def-456"
    finally:
        reset_trace_context(tokens)


def test_reset_trace_context():
    bind_trace_context(trace_id="outer", span_id="outer-span")
    tokens = bind_trace_context(trace_id="inner", span_id="inner-span")
    assert current_trace_context()["trace_id"] == "inner"
    reset_trace_context(tokens)
    assert current_trace_context()["trace_id"] == "outer"


def test_bind_only_one_field():
    # Fixture already cleared state. Bind only trace_id; span_id stays None.
    tokens = bind_trace_context(trace_id="only-trace")
    try:
        ctx = current_trace_context()
        assert ctx["trace_id"] == "only-trace"
        assert ctx["span_id"] is None
    finally:
        reset_trace_context(tokens)


# ---------------------------------------------------------------------------
# TraceContextFilter
# ---------------------------------------------------------------------------


def test_filter_attaches_context():
    flt = TraceContextFilter()
    bind_trace_context(trace_id="tr-1", span_id="sp-1")
    record = logging.LogRecord("test", logging.INFO, "/x", 1, "msg", None, None)
    flt.filter(record)
    assert record.trace_id == "tr-1"
    assert record.span_id == "sp-1"


def test_filter_uses_custom_provider():
    provider = FakeProvider(trace_id="custom-trace", span_id="custom-span")
    flt = TraceContextFilter(provider=provider)
    record = logging.LogRecord("test", logging.INFO, "/x", 1, "msg", None, None)
    flt.filter(record)
    assert record.trace_id == "custom-trace"
    assert record.span_id == "custom-span"


def test_filter_failing_provider_does_not_kill_logging():
    flt = TraceContextFilter(provider=FailingProvider())
    record = logging.LogRecord("test", logging.INFO, "/x", 1, "msg", None, None)
    assert flt.filter(record) is True
    assert record.trace_id is None
    assert record.span_id is None


def test_filter_set_provider_runtime():
    flt = TraceContextFilter(provider=FakeProvider(trace_id="initial"))
    record = logging.LogRecord("test", logging.INFO, "/x", 1, "msg", None, None)
    flt.filter(record)
    assert record.trace_id == "initial"

    flt.set_provider(FakeProvider(trace_id="replaced"))
    flt.filter(record)
    assert record.trace_id == "replaced"


def test_filter_custom_attr_names():
    flt = TraceContextFilter(attr_trace="otel_trace", attr_span="otel_span")
    bind_trace_context(trace_id="a", span_id="b")
    record = logging.LogRecord("test", logging.INFO, "/x", 1, "msg", None, None)
    flt.filter(record)
    assert record.otel_trace == "a"
    assert record.otel_span == "b"


# ---------------------------------------------------------------------------
# Module-level configure()
# ---------------------------------------------------------------------------


def test_configure_returns_filter():
    flt = configure(provider=FakeProvider(trace_id="cfg-trace"))
    assert isinstance(flt, TraceContextFilter)
    record = logging.LogRecord("test", logging.INFO, "/x", 1, "msg", None, None)
    flt.filter(record)
    assert record.trace_id == "cfg-trace"


def test_configure_replaces_provider():
    flt = configure(provider=FakeProvider(trace_id="first"))
    configure(provider=FakeProvider(trace_id="second"))
    record = logging.LogRecord("test", logging.INFO, "/x", 1, "msg", None, None)
    flt.filter(record)
    assert record.trace_id == "second"


def test_get_default_filter():
    # Reset state first
    flt = configure(provider=FakeProvider())
    assert get_default_filter() is flt


# ---------------------------------------------------------------------------
# Integration with tiny-log
# ---------------------------------------------------------------------------


def test_integration_with_tiny_log():
    """End-to-end: configure tiny-log with the trace filter and verify the filter
    attaches trace_id/span_id to LogRecords (formatters opt-in to surface them)."""
    log_configure(level="DEBUG", json=True)
    log = get_logger("otel-test")
    provider = FakeProvider(trace_id="integration-trace", span_id="integration-span")
    flt = configure(provider=provider)

    root = logging.getLogger()
    root.addFilter(flt)

    try:
        # Emit a record and inspect the resulting LogRecord directly.
        record = logging.LogRecord(
            "otel-test", logging.INFO, "/x", 1, "hello", None, None
        )
        flt.filter(record)
        assert record.trace_id == "integration-trace"
        assert record.span_id == "integration-span"
        # Sanity: the logger exists and is usable.
        assert log is not None
    finally:
        root.removeFilter(flt)


def test_integration_trace_id_via_bound_context():
    """When trace context is bound via bind_trace_context, the filter reads from it."""
    log_configure(level="INFO", json=True)
    flt = configure(provider=ContextVarsProvider())

    bind_trace_context(trace_id="bound-trace", span_id="bound-span")
    try:
        record = logging.LogRecord("trace-test", logging.INFO, "/x", 1, "msg", None, None)
        flt.filter(record)
        assert record.trace_id == "bound-trace"
        assert record.span_id == "bound-span"
    finally:
        # Reset context to None
        _t = bind_trace_context(trace_id=None, span_id=None)
        reset_trace_context(_t)


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------


def test_fake_provider_is_protocol_conformant():
    """FakeProvider should satisfy the TraceContextProvider protocol."""
    provider: TraceContextProvider = FakeProvider()
    ctx = provider.current()
    assert "trace_id" in ctx
    assert "span_id" in ctx
