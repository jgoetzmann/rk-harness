"""Lane scheduling and the realised time split (T16). New module; nothing imports it yet.

Importing this module changes nothing. Every function here is either a pure
function of its arguments or a reader of an environment variable that is unset in
the live container, and every default resolves to what the machine does today.

THE SPLIT AS DATA. There are three method classes and one schedule string. LANES
names them in the order the sites lead with. The schedule is a string of letters,
one letter per cycle, indexed by the cycle id the way ``runner._POLICY_ROTATION``
is indexed by policy block (runner.py:80-92), and it is a string on purpose: a
config value can carry it, so changing the split later is a config edit rather
than a code change. ``"E"`` means every cycle is an explicit cycle, which is
exactly the container's behaviour today, so an unset, empty or unreadable
``RK_LANE_SCHEDULE`` leaves the machine where it is. ``"EAI"`` is thirds. The
70/15/15 the roadmap has described since 2026-09-02 is ``LEGACY_70_15_15``.

INTENDED IS NOT REALISED, AND THEY ARE KEPT APART HERE. The survey behind this
module measured where the container's time actually went over 186 cycles: 0.16 s
of about 32,700 s reached the two side classes, 0.0005 percent against an intended
30 percent, and nothing in the tree was in a position to notice. So this module
carries two separate things and never lets one stand in for the other:

* ``scheduled_shares(schedule)`` is what the string asks for. Arithmetic on the
  string, nothing else.
* the shares document is what happened. It is built only from the per-cycle
  evidence in ``<RK_WORK_DIR>/schedule/cycles.jsonl``, one line per cycle, written
  by the runner as a cycle ends. With no rows it reports that no split has been
  measured rather than falling back on the intention.

DETERMINISM. No function here reads a clock. ``append_cycle`` takes its timestamp
and its seconds from the caller, because the runner owns the clock; ``build_shares``
takes rows and returns a document that is a pure function of them, so two builds
over the same log are byte-identical and a page rendered from one cannot move on
its own. Iteration is over LANES and over sorted keys.

STORAGE IS UTC. Every ``ts`` in cycles.jsonl is an ISO-8601 Z stamp. A row whose ts
is not in that shape is discarded and counted rather than quietly averaged in.
Central time is display and belongs to ``rk_harness.timefmt``.

BOUNDED READS. The cycle log grows by one line per cycle forever, so it is read
from the end: ``load_cycles`` seeks backwards for the last ``window_cycles`` lines
and never loads the whole file. This module never opens events.jsonl (45 MB) or
the archive.

TWO LETTER ALPHABETS THAT DO NOT MEAN THE SAME THING. E/A/I are lanes here, while
``literature.TRACK_SCHEDULE`` speaks A/B/C for lead, adaptive and implicit, so a
literal "A" means adaptive in one and explicit in the other.
``literature_schedule()`` is the one place the two are mapped, so a single config
value can set both splits and they cannot drift apart.

The artifact is not a legal source for a public-page number. The traceability rule
lists key_findings.json, validation/results.json, benchmark/results.json and the
side-track ledger; adding shares.json to that list is a deliberate decision for the
owner, not something this module can take by writing a file.
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from rk_harness.paths import work_dir

# --------------------------------------------------------------------------- the schedule

LANES: tuple[str, ...] = ("explicit", "adaptive", "implicit")
LETTERS: dict[str, str] = {"E": "explicit", "A": "adaptive", "I": "implicit"}
LANE_LETTERS: dict[str, str] = {lane: letter for letter, lane in LETTERS.items()}

# Disarmed by construction: every cycle explicit, which is the cycle loop as it
# stands. Nothing in this module can produce another schedule from an absent one.
DEFAULT_SCHEDULE = "E"
THIRDS = "EAI"
LEGACY_70_15_15 = "EEAEEIEEEAEEIEEEAEEI"      # 14 E, 3 A, 3 I, evenly interleaved

# 60 letters is well past any split anyone would write by hand. The cap exists so a
# pasted paragraph or a stray file read cannot become a schedule.
MAX_SCHEDULE_LEN = 60

# lane -> the letter literature.TRACK_SCHEDULE uses for the same research track.
LITERATURE_TRACKS: dict[str, str] = {"explicit": "A", "adaptive": "B", "implicit": "C"}


def schedule_problem(value) -> str | None:
    """Why ``value`` is not a usable schedule, or None when it is.

    Split out from parse_schedule so a caller that wants to complain (configure.py,
    preflight, the watch panel) can say what was wrong. parse_schedule itself is
    silent by design: the runner must not fail a cycle over a typo, and the point of
    the fallback is that it is safe. Silent and unnoticed are different things, which
    is what this function is for.
    """
    if value is None:
        return "unset"
    s = str(value).strip().upper()
    if not s:
        return "empty"
    if len(s) > MAX_SCHEDULE_LEN:
        return f"longer than {MAX_SCHEDULE_LEN} letters"
    bad = sorted({c for c in s if c not in LETTERS})
    if bad:
        return ("carries " + ", ".join(repr(c) for c in bad) + " outside "
                + "".join(sorted(LETTERS)))
    return None


def parse_schedule(value) -> str:
    """Any value to a schedule string. Anything unusable resolves to DEFAULT_SCHEDULE.

    The fallback discipline is _search_policy's (runner.py:80-92): an unknown value
    must not invent behaviour. A schedule is held stricter than that rotation because
    it reaches further. "EAIX" is NOT filtered down to "EAI": a typo in the container
    environment would otherwise arm a two-thirds restructure of the run that nobody
    asked for, and arming that has to be a deliberate act. A schedule is accepted
    whole or not at all, and not at all means today's behaviour.
    """
    if schedule_problem(value) is not None:
        return DEFAULT_SCHEDULE
    return str(value).strip().upper()


def lane_for(cycle_id: int, schedule: str | None = None) -> str:
    """The lane one cycle belongs to. Pure function of (cycle_id, schedule).

    No environment is read here: the caller resolves the schedule once and passes it,
    so an event line and the work it describes cannot disagree because the variable
    changed between two reads.
    """
    sched = parse_schedule(schedule)
    return LETTERS[sched[int(cycle_id) % len(sched)]]


def scheduled_shares(schedule: str | None = None) -> dict[str, float]:
    """What the schedule ASKS FOR, per lane. Never evidence of what happened.

    Always carries all three lanes, in LANES order, so a consumer never has to guess
    whether a missing key means zero or means the lane does not exist.
    """
    sched = parse_schedule(schedule)
    counts = {lane: 0 for lane in LANES}
    for ch in sched:
        counts[LETTERS[ch]] += 1
    n = float(len(sched))
    return {lane: counts[lane] / n for lane in LANES}


def literature_schedule(schedule: str | None = None) -> str:
    """Lane schedule to a literature.TRACK_SCHEDULE string: E->A, A->B, I->C.

    One key then sets both the CPU split and the reading split. "EAI" gives "ABC";
    LEGACY_70_15_15 gives the same 14/3/3 letter counts literature.TRACK_SCHEDULE has
    carried since the rotation was set.
    """
    return "".join(LITERATURE_TRACKS[LETTERS[ch]] for ch in parse_schedule(schedule))


def armed(schedule: str | None = None) -> bool:
    """True when the schedule asks for anything besides the explicit body.

    "E", "EE" and an unreadable value are all disarmed: the letters decide, not the
    length.
    """
    return set(parse_schedule(schedule)) != {"E"}


# --------------------------------------------------------------------------- environment

# A lane cycle costs the archive replay (73 s measured) plus its budget plus the
# publish tail (6 s), so 100 s keeps a lane cycle at about 176 s, which is the steady
# explicit cycle length today. Holding the cycle length still means the publish
# cadence, the watchdog's no-candidate window and the stop arithmetic all keep the
# numbers they were derived from.
LANE_MAX_SECONDS_DEFAULT = 100.0
LANE_MAX_SECONDS_FLOOR = 30.0
# 420 s re-derives docs/SIDETRACK-AUTOMATION.md:445-455 for a lane: a graceful stop
# waits 20 minutes and the STOP killfile is only read at a cycle boundary, so the
# worst case is one full cycle (642 s observed) plus the budget plus the longest
# single point (48 s measured). 642 + 420 + 48 = 18.5 min, inside the grace; 600
# would be 21.5 min and force-stopped. Re-derive this if cycle time grows.
LANE_MAX_SECONDS_CEILING = 420.0


def lane_schedule() -> str:
    """RK_LANE_SCHEDULE (config.json run.lane_schedule) through parse_schedule."""
    return parse_schedule(os.environ.get("RK_LANE_SCHEDULE"))


def lane_max_seconds() -> float:
    """RK_LANE_MAX_SECONDS (config.json run.lane_max_seconds), clamped to the range.

    Clamped rather than rejected, following runner._sidetrack_max_seconds: a budget
    cannot arm anything on its own, so the nearest legal value is the useful answer.
    The schedule is the thing that arms, and that one is rejected whole.
    """
    raw = os.environ.get("RK_LANE_MAX_SECONDS")
    try:
        v = float(raw) if raw not in (None, "") else LANE_MAX_SECONDS_DEFAULT
    except ValueError:
        return LANE_MAX_SECONDS_DEFAULT
    if not math.isfinite(v):
        return LANE_MAX_SECONDS_DEFAULT
    return max(LANE_MAX_SECONDS_FLOOR, min(LANE_MAX_SECONDS_CEILING, v))


# --------------------------------------------------------------------------- the cycle log

SCHEDULE_CYCLES_SCHEMA = "schedule-cycles/1"
SHARES_SCHEMA = "schedule-shares/1"
DEFAULT_WINDOW_CYCLES = 200

CYCLE_ROW_KEYS: tuple[str, ...] = (
    "cycle", "lane", "ts", "seconds", "productive_seconds", "records_appended",
    "points_measured", "lane_records_appended", "schedule", "code_hash",
)
_COUNT_KEYS: tuple[str, ...] = ("records_appended", "points_measured",
                                "lane_records_appended")

# Storage is UTC (CLAUDE.md rule 6). Enforced by shape, not by hope: an offset or a
# naive stamp is a discarded row, so a Central-time value cannot be averaged in as
# though it were an hour of work that never happened.
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

_BANNED = ("novel", "first", "beats", "outperforms", "breakthrough", "proves",
           "state-of-the-art", "best-ever")
_EM_DASH = chr(0x2014)          # by code point, so this file stays pure ASCII


def schedule_dir() -> Path:
    return work_dir() / "schedule"


def cycles_path() -> Path:
    return schedule_dir() / "cycles.jsonl"


def shares_path() -> Path:
    return schedule_dir() / "shares.json"


class Floats:
    """Finite floats pass through; non-finite becomes None, counted.

    An instance per build rather than a module global, so two builds in one process
    do not share a counter and disagree about a document that is otherwise
    byte-identical. Lifted from secondpass.Floats for the same reason.
    """

    def __init__(self) -> None:
        self.nonfinite = 0

    def __call__(self, v) -> float | None:
        if v is None:
            return None
        f = float(v)
        if math.isfinite(f):
            return f
        self.nonfinite += 1
        return None


def cycle_row(cycle: int, lane: str, ts: str, seconds, productive_seconds=None,
              records_appended: int = 0, points_measured: int = 0,
              lane_records_appended: int = 0, schedule: str | None = None,
              code_hash: str | None = None) -> dict:
    """One schedule-cycles/1 row. The caller supplies ts and seconds; no clock here.

    ``records_appended`` counts the scored archive and ``lane_records_appended`` the
    lane's own parallel archive. They stay apart because a lane cycle appends nothing
    to rk-work/archive/ by design, and that is a fact to read rather than a gap.
    ``productive_seconds`` is the part of the cycle that was the lane's own work, so
    the replay and publish overhead every cycle pays can be told from the measurement
    it was charged to.
    """
    if lane not in LANES:
        raise ValueError(f"unknown lane {lane!r}, want one of {list(LANES)}")
    fl = Floats()
    return {
        "cycle": int(cycle),
        "lane": str(lane),
        "ts": str(ts),
        "seconds": fl(seconds),
        "productive_seconds": None if productive_seconds is None else fl(productive_seconds),
        "records_appended": int(records_appended),
        "points_measured": int(points_measured),
        "lane_records_appended": int(lane_records_appended),
        "schedule": parse_schedule(schedule),
        "code_hash": None if code_hash is None else str(code_hash),
    }


def row_problem(row) -> str | None:
    """Why one row cannot be counted, or None. The reason is the key the shares
    document counts it under, so a damaged log says how it is damaged."""
    if not isinstance(row, dict):
        return "not_an_object"
    missing = [k for k in CYCLE_ROW_KEYS if k not in row]
    if missing:
        return "missing_" + missing[0]
    if not isinstance(row["cycle"], int) or isinstance(row["cycle"], bool):
        return "cycle_not_an_integer"
    if row["lane"] not in LANES:
        return "unknown_lane"
    if not isinstance(row["ts"], str) or not _TS_RE.match(row["ts"]):
        return "ts_not_utc_iso"
    s = row["seconds"]
    if not isinstance(s, (int, float)) or isinstance(s, bool) or not math.isfinite(float(s)):
        return "seconds_not_finite"
    if float(s) < 0.0:
        return "seconds_negative"
    for key in _COUNT_KEYS:
        v = row[key]
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            return f"{key}_not_a_count"
    return None


def append_cycle(row: dict, path: Path | str | None = None) -> Path:
    """Append one validated row to cycles.jsonl. Raises before writing, never after.

    Sorted keys and one line per cycle, so the file diffs cleanly and a tail read can
    stop at a line boundary. fsync per line follows sidetrack._append_ledger: the
    evidence for a cycle has to survive the container being killed during the next
    one.
    """
    problem = row_problem(row)
    if problem is not None:
        raise ValueError(f"cycle row: {problem}")
    out = Path(path) if path is not None else cycles_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return out


def _tail_lines(path: Path, max_lines: int, block: int = 65536) -> list[str]:
    """The last max_lines complete lines of a file, read from the end.

    The log grows by a line per cycle forever, so a full read would get slower every
    day for no reason. Reads backwards in blocks until it holds one more newline than
    it needs, then drops the leading fragment, which may be half a line.
    """
    if max_lines <= 0:
        return []
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        pos = fh.tell()
        data = b""
        while pos > 0 and data.count(b"\n") <= max_lines:
            step = min(block, pos)
            pos -= step
            fh.seek(pos)
            data = fh.read(step) + data
    lines = data.decode("utf-8", errors="replace").splitlines()
    if pos > 0 and lines:
        lines = lines[1:]
    return lines[-max_lines:]


@dataclass
class CycleLog:
    """What one bounded read of cycles.jsonl keeps. Mirrors secondpass.ScanResult: the
    rows, and honest counters for what did not survive the read."""
    rows: list[dict] = field(default_factory=list)
    lines_read: int = 0
    lines_discarded: int = 0
    exists: bool = False
    path: str = ""


def load_cycles(window_cycles: int = DEFAULT_WINDOW_CYCLES,
                path: Path | str | None = None) -> CycleLog:
    """The tail of the cycle log. Never reads events.jsonl and never the archive."""
    src = Path(path) if path is not None else cycles_path()
    log = CycleLog(path=str(src), exists=src.exists())
    if not log.exists:
        return log
    for line in _tail_lines(src, max(0, int(window_cycles))):
        line = line.strip()
        if not line:
            continue
        log.lines_read += 1
        try:
            obj = json.loads(line)
        except ValueError:
            log.lines_discarded += 1      # a torn last line, as archive.read_all discards one
            continue
        if not isinstance(obj, dict):
            log.lines_discarded += 1
            continue
        log.rows.append(obj)
    return log


# --------------------------------------------------------------------------- the document

_SCHEMA_DOC = (
    "'lanes' is what happened, aggregated from rk-work/schedule/cycles.jsonl over the "
    "last window_cycles rows; 'scheduled_share' is what the schedule string asks for; "
    "'drift' is the scheduled share subtracted from the measured one, per lane. "
    "Shares are fractions of the recorded wall seconds in the window, not of the day. "
    "Rows that cannot be "
    "counted are discarded and counted by reason in _meta, never averaged in")

_MEASURED_NOT_INTENDED = (
    "every number under 'lanes' comes from recorded per-cycle evidence; when no cycle "
    "has been recorded the shares are zero and 'cycles' is zero, and the scheduled "
    "split is never substituted for a measurement")

_NOT_A_PAGE_SOURCE = (
    "the traceability rule lists key_findings.json, validation/results.json, "
    "benchmark/results.json and the side-track ledger; this artifact is not on that "
    "list")


def _empty_lane() -> dict:
    return {"cycles": 0, "seconds": 0.0, "share": 0.0, "productive_seconds": 0.0,
            "records_appended": 0, "points_measured": 0, "lane_records_appended": 0}


def _span_seconds(start_ts: str, end_ts: str) -> float | None:
    """Seconds between two UTC stamps, or None if either will not parse.

    Arithmetic on stored values, not a clock read. Both stamps already match _TS_RE
    when this is called.
    """
    try:
        a = datetime.datetime.strptime(start_ts, "%Y-%m-%dT%H:%M:%SZ")
        b = datetime.datetime.strptime(end_ts, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return (b - a).total_seconds()


def _statement(lanes: dict, scheduled: dict, n_cycles: int, wall_seconds: float,
               schedule: str) -> str:
    """One sentence a person can paste, derived from the numbers and nothing else.

    Reads correctly whichever way the numbers fall, including the case this module
    exists for: a schedule asking for thirds over a log that recorded none of it.
    """
    want = ", ".join(f"{lane} {scheduled[lane] * 100.0:.4f}" for lane in LANES)
    if n_cycles <= 0 or wall_seconds <= 0.0:
        return (f"No cycles have been recorded, so no split has been measured. The "
                f"schedule {schedule!r} asks for {want} percent.")
    got = ", ".join(f"{lane} {lanes[lane]['share'] * 100.0:.4f}" for lane in LANES)
    return (f"Over {n_cycles} recorded cycles and {wall_seconds:.1f} recorded seconds "
            f"the measured split was {got} percent. The schedule {schedule!r} asks for "
            f"{want} percent.")


def build_shares(log: CycleLog | Sequence[dict] | None = None, *,
                 schedule: str | None = None,
                 lane_max_seconds_value: float | None = None,
                 window_cycles: int = DEFAULT_WINDOW_CYCLES,
                 generated_ts: str | None = None) -> dict:
    """The schedule-shares/1 document. Pure function of the rows plus this module.

    No wall clock: generated_ts defaults to the newest counted row's own stamp, so two
    builds over the same log are byte-identical and a page built from one cannot move
    while the inputs stand still. The runner may pass its own UTC stamp instead.
    """
    if log is None:
        log = load_cycles(window_cycles)
    elif not isinstance(log, CycleLog):
        rows = list(log)
        log = CycleLog(rows=rows, lines_read=len(rows), exists=True, path="")
    sched = parse_schedule(schedule)
    budget = float(lane_max_seconds_value if lane_max_seconds_value is not None
                   else LANE_MAX_SECONDS_DEFAULT)
    fl = Floats()

    lanes = {lane: _empty_lane() for lane in LANES}
    discarded: dict[str, int] = {}
    counted: list[dict] = []
    missing_productive = 0
    for row in log.rows:
        problem = row_problem(row)
        if problem is not None:
            # An uncountable row is set aside with its reason rather than folded in
            # at zero seconds: a row that says nothing about a lane must not become
            # evidence that the lane did nothing.
            discarded[problem] = discarded.get(problem, 0) + 1
            continue
        counted.append(row)
        agg = lanes[row["lane"]]
        agg["cycles"] += 1
        agg["seconds"] += float(row["seconds"])
        prod = row["productive_seconds"]
        if (isinstance(prod, (int, float)) and not isinstance(prod, bool)
                and math.isfinite(float(prod))):
            agg["productive_seconds"] += float(prod)
        else:
            missing_productive += 1
        for key in _COUNT_KEYS:
            agg[key] += int(row[key])

    wall = sum(lanes[lane]["seconds"] for lane in LANES)
    for lane in LANES:
        lanes[lane]["share"] = (lanes[lane]["seconds"] / wall) if wall > 0.0 else 0.0

    scheduled = scheduled_shares(sched)
    window = {"first_cycle": None, "last_cycle": None, "first_ts": None,
              "last_ts": None, "wall_seconds": fl(wall), "span_seconds": None}
    if counted:
        window["first_cycle"] = int(counted[0]["cycle"])
        window["last_cycle"] = int(counted[-1]["cycle"])
        window["first_ts"] = str(counted[0]["ts"])
        window["last_ts"] = str(counted[-1]["ts"])
        # wall_seconds is the sum of the rows and is the denominator of every share;
        # span_seconds is the clock those rows sit in. The two differ by exactly what
        # went unrecorded, which is the quantity nobody had before this document.
        window["span_seconds"] = fl(_span_seconds(counted[0]["ts"], counted[-1]["ts"]))

    doc = {
        "_meta": {
            "schema": SHARES_SCHEMA,
            "script": "rk_harness/lanes.py",
            "sources": ["rk-work/schedule/cycles.jsonl (tail, at most window_cycles lines)"],
            "cycles_schema": SCHEDULE_CYCLES_SCHEMA,
            "generated_cycle": window["last_cycle"],
            "generated_ts": generated_ts if generated_ts is not None else window["last_ts"],
            "schedule": sched,
            "lane_max_seconds": fl(budget),
            "window_cycles": int(window_cycles),
            "lanes": list(LANES),
            "rows_read": int(log.lines_read),
            "rows_counted": len(counted),
            "rows_discarded": sum(discarded.values()),
            "rows_discarded_by_reason": {k: discarded[k] for k in sorted(discarded)},
            "lines_discarded": int(log.lines_discarded),
            "rows_without_productive_seconds": missing_productive,
            "log_exists": bool(log.exists),
            "schema_doc": _SCHEMA_DOC,
            "measured_not_intended": _MEASURED_NOT_INTENDED,
            "not_a_public_page_source": _NOT_A_PAGE_SOURCE,
        },
        "window": window,
        "lanes": {lane: lanes[lane] for lane in LANES},
        "scheduled_share": scheduled,
        "drift": {lane: lanes[lane]["share"] - scheduled[lane] for lane in LANES},
        "statement": _statement(lanes, scheduled, len(counted), wall, sched),
    }
    doc["_meta"]["nonfinite_written_as_null"] = fl.nonfinite
    return doc


def _walk_floats(node, path: str, fail) -> None:
    if isinstance(node, dict):
        for k in sorted(node, key=str):
            _walk_floats(node[k], f"{path}.{k}", fail)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk_floats(v, f"{path}[{i}]", fail)
    elif isinstance(node, float) and not math.isfinite(node):
        fail(f"{path} must be finite or null, got {node!r}")


def _check_prose(text: str, path: str, fail) -> None:
    """The statement is the part of this document a person would paste somewhere, so
    it keeps the findings-site word rule even though the artifact is not a page
    source. Applied to prose only: the window keys are named first_cycle and
    first_ts, which this pattern flags and sitegen's own word-boundary pattern does
    not, so a key name must never be pushed through here.
    """
    low = str(text).lower()
    for word in _BANNED:
        if re.search(rf"(?<![a-z0-9-]){re.escape(word)}(?![a-z0-9-])", low):
            fail(f"{path} carries the banned word {word!r}")
    if _EM_DASH in str(text):
        fail(f"{path} carries an em dash")


def validate_shares(doc: dict) -> None:
    """Raise ValueError if the document violates schedule-shares/1."""
    def fail(msg: str):
        raise ValueError(f"shares schema: {msg}")

    if not isinstance(doc, dict) or "_meta" not in doc:
        fail("missing top-level key '_meta'")
    meta = doc["_meta"]
    if meta.get("schema") != SHARES_SCHEMA:
        fail(f"_meta.schema must be {SHARES_SCHEMA!r}")
    for key in ("script", "cycles_schema", "schedule", "schema_doc",
                "measured_not_intended", "not_a_public_page_source"):
        if not isinstance(meta.get(key), str):
            fail(f"_meta.{key} must be a string")
    if parse_schedule(meta["schedule"]) != meta["schedule"]:
        fail("_meta.schedule must already be a parsed schedule")
    for key in ("window_cycles", "rows_read", "rows_counted", "rows_discarded",
                "lines_discarded", "rows_without_productive_seconds",
                "nonfinite_written_as_null"):
        v = meta.get(key)
        if not isinstance(v, int) or isinstance(v, bool):
            fail(f"_meta.{key} must be an integer")
    if meta.get("lane_max_seconds") is not None and             not isinstance(meta["lane_max_seconds"], (int, float)):
        # None is legal: a non-finite budget is written as null and counted, the same
        # rule the rest of the document keeps.
        fail("_meta.lane_max_seconds must be a number or null")
    if meta.get("lanes") != list(LANES):
        fail(f"_meta.lanes must be {list(LANES)}")
    if not isinstance(meta.get("rows_discarded_by_reason"), dict):
        fail("_meta.rows_discarded_by_reason must be an object")
    if sum(meta["rows_discarded_by_reason"].values()) != meta["rows_discarded"]:
        fail("_meta.rows_discarded_by_reason must account for every discarded row")

    for key in ("window", "lanes", "scheduled_share", "drift"):
        if not isinstance(doc.get(key), dict):
            fail(f"missing top-level object {key!r}")
    win = doc["window"]
    for key in ("first_cycle", "last_cycle", "first_ts", "last_ts", "wall_seconds",
                "span_seconds"):
        if key not in win:
            fail(f"window.{key} is missing")
    if not isinstance(win["wall_seconds"], (int, float)):
        fail("window.wall_seconds must be a number")
    if (isinstance(win["first_ts"], str) and isinstance(win["last_ts"], str)
            and win["first_ts"] > win["last_ts"]):
        # Stamps are UTC ISO-8601 in a fixed shape, so a string compare is a time
        # compare. Without this a non-monotone log gives a negative span_seconds, and
        # span minus wall is rendered as "what went unrecorded"; a negative there is
        # nonsense on a page, so the document refuses itself instead.
        fail("window.first_ts is later than window.last_ts")

    if sorted(doc["lanes"]) != sorted(LANES):
        fail(f"lanes must cover exactly {list(LANES)}")
    total = 0.0
    for lane in LANES:
        agg = doc["lanes"][lane]
        for key in ("cycles", "records_appended", "points_measured",
                    "lane_records_appended"):
            v = agg.get(key)
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                fail(f"lanes.{lane}.{key} must be a non-negative integer")
        for key in ("seconds", "share", "productive_seconds"):
            v = agg.get(key)
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                fail(f"lanes.{lane}.{key} must be a number")
        if not 0.0 <= float(agg["share"]) <= 1.0:
            fail(f"lanes.{lane}.share must be between 0 and 1")
        total += float(agg["share"])
    if float(win["wall_seconds"]) > 0.0 and abs(total - 1.0) > 1e-9:
        fail(f"measured shares must sum to 1 over a non-empty window, got {total!r}")
    if float(win["wall_seconds"]) == 0.0 and total != 0.0:
        fail("an empty window must report zero shares, not the scheduled split")

    if sorted(doc["scheduled_share"]) != sorted(LANES):
        fail(f"scheduled_share must cover exactly {list(LANES)}")
    if abs(sum(float(v) for v in doc["scheduled_share"].values()) - 1.0) > 1e-12:
        fail("scheduled_share must sum to 1")
    if sorted(doc["drift"]) != sorted(LANES):
        fail(f"drift must cover exactly {list(LANES)}")
    for lane in LANES:
        want = float(doc["lanes"][lane]["share"]) - float(doc["scheduled_share"][lane])
        if abs(float(doc["drift"][lane]) - want) > 1e-12:
            fail(f"drift.{lane} must be the measured share less the scheduled one")

    if not isinstance(doc.get("statement"), str) or not doc["statement"].strip():
        fail("statement must be a non-empty string")
    _check_prose(doc["statement"], "statement", fail)
    _walk_floats(doc, "doc", fail)


def write_shares(doc: dict, path: Path | str | None = None) -> Path:
    out = Path(path) if path is not None else shares_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, indent=1, sort_keys=True, allow_nan=False) + "\n"
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return out


def update_shares(window_cycles: int = DEFAULT_WINDOW_CYCLES, *,
                  schedule: str | None = None,
                  lane_max_seconds_value: float | None = None,
                  generated_ts: str | None = None,
                  path: Path | str | None = None,
                  cycles_file: Path | str | None = None) -> Path:
    """Read the tail of the cycle log, build the document, validate it, write it.

    The one call the runner will make. Split from build_shares so the build stays a
    pure function of rows and can be tested without a filesystem.
    """
    log = load_cycles(window_cycles, cycles_file)
    doc = build_shares(
        log,
        schedule=lane_schedule() if schedule is None else schedule,
        lane_max_seconds_value=(lane_max_seconds() if lane_max_seconds_value is None
                                else lane_max_seconds_value),
        window_cycles=window_cycles,
        generated_ts=generated_ts)
    validate_shares(doc)
    return write_shares(doc, path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Lane schedule arithmetic and the realised time split (read only).")
    ap.add_argument("--window", type=int, default=DEFAULT_WINDOW_CYCLES,
                    help=f"cycles to aggregate (default: {DEFAULT_WINDOW_CYCLES})")
    ap.add_argument("--schedule", default=None,
                    help="schedule string (default: RK_LANE_SCHEDULE, then 'E')")
    ap.add_argument("--out", default=None,
                    help="output path (default: <RK_WORK_DIR>/schedule/shares.json)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the document instead of writing it")
    a = ap.parse_args(argv)

    sched = lane_schedule() if a.schedule is None else parse_schedule(a.schedule)
    problem = None if a.schedule is None else schedule_problem(a.schedule)
    if problem is not None:
        print(f"schedule {a.schedule!r} rejected ({problem}); using {sched!r}",
              file=sys.stderr)
    log = load_cycles(a.window)
    doc = build_shares(log, schedule=sched, lane_max_seconds_value=lane_max_seconds(),
                       window_cycles=a.window)
    validate_shares(doc)
    if a.dry_run:
        print(json.dumps(doc, indent=1, sort_keys=True, allow_nan=False))
    else:
        print(f"wrote {write_shares(doc, a.out)}")
    want = scheduled_shares(sched)
    print(f"schedule {sched!r}: " + ", ".join(f"{lane} {want[lane]:.4f}" for lane in LANES))
    print(doc["statement"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
