"""Second-pass metrics over the epoch-1 archive (T14). New module; nothing pinned is
modified, and every score already on disk stays exactly as it was.

Two questions the archive can answer today, out of columns nothing has read yet:

* work-precision. EPOCH2-DESIGN changes the scored metric from fixed-budget error
  to the cycles a method spends reaching a target tolerance. For a fixed-step
  method that metric needs no controller: run ``simulate.problem_error`` at a
  power-of-two ladder of step counts, take the smallest ``n`` whose error is at or
  under the target, and report ``n * costmodel.cycle_count``. So "does the metric
  change the ranking" is answerable now, on epoch-1 methods with epoch-1
  arithmetic, and the answer is a rank correlation per (problem, target).
* the stability frontier. Every record carries ``stability_real`` from
  ``evaluator.stability_extents``. Per (order, stages) cell, the Pareto set over
  (stability_real, heldout_error) is a curve whose width the literature predicts:
  order p fixes the stability polynomial through degree p, so a p-stage order-p
  cell has no free coefficient and should collapse, while p+1 and p+2 stages
  should spread.

Read only. The module opens ``rk-work/archive/*.jsonl``, imports the pinned
evaluator machinery, and writes ``<RK_WORK_DIR>/secondpass/results.json`` in the
shape ``key_findings.json`` uses: one top-level key per finding, each carrying
``verdict``, ``numbers`` and ``series``, plus a ``_meta`` provenance block.

Two properties this module holds onto:

* Determinism. The output is a pure function of the archive plus this file. No
  wall clock is read anywhere, nothing is sampled, iteration is sorted, and the
  only budgets are probe counts and ``N_MAX``. A wall-clock cutoff would have been
  the cheap way to bound the bisection, and it is exactly the thing that would
  make two runs over the same archive disagree.
* Bounded memory. ``scan_archive`` streams the dated files and keeps only the
  grids, the Pareto fronts and a handful of counters, so a run never holds the
  hundred thousand live ``Record`` objects a full ``archive.read_all`` would. It
  is a streaming restatement of ``archive._grids_from`` plus ``archive._better``,
  and ``test_scan_matches_archive_replay_on_a_synthetic_archive`` pins the two
  together the way ``test_A80`` pins ``archive.fold`` to ``archive.replay``.

The honest claim about cycles-to-tolerance is "smallest n on the sampled set".
Q15 error is not monotone in the step count once quantization dominates (euler on
rc_thermal gets worse as the steps get smaller), so a bisection can land on a
non-minimal n. Every ladder carries ``ladder_monotone`` and every number derived
from it carries the flag forward.

The ladder itself is not specific to a fixed-step Q15 run and does not live inside
``cycles_to_tolerance`` any more. ``ladder_scan`` takes a probe, so an adaptive run
controlled by a tolerance in LSB and an implicit run controlled by a step count read
on the same axis as an explicit one, with the same rung statuses, the same target
statuses and the same ``ladder_monotone`` honesty flag. ``cycles_to_tolerance`` is
the fixed-step Q15 probe plugged into it and returns exactly the bytes it always did.

The artifact is not a legal source for a public-page number. The traceability rule
lists key_findings.json, validation/results.json, benchmark/results.json and the
side-track ledger; extending that list is a separate decision.
"""
from __future__ import annotations

import argparse
import functools
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from rk_harness.archive import (
    RecordSchemaError,
    cycle_bucket,
    record_from_json,
    record_order,
)
from rk_harness.costmodel import M0PLUS_FAST, cycle_count
from rk_harness.fixedpoint import Q15OverflowError
from rk_harness.orderconditions import achieved_order_symbolic
from rk_harness.paths import archive_dir, work_dir
from rk_harness.problems import HELDOUT_SET, PROBLEMS, SEARCH_SET
from rk_harness.simulate import problem_error
from rk_harness.tableau import classical, content_hash, stages, to_json
from rk_harness.types import Problem, Record, Tableau
from rk_harness.verifier import STABILITY_THRESHOLD
from rk_harness.verifier_hash import compute_verifier_hash

TARGETS: tuple[float, ...] = (2.0 ** -6, 2.0 ** -8, 2.0 ** -10, 2.0 ** -12)
N_MAX = 16384
BISECT_PROBES = 12
PARETO_CAP = 2000
BUDGET_CYCLES = 65536
COST_MODEL = M0PLUS_FAST

PROBLEM_SET: tuple[Problem, ...] = SEARCH_SET + HELDOUT_SET
PROBLEM_NAMES: tuple[str, ...] = tuple(p.name for p in PROBLEM_SET)
METHOD_SELECTIONS: tuple[str, ...] = ("elites", "anchors", "all")

# Rung outcomes. "error" is its own status rather than being folded into
# "nonfinite": an exception that is not a Q15 overflow says the probe never
# produced a number at all, which is a different fact about the method.
RUNG_STATUSES: tuple[str, ...] = ("ok", "overflow", "nonfinite", "error")
TARGET_STATUSES: tuple[str, ...] = ("reached", "never_reached", "overflow_before_target")

_BANNED = ("novel", "first", "beats", "outperforms", "breakthrough", "proves",
           "state-of-the-art", "best-ever")
_EM_DASH = chr(0x2014)          # by code point, so this file stays pure ASCII

# Where the UNSTABLE rejection lives. Hardcoding a line number is safe here and
# only here: verifier.py is one of the ten pinned files, so it cannot move without
# an epoch boundary.
_UNSTABLE_CITE = "verifier.py:123-125"


def _warn(msg: str) -> None:
    print(f"[secondpass] warning: {msg}", file=sys.stderr)


class Floats:
    """Finite floats pass through; non-finite becomes None, counted.

    An instance per build rather than a module global, because two builds in one
    process would otherwise share a counter and the second document would differ
    from the byte-identical earlier one.
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


# --------------------------------------------------------------------------- scan

@dataclass
class ScanResult:
    """What one streaming pass over the archive keeps.

    Everything here is bounded: the grids hold one Record per occupied cell, the
    fronts hold at most PARETO_CAP per cell, and the rest are counters.
    """
    n_records: int = 0
    last_cycle_id: int = 0
    dates: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    grids: dict[int, dict[tuple[int, int], Record]] = field(default_factory=dict)
    anchors: dict[str, Record] = field(default_factory=dict)
    pareto: dict[tuple[int, int], list[Record]] = field(default_factory=dict)
    pareto_discarded: dict[tuple[int, int], int] = field(default_factory=dict)
    cell_counts: dict[tuple[int, int], int] = field(default_factory=dict)
    discovered_counts: dict[tuple[int, int], int] = field(default_factory=dict)
    discarded_lines: int = 0
    nonfinite_points: int = 0


@functools.lru_cache(maxsize=1)
def classical_by_hash() -> dict[str, str]:
    """tableau content hash -> fixture name, for the 8 classical anchors."""
    return {content_hash(t): name for name, t in classical().items()}


def _archive_files(paths=None) -> list[Path]:
    if paths is not None:
        return sorted((Path(p) for p in paths), key=lambda p: p.name)
    d = archive_dir()
    if not d.is_dir():
        return []
    return sorted((p for p in d.iterdir() if p.is_file() and p.name.endswith(".jsonl")),
                  key=lambda p: p.name)


def _point(r: Record) -> tuple[float, float]:
    return (r.score.stability_real, r.score.heldout_error)


def pareto_insert(front: list[Record], r: Record, cap: int = PARETO_CAP) -> int:
    """Fold `r` into a non-dominated list over (stability_real, heldout_error), both
    minimized. Returns 1 if `r` was dropped because the front was at `cap`, else 0.

    A weakly dominating member keeps `r` out, so an exact tie keeps the earlier
    record, which is the incumbent rule the grids use.
    """
    px, py = _point(r)
    for q in front:
        qx, qy = _point(q)
        if qx <= px and qy <= py:
            return 0
    kept = [q for q in front if not (px <= _point(q)[0] and py <= _point(q)[1])]
    if len(kept) >= cap:
        front[:] = kept
        return 1
    kept.append(r)
    front[:] = kept
    return 0


def _better(cand: Record, inc: Record) -> bool:
    """archive._better: strictly lower heldout_error wins, ties keep the earlier record."""
    return cand.score.heldout_error < inc.score.heldout_error


def scan_archive(paths=None) -> ScanResult:
    """One streaming pass over the dated archive files, in sorted name order.

    Applies archive.read_all's discard rule (a partial trailing line is dropped
    silently, anything else warns) and archive._grids_from's incumbent rule, but
    holds no list of records: 104k live Record objects out of 246 MB of JSONL is
    the thing this module exists to avoid.
    """
    cap = PARETO_CAP
    by_hash = classical_by_hash()
    out = ScanResult(grids={1: {}, 2: {}, 3: {}, 4: {}})
    dates: set[str] = set()
    files: list[str] = []
    for path in _archive_files(paths):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            _warn(f"cannot read {path.name}: {e!r}")
            continue
        files.append(path.name)
        lines = text.split("\n")
        # A file ending in "\n" yields a trailing empty element; drop it so the
        # "last line" is the last real line.
        if lines and lines[-1] == "":
            lines.pop()
        last = len(lines) - 1
        for i, raw in enumerate(lines):
            line = raw.strip()
            if not line:
                continue
            try:
                r = record_from_json(json.loads(line))
            except (ValueError, RecordSchemaError) as e:
                out.discarded_lines += 1
                if i != last:
                    _warn(f"{path.name}:{i + 1}: discarded unparsable line "
                          f"({e.__class__.__name__})")
                elif isinstance(e, RecordSchemaError):
                    _warn(f"{path.name}:{i + 1}: discarded invalid record ({e})")
                # a partial trailing line after a crash is discarded silently (R2)
                continue

            out.n_records += 1
            if r.cycle_id > out.last_cycle_id:
                out.last_cycle_id = r.cycle_id
            dates.add(r.timestamp[:10])

            order = record_order(r)
            s = stages(r.tableau)
            fast = r.score.cycles.get("m0plus_fast")
            key = (s, cycle_bucket(0 if fast is None else int(fast)))
            g = out.grids.get(order)
            if g is not None:
                inc = g.get(key)
                if inc is None or _better(r, inc):
                    g[key] = r

            cell = (order, s)
            out.cell_counts[cell] = out.cell_counts.get(cell, 0) + 1
            name = by_hash.get(r.tableau_hash)
            if name is None:
                out.discovered_counts[cell] = out.discovered_counts.get(cell, 0) + 1
            else:
                out.anchors.setdefault(name, r)

            px, py = _point(r)
            if math.isfinite(px) and math.isfinite(py):
                if pareto_insert(out.pareto.setdefault(cell, []), r, cap):
                    out.pareto_discarded[cell] = out.pareto_discarded.get(cell, 0) + 1
            else:
                out.nonfinite_points += 1
    out.dates = tuple(sorted(dates))
    out.files = tuple(files)
    return out


# --------------------------------------------------------------------------- statistics

def _average_ranks(values: list[float]) -> list[float]:
    """Ranks starting at 1, ties sharing the average of the positions they span."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _spearman(xs, ys) -> float:
    """Rank correlation, average ranks for ties.

    Hand-rolled rather than taken from scipy so the tie rule belongs to this module
    and a test can pin it. Returns nan when there are fewer than two points or when
    either side has no spread, which the caller writes out as null.
    """
    xs = [float(v) for v in xs]
    ys = [float(v) for v in ys]
    if len(xs) != len(ys):
        raise ValueError("spearman: inputs differ in length")
    n = len(xs)
    if n < 2:
        return float("nan")
    rx = _average_ranks(xs)
    ry = _average_ranks(ys)
    mx = sum(rx) / n
    my = sum(ry) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx <= 0.0 or syy <= 0.0:
        return float("nan")
    return sxy / math.sqrt(sxx * syy)


def _ordinal_ranks(keyed: list[tuple[float, str]]) -> dict[str, int]:
    """name -> 1-based rank by value, ties broken by name so the order is total."""
    order = sorted(keyed, key=lambda kv: (kv[0], kv[1]))
    return {name: i + 1 for i, (_, name) in enumerate(order)}


def _series(rows: list, reason: str):
    """A chart-ready array, or the reason there is nothing to chart. An empty array
    is the one thing the schema will not take, because it reads as a missing value
    rather than as an absent one."""
    return rows if rows else {"absent": reason}


def _plural(count: int, noun: str) -> str:
    """"1 problem" / "7 problems". The verdicts are read by people."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _median(vals: list[float]) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else 0.5 * (s[mid - 1] + s[mid])


# --------------------------------------------------------------------------- ladder

def _probe(t: Tableau, problem: Problem, n: int,
           cache: dict[int, tuple[str, float | None, int | None]]):
    hit = cache.get(n)
    if hit is not None:
        return hit
    try:
        err, max_q = problem_error(t, problem, n)
        row = ("ok", float(err), int(max_q)) if math.isfinite(err) \
            else ("nonfinite", None, int(max_q))
    except Q15OverflowError:
        row = ("overflow", None, None)
    except Exception:                       # a broken tableau must not stop the sweep
        row = ("error", None, None)
    cache[n] = row
    return row


def _target_key(target: float) -> str:
    """A round-tripping label for a target, so the JSON keys are exact."""
    return repr(float(target))


def _normalize_targets(target_or_targets) -> list[float]:
    """One target or many, de-duplicated with the caller's order kept.

    The order is kept rather than sorted because the target keys are written into
    the document in the order they are asked for, and sorting here would quietly
    reorder an existing artifact.
    """
    if isinstance(target_or_targets, (int, float)):
        raw = [float(target_or_targets)]
    else:
        raw = [float(v) for v in target_or_targets]
    targets: list[float] = []
    for v in raw:
        if v not in targets:
            targets.append(v)
    return targets


def ladder_scan(probe, target_or_targets, *, unit_ladder=None,
                n_max: int = N_MAX, bisect_probes: int = BISECT_PROBES,
                per_unit_cost: int | None = None, bisect: bool = True) -> dict:
    """The shared work-precision ladder, over any probe that returns a rung status.

    This is the body ``cycles_to_tolerance`` used to carry inline, lifted out so a
    caller that is not a fixed-step Q15 run can stand on the same axis: an adaptive
    run controlled by a tolerance in LSB, or an implicit one controlled by a step
    count, both read the same way as an explicit one.

    ``probe(unit) -> (status, error, max_abs_q)`` with ``status`` in
    ``RUNG_STATUSES``, ``error`` a float or None, ``max_abs_q`` an int or None. The
    probe owns its own cache: this function calls it once per rung and once per
    bisection step, and counts the bisection steps it asked for rather than the
    ones that reached the integrator, which is what ``probes`` has always meant.
    Counters a probe wants to keep beside a rung (accepted and rejected steps, for
    instance) belong in that cache, keyed by the same control value.

    ``unit_ladder`` replaces the power-of-two ladder with an explicit set of
    control values IN ASCENDING COST ORDER, the cheapest rung leading. That order is
    the whole contract: the answer is the earliest rung that meets the target, so
    on a step-count ladder it reads as the smallest n and on a tolerance ladder as
    the coarsest tolerance. Pass ``bisect=False`` with it when there is nothing
    between adjacent rungs, as with a tolerance in whole LSB; then ``probes`` comes
    back 0 because none were taken, rather than as a fiction.

    ``per_unit_cost`` turns a rung into a cycle count. Leave it None when cycles
    are not the control value times a constant (an adaptive run pays per attempt,
    and the attempt count is not the tolerance), and every ``cycles`` comes back
    None for the caller to fill in from its own counters.
    """
    targets = _normalize_targets(target_or_targets)

    if unit_ladder is None:
        units: list = []
        n = 1
        while n <= n_max:
            units.append(n)
            n *= 2
        ladder_max: int | None = n_max
    else:
        units = list(unit_ladder)
        if not units:
            raise ValueError("ladder_scan: unit_ladder must name at least one rung")
        # n_max did not bound this ladder, so reporting it would misdescribe the run.
        ladder_max = None
    if bisect:
        # Bisection walks the integers between two rungs, so it is only defined on
        # an ascending integer ladder. A ladder with nothing between its rungs is
        # exactly what bisect=False is for.
        if any(not isinstance(u, int) or isinstance(u, bool) for u in units):
            raise ValueError("ladder_scan: bisect=True needs an integer ladder")
        if any(a >= b for a, b in zip(units, units[1:])):
            raise ValueError("ladder_scan: bisect=True needs a strictly ascending ladder")

    ladder: list[dict] = []
    for u in units:
        status, err, max_q = probe(u)
        ladder.append({"n": u, "status": status, "error": err, "max_abs_q": max_q})

    ok_errors = [row["error"] for row in ladder if row["status"] == "ok"]
    monotone = all(a >= b for a, b in zip(ok_errors, ok_errors[1:]))
    any_ok = bool(ok_errors)
    any_overflow = any(row["status"] == "overflow" for row in ladder)

    # No clean rung anywhere means the method never ran, which is a different fact
    # from running and staying above the target.
    missed = "never_reached" if any_ok or not any_overflow else "overflow_before_target"

    out_targets: dict[str, dict] = {}
    for target in targets:
        hit_at = None
        for idx, row in enumerate(ladder):
            if row["status"] == "ok" and row["error"] <= target:
                hit_at = idx
                break
        entry = {
            "target": target,
            "n": None,
            "cycles": None,
            "status": missed,
            "probes": 0,
            "probed": [],
            "ladder_monotone": monotone,
        }
        if hit_at is not None:
            best = ladder[hit_at]["n"]
            # The rung below the hit, or 0 when the cheapest rung already met the
            # target. On the power-of-two ladder that is exactly best // 2, which
            # is the bracket cycles_to_tolerance has always bisected inside.
            lo = ladder[hit_at - 1]["n"] if hit_at > 0 else 0
            probed: list[dict] = []
            while bisect and best - lo > 1 and len(probed) < bisect_probes:
                mid = (lo + best) // 2
                status, err, _ = probe(mid)
                probed.append({"n": mid, "status": status, "error": err})
                if status == "ok" and err <= target:
                    best = mid
                else:
                    lo = mid
            entry.update({
                "n": best,
                "cycles": None if per_unit_cost is None else best * per_unit_cost,
                "status": "reached",
                "probes": len(probed),
                "probed": sorted(probed, key=lambda p: p["n"]),
            })
        out_targets[_target_key(target)] = entry

    return {
        "n_max": ladder_max,
        "bisect_probes": bisect_probes,
        "bisect": bool(bisect),
        "per_unit_cost": per_unit_cost,
        "ladder_monotone": monotone,
        "ladder": ladder,
        "targets": out_targets,
    }


def cycles_to_tolerance(t: Tableau, problem: Problem, target_or_targets,
                        n_max: int = N_MAX,
                        bisect_probes: int = BISECT_PROBES) -> dict:
    """Cycles this tableau spends bringing `problem` to each target tolerance.

    One power-of-two ladder n = 1, 2, 4, ... <= n_max is shared by every target,
    then each target bisects inside the octave that brackets its own hit. Every
    rung is kept with its status: a method that overflows at some step counts is a
    Q15 result, not a gap in the table.

    The step counts near the bottom of the ladder overflow on every frozen problem
    (t_end is at least 4, so h >= 1 and h_q is unrepresentable). That is expected
    and does not mark the method as an overflow case; ``overflow_before_target``
    means no rung at all ran cleanly.

    The result is the smallest n on the sampled set, not the smallest n. Q15 error
    can rise with n once quantization dominates, and ``ladder_monotone`` says
    whether it did on the rungs that ran.

    The ladder itself now lives in ``ladder_scan``; what is left here is the
    fixed-step Q15 probe plugged into it. The keys and the values are what they
    have always been, so a document built before the split and one built after it
    are the same bytes.
    """
    cache: dict[int, tuple[str, float | None, int | None]] = {}
    per_step = cycle_count(t, COST_MODEL, problem.n_states)
    scan = ladder_scan(lambda n: _probe(t, problem, n, cache), target_or_targets,
                       n_max=n_max, bisect_probes=bisect_probes,
                       per_unit_cost=per_step)
    return {
        "problem": problem.name,
        "n_max": n_max,
        "bisect_probes": bisect_probes,
        "cycles_per_step": per_step,
        "ladder_monotone": scan["ladder_monotone"],
        "ladder": scan["ladder"],
        "targets": scan["targets"],
    }


# --------------------------------------------------------------------------- methods

def tableau_order(t: Tableau) -> int:
    """The archive's order descriptor for a tableau with no record behind it."""
    return min(achieved_order_symbolic(t, max_order=5), 4)


def select_methods(scan: ScanResult, kind: str = "all") -> list[dict]:
    """The methods a run evaluates, sorted by tableau hash.

    `anchors` is the 8 fixture tableaus, `elites` is every occupied cell of the
    grids, `all` is both with the duplicates folded: an anchor that also holds a
    cell keeps its fixture name and gains the elite role.
    """
    if kind not in METHOD_SELECTIONS:
        raise ValueError(f"unknown method selection {kind!r}, want one of {METHOD_SELECTIONS}")
    entries: dict[str, dict] = {}

    def _add(h: str, name_or_hash: str, mkind: str, t: Tableau, role: str,
             rec: Record | None) -> None:
        e = entries.get(h)
        if e is None:
            e = {
                "name_or_hash": name_or_hash,
                "tableau_hash": h,
                "kind": mkind,
                "roles": [],
                "order": None,
                "stages": stages(t),
                "heldout_error": None,
                "search_error": None,
                "per_problem": {},
                "cycle_id": None,
                "tier": None,
                "tableau": to_json(t),
                "_tableau": t,
            }
            entries[h] = e
        if role not in e["roles"]:
            e["roles"].append(role)
        if rec is not None and e["cycle_id"] is None:
            e["order"] = record_order(rec)
            e["heldout_error"] = rec.score.heldout_error
            e["search_error"] = rec.score.search_error
            e["per_problem"] = {n: rec.score.per_problem.get(n) for n in PROBLEM_NAMES}
            e["cycle_id"] = rec.cycle_id
            e["tier"] = rec.tier

    if kind in ("anchors", "all"):
        for name, t in sorted(classical().items()):
            _add(content_hash(t), name, "classical", t, "anchor", scan.anchors.get(name))
    if kind in ("elites", "all"):
        for order in sorted(scan.grids):
            for cell in sorted(scan.grids[order]):
                rec = scan.grids[order][cell]
                nm = classical_by_hash().get(rec.tableau_hash)
                _add(rec.tableau_hash, nm or rec.tableau_hash,
                     "classical" if nm else "discovered", rec.tableau,
                     f"elite_{order}_{cell[0]}_{cell[1]}", rec)

    out = sorted(entries.values(), key=lambda e: e["tableau_hash"])
    for e in out:
        e["roles"] = sorted(e["roles"])
        if e["order"] is None:
            e["order"] = tableau_order(e["_tableau"])
    return out


# --------------------------------------------------------------------------- finding a

def work_precision(scan: ScanResult, selection: dict, floats: Floats | None = None,
                   targets: tuple[float, ...] = TARGETS, n_max: int = N_MAX,
                   bisect_probes: int = BISECT_PROBES) -> dict:
    """Does cycles-to-tolerance rank methods the way fixed-budget error does.

    Per (problem, target): the Spearman correlation between the archived
    fixed-budget error and the cycles a method spends reaching the target, the
    methods whose rank moves most between the two readings, and the counts of
    methods that never reach the target or never ran cleanly at all.
    """
    fl = floats or Floats()
    methods = select_methods(scan, selection["methods"])
    wanted = set(selection["problems"])
    problems = [PROBLEMS[n] for n in PROBLEM_NAMES if n in wanted]
    by_hash = {m["tableau_hash"]: m for m in methods}

    runs: dict[tuple[str, str], dict] = {}
    for m in methods:
        for p in problems:
            runs[(m["tableau_hash"], p.name)] = cycles_to_tolerance(
                m["_tableau"], p, targets, n_max=n_max, bisect_probes=bisect_probes)

    method_rows = [{
        "method": m["name_or_hash"],
        "tableau_hash": m["tableau_hash"],
        "kind": m["kind"],
        "roles": m["roles"],
        "order": m["order"],
        "stages": m["stages"],
        "heldout_error": fl(m["heldout_error"]),
        "search_error": fl(m["search_error"]),
        "cycle_id": m["cycle_id"],
        "tier": m["tier"],
        "tableau": m["tableau"],
    } for m in methods]

    run_rows: list[dict] = []
    cell_rows: list[dict] = []
    spearmans: list[float] = []
    movements: list[int] = []
    total_never = 0
    total_overflow = 0
    nonmonotone = sum(1 for m in methods for p in problems
                      if not runs[(m["tableau_hash"], p.name)]["ladder_monotone"])

    for p in problems:
        for target in targets:
            tkey = _target_key(target)
            reached: list[tuple[str, int]] = []       # (tableau hash, cycles)
            never = 0
            overflowed = 0
            nonmono_here = 0
            for m in methods:
                run = runs[(m["tableau_hash"], p.name)]
                entry = run["targets"][tkey]
                if not run["ladder_monotone"]:
                    nonmono_here += 1
                run_rows.append({
                    "method": m["name_or_hash"],
                    "tableau_hash": m["tableau_hash"],
                    "problem": p.name,
                    "target": target,
                    "n": entry["n"],
                    "cycles": entry["cycles"],
                    "status": entry["status"],
                    "probes": entry["probes"],
                    "cycles_per_step": run["cycles_per_step"],
                    "ladder_monotone": run["ladder_monotone"],
                })
                if entry["status"] == "reached":
                    reached.append((m["tableau_hash"], entry["cycles"]))
                elif entry["status"] == "overflow_before_target":
                    overflowed += 1
                else:
                    never += 1
            total_never += never
            total_overflow += overflowed

            def _finite(h: str, key: str) -> bool:
                v = by_hash[h]["heldout_error"] if key == "heldout" \
                    else by_hash[h]["per_problem"].get(p.name)
                return v is not None and math.isfinite(v)

            paired_held = [(h, c) for h, c in reached if _finite(h, "heldout")]
            paired_same = [(h, c) for h, c in reached if _finite(h, "same")]
            rho_held = _spearman([by_hash[h]["heldout_error"] for h, _ in paired_held],
                                 [float(c) for _, c in paired_held])
            rho_same = _spearman([by_hash[h]["per_problem"][p.name] for h, _ in paired_same],
                                 [float(c) for _, c in paired_same])
            if math.isfinite(rho_held):
                spearmans.append(rho_held)

            rank_fixed = _ordinal_ranks([(by_hash[h]["heldout_error"], h)
                                         for h, _ in paired_held])
            rank_cycles = _ordinal_ranks([(float(c), h) for h, c in paired_held])
            movers = sorted(
                ({"method": by_hash[h]["name_or_hash"],
                  "tableau_hash": h,
                  "rank_fixed_budget": rank_fixed[h],
                  "rank_cycles_to_tolerance": rank_cycles[h],
                  "movement": abs(rank_fixed[h] - rank_cycles[h])}
                 for h, _ in paired_held),
                key=lambda row: (-row["movement"], row["tableau_hash"]))
            movements.extend(row["movement"] for row in movers)

            cell_rows.append({
                "problem": p.name,
                "target": target,
                "methods_evaluated": len(methods),
                "methods_ranked": len(paired_held),
                "spearman_heldout_vs_cycles": fl(rho_held),
                "spearman_same_problem_vs_cycles": fl(rho_same),
                "never_reached": never,
                "overflow_before_target": overflowed,
                "nonmonotone_ladders": nonmono_here,
                "max_rank_movement": max((row["movement"] for row in movers), default=0),
                "top_movers": movers[:5],
            })

    med = _median(spearmans)
    n_cells = len(cell_rows)
    n_ladders = len(methods) * len(problems)
    n_readings = n_ladders * len(targets)
    verdict = (
        f"Over {_plural(len(methods), 'method')} and "
        f"{_plural(len(problems), 'problem')} at "
        f"{_plural(len(targets), 'target')}, the median Spearman correlation "
        f"between the archived fixed-budget held-out error and "
        f"cycles-to-tolerance is "
        f"{'unavailable' if med is None else format(med, '.3f')}, taken over "
        f"{len(spearmans)} of {n_cells} (problem, target) cells where two or more "
        f"methods reached the target. {total_never} of {n_readings} "
        f"(method, problem, target) readings never reached the target at any "
        f"sampled step count and {total_overflow} came from a method that never "
        f"ran cleanly at any step count; the fixed-budget error exists for every "
        f"archived record, so those readings drop out on the cycles side alone "
        f"and the correlation is taken over what is left. Every n here is the "
        f"smallest on the sampled ladder, and {nonmonotone} of {n_ladders} "
        f"(method, problem) ladders had error rise with the step count, where a "
        f"smaller n outside the sample may exist."
    )

    return {
        "verdict": verdict,
        "numbers": {
            "methods_evaluated": len(methods),
            "anchors_evaluated": sum(1 for m in methods if m["kind"] == "classical"),
            "discovered_evaluated": sum(1 for m in methods if m["kind"] == "discovered"),
            "problems": len(problems),
            "targets": len(targets),
            "cells": n_cells,
            "cells_with_correlation": len(spearmans),
            "median_spearman_heldout_vs_cycles": fl(med),
            "min_spearman_heldout_vs_cycles": fl(min(spearmans) if spearmans else None),
            "max_spearman_heldout_vs_cycles": fl(max(spearmans) if spearmans else None),
            "readings": n_readings,
            "readings_never_reached": total_never,
            "readings_overflow_before_target": total_overflow,
            "ladders_evaluated": n_ladders,
            "nonmonotone_ladders": nonmonotone,
            "max_rank_movement": max(movements) if movements else 0,
        },
        "series": {
            "methods": _series(method_rows, "the selection matched no method"),
            "runs": _series(run_rows, "no (method, problem) pair was evaluated"),
            "cells": _series(cell_rows, "no (problem, target) cell was evaluated"),
            "chart_hint": ("cells: spearman_heldout_vs_cycles against target, one "
                           "line per problem; runs: cycles against target, one line "
                           "per method"),
        },
    }


# --------------------------------------------------------------------------- finding b

def stability_frontier(scan: ScanResult, floats: Floats | None = None) -> dict:
    """The Pareto set over (stability_real, heldout_error) per (order, stages) cell.

    Cells with stages below the order cannot exist for an explicit method, so the
    sweep is every cell with stages at or above the order. The stages == order
    cells are the ones the literature rule predicts collapse for: order p fixes the
    stability polynomial through degree p, so a p-stage order-p method has no free
    coefficient left. Reporting discovered_in_cell next to records_in_cell is what
    keeps a front of size one honest, since an unsearched cell looks the same as a
    collapsed one.
    """
    fl = floats or Floats()
    by_hash = classical_by_hash()
    rows: list[dict] = []
    for cell in sorted(scan.pareto):
        order, s = cell
        if s < order:
            continue
        front = sorted(scan.pareto[cell],
                       key=lambda r: (r.score.stability_real, r.score.heldout_error,
                                      r.tableau_hash))
        stab = [r.score.stability_real for r in front]
        err = [r.score.heldout_error for r in front]
        records_in_cell = scan.cell_counts.get(cell, 0)
        discovered = scan.discovered_counts.get(cell, 0)
        rows.append({
            "order": order,
            "stages": s,
            "front_size": len(front),
            "records_in_cell": records_in_cell,
            "discovered_in_cell": discovered,
            "classical_in_cell": records_in_cell - discovered,
            "collapse_predicted": s == order,
            "stability_real_span": fl(max(stab) - min(stab)) if stab else None,
            "heldout_error_span": fl(max(err) - min(err)) if err else None,
            "pareto_discarded": scan.pareto_discarded.get(cell, 0),
            "members": [{
                "method": by_hash.get(r.tableau_hash, r.tableau_hash),
                "tableau_hash": r.tableau_hash,
                "kind": "classical" if r.tableau_hash in by_hash else "discovered",
                "stability_real": fl(r.score.stability_real),
                "heldout_error": fl(r.score.heldout_error),
                "cycle_id": r.cycle_id,
                "tier": r.tier,
            } for r in front],
        })

    collapse_cells = [r for r in rows if r["collapse_predicted"]]
    spread_cells = [r for r in rows if not r["collapse_predicted"]]
    med_collapse = _median([float(r["front_size"]) for r in collapse_cells])
    med_spread = _median([float(r["front_size"]) for r in spread_cells])
    unsearched = [f"({r['order']},{r['stages']})" for r in collapse_cells
                  if r["discovered_in_cell"] == 0]

    # The shape sentence has to read the same whichever way the numbers fall, so it
    # is derived from them rather than asserted.
    equal_txt = ("no cell where stages equals the order" if med_collapse is None
                 else f"a median front of {format(med_collapse, '.1f')} members "
                      f"where stages equals the order")
    above_txt = ("no cell where stages exceeds the order" if med_spread is None
                 else f"a median front of {format(med_spread, '.1f')} members "
                      f"where stages exceeds it")
    if med_collapse is None or med_spread is None:
        shape = ("With only one side of that comparison present, the front sizes "
                 "say nothing yet about the predicted collapse.")
    elif med_spread > med_collapse:
        shape = ("The wider cells carry the wider front, which is the direction "
                 "the stability-polynomial argument predicts.")
    elif med_spread == med_collapse:
        shape = ("The two sides are equal, which leaves the stability-polynomial "
                 "argument neither supported nor contradicted here.")
    else:
        shape = ("The wider cells carry the narrower front, which runs against "
                 "the stability-polynomial argument.")

    verdict = (
        f"Across {_plural(len(rows), '(order, stages) cell')} with stages at or "
        f"above the order, the Pareto set over (stability_real, heldout_error) "
        f"gives {equal_txt} and {above_txt}. {shape} "
        f"Two censorings apply and "
        f"neither is a footnote: verifier.py rejects any candidate whose "
        f"stability_real is above STABILITY_THRESHOLD = {STABILITY_THRESHOLD} "
        f"({_UNSTABLE_CITE}), so weak-stability records were never archived, "
        f"and what remains is a frontier of what the search looked at rather than "
        f"of what exists. "
        + (f"{len(unsearched)} of the collapse cells "
           f"({', '.join(unsearched)}) hold no discovered method at all, so a "
           f"front of one there is equally consistent with the predicted collapse "
           f"and with an unsearched cell."
           if unsearched else
           "Every collapse cell holds at least one discovered method, so the "
           "front sizes there say something about what the search covered.")
    )

    return {
        "verdict": verdict,
        "numbers": {
            "cells": len(rows),
            "collapse_cells": len(collapse_cells),
            "spread_cells": len(spread_cells),
            "median_front_size_stages_equal_order": fl(med_collapse),
            "median_front_size_stages_above_order": fl(med_spread),
            "largest_front_size": max((r["front_size"] for r in rows), default=0),
            "collapse_cells_without_discovered_records": len(unsearched),
            "stability_threshold": float(STABILITY_THRESHOLD),
            "records_scanned": scan.n_records,
            "nonfinite_points_skipped": scan.nonfinite_points,
            "pareto_discarded_total": sum(scan.pareto_discarded.values()),
        },
        "series": {
            "cells": _series(rows, "no archived record carried a finite "
                                   "(stability_real, heldout_error) pair"),
            "chart_hint": ("cells[].members: heldout_error against stability_real, "
                           "one panel per (order, stages)"),
        },
    }


# --------------------------------------------------------------------------- document

_SCHEMA_DOC = (
    "one top-level key per finding; each finding carries 'verdict' (an honest "
    "statement of the result), 'numbers' (scalar facts) and 'series' (chart-ready "
    "arrays, each non-empty or replaced by {'absent': reason}); non-finite floats "
    "are written as null and counted in _meta.nonfinite_written_as_null"
)


def default_selection() -> dict:
    return {"methods": "all", "problems": list(PROBLEM_NAMES)}


def normalize_selection(selection: dict | None) -> dict:
    """The selection as it is stamped into _meta, so a partial run cannot be read
    as a full one."""
    sel = default_selection() if selection is None else dict(selection)
    kind = sel.get("methods", "all")
    if kind not in METHOD_SELECTIONS:
        raise ValueError(f"unknown method selection {kind!r}, want one of {METHOD_SELECTIONS}")
    names = list(sel.get("problems") or PROBLEM_NAMES)
    unknown = sorted(set(names) - set(PROBLEM_NAMES))
    if unknown:
        raise ValueError(f"unknown problems {unknown}, want a subset of {list(PROBLEM_NAMES)}")
    kept = [n for n in PROBLEM_NAMES if n in set(names)]
    if not kept:
        raise ValueError("selection.problems must name at least one problem")
    return {"methods": kind, "problems": kept}


def build_results(scan: ScanResult | None = None, selection: dict | None = None,
                  targets: tuple[float, ...] = TARGETS, n_max: int = N_MAX,
                  bisect_probes: int = BISECT_PROBES) -> dict:
    """The full results document. Pure function of the archive plus this module;
    no wall clock. Pass scan/selection explicitly for testing."""
    if scan is None:
        scan = scan_archive()
    sel = normalize_selection(selection)
    fl = Floats()
    doc = {
        "_meta": {
            "script": "rk_harness/secondpass.py",
            "sources": ["rk-work/archive/*.jsonl (streamed, not via archive.read_all)"],
            "budget_cycles": BUDGET_CYCLES,
            "cost_model": COST_MODEL.name,
            "rounding": "floor (ASRS), per HANDOFF 4.2",
            "verifier_hash": compute_verifier_hash(),
            "archive_records": scan.n_records,
            "archive_last_cycle_id": scan.last_cycle_id,
            "archive_dates": list(scan.dates),
            "archive_files": list(scan.files),
            "archive_lines_discarded": scan.discarded_lines,
            "targets": [float(v) for v in targets],
            "n_max": n_max,
            "bisect_probes": bisect_probes,
            "pareto_cap": PARETO_CAP,
            "selection": sel,
            "schema": _SCHEMA_DOC,
            "not_a_public_page_source": (
                "the traceability rule lists key_findings.json, "
                "validation/results.json, benchmark/results.json and the "
                "side-track ledger; this artifact is not on that list"),
        },
        "work_precision": work_precision(scan, sel, floats=fl, targets=targets,
                                         n_max=n_max, bisect_probes=bisect_probes),
        "stability_frontier": stability_frontier(scan, floats=fl),
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
    """The verdicts are the part of this document a person would paste somewhere,
    so they keep the findings-site word rule even though the artifact is not a
    page source."""
    low = str(text).lower()
    for word in _BANNED:
        if re.search(rf"(?<![a-z0-9-]){re.escape(word)}(?![a-z0-9-])", low):
            fail(f"{path} carries the banned word {word!r}")
    if _EM_DASH in str(text):
        fail(f"{path} carries an em dash")


def validate_results(doc: dict) -> None:
    """Raise ValueError if the document violates the schema described above."""
    def fail(msg: str):
        raise ValueError(f"results schema: {msg}")

    if "_meta" not in doc:
        fail("missing top-level key '_meta'")
    meta = doc["_meta"]
    for key in ("archive_records", "archive_last_cycle_id", "n_max", "bisect_probes",
                "pareto_cap", "budget_cycles", "nonfinite_written_as_null"):
        v = meta.get(key)
        if not isinstance(v, int) or isinstance(v, bool):
            fail(f"_meta.{key} must be an integer")
    for key in ("script", "cost_model", "rounding", "verifier_hash", "schema"):
        if not isinstance(meta.get(key), str):
            fail(f"_meta.{key} must be a string")
    for key in ("archive_dates", "archive_files", "targets", "sources"):
        if not isinstance(meta.get(key), list):
            fail(f"_meta.{key} must be a list")
    if not meta["targets"]:
        fail("_meta.targets must name at least one target")
    sel = meta.get("selection")
    if not isinstance(sel, dict) or sel.get("methods") not in METHOD_SELECTIONS:
        fail("_meta.selection.methods must name a known selection")
    if not isinstance(sel.get("problems"), list) or not sel["problems"]:
        fail("_meta.selection.problems must be a non-empty list")

    findings = [k for k in doc if not k.startswith("_")]
    if not findings:
        fail("document carries no findings")
    for key in sorted(findings):
        finding = doc[key]
        if not isinstance(finding, dict):
            fail(f"{key} must be an object")
        for req in ("verdict", "numbers", "series"):
            if req not in finding:
                fail(f"{key} missing {req!r}")
        if not isinstance(finding["verdict"], str) or not finding["verdict"].strip():
            fail(f"{key}.verdict must be a non-empty string")
        _check_prose(finding["verdict"], f"{key}.verdict", fail)
        if not isinstance(finding["numbers"], dict) or not finding["numbers"]:
            fail(f"{key}.numbers must be a non-empty object")
        series = finding["series"]
        if not isinstance(series, dict) or not series:
            fail(f"{key}.series must be a non-empty object")
        for sname in sorted(series):
            s = series[sname]
            if sname == "chart_hint":
                continue
            if isinstance(s, dict) and "absent" in s:
                continue
            if not s:
                fail(f"{key}.series.{sname} is empty and carries no 'absent' reason")

    _walk_floats(doc, "doc", fail)


def write_results(doc: dict, path: Path | str | None = None) -> Path:
    out = Path(path) if path is not None else work_dir() / "secondpass" / "results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, indent=1, sort_keys=True, allow_nan=False) + "\n"
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Second-pass metrics over the archive (read only).")
    ap.add_argument("--methods", default="all", choices=list(METHOD_SELECTIONS),
                    help="which methods to evaluate (default: all)")
    ap.add_argument("--problems", default=None,
                    help="comma-separated subset of " + ",".join(PROBLEM_NAMES))
    ap.add_argument("--out", default=None,
                    help="output path (default: <RK_WORK_DIR>/secondpass/results.json)")
    a = ap.parse_args(argv)
    names = [s.strip() for s in a.problems.split(",") if s.strip()] if a.problems else None
    # The caps are passed rather than left to the defaults, so a run reads them from
    # the module at call time and _meta stamps what actually ran.
    doc = build_results(selection={"methods": a.methods,
                                   "problems": names or list(PROBLEM_NAMES)},
                        targets=TARGETS, n_max=N_MAX, bisect_probes=BISECT_PROBES)
    validate_results(doc)
    out = write_results(doc, a.out)
    meta = doc["_meta"]
    print(f"wrote {out}")
    print(f"archive records: {meta['archive_records']}, last cycle "
          f"{meta['archive_last_cycle_id']}, {len(meta['archive_dates'])} dates")
    print(f"selection: {meta['selection']['methods']} over "
          f"{len(meta['selection']['problems'])} problems")
    print(doc["work_precision"]["verdict"])
    print(doc["stability_frontier"]["verdict"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
