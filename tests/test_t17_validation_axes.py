"""T17 tests: the two-axis validation document (rk_harness/validation_axes.py).

Covers: the two axes cover exactly the grids they say they cover, once each; an
adaptive cycle number is attempts times a per-attempt cost that is re-derived here
from costmodel rather than read back out of the module; the accepted-only figure is
a lower bound; adaptive is placed on the shared axis by achieved error at the
coarsest rung that meets the target; the matched-point rule is reproduced from the
rows rather than asserted, including the case where no rung of the ladder is coarse
enough and the answer is a null target; a target under the measured bias floor of
the Q15 error estimate comes back as below_bias_floor with the measurement on the
row, and the float twin, which has no such floor, never does; a ladder that
overflows at every rung comes back as a status rather than as an exception; two
builds are byte-identical; every statement passes the banned-word and em-dash rule;
the schema validator rejects damaged documents; and the fixed-step probe scores a
validation problem at all, which the frozen problems.error_metric cannot do.

Two facts this file is here to hold in place. First, validation.py is a member of
sidetrack.SIDETRACK_FILES and validation_axes.py deliberately is not, so a change
here never re-opens a measured side-track point. Second, this document is a sibling
of rk-work/validation/results.json and never a rewrite of it: write_axes lands on
axes.json and nothing here opens results.json for writing.

Every build here is tiny on purpose: one or two problems, a short ladder, a small
attempt cap. The full document is a host job of tens of minutes and nothing in the
suite runs it.
"""
from __future__ import annotations

import copy
import json
import math

import pytest

from rk_harness import sidetrack
from rk_harness import validation as V
from rk_harness import validation_axes as VA
from rk_harness.costmodel import M0PLUS_FAST, count_sequence, cycle_count
from rk_harness.prototypes import adaptive_q15 as AQ
from rk_harness.simulate import problem_error
from rk_harness.tableau import classical
from rk_harness.types import Tableau

# One problem, two states, a two-rung tolerance ladder and a step ladder that stops
# at 64: the whole three-class document in about a tenth of a second.
SMALL = dict(problems=["buck_converter"], n_max=64, bisect_probes=2,
             tol_ladder=(512, 128), max_attempts=2000, anchors=("euler",),
             reference=None)


@pytest.fixture()
def doc():
    d = VA.build_axes(**SMALL)
    VA.validate_axes(d)
    return d


# --------------------------------------------------------------------------- shape

def test_the_module_is_not_a_side_track_digest_input():
    """Editing validation.py re-opens all 103 measured points because it is in the
    digest. This module is not, and must not become, one of those inputs."""
    assert "rk_harness/validation.py" in sidetrack.SIDETRACK_FILES
    assert "rk_harness/validation_axes.py" not in sidetrack.SIDETRACK_FILES


def test_the_default_artifact_is_a_sibling_and_not_results_json(tmp_path, monkeypatch):
    monkeypatch.setenv("RK_WORK_DIR", str(tmp_path))
    out = VA.write_axes({"schema": {"version": VA.SCHEMA_VERSION}})
    assert out.name == "axes.json"
    assert out.parent.name == "validation"
    assert not (tmp_path / "validation" / "results.json").exists()


def test_class_order_leads_with_explicit_and_orders_the_rows(doc):
    assert VA.CLASS_ORDER == ("explicit", "implicit", "adaptive")
    idx = [VA.CLASS_ORDER.index(r["class"]) for r in doc["results_tolerance"]]
    assert idx == sorted(idx)


def test_axis_t_covers_every_method_problem_target_once(doc):
    want = {(m["name_or_hash"], p["name"], k)
            for m in doc["methods"] for p in doc["problems"]
            for k in doc["target_keys"]}
    have = [(r["method"], r["problem"], r["target_key"]) for r in doc["results_tolerance"]]
    assert len(have) == len(want)
    assert set(have) == want


def test_axis_f_covers_the_step_controlled_rows_and_leaves_explicit_referenced(doc):
    want = {(m["name_or_hash"], p["name"])
            for m in doc["methods"] for p in doc["problems"]
            if m["class"] != "explicit"
            and (m["control"] == "steps" or m["derived_fixed_step"])}
    have = [(r["method"], r["problem"]) for r in doc["results_fixed_budget"]]
    assert len(have) == len(want)
    assert set(have) == want
    # Explicit axis F rows are the ones validation/results.json publishes. They are
    # referenced rather than copied, so a run that read no reference has none.
    assert not [r for r in doc["results_fixed_budget"] if r["class"] == "explicit"]


def test_every_row_names_a_cost_basis_the_document_explains(doc):
    used = {r["cost_basis"] for r in doc["results_tolerance"]}
    used |= {m["cost_basis"] for m in doc["methods"]}
    assert used <= set(doc["cost_bases"])
    for name in sorted(used):
        block = doc["cost_bases"][name]
        assert block["includes"] and block["excludes"] and block["direction"]


def test_work_caps_are_counts_and_never_clocks(doc):
    """sidetrack.SWEEP_MAX_ATTEMPTS is the precedent: a run whose bound is a clock
    stops being a pure function of its inputs."""
    for m in doc["methods"]:
        cap = m["work_cap"]
        for key in ("n_max", "bisect_probes", "max_attempts"):
            assert cap[key] is None or isinstance(cap[key], int)
        assert "second" not in json.dumps(cap)
        assert (cap["n_max"] is not None) == (m["control"] == "steps")
        assert (cap["max_attempts"] is not None) == (m["control"] == "tol_q")


def test_the_prototype_methods_this_module_owns_are_the_ones_it_writes(doc):
    """The names are keys in a document a page will render, so they are pinned here
    rather than left to drift with an edit."""
    assert VA.PROTOTYPE_METHODS == (
        "sdirk2_analytic_jac", "sdirk2_fd_jac", "bs32_q15_adaptive",
        "bs32_float_adaptive", "bs32_propagating_b")
    written = {m["name_or_hash"] for m in doc["methods"] if m["source"] == "prototype"}
    assert written == set(VA.PROTOTYPE_METHODS)
    assert not written & set(classical())


def test_the_tolerance_ladder_is_walked_cheapest_rung_leading():
    assert VA.descending_ladder((1, 8, 4)) == (8, 4, 1)
    assert VA.TOL_LADDER_DESCENDING == tuple(sorted(AQ.TOL_LADDER_LSB, reverse=True))


# --------------------------------------------------------------------------- the adaptive cost

def test_the_adaptive_attempt_cost_is_rederived_from_the_pinned_model():
    """The published per-attempt figure, rebuilt from costmodel here rather than
    read back out of the module that produced it."""
    sec = AQ.reference_sections()
    for n_states in (1, 2, 3):
        stage = cycle_count(Tableau(A=AQ.BS32_A, b=AQ.BS32_B, c=AQ.BS32_C),
                            M0PLUS_FAST, n_states)
        estimate = count_sequence(sec["estimate"], M0PLUS_FAST) * n_states
        controller = count_sequence(
            sec["compare"] + sec["bit_scan"] + sec["table_load"] + sec["step_update"],
            M0PLUS_FAST)
        got = VA.adaptive_attempt_cost(n_states)
        assert got["stage"] == stage
        assert got["estimate"] == estimate
        assert got["controller"] == controller
        assert got["total"] == stage + estimate + controller
        assert got["branch_allowance"] == AQ.BRANCH_ALLOWANCE["conditional_branches"]


def test_the_estimate_section_is_charged_per_state():
    """adaptive_q15.sequence_costs sums the sections flat, which under-books a
    multi-state problem. The scaling correction lives here and nowhere else."""
    one = VA.adaptive_attempt_cost(1)
    three = VA.adaptive_attempt_cost(3)
    assert three["estimate"] == 3 * one["estimate"]
    assert three["controller"] == one["controller"]


def test_the_pair_is_priced_inside_the_pinned_function_contract():
    """cycle_count is used on the propagating formula because an embedded pair's A
    is strictly lower triangular, which is what the pinned function takes."""
    A = VA.BS32_TABLEAU.A
    for i, row in enumerate(A):
        for j, a in enumerate(row):
            if j >= i:
                assert a == 0


def test_every_adaptive_cycle_number_is_attempts_times_the_attempt_cost(doc):
    per_attempt = {m["name_or_hash"]: m["cycles_per_attempt"]
                   for m in doc["methods"]
                   if m["class"] == "adaptive" and not m["derived_fixed_step"]}
    checked = 0
    for r in doc["results_tolerance"]:
        if r["class"] != "adaptive" or r["derived_fixed_step"]:
            continue
        cost = per_attempt[r["method"]]
        if cost is None:                    # the float twin carries no cycle number
            assert r["cycles"] is None and r["cycles_accepted_only"] is None
            continue
        if r["cycles"] is None:
            continue
        total = VA.adaptive_attempt_cost(V.PROBLEMS[r["problem"]].n_states)["total"]
        assert cost[r["problem"]]["total"] == total
        assert r["cycles"] == r["counters"]["attempts"] * total
        assert r["cycles_accepted_only"] == r["counters"]["n_accepted"] * total
        checked += 1
    assert checked, "no adaptive row carried a cycle number"


def test_a_missed_row_still_says_how_close_it_got(doc):
    """achieved_error is null on a miss because there is no answering rung, so
    best_achieved_error carries the ladder's own best, and a method that missed by a
    little is distinguishable from one that never ran."""
    missed = [r for r in doc["results_tolerance"] if r["status"] != "reached"]
    assert missed
    assert all(r["achieved_error"] is None for r in missed)
    ran = [r for r in missed if r["best_achieved_error"] is not None]
    assert ran, "no missed row carried a best error"
    for r in ran:
        assert r["best_achieved_error"] > r["target"]
    for r in doc["results_tolerance"]:
        if r["status"] == "reached":
            assert r["best_achieved_error"] <= r["achieved_error"]


def test_a_missed_adaptive_row_still_reports_the_rung_that_came_closest(doc):
    """Without this the counters of a missed row would be empty and the attempt
    count, the rejection count and the step-ceiling count that say WHY it missed
    would be gone. counters_from names the rung they belong to."""
    for r in doc["results_tolerance"]:
        if r["status"] == "reached":
            assert r["counters_from"] == r["control_value"]
        if r["counters"] is not None and any(v is not None for v in r["counters"].values()):
            assert r["counters_from"] is not None
    missed = [r for r in doc["results_tolerance"]
              if r["method"] == VA.M_BS32_Q15 and r["status"] != "reached"]
    assert missed
    for r in missed:
        assert r["cycles"] is None
        assert r["counters_from"] in SMALL["tol_ladder"]
        assert r["counters"]["attempts"] is not None
        assert r["counters"]["h_q_clamped_high"] is not None


def test_accepted_only_is_a_lower_bound_everywhere(doc):
    for r in doc["results_tolerance"]:
        if r["cycles"] is None or r["cycles_accepted_only"] is None:
            continue
        assert r["cycles_accepted_only"] <= r["cycles"]


def test_rejections_are_charged_and_the_fsal_saving_sits_beside_the_number(doc):
    """A rejection costs the stage work, the estimate and the controller, so it is
    booked at full price. FSAL saves an rhs evaluation the cost model prices at zero
    for every class, so it is reported as a count and never as cycles."""
    rows = [r for r in doc["results_tolerance"]
            if r["class"] == "adaptive" and not r["derived_fixed_step"]
            and r["counters"]["attempts"] is not None]
    assert rows
    for r in rows:
        c = r["counters"]
        assert c["attempts"] == c["n_accepted"] + c["n_rejected"]
        assert c["fevals_saved_by_fsal"] == c["n_accepted"]


def test_the_float_twin_stands_on_the_error_axis_and_off_the_cost_axis(doc):
    m = [m for m in doc["methods"] if m["name_or_hash"] == VA.M_BS32_FLOAT][0]
    assert m["arithmetic"] == "float64"
    assert m["cost_basis"] == "unpriced_float64"
    assert m["cycles_per_attempt"] is None
    rows = [r for r in doc["results_tolerance"] if r["method"] == VA.M_BS32_FLOAT]
    assert rows
    assert all(r["cycles"] is None for r in rows)
    # It is on the shared ERROR axis, so it always says how well it did, whether or
    # not any rung of its ladder met a target.
    assert all(r["best_achieved_error"] is not None for r in rows)


def test_the_control_row_is_never_counted_as_an_adaptive_method(doc):
    ctrl = [m for m in doc["methods"] if m["derived_fixed_step"]]
    assert [m["name_or_hash"] for m in ctrl] == [VA.M_BS32_FIXED]
    assert ctrl[0]["class"] == "adaptive"
    assert ctrl[0]["control"] == "steps"
    assert doc["verdicts"]["per_class"]["adaptive"]["methods_evaluated"] == 2
    assert doc["verdicts"]["controls"]["methods_evaluated"] == 1
    for entry in doc["verdicts"]["per_problem"].values():
        for block in entry["best_by_class"].values():
            assert block["method"] != VA.M_BS32_FIXED


# --------------------------------------------------------------------------- placement

def test_the_frozen_error_metric_cannot_score_a_validation_problem():
    """The reason the fixed-step probe here is not simulate.problem_error verbatim:
    problems.error_metric indexes the FROZEN problem set by name, and the validation
    names are deliberately disjoint from it."""
    with pytest.raises(KeyError):
        problem_error(classical()["euler"], V.PROBLEMS["buck_converter"], 256)


def test_the_fixed_step_probe_scores_a_validation_problem_on_the_shared_metric():
    t = classical()["euler"]
    cache: dict = {}
    status, err, max_q = VA._explicit_probe(t, "buck_converter", 512, cache)
    assert status == "ok"
    assert err is not None and math.isfinite(err)
    p = V.PROBLEMS["buck_converter"]
    from rk_harness.problems import to_physical
    from rk_harness.simulate import solve_q15
    final_q, _ = solve_q15(t, p, 512)
    assert err == V.validation_error("buck_converter", to_physical(final_q, p.scale))
    assert cache[512] == (status, err, max_q)


def test_adaptive_is_placed_at_the_coarsest_rung_that_meets_the_target(doc):
    """The rule is secondpass's own smallest-on-the-sampled-set, read on a tolerance
    ladder. Reproduced here by re-running the rungs coarser than the answer and
    checking none of them met the target."""
    ladder = sorted(SMALL["tol_ladder"], reverse=True)
    for r in doc["results_tolerance"]:
        if r["method"] != VA.M_BS32_Q15 or r["status"] != "reached":
            continue
        assert r["probes"] == 0             # nothing sits between whole LSB rungs
        assert r["achieved_error"] <= r["target"]
        chosen = ladder.index(r["control_value"])
        for coarser in ladder[:chosen]:
            res = AQ.solve_adaptive_q15(r["problem"], V.SCALE[r["problem"]], coarser,
                                        max_attempts=SMALL["max_attempts"])
            err = res.get("achieved_error")
            assert res["status"] != "ok" or err is None or err > r["target"]


def test_a_ladder_that_overflows_at_every_rung_is_a_status_and_not_an_exception():
    """rk4 on buck_converter with the ladder stopped at four steps: h is 6.25 in the
    problem's own time units, so every rung leaves Q15 before it produces a number."""
    d = VA.build_axes(classes=("explicit",), problems=["buck_converter"], n_max=4,
                      bisect_probes=2, anchors=("rk4",), reference=None)
    VA.validate_axes(d)
    rows = d["results_tolerance"]
    assert rows
    assert {r["status"] for r in rows} == {"overflow_before_target"}
    assert all(r["cycles"] is None and r["achieved_error"] is None for r in rows)


def test_a_target_under_the_bias_floor_says_so_and_carries_the_measurement():
    tiny = 1.0e-12
    d = VA.build_axes(classes=("adaptive",), problems=["buck_converter"],
                      targets=(2.0 ** -6, tiny), tol_ladder=(512, 128),
                      max_attempts=2000, include_control=False, reference=None)
    VA.validate_axes(d)
    floor = d["estimate_floor"]["buck_converter"]
    assert floor["mean_bias_lsb_total"] is not None
    assert floor["tolerance_below_which_unreachable"] > tiny
    assert floor["source"] in ("measured_in_this_run",
                              "sidetrack:adaptive.q15_estimate_floor")
    key = VA._target_key(tiny)
    q15 = [r for r in d["results_tolerance"]
           if r["method"] == VA.M_BS32_Q15 and r["target_key"] == key]
    assert [r["status"] for r in q15] == ["below_bias_floor"]
    # The floor is a property of the Q15 error estimate. The float twin runs the
    # same controller in float64 and has no such floor, so it is never told its miss
    # was below one.
    twin = [r for r in d["results_tolerance"]
            if r["method"] == VA.M_BS32_FLOAT and r["target_key"] == key]
    assert twin and all(r["status"] != "below_bias_floor" for r in twin)


def test_the_floor_prefers_the_side_track_measurement_when_one_is_on_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("RK_WORK_DIR", str(tmp_path))
    path = sidetrack.artifact_path("adaptive.q15_estimate_floor", "buck_converter_s1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"summary": {"mean_bias_lsb_total": -1.75}}),
                    encoding="utf-8")
    entry = VA.estimate_floor_entry("buck_converter", {}, VA.Floats())
    assert entry["source"] == "sidetrack:adaptive.q15_estimate_floor"
    assert entry["mean_bias_lsb_total"] == -1.75
    assert entry["lsb_of_scaled_state"] == 1.0 / (32768.0 * V.SCALE["buck_converter"])
    assert entry["tolerance_below_which_unreachable"] == pytest.approx(
        1.75 * entry["lsb_of_scaled_state"] / V.PEAK["buck_converter"])


@pytest.mark.parametrize("base,rungs,target,floor,want", [
    ("reached", [], 1.0, None, "reached"),
    ("never_reached", [{"status": "ok", "h_q_clamped_high": 0}], 1e-9, 1e-6,
     "below_bias_floor"),
    ("never_reached", [{"status": "attempt_cap", "h_q_clamped_high": 0}], 1.0, None,
     "attempt_cap"),
    ("never_reached", [{"status": "step_underflow", "h_q_clamped_high": 0}], 1.0, None,
     "step_underflow"),
    ("never_reached", [{"status": "overflow", "h_q_clamped_high": 0}], 1.0, None,
     "overflow_before_target"),
    ("never_reached", [{"status": "ok", "h_q_clamped_high": 3}], 1.0, None,
     "h_q_ceiling"),
    ("never_reached", [{"status": "ok", "h_q_clamped_high": 3},
                       {"status": "attempt_cap", "h_q_clamped_high": 1}], 1.0, None,
     "h_q_ceiling"),
    ("never_reached", [{"status": "attempt_cap", "h_q_clamped_high": 3},
                       {"status": "step_underflow", "h_q_clamped_high": 1}], 1.0, None,
     "never_reached"),
    ("never_reached", [{"status": "ok", "h_q_clamped_high": 0}], 1.0, None,
     "never_reached"),
])
def test_the_adaptive_miss_is_named_by_the_reason_it_missed(base, rungs, target, floor, want):
    assert VA._adaptive_target_status(base, rungs, target, floor) == want


def test_an_implicit_ladder_with_only_nonfinite_rungs_names_the_newton_iteration():
    ladder = [{"n": 1, "status": "nonfinite"}, {"n": 2, "status": "nonfinite"}]
    assert VA._implicit_target_status("never_reached", ladder) == "newton_nonconvergent"
    mixed = [{"n": 1, "status": "nonfinite"}, {"n": 2, "status": "overflow"}]
    assert VA._implicit_target_status("never_reached", mixed) == "never_reached"
    ran = [{"n": 1, "status": "ok"}, {"n": 2, "status": "nonfinite"}]
    assert VA._implicit_target_status("never_reached", ran) == "never_reached"


def test_the_implicit_row_says_it_ran_in_float64(doc):
    rows = [r for r in doc["results_fixed_budget"] if r["class"] == "implicit"]
    assert rows
    for r in rows:
        assert r["arithmetic"] == "float64"
        assert r["q15_error"] is None and r["max_abs_q"] is None
    for m in doc["methods"]:
        if m["class"] != "implicit":
            continue
        assert m["arithmetic"] == "float64"
        assert m["div_cycles"] == 32
        for pname, used in m["jacobian_by_problem"].items():
            analytic = V.ANALYTIC_JACOBIAN[pname] is not None
            assert used == ("analytic" if (m["jacobian"] == "analytic" and analytic)
                            else "finite_difference")


def test_the_problem_block_states_where_the_q15_step_saturates():
    """h_q cannot reach 1.0 in a problem's own time units, so a long window forces at
    least t_end steps whatever the tolerance. That is a property of the encoding and
    is stated per problem rather than inferred from a run that did badly."""
    d = VA.build_axes(classes=("adaptive",), problems=["buck_converter",
                                                       "robertson_scaled"],
                      targets=(2.0 ** -6,), tol_ladder=(512,), max_attempts=800,
                      include_control=False, reference=None)
    by_name = {p["name"]: p for p in d["problems"]}
    assert by_name["buck_converter"]["h_q_saturates_at_start"] is False
    assert by_name["robertson_scaled"]["h_q_saturates_at_start"] is True
    assert by_name["robertson_scaled"]["h_q_initial"] == AQ.Q15_MAX
    rows = [r for r in d["results_tolerance"]
            if r["problem"] == "robertson_scaled" and r["method"] == VA.M_BS32_Q15]
    assert rows
    # Whether or not the row reached its target, the counters come from a real rung,
    # so the saturating starting step is visible on the row as well as on the problem.
    assert all(r["counters"]["h_q_initial"] == AQ.Q15_MAX for r in rows)


# --------------------------------------------------------------------------- matched point

def test_the_matched_point_rule_is_reproduced_from_the_rows(doc):
    for pname, mp in doc["matched_point"].items():
        cells = [r for r in doc["results_fixed_budget"]
                 if r["problem"] == pname and r["q15_error"] is not None]
        if not cells:
            assert mp["target"] is None and mp["comparable_rows"] == []
            continue
        best = min(c["q15_error"] for c in cells)
        assert mp["axis_f_reference_error"] == best
        below = [t for t in doc["targets"] if t <= best]
        if not below:
            # No rung of the ladder is that coarse. That is a result about the
            # ladder rather than a gap in the table.
            assert mp["target"] is None and mp["comparable_rows"] == []
            continue
        assert mp["target"] == max(below)
        assert mp["target_key"] == VA._target_key(mp["target"])
        want = sorted(
            [(r["cycles"], r["method"]) for r in doc["results_tolerance"]
             if r["problem"] == pname and r["target_key"] == mp["target_key"]
             and r["status"] == "reached" and r["cycles"] is not None])
        assert [(c["cycles"], c["method"]) for c in mp["comparable_rows"]] == want
        for c in mp["comparable_rows"]:
            assert c["cycles_over_budget"] == c["cycles"] / 65536.0


def test_a_problem_with_no_coarse_enough_rung_has_a_null_target():
    """The coarsest target is 2**-12, so a budget that already bought an error under
    that leaves no target at or below it, and the matched point is null. That is a
    result about the ladder, not a gap in the table."""
    fl = VA.Floats()
    axis_f = [{"method": "m", "class": "implicit", "problem": "buck_converter",
               "q15_error": 1.0e-9, "float_error": None, "arithmetic": "q15",
               "derived_fixed_step": False}]
    mp = VA.matched_point("buck_converter", axis_f, [], list(VA.TARGETS), False, fl)
    assert mp["axis_f_reference_error"] == 1.0e-9
    assert mp["target"] is None
    assert mp["comparable_rows"] == []
    assert mp["rule"] == VA.MATCHED_POINT_RULE


def test_the_matched_point_names_the_three_grades_of_number(doc):
    for mp in doc["matched_point"].values():
        for basis in ("assembly_verified", "design_estimate_div32",
                      "upper_bound_unpriced_branches"):
            assert basis in mp["caveat"]
    assert doc["verdicts"]["axes"]["comparable"] is False
    assert doc["verdicts"]["axes"]["matched_point_rule"] == VA.MATCHED_POINT_RULE


# --------------------------------------------------------------------------- prose and determinism

def test_every_statement_passes_the_word_rule(doc):
    banned = ("novel", "first", "beats", "outperforms", "breakthrough", "proves",
              "state-of-the-art", "best-ever")
    texts = [doc["verdicts"]["overall"], doc["verdicts"]["controls"]["statement"]]
    texts += [doc["verdicts"]["per_class"][c]["statement"] for c in VA.CLASS_ORDER]
    texts += [VA.MATCHED_POINT_RULE, VA.MATCHED_POINT_CAVEAT, VA.NOT_COMPARABLE]
    for text in texts:
        assert text.strip()
        low = text.lower()
        for word in banned:
            assert word not in low, f"{word!r} in {text!r}"
        assert chr(0x2014) not in text


def test_the_whole_document_is_swept_for_banned_words(doc):
    bad = copy.deepcopy(doc)
    bad["verdicts"]["overall"] = "this document proves the point"
    with pytest.raises(ValueError, match="banned word"):
        VA.validate_axes(bad)
    worse = copy.deepcopy(doc)
    worse["cost_bases"]["assembly_verified"]["grade"] = "a " + chr(0x2014) + " grade"
    with pytest.raises(ValueError, match="em dash"):
        VA.validate_axes(worse)


def test_two_builds_are_byte_identical(tmp_path):
    a = VA.build_axes(**SMALL)
    b = VA.build_axes(**SMALL)
    VA.validate_axes(a)
    VA.validate_axes(b)
    pa = VA.write_axes(a, tmp_path / "a.json")
    pb = VA.write_axes(b, tmp_path / "b.json")
    assert pa.read_bytes() == pb.read_bytes()


def test_the_clock_is_read_only_when_the_caller_asks(doc):
    assert doc["generated_from"]["generated_ts"] is None
    stamped = VA.build_axes(generated_ts="2026-09-09T00:00:00Z", **SMALL)
    assert stamped["generated_from"]["generated_ts"] == "2026-09-09T00:00:00Z"
    VA.validate_axes(stamped)


def test_a_partial_run_stamps_what_it_ran(doc):
    gf = doc["generated_from"]
    assert gf["classes"] == list(VA.CLASS_ORDER)
    assert gf["prototype_params"]["n_max"] == SMALL["n_max"]
    assert gf["prototype_params"]["max_attempts"] == SMALL["max_attempts"]
    assert gf["prototype_params"]["tol_ladder_lsb"] == list(SMALL["tol_ladder"])
    assert gf["validation_results"]["present"] is False
    assert [p["name"] for p in doc["problems"]] == SMALL["problems"]
    for m in doc["methods"]:
        for key in ("cycles_per_step", "steps", "cycles_per_attempt"):
            v = m.get(key)
            if isinstance(v, dict):
                assert sorted(v) == sorted(SMALL["problems"])


# --------------------------------------------------------------------------- the validator

def test_the_document_validates(doc):
    VA.validate_axes(doc)


def test_a_cycles_value_with_no_cost_basis_is_rejected(doc):
    bad = copy.deepcopy(doc)
    row = next(r for r in bad["results_tolerance"] if r["cycles"] is not None)
    row["cost_basis"] = ""
    with pytest.raises(ValueError, match="cost_basis"):
        VA.validate_axes(bad)


@pytest.mark.parametrize("damage,fragment", [
    (lambda d: d.pop("matched_point"), "matched_point"),
    (lambda d: d["schema"].__setitem__("version", "validation-axes/9"), "schema.version"),
    (lambda d: d.__setitem__("class_order", ["adaptive", "implicit", "explicit"]),
     "class_order"),
    (lambda d: d["results_tolerance"].pop(), "exactly"),
    (lambda d: d["results_fixed_budget"].pop(), "exactly once"),
    (lambda d: d["methods"][0].__setitem__("class", "hybrid"), "bad class"),
    (lambda d: d["methods"][0].__setitem__("cost_basis", "vibes"), "bad cost_basis"),
    (lambda d: d["results_tolerance"][0].__setitem__("status", "fine"), "bad status"),
    (lambda d: d["verdicts"]["axes"].__setitem__("comparable", True), "comparable"),
    (lambda d: d["generated_from"].__setitem__("sidetrack_code_hash", ""),
     "sidetrack_code_hash"),
])
def test_the_validator_rejects_damaged_documents(doc, damage, fragment):
    bad = copy.deepcopy(doc)
    damage(bad)
    with pytest.raises(ValueError, match=fragment):
        VA.validate_axes(bad)


def test_a_row_whose_accepted_only_exceeds_its_total_is_rejected(doc):
    bad = copy.deepcopy(doc)
    row = next(r for r in bad["results_tolerance"] if r["cycles"] is not None)
    row["cycles_accepted_only"] = row["cycles"] + 1
    with pytest.raises(ValueError, match="cycles_accepted_only"):
        VA.validate_axes(bad)


def test_non_finite_numbers_are_written_as_null_and_counted():
    fl = VA.Floats()
    assert fl(float("nan")) is None
    assert fl(float("inf")) is None
    assert fl(1.5) == 1.5
    assert fl.nonfinite == 2


def test_unknown_selections_are_refused():
    with pytest.raises(ValueError, match="unknown classes"):
        VA.build_axes(classes=("hybrid",), reference=None)
    with pytest.raises(ValueError, match="unknown problems"):
        VA.build_axes(problems=["dahlquist"], reference=None)


# --------------------------------------------------------------------------- reference and cli

def test_the_referenced_document_supplies_the_explicit_axis_f_rows():
    ref = {
        "generated_from": {"archive_records": 7, "champion_hash": "abc",
                           "verifier_hash": "def"},
        "budget_cycles": 65536,
        "cost_model": "m0plus_fast",
        "methods": [{"name_or_hash": "euler", "kind": "classical"}],
        "results": [{"method": "euler", "problem": "buck_converter", "steps": 6553,
                     "cycles_per_step": 10, "q15_error": 0.02, "float_error": 0.01,
                     "max_abs_q": 20000}],
    }
    d = VA.build_axes(classes=("adaptive",), problems=["buck_converter"],
                      tol_ladder=(512,), max_attempts=1000, include_control=False,
                      reference=ref)
    VA.validate_axes(d)
    rows = [r for r in d["results_fixed_budget"] if r["class"] == "explicit"]
    assert [r["method"] for r in rows] == ["euler"]
    assert rows[0]["q15_error"] == 0.02
    mp = d["matched_point"]["buck_converter"]
    assert mp["axis_f_reference_method"] == "euler"
    assert mp["axis_f_reference_class"] == "explicit"
    assert d["generated_from"]["validation_results"]["present"] is True
    assert d["generated_from"]["validation_results"]["archive_records"] == 7


def test_cli_writes_the_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("RK_WORK_DIR", str(tmp_path))
    out = tmp_path / "axes.json"
    rc = VA.main(["--classes", "implicit", "--problems", "buck_converter",
                  "--n-max", "8", "--bisect-probes", "1", "--no-discovered",
                  "--no-stamp", "--out", str(out)])
    assert rc == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    VA.validate_axes(doc)
    assert doc["generated_from"]["generated_ts"] is None
    assert {m["class"] for m in doc["methods"]} == {"implicit"}
