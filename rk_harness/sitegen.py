"""Findings site generator — SPEC §Surface/sitegen.py, HANDOFF §17.

Pure HTML + inline SVG, no JavaScript, no timestamps: the same ArchiveState always produces
byte-identical files. Every page is checked against BANNED_WORDS before any file is written.

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


_NAV_ITEMS = (
    ("index.html", "overview"),
    ("costmodel.html", "cost model"),
    ("falsification.html", "falsification"),
    ("hypotheses.html", "hypotheses"),
    ("literature.html", "literature"),
    ("interpretation.html", "interpretation"),
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
    parts = [_stat_cards(arch)]
    parts.append('<p class="note">Fitness is heldout_error under m0plus_fast at equal cycle budget; '
                 "lower is better and nothing more is claimed. Cells are (stages, cycle bucket).</p>")
    parts.append("<h2>Cost against held-out error</h2>")
    parts.append('<div class="panel">' + _elite_scatter(arch) + "</div>")
    parts.append("<h2>Elite grids</h2>")
    parts.append('<div class="charts">')
    for order in sorted(arch.grids.keys()):
        if arch.grids[order]:
            parts.append('<div class="panel">' + _grid_heatmap(order, arch.grids[order]) + "</div>")
    parts.append("</div>")
    for order in sorted(arch.grids.keys()):
        grid = arch.grids[order]
        parts.append(f"<h2>Order {order} grid</h2>")
        if not grid:
            parts.append("<p>no elites yet</p>")
            continue
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
    parts.append('<div class="panel">' + _record_meta(rec) + "</div>")
    parts.append("<h2>Tableau</h2>")
    parts.append(_tableau_table(rec))
    parts.append("<h2>Per problem</h2>")
    parts.append('<div class="panel">' + _per_problem_bars(sv) + "</div>")
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


def render_hypotheses(hyps: list[dict]) -> str:
    parts = []
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
        parts.append('<div class="scroll"><table><tr><th>id</th><th>statement</th><th>mechanism</th><th>control</th>'
                     "<th>predicate</th><th>verdict</th><th>n_samples</th>"
                     "<th>effect_size</th><th>min_samples</th><th>resolved_cycle</th><th>cycle</th>"
                     "<th>rationale</th></tr>")
        for h in sorted(hyps, key=lambda d: str(d.get("id", ""))):
            verdict = h.get("verdict")
            parts.append(
                "<tr>"
                f"<td>{_esc(h.get('id', ''))}</td>"
                f"<td>{_esc(h.get('statement', ''))}</td>"
                f"<td>{_esc(h.get('mechanism', ''))}</td>"
                f"<td>{_esc(h.get('control', ''))}</td>"
                f'<td class="mono">{_esc(h.get("predicate", ""))}</td>'
                f"<td>{_verdict_badge(verdict)}</td>"
                f"<td>{_num(h.get('n_samples'))}</td>"
                f"<td>{_num(h.get('effect_size'))}</td>"
                f"<td>{_num(h.get('min_samples'))}</td>"
                f"<td>{_num(h.get('resolved_cycle'))}</td>"
                f"<td>{_num(h.get('cycle'))}</td>"
                f"<td>{_esc(h.get('rationale', ''))}</td>"
                "</tr>"
            )
        parts.append("</table></div>")
    return _page("hypothesis ledger", "\n".join(parts), active="hypotheses.html",
                 subtitle="Falsifiable statements about the archive, resolved mechanically.")


def render_costmodel() -> str:
    classical = tableau_mod.classical()
    parts = []
    parts.append("<h2>Anchor: rk4 vs rk38</h2>")
    parts.append('<div class="panel">' + _anchor_bars() + "</div>")
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
    parts.append("<h2>Model parameters</h2>")
    parts.append("<table><tr><th>model</th><th>mul</th><th>add</th><th>shift</th><th>load</th><th>store</th></tr>")
    for m in (costmodel.M0PLUS_FAST, costmodel.M0PLUS_SLOW, costmodel.AVR_APPROX):
        cy = m.cycles
        parts.append(f"<tr><td>{m.name}</td><td>{cy.get('mul')}</td><td>{cy.get('add')}</td>"
                     f"<td>{cy.get('shift')}</td><td>{cy.get('load')}</td><td>{cy.get('store')}</td></tr>")
    parts.append("</table>")
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
    if data is None:
        body = "<p>falsification experiment not run (work_dir()/falsification.json absent)</p>"
    else:
        verdict = data.get("verdict")
        parts = []
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
        rest = {k: v for k, v in data.items() if k != "verdict"}
        parts.append("<h2>Raw data</h2>")
        parts.append(_render_value(rest))
        body = "\n".join(parts)
    return _page("falsification experiment", body, active="falsification.html",
                 subtitle="HANDOFF §15 — the kill/proceed measurement, run before the search.")


def render_literature(digests: list[dict]) -> str:
    parts = [f'<p class="note">{_esc(_MODEL_NOTE)}</p>']
    if not digests:
        parts.append("<p>no literature digests yet</p>")
    for d in reversed(digests):
        entry = ['<article class="entry">']
        entry.append(f"<h2>{_esc(d.get('topic', ''))}</h2>")
        entry.append(f'<p class="when">collected {_esc(d.get("ts", ""))} at cycle {int(d.get("cycle", 0))}</p>')
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
        entry.append("</article>")
        parts.append("\n".join(entry))
    return _page("literature digest", "\n".join(parts), active="literature.html",
                 subtitle="Web-researched background that feeds the directive and hypothesis prompts.")


def render_interpretation(entries: list[dict]) -> str:
    parts = [f'<p class="note">{_esc(_MODEL_NOTE)}</p>']
    if not entries:
        parts.append("<p>no interpretation yet</p>")
    for e in reversed(entries):
        entry = ['<article class="entry">']
        entry.append(f"<h2>cycle {int(e.get('cycle', 0))}</h2>")
        entry.append(f'<p class="when">written {_esc(e.get("ts", ""))}</p>')
        for para in str(e.get("text", "")).split("\n\n"):
            if para.strip():
                entry.append(f"<p>{_esc(para.strip())}</p>")
        entry.append("</article>")
        parts.append("\n".join(entry))
    return _page("interpretation", "\n".join(parts), active="interpretation.html",
                 subtitle="Model-written readings of the archive; the numbers stay authoritative.")


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
    for name in sorted(pages.keys()):
        check_banned(pages[name])
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in sorted(pages.keys()):
        with open(out_dir / name, "wb") as fh:
            fh.write(pages[name].encode("utf-8"))
