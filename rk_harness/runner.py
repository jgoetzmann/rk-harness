"""Cycle loop — SPEC §Surface/runner.py.

The only module allowed to contain the string ``openai`` or to open a socket.
The LLM call is one plain ``urllib.request`` POST, made only when the env var
``RK_LLM == "on"`` and the monthly spend is below the cap. No retries.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from rk_harness import archive
from rk_harness import credentials
from rk_harness import directive as directive_mod
from rk_harness import encourager
from rk_harness import enumeration
from rk_harness import evaluator
from rk_harness import ledger
from rk_harness import prompts
from rk_harness import search
from rk_harness import sitegen
from rk_harness import tableau as tableau_mod
from rk_harness import verifier
from rk_harness import verifier_hash
from rk_harness.paths import FIXTURES_DIR, archive_dir, findings_dir, work_dir
from rk_harness.types import Record, RunState, Tableau

HEARTBEAT_INTERVAL_S = 10

_LLM_URL = "https://api.openai.com/v1/chat/completions"
_LLM_IN_USD_PER_M = 0.4
_LLM_OUT_USD_PER_M = 1.6
_ENUM_PER_CYCLE = 500          # enumeration candidates processed per cycle (Phase 0 has 16)

_heartbeat_thread: threading.Thread | None = None


# ----------------------------------------------------------------------------
# clock
# ----------------------------------------------------------------------------

def now() -> datetime.datetime:
    """UTC now; env RK_CLOCK (ISO 8601) overrides, read on every call."""
    raw = os.environ.get("RK_CLOCK")
    if raw:
        s = raw.strip()
        if s.endswith("Z") or s.endswith("z"):
            s = s[:-1] + "+00:00"
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)
    return datetime.datetime.now(datetime.timezone.utc)


def iso_now() -> str:
    return now().strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------------------
# atomic file helpers
# ----------------------------------------------------------------------------

def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _write_heartbeat_once() -> None:
    _atomic_write_text(work_dir() / "HEARTBEAT", iso_now())


def _heartbeat_loop() -> None:
    while True:
        time.sleep(HEARTBEAT_INTERVAL_S)
        try:
            _write_heartbeat_once()
        except Exception as e:  # never let the daemon die
            print(f"heartbeat: {e!r}", file=sys.stderr)


def heartbeat() -> None:
    """Write HEARTBEAT immediately, then every 10 s from a daemon thread."""
    global _heartbeat_thread
    try:
        _write_heartbeat_once()
    except Exception as e:
        print(f"heartbeat: {e!r}", file=sys.stderr)
    if _heartbeat_thread is not None and _heartbeat_thread.is_alive():
        return
    t = threading.Thread(target=_heartbeat_loop, name="rk-heartbeat", daemon=True)
    t.start()
    _heartbeat_thread = t


# ----------------------------------------------------------------------------
# state
# ----------------------------------------------------------------------------

def _state_path() -> Path:
    return work_dir() / "RUNSTATE.json"


def _state_to_json(st: RunState) -> dict:
    return {
        "cycle_id": st.cycle_id,
        "phase": st.phase,
        "started_at": st.started_at,
        "last_heartbeat": st.last_heartbeat,
        "spend_usd": st.spend_usd,
        "stall_counter": st.stall_counter,
        "current_cell": list(st.current_cell) if st.current_cell is not None else None,
    }


def _state_from_json(d: dict) -> RunState:
    if not isinstance(d, dict):
        raise ValueError("RUNSTATE.json is not an object")
    cell = d.get("current_cell")
    if cell is not None:
        cell = (int(cell[0]), int(cell[1]))
    phase = int(d["phase"])
    if phase not in (0, 1, 2, 3):
        raise ValueError(f"bad phase {phase}")
    return RunState(
        cycle_id=int(d["cycle_id"]),
        phase=phase,
        started_at=str(d["started_at"]),
        last_heartbeat=str(d.get("last_heartbeat", "")),
        spend_usd=float(d.get("spend_usd", 0.0)),
        stall_counter=int(d.get("stall_counter", 0)),
        current_cell=cell,
    )


def _rebuild_state() -> RunState:
    arch = archive.replay()
    raw_phase = os.environ.get("RK_PHASE", "0")
    try:
        phase = int(raw_phase)
    except ValueError:
        phase = 0
    if phase not in (0, 1, 2, 3):
        phase = 0
    ts = iso_now()
    return RunState(
        cycle_id=arch.last_cycle_id,
        phase=phase,
        started_at=ts,
        last_heartbeat=ts,
        spend_usd=0.0,
        stall_counter=0,
        current_cell=None,
    )


def load_state() -> RunState:
    path = _state_path()
    if not path.exists():
        return _rebuild_state()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return _state_from_json(json.load(fh))
    except Exception as e:
        print(f"warning: RUNSTATE.json corrupt ({e!r}); rebuilding from archive", file=sys.stderr)
        return _rebuild_state()


def save_state(st: RunState) -> None:
    _atomic_write_text(_state_path(), json.dumps(_state_to_json(st), indent=1))


# ----------------------------------------------------------------------------
# events
# ----------------------------------------------------------------------------

def _events_path() -> Path:
    return work_dir() / "events.jsonl"


def log_event(kind: str, **detail) -> None:
    ev = {"ts": iso_now(), "kind": kind}
    ev.update(detail)
    path = _events_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(ev, default=str) + "\n")
        fh.flush()


def _rejected_hashes() -> set[str]:
    """tableau_hash of every 'rejected' event so far (skipped on later cycles)."""
    out: set[str] = set()
    path = _events_path()
    if not path.exists():
        return out
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if isinstance(ev, dict) and ev.get("kind") == "rejected":
                h = ev.get("tableau_hash")
                if isinstance(h, str):
                    out.add(h)
    return out


# ----------------------------------------------------------------------------
# LLM
# ----------------------------------------------------------------------------

def call_llm(system: str, user: str) -> tuple[str, float]:
    """One urllib POST to the chat completions endpoint. Raises on any failure."""
    key = credentials.openai_key()
    if not key:
        raise RuntimeError("no API key available")
    model = os.environ.get("RK_LLM_MODEL", "gpt-4.1-mini")
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
    }).encode("utf-8")
    req = urllib.request.Request(
        _LLM_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    content = payload["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise RuntimeError("LLM response content is not a string")
    usage = payload.get("usage") or {}
    p_tok = int(usage.get("prompt_tokens", 0) or 0)
    c_tok = int(usage.get("completion_tokens", 0) or 0)
    cost = p_tok * _LLM_IN_USD_PER_M / 1e6 + c_tok * _LLM_OUT_USD_PER_M / 1e6
    return content, float(cost)


# ----------------------------------------------------------------------------
# baselines
# ----------------------------------------------------------------------------

def _classical_orders() -> dict[str, int]:
    with open(FIXTURES_DIR / "classical.json", "r", encoding="utf-8") as fh:
        data = json.load(fh)
    out: dict[str, int] = {}
    for name, entry in data.items():
        if name.startswith("_") or not isinstance(entry, dict):
            continue
        out[name] = int(entry.get("order", 1))
    return out


def seed_baselines(verifier_hash_value: str) -> int:
    """Append the 8 classical tableaus (cycle 0, seed 0, unreplicated) if absent."""
    existing = {r.tableau_hash for r in archive.read_all()}
    orders = _classical_orders()
    added = 0
    for name, t in tableau_mod.classical().items():
        h = tableau_mod.content_hash(t)
        if h in existing:
            continue
        order = orders.get(name, 1)
        verdict, sv = verifier.verify_with_score(t, order)
        if verdict is not None:
            log_event("baseline_rejected", name=name, code=verdict.code, detail=verdict.detail)
        if sv is None:
            sv = evaluator.evaluate(t, evaluator.DEFAULT_BUDGET_CYCLES)
        rec = Record(
            tableau_hash=h,
            tableau=t,
            score=sv,
            tier="unreplicated",
            cycle_id=0,
            seed=0,
            verifier_hash=verifier_hash_value,
            directive_id=None,
            hypothesis_id=None,
            timestamp=iso_now(),
        )
        archive.append(rec)
        existing.add(h)
        added += 1
        log_event("baseline_seeded", name=name, tableau_hash=h)
    return added


# ----------------------------------------------------------------------------
# cycle
# ----------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class _Candidate:
    tableau: Tableau
    order: int
    seed: int
    directive_id: str | None
    hypothesis_id: str | None


def _eval_budget() -> int:
    try:
        return int(os.environ.get("RK_EVAL_BUDGET", "200"))
    except ValueError:
        return 200


def _cmaes_candidates(d: dict, base_cycle_id: int) -> list[_Candidate]:
    order = int(d["target_order"])
    constraints = dict(search.default_constraints())
    constraints.update(d.get("constraints") or {})
    islands = int(d.get("islands", 4))
    budget = _eval_budget()
    out: list[_Candidate] = []
    for stg in d["stages"]:
        for k in range(islands):
            seed = base_cycle_id * 100 + k
            log_event("island_start", order=order, stages=int(stg), seed=seed, budget=budget,
                      directive_id=d.get("directive_id"))
            n_yield = 0
            for t in search.cmaes_island(order, int(stg), seed, constraints, budget):
                out.append(_Candidate(t, order, seed, d.get("directive_id"), d.get("hypothesis_id")))
                n_yield += 1
            log_event("island_done", order=order, stages=int(stg), seed=seed, yielded=n_yield)
    return out


def _llm_directive(state: RunState, arch, phase: int, new_cycle_id: int) -> tuple[dict, float]:
    """Directive for phases 2/3: LLM when enabled, else deterministic fallback."""
    spent = 0.0
    if os.environ.get("RK_LLM") == "on" and state.spend_usd < credentials.monthly_cap_usd():
        hyps = ledger.load_hypotheses()
        refuted = [h for h in hyps if h.get("verdict") == "refuted"]
        open_h = [h for h in hyps if h.get("verdict") is None]
        user = prompts.build_user_prompt(arch, state, refuted, open_h)
        content, cost = call_llm(prompts.SYSTEM_PROMPT, user)
        spent = cost
        log_event("llm_call", cost_usd=cost, model=os.environ.get("RK_LLM_MODEL", "gpt-4.1-mini"),
                  chars=len(content))
        try:
            d = directive_mod.parse_directive(content)
            log_event("directive_accepted", directive_id=d.get("directive_id"),
                      hypothesis_id=d.get("hypothesis_id"))
            return d, spent
        except directive_mod.DirectiveError as e:
            log_event("directive_rejected", error=str(e), text=content[:500])
    elif os.environ.get("RK_LLM") == "on":
        log_event("llm_skipped", reason="spend cap reached", spend_usd=state.spend_usd)
    d = directive_mod.fallback_directive(arch, phase, new_cycle_id)
    log_event("directive_fallback", directive_id=d.get("directive_id"))
    return d, spent


def _git(args: list[str]) -> None:
    try:
        subprocess.run(["git"] + args, check=False, capture_output=True, timeout=120)
    except Exception as e:
        log_event("git_failed", args=args, error=repr(e))


def _commit_outputs(new_cycle_id: int) -> None:
    fd = findings_dir()
    _git(["-C", str(fd), "add", "-A"])
    _git(["-C", str(fd), "commit", "-qm", f"cycle {new_cycle_id}"])
    wd = work_dir()
    today = archive.today_path()
    done = [p for p in sorted(archive_dir().glob("*.jsonl")) if p.resolve() != today.resolve()]
    if done:
        for p in done:
            _git(["-C", str(wd), "add", str(p)])
        _git(["-C", str(wd), "commit", "-qm", f"cycle {new_cycle_id}"])


def _abandon(state: RunState, e: BaseException) -> RunState:
    try:
        log_event("cycle_abandoned", error=repr(e), cycle_id=state.cycle_id)
    except Exception as e2:
        print(f"log_event failed: {e2!r}", file=sys.stderr)
    return dataclasses.replace(state, stall_counter=state.stall_counter + 1)


def run_cycle(state: RunState) -> RunState:
    try:
        return _run_cycle(state)
    except Exception as e:      # the cycle boundary must never raise
        return _abandon(state, e)


def _fallback_like(directive_id: str, order: int, stages: list[int], rationale: str) -> dict:
    return {
        "directive_id": directive_id,
        "hypothesis_id": None,
        "target_order": order,
        "stages": list(stages),
        "constraints": search.default_constraints(),
        "islands": 4,
        "budget_minutes": 5,
        "rationale": rationale,
    }


def _run_cycle(state: RunState) -> RunState:
    new_cycle_id = state.cycle_id + 1
    phase = state.phase

    # 1. replay, verifier hash, baselines
    arch = archive.replay()
    vh = verifier_hash.compute_verifier_hash()
    if arch.n_records == 0:
        n = seed_baselines(vh)
        log_event("baselines_seeded", count=n, verifier_hash=vh)
        arch = archive.replay()

    # 2. encourager
    action = encourager.next_action(state, arch, now())
    log_event("action", action=action.kind, payload=action.payload, cycle_id=new_cycle_id, phase=phase)
    if action.kind == "FREEZE":
        log_event("frozen", cycle_id=state.cycle_id)
        return state
    if action.kind == "PACKAGE":
        failed = 0
        for r in archive.read_all():
            v = verifier.verify(r.tableau, archive.record_order(r))
            if v is not None:
                failed += 1
                log_event("reverify_failed", tableau_hash=r.tableau_hash, code=v.code, detail=v.detail)
        log_event("package_reverified", failed=failed)
        return state

    # 3. candidates
    existing = {r.tableau_hash for r in archive.read_all()}
    seen = set(existing) | _rejected_hashes()
    cands: list[_Candidate] = []
    enumeration_phase = False
    enumeration_exhausted = False
    spent = 0.0

    if phase == 0:
        enumeration_phase = True
        did = f"D-E0{new_cycle_id:05d}"
        all_pts = enumeration.enumerate_phase0()
        fresh = [t for t in all_pts if tableau_mod.content_hash(t) not in seen]
        enumeration_exhausted = len(fresh) == 0
        cands = [_Candidate(t, 2, 0, did, None) for t in fresh[:_ENUM_PER_CYCLE]]
        log_event("enumeration", phase=0, total=len(all_pts), remaining=len(fresh),
                  taken=len(cands), directive_id=did)
    elif phase == 1:
        did = f"D-E1{new_cycle_id:05d}"
        all_pts, cap_exceeded = enumeration.enumerate_phase1()
        if cap_exceeded:
            log_event("phase1_cap_exceeded", cap=enumeration.PHASE1_CAP)
            d = _fallback_like(f"D-F{new_cycle_id:05d}", 3, [3, 4], "phase 1 cap exceeded; CMA-ES fallback")
            log_event("directive_fallback", directive_id=d["directive_id"])
            cands = _cmaes_candidates(d, state.cycle_id)
        else:
            enumeration_phase = True
            fresh = [t for t in all_pts if tableau_mod.content_hash(t) not in seen]
            enumeration_exhausted = len(fresh) == 0
            cands = [_Candidate(t, 3, 0, did, None) for t in fresh[:_ENUM_PER_CYCLE]]
            log_event("enumeration", phase=1, total=len(all_pts), remaining=len(fresh),
                      taken=len(cands), directive_id=did)
            if enumeration_exhausted:
                # the 4-stage part of Phase 1 is treated as cap-exceeded -> CMA-ES fallback
                log_event("phase1_cap_exceeded", cap=enumeration.PHASE1_CAP, part="4-stage")
                d = _fallback_like(f"D-F{new_cycle_id:05d}", 3, [4], "phase 1 4-stage part: CMA-ES fallback")
                log_event("directive_fallback", directive_id=d["directive_id"])
                cands = _cmaes_candidates(d, state.cycle_id)
    else:
        d, spent = _llm_directive(state, arch, phase, new_cycle_id)
        cands = _cmaes_candidates(d, state.cycle_id)

    # 4. verify / tier / append, with an in-memory elite map kept current
    elite_map: dict[tuple[int, int, int], Record] = {}
    for order, grid in arch.grids.items():
        for (stg, bucket), rec in grid.items():
            elite_map[(int(order), int(stg), int(bucket))] = rec

    improved = False
    last_cell: tuple[int, int] | None = None
    n_accepted = 0
    n_rejected = 0
    n_skipped = 0
    for cand in cands:
        t = cand.tableau
        h = tableau_mod.content_hash(t)
        if h in seen:
            n_skipped += 1
            continue
        seen.add(h)
        verdict, sv = verifier.verify_with_score(t, cand.order)
        if verdict is not None or sv is None:
            n_rejected += 1
            code = verdict.code if verdict is not None else "NAN_OR_INF"
            detail = verdict.detail if verdict is not None else "no score"
            log_event("rejected", code=code, detail=detail, tableau_hash=h,
                      claimed_order=cand.order, directive_id=cand.directive_id)
            continue
        stg = tableau_mod.stages(t)
        bucket = archive.cycle_bucket(int(sv.cycles["m0plus_fast"]))
        prelim = Record(
            tableau_hash=h,
            tableau=t,
            score=sv,
            tier="unreplicated",
            cycle_id=new_cycle_id,
            seed=cand.seed,
            verifier_hash=vh,
            directive_id=cand.directive_id,
            hypothesis_id=cand.hypothesis_id,
            timestamp=iso_now(),
        )
        grid_order = archive.record_order(prelim)
        key = (grid_order, stg, bucket)
        inc = elite_map.get(key)
        tier = archive.assign_tier(sv, inc.score if inc is not None else None)
        rec = dataclasses.replace(prelim, tier=tier)
        archive.append(rec)
        n_accepted += 1
        last_cell = (stg, bucket)
        new_elite = inc is None or sv.heldout_error < inc.score.heldout_error
        if new_elite:
            elite_map[key] = rec
            improved = True
        log_event("accepted", tableau_hash=h, tier=tier, order=grid_order, stages=stg,
                  bucket=bucket, heldout_error=sv.heldout_error, search_error=sv.search_error,
                  cycles_fast=int(sv.cycles["m0plus_fast"]), new_elite=new_elite,
                  directive_id=cand.directive_id)
    log_event("candidates_processed", accepted=n_accepted, rejected=n_rejected,
              skipped=n_skipped, total=len(cands))

    # 5. hypotheses
    resolved = ledger.resolve_open(archive.replay(), new_cycle_id)
    if resolved:
        log_event("hypotheses_resolved", ids=list(resolved))

    # 6. site + commits
    if os.environ.get("RK_SITE") != "off":
        try:
            sitegen.build(archive.replay(), findings_dir() / "docs")
        except sitegen.BannedWordError as e:
            log_event("site_build_failed", error=repr(e))
    if os.environ.get("RK_GIT_COMMIT") == "on":
        _commit_outputs(new_cycle_id)

    # 7. phase advance + new state
    new_phase = phase
    if enumeration_phase and enumeration_exhausted and phase < 3:
        new_phase = phase + 1
        log_event("phase_advanced", **{"from": phase, "to": new_phase, "reason": "enumeration complete"})
    if action.kind == "ADVANCE_PHASE":
        target = int(action.payload.get("to", min(phase + 1, 3)))
        if target > new_phase:
            new_phase = target
            log_event("phase_advanced", **{"from": phase, "to": new_phase, "reason": "encourager"})
    if last_cell is None:
        cell = action.payload.get("cell") if isinstance(action.payload, dict) else None
        if cell is not None:
            last_cell = (int(cell[0]), int(cell[1]))
        else:
            last_cell = state.current_cell
    new_state = RunState(
        cycle_id=new_cycle_id,
        phase=new_phase,
        started_at=state.started_at,
        last_heartbeat=iso_now(),
        spend_usd=state.spend_usd + spent,
        stall_counter=0 if improved else state.stall_counter + 1,
        current_cell=last_cell,
    )
    save_state(new_state)
    log_event("cycle_done", cycle_id=new_cycle_id, phase=new_phase, improved=improved,
              stall_counter=new_state.stall_counter, accepted=n_accepted, rejected=n_rejected)
    return new_state


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rk_harness.runner")
    parser.add_argument("--cycles", type=int, default=None, help="stop after N cycles")
    parser.add_argument("--once", action="store_true", help="run exactly one cycle")
    args = parser.parse_args(argv)
    heartbeat()
    state = load_state()
    done = 0
    limit = 1 if args.once else args.cycles
    while not (work_dir() / "STOP").exists():
        state = run_cycle(state)
        done += 1
        if limit is not None and done >= limit:
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
