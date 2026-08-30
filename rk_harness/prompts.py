"""Prompt construction for the optional LLM directive call
(SPEC ### rk_harness/prompts.py, HANDOFF §5, §6).

This file deliberately never mentions any archive tier name (test K8): the model
must not be able to see or reason about tiers, which are assigned by code only.
"""
from __future__ import annotations

import json
import math

from rk_harness.types import ArchiveState, RunState

_DIRECTIVE_EXAMPLE = {
    "directive_id": "D-0112",
    "hypothesis_id": "H-047",
    "target_order": 3,
    "stages": [3, 4],
    "constraints": {
        "force_zero": [[2, 0]],
        "dyadic_denominator_max": 16,
        "c_fixed": {"1": "1/2"},
        "b_nonneg": True,
    },
    "islands": 4,
    "budget_minutes": 45,
    "rationale": "cell (3, bucket 4) empty; forcing a[2][0]=0 removes one multiply",
}

_DIRECTIVE_RULES = """Directive rules (every one is checked by code; a violation discards the directive):
- directive_id: string matching ^D-[A-Za-z0-9]+$ (required)
- hypothesis_id: string matching ^H-[0-9]+$, or null (optional)
- target_order: one of 1, 2, 3, 4 (required)
- stages: list of 1 to 3 integers, each in [2, 6] (required)
- constraints: object with only these optional keys (required, may be {}):
    force_zero: list of [i, j] with 0 <= j < i < max(stages)  (sets A[i][j] = 0)
    dyadic_denominator_max: a power of two in [2, 32768]      (A entries snap to k / this)
    c_fixed: object mapping stage index strings "1".."max(stages)-1" to fractions like "1/2"
    b_nonneg: true or false
- islands: integer in [1, 8] (required)
- budget_minutes: integer in [5, 120] (required)
- rationale: string of at most 500 characters (required)
- Any key not listed above, at any level, rejects the whole directive.
- The directive can only narrow the search. It cannot change the objective, the
  problems, the cost models, or how results are graded."""

SYSTEM_PROMPT = (
    "You direct a deterministic search for explicit Runge-Kutta tableaus that run in "
    "Q15 fixed point on Cortex-M0+ class microcontrollers at an equal cycle budget. "
    "Coefficients are exact rationals; A entries snap to dyadic rationals and b is "
    "solved exactly from the order conditions. The archive is a MAP-Elites grid per "
    "order (1..4) indexed by (stages, cycle bucket); each cell keeps the tableau with "
    "the lowest held-out error. You never see raw code and you cannot change the "
    "evaluator, the cost model, the problem sets, or the grading rules. Your only "
    "output is one search directive.\n\n"
    "Hypotheses are resolved by code from archive statistics, never by you. A "
    "hypothesis that the ledger has marked refuted must not be re-proposed in other "
    "words; propose something the refutation does not already cover.\n\n"
    + _DIRECTIVE_RULES
    + "\n\nExample of a valid directive:\n"
    + json.dumps(_DIRECTIVE_EXAMPLE, indent=2)
    + "\n\nReturn exactly one JSON object and nothing else: no prose, no code fences, "
    "no commentary before or after it."
)


def _fmt(x) -> str:
    if x is None:
        return "n/a"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    if math.isinf(v):
        return "inf"
    if v != v:
        return "nan"
    return f"{v:.3e}"


def _grid_section(arch: ArchiveState) -> list[str]:
    lines = ["## Grid occupancy (order -> cells, best held-out error per cell)"]
    grids = arch.grids if arch is not None and arch.grids else {}
    for order in (1, 2, 3, 4):
        grid = grids.get(order, {}) or {}
        lines.append(f"order {order}: {len(grid)} of 40 cells filled")
        for stages in range(2, 7):
            row = []
            for bucket in range(8):
                rec = grid.get((stages, bucket))
                if rec is None:
                    row.append(f"b{bucket}:empty")
                else:
                    try:
                        he = rec.score.heldout_error
                        se = rec.score.search_error
                        cyc = rec.score.cycles.get("m0plus_fast")
                    except Exception:
                        he, se, cyc = None, None, None
                    row.append(f"b{bucket}:heldout={_fmt(he)},search={_fmt(se)},cycles={cyc}")
            lines.append(f"  stages {stages}: " + " | ".join(row))
        empties = [(s, b) for s in range(2, 7) for b in range(8) if (s, b) not in grid]
        if empties:
            lines.append("  empty cells (stages, bucket): " + ", ".join(f"({s},{b})" for s, b in empties[:40]))
    return lines


def _cell_stats_section(arch: ArchiveState) -> list[str]:
    stats = getattr(arch, "cell_stats", None) or {}
    if not stats:
        return ["## Cell statistics", "none yet"]
    lines = ["## Cell statistics ((order, stages) -> fast held-out: n, mean, min)"]
    for key in sorted(stats):
        cs = stats[key].get("fast.heldout")
        if cs is None:
            continue
        lines.append(f"  order {key[0]} stages {key[1]}: n={cs.n} mean={_fmt(cs.mean)} min={_fmt(cs.min)}")
    if len(lines) == 1:
        lines.append("  none yet")
    return lines


def _hyp_line(h: dict) -> str:
    hid = h.get("id", "?")
    stmt = str(h.get("statement", "")).strip()
    pred = str(h.get("predicate", "")).strip()
    verdict = h.get("verdict")
    n = h.get("n_samples")
    d = h.get("effect_size")
    cyc = h.get("resolved_cycle")
    parts = [f"{hid}: {stmt}", f"predicate: {pred}"]
    if verdict is not None:
        parts.append(f"verdict: {verdict}")
    if n is not None:
        parts.append(f"n_samples: {n}")
    if d is not None:
        parts.append(f"effect_size (Cohen's d): {_fmt(d)}")
    if cyc is not None:
        parts.append(f"resolved at cycle {cyc}")
    return "  - " + "; ".join(parts)


def _state_section(state: RunState) -> list[str]:
    cell = state.current_cell
    return [
        "## Run state",
        f"cycle_id: {state.cycle_id}",
        f"phase: {state.phase}",
        f"stall_counter: {state.stall_counter}",
        f"current_cell (stages, bucket): {tuple(cell) if cell is not None else 'none'}",
        f"llm_spend_usd: {state.spend_usd:.4f}",
        f"started_at: {state.started_at}",
    ]


def build_user_prompt(arch: ArchiveState, state: RunState, refuted: list[dict], open_h: list[dict], literature: str = "") -> str:
    lines: list[str] = []
    lines.append("# Search directive request")
    lines.extend(_state_section(state))
    n_rec = arch.n_records if arch is not None else 0
    last = arch.last_cycle_id if arch is not None else 0
    lines.append(f"archive: {n_rec} records, last cycle {last}")
    lines.append("")
    lines.extend(_grid_section(arch))
    lines.append("")
    lines.extend(_cell_stats_section(arch))
    lines.append("")
    refuted = list(refuted or [])
    lines.append(f"## Refuted hypotheses ({len(refuted)}) - verdict refuted by code; do not re-propose these")
    if refuted:
        lines.extend(_hyp_line(h) for h in refuted)
    else:
        lines.append("  none refuted yet")
    lines.append("")
    open_h = list(open_h or [])
    lines.append(f"## Open hypotheses ({len(open_h)}) - awaiting samples")
    if open_h:
        lines.extend(_hyp_line(h) for h in open_h)
    else:
        lines.append("  none open")
    lines.append("")
    lines.append("## Directive format")
    lines.append(_DIRECTIVE_RULES)
    lines.append("Example:")
    lines.append(json.dumps(_DIRECTIVE_EXAMPLE, indent=2))
    lines.append("")
    lines.append(
        "Choose a target_order and stage counts that fill an empty or weak cell, or that test an "
        "open hypothesis. Reference an open hypothesis id in hypothesis_id when the search is "
        "meant to gather samples for it; otherwise set it to null."
    )
    lines.extend(_literature_section(literature))
    lines.append("Return exactly one JSON object and nothing else.")
    return "\n".join(lines)


HYPOTHESIS_SYSTEM_PROMPT = (
    "You are the hypothesis writer for a fixed-point Runge-Kutta search. Return exactly one JSON "
    "object and nothing else, with exactly these keys: statement (one falsifiable sentence about "
    "which archive cell beats which under which cost model), mechanism (one sentence: why), "
    "control (one sentence: what should happen under the other cost model if the mechanism is "
    "real), predicate (the machine-checkable form), min_samples (int >= 20). The predicate "
    "grammar is: field op field | field op number, joined by AND/OR; field = "
    "(fast|slow|avr_approx).p<digit>s<digit>.(heldout|search|cycles|order). Nothing else parses. "
    "Do not include an id, a cycle number, or any verdict field: ids and verdicts are assigned "
    "by the harness, never by you. Do not restate a hypothesis listed as already proposed."
)


def build_hypothesis_prompt(arch: ArchiveState, state: RunState, refuted: list[dict], open_h: list[dict], literature: str = "") -> str:
    lines = ["## Task", "Propose ONE new falsifiable hypothesis about the archive, as a single JSON object.", ""]
    lines.extend(_grid_section(arch))
    lines.append("")
    lines.append(f"## Already proposed - do not repeat these ({len(refuted) + len(open_h)})")
    for h in list(open_h) + list(refuted):
        lines.append(_hyp_line(h))
    if not (open_h or refuted):
        lines.append("  none yet")
    lines.extend(_literature_section(literature))
    return "\n".join(lines)


def _literature_section(literature: str) -> list[str]:
    if not literature:
        return []
    return ["", "## Literature digest (web-researched; background for your reasoning)", literature]


LITERATURE_SYSTEM_PROMPT = (
    "You are the literature scout for a search over fixed-point Runge-Kutta methods on "
    "Cortex-M0+ (Q15, fixed cycle budget, floor rounding). Use the web search tool to find "
    "current, real sources on the requested topic. Return exactly one JSON object with keys: "
    "topic (short), summary (2-3 plain-text paragraphs of what the literature says and how it "
    "bears on this search), key_points (list of short strings), sources (list of {title, url} "
    "you actually consulted). Facts only; no priority claims about our own project. Do not "
    "repeat digests listed as already collected."
)


def build_literature_prompt(topic: str, state: RunState, open_h: list[dict], digests: list[dict]) -> str:
    lines = ["## Research topic", topic, "",
             f"## Context: the run is in phase {state.phase}, cycle {state.cycle_id}.",
             "Open hypotheses the digest should help sharpen or challenge:"]
    for h in open_h[-5:]:
        lines.append(f"  {h.get('id')}: {h.get('statement')}")
    if not open_h:
        lines.append("  none open")
    lines.append("")
    lines.append(f"## Already collected ({len(digests)}) - do not repeat")
    for d in digests[-8:]:
        lines.append(f"  [{d.get('ts', '')}] {d.get('topic', '')}")
    if not digests:
        lines.append("  none yet")
    return "\n".join(lines)


INTERPRET_SYSTEM_PROMPT = (
    "You write the analysis page of an auto-generated findings site for a fixed-point "
    "Runge-Kutta search. Write 3-5 plain-text paragraphs interpreting the data you are given: "
    "what the archive shows, which mechanisms explain it, how it relates to the literature "
    "digest, what is still uncertain, and what is worth examining next. Plain prose, no "
    "markdown, no lists, no headings. Never claim priority or importance for this project "
    "(avoid words like novel, breakthrough, or claims of being ahead of others); verdicts on "
    "hypotheses are computed by the harness, so describe them as observations, not decisions."
)


def build_interpretation_prompt(arch: ArchiveState, state: RunState, refuted: list[dict],
                                open_h: list[dict], literature: str = "", extra: str = "") -> str:
    lines = ["# Interpretation request", ""]
    lines.extend(_state_section(state))
    lines.extend(_grid_section(arch))
    lines.append("")
    lines.append(f"## Hypotheses: open {len(open_h)}, refuted {len(refuted)}")
    for h in (open_h + refuted)[-8:]:
        lines.append(_hyp_line(h))
    if extra:
        lines.extend(["", "## Fixed reference results", extra])
    lines.extend(_literature_section(literature))
    return "\n".join(lines)
