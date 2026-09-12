#!/usr/bin/env python
"""Rewrite the archive's merged `unreplicated` tier as `no_incumbent` or `no_improvement`.

Host side on purpose, and out of the container start path: this rewrites the scoring
record of truth, so a person decides when it runs.

    python scripts/backfill_tiers.py            # dry run; reports counts, writes nothing
    python scripts/backfill_tiers.py --apply     # rewrites, after a full backup

Why there is anything to backfill. `archive.assign_tier` returned one word for two
opposite cases: there was no incumbent to compare against, and there was an incumbent
the candidate improved on in neither aggregate. The code now returns `no_incumbent` and
`no_improvement`, and `unreplicated` stays a legal stored tier so that every record
written before the split still loads.

Which case an existing record meant is recoverable from `rk-work/events.jsonl`. It holds
one `accepted` row per archived record, in file order, carrying the record's cell as
`order`, `stages` and `bucket`, and the eight `baseline_seeded` rows the runner writes at
cycle 0. A record was the earliest in its cell exactly when its tier was assigned with no
incumbent in hand. The replay seeds the cell set with the eight baselines' own cells,
because a baseline holds its cell before the search starts: the earliest accepted record
in a cell a baseline already occupies did have an incumbent, and its tier says so.

The event stream recovers a label, not a number. Any count of the new tiers that reaches
a published page has to be read back from `rk-work/archive` after this has run.

Under --apply, in this order:
  * every rewritten line is re-validated through `archive.record_from_json`;
  * the new line must differ from the old one in `tier` and nowhere else;
  * the archive directory is copied whole to a backup outside this repository;
  * each file is written to a temp sibling and swapped with `os.replace`;
  * the swapped file is re-read and compared line for line against what was intended.

Cost. Editing `archive.py` already invalidated `ARCHIVE_CHECKPOINT.json`'s derivation
hash, and this rewrite moves every archive file's size and mtime, so the checkpoint is
rejected twice over and the next container start replays the whole archive. Land this at
a deliberate restart, not mid-cycle. The script does not delete the checkpoint: a
rejected checkpoint is reported and replaced on the next write, and deleting it would
throw away the record of what the run believed.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sys
from pathlib import Path

HARNESS = Path(__file__).resolve().parent.parent
WORKSPACE = HARNESS.parent
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

from rk_harness import archive                                    # noqa: E402
from rk_harness.paths import archive_dir, work_dir                # noqa: E402
from rk_harness.tableau import stages                             # noqa: E402
from rk_harness.types import TIERS                                # noqa: E402

LEGACY = "unreplicated"
NO_INCUMBENT = "no_incumbent"
NO_IMPROVEMENT = "no_improvement"

# The tier field as it appears in a record line. `json.dumps` writes exactly this, and
# the substring occurs once per line, so the rewrite is a byte edit rather than a
# re-serialization: every other byte of the record survives untouched.
_TIER_FIELD = f'"tier": "{LEGACY}"'


class BackfillError(Exception):
    """A precondition failed. Nothing has been written."""


# ------------------------------------------------------------------ events replay

def _events_path() -> Path:
    return work_dir() / "events.jsonl"


def _iter_events(path: Path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def _baseline_hashes(path: Path) -> list[str]:
    """The tableau hashes of the seeded classicals, in the order they were seeded."""
    out: list[str] = []
    for e in _iter_events(path):
        if e.get("kind") == "baseline_seeded":
            h = e.get("tableau_hash")
            if isinstance(h, str) and h not in out:
                out.append(h)
    return out


def _find_records(wanted: set[str]) -> dict[str, object]:
    """The archive records for `wanted`, read in file order and stopping when all are in.

    Used for the eight baselines only. Their cells cannot come from the event stream:
    a `baseline_seeded` row carries the hash and nothing about the cell.
    """
    found: dict[str, object] = {}
    for path in archive._archive_files():
        for rec in archive._read_file(path):
            if rec.tableau_hash in wanted and rec.tableau_hash not in found:
                found[rec.tableau_hash] = rec
                if len(found) == len(wanted):
                    return found
    return found


def _cell_of(rec) -> tuple[int, int, int]:
    """The (order, stages, bucket) key the runner compares against the elite map."""
    cycles = int(rec.score.cycles.get("m0plus_fast", 0))
    return (archive.record_order(rec), stages(rec.tableau), archive.cycle_bucket(cycles))


def _event_cell(e: dict) -> tuple[int, int, int] | None:
    try:
        return (int(e["order"]), int(e["stages"]), int(e["bucket"]))
    except (KeyError, TypeError, ValueError):
        return None


def no_incumbent_hashes(verbose: bool = True) -> tuple[set[str], dict]:
    """Every archived record that was written with no incumbent to compare against."""
    path = _events_path()
    if not path.is_file():
        raise BackfillError(f"{path} is absent; the tier a record meant is not recoverable")
    baselines = _baseline_hashes(path)
    if not baselines:
        raise BackfillError("no baseline_seeded events; refusing to guess the seeded cells")
    records = _find_records(set(baselines))
    missing = [h for h in baselines if h not in records]
    if missing:
        raise BackfillError(f"{len(missing)} seeded baselines are not in the archive: {missing}")
    seen: set[tuple[int, int, int]] = set()
    for h in baselines:
        seen.add(_cell_of(records[h]))
    stats = {"baselines": len(baselines), "baseline_cells": len(seen),
             "accepted": 0, "accepted_no_incumbent": 0, "cells": 0}
    out = set(baselines)
    for e in _iter_events(path):
        if e.get("kind") != "accepted":
            continue
        stats["accepted"] += 1
        cell = _event_cell(e)
        h = e.get("tableau_hash")
        if cell is None or not isinstance(h, str):
            continue
        if cell not in seen:
            seen.add(cell)
            out.add(h)
            stats["accepted_no_incumbent"] += 1
    stats["cells"] = len(seen)
    if verbose:
        print(f"events: {stats['accepted']:,} accepted rows, {stats['cells']} cells touched "
              f"({stats['baseline_cells']} of them opened by a seeded baseline)")
        print(f"no incumbent: {stats['baselines']} seeded baselines + "
              f"{stats['accepted_no_incumbent']} earliest-in-cell records = {len(out)}")
    return out, stats


# ------------------------------------------------------------------ the plan

def new_tier(old: str, hash_: str, no_inc: set[str]) -> str:
    if old != LEGACY:
        return old
    return NO_INCUMBENT if hash_ in no_inc else NO_IMPROVEMENT


def plan_file(path: Path, no_inc: set[str]) -> dict:
    """What this file's lines would become. Reads only."""
    counts = {"lines": 0, "records": 0, "unparsable": 0, LEGACY: 0,
              NO_INCUMBENT: 0, NO_IMPROVEMENT: 0, "unchanged": 0, "conflicts": []}
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.strip():
                continue
            counts["lines"] += 1
            try:
                d = json.loads(line)
            except ValueError:
                counts["unparsable"] += 1
                continue
            counts["records"] += 1
            old = d.get("tier")
            h = d.get("tableau_hash")
            if old != LEGACY:
                counts["unchanged"] += 1
                if h in no_inc:
                    counts["conflicts"].append((h, old))
                continue
            counts[LEGACY] += 1
            counts[new_tier(old, h, no_inc)] += 1
    return counts


def plan(no_inc: set[str]) -> list[tuple[Path, dict]]:
    files = archive._archive_files()
    if not files:
        raise BackfillError(f"no archive files under {archive_dir()}")
    return [(p, plan_file(p, no_inc)) for p in files]


def report(rows: list[tuple[Path, dict]], no_inc: set[str]) -> dict:
    total = {"lines": 0, "records": 0, "unparsable": 0, LEGACY: 0,
             NO_INCUMBENT: 0, NO_IMPROVEMENT: 0, "unchanged": 0}
    conflicts: list[tuple[str, str]] = []
    print(f"{'file':<22}{'records':>10}{'legacy':>10}{'no_incumbent':>15}{'no_improvement':>16}")
    for path, c in rows:
        for k in total:
            total[k] += c[k]
        conflicts.extend(c["conflicts"])
        print(f"{path.name:<22}{c['records']:>10,}{c[LEGACY]:>10,}"
              f"{c[NO_INCUMBENT]:>15,}{c[NO_IMPROVEMENT]:>16,}")
    print(f"{'TOTAL':<22}{total['records']:>10,}{total[LEGACY]:>10,}"
          f"{total[NO_INCUMBENT]:>15,}{total[NO_IMPROVEMENT]:>16,}")
    print(f"records carrying another tier, untouched: {total['unchanged']:,}")
    if total["unparsable"]:
        print(f"unparsable lines, left exactly as they are: {total['unparsable']}")
    seen_no_inc = total[NO_INCUMBENT]
    if seen_no_inc != len(no_inc):
        print(f"NOTE: the replay names {len(no_inc)} records with no incumbent and the "
              f"archive holds {seen_no_inc} of them")
    if conflicts:
        print(f"CONFLICT: {len(conflicts)} records have no incumbent by the replay but "
              f"carry another tier already: {conflicts[:5]}")
    total["conflicts"] = len(conflicts)
    return total


# ------------------------------------------------------------------ the rewrite

def _default_backup_root() -> Path:
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = archive_dir().resolve().anchor or str(Path.home())
    return Path(root) / "rk-archive-backup" / stamp


def _check_outside_repo(p: Path) -> Path:
    p = p.resolve()
    ws = WORKSPACE.resolve()
    if p == ws or ws in p.parents:
        raise BackfillError(f"backup {p} is inside the workspace {ws}; put it elsewhere")
    return p


def rewrite_file(path: Path, no_inc: set[str]) -> tuple[list[str], dict]:
    """The file's new lines, with every one of them validated. Writes nothing."""
    lines: list[str] = []
    counts = {"records": 0, "rewritten": 0}
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh, start=1):
            if not line.strip():
                lines.append(line)
                continue
            try:
                d = json.loads(line)
            except ValueError:
                lines.append(line)          # a partial trailing line stays partial
                continue
            counts["records"] += 1
            old = d.get("tier")
            want = new_tier(old, d.get("tableau_hash"), no_inc)
            if want == old:
                lines.append(line)
                continue
            if line.count(_TIER_FIELD) != 1:
                raise BackfillError(f"{path.name}:{i}: {_TIER_FIELD} is not in the line exactly once")
            out = line.replace(_TIER_FIELD, f'"tier": "{want}"')
            new_d = json.loads(out)
            if {k: v for k, v in new_d.items() if k != "tier"} != {
                    k: v for k, v in d.items() if k != "tier"}:
                raise BackfillError(f"{path.name}:{i}: the rewrite changed more than the tier")
            if new_d["tier"] != want:
                raise BackfillError(f"{path.name}:{i}: tier did not take the new value")
            archive.record_from_json(new_d)         # the schema check the reader will run
            lines.append(out)
            counts["rewritten"] += 1
    return lines, counts


def apply(no_inc: set[str], backup_root: Path) -> int:
    for tier in (NO_INCUMBENT, NO_IMPROVEMENT):
        if tier not in TIERS:
            raise BackfillError(f"{tier} is not in types.TIERS; the rewritten records would not load")
    files = archive._archive_files()
    backup = _check_outside_repo(backup_root)
    backup.mkdir(parents=True, exist_ok=False)
    rewritten = 0
    for path in files:
        lines, counts = rewrite_file(path, no_inc)
        shutil.copy2(path, backup / path.name)
        if counts["rewritten"] == 0:
            print(f"{path.name}: nothing to rewrite ({counts['records']:,} records)")
            continue
        tmp = path.parent / f".{path.name}.backfill.tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write("".join(lines))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        after = path.read_text(encoding="utf-8", errors="replace")
        if after != "".join(lines):
            raise BackfillError(f"{path.name}: the file on disk is not what was written")
        rewritten += counts["rewritten"]
        print(f"{path.name}: {counts['rewritten']:,} of {counts['records']:,} records rewritten, "
              f"backup {backup / path.name}")
    print(f"rewrote {rewritten:,} records; backup at {backup}")
    print("ARCHIVE_CHECKPOINT.json no longer matches the files it covers. The next "
          "container start replays the whole archive and writes a new one.")
    return rewritten


# ------------------------------------------------------------------ CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="rewrite the archive. Without it this reports and writes nothing.")
    ap.add_argument("--backup", default=None,
                    help="backup directory, which must be outside this workspace "
                         "(default: <archive drive>/rk-archive-backup/<UTC stamp>)")
    args = ap.parse_args(argv)
    try:
        print(f"archive: {archive_dir()}")
        no_inc, _stats = no_incumbent_hashes()
        rows = plan(no_inc)
        total = report(rows, no_inc)
        if not args.apply:
            print("\ndry run: nothing was written. Pass --apply to rewrite.")
            return 0
        if total["conflicts"]:
            raise BackfillError("conflicts above; resolve them before rewriting")
        root = Path(args.backup) if args.backup else _default_backup_root()
        apply(no_inc, root)
    except BackfillError as e:
        print(f"backfill_tiers: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
