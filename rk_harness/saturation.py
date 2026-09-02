"""Epoch saturation orchestrator. Hand-written (operational layer, unpinned).

The owner delegated the "when is this epoch done" decision (2026-09-02). The rule,
documented in docs/ROADMAP.md: PROGRESS is any of (a) a record accepted into a
previously empty archive cell, (b) an accepted record flagged new_elite (it improved
its cell's best held-out error), or (c) an accepted record at the heldout_verified
tier. When the newest progress event is older than RK_SAT_WINDOW_H hours (default 48)
AND the falsification protocol has produced a file, one assessment is "saturating".
RK_SAT_CONSECUTIVE consecutive saturating checks (default 6; state lives on disk in
saturation_state.json, never in a process, so restarts adopt it) escalate the verdict
to FREEZE with action "freeze". The host watchdog executes the freeze: it drops the
STOP killfile so the runner exits at a cycle boundary, pushes the data repos, and
calls --mark-frozen, which writes EPOCH_STATUS.json and clears STOP. While
EPOCH_STATUS.json exists every check returns FROZEN/none, so a deliberate manual
restart of the run is left alone.

CLI:  python -m rk_harness.saturation --status | --check | --mark-frozen [reason]
--status is read-only; --check also advances the consecutive counter on disk.
"""
from __future__ import annotations

import datetime
import json
import os
import sys

from rk_harness.paths import work_dir

WINDOW_HOURS_DEFAULT = 48.0
CONSECUTIVE_DEFAULT = 6

STATE_FILE = "saturation_state.json"
EPOCH_FILE = "EPOCH_STATUS.json"


def _now() -> datetime.datetime:
    raw = os.environ.get("RK_CLOCK")
    if raw:
        s = raw.strip()
        if s.endswith(("Z", "z")):
            s = s[:-1] + "+00:00"
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_ts(s) -> datetime.datetime | None:
    if not isinstance(s, str) or not s:
        return None
    try:
        t = s[:-1] + "+00:00" if s.endswith(("Z", "z")) else s
        dt = datetime.datetime.fromisoformat(t)
        return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def _window_hours() -> float:
    try:
        return float(os.environ.get("RK_SAT_WINDOW_H", WINDOW_HOURS_DEFAULT))
    except ValueError:
        return WINDOW_HOURS_DEFAULT


def _consecutive_needed() -> int:
    try:
        return max(1, int(os.environ.get("RK_SAT_CONSECUTIVE", CONSECUTIVE_DEFAULT)))
    except ValueError:
        return CONSECUTIVE_DEFAULT


def scan_progress() -> dict:
    """One pass over events.jsonl: newest progress event of each kind, plus totals."""
    seen_cells: set[tuple[int, int, int]] = set()
    last_new_cell = last_improvement = last_verified = last_event = None
    n_accepted = 0
    path = work_dir() / "events.jsonl"
    try:
        fh = open(path, "r", encoding="utf-8")
    except OSError:
        return {"n_accepted": 0, "last_progress_ts": None, "last_progress_kind": None,
                "last_event_ts": None}
    with fh:
        for line in fh:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            ts = _parse_ts(e.get("ts"))
            if ts is not None:
                last_event = ts if last_event is None else max(last_event, ts)
            if e.get("kind") != "accepted" or ts is None:
                continue
            n_accepted += 1
            try:
                cell = (int(e["order"]), int(e["stages"]), int(e["bucket"]))
            except (KeyError, TypeError, ValueError):
                cell = None
            if cell is not None and cell not in seen_cells:
                seen_cells.add(cell)
                last_new_cell = ts if last_new_cell is None else max(last_new_cell, ts)
            if e.get("new_elite"):
                last_improvement = ts if last_improvement is None else max(last_improvement, ts)
            if e.get("tier") == "heldout_verified":
                last_verified = ts if last_verified is None else max(last_verified, ts)
    candidates = [(t, k) for t, k in ((last_new_cell, "new_cell"),
                                      (last_improvement, "elite_improvement"),
                                      (last_verified, "heldout_verified")) if t is not None]
    last_progress, kind = max(candidates) if candidates else (None, None)
    return {
        "n_accepted": n_accepted,
        "n_cells": len(seen_cells),
        "last_new_cell_ts": last_new_cell.isoformat() if last_new_cell else None,
        "last_improvement_ts": last_improvement.isoformat() if last_improvement else None,
        "last_verified_ts": last_verified.isoformat() if last_verified else None,
        "last_progress_ts": last_progress.isoformat() if last_progress else None,
        "last_progress_kind": kind,
        "last_event_ts": last_event.isoformat() if last_event else None,
    }


def _load_state() -> dict:
    try:
        with open(work_dir() / STATE_FILE, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(d: dict) -> None:
    with open(work_dir() / STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=1)


def assess(now: datetime.datetime | None = None) -> dict:
    """Read-only assessment: CONTINUE / SATURATING / FROZEN, no state change."""
    now = now or _now()
    epoch_path = work_dir() / EPOCH_FILE
    if epoch_path.exists():
        return {"verdict": "FROZEN", "action": "none",
                "reason": f"{EPOCH_FILE} present; epoch already frozen"}
    prog = scan_progress()
    window = _window_hours()
    falsified = (work_dir() / "falsification.json").exists()
    last = _parse_ts(prog.get("last_progress_ts"))
    hours = None if last is None else (now - last).total_seconds() / 3600.0
    saturating = falsified and hours is not None and hours > window
    out = dict(prog)
    out.update({
        "window_hours": window,
        "hours_since_progress": None if hours is None else round(hours, 2),
        "falsification_present": falsified,
        "verdict": "SATURATING" if saturating else "CONTINUE",
        "action": "none",
    })
    if not saturating and hours is None:
        out["reason"] = "no progress events yet; too early to judge"
    return out


def check(now: datetime.datetime | None = None) -> dict:
    """Assess AND advance the on-disk consecutive counter; may return action=freeze."""
    now = now or _now()
    out = assess(now)
    if out["verdict"] == "FROZEN":
        return out
    state = _load_state()
    consecutive = int(state.get("consecutive", 0)) if out["verdict"] == "SATURATING" else 0
    if out["verdict"] == "SATURATING":
        consecutive += 1
    needed = _consecutive_needed()
    _save_state({"consecutive": consecutive, "last_check": now.isoformat(),
                 "last_verdict": out["verdict"]})
    out["consecutive"] = consecutive
    out["consecutive_needed"] = needed
    if consecutive >= needed:
        out["verdict"] = "FREEZE"
        out["action"] = "freeze"
    return out


def mark_frozen(reason: str, now: datetime.datetime | None = None) -> dict:
    now = now or _now()
    status = {
        "epoch": 1,
        "frozen_at": now.isoformat(),
        "reason": reason,
        "metrics": scan_progress(),
    }
    with open(work_dir() / EPOCH_FILE, "w", encoding="utf-8") as fh:
        json.dump(status, fh, indent=1)
    try:
        (work_dir() / "STOP").unlink()
    except OSError:
        pass
    return status


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--check"]:
        print(json.dumps(check()))
        return 0
    if args[:1] == ["--mark-frozen"]:
        reason = args[1] if len(args) > 1 else "saturation threshold reached"
        print(json.dumps(mark_frozen(reason)))
        return 0
    print(json.dumps(assess()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
