"""Operational layer: config.json / configure.py, runner auto-stop limits, the watch view."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent.parent
WORKSPACE = HARNESS.parent
CONFIGURE = WORKSPACE / "configure.py"


@pytest.mark.skipif(not CONFIGURE.exists(), reason="workspace configure.py not present")
def test_C11_configure_set_show_reset_roundtrip(tmp_path, monkeypatch):
    # run configure.py against a copy of the workspace so the live config is untouched
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "configure.py").write_text(CONFIGURE.read_text(encoding="utf-8"), encoding="utf-8")
    run = lambda *a: subprocess.run([sys.executable, str(ws / "configure.py"), *a], capture_output=True, text=True, cwd=ws)
    p = run("set", "run.auto_stop_minutes=120", "container.cpus=6", "watchdog.battery_guard=false")
    assert p.returncode == 0, p.stderr
    data = json.loads((ws / "config.json").read_text(encoding="utf-8"))
    assert data["run"]["auto_stop_minutes"] == 120 and data["container"]["cpus"] == 6 and data["watchdog"]["battery_guard"] is False
    assert "next start" in p.stdout
    p = run("show")
    assert "run.auto_stop_minutes" in p.stdout and "120" in p.stdout
    p = run("set", "run.llm=banana")
    assert p.returncode != 0
    p = run("set", "container.cpus=0")
    assert p.returncode != 0
    p = run("set", "nope.key=1")
    assert p.returncode != 0
    p = run("set", "watchdog.cpu_pause_low_percent=90")
    assert p.returncode != 0 and "below" in p.stderr + p.stdout
    p = run("reset", "run.auto_stop_minutes")
    assert p.returncode == 0
    assert json.loads((ws / "config.json").read_text(encoding="utf-8"))["run"]["auto_stop_minutes"] == 0
    p = run("explain")
    assert p.returncode == 0 and "run.auto_stop_minutes" in p.stdout


@pytest.mark.slow
def test_C12_runner_stops_on_cycle_and_time_limits(tmp_path, monkeypatch):
    work = tmp_path / "work"
    monkeypatch.setenv("RK_WORK_DIR", str(work))
    monkeypatch.setenv("RK_SITE", "off")
    monkeypatch.setenv("RK_LLM", "off")
    monkeypatch.setenv("RK_CLOCK", "2026-09-21T10:00:00Z")
    monkeypatch.setenv("RK_MAX_MINUTES", "0")
    monkeypatch.setenv("RK_MAX_CYCLES", "0")
    from rk_harness import runner
    # time limit: 0 minutes elapsed >= limit? no — use a tiny limit and a cycle-free path: RK_MAX_CYCLES=0 with
    # RK_MAX_MINUTES=1 would run a full cycle first; instead assert the cycle limit path.
    monkeypatch.setenv("RK_MAX_CYCLES", "1")
    monkeypatch.setenv("RK_PHASE", "0")
    rc = runner.main([])
    assert rc == 0
    ev = (work / "events.jsonl").read_text(encoding="utf-8")
    assert "runner_started" in ev and "stopped_by_cycle_limit" in ev and ev.count('"cycle_done"') == 1
    # time limit with an already-elapsed budget: RK_MAX_MINUTES=1 but t_start is now; emulate by patching monotonic
    monkeypatch.setenv("RK_MAX_CYCLES", "0")
    monkeypatch.setenv("RK_MAX_MINUTES", "1")
    ticks = iter([0.0, 10_000.0, 20_000.0, 30_000.0])
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(ticks))
    rc = runner.main([])
    assert rc == 0
    ev = (work / "events.jsonl").read_text(encoding="utf-8")
    assert "stopped_by_time_limit" in ev


def test_C13_enum_per_cycle_env(monkeypatch):
    from rk_harness import runner
    monkeypatch.delenv("RK_ENUM_PER_CYCLE", raising=False)
    assert runner._enum_per_cycle() == 500
    monkeypatch.setenv("RK_ENUM_PER_CYCLE", "7")
    assert runner._enum_per_cycle() == 7
    monkeypatch.setenv("RK_ENUM_PER_CYCLE", "junk")
    assert runner._enum_per_cycle() == 500


def test_C14_watch_renders_and_is_clean(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    (work / "events.jsonl").write_text(
        '{"ts": "2026-09-21T10:00:00Z", "kind": "action", "action": "SEARCH_CELL", "payload": {"order": 2, "cell": [2, 0]}, "cycle_id": 1, "phase": 0}\n'
        '{"ts": "2026-09-21T10:00:01Z", "kind": "directive_fallback", "directive_id": "D-F00001", "target_order": 2, "stages": [2], "rationale": "fallback: emptiest cell", "source": "fallback"}\n'
        '{"ts": "2026-09-21T10:00:02Z", "kind": "codex_usage", "tokens": {"input_tokens": 10, "output_tokens": 2}, "used_percent": 3.5, "window_minutes": 10080, "resets_at": 1788671031, "plan_type": "plus"}\n'
        '{"ts": "2026-09-21T10:00:03Z", "kind": "cycle_done", "cycle_id": 1, "phase": 0, "improved": true, "stall_counter": 0, "accepted": 3, "rejected": 1, "spend_usd": 0.0, "cap_usd": 50.0}\n',
        encoding="utf-8")
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"run": {"auto_stop_minutes": 90}, "watcher": {"refresh_seconds": 5, "events_tail": 10}}), encoding="utf-8")
    monkeypatch.setenv("RK_WORK_DIR", str(work))
    monkeypatch.setenv("RK_CONFIG", str(cfg))
    from rk_harness import watch
    text = watch.render_once()
    for needle in ("rk run", "settings", "auto_stop_minutes", "what it is working on", "SEARCH_CELL", "fallback: emptiest cell",
                   "codex usage", "3.5%", "progress", "health", "results", "last 10 events"):
        assert needle in text, needle
    src = (HARNESS / "rk_harness" / "watch.py").read_text(encoding="utf-8").lower()
    assert "openai" not in src
    # read-only: nothing written into the work dir by rendering
    assert sorted(p.name for p in work.iterdir()) == ["events.jsonl"]
