"""Micro-benchmark for tiny_log throughput."""

from __future__ import annotations

import logging
import time

from tiny_log import JsonFormatter, configure, get_logger


def main() -> None:
    import io

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    for h in [x for x in root.handlers if getattr(x, "_tiny_log", False)]:
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    log = get_logger("bench")

    iters = 50_000
    t0 = time.perf_counter()
    for i in range(iters):
        log.info("hello", extra={"i": i, "user_id": i % 1000})
    elapsed = time.perf_counter() - t0
    print(f"{iters} logs in {elapsed:.3f}s → {iters / elapsed:,.0f} logs/s")
    print(f"output size: {len(buf.getvalue())} bytes")


if __name__ == "__main__":
    main()
