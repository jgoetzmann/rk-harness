"""LLM directive validation (SPEC ### rk_harness/directive.py, HANDOFF §5).

jsonschema with ``additionalProperties: false`` at every object level, then the
HANDOFF §5 rules that the schema cannot express are applied by hand.
"""
from __future__ import annotations

import json
import re
from fractions import Fraction

import jsonschema

from rk_harness.types import ArchiveState


class DirectiveError(Exception):
    pass


_DIRECTIVE_ID_RE = re.compile(r"^D-[A-Za-z0-9]+$")
_HYPOTHESIS_ID_RE = re.compile(r"^H-[0-9]+$")
_STAGE_KEY_RE = re.compile(r"^[0-9]+$")

_TARGET_ORDER_BY_PHASE = {0: 2, 1: 3, 2: 4, 3: 4}

DIRECTIVE_SCHEMA: dict = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "directive_id",
        "target_order",
        "stages",
        "constraints",
        "islands",
        "budget_minutes",
        "rationale",
    ],
    "properties": {
        "directive_id": {"type": "string", "pattern": "^D-[A-Za-z0-9]+$"},
        "hypothesis_id": {"type": ["string", "null"], "pattern": "^H-[0-9]+$"},
        "target_order": {"type": "integer", "enum": [1, 2, 3, 4]},
        "stages": {
            "type": "array",
            "minItems": 1,
            "maxItems": 3,
            "items": {"type": "integer", "minimum": 2, "maximum": 6},
        },
        "constraints": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "force_zero": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 2,
                        "items": {"type": "integer", "minimum": 0},
                    },
                },
                "dyadic_denominator_max": {"type": "integer", "minimum": 2, "maximum": 32768},
                "c_fixed": {
                    "type": "object",
                    "additionalProperties": False,
                    "patternProperties": {"^[0-9]+$": {"type": ["string", "integer"]}},
                },
                "b_nonneg": {"type": "boolean"},
            },
        },
        "islands": {"type": "integer", "minimum": 1, "maximum": 8},
        "budget_minutes": {"type": "integer", "minimum": 5, "maximum": 120},
        "rationale": {"type": "string", "maxLength": 500},
    },
}


def _is_int(x) -> bool:
    return type(x) is int


def _is_pow2(n: int) -> bool:
    return n >= 1 and (n & (n - 1)) == 0


def validate_directive(d: dict) -> dict:
    if not isinstance(d, dict):
        raise DirectiveError(f"directive must be a JSON object, got {type(d).__name__}")
    try:
        jsonschema.validate(instance=d, schema=DIRECTIVE_SCHEMA)
    except jsonschema.ValidationError as e:
        raise DirectiveError(f"schema: {e.message}") from None
    except jsonschema.SchemaError as e:
        raise DirectiveError(f"schema definition error: {e}") from None

    if not _DIRECTIVE_ID_RE.match(d["directive_id"]):
        raise DirectiveError("directive_id must match ^D-[A-Za-z0-9]+$")
    hid = d.get("hypothesis_id")
    if hid is not None and not (isinstance(hid, str) and _HYPOTHESIS_ID_RE.match(hid)):
        raise DirectiveError("hypothesis_id must match ^H-[0-9]+$ or be null")

    if not _is_int(d["target_order"]) or d["target_order"] not in (1, 2, 3, 4):
        raise DirectiveError("target_order must be an int in {1,2,3,4}")

    stages = d["stages"]
    if not isinstance(stages, list) or not (1 <= len(stages) <= 3):
        raise DirectiveError("stages must be a list of length 1..3")
    for s in stages:
        if not _is_int(s) or not (2 <= s <= 6):
            raise DirectiveError("every stage count must be an int in [2,6]")
    max_stages = max(stages)

    cons = d["constraints"]
    if not isinstance(cons, dict):
        raise DirectiveError("constraints must be an object")
    for key in cons:
        if key not in ("force_zero", "dyadic_denominator_max", "c_fixed", "b_nonneg"):
            raise DirectiveError(f"unknown constraint key {key!r}")

    for entry in cons.get("force_zero", []):
        if not isinstance(entry, list) or len(entry) != 2:
            raise DirectiveError("force_zero entries must be [i, j]")
        i, j = entry
        if not (_is_int(i) and _is_int(j)):
            raise DirectiveError("force_zero indices must be ints")
        if not (0 <= j < i < max_stages):
            raise DirectiveError(f"force_zero [{i}, {j}] must satisfy 0 <= j < i < max(stages)={max_stages}")

    if "dyadic_denominator_max" in cons:
        dd = cons["dyadic_denominator_max"]
        if not _is_int(dd) or not (2 <= dd <= 32768) or not _is_pow2(dd):
            raise DirectiveError("dyadic_denominator_max must be a power of two in [2, 32768]")

    if "c_fixed" in cons:
        cf = cons["c_fixed"]
        if not isinstance(cf, dict):
            raise DirectiveError("c_fixed must be an object")
        for key, val in cf.items():
            if not isinstance(key, str) or not _STAGE_KEY_RE.match(key):
                raise DirectiveError(f"c_fixed key {key!r} must be a decimal stage index")
            idx = int(key)
            if not (1 <= idx <= max_stages - 1):
                raise DirectiveError(f"c_fixed key {key!r} must be in [1, max(stages)-1]")
            if isinstance(val, bool):
                raise DirectiveError(f"c_fixed value for {key!r} must parse as Fraction")
            try:
                Fraction(str(val))
            except (ValueError, ZeroDivisionError, TypeError):
                raise DirectiveError(f"c_fixed value {val!r} must parse as Fraction") from None

    if "b_nonneg" in cons and not isinstance(cons["b_nonneg"], bool):
        raise DirectiveError("b_nonneg must be a bool")

    if not _is_int(d["islands"]) or not (1 <= d["islands"] <= 8):
        raise DirectiveError("islands must be an int in [1,8]")
    if not _is_int(d["budget_minutes"]) or not (5 <= d["budget_minutes"] <= 120):
        raise DirectiveError("budget_minutes must be an int in [5,120]")
    if not isinstance(d["rationale"], str) or len(d["rationale"]) > 500:
        raise DirectiveError("rationale must be a string of at most 500 chars")
    return d


def _first_json_object(text: str):
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = decoder.raw_decode(text, start)
            return obj
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
    raise DirectiveError("no JSON object found in text")


def parse_directive(text: str) -> dict:
    if not isinstance(text, str):
        raise DirectiveError("directive text must be a string")
    obj = _first_json_object(text)
    if not isinstance(obj, dict):
        raise DirectiveError("directive must be a JSON object")
    return validate_directive(obj)


def _heldout_of(rec) -> float:
    try:
        v = float(rec.score.heldout_error)
    except Exception:
        return float("inf")
    return v if v == v else float("inf")


def _emptiest_cell(arch: ArchiveState, order: int) -> tuple[int, int]:
    grid = arch.grids.get(order, {}) if arch is not None and arch.grids else {}
    for stages in range(2, 7):
        for bucket in range(8):
            if (stages, bucket) not in grid:
                return (stages, bucket)
    best_key = (2, 0)
    best_val = float("-inf")
    for stages in range(2, 7):
        for bucket in range(8):
            v = _heldout_of(grid[(stages, bucket)])
            if v > best_val:
                best_val = v
                best_key = (stages, bucket)
    return best_key


def fallback_directive(arch: ArchiveState, phase: int, cycle_id: int) -> dict:
    order = _TARGET_ORDER_BY_PHASE.get(int(phase), 4)
    stages, _bucket = _emptiest_cell(arch, order)
    d = {
        "directive_id": f"D-F{int(cycle_id):05d}",
        "hypothesis_id": None,
        "target_order": order,
        "stages": [stages],
        "constraints": {
            "force_zero": [],
            "dyadic_denominator_max": 32768,
            "c_fixed": {},
            "b_nonneg": False,
        },
        "islands": 4,
        "budget_minutes": 5,
        "rationale": "fallback: emptiest cell",
    }
    return validate_directive(d)
