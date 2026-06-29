# tiny-log — Zero-Dependency Structured Logging

> **Like structlog/loguru, but in one file. Zero dependencies.**

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen.svg)](tiny_log.py)
[![Part of the tiny-* ecosystem](https://img.shields.io/badge/tiny--*-ecosystem-purple.svg)](#ecosystem)

`tiny-log` is a single-file structured logger. JSON or pretty-printed text, with `LogContext` for correlation IDs, bound loggers, file rotation, and a `log_call` timing helper. Built on Python's stdlib `logging` — no `structlog`, no `loguru`, no `python-json-logger`.

## ✨ Features

- **🪶 Zero dependencies** — stdlib `logging` + `json` only
- **📦 Single file** — drop `tiny_log.py` anywhere
- **🧾 JSON or pretty text** — auto-detects TTY
- **🔗 Correlation IDs** — `LogContext` context manager
- **📌 Bound loggers** — `log.bind(service="api")` to attach permanent fields
- **🌀 File rotation** — `RotatingFileHandler` wrapper, 5 backups
- **⏱️ `log_call`** — time and log any function call
- **⚠️ Captures warnings** — `warnings` module gets piped in

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
configure(level="DEBUG", json=True)   # one JSON object per line
configure(level="DEBUG", json=False, color=True)  # human-readable
```

In a TTY, default is `color=True` text. In a pipe/CI, default is JSON.

## 🔗 Context

```python
with LogContext(request_id="abc", user_id=42):
    log.info("step 1")
    with LogContext(retry=2):  # nested
        log.warning("retrying")
```

## 📌 Bound Loggers

```python
log = get_logger("svc").bind(service="api", region="us-east")
log.info("started")  # service and region auto-included
```

## 🌀 File Rotation

```python
from tiny_log import file_handler
handler = file_handler("/var/log/myapp/app.log", max_bytes=50_000_000, backup_count=10)
logging.getLogger().addHandler(handler)
```

## ⏱️ Timing Helper

```python
from tiny_log import log_call

def fetch(url): return ...

result = log_call(log, "fetch", fetch, "https://api.example.com")
# logs: "fetch ok" with op=fetch, ms=124
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
| Time-it helper | ✅ | ❌ | ❌ |

**Use `tiny-log` when** you want fast, clean, structured output and refuse to install another logging library for what Python's `logging` already does well.

## 🧪 Testing

```bash
python test_tiny_log.py -v
```

## Ecosystem

Part of the **tiny-*** zero-dependency toolkit:

- [tiny-router](https://github.com/hussain-alsaibai/tiny-router) — HTTP routing
- [tiny-log](https://github.com/hussain-alsaibai/tiny-log) — Logging (this repo)
- [tiny-validator](https://github.com/hussain-alsaibai/tiny-validator) — Input validation
- [tiny-cli](https://github.com/hussain-alsaibai/tiny-cli) — CLI parser
- [tiny-config](https://github.com/hussain-alsaibai/tiny-config) — Config loader
- [fast-cache](https://github.com/hussain-alsaibai/fast-cache) — In-memory cache
- [tiny-mcp](https://github.com/hussain-alsaibai/tiny-mcp) — MCP server
- [tiny-agent](https://github.com/hussain-alsaibai/tiny-agent) — Agent framework
- [tiny-embed](https://github.com/hussain-alsaibai/tiny-embed) — Embeddings
- [snapdb](https://github.com/hussain-alsaibai/snapdb) — Embedded DB

## License

MIT — see [LICENSE](LICENSE).
