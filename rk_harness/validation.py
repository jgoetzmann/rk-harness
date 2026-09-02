"""Practical validation suite (T8). New module; nothing pinned is modified.

Five practical problems from embedded application domains (power electronics,
battery management, vehicle dynamics, communications clocking, biomedical
devices), defined per the research agent's equation spec and run through the
SAME machinery the scored suite uses:

* Q15 right-hand sides come from ``problems.make_q15_rhs`` (float64 physical
  derivative wrapped into Q15 with a power-of-two scale),
* fixed-point integration is ``simulate.solve_q15`` (floor / ASRS semantics,
  overflow raises),
* the float64 control run at the same step count is ``simulate.solve_float``,
* the cycle budget maps to a step count via ``simulate.steps_for_budget`` with
  ``costmodel.M0PLUS_FAST``, exactly as the scored evaluator does.

The error metric mirrors the default branch of ``problems.error_metric``:
L2 distance of the final state from the reference solution divided by PEAK,
where PEAK is the largest per-state |y_i| over the window measured by float
RK4 at 40,000 steps (the fixture convention; values hardcoded below like
``fixtures/problems.json`` hardcodes its peaks).

All five problems use derivative scale 1.0.  Their names are deliberately
disjoint from the frozen scored problems, so ``problems.DERIV_SCALE.get(name,
1.0)`` inside ``solve_q15`` resolves to exactly the value in this module's own
``DERIV_SCALE`` map (asserted at import).

``main()`` evaluates the classical anchors (euler, heun2, midpoint, rk4, rk38)
plus the top discovered methods from the live archive (the overall champion,
identified by the tableau hash published in rk-overview/tools/key_findings.json
and fetched from the archive records, and the lowest-heldout-error discovered
elite per symbolic order) on every practical problem at a budget of 65,536
cycles under m0plus_fast, and writes ``<RK_WORK_DIR>/validation/results.json``.

Determinism: the output is a pure function of the archive plus this spec.  No
wall clock, hostname, or environment detail is written; the integrator stamps
context in git.

Note on glucose_minimal: the research spec's printed equation carries the
forcing coefficient 0.01282, but its own measured numbers (y2 peak 1.274,
max|dy|*scale 0.160) and the physical derivation both give 0.1282
(y2 = 100*X, forcing = 100 * p3 * 100 uU/ml = 100 * 1.282e-5 * 100 = 0.1282
per minute).  This module uses 0.1282, which reproduces every measured value
in the spec.
"""
from __future__ import annotations

import functools
import json
import math
import os
from pathlib import Path

import numpy as np
from mpmath import mp
from scipy.linalg import expm

from rk_harness.archive import read_all, record_order
from rk_harness.costmodel import M0PLUS_FAST, cycle_count
from rk_harness.orderconditions import achieved_order_symbolic
from rk_harness.paths import HARNESS_DIR, work_dir
from rk_harness.problems import DERIV_SCALE as _FROZEN_DERIV_SCALE
from rk_harness.problems import make_q15_rhs, to_physical, to_q15_state
from rk_harness.simulate import (
    float_tableau,
    rk_step_float,
    solve_float,
    solve_q15,
    steps_for_budget,
)
from rk_harness.tableau import classical, content_hash, stages, to_json
from rk_harness.types import Problem, Record, Tableau

BUDGET_CYCLES = 65536
COST_MODEL = M0PLUS_FAST
CLASSICAL_ANCHOR_NAMES: tuple[str, ...] = ("euler", "heun2", "midpoint", "rk4", "rk38")

VALIDATION_NAMES: tuple[str, ...] = (
    "buck_converter", "battery_2rc", "bicycle_lateral", "pll_lock", "glucose_minimal",
)

# The suite's own derivative-scale map (the solve_q15 default of 1.0 applies
# because these names never appear in the frozen problems.DERIV_SCALE).
DERIV_SCALE: dict[str, float] = {name: 1.0 for name in VALIDATION_NAMES}

assert not set(VALIDATION_NAMES) & set(_FROZEN_DERIV_SCALE), (
    "validation problem names must not shadow frozen scored problems"
)


# --------------------------------------------------------------------------- parameters

# buck_converter: 12 V -> 5 V averaged CCM buck (Erickson & Maksimovic), per-unit.
_BUCK_D = 5.0 / 12.0        # duty cycle
_BUCK_QF = 2.0              # Qf = R / Z0

# battery_2rc: Chen & Rincon-Mora 2006, 850 mAh cell at SOC 0.5, 2C discharge.
_BAT_T = 20.0               # time unit, seconds
_BAT_I = 1.7                # A (2C)
_BAT_CN = 0.85              # Ah
_BAT_R1, _BAT_C1 = 0.0467, 703.0     # transient S branch: tau1 = 32.8 s
_BAT_R2, _BAT_C2 = 0.0498, 4475.0    # transient L branch: tau2 = 222.9 s

# bicycle_lateral: dynamic single-track model, CarSim C-Class parameters
# (Ge et al., arXiv:2011.09612 Table I), constant 30 m/s, step steer 0.01 rad.
_BIC_M = 1412.0
_BIC_IZ = 1536.7
_BIC_LF = 1.06
_BIC_LR = 1.85
_BIC_KF = -128916.0
_BIC_KR = -85944.0
_BIC_U = 30.0
_BIC_DELTA = 0.01
_BIC_T = 0.1                # time unit, seconds

# pll_lock: type-2 analog PLL, Kuznetsov et al. arXiv:1705.05013 model (15).
_PLL_KVCO = 1.0
_PLL_TAU1 = 1.0
_PLL_TAU2 = 1.4
_PLL_WFREE = 0.5

# glucose_minimal: Bergman MINMOD, Pacini & Bergman 1986 fitted values.
_GLU_P1 = 0.0265
_GLU_P2 = 0.0254
_GLU_GB_N = 0.82            # Gb / 100
_GLU_FORCE = 0.1282         # 100 * p3 * 100 uU/ml (see module docstring)


# --------------------------------------------------------------------------- float64 derivatives

def _rhs_buck_converter(t: float, y: tuple[float, ...]) -> tuple[float, ...]:
    x1, x2 = y[0], y[1]
    return (_BUCK_D - x2, x1 - x2 / _BUCK_QF)


def _rhs_battery_2rc(t: float, y: tuple[float, ...]) -> tuple[float, ...]:
    _dod, v1, v2 = y[0], y[1], y[2]
    return (
        _BAT_T * _BAT_I / (3600.0 * _BAT_CN),
        _BAT_T * (-v1 / (_BAT_R1 * _BAT_C1) + _BAT_I / _BAT_C1),
        _BAT_T * (-v2 / (_BAT_R2 * _BAT_C2) + _BAT_I / _BAT_C2),
    )


def _rhs_bicycle_lateral(t: float, y: tuple[float, ...]) -> tuple[float, ...]:
    v, w = y[0], y[1]
    fy1 = _BIC_KF * ((v + _BIC_LF * w) / _BIC_U - _BIC_DELTA)
    fy2 = _BIC_KR * (v - _BIC_LR * w) / _BIC_U
    return (
        _BIC_T * (-_BIC_U * w + (fy1 + fy2) / _BIC_M),
        _BIC_T * (_BIC_LF * fy1 - _BIC_LR * fy2) / _BIC_IZ,
    )


def _rhs_pll_lock(t: float, y: tuple[float, ...]) -> tuple[float, ...]:
    theta_e, x = y[0], y[1]
    return (
        _PLL_WFREE - _PLL_KVCO * (x + (_PLL_TAU2 / _PLL_TAU1) * math.sin(theta_e)),
        math.sin(theta_e) / _PLL_TAU1,
    )


def _rhs_glucose_minimal(t: float, y: tuple[float, ...]) -> tuple[float, ...]:
    y1, y2 = y[0], y[1]
    return (
        10.0 * (-0.01 * y1 * y2 + _GLU_P1 * (_GLU_GB_N - y1)),
        10.0 * (-_GLU_P2 * y2 + _GLU_FORCE * math.exp(-0.5 * t)),
    )


FLOAT_RHS = {
    "buck_converter": _rhs_buck_converter,
    "battery_2rc": _rhs_battery_2rc,
    "bicycle_lateral": _rhs_bicycle_lateral,
    "pll_lock": _rhs_pll_lock,
    "glucose_minimal": _rhs_glucose_minimal,
}


# --------------------------------------------------------------------------- references

def _augmented_ref(aug: np.ndarray, y0: tuple[float, ...]):
    """Closed form for affine y' = M y + g via the augmented matrix exponential
    (same pattern as the frozen _ref_dc_motor)."""
    n = len(y0)

    def ref(t: float) -> tuple[float, ...]:
        if t == 0.0:
            return y0
        v = expm(aug * float(t)) @ np.array(list(y0) + [1.0], dtype=float)
        return tuple(float(v[i]) for i in range(n))

    return ref


_BUCK_AUG = np.array(
    [
        [0.0, -1.0, _BUCK_D],
        [1.0, -1.0 / _BUCK_QF, 0.0],
        [0.0, 0.0, 0.0],
    ],
    dtype=float,
)
_ref_buck_converter = _augmented_ref(_BUCK_AUG, (0.0, 0.0))


def _ref_battery_2rc(t: float) -> tuple[float, ...]:
    """Exact closed form: linear ramp plus two independent RC charge curves."""
    dod = t * _BAT_T * _BAT_I / (3600.0 * _BAT_CN)
    v1 = _BAT_I * _BAT_R1 * (1.0 - math.exp(-t * _BAT_T / (_BAT_R1 * _BAT_C1)))
    v2 = _BAT_I * _BAT_R2 * (1.0 - math.exp(-t * _BAT_T / (_BAT_R2 * _BAT_C2)))
    return (dod, v1, v2)


# bicycle_lateral affine form derived from the physical constants.
_BIC_A11 = _BIC_T * (_BIC_KF + _BIC_KR) / (_BIC_M * _BIC_U)
_BIC_A12 = _BIC_T * (-_BIC_U + (_BIC_KF * _BIC_LF - _BIC_KR * _BIC_LR) / (_BIC_M * _BIC_U))
_BIC_G1 = _BIC_T * (-_BIC_KF * _BIC_DELTA) / _BIC_M
_BIC_A21 = _BIC_T * (_BIC_KF * _BIC_LF - _BIC_KR * _BIC_LR) / (_BIC_IZ * _BIC_U)
_BIC_A22 = _BIC_T * (_BIC_KF * _BIC_LF * _BIC_LF + _BIC_KR * _BIC_LR * _BIC_LR) / (_BIC_IZ * _BIC_U)
_BIC_G2 = _BIC_T * (-_BIC_LF * _BIC_KF * _BIC_DELTA) / _BIC_IZ

_BIC_AUG = np.array(
    [
        [_BIC_A11, _BIC_A12, _BIC_G1],
        [_BIC_A21, _BIC_A22, _BIC_G2],
        [0.0, 0.0, 0.0],
    ],
    dtype=float,
)
_ref_bicycle_lateral = _augmented_ref(_BIC_AUG, (0.0, 0.0))


@functools.lru_cache(maxsize=None)
def _pll_solution():
    with mp.workdps(30):
        def F(t, y):
            return [
                mp.mpf("0.5") - (y[1] + mp.mpf("1.4") * mp.sin(y[0])),
                mp.sin(y[0]),
            ]
        return mp.odefun(F, 0, [mp.mpf(0), mp.mpf(0)])


@functools.lru_cache(maxsize=None)
def _ref_pll_lock(t: float) -> tuple[float, ...]:
    with mp.workdps(30):
        sol = _pll_solution()
        v = sol(mp.mpf(t))
        return (float(v[0]), float(v[1]))


@functools.lru_cache(maxsize=None)
def _glucose_solution():
    with mp.workdps(30):
        def F(t, y):
            return [
                10 * (-mp.mpf("0.01") * y[0] * y[1] + mp.mpf("0.0265") * (mp.mpf("0.82") - y[0])),
                10 * (-mp.mpf("0.0254") * y[1] + mp.mpf("0.1282") * mp.e ** (-t / 2)),
            ]
        return mp.odefun(F, 0, [mp.mpf("2.79"), mp.mpf(0)])


@functools.lru_cache(maxsize=None)
def _ref_glucose_minimal(t: float) -> tuple[float, ...]:
    with mp.workdps(30):
        sol = _glucose_solution()
        v = sol(mp.mpf(t))
        return (float(v[0]), float(v[1]))


REFERENCE = {
    "buck_converter": _ref_buck_converter,
    "battery_2rc": _ref_battery_2rc,
    "bicycle_lateral": _ref_bicycle_lateral,
    "pll_lock": _ref_pll_lock,
    "glucose_minimal": _ref_glucose_minimal,
}


# --------------------------------------------------------------------------- peaks (fixture convention)

# Per-state max |y_i| over the window, float RK4 at 40,000 steps (y0 included),
# measured once and hardcoded exactly like fixtures/problems.json does.
PER_STATE_PEAKS: dict[str, tuple[float, ...]] = {
    "buck_converter": (0.4685375837, 0.6018100881),
    "battery_2rc": (0.0666666667, 0.0773372299, 0.0352488297),
    "bicycle_lateral": (0.2800102694, 0.0831416553),
    "pll_lock": (0.2305311157, 0.5234280583),
    "glucose_minimal": (2.79, 1.2741375800),
}

PEAK: dict[str, float] = {name: max(v) for name, v in PER_STATE_PEAKS.items()}


def measure_peaks(name: str, n: int = 40000) -> tuple[float, ...]:
    """Per-state max |y_i| over the trajectory (y0 included), float RK4, n steps."""
    A, b, c = float_tableau(classical()["rk4"])
    rhs = FLOAT_RHS[name]
    y0 = Y0_PHYS[name]
    t_end = PROBLEMS[name].t_end
    h = t_end / n
    y = tuple(y0)
    peaks = [abs(v) for v in y]
    for k in range(n):
        y = rk_step_float(A, b, c, rhs, k * h, y, h)
        for m, v in enumerate(y):
            if abs(v) > peaks[m]:
                peaks[m] = abs(v)
    return tuple(peaks)


# --------------------------------------------------------------------------- problem construction

_SPEC = {
    # name: (y0_phys, t_end, scale, family)
    "buck_converter": ((0.0, 0.0), 25.0, 0.25, "oscillatory"),
    "battery_2rc": ((0.0, 0.0, 0.0), 6.0, 8.0, "linear"),
    "bicycle_lateral": ((0.0, 0.0), 15.0, 1.0, "linear"),
    "pll_lock": ((0.0, 0.0), 10.0, 0.5, "nonlinear"),
    "glucose_minimal": ((2.79, 0.0), 12.0, 0.125, "nonlinear"),
}

Y0_PHYS: dict[str, tuple[float, ...]] = {name: s[0] for name, s in _SPEC.items()}
SCALE: dict[str, float] = {name: s[2] for name, s in _SPEC.items()}


def _make(name: str) -> Problem:
    y0_phys, t_end, scale, family = _SPEC[name]
    return Problem(
        name=name,
        n_states=len(y0_phys),
        f=make_q15_rhs(FLOAT_RHS[name], scale, DERIV_SCALE[name]),
        y0=to_q15_state(y0_phys, scale),
        t_end=t_end,
        scale=scale,
        reference=REFERENCE[name],
        family=family,
    )


PROBLEMS: dict[str, Problem] = {name: _make(name) for name in VALIDATION_NAMES}


# Site-facing metadata (domain, source, window). Prose here feeds the findings
# site, so it must stay clear of the banned-word list and use no em dashes.
PROBLEM_META: dict[str, dict] = {
    "buck_converter": {
        "domain": "power electronics",
        "source": (
            "Averaged CCM buck converter model, R. W. Erickson and D. Maksimovic, "
            "Fundamentals of Power Electronics, 2nd ed., Kluwer, 2001; the same averaged "
            "equations appear in arXiv:2002.02544. Per-unit normalization with "
            "Vg = 12 V, D = 5/12, L = 100 uH, C = 100 uF, R = 2 ohm "
            "(Z0 = 1 ohm, Qf = 2, omega0 = 1e4 rad/s). Startup transient from rest."
        ),
        "equation": "x1' = D - x2; x2' = x1 - x2/Qf (D = 5/12, Qf = 2)",
        "reference": "closed form via matrix exponential of the augmented affine system",
        "window": {"t_end": 25.0, "time_unit": "1/omega0 = 0.1 ms", "real_span": "2.5 ms"},
    },
    "battery_2rc": {
        "domain": "battery management",
        "source": (
            "Li-ion Thevenin 2RC equivalent circuit, M. Chen and G. A. Rincon-Mora, "
            "IEEE Trans. Energy Conversion 21(2):504-511, 2006, parameters for the "
            "850 mAh TCL PL-383562 cell at SOC 0.5: R1 = 0.0467 ohm, C1 = 703 F, "
            "R2 = 0.0498 ohm, C2 = 4475 F. Constant 1.7 A (2C) discharge pulse is "
            "our scenario choice. States (dod, v1, v2), time unit 20 s."
        ),
        "equation": "dod' = T*I/(3600*Cn); vk' = T*(-vk/(Rk*Ck) + I/Ck), k = 1, 2",
        "reference": "exact closed form: linear ramp plus two RC charge curves",
        "window": {"t_end": 6.0, "time_unit": "20 s", "real_span": "120 s"},
        "notes": (
            "scale 8 exceeds the 2x-headroom convention deliberately: all three states "
            "are monotone and bounded (|y*scale| < 0.68 always) and per-step increments "
            "must stay at 2 LSB or more; at scale 4 the Q15 integrator stalls. "
            "v2 is partially quantization limited under cheap methods, analogous to the "
            "rc_thermal floor-bias finding."
        ),
    },
    "bicycle_lateral": {
        "domain": "vehicle dynamics / ADAS",
        "source": (
            "Linear-tire dynamic single-track model, Q. Ge, S. E. Li, Q. Sun, S. Zheng, "
            "arXiv:2011.09612 (2020), eqs. (1) and (3), model attributed to R. Rajamani, "
            "Vehicle Dynamics and Control, Springer 2011; Table I parameters (CarSim "
            "C-Class Hatchback 2017): m = 1412 kg, Iz = 1536.7 kg m^2, lf = 1.06 m, "
            "lr = 1.85 m, kf = -128916 N/rad, kr = -85944 N/rad. Constant 30 m/s, "
            "step steer 0.01 rad. States (v, w), time unit 0.1 s."
        ),
        "equation": (
            "v' = T*(-u*w + (Fy1+Fy2)/m); w' = T*(lf*Fy1 - lr*Fy2)/Iz; "
            "Fy1 = kf*((v+lf*w)/u - delta), Fy2 = kr*(v-lr*w)/u"
        ),
        "reference": "closed form via matrix exponential of the augmented affine system",
        "window": {"t_end": 15.0, "time_unit": "0.1 s", "real_span": "1.5 s"},
    },
    "pll_lock": {
        "domain": "communications / clocking",
        "source": (
            "Second-order type-2 analog PLL with active PI filter and sinusoidal phase "
            "detector, N. V. Kuznetsov, G. A. Leonov, M. V. Yuldashev, R. V. Yuldashev, "
            "arXiv:1705.05013, model (15). Kvco = 1, tau1 = 1, tau2 = 1.4 "
            "(wn = 1 rad/s, zeta = 0.7), frequency step w_free = 0.5 rad/s inside the "
            "lock-in range (no cycle slip). States (theta_e, x)."
        ),
        "equation": (
            "theta_e' = w_free - Kvco*(x + (tau2/tau1)*sin(theta_e)); "
            "x' = sin(theta_e)/tau1"
        ),
        "reference": "mpmath odefun at 30 digits (no closed form)",
        "window": {"t_end": 10.0, "time_unit": "1 s", "real_span": "10 s"},
    },
    "glucose_minimal": {
        "domain": "biomedical devices",
        "source": (
            "Bergman glucose minimal model (MINMOD) for an IVGTT, Pacini and Bergman, "
            "Comput. Methods Programs Biomed. 1986, via the MLAB worked example "
            "(civilized.com/mlabexamples/glucose.htmld/): p1 = 0.0265, p2 = 0.0254, "
            "p3 = 1.282e-5, Gb = 82 mg/dl, Ib = 8 uU/ml, G0 = 279 mg/dl. The insulin "
            "input is idealized as I(t) = Ib + 100*exp(-0.05*t_min) uU/ml (our modeling "
            "choice; the source drives it from measured data). Normalized y1 = G/100, "
            "y2 = 100*X, time unit 10 min. Only time-dependent RHS in the suite."
        ),
        "equation": (
            "y1' = 10*(-0.01*y1*y2 + 0.0265*(0.82 - y1)); "
            "y2' = 10*(-0.0254*y2 + 0.1282*exp(-0.5*t))"
        ),
        "reference": "mpmath odefun at 30 digits (no closed form)",
        "window": {"t_end": 12.0, "time_unit": "10 min", "real_span": "120 min"},
        "notes": (
            "y0 = (2.79, 0); the Q15 rounding of 2.79*scale is 0.4 LSB and negligible."
        ),
    },
}


# --------------------------------------------------------------------------- error metric

def validation_error(name: str, y_final_phys: tuple[float, ...]) -> float:
    """L2 final-state error against the reference, divided by PEAK (the default
    branch of the frozen problems.error_metric, applied to this suite)."""
    p = PROBLEMS[name]
    ref = p.reference(p.t_end)
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(y_final_phys, ref))) / PEAK[name]


# --------------------------------------------------------------------------- evaluation

def evaluate_pair(t: Tableau, name: str) -> dict:
    """One (method, problem) cell: Q15 error and float64 error at the same step
    count, budgeted at BUDGET_CYCLES under COST_MODEL. Never raises; a Q15
    overflow becomes q15_error None with a note."""
    p = PROBLEMS[name]
    n = steps_for_budget(t, COST_MODEL, p.n_states, BUDGET_CYCLES)
    out: dict = {
        "problem": name,
        "steps": n,
        "cycles_per_step": cycle_count(t, COST_MODEL, p.n_states),
        "q15_error": None,
        "float_error": None,
        "max_abs_q": None,
    }
    if n <= 0:
        out["note"] = "method too expensive for the budget"
        return out
    try:
        final_q, max_q = solve_q15(t, p, n)
        e = validation_error(name, to_physical(final_q, p.scale))
        out["q15_error"] = e if math.isfinite(e) else None
        out["max_abs_q"] = int(max_q)
    except Exception as exc:  # Q15OverflowError, chiefly
        out["note"] = f"q15 run failed: {exc.__class__.__name__}"
    try:
        y = solve_float(t, FLOAT_RHS[name], Y0_PHYS[name], p.t_end, n)
        e = validation_error(name, y)
        out["float_error"] = e if math.isfinite(e) else None
    except Exception as exc:
        out["note"] = out.get("note", "") + f" float run failed: {exc.__class__.__name__}"
    return out


def method_order(t: Tableau) -> int:
    """Same convention as archive.record_order: symbolic order capped at 4."""
    return min(achieved_order_symbolic(t, max_order=5), 4)


# --------------------------------------------------------------------------- method selection

@functools.lru_cache(maxsize=1)
def classical_hashes() -> tuple[tuple[str, str], ...]:
    """(name, content_hash) for every fixture tableau (all 8, not only the 5
    anchors evaluated here, so discovered selection excludes every seed)."""
    return tuple((name, content_hash(t)) for name, t in sorted(classical().items()))


def key_findings_path() -> Path:
    env = os.environ.get("RK_OVERVIEW_DIR")
    base = Path(env) if env else HARNESS_DIR.parent / "rk-overview"
    return base / "tools" / "key_findings.json"


def champion_hash(path: Path | str | None = None) -> str:
    """Tableau hash of the overall champion, read from key_findings.json
    (efficiency.numbers.best_discovered.tableau_hash)."""
    p = Path(path) if path is not None else key_findings_path()
    with open(p, "r", encoding="utf-8") as fh:
        d = json.load(fh)
    return str(d["efficiency"]["numbers"]["best_discovered"]["tableau_hash"])


def select_discovered(records: list[Record], champ: str) -> list[tuple[Record, list[str]]]:
    """The champion record plus the lowest-heldout-error discovered record per
    symbolic order, deduplicated by tableau hash.

    'Discovered' means the tableau hash is not one of the 8 classical fixture
    hashes (the anchors are seeded into the archive at cycle 0).  Per order,
    strictly lower heldout error wins and ties keep the earliest record, the
    same rule the archive grids use, so the per-order pick equals the best
    elite of that order's grid.  Raises ValueError if the champion hash is not
    in the archive.
    """
    cls = {h for _, h in classical_hashes()}
    champion: Record | None = None
    best: dict[int, Record] = {}
    for r in records:
        if champion is None and r.tableau_hash == champ:
            champion = r
        if r.tableau_hash in cls:
            continue
        o = record_order(r)
        inc = best.get(o)
        if inc is None or r.score.heldout_error < inc.score.heldout_error:
            best[o] = r
    if champion is None:
        raise ValueError(f"champion tableau {champ} not found in the archive")
    picked: dict[str, tuple[Record, list[str]]] = {
        champion.tableau_hash: (champion, ["champion"])
    }
    for o in sorted(best):
        r = best[o]
        role = f"best_elite_order_{o}"
        if r.tableau_hash in picked:
            picked[r.tableau_hash][1].append(role)
        else:
            picked[r.tableau_hash] = (r, [role])
    return list(picked.values())


# --------------------------------------------------------------------------- results document

_SCHEMA_DOC = {
    "generated_from": (
        "archive_records: how many archive records were read; verifier_hash: content "
        "of the pinned VERIFIER_HASH file; champion_hash: tableau hash taken from "
        "rk-overview/tools/key_findings.json and resolved against the archive"
    ),
    "budget_cycles": "shared cycle budget per problem run",
    "cost_model": "cost model used for the budget -> step-count mapping",
    "rounding": "Q15 multiply semantics (floor / ASRS, per HANDOFF 4.2)",
    "problems": (
        "one entry per practical problem: name, domain, source, equation, reference, "
        "family, scale (power of two), deriv_scale (1.0 for all five), y0 in physical "
        "units, peak (max per-state |y| over the window, float RK4 at 40000 steps), "
        "per_state_peaks, window {t_end, time_unit, real_span}, optional notes"
    ),
    "methods": (
        "one entry per evaluated method: name_or_hash (classical fixture name, or the "
        "tableau content hash for discovered methods), kind classical|discovered, roles "
        "(anchor, champion, best_elite_order_k), order (symbolic, capped at 4, the "
        "archive convention), stages, cycles_per_step {problem: cycles for that "
        "problem's state count under the cost model}, steps {problem: budgeted step "
        "count}, tableau (A, b, c as exact fraction strings); discovered methods also "
        "carry archive {cycle_id, tier, heldout_error, search_error, verifier_hash}"
    ),
    "results": (
        "one row per (method, problem): q15_error is the L2 final-state error of the "
        "Q15 run against the reference divided by peak; float_error is the same metric "
        "for a float64 run of the same tableau at the same step count, so the gap "
        "between the two is the cost of Q15 quantization; max_abs_q is the largest "
        "|q| seen anywhere in the Q15 run (32767 is the int16 limit); errors are null "
        "if the run overflowed or the metric was not finite"
    ),
    "verdicts": (
        "per_problem: for each problem the overall winner and the best classical and "
        "best discovered entries by q15_error, with their ratio "
        "(discovered/classical, below 1.0 means the discovered method is ahead); "
        "overall: honest summary phrased from the numbers, plus the scalar facts it "
        "is derived from"
    ),
}


def _read_verifier_hash() -> str:
    return (HARNESS_DIR / "VERIFIER_HASH").read_text(encoding="utf-8").strip()


def _method_entry(name_or_hash: str, kind: str, roles: list[str], t: Tableau,
                  extra: dict | None = None) -> dict:
    entry = {
        "name_or_hash": name_or_hash,
        "kind": kind,
        "roles": roles,
        "order": method_order(t),
        "stages": stages(t),
        "cycles_per_step": {
            name: cycle_count(t, COST_MODEL, PROBLEMS[name].n_states)
            for name in VALIDATION_NAMES
        },
        "steps": {
            name: steps_for_budget(t, COST_MODEL, PROBLEMS[name].n_states, BUDGET_CYCLES)
            for name in VALIDATION_NAMES
        },
        "tableau": to_json(t),
    }
    if extra:
        entry.update(extra)
    return entry


def _problem_entry(name: str) -> dict:
    p = PROBLEMS[name]
    meta = PROBLEM_META[name]
    entry = {
        "name": name,
        "domain": meta["domain"],
        "source": meta["source"],
        "equation": meta["equation"],
        "reference": meta["reference"],
        "family": p.family,
        "n_states": p.n_states,
        "y0": list(Y0_PHYS[name]),
        "t_end": p.t_end,
        "scale": p.scale,
        "deriv_scale": DERIV_SCALE[name],
        "peak": PEAK[name],
        "per_state_peaks": list(PER_STATE_PEAKS[name]),
        "window": meta["window"],
    }
    if "notes" in meta:
        entry["notes"] = meta["notes"]
    return entry


def _verdicts(methods: list[dict], rows: list[dict]) -> dict:
    kind_of = {m["name_or_hash"]: m["kind"] for m in methods}
    per_problem: dict[str, dict] = {}
    ratios: list[float] = []
    wins = 0
    comparable = 0
    for name in VALIDATION_NAMES:
        cells = [r for r in rows if r["problem"] == name and r["q15_error"] is not None]
        if not cells:
            per_problem[name] = {"winner": None}
            continue
        best = min(cells, key=lambda r: r["q15_error"])
        cls = [r for r in cells if kind_of[r["method"]] == "classical"]
        dis = [r for r in cells if kind_of[r["method"]] == "discovered"]
        entry = {
            "winner": best["method"],
            "winner_kind": kind_of[best["method"]],
            "winner_q15_error": best["q15_error"],
        }
        if cls and dis:
            bc = min(cls, key=lambda r: r["q15_error"])
            bd = min(dis, key=lambda r: r["q15_error"])
            ratio = bd["q15_error"] / bc["q15_error"] if bc["q15_error"] > 0 else None
            entry.update({
                "best_classical": bc["method"],
                "best_classical_q15_error": bc["q15_error"],
                "best_discovered": bd["method"],
                "best_discovered_q15_error": bd["q15_error"],
                "ratio_discovered_over_classical": ratio,
            })
            comparable += 1
            if bd["q15_error"] < bc["q15_error"]:
                wins += 1
            if ratio is not None:
                ratios.append(ratio)
        per_problem[name] = entry
    ratios_sorted = sorted(ratios)
    median_ratio = None
    if ratios_sorted:
        mid = len(ratios_sorted) // 2
        if len(ratios_sorted) % 2:
            median_ratio = ratios_sorted[mid]
        else:
            median_ratio = 0.5 * (ratios_sorted[mid - 1] + ratios_sorted[mid])
    if comparable:
        overall = (
            f"On the practical validation suite ({comparable} problems from embedded "
            f"application domains) at a {BUDGET_CYCLES}-cycle budget under "
            f"{COST_MODEL.name} with floor rounding, the best discovered method has "
            f"lower Q15 error than the best classical anchor on {wins} of "
            f"{comparable} problems. The median ratio of best-discovered to "
            f"best-classical Q15 error is {median_ratio:.3f} (below 1.0 favors the "
            f"discovered methods)."
        )
    else:
        overall = "No comparable results; every cell failed or one side is missing."
    return {
        "per_problem": per_problem,
        "problems_won_by_discovered": wins,
        "problems_compared": comparable,
        "median_ratio_discovered_over_classical": median_ratio,
        "overall": overall,
    }


def build_results(records: list[Record] | None = None,
                  champ: str | None = None) -> dict:
    """The full results document. Pure function of the archive plus this module;
    no wall clock. Pass records/champ explicitly for testing."""
    if records is None:
        records = read_all()
    if champ is None:
        champ = champion_hash()
    discovered = select_discovered(records, champ)

    cls_tabs = classical()
    methods: list[dict] = []
    tabs: dict[str, Tableau] = {}
    for name in CLASSICAL_ANCHOR_NAMES:
        t = cls_tabs[name]
        methods.append(_method_entry(name, "classical", ["anchor"], t))
        tabs[name] = t
    def _fin(v):
        return v if isinstance(v, (int, float)) and math.isfinite(v) else None

    for rec, roles in discovered:
        extra = {
            "archive": {
                "cycle_id": rec.cycle_id,
                "tier": rec.tier,
                "heldout_error": _fin(rec.score.heldout_error),
                "search_error": _fin(rec.score.search_error),
                "verifier_hash": rec.verifier_hash,
            }
        }
        methods.append(_method_entry(rec.tableau_hash, "discovered", roles, rec.tableau, extra))
        tabs[rec.tableau_hash] = rec.tableau

    rows: list[dict] = []
    for m in methods:
        t = tabs[m["name_or_hash"]]
        for name in VALIDATION_NAMES:
            cell = evaluate_pair(t, name)
            cell = {"method": m["name_or_hash"], **cell}
            rows.append(cell)

    return {
        "schema": _SCHEMA_DOC,
        "generated_from": {
            "archive_records": len(records),
            "verifier_hash": _read_verifier_hash(),
            "champion_hash": champ,
        },
        "budget_cycles": BUDGET_CYCLES,
        "cost_model": COST_MODEL.name,
        "rounding": "floor (ASRS), per HANDOFF 4.2",
        "problems": [_problem_entry(name) for name in VALIDATION_NAMES],
        "methods": methods,
        "results": rows,
        "verdicts": _verdicts(methods, rows),
    }


def validate_results(doc: dict) -> None:
    """Raise ValueError if the document violates the schema described above."""
    def fail(msg: str):
        raise ValueError(f"results schema: {msg}")

    for key in ("schema", "generated_from", "budget_cycles", "cost_model",
                "rounding", "problems", "methods", "results", "verdicts"):
        if key not in doc:
            fail(f"missing top-level key {key!r}")
    gf = doc["generated_from"]
    if not isinstance(gf.get("archive_records"), int):
        fail("generated_from.archive_records must be an integer")
    for k in ("verifier_hash", "champion_hash"):
        if not isinstance(gf.get(k), str):
            fail(f"generated_from.{k} must be a string")
    pnames = [p["name"] for p in doc["problems"]]
    if sorted(pnames) != sorted(VALIDATION_NAMES):
        fail(f"problems must cover {VALIDATION_NAMES}, got {pnames}")
    for p in doc["problems"]:
        for k in ("name", "domain", "source", "scale", "deriv_scale", "window"):
            if k not in p:
                fail(f"problem {p.get('name')!r} missing {k!r}")
    mnames = [m["name_or_hash"] for m in doc["methods"]]
    if len(set(mnames)) != len(mnames):
        fail("duplicate method entries")
    for m in doc["methods"]:
        if m["kind"] not in ("classical", "discovered"):
            fail(f"method {m['name_or_hash']!r} has bad kind {m['kind']!r}")
        for k in ("order", "stages", "cycles_per_step", "steps", "tableau", "roles"):
            if k not in m:
                fail(f"method {m['name_or_hash']!r} missing {k!r}")
    seen = set()
    for r in doc["results"]:
        for k in ("method", "problem", "q15_error", "float_error"):
            if k not in r:
                fail(f"result row missing {k!r}: {r}")
        if r["method"] not in mnames:
            fail(f"result row references unknown method {r['method']!r}")
        if r["problem"] not in pnames:
            fail(f"result row references unknown problem {r['problem']!r}")
        for k in ("q15_error", "float_error"):
            v = r[k]
            if v is not None and (not isinstance(v, (int, float)) or not math.isfinite(v)):
                fail(f"result {r['method']}/{r['problem']} {k} must be finite or null")
        key = (r["method"], r["problem"])
        if key in seen:
            fail(f"duplicate result row {key}")
        seen.add(key)
    if len(seen) != len(mnames) * len(pnames):
        fail("results must cover every (method, problem) pair exactly once")
    v = doc["verdicts"]
    if "per_problem" not in v or "overall" not in v:
        fail("verdicts must carry per_problem and overall")
    if not isinstance(v["overall"], str):
        fail("verdicts.overall must be a string")


def write_results(doc: dict, path: Path | str | None = None) -> Path:
    out = Path(path) if path is not None else work_dir() / "validation" / "results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, indent=1, sort_keys=True, allow_nan=False) + "\n"
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return out


def main() -> None:
    doc = build_results()
    validate_results(doc)
    out = write_results(doc)
    print(f"wrote {out}")
    print(f"archive records: {doc['generated_from']['archive_records']}")
    print(f"methods: {', '.join(m['name_or_hash'][:12] for m in doc['methods'])}")
    for name, entry in doc["verdicts"]["per_problem"].items():
        print(f"  {name}: winner {entry.get('winner')} ({entry.get('winner_kind')}), "
              f"q15 {entry.get('winner_q15_error')}")
    print(doc["verdicts"]["overall"])


if __name__ == "__main__":
    main()
