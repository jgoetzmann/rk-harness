"""Butcher tableau helpers — HANDOFF §4.1, §4.11, SPEC ### rk_harness/tableau.py.

Coefficients are fractions.Fraction. Never float: make_tableau raises TypeError
on a float entry. canonical()/content_hash() follow HANDOFF §4.11 byte for byte.
"""
from __future__ import annotations

import hashlib
import json
from fractions import Fraction

from rk_harness.paths import FIXTURES_DIR
from rk_harness.types import Q15Tableau, Tableau

_Q15_MIN = -32768
_Q15_MAX = 32767
_Q15_ONE = 32768


def stages(t: Tableau) -> int:
    return len(t.b)


def row_sums_consistent(t: Tableau) -> bool:
    """len(A) == len(b) == len(c), A square, and sum(A[i]) == c[i] exactly for every i."""
    n = len(t.A)
    if len(t.b) != n or len(t.c) != n:
        return False
    for row in t.A:
        if len(row) != n:
            return False
    for i in range(n):
        if sum(t.A[i], Fraction(0)) != t.c[i]:
            return False
    return True


def is_explicit(t: Tableau) -> bool:
    """A square and every entry on or above the diagonal is exactly 0."""
    n = len(t.A)
    for row in t.A:
        if len(row) != n:
            return False
    for i in range(n):
        for j in range(i, n):
            if t.A[i][j] != 0:
                return False
    return True


def _q15_entry(x: Fraction) -> tuple[int, bool]:
    v = x * _Q15_ONE
    # exact: an integer number of LSBs and |x| <= 1 (the value 1 itself is representable
    # only as the clamped 32767, but counts as exact — orchestrator ruling on B9).
    exact = v.denominator == 1 and _Q15_MIN <= v <= _Q15_ONE
    q = round(v)
    if q > _Q15_MAX:
        q = _Q15_MAX
    elif q < _Q15_MIN:
        q = _Q15_MIN
    return q, exact


def to_q15(t: Tableau) -> Q15Tableau:
    """Each entry round(x*32768) clamped to int16. exact iff every x*32768 was an integer
    (B9: heun2, whose entry 1 clamps to 32767, is still exact; rk4 with 1/6 is not)."""
    exact = True
    A_rows = []
    for row in t.A:
        qrow = []
        for x in row:
            q, e = _q15_entry(x)
            exact = exact and e
            qrow.append(q)
        A_rows.append(tuple(qrow))
    b_q = []
    for x in t.b:
        q, e = _q15_entry(x)
        exact = exact and e
        b_q.append(q)
    c_q = []
    for x in t.c:
        q, e = _q15_entry(x)
        exact = exact and e
        c_q.append(q)
    return Q15Tableau(tuple(A_rows), tuple(b_q), tuple(c_q), exact)


def _fstr(x: Fraction) -> str:
    return f"{x.numerator}/{x.denominator}"


def to_json(t: Tableau) -> dict:
    return {
        "A": [[_fstr(x) for x in row] for row in t.A],
        "b": [_fstr(x) for x in t.b],
        "c": [_fstr(x) for x in t.c],
    }


def canonical(t: Tableau) -> str:
    """HANDOFF §4.11: json.dumps of the num/den strings, sort_keys, compact separators."""
    return json.dumps(to_json(t), sort_keys=True, separators=(",", ":"))


def content_hash(t: Tableau) -> str:
    return hashlib.sha256(canonical(t).encode("utf-8")).hexdigest()


def _frac(v) -> Fraction:
    if isinstance(v, Fraction):
        return v
    if isinstance(v, float):
        raise TypeError(f"float coefficient {v!r} not allowed; use int, 'num/den' string, or Fraction")
    if isinstance(v, int):
        return Fraction(v)
    if isinstance(v, str):
        return Fraction(v.strip())
    raise TypeError(f"unsupported coefficient type {type(v).__name__}: {v!r}")


def make_tableau(A, b, c=None) -> Tableau:
    """Entries may be int, 'num/den' str, or Fraction; float raises TypeError.
    c=None -> row sums of A."""
    A_t = tuple(tuple(_frac(x) for x in row) for row in A)
    b_t = tuple(_frac(x) for x in b)
    if c is None:
        c_t = tuple(sum(row, Fraction(0)) for row in A_t)
    else:
        c_t = tuple(_frac(x) for x in c)
    return Tableau(A_t, b_t, c_t)


def from_json(d: dict) -> Tableau:
    return make_tableau(d["A"], d["b"], d.get("c"))


def _is_pow2(d: int) -> bool:
    return d > 0 and (d & (d - 1)) == 0


def all_dyadic(t: Tableau) -> bool:
    """Every entry of A, b, c has a power-of-two denominator (1 counts)."""
    for row in t.A:
        for x in row:
            if not _is_pow2(x.denominator):
                return False
    for x in t.b:
        if not _is_pow2(x.denominator):
            return False
    for x in t.c:
        if not _is_pow2(x.denominator):
            return False
    return True


def classical() -> dict[str, Tableau]:
    """The 8 named tableaus from fixtures/classical.json; keys starting with '_' are skipped."""
    with open(FIXTURES_DIR / "classical.json", "r", encoding="utf-8") as fh:
        data = json.load(fh)
    out: dict[str, Tableau] = {}
    for name, entry in data.items():
        if name.startswith("_"):
            continue
        out[name] = from_json(entry)
    return out
