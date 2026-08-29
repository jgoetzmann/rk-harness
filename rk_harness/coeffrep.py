"""Coefficient representation — HANDOFF §4.2b, SPEC ### rk_harness/coeffrep.py.

States are Q15; coefficients are not. Each coefficient is stored as an
integer-and-shift pair, value = m / 2**s, with |m| <= 32767 and 0 <= s <= 20.
Inexactness is recorded (quant_error), never rejected here.
"""
from __future__ import annotations

from fractions import Fraction

from rk_harness.types import CoeffRep, Tableau


def csd_weight(m: int) -> int:
    """Non-adjacent-form weight of |m|: minimum number of nonzero signed powers of two."""
    m = abs(m)
    w = 0
    while m:
        if m & 1:
            z = 2 - (m % 4)
            m -= z
            w += 1
        m //= 2
    return w


def to_rep(x: Fraction, s_max: int = 20, m_max: int = 32767) -> CoeffRep:
    """Smallest s with x*2**s an integer of magnitude <= m_max (exact); otherwise the
    s minimising |x - round(x*2**s)/2**s| with m clamped (ties -> smaller s). Never raises."""
    x = Fraction(x)
    for s in range(s_max + 1):
        v = x * (1 << s)
        if v.denominator == 1 and abs(v.numerator) <= m_max:
            m = v.numerator
            return CoeffRep(m, s, True, csd_weight(m))
    best_m = 0
    best_s = 0
    best_err = None
    for s in range(s_max + 1):
        m = round(x * (1 << s))
        if m > m_max:
            m = m_max
        elif m < -m_max:
            m = -m_max
        err = abs(x - Fraction(m, 1 << s))
        if best_err is None or err < best_err:
            best_err = err
            best_m = m
            best_s = s
    return CoeffRep(best_m, best_s, False, csd_weight(best_m))


def rep_value(r: CoeffRep) -> Fraction:
    return Fraction(r.m, 1 << r.s)


def quant_error(x: Fraction) -> float:
    x = Fraction(x)
    return abs(float(x - rep_value(to_rep(x))))


def is_trivial(x: Fraction) -> bool:
    return x == 0 or x == 1 or x == -1


def _ab_entries(t: Tableau):
    for row in t.A:
        for x in row:
            yield x
    for x in t.b:
        yield x


def tableau_csd_total(t: Tableau) -> int:
    """Sum of CSD weights over every non-trivial entry of A and b (c excluded)."""
    total = 0
    for x in _ab_entries(t):
        if not is_trivial(x):
            total += to_rep(x).csd_weight
    return total


def tableau_quant_error(t: Tableau) -> float:
    """Max quantisation error over every entry of A and b (c excluded); 0.0 if all exact."""
    worst = 0.0
    for x in _ab_entries(t):
        e = quant_error(x)
        if e > worst:
            worst = e
    return worst
