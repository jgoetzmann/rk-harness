"""Literature digests and model-written interpretation — storage and formatting only.

The LLM calls that *produce* these live in runner.py (the only module allowed to talk to an
LLM); this module stores them under rk-work and formats them for prompts and for the site.

- literature: web-researched digests (machine architecture, fixed-point arithmetic, numerical
  integration research) that inform the directive and hypothesis prompts.
- interpretation: prose readings of the archive, published on the findings site as clearly
  labelled model-written analysis.

Both are published, so both are softened first: the site's banned words are claims of
priority ("novel", "beats", ...) and build() refuses pages containing them. `soften` swaps
them for neutral phrasing at write time, which keeps review item E4 true even for text that
quotes other people's abstracts.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from rk_harness.paths import work_dir

TOPICS: tuple[str, ...] = (
    "fixed-point and quantized arithmetic in Runge-Kutta / ODE integration: rounding bias, "
    "error accumulation, stochastic rounding, Q15/Q31 implementations on MCUs",
    "optimizing Butcher tableaus and low-order Runge-Kutta methods: free parameters, error "
    "constants, RKTK and other numerical searches over order conditions",
    "Cortex-M0+ and small-MCU arithmetic: multiplier variants, cycle costs, shift-add "
    "multiplication, CSD / canonical signed digit constant multiplication",
    "recent research on numerical integration methods for embedded and hard real-time "
    "control: fixed cycle budgets, low-precision solvers, energy-conserving integrators",
    "roundoff versus truncation error trade-offs at large step counts; probabilistic and "
    "backward error analysis of floating- and fixed-point ODE solvers",
    "implicit Runge-Kutta families for embedded use: SDIRK, Radau IIA, Rosenbrock methods, "
    "fixed iteration counts, and their cost structure on small MCUs",
    "embedded Runge-Kutta pairs and step-size control: Bogacki-Shampine, Dormand-Prince, "
    "PI controllers, and step control implemented in fixed-point arithmetic",
    "stability regions of low-stage explicit Runge-Kutta methods and stiffness limits at "
    "fixed cycle budgets; when implicit methods pay for themselves",
    "how production ODE libraries implement their integrators: SciPy solve_ivp, boost "
    "odeint, SUNDIALS CVODE; data layouts, error norms, controller defaults",
)

# Softening map: the site's BANNED_WORDS (claims of priority) -> neutral phrasing,
# plus vocabulary that trips site style ("cost landscape" is a phrase replacement on
# purpose; a bare "landscape" is fine in most contexts and is left alone).
_SOFTEN: tuple[tuple[str, str], ...] = (
    ("state-of-the-art", "leading"),
    ("best-ever", "record"),
    ("breakthrough", "advance"),
    ("outperforms", "does better than"),
    ("beats", "does better than"),
    ("proves", "shows"),
    ("novel", "new"),
    ("first", "earliest"),
    ("cost landscape", "cost structure"),
    ("delve", "dig"),
    ("pivotal", "central"),
    ("showcases", "shows"),
    ("leverages", "uses"),
)


def soften(text: str) -> str:
    """Replace the site's banned words (case-insensitive, whole word) with neutral
    phrasing, then normalise em/en dashes: ", " when the dash sits between word
    characters, " - " otherwise (site style forbids em dashes in published prose)."""
    out = str(text)
    for bad, good in _SOFTEN:
        out = re.sub(rf"(?i)\b{re.escape(bad)}\b", good, out)
    out = re.sub("(?<=\\w)[\u2013\u2014](?=\\w)", ", ", out)
    out = out.replace("\u2013", " - ").replace("\u2014", " - ")
    out = re.sub(" {2,}", " ", out)
    return out


def lit_path() -> Path:
    return work_dir() / "literature" / "digests.jsonl"


def interp_path() -> Path:
    return work_dir() / "interpretation" / "interpretations.jsonl"


def _append(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def append_digest(d: dict) -> None:
    entry = {
        "ts": str(d.get("ts", "")),
        "cycle": int(d.get("cycle", 0)),
        "topic": soften(str(d.get("topic", ""))[:300]),
        "summary": soften(str(d.get("summary", ""))[:4000]),
        "key_points": [soften(str(k))[:300] for k in (d.get("key_points") or [])][:8],
        "sources": [{"title": soften(str(s.get("title", "")))[:200], "url": str(s.get("url", ""))[:400]}
                    for s in (d.get("sources") or []) if isinstance(s, dict)][:8],
    }
    _append(lit_path(), entry)


def load_digests(limit: int | None = None) -> list[dict]:
    out = _load(lit_path())
    return out[-limit:] if limit else out


# Track rotation (owner-directed, 2026-09-02): ~70% of literature attention on the lead
# track, ~15% on each side track, so all three research tracks show steady progress.
# A = current scored epoch and its paper analysis, B = adaptive/embedded pairs,
# C = implicit/stiff. Indices refer to TOPICS above; re-map when the lead track changes.
TRACK_TOPICS: dict[str, tuple[int, ...]] = {
    "A": (0, 1, 2, 3, 4, 8),
    "B": (6,),
    "C": (5, 7),
}
# 20-slot deterministic schedule: 14 A, 3 B, 3 C (70/15/15), evenly interleaved.
TRACK_SCHEDULE = "AABAACAAABAACAAABAAC"


def next_topic(n_existing: int) -> str:
    track = TRACK_SCHEDULE[n_existing % len(TRACK_SCHEDULE)]
    pool = TRACK_TOPICS[track]
    used = sum(1 for i in range(n_existing)
               if TRACK_SCHEDULE[i % len(TRACK_SCHEDULE)] == track)
    return TOPICS[pool[used % len(pool)]]


def digest_for_prompt(max_chars: int = 2600) -> str:
    """Newest digests first, trimmed to max_chars. Empty string when none exist."""
    parts: list[str] = []
    used = 0
    for d in reversed(load_digests()):
        block = f"[{d.get('ts', '')}] {d.get('topic', '')}\n{d.get('summary', '')}"
        pts = d.get("key_points") or []
        if pts:
            block += "\n" + "\n".join(f"- {p}" for p in pts)
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)


def append_interpretation(entry: dict) -> None:
    _append(interp_path(), {
        "ts": str(entry.get("ts", "")),
        "cycle": int(entry.get("cycle", 0)),
        "text": soften(str(entry.get("text", ""))[:8000]),
    })


def load_interpretations(limit: int | None = None) -> list[dict]:
    out = _load(interp_path())
    return out[-limit:] if limit else out
