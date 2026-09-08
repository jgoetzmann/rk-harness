"""T13 - side-track executor (rk_harness/sidetrack.py) and the /work commit set.

Plan and acceptance criteria: docs/SIDETRACK-AUTOMATION.md. The invariants these
tests exist to hold are the ones in its section 9: side-track work is unpinned, it
never writes anything scored, it never fails a cycle, its artifacts are
reproducible, and with the feature off the run behaves exactly as before.

Names are prefixed ST because T9 already owns test_S*.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from rk_harness import runner, sidetrack, verifier_hash


# --------------------------------------------------------------------------- helpers

def _fake_job(name="fake.job", track="adaptive", keys=("a", "b"), boom=False):
    def plan():
        return [(k, {"k": k}) for k in keys]

    def run(params):
        if boom:
            raise RuntimeError("job exploded")
        return {"value": params["k"], "summary": {"k": params["k"]}}

    return sidetrack.Job(name, track, "a test question", plan, run)


@pytest.fixture()
def only_fake(monkeypatch):
    """Replace the catalogue with two cheap synthetic jobs, one per track."""
    jobs = (_fake_job("fake.adaptive", "adaptive", ("a1", "a2")),
            _fake_job("fake.implicit", "implicit", ("i1", "i2")))
    monkeypatch.setattr(sidetrack, "JOBS", jobs)
    monkeypatch.setattr(sidetrack, "JOBS_BY_NAME", {j.name: j for j in jobs})
    return jobs


# --------------------------------------------------------------------------- the plan

def test_ST1_plan_is_deterministic_finite_and_unique():
    first = [(p.job, p.key) for j in sidetrack.JOBS for p in j.points()]
    second = [(p.job, p.key) for j in sidetrack.JOBS for p in j.points()]
    assert first == second, "a plan must not depend on when it is asked for"
    assert len(first) == len(set(first)), "duplicate points would be measured twice"
    assert len(first) == 103, (
        "the catalogue in SIDETRACK-AUTOMATION.md section 4 is J1-J9 and J11, and the "
        "arithmetic at the end of that section is "
        "8 + 16 + 3 + 9 + 4 + 25 + 8 + 3 + 3 + 24 = 103. Moving this literal without "
        "moving that sentence is how the plan and its published description drift apart; "
        "test_ST22 carries the same total broken down per job.")
    assert {j.track for j in sidetrack.JOBS} == set(sidetrack.TRACKS)
    for job in sidetrack.JOBS:
        assert job.closes, f"{job.name} must name the design-doc question it closes"


def test_ST2_a_point_reproduces_byte_for_byte():
    """The cheapest real point, measured twice. Artifacts are pure functions of
    (code, params): no clock, no host detail, no unseeded randomness."""
    job = sidetrack.JOBS_BY_NAME["sdirk.gamma_dyadic_scan"]
    point = next(p for p in job.points() if p.key == "s04")
    sidetrack.run_point(point, ts="fixed")
    first = sidetrack.artifact_path(point.job, point.key).read_bytes()
    sidetrack.run_point(point, ts="fixed")
    assert sidetrack.artifact_path(point.job, point.key).read_bytes() == first


def test_ST3_names_match_the_validation_suite():
    """The catalogue hardcodes problem names so plan() stays cheap; this is the
    guard against them drifting away from the suite they refer to."""
    from rk_harness import validation as V

    assert set(sidetrack.VALIDATION_NAMES) == set(V.VALIDATION_NAMES)
    assert sidetrack.STIFF_NAMES == V.STIFF_NAMES


# --------------------------------------------------------------------------- scheduling

def test_ST4_budget_gates_starting_not_finishing(only_fake):
    """A zero budget still measures one point: a firing always makes progress, and
    the budget then stops it from starting another."""
    out = sidetrack.run_until(0.0)
    assert len(out["points"]) == 1
    assert out["exhausted"] is False


def test_ST5_tracks_alternate(only_fake):
    out = sidetrack.run_until(600.0)
    tracks = [p["track"] for p in out["points"]]
    assert len(tracks) == 4 and out["exhausted"] is True
    assert tracks.count("adaptive") == tracks.count("implicit") == 2
    for i in range(len(tracks) - 1):
        assert tracks[i] != tracks[i + 1], f"rotation broke at {tracks}"


def test_ST6_exhaustion_is_graceful(only_fake):
    sidetrack.run_until(600.0)
    seen: list[str] = []
    out = sidetrack.run_until(600.0, log=lambda kind, **d: seen.append(kind))
    assert out["exhausted"] is True and out["points"] == []
    assert seen.count("sidetrack_exhausted") == 1
    assert "sidetrack_started" not in seen


def test_ST7_stop_halts_a_firing(only_fake):
    out = sidetrack.run_until(600.0, stop=lambda: True)
    assert out["points"] == []


def test_ST8_measured_points_are_not_repeated(only_fake):
    sidetrack.run_until(600.0)
    assert sidetrack.remaining() == []
    assert sidetrack.next_point() is None


def test_ST9_a_code_change_reopens_points(only_fake, monkeypatch):
    """A ledger line counts only under the code hash that produced it, so editing a
    prototype re-opens its points instead of leaving stale numbers published."""
    sidetrack.run_until(600.0)
    assert sidetrack.remaining() == []
    monkeypatch.setattr(sidetrack, "code_hash", lambda: "0" * 16)
    assert len(sidetrack.remaining()) == 4


def test_ST10_tracks_can_be_selected(only_fake):
    out = sidetrack.run_until(600.0, tracks=("implicit",))
    assert {p["track"] for p in out["points"]} == {"implicit"}
    assert sidetrack.parse_tracks("off") == ()
    assert sidetrack.parse_tracks("both") == sidetrack.TRACKS
    assert sidetrack.parse_tracks("adaptive") == ("adaptive",)


# --------------------------------------------------------------------------- failure

def test_ST11_a_failing_job_never_reaches_the_caller(monkeypatch):
    job = _fake_job("fake.boom", "adaptive", ("x",), boom=True)
    monkeypatch.setattr(sidetrack, "JOBS", (job,))
    monkeypatch.setattr(sidetrack, "JOBS_BY_NAME", {job.name: job})
    seen: list[str] = []
    out = sidetrack.run_until(600.0, log=lambda kind, **d: seen.append(kind))
    # one attempt per point per firing: a failure does not mark the point measured,
    # so without that rule the firing would retry it until its budget ran out
    assert len(out["points"]) == 1 and out["points"][0]["status"] == "failed"
    assert seen.count("sidetrack_failed") == 1
    led = sidetrack.load_ledger()
    assert led[0]["status"] == "failed" and "job exploded" in led[0]["error"]
    assert not sidetrack.artifact_path("fake.boom", "x").exists()
    # the next firing does retry it, because a failure can be transient
    assert len(sidetrack.remaining()) == 1


def test_ST11b_a_point_that_keeps_failing_is_set_aside(monkeypatch):
    job = _fake_job("fake.boom", "adaptive", ("x",), boom=True)
    monkeypatch.setattr(sidetrack, "JOBS", (job,))
    monkeypatch.setattr(sidetrack, "JOBS_BY_NAME", {job.name: job})
    for _ in range(sidetrack.MAX_FAILURES_PER_POINT):
        assert len(sidetrack.remaining()) == 1
        sidetrack.run_until(600.0)
    assert sidetrack.remaining() == []
    assert sidetrack.status()["set_aside_total"] == 1
    # and a firing on an all-poisoned plan is a clean no-op
    out = sidetrack.run_until(600.0)
    assert out["points"] == [] and out["exhausted"] is True


# --------------------------------------------------------------------------- invariants

def test_ST12_side_track_code_is_not_pinned():
    for rel in sidetrack.SIDETRACK_FILES:
        assert rel not in verifier_hash.VERIFIER_FILES, (
            f"{rel} is in VERIFIER_FILES; side-track code must stay outside the "
            "frozen scoring path (SIDETRACK-AUTOMATION.md, invariant I1)")


def test_ST13_nothing_scored_is_written(only_fake, tmp_path):
    """The canary: after a full run of the plan, the verifier hash is untouched and
    no scored artefact exists in the work dir."""
    before = verifier_hash.compute_verifier_hash()
    sidetrack.run_until(600.0)
    assert verifier_hash.compute_verifier_hash() == before
    work = sidetrack.work_dir()
    for forbidden in ("archive", "quarantine", "hypotheses.jsonl", "falsification.json",
                      "RUNSTATE.json", "EPOCH_STATUS.json"):
        assert not (work / forbidden).exists(), f"side-track work created {forbidden}"
    assert sorted(p.name for p in (work / "sidetrack").iterdir()) == [
        "fake.adaptive", "fake.implicit", "ledger.jsonl"]


def test_ST14_artifacts_survive_json_round_trip(only_fake):
    sidetrack.run_until(600.0)
    for line in sidetrack.load_ledger():
        doc = json.loads((sidetrack.work_dir() / line["artifact"]).read_text(encoding="utf-8"))
        assert doc["job"] == line["job"] and doc["key"] == line["key"]
        assert doc["closes"]


# --------------------------------------------------------------------------- runner wiring

def test_ST15_disabled_by_default_is_inert(only_fake, monkeypatch):
    monkeypatch.delenv("RK_SIDETRACK_EVERY", raising=False)
    seen: list[str] = []
    monkeypatch.setattr(runner, "log_event", lambda kind, **d: seen.append(kind))
    for cycle in range(1, 12):
        runner._maybe_sidetrack(cycle)
    assert seen == []
    assert not (sidetrack.work_dir() / "sidetrack").exists()


def test_ST16_fires_only_on_the_configured_cadence(only_fake, monkeypatch):
    monkeypatch.setenv("RK_SIDETRACK_EVERY", "5")
    monkeypatch.setenv("RK_SIDETRACK_MAX_SECONDS", "30")
    fired: list[int] = []
    monkeypatch.setattr(runner, "log_event",
                        lambda kind, **d: fired.append(d.get("cycle_id")) if kind == "sidetrack_firing" else None)
    for cycle in range(1, 16):
        runner._maybe_sidetrack(cycle)
    assert fired == [5, 10, 15]


def test_ST17_stop_killfile_blocks_a_firing(only_fake, monkeypatch):
    monkeypatch.setenv("RK_SIDETRACK_EVERY", "1")
    work = sidetrack.work_dir()
    work.mkdir(parents=True, exist_ok=True)
    (work / "STOP").write_text("stop", encoding="utf-8")
    seen: list[str] = []
    monkeypatch.setattr(runner, "log_event", lambda kind, **d: seen.append(kind))
    runner._maybe_sidetrack(1)
    assert seen == ["sidetrack_skipped"]
    assert sidetrack.load_ledger() == []


def test_ST18_tracks_off_disables_firing(only_fake, monkeypatch):
    monkeypatch.setenv("RK_SIDETRACK_EVERY", "1")
    monkeypatch.setenv("RK_SIDETRACK_TRACKS", "off")
    seen: list[str] = []
    monkeypatch.setattr(runner, "log_event", lambda kind, **d: seen.append(kind))
    runner._maybe_sidetrack(1)
    assert seen == []


def test_ST19_max_seconds_is_clamped(monkeypatch):
    monkeypatch.setenv("RK_SIDETRACK_MAX_SECONDS", "5")
    assert runner._sidetrack_max_seconds() == 30.0
    monkeypatch.setenv("RK_SIDETRACK_MAX_SECONDS", "99999")
    assert runner._sidetrack_max_seconds() == 600.0
    monkeypatch.setenv("RK_SIDETRACK_MAX_SECONDS", "nonsense")
    assert runner._sidetrack_max_seconds() == 180.0


# --------------------------------------------------------------------------- the second catalogue

def test_ST22_catalogue_point_counts_are_declared():
    """The per-job breakdown of test_ST1's single total.

    A plan constant edited without intent moves one job's count and another's the
    other way, and a single total hides that. This is the place a deliberate change
    has to be written down.
    """
    counts = {job.name: len(job.points()) for job in sidetrack.JOBS}
    assert counts == {
        "adaptive.suite_sweep": 8,
        "adaptive.controller_gains": 16,
        "sdirk.stiff_suite": 3,
        "sdirk.gamma_dyadic_scan": 9,
        "sdirk.newton_iters": 4,
        "sdirk.gamma_a21_scan": 25,
        "adaptive.pair_census": 8,
        "sdirk.stiff_suite_budget": 3,
        "sdirk.jacobian_cost": 3,
        "adaptive.q15_estimate_floor": 24,
    }
    assert sum(counts.values()) == 103


def test_ST23_the_widened_scan_contains_the_narrow_family():
    """The J4 refactor must not have moved a published number.

    J4's nine measured points come from `order2_tableau_exact`, which is now a
    delegation to the two-parameter form. Two things have to hold: the wide gamma
    window covers the narrow one at the same denominator exponent, and the narrow
    construction is still a21 = 1 - gamma with b solved from the order conditions.
    """
    from fractions import Fraction

    from rk_harness.prototypes.sdirk import (
        GAMMA, dyadic_neighbours, dyadic_window, order2_tableau_exact,
        order2_tableau_exact_a21)

    narrow = dyadic_neighbours(GAMMA, 8, sidetrack.GAMMA_WIDTH)
    wide = dyadic_window(GAMMA, 8, sidetrack.WIDE_GAMMA_WIDTH, sidetrack.WIDE_FULL_BELOW)
    assert set(narrow) <= set(wide) and len(wide) > len(narrow)

    for gamma in narrow:
        a = order2_tableau_exact(gamma)
        b = order2_tableau_exact_a21(gamma, 1 - gamma)
        assert set(a) == set(b)
        for key in a:
            assert a[key] == b[key], key
        # and the construction itself, independent of the delegation
        b2 = (Fraction(1, 2) - gamma) / (1 - gamma)
        assert a["a21"] == 1 - gamma
        assert a["c"] == (gamma, Fraction(1))
        assert a["b"] == (1 - b2, b2)


def test_ST24_r_at_infinity_does_not_move_with_a21():
    """The invariance the J7 run function relies on, and reports as a result.

    With this construction the numerator of R(z) is (1, 1 - 2g, 1/2 - 2g + g^2)
    whatever a21 is, so the L-stability margin, A-stability and L-stability are
    functions of gamma alone. The gamma below is the one the stored J4 artifact
    reports |R(inf)| = -7/5625 for.
    """
    from fractions import Fraction

    from rk_harness.prototypes.sdirk import order2_tableau_exact_a21, stability_num

    gamma = Fraction(75, 256)
    ref = None
    for a21 in (Fraction(181, 256), Fraction(149, 256), Fraction(1, 4),
                Fraction(7, 8), Fraction(3, 2)):
        t = order2_tableau_exact_a21(gamma, a21)
        assert t["num"] == stability_num(gamma)
        got = (t["num"], t["den"], t["r_at_infinity"], t["l_stable"], t["a_stable"])
        if ref is None:
            ref = got
        assert got == ref, f"a21 = {a21} moved the stability data"
    assert ref[2] == Fraction(-7, 5625)


def test_ST25_the_census_caps_deterministically():
    """A space bigger than the cap is reported, not walked, and a walked space
    gives the same counts twice."""
    capped = sidetrack._run_pair_census({"stages": 4, "s_max": 3})
    assert capped["summary"]["status"] == "capped"
    assert capped["summary"]["space_size"] == 16777216
    assert capped["counts"] == {} and capped["fsal_examples"] == []

    # CENSUS_CAP also holds back (4, s_max 2): 262144 matrices measured at 83 s, which
    # is half a firing against the 31.8 s of the largest point measured before it.
    over = sidetrack._run_pair_census({"stages": 4, "s_max": 2})
    assert over["summary"]["status"] == "capped"
    assert over["summary"]["space_size"] == 262144
    assert sidetrack.CENSUS_CAP < 262144

    first = sidetrack._run_pair_census({"stages": 3, "s_max": 1})
    second = sidetrack._run_pair_census({"stages": 3, "s_max": 1})
    assert first["summary"]["status"] == "ok"
    assert first["counts"]["matrices"] == 64
    assert first["counts"] == second["counts"]


def test_ST26_budget_steps_match_the_published_validation_numbers():
    """J9's ladder is anchored on the same arithmetic that produced the published
    validation table, so the two cannot say different things about the same method."""
    from rk_harness import validation as V
    from rk_harness.costmodel import M0PLUS_FAST
    from rk_harness.simulate import steps_for_budget
    from rk_harness.tableau import classical

    assert sidetrack.BUDGET_CYCLES == V.BUDGET_CYCLES
    cl = classical()
    expected = {"euler": 4369, "heun2": 1680, "midpoint": 1985, "rk4": 661}
    for method, steps in expected.items():
        assert steps_for_budget(cl[method], M0PLUS_FAST, 3,
                                sidetrack.BUDGET_CYCLES) == steps, method


def test_ST27_status_reports_the_refill_figures(only_fake):
    """The catalogue exhausts, so 'how much is left' is the number that decides when
    to extend it. Before anything is measured there is no basis for a time estimate
    and status says so rather than guessing."""
    before = sidetrack.status()
    assert before["remaining_total"] == before["planned_total"] == 4
    assert before["estimated_seconds_remaining"] is None

    sidetrack.run_until(600.0)

    after = sidetrack.status()
    assert after["done_total"] == 4
    assert after["remaining_total"] == 0
    assert after["estimated_seconds_remaining"] == 0.0


def test_ST28_the_q15_floor_plan_is_twenty_four_points(only_fake):
    """One point per (validation problem, scale factor), with the scale in the key so
    the three scales of one problem are three separate measurements."""
    plan = sidetrack._plan_q15_estimate_floor()
    assert len(plan) == 24
    keys = [k for k, _ in plan]
    assert len(set(keys)) == 24
    assert sidetrack.Q15_SCALE_FACTORS == ((1, 1), (1, 2), (1, 4))
    for name in sidetrack.VALIDATION_NAMES:
        assert [k for k in keys if k.startswith(name + "_s")] == [
            f"{name}_s1", f"{name}_s2", f"{name}_s4"]
    for _key, params in plan:
        assert set(params) == {"problem", "scale_factor"}
        assert params["problem"] in sidetrack.VALIDATION_NAMES


# --------------------------------------------------------------------------- the commit set

def _git(work, *args):
    subprocess.run(["git", "-C", str(work)] + list(args), check=True,
                   capture_output=True, timeout=60)


def test_ST20_work_commit_covers_the_slow_moving_state(tmp_path, monkeypatch):
    """Before this, the runner staged completed archive files and nothing else, and
    the host watchdog only pushes commits it finds, so hypotheses, digests and
    interpretations never reached git without a human."""
    work = tmp_path / "work"
    (work / "archive").mkdir(parents=True)
    (work / "literature").mkdir()
    (work / "interpretation").mkdir()
    monkeypatch.setenv("RK_WORK_DIR", str(work))
    monkeypatch.setenv("RK_FINDINGS_DIR", str(tmp_path / "findings"))
    _git(work, "init", "-q")
    _git(work, "config", "user.email", "t@t")
    _git(work, "config", "user.name", "t")
    (work / ".gitignore").write_text("events.jsonl\nRUNSTATE.json\n", encoding="utf-8")
    (work / "archive" / "2020-01-01.jsonl").write_text("{}\n", encoding="utf-8")
    (work / "hypotheses.jsonl").write_text("{}\n", encoding="utf-8")
    (work / "LAST_DIRECTIVE.json").write_text("{}", encoding="utf-8")
    (work / "literature" / "digests.jsonl").write_text("{}\n", encoding="utf-8")
    (work / "interpretation" / "interpretations.jsonl").write_text("{}\n", encoding="utf-8")
    (work / "events.jsonl").write_text("{}\n", encoding="utf-8")
    (work / "RUNSTATE.json").write_text("{}", encoding="utf-8")
    (work / "sidetrack").mkdir()
    (work / "sidetrack" / "ledger.jsonl").write_text("{}\n", encoding="utf-8")

    runner._commit_outputs(7)

    out = subprocess.run(["git", "-C", str(work), "ls-files"], check=True,
                         capture_output=True, text=True, timeout=60).stdout.split()
    assert "hypotheses.jsonl" in out
    assert "LAST_DIRECTIVE.json" in out
    assert "literature/digests.jsonl" in out
    assert "interpretation/interpretations.jsonl" in out
    assert "sidetrack/ledger.jsonl" in out
    assert "archive/2020-01-01.jsonl" in out
    # still excluded: today's archive file is tens of MB and grows every cycle,
    # and the ignore list is still honoured
    assert "events.jsonl" not in out and "RUNSTATE.json" not in out


def test_ST21_todays_archive_file_is_still_left_alone(tmp_path, monkeypatch):
    from rk_harness import archive

    work = tmp_path / "work"
    (work / "archive").mkdir(parents=True)
    monkeypatch.setenv("RK_WORK_DIR", str(work))
    monkeypatch.setenv("RK_FINDINGS_DIR", str(tmp_path / "findings"))
    _git(work, "init", "-q")
    _git(work, "config", "user.email", "t@t")
    _git(work, "config", "user.name", "t")
    today = archive.today_path()
    today.parent.mkdir(parents=True, exist_ok=True)
    today.write_text("{}\n", encoding="utf-8")
    (work / "hypotheses.jsonl").write_text("{}\n", encoding="utf-8")

    runner._commit_outputs(7)

    out = subprocess.run(["git", "-C", str(work), "ls-files"], check=True,
                         capture_output=True, text=True, timeout=60).stdout.split()
    assert "hypotheses.jsonl" in out
    assert f"archive/{today.name}" not in out
