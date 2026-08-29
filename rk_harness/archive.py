"""MAP-Elites archive over JSONL files — HANDOFF §4.8, §13.6; SPEC §Surface/archive.

One grid per order 1..4, keyed by (stages, cycle_bucket); fitness is heldout_error
(lower is better).  Records are appended one JSON line at a time with
write-then-fsync; replay discards any line that does not parse (a partial trailing
line after a crash, R2).  Tier assignment is mechanical (K1/K2/B31).
"""
from __future__ import annotations

import dataclasses
import datetime
import json
import os
import sys
from pathlib import Path

from rk_harness.ledger import load_hypotheses
from rk_harness.orderconditions import achieved_order_symbolic
from rk_harness.paths import archive_dir, work_dir  # noqa: F401
from rk_harness.problems import FAMILY
from rk_harness.tableau import content_hash, from_json, stages, to_json
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


def read_all() -> list[Record]:
    d = archive_dir()
    if not d.is_dir():
        return []
    files = sorted((p for p in d.iterdir() if p.is_file() and p.name.endswith(".jsonl")),
                   key=lambda p: p.name)
    out: list[Record] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            _warn(f"cannot read {path.name}: {e!r}")
            continue
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


def replay() -> ArchiveState:
    records = read_all()
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
    )


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
