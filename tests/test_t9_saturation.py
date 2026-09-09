"""T9 — epoch saturation orchestrator (rk_harness/saturation.py)."""
from __future__ import annotations

import json

import pytest

from rk_harness import saturation


def _write_events(work, events):
    (work / "events.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")


def _accepted(ts, order=2, stages=2, bucket=0, tier="unreplicated", new_elite=False):
    return {"ts": ts, "kind": "accepted", "order": order, "stages": stages,
            "bucket": bucket, "tier": tier, "new_elite": new_elite,
            "heldout_error": 0.1, "tableau_hash": "x"}


@pytest.fixture()
def work(tmp_path, monkeypatch):
    monkeypatch.setenv("RK_WORK_DIR", str(tmp_path))
    monkeypatch.setenv("RK_SAT_WINDOW_H", "48")
    monkeypatch.setenv("RK_SAT_CONSECUTIVE", "3")
    monkeypatch.setenv("RK_CLOCK", "2026-09-02T12:00:00Z")
    return tmp_path


def test_S1_recent_progress_is_continue(work):
    _write_events(work, [_accepted("2026-09-02T10:00:00Z")])   # new cell 2 h ago
    (work / "falsification.json").write_text("{}", encoding="utf-8")
    out = saturation.assess()
    assert out["verdict"] == "CONTINUE"
    assert out["last_progress_kind"] == "new_cell"
    assert out["hours_since_progress"] == 2.0


def test_S2_stale_progress_without_falsification_is_continue(work):
    _write_events(work, [_accepted("2026-08-25T10:00:00Z")])
    out = saturation.assess()
    assert out["verdict"] == "CONTINUE"
    assert out["falsification_present"] is False


def test_S3_stale_progress_with_falsification_is_saturating(work):
    _write_events(work, [_accepted("2026-08-25T10:00:00Z")])
    (work / "falsification.json").write_text("{}", encoding="utf-8")
    out = saturation.assess()
    assert out["verdict"] == "SATURATING"
    assert out["action"] == "none"          # assess never escalates


def test_S4_progress_kinds_new_elite_and_verified_reset_the_clock(work):
    _write_events(work, [
        _accepted("2026-08-20T00:00:00Z"),                                   # cell fill, old
        _accepted("2026-08-25T00:00:00Z", new_elite=True),                   # improvement
        _accepted("2026-09-02T00:00:00Z", tier="heldout_verified"),          # verified, 12 h ago
    ])
    (work / "falsification.json").write_text("{}", encoding="utf-8")
    out = saturation.assess()
    assert out["verdict"] == "CONTINUE"
    assert out["last_progress_kind"] == "heldout_verified"


def test_S5_check_escalates_only_after_consecutive_threshold(work):
    _write_events(work, [_accepted("2026-08-25T10:00:00Z")])
    (work / "falsification.json").write_text("{}", encoding="utf-8")
    first = saturation.check()
    second = saturation.check()
    assert (first["consecutive"], second["consecutive"]) == (1, 2)
    assert first["action"] == second["action"] == "none"
    third = saturation.check()
    assert third["consecutive"] == 3
    assert third["verdict"] == "FREEZE" and third["action"] == "freeze"


def test_S6_progress_resets_the_consecutive_counter(work, monkeypatch):
    _write_events(work, [_accepted("2026-08-25T10:00:00Z")])
    (work / "falsification.json").write_text("{}", encoding="utf-8")
    saturation.check()
    saturation.check()
    # a fresh cell fill arrives
    _write_events(work, [_accepted("2026-08-25T10:00:00Z"),
                         _accepted("2026-09-02T11:00:00Z", order=3)])
    out = saturation.check()
    assert out["verdict"] == "CONTINUE" and out["consecutive"] == 0


def test_S7_mark_frozen_writes_epoch_status_and_clears_stop(work):
    _write_events(work, [_accepted("2026-08-25T10:00:00Z")])
    (work / "STOP").write_text("stop", encoding="ascii")
    status = saturation.mark_frozen("test freeze")
    assert status["epoch"] == 1 and status["reason"] == "test freeze"
    assert (work / "EPOCH_STATUS.json").exists()
    assert not (work / "STOP").exists()
    # every later check short-circuits and never re-escalates
    out = saturation.check()
    assert out["verdict"] == "FROZEN" and out["action"] == "none"


def test_S8_no_events_file_is_continue_not_crash(work):
    out = saturation.assess()
    assert out["verdict"] == "CONTINUE"
    assert out["last_progress_ts"] is None


# ---------------------------------------------------------------------------------------
# S9-S15: the window is measured in the lane the rule is about
#
# The 48-hour rule was written when every cycle was an explicit search, so wall clock and
# search time were one quantity. Under a rotation they are not, and a window still counted
# in wall clock would call the epoch saturated after a third of the evidence while the
# machine did exactly what it was told.
# ---------------------------------------------------------------------------------------

def _write_cycles(work, rows):
    d = work / "schedule"
    d.mkdir(parents=True, exist_ok=True)
    (d / "cycles.jsonl").write_text(
        "".join(json.dumps(r) + chr(10) for r in rows), encoding="utf-8")


def _cycle(ts, lane="explicit", seconds=180.0, cycle=1):
    return {"cycle": cycle, "lane": lane, "ts": ts, "seconds": seconds,
            "productive_seconds": seconds, "records_appended": 0, "schedule": "EAI"}


def test_S9_with_no_lane_log_the_measure_is_wall_clock_and_says_so(work):
    """Today's state, and the state the run ships in. Behaviour must not move."""
    _write_events(work, [_accepted("2026-08-31T10:00:00Z")])   # 50 h before RK_CLOCK
    (work / "falsification.json").write_text("{}", encoding="utf-8")
    out = saturation.assess()
    assert out["verdict"] == "SATURATING"
    assert out["hours_since_progress"] == 50.0
    assert out["search_hours_since_progress"] is None
    assert out["window_basis"] == (
        "wall clock; no lane log, so every cycle is an explicit search")
    assert out["window_basis_complete"] is True


def test_S10_a_rotation_stops_the_wall_clock_from_freezing_a_working_epoch(work):
    """The defect this closes. 50 wall-clock hours, of which the explicit lane ran 16.

    Wall clock alone calls that saturated. It is not: the lane has had a third of the
    machine and a third of the search, and the rule asks for 48 hours of SEARCH.
    """
    _write_events(work, [_accepted("2026-08-31T10:00:00Z")])
    (work / "falsification.json").write_text("{}", encoding="utf-8")
    rows = [_cycle("2026-08-31T09:00:00Z", cycle=0)]          # before the progress event
    for i in range(48):                                        # 16 h explicit, 32 h other
        rows.append(_cycle(f"2026-09-01T{i // 3:02d}:00:00Z",
                           lane=("explicit", "adaptive", "implicit")[i % 3],
                           seconds=3600.0, cycle=i + 1))
    _write_cycles(work, rows)
    out = saturation.assess()
    assert out["hours_since_progress"] == 50.0                 # wall clock unchanged
    assert out["search_hours_since_progress"] == 16.0          # what the lane actually ran
    assert out["verdict"] == "CONTINUE"
    assert "explicit-lane seconds from cycles.jsonl" in out["window_basis"]
    assert out["window_basis_complete"] is True


def test_S11_the_lane_measure_still_freezes_a_genuinely_saturated_epoch(work):
    """The rule must keep its teeth: 49 hours of explicit search and no progress."""
    _write_events(work, [_accepted("2026-08-31T10:00:00Z")])
    (work / "falsification.json").write_text("{}", encoding="utf-8")
    rows = [_cycle("2026-08-31T09:00:00Z", cycle=0)]
    rows += [_cycle("2026-09-01T01:00:00Z", seconds=3600.0 * 49, cycle=1)]
    _write_cycles(work, rows)
    out = saturation.assess()
    assert out["search_hours_since_progress"] == 49.0
    assert out["verdict"] == "SATURATING"


def test_S12_only_explicit_cycles_count_toward_the_search_window(work):
    _write_events(work, [_accepted("2026-08-31T10:00:00Z")])
    (work / "falsification.json").write_text("{}", encoding="utf-8")
    rows = [_cycle("2026-08-31T09:00:00Z", cycle=0)]
    rows += [_cycle("2026-09-01T01:00:00Z", lane=lane, seconds=3600.0 * 60, cycle=i)
             for i, lane in enumerate(("adaptive", "implicit"), start=1)]
    _write_cycles(work, rows)
    out = saturation.assess()
    assert out["search_hours_since_progress"] == 0.0
    assert out["verdict"] == "CONTINUE"       # 120 h of OTHER lanes proves nothing here


def test_S13_cycles_at_or_before_the_progress_event_are_not_counted(work):
    _write_events(work, [_accepted("2026-09-01T10:00:00Z")])
    (work / "falsification.json").write_text("{}", encoding="utf-8")
    _write_cycles(work, [_cycle("2026-08-20T00:00:00Z", seconds=3600.0 * 100, cycle=0),
                         _cycle("2026-09-01T10:00:00Z", seconds=3600.0 * 100, cycle=1),
                         _cycle("2026-09-01T11:00:00Z", seconds=3600.0 * 2, cycle=2)])
    out = saturation.assess()
    assert out["search_hours_since_progress"] == 2.0


def test_S14_a_tail_that_does_not_reach_back_reports_a_floor_and_never_freezes_early(work):
    """An undercount can only delay a freeze. The basis says which direction it errs."""
    _write_events(work, [_accepted("2026-08-01T00:00:00Z")])
    (work / "falsification.json").write_text("{}", encoding="utf-8")
    _write_cycles(work, [_cycle("2026-09-01T00:00:00Z", seconds=3600.0 * 10, cycle=1)])
    out = saturation.assess()
    assert out["window_basis_complete"] is False
    assert "this is a floor" in out["window_basis"]
    assert out["search_hours_since_progress"] == 10.0
    assert out["verdict"] == "CONTINUE"        # 768 wall hours, but only 10 searched


def test_S15_a_damaged_lane_log_falls_back_and_never_averages_rubbish_in(work):
    _write_events(work, [_accepted("2026-08-31T10:00:00Z")])
    (work / "falsification.json").write_text("{}", encoding="utf-8")
    d = work / "schedule"
    d.mkdir(parents=True, exist_ok=True)
    (d / "cycles.jsonl").write_text("not json" + chr(10) + "{]" + chr(10), encoding="utf-8")
    out = saturation.assess()
    assert out["search_hours_since_progress"] is None
    assert out["window_basis"] == "wall clock; the lane log records no usable cycle"
    assert out["verdict"] == "SATURATING"      # falls back to the 50 wall-clock hours


def test_S16_a_row_with_a_non_numeric_duration_is_discarded_not_counted_as_zero(work):
    _write_events(work, [_accepted("2026-08-31T10:00:00Z")])
    (work / "falsification.json").write_text("{}", encoding="utf-8")
    _write_cycles(work, [_cycle("2026-08-31T09:00:00Z", cycle=0),
                         dict(_cycle("2026-09-01T01:00:00Z", cycle=1), seconds="lots"),
                         _cycle("2026-09-01T02:00:00Z", seconds=3600.0 * 5, cycle=2)])
    out = saturation.assess()
    assert out["search_hours_since_progress"] == 5.0
