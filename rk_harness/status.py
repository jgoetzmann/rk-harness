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

This module must not import runner (viewers read state files; K13), and must import
cleanly on Linux for CI - every Windows call is looked up lazily and returns None
elsewhere.
"""
from __future__ import annotations

import datetime
import json
import os
import platform
import shutil
import subprocess
import textwrap
import time
from pathlib import Path

from rk_harness.paths import findings_dir, work_dir
from rk_harness.timefmt import fmt_ct

# Probe timeouts. Docker on this machine has been observed taking 47 s and returning HTTP
# 500 on every endpoint, so nothing may block unbounded: a slow probe must degrade to
# "unknown" long before it delays the file.
DOCKER_TIMEOUT_S = 6.0
GPU_TIMEOUT_S = 5.0
# events.jsonl grows ~5 MB/day with no rotation and is already 35 MB. Only the tail is
# ever read, so cadence stays cheap as the file grows without bound.
EVENTS_TAIL_BYTES = 512 * 1024

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


def tail_events(path: Path, max_bytes: int = EVENTS_TAIL_BYTES) -> tuple[list[dict], str | None]:
    """Parse only the last max_bytes of events.jsonl.

    The file has no rotation and grows about 5 MB/day; a full scan already costs ~1.5 s and
    gets worse forever, which is not a price a status refresh should pay.
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
            with_docker: bool = True, container: str = "rk") -> dict:
    """One status document. Never raises: every probe failure lands in doc['problems'].

    The probe switches exist so unit tests and the watcher can build a document without
    touching Docker, nvidia-smi or the host counters.
    """
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

    doc["stop_file"] = (work / "STOP").exists()
    doc["frozen"] = (work / "EPOCH_STATUS.json").exists()

    events, err = tail_events(work / "events.jsonl")
    if err:
        bad("events: " + err)
    doc["cadence"] = cycle_cadence(events)
    doc["events_seen"] = len(events)

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
        L.append(_row("refreshed", "written once by hand. stats.ps1 -Loop keeps it current."))

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
    L.append(_row("stall", "{} cycles with no new elite".format(rs.get("stall_counter", "unknown"))))
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

    L += _sec("PROBLEMS READING STATE")
    probs = doc.get("problems") or []
    L += ["  - " + str(p) for p in probs] if probs else ["  none"]

    L += _sec("WHERE THESE NUMBERS COME FROM")
    L.append("  container         docker inspect, with a hard timeout. Never docker")
    L.append("                    stats, which has been measured at 47 s here.")
    L.append("  cycle, stall      rk-work/RUNSTATE.json")
    L.append("  heartbeat         rk-work/HEARTBEAT")
    L.append("  cadence           the tail of rk-work/events.jsonl")
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


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Write a status snapshot for the rk run.")
    ap.add_argument("--out", default=None, help="output path (default: <workspace>/stats.txt)")
    ap.add_argument("--json", action="store_true", help="print the document as JSON instead")
    ap.add_argument("--refresh", type=int, default=None, help="declared refresh interval, seconds")
    ap.add_argument("--no-gpu", action="store_true")
    ap.add_argument("--no-docker", action="store_true")
    a = ap.parse_args(argv)
    doc = collect(with_gpu=not a.no_gpu, with_docker=not a.no_docker)
    if a.json:
        print(json.dumps(doc, indent=1, default=str))
        return 0
    out = Path(a.out) if a.out else work_dir().parent / "stats.txt"
    write(out, doc, a.refresh)
    print("{}  [{}]".format(out, doc["verdict"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
