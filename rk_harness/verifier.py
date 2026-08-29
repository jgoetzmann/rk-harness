"""Verifier — HANDOFF §4.4 as resolved by SPEC.md.

`verify` never raises, never calls an LLM, never opens a socket, never writes.
Checks run in the fixed order 1..9; steps 6-9 come from exactly one call to
`evaluator.evaluate` made through the module attribute (tests monkeypatch it),
and only after steps 1-5 all pass.
"""
from __future__ import annotations

import math
from fractions import Fraction

from rk_harness import evaluator
from rk_harness.types import ScoreVector, Tableau, VerdictReason
from rk_harness.tableau import is_explicit, row_sums_consistent, all_dyadic
from rk_harness.orderconditions import residuals
from rk_harness.coeffrep import to_rep

REJECT_CODES = frozenset({
    "NOT_EXPLICIT", "ROW_SUM_INCONSISTENT", "ORDER_NOT_MET",
    "DYADIC_IMPOSSIBLE", "COEFF_UNREPRESENTABLE", "Q15_OVERFLOW",
    "UNSTABLE", "NAN_OR_INF", "NO_ASYMPTOTIC_WINDOW",
})
STABILITY_THRESHOLD = -0.5

_Q15_LIMIT = 32768
_MIN_ORDER = 1
_MAX_ORDER = 4
_DETAIL_MAX = 200


def _short(x: object) -> str:
    try:
        r = repr(x)
    except Exception:
        r = "<unrepresentable>"
    if len(r) > _DETAIL_MAX:
        r = r[:_DETAIL_MAX] + "..."
    return r


def _malformed(t: object, claimed_order: object) -> str | None:
    """Return a reason string if (t, claimed_order) is not a well-formed input; else None."""
    if not isinstance(t, Tableau):
        return "not a Tableau: " + _short(t)
    if isinstance(claimed_order, bool) or not isinstance(claimed_order, int):
        return "claimed_order not int: " + _short(claimed_order)
    if not (_MIN_ORDER <= claimed_order <= _MAX_ORDER):
        return f"claimed_order {claimed_order} not in {_MIN_ORDER}..{_MAX_ORDER}"
    A, b, c = t.A, t.b, t.c
    try:
        s = len(A)
        if s < 1:
            return "empty tableau"
        if len(b) != s or len(c) != s:
            return f"len mismatch: A {s}, b {len(b)}, c {len(c)}"
        for row in A:
            if len(row) != s:
                return "A not square"
            for x in row:
                if not isinstance(x, Fraction):
                    return "non-Fraction entry in A: " + _short(x)
        for x in b:
            if not isinstance(x, Fraction):
                return "non-Fraction entry in b: " + _short(x)
        for x in c:
            if not isinstance(x, Fraction):
                return "non-Fraction entry in c: " + _short(x)
    except TypeError as e:
        return "bad container: " + _short(e)
    return None


def _entries(t: Tableau):
    for row in t.A:
        for x in row:
            yield x
    for x in t.b:
        yield x
    for x in t.c:
        yield x


def _cheap_checks(t: Tableau, claimed_order: int) -> VerdictReason | None:
    # 1 NOT_EXPLICIT
    if not is_explicit(t):
        return VerdictReason("NOT_EXPLICIT", "nonzero entry on or above the diagonal of A")
    # 2 ROW_SUM_INCONSISTENT
    if not row_sums_consistent(t):
        return VerdictReason("ROW_SUM_INCONSISTENT", "some sum(A[i]) != c[i]")
    # 3 DYADIC_IMPOSSIBLE
    if claimed_order >= 3 and all_dyadic(t):
        return VerdictReason("DYADIC_IMPOSSIBLE",
                             f"all coefficients dyadic but claimed order {claimed_order} >= 3")
    # 4 ORDER_NOT_MET
    res = residuals(t, claimed_order)
    for k, r in enumerate(res):
        if r != 0:
            return VerdictReason("ORDER_NOT_MET",
                                 f"residual {k} of order <= {claimed_order} is {r}")
    # 5 COEFF_UNREPRESENTABLE
    for x in _entries(t):
        if abs(x) >= _Q15_LIMIT:
            return VerdictReason("COEFF_UNREPRESENTABLE", f"|{x}| >= {_Q15_LIMIT}")
        if x != 0 and to_rep(x).m == 0:
            return VerdictReason("COEFF_UNREPRESENTABLE", f"{x} needs s > 20")
    return None


def _is_finite_number(v: object) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float, Fraction)):
        return math.isfinite(float(v))
    return False


def _score_checks(sv: ScoreVector) -> VerdictReason | None:
    # 6 Q15_OVERFLOW
    if sv.overflow_margin <= 1.0:
        return VerdictReason("Q15_OVERFLOW", f"overflow_margin {sv.overflow_margin} <= 1.0")
    # 7 UNSTABLE
    if sv.stability_real > STABILITY_THRESHOLD:
        return VerdictReason("UNSTABLE",
                             f"stability_real {sv.stability_real} > {STABILITY_THRESHOLD}")
    # 8 NO_ASYMPTOTIC_WINDOW
    if sv.measured_order is None:
        return VerdictReason("NO_ASYMPTOTIC_WINDOW", "measured_order is None")
    # 9 NAN_OR_INF
    scalars = (
        ("search_error", sv.search_error),
        ("heldout_error", sv.heldout_error),
        ("error_constant", sv.error_constant),
        ("stability_real", sv.stability_real),
        ("stability_imag", sv.stability_imag),
        ("measured_order", sv.measured_order),
    )
    for name, v in scalars:
        if not _is_finite_number(v):
            return VerdictReason("NAN_OR_INF", f"{name} = {v!r}")
    # Only the primary-model columns gate verification. "slow:*" and "avr_approx:*" entries are
    # archive columns (HANDOFF §1 scope lock, §4.5: no claim may rest on AVR_APPROX), and an
    # expensive method legitimately gets too few steps under a 32-cycle multiplier.
    for key, v in sv.per_problem.items():
        if ":" in key:
            continue
        if not _is_finite_number(v):
            return VerdictReason("NAN_OR_INF", f"per_problem[{key!r}] = {v!r}")
    return None


def cheap_checks(t: Tableau, claimed_order: int) -> VerdictReason | None:
    """Steps 1-5 only. Never raises."""
    try:
        bad = _malformed(t, claimed_order)
    except Exception as e:
        bad = _short(e)
    if bad is not None:
        return VerdictReason("NOT_EXPLICIT", "malformed: " + bad)
    try:
        return _cheap_checks(t, claimed_order)
    except Exception as e:
        return VerdictReason("NAN_OR_INF", "internal: " + _short(e))


def verify_with_score(t: Tableau, claimed_order: int) -> tuple[VerdictReason | None, ScoreVector | None]:
    """Same as verify but also returns the ScoreVector from steps 6-9
    (None if rejected in steps 1-5 or on internal error)."""
    try:
        bad = _malformed(t, claimed_order)
    except Exception as e:
        bad = _short(e)
    if bad is not None:
        return VerdictReason("NOT_EXPLICIT", "malformed: " + bad), None

    try:
        v = _cheap_checks(t, claimed_order)
    except Exception as e:
        return VerdictReason("NAN_OR_INF", "internal: " + _short(e)), None
    if v is not None:
        return v, None

    try:
        sv = evaluator.evaluate(t, evaluator.DEFAULT_BUDGET_CYCLES)
        v = _score_checks(sv)
    except Exception as e:
        return VerdictReason("NAN_OR_INF", "internal: " + _short(e)), None
    return v, sv


def verify(t: Tableau, claimed_order: int) -> VerdictReason | None:
    """None means pass. Never raises."""
    try:
        return verify_with_score(t, claimed_order)[0]
    except Exception as e:  # belt and braces: nothing may escape
        return VerdictReason("NAN_OR_INF", "internal: " + _short(e))
