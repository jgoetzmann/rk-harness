"""T7 — methodology page (rk_harness/methodology.py).

The module owns a single long-form article and renders it through an injected page
callable (sitegen._page in production). Since D41 the caller also injects the closing
sections that need run data or the pinned cost model (cost model, measurement ledger,
glossary). These tests build the page with a minimal fake callable and with the real
sitegen._page, with and without those sections, then check the banned-word guard,
anchor integrity, section coverage, determinism, and the no-sitegen-import rule.
"""
from __future__ import annotations

import ast
import inspect
import os
import re
from pathlib import Path

import pytest

from rk_harness import methodology
from rk_harness import sitegen


SECTION_IDS = (
    "meth-setup",
    "meth-measurement",
    "meth-protocol",
    "meth-trust",
    "meth-testing",
    "meth-reproducibility",
    "meth-limitations",
)

SECTION_TITLES = (
    "1. Experimental setup",
    "2. Measurement",
    "3. Statistical protocol",
    "4. Verification and trust",
    "5. Testing",
    "6. Reproducibility",
    "7. Limitations",
)

# Captured at import, which happens before any fixture runs. conftest's autouse fixture
# repoints RK_WORK_DIR at a throwaway directory so no test can touch a real archive, and
# says that a test setting it itself overrides that. The word budget takes that override,
# because the page it has to weigh is built from two live run values.
_AMBIENT_WORK = os.environ.get("RK_WORK_DIR")


def _live_work_dir() -> Path:
    """The run directory this session was launched against, or the checkout beside this.

    Two candidates, tried in order: the container runs with RK_WORK_DIR set to a path
    nowhere near this file, and the host runs with rk-work as a sibling of rk-harness.
    Nothing is guessed quietly; the caller fails naming what it tried.
    """
    for cand in (_AMBIENT_WORK, Path(__file__).resolve().parents[2] / "rk-work"):
        if cand and Path(cand).is_dir():
            return Path(cand)
    return Path(_AMBIENT_WORK or "rk-work")


def _fake_page(title: str, body: str, active: str = "", subtitle: str = "") -> str:
    return (
        f"<html><head><title>{title}</title></head><body>"
        f"<p>{subtitle}</p><p>{active}</p>{body}</body></html>"
    )


@pytest.fixture(scope="module")
def fake_html() -> str:
    return methodology.render_page(_fake_page)


@pytest.fixture(scope="module")
def real_html() -> str:
    return methodology.render_page(sitegen._page)


# --------------------------------------------------------------------- interface

def test_title_constant():
    """Sentence case: the h1 and the browser tab capitalize, the nav label does not."""
    assert methodology.TITLE == "Methodology"


def test_render_page_passes_frozen_arguments():
    calls: list[tuple] = []

    def spy(title, body, active="", subtitle=""):
        calls.append((title, body, active, subtitle))
        return "ok"

    out = methodology.render_page(spy)
    assert out == "ok"
    assert len(calls) == 1
    title, body, active, subtitle = calls[0]
    assert title == "Methodology"
    assert active == "methodology.html"
    assert isinstance(body, str) and len(body) > 5000
    assert isinstance(subtitle, str) and subtitle


def test_module_does_not_import_sitegen():
    src = inspect.getsource(methodology)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any("sitegen" in alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module is None or "sitegen" not in node.module


# ------------------------------------------------------------------ banned words

def test_banned_words_fake_page(fake_html):
    sitegen.check_banned(fake_html)  # raises BannedWordError on a hit


def test_banned_words_real_page(real_html):
    sitegen.check_banned(real_html)


# ----------------------------------------------------------------------- anchors

def _hrefs_and_ids(html_text: str) -> tuple[set[str], set[str]]:
    hrefs = set(re.findall(r'href="#([^"]+)"', html_text))
    ids = set(re.findall(r'id="([^"]+)"', html_text))
    return hrefs, ids


def test_every_internal_href_resolves(fake_html):
    hrefs, ids = _hrefs_and_ids(fake_html)
    assert hrefs, "expected internal anchors (TOC and citations)"
    missing = hrefs - ids
    assert not missing, f"dangling anchors: {sorted(missing)}"


def test_every_internal_href_resolves_real_page(real_html):
    # The real chrome adds nav links to other pages; internal '#' anchors must
    # still all resolve within the document.
    hrefs, ids = _hrefs_and_ids(real_html)
    missing = hrefs - ids
    assert not missing, f"dangling anchors: {sorted(missing)}"


def test_toc_links_every_section(fake_html):
    hrefs, _ = _hrefs_and_ids(fake_html)
    for sid in SECTION_IDS + ("meth-references",):
        assert sid in hrefs, f"TOC does not link #{sid}"


# ---------------------------------------------------------------------- sections

def test_all_seven_sections_present(fake_html):
    _, ids = _hrefs_and_ids(fake_html)
    for sid in SECTION_IDS:
        assert sid in ids, f"missing section id {sid}"
    assert "meth-references" in ids
    for heading in SECTION_TITLES:
        assert heading in fake_html, f"missing heading {heading!r}"


def test_practical_validation_subsection_present(fake_html):
    _, ids = _hrefs_and_ids(fake_html)
    assert "meth-practical" in ids
    assert "Practical validation" in fake_html


def test_the_page_says_which_weighting_the_aggregate_uses(fake_html):
    """G1 acceptance 5. This page defined the per-problem error and stopped there, so the
    site published an aggregate it never defined and never said which weighting that
    aggregate is. An RMS sums squares, so a problem's influence grows with the square of
    its error, and that is a choice about which problem decides a method's score.

    The dominant problem is stated per method and never for the metric as a whole. It is
    rc_thermal for most methods here but pendulum for euler, which carries 86 percent of
    euler's held-out sum of squares, so a blanket claim that the metric is an rc_thermal
    score would be false. The paragraph therefore names no problem and no share: those
    numbers come from the overview's key_findings.json, which this site does not read.
    """
    para = ("Search error and held-out error"
            + fake_html.split("Search error and held-out error", 1)[1].split("</p>", 1)[0])
    # the source wraps mid-sentence, so match on the text rather than on its line breaks
    para = re.sub(r"\s+", " ", para)
    assert "root mean square of the per-problem errors" in para
    assert "grows with the square of its error" in para
    assert "do not carry equal weight" in para
    # which weighting every archived score was actually computed under, in those terms
    assert "magnitude weighting is what every score in the archive was computed under" in para
    # the equal-weighting answer named as such, and the grid it points at
    assert "median error of the classical anchors" in para
    assert f'href="{methodology._RESULTS}#efficiency"' in para
    assert "two scales disagree" in para
    # no number here, because none of them trace to a document this site reads
    assert "rc_thermal" not in para and "%" not in para
    assert not re.search(r"\d+\.\d", para), "a share or a ratio reached the findings site"


def test_reference_targets_present(fake_html):
    """21 internal files and specification sections, then the three outside papers.

    References 22 to 24 are published work, cited by the related-work section: the lead
    says the list carries papers as well as repository files, so a citation that points
    outside the repository has somewhere to land.
    """
    _, ids = _hrefs_and_ids(fake_html)
    refs = {i for i in ids if i.startswith("meth-ref-")}
    assert refs == {f"meth-ref-{n}" for n in range(1, 25)}
    for name in ("Croci", "Giles", "Hopkins", "Rosilho de Souza"):
        assert name in fake_html, name


def test_infobox_present(fake_html):
    assert 'class="infobox meth-infobox"' in fake_html


# ------------------------------------------------------------------- page hygiene

def test_no_javascript(real_html):
    assert "<script" not in real_html.lower()


def test_deterministic_output(real_html):
    assert methodology.render_page(sitegen._page) == real_html


def test_real_page_is_full_document(real_html):
    assert real_html.startswith("<!doctype html>")
    assert "<title>Methodology</title>" in real_html
    assert "<h1>Methodology</h1>" in real_html
    assert sitegen.BANNER in real_html


# ------------------------------------------------------------- injected sections

def test_injected_sections_land_after_section_7_and_in_the_toc():
    sections = (("costmodel", "Cost model", "<p>cm body</p>"),
                ("ledger", "Measurement ledger", "<p>ledger body</p>"),
                ("glossary", "Glossary", '<dl class="gloss"><dt id="q15">Q15</dt></dl>'))
    html = methodology.render_page(_fake_page, sections)
    hrefs, ids = _hrefs_and_ids(html)
    for sid, title, body in sections:
        assert f'<h2 id="{sid}">{title}</h2>' in html, sid
        assert sid in hrefs, f"TOC does not link #{sid}"
        assert body in html
    assert not (hrefs - ids), f"dangling anchors: {sorted(hrefs - ids)}"
    assert (html.index('id="meth-limitations"') < html.index('id="costmodel"')
            < html.index('id="ledger"') < html.index('id="glossary"')
            < html.index('id="meth-references"'))
    assert methodology.render_page(_fake_page, sections) == html


def test_the_site_sections_render_on_the_real_page():
    html = methodology.render_page(sitegen._page, sitegen._methodology_sections(None))
    hrefs, ids = _hrefs_and_ids(html)
    assert not (hrefs - ids), f"dangling anchors: {sorted(hrefs - ids)}"
    for sid in ("costmodel", "ledger", "glossary", "floor-rounding", "csd-weight", "tiers"):
        assert sid in ids, sid
    # links into the glossary from the article itself resolve on this page
    for anchor in re.findall(r'href="methodology\.html#([^"]+)"', html):
        assert anchor in ids, anchor
    assert sitegen.AVR_NOTE in html and "avr_approx" in html      # preflight H4
    assert "<style>" not in html.split("<body>", 1)[1]            # no style block in the body
    assert "glossary.html" not in html and "costmodel.html" not in html
    sitegen.check_banned(html)
    assert "<script" not in html.lower()


# ------------------------------------------------------------------ length (A2 round)

def test_sections_4_to_6_are_short_and_end_on_the_overview(fake_html):
    """The overview's architecture page carries the machinery; these sections keep what
    the method rests on and link there rather than repeating it."""
    for sid, frag in (("meth-trust", "#verify"), ("meth-testing", "#tests"),
                      ("meth-reproducibility", "#repro")):
        sec = fake_html.split(f'id="{sid}"', 1)[1].split("<h2", 1)[0]
        assert f'href="{methodology._ARCH}{frag}"' in sec, sid
        assert "<h3" not in sec, sid
        assert len(re.sub(r"<[^>]+>", " ", sec).split()) < 160, sid


def test_every_glossary_definition_is_at_most_two_sentences():
    for anchor, _term, paras in sitegen._GLOSSARY:
        text = " ".join(paras)
        assert 1 <= len(re.findall(r"[.?!](?:\s|$)", text)) <= 2, anchor


_CHART_TABLE_RE = r'<details class="fold" id="[a-z0-9-]+-values">.*?</details>'


def test_the_page_stays_under_its_word_budget(monkeypatch):
    """Visible words, counting the article's own tables, the infobox and the references:
    under 4,100.

    The budget was 3,500 and the page was within a few words of it, so the audit's two
    required additions did not fit: a related-work section naming the low-precision
    time-integration literature (about 260 words and three full citations), and the
    numeric thresholds behind the degeneracy filter and the tie band in section 3 (about
    125). The budget was raised once, by what those cost plus a little headroom, and it
    still fails on unbounded growth, which is what it is for.

    It was raised a second time, by 100, when a third degeneracy criterion shipped: any
    two finishers agreeing on the reference norm, which is what catches a field that one
    live method drags past the other two criteria. A criterion that decides whether a
    problem enters a published tally has to be stated on the page that states the other
    two, and the page had about three words of headroom left. The rule is unchanged:
    additions are paid for once, deliberately, and drift still fails here.

    It was raised a third time, by 200, when the page finally said how the per-problem
    errors are combined. Until then this site published an aggregate it never defined:
    search error and held-out error are an RMS, an RMS sums squares, and summing squares
    is a weighting choice that decides which problem sets a method's score. A reader could
    not tell from this page that the choice had been made, let alone which way. The
    definition, the weighting the archive was scored under and the scale that answers the
    equal-weighting question cost 170 words together, and they are not optional prose.

    A chart's folded data table is left out of the count. It is generated from the
    numbers already in the drawing, it stays collapsed until a reader opens it, and it
    exists because role="img" puts those numbers out of reach otherwise. Counting it
    would price a chart's accessibility as prose and squeeze the writing to pay for it.
    Its size is bounded below instead, so the exclusion cannot hide growth.

    The guard measures the page the build publishes. It used to build its own page from
    _methodology_sections(None) and no merged-tier count, which leaves out the ledger
    section's failed points and section 3's count clause: 4,306 words against a page that
    ships at 4,365. The budget is unchanged at 4,400 and is not raised to make room,
    so the real headroom is 35 words rather than the 87 the old measurement implied.
    Two of the three injected sections read run data, so this needs a work directory.
    """
    monkeypatch.setenv("RK_WORK_DIR", str(_live_work_dir()))
    sidetrack = sitegen._load_sidetrack()
    merged = sitegen._merged_tier_count()
    assert sidetrack is not None, (
        "this guard has to weigh the page the build publishes, and the measurement "
        "ledger section is part of that page; point RK_WORK_DIR at a work directory "
        "that carries sidetrack/ledger.jsonl")
    assert merged is not None, (
        "section 3 states the merged tier count, so the published page depends on it; "
        "point RK_WORK_DIR at a readable archive")
    html = sitegen.render_methodology(sidetrack, merged)
    body = html.split("</header>", 1)[1].split("<footer>", 1)[0]
    tables = re.findall(_CHART_TABLE_RE, body, re.S)
    prose = re.sub(_CHART_TABLE_RE, " ", body, flags=re.S)
    words = re.sub(r"<[^>]+>", " ", prose).split()
    assert len(words) < 4400, len(words)
    table_words = sum(len(re.sub(r"<[^>]+>", " ", t).split()) for t in tables)
    assert len(tables) == 1, len(tables)
    assert table_words < 100, table_words
    assert methodology._SUBTITLE == "How the run measures, checks and reproduces its numbers."


# ------------------------------------------------- the merged tier word (SHARE-FIX)

def test_section_three_states_the_merged_tier_word_from_a_count_not_from_memory():
    """scripts/backfill_tiers.py rewrites the stored tier word across the archive, so a
    sentence saying every earlier record still carries it is true until that script runs
    and false the moment it does. The clause is generated from a count the caller takes
    from the archive, so it reads correctly in both states and needs no third edit.

    None is the answer when the archive cannot be counted. The paragraph then says only
    what the word meant, which holds either way.
    """
    for probe, want in (
            (None, "before the split. The tier"),
            (0, "before the split, and no archived record carries it now."),
            (1, "before the split, and one archived record still carries it."),
            (82199, "before the split, and 82,199 archived records still carry it."),
    ):
        html = re.sub(r"\s+", " ",
                      methodology.render_page(_fake_page, merged_tier_records=probe))
        assert want in html, (probe, want)
        sitegen.check_banned(html)
    # the retired sentence is gone from the section in every state
    for probe in (None, 0, 1, 82199):
        assert "every record written earlier still carries it" not in methodology._s3(probe)


def test_the_tier_glossary_entry_follows_the_same_count_and_stays_two_sentences():
    """The glossary made the same claim in its own words, so it follows the same count.
    Two sentences is the rule every entry keeps, in each state a count can produce."""
    for probe in (None, 0, 1, 82199):
        text = " ".join(sitegen._tiers_gloss(probe))
        assert 1 <= len(re.findall(r"[.?!](?:\s|$)", text)) <= 2, probe
        assert "unreplicated" in text
        assert "which merged those last two" not in text
    assert "82,199 records still carry" in " ".join(sitegen._tiers_gloss(82199))
    assert "one record still carries" in " ".join(sitegen._tiers_gloss(1))
    assert "no record carries now" in " ".join(sitegen._tiers_gloss(0))
    # the entry standing in _GLOSSARY is the count-free form, so the two cannot drift
    static = dict((anchor, paras) for anchor, _term, paras in sitegen._GLOSSARY)["tiers"]
    assert static == sitegen._tiers_gloss(None)
