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

The B4 block covers rk_harness/benchcounts.py and the three-class tables:
matched accuracy as the shared condition with a side on every row, our
embedded pair against SciPy RK23 and RK45 at matched tolerance, our SDIRK2
against Radau, BDF and LSODA on the stiff application problems, and the
honesty rules the validator now enforces (full coverage of the declared cross
product, no cycle number under a cost grade that says there is no cost model,
no wall-clock ratio across two timing families, and no class that vanishes
without saying so).

Timings inside the module-scoped document use a reduced repeat count so the
suite stays quick; the accuracy numbers those tests check are the same ones a
full run produces because nothing about accuracy depends on the repeat count.
The three-class scope is likewise reduced, and the coverage assertion checks
the rows against that reduced scope exactly as it would against the full one.
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
from rk_harness import benchcounts as BC
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


# A small but complete three-class scope: two application problems (one of them
# stiff, which is the regime the implicit class exists for), two targets, short
# ladders. Every coverage rule is checked against this declaration, so a reduced
# scope buys speed and gives up no strictness.
_SCOPE = B.ThreeClassScope(
    problems=("buck_converter", "enzyme_qssa"),
    targets=(2.0 ** -6, 2.0 ** -10),
    n_max=64, bisect_probes=2,
    tol_ladder=(32, 128), max_attempts=2000,
    lib_tol_ladder=(2.0 ** -8, 2.0 ** -14),
    adaptive_tols=(2.0 ** -6,),
    budget_problems=("enzyme_qssa",),
)


@pytest.fixture(scope="module")
def doc():
    return B.build_results(validation_doc=_synthetic_validation_doc(),
                           n_repeats=_N_REPEATS, warmup=_N_WARMUP, scope=_SCOPE)


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


# ------------------------------------------------- B4 three-class benchmark


def test_B4_q15_tolerance_lsb_follows_the_stated_rule():
    """max(1, round(tol * scale * 2**15)), and the clamp is the whole reason the
    rule needs writing down: below one LSB there is no tolerance to ask for."""
    assert BC.q15_tolerance_lsb(2.0 ** -6, 1.0) == 512
    assert BC.q15_tolerance_lsb(2.0 ** -6, 0.25) == 128
    assert BC.q15_tolerance_lsb(2.0 ** -20, 0.25) == 1        # clamped
    for tol in (2.0 ** -4, 2.0 ** -8, 2.0 ** -12):
        for scale in (0.125, 0.25, 1.0, 8.0):
            got = BC.q15_tolerance_lsb(tol, scale)
            assert got == max(1, round(tol * scale * 32768.0))
            assert got >= 1
    with pytest.raises(ValueError):
        BC.q15_tolerance_lsb(0.0, 1.0)
    with pytest.raises(ValueError):
        BC.q15_tolerance_lsb(1.0, 0.0)
    assert "2**15" in BC.TOLERANCE_RULE_ADAPTIVE


def test_B4_below_bias_floor_reads_the_bias_magnitude():
    """The floor bias is reported signed because floor rounding biases downward;
    it is the magnitude that says whether a tolerance is askable."""
    flag, basis = BC.below_bias_floor(1, -1.98)
    assert flag is True and "magnitude" in basis
    flag, basis = BC.below_bias_floor(8, -1.98)
    assert flag is False
    flag, basis = BC.below_bias_floor(1, None)
    assert flag is True and "no floor statistics" in basis


def test_B4_select_benchmark_methods_skips_non_explicit_entries():
    """A validation document that carries implicit or adaptive entries must not
    feed them to a pool that runs the pinned explicit solver."""
    vdoc = _synthetic_validation_doc()
    cls = classical()
    vdoc["methods"].append(
        {"name_or_hash": "sdirk2_fd_jac", "kind": "prototype", "class": "implicit",
         "roles": [], "order": 2, "stages": 2, "tableau": to_json(cls["rk4"])})
    vdoc["methods"].append(
        {"name_or_hash": "bs32_propagating_b", "kind": "prototype",
         "class": "adaptive", "derived_fixed_step": True, "roles": [],
         "order": 3, "stages": 4, "tableau": to_json(cls["rk4"])})
    names = [m["name_or_hash"] for m in B.select_benchmark_methods(vdoc)]
    assert "sdirk2_fd_jac" not in names
    assert "bs32_propagating_b" not in names
    assert "rk4" in names and "euler" in names
    # the guard runs before the hash check, so a non-explicit entry carrying a
    # deliberately wrong name is skipped rather than raising
    vdoc["methods"][-1]["name_or_hash"] = "0" * 64
    B.select_benchmark_methods(vdoc)


def test_B4_adaptive_results_rows_carry_a_class(doc):
    """RK45 and RK23 are explicit pairs under a controller; Radau, BDF and LSODA
    solve an implicit system every step. The table used to file all five under
    one heading that said adaptive."""
    seen = {}
    for r in doc["adaptive_results"]:
        seen[r["integrator"]] = r["class"]
        assert r["side"] == "library"
        assert r["arithmetic"] == "compiled_float64"
        assert r["timing_family"] == "compiled_scipy"
        assert isinstance(r["family"], str) and r["family"]
    assert seen["RK45"] == "adaptive" and seen["RK23"] == "adaptive"
    for name in ("Radau", "BDF", "LSODA"):
        assert seen[name] == "implicit", name


def test_B4_matched_accuracy_covers_the_declared_cross_product(doc):
    scope = doc["three_class_scope"]
    solvers = [s["solver"] for s in doc["solvers"]]
    cells = {(r["solver"], r["problem"], r["target_key"])
             for r in doc["matched_accuracy"]}
    expect = {(s, p, repr(float(t))) for s in solvers
              for p in scope["resolved_problems"] for t in scope["targets"]}
    assert cells == expect
    assert len(doc["matched_accuracy"]) == len(expect)
    for cls, entry in doc["classes"].items():
        assert entry["n_rows"]["matched_accuracy_ours"] >= 1, cls
        assert (entry["n_rows"]["matched_accuracy_library"] >= 1
                or entry.get("library_absent_reason")), cls


def test_B4_rows_name_what_they_do_not_control_for(doc):
    """The bar is honesty: a cross-class or cross-library row states the
    uncontrolled differences in fields, not in a comment."""
    for r in doc["matched_accuracy"]:
        assert r["controls_for"] and isinstance(r["controls_for"], list)
        assert r["not_controlled"] and isinstance(r["not_controlled"], list)
        assert any("arithmetic" in s for s in r["not_controlled"])
        assert any("implementation" in s for s in r["not_controlled"])
        assert r["timing_family"] in BC.TIMING_FAMILIES
        assert r["cost_grade"] in BC.COST_GRADES
        assert r["control_unit"]
        if r["cost_grade"] == "none":
            assert r["analytic_cycles_total"] is None
            assert r["analytic_cycles_per_step"] is None
        if r["status"] != "reached":
            assert r["reason"], (r["solver"], r["problem"], r["status"])


def test_B4_library_rows_publish_nulls_with_reasons(doc):
    """SciPy reports no rejected-step count. For RK23 and RK45 it is recoverable
    exactly from the fixed per-attempt evaluation count; for the rest the field
    is null with the reason beside it rather than a plausible figure."""
    lib = [r for r in doc["matched_accuracy"] if r["side"] == "library"]
    assert lib
    for r in lib:
        assert r["analytic_cycles_total"] is None
        assert r["cost_grade"] == "none"
        assert r["n_steps_rejected_basis"]
        if r["solver"] in ("RK23", "RK45") and r["status"] == "reached":
            assert isinstance(r["n_steps_rejected"], int)
            assert r["n_steps_rejected"] >= 0
            k = BC.SCIPY_FEVALS_PER_ATTEMPT[r["solver"]]
            attempts = (r["nfev"] - BC.SCIPY_INIT_FEVALS) // k
            assert r["n_steps_rejected"] == attempts - r["n_steps_accepted"]
        if r["solver"] in ("Radau", "BDF", "LSODA"):
            assert r["n_steps_rejected"] is None
            assert "cannot be recovered" in r["n_steps_rejected_basis"]


def test_B4_implied_attempts_refuses_a_rounding():
    got, basis = BC.implied_attempts("RK23", 53)
    assert got == 17 and "derived exactly" in basis
    got, basis = BC.implied_attempts("RK23", 54)      # not 2 plus a multiple of 3
    assert got is None and "rounding" in basis
    got, basis = BC.implied_attempts("Radau", 500)
    assert got is None and "fixed number of derivative evaluations" in basis


def test_B4_adaptive_pairs_hold_the_tableau_constant(doc):
    """SciPy RK23 is Bogacki-Shampine 3(2), the pair our prototype runs, so the
    entry states same_pair and the only differences are the controller and the
    arithmetic. No wall-clock ratio crosses the two implementations."""
    pairs = doc["adaptive_pairs"]
    assert pairs
    for e in pairs:
        assert e["same_pair"] is True
        assert e["counterpart"] == "RK23"
        assert e["ours_solver"] == BC.M_BS32_Q15
        assert set(e["solvers"]) <= set(BC.PAIR_SOLVER_ORDER)
        assert e["time_ratio_ours_over_rk23"] is None
        assert e["time_ratio_reason"]
        fams = {v["timing_family"] for v in e["solvers"].values()}
        assert len(fams) > 1                       # which is why the ratio is null
        ours = e["solvers"].get(BC.M_BS32_Q15)
        rk23 = e["solvers"].get("RK23")
        if ours and rk23 and ours["n_fevals"] and rk23["n_fevals"]:
            assert math.isclose(e["fevals_ratio_ours_over_rk23"],
                                ours["n_fevals"] / rk23["n_fevals"], rel_tol=1e-9)


def test_B4_adaptive_tolerance_rows_state_the_conversion(doc):
    rows = doc["adaptive_matched_tolerance"]
    assert rows
    for r in rows:
        assert r["rtol"] == r["tol"] and r["atol"] == r["tol"]
        if r["arithmetic"] == "q15":
            assert r["tol_q_lsb"] == BC.q15_tolerance_lsb(r["tol"], r["scale"])
            assert r["below_bias_floor"] in (True, False)
            assert r["bias_floor_basis"]
        else:
            assert r["tol_q_lsb"] is None
    keys = {(r["solver"], r["problem"], r["tol"]) for r in rows}
    scope = doc["three_class_scope"]
    assert keys == {(s, p, t) for s in BC.PAIR_SOLVER_ORDER
                    for p in scope["resolved_problems"]
                    for t in scope["adaptive_tols"]}


@pytest.mark.slow
def test_B4_implicit_budget_agrees_with_the_side_track():
    """The budget ladder is recomputed here rather than cited, so a test holds it
    equal to sidetrack's own J9 job cell for cell; otherwise the two documents
    drift and both look authoritative."""
    from rk_harness import sidetrack as ST
    name = "enzyme_qssa"
    mine = BC.implicit_budget((name,))[name]
    theirs = ST._run_stiff_budget({"problem": name})["methods"]["sdirk2"]
    assert mine["cycles_per_step"] == theirs["est_cycles_per_step"]
    assert mine["steps_at_budget"] == theirs["steps_at_budget"]
    assert mine["error_at_budget"] == theirs["error_at_budget"]
    assert mine["status_at_budget"] == theirs["status_at_budget"]
    assert mine["f_evals_per_step"] == theirs["f_evals_per_step"]
    assert [r["n"] for r in mine["ladder"]] == theirs["ladder"]
    for a, b in zip(mine["ladder"], theirs["points"]):
        assert a["n"] == b["n"] and a["status"] == b["status"]
        assert a["error"] == b["error"]
        assert a["analytic_cycles"] == b["analytic_cycles"]
    assert mine["library_column"] is None and mine["why_no_library"]


def test_B4_implicit_budget_in_the_document_is_ours_only(doc):
    for name, e in doc["implicit_budget"].items():
        assert e["library_column"] is None
        assert "cycle model" in e["why_no_library"]
        assert e["cost_grade"] == "design_estimate"
        assert e["steps_at_budget"] == e["budget_cycles"] // e["cycles_per_step"]
        for row in e["ladder"]:
            assert row["analytic_cycles"] == row["n"] * e["cycles_per_step"]


@pytest.mark.slow
def test_B4_matched_explicit_rows_agree_with_validation_axes():
    """The ours side runs validation_axes's own probes on secondpass's own
    ladder. Pin that: a benchmark row and the two-axis document's scan of the
    same method on the same problem select the same step count."""
    from rk_harness import validation_axes as VA
    t = classical()["rk4"]
    targets = (2.0 ** -6, 2.0 ** -10)
    spec = BC.explicit_spec("rk4", t)
    rows = BC.explicit_matched_rows(spec, "application", "buck_converter",
                                    targets, n_max=128, bisect_probes=2)
    scan = VA._explicit_scan(t, "buck_converter", targets, 128, 2)
    for r in rows:
        e = scan["targets"][r["target_key"]]
        assert r["control_value"] == e["n"]
        assert r["analytic_cycles_total"] == e["cycles"]
        assert r["probes"] == e["probes"]
        if e["n"] is not None:
            assert r["analytic_cycles_total"] == e["n"] * r["analytic_cycles_per_step"]


@pytest.mark.slow
def test_B4_implicit_rows_price_the_step_from_the_sdirk_estimate():
    from rk_harness.prototypes import sdirk as SD
    spec = [s for s in BC.prototype_specs() if s.key == BC.M_SDIRK_ANALYTIC][0]
    rows = BC.implicit_matched_rows(spec, "application", "enzyme_qssa",
                                    (2.0 ** -10,), n_max=32, bisect_probes=2)
    est = SD.estimate_sdirk2_cycles(2, B.COST_MODEL, newton_iters=SD.NEWTON_ITERS,
                                    fd=False)
    r = rows[0]
    assert r["analytic_cycles_per_step"] == est["total"]
    assert r["cost_grade"] == "design_estimate"
    assert r["cycle_terms"] == est["terms"]
    if r["status"] == "reached":
        assert r["njev"] == r["control_value"]
        assert r["nlu"] == r["control_value"]
        assert r["nlinsolve"] == r["control_value"] * SD.STAGES * SD.NEWTON_ITERS
        assert r["analytic_cycles_total"] == r["control_value"] * est["total"]


def test_B4_timing_is_measured_only_where_the_row_selected_something(doc):
    """Fifteen repeats on every rung of every ladder would be hours. Timing lands
    on the configuration the tightest reached target selected, and nowhere else."""
    timed = [r for r in doc["matched_accuracy"] if r["timing"] is not None]
    assert timed
    for r in timed:
        assert r["status"] == "reached" and r["control_value"] is not None
        assert r["timing"]["n"] == _N_REPEATS
    for r in doc["matched_accuracy"]:
        if r["status"] != "reached":
            assert r["timing"] is None
    assert "timing_policy" in doc and "family" in doc["timing_policy"]


def test_B4_validator_rejects_the_dishonest_shapes(doc):
    """Every honesty rule that can be checked by machine is checked by machine."""
    broken = copy.deepcopy(doc)
    broken["matched_accuracy"].pop()
    with pytest.raises(ValueError, match="every declared"):
        B.validate_results(broken)

    broken = copy.deepcopy(doc)
    for r in broken["matched_accuracy"]:
        if r["cost_grade"] == "none":
            r["analytic_cycles_total"] = 12345
            break
    with pytest.raises(ValueError, match="cost_grade none"):
        B.validate_results(broken)

    broken = copy.deepcopy(doc)
    broken["adaptive_pairs"][0]["time_ratio_ours_over_rk23"] = 3.5
    with pytest.raises(ValueError, match="across two timing families"):
        B.validate_results(broken)

    broken = copy.deepcopy(doc)
    broken["classes"]["adaptive"]["n_rows"]["matched_accuracy_library"] = 0
    broken["classes"]["adaptive"].pop("library_absent_reason", None)
    with pytest.raises(ValueError, match="no library row"):
        B.validate_results(broken)

    broken = copy.deepcopy(doc)
    broken["classes"]["implicit"]["n_rows"]["matched_accuracy_ours"] = 0
    with pytest.raises(ValueError, match="no rows of ours"):
        B.validate_results(broken)

    broken = copy.deepcopy(doc)
    for r in broken["matched_accuracy"]:
        if r["status"] != "reached":
            r["reason"] = None
            break
    with pytest.raises(ValueError, match="no reason"):
        B.validate_results(broken)

    broken = copy.deepcopy(doc)
    broken["adaptive_pairs"][0]["fevals_ratio_ours_over_rk23"] = 99.0
    with pytest.raises(ValueError, match="does not match its rows"):
        B.validate_results(broken)

    broken = copy.deepcopy(doc)
    del broken["comparability"]
    with pytest.raises(ValueError, match="comparability"):
        B.validate_results(broken)


def test_B4_three_class_prose_avoids_the_banned_words(doc):
    banned = re.compile(
        r"(?<![a-z0-9-])(novel|first|beats|outperforms|breakthrough|proves|"
        r"state-of-the-art|best-ever)(?![a-z0-9-])")
    tv = doc["verdicts"]["three_class"]
    comp = doc["comparability"]
    texts = [tv["adaptive_pair"], tv["stiff"], tv["cost_grades"], tv["overall"],
             *tv["per_class"].values(), comp["shared_condition"],
             comp["not_matched"], comp["ratio_rule"], comp["null_rule"],
             comp["tolerance_rule_matched"], comp["tolerance_rule_adaptive"],
             doc["timing_policy"], doc["problem_sets"]["why_two_sets"]]
    texts.extend(str(v) for v in comp["timing_families"].values())
    texts.extend(str(v) for v in comp["arithmetic"].values())
    for block in comp["cost_grades"].values():
        texts.extend(str(v) for v in block.values())
    for r in doc["matched_accuracy"][:40]:
        texts.extend(r["controls_for"] + r["not_controlled"])
    for text in texts:
        assert not banned.search(str(text).lower()), text
        assert "—" not in str(text)


def test_B4_full_scope_is_the_default_and_covers_every_application_problem():
    """The tests run a reduced scope; the document the host writes does not."""
    import inspect
    sig = inspect.signature(B.build_results)
    assert sig.parameters["scope"].default is B.FULL_SCOPE
    full = B.FULL_SCOPE
    assert full.resolved_problems() == BC.problem_names("application")
    assert len(full.resolved_problems()) == 8
    assert full.targets == BC.TARGETS
    assert set(full.resolved_budget_problems()) == set(BC.STIFF_NAMES)
    for name in ("RK23", "RK45", "Radau", "BDF", "LSODA"):
        assert name in full.library
    assert any(BC.problem_entry("application", p)["stiff"]
               for p in full.resolved_problems())


def test_B4_problem_sets_registry_names_the_stiff_regime():
    reg = BC.problem_sets_block()
    assert reg["frozen"]["scored"] is True
    assert reg["application"]["scored"] is False
    assert set(reg["application"]["stiff"]) == set(BC.STIFF_NAMES)
    for name in BC.STIFF_NAMES:
        assert reg["application"]["stiffness_ratio"][name] > 200.0
    # the reason the three-class tables do not run on the frozen set
    ratios = [v for v in reg["frozen"]["stiffness_ratio"].values() if v is not None]
    assert max(ratios) < 100.0


def test_B4_benchcounts_writes_no_thread_environment():
    """The reason benchcounts is its own module: importing benchmark exports five
    BLAS thread variables before numpy loads, which is a process-wide change no
    counter needs to make. The deterministic half makes none."""
    out = subprocess.run(
        [sys.executable, "-c",
         "import os;"
         "keys=('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS',"
         "'NUMEXPR_NUM_THREADS','VECLIB_MAXIMUM_THREADS');"
         "before=[os.environ.get(k) for k in keys];"
         "import rk_harness.benchcounts;"
         "print([os.environ.get(k) for k in keys] == before)"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(Path(__file__).resolve().parents[1]))
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "True", out.stdout


def test_B4_library_rows_degrade_to_a_stated_skip_without_scipy(monkeypatch):
    """A venv without scipy is a stated skip on every library row, with the
    coverage of the table unchanged, rather than a missing class."""
    monkeypatch.setattr(BC, "scipy_available",
                        lambda: (False, "ImportError: no scipy"))
    spec = BC.library_specs(("Radau",))[0]
    rows = BC.library_matched_rows(spec, "application", "buck_converter",
                                   (2.0 ** -6, 2.0 ** -10))
    assert len(rows) == 2
    for r in rows:
        assert r["status"] == "skipped"
        assert "scipy unavailable" in r["reason"]
        assert r["analytic_cycles_total"] is None
        assert r["achieved_error"] is None
        assert r["not_controlled"]
