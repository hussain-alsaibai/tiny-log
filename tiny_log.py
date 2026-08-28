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

import asyncio
import functools
import inspect
import json
import logging
import os
import random
import sys
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import (
    Any,
    Awaitable,
    Callable,
    Iterable,
    List,
    Mapping,
    Optional,
    TextIO,
    TypeVar,
    Union,
    overload,
)


__version__ = "0.4.0"


F = TypeVar("F", bound=Callable[..., Any])


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

    @classmethod
    def from_request_id(cls, request_id: str | None = None) -> "LogContext":
        """Create a LogContext with a new or given request_id."""
        return cls(request_id=request_id or new_request_id())


def current_context() -> dict[str, Any]:
    return dict(_context.get())


def new_request_id(length: int = 16) -> str:
    """Generate a short, unique request ID."""
    return uuid.uuid4().hex[:length]


def new_trace_id() -> str:
    """Generate a trace ID (24 hex chars, compatible with OpenTelemetry)."""
    return uuid.uuid4().hex[:24]


def snapshot() -> dict[str, Any]:
    """Return a dict of the current logger context.

    Useful for debugging — inspect the current context values at any point.

    Example:
        with LogContext(request_id="abc", user_id=42):
            data = log_snapshot()
            print(data)  # {"request_id": "abc", "user_id": 42}
    """
    return current_context()


# ---------- Field formatters ----------


# ANSI color codes (foreground)
_ANSI_COLORS = {
    "black": "\x1b[30m",
    "red": "\x1b[31m",
    "green": "\x1b[32m",
    "yellow": "\x1b[33m",
    "blue": "\x1b[34m",
    "magenta": "\x1b[35m",
    "cyan": "\x1b[36m",
    "white": "\x1b[37m",
    "grey": "\x1b[90m",
    "bright_red": "\x1b[91m",
    "bright_green": "\x1b[92m",
    "bright_yellow": "\x1b[93m",
    "bright_blue": "\x1b[94m",
    "bright_magenta": "\x1b[95m",
    "bright_cyan": "\x1b[96m",
    "bright_white": "\x1b[97m",
    "bold": "\x1b[1m",
    "dim": "\x1b[2m",
    "underline": "\x1b[4m",
    "reset": "\x1b[0m",
}


class ColoredString(str):
    """A string subclass carrying an ANSI color tag.

    Both `JsonFormatter` and `TextFormatter` honor the tag: JSON includes a
    `_color` field alongside the value, text mode embeds the actual escape
    codes so it shows up colored in a TTY.
    """

    __slots__ = ("_tiny_color",)

    def __new__(cls, value: str, color: str | None = None) -> "ColoredString":
        instance = super().__new__(cls, value)
        instance._tiny_color = color
        return instance

    def __repr__(self) -> str:  # pragma: no cover - cosmetic only
        return f"ColoredString({str.__repr__(self)}, color={self._tiny_color!r})"


def color(text: Any, name: str = "reset") -> ColoredString:
    """Wrap a value in an ANSI-colored string.

    Usage:
        log.info("user", extra={"role": color("admin", "bright_red")})

    When logged via JsonFormatter, the color name is preserved in a `_color`
    field. When logged via TextFormatter, the actual ANSI codes are emitted
    so the text shows up colored in a TTY.
    """
    return ColoredString(str(text), name)


def json_field(data: Any, *, indent: int | None = 2, ensure_ascii: bool = False) -> str:
    """Serialize a value to a compact or pretty-printed JSON string.

    Useful when you want a JSON object embedded inline in another log field
    rather than merged into the surrounding payload.

    Usage:
        log.info("request", extra={"payload": json_field({"a": 1, "b": [1,2,3]})})
    """
    return json.dumps(data, indent=indent, ensure_ascii=ensure_ascii, default=str, sort_keys=False)


def bytes_human(n: int | float, *, precision: int | None = 1) -> str:
    """Render a byte count as a human-readable string (e.g. ``"1.5 MB"``).

    Supports B, KB, MB, GB, TB, PB. Negative values are formatted with a
    minus prefix. Whole-number magnitudes (``512 B``, ``1024 B -> 1 KB``)
    drop the decimal point entirely so they read naturally.

    Usage:
        log.info("downloaded", extra={"size": bytes_human(1_572_864)})
        # -> {"size": "1.5 MB"}
    """
    if n != n:  # NaN guard
        return "NaN B"
    sign = "-" if n < 0 else ""
    n = abs(n)
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    if n == 0:
        return "0 B"
    idx = 0
    value = float(n)
    while value >= 1024 and idx < len(units) - 1:
        value /= 1024.0
        idx += 1
    # Drop decimals when the value is integral at the requested precision.
    eff_precision = max(0, precision if precision is not None else 1)
    if eff_precision == 0 or value == int(value):
        rendered = f"{int(value)}"
    else:
        rendered = f"{value:.{eff_precision}f}"
    return f"{sign}{rendered} {units[idx]}"


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
            if isinstance(value, ColoredString):
                payload[key] = str(value)
                payload[f"{key}_color"] = value._tiny_color  # type: ignore[attr-defined]
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

        return json.dumps(payload, ensure_ascii=self._ensure_ascii, sort_keys=self._sort_keys, default=str)


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
        is_tty = sys.stderr.isatty()
        if self._color and level in self.COLORS and is_tty:
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
                continue
            if isinstance(value, ColoredString):
                code = _ANSI_COLORS.get(value._tiny_color, "")  # type: ignore[attr-defined]
                if self._color and is_tty and code:
                    line += f"  {key}={code}{value}{self.RESET}"
                else:
                    line += f"  {key}={value}"
                continue
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
    force_json: bool = False,
) -> logging.Logger:
    """Configure the root logger. Safe to call multiple times.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        json: If True, emit JSON; if None, auto-detect (JSON in pipes, text in TTY).
        color: Use ANSI colors (only applies to text mode).
        stream: Output stream (default: stderr).
        capture_warnings: Pipe Python warnings through logging.
        force_json: Skip auto-detect and always use JSON format.
    """
    global _CONFIGURED

    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    if force_json:
        json = True
        color = False

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
        self._filters: List[Callable[[str, Any], bool]] = []

    @property
    def level(self) -> int:
        return self._log.level

    def set_level(self, level: str | int) -> None:
        self._log.setLevel(level if isinstance(level, int) else getattr(logging, level.upper()))

    def add_filter(self, fn: Callable[[str, Any], bool]) -> "TinyLogger":
        """Add a filter function. Return False to drop the log call."""
        self._filters.append(fn)
        return self

    def _log_at(self, level: int, msg: str, *, extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        # Run filters
        for f in self._filters:
            try:
                if not f(msg, extra or {}):
                    return
            except Exception:
                pass
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
        for f in self._filters:
            try:
                if not f(msg, extra or {}):
                    return
            except Exception:
                pass
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
    logger: TinyLogger | BoundLogger,
    op: str,
    fn: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Run fn, logging duration. Returns the function's result.

    Usage:
        result = log_call(log, "db.query", db.query, user_id=42)
    """
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


def log_call_async(
    logger: TinyLogger | BoundLogger,
    op: str,
    fn: Callable[..., Awaitable[Any]],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Async version of log_call. Awaitable.

    Usage:
        result = await log_call_async(log, "api.fetch", fetch_data, user_id=42)
    """
    async def _wrapper() -> Any:
        t0 = time.perf_counter()
        try:
            result = await fn(*args, **kwargs)
            logger.info(f"{op} ok", extra={"op": op, "ms": int((time.perf_counter() - t0) * 1000)})
            return result
        except Exception as exc:
            logger.error(
                f"{op} failed: {exc}",
                extra={"op": op, "ms": int((time.perf_counter() - t0) * 1000)},
            )
            raise
    return _wrapper()


# ---------- Sampling handler ----------


class SamplingHandler(logging.Handler):
    """A logging handler that drops records based on a per-level sample rate.

    Useful when DEBUG-level logging is high volume but you only want to keep
    a fraction of records (e.g. 1 in 100). WARNING/ERROR records are usually
    kept at 100% so failures aren't lost.

    Args:
        rates: Mapping of level name -> keep ratio in ``[0.0, 1.0]``. A rate
            of ``1.0`` keeps every record; ``0.1`` keeps ~10%. Levels not in
            the mapping default to ``1.0`` (keep everything). Level names are
            case-insensitive.
        seed: Optional seed for deterministic sampling — handy in tests.
        target: Optional downstream handler to forward sampled records to.
            If ``None`` the handler still emits a ``LogRecord`` that flows
            through the standard logger hierarchy, but its only behaviour is
            filtering. Use this when you wire ``SamplingHandler`` between
            a logger and its real output handler.

    Usage:
        import logging
        root = logging.getLogger()
        sampled = SamplingHandler(
            rates={"DEBUG": 0.01, "INFO": 1.0, "WARNING": 1.0, "ERROR": 1.0},
        )
        sampled.setFormatter(JsonFormatter())
        root.addHandler(sampled)
    """

    _DEFAULT_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

    def __init__(
        self,
        rates: Mapping[str, float] | None = None,
        *,
        seed: int | None = None,
        target: logging.Handler | None = None,
        level: int | str = logging.NOTSET,
    ) -> None:
        super().__init__(level=level)
        self._rates: dict[str, float] = {}
        for lvl in self._DEFAULT_LEVELS:
            self._rates[lvl] = 1.0
        if rates:
            for k, v in rates.items():
                key = str(k).upper()
                if key not in self._rates:
                    # Allow custom level names too — just register them.
                    self._rates[key] = 1.0
                # Clamp to [0, 1]
                self._rates[key] = max(0.0, min(1.0, float(v)))
        self._rng = random.Random(seed)
        self._target = target

    def should_sample(self, level: int) -> bool:  # pragma: no cover - trivial
        """Return True if a record at ``level`` should be emitted."""
        name = logging.getLevelName(level)
        rate = self._rates.get(name, 1.0)
        if rate >= 1.0:
            return True
        if rate <= 0.0:
            return False
        return self._rng.random() < rate

    def emit(self, record: logging.LogRecord) -> None:
        if not self.should_sample(record.levelno):
            return
        if self._target is not None:
            self._target.emit(record)
        else:
            # If no target handler is wired, default to stderr via stdlib
            # StreamHandler machinery.
            super().emit(record)

    @property
    def rates(self) -> dict[str, float]:
        return dict(self._rates)

    def set_rate(self, level: str, rate: float) -> None:
        """Adjust the sample rate for a level at runtime."""
        key = str(level).upper()
        self._rates[key] = max(0.0, min(1.0, float(rate)))


# ---------- Async logging ----------


class TinyLogAsync:
    """Async wrapper around a TinyLogger.

    Each method schedules the underlying sync call on a worker thread via
    ``asyncio.to_thread`` so the I/O cost of formatting/serialising never
    blocks the event loop. The returned coroutine is fire-and-forget from
    the caller's perspective; awaiting it ensures the log line is fully
    flushed before continuing.

    Usage:
        log = get_logger("svc")
        async def handle(req):
            await TinyLogAsync(log).ainfo("processing", extra={"req_id": req.id})

    For best results, configure JSON output (it's the slowest path).
    """

    def __init__(self, logger: TinyLogger | BoundLogger) -> None:
        self._logger = logger

    async def adebug(self, msg: str, *, extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        await asyncio.to_thread(self._logger.debug, msg, extra=extra, **kwargs)

    async def ainfo(self, msg: str, *, extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        await asyncio.to_thread(self._logger.info, msg, extra=extra, **kwargs)

    async def awarning(self, msg: str, *, extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        await asyncio.to_thread(self._logger.warning, msg, extra=extra, **kwargs)

    async def aerror(self, msg: str, *, extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        await asyncio.to_thread(self._logger.error, msg, extra=extra, **kwargs)

    async def acritical(self, msg: str, *, extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        await asyncio.to_thread(self._logger.critical, msg, extra=extra, **kwargs)


# ---------- Attach decorator ----------


def _safe_repr(value: Any, max_len: int = 200) -> str:
    try:
        r = repr(value)
    except Exception:
        r = object.__repr__(value)
    if len(r) > max_len:
        r = r[: max_len - 1] + "\u2026"
    return r


def attach(
    logger: TinyLogger | BoundLogger | None = None,
    *,
    op: str | None = None,
    log_args: bool = True,
    log_result: bool = True,
    max_arg_len: int = 200,
) -> Callable[[F], F]:
    """Decorator that logs function entry and exit with args and return value.

    Args:
        logger: Logger to write to. Defaults to ``get_logger()`` at call time.
        op: Operation name. Defaults to the function's qualified name.
        log_args: Whether to include positional/keyword args in the entry log.
        log_result: Whether to include the return value in the exit log.
        max_arg_len: Maximum length for repr'd args/result before truncating.

    The argument list is emitted under the ``fn_args`` field (not ``args``)
    because ``args`` is a reserved stdlib LogRecord attribute.

    Usage:
        @attach()
        def parse(s: str): ...
    """

    def decorator(fn: F) -> F:
        op_name = op or f"{fn.__module__}.{fn.__qualname__}"

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            log = logger or get_logger()
            if log_args:
                args_repr = [_safe_repr(a, max_arg_len) for a in args]
                kwargs_repr = {k: _safe_repr(v, max_arg_len) for k, v in kwargs.items()}
                log.debug(
                    f"-> {op_name}",
                    extra={"op": op_name, "fn_args": args_repr, "fn_kwargs": kwargs_repr},
                )
            else:
                log.debug(f"-> {op_name}", extra={"op": op_name})
            t0 = time.perf_counter()
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:
                ms = int((time.perf_counter() - t0) * 1000)
                log.error(
                    f"!! {op_name} failed: {exc}",
                    extra={"op": op_name, "ms": ms, "exc_type": type(exc).__name__},
                )
                raise
            ms = int((time.perf_counter() - t0) * 1000)
            extra: dict[str, Any] = {"op": op_name, "ms": ms}
            if log_result:
                extra["result"] = _safe_repr(result, max_arg_len)
            log.debug(f"<- {op_name}", extra=extra)
            return result

        return wrapper  # type: ignore[return-value]

    return decorator


def attach_async(
    logger: TinyLogger | BoundLogger | None = None,
    *,
    op: str | None = None,
    log_args: bool = True,
    log_result: bool = True,
    max_arg_len: int = 200,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Async version of :func:`attach`.

    Logs entry/exit at DEBUG, exceptions at ERROR. The decorated function
    remains awaitable.

    Usage:
        @attach_async()
        async def fetch(url): ...
    """

    def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        op_name = op or f"{fn.__module__}.{fn.__qualname__}"

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            log = logger or get_logger()
            if log_args:
                args_repr = [_safe_repr(a, max_arg_len) for a in args]
                kwargs_repr = {k: _safe_repr(v, max_arg_len) for k, v in kwargs.items()}
                log.debug(
                    f"-> {op_name}",
                    extra={"op": op_name, "fn_args": args_repr, "fn_kwargs": kwargs_repr},
                )
            else:
                log.debug(f"-> {op_name}", extra={"op": op_name})
            t0 = time.perf_counter()
            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:
                ms = int((time.perf_counter() - t0) * 1000)
                log.error(
                    f"!! {op_name} failed: {exc}",
                    extra={"op": op_name, "ms": ms, "exc_type": type(exc).__name__},
                )
                raise
            ms = int((time.perf_counter() - t0) * 1000)
            extra: dict[str, Any] = {"op": op_name, "ms": ms}
            if log_result:
                extra["result"] = _safe_repr(result, max_arg_len)
            log.debug(f"<- {op_name}", extra=extra)
            return result

        return wrapper

    return decorator


__all__ = [
    "configure",
    "get_logger",
    "LogContext",
    "current_context",
    "snapshot",
    "new_request_id",
    "new_trace_id",
    "color",
    "json_field",
    "bytes_human",
    "ColoredString",
    "JsonFormatter",
    "TextFormatter",
    "TinyLogger",
    "BoundLogger",
    "TinyLogAsync",
    "SamplingHandler",
    "file_handler",
    "log_call",
    "log_call_async",
    "attach",
    "attach_async",
    "__version__",
    "structured",
]


def structured(
    data: dict[str, Any],
    *,
    level: str = "INFO",
    logger: "TinyLogger | BoundLogger | None" = None,
) -> None:
    """Log a structured dict as a JSON line.

    Useful for events, metrics, and audit logs that aren't "messages"
    but should still go through the same pipeline.
    """
    if logger is None:
        logger = get_logger("event")
    lvl = getattr(logging, level.upper(), logging.INFO)
    logger._log_at(lvl, "", extra={"_event": data})  # type: ignore[attr-defined]


# ─── v0.4.0 additions ────────────────────────────────────────────────────────

class MemoryHandler(logging.Handler):
    """In-memory ring-buffer log handler. Keeps last N records.

    Great for embedding recent context in error reports or for testing.
    """

    def __init__(self, capacity: int = 500, level: int = logging.NOTSET) -> None:
        super().__init__(level)
        self._capacity = capacity
        self._records: list[logging.LogRecord] = []
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        with self._lock:
            self._records.append(record)
            if len(self._records) > self._capacity:
                self._records.pop(0)

    def records(self, level: int | None = None) -> list[logging.LogRecord]:
        """Return all buffered records, optionally filtered by level."""
        with self._lock:
            if level is None:
                return list(self._records)
            return [r for r in self._records if r.levelno >= level]

    def dump(self, formatter: str = "%(levelname)s %(message)s") -> str:
        """Return all records as a formatted string."""
        out = []
        for r in self.records():
            # LogRecord.message is lazily computed; use getMessage() to
            # ensure %(message)s and other fields are populated reliably.
            d = {"message": r.getMessage(), **r.__dict__}
            out.append(formatter % d)
        return "\n".join(out)

    def last(self, n: int = 10, level: int | None = None) -> list[logging.LogRecord]:
        """Return the last n records."""
        recs = self.records(level)
        return recs[-n:]


import threading


class RateLimitHandler(logging.Handler):
    """Per-level rate-limiting handler. Fires at most N events per window seconds.

    Prevents log flooding from tight loops while ensuring every level
    gets through at least N events per window.
    """

    def __init__(
        self,
        limit_per_window: int = 10,
        window_sec: float = 60.0,
        level: int = logging.NOTSET,
    ) -> None:
        super().__init__(level)
        self._limit = limit_per_window
        self._window = window_sec
        self._counts: dict[int, list[float]] = {}
        self._lock = threading.Lock()
        self._dropped = 0
        self._total = 0

    def emit(self, record: logging.LogRecord) -> None:
        lvl = record.levelno
        now = time.monotonic()

        with self._lock:
            self._total += 1
            if lvl not in self._counts:
                self._counts[lvl] = []

            # Evict old timestamps
            cutoff = now - self._window
            self._counts[lvl] = [t for t in self._counts[lvl] if t > cutoff]

            if len(self._counts[lvl]) < self._limit:
                self._counts[lvl].append(now)
                super().emit(record)
            else:
                self._dropped += 1

    def stats(self) -> dict:
        with self._lock:
            return {
                "total": self._total,
                "dropped": self._dropped,
                "pass_rate": round(
                    (self._total - self._dropped) / max(self._total, 1) * 100, 2
                ),
            }


class MultiHandler(logging.Handler):
    """Fan-out handler — emits to multiple sub-handlers."""

    def __init__(self, *handlers: logging.Handler) -> None:
        super().__init__()
        self._handlers = list(handlers)

    def add(self, handler: logging.Handler) -> "MultiHandler":
        self._handlers.append(handler)
        return self

    def emit(self, record: logging.LogRecord) -> None:
        for h in self._handlers:
            try:
                h.emit(record)
            except Exception:
                self.handleError(record)

    def flush(self) -> None:
        for h in self._handlers:
            h.flush()

    def close(self) -> None:
        for h in self._handlers:
            h.close()
        super().close()
