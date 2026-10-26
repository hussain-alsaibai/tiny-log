"""Regression tests for tiny-log v0.5.2 -- Python 3.8 compatibility.

``asyncio.to_thread`` was added in Python 3.9. tiny-log declares
``requires-python = ">=3.8"`` and runs CI on 3.8, so the async wrapper
must not depend on it. These tests simulate a 3.8 interpreter by hiding
``asyncio.to_thread`` and forcing the ``run_in_executor`` fallback.
"""

from __future__ import annotations

import asyncio
import io
import logging

import tiny_log


def _logger_with_buffer(name):
    """Build a TinyLogger whose output lands in a StringIO buffer."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(message)s"))
    base = logging.getLogger(name)
    base.handlers = [handler]
    base.setLevel(logging.DEBUG)
    base.propagate = False
    return tiny_log.TinyLogger(base), buf


def test_run_in_thread_falls_back_when_to_thread_missing(monkeypatch):
    """The 3.8 fallback must work when ``asyncio.to_thread`` is absent."""
    monkeypatch.delattr(asyncio, "to_thread", raising=False)

    async def run():
        return await tiny_log._run_in_thread(lambda a, b: a + b, 2, 3)

    assert asyncio.run(run()) == 5


def test_run_in_thread_uses_to_thread_when_available(monkeypatch):
    """On 3.9+ the native helper must be used, not the fallback."""
    calls = []

    def fake_to_thread(fn, *args, **kwargs):
        calls.append(fn)
        return asyncio.sleep(0, result=fn(*args, **kwargs))

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread, raising=False)

    async def run():
        return await tiny_log._run_in_thread(lambda: "ok")

    assert asyncio.run(run()) == "ok"
    assert len(calls) == 1


def test_async_logger_works_without_to_thread(monkeypatch):
    """The public TinyLogAsync API must not need ``asyncio.to_thread``."""
    monkeypatch.delattr(asyncio, "to_thread", raising=False)

    logger, buf = _logger_with_buffer("tiny-log-test38")

    async def run():
        alog = tiny_log.TinyLogAsync(logger)
        await alog.ainfo("hello from 3.8", extra={"k": "v"})

    asyncio.run(run())
    assert "hello from 3.8" in buf.getvalue()


def test_async_logger_works_with_to_thread():
    """Sanity check that the normal 3.9+ path still logs."""
    logger, buf = _logger_with_buffer("tiny-log-test39")

    async def run():
        alog = tiny_log.TinyLogAsync(logger)
        await alog.ainfo("hello from 3.9")

    asyncio.run(run())
    assert "hello from 3.9" in buf.getvalue()
