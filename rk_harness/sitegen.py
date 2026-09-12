"""Findings site generator: SPEC §Surface/sitegen.py, HANDOFF §17.

Seven tabs (D41): overview, the three class pages, validation, the research log and
methodology. Retired pages are deleted by build(); see _RETIRED.

Pure HTML + inline SVG, no JavaScript, no wall-clock reads: the same ArchiveState always
produces byte-identical files. Stored UTC timestamps are displayed in US Central via
rk_harness.timefmt (a pure conversion of stored data); still no wall-clock reads. Every
page is checked against BANNED_WORDS before any file is written.

Charts follow the dataviz method: form first, colors by job (categorical slots 1-3 of the
validated reference palette for series, slot 4 only inside stacked bars, sequential blue
ramp for magnitude), status colors reserved for
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
AVR_NOTE = "avr_approx is an approximate model, shown for context only."

_EXHAUSTIVE_LABEL = "exhaustive: optimal within the enumerated space"
_SEARCH_LABEL = "search result"
_SEEDED_LABEL = "seeded classical baseline"
_MODEL_NOTE = ("Model-written text, in this section and the next. The sources are citations "
               "the model collected itself, so check them before relying on them.")

_BANNED_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in BANNED_WORDS) + r")\b",
    re.IGNORECASE,
)

# Reference palette. Slots 1-3 pass the all-pairs checks in light and dark; slot 4
# (--s4) passes the adjacent-pair checks only, so it appears in stacked bars and
# never in a scatter. Aqua's and yellow's light-surface contrast WARN is relieved by
# direct labels and table views, present on every chart page.
_STYLE = """
:root{
  color-scheme:light;
  --surface-0:#f5f4f2;--surface-1:#fcfcfb;--line:#dedcd6;--grid:#eceae5;
  --text-1:#0b0b0b;--text-2:#52514e;--text-3:#8a887f;
  --s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;
  --good-bg:#e0f2e3;--good-fg:#0d5c1d;--warn-bg:#fdf0d3;--warn-fg:#7a5300;
  --mut-bg:#eceae5;--mut-fg:#52514e;--bad-bg:#fbe3e3;--bad-fg:#8f1d1d;
  --q1:#cde2fb;--q2:#9ec5f4;--q3:#6da7ec;--q4:#3987e5;--q5:#256abf;--q6:#184f95;--q7:#0d366b;
  --on-q1:#0b0b0b;--on-q2:#0b0b0b;--on-q3:#0b0b0b;--on-q4:#0b0b0b;
  --on-q5:#ffffff;--on-q6:#ffffff;--on-q7:#ffffff;
  --banner-bg:#fdf0d3;--banner-line:#c98500;
}
@media (prefers-color-scheme: dark){
  :root{
    color-scheme:dark;
    --surface-0:#111110;--surface-1:#1a1a19;--line:#3a3936;--grid:#262523;
    --text-1:#ffffff;--text-2:#c3c2b7;--text-3:#8a887f;
    --s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;
    --good-bg:#123a1c;--good-fg:#9fdca9;--warn-bg:#42350b;--warn-fg:#ecc76a;
    --mut-bg:#262523;--mut-fg:#c3c2b7;--bad-bg:#461414;--bad-fg:#f1a5a5;
    --q1:#0d366b;--q2:#184f95;--q3:#256abf;--q4:#3987e5;--q5:#6da7ec;--q6:#9ec5f4;--q7:#cde2fb;
    --on-q1:#ffffff;--on-q2:#ffffff;--on-q3:#ffffff;--on-q4:#0b0b0b;
    --on-q5:#0b0b0b;--on-q6:#0b0b0b;--on-q7:#0b0b0b;
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
header.site .sub{max-width:none}
nav.tabs{display:flex;gap:2px;flex-wrap:wrap;margin:14px 0 0}
nav.tabs a{padding:8px 14px;border-radius:8px 8px 0 0;color:var(--text-2);
  text-decoration:none;font-size:13.5px;border:1px solid transparent;border-bottom:none}
nav.tabs a:hover{color:var(--text-1);background:var(--surface-0)}
nav.tabs a.on{background:var(--surface-0);border-color:var(--line);color:var(--text-1);
  font-weight:600;box-shadow:0 1px 0 var(--surface-0)}
.navrow{display:flex;align-items:flex-end;justify-content:space-between;gap:4px 16px;
  flex-wrap:wrap}
a.about{font-size:13.5px;color:var(--text-1);text-decoration:none;white-space:nowrap;
  padding:5px 12px;margin:0 0 6px;border:1px solid var(--line);border-radius:8px}
a.about:hover{background:var(--surface-0);text-decoration:underline}
a{color:var(--s1)}
/* card rows are a grid, so every card in a row has the same width and the rows line up */
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(165px,1fr));gap:12px;
  margin:16px 0}
.card{background:var(--surface-1);border:1px solid var(--line);border-radius:10px;
  padding:12px 16px;min-width:0;display:flex;flex-direction:column}
/* label, value and note line up across a row even where one label wraps to two lines:
   each card takes its three rows from the row's own grid */
.cards{grid-auto-rows:auto}
.cards>.card:not(.klass){display:grid;grid-template-rows:subgrid;grid-row:span 3}
.card .k{font-size:11px;color:var(--text-2);letter-spacing:.06em;text-transform:uppercase}
.card .v{font-size:26px;font-weight:650;font-variant-numeric:tabular-nums;
  letter-spacing:-.01em;line-height:1.25;margin:2px 0}
.card .v.vh{font:600 17px/1.9 ui-monospace,Consolas,monospace;letter-spacing:0;
  overflow-wrap:anywhere}
.card .d{font-size:12px;color:var(--text-3);margin-top:auto}
.panel{background:var(--surface-1);border:1px solid var(--line);border-radius:10px;
  padding:16px 18px;margin:16px 0}
/* a panel that holds one chart, one chart drawn at two widths, or one set of small
   multiples is as wide as its content rather than the page */
.panel:has(>figure>svg),.panel:has(>figure>.chart-wide),
.panel:has(>figure>.multiples){width:fit-content;max-width:100%}
figure{margin:0}
figure figcaption{font-size:13px;color:var(--text-2);margin:0 0 10px;max-width:72ch;
  line-height:1.5}
/* the cap is what stops a legend from setting the panel width: the panel is fit-content,
   and an uncapped flex row offers its whole single-line width as max-content, so five
   long solver names would widen the panel past the charts they label */
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:12.5px;color:var(--text-2);
  margin:2px 0 6px;max-width:640px}
.legend .sw{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;
  vertical-align:-1px}
.charts{display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start}
svg{max-width:100%;height:auto;display:block}
svg text{font:11px system-ui,-apple-system,"Segoe UI",sans-serif;fill:var(--text-2)}
svg .lbl{font-weight:600;fill:var(--text-1);paint-order:stroke;stroke:var(--surface-1);
  stroke-width:3px;stroke-linejoin:round}
svg .cv{font-weight:600}
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
.hash{word-break:break-all;font:12px ui-monospace,Consolas,monospace;color:var(--text-2);
  line-height:inherit}
p.note .hash{word-break:normal;white-space:nowrap}
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
table.side td{min-width:150px;max-width:34ch}
dl.gloss{margin:10px 0;max-width:82ch}
dl.gloss dt{font-weight:600;margin-top:12px}
dl.gloss dd{margin:2px 0 0;font-size:13.5px}
/* the methodology article: infobox, contents and references */
.meth-infobox{float:right;width:320px;margin:4px 0 18px 24px;font-size:12.5px}
.meth-infobox caption{caption-side:top;font-weight:700;font-size:13.5px;text-align:left;
  padding:6px 2px;color:var(--text-1)}
.meth-infobox th,.meth-infobox td{padding:4px 8px;white-space:normal;font-weight:400}
.meth-infobox th{width:42%;color:var(--text-2);font-weight:600;font-size:12px}
@media(max-width:760px){.meth-infobox{float:none;width:100%;margin:12px 0}}
.meth-toc{background:var(--surface-1);border:1px solid var(--line);border-radius:10px;
  padding:12px 18px 12px 40px;max-width:44ch;font-size:13.5px;margin:18px 0}
.meth-toc li{margin:3px 0}
.meth-refs{font-size:13px;max-width:82ch;padding-left:2.2em}
.meth-refs li{margin:4px 0}
sup.meth-cite{font-size:10.5px;line-height:0}
sup.meth-cite a{text-decoration:none}

/* the three method classes: one accent each, slots 1-3 of the validated palette */
.cards:has(>.card.klass){grid-template-columns:repeat(auto-fit,minmax(250px,1fr))}
.card.klass{border-left-width:3px;border-left-style:solid}
.card.k-explicit{border-left-color:var(--s1)}
.card.k-implicit{border-left-color:var(--s2)}
.card.k-adaptive{border-left-color:var(--s3)}
.card.klass .d{margin-top:0;margin-bottom:8px}
.card.klass .n{font-size:12.5px;color:var(--text-2);margin:0 0 8px;max-width:none}
.card.klass .g{font-size:12px;color:var(--text-3);margin:0 0 8px;max-width:none}
.card.klass .go{font-size:13px;margin:auto 0 0;max-width:none}
/* A class with nothing measured yet states that in words. Set smaller and in
   the secondary color so it does not read as a headline figure sitting beside
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
/* an open row shows its whole predicate, so no hover title has to repeat it */
details.led[open] .pred{white-space:normal;overflow-wrap:anywhere}
details.led .num,.ledhead .num{text-align:right}
details.led .badge{justify-self:start}
details.led .mono{font-size:12px;color:var(--text-2)}
details.led .rep{color:var(--text-3);font-weight:600}
details.led table{font-size:12px;margin:6px 0 0}
.hash a,a.hash{font:12px ui-monospace,Consolas,monospace}
/* class pages: the 2 by 2 heatmap grid, small multiples, legend keys drawn as marks */
.charts.grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;
  align-items:stretch}
.charts.grid2>.panel{margin:0}
.ptitle{margin:0 0 6px;font-size:13px;font-weight:600;color:var(--text-1)}
/* the matched-accuracy table: columns 5 to 9 are numbers, aligned here once rather
   than by a class on each of several hundred cells */
table.matched td:nth-child(n+5):nth-child(-n+9){text-align:right}
@media(max-width:900px){.charts.grid2{grid-template-columns:minmax(0,1fr)}}
/* small multiples size the container, not the column: fixed tracks give the box a
   max-content width of --cols panels, so the panel's fit-content wraps the charts
   instead of resolving to one very long row and then clamping to the full width */
.multiples{display:grid;grid-template-columns:repeat(var(--cols,3),minmax(0,var(--tw,272px)));
  gap:10px 14px;margin-top:6px;width:fit-content;max-width:100%}
.legend svg.key{display:inline-block;vertical-align:-1px;margin-right:5px;overflow:visible}
dl.glance{max-width:100ch;margin:6px 0 4px}
/* A chart drawn for a phone is emitted beside the wide one; only one of the two is ever
   shown, so a narrow screen gets the marks inside its viewport instead of the left edge
   of a 640-wide frame. */
.chart-phone{display:none}
/* phones: a chart keeps its drawn size and scrolls inside its figure or its panel, since
   shrinking a 640-wide chart to fit leaves 5 px text. The gradients are the cue that a
   figure scrolls: the pale pair hides where the content ends, the dark pair shows at an
   edge with more content past it, and background-attachment:local moves them with the
   scroll. The side-by-side table gives up its fixed widths so all four columns fit. */
@media(max-width:640px){
  figure{overflow-x:auto}
  figure svg{max-width:none}
  .charts.grid2>.panel{overflow-x:auto}
  figure,.charts.grid2>.panel{
    background:
      linear-gradient(90deg,var(--surface-1) 30%,transparent),
      linear-gradient(270deg,var(--surface-1) 30%,transparent) 100% 0,
      radial-gradient(farthest-side at 0 50%,rgba(0,0,0,.18),transparent),
      radial-gradient(farthest-side at 100% 50%,rgba(0,0,0,.18),transparent) 100% 0;
    background-repeat:no-repeat;
    background-size:28px 100%,28px 100%,14px 100%,14px 100%;
    background-attachment:local,local,scroll,scroll}
  .chart-wide{display:none}
  .chart-phone{display:block}
  table.side{table-layout:fixed;width:100%}
  table.side th,table.side td{padding:6px;font-size:12px}
  table.side th:nth-child(1){width:22%}
  table.side th{white-space:normal;min-width:0}
  table.side td{min-width:0;max-width:none;overflow-wrap:break-word}
}
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


def _count(v) -> str:
    """A count on a card, with thousands separators (141,364); anything else as _num."""
    if isinstance(v, int) and not isinstance(v, bool):
        return f"{v:,}"
    return _num(v)


def _tier_badge(tier: str) -> str:
    return f'<span class="tier tier-{_esc(tier)}">{_esc(tier)}</span>'


def _is_seeded(rec: Record) -> bool:
    """A classical tableau written in before the search began, not a search result.

    A startup seed carries no directive and lands in cycle 0. Both halves are needed: a
    search record can also carry no directive, and cycle 0 alone would claim any record
    the first cycle happened to archive.
    """
    return rec.directive_id is None and rec.cycle_id == 0


def _phase_label(rec: Record) -> str:
    """Which process put this record in its cell, read off the record itself.

    The exhaustive test comes first, because an enumerated entry carries a directive and
    so can never be mistaken for a seed.
    """
    did = rec.directive_id
    if isinstance(did, str) and did.startswith("D-E"):
        return _EXHAUSTIVE_LABEL
    if _is_seeded(rec):
        return _SEEDED_LABEL
    return _SEARCH_LABEL


def _cell_file(order: int, stages: int, bucket: int) -> str:
    return f"cell-p{order}-s{stages}-b{bucket}.html"


def _ct(value) -> str:
    """Stored UTC timestamp -> US Central display string (pure, deterministic)."""
    return timefmt.fmt_ct(value)


def _gloss(anchor: str, text: str) -> str:
    """A deep link into the glossary, which is the last section of methodology.html."""
    return f'<a href="methodology.html#{_esc(anchor)}">{_esc(text)}</a>'


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
        # a code hash is set in monospace at a size that fits the card's grid column
        vcls = "v vh" if re.fullmatch(r"[0-9a-f]{10,}", str(value)) else "v"
        out.append(f'<div class="card{extra}"><div class="k">{_esc(key)}</div>'
                   f'<div class="{vcls}">{_esc(value)}</div>'
                   f'<div class="d">{_esc(desc)}</div></div>')
    return '<div class="cards">' + "".join(out) + "</div>"


# The three method classes, in the order every page iterates them. Explicit leads
# because it is the class the verifier scores; the other two follow in a fixed order so
# the nav, the index cards and the page bodies cannot disagree about which is which.
CLASS_ORDER: tuple[str, ...] = ("explicit", "implicit", "adaptive")
_CLASS_PAGE = {cls: f"{cls}.html" for cls in CLASS_ORDER}


def _class_cards(rows) -> str:
    """The index's class row: one card per method class, each with one traced number.

    rows are (class, headline value, what the number is, the file it came from, blurb),
    plus an optional sixth element: the at-a-glance line naming the class's arithmetic
    and what checks it. A source of None means the document this card would have read
    has not been written, and then the card carries no number at all: `value` is a
    statement of absence and `what` says which document is missing. A zero here would be
    read as a measurement that came back empty, which is a different claim from not
    having measured, and the hub is the one page where the three classes sit side by
    side to be compared.
    """
    out = []
    for row in rows:
        cls, value, what, source, blurb = row[:5]
        glance = row[5] if len(row) > 5 else ""
        desc = _esc(what) if source is None else f"{_esc(what)}, from {_esc(source)}"
        klass = "card klass k-" + _esc(cls) + ("" if source is not None else " unmeasured")
        out.append(
            f'<div class="{klass}">'
            f'<div class="k">{_esc(cls)}</div>'
            f'<div class="v">{_esc(value)}</div>'
            f'<div class="d">{desc}</div>'
            f'<p class="n">{_esc(blurb)}</p>'
            + (f'<p class="g">{_esc(glance)}</p>' if glance else "")
            + f'<p class="go"><a href="{_esc(_CLASS_PAGE[cls])}">{_esc(cls)} methods</a></p>'
            "</div>")
    return '<div class="cards">' + "".join(out) + "</div>"


# Seven tabs in one row. The overview hub and the three method classes lead, in the
# order the site ranks them, so each class is one click from anywhere. Validation holds
# the evidence that spans classes, the research log holds the hypothesis ledger with the
# model's readings and digests, and methodology holds the method with the cost model,
# the measurement ledger and the glossary as its closing sections. Cell pages are not
# tabs: explicit.html links them and they keep the explicit tab active.
_NAV_ITEMS: tuple[tuple[str, str], ...] = (
    ("index.html", "overview"),
    ("explicit.html", "explicit"),
    ("implicit.html", "implicit"),
    ("adaptive.html", "adaptive"),
    ("validation.html", "validation"),
    ("hypotheses.html", "research log"),
    ("methodology.html", "methodology"),
)

# The tabs whose page may be absent. validation.html is written when any of
# validation/results.json, benchmark/results.json or falsification.json exists. build()
# raises _PRESENT to the hrefs it actually wrote and clears it in the finally, so every
# page's nav matches the pages on disk.
_CONDITIONAL = frozenset({"validation.html"})
_PRESENT: frozenset[str] = frozenset()

# Pages this site published under earlier layouts. GitHub Pages serves whatever is in
# docs/, so a page dropped from build() would stay live, stale and unlinked, until
# someone removed it by hand. build() deletes exactly these names, plus cell pages for
# cells the archive no longer holds, and nothing else.
_RETIRED: tuple[str, ...] = (
    "benchmark.html", "costmodel.html", "falsification.html", "glossary.html",
    "interpretation.html", "literature.html", "sidetrack.html",
)
_CELL_FILE_RE = re.compile(r"^cell-p\d+-s\d+-b\d+\.html$")


def _nav(active: str) -> str:
    links = []
    for href, label in _NAV_ITEMS:
        if href in _CONDITIONAL and href not in _PRESENT:
            continue
        on = ' class="on"' if href == active else ""
        links.append(f'<a href="{href}"{on}>{_esc(label)}</a>')
    return '<nav class="tabs">' + "".join(links) + "</nav>"


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
        f'<div class="navrow">{_nav(active)}'
        f'<a class="about" href="{OVERVIEW_URL}">About the project &#8599;</a></div>\n'
        "</div></header>\n"
        '<div class="wrap">\n'
        f"{body}\n"
        "<footer>\n"
        "<p>rk-harness findings: Runge-Kutta integrators for a Cortex-M0+ microcontroller.</p>\n"
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


def _phone_pair(wide: str, narrow: str) -> str:
    """Two drawings of one chart; the stylesheet shows whichever fits the screen.

    A 640-wide chart inside a 342-wide viewport opens on its left gutter, which is axis
    labels and few marks, and a scrollable figure gives no sign that the rest is there.
    The phone drawing puts the same marks inside the visible box. It carries no hover
    text, because a touch screen has no hover and the wide drawing keeps it.
    """
    return (f'<div class="chart-wide">{wide}</div>'
            f'<div class="chart-phone">{narrow}</div>')


def _side_label(px: float, py: float, text: str, w: float) -> str:
    """A mark's label, to the right of it unless that would run past the frame."""
    room = len(text) * 7.0
    if px + 8 + room <= w - 2:
        return f'<text class="lbl" x="{_fmt(px + 8)}" y="{_fmt(py)}">{_esc(text)}</text>'
    return (f'<text class="lbl" x="{_fmt(max(px - 8, room + 2))}" y="{_fmt(py)}" '
            f'text-anchor="end">{_esc(text)}</text>')


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
    def draw(w: int, tips: bool = True) -> str:
        pl = _LogLog(w, 340, xlo, xhi, ylo, yhi,
                     "cycles per step (m0plus_fast, n_states=1)", "held-out error")
        pl.frame()
        for order, stg, bucket, rec, cyc, err in elites:
            title = (f"order {order}, {stg} stages, bucket {bucket}: {_num(err)} held-out at {int(cyc)} cycles "
                     f"({rec.tier}); {rec.tableau_hash[:12]}")
            tip = f"<title>{_esc(title)}</title>" if tips else ""
            pl.parts.append(
                f'<a href="{_cell_file(order, stg, bucket)}"><circle cx="{_fmt(pl.x(cyc))}" cy="{_fmt(pl.y(err))}" '
                f'r="5" fill="var(--s1)" class="cellstroke">{tip}</circle></a>')
        for name, cyc, err in base:
            if err is None:
                continue
            px, py = pl.x(cyc), pl.y(err)
            tip = (f'<title>{_esc(name)}: {_num(err)} held-out at {int(cyc)} cycles</title>'
                   if tips else "")
            pl.parts.append(
                f'<path d="M {_fmt(px)} {_fmt(py - 6)} L {_fmt(px + 6)} {_fmt(py)} L {_fmt(px)} {_fmt(py + 6)} '
                f'L {_fmt(px - 6)} {_fmt(py)} Z" fill="var(--s2)" class="cellstroke">{tip}</path>')
            pl.parts.append(_side_label(px, py - 6, name, w))
        return pl.svg("Scatter of held-out error against cycles per step for archive "
                      "elites and classical baselines")

    fig = _phone_pair(draw(640), draw(430, tips=False))
    return ('<figure><figcaption>Blue dots are archive elites and orange diamonds '
            "classical baselines: analytic cycles per step under m0plus_fast against held-out "
            "error at the fixed " + _gloss("cycle-budget", "cycle budget") + ", both log, "
            "so down-left is better. A diamond appears only when its identical tableau is "
            "archived; each dot links to its cell page.</figcaption>"
            + _legend([("var(--s1)", "archive elite (links to its cell)"), ("var(--s2)", "classical baseline")])
            + fig + _ARCHIVE_SOURCE + "</figure>")


# The source line under the explicit scatter and the elite grids: both draw the archive.
_ARCHIVE_SOURCE = ('<p class="note">Source: rk-work/archive, the elite in each cell when '
                   "this page was built. Arithmetic: Q15 with floor rounding; cycles per "
                   "step from the pinned m0plus_fast model.</p>")


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
            # The value takes the ink token paired with its fill step, so it reads on a
            # pale cell and on a deep one in either theme without a halo.
            parts.append(
                f'<a href="{_cell_file(order, s, b)}"><rect x="{x}" y="{y}" width="{cw}" height="{ch}" rx="4" '
                f'fill="var(--q{step})" class="cellstroke"><title>{_esc(title)}</title></rect>'
                f'<text class="cv" style="fill:var(--on-q{step})" x="{_fmt(x + cw / 2)}" '
                f'y="{_fmt(y + ch / 2 + 3.5)}" text-anchor="middle">'
                f'{f"{float(err):.3g}" if _finite_pos(err) else "?"}</text></a>')
    parts.append(f'<text x="{ml}" y="{h - 6}">cycle bucket (m0plus_fast)</text>')
    svg = (f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
           f'aria-label="Order {order} elite grid heatmap">' + "".join(parts) + "</svg>")
    # A panel, not a figure: render_explicit puts the four orders in one figure that
    # carries the shared caption and the source line.
    return f'<p class="ptitle">Order {order}</p>{svg}'


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
            "slow multiplier cost models. Bar height is the per-step cost and the printed "
            "number is the exact cycle count. Which of the two "
            + _gloss("anchor-methods", "anchor methods") + " is cheaper swaps between the "
            "multiplier models, and that swap is the cost model's main sanity "
            "check.</figcaption>"
            + _legend([("var(--s1)", "m0plus_fast (1-cycle multiplier)"), ("var(--s2)", "m0plus_slow (32-cycle multiplier)")])
            + svg + "</figure>")


def _sweep_chart(name: str, method: dict) -> str:
    # A float64 error above 1 is larger than any value a Q15 state can hold. Plotting
    # those runs stretched the axis over twenty decades and pressed the Q15 curve, the
    # thing the chart is for, into a few pixels, so they stay off it and the caption
    # names the step sizes that were left out.
    def shown(r, key: str) -> bool:
        v = r.get(key)
        return _finite_pos(v) and not (key == "float_error" and v > 1.0)

    sweep = [r for r in method.get("sweep", []) if isinstance(r, dict) and _finite_pos(r.get("h"))]
    off = sorted(r["h"] for r in sweep if _finite_pos(r.get("float_error")) and r["float_error"] > 1.0)
    rows = [r for r in sweep if shown(r, "q15_error") or shown(r, "float_error")]
    if not rows:
        return ""
    hs = [r["h"] for r in rows]
    errs = ([r["q15_error"] for r in rows if shown(r, "q15_error")]
            + [r["float_error"] for r in rows if shown(r, "float_error")])
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
        pts = [(r["h"], r[key]) for r in rows if shown(r, key)]
        if len(pts) < 2:
            continue
        path = " ".join(f"{'M' if i == 0 else 'L'} {_fmt(pl.x(hv))} {_fmt(pl.y(ev))}"
                        for i, (hv, ev) in enumerate(pts))
        pl.parts.append(f'<path d="{path}" fill="none" stroke="{sw}" stroke-width="2"/>')
        for hv, ev in pts:
            pl.parts.append(f'<circle cx="{_fmt(pl.x(hv))}" cy="{_fmt(pl.y(ev))}" r="4" fill="{sw}" '
                            f'class="cellstroke"><title>{_esc(name)} {key} at h={_num(hv)}: {_num(ev)}</title></circle>')
    svg = pl.svg(f"{name}: Q15 and float64 error against step size, log-log")
    # The reading of the crossover is said once, above the pair, by the section.
    return (f"<figure><figcaption>{_esc(name)}: final-state error against step size h, "
            "log-log, Q15 and float64 over identical steps"
            + ("; the dashed line is the measured crossover." if _finite_pos(cross) else ".")
            + ("" if not off else
               " Float64 errors above 1, at h = " + _esc(", ".join(_num(h) for h in off))
               + ", are left off so the Q15 curve keeps its scale.")
            + "</figcaption>"
            f'{svg}<p class="note">Source: rk-work/falsification.json; Q15 and float64 as '
            "labeled.</p></figure>")


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
    # Seeded classical tableaus hold cells too. Counting them as coverage of the search
    # would credit the search with a method it was given, so the note says how many of
    # the occupied cells each process accounts for.
    seeded = sum(1 for r in elites if _is_seeded(r))
    coverage = "grid coverage, all orders"
    if seeded:
        coverage += (f"; {len(elites)} cells hold an elite, {len(elites) - seeded} "
                     f"found by the search and {seeded} seeded classical")
    if outside:
        coverage += (f", {outside} of them outside the searched stage range" if seeded
                     else f"; {outside} outside the searched stage range")
    # Three cards about the explicit class itself. The last cycle id and the hypothesis
    # counts were run telemetry and research-log numbers, and they left for those places.
    cards = [
        ("records", _count(arch.n_records), "verified tableaus archived"),
        ("elite cells", f"{occupied}/{cells}", coverage),
        ("heldout_verified", str(verified), "top tier at insertion"),
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
        # the raw kind is printed beside a label that does not already name it
        tag = "" if str(kind) in label else f' <span class="when">({_esc(kind)})</span>'
        rows.append(("last progress",
                     f"{_esc(_ct(d.get('last_progress_ts')))}: {_esc(label)}{tag}"))
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
    note = ('<p class="note">Progress is a record in an empty cell, an improved elite or a '
            "heldout_verified acceptance. With no progress inside the saturation window and "
            "the falsification file written, a check counts as saturating, and enough in a "
            "row reach the freeze threshold. Starting the next epoch, and re-pinning the "
            "verifier for it, is a person's decision. Times are stored in UTC and shown "
            "in US Central.</p>")
    return '<div class="panel">' + head + dl + note + "</div>"


# ----------------------------------------------------------------------------
# off-list documents: named on the page, never quoted from
# ----------------------------------------------------------------------------

# Some documents describe a method class in detail and still may not put a number on a
# public page. The two-axis document, the per-day lane records and the shares document
# each repeat the same sentence inside their own schema, saying the document is not on
# the traceability list. So a class page names such a document, says whether it exists
# yet, and publishes none of its numbers. The lane elites documents were admitted to the
# list and are published on the class pages instead; the per-day records beside them
# were not, and stay here.
_OFF_LIST_RULE = (
    "The traceability rule lists key_findings.json, validation/results.json, "
    "benchmark/results.json, the side-track ledger with its artifacts, and the two lane "
    "elites documents, adaptive_archive/elites.json and implicit_archive/elites.json. "
    "Each document named above sits outside that list and says so in its own schema, so "
    "this page names it and reports whether it exists without publishing a number from "
    "it. Only the owner can add one to the list.")


def _offlist_panel(rows) -> str:
    """One row per off-list document: its path, whether it is written, what it holds.

    Rendered inside the "Further evidence, not published here" fold at the foot of the
    implicit and adaptive pages, followed by the rule that keeps these documents off.
    """
    body = ['<div class="scroll"><table><tr><th>document</th><th>state</th>'
            "<th>what it holds</th></tr>"]
    for rel, present, holds in rows:
        body.append(f'<tr><td class="mono">{_esc(rel)}</td>'
                    f"<td>{'written' if present else 'not written yet'}</td>"
                    f"<td>{_esc(holds)}</td></tr>")
    body.append("</table></div>")
    return "\n".join(body) + f'<p class="note">{_esc(_OFF_LIST_RULE)}</p>'


def _further_fold(rows) -> str:
    """The off-list documents, folded at the foot of a class page."""
    return _fold("Further evidence, not published here", _offlist_panel(rows))


# ----------------------------------------------------------------------------
# pages
# ----------------------------------------------------------------------------

_CLASS_BOUNDARY = (
    "A scored record has room for one explicit tableau and nothing else, so the implicit "
    "and adaptive classes are measured outside the archive and never ranked against it.")

# The arithmetic behind each class and what checks a result, one line per hub card.
_CLASS_GLANCE = {
    "explicit": "Q15 fixed point, checked by the pinned verifier.",
    "implicit": "float64; not order-verified and not scored.",
    "adaptive": "float64, with one fixed pair also run in Q15; not order-verified and not "
                "scored.",
}


def _class_facts() -> tuple[tuple[str, str, tuple[str, str, str]], ...]:
    """What each class is, in words: (hub row label, class-page label, cells).

    One source for the hub's side-by-side table and for the "At a glance" list on each
    class page, so the two cannot describe a class differently. Cells follow
    CLASS_ORDER and are trusted HTML fragments.
    """
    sdirk = _gloss("sdirk", "SDIRK")
    pair = _gloss("embedded-pair", "embedded pair")
    lane_q = ("How many modeled cycles does a method need to reach an accuracy target? ("
              + _gloss("cycles-to-tolerance", "cycles to tolerance") + ")")
    return (
        ("method family", "family", (
            "Explicit fixed-step Runge-Kutta: a strictly lower triangular A and one b "
            "vector",
            "Two-stage " + sdirk + " over a dyadic grid of gamma and a21, with a Newton "
            "iteration count and a Jacobian policy",
            "An " + pair + " on the dyadic lattice (order 3 with an order-2 estimate) and "
            "a step-size controller")),
        ("how candidates are found", "search", (
            _gloss("map-elites", "MAP-Elites") + " cells filled by exhaustive enumeration "
            "and CMA-ES islands",
            "Deterministic enumeration, plus the side-track jobs",
            "Deterministic enumeration, plus the side-track jobs")),
        ("the question asked", "question", (
            "What error does a method reach inside a fixed "
            + _gloss("cycle-budget", "cycle budget") + "?",
            lane_q,
            lane_q)),
        ("problems", "problems", (
            "The frozen search set and " + _gloss("held-out-set", "held-out set"),
            "The validation problems",
            "The validation problems")),
        ("arithmetic", "arithmetic", (
            _gloss("q15", "Q15") + " through the pinned solver",
            "float64: there is no Q15 LU factorization",
            "float64; the Q15 adaptive solver runs one fixed pair")),
        ("what checks a result", "checked by", (
            "The pinned verifier, order conditions included, then scoring on the held-out "
            "set",
            "Nothing: not order-verified and not scored",
            "Nothing: not order-verified and not scored")),
        ("cost basis", "cost basis", (
            'The pinned <a href="methodology.html#costmodel">cost model</a>',
            "A design estimate that assumes a division cost",
            "Float64 attempt counts, each attempt priced by the Q15 cost model")),
        ("source documents", "sources", (
            "rk-work/archive, validation/results.json, benchmark/results.json",
            "implicit_archive/elites.json, the side-track ledger, validation/results.json, "
            "benchmark/results.json",
            "adaptive_archive/elites.json, the side-track ledger, benchmark/results.json")),
    )


def _class_side_table() -> str:
    """The hub's comparison of the three classes, in words.

    Words and not numbers on purpose: the numbers each class reports answer different
    questions over different problems in different arithmetic, which is exactly what
    this table exists to say, and a number in one of its cells would invite the
    comparison across columns that it warns against.
    """
    head = ("<tr><th></th>" + "".join(
        f'<th><a href="{_CLASS_PAGE[c]}">{_esc(c)}</a></th>' for c in CLASS_ORDER) + "</tr>")
    body = "".join(
        f'<tr><th scope="row">{_esc(label)}</th>'
        + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"
        for label, _short, cells in _class_facts())
    return f'<div class="scroll"><table class="side">{head}{body}</table></div>'


def _glance(cls: str) -> str:
    """A class page's "At a glance" list: the hub table's column for this class."""
    i = CLASS_ORDER.index(cls)
    items = "\n".join(f"<dt>{_esc(short)}</dt><dd>{cells[i]}</dd>"
                      for _label, short, cells in _class_facts())
    return f'<h2>At a glance</h2>\n<dl class="meta glance">\n{items}\n</dl>'


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


def _arith_label(v) -> str:
    """An arithmetic name as the prose writes it: the data says q15, a sentence says Q15."""
    s = str(v)
    return "Q15" if s == "q15" else s


def _matched_class_card(cls: str, benchmark, blurb: str):
    """One hub card from this class's own matched-accuracy rows, or None without them.

    Every class answers the same question here, whether its own runs reached a fixed
    error target, and each card names the arithmetic its runs used. None of the three
    numbers ranks one class against another: the runs differ in method, arithmetic and
    solver count, which is why the card states its own denominator.
    """
    rows = [r for r in _matched_rows(benchmark, cls) if str(r.get("side")) == "ours"]
    # Rows that never ran are not targets this class missed, so they stay out of the
    # denominator and are counted beside it instead.
    ran = [r for r in rows if str(r.get("status")) != "skipped"]
    if not ran:
        return None
    hit = sum(1 for r in ran if str(r.get("status")) == "reached")
    ariths = sorted({_arith_label(r.get("arithmetic")) for r in rows
                     if isinstance(r.get("arithmetic"), str) and r.get("arithmetic")})
    what = "matched-accuracy targets our solvers reached"
    if len(rows) > len(ran):
        n = len(rows) - len(ran)
        what += f"; {n} further {'row' if n == 1 else 'rows'} never ran"
    if ariths:
        what += "; " + " and ".join(ariths) + " runs"
    return (cls, f"{hit} of {len(ran)}", what, "benchmark/results.json", blurb)


def render_index(arch: ArchiveState, benchmark: dict | None = None,
                 validation: dict | None = None, sidetrack: dict | None = None) -> str:
    """The hub: what the run is, the epoch state, and the three classes side by side.

    The archive itself (grids, scatter, elite table) lives on explicit.html. The why and
    the how live on the overview site, which the lead and the header both link.
    """
    parts = [
        '<p class="lead">An automated search for Runge-Kutta methods that hold up in '
        + _gloss("q15", "Q15") + " fixed-point arithmetic on small microcontrollers. This "
        "site is the run's live record: it is generated from the run's data at the end of "
        "every cycle, and nobody edits it by hand. "
        f'The <a href="{OVERVIEW_URL}">project overview</a> explains why the project '
        "exists and how it works.</p>"
    ]
    parts.append("<h2>The three classes</h2>")
    parts.append(f'<p class="note">{_esc(_CLASS_BOUNDARY)}</p>')
    # Each card carries its own class's number. With a benchmark document that is the
    # share of the class's own runs that reached a matched-accuracy target; without one,
    # the explicit card counts archive records and the other two count ledger points.
    exp_blurb = "Scored, archived and ranked at an equal cycle budget."
    card = _matched_class_card("explicit", benchmark, exp_blurb)
    if card is not None:
        rows = [card]
    elif arch.n_records:
        rows = [("explicit", _count(arch.n_records), "archive records", "the run archive",
                 exp_blurb)]
    else:
        rows = [("explicit", "not started", "the run archive holds no records yet", None,
                 exp_blurb)]
    # The stiff gap is a fact about explicit tableaus in Q15, so it explains why this
    # class is measured and never stands in as the implicit class's own number.
    gap = _stiff_gap(validation)
    if gap is not None:
        imp_blurb = (f"Measured because the explicit class runs out of stability before it "
                     f"runs out of budget: on {gap[0]} of {gap[1]} stiff validation problems "
                     "every discovered explicit tableau overflows Q15 "
                     "(validation/results.json).")
    else:
        imp_blurb = ("Measured because the explicit class runs out of stability before it "
                     "runs out of budget.")
    adp_blurb = "A record has no room for the second b vector an error estimate needs."
    for cls, blurb in (("implicit", imp_blurb), ("adaptive", adp_blurb)):
        card = _matched_class_card(cls, benchmark, blurb)
        rows.append(card if card is not None else _ledger_class_card(cls, sidetrack, blurb))
    rows = [tuple(r) + (_CLASS_GLANCE[r[0]],) for r in rows]
    parts.append(_class_cards(rows))
    parts.append("<h2>The three classes side by side</h2>")
    parts.append(_class_side_table())
    parts.append('<p class="note">Numbers from different columns do not compare, so each '
                 "class tab carries its own.</p>")
    speed = _speed_sentence(benchmark)
    if speed:
        parts.append(f"<p>{speed}</p>")
    # The run's own state closes the page: it is about the scored search, not about
    # any one class, and a reader arriving here wants the classes first.
    parts.append("<h2>Epoch status</h2>")
    parts.append(_epoch_panel())
    return _page("rk-harness findings", "\n".join(parts), active="index.html",
                 subtitle="Explicit methods scored in Q15 fixed point, and implicit and "
                          "adaptive methods measured outside the archive.")


_EXPLICIT_CLASS = (
    "Explicit Runge-Kutta methods build each stage from stages already computed, so a "
    "step is a fixed run of multiply-adds. For each order, stage count and cost, the "
    "grid keeps the verified tableau with the lowest held-out error, and that error, "
    "measured in Q15 at a fixed cycle budget, is the number each elite reports.")


def _elite_table_fold(arch: ArchiveState) -> str:
    """Every occupied cell in one table, or "" when no grid holds an elite.

    It expands the elite grids rather than the library comparison, so render_explicit
    folds it under the grids instead of at the foot of the page.
    """
    rows: list[str] = []
    vhashes: dict[str, int] = {}
    seeded = 0
    for order in sorted(arch.grids.keys()):
        for (stg, bucket) in sorted(arch.grids[order].keys()):
            rec = arch.grids[order][(stg, bucket)]
            sv = rec.score
            vhashes[rec.verifier_hash] = vhashes.get(rec.verifier_hash, 0) + 1
            if _is_seeded(rec):
                seeded += 1
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
        return ""
    vlist = ", ".join(f'<span class="hash">{_esc(h)}</span> ({n} '
                      + ("row" if n == 1 else "rows") + ")"
                      for h, n in sorted(vhashes.items(), key=lambda kv: (-kv[1], kv[0])))
    seed_note = ""
    if seeded:
        seed_note = (f" {seeded} of these rows "
                     + ("is a seeded classical baseline rather than a search result"
                        if seeded == 1 else
                        "are seeded classical baselines rather than search results")
                     + "; the label column says which.")
    body = ('<p class="note">One row per occupied cell, every order together so cells '
            "can be compared across orders. The hash links to the full record. A large "
            "gap between search_error and heldout_error is overfitting to the visible "
            "search set; " + _gloss("order", "measured_order") + " and the "
            + _gloss("tiers", "tier") + " badge are defined in the glossary."
            + seed_note + "</p>"
            '<div class="scroll"><table>\n'
            '<tr><th>order</th><th>stages</th><th>bucket</th><th>tableau_hash</th>'
            '<th>tier</th><th class="num">heldout_error</th><th class="num">search_error</th>'
            '<th class="num">cycles fast</th><th class="num">cycles slow</th>'
            '<th class="num">measured_order</th><th>label</th></tr>'
            + "".join(rows) + "</table></div>"
            + '<p class="note">Scored under '
            + ("one " if len(vhashes) == 1 else f"{len(vhashes)} ")
            + _gloss("verifier-hash", "verifier hash")
            + ("" if len(vhashes) == 1 else "es") + ": " + vlist
            + ". Each record's own hash is on its detail page.</p>")
    return _fold(f"Every elite in one table ({len(rows)} "
                 + ("row" if len(rows) == 1 else "rows") + ")", body)


def render_explicit(arch: ArchiveState, validation: dict | None = None,
                    benchmark: dict | None = None) -> str:
    """The explicit class: the archive's elites, its grids and the matched comparison.

    The same skeleton as the other two class pages. The cell pages keep their filenames,
    so every link into a cell still resolves; the scatter, the heatmaps and the folded
    table at the foot all link to them.
    """
    parts = [f'<p class="lead">{_esc(_EXPLICIT_CLASS)}</p>', _glance("explicit"),
             _stat_cards(arch)]
    parts.append("<h2>Cost against held-out error</h2>")
    parts.append(_chart_block(_elite_scatter(arch)))
    parts.append("<h2>Elite grids</h2>")
    grids = [f'<div class="panel">{_grid_heatmap(order, arch.grids[order])}</div>'
             for order in sorted(arch.grids.keys()) if arch.grids[order]]
    if grids:
        # One figure for the four orders: one caption, one source line. The caption
        # names the color by its distance from the page background, because the dark
        # theme runs the ramp the other way and "deeper blue" is only true in light.
        parts.append("<figure><figcaption>One grid per order. Rows are stage counts, "
                     "columns are m0plus_fast cycle buckets, and each filled cell prints "
                     "its elite's held-out error. The further a cell's color is from the "
                     "page background, the lower the error within that grid. Click a cell "
                     "for the record.</figcaption>"
                     '<div class="charts grid2">' + "\n".join(grids) + "</div>"
                     + _ARCHIVE_SOURCE + "</figure>")
        parts.append(_explain(
            "The archive is a " + _gloss("map-elites", "MAP-Elites") + " structure: one "
            "grid per algebraic " + _gloss("order", "order") + " 1 to 4, whose cells are "
            "keyed by (stage count, cycle bucket). A cell holds at most one record, its "
            + _gloss("elite", "elite") + ": the verified tableau with the lowest held-out "
            "error seen so far for that shape and cost; ties keep the earlier record. An "
            "empty cell means no verified tableau has landed there yet.",
            _gloss("stage", "Stage") + " rows span 2 to 6 by default, plus a row for any "
            "other stage count that holds an elite (order 1's single-stage cell appears "
            "as s=1). The " + _gloss("cost-bucket", "cycle buckets") + " are log2-spaced "
            "bins of the m0plus_fast cycles per step: bucket 0 means fewer than 16 "
            "cycles, bucket 1 is 16 to 31, bucket 2 is 32 to 63, doubling each column, "
            "and bucket 7 collects everything at 1024 cycles or more."))
    else:
        parts.append('<p class="note">No grid holds an elite yet.</p>')
    table = _elite_table_fold(arch)
    if table:
        parts.append(table)
    parts.extend(_practical_section(validation, benchmark))
    parts.extend(_matched_section(benchmark, "explicit"))
    return _page("Explicit methods", "\n".join(parts), active="explicit.html",
                 subtitle="Explicit Runge-Kutta tableaus scored end-to-end in Q15 at a "
                          "fixed cycle budget.")


def _practical_chart(validation) -> str:
    """Best discovered against best classical Q15 error on each non-stiff practical problem.

    A dumbbell per problem on one log axis: the blue dot is the best archive tableau,
    the orange diamond the best classical anchor, and the grey bar between them is the
    gap. Stiff problems are left to the validation page, where finishing at all is the
    question, and a problem with only one side finished shows the one mark it has.
    """
    if not isinstance(validation, dict):
        return _absent("rk-work/validation/results.json has not been written, so there "
                       "is nothing to plot.")
    verdicts = validation.get("verdicts") if isinstance(validation.get("verdicts"), dict) else {}
    per = verdicts.get("per_problem") if isinstance(verdicts.get("per_problem"), dict) else {}
    probs = [p for p in (validation.get("problems") or []) if isinstance(p, dict)]
    names = [str(p.get("name")) for p in probs if not p.get("stiff")]
    names += sorted(k for k in per if k not in set(names)
                    and k not in {str(p.get("name")) for p in probs if p.get("stiff")})
    rows = []
    for name in names:
        d = per.get(name)
        if not isinstance(d, dict):
            continue
        disc, disc_err = d.get("best_discovered"), d.get("best_discovered_q15_error")
        cls, cls_err = d.get("best_classical"), d.get("best_classical_q15_error")
        if cls is None and d.get("winner_kind") == "classical":
            cls, cls_err = d.get("winner"), d.get("winner_q15_error")
        marks = []
        if disc is not None and _finite_pos(disc_err):
            marks.append(("disc", _vlabel(str(disc), "discovered"), float(disc_err)))
        if cls is not None and _finite_pos(cls_err):
            marks.append(("cls", str(cls), float(cls_err)))
        if marks:
            rows.append((name, marks))
    if not rows:
        return _absent("The validation document has no non-stiff problem with a best "
                       "discovered or best classical error, so there is nothing to plot.")
    vals = [v for _n, marks in rows for _k, _l, v in marks]
    xs = _log_scale(min(vals), max(vals))

    def draw(w: int, tips: bool = True) -> str:
        ml, mr, mt, row_h = 128, 18, 8, 28
        span = w - ml - mr
        base = mt + row_h * len(rows)
        h = base + 30
        body: list[str] = []
        for i, (name, marks) in enumerate(rows):
            cy = mt + row_h * i + row_h / 2
            body.append(f'<text x="{ml - 10}" y="{_fmt(cy + 3.5)}" text-anchor="end">'
                        f"{_soft(_clip(name, (ml - 14) / 7.0))}</text>")
            px = {k: ml + xs.frac(v) * span for k, _l, v in marks}
            if len(px) == 2:
                lo, hi = sorted(px.values())
                body.append(f'<line x1="{_fmt(lo)}" y1="{_fmt(cy)}" x2="{_fmt(hi)}" '
                            f'y2="{_fmt(cy)}" stroke="var(--line)" stroke-width="4" '
                            'stroke-linecap="round"/>')
            for k, label, v in marks:
                who = "best discovered" if k == "disc" else "best classical"
                body.append(_mark("circle" if k == "disc" else "diamond", px[k], cy,
                                  "var(--s1)" if k == "disc" else "var(--s2)",
                                  f"{name}: {who}, {label}, Q15 error {_num(v)}"
                                  if tips else ""))
        return (f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
                'aria-label="Best discovered against best classical Q15 error on each '
                'non-stiff practical problem, log scale">'
                + "".join(_hbar_frame(xs, ml, span, mt, base)) + "".join(body) + "</svg>")

    svg = _phone_pair(draw(640), draw(430, tips=False))
    model = validation.get("cost_model")
    return ("<figure><figcaption>Final-state Q15 error of the best discovered method and "
            "the best classical anchor on each non-stiff practical problem, log axis, so "
            "left is better. Every method gets the same cycle budget.</figcaption>"
            + _key_legend([("var(--s1)", "circle", "best discovered (archive)"),
                           ("var(--s2)", "diamond", "best classical anchor")])
            + svg + '<p class="note">Source: rk-work/validation/results.json, '
            "verdicts.per_problem. Arithmetic: Q15 with floor rounding"
            + (f" under {_soft(model)}" if isinstance(model, str) and model else "")
            + ".</p></figure>")


def _practical_section(validation, benchmark) -> list[str]:
    """The explicit class away from its search problems: the validation suite's practical
    problems, one count and one chart, and the measured speed sentence."""
    parts = ["<h2>Beyond the search problems</h2>"]
    if isinstance(validation, dict):
        v = validation.get("verdicts") if isinstance(validation.get("verdicts"), dict) else {}
        won = v.get("practical_problems_won_by_discovered", v.get("problems_won_by_discovered"))
        compared = v.get("practical_problems_compared", v.get("problems_compared"))
        if isinstance(won, int) and isinstance(compared, int) and compared > 0:
            parts.append(
                f"<p>On {won} of {compared} practical problems that no search saw, the "
                "best discovered tableau ends with lower Q15 error than the best classical "
                'anchor at the same cycle budget. The <a href="validation.html">validation '
                "tab</a> has every method on every problem, the stiff ones included.</p>")
        parts.append(_chart_block(_practical_chart(validation)))
    else:
        parts.append('<p class="note">rk-work/validation/results.json has not been written '
                     "in this work directory, so the practical problems are not reported "
                     "here.</p>")
    speed = _speed_sentence(benchmark)
    if speed:
        parts.append(f"<p>{speed}</p>")
    return parts


def render_cell(order: int, stages: int, bucket: int, rec: Record) -> str:
    sv = rec.score
    parts = []
    parts.append(
        '<p class="lead">This page is the full archive record for this cell\'s '
        + _gloss("elite", "elite") + ". Every number here was produced by the pinned "
        "scoring code, and " + _gloss("verifier-hash", "verifier_hash") + " pins which "
        'version of it; see the <a href="explicit.html">explicit archive</a> for where '
        "this cell sits in the grids. The "
        + _gloss("tiers", "tier") + " and phase label are assigned mechanically.</p>")
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
    title = f"Cell p{order} s{stages} b{bucket}"
    return _page(title, "\n".join(parts), active="explicit.html",
                 subtitle=f"grid order {order}, {stages} stages, cycle bucket {bucket}")


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
    return "\n".join(out)


def _verdict_badge(verdict) -> str:
    v = verdict if verdict is not None else "open"
    return f'<span class="badge badge-{_esc(v)}">{_esc(v)}</span>'


def _hyp_cycle(h: dict) -> str:
    cyc = h.get("resolved_cycle")
    if cyc is not None:
        return f"c{_num(cyc)}"
    return f"c{_num(h.get('cycle_proposed'))}+"


def _hyp_num(hid) -> tuple[int, str]:
    """Order hypothesis ids by their number, so H-1000 sorts after H-999."""
    s = str(hid)
    m = re.search(r"(\d+)$", s)
    return (int(m.group(1)) if m else -1, s)


def _hyp_row(group: list[dict]) -> str:
    """One ledger row per distinct predicate.

    The planning model re-proposes a predicate it has already tested, sometimes dozens of
    times with an identical verdict and an identical effect size. The row counts the
    repeats and lists their ids; the posings themselves stay in rk-work/hypotheses.jsonl."""
    latest = group[-1]
    pred = str(latest.get("predicate", ""))
    verdicts = {str(h.get("verdict") or "open") for h in group}
    badge = (_verdict_badge(latest.get("verdict")) if len(verdicts) == 1
             else _verdict_badge(latest.get("verdict")) + '<span class="when">&nbsp;mixed</span>')
    rep = f' <span class="rep">&times;{len(group)}</span>' if len(group) > 1 else ""
    summary = (f'<span class="mono">{_esc(group[0].get("id", ""))}{rep}</span>'
               f"{badge}"
               f'<span class="pred">{_esc(pred)}</span>'
               f'<span class="num">{_num(latest.get("effect_size"))}</span>'
               f'<span class="num">{_num(latest.get("n_samples"))}</span>'
               f'<span class="when">{_hyp_cycle(latest)}</span>')

    first = group[0]
    body = ['<dl class="meta">'
            f'<dt>statement</dt><dd>{_esc(first.get("statement", ""))}</dd>'
            f'<dt>mechanism</dt><dd>{_esc(first.get("mechanism", ""))}</dd>'
            f'<dt>control</dt><dd>{_esc(first.get("control", ""))}</dd>'
            f'<dt>min_samples</dt>'
            f'<dd>{_num(first.get("min_samples"))}</dd>'
            + (f'<dt>posed as</dt><dd class="mono">'
               f'{_esc(", ".join(str(h.get("id", "")) for h in group))}</dd>'
               if len(group) > 1 else "")
            + "</dl>"]
    return f'<details class="led"><summary>{summary}</summary><div>{"".join(body)}</div></details>'


_HYP_ORDER = ("supported", "refuted", "inconclusive", "open")
# Predicates shown per verdict group, newest first. The rest are one line away in the
# ledger file, as the interpretation and literature logs below already do it. Twenty
# keeps the page under 150 KB at the run's current size (about 940 bytes a row).
_HYP_SHOWN = 20
_HYP_GLOSS = {
    "supported": "the predicate held once every cell it names had enough records",
    "refuted": "the predicate failed on the data it names",
    "inconclusive": "effect size under 0.2, or a named cell with no records",
    "open": "waiting for min_samples in at least one named cell",
}


def _verdict_chart(counts, totals: tuple[int, int, int] | None = None) -> str:
    """Distinct predicates per verdict group, one horizontal bar each.

    totals, when given, is (hypotheses, distinct predicates, predicates posed more than
    once), and the caption states it, since the bars count predicates, not posings.

    One series, so one color and no legend: the verdict names label the rows and the
    counts are printed at the bar ends. The status colors stay on the badges, because
    the rows are already named in text and coloring bars by status would only add a
    second key. A group with no predicate is left out, as the folds below leave it out,
    and with no hypotheses at all the chart becomes a one-line note.
    """
    rows = [(k, int(counts.get(k, 0))) for k in _HYP_ORDER
            if isinstance(counts.get(k), int) and counts.get(k, 0) > 0]
    if not rows:
        return ('<p class="note">No hypotheses are recorded yet, so there is no verdict '
                "count to chart.</p>")
    vmax = max(v for _k, v in rows)
    step = _nice_step(max(vmax, 1) / 4.0)
    if step < 1:
        step = 1.0
    elif step != int(step):
        step = _nice_step(step * 1.01)
    top = step * math.ceil(vmax / step)
    w, ml, mr, mt, row_h = 480, 100, 16, 6, 28
    base_y = mt + row_h * len(rows)
    h = base_y + 30
    span = w - ml - mr

    def fx(v: float) -> float:
        return ml + v / top * span

    parts = []
    tv = 0.0
    while tv <= top * 1.0001:
        px = fx(tv)
        parts.append(f'<line class="gridline" x1="{_fmt(px)}" y1="{mt}" x2="{_fmt(px)}" '
                     f'y2="{base_y}"/>')
        parts.append(f'<text x="{_fmt(px)}" y="{base_y + 16}" text-anchor="middle">'
                     f"{tv:g}</text>")
        tv += step
    parts.append(f'<line class="axis" x1="{ml}" y1="{mt}" x2="{ml}" y2="{base_y}"/>')
    for i, (key, v) in enumerate(rows):
        y = mt + row_h * i + 5
        bw = max(fx(v) - ml, 2.0)
        parts.append(f'<text x="{ml - 8}" y="{_fmt(y + 13)}" text-anchor="end">'
                     f"{_esc(key)}</text>")
        parts.append(f'<rect x="{ml}" y="{_fmt(y)}" width="{_fmt(bw)}" height="18" rx="4" '
                     f'fill="var(--s1)" class="cellstroke"><title>{_esc(key)}: {v} distinct '
                     f"{'predicate' if v == 1 else 'predicates'}</title></rect>")
        label = str(v)
        end = ml + bw
        if end + 6 + len(label) * 7.0 > w - 2:
            parts.append(f'<text class="lbl" x="{_fmt(end - 6)}" y="{_fmt(y + 13)}" '
                         f'text-anchor="end">{label}</text>')
        else:
            parts.append(f'<text class="lbl" x="{_fmt(end + 6)}" y="{_fmt(y + 13)}">'
                         f"{label}</text>")
    svg = (f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
           'aria-label="Distinct predicates in each verdict group, horizontal bars">'
           + "".join(parts) + "</svg>")
    tally = ""
    if totals is not None:
        n_h, n_p, rep = totals
        tally = (f" {n_h} {'hypothesis' if n_h == 1 else 'hypotheses'} over {n_p} distinct "
                 f"{'predicate' if n_p == 1 else 'predicates'}; {rep} "
                 f"{'was' if rep == 1 else 'were'} posed more than once.")
    return ("<figure><figcaption>Distinct predicates in each verdict group, with the count "
            "at the end of each bar." + tally + "</figcaption>" + svg
            + '<p class="note">Source: rk-work/hypotheses.jsonl; exact counts.</p></figure>')


_INTERP_SHOWN = 5
_LIT_SHOWN = 10


def _interpretation_tops(entries: list[dict]) -> list[tuple[dict, list[dict]]]:
    """One top-level entry per cycle, newest cycle first.

    The newest draft of a cycle (latest ts, ties broken by stored position) speaks for
    it, and older same-cycle drafts fold inside it.
    """
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
    return tops


def _older_note(n: int, one: str, many: str, path: str) -> str:
    """The one line that says how much of a model-written log the page leaves out."""
    if n <= 0:
        return ""
    return (f'<p class="note">{n} older {one if n == 1 else many} '
            f"{'is' if n == 1 else 'are'} not shown here. The whole log is in "
            f"{_esc(path)}.</p>")


def _interpretation_section(entries: list[dict]) -> list[str]:
    parts = ['<h2 id="interpretation">Interpretation</h2>',
             f'<p class="note">{_esc(_MODEL_NOTE)}</p>',
             "<p>Readings of the archive the model wrote during the run, newest at the top. "
             "Each one comments on the numbers as they stood at its cycle, so where the "
             "text and the tables disagree, trust the tables. Unlike "
             + _gloss("hypothesis-ledger", "hypotheses") + " these readings carry no "
             "predicate, so code cannot catch them being wrong.</p>"]
    tops = _interpretation_tops(entries)
    if not tops:
        parts.append("<p>no interpretation yet</p>")
        return parts
    for i, (e, drafts) in enumerate(tops[:_INTERP_SHOWN]):
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
    note = _older_note(len(tops) - _INTERP_SHOWN, "entry", "entries",
                       "rk-work/interpretation/interpretations.jsonl")
    if note:
        parts.append(note)
    return parts


def _literature_section(digests: list[dict]) -> list[str]:
    parts = ['<h2 id="literature">Literature</h2>',
             "<p>Digests of published work the model looked up on the web during the run, "
             "newest at the top. They feed the " + _gloss("directive", "directive") + " and "
             "hypothesis prompts. Nothing here is a measurement from this project, and no "
             "measured page depends on it.</p>"]
    if not digests:
        parts.append("<p>no literature digests yet</p>")
        return parts
    newest = list(reversed(digests))
    for d in newest[:_LIT_SHOWN]:
        entry = ['<details class="fold entry">']
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
            items = "".join(
                f'<li><a href="{_esc(x.get("url", ""))}">'
                f'{_esc(x.get("title", "") or x.get("url", ""))}</a></li>'
                for x in srcs if isinstance(x, dict))
            entry.append(f"<p>sources:</p><ul>{items}</ul>")
        entry.append("</div></details>")
        parts.append("\n".join(entry))
    note = _older_note(len(newest) - _LIT_SHOWN, "digest", "digests",
                       "rk-work/literature/digests.jsonl")
    if note:
        parts.append(note)
    return parts


def render_hypotheses(hyps: list[dict], digests: list[dict] | None = None,
                      interpretations: list[dict] | None = None) -> str:
    """The research log: the hypothesis ledger, then the model's readings and digests.

    The ledger rows are folded by verdict. The two model-written logs show their newest
    entries only and say where the rest are, so the page stays readable as they grow.
    """
    parts = [
        '<p class="lead">The ' + _gloss("hypothesis-ledger", "hypothesis ledger")
        + ": predicates the planning model committed to before the data could answer them, "
        "each resolved by code. Open a row for the statement, the proposed mechanism and "
        "the control that would have shown the mechanism wrong. Below the ledger are the "
        'model\'s <a href="#interpretation">readings of the archive</a> and its '
        '<a href="#literature">literature digests</a>.</p>'
    ]
    parts.append(_explain(
        "Each row pairs a machine-checkable predicate over per-cell statistics, for example "
        '<span class="mono">slow.p3s4.heldout &lt; slow.p4s4.heldout</span>, with the mechanism '
        "and control the model recorded before the data could answer. p and s name an "
        "(order, stages) cell, and the leading word picks the cost model.",
        "Verdicts come from code, never from the model. Once every cell a predicate names "
        "holds min_samples records, the predicate is evaluated against those cells' running "
        "statistics. d is " + _gloss("cohens-d", "Cohen's d") + "; below 0.2 the verdict is "
        "inconclusive whichever way the comparison went, so a weak effect cannot be claimed. "
        "A predicate that names an empty cell is inconclusive too, because absence of "
        "evidence never refutes. n is the smallest record count among the named cells, and "
        "c is the cycle the verdict landed on (c123+ means proposed at cycle 123 and still "
        "open).",
        "&times;N means the model posed that predicate N times over the run. The row "
        "shows the newest verdict and lists the id of every posing; mixed beside the "
        "verdict means those posings did not all get the same one."))
    if not hyps:
        parts.append("<p>no hypotheses recorded</p>")
        parts.append(_verdict_chart({}))
    else:
        by_pred: dict[str, list[dict]] = {}
        for h in sorted(hyps, key=lambda d: _hyp_num(d.get("id", ""))):
            by_pred.setdefault(str(h.get("predicate", "")), []).append(h)
        groups: dict[str, list[list[dict]]] = {k: [] for k in _HYP_ORDER}
        # newest first: a group is as new as its latest posing
        for group in sorted(by_pred.values(),
                            key=lambda g: max(_hyp_num(h.get("id", "")) for h in g),
                            reverse=True):
            key = str(group[-1].get("verdict") or "open")
            groups.setdefault(key, []).append(group)
        repeats = sum(1 for g in by_pred.values() if len(g) > 1)
        parts.append('<div class="panel">'
                     + _verdict_chart({k: len(groups.get(k, [])) for k in _HYP_ORDER},
                                      (len(hyps), len(by_pred), repeats))
                     + "</div>")
        head = ('<div class="ledhead"><span></span><span>id</span><span>verdict</span>'
                '<span>predicate</span><span class="num">d</span><span class="num">n</span>'
                "<span>cycle</span></div>")
        parts.append("<h2>Every predicate, by verdict</h2>")
        for key in _HYP_ORDER:
            rows = groups.get(key, [])
            if not rows:
                continue
            gloss = _HYP_GLOSS.get(key, "")
            parts.append(f'<details class="fold"><summary>{_esc(key)} ({len(rows)} '
                         f"{'predicate' if len(rows) == 1 else 'predicates'})"
                         + (f' <span class="when">{_esc(gloss)}</span>' if gloss else "")
                         + "</summary><div>"
                         '<div class="scroll"><div class="ledger">' + head
                         + "".join(_hyp_row(g) for g in rows[:_HYP_SHOWN])
                         + "</div></div>"
                         + _older_note(len(rows) - _HYP_SHOWN, "predicate", "predicates",
                                       "rk-work/hypotheses.jsonl")
                         + "</div></details>")
    parts.extend(_interpretation_section(interpretations or []))
    parts.extend(_literature_section(digests or []))
    return _page("Research log", "\n".join(parts), active="hypotheses.html",
                 subtitle="Predicates resolved by code, and the model's own readings and "
                          "reading list.")


def _costmodel_section() -> str:
    """The cost model, for the methodology page: anchor chart, tables, parameters."""
    classical = tableau_mod.classical()
    parts = [
        "<p>Cycles per integration step, computed from instruction counts. The "
        + _gloss("cycle-budget", "cycle budget") + " and "
        "the " + _gloss("cost-bucket", "cycle buckets") + " on the other tabs are built "
        "from these numbers.</p>",
        "<h3>Anchor: rk4 and rk38</h3>",
        '<div class="panel">' + _anchor_bars() + "</div>",
        '<table><tr><th>tableau</th><th class="num">n_states</th>'
        '<th class="num">m0plus_fast</th><th class="num">m0plus_slow</th></tr>',
    ]
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
    parts.append("<h3>Classical tableaus (n_states = 1)</h3>")
    parts.append('<div class="scroll"><table><tr><th>tableau</th><th class="num">stages</th>'
                 '<th class="num">m0plus_fast</th><th class="num">m0plus_slow</th>'
                 '<th class="num">avr_approx</th></tr>')
    for name in sorted(classical.keys()):
        t = classical[name]
        fast = costmodel.cycle_count(t, costmodel.M0PLUS_FAST, 1)
        slow = costmodel.cycle_count(t, costmodel.M0PLUS_SLOW, 1)
        avr = costmodel.cycle_count(t, costmodel.AVR_APPROX, 1)
        parts.append(f'<tr><td>{name}</td><td class="num">{len(t.b)}</td>'
                     f'<td class="num">{fast}</td><td class="num">{slow}</td>'
                     f'<td class="num">{avr}</td></tr>')
    parts.append("</table></div>")
    parts.append(f'<p class="note">{_esc(AVR_NOTE)} It never drives the search or the '
                 "archive grids. A tableau of simple " + _gloss("dyadic-rational", "dyadic")
                 + " values can cost much less than its stage count suggests.</p>")
    parts.append("<h3>Model parameters</h3>")
    parts.append('<table><tr><th>model</th><th class="num">mul</th><th class="num">add</th>'
                 '<th class="num">shift</th><th class="num">load</th>'
                 '<th class="num">store</th></tr>')
    for m in (costmodel.M0PLUS_FAST, costmodel.M0PLUS_SLOW, costmodel.AVR_APPROX):
        cy = m.cycles
        parts.append(f'<tr><td>{m.name}</td><td class="num">{cy.get("mul")}</td>'
                     f'<td class="num">{cy.get("add")}</td>'
                     f'<td class="num">{cy.get("shift")}</td>'
                     f'<td class="num">{cy.get("load")}</td>'
                     f'<td class="num">{cy.get("store")}</td></tr>')
    parts.append("</table>")
    parts.append('<p class="note">m0plus_fast and m0plus_slow differ only in the multiplier. '
                 "Every archive cycle count is these five numbers applied to a tableau's "
                 "instruction sequence, never a hardware measurement. The off-archive lanes "
                 "add their own stated terms, named on the implicit and adaptive tabs.</p>")
    return "\n".join(parts)


def _falsification_section(data) -> list[str]:
    """The premise test that ran before any search, for the validation page."""
    parts = ['<h2 id="falsification">Where roundoff overtakes truncation</h2>']
    if not isinstance(data, dict):
        parts.append('<p class="note">rk-work/falsification.json has not been written in '
                     "this work directory, so the falsification experiment is not reported "
                     "here.</p>")
        return parts
    methods = data.get("methods") if isinstance(data.get("methods"), dict) else {}
    names = sorted(k for k, v in methods.items() if isinstance(v, dict))
    who = ("two fixed classical methods, " + " and ".join(_esc(n) for n in names)
           if len(names) == 2 else "fixed classical methods")
    intro = ("<p>Before any search ran, the project tested its own premise on " + who
             + ": is coefficient arithmetic a real share of step cost, and does "
             + _gloss("floor-rounding", "Q15 roundoff") + " matter at practical step "
             "sizes?")
    verdict = data.get("verdict")
    if verdict is not None:
        intro += (" Code computed the verdict from the measurements: "
                  f"<strong>{_esc(verdict)}</strong>.")
    parts.append(intro + "</p>")
    charts = [_sweep_chart(n, methods[n]) for n in names]
    charts = [c for c in charts if c]
    if charts:
        parts.append("<p>Left of a crossover step size, "
                     + _gloss("floor-rounding", "floor rounding") + " loses more per extra "
                     "step than the smaller step recovers, so the Q15 line turns back up "
                     "while float64 keeps falling. Hover a point for exact values.</p>")
        parts.append(_legend([("var(--s1)", "Q15 fixed point"),
                              ("var(--s2)", "float64, same steps")]))
        parts.append('<div class="charts">'
                     + "".join(f'<div class="panel">{c}</div>' for c in charts) + "</div>")
    models: set[str] = set()
    for n in names:
        cf = methods[n].get("coefficient_fraction")
        if isinstance(cf, dict):
            models |= {str(k) for k in cf}
    if names:
        head = ("<tr><th>method</th>"
                + "".join(f'<th class="num">coefficient share, {_esc(m)}</th>'
                          for m in sorted(models))
                + '<th class="num">crossover h</th><th>crossover in the practical range</th>'
                "</tr>")
        body = []
        for n in names:
            d = methods[n]
            cf = d.get("coefficient_fraction") if isinstance(d.get("coefficient_fraction"),
                                                              dict) else {}
            prac = d.get("crossover_practical")
            body.append(f"<tr><td>{_esc(n)}</td>"
                        + "".join(f'<td class="num">{_num(cf.get(m))}</td>'
                                  for m in sorted(models))
                        + f'<td class="num">{_num(d.get("crossover_h"))}</td>'
                        f"<td>{'yes' if prac is True else 'no' if prac is False else 'n/a'}"
                        "</td></tr>")
        parts.append('<div class="scroll"><table>' + head + "".join(body) + "</table></div>")
    parts.append(_explain(
        "The protocol fixes two classical methods, rk4 and heun2, on the damped oscillator "
        "and measures two things. One is the share of a step's cycle count spent on "
        "coefficient arithmetic rather than derivative evaluation, under both primary cost "
        "models. The other is a sweep over n = 8, 16, ..., 4096 steps that compares the Q15 "
        "integrator with float64 on identical steps, looking for the step size where "
        "roundoff overtakes truncation error.",
        "The verdict is mechanical. proceed needs every coefficient share at 0.30 or more "
        "and at least one crossover inside the practical range 1e-3 &le; h &le; 1.0. kill "
        "needs every share below 0.15 and no practical crossover. Anything else reads mixed. "
        "A proceed verdict says that searching over coefficients can plausibly matter; it "
        "does not show that any searched method is good."))
    return parts


# Glossary terms: (anchor id, display term, definition paragraphs). Kept in
# alphabetical order of the display term; every definition is grounded in the
# pinned code (fixedpoint, coeffrep, costmodel, archive, ledger, verifier_hash).
_GLOSSARY: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("anchor-methods", "anchor methods", (
        "rk4 and rk38, two classical four-stage, order-4 tableaus with the same stability "
        "polynomial, so they differ in cost only through coefficient arithmetic; the cost "
        "model section compares them.",
    )),
    ("cohens-d", "Cohen's d", (
        "The effect size on a hypothesis verdict: the difference of two cell populations' "
        "means over their pooled standard deviation, with the 0.2 threshold of section 3.",
    )),
    ("cost-bucket", "cost bucket (cycle bucket)", (
        "A log2-spaced bin of cycles per step under m0plus_fast, one axis of the archive "
        "grids: bucket 0 is under 16 cycles, bucket 1 is 16 to 31, and so on up to bucket 7 "
        "at 1024 cycles or more.",
    )),
    ("csd-weight", "CSD weight", (
        "The fewest nonzero signed power-of-two terms that write an integer multiplier m. "
        "The cost model charges a coefficient the cheaper of a shift-add chain of that "
        "length and a hardware multiply.",
    )),
    ("cycle-budget", "cycle budget", (
        "The fixed allowance of every archive evaluation, 65536 cycles per problem per "
        "cost model, so archive errors compare methods at equal budget (section 1).",
    )),
    ("cycles-to-tolerance", "cycles to tolerance", (
        "The modeled cycles a method needs to reach a fixed accuracy target, whether by "
        "more steps, a tighter tolerance or more Newton work per step. The lanes measure it "
        "on the axis-T ladder, one fixed list of final-state error targets run on every "
        "validation problem, and never mix it with the archive's fixed cycle budget.",
    )),
    ("directive", "directive", (
        "A JSON search instruction from the planning model, checked against a strict "
        "schema and stamped on every record it produces. Ids starting with D-E mark "
        "exhaustive enumeration cycles.",
    )),
    ("dyadic-rational", "dyadic rational", (
        "A number m / 2^s, which binary fixed-point hardware applies exactly, so A entries "
        "are snapped to dyadics and every coefficient is stored as an (m, s) pair. The b "
        "weights are solved exactly from the order conditions instead, since snapping them "
        "would put order 3 out of reach.",
    )),
    ("elite", "elite", (
        "The one record a MAP-Elites cell holds: the verified tableau with the lowest "
        "held-out error for that order, stage count and cost bucket, displaced only by a "
        "strictly lower one.",
    )),
    ("embedded-pair", "embedded pair", (
        "Two Runge-Kutta methods sharing one A matrix and one set of stages, with weight "
        "vectors of different order whose difference estimates the local error. An archive "
        "record holds one b vector, so a pair cannot be scored there and is measured "
        "off-archive.",
    )),
    ("floor-rounding", "floor rounding (ASRS)", (
        "The Q15 multiply rounds toward negative infinity, as the ARM ASRS shift does, so "
        "every product loses up to one LSB, always downward (section 1).",
    )),
    ("held-out-set", "held-out set", (
        "The four problems that decide archive fitness and that the optimizer never sees: "
        "pendulum, dc_motor, rc_thermal and quaternion.",
    )),
    ("hypothesis-ledger", "hypothesis ledger", (
        "An append-only file of falsifiable statements about the archive, each carrying a "
        "machine-checkable predicate over cell statistics. Code writes the verdict once the "
        "named cells have enough records.",
    )),
    ("l-stability", "L-stability", (
        "A-stability plus a stability function that tends to zero as stiffness grows, so a "
        "very fast mode is damped out in one step. For the two-stage SDIRK it holds only "
        "for particular values of the diagonal gamma.",
    )),
    ("lsb", "LSB", (
        "Least significant bit: the smallest step Q15 can represent, 2^-15, about 3.05e-5.",
    )),
    ("map-elites", "MAP-Elites archive", (
        "One grid per algebraic order 1 to 4, with cells keyed by stage count 2 to 6 and "
        "cost bucket 0 to 7, each holding only its elite. The filled grid, a map of the "
        "accuracy each shape and cost can reach, is the project's output.",
    )),
    ("off-archive", "off-archive", (
        "Measured outside the scored path: no verifier run, no archive record, and no part "
        "of any archive statistic or hypothesis verdict. Implicit and adaptive numbers are "
        "off-archive because the record schema cannot hold them; they are not "
        "order-verified and not scored, and they do not compare with an archive score.",
    )),
    ("order", "order (measured vs algebraic)", (
        "Algebraic order is the largest p whose order conditions the exact coefficients "
        "satisfy, and it keys the grids. Measured order is the fitted slope of section 2, "
        "and the two can differ.",
    )),
    ("q15", "Q15", (
        "Signed 16-bit fixed point: an integer q in [-32768, 32767] standing for q / 32768, "
        "in which every problem state is stored. Overflow is never saturated; the verifier "
        "turns it into a rejection.",
    )),
    ("sdirk", "SDIRK", (
        "Singly diagonally implicit Runge-Kutta: A is lower triangular with one repeated "
        "value gamma on the diagonal, so each stage is solved for, here with a fixed number "
        "of Newton iterations. The repeated diagonal lets one Jacobian factorization serve "
        "every stage.",
    )),
    ("stage", "stage", (
        "One derivative evaluation inside a step: an s-stage method calls the right-hand "
        "side s times per step. Stage count is one axis of the archive grids.",
    )),
    ("step-size-controller", "step-size controller", (
        "The rule that turns an error estimate into the next step size, here "
        "proportional-integral on the ratio of tolerance to estimated error. A step whose "
        "estimate exceeds the tolerance is rejected and retried smaller, so rejected "
        "attempts cost work too.",
    )),
    ("stiffness-ratio", "stiffness ratio", (
        "The fastest decay rate in a problem over the slowest, from its linearization. A "
        "large ratio caps an explicit method's step size at a mode that has already died "
        "away, and the validation suite labels problems stiff on this basis.",
    )),
    ("tableau", "tableau", (
        "The Butcher tableau (A, b, c) that defines a Runge-Kutta method: stage weights, "
        "final weights and stage times, with A strictly lower triangular for an explicit "
        "method. On this site every tableau is exact fractions, hashed by content.",
    )),
    ("tiers", "tier names", (
        "The evidence tier a record gets on entering its cell, heldout_verified, "
        "search_only or unreplicated, by the rules in section 3. The grid ranks on held-out "
        "error alone, so the heldout_verified count can fall without anything going wrong.",
    )),
    ("verifier-hash", "verifier hash", (
        "A sha256 over the ten pinned scoring files, checked at container start and stored "
        "on every record. Every score therefore carries the version of the code that "
        "produced it, and a table holding more than one hash says so beneath it.",
    )),
    ("work-precision", "work-precision", (
        "A chart of achieved error against work spent, one line per method, for comparing "
        "methods that share no control parameter. Down and to the left is better.",
    )),
)


def _glossary_section() -> str:
    """Every glossary term as a compact definition list, for the methodology page.

    Each term keeps its anchor id, so every _gloss link on the site resolves to
    methodology.html#<anchor>.
    """
    parts = ["<p>Terms used across this site, in alphabetical order. Each definition "
             "says what the code does.</p>", '<dl class="gloss">']
    for anchor, term, paras in _GLOSSARY:
        parts.append(f'<dt id="{_esc(anchor)}">{_esc(term)}</dt>')
        for p in paras:
            parts.append(f"<dd>{_esc(p)}</dd>")
    parts.append("</dl>")
    return "\n".join(parts)


# ----------------------------------------------------------------------------
# practical validation page (rendered only when work_dir()/validation/results.json exists)
# ----------------------------------------------------------------------------

def _vlabel(name_or_hash: str, kind: str) -> str:
    """Chart/table label: classical fixture name, or a short prefix of the content hash."""
    return name_or_hash if kind == "classical" else name_or_hash[:8]


def _validation_chart(data: dict, subset: str = "", caption: str = "") -> str:
    """Per-problem dot rows: Q15 error of every method on a log axis, colored by kind.

    subset names the problem group ("non-stiff", "stiff") in the aria-label, so two
    charts of the same kind on one page stay distinct to a screen reader. caption, when
    given, replaces the full reading guide, so a second chart of the same form on the
    page says what differs instead of repeating it.
    """
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
    group = f"{subset} " if subset else ""

    def draw(w: int, tips: bool = True) -> str:
        ml, mr = 118, 16
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
                tip = f"<title>{_esc(title)}</title>" if tips else ""
                parts.append(f'<circle cx="{_fmt(px)}" cy="{_fmt(cy)}" r="4.5" fill="{sw}" '
                             f'class="cellstroke">{tip}</circle>')
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
        return (f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
                f'aria-label="Q15 final-state error per method on each {_esc(group)}validation '
                'problem, log scale">' + "".join(parts) + "</svg>")

    svg = _phone_pair(draw(640), draw(430, tips=False))
    model = data.get("cost_model")
    source = ('<p class="note">Source: rk-work/validation/results.json, results. '
              "Arithmetic: Q15 with floor rounding"
              + (f" under {_esc(model)}" if isinstance(model, str) and model else "")
              + "; hover a dot for the float64 run over the same steps.</p>")
    guide = caption or (
        "One row per method within each problem; the dot is final-state Q15 error at the "
        "shared cycle budget on a log axis, so left is better, and each method takes as many "
        "steps as its per-step cost allows. Blue is a discovered method, orange a classical "
        "anchor; the ringed dot is the problem's lowest error, printed exactly.")
    return (f"<figure><figcaption>{guide}</figcaption>"
            + _legend([("var(--s1)", "discovered (archive)"), ("var(--s2)", "classical anchor")])
            + svg + source + "</figure>")


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


def _fold(summary: str, body: str) -> str:
    """A closed generic fold. summary is plain text, body is trusted HTML."""
    return (f'<details class="fold"><summary>{_esc(summary)}</summary>'
            f"<div>{body}</div></details>")


# The validation suite's own verdict carries two phrases from the epoch it was written
# in. Every page here calls the class "implicit" and numbers no track, so the sentences
# are substituted on the way to the page, as the RK23 verdict already is. Rewriting
# validation.py instead would need a host validation run to take effect.
_VERDICT_SUBS: tuple[tuple[str, str], ...] = (
    ("The pattern is the stability tax of explicit methods:",
     "Explicit methods pay for stability:"),
    ("This is the motivating evidence for the epoch-3 implicit (SDIRK) track.",
     "This is why the implicit (SDIRK) class is measured."),
)


def _suite_verdict(text) -> str:
    """The suite's verdict with those two phrases in the site's own words."""
    out = str(text)
    for old, new in _VERDICT_SUBS:
        out = out.replace(old, new)
    return out


def _validation_body(data: dict, benchmark) -> tuple[list[str], list[str]]:
    """The practical validation suite: (the visible part, the folds at the bottom)."""
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
    parts = [
        '<p class="lead">The practical validation suite runs '
        f"{len(problems)} problems from embedded application domains that no optimizer saw "
        "and no archive statistic includes. Discovered methods from the live archive and "
        "the classical anchors integrate each one in " + _gloss("q15", "Q15") + " at the "
        "same " + _gloss("cycle-budget", "cycle budget") + f", {_num(budget)} cycles under "
        f"{_esc(data.get('cost_model'))} with "
        + _gloss("floor-rounding", "floor rounding") + ", and each run is scored by its "
        "final-state error against an independent reference solution.</p>"
    ]

    def _ratio_card(v) -> str:
        return f"{v:.3g}" if isinstance(v, (int, float)) else "n/a"

    if has_stiff:
        cards = [
            ("practical problems",
             f"{_num(verdicts.get('practical_problems_won_by_discovered'))} of "
             f"{_num(verdicts.get('practical_problems_compared'))}",
             "non-stiff problems won by a discovered method"),
            ("practical median ratio",
             _ratio_card(verdicts.get("practical_median_ratio_discovered_over_classical")),
             "best discovered over best classical; below 1.0 favors discovered"),
            ("stiff problems",
             f"{_num(verdicts.get('stiff_problems_won_by_discovered'))} of "
             f"{_num(verdicts.get('stiff_problems_compared'))}",
             "won by a discovered method where both sides finish"),
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
             "best discovered over best classical; below 1.0 favors discovered"),
        ]
    parts.append(_cards(cards))
    speed = _speed_sentence(benchmark)
    if speed:
        parts.append(f"<p>{speed}</p>")
    if has_stiff:
        parts.append("<h2>Q15 error per problem: practical (non-stiff)</h2>")
        chart = _validation_chart(_filter_validation(data, set(practical_names)), "non-stiff")
        if chart:
            parts.append('<div class="panel">' + chart + "</div>")
        parts.append("<h2>Q15 error per problem: stiff subset</h2>")
        chart = _validation_chart(
            _filter_validation(data, set(stiff_names)), "stiff",
            "Same layout as the practical chart, for the moderately stiff problems. A run "
            "that overflowed has no error, so it has no dot; the full table at the bottom "
            "lists it.")
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
                 "below 1.0 the discovered method has the lower error.</p>")

    folds: list[str] = []
    # The suite's own verdict restates the cards in prose, so it waits in a fold and the
    # page reaches its first chart inside one screen.
    overall = verdicts.get("overall")
    if overall:
        folds.append(_fold("The suite's verdict, in words",
                           f"<p>{_esc(_suite_verdict(overall))}</p>"))
    table = []
    floats = [r.get("float_error") for r in results if _finite_pos(r.get("float_error"))]
    maxq = [r.get("max_abs_q") for r in results if isinstance(r.get("max_abs_q"), int)]
    if floats and maxq:
        table.append(
            f"<p>Float64 runs of the same tableaus over the same steps land between "
            f"{_num(min(floats))} and {_num(max(floats))}, and the largest raw Q15 "
            f"magnitude seen anywhere is {max(maxq)} of the int16 limit 32767. Where a "
            "row's float64 error sits far below its Q15 error, the Q15 number measures "
            "quantization, not truncation.</p>")
    any_note = any(r.get("note") for r in results)
    table.append('<div class="scroll"><table><tr><th>problem</th><th>method</th><th>kind</th>'
                 '<th class="num">steps</th><th class="num">cycles/step</th>'
                 '<th class="num">Q15 error</th><th class="num">float64 error</th>'
                 '<th class="num">max |q|</th>' + ("<th>note</th>" if any_note else "")
                 + "</tr>")
    for r in results:
        m = str(r.get("method"))
        kind = kind_of.get(m, "")
        table.append(
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
    table.append("</table></div>")
    folds.append(_fold("Q15 against float64, every run", "\n".join(table)))

    mrows = ['<div class="scroll"><table><tr><th>kind</th><th>name / tableau_hash</th>'
             '<th class="num">order</th><th class="num">stages</th><th>roles</th>'
             "<th>archive provenance</th></tr>"]
    for m in methods:
        name = str(m.get("name_or_hash"))
        kind = str(m.get("kind", ""))
        roles = ", ".join(str(x) for x in (m.get("roles") or []))
        arch_info = m.get("archive")
        if isinstance(arch_info, dict):
            prov = (f"cycle {_num(arch_info.get('cycle_id'))}, "
                    f"{_esc(str(arch_info.get('tier')))}, "
                    f"held-out {_num(arch_info.get('heldout_error'))}")
        else:
            prov = "classical fixture"
        mrows.append(
            "<tr>"
            f"<td>{_esc(kind)}</td>"
            f'<td class="hash">{_esc(name)}</td>'
            f'<td class="num">{_num(m.get("order"))}</td>'
            f'<td class="num">{_num(m.get("stages"))}</td>'
            f"<td>{_esc(roles)}</td><td>{prov}</td>"
            "</tr>")
    mrows.append("</table></div>")
    folds.append(_fold("Methods", "\n".join(mrows)))

    prows = []
    for p in problems:
        prows.append(f"<h3>{_esc(str(p.get('name')))}</h3>")
        prows.append(f"<p>{_esc(str(p.get('domain')))}; {_esc(str(p.get('family')))}, "
                     f"{_num(p.get('n_states'))} states, integrated to t = "
                     f"{_num(p.get('t_end'))}.</p>")
        if p.get("stiffness_ratio") is not None:
            basis = p.get("stiffness_basis")
            prows.append(
                f'<p class="note">{"Stiff" if p.get("stiff") else "Non-stiff"}; '
                f"stiffness ratio {_num(p.get('stiffness_ratio'))}"
                + (f" ({_esc(str(basis))})" if basis else "") + ".</p>")
        eq = p.get("equation")
        if eq:
            prows.append(f'<p class="mono">{_esc(str(eq))}</p>')
        ref = p.get("reference")
        if ref:
            prows.append(f"<p>Reference: {_esc(str(ref))}.</p>")
        src = p.get("source")
        if src:
            prows.append(f'<p class="note">Source: {_esc(str(src))}</p>')
    if prows:
        folds.append(_fold("Problems", "\n".join(prows)))
    return parts, folds


# Provenance keys left off the page: "where" is a module docstring about how and where
# the benchmark runs on the host, which tells a reader of the findings nothing.
_PROVENANCE_SKIP = frozenset({"where"})


def _provenance_dl(gen: dict) -> str:
    rows = []
    for k in sorted(gen.keys(), key=str):
        if str(k) in _PROVENANCE_SKIP:
            continue
        v = gen[k]
        shown = ", ".join(str(x) for x in v) if isinstance(v, list) else str(v)
        if "hash" in str(k) or isinstance(v, (list, str)):
            rows.append(f'<dt>{_esc(str(k))}</dt><dd><span class="hash">{_esc(shown)}'
                        "</span></dd>")
        else:
            rows.append(f"<dt>{_esc(str(k))}</dt><dd>{_num(v)}</dd>")
    return '<dl class="meta">\n' + "\n".join(rows) + "\n</dl>"


def render_validation(data: dict | None = None, benchmark: dict | None = None,
                      falsification: dict | None = None) -> str:
    """The evidence that spans classes: validation suite, measured speed, premise test.

    Written when any of the three sources exists. Each section says so in words when
    its own source is absent, and keeps its anchor either way, so a link to #speed or
    #falsification always lands somewhere.
    """
    parts: list[str] = []
    folds: list[str] = []
    if isinstance(data, dict):
        body, folds = _validation_body(data, benchmark)
        parts.extend(body)
    else:
        parts.append('<p class="lead">Evidence that spans the method classes. The practical '
                     "validation suite has not reported in this work directory "
                     "(rk-work/validation/results.json is absent). Below are the "
                     '<a href="#speed">measured time per step</a> and the '
                     '<a href="#falsification">falsification experiment</a> that ran '
                     "before any search.</p>")
    parts.extend(_speed_section(benchmark))
    parts.extend(_falsification_section(falsification))
    prov = []
    if isinstance(data, dict) and isinstance(data.get("generated_from"), dict):
        prov.append("<h3>validation/results.json</h3>" + _provenance_dl(data["generated_from"]))
    if isinstance(benchmark, dict) and isinstance(benchmark.get("generated_from"), dict):
        prov.append("<h3>benchmark/results.json</h3>"
                    + _provenance_dl(benchmark["generated_from"]))
    if prov:
        folds.append(_fold("Provenance", "\n".join(prov)))
    if folds:
        parts.append("<h2>Full tables</h2>")
        parts.extend(folds)
    return _page("Validation", "\n".join(parts), active="validation.html",
                 subtitle="Problems no search saw, measured speed, and the premise test.")


# ----------------------------------------------------------------------------
# measured speed (validation.html#speed), rendered only when
# work_dir()/benchmark/results.json exists
# ----------------------------------------------------------------------------

def _nice_step(raw: float) -> float:
    """The smallest of 1, 2, 2.5, 5, 10 times a power of ten at or above raw."""
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1.0, 2.0, 2.5, 5.0):
        if raw <= m * mag:
            return m * mag
    return 10.0 * mag


def _bench_us_chart(sp: dict, methods: list, protocol=None) -> str:
    """Measured microseconds per Q15 step, one row per method: a faded median bar with
    one dot per problem on top, on a linear axis, and the median in a column of its own.

    The bar is faded so a dot inside it still reads as a filled dot, and the value sits
    right of the plot so it can never land on a mark. protocol is the benchmark's
    timing_protocol, quoted in the source line.
    """
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
    row_h, ml, w, mr = 30, 118, 640, 64
    h = 14 + row_h * len(rows) + 30
    px_per_us = (w - ml - mr) / top

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
                     f'fill="{sw}" fill-opacity="0.35"><title>{_esc(title)}</title></rect>')
        for prob in sorted((d.get("per_problem_us_per_step") or {}).keys()):
            v = d["per_problem_us_per_step"][prob]
            if _finite_pos(v):
                parts.append(f'<circle cx="{_fmt(fx(float(v)))}" cy="{_fmt(y + 10)}" r="4" '
                             f'fill="{sw}" class="cellstroke">'
                             f'<title>{_esc(f"{label} / {prob}: {_num(v)} us per step")}</title></circle>')
        parts.append(f'<text class="lbl" x="{w - 8}" y="{_fmt(y + 15)}" '
                     f'text-anchor="end">{_num(med)}</text>')
    svg = (f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
           'aria-label="Measured microseconds per Q15 step, per method">'
           + "".join(parts) + "</svg>")
    how = ""
    if isinstance(protocol, dict):
        clock, reps, warm = (protocol.get("clock"), protocol.get("n_repeats"),
                             protocol.get("warmup"))
        if isinstance(clock, str) and isinstance(reps, int) and isinstance(warm, int):
            how = f" ({_esc(clock)}, median of {reps} repeats after {warm} warmups)"
    return ('<figure><figcaption>Measured wall clock per Q15 step for each method, linear '
            "axis in microseconds. The faded bar and the number at the right are the "
            "median across the benchmark problems; each dot is one problem. Every method "
            "runs the same pinned solve_q15 path, so row differences isolate tableau cost. "
            "Blue is a discovered method, orange a classical anchor; hover any mark for "
            "exact values.</figcaption>"
            + _legend([("var(--s1)", "discovered (archive)"), ("var(--s2)", "classical anchor")])
            + svg + '<p class="note">Source: rk-work/benchmark/results.json, '
            "speedup.per_method_us_per_step. Python wall clock of the pinned solve_q15 path "
            "in Q15" + how + ".</p></figure>")


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
            f'speedup {float(gm):.3f}x). Details are in the <a href="validation.html#speed">'
            "measured speed section</a>.")


def _speed_section(data) -> list[str]:
    """Measured time per step, for the validation page, from benchmark/results.json."""
    parts = ['<h2 id="speed">Measured time per step</h2>']
    if not isinstance(data, dict):
        parts.append('<p class="note">rk-work/benchmark/results.json has not been written '
                     "in this work directory, so no wall-clock timing is reported.</p>")
        return parts
    sp = data.get("speedup") if isinstance(data.get("speedup"), dict) else {}
    methods = data.get("methods") or []
    verdicts = data.get("verdicts") if isinstance(data.get("verdicts"), dict) else {}
    corr = data.get("correlation") if isinstance(data.get("correlation"), dict) else {}
    env = data.get("environment") if isinstance(data.get("environment"), dict) else {}
    kind_of = {str(m.get("name_or_hash")): str(m.get("kind", "")) for m in methods}
    champ = str(sp.get("champion") or "")
    base = str(sp.get("baseline") or "")
    parts.append("<p>Wall clock per Q15 step, next to the analytic cycle model the rest of "
                 "this site ranks by. Every fixed-step row runs the same pinned Q15 path at "
                 "the shared " + _gloss("cycle-budget", "cycle budget")
                 + f" of {_num(data.get('budget_cycles'))} cycles.</p>")
    chart = _bench_us_chart(sp, methods, data.get("timing_protocol"))
    if chart:
        parts.append('<div class="panel">' + chart + "</div>")
    else:
        parts.append('<p class="note">The benchmark document carries no per-method timing, '
                     "so none is charted.</p>")

    rows = [r for r in (sp.get("rows") or []) if isinstance(r, dict)]
    gm_m = sp.get("geomean_measured_speedup_rk4_over_champion")
    gm_p = sp.get("geomean_predicted_speedup_rk4_over_champion")
    if rows:
        champ_label = _vlabel(champ, kind_of.get(champ, "discovered")) if champ else "champion"
        bname = _esc(base or "baseline")

        def _ratio(v) -> str:
            return f"{float(v):.3f}" if isinstance(v, (int, float)) else "n/a"

        body = ['<div class="scroll"><table><tr><th>problem</th>'
                f'<th class="num">champion us/step</th><th class="num">{bname} us/step</th>'
                '<th class="num">predicted ratio</th><th class="num">measured ratio</th>'
                f'<th class="num">champion Q15 error</th><th class="num">{bname} Q15 error</th>'
                "<th>lower error</th></tr>"]
        for r in rows:
            lower = champ_label if r.get("champion_error_lower") else (base or "baseline")
            body.append(
                "<tr>"
                f"<td>{_esc(str(r.get('problem')))}</td>"
                f'<td class="num">{_num(r.get("champion_us_per_step"))}</td>'
                f'<td class="num">{_num(r.get("rk4_us_per_step"))}</td>'
                f'<td class="num">{_ratio(r.get("predicted_ratio_rk4_over_champion"))}</td>'
                f'<td class="num">{_ratio(r.get("measured_ratio_rk4_over_champion"))}</td>'
                f'<td class="num">{_num(r.get("champion_error"))}</td>'
                f'<td class="num">{_num(r.get("rk4_error"))}</td>'
                f"<td>{_esc(lower)}</td>"
                "</tr>")
        body.append("</table></div>")
        parts.append("\n".join(body))
    bits = []
    if _finite_pos(gm_m) and _finite_pos(gm_p):
        # The measured mean is in the sentence at the top of this page, so this line
        # carries what is new beside it: what the cycle model predicted.
        bits.append((f"The cycle model predicts {float(gm_p):.3f}."
                     if _speed_sentence(data) else
                     f"Geometric mean over the problems: measured {float(gm_m):.3f}, "
                     f"predicted {float(gm_p):.3f}.")
                    + " A ratio above 1.0 means the champion needs less time per step.")
    if isinstance(sp.get("champion_error_lower_count"), int):
        ratio = sp.get("median_error_ratio_champion_over_rk4")
        bits.append(f"At the same budget the champion reaches the lower Q15 error on "
                    f"{_num(sp.get('champion_error_lower_count'))} of "
                    f"{_num(sp.get('error_comparisons'))} problems, with a median error "
                    f"ratio (champion over {_esc(base or 'baseline')}) of "
                    + (f"{float(ratio):.3g}" if _finite_pos(ratio) else _num(ratio)) + ".")
    # The benchmark's cycle-model verdict states the correlation with its caveat; the
    # page composes its own sentence only when that verdict is missing.
    if _finite_pos(corr.get("pearson_r")) and not verdicts.get("cycle_model"):
        bits.append(f"Cycle count and measured time correlate with Pearson r = "
                    f"{float(corr['pearson_r']):.3f} over {_num(corr.get('n_points'))} "
                    "fixed-step Q15 runs.")
    if bits:
        parts.append("<p>" + " ".join(bits) + "</p>")
    if verdicts.get("cycle_model"):
        parts.append(f"<p>{_esc(str(verdicts.get('cycle_model')))}</p>")
    # One wall-clock caveat stays in view (the verdict above). The head-to-head's own
    # argument, the environment and the timing caveat are fine print, in one fold.
    fine = []
    if sp.get("caveat"):
        fine.append(f"<p>{_esc(str(sp.get('caveat')))}</p>")
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
        fine.append("<p>Environment: " + _esc("; ".join(env_bits)) + ".</p>")
    if env.get("timing_caveat"):
        fine.append(f"<p>{_esc(str(env.get('timing_caveat')))}</p>")
    if fine:
        parts.append('<details class="explain"><summary>How the timings were taken</summary>'
                     f"<div>{''.join(fine)}</div></details>")
    return parts


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


def _lane_elites_path(cls: str) -> Path:
    """Where one lane writes its ranked elites."""
    return work_dir() / f"{cls}_archive" / "elites.json"


def _load_lane_archive(cls: str) -> dict | None:
    """One lane's elites document, or None when it is missing or does not parse.

    Unlike the axes and shares documents beside it, this one is a page source: it is
    bounded by its own cap, ordered by a total order it states in its own field, and it
    carries the sentence saying what it ranked and over what. The per-day lane records
    in the same directory stay off every page.
    """
    if cls not in LANE_CLASSES:
        return None
    return _load_json_or_none(_lane_elites_path(cls))


_LANE_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.jsonl$")


def _lane_records_present(cls: str) -> bool:
    """Whether a lane has written any per-day search log. Presence only, never opened.

    These are the records that stayed off the traceability list when the lane elites
    document was admitted to it: one line per candidate the lane enumerated, ranked
    against nothing, and their per-candidate numbers would invite the comparison with a
    scored archive record that must never be made.
    """
    if cls not in LANE_CLASSES:
        return False
    try:
        names = sorted(p.name for p in (work_dir() / f"{cls}_archive").iterdir())
    except OSError:
        return False
    return any(_LANE_DAY_RE.match(n) for n in names)


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
    """The card row an off-archive class page opens with, plus this class's own cards.

    The hash on the last card is the side-track executor's. The lane search has a code
    hash of its own, shown with the lane elites, and the two are unrelated, so each
    carries its own label.
    """
    jobs = sorted({str(e.get("job", "")) for e in entries if e.get("job")})
    newest = _newest_code_hash(entries)
    cards = [("points measured", _count(_distinct_points(entries)), _points_caption(entries)),
             ("jobs", str(len(jobs)), "each closes one open design question")]
    cards.extend(extra)
    cards.append(("side-track code hash", newest[:12] if newest else "n/a",
                  "the executor and prototypes, at the latest measurement"))
    return _cards(cards)


def _job_tables(entries, artifacts) -> list[str]:
    """One section per job: what it closes, the arithmetic it ran in, and its points.

    One row per point, from its newest ledger line, which is the reading the charts draw.
    A point measured again after the executor changed has an older line under earlier
    code; the code-hash rule no longer counts it, so the table leaves it out and the
    job's subhead says how many ledger lines there are. Every count on the page is then
    a count of points: the per-job counts add up to the fold's own.
    """
    by_job: dict[str, list[dict]] = {}
    for e in entries:
        by_job.setdefault(str(e.get("job", "")), []).append(e)
    parts: list[str] = []
    for job in sorted(by_job):
        rows_in = sorted(_latest_points(by_job[job]), key=lambda e: str(e.get("key", "")))
        doc = artifacts.get(str(rows_in[0].get("artifact", "")), {})
        parts.append(f"<h3>{_esc(job)}</h3>")
        n, lines = len(rows_in), len(by_job[job])
        extra = "" if n == lines else f", from {lines} ledger lines"
        parts.append(f'<p class="sub">{n} {"point" if n == 1 else "points"}{extra}</p>')
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


def _latest_points(entries) -> list[dict]:
    """One ledger line per point, the newest by timestamp, in (job, point) order.

    A point measured again after the executor changed has a line under each code hash.
    The folded tables and the charts both use the newest line per point, so a re-measured
    point is listed and plotted once; the job's subhead gives the ledger-line count.
    """
    best: dict[tuple[str, str], tuple[tuple[str, str], dict]] = {}
    for e in entries:
        if not isinstance(e, dict):
            continue
        key = (str(e.get("job", "")), str(e.get("key", "")))
        rank = (str(e.get("ts", "")), str(e.get("code_hash", "")))
        if key not in best or rank > best[key][0]:
            best[key] = (rank, e)
    return [best[k][1] for k in sorted(best)]


def _job_docs(entries, artifacts, job: str) -> list[tuple[dict, dict]]:
    """(ledger line, artifact) for every point of one job, newest reading per point."""
    arts = artifacts if isinstance(artifacts, dict) else {}
    out = []
    for e in _latest_points(entries):
        if str(e.get("job", "")) != job:
            continue
        doc = arts.get(str(e.get("artifact", "")))
        if isinstance(doc, dict):
            out.append((e, doc))
    return out


def _job_arith(entries, artifacts, job: str) -> str:
    """". Arithmetic: <the job's own string>" for a card caption, or "" when no artifact
    of that job states one. Plain text after the vocabulary pass: _cards escapes it."""
    arts = artifacts if isinstance(artifacts, dict) else {}
    seen = sorted({str(d["arithmetic"]).strip() for e in entries
                   if str(e.get("job", "")) == job
                   for d in [arts.get(str(e.get("artifact", "")))]
                   if isinstance(d, dict) and isinstance(d.get("arithmetic"), str)
                   and d["arithmetic"].strip()})
    if not seen:
        return ""
    return ". Arithmetic: " + literature_mod.soften("; ".join(seen))


def _arith_of(docs) -> str:
    """Every distinct arithmetic string the artifacts state, softened, in sorted order.

    Side-track numbers carry their own job's arithmetic wherever they appear, so each
    chart's source line quotes it rather than a page-level claim.
    """
    seen = sorted({d["arithmetic"].strip() for _e, d in docs
                   if isinstance(d.get("arithmetic"), str) and d["arithmetic"].strip()})
    return "; ".join(_soft(s) for s in seen) if seen else "not stated by the artifacts"


def _points_fold(entries, artifacts) -> str:
    """Every ledger line of one class, one table per job, folded at the foot of the page."""
    body = ('<p class="note">One table per job and one row per point, from its newest '
            "ledger line. A point counts as measured only under the code hash that "
            "measured it, so an older line from earlier code is left out; that rule and "
            'the retry policy are in the <a href="methodology.html#ledger">measurement '
            "ledger</a>. " + _esc(_NOT_THESE_WHERE) + "</p>"
            + "\n".join(_job_tables(entries, artifacts)))
    n = _distinct_points(entries)
    return _fold(f"Every measured point ({n} {'point' if n == 1 else 'points'})", body)


# The canonical invariants for every off-archive number on this site. One constant,
# rendered once on each side-class page, so the invariants cannot drift into two
# versions that disagree.
_NOT_THESE_NUMBERS: tuple[tuple[str, str], ...] = (
    ("Not scored.",
     "Nothing here enters the archive or moves a hypothesis verdict."),
    ("Not Q15.",
     "Unless a job or a row says otherwise, runs are float64 with no cycle budget or "
     "floor bias; each job states its arithmetic."),
    ("Not a cost comparison with the archive.",
     "None of these cycle counts is an archive score, and none converts into one."),
    ("Preliminary.",
     "These readings pick the parameters frozen at the next epoch boundary, where the "
     "scored code is written fresh."),
)

_NOT_THESE_WHERE = ("Plan, job catalogue and invariants: docs/SIDETRACK-AUTOMATION.md in "
                    "the harness repository. Designs these feed: docs/EPOCH2-DESIGN.md "
                    "and docs/EPOCH3-DESIGN.md.")


def _not_these_numbers() -> str:
    items = "".join(f"<li><strong>{_esc(h)}</strong> {_esc(b)}</li>"
                    for h, b in _NOT_THESE_NUMBERS)
    return f"<h2>What these numbers are not</h2>\n<ul>{items}</ul>"


_NO_POINTS = ("No measurements recorded under the current code hash. The ledger has "
              "nothing for this class yet, which is where a fresh work directory starts, "
              "so the sections that would carry its numbers are left out rather than "
              "shown as an empty table or a zero.")


# ----------------------------------------------------------------------------
# library counterparts, read from benchmark/results.json
# ----------------------------------------------------------------------------

# Which method class each scipy integrator belongs to. Held here as data rather than
# imported: sitegen runs inside the cycle and rk_harness.benchcounts pulls in the
# prototypes and the second pass. tests/test_t18_class_pages.py asserts this mapping
# agrees with benchcounts.SCIPY_ADAPTIVE and benchcounts.SCIPY_IMPLICIT, so the two
# cannot drift apart without a failing test. The matched-accuracy chart also draws
# the library solvers in this order, so each keeps its marker shape.
_LIB_CLASS: dict[str, str] = {
    "RK23": "adaptive", "RK45": "adaptive", "DOP853": "adaptive",
    "Radau": "implicit", "BDF": "implicit", "LSODA": "implicit",
}

_NEVER_SAME_WORK = ("Library integrators choose their own step counts, so their work is "
                    "context and never a same-work comparison with any fixed-step run on "
                    "this site.")


def _sort_num(v) -> tuple:
    """A total order that puts every finite number ahead of everything else."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
        return (1, 0.0)
    return (0, float(v))


def _matched_rows(benchmark, cls: str) -> list[dict]:
    """benchmark/results.json matched_accuracy, restricted to one method class.

    Matched ACCURACY, not matched tolerance, and the difference is the whole point of the
    table. Asking two solvers for the same tolerance does not put them at the same
    accuracy: one overshoots and one undershoots, and comparing their work then compares
    two different jobs. These rows fix the achieved error instead and report what each
    side spent to reach it.
    """
    if not isinstance(benchmark, dict):
        return []
    rows = [r for r in (benchmark.get("matched_accuracy") or [])
            if isinstance(r, dict) and str(r.get("class")) == cls]
    return sorted(rows, key=lambda r: (str(r.get("problem")), str(r.get("side")),
                                       str(r.get("solver")), _sort_num(r.get("target_error"))))


def _three_class_text(benchmark, *keys) -> str:
    """One sentence out of verdicts.three_class, or "" when the document predates it."""
    if not isinstance(benchmark, dict):
        return ""
    node = benchmark.get("verdicts")
    if not isinstance(node, dict):
        return ""
    node = node.get("three_class")
    for key in keys:
        if not isinstance(node, dict):
            return ""
        node = node.get(key)
    return str(node) if isinstance(node, str) else ""


def _matched_table(rows) -> str:
    """Ours and the library side by side, with the grade of every number stated.

    `cost_grade` travels with the row rather than being inferred here, because whether a
    cycle count is a measurement, a model or absent is a property of how the row was
    produced and not of how it is displayed.
    """
    out = ['<div class="scroll"><table class="matched"><tr><th>problem</th><th>side</th>'
           "<th>solver</th><th>arithmetic</th>"
           '<th class="num">target error</th><th class="num">achieved</th>'
           '<th class="num">steps</th><th class="num">rhs evaluations</th>'
           '<th class="num">analytic cycles</th><th>cost grade</th><th>status</th></tr>']
    for r in rows:
        # Columns 5 to 9 are right-aligned by the table.matched rule in _STYLE.
        out.append(
            "<tr>"
            f"<td>{_soft(r.get('problem'))}</td>"
            f"<td>{_soft(r.get('side'))}</td>"
            f"<td>{_soft(r.get('solver'))}</td>"
            f"<td>{_soft(r.get('arithmetic'))}</td>"
            f"<td>{_num(r.get('target_error'))}</td>"
            f"<td>{_num(r.get('achieved_error'))}</td>"
            f"<td>{_num(r.get('n_steps_accepted'))}</td>"
            f"<td>{_num(r.get('nfev'))}</td>"
            f"<td>{_num(r.get('analytic_cycles_total'))}</td>"
            f"<td>{_soft(r.get('cost_grade'))}</td>"
            f"<td>{_soft(r.get('status'))}</td>"
            "</tr>")
    out.append("</table></div>")
    return "".join(out)


def _matched_split(rows) -> tuple[list[dict], list[dict]]:
    """(the rows the table prints, the rows it collapses into one line).

    A skipped row carries no measurement at all: every number in it is n/a and its
    status is the whole content. Twenty of them together read as a table of failures,
    so they are named and counted under the table instead.
    """
    empty = ("achieved_error", "n_steps_accepted", "nfev", "analytic_cycles_total")
    shown, skipped = [], []
    for r in rows:
        if str(r.get("status")) == "skipped" and all(r.get(k) is None for k in empty):
            skipped.append(r)
        else:
            shown.append(r)
    return shown, skipped


def _skipped_note(rows) -> str:
    """The line that stands in for the collapsed rows: which solver, where, and why."""
    if not rows:
        return ""
    solvers = sorted({_solver_label(r.get("solver")) for r in rows if r.get("solver")})
    probs = sorted({str(r.get("problem")) for r in rows if r.get("problem")})
    reasons = sorted({str(r.get("reason")).strip() for r in rows
                      if isinstance(r.get("reason"), str) and r.get("reason").strip()})
    n = len(rows)
    text = (f"A further {n} {'row is' if n == 1 else 'rows are'} left out of the table "
            f"because {'it' if n == 1 else 'they'} never ran: {', '.join(solvers)} on "
            f"{', '.join(probs)}")
    if len(reasons) == 1:
        text += f", each recorded with the same reason: {reasons[0]}"
    return f'<p class="note">{_soft(text)}.</p>'


def _names_only_these_rows(benchmark, text: str, rows) -> bool:
    """Whether a generated sentence names only solvers this class's rows hold.

    The benchmark writes one verdict for the stiff problems across all three classes,
    listing every solver that met the target. Above a single-class table it sends the
    reader looking for rows that are on another tab, so it is printed only where the
    table below it can answer it.
    """
    own = {_solver_label(r.get("solver")) for r in rows if r.get("solver")}
    every = set()
    if isinstance(benchmark, dict):
        every = {_solver_label(r.get("solver"))
                 for r in (benchmark.get("matched_accuracy") or [])
                 if isinstance(r, dict) and r.get("solver")}
    return not any(re.search(r"\b" + re.escape(name) + r"\b", text)
                   for name in sorted(every - own))


_MATCHED_ABSENT = (
    "The benchmark document in this work directory predates the three-class tables, so "
    "the matched-accuracy comparison is not stated here. It appears once "
    "rk_harness.benchmark has been run against the current code; nothing is inferred "
    "from the older tables in its place.")


# ----------------------------------------------------------------------------
# chart primitives for the class pages: log or linear axes, marker shapes, stacks
# ----------------------------------------------------------------------------

def _lin_label(v: float) -> str:
    return f"{v:g}"


def _finite_num(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and v == v and v not in (float("inf"), float("-inf")))


def _plain(v) -> str:
    """A value for hover text: numbers as _num prints them, text as it is."""
    if v is None:
        return "n/a"
    if isinstance(v, (int, float, bool)):
        return _num(v)
    return str(v)


def _clip(text: str, room: float) -> str:
    """Text cut to fit room characters, with an ASCII ellipsis when it had to be cut."""
    n = max(int(room), 4)
    return text if len(text) <= n else text[:n - 3] + "..."


def _to_frac(v):
    """An exact rational from "3/8", 0.375, 3 or [3, 8], or None for anything else."""
    if v is None or isinstance(v, bool):
        return None
    if (isinstance(v, (list, tuple)) and len(v) == 2
            and all(isinstance(x, int) and not isinstance(x, bool) for x in v) and v[1]):
        return Fraction(v[0], v[1])
    if isinstance(v, int):
        return Fraction(v)
    if isinstance(v, float) and _finite_num(v):
        return Fraction(v).limit_denominator(1 << 20)
    if isinstance(v, str):
        try:
            return Fraction(v.strip())
        except (ValueError, ZeroDivisionError):
            return None
    return None


def _frac_float(v):
    f = _to_frac(v)
    return float(f) if f is not None else None


def _plain_frac(f: Fraction) -> str:
    """A fraction for prose: 2 rather than 2/1, 5/4 as it is."""
    return str(f.numerator) if f.denominator == 1 else _frac(f)


class _Scale:
    """One axis, log or linear, with its tick values and how to print them."""

    def __init__(self, lo: float, hi: float, log: bool, ticks, fmt=None):
        self.lo, self.hi, self.log = float(lo), float(hi), bool(log)
        self.ticks = [float(t) for t in ticks]
        self.fmt = fmt or (_pow_label if log else _lin_label)

    def frac(self, v: float) -> float:
        if self.log:
            span = math.log10(self.hi) - math.log10(self.lo) or 1.0
            return (math.log10(max(v, self.lo)) - math.log10(self.lo)) / span
        return (v - self.lo) / ((self.hi - self.lo) or 1.0)


def _log_scale(lo: float, hi: float, fmt=None) -> _Scale:
    """A log axis widened to whole decades around lo..hi."""
    a = 10 ** math.floor(math.log10(lo))
    b = 10 ** math.ceil(math.log10(hi))
    if b <= a:
        b = a * 10
    return _Scale(a, b, True, _log_ticks(a, b), fmt)


def _lin_scale(lo: float, hi: float, n: int = 5, fmt=None) -> _Scale:
    """A linear axis widened to a whole number of nice steps around lo..hi."""
    if hi <= lo:
        pad = abs(lo) * 0.05 or 1.0
        lo, hi = lo - pad, hi + pad
    step = _nice_step((hi - lo) / n)
    a = step * math.floor(lo / step + 1e-9)
    b = step * math.ceil(hi / step - 1e-9)
    count = int(round((b - a) / step))
    return _Scale(a, b, False, [round(a + k * step, 9) for k in range(count + 1)], fmt)


class _Axes:
    """A plot area on two _Scales, drawn the way _LogLog draws: recessive grid, labels
    thinned to clear each other, the bottom y label nudged clear of the x row."""

    def __init__(self, width: int, height: int, xs: _Scale, ys: _Scale, xlabel: str = "",
                 ylabel: str = "", ml: int = 52, mr: int = 14, mt: int = 10, mb: int = 34,
                 title: str = ""):
        self.w, self.h = width, height
        self.xs, self.ys = xs, ys
        self.ml, self.mr, self.mt, self.mb = ml, mr, mt, mb
        self.xlabel, self.ylabel, self.title = xlabel, ylabel, title
        self.parts: list[str] = []

    def x(self, v: float) -> float:
        return self.ml + self.xs.frac(v) * (self.w - self.ml - self.mr)

    def y(self, v: float) -> float:
        return self.h - self.mb - self.ys.frac(v) * (self.h - self.mt - self.mb)

    def frame(self) -> None:
        base = self.h - self.mb
        xl = [self.xs.fmt(t) for t in self.xs.ticks]
        xw = max((len(s) for s in xl), default=1) * 7.0 + 4.0
        xk = _tick_stride(len(xl), self.w - self.ml - self.mr, xw)
        yk = _tick_stride(len(self.ys.ticks), base - self.mt, 21.0)
        for i, tv in enumerate(self.xs.ticks):
            px = self.x(tv)
            self.parts.append(f'<line class="gridline" x1="{_fmt(px)}" y1="{self.mt}" '
                              f'x2="{_fmt(px)}" y2="{base}"/>')
            if i % xk == 0:
                half = len(xl[i]) * 3.5
                lx = min(max(px, half + 1.0), self.w - half - 1.0)
                self.parts.append(f'<text x="{_fmt(lx)}" y="{base + 14}" '
                                  f'text-anchor="middle">{_esc(xl[i])}</text>')
        for i, tv in enumerate(self.ys.ticks):
            py = self.y(tv)
            self.parts.append(f'<line class="gridline" x1="{self.ml}" y1="{_fmt(py)}" '
                              f'x2="{self.w - self.mr}" y2="{_fmt(py)}"/>')
            if i % yk == 0:
                ly = min(py + 3.5, base - 2.0)
                self.parts.append(f'<text x="{self.ml - 6}" y="{_fmt(ly)}" '
                                  f'text-anchor="end">{_esc(self.ys.fmt(tv))}</text>')
        self.parts.append(f'<line class="axis" x1="{self.ml}" y1="{base}" '
                          f'x2="{self.w - self.mr}" y2="{base}"/>')
        self.parts.append(f'<line class="axis" x1="{self.ml}" y1="{self.mt}" '
                          f'x2="{self.ml}" y2="{base}"/>')
        if self.xlabel:
            self.parts.append(f'<text x="{_fmt((self.ml + self.w - self.mr) / 2)}" '
                              f'y="{self.h - 4}" text-anchor="middle">{_esc(self.xlabel)}</text>')
        if self.ylabel:
            mid = _fmt((self.mt + base) / 2)
            self.parts.append(f'<text x="12" y="{mid}" text-anchor="middle" '
                              f'transform="rotate(-90 12 {mid})">{_esc(self.ylabel)}</text>')
        if self.title:
            room = (self.w - self.ml - 4) / 7.0
            self.parts.append(f'<text class="lbl" x="{self.ml}" y="{self.mt - 8}">'
                              f"{_soft(_clip(self.title, room))}</text>")

    def svg(self, aria: str) -> str:
        return (f'<svg viewBox="0 0 {self.w} {self.h}" width="{self.w}" height="{self.h}" '
                f'role="img" aria-label="{_soft(aria)}">' + "".join(self.parts) + "</svg>")


# Marker shapes, in the order a side's solvers take them. Shape is the second channel
# wherever a chart has more series than the three hues that stay distinct in every
# pairing (the palette check in both modes), so identity never rests on hue alone.
_SHAPES = ("circle", "square", "tri", "diamond")

# Four hues in the validated order. The fourth passes only the adjacent-pair checks,
# so it is used in stacks and never in a scatter; a scatter takes at most three.
_SERIES4 = ("var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)")


def _series_fill(i: int, limit: int = 4) -> str:
    """Slot i of the validated order, or neutral grey past the limit."""
    return _SERIES4[i] if i < min(limit, len(_SERIES4)) else "var(--text-3)"


def _mark(shape: str, cx: float, cy: float, fill: str, tip: str = "") -> str:
    """One marker with its hover text. Filled shapes carry the 2px surface ring."""
    t = f"<title>{_soft(tip)}</title>" if tip else ""
    if shape == "square":
        return (f'<rect x="{_fmt(cx - 4)}" y="{_fmt(cy - 4)}" width="8" height="8" rx="1" '
                f'fill="{fill}" class="cellstroke">{t}</rect>')
    if shape == "tri":
        return (f'<path d="M {_fmt(cx)} {_fmt(cy - 5.5)} L {_fmt(cx + 5)} {_fmt(cy + 4)} '
                f'L {_fmt(cx - 5)} {_fmt(cy + 4)} Z" fill="{fill}" class="cellstroke">{t}</path>')
    if shape == "diamond":
        return (f'<path d="M {_fmt(cx)} {_fmt(cy - 5.5)} L {_fmt(cx + 5.5)} {_fmt(cy)} '
                f'L {_fmt(cx)} {_fmt(cy + 5.5)} L {_fmt(cx - 5.5)} {_fmt(cy)} Z" '
                f'fill="{fill}" class="cellstroke">{t}</path>')
    if shape == "cross":
        return (f'<path d="M {_fmt(cx - 4)} {_fmt(cy - 4)} L {_fmt(cx + 4)} {_fmt(cy + 4)} '
                f'M {_fmt(cx - 4)} {_fmt(cy + 4)} L {_fmt(cx + 4)} {_fmt(cy - 4)}" '
                f'fill="none" stroke="{fill}" stroke-width="2.5" stroke-linecap="round">{t}</path>')
    return (f'<circle cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="4.5" fill="{fill}" '
            f'class="cellstroke">{t}</circle>')


def _key_legend(items) -> str:
    """A legend row whose keys are the chart's own marks, for charts where shape carries
    identity alongside hue. items are (fill, shape, label)."""
    out = []
    for fill, shape, label in items:
        out.append('<span><svg class="key" viewBox="0 0 12 12" width="12" height="12" '
                   f'aria-hidden="true">{_mark(shape, 6, 6, fill)}</svg>{_soft(label)}</span>')
    return '<div class="legend">' + "".join(out) + "</div>"


def _chart_block(html: str) -> str:
    """A figure goes in a panel; a one-line absence note stands on its own."""
    return f'<div class="panel">{html}</div>' if html.startswith("<figure") else html


def _round_end_bar(x: float, y: float, w: float, h: float, fill: str, tip: str,
                   r: float = 4, attrs: str = "") -> str:
    """A horizontal bar, square at the baseline with its data end rounded."""
    r = min(r, h / 2, max(w, 0.01))
    d = (f"M {_fmt(x)} {_fmt(y)} L {_fmt(x + w - r)} {_fmt(y)} "
         f"Q {_fmt(x + w)} {_fmt(y)} {_fmt(x + w)} {_fmt(y + r)} "
         f"L {_fmt(x + w)} {_fmt(y + h - r)} Q {_fmt(x + w)} {_fmt(y + h)} "
         f"{_fmt(x + w - r)} {_fmt(y + h)} L {_fmt(x)} {_fmt(y + h)} Z")
    return (f'<path d="{d}" fill="{fill}"{attrs} class="cellstroke">'
            f"<title>{_soft(tip)}</title></path>")


def _stack_bar(x0: float, y: float, h: float, segs, px_per: float) -> tuple[list[str], float]:
    """A horizontal stack of (value, fill, hover text[, extra attributes]) segments;
    returns (parts, end x).

    The 2px surface ring on each segment is the gap between neighbours, so no border
    is drawn to separate them. Only the last segment's end is rounded.
    """
    live = [s for s in segs if s[0] > 0]
    out, x = [], x0
    for i, seg in enumerate(live):
        v, fill, tip = seg[:3]
        attrs = seg[3] if len(seg) > 3 else ""
        w = max(v * px_per, 1.0)
        if i == len(live) - 1:
            out.append(_round_end_bar(x, y, w, h, fill, tip, attrs=attrs))
        else:
            out.append(f'<rect x="{_fmt(x)}" y="{_fmt(y)}" width="{_fmt(w)}" '
                       f'height="{_fmt(h)}" fill="{fill}"{attrs} class="cellstroke">'
                       f"<title>{_soft(tip)}</title></rect>")
        x += w
    return out, x


def _hbar_frame(xs: _Scale, ml: float, span: float, top: float, base: float) -> list[str]:
    """Vertical gridlines, tick labels under the plot and the baseline, for bar rows."""
    labels = [xs.fmt(t) for t in xs.ticks]
    k = _tick_stride(len(labels), span, max((len(s) for s in labels), default=1) * 7.0 + 4.0)
    parts = []
    for i, tv in enumerate(xs.ticks):
        px = ml + xs.frac(tv) * span
        parts.append(f'<line class="gridline" x1="{_fmt(px)}" y1="{_fmt(top)}" '
                     f'x2="{_fmt(px)}" y2="{_fmt(base)}"/>')
        if i % k == 0:
            parts.append(f'<text x="{_fmt(px)}" y="{_fmt(base + 16)}" '
                         f'text-anchor="middle">{_esc(labels[i])}</text>')
    parts.append(f'<line class="axis" x1="{_fmt(ml)}" y1="{_fmt(top)}" x2="{_fmt(ml)}" '
                 f'y2="{_fmt(base)}"/>')
    return parts


def _end_label(end: float, y: float, text: str, w: float) -> str:
    """A value at a bar's end: just outside it when that fits the frame, else inside."""
    if end + 6 + len(text) * 7.0 > w - 2:
        return (f'<text class="lbl" x="{_fmt(end - 6)}" y="{_fmt(y)}" '
                f'text-anchor="end">{_esc(text)}</text>')
    return f'<text class="lbl" x="{_fmt(end + 6)}" y="{_fmt(y)}">{_esc(text)}</text>'


def _absent(text: str) -> str:
    return f'<p class="note">{_esc(text)}</p>'


# ----------------------------------------------------------------------------
# matched accuracy: the chart, the verdict and the folded table
# ----------------------------------------------------------------------------

_SIDE_FILL = {"ours": "var(--s1)", "library": "var(--s2)"}


def _solver_label(name) -> str:
    """A content-hash solver name shortens to 12 characters, as the tables print it."""
    s = str(name)
    if len(s) >= 32 and all(c in "0123456789abcdef" for c in s):
        return s[:12]
    return s


def _matched_solvers(rows) -> list[tuple[str, str]]:
    """(side, solver) in a fixed order: ours by name, then the library in _LIB_CLASS order.

    Taken over every row of the class rather than per panel, so a solver keeps its hue
    and shape on every panel whether or not it reached a target there.
    """
    lib_rank = {name: i for i, name in enumerate(_LIB_CLASS)}
    pairs = {(str(r.get("side")), str(r.get("solver"))) for r in rows
             if str(r.get("side")) in _SIDE_FILL}
    return sorted(pairs, key=lambda p: (0 if p[0] == "ours" else 1,
                                        lib_rank.get(p[1], len(lib_rank)), p[1]))


def _multiples_open(width: int, cols: int) -> str:
    """The opening tag for a set of small multiples, carrying its own track size.

    The panel around a figure is fit-content, which only ends where the charts end if the
    box inside it has a max-content width of a few panels rather than of one long row. The
    drawn panel width travels with the tag so the CSS tracks cannot drift from the svg.
    """
    return f'<div class="multiples" style="--tw:{width}px;--cols:{cols}">'


_MATCHED_PANEL_W, _MATCHED_COLS = 272, 3


def _matched_chart(rows, cls: str) -> str:
    """Achieved error against rhs evaluations at matched accuracy, one panel per problem.

    Small multiples on shared axes, so the panels compare. Hue says which side a solver
    is on and the marker shape says which solver it is: a class has up to five solvers,
    and only three hues stay distinct in every pairing. A mark is a reached row, and
    targets that one run met share its mark. Library rows carry no cycle number, so the
    x axis is rhs evaluations, which both sides report.
    """
    solvers = _matched_solvers(rows)
    shape_of: dict[tuple[str, str], str] = {}
    for side in _SIDE_FILL:
        for i, key in enumerate([k for k in solvers if k[0] == side][:len(_SHAPES)]):
            shape_of[key] = _SHAPES[i]
    arith: dict[tuple[str, str], set] = {}
    pts: dict[str, dict] = {}
    stiff: set[str] = set()
    problems = sorted({str(r.get("problem")) for r in rows})
    for r in rows:
        prob = str(r.get("problem"))
        key = (str(r.get("side")), str(r.get("solver")))
        if r.get("stiff") is True:
            stiff.add(prob)
        if isinstance(r.get("arithmetic"), str) and r.get("arithmetic"):
            arith.setdefault(key, set()).add(r["arithmetic"])
        if key not in shape_of or str(r.get("status")) != "reached":
            continue
        x, y = r.get("nfev"), r.get("achieved_error")
        if not (_finite_pos(x) and _finite_pos(y)):
            continue
        pts.setdefault(prob, {}).setdefault(key, {}).setdefault(
            (float(x), float(y)), []).append(r.get("target_error"))
    allp = [xy for d in pts.values() for s in d.values() for xy in s]
    if not allp:
        return _absent("No row of this class reached its target, so there is no mark to "
                       "draw. The table below lists every row with its status.")
    xs = _log_scale(min(x for x, _y in allp), max(x for x, _y in allp))
    ys = _log_scale(min(y for _x, y in allp), max(y for _x, y in allp))
    panels = []
    for prob in problems:
        title = prob + (" (stiff)" if prob in stiff else "")
        pl = _Axes(_MATCHED_PANEL_W, 212, xs, ys, "rhs evaluations", "achieved error",
                   ml=50, mr=14, mt=24, title=title)
        pl.frame()
        series = pts.get(prob, {})
        if not series:
            pl.parts.append(f'<text x="{_fmt((pl.ml + pl.w - pl.mr) / 2)}" '
                            f'y="{_fmt((pl.mt + pl.h - pl.mb) / 2)}" text-anchor="middle">'
                            "no target reached</text>")
        for key in solvers:
            if key not in series:
                continue
            fill = _SIDE_FILL[key[0]]
            ordered = sorted(series[key])
            if len(ordered) > 1:
                d = " ".join(f"{'M' if i == 0 else 'L'} {_fmt(pl.x(x))} {_fmt(pl.y(y))}"
                             for i, (x, y) in enumerate(ordered))
                pl.parts.append(f'<path d="{d}" fill="none" stroke="{fill}" '
                                'stroke-width="2" stroke-linejoin="round"/>')
            for x, y in ordered:
                met = ", ".join(_num(t) for t in sorted(
                    (t for t in series[key][(x, y)] if _finite_pos(t)), reverse=True))
                tip = (f"{prob}, {_solver_label(key[1])} ({key[0]}): error {_num(y)} after "
                       f"{_num(x)} rhs evaluations"
                       + (f"; meets target {met}" if met else ""))
                pl.parts.append(_mark(shape_of[key], pl.x(x), pl.y(y), fill, tip))
        panels.append(pl.svg(f"{prob}: achieved error against rhs evaluations for the "
                             f"{cls} class at matched accuracy"))
    legend = _key_legend([
        (_SIDE_FILL[key[0]], shape_of[key],
         f"{_solver_label(key[1])} ({key[0]}, "
         + (", ".join(sorted(arith.get(key, ()))) or "arithmetic not stated") + ")")
        for key in solvers if key in shape_of])
    if any(key[0] == "library" for key in shape_of):
        hue = "Blue is ours, orange the library, and the shape names the solver."
    else:
        hue = "Every solver here is ours; the shape names it."
    dropped = len([key for key in solvers if key not in shape_of])
    extra = (f'<p class="note">{dropped} further solvers appear in the table only.</p>'
             if dropped else "")
    return ("<figure><figcaption>Achieved error against rhs evaluations for each problem, "
            "both axes log (a " + _gloss("work-precision", "work-precision")
            + " view). A mark is the cheapest run that met a target; lower left is better. "
            + _esc(hue) + "</figcaption>"
            + legend + _multiples_open(_MATCHED_PANEL_W, _MATCHED_COLS)
            + "".join(panels) + "</div>"
            + '<p class="note">Source: benchmark/results.json, matched_accuracy '
            f"({_esc(cls)} rows); arithmetic per solver in the legend.</p>"
            + extra + "</figure>")


_MATCHED_HOW = (
    "Each row fixes a target for the achieved final-state error and reports what the "
    "solver spent to reach it. A row that did not reach its target stays in the table, "
    "and its status column says so.")


def _median(vals: list[float]) -> float:
    s = sorted(vals)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0


def _matched_sentence(benchmark, rows, cls: str) -> str:
    """The section's verdict, composed from the rows the section already holds.

    The benchmark writes a sentence of its own for each class, but it points at blocks of
    the JSON document that the page never shows. Counting the rows here keeps the words
    and the chart below them on the same numbers. Returns trusted HTML.
    """
    def reached(r) -> bool:
        return str(r.get("status")) == "reached"

    ours = [r for r in rows if str(r.get("side")) == "ours"]
    lib = [r for r in rows if str(r.get("side")) == "library"]
    # A skipped row never ran, so it is not a target a solver missed. Counting the two
    # together reads as a failure rate and understates the class: the implicit page said
    # 44 of 64 where 20 of the 64 were never attempted.
    o_skip = [r for r in ours if str(r.get("status")) == "skipped"]
    o_ran = [r for r in ours if str(r.get("status")) != "skipped"]
    o_hit, l_hit = sum(map(reached, o_ran)), sum(map(reached, lib))
    out = []
    if lib:
        out.append(f"Our solvers reached {o_hit} of their {len(o_ran)} targets and the "
                   f"library solvers {l_hit} of their {len(lib)}; a target is one solver "
                   "on one problem at one accuracy level.")
    else:
        out.append(f"Our solvers reached {o_hit} of their {len(o_ran)} targets; a target is "
                   "one solver on one problem at one accuracy level.")
        classes = benchmark.get("classes") if isinstance(benchmark, dict) else None
        node = classes.get(cls) if isinstance(classes, dict) else None
        reason = node.get("library_absent_reason") if isinstance(node, dict) else None
        out.append(_soft(reason.strip()) if isinstance(reason, str) and reason.strip()
                   else "No library solver runs this class, so every row here is ours.")
    if o_skip:
        n = len(o_skip)
        tail = (f"A further {n} {'row was' if n == 1 else 'rows were'} skipped and never "
                "ran")
        reasons = sorted({str(r.get("reason")).strip() for r in o_skip
                          if isinstance(r.get("reason"), str) and r.get("reason").strip()})
        out.append(tail + (f": {_soft(reasons[0])}." if len(reasons) == 1 else "."))
    cyc = [float(r["analytic_cycles_total"]) for r in o_ran
           if reached(r) and _finite_pos(r.get("analytic_cycles_total"))]
    if cyc:
        out.append(f"Where our runs carry a cycle count, a reached target costs a median of "
                   f"{_num(_median(cyc))} analytic cycles; the table gives each row's cost "
                   "grade.")
    return " ".join(out)


def _matched_section(benchmark, cls: str, extra_keys=()) -> list[str]:
    """The class page's "against real counterparts" section, or an honest absence.

    The per-class verdict and the chart stay visible; the full table, with the rule for
    reading it and the note on cost grades, sits in a fold under them.
    """
    rows = _matched_rows(benchmark, cls)
    parts = ["<h2>Measured against real counterparts, at matched accuracy</h2>"]
    if not rows:
        parts.append(f'<p class="note">{_esc(_MATCHED_ABSENT)}</p>')
        return parts
    parts.append(f"<p>{_matched_sentence(benchmark, rows, cls)}</p>")
    parts.append(_chart_block(_matched_chart(rows, cls)))
    if any(str(r.get("side")) == "library" for r in rows):
        parts.append(f'<p class="note">{_esc(_NEVER_SAME_WORK)}</p>')
    # A class-specific verdict (the stiff subset) is a long generated sentence; it opens
    # the fold, next to the rows it summarises, unless it names a solver this class did
    # not run.
    body = "".join(f"<p>{_esc(t)}</p>" for t in
                   (_three_class_text(benchmark, key) for key in extra_keys)
                   if t and _names_only_these_rows(benchmark, t, rows))
    shown, never_ran = _matched_split(rows)
    body += f"<p>{_esc(_MATCHED_HOW)}</p>" + _matched_table(shown) + _skipped_note(never_ran)
    # The benchmark's note on cost grades describes every class's grades at once. It is
    # printed where it has something to explain here: rows of more than one grade, or
    # library rows, which carry no analytic cycles.
    grades = _three_class_text(benchmark, "cost_grades")
    kinds = {str(r.get("cost_grade")) for r in rows} - {"none", "None", ""}
    if grades and (len(kinds) > 1 or any(str(r.get("side")) == "library" for r in rows)):
        body += f'<p class="note">{_esc(grades)}</p>'
    head = "Every row" if not never_ran else "Every measured row"
    parts.append(_fold(f"{head}, with its cost grade ({len(shown)} "
                       + ("row" if len(shown) == 1 else "rows") + ")", body))
    return parts


# ----------------------------------------------------------------------------
# SDIRK2 on the budget ladder
# ----------------------------------------------------------------------------

def _budget_factor(factor) -> str:
    """A rung's share of the budget in words: "1/1" reads as the full budget, not 1/1."""
    fr = _to_frac(factor)
    if fr is None:
        return f"{_plain(factor)} of the budget"
    if fr == 1:
        return "the full budget"
    if fr == 2:
        return "twice the budget"
    if fr.denominator == 1:
        return f"{fr.numerator} times the budget"
    return f"{_frac(fr)} of the budget"


def _budget_chart(doc: dict) -> str:
    """SDIRK2 error against analytic cycles along the budget ladder, one line per problem.

    A rung that diverged has no error, so it is drawn as a cross on the top edge at its
    cycle count rather than dropped: a ladder that loses its failures reads as a clean
    line that never broke.
    """
    names = sorted(n for n in doc if isinstance(doc[n], dict))
    series: dict[str, list] = {}
    broke: list = []
    budgets: set[float] = set()
    grades: set[str] = set()
    ariths: set[str] = set()
    for name in names:
        e = doc[name]
        if _finite_pos(e.get("budget_cycles")):
            budgets.add(float(e["budget_cycles"]))
        for key, bucket in (("cost_grade", grades), ("arithmetic", ariths)):
            if isinstance(e.get(key), str) and e[key]:
                bucket.add(e[key])
        for rung in e.get("ladder") or []:
            if not isinstance(rung, dict) or not _finite_pos(rung.get("analytic_cycles")):
                continue
            c = float(rung["analytic_cycles"])
            if str(rung.get("status")) == "ok" and _finite_pos(rung.get("error")):
                series.setdefault(name, []).append((c, float(rung["error"]), rung))
            else:
                broke.append((name, c, rung))
    if not series:
        return _absent("The benchmark document carries no budget ladder for these "
                       "problems, so there is no line to draw. The numbers at the budget "
                       "are in the table below.")
    cs = ([c for pts in series.values() for c, _e, _r in pts]
          + [c for _n, c, _r in broke] + sorted(budgets))
    lo2 = math.floor(math.log2(min(cs)))
    hi2 = max(math.ceil(math.log2(max(cs))), lo2 + 1)
    xs = _Scale(2.0 ** lo2, 2.0 ** hi2, True, [2.0 ** k for k in range(lo2, hi2 + 1)],
                fmt=lambda v: f"{int(round(v)):,}")
    errs = [err for pts in series.values() for _c, err, _r in pts]
    ys = _log_scale(min(errs), max(errs))
    drawn = [n for n in names if n in series or any(b[0] == n for b in broke)]
    fill = {name: _series_fill(i) for i, name in enumerate(drawn)}

    def draw(w: int, tips: bool = True) -> str:
        pl = _Axes(w, 320, xs, ys, "analytic cycles for the whole run",
                   "final-state error", ml=52, mr=30, mt=24)
        pl.frame()
        for b in sorted(budgets):
            px = pl.x(b)
            pl.parts.append(f'<line x1="{_fmt(px)}" y1="{pl.mt}" x2="{_fmt(px)}" '
                            f'y2="{pl.h - pl.mb}" stroke="var(--text-3)" stroke-width="1"/>')
            lx = min(max(px, 44.0), pl.w - 44.0)
            pl.parts.append(f'<text x="{_fmt(lx)}" y="{pl.mt - 8}" text-anchor="middle">'
                            "cycle budget</text>")
        for name in drawn:
            pts = sorted(series.get(name, []), key=lambda p: p[0])
            if len(pts) > 1:
                d = " ".join(f"{'M' if i == 0 else 'L'} {_fmt(pl.x(c))} {_fmt(pl.y(err))}"
                             for i, (c, err, _r) in enumerate(pts))
                pl.parts.append(f'<path d="{d}" fill="none" stroke="{fill[name]}" '
                                'stroke-width="2" stroke-linejoin="round"/>')
            for c, err, rung in pts:
                tip = (f"{name}: error {_num(err)} at {int(round(c)):,} cycles, "
                       f"{_plain(rung.get('n'))} steps, {_budget_factor(rung.get('factor'))}")
                pl.parts.append(_mark("circle", pl.x(c), pl.y(err), fill[name],
                                      tip if tips else ""))
        # Crosses wear the legend key's neutral ink, not their problem's hue: the key
        # says "diverged rung" in grey, and the hover text names the problem.
        for name, c, rung in sorted(broke, key=lambda b: (b[0], b[1])):
            tip = (f"{name}: {_plain(rung.get('status'))} at {int(round(c)):,} cycles, "
                   f"{_budget_factor(rung.get('factor'))}, so there is no error to plot")
            pl.parts.append(_mark("cross", pl.x(c), pl.mt + 7, "var(--text-2)",
                                  tip if tips else ""))
        return pl.svg("SDIRK2 error against analytic cycles on the budget ladder, one "
                      "line per stiff problem")

    keys = [(fill[n], "circle", n) for n in drawn]
    if broke:
        keys.append(("var(--text-2)", "cross", "diverged rung"))
    lone = any(len(series.get(n, [])) == 1 for n in drawn)
    return ("<figure><figcaption>SDIRK2 error against the analytic cycles of the whole run, "
            "one line per stiff problem"
            + (" (a lone dot where only one rung finished)" if lone else "")
            + ", both axes log. The vertical line is the shared cycle budget; a cross on "
            "the top edge is a rung that diverged.</figcaption>"
            + _key_legend(keys)
            + _phone_pair(draw(640), draw(430, tips=False))
            + '<p class="note">Source: benchmark/results.json, implicit_budget. '
            "Arithmetic: " + (_soft(", ".join(sorted(ariths))) or "not stated")
            + ". Cycle grade: " + (_soft(", ".join(sorted(grades))) or "not stated")
            + ".</p></figure>")


def _implicit_budget_table(benchmark) -> list[str]:
    """SDIRK2 on the stiff application problems at the shared cycle budget and around it:
    the ladder as a chart, then the numbers at the budget itself in a fold."""
    if not isinstance(benchmark, dict):
        return []
    doc = benchmark.get("implicit_budget")
    if not isinstance(doc, dict) or not doc:
        return []
    out = ["<h2>SDIRK2 error as the cycle budget grows</h2>",
           _chart_block(_budget_chart(doc))]
    rows = ['<div class="scroll"><table><tr><th>problem</th>'
            '<th class="num">steps at budget</th><th class="num">cycles per step</th>'
            '<th class="num">error at budget</th><th>status</th></tr>']
    for name in sorted(doc):
        e = doc[name] if isinstance(doc[name], dict) else {}
        err = e.get("error_at_budget", e.get("error"))
        rows.append(f"<tr><td>{_soft(name)}</td>"
                    f'<td class="num">{_num(e.get("steps_at_budget"))}</td>'
                    f'<td class="num">{_num(e.get("cycles_per_step"))}</td>'
                    f'<td class="num">{_num(err)}</td>'
                    f"<td>{_soft(e.get('status_at_budget', 'n/a'))}</td></tr>")
    rows.append("</table></div>")
    out.append(_fold("The numbers at the budget", "".join(rows)))
    return out


# ----------------------------------------------------------------------------
# lane elites: the one lane document a page may publish numbers from
# ----------------------------------------------------------------------------

# rk-work/{lane}_archive/elites.json is on the traceability list. The per-day lane
# records in the same directory are not, and no page reads them: they are a search log,
# and their per-candidate numbers invite exactly the comparison with a scored archive
# record that must never be made. The elites document is bounded by its own cap, ordered
# by a total order it states in its own "rule" field, and it carries the sentence saying
# what it ranked and over what, so the page renders that field rather than restating it
# and the two cannot drift apart.

_LANE_ELITES_METRIC = (
    "The archive fixes a cycle budget and measures the error a method reaches. The lane "
    "fixes an accuracy target on the axis-T ladder and measures the modeled cycles a "
    "candidate needs to reach it, so neither number converts into the other.")

# "Not scored" is said once per page, by the "What these numbers are not" list that
# follows this section, so the lane paragraph carries only the order-verification half.
_LANE_ELITES_UNSCORED = (
    "The pinned order-condition checker never saw these coefficients, so none is "
    "order-verified.")

# One sentence per lane about the arithmetic behind its cycle number, because the two
# lanes differ and a page-level claim about arithmetic would be false on one of them.
# The implicit reason (no Q15 LU factorization) is in the page's At a glance list.
_LANE_ARITHMETIC: dict[str, str] = {
    "implicit": "Every run is float64.",
    "adaptive": "Every run is float64.",
}

_LANE_ELITES_ABSENT = (
    "{path} has not been written in this work directory, so there is no ranking to "
    "show. The lane search writes it at the end of a cycle, and nothing is inferred in "
    "its place.")

_LANE_ELITES_DAMAGED = (
    "{path} exists but does not read as the document this page expects, so nothing from "
    "it is stated here. A file that does not parse, or has no list of ranked entries, is "
    "reported as unreadable rather than half rendered.")

_LANE_ELITES_EMPTY = (
    "{path} is written and lists no ranked candidate, so this section shows no count "
    "and no table. A zero would read as a measurement that came back empty, which is a "
    "different claim from having measured nothing yet.")

_LANE_ELITES_REFILL = (
    "A fresh work directory starts here, and the lane comes back here for a cycle or two "
    "after its code changes: a candidate counts as measured only under the code hash "
    "that measured it, so the ranking re-fills as the lane re-enumerates. Nothing is "
    "carried forward from an earlier hash.")

_LANE_COUNTS_ABSENT = (
    "The document does not state how many candidates were measured or ranked, so those "
    "counts are left out rather than guessed from the rows it lists.")

_LANE_RANK_NOTE = (
    "Rank is the position the document gives under the rule above. Each row shows the "
    "candidate's shape, the median modeled cycles it needed at the elite target, how "
    "many problems it reached that target on, and the cost basis its cycles were priced "
    "on.")

# The shape columns each lane's page shows, as (label, (group, key)) pairs read out of
# an entry's nested blocks. Held as data so both pages render one table renderer and a
# lane cannot quietly grow a column the other lane's tests never see.
_LANE_SHAPE_COLS: dict[str, tuple[tuple[str, tuple[str, str]], ...]] = {
    "implicit": (
        ("gamma", ("method", "gamma")),
        ("a21", ("method", "a21")),
        ("newton iterations", ("method", "newton_iters")),
        ("jacobian", ("method", "jacobian")),
        ("A-stable", ("stability", "a_stable")),
    ),
    "adaptive": (
        ("order", ("method", "order")),
        ("embedded order", ("method", "order_hat")),
        ("stages", ("method", "stages")),
        ("safety", ("controller", "safety")),
        ("alpha", ("controller", "alpha")),
        ("beta", ("controller", "beta")),
    ),
}


def _lane_int(node, key):
    """One non-negative integer out of a document node, or None for anything else.

    A string, a null or a float is what a damaged document puts where a count belongs.
    A count that did not validate is reported as absent in words rather than coerced,
    because a coerced count is indistinguishable on the page from a measured one.
    """
    if not isinstance(node, dict):
        return None
    v = node.get(key)
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        return None
    return v


def _lane_elites_view(cls: str) -> dict:
    """One lane's elites document reduced to what a page may render, plus its state.

    The state is one of absent (no file), damaged (a file that does not parse or that
    carries no list of entries), empty (a document that ranks nothing) and ranked. Each
    renders as its own words; none of them renders as a zero.
    """
    view = {"state": "absent", "meta": {}, "rows": [], "rule": "", "statement": ""}
    if cls not in LANE_CLASSES:
        return view
    if not _lane_elites_path(cls).exists():
        return view
    doc = _load_lane_archive(cls)
    if doc is None:
        view["state"] = "damaged"
        return view
    raw = doc.get("elites")
    if not isinstance(raw, list):
        view["state"] = "damaged"
        return view
    rows = [e for e in raw if isinstance(e, dict)]
    meta = doc.get("_meta")
    view["meta"] = meta if isinstance(meta, dict) else {}
    view["rows"] = rows
    # Text only. A rule or a statement that came through as a number is not a sentence,
    # and rendering str() of it would put "5." on the page where the order should be.
    rule, statement = doc.get("rule"), doc.get("statement")
    view["rule"] = rule.strip() if isinstance(rule, str) else ""
    view["statement"] = statement.strip() if isinstance(statement, str) else ""
    view["state"] = "ranked" if rows else "empty"
    return view


def _lane_field(entry, group: str, key: str):
    node = entry.get(group) if isinstance(entry, dict) else None
    return node.get(key) if isinstance(node, dict) else None


def _lane_reached(entry, num_key: str, den_key: str) -> str:
    """"r of n" for a pair of counts, or "n/a" when either half did not validate."""
    score = entry.get("score") if isinstance(entry, dict) else None
    r = _lane_int(score, num_key)
    n = _lane_int(score, den_key)
    if r is None or n is None:
        return "n/a"
    return f"{r} of {n}"


def _lane_dl(rows) -> str:
    return ('<dl class="meta">\n'
            + "\n".join(f"<dt>{_esc(k)}</dt><dd>{v}</dd>" for k, v in rows) + "\n</dl>")


def _lane_elites_table(cls: str, rows) -> str:
    """The ranked entries, in the order the document ranked them.

    Every value comes out of the document, so every cell goes through _st_cell, which
    routes text through the vocabulary pass. Rank is the row's position in the list and
    is generated here, not read.
    """
    cols = _LANE_SHAPE_COLS.get(cls, ())
    head = ('<tr><th class="num">rank</th><th>candidate</th>'
            + "".join(f"<th>{_esc(label)}</th>" for label, _p in cols)
            + '<th class="num">median cycles at target</th>'
            '<th class="num">problems at target</th>'
            '<th class="num">targets reached</th><th>cost basis</th></tr>')
    body = []
    for i, e in enumerate(rows, start=1):
        shape = "".join(f"<td>{_st_cell(_lane_field(e, g, k))}</td>"
                        for _label, (g, k) in cols)
        key = str(e.get("key") or "")[:16]
        score = e.get("score") if isinstance(e.get("score"), dict) else {}
        at_target = _lane_reached(e, "reached_at_elite_target",
                                  "problems_at_elite_target")
        reached = _lane_reached(e, "targets_reached", "targets_total")
        body.append(
            f'<tr><td class="num">{i}</td>'
            f'<td class="hash">{_soft(key) if key else "n/a"}</td>'
            + shape
            + f'<td class="num">{_st_cell(score.get("median_cycles_at_target"))}</td>'
            f'<td class="num">{_esc(at_target)}</td>'
            f'<td class="num">{_esc(reached)}</td>'
            f'<td>{_st_cell(e.get("cost_basis"))}</td></tr>')
    return ('<div class="scroll"><table>\n' + head + "\n"
            + "\n".join(body) + "\n</table></div>")


def _lane_cost_bases(meta, rows) -> str:
    """The document's own account of every cost basis the rendered rows were priced on.

    Names come from the rows and from the document's default, sorted, so the block is a
    function of what is on the page. A basis the document names and does not describe
    says that, rather than leaving the reader to assume a grade.
    """
    names = {str(e.get("cost_basis")) for e in rows
             if isinstance(e.get("cost_basis"), str) and e.get("cost_basis")}
    default = meta.get("cost_basis") if isinstance(meta, dict) else None
    if isinstance(default, str) and default:
        names.add(default)
    if not names:
        return ""
    bases = meta.get("cost_bases") if isinstance(meta, dict) else None
    body = ["<h3>How this lane prices a cycle</h3>"]
    for name in sorted(names):
        node = bases.get(name) if isinstance(bases, dict) else None
        body.append(f'<p class="mono"><strong>{_soft(name)}</strong></p>')
        if isinstance(node, dict) and node:
            body.append('<dl class="meta">\n' + "\n".join(
                f"<dt>{_soft(k)}</dt><dd>{_st_cell(node[k])}</dd>"
                for k in sorted(node)) + "\n</dl>")
        else:
            body.append('<p class="note">The document names this cost basis and does '
                        "not describe it, so nothing is stated here about what it "
                        "prices and what it leaves out.</p>")
    return "\n".join(body)


def _lane_freight(cls: str) -> str:
    """The metric, the lane's arithmetic and the two negatives, as one paragraph.

    Rendered in every state the document can be in, because they are properties of how
    the lane measures, not of what the document holds today.
    """
    text = " ".join(t for t in (_LANE_ELITES_METRIC, _LANE_ARITHMETIC.get(cls, ""),
                                _LANE_ELITES_UNSCORED) if t)
    # The ladder links the glossary entry that defines the metric it belongs to.
    return ("<p>" + _esc(text).replace(
        "axis-T ladder", _gloss("cycles-to-tolerance", "axis-T ladder"), 1) + "</p>")


def _ladder_text(benchmark, meta) -> str:
    """The axis-T ladder's targets as exact fractions, or "" when no admitted document
    states them.

    sitegen does not import lanesearch, so the values are read from benchmark/results.json,
    whose matched-accuracy targets come from the same secondpass.TARGETS the lanes use.
    They are printed only when the lane's own elite target is one of them, so a benchmark
    run against a different ladder cannot put the wrong targets under a lane.
    """
    scope = benchmark.get("three_class_scope") if isinstance(benchmark, dict) else None
    raw = scope.get("targets") if isinstance(scope, dict) else None
    if not isinstance(raw, list) or not raw or not all(_finite_pos(t) for t in raw):
        return ""
    fracs = sorted({_to_frac(t) for t in raw}, reverse=True)
    elite = _to_frac(meta.get("elite_target")) if isinstance(meta, dict) else None
    if None in fracs or elite is None or elite not in fracs:
        return ""
    names = [_frac(f) for f in fracs]
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f"targets of {listed} final-state error (benchmark/results.json)"


def _lane_bases_text(rows) -> str:
    bases = sorted({str(e.get("cost_basis")) for e in rows
                    if isinstance(e.get("cost_basis"), str) and e.get("cost_basis")})
    return _soft(", ".join(bases)) if bases else "a basis the entries do not state"


def _lane_strip_chart(rows, rel: str) -> str:
    """Implicit lane elites: a21 against the median cycles at the elite target.

    A strip rather than a scatter of two measures, because the document ranks entries
    that differ in a21 and in Jacobian policy, and those are the two things a reader
    can see move the cycle count. Policies at one a21 sit side by side.
    """
    pts = []
    for i, e in enumerate(rows, start=1):
        a21 = _frac_float(_lane_field(e, "method", "a21"))
        score = e.get("score") if isinstance(e.get("score"), dict) else {}
        med = score.get("median_cycles_at_target")
        if a21 is None or not _finite_num(med):
            continue
        pol = _lane_field(e, "method", "jacobian")
        pts.append((a21, float(med), pol if isinstance(pol, str) and pol else "not stated",
                    i, e))
    if not pts:
        return _absent("No ranked entry carries both an a21 value and a median cycle "
                       "count, so there is nothing to plot.")
    fixed = ("analytic", "finite_difference")
    policies = sorted({p[2] for p in pts},
                      key=lambda s: (fixed.index(s) if s in fixed else len(fixed), s))
    fill = {p: _series_fill(i, 3) for i, p in enumerate(policies)}
    lo, hi = min(p[0] for p in pts), max(p[0] for p in pts)
    pad = (hi - lo) * 0.03       # dodged marks at either end stay inside the frame
    xs = _lin_scale(lo - pad, hi + pad, n=8)
    # The y domain reaches at least 2% either side of the middle value, so a gap of a
    # few cycles is drawn at its size rather than stretched over the whole plot height.
    ylo, yhi = min(p[1] for p in pts), max(p[1] for p in pts)
    ypad = max(abs(ylo + yhi) / 2 * 0.02, (yhi - ylo) * 0.05)
    ys = _lin_scale(ylo - ypad, yhi + ypad, n=4)
    def draw(w: int, tips: bool = True) -> str:
        pl = _Axes(w, 280, xs, ys, "a21", "median cycles at the elite target", ml=58,
                   mr=18, mt=12)
        pl.frame()
        for a21, med, pol, rank, e in sorted(pts, key=lambda p: (p[0], p[2], p[3])):
            dx = (policies.index(pol) - (len(policies) - 1) / 2) * 6
            tip = (f"rank {rank}, {str(e.get('key') or '')[:16]}: gamma "
                   f"{_plain(_lane_field(e, 'method', 'gamma'))}, a21 "
                   f"{_plain(_lane_field(e, 'method', 'a21'))}, {pol.replace('_', ' ')} "
                   f"Jacobian, median {_num(med)} cycles at the elite target")
            pl.parts.append(_mark("circle", pl.x(a21) + dx, pl.y(med), fill[pol],
                                  tip if tips else ""))
        return pl.svg("Implicit lane elites: a21 against median cycles at the elite "
                      "target, by Jacobian policy")

    gammas = {_lane_field(e, "method", "gamma") for *_x, e in pts}
    one = ""
    if len(gammas) == 1 and isinstance(next(iter(gammas)), str):
        one = f" Every plotted entry has gamma {next(iter(gammas))}."
    # Name the cycle levels in words when there are few of them, with the a21 span each
    # one covers, so a reader can tell what the two lines of dots are. It is a long
    # sentence about where the marks landed rather than about how to read them, so it
    # folds under the figure instead of standing in the caption.
    levels: dict[tuple[str, float], list] = {}
    for a21, med, pol, _rank, e in pts:
        levels.setdefault((pol, med), []).append(_to_frac(_lane_field(e, "method", "a21")))
    said: list[str] = []
    if len(levels) <= 6:
        for pol in policies:
            bits = []
            for (p, med) in sorted(k for k in levels if k[0] == pol):
                span = [_plain_frac(f) for f in sorted(f for f in levels[(p, med)]
                                                       if f is not None)]
                where = ("" if not span else f" at a21 {span[0]}" if span[0] == span[-1]
                         else f" at a21 {span[0]} to {span[-1]}")
                bits.append(f"{_num(med)} cycles{where}")
            said.append(f"{pol.replace('_', ' ')} entries need " + " and ".join(bits))
    text = "; ".join(said)
    where_fold = ("" if not text else
                  _fold("Where the cycle levels sit",
                        "<p>" + _soft(text[:1].upper() + text[1:] + ".") + "</p>"))
    n = len(pts)
    return (f"<figure><figcaption>The {n} {'entry' if n == 1 else 'entries'} plotted here, "
            "each at its a21 against the median modeled cycles it needed at the elite "
            "target, by Jacobian policy." + _soft(one)
            + "</figcaption>"
            + _key_legend([(fill[p], "circle", f"{p.replace('_', ' ')} Jacobian")
                           for p in policies])
            # One drawing here, unlike the four charts that carry a phone variant: this
            # is the largest chart on the site and the one whose marks already sit
            # inside a phone viewport, so a second copy would cost the implicit page
            # about 4 KB to move a few dots.
            + draw(640)
            + f'<p class="note">Source: {_esc(rel)}. float64 runs, cycles priced on '
            + _lane_bases_text([p[4] for p in pts]) + "; not order-verified.</p>"
            + where_fold + "</figure>")


def _lane_scatter_chart(rows, rel: str) -> str:
    """Adaptive lane elites: median cycles at the elite target against best error.

    Entries that land on one point share a mark whose hover text lists their ranks, so
    thirty-two entries on a few distinct points do not print as one heavy blot.
    """
    groups: dict[tuple[float, str], list] = {}
    for i, e in enumerate(rows, start=1):
        score = e.get("score") if isinstance(e.get("score"), dict) else {}
        med, err = score.get("median_cycles_at_target"), score.get("best_achieved_error")
        if not (_finite_num(med) and _finite_pos(err)):
            continue
        groups.setdefault((float(med), f"{float(err):.6g}"), []).append((i, e, float(err)))
    if not groups:
        return _absent("No ranked entry carries both a median cycle count and a best "
                       "achieved error, so there is nothing to plot.")
    meds = [k[0] for k in groups]
    errs = [m[0][2] for m in groups.values()]
    pad = (max(meds) - min(meds)) * 0.03
    xs = _lin_scale(min(meds) - pad, max(meds) + pad, n=5)
    ys = _log_scale(min(errs), max(errs))
    pl = _Axes(640, 300, xs, ys, "median cycles at the elite target", "best achieved error",
               ml=52, mr=20, mt=12)
    pl.frame()
    for key in sorted(groups):
        members = groups[key]
        ranks = ", ".join(str(i) for i, _e, _err in members)
        tip = (f"rank {ranks}: median {_num(key[0])} cycles at the elite target, best "
               f"achieved error {_num(members[0][2])}"
               + (f" ({len(members)} entries)" if len(members) > 1 else ""))
        pl.parts.append(_mark("circle", pl.x(key[0]), pl.y(members[0][2]), "var(--s1)", tip))
    plotted = [e for m in groups.values() for _i, e, _err in m]
    gains = {tuple(_lane_field(e, "controller", k) for k in ("alpha", "beta", "safety"))
             for e in plotted}
    one = ""
    if len(gains) == 1 and all(isinstance(v, str) for v in next(iter(gains))):
        a, b, s = next(iter(gains))
        one = f" Every plotted entry uses controller gains alpha {a}, beta {b} and safety {s}."
    n = len(plotted)
    return (f"<figure><figcaption>The {n} {'entry' if n == 1 else 'entries'} plotted here, "
            "each at its median modeled cycles at the elite target against its best "
            "achieved error (log axis); left is cheaper and lower is more accurate."
            + _soft(one)
            + "</figcaption>"
            + pl.svg("Adaptive lane elites: median cycles at the elite target against best "
                     "achieved error")
            + f'<p class="note">Source: {_esc(rel)}. float64 runs, cycles priced on '
            + _lane_bases_text(plotted) + "; not order-verified.</p></figure>")


def _lane_elites_section(cls: str, benchmark=None) -> list[str]:
    """One lane's ranked elites, rendered honestly in every state the document can be in.

    The freight paragraph comes first on every render. A ranked document then gets its
    counts, its provenance and a chart; the entries themselves, the rule that ordered
    them and the document's account of its cost basis sit in one fold. The section has
    no h2 of its own after the heading, so it ends where the next section starts.
    """
    view = _lane_elites_view(cls)
    rel = f"rk-work/{cls}_archive/elites.json"
    parts = ["<h2>Lane elites, ranked by cycles to tolerance</h2>", _lane_freight(cls)]

    state = view["state"]
    if state in ("absent", "damaged"):
        text = _LANE_ELITES_ABSENT if state == "absent" else _LANE_ELITES_DAMAGED
        parts.append(f"<p>{_esc(text.format(path=rel))}</p>")
        if state == "absent":
            parts.append(f'<p class="note">{_esc(_LANE_ELITES_REFILL)}</p>')
        return parts

    meta = view["meta"]
    rows = view["rows"]
    dl_rows: list[tuple[str, str]] = [
        ("source", f'<span class="mono">{_esc(rel)}</span>')]
    target_key = meta.get("elite_target_key")
    target = meta.get("elite_target")
    tail = " final-state error"
    if isinstance(target_key, str) and target_key.strip():
        dl_rows.append(("elite target", _soft(target_key.strip()) + _esc(tail)))
    elif isinstance(target, (int, float)) and not isinstance(target, bool):
        dl_rows.append(("elite target", _num(target) + _esc(tail)))
    ladder = _ladder_text(benchmark, meta)
    if ladder:
        dl_rows.append(("axis-T ladder", _esc(ladder)))
    code_hash = meta.get("lanesearch_code_hash")
    if isinstance(code_hash, str) and code_hash.strip():
        dl_rows.append(("lane search code hash",
                        f'<span class="hash">{_soft(code_hash.strip())}</span>'))
    else:
        dl_rows.append(("lane search code hash",
                        _esc("not stamped in this document, so the code these were "
                             "measured under is not stated here")))
    # A cycle number of 0 is the default build_elites uses when it ranked nothing, and
    # no real cycle is 0. Printing it would put a bare zero in the one section whose
    # own prose argues against bare zeros.
    gen_cycle = _lane_int(meta, "generated_cycle")
    if gen_cycle is not None and gen_cycle <= 0:
        gen_cycle = None
    gen_ts = meta.get("generated_ts")
    if gen_cycle is not None and isinstance(gen_ts, str) and gen_ts.strip():
        dl_rows.append(("document written",
                        f"cycle {_num(gen_cycle)}, {_esc(_ct(gen_ts))}"))
    elif gen_cycle is not None:
        dl_rows.append(("document written", f"cycle {_num(gen_cycle)}"))

    if view["rule"]:
        rule = view["rule"]
        stop = "" if rule.endswith((".", "!", "?")) else "."
        rule_note = ('<p class="note">Ranking rule, quoted from the document: '
                     + _soft(rule) + stop + "</p>")
    else:
        rule_note = ('<p class="note">The document states no ranking rule, so the order '
                     "of the entries below is the order it wrote them in and this page "
                     "does not say what that order means.</p>")

    if state == "empty":
        parts.append(rule_note)
        parts.append(f"<p>{_esc(_LANE_ELITES_EMPTY.format(path=rel))}</p>")
        parts.append(f'<p class="note">{_esc(_LANE_ELITES_REFILL)}</p>')
        parts.append(_lane_dl(dl_rows))
        return parts

    n_measured = _lane_int(meta, "n_measured")
    n_ranked = _lane_int(meta, "n_ranked")
    cap = _lane_int(meta, "elite_cap")
    k = len(rows)
    if n_ranked is not None and k >= n_ranked:
        listed = "all of them are listed below"
    elif cap is not None and k == cap:
        listed = f"the leading {k} are listed below, the document's cap"
    else:
        listed = f"the leading {k} are listed below"
    cards = []
    if n_measured is not None:
        cards.append(("candidates measured", _count(n_measured),
                      "under the lane search code hash below"))
    if n_ranked is not None:
        cards.append(("candidates ranked", _count(n_ranked),
                      f"the subset this document put in order; {listed}"))
    if cards:
        parts.append(_cards(cards))
    if n_measured is None or n_ranked is None:
        parts.append(f'<p class="note">{_esc(_LANE_COUNTS_ABSENT)}</p>')
    parts.append(_lane_dl(dl_rows))
    chart = (_lane_strip_chart(rows, rel) if cls == "implicit"
             else _lane_scatter_chart(rows, rel))
    parts.append(_chart_block(chart))
    # The document's own summary sentence is not quoted: it rounds the leading median
    # (3623.5 prints as 3624), which the table beside it shows exactly, and the cards
    # and the table already carry every number it states.
    body = [rule_note, f"<p>{_esc(_LANE_RANK_NOTE)}</p>", _lane_elites_table(cls, rows)]
    bases = _lane_cost_bases(meta, rows)
    if bases:
        body.append(bases)
    parts.append(_fold(f"The {k} listed {'entry' if k == 1 else 'entries'}, the ranking "
                       "rule and how the lane prices a cycle", "\n".join(body)))
    return parts


# ----------------------------------------------------------------------------
# the implicit class
# ----------------------------------------------------------------------------

_IMPLICIT_CLASS = (
    "Implicit methods solve an equation at every stage, paying for a Jacobian and Newton "
    "iterations to stay stable on stiff problems. A scored record has no field for them, "
    "so the run searches two-stage SDIRK methods in a lane of its own: cycles set aside "
    "for this class that write an unscored archive. The lane reports the modeled cycles "
    "a method needs to reach an accuracy target.")


def _stiff_gap(validation) -> tuple[int, int] | None:
    """(stiff problems no discovered method finishes, stiff problems), or None."""
    if not isinstance(validation, dict):
        return None
    verdicts = validation.get("verdicts")
    verdicts = verdicts if isinstance(verdicts, dict) else {}
    stiff = [p for p in (validation.get("problems") or [])
             if isinstance(p, dict) and p.get("stiff")]
    no_fin = verdicts.get("stiff_problems_with_no_discovered_finisher")
    total = verdicts.get("stiff_problems_total")
    if (stiff and isinstance(no_fin, int) and isinstance(total, int)
            and not isinstance(no_fin, bool) and not isinstance(total, bool)):
        return no_fin, total
    return None


def _stability_chart(docs) -> str:
    """Every gamma the dyadic scan tried against |R(inf)|, A-stable or not.

    R at infinity is the factor one step applies to an infinitely stiff mode, and it
    depends on gamma alone, so each gamma is one mark however many a21 values the scan
    paired it with.
    """
    rows, seen = [], set()
    for _e, d in docs:
        for r in d.get("rows") or []:
            if not isinstance(r, dict):
                continue
            g, rv = r.get("gamma_float"), r.get("r_at_infinity_float")
            if not (_finite_num(g) and _finite_num(rv)):
                continue
            key = (str(r.get("gamma")), float(rv), r.get("a_stable") is True)
            if key in seen:
                continue
            seen.add(key)
            rows.append((float(g), abs(float(rv)), r.get("a_stable") is True, r))
    if not rows:
        return _absent("The stability scan has no row with both a gamma and an R at "
                       "infinity yet, so there is nothing to plot.")
    pos = [a for _g, a, _s, _r in rows if a > 0]
    ys = _log_scale(min(pos) if pos else 0.1, max(pos + [1.0]) if pos else 1.0,
                    fmt=_lin_label)
    xs = _lin_scale(0.0, max(g for g, *_x in rows), n=8)
    pl = _Axes(640, 320, xs, ys, "gamma, the diagonal value", "|R(∞)|", ml=52, mr=16,
               mt=12)
    pl.frame()
    if ys.lo < 1.0 < ys.hi:
        py = pl.y(1.0)
        pl.parts.append(f'<line x1="{pl.ml}" y1="{_fmt(py)}" x2="{pl.w - pl.mr}" '
                        f'y2="{_fmt(py)}" stroke="var(--text-3)" stroke-width="1"/>')
        pl.parts.append(f'<text x="{pl.w - pl.mr - 4}" y="{_fmt(py - 6)}" '
                        'text-anchor="end">|R(∞)| = 1</text>')
    for g, a, stable, r in sorted(rows, key=lambda t: (t[0], t[1], t[2])):
        tip = (f"gamma {_plain(r.get('gamma'))}: R(∞) = {_plain(r.get('r_at_infinity'))}, "
               + ("A-stable" if stable else "not A-stable")
               + f", measured order {_plain(r.get('measured_order'))}")
        pl.parts.append(_mark("circle" if stable else "cross", pl.x(g),
                              pl.y(a if a > 0 else ys.lo),
                              "var(--s1)" if stable else "var(--s2)", tip))
    tail = ""
    if pos and len(pos) == len(rows):
        low = min(rows, key=lambda t: (t[1], t[0]))
        tail = (" None reaches 0, which " + _gloss("l-stability", "L-stability")
                + " needs; the smallest is " + _num(low[1]) + ", at gamma "
                + _soft(_plain(low[3].get("gamma"))) + ".")
    return ("<figure><figcaption>Each gamma the dyadic scan tried against |R(∞)|, "
            "the factor one step applies to a very stiff mode (log axis). Below 1 the mode "
            "is damped." + tail + "</figcaption>"
            + _key_legend([("var(--s1)", "circle", "A-stable"),
                           ("var(--s2)", "cross", "not A-stable")])
            + pl.svg("Stability function at infinity against gamma for every SDIRK the "
                     "dyadic scan tried")
            + '<p class="note">Source: side-track job sdirk.gamma_dyadic_scan, '
            f"{len(docs)} ledger {'point' if len(docs) == 1 else 'points'}, "
            f"{len(rows)} distinct {'gamma' if len(rows) == 1 else 'gammas'} plotted. "
            "Arithmetic: " + _arith_of(docs) + ".</p></figure>")


# Where an SDIRK2 step's cycles go, grouped so a stack has four parts at most. A term
# the artifact names and this list does not lands in "other stage arithmetic", so a new
# term shows up on the page rather than vanishing from it.
_JAC_GROUPS: tuple[tuple[tuple[str, ...] | None, str], ...] = (
    (("newton_iterations",), "Newton iterations"),
    (("lu_factor",), "LU factorization"),
    (None, "other stage arithmetic"),
    (("fd_jacobian_arith",), "finite-difference Jacobian"),
)
_JAC_POLICIES = (("analytic", "analytic"), ("finite_difference", "finite difference"))


def _jacobian_chart(docs) -> str:
    """Modeled cycles per SDIRK2 step, stacked by where they go, for each Jacobian policy."""
    named = {n for names, _l in _JAC_GROUPS if names for n in names}
    bars = []
    models: set[str] = set()
    for e, d in docs:
        cyc = d.get("cycles") if isinstance(d.get("cycles"), dict) else {}
        prob = str(d.get("problem") or e.get("key") or "")
        for key, label in _JAC_POLICIES:
            node = cyc.get(key)
            if not isinstance(node, dict) or not isinstance(node.get("terms"), dict):
                continue
            terms = {str(k): float(v) for k, v in node["terms"].items()
                     if _finite_num(v) and v >= 0}
            segs = []
            for gi, (names, glabel) in enumerate(_JAC_GROUPS):
                ks = (sorted(k for k in terms if k not in named) if names is None
                      else [k for k in names if k in terms])
                v = sum(terms[k] for k in ks)
                if v > 0:
                    segs.append((v, _SERIES4[gi], glabel, ks))
            if segs:
                bars.append((prob, label, segs, node.get("f_evals_per_step")))
            if isinstance(node.get("model"), str) and node["model"]:
                models.add(node["model"])
    if not bars:
        return _absent("The Jacobian-cost job has no measured point with a cycle "
                       "breakdown yet, so there is nothing to plot.")
    vmax = max(sum(s[0] for s in segs) for _p, _l, segs, _f in bars)
    xs = _lin_scale(0.0, vmax, n=5)
    w, ml, mr, mt, head_h, row_h, bar_h = 640, 132, 40, 6, 22, 26, 18
    span = w - ml - mr
    px_per = span / (xs.hi - xs.lo)
    body: list[str] = []
    y = mt
    for prob in sorted({b[0] for b in bars}):
        body.append(f'<text x="2" y="{_fmt(y + 14)}">{_soft(_clip(prob, (w - 4) / 7.0))}</text>')
        y += head_h
        for bprob, label, segs, fevals in bars:
            if bprob != prob:
                continue
            by = y + (row_h - bar_h) / 2
            body.append(f'<text x="{ml - 8}" y="{_fmt(by + bar_h / 2 + 3.5)}" '
                        f'text-anchor="end">{_esc(label)}</text>')
            total = sum(s[0] for s in segs)
            stack = [(v, fill, f"{prob}, {label} Jacobian: {glabel}, {_num(v)} cycles "
                      f"per step ({', '.join(ks)})") for v, fill, glabel, ks in segs]
            # The rhs evaluations are outside the count, so they ride on the hover text
            # of the bar's last segment rather than in a second printed number.
            v, fill, tip = stack[-1]
            stack[-1] = (v, fill, f"{tip}; {_num(total)} in all, plus {_plain(fevals)} rhs "
                         "evaluations per step that the count leaves out")
            drawn, end = _stack_bar(ml, by, bar_h, stack, px_per)
            body.extend(drawn)
            body.append(_end_label(end, by + bar_h / 2 + 3.5, _num(total), w))
            y += row_h
        y += 4
    base = y
    h = int(math.ceil(base + 30))
    svg = (f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
           'aria-label="Modeled cycles per SDIRK2 step by where they go, for each '
           'Jacobian policy on each stiff problem">'
           + "".join(_hbar_frame(xs, ml, span, mt, base)) + "".join(body) + "</svg>")
    used = [gi for gi in range(len(_JAC_GROUPS))
            if any(s[1] == _SERIES4[gi] for _p, _l, segs, _f in bars for s in segs)]
    return ("<figure><figcaption>Modeled cycles per SDIRK2 step by where they go, for "
            "each Jacobian policy. Neither count includes the rhs or Jacobian evaluations "
            "themselves; hover a bar's end for the rhs evaluations per step.</figcaption>"
            + _legend([(_SERIES4[gi], _JAC_GROUPS[gi][1]) for gi in used]) + svg
            + '<p class="note">Source: side-track job sdirk.jacobian_cost, '
            f"{len(docs)} {'point' if len(docs) == 1 else 'points'}; cycle model "
            + (_soft(", ".join(sorted(models))) or "not stated") + ". Arithmetic: "
            + _arith_of(docs) + ".</p></figure>")


def render_implicit(sidetrack: dict | None = None, validation: dict | None = None,
                    benchmark: dict | None = None) -> str:
    """The implicit class, in the skeleton all three class pages share: lead, at a
    glance, cards, charts, matched accuracy, lane elites, the invariants, then folds."""
    entries = _track_entries(sidetrack, "implicit")
    artifacts = (sidetrack.get("artifacts") if isinstance(sidetrack, dict)
                 and isinstance(sidetrack.get("artifacts"), dict) else {})
    parts = [f'<p class="lead">{_esc(_IMPLICIT_CLASS)}</p>', _glance("implicit")]
    gap = _stiff_gap(validation)
    # The number is about explicit tableaus in Q15, which is why this class is measured;
    # the label says whose failure it counts so it cannot read as SDIRK leaving them.
    gap_card = ([("stiff problems with no explicit finisher", f"{gap[0]} of {gap[1]}",
                  "every discovered explicit tableau overflows Q15 there "
                  "(validation/results.json)")]
                if gap else [])
    if entries:
        extra = list(gap_card)
        job = "sdirk.gamma_a21_scan"
        window = _summary_max(_latest_points(entries), "l_stable_gammas", job=job)
        if window is not None:
            extra.append(("L-stable gammas", _num(int(window)),
                          ("none at any " if window == 0 else "most at one ") + job
                          + " point" + _job_arith(entries, artifacts, job)))
        parts.append(_ledger_cards(entries, extra))
    else:
        parts.append(f"<p>{_esc(_NO_POINTS)}</p>")
        if gap_card:
            parts.append(_cards(gap_card))
    if gap is None:
        parts.append('<p class="note">The validation suite has not reported a stiff subset '
                     "yet, so the gap this class addresses is not stated here.</p>")

    if entries:
        parts.append("<h2>Stability of each diagonal value</h2>")
        parts.append(_chart_block(_stability_chart(
            _job_docs(entries, artifacts, "sdirk.gamma_dyadic_scan"))))
    parts.extend(_implicit_budget_table(benchmark))
    if entries:
        parts.append("<h2>Where one step's cycles go</h2>")
        parts.append(_chart_block(_jacobian_chart(
            _job_docs(entries, artifacts, "sdirk.jacobian_cost"))))
    parts.extend(_matched_section(benchmark, "implicit", ("stiff",)))
    parts.extend(_lane_elites_section("implicit", benchmark))
    parts.append(_not_these_numbers())
    if entries:
        parts.append(_points_fold(entries, artifacts))
    parts.append(_further_fold([
        ("rk-work/validation/axes.json", _load_validation_axes() is not None,
         "the cycles-to-tolerance rows for this class, next to the explicit class on "
         "the same accuracy targets"),
        ("rk-work/implicit_archive/YYYY-MM-DD.jsonl", _lane_records_present("implicit"),
         "the lane's per-day search log: one line per candidate the lane enumerated, "
         "ranked against nothing"),
        ("rk-work/schedule/shares.json", _load_shares() is not None,
         "the per-cycle lane log summarised per method class"),
    ]))
    return _page("Implicit methods", "\n".join(parts), active="implicit.html",
                 subtitle="SDIRK methods measured off-archive, and the stiff problems "
                          "that motivate them.")


# ----------------------------------------------------------------------------
# the adaptive class
# ----------------------------------------------------------------------------

_ADAPTIVE_CLASS = (
    "An adaptive method uses an embedded pair to estimate each step's error, and a "
    "step-size controller retries any step that misses the tolerance. A record holds one "
    "set of weights and a fixed step count, so the run searches pairs in a lane of its "
    "own: cycles set aside for this class that write an unscored archive. The lane "
    "reports the modeled cycles a method needs to reach an accuracy target.")


_SWEEP_PANEL_W, _SWEEP_COLS = 256, 4


def _sweep_multiples(docs) -> str:
    """Achieved error against rhs evaluations across the tolerance sweep, one small panel
    per problem on shared axes, four to a row at full width.

    The panels share both axes, so they compare by position; each draws only its own
    problem. The controller's accepted and rejected counts behind every point are in the
    "Every measured point" fold, which is why no second table repeats them here.
    """
    series: dict[str, list] = {}
    pairs: set[str] = set()
    for e, d in docs:
        prob = str(d.get("problem") or e.get("key") or "")
        if isinstance(d.get("pair"), str) and d["pair"]:
            pairs.add(d["pair"])
        pts = []
        for p in d.get("points") or []:
            if not isinstance(p, dict) or str(p.get("status")) != "ok":
                continue
            x, y = p.get("n_fevals"), p.get("achieved_error")
            if _finite_pos(x) and _finite_pos(y):
                pts.append((float(x), float(y), p))
        series[prob] = sorted(pts, key=lambda t: (t[0], t[1]))
    drawn = {k: v for k, v in series.items() if v}
    if not drawn:
        return _absent("The tolerance sweep has no finished run yet, so there is nothing "
                       "to plot.")
    allp = [(x, y) for pts in drawn.values() for x, y, _p in pts]
    xs = _log_scale(min(x for x, _y in allp), max(x for x, _y in allp))
    ys = _log_scale(min(y for _x, y in allp), max(y for _x, y in allp))
    panels = []
    for prob in sorted(series):
        pl = _Axes(_SWEEP_PANEL_W, 184, xs, ys, "rhs evaluations", "achieved error",
                   ml=50, mr=14, mt=22, title=prob)
        pl.frame()
        own = series[prob]
        if not own:
            pl.parts.append(f'<text x="{_fmt((pl.ml + pl.w - pl.mr) / 2)}" '
                            f'y="{_fmt((pl.mt + pl.h - pl.mb) / 2)}" text-anchor="middle">'
                            "no finished run</text>")
        if len(own) > 1:
            d = " ".join(f"{'M' if i == 0 else 'L'} {_fmt(pl.x(x))} {_fmt(pl.y(y))}"
                         for i, (x, y, _p) in enumerate(own))
            pl.parts.append(f'<path d="{d}" fill="none" stroke="var(--s1)" '
                            'stroke-width="2" stroke-linejoin="round"/>')
        for x, y, p in own:
            tip = (f"{prob}: tolerance {_plain(p.get('tol'))}, error {_num(y)} after "
                   f"{_num(x)} rhs evaluations, {_plain(p.get('n_rejected'))} rejected steps")
            pl.parts.append(_mark("circle", pl.x(x), pl.y(y), "var(--s1)", tip))
        panels.append(pl.svg(f"{prob}: achieved error against rhs evaluations across the "
                             "tolerance sweep"))
    pair = (" Pair: " + _soft(", ".join(sorted(pairs))) + "." if pairs else "")
    return ("<figure><figcaption>Achieved error against rhs evaluations as the tolerance "
            "tightens, one panel per problem on shared axes, both log." + pair
            + "</figcaption>"
            + _multiples_open(_SWEEP_PANEL_W, _SWEEP_COLS) + "".join(panels) + "</div>"
            + '<p class="note">Source: side-track job adaptive.suite_sweep, '
            f"{len(docs)} {'point' if len(docs) == 1 else 'points'}. Arithmetic: "
            + _arith_of(docs) + ".</p></figure>")


def _param_frac(doc, key: str):
    """One controller gain as an exact rational, from the point's params or the doc."""
    params = doc.get("params") if isinstance(doc.get("params"), dict) else {}
    for v in (params.get(key), doc.get(key + "_exact"), doc.get(key)):
        f = _to_frac(v)
        if f is not None:
            return f
    return None


def _heat_up(v: float, lo: float, hi: float) -> int:
    """Sequential step 1..7 on a log scale, deeper for a larger value."""
    if hi <= lo:
        return 4
    a = (math.log10(v) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))
    return max(1, min(7, 1 + int(round(a * 6))))


def _gain_map(docs) -> str:
    """The share of attempts the controller rejected, for each (alpha, beta) pair.

    A heat grid with the value printed in every cell, so the color only has to carry
    the pattern. A point where no run finished says so in its cell rather than printing
    a share nobody measured.
    """
    cells: dict[tuple, tuple[dict, dict]] = {}
    for e, d in docs:
        a, b = _param_frac(d, "alpha"), _param_frac(d, "beta")
        if a is not None and b is not None:
            cells[(a, b)] = (e, d)
    if not cells:
        return _absent("The controller-gain job has no measured point with both gains "
                       "stated, so there is no map to draw.")

    def rate(e):
        s = e.get("summary") if isinstance(e.get("summary"), dict) else {}
        v = s.get("rejection_rate")
        return float(v) if _finite_num(v) and v >= 0 else None

    alphas = sorted({a for a, _b in cells})
    betas = sorted({b for _a, b in cells})
    pos = [v for v in (rate(e) for e, _d in cells.values()) if v is not None and v > 0]
    lo, hi = (min(pos), max(pos)) if pos else (1.0, 1.0)
    cw, ch, ml, mt = 76, 40, 64, 44
    w = ml + cw * len(betas) + 8
    h = mt + ch * len(alphas) + 8
    parts = [f'<text x="{_fmt(ml + cw * len(betas) / 2)}" y="14" text-anchor="middle">'
             "beta</text>",
             f'<text x="{ml - 10}" y="{mt - 9}" text-anchor="end">alpha</text>']
    for j, b in enumerate(betas):
        parts.append(f'<text x="{_fmt(ml + cw * j + cw / 2)}" y="{mt - 9}" '
                     f'text-anchor="middle">{b}</text>')
    for i, a in enumerate(alphas):
        parts.append(f'<text x="{ml - 10}" y="{_fmt(mt + ch * i + ch / 2 + 3.5)}" '
                     f'text-anchor="end">{a}</text>')
    for i, a in enumerate(alphas):
        for j, b in enumerate(betas):
            x, y = ml + cw * j, mt + ch * i
            tx, ty = _fmt(x + cw / 2), _fmt(y + ch / 2 + 3.5)
            got = cells.get((a, b))
            if got is None:
                parts.append(f'<rect x="{x}" y="{y}" width="{cw}" height="{ch}" rx="4" '
                             f'fill="var(--surface-1)" stroke="var(--grid)">'
                             f"<title>alpha {a}, beta {b}: not in the plan</title></rect>")
                continue
            e, d = got
            v = rate(e)
            if v is None:
                statuses = sorted({str(p.get("status")) for p in d.get("points") or []
                                   if isinstance(p, dict) and p.get("status")})
                tip = (f"alpha {a}, beta {b}: no run finished"
                       + (f" (status {', '.join(statuses)})" if statuses else ""))
                parts.append(f'<rect x="{x}" y="{y}" width="{cw}" height="{ch}" rx="4" '
                             f'fill="var(--surface-1)" stroke="var(--line)">'
                             f"<title>{_soft(tip)}</title></rect>")
                parts.append(f'<text x="{tx}" y="{ty}" text-anchor="middle">n/a</text>')
                continue
            s = e["summary"]
            step = 1 if v == 0 else _heat_up(v, lo, hi)
            tip = (f"alpha {a}, beta {b}: rejection share {_num(v)}; "
                   f"{_plain(s.get('total_rejected'))} rejected and "
                   f"{_plain(s.get('total_accepted'))} accepted steps, "
                   f"{_plain(s.get('total_fevals'))} rhs evaluations, largest error "
                   f"{_plain(s.get('max_error'))}")
            parts.append(f'<rect x="{x}" y="{y}" width="{cw}" height="{ch}" rx="4" '
                         f'fill="var(--q{step})" class="cellstroke">'
                         f"<title>{_soft(tip)}</title></rect>")
            parts.append(f'<text class="cv" style="fill:var(--on-q{step})" x="{tx}" '
                         f'y="{ty}" text-anchor="middle">{v:.3g}</text>')
    svg = (f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
           'aria-label="Rejected share of attempted steps for each pair of controller '
           'gains alpha and beta">' + "".join(parts) + "</svg>")
    probs = {tuple(str(p) for p in d.get("problems")) for _e, d in docs
             if isinstance(d.get("problems"), list)}
    tols = {d.get("tolerance") for _e, d in docs if _finite_num(d.get("tolerance"))}
    where = ""
    if len(probs) == 1 and len(tols) == 1:
        where = (" Each point runs " + ", ".join(next(iter(probs))) + " at tolerance "
                 + _num(next(iter(tols))) + ".")
    # The ramp runs light to dark in the light theme and the other way in the dark one,
    # so the caption names the color by its distance from the page background.
    return ("<figure><figcaption>Share of attempted steps the controller rejected, by "
            "gains alpha (rows) and beta (columns). Every cell prints its share; on a log "
            "scale, the further a cell's color is from the page background, the larger "
            "the share. n/a means no run finished." + _soft(where) + "</figcaption>" + svg
            + '<p class="note">Source: side-track job adaptive.controller_gains, '
            f"{len(docs)} {'point' if len(docs) == 1 else 'points'}. Arithmetic: "
            + _arith_of(docs) + ".</p></figure>")


# The three priced parts of one attempt. The document also lists a branch_allowance, and
# it is a count of conditional branches, not cycles: the pinned cost model prices no
# branch, so the allowance is named in the caption and never drawn on the cycle axis.
_ATTEMPT_PARTS = (("stage", "stage arithmetic"), ("estimate", "error estimate"),
                  ("controller", "controller"))


def _branch_count(r) -> int | None:
    """The conditional-branch allowance of one row, from its per-attempt block or itself."""
    for node in (r.get("per_attempt_cycles"), r):
        v = node.get("branch_allowance") if isinstance(node, dict) else None
        if isinstance(v, dict):
            v = v.get("conditional_branches")
        if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
            return v
    return None


def _grade_direction(benchmark, grade: str) -> str:
    """What the benchmark says a cost grade bounds, e.g. "upper bound within the model"."""
    comp = benchmark.get("comparability") if isinstance(benchmark, dict) else None
    grades = comp.get("cost_grades") if isinstance(comp, dict) else None
    node = grades.get(grade) if isinstance(grades, dict) else None
    d = node.get("direction") if isinstance(node, dict) else None
    return d.strip() if isinstance(d, str) else ""


def _attempt_cost_chart(benchmark) -> str:
    """Modeled Q15 cycles for one attempted step of the adaptive solver, by part.

    One bar per distinct price, labeled by the problem size it applies to. The bar
    stacks the three priced parts and prints the document's own total at its end; the
    unpriced conditional branches are counted in the caption, because they are
    instructions and not cycles.
    """
    src = benchmark.get("adaptive_matched_tolerance") if isinstance(benchmark, dict) else None
    rows = [r for r in (src if isinstance(src, list) else [])
            if isinstance(r, dict) and isinstance(r.get("per_attempt_cycles"), dict)]
    groups: dict[tuple, dict] = {}
    branches: set[int] = set()
    for r in rows:
        pac = r["per_attempt_cycles"]
        vals = tuple(pac.get(k) for k, _l in _ATTEMPT_PARTS)
        if not all(_finite_num(v) and v >= 0 for v in vals):
            continue
        tot = pac.get("total")
        key = tuple(float(v) for v in vals) + ((float(tot),) if _finite_num(tot) else (-1.0,))
        g = groups.setdefault(key, {"n": 0, "states": set(), "solvers": set(),
                                    "grades": set(), "ariths": set()})
        g["n"] += 1
        nb = _branch_count(r)
        if nb is not None:
            branches.add(nb)
        if isinstance(r.get("n_states"), int) and not isinstance(r.get("n_states"), bool):
            g["states"].add(r["n_states"])
        for field, bucket in (("solver", "solvers"), ("cost_grade", "grades"),
                              ("arithmetic", "ariths")):
            if isinstance(r.get(field), str) and r[field]:
                g[bucket].add(r[field])
    if not groups:
        return _absent("The benchmark document has no row with a per-attempt Q15 price, "
                       "so there is nothing to plot.")

    def _n(v: float) -> str:
        return _num(int(v)) if float(v).is_integer() else _num(v)

    order = sorted(groups, key=lambda k: (sum(k[:3]), k))
    xs = _lin_scale(0.0, max(max(sum(k[:3]), k[3]) for k in order), n=5)
    w, ml, mr, mt, row_h, bar_h = 640, 132, 76, 8, 34, 22
    span = w - ml - mr
    px_per = span / (xs.hi - xs.lo)
    base = mt + row_h * len(order)
    h = base + 30
    body: list[str] = []
    mismatch = []
    for i, k in enumerate(order):
        g = groups[k]
        y = mt + row_h * i + (row_h - bar_h) / 2
        label = (" or ".join(str(s) for s in sorted(g["states"])) + "-state problems"
                 if g["states"] else "size not stated")
        body.append(f'<text x="{ml - 8}" y="{_fmt(y + bar_h / 2 + 3.5)}" '
                    f'text-anchor="end">{_esc(_clip(label, (ml - 10) / 7.0))}</text>')
        tot = k[3] if k[3] >= 0 else None
        if tot is not None and abs(tot - sum(k[:3])) > 1e-6:
            mismatch.append((label, tot, sum(k[:3])))
        segs = [(v, _SERIES4[j], f"{label}: {name}, {_n(v)} cycles per attempt "
                 f"({g['n']} rows)")
                for j, ((_key, name), v) in enumerate(zip(_ATTEMPT_PARTS, k[:3]))]
        drawn, end = _stack_bar(ml, y, bar_h, segs, px_per)
        body.extend(drawn)
        x = float(ml)
        for seg in segs:
            v = seg[0]
            wv = max(v * px_per, 1.0) if v > 0 else 0.0
            txt = _n(v)
            if v > 0 and wv >= len(txt) * 7.0 + 10:
                body.append(f'<text class="lbl" x="{_fmt(x + wv / 2)}" '
                            f'y="{_fmt(y + bar_h / 2 + 3.5)}" text-anchor="middle">{txt}</text>')
            x += wv
        body.append(_end_label(end, y + bar_h / 2 + 3.5,
                               _n(tot if tot is not None else sum(k[:3])), w))
    svg = (f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
           'aria-label="Modeled Q15 cycles for one attempted step, split by part, one bar '
           'per problem size">' + "".join(_hbar_frame(xs, ml, span, mt, base))
           + "".join(body) + "</svg>")
    solvers = sorted({s for g in groups.values() for s in g["solvers"]})
    grades = sorted({s for g in groups.values() for s in g["grades"]})
    ariths = sorted({s for g in groups.values() for s in g["ariths"]})
    n = sum(g["n"] for g in groups.values())
    keys = [(_SERIES4[j], name) for j, (_k, name) in enumerate(_ATTEMPT_PARTS)]
    if branches:
        most = max(branches)
        branch = (" The pinned cost model prices no branch, so the error-magnitude bit scan "
                  "is booked at its worst case, and each attempt can also run up to "
                  f"{most} conditional {'branch' if most == 1 else 'branches'} that no bar "
                  "includes.")
    else:
        branch = " The pinned cost model prices no branch, and no bar includes one."
    graded = []
    for gname in grades:
        d = _grade_direction(benchmark, gname)
        graded.append(_soft(gname) + (f" ({_soft(d)})" if d else ""))
    odd = "".join(f" The document's total for {_esc(lab)} is {_n(t)}, while the parts "
                  f"drawn add to {_n(s)}." for lab, t, s in mismatch)
    return ("<figure><figcaption>Modeled Q15 cycles for one attempted step of "
            + (_soft(", ".join(solvers)) or "the adaptive solver")
            + ", by part and problem size; the number at the end of each bar is the "
            "document's total." + branch + odd
            + " Grade: " + ("; ".join(graded) or "not stated") + ".</figcaption>"
            + _legend(keys) + svg + '<p class="note">Source: benchmark/results.json, '
            f"adaptive_matched_tolerance: the {n} rows that carry a per-attempt price. "
            "Arithmetic: " + (_soft(", ".join(_arith_label(a) for a in ariths))
                              or "not stated") + ".</p></figure>")


def _pair_error_count(text: str, benchmark) -> str:
    """Give the achieved-error median the sample size it was actually taken over.

    The document states one count for the paragraph, but the two medians run over
    different rows: a pair where neither side recorded an error still reports a function
    evaluation ratio, so the error median is taken over fewer cells than the paragraph
    names. The count is read from adaptive_pairs rather than written in, so it follows
    the data.
    """
    rows = benchmark.get("adaptive_pairs") if isinstance(benchmark, dict) else None
    if not isinstance(rows, list):
        return text
    n = sum(1 for r in rows if isinstance(r, dict)
            and isinstance(r.get("error_ratio_ours_over_rk23"), (int, float))
            and not isinstance(r.get("error_ratio_ours_over_rk23"), bool))
    if not n:
        return text
    return re.sub(
        r"(median achieved-error ratio of [0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)(?! over)",
        lambda m: f"{m.group(1)} over the {n} cells where both sides recorded an error",
        text, count=1)


def _pair_note(benchmark) -> str:
    """The RK23 same-tableau comparison, under the attempt-cost figure it belongs beside.

    Its ratios come from the matched-tolerance rows, the same family the attempt-cost bar
    reads, so it sits here rather than inside the matched-accuracy fold, whose table does
    not carry them. The benchmark's own closing sentence pointed at "this one" from inside
    that fold; it is replaced by one that holds wherever the paragraph sits.
    """
    text = _three_class_text(benchmark, "adaptive_pair").strip()
    if not text:
        return ""
    kept = [s for s in re.split(r"(?<=\.)\s+", text)
            if not s.startswith("Asking two solvers for the same tolerance")]
    out = _pair_error_count(" ".join(kept), benchmark)
    if "like-for-like" not in out:
        out += (" These ratios come from the matched-tolerance rows. Asking two solvers "
                "for the same tolerance does not put them at the same accuracy, so the "
                "matched-accuracy section below is the like-for-like comparison, and no "
                "wall-clock ratio is formed across the two implementations.")
    return f"<p>{_soft(out)}</p>"


_CENSUS_BARS = (("matrices", "matrices"), ("order2_consistent", "order-2 consistent"),
                ("order3_consistent", "order-3 consistent"),
                ("both_solvable", "both solvable"))


def _census_chart(docs) -> str:
    """How many lattice matrices meet each condition an embedded 3(2) pair needs.

    Grouped bars on a log axis with every exact count printed, because the counts fall
    from tens of thousands to single digits within one group. A point the census did
    not count (its space was over the cap) is named under the chart, not drawn.
    """
    groups, skipped = [], []
    for e, d in docs:
        params = d.get("params") if isinstance(d.get("params"), dict) else {}
        st, s = params.get("stages", d.get("stages")), params.get("s_max", d.get("s_max"))
        shaped = all(isinstance(v, int) and not isinstance(v, bool) for v in (st, s))
        label = f"{st} stages, s ≤ {s}" if shaped else str(e.get("key") or "")
        counts = d.get("counts") if isinstance(d.get("counts"), dict) else {}
        vals = [(name, counts.get(k)) for k, name in _CENSUS_BARS]
        if (str(d.get("status")) == "ok"
                and all(isinstance(v, int) and not isinstance(v, bool) and v >= 0
                        for _n, v in vals) and vals[0][1] > 0):
            groups.append(((st, s) if shaped else (0, 0), label, vals))
        else:
            skipped.append((label, d))
    if not groups:
        return _absent("The pair census has no counted point yet, so there is nothing to "
                       "plot.")
    groups.sort(key=lambda g: (g[0], g[1]))
    top = 10.0 ** max(1, math.ceil(math.log10(max(v for _k, _l, vals in groups
                                                   for _n, v in vals))))
    xs = _Scale(1.0, top, True, _log_ticks(1.0, top), fmt=lambda v: f"{int(round(v)):,}")
    w, ml, mr, mt, head_h, pitch, bar_h = 640, 248, 64, 6, 22, 16, 11
    span = w - ml - mr
    body: list[str] = []
    y = float(mt)
    merged_any = False
    for _key, label, vals in groups:
        body.append(f'<text x="2" y="{_fmt(y + 14)}">{_soft(_clip(label, (w - 4) / 7.0))}</text>')
        y += head_h
        # Neighboring conditions with the same count share one bar: every matrix is
        # often order-2 consistent, and every order-3 one often solvable, and drawing
        # the same number twice doubled the chart without adding a fact.
        runs: list[tuple[list[str], int]] = []
        for name, v in vals:
            if runs and runs[-1][1] == v:
                runs[-1][0].append(name)
            else:
                runs.append(([name], v))
        for names, v in runs:
            merged_any = merged_any or len(names) > 1
            rowname = (", ".join(names) if len(names) <= 2
                       else f"{names[0]} to {names[-1]}")
            ty = _fmt(y + bar_h / 2 + 3.5)
            body.append(f'<text x="{ml - 8}" y="{ty}" text-anchor="end">'
                        f"{_esc(_clip(rowname, (ml - 10) / 7.0))}</text>")
            bw = max(xs.frac(v) * span, 2.0) if v >= 1 else 0.0
            if bw:
                body.append(_round_end_bar(ml, y, bw, bar_h, "var(--s1)",
                                           f"{label}: {v:,} " + ", ".join(names)))
            body.append(_end_label(ml + bw, y + bar_h / 2 + 3.5, f"{v:,}", w))
            y += pitch
        y += 6
    base = y
    h = int(math.ceil(base + 30))
    svg = (f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
           'aria-label="Lattice matrices meeting each embedded-pair condition, by stage '
           'count and lattice depth">' + "".join(_hbar_frame(xs, ml, span, mt, base))
           + "".join(body) + "</svg>")
    note = ""
    capped = [(label, d) for label, d in skipped if str(d.get("status")) == "capped"]
    if capped:
        caps = {d.get("cap") for _l, d in capped}
        cap = next(iter(caps)) if len(caps) == 1 else None
        note = ('<p class="note">Not counted (space over the census cap'
                + (f" of {cap:,} matrices" if isinstance(cap, int) else "") + "): "
                + "; ".join(_soft(label) for label, _d in capped) + ".</p>")
    other = [label for label, d in skipped if str(d.get("status")) != "capped"]
    if other:
        note += ('<p class="note">Not drawn, because the point carries no complete count: '
                 + ", ".join(_soft(x) for x in other) + ".</p>")
    return ("<figure><figcaption>Lattice matrices that meet each condition an order-3 "
            "pair with an order-2 estimate needs, by stage count and lattice depth s. "
            "Counts are exact; the axis is log, so 0 has no bar."
            + (" Neighboring conditions with the same count share a bar." if merged_any
               else "")
            + "</figcaption>" + svg + note
            + '<p class="note">Source: side-track job adaptive.pair_census, '
            f"{len(docs)} {'point' if len(docs) == 1 else 'points'}. Arithmetic: "
            + _arith_of(docs) + ".</p></figure>")


def render_adaptive(sidetrack: dict | None = None, benchmark: dict | None = None) -> str:
    """The adaptive class, in the skeleton all three class pages share: lead, at a
    glance, cards, charts, matched accuracy, lane elites, the invariants, then folds."""
    entries = _track_entries(sidetrack, "adaptive")
    artifacts = (sidetrack.get("artifacts") if isinstance(sidetrack, dict)
                 and isinstance(sidetrack.get("artifacts"), dict) else {})
    parts = [f'<p class="lead">{_esc(_ADAPTIVE_CLASS)}</p>', _glance("adaptive")]
    if entries:
        extra = []
        # One job's number, named with that job: controller_gains records a rejection
        # share per point, and suite_sweep records a different quantity (the largest
        # share over one sweep), so a maximum taken across both would mix the two.
        job = "adaptive.controller_gains"
        # Newest reading per point, as the charts draw it: a superseded line from
        # earlier code no longer counts as measured, so it cannot set the maximum.
        rate = _summary_max(_latest_points(entries), "rejection_rate", job=job)
        if rate is not None:
            extra.append(("highest rejection share", _num(rate),
                          f"largest over the {job} points"
                          + _job_arith(entries, artifacts, job)))
        parts.append(_ledger_cards(entries, extra))
    else:
        parts.append(f"<p>{_esc(_NO_POINTS)}</p>")

    if entries:
        parts.append("<h2>Work against accuracy across the tolerance sweep</h2>")
        parts.append(_chart_block(_sweep_multiples(
            _job_docs(entries, artifacts, "adaptive.suite_sweep"))))
        parts.append("<h2>Controller gains and rejected steps</h2>")
        parts.append(_chart_block(_gain_map(
            _job_docs(entries, artifacts, "adaptive.controller_gains"))))
    if isinstance(benchmark, dict):
        parts.append("<h2>What one Q15 attempt costs</h2>")
        parts.append(_chart_block(_attempt_cost_chart(benchmark)))
        pair = _pair_note(benchmark)
        if pair:
            # A hundred words on a second comparison, beside the figure it belongs to
            # but folded, so the open page stays on what one attempt costs.
            parts.append(_fold("How this compares with SciPy RK23", pair))
    if entries:
        parts.append("<h2>Embedded pairs on the dyadic lattice</h2>")
        parts.append(_chart_block(_census_chart(
            _job_docs(entries, artifacts, "adaptive.pair_census"))))
    parts.extend(_matched_section(benchmark, "adaptive"))
    parts.extend(_lane_elites_section("adaptive", benchmark))
    parts.append(_not_these_numbers())
    if entries:
        parts.append(_points_fold(entries, artifacts))
    parts.append(_further_fold([
        ("rk-work/validation/axes.json", _load_validation_axes() is not None,
         "the cycles-to-tolerance rows for this class, and the measured floor of the "
         "Q15 error estimate that bounds how tight a tolerance can mean anything"),
        ("rk-work/adaptive_archive/YYYY-MM-DD.jsonl", _lane_records_present("adaptive"),
         "the lane's per-day search log: one line per candidate the lane enumerated, "
         "ranked against nothing"),
        ("rk-work/schedule/shares.json", _load_shares() is not None,
         "the per-cycle lane log summarised per method class"),
    ]))
    return _page("Adaptive methods", "\n".join(parts), active="adaptive.html",
                 subtitle="Embedded pairs and step-size control, measured off-archive.")


# ----------------------------------------------------------------------------
# the measurement ledger: provenance for every off-archive point, on methodology.html
# ----------------------------------------------------------------------------

def _ledger_section(data) -> str:
    """The side-track ledger's rules and its failures, for the methodology page.

    The readings are on the class pages, each under its own class. What stays here is
    what applies to every point: the code-hash rule, the retry policy, and the points
    that did not complete. This replaced sidetrack.html, which also carried the whole
    ledger flattened into one table; that table repeated the class pages' job tables.
    """
    rows_all = ([e for e in (data.get("ledger") or []) if isinstance(e, dict)]
                if isinstance(data, dict) else [])
    ok = sorted([e for e in rows_all if e.get("status") == "ok"],
                key=lambda e: (str(e.get("track", "")), str(e.get("job", "")),
                               str(e.get("key", ""))))
    failed = sorted([e for e in rows_all if e.get("status") == "failed"],
                    key=lambda e: (str(e.get("track", "")), str(e.get("job", "")),
                                   str(e.get("key", ""))))
    parts = []
    if not isinstance(data, dict):
        parts.append("<p>The side-track ledger, rk-work/sidetrack/ledger.jsonl, has not been "
                     "written in this work directory, so no measurement outside the scored "
                     "archive is recorded yet.</p>")
    else:
        n = _distinct_points(ok)
        jobs = sorted({str(e.get("job", "")) for e in ok if e.get("job")})
        text = ("<p>Every measurement the run takes outside the scored archive is a line in "
                "rk-work/sidetrack/ledger.jsonl, stamped with the code hash that produced it "
                "and the artifact it was read from. ")
        if n:
            text += (f"The ledger holds {n} {'point' if n == 1 else 'points'} across "
                     f"{len(jobs)} {'job' if len(jobs) == 1 else 'jobs'}. The readings are "
                     'on the <a href="implicit.html">implicit</a> and '
                     '<a href="adaptive.html">adaptive</a> tabs.')
        else:
            text += "No point has completed yet."
        newest = _newest_code_hash(ok)
        if newest:
            text += (' The newest measurement ran under code hash <span class="hash">'
                     f"{_soft(newest[:12])}</span>.")
        parts.append(text + "</p>")
    parts.append("<h3>The code-hash rule</h3>")
    parts.append("<p>Every artifact is a pure function of the code and the point's "
                 "parameters, so measuring a point again reproduces it byte for byte. A "
                 "point counts as measured only under the code hash that measured it, a "
                 "digest over the executor and the prototype modules, so editing a "
                 "prototype re-opens its points instead of leaving stale numbers beside "
                 "fresh ones.</p>")
    codes = _code_hashes(ok)
    if len(codes) > 1:
        parts.append('<p class="note">Points in the ledger were measured under '
                     f"{len(codes)} code hashes: "
                     + ", ".join(f'<span class="hash">{_soft(h)}</span>' for h in codes)
                     + ".</p>")
    parts.append("<h3>The retry policy</h3>")
    parts.append("<p>A point that raises is recorded with its message and retried on later "
                 "firings; after three failures under one code hash it is set aside and "
                 "listed here. A side-track failure never costs a cycle, because the "
                 "executor catches it and the run continues.</p>")
    if failed:
        rows = "\n".join(
            f'<tr><th class="mono">{_soft(e.get("job"))}:{_soft(e.get("key"))}</th>'
            f'<td>{_soft(str(e.get("error", ""))[:200])}</td></tr>'
            for e in failed)
        parts.append("<h3>Points that did not complete</h3>")
        parts.append('<div class="scroll"><table>\n<tr><th>point</th><th>error</th></tr>\n'
                     + rows + "\n</table></div>")
    return "\n".join(parts)


def _methodology_sections(sidetrack) -> tuple[tuple[str, str, str], ...]:
    """The sections sitegen builds for methodology.html, as (anchor, heading, body).

    methodology.py owns the article and never imports this module, so the parts that
    need run data or the pinned cost model are built here and handed over.
    """
    return (
        ("costmodel", "Cost model", _costmodel_section()),
        ("ledger", "Measurement ledger", _ledger_section(sidetrack)),
        ("glossary", "Glossary", _glossary_section()),
    )


def _prune(out_dir: Path, pages: dict[str, str]) -> None:
    """Delete the retired pages and the cell pages of cells the archive no longer holds.

    Nothing else in out_dir is touched: not the README, not CNAME, not a file whose
    name merely looks like a page. Runs only after every page has passed the banned
    word check and been written, so a failed build deletes nothing either.
    """
    for name in _RETIRED:
        path = out_dir / name
        if path.is_file():
            path.unlink()
    for path in sorted(out_dir.iterdir()):
        if path.is_file() and _CELL_FILE_RE.match(path.name) and path.name not in pages:
            path.unlink()


def build(arch: ArchiveState, out_dir: Path) -> None:
    global _PRESENT
    out_dir = Path(out_dir)
    validation = _load_validation()
    benchmark = _load_benchmark()
    sidetrack = _load_sidetrack()
    falsification = _load_falsification()
    evidence = any(x is not None for x in (validation, benchmark, falsification))
    _PRESENT = frozenset({"validation.html"}) if evidence else frozenset()
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
        pages["hypotheses.html"] = render_hypotheses(
            _load_hypotheses(), digests=literature_mod.load_digests(),
            interpretations=literature_mod.load_interpretations())
        if evidence:
            pages["validation.html"] = render_validation(validation, benchmark=benchmark,
                                                         falsification=falsification)
        try:
            from rk_harness import methodology
        except ImportError:
            pass
        else:
            pages["methodology.html"] = methodology.render_page(
                _page, sections=_methodology_sections(sidetrack))
        for name in sorted(pages.keys()):
            check_banned(pages[name])
        out_dir.mkdir(parents=True, exist_ok=True)
        for name in sorted(pages.keys()):
            with open(out_dir / name, "wb") as fh:
                fh.write(pages[name].encode("utf-8"))
        _prune(out_dir, pages)
    finally:
        _PRESENT = frozenset()
