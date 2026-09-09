"""T16 tests: the lane rotation wired into the cycle loop (B9).

Same tier as tests/test_t16_lanes.py and tests/test_t16_lanesearch.py, and a third
file because this one imports the runner and those two deliberately do not.

The property these tests exist for is that the wiring is INVISIBLE under the schedule
the run ships with. 'E' means one explicit search every cycle, which is what the run
has always done, and every test below that asserts today's behaviour asserts it
against the same code path an armed schedule would take.

What a lane cycle must not do is as important as what it does: no encourager, no LLM,
no enumeration, no evaluation, no verification, and nothing appended to the scored
archive. A lane record is not a cell and cannot be ranked against one.
"""
from __future__ import annotations

import json

import pytest

from rk_harness import lanes, runner
from rk_harness.paths import work_dir
from rk_harness.types import ArchiveState, RunState

CLOCK = "2026-09-09T12:00:00Z"


def _env(monkeypatch, tmp_path, schedule=None):
    work = tmp_path / "work"
    findings = tmp_path / "findings"
    work.mkdir(parents=True, exist_ok=True)
    findings.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("RK_WORK_DIR", str(work))
    monkeypatch.setenv("RK_FINDINGS_DIR", str(findings))
    monkeypatch.setenv("RK_SITE", "off")
    monkeypatch.setenv("RK_LLM", "off")
    monkeypatch.setenv("RK_CLOCK", CLOCK)
    monkeypatch.delenv("RK_GIT_COMMIT", raising=False)
    if schedule is None:
        monkeypatch.delenv("RK_LANE_SCHEDULE", raising=False)
    else:
        monkeypatch.setenv("RK_LANE_SCHEDULE", schedule)
    assert work_dir() == work
    return work


def _state(**over) -> RunState:
    st = dict(cycle_id=10, phase=2, started_at=CLOCK, last_heartbeat=CLOCK,
              spend_usd=1.25, stall_counter=7, current_cell=(3, 4))
    st.update(over)
    return RunState(**st)


def _arch() -> ArchiveState:
    return ArchiveState(n_records=5, last_cycle_id=10, grids={1: {}, 2: {}, 3: {}, 4: {}},
                        open_hypotheses=(), refuted_hypotheses=())


def _events(work) -> list[dict]:
    path = work / "events.jsonl"
    if not path.is_file():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _rows(work) -> list[dict]:
    path = work / "schedule" / "cycles.jsonl"
    if not path.is_file():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


# --------------------------------------------------------------------------- disarmed

def test_the_shipped_schedule_puts_every_cycle_on_the_explicit_lane(monkeypatch, tmp_path):
    """'E' is the default and the default must be today. Checked over a full turn of
    the longest schedule this test file uses, so a modulo error cannot hide in it."""
    _env(monkeypatch, tmp_path)
    assert lanes.lane_schedule() == "E"
    assert {lanes.lane_for(c, lanes.lane_schedule()) for c in range(1, 61)} == {"explicit"}


def test_an_unset_variable_is_the_shipped_schedule_and_not_an_error(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    monkeypatch.delenv("RK_LANE_SCHEDULE", raising=False)
    assert lanes.lane_schedule() == "E"


def test_a_typo_falls_back_to_explicit_rather_than_arming_something(monkeypatch, tmp_path):
    """The safe direction. A schedule is the thing that arms, so an unreadable one has
    to mean 'do what you have always done', never 'guess'."""
    _env(monkeypatch, tmp_path, schedule="EAX")
    assert lanes.schedule_problem("EAX") is not None
    assert lanes.lane_schedule() == "E"
    assert lanes.lane_for(1, lanes.lane_schedule()) == "explicit"


# --------------------------------------------------------------------------- armed

def test_thirds_give_the_three_classes_equal_turns(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, schedule="EAI")
    got = [lanes.lane_for(c, lanes.lane_schedule()) for c in range(1, 10)]
    assert got == ["adaptive", "implicit", "explicit"] * 3
    shares = lanes.scheduled_shares("EAI")
    assert shares == {"explicit": pytest.approx(1 / 3), "adaptive": pytest.approx(1 / 3),
                      "implicit": pytest.approx(1 / 3)}


# --------------------------------------------------------------------------- the lane cycle

def _stub_step(monkeypatch, records=2, raises=None, capture=None):
    def fake_step(lane, seed=0, budget_seconds=0.0, cycle=0, **kw):
        if capture is not None:
            capture.append({"lane": lane, "seed": seed, "budget": budget_seconds,
                            "cycle": cycle})
        if raises is not None:
            raise raises
        return [{"lane": lane, "index": i} for i in range(records)]
    monkeypatch.setattr(runner.lanesearch, "step", fake_step)


def test_a_lane_cycle_measures_its_lane_and_appends_nothing_scored(monkeypatch, tmp_path):
    work = _env(monkeypatch, tmp_path, schedule="EAI")
    seen = []
    _stub_step(monkeypatch, records=3, capture=seen)
    out = runner._run_lane_cycle(_state(), 11, _arch(), "adaptive", "EAI", 0.0)
    assert seen == [{"lane": "adaptive", "seed": 11, "budget": lanes.lane_max_seconds(),
                     "cycle": 11}]
    assert out.cycle_id == 11
    assert not (work / "archive").exists(), "a lane cycle wrote to the scored archive"
    done = [e for e in _events(work) if e["kind"] == "cycle_done"]
    assert len(done) == 1
    assert done[0]["lane"] == "adaptive" and done[0]["lane_records"] == 3
    assert done[0]["accepted"] == 0 and done[0]["improved"] is False


def test_a_lane_cycle_leaves_the_stall_counter_exactly_where_it_found_it(monkeypatch, tmp_path):
    """It counts cycles since the EXPLICIT search last improved a cell. A lane cycle
    neither advances it (the explicit search did not fail, it did not run) nor clears
    it (nothing improved). Advancing it is how a rotation manufactures a false stall."""
    _env(monkeypatch, tmp_path, schedule="EAI")
    _stub_step(monkeypatch)
    out = runner._run_lane_cycle(_state(stall_counter=7), 11, _arch(), "implicit", "EAI", 0.0)
    assert out.stall_counter == 7


def test_a_lane_cycle_carries_phase_spend_and_cell_through_untouched(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, schedule="EAI")
    _stub_step(monkeypatch)
    st = _state(phase=3, spend_usd=4.5, current_cell=(2, 5))
    out = runner._run_lane_cycle(st, 11, _arch(), "adaptive", "EAI", 0.0)
    assert (out.phase, out.spend_usd, out.current_cell) == (3, 4.5, (2, 5))


def test_a_failing_lane_does_not_end_the_run(monkeypatch, tmp_path):
    """A lane is off the scored path. Nothing it can do is worth losing a cycle over,
    let alone the process."""
    work = _env(monkeypatch, tmp_path, schedule="EAI")
    _stub_step(monkeypatch, raises=RuntimeError("the lane exploded"))
    out = runner._run_lane_cycle(_state(), 11, _arch(), "adaptive", "EAI", 0.0)
    assert out.cycle_id == 11
    kinds = [e["kind"] for e in _events(work)]
    assert "lane_cycle_failed" in kinds
    assert "cycle_done" in kinds
    failed = [e for e in _events(work) if e["kind"] == "lane_cycle_failed"][0]
    assert "the lane exploded" in failed["error"]


def test_the_lane_budget_comes_from_config_and_is_clamped(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, schedule="EAI")
    monkeypatch.setenv("RK_LANE_MAX_SECONDS", "240")
    assert runner._lane_budget_seconds() == 240.0
    monkeypatch.setenv("RK_LANE_MAX_SECONDS", "99999")
    assert runner._lane_budget_seconds() == lanes.LANE_MAX_SECONDS_CEILING
    monkeypatch.setenv("RK_LANE_MAX_SECONDS", "rubbish")
    assert runner._lane_budget_seconds() == lanes.LANE_MAX_SECONDS_DEFAULT


# --------------------------------------------------------------------------- the cycle log

def test_every_cycle_writes_a_row_explicit_included(monkeypatch, tmp_path):
    """A log that recorded only the lanes would leave the explicit share unmeasurable,
    and an unmeasurable share is how the intended split and the real one drifted to
    0.0005 percent with nothing in a position to notice."""
    work = _env(monkeypatch, tmp_path, schedule="EAI")
    runner._record_cycle(11, "explicit", "EAI", 176.0, None, records_appended=28)
    runner._record_cycle(12, "adaptive", "EAI", 190.0, 120.0, lane_records=3)
    rows = _rows(work)
    assert [r["lane"] for r in rows] == ["explicit", "adaptive"]
    assert rows[0]["records_appended"] == 28 and rows[0]["lane_records_appended"] == 0
    assert rows[1]["records_appended"] == 0 and rows[1]["lane_records_appended"] == 3
    assert rows[1]["productive_seconds"] == 120.0
    assert all(lanes.row_problem(r) is None for r in rows)


def test_the_shares_document_is_refreshed_and_reports_what_happened(monkeypatch, tmp_path):
    work = _env(monkeypatch, tmp_path, schedule="EAI")
    for c, lane in enumerate(("explicit", "adaptive", "implicit"), start=11):
        runner._record_cycle(c, lane, "EAI", 60.0, None)
    doc = json.loads((work / "schedule" / "shares.json").read_text(encoding="utf-8"))
    assert doc["_meta"]["schema"] == lanes.SHARES_SCHEMA
    for lane in lanes.LANES:
        assert doc["lanes"][lane]["cycles"] == 1
        assert doc["lanes"][lane]["share"] == pytest.approx(1 / 3)


def test_a_lane_cycle_stamps_the_lane_code_hash_and_an_explicit_one_does_not(
        monkeypatch, tmp_path):
    """The digest says which code measured the lane's records. An explicit cycle has
    no lane records, so stamping one would name a code that produced nothing here."""
    work = _env(monkeypatch, tmp_path, schedule="EAI")
    runner._record_cycle(11, "adaptive", "EAI", 60.0, 30.0, lane_records=1)
    runner._record_cycle(12, "explicit", "EAI", 60.0, None, records_appended=1)
    rows = _rows(work)
    assert rows[0]["code_hash"] == runner.lanesearch.code_hash()
    assert rows[1]["code_hash"] is None


def test_a_broken_cycle_log_cannot_end_a_cycle_that_did_its_work(monkeypatch, tmp_path):
    """Reporting is downstream of the work. If it throws, the work still happened."""
    work = _env(monkeypatch, tmp_path, schedule="EAI")

    def boom(*a, **kw):
        raise OSError("the disk is full")
    monkeypatch.setattr(runner.lanes, "append_cycle", boom)
    runner._record_cycle(11, "explicit", "EAI", 60.0, None)
    kinds = [e["kind"] for e in _events(work)]
    assert "lane_log_failed" in kinds
    assert not (work / "schedule" / "cycles.jsonl").exists()


def test_a_broken_shares_write_is_logged_and_leaves_the_row_standing(monkeypatch, tmp_path):
    work = _env(monkeypatch, tmp_path, schedule="EAI")

    def boom(*a, **kw):
        raise ValueError("schema drift")
    monkeypatch.setattr(runner.lanes, "update_shares", boom)
    runner._record_cycle(11, "explicit", "EAI", 60.0, None)
    assert [r["cycle"] for r in _rows(work)] == [11]
    assert "lane_shares_failed" in [e["kind"] for e in _events(work)]


# --------------------------------------------------------------------------- the boundary

def test_the_lane_cycle_never_reaches_the_encourager_or_the_model(monkeypatch, tmp_path):
    """Read as a list of what a lane record is not. It is not a cell, so the encourager
    has nothing to say about it; it is not a target order and stage count, so a
    directive cannot describe it; and it never goes through the pinned checker."""
    _env(monkeypatch, tmp_path, schedule="EAI")
    _stub_step(monkeypatch)
    called = []
    for mod, name in ((runner.encourager, "next_action"), (runner, "call_llm"),
                      (runner, "seed_baselines"), (runner.enumeration, "enumerate_phase")):
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name,
                                lambda *a, _n=name, **kw: called.append(_n) or (_ for _ in ()).throw(
                                    AssertionError(f"a lane cycle called {_n}")))
    runner._run_lane_cycle(_state(), 11, _arch(), "adaptive", "EAI", 0.0)
    assert called == []


def test_the_lane_is_resolved_once_per_cycle_from_one_read(monkeypatch, tmp_path):
    """Source-level, because the failure it prevents is invisible at runtime: two reads
    of the variable can straddle a config change and put an event line and the work it
    describes on different lanes."""
    src = (runner.__file__ and open(runner.__file__, encoding="utf-8").read()) or ""
    body = src.split("def _run_cycle(state: RunState) -> RunState:", 1)[1]
    assert body.count("lanes.lane_schedule()") == 1
    assert body.count("lanes.lane_for(") == 1
