"""Scaling regressions for incremental history bookkeeping."""

from __future__ import annotations

from time import perf_counter

import aimformat as aim

_BOT = aim.agent("history-scaling-benchmark")


def _bulk_add(count: int) -> float:
    doc = aim.new_document(title="history scaling")
    started = perf_counter()
    for i in range(count):
        doc.add_chunk(
            f'<p data-aim="c{i}">chunk {i}</p>',
            author=_BOT,
            at="2026-07-17T00:00:00Z",
        )
    return perf_counter() - started


def test_bulk_add_history_bookkeeping_scales() -> None:
    """Four times the work stays far below the old quadratic history curve.

    The absolute guard catches the measured 12--70 second pathology at 400
    adds. The ratio guard is machine-speed independent: origin/main takes
    roughly 49x longer for 400 adds than for 100, while the incremental path
    has a deliberately generous 14x budget for the remaining tree walks and
    timer noise (still below the 16x signature of quadratic growth).
    """
    small_elapsed = _bulk_add(100)
    large_elapsed = _bulk_add(400)
    ratio = large_elapsed / small_elapsed

    failures = []
    if large_elapsed >= 5.0:
        failures.append(f"400 adds took {large_elapsed:.3f}s (limit: 5.000s)")
    if ratio >= 14.0:
        failures.append(
            f"400/100 growth ratio was {ratio:.2f}x (limit: 14.00x; "
            f"small={small_elapsed:.3f}s, large={large_elapsed:.3f}s)"
        )
    assert not failures, "; ".join(failures)
