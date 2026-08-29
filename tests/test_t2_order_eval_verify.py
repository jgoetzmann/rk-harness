"""T2 — order conditions, problems/simulation, evaluator, verifier.

Written from .fullsend/SPEC.md and .fullsend/HANDOFF.md only, before any
implementation existed. Every test name carries the behavior ID it arbitrates.

Expensive results (``evaluate``, ``measured_order``, ``stability_extents``,
``verify`` reaching steps 6-9) are computed once per module through
module-scoped fixtures; the tests that use them are marked ``slow``.
"""
from __future__ import annotations

import dataclasses
import json
import math
import random
from fractions import Fraction

import pytest

import rk_harness.evaluator
from rk_harness.orderconditions import (
    trees,
    all_trees,
    tree_order,
    gamma,
    elementary_weight,
    residuals,
    achieved_order_symbolic,
    is_dyadic,
    dyadic_order_bound,
    b_linear_system,
)
from rk_harness.problems import (
    SEARCH_SET,
    HELDOUT_SET,
    PROBLEMS,
    FLOAT_RHS,
    PEAK,
    FAMILY,
    make_q15_rhs,
    to_physical,
    to_q15_state,
    error_metric,
    load_fixture,
)
from rk_harness.simulate import solve_float, solve_q15, steps_for_budget, problem_error
from rk_harness.evaluator import (
    evaluate,
    measured_order,
    measured_order_with_points,
    stability_polynomial,
    stability_extents,
    error_constant,
    DEFAULT_BUDGET_CYCLES,
)
from rk_harness.verifier import verify, verify_with_score, REJECT_CODES, cheap_checks
from rk_harness.costmodel import M0PLUS_FAST, M0PLUS_SLOW
from rk_harness.tableau import make_tableau, content_hash
from rk_harness.fixedpoint import Q15OverflowError, q15_from_float
from rk_harness.paths import FIXTURES_DIR
from rk_harness.types import Tableau, ScoreVector, VerdictReason

slow = pytest.mark.slow

F0 = Fraction(0)
F1 = Fraction(1)

# --------------------------------------------------------------------------- #
# Inline fixtures (HANDOFF §9.1, exact) — duplicated deliberately, never floats
# --------------------------------------------------------------------------- #

CLASSICAL_NAMES = ("euler", "midpoint", "heun2", "ralston2", "heun3", "kutta3", "rk4", "rk38")

_CLASSICAL_SRC = {
    "euler": ([["0"]], ["1"], ["0"]),
    "midpoint": ([["0", "0"], ["1/2", "0"]], ["0", "1"], ["0", "1/2"]),
    "heun2": ([["0", "0"], ["1", "0"]], ["1/2", "1/2"], ["0", "1"]),
    "ralston2": ([["0", "0"], ["2/3", "0"]], ["1/4", "3/4"], ["0", "2/3"]),
    "heun3": (
        [["0", "0", "0"], ["1/3", "0", "0"], ["0", "2/3", "0"]],
        ["1/4", "0", "3/4"],
        ["0", "1/3", "2/3"],
    ),
    "kutta3": (
        [["0", "0", "0"], ["1/2", "0", "0"], ["-1", "2", "0"]],
        ["1/6", "2/3", "1/6"],
        ["0", "1/2", "1"],
    ),
    "rk4": (
        [["0", "0", "0", "0"], ["1/2", "0", "0", "0"], ["0", "1/2", "0", "0"], ["0", "0", "1", "0"]],
        ["1/6", "1/3", "1/3", "1/6"],
        ["0", "1/2", "1/2", "1"],
    ),
    "rk38": (
        [["0", "0", "0", "0"], ["1/3", "0", "0", "0"], ["-1/3", "1", "0", "0"], ["1", "-1", "1", "0"]],
        ["1/8", "3/8", "3/8", "1/8"],
        ["0", "1/3", "2/3", "1"],
    ),
}

SEARCH_NAMES = ("dahlquist", "damped_osc", "vanderpol_mild")
HELDOUT_NAMES = ("pendulum", "dc_motor", "rc_thermal", "quaternion")
ALL_PROBLEM_NAMES = SEARCH_NAMES + HELDOUT_NAMES


def _classical(name: str) -> Tableau:
    A, b, c = _CLASSICAL_SRC[name]
    return make_tableau(A, b, c)


def _ovf() -> Tableau:
    """The V9 / B22 / B28 overflow tableau, exactly as SPEC writes it."""
    return make_tableau([[0, 0], [100, 0]], [1, 0], [0, 100])


def _rk4_b0_perturbed(rk4: Tableau) -> Tableau:
    b = list(rk4.b)
    b[0] += Fraction(1, 1000)
    return dataclasses.replace(rk4, b=tuple(b))


def _rk4_bad_c(rk4: Tableau) -> Tableau:
    c = list(rk4.c)
    c[1] = Fraction(1, 3)
    return dataclasses.replace(rk4, c=tuple(c))


def _rk4_not_explicit(rk4: Tableau) -> Tableau:
    A = [list(r) for r in rk4.A]
    A[0][1] = Fraction(1, 2)
    return dataclasses.replace(rk4, A=tuple(tuple(r) for r in A))


def _tab_b_40000() -> Tableau:
    # order 1 holds exactly (sum b == 1) so step 5 is the first check that can fire
    return Tableau(
        A=((F0, F0), (Fraction(1, 2), F0)),
        b=(Fraction(40000), Fraction(-39999)),
        c=(F0, Fraction(1, 2)),
    )


def _tab_b_neg_40000() -> Tableau:
    return Tableau(
        A=((F0, F0), (Fraction(1, 2), F0)),
        b=(Fraction(-40000), Fraction(40001)),
        c=(F0, Fraction(1, 2)),
    )


def _tab_A_40000() -> Tableau:
    return Tableau(A=((F0, F0), (Fraction(40000), F0)), b=(F1, F0), c=(F0, Fraction(40000)))


def _tab_A_32768() -> Tableau:
    return Tableau(A=((F0, F0), (Fraction(32768), F0)), b=(F1, F0), c=(F0, Fraction(32768)))


def _tab_tiny() -> Tableau:
    tiny = Fraction(1, 2**21)
    return Tableau(A=((F0, F0), (tiny, F0)), b=(F1, F0), c=(F0, tiny))


def _tab_tinier() -> Tableau:
    tiny = Fraction(1, 2**22)
    return Tableau(A=((F0, F0), (tiny, F0)), b=(F1, F0), c=(F0, tiny))


def _tab_tiny_nondyadic() -> Tableau:
    # |x * 2**s| < 1/2 for every s <= 20, so round() gives m == 0 at every shift
    tiny = Fraction(1, 3 * 2**20)
    return Tableau(A=((F0, F0), (tiny, F0)), b=(F1, F0), c=(F0, tiny))


def _tab_b_neg_32768() -> Tableau:
    return Tableau(
        A=((F0, F0), (Fraction(1, 2), F0)),
        b=(Fraction(-32768), Fraction(32769)),
        c=(F0, Fraction(1, 2)),
    )


def _per_problem(value: float = 1e-4) -> dict[str, float]:
    d: dict[str, float] = {}
    for p in ALL_PROBLEM_NAMES:
        d[p] = value
        d[f"slow:{p}"] = value
        d[f"avr_approx:{p}"] = value
    for m in ("slow", "avr_approx"):
        d[f"{m}:search_error"] = value
        d[f"{m}:heldout_error"] = value
    return d


def _expected_per_problem_keys() -> set[str]:
    return set(_per_problem().keys())


def _healthy_sv(**over) -> ScoreVector:
    """A hand-built ScoreVector (all 12 fields) that passes verifier steps 6-9."""
    fields = dict(
        measured_order=4.07,
        order_fit_points=3,
        error_constant=0.0136,
        stability_real=-2.785294,
        stability_imag=2.828427,
        cycles={"m0plus_fast": 33, "m0plus_slow": 85, "avr_approx": 134},
        csd_weight_total=34,
        coeff_quant_error=5.086e-06,
        search_error=1e-4,
        heldout_error=2e-4,
        overflow_margin=2.0,
        per_problem=_per_problem(),
    )
    fields.update(over)
    return ScoreVector(**fields)


def _rms(values) -> float:
    values = list(values)
    return math.sqrt(sum(v * v for v in values) / len(values))


def _is_canonical(t: tuple) -> bool:
    return list(t) == sorted(t) and all(_is_canonical(s) for s in t)


# --------------------------------------------------------------------------- #
# Module-scoped fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def fx() -> dict:
    with open(FIXTURES_DIR / "classical.json", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def pfx() -> dict:
    with open(FIXTURES_DIR / "problems.json", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def ct() -> dict[str, Tableau]:
    return {n: _classical(n) for n in CLASSICAL_NAMES}


@pytest.fixture(scope="module")
def sv_rk4(ct) -> ScoreVector:
    return evaluate(ct["rk4"], 65536)


@pytest.fixture(scope="module")
def sv_euler(ct) -> ScoreVector:
    return evaluate(ct["euler"], 65536)


@pytest.fixture(scope="module")
def sv_heun3(ct) -> ScoreVector:
    return evaluate(ct["heun3"], 65536)


@pytest.fixture(scope="module")
def mo(ct) -> dict[str, tuple]:
    return {n: measured_order_with_points(ct[n]) for n in ("euler", "heun2", "kutta3", "rk4")}


@pytest.fixture(scope="module")
def stab(ct) -> dict[str, tuple[float, float]]:
    return {n: stability_extents(ct[n]) for n in ("euler", "midpoint", "heun2", "kutta3", "rk4", "rk38")}


# =========================================================================== #
# Order conditions
# =========================================================================== #


def test_G1_achieved_order_symbolic_rk4(ct):
    assert achieved_order_symbolic(ct["rk4"]) == 4


def test_G1_achieved_order_symbolic_rk4_capped_by_max_order(ct):
    assert achieved_order_symbolic(ct["rk4"], max_order=3) == 3
    assert achieved_order_symbolic(ct["rk4"], 8) == 4


def test_G2_achieved_order_symbolic_heun2(ct):
    assert achieved_order_symbolic(ct["heun2"]) == 2


def test_G3_achieved_order_symbolic_midpoint(ct):
    assert achieved_order_symbolic(ct["midpoint"]) == 2


def test_G4_achieved_order_symbolic_euler(ct):
    assert achieved_order_symbolic(ct["euler"]) == 1


def test_G5_residuals_rk4_order4_eight_exact_zeros(ct):
    r = residuals(ct["rk4"], 4)
    assert len(r) == 8
    for x in r:
        assert isinstance(x, Fraction)
        assert x == 0


def test_G5_residuals_euler_order2_is_exact_fraction(ct):
    # Phi(((),)) = sum b_i c_i = 0 for euler, so residual is 0 - 1/2 exactly
    r = residuals(ct["euler"], 2)
    assert r == [Fraction(0), Fraction(-1, 2)]
    assert all(isinstance(x, Fraction) for x in r)


def test_G5_elementary_weight_matches_definition(ct):
    rk4 = ct["rk4"]
    assert elementary_weight(rk4, ()) == 1
    assert elementary_weight(rk4, ((),)) == Fraction(1, 2)
    assert elementary_weight(rk4, ((), ())) == Fraction(1, 3)
    assert elementary_weight(rk4, (((),),)) == Fraction(1, 6)
    assert elementary_weight(ct["euler"], ((),)) == 0
    assert isinstance(elementary_weight(rk4, ((),)), Fraction)


def test_G6_rk4_order5_residuals_all_nonzero_match_fixture(ct, fx):
    r = residuals(ct["rk4"], 5)
    assert len(r) == 17
    order5 = r[8:]
    assert len(order5) == 9
    for x in order5:
        assert isinstance(x, Fraction)
        assert x != 0
    expected = sorted(Fraction(s) for s in fx["_rk4_order5_residuals"])
    assert sorted(order5) == expected


def test_G17_tree_counts_A000081(fx):
    counts = [len(trees(k)) for k in range(1, 7)]
    assert counts == [1, 1, 2, 4, 9, 20]
    for k in range(1, 7):
        assert len(trees(k)) == fx["_tree_counts"][str(k)]
    assert len(all_trees(4)) == 8
    assert len(all_trees(6)) == 37
    assert trees(1) == [()]


def test_G17_trees_are_canonical_and_distinct():
    for k in range(1, 7):
        ts = trees(k)
        assert len(set(ts)) == len(ts)
        for t in ts:
            assert isinstance(t, tuple)
            assert tree_order(t) == k
            assert _is_canonical(t)
    assert set(trees(3)) == {((), ()), (((),),)}


def test_G17_all_trees_concatenated_in_increasing_order():
    orders = [tree_order(t) for t in all_trees(5)]
    assert orders == [1, 2, 3, 3, 4, 4, 4, 4] + [5] * 9
    assert all_trees(5)[:8] == all_trees(4)


@pytest.mark.parametrize("name", CLASSICAL_NAMES)
def test_G18_achieved_order_symbolic_all_classical_matches_fixture(ct, fx, name):
    assert achieved_order_symbolic(ct[name]) == fx[name]["order"]


def test_G18_inline_tableaus_hash_equal_to_fixture_tableaus(ct, fx):
    hashes = set()
    for name in CLASSICAL_NAMES:
        from_fixture = make_tableau(fx[name]["A"], fx[name]["b"], fx[name]["c"])
        assert content_hash(ct[name]) == content_hash(from_fixture), name
        assert ct[name] == from_fixture, name
        hashes.add(content_hash(ct[name]))
    assert len(hashes) == 8


def test_B10_gamma_and_tree_order():
    assert gamma(()) == 1
    assert gamma(((),)) == 2
    assert gamma(((), ())) == 3
    assert gamma((((),),)) == 6
    assert gamma((((),), ())) == 8
    assert tree_order(()) == 1
    assert tree_order(((),)) == 2
    assert tree_order((((),), ())) == 4


def test_B11_is_dyadic_and_bound():
    assert is_dyadic(Fraction(3, 8)) is True
    assert is_dyadic(Fraction(1, 3)) is False
    assert is_dyadic(Fraction(2)) is True
    assert is_dyadic(Fraction(0)) is True
    assert is_dyadic(Fraction(-3, 16)) is True
    assert is_dyadic(Fraction(1, 6)) is False
    assert is_dyadic(Fraction(5, 12)) is False
    assert dyadic_order_bound() == 2


@pytest.mark.parametrize("name", CLASSICAL_NAMES)
def test_B12_perturbed_b0_breaks_order_one(ct, name):
    b = list(ct[name].b)
    b[0] += Fraction(1, 1000)
    t = dataclasses.replace(ct[name], b=tuple(b))
    assert achieved_order_symbolic(t) == 0
    assert residuals(t, 1) == [Fraction(1, 1000)]
    if name == "rk4":
        assert t == _rk4_b0_perturbed(ct["rk4"])


def test_B12_perturbation_preserving_sum_b_breaks_order_two(ct):
    rk4 = ct["rk4"]
    b = list(rk4.b)
    b[0] += Fraction(1, 1000)
    b[1] -= Fraction(1, 1000)
    t = dataclasses.replace(rk4, b=tuple(b))
    assert residuals(t, 1) == [Fraction(0)]
    assert achieved_order_symbolic(t) == 1


def test_B13_b_linear_system_rk4(ct):
    rk4 = ct["rk4"]
    G, r = b_linear_system(rk4.A, rk4.c, 4)
    assert len(G) == 8
    assert len(r) == 8
    for row in G:
        assert len(row) == 4
        assert all(isinstance(x, Fraction) for x in row)
    assert all(isinstance(x, Fraction) for x in r)
    assert G[0] == [Fraction(1)] * 4
    assert r[0] == 1
    assert r[1] == Fraction(1, 2)
    for k in range(8):
        assert sum(G[k][i] * rk4.b[i] for i in range(4)) == r[k]


def test_B13_b_linear_system_rejects_wrong_b(ct):
    # rk38's internal weights with rk4's b: order-3 bushy condition fails
    rk38 = ct["rk38"]
    rk4 = ct["rk4"]
    G, r = b_linear_system(rk38.A, rk38.c, 4)
    lhs = [sum(G[k][i] * rk4.b[i] for i in range(4)) for k in range(8)]
    assert lhs != r
    assert lhs[0] == r[0]
    assert lhs[1] == r[1]


# =========================================================================== #
# Problems and simulation
# =========================================================================== #


def test_B16_search_and_heldout_sets_in_order():
    assert tuple(p.name for p in SEARCH_SET) == SEARCH_NAMES
    assert tuple(p.name for p in HELDOUT_SET) == HELDOUT_NAMES
    assert set(PROBLEMS) == set(ALL_PROBLEM_NAMES)
    assert len(PROBLEMS) == 7
    assert set(FLOAT_RHS) >= set(ALL_PROBLEM_NAMES)
    for p in SEARCH_SET + HELDOUT_SET:
        assert PROBLEMS[p.name] is p


@pytest.mark.parametrize("name", ALL_PROBLEM_NAMES)
def test_B16_problem_fields_match_fixture(pfx, name):
    p = PROBLEMS[name]
    row = pfx[name]
    assert p.name == name
    assert p.n_states == row["n_states"]
    assert p.t_end == row["t_end"]
    assert p.scale == row["scale"]
    assert p.family == row["family"]
    assert FAMILY[name] == row["family"]
    assert PEAK[name] == row["peak"]
    assert p.y0 == to_q15_state(tuple(row["y0"]), row["scale"])
    assert len(p.y0) == row["n_states"]


def test_B16_load_fixture_equals_problems_json(pfx):
    assert load_fixture() == pfx


def test_B17_dahlquist_reference():
    ref = PROBLEMS["dahlquist"].reference(10.0)
    assert len(ref) == 1
    assert abs(ref[0] - math.exp(-10.0)) < 1e-15
    assert PROBLEMS["dahlquist"].reference(0.0) == pytest.approx((1.0,), abs=1e-15)


def test_B17_rc_thermal_reference_at_zero():
    ref = PROBLEMS["rc_thermal"].reference(0.0)
    assert len(ref) == 3
    assert ref == pytest.approx((1.0, 0.0, 0.0), abs=1e-12)


@pytest.mark.parametrize("t", [0.0, 7.5, 30.0])
def test_B17_quaternion_reference_unit_norm(t):
    q = PROBLEMS["quaternion"].reference(t)
    assert len(q) == 4
    assert abs(math.sqrt(sum(x * x for x in q)) - 1.0) < 1e-12
    if t == 0.0:
        assert q == pytest.approx((1.0, 0.0, 0.0, 0.0), abs=1e-12)


def test_B17_dc_motor_reference_matches_float_rk4(ct):
    ref = PROBLEMS["dc_motor"].reference(5.0)
    num = solve_float(ct["rk4"], FLOAT_RHS["dc_motor"], (0.0, 0.0), 5.0, 20000)
    assert len(ref) == 2
    for a, b in zip(ref, num):
        assert abs(a - b) < 1e-9


@pytest.mark.parametrize("t", [0.0, 1.0, 40.0])
def test_B17_damped_osc_reference_is_analytic_x_and_xdot(t):
    zeta, omega = 0.1, 1.0
    wd = omega * math.sqrt(1 - zeta**2)
    env = math.exp(-zeta * omega * t)
    x = env * (math.cos(wd * t) + (zeta * omega / wd) * math.sin(wd * t))
    xdot = -zeta * omega * x + env * (-wd * math.sin(wd * t) + zeta * omega * math.cos(wd * t))
    ref = PROBLEMS["damped_osc"].reference(t)
    assert len(ref) == 2
    assert ref[0] == pytest.approx(x, abs=1e-9)
    assert ref[1] == pytest.approx(xdot, abs=1e-9)


def test_B18_state_scaling_roundtrip_and_dahlquist_rhs():
    q = to_q15_state((0.5, -0.25), 0.25)
    assert q == (4096, -2048)
    back = to_physical(q, 0.25)
    tol = 2**-15 / 0.25
    assert abs(back[0] - 0.5) <= tol
    assert abs(back[1] - (-0.25)) <= tol
    assert PROBLEMS["dahlquist"].f(0.0, (8192,)) == (-8192,)


def test_B18_make_q15_rhs_raises_on_unrepresentable_derivative():
    f = make_q15_rhs(lambda t, y: (100.0,), 0.25)
    with pytest.raises(Q15OverflowError):
        f(0.0, (0,))


@pytest.mark.parametrize(
    "y, scale",
    [((4.0,), 0.25), ((-4.5,), 0.25), ((1.0,), 1.0), ((2.0,), 0.5), ((0.0, 16.0), 0.0625)],
)
def test_B18_to_q15_state_raises_out_of_range(y, scale):
    with pytest.raises(Q15OverflowError):
        to_q15_state(y, scale)


def test_B19_solve_float_rk4_dahlquist(ct):
    y = solve_float(ct["rk4"], FLOAT_RHS["dahlquist"], (1.0,), 10.0, 1000)
    assert len(y) == 1
    assert abs(y[0] - math.exp(-10.0)) < 1e-9


def test_B20_solve_q15_rk4_dahlquist(ct):
    final, max_abs_q = solve_q15(ct["rk4"], PROBLEMS["dahlquist"], 500)
    assert len(final) == 1
    assert isinstance(final[0], int)
    expected_q = q15_from_float(math.exp(-10.0) * 0.25)
    assert abs(final[0] - expected_q) <= 4
    assert max_abs_q == 8192
    err, mq = problem_error(ct["rk4"], PROBLEMS["dahlquist"], 500)
    assert 0.0 <= err < 1e-3
    assert mq == 8192


def test_B21_steps_for_budget(ct):
    assert steps_for_budget(ct["rk4"], M0PLUS_FAST, 1, 65536) == 1985
    assert steps_for_budget(ct["rk38"], M0PLUS_SLOW, 2, 65536) == 512


@pytest.mark.parametrize("budget", [0, 1, 10, 32])
def test_B21_steps_for_budget_too_small_is_zero(ct, budget):
    assert steps_for_budget(ct["rk4"], M0PLUS_FAST, 1, budget) == 0
    assert steps_for_budget(ct["rk4"], M0PLUS_SLOW, 1, budget) == 0


def test_B21_steps_for_budget_boundary(ct):
    assert steps_for_budget(ct["rk4"], M0PLUS_FAST, 1, 33) == 1
    assert steps_for_budget(ct["rk4"], M0PLUS_FAST, 1, 65) == 1
    assert steps_for_budget(ct["rk4"], M0PLUS_FAST, 1, 66) == 2


def test_B22_solve_q15_overflow_tableau_raises_on_large_h():
    with pytest.raises(Q15OverflowError):
        solve_q15(_ovf(), PROBLEMS["dahlquist"], 8)


def test_B22_solve_q15_rk4_raises_when_h_ge_one(ct):
    with pytest.raises(Q15OverflowError):
        solve_q15(ct["rk4"], PROBLEMS["dahlquist"], 8)


@pytest.mark.parametrize("name", ALL_PROBLEM_NAMES)
def test_B22_solve_q15_single_step_raises_for_every_problem(ct, name):
    # every t_end >= 4, so n = 1 gives h >= 1 and q15_from_float(h) cannot represent it
    with pytest.raises(Q15OverflowError):
        solve_q15(ct["rk4"], PROBLEMS[name], 1)


def test_B22_problem_error_propagates_overflow(ct):
    with pytest.raises(Q15OverflowError):
        problem_error(_ovf(), PROBLEMS["dahlquist"], 8)
    with pytest.raises(Q15OverflowError):
        problem_error(ct["rk4"], PROBLEMS["dahlquist"], 8)


def test_B23_error_metric_zero_at_reference():
    assert error_metric("pendulum", (1.0, 0.0)) == 0.0
    assert error_metric("quaternion", (0.6, 0.8, 0.0, 0.0)) == pytest.approx(0.0, abs=1e-12)
    assert error_metric("dahlquist", (math.exp(-10.0),)) == pytest.approx(0.0, abs=1e-12)


def test_B23_error_metric_positive_away_from_reference():
    assert error_metric("pendulum", (1.0, 0.5)) > 0.0
    assert error_metric("quaternion", (1.0, 1.0, 0.0, 0.0)) == pytest.approx(math.sqrt(2) - 1.0, abs=1e-12)
    assert error_metric("dahlquist", (1.0,)) == pytest.approx((1.0 - math.exp(-10.0)) / PEAK["dahlquist"], abs=1e-12)


# =========================================================================== #
# Evaluator
# =========================================================================== #


@slow
def test_G7_measured_order_rk4(ct, mo):
    v = measured_order(ct["rk4"])
    assert isinstance(v, float)
    assert abs(v - 4.0) <= 0.10
    assert v == mo["rk4"][0]


@slow
def test_G7_measured_order_rk4_matches_fixture_value(mo, fx):
    assert mo["rk4"][0] == pytest.approx(fx["_measured_order_dahlquist"]["rk4"]["measured"], abs=1e-3)


@slow
def test_G8_measured_order_heun2(ct, mo, fx):
    v = measured_order(ct["heun2"])
    assert abs(v - 2.0) <= 0.05
    assert v == mo["heun2"][0]
    assert v == pytest.approx(fx["_measured_order_dahlquist"]["heun2"]["measured"], abs=1e-3)


@slow
def test_G9_measured_order_kutta3(ct, mo, fx):
    v = measured_order(ct["kutta3"])
    assert abs(v - 3.0) <= 0.05
    assert v == mo["kutta3"][0]
    assert v == pytest.approx(fx["_measured_order_dahlquist"]["kutta3"]["measured"], abs=1e-3)


@slow
def test_G23_measured_order_euler(ct, mo, fx):
    v = measured_order(ct["euler"])
    assert abs(v - 1.0) <= 0.05
    assert v == mo["euler"][0]
    assert v == pytest.approx(fx["_measured_order_dahlquist"]["euler"]["measured"], abs=1e-3)


@slow
def test_G27_order_fit_points(mo):
    assert mo["euler"][1] == 7
    assert mo["heun2"][1] == 8
    assert mo["kutta3"][1] == 5
    assert mo["rk4"][1] == 3
    for name in ("euler", "heun2", "kutta3", "rk4"):
        assert isinstance(mo[name][1], int)
        assert isinstance(mo[name][0], float)


@slow
def test_V10d_measured_order_none_when_no_window():
    # y' = -y with b = 1000: the step factor (1 - 1000 h) exceeds 1 in magnitude for
    # every n <= 4096, so those errors are non-finite; only the n=8192/16384 pair
    # survives, giving a single slope, fewer than the two the rule requires.
    t = make_tableau([[0]], [1000], [0])
    assert measured_order(t) is None
    assert measured_order_with_points(t) == (None, 0)


@slow
def test_G10_stability_real_rk4(stab):
    assert abs(stab["rk4"][0] - (-2.785294)) <= 0.001


@slow
def test_G11_stability_imag_rk4(stab):
    assert abs(stab["rk4"][1] - 2.828427) <= 0.001


@slow
def test_G12_stability_real_euler(stab):
    assert abs(stab["euler"][0] - (-2.0)) <= 0.001


@slow
def test_G13_stability_real_heun2(stab):
    assert abs(stab["heun2"][0] - (-2.0)) <= 0.001


@slow
def test_G14_stability_imag_euler_heun2_midpoint_zero(stab):
    assert 0.0 <= stab["euler"][1] < 0.01
    assert 0.0 <= stab["heun2"][1] < 0.01
    assert 0.0 <= stab["midpoint"][1] < 0.01


@slow
def test_G15_stability_imag_kutta3(stab):
    assert abs(stab["kutta3"][1] - 1.732051) <= 0.001


@slow
def test_G16_stability_real_kutta3(stab):
    assert abs(stab["kutta3"][0] - (-2.512745)) <= 0.001


@slow
@pytest.mark.parametrize("name", ["euler", "midpoint", "heun2", "kutta3", "rk4", "rk38"])
def test_G10_G16_stability_extents_match_fixture(stab, fx, name):
    real, imag = stab[name]
    row = fx["_stability"][name]
    assert isinstance(real, float) and isinstance(imag, float)
    assert abs(real - row["real"]) <= 0.001
    if row["imag"] == 0.0:
        assert 0.0 <= imag < 0.01
    else:
        assert abs(imag - row["imag"]) <= 0.001


@slow
def test_G12_stability_extents_of_scaled_euler():
    # R(z) = 1 + 2z: |R| <= 1 on [-1, 0], never on the imaginary axis
    t = make_tableau([[0]], [2], [0])
    real, imag = stability_extents(t)
    assert abs(real - (-1.0)) <= 0.001
    assert 0.0 <= imag < 0.01


def test_G19_stability_polynomial_rk4_equals_rk38(ct):
    p4 = stability_polynomial(ct["rk4"])
    p38 = stability_polynomial(ct["rk38"])
    expected = [Fraction(1), Fraction(1), Fraction(1, 2), Fraction(1, 6), Fraction(1, 24)]
    assert p4 == expected
    assert p38 == expected
    assert p4 == p38
    assert all(isinstance(x, Fraction) for x in p4)


def test_G19_stability_polynomial_matches_fixture(ct, fx):
    for name in ("euler", "midpoint", "heun2", "kutta3", "rk4", "rk38"):
        expected = [Fraction(s) for s in fx["_stability"][name]["R"]]
        assert stability_polynomial(ct[name]) == expected, name


def test_G19_stability_polynomials_differ_across_orders(ct):
    assert stability_polynomial(ct["heun2"]) != stability_polynomial(ct["kutta3"])
    assert stability_polynomial(ct["euler"]) == [Fraction(1), Fraction(1)]
    assert stability_polynomial(_ovf()) == [Fraction(1), Fraction(1), Fraction(0)]


def test_B24_default_budget_constant():
    assert DEFAULT_BUDGET_CYCLES == 65536


@slow
def test_B24_evaluate_rk4_scorevector(ct, sv_rk4):
    sv = sv_rk4
    assert isinstance(sv, ScoreVector)
    assert set(sv.cycles) == {"m0plus_fast", "m0plus_slow", "avr_approx"}
    assert sv.cycles["m0plus_fast"] == 33
    assert sv.cycles["m0plus_slow"] == 85
    assert isinstance(sv.cycles["avr_approx"], int)
    assert sv.cycles["avr_approx"] > 0
    assert math.isfinite(sv.search_error) and sv.search_error < 0.5
    assert math.isfinite(sv.heldout_error) and sv.heldout_error < 0.5
    assert sv.search_error >= 0.0 and sv.heldout_error >= 0.0
    assert sv.overflow_margin > 1.0
    assert sv.csd_weight_total == 34
    for name in ALL_PROBLEM_NAMES:
        assert name in sv.per_problem
    assert "slow:heldout_error" in sv.per_problem
    assert set(sv.per_problem) == _expected_per_problem_keys()
    again = evaluate(ct["rk4"], 65536)
    assert again == sv


@slow
def test_B24_evaluate_rk4_aggregates_are_rms_of_per_problem(sv_rk4):
    pp = sv_rk4.per_problem
    assert sv_rk4.search_error == pytest.approx(_rms(pp[n] for n in SEARCH_NAMES), rel=1e-9)
    assert sv_rk4.heldout_error == pytest.approx(_rms(pp[n] for n in HELDOUT_NAMES), rel=1e-9)
    for m in ("slow", "avr_approx"):
        assert pp[f"{m}:search_error"] == pytest.approx(_rms(pp[f"{m}:{n}"] for n in SEARCH_NAMES), rel=1e-9)
        assert pp[f"{m}:heldout_error"] == pytest.approx(_rms(pp[f"{m}:{n}"] for n in HELDOUT_NAMES), rel=1e-9)
    for k, v in pp.items():
        assert math.isfinite(v), k
        assert v >= 0.0


@slow
def test_B24_evaluate_rk4_fields_agree_with_standalone_functions(ct, sv_rk4, mo, stab):
    assert sv_rk4.measured_order == mo["rk4"][0]
    assert sv_rk4.order_fit_points == 3
    assert abs(sv_rk4.measured_order - 4.0) <= 0.10
    assert sv_rk4.stability_real == stab["rk4"][0]
    assert sv_rk4.stability_imag == stab["rk4"][1]
    assert sv_rk4.error_constant == error_constant(ct["rk4"])
    assert sv_rk4.coeff_quant_error == pytest.approx(5.086e-06, abs=1e-8)


@slow
def test_B25_euler_worse_than_rk4_on_heldout_set(sv_euler, sv_rk4):
    assert sv_euler.heldout_error > sv_rk4.heldout_error
    assert sv_euler.cycles["m0plus_fast"] == 5
    assert sv_euler.csd_weight_total == 0


@slow
@pytest.mark.parametrize("budget", [0, 1, 10])
def test_B26_evaluate_with_tiny_budget_gives_inf_and_does_not_raise(ct, budget):
    sv = evaluate(ct["rk4"], budget)
    assert isinstance(sv, ScoreVector)
    assert sv.search_error == math.inf
    assert sv.heldout_error == math.inf
    for name in ALL_PROBLEM_NAMES:
        assert sv.per_problem[name] == math.inf
    assert sv.cycles["m0plus_fast"] == 33


def test_B27_error_constant_ordering(ct):
    ec_rk4 = error_constant(ct["rk4"])
    ec_euler = error_constant(ct["euler"])
    assert isinstance(ec_rk4, float)
    assert ec_rk4 > 0.0
    assert ec_euler > ec_rk4


def test_B27_error_constant_rk4_is_l2_norm_of_order5_residuals(ct, fx):
    expected = math.sqrt(sum(float(Fraction(s)) ** 2 for s in fx["_rk4_order5_residuals"]))
    assert error_constant(ct["rk4"]) == pytest.approx(expected, rel=1e-12)
    # euler: achieved order 1, the single order-2 tree has residual -1/2
    assert error_constant(ct["euler"]) == pytest.approx(0.5, rel=1e-12)


@slow
def test_B28_evaluate_overflow_tableau_never_raises(ct):
    sv = evaluate(_ovf(), 65536)
    assert isinstance(sv, ScoreVector)
    assert sv.overflow_margin < 1.0
    assert sv.overflow_margin >= 0.0


# =========================================================================== #
# Verifier
# =========================================================================== #


def test_V8_reject_codes_are_the_nine_handoff_codes():
    assert isinstance(REJECT_CODES, frozenset)
    assert REJECT_CODES == frozenset(
        {
            "NOT_EXPLICIT",
            "ROW_SUM_INCONSISTENT",
            "ORDER_NOT_MET",
            "DYADIC_IMPOSSIBLE",
            "COEFF_UNREPRESENTABLE",
            "Q15_OVERFLOW",
            "UNSTABLE",
            "NAN_OR_INF",
            "NO_ASYMPTOTIC_WINDOW",
        }
    )


@slow
@pytest.mark.parametrize("name", CLASSICAL_NAMES)
def test_V1_all_classical_pass_at_their_order(ct, fx, name):
    assert verify(ct[name], fx[name]["order"]) is None


@slow
def test_V1_verify_with_score_returns_scorevector_on_pass(ct):
    verdict, sv = verify_with_score(ct["rk4"], 4)
    assert verdict is None
    assert isinstance(sv, ScoreVector)
    assert sv.cycles["m0plus_fast"] == 33
    assert sv.overflow_margin > 1.0


def test_V2_perturbed_b_order_not_met(ct):
    res = verify(_rk4_b0_perturbed(ct["rk4"]), 4)
    assert isinstance(res, VerdictReason)
    assert res.code == "ORDER_NOT_MET"
    assert isinstance(res.detail, str)


def test_V2_rk4_claiming_order_5_is_malformed_not_order_not_met(ct):
    # claimed_order must be an int in 1..4; 5 is malformed input, not a check
    res = verify(ct["rk4"], 5)
    assert isinstance(res, VerdictReason)
    assert res.code == "NOT_EXPLICIT"
    assert res.detail.startswith("malformed")


def test_V3_row_sum_inconsistent(ct):
    res = verify(_rk4_bad_c(ct["rk4"]), 4)
    assert isinstance(res, VerdictReason)
    assert res.code == "ROW_SUM_INCONSISTENT"


@pytest.mark.parametrize(
    "index, value",
    [(0, Fraction(1)), (1, Fraction(1, 3)), (2, Fraction(1)), (3, Fraction(0)), (3, Fraction(32767, 32768))],
)
def test_V3_any_row_sum_mismatch_is_row_sum_inconsistent(ct, index, value):
    c = list(ct["rk4"].c)
    c[index] = value
    res = verify(dataclasses.replace(ct["rk4"], c=tuple(c)), 4)
    assert isinstance(res, VerdictReason)
    assert res.code == "ROW_SUM_INCONSISTENT"


def test_V4_not_explicit(ct):
    res = verify(_rk4_not_explicit(ct["rk4"]), 4)
    assert isinstance(res, VerdictReason)
    assert res.code == "NOT_EXPLICIT"


@pytest.mark.parametrize("i, j", [(0, 1), (1, 1), (2, 3), (3, 3), (0, 3)])
def test_V4_any_entry_on_or_above_diagonal_is_not_explicit(ct, i, j):
    A = [list(r) for r in ct["rk4"].A]
    A[i][j] = Fraction(1, 32768)
    c = tuple(sum(r, F0) for r in A)  # keep row sums consistent so only step 1 can fire
    t = dataclasses.replace(ct["rk4"], A=tuple(tuple(r) for r in A), c=c)
    res = verify(t, 4)
    assert isinstance(res, VerdictReason)
    assert res.code == "NOT_EXPLICIT"


def test_V4_not_explicit_checked_before_row_sums(ct):
    # A[0][1] = 1/2 also breaks row 0's sum; step 1 must fire first
    res = verify(_rk4_not_explicit(ct["rk4"]), 4)
    assert res.code == "NOT_EXPLICIT"
    diag = dataclasses.replace(
        ct["heun2"],
        A=((Fraction(1, 2), F0), (F1, F0)),
        c=(Fraction(1, 2), F1),
    )
    assert verify(diag, 2).code == "NOT_EXPLICIT"


@pytest.mark.parametrize(
    "name, claimed",
    [("heun2", 3), ("midpoint", 3), ("heun2", 4), ("midpoint", 4)],
)
def test_V5_all_dyadic_claiming_order_ge_3_is_dyadic_impossible(ct, name, claimed):
    res = verify(ct[name], claimed)
    assert isinstance(res, VerdictReason)
    assert res.code == "DYADIC_IMPOSSIBLE"
    verdict, sv = verify_with_score(ct[name], claimed)
    assert verdict == res
    assert sv is None


def test_V5_dyadic_three_stage_claiming_three_rejected_before_order_check():
    # All-dyadic 3-stage tableau that would also fail order 3; step 3 fires first
    t = make_tableau([[0, 0, 0], ["1/2", 0, 0], [0, "1/2", 0]], ["1/4", "1/4", "1/2"])
    res = verify(t, 3)
    assert res.code == "DYADIC_IMPOSSIBLE"


@pytest.mark.parametrize("claimed", [3, 4])
def test_V5_rk4_matrix_with_dyadic_weights_is_dyadic_impossible(ct, claimed):
    # rk4's A with rk38's (all-dyadic) weights: every entry has a power-of-two denominator
    t = dataclasses.replace(ct["rk4"], b=ct["rk38"].b)
    res = verify(t, claimed)
    assert isinstance(res, VerdictReason)
    assert res.code == "DYADIC_IMPOSSIBLE"
    assert cheap_checks(t, claimed) == res


@slow
def test_V6_heun2_claiming_order_2_passes(ct):
    assert verify(ct["heun2"], 2) is None


@slow
def test_V7_midpoint_claiming_order_2_passes(ct):
    assert verify(ct["midpoint"], 2) is None


def _garbage(rng: random.Random, ct: dict[str, Tableau]):
    kind = rng.randrange(14)
    if kind == 0:
        return None
    if kind == 1:
        return rng.randint(-(10**6), 10**6)
    if kind == 2:
        return "".join(rng.choice("abc/01 ,[]") for _ in range(rng.randint(0, 12)))
    if kind == 3:
        return [rng.random() for _ in range(rng.randint(0, 5))]
    if kind == 4:
        return {"A": [[0]], "b": [1], "c": [0]}
    if kind == 5:
        return rng.choice([float("nan"), float("inf"), -0.0, 1.5])
    if kind == 6:
        # random Fraction tableau, mostly strictly lower triangular
        s = rng.randint(1, 4)
        dens = [1, 2, 3, 4, 6, 8, 16]
        A = tuple(
            tuple(
                Fraction(rng.randint(-8, 8), rng.choice(dens)) if (j < i or rng.random() < 0.1) else F0
                for j in range(s)
            )
            for i in range(s)
        )
        b = tuple(Fraction(rng.randint(-8, 8), rng.choice(dens)) for _ in range(s))
        if rng.random() < 0.7:
            c = tuple(sum(row, F0) for row in A)
        else:
            c = tuple(Fraction(rng.randint(0, 2)) for _ in range(s))
        return Tableau(A, b, c)
    if kind == 7:
        # float entries (never allowed)
        s = rng.randint(1, 3)
        A = tuple(tuple(rng.uniform(-2, 2) if j < i else 0.0 for j in range(s)) for i in range(s))
        return Tableau(A, tuple(rng.uniform(-2, 2) for _ in range(s)), tuple(sum(r) for r in A))
    if kind == 8:
        # NaN / inf entries
        bad = rng.choice([float("nan"), float("inf"), -float("inf")])
        return Tableau(((F0, F0), (Fraction(1, 2), F0)), (bad, F1), (F0, Fraction(1, 2)))
    if kind == 9:
        # length mismatch between A, b, c
        return Tableau(((F0,), (Fraction(1, 2), F0)), (F1,), (F0, Fraction(1, 2), F1))
    if kind == 10:
        # non-square A
        return Tableau(((F0, F0, F0), (Fraction(1, 2), F0, F0)), (Fraction(1, 2), Fraction(1, 2)), (F0, Fraction(1, 2)))
    if kind == 11:
        return Tableau((), (), ())
    if kind == 12:
        # int entries instead of Fractions
        return Tableau(((0, 0), (1, 0)), (1, 0), (0, 1))
    # a classical tableau, sometimes with a huge entry spliced in
    t = ct[rng.choice(CLASSICAL_NAMES)]
    if rng.random() < 0.3:
        b = list(t.b)
        b[0] = Fraction(rng.choice([40000, -50000, 2**40, 1]), 1)
        return dataclasses.replace(t, b=tuple(b))
    return t


def test_V8_random_garbage_never_raises(monkeypatch, ct):
    monkeypatch.setattr(rk_harness.evaluator, "evaluate", lambda t, budget: _healthy_sv())
    rng = random.Random(1)
    n_reject = 0
    n_pass = 0
    for _ in range(10_000):
        t = _garbage(rng, ct)
        claimed = rng.randint(-3, 9)
        try:
            res = verify(t, claimed)
        except Exception as exc:  # noqa: BLE001 - the behavior under test is "never raises"
            pytest.fail(f"verify raised {exc!r} on {t!r} with claimed_order={claimed}")
        if res is None:
            n_pass += 1
        else:
            assert isinstance(res, VerdictReason)
            assert res.code in REJECT_CODES
            assert isinstance(res.detail, str)
            n_reject += 1
    assert n_reject > 0
    assert n_pass > 0


@pytest.mark.parametrize(
    "bad_t, claimed",
    [
        (None, 2),
        (42, 2),
        ("rk4", 4),
        ([[0]], 1),
        ({"A": [[0]], "b": [1], "c": [0]}, 1),
        (Tableau(((0,), ), (1,), (0,)), 1),
        (Tableau(((0.0, 0.0), (1.0, 0.0)), (0.5, 0.5), (0.0, 1.0)), 2),
        (Tableau(((F0, F0), (F1, F0)), (float("nan"), Fraction(1, 2)), (F0, F1)), 2),
        (Tableau(((F0,), (F1, F0)), (F1,), (F0, F1)), 1),
        (Tableau(((F0, F0, F0), (F1, F0, F0)), (Fraction(1, 2), Fraction(1, 2)), (F0, F1)), 2),
        (Tableau((), (), ()), 1),
    ],
)
def test_V8_malformed_tableau_is_not_explicit_malformed(monkeypatch, bad_t, claimed):
    monkeypatch.setattr(rk_harness.evaluator, "evaluate", lambda t, budget: _healthy_sv())
    res = verify(bad_t, claimed)
    assert isinstance(res, VerdictReason)
    assert res.code == "NOT_EXPLICIT"
    assert res.detail.startswith("malformed")


@pytest.mark.parametrize("claimed", [0, -1, 5, 9, "4", 4.0, None])
def test_V8_bad_claimed_order_is_malformed(monkeypatch, ct, claimed):
    monkeypatch.setattr(rk_harness.evaluator, "evaluate", lambda t, budget: _healthy_sv())
    res = verify(ct["rk4"], claimed)
    assert isinstance(res, VerdictReason)
    assert res.code == "NOT_EXPLICIT"
    assert res.detail.startswith("malformed")


def test_V8_malformed_input_never_reaches_evaluate(monkeypatch):
    def boom(t, budget):
        raise AssertionError("evaluate must not be called on malformed input")

    monkeypatch.setattr(rk_harness.evaluator, "evaluate", boom)
    assert verify(None, 2).code == "NOT_EXPLICIT"
    assert verify(Tableau((), (), ()), 1).code == "NOT_EXPLICIT"


@slow
def test_V9_overflow_tableau_rejected_q15_overflow():
    res = verify(_ovf(), 1)
    assert isinstance(res, VerdictReason)
    assert res.code == "Q15_OVERFLOW"


def test_V9_overflow_tableau_passes_cheap_checks():
    assert cheap_checks(_ovf(), 1) is None


@slow
def test_V10_inexact_coefficients_pass_and_are_recorded(ct, sv_heun3, sv_rk4):
    assert verify(ct["heun3"], 3) is None
    assert sv_heun3.coeff_quant_error == pytest.approx(1.017e-05, abs=1e-8)
    assert sv_rk4.coeff_quant_error == pytest.approx(5.086e-06, abs=1e-8)
    assert sv_heun3.coeff_quant_error > 0.0


@pytest.mark.parametrize(
    "maker, claimed",
    [
        (_tab_b_40000, 1),
        (_tab_b_neg_40000, 1),
        (_tab_A_40000, 1),
        (_tab_A_32768, 1),
        (_tab_b_neg_32768, 1),
        (_tab_tiny, 1),
        (_tab_tinier, 1),
        (_tab_tiny_nondyadic, 1),
    ],
)
def test_V10b_out_of_range_coefficient_is_coeff_unrepresentable(maker, claimed):
    res = verify(maker(), claimed)
    assert isinstance(res, VerdictReason)
    assert res.code == "COEFF_UNREPRESENTABLE"
    assert cheap_checks(maker(), claimed) == res


def test_V10b_euler_with_b_40000_is_rejected_before_evaluation(monkeypatch):
    def boom(t, budget):
        raise AssertionError("evaluate must not be called")

    monkeypatch.setattr(rk_harness.evaluator, "evaluate", boom)
    t = Tableau(((F0,),), (Fraction(40000),), (F0,))
    res = verify(t, 1)
    assert isinstance(res, VerdictReason)
    assert res.code in {"COEFF_UNREPRESENTABLE", "ORDER_NOT_MET"}
    assert res.code != "NAN_OR_INF"


@slow
def test_V10c_kutta3_with_coefficient_2_passes(ct):
    assert ct["kutta3"].A[2][1] == 2
    assert verify(ct["kutta3"], 3) is None


def test_V10d_no_asymptotic_window(monkeypatch, ct):
    monkeypatch.setattr(
        rk_harness.evaluator, "evaluate", lambda t, budget: _healthy_sv(measured_order=None, order_fit_points=0)
    )
    res = verify(ct["rk4"], 4)
    assert isinstance(res, VerdictReason)
    assert res.code == "NO_ASYMPTOTIC_WINDOW"


@pytest.mark.parametrize(
    "case, claimed, expected",
    [
        ("heun2", 3, "DYADIC_IMPOSSIBLE"),
        ("midpoint", 3, "DYADIC_IMPOSSIBLE"),
        ("rk4_b0", 4, "ORDER_NOT_MET"),
        ("rk4_bad_c", 4, "ROW_SUM_INCONSISTENT"),
        ("rk4_not_explicit", 4, "NOT_EXPLICIT"),
        ("b_40000", 1, "COEFF_UNREPRESENTABLE"),
        ("tiny", 1, "COEFF_UNREPRESENTABLE"),
    ],
)
def test_V11_cheap_rejections_never_call_evaluate(monkeypatch, ct, case, claimed, expected):
    calls = []

    def boom(t, budget):
        calls.append(t)
        raise AssertionError("evaluate must not be called")

    monkeypatch.setattr(rk_harness.evaluator, "evaluate", boom)
    tabs = {
        "heun2": ct["heun2"],
        "midpoint": ct["midpoint"],
        "rk4_b0": _rk4_b0_perturbed(ct["rk4"]),
        "rk4_bad_c": _rk4_bad_c(ct["rk4"]),
        "rk4_not_explicit": _rk4_not_explicit(ct["rk4"]),
        "b_40000": _tab_b_40000(),
        "tiny": _tab_tiny(),
    }
    t = tabs[case]
    res = verify(t, claimed)
    assert isinstance(res, VerdictReason)
    assert res.code == expected
    assert calls == []
    assert cheap_checks(t, claimed) == res
    verdict, sv = verify_with_score(t, claimed)
    assert verdict == res
    assert sv is None
    assert calls == []


def test_V11_cheap_checks_pass_for_rk4_without_evaluate(monkeypatch, ct):
    def boom(t, budget):
        raise AssertionError("evaluate must not be called by cheap_checks")

    monkeypatch.setattr(rk_harness.evaluator, "evaluate", boom)
    assert cheap_checks(ct["rk4"], 4) is None
    assert cheap_checks(ct["heun2"], 2) is None
    assert cheap_checks(ct["kutta3"], 3) is None


def test_V11_verify_uses_module_attribute_evaluate(monkeypatch, ct):
    seen = []

    def fake(t, budget):
        seen.append((t, budget))
        return _healthy_sv()

    monkeypatch.setattr(rk_harness.evaluator, "evaluate", fake)
    assert verify(ct["rk4"], 4) is None
    assert len(seen) == 1
    assert seen[0][0] == ct["rk4"]
    assert seen[0][1] == DEFAULT_BUDGET_CYCLES


@pytest.mark.parametrize(
    "label, over, expected",
    [
        ("stability_real -0.3", {"stability_real": -0.3}, "UNSTABLE"),
        ("stability_real -0.499", {"stability_real": -0.499}, "UNSTABLE"),
        ("stability_real 0.0", {"stability_real": 0.0}, "UNSTABLE"),
        ("search_error nan", {"search_error": float("nan")}, "NAN_OR_INF"),
        ("heldout_error inf", {"heldout_error": float("inf")}, "NAN_OR_INF"),
        ("error_constant nan", {"error_constant": float("nan")}, "NAN_OR_INF"),
        ("stability_imag inf", {"stability_imag": float("inf")}, "NAN_OR_INF"),
        ("measured_order inf", {"measured_order": float("inf")}, "NAN_OR_INF"),
        ("per_problem inf", {"per_problem": {**_per_problem(), "slow:dahlquist": float("inf")}}, "NAN_OR_INF"),
        ("per_problem nan", {"per_problem": {**_per_problem(), "pendulum": float("nan")}}, "NAN_OR_INF"),
        ("overflow_margin 0.9", {"overflow_margin": 0.9}, "Q15_OVERFLOW"),
        ("overflow_margin 1.0", {"overflow_margin": 1.0}, "Q15_OVERFLOW"),
        ("overflow_margin 0.0", {"overflow_margin": 0.0}, "Q15_OVERFLOW"),
        ("overflow before unstable", {"overflow_margin": 0.9, "stability_real": -0.3}, "Q15_OVERFLOW"),
        ("unstable before no window", {"stability_real": -0.3, "measured_order": None}, "UNSTABLE"),
        ("no window before nan", {"measured_order": None, "search_error": float("nan")}, "NO_ASYMPTOTIC_WINDOW"),
        ("overflow before nan", {"overflow_margin": 0.5, "heldout_error": float("nan")}, "Q15_OVERFLOW"),
    ],
)
def test_B29_evaluator_driven_rejections(monkeypatch, ct, label, over, expected):
    monkeypatch.setattr(rk_harness.evaluator, "evaluate", lambda t, budget: _healthy_sv(**over))
    res = verify(ct["rk4"], 4)
    assert isinstance(res, VerdictReason), label
    assert res.code == expected, label
    assert res.code in REJECT_CODES


@pytest.mark.parametrize(
    "label, over",
    [
        ("stability_real exactly -0.5", {"stability_real": -0.5}),
        ("stability_real -0.51", {"stability_real": -0.51}),
        ("overflow_margin just above 1", {"overflow_margin": 1.0001}),
        ("measured_order 2-point fit", {"measured_order": 3.9, "order_fit_points": 2}),
    ],
)
def test_B29_boundary_values_pass(monkeypatch, ct, label, over):
    sv = _healthy_sv(**over)
    monkeypatch.setattr(rk_harness.evaluator, "evaluate", lambda t, budget: sv)
    assert verify(ct["rk4"], 4) is None, label
    verdict, got = verify_with_score(ct["rk4"], 4)
    assert verdict is None
    assert got == sv


def test_B29_verify_with_score_returns_score_when_rejected_in_steps_6_to_9(monkeypatch, ct):
    sv = _healthy_sv(stability_real=-0.3)
    monkeypatch.setattr(rk_harness.evaluator, "evaluate", lambda t, budget: sv)
    verdict, got = verify_with_score(ct["rk4"], 4)
    assert isinstance(verdict, VerdictReason)
    assert verdict.code == "UNSTABLE"
    assert got == sv


def test_B29_evaluate_exception_becomes_nan_or_inf_internal(monkeypatch, ct):
    def boom(t, budget):
        raise RuntimeError("simulated evaluator failure")

    monkeypatch.setattr(rk_harness.evaluator, "evaluate", boom)
    res = verify(ct["rk4"], 4)
    assert isinstance(res, VerdictReason)
    assert res.code == "NAN_OR_INF"
    assert res.detail.startswith("internal")
    verdict, sv = verify_with_score(ct["rk4"], 4)
    assert verdict == res
    assert sv is None


def test_B29_evaluate_returning_garbage_never_raises(monkeypatch, ct):
    monkeypatch.setattr(rk_harness.evaluator, "evaluate", lambda t, budget: None)
    res = verify(ct["rk4"], 4)
    assert isinstance(res, VerdictReason)
    assert res.code in REJECT_CODES


@slow
def test_B30_verify_is_deterministic(ct):
    first = verify(ct["rk4"], 4)
    second = verify(ct["rk4"], 4)
    assert first is None
    assert second is None
    assert first == second
    assert verify_with_score(ct["rk4"], 4)[0] == first
