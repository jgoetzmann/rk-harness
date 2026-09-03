#!/usr/bin/env python3
"""Edit the run's operational settings (config.json next to this file).

    python configure.py show                      current values
    python configure.py explain                   every key, meaning, range, and what must restart
    python configure.py set run.auto_stop_minutes=120 container.cpus=6
    python configure.py reset [key ...]           back to the defaults (all keys, or the given ones)
    python configure.py set ... --apply           also stop.ps1 + start.ps1 so the change takes effect

Keys are dotted: <section>.<name>. Values are parsed as JSON where possible (true/false/null,
numbers), otherwise taken as strings. The file is written atomically. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.json"

# key -> (default, type, (min, max) or choices, restart, help)
SCHEMA: dict[str, tuple] = {
    "container.cpus":             (4, float, (0.5, 64), "container", "CPU cores for the container (docker --cpus)"),
    "container.memory_gb":        (6, float, (1, 512), "container", "memory limit in GB (docker --memory)"),
    "container.pids_limit":       (512, int, (64, 65536), "container", "max processes in the container (fork-bomb guard)"),
    "container.cpu_shares":       (256, int, (2, 262144), "container", "CPU weight vs other processes; 1024 = default, 256 = a quarter"),
    "container.scratch_tmpfs_gb": (2, float, (0.1, 64), "container", "size of the RAM-backed /scratch (docker --tmpfs)"),
    "run.llm":                    ("auto", str, ("auto", "codex", "on", "off"), "container",
                                   "LLM for directives: codex (mounted ~/.codex/auth.json), on (API key), off (deterministic fallback), auto = codex if auth.json exists else off"),
    "run.llm_model":              ("", str, None, "container", "model override (empty = the CLI/API default)"),
    "run.eval_budget":            (200, int, (10, 100000), "container", "CMA-ES fitness evaluations per island per cycle (phases 2-3)"),
    "run.llm_every_cycles":       (5, int, (1, 1000), "container", "ask the LLM for a fresh directive every N cycles (and on every escalation); in between the last directive is reused"),
    "run.codex_usage_cap_percent": (80, int, (1, 100), "container", "skip LLM calls once Codex reports this much of the weekly plan limit used (falls back to the deterministic directive)"),
    "run.litreview_every_cycles": (50, int, (0, 100000), "container", "every N cycles, one web-searched literature digest (codex only; 0 = off); digests feed the directive/hypothesis prompts and the literature page"),
    "run.interpret_every_cycles": (25, int, (0, 100000), "container", "every N cycles, a model-written interpretation of the archive published on the findings site (0 = off)"),
    "run.enum_per_cycle":         (500, int, (1, 100000), "container", "enumerated candidates verified per cycle (phases 0-1)"),
    "run.auto_stop_minutes":      (0, int, (0, 1000000), "container", "stop the runner after this many minutes of wall clock (0 = never); it exits at the next cycle boundary"),
    "run.auto_stop_cycles":       (0, int, (0, 1000000), "container", "stop the runner after this many cycles in this process (0 = never)"),
    "run.site":                   (True, bool, None, "container", "regenerate the findings site every cycle"),
    "run.git_commit":             (True, bool, None, "container", "commit rk-work / rk-findings inside the container (the host watchdog pushes)"),
    "run.initial_phase":          (None, "int_or_null", (0, 3), "container", "phase to start from when RUNSTATE.json is absent (null = 0); does not override an existing state"),
    "watchdog.poll_seconds":      (10, int, (2, 600), "watchdog", "how often the host watchdog checks everything"),
    "watchdog.heartbeat_stale_seconds": (120, int, (30, 3600), "watchdog", "docker kill when HEARTBEAT is older than this"),
    "watchdog.min_free_gb":       (5.0, float, (0.5, 1000), "watchdog", "docker stop when free disk on the work drive drops below this"),
    "watchdog.no_candidate_minutes": (30, int, (1, 100000), "watchdog", "print an alert when no candidate was accepted for this long"),
    "watchdog.push_minutes":      (10, int, (1, 1440), "watchdog", "push rk-work / rk-findings from the host this often"),
    "watchdog.battery_guard":     (True, bool, None, "watchdog", "pause the container while the laptop is on battery"),
    "watchdog.cpu_pause_high_percent": (70, int, (5, 100), "watchdog", "pause when non-container host CPU stays above this"),
    "watchdog.cpu_pause_low_percent":  (30, int, (0, 99), "watchdog", "unpause when host CPU stays below this"),
    "watchdog.cpu_pause_sustain_seconds": (30, int, (5, 3600), "watchdog", "how long the CPU condition must hold before pausing/unpausing"),
    "watchdog.cpu_pause_high_avg_percent": (60, int, (10, 100), "watchdog", "pause when the rolling CPU average tops this"),
    "watchdog.cpu_pause_low_avg_percent": (40, int, (5, 95), "watchdog", "resume when the rolling CPU average is under this"),
    "watchdog.cpu_avg_window_seconds": (300, int, (60, 3600), "watchdog", "window for the rolling CPU average triggers"),
    "watchdog.saturation_check_seconds": (1800, int, (0, 86400), "watchdog", "epoch-saturation orchestrator cadence in seconds, 0 disables; freeze rule in rk-harness/docs/ROADMAP.md"),
    "watchdog.auto_freeze": (False, bool, None, "watchdog", "let the orchestrator freeze a saturated epoch on its own; off = advisory logging only (owner ruling 2026-09-03)"),
    "watcher.refresh_seconds":    (5, int, (1, 300), "watcher", "watcher window refresh interval"),
    "watcher.events_tail":        (25, int, (5, 200), "watcher", "how many recent events the watcher shows"),
}
RESTART_HINT = {
    "container": "takes effect on the next start: python configure.py ... --apply  (or .\\stop.ps1 then .\\start.ps1)",
    "watchdog": "takes effect when the watchdog restarts (.\\start.ps1 restarts it)",
    "watcher": "takes effect when the watcher window restarts (.\\watcher.ps1)",
}


def defaults() -> dict:
    out: dict = {}
    for key, (default, *_rest) in SCHEMA.items():
        sec, name = key.split(".", 1)
        out.setdefault(sec, {})[name] = default
    return out


def load() -> dict:
    cfg = defaults()
    if CONFIG.exists():
        try:
            data = json.loads(CONFIG.read_text(encoding="utf-8"))
        except ValueError as e:
            sys.exit(f"config.json is not valid JSON: {e}")
        for sec, vals in data.items():
            if sec.startswith("_") or not isinstance(vals, dict):
                continue
            for name, v in vals.items():
                if f"{sec}.{name}" in SCHEMA:
                    cfg.setdefault(sec, {})[name] = v
    return cfg


def save(cfg: dict) -> None:
    body = {"_comment": "Operational settings for the rk run. Edit with configure.py (python configure.py explain). "
                        "Scientific thresholds (UNSTABLE, overflow margin, cost tables) are NOT here on purpose: "
                        "changing them invalidates the archive and the verifier hash."}
    body.update(cfg)
    tmp = CONFIG.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, CONFIG)


def coerce(key: str, raw) -> object:
    default, typ, rng, _restart, _help = SCHEMA[key]
    if isinstance(raw, str):
        try:
            val = json.loads(raw)
        except ValueError:
            val = raw
    else:
        val = raw
    if typ == "int_or_null":
        if val is None or val == "null":
            return None
        if isinstance(val, bool) or not isinstance(val, (int, float)) or int(val) != val:
            raise ValueError(f"{key} must be an integer or null")
        val = int(val)
        lo, hi = rng
        if not lo <= val <= hi:
            raise ValueError(f"{key} must be between {lo} and {hi}")
        return val
    if typ is bool:
        if isinstance(val, bool):
            return val
        if isinstance(val, str) and val.lower() in ("true", "yes", "on", "1"):
            return True
        if isinstance(val, str) and val.lower() in ("false", "no", "off", "0"):
            return False
        raise ValueError(f"{key} must be true or false")
    if typ is int:
        if isinstance(val, bool) or not isinstance(val, (int, float)) or int(val) != val:
            raise ValueError(f"{key} must be an integer")
        val = int(val)
    elif typ is float:
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            raise ValueError(f"{key} must be a number")
        val = float(val)
        if val == int(val):
            val = int(val)
    elif typ is str:
        val = str(val)
    if isinstance(rng, tuple) and typ in (int, float):
        lo, hi = rng
        if not lo <= val <= hi:
            raise ValueError(f"{key} must be between {lo} and {hi}")
    if isinstance(rng, tuple) and typ is str and val not in rng:
        raise ValueError(f"{key} must be one of {', '.join(rng)}")
    if key == "watchdog.cpu_pause_low_percent" or key == "watchdog.cpu_pause_high_percent":
        pass
    return val


def get(cfg: dict, key: str):
    sec, name = key.split(".", 1)
    return cfg[sec][name]


def put(cfg: dict, key: str, val) -> None:
    sec, name = key.split(".", 1)
    cfg.setdefault(sec, {})[name] = val


def cmd_show(cfg: dict) -> None:
    width = max(len(k) for k in SCHEMA)
    for key in SCHEMA:
        val = get(cfg, key)
        default = SCHEMA[key][0]
        mark = "" if val == default else f"   (default {json.dumps(default)})"
        print(f"{key.ljust(width)}  {json.dumps(val)}{mark}")
    print(f"\nfile: {CONFIG}")


def cmd_explain() -> None:
    for key, (default, typ, rng, restart, help_) in SCHEMA.items():
        if isinstance(rng, tuple) and typ in (int, float, "int_or_null"):
            span = f"{rng[0]}..{rng[1]}"
        elif isinstance(rng, tuple):
            span = "|".join(rng)
        else:
            span = {bool: "true|false", str: "text"}.get(typ, "")
        print(f"{key}\n    {help_}\n    default {json.dumps(default)}   allowed {span}\n    {RESTART_HINT[restart]}")


def cmd_set(cfg: dict, pairs: list[str]) -> set[str]:
    touched: set[str] = set()
    for pair in pairs:
        if "=" not in pair:
            sys.exit(f"expected key=value, got {pair!r}")
        key, raw = pair.split("=", 1)
        key = key.strip()
        if key not in SCHEMA:
            sys.exit(f"unknown key {key!r}; run: python configure.py explain")
        try:
            val = coerce(key, raw.strip())
        except ValueError as e:
            sys.exit(str(e))
        put(cfg, key, val)
        touched.add(SCHEMA[key][3])
        print(f"{key} = {json.dumps(val)}")
    low, high = cfg["watchdog"]["cpu_pause_low_percent"], cfg["watchdog"]["cpu_pause_high_percent"]
    if low >= high:
        sys.exit(f"watchdog.cpu_pause_low_percent ({low}) must be below cpu_pause_high_percent ({high})")
    save(cfg)
    for r in sorted(touched):
        print(f"-> {RESTART_HINT[r]}")
    return touched


def cmd_reset(cfg: dict, keys: list[str]) -> None:
    if not keys:
        save(defaults())
        print("all keys reset to defaults")
        return
    for key in keys:
        if key not in SCHEMA:
            sys.exit(f"unknown key {key!r}")
        put(cfg, key, SCHEMA[key][0])
        print(f"{key} = {json.dumps(SCHEMA[key][0])}")
    save(cfg)


def apply() -> None:
    ps = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
    print("stopping (graceful, at the next cycle boundary) ...")
    subprocess.run(ps + [str(ROOT / "stop.ps1")], check=False)
    print("starting ...")
    subprocess.run(ps + [str(ROOT / "start.ps1")], check=False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("show")
    sub.add_parser("explain")
    p_set = sub.add_parser("set")
    p_set.add_argument("pairs", nargs="+")
    p_set.add_argument("--apply", action="store_true", help="stop.ps1 + start.ps1 afterwards")
    p_reset = sub.add_parser("reset")
    p_reset.add_argument("keys", nargs="*")
    p_reset.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    cfg = load()
    if args.cmd in (None, "show"):
        cmd_show(cfg)
    elif args.cmd == "explain":
        cmd_explain()
    elif args.cmd == "set":
        cmd_set(cfg, args.pairs)
        if args.apply:
            apply()
    elif args.cmd == "reset":
        cmd_reset(cfg, args.keys)
        if args.apply:
            apply()
    return 0


if __name__ == "__main__":
    sys.exit(main())
