"""MAP-Elites archive over JSONL files — HANDOFF §4.8, §13.6; SPEC §Surface/archive.

One grid per order 1..4, keyed by (stages, cycle_bucket); fitness is heldout_error
(lower is better).  Records are appended one JSON line at a time with
write-then-fsync; replay discards any line that does not parse (a partial trailing
line after a crash, R2).  Tier assignment is mechanical (K1/K2/B31).

`replay` reads a checkpoint over the closed daily files when one matches them exactly and
folds the current day in; `python -m rk_harness.archive --verify-checkpoint` proves that
path against a full replay.
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import os
import sys
from pathlib import Path

from rk_harness.ledger import load_hypotheses
from rk_harness.orderconditions import achieved_order_symbolic
from rk_harness.paths import archive_dir, work_dir
from rk_harness.problems import FAMILY
from rk_harness.tableau import content_hash, from_json, stages, to_json
from rk_harness.verifier_hash import compute_verifier_hash
from rk_harness.types import TIERS, ArchiveState, CellStat, Record, ScoreVector, Tier


class RecordSchemaError(Exception):
    """A JSON line does not describe a valid Record (K7)."""


RECORD_KEYS = (
    "tableau_hash", "tableau", "score", "tier", "cycle_id", "seed",
    "verifier_hash", "directive_id", "hypothesis_id", "timestamp",
)

_SCORE_KEYS = tuple(f.name for f in dataclasses.fields(ScoreVector))

_MODEL_LONG = {"fast": "m0plus_fast", "slow": "m0plus_slow", "avr_approx": "avr_approx"}
_MODEL_SHORTS = ("fast", "slow", "avr_approx")
_METRICS = ("heldout", "search", "cycles", "order")


# --------------------------------------------------------------------------- JSON

def record_to_json(r: Record) -> dict:
    score = {
        "measured_order": r.score.measured_order,
        "order_fit_points": r.score.order_fit_points,
        "error_constant": r.score.error_constant,
        "stability_real": r.score.stability_real,
        "stability_imag": r.score.stability_imag,
        "cycles": dict(r.score.cycles),
        "csd_weight_total": r.score.csd_weight_total,
        "coeff_quant_error": r.score.coeff_quant_error,
        "search_error": r.score.search_error,
        "heldout_error": r.score.heldout_error,
        "overflow_margin": r.score.overflow_margin,
        "per_problem": dict(r.score.per_problem),
    }
    return {
        "tableau_hash": r.tableau_hash,
        "tableau": to_json(r.tableau),
        "score": score,
        "tier": r.tier,
        "cycle_id": r.cycle_id,
        "seed": r.seed,
        "verifier_hash": r.verifier_hash,
        "directive_id": r.directive_id,
        "hypothesis_id": r.hypothesis_id,
        "timestamp": r.timestamp,
    }


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _score_from_json(s: object) -> ScoreVector:
    if not isinstance(s, dict):
        raise RecordSchemaError("score must be an object")
    keys = set(s.keys())
    expected = set(_SCORE_KEYS)
    if keys != expected:
        missing = sorted(expected - keys)
        unknown = sorted(keys - expected)
        raise RecordSchemaError(f"score keys mismatch: missing={missing} unknown={unknown}")
    if not isinstance(s["cycles"], dict) or not isinstance(s["per_problem"], dict):
        raise RecordSchemaError("score.cycles and score.per_problem must be objects")
    mo = s["measured_order"]
    if mo is not None and not _is_number(mo):
        raise RecordSchemaError("score.measured_order must be a number or null")
    for k in ("error_constant", "stability_real", "stability_imag", "coeff_quant_error",
              "search_error", "heldout_error", "overflow_margin"):
        if not _is_number(s[k]):
            raise RecordSchemaError(f"score.{k} must be a number")
    for k in ("order_fit_points", "csd_weight_total"):
        if not _is_int(s[k]):
            raise RecordSchemaError(f"score.{k} must be an integer")
    for k, v in s["cycles"].items():
        if not _is_int(v):
            raise RecordSchemaError(f"score.cycles[{k!r}] must be an integer")
    for k, v in s["per_problem"].items():
        if not _is_number(v):
            raise RecordSchemaError(f"score.per_problem[{k!r}] must be a number")
    return ScoreVector(
        measured_order=None if mo is None else float(mo),
        order_fit_points=int(s["order_fit_points"]),
        error_constant=float(s["error_constant"]),
        stability_real=float(s["stability_real"]),
        stability_imag=float(s["stability_imag"]),
        cycles={str(k): int(v) for k, v in s["cycles"].items()},
        csd_weight_total=int(s["csd_weight_total"]),
        coeff_quant_error=float(s["coeff_quant_error"]),
        search_error=float(s["search_error"]),
        heldout_error=float(s["heldout_error"]),
        overflow_margin=float(s["overflow_margin"]),
        per_problem={str(k): float(v) for k, v in s["per_problem"].items()},
    )


def record_from_json(d: dict) -> Record:
    if not isinstance(d, dict):
        raise RecordSchemaError("record must be an object")
    keys = set(d.keys())
    expected = set(RECORD_KEYS)
    if keys != expected:
        missing = sorted(expected - keys)
        unknown = sorted(keys - expected)
        raise RecordSchemaError(f"record keys mismatch: missing={missing} unknown={unknown}")
    tier = d["tier"]
    if not isinstance(tier, str) or tier not in TIERS:
        raise RecordSchemaError(f"tier {tier!r} not in {TIERS}")
    if not isinstance(d["tableau_hash"], str):
        raise RecordSchemaError("tableau_hash must be a string")
    for k in ("cycle_id", "seed"):
        if not _is_int(d[k]):
            raise RecordSchemaError(f"{k} must be an integer")
    for k in ("verifier_hash", "timestamp"):
        if not isinstance(d[k], str):
            raise RecordSchemaError(f"{k} must be a string")
    for k in ("directive_id", "hypothesis_id"):
        if d[k] is not None and not isinstance(d[k], str):
            raise RecordSchemaError(f"{k} must be a string or null")
    try:
        tab = from_json(d["tableau"])
        h = content_hash(tab)
    except Exception as e:  # any malformed tableau
        raise RecordSchemaError(f"bad tableau: {e!r}") from e
    if h != d["tableau_hash"]:
        raise RecordSchemaError(f"tableau_hash mismatch: {d['tableau_hash']} != {h}")
    score = _score_from_json(d["score"])
    return Record(
        tableau_hash=d["tableau_hash"],
        tableau=tab,
        score=score,
        tier=tier,
        cycle_id=d["cycle_id"],
        seed=d["seed"],
        verifier_hash=d["verifier_hash"],
        directive_id=d["directive_id"],
        hypothesis_id=d["hypothesis_id"],
        timestamp=d["timestamp"],
    )


# --------------------------------------------------------------------------- descriptors

def record_order(r: Record) -> int:
    return min(achieved_order_symbolic(r.tableau, max_order=5), 4)


def cycle_bucket(cycles: int) -> int:
    """HANDOFF §4.8 table: <16 -> 0, 16-31 -> 1, 32-63 -> 2, ..., >=1024 -> 7."""
    c = int(cycles)
    if c < 16:
        return 0
    b = c.bit_length() - 4          # floor(log2(c)) - 3 : 16 -> 1, 32 -> 2, 1024 -> 7
    return 7 if b > 7 else b


def _fast_cycles(r: Record) -> int:
    v = r.score.cycles.get("m0plus_fast")
    return 0 if v is None else int(v)


def _cell_key(r: Record) -> tuple[int, int]:
    return (stages(r.tableau), cycle_bucket(_fast_cycles(r)))


# --------------------------------------------------------------------------- files

def _clock_date() -> datetime.date:
    raw = os.environ.get("RK_CLOCK")
    if raw:
        txt = raw.strip()
        if txt.endswith("Z") or txt.endswith("z"):
            txt = txt[:-1] + "+00:00"
        dt = datetime.datetime.fromisoformat(txt)
        if dt.tzinfo is not None:
            dt = dt.astimezone(datetime.timezone.utc)
        return dt.date()
    return datetime.datetime.now(datetime.timezone.utc).date()


def today_path() -> Path:
    return archive_dir() / f"{_clock_date().isoformat()}.jsonl"


def append(r: Record) -> None:
    path = today_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record_to_json(r)) + "\n"
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def _warn(msg: str) -> None:
    print(f"[archive] warning: {msg}", file=sys.stderr)


def _archive_files() -> list[Path]:
    """Every *.jsonl in the archive directory, in name order, which is date order."""
    d = archive_dir()
    if not d.is_dir():
        return []
    return sorted((p for p in d.iterdir() if p.is_file() and p.name.endswith(".jsonl")),
                  key=lambda p: p.name)


def _read_file(path: Path) -> list[Record]:
    """The records in one archive file, with the discards read_all has always made."""
    out: list[Record] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        _warn(f"cannot read {path.name}: {e!r}")
        return out
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
            out.append(record_from_json(json.loads(line)))
        except (ValueError, RecordSchemaError) as e:   # JSONDecodeError is a ValueError
            if i != last:
                _warn(f"{path.name}:{i + 1}: discarded unparsable line ({e.__class__.__name__})")
            elif isinstance(e, RecordSchemaError):
                _warn(f"{path.name}:{i + 1}: discarded invalid record ({e})")
            # a partial trailing line after a crash is discarded silently (R2)
    return out


def read_all() -> list[Record]:
    return [r for path in _archive_files() for r in _read_file(path)]


# --------------------------------------------------------------------------- grids

def _better(cand: Record, inc: Record) -> bool:
    """Strictly lower heldout_error wins; ties (and NaN) keep the earlier record."""
    return cand.score.heldout_error < inc.score.heldout_error


def _grids_from(records: list[Record], orders: list[int]) -> dict[int, dict[tuple[int, int], Record]]:
    grids: dict[int, dict[tuple[int, int], Record]] = {1: {}, 2: {}, 3: {}, 4: {}}
    for r, o in zip(records, orders):
        g = grids.get(o)
        if g is None:
            continue
        key = _cell_key(r)
        inc = g.get(key)
        if inc is None or _better(r, inc):
            g[key] = r
    return grids


def elites(order: int) -> dict[tuple[int, int], Record]:
    records = read_all()
    orders = [record_order(r) for r in records]
    return _grids_from(records, orders).get(order, {})


def metric_value(score: ScoreVector, model_short: str, metric: str) -> float | None:
    if model_short not in _MODEL_LONG:
        raise ValueError(f"unknown model {model_short!r}")
    if metric == "cycles":
        v = score.cycles.get(_MODEL_LONG[model_short])
        return None if v is None else float(v)
    if metric == "order":
        mo = score.measured_order
        return None if mo is None else float(mo)
    if metric not in ("heldout", "search"):
        raise ValueError(f"unknown metric {metric!r}")
    if model_short == "fast":
        v = score.heldout_error if metric == "heldout" else score.search_error
        return None if v is None else float(v)
    v = score.per_problem.get(f"{model_short}:{metric}_error")
    return None if v is None else float(v)


def update_cell_stat(cs: CellStat | None, x: float) -> CellStat:
    x = float(x)
    if cs is None:
        return CellStat(n=1, mean=x, m2=0.0, min=x)
    n = cs.n + 1
    delta = x - cs.mean
    mean = cs.mean + delta / n
    m2 = cs.m2 + delta * (x - mean)
    return CellStat(n=n, mean=mean, m2=m2, min=x if x < cs.min else cs.min)


def _hypothesis_ids() -> tuple[tuple[str, ...], tuple[str, ...]]:
    try:
        hyps = load_hypotheses()
    except OSError as e:
        _warn(f"cannot load hypotheses: {e!r}")
        hyps = []
    open_ids: list[str] = []
    refuted: list[str] = []
    for h in hyps:
        if not isinstance(h, dict) or "id" not in h:
            continue
        v = h.get("verdict")
        if v is None:
            open_ids.append(str(h["id"]))
        elif v == "refuted":
            refuted.append(str(h["id"]))
    return tuple(open_ids), tuple(refuted)


def _state_from(records: list[Record]) -> ArchiveState:
    """The state those records describe. The whole of what `replay` used to be, over a
    record list the caller already has."""
    orders = [record_order(r) for r in records]
    grids = _grids_from(records, orders)
    last_cycle = max((r.cycle_id for r in records), default=0)
    cell_stats: dict[tuple[int, int], dict[str, CellStat]] = {}
    for r, o in zip(records, orders):
        key = (o, stages(r.tableau))
        cell = cell_stats.setdefault(key, {})
        for ms in _MODEL_SHORTS:
            for metric in _METRICS:
                v = metric_value(r.score, ms, metric)
                if v is None:
                    continue
                name = f"{ms}.{metric}"
                cell[name] = update_cell_stat(cell.get(name), v)
    open_ids, refuted = _hypothesis_ids()
    return ArchiveState(
        n_records=len(records),
        last_cycle_id=last_cycle,
        grids=grids,
        open_hypotheses=open_ids,
        refuted_hypotheses=refuted,
        cell_stats=cell_stats,
        record_hashes=frozenset(r.tableau_hash for r in records),
    )


def _replay_all() -> ArchiveState:
    """Every file, every record, no checkpoint. The fallback `replay` drops to, and the
    reference every checkpoint is checked against."""
    return _state_from(read_all())


def replay_with_records() -> tuple[ArchiveState, list[Record]]:
    """(state, records) from one full pass, for callers that want both.

    `replay()` alone reads the archive and throws the records away, so a viewer that
    wants both was making two passes over the same files. This makes one.
    """
    records = read_all()
    return _state_from(records), records


def replay() -> ArchiveState:
    """The state of the whole archive.

    Tries the checkpoint over the closed daily files first and folds the rest in; falls
    back to a full pass whenever the checkpoint does not match the files exactly. The
    checkpoint is an accelerator and never a source of truth: `load_checkpoint` rejects
    it on any change of name, size, mtime, file order or verifier hash, and the rejection
    is warned about and reported through `last_checkpoint_report()`. Either path ends in
    code that re-reads the hypothesis ids, because `replay` promises the ledger's current
    verdicts.
    """
    global _LAST_CHECKPOINT
    base, covered, reason = load_checkpoint()
    if base is None:
        _LAST_CHECKPOINT = {"used": False, "reason": reason, "covered_files": 0,
                            "folded_files": len(_archive_files())}
        if reason != "absent":
            _warn(f"checkpoint not used: {reason}")
        return _replay_all()
    rest = _archive_files()[len(covered):]
    delta = [r for path in rest for r in _read_file(path)]
    _LAST_CHECKPOINT = {"used": True, "reason": "", "covered_files": len(covered),
                        "covered_records": base.n_records, "folded_files": len(rest),
                        "folded_records": len(delta)}
    return fold(base, delta)


def fold(state: ArchiveState, records: list[Record]) -> ArchiveState:
    """The state `replay()` would return if `records` were appended to the archive that
    `state` was built from.

    A cycle needs the post-append state twice, to resolve hypotheses and to build the
    site, and re-reading the whole archive for each costs more than the search it wraps:
    at 71k records a replay is about 66 s against roughly 250 s of actual searching.
    Every rule below is the one `replay()` applies, moved from the whole file onto the
    delta. Grids use `_better`, cell stats use the same Welford update fed in the same
    order (appended records sort last in the same files `read_all()` walks), so this is
    identical to a replay rather than merely close; `test_A80` asserts exactly that.

    Hypothesis ids are re-read rather than carried, because the cycle may have proposed
    or resolved one since `state` was built.
    """
    grids = {o: dict(g) for o, g in state.grids.items()}
    cell_stats = {k: dict(v) for k, v in state.cell_stats.items()}
    last_cycle = state.last_cycle_id
    hashes = set(state.record_hashes)
    for r in records:
        o = record_order(r)
        g = grids.get(o)
        if g is not None:
            key = _cell_key(r)
            inc = g.get(key)
            if inc is None or _better(r, inc):
                g[key] = r
        cell = cell_stats.setdefault((o, stages(r.tableau)), {})
        for ms in _MODEL_SHORTS:
            for metric in _METRICS:
                v = metric_value(r.score, ms, metric)
                if v is None:
                    continue
                name = f"{ms}.{metric}"
                cell[name] = update_cell_stat(cell.get(name), v)
        if r.cycle_id > last_cycle:
            last_cycle = r.cycle_id
        hashes.add(r.tableau_hash)
    open_ids, refuted = _hypothesis_ids()
    return ArchiveState(
        n_records=state.n_records + len(records),
        last_cycle_id=last_cycle,
        grids=grids,
        open_hypotheses=open_ids,
        refuted_hypotheses=refuted,
        cell_stats=cell_stats,
        record_hashes=frozenset(hashes),
    )


def refresh_hypotheses(state: ArchiveState) -> ArchiveState:
    """`state` with only the hypothesis id lists re-read from the ledger.

    Resolving hypotheses rewrites the ledger mid-cycle, and the site is built after
    that, so it has to see the new verdicts. Nothing else about the state changes, and
    the grids are shared rather than copied.
    """
    open_ids, refuted = _hypothesis_ids()
    return dataclasses.replace(state, open_hypotheses=open_ids, refuted_hypotheses=refuted)


# --------------------------------------------------------------------------- checkpoint

CHECKPOINT_FORMAT = 1

# What the last replay() in this process did with the checkpoint. Read by the runner,
# which logs a rejection, and by --verify-checkpoint.
_LAST_CHECKPOINT: dict = {"used": False, "reason": "absent", "covered_files": 0,
                          "folded_files": 0}

# What the last maybe_write_checkpoint did, for the event the runner logs.
_LAST_WRITE: dict = {"written": False, "reason": "not attempted", "covered_files": 0,
                     "n_records": 0}


def checkpoint_path() -> Path:
    return work_dir() / "ARCHIVE_CHECKPOINT.json"


def last_checkpoint_report() -> dict:
    """What the most recent replay() in this process did with the checkpoint."""
    return dict(_LAST_CHECKPOINT)


def _checkpoint_stamp() -> str:
    """UTC ISO stamp for the checkpoint file, honouring RK_CLOCK the way today_path does."""
    raw = os.environ.get("RK_CLOCK")
    dt = None
    if raw:
        txt = raw.strip()
        if txt.endswith("Z") or txt.endswith("z"):
            txt = txt[:-1] + "+00:00"
        try:
            dt = datetime.datetime.fromisoformat(txt)
        except ValueError:
            dt = None
        if dt is not None:
            dt = dt.replace(tzinfo=datetime.timezone.utc) if dt.tzinfo is None else dt
            dt = dt.astimezone(datetime.timezone.utc)
    if dt is None:
        dt = datetime.datetime.now(datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


_DERIVATION_FILES: tuple[str, ...] = ("archive.py", "tableau.py")


def _derivation_hash() -> str | None:
    """sha256 over the modules that decide the SHAPE of a replayed state, or None if they
    cannot be read.

    The verifier hash covers the ten pinned files, which is what the records were scored
    under. It says nothing about the code that folds records into grids and cell statistics,
    and that code is here and in tableau.py: cycle_bucket, _cell_key, _better,
    update_cell_stat, metric_value. Change one of those and a checkpoint written under the
    old rules is still internally consistent, still passes every size and mtime check, and
    still serves the old cell layout while a full replay produces a new one. That failure is
    silent and it moves published elite pages, so the document pins its own derivation.
    """
    h = hashlib.sha256()
    here = Path(__file__).resolve().parent
    for name in _DERIVATION_FILES:
        try:
            h.update((here / name).read_bytes())
        except OSError:
            return None
    return h.hexdigest()


def _current_verifier_hash() -> str | None:
    """The verifier hash now, or None if it cannot be computed. A checkpoint records the
    hash the files it covers were scored under, and is not used under a different one."""
    try:
        return compute_verifier_hash()
    except Exception:  # noqa: BLE001 - an unreadable pinned file must not break a replay
        return None


def _atomic_write_text(path: Path, text: str) -> None:
    """Write through a temp sibling and os.replace, so a reader never sees half a file.
    archive.py must not import runner for this: runner imports archive."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _state_to_json(state: ArchiveState, covered, verifier_hash: str | None) -> dict:
    """The checkpoint document.

    `covered` is a sequence of (name, size, mtime_ns) triples: everything a later load
    needs to prove the files have not moved under it. Every list is sorted, so two writes
    of one state are byte-identical. Floats are emitted untouched, so the shortest
    round-trip repr carries them back bit for bit. Hypothesis ids are deliberately not
    stored: replay re-reads them from the ledger on every call, and so must the checkpoint
    path.
    """
    grids: dict[str, list] = {}
    for order in (1, 2, 3, 4):
        grid = state.grids.get(order) or {}
        grids[str(order)] = [[[int(stg), int(bucket)], record_to_json(rec)]
                             for (stg, bucket), rec in sorted(grid.items())]
    cell_stats: list = []
    for key in sorted(state.cell_stats):
        stats = state.cell_stats[key]
        cell_stats.append([[int(key[0]), int(key[1])],
                           [[name, [stats[name].n, stats[name].mean, stats[name].m2, stats[name].min]]
                            for name in sorted(stats)]])
    return {
        "format": CHECKPOINT_FORMAT,
        "written_at": _checkpoint_stamp(),
        "verifier_hash": verifier_hash,
        "derivation_hash": _derivation_hash(),
        "covered": [{"name": str(name), "size": int(size), "mtime_ns": int(mtime_ns)}
                    for name, size, mtime_ns in covered],
        "n_records": int(state.n_records),
        "last_cycle_id": int(state.last_cycle_id),
        "grids": grids,
        "cell_stats": cell_stats,
        "record_hashes": sorted(state.record_hashes),
    }


def _state_from_json(d: dict) -> ArchiveState:
    """The inverse of _state_to_json. Any shape error is a RecordSchemaError, which
    load_checkpoint turns into a rejection rather than a crash."""
    try:
        grids: dict[int, dict[tuple[int, int], Record]] = {}
        for order in (1, 2, 3, 4):
            grid: dict[tuple[int, int], Record] = {}
            for key, rec in d["grids"][str(order)]:
                grid[(int(key[0]), int(key[1]))] = record_from_json(rec)
            grids[order] = grid
        cell_stats: dict[tuple[int, int], dict[str, CellStat]] = {}
        for key, stats in d["cell_stats"]:
            cell: dict[str, CellStat] = {}
            for name, (n, mean, m2, mn) in stats:
                cell[str(name)] = CellStat(n=int(n), mean=float(mean), m2=float(m2), min=float(mn))
            cell_stats[(int(key[0]), int(key[1]))] = cell
        return ArchiveState(
            n_records=int(d["n_records"]),
            last_cycle_id=int(d["last_cycle_id"]),
            grids=grids,
            open_hypotheses=(),
            refuted_hypotheses=(),
            cell_stats=cell_stats,
            record_hashes=frozenset(str(h) for h in d["record_hashes"]),
        )
    except RecordSchemaError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError) as e:
        raise RecordSchemaError(f"bad checkpoint shape: {e!r}") from e


def _load_checkpoint_doc() -> tuple[dict | None, list[str], str]:
    """(document, covered file names, reason it was rejected), without rebuilding the
    state. Every rule that decides whether a checkpoint may be used lives here; the write
    path needs the answer but not the records."""
    path = checkpoint_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, [], "absent"
    except OSError as e:
        return None, [], f"unreadable: {e.__class__.__name__}"
    try:
        d = json.loads(text)
    except ValueError as e:
        return None, [], f"unreadable: {e.__class__.__name__}"
    if not isinstance(d, dict):
        return None, [], "unreadable: not an object"
    fmt = d.get("format")
    if fmt != CHECKPOINT_FORMAT:
        return None, [], f"format {fmt!r} is not {CHECKPOINT_FORMAT}"
    stored_vh = d.get("verifier_hash")
    if not isinstance(stored_vh, str):
        return None, [], "verifier hash not recorded"
    current_vh = _current_verifier_hash()
    if current_vh is None:
        return None, [], "verifier hash unavailable"
    if current_vh != stored_vh:
        return None, [], f"verifier hash changed: {stored_vh[:12]} != {current_vh[:12]}"
    stored_dh = d.get("derivation_hash")
    if not isinstance(stored_dh, str):
        return None, [], "derivation hash not recorded"
    current_dh = _derivation_hash()
    if current_dh is None:
        return None, [], "derivation hash unavailable"
    if current_dh != stored_dh:
        return None, [], f"derivation changed: {stored_dh[:12]} != {current_dh[:12]}"
    covered = d.get("covered")
    if not isinstance(covered, list) or not covered:
        return None, [], "no covered files"
    names: list[str] = []
    stamps: list[tuple[int, int]] = []
    for entry in covered:
        if (not isinstance(entry, dict) or not isinstance(entry.get("name"), str)
                or not _is_int(entry.get("size")) or not _is_int(entry.get("mtime_ns"))):
            return None, [], "malformed covered entry"
        names.append(entry["name"])
        stamps.append((int(entry["size"]), int(entry["mtime_ns"])))
    d_dir = archive_dir()
    for name, (size, mtime_ns) in zip(names, stamps):
        try:
            st = (d_dir / name).stat()
        except OSError:
            return None, [], f"covered file is gone: {name}"
        if st.st_size != size:
            return None, [], f"size changed for {name}: {size} != {st.st_size}"
        if st.st_mtime_ns != mtime_ns:
            return None, [], f"mtime changed for {name}"
    present = [q.name for q in _archive_files()]
    if present[:len(names)] != names:
        return None, [], "covered files are not the oldest files present"
    return d, names, ""


def load_checkpoint() -> tuple[ArchiveState | None, list[str], str]:
    """(state, covered file names, reason it was rejected).

    A checkpoint is used only when it summarises exactly the oldest files present, in
    order, each still the size it was when the checkpoint was written. That prefix rule
    is what makes checkpoint-plus-fold identical to a replay rather than merely close:
    Welford is order dependent and a grid keeps the earlier record on a tie, so a file
    that sorts before a covered one has to force a full replay.
    """
    d, names, reason = _load_checkpoint_doc()
    if d is None:
        return None, [], reason
    try:
        return _state_from_json(d), names, ""
    except RecordSchemaError as e:
        return None, [], f"unreadable: {e}"


def write_checkpoint(state: ArchiveState, covered, verifier_hash: str | None = None) -> Path:
    """Write the checkpoint for `state` over `covered`, a sequence of (name, size,
    mtime_ns) triples. `verifier_hash` defaults to the current one, because a checkpoint
    without a hash is never used. Unconditional; maybe_write_checkpoint is what decides
    whether writing is safe."""
    path = checkpoint_path()
    doc = _state_to_json(state, covered, _current_verifier_hash() if verifier_hash is None else verifier_hash)
    _atomic_write_text(path, json.dumps(doc, sort_keys=True, separators=(",", ":")))
    return path


def last_checkpoint_write() -> dict:
    """What the most recent maybe_write_checkpoint in this process did, so the caller can
    log the covered file count without re-reading the file it just wrote."""
    return dict(_LAST_WRITE)


def maybe_write_checkpoint(state: ArchiveState, verifier_hash: str | None = None) -> str | None:
    """Write a checkpoint if `state` provably describes exactly the closed daily files.

    None means written; a string is the reason it was skipped, and skipping is the normal
    case, since a checkpoint is owed about once a day. The state must have been built
    while the current day file was still empty, which is why the runner calls this at the
    start of a cycle, before anything is appended.
    """
    global _LAST_WRITE
    reason, covered_files = _maybe_write_checkpoint(state, verifier_hash)
    _LAST_WRITE = {"written": reason is None, "reason": reason,
                   "covered_files": covered_files, "n_records": int(state.n_records)}
    return reason


def _maybe_write_checkpoint(state: ArchiveState, verifier_hash: str | None) -> tuple[str | None, int]:
    files = _archive_files()
    today = today_path().name
    if any(p.name > today for p in files):
        return "a file dated after today is present", 0
    closed = [p for p in files if p.name != today]
    if not closed:
        return "no closed archive files", 0
    today_file = archive_dir() / today
    try:
        if today_file.stat().st_size > 0:
            return "the current day file already has records", 0
    except OSError:
        pass
    covered: list[tuple[str, int, int]] = []
    for p in closed:
        try:
            st = p.stat()
        except OSError as e:
            return f"cannot stat {p.name}: {e.__class__.__name__}", 0
        covered.append((p.name, st.st_size, st.st_mtime_ns))
    # only the covered names matter here, so the document is read without rebuilding the
    # state: this runs at the start of every cycle and the file is about 7 MB
    current, current_names, _ = _load_checkpoint_doc()
    if current is not None and current_names == [name for name, _, _ in covered]:
        return "already current", len(covered)
    path = write_checkpoint(state, covered, verifier_hash)
    moved = False
    for name, size, mtime_ns in covered:
        try:
            st = (archive_dir() / name).stat()
            if st.st_size != size or st.st_mtime_ns != mtime_ns:
                moved = True
        except OSError:
            moved = True
    try:
        if today_file.stat().st_size > 0:
            moved = True
    except OSError:
        pass
    if moved:
        try:
            path.unlink()
        except OSError:
            pass
        return "the archive moved while writing", 0
    return None, len(covered)


# --------------------------------------------------------------------------- viewer cache

_VIEW_CACHE: dict = {"key": None, "state": None, "records": None}


def _dir_signature() -> tuple:
    """Cheap proof that the append-only archive has not changed: the directory itself,
    then name, size and mtime_ns per file.

    archive_dir() reads RK_WORK_DIR on every call, so the directory has to be in the key.
    Without it, a test or host tool that redirects the work dir is served another
    directory records.
    """
    try:
        return (str(archive_dir()),) + tuple(
            (p.name, p.stat().st_size, p.stat().st_mtime_ns)
            for p in sorted(archive_dir().glob("*.jsonl")))
    except OSError:
        return (str(archive_dir()),)


def cached_view() -> tuple[ArchiveState, list[Record]]:
    """(state, records), re-read only when the archive files actually change.

    Viewers and host tools only, never the runner: the runner uses `fold`, because it
    knows what it appended and a cache can only be wrong there. The archive is
    append-only, so a rewrite that preserves both size and mtime_ns is not a case this
    system produces.
    """
    key = _dir_signature()
    if _VIEW_CACHE["key"] == key and _VIEW_CACHE["records"] is not None:
        return _VIEW_CACHE["state"], _VIEW_CACHE["records"]
    state, records = replay_with_records()
    _VIEW_CACHE["key"] = key
    _VIEW_CACHE["state"] = state
    _VIEW_CACHE["records"] = records
    return state, records


def cached_state() -> ArchiveState:
    """The state alone, cached on the same signature.

    Same rule as cached_view: viewers and host tools only. Holding the record list for
    the life of a process costs about a gigabyte on a full archive, so a caller that does
    not need the records asks for this instead.
    """
    key = _dir_signature()
    if _VIEW_CACHE["key"] == key and _VIEW_CACHE["state"] is not None:
        return _VIEW_CACHE["state"]
    state = replay()
    _VIEW_CACHE["key"] = key
    _VIEW_CACHE["state"] = state
    _VIEW_CACHE["records"] = None
    return state


def clear_cache() -> None:
    """Forget the cached view. The test suite calls this around every test, because a
    cached view must not survive into another work dir."""
    _VIEW_CACHE["key"] = None
    _VIEW_CACHE["state"] = None
    _VIEW_CACHE["records"] = None


# --------------------------------------------------------------------------- tiers

def _families_improved(cand: ScoreVector, inc: ScoreVector) -> int:
    fams: set[str] = set()
    for name, fam in FAMILY.items():
        cv = cand.per_problem.get(name)
        iv = inc.per_problem.get(name)
        if cv is None or iv is None:
            continue
        if cv < iv:
            fams.add(fam)
    return len(fams)


def assign_tier(cand: ScoreVector, incumbent: ScoreVector | None) -> Tier:
    if incumbent is None:
        return "unreplicated"
    beats_search = cand.search_error < incumbent.search_error
    beats_heldout = cand.heldout_error < incumbent.heldout_error
    fam = _families_improved(cand, incumbent)
    if beats_search and beats_heldout and fam >= 2:
        return "heldout_verified"
    if beats_search and not beats_heldout:
        return "search_only"
    return "unreplicated"


# --------------------------------------------------------------------------- CLI

def _same_number(a, b) -> bool:
    """Equality that treats two NaNs as the same value, because a cell stat over a
    non-finite score is NaN on both sides and that is agreement, not a difference."""
    if isinstance(a, float) and isinstance(b, float) and a != a and b != b:
        return True
    return a == b


def _diff_states(fast: ArchiveState, full: ArchiveState) -> list[str]:
    """Every way the checkpoint path and a full replay disagree. Empty means identical."""
    diffs: list[str] = []
    if fast.n_records != full.n_records:
        diffs.append(f"n_records {fast.n_records} != {full.n_records}")
    if fast.last_cycle_id != full.last_cycle_id:
        diffs.append(f"last_cycle_id {fast.last_cycle_id} != {full.last_cycle_id}")
    for order in (1, 2, 3, 4):
        a = fast.grids.get(order) or {}
        b = full.grids.get(order) or {}
        if sorted(a) != sorted(b):
            only_a = sorted(set(a) - set(b))
            only_b = sorted(set(b) - set(a))
            diffs.append(f"grid {order} cells differ: checkpoint only {only_a}, replay only {only_b}")
        for key in sorted(set(a) & set(b)):
            if (a[key].tableau_hash, a[key].cycle_id) != (b[key].tableau_hash, b[key].cycle_id):
                diffs.append(f"grid {order} cell {key}: {a[key].tableau_hash[:12]}/{a[key].cycle_id} "
                             f"!= {b[key].tableau_hash[:12]}/{b[key].cycle_id}")
    if sorted(fast.cell_stats) != sorted(full.cell_stats):
        only_a = sorted(set(fast.cell_stats) - set(full.cell_stats))
        only_b = sorted(set(full.cell_stats) - set(fast.cell_stats))
        diffs.append(f"cell_stats keys differ: checkpoint only {only_a}, replay only {only_b}")
    for key in sorted(set(fast.cell_stats) & set(full.cell_stats)):
        sa, sb = fast.cell_stats[key], full.cell_stats[key]
        if sorted(sa) != sorted(sb):
            diffs.append(f"cell_stats {key} metric names differ")
        for name in sorted(set(sa) & set(sb)):
            ta = (sa[name].n, sa[name].mean, sa[name].m2, sa[name].min)
            tb = (sb[name].n, sb[name].mean, sb[name].m2, sb[name].min)
            if not all(_same_number(x, y) for x, y in zip(ta, tb)):
                diffs.append(f"cell_stats {key} {name}: {ta} != {tb}")
    if len(fast.record_hashes) != len(full.record_hashes):
        diffs.append(f"record_hashes {len(fast.record_hashes)} != {len(full.record_hashes)}")
    sym = fast.record_hashes ^ full.record_hashes
    if sym:
        diffs.append(f"record_hashes differ by {len(sym)} hashes, e.g. {sorted(sym)[:3]}")
    return diffs


def main(argv: list[str] | None = None) -> int:
    import argparse

    from rk_harness import verifier_hash as vh_module

    ap = argparse.ArgumentParser(prog="rk_harness.archive",
                                 description="archive checkpoint maintenance (read-only apart from --checkpoint)")
    ap.add_argument("--checkpoint", action="store_true",
                    help="replay every file, then write a checkpoint over the closed daily files")
    ap.add_argument("--verify-checkpoint", action="store_true",
                    help="compare the checkpoint path against a full replay, field by field")
    args = ap.parse_args(argv)

    if args.checkpoint:
        state = _replay_all()
        today = today_path().name
        closed = [q.name for q in _archive_files() if q.name != today]
        reason = maybe_write_checkpoint(state, vh_module.compute_verifier_hash())
        if reason is None:
            print(f"wrote {checkpoint_path()}: {len(closed)} closed files, {state.n_records} records")
        else:
            print(f"no checkpoint written: {reason}")
        return 0

    if args.verify_checkpoint:
        fast = replay()
        report = last_checkpoint_report()
        full = _replay_all()
        print(f"checkpoint used: {report.get('used')} reason: {report.get('reason') or 'none'} "
              f"covered files: {report.get('covered_files')} folded files: {report.get('folded_files')}")
        diffs = _diff_states(fast, full)
        for line in diffs:
            print(f"DIFFERENCE {line}")
        print(f"{len(diffs)} differences; {full.n_records} records in the archive")
        return 1 if diffs else 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
