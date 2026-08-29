"""Read-only rich TUI — SPEC §Surface/dashboard.py, HANDOFF §18.

Never writes anything. Does not import runner (K13: runner's source contains the
LLM vendor string), so it carries its own clock and state reader.
"""
from __future__ import annotations

import collections
import datetime
import json
import os
import shutil
import sys
import time

from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from rk_harness import archive
from rk_harness import credentials
from rk_harness import encourager
from rk_harness import enumeration
from rk_harness import tableau as tableau_mod
from rk_harness import verifier_hash
from rk_harness.paths import work_dir
from rk_harness.types import ArchiveState, RunState

_ESCALATION_KINDS = ("WIDEN", "HYPOTHESIZE", "ADVANCE_PHASE", "ROTATE_PROBLEMS")
_CELLS_PER_ORDER = 5 * 8            # stages 2..6 x buckets 0..7
_enum_cache: dict[int, tuple[int, frozenset[str]]] = {}


# ----------------------------------------------------------------------------
# private helpers (duplicates of runner's on purpose; runner is not importable here)
# ----------------------------------------------------------------------------

def _now() -> datetime.datetime:
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


def _parse_ts(s: str) -> datetime.datetime | None:
    try:
        t = s.strip()
        if t.endswith("Z") or t.endswith("z"):
            t = t[:-1] + "+00:00"
        dt = datetime.datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)
    except (ValueError, AttributeError):
        return None


def _fmt_td(td: datetime.timedelta) -> str:
    secs = int(td.total_seconds())
    if secs < 0:
        secs = 0
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    return f"{d}d {h:02d}:{m:02d}:{s:02d}" if d else f"{h:02d}:{m:02d}:{s:02d}"


def _num(v) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        if v != v:
            return "nan"
        if v in (float("inf"), float("-inf")):
            return "inf"
        return f"{v:.4g}"
    return str(v)


def _load_state_readonly() -> RunState:
    path = work_dir() / "RUNSTATE.json"
    try:
        with open(path, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        cell = d.get("current_cell")
        if cell is not None:
            cell = (int(cell[0]), int(cell[1]))
        return RunState(
            cycle_id=int(d["cycle_id"]),
            phase=int(d["phase"]),
            started_at=str(d["started_at"]),
            last_heartbeat=str(d.get("last_heartbeat", "")),
            spend_usd=float(d.get("spend_usd", 0.0)),
            stall_counter=int(d.get("stall_counter", 0)),
            current_cell=cell,
        )
    except Exception:
        arch = archive.replay()
        ts = _now().strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            phase = int(os.environ.get("RK_PHASE", "0"))
        except ValueError:
            phase = 0
        return RunState(arch.last_cycle_id, phase, ts, ts, 0.0, 0, None)


def _all_events() -> list[dict]:
    path = work_dir() / "events.jsonl"
    out: list[dict] = []
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
            if isinstance(ev, dict):
                out.append(ev)
    return out


def read_events(limit: int = 20) -> list[dict]:
    path = work_dir() / "events.jsonl"
    if not path.exists() or limit <= 0:
        return []
    tail: collections.deque = collections.deque(maxlen=limit)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if isinstance(ev, dict):
                tail.append(ev)
    return list(tail)


def _enum_info(phase: int) -> tuple[int, frozenset[str]]:
    """(total, hashes) of the enumerated space for phase 0/1, cached per process."""
    if phase in _enum_cache:
        return _enum_cache[phase]
    if phase == 0:
        pts = enumeration.enumerate_phase0()
    else:
        pts, cap = enumeration.enumerate_phase1()
        if cap:
            pts = []
    hashes = frozenset(tableau_mod.content_hash(t) for t in pts)
    _enum_cache[phase] = (len(pts), hashes)
    return _enum_cache[phase]


def _all_records():
    try:
        return archive.read_all()
    except Exception:
        return []


# ----------------------------------------------------------------------------
# panels
# ----------------------------------------------------------------------------

def _header_panel(st: RunState, now: datetime.datetime) -> Panel:
    started = _parse_ts(st.started_at)
    uptime = _fmt_td(now - started) if started else "n/a"
    try:
        cap = credentials.monthly_cap_usd()
    except Exception:
        cap = float("nan")
    hb = _parse_ts(st.last_heartbeat)
    hb_age = _fmt_td(now - hb) if hb else "n/a"
    txt = Text()
    txt.append(f"cycle {st.cycle_id}   phase {st.phase}   uptime {uptime}   ")
    txt.append(f"spend ${st.spend_usd:.4f} / ${cap:.2f}   last heartbeat age {hb_age}")
    return Panel(txt, title="rk-harness")


def _candidates_panel(arch: ArchiveState, st: RunState, events: list[dict],
                      records, now: datetime.datetime) -> Panel:
    n_verified = sum(1 for r in records if r.tier == "heldout_verified")
    lines = []
    lines.append(f"verified (heldout tier): {n_verified}")
    lines.append(f"records set: {arch.n_records}")
    if st.phase in (0, 1):
        total, hashes = _enum_info(st.phase)
        archived = {r.tableau_hash for r in records}
        rejected = {e.get("tableau_hash") for e in events if e.get("kind") == "rejected"}
        visited = sum(1 for h in hashes if h in archived or h in rejected)
        lines.append(f"enumeration progress: {visited}/{total} points visited")
    else:
        last = None
        for grid in arch.grids.values():
            for rec in grid.values():
                if last is None or rec.timestamp > last:
                    last = rec.timestamp
        lines.append(f"last discovery: {last if last else 'none'}")
        hour_ago = now - datetime.timedelta(hours=1)
        n_hour = 0
        for e in events:
            if e.get("kind") in ("accepted", "rejected"):
                ts = _parse_ts(str(e.get("ts", "")))
                if ts and ts >= hour_ago:
                    n_hour += 1
        lines.append(f"candidates/hour: {n_hour}")
    try:
        cap = credentials.monthly_cap_usd()
        lines.append(f"spend remaining: ${max(cap - st.spend_usd, 0.0):.4f}")
    except Exception:
        lines.append("spend remaining: n/a")
    return Panel("\n".join(lines), title="candidates")


def _cells_panel(arch: ArchiveState, records) -> Panel:
    classical_hashes = {}
    try:
        for name, t in tableau_mod.classical().items():
            classical_hashes[tableau_mod.content_hash(t)] = name
    except Exception:
        pass
    baseline: dict[tuple[int, int, int], tuple[str, float]] = {}
    for r in records:
        name = classical_hashes.get(r.tableau_hash)
        if name is None:
            continue
        try:
            order = archive.record_order(r)
            bucket = archive.cycle_bucket(int(r.score.cycles["m0plus_fast"]))
        except Exception:
            continue
        key = (order, len(r.tableau.b), bucket)
        cur = baseline.get(key)
        if cur is None or r.score.heldout_error < cur[1]:
            baseline[key] = (name, r.score.heldout_error)
    table = Table(expand=True)
    table.add_column("p")
    table.add_column("s")
    table.add_column("b")
    table.add_column("elite heldout")
    table.add_column("tier")
    table.add_column("baseline")
    for order in sorted(arch.grids.keys()):
        for (stg, bucket) in sorted(arch.grids[order].keys()):
            rec = arch.grids[order][(stg, bucket)]
            base = baseline.get((order, stg, bucket))
            base_txt = f"{base[0]} {_num(base[1])}" if base else "-"
            table.add_row(str(order), str(stg), str(bucket), _num(rec.score.heldout_error),
                          rec.tier, base_txt)
    return Panel(table, title="per-cell best vs classical baseline")


def _health_panel(events: list[dict]) -> Panel:
    lines = []
    try:
        vh = verifier_hash.compute_verifier_hash()
        pinned = verifier_hash.pinned_verifier_hash()
        status = "matches pin" if pinned == vh else ("no pin" if pinned is None else "PIN MISMATCH")
        lines.append(f"verifier hash: {vh[:16]} ({status})")
    except Exception as e:
        lines.append(f"verifier hash: error {e!r}")
    n_rej = sum(1 for e in events if e.get("kind") == "rejected")
    n_acc = sum(1 for e in events if e.get("kind") == "accepted")
    rate = (n_rej / (n_rej + n_acc)) if (n_rej + n_acc) else 0.0
    lines.append(f"reject rate: {rate:.1%} ({n_rej} rejected / {n_acc} accepted)")
    n_esc = sum(1 for e in events if e.get("kind") == "action" and e.get("action") in _ESCALATION_KINDS)
    lines.append(f"escalations: {n_esc}")
    n_ab = sum(1 for e in events if e.get("kind") == "cycle_abandoned")
    lines.append(f"abandoned cycles: {n_ab}")
    try:
        wd = work_dir()
        probe = wd if wd.exists() else wd.parent
        du = shutil.disk_usage(str(probe))
        lines.append(f"disk free: {du.free / 1e9:.2f} GB of {du.total / 1e9:.2f} GB")
    except Exception:
        lines.append("disk free: n/a")
    return Panel("\n".join(lines), title="health")


def _promotion_panel(arch: ArchiveState, st: RunState, events: list[dict]) -> Panel:
    lines = []
    for order in sorted(arch.grids.keys()):
        n = len(arch.grids[order])
        lines.append(f"order {order} coverage: {n}/{_CELLS_PER_ORDER} cells")
    done = [e for e in events if e.get("kind") == "cycle_done"][-5:]
    gain = sum(int(e.get("accepted", 0) or 0) for e in done)
    improved = sum(1 for e in done if e.get("improved"))
    lines.append(f"gain over last 5 cycles: {gain} records, {improved} improving cycles")
    lines.append(f"stall counter: {st.stall_counter}")
    lines.append(f"current cell: {st.current_cell if st.current_cell else 'none'}")
    return Panel("\n".join(lines), title="promotion")


def _gap_panel(arch: ArchiveState) -> Panel:
    try:
        gap = encourager.heldout_gap(arch)
        gap_txt = _num(gap)
    except Exception as e:
        gap_txt = f"error {e!r}"
    lines = [f"held-out gap (mean heldout - search over elites): {gap_txt}"]
    if arch.n_records >= 5000:
        lines.append(f"surrogate: eligible (have {arch.n_records} of 5000)")
    else:
        lines.append(f"surrogate: not yet trained (need 5000, have {arch.n_records})")
    lines.append(f"open hypotheses: {len(arch.open_hypotheses)}")
    lines.append(f"refuted hypotheses: {len(arch.refuted_hypotheses)}")
    return Panel("\n".join(lines), title="held-out gap / surrogate / hypotheses")


def _events_panel(tail: list[dict]) -> Panel:
    table = Table(expand=True)
    table.add_column("ts", no_wrap=True)
    table.add_column("kind", no_wrap=True)
    table.add_column("detail")
    for e in tail:
        detail = {k: v for k, v in e.items() if k not in ("ts", "kind")}
        text = json.dumps(detail, default=str, sort_keys=True)
        if len(text) > 100:
            text = text[:97] + "..."
        table.add_row(str(e.get("ts", "")), str(e.get("kind", "")), text)
    return Panel(table, title="recent events")


# ----------------------------------------------------------------------------
# public
# ----------------------------------------------------------------------------

def build_layout(arch: ArchiveState, st: RunState) -> Layout:
    now = _now()
    events = _all_events()
    records = _all_records()
    root = Layout(name="root")
    root.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="events", size=14),
    )
    root["body"].split_row(Layout(name="left"), Layout(name="right"))
    root["left"].split_column(
        Layout(name="candidates"),
        Layout(name="health"),
        Layout(name="promotion"),
    )
    root["right"].split_column(
        Layout(name="cells", ratio=2),
        Layout(name="gap"),
    )
    root["header"].update(_header_panel(st, now))
    root["candidates"].update(_candidates_panel(arch, st, events, records, now))
    root["health"].update(_health_panel(events))
    root["promotion"].update(_promotion_panel(arch, st, events))
    root["cells"].update(_cells_panel(arch, records))
    root["gap"].update(_gap_panel(arch))
    root["events"].update(_events_panel(events[-20:]))
    return root


def render(arch: ArchiveState, st: RunState) -> None:
    console = Console()
    # A Layout is clipped to the console height; under capture that is 25 lines, which hides
    # panels. Render at a height that fits every panel.
    console.print(build_layout(arch, st), height=max(console.height, 60))


def main() -> int:
    console = Console()
    try:
        while True:
            arch = archive.replay()
            st = _load_state_readonly()
            console.clear()
            console.print(build_layout(arch, st))
            time.sleep(5)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
