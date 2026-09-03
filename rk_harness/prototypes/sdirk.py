"""SDIRK prototype for the epoch-3 side track. PRELIMINARY, FLOAT-ONLY, OFF-ARCHIVE.

Nothing here is verifier-pinned and nothing here writes to the archive. This module
is the working half of docs/EPOCH3-DESIGN.md: a 2-stage, order-2, L-stable SDIRK
(Alexander 1977, gamma = 1 - sqrt(2)/2) integrated in float64 with a fixed modified
Newton iteration count, so the per-step work is a compile-time constant exactly as
the epoch-3 cost model requires.

Tableau (stiffly accurate, b equals the last row of A):

    c = [gamma, 1]
    A = [[gamma,     0    ],
         [1 - gamma, gamma]]
    b = [1 - gamma, gamma]

Stability function R(z) = (1 + (1 - 2*gamma)*z) / (1 - gamma*z)**2; degree 1 over
degree 2, so R(-inf) = 0 (L-stable).

Per step: one Jacobian evaluation at (t, y) (analytic when the problem supplies
one, one-sided finite differences otherwise), one LU factorization of
M = I - h*gamma*J (shared by both stages because the diagonal is constant), then
per stage NEWTON_ITERS iterations, each one rhs evaluation plus one forward/back
substitution. rhs evaluations per step are therefore exactly 2 * NEWTON_ITERS,
plus n_states extra rhs evaluations when the Jacobian is finite-differenced.

``main()`` writes <RK_WORK_DIR>/prototypes/sdirk_curve.json: step count vs error
for euler, rk4 and sdirk2 on (a) the frozen rc_thermal problem in its float64
physical form and (b) a two-rate linear system with rate ratio 1000 defined below,
plus estimated per-step cycle costs from the EPOCH3-DESIGN.md solver terms.
"""
from __future__ import annotations

import json
import math
from typing import Callable, Sequence

from rk_harness.costmodel import M0PLUS_FAST, cycle_count
from rk_harness.evaluator import stability_extents
from rk_harness.paths import work_dir
from rk_harness.problems import FLOAT_RHS, PROBLEMS, load_fixture
from rk_harness.simulate import solve_float
from rk_harness.tableau import classical
from rk_harness.types import CostModel

# --------------------------------------------------------------------------- method constants

GAMMA: float = 1.0 - math.sqrt(2.0) / 2.0        # 0.2928932188134524; Alexander (1977)
NEWTON_ITERS: int = 3                             # fixed, for deterministic cycle cost
STAGES: int = 2

_A = ((GAMMA, 0.0), (1.0 - GAMMA, GAMMA))
_B = (1.0 - GAMMA, GAMMA)
_C = (GAMMA, 1.0)

# Estimated cycles for one Q15 pivot-reciprocal (software divide on cores without a
# divider); a design-doc estimate, not a measured number.
DIV_CYCLES: int = 32

Rhs = Callable[[float, tuple[float, ...]], tuple[float, ...]]
Jac = Callable[[float, tuple[float, ...]], list[list[float]]]


def stability_function(z: complex) -> complex:
    """Closed-form R(z) for the tableau above."""
    return (1.0 + (1.0 - 2.0 * GAMMA) * z) / (1.0 - GAMMA * z) ** 2


# --------------------------------------------------------------------------- small dense LU

def lu_factor(mat: Sequence[Sequence[float]]) -> tuple[list[list[float]], list[int]]:
    """LU with partial pivoting for the 1..4 state systems this program targets.

    Returns (packed LU, pivot row order). Pure Python on purpose: the loop mirrors
    the fixed-point routine the epoch-3 cost model prices, so operation counts can
    be read off the code.
    """
    n = len(mat)
    lu = [list(row) for row in mat]
    piv = list(range(n))
    for k in range(n):
        p = max(range(k, n), key=lambda r: abs(lu[r][k]))
        if lu[p][k] == 0.0:
            raise ZeroDivisionError("singular Newton matrix")
        if p != k:
            lu[k], lu[p] = lu[p], lu[k]
            piv[k], piv[p] = piv[p], piv[k]
        inv = 1.0 / lu[k][k]
        for r in range(k + 1, n):
            m = lu[r][k] * inv
            lu[r][k] = m
            for ccol in range(k + 1, n):
                lu[r][ccol] -= m * lu[k][ccol]
    return lu, piv


def lu_solve(lu: list[list[float]], piv: list[int], rhs_vec: Sequence[float]) -> list[float]:
    """Forward/back substitution against a factorization from lu_factor."""
    n = len(lu)
    x = [rhs_vec[piv[i]] for i in range(n)]
    for i in range(1, n):
        for j in range(i):
            x[i] -= lu[i][j] * x[j]
    for i in range(n - 1, -1, -1):
        for j in range(i + 1, n):
            x[i] -= lu[i][j] * x[j]
        x[i] /= lu[i][i]
    return x


# --------------------------------------------------------------------------- Jacobian

def fd_jacobian(rhs: Rhs, t: float, y: tuple[float, ...], f0: tuple[float, ...] | None = None) -> list[list[float]]:
    """One-sided difference Jacobian, column j from perturbing y_j.

    Costs n_states extra rhs evaluations (f0 is reused when the caller already
    has it). Step size sqrt(eps)*max(|y_j|, 1), the standard one-sided choice.
    """
    n = len(y)
    if f0 is None:
        f0 = rhs(t, y)
    jac = [[0.0] * n for _ in range(n)]
    for j in range(n):
        d = math.sqrt(2.220446049250313e-16) * max(abs(y[j]), 1.0)
        yp = list(y)
        yp[j] += d
        fp = rhs(t, tuple(yp))
        for i in range(n):
            jac[i][j] = (fp[i] - f0[i]) / d
    return jac


# --------------------------------------------------------------------------- the integrator

def sdirk2_step(rhs: Rhs, t: float, y: tuple[float, ...], h: float, jac: Jac | None = None) -> tuple[float, ...]:
    """One SDIRK2 step with exactly NEWTON_ITERS modified-Newton iterations per stage.

    The Jacobian is evaluated once at (t, y) and frozen for the whole step; both
    stages share one factorization of M = I - h*gamma*J. On linear problems the
    frozen Jacobian is exact, so the stage equations are solved to rounding after
    the very first iteration and the extra iterations change nothing; the fixed
    count exists so the cycle cost never depends on the data.
    """
    n = len(y)
    jmat = jac(t, y) if jac is not None else fd_jacobian(rhs, t, y)
    m = [[(1.0 if i == j else 0.0) - h * GAMMA * jmat[i][j] for j in range(n)] for i in range(n)]
    lu, piv = lu_factor(m)

    ks: list[list[float]] = []
    k = [0.0] * n                                   # predictor for stage 0
    for i in range(STAGES):
        base = list(y)
        for j in range(i):
            a = _A[i][j]
            for st in range(n):
                base[st] += h * a * ks[j][st]
        k = list(k)                                 # warm start from the previous stage
        for _ in range(NEWTON_ITERS):
            yi = tuple(base[st] + h * GAMMA * k[st] for st in range(n))
            f = rhs(t + _C[i] * h, yi)
            resid = [k[st] - f[st] for st in range(n)]
            delta = lu_solve(lu, piv, resid)
            for st in range(n):
                k[st] -= delta[st]
        ks.append(k)

    return tuple(y[st] + h * (_B[0] * ks[0][st] + _B[1] * ks[1][st]) for st in range(n))


def solve_sdirk2(rhs: Rhs, y0: tuple[float, ...], t_end: float, n: int, jac: Jac | None = None) -> tuple[float, ...]:
    """n fixed SDIRK2 steps of size h = t_end / n from t = 0, like simulate.solve_float."""
    h = t_end / n
    y = tuple(float(v) for v in y0)
    for kstep in range(n):
        y = sdirk2_step(rhs, kstep * h, y, h, jac=jac)
    return y


# --------------------------------------------------------------------------- local stiff test problem

# Two-rate linear system with rate ratio 1000, defined here and only here (the
# validation suite is owned elsewhere): y1' = -1000 y1, y2' = y1 - y2, y0 = (1, 1).
# Eigenvalues -1000 and -1. Closed form below.
STIFF_A = ((-1000.0, 0.0), (1.0, -1.0))
STIFF_Y0 = (1.0, 1.0)
STIFF_T_END = 2.0
STIFF_RATIO = 1000.0


def stiff_rhs(t: float, y: tuple[float, ...]) -> tuple[float, ...]:
    return (
        STIFF_A[0][0] * y[0] + STIFF_A[0][1] * y[1],
        STIFF_A[1][0] * y[0] + STIFF_A[1][1] * y[1],
    )


def stiff_jac(t: float, y: tuple[float, ...]) -> list[list[float]]:
    return [list(row) for row in STIFF_A]


def stiff_reference(t: float) -> tuple[float, ...]:
    """y1 = e^(-1000 t); y2 solves y2' = -y2 + y1 with y2(0) = 1."""
    e_fast = math.exp(-1000.0 * t)
    e_slow = math.exp(-t)
    return (e_fast, e_slow + (e_slow - e_fast) / 999.0)


# --------------------------------------------------------------------------- cost estimate

def estimate_sdirk2_cycles(n_states: int, model: CostModel, newton_iters: int = NEWTON_ITERS,
                           fd: bool = False) -> dict:
    """Per-step cycle estimate from the EPOCH3-DESIGN.md solver terms.

    Same convention as costmodel.cycle_count: rhs and analytic-Jacobian
    evaluations are excluded (application-supplied code); everything the solver
    does with their results is counted. These are design estimates, not an
    assembly-verified count like the explicit model has.
    """
    cyc = model.cycles
    mul, add, shift = cyc["mul"], cyc["add"], cyc["shift"]
    load, store = cyc["load"], cyc["store"]
    n, s, it = n_states, STAGES, newton_iters

    # M = I - h*gamma*J: one coefficient apply plus store per entry, add on the diagonal.
    form_m = n * n * (mul + shift + load + store) + n * add
    # LU of the n x n matrix, once per step: Doolittle counts for tiny n, plus one
    # software reciprocal per pivot.
    lu = (n * n * n - n) // 3 * (mul + add) + n * n * (load + store) + n * DIV_CYCLES
    # One forward/back substitution: ~n^2 multiply-adds.
    solve = n * n * (mul + add) + 2 * n * (load + store)
    # Stage base y + h*sum(a_ij k_j): stage 0 has no sub-diagonal entries, stage 1
    # has one, so one combination row total in SDIRK2.
    stage_base = (s - 1) * n * (load + store + mul + shift + add)
    # Per stage per iteration: form Y_i (apply h*gamma, add), residual, solve, update k.
    per_iter = n * (mul + shift + add) + n * add + solve + n * add
    newton = s * it * per_iter
    # Final combination over b (two non-trivial coefficients per state).
    combine_b = n * (load + store + 2 * (mul + shift + add))
    # One-sided FD Jacobian assembly (the n extra rhs evaluations are excluded like
    # all rhs evaluations; this is the subtract/scale/store arithmetic only).
    jac_fd = n * n * (add + shift + load + store) if fd else 0

    total = form_m + lu + stage_base + newton + combine_b + jac_fd
    return {
        "model": model.name,
        "n_states": n,
        "newton_iters": it,
        "terms": {
            "form_m": form_m,
            "lu_factor": lu,
            "stage_base": stage_base,
            "newton_iterations": newton,
            "combine_b": combine_b,
            "fd_jacobian_arith": jac_fd,
        },
        "total": total,
        "f_evals_per_step": s * it + (n if fd else 0),
    }


# --------------------------------------------------------------------------- error curves

_L2_DIVERGED = 1.0e3


def _l2_error(y: tuple[float, ...], ref: tuple[float, ...], peak: float) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(y, ref))) / peak


def _run_point(method: str, rhs: Rhs, jac: Jac | None, y0: tuple[float, ...],
               t_end: float, n: int, ref: tuple[float, ...], peak: float) -> dict:
    try:
        if method == "sdirk2":
            y = solve_sdirk2(rhs, y0, t_end, n, jac=jac)
        else:
            y = solve_float(classical()[method], rhs, y0, t_end, n)
        err = _l2_error(y, ref, peak)
    except (OverflowError, ZeroDivisionError):
        return {"n": n, "h": t_end / n, "error": None, "status": "diverged"}
    if not math.isfinite(err) or err > _L2_DIVERGED:
        return {"n": n, "h": t_end / n, "error": None, "status": "diverged"}
    return {"n": n, "h": t_end / n, "error": err, "status": "ok"}


def _curve(rhs: Rhs, jac: Jac | None, y0: tuple[float, ...], t_end: float,
           ref: tuple[float, ...], peak: float, ladder: Sequence[int], n_states: int) -> dict:
    cl = classical()
    methods: dict[str, dict] = {}
    for name in ("euler", "rk4", "sdirk2"):
        points = [_run_point(name, rhs, jac, y0, t_end, n, ref, peak) for n in ladder]
        if name == "sdirk2":
            cost = estimate_sdirk2_cycles(n_states, M0PLUS_FAST)
            cycles, f_evals = cost["total"], cost["f_evals_per_step"]
        else:
            cycles = cycle_count(cl[name], M0PLUS_FAST, n_states)
            f_evals = len(cl[name].b)
        stable = [p["n"] for p in points if p["status"] == "ok"]
        methods[name] = {
            "est_cycles_per_step": cycles,
            "f_evals_per_step": f_evals,
            "min_stable_n": min(stable) if stable else None,
            "points": points,
        }
    return methods


def build_curve() -> dict:
    """The sdirk_curve.json payload, as a dict. Pure function of this module."""
    fix = load_fixture()["rc_thermal"]
    rc = PROBLEMS["rc_thermal"]
    rc_a = [[float(v) for v in row] for row in fix["params"]["A"]]

    def rc_jac(t: float, y: tuple[float, ...]) -> list[list[float]]:
        return [list(row) for row in rc_a]

    rc_ref = rc.reference(rc.t_end)
    rc_y0 = (1.0, 0.0, 0.0)
    rc_ladder = (4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256)
    stiff_ladder = (5, 10, 20, 50, 100, 200, 500, 720, 1000, 2000, 5000)

    rc_methods = _curve(FLOAT_RHS["rc_thermal"], rc_jac, rc_y0, rc.t_end,
                        rc_ref, 1.0, rc_ladder, rc.n_states)
    st_methods = _curve(stiff_rhs, stiff_jac, STIFF_Y0, STIFF_T_END,
                        stiff_reference(STIFF_T_END), 1.0, stiff_ladder, 2)

    # Quantitative crossover note on the two-rate problem.
    rk4_real = abs(stability_extents(classical()["rk4"])[0])
    rk4_floor_n = math.ceil(STIFF_RATIO * STIFF_T_END / rk4_real)
    rk4_floor_cycles = rk4_floor_n * st_methods["rk4"]["est_cycles_per_step"]
    sd_tol_n = None
    for p in st_methods["sdirk2"]["points"]:
        if p["status"] == "ok" and p["error"] is not None and p["error"] <= 1.0e-3:
            sd_tol_n = p["n"]
            break
    sd_tol_cycles = (sd_tol_n or 0) * st_methods["sdirk2"]["est_cycles_per_step"]
    cost_note = (
        "Cycle estimates exclude rhs and analytic-Jacobian evaluations, matching "
        "costmodel.cycle_count. On stiff_two_rate (2 states, m0plus_fast): rk4 costs "
        f"{st_methods['rk4']['est_cycles_per_step']} cycles/step but its real-axis stability "
        f"interval of about {rk4_real:.3f} forces n >= {rk4_floor_n} before any step is stable, "
        f"a floor of about {rk4_floor_cycles} cycles at any accuracy. sdirk2 costs "
        f"{st_methods['sdirk2']['est_cycles_per_step']} cycles/step (estimate) and reaches "
        f"error 1e-3 at n = {sd_tol_n} (about {sd_tol_cycles} cycles). The implicit method "
        "pays several times more per step and still lands well under the explicit stability "
        "floor at loose and moderate tolerances; explicit methods only recover once the "
        "tolerance demands more steps than their stability floor already forces."
    )

    return {
        "label": "preliminary, float-only prototype (epoch 3 side track, off-archive)",
        "generated_by": "rk_harness.prototypes.sdirk.main; not verifier-pinned, no Q15 arithmetic",
        "method": {
            "name": "sdirk2",
            "gamma": GAMMA,
            "newton_iters": NEWTON_ITERS,
            "stages": STAGES,
            "properties": "order 2, stiffly accurate, L-stable (Alexander 1977)",
        },
        "cost_model": M0PLUS_FAST.name,
        "cost_note": cost_note,
        "error_metric": "final-state L2 distance from the reference, over peak 1.0; "
                        "'diverged' = non-finite or > 1e3",
        "problems": {
            "rc_thermal": {
                "source": "frozen scored problem (fixtures/problems.json), float64 physical form",
                "n_states": rc.n_states,
                "t_end": rc.t_end,
                "stiffness_ratio": float(fix["stiffness_ratio"]),
                "methods": rc_methods,
            },
            "stiff_two_rate": {
                "source": "defined locally in rk_harness/prototypes/sdirk.py",
                "definition": "y1' = -1000 y1; y2' = y1 - y2; y0 = (1, 1); eigenvalues -1000, -1",
                "n_states": 2,
                "t_end": STIFF_T_END,
                "stiffness_ratio": STIFF_RATIO,
                "methods": st_methods,
            },
        },
    }


def main() -> None:
    out_dir = work_dir() / "prototypes"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = build_curve()
    path = out_dir / "sdirk_curve.json"
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2, sort_keys=False)
        fh.write("\n")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
