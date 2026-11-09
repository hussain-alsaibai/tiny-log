# tiny-log — Zero-Dependency Structured Logging
[![CI](https://github.com/hussain-alsaibai/tiny-log/actions/workflows/ci.yml/badge.svg)](https://github.com/hussain-alsaibai/tiny-log/actions/workflows/ci.yml)

> **Like structlog/loguru, but in one file. Zero dependencies.**

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen.svg)](tiny_log.py)
[![Part of the tiny-* ecosystem](https://img.shields.io/badge/tiny--*-ecosystem-purple.svg)](#ecosystem)
[![Version](https://img.shields.io/badge/version-0.6.0-blue)](https://github.com/hussain-alsaibai/tiny-log)
[![CI](https://github.com/hussain-alsaibai/tiny-log/actions/workflows/ci.yml/badge.svg)](https://github.com/hussain-alsaibai/tiny-log/actions/workflows/ci.yml)

Single-file structured logger. JSON or pretty-printed text, with `LogContext` for
correlation IDs, bound loggers, file rotation, `log_call` / `log_call_async` timing
helpers, and an **opt-in secret redaction bridge** (v0.5.0) that scrubs credentials
out of every log line before it hits a sink or an LLM context window.

## ✨ Features

- **🪶 Zero dependencies** — stdlib `logging` + `json` only
- **📦 Single file** — drop `tiny_log.py` anywhere
- **🧾 JSON or pretty text** — auto-detects TTY, or force either mode
- **🔗 Correlation IDs** — `LogContext` context manager for trace propagation
- **📌 Bound loggers** — `log.bind(service="api")` to attach permanent fields
- **🌀 File rotation** — `file_handler()` wrapper around stdlib `RotatingFileHandler`
- **⏱️ `log_call` & `log_call_async`** — time any sync or async function
- **⚡ `TinyLogAsync`** — async `ainfo`/`aerror`/`awarning`/`adebug` via `asyncio.to_thread`
- **🎚️ `SamplingHandler`** — downsample noisy paths
- **🌈 `ColoredString`** — ANSI-coloured log fields, preserved in JSON output
- **🔒 `configure(redact=True)` (v0.5.0)** — auto-redact credentials, JWTs, and PII
  via the [tiny-secret-mask](https://github.com/hussain-alsaibai/tiny-secret-mask) bridge
- **🧵 W3C trace context (v0.6.0)** — `TraceContext` binds `trace_id` / `span_id` /
  `flags` / `tracestate` to the log scope, and `as_headers()` returns the
  `traceparent` that continues the *same* trace downstream

## 🚀 Quick start

```python
from tiny_log import get_logger, configure

configure(level="INFO", json=False, redact=True)  # redact=True needs tiny-secret-mask
log = get_logger("myapp")

log.info("login", extra={"user": "alice", "password": "hunter2"})
# -> "login  user=alice  password=[REDACTED:password]"
```

`extra=` fields render in **both** formats — text and JSON carry the same
fields for the same call. Strings stay unquoted in text mode, everything else
is `repr`'d so `None` and `"None"` are distinguishable. Turn it off with
`TextFormatter(extras=False)`.

Install the optional bridge:

```bash
# From GitHub (recommended -- not yet on PyPI)
pip install git+https://github.com/hussain-alsaibai/tiny-log.git
```

The redaction bridge is optional and lives in a separate package:

```bash
pip install git+https://github.com/hussain-alsaibai/tiny-secret-mask.git
```

### `redact=True` fails closed (v0.7.0)

Passing `redact=True` is a statement that logs must not contain credentials.
Before v0.7.0 the bridge import was wrapped in `except ImportError: pass`, so a
container image that had lost `tiny-secret-mask` in a rebuild returned a
perfectly working logger that wrote passwords and JWTs in the clear. Nothing
raised, nothing warned, and the logs looked normal.

Now the call raises:

```python
>>> configure(redact=True)
Traceback (most recent call last):
    ...
tiny_log.RedactionUnavailable: configure(redact=True) requires the redaction
bridge from tiny_log_v050, which could not be imported (No module named
'tiny_secret_mask'). Install 'tiny-secret-mask>=0.4.0', or call
configure(redact=False) if unredacted logs are what you want.
```

`configure(redact=False)` is unchanged — omitting redaction is a valid choice,
you just have to say it. Same rule as `tiny-router` and `tiny-route-redact`:
a redaction layer that cannot redact must refuse.

## 🧵 W3C trace context (v0.6.0)

```python
from tiny_log import TraceContext, get_logger

log = get_logger("api")

with TraceContext() as tc:
    log.info("handling request")   # carries trace_id + span_id
    headers = tc.as_headers()      # traceparent for the downstream call
    # headers["traceparent"] -> "00-<this trace>-<this span>-01"

# Continue a trace that arrived from upstream:
with TraceContext.from_headers(incoming_headers) as tc:
    log.info("continuing")
```

`as_headers()` carries the context's own trace id, so the downstream service
stays in the same trace instead of starting a new one.

### Nested spans

Two blocks opened under one root both report the *root* span as their caller,
so a trace viewer draws them as siblings. When the nesting is real, say so:

```python
with TraceContext.from_headers(incoming) as root:
    log.info("request")                 # parent_span_id == <upstream span>
    with root.child() as db:
        log.info("query")               # parent_span_id == root.span_id
        with db.child() as retry:
            log.info("retry")           # parent_span_id == db.span_id
```

`child()` keeps the trace id, the sampling flags and the `tracestate`, and
mints only the span id. Nothing becomes active until the `with` block opens,
so building a child ahead of time is safe.

## 🧩 Trace context injection into log records (otel)

The v0.6.0 `TraceContext` above carries a trace through your code. This module
injects `trace_id` and `span_id` into every `LogRecord`, so formatters can
pick them up with `%(trace_id)s`:

```python
import logging
from otel import TraceContextFilter, bind_trace_context, reset_trace_context

handler = logging.StreamHandler()
handler.addFilter(TraceContextFilter())
handler.setFormatter(logging.Formatter("%(trace_id)s %(span_id)s %(message)s"))

tokens = bind_trace_context(trace_id="abc123", span_id="def456")
try:
    logging.getLogger("api").info("handling request")
    # -> abc123 def456 handling request
finally:
    reset_trace_context(tokens)
```

The provider is pluggable, so trace context can come from somewhere other
than `contextvars`:

```python
class MyProvider:
    def current(self):
        return {"trace_id": ..., "span_id": ...}

handler.addFilter(TraceContextFilter(MyProvider()))
```

Stdlib only - no `opentelemetry` package required.

## 🧪 Tests

```bash
cd tiny-log
python3 -m pytest -q
```

106 tests, stdlib only:

| File | Tests | Covers |
|------|-------|--------|
| `test_tiny_log.py` | 17 | core logger, formatters, `LogContext`, `bind` |
| `test_tiny_log_v030.py` | 39 | async logging, sampling, rotation |
| `tests/test_tiny_log_v050.py` | 16 | secret-redaction bridge |
| `tests/test_tiny_log_v052_py38.py` | 4 | Python 3.8 compatibility (`asyncio.to_thread` is 3.9+) |
| `tests/test_tiny_log_v060_trace.py` | 28 | W3C trace context, `traceparent` parsing and injection, nested child spans |
| `test_version_consistency.py` | 2 | `pyproject.toml` version matches `tiny_log.__version__` |

CI runs the suite on Python 3.8 through 3.12.
