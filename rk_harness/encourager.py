"""Encourager — HANDOFF §8; SPEC §Surface/encourager.

A pure function of (RunState, ArchiveState, now).  Calendar rules are checked
first and are absolute: never PACKAGE/FREEZE before 2026-11-20, PACKAGE on/after
it, FREEZE on/after 2026-12-05.  Then a stall-counter ladder.
"""
from __future__ import annotations

import datetime
import math

from rk_harness.types import Action, ArchiveState, RunState

PACKAGE_DATE = datetime.date(2026, 11, 20)
FREEZE_DATE = datetime.date(2026, 12, 5)

_ORDER_BY_PHASE = {0: 2, 1: 3, 2: 4, 3: 4}
_STAGES = (2, 3, 4, 5, 6)
_BUCKETS = (0, 1, 2, 3, 4, 5, 6, 7)
_MAX_STAGES = 6
_DYADIC_DENOMINATOR_MAX = 32768


def _finite(x: object) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)


def _all_elites(arch: ArchiveState):
    for order in sorted(arch.grids):
        for key in sorted(arch.grids[order]):
            yield arch.grids[order][key]


def emptiest_cell(arch: ArchiveState, order: int) -> tuple[int, int]:
    grid = arch.grids.get(order, {})
    for s in _STAGES:
        for b in _BUCKETS:
            if (s, b) not in grid:
                return (s, b)
    # Grid full: the cell whose elite has the highest heldout_error (first on ties).
    worst_key: tuple[int, int] | None = None
    worst_val = -math.inf
    for s in _STAGES:
        for b in _BUCKETS:
            v = grid[(s, b)].score.heldout_error
            if worst_key is None or v > worst_val:
                worst_key = (s, b)
                worst_val = v
    return worst_key if worst_key is not None else (2, 0)


def heldout_gap(arch: ArchiveState) -> float:
    total = 0.0
    n = 0
    for rec in _all_elites(arch):
        h = rec.score.heldout_error
        s = rec.score.search_error
        if not (_finite(h) and _finite(s)):
            continue
        total += h - s
        n += 1
    return total / n if n else 0.0


def _mean_search_error(arch: ArchiveState) -> float:
    total = 0.0
    n = 0
    for rec in _all_elites(arch):
        s = rec.score.search_error
        if not _finite(s):
            continue
        total += s
        n += 1
    return total / n if n else 0.0


def _widen_stages(arch: ArchiveState, order: int) -> list[int]:
    seen = sorted({s for (s, _b) in arch.grids.get(order, {})})
    if not seen:
        seen = [emptiest_cell(arch, order)[0]]
    widened = sorted({min(s + 1, _MAX_STAGES) for s in seen})
    return widened[-3:]


def next_action(state: RunState, arch: ArchiveState, now: datetime.datetime) -> Action:
    today = now.date()
    if today >= FREEZE_DATE:
        return Action("FREEZE", {"date": today.isoformat()})
    if today >= PACKAGE_DATE:
        return Action("PACKAGE", {"date": today.isoformat()})

    st = state.stall_counter
    p = state.phase
    order = _ORDER_BY_PHASE.get(p, 4)

    if st >= 10 and st % 10 == 0:
        gap = heldout_gap(arch)
        if gap > 2.0 * _mean_search_error(arch):
            return Action("ROTATE_PROBLEMS", {"gap": gap})

    if st < 5:
        cell = tuple(state.current_cell) if state.current_cell else emptiest_cell(arch, order)
        return Action("SEARCH_CELL", {"order": order, "cell": cell})
    if st < 10:
        return Action("SEARCH_CELL", {"order": order, "cell": emptiest_cell(arch, order)})
    if st < 20:
        return Action("WIDEN", {
            "order": order,
            "stages": _widen_stages(arch, order),
            "dyadic_denominator_max": _DYADIC_DENOMINATOR_MAX,
        })
    if st < 30:
        return Action("HYPOTHESIZE", {"order": order})
    if p < 3:
        return Action("ADVANCE_PHASE", {"from": p, "to": min(p + 1, 3)})
    return Action("HYPOTHESIZE", {"order": order})
