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
    monkeypatch.setattr(watch, "docker_info", lambda name="rk": {"status": "running", "started": "2026-09-21T09:00:00Z",
                                                                 "image": "rk-harness:latest", "cpus": 4.0, "memory_gb": 6.0,
                                                                 "pids_limit": 512, "cpu_shares": 256, "env": {"RK_LLM": "codex"}})
    monkeypatch.setattr(watch, "watchdog_running", lambda: True)
    monkeypatch.setattr(watch, "last_push_time", lambda repo: "n/a")
    text = watch.render_once()
    for needle in ("rk run", "settings", "auto_stop_minutes", "what it is working on", "SEARCH_CELL", "fallback: emptiest cell",
                   "codex usage", "3.5%", "progress", "health", "results", "last 10 events"):
        assert needle in text, needle
    src = (HARNESS / "rk_harness" / "watch.py").read_text(encoding="utf-8").lower()
    assert "openai" not in src
    # read-only: nothing written into the work dir by rendering. HEARTBEAT is excluded
    # because T4's heartbeat test leaves a daemon thread running for the rest of the
    # pytest process, and it writes into whatever RK_WORK_DIR points at by then; that
    # made this assertion fail depending on where the 10 s timer landed, which is
    # nothing to do with what render_once() writes.
    assert sorted(p.name for p in work.iterdir() if p.name != "HEARTBEAT") == ["events.jsonl"]


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


def test_C18_literature_store_soften_and_prompt_wiring(tmp_path, monkeypatch):
    monkeypatch.setenv("RK_WORK_DIR", str(tmp_path / "work"))
    from rk_harness import literature, prompts
    from rk_harness.types import ArchiveState, RunState
    softened = literature.soften("a novel first method that beats X, proves Y, a breakthrough")
    for banned in ("novel", "first", "beats", "proves", "breakthrough"):
        assert banned not in softened.lower()
    literature.append_digest({"ts": "2026-09-21T10:00:00Z", "cycle": 5, "topic": "novel Q15 RK",
                              "summary": "Stochastic rounding beats floor rounding in drift.\n\nSecond para.",
                              "key_points": ["floor bias is systematic"],
                              "sources": [{"title": "first paper", "url": "https://arxiv.org/abs/1911.00318"}]})
    d = literature.load_digests()[0]
    assert "novel" not in d["topic"] and "beats" not in d["summary"]
    text = literature.digest_for_prompt()
    assert "floor bias is systematic" in text
    assert literature.next_topic(0) != literature.next_topic(1)
    arch = ArchiveState(0, 0, {1: {}, 2: {}, 3: {}, 4: {}}, (), ())
    st = RunState(1, 2, "", "", 0.0, 0, None)
    assert "floor bias is systematic" in prompts.build_user_prompt(arch, st, [], [], literature=text)
    assert "floor bias is systematic" in prompts.build_hypothesis_prompt(arch, st, [], [], literature=text)
    for banned in ("heldout_verified", "search_only", "unreplicated"):
        assert banned not in prompts.LITERATURE_SYSTEM_PROMPT + prompts.INTERPRET_SYSTEM_PROMPT


def test_C19_runner_literature_and_interpretation_gating(tmp_path, monkeypatch):
    from rk_harness import ledger, literature, runner
    from rk_harness.types import ArchiveState, RunState
    work = tmp_path / "work"
    monkeypatch.setenv("RK_WORK_DIR", str(work))
    monkeypatch.setenv("RK_LLM", "codex")
    monkeypatch.setenv("RK_LIT_EVERY", "2")
    monkeypatch.setenv("RK_INTERPRET_EVERY", "3")
    arch = ArchiveState(0, 0, {1: {}, 2: {}, 3: {}, 4: {}}, (), ())
    st = RunState(1, 2, "2026-09-21T10:00:00Z", "2026-09-21T10:00:00Z", 0.0, 0, None)
    dig = json.dumps({"topic": "csd multipliers", "summary": "CSD halves the shift count on slow multipliers.",
                      "key_points": ["naf is minimal"], "sources": [{"title": "t", "url": "https://x"}]})
    seen = []

    def fake_codex(system, user, extra_args=None, timeout=600):
        seen.append(list(extra_args or []))
        return (dig if extra_args else "Para one interpreting the grids at length, easily long enough to pass the two-hundred-character floor for a published interpretation entry, with mechanism talk.\n\nPara two about the novel first results."), 0.0
    monkeypatch.setattr(runner, "_call_codex", fake_codex)
    monkeypatch.setattr(runner, "_codex_rate_limits", lambda: {"used_percent": 10.0})
    assert runner._maybe_literature_review(st, arch, 3) == 0.0        # not due
    assert literature.load_digests() == []
    runner._maybe_literature_review(st, arch, 4)                      # due -> digest written via web search args
    assert ["-c", "tools.web_search=true"] in seen
    assert literature.load_digests()[0]["topic"] == "csd multipliers"
    assert runner._maybe_interpret(st, arch, 4) == 0.0                # not due
    runner._maybe_interpret(st, arch, 6)                              # due -> interpretation written, softened
    entries = literature.load_interpretations()
    assert len(entries) == 1 and "novel" not in entries[0]["text"]
    ev = (work / "events.jsonl").read_text(encoding="utf-8")
    assert "literature_digest" in ev and "interpretation_published" in ev
    monkeypatch.setattr(runner, "_codex_rate_limits", lambda: {"used_percent": 95.0})
    assert runner._maybe_literature_review(st, arch, 6) == 0.0        # capped


def test_C20_sitegen_publishes_literature_and_interpretation(tmp_path, monkeypatch):
    work = tmp_path / "work"
    monkeypatch.setenv("RK_WORK_DIR", str(work))
    from rk_harness import archive, literature, sitegen
    literature.append_digest({"ts": "2026-09-21T10:00:00Z", "cycle": 5, "topic": "novel Q15 RK",
                              "summary": "Floor rounding drift beats naive expectations.",
                              "key_points": ["k1"], "sources": [{"title": "first paper", "url": "https://arxiv.org/abs/1911.00318"}]})
    literature.append_interpretation({"ts": "2026-09-21T10:01:00Z", "cycle": 6,
                                      "text": "The archive proves a novel pattern.\n\nSecond paragraph."})
    out = tmp_path / "docs"
    sitegen.build(archive.replay(), out)                              # would raise on any banned word
    lit = (out / "literature.html").read_text(encoding="utf-8")
    interp = (out / "interpretation.html").read_text(encoding="utf-8")
    assert sitegen.BANNER in lit and sitegen.BANNER in interp
    assert "arxiv.org" in lit and "Model-written" in lit and "Model-written" in interp
    for page in (lit, interp):
        sitegen.check_banned(page)
