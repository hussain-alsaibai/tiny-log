# Changelog

All notable changes to tiny-log are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.5.2] — 2026-10-26

### Fixed
- `TinyLogAsync` no longer crashes on Python 3.8. The async wrapper called
  `asyncio.to_thread`, which was only added in **Python 3.9**, while the
  package declares `requires-python = ">=3.8"` and CI runs on 3.8. Every
  `TinyLogAsync` call raised `AttributeError: module 'asyncio' has no
  attribute 'to_thread'`. The new `_run_in_thread()` helper uses
  `asyncio.to_thread` when available and falls back to the equivalent
  `loop.run_in_executor(None, ...)` call on 3.8.

### Added
- `tests/test_tiny_log_v052_py38.py` — regression coverage that hides
  `asyncio.to_thread` to simulate a 3.8 interpreter. Verified to fail
  against the pre-fix code (3 failed) and pass after (4 passed).

### Changed
- CI now runs the new test file.
- Bumped `__version__` and `pyproject.toml` to `0.5.2`.

## [0.5.1] — 2026-10-12

### Fixed
- `redact_value(v, key=k)` now also accepts an optional `key=` so
  `redact_payload` can forward the field name. Without the key, a
  plain string under ``password``/``token``/``secret``/``api_key`` was
  not being redacted because content-only rules look for JWTs and
  bearers, not for opaque short strings.
- `RedactFilter.filter` now uses the same content-first, key-fallback
  strategy. A string extra like ``password="hunter2"`` is now masked
  to ``"[REDACTED:password]"``; a string extra that already contains a
  JWT/bearer/AWS-key is still masked by content.
- Test fixtures updated to use realistic secret patterns (a full
  ``eyJ…`` JWT) instead of the placeholder ``abc.def.ghi`` that the
  default `tiny_secret_mask` rules do not match.

### Notes
- 16/16 v0.5.x tests pass; total suite 38/38.
- No API break. `redact_value(v)` still works without `key=`.

## [0.5.0] — 2026-10-05

### Added
- `configure(redact=True)` — opt-in secret redaction bridge via
  `tiny_secret_mask` (v0.2.0+). When enabled, every handler gets a
  `RedactFilter` that scrubs string extras and dict/lists extras before
  the formatter sees them. The JSON formatter also gets the
  `install_json_default()` bridge so `REDACT` sentinel values
  serialise as `"[REDACTED]"`.
- New module `tiny_log_v050.py` (sidecar, zero required deps):
  - `get_redactor()` — returns the active `SecretMask` or None
    if `tiny_secret_mask` is not installed.
  - `redact_value(v)` — single-value redaction (string, dict, list, or
    passthrough).
  - `redact_payload(payload)` — recursive payload redaction.
  - `install_json_redact_bridge()` — bridge to `json.JSONEncoder`.
  - `RedactFilter` — `logging.Filter` subclass that mutates record
    extras in place.
  - `safe_log(logger, level, msg, *args, extra=..., redact=True)` —
    drop-in replacement for `logger.log()` that scrubs the rendered
    message (catches interpolated secrets).
- 18 new tests in `tests/test_tiny_log_v050.py` covering payload
  redaction, the filter, the JSON bridge, and the safe_log helper.

### Changed
- Bumped `__version__` to `0.5.1`.
- `configure()` now accepts `redact: bool = False` and
  `redact_keys: list | None = None` keyword arguments. Both default
  to off, so existing callers see no behaviour change.
- Added optional `redact` extra (`pip install tiny-log[redact]`) that
  pulls in `tiny-secret-mask`.

### Notes
- `tiny_log` core stays at **zero dependencies**. The redaction
  bridge is a sidecar file; `configure(redact=True)` only does work
  if `tiny_secret_mask` is importable.
- The `redact_keys` parameter is accepted but currently a no-op
  (reserved for v0.5.1). Use the default key heuristic via
  `SecretMask.mask_dict()` for now.
- Combined suite is now 38 tests (20 v0.4.0 + 18 v0.5.0).

## [0.4.0] — 2026-09-08

### Added
- Async logging via `TinyLogAsync`.
- Sampling handler (`sample_rate=...`).
- `log_call` / `log_call_async` decorators.
- Field formatters (`color()`, `json_field()`, `bytes_human()`).
- `ColoredString` for ANSI-coloured log fields.

## [0.3.0] — 2026-08-04

### Added
- Async logging primitives, sampling handler, attach decorator,
  field formatters.

## [0.2.0] — 2026-07-10

### Added
- `LogContext` for correlation IDs, bound loggers,
  `log.bind()`, file rotation wrapper.

## [0.1.0] — 2026-06-25

### Added
- Initial release: `configure`, `get_logger`, `JsonFormatter`,
  `TextFormatter`, `LogContext`.
