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
from rk_harness import encourager
from rk_harness import ledger
from rk_harness import saturation
from rk_harness import tableau as tableau_mod
from rk_harness import timefmt
from rk_harness.paths import work_dir
from rk_harness.types import ArchiveState, Record

BANNED_WORDS = ("novel", "first", "beats", "outperforms", "breakthrough", "proves",
                "state-of-the-art", "best-ever")
# Provenance line, rendered quietly in the footer of every page. The footer's link to
# the rk-overview site completes the sentence, so the constant ends mid-phrase.
BANNER = ("Generated from run data by the harness; no human review. "
          "Human interpretation is at")
OVERVIEW_URL = "https://jgoetzmann.github.io/rk-overview/"
AVR_NOTE = "Cost model approximate; see HANDOFF §4.5."

_EXHAUSTIVE_LABEL = "exhaustive: optimal within the enumerated space"
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
header.site{background:var(--surface-1);border-bottom:1px solid var(--line);margin-bottom:28px}
header.site .wrap{padding-top:22px;padding-bottom:0}
h1{font-size:26px;margin:6px 0 2px;letter-spacing:-.015em}
h2{font-size:18px;margin:36px 0 12px;letter-spacing:-.01em}
h3{font-size:14px;margin:20px 0 6px;color:var(--text-2)}
p{max-width:76ch}
.sub{color:var(--text-2);margin:0 0 14px;font-size:14px}
nav.tabs{display:flex;gap:2px;flex-wrap:wrap;margin:14px 0 0}
nav.tabs a{padding:8px 14px;border-radius:8px 8px 0 0;color:var(--text-2);
  text-decoration:none;font-size:13.5px;border:1px solid transparent;border-bottom:none}
nav.tabs a:hover{color:var(--text-1);background:var(--surface-0)}
nav.tabs a.on{background:var(--surface-0);border-color:var(--line);color:var(--text-1);
  font-weight:600;box-shadow:0 1px 0 var(--surface-0)}
a{color:var(--s1)}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0;align-items:stretch}
.card{background:var(--surface-1);border:1px solid var(--line);border-radius:10px;
  padding:12px 16px;min-width:148px;flex:0 1 auto;display:flex;flex-direction:column}
.card .k{font-size:11px;color:var(--text-2);letter-spacing:.06em;text-transform:uppercase}
.card .v{font-size:26px;font-weight:650;font-variant-numeric:tabular-nums;
  letter-spacing:-.01em;line-height:1.25;margin:2px 0}
.card .d{font-size:12px;color:var(--text-3);margin-top:auto}
.panel{background:var(--surface-1);border:1px solid var(--line);border-radius:10px;
  padding:16px 18px;margin:16px 0}
figure{margin:0}
figure figcaption{font-size:13px;color:var(--text-2);margin:0 0 10px;max-width:72ch;
  line-height:1.5}
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
tr:hover>td{background:var(--grid)}
td{font-variant-numeric:tabular-nums}
th.num,td.num{text-align:right}
.tier,.badge{display:inline-block;padding:1px 8px;border-radius:99px;font-size:11.5px;
  font-weight:600;white-space:nowrap}
.tier-heldout_verified{background:var(--good-bg);color:var(--good-fg)}
.tier-search_only{background:var(--warn-bg);color:var(--warn-fg)}
.tier-unreplicated{background:var(--mut-bg);color:var(--mut-fg)}
.badge-open{background:var(--mut-bg);color:var(--mut-fg)}
.badge-supported{background:var(--good-bg);color:var(--good-fg)}
.badge-refuted{background:var(--bad-bg);color:var(--bad-fg)}
.badge-inconclusive{background:var(--warn-bg);color:var(--warn-fg)}
.badge-active{background:var(--good-bg);color:var(--good-fg)}
.badge-frozen{background:var(--mut-bg);color:var(--mut-fg)}
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
footer{margin-top:48px;padding-top:14px;border-top:1px solid var(--line);
  font-size:12px;color:var(--text-3)}
footer p{margin:3px 0;max-width:none}
footer p.prov a{color:var(--text-2)}
nav.tabs.sub2{margin-top:0;padding-bottom:2px}
nav.tabs.sub2 a{font-size:12.5px;padding:4px 12px;border-radius:7px;color:var(--text-3)}
nav.tabs.sub2 a:hover{color:var(--text-1)}
nav.tabs.sub2 a.on{background:var(--surface-0);border:1px solid var(--line);
  color:var(--text-1);font-weight:600;box-shadow:none}
nav.tabs.sub3{margin-top:0;padding-bottom:4px}
nav.tabs.sub3 a{font-size:12px;padding:3px 12px;border-radius:7px;color:var(--text-3)}
nav.tabs.sub3 a:hover{color:var(--text-1)}
nav.tabs.sub3 a.on{background:var(--surface-0);border:1px solid var(--line);
  color:var(--text-1);font-weight:600;box-shadow:none}

/* the three method classes: one accent each, slots 1-3 of the validated palette */
.card.klass{flex:1 1 280px;min-width:250px;max-width:380px;
  border-left-width:3px;border-left-style:solid}
.card.k-explicit{border-left-color:var(--s1)}
.card.k-implicit{border-left-color:var(--s2)}
.card.k-adaptive{border-left-color:var(--s3)}
.card.klass .d{margin-top:0;margin-bottom:8px}
.card.klass .n{font-size:12.5px;color:var(--text-2);margin:0 0 8px;max-width:none}
.card.klass .go{font-size:13px;margin:auto 0 0;max-width:none}
/* A class with nothing measured yet states that in words. Set smaller and in
   the secondary colour so it does not read as a headline figure sitting beside
   one, which is the comparison the hub row invites. */
.card.unmeasured .v{font-size:17px;font-weight:550;color:var(--text-2);letter-spacing:0}

/* one-line ledger rows: the summary carries the whole record, the body carries prose */
.ledger{min-width:820px}
.ledhead,details.led>summary{display:grid;
  grid-template-columns:14px 78px 104px 1fr 58px 62px 52px;
  gap:12px;align-items:center;padding:6px 12px}
.ledhead{font-size:11px;color:var(--text-2);font-weight:600;letter-spacing:.04em;
  text-transform:uppercase}
details.led{background:var(--surface-1);border:1px solid var(--line);border-radius:8px;
  margin:4px 0;font-size:12.5px}
details.led>summary{cursor:pointer;list-style:none;font-variant-numeric:tabular-nums}
details.led>summary::-webkit-details-marker{display:none}
details.led>summary::before{content:"+";color:var(--text-3);font-weight:600}
details.led[open]>summary::before{content:"\\2212"}
details.led[open]>summary{border-bottom:1px solid var(--line)}
details.led>div{padding:10px 16px 12px 40px}
details.led .pred{font:12px ui-monospace,Consolas,monospace;color:var(--text-2);
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
details.led .num,.ledhead .num{text-align:right}
details.led .badge{justify-self:start}
details.led .mono{font-size:12px;color:var(--text-2)}
details.led .rep{color:var(--text-3);font-weight:600}
details.led table{font-size:12px;margin:6px 0 0}
details.repeats{margin:10px 0;font-size:13px;color:var(--text-2);border:1px solid var(--line);
  border-radius:8px;background:var(--surface-0)}
details.repeats>summary{cursor:pointer;padding:6px 12px;color:var(--text-3);font-size:12.5px}
details.repeats>summary:hover{color:var(--text-1)}
details.repeats[open]>summary{border-bottom:1px solid var(--line);color:var(--text-2)}
details.repeats>div{padding:4px 12px 8px}
.hash a,a.hash{font:12px ui-monospace,Consolas,monospace}
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


def _cards(rows) -> str:
    """A row of stat cards from (key, value, description) triples.

    One markup definition for every card row on the site. Values and descriptions are
    plain text and are escaped here, so no caller can put markup in a card by accident.
    An optional fourth element adds CSS classes to that card.
    """
    out = []
    for row in rows:
        key, value, desc = row[0], row[1], row[2]
        extra = f" {row[3]}" if len(row) > 3 and row[3] else ""
        out.append(f'<div class="card{extra}"><div class="k">{_esc(key)}</div>'
                   f'<div class="v">{_esc(value)}</div>'
                   f'<div class="d">{_esc(desc)}</div></div>')
    return '<div class="cards">' + "".join(out) + "</div>"


# The three method classes, in the order every page iterates them. Explicit leads
# because it is the class the verifier scores; the other two follow in a fixed order so
# the nav, the index cards and the page bodies cannot disagree about which is which.
CLASS_ORDER: tuple[str, ...] = ("explicit", "implicit", "adaptive")
_CLASS_PAGE = {cls: f"{cls}.html" for cls in CLASS_ORDER}


def _class_cards(rows) -> str:
    """The index's class row: one card per method class, each with one traced number.

    rows are (class, headline value, what the number is, the file it came from, blurb).
    A source of None means the document this card would have read has not been written,
    and then the card carries no number at all: `value` is a statement of absence and
    `what` says which document is missing. A zero here would be read as a measurement
    that came back empty, which is a different claim from not having measured, and the
    hub is the one page where the three classes sit side by side to be compared.
    """
    out = []
    for cls, value, what, source, blurb in rows:
        desc = _esc(what) if source is None else f"{_esc(what)}, from {_esc(source)}"
        klass = "card klass k-" + _esc(cls) + ("" if source is not None else " unmeasured")
        out.append(
            f'<div class="{klass}">'
            f'<div class="k">{_esc(cls)}</div>'
            f'<div class="v">{_esc(value)}</div>'
            f'<div class="d">{desc}</div>'
            f'<p class="n">{_esc(blurb)}</p>'
            f'<p class="go"><a href="{_esc(_CLASS_PAGE[cls])}">{_esc(cls)} methods</a></p>'
            "</div>")
    return '<div class="cards">' + "".join(out) + "</div>"


# Three tiers. Tier 1 is the overview and the three method classes, in the order the
# site leads with them, so each class is one click from anywhere and the three carry the
# same weight in the same row. Tier 2 is the evidence that spans classes. Tier 3 is the
# record and the reference.
_NAV_ITEMS = (
    ("index.html", "overview", 1),
    ("explicit.html", "explicit", 1),
    ("implicit.html", "implicit", 1),
    ("adaptive.html", "adaptive", 1),
    ("validation.html", "validation", 2),
    ("benchmark.html", "benchmark", 2),
    ("hypotheses.html", "hypotheses", 2),
    ("falsification.html", "falsification", 2),
    ("methodology.html", "methodology", 3),
    ("costmodel.html", "cost model", 3),
    ("sidetrack.html", "measurement ledger", 3),
    ("literature.html", "literature", 3),
    ("interpretation.html", "interpretation", 3),
    ("glossary.html", "glossary", 3),
)

# The pages whose source file may be absent: validation.html needs
# work_dir()/validation/results.json, benchmark.html needs benchmark/results.json and
# sidetrack.html needs sidetrack/ledger.jsonl. build() raises _PRESENT to the hrefs it
# actually wrote and clears it in the finally, so every page's nav matches the pages on
# disk. A set rather than one module boolean per page: at fourteen entries a boolean
# each stopped scaling, and a new conditional page no longer needs a new global.
_CONDITIONAL = frozenset({"validation.html", "benchmark.html", "sidetrack.html"})
_PRESENT: frozenset[str] = frozenset()


def _nav(active: str) -> str:
    items = [(href, label, tier) for href, label, tier in _NAV_ITEMS
             if href not in _CONDITIONAL or href in _PRESENT]

    def row(tier: int) -> str:
        return "".join(
            f'<a href="{href}"{" class=" + chr(34) + "on" + chr(34) if href == active else ""}>{_esc(label)}</a>'
            for href, label, t in items if t == tier)
    return (f'<nav class="tabs">{row(1)}</nav>'
            f'<nav class="tabs sub2">{row(2)}</nav>'
            f'<nav class="tabs sub3">{row(3)}</nav>')


def _page(title: str, body: str, active: str = "", subtitle: str = "") -> str:
    sub = f'<p class="sub">{_esc(subtitle)}</p>' if subtitle else ""
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_esc(title)}</title>\n"
        f"<style>{_STYLE}</style>\n</head>\n<body>\n"
        '<header class="site"><div class="wrap">\n'
        f"<h1>{_esc(title)}</h1>\n{sub}\n"
        f"{_nav(active)}\n"
        "</div></header>\n"
        '<div class="wrap">\n'
        f"{body}\n"
        "<footer>\n"
        "<p>rk-harness findings: Q15 fixed point, equal cycle budget, Cortex-M0+ cost models.</p>\n"
        f'<p class="prov">{_esc(BANNER)} <a href="{OVERVIEW_URL}">the rk-overview site</a>.</p>\n'
        "</footer>\n"
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


def _tick_stride(n_ticks: int, span_px: float, needed_px: float) -> int:
    """Label every k-th tick so labeled neighbors sit at least needed_px apart.

    Chart-fit rule: an 11px tick label needs its pitch to clear the label's own
    footprint plus a 4px gutter, otherwise adjacent labels collide (the audit
    flags any pair closer than 4px). Gridlines are unaffected; only labels thin.
    """
    if n_ticks < 2 or span_px <= 0:
        return 1
    pitch = span_px / (n_ticks - 1)
    return max(1, math.ceil(needed_px / pitch))


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
        xticks = _log_ticks(self.xlo, self.xhi)
        yticks = _log_ticks(self.ylo, self.yhi)
        # Chart-fit audit fixes. Labels thin to every k-th decade when the pitch is
        # too tight (gridlines keep every decade); the bottom y label nudges up to
        # clear the x tick row in the axis corner; the x-axis title sits 2px lower
        # so its box clears the tick labels by the 4px minimum.
        xw = max((len(_pow_label(t)) for t in xticks), default=1) * 7.0 + 4.0
        xk = _tick_stride(len(xticks), self.w - self.ml - self.mr, xw)
        # 21px keeps labeled y neighbors 4px clear even of the nudged bottom label.
        yk = _tick_stride(len(yticks), self.h - self.mt - self.mb, 21.0)
        for i, tv in enumerate(xticks):
            px = self.x(tv)
            self.parts.append(f'<line class="gridline" x1="{_fmt(px)}" y1="{self.mt}" x2="{_fmt(px)}" y2="{self.h - self.mb}"/>')
            if i % xk == 0:
                self.parts.append(f'<text x="{_fmt(px)}" y="{self.h - self.mb + 14}" text-anchor="middle">{_pow_label(tv)}</text>')
        for i, tv in enumerate(yticks):
            py = self.y(tv)
            self.parts.append(f'<line class="gridline" x1="{self.ml}" y1="{_fmt(py)}" x2="{self.w - self.mr}" y2="{_fmt(py)}"/>')
            if i % yk == 0:
                ly = min(py + 3.5, self.h - self.mb - 2.0)
                self.parts.append(f'<text x="{self.ml - 6}" y="{_fmt(ly)}" text-anchor="end">{_pow_label(tv)}</text>')
        self.parts.append(f'<line class="axis" x1="{self.ml}" y1="{self.h - self.mb}" x2="{self.w - self.mr}" y2="{self.h - self.mb}"/>')
        self.parts.append(f'<line class="axis" x1="{self.ml}" y1="{self.mt}" x2="{self.ml}" y2="{self.h - self.mb}"/>')
        self.parts.append(f'<text x="{_fmt((self.ml + self.w - self.mr) / 2)}" y="{self.h - 4}" text-anchor="middle">{_esc(self.xlabel)}</text>')
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
                 f"({rec.tier}); {rec.tableau_hash[:12]}")
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
    return ('<figure><figcaption>One mark per method: blue dots are archive elites, orange '
            "diamonds are classical baselines. The x axis is analytic cycles per step under "
            "m0plus_fast and the y axis is held-out error at the fixed budget; both scales are "
            "log. Cheaper methods take more, smaller steps inside the same "
            + _gloss("cycle-budget", "cycle budget") + ", so down-left is better on both axes; "
            "a baseline diamond gets a vertical position only when its identical tableau is "
            "archived, and every dot links to its cell page.</figcaption>"
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
    # Rows cover the default stage range unioned with every stage actually occupied in
    # this grid, so an elite outside 2..6 (order 1's single-stage cell, say) stays visible.
    stage_rows = tuple(sorted(set(_HEAT_STAGES) | {s for (s, _b) in grid.keys()}))
    cw, ch, ml, mt = 52, 34, 58, 22
    w = ml + cw * len(_HEAT_BUCKETS) + 8
    h = mt + ch * len(stage_rows) + 26
    parts = []
    for j, b in enumerate(_HEAT_BUCKETS):
        parts.append(f'<text x="{_fmt(ml + cw * j + cw / 2)}" y="{mt - 7}" text-anchor="middle">b{b}</text>')
    for i, s in enumerate(stage_rows):
        parts.append(f'<text x="{ml - 8}" y="{_fmt(mt + ch * i + ch / 2 + 3.5)}" text-anchor="end">s={s}</text>')
    for i, s in enumerate(stage_rows):
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
                f'text-anchor="middle">{f"{float(err):.3g}" if _finite_pos(err) else "?"}</text></a>')
    parts.append(f'<text x="{ml}" y="{h - 6}">cycle bucket (m0plus_fast)</text>')
    svg = (f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
           f'aria-label="Order {order} elite grid heatmap">' + "".join(parts) + "</svg>")
    return (f'<figure><figcaption><strong>Order {order}</strong></figcaption>{svg}</figure>')


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
    return ("<figure><figcaption>Analytic cycles per step for rk4 and rk38 under the fast and "
            "slow multiplier cost models. Bar height is per-step cost; the printed number is "
            "the exact cycle count. The cheaper of the two "
            + _gloss("anchor-methods", "anchor methods") + " swaps between the multiplier "
            "models, the sanity check the whole cost model hangs on.</figcaption>"
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
    return (f"<figure><figcaption>{_esc(name)}: final-state error against step size h, both "
            "axes log scale, for the Q15 and float64 integrations over identical steps. The "
            "dashed vertical line, where present, marks the measured crossover step size: to "
            "its left, " + _gloss("floor-rounding", "floor rounding") + " loses more per extra "
            "step than smaller steps recover, so the Q15 line turns back up while float64 "
            "keeps falling. Hover any point for exact values.</figcaption>"
            f"{svg}</figure>")


# Estimated advance width of one character of an 11px semibold .lbl value label,
# used to keep bar-value labels inside the drawable width. Deliberately conservative.
_LBL_CHAR_W = 6.6


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
        # Value label: outside the bar end unless it would run past the drawable width,
        # in which case it sits end-anchored inside the bar (the .lbl halo keeps it legible).
        label = _num(v)
        bar_end = ml + max(bw, 2)
        if bar_end + 6 + len(label) * _LBL_CHAR_W > w - 6:
            parts.append(f'<text class="lbl" x="{_fmt(bar_end - 6)}" y="{_fmt(y + 13)}" '
                         f'text-anchor="end">{label}</text>')
        else:
            parts.append(f'<text class="lbl" x="{_fmt(bar_end + 6)}" y="{_fmt(y + 13)}">{label}</text>')
    svg = (f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
           'aria-label="Per-problem error, log scale">' + "".join(parts) + "</svg>")
    return ('<figure><figcaption>Final-state error of this tableau on each problem, integrated '
            "in Q15 under m0plus_fast at the fixed cycle budget. Bar length is error on a log "
            "scale; the printed value is exact. dahlquist, damped_osc and vanderpol_mild are "
            "the search set the optimizer sees; the other four are the "
            + _gloss("held-out-set", "held-out set") + " that decides archive fitness, and on "
            "several problems " + _gloss("floor-rounding", "floor rounding") + " dominates the "
            "method choice, so bars can look similar across very different "
            "tableaus.</figcaption>"
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
                    f'<td class="num">{r.m}</td><td class="num">{r.s}</td><td class="mono">{r.m}/2^{r.s}</td>'
                    f"<td>{'yes' if r.exact else 'no'}</td><td class=\"num\">{r.csd_weight}</td></tr>")
    body = [
        "<p>The Q15 integrator applies each nonzero A and b entry to a state value v as "
        "(v * m) &gt;&gt; s, an arithmetic right shift that "
        + _gloss("floor-rounding", "floors") + ". Zero entries are skipped. "
        "When exact is no, m/2^s only approximates the fraction and the largest such gap is "
        "the record's coeff_quant_error. " + _gloss("csd-weight", "CSD weight")
        + " is the length of the shift-add chain the cost model may charge for the multiply "
        "by m.</p>",
        '<div class="scroll"><table><tr><th>entry</th><th>exact value</th><th class="num">m</th>'
        '<th class="num">s</th><th>m/2^s</th><th>exact</th><th class="num">csd weight</th></tr>',
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
    # Numerator and denominator over one domain. An order can only use stage counts that can
    # reach it, so the denominator is not the same for every order: a two-stage cell in the
    # order-4 grid is not an empty cell, it is an impossible one. The grid can also hold a cell
    # outside the searchable stage range (the seeded single-stage order-1 baseline); that is
    # stated next to the fraction rather than counted into it.
    domains = {o: encourager.stage_domain(o) for o in sorted(arch.grids)}
    cells = sum(len(d) * 8 for d in domains.values())
    occupied = sum(1 for o, g in arch.grids.items()
                   for (s, _b) in g if s in domains.get(o, ()))
    outside = len(elites) - occupied
    coverage = "grid coverage, all orders"
    if outside:
        coverage += f"; {outside} seeded outside the searched stage range"
    cards = [
        ("records", _num(arch.n_records), "verified tableaus archived"),
        ("last cycle id", _num(arch.last_cycle_id), "cycle ids start at 0"),
        ("elite cells", f"{occupied}/{cells}", coverage),
        ("heldout_verified", str(verified), "top tier at insertion"),
        ("hypotheses", f"{len(arch.open_hypotheses)} open", f"{len(arch.refuted_hypotheses)} refuted"),
    ]
    return _cards(cards)


# ----------------------------------------------------------------------------
# epoch-status panel (the public progress loop, HANDOFF-era determinism kept)
# ----------------------------------------------------------------------------

# Display names for saturation.scan_progress kinds. Progress is defined in
# docs/ROADMAP.md and implemented in rk_harness/saturation.py.
_PROGRESS_KIND_LABEL = {
    "new_cell": "a record landed in a previously empty grid cell",
    "elite_improvement": "a cell elite improved its held-out error",
    "heldout_verified": "an acceptance at the heldout_verified tier",
}


def _load_json_or_none(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def epoch_status_data() -> dict:
    """Progress-loop state, read from the files in work_dir().

    A pure function of the files on disk (events.jsonl, saturation_state.json,
    EPOCH_STATUS.json, falsification.json presence): no wall clock is read, so pages
    built from the same files are byte-identical. Also imported by the rk-overview
    generator so both sites report the same state.
    """
    wd = work_dir()
    status = _load_json_or_none(wd / saturation.EPOCH_FILE)
    state = _load_json_or_none(wd / saturation.STATE_FILE) or {}
    prog = saturation.scan_progress()
    try:
        consecutive = int(state.get("consecutive", 0))
    except (TypeError, ValueError):
        consecutive = 0
    return {
        "epoch": int(status.get("epoch", 1)) if status else 1,
        "state": "frozen" if status else "active",
        "frozen_at": status.get("frozen_at") if status else None,
        "freeze_reason": status.get("reason") if status else None,
        "n_accepted": prog.get("n_accepted"),
        "last_progress_ts": prog.get("last_progress_ts"),
        "last_progress_kind": prog.get("last_progress_kind"),
        "consecutive": consecutive,
        "consecutive_needed": saturation._consecutive_needed(),
        "last_check_ts": state.get("last_check"),
        "last_verdict": state.get("last_verdict"),
        "falsification_present": (wd / "falsification.json").exists(),
    }


def _epoch_panel(data: dict | None = None) -> str:
    d = data if data is not None else epoch_status_data()
    state = d.get("state", "active")
    badge = f'<span class="badge badge-{_esc(state)}">{_esc(state)}</span>'
    head = (f'<p style="margin:0 0 6px"><strong>Epoch {_num(d.get("epoch"))}</strong> '
            f"{badge} "
            '<span class="when">scored method class: explicit fixed-step Runge-Kutta</span></p>')
    rows: list[tuple[str, str]] = []
    if state == "frozen":
        rows.append(("frozen at", _esc(_ct(d.get("frozen_at")))))
        if d.get("freeze_reason"):
            rows.append(("reason", _esc(d.get("freeze_reason"))))
    kind = d.get("last_progress_kind")
    if d.get("last_progress_ts") and kind:
        label = _PROGRESS_KIND_LABEL.get(kind, str(kind))
        rows.append(("last progress", f"{_esc(_ct(d.get('last_progress_ts')))}: {_esc(label)} "
                                      f'<span class="when">({_esc(kind)})</span>'))
    else:
        rows.append(("last progress", "no progress events recorded yet"))
    needed = d.get("consecutive_needed")
    rows.append(("saturation counter",
                 f"{_num(d.get('consecutive'))} consecutive saturating checks; "
                 f"{_num(needed)} trigger a freeze"))
    if d.get("last_check_ts"):
        verdict = d.get("last_verdict")
        rows.append(("last check", _esc(_ct(d.get("last_check_ts")))
                     + (f", verdict {_esc(verdict)}" if verdict else "")))
    rows.append(("falsification file",
                 "present" if d.get("falsification_present") else "not yet produced"))
    dl = '<dl class="meta">\n' + "\n".join(
        f"<dt>{_esc(k)}</dt><dd>{v}</dd>" for k, v in rows) + "\n</dl>"
    note = ('<p class="note">Progress means a record in a previously empty cell, an elite '
            "improving its cell, or a heldout_verified acceptance. When the newest progress "
            "event is older than the saturation window and the falsification file exists, a "
            "check counts as saturating; enough consecutive saturating checks freeze the "
            "epoch and re-pin the verifier. Read from the run state files; timestamps are "
            "stored UTC, shown as US Central.</p>")
    return '<div class="panel">' + head + dl + note + "</div>"


# ----------------------------------------------------------------------------
# off-list documents: named on the page, never quoted from
# ----------------------------------------------------------------------------

# Some documents describe a method class in detail and still may not put a number on a
# public page. The traceability rule names four sources, and the two-axis document, the
# lane archives and the shares document each repeat the same sentence inside their own
# schema, saying the document is not on that list. So a class page names such a
# document, says whether it exists yet, and publishes none of its numbers.
_OFF_LIST_RULE = (
    "The traceability rule lists key_findings.json, validation/results.json, "
    "benchmark/results.json and the side-track ledger with its artifacts. Each document "
    "named above sits outside that list and says so in its own schema, so this page "
    "names it and reports whether it exists without publishing a number from it. "
    "Admitting one to the list is a decision for the owner, not for the page generator.")


def _offlist_panel(rows) -> str:
    """One row per off-list document: its path, whether it is written, what it holds."""
    body = ['<div class="scroll"><table><tr><th>document</th><th>state</th>'
            "<th>what it holds</th></tr>"]
    for rel, present, holds in rows:
        body.append(f'<tr><td class="mono">{_esc(rel)}</td>'
                    f"<td>{'written' if present else 'not written yet'}</td>"
                    f"<td>{_esc(holds)}</td></tr>")
    body.append("</table></div>")
    return ('<div class="panel">' + "\n".join(body)
            + f'<p class="note">{_esc(_OFF_LIST_RULE)}</p></div>')


# ----------------------------------------------------------------------------
# pages
# ----------------------------------------------------------------------------

_CLASS_BOUNDARY = (
    "The scored archive holds one method class: explicit fixed-step Runge-Kutta "
    "tableaus. The verifier accepts a strictly lower triangular A and a single b, and a "
    "record has nowhere to put a second b vector, a diagonal entry or a Newton iteration "
    "count, so implicit and adaptive methods are measured outside the archive and are "
    "never ranked against it.")


def _evidence_row(validation, benchmark, sidetrack) -> str:
    """The cross-class evidence links, one line each, present pages only."""
    head = []
    if validation is not None:
        head.append(("validation.html", "practical validation",
                     "application-domain problems no search ever saw, at one budget"))
    if benchmark is not None:
        head.append(("benchmark.html", "library benchmark",
                     "measured wall clock next to the analytic cycle model"))
    if sidetrack is not None:
        head.append(("sidetrack.html", "measurement ledger",
                     "every off-archive point, its code hash and its artifact"))
    items = [
        ("hypotheses.html", "hypothesis ledger",
         "predicates the planning model committed to, resolved by code"),
        ("falsification.html", "falsification experiment",
         "the kill-or-proceed measurement that ran before any searching"),
        ("methodology.html", "methodology",
         "how a candidate becomes a record, and what each number means"),
        ("costmodel.html", "cost model",
         "analytic cycles per step, the currency every budget is priced in"),
    ]
    out = ["<ul>"]
    for href, label, blurb in head + items:
        out.append(f'<li><a href="{href}">{_esc(label)}</a>: {_esc(blurb)}</li>')
    out.append("</ul>")
    return "\n".join(out)


def _ledger_class_card(cls: str, sidetrack, blurb: str) -> tuple:
    """One hub card for a class whose evidence is the measurement ledger.

    Three states, and the difference between the last two is the point: the ledger is
    missing, the ledger exists and holds nothing for this class, or it holds points.
    Only the third carries a number. A fresh work directory is in the first state, and
    that is the state the site ships in at the start of an epoch, so it is the one the
    wording has to get right.
    """
    if not isinstance(sidetrack, dict) or not (sidetrack.get("ledger") or []):
        return (cls, "not measured", "no measurement ledger has been written yet",
                None, blurb)
    points = _distinct_points(_track_entries(sidetrack, cls))
    if not points:
        return (cls, "not measured", "the ledger records no points for this class yet",
                None, blurb)
    return (cls, str(points), "measured ledger points", "the measurement ledger", blurb)


def render_index(arch: ArchiveState, benchmark: dict | None = None,
                 validation: dict | None = None, sidetrack: dict | None = None) -> str:
    """The hub: three classes, the boundary between them, and where to read each one.

    This page carried the archive as well as the hub until the site gave the three
    classes a page each; the grids, the scatter and the elite table are now on
    explicit.html and this page links to them.
    """
    parts = [
        '<p class="lead">An automated search for integration methods that hold up in '
        + _gloss("q15", "Q15") + " fixed-point arithmetic on small microcontrollers. The "
        "run covers three method classes: <strong>explicit</strong> Runge-Kutta tableaus, "
        "which the verifier scores and the archive holds; <strong>implicit</strong> "
        + _gloss("sdirk", "SDIRK") + " methods, which solve an equation at each stage and "
        "stay stable where an explicit method cannot; and <strong>adaptive</strong> "
        + _gloss("embedded-pair", "embedded pairs") + ", which choose their own step size "
        "from an error estimate. Each class has its own page below. Terms are defined in "
        'the <a href="glossary.html">glossary</a>.</p>'
    ]
    parts.append(f'<p class="note">{_esc(_CLASS_BOUNDARY)}</p>')
    parts.append(_epoch_panel())
    parts.append("<h2>The three classes</h2>")
    if arch.n_records:
        rows = [("explicit", _num(arch.n_records), "archive records", "the run archive",
                 "Scored, archived and ranked at an equal cycle budget.")]
    else:
        rows = [("explicit", "not started", "the run archive holds no records yet", None,
                 "Scored, archived and ranked at an equal cycle budget.")]
    stiff_gap = None
    if isinstance(validation, dict):
        v = validation.get("verdicts") or {}
        no_fin = v.get("stiff_problems_with_no_discovered_finisher")
        total = v.get("stiff_problems_total")
        if isinstance(no_fin, int) and isinstance(total, int):
            stiff_gap = f"{no_fin} of {total}"
    imp_blurb = ("Off-archive by construction, and measured because the explicit class "
                 "runs out of stability before it runs out of budget.")
    if stiff_gap is not None:
        rows.append(("implicit", stiff_gap,
                     "stiff problems no discovered method finishes",
                     "validation/results.json", imp_blurb))
    else:
        rows.append(_ledger_class_card("implicit", sidetrack, imp_blurb))
    rows.append(_ledger_class_card(
        "adaptive", sidetrack,
        "Off-archive by construction: a record has no room for the second b "
        "vector an error estimate needs."))
    parts.append(_class_cards(rows))
    parts.append('<p class="note">Each card carries one number and names the file it was '
                 "read from. The class pages carry the rest, and each says plainly where "
                 "its class has not been measured yet.</p>")
    parts.append("<h2>Evidence that spans the classes</h2>")
    parts.append(_evidence_row(validation, benchmark, sidetrack))
    speed = _speed_sentence(benchmark)
    if speed:
        parts.append(f"<p>{speed}</p>")
    return _page("rk-harness findings", "\n".join(parts), active="index.html",
                 subtitle="Explicit, implicit and adaptive integrators in Q15 fixed point.")


_EXPLICIT_CLASS = (
    "An explicit Runge-Kutta method has a strictly lower triangular A, so every stage "
    "input is a finished sum of stages already computed and a step is a fixed sequence of "
    "multiply-accumulates with no equation to solve. It carries a single b vector, so it "
    "has no error estimate of its own and no step-size controller, and its step count is "
    "settled before the run: the cycle budget divided by the cost of one step. This is "
    "the class the pinned verifier accepts, and the only class with an archive cell.")


def render_explicit(arch: ArchiveState, validation: dict | None = None,
                    benchmark: dict | None = None) -> str:
    """The explicit class: the archive, the grids and every elite.

    This body was index.html until the site gave the three classes a page each. The cell
    pages keep their filenames, so every link into a cell still resolves.
    """
    parts = [
        '<p class="lead">This page is the archive: every verified explicit '
        + _gloss("tableau", "tableau") + " the search has kept, arranged in a "
        + _gloss("map-elites", "MAP-Elites grid") + " by algebraic order, stage count and "
        "cost. The tables and charts below are generated from that archive and each entry "
        "links to a detail page carrying the whole record.</p>",
        f'<p class="note">{_esc(_EXPLICIT_CLASS)}</p>',
    ]
    parts.append(_stat_cards(arch))
    parts.append('<p class="note">Fitness is heldout_error under m0plus_fast at equal cycle budget; '
                 "lower is better and nothing more is claimed. Cells are (stages, cycle bucket).</p>")
    speed = _speed_sentence(benchmark)
    if speed:
        parts.append(f"<p>{speed}</p>")
    parts.append("<h2>Cost against held-out error</h2>")
    parts.append('<div class="panel">' + _elite_scatter(arch) + "</div>")
    parts.append("<h2>Elite grids</h2>")
    parts.append('<p class="note">Rows are stage counts, columns are m0plus_fast cycle buckets, '
                 "and each filled cell prints its elite's held-out error to 3 significant "
                 "figures. Deeper blue is lower error, scaled within each grid. Hover a cell "
                 "for the exact value, click it for the record.</p>")
    parts.append(_explain(
        "The archive is a " + _gloss("map-elites", "MAP-Elites") + " structure: one grid per "
        "algebraic " + _gloss("order", "order") + " 1 to 4, whose cells are keyed by (stage count, "
        "cycle bucket). A cell holds at most one record, its " + _gloss("elite", "elite") + ": the "
        "verified tableau with the lowest held-out error seen so far for that shape and cost; ties "
        "keep the earlier record. An empty cell means no verified tableau has landed there yet.",
        _gloss("stage", "Stage") + " rows span 2 to 6 by default, extended with a row for any "
        "other stage count that holds an elite (order 1's single-stage cell appears as s=1), "
        "and the " + _gloss("cost-bucket", "cycle buckets") + " are log2-spaced bins of the m0plus_fast "
        "cycles per step: bucket 0 means fewer than 16 cycles, bucket 1 is 16 to 31, bucket 2 "
        "is 32 to 63, doubling each column, and bucket 7 collects everything at 1024 cycles or "
        "more."))
    parts.append('<div class="charts">')
    for order in sorted(arch.grids.keys()):
        if arch.grids[order]:
            parts.append('<div class="panel">' + _grid_heatmap(order, arch.grids[order]) + "</div>")
    parts.append("</div>")
    parts.append("<h2>Every elite in one table</h2>")
    parts.append('<p class="note">One row per occupied cell, every order together so cells can be '
                 "compared across orders. The hash links to the full record. A large gap between "
                 "search_error and heldout_error is overfitting to the visible search set; "
                 + _gloss("order", "measured_order") + " and the " + _gloss("tiers", "tier")
                 + " badge are defined in the glossary.</p>")
    rows: list[str] = []
    vhashes: dict[str, int] = {}
    for order in sorted(arch.grids.keys()):
        for (stg, bucket) in sorted(arch.grids[order].keys()):
            rec = arch.grids[order][(stg, bucket)]
            sv = rec.score
            vhashes[rec.verifier_hash] = vhashes.get(rec.verifier_hash, 0) + 1
            rows.append(
                "<tr>"
                f"<td>{order}</td><td>{stg}</td><td>{bucket}</td>"
                f'<td class="hash"><a href="{_cell_file(order, stg, bucket)}" '
                f'title="{_esc(rec.tableau_hash)}">{_esc(rec.tableau_hash[:12])}</a></td>'
                f"<td>{_tier_badge(rec.tier)}</td>"
                f'<td class="num">{_num(sv.heldout_error)}</td><td class="num">{_num(sv.search_error)}</td>'
                f'<td class="num">{_num(sv.cycles.get("m0plus_fast"))}</td>'
                f'<td class="num">{_num(sv.cycles.get("m0plus_slow"))}</td>'
                f'<td class="num">{_num(sv.measured_order)}</td>'
                f"<td>{_esc(_phase_label(rec))}</td>"
                "</tr>"
            )
    if not rows:
        parts.append("<p>no elites yet</p>")
    else:
        parts.append('<div class="scroll"><table>\n'
                     '<tr><th>order</th><th>stages</th><th>bucket</th><th>tableau_hash</th>'
                     '<th>tier</th><th class="num">heldout_error</th><th class="num">search_error</th>'
                     '<th class="num">cycles fast</th><th class="num">cycles slow</th>'
                     '<th class="num">measured_order</th><th>label</th></tr>'
                     + "".join(rows) + "</table></div>")
        vlist = ", ".join(f'<span class="hash">{_esc(h)}</span> ({n} '
                          + ("row" if n == 1 else "rows") + ")"
                          for h, n in sorted(vhashes.items(), key=lambda kv: (-kv[1], kv[0])))
        parts.append('<p class="note">Scored under '
                     + ("one " if len(vhashes) == 1 else f"{len(vhashes)} ")
                     + _gloss("verifier-hash", "verifier hash")
                     + ("" if len(vhashes) == 1 else "es") + ": " + vlist
                     + ". Each record's own hash is on its detail page.</p>")
    parts.append("<h2>Where else this class is measured</h2>")
    where = []
    if validation is not None:
        where.append('<li><a href="validation.html">Practical validation</a>: these '
                     "tableaus on application-domain problems no optimizer saw, in Q15 at "
                     "the shared cycle budget. The numbers live there rather than being "
                     "restated here.</li>")
    if benchmark is not None:
        where.append('<li><a href="benchmark.html">Library benchmark</a>: measured wall '
                     "clock for the same pinned fixed-step Q15 path, next to the analytic "
                     "cycle model this page ranks by.</li>")
    where.append('<li><a href="costmodel.html">Cost model</a>: the five instruction costs '
                 "every cycle number on this page is built from.</li>")
    where.append('<li><a href="methodology.html">Methodology</a>: how a candidate tableau '
                 "becomes a record, and what each score field means.</li>")
    parts.append("<ul>" + "".join(where) + "</ul>")
    parts.append(_offlist_panel([
        ("rk-work/validation/axes.json", _load_validation_axes() is not None,
         "the two-axis document: this class at a fixed budget, and the same class "
         "measured by cycles to a tolerance"),
    ]))
    return _page("explicit methods", "\n".join(parts), active="explicit.html",
                 subtitle="Explicit Runge-Kutta tableaus scored end-to-end in Q15 at a "
                          "fixed cycle budget.")


def render_cell(order: int, stages: int, bucket: int, rec: Record) -> str:
    sv = rec.score
    parts = []
    parts.append(f'<p class="sub">grid order {order}, {stages} stages, cycle bucket {bucket}</p>')
    parts.append(
        '<p class="lead">This page is the full archive record for the '
        + _gloss("elite", "elite") + f" of one grid cell: algebraic order {order}, "
        f"{stages} stages, " + _gloss("cost-bucket", "cycle bucket") + f" {bucket}. "
        'Everything here was produced by the pinned verifier; see the '
        '<a href="explicit.html">explicit archive</a> for where this cell sits in '
        "the grids. The "
        + _gloss("tiers", "tier") + " and phase label are assigned mechanically, and "
        + _gloss("verifier-hash", "verifier_hash") + " pins the exact scoring code.</p>")
    parts.append('<div class="panel">' + _record_meta(rec) + "</div>")
    parts.append("<h2>Tableau</h2>")
    parts.append(_tableau_table(rec))
    parts.append('<p class="note">All values are exact fractions; the Q15 integrator applies '
                 "each one as a " + _gloss("dyadic-rational", "dyadic") + " m/2^s pair, listed "
                 "below. The " + _gloss("tableau", "tableau") + " layout is defined in the "
                 "glossary.</p>")
    parts.append(_coeff_rep_details(rec))
    parts.append("<h2>Per problem</h2>")
    parts.append('<div class="panel">' + _per_problem_bars(sv) + "</div>")
    parts.append("<h2>Cycle counts (n_states = 1)</h2>")
    parts.append('<table><tr><th>model</th><th class="num">cycles</th><th>note</th></tr>')
    for name in ("m0plus_fast", "m0plus_slow", "avr_approx"):
        note = f'<span class="note">{_esc(AVR_NOTE)}</span>' if name == "avr_approx" else ""
        parts.append(f'<tr><td>{name}</td><td class="num">{_num(sv.cycles.get(name))}</td><td>{note}</td></tr>')
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
    parts.append('<table><tr><th>metric</th><th class="num">value</th></tr>')
    for k, v in score_rows:
        parts.append(f'<tr><td>{k}</td><td class="num">{_num(v)}</td></tr>')
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
    parts.append("<h3>Every per-problem error, by cost model</h3>")
    parts.append(_per_problem_matrix(sv))
    title = f"cell p{order} s{stages} b{bucket}"
    return _page(title, "\n".join(parts), active="explicit.html")


_PROBLEM_ROWS = ("dahlquist", "damped_osc", "vanderpol_mild",
                 "pendulum", "dc_motor", "rc_thermal", "quaternion")
_AGG_ROWS = ("search_error", "heldout_error")
_MODEL_COLS = ("", "slow", "avr_approx")
_MODEL_HEAD = {"": "m0plus_fast", "slow": "m0plus_slow", "avr_approx": "avr_approx"}


def _per_problem_matrix(sv) -> str:
    """per_problem keys pivoted to problem x cost model.

    The stored keys are '<name>' for the fast model and '<model>:<name>' otherwise, so the
    flat listing repeated every problem once per model and every avr_approx row carried the
    same footnote. One matrix says the same thing in a third of the rows and one footnote."""
    cells: dict[tuple[str, str], object] = {}
    names: list[str] = []
    models: list[str] = []
    for key, val in sv.per_problem.items():
        model, _sep, name = str(key).rpartition(":")
        cells[(name, model)] = val
        if name not in names:
            names.append(name)
        if model not in models:
            models.append(model)
    cols = [m for m in _MODEL_COLS if m in models] + sorted(set(models) - set(_MODEL_COLS))
    known = set(_PROBLEM_ROWS) | set(_AGG_ROWS)
    rows = ([n for n in _PROBLEM_ROWS if n in names]
            + sorted(n for n in names if n not in known)
            + [n for n in _AGG_ROWS if n in names])
    if not rows or not cols:
        return "<p>no per-problem errors recorded</p>"
    out = ['<div class="scroll"><table><tr><th>problem</th>'
           + "".join(f'<th class="num">{_esc(_MODEL_HEAD.get(m, m))}</th>' for m in cols)
           + "</tr>"]
    for name in rows:
        agg = name in _AGG_ROWS
        label = f"<strong>{_esc(name)}</strong>" if agg else _esc(name)
        out.append(f"<tr><td>{label}</td>" + "".join(
            f'<td class="num">{_num(cells[(name, m)]) if (name, m) in cells else "n/a"}</td>'
            for m in cols) + "</tr>")
    out.append("</table></div>")
    if "avr_approx" in cols:
        out.append(f'<p class="note">{_esc(AVR_NOTE)}</p>')
    return "\n".join(out)


def _verdict_badge(verdict) -> str:
    v = verdict if verdict is not None else "open"
    return f'<span class="badge badge-{_esc(v)}">{_esc(v)}</span>'


def _hyp_cycle(h: dict) -> str:
    cyc = h.get("resolved_cycle")
    if cyc is not None:
        return f"c{_num(cyc)}"
    return f"c{_num(h.get('cycle_proposed'))}+"


def _hyp_row(group: list[dict]) -> str:
    """One ledger row per distinct predicate.

    The planning model re-proposes a predicate it has already tested, sometimes many times
    over: 310 hypotheses cover 172 predicates, and the largest group is one predicate posed
    28 times with an identical verdict and an identical effect size. Grouping keeps every
    record while showing the repeat as a count instead of 28 near-identical rows."""
    latest = group[-1]
    pred = str(latest.get("predicate", ""))
    verdicts = {str(h.get("verdict") or "open") for h in group}
    badge = (_verdict_badge(latest.get("verdict")) if len(verdicts) == 1
             else _verdict_badge(latest.get("verdict")) + '<span class="when">&nbsp;mixed</span>')
    rep = f' <span class="rep">&times;{len(group)}</span>' if len(group) > 1 else ""
    summary = (f'<span class="mono">{_esc(group[0].get("id", ""))}{rep}</span>'
               f"{badge}"
               f'<span class="pred" title="{_esc(pred)}">{_esc(pred)}</span>'
               f'<span class="num">{_num(latest.get("effect_size"))}</span>'
               f'<span class="num">{_num(latest.get("n_samples"))}</span>'
               f'<span class="when">{_hyp_cycle(latest)}</span>')

    first = group[0]
    body = ['<dl class="meta">'
            f'<dt>statement</dt><dd>{_esc(first.get("statement", ""))}</dd>'
            f'<dt>mechanism</dt><dd>{_esc(first.get("mechanism", ""))}</dd>'
            f'<dt>control</dt><dd>{_esc(first.get("control", ""))}</dd>'
            f'<dt>min_samples</dt><dd>{_num(first.get("min_samples"))}</dd>'
            "</dl>"]
    if len(group) > 1:
        body.append(f'<details class="repeats"><summary>the same predicate, posed '
                    + ("twice" if len(group) == 2 else f"{len(group)} times")
                    + "</summary><div>"
                    '<div class="scroll"><table><tr><th>id</th><th>verdict</th>'
                    '<th class="num">d</th><th class="num">n</th><th>cycle</th>'
                    "<th>mechanism as restated</th></tr>")
        for h in group:
            body.append(f'<tr><td class="mono">{_esc(h.get("id", ""))}</td>'
                        f"<td>{_verdict_badge(h.get('verdict'))}</td>"
                        f'<td class="num">{_num(h.get("effect_size"))}</td>'
                        f'<td class="num">{_num(h.get("n_samples"))}</td>'
                        f"<td>{_hyp_cycle(h)}</td>"
                        f'<td>{_esc(h.get("mechanism", ""))}</td></tr>')
        body.append("</table></div></div></details>")
    return f'<details class="led"><summary>{summary}</summary><div>{"".join(body)}</div></details>'


_HYP_ORDER = ("supported", "refuted", "inconclusive", "open")
_HYP_GLOSS = {
    "supported": "the predicate held once every cell it names had enough records",
    "refuted": "the predicate failed on the data it names",
    "inconclusive": "effect size under 0.2, or a named cell with no records",
    "open": "waiting for min_samples in at least one named cell",
}


def render_hypotheses(hyps: list[dict]) -> str:
    parts = [
        '<p class="lead">The ' + _gloss("hypothesis-ledger", "hypothesis ledger")
        + ": predicates the planning model committed to before the data could answer them, "
        "each resolved by code. One row per distinct predicate, grouped by verdict. Open a row "
        "for the statement, the proposed mechanism, and the control that would have shown the "
        "mechanism wrong.</p>"
    ]
    parts.append(_explain(
        "Each row pairs a machine-checkable predicate over per-cell statistics, for example "
        '<span class="mono">slow.p3s4.heldout &lt; slow.p4s4.heldout</span>, with the mechanism '
        "and control the model recorded before the data could answer. The p and s numbers name "
        "an (order, stages) cell and the leading word picks the cost model.",
        "Verdicts come from code, never from the model. Once every cell a predicate references "
        "holds min_samples records the predicate is evaluated against those cells' running "
        "statistics. d is " + _gloss("cohens-d", "Cohen's d") + "; below 0.2 the verdict is "
        "inconclusive whichever way the comparison went, so weak effects cannot be claimed. A "
        "predicate naming an empty cell is inconclusive too, because absence of evidence never "
        "refutes. n is the smallest record count among the named cells, and c is the cycle the "
        "verdict landed on (c123+ means proposed at 123, still open).",
        "A &times;N marker means the model posed that predicate N times across the run. The "
        "repeats are kept and listed inside the row rather than dropped, because how often the "
        "planner revisits a settled question is itself a property of the loop."))
    if not hyps:
        parts.append("<p>no hypotheses recorded</p>")
        return _page("hypothesis ledger", "\n".join(parts), active="hypotheses.html",
                     subtitle="Falsifiable statements about the archive, resolved mechanically.")

    by_pred: dict[str, list[dict]] = {}
    for h in sorted(hyps, key=lambda d: str(d.get("id", ""))):
        by_pred.setdefault(str(h.get("predicate", "")), []).append(h)
    groups: dict[str, list[list[dict]]] = {k: [] for k in _HYP_ORDER}
    counts: dict[str, int] = {k: 0 for k in _HYP_ORDER}
    for group in sorted(by_pred.values(), key=lambda g: str(g[0].get("id", ""))):
        key = str(group[-1].get("verdict") or "open")
        groups.setdefault(key, []).append(group)
        for h in group:
            counts[str(h.get("verdict") or "open")] = counts.get(str(h.get("verdict") or "open"), 0) + 1

    parts.append(_cards([(k, str(counts.get(k, 0)), _HYP_GLOSS[k]) for k in _HYP_ORDER]))
    repeats = sum(1 for g in by_pred.values() if len(g) > 1)
    parts.append(f'<p class="note">{len(hyps)} hypotheses over {len(by_pred)} distinct '
                 f"predicates; {repeats} predicates were posed more than once. Counts above are "
                 "per hypothesis, rows below are per predicate.</p>")

    head = ('<div class="ledhead"><span></span><span>id</span><span>verdict</span>'
            '<span>predicate</span><span class="num">d</span><span class="num">n</span>'
            "<span>cycle</span></div>")
    for key in _HYP_ORDER:
        rows = groups.get(key, [])
        if not rows:
            continue
        parts.append(f"<h2>{_esc(key)} ({len(rows)} predicates)</h2>")
        parts.append('<div class="scroll"><div class="ledger">' + head
                     + "".join(_hyp_row(g) for g in rows) + "</div></div>")
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
    parts.append('<p class="caption">Both are 4-stage order-4 tableaus with the same '
                 'stability polynomial, so any cost difference between them comes from '
                 'coefficient arithmetic alone.</p>')
    parts.append('<div class="panel">' + _anchor_bars() + "</div>")
    parts.append('<table><tr><th>tableau</th><th class="num">n_states</th>'
                 '<th class="num">m0plus_fast</th><th class="num">m0plus_slow</th></tr>')
    for name in ("rk4", "rk38"):
        t = classical.get(name)
        if t is None:
            continue
        for n in (1, 2, 4):
            fast = costmodel.cycle_count(t, costmodel.M0PLUS_FAST, n)
            slow = costmodel.cycle_count(t, costmodel.M0PLUS_SLOW, n)
            parts.append(f'<tr><td>{name}</td><td class="num">{n}</td>'
                         f'<td class="num">{fast}</td><td class="num">{slow}</td></tr>')
    parts.append("</table>")
    parts.append("<h2>Classical tableaus (n_states = 1)</h2>")
    parts.append('<div class="scroll"><table><tr><th>tableau</th><th class="num">stages</th>'
                 '<th class="num">m0plus_fast</th><th class="num">m0plus_slow</th>'
                 '<th class="num">avr_approx</th></tr>')
    for name in sorted(classical.keys()):
        t = classical[name]
        fast = costmodel.cycle_count(t, costmodel.M0PLUS_FAST, 1)
        slow = costmodel.cycle_count(t, costmodel.M0PLUS_SLOW, 1)
        avr = costmodel.cycle_count(t, costmodel.AVR_APPROX, 1)
        parts.append(f'<tr><td>{name}</td><td class="num">{len(t.b)}</td><td class="num">{fast}</td>'
                     f'<td class="num">{slow}</td><td class="num">{avr}</td></tr>')
    parts.append("</table></div>")
    parts.append(f'<p class="note">avr_approx: {_esc(AVR_NOTE)} It is reported for context and '
                 "never drives the search or the archive grids.</p>")
    parts.append('<p class="note">Each coefficient is charged the cheaper of a shift-add chain '
                 "of its " + _gloss("csd-weight", "CSD weight") + " and a single hardware "
                 "multiply, so a tableau of simple "
                 + _gloss("dyadic-rational", "dyadic") + " values can be much cheaper than its "
                 "stage count suggests.</p>")
    parts.append("<h2>Model parameters</h2>")
    parts.append('<table><tr><th>model</th><th class="num">mul</th><th class="num">add</th>'
                 '<th class="num">shift</th><th class="num">load</th><th class="num">store</th></tr>')
    for m in (costmodel.M0PLUS_FAST, costmodel.M0PLUS_SLOW, costmodel.AVR_APPROX):
        cy = m.cycles
        parts.append(f'<tr><td>{m.name}</td><td class="num">{cy.get("mul")}</td><td class="num">{cy.get("add")}</td>'
                     f'<td class="num">{cy.get("shift")}</td><td class="num">{cy.get("load")}</td>'
                     f'<td class="num">{cy.get("store")}</td></tr>')
    parts.append("</table>")
    parts.append('<p class="note">m0plus_fast and m0plus_slow are identical except for the '
                 "multiplier, the hardware difference this project studies; every cycle count "
                 "on this site is these five numbers applied to the tableau's instruction "
                 "sequence, never a hardware measurement.</p>")
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
                 subtitle="HANDOFF §15: the kill/proceed measurement, run before the search.")


def render_literature(digests: list[dict]) -> str:
    parts = [
        '<p class="lead">Digests of published work the model researched on the web during the '
        "run, newest at the top. They exist to inform the "
        + _gloss("directive", "directive") + " and "
        + _gloss("hypothesis-ledger", "hypothesis") + " prompts; nothing on this page is a "
        "measurement from this project, and the measured pages never depend on anything "
        "written here. The stored text is shown as written, after a vocabulary pass that "
        "keeps this site's banned words out. Click an entry for its full text and sources.</p>",
        f'<p class="note">{_esc(_MODEL_NOTE)}</p>',
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
        "disagree, trust the tables. Unlike "
        + _gloss("hypothesis-ledger", "hypotheses") + " these readings carry no "
        "machine-checkable predicate, so they can be wrong in ways the code cannot check; they "
        "are kept because they explain what the search was steering toward. Click an entry for "
        "its full text.</p>",
        f'<p class="note">{_esc(_MODEL_NOTE)}</p>',
    ]
    if not entries:
        parts.append("<p>no interpretation yet</p>")
    # One top-level entry per cycle: the newest draft (latest ts, ties broken by stored
    # position) speaks for its cycle, and older same-cycle drafts fold inside it.
    by_cycle: dict[int, list[tuple[int, dict]]] = {}
    for idx, e in enumerate(entries):
        by_cycle.setdefault(int(e.get("cycle", 0)), []).append((idx, e))
    tops: list[tuple[dict, list[dict]]] = []
    seen_cycles: set[int] = set()
    for idx in range(len(entries) - 1, -1, -1):
        cyc = int(entries[idx].get("cycle", 0))
        if cyc in seen_cycles:
            continue
        seen_cycles.add(cyc)
        group = by_cycle[cyc]
        newest_idx, newest = max(group, key=lambda p: (str(p[1].get("ts", "")), p[0]))
        older = sorted((p for p in group if p[0] != newest_idx),
                       key=lambda p: (str(p[1].get("ts", "")), p[0]), reverse=True)
        tops.append((newest, [d for _i, d in older]))
    for i, (e, drafts) in enumerate(tops):
        open_attr = " open" if i == 0 else ""
        entry = [f'<details class="fold entry"{open_attr}>']
        entry.append(f"<summary><strong>cycle {int(e.get('cycle', 0))}</strong> "
                     f'<span class="when">written {_esc(_ct(e.get("ts")))}</span></summary>')
        entry.append("<div>")
        for para in str(e.get("text", "")).split("\n\n"):
            if para.strip():
                entry.append(f"<p>{_esc(para.strip())}</p>")
        if drafts:
            entry.append('<details class="fold"><summary>superseded same-cycle drafts '
                         f"({len(drafts)})</summary><div>")
            for d in drafts:
                entry.append(f'<p class="when">written {_esc(_ct(d.get("ts")))}</p>')
                for para in str(d.get("text", "")).split("\n\n"):
                    if para.strip():
                        entry.append(f"<p>{_esc(para.strip())}</p>")
            entry.append("</div></details>")
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
    ("cycles-to-tolerance", "cycles to tolerance", (
        "The second way this project measures a method, and the one that lets classes "
        "with different control parameters be compared: fix an accuracy target, then "
        "ask how many modelled cycles a method needs to reach it. A fixed-step method "
        "reaches a target by taking more steps, an adaptive one by being given a "
        "tighter tolerance, and an implicit one by taking more Newton work per step, so "
        "the target is the shared quantity and cycles are the answer. It is not the "
        "same measurement as the fixed cycle budget the archive scores on, and the two "
        "are never mixed in one number.",
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
    ("embedded-pair", "embedded pair", (
        "Two Runge-Kutta methods sharing one A matrix and one set of stages, with two "
        "weight vectors b and b_hat of different order. Subtracting the two updates "
        "gives an estimate of the local error for the price of the arithmetic on the "
        "difference, which is what a step-size controller needs. An archive record "
        "holds one b, so an embedded pair has no place to live in the scored schema and "
        "is measured off-archive.",
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
    ("l-stability", "L-stability", (
        "A stability property stronger than A-stability: the method is stable for every "
        "decaying linear test problem, and in addition its stability function tends to "
        "zero as the problem stiffness grows without bound, so a very fast mode is "
        "damped out in one step instead of being carried along at full size. It is the "
        "property that makes an implicit method useful on a stiff problem, and for the "
        "two-stage SDIRK it holds only for particular values of the diagonal gamma.",
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
    ("off-archive", "off-archive", (
        "Measured outside the scored path: not produced by the pinned verifier, not "
        "stored as a record, not holding a grid cell, and not counted in any archive "
        "statistic or hypothesis verdict. Implicit and adaptive measurements are "
        "off-archive because the record schema cannot hold them, not because they are "
        "less trusted. An off-archive number can be compared with other off-archive "
        "numbers from the same code hash; it cannot be compared with an archive score, "
        "which uses different arithmetic and a different budget.",
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
    ("sdirk", "SDIRK", (
        "Singly diagonally implicit Runge-Kutta: the A matrix is lower triangular with "
        "one repeated value gamma on the diagonal, so each stage is defined in terms of "
        "itself and must be solved for, one stage at a time, rather than evaluated. The "
        "prototype here solves each stage with a fixed number of Newton iterations. The "
        "repeated diagonal means one Jacobian factorization serves every stage, which "
        "is what makes the class affordable on small hardware at all.",
    )),
    ("stage", "stage", (
        "One derivative evaluation inside a single step: an s-stage explicit method calls the "
        "problem right-hand side s times per step, each call fed by a weighted combination of "
        "the earlier stage results. Stage count is a major cost driver and one axis of the "
        "archive grids.",
    )),
    ("step-size-controller", "step-size controller", (
        "The rule an adaptive method uses to turn an error estimate into the next step "
        "size. A proportional-integral controller scales the step by the ratio of the "
        "tolerance to the estimated error, raised to fixed exponents, using both the "
        "current and the previous ratio so the step sequence settles instead of "
        "oscillating. A step whose estimate exceeds the tolerance is rejected and "
        "retried at a smaller size, so an adaptive run costs work on attempts that "
        "never advance the solution.",
    )),
    ("stiffness-ratio", "stiffness ratio", (
        "The ratio of the fastest decay rate in a problem to the slowest, taken over "
        "the linearization; a large ratio means an explicit method's step size is "
        "capped by a mode that has already died away, so it takes far more steps than "
        "accuracy alone would need. The validation suite labels each problem stiff or "
        "not on this basis and reports the ratio next to the label.",
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
        "to compare against. The elite grid ranks on held-out error alone, so a cell can pass "
        "from a heldout_verified record to a lower-error record labelled otherwise, and the "
        "top-tier count can fall without anything having gone wrong.",
    )),
    ("verifier-hash", "verifier hash", (
        "A sha256 over ten pinned files in fixed order: the six scoring modules (coeffrep, "
        "orderconditions, verifier, costmodel, evaluator, problems) and four fixtures. Any "
        "byte changed in any of them changes the hash, and the container refuses to start if "
        "the computed hash differs from the pinned one. Every record stores the hash that was "
        "active when it was scored, so scores from different code can never be silently mixed.",
    )),
    ("work-precision", "work-precision", (
        "A chart form for comparing methods that do not share a control parameter: "
        "achieved error on one log axis against work spent on the other, one mark per "
        "run and one line per method, so a reader picks an accuracy and reads off what "
        "it cost. Work is counted in whatever unit the runs share, usually derivative "
        "evaluations or modelled cycles. Down and to the left is better on both axes.",
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
# practical validation page (rendered only when work_dir()/validation/results.json exists)
# ----------------------------------------------------------------------------

def _vlabel(name_or_hash: str, kind: str) -> str:
    """Chart/table label: classical fixture name, or a short prefix of the content hash."""
    return name_or_hash if kind == "classical" else name_or_hash[:8]


def _validation_chart(data: dict) -> str:
    """Per-problem dot rows: Q15 error of every method on a log axis, colored by kind."""
    methods = data.get("methods") or []
    kind_of = {m.get("name_or_hash"): m.get("kind", "") for m in methods}
    method_order = [m.get("name_or_hash") for m in methods]
    results = [r for r in (data.get("results") or []) if _finite_pos(r.get("q15_error"))]
    if not results:
        return ""
    by: dict[str, dict[str, dict]] = {}
    for r in results:
        by.setdefault(str(r.get("problem")), {})[str(r.get("method"))] = r
    problems = [str(p.get("name")) for p in (data.get("problems") or [])
                if str(p.get("name")) in by]
    problems += sorted(k for k in by if k not in set(problems))
    errs = [r["q15_error"] for r in results]
    lo = 10 ** math.floor(math.log10(min(errs)))
    hi = 10 ** math.ceil(math.log10(max(errs)))
    span = math.log10(hi) - math.log10(lo) or 1.0
    w, ml, mr = 640, 118, 16
    head_h, row_h, group_pad = 22, 20, 12
    n_rows = sum(1 for p in problems for m in method_order if m in by[p])
    h = 8 + len(problems) * (head_h + group_pad) + n_rows * row_h + 30

    def fx(v: float) -> float:
        return ml + (math.log10(v) - math.log10(lo)) / span * (w - ml - mr)

    parts = []
    for tv in _log_ticks(lo, hi):
        px = fx(tv)
        parts.append(f'<line class="gridline" x1="{_fmt(px)}" y1="6" x2="{_fmt(px)}" y2="{h - 26}"/>')
        parts.append(f'<text x="{_fmt(px)}" y="{h - 10}" text-anchor="middle">{_pow_label(tv)}</text>')
    y = 8
    for prob in problems:
        rows = [(m, by[prob][m]) for m in method_order if m in by[prob]]
        parts.append(f'<text class="lbl" x="6" y="{_fmt(y + 14)}">{_esc(prob)}</text>')
        y += head_h
        best = min(r["q15_error"] for _m, r in rows)
        for m, r in rows:
            cy = y + row_h / 2
            err = r["q15_error"]
            px = fx(err)
            kind = kind_of.get(m, "")
            sw = "var(--s1)" if kind == "discovered" else "var(--s2)"
            label = _vlabel(str(m), kind)
            parts.append(f'<text x="{ml - 8}" y="{_fmt(cy + 3.5)}" text-anchor="end">{_esc(label)}</text>')
            title = (f"{prob} / {label} ({kind}): Q15 error {_num(err)}, float64 "
                     f"{_num(r.get('float_error'))} over the same {_num(r.get('steps'))} steps "
                     f"({_num(r.get('cycles_per_step'))} cycles/step)")
            parts.append(f'<circle cx="{_fmt(px)}" cy="{_fmt(cy)}" r="4.5" fill="{sw}" '
                         f'class="cellstroke"><title>{_esc(title)}</title></circle>')
            if err == best:
                parts.append(f'<circle cx="{_fmt(px)}" cy="{_fmt(cy)}" r="8.5" fill="none" '
                             f'stroke="{sw}" stroke-width="1.5"/>')
                vtxt = _num(err)
                if px + 12 + len(vtxt) * _LBL_CHAR_W > w - 6:
                    parts.append(f'<text class="lbl" x="{_fmt(px - 12)}" y="{_fmt(cy + 3.5)}" '
                                 f'text-anchor="end">{vtxt}</text>')
                else:
                    parts.append(f'<text class="lbl" x="{_fmt(px + 12)}" y="{_fmt(cy + 3.5)}">{vtxt}</text>')
            y += row_h
        y += group_pad
    svg = (f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
           'aria-label="Q15 final-state error per method on each validation problem, log scale">'
           + "".join(parts) + "</svg>")
    return ('<figure><figcaption>One row per method within each problem; the dot is '
            "final-state Q15 error at the shared cycle budget on a log axis, so left is "
            "better and each method takes as many steps as its per-step cost allows. Blue is "
            "a discovered method, orange a classical anchor; the ringed dot is the problem's "
            "lowest error, printed exactly. Hover a dot for exact values and step "
            "counts.</figcaption>"
            + _legend([("var(--s1)", "discovered (archive)"), ("var(--s2)", "classical anchor")])
            + svg + "</figure>")


def _filter_validation(data: dict, names: set[str]) -> dict:
    """A shallow copy of a validation results dict restricted to the named problems."""
    out = dict(data)
    out["problems"] = [p for p in (data.get("problems") or [])
                       if str(p.get("name")) in names]
    out["results"] = [r for r in (data.get("results") or [])
                      if str(r.get("problem")) in names]
    return out


def _best_table(names: list[str], per: dict, stiff_cols: bool) -> list[str]:
    """The best-per-problem rows for one problem group. With stiff_cols, finisher
    counts join the columns and a problem no discovered method finished renders
    'none finished' instead of numbers (traceable to the overflow notes below)."""
    head = ('<div class="scroll"><table><tr><th>problem</th><th>winner</th>'
            '<th>best classical</th><th class="num">Q15 error</th>'
            '<th>best discovered</th><th class="num">Q15 error</th>'
            '<th class="num">ratio</th>')
    if stiff_cols:
        head += '<th class="num">finishers (classical / discovered)</th>'
    parts = [head + "</tr>"]
    for name in names:
        d = per.get(name)
        if not isinstance(d, dict):
            continue
        wkind = d.get("winner_kind", "")
        winner = _vlabel(str(d.get("winner")), wkind)
        ratio = d.get("ratio_discovered_over_classical")
        best_disc = d.get("best_discovered")
        if best_disc is None:
            disc_cells = ('<td>none finished</td><td class="num">n/a</td>')
        else:
            disc_cells = (f'<td class="hash">{_esc(_vlabel(str(best_disc), "discovered"))}</td>'
                          f'<td class="num">{_num(d.get("best_discovered_q15_error"))}</td>')
        best_cls = d.get("best_classical")
        best_cls_err = d.get("best_classical_q15_error")
        if best_cls is None and wkind == "classical":
            # Verdicts omit best_classical when no discovered method finished; the
            # winner is then classical by construction, so it is the best classical.
            best_cls = d.get("winner")
            best_cls_err = d.get("winner_q15_error")
        cls_cells = (f"<td>{_esc(str(best_cls)) if best_cls is not None else 'n/a'}</td>"
                     f'<td class="num">{_num(best_cls_err)}</td>')
        row = ("<tr>"
               f"<td>{_esc(name)}</td>"
               f"<td>{_esc(winner)} <span class=\"when\">({_esc(wkind)})</span></td>"
               + cls_cells + disc_cells +
               f'<td class="num">{f"{ratio:.3g}" if isinstance(ratio, (int, float)) else "n/a"}</td>')
        if stiff_cols:
            row += (f'<td class="num">{_num(d.get("finishers_classical"))} / '
                    f'{_num(d.get("finishers_discovered"))}</td>')
        parts.append(row + "</tr>")
    parts.append("</table></div>")
    return parts


def render_validation(data: dict, benchmark: dict | None = None) -> str:
    verdicts = data.get("verdicts") or {}
    per = verdicts.get("per_problem") or {}
    problems = data.get("problems") or []
    methods = data.get("methods") or []
    results = data.get("results") or []
    kind_of = {m.get("name_or_hash"): m.get("kind", "") for m in methods}
    budget = data.get("budget_cycles")
    stiff_names = [str(p.get("name")) for p in problems
                   if isinstance(p, dict) and p.get("stiff")]
    has_stiff = bool(stiff_names)
    practical_names = [str(p.get("name")) for p in problems
                       if str(p.get("name")) not in set(stiff_names)]
    practical_names += sorted(k for k in per
                              if k not in set(practical_names) | set(stiff_names))
    lead_tail = (", scored as final-state error against an independent reference solution."
                 if not has_stiff else
                 ", scored as final-state error against an independent reference solution. "
                 f"The suite splits into {len(practical_names)} non-stiff practical problems "
                 f"and {len(stiff_names)} moderately stiff ones, grouped separately below "
                 "because stiffness changes which methods finish at all.")
    parts = [
        '<p class="lead">This page reports the practical validation suite: '
        f"{len(problems)} problems taken from embedded application domains, disjoint from "
        "both the search set and the " + _gloss("held-out-set", "held-out set") + ", which no "
        "optimizer saw and no archive statistic includes. Discovered methods from the live "
        "archive and the classical anchors integrate each problem in "
        + _gloss("q15", "Q15") + " at the same fixed "
        + _gloss("cycle-budget", "cycle budget") + f" of {_num(budget)} cycles under "
        f"{_esc(data.get('cost_model'))} with "
        + _gloss("floor-rounding", "floor rounding") + lead_tail + "</p>"
    ]

    def _ratio_card(v) -> str:
        return f"{v:.3g}" if isinstance(v, (int, float)) else "n/a"

    if has_stiff:
        cards = [
            ("practical problems",
             f"{_num(verdicts.get('practical_problems_won_by_discovered'))} of "
             f"{_num(verdicts.get('practical_problems_compared'))}",
             "non-stiff; won by a discovered method"),
            ("practical median ratio",
             _ratio_card(verdicts.get("practical_median_ratio_discovered_over_classical")),
             "best discovered / best classical; below 1.0 favors discovered"),
            ("stiff problems",
             f"{_num(verdicts.get('stiff_problems_won_by_discovered'))} of "
             f"{_num(verdicts.get('stiff_problems_compared'))}",
             "won by discovered, where both sides finish"),
            ("stiff median ratio",
             _ratio_card(verdicts.get("stiff_median_ratio_discovered_over_classical")),
             "over the stiff problems both sides finish"),
            ("no discovered finisher",
             f"{_num(verdicts.get('stiff_problems_with_no_discovered_finisher'))} of "
             f"{_num(verdicts.get('stiff_problems_total'))}",
             "stiff problems where every discovered method overflows"),
        ]
    else:
        won = verdicts.get("problems_won_by_discovered")
        compared = verdicts.get("problems_compared")
        cards = [
            ("problems", _num(compared), "from embedded application domains"),
            ("won by discovered", f"{_num(won)} of {_num(compared)}",
             "lower Q15 error than every anchor"),
            ("median error ratio",
             _ratio_card(verdicts.get("median_ratio_discovered_over_classical")),
             "best discovered / best classical; below 1.0 favors discovered"),
        ]
    parts.append(_cards(cards))
    overall = verdicts.get("overall")
    if overall:
        parts.append(f"<p>{_esc(overall)}</p>")
    speed = _speed_sentence(benchmark)
    if speed:
        parts.append(f"<p>{speed}</p>")
    if has_stiff:
        parts.append("<h2>Q15 error per problem: practical (non-stiff)</h2>")
        chart = _validation_chart(_filter_validation(data, set(practical_names)))
        if chart:
            parts.append('<div class="panel">' + chart + "</div>")
        parts.append("<h2>Q15 error per problem: stiff subset</h2>")
        parts.append('<p class="note">A method appears in a stiff row only if its Q15 run '
                     "finished; a run that overflowed has no error to plot and is listed "
                     "in the finisher counts and the full table below.</p>")
        chart = _validation_chart(_filter_validation(data, set(stiff_names)))
        if chart:
            parts.append('<div class="panel">' + chart + "</div>")
    else:
        parts.append("<h2>Q15 error per problem</h2>")
        chart = _validation_chart(data)
        if chart:
            parts.append('<div class="panel">' + chart + "</div>")
    parts.append("<h2>Best per problem</h2>")
    if has_stiff:
        parts.append("<h3>Practical (non-stiff)</h3>")
        parts.extend(_best_table(practical_names, per, stiff_cols=False))
        parts.append("<h3>Stiff</h3>")
        parts.extend(_best_table(stiff_names, per, stiff_cols=True))
    else:
        parts.extend(_best_table(practical_names, per, stiff_cols=False))
    parts.append('<p class="note">ratio is best-discovered over best-classical Q15 error; '
                 "below 1.0 the discovered method carries the lower error.</p>")
    parts.append("<h2>Q15 against float64</h2>")
    floats = [r.get("float_error") for r in results if _finite_pos(r.get("float_error"))]
    maxq = [r.get("max_abs_q") for r in results if isinstance(r.get("max_abs_q"), int)]
    if floats and maxq:
        parts.append(
            f"<p>Float64 runs of the same tableaus over the same steps land between "
            f"{_num(min(floats))} and {_num(max(floats))}; the largest raw Q15 magnitude seen "
            f"anywhere is {max(maxq)} of the int16 limit 32767. Where a row's float64 error "
            "sits far below its Q15 error, the Q15 number measures quantization, not "
            "truncation.</p>")
    any_note = any(r.get("note") for r in results)
    parts.append('<div class="scroll"><table><tr><th>problem</th><th>method</th><th>kind</th>'
                 '<th class="num">steps</th><th class="num">cycles/step</th>'
                 '<th class="num">Q15 error</th><th class="num">float64 error</th>'
                 '<th class="num">max |q|</th>' + ("<th>note</th>" if any_note else "")
                 + "</tr>")
    for r in results:
        m = str(r.get("method"))
        kind = kind_of.get(m, "")
        parts.append(
            "<tr>"
            f"<td>{_esc(str(r.get('problem')))}</td>"
            f"<td>{_esc(_vlabel(m, kind))}</td><td>{_esc(kind)}</td>"
            f'<td class="num">{_num(r.get("steps"))}</td>'
            f'<td class="num">{_num(r.get("cycles_per_step"))}</td>'
            f'<td class="num">{_num(r.get("q15_error"))}</td>'
            f'<td class="num">{_num(r.get("float_error"))}</td>'
            f'<td class="num">{_num(r.get("max_abs_q"))}</td>'
            + (f'<td><span class="note">{_esc(str(r.get("note")))}</span></td>'
               if any_note and r.get("note")
               else ("<td></td>" if any_note else ""))
            + "</tr>")
    parts.append("</table></div>")
    parts.append("<h2>Methods</h2>")
    parts.append('<div class="scroll"><table><tr><th>kind</th><th>name / tableau_hash</th>'
                 '<th class="num">order</th><th class="num">stages</th><th>roles</th>'
                 "<th>archive provenance</th></tr>")
    for m in methods:
        name = str(m.get("name_or_hash"))
        kind = str(m.get("kind", ""))
        roles = ", ".join(str(x) for x in (m.get("roles") or []))
        arch_info = m.get("archive")
        if isinstance(arch_info, dict):
            prov = (f"cycle {_num(arch_info.get('cycle_id'))}, {_esc(str(arch_info.get('tier')))}, "
                    f"held-out {_num(arch_info.get('heldout_error'))}")
        else:
            prov = "classical fixture"
        parts.append(
            "<tr>"
            f"<td>{_esc(kind)}</td>"
            f'<td class="hash">{_esc(name)}</td>'
            f'<td class="num">{_num(m.get("order"))}</td>'
            f'<td class="num">{_num(m.get("stages"))}</td>'
            f"<td>{_esc(roles)}</td><td>{prov}</td>"
            "</tr>")
    parts.append("</table></div>")
    parts.append("<h2>Problems</h2>")
    for p in problems:
        parts.append(f"<h3>{_esc(str(p.get('name')))}</h3>")
        parts.append(f"<p>{_esc(str(p.get('domain')))}; {_esc(str(p.get('family')))}, "
                     f"{_num(p.get('n_states'))} states, integrated to t = "
                     f"{_num(p.get('t_end'))}.</p>")
        if p.get("stiffness_ratio") is not None:
            basis = p.get("stiffness_basis")
            parts.append(
                f'<p class="note">{"Stiff" if p.get("stiff") else "Non-stiff"}; '
                f"stiffness ratio {_num(p.get('stiffness_ratio'))}"
                + (f" ({_esc(str(basis))})" if basis else "") + ".</p>")
        eq = p.get("equation")
        if eq:
            parts.append(f'<p class="mono">{_esc(str(eq))}</p>')
        ref = p.get("reference")
        if ref:
            parts.append(f"<p>Reference: {_esc(str(ref))}.</p>")
        src = p.get("source")
        if src:
            parts.append(f'<p class="note">Source: {_esc(str(src))}</p>')
    gen = data.get("generated_from")
    if isinstance(gen, dict):
        parts.append("<h2>Provenance</h2>")
        parts.append('<dl class="meta">\n' + "\n".join(
            f"<dt>{_esc(k)}</dt><dd><span class=\"hash\">{_esc(str(gen[k]))}</span></dd>"
            if "hash" in str(k) else f"<dt>{_esc(k)}</dt><dd>{_num(gen[k])}</dd>"
            for k in sorted(gen.keys())) + "\n</dl>")
    return _page("practical validation", "\n".join(parts), active="validation.html",
                 subtitle="Application-domain problems no search ever saw, at the same budget "
                          "and arithmetic.")


# ----------------------------------------------------------------------------
# benchmark page (rendered only when work_dir()/benchmark/results.json exists)
# ----------------------------------------------------------------------------

def _nice_step(raw: float) -> float:
    """The smallest of 1, 2, 2.5, 5, 10 times a power of ten at or above raw."""
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1.0, 2.0, 2.5, 5.0):
        if raw <= m * mag:
            return m * mag
    return 10.0 * mag


def _bench_us_chart(sp: dict, methods: list) -> str:
    """Measured microseconds per Q15 step, one row per method: median bar plus
    one dot per problem, on a linear axis."""
    per = sp.get("per_method_us_per_step")
    if not isinstance(per, dict):
        return ""
    kind_of = {str(m.get("name_or_hash")): str(m.get("kind", "")) for m in methods}
    order = [str(m.get("name_or_hash")) for m in methods
             if str(m.get("name_or_hash")) in per]
    order += sorted(k for k in per if k not in set(order))
    rows = []
    for name in order:
        d = per.get(name)
        if isinstance(d, dict) and _finite_pos(d.get("median_us_per_step")):
            rows.append((name, d))
    if not rows:
        return ""
    hi = 0.0
    for _name, d in rows:
        hi = max(hi, float(d["median_us_per_step"]))
        for v in (d.get("per_problem_us_per_step") or {}).values():
            if _finite_pos(v):
                hi = max(hi, float(v))
    step = _nice_step(hi / 5.0)
    top = step * math.ceil(hi / step)
    row_h, ml, w = 30, 118, 640
    h = 14 + row_h * len(rows) + 30
    px_per_us = (w - ml - 16) / top

    def fx(v: float) -> float:
        return ml + v * px_per_us

    parts = []
    tv = 0.0
    while tv <= top * 1.0001:
        px = fx(tv)
        parts.append(f'<line class="gridline" x1="{_fmt(px)}" y1="10" x2="{_fmt(px)}" y2="{h - 26}"/>')
        parts.append(f'<text x="{_fmt(px)}" y="{h - 12}" text-anchor="middle">{tv:g}</text>')
        tv += step
    for i, (name, d) in enumerate(rows):
        y = 12 + row_h * i
        kind = kind_of.get(name, "")
        sw = "var(--s1)" if kind == "discovered" else "var(--s2)"
        label = _vlabel(name, kind)
        med = float(d["median_us_per_step"])
        bw = med * px_per_us
        parts.append(f'<text x="{ml - 8}" y="{_fmt(y + 15)}" text-anchor="end">{_esc(label)}</text>')
        title = (f"{label} ({kind}): median {_num(med)} us per Q15 step over "
                 f"{_num(d.get('n_problems'))} problems; min {_num(d.get('min_us_per_step'))}, "
                 f"max {_num(d.get('max_us_per_step'))}")
        parts.append(f'<rect x="{ml}" y="{y}" width="{_fmt(max(bw, 2))}" height="20" rx="4" '
                     f'fill="{sw}" class="cellstroke"><title>{_esc(title)}</title></rect>')
        for prob in sorted((d.get("per_problem_us_per_step") or {}).keys()):
            v = d["per_problem_us_per_step"][prob]
            if _finite_pos(v):
                parts.append(f'<circle cx="{_fmt(fx(float(v)))}" cy="{_fmt(y + 10)}" r="3.5" '
                             f'fill="{sw}" class="cellstroke">'
                             f'<title>{_esc(f"{label} / {prob}: {_num(v)} us per step")}</title></circle>')
        vtxt = _num(med)
        if bw > len(vtxt) * _LBL_CHAR_W + 12:
            parts.append(f'<text class="lbl" x="{_fmt(ml + bw - 6)}" y="{_fmt(y + 15)}" '
                         f'text-anchor="end">{vtxt}</text>')
        else:
            parts.append(f'<text class="lbl" x="{_fmt(ml + bw + 6)}" y="{_fmt(y + 15)}">{vtxt}</text>')
    svg = (f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
           'aria-label="Measured microseconds per Q15 step, per method">'
           + "".join(parts) + "</svg>")
    return ('<figure><figcaption>Measured wall clock per Q15 step for each method, linear '
            "axis in microseconds. The bar length and printed value are the median across "
            "the benchmark problems; each dot is one problem. Every method runs the same "
            "pinned solve_q15 path, so row differences isolate tableau cost. Blue is a "
            "discovered method, orange a classical anchor; hover any mark for exact "
            "values.</figcaption>"
            + _legend([("var(--s1)", "discovered (archive)"), ("var(--s2)", "classical anchor")])
            + svg + "</figure>")


def _speed_sentence(bench) -> str:
    """One measured-speed sentence for verdict spots, traced to the speedup section.

    Returns an empty string when the benchmark file (or any cited field) is absent,
    so no page states a wall-clock figure it cannot trace to results.json.
    """
    if not isinstance(bench, dict):
        return ""
    sp = bench.get("speedup")
    if not isinstance(sp, dict):
        return ""
    champ = str(sp.get("champion") or "")
    base = str(sp.get("baseline") or "")
    per = sp.get("per_method_us_per_step")
    if not champ or not base or not isinstance(per, dict):
        return ""
    cm = per.get(champ, {}).get("median_us_per_step") if isinstance(per.get(champ), dict) else None
    bm = per.get(base, {}).get("median_us_per_step") if isinstance(per.get(base), dict) else None
    gm = sp.get("geomean_measured_speedup_rk4_over_champion")
    if not (_finite_pos(cm) and _finite_pos(bm) and _finite_pos(gm)):
        return ""
    return ("Measured wall clock agrees with the cycle model: in the benchmark head-to-head "
            f"the champion tableau ({_esc(champ[:8])}) runs in {_num(cm)} us per Q15 step "
            f"against {_num(bm)} us for {_esc(base)} (medians across "
            f"{_num(sp.get('n_problems_compared'))} problems, geometric-mean per-step "
            f'speedup {float(gm):.3f}x); details on the <a href="benchmark.html">benchmark '
            "page</a>.")


def render_benchmark(data: dict) -> str:
    sp = data.get("speedup") if isinstance(data.get("speedup"), dict) else {}
    methods = data.get("methods") or []
    verdicts = data.get("verdicts") if isinstance(data.get("verdicts"), dict) else {}
    corr = data.get("correlation") if isinstance(data.get("correlation"), dict) else {}
    env = data.get("environment") if isinstance(data.get("environment"), dict) else {}
    caveats = [str(c) for c in (data.get("caveats") or [])]
    kind_of = {str(m.get("name_or_hash")): str(m.get("kind", "")) for m in methods}
    champ = str(sp.get("champion") or "")
    base = str(sp.get("baseline") or "")
    parts = [
        '<p class="lead">This page reports the library benchmark: measured wall-clock '
        "timings of the benchmark methods on a desktop Python environment, next to the "
        "analytic cycle model the rest of this site ranks by. The fixed-step rows run the "
        "identical pinned Q15 path at the shared "
        + _gloss("cycle-budget", "cycle budget") + f" of {_num(data.get('budget_cycles'))} "
        "cycles; the scipy adaptive integrators are accuracy context in a different regime. "
        "Accuracy numbers are deterministic; timing numbers are measured on one machine and "
        "vary run to run.</p>"
    ]
    # headline cards, all cited from the speedup and correlation sections
    cards: list[tuple[str, str, str]] = []
    gm_m = sp.get("geomean_measured_speedup_rk4_over_champion")
    gm_p = sp.get("geomean_predicted_speedup_rk4_over_champion")
    if _finite_pos(gm_m):
        cards.append(("measured speedup", f"{float(gm_m):.3f}x",
                      f"geometric mean, {base or 'baseline'} over champion, per step"))
    if _finite_pos(gm_p):
        cards.append(("predicted speedup", f"{float(gm_p):.3f}x",
                      f"cycle-model quotient under {data.get('cost_model')}"))
    per = sp.get("per_method_us_per_step")
    if isinstance(per, dict):
        for name, head in ((champ, "champion"), (base, str(base))):
            d = per.get(name)
            if isinstance(d, dict) and _finite_pos(d.get("median_us_per_step")):
                cards.append((head, f"{_num(d['median_us_per_step'])} us/step",
                              "median across problems, measured"))
    if _finite_pos(corr.get("pearson_r")):
        cards.append(("cycles against time", f"r = {float(corr['pearson_r']):.3f}",
                      f"Pearson r over {_num(corr.get('n_points'))} fixed-step Q15 runs"))
    if cards:
        parts.append(_cards(cards))

    chart = _bench_us_chart(sp, methods)
    if chart:
        parts.append("<h2>Measured time per step</h2>")
        parts.append('<div class="panel">' + chart + "</div>")
        parts.append('<div class="scroll"><table><tr><th>method</th><th>kind</th>'
                     '<th class="num">order</th><th class="num">stages</th>'
                     '<th class="num">min us/step</th><th class="num">median us/step</th>'
                     '<th class="num">max us/step</th></tr>')
        pm = sp.get("per_method_us_per_step") or {}
        for m in methods:
            name = str(m.get("name_or_hash"))
            d = pm.get(name)
            if not isinstance(d, dict):
                continue
            parts.append(
                "<tr>"
                f'<td class="hash">{_esc(_vlabel(name, str(m.get("kind", ""))))}</td>'
                f"<td>{_esc(str(m.get('kind', '')))}</td>"
                f'<td class="num">{_num(m.get("order"))}</td>'
                f'<td class="num">{_num(m.get("stages"))}</td>'
                f'<td class="num">{_num(d.get("min_us_per_step"))}</td>'
                f'<td class="num">{_num(d.get("median_us_per_step"))}</td>'
                f'<td class="num">{_num(d.get("max_us_per_step"))}</td>'
                "</tr>")
        parts.append("</table></div>")

    rows = [r for r in (sp.get("rows") or []) if isinstance(r, dict)]
    if rows:
        champ_label = _vlabel(champ, kind_of.get(champ, "discovered")) if champ else "champion"
        parts.append(f"<h2>Champion against {_esc(base or 'the baseline')}: predicted and measured</h2>")
        if sp.get("regime"):
            parts.append(f"<p>{_esc(str(sp.get('regime')))}</p>")
        parts.append('<div class="scroll"><table><tr><th>problem</th>'
                     '<th class="num">champion cycles/step</th><th class="num">rk4 cycles/step</th>'
                     '<th class="num">predicted ratio</th><th class="num">measured ratio</th>'
                     '<th class="num">champion us/step</th><th class="num">rk4 us/step</th>'
                     '<th class="num">champion Q15 error</th><th class="num">rk4 Q15 error</th>'
                     '<th>lower error</th>'
                     '<th class="num">champion budget s</th><th class="num">rk4 budget s</th></tr>')
        def _ratio(v) -> str:
            return f"{float(v):.3f}" if isinstance(v, (int, float)) else "n/a"

        def _secs(v) -> str:
            return f"{float(v):.3g}" if isinstance(v, (int, float)) else "n/a"

        for r in rows:
            lower = champ_label if r.get("champion_error_lower") else (base or "baseline")
            parts.append(
                "<tr>"
                f"<td>{_esc(str(r.get('problem')))}</td>"
                f'<td class="num">{_num(r.get("champion_cycles_per_step"))}</td>'
                f'<td class="num">{_num(r.get("rk4_cycles_per_step"))}</td>'
                f'<td class="num">{_ratio(r.get("predicted_ratio_rk4_over_champion"))}</td>'
                f'<td class="num">{_ratio(r.get("measured_ratio_rk4_over_champion"))}</td>'
                f'<td class="num">{_num(r.get("champion_us_per_step"))}</td>'
                f'<td class="num">{_num(r.get("rk4_us_per_step"))}</td>'
                f'<td class="num">{_num(r.get("champion_error"))}</td>'
                f'<td class="num">{_num(r.get("rk4_error"))}</td>'
                f"<td>{_esc(lower)}</td>"
                f'<td class="num">{_secs(r.get("champion_budget_seconds"))}</td>'
                f'<td class="num">{_secs(r.get("rk4_budget_seconds"))}</td>'
                "</tr>")
        parts.append("</table></div>")
        summary_bits = []
        if _finite_pos(gm_m) and _finite_pos(gm_p):
            summary_bits.append(
                f"Geometric mean over the rows: measured {float(gm_m):.3f}, predicted "
                f"{float(gm_p):.3f}; ratios above 1.0 mean the champion needs less time per step.")
        if isinstance(sp.get("champion_error_lower_count"), int):
            summary_bits.append(
                f"At the same budget the champion reaches the lower Q15 error in "
                f"{_num(sp.get('champion_error_lower_count'))} of "
                f"{_num(sp.get('error_comparisons'))} problems; the median error ratio "
                f"(champion over {base or 'baseline'}) is "
                f"{_num(sp.get('median_error_ratio_champion_over_rk4'))}.")
        if summary_bits:
            parts.append("<p>" + " ".join(summary_bits) + "</p>")
        if verdicts.get("cycle_model"):
            parts.append(f"<p>{_esc(str(verdicts.get('cycle_model')))}</p>")
        if sp.get("caveat"):
            parts.append(f'<p class="note">{_esc(str(sp.get("caveat")))}</p>')

    adaptive = [r for r in (data.get("adaptive_results") or []) if isinstance(r, dict)]
    if adaptive:
        parts.append("<h2>Library accuracy at matched tolerance (adaptive regime)</h2>")
        prob_order = {str(p.get("name")): i for i, p in enumerate(data.get("problems") or [])}
        never_same_work = next(
            (c for c in caveats if "same-work" in c),
            "Adaptive integrators choose their own step counts; their wall clock is "
            "reported for context and is never a same-work comparison with any "
            "fixed-step run.")
        table = ['<div class="scroll"><table><tr><th>problem</th><th>integrator</th>'
                 '<th class="num">error</th><th class="num">rtol</th><th class="num">atol</th>'
                 '<th class="num">accepted steps</th><th class="num">rhs evaluations</th>'
                 '<th class="num">median s per solve</th><th>status</th></tr>']
        for r in sorted(adaptive, key=lambda r: (prob_order.get(str(r.get("problem")), 99),
                                                 str(r.get("problem")), str(r.get("integrator")))):
            timing = r.get("timing") if isinstance(r.get("timing"), dict) else {}
            table.append(
                "<tr>"
                f"<td>{_esc(str(r.get('problem')))}</td>"
                f"<td>{_esc(str(r.get('integrator')))}</td>"
                f'<td class="num">{_num(r.get("error"))}</td>'
                f'<td class="num">{_num(r.get("rtol"))}</td>'
                f'<td class="num">{_num(r.get("atol"))}</td>'
                f'<td class="num">{_num(r.get("n_steps_accepted"))}</td>'
                f'<td class="num">{_num(r.get("nfev"))}</td>'
                f'<td class="num">{_num(timing.get("median_s"))}</td>'
                f"<td>{_esc(str(r.get('status')))}</td>"
                "</tr>")
        table.append("</table></div>")
        parts.append("<figure><figcaption>" + _esc(never_same_work) + "</figcaption>"
                     + "\n".join(table) + "</figure>")
        if data.get("tolerance_rule"):
            parts.append(f'<p class="note">{_esc(str(data.get("tolerance_rule")))}</p>')
        if verdicts.get("matched_tolerance"):
            parts.append(f"<p>{_esc(str(verdicts.get('matched_tolerance')))}</p>")

    if verdicts.get("overall"):
        parts.append("<h2>Verdict</h2>")
        parts.append(f"<p>{_esc(str(verdicts.get('overall')))}</p>")

    env_bits = []
    if env.get("implementation") or env.get("python"):
        env_bits.append(f"{env.get('implementation', 'Python')} {env.get('python', '')}".strip())
    if env.get("os"):
        env_bits.append(f"{env.get('os')} ({env.get('machine', '')})".replace(" ()", ""))
    for lib in ("scipy", "numpy"):
        if env.get(lib):
            env_bits.append(f"{lib} {env.get(lib)}")
    if env.get("cpu"):
        env_bits.append(f"CPU {env.get('cpu')}")
    if env_bits:
        parts.append('<p class="note">Environment: ' + _esc("; ".join(env_bits)) + ".</p>")
    if env.get("timing_caveat"):
        parts.append(f'<p class="note">{_esc(str(env.get("timing_caveat")))}</p>')

    gen = data.get("generated_from")
    if isinstance(gen, dict):
        parts.append("<h2>Provenance</h2>")
        rows_out = []
        for k in sorted(gen.keys(), key=str):
            v = gen[k]
            if isinstance(v, list):
                shown = ", ".join(str(x) for x in v)
            else:
                shown = str(v)
            rows_out.append(f'<dt>{_esc(str(k))}</dt><dd><span class="hash">{_esc(shown)}</span></dd>')
        parts.append('<dl class="meta">\n' + "\n".join(rows_out) + "\n</dl>")
    return _page("library benchmark", "\n".join(parts), active="benchmark.html",
                 subtitle="Measured wall clock next to the analytic cycle model, on one "
                          "desktop machine.")


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


def _load_validation() -> dict | None:
    path = work_dir() / "validation" / "results.json"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _load_benchmark() -> dict | None:
    path = work_dir() / "benchmark" / "results.json"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _load_sidetrack() -> dict | None:
    """The side-track ledger plus the artifact each measured point produced.

    Absent until the side tracks are switched on (`run.sidetrack_every_cycles`), so the
    page and its nav entry appear only once there is something to show, exactly as
    validation and benchmark do.
    """
    path = work_dir() / "sidetrack" / "ledger.jsonl"
    if not path.exists():
        return None
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    if not rows:
        return None
    artifacts: dict[str, dict] = {}
    for r in rows:
        rel = str(r.get("artifact") or "")
        if not rel or rel in artifacts:
            continue
        p = work_dir() / rel
        if not p.exists():
            continue
        try:
            with open(p, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            artifacts[rel] = doc
    return {"ledger": rows, "artifacts": artifacts}


# ----------------------------------------------------------------------------
# off-list documents: loaded for presence, never for a published number
# ----------------------------------------------------------------------------

# The unpinned lane archives, one directory per method class. Written by the lane
# search, which this module neither imports nor runs: it reads the file if it is there.
LANE_CLASSES: tuple[str, ...] = ("implicit", "adaptive")


def _load_validation_axes() -> dict | None:
    """The two-axis validation document, or None when it has not been written.

    Absent is the normal state: nothing in the cycle builds it yet. Loaded so a class
    page can say whether it exists; its numbers stay off the site, because the document
    says in its own schema that it is not on the traceability list.
    """
    return _load_json_or_none(work_dir() / "validation" / "axes.json")


def _load_lane_archive(cls: str) -> dict | None:
    """One class's unpinned lane archive, or None.

    Same rule as the axes document: presence only. A lane record is not comparable with
    an archive record, and the lane records say so themselves.
    """
    if cls not in LANE_CLASSES:
        return None
    return _load_json_or_none(work_dir() / f"{cls}_archive" / "elites.json")


def _load_shares() -> dict | None:
    """The per-cycle lane log summarised per method class, or None."""
    return _load_json_or_none(work_dir() / "schedule" / "shares.json")


# ----------------------------------------------------------------------------
# the off-archive ledger, shared by the two side-class pages
# ----------------------------------------------------------------------------

# Summary keys that read as the shape of a point rather than as one of its results, so
# they lead the table instead of landing wherever the alphabet puts them.
_ST_KEY_ORDER = ("points", "finished", "candidates", "statuses")


def _soft(text) -> str:
    """Escape a string that came out of an artifact, after the vocabulary pass.

    Artifact text is written by prototype code and by failure messages, so one unlucky
    word would fail check_banned and publish nothing that cycle. literature.soften is a
    pure substitution, so routing every artifact-sourced string through it keeps the
    build deterministic and keeps the reader's message intact.
    """
    return _esc(literature_mod.soften(str(text)))


def _st_cell(v) -> str:
    """One summary value, compactly. Every container is walked in sorted order so the
    rendered page stays byte-identical across builds."""
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return _num(v)
    if isinstance(v, list):
        return ", ".join(_st_cell(x) for x in v) if v else "none"
    if isinstance(v, dict):
        return "; ".join(f"{_soft(k)} {_st_cell(v[k])}" for k in sorted(v)) if v else "none"
    return _soft(v)


def _track_entries(sidetrack, track: str) -> list[dict]:
    """Completed ledger entries for one method class, sorted by (job, point).

    Sorted rather than left in file order, so a page is a function of what the ledger
    holds and not of the order the executor happened to measure in.
    """
    if not isinstance(sidetrack, dict):
        return []
    rows = [e for e in (sidetrack.get("ledger") or [])
            if isinstance(e, dict) and e.get("status") == "ok"
            and str(e.get("track", "")) == track]
    return sorted(rows, key=lambda e: (str(e.get("job", "")), str(e.get("key", ""))))


def _art_text(doc, *keys) -> str:
    """The earliest non-empty artifact field among keys, softened and escaped."""
    if not isinstance(doc, dict):
        return ""
    for k in keys:
        v = str(doc.get(k) or "").strip()
        if v:
            return _soft(v)
    return ""


def _summary_max(entries, key: str, job: str | None = None):
    """The largest finite value of one summary key over the given points, or None."""
    out = None
    for e in entries:
        if job is not None and str(e.get("job", "")) != job:
            continue
        summary = e.get("summary") if isinstance(e.get("summary"), dict) else {}
        v = summary.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
            continue
        out = float(v) if out is None else max(out, float(v))
    return out


def _code_hashes(entries) -> list[str]:
    return sorted({str(e.get("code_hash", "")) for e in entries if e.get("code_hash")})


def _distinct_points(entries) -> int:
    """Points, not ledger lines.

    A point re-measured after the executor changed leaves a second line under a second
    code hash, and the two lines are one point measured twice. Counting lines inflates
    the total by however many points a code change re-opened, which is a number that
    says something about the edit history and nothing about the plan.
    """
    return len({(str(e.get("job", "")), str(e.get("key", ""))) for e in entries})


def _newest_code_hash(entries) -> str:
    """The hash the most recent measurement ran under, or "" if none is stamped.

    By timestamp, not alphabetically. These are digests, so their lexicographic order
    carries no information at all, and taking the last one advertises whichever hash
    happens to sort highest as the code the readings were taken under. The card is
    read as provenance, so it has to be the current one.
    """
    stamped = [e for e in entries if e.get("code_hash")]
    if not stamped:
        return ""
    newest = max(stamped, key=lambda e: (str(e.get("ts", "")), str(e.get("code_hash", ""))))
    return str(newest.get("code_hash", ""))


def _points_caption(entries) -> str:
    """Says so when the line count and the point count differ, rather than hiding it."""
    extra = len(entries) - _distinct_points(entries)
    if extra <= 0:
        return "one per parameter point in the plan"
    return (f"one per parameter point in the plan; {extra} of them were measured again "
            "after the executor changed")


def _ledger_cards(entries, extra=()) -> str:
    """The card row every off-archive page opens with, plus this class's own cards."""
    jobs = sorted({str(e.get("job", "")) for e in entries if e.get("job")})
    newest = _newest_code_hash(entries)
    cards = [("points measured", str(_distinct_points(entries)), _points_caption(entries)),
             ("jobs", str(len(jobs)), "each closes one open design question")]
    cards.extend(extra)
    cards.append(("code hash", newest[:12] if newest else "n/a",
                  "digest over the executor and the prototypes, at the latest measurement"))
    return _cards(cards)


def _job_tables(entries, artifacts) -> list[str]:
    """One section per job: what it closes, the arithmetic it ran in, and its points.

    The same renderer the measurement ledger used when both side classes shared a page,
    now taking an already-filtered list so each class page shows only its own jobs.
    """
    by_job: dict[str, list[dict]] = {}
    for e in entries:
        by_job.setdefault(str(e.get("job", "")), []).append(e)
    parts: list[str] = []
    for job in sorted(by_job):
        rows_in = sorted(by_job[job], key=lambda e: str(e.get("key", "")))
        doc = artifacts.get(str(rows_in[0].get("artifact", "")), {})
        parts.append(f"<h3>{_esc(job)}</h3>")
        parts.append(f'<p class="sub">{len(rows_in)} points</p>')
        closes = _art_text(doc, "closes")
        if closes:
            parts.append(f"<p>Closes: {closes}</p>")
        arith = _art_text(doc, "arithmetic")
        if arith:
            parts.append(f'<p class="note">Arithmetic: {arith}</p>')
        note = _art_text(doc, "construction", "question", "note")
        if note:
            parts.append(f'<p class="note">{note}</p>')
        # Counts lead, then everything else alphabetically. Both halves are a total
        # order, so the column list is a function of the data and nothing else.
        seen = {k for e in rows_in
                for k in (e.get("summary") or {}) if isinstance(e.get("summary"), dict)}
        keys: list[str] = sorted(
            seen, key=lambda k: (_ST_KEY_ORDER.index(k) if k in _ST_KEY_ORDER
                                 else len(_ST_KEY_ORDER), k))
        head = ("<tr><th>point</th><th>cycle</th>"
                + "".join(f"<th>{_soft(k)}</th>" for k in keys) + "</tr>")
        rows = []
        for e in rows_in:
            summary = e.get("summary") if isinstance(e.get("summary"), dict) else {}
            cells = "".join(f"<td>{_st_cell(summary.get(k))}</td>" for k in keys)
            rows.append(f'<tr><th class="mono">{_soft(e.get("key"))}</th>'
                        f'<td class="num">{_num(e.get("cycle"))}</td>{cells}</tr>')
        parts.append('<div class="scroll"><table>\n' + head + "\n"
                     + "\n".join(rows) + "\n</table></div>")
    return parts


# The canonical invariants for every off-archive number on this site. One constant,
# rendered by the measurement ledger and by both side-class pages, so the invariants
# cannot drift into two versions that disagree.
_NOT_THESE_NUMBERS: tuple[tuple[str, str], ...] = (
    ("Not scored.",
     "No off-archive measurement enters the archive, changes an elite, or affects a "
     "hypothesis verdict. The executor sits outside the verifier hash by construction."),
    ("Not Q15.",
     "Except where a job says otherwise, nothing here carries the quantization effects "
     "that dominate the archive, the floor bias in particular, and nothing here runs "
     "under a cycle budget. Each job states its own arithmetic, because they differ: "
     "the solver jobs run in float64, while the stability scan is exact over rationals "
     "with only the measured order in float."),
    ("Not a cost comparison with the archive.",
     "There is no shared cycle budget. Where cycles per step appear, the explicit "
     "anchors are priced by the same pinned cost model the archive uses, and SDIRK2 is "
     "priced by the unpinned prototype estimate with a finite-difference Jacobian. "
     "Neither figure includes the right-hand side or the Jacobian evaluation itself."),
    ("Preliminary.",
     "These exist to choose the parameters that get frozen at an epoch boundary. The "
     "scored implementation is written fresh against the pinned interfaces when that "
     "boundary arrives."),
)

_NOT_THESE_WHERE = ("Plan, job catalogue and invariants: docs/SIDETRACK-AUTOMATION.md in "
                    "the harness repository. Designs these feed: docs/EPOCH2-DESIGN.md "
                    "and docs/EPOCH3-DESIGN.md.")


def _not_these_numbers() -> str:
    items = "".join(f"<li><strong>{_esc(h)}</strong> {_esc(b)}</li>"
                    for h, b in _NOT_THESE_NUMBERS)
    return ("<h2>What these numbers are not</h2>\n"
            f"<ul>{items}</ul>\n"
            f'<p class="note">{_esc(_NOT_THESE_WHERE)}</p>')


_NO_POINTS = ("No measurements recorded under the current code hash. The ledger for this "
              "class is empty, which is where a fresh work directory starts; the sections "
              "that would carry numbers say so rather than showing an empty table or a "
              "zero that reads as a measurement.")


# ----------------------------------------------------------------------------
# library counterparts, read from benchmark/results.json
# ----------------------------------------------------------------------------

# Which method class each scipy integrator belongs to. Held here as data rather than
# imported: sitegen runs inside the cycle and rk_harness.benchcounts pulls in the
# prototypes and the second pass. tests/test_t18_class_pages.py asserts this mapping
# agrees with benchcounts.SCIPY_ADAPTIVE and benchcounts.SCIPY_IMPLICIT, so the two
# cannot drift apart without a failing test.
_LIB_CLASS: dict[str, str] = {
    "RK23": "adaptive", "RK45": "adaptive", "DOP853": "adaptive",
    "Radau": "implicit", "BDF": "implicit", "LSODA": "implicit",
}

_NEVER_SAME_WORK = ("Adaptive and implicit library integrators choose their own step "
                    "counts, so their work is reported for context and is never a "
                    "same-work comparison with any fixed-step run on this site.")


def _sort_num(v) -> tuple:
    """A total order that puts every finite number ahead of everything else."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
        return (1, 0.0)
    return (0, float(v))


def _library_rows(benchmark, cls: str) -> list[dict]:
    """benchmark/results.json adaptive_results, restricted to one method class."""
    if not isinstance(benchmark, dict):
        return []
    rows = [r for r in (benchmark.get("adaptive_results") or [])
            if isinstance(r, dict) and _LIB_CLASS.get(str(r.get("integrator"))) == cls]
    return sorted(rows, key=lambda r: (str(r.get("problem")), str(r.get("integrator")),
                                       _sort_num(r.get("rtol"))))


def _library_table(rows) -> str:
    out = ['<div class="scroll"><table><tr><th>problem</th><th>integrator</th>'
           '<th class="num">error</th><th class="num">rtol</th><th class="num">atol</th>'
           '<th class="num">accepted steps</th><th class="num">rhs evaluations</th>'
           '<th class="num">median s per solve</th><th>status</th></tr>']
    for r in rows:
        timing = r.get("timing") if isinstance(r.get("timing"), dict) else {}
        out.append(
            "<tr>"
            f"<td>{_soft(r.get('problem'))}</td>"
            f"<td>{_soft(r.get('integrator'))}</td>"
            f'<td class="num">{_num(r.get("error"))}</td>'
            f'<td class="num">{_num(r.get("rtol"))}</td>'
            f'<td class="num">{_num(r.get("atol"))}</td>'
            f'<td class="num">{_num(r.get("n_steps_accepted"))}</td>'
            f'<td class="num">{_num(r.get("nfev"))}</td>'
            f'<td class="num">{_num(timing.get("median_s"))}</td>'
            f"<td>{_soft(r.get('status'))}</td>"
            "</tr>")
    out.append("</table></div>")
    return "\n".join(out)


_SERIES_SWATCH = ("var(--s1)", "var(--s2)", "var(--s3)")


def _workprec_chart(rows, aria: str) -> str:
    """Achieved error against derivative evaluations, log-log, one line per series.

    A series is one (problem, integrator) pair, drawn in tolerance order. With a single
    tolerance rung on the ladder a series is one mark and no line is drawn; the caption
    says so rather than implying a curve that was never measured. Geometry comes from
    the same _LogLog, _log_ticks and _legend the other charts use.
    """
    pts = [(str(r.get("problem")), str(r.get("integrator")),
            float(r.get("nfev")), float(r.get("error")))
           for r in rows
           if _finite_pos(r.get("nfev")) and _finite_pos(r.get("error"))]
    if not pts:
        return ""
    integrators = sorted({i for _p, i, _x, _y in pts})
    swatch = {name: _SERIES_SWATCH[i % len(_SERIES_SWATCH)]
              for i, name in enumerate(integrators)}
    xs = [x for _p, _i, x, _y in pts]
    ys = [y for _p, _i, _x, y in pts]
    xlo = 10 ** math.floor(math.log10(min(xs)))
    xhi = 10 ** math.ceil(math.log10(max(xs)))
    ylo = 10 ** math.floor(math.log10(min(ys)))
    yhi = 10 ** math.ceil(math.log10(max(ys)))
    pl = _LogLog(640, 340, xlo, xhi, ylo, yhi,
                 "derivative evaluations per solve", "error against the reference")
    pl.frame()
    series: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for prob, integ, x, y in pts:
        series.setdefault((prob, integ), []).append((x, y))
    curves = 0
    for key in sorted(series):
        prob, integ = key
        ordered = sorted(series[key])
        sw = swatch[integ]
        if len(ordered) > 1:
            curves += 1
            path = " ".join(f"{'M' if i == 0 else 'L'} {_fmt(pl.x(x))} {_fmt(pl.y(y))}"
                            for i, (x, y) in enumerate(ordered))
            pl.parts.append(f'<path d="{path}" fill="none" stroke="{sw}" stroke-width="2"/>')
        for x, y in ordered:
            title = (f"{prob} / {integ}: error {_num(y)} after {int(x)} derivative "
                     "evaluations")
            pl.parts.append(f'<circle cx="{_fmt(pl.x(x))}" cy="{_fmt(pl.y(y))}" r="4.5" '
                            f'fill="{sw}" class="cellstroke">'
                            f"<title>{_soft(title)}</title></circle>")
    tail = ("Each line joins the tolerance rungs of one problem under one integrator."
            if curves else
            "The measured ladder holds one tolerance rung, so each series is a single "
            "mark and no line is drawn.")
    return ('<figure><figcaption>Achieved error against derivative evaluations, both '
            "axes log, one mark per solve. Down and to the left is better on both axes: "
            "less work for less error. " + _esc(tail) + " " + _esc(_NEVER_SAME_WORK)
            + " Hover a mark for exact values.</figcaption>"
            + _legend([(swatch[name], name) for name in integrators])
            + pl.svg(aria) + "</figure>")


# ----------------------------------------------------------------------------
# the implicit class
# ----------------------------------------------------------------------------

_IMPLICIT_CLASS = (
    "An implicit Runge-Kutta method lets a stage depend on itself: A has entries on and "
    "above the diagonal, so a stage is the solution of an equation rather than a sum of "
    "quantities already known. The prototype here is a two-stage SDIRK, which repeats a "
    "single value gamma down the diagonal and solves each stage with a fixed number of "
    "Newton iterations. That buys stability on stiff problems, where an explicit "
    "method's step size is capped by a mode that has already decayed away, and it costs "
    "a Jacobian and several derivative evaluations per stage.")

_IMPLICIT_OFF_ARCHIVE = (
    "The pinned verifier accepts a strictly lower triangular A, and a record has no "
    "field for gamma, for a Newton iteration count or for a Jacobian policy, so an "
    "implicit method cannot hold an archive cell as things stand. Everything on this "
    "page is measured outside the scored path.")


def render_implicit(sidetrack: dict | None = None, validation: dict | None = None,
                    benchmark: dict | None = None) -> str:
    entries = _track_entries(sidetrack, "implicit")
    artifacts = (sidetrack or {}).get("artifacts") or {}
    parts = [
        '<p class="lead">Implicit methods: the class that stays stable where an explicit '
        "method cannot, measured " + _gloss("off-archive", "off-archive") + " because the "
        "scored record schema has nowhere to put one. This page carries what the run has "
        "measured about the class, the evidence for why it is worth measuring, and a "
        "plain statement of what these numbers are not.</p>",
        f'<p class="note">{_esc(_IMPLICIT_CLASS)}</p>',
        f'<p class="note">{_esc(_IMPLICIT_OFF_ARCHIVE)}</p>',
    ]
    if entries:
        window = _summary_max(entries, "l_stable_gammas", job="sdirk.gamma_a21_scan")
        extra = []
        if window is not None:
            extra.append(("L-stable gammas", _num(int(window)),
                          "the scan has found none at any sdirk.gamma_a21_scan point"
                          if window == 0 else
                          "most found at any single sdirk.gamma_a21_scan point"))
        parts.append(_ledger_cards(entries, extra))
    else:
        parts.append(f"<p>{_esc(_NO_POINTS)}</p>")

    parts.append("<h2>Why this class exists</h2>")
    verdicts = (validation or {}).get("verdicts") if isinstance(validation, dict) else None
    per = (verdicts or {}).get("per_problem") or {}
    problems = (validation or {}).get("problems") or [] if isinstance(validation, dict) else []
    stiff_names = [str(p.get("name")) for p in problems
                   if isinstance(p, dict) and p.get("stiff")]
    no_fin = (verdicts or {}).get("stiff_problems_with_no_discovered_finisher")
    total = (verdicts or {}).get("stiff_problems_total")
    if stiff_names and isinstance(no_fin, int) and isinstance(total, int):
        parts.append(
            "<p>Stiff problems on the practical validation suite where no discovered "
            f"explicit method finishes at all: {_num(no_fin)} of {_num(total)}. On each "
            "of those, every discovered method overflows Q15 range before the "
            "integration ends. That is the gap this class is measured against, and it "
            "is the reason the run spends time on a class that cannot enter the "
            'archive. The counts below come from <a href="validation.html">'
            "validation/results.json</a>, which is also where the non-stiff half of the "
            "same suite is reported.</p>")
        parts.extend(_best_table(stiff_names, per, stiff_cols=True))
        parts.append('<p class="note">finishers are counted per problem: how many '
                     "classical anchors and how many discovered methods completed the "
                     "integration without overflowing. A problem with no discovered "
                     "finisher has no ratio, because there is nothing to divide.</p>")
    else:
        parts.append("<p>The practical validation suite has not reported a stiff subset "
                     "yet, so the size of the gap this class addresses is not stated "
                     "here. It is reported on the validation page once that suite has "
                     "run with stiff problems labelled.</p>")

    if entries:
        parts.append("<h2>What the run has measured</h2>")
        parts.append(_explain(
            "A <em>point</em> is one member of a job's finite, deterministic plan. A "
            "firing of the side-track executor measures the points it has not measured "
            "yet, writes each as its own artifact, and appends a line to the ledger "
            "these tables are rendered from.",
            "Every artifact is a pure function of the code and the point's parameters: "
            "no clock, no host detail, no unseeded randomness, so re-measuring a point "
            "reproduces it byte for byte. The <strong>code hash</strong> is a digest "
            "over the executor and the prototype modules, and a point counts as "
            "measured only under the hash that measured it, so editing a prototype "
            "re-opens its points instead of leaving stale numbers standing beside fresh "
            "ones.",
            "These runs are float64 and " + _gloss("off-archive", "off-archive") + " by "
            "design. They carry no " + _gloss("q15", "Q15") + " quantization, no floor "
            "bias and no cycle budget, so their errors are not comparable with anything "
            "on the " + _gloss("elite", "elite") + " grids. The comparison they support "
            "is between methods inside this class, not between this page and the "
            "archive."))
        parts.extend(_job_tables(entries, artifacts))

    lib = _library_rows(benchmark, "implicit")
    if lib:
        parts.append("<h2>Library implicit integrators</h2>")
        parts.append("<p>The same problems solved by the compiled implicit integrators "
                     "scipy provides, at the benchmark's tolerance. They are accuracy "
                     "and work context for the class, measured in a different regime "
                     'from anything else on this site; the <a href="benchmark.html">'
                     "benchmark page</a> carries the whole set and the environment they "
                     "ran in.</p>")
        chart = _workprec_chart(lib, "Error against derivative evaluations for the "
                                     "library implicit integrators")
        if chart:
            parts.append('<div class="panel">' + chart + "</div>")
        parts.append(_library_table(lib))
        parts.append(f'<p class="note">{_esc(_NEVER_SAME_WORK)}</p>')

    parts.append("<h2>Further evidence, not published here</h2>")
    parts.append(_offlist_panel([
        ("rk-work/validation/axes.json", _load_validation_axes() is not None,
         "the cycles-to-tolerance rows for this class, next to the explicit class on "
         "the same accuracy targets"),
        ("rk-work/implicit_archive/elites.json", _load_lane_archive("implicit") is not None,
         "the unpinned lane archive: candidate SDIRK parameter sets ranked among "
         "themselves"),
        ("rk-work/schedule/shares.json", _load_shares() is not None,
         "the per-cycle lane log summarised per method class"),
    ]))
    parts.append(_not_these_numbers())
    return _page("implicit methods", "\n".join(parts), active="implicit.html",
                 subtitle="SDIRK methods measured off-archive, and the stiff problems "
                          "that motivate them.")


# ----------------------------------------------------------------------------
# the adaptive class
# ----------------------------------------------------------------------------

_ADAPTIVE_CLASS = (
    "An adaptive method carries an embedded pair: two weight vectors over one set of "
    "stages, of different order. The difference between the two updates estimates the "
    "local error of the step, and a step-size controller turns that estimate into the "
    "next step size, shrinking after a step whose estimate exceeds the tolerance and "
    "growing when there is accuracy to spare. A step the controller declines is "
    "recomputed at a smaller size, so an adaptive run spends work on attempts that "
    "never advance the solution.")

_ADAPTIVE_OFF_ARCHIVE = (
    "A record holds one b vector, and the verifier scores a fixed step count against a "
    "fixed cycle budget. An embedded pair has a second b vector with nowhere to live, "
    "and an adaptive run has no fixed step count to score, so the class is measured "
    "outside the scored path and its runs are never ranked against an archive score.")

# Controller counters a point may record. Rendered as one table when at least one point
# carries any of them, so the page shows accepted and rejected work rather than only
# aggregate rates.
_CTRL_COLS: tuple[tuple[str, str], ...] = (
    ("total_accepted", "accepted steps"),
    ("total_rejected", "declined attempts"),
    ("rejection_rate", "declined share"),
    ("total_fevals", "rhs evaluations"),
    ("max_error", "largest error"),
)


def _controller_table(entries) -> str:
    rows = [e for e in entries
            if isinstance(e.get("summary"), dict)
            and any(k in e["summary"] for k, _label in _CTRL_COLS)]
    if not rows:
        return ""
    head = ("<tr><th>job</th><th>point</th><th>cycle</th>"
            + "".join(f'<th class="num">{_esc(label)}</th>' for _k, label in _CTRL_COLS)
            + "</tr>")
    body = []
    for e in rows:
        summary = e["summary"]
        cells = "".join(f'<td class="num">{_st_cell(summary.get(k))}</td>'
                        for k, _label in _CTRL_COLS)
        body.append(f"<tr><td>{_soft(e.get('job'))}</td>"
                    f'<th class="mono">{_soft(e.get("key"))}</th>'
                    f'<td class="num">{_num(e.get("cycle"))}</td>{cells}</tr>')
    return ('<div class="scroll"><table>\n' + head + "\n" + "\n".join(body)
            + "\n</table></div>")


def render_adaptive(sidetrack: dict | None = None, benchmark: dict | None = None) -> str:
    entries = _track_entries(sidetrack, "adaptive")
    artifacts = (sidetrack or {}).get("artifacts") or {}
    parts = [
        '<p class="lead">Adaptive methods: the class that chooses its own step size from '
        "an error estimate, measured " + _gloss("off-archive", "off-archive") + " because "
        "the scored record schema holds one set of weights and one fixed step count. "
        "This page carries what the run has measured about the class, the library "
        "integrators that stand next to it, and a plain statement of what these numbers "
        "are not.</p>",
        f'<p class="note">{_esc(_ADAPTIVE_CLASS)}</p>',
        f'<p class="note">{_esc(_ADAPTIVE_OFF_ARCHIVE)}</p>',
    ]
    if entries:
        extra = []
        rate = _summary_max(entries, "rejection_rate")
        if rate is None:
            rate = _summary_max(entries, "rejection_rate_max")
        if rate is not None:
            extra.append(("highest declined share", _num(rate),
                          "largest share of attempts the controller declined at any "
                          "measured point"))
        parts.append(_ledger_cards(entries, extra))
    else:
        parts.append(f"<p>{_esc(_NO_POINTS)}</p>")

    parts.append("<h2>What an embedded pair is</h2>")
    parts.append("<p>Two methods share one A matrix and one set of stage derivatives. "
                 "The higher-order weights b give the update that advances the solution; "
                 "the lower-order weights b_hat give a second update from the same "
                 "stages, and the difference between them is the error estimate, bought "
                 "for the price of the arithmetic on the difference alone. The "
                 + _gloss("step-size-controller", "step-size controller") + " compares "
                 "that estimate against the requested tolerance: when the estimate is "
                 "larger, the attempt is declined and recomputed at a smaller step size. "
                 "Nothing in an archive record can hold b_hat, which "
                 "is the whole of why this class sits outside the grids. The terms are "
                 'defined in the <a href="glossary.html#embedded-pair">glossary</a>.</p>')

    if entries:
        ctrl = _controller_table(entries)
        if ctrl:
            parts.append("<h2>Accepted and declined work</h2>")
            parts.append("<p>What the controller did at each measured point: how many "
                         "attempts advanced the solution, how many were declined and "
                         "recomputed, and how much derivative work the whole run cost. "
                         "The declined share is the number a fixed-step method has no "
                         "counterpart for, and it is the reason an adaptive run cannot "
                         "be priced by its step count alone.</p>")
            parts.append(ctrl)
        parts.append("<h2>What the run has measured</h2>")
        parts.append(_explain(
            "A <em>point</em> is one member of a job's finite, deterministic plan. A "
            "firing of the side-track executor measures the points it has not measured "
            "yet, writes each as its own artifact, and appends a line to the ledger "
            "these tables are rendered from.",
            "Every artifact is a pure function of the code and the point's parameters: "
            "no clock, no host detail, no unseeded randomness, so re-measuring a point "
            "reproduces it byte for byte. The <strong>code hash</strong> is a digest "
            "over the executor and the prototype modules, and a point counts as "
            "measured only under the hash that measured it.",
            "These runs are " + _gloss("off-archive", "off-archive") + " by design and, "
            "except where a job says otherwise, they are float64: no "
            + _gloss("q15", "Q15") + " quantization, no floor bias, no cycle budget. "
            "Their errors are not comparable with anything on the "
            + _gloss("elite", "elite") + " grids."))
        parts.extend(_job_tables(entries, artifacts))

    lib = _library_rows(benchmark, "adaptive")
    if lib:
        parts.append("<h2>Library adaptive integrators</h2>")
        parts.append("<p>The same problems solved by the compiled explicit adaptive "
                     "integrators scipy provides, at the benchmark's tolerance. They are "
                     "the counterpart this class gets to stand next to, in the same "
                     'place as its own numbers; the <a href="benchmark.html">benchmark '
                     "page</a> carries the whole set and the environment they ran in.</p>")
        chart = _workprec_chart(lib, "Error against derivative evaluations for the "
                                     "library adaptive integrators")
        if chart:
            parts.append('<div class="panel">' + chart + "</div>")
        parts.append(_library_table(lib))
        parts.append(f'<p class="note">{_esc(_NEVER_SAME_WORK)}</p>')

    parts.append("<h2>Further evidence, not published here</h2>")
    parts.append(_offlist_panel([
        ("rk-work/validation/axes.json", _load_validation_axes() is not None,
         "the cycles-to-tolerance rows for this class, and the measured floor of the "
         "Q15 error estimate that bounds how tight a tolerance can mean anything"),
        ("rk-work/adaptive_archive/elites.json", _load_lane_archive("adaptive") is not None,
         "the unpinned lane archive: candidate pairs and controller gains ranked among "
         "themselves"),
        ("rk-work/schedule/shares.json", _load_shares() is not None,
         "the per-cycle lane log summarised per method class"),
    ]))
    parts.append(_not_these_numbers())
    return _page("adaptive methods", "\n".join(parts), active="adaptive.html",
                 subtitle="Embedded pairs and step-size control, measured off-archive.")


# ----------------------------------------------------------------------------
# the measurement ledger: provenance for every off-archive point
# ----------------------------------------------------------------------------

def render_sidetrack(data: dict) -> str:
    """The provenance record. The readings moved to the class pages; the ledger did not.

    The file keeps its name and its URL. It has been published under sidetrack.html for
    long enough that the name is a permanent link as far as anyone who bookmarked it is
    concerned, so the page changes role rather than being deleted and replaced.
    """
    rows_all = [e for e in (data.get("ledger") or []) if isinstance(e, dict)]
    ok = sorted([e for e in rows_all if e.get("status") == "ok"],
                key=lambda e: (str(e.get("track", "")), str(e.get("job", "")),
                               str(e.get("key", ""))))
    failed = sorted([e for e in rows_all if e.get("status") == "failed"],
                    key=lambda e: (str(e.get("track", "")), str(e.get("job", "")),
                                   str(e.get("key", ""))))
    tracks = sorted({str(e.get("track", "")) for e in ok if e.get("track")})
    jobs_seen = sorted({str(e.get("job", "")) for e in ok if e.get("job")})
    codes = _code_hashes(ok)

    parts = [
        '<p class="lead">Every measurement the run has taken outside the scored archive, '
        "in one list, with the code hash that produced it and the artifact it was read "
        "from. This page is the provenance record; the readings themselves are on the "
        '<a href="implicit.html">implicit</a> and <a href="adaptive.html">adaptive</a> '
        "pages, where each class states what its numbers mean. Nothing here is scored, "
        "nothing here is Q15, and nothing here is ranked against the archive.</p>"
    ]
    newest = _newest_code_hash(ok)
    cards = [("points measured", str(_distinct_points(ok)), _points_caption(ok)),
             ("jobs", str(len(jobs_seen)), "each closes one open design question"),
             ("classes", ", ".join(tracks) or "none",
              "the two method classes the archive cannot hold"),
             ("code hash", newest[:12] if newest else "n/a",
              "digest over the executor and the prototypes, at the latest measurement")]
    if failed:
        cards.append(("failed points", str(len(failed)),
                      "recorded, retried, then set aside"))
    parts.append(_cards(cards))

    parts.append("<h2>The whole ledger</h2>")
    parts.append('<p class="note">Sorted by class, then job, then point, so the listing '
                 "is a function of what the ledger holds and not of the order the "
                 "executor happened to measure in. cycle is the run cycle the point was "
                 "measured on; the summary column is the artifact's own summary block, "
                 "with its keys in sorted order.</p>")
    if not ok:
        parts.append("<p>no points measured yet</p>")
    else:
        body = ['<div class="scroll"><table><tr><th>class</th><th>job</th><th>point</th>'
                '<th class="num">cycle</th><th>code hash</th><th>artifact</th>'
                "<th>summary</th></tr>"]
        for e in ok:
            summary = e.get("summary") if isinstance(e.get("summary"), dict) else {}
            body.append(
                "<tr>"
                f"<td>{_soft(e.get('track'))}</td>"
                f"<td>{_soft(e.get('job'))}</td>"
                f'<th class="mono">{_soft(e.get("key"))}</th>'
                f'<td class="num">{_num(e.get("cycle"))}</td>'
                f'<td class="hash">{_soft(str(e.get("code_hash", ""))[:12])}</td>'
                f'<td class="mono">{_soft(e.get("artifact"))}</td>'
                f"<td>{_st_cell(summary)}</td>"
                "</tr>")
        body.append("</table></div>")
        parts.append("\n".join(body))

    parts.append("<h2>The code-hash rule</h2>")
    parts.append("<p>Every artifact is a pure function of the code and the point's "
                 "parameters: no clock, no host detail, no unseeded randomness, so "
                 "re-measuring a point reproduces it byte for byte. The code hash is a "
                 "digest over the executor and the prototype modules, and a point counts "
                 "as measured only under the hash that measured it. Editing a prototype "
                 "re-opens its points instead of leaving stale numbers standing beside "
                 "fresh ones, which is why a listing can carry more than one hash while "
                 "a job is being re-measured.</p>")
    if len(codes) > 1:
        parts.append('<p class="note">Points in this listing were measured under '
                     f"{len(codes)} code hashes: "
                     + ", ".join(f'<span class="hash">{_esc(h)}</span>' for h in codes)
                     + ".</p>")

    parts.append("<h2>The retry policy</h2>")
    parts.append("<p>A point that raises is recorded with its message and retried on "
                 "later firings. After three failures under one code hash it is set "
                 "aside and reported here rather than retried forever, so one broken "
                 "point cannot consume the whole side-track budget. A side-track failure "
                 "never costs a cycle: the executor swallows it and the run continues.</p>")
    if failed:
        rows = "\n".join(
            f'<tr><th class="mono">{_soft(e.get("job"))}:{_soft(e.get("key"))}</th>'
            f'<td>{_soft(str(e.get("error", ""))[:200])}</td></tr>'
            for e in failed)
        parts.append("<h3>Points that did not complete</h3>")
        parts.append('<div class="scroll"><table>\n<tr><th>point</th><th>error</th></tr>\n'
                     + rows + "\n</table></div>")

    parts.append(_not_these_numbers())
    return _page("measurement ledger", "\n".join(parts), active="sidetrack.html",
                 subtitle="Provenance for every off-archive measurement: code hash, "
                          "artifact and summary.")


def build(arch: ArchiveState, out_dir: Path) -> None:
    global _PRESENT
    out_dir = Path(out_dir)
    validation = _load_validation()
    benchmark = _load_benchmark()
    sidetrack = _load_sidetrack()
    present = set()
    if validation is not None:
        present.add("validation.html")
    if benchmark is not None:
        present.add("benchmark.html")
    if sidetrack is not None:
        present.add("sidetrack.html")
    _PRESENT = frozenset(present)
    try:
        pages: dict[str, str] = {}
        pages["index.html"] = render_index(arch, benchmark=benchmark,
                                           validation=validation, sidetrack=sidetrack)
        # The three class pages are unconditional. A class that vanished from a fresh
        # build would take its nav entry with it, and the site would quietly go back to
        # having one method class.
        pages["explicit.html"] = render_explicit(arch, validation=validation,
                                                 benchmark=benchmark)
        pages["implicit.html"] = render_implicit(sidetrack=sidetrack,
                                                 validation=validation,
                                                 benchmark=benchmark)
        pages["adaptive.html"] = render_adaptive(sidetrack=sidetrack, benchmark=benchmark)
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
        if validation is not None:
            pages["validation.html"] = render_validation(validation, benchmark=benchmark)
        if benchmark is not None:
            pages["benchmark.html"] = render_benchmark(benchmark)
        if sidetrack is not None:
            pages["sidetrack.html"] = render_sidetrack(sidetrack)
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
    finally:
        _PRESENT = frozenset()
