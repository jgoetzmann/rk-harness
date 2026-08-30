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


def test_C15_project_falls_back_to_highest_solvable_order():
    from rk_harness import search
    from rk_harness.orderconditions import achieved_order_symbolic
    from rk_harness.tableau import classical, content_hash
    rk4 = classical()["rk4"]
    a_free = [float(rk4.A[i][j]) for i in range(4) for j in range(i)]
    t = search.project_or_lower(a_free, [float(b) for b in rk4.b], 4, 4, search.default_constraints())
    assert t is not None and content_hash(t) == content_hash(rk4)          # exact order 4 still wins when solvable
    # a dyadic A with no exact order-4 b: project() stays None (B46), project_or_lower falls back to order 3 (or 2)
    a_free = [0.25, 0.125, 0.375, 0.5, -0.25, 0.75]
    assert search.project(a_free, [0.25] * 4, 4, 4, search.default_constraints()) is None
    t = search.project_or_lower(a_free, [0.25] * 4, 4, 4, search.default_constraints())
    assert t is not None
    assert 2 <= achieved_order_symbolic(t, max_order=4) < 4


def test_C16_llm_throttle_and_usage_cap(tmp_path, monkeypatch):
    from rk_harness import runner
    from rk_harness.types import ArchiveState, RunState
    work = tmp_path / "work"
    monkeypatch.setenv("RK_WORK_DIR", str(work))
    monkeypatch.setenv("RK_LLM", "codex")
    monkeypatch.setenv("RK_LLM_EVERY_CYCLES", "3")
    monkeypatch.setenv("RK_CODEX_USAGE_CAP", "80")
    arch = ArchiveState(0, 0, {1: {}, 2: {}, 3: {}, 4: {}}, (), ())
    st = RunState(0, 2, "2026-09-21T10:00:00Z", "2026-09-21T10:00:00Z", 0.0, 0, None)
    calls = []
    good = json.dumps({"directive_id": "D-T1", "hypothesis_id": None, "target_order": 3, "stages": [4],
                       "constraints": {}, "islands": 1, "budget_minutes": 5, "rationale": "test"})

    def fake_call(system, user):
        calls.append(1)
        return good, 0.0
    monkeypatch.setattr(runner, "call_llm", fake_call)
    monkeypatch.setattr(runner, "_codex_rate_limits", lambda: {"used_percent": 10.0})
    assert runner.llm_due(3, "SEARCH_CELL", 3) and not runner.llm_due(4, "SEARCH_CELL", 3) and runner.llm_due(4, "HYPOTHESIZE", 3)
    d, _ = runner._llm_directive(st, arch, 2, 1, "SEARCH_CELL")      # cycle 1: not due, nothing to reuse -> fallback
    assert calls == [] and d["directive_id"].startswith("D-F")
    d, _ = runner._llm_directive(st, arch, 2, 3, "SEARCH_CELL")      # cycle 3: due -> call
    assert calls == [1] and d["directive_id"] == "D-T1"
    d, _ = runner._llm_directive(st, arch, 2, 4, "SEARCH_CELL")      # cycle 4: reuse the last directive
    assert calls == [1] and d["directive_id"] == "D-T1"
    assert "directive_reused" in (work / "events.jsonl").read_text(encoding="utf-8")
    d, _ = runner._llm_directive(st, arch, 2, 5, "WIDEN")            # escalation -> call again
    assert calls == [1, 1]
    monkeypatch.setattr(runner, "_codex_rate_limits", lambda: {"used_percent": 85.0})
    d, _ = runner._llm_directive(st, arch, 2, 6, "SEARCH_CELL")      # usage cap -> skipped, fallback
    assert calls == [1, 1] and d["directive_id"].startswith("D-F")
    assert "plan usage cap" in (work / "events.jsonl").read_text(encoding="utf-8")


def test_C17_hypothesize_action_appends_a_validated_hypothesis(tmp_path, monkeypatch):
    from rk_harness import ledger, runner
    from rk_harness.types import ArchiveState, RunState
    work = tmp_path / "work"
    monkeypatch.setenv("RK_WORK_DIR", str(work))
    monkeypatch.setenv("RK_LLM", "codex")
    arch = ArchiveState(0, 0, {1: {}, 2: {}, 3: {}, 4: {}}, (), ())
    st = RunState(9, 2, "2026-09-21T10:00:00Z", "2026-09-21T10:00:00Z", 0.0, 21, None)
    good = json.dumps({"statement": "slow p3s4 beats p4s4", "mechanism": "csd weight",
                       "control": "reverses under fast", "predicate": "slow.p3s4.heldout < slow.p4s4.heldout",
                       "min_samples": 200})
    monkeypatch.setattr(runner, "call_llm", lambda s, u: (good, 0.0))
    monkeypatch.setattr(runner, "_codex_rate_limits", lambda: {"used_percent": 10.0})
    assert runner._maybe_propose_hypothesis(st, arch, "SEARCH_CELL", 10) == 0.0      # only on HYPOTHESIZE
    assert ledger.load_hypotheses() == []
    runner._maybe_propose_hypothesis(st, arch, "HYPOTHESIZE", 10)
    hyps = ledger.load_hypotheses()
    assert len(hyps) == 1 and hyps[0]["id"] == "H-001" and hyps[0]["cycle_proposed"] == 10
    assert hyps[0]["verdict"] is None and hyps[0]["min_samples"] == 200
    runner._maybe_propose_hypothesis(st, arch, "HYPOTHESIZE", 11)                    # ids increment
    assert [h["id"] for h in ledger.load_hypotheses()] == ["H-001", "H-002"]
    # malformed predicate: rejected, nothing appended, cycle unaffected
    bad = json.dumps({"statement": "x", "mechanism": "y", "control": "z",
                      "predicate": "__import__('os')", "min_samples": 50})
    monkeypatch.setattr(runner, "call_llm", lambda s, u: (bad, 0.0))
    runner._maybe_propose_hypothesis(st, arch, "HYPOTHESIZE", 12)
    assert len(ledger.load_hypotheses()) == 2
    ev = (work / "events.jsonl").read_text(encoding="utf-8")
    assert "hypothesis_proposed" in ev and "hypothesis_rejected" in ev
    # K8/I5: the hypothesis prompt carries no tier strings and never asks for a verdict
    from rk_harness import prompts
    text = prompts.HYPOTHESIS_SYSTEM_PROMPT + prompts.build_hypothesis_prompt(arch, st, [], [])
    for banned in ("heldout_verified", "search_only", "unreplicated"):
        assert banned not in text
    assert "verdicts are assigned" in prompts.HYPOTHESIS_SYSTEM_PROMPT
