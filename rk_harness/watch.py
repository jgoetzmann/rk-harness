"""Live status view for the run — everything an operator wants on one screen.

Read-only: reads config.json, the rk-work event stream / archive / state / hypotheses,
`docker inspect` for the container, and nothing else. Never writes.

    python -m rk_harness.watch            live, refreshes every config watcher.refresh_seconds
    python -m rk_harness.watch --once     one snapshot to stdout
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import warnings
from collections import Counter

warnings.filterwarnings("ignore", category=UserWarning, module="cma")   # matplotlib absent; irrelevant here
from pathlib import Path

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from rk_harness import archive, ledger, verifier_hash
from rk_harness import tableau as tableau_mod
from rk_harness.paths import findings_dir, work_dir
from rk_harness.timefmt import fmt_ct, to_ct
from rk_harness.types import ArchiveState

PHASES = {
    0: ("exhaustive enumeration", "2-stage order-2 family: 16 exactly-representable points (a proof, not a search)"),
    1: ("exhaustive enumeration", "3-stage order-3 lattice: 5,094 exact points, verified in batches"),
    2: ("CMA-ES search", "order <= 4, stages 4-5, 4 islands; directives from the LLM narrow the cells"),
    3: ("CMA-ES search", "order <= 4, stages 4-6, 4 islands; PACKAGE from 2026-11-20, FREEZE from 2026-12-05"),
}
_cache: dict = {}


# ----------------------------------------------------------------------------- data access

def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_ts(s) -> datetime.datetime | None:
    if not isinstance(s, str) or not s:
        return None
    try:
        t = s[:-1] + "+00:00" if s.endswith("Z") else s
        d = datetime.datetime.fromisoformat(t)
        return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def _fmt_td(td: datetime.timedelta | None) -> str:
    if td is None:
        return "n/a"
    s = int(abs(td.total_seconds()))
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m, sec = divmod(rem, 60)
    return (f"{d}d " if d else "") + f"{h:02d}:{m:02d}:{sec:02d}"


def _config_path() -> Path:
    raw = os.environ.get("RK_CONFIG")
    return Path(raw) if raw else work_dir().parent / "config.json"


def load_config() -> dict:
    try:
        data = json.loads(_config_path().read_text(encoding="utf-8"))
        return {k: v for k, v in data.items() if not k.startswith("_")}
    except (OSError, ValueError):
        return {}


def load_events() -> list[dict]:
    path = work_dir() / "events.jsonl"
    out: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev, dict):
                    out.append(ev)
    except OSError:
        pass
    return out


def docker_info(name: str = "rk") -> dict:
    """Container state + resources + RK_* env, cached 10 s. Empty if docker is unavailable."""
    key = ("docker", name)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < 10:
        return hit[1]
    info: dict = {}
    if shutil.which("docker"):
        try:
            p = subprocess.run(["docker", "inspect", name], capture_output=True, text=True, timeout=8)
            if p.returncode == 0:
                d = json.loads(p.stdout)[0]
                hc = d.get("HostConfig", {})
                info = {
                    "status": d.get("State", {}).get("Status"),
                    "started": d.get("State", {}).get("StartedAt", "")[:19] + "Z",
                    "image": d.get("Config", {}).get("Image"),
                    "cpus": (hc.get("NanoCpus") or 0) / 1e9,
                    "memory_gb": (hc.get("Memory") or 0) / 2**30,
                    "pids_limit": hc.get("PidsLimit"),
                    "cpu_shares": hc.get("CpuShares"),
                    "env": {e.split("=", 1)[0]: e.split("=", 1)[1] for e in d.get("Config", {}).get("Env", [])
                            if e.startswith("RK_")},
                }
            else:
                info = {"status": "absent"}
        except Exception:  # noqa: BLE001
            info = {"status": "unknown"}
    _cache[key] = (time.monotonic(), info)
    return info


def watchdog_running() -> bool | None:
    if platform.system() != "Windows":
        return None
    hit = _cache.get("watchdog")
    if hit and time.monotonic() - hit[0] < 30:
        return hit[1]
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*watchdog.ps1*' -and $_.CommandLine -notlike '*-Once*' } | Measure-Object).Count"],
                           capture_output=True, text=True, timeout=15)
        val = int(p.stdout.strip() or 0) > 0
    except Exception:  # noqa: BLE001
        val = None
    _cache["watchdog"] = (time.monotonic(), val)
    return val


def _load_state(arch: ArchiveState):
    """RUNSTATE.json as a RunState, read directly (this module must not import runner: K13).
    Absent or corrupt -> a state rebuilt from the archive, like the runner does."""
    from rk_harness.types import RunState
    try:
        d = json.loads((work_dir() / "RUNSTATE.json").read_text(encoding="utf-8"))
        cell = d.get("current_cell")
        return RunState(int(d["cycle_id"]), int(d["phase"]), str(d.get("started_at", "")), str(d.get("last_heartbeat", "")),
                        float(d.get("spend_usd", 0.0)), int(d.get("stall_counter", 0)),
                        (int(cell[0]), int(cell[1])) if cell else None)
    except Exception:  # noqa: BLE001
        return RunState(arch.last_cycle_id, 0, "", "", 0.0, 0, None)


def last_push_time(repo: Path) -> str:
    try:
        p = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%cI", "origin/main"], capture_output=True, text=True, timeout=8)
        return fmt_ct(p.stdout.strip()) if p.returncode == 0 else "n/a"
    except Exception:  # noqa: BLE001
        return "n/a"


# ----------------------------------------------------------------------------- panels

def _kv_table(rows: list[tuple[str, str]], title: str) -> Panel:
    t = Table.grid(padding=(0, 1))
    t.add_column(style="bold cyan", no_wrap=True)
    t.add_column()
    for k, v in rows:
        t.add_row(k, str(v))
    return Panel(t, title=title)


def header(st, arch: ArchiveState, events: list[dict], dk: dict, now) -> Panel:
    hb = None
    try:
        hb = _parse_ts((work_dir() / "HEARTBEAT").read_text(encoding="utf-8").strip())
    except OSError:
        pass
    hb_age = _fmt_td(now - hb) if hb else "n/a"
    started = _parse_ts(dk.get("started", "")) if dk.get("status") == "running" else None
    up = _fmt_td(now - started) if started else "-"
    name, desc = PHASES.get(st.phase, ("?", ""))
    status = dk.get("status") or "docker n/a"
    colour = {"running": "green", "paused": "yellow"}.get(status, "red")
    wd = watchdog_running()
    wd_txt = {True: "[green]running[/]", False: "[red]NOT running[/]", None: "n/a"}[wd]
    txt = Text.from_markup(
        f"container [{colour}]{status}[/]  up {up}   heartbeat age {hb_age}   watchdog {wd_txt}   "
        f"cycle {st.cycle_id}   phase {st.phase} = {name}   records {arch.n_records}   stall {st.stall_counter}\n"
        f"[dim]{desc}[/]   now {fmt_ct(now, seconds=True)}"
    )
    return Panel(txt, title="rk run")


def settings_panel(cfg: dict, dk: dict) -> Panel:
    rows: list[tuple[str, str]] = []
    for sec in ("container", "run", "watchdog", "watcher"):
        vals = cfg.get(sec, {})
        if vals:
            rows.append((sec, "  ".join(f"{k}={json.dumps(v)}" for k, v in vals.items())))
    if dk.get("status") in ("running", "paused"):
        rows.append(("live container", f"cpus={dk.get('cpus')} mem={dk.get('memory_gb', 0):.1f}G pids={dk.get('pids_limit')} shares={dk.get('cpu_shares')} image={dk.get('image')}"))
        env = dk.get("env", {})
        rows.append(("live RK_* env", "  ".join(f"{k}={v}" for k, v in sorted(env.items())) or "-"))
    rows.append(("config file", str(_config_path())))
    return _kv_table(rows, "settings (python configure.py explain)")


def progress_panel(st, arch: ArchiveState, events: list[dict], records, now) -> Panel:
    accepted = [e for e in events if e.get("kind") == "accepted"]
    rejected = [e for e in events if e.get("kind") == "rejected"]
    cycles = [e for e in events if e.get("kind") == "cycle_done"]
    recent = [e for e in accepted if (_parse_ts(e.get("ts")) or now) > now - datetime.timedelta(minutes=10)]
    rate_h = len(recent) * 6
    rows: list[tuple[str, str]] = [
        ("cycles done", f"{len(cycles)} (this archive)   last: " + (fmt_ct(cycles[-1].get("ts"), default="?") if cycles else "none")),
        ("candidates", f"accepted {len(accepted)}   rejected {len(rejected)}   rate {rate_h}/h (last 10 min)"),
    ]
    if rejected:
        top = Counter(e.get("code") for e in rejected).most_common(3)
        rows.append(("reject codes", ", ".join(f"{c} x{n}" for c, n in top)))
    enum = [e for e in events if e.get("kind") == "enumeration"]
    if enum and st.phase in (0, 1):
        e = enum[-1]
        remaining = int(e.get("remaining", 0)) - int(e.get("taken", 0))
        eta = f"~{remaining / max(rate_h, 1) * 60:.0f} min" if rate_h else "n/a"
        rows.append(("enumeration", f"phase {e.get('phase')}: {int(e.get('total', 0)) - remaining}/{e.get('total')} visited, {remaining} to go, ETA {eta}"))
    tiers = Counter(r.tier for r in records)
    rows.append(("tiers", "  ".join(f"{k}={v}" for k, v in sorted(tiers.items())) or "-"))
    cov = {o: len(g) for o, g in arch.grids.items()}
    rows.append(("grid coverage", "  ".join(f"order {o}: {n}/40 cells" for o, n in sorted(cov.items()))))
    improved = [e for e in cycles if e.get("improved")]
    rows.append(("last improvement", fmt_ct(improved[-1].get("ts"), default="?") if improved else "none yet"))
    rows.append(("current cell", str(st.current_cell)))
    return _kv_table(rows, "progress")


def working_panel(events: list[dict], hyps: list[dict]) -> Panel:
    def last(kind):
        for e in reversed(events):
            if e.get("kind") == kind:
                return e
        return None
    rows: list[tuple[str, str]] = []
    a = last("action")
    if a:
        rows.append(("encourager", f"{a.get('action')} {json.dumps(a.get('payload'))}  (cycle {a.get('cycle_id')})"))
    d = last("directive_accepted") or last("directive_fallback")
    if d:
        rows.append(("directive", f"{d.get('directive_id')} [{d.get('source', d.get('kind'))}] order {d.get('target_order')} stages {d.get('stages')}"))
        if d.get("rationale"):
            rows.append(("rationale", str(d.get("rationale"))[:300]))
        if d.get("constraints"):
            rows.append(("constraints", json.dumps(d.get("constraints"))[:200]))
        if d.get("hypothesis_id"):
            rows.append(("testing", str(d.get("hypothesis_id"))))
    r = last("directive_rejected")
    if r:
        rows.append(("last rejected directive", str(r.get("error"))[:200]))
    isl = last("island_done")
    if isl:
        rows.append(("last island", f"order {isl.get('order')} stages {isl.get('stages')} seed {isl.get('seed')} yielded {isl.get('yielded')}"))
    e = last("enumeration")
    if e and not isl:
        rows.append(("enumeration batch", f"phase {e.get('phase')}: took {e.get('taken')} of {e.get('remaining')} remaining ({e.get('directive_id')})"))
    lit = last("literature_digest")
    if lit:
        rows.append(("last literature", str(lit.get("topic"))[:110] + " (" + str(lit.get("sources")) + " sources, " + fmt_ct(lit.get("ts")) + ")"))
    interp = last("interpretation_published")
    if interp:
        rows.append(("last interpretation", "cycle " + str(interp.get("cycle")) + " at " + fmt_ct(interp.get("ts")) + " (" + str(interp.get("chars")) + " chars)"))
    open_h = [h for h in hyps if h.get("verdict") is None]
    done_h = [h for h in hyps if h.get("verdict") is not None]
    rows.append(("hypotheses", f"open {len(open_h)}   supported {sum(h.get('verdict') == 'supported' for h in done_h)}   refuted {sum(h.get('verdict') == 'refuted' for h in done_h)}   inconclusive {sum(h.get('verdict') == 'inconclusive' for h in done_h)}"))
    for h in open_h[-3:]:
        rows.append((f"  {h.get('id')}", f"{str(h.get('statement'))[:120]}  |  {h.get('predicate')}  (needs {h.get('min_samples')} samples)"))
    for h in done_h[-2:]:
        rows.append((f"  {h.get('id')} {h.get('verdict')}", f"{str(h.get('statement'))[:100]}  n={h.get('n_samples')} d={h.get('effect_size')}"))
    if not rows:
        rows.append(("state", "no events yet"))
    return _kv_table(rows, "what it is working on")


def llm_panel(events: list[dict], dk: dict) -> Panel:
    def last(kind):
        for e in reversed(events):
            if e.get("kind") == kind:
                return e
        return None
    env = dk.get("env", {})
    rows: list[tuple[str, str]] = [("mode", f"RK_LLM={env.get('RK_LLM', os.environ.get('RK_LLM', '?'))}  model={env.get('RK_LLM_MODEL', 'default')}")]
    calls = [e for e in events if e.get("kind") == "llm_call"]
    rows.append(("directive calls", f"{len(calls)} total; last {fmt_ct(calls[-1].get('ts')) if calls else 'none'}"))
    u = last("codex_usage")
    if u:
        used, window, resets, plan = u.get("used_percent"), u.get("window_minutes"), u.get("resets_at"), u.get("plan_type")
        span = "weekly" if isinstance(window, (int, float)) and window >= 10000 else (f"{int(window) // 60}h" if isinstance(window, (int, float)) else "?")
        when = fmt_ct(resets, default="?") if isinstance(resets, (int, float)) and not isinstance(resets, bool) else "?"
        tok = u.get("tokens") or {}
        rows.append(("codex usage", f"{used}% of the {span} {plan or ''} limit used, resets {when}"))
        rows.append(("last call tokens", f"in {tok.get('input_tokens', 0)} (cached {tok.get('cached_input_tokens', 0)})  out {tok.get('output_tokens', 0)}  reasoning {tok.get('reasoning_output_tokens', 0)}"))
    cd = last("cycle_done")
    if cd and "spend_usd" in cd:
        rows.append(("api spend", f"${float(cd.get('spend_usd', 0)):.4f} of ${float(cd.get('cap_usd', 0)):.2f} cap (metered path only; codex is plan-billed)"))
    sk = last("llm_skipped")
    if sk:
        rows.append(("last skip", f"{sk.get('reason')} at {fmt_ct(sk.get('ts'))}"))
    return _kv_table(rows, "LLM / codex")


def results_panel(arch: ArchiveState, records) -> Panel:
    classical = {}
    try:
        for name, t in tableau_mod.classical().items():
            classical[tableau_mod.content_hash(t)] = name
    except Exception:  # noqa: BLE001
        pass
    base: dict[tuple[int, int, int], tuple[str, float]] = {}
    for r in records:
        name = classical.get(r.tableau_hash)
        if not name:
            continue
        try:
            key = (archive.record_order(r), len(r.tableau.b), archive.cycle_bucket(int(r.score.cycles["m0plus_fast"])))
        except Exception:  # noqa: BLE001
            continue
        if key not in base or r.score.heldout_error < base[key][1]:
            base[key] = (name, r.score.heldout_error)
    t = Table(expand=True, title="per cell: best vs classical baseline")
    for col in ("p", "s", "bucket", "elite heldout", "search", "fast/slow cyc", "tier", "baseline"):
        t.add_column(col)
    for order in sorted(arch.grids):
        for (stg, bucket), rec in sorted(arch.grids[order].items()):
            b = base.get((order, stg, bucket))
            t.add_row(str(order), str(stg), str(bucket), f"{rec.score.heldout_error:.4g}", f"{rec.score.search_error:.4g}",
                      f"{rec.score.cycles.get('m0plus_fast')}/{rec.score.cycles.get('m0plus_slow')}", rec.tier,
                      f"{b[0]} {b[1]:.4g}" if b else "-")
    best = sorted((r for r in records if r.tier == "heldout_verified"), key=lambda r: r.score.heldout_error)[:5]
    t2 = Table(expand=True, title="best heldout_verified records")
    for col in ("hash", "stages", "fast/slow", "heldout", "search", "cycle", "tableau b"):
        t2.add_column(col)
    for r in best:
        t2.add_row(r.tableau_hash[:10], str(len(r.tableau.b)), f"{r.score.cycles.get('m0plus_fast')}/{r.score.cycles.get('m0plus_slow')}",
                   f"{r.score.heldout_error:.4g}", f"{r.score.search_error:.4g}", str(r.cycle_id),
                   "(" + ", ".join(str(x) for x in r.tableau.b) + ")")
    grid = Table.grid(expand=True)
    grid.add_row(t)
    grid.add_row(t2)
    return Panel(grid, title="results")


def health_panel(events: list[dict], now) -> Panel:
    rows: list[tuple[str, str]] = []
    try:
        vh = verifier_hash.compute_verifier_hash()
        pin = verifier_hash.pinned_verifier_hash()
        rows.append(("verifier hash", f"{vh[:16]} " + ("matches pin" if pin == vh else ("NO PIN" if pin is None else "PIN MISMATCH"))))
    except Exception as e:  # noqa: BLE001
        rows.append(("verifier hash", f"unavailable ({e!r})"))
    abandoned = [e for e in events if e.get("kind") == "cycle_abandoned"]
    rows.append(("abandoned cycles", f"{len(abandoned)}" + (f"; last: {str(abandoned[-1].get('error'))[:120]}" if abandoned else "")))
    stops = [e for e in events if str(e.get("kind", "")).startswith("stopped_by") or e.get("kind") == "spend_cap_exceeded"]
    if stops:
        rows.append(("last stop", f"{stops[-1].get('kind')} at {fmt_ct(stops[-1].get('ts'))}"))
    sb = [e for e in events if e.get("kind") == "site_build_failed"]
    if sb:
        rows.append(("site build failures", f"{len(sb)}; last {str(sb[-1].get('error'))[:100]}"))
    try:
        du = shutil.disk_usage(str(work_dir()))
        rows.append(("disk (work drive)", f"{du.free / 1e9:.1f} GB free of {du.total / 1e9:.0f} GB"))
    except OSError:
        pass
    rows.append(("last pushed", f"rk-findings {last_push_time(findings_dir())}   rk-work {last_push_time(work_dir())}"))
    f = work_dir() / "falsification.json"
    if f.exists():
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            rk4 = d.get("methods", {}).get("rk4", {})
            cf = rk4.get("coefficient_fraction", {})
            rows.append(("falsification", f"verdict {d.get('verdict')}; rk4 coefficient fraction fast {cf.get('m0plus_fast', 0):.0%} / slow {cf.get('m0plus_slow', 0):.0%}, crossover h={rk4.get('crossover_h')}"))
        except ValueError:
            pass
    return _kv_table(rows, "health")


def events_panel(events: list[dict], n: int) -> Panel:
    t = Table(expand=True)
    t.add_column("time (CT)", no_wrap=True)
    t.add_column("kind", no_wrap=True)
    t.add_column("detail")
    for e in events[-n:]:
        detail = {k: v for k, v in e.items() if k not in ("ts", "kind")}
        s = json.dumps(detail, default=str)
        ct = to_ct(e.get("ts"))
        t.add_row(ct.strftime("%Y-%m-%d %H:%M:%S") if ct else "", str(e.get("kind")), s[:150])
    return Panel(t, title=f"last {n} events")


def build_layout() -> Layout:
    cfg = load_config()
    now = _now()
    events = load_events()
    dk = docker_info()
    try:
        arch = archive.replay()
        records = archive.read_all()
    except Exception:  # noqa: BLE001
        arch = ArchiveState(0, 0, {1: {}, 2: {}, 3: {}, 4: {}}, (), ())
        records = []
    st = _load_state(arch)
    try:
        hyps = ledger.load_hypotheses()
    except Exception:  # noqa: BLE001
        hyps = []
    n_tail = int(cfg.get("watcher", {}).get("events_tail", 25))
    root = Layout(name="root")
    root.split_column(Layout(name="head", size=4), Layout(name="top", size=14), Layout(name="mid"), Layout(name="bottom", size=n_tail + 4))
    root["top"].split_row(Layout(name="settings", ratio=3), Layout(name="llm", ratio=2))
    root["mid"].split_row(Layout(name="left"), Layout(name="right", ratio=2))
    root["left"].split_column(Layout(name="progress"), Layout(name="working"), Layout(name="health"))
    root["head"].update(header(st, arch, events, dk, now))
    root["settings"].update(settings_panel(cfg, dk))
    root["llm"].update(llm_panel(events, dk))
    root["progress"].update(progress_panel(st, arch, events, records, now))
    root["working"].update(working_panel(events, hyps))
    root["health"].update(health_panel(events, now))
    root["right"].update(results_panel(arch, records))
    root["bottom"].update(events_panel(events, n_tail))
    return root


def render_once(width: int = 160, height: int = 80) -> str:
    console = Console(width=width, height=height, record=True, force_terminal=False)
    console.print(build_layout(), height=height)
    return console.export_text()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rk_harness.watch")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args(argv)
    if args.once:
        base = Console()
        console = Console(width=max(base.width, 160), force_terminal=base.is_terminal)
        console.print(build_layout(), height=max(base.height, 80))
        return 0
    refresh = int(load_config().get("watcher", {}).get("refresh_seconds", 5))
    console = Console()
    try:
        with Live(build_layout(), console=console, screen=True, refresh_per_second=1) as live:
            while True:
                time.sleep(max(1, refresh))
                try:
                    live.update(build_layout())
                except Exception as e:  # noqa: BLE001 — never let the view die on a transient read
                    live.update(Panel(f"refresh failed: {e!r}", title="rk run"))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
