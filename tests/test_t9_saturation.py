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
