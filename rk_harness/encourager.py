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

# Stages an explicit Runge-Kutta method needs to reach an order. Orders 1 to 4 need one
# stage each; order 5 is the Butcher barrier at 6. archive.record_order caps the recorded
# order at 4, so only the first four entries are reachable today. The barrier is written
# down anyway, because the scan below is the one place where getting it wrong is silent.
_MIN_STAGES: dict[int, int] = {1: 1, 2: 2, 3: 3, 4: 4, 5: 6, 6: 7, 7: 9, 8: 11}


def min_stages_for_order(order: int) -> int:
    return _MIN_STAGES.get(int(order), int(order))


def stage_domain(order: int) -> tuple[int, ...]:
    """The stage counts worth searching for `order`.

    Scanning stage 2 for an order-4 grid asks for a cell that cannot exist, so the scan
    returns it on every call forever and the search spends every cycle re-proposing
    tableaus it has already archived. Never empty: an order past the largest searchable
    stage count falls back to that stage count.
    """
    lo = min_stages_for_order(order)
    dom = tuple(s for s in _STAGES if s >= lo)
    return dom or (_STAGES[-1],)
_DYADIC_DENOMINATOR_MAX = 32768


def _finite(x: object) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)


def _all_elites(arch: ArchiveState):
    for order in sorted(arch.grids):
        for key in sorted(arch.grids[order]):
            yield arch.grids[order][key]


POLICIES: tuple[str, ...] = ("empty", "revisit")


def cell_policy(policy: str) -> str:
    """The cell-targeting half of a run policy name.

    The runner's policy names carry two independent choices: which cell to aim at
    ("empty" or "revisit") and whether CMA-ES warm starts from that cell's incumbent
    ("warm"). Only the first half concerns this module, so "revisit+warm" targets like
    "revisit" and anything unrecognised targets like "empty" (the shipped default).
    """
    return "revisit" if "revisit" in str(policy or "").lower() else "empty"


def _heldout_of(rec) -> float:
    try:
        return float(rec.score.heldout_error)
    except (AttributeError, TypeError, ValueError):
        return math.nan


def revisit_cell(arch: ArchiveState, order: int) -> tuple[int, int] | None:
    """The occupied cell worth returning to: highest finite held-out error, earliest on ties.

    None when nothing in stage_domain(order) is occupied, which is the caller's signal that
    there is nothing to revisit and an empty cell has to be chosen instead. A cell whose
    elite carries a non-finite held-out error cannot be ranked, so it is passed over unless
    it is the only kind of cell there is.
    """
    grid = arch.grids.get(order, {}) if arch is not None and arch.grids else {}
    domain = stage_domain(order)
    first_occupied: tuple[int, int] | None = None
    worst_key: tuple[int, int] | None = None
    worst_val = -math.inf
    for s in domain:
        for b in _BUCKETS:
            rec = grid.get((s, b))
            if rec is None:
                continue
            if first_occupied is None:
                first_occupied = (s, b)
            v = _heldout_of(rec)
            if _finite(v) and v > worst_val:
                worst_key = (s, b)
                worst_val = v
    return worst_key if worst_key is not None else first_occupied


def emptiest_cell(arch: ArchiveState, order: int) -> tuple[int, int]:
    grid = arch.grids.get(order, {})
    domain = stage_domain(order)
    for s in domain:
        for b in _BUCKETS:
            if (s, b) not in grid:
                return (s, b)
    # Grid full: there is no empty cell left, so the worst occupied one is the target.
    return revisit_cell(arch, order) or (domain[0], 0)


def target_cell(arch: ArchiveState, order: int, policy: str = "empty") -> tuple[int, int]:
    """The cell a search should aim at under `policy`.

    Pure: the policy is an argument, never an environment read, so a caller that wants the
    shipped behaviour asks for it by name. "revisit" falls back to the emptiest cell while
    the grid still holds nothing, because there is no incumbent to return to.
    """
    if cell_policy(policy) == "revisit":
        return revisit_cell(arch, order) or emptiest_cell(arch, order)
    return emptiest_cell(arch, order)


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
