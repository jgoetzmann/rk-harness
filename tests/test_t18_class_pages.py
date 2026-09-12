"""T18 - the per-class findings pages: explicit, implicit, adaptive.

The site gives the three method classes equal standing: a page each, a nav entry each,
an index card each. These tests arbitrate the properties that make that safe to publish
from a container nobody is watching.

The primary case here is ABSENT DATA. rk-work/validation/axes.json is unwritten, the
lane archives are unwritten, and the ledger is empty on a fresh work directory, so that
is the state the next build will actually ship. Every test that supplies data also has a
sibling that supplies none.

Fixtures are inline and duplicated on purpose, in the style of the other test files:
each one states the shape it is arbitrating rather than importing it.
"""
from __future__ import annotations

import hashlib
import html as htmlmod
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

from rk_harness import sitegen
from rk_harness.sitegen import (
    build, check_banned, render_adaptive, render_explicit, render_implicit, render_index,
)
from rk_harness.paths import work_dir
from rk_harness.types import ArchiveState
# The chart-fit audit lives with the other site tests in t4; one copy, imported here.
from test_t4_ledger_runner_site import _assert_chart_fit

CLASS_PAGES = ("explicit.html", "implicit.html", "adaptive.html")
CLASS_HREFS = ('href="explicit.html"', 'href="implicit.html"', 'href="adaptive.html"')


# --------------------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------------------

def _env(monkeypatch, tmp_path) -> Path:
    work = tmp_path / "work"
    findings = tmp_path / "findings"
    work.mkdir(parents=True, exist_ok=True)
    findings.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("RK_WORK_DIR", str(work))
    monkeypatch.setenv("RK_FINDINGS_DIR", str(findings))
    monkeypatch.setenv("RK_SITE", "off")
    monkeypatch.setenv("RK_LLM", "off")
    monkeypatch.delenv("RK_CLOCK", raising=False)
    assert work_dir() == work
    return work


def _empty_arch() -> ArchiveState:
    return ArchiveState(n_records=0, last_cycle_id=0, grids={1: {}, 2: {}, 3: {}, 4: {}},
                        open_hypotheses=(), refuted_hypotheses=())


def _stocked_arch(n: int = 132049) -> ArchiveState:
    """An archive that holds records, for the tests about what a number looks like.

    _empty_arch is the fresh-work-directory case and the hub now says "not started"
    for it, correctly: a card cannot name the archive as its source and then print a
    count that no archive produced.
    """
    return ArchiveState(n_records=n, last_cycle_id=2726,
                        grids={1: {}, 2: {}, 3: {}, 4: {}},
                        open_hypotheses=(), refuted_hypotheses=())


def _snapshot(root: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and "HEARTBEAT" not in p.name}


def _write_ledger(work: Path, points: list[dict]) -> None:
    """A side-track ledger and its artifacts, in the layout rk_harness.sidetrack writes."""
    base = work / "sidetrack"
    base.mkdir(parents=True, exist_ok=True)
    for p in points:
        entry = {"ts": "2026-09-04T00:00:00Z", "cycle": 20, "track": p["track"],
                 "job": p["job"], "key": p["key"], "code_hash": "0123456789abcdef",
                 "status": p.get("status", "ok"), "duration_s": 0.5}
        if p.get("status") == "failed":
            entry["error"] = p["error"]
        else:
            rel = f"sidetrack/{p['job']}/{p['key']}.json"
            art = work / rel
            art.parent.mkdir(parents=True, exist_ok=True)
            doc = {"job": p["job"], "key": p["key"],
                   "closes": p.get("closes", "a question"),
                   "arithmetic": p.get("arithmetic", "float64"),
                   "summary": p["summary"]}
            doc.update(p.get("art", {}))     # the fields a class-page chart draws
            art.write_text(json.dumps(doc, sort_keys=True), encoding="utf-8")
            entry["artifact"] = rel
            entry["summary"] = p["summary"]
        with open(base / "ledger.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")


def _ledger_fixture() -> list[dict]:
    return [
        {"track": "adaptive", "job": "adaptive.suite_sweep", "key": "buck_converter",
         "summary": {"points": 6, "finished": 6, "rejection_rate_max": 0.0167}},
        {"track": "adaptive", "job": "adaptive.controller_gains", "key": "a1o8_b0o1",
         "summary": {"total_accepted": 3080, "total_rejected": 40,
                     "rejection_rate": 0.0128, "total_fevals": 9500,
                     "max_error": 2.1e-05}},
        {"track": "implicit", "job": "sdirk.stiff_suite", "key": "servo_load_step",
         "summary": {"finishers": ["sdirk2"], "explicit_finishers": [],
                     "sdirk2_min_stable_n": 8}},
        {"track": "implicit", "job": "sdirk.gamma_a21_scan", "key": "s04_t06",
         "summary": {"pairs": 17, "l_stable_gammas": 5}},
    ]


def _banned_ledger_fixture() -> list[dict]:
    """Artifact prose carrying every banned word, plus a failed point that does too.

    Artifact strings are written by prototype code and by exception messages, so a page
    that renders them raw is one unlucky word away from publishing nothing that cycle.
    """
    words = " ".join(sitegen.BANNED_WORDS)
    return [
        {"track": "adaptive", "job": "adaptive.suite_sweep", "key": "novel-point",
         "closes": f"the {words} question",
         "arithmetic": f"float64; this proves the first novel case",
         "summary": {"points": 1, "statuses": ["beats the breakthrough"]}},
        {"track": "implicit", "job": "sdirk.stiff_suite", "key": "first-case",
         "closes": f"whether SDIRK2 outperforms the state-of-the-art",
         "arithmetic": "float64",
         "summary": {"finishers": ["best-ever"], "note": words}},
        {"track": "implicit", "job": "sdirk.newton_iters", "key": "n1",
         "status": "failed",
         "error": "RuntimeError('this proves the first novel case')",
         "summary": {}},
    ]


def _validation_fixture() -> dict:
    """A stiff-labelled validation suite: the shape render_implicit reads its gap from."""
    return {
        "budget_cycles": 65536,
        "cost_model": "m0plus_fast",
        "rounding": "floor (ASRS), per HANDOFF 4.2",
        "methods": [
            {"kind": "classical", "name_or_hash": "midpoint", "order": 2, "stages": 2,
             "roles": ["anchor"]},
            {"kind": "discovered", "name_or_hash": "11e898cb2f01", "order": 2,
             "stages": 3, "roles": ["champion"]},
        ],
        "problems": [
            {"name": "buck_converter", "domain": "power electronics",
             "family": "oscillatory", "n_states": 2, "t_end": 25.0, "stiff": False,
             "stiffness_ratio": 3.2},
            {"name": "servo_load_step", "domain": "motion control", "family": "stiff",
             "n_states": 2, "t_end": 4.0, "stiff": True, "stiffness_ratio": 940.0},
            {"name": "robertson_scaled", "domain": "chemical kinetics",
             "family": "stiff", "n_states": 3, "t_end": 40.0, "stiff": True,
             "stiffness_ratio": 20500.0},
        ],
        "results": [
            {"problem": "buck_converter", "method": "midpoint", "steps": 3276,
             "cycles_per_step": 20, "q15_error": 0.0254, "float_error": 7.5e-05,
             "max_abs_q": 4743},
            {"problem": "servo_load_step", "method": "midpoint", "steps": 3276,
             "cycles_per_step": 20, "q15_error": 0.0116, "float_error": 1.1e-05,
             "max_abs_q": 9001},
        ],
        "verdicts": {
            "practical_problems_compared": 1,
            "practical_problems_won_by_discovered": 1,
            "practical_median_ratio_discovered_over_classical": 0.55,
            "stiff_problems_compared": 1,
            "stiff_problems_won_by_discovered": 0,
            "stiff_median_ratio_discovered_over_classical": 1.0004,
            "stiff_problems_total": 2,
            "stiff_problems_with_no_discovered_finisher": 1,
            "overall": "On this suite the best discovered method has lower Q15 error "
                       "than the best classical anchor on 1 of 3 problems.",
            "per_problem": {
                "buck_converter": {
                    "winner": "11e898cb2f01", "winner_kind": "discovered",
                    "winner_q15_error": 0.0014, "best_classical": "midpoint",
                    "best_classical_q15_error": 0.0254,
                    "best_discovered": "11e898cb2f01",
                    "best_discovered_q15_error": 0.0014,
                    "ratio_discovered_over_classical": 0.055},
                "servo_load_step": {
                    "winner": "midpoint", "winner_kind": "classical",
                    "winner_q15_error": 0.0116, "best_classical": "midpoint",
                    "best_classical_q15_error": 0.0116,
                    "best_discovered": "11e898cb2f01",
                    "best_discovered_q15_error": 0.0116,
                    "ratio_discovered_over_classical": 1.0004,
                    "finishers_classical": 5, "finishers_discovered": 1},
                "robertson_scaled": {
                    "winner": "midpoint", "winner_kind": "classical",
                    "winner_q15_error": 0.0894, "best_discovered": None,
                    "finishers_classical": 2, "finishers_discovered": 0},
            },
        },
    }


def _benchmark_fixture() -> dict:
    """Only the adaptive_results block matters here: it is where the library
    counterparts for both side classes come from."""
    return {
        "budget_cycles": 65536,
        "cost_model": "m0plus_fast",
        "methods": [],
        "problems": [{"name": "dahlquist"}, {"name": "rc_thermal"}],
        "caveats": ["Adaptive integrators choose their own step counts, so a same-work "
                    "comparison with a fixed-step run does not exist."],
        "adaptive_results": [
            {"problem": "dahlquist", "integrator": "RK45", "error": 6.6e-06,
             "rtol": 3.05e-05, "atol": 1.22e-04, "n_steps_accepted": 11, "nfev": 68,
             "status": "ok", "timing": {"median_s": 0.00061}},
            {"problem": "dahlquist", "integrator": "RK45", "error": 2.2e-08,
             "rtol": 3.05e-07, "atol": 1.22e-06, "n_steps_accepted": 40, "nfev": 260,
             "status": "ok", "timing": {"median_s": 0.0019}},
            {"problem": "dahlquist", "integrator": "Radau", "error": 1.4e-07,
             "rtol": 3.05e-05, "atol": 1.22e-04, "n_steps_accepted": 9, "nfev": 96,
             "status": "ok", "timing": {"median_s": 0.0033}},
            {"problem": "rc_thermal", "integrator": "BDF", "error": 5.1e-06,
             "rtol": 3.05e-05, "atol": 1.22e-04, "n_steps_accepted": 25, "nfev": 61,
             "status": "ok", "timing": {"median_s": 0.0028}},
        ],
    }


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")


LANE_HEADING = "Lane elites, ranked by cycles to tolerance"


def _lane_section(html: str) -> str:
    """The lane elites section on its own.

    Assertions about this section have to be scoped to it: the class pages are long, and
    a phrase satisfied by some other section would leave the property untested.
    """
    assert LANE_HEADING in html, "the lane elites section is missing"
    return html.split(LANE_HEADING, 1)[1].split("<h2>", 1)[0]


def _write_day_record(work: Path, lane: str, marker: int) -> Path:
    """One per-day lane record: the search log that stayed off the traceability list.

    One line per candidate, carrying the same kind of per-candidate numbers the elites
    document ranks. No page may open it, so the fixture puts a marker in it and the
    tests look for that marker everywhere.
    """
    path = work / f"{lane}_archive" / "2026-09-09.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"key": "d0d0d0d0d0d0d0d0", "lane": lane,
                                "median_cycles_at_target": marker,
                                "best_achieved_error": 1.0 / marker},
                               sort_keys=True) + "\n", encoding="utf-8")
    return path


def _lane_entry(lane: str, key: str, median: float, cost_basis: str) -> dict:
    """One elite entry, in the shape rk_harness.lanesearch writes it.

    The two lanes carry different blocks: an implicit entry has a gamma and a stability
    verdict, an adaptive entry has an embedded order and a controller. The page renders
    a different set of shape columns for each, so the fixture has to differ too.
    """
    entry = {
        "index": 239, "key": key, "shell": 0, "cost_basis": cost_basis,
        "record_hash": "9084a2a56d8c5011", "results_digest": "1dbe713cd0470e51",
        "cycle": 3001, "ts": "2026-09-09T23:14:53Z",
        "score": {"median_cycles_at_target": median, "elite_target": 0.0009765625,
                  "elite_target_key": "0.0009765625", "best_achieved_error": 2.04e-05,
                  "problems_at_elite_target": 8, "reached_at_elite_target": 8,
                  "targets_reached": 30, "targets_total": 32,
                  "worst_status": "reached"},
    }
    if lane == "implicit":
        entry["method"] = {"family": "sdirk2_dyadic_gamma", "gamma": "15/16",
                           "a21": "3/4", "newton_iters": 2,
                           "jacobian": "finite_difference", "order": 2, "stages": 2,
                           "arithmetic": "float64"}
        entry["stability"] = {"a_stable": True, "l_stable": False,
                              "r_at_infinity": "-127/225"}
    else:
        entry["method"] = {"family": "embedded_pair_s4_dyadic", "order": 3,
                           "order_hat": 2, "stages": 4, "fsal": False,
                           "nonzero_d_terms": 4}
        entry["controller"] = {"alpha": "1/4", "beta": "1/8", "safety": "7/8",
                               "clamp_lo": "1/4", "clamp_hi": "2", "table_bits": 14}
    return entry


_LANE_BASIS = {"implicit": "design_estimate_div32",
               "adaptive": "float_trajectory_q15_attempt_cost"}


def _lane_elites_fixture(lane: str, entries=None, meta=None) -> dict:
    """A lane elites document, schema lane-elites/1.

    Two entries rather than the cap of thirty-two: how many the document keeps is its
    own business, and what these tests arbitrate is what a page does with the entries it
    is handed and with the fields that describe them.
    """
    basis = _LANE_BASIS[lane]
    doc = {
        "_meta": {
            "lane": lane, "schema": "lane-elites/1", "elite_cap": 32,
            "elite_target": 0.0009765625, "elite_target_key": "0.0009765625",
            "n_elites": 2, "n_input": 64, "n_measured": 8896, "n_ranked": 64,
            "generated_cycle": 3811, "generated_ts": "2026-09-10T12:29:13Z",
            "lanesearch_code_hash": "6a5be0d8f26639ea", "cost_basis": basis,
            "cost_bases": {basis: {
                "grade": "a design estimate from the solver terms, not an "
                         "assembly-verified count",
                "direction": "estimate, unsigned",
                "includes": "the stage work and the fixed Newton iterations",
                "excludes": "derivative evaluations and every branch",
                "source": "sdirk.estimate_sdirk2_cycles"}},
            "not_a_page_source": "not on the traceability list",
            "not_comparable": "a record here is not comparable with a scored record",
        },
        "elites": [_lane_entry(lane, "60b0436d36617396", 2292.0, basis),
                   _lane_entry(lane, "7d0d9224e3df2c7b", 2358.5, basis)],
        "rule": "lowest median cycles at the elite target across the problems where the "
                "status is reached; ties broken by the count of targets reached, "
                "highest leading, then by record_hash",
        "statement": "The lane archive holds 8896 measured candidates under the current "
                     "code hash, of which this document ranked 64. The leading "
                     "candidate shows a median of 2292 cycles at the elite target.",
    }
    if entries is not None:
        doc["elites"] = entries
    if meta is not None:
        doc["_meta"] = meta
    return doc


def _empty_lane_elites_fixture(lane: str) -> dict:
    """The document a lane writes when nothing has been measured under its code hash.

    This is the state that ships on the next restart: the traceability decision moves
    lanesearch.code_hash(), so both lanes re-open every candidate and the document
    describes zero of them until the lanes have re-enumerated.
    """
    doc = _lane_elites_fixture(lane)
    doc["elites"] = []
    doc["_meta"].update({"n_elites": 0, "n_input": 0, "n_measured": 0, "n_ranked": 0})
    doc["statement"] = ("The lane archive holds 0 measured candidates under the current "
                        "code hash, of which this document ranked 0.")
    return doc


# --------------------------------------------------------------------------------------
# the class pages exist and are unconditional
# --------------------------------------------------------------------------------------

def test_three_class_pages_build_from_an_empty_work_dir(monkeypatch, tmp_path):
    """No ledger, no axes.json, no lane archive, no archive records: the state a fresh
    container starts in, and the state the next build will ship."""
    work = _env(monkeypatch, tmp_path)
    out = tmp_path / "docs"
    build(_empty_arch(), out)
    for name in CLASS_PAGES:
        page = out / name
        assert page.is_file(), name
        html = page.read_text(encoding="utf-8")
        assert html.lower().lstrip().startswith("<!doctype html>"), name
        assert sitegen.BANNER in html, name
        assert "<script" not in html.lower(), name
        check_banned(html)
    # nothing optional was written, and no class page pretended otherwise
    assert not (work / "validation").exists()
    for name in ("validation.html", "benchmark.html", "sidetrack.html"):
        assert not (out / name).exists(), name


def test_absent_sources_say_so_instead_of_showing_zeroes(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    out = tmp_path / "docs"
    build(_empty_arch(), out)
    for name in ("implicit.html", "adaptive.html"):
        html = (out / name).read_text(encoding="utf-8")
        assert "No measurements recorded under the current code hash" in html, name
        # no card row invented a zero that reads as a measurement
        assert "points measured" not in html, name
        # the off-list documents are named and reported absent, not quoted
        assert "rk-work/validation/axes.json" in html, name
        assert "not written yet" in html, name
    imp = (out / "implicit.html").read_text(encoding="utf-8")
    assert "rk-work/implicit_archive/elites.json" in imp
    adp = (out / "adaptive.html").read_text(encoding="utf-8")
    assert "rk-work/adaptive_archive/elites.json" in adp
    # with no stiff suite reported, the implicit page says the gap is unstated
    assert "has not reported a stiff subset" in imp


def test_class_pages_survive_when_only_some_sources_exist(monkeypatch, tmp_path):
    """Partial data is the ordinary case: a ledger with no benchmark, a benchmark with
    no ledger. Neither may fail the build or leave a half-rendered section."""
    work = _env(monkeypatch, tmp_path)
    _write_ledger(work, _ledger_fixture())
    out1 = tmp_path / "d1"
    build(_empty_arch(), out1)
    imp = (out1 / "implicit.html").read_text(encoding="utf-8")
    assert "sdirk.stiff_suite" in imp
    assert "predates the three-class tables" in imp      # no benchmark file yet
    assert "Library implicit integrators" not in imp     # the retired section stays gone
    check_banned(imp)

    work2 = _env(monkeypatch, tmp_path / "second")
    _write_json(work2 / "benchmark" / "results.json", _benchmark_fixture())
    out2 = tmp_path / "d2"
    build(_empty_arch(), out2)
    adp = (out2 / "adaptive.html").read_text(encoding="utf-8")
    # adaptive_results alone is the older document: the matched section says so, and
    # the attempt-cost section says it has nothing to draw rather than drawing nothing
    assert "predates the three-class tables" in adp
    assert "Library adaptive integrators" not in adp
    assert "no row with a per-attempt Q15 price" in adp
    assert "No measurements recorded under the current code hash" in adp
    check_banned(adp)


def test_the_three_class_pages_are_deterministic(monkeypatch, tmp_path):
    work = _env(monkeypatch, tmp_path)
    _write_ledger(work, _ledger_fixture())
    _write_json(work / "validation" / "results.json", _validation_fixture())
    _write_json(work / "benchmark" / "results.json", _benchmark_fixture())
    d1, d2 = tmp_path / "d1", tmp_path / "d2"
    build(_empty_arch(), d1)
    build(_empty_arch(), d2)
    assert _snapshot(d1) == _snapshot(d2)
    # and a direct render, twice, with no build around it
    assert render_implicit() == render_implicit()
    assert render_adaptive() == render_adaptive()
    assert render_explicit(_empty_arch()) == render_explicit(_empty_arch())


# --------------------------------------------------------------------------------------
# the nav carries three classes everywhere
# --------------------------------------------------------------------------------------

def _nav_hrefs(html: str) -> list[str]:
    rows = re.findall(r'<nav class="tabs[^"]*">(.*?)</nav>', html, re.S)
    assert rows, "no nav found"
    return [h for row in rows for h in re.findall(r'href="([^"]+)"', row)]


def test_every_page_shows_all_three_class_entries(monkeypatch, tmp_path):
    work = _env(monkeypatch, tmp_path)
    _write_ledger(work, _ledger_fixture())
    _write_json(work / "validation" / "results.json", _validation_fixture())
    _write_json(work / "benchmark" / "results.json", _benchmark_fixture())
    out = tmp_path / "docs"
    build(_empty_arch(), out)
    pages = sorted(p for p in out.rglob("*.html"))
    # the seven tabbed pages; an empty archive has no cell pages
    assert {p.name for p in pages} == {
        "index.html", "explicit.html", "implicit.html", "adaptive.html",
        "validation.html", "hypotheses.html", "methodology.html"}
    for page in pages:
        hrefs = _nav_hrefs(page.read_text(encoding="utf-8"))
        assert hrefs == ["index.html", "explicit.html", "implicit.html", "adaptive.html",
                         "validation.html", "hypotheses.html", "methodology.html"], page.name
    # the active tab lands on each class page itself
    for cls in ("explicit", "implicit", "adaptive"):
        html = (out / f"{cls}.html").read_text(encoding="utf-8")
        assert f'<a href="{cls}.html" class="on">{cls}</a>' in html


def test_the_nav_is_one_row_and_the_ledger_lives_in_methodology(monkeypatch, tmp_path):
    """The three-row nav of fourteen tabs became one row of seven (D41). The ledger lost
    its tab: its rules are the methodology page's #ledger section."""
    work = _env(monkeypatch, tmp_path)
    _write_ledger(work, _ledger_fixture())
    out = tmp_path / "docs"
    build(_empty_arch(), out)
    html = (out / "index.html").read_text(encoding="utf-8")
    rows = re.findall(r'<nav class="tabs([^"]*)">(.*?)</nav>', html, re.S)
    assert [cls.strip() for cls, _body in rows] == [""]
    hrefs = re.findall(r'href="([^"]+)"', rows[0][1])
    # a ledger alone is not evidence for the validation tab
    assert hrefs == ["index.html", "explicit.html", "implicit.html", "adaptive.html",
                     "hypotheses.html", "methodology.html"]
    assert len(hrefs) <= 8
    assert not (out / "sidetrack.html").exists()
    assert 'href="sidetrack.html"' not in html
    meth = (out / "methodology.html").read_text(encoding="utf-8")
    assert '<h2 id="ledger">Measurement ledger</h2>' in meth


def test_the_index_leads_with_three_class_cards(monkeypatch, tmp_path):
    work = _env(monkeypatch, tmp_path)
    _write_ledger(work, _ledger_fixture())
    _write_json(work / "validation" / "results.json", _validation_fixture())
    out = tmp_path / "docs"
    build(_stocked_arch(), out)
    html = (out / "index.html").read_text(encoding="utf-8")
    for cls in ("explicit", "implicit", "adaptive"):
        assert f'card klass k-{cls}' in html, cls
        assert f'<a href="{cls}.html">{cls} methods</a>' in html, cls
    # explicit leads: its card is the earliest of the three in document order
    order = [html.index(f"card klass k-{c}") for c in ("explicit", "implicit", "adaptive")]
    assert order == sorted(order)
    # each card names the file its number came from; without a benchmark document the
    # explicit card counts archive records and the side classes count ledger points
    assert "from the run archive" in html
    assert "from the measurement ledger" in html
    # the stiff gap is a fact about explicit tableaus, so it explains the implicit card
    # with its own source instead of standing in as the implicit class's number (D41)
    assert ("stiff validation problems every discovered explicit tableau overflows Q15 "
            "(validation/results.json)") in html
    assert "from validation/results.json" not in html
    # the archive itself moved off the index
    assert "elite grid heatmap" not in html
    assert 'href="explicit.html"' in html


# --------------------------------------------------------------------------------------
# banned words cannot reach a page, whatever an artifact says
# --------------------------------------------------------------------------------------

def test_artifact_prose_carrying_every_banned_word_still_publishes(monkeypatch, tmp_path):
    work = _env(monkeypatch, tmp_path)
    _write_ledger(work, _banned_ledger_fixture())
    out = tmp_path / "docs"
    build(_empty_arch(), out)                    # build() checks before it writes
    for name in CLASS_PAGES + ("methodology.html", "index.html"):
        html = (out / name).read_text(encoding="utf-8")
        check_banned(html)
    # softened, not dropped: the reader still gets the message
    ledger_page = (out / "methodology.html").read_text(encoding="utf-8")
    assert "RuntimeError" in ledger_page
    assert "shows the earliest new case" in ledger_page
    imp = (out / "implicit.html").read_text(encoding="utf-8")
    assert "does better than the leading" in imp     # outperforms / state-of-the-art
    assert "earliest-case" in imp and "first-case" not in imp   # the point key too
    assert "record" in imp                          # best-ever, inside a summary list
    adp = (out / "adaptive.html").read_text(encoding="utf-8")
    assert "new-point" in adp and "novel-point" not in adp
    assert "the new earliest does better than" in adp   # the whole banned list, softened
    assert "adaptive.suite_sweep" in adp


def test_the_shipped_class_prose_passes_the_guard():
    """The prose on these pages is written in this repository, so nothing softens it.

    A single banned word in a module constant would fail check_banned on every cycle,
    not once, and the site would publish nothing until someone noticed.
    """
    for text in (sitegen._CLASS_BOUNDARY, sitegen._EXPLICIT_CLASS,
                 sitegen._IMPLICIT_CLASS, sitegen._ADAPTIVE_CLASS,
                 sitegen._MATCHED_HOW,
                 sitegen._NO_POINTS, sitegen._OFF_LIST_RULE, sitegen._NEVER_SAME_WORK,
                 sitegen._NOT_THESE_WHERE, sitegen._LANE_ELITES_METRIC,
                 sitegen._LANE_ELITES_UNSCORED, sitegen._LANE_ELITES_ABSENT,
                 sitegen._LANE_ELITES_DAMAGED, sitegen._LANE_ELITES_EMPTY,
                 sitegen._LANE_ELITES_REFILL, sitegen._LANE_COUNTS_ABSENT,
                 sitegen._LANE_RANK_NOTE, sitegen._MODEL_NOTE) + tuple(
                     sitegen._LANE_ARITHMETIC.values()) + tuple(
                     sitegen._CLASS_GLANCE.values()):
        check_banned(text)
        assert chr(0x2014) not in text and chr(0x2013) not in text
    for head, body in sitegen._NOT_THESE_NUMBERS:
        check_banned(head)
        check_banned(body)
    for _anchor, term, paras in sitegen._GLOSSARY:
        check_banned(term)
        for para in paras:
            check_banned(para)
            assert chr(0x2014) not in para and chr(0x2013) not in para
    for _href, label in sitegen._NAV_ITEMS:
        check_banned(label)
    # the fixed labels the class-page charts print
    for label in ([g[1] for g in sitegen._JAC_GROUPS] + [p[1] for p in sitegen._ATTEMPT_PARTS]
                  + [b[1] for b in sitegen._CENSUS_BARS]):
        check_banned(label)
    for _label, short, cells in sitegen._class_facts():
        check_banned(short)
        for cell in cells:
            check_banned(cell)
            assert chr(0x2014) not in cell and chr(0x2013) not in cell
    check_banned(sitegen._class_side_table())
    assert chr(0x2014) not in sitegen._class_side_table()


def test_no_css_selector_spells_a_banned_word():
    """The style block is inlined into every page, so :first-child would fail the whole
    site build. :nth-child(1) says the same thing and survives the guard."""
    check_banned(sitegen._STYLE)
    assert ":first-child" not in sitegen._STYLE and ":first-of-type" not in sitegen._STYLE


# --------------------------------------------------------------------------------------
# _PRESENT drops exactly the conditional pages whose files are missing
# --------------------------------------------------------------------------------------

def test_present_set_filters_the_nav_and_resets_after_build(monkeypatch, tmp_path):
    work = _env(monkeypatch, tmp_path)
    out1 = tmp_path / "d1"
    build(_empty_arch(), out1)
    idx = (out1 / "index.html").read_text(encoding="utf-8")
    for href in ('href="validation.html"', 'href="benchmark.html"',
                 'href="sidetrack.html"'):
        assert href not in idx, href
    for href in CLASS_HREFS:
        assert href in idx, href

    _write_json(work / "validation" / "results.json", _validation_fixture())
    out2 = tmp_path / "d2"
    build(_empty_arch(), out2)
    idx2 = (out2 / "index.html").read_text(encoding="utf-8")
    assert 'href="validation.html"' in idx2
    assert 'href="benchmark.html"' not in idx2
    assert 'href="sidetrack.html"' not in idx2

    # the flag never leaks out of build(): a direct render afterwards has no tab
    assert sitegen._PRESENT == frozenset()
    assert 'href="validation.html"' not in render_index(_empty_arch())


def test_any_one_evidence_source_raises_the_validation_tab(monkeypatch, tmp_path):
    """validation.html now carries three sections, so any one of their three sources
    is enough to write it, and the other two sections say in words what is absent."""
    for i, (rel, doc) in enumerate((
            ("validation/results.json", _validation_fixture()),
            ("benchmark/results.json", _benchmark_fixture()),
            ("falsification.json", {"verdict": "mixed"}))):
        work = _env(monkeypatch, tmp_path / f"w{i}")
        _write_json(work / rel, doc)
        out = tmp_path / f"d{i}"
        build(_empty_arch(), out)
        assert (out / "validation.html").is_file(), rel
        for name in ("benchmark.html", "sidetrack.html", "falsification.html"):
            assert not (out / name).exists(), (rel, name)
        idx = (out / "index.html").read_text(encoding="utf-8")
        assert 'href="validation.html"' in idx, rel
        val = (out / "validation.html").read_text(encoding="utf-8")
        assert '<h2 id="speed">' in val and '<h2 id="falsification">' in val, rel
        check_banned(val)
    assert sitegen._PRESENT == frozenset()


def test_the_conditional_set_names_exactly_the_optional_pages():
    """Every entry of _CONDITIONAL must be a real nav href, and no class page may be in
    it: a conditional class page would vanish from a fresh build and take the whole
    equal-standing arrangement with it."""
    hrefs = {href for href, _label in sitegen._NAV_ITEMS}
    assert sitegen._CONDITIONAL <= hrefs
    assert sitegen._CONDITIONAL == {"validation.html"}
    for cls in sitegen.CLASS_ORDER:
        assert f"{cls}.html" not in sitegen._CONDITIONAL


# --------------------------------------------------------------------------------------
# each class page carries its own class, and only its own
# --------------------------------------------------------------------------------------

def test_the_ledger_is_split_by_track(monkeypatch, tmp_path):
    work = _env(monkeypatch, tmp_path)
    _write_ledger(work, _ledger_fixture())
    out = tmp_path / "docs"
    build(_empty_arch(), out)
    imp = (out / "implicit.html").read_text(encoding="utf-8")
    adp = (out / "adaptive.html").read_text(encoding="utf-8")
    for job in ("sdirk.stiff_suite", "sdirk.gamma_a21_scan"):
        assert job in imp and job not in adp, job
    for job in ("adaptive.suite_sweep", "adaptive.controller_gains"):
        assert job in adp and job not in imp, job
    # the ledger page is gone, and its successor section does not dump the points again
    assert not (out / "sidetrack.html").exists()
    led = (out / "methodology.html").read_text(encoding="utf-8").split(
        '<h2 id="ledger">', 1)[1].split("<h2 ", 1)[0]
    for job in ("sdirk.stiff_suite", "adaptive.suite_sweep"):
        assert job not in led, job


def test_library_integrators_land_on_the_matching_class(monkeypatch, tmp_path):
    """The library counterparts come from the matched-accuracy rows now: Radau, BDF and
    LSODA are implicit, RK23 and RK45 adaptive. The degenerate single-rung chart over
    adaptive_results and the library table beside it were retired (D41)."""
    _env(monkeypatch, tmp_path)
    bench = _matched_fixture()
    imp = render_implicit(benchmark=bench)
    adp = render_adaptive(benchmark=bench)
    assert "Radau" in imp and "Radau" not in adp
    assert "RK23" in adp and "RK23" not in imp
    # the never-same-work caveat travels with the library rows, once per page
    for html in (imp, adp):
        assert html.count("never a same-work comparison") == 1
        sec = html.split("at matched accuracy</h2>", 1)[1].split("<h2>", 1)[0]
        assert "<figure><figcaption>" in sec and "rhs evaluations" in sec
    # the explicit class has no library side, so the caveat has nothing to qualify
    assert "never a same-work comparison" not in render_explicit(_stocked_arch(),
                                                                 benchmark=bench)
    for gone in ("_workprec_chart", "_library_table", "_library_rows", "_SERIES_SWATCH"):
        assert not hasattr(sitegen, gone), gone


def test_the_class_map_agrees_with_benchcounts():
    """sitegen holds the scipy-integrator to class map as data rather than importing
    benchcounts, which pulls in the prototypes and the second pass. This is the test
    that keeps the copy honest."""
    from rk_harness import benchcounts

    for name in benchcounts.SCIPY_ADAPTIVE:
        assert sitegen._LIB_CLASS[name] == "adaptive", name
    for name in benchcounts.SCIPY_IMPLICIT:
        assert sitegen._LIB_CLASS[name] == "implicit", name
    assert set(sitegen._LIB_CLASS) >= set(benchcounts.SCIPY_ADAPTIVE + benchcounts.SCIPY_IMPLICIT)


def test_the_matched_chart_draws_a_line_only_through_distinct_reached_points(
        monkeypatch, tmp_path):
    """A solver that met two targets with one run has one mark, not a line to itself; a
    line appears once a second, cheaper or dearer run reached a target. A problem where
    nothing reached a target keeps its panel and says so."""
    _env(monkeypatch, tmp_path)

    def row(side, solver, problem, target, nfev, err, status="reached", arith="q15"):
        return {"class": "adaptive", "side": side, "solver": solver, "problem": problem,
                "arithmetic": arith, "target_error": target, "nfev": nfev,
                "achieved_error": err, "status": status}
    one_run = [row("ours", "bs32_q15", "pendulum", 1e-2, 160, 8e-4),
               row("ours", "bs32_q15", "pendulum", 1e-3, 160, 8e-4),
               row("library", "RK23", "pendulum", 1e-3, 130, 7e-4, arith="float64"),
               row("ours", "bs32_q15", "stiff_one", 1e-3, None, None, "never_reached")]
    chart = sitegen._matched_chart(one_run, "adaptive")
    assert chart == sitegen._matched_chart(list(one_run), "adaptive")
    assert chart.startswith("<figure><figcaption>")
    assert chart.count('role="img"') == 2                    # a panel per problem
    assert 'stroke="var(--s1)" stroke-width="2"' not in chart  # one run, one mark
    assert "meets target 0.01, 0.001" in chart
    assert ">no target reached<" in chart
    assert "bs32_q15 (ours, q15)" in chart and "RK23 (library, float64)" in chart
    assert 'aria-label="pendulum: achieved error against rhs evaluations for the adaptive' in chart
    assert "Source: benchmark/results.json, matched_accuracy (adaptive rows)" in chart
    _assert_chart_fit(chart)
    check_banned(chart)
    two_runs = one_run + [row("ours", "bs32_q15", "pendulum", 1e-4, 480, 9e-5)]
    assert 'stroke="var(--s1)" stroke-width="2"' in sitegen._matched_chart(two_runs,
                                                                          "adaptive")
    nothing = sitegen._matched_chart(one_run[3:], "adaptive")
    assert "<svg" not in nothing and "no mark to draw" in nothing


def test_every_matched_panel_names_its_size_and_shares_one_table(monkeypatch, tmp_path):
    """F13 remainder. The per-problem panels are the widest set of charts on the site,
    one per problem on each of the three class pages, and every one of them stopped at
    what it plotted without saying how much. role="img" also collapses a panel to a
    single announced node, so the marks inside it are out of reach: the panels now share
    one table of every plotted mark, the way the four elite grids share the elite table.
    """
    _env(monkeypatch, tmp_path)

    def row(side, solver, problem, target, nfev, err, arith="q15"):
        return {"class": "adaptive", "side": side, "solver": solver, "problem": problem,
                "arithmetic": arith, "target_error": target, "nfev": nfev,
                "achieved_error": err, "status": "reached"}

    rows = [row("ours", "bs32_q15", "pendulum", 1e-2, 160, 8e-4),
            row("ours", "bs32_q15", "pendulum", 1e-4, 480, 9e-5),
            row("library", "RK23", "pendulum", 1e-3, 130, 7e-4, arith="float64"),
            row("ours", "bs32_q15", "stiff_one", 1e-3, 220, 5e-4)]
    chart = sitegen._matched_chart(rows, "adaptive")
    assert ('aria-label="pendulum: achieved error against rhs evaluations for the '
            'adaptive class at matched accuracy, 3 marks"') in chart
    assert ('aria-label="stiff_one: achieved error against rhs evaluations for the '
            'adaptive class at matched accuracy, 1 mark"') in chart
    # both panels point at the one table, and the chart comes before it
    assert chart.count('aria-describedby="matched-adaptive-values"') == 2
    assert (chart.index('aria-describedby="matched-adaptive-values"')
            < chart.index('id="matched-adaptive-values"'))
    table = chart.split('id="matched-adaptive-values"', 1)[1]
    for cell in ("pendulum", "stiff_one", "bs32_q15", "RK23", "library", "ours"):
        assert cell in table, cell
    assert "4 rows" in table                       # every mark on both panels
    check_banned(chart)
    # the id follows the class, so the three class pages cannot collide on one id
    assert 'id="matched-implicit-values"' in sitegen._matched_chart(rows, "implicit")


# --------------------------------------------------------------------------------------
# the traceability rule
# --------------------------------------------------------------------------------------

def test_off_list_documents_are_named_but_never_quoted(monkeypatch, tmp_path):
    """axes.json, the per-day lane records and the shares document each say inside their
    own schema that they are not a source for a published number. The pages name them
    and report whether they exist; nothing else from them reaches a page.

    The elites documents here carry no "elites" list, which is the damaged shape: a
    document that does not read as a ranking publishes nothing, marker included.
    """
    work = _env(monkeypatch, tmp_path)
    marker = 987654321
    _write_json(work / "validation" / "axes.json",
                {"version": "validation-axes/1", "marker_value": marker,
                 "results_tolerance": [{"cycles": marker}]})
    _write_json(work / "implicit_archive" / "elites.json",
                {"schema": "lane-elites/1", "n_measured": marker, "entries": []})
    _write_json(work / "adaptive_archive" / "elites.json",
                {"schema": "lane-elites/1", "n_measured": marker, "entries": []})
    for lane in ("implicit", "adaptive"):
        _write_day_record(work, lane, marker)
    share = 0.777777777
    _write_json(work / "schedule" / "shares.json",
                {"schema": "schedule-shares/1", "lanes": {"explicit": {"share": share}}})
    out = tmp_path / "docs"
    build(_empty_arch(), out)
    for page in sorted(out.rglob("*.html")):
        html = page.read_text(encoding="utf-8")
        assert str(marker) not in html, page.name
        assert "0.7777" not in html, page.name
    imp = (out / "implicit.html").read_text(encoding="utf-8")
    assert "rk-work/validation/axes.json" in imp
    assert "written" in imp and "not written yet" not in imp.split("Further evidence")[1]
    assert "Only the owner can add one to the list." in imp


def test_the_loaders_tolerate_absence_and_rubbish(monkeypatch, tmp_path):
    work = _env(monkeypatch, tmp_path)
    assert sitegen._load_validation_axes() is None
    assert sitegen._load_lane_archive("implicit") is None
    assert sitegen._load_lane_archive("adaptive") is None
    assert sitegen._load_lane_archive("explicit") is None      # no lane archive exists
    assert sitegen._load_shares() is None
    (work / "validation").mkdir(parents=True, exist_ok=True)
    (work / "validation" / "axes.json").write_text("{ not json", encoding="utf-8")
    assert sitegen._load_validation_axes() is None             # unreadable is absent
    _write_json(work / "validation" / "axes.json", {"version": "validation-axes/1"})
    assert sitegen._load_validation_axes() == {"version": "validation-axes/1"}


# --------------------------------------------------------------------------------------
# one canonical invariants list
# --------------------------------------------------------------------------------------

def test_the_invariants_render_identically_on_every_page_that_needs_them(
        monkeypatch, tmp_path):
    work = _env(monkeypatch, tmp_path)
    _write_ledger(work, _ledger_fixture())
    out = tmp_path / "docs"
    build(_empty_arch(), out)
    block = sitegen._not_these_numbers()
    for name in ("implicit.html", "adaptive.html"):
        html = (out / name).read_text(encoding="utf-8")
        assert block in html, name
        assert html.count(block) == 1, name        # said once per page
    # and the constant is what both class pages and the ledger render
    assert len(sitegen._NOT_THESE_NUMBERS) >= 4
    assert "Not scored." in block and "Not Q15." in block


def test_the_ledger_rules_live_in_methodology_and_the_points_on_their_class_pages(
        monkeypatch, tmp_path):
    """sidetrack.html used to be treated as a permanent URL that "changes role rather
    than being deleted". D41 retired it: the site dropped to seven tabs, and build()
    deletes the old file. The ledger's rules and its failed points are now
    methodology.html#ledger, and every measured point is on its own class page."""
    work = _env(monkeypatch, tmp_path)
    _write_ledger(work, _ledger_fixture() + _banned_ledger_fixture()[2:])
    out = tmp_path / "docs"
    out.mkdir()
    (out / "sidetrack.html").write_text("the old ledger page", encoding="utf-8")
    build(_empty_arch(), out)
    assert not (out / "sidetrack.html").exists()
    html = (out / "methodology.html").read_text(encoding="utf-8")
    sec = html.split('<h2 id="ledger">Measurement ledger</h2>', 1)[1].split("<h2 ", 1)[0]
    assert "The code-hash rule" in sec and "The retry policy" in sec
    assert "The whole ledger" not in html            # the flattened dump is gone
    # the failed point is reported here, not on a class page
    assert "did not complete" in sec
    assert "shows the earliest new case" in sec
    for name in ("implicit.html", "adaptive.html"):
        assert "shows the earliest new case" not in (out / name).read_text(encoding="utf-8")
    # every measured point appears on its own class page ...
    imp = (out / "implicit.html").read_text(encoding="utf-8")
    adp = (out / "adaptive.html").read_text(encoding="utf-8")
    for p in _ledger_fixture():
        page = imp if p["track"] == "implicit" else adp
        assert f'>{p["key"]}<' in page, p["key"]
    # ... in an order that is a function of the ledger, not of the order it was written
    doc = sitegen._load_sidetrack()
    for track, keys in (("adaptive", ["a1o8_b0o1", "buck_converter"]),
                        ("implicit", ["s04_t06", "servo_load_step"])):
        assert [e["key"] for e in sitegen._track_entries(doc, track)] == keys, track


def test_the_ledger_section_direct_is_deterministic_and_safe():
    data = {"ledger": [{"ts": "2026-09-04T00:00:00Z", "cycle": 3, "track": "implicit",
                        "job": "sdirk.newton_iters", "key": "n2", "status": "ok",
                        "code_hash": "abc123def456", "artifact": "sidetrack/x/n2.json",
                        "summary": {"newton_iters": 2}}],
            "artifacts": {}}
    html = sitegen._ledger_section(data)
    assert html == sitegen._ledger_section(data)
    assert "The ledger holds 1 point across 1 job" in html
    check_banned(html)
    assert "<script" not in html.lower()
    absent = sitegen._ledger_section(None)
    assert "has not been written" in absent and ">0<" not in absent


# --------------------------------------------------------------------------------------
# the hub row: absence is not a zero, and a line is not a point
# --------------------------------------------------------------------------------------

def test_the_hub_states_absence_rather_than_printing_a_zero(monkeypatch, tmp_path):
    """The state a fresh work directory is in, which is the state a new epoch ships in.

    A card reading "0 measured ledger points, from the measurement ledger" makes two
    false claims at once: that a ledger was read, and that reading it returned nothing.
    Neither happened. The class pages already got this right; the hub is where the three
    numbers sit side by side, so it is the page where a zero does the most damage.
    """
    _env(monkeypatch, tmp_path)
    html = render_index(_empty_arch())
    cards = html.split('<div class="cards">')[1].split("</div></div>")[0]
    assert ">0<" not in cards, "a class card carries a bare zero"
    assert "from the measurement ledger" not in cards, "claims a ledger that was never written as a source"
    assert cards.count("not measured") == 2          # implicit and adaptive
    assert "no measurement ledger has been written yet" in cards
    assert "not started" in cards                     # explicit, with an empty archive
    assert cards.count("unmeasured") == 3
    for page in CLASS_PAGES:                          # all three still reachable
        assert f'href="{page}"' in html
    check_banned(html)


def test_the_hub_separates_a_missing_ledger_from_an_empty_one(monkeypatch, tmp_path):
    """Two different facts, and the run passes through both, in this order."""
    _env(monkeypatch, tmp_path)
    other_class_only = [e for e in _ledger_fixture() if e["track"] == "adaptive"]
    doc = {"ledger": [dict(e, status="ok", code_hash="c0ffee", ts="2026-09-04T00:00:00Z")
                      for e in other_class_only]}
    html = render_index(_empty_arch(), sidetrack=doc)
    assert "the ledger records no points for this class yet" in html
    assert "no measurement ledger has been written yet" not in html


def test_the_hub_counts_points_and_not_ledger_lines(monkeypatch, tmp_path):
    """Re-measuring a point after the executor changes leaves two lines for one point.

    The caption says "one per parameter point in the plan", so the number has to be a
    count of points or the caption is false. It said 80 for adaptive when the plan held
    56, and the hub prints that figure beside the archive record count.
    """
    _env(monkeypatch, tmp_path)
    one_point = {"track": "adaptive", "job": "adaptive.suite_sweep", "key": "buck",
                 "status": "ok", "summary": {"points": 1}}
    doc = {"ledger": [dict(one_point, code_hash="aaaa", ts="2026-09-01T00:00:00Z"),
                      dict(one_point, code_hash="bbbb", ts="2026-09-08T00:00:00Z")]}
    html = render_index(_empty_arch(), sidetrack=doc)
    assert ">1</div>" in html, "two lines for one point counted as two points"
    assert ">2</div>" not in html


def test_the_code_hash_card_names_the_latest_measurement_not_the_last_alphabetically(
        monkeypatch, tmp_path):
    """These are digests. Their lexicographic order carries no information, so taking
    the last one advertises whichever hash happens to sort highest as the code the
    readings ran under. The live site showed a98acb39fb4f while the executor had
    already moved to 305c692a1b5c085a, which sorts lower.
    """
    _env(monkeypatch, tmp_path)
    superseded, current = "a98acb39fb4f31ca", "305c692a1b5c085a"
    assert superseded > current                        # the ordering that caused it
    rows = [{"track": "adaptive", "job": "adaptive.suite_sweep", "key": "p1",
             "status": "ok", "code_hash": superseded, "ts": "2026-09-01T00:00:00Z",
             "summary": {"points": 1}},
            {"track": "adaptive", "job": "adaptive.suite_sweep", "key": "p2",
             "status": "ok", "code_hash": current, "ts": "2026-09-08T22:24:19Z",
             "summary": {"points": 1}}]
    html = render_adaptive(sidetrack={"ledger": rows})
    assert current[:12] in html
    assert superseded[:12] not in html.split("code hash")[1][:400]


def test_the_points_caption_says_so_when_lines_and_points_differ(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    pt = {"track": "implicit", "job": "sdirk.stiff_suite", "key": "k1", "status": "ok",
          "summary": {"finishers": []}}
    doc = {"ledger": [dict(pt, code_hash="aaaa", ts="2026-09-01T00:00:00Z"),
                      dict(pt, code_hash="bbbb", ts="2026-09-08T00:00:00Z")]}
    html = render_implicit(sidetrack=doc)
    assert "measured again after the executor changed" in html
    plain = render_implicit(sidetrack={"ledger": [dict(pt, code_hash="aaaa",
                                                       ts="2026-09-01T00:00:00Z")]})
    assert "measured again" not in plain
    assert "one per parameter point in the plan" in plain


# --------------------------------------------------------------------------------------
# matched accuracy: the comparison against real counterparts
# --------------------------------------------------------------------------------------

def _matched_fixture() -> dict:
    """A benchmark document carrying the three-class tables benchmark.py now writes."""
    def row(cls, side, solver, problem, arith, target, achieved, steps, nfev,
            cycles=None, grade="model", status="reached"):
        return {"class": cls, "side": side, "solver": solver, "problem": problem,
                "arithmetic": arith, "target_error": target,
                "achieved_error": achieved, "n_steps_accepted": steps, "nfev": nfev,
                "analytic_cycles_total": cycles, "cost_grade": grade, "status": status}
    def skipped(cls, side, solver, problem, arith, target, why):
        r = row(cls, side, solver, problem, arith, target, None, None, None, None,
                grade="none", status="skipped")
        r["reason"] = why
        return r
    return {
        "matched_accuracy": [
            row("explicit", "ours", "rk4", "dc_motor", "q15", 1e-3, 8.2e-4, 26, 104, 462),
            row("explicit", "ours", "rk4", "pendulum", "q15", 1e-4, 9.1e-5, 52, 208, 858),
            row("explicit", "ours", "rk4", "quaternion", "q15", 1e-5, 9.6e-6, 104, 416,
                60588),
            # the arithmetic control: float64, and the document grades its cycles "none"
            row("explicit", "ours", "rk4_float64", "dc_motor", "float64", 1e-3, 8.0e-4,
                26, 104, None, grade="none"),
            row("implicit", "ours", "sdirk2_fd_jac", "enzyme_qssa", "float64",
                1e-3, 9.1e-4, 176, 900, 58000),
            skipped("implicit", "ours", "sdirk2_analytic_jac", "enzyme_qssa", "float64",
                    1e-3, "no analytic Jacobian is derived for this problem"),
            row("implicit", "library", "Radau", "enzyme_qssa", "float64",
                1e-3, 4.0e-7, 88, 795, None, grade="absent"),
            row("implicit", "library", "Radau", "robertson_scaled", "float64",
                1e-3, None, None, None, None, grade="absent", status="never_reached"),
            row("adaptive", "ours", "bs32_q15", "pendulum", "q15", 1e-3, 2.0e-3, 40, 160,
                3978, status="not_reached"),
            row("adaptive", "library", "RK23", "pendulum", "float64", 1e-3, 7.0e-4,
                31, 130, None, grade="absent"),
        ],
        "implicit_budget": {
            "enzyme_qssa": {"steps_at_budget": 176, "error": 1.46e-06},
            "robertson_scaled": {"steps_at_budget": 100, "error": None},
        },
        "verdicts": {"three_class": {
            "per_class": {
                "explicit": "The explicit class carries 1 row of ours and 0 library rows.",
                "implicit": "The implicit class carries 1 row of ours and 1 library row.",
                "adaptive": "The adaptive class carries 1 row of ours and 1 library row.",
            },
            "adaptive_pair": "SciPy RK23 runs the same tableau our pair runs.",
            "stiff": "On the stiff application problems, 9 of 12 solvers reached the target.",
            "cost_grades": "A cost grade says whether a cycle count is measured or modelled.",
        }},
    }


def test_each_class_page_publishes_its_matched_accuracy_rows(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    bench = _matched_fixture()
    pages = {
        "explicit": render_explicit(_stocked_arch(), benchmark=bench),
        "implicit": render_implicit(benchmark=bench),
        "adaptive": render_adaptive(benchmark=bench),
    }
    # the verdict is counted from the rows the section draws, not quoted from the
    # benchmark, whose sentence names JSON blocks the page never shows
    expect = {
        "explicit": "Our solvers reached 4 of their 4 targets;",
        "implicit": "Our solvers reached 1 of their 1 targets and the library solvers 1 of "
                    "their 2;",
        "adaptive": "Our solvers reached 0 of their 1 targets and the library solvers 1 of "
                    "their 1;",
    }
    for cls, html in pages.items():
        assert "Measured against real counterparts, at matched accuracy" in html, cls
        assert expect[cls] in html, cls
        assert bench["verdicts"]["three_class"]["per_class"][cls] not in html, cls
        check_banned(html)
    # the median of the priced rows, not their maximum: 462, 858 and 60588 are three
    # different claims about what a reached target costs
    assert "median of 858 analytic cycles" in pages["explicit"]
    # a skipped row never ran, so it is counted apart from the targets we missed
    assert ("A further 1 row was skipped and never ran: no analytic Jacobian is derived "
            "for this problem.") in pages["implicit"]
    # and it is collapsed out of the table, named once underneath it
    assert ("A further 1 row is left out of the table because it never ran: "
            "sdirk2_analytic_jac on enzyme_qssa") in pages["implicit"]
    assert "Every measured row, with its cost grade (3 rows)" in pages["implicit"]
    assert "Every row, with its cost grade (4 rows)" in pages["explicit"]
    # rows land on their own class page and nowhere else
    assert "sdirk2_fd_jac" in pages["implicit"]
    assert "sdirk2_fd_jac" not in pages["adaptive"]
    assert "RK23" in pages["adaptive"] and "RK23" not in pages["implicit"]
    assert "rk4" in pages["explicit"]


def test_the_class_specific_sentence_goes_only_to_its_own_class(monkeypatch, tmp_path):
    """The RK23 head-to-head is an adaptive fact and the stiff sentence an implicit one.
    Printing either on the wrong page would attribute a measurement to a class that did
    not produce it."""
    _env(monkeypatch, tmp_path)
    bench = _matched_fixture()
    adaptive = render_adaptive(benchmark=bench)
    implicit = render_implicit(benchmark=bench)
    pair = bench["verdicts"]["three_class"]["adaptive_pair"]
    stiff = bench["verdicts"]["three_class"]["stiff"]
    assert pair in adaptive and pair not in implicit
    assert stiff in implicit and stiff not in adaptive
    # the benchmark writes one stiff verdict for all three classes. Where it names a
    # solver this class did not run, it is left out: printed above a single-class table
    # it sends the reader looking for rows that are on another tab.
    bench["verdicts"]["three_class"]["stiff"] = (
        "On the stiff problems 2 of 3 solvers reached the target (Radau, RK23) and the "
        "rest did not (bs32_q15).")
    assert "On the stiff problems 2 of 3" not in render_implicit(benchmark=bench)


def test_a_row_that_missed_its_target_is_kept_and_says_so(monkeypatch, tmp_path):
    """A target a method cannot reach is a result about the method. Dropping the row
    would leave a table where every method reaches everything."""
    _env(monkeypatch, tmp_path)
    html = render_adaptive(benchmark=_matched_fixture())
    assert "not_reached" in html
    assert "did not" in html and "status column" in html


def test_the_cost_grade_travels_with_the_row(monkeypatch, tmp_path):
    """Whether a cycle count is measured, modelled or absent is a property of how the
    row was produced, so it is displayed rather than inferred at render time."""
    _env(monkeypatch, tmp_path)
    html = render_implicit(benchmark=_matched_fixture())
    assert "<td>model</td>" in html and "<td>absent</td>" in html
    assert "cost grade" in html


def test_the_implicit_budget_table_is_implicit_only(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    bench = _matched_fixture()
    implicit = render_implicit(benchmark=bench)
    assert "<h2>SDIRK2 error as the cycle budget grows</h2>" in implicit
    assert "enzyme_qssa" in implicit
    assert "SDIRK2 error as the cycle budget grows" not in render_adaptive(benchmark=bench)
    # "anchor" means rk4 and rk38 on this site, so the SDIRK2 ladder no longer uses it
    assert "The implicit anchor" not in implicit


def test_an_older_benchmark_document_says_so_rather_than_showing_nothing(
        monkeypatch, tmp_path):
    """The state every work directory is in until rk_harness.benchmark is re-run. An
    empty section would read as "we compared and found nothing"."""
    _env(monkeypatch, tmp_path)
    for html in (render_explicit(_stocked_arch(), benchmark={"adaptive_results": []}),
                 render_implicit(benchmark={"adaptive_results": []}),
                 render_adaptive(benchmark=None)):
        assert "Measured against real counterparts, at matched accuracy" in html
        assert "predates the three-class tables" in html
        assert "<td>ours</td>" not in html
        check_banned(html)


def test_the_matched_helpers_tolerate_rubbish(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    for bad in (None, {}, {"matched_accuracy": None}, {"matched_accuracy": "no"},
                {"matched_accuracy": [None, 3, {"class": "adaptive"}]},
                {"verdicts": "not a dict"}, {"verdicts": {"three_class": 7}}):
        assert isinstance(sitegen._matched_rows(bad, "adaptive"), list)
        assert isinstance(sitegen._three_class_text(bad, "per_class", "adaptive"), str)
        assert isinstance(sitegen._implicit_budget_table(bad), list)


# --------------------------------------------------------------------------------------
# the lane elites documents, published on the two class pages
# --------------------------------------------------------------------------------------

def test_a_lane_with_no_elites_document_names_it_and_still_states_the_metric(
        monkeypatch, tmp_path):
    """A fresh work directory has no elites document, and the section still has to say
    what the ranking would measure and what it would not be.

    Naming the missing document is the whole of the report: a reader who cannot see a
    ranking should be able to tell whether the lane measured nothing or the page simply
    did not look.
    """
    _env(monkeypatch, tmp_path)
    for cls, html in (("implicit", render_implicit()), ("adaptive", render_adaptive())):
        sec = _lane_section(html)
        assert f"rk-work/{cls}_archive/elites.json has not been written" in sec, cls
        assert "re-fills as the lane re-enumerates" in sec, cls
        # the metric, the arithmetic and the two negatives, on every render
        assert "cycles a candidate needs to reach it" in sec, cls
        assert "float64" in sec and "order-verified" in sec, cls
        # "not scored" is said once, by the invariants list after this section
        assert "Nothing here is scored either" not in sec, cls
        block = html.split("<h2>What these numbers are not</h2>", 1)[1]
        assert "<strong>Not scored.</strong>" in block, cls
        # the ladder links the glossary entry for the metric
        assert 'href="methodology.html#cycles-to-tolerance">axis-T ladder</a>' in sec, cls
        # nothing that reads as a measurement
        assert "candidates measured" not in sec, cls
        assert "<table>" not in sec, cls
        assert ">0<" not in sec, cls
        check_banned(html)


def test_the_state_that_ships_is_an_elites_document_that_ranks_nothing(
        monkeypatch, tmp_path):
    """The next restart moves lanesearch.code_hash(), so both documents describe zero
    candidates measured under the current hash until the lanes re-enumerate.

    That state has to render as words. A card reading zero, or an empty table, is read
    as a measurement that came back empty, which is a different claim from not having
    measured yet, and it is the claim a reader would carry away for the cycle or two
    the lanes take to re-fill.
    """
    work = _env(monkeypatch, tmp_path)
    for lane in ("implicit", "adaptive"):
        _write_json(work / f"{lane}_archive" / "elites.json",
                    _empty_lane_elites_fixture(lane))
    for cls, html in (("implicit", render_implicit()), ("adaptive", render_adaptive())):
        sec = _lane_section(html)
        assert "lists no ranked candidate" in sec, cls
        assert "would read as a measurement that came back empty" in sec, cls
        assert "re-fills as the lane re-enumerates" in sec, cls
        # the counts the document does carry are all zero, and none of them is printed
        assert "candidates measured" not in sec, cls
        assert "candidates ranked" not in sec, cls
        assert "holds 0 measured candidates" not in sec, cls
        assert "<table>" not in sec, cls
        assert ">0<" not in sec, cls
        # provenance is not a measurement, so the hash and the target still show
        assert "6a5be0d8f26639ea" in sec, cls
        assert "0.0009765625" in sec, cls
        check_banned(html)


def test_a_populated_lane_elites_document_publishes_its_ranking_and_its_own_rule(
        monkeypatch, tmp_path):
    """The document states the order it applied; the page renders that field rather than
    restating it, so the two cannot drift apart.

    Everything a reader needs to place these numbers is in the same section: the metric,
    the arithmetic, the code hash they were measured under, how many candidates were
    measured and ranked, and the cost basis each row was priced on.
    """
    work = _env(monkeypatch, tmp_path)
    docs = {lane: _lane_elites_fixture(lane) for lane in ("implicit", "adaptive")}
    for lane, doc in docs.items():
        _write_json(work / f"{lane}_archive" / "elites.json", doc)
    for cls, html in (("implicit", render_implicit()), ("adaptive", render_adaptive())):
        sec = _lane_section(html)
        doc = docs[cls]
        assert doc["rule"] in sec, cls                     # verbatim, not paraphrased
        # the summary sentence is not quoted: it rounds the leading median, which the
        # table prints exactly, and restates numbers the cards already carry
        assert doc["statement"] not in sec, cls
        # n_measured and n_ranked, with thousands separators as every card count has
        assert ">8,896<" in sec and ">64<" in sec, cls
        assert "candidates measured" in sec and "candidates ranked" in sec, cls
        assert "the leading 2 are listed below" in sec, cls
        assert "entries listed" not in sec, cls            # the card that said 2 three times
        assert '<details class="fold"><summary>The 2 listed entries' in sec, cls
        assert "6a5be0d8f26639ea" in sec, cls              # lanesearch_code_hash
        assert f"rk-work/{cls}_archive/elites.json" in sec, cls
        assert "60b0436d36617396" in sec and "7d0d9224e3df2c7b" in sec, cls
        assert "2292" in sec and "2358.5" in sec, cls
        assert "8 of 8" in sec and "30 of 32" in sec, cls
        assert _LANE_BASIS[cls] in sec, cls
        assert "assembly-verified count" in sec, cls       # the document's own grade
        check_banned(html)
    imp, adp = _lane_section(render_implicit()), _lane_section(render_adaptive())
    assert "15/16" in imp and "finite_difference" in imp   # gamma, jacobian policy
    assert "embedded order" in adp and "7/8" in adp        # order_hat, controller safety
    # the arithmetic sentence is the lane's own, because the two lanes differ; the
    # implicit reason (no Q15 LU factorization) is said once, in At a glance
    assert sitegen._LANE_ARITHMETIC["implicit"] in imp
    assert "no Q15 LU factorization" not in imp
    assert render_implicit().count("no Q15 LU factorization") == 1
    assert sitegen._LANE_ARITHMETIC["adaptive"] in adp
    assert "the cycle count is mixed" not in adp    # the cost basis fold states the mix


def test_a_damaged_elites_document_is_reported_unreadable_and_never_half_rendered(
        monkeypatch, tmp_path):
    """Every shape a broken document can arrive in: unparseable, not a dict, no list of
    entries, entries that are not dicts, no _meta, counts that came through as strings
    or nulls.

    None of these may raise, and none may put a coerced number on the page. A count
    read out of a damaged document is indistinguishable, once rendered, from a measured
    one, so a count that did not validate is reported absent in words instead.
    """
    work = _env(monkeypatch, tmp_path)
    path = work / "implicit_archive" / "elites.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    unreadable = (
        "{ not json at all",
        json.dumps([1, 2, 3]),
        json.dumps("a string"),
        json.dumps({"_meta": {"lane": "implicit"}, "elites": "not a list"}),
        json.dumps({"schema": "lane-elites/1", "n_measured": 987654321}),
    )
    for raw in unreadable:
        path.write_text(raw, encoding="utf-8")
        sec = _lane_section(render_implicit())
        assert "does not read as the document this page expects" in sec, raw[:40]
        assert "987654321" not in sec, raw[:40]
        assert "<table>" not in sec, raw[:40]
        check_banned(sec)
    # a list of entries that are not entries: the page reports what it can render
    _write_json(path, {"elites": ["a string", 7, None]})
    sec = _lane_section(render_implicit())
    assert "does not read as the document this page expects" not in sec
    assert "lists no ranked candidate" in sec
    # one usable entry, no _meta, and every count a string or a null
    _write_json(path, {
        "elites": [{"key": "abcdef0123456789", "cost_basis": None, "method": "not a dict",
                    "score": {"median_cycles_at_target": None,
                              "reached_at_elite_target": "8",
                              "problems_at_elite_target": 8,
                              "targets_reached": None, "targets_total": 32}}],
        "rule": 5, "statement": None,
        "_meta": {"n_measured": "8896", "n_ranked": None, "elite_cap": "32"}})
    sec = _lane_section(render_implicit())
    assert "abcdef0123456789" in sec
    assert "does not state how many candidates were measured" in sec
    assert "8896" not in sec                       # a string count is not a count
    assert "The 1 listed entry," in sec            # the rows it could render, counted
    assert '<div class="cards">' not in sec        # no count validated, so no card row
    assert "n/a" in sec                            # the cells that did not validate
    assert "Ranking rule, quoted from the document: 5" not in sec
    assert "The document states no ranking rule" in sec
    assert "not stamped in this document" in sec
    check_banned(render_implicit())


def test_a_lane_elites_document_lands_only_on_its_own_class_page(monkeypatch, tmp_path):
    """A candidate ranked in the implicit lane is not an adaptive result. Publishing one
    on the other class page would attribute a measurement to a class that never made
    it, which is the same mistake the archive boundary exists to prevent."""
    work = _env(monkeypatch, tmp_path)
    imp_doc = _lane_elites_fixture("implicit")
    imp_doc["elites"][0]["key"] = "aaaa1111aaaa1111"
    adp_doc = _lane_elites_fixture("adaptive")
    adp_doc["elites"][0]["key"] = "bbbb2222bbbb2222"
    _write_json(work / "implicit_archive" / "elites.json", imp_doc)
    _write_json(work / "adaptive_archive" / "elites.json", adp_doc)
    imp = _lane_section(render_implicit())
    adp = _lane_section(render_adaptive())
    assert "aaaa1111aaaa1111" in imp and "aaaa1111aaaa1111" not in adp
    assert "bbbb2222bbbb2222" in adp and "bbbb2222bbbb2222" not in imp
    assert "gamma" in imp and "gamma" not in adp
    assert "embedded order" in adp and "embedded order" not in imp
    assert "rk-work/adaptive_archive/elites.json" not in imp
    assert "rk-work/implicit_archive/elites.json" not in adp


def test_no_number_from_a_per_day_lane_record_reaches_a_page(monkeypatch, tmp_path):
    """The per-day records were kept off the traceability list when the elites document
    was admitted to it.

    They are a search log at one line per candidate, and their per-candidate numbers
    invite comparison with a scored archive record, which is the comparison that must
    never be made. The pages name the file and report that it exists; nothing opens it.
    """
    work = _env(monkeypatch, tmp_path)
    marker = 987654321
    for lane in ("implicit", "adaptive"):
        _write_json(work / f"{lane}_archive" / "elites.json", _lane_elites_fixture(lane))
        _write_day_record(work, lane, marker)
    out = tmp_path / "docs"
    build(_empty_arch(), out)
    for page in sorted(out.rglob("*.html")):
        html = page.read_text(encoding="utf-8")
        assert str(marker) not in html, page.name
        assert "d0d0d0d0d0d0d0d0" not in html, page.name
        assert "1.0125e-09" not in html, page.name
    imp = (out / "implicit.html").read_text(encoding="utf-8")
    assert "rk-work/implicit_archive/YYYY-MM-DD.jsonl" in imp
    assert "ranked against nothing" in imp
    # the elites document, on the same page, does publish its numbers
    assert "2292" in _lane_section(imp)


def test_the_off_list_rule_quotes_the_admitted_documents(monkeypatch, tmp_path):
    """The panel's closing sentence quotes the traceability rule, and the rule now names
    the two lane elites documents. If the quote and CLAUDE.md disagree, the page is
    telling a reader something about the project's own rules that is not true."""
    _env(monkeypatch, tmp_path)
    assert "adaptive_archive/elites.json" in sitegen._OFF_LIST_RULE
    assert "implicit_archive/elites.json" in sitegen._OFF_LIST_RULE
    for html in (render_implicit(), render_adaptive()):
        panel = html.split("Further evidence, not published here", 1)[1]
        assert "the side-track ledger with its artifacts" in panel
        # the elites documents are no longer listed as off-list rows
        assert "elites.json</span>" not in panel
        assert "YYYY-MM-DD.jsonl" in panel
        assert "rk-work/validation/axes.json" in panel
        assert "rk-work/schedule/shares.json" in panel


def test_the_lane_elites_section_is_deterministic_across_two_renders(
        monkeypatch, tmp_path):
    """Same document, same bytes. The section reads a stored timestamp and converts it
    for display, which is a pure function of the file; nothing here reads a clock."""
    work = _env(monkeypatch, tmp_path)
    for lane in ("implicit", "adaptive"):
        _write_json(work / f"{lane}_archive" / "elites.json", _lane_elites_fixture(lane))
        _write_day_record(work, lane, 987654321)
    assert render_implicit() == render_implicit()
    assert render_adaptive() == render_adaptive()
    assert (sitegen._lane_elites_section("implicit")
            == sitegen._lane_elites_section("implicit"))
    d1, d2 = tmp_path / "d1", tmp_path / "d2"
    build(_empty_arch(), d1)
    build(_empty_arch(), d2)
    assert _snapshot(d1) == _snapshot(d2)


def test_lane_prose_carrying_every_banned_word_still_publishes(monkeypatch, tmp_path):
    """The rule and the statement are written by rk_harness.lanesearch, not here.

    One unlucky word in a document this module does not own would fail check_banned on
    every cycle and publish nothing until somebody noticed. Every string that came out
    of the document goes through the vocabulary pass first, so the reader still gets
    the message and the site still builds.
    """
    work = _env(monkeypatch, tmp_path)
    words = " ".join(sitegen.BANNED_WORDS)
    for lane in ("implicit", "adaptive"):
        doc = _lane_elites_fixture(lane)
        doc["rule"] = ("the first entry beats every other, which proves it is "
                       "state-of-the-art")
        doc["statement"] = f"A novel breakthrough. {words}. This outperforms the rest."
        basis = "best-ever design estimate"
        doc["_meta"]["cost_basis"] = basis
        doc["_meta"]["cost_bases"] = {basis: {"grade": words, "source": "the first pass"}}
        for e in doc["elites"]:
            e["cost_basis"] = basis
            if lane == "implicit":
                e["method"]["jacobian"] = "first order difference"
            else:
                e["controller"]["safety"] = "novel"
        _write_json(work / f"{lane}_archive" / "elites.json", doc)
    out = tmp_path / "docs"
    build(_empty_arch(), out)                       # build() checks before it writes
    for name in CLASS_PAGES:
        check_banned((out / name).read_text(encoding="utf-8"))
    imp = _lane_section((out / "implicit.html").read_text(encoding="utf-8"))
    # softened, not dropped
    assert ("the earliest entry does better than every other, which shows it is leading"
            in imp)
    assert "A new advance" not in imp               # the statement is not quoted at all
    assert "record design estimate" in imp          # the cost basis name, softened
    assert "earliest order difference" in imp       # a value inside a shape column
    adp = _lane_section((out / "adaptive.html").read_text(encoding="utf-8"))
    assert "<td>new</td>" in adp                    # the controller safety value
    # the strip chart's own table prints the Jacobian policy, so it takes the same pass
    assert "earliest order difference" in imp.split('id="lane-implicit-values"', 1)[1]


def test_the_lane_charts_name_their_size_and_carry_their_numbers(monkeypatch, tmp_path):
    """F13 remainder, the two lane charts. Both draw a ranked document whose per-entry
    numbers a reader cannot otherwise reach: the strip dodges marks by Jacobian policy
    and the scatter merges entries that land on one point, so the table is the only
    place each entry's own values appear."""
    work = _env(monkeypatch, tmp_path)
    for lane in ("implicit", "adaptive"):
        _write_json(work / f"{lane}_archive" / "elites.json", _lane_elites_fixture(lane))
    out = tmp_path / "docs"
    build(_empty_arch(), out)
    imp = (out / "implicit.html").read_text(encoding="utf-8")
    adp = (out / "adaptive.html").read_text(encoding="utf-8")
    for html, tid, label in ((imp, "lane-implicit-values", "Implicit lane elites:"),
                             (adp, "lane-adaptive-values", "Adaptive lane elites:")):
        assert f'aria-describedby="{tid}"' in html, tid
        assert html.index(f'aria-describedby="{tid}"') < html.index(f'id="{tid}"')
        m = re.search(r'aria-label="' + label + r'[^"]+, (\d+) entr(?:y|ies)"', html)
        assert m, label
        # the count in the label is the number of rows in the table beside it
        body = html.split(f'id="{tid}"', 1)[1].split("</details>", 1)[0]
        assert f"({m.group(1)} row" in body, (label, m.group(1))


def test_the_lane_elites_helpers_tolerate_rubbish(monkeypatch, tmp_path):
    """The renderer is handed whatever the document holds. Every helper has to return a
    value for anything, because one exception here takes the whole site build with it."""
    _env(monkeypatch, tmp_path)
    assert sitegen._lane_elites_view("explicit")["state"] == "absent"
    assert sitegen._lane_elites_view("implicit")["state"] == "absent"
    for bad in (None, {}, "text", 7, [], {"n": "8"}, {"n": None}, {"n": True},
                {"n": -1}, {"n": 2.0}):
        assert sitegen._lane_int(bad, "n") is None
    assert sitegen._lane_int({"n": 0}, "n") == 0
    for bad in (None, {}, {"score": None}, {"score": {"a": "1", "b": 2}},
                {"score": {"a": 1}}):
        assert sitegen._lane_reached(bad, "a", "b") == "n/a"
    assert sitegen._lane_reached({"score": {"a": 1, "b": 2}}, "a", "b") == "1 of 2"
    assert sitegen._lane_field({"method": None}, "method", "gamma") is None
    assert sitegen._lane_field({}, "method", "gamma") is None
    assert sitegen._lane_cost_bases({}, []) == ""
    assert sitegen._lane_cost_bases(None, [{"cost_basis": 7}]) == ""
    assert "names this cost basis and does" in sitegen._lane_cost_bases(
        {"cost_bases": "not a dict"}, [{"cost_basis": "x"}])
    for cls in ("implicit", "adaptive"):
        table = sitegen._lane_elites_table(cls, [{}, {"score": "no"}])
        assert "n/a" in table
        check_banned(table)


# --------------------------------------------------------------------------------------
# one skeleton for the three class pages, and the charts on them (D41)
# --------------------------------------------------------------------------------------

_DYADIC_ARITH = ("tableau and stability algebra exact over Fractions; the measured order "
                 "is float64")
_FLOAT_ONLY = "float64 only; no Q15 effects are included"
_EXACT = "exact over Fractions; no float and no Q15 arithmetic is involved"


def _dyadic_rows() -> list[dict]:
    """Stability-scan rows in the shape sdirk.gamma_dyadic_scan writes them."""
    return [
        {"gamma": "1/16", "gamma_float": 0.0625, "r_at_infinity": "97",
         "r_at_infinity_float": 97.0, "a_stable": False, "measured_order": 2.0912},
        {"gamma": "5/16", "gamma_float": 0.3125, "r_at_infinity": "-7/25",
         "r_at_infinity_float": -0.28, "a_stable": True, "measured_order": 2.0062},
        # the same gamma under a second a21: R at infinity does not move, so one mark
        {"gamma": "5/16", "gamma_float": 0.3125, "r_at_infinity": "-7/25",
         "r_at_infinity_float": -0.28, "a_stable": True, "measured_order": 2.0062},
        {"gamma": "3/4", "gamma_float": 0.75, "r_at_infinity": "-5/9",
         "r_at_infinity_float": -0.5556, "a_stable": True, "measured_order": 2.0},
    ]


def _jac_cycles() -> dict:
    """sdirk.jacobian_cost's cycle breakdown: the same terms, plus the FD assembly."""
    base = {"combine_b": 20, "form_m": 26, "lu_factor": 84, "newton_iterations": 204,
            "stage_base": 14}
    return {"analytic": {"model": "m0plus_fast", "f_evals_per_step": 6,
                         "terms": dict(base, fd_jacobian_arith=0), "total": 348},
            "finite_difference": {"model": "m0plus_fast", "f_evals_per_step": 8,
                                  "terms": dict(base, fd_jacobian_arith=24),
                                  "total": 372}}


def _sweep_points(scale: float) -> list[dict]:
    return [{"tol": tol, "n_fevals": int(n * scale), "achieved_error": err,
             "status": "ok", "n_rejected": r}
            for tol, n, err, r in ((1e-3, 91, 1.5e-3, 0), (1e-5, 376, 7.4e-5, 2),
                                   (1e-7, 1666, 8.9e-7, 3))]


def _charted_ledger() -> list[dict]:
    """Points whose artifacts carry what the class-page charts draw, one job per chart."""
    gains = []
    for key, alpha, beta, rate in (("a1o8_b0o1", [1, 8], [0, 1], 0.0253),
                                   ("a1o8_b1o8", [1, 8], [1, 8], None),
                                   ("a1o4_b0o1", [1, 4], [0, 1], 0.0166),
                                   ("a1o4_b1o8", [1, 4], [1, 8], 0.25)):
        summary = ({"rejection_rate": rate, "total_rejected": 7, "total_accepted": 414,
                    "total_fevals": 1266, "max_error": 1.5e-05} if rate is not None
                   else {"rejection_rate": None, "finished": 0})
        gains.append({"track": "adaptive", "job": "adaptive.controller_gains", "key": key,
                      "arithmetic": _FLOAT_ONLY, "summary": summary,
                      "art": {"params": {"alpha": alpha, "beta": beta},
                              "problems": ["buck_converter", "pll_lock"],
                              "tolerance": 1e-06,
                              "points": [{"problem": "buck_converter",
                                          "status": "ok" if rate is not None
                                          else "underflow"}]}})
    census = {"arithmetic": _EXACT}
    return [
        {"track": "implicit", "job": "sdirk.gamma_dyadic_scan", "key": "s04",
         "arithmetic": _DYADIC_ARITH, "summary": {"candidates": 13, "a_stable": 10},
         "art": {"rows": _dyadic_rows()}},
        {"track": "implicit", "job": "sdirk.jacobian_cost", "key": "enzyme_qssa",
         "arithmetic": _FLOAT_ONLY,
         "summary": {"cycles_analytic": 348, "cycles_finite_difference": 372},
         "art": {"problem": "enzyme_qssa", "cycles": _jac_cycles()}},
        {"track": "adaptive", "job": "adaptive.suite_sweep", "key": "buck_converter",
         "arithmetic": _FLOAT_ONLY, "summary": {"points": 3, "finished": 3},
         "art": {"problem": "buck_converter", "pair": "Bogacki-Shampine 3(2)",
                 "points": _sweep_points(1.0)}},
        {"track": "adaptive", "job": "adaptive.suite_sweep", "key": "pll_lock",
         "arithmetic": _FLOAT_ONLY, "summary": {"points": 3, "finished": 3},
         "art": {"problem": "pll_lock", "pair": "Bogacki-Shampine 3(2)",
                 "points": _sweep_points(0.6)}},
        dict(census, track="adaptive", job="adaptive.pair_census", key="st3_s1",
             summary={"matrices": 64},
             art={"status": "ok", "params": {"stages": 3, "s_max": 1},
                  "counts": {"matrices": 64, "order2_consistent": 64,
                             "order3_consistent": 0, "both_solvable": 0}}),
        dict(census, track="adaptive", job="adaptive.pair_census", key="st3_s2",
             summary={"matrices": 512},
             art={"status": "ok", "params": {"stages": 3, "s_max": 2},
                  "counts": {"matrices": 512, "order2_consistent": 512,
                             "order3_consistent": 4, "both_solvable": 4}}),
        dict(census, track="adaptive", job="adaptive.pair_census", key="st4_s2",
             summary={"matrices": 0, "status": "capped"},
             art={"status": "capped", "params": {"stages": 4, "s_max": 2},
                  "space_size": 262144, "cap": 100000, "counts": {}}),
    ] + gains


def _attempt_rows() -> list[dict]:
    """adaptive_matched_tolerance rows: two Q15 prices and a library row with none."""
    def row(n_states, stage, estimate, total):
        return {"solver": "bs32_q15", "arithmetic": "q15", "n_states": n_states,
                "cost_grade": "upper_bound_unpriced_branches",
                "per_attempt_cycles": {"stage": stage, "estimate": estimate,
                                       "controller": 100, "branch_allowance": 47,
                                       "total": total}}
    return [row(2, 78, 56, 234), row(2, 78, 56, 234), row(3, 117, 84, 301),
            {"solver": "RK23", "arithmetic": "compiled_float64",
             "per_attempt_cycles": None}]


def _charted_benchmark() -> dict:
    bench = _matched_fixture()
    ladder_meta = {"budget_cycles": 65536, "cost_grade": "design_estimate",
                   "arithmetic": "float64"}
    bench["implicit_budget"]["enzyme_qssa"].update(ladder_meta, ladder=[
        {"factor": "1/2", "analytic_cycles": 32736, "error": 5.85e-06, "n": 88,
         "status": "ok"},
        {"factor": "1/1", "analytic_cycles": 65472, "error": 1.46e-06, "n": 176,
         "status": "ok"}])
    bench["implicit_budget"]["robertson_scaled"].update(ladder_meta, ladder=[
        {"factor": "1/1", "analytic_cycles": 65200, "error": None, "n": 100,
         "status": "diverged"},
        {"factor": "4/1", "analytic_cycles": 260800, "error": 5.0e-06, "n": 400,
         "status": "ok"}])
    bench["adaptive_matched_tolerance"] = _attempt_rows()
    return bench


def _charted_env(monkeypatch, tmp_path) -> Path:
    """Every source a class-page chart reads, at once."""
    work = _env(monkeypatch, tmp_path)
    _write_ledger(work, _charted_ledger())
    _write_json(work / "validation" / "results.json", _validation_fixture())
    _write_json(work / "benchmark" / "results.json", _charted_benchmark())
    for lane in ("implicit", "adaptive"):
        _write_json(work / f"{lane}_archive" / "elites.json", _lane_elites_fixture(lane))
    return work


def _figures_in(html: str) -> list[str]:
    return re.findall(r"<figure>(.*?)</figure>", html, re.S)


def _marks(html: str) -> list[dict]:
    """Every mark that carries hover text: where it lands, how wide it is, what it says.

    Captions, counts and source lines can all stay right while a chart plots the wrong
    field, swaps its axes or labels a segment with its neighbour's name. The chart tests
    read positions through this and assert value-to-pixel order, so those fail too.
    A path's position is the centre of its bounding box, which is the mark's centre for
    every shape _mark draws and the bar's middle for a rounded bar end.
    """
    out = []
    for m in re.finditer(r"<(circle|rect|path)\b([^>]*)><title>([^<]*)</title>", html):
        tag, attrs, title = m.groups()
        a = dict(re.findall(r'([a-zA-Z-]+)="([^"]*)"', attrs))
        if tag == "circle":
            cx, cy, w = float(a["cx"]), float(a["cy"]), 0.0
        elif tag == "rect":
            x, y, w, h = (float(a[k]) for k in ("x", "y", "width", "height"))
            cx, cy = x + w / 2, y + h / 2
        else:
            nums = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", a["d"])]
            xs, ys = nums[0::2], nums[1::2]
            cx, cy, w = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, max(xs) - min(xs)
        out.append({"tag": tag, "cx": cx, "cy": cy, "w": w, "title": htmlmod.unescape(title),
                    "fill": a.get("fill", ""), "stroke": a.get("stroke", "")})
    return out


def _mark_titled(marks: list[dict], text: str) -> dict:
    """The one mark whose hover text contains text."""
    found = [m for m in marks if text in m["title"]]
    assert len(found) == 1, (text, [m["title"] for m in found][:4])
    return found[0]


def _assert_proportional(pixels, values, log: bool = False, rel: float = 0.01) -> None:
    """Pixel positions are one linear function of the values, or of their logarithms.

    Any mark drawn from a different field, or on the other axis, breaks the common slope.
    """
    vs = [math.log10(v) if log else float(v) for v in values]
    slopes = [(pixels[i + 1] - pixels[i]) / (vs[i + 1] - vs[i]) for i in range(len(vs) - 1)]
    assert slopes[0] != 0, (pixels, values)
    for s in slopes[1:]:
        assert abs(s - slopes[0]) <= rel * abs(slopes[0]), (slopes, values)


def test_the_three_class_pages_share_one_skeleton(monkeypatch, tmp_path):
    """Lead, at a glance, cards, charts, matched accuracy; then, on the two side
    classes, the lane elites, the invariants once, and two folds at the foot."""
    _charted_env(monkeypatch, tmp_path)
    out = tmp_path / "docs"
    build(_stocked_arch(), out)
    glance = ["family", "search", "question", "problems", "arithmetic", "checked by",
              "cost basis", "sources"]
    for name in CLASS_PAGES:
        html = (out / name).read_text(encoding="utf-8")
        cls = name.split(".")[0]
        body = html.split("</header>", 1)[1]
        marks = [body.index('<p class="lead">'), body.index("<h2>At a glance</h2>"),
                 body.index('<div class="cards">'),
                 body.index("<h2>Measured against real counterparts, at matched accuracy</h2>")]
        assert marks == sorted(marks), name
        dl = body.split('<dl class="meta glance">', 1)[1].split("</dl>", 1)[0]
        assert re.findall(r"<dt>([^<]+)</dt>", dl) == glance, name
        # the list is the hub table's column for this class, not a second description
        for _label, _short, cells in sitegen._class_facts():
            assert cells[sitegen.CLASS_ORDER.index(cls)] in dl, (name, _label)
        for gone in ("Library implicit integrators", "Library adaptive integrators",
                     "What an embedded pair is", "Why this class exists",
                     "Where else this class is measured", "The whole ledger",
                     "Accepted and declined work</h2>"):
            assert gone not in html, (name, gone)
        check_banned(html)
    exp = (out / "explicit.html").read_text(encoding="utf-8")
    assert LANE_HEADING not in exp and "Further evidence, not published here" not in exp
    for name in ("implicit.html", "adaptive.html"):
        html = (out / name).read_text(encoding="utf-8")
        order = [html.index(x) for x in (
            "<h2>Measured against real counterparts", LANE_HEADING,
            "<h2>What these numbers are not</h2>",
            '<details class="fold"><summary>Every measured point',
            '<details class="fold"><summary>Further evidence, not published here</summary>')]
        assert order == sorted(order), name
        assert html.count("<h2>What these numbers are not</h2>") == 1, name
        assert '<details class="explain">' not in html, name


def test_the_two_code_hashes_carry_distinct_labels(monkeypatch, tmp_path):
    """The side-track executor and the lane search each stamp a code hash, and the two
    are unrelated. A page that captioned both "code hash" invited reading one as the
    other, so each now says whose it is."""
    _charted_env(monkeypatch, tmp_path)
    doc = sitegen._load_sidetrack()
    for html in (render_implicit(sidetrack=doc), render_adaptive(sidetrack=doc)):
        assert '<div class="k">side-track code hash</div>' in html
        assert "<dt>lane search code hash</dt>" in html
        assert '<div class="k">code hash</div>' not in html


def test_each_caveat_is_said_once_per_class_page(monkeypatch, tmp_path):
    _charted_env(monkeypatch, tmp_path)
    out = tmp_path / "docs"
    build(_stocked_arch(), out)
    for name in ("implicit.html", "adaptive.html"):
        html = (out / name).read_text(encoding="utf-8")
        for text in (sitegen._NEVER_SAME_WORK, sitegen._OFF_LIST_RULE,
                     sitegen._LANE_ELITES_UNSCORED):
            assert html.count(sitegen._esc(text)) == 1, (name, text[:40])
        # the metric sentence carries a glossary link, so its tail is what is counted
        assert html.count("so neither number converts into the other") == 1, name
        # "not scored" and "order-verified" once each outside At a glance and the
        # hub-shared cells: the lane paragraph no longer repeats the invariants list
        assert html.count("Nothing here is scored") == 0, name
        # the code-hash rule is linked, not restated
        assert html.count("counts as measured only under the code hash") == 1, name
        assert 'href="methodology.html#ledger"' in html, name


def test_each_class_chart_lands_on_its_own_page_deterministically(monkeypatch, tmp_path):
    """Per-class attribution for the charts: each one appears on the page of the class
    that produced its numbers and on no other, every figure opens with its caption,
    every new chart closes on a source line, and two builds are byte-identical."""
    _charted_env(monkeypatch, tmp_path)
    d1, d2 = tmp_path / "d1", tmp_path / "d2"
    build(_stocked_arch(), d1)
    build(_stocked_arch(), d2)
    assert _snapshot(d1) == _snapshot(d2)
    aria = {
        "explicit.html": ("achieved error against rhs evaluations for the explicit class",),
        "implicit.html": ("Stability function at infinity against gamma",
                          "SDIRK2 error against analytic cycles on the budget ladder",
                          "Modeled cycles per SDIRK2 step by where they go",
                          "achieved error against rhs evaluations for the implicit class",
                          "Implicit lane elites: a21 against median cycles"),
        "adaptive.html": ("achieved error against rhs evaluations across the tolerance sweep",
                          "Rejected share of attempted steps for each pair of controller gains",
                          "Modeled Q15 cycles for one attempted step",
                          "Lattice matrices meeting each embedded-pair condition",
                          "achieved error against rhs evaluations for the adaptive class",
                          "Adaptive lane elites: median cycles at the elite target"),
    }
    pages = {n: (d1 / n).read_text(encoding="utf-8") for n in CLASS_PAGES}
    for name, labels in aria.items():
        for label in labels:
            assert label in pages[name], (name, label)
            for other in CLASS_PAGES:
                if other != name:
                    assert label not in pages[other], (label, other)
        for fig in _figures_in(pages[name]):
            assert fig.startswith("<figcaption>"), name
            assert 'role="img" aria-label="' in fig, name
            assert 'class="note">Source: ' in fig, (name, fig[:80])
        _assert_chart_fit(pages[name])
        check_banned(pages[name])


def test_the_stability_chart_draws_each_gamma_once_and_names_its_arithmetic():
    docs = [({"job": "sdirk.gamma_dyadic_scan", "key": "s04"},
             {"arithmetic": _DYADIC_ARITH, "rows": _dyadic_rows()})]
    chart = sitegen._stability_chart(docs)
    assert chart == sitegen._stability_chart(list(docs))
    assert chart.startswith("<figure><figcaption>")
    assert 'aria-label="Stability function at infinity against gamma' in chart
    svg = chart.split('role="img"', 1)[1]
    assert svg.count("<circle") == 2                      # two A-stable gammas, once each
    assert svg.count('stroke-linecap="round"') == 1       # one cross: not A-stable
    assert "the smallest is 0.28, at gamma 5/16" in chart
    assert _DYADIC_ARITH in chart and "sdirk.gamma_dyadic_scan" in chart
    # the source line counts ledger points and plotted gammas apart: 1 point, 3 marks
    assert "1 ledger point, 3 distinct gammas plotted" in chart
    # where the marks land: gamma on a linear x axis, |R(inf)| on a log y axis
    marks = _marks(chart)
    g1, g5, g12 = (_mark_titled(marks, f"gamma {g}:") for g in ("1/16", "5/16", "3/4"))
    _assert_proportional([m["cx"] for m in (g1, g5, g12)], [0.0625, 0.3125, 0.75])
    _assert_proportional([m["cy"] for m in (g1, g5, g12)], [97.0, 0.28, 0.5556], log=True)
    assert g1["cy"] < g12["cy"] < g5["cy"]            # 97 above 0.5556 above 0.28
    assert g1["tag"] == "path" and g1["stroke"] == "var(--s2)"   # the not-A-stable cross
    assert g5["tag"] == g12["tag"] == "circle"
    _assert_chart_fit(chart)
    check_banned(chart)
    for empty in ([], [({}, {"rows": [{"gamma": "x"}]})]):
        note = sitegen._stability_chart(empty)
        assert note.startswith('<p class="note">') and "<svg" not in note
        assert ">0<" not in note


def test_the_budget_chart_marks_a_diverged_rung_instead_of_dropping_it():
    doc = _charted_benchmark()["implicit_budget"]
    chart = sitegen._budget_chart(doc)
    assert chart == sitegen._budget_chart(json.loads(json.dumps(doc)))
    assert chart.startswith("<figure><figcaption>")
    assert 'aria-label="SDIRK2 error against analytic cycles on the budget ladder' in chart
    assert "robertson_scaled: diverged at 65,200 cycles" in chart
    assert ">cycle budget<" in chart
    assert "Source: benchmark/results.json, implicit_budget" in chart
    assert "design_estimate" in chart and "float64" in chart
    # where the marks land: analytic cycles on a log2 x axis, error on a log y axis
    marks = _marks(chart)
    e1 = _mark_titled(marks, "enzyme_qssa: error 5.85e-06 at 32,736 cycles")
    e2 = _mark_titled(marks, "enzyme_qssa: error 1.46e-06 at 65,472 cycles")
    r4 = _mark_titled(marks, "robertson_scaled: error 5e-06 at 260,800 cycles")
    cross = _mark_titled(marks, "robertson_scaled: diverged at 65,200 cycles")
    _assert_proportional([e1["cx"], e2["cx"], r4["cx"]], [32736, 65472, 260800], log=True)
    assert abs(cross["cx"] - e2["cx"]) < 2.0          # 65,200 against 65,472 cycles
    assert e2["cy"] > e1["cy"]                          # the lower error sits lower
    # the cross wears the legend key's grey; its hover text names the problem
    assert cross["stroke"] == "var(--text-2)"
    # a rung's share of the budget in words, not as a fraction over one
    assert "the full budget" in chart and "1/2 of the budget" in chart
    assert "4 times the budget" in chart and "1/1 of the budget" not in chart
    assert "(a lone dot where only one rung finished)" in chart
    two_rungs = {"enzyme_qssa": doc["enzyme_qssa"]}
    assert "a lone dot" not in sitegen._budget_chart(two_rungs)
    _assert_chart_fit(chart)
    check_banned(chart)
    note = sitegen._budget_chart({"enzyme_qssa": {"steps_at_budget": 176}})
    assert "<svg" not in note and "no budget ladder" in note
    # the numbers at the budget itself stay, folded under the chart
    page = render_implicit(benchmark=_charted_benchmark())
    sec = page.split("SDIRK2 error as the cycle budget grows", 1)[1].split("<h2>", 1)[0]
    assert '<details class="fold"><summary>The numbers at the budget</summary>' in sec
    assert "<td>enzyme_qssa</td>" in sec


def test_the_jacobian_chart_stacks_each_policy_and_prints_its_total():
    docs = [({"key": "enzyme_qssa"}, {"problem": "enzyme_qssa", "arithmetic": _FLOAT_ONLY,
                                      "cycles": _jac_cycles()})]
    chart = sitegen._jacobian_chart(docs)
    assert chart == sitegen._jacobian_chart(list(docs))
    assert chart.startswith("<figure><figcaption>")
    assert 'aria-label="Modeled cycles per SDIRK2 step by where they go' in chart
    assert ">348<" in chart and ">372<" in chart
    assert "finite-difference Jacobian" in chart and "m0plus_fast" in chart
    assert _FLOAT_ONLY in chart and "sdirk.jacobian_cost" in chart
    # the rhs evaluations the count leaves out ride on the hover text, per policy
    assert "plus 8 rhs evaluations per step" in chart
    # each segment carries its own group's name and terms, in _JAC_GROUPS order, at a
    # width proportional to its cycles
    marks = _marks(chart)
    newton = _mark_titled(marks, "analytic Jacobian: Newton iterations, 204 cycles")
    lu = _mark_titled(marks, "analytic Jacobian: LU factorization, 84 cycles")
    other = _mark_titled(marks, "analytic Jacobian: other stage arithmetic, 60 cycles")
    fd = _mark_titled(marks, "finite difference Jacobian: finite-difference Jacobian, 24 cycles")
    assert "(newton_iterations)" in newton["title"] and "(lu_factor)" in lu["title"]
    assert newton["cx"] < lu["cx"] < other["cx"]
    assert abs(newton["w"] / lu["w"] - 204 / 84) < 0.02
    assert fd["cy"] > newton["cy"]                    # the second policy's bar sits below
    _assert_chart_fit(chart)
    check_banned(chart)
    note = sitegen._jacobian_chart([({}, {"cycles": "no"})])
    assert "<svg" not in note and "nothing to plot" in note


def test_the_sweep_draws_one_panel_per_problem_with_the_others_for_scale(
        monkeypatch, tmp_path):
    work = _env(monkeypatch, tmp_path)
    _write_ledger(work, [p for p in _charted_ledger() if p["job"] == "adaptive.suite_sweep"])
    doc = sitegen._load_sidetrack()
    docs = sitegen._job_docs(sitegen._track_entries(doc, "adaptive"), doc["artifacts"],
                             "adaptive.suite_sweep")
    chart = sitegen._sweep_multiples(docs)
    assert chart == sitegen._sweep_multiples(list(docs))
    assert chart.count('role="img"') == 2
    assert ('aria-label="buck_converter: achieved error against rhs evaluations across '
            'the tolerance sweep, 3 points"') in chart
    # the panel points at the one table under the set, which carries every plotted point
    assert chart.count('aria-describedby="sweep-tolerance-values"') == 2
    assert 'id="sweep-tolerance-values"' in chart
    # small panels on shared axes, four to a row at full width; a panel draws only its
    # own problem, so the page carries no second copy of every line in grey
    assert chart.count('viewBox="0 0 256 184"') == 2
    assert 'stroke="var(--line)"' not in chart
    assert "Pair: Bogacki-Shampine 3(2)." in chart and _FLOAT_ONLY in chart
    # where the marks land: rhs evaluations on x, achieved error on y, both log
    buck = sorted((m for m in _marks(chart) if m["title"].startswith("buck_converter:")),
                  key=lambda m: m["cx"])
    assert [m["title"].split(" error ")[1].split(" ")[0] for m in buck] == [
        "0.0015", "7.4e-05", "8.9e-07"]
    _assert_proportional([m["cx"] for m in buck], [91, 376, 1666], log=True)
    _assert_proportional([m["cy"] for m in buck], [1.5e-3, 7.4e-5, 8.9e-7], log=True)
    _assert_chart_fit(chart)
    check_banned(chart)
    assert "<svg" not in sitegen._sweep_multiples([])


def test_the_gain_map_prints_every_share_and_says_n_a_where_no_run_finished(
        monkeypatch, tmp_path):
    work = _env(monkeypatch, tmp_path)
    _write_ledger(work, [p for p in _charted_ledger()
                         if p["job"] == "adaptive.controller_gains"])
    doc = sitegen._load_sidetrack()
    docs = sitegen._job_docs(sitegen._track_entries(doc, "adaptive"), doc["artifacts"],
                             "adaptive.controller_gains")
    chart = sitegen._gain_map(docs)
    assert chart == sitegen._gain_map(list(docs))
    assert chart.startswith("<figure><figcaption>")
    assert 'aria-label="Rejected share of attempted steps' in chart
    for value in (">0.0253<", ">0.0166<", ">0.25<", ">n/a<"):
        assert value in chart, value
    assert "no run finished (status underflow)" in chart
    # the largest share takes the far end of the ramp, the smallest the near end, and
    # each cell sits in its alpha row and beta column
    marks = _marks(chart)
    big = _mark_titled(marks, "alpha 1/4, beta 1/8: rejection share 0.25")
    small = _mark_titled(marks, "alpha 1/4, beta 0: rejection share 0.0166")
    top = _mark_titled(marks, "alpha 1/8, beta 0: rejection share 0.0253")
    assert big["fill"] == "var(--q7)" and small["fill"] == "var(--q1)"
    assert big["cy"] == small["cy"] and big["cx"] > small["cx"]      # same row
    assert small["cx"] == top["cx"] and small["cy"] > top["cy"]      # same column
    row = re.search(r'<text x="[\d.]+" y="([\d.]+)" text-anchor="end">1/4</text>', chart)
    assert row and abs(float(row.group(1)) - 3.5 - big["cy"]) < 0.01
    # values wear the ink paired with their cell's step, and the caption holds in both
    # themes, where the ramp runs opposite ways
    assert 'style="fill:var(--on-q7)"' in chart and 'class="lbl"' not in chart
    assert "further a cell's color is from the page background" in chart
    assert "Deeper blue" not in chart
    assert "Each point runs buck_converter, pll_lock at tolerance 1e-06." in chart
    _assert_chart_fit(chart)
    check_banned(chart)
    assert "<svg" not in sitegen._gain_map([({}, {"params": {}})])


def test_the_attempt_cost_bar_stacks_cycles_only_and_counts_the_branches_in_words():
    """branch_allowance is a count of conditional branch instructions, not cycles: the
    pinned cost model prices no branch. So the bar stacks the three priced parts, prints
    the document's own total, and the caption names the branch count. It once drew the
    47 branches as 47 cycles on the cycle axis and printed "234 + 47"."""
    chart = sitegen._attempt_cost_chart({"adaptive_matched_tolerance": _attempt_rows()})
    assert chart == sitegen._attempt_cost_chart(
        {"adaptive_matched_tolerance": _attempt_rows()})
    assert chart.startswith("<figure><figcaption>")
    assert 'aria-label="Modeled Q15 cycles for one attempted step' in chart
    assert ">234<" in chart and ">301<" in chart
    assert "+ 47" not in chart and "47 cycles" not in chart
    assert "up to 47 conditional branches that no bar includes" in chart
    assert "2-state problems" in chart and "3-state problems" in chart
    assert "fill-opacity" not in chart and "branch allowance" not in chart
    svg = chart.split('role="img"', 1)[1]
    # three segments per bar: two rects and the rounded end, on each of the two bars
    assert svg.count("<rect ") == 4 and svg.count('class="cellstroke"><title>') == 6
    assert "upper_bound_unpriced_branches" in chart
    assert "the 3 rows that carry a per-attempt price" in chart
    # each segment is its own part, in order, at a width proportional to its cycles
    marks = _marks(chart)
    stage = _mark_titled(marks, "2-state problems: stage arithmetic, 78 cycles")
    est = _mark_titled(marks, "2-state problems: error estimate, 56 cycles")
    ctl = _mark_titled(marks, "2-state problems: controller, 100 cycles")
    assert stage["cx"] < est["cx"] < ctl["cx"]
    assert abs(stage["w"] / est["w"] - 78 / 56) < 0.02
    dear = _mark_titled(marks, "3-state problems: stage arithmetic, 117 cycles")
    assert dear["cy"] > stage["cy"] and abs(dear["w"] / stage["w"] - 117 / 78) < 0.02
    _assert_chart_fit(chart)
    check_banned(chart)
    # the grade's own direction is quoted when the document defines it
    bench = {"adaptive_matched_tolerance": _attempt_rows(),
             "comparability": {"cost_grades": {"upper_bound_unpriced_branches": {
                 "direction": "upper bound within the model"}}}}
    assert ("Grade: upper_bound_unpriced_branches (upper bound within the model)."
            in sitegen._attempt_cost_chart(bench))
    # a total that disagrees with its parts is printed and the disagreement said
    odd = [dict(r, per_attempt_cycles=dict(r["per_attempt_cycles"], total=281))
           for r in _attempt_rows()[:1]]
    chart2 = sitegen._attempt_cost_chart({"adaptive_matched_tolerance": odd})
    assert ">281<" in chart2 and "while the parts drawn add to 234" in chart2
    # the count may also come as the prototype's {"conditional_branches": n} block
    block = [dict(r, per_attempt_cycles={k: v for k, v in r["per_attempt_cycles"].items()
                                         if k != "branch_allowance"},
                  branch_allowance={"conditional_branches": 12})
             for r in _attempt_rows()[:1]]
    assert "up to 12 conditional branches" in sitegen._attempt_cost_chart(
        {"adaptive_matched_tolerance": block})
    for bad in (None, {}, {"adaptive_matched_tolerance": "no"},
                {"adaptive_matched_tolerance": [{"per_attempt_cycles": {"stage": "x"}}]}):
        note = sitegen._attempt_cost_chart(bad)
        assert "<svg" not in note and "per-attempt Q15 price" in note


def test_the_census_prints_exact_counts_and_names_the_capped_points(monkeypatch, tmp_path):
    work = _env(monkeypatch, tmp_path)
    _write_ledger(work, [p for p in _charted_ledger() if p["job"] == "adaptive.pair_census"])
    doc = sitegen._load_sidetrack()
    docs = sitegen._job_docs(sitegen._track_entries(doc, "adaptive"), doc["artifacts"],
                             "adaptive.pair_census")
    chart = sitegen._census_chart(docs)
    assert chart == sitegen._census_chart(list(docs))
    assert chart.startswith("<figure><figcaption>")
    assert 'aria-label="Lattice matrices meeting each embedded-pair condition' in chart
    assert ">512<" in chart and ">4<" in chart
    # every count is printed; a count of 0 is printed and has no bar; neighbouring
    # conditions with equal counts share one bar (64/64/0/0 and 512/512/4/4 here)
    assert chart.count('fill="var(--s1)" class="cellstroke"') == 3
    assert ">matrices, order-2 consistent<" in chart
    assert ">order-3 consistent, both solvable<" in chart
    assert "Neighboring conditions with the same count share a bar." in chart
    # bar length is the count on a log axis from 1, so it is proportional to log10(count)
    marks = _marks(chart)
    wide = _mark_titled(marks, "3 stages, s ≤ 2: 512 matrices, order-2 consistent")
    short = _mark_titled(marks, "3 stages, s ≤ 2: 4 order-3 consistent, both solvable")
    assert abs(wide["w"] / short["w"] - math.log10(512) / math.log10(4)) < 0.02
    assert short["cy"] > wide["cy"]
    assert "Not counted (space over the census cap of 100,000 matrices): 4 stages, s ≤ 2." in chart
    assert _EXACT in chart
    _assert_chart_fit(chart)
    check_banned(chart)
    assert "<svg" not in sitegen._census_chart([])


def test_the_lane_charts_carry_the_freight_in_their_source_line(monkeypatch, tmp_path):
    work = _env(monkeypatch, tmp_path)
    for lane in ("implicit", "adaptive"):
        _write_json(work / f"{lane}_archive" / "elites.json", _lane_elites_fixture(lane))
    imp = _lane_section(render_implicit())
    adp = _lane_section(render_adaptive())
    assert 'aria-label="Implicit lane elites: a21 against median cycles' in imp
    assert 'aria-label="Adaptive lane elites: median cycles' in adp
    assert "Implicit lane elites" not in adp and "Adaptive lane elites" not in imp
    for sec in (imp, adp):
        assert "float64 runs" in sec and "not order-verified" in sec
        assert '<details class="fold"><summary>The 2 listed entries' in sec
        assert sec.index('role="img"') < sec.index("<table>")    # chart, then the table
        _assert_chart_fit(sec)
        check_banned(sec)
    # the caption counts the entries it plots, not the 71 the document ranked
    assert "The 2 entries plotted here" in imp and "The 2 entries plotted here" in adp
    assert "Each ranked entry" not in imp and "Each ranked entry" not in adp
    assert "Every plotted entry has gamma 15/16." in imp
    assert ("Every plotted entry uses controller gains alpha 1/4, beta 1/8 and safety 7/8."
            in adp)
    assert sitegen._lane_strip_chart(
        [{"method": "x"}], "rk-work/implicit_archive/elites.json").startswith("<p")
    assert sitegen._lane_scatter_chart(
        [{"score": {}}], "rk-work/adaptive_archive/elites.json").startswith("<p")


def test_latest_points_draws_a_re_measured_point_once():
    old = {"job": "j", "key": "k", "ts": "2026-09-01T00:00:00Z", "code_hash": "aaaa",
           "artifact": "sidetrack/j/old.json"}
    new = dict(old, ts="2026-09-08T00:00:00Z", code_hash="bbbb",
               artifact="sidetrack/j/new.json")
    assert sitegen._latest_points([new, old, "rubbish"]) == [new]
    docs = sitegen._job_docs([old, new], {"sidetrack/j/new.json": {"x": 1},
                                          "sidetrack/j/old.json": {"x": 0}}, "j")
    assert docs == [(new, {"x": 1})]


# --------------------------------------------------------------------------------------
# fix round 1 (2026-09-11): marks where the data says, counts that agree, freight on cards
# --------------------------------------------------------------------------------------

def test_the_lane_strip_puts_each_entry_at_its_a21_and_draws_the_gap_to_scale():
    """a21 on x, median cycles on y. The y domain reaches 2% either side of the middle
    value, so a 23-cycle gap between two levels no longer fills the whole plot."""
    def entry(key, a21, med, pol):
        return {"key": key * 16, "method": {"a21": a21, "gamma": "7/8", "jacobian": pol},
                "score": {"median_cycles_at_target": med}}
    rows = [entry("a", "1/8", 2713.0, "analytic"), entry("b", "1", 2713.0, "analytic"),
            entry("c", "2", 2736.0, "finite_difference"), entry("d", "2", 2713.0, "analytic")]
    rel = "rk-work/implicit_archive/elites.json"
    chart = sitegen._lane_strip_chart(rows, rel)
    assert chart == sitegen._lane_strip_chart(list(rows), rel)
    marks = _marks(chart)
    a, b, c, d = (_mark_titled(marks, f"rank {i}, ") for i in (1, 2, 3, 4))
    assert a["cx"] < b["cx"] < d["cx"]
    _assert_proportional([a["cx"], b["cx"], d["cx"]], [0.125, 1.0, 2.0])
    assert c["cy"] < d["cy"]                           # 2736 cycles above 2713
    plot_h = 280 - 12 - 34
    assert d["cy"] - c["cy"] < 0.35 * plot_h           # drawn to scale, not stretched
    # the caption names the levels and the a21 span of each, as the data gives them
    assert ("Analytic entries need 2713 cycles at a21 1/8 to 2; finite difference "
            "entries need 2736 cycles at a21 2.") in chart
    assert "2/1" not in chart
    _assert_chart_fit(chart)
    check_banned(chart)


def test_the_lane_scatter_puts_median_cycles_on_x_and_best_error_on_y():
    rows = [{"key": k * 16, "controller": {},
             "score": {"median_cycles_at_target": m, "best_achieved_error": e}}
            for k, m, e in (("a", 3000.0, 1e-5), ("b", 3500.0, 1e-4), ("c", 4500.0, 1e-3))]
    chart = sitegen._lane_scatter_chart(rows, "rk-work/adaptive_archive/elites.json")
    marks = [_mark_titled(_marks(chart), f"rank {i}:") for i in (1, 2, 3)]
    _assert_proportional([m["cx"] for m in marks], [3000, 3500, 4500])
    _assert_proportional([m["cy"] for m in marks], [1e-5, 1e-4, 1e-3], log=True)
    assert marks[0]["cy"] > marks[2]["cy"]             # the smaller error sits lower
    _assert_chart_fit(chart)


def test_the_falsification_sweep_leaves_off_float64_errors_above_one():
    """An error above 1 is larger than any value a Q15 state can hold. Drawing the
    diverged float64 runs stretched the axis over twenty decades and pressed the Q15
    curve into a few pixels; they stay off it, and the caption names the step sizes."""
    sweep = [{"h": 5.0, "q15_error": float("inf"), "float_error": 2.37e10},
             {"h": 2.5, "q15_error": float("inf"), "float_error": 0.0176},
             {"h": 0.625, "q15_error": 1.3e-3, "float_error": 1.0e-3},
             {"h": 0.3125, "q15_error": 1.1e-3, "float_error": 6.3e-5},
             {"h": 0.15625, "q15_error": 2.5e-3, "float_error": 3.9e-6}]
    chart = sitegen._sweep_chart("rk4", {"crossover_h": 0.3125, "sweep": sweep})
    assert chart == sitegen._sweep_chart("rk4", {"crossover_h": 0.3125, "sweep": list(sweep)})
    assert "Float64 errors above 1, at h = 5, are left off" in chart
    assert "2.37e+10" not in chart
    assert "rk4 float_error at h=2.5: 0.0176" in chart     # float64 still fine there
    tops = [int(e) for e in re.findall(r'text-anchor="end">1e(-?\d+)</text>', chart)]
    assert tops and max(tops) <= 0
    q15 = sorted((m for m in _marks(chart) if "q15_error" in m["title"]),
                 key=lambda m: m["cx"])
    _assert_proportional([m["cy"] for m in q15], [2.5e-3, 1.1e-3, 1.3e-3], log=True)
    _assert_chart_fit(chart)
    check_banned(chart)
    assert "left off" not in sitegen._sweep_chart("rk4", {"sweep": sweep[2:]})


def test_the_validation_charts_carry_distinct_labels_and_a_source_line(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    html = sitegen.render_validation(_validation_fixture())
    for group in ("non-stiff", "stiff"):
        # the label names the group and counts what it plots: a chart that collapses to
        # one announced node for a screen reader should at least say how much is in it
        assert (f'aria-label="Q15 final-state error per method on each {group} validation '
                "problem, log scale, ") in html, group
    for fig in _figures_in(html):
        if 'role="img"' in fig:
            assert 'class="note">Source: rk-work/validation/results.json, results.' in fig
    check_banned(html)


def test_the_speed_chart_fades_the_median_bar_and_keeps_values_off_the_marks():
    sp = {"per_method_us_per_step": {
        "11e898cb2f01": {"median_us_per_step": 20.47, "n_problems": 3,
                         "per_problem_us_per_step": {"a": 18.0, "b": 20.47, "c": 25.0}},
        "rk4": {"median_us_per_step": 31.993, "n_problems": 3,
                "per_problem_us_per_step": {"a": 30.0, "b": 31.993, "c": 40.0}}}}
    methods = [{"name_or_hash": "11e898cb2f01", "kind": "discovered"},
               {"name_or_hash": "rk4", "kind": "classical"}]
    proto = {"clock": "time.perf_counter", "n_repeats": 15, "warmup": 3}
    chart = sitegen._bench_us_chart(sp, methods, proto)
    assert chart == sitegen._bench_us_chart(sp, list(methods), dict(proto))
    assert 'fill-opacity="0.35"' in chart
    assert ("Source: rk-work/benchmark/results.json, speedup.per_method_us_per_step. "
            "Python wall clock of the pinned solve_q15 path in Q15 (time.perf_counter, "
            "median of 15 repeats after 3 warmups).") in chart
    marks = _marks(chart)
    dots = {m["title"]: m for m in marks if m["tag"] == "circle"}
    # every microsecond figure prints to the same precision, chart included: the stored
    # medians carry whatever the timer produced (20.47 beside 31.993) and the page used
    # to print both as stored, four significant figures beside five
    ours = [dots[f"11e898cb / {p}: {v} us per step"] for p, v in
            (("a", "18.00"), ("b", "20.47"), ("c", "25.00"))]
    _assert_proportional([m["cx"] for m in ours], [18.0, 20.47, 25.0])
    labels = re.findall(r'<text class="lbl" x="([\d.]+)" y="[\d.]+" text-anchor="end">'
                        r"([^<]+)</text>", chart)
    assert sorted(t for _x, t in labels) == ["20.47", "31.99"]
    right = max(m["cx"] + 4 for m in dots.values())
    for x, t in labels:                                # the value column clears every dot
        assert float(x) - 7.0 * len(t) > right, t
    _assert_chart_fit(chart)
    assert "median of" not in sitegen._bench_us_chart(sp, methods)


def test_the_explicit_page_reaches_past_its_search_problems(monkeypatch, tmp_path):
    """The explicit class beyond the problems it was searched on: one count, a chart of
    best discovered against best classical per practical problem, and the measured speed
    sentence. Both sources are admitted, and both link to where the full tables are."""
    _env(monkeypatch, tmp_path)
    champ = "11e898cb2f01" * 5 + "abcd"
    val = {"cost_model": "m0plus_fast",
           "problems": [{"name": "p1", "stiff": False}, {"name": "p2", "stiff": False},
                        {"name": "stiff1", "stiff": True}],
           # the sentence states the size of both sides, so the document has to say how
           # many methods each side had; without them the page states no tally at all
           "methods": [{"kind": "discovered", "name_or_hash": champ},
                       {"kind": "classical", "name_or_hash": "rk4"},
                       {"kind": "classical", "name_or_hash": "midpoint"}],
           "verdicts": {"practical_problems_won_by_discovered": 1,
                        "practical_problems_compared": 2,
                        # the whole-suite counts, which include the stiff problem this
                        # section excludes: reading them here would publish 2 of 3
                        "problems_won_by_discovered": 2, "problems_compared": 3,
                        "per_problem": {
                            "p1": {"best_discovered": champ,
                                   "best_discovered_q15_error": 1e-3,
                                   "best_classical": "rk4", "best_classical_q15_error": 1e-2},
                            "p2": {"best_discovered": champ,
                                   "best_discovered_q15_error": 1e-1,
                                   "best_classical": "midpoint",
                                   "best_classical_q15_error": 1e-4},
                            "stiff1": {"best_discovered": None, "winner": "midpoint",
                                       "winner_kind": "classical",
                                       "winner_q15_error": 0.5}}}}
    bench = {"speedup": {"champion": champ, "baseline": "rk4", "n_problems_compared": 7,
                         "geomean_measured_speedup_rk4_over_champion": 1.452,
                         "per_method_us_per_step": {champ: {"median_us_per_step": 20.47},
                                                    "rk4": {"median_us_per_step": 31.993}}}}
    html = render_explicit(_stocked_arch(), validation=val, benchmark=bench)
    assert html == render_explicit(_stocked_arch(), validation=val, benchmark=bench)
    sec = html.split("<h2>Beyond the search problems</h2>", 1)[1].split("<h2>", 1)[0]
    assert ("On 1 of 2 practical problems that no search saw, the best of 1 discovered "
            "tableau ends with lower Q15 error than the best of 2 classical anchors") in sec
    assert 'href="validation.html"' in sec and 'href="validation.html#speed"' in sec
    assert ('aria-label="Best discovered against best classical Q15 error on each '
            'non-stiff practical problem, log scale, 2 problems"') in sec
    assert "Source: rk-work/validation/results.json, verdicts.per_problem." in sec
    marks = _marks(sec)
    d1, c1 = (_mark_titled(marks, f"p1: best {w}") for w in ("discovered", "classical"))
    d2, c2 = (_mark_titled(marks, f"p2: best {w}") for w in ("discovered", "classical"))
    _assert_proportional([c2["cx"], d1["cx"], c1["cx"], d2["cx"]],
                         [1e-4, 1e-3, 1e-2, 1e-1], log=True)
    assert d1["cy"] == c1["cy"] < d2["cy"] == c2["cy"]  # one row per problem, in order
    assert "stiff1" not in sec                          # finishing is validation's question
    _assert_chart_fit(sec)
    check_banned(html)
    # the section sits between the grids and the matched comparison
    assert (html.index("<h2>Elite grids</h2>") < html.index("Beyond the search problems")
            < html.index("at matched accuracy</h2>"))
    # absent sources say so, and never draw an empty frame
    bare = render_explicit(_stocked_arch())
    assert "validation/results.json has not been written" in bare
    for bad in (None, {}, {"verdicts": {"per_problem": {"p1": {"best_discovered": None}}}}):
        note = sitegen._practical_chart(bad)
        assert "<svg" not in note and note.startswith('<p class="note">')


def test_each_hub_card_carries_its_own_class_number(monkeypatch, tmp_path):
    """Each class card counts the targets its own solvers reached at matched accuracy,
    names its runs' arithmetic and its file. The stiff gap is a fact about explicit
    tableaus in Q15, so it moved into the implicit blurb with its own source rather than
    standing as the implicit class's number."""
    _env(monkeypatch, tmp_path)
    html = render_index(_stocked_arch(), benchmark=_matched_fixture(),
                        validation=_validation_fixture())
    cards = re.findall(r'<div class="card klass k-(\w+)[^"]*"><div class="k">\w+</div>'
                       r'<div class="v">([^<]+)</div><div class="d">([^<]+)</div>', html)
    got = {cls: (v, d) for cls, v, d in cards}
    # the three cards carry the same two figures, so none of them reads as perfect:
    # targets reached out of the rows that ran, then the rows that ran out of the rows
    # the class has. A row that never ran is still not a target this class missed, so it
    # stays out of the value's denominator and is counted in the description instead.
    assert got["explicit"][0] == "4 of 4 run"
    assert got["implicit"][0] == "1 of 1 run"
    assert got["adaptive"][0] == "0 of 1 run"
    assert "; 4 of 4 rows ran" in got["explicit"][1]
    assert "; 1 of 2 rows ran" in got["implicit"][1]
    assert "; 1 of 1 row ran" in got["adaptive"][1]
    # the solvers behind the number are named with their arithmetic, because "our
    # solvers" reads as "the methods this project found" and two thirds of the explicit
    # set is the classical rk4 control, one row of it in float64. The pairs come out of
    # a set, so they have to be sorted or two processes print two orders
    assert ("matched-accuracy targets reached by rk4 in Q15 and rk4_float64 in float64;"
            in got["explicit"][1])
    assert ("reached by sdirk2_analytic_jac in float64 and sdirk2_fd_jac in float64;"
            in got["implicit"][1])
    assert "reached by bs32_q15 in Q15;" in got["adaptive"][1]
    assert "our solvers" not in html.lower().split("<footer>")[0]
    for _v, d in got.values():
        assert d.endswith("from benchmark/results.json")
    # and the convention the three share is stated once, under the row
    assert html.count("a row that never ran is not a target the class missed") == 1
    assert html.count("so it does not mean discovered") == 1
    assert ("on 1 of 2 stiff validation problems every discovered explicit tableau "
            "overflows Q15 (validation/results.json)") in html
    assert "stiff problems no discovered method finishes" not in html
    assert "<svg" not in html                           # no chart ranks the classes
    check_banned(html)


def test_side_track_cards_name_their_job_and_carry_its_arithmetic(monkeypatch, tmp_path):
    """Side-track numbers carry their own job's arithmetic wherever they appear (CLAUDE.md
    Prose), cards included. The declined-share card reads one job, the one that records
    a rejection share per point; suite_sweep's per-sweep maximum is another quantity."""
    work = _env(monkeypatch, tmp_path)
    ledger = [
        {"track": "implicit", "job": "sdirk.gamma_a21_scan", "key": "s04",
         "arithmetic": _DYADIC_ARITH, "summary": {"pairs": 17, "l_stable_gammas": 0}},
        {"track": "adaptive", "job": "adaptive.controller_gains", "key": "a1o8_b0o1",
         "arithmetic": _FLOAT_ONLY, "summary": {"rejection_rate": 0.0128}},
        {"track": "adaptive", "job": "adaptive.suite_sweep", "key": "buck",
         "arithmetic": "float64 sweep", "summary": {"rejection_rate_max": 0.5}},
    ]
    _write_ledger(work, ledger)
    doc = sitegen._load_sidetrack()
    card_re = (r'<div class="card"><div class="k">([^<]+)</div><div class="v">([^<]+)</div>'
               r'<div class="d">([^<]+)</div></div>')
    named = 0
    for html in (render_implicit(sidetrack=doc), render_adaptive(sidetrack=doc)):
        for _k, _v, d in re.findall(card_re, html):
            for p in ledger:
                if p["job"] in d:
                    named += 1
                    assert f"Arithmetic: {p['arithmetic']}" in d, d
    assert named == 2
    cards = {k: v for k, v, _d in re.findall(card_re, render_adaptive(sidetrack=doc))}
    assert cards["highest rejection share"] == "0.0128"


def test_every_measured_point_counts_points_and_its_subcounts_add_up():
    """One row per point, from its newest line: the older reading of a re-measured point
    is not a measurement under the code-hash rule. The job subheads count points, say how
    many ledger lines stand behind them, and add up to the fold's own count."""
    old = {"track": "adaptive", "job": "adaptive.controller_gains", "key": "p1",
           "status": "ok", "code_hash": "aaaa", "ts": "2026-09-01T00:00:00Z",
           "summary": {"rejection_rate": 0.999}}
    new = dict(old, code_hash="bbbb", ts="2026-09-08T00:00:00Z",
               summary={"rejection_rate": 0.0128})
    other = dict(new, key="p2", summary={"rejection_rate": 0.02})
    sweep = dict(new, job="adaptive.suite_sweep", key="buck", summary={"points": 3})
    html = render_adaptive(sidetrack={"ledger": [old, new, other, sweep], "artifacts": {}})
    fold = html.split("<summary>Every measured point", 1)[1].split("</details>", 1)[0]
    total = int(re.match(r" \((\d+) points?\)", fold).group(1))
    subs = [int(n) for n in re.findall(r'<p class="sub">(\d+) points?', fold)]
    assert total == 3 == sum(subs)
    assert "2 points, from 3 ledger lines" in fold
    assert "0.999" not in html and "0.0128" in fold
    cards = dict(re.findall(r'<div class="k">([^<]+)</div><div class="v">([^<]+)</div>', html))
    assert cards["highest rejection share"] == "0.02"    # newest readings only


def test_the_lane_section_names_the_axis_t_ladder_only_when_a_document_states_it(
        monkeypatch, tmp_path):
    """sitegen does not import lanesearch. The ladder's targets are read from the
    benchmark document, whose targets come from the same constant, and only when the
    lane's own elite target is one of them."""
    work = _env(monkeypatch, tmp_path)
    _write_json(work / "implicit_archive" / "elites.json", _lane_elites_fixture("implicit"))
    scope = {"three_class_scope": {"targets": [0.015625, 0.00390625, 0.0009765625,
                                               0.000244140625]}}
    sec = _lane_section(render_implicit(benchmark=scope))
    assert ("<dt>axis-T ladder</dt><dd>targets of 1/64, 1/256, 1/1024 and 1/4096 "
            "final-state error (benchmark/results.json)</dd>") in sec
    assert "axis-T ladder</dt>" not in _lane_section(render_implicit())
    other = {"three_class_scope": {"targets": [0.5, 0.25]}}
    assert "axis-T ladder</dt>" not in _lane_section(render_implicit(benchmark=other))
    for bad in (None, {}, {"three_class_scope": {"targets": ["x", None]}}):
        assert sitegen._ladder_text(bad, {"elite_target": 0.0009765625}) == ""


def test_the_rk23_pair_sentence_sits_under_the_attempt_cost_figure(monkeypatch, tmp_path):
    """Its ratios come from the matched-tolerance rows, the family the attempt-cost bar
    reads, and not from the matched-accuracy table whose fold it used to open. Its closing
    sentence pointed at "this one" from inside that fold; the page replaces it."""
    _env(monkeypatch, tmp_path)
    bench = _charted_benchmark()
    bench["verdicts"]["three_class"]["adaptive_pair"] = (
        "SciPy RK23 runs the same tableau our pair runs: a median function-evaluation "
        "ratio of 1.27 over 39 cells and a median achieved-error ratio of 0.176. "
        "Asking two solvers for the same tolerance does not "
        "put them at the same accuracy, which is why the matched-accuracy table exists "
        "beside this one.")
    # visual-prose-2: one count cannot stand for both medians, because a pair that
    # recorded no error still reports a function-evaluation ratio. The error median's
    # count is read off adaptive_pairs, so it holds even though the sentence says 39.
    bench["adaptive_pairs"] = [
        {"fevals_ratio_ours_over_rk23": 1.0, "error_ratio_ours_over_rk23": 0.1},
        {"fevals_ratio_ours_over_rk23": 1.1, "error_ratio_ours_over_rk23": 0.2},
        {"fevals_ratio_ours_over_rk23": 1.2, "error_ratio_ours_over_rk23": 0.3},
        {"fevals_ratio_ours_over_rk23": 1.3, "error_ratio_ours_over_rk23": None},
        {"fevals_ratio_ours_over_rk23": 1.4},
    ]
    html = render_adaptive(benchmark=bench)
    sec = html.split("<h2>What one Q15 attempt costs</h2>", 1)[1].split("<h2>", 1)[0]
    assert "<summary>How this compares with SciPy RK23</summary>" in sec
    assert "median function-evaluation ratio of 1.27 over 39 cells" in sec
    assert ("median achieved-error ratio of 0.176 over the 3 cells where both sides "
            "recorded an error.") in sec
    # the function-evaluation median keeps the document's own count, ungarnished
    assert "1.27 over 39 cells and" in sec
    assert "the matched-accuracy section below is the like-for-like comparison" in sec
    assert "exists beside this one" not in html
    assert "1.27" not in html.split("at matched accuracy</h2>", 1)[1]
    assert "median function-evaluation" not in render_implicit(benchmark=bench)


def test_explicit_cards_are_about_the_explicit_class(monkeypatch, tmp_path):
    """The last cycle id is run telemetry and the hypothesis counts belong to the research
    log, so the explicit cards keep records, elite cells and heldout_verified."""
    _env(monkeypatch, tmp_path)
    html = render_explicit(_stocked_arch())
    keys = re.findall(r'<div class="card"><div class="k">([^<]+)</div>', html)
    assert keys == ["records", "elite cells", "heldout_verified"]


def test_the_ledger_order_on_the_page_is_independent_of_the_file_order(
        monkeypatch, tmp_path):
    """The page is a function of what the ledger holds. A ledger written in the opposite
    order builds byte-identical class pages, and the points appear sorted by job, then
    point, on the page itself (not only in the helper that sorts them)."""
    points = _ledger_fixture() + _banned_ledger_fixture()[2:]
    pages = []
    for i, order in enumerate((points, list(reversed(points)))):
        work = _env(monkeypatch, tmp_path / f"w{i}")
        _write_ledger(work, order)
        out = tmp_path / f"d{i}"
        build(_empty_arch(), out)
        pages.append({n: (out / n).read_bytes() for n in
                      ("implicit.html", "adaptive.html", "methodology.html")})
    assert pages[0] == pages[1]
    adp = pages[0]["adaptive.html"].decode("utf-8")
    imp = pages[0]["implicit.html"].decode("utf-8")
    assert adp.index(">a1o8_b0o1<") < adp.index(">buck_converter<")
    assert imp.index(">s04_t06<") < imp.index(">servo_load_step<")


def test_two_processes_with_different_hash_seeds_build_identical_sites(monkeypatch, tmp_path):
    """The charts gather names in sets. Iterating a set of strings follows PYTHONHASHSEED,
    which differs between processes, so a chart that stopped sorting one would pass every
    in-process determinism check. Two processes under two seeds catch it."""
    work = _charted_env(monkeypatch, tmp_path)
    script = ("import sys\nfrom pathlib import Path\nfrom rk_harness import sitegen\n"
              "from rk_harness.types import ArchiveState\n"
              "arch = ArchiveState(n_records=132049, last_cycle_id=2726, grids={1: {}, "
              "2: {}, 3: {}, 4: {}}, open_hypotheses=(), refuted_hypotheses=())\n"
              "sitegen.build(arch, Path(sys.argv[1]))\n")
    root = Path(sitegen.__file__).resolve().parents[1]
    snaps = []
    for seed in ("0", "1"):
        out = tmp_path / f"seed{seed}"
        env = dict(os.environ, PYTHONHASHSEED=seed, RK_WORK_DIR=str(work), RK_SITE="off",
                   RK_LLM="off")
        proc = subprocess.run([sys.executable, "-c", script, str(out)], cwd=str(root),
                              env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=300)
        assert proc.returncode == 0, proc.stderr[-2000:]
        snaps.append(_snapshot(out))
    assert len(snaps[0]) >= 7 and snaps[0] == snaps[1]


# ----------------------------------------------------------------------------- A2 round

def test_the_cost_grade_note_prints_only_where_it_explains_something(monkeypatch, tmp_path):
    """The benchmark's note describes every class's grades at once. On a page whose rows
    carry one grade and no library row it would describe rows the table does not hold."""
    _env(monkeypatch, tmp_path)
    bench = _matched_fixture()
    note = bench["verdicts"]["three_class"]["cost_grades"]
    explicit = render_explicit(_stocked_arch(), benchmark=bench)
    # the explicit rows carry one real grade and the empty "none" the float64 control
    # row has; an empty grade is not a second grade, so the note still has nothing to
    # explain here
    assert {r["cost_grade"] for r in bench["matched_accuracy"]
            if r["class"] == "explicit"} == {"model", "none"}
    assert note not in explicit                          # one grade, no library row
    for html in (render_implicit(benchmark=bench), render_adaptive(benchmark=bench)):
        assert note in html                              # library rows carry no cycles
    assert "both sides answer" not in explicit and "happen to share a number" not in explicit
    assert "A row that did not reach its target stays in the table" in explicit


def test_the_not_these_list_says_why_without_calling_archive_counts_unmodelled(
        monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    html = render_implicit()
    assert "None of these cycle counts is an archive score, and none converts into one." in html
    assert "Every cycle count here is modelled" not in html


def test_the_attempt_cost_source_line_writes_q15_as_the_prose_does():
    fig = sitegen._attempt_cost_chart({"adaptive_matched_tolerance": _attempt_rows()})
    note = fig.split('<p class="note">Source:', 1)[1]
    assert "Arithmetic: Q15." in note and "Arithmetic: q15" not in note


# --------------------------------------------------------------------------------------
# FIND-2 (2026-09-12): one fixed comparator, a tie band and a degeneracy filter
# --------------------------------------------------------------------------------------

def _champion_validation_fixture() -> dict:
    """One champion, two anchors, four problems: a win, a loss to one anchor, a tie
    inside the band, and a problem whose whole field lands within a few percent."""
    champ = "11e898cb" + "0" * 56
    other = "196b1d17" + "0" * 56
    def row(problem, method, err, **extra):
        return {"problem": problem, "method": method, "q15_error": err,
                "float_error": 1e-9, "steps": 1000, "cycles_per_step": 44,
                "max_abs_q": 9000, **extra}
    return {
        "budget_cycles": 65536, "cost_model": "m0plus_fast",
        "rounding": "floor (ASRS), per HANDOFF 4.2",
        "generated_from": {"archive_records": 46398, "champion_hash": champ,
                           "verifier_hash": "de5bec22" + "0" * 56},
        "methods": [
            {"kind": "classical", "name_or_hash": "midpoint", "order": 2, "stages": 2,
             "roles": ["anchor"]},
            {"kind": "classical", "name_or_hash": "rk4", "order": 4, "stages": 4,
             "roles": ["anchor"]},
            {"kind": "discovered", "name_or_hash": champ, "order": 2, "stages": 3,
             "roles": ["champion"],
             "archive": {"cycle_id": 33, "tier": "no_improvement", "heldout_error": 0.0286}},
            {"kind": "discovered", "name_or_hash": other, "order": 4, "stages": 4,
             "roles": ["best_elite_order_4"]},
        ],
        "problems": [
            {"name": "buck_converter", "domain": "power electronics", "n_states": 2,
             "t_end": 25.0, "stiff": False, "stiffness_ratio": 1.0},
            {"name": "bicycle_lateral", "domain": "vehicle dynamics", "n_states": 2,
             "t_end": 20.0, "stiff": False, "stiffness_ratio": 1.4},
            {"name": "servo_load_step", "domain": "motion control", "n_states": 2,
             "t_end": 4.0, "stiff": True, "stiffness_ratio": 940.0},
            {"name": "enzyme_qssa", "domain": "chemical kinetics", "n_states": 2,
             "t_end": 12.0, "stiff": True, "stiffness_ratio": 300.0},
        ],
        "results": [
            row("buck_converter", champ, 0.002), row("buck_converter", "midpoint", 0.02),
            row("buck_converter", "rk4", 0.03), row("buck_converter", other, 0.004),
            row("bicycle_lateral", champ, 0.00054),
            row("bicycle_lateral", "midpoint", 0.00039),
            row("bicycle_lateral", "rk4", 0.04), row("bicycle_lateral", other, 0.00018),
            row("servo_load_step", champ, 0.0115754),
            row("servo_load_step", "midpoint", 0.0115746),
            row("servo_load_step", "rk4", 0.0124), row("servo_load_step", other, 0.013),
            row("enzyme_qssa", champ, 0.009655), row("enzyme_qssa", "midpoint", 0.009698),
            row("enzyme_qssa", "rk4", 0.009913), row("enzyme_qssa", other, 0.009741),
        ],
        "verdicts": {
            "practical_problems_total": 2, "practical_problems_compared": 2,
            "practical_problems_won_by_discovered": 2,
            "practical_median_ratio_discovered_over_classical": 0.28,
            "stiff_problems_total": 2, "stiff_problems_compared": 2,
            "stiff_problems_won_by_discovered": 1,
            "stiff_problems_with_no_discovered_finisher": 0,
            "stiff_median_ratio_discovered_over_classical": 0.998,
            "overall": "On the 2 non-stiff practical problems the best discovered method "
                       "has lower Q15 error on 2 of 2.",
            "per_problem": {
                "buck_converter": {"winner": "11e898cb" + "0" * 56,
                                   "winner_kind": "discovered", "winner_q15_error": 0.002,
                                   "best_classical": "midpoint",
                                   "best_classical_q15_error": 0.02,
                                   "best_discovered": "11e898cb" + "0" * 56,
                                   "best_discovered_q15_error": 0.002,
                                   "ratio_discovered_over_classical": 0.1,
                                   "finishers_classical": 2, "finishers_discovered": 2},
                "bicycle_lateral": {"winner": "196b1d17" + "0" * 56,
                                    "winner_kind": "discovered",
                                    "winner_q15_error": 0.00018,
                                    "best_classical": "midpoint",
                                    "best_classical_q15_error": 0.00039,
                                    "best_discovered": "196b1d17" + "0" * 56,
                                    "best_discovered_q15_error": 0.00018,
                                    "ratio_discovered_over_classical": 0.4615,
                                    "finishers_classical": 2, "finishers_discovered": 2},
                "servo_load_step": {"winner": "midpoint", "winner_kind": "classical",
                                    "winner_q15_error": 0.0115746,
                                    "best_classical": "midpoint",
                                    "best_classical_q15_error": 0.0115746,
                                    "best_discovered": "11e898cb" + "0" * 56,
                                    "best_discovered_q15_error": 0.0115754,
                                    "ratio_discovered_over_classical": 1.00007,
                                    "finishers_classical": 2, "finishers_discovered": 2},
                "enzyme_qssa": {"winner": "11e898cb" + "0" * 56,
                                "winner_kind": "discovered",
                                "winner_q15_error": 0.009655,
                                "best_classical": "midpoint",
                                "best_classical_q15_error": 0.009698,
                                "best_discovered": "11e898cb" + "0" * 56,
                                "best_discovered_q15_error": 0.009655,
                                "ratio_discovered_over_classical": 0.99557,
                                "finishers_classical": 2, "finishers_discovered": 2},
            },
        },
    }


def test_the_champion_table_fixes_one_comparator_against_every_anchor(monkeypatch,
                                                                      tmp_path):
    """F4. Every row of the old primary table was a maximum over the discovered methods
    against a maximum over the anchors, and the discovered side was itself selected on
    error, so the win rate rose with the number of discovered methods run. The primary
    view is now one pre-registered method against each anchor, and the max-over-both
    table is a fold that states the size of both sides."""
    _env(monkeypatch, tmp_path)
    data = _champion_validation_fixture()
    html = sitegen.render_validation(data)
    sec = html.split("<h2>The champion against each anchor</h2>", 1)[1]
    head = sec.split("<details", 1)[0]
    # the pre-registration rule, tied to the cycle the champion was archived at
    assert "the archive champion" in head and "coefficients unchanged" in head
    assert "It was picked at cycle 33 out of 46,398 archived records" in head
    # one column per anchor, and a verdict in every cell
    assert head.count("<th class=\"num\">midpoint</th>") == 1
    assert "(lower)" in head and "(higher)" in head
    assert "(tie)" in head                         # servo_load_step, 0.007 percent apart
    assert "(flagged)" in head                     # enzyme_qssa, whole field within 2.7%
    # both tallies carry both sample sizes, and the gate agrees
    assert "1 discovered method against 2 classical anchors" in head
    sitegen.check_tallies("validation.html", html)
    # the champion is not the discovered method that wins bicycle_lateral, and the page
    # says so rather than letting the max-over-both tally stand in for the champion
    assert "the champion is not the discovered method with the lowest error" in head
    assert "196b1d17" in head
    # the demoted view is a fold labelled with N on both sides
    assert ("<summary>Best per problem, a maximum over 2 discovered and 2 classical "
            "methods</summary>") in sec
    assert sitegen.render_validation(data) == html
    check_banned(html)


def test_a_ratio_inside_the_tie_band_counts_as_a_tie_everywhere(monkeypatch, tmp_path):
    """F5. servo_load_step's two errors are 0.007 percent apart and were published as a
    classical win; enzyme_qssa's are 0.44 percent apart and were published as a discovered
    win. Those are the same number, and the site now says so in both places."""
    _env(monkeypatch, tmp_path)
    html = sitegen.render_validation(_champion_validation_fixture())
    cards = dict(re.findall(r'<div class="k">([^<]+)</div><div class="v">([^<]+)</div>',
                            html))
    # enzyme is flagged and leaves the stiff tally; servo is a tie, so nobody won it
    assert cards["stiff problems"] == "0 of 1"
    assert cards["practical problems"] == "2 of 2"
    stiff = html.split("<h3>Stiff</h3>", 1)[1].split("</table>", 1)[0]
    assert "tie" in stiff and "inside the 2 percent band" in stiff
    assert "1 left out as degenerate (enzyme_qssa)" in html
    assert "1 of them is a tie inside the 2 percent band" in html


def test_degeneracy_flags_a_dead_integration_and_leaves_a_healthy_field():
    """F5 acceptance 7, on a synthetic case: every method returning the same error is the
    signature of a state that went nowhere, and a healthy field spans orders of
    magnitude. The reference-norm criterion reads the norm from the problem's own entry;
    a document written before that field existed carries none, and the criterion then
    stays quiet rather than inventing one."""
    def rows(*errs):
        return [{"q15_error": e} for e in errs]
    flagged, why = sitegen.degeneracy({}, rows(0.00970, 0.00971, 0.00974))
    assert flagged and "span" in why and "percent from best to worst" in why
    assert not sitegen.degeneracy({}, rows(0.002, 0.02, 0.2))[0]
    # two finishers agreeing is not a field, so the spread criterion stays quiet
    assert not sitegen.degeneracy({}, rows(0.00970, 0.00971))[0]
    # a run that returned the reference itself, once the document carries the norm
    norm = {"reference_norm_over_peak": 0.0098}
    flagged, why = sitegen.degeneracy(norm, rows(0.00970, 0.00971, 0.00974))
    assert flagged
    healthy = sitegen.degeneracy(norm, rows(0.0005, 0.005, 0.05))
    assert not healthy[0]
    # without the field, the same healthy rows stay unflagged and nothing is claimed
    assert not sitegen.degeneracy({}, rows(0.0005, 0.005, 0.05))[0]
    assert sitegen._REFERENCE_NORM_KEY == "reference_norm_over_peak"
    # two finishers are too few for a spread to say anything, but two errors that both
    # sit on the reference norm are still a dead integration, and the norm criterion
    # carries that case by itself
    flagged, why = sitegen.degeneracy(norm, rows(0.00970, 0.00991))
    assert flagged and "reference solution's norm" in why
    # the contract with the other file: the key this reads is the field that one writes
    from rk_harness import validation as V
    assert sitegen._REFERENCE_NORM_KEY in V._problem_entry("enzyme_qssa")


def test_the_degenerate_block_prints_its_reason_and_the_thresholds_are_published(
        monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    html = sitegen.render_validation(_champion_validation_fixture())
    assert "<h3>Problems left out of the tallies</h3>" in html
    block = html.split("<h3>Problems left out of the tallies</h3>", 1)[1]
    assert "enzyme_qssa" in block
    assert "reporting the problem rather than the method" in block
    assert 'href="methodology.html#meth-protocol"' in block
    from rk_harness import methodology as methodology_mod
    meth = methodology_mod.render_page(sitegen._page,
                                       sitegen._methodology_sections(None))
    assert "span less than 5 percent" in meth
    assert "within 2 percent of" in meth and "counted as a tie" in meth
    # the norm criterion now runs, and the page says where the norm comes from
    assert "stores that norm for each problem" in meth
    assert "does not yet carry" not in meth
