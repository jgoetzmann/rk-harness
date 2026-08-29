"""Hypothesis ledger — HANDOFF §6.

Hand-written (HANDOFF §16.1): `parse_predicate` consumes untrusted model output.
It is a hand-rolled tokenizer + recursive-descent parser over the fixed grammar

    expr    := term (("AND" | "OR") term)*
    term    := field op field | field op number
    field   := model "." cell "." metric
    model   := "fast" | "slow" | "avr_approx"
    cell    := "p" digit "s" digit
    metric  := "heldout" | "search" | "cycles" | "order"
    op      := "<" | ">" | "<=" | ">=" | "=="

Nothing else parses. There is no dynamic code execution anywhere in this file.
The verdict is computed by code from the archive; the model never writes it.
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path

from rk_harness.paths import work_dir
from rk_harness.types import ArchiveState, CellStat, Verdict


class PredicateSyntaxError(Exception):
    """Raised on any input outside the §6 grammar."""


@dataclass(frozen=True)
class Field:
    model: str      # "fast" | "slow" | "avr_approx"
    order: int      # p digit
    stages: int     # s digit
    metric: str     # "heldout" | "search" | "cycles" | "order"


@dataclass(frozen=True)
class Term:
    left: Field
    op: str
    right: Field | float


@dataclass(frozen=True)
class Predicate:
    terms: tuple[Term, ...]
    ops: tuple[str, ...]        # "AND"/"OR" between consecutive terms; evaluated left to right


MODELS = ("fast", "slow", "avr_approx")
METRICS = ("heldout", "search", "cycles", "order")
OPS = ("<", ">", "<=", ">=", "==")
CONNECTIVES = ("AND", "OR")
MAX_PREDICATE_CHARS = 2000

_FIELD_RE = re.compile(r"^(fast|slow|avr_approx)\.p([0-9])s([0-9])\.(heldout|search|cycles|order)$")
_NUMBER_RE = re.compile(r"^-?(?:[0-9]+\.[0-9]*|[0-9]+|\.[0-9]+)(?:[eE][-+]?[0-9]+)?$")
# Tokens: two-char ops first, then one-char ops, then bare words/numbers. Anything else is a
# single "junk" character token which the parser rejects.
_TOKEN_RE = re.compile(r"<=|>=|==|<|>|[A-Za-z_][A-Za-z0-9_.]*|-?[0-9.]+(?:[eE][-+]?[0-9]+)?|\S")

# Required hypothesis keys and their accepted types (HANDOFF §6 example).
_HYP_REQUIRED = {
    "id": str, "cycle_proposed": int, "statement": str, "mechanism": str,
    "control": str, "predicate": str, "min_samples": int,
}
_HYP_OPTIONAL = ("verdict", "n_samples", "effect_size", "resolved_cycle")
_RESOLUTION_KEYS = ("id", "verdict", "n_samples", "effect_size", "resolved_cycle")
_ID_RE = re.compile(r"^H-[0-9]+$")


# --------------------------------------------------------------------------- parsing

def _tokenize(src: str) -> list[str]:
    if not isinstance(src, str):
        raise PredicateSyntaxError("predicate must be a string")
    if len(src) > MAX_PREDICATE_CHARS:
        raise PredicateSyntaxError("predicate too long")
    if any(ord(ch) < 32 and ch not in " \t" for ch in src):
        raise PredicateSyntaxError("control character in predicate")
    tokens = _TOKEN_RE.findall(src)
    if not tokens:
        raise PredicateSyntaxError("empty predicate")
    return tokens


def _parse_field(tok: str) -> Field:
    m = _FIELD_RE.match(tok)
    if m is None:
        raise PredicateSyntaxError(f"expected field, got {tok!r}")
    return Field(m.group(1), int(m.group(2)), int(m.group(3)), m.group(4))


def _parse_operand(tok: str) -> Field | float:
    if _FIELD_RE.match(tok):
        return _parse_field(tok)
    if _NUMBER_RE.match(tok):
        value = float(tok)
        if not math.isfinite(value):
            raise PredicateSyntaxError("non-finite number")
        return value
    raise PredicateSyntaxError(f"expected field or number, got {tok!r}")


def _parse_term(tokens: list[str], pos: int) -> tuple[Term, int]:
    if pos + 3 > len(tokens):
        raise PredicateSyntaxError("incomplete term")
    left = _parse_field(tokens[pos])
    op = tokens[pos + 1]
    if op not in OPS:
        raise PredicateSyntaxError(f"expected comparison operator, got {op!r}")
    right = _parse_operand(tokens[pos + 2])
    return Term(left, op, right), pos + 3


def parse_predicate(src: str) -> Predicate:
    """Parse `src` against the §6 grammar. Raises PredicateSyntaxError on anything else."""
    tokens = _tokenize(src)
    terms: list[Term] = []
    ops: list[str] = []
    term, pos = _parse_term(tokens, 0)
    terms.append(term)
    while pos < len(tokens):
        connective = tokens[pos]
        if connective not in CONNECTIVES:
            raise PredicateSyntaxError(f"expected AND/OR, got {connective!r}")
        term, pos = _parse_term(tokens, pos + 1)
        ops.append(connective)
        terms.append(term)
    return Predicate(tuple(terms), tuple(ops))


# --------------------------------------------------------------------------- evaluation

def _cell_stat(arch: ArchiveState, f: Field) -> CellStat | None:
    cell = arch.cell_stats.get((f.order, f.stages))
    if not cell:
        return None
    return cell.get(f"{f.model}.{f.metric}")


def _field_value(stat: CellStat, metric: str) -> float:
    # pXsY resolves to the best (minimum) value of the metric over every bucket at that
    # (order, stages) — HANDOFF §6. For "order" the mean is the meaningful summary.
    return stat.mean if metric == "order" else stat.min


def _cohens_d(a: CellStat, b: CellStat) -> float:
    var_a = a.m2 / (a.n - 1) if a.n > 1 else 0.0
    var_b = b.m2 / (b.n - 1) if b.n > 1 else 0.0
    dof = a.n + b.n - 2
    pooled = math.sqrt(((a.n - 1) * var_a + (b.n - 1) * var_b) / dof) if dof > 0 else 0.0
    diff = abs(a.mean - b.mean)
    if pooled == 0.0:
        return math.inf if diff > 0 else 0.0
    return diff / pooled


def _compare(left: float, op: str, right: float) -> bool:
    if op == "<":
        return left < right
    if op == ">":
        return left > right
    if op == "<=":
        return left <= right
    if op == ">=":
        return left >= right
    return left == right


def evaluate_predicate(pr: Predicate, arch: ArchiveState) -> tuple[Verdict, int, float]:
    """Pure. Returns (verdict, n_samples, effect_size).

    A missing cell is absence of evidence: ("inconclusive", 0, 0.0), never refuted.
    n_samples is the smallest record count among the cells referenced. effect_size is the
    smallest Cohen's d over field-vs-field terms (0.0 when no populations are compared);
    d below 0.2 on a compared population makes the verdict inconclusive.
    """
    truth: bool | None = None
    n_samples = math.inf
    effect = math.inf
    compared = False
    for i, term in enumerate(pr.terms):
        ls = _cell_stat(arch, term.left)
        if ls is None:
            return "inconclusive", 0, 0.0
        n_samples = min(n_samples, ls.n)
        lv = _field_value(ls, term.left.metric)
        if isinstance(term.right, Field):
            rs = _cell_stat(arch, term.right)
            if rs is None:
                return "inconclusive", 0, 0.0
            n_samples = min(n_samples, rs.n)
            rv = _field_value(rs, term.right.metric)
            effect = min(effect, _cohens_d(ls, rs))
            compared = True
        else:
            rv = float(term.right)
        t = _compare(lv, term.op, rv)
        if truth is None:
            truth = t
        elif pr.ops[i - 1] == "AND":
            truth = truth and t
        else:
            truth = truth or t
    n = int(n_samples) if n_samples != math.inf else 0
    d = 0.0 if not compared else (effect if math.isfinite(effect) else 1e9)
    if compared and d < 0.2:
        return "inconclusive", n, d
    return ("supported" if truth else "refuted"), n, d


# --------------------------------------------------------------------------- persistence

def hypotheses_path() -> Path:
    return work_dir() / "hypotheses.jsonl"


def _append_line(obj: dict) -> None:
    path = hypotheses_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, sort_keys=True) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


def _validate_hypothesis(h: dict) -> dict:
    if not isinstance(h, dict):
        raise ValueError("hypothesis must be a dict")
    unknown = set(h) - set(_HYP_REQUIRED) - set(_HYP_OPTIONAL)
    if unknown:
        raise ValueError(f"unknown hypothesis keys: {sorted(unknown)}")
    for key, typ in _HYP_REQUIRED.items():
        if key not in h:
            raise ValueError(f"missing hypothesis key: {key}")
        if not isinstance(h[key], typ) or isinstance(h[key], bool):
            raise ValueError(f"hypothesis key {key} must be {typ.__name__}")
    if not _ID_RE.match(h["id"]):
        raise ValueError("hypothesis id must match H-<digits>")
    if h["min_samples"] < 1:
        raise ValueError("min_samples must be >= 1")
    parse_predicate(h["predicate"])          # PredicateSyntaxError propagates
    out = dict(h)
    for key in _HYP_OPTIONAL:
        out.setdefault(key, None)
    if out["verdict"] is not None and out["verdict"] not in ("supported", "refuted", "inconclusive"):
        raise ValueError("verdict must be null or one of supported/refuted/inconclusive")
    return out


def append_hypothesis(h: dict) -> None:
    """Append one hypothesis. Raises ValueError on a malformed dict, PredicateSyntaxError on a bad predicate."""
    _append_line(_validate_hypothesis(h))


def load_hypotheses() -> list[dict]:
    """Hypotheses in first-seen order, with later resolution lines merged in (last write wins)."""
    path = hypotheses_path()
    if not path.exists():
        return []
    order: list[str] = []
    by_id: dict[str, dict] = {}
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(obj, dict) or not isinstance(obj.get("id"), str):
                continue
            hid = obj["id"]
            if "statement" in obj:
                if hid not in by_id:
                    order.append(hid)
                merged = dict(obj)
                for key in _HYP_OPTIONAL:
                    merged.setdefault(key, None)
                by_id[hid] = merged
            elif hid in by_id and set(obj) <= set(_RESOLUTION_KEYS):
                by_id[hid].update({k: obj[k] for k in _RESOLUTION_KEYS if k in obj})
    return [by_id[h] for h in order]


def resolve_one(h: dict, arch: ArchiveState) -> tuple[Verdict, int, float]:
    """Verdict for one hypothesis dict against the archive, honouring min_samples."""
    pr = parse_predicate(h["predicate"])
    verdict, n, d = evaluate_predicate(pr, arch)
    if n < int(h.get("min_samples", 1)):
        return "inconclusive", n, d
    return verdict, n, d


def resolve_open(arch: ArchiveState, cycle_id: int) -> list[str]:
    """Resolve every open hypothesis whose referenced cells have reached min_samples.

    Writes a resolution line per resolved hypothesis and returns their ids. A hypothesis
    with insufficient samples stays open; one with enough samples but Cohen's d < 0.2 is
    resolved as inconclusive — the third bucket must be reachable (HANDOFF §6).
    """
    resolved: list[str] = []
    for h in load_hypotheses():
        if h.get("verdict") is not None:
            continue
        try:
            verdict, n, d = resolve_one(h, arch)
        except PredicateSyntaxError:
            continue
        if n < int(h.get("min_samples", 1)):
            continue
        _append_line({"id": h["id"], "verdict": verdict, "n_samples": n,
                      "effect_size": d, "resolved_cycle": cycle_id})
        resolved.append(h["id"])
    return resolved
