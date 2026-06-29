"""tiny_log — zero-dependency structured logging for Python.

A single-file structured logger that emits JSON or pretty-printed text, with
context binding, log levels, rotation hooks, and correlation IDs. Built on
the standard library `logging` module — no extra packages.

Usage:
    from tiny_log import get_logger, configure, LogContext

    configure(level="INFO", json=False)
    log = get_logger("myapp")

    with LogContext(request_id="abc-123"):
        log.info("starting job", extra={"job": "ingest"})
        log.error("failed", extra={"reason": "timeout"})
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Callable, TextIO


__version__ = "0.1.0"


# ---------- Context (correlation IDs) ----------


_context: ContextVar[dict[str, Any]] = ContextVar("tiny_log_context", default={})


class LogContext:
    """Context manager / decorator that binds values to the current log scope.

    Bound values are automatically added to every log record emitted inside
    the context — perfect for request_id, user_id, trace_id, etc.

    Example:
        with LogContext(request_id="abc-123"):
            log.info("processing")  # includes request_id
    """

    def __init__(self, **values: Any) -> None:
        self._values = values
        self._token: Any = None

    def __enter__(self) -> "LogContext":
        self._token = _context.set({**_context.get(), **self._values})
        return self

    def __exit__(self, *exc_info: Any) -> None:
        _context.reset(self._token)

    def bind(self, **more: Any) -> "LogContext":
        return LogContext(**{**self._values, **more})


def current_context() -> dict[str, Any]:
    return dict(_context.get())


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


# ---------- Formatters ----------


class JsonFormatter(logging.Formatter):
    """Emits one JSON object per line."""

    DEFAULT_KEYS = {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "asctime",
    }

    def __init__(self, *, ensure_ascii: bool = False, sort_keys: bool = False) -> None:
        super().__init__()
        self._ensure_ascii = ensure_ascii
        self._sort_keys = sort_keys

    def format(self, record: logging.LogRecord) -> str:
        # Standard fields
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Bound context (request_id, etc.)
        ctx = _context.get()
        if ctx:
            payload["ctx"] = ctx

        # Anything passed via extra=
        for key, value in record.__dict__.items():
            if key in self.DEFAULT_KEYS or key.startswith("_"):
                continue
            if key in payload:
                continue
            try:
                json.dumps(value)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = repr(value)

        # Exception info
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = record.stack_info

        return json.dumps(payload, ensure_ascii=self._ensure_ascii, sort_keys=self._sort_keys)


class TextFormatter(logging.Formatter):
    """Colorized, pretty-printed text format for humans."""

    COLORS = {
        "DEBUG": "\x1b[90m",     # grey
        "INFO": "\x1b[36m",      # cyan
        "WARNING": "\x1b[33m",   # yellow
        "ERROR": "\x1b[31m",     # red
        "CRITICAL": "\x1b[1;31m",  # bold red
    }
    RESET = "\x1b[0m"

    def __init__(self, *, color: bool = True) -> None:
        super().__init__()
        self._color = color

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%H:%M:%S.%f")[:-3]
        level = record.levelname
        prefix = f"{ts} {level:<8} {record.name}"
        if self._color and level in self.COLORS and sys.stderr.isatty():
            prefix = f"{self.COLORS[level]}{prefix}{self.RESET}"
        line = f"{prefix} | {record.getMessage()}"
        ctx = _context.get()
        if ctx:
            line += f"  ctx={ctx}"
        # Include extras
        for key, value in record.__dict__.items():
            if key in self.DEFAULT_KEYS or key.startswith("_") or key in {"ctx"}:
                continue
            if key in {"request_id", "user_id", "trace_id"}:
                line += f"  {key}={value}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line

    DEFAULT_KEYS = JsonFormatter.DEFAULT_KEYS  # type: ignore[assignment]


# ---------- Configuration ----------


_CONFIGURED = False


def configure(
    level: str | int = "INFO",
    *,
    json: bool | None = None,  # type: ignore[override]
    color: bool = True,
    stream: TextIO | None = None,
    capture_warnings: bool = True,
) -> logging.Logger:
    """Configure the root logger. Safe to call multiple times."""
    global _CONFIGURED

    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    # Remove our own handlers (don't touch handlers we didn't add)
    to_remove = [h for h in root.handlers if getattr(h, "_tiny_log", False)]
    for h in to_remove:
        root.removeHandler(h)

    handler = logging.StreamHandler(stream or sys.stderr)
    handler._tiny_log = True  # type: ignore[attr-defined]

    if json is None:
        # Auto: JSON if not a TTY, text if TTY
        json = not (sys.stderr.isatty() if stream is None else False)
    handler.setFormatter(JsonFormatter() if json else TextFormatter(color=color and json is False))
    root.addHandler(handler)

    if capture_warnings:
        logging.captureWarnings(True)

    _CONFIGURED = True
    return root


def get_logger(name: str | None = None) -> "TinyLogger":
    """Get a tiny-log Logger. Returns a thin wrapper over stdlib Logger."""
    if not _CONFIGURED:
        configure()
    return TinyLogger(logging.getLogger(name or "tiny"))


# ---------- Tiny logger wrapper ----------


class TinyLogger:
    """A small wrapper that adds keyword-only `extra` to each call."""

    def __init__(self, logger: logging.Logger) -> None:
        self._log = logger

    @property
    def level(self) -> int:
        return self._log.level

    def set_level(self, level: str | int) -> None:
        self._log.setLevel(level if isinstance(level, int) else getattr(logging, level.upper()))

    def _log_at(self, level: int, msg: str, *, extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        merged: dict[str, Any] = dict(extra or {})
        merged.update(kwargs)
        self._log.log(level, msg, extra=merged)

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

    def exception(self, msg: str, *, extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        merged: dict[str, Any] = dict(extra or {})
        merged.update(kwargs)
        self._log.exception(msg, extra=merged)

    def bind(self, **values: Any) -> "BoundLogger":
        """Return a logger with permanent extra values merged into every call."""
        return BoundLogger(self, values)


class BoundLogger:
    """A logger with permanent extra values. Useful for service-name, etc."""

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
        merged = self._merge(extra)
        self._logger._log.exception(msg, extra=merged)


# ---------- Rotation hook (optional) ----------


def file_handler(
    path: str,
    *,
    level: str | int = "INFO",
    json: bool = True,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> logging.Handler:
    """Return a rotating file handler configured for tiny-log.

    Implemented with stdlib `logging.handlers.RotatingFileHandler` — no deps.
    """
    from logging.handlers import RotatingFileHandler

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    handler: logging.Handler = RotatingFileHandler(
        path, maxBytes=max_bytes, backupCount=backup_count
    )
    handler.setLevel(level if isinstance(level, int) else getattr(logging, level.upper()))
    handler.setFormatter(JsonFormatter() if json else TextFormatter(color=False))
    handler._tiny_log = True  # type: ignore[attr-defined]
    return handler


# ---------- Time-it helper ----------


def log_call(
    logger: TinyLogger,
    op: str,
    fn: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Run fn, logging duration. Returns the function's result."""
    t0 = time.perf_counter()
    try:
        result = fn(*args, **kwargs)
        logger.info(f"{op} ok", extra={"op": op, "ms": int((time.perf_counter() - t0) * 1000)})
        return result
    except Exception as exc:
        logger.error(
            f"{op} failed: {exc}",
            extra={"op": op, "ms": int((time.perf_counter() - t0) * 1000)},
        )
        raise


__all__ = [
    "configure",
    "get_logger",
    "LogContext",
    "current_context",
    "new_request_id",
    "JsonFormatter",
    "TextFormatter",
    "TinyLogger",
    "BoundLogger",
    "file_handler",
    "log_call",
    "__version__",
]
