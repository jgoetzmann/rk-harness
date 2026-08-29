"""Test problems -- HANDOFF section 11 / SPEC ### rk_harness/problems.py.

Seven problems: three in the search set, four held out. Every Problem carries a Q15
right-hand side built from the float64 physical-units derivative by `make_q15_rhs`,
a Q15 initial state, and a float64 reference solution.

K12 hazard: this module is in search.py's import graph, so the held-out tuple is
assigned exactly once at the bottom and never read here.
"""
from __future__ import annotations

import functools
import json
import math
from typing import Callable

import numpy as np
from mpmath import mp
from scipy.linalg import expm

from rk_harness.fixedpoint import q15_from_float
from rk_harness.paths import FIXTURES_DIR
from rk_harness.types import Problem, Q15


# --------------------------------------------------------------------------- fixture

def load_fixture() -> dict:
    """fixtures/problems.json, verbatim."""
    with open(FIXTURES_DIR / "problems.json", "r", encoding="utf-8") as fh:
        return json.load(fh)


_FIX: dict = load_fixture()
_NAMES: tuple[str, ...] = (
    "dahlquist", "damped_osc", "vanderpol_mild",
    "pendulum", "dc_motor", "rc_thermal", "quaternion",
)

PEAK: dict[str, float] = {name: float(_FIX[name]["peak"]) for name in _NAMES}
FAMILY: dict[str, str] = {name: str(_FIX[name]["family"]) for name in _NAMES}


# --------------------------------------------------------------------------- Q15 <-> physical

def to_physical(yq: tuple[Q15, ...], scale: float) -> tuple[float, ...]:
    """q / 32768 / scale for every component."""
    return tuple(q / 32768.0 / scale for q in yq)


def to_q15_state(y: tuple[float, ...], scale: float) -> tuple[Q15, ...]:
    """q15_from_float(v * scale) for every component; raises Q15OverflowError."""
    return tuple(q15_from_float(v * scale) for v in y)


def make_q15_rhs(phys: Callable, scale: float) -> Callable[[float, tuple[Q15, ...]], tuple[Q15, ...]]:
    """Wrap a physical-units float64 derivative as a Q15 derivative.

    Q15 state -> physical (q/32768/scale) -> phys(t, y) -> times scale -> q15_from_float
    (which raises Q15OverflowError if a component does not fit int16)."""
    def f(t: float, yq: tuple[Q15, ...]) -> tuple[Q15, ...]:
        y = to_physical(yq, scale)
        d = phys(t, y)
        return tuple(q15_from_float(v * scale) for v in d)
    return f


# --------------------------------------------------------------------------- parameters

_ZETA = 0.1
_OMEGA = 1.0
_MU = 0.5
_R = 2.0
_L = 0.5
_KE = 0.1
_KT = 0.1
_B = 0.02
_J = 0.02
_V = 1.0
_RC_A = ((-11.0, 10.0, 0.0), (5.0, -6.0, 1.0), (0.0, 2.0, -2.0))
_WX, _WY, _WZ = 0.3, 0.2, 0.5


# --------------------------------------------------------------------------- float64 derivatives

def _rhs_dahlquist(t: float, y: tuple[float, ...]) -> tuple[float, ...]:
    return (-y[0],)


def _rhs_damped_osc(t: float, y: tuple[float, ...]) -> tuple[float, ...]:
    x, v = y[0], y[1]
    return (v, -2.0 * _ZETA * _OMEGA * v - _OMEGA * _OMEGA * x)


def _rhs_vanderpol_mild(t: float, y: tuple[float, ...]) -> tuple[float, ...]:
    x, v = y[0], y[1]
    return (v, _MU * (1.0 - x * x) * v - x)


def _rhs_pendulum(t: float, y: tuple[float, ...]) -> tuple[float, ...]:
    theta, w = y[0], y[1]
    return (w, -math.sin(theta))


def _rhs_dc_motor(t: float, y: tuple[float, ...]) -> tuple[float, ...]:
    i, w = y[0], y[1]
    return ((-_R * i - _KE * w + _V) / _L, (_KT * i - _B * w) / _J)


def _rhs_rc_thermal(t: float, y: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(
        _RC_A[r][0] * y[0] + _RC_A[r][1] * y[1] + _RC_A[r][2] * y[2]
        for r in range(3)
    )


def _rhs_quaternion(t: float, y: tuple[float, ...]) -> tuple[float, ...]:
    q0, q1, q2, q3 = y[0], y[1], y[2], y[3]
    return (
        0.5 * (-_WX * q1 - _WY * q2 - _WZ * q3),
        0.5 * (_WX * q0 + _WZ * q2 - _WY * q3),
        0.5 * (_WY * q0 - _WZ * q1 + _WX * q3),
        0.5 * (_WZ * q0 + _WY * q1 - _WX * q2),
    )


FLOAT_RHS: dict[str, Callable[[float, tuple[float, ...]], tuple[float, ...]]] = {
    "dahlquist": _rhs_dahlquist,
    "damped_osc": _rhs_damped_osc,
    "vanderpol_mild": _rhs_vanderpol_mild,
    "pendulum": _rhs_pendulum,
    "dc_motor": _rhs_dc_motor,
    "rc_thermal": _rhs_rc_thermal,
    "quaternion": _rhs_quaternion,
}


# --------------------------------------------------------------------------- references

def _ref_dahlquist(t: float) -> tuple[float, ...]:
    return (math.exp(-t),)


def _ref_damped_osc(t: float) -> tuple[float, ...]:
    # x(t)  = e^{-a t} (cos(wd t) + (a/wd) sin(wd t)),  a = zeta*omega,  wd = omega sqrt(1 - zeta^2)
    # x'(t) = -e^{-a t} (omega^2 / wd) sin(wd t)        (a^2 + wd^2 = omega^2)
    a = _ZETA * _OMEGA
    wd = _OMEGA * math.sqrt(1.0 - _ZETA * _ZETA)
    e = math.exp(-a * t)
    s = math.sin(wd * t)
    c = math.cos(wd * t)
    x = e * (c + (a / wd) * s)
    v = -e * (_OMEGA * _OMEGA / wd) * s
    return (x, v)


_DC_Y0 = (float(_FIX["dc_motor"]["y0"][0]), float(_FIX["dc_motor"]["y0"][1]))
_DC_AUG = np.array(
    [
        [-_R / _L, -_KE / _L, _V / _L],
        [_KT / _J, -_B / _J, 0.0],
        [0.0, 0.0, 0.0],
    ],
    dtype=float,
)


def _ref_dc_motor(t: float) -> tuple[float, ...]:
    # affine y' = M y + g  ->  augmented [[M, g], [0, 0]] acting on [y; 1]
    if t == 0.0:
        return _DC_Y0
    v = expm(_DC_AUG * float(t)) @ np.array([_DC_Y0[0], _DC_Y0[1], 1.0], dtype=float)
    return (float(v[0]), float(v[1]))


_RC_Y0 = tuple(float(v) for v in _FIX["rc_thermal"]["y0"])
_RC_M = np.array(_RC_A, dtype=float)


def _ref_rc_thermal(t: float) -> tuple[float, ...]:
    if t == 0.0:
        return _RC_Y0
    v = expm(_RC_M * float(t)) @ np.array(_RC_Y0, dtype=float)
    return (float(v[0]), float(v[1]), float(v[2]))


def _ref_quaternion(t: float) -> tuple[float, ...]:
    # q(t) = (cos(|w| t / 2), w_hat sin(|w| t / 2)) for constant body rates from (1,0,0,0)
    wn = math.sqrt(_WX * _WX + _WY * _WY + _WZ * _WZ)
    half = 0.5 * wn * t
    s = math.sin(half)
    return (math.cos(half), _WX / wn * s, _WY / wn * s, _WZ / wn * s)


@functools.lru_cache(maxsize=None)
def _vdp_solution():
    with mp.workdps(30):
        def F(x, y):
            return [y[1], _MU * (1 - y[0] ** 2) * y[1] - y[0]]
        return mp.odefun(F, 0, [mp.mpf(1), mp.mpf(0)])


@functools.lru_cache(maxsize=None)
def _ref_vanderpol_mild(t: float) -> tuple[float, ...]:
    with mp.workdps(30):
        sol = _vdp_solution()
        v = sol(mp.mpf(t))
        return (float(v[0]), float(v[1]))


@functools.lru_cache(maxsize=None)
def _pend_solution():
    with mp.workdps(30):
        def F(x, y):
            return [y[1], -mp.sin(y[0])]
        return mp.odefun(F, 0, [mp.mpf(1), mp.mpf(0)])


@functools.lru_cache(maxsize=None)
def _ref_pendulum(t: float) -> tuple[float, ...]:
    with mp.workdps(30):
        sol = _pend_solution()
        v = sol(mp.mpf(t))
        return (float(v[0]), float(v[1]))


# --------------------------------------------------------------------------- problems

def _make(name: str, reference: Callable[[float], tuple[float, ...]]) -> Problem:
    d = _FIX[name]
    scale = float(d["scale"])
    y0_phys = tuple(float(v) for v in d["y0"])
    return Problem(
        name=name,
        n_states=int(d["n_states"]),
        f=make_q15_rhs(FLOAT_RHS[name], scale),
        y0=to_q15_state(y0_phys, scale),
        t_end=float(d["t_end"]),
        scale=scale,
        reference=reference,
        family=d["family"],
    )


_dahlquist = _make("dahlquist", _ref_dahlquist)
_damped_osc = _make("damped_osc", _ref_damped_osc)
_vanderpol_mild = _make("vanderpol_mild", _ref_vanderpol_mild)
_pendulum = _make("pendulum", _ref_pendulum)
_dc_motor = _make("dc_motor", _ref_dc_motor)
_rc_thermal = _make("rc_thermal", _ref_rc_thermal)
_quaternion = _make("quaternion", _ref_quaternion)

PROBLEMS: dict[str, Problem] = {
    "dahlquist": _dahlquist,
    "damped_osc": _damped_osc,
    "vanderpol_mild": _vanderpol_mild,
    "pendulum": _pendulum,
    "dc_motor": _dc_motor,
    "rc_thermal": _rc_thermal,
    "quaternion": _quaternion,
}


# --------------------------------------------------------------------------- error metric

_E0_PENDULUM = 1.0 - math.cos(1.0)


def error_metric(name: str, y_final_phys: tuple[float, ...]) -> float:
    """Per-problem error in physical units.

    pendulum:   |E(y) - E0| / E0,  E = w^2/2 + (1 - cos theta), E0 = 1 - cos(1)
    quaternion: | ||q|| - 1 |
    otherwise:  ||y - reference(t_end)||_2 / PEAK[name]
    """
    if name == "pendulum":
        theta, w = y_final_phys[0], y_final_phys[1]
        e = 0.5 * w * w + (1.0 - math.cos(theta))
        return abs(e - _E0_PENDULUM) / _E0_PENDULUM
    if name == "quaternion":
        return abs(math.sqrt(sum(v * v for v in y_final_phys)) - 1.0)
    p = PROBLEMS[name]
    ref = p.reference(p.t_end)
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(y_final_phys, ref))) / PEAK[name]


# --------------------------------------------------------------------------- sets (plain assignments only)

SEARCH_SET: tuple[Problem, ...] = (_dahlquist, _damped_osc, _vanderpol_mild)
HELDOUT_SET: tuple[Problem, ...] = (_pendulum, _dc_motor, _rc_thermal, _quaternion)
QUARANTINE_SET: tuple[Problem, ...] = ()
