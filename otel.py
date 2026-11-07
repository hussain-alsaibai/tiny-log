"""OpenTelemetry-compatible trace context for tiny-log.

When a log record is emitted, look up the active trace_id / span_id
from an injected provider (or contextvars fallback) and add them to
the structured record. This bridges tiny-log to tiny-trace and any
OTel-compatible exporter.

Stdlib-only. Zero deps.

Usage:
    from tiny_log import configure, get_logger
    from tiny_log.otel import TraceContextProvider, bind_trace_context

    # Option 1: use the contextvars-based default provider
    bind_trace_context(trace_id="abc123", span_id="def456")
    log = get_logger("myapp")
    log.info("hello")  # record carries trace_id=abc123, span_id=def456

    # Option 2: inject a tiny-trace-compatible provider
    class TinyTraceProvider:
        def current(self):
            return {"trace_id": "...", "span_id": "..."}

    configure(provider=TinyTraceProvider())
    log.info("now OTel-aware")  # auto-resolved from provider
"""

from __future__ import annotations

import contextvars
import logging
import threading
from typing import Any, Dict, Optional, Protocol

__all__ = [
    "TraceContextProvider",
    "ContextVarsProvider",
    "bind_trace_context",
    "current_trace_context",
    "TraceContextFilter",
    "configure",
]


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------


class TraceContextProvider(Protocol):
    """Anything that returns the current trace_id / span_id.

    Implementations: tiny-trace's Tracer, OpenTelemetry's get_current_span(),
    custom dict lookups, etc. The only requirement is a `current()` method
    that returns a mapping with at least 'trace_id' and 'span_id' (both
    may be None).
    """

    def current(self) -> Dict[str, Optional[str]]:
        ...


class ContextVarsProvider:
    """Default provider backed by contextvars (stdlib)."""

    def current(self) -> Dict[str, Optional[str]]:
        return {
            "trace_id": _trace_id_var.get(),
            "span_id": _span_id_var.get(),
        }


_trace_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "tiny_log_trace_id", default=None
)
_span_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "tiny_log_span_id", default=None
)


# ---------------------------------------------------------------------------
# Public API: bind / read trace context
# ---------------------------------------------------------------------------


def bind_trace_context(
    trace_id: Optional[str] = None,
    span_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Set trace_id and span_id in the current context. Returns the tokens.

    Use the tokens to reset:
        tokens = bind_trace_context(trace_id="abc", span_id="def")
        try:
            ...
        finally:
            reset_trace_context(tokens)
    """
    tokens: Dict[str, Any] = {}
    if trace_id is not None:
        tokens["trace_id"] = _trace_id_var.set(trace_id)
    if span_id is not None:
        tokens["span_id"] = _span_id_var.set(span_id)
    return tokens


def reset_trace_context(tokens: Dict[str, Any]) -> None:
    """Reset trace context to its prior values (from bind_trace_context)."""
    for key, token in tokens.items():
        if key == "trace_id":
            _trace_id_var.reset(token)
        elif key == "span_id":
            _span_id_var.reset(token)


def clear_trace_context() -> None:
    """Reset both trace_id and span_id to None unconditionally.

    Use this in test fixtures to guarantee a clean slate between tests.
    """
    _trace_id_var.set(None)
    _span_id_var.set(None)


def current_trace_context() -> Dict[str, Optional[str]]:
    """Read the current trace_id / span_id from the default contextvars provider."""
    return ContextVarsProvider().current()


# ---------------------------------------------------------------------------
# Filter: inject trace_id / span_id into every LogRecord
# ---------------------------------------------------------------------------


class TraceContextFilter(logging.Filter):
    """Logging filter that injects trace_id / span_id into every record.

    Reads from the configured provider (default: ContextVarsProvider) and
    attaches the values to the record's `trace_id` and `span_id` attributes.
    Downstream formatters can pick these up via `%(trace_id)s` etc.

    Example:
        handler = logging.StreamHandler()
        handler.addFilter(TraceContextFilter())
    """

    def __init__(
        self,
        provider: Optional[TraceContextProvider] = None,
        *,
        attr_trace: str = "trace_id",
        attr_span: str = "span_id",
    ) -> None:
        super().__init__()
        self._provider = provider or ContextVarsProvider()
        self._attr_trace = attr_trace
        self._attr_span = attr_span
        self._lock = threading.Lock()
        self._configured_provider: Optional[TraceContextProvider] = None

    def set_provider(self, provider: TraceContextProvider) -> None:
        """Replace the trace context provider at runtime."""
        with self._lock:
            self._configured_provider = provider

    def filter(self, record: logging.LogRecord) -> bool:
        provider = self._configured_provider or self._provider
        try:
            ctx = provider.current()
        except Exception:  # pragma: no cover - provider failure shouldn't kill logging
            setattr(record, self._attr_trace, None)
            setattr(record, self._attr_span, None)
            return True
        setattr(record, self._attr_trace, ctx.get("trace_id"))
        setattr(record, self._attr_span, ctx.get("span_id"))
        return True


# ---------------------------------------------------------------------------
# Module-level configuration
# ---------------------------------------------------------------------------


_default_filter: Optional[TraceContextFilter] = None
_config_lock = threading.Lock()


def configure(provider: Optional[TraceContextProvider] = None) -> TraceContextFilter:
    """Configure the module-level trace context filter.

    If a filter was already configured, replaces its provider. Returns the
    filter so callers can attach it to handlers / loggers.
    """
    global _default_filter
    with _config_lock:
        if _default_filter is None:
            _default_filter = TraceContextFilter(provider=provider)
        elif provider is not None:
            _default_filter.set_provider(provider)
    return _default_filter


def get_default_filter() -> Optional[TraceContextFilter]:
    """Return the module-level filter (or None if not configured)."""
    return _default_filter
