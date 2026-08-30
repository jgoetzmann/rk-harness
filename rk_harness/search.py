"""CMA-ES search over explicit RK tableaus with exact projection onto the order
conditions (SPEC ### rk_harness/search.py, HANDOFF §4.9).

Import discipline (test K12): this module and everything it imports must never
load the name of the held-out problem set. Only ``SEARCH_SET`` is used here.
Never import evaluator, verifier, archive or runner.
"""
from __future__ import annotations

import dataclasses
import math
from fractions import Fraction
from typing import Iterator

import cma

from rk_harness.costmodel import M0PLUS_FAST
from rk_harness.fixedpoint import Q15OverflowError
from rk_harness.orderconditions import b_linear_system, residuals
from rk_harness.problems import SEARCH_SET
from rk_harness.simulate import problem_error, steps_for_budget
from rk_harness.tableau import content_hash, make_tableau
from rk_harness.types import Island, Tableau

_DEFAULT_BUDGET_CYCLES = 65536
_OVERFLOW_FITNESS = 1e9
_PENALTY_WEIGHT = 1e6


def free_parameters(stages: int) -> int:
    return stages * (stages - 1) // 2 + stages


def snap(x: float, denominator: int = 32768) -> Fraction:
    return Fraction(round(x * denominator), denominator)


def default_constraints() -> dict:
    return {"force_zero": [], "dyadic_denominator_max": 32768, "c_fixed": {}, "b_nonneg": False}


def _finite(x) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError, OverflowError):
        return False


def _lower_indices(stages: int) -> list[tuple[int, int]]:
    out = []
    for i in range(stages):
        for j in range(i):
            out.append((i, j))
    return out


def _solve_exact(G: list[list[Fraction]], r: list[Fraction], b_guess: list[float],
                 denominator: int) -> list[Fraction] | None:
    """Gaussian elimination over Fractions on G @ b == r.  Pivot columns are solved
    exactly, free columns are snapped from b_guess.  Inconsistent -> None."""
    m = len(G)
    n = len(G[0]) if m else len(b_guess)
    M = [[Fraction(v) for v in G[i]] + [Fraction(r[i])] for i in range(m)]
    pivots: list[tuple[int, int]] = []
    row = 0
    for col in range(n):
        if row >= m:
            break
        piv = None
        for i in range(row, m):
            if M[i][col] != 0:
                piv = i
                break
        if piv is None:
            continue
        M[row], M[piv] = M[piv], M[row]
        pv = M[row][col]
        M[row] = [v / pv for v in M[row]]
        for i in range(m):
            if i != row and M[i][col] != 0:
                f = M[i][col]
                M[i] = [a - f * bb for a, bb in zip(M[i], M[row])]
        pivots.append((row, col))
        row += 1
    for i in range(row, m):
        if M[i][n] != 0:
            return None
    pivot_cols = {c for _, c in pivots}
    b: list[Fraction] = [Fraction(0)] * n
    free_cols = [j for j in range(n) if j not in pivot_cols]
    for j in free_cols:
        guess = float(b_guess[j]) if j < len(b_guess) else 0.0
        b[j] = snap(guess, denominator)
    for i, c in pivots:
        val = M[i][n]
        for j in free_cols:
            if M[i][j] != 0:
                val -= M[i][j] * b[j]
        b[c] = val
    return b


def project(A_free: list[float], b_guess: list[float], stages: int, order: int,
            constraints: dict) -> Tableau | None:
    if not all(_finite(v) for v in A_free) or not all(_finite(v) for v in b_guess):
        return None
    D = int(constraints.get("dyadic_denominator_max", 32768) or 32768)
    A: list[list[Fraction]] = [[Fraction(0)] * stages for _ in range(stages)]
    for k, (i, j) in enumerate(_lower_indices(stages)):
        if k < len(A_free):
            A[i][j] = snap(float(A_free[k]), D)
    for entry in constraints.get("force_zero", []) or []:
        i, j = int(entry[0]), int(entry[1])
        if 0 <= j < i < stages:
            A[i][j] = Fraction(0)
    for key, val in (constraints.get("c_fixed", {}) or {}).items():
        i = int(key)
        if 1 <= i < stages:
            ci = val if isinstance(val, Fraction) else Fraction(str(val))
            A[i][i - 1] = ci - sum(A[i][:i - 1], Fraction(0))
    c = [sum(row, Fraction(0)) for row in A]
    tab_A = tuple(tuple(row) for row in A)
    G, r = b_linear_system(tab_A, tuple(c), order)
    b = _solve_exact(G, r, [float(v) for v in b_guess], D)
    if b is None:
        return None
    return make_tableau(A, b, c)


def project_or_lower(A_free: list[float], b_guess: list[float], stages: int, order: int,
                     constraints: dict) -> Tableau | None:
    """project() at `order`, else at the highest lower order >= 2 that is exactly solvable.

    Exact order-4 conditions are rarely solvable for a snapped (dyadic) A, so an order-4
    directive would otherwise yield nothing for months. The runner verifies each candidate at
    the order it actually achieves, and the residual penalty in the fitness still pulls the
    search toward the requested order."""
    p = order
    while p >= 2:
        t = project(A_free, b_guess, stages, p, constraints)
        if t is not None:
            return t
        p -= 1
    return None


def _search_rms(t: Tableau, budget_cycles: int) -> float:
    errs = []
    try:
        for p in SEARCH_SET:
            n = steps_for_budget(t, M0PLUS_FAST, p.n_states, budget_cycles)
            if n <= 0:
                return _OVERFLOW_FITNESS
            e, _ = problem_error(t, p, n)
            if not _finite(e):
                return _OVERFLOW_FITNESS
            errs.append(float(e))
    except Q15OverflowError:
        return _OVERFLOW_FITNESS
    except (OverflowError, ValueError, ZeroDivisionError, ArithmeticError):
        return _OVERFLOW_FITNESS
    if not errs:
        return _OVERFLOW_FITNESS
    return math.sqrt(sum(e * e for e in errs) / len(errs))


def _residual_penalty(t: Tableau, order: int) -> float:
    return _PENALTY_WEIGHT * sum(float(r) ** 2 for r in residuals(t, order))


def objective(t: Tableau, order: int, budget_cycles: int) -> float:
    rms = _search_rms(t, budget_cycles)
    pen = _residual_penalty(t, order)
    return rms + pen


def _float_penalty(A_free: list[float], b_guess: list[float], stages: int, order: int) -> float:
    """Residual penalty of the raw (unprojected) floats, converted exactly to Fractions."""
    try:
        A = [[Fraction(0)] * stages for _ in range(stages)]
        for k, (i, j) in enumerate(_lower_indices(stages)):
            if k < len(A_free) and _finite(A_free[k]):
                A[i][j] = Fraction(float(A_free[k]))
        b = [Fraction(float(b_guess[j])) if j < len(b_guess) and _finite(b_guess[j]) else Fraction(0)
             for j in range(stages)]
        t = make_tableau(A, b)
        pen = sum(float(r) ** 2 for r in residuals(t, order))
        if not _finite(pen):
            return 1e3
        return pen
    except Exception:
        return 1e3


def _fitness(x, stages: int, order: int, constraints: dict, budget_cycles: int) -> tuple[Tableau | None, float]:
    n_a = stages * (stages - 1) // 2
    A_free = [float(v) for v in x[:n_a]]
    b_guess = [float(v) for v in x[n_a:n_a + stages]]
    try:
        t = project_or_lower(A_free, b_guess, stages, order, constraints)
    except Exception:
        t = None
    if t is None:
        return None, _OVERFLOW_FITNESS + _PENALTY_WEIGHT * _float_penalty(A_free, b_guess, stages, order)
    fit = objective(t, order, budget_cycles)
    if constraints.get("b_nonneg", False):
        fit += _PENALTY_WEIGHT * sum(min(float(bi), 0.0) ** 2 for bi in t.b)
    return t, fit


def _default_x0(stages: int) -> list[float]:
    x0 = []
    for i, j in _lower_indices(stages):
        x0.append(0.5 if j == i - 1 else 0.0)
    x0.extend([1.0 / stages] * stages)
    return x0


def cmaes_island(order: int, stages: int, seed: int, constraints: dict, budget: int) -> Iterator[Tableau]:
    n = free_parameters(stages)
    x0_raw = constraints.get("x0")
    if x0_raw is not None and len(x0_raw) == n:
        x0 = [float(v) for v in x0_raw]
    else:
        x0 = _default_x0(stages)
    budget_cycles = _DEFAULT_BUDGET_CYCLES
    evals = 0
    best = math.inf
    seen: set[str] = set()
    k = 0
    while evals < budget:
        cma_seed = seed + 1000 * k
        if cma_seed == 0:
            cma_seed = 2**31 - 1
        es = cma.CMAEvolutionStrategy(
            list(x0), 0.3,
            {"seed": cma_seed, "verbose": -9, "verb_log": 0, "verb_disp": 0},
        )
        k += 1
        while evals < budget and not es.stop():
            xs = es.ask()
            remaining = budget - evals
            fits: list[float] = []
            for x in xs[:remaining]:
                t, fit = _fitness(x, stages, order, constraints, budget_cycles)
                evals += 1
                fits.append(fit)
                if t is not None and fit < best:
                    best = fit
                    h = content_hash(t)
                    if h not in seen:
                        seen.add(h)
                        yield t
            if len(fits) < len(xs):
                break
            es.tell(xs, fits)


def _heldout_key(rec) -> float:
    try:
        v = float(rec.score.heldout_error)
    except Exception:
        return math.inf
    return v if math.isfinite(v) else math.inf


def migrate(islands: list[Island]) -> None:
    bests = [isl.best for isl in islands if isl.best is not None]
    if not bests:
        return
    global_best = bests[0]
    for rec in bests[1:]:
        if _heldout_key(rec) < _heldout_key(global_best):
            global_best = rec
    gval = _heldout_key(global_best)
    for i, isl in enumerate(islands):
        if isl.best is None or _heldout_key(isl.best) > gval:
            islands[i] = dataclasses.replace(isl, best=global_best)
