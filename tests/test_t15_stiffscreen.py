"""T15 tests: the stiff-candidate screen (rk_harness/stiffscreen.py).

Deliberately its own tier rather than folded into T8. Every T8 test that bears on
a stiff problem is parametrized over validation.VALIDATION_NAMES, and a screen
candidate is by design not in that tuple; mixing the two is how a candidate would
quietly acquire the out-of-band suite's finisher assertions, which is the thing
this module exists to keep as an owner decision.

Covers: the candidate names stay out of both suites and out of the pinned
derivative-scale map, the scale search prices the margin the way the verifier does
and keeps its rejections, the Jacobian and eigenvalue path reproduces a documented
ratio, references agree with fine float integration, the wall test accounts for
every method it evaluates and compares at matched analytic cost, the screen never
raises, the results document validates and writes deterministically, and the
standing guard that a screen run adopts nothing.
"""
from __future__ import annotations

import copy
import math
import re

import numpy as np
import pytest

from rk_harness import problems as frozen_problems
from rk_harness import sidetrack
from rk_harness import stiffscreen as S
from rk_harness import validation as V
from rk_harness import verifier_hash
from rk_harness.costmodel import M0PLUS_FAST
from rk_harness.prototypes.sdirk import estimate_sdirk2_cycles
from rk_harness.simulate import steps_for_budget
from rk_harness.tableau import classical

# The one candidate the screen admits today, used wherever a test needs a run that
# reaches the later steps. If the parameters move, this moves with them.
_ADMITTED = "thermal_3node_fast"
# Coarse trajectory resolution for the tests: a multiple of JAC_SAMPLES, one tenth
# of PEAK_STEPS, the same relationship T8 uses (4000 against the stored 40000).
_N = 4000


@pytest.fixture(scope="module")
def doc():
    return S.build_results(n=_N)


@pytest.fixture(scope="module")
def admitted_entry(doc):
    return next(e for e in doc["screen"] if e["candidate"] == _ADMITTED)


# --------------------------------------------------------------------------- registry hygiene

def test_candidate_names_are_disjoint_from_both_suites():
    assert not set(S.CANDIDATES) & set(frozen_problems.PROBLEMS)
    assert not set(S.CANDIDATES) & set(V.VALIDATION_NAMES)
    assert len(set(S.CANDIDATES)) == len(S.CANDIDATES)
    for name in S.CANDIDATES:
        assert name in S.FLOAT_RHS and name in S.REFERENCE and name in S.CANDIDATE_META
        assert len(S.PER_STATE_PEAKS[name]) == S.N_STATES[name]


def test_every_candidate_runs_at_derivative_scale_one():
    """The silent trap: solve_q15 forms h_q from problems.DERIV_SCALE.get(name,
    1.0), reading the pinned map by name. A candidate scaled on its right-hand
    side but absent from that map integrates with the wrong h and raises nothing,
    so both sides of the lookup have to read 1.0."""
    assert set(S.DERIV_SCALE) == set(S.CANDIDATES)
    for name in S.CANDIDATES:
        assert S.DERIV_SCALE[name] == 1.0, name
        assert frozen_problems.DERIV_SCALE.get(name, 1.0) == 1.0, name
        assert name not in frozen_problems.DERIV_SCALE, name


def test_peaks_match_measurement():
    for name in S.CANDIDATES:
        measured = S.reference_trajectory(name, n=_N)["per_state_peaks"]
        stored = S.PER_STATE_PEAKS[name]
        assert len(measured) == len(stored) == S.N_STATES[name]
        for m, s in zip(measured, stored):
            assert abs(m - s) <= 1e-3 * max(s, 1e-12), (name, measured, stored)
        assert S.PEAK[name] == max(stored)


# --------------------------------------------------------------------------- the scale search

def _validation_row(name: str, scale: float) -> dict:
    """The scale row the screen would compute for one out-of-band problem at the
    scale that suite chose. Only the state-margin arithmetic is under test here,
    so the derivative inputs are the ones at t = 0 rather than a full window
    measurement."""
    d0 = tuple(V.FLOAT_RHS[name](0.0, V.Y0_PHYS[name]))
    rows = S.admissible_scales(
        V.PER_STATE_PEAKS[name], tuple(abs(v) for v in d0), d0,
        V.PROBLEMS[name].t_end, V.PROBLEMS[name].n_states,
    )
    return next(r for r in rows if r["scale"] == scale)


def test_scale_search_rejects_a_margin_of_exactly_one():
    """robertson_scaled and enzyme_qssa both reach max|y| * scale = 0.5, giving
    overflow_margin exactly 1.000, and battery_2rc sits at 0.808. verifier.py
    rejects a tableau whose overflow_margin is at or below 1.0 and computes that
    margin over the whole scored set at once, so none of the three could be
    adopted into the scored suite at its present scale. MIN_OVERFLOW_MARGIN is 1.2
    for that reason and all three rows have to come back not admitted."""
    for name, scale, expected in (
        ("robertson_scaled", 0.5, 1.0),
        ("enzyme_qssa", 0.5, 1.0),
        ("battery_2rc", 8.0, 0.808148),
    ):
        row = _validation_row(name, scale)
        assert math.isclose(row["state_margin"], expected, rel_tol=1e-5), (name, row)
        assert row["state_margin"] < S.MIN_OVERFLOW_MARGIN
        assert row["admitted"] is False, (name, row)
        assert "state_margin" in row["failed"], (name, row)


def test_scale_search_reproduces_the_battery_stall_threshold():
    """validation.py records by hand that battery_2rc needs per-step increments of
    2 LSB or more and that at scale 4 its Q15 integrator stalls. The screen's
    increment floor has to reproduce that: 2.74 LSB at scale 8, 1.37 at scale 4."""
    assert math.isclose(_validation_row("battery_2rc", 8.0)["start_increment_lsb"],
                        2.736, rel_tol=1e-3)
    row4 = _validation_row("battery_2rc", 4.0)
    assert row4["start_increment_lsb"] < S.MIN_INCREMENT_LSB
    assert "start_increment" in row4["failed"]


def test_scale_search_keeps_its_rejections():
    """Every exponent comes back, admitted or not, so 'no scale exists' is a table
    a reader can check rather than an empty result."""
    for name in S.CANDIDATES:
        rows, bounds = S.candidate_scales(name, S.reference_trajectory(name, n=_N))
        assert [r["exponent"] for r in rows] == sorted(S.SCALE_EXPONENTS)
        assert all(r["scale"] == 2.0 ** r["exponent"] for r in rows)
        for r in rows:
            assert r["admitted"] == (not r["failed"]), (name, r)
        if not any(r["admitted"] for r in rows):
            # the stronger statement: no scale works at any exponent, searched or not
            assert bounds["interval_is_empty"], (name, bounds)


def test_the_flame_candidates_stall_before_they_ignite():
    """The rejection that is the finding: y' = y^2 - y^3 starts at 2**-6 with a
    derivative of 2.4e-4, so the increment the cheapest anchor can take is a
    fraction of one LSB, while the state reaches 1.0 and caps the scale at 0.42.
    The two bounds do not overlap, at any exponent."""
    for name in ("flame_scalar_e64", "flame_scalar_e256"):
        rows, bounds = S.candidate_scales(name, S.reference_trajectory(name, n=_N))
        assert not any(r["admitted"] for r in rows), name
        assert bounds["interval_is_empty"], (name, bounds)
        assert bounds["lower_from_start_increment"] > bounds["upper_from_state_margin"]


# --------------------------------------------------------------------------- stiffness

def test_stiffness_measured_on_a_known_linear_system():
    """The frozen rc_thermal matrix has its eigenvalues and stiffness ratio written
    into fixtures/problems.json (16.047758 over 0.228807, ratio 70.137). Running
    the screen's central-difference Jacobian and eigenvalue path over it has to
    reproduce that number."""
    A = frozen_problems._RC_A

    def rhs(t, y):
        return tuple(A[r][0] * y[0] + A[r][1] * y[1] + A[r][2] * y[2] for r in range(3))

    samples = [(0.0, (1.0, 0.0, 0.0)), (1.0, (0.5, 0.4, 0.1)), (2.0, (0.2, 0.3, 0.3))]
    spec = S.jacobian_spectrum(rhs, samples)
    assert math.isclose(spec["fast_abs_re"], 16.047758, rel_tol=1e-6)
    assert math.isclose(spec["slow_abs_re"], 0.228807, rel_tol=1e-5)
    assert math.isclose(spec["stiffness_ratio"], 70.137, rel_tol=1e-4)
    assert spec["samples"] == len(samples)


def test_the_thinned_thermal_candidate_lands_in_the_band(admitted_entry):
    """The thinning exists to move the ratio from the frozen 70.1 into the 50 to
    2000 band T8 asserts for its own stiff subset, and the run has to start on the
    slow manifold so stiffness is felt through stability rather than range."""
    ev = sorted(abs(v.real) for v in np.linalg.eigvals(np.array(S._TN_A, dtype=float)))
    st = admitted_entry["stiffness"]
    assert math.isclose(st["stiffness_ratio"], ev[-1] / ev[0], rel_tol=1e-9)
    lo, hi = S.STIFFNESS_BAND
    assert lo <= st["stiffness_ratio"] <= hi
    assert st["jacobian"] == "analytic"
    assert st["samples"] == S.JAC_SAMPLES + 1
    assert st["start_deriv_q"] <= S.START_DERIV_BOUND
    assert st["starts_on_slow_manifold"] is True


# --------------------------------------------------------------------------- references

@pytest.mark.slow
@pytest.mark.parametrize("name", S.CANDIDATES)
def test_reference_agrees_with_fine_float_integration(name):
    r = S.reference_computability(name)
    assert r["max_rel_diff"] is not None, r
    assert r["max_rel_diff"] <= S.REF_TOL, (name, r["max_rel_diff"])
    assert r["computable"] is True
    if S.FAMILY[name] == "linear":
        assert r["kind"].startswith("matrix exponential")
    else:
        assert r["kind"] == "mpmath odefun at 30 digits"


# --------------------------------------------------------------------------- the wall test

@pytest.fixture(scope="module")
def wall():
    return S.wall_test(_ADMITTED, 0.5)


def test_wall_test_counts_overflow_rather_than_dropping_it(wall):
    """kutta3, rk4 and rk38 can afford only 840, 661 and 606 steps on three states,
    which puts h times the fast eigenvalue at 2.58, 3.28 and 3.58, outside their
    stability intervals, and their Q15 runs overflow. An overflow is a counted
    outcome, not a dropped row."""
    assert wall["overflowed"] >= 1
    assert wall["finishers"] + wall["overflowed"] + wall["nonfinite"] \
        == wall["methods_evaluated"]
    assert wall["methods_evaluated"] == len(classical())
    outcomes = {r["method"]: r["outcome"] for r in wall["methods"]}
    assert set(outcomes) == set(classical())
    for r in wall["methods"]:
        assert (r["q15_error"] is not None) == (r["outcome"] == "finished"), r
        assert r["outcome"] in ("finished", "overflowed", "nonfinite", "unaffordable")


def test_wall_test_runs_without_the_archive():
    """The J10 eligibility check: the archive-free half is self-contained, so a
    side-track job could lift this function unchanged. A side-track plan may not
    depend on archive contents."""
    entry = S.screen(_ADMITTED, records=None, n=_N)
    assert entry["admitted"] is True, entry["reason"]
    w = entry["wall"]
    assert w["archive_elites"] == 0
    assert w["methods_offered"] == len(classical())
    assert all(r["kind"] == "classical" for r in w["methods"])
    assert w["wall"] in (True, False)
    assert entry["reference"]["computable"] is True


def test_wall_verdict_uses_matched_analytic_cost(wall):
    """The comparison is at analytic cost, not at step count: the SDIRK2 reference
    gets the steps its own per-step estimate buys out of the same budget."""
    cost = estimate_sdirk2_cycles(S.N_STATES[_ADMITTED], M0PLUS_FAST)["total"]
    sd = wall["sdirk2_reference"]
    assert sd["cycles_per_step"] == cost
    assert sd["steps"] == S.BUDGET_CYCLES // cost
    assert sd["jacobian"] == "analytic"
    anchor_steps = {
        steps_for_budget(t, M0PLUS_FAST, S.N_STATES[_ADMITTED], S.BUDGET_CYCLES)
        for t in classical().values()
    }
    assert sd["steps"] not in anchor_steps
    assert sd["error"] is not None
    assert wall["wall_threshold"] == S.WALL_MULTIPLE * sd["error"]
    assert wall["wall"] is (wall["best_finisher_q15_error"] > wall["wall_threshold"])


# --------------------------------------------------------------------------- the screen

def test_screen_never_raises(monkeypatch):
    """A candidate whose states leave every power-of-two scale comes back as a
    rejection dict carrying its reason, and so does one whose float trajectory
    diverges outright."""
    monkeypatch.setitem(S.Y0_PHYS, _ADMITTED, (1.0e6, 1.0e6, 1.0e6))
    out = S.screen(_ADMITTED, n=_N)
    assert out["admitted"] is False
    assert out["reason"] == "no admissible power-of-two state scale"
    assert out["stopped_at"] == "dynamic_range"
    assert out["scales"] and not any(r["admitted"] for r in out["scales"])
    assert out["scale_bounds"]["interval_is_empty"] is True

    monkeypatch.setitem(S.Y0_PHYS, "flame_scalar_e64", (1.0e9,))
    out2 = S.screen("flame_scalar_e64", n=_N)
    assert out2["admitted"] is False
    assert isinstance(out2["reason"], str) and out2["reason"]


def test_screen_short_circuits_and_keeps_its_evidence(doc):
    for e in doc["screen"]:
        assert isinstance(e["reason"], str) and e["reason"]
        assert e["measurement"] is not None
        if e["admitted"]:
            assert e["stopped_at"] is None
            assert e["scale"] is not None and e["wall"] is not None
        else:
            assert e["stopped_at"] is not None
            # nothing past the step that stopped it is fabricated
            if e["stopped_at"] == "dynamic_range":
                assert e["scale"] is None and e["stiffness"] is None
                assert e["wall"] is None and e["reference"] is None


# --------------------------------------------------------------------------- results document

def test_results_schema_validates(doc):
    S.validate_results(doc)
    assert doc["budget_cycles"] == 65536
    assert doc["cost_model"] == "m0plus_fast"
    assert [c["name"] for c in doc["candidates"]] == list(S.CANDIDATES)
    assert len(doc["screen"]) == len(S.CANDIDATES)
    rec = doc["recommendation"]
    assert rec["screened"] == len(S.CANDIDATES)
    assert rec["surviving"] == sum(1 for e in doc["screen"] if e["admitted"])
    assert rec["screened"] >= rec["surviving"]
    assert sorted(rec["carry_forward"]) == sorted(
        e["candidate"] for e in doc["screen"] if e["admitted"])
    assert len(rec["rejected"]) == rec["screened"] - rec["surviving"]
    assert doc["adoption"]["taken_here"] == "neither"


def test_results_schema_rejects_bad_docs(doc):
    bad = copy.deepcopy(doc)
    bad["screen"][0]["reason"] = ""
    with pytest.raises(ValueError, match="reason"):
        S.validate_results(bad)

    bad2 = copy.deepcopy(doc)
    del bad2["recommendation"]["screened"]
    with pytest.raises(ValueError, match="screened"):
        S.validate_results(bad2)

    bad3 = copy.deepcopy(doc)
    bad3["recommendation"]["surviving"] = bad3["recommendation"]["surviving"] + 1
    with pytest.raises(ValueError, match="surviving"):
        S.validate_results(bad3)

    bad4 = copy.deepcopy(doc)
    bad4["screen"][0]["measurement"]["peak"] = float("inf")
    with pytest.raises(ValueError, match="peak"):
        S.validate_results(bad4)

    bad5 = copy.deepcopy(doc)
    bad5["screen"][0]["scales"].pop()
    with pytest.raises(ValueError, match="exponent"):
        S.validate_results(bad5)

    bad6 = copy.deepcopy(doc)
    bad6["candidates"][0]["deriv_scale"] = 0.125
    with pytest.raises(ValueError, match="deriv_scale"):
        S.validate_results(bad6)

    bad7 = copy.deepcopy(doc)
    del bad7["recommendation"]
    with pytest.raises(ValueError, match="recommendation"):
        S.validate_results(bad7)


def test_write_results_deterministic(tmp_path, doc):
    p1 = S.write_results(doc, tmp_path / "a.json")
    p2 = S.write_results(doc, tmp_path / "b.json")
    b1, b2 = p1.read_bytes(), p2.read_bytes()
    assert b1 == b2
    assert b"\r\n" not in b1


_BANNED = re.compile(
    r"\b(novel|first|beats|outperforms|breakthrough|proves|state-of-the-art|best-ever)\b",
    re.IGNORECASE,
)


def test_document_prose_is_site_safe(doc):
    """This artifact can reach the findings site, so its prose stays clear of the
    banned-word list and uses no em dashes."""
    texts: list[str] = [doc["recommendation"]["note"]]
    texts.extend(str(v) for v in doc["schema"].values())
    texts.extend(str(v) for v in doc["adoption"].values())
    for c in doc["candidates"]:
        texts.extend([c["origin"], c["equation"], c["reference"]])
    for e in doc["screen"]:
        texts.append(e["reason"])
    for text in texts:
        assert not _BANNED.search(text), text
        assert "—" not in text


# --------------------------------------------------------------------------- standing guards

def test_the_screen_adopts_nothing(doc):
    """The canary. A screen run measures candidates and writes a document; it does
    not put any of them into either suite, and it cannot move the scoring path."""
    before = verifier_hash.compute_verifier_hash()
    assert before == verifier_hash.pinned_verifier_hash()
    S.validate_results(doc)
    for name in S.CANDIDATES:
        assert name not in frozen_problems.PROBLEMS, name
        assert name not in V.VALIDATION_NAMES, name
        assert name not in V.PROBLEMS, name
    assert verifier_hash.compute_verifier_hash() == before
    assert "rk_harness/stiffscreen.py" not in verifier_hash.VERIFIER_FILES


def test_sidetrack_mirror_is_untouched():
    """sidetrack.VALIDATION_NAMES is a hand-written mirror of the out-of-band suite
    and feeds sidetrack.code_hash(), which is the identity every measured
    side-track point carries. Adding a screened candidate to validation.py would
    force an edit here and re-open all of them, so this module stays outside the
    side-track file list entirely."""
    assert set(sidetrack.VALIDATION_NAMES) == set(V.VALIDATION_NAMES)
    assert set(sidetrack.STIFF_NAMES) == set(V.STIFF_NAMES)
    assert "rk_harness/stiffscreen.py" not in sidetrack.SIDETRACK_FILES
    assert sidetrack.code_hash() == sidetrack.code_hash()
