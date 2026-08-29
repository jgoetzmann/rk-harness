"""Findings site generator — SPEC §Surface/sitegen.py, HANDOFF §17.

Pure HTML, no JavaScript, no timestamps: the same ArchiveState always produces
byte-identical files. Every page is checked against BANNED_WORDS before any file
is written.
"""
from __future__ import annotations

import html
import json
import re
from fractions import Fraction
from pathlib import Path

from rk_harness import costmodel
from rk_harness import ledger
from rk_harness import tableau as tableau_mod
from rk_harness.paths import work_dir
from rk_harness.types import ArchiveState, Record

BANNED_WORDS = ("novel", "first", "beats", "outperforms", "breakthrough", "proves",
                "state-of-the-art", "best-ever")
BANNER = "Automatically generated. Not reviewed by a human. See rk-overview for interpretation."
AVR_NOTE = "Cost model approximate; see HANDOFF §4.5."

_EXHAUSTIVE_LABEL = "exhaustive — optimal within the enumerated space"
_SEARCH_LABEL = "search result"

_BANNED_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in BANNED_WORDS) + r")\b",
    re.IGNORECASE,
)

_STYLE = """
body{font-family:monospace;max-width:1100px;margin:1em auto;padding:0 1em;color:#111;background:#fff}
p.banner{border:2px solid #b00;background:#fff3f3;padding:.5em;font-weight:bold}
table{border-collapse:collapse;margin:.5em 0}
th,td{border:1px solid #999;padding:.2em .5em;text-align:left;vertical-align:top}
th{background:#eee}
.tier{padding:0 .3em;border:1px solid #333;border-radius:.2em}
.tier-heldout_verified{background:#cfc}
.tier-search_only{background:#ffc}
.tier-unreplicated{background:#ddd}
.hash{word-break:break-all}
.note{font-style:italic;color:#444}
nav a{margin-right:1em}
"""


class BannedWordError(Exception):
    pass


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------

def _esc(x) -> str:
    return html.escape(str(x), quote=True)


def _frac(x) -> str:
    if isinstance(x, Fraction):
        return f"{x.numerator}/{x.denominator}"
    f = Fraction(x)
    return f"{f.numerator}/{f.denominator}"


def _num(v) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v != v:
            return "nan"
        if v in (float("inf"), float("-inf")):
            return "inf" if v > 0 else "-inf"
        return f"{v:.6g}"
    if isinstance(v, Fraction):
        return _frac(v)
    return _esc(v)


def _tier_badge(tier: str) -> str:
    return f'<span class="tier tier-{_esc(tier)}">{_esc(tier)}</span>'


def _phase_label(rec: Record) -> str:
    did = rec.directive_id
    if isinstance(did, str) and did.startswith("D-E"):
        return _EXHAUSTIVE_LABEL
    return _SEARCH_LABEL


def _cell_file(order: int, stages: int, bucket: int) -> str:
    return f"cell-p{order}-s{stages}-b{bucket}.html"


def _nav() -> str:
    return ('<nav><a href="index.html">index</a><a href="hypotheses.html">hypotheses</a>'
            '<a href="costmodel.html">cost model</a><a href="falsification.html">falsification</a></nav>')


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{_esc(title)}</title>\n"
        f"<style>{_STYLE}</style>\n</head>\n<body>\n"
        f'<p class="banner">{_esc(BANNER)}</p>\n'
        f"{_nav()}\n"
        f"<h1>{_esc(title)}</h1>\n"
        f"{body}\n"
        "</body>\n</html>\n"
    )


def _tableau_table(rec: Record) -> str:
    t = rec.tableau
    s = len(t.b)
    rows = []
    for i in range(s):
        cells = "".join(f"<td>{_frac(x)}</td>" for x in t.A[i])
        rows.append(f"<tr><th>c[{i}] = {_frac(t.c[i])}</th>{cells}</tr>")
    bcells = "".join(f"<td>{_frac(x)}</td>" for x in t.b)
    rows.append(f"<tr><th>b</th>{bcells}</tr>")
    head = "<tr><th></th>" + "".join(f"<th>A[.][{j}]</th>" for j in range(s)) + "</tr>"
    return f"<table>\n{head}\n" + "\n".join(rows) + "\n</table>"


def _record_meta(rec: Record) -> str:
    items = [
        ("tier", _tier_badge(rec.tier)),
        ("phase label", _esc(_phase_label(rec))),
        ("tableau_hash", f'<span class="hash">{_esc(rec.tableau_hash)}</span>'),
        ("verifier_hash", f'<span class="hash">{_esc(rec.verifier_hash)}</span>'),
        ("cycle_id", _num(rec.cycle_id)),
        ("seed", _num(rec.seed)),
        ("directive_id", _esc(rec.directive_id) if rec.directive_id is not None else "none"),
        ("hypothesis_id", _esc(rec.hypothesis_id) if rec.hypothesis_id is not None else "none"),
        ("recorded", _esc(rec.timestamp)),
    ]
    return "<dl>\n" + "\n".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in items) + "\n</dl>"


# ----------------------------------------------------------------------------
# pages
# ----------------------------------------------------------------------------

def render_index(arch: ArchiveState) -> str:
    parts = []
    parts.append("<h2>Archive</h2><dl>")
    parts.append(f"<dt>records</dt><dd>{_num(arch.n_records)}</dd>")
    parts.append(f"<dt>last cycle</dt><dd>{_num(arch.last_cycle_id)}</dd>")
    parts.append(f"<dt>open hypotheses</dt><dd>{len(arch.open_hypotheses)}</dd>")
    parts.append(f"<dt>refuted hypotheses</dt><dd>{len(arch.refuted_hypotheses)}</dd>")
    parts.append("</dl>")
    parts.append('<p class="note">Fitness is heldout_error under m0plus_fast at equal cycle budget; '
                 "lower is better and nothing more is claimed. Cells are (stages, cycle bucket).</p>")
    for order in sorted(arch.grids.keys()):
        grid = arch.grids[order]
        parts.append(f"<h2>Order {order} grid</h2>")
        if not grid:
            parts.append("<p>no elites yet</p>")
            continue
        parts.append("<table>\n<tr><th>stages</th><th>bucket</th><th>tableau_hash</th><th>tier</th>"
                     "<th>heldout_error</th><th>search_error</th><th>cycles fast</th><th>cycles slow</th>"
                     "<th>measured_order</th><th>label</th><th>verifier_hash</th></tr>")
        for (stg, bucket) in sorted(grid.keys()):
            rec = grid[(stg, bucket)]
            link = _cell_file(order, stg, bucket)
            sv = rec.score
            parts.append(
                "<tr>"
                f"<td>{stg}</td><td>{bucket}</td>"
                f'<td class="hash"><a href="{link}">{_esc(rec.tableau_hash)}</a></td>'
                f"<td>{_tier_badge(rec.tier)}</td>"
                f"<td>{_num(sv.heldout_error)}</td><td>{_num(sv.search_error)}</td>"
                f"<td>{_num(sv.cycles.get('m0plus_fast'))}</td><td>{_num(sv.cycles.get('m0plus_slow'))}</td>"
                f"<td>{_num(sv.measured_order)}</td>"
                f"<td>{_esc(_phase_label(rec))}</td>"
                f'<td class="hash">{_esc(rec.verifier_hash)}</td>'
                "</tr>"
            )
        parts.append("</table>")
    return _page("rk-harness findings", "\n".join(parts))


def render_cell(order: int, stages: int, bucket: int, rec: Record) -> str:
    sv = rec.score
    parts = []
    parts.append(f"<p>grid order {order}, {stages} stages, cycle bucket {bucket}</p>")
    parts.append(_record_meta(rec))
    parts.append("<h2>Tableau</h2>")
    parts.append(_tableau_table(rec))
    parts.append("<h2>Cycle counts (n_states = 1)</h2>")
    parts.append("<table><tr><th>model</th><th>cycles</th><th>note</th></tr>")
    for name in ("m0plus_fast", "m0plus_slow", "avr_approx"):
        note = _esc(AVR_NOTE) if name == "avr_approx" else ""
        parts.append(f"<tr><td>{name}</td><td>{_num(sv.cycles.get(name))}</td><td>{note}</td></tr>")
    parts.append("</table>")
    parts.append("<h2>Score</h2>")
    score_rows = [
        ("measured_order", sv.measured_order),
        ("order_fit_points", sv.order_fit_points),
        ("error_constant", sv.error_constant),
        ("stability_real", sv.stability_real),
        ("stability_imag", sv.stability_imag),
        ("csd_weight_total", sv.csd_weight_total),
        ("coeff_quant_error", sv.coeff_quant_error),
        ("search_error", sv.search_error),
        ("heldout_error", sv.heldout_error),
        ("overflow_margin", sv.overflow_margin),
    ]
    parts.append("<table><tr><th>metric</th><th>value</th></tr>")
    for k, v in score_rows:
        parts.append(f"<tr><td>{k}</td><td>{_num(v)}</td></tr>")
    parts.append("</table>")
    parts.append("<h2>Per problem</h2>")
    parts.append("<table><tr><th>key</th><th>error</th><th>note</th></tr>")
    for k in sorted(sv.per_problem.keys()):
        note = _esc(AVR_NOTE) if str(k).startswith("avr_approx:") else ""
        parts.append(f"<tr><td>{_esc(k)}</td><td>{_num(sv.per_problem[k])}</td><td>{note}</td></tr>")
    parts.append("</table>")
    title = f"cell p{order} s{stages} b{bucket}"
    return _page(title, "\n".join(parts))


def render_hypotheses(hyps: list[dict]) -> str:
    parts = []
    if not hyps:
        parts.append("<p>no hypotheses recorded</p>")
    else:
        parts.append("<table><tr><th>id</th><th>predicate</th><th>verdict</th><th>n_samples</th>"
                     "<th>effect_size</th><th>min_samples</th><th>resolved_cycle</th><th>cycle</th>"
                     "<th>rationale</th></tr>")
        for h in sorted(hyps, key=lambda d: str(d.get("id", ""))):
            verdict = h.get("verdict")
            parts.append(
                "<tr>"
                f"<td>{_esc(h.get('id', ''))}</td>"
                f"<td>{_esc(h.get('predicate', ''))}</td>"
                f"<td>{_esc(verdict) if verdict is not None else 'open'}</td>"
                f"<td>{_num(h.get('n_samples'))}</td>"
                f"<td>{_num(h.get('effect_size'))}</td>"
                f"<td>{_num(h.get('min_samples'))}</td>"
                f"<td>{_num(h.get('resolved_cycle'))}</td>"
                f"<td>{_num(h.get('cycle'))}</td>"
                f"<td>{_esc(h.get('rationale', ''))}</td>"
                "</tr>"
            )
        parts.append("</table>")
    return _page("hypothesis ledger", "\n".join(parts))


def render_costmodel() -> str:
    classical = tableau_mod.classical()
    parts = []
    parts.append("<h2>Anchor: rk4 vs rk38</h2>")
    parts.append("<table><tr><th>tableau</th><th>n_states</th><th>m0plus_fast</th><th>m0plus_slow</th></tr>")
    for name in ("rk4", "rk38"):
        t = classical.get(name)
        if t is None:
            continue
        for n in (1, 2, 4):
            fast = costmodel.cycle_count(t, costmodel.M0PLUS_FAST, n)
            slow = costmodel.cycle_count(t, costmodel.M0PLUS_SLOW, n)
            parts.append(f"<tr><td>{name}</td><td>{n}</td><td>{fast}</td><td>{slow}</td></tr>")
    parts.append("</table>")
    parts.append("<h2>Classical tableaus (n_states = 1)</h2>")
    parts.append("<table><tr><th>tableau</th><th>stages</th><th>m0plus_fast</th><th>m0plus_slow</th>"
                 "<th>avr_approx</th><th>note</th></tr>")
    for name in sorted(classical.keys()):
        t = classical[name]
        fast = costmodel.cycle_count(t, costmodel.M0PLUS_FAST, 1)
        slow = costmodel.cycle_count(t, costmodel.M0PLUS_SLOW, 1)
        avr = costmodel.cycle_count(t, costmodel.AVR_APPROX, 1)
        parts.append(f"<tr><td>{name}</td><td>{len(t.b)}</td><td>{fast}</td><td>{slow}</td>"
                     f"<td>{avr}</td><td>{_esc(AVR_NOTE)}</td></tr>")
    parts.append("</table>")
    parts.append("<h2>Model parameters</h2>")
    parts.append("<table><tr><th>model</th><th>mul</th><th>add</th><th>shift</th><th>load</th><th>store</th></tr>")
    for m in (costmodel.M0PLUS_FAST, costmodel.M0PLUS_SLOW, costmodel.AVR_APPROX):
        cy = m.cycles
        parts.append(f"<tr><td>{m.name}</td><td>{cy.get('mul')}</td><td>{cy.get('add')}</td>"
                     f"<td>{cy.get('shift')}</td><td>{cy.get('load')}</td><td>{cy.get('store')}</td></tr>")
    parts.append("</table>")
    return _page("cost model comparison", "\n".join(parts))


def _render_value(v) -> str:
    if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
        keys: list[str] = []
        for row in v:
            for k in row.keys():
                if k not in keys:
                    keys.append(k)
        out = ["<table><tr>" + "".join(f"<th>{_esc(k)}</th>" for k in keys) + "</tr>"]
        for row in v:
            out.append("<tr>" + "".join(f"<td>{_render_value(row.get(k))}</td>" for k in keys) + "</tr>")
        out.append("</table>")
        return "\n".join(out)
    if isinstance(v, dict):
        out = ["<dl>"]
        for k in sorted(v.keys(), key=str):
            out.append(f"<dt>{_esc(k)}</dt><dd>{_render_value(v[k])}</dd>")
        out.append("</dl>")
        return "\n".join(out)
    if isinstance(v, list):
        return "[" + ", ".join(_render_value(x) for x in v) + "]"
    return _num(v)


def render_falsification(data: dict | None) -> str:
    if data is None:
        body = "<p>falsification experiment not run (work_dir()/falsification.json absent)</p>"
    else:
        verdict = data.get("verdict")
        parts = []
        if verdict is not None:
            parts.append(f"<p>verdict: <strong>{_esc(verdict)}</strong></p>")
        rest = {k: v for k, v in data.items() if k != "verdict"}
        parts.append(_render_value(rest))
        body = "\n".join(parts)
    return _page("falsification experiment", body)


# ----------------------------------------------------------------------------
# banned words + build
# ----------------------------------------------------------------------------

def check_banned(html_text: str) -> None:
    m = _BANNED_RE.search(html_text)
    if m is not None:
        raise BannedWordError(f"banned word {m.group(0)!r} at offset {m.start()}")


def _load_hypotheses() -> list[dict]:
    try:
        return list(ledger.load_hypotheses())
    except FileNotFoundError:
        return []


def _load_falsification() -> dict | None:
    path = work_dir() / "falsification.json"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def build(arch: ArchiveState, out_dir: Path) -> None:
    out_dir = Path(out_dir)
    pages: dict[str, str] = {}
    pages["index.html"] = render_index(arch)
    for order in sorted(arch.grids.keys()):
        grid = arch.grids[order]
        for (stg, bucket) in sorted(grid.keys()):
            rec = grid[(stg, bucket)]
            pages[_cell_file(order, stg, bucket)] = render_cell(order, stg, bucket, rec)
    pages["hypotheses.html"] = render_hypotheses(_load_hypotheses())
    pages["costmodel.html"] = render_costmodel()
    pages["falsification.html"] = render_falsification(_load_falsification())
    for name in sorted(pages.keys()):
        check_banned(pages[name])
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in sorted(pages.keys()):
        with open(out_dir / name, "wb") as fh:
            fh.write(pages[name].encode("utf-8"))
