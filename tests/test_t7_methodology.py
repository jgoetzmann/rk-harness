"""T7 — methodology page (rk_harness/methodology.py).

The module owns a single long-form article and renders it through an injected page
callable (sitegen._page in production). These tests build the page both with a
minimal fake callable and with the real sitegen._page, then check the banned-word
guard, anchor integrity, section coverage, determinism, and the no-sitegen-import
rule.
"""
from __future__ import annotations

import ast
import inspect
import re

import pytest

from rk_harness import methodology
from rk_harness import sitegen


SECTION_IDS = (
    "meth-number-system",
    "meth-test-problems",
    "meth-simulation",
    "meth-measured-order",
    "meth-cost-model",
    "meth-search",
    "meth-enumeration",
    "meth-archive",
    "meth-ledger",
    "meth-falsification",
    "meth-trust",
    "meth-testing",
    "meth-reproducibility",
    "meth-limitations",
)

SECTION_TITLES = (
    "1. Number system",
    "2. Test problems",
    "3. Simulation and evaluation",
    "4. Measured order",
    "5. Cost model",
    "6. Search",
    "7. Exhaustive enumeration",
    "8. Archive",
    "9. Hypothesis ledger",
    "10. Falsification protocol",
    "11. Model integration and trust boundaries",
    "12. Testing",
    "13. Reproducibility",
    "14. Limitations",
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

def test_all_fourteen_sections_present(fake_html):
    _, ids = _hrefs_and_ids(fake_html)
    for sid in SECTION_IDS:
        assert sid in ids, f"missing section id {sid}"
    assert "meth-references" in ids
    for heading in SECTION_TITLES:
        assert heading in fake_html, f"missing heading {heading!r}"


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
    assert sitegen.BANNER in real_html
