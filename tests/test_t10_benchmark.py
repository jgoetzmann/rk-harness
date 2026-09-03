"""T10 tests: the library benchmark harness (rk_harness/benchmark.py).

Covers: the tolerance matching rule, champion tableau parsing from a
validation-results document (synthetic, plus the live file when present), the
hand-rolled float64 rk4 against the pinned float solver, exact agreement of
the Q15 accuracy numbers with the pinned solve_q15 machinery, the budget to
step-count rule, schema validity of the built document, timing fields, the
cycles-vs-time correlation, and the banned-words guard on the verdict prose.

Timings inside the module-scoped document use a reduced repeat count so the
suite stays quick; the accuracy numbers those tests check are the same ones a
full run produces because nothing about accuracy depends on the repeat count.
"""
from __future__ import annotations

import copy
import math
import os
import re
from pathlib import Path

import pytest

from rk_harness import benchmark as B
from rk_harness.simulate import problem_error, solve_float, steps_for_budget
from rk_harness.tableau import classical, content_hash, make_tableau, to_json
from rk_harness.problems import PROBLEMS

# Captured at import time, before the autouse conftest fixture redirects
# RK_WORK_DIR to a throwaway directory.
_LIVE_WORK = os.environ.get("RK_WORK_DIR")

_N_REPEATS = 2
_N_WARMUP = 1

# The champion tableau as recorded in rk-work/validation/results.json (also a
# fixed test vector: 3 stages, order 2, all-dyadic, b = e_3).
_CHAMPION_TABLEAU = {
    "A": [["0/1", "0/1", "0/1"],
          ["13/16", "0/1", "0/1"],
          ["5/32", "11/32", "0/1"]],
    "b": ["0/1", "0/1", "1/1"],
    "c": ["0/1", "13/16", "1/2"],
}


def _synthetic_validation_doc() -> dict:
    cls = classical()
    champ = make_tableau(_CHAMPION_TABLEAU["A"], _CHAMPION_TABLEAU["b"],
                         _CHAMPION_TABLEAU["c"])
    return {
        "methods": [
            {"name_or_hash": "euler", "kind": "classical", "roles": ["anchor"],
             "order": 1, "stages": 1, "tableau": to_json(cls["euler"])},
            {"name_or_hash": "rk4", "kind": "classical", "roles": ["anchor"],
             "order": 4, "stages": 4, "tableau": to_json(cls["rk4"])},
            {"name_or_hash": content_hash(champ), "kind": "discovered",
             "roles": ["champion"], "order": 2, "stages": 3,
             "tableau": to_json(champ),
             "archive": {"cycle_id": 33, "tier": "unreplicated"}},
        ],
    }


@pytest.fixture(scope="module")
def doc():
    return B.build_results(validation_doc=_synthetic_validation_doc(),
                           n_repeats=_N_REPEATS, warmup=_N_WARMUP)


# ------------------------------------------------------------ tolerance rule


def test_tolerance_matching_rule():
    for name, p in PROBLEMS.items():
        rtol, atol = B.tolerances(name)
        assert rtol == 2.0 ** -15
        assert atol == 2.0 ** -15 / p.scale
    # spelled out for the two extreme scales in the frozen set
    assert B.tolerances("dahlquist")[1] == 2.0 ** -15 / PROBLEMS["dahlquist"].scale
    assert "2**-15" in B.TOLERANCE_RULE


# ---------------------------------------------------------- champion parsing


def test_champion_parsing_synthetic():
    methods = B.select_benchmark_methods(_synthetic_validation_doc())
    names = [m["name_or_hash"] for m in methods]
    assert "rk4" in names
    assert "euler" not in names                      # only rk4 among classicals
    disc = [m for m in methods if m["kind"] == "discovered"]
    assert len(disc) == 1
    assert content_hash(disc[0]["tableau"]) == disc[0]["name_or_hash"]
    assert disc[0]["stages"] == 3 and disc[0]["order"] == 2
    assert "archive" in disc[0]


def test_champion_parsing_rejects_bad_hash():
    bad = _synthetic_validation_doc()
    for m in bad["methods"]:
        if m["kind"] == "discovered":
            m["name_or_hash"] = "0" * 64
    with pytest.raises(ValueError, match="hashes to"):
        B.select_benchmark_methods(bad)


def test_champion_parsing_live_file():
    if not _LIVE_WORK:
        pytest.skip("RK_WORK_DIR not set at import time")
    path = Path(_LIVE_WORK) / "validation" / "results.json"
    if not path.is_file():
        pytest.skip(f"no live validation results at {path}")
    methods = B.select_benchmark_methods(B.load_validation_doc(path))
    names = [m["name_or_hash"] for m in methods]
    assert "rk4" in names
    assert any(m["kind"] == "discovered" for m in methods)
    for m in methods:  # select_benchmark_methods re-verified every hash
        if m["kind"] == "discovered":
            assert content_hash(m["tableau"]) == m["name_or_hash"]


# ------------------------------------------------------- float64 rk4 correct


def test_hand_rolled_rk4_matches_pinned_float_solver():
    t = classical()["rk4"]
    for name, n in (("dahlquist", 100), ("damped_osc", 128)):
        p = PROBLEMS[name]
        y0 = B.physical_y0(name)
        from rk_harness.problems import FLOAT_RHS
        mine = B.solve_rk4_float(FLOAT_RHS[name], y0, p.t_end, n)
        pinned = solve_float(t, FLOAT_RHS[name], y0, p.t_end, n)
        for a, b in zip(mine, pinned):
            assert math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-14)


# --------------------------------------------------- accuracy reproducibility


@pytest.mark.slow
def test_q15_accuracy_matches_pinned_machinery(doc):
    """The q15_error in a fixed-step row is exactly problem_error of the pinned
    machinery at the budgeted step count, bit for bit."""
    methods = {m["name_or_hash"]: m for m in doc["methods"]}
    checked = 0
    for row in doc["fixed_step_results"]:
        if row["q15"].get("status") != "ok" or checked >= 4:
            continue
        t = make_tableau(**methods[row["method"]]["tableau"])
        err, max_q = problem_error(t, PROBLEMS[row["problem"]], row["n_steps"])
        assert row["q15"]["error"] == err
        assert row["q15"]["max_abs_q"] == max_q
        checked += 1
    assert checked >= 2


@pytest.mark.slow
def test_adaptive_accuracy_reproducible(doc):
    """Re-running an adaptive cell reproduces the recorded error exactly
    (deterministic scipy, deterministic reference)."""
    rows = [r for r in doc["adaptive_results"] if r.get("status") == "ok"]
    assert rows, "no ok adaptive rows (scipy missing?)"
    row = rows[0]
    again = B.adaptive_row(row["integrator"], row["problem"],
                           n_repeats=_N_REPEATS, warmup=0)
    assert again["status"] == "ok"
    assert again["error"] == row["error"]
    assert again["n_steps_accepted"] == row["n_steps_accepted"]
    assert again["nfev"] == row["nfev"]


# ------------------------------------------------------------ budget mapping


@pytest.mark.slow
def test_steps_follow_cycle_budget(doc):
    methods = {m["name_or_hash"]: m for m in doc["methods"]}
    for row in doc["fixed_step_results"]:
        m = methods[row["method"]]
        t = make_tableau(**m["tableau"])
        p = PROBLEMS[row["problem"]]
        assert row["n_steps"] == steps_for_budget(t, B.COST_MODEL, p.n_states,
                                                  B.BUDGET_CYCLES)
        assert row["n_steps"] == B.BUDGET_CYCLES // row["cycles_per_step"]
        assert row["total_cycles"] <= B.BUDGET_CYCLES
        assert m["steps"][row["problem"]] == row["n_steps"]


# ------------------------------------------------------------------- schema


@pytest.mark.slow
def test_schema_validates_and_rejects_violations(doc):
    B.validate_results(doc)
    broken = copy.deepcopy(doc)
    broken["problems"][0]["rtol"] = 1e-6           # breaks the matching rule
    with pytest.raises(ValueError, match="tolerance matching rule"):
        B.validate_results(broken)
    broken = copy.deepcopy(doc)
    del broken["correlation"]
    with pytest.raises(ValueError, match="correlation"):
        B.validate_results(broken)


@pytest.mark.slow
def test_timing_fields_present_and_positive(doc):
    assert doc["timing_protocol"]["clock"] == "time.perf_counter"
    seen = 0
    for row in doc["adaptive_results"]:
        if row.get("status") == "ok":
            tm = row["timing"]
            assert tm["median_s"] > 0 and tm["iqr_s"] >= 0
            assert tm["min_s"] > 0 and tm["n"] == _N_REPEATS
            assert row["per_step_median_s"] > 0
            seen += 1
    for row in doc["fixed_step_results"]:
        for side in ("q15", "float_rk4"):
            cell = row[side]
            if cell.get("status") == "ok":
                tm = cell["timing"]
                assert tm["median_s"] > 0 and tm["iqr_s"] >= 0
                assert cell["per_step_median_s"] > 0
                seen += 1
    assert seen > 0


def test_default_protocol_meets_spec():
    """The real run must time at least 15 repeats after a warmup."""
    assert B.N_REPEATS >= 15
    assert B.N_WARMUP >= 1


# -------------------------------------------------------------- correlation


@pytest.mark.slow
def test_correlation_reported_and_bounded(doc):
    corr = doc["correlation"]
    ok_q15 = sum(1 for r in doc["fixed_step_results"]
                 if r["q15"].get("status") == "ok")
    assert corr["n_points"] == ok_q15
    if corr["pearson_r"] is not None:
        assert -1.0 <= corr["pearson_r"] <= 1.0
        assert corr["median_s_per_cycle"] > 0


# ----------------------------------------------------------------- verdicts


@pytest.mark.slow
def test_verdicts_avoid_banned_words_and_em_dashes(doc):
    banned = re.compile(
        r"(?<![a-z0-9-])(novel|first|beats|outperforms|breakthrough|proves|"
        r"state-of-the-art|best-ever)(?![a-z0-9-])")
    v = doc["verdicts"]
    prose = [v["matched_tolerance"], v["fixed_step"], v["cycle_model"],
             v["overall"], *doc["caveats"], *doc["schema"].values(),
             doc["tolerance_rule"]]
    for text in prose:
        assert not banned.search(str(text).lower()), text
        assert "—" not in str(text)


@pytest.mark.slow
def test_environment_recorded(doc):
    env = doc["environment"]
    for k in ("python", "cpu", "os", "numpy", "perf_counter_resolution_s",
              "thread_env", "timing_caveat"):
        assert k in env
    assert env["python"].count(".") == 2
    assert isinstance(env["timing_caveat"], str) and env["timing_caveat"]


# -------------------------------------------------------------------- write


@pytest.mark.slow
def test_write_results_round_trips(doc, tmp_path):
    import json
    out = B.write_results(doc, tmp_path / "results.json")
    with open(out, "r", encoding="utf-8") as fh:
        loaded = json.load(fh)
    B.validate_results(loaded)
    assert loaded["budget_cycles"] == B.BUDGET_CYCLES
