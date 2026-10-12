"""tiny_log v0.5.0 redaction bridge.

Optional integration with ``tiny_secret_mask``. The core ``tiny_log``
module has zero dependencies; this sidecar adds secret redaction hooks
that are only active when ``tiny_secret_mask`` is installed.

Public API
----------
- :func:`get_redactor` — return the active :class:`SecretMask` or a stub
  no-op if ``tiny_secret_mask`` is not installed.
- :func:`redact_value` — apply the redactor to a single value. Strings
  get text-level redaction; dicts/lists get structural redaction; everything
  else passes through unchanged.
- :func:`redact_payload` — recursively redact a log-record payload (the
  dict that comes from ``extra=`` plus the rendered message).
- :func:`install_json_redact_bridge` — call
  ``tiny_secret_mask.install_json_default()`` so the ``REDACT`` sentinel
  serialises as ``"[REDACTED]"`` inside the JSON formatter.
- :class:`RedactFilter` — a ``logging.Filter`` subclass that mutates
  every record's ``extra`` fields in-place before the formatter sees them.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping, Optional


# Lazy loader for tiny_secret_mask. The core tiny_log module keeps
# zero dependencies; the bridge is only activated if the user opts in
# via configure(redact=True).

_MASK: Any = None
_MASK_FAILED: bool = False


def _load_mask():
    """Load the SecretMask on first use. Caches the result.

    Returns None if tiny_secret_mask is not installed. After a load failure
    we set _MASK_FAILED=True so subsequent calls short-circuit instead of
    retrying the import and slowing down the log path.
    """
    global _MASK, _MASK_FAILED
    if _MASK is not None or _MASK_FAILED:
        return _MASK
    try:
        from tiny_secret_mask import SecretMask
        _MASK = SecretMask.default()
    except ImportError:
        _MASK = None
        _MASK_FAILED = True
    return _MASK



def get_redactor():
    """Return the active :class:`SecretMask` or None if unavailable."""
    return _load_mask()



def redact_value(value: Any, *, key: Optional[str] = None) -> Any:
    """Redact a single value. Strings and dicts/lists are walked; everything

    else passes through unchanged. If no redactor is available, returns
    the value untouched.

    When called via :func:`redact_payload`, the field name is forwarded
    as ``key=`` so a plain string under ``password`` or ``token``
    still gets redacted, even though the content does not match any
    regex. Content-based redaction is attempted first; key-based
    redaction is the fallback.
    """
    mask = _load_mask()
    if mask is None:
        return value
    if isinstance(value, str):
        content_redacted = mask.apply(value)
        if content_redacted == value and key is not None:
            try:
                masked = mask.mask_dict({key: value})
            except Exception:  # pragma: no cover - defensive
                masked = {}
            if masked.get(key) != value:
                return masked[key]
        return content_redacted
    if isinstance(value, Mapping):
        return mask.mask_dict(value)
    if isinstance(value, list):
        return [
            mask.mask_dict(x) if isinstance(x, Mapping)
            else redact_value(x, key=key) if isinstance(x, str)
            else x
            for x in value
        ]
    return value



def redact_payload(payload: Mapping[str, Any]) -> dict:
    """Return a copy of *payload* with every value redacted.

    The result is always a fresh dict; the input is never mutated. Nested
    dicts and lists are walked recursively. Strings are passed through the
    text-level redactor (catches JWTs, bearer tokens, etc.); dicts are
    passed through ``mask_dict`` (catches sensitive keys). The string
    ``"msg"`` field is *not* redacted here — log messages typically have
    placeholders, and a separate ``safe_log()`` helper handles the
    post-format redaction.

    Field names are forwarded to :func:`redact_value` so a plain string
    under a sensitive key (``password``, ``token``, ...) is still
    redacted, even if the value does not match a content regex.
    """
    out: dict = {}
    for k, v in payload.items():
        out[k] = redact_value(v, key=k)
    return out



def install_json_redact_bridge() -> bool:
    """Bridge the REDACT sentinel into stdlib :mod:`json`.

    Returns True if the bridge was installed, False if tiny_secret_mask
    is not available. Idempotent — safe to call multiple times.
    """
    try:
        from tiny_secret_mask import install_json_default
    except ImportError:
        return False
    return bool(install_json_default())



class RedactFilter(logging.Filter):
    """``logging.Filter`` that redacts ``record.__dict__`` extras in place.

    Attach it to any handler via ``handler.addFilter(RedactFilter())`` to
    get automatic redaction without changing the formatter. The filter
    is a no-op if ``tiny_secret_mask`` is not installed.
    """

    def __init__(self, name: str = "") -> None:
        super().__init__(name)
        self._mask = _load_mask()

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        if self._mask is None:
            return True
        for key, value in list(record.__dict__.items()):
            if key.startswith("_"):
                continue
            if isinstance(value, Mapping):
                # Whole-dict redaction: walk every key through mask_dict.
                try:
                    record.__dict__[key] = self._mask.mask_dict(value)
                except Exception:  # pragma: no cover - defensive
                    pass
            elif isinstance(value, str):
                # String values: try content-based redaction first
                # (catches JWTs, bearer tokens, AWS keys, etc.). Only if
                # the content is unchanged AND the key name is a known
                # sensitive field (e.g. "password", "token"), fall back
                # to key-based redaction via mask_dict.
                content_redacted = self._mask.apply(value)
                if content_redacted == value:
                    try:
                        masked = self._mask.mask_dict({key: value})
                    except Exception:  # pragma: no cover - defensive
                        masked = {}
                    if masked.get(key) != value:
                        content_redacted = masked[key]
                record.__dict__[key] = content_redacted
        return True



def safe_log(
    logger: logging.Logger,
    level: int,
    msg: str,
    *args: Any,
    extra: Optional[Mapping[str, Any]] = None,
    redact: bool = True,
) -> None:
    """Log a record with optional post-format redaction of the message.

    Unlike ``logger.info(msg, *args, extra=...)``, this helper redacts the
    *rendered* message string before it reaches the formatter, so any
    interpolated value is also scrubbed. The ``extra=`` mapping is
    always run through :func:`redact_payload` regardless of ``redact``,
    so the field-level redaction still works when the message itself
    passes through untouched.
    """
    rendered = msg % args if args else msg
    safe_extra = redact_payload(extra) if extra else {}
    if redact:
        mask = _load_mask()
        if mask is not None:
            rendered = mask.apply(rendered)
    logger.log(level, rendered, extra=safe_extra)
