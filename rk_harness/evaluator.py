"""Evaluator — HANDOFF §4.7 as resolved by SPEC.md.

Deterministic (no wall clock, no threads, no randomness). `evaluate` never raises:
internal failures become inf / 0.0 / None fields. Coefficients stay Fractions except
inside the float64 convergence study and the numpy stability bisection.
"""
from __future__ import annotations

import math
from fractions import Fraction

import numpy as np

from rk_harness.types import CostModel, Problem, ScoreVector, Tableau
from rk_harness.simulate import solve_float, problem_error, steps_for_budget
from rk_harness.problems import SEARCH_SET, HELDOUT_SET, FLOAT_RHS
from rk_harness.costmodel import M0PLUS_FAST, M0PLUS_SLOW, AVR_APPROX, cycle_count
from rk_harness.coeffrep import tableau_csd_total, tableau_quant_error
from rk_harness.orderconditions import residuals, achieved_order_symbolic, trees

DEFAULT_BUDGET_CYCLES = 65536
CONVERGENCE_NS = [8 * 2**k for k in range(12)]
SLOPE_TOLERANCE = 0.08
ERROR_FLOOR = 1e-12

_INF = float("inf")
_DAHLQUIST_T_END = 10.0
_DAHLQUIST_REF = math.exp(-10.0)
_STABILITY_SAMPLES = 4000
_BISECTION_ITERS = 200
_STABILITY_EPS = 1e-12


# --------------------------------------------------------------------------- order


def _dahlquist_errors(t: Tableau) -> tuple[list[float], list[float]]:
    """Absolute final-state error on dahlquist (float64) at every n in CONVERGENCE_NS."""
    rhs = FLOAT_RHS["dahlquist"]
    errs: list[float] = []
    hs: list[float] = []
    for n in CONVERGENCE_NS:
        try:
            y = solve_float(t, rhs, (1.0,), _DAHLQUIST_T_END, n)
            e = abs(float(y[0]) - _DAHLQUIST_REF)
        except Exception:
            e = _INF
        if not math.isfinite(e):
            e = _INF
        errs.append(e)
        hs.append(_DAHLQUIST_T_END / n)
    return errs, hs


def _usable(e: float) -> bool:
    return math.isfinite(e) and e > ERROR_FLOOR


def _lsq_slope(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / sxx


def measured_order_with_points(t: Tableau) -> tuple[float | None, int]:
    """Longest-consistent-slope-run rule (SPEC §evaluator). (None, 0) when no window."""
    errs, hs = _dahlquist_errors(t)
    m = len(errs)
    slopes: list[float | None] = [None] * (m - 1)
    for i in range(m - 1):
        if _usable(errs[i]) and _usable(errs[i + 1]):
            slopes[i] = math.log(errs[i] / errs[i + 1]) / math.log(hs[i] / hs[i + 1])

    best_len = 0
    best_i = -1
    best_j = -1
    for i in range(m - 1):
        if slopes[i] is None:
            continue
        lo = hi = slopes[i]
        j = i
        while j + 1 < m - 1 and slopes[j + 1] is not None:
            nxt = slopes[j + 1]
            nlo = min(lo, nxt)
            nhi = max(hi, nxt)
            if nhi - nlo > SLOPE_TOLERANCE:
                break
            lo, hi = nlo, nhi
            j += 1
        length = j - i + 1
        if length > best_len:  # strict: ties keep the earliest i
            best_len, best_i, best_j = length, i, j

    if best_len < 2:
        return (None, 0)
    idx = range(best_i, best_j + 2)
    xs = [math.log(hs[k]) for k in idx]
    ys = [math.log(errs[k]) for k in idx]
    slope = _lsq_slope(xs, ys)
    if not math.isfinite(slope):
        return (None, 0)
    return (slope, best_j - best_i + 2)


def measured_order(t: Tableau) -> float | None:
    return measured_order_with_points(t)[0]


# ----------------------------------------------------------------------- stability


def stability_polynomial(t: Tableau) -> list[Fraction]:
    """[1, c1, ..., cs] with c_k = b^T A^(k-1) 1, exact Fractions."""
    s = len(t.b)
    coeffs = [Fraction(1)]
    v = [Fraction(1)] * s
    for _ in range(s):
        coeffs.append(sum((Fraction(bi) * vi for bi, vi in zip(t.b, v)), Fraction(0)))
        v = [sum((Fraction(t.A[i][j]) * v[j] for j in range(s)), Fraction(0)) for i in range(s)]
    return coeffs


def stability_extents(t: Tableau) -> tuple[float, float]:
    """(real, imag) extents by bisection: 4000 samples, 200 iterations."""
    coeffs = [float(c) for c in stability_polynomial(t)]
    poly = np.array(coeffs[::-1], dtype=float)  # np.polyval wants highest power first
    limit = 1.0 + _STABILITY_EPS

    def stable_real(x: float) -> bool:
        ts = np.linspace(x, 0.0, _STABILITY_SAMPLES)
        return bool(np.all(np.abs(np.polyval(poly, ts)) <= limit))

    def stable_imag(y: float) -> bool:
        ts = np.linspace(0.0, y, _STABILITY_SAMPLES)
        return bool(np.all(np.abs(np.polyval(poly, 1j * ts)) <= limit))

    lo, hi = -100.0, 0.0
    if stable_real(lo):
        real = lo
    else:
        for _ in range(_BISECTION_ITERS):
            mid = (lo + hi) / 2.0
            if stable_real(mid):
                hi = mid
            else:
                lo = mid
        real = hi

    lo, hi = 0.0, 100.0
    if stable_imag(hi):
        imag = hi
    else:
        for _ in range(_BISECTION_ITERS):
            mid = (lo + hi) / 2.0
            if stable_imag(mid):
                lo = mid
            else:
                hi = mid
        imag = lo
    return (float(real), float(imag))


# ------------------------------------------------------------------ error constant


def error_constant(t: Tableau) -> float:
    """L2 norm of the residuals of order achieved_order + 1 (that order's trees only)."""
    p = achieved_order_symbolic(t, max_order=6)
    res = residuals(t, p + 1)
    skip = sum(len(trees(k)) for k in range(1, p + 1))
    return math.sqrt(sum(float(r) ** 2 for r in res[skip:]))


# --------------------------------------------------------------------- set errors


def _rms(values: list[float]) -> float:
    if not values:
        return 0.0
    return math.sqrt(sum(v * v for v in values) / len(values))


def _run_set(t: Tableau, problems: tuple[Problem, ...], model: CostModel,
             budget_cycles: int) -> tuple[dict[str, float], int, bool]:
    """Per-problem error dict, max |q| seen, and whether any overflow occurred."""
    per: dict[str, float] = {}
    max_q = 0
    overflowed = False
    for p in problems:
        try:
            n = steps_for_budget(t, model, p.n_states, budget_cycles)
            if n <= 0:
                per[p.name] = _INF
                continue
            e, q = problem_error(t, p, n)
            e = float(e)
            if not math.isfinite(e):
                e = _INF
            per[p.name] = e
            max_q = max(max_q, abs(int(q)))
        except Exception:
            per[p.name] = _INF
            overflowed = True
    return per, max_q, overflowed


def set_error(t: Tableau, problems: tuple[Problem, ...], model: CostModel,
              budget_cycles: int) -> tuple[float, dict[str, float], int]:
    """(RMS of problem_error over problems, per-problem dict, max_abs_q).
    On Q15OverflowError anywhere: (inf, {name: inf for all}, 32768)."""
    per, max_q, overflowed = _run_set(t, problems, model, budget_cycles)
    if overflowed:
        return (_INF, {p.name: _INF for p in problems}, 32768)
    return (_rms(list(per.values())), per, max_q)


# ----------------------------------------------------------------------- evaluate


def evaluate(t: Tableau, budget_cycles: int) -> ScoreVector:
    """Full score at equal cycle budget under the three cost models. Never raises."""
    cycles: dict[str, int] = {}
    for model in (M0PLUS_FAST, M0PLUS_SLOW, AVR_APPROX):
        try:
            cycles[model.name] = int(cycle_count(t, model, 1))
        except Exception:
            cycles[model.name] = 0

    per_problem: dict[str, float] = {}
    search_error = _INF
    heldout_error = _INF
    overflow_margin = 0.0

    for model, prefix in ((M0PLUS_FAST, ""), (M0PLUS_SLOW, "slow:"), (AVR_APPROX, "avr_approx:")):
        try:
            s_per, s_q, s_ovf = _run_set(t, SEARCH_SET, model, budget_cycles)
        except Exception:
            s_per, s_q, s_ovf = {p.name: _INF for p in SEARCH_SET}, 0, True
        try:
            h_per, h_q, h_ovf = _run_set(t, HELDOUT_SET, model, budget_cycles)
        except Exception:
            h_per, h_q, h_ovf = {p.name: _INF for p in HELDOUT_SET}, 0, True
        s_rms = _rms(list(s_per.values()))
        h_rms = _rms(list(h_per.values()))
        for name, e in s_per.items():
            per_problem[prefix + name] = e
        for name, e in h_per.items():
            per_problem[prefix + name] = e
        if prefix == "":
            search_error = s_rms
            heldout_error = h_rms
            max_abs_q = max(s_q, h_q)
            if s_ovf or h_ovf or max_abs_q == 0:
                overflow_margin = 0.0
            else:
                overflow_margin = 1.0 / (2.0 * max_abs_q / 32768.0)
        else:
            per_problem[prefix + "search_error"] = s_rms
            per_problem[prefix + "heldout_error"] = h_rms

    try:
        m_order, fit_points = measured_order_with_points(t)
        if m_order is not None and not math.isfinite(m_order):
            m_order, fit_points = None, 0
    except Exception:
        m_order, fit_points = None, 0

    try:
        stab_real, stab_imag = stability_extents(t)
    except Exception:
        stab_real, stab_imag = _INF, _INF

    try:
        csd_total = int(tableau_csd_total(t))
    except Exception:
        csd_total = 0

    try:
        quant_err = float(tableau_quant_error(t))
    except Exception:
        quant_err = _INF

    try:
        err_const = float(error_constant(t))
    except Exception:
        err_const = _INF

    return ScoreVector(
        measured_order=m_order,
        order_fit_points=int(fit_points),
        error_constant=err_const,
        stability_real=stab_real,
        stability_imag=stab_imag,
        cycles=cycles,
        csd_weight_total=csd_total,
        coeff_quant_error=quant_err,
        search_error=search_error,
        heldout_error=heldout_error,
        overflow_margin=overflow_margin,
        per_problem=per_problem,
    )
