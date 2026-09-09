"""Host-side status snapshot: is the run alive, what is it doing, what is this machine doing.

Writes a plain-text file a human can open in Notepad (workspace stats.txt) and backs the
watcher's machine panel, so the two cannot disagree.

Why this lives on the host and not in the container: a file the container writes cannot
report that the container is dead. Everything here works with the run stopped, paused,
missing, or with Docker itself broken.

Three rules the file keeps, because a status file that lies is worse than no status file:

1. No value is ever carried forward. If a probe fails this pass the field prints "unknown"
   and the reason lands in PROBLEMS. There is no cache to go stale.
2. Nothing is claimed about the container unless Docker actually answered. A daemon that
   times out or returns an error is its own verdict, distinct from "not running" - a
   distinction that matters, because "not running" invites the reader to run start.ps1 and
   recreate a container that is alive.
3. The liveness deadline is printed in this machine's own local time as well as UTC. The
   harness displays US Central everywhere else (HANDOFF timezone policy), but the deadline
   exists to be compared against the reader's taskbar clock, and this machine is not on
   Central time.

Those three rules are what the lane rows obey too. A cycle used to be one thing, an explicit
search, and every counter here still reads that way; under a lane schedule a cycle can be any
of three, and one that spent its budget on the adaptive or implicit lane appends nothing to
the scored archive because that is what it was told to do. So the schedule is only claimed
when Docker actually answered, the split that was MEASURED comes from
rk-work/schedule/shares.json and from nowhere else, and the split the schedule asks for is
never printed in its place. All of it tolerates rk-work/schedule/ not existing, which is the
state while the rotation ships disarmed.

This module must not import runner (viewers read state files; K13), and must import
cleanly on Linux for CI - every Windows call is looked up lazily and returns None
elsewhere.
"""
from __future__ import annotations

import datetime
import json
import os
import platform
import re
import shutil
import subprocess
import textwrap
import time
from pathlib import Path

from rk_harness import lanes
from rk_harness.paths import findings_dir, work_dir
from rk_harness.timefmt import fmt_ct

# Probe timeouts. Docker on this machine has been observed taking 47 s and returning HTTP
# 500 on every endpoint, so nothing may block unbounded: a slow probe must degrade to
# "unknown" long before it delays the file.
DOCKER_TIMEOUT_S = 6.0
GPU_TIMEOUT_S = 5.0
# A restart count at or above this is flagged in the census. It is a single-sample fact, not
# a trend: see probe_containers for why "climbing" cannot be computed here.
RESTART_FLAG = 20
# events.jsonl grows ~5 MB/day with no rotation and is already 35 MB. Only the tail is
# ever read, so cadence stays cheap as the file grows without bound.
EVENTS_TAIL_BYTES = 512 * 1024
# Cadence needs a handful of cycle_done events; a two-window comparison needs at least
# ACCEPT_MIN_SAMPLES in each window. Measured 2026-09-08: 200 KB of the live stream carried
# five cycle_done, so the 512 KB cadence tail holds about a dozen - not enough to compare two
# windows. The productivity read therefore takes a wider tail and json-parses only the kinds
# it asked for; watch.py has read 4 MB every few seconds for months for the same reason.
PRODUCTIVITY_TAIL_BYTES = 4 * 1024 * 1024
ACCEPT_MIN_SAMPLES = 10
ACCEPT_COLLAPSE_RATIO = 0.5
WATCHDOG_LOG_TAIL_BYTES = 64 * 1024
WATCHDOG_LOG_LINES = 5
# The run refreshes rk-work/schedule/shares.json as a cycle ends. A document further behind
# RUNSTATE.json than this is not describing the present, and printing its numbers would be
# carrying a value forward under another name. Twenty cycles is about an hour at the 176 s
# cadence measured 2026-09-09: wide enough that a cycle boundary landing between two reads
# is never a fault, tight enough that a writer that has stopped shows up within the hour.
LANE_SHARES_STALE_CYCLES = 20
# The value column stats.txt gives a row: 2 spaces + a 14-character label + this is 78, the
# width the rest of the file is written to. Lane values are wrapped to it here rather than
# in the renderer, so the watcher gets the same lines the file gets.
LANE_VALUE_W = 62

WINDOWS = platform.system() == "Windows"
_ZERO_TIMES = ("0001-01-01T00:00:00Z", "0001-01-01T00:00:00")


# ----------------------------------------------------------------------------- helpers

def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_ts(s) -> datetime.datetime | None:
    """Parse the two timestamp encodings in the run: '...Z' and '...+00:00'.

    Docker's zero value (year 0001, meaning "never") is treated as absent; printed
    literally it renders as a plausible-looking date and an uptime of ~2000 years.
    """
    if not s or not isinstance(s, str):
        return None
    if s.startswith("0001-01-01"):
        return None
    txt = s.strip()
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    try:
        d = datetime.datetime.fromisoformat(txt)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)


def _age(then: datetime.datetime | None, now: datetime.datetime) -> float | None:
    return None if then is None else (now - then).total_seconds()


def _dur(seconds: float | None) -> str:
    """'3d 4h 12m', '7m 41s', '12s'. Coarse on purpose: this is read, not computed with."""
    if seconds is None:
        return "unknown"
    s = int(abs(seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d}d {h}h {m}m"
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _ascii(s) -> str:
    """Anything from outside this project, made safe for a file test S11 asserts is ASCII.

    write() already falls back to errors='replace', but render_text is asserted ASCII
    directly, and a third-party container name is not under this project's control.
    """
    return str(s).encode("ascii", "replace").decode("ascii")


def _num(v) -> str:
    """A count read by a human: '3', not '3.0'. Halves survive as halves."""
    if v is None:
        return "unknown"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if f.is_integer() else "{:.1f}".format(f)


def _ct(ts) -> str:
    try:
        out = fmt_ct(ts)
    except Exception:                                        # noqa: BLE001
        return "unknown"
    return out or "unknown"


def _local(dt: datetime.datetime) -> tuple[str, str]:
    """(wall-clock string, tz abbreviation) in this machine's own timezone."""
    loc = dt.astimezone()
    return loc.strftime("%Y-%m-%d %H:%M:%S"), (loc.tzname() or "local")


def _run(cmd: list[str], timeout: float) -> tuple[int | None, str, str]:
    """(returncode, stdout, stderr). returncode is None if it timed out or never started."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired:
        return None, "", f"timed out after {timeout:g}s"
    except Exception as exc:                                 # noqa: BLE001
        return None, "", f"{type(exc).__name__}: {exc}"


# ----------------------------------------------------------------------------- windows

def _kernel32():
    if not WINDOWS:
        return None
    try:
        import ctypes
        return ctypes.WinDLL("kernel32", use_last_error=True)
    except Exception:                                        # noqa: BLE001
        return None


def host_cpu_percent(sample_seconds: float = 0.25) -> float | None:
    """Total host CPU over a short window, sampled twice inside this process.

    Deliberately not cached between runs. A persisted baseline shared by the watcher and a
    one-shot writer produces a delta over whichever interval happened to elapse, so an
    overnight gap renders the night's average as an instantaneous reading.
    """
    k = _kernel32()
    if k is None:
        return None
    import ctypes
    from ctypes import wintypes

    def times():
        idle, kern, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        if not k.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):
            return None
        def q(ft):
            return (ft.dwHighDateTime << 32) | ft.dwLowDateTime
        return q(idle), q(kern), q(user)

    a = times()
    if a is None:
        return None
    time.sleep(max(0.05, sample_seconds))
    b = times()
    if b is None:
        return None
    idle = b[0] - a[0]
    total = (b[1] - a[1]) + (b[2] - a[2])          # kernel already includes idle
    if total <= 0:
        return None
    return round(max(0.0, min(100.0, (1.0 - idle / total) * 100.0)), 1)


def host_memory() -> dict | None:
    k = _kernel32()
    if k is None:
        return None
    import ctypes
    from ctypes import wintypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    m = MEMORYSTATUSEX()
    m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not k.GlobalMemoryStatusEx(ctypes.byref(m)):
        return None
    return {"percent_used": int(m.dwMemoryLoad),
            "free_gb": round(m.ullAvailPhys / 2**30, 2),
            "total_gb": round(m.ullTotalPhys / 2**30, 2)}


def host_power() -> dict | None:
    """AC/battery. BatteryLifePercent is a BYTE where 255 means "unknown", not 255%."""
    k = _kernel32()
    if k is None:
        return None
    import ctypes

    class SPS(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                    ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                    ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]

    s = SPS()
    if not k.GetSystemPowerStatus(ctypes.byref(s)):
        return None
    ac = {0: "battery", 1: "AC"}.get(s.ACLineStatus)
    pct = None if s.BatteryLifePercent in (255,) else int(s.BatteryLifePercent)
    return {"source": ac or "unknown", "battery_percent": pct, "on_ac": s.ACLineStatus == 1}


# ----------------------------------------------------------------------------- probes

def probe_docker(name: str = "rk") -> dict:
    """Container state via the docker CLI, with a hard timeout.

    Distinguishes "Docker answered and there is no such container" from "Docker did not
    answer", because only the first justifies telling someone to start the run.
    """
    out: dict = {"probe": "docker inspect", "ok": False}
    if not shutil.which("docker"):
        out["error"] = "the docker command is not on PATH"
        out["state"] = "DOCKER_ABSENT"
        return out
    t0 = time.monotonic()
    rc, so, se = _run(["docker", "inspect", name], DOCKER_TIMEOUT_S)
    out["latency_ms"] = int((time.monotonic() - t0) * 1000)
    err = (se or "").strip().splitlines()
    err = err[-1][:160] if err else ""
    if rc is None:
        out["state"] = "DOCKER_UNREACHABLE"
        out["error"] = se.strip()[:160] or "docker did not respond"
        return out
    if rc != 0:
        low = (se or "").lower()
        if "no such object" in low or "no such container" in low:
            out["state"] = "ABSENT"
            out["ok"] = True                                 # a definite answer
            return out
        out["state"] = "DOCKER_ERROR"
        out["error"] = err or f"docker exited {rc}"
        return out
    try:
        d = json.loads(so)[0]
    except Exception as exc:                                 # noqa: BLE001
        out["state"] = "DOCKER_ERROR"
        out["error"] = f"could not parse docker output: {type(exc).__name__}"
        return out
    st = d.get("State", {}) or {}
    hc = d.get("HostConfig", {}) or {}
    status = str(st.get("Status") or "unknown")
    out.update({
        "ok": True,
        "state": status.upper(),
        "status": status,
        "started_at": None if str(st.get("StartedAt", "")) in _ZERO_TIMES else st.get("StartedAt"),
        "finished_at": None if str(st.get("FinishedAt", "")) in _ZERO_TIMES else st.get("FinishedAt"),
        "restart_count": d.get("RestartCount"),
        "restart_policy": f'{(hc.get("RestartPolicy") or {}).get("Name", "")}'
                          f':{(hc.get("RestartPolicy") or {}).get("MaximumRetryCount", 0)}',
        "cpus": round((hc.get("NanoCpus") or 0) / 1e9, 2) or None,
        "memory_gb": round((hc.get("Memory") or 0) / 2**30, 2) or None,
        "image": (d.get("Config", {}) or {}).get("Image"),
        "env": {e.split("=", 1)[0]: e.split("=", 1)[1]
                for e in ((d.get("Config", {}) or {}).get("Env") or []) if e.startswith("RK_")},
    })
    # Only meaningful once the container has actually stopped; 0 on a running container
    # reads as a clean exit that never happened.
    if status in ("exited", "dead"):
        out["exit_code"] = st.get("ExitCode")
        out["oom_killed"] = bool(st.get("OOMKilled"))
    return out


def _flagged(row: dict) -> bool:
    return (row.get("status") == "restarting"
            or row.get("health") == "unhealthy"
            or (row.get("restart_count") or 0) >= RESTART_FLAG)


def probe_containers(timeout: float = DOCKER_TIMEOUT_S) -> dict:
    """Every container on this daemon, not just rk's own.

    The machine is shared. A third-party stack restarting in a loop takes CPU the pause
    guard then charges to "foreground load", and an unhealthy container next to the run is
    the kind of fact neither harness's status file states today.

    Two docker calls, both with a hard timeout, and never docker stats: this file is written
    on a timer and nothing on that path may block on a probe that samples every container.

    Restart counts are reported as of this pass and never as a trend. Rule 1 forbids
    carrying a value forward and this module keeps no cache, so "climbing" is not something
    it can honestly say. What it flags instead are single-sample facts: a restarting status,
    an unhealthy health check, or a count already past RESTART_FLAG. Do not add a history
    file to make "climbing" work; the absence of a cache is the property, not an oversight.
    """
    out: dict = {"probe": "docker ps + docker inspect", "ok": False, "containers": []}
    if not shutil.which("docker"):
        out["error"] = "the docker command is not on PATH"
        return out

    def _last_error(rc, se, what):
        line = [x for x in (se or "").strip().splitlines() if x.strip()]
        if line:
            return line[-1][:160]
        return "docker did not respond" if rc is None else "{} exited {}".format(what, rc)

    rc, so, se = _run(["docker", "ps", "--format", "{{.Names}}"], timeout)
    if rc is None or rc != 0:
        # A silent daemon is its own verdict. It must never render as "no containers".
        out["error"] = _last_error(rc, se, "docker ps")
        return out
    names = [n.strip() for n in (so or "").splitlines() if n.strip()]
    if not names:
        out["ok"] = True
        out["flagged"] = []
        return out

    rc, so, se = _run(["docker", "inspect", *names], timeout)
    if rc is None or rc != 0:
        out["error"] = _last_error(rc, se, "docker inspect")
        return out
    try:
        data = json.loads(so)
    except Exception as exc:                                 # noqa: BLE001
        out["error"] = "could not parse docker inspect output: {}".format(type(exc).__name__)
        return out
    rows = []
    for d in (data if isinstance(data, list) else []):
        if not isinstance(d, dict):
            continue
        st = d.get("State") or {}
        rows.append({"name": str(d.get("Name") or "?").lstrip("/"),
                     "status": st.get("Status"),
                     "health": (st.get("Health") or {}).get("Status"),
                     "restart_count": d.get("RestartCount"),
                     "image": (d.get("Config") or {}).get("Image"),
                     "started_at": None if str(st.get("StartedAt", "")) in _ZERO_TIMES
                                   else st.get("StartedAt")})
    rows.sort(key=lambda r: r["name"])
    out["ok"] = True
    out["containers"] = rows
    out["flagged"] = [r["name"] for r in rows if _flagged(r)]
    return out


def probe_gpu(skip: bool = False) -> dict:
    """Whole-machine GPU via nvidia-smi.

    Per-process attribution is not available: on this WDDM laptop
    --query-compute-apps returns [N/A] for used_gpu_memory on every row. The container is
    CPU-only, so this reports what else on the machine is busy, not the run's own use.
    """
    out: dict = {"probe": "nvidia-smi", "ok": False}
    if skip:
        out["skipped"] = "on battery"
        return out
    if not shutil.which("nvidia-smi"):
        out["error"] = "nvidia-smi is not on PATH (no NVIDIA driver, or not installed)"
        return out
    fields = ("name,utilization.gpu,memory.used,memory.total,temperature.gpu,"
              "power.draw,enforced.power.limit,pstate")
    t0 = time.monotonic()
    rc, so, se = _run(["nvidia-smi", f"--query-gpu={fields}",
                       "--format=csv,noheader,nounits"], GPU_TIMEOUT_S)
    out["latency_ms"] = int((time.monotonic() - t0) * 1000)
    if rc is None or rc != 0:
        out["error"] = (se.strip()[:160] or f"nvidia-smi exited {rc}")
        return out
    line = (so or "").strip().splitlines()
    if not line:
        out["error"] = "nvidia-smi returned nothing"
        return out
    parts = [p.strip() for p in line[0].split(",")]
    if len(parts) < 8:
        out["error"] = f"unexpected nvidia-smi output: {line[0][:80]}"
        return out

    def num(v):
        try:
            return float(v)
        except ValueError:
            return None

    out.update({"ok": True, "sampled_at": _utcnow().replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "name": parts[0], "util_percent": num(parts[1]),
                "memory_used_mib": num(parts[2]), "memory_total_mib": num(parts[3]),
                "temp_c": num(parts[4]), "power_w": num(parts[5]),
                "power_limit_w": num(parts[6]), "pstate": parts[7]})
    return out


def probe_disks(paths: list[Path]) -> list[dict]:
    seen, out = set(), []
    for p in paths:
        try:
            root = Path(os.path.splitdrive(str(Path(p).resolve()))[0] or "/") \
                if WINDOWS else Path("/")
            root = Path(str(root) + os.sep) if WINDOWS else root
            key = str(root).upper()
            if key in seen:
                continue
            seen.add(key)
            u = shutil.disk_usage(str(root))
            out.append({"mount": str(root), "free_gb": round(u.free / 2**30, 1),
                        "total_gb": round(u.total / 2**30, 1),
                        "percent_free": round(100.0 * u.free / u.total, 1) if u.total else None})
        except Exception:                                    # noqa: BLE001
            continue
    return out


# ----------------------------------------------------------------------------- run state

def _read_json(path: Path) -> tuple[dict | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, f"{path.name} is absent"
    except Exception as exc:                                 # noqa: BLE001
        return None, f"{path.name}: {type(exc).__name__}"


def tail_events(path: Path, max_bytes: int = EVENTS_TAIL_BYTES,
                kinds: tuple[str, ...] | None = None) -> tuple[list[dict], str | None]:
    """Parse only the last max_bytes of events.jsonl.

    The file has no rotation and grows about 5 MB/day; a full scan already costs ~1.5 s and
    gets worse forever, which is not a price a status refresh should pay.

    kinds is a cheap substring gate applied before json.loads, so a wide tail can be read
    without parsing every dict in it; the kind is confirmed after parsing by the callers
    that care. kinds=None keeps the original behaviour.
    """
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()                                # discard the partial line
            raw = fh.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return [], "events.jsonl is absent"
    except Exception as exc:                                 # noqa: BLE001
        return [], f"events.jsonl: {type(exc).__name__}"
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if kinds and not any(k in line for k in kinds):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict):
            out.append(ev)
    return out, None


def cycle_cadence(events: list[dict], n: int = 20) -> dict:
    """Median seconds between recent cycle_done events, and when the last one landed."""
    ts = [_parse_ts(e.get("ts")) for e in events if e.get("kind") == "cycle_done"]
    ts = [t for t in ts if t is not None]
    out: dict = {"samples": 0, "last": ts[-1] if ts else None}
    if len(ts) < 2:
        return out
    ts = ts[-(n + 1):]
    gaps = sorted((ts[i + 1] - ts[i]).total_seconds() for i in range(len(ts) - 1))
    mid = len(gaps) // 2
    out["samples"] = len(gaps)
    out["median_s"] = gaps[mid] if len(gaps) % 2 else (gaps[mid - 1] + gaps[mid]) / 2.0
    return out


def _median(values: list[float]) -> float | None:
    vals = sorted(values)
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def accept_rate(events: list[dict], min_samples: int = ACCEPT_MIN_SAMPLES,
                lane: str = "explicit") -> dict:
    """The median of the newest third of the cycle_done events in the tail against the
    median of the oldest third, with both sample counts.

    Both counts are reported because a byte window is not a cycle window: phase 0 and 1
    cycles enumerate in bulk and occupy more bytes each, so the two thirds can straddle a
    phase change. A verdict is only offered once each window holds min_samples cycles, and
    the row prints the two medians whether or not a verdict came with them - a phase change
    has to be able to read as a phase change rather than as a collapse.

    Counted by lane, but only once the stream says which lane a cycle was. No cycle_done
    carries a lane today and every one of them is counted, which is the run as it stands.
    Once they do, a cycle told to spend its budget on the adaptive or implicit lane appends
    nothing to the scored archive by design; folding those zeros in would put the median on
    the floor and report a collapse that is the schedule working exactly as instructed. A
    cycle_done with no lane at all is still counted whatever else is in the tail, because a
    row written before the rotation existed was an explicit cycle.
    """
    dones = [e for e in events if e.get("kind") == "cycle_done"]
    lane_aware = any("lane" in e for e in dones)
    skipped = 0
    vals: list[float] = []
    for e in dones:
        if lane_aware and e.get("lane") not in (None, lane):
            skipped += 1
            continue
        v = e.get("accepted")
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        vals.append(float(v))
    out = {"cycles": len(vals), "older_n": 0, "recent_n": 0, "older_median": None,
           "recent_median": None, "enough": False, "collapsed": False,
           "lane_aware": lane_aware, "lane": lane, "other_lane_cycles": skipped}
    third = len(vals) // 3
    if third == 0:
        return out
    older, recent = vals[:third], vals[-third:]
    out["older_n"], out["recent_n"] = len(older), len(recent)
    out["older_median"], out["recent_median"] = _median(older), _median(recent)
    out["enough"] = len(recent) >= min_samples and len(older) >= min_samples
    # Never a ratio: a zero baseline is not an infinite collapse, and a rise is not one at all.
    out["collapsed"] = bool(out["enough"] and out["older_median"] > 0
                            and out["recent_median"] < out["older_median"] * ACCEPT_COLLAPSE_RATIO)
    return out


def directive_gap(events: list[dict]) -> dict:
    """How many cycles since the model itself last wrote a directive, plus the plan snapshot.

    Only directive_accepted with source='llm' counts. The fallback path logs
    directive_fallback and never writes LAST_DIRECTIVE.json, so it is the one event that
    means the model actually spoke. With no such event in the tail the count degrades to
    'at least N' rather than to a number that would be wrong.
    """
    last_i, last_at = None, None
    for i, e in enumerate(events):
        if e.get("kind") == "directive_accepted" and e.get("source") == "llm":
            last_i, last_at = i, e.get("ts")
    total = sum(1 for e in events if e.get("kind") == "cycle_done")
    if last_i is None:
        cycles, at_least = total, True
    else:
        cycles = sum(1 for e in events[last_i + 1:] if e.get("kind") == "cycle_done")
        at_least = False

    snapshot: dict = {}
    for e in events:
        if e.get("kind") in ("codex_usage", "llm_skipped") and e.get("used_percent") is not None:
            snapshot = {"used_percent": e.get("used_percent"),
                        "window_minutes": e.get("window_minutes"),
                        "resets_at": e.get("resets_at"),
                        "plan_type": e.get("plan_type"),
                        "snapshot_age_s": e.get("snapshot_age_s")}
    age_s, window_m = snapshot.get("snapshot_age_s"), snapshot.get("window_minutes")
    stale = bool(isinstance(age_s, (int, float)) and not isinstance(age_s, bool)
                 and isinstance(window_m, (int, float)) and not isinstance(window_m, bool)
                 and age_s > float(window_m) * 60.0)

    last_skip: dict = {}
    for e in events:
        if e.get("kind") == "llm_skipped":
            last_skip = {"gate": e.get("gate"), "reason": e.get("reason"), "ts": e.get("ts")}
    return {"cycles": cycles, "at_least": at_least, "last_at": last_at,
            "snapshot": snapshot, "stale_snapshot": stale, "last_skip": last_skip}


def _lane_pcts(shares: dict) -> str:
    """'explicit 70%, adaptive 15%, implicit 15%', always all three lanes in LANES order."""
    return ", ".join("{} {:.0f}%".format(lane, 100.0 * float(shares.get(lane, 0.0) or 0.0))
                     for lane in lanes.LANES)


# How the schedule was learned. Short tokens so a row can name the source without spending
# its whole width on it; the sentences live in lane_rows.
_LANE_SOURCE_TEXT = {
    "shares": "named in shares.json",
    "env": "set in the container env",
    "env-unset": "container sets none",
    "env-bad": "container value rejected",
}


def read_lanes(work: Path | None = None, docker_env: dict | None = None,
               current_cycle=None) -> dict:
    """The lane schedule in force, and the split that was actually measured.

    Two questions, kept apart the way rk_harness.lanes keeps them apart. What the schedule
    ASKS FOR is arithmetic on a string and is always available. What the machine DID is only
    ever read from rk-work/schedule/shares.json, which the run writes; with no such file the
    measured split is unknown and stays unknown. The scheduled split is never printed in its
    place, because the survey behind the rotation found a run whose intended 70/15/15 had in
    fact been 0.0005 percent with nothing in a position to notice.

    docker_env is the container's RK_* environment when Docker actually answered, and None
    when it did not. None is not an empty dict: empty means Docker answered and
    RK_LANE_SCHEDULE is unset, which is a definite answer and means the default schedule.
    None means nothing on this machine can say what the container is running, and then the
    schedule prints unknown - rule 2 of this file, applied to one more field.

    Everything here tolerates rk-work/schedule/ not existing at all, which is the state
    today: the rotation ships disarmed and the directory arrives with it.
    """
    work = Path(work) if work else work_dir()
    path = work / "schedule" / "shares.json"
    out: dict = {"path": str(path), "exists": path.exists(), "error": None, "fault": False,
                 "schedule": None, "schedule_source": None, "armed": None,
                 "scheduled_share": None, "measured": None, "cycles": None,
                 "window_cycles": None, "generated_cycle": None, "behind_cycles": None,
                 "stale": False, "problem": None, "schedule_problem": None}

    doc = None
    if out["exists"]:
        doc, err = _read_json(path)
        if err or not isinstance(doc, dict):
            doc, out["error"], out["fault"] = None, err or "shares.json is not an object", True
        elif (doc.get("_meta") or {}).get("schema") != lanes.SHARES_SCHEMA:
            doc, out["fault"] = None, True
            out["error"] = "shares.json is not " + lanes.SHARES_SCHEMA
    else:
        out["error"] = "shares.json is absent"

    meta = (doc or {}).get("_meta") or {}
    raw = meta.get("schedule")
    if isinstance(raw, str) and lanes.schedule_problem(raw) is None:
        out["schedule"], out["schedule_source"] = lanes.parse_schedule(raw), "shares"
    elif docker_env is not None:
        env_raw = docker_env.get("RK_LANE_SCHEDULE")
        problem = lanes.schedule_problem(env_raw)
        out["schedule"] = lanes.parse_schedule(env_raw)
        out["schedule_source"] = ("env" if problem is None
                                  else ("env-unset" if problem == "unset" else "env-bad"))
        if out["schedule_source"] == "env-bad":
            # The runner falls back to the default on a value it cannot parse rather than
            # failing a cycle, which is right and also silent. A schedule that was set and
            # is not in force is exactly the gap between what someone configured and what
            # the machine is doing, so it is said out loud here.
            out["schedule_problem"] = (
                "lane schedule: the container's RK_LANE_SCHEDULE {} and is ignored, "
                "so every cycle is an explicit search".format(problem))
    if out["schedule"] is not None:
        out["armed"] = lanes.armed(out["schedule"])
        out["scheduled_share"] = lanes.scheduled_shares(out["schedule"])

    if doc is not None:
        gen = meta.get("generated_cycle")
        out["generated_cycle"] = gen if isinstance(gen, int) and not isinstance(gen, bool) else None
        win = meta.get("window_cycles")
        out["window_cycles"] = win if isinstance(win, int) and not isinstance(win, bool) else None
        cur = current_cycle if isinstance(current_cycle, int) and not isinstance(current_cycle, bool) else None
        if cur is not None and out["generated_cycle"] is not None:
            out["behind_cycles"] = cur - out["generated_cycle"]
            out["stale"] = out["behind_cycles"] > LANE_SHARES_STALE_CYCLES
        got = doc.get("lanes")
        counted = 0
        if isinstance(got, dict):
            counted = sum(int((got.get(lane) or {}).get("cycles") or 0) for lane in lanes.LANES)
        if out["stale"]:
            out["error"] = "shares.json is {} cycles behind the run".format(out["behind_cycles"])
            out["fault"] = True
        elif not isinstance(got, dict) or counted <= 0:
            out["error"] = "no cycle has been recorded yet"
        else:
            out["cycles"] = counted
            out["measured"] = {lane: {
                "share": float((got.get(lane) or {}).get("share") or 0.0),
                "cycles": int((got.get(lane) or {}).get("cycles") or 0),
                "seconds": float((got.get(lane) or {}).get("seconds") or 0.0),
            } for lane in lanes.LANES}

    # A missing document is only a fault when the schedule asks for a rotation nobody is
    # recording. Under the default schedule there is no rotation to record, and reporting
    # that as a problem every pass would train the reader to skip the section that exists
    # to be read - the same reasoning that keeps an absent saturation_state.json quiet.
    if out["fault"] or (out["measured"] is None and out["armed"]):
        out["problem"] = "lane split: " + str(out["error"])
    return out


def lane_rows(ln: dict) -> list[tuple[str, str]]:
    """The lane lines as (label, value) pairs, wrapped to the file's column budget.

    stats.txt and the watcher both render from this, so the two cannot disagree about what
    the run is scheduled to do or about what it actually did.
    """
    def wrap(label: str, value: str) -> list[tuple[str, str]]:
        parts = textwrap.wrap(value, LANE_VALUE_W) or [value]
        return [(label, parts[0])] + [("", p) for p in parts[1:]]

    ln = ln or {}
    rows: list[tuple[str, str]] = []
    sched = ln.get("schedule")
    if sched is None:
        rows += wrap("lane", "unknown; no shares.json names one and Docker did not say")
        return rows
    source = _LANE_SOURCE_TEXT.get(str(ln.get("schedule_source")), "source unnamed")
    if not ln.get("armed"):
        rows += wrap("lane", "every cycle explicit (schedule {!r}; {})".format(sched, source))
    else:
        rows += wrap("lane", "schedule {!r}, {}".format(sched, source))
        rows += wrap("", "asks for " + _lane_pcts(ln.get("scheduled_share") or {}))

    measured = ln.get("measured")
    if measured is None:
        if ln.get("armed"):
            rows += wrap("measured", "unknown ({}); nothing has recorded the rotation the "
                                     "schedule asks for".format(ln.get("error") or "not read"))
        else:
            rows += wrap("measured", "unknown; no rotation is scheduled, so none is recorded")
    else:
        rows += wrap("measured", _lane_pcts({lane: measured[lane]["share"]
                                             for lane in lanes.LANES})
                     + " over {} cycles".format(ln.get("cycles")))
        rows += wrap("", "from shares.json, at cycle {}".format(ln.get("generated_cycle")))
    return rows


def read_watchdog_log(path: Path, now: datetime.datetime,
                      max_bytes: int = WATCHDOG_LOG_TAIL_BYTES,
                      max_lines: int = WATCHDOG_LOG_LINES) -> dict:
    """The last few lines the host watchdog printed, with each line's own age.

    The watchdog runs in a minimized window nobody looks at, so an ALERT or a pause decision
    is invisible unless something reads it back. These are lines it printed when it acted:
    a record of what it did, never a reading of the container's state now. An absent log is
    not a failure - it only means the watchdog was started without -LogFile.
    """
    out: dict = {"path": str(path), "present": False, "error": None,
                 "written_age_s": None, "lines": []}
    try:
        info = path.stat()
        with open(path, "rb") as fh:
            if info.st_size > max_bytes:
                fh.seek(info.st_size - max_bytes)
                fh.readline()                                # discard the partial line
            raw = fh.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return out
    except Exception as exc:                                 # noqa: BLE001
        out["error"] = type(exc).__name__
        return out
    out["present"] = True
    out["written_age_s"] = _age(datetime.datetime.fromtimestamp(
        info.st_mtime, datetime.timezone.utc), now)

    entries: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        when = _parse_ts(parts[0]) if parts else None
        text = parts[1] if len(parts) > 1 else ("" if when else line)
        text = "".join(c for c in text if 32 <= ord(c) < 127)
        if len(text) > 58:
            text = text[:55] + "..."
        if entries and entries[-1]["text"] == text:
            entries[-1]["repeat"] += 1
            entries[-1]["at"] = when or entries[-1]["at"]
        else:
            entries.append({"text": text, "at": when, "repeat": 1})
    for ent in entries[-max_lines:]:
        out["lines"].append({"text": ent["text"], "repeat": ent["repeat"],
                             "age_s": _age(ent["at"], now)})
    return out


# ----------------------------------------------------------------------------- verdict

_VERDICT_TEXT = {
    "RUNNING": ("OK", "RUNNING - the container is up and the heartbeat is fresh."),
    "RUNNING_STALE": ("WARN", "NO HEARTBEAT - the container is up but has not written a heartbeat recently."),
    "PAUSED": ("WARN", "PAUSED - the container is up but frozen. The host watchdog pauses it when this machine gets busy."),
    "RESTARTING": ("WARN", "RESTARTING - Docker is bringing the container back up."),
    "CREATED": ("WARN", "CREATED - the container exists but has never been started."),
    "EXITED": ("STOP", "NOT RUNNING - the container has exited."),
    "DEAD": ("STOP", "DEAD - the container is in Docker's dead state and must be recreated."),
    "REMOVING": ("WARN", "REMOVING - the container is being deleted."),
    "ABSENT": ("STOP", "NOT CREATED - no container by that name exists."),
    "STOPPING": ("WARN", "STOPPING - a STOP file is present; the run exits at the next cycle boundary."),
    "FROZEN": ("OK", "FROZEN - the epoch was closed deliberately. This is a clean end, not a failure."),
    "DOCKER_UNREACHABLE": ("STOP", "CANNOT TELL - Docker did not answer in time, so the container's state is unknown. The heartbeat below is the only evidence either way."),
    "DOCKER_ERROR": ("STOP", "CANNOT TELL - Docker answered with an error, so the container's state is unknown. The heartbeat below is the only evidence either way."),
    "DOCKER_ABSENT": ("STOP", "CANNOT TELL - the docker command is not available on this machine."),
    "UNKNOWN": ("WARN", "UNKNOWN - the container state could not be determined."),
}

# Heartbeat older than this, with the container up and not paused, reads as no heartbeat.
HEARTBEAT_STALE_S = 300
# How long a file written once stays worth reading. Fifteen minutes is longer than any cycle
# at the current cadence, so a one-shot file written between cycles is still describing the
# present, and short enough that nobody builds a habit of trusting a stale one.
ONE_SHOT_SHELF_LIFE_S = 900
# A cycle taking longer than this multiple of the recent median is not slow, it is stuck.
STUCK_CYCLE_FACTOR = 3.0
# ...unless there is no cadence to compare against, in which case fall back to this.
STUCK_CYCLE_FLOOR_S = 1800


def progress_note(cadence: dict, now) -> str | None:
    """Whether cycles are still completing, independent of the heartbeat.

    A live heartbeat means the container is executing; it does not mean the run is getting
    anywhere. Observed directly: the heartbeat advanced once and froze, while no cycle had
    completed for eighty minutes against a 470 s median. Anything that reports liveness
    from the heartbeat alone will call that healthy.
    """
    last = cadence.get("last")
    if last is None:
        return None
    age = _age(last, now)
    if age is None:
        return None
    median = cadence.get("median_s")
    limit = median * STUCK_CYCLE_FACTOR if median else STUCK_CYCLE_FLOOR_S
    if age <= limit:
        return None
    against = ("against a median of {}".format(_dur(median)) if median
               else "and no cadence has been established")
    return "no cycle has completed in {}, {}".format(_dur(age), against)


def decide(docker: dict, heartbeat_age: float | None, stop_file: bool, frozen: bool) -> str:
    state = docker.get("state", "UNKNOWN")
    if state in ("DOCKER_UNREACHABLE", "DOCKER_ERROR", "DOCKER_ABSENT"):
        return state                                         # never guess past a silent daemon
    if frozen:
        return "FROZEN"
    if state == "RUNNING":
        if stop_file:
            return "STOPPING"
        if heartbeat_age is not None and heartbeat_age > HEARTBEAT_STALE_S:
            return "RUNNING_STALE"
        return "RUNNING"
    return state if state in _VERDICT_TEXT else "UNKNOWN"


# ----------------------------------------------------------------------------- collect

def collect(work: Path | None = None, findings: Path | None = None,
            with_host: bool = True, with_gpu: bool = True,
            with_docker: bool = True, container: str = "rk",
            with_census: bool | None = None) -> dict:
    """One status document. Never raises: every probe failure lands in doc['problems'].

    The probe switches exist so unit tests and the watcher can build a document without
    touching Docker, nvidia-smi or the host counters. with_census defaults to following
    with_docker, because the census is two more docker calls and --no-docker already means
    "do not touch Docker"; pass it explicitly to run one probe without the other.
    """
    if with_census is None:
        with_census = with_docker
    now = _utcnow()
    work = Path(work) if work else work_dir()
    try:
        find = Path(findings) if findings else findings_dir()
    except Exception:                                        # noqa: BLE001
        find = None
    doc: dict = {"written_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                 "work_dir": str(work), "problems": []}
    bad = doc["problems"].append

    doc["docker"] = probe_docker(container) if with_docker else {"state": "UNKNOWN", "skipped": True}
    if with_docker and doc["docker"].get("error"):
        bad("docker: " + str(doc["docker"]["error"]))

    doc["containers"] = probe_containers() if with_census else {"ok": False, "skipped": True}
    if with_census and doc["containers"].get("error"):
        bad("containers: " + str(doc["containers"]["error"]))

    runstate, err = _read_json(work / "RUNSTATE.json")
    if err:
        bad("run state: " + err)
    doc["runstate"] = runstate or {}

    hb_raw, hb_age = None, None
    try:
        hb_raw = (work / "HEARTBEAT").read_text(encoding="utf-8").strip()
        hb_age = _age(_parse_ts(hb_raw), now)
    except FileNotFoundError:
        bad("heartbeat: HEARTBEAT is absent")
    except Exception as exc:                                 # noqa: BLE001
        bad("heartbeat: " + type(exc).__name__)
    doc["heartbeat"] = {"at": hb_raw, "age_s": hb_age}

    sat, err = _read_json(work / "saturation_state.json")
    if err and "absent" not in err:
        bad("saturation: " + err)
    doc["saturation"] = sat or {}

    directive, _ = _read_json(work / "LAST_DIRECTIVE.json")
    doc["directive"] = directive or {}

    # The container's own environment, and only when Docker actually answered with a
    # container's configuration. probe_docker sets "env" on that path alone, so an absent
    # container or a silent daemon leaves the schedule unknown rather than assumed.
    doc["lanes"] = read_lanes(
        work,
        docker_env=(doc["docker"].get("env") if isinstance(doc["docker"].get("env"), dict)
                    else None),
        current_cycle=doc["runstate"].get("cycle_id"))
    for note in (doc["lanes"].get("schedule_problem"), doc["lanes"].get("problem")):
        if note:
            bad(note)

    doc["stop_file"] = (work / "STOP").exists()
    doc["frozen"] = (work / "EPOCH_STATUS.json").exists()

    # One read, wide enough for a two-window acceptance comparison, parsed down to the four
    # kinds these three readers need.
    events, err = tail_events(work / "events.jsonl", PRODUCTIVITY_TAIL_BYTES,
                              kinds=("cycle_done", "directive_accepted",
                                     "codex_usage", "llm_skipped"))
    if err:
        bad("events: " + err)
    doc["cadence"] = cycle_cadence(events)
    doc["events_seen"] = len(events)
    doc["accept"] = accept_rate(events)
    doc["gate"] = directive_gap(events)

    # The workspace root, the same convention main() uses for stats.txt. An absent log is
    # not a problem; only one that exists and cannot be read is.
    doc["watchdog_log"] = read_watchdog_log(work.parent / "watchdog.log", now)
    if doc["watchdog_log"].get("error"):
        bad("watchdog log: " + str(doc["watchdog_log"]["error"]))

    doc["verdict"] = decide(doc["docker"], hb_age, doc["stop_file"], doc["frozen"])

    try:
        files = sorted((work / "archive").glob("*.jsonl"))
        newest = max((f.stat().st_mtime for f in files), default=None)
        doc["archive"] = {
            "files": len(files),
            "bytes": sum(f.stat().st_size for f in files),
            "latest": files[-1].name if files else None,
            "latest_mtime_age_s": _age(datetime.datetime.fromtimestamp(
                newest, datetime.timezone.utc), now) if newest else None}
    except Exception as exc:                                 # noqa: BLE001
        bad("archive: " + type(exc).__name__)
        doc["archive"] = {}

    doc["site"] = {}
    if find is not None:
        try:
            pages = sorted((find / "docs").glob("*.html"))
            newest = max((p.stat().st_mtime for p in pages), default=None)
            doc["site"] = {"pages": len(pages),
                           "built_age_s": _age(datetime.datetime.fromtimestamp(
                               newest, datetime.timezone.utc), now) if newest else None}
        except Exception:                                    # noqa: BLE001
            doc["site"] = {}

    if with_host:
        try:
            drives = [work]
            sysdrive = os.environ.get("SystemDrive")
            if sysdrive:
                drives.append(Path(sysdrive + os.sep))
            doc["host"] = {"cpu_percent": host_cpu_percent(), "memory": host_memory(),
                           "power": host_power(), "disks": probe_disks(drives)}
        except Exception as exc:                             # noqa: BLE001
            bad("host counters: " + type(exc).__name__)
            doc["host"] = {}
    else:
        doc["host"] = {}

    on_battery = ((doc.get("host") or {}).get("power") or {}).get("on_ac") is False
    doc["gpu"] = probe_gpu(skip=(not with_gpu) or on_battery)
    if doc["gpu"].get("error"):
        bad("gpu: " + str(doc["gpu"]["error"]))
    return doc


# ----------------------------------------------------------------------------- render

_W = 78


def _rule(ch: str = "-") -> str:
    return ch * _W


def _sec(title: str) -> list[str]:
    return ["", _rule(), "  " + title, _rule()]


def _row(label: str, value: str) -> str:
    return "  {:<14}{}".format(label, value)


def render_text(doc: dict, refresh_s: int | None = None) -> str:
    """The stats.txt body. ASCII only, so Notepad and PowerShell 5.1 both cope."""
    now = _parse_ts(doc.get("written_at")) or _utcnow()
    lw, tz = _local(now)
    L: list[str] = [_rule("="), "  rk run - status snapshot", _rule("=")]
    L.append(_row("written", "{} {}   ({})".format(lw, tz, doc.get("written_at"))))
    L.append(_row("", _ct(doc.get("written_at")) + ", the timezone the harness reports in"))
    if refresh_s:
        dead = (now + datetime.timedelta(seconds=refresh_s * 6)).astimezone()
        L.append(_row("refreshed", "every ~{}s while stats.ps1 -Loop runs".format(refresh_s)))
        L.append(_row("stale after", "{} {}".format(dead.strftime("%H:%M:%S"), tz)))
        L.append(_row("", "If your clock is past that, nothing is updating this file"))
        L.append(_row("", "and every number below it is old."))
    else:
        # A one-shot file gets the same deadline pair a looping one gets. The deadline is a
        # pure function of written_at, so render_text reads no clock and --age agrees with
        # the header it is reading.
        dead = (now + datetime.timedelta(seconds=ONE_SHOT_SHELF_LIFE_S)).astimezone()
        L.append(_row("refreshed", "written once. Nothing is keeping this file current."))
        L.append(_row("stale after", "{} {}".format(dead.strftime("%H:%M:%S"), tz)))
        L.append(_row("", "If your clock is past that, nothing is updating this file"))
        L.append(_row("", "and every number below it is old."))
        L.append(_row("", "stats.ps1 -Loop -Background keeps it current, and start.ps1"))
        L.append(_row("", "launches that writer for you."))

    code, text = _VERDICT_TEXT.get(doc.get("verdict", "UNKNOWN"), _VERDICT_TEXT["UNKNOWN"])
    L += _sec("IS IT RUNNING?")
    wrapped = textwrap.wrap(text, _W - 10) or [text]
    L.append("  [{}] {}".format(code, wrapped[0]))
    L += ["         " + w for w in wrapped[1:]]

    dk = doc.get("docker", {}) or {}
    if dk.get("error"):
        L.append("         " + str(dk["error"]))
        if dk.get("latency_ms"):
            L.append("         docker took {} ms to fail".format(dk["latency_ms"]))
        L.append("         Docker cannot tell us anything about the container.")
        # With the daemon silent the heartbeat is the only evidence of life there is, so
        # it gets read out here rather than left as a quiet row further down. Learned the
        # hard way: a 140 s heartbeat during a brief unpause was mistaken for recovery
        # while the run had in fact done no work for over an hour.
        age = (doc.get("heartbeat") or {}).get("age_s")
        if age is None:
            L.append("         There is no heartbeat file either, so nothing here is")
            L.append("         evidence that the run is alive.")
        elif age <= HEARTBEAT_STALE_S:
            stuck = progress_note(doc.get("cadence") or {}, now)
            if stuck:
                sentence = ("The heartbeat is {} old, so the container is executing. "
                            "But {}, so it is running without getting "
                            "anywhere.").format(_dur(age), stuck)
                L += ["         " + w for w in textwrap.wrap(sentence, _W - 10)]
            else:
                L.append("         The heartbeat is {} old though, so the run is alive".format(_dur(age)))
                L.append("         and working. It is Docker that is broken, not the run.")
        else:
            L.append("         The heartbeat has not moved in {}, so there is no".format(_dur(age)))
            L.append("         evidence the run is alive. Check again in a minute: if that")
            L.append("         age keeps growing, it has stopped doing work.")
    L.append("")
    if dk.get("ok") and dk.get("status"):
        L.append(_row("container", "{}   image {}".format(dk.get("status"), dk.get("image") or "unknown")))
        st = _parse_ts(dk.get("started_at"))
        L.append(_row("started", "{}   up {}".format(_ct(dk.get("started_at")), _dur(_age(st, now)))
                      if st else "unknown"))
        L.append(_row("limits", "{} cpus, {} GB, restart {}, {} restarts".format(
            dk.get("cpus") or "?", dk.get("memory_gb") or "?",
            dk.get("restart_policy"), dk.get("restart_count"))))
        if "exit_code" in dk:
            L.append(_row("exit", "code {}, OOM killed {}, at {}".format(
                dk["exit_code"], "yes" if dk.get("oom_killed") else "no",
                _ct(dk.get("finished_at")))))
    hb = doc.get("heartbeat", {}) or {}
    L.append(_row("heartbeat", "{}   {} old".format(_ct(hb.get("at")), _dur(hb.get("age_s")))
                  if hb.get("at") else "unknown"))
    if doc.get("verdict") == "PAUSED":
        L.append(_row("", "A paused container cannot write a heartbeat, so a stale"))
        L.append(_row("", "one is expected and does not mean the run has died."))
    L.append(_row("STOP file", "present - a stop was requested" if doc.get("stop_file") else "absent"))
    L.append(_row("epoch", "FROZEN (EPOCH_STATUS.json present)" if doc.get("frozen") else "active"))

    rs = doc.get("runstate", {}) or {}
    cad = doc.get("cadence", {}) or {}
    L += _sec("WHAT IT IS DOING")
    L.append(_row("cycle", "{}   phase {}".format(rs.get("cycle_id", "unknown"), rs.get("phase", "?"))))
    # What KIND of cycle that was. Every counter below this point is read as a count of
    # explicit searches, so the reader is told first whether the run is still doing one on
    # every cycle. The measured split is never filled in from the schedule.
    ln = doc.get("lanes") or {}
    for label, value in lane_rows(ln):
        L.append(_row(label, value))
    last = cad.get("last")
    L.append(_row("last cycle", "{}   {} ago".format(_ct(last.isoformat()), _dur(_age(last, now)))
                  if last else "unknown (no cycle_done in the recent events tail)"))
    if cad.get("median_s"):
        L.append(_row("cadence", "median {}s over {} cycles   ({:.1f} cycles/hour)".format(
            int(cad["median_s"]), cad["samples"], 3600.0 / cad["median_s"])))
    cell = rs.get("current_cell")
    if cell:
        L.append(_row("cell", "stages {}, cycle bucket {}".format(cell[0], cell[1])))
    d = doc.get("directive", {}) or {}
    if d:
        L.append(_row("directive", "{}   order {}, stages {}".format(
            d.get("directive_id", "?"), d.get("target_order", "?"), d.get("stages", "?"))))

    sat = doc.get("saturation", {}) or {}
    arc = doc.get("archive", {}) or {}
    L += _sec("PROGRESS")
    stuck = progress_note(cad, now)
    if stuck:
        head = stuck[0].upper() + stuck[1:]
        parts = textwrap.wrap(head, _W - 16) or [head]
        L.append(_row("STUCK", parts[0]))
        L += [_row("", w) for w in parts[1:]]
    # The counter counts explicit cycles that produced no elite. Under a lane rotation a
    # cycle that spent its budget on the adaptive or implicit lane appends nothing to the
    # scored archive because that is what it was told to do, and calling that a stall is the
    # false fault this whole reading exists to avoid. Today's schedule makes every cycle an
    # explicit one, so today's sentence is the one it has always been.
    if ln.get("armed"):
        L.append(_row("stall", "{} explicit cycles with no new elite".format(
            rs.get("stall_counter", "unknown"))))
        L.append(_row("", "a lane cycle appends nothing to the scored archive by"))
        L.append(_row("", "design and is not counted here"))
    else:
        L.append(_row("stall", "{} cycles with no new elite".format(rs.get("stall_counter", "unknown"))))

    # Rate of work, not signs of life. A run can be fast, green and heartbeating while
    # accepting nothing and never hearing from the model; nothing above this point says so.
    def _wrapped(label: str, sentence: str) -> None:
        parts = textwrap.wrap(sentence, _W - 16) or [sentence]
        L.append(_row(label, parts[0]))
        L.extend(_row("", w) for w in parts[1:])

    acc = doc.get("accept") or {}
    # "per cycle" only stays true while every cycle is an explicit search. Once the stream
    # says which lane a cycle was, accept_rate counts one lane and the row says which.
    unit = "{} cycle".format(acc.get("lane", "explicit")) if acc.get("lane_aware") else "cycle"
    if not acc.get("cycles"):
        L.append(_row("accepted", "unknown (no cycle_done in the tail)"))
    elif acc.get("enough"):
        _wrapped("accepted", "median {} per {} over the newest {} cycles, against {} over "
                             "the oldest {} in this tail".format(
                                 _num(acc.get("recent_median")), unit, acc.get("recent_n"),
                                 _num(acc.get("older_median")), acc.get("older_n")))
    else:
        _wrapped("accepted", "median {} per {} over the newest {} of {} cycles in this "
                             "tail; {} per window needed before two windows can be "
                             "compared".format(
                                 _num(acc.get("recent_median")), unit, acc.get("recent_n"),
                                 acc.get("cycles"), ACCEPT_MIN_SAMPLES))
    if acc.get("other_lane_cycles"):
        _wrapped("", "{} cycles in this tail were on another lane and are not counted "
                     "above; a lane cycle appends nothing by design".format(
                         acc.get("other_lane_cycles")))
    if acc.get("collapsed"):
        _wrapped("COLLAPSE", "acceptance is under half what it was earlier in this same "
                             "tail. The two medians it is comparing are on the line above.")

    gate = doc.get("gate") or {}
    if gate.get("at_least") and not gate.get("cycles"):
        _wrapped("model gate", "no directive from the model in this tail, and no completed "
                               "cycles in it either")
    elif not gate.get("cycles"):
        _wrapped("model gate", "the model wrote a directive since the last completed cycle")
    else:
        _wrapped("model gate", "the model has not written a directive in {}{} cycles".format(
            "at least " if gate.get("at_least") else "", gate.get("cycles", 0)))
    snap = gate.get("snapshot") or {}
    if snap:
        L.append(_row("codex plan", "{}% of the {} minute window used, resets {}".format(
            _num(snap.get("used_percent")), _num(snap.get("window_minutes")),
            _ct(snap.get("resets_at")))))
        L.append(_row("", "reading {} old{}".format(
            _dur(snap.get("snapshot_age_s")),
            ", older than its own window" if gate.get("stale_snapshot") else "")))
    skip = gate.get("last_skip") or {}
    if skip:
        L.append(_row("last skip", "{} gate: {} at {}".format(
            skip.get("gate") or "unnamed", skip.get("reason") or "unstated",
            _ct(skip.get("ts")))))

    if arc.get("files"):
        L.append(_row("archive", "{} daily files, {:.1f} MB, newest touched {} ago".format(
            arc.get("files", 0), (arc.get("bytes") or 0) / 2 ** 20,
            _dur(arc.get("latest_mtime_age_s")))))
    if sat:
        L.append(_row("saturation", "{}   {} consecutive saturating checks".format(
            sat.get("last_verdict", "unknown"), sat.get("consecutive", 0))))
        L.append(_row("", "last checked " + _ct(sat.get("last_check"))))
    site = doc.get("site", {}) or {}
    if site.get("pages"):
        L.append(_row("findings site", "{} pages, built {} ago".format(
            site["pages"], _dur(site.get("built_age_s")))))

    L += _sec("WHAT THE WATCHDOG SAID")
    wl = doc.get("watchdog_log") or {}
    if wl.get("error"):
        L.append(_row("log", "unreadable ({})".format(wl["error"])))
    elif not wl.get("present"):
        L.append(_row("log", "absent - no watchdog.log next to this file"))
        L.append(_row("", "start.ps1 passes -LogFile; a watchdog started another way"))
        L.append(_row("", "keeps its output in its own minimized window only."))
    else:
        L.append(_row("log", "watchdog.log, last written {} ago".format(
            _dur(wl.get("written_age_s")))))
        for ent in wl.get("lines") or []:
            text = str(ent.get("text") or "")
            if (ent.get("repeat") or 1) > 1:
                text += "  (x{})".format(ent["repeat"])
            L.append(_row(_dur(ent.get("age_s")) + " ago", text))
        L.append(_row("", "These are lines the watchdog printed when it acted. They are"))
        L.append(_row("", "its record, not a reading of the container's state now."))

    L += _sec("THIS MACHINE")
    h = doc.get("host", {}) or {}
    cpu = h.get("cpu_percent")
    L.append(_row("host CPU", "{}% total".format(cpu) if cpu is not None else "unknown"))
    if cpu is not None:
        L.append(_row("", "the watchdog's pause guard measures host CPU minus the"))
        L.append(_row("", "container's own share, so its number runs lower than this"))
    mem = h.get("memory")
    L.append(_row("memory", "{}% used, {} GB free of {} GB".format(
        mem["percent_used"], mem["free_gb"], mem["total_gb"]) if mem else "unknown"))
    pw = h.get("power")
    if pw:
        b = ", battery {}%".format(pw["battery_percent"]) if pw.get("battery_percent") is not None else ""
        L.append(_row("power", "on " + str(pw["source"]) + b))
    for dsk in h.get("disks") or []:
        pf = dsk.get("percent_free")
        # The watchdog's disk floor only watches the work drive. Docker's VHDX lives on
        # the system drive, and nothing guards that one, so flag any drive running out.
        flag = "   LOW" if (pf is not None and pf < 10) else ""
        L.append(_row("disk " + dsk["mount"].rstrip("\\/"),
                      "{} GB free of {} GB ({}% free){}".format(
                          dsk["free_gb"], dsk["total_gb"], pf, flag)))
    g = doc.get("gpu", {}) or {}
    if g.get("ok"):
        L.append(_row("GPU", str(g.get("name"))))
        L.append(_row("", "{}% util   {:.0f} of {:.0f} MiB   {:.0f} C   {} of {} W   pstate {}".format(
            g.get("util_percent"), g.get("memory_used_mib") or 0, g.get("memory_total_mib") or 0,
            g.get("temp_c") or 0, g.get("power_w"), g.get("power_limit_w"), g.get("pstate"))))
        L.append(_row("", "sampled " + _ct(g.get("sampled_at")) + ". The run is CPU-only,"))
        L.append(_row("", "so this is whatever else is using the GPU."))
    elif g.get("skipped"):
        L.append(_row("GPU", "not sampled ({}; polling wakes the dGPU)".format(g["skipped"])))
    else:
        L.append(_row("GPU", "unknown ({})".format(g.get("error", "not probed"))))

    L += _sec("OTHER CONTAINERS ON THIS DAEMON")
    cs = doc.get("containers") or {}
    if cs.get("skipped"):
        L.append(_row("census", "not sampled (--no-census, or Docker was not probed)"))
    elif not cs.get("ok"):
        L.append(_row("census", "unknown ({})".format(_ascii(cs.get("error", "not probed")))))
    else:
        rows = cs.get("containers") or []
        flags = set(cs.get("flagged") or [])
        L.append(_row("census", "{} running, {} flagged".format(len(rows), len(flags))))
        for r in rows:
            bits = _ascii(r.get("status") or "unknown")
            if r.get("health"):
                bits += ", " + _ascii(r["health"])
            if r.get("restart_count") is not None:
                bits += ", {} restarts".format(r["restart_count"])
            if r.get("name") in flags:
                bits += "   FLAG"
            L.append(_row(_ascii(r.get("name") or "?")[:20], bits))
        L.append(_row("", "Restart counts are this pass only. Nothing here is a trend:"))
        L.append(_row("", "no value is carried forward, so there is nothing to compare to."))

    L += _sec("PROBLEMS READING STATE")
    probs = doc.get("problems") or []
    L += ["  - " + str(p) for p in probs] if probs else ["  none"]

    L += _sec("WHERE THESE NUMBERS COME FROM")
    L.append("  container         docker inspect, with a hard timeout. Never docker")
    L.append("                    stats: it samples every container and has no timeout")
    L.append("                    of its own (1.4 to 2.0 s here when quiet, 47 s once")
    L.append("                    when not), so it stays off the path of a timed write.")
    L.append("  other containers  docker ps then one docker inspect, same timeout")
    L.append("  cycle, stall      rk-work/RUNSTATE.json")
    L.append("  lane schedule     rk-work/schedule/shares.json, else the container's")
    L.append("                    own RK_LANE_SCHEDULE as docker inspect reported it")
    L.append("  measured split    rk-work/schedule/shares.json only. The schedule is")
    L.append("                    never printed in its place.")
    L.append("  heartbeat         rk-work/HEARTBEAT")
    L.append("  cadence           the tail of rk-work/events.jsonl")
    L.append("  accepted, gate    the tail of rk-work/events.jsonl")
    L.append("  watchdog          watchdog.log next to this file, written by watchdog.ps1")
    L.append("  saturation        rk-work/saturation_state.json")
    L.append("  host, GPU         Windows kernel32 and nvidia-smi, sampled just now")
    L.append("")
    L.append("  Run times are US Central, the harness convention. The staleness")
    L.append("  deadline above is in " + tz + ", this machine's own clock.")
    L.append("  stats.ps1   refresh this file      watcher.ps1  live view")
    L.append("  start.ps1   start the run          stop.ps1     stop it")
    L.append(_rule("="))
    return "\r\n".join(L) + "\r\n"


def write(path: Path, doc: dict, refresh_s: int | None = None) -> Path:
    """Atomic where the OS allows; a locked destination falls back rather than losing the write."""
    path = Path(path)
    body = render_text(doc, refresh_s)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(body, encoding="ascii", errors="replace", newline="")
    try:
        os.replace(tmp, path)
    except PermissionError:
        path.write_text(body, encoding="ascii", errors="replace", newline="")
        try:
            tmp.unlink()
        except OSError:
            pass
    return path


# The two header rows render_text writes, read back. Anchored on the row labels rather than
# on line numbers so a reordered header does not silently stop being readable.
_WRITTEN_RE = re.compile(r"^\s*written\s+.*\((\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\)\s*$")
_REFRESH_RE = re.compile(r"every ~(\d+)s")


def read_written_at(path) -> tuple[datetime.datetime | None, int | None, str | None]:
    """(written_at, declared refresh interval, error) read out of an existing stats.txt.

    Runs no probe by construction: it opens one file and reads its first 4 KB. That is what
    makes --age answerable while Docker is wedged, which is exactly when someone wants to
    know whether the file in front of them is still describing the present.
    """
    try:
        with open(path, "r", encoding="ascii", errors="replace") as fh:
            head = fh.read(4096)
    except FileNotFoundError:
        return None, None, "stats.txt is absent"
    except Exception as exc:                                 # noqa: BLE001
        return None, None, type(exc).__name__
    when, refresh = None, None
    for line in head.splitlines():
        if when is None:
            m = _WRITTEN_RE.match(line)
            if m:
                when = _parse_ts(m.group(1))
        if refresh is None:
            m = _REFRESH_RE.search(line)
            if m:
                try:
                    refresh = int(m.group(1)) or None
                except ValueError:
                    refresh = None
    if when is None:
        return None, refresh, "no written timestamp in the first 4096 bytes"
    return when, refresh, None


def age_report(path, now: datetime.datetime | None = None,
               max_age_s: int | None = None) -> dict:
    """How old an existing stats.txt is, and whether that is past its own shelf life.

    The shelf life is the one the file itself declares: six refresh intervals for a looping
    writer, the one-shot shelf life otherwise, so this answer and the 'stale after' row in
    the header cannot disagree.
    """
    now = now or _utcnow()
    when, refresh_s, err = read_written_at(path)
    if err or when is None:
        return {"ok": False, "path": str(path), "error": err or "no written timestamp"}
    age_s = (now - when).total_seconds()
    shelf = max_age_s if max_age_s else (refresh_s * 6 if refresh_s else ONE_SHOT_SHELF_LIFE_S)
    return {"ok": True, "path": str(path),
            "written_at": when.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "age_s": age_s, "shelf_life_s": shelf, "refresh_s": refresh_s,
            "stale": age_s > shelf}


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Write a status snapshot for the rk run.")
    ap.add_argument("--out", default=None, help="output path (default: <workspace>/stats.txt)")
    ap.add_argument("--json", action="store_true", help="print the document as JSON instead")
    ap.add_argument("--refresh", type=int, default=None, help="declared refresh interval, seconds")
    ap.add_argument("--no-gpu", action="store_true")
    ap.add_argument("--no-docker", action="store_true")
    ap.add_argument("--no-census", action="store_true",
                    help="skip the other-containers census (two extra docker calls)")
    ap.add_argument("--age", action="store_true",
                    help="report the age of an existing stats.txt and exit; runs no probe")
    ap.add_argument("--max-age-s", type=int, default=None,
                    help="with --age: override the shelf life the file declares, in seconds")
    a = ap.parse_args(argv)
    out = Path(a.out) if a.out else work_dir().parent / "stats.txt"

    # --age answers the freshness question by reading the file, so it returns before any
    # probe runs: a wedged daemon must not delay the one question that is about the file.
    if a.age:
        rep = age_report(out, max_age_s=a.max_age_s)
        if a.json:
            print(json.dumps(rep, indent=1, default=str))
        elif not rep["ok"]:
            print("{}  unreadable ({})".format(rep["path"], rep["error"]))
        else:
            print("{}  written {}, {} old, shelf life {}  [{}]".format(
                rep["path"], rep["written_at"], _dur(rep["age_s"]),
                _dur(rep["shelf_life_s"]), "STALE" if rep["stale"] else "current"))
        if not rep["ok"]:
            return 2
        return 1 if rep["stale"] else 0

    doc = collect(with_gpu=not a.no_gpu, with_docker=not a.no_docker,
                  with_census=False if a.no_census else None)
    if a.json:
        print(json.dumps(doc, indent=1, default=str))
        return 0
    write(out, doc, a.refresh)
    print("{}  [{}]".format(out, doc["verdict"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
