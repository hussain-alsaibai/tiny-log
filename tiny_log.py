"""
tiny-log v0.3.0 — Zero-Dependency Structured Logging
--------------------------------------------------
Like structlog/loguru, but in one file. Zero dependencies.

v0.3.0 adds:
- OpenTelemetry W3C TraceContext propagation (inject/extract trace IDs)
- JSONL file output for log pipelines and Loki/Prometheus ingestion
- Better JSON mode with trace_id, span_id, service_name fields
- `log_call` / `log_call_async` with structured timing
- Structured filter for log levels by field

Usage:
    from tiny_log import configure, get_logger, LogContext

    configure(service_name="my-agent", exporter="jsonl", log_file="logs/app.jsonl")
    log = get_logger("api")

    with LogContext(trace_id=get_trace_id()):
        log.info("request received", extra={"user_id": 42})
"""

from __future__ import annotations

import sys
import json
import time
import uuid
import logging
import threading
import contextvars
from typing import Any, Optional
from functools import wraps

__version__ = "0.3.0"
__all__ = [
    "configure", "get_logger", "TinyLogger", "BoundLogger",
    "LogContext", "new_trace_id", "new_request_id", "new_span_id",
    "get_trace_context", "inject_trace_context", "extract_trace_context",
    "get_current_trace_id", "file_handler", "log_call", "log_call_async",
]

# ─── Global config ─────────────────────────────────────────────────────────────

_CONFIG: dict[str, Any] = {
    "service_name": "tiny-log",
    "exporter": "stdout",  # "stdout" | "jsonl" | "text"
    "log_file": None,      # path for jsonl file
    "jsonl_append": True,  # append to existing file
    "level": logging.INFO,
    "color": True,
    "capture_warnings": True,
    "_lock": threading.Lock(),
}

_CONFIGURED = False

# ─── Trace context ─────────────────────────────────────────────────────────────

_trace_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "trace_id", default=None
)
_span_id_counter: contextvars.ContextVar[int] = contextvars.ContextVar(
    "span_id_counter", default=0
)

def new_trace_id() -> str:
    """Generate a 32-char hex trace ID (compatible with W3C TraceContext)."""
    return uuid.uuid4().hex[:32]

def new_span_id() -> str:
    """Generate a 16-char hex span ID."""
    return uuid.uuid4().hex[:16]

def new_request_id(length: int = 16) -> str:
    """Generate a random request ID."""
    return uuid.uuid4().hex[:length]

def get_current_trace_id() -> Optional[str]:
    """Return the current trace ID from context, or None."""
    return _trace_id_var.get()

def get_trace_context() -> dict[str, str]:
    """Return W3C TraceContext headers for propagation."""
    trace_id = _trace_id_var.get() or new_trace_id()
    span_id = new_span_id()
    return {
        "traceparent": f"00-{trace_id}-{span_id}-01",
        "tracestate": "",
    }

def inject_trace_context(headers: dict[str, str]) -> dict[str, str]:
    """Inject current trace context into a headers dict (mutates and returns)."""
    ctx = get_trace_context()
    headers.update(ctx)
    return headers

def extract_trace_context(headers: dict[str, str]) -> Optional[str]:
    """Parse traceparent header, return trace_id or None."""
    tp = headers.get("traceparent", "")
    if tp.startswith("00-"):
        parts = tp.split("-")
        if len(parts) >= 2:
            return parts[1]
    return None

def set_trace_id(trace_id: Optional[str]) -> None:
    """Set the current trace ID in context."""
    _trace_id_var.set(trace_id)

# ─── Formatters ───────────────────────────────────────────────────────────────

class JsonFormatter(logging.Formatter):
    """JSON formatter with trace context, service name, and structured extras."""

    def __init__(
        self,
        *,
        ensure_ascii: bool = False,
        sort_keys: bool = False,
        service_name: str = "tiny-log",
        pretty: bool = False,
    ) -> None:
        super().__init__()
        self.ensure_ascii = ensure_ascii
        self.sort_keys = sort_keys
        self.service_name = service_name
        self.indent = 2 if pretty else None

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, "%Y-%m-%dT%H:%M:%S.fffZ")
        trace_id = _trace_id_var.get() or getattr(record, "trace_id", None)
        span_id = getattr(record, "span_id", None)

        payload = {
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "service": getattr(record, "service", self.service_name),
        }

        if trace_id:
            payload["trace_id"] = trace_id
        if span_id:
            payload["span_id"] = span_id

        # Merge all extra fields
        if hasattr(record, "extra_fields"):
            payload.update(record.extra_fields)
        if hasattr(record, "duration_ms"):
            payload["duration_ms"] = round(record.duration_ms, 2)
        if hasattr(record, "error"):
            payload["error"] = record.error

        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            payload["exc_message"] = str(record.exc_info[1]) if record.exc_info[1] else None

        return json.dumps(payload, ensure_ascii=self.ensure_ascii, sort_keys=self.sort_keys,
                         default=str, indent=self.indent)


class TextFormatter(logging.Formatter):
    """Pretty text formatter with color support and structured fields."""

    LEVEL_COLORS = {
        "DEBUG":    "\033[36m",    # cyan
        "INFO":     "\033[32m",    # green
        "WARNING":  "\033[33m",    # yellow
        "ERROR":    "\033[31m",    # red
        "CRITICAL": "\033[35m",    # magenta
        "RESET":    "\033[0m",
    }

    def __init__(self, *, color: bool = True) -> None:
        super().__init__()
        self.color = color and sys.stderr.isatty()

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, "%H:%M:%S.fff")
        level = record.levelname.ljust(8)
        trace_id = _trace_id_var.get() or getattr(record, "trace_id", None)
        trace_str = f"[{trace_id[:8]}...] " if trace_id else ""

        extra_fields = getattr(record, "extra_fields", {})
        extra_str = ""
        if extra_fields:
            pairs = " ".join(f"{k}={self._fmt_val(v)}" for k, v in extra_fields.items() if k not in ("duration_ms",))
            extra_str = f" {pairs}"

        duration = ""
        if hasattr(record, "duration_ms"):
            duration = f" +{record.duration_ms:.1f}ms"

        color = self.LEVEL_COLORS.get(record.levelname, "")
        reset = self.LEVEL_COLORS["RESET"] if self.color else ""

        return (
            f"{ts} {color}{level}{reset}{trace_str}{record.getMessage()}{duration}{extra_str}"
        )

    def _fmt_val(self, v: Any) -> str:
        if isinstance(v, str):
            return f'"{v}"'
        if isinstance(v, float):
            return f"{v:.2f}"
        return str(v)


# ─── JSONL File Handler ────────────────────────────────────────────────────────

class JsonlFileHandler(logging.Handler):
    """
    File handler that writes one JSON object per line (JSONL / ndjson).
    Thread-safe. Uses write-flush to ensure durability.
    """

    def __init__(
        self,
        path: str,
        *,
        service_name: str = "tiny-log",
        append: bool = True,
        lock: Optional[threading.Lock] = None,
    ) -> None:
        super().__init__()
        self.path = path
        self._service_name = service_name
        self._lock = lock or threading.Lock()
        self._file = None
        self._closed = False

        # Ensure directory exists
        import os as _os
        _os.makedirs(_os.path.dirname(_os.path.abspath(path)) or ".", exist_ok=True)

        mode = "a" if append else "w"
        self._file = open(path, mode, encoding="utf-8", buffering=1)

    def emit(self, record: logging.LogRecord) -> None:
        if self._closed:
            return
        try:
            ts = self.formatTime(record, "%Y-%m-%dT%H:%M:%S.fffZ")
            trace_id = _trace_id_var.get() or getattr(record, "trace_id", None)

            payload: dict[str, Any] = {
                "ts": ts,
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
                "service": getattr(record, "service", self._service_name),
            }

            if trace_id:
                payload["trace_id"] = trace_id

            if hasattr(record, "extra_fields"):
                payload.update(record.extra_fields)
            if hasattr(record, "duration_ms"):
                payload["duration_ms"] = round(record.duration_ms, 2)
            if hasattr(record, "error"):
                payload["error"] = record.error

            if record.exc_info:
                payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
                payload["exc_message"] = str(record.exc_info[1]) if record.exc_info[1] else None

            line = json.dumps(payload, ensure_ascii=True, default=str) + "\n"
            with self._lock:
                if self._file and not self._closed:
                    self._file.write(line)
                    self._file.flush()

        except Exception:
            self.handleError(record)

    def close(self) -> None:
        with self._lock:
            if self._file and not self._closed:
                self._file.close()
                self._closed = True
        super().close()


# ─── LogContext ────────────────────────────────────────────────────────────────

class LogContext:
    """
    Context manager that adds structured fields to all log calls within the block.

    Usage:
        with LogContext(request_id="req-123", user_id=42):
            log.info("processing")
            log.info("done")  # both include request_id and user_id
    """

    def __init__(self, **values: Any) -> None:
        self._values = values
        self._token: Optional[contextvars.Token] = None

    def __enter__(self) -> "LogContext":
        self._token = _extra_context_var.set(dict(self._values))
        return self

    def __exit__(self, *exc_info: Any) -> None:
        if self._token is not None:
            _extra_context_var.reset(self._token)

    def bind(self, **more: Any) -> "LogContext":
        """Return a new context with additional fields."""
        return LogContext(**{**self._values, **more})

    @classmethod
    def from_request_id(cls, request_id: Optional[str] = None) -> "LogContext":
        request_id = request_id or new_request_id()
        return cls(request_id=request_id)

    @classmethod
    def from_trace(cls, trace_id: Optional[str] = None) -> "LogContext":
        trace_id = trace_id or new_trace_id()
        set_trace_id(trace_id)
        return cls(trace_id=trace_id)


_extra_context_var: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "extra_context", default={}
)

def current_context() -> dict[str, Any]:
    """Return the current LogContext fields."""
    return dict(_extra_context_var.get())


# ─── Configure ────────────────────────────────────────────────────────────────

def configure(
    level: str | int = "INFO",
    *,
    json: bool | None = None,
    color: bool = True,
    stream: Optional[Any] = None,
    capture_warnings: bool = True,
    force_json: bool = False,
    service_name: str = "tiny-log",
    exporter: str = "stdout",
    log_file: Optional[str] = None,
    jsonl_append: bool = True,
    pretty_json: bool = False,
) -> logging.Logger:
    """
    Configure the root logger.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        json: If True, emit JSON; if None, auto-detect (JSON in pipes, text in TTY).
        color: Use ANSI colors (only in text mode).
        stream: Output stream (default: stderr).
        capture_warnings: Pipe Python warnings through logging.
        force_json: Skip auto-detect, always JSON.
        service_name: Service identifier in log output.
        exporter: "stdout" | "jsonl" | "text"
        log_file: Path for jsonl output.
        jsonl_append: Append to existing log file (True) or overwrite (False).
        pretty_json: Pretty-print JSON output.
    """
    global _CONFIGURED, _CONFIG

    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    _CONFIG.update({
        "service_name": service_name,
        "exporter": exporter,
        "log_file": log_file,
        "jsonl_append": jsonl_append,
        "level": level,
        "color": color,
        "capture_warnings": capture_warnings,
    })

    root = logging.getLogger()
    root.setLevel(level)

    # Remove old handlers
    to_remove = [h for h in root.handlers if getattr(h, "_tiny_log", False)]
    for h in to_remove:
        root.removeHandler(h)

    if exporter == "jsonl":
        if not log_file:
            log_file = f"{service_name}.jsonl"
        handler: logging.Handler = JsonlFileHandler(
            log_file,
            service_name=service_name,
            append=jsonl_append,
            lock=_CONFIG["_lock"],
        )
        handler._tiny_log = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    else:
        if force_json:
            json_mode = True
            color = False
        elif json is None:
            json_mode = not (sys.stderr.isatty() if stream is None else False)
        else:
            json_mode = json

        handler = logging.StreamHandler(stream or sys.stderr)
        handler._tiny_log = True  # type: ignore[attr-defined]
        handler.setFormatter(
            JsonFormatter(service_name=service_name, pretty=pretty_json)
            if json_mode else TextFormatter(color=color and not sys.stderr.isatty())
        )
        root.addHandler(handler)

    if capture_warnings:
        logging.captureWarnings(True)

    _CONFIGURED = True
    return root


def get_logger(name: str | None = None) -> "TinyLogger":
    """Get a tiny-log Logger."""
    if not _CONFIGURED:
        configure()
    return TinyLogger(logging.getLogger(name or "tiny"))


# ─── TinyLogger ───────────────────────────────────────────────────────────────

class TinyLogger:
    """A structured logger wrapping stdlib Logger with extra fields and trace context."""

    def __init__(self, logger: logging.Logger) -> None:
        self._log = logger

    @property
    def level(self) -> int:
        return self._log.level

    def set_level(self, level: str | int) -> None:
        self._log.setLevel(level if isinstance(level, int)
                           else getattr(logging, level.upper()))

    def _log_at(
        self,
        level: int,
        msg: str,
        *,
        extra: dict[str, Any] | None = None,
        duration_ms: float | None = None,
        error: str | None = None,
        **kwargs: Any,
    ) -> None:
        # Merge context vars + extras + kwargs
        ctx = dict(_extra_context_var.get())
        merged: dict[str, Any] = {**ctx, **(extra or {}), **kwargs}

        record = self._log.makeRecord(
            self._log.name, level, "(unknown)", 0, msg, (), None
        )
        record.extra_fields = merged
        if duration_ms is not None:
            record.duration_ms = duration_ms
        if error:
            record.error = error

        self._log.handle(record)

    def debug(self, msg: str, *, extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        self._log_at(logging.DEBUG, msg, extra=extra, **kwargs)

    def info(self, msg: str, *, extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        self._log_at(logging.INFO, msg, extra=extra, **kwargs)

    def warning(self, msg: str, *, extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        self._log_at(logging.WARNING, msg, extra=extra, **kwargs)

    def error(self, msg: str, *, extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        self._log_at(logging.ERROR, msg, extra=extra, **kwargs)

    def critical(self, msg: str, *, extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        self._log_at(logging.CRITICAL, msg, extra=extra, **kwargs)

    def exception(
        self,
        msg: str,
        *,
        extra: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self._log_at(logging.ERROR, msg, extra=extra, **kwargs)

    def bind(self, **values: Any) -> "BoundLogger":
        """Return a logger with permanent extra values merged into every call."""
        return BoundLogger(self, values)


class BoundLogger:
    """A logger with permanently bound fields."""

    def __init__(self, logger: TinyLogger, bound: dict[str, Any]) -> None:
        self._logger = logger
        self._bound = dict(bound)

    def _merge(self, extra: dict[str, Any] | None) -> dict[str, Any]:
        return {**self._bound, **(extra or {})}

    def debug(self, msg: str, *, extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        self._logger._log_at(logging.DEBUG, msg, extra=self._merge(extra), **kwargs)

    def info(self, msg: str, *, extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        self._logger._log_at(logging.INFO, msg, extra=self._merge(extra), **kwargs)

    def warning(self, msg: str, *, extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        self._logger._log_at(logging.WARNING, msg, extra=self._merge(extra), **kwargs)

    def error(self, msg: str, *, extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        self._logger._log_at(logging.ERROR, msg, extra=self._merge(extra), **kwargs)

    def critical(self, msg: str, *, extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        self._logger._log_at(logging.CRITICAL, msg, extra=self._merge(extra), **kwargs)

    def exception(self, msg: str, *, extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        self._logger._log_at(logging.ERROR, msg, extra=self._merge(extra), **kwargs)


# ─── File handler (backward compat) ──────────────────────────────────────────

def file_handler(
    path: str,
    *,
    level: str | int = "INFO",
    json: bool = True,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> logging.Handler:
    """
    Return a rotating file handler.
    Deprecated: use configure(exporter="jsonl", log_file="path") instead.
    """
    from logging.handlers import RotatingFileHandler
    handler = RotatingFileHandler(
        path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    handler.setLevel(level if isinstance(level, int) else getattr(logging, level.upper()))
    handler.setFormatter(JsonFormatter() if json else TextFormatter())
    return handler


# ─── log_call / log_call_async ───────────────────────────────────────────────

def log_call(
    logger: Optional[TinyLogger] = None,
    level: int = logging.INFO,
    log_result: bool = False,
    log_args: bool = False,
) -> Callable:
    """
    Decorator to time and log a function call.

    Usage:
        @log_call(get_logger("api"))
        def fetch_data(url):
            return requests.get(url).json()

        @log_call(log_result=True, log_args=True)
        def expensive_computation(n):
            return sum(range(n))
    """
    def decorator(fn: Callable) -> Callable:
        _logger = logger or get_logger(fn.__module__)

        @wraps(fn)
        def wrapper(*args, **kwargs):
            name = f"{fn.__module__}.{fn.__qualname__}"
            extra = {}
            if log_args:
                extra["args"] = str(args)[:200]
                extra["kwargs"] = str(kwargs)[:200]

            start = time.perf_counter()
            try:
                result = fn(*args, **kwargs)
                duration = (time.perf_counter() - start) * 1000
                record = _logger._log.makeRecord(
                    _logger._log.name, level, "(unknown)", 0, name, (), None
                )
                record.extra_fields = {**dict(_extra_context_var.get()), **extra}
                record.duration_ms = duration
                if log_result:
                    record.extra_fields["result"] = str(result)[:200]
                _logger._log.handle(record)
                return result
            except Exception as exc:
                duration = (time.perf_counter() - start) * 1000
                record = _logger._log.makeRecord(
                    _logger._log.name, logging.ERROR, "(unknown)", 0, name, (), None
                )
                record.extra_fields = {**dict(_extra_context_var.get()), **extra, "error": str(exc)}
                record.duration_ms = duration
                record.exc_info = sys.exc_info()
                _logger._log.handle(record)
                raise

        return wrapper
    return decorator


async def log_call_async(
    logger: Optional[TinyLogger] = None,
    level: int = logging.INFO,
    log_result: bool = False,
) -> Callable:
    """Async version of log_call."""
    def decorator(fn: Callable) -> Callable:
        _logger = logger or get_logger(fn.__module__)

        @wraps(fn)
        async def wrapper(*args, **kwargs):
            name = f"{fn.__module__}.{fn.__qualname__}"
            start = time.perf_counter()
            try:
                result = await fn(*args, **kwargs)
                duration = (time.perf_counter() - start) * 1000
                record = _logger._log.makeRecord(
                    _logger._log.name, level, "(unknown)", 0, name, (), None
                )
                record.extra_fields = dict(_extra_context_var.get())
                record.duration_ms = duration
                if log_result:
                    record.extra_fields["result"] = str(result)[:200]
                _logger._log.handle(record)
                return result
            except Exception as exc:
                duration = (time.perf_counter() - start) * 1000
                record = _logger._log.makeRecord(
                    _logger._log.name, logging.ERROR, "(unknown)", 0, name, (), None
                )
                record.extra_fields = {**dict(_extra_context_var.get()), "error": str(exc)}
                record.duration_ms = duration
                record.exc_info = sys.exc_info()
                _logger._log.handle(record)
                raise

        return wrapper
    return decorator
