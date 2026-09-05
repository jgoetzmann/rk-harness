"""T5: the host status snapshot (rk_harness.status).

Named test_t5_* so the existing CI shard glob (tests/test_t5_*.py) picks it up without
touching the workflow, and so generate._SUITE_DESC keeps describing it as the operational
tier it belongs to.

The theme of these tests is that the file must never state something false. Every probe is
injectable, so nothing here touches Docker, nvidia-smi or the host counters: the whole
point is to pin the behaviour when those are broken, which is exactly when a real run
cannot exercise it.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from rk_harness import status


def _work(tmp_path: Path, **files) -> Path:
    w = tmp_path / "work"
    w.mkdir(exist_ok=True)
    for name, body in files.items():
        p = w / name.replace("__", "/")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
    return w


def _iso(minutes_ago: float) -> str:
    t = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes_ago)
    return t.replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------- timestamps and sentinels

def test_S1_docker_zero_time_is_absent_not_year_one():
    """Docker's 'never' value is year 0001; printed literally it becomes a real-looking
    date and an uptime of about two thousand years."""
    assert status._parse_ts("0001-01-01T00:00:00Z") is None
    assert status._parse_ts("2026-09-05T17:00:00Z") is not None
    # both encodings the run actually writes
    assert status._parse_ts("2026-09-05T17:00:00+00:00") is not None
    assert status._parse_ts(None) is None
    assert status._parse_ts("not a date") is None


def test_S2_durations_read_as_english():
    assert status._dur(None) == "unknown"
    assert status._dur(12) == "12s"
    assert status._dur(461) == "7m 41s"
    assert status._dur(3 * 3600 + 200) == "3h 3m"
    assert status._dur(2 * 86400 + 3 * 3600) == "2d 3h 0m"


# ---------------------------------------------------------------- the verdict

@pytest.mark.parametrize("state,hb,stop,frozen,want", [
    ("RUNNING", 10, False, False, "RUNNING"),
    ("RUNNING", 9999, False, False, "RUNNING_STALE"),
    ("RUNNING", 10, True, False, "STOPPING"),
    ("PAUSED", 9999, False, False, "PAUSED"),          # a paused container cannot beat
    ("EXITED", None, False, False, "EXITED"),
    ("ABSENT", None, False, False, "ABSENT"),
    ("RESTARTING", None, False, False, "RESTARTING"),
    ("DEAD", None, False, False, "DEAD"),
    ("RUNNING", 10, False, True, "FROZEN"),
])
def test_S3_verdicts_cover_the_states_the_run_can_reach(state, hb, stop, frozen, want):
    assert status.decide({"state": state}, hb, stop, frozen) == want


@pytest.mark.parametrize("state", ["DOCKER_UNREACHABLE", "DOCKER_ERROR", "DOCKER_ABSENT"])
def test_S4_a_silent_daemon_never_becomes_not_running(state):
    """The dangerous failure: reporting NOT RUNNING because Docker did not answer.

    That reads as 'start it', and start.ps1 would recreate a container that is alive. A
    daemon that times out must say so and claim nothing about the run - even with a very
    stale heartbeat and a STOP file present, which is the most tempting evidence.
    """
    v = status.decide({"state": state}, 99999, True, False)
    assert v == state
    code, text = status._VERDICT_TEXT[v]
    assert "CANNOT TELL" in text
    assert "NOT RUNNING" not in text


def test_S5_every_verdict_has_display_text():
    """A verdict with no entry renders as the UNKNOWN fallback, silently."""
    reachable = {"RUNNING", "RUNNING_STALE", "PAUSED", "RESTARTING", "CREATED", "EXITED",
                 "DEAD", "REMOVING", "ABSENT", "STOPPING", "FROZEN",
                 "DOCKER_UNREACHABLE", "DOCKER_ERROR", "DOCKER_ABSENT", "UNKNOWN"}
    assert reachable <= set(status._VERDICT_TEXT)
    for code, text in status._VERDICT_TEXT.values():
        assert code in ("OK", "WARN", "STOP")
        assert text and text[0].isupper()


# ---------------------------------------------------------------- cadence from the tail

def test_S6_cadence_reads_only_the_tail_of_a_large_events_file(tmp_path):
    """events.jsonl has no rotation and is already 35 MB; a full scan gets slower forever."""
    w = tmp_path / "work"
    w.mkdir()
    ev = w / "events.jsonl"
    filler = json.dumps({"ts": "2026-09-05T00:00:00Z", "kind": "noise", "pad": "x" * 400})
    with open(ev, "w", encoding="utf-8") as fh:
        for _ in range(4000):                                # comfortably over the tail cap
            fh.write(filler + "\n")
        for i in range(5):
            fh.write(json.dumps({"ts": f"2026-09-05T12:{i * 8:02d}:00Z",
                                 "kind": "cycle_done", "cycle_id": i}) + "\n")
    assert ev.stat().st_size > status.EVENTS_TAIL_BYTES
    events, err = status.tail_events(ev)
    assert err is None
    assert len(events) < 4005                                # did not read the whole file
    cad = status.cycle_cadence(events)
    assert cad["samples"] == 4
    assert cad["median_s"] == 480.0                          # 8 minutes between cycles


def test_S7_cadence_is_silent_rather_than_wrong_with_too_little_data(tmp_path):
    w = tmp_path / "work"
    w.mkdir()
    (w / "events.jsonl").write_text(
        json.dumps({"ts": "2026-09-05T12:00:00Z", "kind": "cycle_done"}) + "\n", encoding="utf-8")
    events, _ = status.tail_events(w / "events.jsonl")
    cad = status.cycle_cadence(events)
    assert cad["samples"] == 0 and "median_s" not in cad
    assert cad["last"] is not None


def test_S8_a_missing_events_file_is_a_problem_not_a_crash(tmp_path):
    events, err = status.tail_events(tmp_path / "nope.jsonl")
    assert events == [] and "absent" in err


# ---------------------------------------------------------------- collect and render

def _collect(work, **kw):
    kw.setdefault("with_host", False)
    kw.setdefault("with_gpu", False)
    kw.setdefault("with_docker", False)
    return status.collect(work=work, findings=work / "nowhere", **kw)


def test_S9_collect_survives_a_completely_empty_work_dir(tmp_path):
    doc = _collect(_work(tmp_path))
    assert doc["verdict"] in status._VERDICT_TEXT
    assert doc["problems"], "an empty work dir should be reported, not rendered as healthy"
    body = status.render_text(doc)
    assert "PROBLEMS READING STATE" in body
    assert "HEARTBEAT is absent" in body


def test_S10_collect_reads_the_real_state_files(tmp_path):
    w = _work(
        tmp_path,
        **{"RUNSTATE.json": {"cycle_id": 1146, "phase": 3, "stall_counter": 106,
                             "current_cell": [6, 3]},
           "HEARTBEAT": _iso(2),
           "saturation_state.json": {"consecutive": 0, "last_verdict": "CONTINUE",
                                     "last_check": _iso(30)},
           "LAST_DIRECTIVE.json": {"directive_id": "D-1145", "target_order": 4,
                                   "stages": [4, 6]},
           "events.jsonl": "".join(
               json.dumps({"ts": f"2026-09-05T1{h}:00:00Z", "kind": "cycle_done"}) + "\n"
               for h in range(4))})
    doc = _collect(w)
    assert doc["runstate"]["cycle_id"] == 1146
    assert doc["heartbeat"]["age_s"] < 300
    assert doc["saturation"]["last_verdict"] == "CONTINUE"
    assert doc["directive"]["directive_id"] == "D-1145"
    body = status.render_text(doc)
    for needle in ("1146", "phase 3", "106 cycles with no new elite", "CONTINUE", "D-1145"):
        assert needle in body, needle


def test_S11_the_rendered_file_is_ascii_and_fits_a_notepad_window(tmp_path):
    """PowerShell 5.1 and Notepad both have to cope, and the repo is ASCII-only."""
    doc = _collect(_work(tmp_path, **{"HEARTBEAT": _iso(1)}))
    body = status.render_text(doc, refresh_s=20)
    body.encode("ascii")                                     # raises if anything crept in
    assert "\r\n" in body
    overlong = [ln for ln in body.split("\r\n") if len(ln) > 90]
    assert not overlong, overlong[:3]


def test_S12_a_stale_file_declares_its_own_deadline(tmp_path):
    """The whole staleness contract: a reader must be able to tell without arithmetic."""
    doc = _collect(_work(tmp_path))
    looped = status.render_text(doc, refresh_s=20)
    assert "stale after" in looped
    assert "nothing is updating this file" in looped
    once = status.render_text(doc, refresh_s=None)
    assert "stale after" not in once
    assert "written once by hand" in once


def test_S13_the_deadline_is_in_this_machines_timezone_not_the_harnesss(tmp_path):
    """The harness reports US Central, but the deadline is compared against the reader's
    own taskbar clock, and this machine is not on Central time."""
    doc = _collect(_work(tmp_path))
    body = status.render_text(doc, refresh_s=20)
    now = status._parse_ts(doc["written_at"])
    _wall, tz = status._local(now)
    assert "this machine's own clock" in body
    assert tz in body
    assert "US Central, the harness convention" in body


def test_S14_never_states_the_container_is_down_when_docker_is_the_thing_that_is_down(tmp_path):
    w = _work(tmp_path, **{"HEARTBEAT": _iso(600)})          # ten hours stale
    doc = _collect(w)
    doc["docker"] = {"state": "DOCKER_UNREACHABLE", "error": "timed out after 6s",
                     "latency_ms": 6001}
    doc["verdict"] = status.decide(doc["docker"], 36000, False, False)
    body = status.render_text(doc)
    assert "CANNOT TELL" in body
    assert "NOT RUNNING" not in body
    assert "may be perfectly healthy" in body


def test_S15_exit_code_is_shown_only_once_the_container_has_stopped(tmp_path):
    """State.ExitCode is 0 on a running container; printed there it reads as a clean exit
    that never happened."""
    doc = _collect(_work(tmp_path))
    doc["docker"] = {"ok": True, "state": "RUNNING", "status": "running",
                     "started_at": _iso(60), "image": "rk-harness:latest",
                     "restart_policy": "on-failure:5", "restart_count": 0}
    doc["verdict"] = "RUNNING"
    assert "exit" not in status.render_text(doc).split("WHAT IT IS DOING")[0].lower().replace(
        "exited", "")
    doc["docker"].update({"state": "EXITED", "status": "exited", "exit_code": 137,
                          "oom_killed": False, "finished_at": _iso(5)})
    doc["verdict"] = "EXITED"
    assert "code 137" in status.render_text(doc)


def test_S16_a_paused_container_explains_its_own_stale_heartbeat(tmp_path):
    doc = _collect(_work(tmp_path, **{"HEARTBEAT": _iso(45)}))
    doc["docker"] = {"ok": True, "state": "PAUSED", "status": "paused",
                     "started_at": _iso(3000), "restart_policy": "on-failure:5",
                     "restart_count": 0, "image": "x"}
    doc["verdict"] = "PAUSED"
    body = status.render_text(doc)
    assert "PAUSED" in body
    assert "does not mean the run has died" in body


def test_S17_battery_unknown_is_not_255_percent():
    """GetSystemPowerStatus returns a BYTE where 255 means unknown."""
    src = (Path(status.__file__)).read_text(encoding="utf-8")
    assert "255" in src, "the 255 sentinel must be handled explicitly"
    p = status.host_power()
    if p is not None:                                        # Windows only
        assert p["battery_percent"] is None or 0 <= p["battery_percent"] <= 100


def test_S18_write_is_atomic_and_leaves_no_temp_file(tmp_path):
    doc = _collect(_work(tmp_path))
    out = tmp_path / "stats.txt"
    status.write(out, doc, refresh_s=20)
    assert out.is_file()
    assert not (tmp_path / "stats.txt.tmp").exists()
    first = out.read_text(encoding="ascii")
    status.write(out, doc, refresh_s=20)                     # rewriting is fine
    assert out.read_text(encoding="ascii") == first


def test_S19_collect_writes_nothing_into_the_work_dir(tmp_path):
    """A viewer must not litter the run's directory: T5's watcher test asserts the same
    thing, and anything dropped here shows up as permanent dirty state in the submodule."""
    w = _work(tmp_path, **{"HEARTBEAT": _iso(1), "events.jsonl": ""})
    before = sorted(p.name for p in w.iterdir())
    _collect(w)
    assert sorted(p.name for p in w.iterdir()) == before


def test_S20_imports_and_renders_without_windows(monkeypatch, tmp_path):
    """CI runs this shard on ubuntu-latest, where there is no windll and no nvidia-smi."""
    monkeypatch.setattr(status, "WINDOWS", False)
    assert status._kernel32() is None
    assert status.host_cpu_percent() is None
    assert status.host_memory() is None
    assert status.host_power() is None
    doc = status.collect(work=_work(tmp_path, **{"HEARTBEAT": _iso(1)}),
                         findings=tmp_path / "none",
                         with_host=True, with_gpu=False, with_docker=False)
    body = status.render_text(doc)
    assert "THIS MACHINE" in body
    body.encode("ascii")


def test_S21_a_skipped_gpu_says_so_rather_than_showing_nothing():
    out = status.probe_gpu(skip=True)
    assert out["skipped"] == "on battery"
    assert not out["ok"]
