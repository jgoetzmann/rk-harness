"""Findings site generator — SPEC §Surface/sitegen.py, HANDOFF §17.

Pure HTML + inline SVG, no JavaScript, no wall-clock reads: the same ArchiveState always
produces byte-identical files. Stored UTC timestamps are displayed in US Central via
rk_harness.timefmt (a pure conversion of stored data); still no wall-clock reads. Every
page is checked against BANNED_WORDS before any file is written.

Charts follow the dataviz method: form first, colors by job (categorical slots 1-3 of the
validated reference palette, sequential blue ramp for magnitude), status colors reserved for
tiers/verdicts and never color-alone, direct labels + tables as the relief for low-contrast
marks, native SVG <title> as the static hover layer, and a selected dark mode via CSS custom
properties (prefers-color-scheme) rather than an automatic flip.
"""
from __future__ import annotations

import html
import json
import math
import re
from fractions import Fraction
from pathlib import Path

from rk_harness import literature as literature_mod
from rk_harness import coeffrep
from rk_harness import costmodel
from rk_harness import ledger
from rk_harness import tableau as tableau_mod
from rk_harness import timefmt
from rk_harness.paths import work_dir
from rk_harness.types import ArchiveState, Record

BANNED_WORDS = ("novel", "first", "beats", "outperforms", "breakthrough", "proves",
                "state-of-the-art", "best-ever")
BANNER = "Automatically generated. Not reviewed by a human. See rk-overview for interpretation."
AVR_NOTE = "Cost model approximate; see HANDOFF §4.5."

_EXHAUSTIVE_LABEL = "exhaustive — optimal within the enumerated space"
_SEARCH_LABEL = "search result"
_MODEL_NOTE = ("Model-written text. Sources are model-collected citations; verify them before "
               "relying on them. See rk-overview for the human view.")

_BANNED_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in BANNED_WORDS) + r")\b",
    re.IGNORECASE,
)

# Reference palette (validated: slots 1-3 pass all-pairs light+dark; aqua's light-surface
# contrast WARN is relieved by direct labels + table views, present on every chart page).
_STYLE = """
:root{
  color-scheme:light;
  --surface-0:#f5f4f2;--surface-1:#fcfcfb;--line:#dedcd6;--grid:#eceae5;
  --text-1:#0b0b0b;--text-2:#52514e;--text-3:#8a887f;
  --s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;
  --good-bg:#e0f2e3;--good-fg:#0d5c1d;--warn-bg:#fdf0d3;--warn-fg:#7a5300;
  --mut-bg:#eceae5;--mut-fg:#52514e;--bad-bg:#fbe3e3;--bad-fg:#8f1d1d;
  --q1:#cde2fb;--q2:#9ec5f4;--q3:#6da7ec;--q4:#3987e5;--q5:#256abf;--q6:#184f95;--q7:#0d366b;
  --banner-bg:#fdf0d3;--banner-line:#c98500;
}
@media (prefers-color-scheme: dark){
  :root{
    color-scheme:dark;
    --surface-0:#111110;--surface-1:#1a1a19;--line:#3a3936;--grid:#262523;
    --text-1:#ffffff;--text-2:#c3c2b7;--text-3:#8a887f;
    --s1:#3987e5;--s2:#d95926;--s3:#199e70;
    --good-bg:#123a1c;--good-fg:#9fdca9;--warn-bg:#42350b;--warn-fg:#ecc76a;
    --mut-bg:#262523;--mut-fg:#c3c2b7;--bad-bg:#461414;--bad-fg:#f1a5a5;
    --q1:#0d366b;--q2:#184f95;--q3:#256abf;--q4:#3987e5;--q5:#6da7ec;--q6:#9ec5f4;--q7:#cde2fb;
    --banner-bg:#42350b;--banner-line:#ecc76a;
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--surface-0);color:var(--text-1);
  font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:0 20px 48px}
header.site{background:var(--surface-1);border-bottom:1px solid var(--line);margin-bottom:24px}
header.site .wrap{padding-top:18px;padding-bottom:0}
h1{font-size:24px;margin:6px 0 2px;letter-spacing:-.01em}
h2{font-size:17px;margin:32px 0 10px;letter-spacing:-.01em}
h3{font-size:14px;margin:18px 0 6px;color:var(--text-2)}
p{max-width:76ch}
.sub{color:var(--text-2);margin:0 0 12px}
p.banner{border:1px solid var(--banner-line);border-left:4px solid var(--banner-line);
  background:var(--banner-bg);color:var(--text-1);padding:8px 12px;border-radius:6px;
  font-size:13px;max-width:none;margin:14px 0}
nav.tabs{display:flex;gap:2px;flex-wrap:wrap;margin:10px 0 0}
nav.tabs a{padding:8px 14px;border-radius:8px 8px 0 0;color:var(--text-2);
  text-decoration:none;font-size:13.5px;border:1px solid transparent;border-bottom:none}
nav.tabs a:hover{color:var(--text-1);background:var(--surface-0)}
nav.tabs a.on{background:var(--surface-0);border-color:var(--line);color:var(--text-1);font-weight:600}
a{color:var(--s1)}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}
.card{background:var(--surface-1);border:1px solid var(--line);border-radius:10px;
  padding:12px 16px;min-width:132px;flex:0 1 auto}
.card .k{font-size:12px;color:var(--text-2);letter-spacing:.02em}
.card .v{font-size:26px;font-weight:650;font-variant-numeric:tabular-nums;letter-spacing:-.01em}
.card .d{font-size:12px;color:var(--text-3)}
.panel{background:var(--surface-1);border:1px solid var(--line);border-radius:10px;
  padding:14px 16px;margin:14px 0}
figure{margin:0}
figure figcaption{font-size:13px;color:var(--text-2);margin:4px 0 8px}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:12.5px;color:var(--text-2);margin:2px 0 6px}
.legend .sw{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;
  vertical-align:-1px}
.charts{display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start}
svg{max-width:100%;height:auto;display:block}
svg text{font:11px system-ui,-apple-system,"Segoe UI",sans-serif;fill:var(--text-2)}
svg .lbl{font-weight:600;fill:var(--text-1);paint-order:stroke;stroke:var(--surface-1);
  stroke-width:3px;stroke-linejoin:round}
svg .axis{stroke:var(--line)}
svg .gridline{stroke:var(--grid)}
svg .cellstroke{stroke:var(--surface-1);stroke-width:2px}
.scroll{overflow-x:auto}
table{border-collapse:collapse;margin:10px 0;font-size:13px;background:var(--surface-1);
  border:1px solid var(--line);border-radius:8px}
th,td{border-bottom:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top}
th{background:var(--surface-0);color:var(--text-2);font-weight:600;font-size:12px;
  letter-spacing:.02em;white-space:nowrap}
tr:last-child td{border-bottom:none}
td{font-variant-numeric:tabular-nums}
.tier,.badge{display:inline-block;padding:1px 8px;border-radius:99px;font-size:11.5px;
  font-weight:600;white-space:nowrap}
.tier-heldout_verified{background:var(--good-bg);color:var(--good-fg)}
.tier-search_only{background:var(--warn-bg);color:var(--warn-fg)}
.tier-unreplicated{background:var(--mut-bg);color:var(--mut-fg)}
.badge-open{background:var(--mut-bg);color:var(--mut-fg)}
.badge-supported{background:var(--good-bg);color:var(--good-fg)}
.badge-refuted{background:var(--bad-bg);color:var(--bad-fg)}
.badge-inconclusive{background:var(--warn-bg);color:var(--warn-fg)}
.hash{word-break:break-all;font:12px ui-monospace,Consolas,monospace;color:var(--text-2)}
.mono{font:13px ui-monospace,Consolas,monospace}
.note{font-size:13px;color:var(--text-2);font-style:italic}
dl.meta{display:grid;grid-template-columns:max-content 1fr;gap:2px 16px;font-size:13px}
dl.meta dt{color:var(--text-2)}
dl.meta dd{margin:0}
article.entry{background:var(--surface-1);border:1px solid var(--line);border-radius:10px;
  padding:16px 20px;margin:14px 0}
article.entry h2{margin-top:0}
article.entry .when{font-size:12px;color:var(--text-3);margin:0 0 8px}
.when{font-size:12px;color:var(--text-3)}
p.lead{color:var(--text-2);font-size:13.5px;max-width:76ch;margin:10px 0 16px}
details.explain{margin:10px 0;max-width:76ch;font-size:13px;color:var(--text-2);
  border:1px solid var(--line);border-radius:8px;background:var(--surface-1)}
details.explain summary{cursor:pointer;padding:6px 12px;color:var(--text-3);
  font-size:12.5px;letter-spacing:.02em}
details.explain summary:hover{color:var(--text-1)}
details.explain[open] summary{border-bottom:1px solid var(--line);color:var(--text-2)}
details.explain>div{padding:4px 12px 8px}
details.explain p{margin:8px 0}
details.fold{background:var(--surface-1);border:1px solid var(--line);border-radius:10px;
  margin:10px 0}
details.fold>summary{cursor:pointer;padding:10px 16px;font-size:13.5px}
details.fold>summary:hover{color:var(--text-1)}
details.fold[open]>summary{border-bottom:1px solid var(--line)}
details.fold>div{padding:6px 16px 12px}
footer{margin-top:40px;font-size:12px;color:var(--text-3)}
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


def _ct(value) -> str:
    """Stored UTC timestamp -> US Central display string (pure, deterministic)."""
    return timefmt.fmt_ct(value)


def _gloss(anchor: str, text: str) -> str:
    """A deep link into the glossary."""
    return f'<a href="glossary.html#{_esc(anchor)}">{_esc(text)}</a>'


def _explain(*paras: str) -> str:
    """A collapsed "How to read this" block. Paragraphs are trusted HTML fragments."""
    body = "".join(f"<p>{p}</p>" for p in paras)
    return ('<details class="explain"><summary>How to read this</summary>'
            f"<div>{body}</div></details>")


_NAV_ITEMS = (
    ("index.html", "overview"),
    ("methodology.html", "methodology"),
    ("costmodel.html", "cost model"),
    ("falsification.html", "falsification"),
    ("hypotheses.html", "hypotheses"),
    ("literature.html", "literature"),
    ("interpretation.html", "interpretation"),
    ("glossary.html", "glossary"),
)


def _nav(active: str) -> str:
    links = "".join(
        f'<a href="{href}"{" class=" + chr(34) + "on" + chr(34) if href == active else ""}>{_esc(label)}</a>'
        for href, label in _NAV_ITEMS)
    return f'<nav class="tabs">{links}</nav>'


def _page(title: str, body: str, active: str = "", subtitle: str = "") -> str:
    sub = f'<p class="sub">{_esc(subtitle)}</p>' if subtitle else ""
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_esc(title)}</title>\n"
        f"<style>{_STYLE}</style>\n</head>\n<body>\n"
        '<header class="site"><div class="wrap">\n'
        f'<p class="banner">{_esc(BANNER)}</p>\n'
        f"<h1>{_esc(title)}</h1>\n{sub}\n"
        f"{_nav(active)}\n"
        "</div></header>\n"
        '<div class="wrap">\n'
        f"{body}\n"
        "<footer>rk-harness findings — Q15 fixed point, equal cycle budget, Cortex-M0+ cost models.</footer>\n"
        "</div>\n</body>\n</html>\n"
    )


# ----------------------------------------------------------------------------
# SVG chart primitives (deterministic: every coordinate f-formatted)
# ----------------------------------------------------------------------------

def _fmt(v: float) -> str:
    return f"{v:.2f}"


def _log_ticks(lo: float, hi: float) -> list[float]:
    out = []
    e = math.floor(math.log10(lo))
    while 10 ** e <= hi * 1.0001:
        if 10 ** e >= lo * 0.9999:
            out.append(10 ** e)
        e += 1
    return out or [lo, hi]


def _pow_label(v: float) -> str:
    e = round(math.log10(v))
    return f"1e{e}" if abs(10 ** e - v) / v < 1e-6 else f"{v:g}"


class _LogLog:
    """A log-log plot area with recessive grid + axes."""

    def __init__(self, width: int, height: int, xlo, xhi, ylo, yhi,
                 xlabel: str, ylabel: str, ml=52, mr=14, mt=10, mb=34):
        self.w, self.h = width, height
        self.ml, self.mr, self.mt, self.mb = ml, mr, mt, mb
        self.xlo, self.xhi, self.ylo, self.yhi = xlo, xhi, ylo, yhi
        self.xlabel, self.ylabel = xlabel, ylabel
        self.parts: list[str] = []

    def x(self, v: float) -> float:
        span = math.log10(self.xhi) - math.log10(self.xlo) or 1.0
        return self.ml + (math.log10(v) - math.log10(self.xlo)) / span * (self.w - self.ml - self.mr)

    def y(self, v: float) -> float:
        span = math.log10(self.yhi) - math.log10(self.ylo) or 1.0
        return self.h - self.mb - (math.log10(v) - math.log10(self.ylo)) / span * (self.h - self.mt - self.mb)

    def frame(self) -> None:
        for tv in _log_ticks(self.xlo, self.xhi):
            px = self.x(tv)
            self.parts.append(f'<line class="gridline" x1="{_fmt(px)}" y1="{self.mt}" x2="{_fmt(px)}" y2="{self.h - self.mb}"/>')
            self.parts.append(f'<text x="{_fmt(px)}" y="{self.h - self.mb + 14}" text-anchor="middle">{_pow_label(tv)}</text>')
        for tv in _log_ticks(self.ylo, self.yhi):
            py = self.y(tv)
            self.parts.append(f'<line class="gridline" x1="{self.ml}" y1="{_fmt(py)}" x2="{self.w - self.mr}" y2="{_fmt(py)}"/>')
            self.parts.append(f'<text x="{self.ml - 6}" y="{_fmt(py + 3.5)}" text-anchor="end">{_pow_label(tv)}</text>')
        self.parts.append(f'<line class="axis" x1="{self.ml}" y1="{self.h - self.mb}" x2="{self.w - self.mr}" y2="{self.h - self.mb}"/>')
        self.parts.append(f'<line class="axis" x1="{self.ml}" y1="{self.mt}" x2="{self.ml}" y2="{self.h - self.mb}"/>')
        self.parts.append(f'<text x="{_fmt((self.ml + self.w - self.mr) / 2)}" y="{self.h - 6}" text-anchor="middle">{_esc(self.xlabel)}</text>')
        self.parts.append(f'<text x="12" y="{_fmt((self.mt + self.h - self.mb) / 2)}" text-anchor="middle" '
                          f'transform="rotate(-90 12 {_fmt((self.mt + self.h - self.mb) / 2)})">{_esc(self.ylabel)}</text>')

    def svg(self, aria: str) -> str:
        return (f'<svg viewBox="0 0 {self.w} {self.h}" width="{self.w}" height="{self.h}" '
                f'role="img" aria-label="{_esc(aria)}">' + "".join(self.parts) + "</svg>")


def _legend(items: list[tuple[str, str]]) -> str:
    return '<div class="legend">' + "".join(
        f'<span><span class="sw" style="background:{sw}"></span>{_esc(label)}</span>'
        for sw, label in items) + "</div>"


def _finite_pos(v) -> bool:
    return isinstance(v, (int, float)) and v == v and 0 < v < float("inf")


def _elite_scatter(arch: ArchiveState) -> str:
    """Cost (fast cycles, log) vs held-out error (log): elites + classical baselines."""
    elites = []
    for order in sorted(arch.grids):
        for (stg, bucket) in sorted(arch.grids[order]):
            rec = arch.grids[order][(stg, bucket)]
            cyc, err = rec.score.cycles.get("m0plus_fast"), rec.score.heldout_error
            if _finite_pos(cyc) and _finite_pos(err):
                elites.append((order, stg, bucket, rec, float(cyc), float(err)))
    base = []
    for name, t in sorted(tableau_mod.classical().items()):
        h = tableau_mod.content_hash(t)
        match = None
        for order, stg, bucket, rec, cyc, err in elites:
            if rec.tableau_hash == h:
                match = (cyc, err)
        cyc = costmodel.cycle_count(t, costmodel.M0PLUS_FAST, 1)
        base.append((name, float(cyc), match[1] if match else None))
    if not elites:
        return "<p>no elites yet</p>"
    xs = [c for *_x, c, _e in elites] + [c for _n, c, _e in base]
    ys = [e for *_x, _c, e in elites] + [e for _n, _c, e in base if e is not None]
    xlo = 10 ** math.floor(math.log10(min(xs)))
    xhi = 10 ** math.ceil(math.log10(max(xs)))
    ylo = 10 ** math.floor(math.log10(min(ys)))
    yhi = 10 ** math.ceil(math.log10(max(ys)))
    pl = _LogLog(640, 340, xlo, xhi, ylo, yhi, "cycles per step (m0plus_fast, n_states=1)",
                 "held-out error")
    pl.frame()
    for order, stg, bucket, rec, cyc, err in elites:
        title = (f"order {order}, {stg} stages, bucket {bucket}: {_num(err)} held-out at {int(cyc)} cycles "
                 f"({rec.tier}) — {rec.tableau_hash[:12]}")
        pl.parts.append(
            f'<a href="{_cell_file(order, stg, bucket)}"><circle cx="{_fmt(pl.x(cyc))}" cy="{_fmt(pl.y(err))}" '
            f'r="5" fill="var(--s1)" class="cellstroke"><title>{_esc(title)}</title></circle></a>')
    for name, cyc, err in base:
        if err is None:
            continue
        px, py = pl.x(cyc), pl.y(err)
        pl.parts.append(
            f'<path d="M {_fmt(px)} {_fmt(py - 6)} L {_fmt(px + 6)} {_fmt(py)} L {_fmt(px)} {_fmt(py + 6)} '
            f'L {_fmt(px - 6)} {_fmt(py)} Z" fill="var(--s2)" class="cellstroke">'
            f'<title>{_esc(name)}: {_num(err)} held-out at {int(cyc)} cycles</title></path>')
        pl.parts.append(f'<text class="lbl" x="{_fmt(px + 8)}" y="{_fmt(py - 6)}">{_esc(name)}</text>')
    fig = pl.svg("Scatter of held-out error against cycles per step for archive elites and classical baselines")
    return ('<figure><figcaption>Held-out error vs cost, both log scales. Lower-left is better; '
            "hover a mark for its cell.</figcaption>"
            + _legend([("var(--s1)", "archive elite (links to its cell)"), ("var(--s2)", "classical baseline")])
            + fig + "</figure>")


_HEAT_STAGES = (2, 3, 4, 5, 6)
_HEAT_BUCKETS = tuple(range(8))


def _heat_step(err: float, lo: float, hi: float) -> int:
    """Map error to sequential step 1..7 (more accurate = deeper color)."""
    if hi <= lo:
        return 4
    a = (math.log10(hi) - math.log10(err)) / (math.log10(hi) - math.log10(lo))
    return max(1, min(7, 1 + int(round(a * 6))))


def _grid_heatmap(order: int, grid: dict) -> str:
    errs = [rec.score.heldout_error for rec in grid.values() if _finite_pos(rec.score.heldout_error)]
    lo, hi = (min(errs), max(errs)) if errs else (1e-3, 1.0)
    cw, ch, ml, mt = 52, 34, 58, 22
    w = ml + cw * len(_HEAT_BUCKETS) + 8
    h = mt + ch * len(_HEAT_STAGES) + 26
    parts = []
    for j, b in enumerate(_HEAT_BUCKETS):
        parts.append(f'<text x="{_fmt(ml + cw * j + cw / 2)}" y="{mt - 7}" text-anchor="middle">b{b}</text>')
    for i, s in enumerate(_HEAT_STAGES):
        parts.append(f'<text x="{ml - 8}" y="{_fmt(mt + ch * i + ch / 2 + 3.5)}" text-anchor="end">s={s}</text>')
    for i, s in enumerate(_HEAT_STAGES):
        for j, b in enumerate(_HEAT_BUCKETS):
            x, y = ml + cw * j, mt + ch * i
            rec = grid.get((s, b))
            if rec is None:
                parts.append(f'<rect x="{x}" y="{y}" width="{cw}" height="{ch}" rx="4" '
                             f'fill="var(--surface-1)" stroke="var(--grid)"><title>order {order}, {s} stages, '
                             f"bucket {b}: empty</title></rect>")
                continue
            err = rec.score.heldout_error
            step = _heat_step(err, lo, hi) if _finite_pos(err) else 1
            title = (f"order {order}, {s} stages, bucket {b}: {_num(err)} held-out, "
                     f"{rec.tier}, {rec.tableau_hash[:12]}")
            parts.append(
                f'<a href="{_cell_file(order, s, b)}"><rect x="{x}" y="{y}" width="{cw}" height="{ch}" rx="4" '
                f'fill="var(--q{step})" class="cellstroke"><title>{_esc(title)}</title></rect>'
                f'<text class="lbl" x="{_fmt(x + cw / 2)}" y="{_fmt(y + ch / 2 + 3.5)}" '
                f'text-anchor="middle">{_num(err) if _finite_pos(err) else "?"}</text></a>')
    parts.append(f'<text x="{ml}" y="{h - 6}">cycle bucket (m0plus_fast) — deeper fill = lower held-out error</text>')
    svg = (f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
           f'aria-label="Order {order} elite grid heatmap">' + "".join(parts) + "</svg>")
    return f'<figure><figcaption>Order {order} — elites by (stages, cycle bucket)</figcaption>{svg}</figure>'


def _round_top_bar(x: float, y: float, w: float, h: float, fill: str, title: str, r: float = 4) -> str:
    r = min(r, w / 2, max(h, 0.01))
    d = (f"M {_fmt(x)} {_fmt(y + h)} L {_fmt(x)} {_fmt(y + r)} Q {_fmt(x)} {_fmt(y)} {_fmt(x + r)} {_fmt(y)} "
         f"L {_fmt(x + w - r)} {_fmt(y)} Q {_fmt(x + w)} {_fmt(y)} {_fmt(x + w)} {_fmt(y + r)} "
         f"L {_fmt(x + w)} {_fmt(y + h)} Z")
    return f'<path d="{d}" fill="{fill}" class="cellstroke"><title>{_esc(title)}</title></path>'


def _anchor_bars() -> str:
    classical = tableau_mod.classical()
    vals = []
    for name in ("rk4", "rk38"):
        t = classical.get(name)
        if t is None:
            return ""
        vals.append((name,
                     costmodel.cycle_count(t, costmodel.M0PLUS_FAST, 1),
                     costmodel.cycle_count(t, costmodel.M0PLUS_SLOW, 1)))
    vmax = max(max(f, s) for _n, f, s in vals)
    w, h, ml, mb, mt = 460, 260, 46, 40, 16
    plot_h = h - mb - mt
    scale = plot_h / (vmax * 1.15)
    group_w = (w - ml - 20) / len(vals)
    bar_w, gap = 56, 2
    parts = [f'<line class="axis" x1="{ml}" y1="{h - mb}" x2="{w - 10}" y2="{h - mb}"/>']
    for gi, (name, fast, slow) in enumerate(vals):
        cx = ml + group_w * gi + group_w / 2
        for k, (label, v, sw) in enumerate((("m0plus_fast", fast, "var(--s1)"), ("m0plus_slow", slow, "var(--s2)"))):
            bx = cx - bar_w - gap / 2 + k * (bar_w + gap)
            bh = v * scale
            parts.append(_round_top_bar(bx, h - mb - bh, bar_w, bh, sw,
                                        f"{name} under {label}: {v} cycles per step"))
            parts.append(f'<text class="lbl" x="{_fmt(bx + bar_w / 2)}" y="{_fmt(h - mb - bh - 5)}" '
                         f'text-anchor="middle">{v}</text>')
        parts.append(f'<text x="{_fmt(cx)}" y="{h - mb + 16}" text-anchor="middle">{_esc(name)}</text>')
    svg = (f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
           'aria-label="Cycles per step for rk4 and rk38 under the fast and slow multiplier models">'
           + "".join(parts) + "</svg>")
    return ("<figure><figcaption>The anchor result: the cheaper method swaps between the two "
            "multiplier models (same ISA, same stability polynomial).</figcaption>"
            + _legend([("var(--s1)", "m0plus_fast (1-cycle multiplier)"), ("var(--s2)", "m0plus_slow (32-cycle multiplier)")])
            + svg + "</figure>")


def _sweep_chart(name: str, method: dict) -> str:
    rows = [r for r in method.get("sweep", [])
            if _finite_pos(r.get("h")) and (_finite_pos(r.get("q15_error")) or _finite_pos(r.get("float_error")))]
    if not rows:
        return ""
    hs = [r["h"] for r in rows]
    errs = ([r["q15_error"] for r in rows if _finite_pos(r.get("q15_error"))]
            + [r["float_error"] for r in rows if _finite_pos(r.get("float_error"))])
    pl = _LogLog(430, 300, min(hs), max(hs), 10 ** math.floor(math.log10(min(errs))),
                 10 ** math.ceil(math.log10(max(errs))), "step size h", "final-state error", ml=56)
    pl.frame()
    cross = method.get("crossover_h")
    if _finite_pos(cross):
        px = pl.x(cross)
        pl.parts.append(f'<line x1="{_fmt(px)}" y1="{pl.mt}" x2="{_fmt(px)}" y2="{pl.h - pl.mb}" '
                        'stroke="var(--text-3)" stroke-dasharray="4 3"/>')
        pl.parts.append(f'<text class="lbl" x="{_fmt(px + 4)}" y="{pl.mt + 12}">crossover h={_num(cross)}</text>')
    for key, sw in (("q15_error", "var(--s1)"), ("float_error", "var(--s2)")):
        pts = [(r["h"], r[key]) for r in rows if _finite_pos(r.get(key))]
        if len(pts) < 2:
            continue
        path = " ".join(f"{'M' if i == 0 else 'L'} {_fmt(pl.x(hv))} {_fmt(pl.y(ev))}"
                        for i, (hv, ev) in enumerate(pts))
        pl.parts.append(f'<path d="{path}" fill="none" stroke="{sw}" stroke-width="2"/>')
        for hv, ev in pts:
            pl.parts.append(f'<circle cx="{_fmt(pl.x(hv))}" cy="{_fmt(pl.y(ev))}" r="4" fill="{sw}" '
                            f'class="cellstroke"><title>{_esc(name)} {key} at h={_num(hv)}: {_num(ev)}</title></circle>')
    svg = pl.svg(f"{name}: Q15 and float64 error against step size, log-log")
    return f"<figure><figcaption>{_esc(name)}</figcaption>{svg}</figure>"


def _per_problem_bars(sv) -> str:
    keys = [k for k in sorted(sv.per_problem) if ":" not in str(k)]
    vals = [(k, sv.per_problem[k]) for k in keys if _finite_pos(sv.per_problem[k])]
    if not vals:
        return ""
    lo = 10 ** math.floor(math.log10(min(v for _k, v in vals)))
    hi = 10 ** math.ceil(math.log10(max(v for _k, v in vals)))
    row_h, ml, w = 26, 118, 560
    h = 16 + row_h * len(vals) + 30
    span = math.log10(hi) - math.log10(lo) or 1.0
    parts = []
    for tv in _log_ticks(lo, hi):
        px = ml + (math.log10(tv) - math.log10(lo)) / span * (w - ml - 16)
        parts.append(f'<line class="gridline" x1="{_fmt(px)}" y1="10" x2="{_fmt(px)}" y2="{h - 26}"/>')
        parts.append(f'<text x="{_fmt(px)}" y="{h - 12}" text-anchor="middle">{_pow_label(tv)}</text>')
    for i, (k, v) in enumerate(vals):
        y = 14 + row_h * i
        bw = (math.log10(v) - math.log10(lo)) / span * (w - ml - 16)
        parts.append(f'<text x="{ml - 6}" y="{_fmt(y + 13)}" text-anchor="end">{_esc(k)}</text>')
        parts.append(f'<rect x="{ml}" y="{y}" width="{_fmt(max(bw, 2))}" height="18" rx="4" fill="var(--s1)" '
                     f'class="cellstroke"><title>{_esc(k)}: {_num(v)}</title></rect>')
        parts.append(f'<text class="lbl" x="{_fmt(ml + max(bw, 2) + 6)}" y="{_fmt(y + 13)}">{_num(v)}</text>')
    svg = (f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
           'aria-label="Per-problem error, log scale">' + "".join(parts) + "</svg>")
    return ('<figure><figcaption>Per-problem error under m0plus_fast (log scale)</figcaption>'
            + svg + "</figure>")


# ----------------------------------------------------------------------------
# page fragments
# ----------------------------------------------------------------------------

def _tableau_table(rec: Record) -> str:
    t = rec.tableau
    s = len(t.b)
    rows = []
    for i in range(s):
        cells = "".join(f'<td class="mono">{_frac(x)}</td>' for x in t.A[i])
        rows.append(f'<tr><th class="mono">c[{i}] = {_frac(t.c[i])}</th>{cells}</tr>')
    bcells = "".join(f'<td class="mono">{_frac(x)}</td>' for x in t.b)
    rows.append(f"<tr><th>b</th>{bcells}</tr>")
    head = "<tr><th></th>" + "".join(f"<th>A[.][{j}]</th>" for j in range(s)) + "</tr>"
    return f'<div class="scroll"><table>\n{head}\n' + "\n".join(rows) + "\n</table></div>"


def _coeff_rep_details(rec: Record) -> str:
    """The dyadic m/2^s form of every nonzero A and b entry, as the integrator applies it.

    The Record stores the tableau as exact fractions; the m/2^s pairs shown here are
    recomputed with the same pinned coeffrep.to_rep the integrator uses, so the listing
    is exactly what ran.
    """
    t = rec.tableau
    entries: list[tuple[str, object]] = []
    for i, row in enumerate(t.A):
        for j in range(i):
            if row[j] != 0:
                entries.append((f"A[{i}][{j}]", row[j]))
    for i, x in enumerate(t.b):
        if x != 0:
            entries.append((f"b[{i}]", x))
    rows = []
    for label, x in entries:
        r = coeffrep.to_rep(x)
        rows.append(f'<tr><td class="mono">{_esc(label)}</td><td class="mono">{_frac(x)}</td>'
                    f"<td>{r.m}</td><td>{r.s}</td><td class=\"mono\">{r.m}/2^{r.s}</td>"
                    f"<td>{'yes' if r.exact else 'no'}</td><td>{r.csd_weight}</td></tr>")
    body = [
        "<p>The Q15 integrator applies each nonzero A and b entry to a state value v as "
        "(v * m) &gt;&gt; s, an arithmetic right shift that "
        + _gloss("floor-rounding", "floors") + ". Zero entries are skipped. "
        "When exact is no, m/2^s only approximates the fraction and the largest such gap is "
        "the record's coeff_quant_error. " + _gloss("csd-weight", "CSD weight")
        + " is the length of the shift-add chain the cost model may charge for the multiply "
        "by m.</p>",
        '<div class="scroll"><table><tr><th>entry</th><th>exact value</th><th>m</th><th>s</th>'
        "<th>m/2^s</th><th>exact</th><th>csd weight</th></tr>",
    ]
    body.extend(rows)
    body.append("</table></div>")
    return ('<details class="fold"><summary>Raw coefficient representation (m/2^s)</summary>'
            "<div>" + "\n".join(body) + "</div></details>")


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
        ("recorded", f"{_esc(_ct(rec.timestamp))} <span class=\"when\">(stored {_esc(rec.timestamp)} UTC)</span>"),
    ]
    return '<dl class="meta">\n' + "\n".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in items) + "\n</dl>"


def _stat_cards(arch: ArchiveState) -> str:
    elites = [rec for grid in arch.grids.values() for rec in grid.values()]
    verified = sum(1 for r in elites if r.tier == "heldout_verified")
    cells = 40 * len(arch.grids)
    cards = [
        ("records", _num(arch.n_records), "verified tableaus archived"),
        ("last cycle", _num(arch.last_cycle_id), ""),
        ("elite cells", f"{len(elites)}/{cells}", "grid coverage, all orders"),
        ("heldout_verified", str(verified), "elites in the top tier"),
        ("hypotheses", f"{len(arch.open_hypotheses)} open", f"{len(arch.refuted_hypotheses)} refuted"),
    ]
    return '<div class="cards">' + "".join(
        f'<div class="card"><div class="k">{_esc(k)}</div><div class="v">{_esc(v)}</div>'
        f'<div class="d">{_esc(d)}</div></div>' for k, v, d in cards) + "</div>"


# ----------------------------------------------------------------------------
# pages
# ----------------------------------------------------------------------------

def render_index(arch: ArchiveState) -> str:
    parts = [
        '<p class="lead">This page summarizes the archive of an automated search for explicit '
        "Runge-Kutta methods that hold up in " + _gloss("q15", "Q15") + " fixed-point arithmetic "
        "on small microcontrollers. Every verified " + _gloss("tableau", "tableau") + " lives in a "
        + _gloss("map-elites", "MAP-Elites grid") + "; the tables and charts below are generated "
        "from that archive and each entry links to a detail page. Terms are defined in the "
        '<a href="glossary.html">glossary</a>.</p>'
    ]
    parts.append(_stat_cards(arch))
    parts.append('<p class="note">Fitness is heldout_error under m0plus_fast at equal cycle budget; '
                 "lower is better and nothing more is claimed. Cells are (stages, cycle bucket).</p>")
    parts.append("<h2>Cost against held-out error</h2>")
    parts.append('<div class="panel">' + _elite_scatter(arch) + "</div>")
    parts.append(_explain(
        "Each blue dot is one archive record: a " + _gloss("tableau", "Butcher tableau")
        + " that passed the pinned verifier, stored with its full score, provenance and timestamps. "
        "The horizontal axis is the analytic cycle count of one step under the m0plus_fast cost "
        "model for a one-state problem, on a log scale.",
        "The vertical axis, also log, is held-out error: the root-mean-square final-state error "
        "over the four " + _gloss("held-out-set", "held-out problems") + ", integrated in Q15 at a "
        "fixed " + _gloss("cycle-budget", "cycle budget") + " of 65536 cycles. Cheaper methods get "
        "more, smaller steps inside the same budget, so the plot shows the cost-accuracy trade "
        "directly. Down and to the left is better on both axes.",
        "Orange diamonds are classical baseline methods at the same budget; a baseline gets a "
        "vertical position only when a record with the identical tableau is in the archive. "
        "Every dot links to the detail page of its grid cell."))
    parts.append("<h2>Elite grids</h2>")
    parts.append(_explain(
        "The archive is a " + _gloss("map-elites", "MAP-Elites") + " structure: one grid per "
        "algebraic " + _gloss("order", "order") + " 1 to 4, whose cells are keyed by (stage count, "
        "cycle bucket). A cell holds at most one record, its " + _gloss("elite", "elite") + ": the "
        "verified tableau with the lowest held-out error seen so far for that shape and cost; ties "
        "keep the earlier record.",
        "Rows are " + _gloss("stage", "stage") + " counts 2 to 6. Columns are "
        + _gloss("cost-bucket", "cycle buckets") + " computed from the m0plus_fast cycles per "
        "step: bucket 0 means fewer than 16 cycles, bucket 1 is 16 to 31, bucket 2 is 32 to 63, "
        "doubling each column, and bucket 7 collects everything at 1024 cycles or more.",
        "Deeper blue means lower held-out error within that grid; the printed number is the error "
        "itself. An empty cell means no verified tableau has landed there yet. Click any filled "
        "cell to open its detail page."))
    parts.append('<div class="charts">')
    for order in sorted(arch.grids.keys()):
        if arch.grids[order]:
            parts.append('<div class="panel">' + _grid_heatmap(order, arch.grids[order]) + "</div>")
    parts.append("</div>")
    table_explain = _explain(
        "Each row is the elite of one grid cell; the tableau hash links to the cell's detail "
        "page with the full tableau, score and per-problem errors. heldout_error is the fitness "
        "that decides which record occupies a cell; search_error is the same measurement on the "
        "three problems the optimizer was allowed to see, so a large gap between the two columns "
        "is overfitting to the search set.",
        "The cycles columns are analytic per-step costs under the two primary cost models. "
        "measured_order is the convergence slope observed in float64 on the dahlquist problem "
        "(see " + _gloss("order", "order") + " in the glossary); it can differ from the algebraic "
        "order that names the grid. The " + _gloss("tiers", "tier") + " badge records how the "
        "elite compared against the incumbent it displaced, and the label column separates "
        "exhaustive-enumeration results from search results. verifier_hash pins the exact "
        + _gloss("verifier-hash", "scoring code") + " that produced the row.")
    for order in sorted(arch.grids.keys()):
        grid = arch.grids[order]
        parts.append(f"<h2>Order {order} grid</h2>")
        if not grid:
            parts.append("<p>no elites yet</p>")
            continue
        parts.append(table_explain)
        parts.append('<div class="scroll"><table>\n<tr><th>stages</th><th>bucket</th><th>tableau_hash</th><th>tier</th>'
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
        parts.append("</table></div>")
    return _page("rk-harness findings", "\n".join(parts), active="index.html",
                 subtitle="Explicit Runge-Kutta tableaus scored end-to-end in Q15 at a fixed cycle budget.")


def render_cell(order: int, stages: int, bucket: int, rec: Record) -> str:
    sv = rec.score
    parts = []
    parts.append(f'<p class="sub">grid order {order}, {stages} stages, cycle bucket {bucket}</p>')
    parts.append(
        '<p class="lead">This page is the full archive record for the '
        + _gloss("elite", "elite") + f" of one grid cell: algebraic order {order}, "
        f"{stages} stages, " + _gloss("cost-bucket", "cycle bucket") + f" {bucket}. "
        'Everything here was produced by the pinned verifier; see the <a href="index.html">'
        'overview</a> for where this cell sits in the archive.</p>')
    parts.append('<div class="panel">' + _record_meta(rec) + "</div>")
    parts.append(_explain(
        "The metadata above identifies the record. tableau_hash is a content hash of the exact "
        "coefficients, so identical methods collide on purpose; "
        + _gloss("verifier-hash", "verifier_hash") + " pins the code and fixtures that scored "
        "the record, and a record scored by different code would carry a different hash. "
        "recorded is the stored UTC timestamp shown in US Central.",
        "The " + _gloss("tiers", "tier") + " is assigned mechanically when the record enters its "
        "cell: heldout_verified means it improved on the incumbent's search and held-out error "
        "with gains in at least two problem families, search_only means it improved on search "
        "error but not held-out error, and unreplicated covers the rest, including records that "
        "landed in an empty cell.",
        "The phase label separates exhaustive enumeration (" + _gloss("directive", "directives")
        + " whose id starts with D-E, where every point in a small space was scored) from "
        "ordinary search results. cycle_id and seed say when and with what randomness the "
        "record was produced."))
    parts.append("<h2>Tableau</h2>")
    parts.append(_tableau_table(rec))
    parts.append(_explain(
        "This is the " + _gloss("tableau", "Butcher tableau") + ", the coefficient table that "
        "defines the method. Row i of A weights the earlier "
        + _gloss("stage", "stage") + " derivatives that form the input of stage i; A is strictly "
        "lower triangular, so the method is explicit. c[i] is the time offset of stage i as a "
        "fraction of the step, and the b row weights the stage derivatives in the final state "
        "update.",
        "All values are exact fractions. The Q15 integrator never uses them directly: each one "
        "is applied as a " + _gloss("dyadic-rational", "dyadic") + " pair m/2^s, listed below."))
    parts.append(_coeff_rep_details(rec))
    parts.append("<h2>Per problem</h2>")
    parts.append('<div class="panel">' + _per_problem_bars(sv) + "</div>")
    parts.append(_explain(
        "One bar per problem: the final-state error of this tableau on that problem, integrated "
        "in Q15 under m0plus_fast at the fixed " + _gloss("cycle-budget", "cycle budget")
        + ", log scale. dahlquist, damped_osc and vanderpol_mild are the search set the "
        "optimizer was allowed to see; pendulum, dc_motor, rc_thermal and quaternion are the "
        + _gloss("held-out-set", "held-out set") + " that decides archive fitness.",
        "Errors on several problems are dominated by Q15 quantization rather than by the "
        "method: " + _gloss("floor-rounding", "floor rounding") + " loses up to one "
        + _gloss("lsb", "LSB") + " per multiply, always downward, so bars can look similar "
        "across very different tableaus. The table further down lists the same measurements "
        "under the other cost models."))
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
    parts.append(_explain(
        "measured_order is the slope of a log-log fit of float64 final-state error against step "
        "size on the dahlquist problem, over the longest usable run of points "
        "(order_fit_points of them); it is measured evidence, distinct from the algebraic "
        + _gloss("order", "order") + " that keys the grid. error_constant is the L2 norm of the "
        "order-condition residuals one order past the achieved one, a size estimate for the "
        "leading truncation term. stability_real and stability_imag are the extents of the "
        "stability region along the negative real axis and the imaginary axis.",
        "csd_weight_total sums " + _gloss("csd-weight", "CSD weights") + " over the non-trivial "
        "coefficients, a proxy for coefficient-arithmetic cost. coeff_quant_error is the largest "
        "gap between an exact coefficient and its m/2^s form. search_error and heldout_error are "
        "root-mean-square errors over the two problem sets at the fixed cycle budget; "
        "heldout_error is the archive fitness. overflow_margin is 1 / max|state| observed at "
        "twice the nominal amplitude and must exceed 1.0, meaning a doubled signal still fits "
        "in " + _gloss("q15", "Q15") + " range."))
    parts.append("<h3>All per-problem keys</h3>")
    parts.append('<div class="scroll"><table><tr><th>key</th><th>error</th><th>note</th></tr>')
    for k in sorted(sv.per_problem.keys()):
        note = _esc(AVR_NOTE) if str(k).startswith("avr_approx:") else ""
        parts.append(f"<tr><td>{_esc(k)}</td><td>{_num(sv.per_problem[k])}</td><td>{note}</td></tr>")
    parts.append("</table></div>")
    title = f"cell p{order} s{stages} b{bucket}"
    return _page(title, "\n".join(parts), active="index.html")


def _verdict_badge(verdict) -> str:
    v = verdict if verdict is not None else "open"
    return f'<span class="badge badge-{_esc(v)}">{_esc(v)}</span>'


def _hyp_details(h: dict) -> str:
    verdict = h.get("verdict")
    resolved_cycle = h.get("resolved_cycle")
    if verdict is None:
        provenance = ("open: the ledger resolves it automatically once every cell the predicate "
                      "references has at least min_samples records")
    else:
        provenance = ("verdict computed by the ledger from archive cell statistics"
                      + (f" at cycle {_num(resolved_cycle)}" if resolved_cycle is not None else "")
                      + "; the model never writes verdicts")
    rows = [
        ("statement", _esc(h.get("statement", ""))),
        ("mechanism", _esc(h.get("mechanism", ""))),
        ("control", _esc(h.get("control", ""))),
        ("predicate", f'<span class="mono">{_esc(h.get("predicate", ""))}</span>'),
        ("min_samples", _num(h.get("min_samples"))),
        ("n_samples", _num(h.get("n_samples"))),
        ("effect_size", _num(h.get("effect_size"))),
        ("proposed at cycle", _num(h.get("cycle_proposed"))),
        ("resolved at cycle", _num(resolved_cycle)),
        ("provenance", _esc(provenance)),
    ]
    body = ('<dl class="meta">\n'
            + "\n".join(f"<dt>{_esc(k)}</dt><dd>{v}</dd>" for k, v in rows)
            + "\n</dl>")
    summary = (f'<span class="mono">{_esc(h.get("id", ""))}</span> {_verdict_badge(verdict)} '
               f'<span class="when">effect size {_num(h.get("effect_size"))}, '
               f"n = {_num(h.get('n_samples'))}</span>")
    return (f'<details class="fold"><summary>{summary}</summary>'
            f"<div>{body}</div></details>")


def render_hypotheses(hyps: list[dict]) -> str:
    parts = [
        '<p class="lead">The ' + _gloss("hypothesis-ledger", "hypothesis ledger")
        + ": statements the planning model committed to before the data could answer them, "
        "each resolved mechanically against the archive. Click a hypothesis to see its full "
        "text, predicate and resolution provenance.</p>"
    ]
    parts.append(_explain(
        "A hypothesis is a falsifiable statement about archive cells, recorded with a proposed "
        "mechanism, a control (what should happen instead if the mechanism is wrong), and a "
        "machine-checkable predicate over per-cell statistics, for example "
        '<span class="mono">slow.p3s4.heldout &lt; slow.p4s4.heldout</span>. '
        "The p and s numbers name an (order, stages) cell; the leading word picks the cost model.",
        "Verdicts come from code, never from the model. Once every cell a predicate references "
        "has at least min_samples records, the predicate is evaluated against the cells' running "
        "statistics and the result is supported or refuted. The effect size is "
        + _gloss("cohens-d", "Cohen's d") + ", the absolute difference of the two populations' "
        "means divided by their pooled standard deviation; when populations are compared and d "
        "is below 0.2 the verdict is inconclusive no matter which way the comparison went, so "
        "weak effects cannot be claimed as findings.",
        "A predicate that references a cell with no data is also inconclusive: absence of "
        "evidence never refutes. n is the smallest record count among the referenced cells. "
        "Hypotheses carry cycle numbers rather than clock times; cycles are the run's unit of "
        "progress."))
    if not hyps:
        parts.append("<p>no hypotheses recorded</p>")
    else:
        counts = {"open": 0, "supported": 0, "refuted": 0, "inconclusive": 0}
        for h in hyps:
            counts[h.get("verdict") or "open"] = counts.get(h.get("verdict") or "open", 0) + 1
        parts.append('<div class="cards">' + "".join(
            f'<div class="card"><div class="k">{_esc(k)}</div><div class="v">{v}</div></div>'
            for k, v in counts.items()) + "</div>")
        parts.append('<p class="note">Verdicts are computed by code from the archive; the model never '
                     "writes one. A missing cell reads as inconclusive, never refuted.</p>")
        for h in sorted(hyps, key=lambda d: str(d.get("id", ""))):
            parts.append(_hyp_details(h))
    return _page("hypothesis ledger", "\n".join(parts), active="hypotheses.html",
                 subtitle="Falsifiable statements about the archive, resolved mechanically.")


def render_costmodel() -> str:
    classical = tableau_mod.classical()
    parts = [
        '<p class="lead">This page shows the analytic cost model: cycles per integration step '
        "computed from instruction counts, with no compiler or hardware in the loop. It is what "
        "the " + _gloss("cycle-budget", "cycle budget") + " and the "
        + _gloss("cost-bucket", "cycle buckets") + " on the other pages are built from.</p>"
    ]
    parts.append("<h2>Anchor: rk4 vs rk38</h2>")
    parts.append('<div class="panel">' + _anchor_bars() + "</div>")
    parts.append(_explain(
        "rk4 and rk38 are the " + _gloss("anchor-methods", "anchor methods") + ": two classical "
        "four-stage, order-4 tableaus with the same stability polynomial. The only difference "
        "the cost model can see between them is coefficient arithmetic, since stages and order "
        "match.",
        "Each pair of bars is one method; blue is m0plus_fast (a Cortex-M0+ with the "
        "single-cycle multiplier option), orange is m0plus_slow (the same core with the 32-cycle "
        "iterative multiplier). Under the fast multiplier rk4's simpler coefficients cost less; "
        "under the slow multiplier rk38's shift-friendly eighths pull ahead, so the cheaper "
        "method swaps between the two models. That swap is the sanity check the whole model "
        "hangs on.",
        "The table below repeats the numbers for one, two and four state variables."))
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
    parts.append('<div class="scroll"><table><tr><th>tableau</th><th>stages</th><th>m0plus_fast</th><th>m0plus_slow</th>'
                 "<th>avr_approx</th><th>note</th></tr>")
    for name in sorted(classical.keys()):
        t = classical[name]
        fast = costmodel.cycle_count(t, costmodel.M0PLUS_FAST, 1)
        slow = costmodel.cycle_count(t, costmodel.M0PLUS_SLOW, 1)
        avr = costmodel.cycle_count(t, costmodel.AVR_APPROX, 1)
        parts.append(f"<tr><td>{name}</td><td>{len(t.b)}</td><td>{fast}</td><td>{slow}</td>"
                     f"<td>{avr}</td><td>{_esc(AVR_NOTE)}</td></tr>")
    parts.append("</table></div>")
    parts.append(_explain(
        "Each row is one classical reference " + _gloss("tableau", "tableau") + ". stages is "
        "the number of derivative evaluations per step; the three cycle columns are analytic "
        "per-step costs for a one-state problem under each model. avr_approx is a rough model "
        "of an 8-bit AVR doing 16-bit arithmetic; it is reported for context and never drives "
        "the search or the archive grids.",
        "A coefficient is charged the cheaper of two implementations: a chain of shifts and "
        "adds whose length is the coefficient's " + _gloss("csd-weight", "CSD weight") + " (the "
        "minimum number of signed powers of two that sum to the multiplier m), or a single "
        "hardware multiply. Trivial coefficients (0, 1, -1) need no arithmetic beyond a move, "
        "so a tableau full of simple " + _gloss("dyadic-rational", "dyadic") + " values can be "
        "much cheaper than its stage count suggests."))
    parts.append("<h2>Model parameters</h2>")
    parts.append("<table><tr><th>model</th><th>mul</th><th>add</th><th>shift</th><th>load</th><th>store</th></tr>")
    for m in (costmodel.M0PLUS_FAST, costmodel.M0PLUS_SLOW, costmodel.AVR_APPROX):
        cy = m.cycles
        parts.append(f"<tr><td>{m.name}</td><td>{cy.get('mul')}</td><td>{cy.get('add')}</td>"
                     f"<td>{cy.get('shift')}</td><td>{cy.get('load')}</td><td>{cy.get('store')}</td></tr>")
    parts.append("</table>")
    parts.append(_explain(
        "The per-instruction cycle costs each model assigns. m0plus_fast and m0plus_slow are "
        "the two Cortex-M0+ configurations, identical except for the multiplier: the core is "
        "sold with either a single-cycle or a 32-cycle iterative multiply, which is exactly the "
        "hardware difference this project studies. avr_approx approximates an AVR-class 8-bit "
        "part and is advisory only.",
        "Every cycle count on this site is these five numbers applied to an instruction "
        "sequence derived from the tableau; nothing is measured on hardware."))
    return _page("cost model comparison", "\n".join(parts), active="costmodel.html",
                 subtitle="Analytic cycle counts; no compiler in the loop.")


def _render_value(v) -> str:
    if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
        keys: list[str] = []
        for row in v:
            for k in row.keys():
                if k not in keys:
                    keys.append(k)
        out = ['<div class="scroll"><table><tr>' + "".join(f"<th>{_esc(k)}</th>" for k in keys) + "</tr>"]
        for row in v:
            out.append("<tr>" + "".join(f"<td>{_render_value(row.get(k))}</td>" for k in keys) + "</tr>")
        out.append("</table></div>")
        return "\n".join(out)
    if isinstance(v, dict):
        out = ['<dl class="meta">']
        for k in sorted(v.keys(), key=str):
            out.append(f"<dt>{_esc(k)}</dt><dd>{_render_value(v[k])}</dd>")
        out.append("</dl>")
        return "\n".join(out)
    if isinstance(v, list):
        return "[" + ", ".join(_render_value(x) for x in v) + "]"
    return _num(v)


def render_falsification(data: dict | None) -> str:
    lead = (
        '<p class="lead">This page reports the kill-or-proceed measurement that ran before any '
        "searching. The premise of the whole project is only worth testing if coefficient "
        "arithmetic is a meaningful share of step cost and Q15 roundoff matters at practical "
        "step sizes; this experiment checked both on fixed classical methods, with the verdict "
        "computed by code.</p>")
    protocol = _explain(
        "The protocol fixes two classical methods, rk4 and heun2, on the damped oscillator "
        "problem and measures two things. One: the fraction of one step's cycle count spent on "
        "coefficient arithmetic rather than derivative evaluation, under both primary cost "
        "models. Two: a step-size sweep at n = 8, 16, ..., 4096 steps comparing the Q15 "
        "integrator against float64 on identical steps, looking for the step size where "
        + _gloss("floor-rounding", "roundoff") + " overtakes truncation error.",
        "The verdict is mechanical. proceed requires every coefficient fraction to reach at "
        "least 0.30 and at least one crossover inside the practical range 1e-3 &le; h &le; 1.0. "
        "kill requires every fraction below 0.15 and no practical crossover. Anything in "
        "between reads mixed. A proceed verdict means searching over coefficients can plausibly "
        "matter; it does not by itself establish that any searched method is good.")
    if data is None:
        body = lead + "\n" + protocol + "\n<p>falsification experiment not run (work_dir()/falsification.json absent)</p>"
    else:
        verdict = data.get("verdict")
        parts = [lead, protocol]
        if verdict is not None:
            parts.append(f'<p>verdict: <strong>{_esc(verdict)}</strong></p>')
        methods = data.get("methods")
        if isinstance(methods, dict) and methods:
            parts.append("<h2>Where roundoff overtakes truncation</h2>")
            parts.append('<p class="note">Final-state error against step size, log-log. The float64 line keeps '
                         "falling as h shrinks; the Q15 line turns back up once roundoff dominates — the dashed "
                         "line marks the crossover.</p>")
            parts.append(_legend([("var(--s1)", "Q15 fixed point"), ("var(--s2)", "float64, same steps")]))
            parts.append('<div class="charts">')
            for name in sorted(methods.keys()):
                if isinstance(methods[name], dict):
                    chart = _sweep_chart(name, methods[name])
                    if chart:
                        parts.append('<div class="panel">' + chart + "</div>")
            parts.append("</div>")
            parts.append(_explain(
                "Each chart is one method's sweep. Both lines run the same tableau over the "
                "same steps; only the arithmetic differs. In float64 (orange), halving the step "
                "size keeps cutting the truncation error at the method's order. In "
                + _gloss("q15", "Q15") + " (blue), each step also loses up to one "
                + _gloss("lsb", "LSB") + " to floor rounding, and more steps mean more losses, "
                "so past some h the total error turns back up.",
                "The dashed vertical line marks that crossover. A crossover inside the "
                "practical step-size range is evidence that coefficient choices which reduce "
                "roundoff can pay for themselves; hover any point for its exact values."))
        rest = {k: v for k, v in data.items() if k != "verdict"}
        parts.append("<h2>Raw data</h2>")
        parts.append(_render_value(rest))
        body = "\n".join(parts)
    return _page("falsification experiment", body, active="falsification.html",
                 subtitle="HANDOFF §15 — the kill/proceed measurement, run before the search.")


def render_literature(digests: list[dict]) -> str:
    parts = [
        '<p class="lead">Digests of published work the model researched on the web during the '
        "run, newest at the top. They exist to inform the "
        + _gloss("directive", "directive") + " and "
        + _gloss("hypothesis-ledger", "hypothesis") + " prompts; nothing on this page is a "
        "measurement from this project. Click an entry for its full text and sources.</p>",
        f'<p class="note">{_esc(_MODEL_NOTE)}</p>',
        _explain(
            "Each entry is one digest: the model was given a topic, searched the web, and wrote "
            "the summary and key points itself, attaching the sources it found. The stored text "
            "is shown as written (after a vocabulary pass that keeps this site's banned words "
            "out), with its stored UTC collection time displayed in US Central alongside the "
            "run cycle it fed into.",
            "Because both the summaries and the citations are model-collected, treat them as "
            "leads to verify against the linked sources, not as established results. The "
            "measured pages of this site never depend on anything written here."),
    ]
    if not digests:
        parts.append("<p>no literature digests yet</p>")
    for i, d in enumerate(reversed(digests)):
        open_attr = " open" if i == 0 else ""
        entry = [f'<details class="fold entry"{open_attr}>']
        entry.append(f"<summary><strong>{_esc(d.get('topic', ''))}</strong> "
                     f'<span class="when">collected {_esc(_ct(d.get("ts")))}, '
                     f'cycle {int(d.get("cycle", 0))}</span></summary>')
        entry.append("<div>")
        for para in str(d.get("summary", "")).split("\n\n"):
            if para.strip():
                entry.append(f"<p>{_esc(para.strip())}</p>")
        pts = d.get("key_points") or []
        if pts:
            entry.append("<ul>" + "".join(f"<li>{_esc(k)}</li>" for k in pts) + "</ul>")
        srcs = d.get("sources") or []
        if srcs:
            items = "".join(f'<li><a href="{_esc(x.get("url", ""))}">{_esc(x.get("title", "") or x.get("url", ""))}</a></li>'
                            for x in srcs)
            entry.append(f"<p>sources:</p><ul>{items}</ul>")
        entry.append("</div></details>")
        parts.append("\n".join(entry))
    return _page("literature digest", "\n".join(parts), active="literature.html",
                 subtitle="Web-researched background that feeds the directive and hypothesis prompts.")


def render_interpretation(entries: list[dict]) -> str:
    parts = [
        '<p class="lead">Model-written readings of the archive at points during the run, newest '
        "at the top. Each entry is commentary on the numbers as they stood at that cycle; the "
        "tables and charts on the other pages stay authoritative, and where text and tables "
        "disagree, trust the tables. Click an entry for its full text.</p>",
        f'<p class="note">{_esc(_MODEL_NOTE)}</p>',
        _explain(
            "At intervals the run hands the model a snapshot of the archive and asks it to "
            "write what it sees: coverage, gaps, apparent patterns, things worth testing next. "
            "The text is stored verbatim (after the same vocabulary pass as the literature "
            "page) with its stored UTC write time, displayed here in US Central.",
            "These readings can be wrong in ways the code cannot check, since unlike "
            + _gloss("hypothesis-ledger", "hypotheses") + " they carry no machine-checkable "
            "predicate. They are kept because they explain what the search was steering toward "
            "at each point in the run."),
    ]
    if not entries:
        parts.append("<p>no interpretation yet</p>")
    for i, e in enumerate(reversed(entries)):
        open_attr = " open" if i == 0 else ""
        entry = [f'<details class="fold entry"{open_attr}>']
        entry.append(f"<summary><strong>cycle {int(e.get('cycle', 0))}</strong> "
                     f'<span class="when">written {_esc(_ct(e.get("ts")))}</span></summary>')
        entry.append("<div>")
        for para in str(e.get("text", "")).split("\n\n"):
            if para.strip():
                entry.append(f"<p>{_esc(para.strip())}</p>")
        entry.append("</div></details>")
        parts.append("\n".join(entry))
    return _page("interpretation", "\n".join(parts), active="interpretation.html",
                 subtitle="Model-written readings of the archive; the numbers stay authoritative.")


# Glossary terms: (anchor id, display term, definition paragraphs). Kept in
# alphabetical order of the display term; every definition is grounded in the
# pinned code (fixedpoint, coeffrep, costmodel, archive, ledger, verifier_hash).
_GLOSSARY: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("anchor-methods", "anchor methods", (
        "rk4 and rk38: two classical four-stage, order-4 tableaus with the same stability "
        "polynomial. Because stages and order match, the only cost difference between them is "
        "coefficient arithmetic. For one state, rk4 costs 33 cycles per step under m0plus_fast "
        "and 85 under m0plus_slow; rk38 costs 36 and 64. The cheaper of the two swaps between "
        "the multiplier models, and the cost model page uses that swap as its sanity check.",
    )),
    ("cohens-d", "Cohen's d", (
        "The effect size the hypothesis ledger attaches to a verdict: the absolute difference "
        "of two cell populations' means divided by their pooled standard deviation. When a "
        "predicate compares two populations and d is below 0.2, the verdict is inconclusive "
        "regardless of which way the comparison came out, so a true-but-tiny difference cannot "
        "be claimed as a finding.",
    )),
    ("cost-bucket", "cost bucket (cycle bucket)", (
        "A log2-spaced bin of cycles per step under m0plus_fast, used as one axis of the "
        "archive grids. Fewer than 16 cycles is bucket 0; 16 to 31 is bucket 1; 32 to 63 is "
        "bucket 2; the range doubles each bucket up to bucket 7, which collects everything at "
        "1024 cycles or more.",
    )),
    ("csd-weight", "CSD weight", (
        "The minimum number of nonzero signed power-of-two terms needed to write an integer "
        "multiplier m (its nonadjacent form). A multiply by m can be replaced by a chain of "
        "CSD-weight shifts and one fewer adds, so the cost model charges each coefficient the "
        "cheaper of that chain and a single hardware multiply. Under a 32-cycle multiplier, "
        "low-CSD-weight coefficients are what make a method cheap.",
    )),
    ("cycle-budget", "cycle budget", (
        "The fixed compute allowance every evaluation gets: 65536 cycles per problem per cost "
        "model. A method takes as many steps as fit, steps = budget // cycles_per_step, so a "
        "cheaper method integrates with more, smaller steps. All errors on this site are "
        "equal-budget comparisons, never equal-step-count ones.",
    )),
    ("directive", "directive", (
        "A JSON search instruction from the planning model: an id (D- prefix), a target order, "
        "stage counts, coefficient constraints, island count and a time budget, plus a "
        "rationale. Directives are validated against a strict schema before use and their id "
        "is stamped on every record they produce. Ids starting with D-E mark exhaustive "
        "enumeration cycles, whose elites are optimal within the enumerated space rather than "
        "search results.",
    )),
    ("dyadic-rational", "dyadic rational", (
        "A number of the form m / 2^s. These are exactly the values binary fixed-point "
        "hardware can represent and apply without error, which is why the search snaps A "
        "entries to dyadics and why every coefficient is stored and applied as an (m, s) pair. "
        "The b weights are instead solved exactly as fractions from the order conditions, "
        "since snapping them would make order 3 and above unreachable.",
    )),
    ("elite", "elite", (
        "The single record occupying one cell of a MAP-Elites grid: the verified tableau with "
        "the lowest held-out error seen so far for that (order, stages, cost bucket) "
        "combination. A new record displaces the incumbent only by a strictly lower held-out "
        "error; ties keep the earlier record.",
    )),
    ("floor-rounding", "floor rounding (ASRS)", (
        "The rounding rule of the Q15 arithmetic: a multiply computes (a * b) >> 15 with an "
        "arithmetic right shift, which rounds toward negative infinity, matching the ARM ASRS "
        "instruction. Every product loses up to one LSB, always downward, an average bias of "
        "about half an LSB per multiply that accumulates over thousands of steps. This is a "
        "deliberate modeling choice from the handoff, and its consequences are treated as "
        "findings to measure, not as bugs to fix.",
    )),
    ("held-out-set", "held-out set", (
        "The four problems that decide archive fitness but that the optimizer never sees "
        "during search: pendulum, dc_motor, rc_thermal and quaternion. The search set it does "
        "see is dahlquist, damped_osc and vanderpol_mild. heldout_error, the root-mean-square "
        "error over the held-out set at the fixed cycle budget, is what a record must lower to "
        "claim a grid cell, so overfitting to the search set does not pay.",
    )),
    ("hypothesis-ledger", "hypothesis ledger", (
        "An append-only JSONL file of falsifiable statements about the archive. Each "
        "hypothesis carries an id (H- prefix), a statement, a proposed mechanism, a control, a "
        "machine-checkable predicate over cell statistics, and a min_samples threshold. "
        "Resolution lines are appended by code once the referenced cells have enough records; "
        "verdicts (supported, refuted, inconclusive) are computed from the data and the model "
        "never writes one.",
    )),
    ("lsb", "LSB", (
        "Least significant bit: the smallest increment the Q15 format can represent, 2^-15, "
        "about 3.05e-5. It is the natural unit for quantization effects; floor rounding costs "
        "up to one LSB per multiply.",
    )),
    ("map-elites", "MAP-Elites archive", (
        "The quality-diversity structure the search fills instead of chasing a single winner: "
        "one grid per algebraic order 1 to 4, with cells keyed by (stage count 2 to 6, cost "
        "bucket 0 to 7) and each cell keeping only its elite. The output of the project is "
        "coverage of this grid, a map of what accuracy is available at each shape and cost, "
        "rather than one recommended method.",
    )),
    ("order", "order (measured vs algebraic)", (
        "Algebraic order is the largest p whose order conditions the exact coefficients "
        "satisfy, checked symbolically; it keys the archive grids. Measured order is the slope "
        "of a log-log fit of float64 final-state error against step size on the dahlquist "
        "problem, taken over the longest usable run of points (order_fit_points reports how "
        "many). The two can differ: algebraic order is a property of the fractions, measured "
        "order is evidence about actual convergence, and Q15 effects belong to neither.",
    )),
    ("q15", "Q15", (
        "Signed 16-bit fixed point: an integer q in [-32768, 32767] representing the value "
        "q / 32768, so the format covers [-1, 1) in steps of 2^-15. All problem states are "
        "stored and updated in Q15; overflow is never saturated but raises an error, which the "
        "verifier turns into a rejection. Coefficients are not Q15: they are dyadic (m, s) "
        "pairs applied by multiply and shift.",
    )),
    ("stage", "stage", (
        "One derivative evaluation inside a single step: an s-stage explicit method calls the "
        "problem right-hand side s times per step, each call fed by a weighted combination of "
        "the earlier stage results. Stage count is a major cost driver and one axis of the "
        "archive grids.",
    )),
    ("tableau", "tableau", (
        "The Butcher tableau (A, b, c) that defines a Runge-Kutta method. A is a strictly "
        "lower triangular matrix weighting earlier stage derivatives into each stage input "
        "(strict lower triangularity is what makes the method explicit), c holds each stage's "
        "time offset as a fraction of the step, and b weights the stage derivatives in the "
        "final update. On this site tableaus are always exact fractions, hashed by content.",
    )),
    ("tiers", "tier names", (
        "Every record carries one of three tier strings, assigned mechanically when it enters "
        "its cell. heldout_verified: it improved on the incumbent elite's search error and "
        "held-out error, with improvements in at least two problem families. search_only: it "
        "improved on search error but not on held-out error. unreplicated: everything else, "
        "including any record that landed in a previously empty cell, which has no incumbent "
        "to compare against.",
    )),
    ("verifier-hash", "verifier hash", (
        "A sha256 over ten pinned files in fixed order: the six scoring modules (coeffrep, "
        "orderconditions, verifier, costmodel, evaluator, problems) and four fixtures. Any "
        "byte changed in any of them changes the hash, and the container refuses to start if "
        "the computed hash differs from the pinned one. Every record stores the hash that was "
        "active when it was scored, so scores from different code can never be silently mixed.",
    )),
)


def render_glossary() -> str:
    parts = [
        '<p class="lead">Definitions for the terms used across this site, in alphabetical '
        "order. Each heading is an anchor, so other pages deep-link straight to a term; every "
        "definition states what the code actually does rather than the textbook general "
        "case.</p>"
    ]
    for anchor, term, paras in _GLOSSARY:
        parts.append(f'<h2 id="{_esc(anchor)}">{_esc(term)}</h2>')
        for p in paras:
            parts.append(f"<p>{_esc(p)}</p>")
    return _page("glossary", "\n".join(parts), active="glossary.html",
                 subtitle="Plain-language definitions of the terms this site relies on.")


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
    pages["literature.html"] = render_literature(literature_mod.load_digests())
    pages["interpretation.html"] = render_interpretation(literature_mod.load_interpretations())
    pages["costmodel.html"] = render_costmodel()
    pages["falsification.html"] = render_falsification(_load_falsification())
    pages["glossary.html"] = render_glossary()
    try:
        from rk_harness import methodology
    except ImportError:
        pass
    else:
        pages["methodology.html"] = methodology.render_page(_page)
    for name in sorted(pages.keys()):
        check_banned(pages[name])
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in sorted(pages.keys()):
        with open(out_dir / name, "wb") as fh:
            fh.write(pages[name].encode("utf-8"))
