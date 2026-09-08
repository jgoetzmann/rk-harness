"""Stiff-candidate screen (T15). New module; nothing pinned is modified.

`EPOCH3-DESIGN.md` names candidate stiff problems for the epoch-3 scored suite in
one sentence and nothing has ever measured them. This module measures them, off
the archive, and writes down what it found for each one, including the ones it
turned away. A candidate that cannot be put into Q15 at any power-of-two scale is
a result about fixed-point integration, so the rejections are recorded with the
number that produced them and sit in the artifact next to the admissions.

What the screen is not: it adopts nothing. `rk_harness/problems.py` and
`fixtures/problems.json` are verifier-pinned, so putting a candidate into the
scored suite is the epoch-3 boundary and a hash re-pin. `rk_harness/validation.py`
is unpinned but it is the out-of-band suite, its T8 tests assert that euler, heun2
and midpoint finish every stiff problem, and its name tuple is mirrored by hand in
`sidetrack.VALIDATION_NAMES`, which feeds `sidetrack.code_hash()` and therefore the
identity of every measured side-track point. Adoption into either home is an owner
decision. This module exists so that decision is made from measurements.

The four steps, applied in order and short-circuited on the earliest failure:

1. Dynamic range. Float RK4 over the window gives per-state peaks and the largest
   derivative magnitude. Every power-of-two scale in `SCALE_EXPONENTS` is then
   priced: the state margin `1 / (2 * max|y| * scale)` against
   `MIN_OVERFLOW_MARGIN`, the Q15 derivative peak against full scale, the step `h`
   against Q15 representability, and the per-step increment against
   `MIN_INCREMENT_LSB`. Every scale row is kept, admitted or not, so "no scale
   exists" is a printed table.
2. Stiffness through stability rather than through range, the rule
   `validation.py` states for its own stiff subset. Jacobian eigenvalues at
   `JAC_SAMPLES` deterministic sample times give the fast over slow ratio, and the
   initial Q15 derivative is checked against the same 8192 bound T8 uses. A
   candidate that is stiff only because its derivative leaves the representable
   range is measuring Q15, not stiffness.
3. The wall test at `BUDGET_CYCLES` under `COST_MODEL`: all eight fixture anchors
   (and archive elites when the caller supplies records) through `solve_q15` at the
   budget-implied step count, each classified as a finisher, an overflow or a
   non-finite result, against an SDIRK2 float reference at matched analytic cost
   from `prototypes.sdirk`. The verdict is recorded on the candidate. It does not
   select the candidate: a problem chosen because it stops explicit methods is a
   problem chosen to make implicit methods look good, so the document reports how
   many candidates were screened against how many survived and a reader can price
   the selection.
4. Reference computability: matrix exponential for the linear candidates, mpmath
   odefun at 30 digits for the nonlinear ones, each cross-checked against a fine
   float RK4 run, which is what `validation.py` already does.

Every candidate runs at derivative scale 1.0 and every candidate name is disjoint
from both suites, asserted at import. That pair of facts is load bearing rather
than tidy: `simulate.solve_q15` forms `h_q` from
`problems.DERIV_SCALE.get(name, 1.0)`, reading the pinned map by name, so a
candidate scaled on its right-hand side but absent from that map would integrate
with the wrong `h` and raise nothing. A candidate that needs a derivative scale
below 1.0 is unmeasurable here and is recorded as rejected for dynamic range, with
that reason spelled out.

The 2 LSB increment floor is not an invention of this module. `validation.py` has
it written down for battery_2rc: "per-step increments must stay at 2 LSB or more;
at scale 4 the Q15 integrator stalls". At the scale that suite chose, battery_2rc's
smallest starting increment is 2.74 LSB and at half that scale it is 1.37, so the
threshold reproduces a stall the suite already recorded by hand.

Determinism: the output is a pure function of this module plus whatever records the
caller passes. No wall clock, no randomness, sorted iteration throughout. `main()`
runs archive-free, which is also what makes the whole screen liftable into a
side-track job: a side-track plan may not depend on archive contents.
"""
from __future__ import annotations

import functools
import json
import math
from pathlib import Path

import numpy as np
from mpmath import mp
from scipy.linalg import expm

from rk_harness.costmodel import M0PLUS_FAST, cycle_count
from rk_harness.paths import HARNESS_DIR, work_dir
from rk_harness.problems import DERIV_SCALE as _FROZEN_DERIV_SCALE
from rk_harness.problems import PROBLEMS as _FROZEN_PROBLEMS
from rk_harness.problems import make_q15_rhs, to_physical, to_q15_state
from rk_harness.prototypes.sdirk import estimate_sdirk2_cycles, solve_sdirk2
from rk_harness.simulate import (
    float_tableau,
    rk_step_float,
    solve_float,
    solve_q15,
    steps_for_budget,
)
from rk_harness.tableau import classical, content_hash, stages
from rk_harness.types import Problem, Record
from rk_harness.validation import VALIDATION_NAMES as _OUT_OF_BAND_NAMES

BUDGET_CYCLES = 65536
COST_MODEL = M0PLUS_FAST

# Admissibility thresholds.  MIN_OVERFLOW_MARGIN sits above the verifier's own
# gate: verifier.py rejects a tableau whose overflow_margin is at or below 1.0, and
# that margin is computed over the whole scored set at once, so a problem that only
# just clears 1.0 would poison every future scored candidate rather than only
# itself.
MIN_OVERFLOW_MARGIN = 1.2
MIN_INCREMENT_LSB = 2.0
DERIV_HEADROOM = 32767.0 / 32768.0        # largest magnitude q15_from_float accepts
WALL_MULTIPLE = 10.0
REF_TOL = 1e-9
SCALE_EXPONENTS: tuple[int, ...] = tuple(range(-6, 7))
PEAK_STEPS = 40000
JAC_SAMPLES = 32
# T8's test_stiff_start_on_slow_manifold bound: the initial Q15 derivative of a
# stiff problem stays at or below a quarter of full scale.
START_DERIV_BOUND = 8192.0
# The band T8 asserts for its own moderately stiff subset.
STIFFNESS_BAND: tuple[float, float] = (50.0, 2000.0)
# Relative floors.  A state whose derivative is this far below the largest one is
# treated as not moving rather than as stalled, which also absorbs the float
# cancellation a quasi-steady start leaves behind (a residual of order 1e-14).
DERIV_FLOOR = 1e-9
EIGENVALUE_FLOOR = 1e-9
# Central-difference step, eps**(1/3) scaled by the state, the standard choice.
_CD_STEP = 2.220446049250313e-16 ** (1.0 / 3.0)

CANDIDATES: tuple[str, ...] = (
    "vanderpol_relax",
    "flame_scalar_e64",
    "flame_scalar_e256",
    "thermal_3node_fast",
    "battery_2rc_fast",
)

# Every candidate runs at derivative scale 1.0, like the whole out-of-band suite.
DERIV_SCALE: dict[str, float] = {name: 1.0 for name in CANDIDATES}

assert not set(CANDIDATES) & set(_FROZEN_PROBLEMS), (
    "screen candidate names must not shadow the frozen scored problems"
)
assert not set(CANDIDATES) & set(_OUT_OF_BAND_NAMES), (
    "screen candidate names must not shadow the out-of-band validation problems"
)
assert all(_FROZEN_DERIV_SCALE.get(name, 1.0) == 1.0 for name in CANDIDATES), (
    "solve_q15 reads the pinned DERIV_SCALE by name; a candidate that resolves to "
    "anything but 1.0 there would integrate with the wrong h and raise nothing"
)


# --------------------------------------------------------------------------- parameters

# vanderpol_relax: the frozen vanderpol_mild equation with mu = 20 in place of
# problems._MU = 0.5, so the cross-epoch comparison is one equation with one number
# changed.  y0 is the frozen (1, 0).  t_end is one relaxation period, measured at
# 34.68232 from successive upward zero crossings of x in a float RK4 run at 200,000
# steps over t = 160 (crossings 17.98755, 52.66987, 87.35220, 122.03452, 156.71684).
_VDP_MU = 20.0
_VDP_PERIOD = 34.6823

# flame_scalar_e64 / flame_scalar_e256: y' = y^2 - y^3 with y(0) = 2**-6 and 2**-8
# and t_end = 2 / y(0), the standard ignition window.  One state, so the SDIRK2
# solver terms are as small as they get: 150 m0plus_fast cycles per step against 33
# for rk4, the cheapest wall test available.
_FLAME_E64 = 2.0 ** -6
_FLAME_E256 = 2.0 ** -8

# thermal_3node_fast: the frozen problems._RC_A structure with node 1 thinned by 16
# (its row scaled, which is what a smaller thermal mass does), putting the
# eigenvalue ratio at 638.9 against the frozen 70.1.  A constant heat input at node
# 3 makes the system affine so the run settles at a nonzero steady state instead of
# decaying into the quantization floor; on the homogeneous frozen problem the floor
# bias becomes the whole story well before stability does.  Starting cold is also
# what keeps the derivative inside Q15 at derivative scale 1.0: the frozen problem
# starts at (1, 0, 0), whose derivative is 11 and needs DERIV_SCALE 0.125, while
# this one starts at rest with only the source term live, so max|f| is the source.
_TN_A: tuple[tuple[float, ...], ...] = (
    (-176.0, 160.0, 0.0),
    (5.0, -6.0, 1.0),
    (0.0, 2.0, -2.0),
)
_TN_G: tuple[float, ...] = (0.0, 0.0, 0.4)

# battery_2rc_fast: validation.battery_2rc with the fast branch capacitance cut by
# 64, which moves tau1 from 32.8 s to 0.513 s and the ratio from 6.8 to 434.5.
# Everything else is carried over from validation.PROBLEM_META['battery_2rc'].
_BAT_T = 20.0               # time unit, seconds
_BAT_I = 1.7                # A (2C)
_BAT_CN = 0.85              # Ah
_BAT_R1, _BAT_C1 = 0.0467, 703.0 / 64.0     # tau1 = 0.5130 s (thinned)
_BAT_R2, _BAT_C2 = 0.0498, 4475.0           # tau2 = 222.9 s, unchanged


# --------------------------------------------------------------------------- float64 derivatives

def _rhs_vanderpol_relax(t: float, y: tuple[float, ...]) -> tuple[float, ...]:
    x, v = y[0], y[1]
    return (v, _VDP_MU * (1.0 - x * x) * v - x)


def _jac_vanderpol_relax(t: float, y: tuple[float, ...]) -> list[list[float]]:
    x, v = y[0], y[1]
    return [[0.0, 1.0], [-2.0 * _VDP_MU * x * v - 1.0, _VDP_MU * (1.0 - x * x)]]


def _rhs_flame(t: float, y: tuple[float, ...]) -> tuple[float, ...]:
    u = y[0]
    return (u * u - u * u * u,)


def _jac_flame(t: float, y: tuple[float, ...]) -> list[list[float]]:
    u = y[0]
    return [[2.0 * u - 3.0 * u * u]]


def _rhs_thermal_3node_fast(t: float, y: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(
        _TN_A[r][0] * y[0] + _TN_A[r][1] * y[1] + _TN_A[r][2] * y[2] + _TN_G[r]
        for r in range(3)
    )


def _jac_thermal_3node_fast(t: float, y: tuple[float, ...]) -> list[list[float]]:
    return [list(row) for row in _TN_A]


def _rhs_battery_2rc_fast(t: float, y: tuple[float, ...]) -> tuple[float, ...]:
    _dod, v1, v2 = y[0], y[1], y[2]
    return (
        _BAT_T * _BAT_I / (3600.0 * _BAT_CN),
        _BAT_T * (-v1 / (_BAT_R1 * _BAT_C1) + _BAT_I / _BAT_C1),
        _BAT_T * (-v2 / (_BAT_R2 * _BAT_C2) + _BAT_I / _BAT_C2),
    )


def _jac_battery_2rc_fast(t: float, y: tuple[float, ...]) -> list[list[float]]:
    return [
        [0.0, 0.0, 0.0],
        [0.0, -_BAT_T / (_BAT_R1 * _BAT_C1), 0.0],
        [0.0, 0.0, -_BAT_T / (_BAT_R2 * _BAT_C2)],
    ]


FLOAT_RHS = {
    "vanderpol_relax": _rhs_vanderpol_relax,
    "flame_scalar_e64": _rhs_flame,
    "flame_scalar_e256": _rhs_flame,
    "thermal_3node_fast": _rhs_thermal_3node_fast,
    "battery_2rc_fast": _rhs_battery_2rc_fast,
}

# Analytic Jacobians for every candidate, so the SDIRK2 reference never pays for a
# finite-difference assembly and its matched cost is the analytic estimate.  This
# is also what J11 asks the linear candidates to supply.
JACOBIAN = {
    "vanderpol_relax": _jac_vanderpol_relax,
    "flame_scalar_e64": _jac_flame,
    "flame_scalar_e256": _jac_flame,
    "thermal_3node_fast": _jac_thermal_3node_fast,
    "battery_2rc_fast": _jac_battery_2rc_fast,
}


# --------------------------------------------------------------------------- references

def _augmented_ref(aug: np.ndarray, y0: tuple[float, ...]):
    """Closed form for affine y' = M y + g via the augmented matrix exponential,
    the pattern validation._augmented_ref and the frozen _ref_dc_motor both use."""
    n = len(y0)

    def ref(t: float) -> tuple[float, ...]:
        if t == 0.0:
            return y0
        v = expm(aug * float(t)) @ np.array(list(y0) + [1.0], dtype=float)
        return tuple(float(v[i]) for i in range(n))

    return ref


_TN_AUG = np.array(
    [
        [_TN_A[0][0], _TN_A[0][1], _TN_A[0][2], _TN_G[0]],
        [_TN_A[1][0], _TN_A[1][1], _TN_A[1][2], _TN_G[1]],
        [_TN_A[2][0], _TN_A[2][1], _TN_A[2][2], _TN_G[2]],
        [0.0, 0.0, 0.0, 0.0],
    ],
    dtype=float,
)
_ref_thermal_3node_fast = _augmented_ref(_TN_AUG, (0.0, 0.0, 0.0))

_BAT_AUG = np.array(
    [
        [0.0, 0.0, 0.0, _BAT_T * _BAT_I / (3600.0 * _BAT_CN)],
        [0.0, -_BAT_T / (_BAT_R1 * _BAT_C1), 0.0, _BAT_T * _BAT_I / _BAT_C1],
        [0.0, 0.0, -_BAT_T / (_BAT_R2 * _BAT_C2), _BAT_T * _BAT_I / _BAT_C2],
        [0.0, 0.0, 0.0, 0.0],
    ],
    dtype=float,
)
_ref_battery_2rc_fast = _augmented_ref(_BAT_AUG, (0.0, 0.0, 0.0))


@functools.lru_cache(maxsize=None)
def _flame_solution(y0_exponent: int):
    with mp.workdps(30):
        def F(t, y):
            return [y[0] ** 2 - y[0] ** 3]
        return mp.odefun(F, 0, [mp.mpf(2) ** (-y0_exponent)])


@functools.lru_cache(maxsize=None)
def _ref_flame_e64(t: float) -> tuple[float, ...]:
    with mp.workdps(30):
        return (float(_flame_solution(6)(mp.mpf(t))[0]),)


@functools.lru_cache(maxsize=None)
def _ref_flame_e256(t: float) -> tuple[float, ...]:
    with mp.workdps(30):
        return (float(_flame_solution(8)(mp.mpf(t))[0]),)


@functools.lru_cache(maxsize=None)
def _vdp_relax_solution():
    with mp.workdps(30):
        def F(t, y):
            return [y[1], 20 * (1 - y[0] ** 2) * y[1] - y[0]]
        return mp.odefun(F, 0, [mp.mpf(1), mp.mpf(0)])


@functools.lru_cache(maxsize=None)
def _ref_vanderpol_relax(t: float) -> tuple[float, ...]:
    with mp.workdps(30):
        v = _vdp_relax_solution()(mp.mpf(t))
        return (float(v[0]), float(v[1]))


REFERENCE = {
    "vanderpol_relax": _ref_vanderpol_relax,
    "flame_scalar_e64": _ref_flame_e64,
    "flame_scalar_e256": _ref_flame_e256,
    "thermal_3node_fast": _ref_thermal_3node_fast,
    "battery_2rc_fast": _ref_battery_2rc_fast,
}

REFERENCE_KIND = {
    "vanderpol_relax": "mpmath odefun at 30 digits",
    "flame_scalar_e64": "mpmath odefun at 30 digits",
    "flame_scalar_e256": "mpmath odefun at 30 digits",
    "thermal_3node_fast": "matrix exponential of the augmented affine system",
    "battery_2rc_fast": "matrix exponential of the augmented affine system",
}

# Float RK4 steps the cross-check needs to resolve the reference to REF_TOL.  The
# resolution a candidate needs is a property of the candidate: PEAK_STEPS is enough
# everywhere except van der Pol at mu = 20, where the RK4 error is concentrated in
# the two relaxation jumps and 40,000 steps leave it at 2e-9, just past the
# tolerance.  This is the cross-check resolution, not the reference itself.
REFERENCE_CHECK_STEPS: dict[str, int] = {
    name: (80000 if name == "vanderpol_relax" else PEAK_STEPS) for name in CANDIDATES
}


# --------------------------------------------------------------------------- candidate spec

_SPEC = {
    # name: (y0_phys, t_end, family)
    "vanderpol_relax": ((1.0, 0.0), _VDP_PERIOD, "nonlinear"),
    "flame_scalar_e64": ((_FLAME_E64,), 2.0 / _FLAME_E64, "nonlinear"),
    "flame_scalar_e256": ((_FLAME_E256,), 2.0 / _FLAME_E256, "nonlinear"),
    "thermal_3node_fast": ((0.0, 0.0, 0.0), 12.0, "linear"),
    "battery_2rc_fast": ((0.0, 0.0, 0.0), 6.0, "linear"),
}

Y0_PHYS: dict[str, tuple[float, ...]] = {name: s[0] for name, s in _SPEC.items()}
T_END: dict[str, float] = {name: s[1] for name, s in _SPEC.items()}
FAMILY: dict[str, str] = {name: s[2] for name, s in _SPEC.items()}
N_STATES: dict[str, int] = {name: len(s[0]) for name, s in _SPEC.items()}

# Per-state max |y_i| over the window, float RK4 at PEAK_STEPS steps (y0 included),
# measured once and hardcoded exactly like validation.PER_STATE_PEAKS and
# fixtures/problems.json do.  test_peaks_match_measurement checks them.
PER_STATE_PEAKS: dict[str, tuple[float, ...]] = {
    "vanderpol_relax": (2.0077899662, 27.3903193592),
    "flame_scalar_e64": (1.0,),
    "flame_scalar_e256": (1.0,),
    "thermal_3node_fast": (0.3851836104, 0.4237281455, 0.6210500499),
    "battery_2rc_fast": (0.0666666667, 0.0793900000, 0.0352488297),
}

PEAK: dict[str, float] = {name: max(v) for name, v in PER_STATE_PEAKS.items()}

# Site-facing metadata: where the candidate comes from and what was changed.  Prose
# here can reach the findings site, so it stays clear of the banned-word list and
# uses no em dashes.
CANDIDATE_META: dict[str, dict] = {
    "vanderpol_relax": {
        "origin": (
            "The frozen scored problem vanderpol_mild (problems.py) with mu raised "
            "from 0.5 to 20, which is the relaxation regime EPOCH3-DESIGN.md names "
            "as the nonlinear stress case. Same equation, same reference machinery, "
            "one number changed, so a cross-epoch comparison stays clean."
        ),
        "equation": "x' = v; v' = mu*(1 - x^2)*v - x (mu = 20)",
        "window": {
            "t_end": _VDP_PERIOD,
            "time_unit": "dimensionless",
            "real_span": "one relaxation period, measured at 34.68232",
        },
    },
    "flame_scalar_e64": {
        "origin": (
            "Scalar flame-type ignition, y' = y^2 - y^3 with y(0) = 2**-6 and "
            "t_end = 2/y(0), the standard window in which the solution ignites near "
            "t = 1/y(0) and settles at y = 1. One state keeps the SDIRK2 solver "
            "terms at 150 m0plus_fast cycles per step against 33 for rk4."
        ),
        "equation": "y' = y^2 - y^3, y(0) = 2**-6",
        "window": {"t_end": 2.0 / _FLAME_E64, "time_unit": "dimensionless",
                   "real_span": "2/y(0) = 128 units"},
    },
    "flame_scalar_e256": {
        "origin": (
            "The same scalar flame equation with y(0) = 2**-8, a sharper ignition "
            "and a window four times longer. Included so the screen prices the "
            "ignition parameter rather than one setting of it."
        ),
        "equation": "y' = y^2 - y^3, y(0) = 2**-8",
        "window": {"t_end": 2.0 / _FLAME_E256, "time_unit": "dimensionless",
                   "real_span": "2/y(0) = 512 units"},
    },
    "thermal_3node_fast": {
        "origin": (
            "The frozen rc_thermal conductance structure (problems._RC_A) with node "
            "1 thinned by 16, which is what a smaller thermal mass does to its row, "
            "plus a constant heat input of 0.4 at node 3 and a cold start. The "
            "thinning moves the eigenvalue ratio from 70.1 to 638.9. The heat input "
            "makes the system affine so the run settles at a nonzero steady state "
            "rather than decaying into the quantization floor, and the cold start "
            "puts node 1 at its quasi-steady value, which is what keeps the "
            "derivative representable at derivative scale 1.0 where the frozen "
            "problem needs 0.125."
        ),
        "equation": "y' = A y + g, A = ((-176, 160, 0), (5, -6, 1), (0, 2, -2)), g = (0, 0, 0.4)",
        "window": {"t_end": 12.0, "time_unit": "dimensionless",
                   "real_span": "3.4 slow time constants"},
    },
    "battery_2rc_fast": {
        "origin": (
            "validation.battery_2rc (Chen and Rincon-Mora 2006, 850 mAh cell at SOC "
            "0.5, 1.7 A discharge) with the fast branch capacitance cut by 64, so "
            "tau1 falls from 32.8 s to 0.513 s and the branch ratio rises from 6.8 "
            "to 434.5. Sourcing, equations and normalization are carried over "
            "unchanged from validation.PROBLEM_META."
        ),
        "equation": "dod' = T*I/(3600*Cn); vk' = T*(-vk/(Rk*Ck) + I/Ck), k = 1, 2",
        "window": {"t_end": 6.0, "time_unit": "20 s", "real_span": "120 s"},
    },
}


# --------------------------------------------------------------------------- error metric

def screen_error(name: str, y_final_phys: tuple[float, ...]) -> float:
    """L2 final-state error against the reference divided by PEAK, the default
    branch of the frozen problems.error_metric applied to this candidate set.

    The frozen simulate.problem_error cannot be used here: it calls
    problems.error_metric, which looks the name up in the pinned PROBLEMS dict and
    raises KeyError for anything off-archive."""
    ref = REFERENCE[name](T_END[name])
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(y_final_phys, ref))) / PEAK[name]


def make_problem(name: str, scale: float) -> Problem:
    """The candidate as a Problem at one power-of-two state scale, built through
    the same pinned make_q15_rhs / to_q15_state the scored suite uses."""
    return Problem(
        name=name,
        n_states=N_STATES[name],
        f=make_q15_rhs(FLOAT_RHS[name], scale, DERIV_SCALE[name]),
        y0=to_q15_state(Y0_PHYS[name], scale),
        t_end=T_END[name],
        scale=scale,
        reference=REFERENCE[name],
        family=FAMILY[name],
    )


# --------------------------------------------------------------------------- step 1: measurement

def reference_trajectory(name: str, n: int = PEAK_STEPS) -> dict:
    """Per-state peaks, per-state and overall derivative peaks, the derivative at
    t = 0, and JAC_SAMPLES + 1 evenly spaced states along the window, all from one
    float RK4 run. Copied in shape from validation.measure_peaks."""
    if n % JAC_SAMPLES:
        raise ValueError(f"n must be a multiple of JAC_SAMPLES ({JAC_SAMPLES}), got {n}")
    A, b, c = float_tableau(classical()["rk4"])
    rhs = FLOAT_RHS[name]
    t_end = T_END[name]
    h = t_end / n
    y = tuple(float(v) for v in Y0_PHYS[name])
    d0 = tuple(float(v) for v in rhs(0.0, y))
    peaks = [abs(v) for v in y]
    deriv_peaks = [abs(v) for v in d0]
    stride = n // JAC_SAMPLES
    samples: list[tuple[float, tuple[float, ...]]] = [(0.0, y)]
    for k in range(n):
        y = rk_step_float(A, b, c, rhs, k * h, y, h)
        for m, v in enumerate(y):
            if abs(v) > peaks[m]:
                peaks[m] = abs(v)
        d = rhs((k + 1) * h, y)
        for m, v in enumerate(d):
            if abs(v) > deriv_peaks[m]:
                deriv_peaks[m] = abs(v)
        if (k + 1) % stride == 0:
            samples.append(((k + 1) * h, y))
    return {
        "steps": n,
        "per_state_peaks": tuple(peaks),
        "peak": max(peaks),
        "per_state_deriv_peaks": tuple(deriv_peaks),
        "deriv_peak": max(deriv_peaks),
        "deriv_at_zero": d0,
        "final": y,
        "samples": samples,
    }


def _floored_min(values: tuple[float, ...] | list[float]) -> float:
    """Smallest magnitude among the entries that are within DERIV_FLOOR of the
    largest one, i.e. ignoring states that are not moving at all.

    A state whose derivative is exactly zero at t = 0 is at rest, not stalled, and
    a quasi-steady start leaves a float cancellation residual of order 1e-14 that
    would otherwise be read as a stall."""
    mags = [abs(v) for v in values]
    top = max(mags) if mags else 0.0
    if top <= 0.0:
        return 0.0
    live = [v for v in mags if v >= DERIV_FLOOR * top]
    return min(live)


def admissible_scales(
    per_state_peaks: tuple[float, ...],
    per_state_deriv_peaks: tuple[float, ...],
    deriv_at_zero: tuple[float, ...],
    t_end: float,
    n_states: int,
    deriv_scale: float = 1.0,
    exponents: tuple[int, ...] = SCALE_EXPONENTS,
) -> list[dict]:
    """One row per power-of-two scale, admitted or not. Never raises, never drops.

    A row is admitted when all four hold:

    * state_margin = 1 / (2 * max|y| * scale) is at or above MIN_OVERFLOW_MARGIN,
      the same arithmetic evaluator.py uses for ScoreVector.overflow_margin with
      max|y| * scale standing in for max|q| / 32768;
    * deriv_peak_q = max|f| * scale fits Q15 (q15_from_float refuses 1.0 and above);
    * h_over_deriv_scale is below 1.0, so h_q is representable, taken at the
      dearest fixture anchor because that is the one taking the largest step;
    * both increment measures are at or above MIN_INCREMENT_LSB, taken at the
      cheapest fixture anchor because that is the one taking the smallest step.
      start_increment_lsb is the smallest moving state at t = 0, which is what a
      slow ignition fails; slowest_state_increment_lsb is the smallest per-state
      derivative peak over the whole window, which is what a state that never gets
      going fails.
    """
    peak = max(per_state_peaks)
    deriv_peak = max(per_state_deriv_peaks)
    anchors = classical()
    counts = [
        steps_for_budget(t, COST_MODEL, n_states, BUDGET_CYCLES)
        for _, t in sorted(anchors.items())
    ]
    n_cheapest, n_dearest = max(counts), min(counts)
    h_cheapest = t_end / n_cheapest if n_cheapest > 0 else float("inf")
    h_dearest = t_end / n_dearest if n_dearest > 0 else float("inf")
    start_deriv = _floored_min(deriv_at_zero)
    slowest_deriv = _floored_min(per_state_deriv_peaks)

    rows: list[dict] = []
    for e in exponents:
        scale = 2.0 ** e
        state_margin = 1.0 / (2.0 * peak * scale) if peak > 0.0 else float("inf")
        deriv_peak_q = deriv_peak * scale
        h_over = h_dearest / deriv_scale
        start_lsb = h_cheapest * start_deriv * scale * 32768.0
        slow_lsb = h_cheapest * slowest_deriv * scale * 32768.0
        checks = {
            "state_margin": state_margin >= MIN_OVERFLOW_MARGIN,
            "deriv_fits": deriv_peak_q <= DERIV_HEADROOM,
            "h_representable": h_over < 1.0,
            "start_increment": start_lsb >= MIN_INCREMENT_LSB,
            "slowest_state_increment": slow_lsb >= MIN_INCREMENT_LSB,
        }
        rows.append({
            "exponent": e,
            "scale": scale,
            "state_margin": state_margin,
            "deriv_peak_q": deriv_peak_q,
            "h_over_deriv_scale": h_over,
            "steps_cheapest_anchor": n_cheapest,
            "steps_dearest_anchor": n_dearest,
            "start_increment_lsb": start_lsb,
            "slowest_state_increment_lsb": slow_lsb,
            "admitted": all(checks.values()),
            "failed": sorted(k for k, ok in checks.items() if not ok),
        })
    return rows


def scale_bounds(
    per_state_peaks: tuple[float, ...],
    per_state_deriv_peaks: tuple[float, ...],
    deriv_at_zero: tuple[float, ...],
    t_end: float,
    n_states: int,
) -> dict:
    """The scale interval each criterion allows, independent of which powers of two
    were searched. When the lower bound is above the upper bound no scale exists at
    any exponent, which is a stronger statement than an empty table of rows."""
    peak = max(per_state_peaks)
    deriv_peak = max(per_state_deriv_peaks)
    counts = [
        steps_for_budget(t, COST_MODEL, n_states, BUDGET_CYCLES)
        for _, t in sorted(classical().items())
    ]
    h_cheapest = t_end / max(counts) if max(counts) > 0 else float("inf")
    start_deriv = _floored_min(deriv_at_zero)
    slowest_deriv = _floored_min(per_state_deriv_peaks)
    upper_state = 1.0 / (2.0 * MIN_OVERFLOW_MARGIN * peak) if peak > 0.0 else float("inf")
    upper_deriv = DERIV_HEADROOM / deriv_peak if deriv_peak > 0.0 else float("inf")
    lower_start = (
        MIN_INCREMENT_LSB / (h_cheapest * start_deriv * 32768.0)
        if start_deriv > 0.0 else float("inf")
    )
    lower_slow = (
        MIN_INCREMENT_LSB / (h_cheapest * slowest_deriv * 32768.0)
        if slowest_deriv > 0.0 else float("inf")
    )
    upper = min(upper_state, upper_deriv)
    lower = max(lower_start, lower_slow)
    return {
        "upper_from_state_margin": upper_state,
        "upper_from_derivative_range": upper_deriv,
        "lower_from_start_increment": lower_start,
        "lower_from_slowest_state_increment": lower_slow,
        "upper": upper,
        "lower": lower,
        "interval_is_empty": not (lower <= upper),
    }


def candidate_scales(name: str, meas: dict | None = None) -> tuple[list[dict], dict]:
    """(scale rows, scale bounds) for one candidate."""
    m = meas if meas is not None else reference_trajectory(name)
    args = (
        m["per_state_peaks"], m["per_state_deriv_peaks"], m["deriv_at_zero"],
        T_END[name], N_STATES[name],
    )
    return admissible_scales(*args, deriv_scale=DERIV_SCALE[name]), scale_bounds(*args)


# --------------------------------------------------------------------------- step 2: stiffness

def jacobian_central(rhs, t: float, y: tuple[float, ...]) -> list[list[float]]:
    """Central-difference Jacobian, column j from perturbing y_j both ways.

    Costs 2*n rhs evaluations against the one-sided n of prototypes.sdirk.fd_jacobian
    and is second order in the step, which matters here because the eigenvalue ratio
    is the reported number rather than a Newton correction."""
    n = len(y)
    jac = [[0.0] * n for _ in range(n)]
    for j in range(n):
        d = _CD_STEP * max(abs(y[j]), 1.0)
        yp, ym = list(y), list(y)
        yp[j] += d
        ym[j] -= d
        fp = rhs(t, tuple(yp))
        fm = rhs(t, tuple(ym))
        for i in range(n):
            jac[i][j] = (fp[i] - fm[i]) / (2.0 * d)
    return jac


def jacobian_spectrum(rhs, samples, jac=None) -> dict:
    """Fast and slow real parts over the sample points, and their ratio.

    fast is the largest |Re lambda| anywhere on the sampled trajectory; slow is the
    smallest |Re lambda| that is not within EIGENVALUE_FLOOR of zero relative to
    fast, so a conserved quantity (an exactly zero eigenvalue, as robertson_scaled
    has) does not become the denominator."""
    fast = 0.0
    slow: float | None = None
    per_sample: list[dict] = []
    for t, y in samples:
        mat = jac(t, y) if jac is not None else jacobian_central(rhs, t, y)
        eig = np.linalg.eigvals(np.array(mat, dtype=float))
        res = sorted(float(v.real) for v in eig)
        mags = [abs(v) for v in res]
        top = max(mags)
        if top > fast:
            fast = top
        live = [v for v in mags if v >= EIGENVALUE_FLOOR * top] if top > 0.0 else []
        if live:
            lo = min(live)
            slow = lo if slow is None else min(slow, lo)
        per_sample.append({"t": t, "real_parts": res})
    ratio = (fast / slow) if (slow is not None and slow > 0.0) else None
    return {
        "samples": len(samples),
        "fast_abs_re": fast,
        "slow_abs_re": slow,
        "stiffness_ratio": ratio,
        "per_sample": per_sample,
    }


def stiffness_through_stability(name: str, scale: float, meas: dict | None = None) -> dict:
    """Does the candidate get its stiffness from stability or from dynamic range.

    The suite's rule (validation.py module docstring) is that the run starts on or
    near the slow manifold, so derivatives stay representable and the affordable
    step count decides whether h times the fast eigenvalue leaves the stability
    interval. A candidate whose initial Q15 derivative is past START_DERIV_BOUND is
    measuring Q15 instead."""
    m = meas if meas is not None else reference_trajectory(name)
    spec = jacobian_spectrum(FLOAT_RHS[name], m["samples"], jac=JACOBIAN.get(name))
    start_q = max(abs(v) for v in m["deriv_at_zero"]) * scale * 32768.0
    ratio = spec["stiffness_ratio"]
    lo, hi = STIFFNESS_BAND
    return {
        "jacobian": "analytic" if name in JACOBIAN else "central difference",
        "samples": spec["samples"],
        "fast_abs_re": spec["fast_abs_re"],
        "slow_abs_re": spec["slow_abs_re"],
        "stiffness_ratio": ratio,
        "band": [lo, hi],
        "in_band": ratio is not None and lo <= ratio <= hi,
        "start_deriv_q": start_q,
        "start_deriv_bound": START_DERIV_BOUND,
        "starts_on_slow_manifold": start_q <= START_DERIV_BOUND,
    }


# --------------------------------------------------------------------------- step 3: the wall

def _classical_hashes() -> set[str]:
    return {content_hash(t) for t in classical().values()}


def _method_rows(name: str, scale: float, records: list[Record] | None):
    """(label, kind, tableau) for every method the wall test evaluates, in a
    deterministic order: the 8 fixture anchors by name, then discovered archive
    records by tableau hash."""
    rows = [(nm, "classical", t) for nm, t in sorted(classical().items())]
    if records:
        cls = _classical_hashes()
        seen: dict[str, object] = {}
        for r in records:
            if r.tableau_hash in cls or r.tableau_hash in seen:
                continue
            seen[r.tableau_hash] = r.tableau
        rows.extend((h, "discovered", seen[h]) for h in sorted(seen))
    return rows


def wall_test(name: str, scale: float, records: list[Record] | None = None) -> dict:
    """Every method at BUDGET_CYCLES under COST_MODEL, against an SDIRK2 float
    reference at matched analytic cost.

    `records` defaults to None, meaning no archive. The archive-free half is
    self-contained on purpose: a side-track plan may not depend on archive
    contents, so only this half can ever be lifted into a side-track job, and
    lifting it should be a move rather than a rewrite.

    A candidate is a wall when no method finishes, or when every method that
    finishes is worse than WALL_MULTIPLE times the SDIRK2 float error. The verdict
    is recorded. It is not what puts a candidate in the recommendation list."""
    p = make_problem(name, scale)
    n_states = N_STATES[name]
    rows: list[dict] = []
    skipped = 0
    for label, kind, t in _method_rows(name, scale, records):
        n = steps_for_budget(t, COST_MODEL, n_states, BUDGET_CYCLES)
        if n <= 0:
            skipped += 1
            rows.append({
                "method": label, "kind": kind, "stages": stages(t),
                "cycles_per_step": cycle_count(t, COST_MODEL, n_states),
                "steps": n, "outcome": "unaffordable", "q15_error": None,
                "float_error": None, "max_abs_q": None,
                "note": "method too expensive for the budget",
            })
            continue
        row = {
            "method": label, "kind": kind, "stages": stages(t),
            "cycles_per_step": cycle_count(t, COST_MODEL, n_states),
            "steps": n, "outcome": "overflowed", "q15_error": None,
            "float_error": None, "max_abs_q": None,
        }
        try:
            final_q, max_q = solve_q15(t, p, n)
            e = screen_error(name, to_physical(final_q, scale))
            row["max_abs_q"] = int(max_q)
            if math.isfinite(e):
                row["outcome"] = "finished"
                row["q15_error"] = e
            else:
                row["outcome"] = "nonfinite"
        except Exception as exc:
            row["note"] = f"q15 run failed: {exc.__class__.__name__}"
        try:
            yf = solve_float(t, FLOAT_RHS[name], Y0_PHYS[name], T_END[name], n)
            ef = screen_error(name, yf)
            row["float_error"] = ef if math.isfinite(ef) else None
        except Exception as exc:
            row["note"] = row.get("note", "") + f" float run failed: {exc.__class__.__name__}"
        rows.append(row)

    finishers = [r for r in rows if r["outcome"] == "finished"]
    overflowed = sum(1 for r in rows if r["outcome"] == "overflowed")
    nonfinite = sum(1 for r in rows if r["outcome"] == "nonfinite")

    sd_cost = estimate_sdirk2_cycles(n_states, COST_MODEL)["total"]
    sd_steps = BUDGET_CYCLES // sd_cost if sd_cost > 0 else 0
    sd: dict = {
        "steps": sd_steps,
        "cycles_per_step": sd_cost,
        "jacobian": "analytic",
        "newton_iters_fixed": True,
        "error": None,
    }
    if sd_steps > 0:
        try:
            ysd = solve_sdirk2(
                FLOAT_RHS[name], Y0_PHYS[name], T_END[name], sd_steps,
                jac=JACOBIAN.get(name), diverge_at=1.0e3 * max(PEAK[name], 1.0),
            )
            e = screen_error(name, ysd)
            sd["error"] = e if math.isfinite(e) else None
        except Exception as exc:
            sd["note"] = f"sdirk2 run failed: {exc.__class__.__name__}"
    else:
        sd["note"] = "sdirk2 too expensive for the budget"

    best = min((r["q15_error"] for r in finishers), default=None)
    threshold = WALL_MULTIPLE * sd["error"] if sd["error"] is not None else None
    if not finishers:
        wall: bool | None = True
    elif threshold is None:
        wall = None
    else:
        wall = best > threshold

    return {
        "scale": scale,
        "budget_cycles": BUDGET_CYCLES,
        "cost_model": COST_MODEL.name,
        "archive_elites": sum(1 for r in rows if r["kind"] == "discovered"),
        "methods_offered": len(rows),
        "methods_evaluated": len(rows) - skipped,
        "methods_unaffordable": skipped,
        "finishers": len(finishers),
        "overflowed": overflowed,
        "nonfinite": nonfinite,
        "best_finisher": min(finishers, key=lambda r: r["q15_error"])["method"] if finishers else None,
        "best_finisher_q15_error": best,
        "sdirk2_reference": sd,
        "wall_multiple": WALL_MULTIPLE,
        "wall_threshold": threshold,
        "wall": wall,
        "methods": rows,
    }


# --------------------------------------------------------------------------- step 4: references

def reference_computability(name: str, n: int | None = None) -> dict:
    """The reference at t_end against an independent fine float RK4 run, the T8
    pattern (test_reference_matches_fine_float_integration)."""
    if n is None:
        n = REFERENCE_CHECK_STEPS[name]
    out: dict = {
        "kind": REFERENCE_KIND[name],
        "fine_float_steps": n,
        "tol": REF_TOL,
        "max_rel_diff": None,
        "computable": False,
    }
    try:
        ref = REFERENCE[name](T_END[name])
        yf = solve_float(classical()["rk4"], FLOAT_RHS[name], Y0_PHYS[name], T_END[name], n)
    except Exception as exc:
        out["note"] = f"reference failed: {exc.__class__.__name__}"
        return out
    diff = max(abs(a - b) for a, b in zip(ref, yf)) / PEAK[name]
    out["reference_at_t_end"] = [float(v) for v in ref]
    out["fine_float_at_t_end"] = [float(v) for v in yf]
    out["max_rel_diff"] = diff
    out["computable"] = math.isfinite(diff) and diff <= REF_TOL
    return out


# --------------------------------------------------------------------------- the screen

_REASON_NO_SCALE = "no admissible power-of-two state scale"
_REASON_RANGE = "stiff only by leaving the representable range"
_REASON_SEPARATION = "no fast over slow separation inside the band"
_REASON_REFERENCE = "reference not computable to the stated tolerance"
_REASON_ADMITTED = "admitted"


def screen(name: str, records: list[Record] | None = None, n: int = PEAK_STEPS) -> dict:
    """Run the four steps in order and stop at the earliest failure. Never raises.

    A rejection carries the reason and every measurement taken up to that point,
    because the measurement is the finding: a candidate that cannot be put into Q15
    at any scale says something about fixed-point integration, and saying it needs
    the numbers that got there."""
    out: dict = {
        "candidate": name,
        "admitted": False,
        "reason": "",
        "stopped_at": None,
        "deriv_scale": DERIV_SCALE[name],
        "frozen_deriv_scale_lookup": _FROZEN_DERIV_SCALE.get(name, 1.0),
        "measurement": None,
        "scales": None,
        "scale_bounds": None,
        "scale": None,
        "stiffness": None,
        "wall": None,
        "reference": None,
    }
    try:
        meas = reference_trajectory(name, n)
    except Exception as exc:
        out["reason"] = f"trajectory measurement failed: {exc.__class__.__name__}"
        out["stopped_at"] = "measurement"
        return out
    out["measurement"] = {
        "steps": meas["steps"],
        "per_state_peaks": [float(v) for v in meas["per_state_peaks"]],
        "peak": float(meas["peak"]),
        "per_state_deriv_peaks": [float(v) for v in meas["per_state_deriv_peaks"]],
        "deriv_peak": float(meas["deriv_peak"]),
        "deriv_at_zero": [float(v) for v in meas["deriv_at_zero"]],
        "final": [float(v) for v in meas["final"]],
    }

    try:
        rows, bounds = candidate_scales(name, meas)
    except Exception as exc:
        out["reason"] = f"scale search failed: {exc.__class__.__name__}"
        out["stopped_at"] = "dynamic_range"
        return out
    out["scales"] = rows
    out["scale_bounds"] = bounds
    admitted = [r for r in rows if r["admitted"]]
    if not admitted:
        out["reason"] = _REASON_NO_SCALE
        out["stopped_at"] = "dynamic_range"
        return out
    scale = max(r["scale"] for r in admitted)
    out["scale"] = scale

    try:
        stiff = stiffness_through_stability(name, scale, meas)
    except Exception as exc:
        out["reason"] = f"stiffness measurement failed: {exc.__class__.__name__}"
        out["stopped_at"] = "stiffness"
        return out
    out["stiffness"] = stiff
    if not stiff["starts_on_slow_manifold"]:
        out["reason"] = _REASON_RANGE
        out["stopped_at"] = "stiffness"
        return out
    if not stiff["in_band"]:
        out["reason"] = _REASON_SEPARATION
        out["stopped_at"] = "stiffness"
        return out

    try:
        out["wall"] = wall_test(name, scale, records)
    except Exception as exc:
        out["reason"] = f"wall test failed: {exc.__class__.__name__}"
        out["stopped_at"] = "wall"
        return out

    try:
        ref = reference_computability(name)
    except Exception as exc:
        out["reason"] = f"reference check failed: {exc.__class__.__name__}"
        out["stopped_at"] = "reference"
        return out
    out["reference"] = ref
    if not ref["computable"]:
        out["reason"] = _REASON_REFERENCE
        out["stopped_at"] = "reference"
        return out

    out["admitted"] = True
    out["reason"] = _REASON_ADMITTED
    return out


# --------------------------------------------------------------------------- results document

_SCHEMA_DOC = {
    "generated_from": (
        "archive_elites: how many discovered archive records the caller supplied "
        "(0 when the screen ran archive-free, which is the default); verifier_hash: "
        "content of the pinned VERIFIER_HASH file, recorded so a reader can see the "
        "screen ran against a known scoring path without changing it"
    ),
    "budget_cycles": "shared cycle budget per candidate run",
    "cost_model": "cost model used for the budget to step-count mapping",
    "rounding": "Q15 multiply semantics (floor / ASRS, per HANDOFF 4.2)",
    "constants": (
        "the thresholds the screen applies: min_overflow_margin (above the "
        "verifier's own 1.0 gate on purpose), min_increment_lsb, wall_multiple, "
        "ref_tol, scale_exponents searched, peak_steps, jac_samples, "
        "start_deriv_bound and stiffness_band"
    ),
    "candidates": (
        "one entry per candidate configuration: name, origin (what it was derived "
        "from and what was changed), equation, family, n_states, y0 in physical "
        "units, t_end, window, deriv_scale (1.0 for all) and reference kind"
    ),
    "screen": (
        "one entry per candidate: admitted, reason, stopped_at (the step that ended "
        "the run, null when every step passed), measurement (per-state peaks, "
        "derivative peaks, derivative at t = 0, final state), scales (every "
        "power-of-two scale searched with its state_margin, deriv_peak_q, "
        "h_over_deriv_scale, both increment measures, whether it was admitted and "
        "which checks it failed), scale_bounds (the interval each criterion allows, "
        "so an empty interval reads as 'no scale exists at any exponent' rather "
        "than 'none of the ones we tried'), scale (the largest admitted scale), "
        "stiffness (Jacobian eigenvalue extremes over the sampled trajectory, their "
        "ratio, and the initial Q15 derivative against the slow-manifold bound), "
        "wall (every method at the budget with its outcome, the SDIRK2 float "
        "reference at matched analytic cost, and the verdict), reference (the "
        "reference at t_end against a fine float run)"
    ),
    "recommendation": (
        "screened against surviving, the surviving names to carry into the epoch-3 "
        "change set, and every rejection with its reason. The wall verdict is a "
        "field on a candidate and not what put it in the list: a problem chosen "
        "because it stops explicit methods is a problem chosen to make implicit "
        "methods look good, and the two counts are printed so a reader can price "
        "that selection"
    ),
    "adoption": (
        "what adopting a surviving candidate would cost, stated because this "
        "document does not do it: the scored suite is problems.py plus "
        "fixtures/problems.json, both verifier-pinned, so that is the epoch-3 "
        "boundary and a VERIFIER_HASH re-pin; the out-of-band suite is "
        "validation.py, unpinned but mirrored by hand in sidetrack.VALIDATION_NAMES, "
        "which feeds sidetrack.code_hash() and therefore the identity of every "
        "measured side-track point"
    ),
}


def _read_verifier_hash() -> str:
    return (HARNESS_DIR / "VERIFIER_HASH").read_text(encoding="utf-8").strip()


def _candidate_entry(name: str) -> dict:
    meta = CANDIDATE_META[name]
    return {
        "name": name,
        "origin": meta["origin"],
        "equation": meta["equation"],
        "family": FAMILY[name],
        "n_states": N_STATES[name],
        "y0": [float(v) for v in Y0_PHYS[name]],
        "t_end": T_END[name],
        "window": meta["window"],
        "deriv_scale": DERIV_SCALE[name],
        "peak": PEAK[name],
        "per_state_peaks": [float(v) for v in PER_STATE_PEAKS[name]],
        "reference": REFERENCE_KIND[name],
    }


def _recommendation(entries: list[dict]) -> dict:
    surviving = [e["candidate"] for e in entries if e["admitted"]]
    rejected = [
        {"candidate": e["candidate"], "reason": e["reason"], "stopped_at": e["stopped_at"]}
        for e in entries if not e["admitted"]
    ]
    walls = sorted(
        e["candidate"] for e in entries
        if e["admitted"] and e.get("wall") and e["wall"].get("wall") is True
    )
    parts = [
        f"{len(entries)} candidate configurations were screened and {len(surviving)} "
        f"survived every step."
    ]
    if rejected:
        by_reason: dict[str, list[str]] = {}
        for r in rejected:
            by_reason.setdefault(r["reason"], []).append(r["candidate"])
        bits = [
            f"{len(names)} for {reason} ({', '.join(sorted(names))})"
            for reason, names in sorted(by_reason.items())
        ]
        parts.append("Rejections: " + "; ".join(bits) + ".")
    if surviving:
        parts.append(
            "The surviving candidates carry into the epoch-3 change set as material "
            "for an owner decision, not as an adoption: nothing here edits the "
            "scored suite or the out-of-band suite."
        )
        if walls:
            parts.append(
                f"{len(walls)} of {len(surviving)} also read as a wall against the "
                f"SDIRK2 float reference at matched analytic cost. That verdict is "
                f"recorded on the candidate and did not select it."
            )
    else:
        parts.append(
            "Nothing survived. Read that as the result it is: at 16 bits the "
            "binding constraint on these candidates is Q15 dynamic range rather "
            "than stability, which changes the shape of the epoch-3 motivation."
        )
    return {
        "screened": len(entries),
        "surviving": len(surviving),
        "carry_forward": sorted(surviving),
        "walls_among_survivors": walls,
        "rejected": rejected,
        "note": " ".join(parts),
    }


def build_results(records: list[Record] | None = None, n: int = PEAK_STEPS) -> dict:
    """The full results document. Pure function of this module plus whatever
    records the caller passes; no wall clock, no archive read of its own."""
    entries = [screen(name, records, n) for name in CANDIDATES]
    # Counted from the records the caller passed rather than from a wall test, so
    # the number is honest even when no candidate survived far enough to use them.
    elites = 0
    if records:
        cls = _classical_hashes()
        elites = len({r.tableau_hash for r in records} - cls)
    return {
        "schema": _SCHEMA_DOC,
        "generated_from": {
            "archive_elites": elites,
            "archive_free": not records,
            "verifier_hash": _read_verifier_hash(),
        },
        "budget_cycles": BUDGET_CYCLES,
        "cost_model": COST_MODEL.name,
        "rounding": "floor (ASRS), per HANDOFF 4.2",
        "constants": {
            "min_overflow_margin": MIN_OVERFLOW_MARGIN,
            "min_increment_lsb": MIN_INCREMENT_LSB,
            "deriv_headroom": DERIV_HEADROOM,
            "wall_multiple": WALL_MULTIPLE,
            "ref_tol": REF_TOL,
            "scale_exponents": list(SCALE_EXPONENTS),
            "peak_steps": n,
            "jac_samples": JAC_SAMPLES,
            "start_deriv_bound": START_DERIV_BOUND,
            "stiffness_band": list(STIFFNESS_BAND),
        },
        "candidates": [_candidate_entry(name) for name in CANDIDATES],
        "screen": entries,
        "recommendation": _recommendation(entries),
        "adoption": {
            "scored_suite": (
                "rk_harness/problems.py and fixtures/problems.json, both "
                "verifier-pinned: an edit is the epoch-3 boundary and needs a "
                "VERIFIER_HASH re-pin in one change set"
            ),
            "out_of_band_suite": (
                "rk_harness/validation.py, unpinned, but its T8 tests assert that "
                "euler, heun2 and midpoint finish every stiff problem and that the "
                "stiffness ratio stays between 50 and 2000, and its name tuple is "
                "mirrored by hand in sidetrack.VALIDATION_NAMES, which feeds "
                "sidetrack.code_hash()"
            ),
            "taken_here": "neither",
        },
    }


def _walk_numbers(node, path: str, fail):
    if isinstance(node, dict):
        for k in sorted(node):
            _walk_numbers(node[k], f"{path}.{k}", fail)
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            _walk_numbers(v, f"{path}[{i}]", fail)
    elif isinstance(node, bool):
        return
    elif isinstance(node, (int, float)):
        if not math.isfinite(node):
            fail(f"{path} must be finite, got {node!r}")


def validate_results(doc: dict) -> None:
    """Raise ValueError if the document violates the schema described above."""
    def fail(msg: str):
        raise ValueError(f"stiffscreen results schema: {msg}")

    for key in ("schema", "generated_from", "budget_cycles", "cost_model", "rounding",
                "constants", "candidates", "screen", "recommendation", "adoption"):
        if key not in doc:
            fail(f"missing top-level key {key!r}")
    gf = doc["generated_from"]
    if not isinstance(gf.get("archive_elites"), int):
        fail("generated_from.archive_elites must be an integer")
    if not isinstance(gf.get("verifier_hash"), str):
        fail("generated_from.verifier_hash must be a string")

    cnames = [c["name"] for c in doc["candidates"]]
    if sorted(cnames) != sorted(CANDIDATES):
        fail(f"candidates must cover {CANDIDATES}, got {cnames}")
    for c in doc["candidates"]:
        for k in ("name", "origin", "equation", "family", "n_states", "y0", "t_end",
                  "deriv_scale", "peak", "reference"):
            if k not in c:
                fail(f"candidate {c.get('name')!r} missing {k!r}")
        if c["deriv_scale"] != 1.0:
            fail(f"candidate {c['name']!r} deriv_scale must be 1.0, got {c['deriv_scale']}")

    seen = set()
    for e in doc["screen"]:
        nm = e.get("candidate")
        if nm not in cnames:
            fail(f"screen entry references unknown candidate {nm!r}")
        if nm in seen:
            fail(f"duplicate screen entry {nm!r}")
        seen.add(nm)
        if not isinstance(e.get("admitted"), bool):
            fail(f"screen {nm!r} admitted must be a boolean")
        if not isinstance(e.get("reason"), str) or not e["reason"]:
            fail(f"screen {nm!r} missing a non-empty reason")
        for k in ("stopped_at", "scales", "scale_bounds", "measurement"):
            if k not in e:
                fail(f"screen {nm!r} missing {k!r}")
        if e["scales"] is not None:
            got = sorted(r["exponent"] for r in e["scales"])
            if got != sorted(SCALE_EXPONENTS):
                fail(f"screen {nm!r} scales must keep every exponent, got {got}")
        if e["admitted"]:
            for k in ("scale", "stiffness", "wall", "reference"):
                if e.get(k) is None:
                    fail(f"admitted candidate {nm!r} missing {k!r}")
            w = e["wall"]
            total = w["finishers"] + w["overflowed"] + w["nonfinite"]
            if total != w["methods_evaluated"]:
                fail(f"screen {nm!r} wall outcomes {total} do not cover "
                     f"{w['methods_evaluated']} evaluated methods")
    if len(seen) != len(cnames):
        fail("screen must cover every candidate exactly once")

    rec = doc["recommendation"]
    for k in ("screened", "surviving"):
        if not isinstance(rec.get(k), int):
            fail(f"recommendation.{k} must be an integer")
    if rec["screened"] != len(cnames):
        fail(f"recommendation.screened must be {len(cnames)}, got {rec['screened']}")
    if rec["surviving"] != sum(1 for e in doc["screen"] if e["admitted"]):
        fail("recommendation.surviving does not match the screen entries")
    if not isinstance(rec.get("note"), str) or not rec["note"]:
        fail("recommendation.note must be a non-empty string")
    for r in rec.get("rejected", []):
        if not r.get("reason"):
            fail(f"recommendation rejection {r.get('candidate')!r} missing a reason")

    _walk_numbers(doc, "doc", fail)


def write_results(doc: dict, path: Path | str | None = None) -> Path:
    out = Path(path) if path is not None else work_dir() / "stiffscreen" / "results.json"
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
    for e in doc["screen"]:
        head = f"  {e['candidate']}: {e['reason']}"
        if e["admitted"]:
            w = e["wall"]
            print(f"{head} at scale {e['scale']}, stiffness ratio "
                  f"{e['stiffness']['stiffness_ratio']:.1f}, "
                  f"{w['finishers']} finishers / {w['overflowed']} overflowed / "
                  f"{w['nonfinite']} nonfinite, wall {w['wall']}")
        else:
            b = e.get("scale_bounds")
            if b is not None and b["interval_is_empty"]:
                print(f"{head} (scale must be at or below {b['upper']:.4g} for range "
                      f"and at or above {b['lower']:.4g} for increments)")
            else:
                print(head)
    print(doc["recommendation"]["note"])


if __name__ == "__main__":
    main()
