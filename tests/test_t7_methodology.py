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
import re

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


def test_reference_targets_present(fake_html):
    _, ids = _hrefs_and_ids(fake_html)
    refs = {i for i in ids if i.startswith("meth-ref-")}
    assert refs == {f"meth-ref-{n}" for n in range(1, 22)}


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


def test_the_page_stays_under_its_word_budget():
    """Visible words, counting tables, the infobox and the references: under 3,500."""
    html = methodology.render_page(sitegen._page, sitegen._methodology_sections(None))
    body = html.split("</header>", 1)[1].split("<footer>", 1)[0]
    words = re.sub(r"<[^>]+>", " ", body).split()
    assert len(words) < 3500, len(words)
    assert methodology._SUBTITLE == "How the run measures, checks and reproduces its numbers."
