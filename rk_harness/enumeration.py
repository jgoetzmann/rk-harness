"""Exhaustive enumeration of the Phase 0 and Phase 1 spaces
(SPEC ### rk_harness/enumeration.py, HANDOFF §8, §9.6).

Independent of search.py on purpose.
"""
from __future__ import annotations

from fractions import Fraction
from math import gcd

from rk_harness.coeffrep import to_rep
from rk_harness.costmodel import cycle_count
from rk_harness.orderconditions import residuals
from rk_harness.tableau import canonical, make_tableau
from rk_harness.types import CostModel, Tableau

PHASE0_S_MAX = 6
PHASE0_ABS_MAX = 2
PHASE1_S_MAX = 8
PHASE1_CAP = 100_000_000


def lattice(s_max: int, abs_max: int) -> list[Fraction]:
    """Sorted distinct m / 2**s with 0 <= s <= s_max and 0 < |m / 2**s| <= abs_max."""
    vals: set[Fraction] = set()
    for s in range(s_max + 1):
        den = 2 ** s
        m_max = abs_max * den
        for m in range(-m_max, m_max + 1):
            if m == 0:
                continue
            vals.add(Fraction(m, den))
    return sorted(vals)


def enumerate_phase0() -> list[Tableau]:
    out: list[Tableau] = []
    for a21 in lattice(PHASE0_S_MAX, PHASE0_ABS_MAX):
        b2 = 1 / (2 * a21)
        b1 = 1 - b2
        if not (to_rep(b1).exact and to_rep(b2).exact):
            continue
        A = [[Fraction(0), Fraction(0)], [a21, Fraction(0)]]
        out.append(make_tableau(A, [b1, b2], [Fraction(0), a21]))
    return out


def phase0_candidate_count() -> int:
    return len(lattice(PHASE0_S_MAX, PHASE0_ABS_MAX))


def _is_pow2_den(num: int, den: int) -> bool:
    """Cheap integer prefilter: does num/den reduce to a dyadic rational?"""
    if den == 0:
        return False
    g = gcd(num, den)
    d = abs(den // g)
    return (d & (d - 1)) == 0


def enumerate_phase1() -> tuple[list[Tableau], bool]:
    lat = lattice(PHASE1_S_MAX, 2)
    if len(lat) ** 2 > PHASE1_CAP:
        return [], True
    unit = 2 ** PHASE1_S_MAX                       # every lattice value is k / unit
    ks = [x.numerator * (unit // x.denominator) for x in lat]
    two_thirds = Fraction(2, 3)
    out: list[Tableau] = []
    zero = Fraction(0)
    for p in ks:                                   # c2 = p / unit
        c2 = Fraction(p, unit)
        if c2 == two_thirds:
            continue
        den_a = p * (2 * unit - 3 * p)             # c2 * (2 - 3 c2) * unit**2
        if den_a == 0:
            continue
        for q in ks:                               # c3 = q / unit
            if q == p:
                continue
            num_a = q * (q - p)                    # c3 * (c3 - c2) * unit**2
            if not _is_pow2_den(num_a, den_a):
                continue
            a32 = Fraction(num_a, den_a)
            c3 = Fraction(q, unit)
            a31 = c3 - a32
            r32 = to_rep(a32)
            if not r32.exact:
                continue
            r31 = to_rep(a31)
            if not r31.exact:
                continue
            if not to_rep(c2).exact:
                continue
            b2 = (3 * c3 - 2) / (6 * c2 * (c3 - c2))
            b3 = (2 - 3 * c2) / (6 * c3 * (c3 - c2))
            b1 = 1 - b2 - b3
            A = [[zero, zero, zero], [c2, zero, zero], [a31, a32, zero]]
            t = make_tableau(A, [b1, b2, b3], [zero, c2, c3])
            assert all(r == 0 for r in residuals(t, 3)), "phase 1 family must satisfy order 3 exactly"
            out.append(t)
    return out, False


def cheapest(tableaus: list[Tableau], model: CostModel) -> list[tuple[int, Tableau]]:
    rows = [(cycle_count(t, model, 1), t) for t in tableaus]
    rows.sort(key=lambda ct: (ct[0], canonical(ct[1])))
    return rows
