"""T10 tests: the library benchmark harness (rk_harness/benchmark.py).

Covers: the tolerance matching rule, champion tableau parsing from a
validation-results document (synthetic, plus the live file when present), the
hand-rolled float64 rk4 against the pinned float solver, exact agreement of
the Q15 accuracy numbers with the pinned solve_q15 machinery, the budget to
step-count rule, schema validity of the built document, timing fields, the
cycles-vs-time correlation, and the banned-words guard on the verdict prose.

The B70 block covers rk_harness/coeffmem.py and the two numbers the
trade-offs matrix needs from this document: coefficient memory per method and
the repeat-to-repeat timing spread per method.

Timings inside the module-scoped document use a reduced repeat count so the
suite stays quick; the accuracy numbers those tests check are the same ones a
full run produces because nothing about accuracy depends on the repeat count.
"""
from __future__ import annotations

import copy
import math
import os
import re
import statistics
import subprocess
import sys
from pathlib import Path

import pytest

from rk_harness import benchmark as B
from rk_harness import coeffmem, coeffrep
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
    # Was `"euler" not in names`: select_benchmark_methods used to keep rk4 as
    # the only classical anchor. Every classical anchor is benchmarked now, so
    # the trade-offs matrix has a measured row for each of them.
    assert "euler" in names
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


def test_B70_selection_keeps_every_classical_anchor():
    """The live validation document carries five classical anchors and three
    discovered methods, and all eight reach the benchmark pool."""
    if not _LIVE_WORK:
        pytest.skip("RK_WORK_DIR not set at import time")
    path = Path(_LIVE_WORK) / "validation" / "results.json"
    if not path.is_file():
        pytest.skip(f"no live validation results at {path}")
    methods = B.select_benchmark_methods(B.load_validation_doc(path))
    names = [m["name_or_hash"] for m in methods]
    for anchor in ("euler", "heun2", "midpoint", "rk4", "rk38"):
        assert anchor in names, anchor
    disc = [m for m in methods if m["kind"] == "discovered"]
    # Not a fixed count: select_discovered returns the champion plus the lowest
    # held-out elite per symbolic order, so this moves when the archive gains an
    # elite at a new order. Assert the property, not today's number.
    assert len(disc) >= 1
    for m in disc:
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
    broken = copy.deepcopy(doc)
    del broken["methods"][0]["coefficient_memory"]
    with pytest.raises(ValueError, match="coefficient_memory"):
        B.validate_results(broken)
    broken = copy.deepcopy(doc)
    for s in broken["speedup"]["per_method_us_per_step"].values():
        del s["median_relative_iqr"]
    with pytest.raises(ValueError, match="median_relative_iqr"):
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
    sp = doc["speedup"]
    prose = [v["matched_tolerance"], v["fixed_step"], v["cycle_model"],
             v["overall"], sp["regime"], sp["caveat"], sp["prose"],
             *doc["caveats"], *doc["schema"].values(),
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


# ------------------------------------------------------------------ speedup


@pytest.mark.slow
def test_speedup_section_present(doc):
    """The measured head-to-head exists: champion vs rk4, one row per frozen
    problem, per-step seconds and microseconds for both sides, budget wall
    clock, cited errors, and per-method absolute timing summaries."""
    sp = doc["speedup"]
    assert sp["baseline"] == "rk4"
    methods = {m["name_or_hash"]: m for m in doc["methods"]}
    champ = methods[sp["champion"]]
    assert champ["kind"] == "discovered"
    assert "champion" in champ["roles"]
    assert [r["problem"] for r in sp["rows"]] == list(B.PROBLEM_NAMES)
    for r in sp["rows"]:
        assert r["champion_cycles_per_step"] < r["rk4_cycles_per_step"]
        assert r["champion_n_steps"] > r["rk4_n_steps"]
        if r["status"] == "ok":
            for k in ("champion_per_step_median_s", "rk4_per_step_median_s",
                      "champion_us_per_step", "rk4_us_per_step",
                      "champion_budget_seconds", "rk4_budget_seconds",
                      "measured_ratio_rk4_over_champion",
                      "champion_error", "rk4_error", "champion_error_lower"):
                assert k in r, (r["problem"], k)
    # the like-vs-like caveat names the shared solve path
    assert "solve_q15" in sp["caveat"] and "solve_q15" in sp["regime"]
    pm = sp["per_method_us_per_step"]
    assert sp["champion"] in pm and "rk4" in pm
    for s in pm.values():
        assert s["median_us_per_step"] > 0
        assert s["n_problems"] == len(s["per_problem_us_per_step"])
        assert s["min_us_per_step"] <= s["median_us_per_step"] <= s["max_us_per_step"]


@pytest.mark.slow
def test_speedup_rows_consistent_with_timing_rows(doc):
    """Every speedup number traces to the fixed-step table: per-step seconds,
    budget seconds and errors are cited bit for bit, the measured ratio is the
    quotient of the two per-step medians, and the microsecond renderings are
    the seconds scaled by 1e6."""
    sp = doc["speedup"]
    cells = {(r["method"], r["problem"]): r for r in doc["fixed_step_results"]}
    checked = 0
    for row in sp["rows"]:
        if row["status"] != "ok":
            assert "reason" in row
            continue
        c = cells[(sp["champion"], row["problem"])]["q15"]
        k = cells[("rk4", row["problem"])]["q15"]
        assert row["champion_per_step_median_s"] == c["per_step_median_s"]
        assert row["rk4_per_step_median_s"] == k["per_step_median_s"]
        assert row["champion_budget_seconds"] == c["timing"]["median_s"]
        assert row["rk4_budget_seconds"] == k["timing"]["median_s"]
        assert row["champion_error"] == c["error"]
        assert row["rk4_error"] == k["error"]
        assert row["champion_error_lower"] == (c["error"] < k["error"])
        assert math.isclose(row["measured_ratio_rk4_over_champion"],
                            k["per_step_median_s"] / c["per_step_median_s"],
                            rel_tol=1e-9)
        assert math.isclose(row["champion_us_per_step"],
                            c["per_step_median_s"] * 1e6, abs_tol=5e-4)
        assert math.isclose(row["rk4_us_per_step"],
                            k["per_step_median_s"] * 1e6, abs_tol=5e-4)
        checked += 1
    assert checked >= 1


@pytest.mark.slow
def test_speedup_predicted_ratio_matches_costmodel(doc):
    """The predicted column is exactly the m0plus_fast cycle-count quotient of
    the two tableaus, recomputed here from the cost model itself."""
    from rk_harness.costmodel import M0PLUS_FAST, cycle_count
    sp = doc["speedup"]
    methods = {m["name_or_hash"]: m for m in doc["methods"]}
    champ_t = make_tableau(**methods[sp["champion"]]["tableau"])
    rk4_t = make_tableau(**methods["rk4"]["tableau"])
    for row in sp["rows"]:
        p = PROBLEMS[row["problem"]]
        cc = cycle_count(champ_t, M0PLUS_FAST, p.n_states)
        kc = cycle_count(rk4_t, M0PLUS_FAST, p.n_states)
        assert row["champion_cycles_per_step"] == cc
        assert row["rk4_cycles_per_step"] == kc
        assert math.isclose(row["predicted_ratio_rk4_over_champion"], kc / cc,
                            rel_tol=1e-12)


@pytest.mark.slow
def test_speedup_geomean_and_schema_guard(doc):
    """The geometric means equal exp(mean(log ratio)) recomputed from the ok
    rows, the error-lower count matches, and validate_results rejects a
    tampered or missing speedup section."""
    sp = doc["speedup"]
    ratios = [r["measured_ratio_rk4_over_champion"] for r in sp["rows"]
              if r["status"] == "ok"]
    preds = [r["predicted_ratio_rk4_over_champion"] for r in sp["rows"]
             if r["status"] == "ok"]
    assert sp["n_problems_compared"] == len(ratios)
    if ratios:
        expect = math.exp(sum(math.log(v) for v in ratios) / len(ratios))
        assert math.isclose(sp["geomean_measured_speedup_rk4_over_champion"],
                            expect, rel_tol=1e-9)
        expect = math.exp(sum(math.log(v) for v in preds) / len(preds))
        assert math.isclose(sp["geomean_predicted_speedup_rk4_over_champion"],
                            expect, rel_tol=1e-9)
    lower = sum(1 for r in sp["rows"] if r.get("champion_error_lower"))
    assert sp["champion_error_lower_count"] == lower

    broken = copy.deepcopy(doc)
    broken["speedup"]["rows"][0]["predicted_ratio_rk4_over_champion"] *= 2.0
    with pytest.raises(ValueError, match="predicted ratio"):
        B.validate_results(broken)
    broken = copy.deepcopy(doc)
    broken["speedup"]["geomean_measured_speedup_rk4_over_champion"] = 99.0
    with pytest.raises(ValueError, match="geomean_measured"):
        B.validate_results(broken)
    broken = copy.deepcopy(doc)
    del broken["speedup"]
    with pytest.raises(ValueError, match="speedup"):
        B.validate_results(broken)


# -------------------------------------------------- B70 coefficient memory


def test_B70_coefficient_memory_counts_only_non_trivial_A_and_b_entries():
    """Fixed vectors from the classical set. Entries are A and b with c
    excluded, and 0, 1 and -1 cost nothing."""
    cls = classical()
    assert coeffmem.coefficient_memory(cls["rk4"]) == {
        "words": 6, "bytes": 12, "max_shift": 17, "entries": 20, "trivial": 14}
    assert coeffmem.coefficient_memory(cls["rk38"]) == {
        "words": 6, "bytes": 12, "max_shift": 16, "entries": 20, "trivial": 14}
    assert coeffmem.coefficient_memory(cls["heun2"])["words"] == 2
    assert coeffmem.coefficient_memory(cls["midpoint"])["words"] == 1
    # c excluded: rk4's c is (0, 1/2, 1/2, 1) and two of those are non-trivial,
    # so counting c would push entries to 24 and words to 8.
    assert coeffmem.coefficient_memory(cls["rk4"])["entries"] == (
        len(cls["rk4"].A) ** 2 + len(cls["rk4"].b))
    # heun2's b is (1/2, 1/2) and its one non-trivial A entry is 1, so the two
    # counted words are exactly the two b entries.
    assert coeffmem.coefficient_memory(cls["heun2"])["trivial"] == 4


def test_B70_coefficient_memory_of_euler_is_zero_words():
    """euler is A = [[0]], b = [1]: nothing to store. The empty case is the
    one a naive max() over the counted shifts would crash on."""
    cm = coeffmem.coefficient_memory(classical()["euler"])
    assert cm == {"words": 0, "bytes": 0, "max_shift": 0,
                  "entries": 2, "trivial": 2}


def test_B70_coefficient_memory_max_shift_matches_to_rep():
    cls = classical()
    for name, expect in (("rk4", 17), ("rk38", 16)):
        t = cls[name]
        shifts = [coeffrep.to_rep(x).s for x in coeffrep._ab_entries(t)
                  if not coeffrep.is_trivial(x)]
        assert coeffmem.coefficient_memory(t)["max_shift"] == max(shifts)
        assert coeffmem.coefficient_memory(t)["max_shift"] == expect


_CLASSICAL_NAMES = tuple(sorted(classical()))


@pytest.mark.parametrize("name", _CLASSICAL_NAMES)
def test_B70_coefficient_memory_word_count_never_exceeds_entries(name):
    t = classical()[name]
    cm = coeffmem.coefficient_memory(t)
    assert 0 <= cm["words"] <= cm["entries"]
    assert cm["bytes"] == 2 * cm["words"]
    assert cm["trivial"] == cm["entries"] - cm["words"]
    assert cm["entries"] == len(t.A) ** 2 + len(t.b)


def test_B70_coeffmem_imports_without_numpy():
    """The reason coeffmem is its own module: importing benchmark sets five
    BLAS thread variables and pulls in numpy, and the overview generator has
    no business inheriting either just to print a column."""
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; import rk_harness.coeffmem; "
         "print('numpy' in sys.modules, 'scipy' in sys.modules)"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(Path(__file__).resolve().parents[1]))
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False False", out.stdout


@pytest.mark.slow
def test_B70_method_entries_carry_coefficient_memory(doc):
    """Every method entry carries the block, and it is what coeffmem returns
    for the tableau parsed back out of that entry."""
    assert doc["methods"]
    for m in doc["methods"]:
        cm = m["coefficient_memory"]
        t = make_tableau(**m["tableau"])
        assert cm == coeffmem.coefficient_memory(t)
        assert cm["bytes"] == 2 * cm["words"]
        assert cm["words"] <= cm["entries"]
    by_name = {m["name_or_hash"]: m["coefficient_memory"] for m in doc["methods"]}
    assert by_name["euler"]["words"] == 0
    assert by_name["rk4"]["words"] == 6


@pytest.mark.slow
def test_B70_per_method_relative_iqr_present_and_bounded(doc):
    """median_relative_iqr is the median of iqr_s / median_s over the same
    finished Q15 rows the microsecond figures come from."""
    pm = doc["speedup"]["per_method_us_per_step"]
    assert pm
    for name, s in sorted(pm.items()):
        riqr = s["median_relative_iqr"]
        assert isinstance(riqr, (int, float)) and math.isfinite(riqr)
        assert riqr >= 0.0
        expect = [r["q15"]["timing"]["iqr_s"] / r["q15"]["timing"]["median_s"]
                  for r in doc["fixed_step_results"]
                  if r["method"] == name and r["q15"].get("status") == "ok"]
        assert len(expect) == s["n_problems"]
        assert math.isclose(riqr, statistics.median(expect), rel_tol=1e-12)


# -------------------------------------------------------------------- write


@pytest.mark.slow
def test_write_results_round_trips(doc, tmp_path):
    import json
    out = B.write_results(doc, tmp_path / "results.json")
    with open(out, "r", encoding="utf-8") as fh:
        loaded = json.load(fh)
    B.validate_results(loaded)
    assert loaded["budget_cycles"] == B.BUDGET_CYCLES
