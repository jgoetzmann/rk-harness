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
    """The whole staleness contract: a reader must be able to tell without arithmetic.

    It now holds on both paths. A file written once used to say only that a loop would keep
    it current, which left the reader to work out for themselves whether the numbers under
    that sentence were minutes or days old.
    """
    doc = _collect(_work(tmp_path))
    looped = status.render_text(doc, refresh_s=20)
    assert "stale after" in looped
    assert "nothing is updating this file" in looped
    once = status.render_text(doc, refresh_s=None)
    assert "stale after" in once
    assert "nothing is updating this file" in once
    assert "written once" in once
    assert "every ~" not in once           # only the loop branch may claim an interval


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
    assert "Docker cannot tell us anything about the container." in body
    # a ten-hour-old heartbeat is not evidence of life, and must not read as reassurance
    assert "has not moved in" in body
    assert "It is Docker that is broken" not in body


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


def test_S22_a_silent_daemon_reads_the_heartbeat_out_loud(tmp_path):
    """When Docker cannot answer, the heartbeat is the only evidence of life there is.

    Regression for a real incident: the daemon wedged, a 140 s heartbeat left over from a
    brief unpause was read as recovery, and the run had in fact done no work for an hour.
    """
    doc = _collect(_work(tmp_path))
    doc["docker"] = {"state": "DOCKER_UNREACHABLE", "error": "timed out after 6s"}
    doc["verdict"] = "DOCKER_UNREACHABLE"

    doc["heartbeat"] = {"at": _iso(0.5), "age_s": 30}
    fresh = status.render_text(doc)
    assert "It is Docker that is broken, not the run." in fresh

    doc["heartbeat"] = {"at": _iso(75), "age_s": 4500}
    stale = status.render_text(doc)
    assert "has not moved in" in stale
    assert "evidence the run is alive" in stale
    assert "It is Docker that is broken" not in stale

    doc["heartbeat"] = {"at": None, "age_s": None}
    none = status.render_text(doc)
    assert "no heartbeat file either" in none


def test_S23_a_live_heartbeat_is_not_proof_of_progress(tmp_path):
    """The trap one level down from S22.

    Observed live: the heartbeat advanced once and froze, while no cycle had completed for
    eighty minutes against a 470 s median. Anything reading liveness from the heartbeat
    alone calls that healthy. It is executing; it is not getting anywhere.
    """
    now = status._utcnow()
    fresh_beat = {"at": _iso(0.5), "age_s": 30}

    moving = {"last": now - datetime.timedelta(seconds=200), "median_s": 470.0, "samples": 20}
    assert status.progress_note(moving, now) is None

    stuck = {"last": now - datetime.timedelta(seconds=4800), "median_s": 470.0, "samples": 20}
    note = status.progress_note(stuck, now)
    assert note and "no cycle has completed in" in note and "median of" in note

    # with no cadence yet, fall back to a floor rather than staying silent
    assert status.progress_note({"last": now - datetime.timedelta(seconds=60)}, now) is None
    bare = status.progress_note({"last": now - datetime.timedelta(seconds=9000)}, now)
    assert bare and "no cadence has been established" in bare
    assert status.progress_note({}, now) is None

    doc = _collect(_work(tmp_path))
    doc["docker"] = {"state": "DOCKER_UNREACHABLE", "error": "timed out after 6s"}
    doc["verdict"] = "DOCKER_UNREACHABLE"
    doc["heartbeat"] = fresh_beat
    doc["cadence"] = stuck
    body = status.render_text(doc)
    assert "running without getting anywhere" in body
    assert "It is Docker that is broken" not in body
    assert "STUCK" in body

    doc["cadence"] = moving
    ok = status.render_text(doc)
    assert "It is Docker that is broken, not the run." in ok
    assert "running without getting anywhere" not in ok
    assert "STUCK" not in ok


# ---------------------------------------------------------------- P08: a file that ages out


def test_S24_a_one_shot_file_declares_its_own_shelf_life(tmp_path):
    """A file written once carries the same deadline pair a looping one carries.

    Without it the header said only that stats.ps1 -Loop would keep the file current, which
    is a fact about a writer that is not running and tells the reader nothing about the age
    of the numbers underneath it.
    """
    doc = _collect(_work(tmp_path))
    body = status.render_text(doc, refresh_s=None)
    assert "stale after" in body
    assert "and every number below it is old." in body
    assert "every ~" not in body

    when = status._parse_ts(doc["written_at"])
    dead = (when + datetime.timedelta(seconds=status.ONE_SHOT_SHELF_LIFE_S)).astimezone()
    _wall, tz = status._local(when)
    assert dead.strftime("%H:%M:%S") in body
    assert tz in body


def test_S25_the_deadline_is_a_pure_function_of_written_at(tmp_path):
    """render_text reads no clock. That is what lets --age and the header agree: both are
    reading the same written_at, not two samples of now."""
    doc = _collect(_work(tmp_path))
    assert status.render_text(doc, refresh_s=None) == status.render_text(doc, refresh_s=None)

    old = dict(doc)
    old["written_at"] = "2026-01-02T03:04:05Z"
    body = status.render_text(old, refresh_s=None)
    when = status._parse_ts(old["written_at"])
    dead = (when + datetime.timedelta(seconds=status.ONE_SHOT_SHELF_LIFE_S)).astimezone()
    # the header and its deadline both follow the document, not the wall clock: a two-day-old
    # file renders a two-day-old deadline, which is the whole point of printing one
    assert status._local(when)[0] in body
    assert dead.strftime("%H:%M:%S") in body


def test_S26_age_reads_the_header_and_runs_no_probe(tmp_path, monkeypatch):
    """--age is the question you ask when Docker is wedged, so it must not touch Docker."""
    doc = _collect(_work(tmp_path))
    doc["written_at"] = "2026-09-08T12:00:00Z"
    p = tmp_path / "stats.txt"
    status.write(p, doc)

    def never(*a, **k):
        raise AssertionError("--age must run no probe")
    monkeypatch.setattr(status, "probe_docker", never)
    monkeypatch.setattr(status, "probe_gpu", never)
    monkeypatch.setattr(status, "host_cpu_percent", never)
    monkeypatch.setattr(status, "_run", never)

    now = status._parse_ts("2026-09-08T12:05:00Z")
    rep = status.age_report(p, now=now)
    assert rep["ok"] and rep["age_s"] == 300.0
    assert rep["written_at"] == "2026-09-08T12:00:00Z"
    assert status.main(["--age", "--out", str(p)]) in (0, 1)


def test_S27_age_exit_codes_say_fresh_stale_and_unreadable(tmp_path, capsys):
    doc = _collect(_work(tmp_path))
    fresh = tmp_path / "fresh.txt"
    status.write(fresh, doc)
    assert status.main(["--age", "--out", str(fresh)]) == 0
    assert "current" in capsys.readouterr().out

    doc["written_at"] = _iso(60)                              # an hour ago
    stale = tmp_path / "stale.txt"
    status.write(stale, doc)
    assert status.main(["--age", "--out", str(stale)]) == 1
    assert "STALE" in capsys.readouterr().out

    assert status.main(["--age", "--out", str(tmp_path / "nope.txt")]) == 2
    assert "absent" in capsys.readouterr().out


def test_S28_the_declared_loop_interval_drives_the_shelf_life(tmp_path):
    """Six refresh intervals, the same multiple the header renders, so the two agree."""
    doc = _collect(_work(tmp_path))
    doc["written_at"] = "2026-09-08T12:00:00Z"
    now = status._parse_ts("2026-09-08T12:02:30Z")            # 150 s later

    looped = tmp_path / "loop.txt"
    status.write(looped, doc, refresh_s=20)
    rep = status.age_report(looped, now=now)
    assert rep["refresh_s"] == 20 and rep["shelf_life_s"] == 120 and rep["stale"] is True

    once = tmp_path / "once.txt"
    status.write(once, doc)
    rep = status.age_report(once, now=now)
    assert rep["refresh_s"] is None
    assert rep["shelf_life_s"] == status.ONE_SHOT_SHELF_LIFE_S and rep["stale"] is False

    assert status.age_report(looped, now=now, max_age_s=3600)["stale"] is False
    assert status.age_report(once, now=now, max_age_s=60)["stale"] is True


def test_S29_read_written_at_survives_a_truncated_or_foreign_file(tmp_path):
    """Anything can be sitting at that path: a half-written file, someone else's file, or
    bytes. None of them may raise, and none of them may read as fresh."""
    cases = {
        "empty.txt": b"",
        "foreign.txt": b"hello, this is not a status file\r\n",
        "bytes.txt": bytes(range(256)) * 4,
    }
    for name, raw in cases.items():
        p = tmp_path / name
        p.write_bytes(raw)
        when, _refresh, err = status.read_written_at(p)
        assert when is None and err, name
        rep = status.age_report(p)
        assert rep["ok"] is False and rep["error"], name

    when, _refresh, err = status.read_written_at(tmp_path / "missing.txt")
    assert when is None and "absent" in err


# ------------------------------------------------- P07: the rate of work, not signs of life


def _cycles(n: int, accepted, start_min: int = 0) -> list[dict]:
    return [{"ts": _iso(start_min + n - i), "kind": "cycle_done", "cycle_id": i,
             "accepted": accepted(i) if callable(accepted) else accepted}
            for i in range(n)]


def _flat(body: str) -> str:
    """The body with its wrapping undone, so an assertion can name a whole sentence."""
    return " ".join(body.split())


def test_S30_accept_rate_quotes_both_medians_and_both_counts(tmp_path):
    """A verdict without its evidence is a number nobody can check. The row prints both
    medians and both sample counts whether or not the verdict comes with them."""
    events = _cycles(60, lambda i: 100 if i < 20 else (5 if i >= 40 else 40))
    acc = status.accept_rate(events)
    assert acc["cycles"] == 60
    assert acc["older_n"] == 20 and acc["recent_n"] == 20
    assert acc["older_median"] == 100 and acc["recent_median"] == 5
    assert acc["enough"] is True and acc["collapsed"] is True

    doc = _collect(_work(tmp_path))
    doc["accept"] = acc
    flat = _flat(status.render_text(doc))
    assert ("median 5 per cycle over the newest 20 cycles, against 100 over the oldest 20 "
            "in this tail") in flat
    assert "COLLAPSE" in flat
    assert "under half what it was earlier in this same tail" in flat


def test_S31_accept_rate_says_it_cannot_compare_rather_than_computing_a_ratio(tmp_path):
    """A byte window is not a cycle window. With four cycles per third, a drop from 100 to 1
    is still not enough to call a phase change, and the row says so instead of ruling."""
    events = _cycles(12, lambda i: 100 if i < 4 else (1 if i >= 8 else 50))
    acc = status.accept_rate(events)
    assert acc["older_n"] == 4 and acc["recent_n"] == 4
    assert acc["enough"] is False and acc["collapsed"] is False

    doc = _collect(_work(tmp_path))
    doc["accept"] = acc
    flat = _flat(status.render_text(doc))
    assert "COLLAPSE" not in flat
    assert "12 cycles in this tail" in flat
    assert "{} per window needed".format(status.ACCEPT_MIN_SAMPLES) in flat


def test_S32_accept_rate_never_divides_by_a_zero_baseline():
    """No ratio anywhere: a zero baseline is not an infinite collapse, and a rise is not a
    collapse at all."""
    zeros = status.accept_rate(_cycles(60, 0))
    assert zeros["older_median"] == 0 and zeros["recent_median"] == 0
    assert zeros["enough"] is True and zeros["collapsed"] is False

    rising = status.accept_rate(_cycles(60, lambda i: 0 if i < 20 else 50))
    assert rising["collapsed"] is False

    # booleans are not counts, however int-like python thinks they are
    assert status.accept_rate([{"kind": "cycle_done", "accepted": True}] * 30)["cycles"] == 0
    assert status.accept_rate([])["cycles"] == 0
    assert status.accept_rate([])["collapsed"] is False


def test_S33_the_model_gate_counts_cycles_since_the_model_last_spoke(tmp_path):
    """directive_fallback is not the model speaking. Only directive_accepted with
    source='llm' writes LAST_DIRECTIVE.json, and only it may reset this counter."""
    spoke = [{"ts": _iso(100), "kind": "directive_accepted", "directive_id": "D-1",
              "source": "llm"}] + _cycles(7, 1, start_min=10)
    g = status.directive_gap(spoke)
    assert g["cycles"] == 7 and g["at_least"] is False

    fell_back = [dict(spoke[0], kind="directive_fallback", source="fallback")] + spoke[1:]
    g = status.directive_gap(fell_back)
    assert g["cycles"] == 7 and g["at_least"] is True

    g = status.directive_gap(spoke[1:])
    assert g["cycles"] == 7 and g["at_least"] is True

    doc = _collect(_work(tmp_path))
    doc["gate"] = g
    flat = _flat(status.render_text(doc))
    assert "the model has not written a directive in at least 7 cycles" in flat


def test_S34_the_codex_snapshot_is_printed_and_a_reading_past_its_own_window_says_so(tmp_path):
    """The latch the run actually hit: a reading that closed the gate was the last reading
    that would ever be taken, so the gate stayed shut for a thousand cycles."""
    base = {"ts": _iso(5), "kind": "codex_usage", "used_percent": 95.0,
            "resets_at": 1788671031, "plan_type": "plus"}
    fresh = status.directive_gap([dict(base, window_minutes=10080, snapshot_age_s=60)])
    assert fresh["snapshot"]["used_percent"] == 95.0
    assert fresh["stale_snapshot"] is False

    doc = _collect(_work(tmp_path))
    doc["gate"] = fresh
    flat = _flat(status.render_text(doc))
    assert "95% of the 10080 minute window used, resets" in flat
    assert "older than its own window" not in flat

    old = status.directive_gap([dict(base, window_minutes=60, snapshot_age_s=7200)])
    assert old["stale_snapshot"] is True
    doc["gate"] = old
    assert "older than its own window" in _flat(status.render_text(doc))

    skipped = status.directive_gap([{"ts": _iso(1), "kind": "llm_skipped", "gate": "directive",
                                     "reason": "plan usage cap", "used_percent": 95.0}])
    assert skipped["last_skip"]["gate"] == "directive"
    doc["gate"] = skipped
    assert "directive gate: plan usage cap at" in _flat(status.render_text(doc))


def test_S35_watchdog_lines_are_dated_and_framed_as_a_record_not_as_state(tmp_path):
    """Property 2: nothing is claimed about the container unless Docker answered. These are
    lines the watchdog printed when it acted, and the section says exactly that."""
    now = status._utcnow()
    log = tmp_path / "watchdog.log"
    log.write_text(
        "\n".join([
            "{} watchdog: container=rk work=D:/x".format(
                (now - datetime.timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")),
            "{} ALERT: no verified candidate for 91 min".format(
                (now - datetime.timedelta(minutes=12)).strftime("%Y-%m-%dT%H:%M:%SZ")),
            "{} host CPU avg 63.4% > 60% over 5 min -> docker pause".format(
                (now - datetime.timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")),
        ]) + "\n", encoding="ascii")

    wl = status.read_watchdog_log(log, now)
    assert wl["present"] is True and wl["error"] is None
    assert len(wl["lines"]) == 3
    assert all(e["age_s"] is not None for e in wl["lines"])
    assert "docker pause" in wl["lines"][-1]["text"]

    doc = _collect(_work(tmp_path))
    doc["watchdog_log"] = wl
    body = status.render_text(doc)
    assert "WHAT THE WATCHDOG SAID" in body
    assert "not a reading of the container's state now." in body
    assert "12m" in body and "2m" in body
    assert "docker pause" in body
    # the verdict block is untouched by anything the log says
    assert "CANNOT TELL" not in body

    # a run of identical lines collapses into one entry carrying its own count
    stamp = (now - datetime.timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    repeated = ["{} container not running (exited); watchdog idle".format(stamp)] * 6
    log.write_text("\n".join(repeated + ["{} back on AC -> docker unpause".format(stamp)])
                   + "\n", encoding="ascii")
    wl = status.read_watchdog_log(log, now)
    assert len(wl["lines"]) == 2 and wl["lines"][0]["repeat"] == 6
    doc["watchdog_log"] = wl
    assert "(x6)" in status.render_text(doc)


def test_S36_an_absent_watchdog_log_is_not_a_problem_but_an_unreadable_one_is(tmp_path):
    """Property 1 cuts both ways: a probe that failed lands in PROBLEMS, and a file that was
    never written is not a failed probe."""
    doc = _collect(_work(tmp_path))
    assert doc["watchdog_log"]["present"] is False
    assert doc["watchdog_log"]["error"] is None
    assert not [p for p in doc["problems"] if "watchdog" in p]
    body = status.render_text(doc)
    assert "absent - no watchdog.log next to this file" in body
    assert "start.ps1 passes -LogFile" in body

    # a directory at that path is a real read failure, and reads as one
    (tmp_path / "watchdog.log").mkdir()
    doc = _collect(_work(tmp_path))
    assert doc["watchdog_log"]["error"]
    assert any("watchdog log" in p for p in doc["problems"])
    assert "unreadable" in status.render_text(doc)


def test_S37_the_new_sections_stay_ascii_and_inside_the_column_budget(tmp_path):
    """The same budget S11 enforces, applied to fields nobody in this project controls: a
    watchdog line can be any length and can carry any byte."""
    now = status._utcnow()
    log = tmp_path / "watchdog.log"
    log.write_bytes(("{} ".format(now.strftime("%Y-%m-%dT%H:%M:%SZ"))
                     + "x" * 300).encode("ascii") + b" \xc3\xa9\xe2\x80\x94\n")
    doc = _collect(_work(tmp_path, **{"HEARTBEAT": _iso(1)}))
    doc["accept"] = status.accept_rate(_cycles(60, lambda i: 100 if i < 20 else 5))
    doc["gate"] = status.directive_gap(
        [{"ts": _iso(9), "kind": "codex_usage", "used_percent": 95.0, "window_minutes": 10080,
          "resets_at": 1788671031, "plan_type": "plus", "snapshot_age_s": 7200},
         {"ts": _iso(1), "kind": "llm_skipped", "gate": "interpret", "reason": "plan usage cap",
          "used_percent": 95.0}] + _cycles(4, 1))
    for refresh in (20, None):
        body = status.render_text(doc, refresh_s=refresh)
        body.encode("ascii")
        overlong = [ln for ln in body.split("\r\n") if len(ln) > 90]
        assert not overlong, overlong[:3]


def test_S38_the_productivity_tail_is_bounded_and_kind_filtered(tmp_path):
    """One read, wide enough for two acceptance windows, parsed down to the kinds asked for."""
    assert status.PRODUCTIVITY_TAIL_BYTES > status.EVENTS_TAIL_BYTES
    ev = tmp_path / "events.jsonl"
    filler = json.dumps({"ts": "2026-09-05T00:00:00Z", "kind": "noise", "pad": "x" * 900}) + "\n"
    wanted = json.dumps({"ts": "2026-09-05T12:00:00Z", "kind": "cycle_done", "accepted": 3}) + "\n"
    with open(ev, "w", encoding="utf-8") as fh:
        fh.write(filler * 5000)                              # over the productivity cap
        fh.write(wanted * 5)
    assert ev.stat().st_size > status.PRODUCTIVITY_TAIL_BYTES

    events, err = status.tail_events(ev, status.PRODUCTIVITY_TAIL_BYTES, kinds=("cycle_done",))
    assert err is None
    assert len(events) == 5 and {e["kind"] for e in events} == {"cycle_done"}
    unfiltered, _ = status.tail_events(ev, status.PRODUCTIVITY_TAIL_BYTES)
    assert len(unfiltered) > len(events)
    assert len(unfiltered) < 5005                            # still only the tail


def test_S39_the_watchdog_script_stays_ascii_and_carries_the_logfile_contract():
    """Seventeen call sites in the one hand-written safety-critical component were rewritten.
    A smart quote or an em dash there is a parse error, and a parse error there is a
    disabled kill switch."""
    wd = Path(status.__file__).resolve().parent.parent / "scripts" / "watchdog.ps1"
    raw = wd.read_bytes()
    text = raw.decode("ascii")
    assert "[string]$LogFile" in text
    assert "function Say" in text
    assert 'Write-Host "$(Get-Date -Format s)' not in text
    assert "[datetime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')" in text

    start = wd.parent.parent.parent / "start.ps1"
    if not start.exists():
        pytest.skip("workspace start.ps1 not present (rk-harness is checked out alone in CI)")
    start.read_bytes().decode("ascii")


# ------------------------------------------- P12: the rest of the daemon, on a shared machine


def _fake_docker(monkeypatch, ps_rc=0, ps_out="", inspect_rc=0, inspect_out="[]", err=""):
    """Inject at status._run, which every probe in this module already goes through, so
    nothing here touches Docker."""
    calls = []

    def fake(cmd, timeout):
        calls.append(list(cmd))
        if cmd[:2] == ["docker", "ps"]:
            return ps_rc, ps_out, err
        if cmd[:2] == ["docker", "inspect"]:
            return inspect_rc, inspect_out, err
        raise AssertionError("unexpected probe: {}".format(cmd))
    monkeypatch.setattr(status, "_run", fake)
    monkeypatch.setattr(status.shutil, "which", lambda name: "docker")
    return calls


def _inspect(name, status_str="running", health=None, restarts=0, image="img"):
    st = {"Status": status_str, "StartedAt": "2026-09-08T00:00:00Z"}
    if health:
        st["Health"] = {"Status": health}
    return {"Name": "/" + name, "State": st, "RestartCount": restarts,
            "Config": {"Image": image}}


def test_S40_the_census_lists_every_container_with_its_restart_count_and_health(tmp_path, monkeypatch):
    """The machine is shared. A stack restarting in a loop next to the run is a fact about
    this run's environment, and no status file on this machine states it today."""
    payload = json.dumps([
        _inspect("vector", health="unhealthy"),
        _inspect("rk", health="healthy"),
        _inspect("supabase-db", status_str="restarting", restarts=2553),
    ])
    calls = _fake_docker(monkeypatch, ps_out="vector\nrk\nsupabase-db\n", inspect_out=payload)

    cs = status.probe_containers()
    assert cs["ok"] is True
    assert [c["name"] for c in cs["containers"]] == ["rk", "supabase-db", "vector"]
    assert set(cs["flagged"]) == {"supabase-db", "vector"}
    assert cs["containers"][1]["restart_count"] == 2553
    assert cs["containers"][2]["health"] == "unhealthy"
    # one ps and one inspect over every name, never docker stats
    assert len(calls) == 2 and calls[1][:2] == ["docker", "inspect"]
    assert not any("stats" in c for call in calls for c in call)

    doc = _collect(_work(tmp_path))
    doc["containers"] = cs
    body = status.render_text(doc)
    assert "OTHER CONTAINERS ON THIS DAEMON" in body
    assert "3 running, 2 flagged" in body
    for needle in ("supabase-db", "2553 restarts", "unhealthy", "FLAG"):
        assert needle in body, needle
    assert "Nothing here is a trend" in body


def test_S41_a_silent_daemon_never_becomes_an_empty_container_list(tmp_path, monkeypatch):
    """S14 for the census. 'Docker did not answer' and 'there is nothing running' are
    different facts, and only the second one would be worth acting on."""
    _fake_docker(monkeypatch, ps_rc=None, err="timed out after 6s")
    cs = status.probe_containers()
    assert cs["ok"] is False and cs["containers"] == []
    assert "timed out" in cs["error"]

    doc = status.collect(work=_work(tmp_path), findings=tmp_path / "none",
                         with_host=False, with_gpu=False, with_docker=False, with_census=True)
    assert doc["containers"]["ok"] is False
    assert any("containers:" in p for p in doc["problems"])
    body = status.render_text(doc)
    section = body.split("OTHER CONTAINERS ON THIS DAEMON")[1].split("PROBLEMS")[0]
    assert "unknown" in section
    assert "0 running" not in section

    # and the switch is honoured: no probe at all, and no claim either
    doc = _collect(_work(tmp_path))
    assert doc["containers"] == {"ok": False, "skipped": True}
    assert "not sampled" in status.render_text(doc)


def test_S42_a_non_ascii_container_name_does_not_break_the_ascii_rule(tmp_path, monkeypatch):
    """Third-party container names are not under this project's control, and S11 asserts the
    whole file is ASCII. The name is transliterated, never dropped."""
    payload = json.dumps([_inspect("caf\u00e9-\u00fcber\u2014db", restarts=99)])
    _fake_docker(monkeypatch, ps_out="whatever\n", inspect_out=payload)
    doc = status.collect(work=_work(tmp_path), findings=tmp_path / "none",
                         with_host=False, with_gpu=False, with_docker=False, with_census=True)
    body = status.render_text(doc, refresh_s=20)
    body.encode("ascii")
    assert not [ln for ln in body.split("\r\n") if len(ln) > 90]
    assert "caf" in body and "db" in body                     # present, not silently dropped
    assert "99 restarts" in body and "FLAG" in body


# ------------------------------- the lane schedule: a cycle is not always an explicit search
#
# Every counter this file prints - the stall count, the accepted median, the archive age -
# was written when a cycle could only be one thing. Under a lane rotation two cycles in three
# legitimately append nothing to the scored archive, and a viewer that does not know which
# lane a cycle was on reports that as a stall, a collapse and a saturation. These tests pin
# the reading BEFORE the rotation is armed, and pin that today's reading did not move.

def _shares(work: Path, schedule: str, n: int, first_cycle: int = 100) -> Path:
    """A real schedule-shares/1 document, built by the module that will write it."""
    from rk_harness import lanes
    rows = [lanes.cycle_row(first_cycle + i, lanes.lane_for(first_cycle + i, schedule),
                            "2026-09-09T06:{:02d}:00Z".format(i % 60), 176.0, 100.0,
                            schedule=schedule)
            for i in range(n)]
    doc = lanes.build_shares(rows, schedule=schedule, window_cycles=200)
    lanes.validate_shares(doc)
    return lanes.write_shares(doc, work / "schedule" / "shares.json")


def _lane_text(rows) -> str:
    return " ".join(" ".join(str(v) for v in row) for row in rows)


def test_S43_with_no_schedule_directory_the_split_is_unknown_and_stays_unknown(tmp_path):
    """rk-work/schedule/ does not exist until the rotation ships. Every lane reader has to
    cope with that, and none of them may fill the gap in with the schedule's own intention."""
    w = _work(tmp_path)
    assert not (w / "schedule").exists()
    ln = status.read_lanes(w, docker_env={"RK_LLM": "codex"}, current_cycle=2726)

    assert ln["exists"] is False and ln["error"] == "shares.json is absent"
    assert ln["measured"] is None, "no measurement exists, so none may be reported"
    assert ln["schedule"] == "E" and ln["armed"] is False
    assert ln["scheduled_share"] == {"explicit": 1.0, "adaptive": 0.0, "implicit": 0.0}
    # The default schedule asks for no rotation, so nothing missing is a fault.
    assert ln["problem"] is None

    text = _lane_text(status.lane_rows(ln))
    assert "every cycle explicit" in text
    assert "unknown" in text
    assert "33" not in text, "a scheduled percentage must never stand in for a measured one"

    # Nothing is cached between passes: a second read of the same absent file is the same
    # answer, and a read after the file appears is the new one.
    assert status.read_lanes(w, docker_env={}, current_cycle=2726)["measured"] is None
    _shares(w, "E", 4, first_cycle=2723)
    assert status.read_lanes(w, docker_env={}, current_cycle=2726)["measured"] is not None


def test_S44_an_armed_schedule_nobody_is_recording_is_a_problem_not_a_number(tmp_path):
    """The survey behind the rotation found a run whose intended 70/15/15 had in fact been
    0.0005 percent, with nothing in a position to notice. A schedule asking for a rotation
    that no document records is exactly that state, and it lands in PROBLEMS."""
    w = _work(tmp_path)
    ln = status.read_lanes(w, docker_env={"RK_LANE_SCHEDULE": "EAI"}, current_cycle=2726)
    assert ln["armed"] is True
    assert ln["measured"] is None
    assert ln["problem"] == "lane split: shares.json is absent"

    doc = _collect(w)
    doc["lanes"] = ln
    body = status.render_text(doc)
    flat = " ".join(body.split())
    assert "schedule 'EAI'" in flat
    assert "asks for explicit 33%, adaptive 33%, implicit 33%" in flat
    assert "unknown (shares.json is absent)" in flat
    # the asked-for split appears once, on the line that says it is only asked for
    assert flat.count("explicit 33%") == 1


def test_S45_the_schedule_is_claimed_only_when_docker_answered(tmp_path):
    """Rule 2 of this file, applied to one more field. An empty env dict is Docker answering
    that the variable is unset, which is a definite answer; None is nobody being able to say."""
    w = _work(tmp_path)
    silent = status.read_lanes(w, docker_env=None, current_cycle=1)
    assert silent["schedule"] is None and silent["armed"] is None
    assert "unknown" in _lane_text(status.lane_rows(silent))
    assert silent["problem"] is None, "the silent daemon is already reported once, by docker"

    answered = status.read_lanes(w, docker_env={}, current_cycle=1)
    assert answered["schedule"] == "E" and answered["schedule_source"] == "env-unset"

    # ...and the run's own document outranks the container environment, because it is what
    # the runner actually resolved.
    _shares(w, "EAI", 6, first_cycle=1)
    both = status.read_lanes(w, docker_env={"RK_LANE_SCHEDULE": "E"}, current_cycle=6)
    assert both["schedule"] == "EAI" and both["schedule_source"] == "shares"


def test_S46_a_shares_document_behind_the_run_prints_unknown_not_its_own_numbers(tmp_path):
    """Printing a stale document's numbers is carrying a value forward under another name."""
    w = _work(tmp_path)
    _shares(w, "EAI", 9, first_cycle=100)
    fresh = status.read_lanes(w, docker_env={},
                              current_cycle=108 + status.LANE_SHARES_STALE_CYCLES)
    assert fresh["stale"] is False and fresh["measured"] is not None

    stale = status.read_lanes(w, docker_env={},
                              current_cycle=109 + status.LANE_SHARES_STALE_CYCLES)
    assert stale["stale"] is True
    assert stale["measured"] is None
    assert "behind the run" in str(stale["error"])
    assert stale["problem"] and "behind the run" in stale["problem"]
    assert "unknown" in _lane_text(status.lane_rows(stale))


def test_S47_a_measured_split_is_printed_with_the_cycle_it_was_measured_at(tmp_path):
    w = _work(tmp_path)
    _shares(w, "EAI", 30, first_cycle=100)
    ln = status.read_lanes(w, docker_env={}, current_cycle=129)
    assert ln["cycles"] == 30
    assert sorted(ln["measured"]) == ["adaptive", "explicit", "implicit"]
    assert abs(sum(v["share"] for v in ln["measured"].values()) - 1.0) < 1e-9

    doc = _collect(w)
    doc["lanes"] = ln
    flat = " ".join(status.render_text(doc).split())
    assert "explicit 33%, adaptive 33%, implicit 33% over 30 cycles" in flat
    assert "from shares.json, at cycle 129" in flat


def test_S48_accepted_counts_explicit_cycles_once_the_stream_says_which_lane(tmp_path):
    """Two cycles in three appending nothing is the schedule working. Folding those zeros
    into the median puts it on the floor and reports a collapse that never happened."""
    plain = _cycles(30, 40)
    assert status.accept_rate(plain)["lane_aware"] is False
    assert status.accept_rate(plain)["cycles"] == 30, "today's tail counts exactly as before"

    mixed = []
    for i, e in enumerate(_cycles(30, 40)):
        lane = ("explicit", "adaptive", "implicit")[i % 3]
        mixed.append(dict(e, lane=lane, accepted=(40 if lane == "explicit" else 0)))
    acc = status.accept_rate(mixed)
    assert acc["lane_aware"] is True
    assert acc["cycles"] == 10 and acc["other_lane_cycles"] == 20
    assert acc["recent_median"] == 40 and acc["collapsed"] is False

    # the same tail counted blind: a floor of zeros and a collapse that is the schedule
    blind = [{k: v for k, v in e.items() if k != "lane"} for e in mixed]
    assert status.accept_rate(blind)["recent_median"] == 0

    # a row written before the rotation existed carries no lane and is still counted
    older = [{k: v for k, v in e.items() if k != "lane"} for e in mixed[:9]] + mixed[9:]
    assert status.accept_rate(older)["cycles"] == 10 + 6

    doc = _collect(_work(tmp_path))
    doc["accept"] = acc
    flat = " ".join(status.render_text(doc).split())
    assert "per explicit cycle" in flat
    assert "20 cycles in this tail were on another lane and are not counted above" in flat


def test_S49_the_stall_row_names_explicit_cycles_only_under_a_rotation(tmp_path):
    """A lane cycle appends nothing because that is what it was told to do. Counting it as a
    cycle that failed to produce is the false stall this reading exists to prevent - and the
    wording under today's all-explicit schedule is the wording it has always had."""
    w = _work(tmp_path, **{"RUNSTATE.json": {"cycle_id": 2726, "phase": 3,
                                             "stall_counter": 106, "current_cell": [6, 3]}})
    doc = _collect(w)

    doc["lanes"] = status.read_lanes(w, docker_env={}, current_cycle=2726)
    today = " ".join(status.render_text(doc).split())
    assert "106 cycles with no new elite" in today
    assert "explicit cycles with no new elite" not in today

    doc["lanes"] = status.read_lanes(w, docker_env={"RK_LANE_SCHEDULE": "EAI"},
                                     current_cycle=2726)
    armed = " ".join(status.render_text(doc).split())
    assert "106 explicit cycles with no new elite" in armed
    assert "a lane cycle appends nothing to the scored archive by design" in armed


def test_S50_every_lane_shape_stays_ascii_and_inside_the_column_budget(tmp_path):
    from rk_harness import lanes
    w = _work(tmp_path, **{"HEARTBEAT": _iso(1)})
    _shares(w, lanes.LEGACY_70_15_15, 40, first_cycle=100)
    envs = [None, {}, {"RK_LANE_SCHEDULE": "EAI"}, {"RK_LANE_SCHEDULE": "EAIX"},
            {"RK_LANE_SCHEDULE": lanes.LEGACY_70_15_15},
            {"RK_LANE_SCHEDULE": "E" * lanes.MAX_SCHEDULE_LEN}]
    doc = _collect(w)
    for env in envs:
        for cycle in (139, 139 + status.LANE_SHARES_STALE_CYCLES + 1):
            doc["lanes"] = status.read_lanes(w, docker_env=env, current_cycle=cycle)
            for refresh in (20, None):
                body = status.render_text(doc, refresh_s=refresh)
                body.encode("ascii")
                overlong = [ln for ln in body.split("\r\n") if len(ln) > 90]
                assert not overlong, (env, cycle, overlong[:2])


def test_S51_a_schedule_that_was_set_and_is_not_in_force_is_said_out_loud(tmp_path, monkeypatch):
    """The runner resolves an unusable schedule to the default rather than failing a cycle,
    which is right and also silent. Silent and unnoticed are different things."""
    w = _work(tmp_path, **{"HEARTBEAT": _iso(1)})
    monkeypatch.setattr(status, "probe_docker", lambda name="rk": {
        "ok": True, "state": "RUNNING", "status": "running",
        "env": {"RK_LANE_SCHEDULE": "EAIX"}})
    monkeypatch.setattr(status, "probe_containers",
                        lambda *a, **k: {"ok": False, "skipped": True})
    doc = status.collect(work=w, findings=w / "nowhere", with_host=False, with_gpu=False)
    assert doc["lanes"]["schedule"] == "E" and doc["lanes"]["armed"] is False
    note = [p for p in doc["problems"] if p.startswith("lane schedule:")]
    assert note and "is ignored" in note[0]
    flat = " ".join(status.render_text(doc).split())
    assert "container value rejected" in flat
    assert "every cycle explicit" in flat
