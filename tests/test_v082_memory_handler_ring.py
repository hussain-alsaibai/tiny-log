"""v0.8.2 regression: MemoryHandler must evict in O(1), not O(capacity).

``MemoryHandler`` is the "keep the last N records for the error report"
handler -- exactly the thing that runs under a log flood, which is when
logging must be cheapest. It held a plain list and called ``pop(0)`` on
every record past capacity, shifting the whole buffer each time. Sustained
logging was therefore O(capacity) per record: quadratic in total.

Measured before the fix (200k emits, one pre-built record):

    capacity    1,000  ->  0.07s
    capacity   50,000  ->  1.41s      (20x for 50x the buffer)

After: capacity 50,000 -> 0.04s.

The timing assertion below is deliberately loose -- it fails on the old
list/pop(0) shape by an order of magnitude, and cannot flake on a slow CI
box the way a tight bound would.
"""

from __future__ import annotations

import logging
import time

import pytest

from tiny_log import MemoryHandler


def _record(msg: str = "m") -> logging.LogRecord:
    return logging.LogRecord("n", logging.INFO, "p", 1, msg, None, None)


def test_eviction_is_linear_in_records_not_capacity():
    """Doubling capacity must not double the cost of a fixed emit count.

    The quadratic term only dominates once the buffer is *repeatedly*
    refilled, so the emit count is several times the large capacity.
    """
    rec = _record()

    def run(capacity: int, emits: int) -> float:
        h = MemoryHandler(capacity=capacity)
        start = time.perf_counter()
        for _ in range(emits):
            h.emit(rec)
        return time.perf_counter() - start

    small = run(500, 60_000)
    large = run(50_000, 600_000)

    # With list.pop(0) the large run is ~30-60x slower. A correct ring
    # buffer costs the same per emit, so normalise by emit count and allow
    # a generous 4x for cache/timer noise.
    small_per = small / 60_000
    large_per = large / 600_000
    assert large_per < small_per * 4, (
        f"per-emit cost scales with capacity: {small_per*1e6:.3f}us at 500 "
        f"vs {large_per*1e6:.3f}us at 50000"
    )


def test_retains_exactly_capacity():
    h = MemoryHandler(capacity=10)
    for i in range(100):
        h.emit(_record(str(i)))
    recs = h.records()
    assert len(recs) == 10
    # The newest 10 survive, in order.
    assert [r.getMessage() for r in recs] == [str(i) for i in range(90, 100)]


def test_under_capacity_keeps_everything():
    h = MemoryHandler(capacity=100)
    for i in range(7):
        h.emit(_record(str(i)))
    assert len(h.records()) == 7


def test_zero_capacity_is_rejected():
    """A 0-capacity buffer silently dropped every record before."""
    with pytest.raises(ValueError, match="capacity must be positive"):
        MemoryHandler(capacity=0)


def test_capacity_property_matches_construction():
    assert MemoryHandler(capacity=42).capacity == 42


def test_level_filter_and_last_still_work():
    h = MemoryHandler(capacity=5)
    h.emit(logging.LogRecord("n", logging.WARNING, "p", 1, "warn", None, None))
    h.emit(logging.LogRecord("n", logging.ERROR, "p", 1, "err", None, None))
    assert [r.getMessage() for r in h.records(logging.ERROR)] == ["err"]
    assert len(h.last(1)) == 1


def test_dump_unaffected_by_buffer_type():
    h = MemoryHandler(capacity=3)
    lg = logging.getLogger("tiny_log_test_dump")
    lg.setLevel(1)
    lg.propagate = False
    lg.addHandler(h)
    try:
        lg.error("user %s failed", "bob")
        assert h.dump() == "ERROR user bob failed"
    finally:
        lg.removeHandler(h)
        h.close()
