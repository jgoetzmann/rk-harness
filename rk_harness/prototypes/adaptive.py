"""Adaptive embedded-pair prototype (epoch-2 side track, docs/EPOCH2-DESIGN.md).

Float-only working model of the epoch-2 method class: one explicit tableau A
with two weight vectors, b (order p, propagated) and b_hat (order p-1, used
only for the error estimate), plus a PI step-size controller. Everything here
is float64 and off-archive; pinned modules are imported read-only or not at
all. The point is to validate the pair, the controller gains, and the
work-precision measurement before any of it goes near the Q15 pipeline.

Reference pair: Bogacki-Shampine 3(2). P. Bogacki and L. F. Shampine,
"A 3(2) pair of Runge-Kutta formulas", Applied Mathematics Letters 2(4),
pp. 321-325, 1989. Four stages, FSAL (the last stage of an accepted step is
the next step's stage one), propagates the order-3 solution.

Controller: standard PI error-per-step control (Hairer/Wanner II.4 form,
fac = safety * err**(-alpha) * err_prev**beta) with every constant chosen
dyadic so the same gains survive a fixed-point port unchanged:
alpha = 1/4 (the classical value for a 3(2) pair is 1/3), beta = 1/8,
safety = 7/8, factor clamp [1/4, 2]. The float prototype checks that these
rounded-to-shifts gains still converge; the table-driven Q15 realization is
specified in docs/EPOCH2-DESIGN.md.

Q15 feasibility note (which quantities fit Q15, which need Q31):

* Pair coefficients. All BS32 entries of A, b, b_hat, c lie in [0, 1] and go
  through the existing CoeffRep machinery (m / 2**s, |m| <= 32767, s <= 20)
  like any epoch-1 coefficient. The error-estimate weights d_i = b_i - b_hat_i
  are (-5/72, 1/12, 1/9, -1/8); all fit CoeffRep, only -1/8 is exact.
* Error estimate. solve_q15 already stores hk[i] = q15_mul(k_i, h_q), so the
  per-state estimate is E_m = sum_i q15_apply(hk[i][m], d_i.m, d_i.s). Each
  individual term fits Q15, but the sum should accumulate in an int32 (Q31)
  register before the tolerance compare, like the int32 tmp in
  costmodel.emit_c. E_m itself is a near-LSB quantity: with floor (ASRS)
  semantics each nonzero d term carries about -0.5 LSB of bias, so with four
  terms the estimate has a bias floor of roughly 2 LSB. Tolerances below a few
  LSB of the scaled state are therefore not measurable in Q15; the scored
  tolerance ladder has to sit above that floor.
* Acceptance test. Accept iff max_m |E_m| <= tol_q with tol_q a precomputed
  Q15 constant (absolute tolerance in the scaled state). No division. A
  relative component costs one q15_mul plus one q15_add per state.
* Controller. err**(-1/4) needs neither division nor roots: take the leading
  bit position of max|E_m| against that of tol_q (ARMv6-M has no CLZ, so this
  is a short bounded software loop) and index a small precomputed Q15 factor
  table; the PI memory folds in as an integer exponent (see the design doc).
  Cost per step: two bit scans, adds, one table load, one q15_mul.
* Step size. h_q stays a Q15 scalar as in solve_q15 (h / DERIV_SCALE scaled by
  2**15); the update is one q15_mul by the table factor. Minimum step is 1 LSB
  of h_q; the controller must fail loudly below it. Elapsed time needs a Q31
  accumulator (a sum of many Q15 steps leaves int16 immediately); the harness
  sidesteps this because make_q15_rhs takes float t, but hardware and the
  eventual adaptive Q15 solver need the int32 time register in the cost model.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass

from rk_harness.paths import work_dir

# ---------------------------------------------------------------------- pair


@dataclass(frozen=True)
class EmbeddedPair:
    """Explicit embedded pair: one A, two weight vectors sharing it."""
    name: str
    A: tuple[tuple[float, ...], ...]
    b: tuple[float, ...]        # order `order`, propagated
    b_hat: tuple[float, ...]    # order `order_hat`, estimate only
    c: tuple[float, ...]
    order: int
    order_hat: int
    fsal: bool


BOGACKI_SHAMPINE_32 = EmbeddedPair(
    name="bogacki_shampine_32",
    A=(
        (0.0, 0.0, 0.0, 0.0),
        (1.0 / 2.0, 0.0, 0.0, 0.0),
        (0.0, 3.0 / 4.0, 0.0, 0.0),
        (2.0 / 9.0, 1.0 / 3.0, 4.0 / 9.0, 0.0),
    ),
    b=(2.0 / 9.0, 1.0 / 3.0, 4.0 / 9.0, 0.0),
    b_hat=(7.0 / 24.0, 1.0 / 4.0, 1.0 / 3.0, 1.0 / 8.0),
    c=(0.0, 1.0 / 2.0, 3.0 / 4.0, 1.0),
    order=3,
    order_hat=2,
    fsal=True,
)


# ---------------------------------------------------------------------- stepping


def _combine(y: tuple[float, ...], h: float, ks: list[tuple[float, ...]],
             w: tuple[float, ...]) -> tuple[float, ...]:
    """y + h * sum_i w_i k_i, coefficient sums accumulated before the one
    multiply by h (simulate.rk_step_float convention)."""
    n = len(y)
    acc = [0.0] * n
    for i, wi in enumerate(w):
        if wi != 0.0:
            ki = ks[i]
            for m in range(n):
                acc[m] += wi * ki[m]
    return tuple(y[m] + h * acc[m] for m in range(n))


def embedded_step(pair: EmbeddedPair, rhs, t: float, y: tuple[float, ...], h: float,
                  k1: tuple[float, ...] | None = None):
    """One embedded step. Returns (y_high, err_vec, ks).

    y_high propagates with pair.b; err_vec = h * sum_i (b_i - b_hat_i) * k_i,
    which equals y_high - y_hat. Pass k1 to reuse a stage-one derivative
    (FSAL after an acceptance, or an unchanged y after a rejection).
    """
    s = len(pair.b)
    n = len(y)
    ks: list[tuple[float, ...]] = []
    for i in range(s):
        if i == 0 and k1 is not None:
            ks.append(k1)
            continue
        acc = [0.0] * n
        row = pair.A[i]
        for j in range(i):
            a = row[j]
            if a != 0.0:
                kj = ks[j]
                for m in range(n):
                    acc[m] += a * kj[m]
        yi = tuple(y[m] + h * acc[m] for m in range(n))
        ks.append(tuple(rhs(t + pair.c[i] * h, yi)))
    y_high = _combine(y, h, ks, pair.b)
    err = tuple(
        h * sum((pair.b[i] - pair.b_hat[i]) * ks[i][m] for i in range(s))
        for m in range(n)
    )
    return y_high, err, ks


def solve_fixed(pair: EmbeddedPair, rhs, y0: tuple[float, ...], t_end: float, n: int,
                weights: tuple[float, ...] | None = None) -> tuple[float, ...]:
    """n equal steps from t = 0, propagating with `weights` (default pair.b).

    Order-measurement helper: no controller, no FSAL reuse. Passing
    pair.b_hat measures the embedded formula's own order.
    """
    w = pair.b if weights is None else weights
    h = t_end / n
    y = tuple(float(v) for v in y0)
    for k in range(n):
        _, _, ks = embedded_step(pair, rhs, k * h, y, h)
        y = _combine(y, h, ks, w)
    return y


# ---------------------------------------------------------------------- controller

# Dyadic PI gains; see the module docstring and docs/EPOCH2-DESIGN.md.
ALPHA = 0.25       # 1/4, I gain (classical 1/(order_hat + 1) = 1/3 for a 3(2) pair)
BETA = 0.125       # 1/8, P gain on the previous accepted error
SAFETY = 0.875     # 7/8
FAC_MIN = 0.25     # 1/4
FAC_MAX = 2.0
_ERR_FLOOR = 1e-10  # keeps err**(-ALPHA) finite; the clamp makes the exact value moot


@dataclass(frozen=True)
class AdaptiveResult:
    y: tuple[float, ...]
    t: float
    n_accepted: int
    n_rejected: int
    n_fevals: int
    h_final: float


def _error_norm(err: tuple[float, ...], y: tuple[float, ...],
                y_new: tuple[float, ...], rtol: float, atol: float) -> float:
    """RMS of err scaled per state by atol + rtol * max(|y|, |y_new|)."""
    n = len(err)
    acc = 0.0
    for m in range(n):
        sc = atol + rtol * max(abs(y[m]), abs(y_new[m]))
        r = err[m] / sc
        acc += r * r
    return math.sqrt(acc / n)


def solve_adaptive(pair: EmbeddedPair, rhs, y0: tuple[float, ...], t0: float,
                   t_end: float, rtol: float, atol: float,
                   h0: float | None = None, max_attempts: int = 1_000_000) -> AdaptiveResult:
    """Integrate to t_end with PI step control. A step whose scaled error norm
    is <= 1 is accepted; otherwise it repeats with a smaller h and the
    rejection is counted. Function evaluations are counted exactly (FSAL
    reuse after an acceptance, unchanged stage one after a rejection).
    """
    span = t_end - t0
    if span <= 0.0:
        raise ValueError("t_end must exceed t0")
    h = h0 if h0 is not None else span / 64.0
    h_min = span * 1e-12
    t = t0
    y = tuple(float(v) for v in y0)
    k1: tuple[float, ...] | None = None
    err_prev = 1.0
    n_acc = n_rej = n_fev = 0
    attempts = 0
    while t_end - t > span * 1e-12:
        if h < h_min:
            raise RuntimeError(f"step size underflow at t={t} (h={h})")
        attempts += 1
        if attempts > max_attempts:
            raise RuntimeError(f"exceeded {max_attempts} step attempts")
        h = min(h, t_end - t)
        y_new, err_vec, ks = embedded_step(pair, rhs, t, y, h, k1=k1)
        n_fev += len(pair.b) - (1 if k1 is not None else 0)
        e = _error_norm(err_vec, y, y_new, rtol, atol)
        if e <= 1.0:
            t += h
            y = y_new
            n_acc += 1
            k1 = ks[-1] if pair.fsal else None
            fac = SAFETY * max(e, _ERR_FLOOR) ** (-ALPHA) * err_prev ** BETA
            err_prev = max(e, _ERR_FLOOR)
            h *= min(FAC_MAX, max(FAC_MIN, fac))
        else:
            n_rej += 1
            k1 = ks[0]                        # y unchanged, stage one still valid
            fac = SAFETY * e ** (-ALPHA)
            h *= min(1.0, max(FAC_MIN, fac))  # never grow on a rejection
    return AdaptiveResult(y=y, t=t, n_accepted=n_acc, n_rejected=n_rej,
                          n_fevals=n_fev, h_final=h)


# ---------------------------------------------------------------------- work-precision curve

CURVE_PROBLEMS: tuple[str, ...] = ("buck_converter", "pll_lock", "glucose_minimal")
CURVE_TOLS: tuple[float, ...] = (1e-3, 1e-4, 1e-5, 1e-6, 1e-7, 1e-8)


def curve_point(name: str, tol: float) -> dict:
    """One measured point: run BS32 adaptively on a T8 validation problem at
    rtol = atol = tol, report the cost counters and the achieved final error
    under the suite's own metric (validation.validation_error)."""
    from rk_harness import validation as V   # read-only; lazy so the pair core stays light

    rhs = V.FLOAT_RHS[name]
    y0 = V.Y0_PHYS[name]
    t_end = V.PROBLEMS[name].t_end
    res = solve_adaptive(BOGACKI_SHAMPINE_32, rhs, y0, 0.0, t_end,
                         rtol=tol, atol=tol, h0=t_end / 64.0)
    return {
        "problem": name,
        "tol": tol,
        "n_accepted": res.n_accepted,
        "n_rejected": res.n_rejected,
        "n_fevals": res.n_fevals,
        "achieved_error": V.validation_error(name, res.y),
    }


def build_curve(problems: tuple[str, ...] = CURVE_PROBLEMS,
                tols: tuple[float, ...] = CURVE_TOLS) -> dict:
    """The full curve document. Deterministic: a pure function of this module
    and rk_harness.validation; no clock, no host detail."""
    points = [curve_point(name, tol) for name in problems for tol in tols]
    return {
        "schema": {
            "points": "one entry per (problem, tol): n_accepted / n_rejected "
                      "steps, n_fevals (exact count, FSAL reuse included), "
                      "achieved_error (L2 final-state error against the "
                      "reference divided by the problem PEAK, the T8 metric)",
            "pair": "the embedded pair used for every point",
            "controller": "PI controller constants (all dyadic)",
        },
        "status": "preliminary",
        "arithmetic": "float64 only; no Q15 effects are included",
        "caveats": [
            "float-only prototype; Q15 quantization, floor bias, and the "
            "controller's table realization are not modeled",
            "single literature pair (Bogacki-Shampine 3(2)); no discovered pairs",
            "n_fevals counts right-hand-side calls, not cycles; the analytic "
            "cycle model for pairs lands with epoch 2",
            "controller gains are the dyadic values intended for the Q15 port, "
            "not the classical optimum",
        ],
        "pair": {
            "name": BOGACKI_SHAMPINE_32.name,
            "stages": len(BOGACKI_SHAMPINE_32.b),
            "order": BOGACKI_SHAMPINE_32.order,
            "embedded_order": BOGACKI_SHAMPINE_32.order_hat,
            "fsal": BOGACKI_SHAMPINE_32.fsal,
            "citation": "P. Bogacki, L. F. Shampine, A 3(2) pair of Runge-Kutta "
                        "formulas, Applied Mathematics Letters 2(4):321-325, 1989",
        },
        "controller": {
            "type": "PI",
            "alpha": ALPHA,
            "beta": BETA,
            "safety": SAFETY,
            "factor_min": FAC_MIN,
            "factor_max": FAC_MAX,
            "h0": "t_end / 64",
            "error_norm": "RMS of per-state error / (atol + rtol * max(|y|, |y_new|))",
        },
        "problems": list(problems),
        "tolerances": list(tols),
        "points": points,
        "generated_by": "python -m rk_harness.prototypes.adaptive",
    }


def write_curve(problems: tuple[str, ...] = CURVE_PROBLEMS,
                tols: tuple[float, ...] = CURVE_TOLS):
    """Write the curve to work_dir()/prototypes/adaptive_curve.json; returns the path."""
    out_dir = work_dir() / "prototypes"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "adaptive_curve.json"
    doc = build_curve(problems, tols)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main() -> int:
    path = write_curve()
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
