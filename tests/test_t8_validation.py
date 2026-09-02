"""T8 tests: the practical validation suite (rk_harness/validation.py).

Covers: the five practical problems stay inside Q15 bounds over their windows,
reference solutions agree with an independent fine-step float integration,
peaks match the fixture-convention measurement, the results document schema
validates, one anchor evaluation is bit-for-bit reproducible, classical anchor
identification is correct, and discovered-method selection follows the archive
grid rule (synthetic archive, no dependence on the live one).
"""
from __future__ import annotations

import math
import re

import pytest

from rk_harness import problems as frozen_problems
from rk_harness import validation as V
from rk_harness.costmodel import M0PLUS_FAST, cycle_count
from rk_harness.simulate import solve_float, solve_q15, steps_for_budget
from rk_harness.tableau import classical, content_hash, make_tableau
from rk_harness.types import Record, ScoreVector


# --------------------------------------------------------------------------- helpers

def _score(heldout: float, search: float = 0.5) -> ScoreVector:
    return ScoreVector(
        measured_order=None, order_fit_points=0, error_constant=0.0,
        stability_real=-1.0, stability_imag=1.0, cycles={"m0plus_fast": 20},
        csd_weight_total=0, coeff_quant_error=0.0, search_error=search,
        heldout_error=heldout, overflow_margin=1.5, per_problem={},
    )


def _rec(t, heldout: float, cycle_id: int = 1) -> Record:
    return Record(
        tableau_hash=content_hash(t), tableau=t, score=_score(heldout),
        tier="unreplicated", cycle_id=cycle_id, seed=0, verifier_hash="testhash",
        directive_id=None, hypothesis_id=None, timestamp="2026-09-02T00:00:00Z",
    )


# Discovered (non-fixture) order-2 tableaus: c2 = 3/4 -> b = (1/3, 2/3);
# c2 = 1/4 -> b = (-1, 2). Both satisfy the order-2 conditions and fail order 3.
_T_A = make_tableau([["0", "0"], ["3/4", "0"]], ["1/3", "2/3"])
_T_B = make_tableau([["0", "0"], ["1/4", "0"]], ["-1", "2"])
# Discovered order-1 tableau: b sums to 1 but b.c = 1/6 != 1/2.
_T_C = make_tableau([["0", "0"], ["1/3", "0"]], ["1/2", "1/2"])


@pytest.fixture(scope="module")
def cls():
    return classical()


@pytest.fixture(scope="module")
def euler_cells(cls):
    return {name: V.evaluate_pair(cls["euler"], name) for name in V.VALIDATION_NAMES}


@pytest.fixture(scope="module")
def rk4_cells(cls):
    return {name: V.evaluate_pair(cls["rk4"], name) for name in V.VALIDATION_NAMES}


@pytest.fixture(scope="module")
def synthetic_doc(cls):
    records = [_rec(cls["euler"], 0.001, cycle_id=0), _rec(_T_A, 0.03), _rec(_T_B, 0.02)]
    doc = V.build_results(records=records, champ=content_hash(_T_A))
    return doc


# --------------------------------------------------------------------------- registry hygiene

def test_names_disjoint_from_frozen():
    assert not set(V.VALIDATION_NAMES) & set(frozen_problems.PROBLEMS)
    # crucial for solve_q15: the frozen DERIV_SCALE lookup must fall through
    # to its 1.0 default for every validation problem
    assert not set(V.VALIDATION_NAMES) & set(frozen_problems.DERIV_SCALE)


def test_deriv_scale_map():
    assert set(V.DERIV_SCALE) == set(V.VALIDATION_NAMES)
    assert all(v == 1.0 for v in V.DERIV_SCALE.values())


def test_scales_power_of_two_and_y0_roundtrip():
    for name in V.VALIDATION_NAMES:
        p = V.PROBLEMS[name]
        assert math.log2(p.scale).is_integer(), name
        assert p.y0 == frozen_problems.to_q15_state(V.Y0_PHYS[name], p.scale)
        assert p.n_states == len(V.Y0_PHYS[name])
        assert p.f is not None


# --------------------------------------------------------------------------- Q15 bounds

# Upper bounds on max |q| from the research spec (verified over euler/heun2/
# rk4/rk38 under both primary models); euler and rk4 under fast must stay <=.
_MAXQ_BOUND = {
    "buck_converter": 4872,
    "battery_2rc": 20086,
    "bicycle_lateral": 9170,
    "pll_lock": 8332,
    "glucose_minimal": 11430,
}


@pytest.mark.parametrize("name", V.VALIDATION_NAMES)
def test_q15_bounds_euler(euler_cells, name):
    cell = euler_cells[name]
    assert cell["q15_error"] is not None, cell.get("note")
    assert 0 < cell["max_abs_q"] <= _MAXQ_BOUND[name]


@pytest.mark.parametrize("name", V.VALIDATION_NAMES)
def test_q15_bounds_rk4(rk4_cells, name):
    cell = rk4_cells[name]
    assert cell["q15_error"] is not None, cell.get("note")
    assert 0 < cell["max_abs_q"] <= _MAXQ_BOUND[name]
    # comfortably inside int16: at least 25 percent headroom to overflow
    assert cell["max_abs_q"] < 0.75 * 32768


# --------------------------------------------------------------------------- references

@pytest.mark.parametrize("name", V.VALIDATION_NAMES)
def test_reference_matches_fine_float_integration(cls, name):
    """reference(t_end) vs an independent float64 RK4 run at 4000 steps: the
    normalized L2 gap must be far below every Q15 error we report."""
    p = V.PROBLEMS[name]
    y = solve_float(cls["rk4"], V.FLOAT_RHS[name], V.Y0_PHYS[name], p.t_end, 4000)
    ref = p.reference(p.t_end)
    gap = math.sqrt(sum((a - b) ** 2 for a, b in zip(y, ref))) / V.PEAK[name]
    assert gap < 1e-9, (name, gap)


@pytest.mark.parametrize("name", V.VALIDATION_NAMES)
def test_peaks_match_measurement(name):
    measured = V.measure_peaks(name, n=4000)
    stored = V.PER_STATE_PEAKS[name]
    assert len(measured) == len(stored) == V.PROBLEMS[name].n_states
    for m, s in zip(measured, stored):
        assert abs(m - s) <= 1e-3 * max(s, 1e-12), (name, measured, stored)
    assert V.PEAK[name] == max(stored)


def test_glucose_rhs_is_time_dependent():
    p = V.PROBLEMS["glucose_minimal"]
    d0 = p.f(0.0, p.y0)
    d6 = p.f(6.0, p.y0)
    assert d0 != d6  # the exp(-t/2) forcing must reach the Q15 rhs
    assert d0[1] > d6[1] > 0


# --------------------------------------------------------------------------- evaluation

def test_reproducible_anchor_eval(cls):
    a = V.evaluate_pair(cls["heun2"], "buck_converter")
    b = V.evaluate_pair(cls["heun2"], "buck_converter")
    assert a == b
    assert a["q15_error"] is not None and a["float_error"] is not None


def test_euler_buck_error_in_expected_band(euler_cells):
    # research spec: relerr 2.5e-2 for euler on buck_converter at this budget
    assert 0.015 < euler_cells["buck_converter"]["q15_error"] < 0.04


@pytest.mark.parametrize("name", V.VALIDATION_NAMES)
def test_float_error_below_q15(rk4_cells, name):
    """Quantization cost must be visible: float64 rk4 at the same step count is
    orders of magnitude more accurate than the Q15 run."""
    cell = rk4_cells[name]
    assert cell["float_error"] < cell["q15_error"] / 100.0


def test_steps_match_fixture_convention(cls):
    # fixtures/classical.json: euler 5 cycles/state, rk4 33 cycles/state (fast)
    assert steps_for_budget(cls["euler"], M0PLUS_FAST, 2, V.BUDGET_CYCLES) == 65536 // 10
    assert steps_for_budget(cls["euler"], M0PLUS_FAST, 3, V.BUDGET_CYCLES) == 65536 // 15
    assert steps_for_budget(cls["rk4"], M0PLUS_FAST, 2, V.BUDGET_CYCLES) == 65536 // 66
    assert cycle_count(cls["rk4"], M0PLUS_FAST, 1) == 33


# --------------------------------------------------------------------------- anchors and selection

def test_classical_anchor_identification(cls):
    hashes = dict(V.classical_hashes())
    assert len(hashes) == 8 and len(set(hashes.values())) == 8
    assert set(V.CLASSICAL_ANCHOR_NAMES) <= set(hashes)
    for name in V.CLASSICAL_ANCHOR_NAMES:
        assert hashes[name] == content_hash(cls[name])
    orders = {name: V.method_order(cls[name]) for name in V.CLASSICAL_ANCHOR_NAMES}
    assert orders == {"euler": 1, "heun2": 2, "midpoint": 2, "rk4": 4, "rk38": 4}


def test_select_discovered_synthetic(cls):
    records = [
        _rec(cls["euler"], 0.001, cycle_id=0),   # classical seed: must be excluded
        _rec(_T_C, 0.5),                          # discovered, order 1
        _rec(_T_A, 0.03),                         # discovered, order 2
        _rec(_T_B, 0.02),                         # discovered, order 2, better
    ]
    picked = V.select_discovered(records, content_hash(_T_A))
    roles = {r.tableau_hash: rs for r, rs in picked}
    assert roles[content_hash(_T_A)] == ["champion"]
    assert roles[content_hash(_T_C)] == ["best_elite_order_1"]
    assert roles[content_hash(_T_B)] == ["best_elite_order_2"]
    # euler is order 1 but classical, so it must not displace the discovered pick
    assert content_hash(cls["euler"]) not in roles

    # champion that is also the per-order best carries both roles
    picked2 = V.select_discovered(records, content_hash(_T_B))
    roles2 = {r.tableau_hash: rs for r, rs in picked2}
    assert roles2[content_hash(_T_B)] == ["champion", "best_elite_order_2"]

    # per-order ties keep the earliest record (the archive grid rule)
    tie = [_rec(_T_A, 0.02), _rec(_T_B, 0.02)]
    picked3 = V.select_discovered(tie, content_hash(_T_A))
    roles3 = {r.tableau_hash: rs for r, rs in picked3}
    assert roles3[content_hash(_T_A)] == ["champion", "best_elite_order_2"]

    with pytest.raises(ValueError):
        V.select_discovered(records, "no-such-hash")


# --------------------------------------------------------------------------- results document

def test_results_schema_validates(synthetic_doc):
    V.validate_results(synthetic_doc)
    doc = synthetic_doc
    assert doc["budget_cycles"] == 65536
    assert doc["cost_model"] == "m0plus_fast"
    assert doc["generated_from"]["archive_records"] == 3
    kinds = {m["name_or_hash"]: m["kind"] for m in doc["methods"]}
    assert sum(1 for k in kinds.values() if k == "classical") == 5
    assert sum(1 for k in kinds.values() if k == "discovered") == 2
    assert len(doc["results"]) == 7 * 5


def test_results_schema_rejects_bad_docs(synthetic_doc):
    import copy
    bad = copy.deepcopy(synthetic_doc)
    del bad["verdicts"]
    with pytest.raises(ValueError):
        V.validate_results(bad)
    bad2 = copy.deepcopy(synthetic_doc)
    bad2["results"][0]["q15_error"] = float("inf")
    with pytest.raises(ValueError):
        V.validate_results(bad2)
    bad3 = copy.deepcopy(synthetic_doc)
    bad3["results"].pop()
    with pytest.raises(ValueError):
        V.validate_results(bad3)


_BANNED = re.compile(
    r"\b(novel|first|beats|outperforms|breakthrough|proves|state-of-the-art|best-ever)\b",
    re.IGNORECASE,
)


def test_verdicts_consistent_and_site_safe(synthetic_doc):
    v = synthetic_doc["verdicts"]
    assert set(v["per_problem"]) == set(V.VALIDATION_NAMES)
    for name, entry in v["per_problem"].items():
        assert entry["winner"] is not None, name
        bc, bd = entry["best_classical_q15_error"], entry["best_discovered_q15_error"]
        ratio = entry["ratio_discovered_over_classical"]
        assert math.isclose(ratio, bd / bc, rel_tol=1e-12)
        assert entry["winner_q15_error"] <= min(bc, bd)
    assert isinstance(v["overall"], str) and v["overall"]
    # this document feeds the findings site: banned words and em dashes stay out
    for text in [v["overall"]] + [p["source"] for p in synthetic_doc["problems"]]:
        assert not _BANNED.search(text), text
        assert "—" not in text


def test_write_results_deterministic(tmp_path, synthetic_doc):
    p1 = V.write_results(synthetic_doc, tmp_path / "a.json")
    p2 = V.write_results(synthetic_doc, tmp_path / "b.json")
    b1, b2 = p1.read_bytes(), p2.read_bytes()
    assert b1 == b2
    assert b"\r\n" not in b1  # byte-deterministic LF output
