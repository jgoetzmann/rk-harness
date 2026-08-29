"""Simulation -- SPEC ### rk_harness/simulate.py.

Two integrators over the same tableau:

* `rk_step_float` / `solve_float`: float64, used by the convergence study
  (`measured_order`). Coefficient sums are accumulated first, then multiplied by h once.
* `solve_q15`: the object of study. Every operation is a Q15 primitive from
  `rk_harness.fixedpoint`; coefficients are applied as `(v * m) >> s` via `q15_apply`
  using the CoeffRep from `rk_harness.coeffrep.to_rep`. Every intermediate stage input
  and every new state feeds the tracked maximum |q|. Q15OverflowError propagates.

This module sits in search.py's import graph: it reads only `PROBLEMS`, `to_physical`
and `error_metric` from problems.py.
"""
from __future__ import annotations

import math

from rk_harness.coeffrep import to_rep
from rk_harness.costmodel import cycle_count
from rk_harness.fixedpoint import q15_add, q15_apply, q15_from_float, q15_mul
from rk_harness.problems import PROBLEMS, error_metric, to_physical
from rk_harness.types import CostModel, Problem, Q15, Tableau


# --------------------------------------------------------------------------- float64

def float_tableau(t: Tableau) -> tuple[list[list[float]], list[float], list[float]]:
    """(A, b, c) as nested float lists."""
    A = [[float(x) for x in row] for row in t.A]
    b = [float(x) for x in t.b]
    c = [float(x) for x in t.c]
    return A, b, c


def rk_step_float(
    A: list[list[float]],
    b: list[float],
    c: list[float],
    rhs,
    t: float,
    y: tuple[float, ...],
    h: float,
) -> tuple[float, ...]:
    """One explicit RK step in float64.

    stage i: yi = y + h * sum_{j<i, A[i][j]!=0} A[i][j] * k_j   (sum first, one multiply by h)
             k_i = rhs(t + c_i*h, yi)
    y_new = y + h * sum_i b_i * k_i   (zero b_i skipped)
    """
    s = len(b)
    n = len(y)
    ks: list[tuple[float, ...]] = []
    for i in range(s):
        acc = [0.0] * n
        row = A[i]
        for j in range(i):
            a = row[j]
            if a != 0.0:
                kj = ks[j]
                for m in range(n):
                    acc[m] += a * kj[m]
        yi = tuple(y[m] + h * acc[m] for m in range(n))
        ks.append(tuple(rhs(t + c[i] * h, yi)))
    acc = [0.0] * n
    for i in range(s):
        bi = b[i]
        if bi != 0.0:
            ki = ks[i]
            for m in range(n):
                acc[m] += bi * ki[m]
    return tuple(y[m] + h * acc[m] for m in range(n))


def solve_float(t: Tableau, rhs, y0: tuple[float, ...], t_end: float, n: int) -> tuple[float, ...]:
    """n fixed steps of size h = t_end / n from t = 0; t_k = k*h."""
    A, b, c = float_tableau(t)
    h = t_end / n
    y = tuple(float(v) for v in y0)
    for k in range(n):
        y = rk_step_float(A, b, c, rhs, k * h, y, h)
    return y


# --------------------------------------------------------------------------- Q15

def solve_q15(t: Tableau, problem: Problem, n: int) -> tuple[tuple[Q15, ...], int]:
    """Integrate `problem` with tableau `t` in Q15 over n steps.

    Returns (final state, max |q| over every state and every stage input seen).
    Raises Q15OverflowError from any primitive, including h >= 1 (h_q unrepresentable).
    """
    h = problem.t_end / n
    h_q = q15_from_float(h)
    s = len(t.b)
    reps_A = [[(to_rep(x) if x != 0 else None) for x in row] for row in t.A]
    reps_b = [(to_rep(x) if x != 0 else None) for x in t.b]
    c_f = [float(x) for x in t.c]
    states = range(len(problem.y0))

    y = tuple(int(v) for v in problem.y0)
    max_abs = 0
    for v in y:
        if abs(v) > max_abs:
            max_abs = abs(v)

    for step in range(n):
        tk = step * h
        hk: list[tuple[Q15, ...]] = []
        for i in range(s):
            acc = y                                        # the "load"
            row = reps_A[i]
            for j in range(i):
                rep = row[j]
                if rep is None:
                    continue
                hkj = hk[j]
                acc = tuple(q15_add(acc[m], q15_apply(hkj[m], rep.m, rep.s)) for m in states)
            for v in acc:                                  # intermediates count for overflow
                if abs(v) > max_abs:
                    max_abs = abs(v)
            k_i = problem.f(tk + c_f[i] * h, acc)
            hk.append(tuple(q15_mul(kk, h_q) for kk in k_i))
        y_new = y
        for i in range(s):
            rep = reps_b[i]
            if rep is None:
                continue
            hki = hk[i]
            y_new = tuple(q15_add(y_new[m], q15_apply(hki[m], rep.m, rep.s)) for m in states)
        for v in y_new:
            if abs(v) > max_abs:
                max_abs = abs(v)
        y = y_new
    return y, max_abs


def steps_for_budget(t: Tableau, model: CostModel, n_states: int, budget_cycles: int) -> int:
    """budget_cycles // cycle_count(t, model, n_states); 0 if the cost is 0."""
    cost = cycle_count(t, model, n_states)
    if cost <= 0:
        return 0
    return budget_cycles // cost


def _fallback_error(problem: Problem, y_phys: tuple[float, ...]) -> float:
    """Error for a problem not in PROBLEMS (runtime-admitted quarantine problems):
    L2 distance to reference(t_end), scaled by the reference's peak magnitude (>= 1)."""
    ref = problem.reference(problem.t_end)
    peak = 1.0
    for v in ref:
        if abs(v) > peak:
            peak = abs(v)
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(y_phys, ref))) / peak


def problem_error(t: Tableau, problem: Problem, n: int) -> tuple[float, int]:
    """(error_metric(name, to_physical(final)), max_abs_q). Raises Q15OverflowError."""
    final, max_abs = solve_q15(t, problem, n)
    y_phys = to_physical(final, problem.scale)
    if problem.name in PROBLEMS:
        return error_metric(problem.name, y_phys), max_abs
    return _fallback_error(problem, y_phys), max_abs
