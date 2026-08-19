# tiny-log — Zero-Dependency Structured Logging

> **Like structlog/loguru, but in one file. Zero dependencies.**

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen.svg)](tiny_log.py)
[![Version](https://img.shields.io/badge/version-0.2.0-blue)](https://github.com/hussain-alsaibai/tiny-log)
[![Part of the tiny-* ecosystem](https://img.shields.io/badge/tiny--*-ecosystem-purple.svg)](#ecosystem)

`tiny-log` is a single-file structured logger. JSON or pretty-printed text, with `LogContext` for correlation IDs, bound loggers, file rotation, `log_call` / `log_call_async` timing helpers. Built on Python's stdlib `logging` — no `structlog`, no `loguru`, no `python-json-logger`.

## ✨ Features

- **🪶 Zero dependencies** — stdlib `logging` + `json` only
- **📦 Single file** — drop `tiny_log.py` anywhere
- **🧾 JSON or pretty text** — auto-detects TTY, or force either mode
- **🔗 Correlation IDs** — `LogContext` context manager for trace propagation
- **📌 Bound loggers** — `log.bind(service="api")` to attach permanent fields
- **🌀 File rotation** — `file_handler()` wrapper around stdlib `RotatingFileHandler`
- **⏱️ `log_call` & `log_call_async`** — time any sync or async function
- **🆔 Trace IDs** — `new_trace_id()` generates OpenTelemetry-compatible IDs
- **⚠️ Captures warnings** — `warnings` module gets piped to logging
- **🔧 Safe serialization** — `default=str` fallback for non-serializable objects

## 🚀 Quick Start

```python
from tiny_log import configure, get_logger, LogContext, new_request_id

configure(level="INFO")  # auto: JSON in pipes, text in TTY
log = get_logger("myapp").bind(service="ingest")

log.info("starting")
log.info("job done", extra={"count": 42})

with LogContext(request_id=new_request_id()):
    log.info("processing user", extra={"user_id": 7})
    # Output: {"ts":"...","level":"INFO","logger":"myapp",
    #          "msg":"processing user","ctx":{"request_id":"..."},
    #          "service":"ingest","user_id":7}
```

## 🧾 JSON vs Text

```python
configure(level="DEBUG", json=True)              # one JSON object per line
configure(level="DEBUG", json=False, color=True) # human-readable
configure(level="INFO", force_json=True)          # JSON even in TTY
```

In a TTY, default is `color=True` text. In a pipe/CI, default is JSON.

## 🔗 Context

```python
# Manual context
with LogContext(request_id="abc", user_id=42):
    log.info("step 1")

# With generated request ID
with LogContext.from_request_id():
    log.info("auto-id")

# Nested context (merges)
with LogContext(trace_id="abc"):
    with LogContext(span_id="def"):
        log.info("nested")
```

## 📌 Bound Loggers

```python
log = get_logger("svc").bind(service="api", region="us-east")
log.info("started")  # service and region auto-included in every message
```

## 🌀 File Rotation

```python
from tiny_log import file_handler

handler = file_handler("/var/log/myapp/app.log", max_bytes=50_000_000, backup_count=10)
logging.getLogger().addHandler(handler)
```

## ⏱️ Timing Helpers

```python
from tiny_log import log_call, log_call_async

# Sync
def fetch(url): return ...
result = log_call(log, "fetch", fetch, "https://api.example.com")

# Async
async def fetch_async(url): return ...
result = await log_call_async(log, "fetch", fetch_async, "https://api.example.com")
# Both log: "fetch ok" with op=fetch, ms=<duration>
```

## 🆔 Trace IDs

```python
from tiny_log import new_trace_id, LogContext

with LogContext(trace_id=new_trace_id()):
    log.info("traceable")
    # The trace_id (24 hex chars) is compatible with OpenTelemetry W3C trace context
```

## 📊 Comparison

| Feature | **tiny-log** | structlog | loguru |
|---|---|---|---|
| Dependencies | **0** | 0 (core) | 0 (but heavy) |
| File count | **1** | multiple | 1 |
| JSON output | ✅ | ✅ | ✅ |
| Bound fields | ✅ | ✅ | ✅ |
| Context vars | ✅ | ✅ | ✅ |
| File rotation | ✅ (stdlib) | needs config | ✅ |
| Time-it helper (sync + async) | ✅ | ❌ | ❌ |
| Trace IDs | ✅ | ❌ | ❌ |
| `force_json` TTY override | ✅ | ❌ | ❌ |

**Use `tiny-log` when** you want fast, clean, structured output and refuse to install another logging library for what Python's `logging` already does well.

## 🧪 Testing

```bash
python -m pytest test_tiny_log.py -v
```

## 🔧 API Reference

### Core

| Function | Description |
|---|---|
| `configure(level, json, color, stream, capture_warnings, force_json)` | Configure root logger |
| `get_logger(name)` | Get a TinyLogger instance |
| `LogContext(**values)` | Context manager that merges values into all log records |
| `LogContext.from_request_id(rid)` | Create context with a request_id |
| `current_context()` | Get current context dict |
| `new_request_id(length)` | Generate short unique ID |
| `new_trace_id()` | Generate OTel-compatible 24-char trace ID |
| `file_handler(path, level, json, max_bytes, backup_count)` | Rotating file handler factory |
| `log_call(logger, op, fn, *args, **kwargs)` | Time and log a sync function |
| `log_call_async(logger, op, fn, *args, **kwargs)` | Time and log an async function |

### Logging methods

`log.debug(msg, extra)`, `log.info(msg, extra)`, `log.warning(msg, extra)`, `log.error(msg, extra)`, `log.critical(msg, extra)`, `log.exception(msg, extra)`

### Bound logger

`log.bind(service="api")` returns a `BoundLogger` that merges these values into every call.

## Ecosystem

Part of the **tiny-*** zero-dependency toolkit for Python agent infrastructure. All single-file, MIT, fully type-hinted.

| Package | Description | Version |
|---|---|---|
| [**tiny-agent**](https://github.com/hussain-alsaibai/tiny-agent) | Zero-dep agent framework | ✅ |
| [**tiny-router**](https://github.com/hussain-alsaibai/tiny-router) | HTTP router | ✅ |
| [**tiny-validator**](https://github.com/hussain-alsaibai/tiny-validator) | Input validation | ✅ |
| [**tiny-config**](https://github.com/hussain-alsaibai/tiny-config) | Layered config loader | ✅ |
| [**tiny-cli**](https://github.com/hussain-alsaibai/tiny-cli) | CLI builder with colors | ✅ |
| [**fast-cache**](https://github.com/hussain-alsaibai/fast-cache) | LRU + TTL + SWR cache | ✅ |
| [**tiny-log**](https://github.com/hussain-alsaibai/tiny-log) | ✨ Structured logging | **0.2.0** |
| [**tiny-mcp**](https://github.com/hussain-alsaibai/tiny-mcp) | Model Context Protocol | ✅ |
| [**tiny-circuit**](https://github.com/hussain-alsaibai/tiny-circuit) | Circuit breaker, fault tolerance | ✅ |
| [**tiny-semaphore**](https://github.com/hussain-alsaibai/tiny-semaphore) | Async concurrency limiter | ✅ |
| [**tiny-rate-limiter**](https://github.com/hussain-alsaibai/tiny-rate-limiter) | Token bucket + sliding window | ✅ |
| [**snapdb**](https://github.com/hussain-alsaibai/snapdb) | Embedded in-memory DB | ✅ |
| [**tiny-metrics**](https://github.com/hussain-alsaibai/tiny-metrics) | Prometheus metrics | ✅ |
| [**tiny-cron**](https://github.com/hussain-alsaibai/tiny-cron) | Cron scheduler | ✅ |

23+ repos, ~8,000 LOC, zero dependencies across the entire stack.

Built by [OpenClaw](https://github.com/hussain-alsaibai).

## License

MIT — see [LICENSE](LICENSE).
