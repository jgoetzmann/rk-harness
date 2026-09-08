"""Host-side comparison of the exploration policies (P05). Hand-written, unpinned.

One streaming pass over ``events.jsonl`` that splits the run into policy blocks and reports
what each block produced: cycles, median accepted records per cycle, the fraction of
candidates skipped as already seen, new elites, and cells occupied for the earliest time
during that policy. It never reads the file whole; at 45 MB that matters.

Attribution. A fallback directive id of the form ``D-FE00123`` / ``D-FR00123`` names the
policy that chose the cell for that record, and it is the only part of the attribution that
survives in the archive itself. Everything else in a cycle belongs to the policy recorded on
that cycle's closing ``cycle_done`` event, which is more specific (it also says whether the
search warm started), so the two are combined: the cycle policy wins when its cell half
agrees with the id letter, the letter wins when they disagree, and a record with neither
lands in the ``unlabelled`` bucket. Every event written before P05 shipped is unlabelled,
which is why the bucket exists rather than silently joining the default policy.

Reproducibility. ``rk-work/.gitignore`` ignores ``events.jsonl``, so the event log is
host-local and is never pushed. Any comparison that rests on it cannot be reproduced from
the published record, and that has to be said wherever such a comparison is published. The
directive-id letter is the part a reader of the archive can check.

CLI:  python -m rk_harness.policyab [path-to-events.jsonl]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from rk_harness.encourager import cell_policy
from rk_harness.paths import work_dir

UNLABELLED = "unlabelled"

_FALLBACK_ID_RE = re.compile(r"^D-F([ER])[0-9]{5}$")
_LETTER_POLICY = {"E": "empty", "R": "revisit"}


def _letter_policy(directive_id) -> str | None:
    """The policy named by a fallback directive id, or None for every other id."""
    if not isinstance(directive_id, str):
        return None
    m = _FALLBACK_ID_RE.match(directive_id)
    return _LETTER_POLICY[m.group(1)] if m else None


def _resolve(letter: str | None, cycle_policy: str | None) -> str:
    if cycle_policy is None:
        return letter or UNLABELLED
    if letter is None or cell_policy(cycle_policy) == letter:
        return cycle_policy
    return letter


def _blank() -> dict:
    return {"cycles": 0, "accepted": 0, "rejected": 0, "skipped": 0, "new_elites": 0,
            "cells": set(), "accepted_per_cycle": []}


def _bucket(totals: dict, name: str) -> dict:
    if name not in totals:
        totals[name] = _blank()
    return totals[name]


def _median(values: list[int]) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    mid = len(xs) // 2
    if len(xs) % 2:
        return float(xs[mid])
    return (xs[mid - 1] + xs[mid]) / 2.0


def _cell_of(e: dict) -> tuple[int, int, int] | None:
    try:
        return (int(e["order"]), int(e["stages"]), int(e["bucket"]))
    except (KeyError, TypeError, ValueError):
        return None


def scan_policies(path=None) -> dict:
    """One pass over events.jsonl; per-policy totals. `path` overrides the work dir file."""
    target = Path(path) if path is not None else work_dir() / "events.jsonl"
    totals: dict[str, dict] = {}
    seen_cells: set[tuple[int, int, int]] = set()
    # Counters for the cycle currently being read, keyed by the record's own id letter. They
    # cannot be attributed until the closing cycle_done says which policy the cycle ran under.
    pend_accepted: dict[str | None, int] = {}
    pend_elites: dict[str | None, int] = {}
    pend_cells: dict[str | None, set] = {}
    pend_processed: dict | None = None
    n_events = 0

    def flush(cycle_policy: str | None, closed: bool) -> None:
        nonlocal pend_accepted, pend_elites, pend_cells, pend_processed
        for letter, n in pend_accepted.items():
            _bucket(totals, _resolve(letter, cycle_policy))["accepted"] += n
        for letter, n in pend_elites.items():
            _bucket(totals, _resolve(letter, cycle_policy))["new_elites"] += n
        for letter, cells in pend_cells.items():
            _bucket(totals, _resolve(letter, cycle_policy))["cells"].update(cells)
        if closed:
            b = _bucket(totals, _resolve(None, cycle_policy))
            b["cycles"] += 1
            if pend_processed is not None:
                for key in ("rejected", "skipped"):
                    try:
                        b[key] += int(pend_processed.get(key) or 0)
                    except (TypeError, ValueError):
                        pass
                try:
                    b["accepted_per_cycle"].append(int(pend_processed.get("accepted") or 0))
                except (TypeError, ValueError):
                    b["accepted_per_cycle"].append(0)
            else:
                b["accepted_per_cycle"].append(sum(pend_accepted.values()))
        pend_accepted = {}
        pend_elites = {}
        pend_cells = {}
        pend_processed = None

    try:
        fh = open(target, "r", encoding="utf-8")
    except OSError:
        return {"path": str(target), "events": 0, "policies": {}}
    with fh:
        for line in fh:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not isinstance(e, dict):
                continue
            n_events += 1
            kind = e.get("kind")
            if kind == "accepted":
                letter = _letter_policy(e.get("directive_id"))
                pend_accepted[letter] = pend_accepted.get(letter, 0) + 1
                if e.get("new_elite"):
                    pend_elites[letter] = pend_elites.get(letter, 0) + 1
                cell = _cell_of(e)
                if cell is not None and cell not in seen_cells:
                    seen_cells.add(cell)
                    pend_cells.setdefault(letter, set()).add(cell)
            elif kind == "candidates_processed":
                pend_processed = e
            elif kind == "cycle_done":
                pol = e.get("policy")
                flush(pol if isinstance(pol, str) and pol else None, True)
    # Whatever the last, unfinished cycle wrote: no cycle_done, so no cycle policy.
    flush(None, False)

    out: dict[str, dict] = {}
    for name in sorted(totals, key=lambda n: (n == UNLABELLED, n)):
        t = totals[name]
        proposed = t["accepted"] + t["rejected"] + t["skipped"]
        out[name] = {
            "cycles": t["cycles"],
            "accepted": t["accepted"],
            "rejected": t["rejected"],
            "skipped": t["skipped"],
            "new_elites": t["new_elites"],
            "new_cells": len(t["cells"]),
            "cells": tuple(sorted(t["cells"])),
            "median_accepted": _median(t["accepted_per_cycle"]),
            "skipped_fraction": (t["skipped"] / proposed) if proposed else 0.0,
        }
    return {"path": str(target), "events": n_events, "policies": out}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    path = args[0] if args else None
    out = scan_policies(path)
    print(f"events: {out['events']}  file: {out['path']}")
    print(f"{'policy':<14}{'cycles':>8}{'accepted/cycle':>16}{'skipped':>10}"
          f"{'new elites':>12}{'new cells':>11}")
    for name, row in out["policies"].items():
        print(f"{name:<14}{row['cycles']:>8}{row['median_accepted']:>16.1f}"
              f"{row['skipped_fraction']:>10.3f}{row['new_elites']:>12}{row['new_cells']:>11}")
    if not out["policies"]:
        print("(no events)")
    print("events.jsonl is host-local (rk-work/.gitignore); this table is not reproducible "
          "from the published record.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
