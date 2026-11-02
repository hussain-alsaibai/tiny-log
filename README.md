# tiny-log — Zero-Dependency Structured Logging
[![CI](https://github.com/hussain-alsaibai/tiny-log/actions/workflows/ci.yml/badge.svg)](https://github.com/hussain-alsaibai/tiny-log/actions/workflows/ci.yml)

> **Like structlog/loguru, but in one file. Zero dependencies.**

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen.svg)](tiny_log.py)
[![Part of the tiny-* ecosystem](https://img.shields.io/badge/tiny--*-ecosystem-purple.svg)](#ecosystem)
[![Version](https://img.shields.io/badge/version-0.5.2-blue)](https://github.com/hussain-alsaibai/tiny-log)
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

## 🚀 Quick start

```python
from tiny_log import get_logger, configure

configure(level="INFO", json=False, redact=True)  # redact=True needs tiny-secret-mask
log = get_logger("myapp")

log.info("login", extra={"user": "alice", "password": "hunter2"})
# -> "login  user=alice  password=[REDACTED:password]"
```

Install the optional bridge:

```bash
# From GitHub (recommended -- not yet on PyPI)
pip install git+https://github.com/hussain-alsaibai/tiny-log.git
```

The redaction bridge is optional and lives in a separate package:

```bash
pip install git+https://github.com/hussain-alsaibai/tiny-secret-mask.git
```

## 🧪 Tests

```bash
cd tiny-log
python3 -m pytest -q
```

78 tests, stdlib only:

| File | Tests | Covers |
|------|-------|--------|
| `test_tiny_log.py` | 17 | core logger, formatters, `LogContext`, `bind` |
| `test_tiny_log_v030.py` | 39 | async logging, sampling, rotation |
| `tests/test_tiny_log_v050.py` | 16 | secret-redaction bridge |
| `tests/test_tiny_log_v052_py38.py` | 4 | Python 3.8 compatibility (`asyncio.to_thread` is 3.9+) |
| `test_version_consistency.py` | 2 | `pyproject.toml` version matches `tiny_log.__version__` |

CI runs the suite on Python 3.8 through 3.12.
