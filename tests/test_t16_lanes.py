"""T16 tests: the lane scheduler and the realised time split (rk_harness/lanes.py).

Covers: lane_for is a pure function of (cycle_id, schedule) and reads no
environment; every unusable schedule resolves to 'E', which is the cycle loop as
it stands, so the module ships disarmed and a typo cannot arm a restructure;
schedule_problem says what was wrong so a silent fallback is still a noticed one;
scheduled_shares is arithmetic on the string and is never substituted for a
measurement; the literature letters are mapped in one place and the legacy lane
string reproduces literature.TRACK_SCHEDULE exactly; the environment readers
default to today's behaviour and the lane budget is clamped to the stop-grace
range; a cycle row round-trips through cycles.jsonl; the log is read from the end
so a year of lines costs nothing; rows that cannot be counted are set aside by
reason rather than averaged in at zero; the shares document measures what happened
and reports no measurement when there is none; two builds over the same rows are
byte-identical and no clock is read anywhere; the document validates and damaged
documents are rejected.

Pure Python: this file imports the standard library, lanes and literature, so a
collection error here cannot come from a missing scientific dependency. Every log
is synthetic and written under the throwaway work dir conftest sets.
"""
from __future__ import annotations

import ast
import copy
import json
import math
import re
from pathlib import Path

import pytest

from rk_harness import lanes as L
from rk_harness import literature
from rk_harness.paths import HARNESS_DIR, work_dir

BANNED = ("novel", "first", "beats", "outperforms", "breakthrough", "proves",
          "state-of-the-art", "best-ever")
EM_DASH = chr(0x2014)


# --------------------------------------------------------------------------- helpers

def _row(cycle: int, lane: str, seconds: float = 176.0, **kw) -> dict:
    """One well-formed row. The stamp is derived from the cycle id so a log is a pure
    function of its length and two builds cannot differ by a second."""
    hh, mm = divmod(cycle * 3, 60)
    kw.setdefault("productive_seconds", 100.0)
    kw.setdefault("schedule", "EAI")
    return L.cycle_row(cycle, lane, f"2026-09-09T{hh % 24:02d}:{mm:02d}:00Z", seconds, **kw)


def _write(path: Path, rows) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    return path


# --------------------------------------------------------------------------- the schedule

def test_lane_for_is_a_pure_function_of_the_cycle_id_and_the_schedule(monkeypatch):
    monkeypatch.setenv("RK_LANE_SCHEDULE", "AAAAA")     # must not be read by lane_for
    assert [L.lane_for(c, "EAI") for c in range(6)] == [
        "explicit", "adaptive", "implicit", "explicit", "adaptive", "implicit"]
    # Same arguments, same answer, whatever else has happened in between.
    assert L.lane_for(7, "EAI") == L.lane_for(7, "EAI") == L.lane_for(4, "EAI")
    assert L.lane_for(0, "EAI") == "explicit"


def test_lane_for_follows_the_string_position_by_position():
    sched = L.LEGACY_70_15_15
    for c in range(2 * len(sched)):
        assert L.lane_for(c, sched) == L.LETTERS[sched[c % len(sched)]]


def test_the_lane_counts_over_one_period_are_the_scheduled_shares():
    sched = L.LEGACY_70_15_15
    counts = {lane: 0 for lane in L.LANES}
    for c in range(len(sched)):
        counts[L.lane_for(c, sched)] += 1
    want = L.scheduled_shares(sched)
    for lane in L.LANES:
        assert counts[lane] / len(sched) == pytest.approx(want[lane], abs=1e-12)


@pytest.mark.parametrize("value", [
    None, "", "   ", "ZZZ", "EAIX", "eai eai", "E A I", "1", "EAI\n\nEAI",
    "E" * (L.MAX_SCHEDULE_LEN + 1), 3, ["E", "A"],
])
def test_an_unusable_schedule_ships_disarmed(value):
    """The whole safety property of this batch: anything the module cannot read whole
    means today's behaviour, and today's behaviour is every cycle explicit."""
    assert L.parse_schedule(value) == L.DEFAULT_SCHEDULE == "E"
    assert L.schedule_problem(value) is not None
    assert all(L.lane_for(c, value) == "explicit" for c in range(25))
    assert L.scheduled_shares(value) == {"explicit": 1.0, "adaptive": 0.0, "implicit": 0.0}
    assert not L.armed(value)


def test_a_typo_is_rejected_whole_rather_than_filtered_down():
    """'EAIX' must not become 'EAI'. Filtering would arm two thirds of the machine
    from a stray keystroke."""
    assert L.parse_schedule("EAIX") == "E"
    assert "X" in L.schedule_problem("EAIX")


@pytest.mark.parametrize("value,fragment", [
    (None, "unset"),
    ("", "empty"),
    ("E" * 61, "longer than 60"),
    ("EAB", "'B'"),
])
def test_schedule_problem_names_what_is_wrong(value, fragment):
    problem = L.schedule_problem(value)
    assert problem is not None and fragment in problem


def test_a_usable_schedule_is_upper_cased_and_stripped():
    assert L.parse_schedule("  eai  ") == "EAI"
    assert L.parse_schedule("EAI") == "EAI"
    assert L.schedule_problem(" eai ") is None
    assert L.parse_schedule("E" * L.MAX_SCHEDULE_LEN) == "E" * L.MAX_SCHEDULE_LEN


def test_scheduled_shares_are_thirds_for_the_thirds_string():
    got = L.scheduled_shares(L.THIRDS)
    assert L.THIRDS == "EAI"
    for lane in L.LANES:
        assert got[lane] == pytest.approx(1.0 / 3.0, abs=1e-12)
    assert sum(got.values()) == pytest.approx(1.0, abs=1e-12)
    assert list(got) == list(L.LANES)


def test_the_legacy_string_is_the_roadmaps_split():
    got = L.scheduled_shares(L.LEGACY_70_15_15)
    assert got["explicit"] == pytest.approx(0.70, abs=1e-12)
    assert got["adaptive"] == pytest.approx(0.15, abs=1e-12)
    assert got["implicit"] == pytest.approx(0.15, abs=1e-12)


def test_literature_schedule_maps_the_lanes_onto_the_track_letters():
    assert L.literature_schedule("EAI") == "ABC"
    assert L.literature_schedule("E") == "A"
    assert L.literature_schedule(None) == "A"
    # The one place the two alphabets meet: 'A' is adaptive as a lane and the lead
    # track in literature.py, so this mapping is what keeps a single config value
    # from setting two different splits.
    assert L.literature_schedule(L.LEGACY_70_15_15) == literature.TRACK_SCHEDULE
    assert set(L.literature_schedule("EAI")) <= set(literature.TRACK_TOPICS)


def test_armed_reads_the_letters_not_the_length():
    assert not L.armed("E")
    assert not L.armed("EEEEE")
    assert not L.armed(None)
    assert L.armed("EAI")
    assert L.armed("EA")


# --------------------------------------------------------------------------- environment

def test_the_environment_readers_default_to_todays_behaviour(monkeypatch):
    monkeypatch.delenv("RK_LANE_SCHEDULE", raising=False)
    monkeypatch.delenv("RK_LANE_MAX_SECONDS", raising=False)
    assert L.lane_schedule() == "E"
    assert L.lane_max_seconds() == L.LANE_MAX_SECONDS_DEFAULT == 100.0
    monkeypatch.setenv("RK_LANE_SCHEDULE", "eai")
    assert L.lane_schedule() == "EAI"
    monkeypatch.setenv("RK_LANE_SCHEDULE", "not a schedule")
    assert L.lane_schedule() == "E"


@pytest.mark.parametrize("raw,want", [
    ("", 100.0), ("nonsense", 100.0), ("nan", 100.0), ("inf", 100.0),
    ("1", 30.0), ("-5", 30.0), ("100", 100.0), ("420", 420.0), ("100000", 420.0),
])
def test_lane_max_seconds_is_clamped_to_the_stop_grace_range(monkeypatch, raw, want):
    """A budget cannot arm anything on its own, so an out-of-range value is clamped
    the way runner._sidetrack_max_seconds clamps its own. The ceiling is the graceful
    stop arithmetic, not a preference. A value that is not a number at all, infinity
    included, takes the default rather than the ceiling: clamping it would hand the
    longest legal lane budget to a typo."""
    monkeypatch.setenv("RK_LANE_MAX_SECONDS", raw)
    assert L.lane_max_seconds() == want
    assert L.LANE_MAX_SECONDS_FLOOR <= L.lane_max_seconds() <= L.LANE_MAX_SECONDS_CEILING


# --------------------------------------------------------------------------- the cycle log

def test_cycle_row_carries_the_declared_key_set():
    row = _row(12, "adaptive", points_measured=4)
    assert sorted(row) == sorted(L.CYCLE_ROW_KEYS)
    assert L.row_problem(row) is None
    assert row["cycle"] == 12 and row["lane"] == "adaptive" and row["points_measured"] == 4
    assert row["ts"].endswith("Z")


def test_cycle_row_rejects_an_unknown_lane():
    with pytest.raises(ValueError):
        L.cycle_row(1, "sidetrack", "2026-09-09T00:00:00Z", 10.0)


@pytest.mark.parametrize("mutate,reason", [
    (lambda r: r.pop("seconds"), "missing_seconds"),
    (lambda r: r.update(lane="nowhere"), "unknown_lane"),
    (lambda r: r.update(ts="2026-09-09T00:00:00-05:00"), "ts_not_utc_iso"),
    (lambda r: r.update(ts="2026-09-09 00:00:00"), "ts_not_utc_iso"),
    (lambda r: r.update(cycle="12"), "cycle_not_an_integer"),
    (lambda r: r.update(seconds=None), "seconds_not_finite"),
    (lambda r: r.update(seconds=-1.0), "seconds_negative"),
    (lambda r: r.update(points_measured=-2), "points_measured_not_a_count"),
])
def test_row_problem_names_the_damage(mutate, reason):
    row = _row(3, "implicit")
    mutate(row)
    assert L.row_problem(row) == reason


def test_append_cycle_refuses_a_damaged_row_and_writes_nothing(tmp_path):
    path = tmp_path / "cycles.jsonl"
    bad = _row(1, "explicit")
    bad["ts"] = "yesterday"
    with pytest.raises(ValueError):
        L.append_cycle(bad, path)
    assert not path.exists()


def test_append_and_load_round_trip_through_the_work_dir():
    rows = [_row(c, L.lane_for(c, "EAI")) for c in range(5)]
    for row in rows:
        L.append_cycle(row)
    assert L.cycles_path() == work_dir() / "schedule" / "cycles.jsonl"
    log = L.load_cycles(200)
    assert log.exists and log.lines_read == 5 and log.lines_discarded == 0
    assert log.rows == rows


def test_load_cycles_reads_only_the_tail():
    """The log grows by a line per cycle forever, so the window is read from the end
    rather than by loading the file."""
    rows = [_row(c, L.lane_for(c, "EAI")) for c in range(500)]
    _write(L.cycles_path(), rows)
    log = L.load_cycles(200)
    assert len(log.rows) == 200
    assert log.rows[0]["cycle"] == 300 and log.rows[-1]["cycle"] == 499
    assert L.load_cycles(1).rows[0]["cycle"] == 499
    assert L.load_cycles(0).rows == []
    assert len(L.load_cycles(10_000).rows) == 500


def test_a_torn_last_line_is_discarded_and_counted():
    path = L.cycles_path()
    _write(path, [_row(c, "explicit") for c in range(3)])
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write('{"cycle": 4, "lane": "expl')
    log = L.load_cycles(200)
    assert len(log.rows) == 3 and log.lines_discarded == 1
    doc = L.build_shares(log, schedule="E")
    L.validate_shares(doc)
    assert doc["_meta"]["lines_discarded"] == 1


def test_a_missing_log_is_not_an_error():
    log = L.load_cycles(200)
    assert not log.exists and log.rows == [] and log.lines_read == 0


# --------------------------------------------------------------------------- the document

def test_the_realised_split_is_measured_and_not_assumed():
    """The finding this module exists for: a schedule can ask for thirds while every
    recorded cycle went to one lane, and the document has to say the second thing."""
    rows = [_row(c, "explicit") for c in range(20)]
    doc = L.build_shares(rows, schedule="EAI", lane_max_seconds_value=100.0)
    L.validate_shares(doc)
    assert doc["lanes"]["explicit"]["share"] == pytest.approx(1.0, abs=1e-12)
    assert doc["lanes"]["adaptive"]["share"] == 0.0
    assert doc["lanes"]["implicit"]["share"] == 0.0
    assert doc["scheduled_share"]["adaptive"] == pytest.approx(1.0 / 3.0, abs=1e-12)
    assert doc["drift"]["adaptive"] == pytest.approx(-1.0 / 3.0, abs=1e-12)
    assert doc["drift"]["explicit"] == pytest.approx(2.0 / 3.0, abs=1e-12)
    assert "33.3333" in doc["statement"] and "100.0000" in doc["statement"]


def test_the_measured_split_follows_the_seconds_not_the_cycle_count():
    """Equal cadence is not equal time: three cycles each, one of them long."""
    rows = [_row(0, "explicit", 300.0), _row(1, "adaptive", 100.0),
            _row(2, "implicit", 100.0)]
    doc = L.build_shares(rows, schedule="EAI")
    L.validate_shares(doc)
    assert doc["lanes"]["explicit"]["cycles"] == doc["lanes"]["adaptive"]["cycles"] == 1
    assert doc["lanes"]["explicit"]["share"] == pytest.approx(0.6, abs=1e-12)
    assert doc["window"]["wall_seconds"] == pytest.approx(500.0, abs=1e-12)


def test_an_empty_window_reports_no_measurement_rather_than_the_intention():
    doc = L.build_shares([], schedule="EAI")
    L.validate_shares(doc)
    for lane in L.LANES:
        assert doc["lanes"][lane]["cycles"] == 0
        assert doc["lanes"][lane]["share"] == 0.0
    assert doc["window"]["wall_seconds"] == 0.0
    assert doc["window"]["first_cycle"] is None and doc["window"]["last_ts"] is None
    assert doc["_meta"]["generated_cycle"] is None
    assert "no split has been measured" in doc["statement"]
    assert doc["scheduled_share"]["adaptive"] == pytest.approx(1.0 / 3.0, abs=1e-12)


def test_rows_that_cannot_be_counted_are_set_aside_by_reason():
    good = [_row(c, "explicit") for c in range(3)]
    torn = _row(9, "adaptive")
    torn["ts"] = "2026-09-09T00:00:00+00:00"
    unknown = dict(_row(10, "implicit"), lane="rocket")
    doc = L.build_shares(good + [torn, unknown, {"cycle": 11}], schedule="E")
    L.validate_shares(doc)
    meta = doc["_meta"]
    assert meta["rows_counted"] == 3
    assert meta["rows_discarded"] == 3
    assert meta["rows_discarded_by_reason"] == {
        "ts_not_utc_iso": 1, "unknown_lane": 1, "missing_lane": 1}
    # A row that says nothing about a lane must not become evidence that the lane
    # did nothing: the adaptive seconds are absent, not zero-with-a-cycle.
    assert doc["lanes"]["adaptive"]["cycles"] == 0


def test_the_window_reports_the_clock_its_rows_sit_in():
    rows = [_row(c, "explicit") for c in range(10)]
    doc = L.build_shares(rows, schedule="E")
    assert doc["window"]["first_cycle"] == 0 and doc["window"]["last_cycle"] == 9
    assert doc["window"]["span_seconds"] == pytest.approx(9 * 180.0, abs=1e-9)
    # 1760 s of recorded work inside a 1620 s span means the rows overlap or a stamp
    # is wrong; publishing both numbers is what makes that visible.
    assert doc["window"]["wall_seconds"] == pytest.approx(1760.0, abs=1e-9)


def test_a_row_without_productive_seconds_is_counted_as_missing_not_as_zero():
    rows = [_row(0, "explicit"), _row(1, "adaptive", productive_seconds=None)]
    doc = L.build_shares(rows, schedule="EA")
    L.validate_shares(doc)
    assert doc["_meta"]["rows_without_productive_seconds"] == 1
    assert doc["lanes"]["adaptive"]["productive_seconds"] == 0.0
    assert doc["lanes"]["adaptive"]["cycles"] == 1


def test_a_nonfinite_number_is_written_as_null_and_counted():
    doc = L.build_shares([], schedule="E", lane_max_seconds_value=float("inf"))
    L.validate_shares(doc)
    assert doc["_meta"]["lane_max_seconds"] is None
    assert doc["_meta"]["nonfinite_written_as_null"] == 1


def test_two_builds_over_the_same_rows_are_byte_identical():
    rows = [_row(c, L.lane_for(c, "EAI")) for c in range(30)]
    a = json.dumps(L.build_shares(rows, schedule="EAI"), indent=1, sort_keys=True,
                   allow_nan=False)
    b = json.dumps(L.build_shares(rows, schedule="EAI"), indent=1, sort_keys=True,
                   allow_nan=False)
    assert a == b
    # The default stamp is the newest row's own, so the document does not move on its
    # own between two builds.
    assert L.build_shares(rows, schedule="EAI")["_meta"]["generated_ts"] == rows[-1]["ts"]


def test_the_module_reads_no_clock():
    """A page rendered from this document has to be a pure function of its inputs, so
    the clock stays with the caller. Checked in the source, because a test that only
    exercised one path would miss the next one added."""
    src = (HARNESS_DIR / "rk_harness" / "lanes.py").read_text(encoding="utf-8")
    body = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
    for pattern in (r"\.now\(", r"utcnow", r"time\.time\(", r"perf_counter", r"random\."):
        assert not re.search(pattern, body), f"lanes.py reads {pattern}"


def test_the_module_imports_only_the_standard_library_and_paths():
    """The container's start-up gate collects the whole suite, so a heavy or missing
    import here would stop the run starting rather than fail a test."""
    tree = ast.parse((HARNESS_DIR / "rk_harness" / "lanes.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    internal = {m for m in imported if m.startswith("rk_harness")}
    assert internal == {"rk_harness.paths"}
    assert "numpy" not in imported and "scipy" not in imported and "mpmath" not in imported


def test_the_statement_carries_no_banned_word_and_no_em_dash():
    for rows, sched in (([], "EAI"),
                        ([_row(c, "explicit") for c in range(4)], "EAI"),
                        ([_row(c, L.lane_for(c, "EAI")) for c in range(6)], "EAI")):
        text = L.build_shares(rows, schedule=sched)["statement"]
        low = text.lower()
        for word in BANNED:
            assert not re.search(rf"(?<![a-z0-9-]){re.escape(word)}(?![a-z0-9-])", low)
        assert EM_DASH not in text


def test_the_document_says_it_is_not_a_public_page_source():
    doc = L.build_shares([], schedule="E")
    assert doc["_meta"]["schema"] == L.SHARES_SCHEMA == "schedule-shares/1"
    assert doc["_meta"]["cycles_schema"] == "schedule-cycles/1"
    assert "not on that list" in doc["_meta"]["not_a_public_page_source"]
    assert "never substituted" in doc["_meta"]["measured_not_intended"]


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("_meta"),
    lambda d: d["_meta"].update(schema="schedule-shares/2"),
    lambda d: d["_meta"].update(schedule="ZZZ"),
    lambda d: d["_meta"].update(lanes=["explicit"]),
    lambda d: d["_meta"].update(rows_discarded=7),
    lambda d: d["lanes"].pop("implicit"),
    lambda d: d["lanes"]["explicit"].update(share=0.5),
    lambda d: d["lanes"]["explicit"].update(cycles=-1),
    lambda d: d["scheduled_share"].update(explicit=0.9),
    lambda d: d["drift"].update(explicit=0.5),
    lambda d: d.update(statement=""),
    lambda d: d.update(statement="the earliest lane proves it"),
    lambda d: d.update(statement="a split " + EM_DASH + " measured"),
    lambda d: d["window"].pop("wall_seconds"),
])
def test_validate_shares_rejects_a_damaged_document(mutate):
    doc = L.build_shares([_row(c, "explicit") for c in range(3)], schedule="E")
    L.validate_shares(doc)
    broken = copy.deepcopy(doc)
    mutate(broken)
    with pytest.raises((ValueError, KeyError)):
        L.validate_shares(broken)


def test_an_empty_window_may_not_borrow_the_scheduled_split():
    doc = L.build_shares([], schedule="EAI")
    for lane in L.LANES:
        doc["lanes"][lane]["share"] = doc["scheduled_share"][lane]
    with pytest.raises(ValueError):
        L.validate_shares(doc)


# --------------------------------------------------------------------------- writing

def test_write_shares_uses_the_house_json_shape():
    doc = L.build_shares([_row(c, "explicit") for c in range(3)], schedule="E")
    out = L.write_shares(doc)
    assert out == work_dir() / "schedule" / "shares.json"
    text = out.read_text(encoding="utf-8")
    assert text.endswith("}\n") and "\r" not in text
    assert text.splitlines()[1].startswith(' "')          # indent=1
    assert json.loads(text) == doc
    keys = [k for k in json.loads(text)]
    assert keys == sorted(keys)                            # sort_keys=True
    assert L.write_shares(doc).read_text(encoding="utf-8") == text


def test_update_shares_reads_the_log_and_writes_a_valid_document(monkeypatch):
    monkeypatch.setenv("RK_LANE_SCHEDULE", "EAI")
    monkeypatch.setenv("RK_LANE_MAX_SECONDS", "100")
    for c in range(9):
        L.append_cycle(_row(c, L.lane_for(c, "EAI")))
    out = L.update_shares(window_cycles=5)
    doc = json.loads(out.read_text(encoding="utf-8"))
    L.validate_shares(doc)
    assert doc["_meta"]["schedule"] == "EAI"
    assert doc["_meta"]["window_cycles"] == 5
    assert doc["_meta"]["lane_max_seconds"] == 100.0
    assert doc["_meta"]["rows_counted"] == 5
    assert doc["window"]["first_cycle"] == 4 and doc["window"]["last_cycle"] == 8
    assert sum(doc["lanes"][lane]["cycles"] for lane in L.LANES) == 5


def test_the_command_line_writes_the_document(capsys, monkeypatch):
    monkeypatch.delenv("RK_LANE_SCHEDULE", raising=False)
    for c in range(4):
        L.append_cycle(_row(c, "explicit"))
    assert L.main(["--window", "10"]) == 0
    out = capsys.readouterr().out
    assert "wrote" in out and "explicit 1.0000" in out
    doc = json.loads(L.shares_path().read_text(encoding="utf-8"))
    L.validate_shares(doc)
    assert doc["_meta"]["schedule"] == "E"


def test_the_command_line_says_when_it_rejected_a_schedule(capsys):
    """A rejected schedule falls back silently inside the runner and loudly here: the
    host tool is where a person is standing, so it says what it did with the value."""
    assert L.main(["--schedule", "EAIX", "--dry-run"]) == 0
    cap = capsys.readouterr()
    assert "rejected" in cap.err and "outside" in cap.err
    assert '"schedule": "E"' in cap.out
    assert not L.shares_path().exists()          # --dry-run writes nothing


def test_nothing_written_here_carries_a_non_finite_float():
    doc = L.build_shares([_row(c, "adaptive") for c in range(2)], schedule="EAI")
    text = json.dumps(doc, allow_nan=False)          # raises on NaN or Infinity
    for value in json.loads(text)["lanes"]["adaptive"].values():
        assert not isinstance(value, float) or math.isfinite(value)
