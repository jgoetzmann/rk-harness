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
import json
import re
from pathlib import Path

from rk_harness import sitegen
from rk_harness.sitegen import (
    build, check_banned, render_adaptive, render_explicit, render_implicit, render_index,
    render_sidetrack,
)
from rk_harness.paths import work_dir
from rk_harness.types import ArchiveState

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
            art.write_text(json.dumps({"job": p["job"], "key": p["key"],
                                       "closes": p.get("closes", "a question"),
                                       "arithmetic": p.get("arithmetic", "float64"),
                                       "summary": p["summary"]}, sort_keys=True),
                           encoding="utf-8")
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
    assert "Library implicit integrators" not in imp     # no benchmark file yet
    check_banned(imp)

    work2 = _env(monkeypatch, tmp_path / "second")
    _write_json(work2 / "benchmark" / "results.json", _benchmark_fixture())
    out2 = tmp_path / "d2"
    build(_empty_arch(), out2)
    adp = (out2 / "adaptive.html").read_text(encoding="utf-8")
    assert "Library adaptive integrators" in adp
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
    assert len(pages) >= 14
    for page in pages:
        hrefs = _nav_hrefs(page.read_text(encoding="utf-8"))
        assert hrefs[:4] == ["index.html", "explicit.html", "implicit.html",
                             "adaptive.html"], page.name
    # the active tab lands on each class page itself
    for cls in ("explicit", "implicit", "adaptive"):
        html = (out / f"{cls}.html").read_text(encoding="utf-8")
        assert f'<a href="{cls}.html" class="on">{cls}</a>' in html


def test_the_nav_has_three_rows_and_the_ledger_sits_in_the_third(monkeypatch, tmp_path):
    work = _env(monkeypatch, tmp_path)
    _write_ledger(work, _ledger_fixture())
    out = tmp_path / "docs"
    build(_empty_arch(), out)
    html = (out / "index.html").read_text(encoding="utf-8")
    rows = re.findall(r'<nav class="tabs([^"]*)">(.*?)</nav>', html, re.S)
    assert [cls.strip() for cls, _body in rows] == ["", "sub2", "sub3"]
    tier1 = re.findall(r'href="([^"]+)"', rows[0][1])
    tier3 = re.findall(r'href="([^"]+)"', rows[2][1])
    assert tier1 == ["index.html", "explicit.html", "implicit.html", "adaptive.html"]
    assert "sidetrack.html" in tier3
    assert ">measurement ledger<" in html


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
    # each card names the file its number came from
    assert "from the run archive" in html
    assert "from validation/results.json" in html
    assert "from the measurement ledger" in html
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
    for name in CLASS_PAGES + ("sidetrack.html", "index.html"):
        html = (out / name).read_text(encoding="utf-8")
        check_banned(html)
    # softened, not dropped: the reader still gets the message
    ledger_page = (out / "sidetrack.html").read_text(encoding="utf-8")
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
                 sitegen._IMPLICIT_CLASS, sitegen._IMPLICIT_OFF_ARCHIVE,
                 sitegen._ADAPTIVE_CLASS, sitegen._ADAPTIVE_OFF_ARCHIVE,
                 sitegen._NO_POINTS, sitegen._OFF_LIST_RULE, sitegen._NEVER_SAME_WORK,
                 sitegen._NOT_THESE_WHERE, sitegen._LANE_ELITES_METRIC,
                 sitegen._LANE_ELITES_UNSCORED, sitegen._LANE_ELITES_ABSENT,
                 sitegen._LANE_ELITES_DAMAGED, sitegen._LANE_ELITES_EMPTY,
                 sitegen._LANE_ELITES_REFILL, sitegen._LANE_COUNTS_ABSENT,
                 sitegen._LANE_RANK_NOTE) + tuple(sitegen._LANE_ARITHMETIC.values()):
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
    for _href, label, _tier in sitegen._NAV_ITEMS:
        check_banned(label)


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


def test_the_conditional_set_names_exactly_the_optional_pages():
    """Every entry of _CONDITIONAL must be a real nav href, and no class page may be in
    it: a conditional class page would vanish from a fresh build and take the whole
    equal-standing arrangement with it."""
    hrefs = {href for href, _label, _tier in sitegen._NAV_ITEMS}
    assert sitegen._CONDITIONAL <= hrefs
    assert sitegen._CONDITIONAL == {"validation.html", "benchmark.html",
                                    "sidetrack.html"}
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
    # the ledger page keeps every point of both classes
    led = (out / "sidetrack.html").read_text(encoding="utf-8")
    for job in ("sdirk.stiff_suite", "adaptive.suite_sweep"):
        assert job in led, job


def test_library_integrators_land_on_the_matching_class(monkeypatch, tmp_path):
    work = _env(monkeypatch, tmp_path)
    _write_json(work / "benchmark" / "results.json", _benchmark_fixture())
    out = tmp_path / "docs"
    build(_empty_arch(), out)
    imp = (out / "implicit.html").read_text(encoding="utf-8")
    adp = (out / "adaptive.html").read_text(encoding="utf-8")
    assert "Radau" in imp and "BDF" in imp and "RK45" not in imp
    assert "RK45" in adp and "Radau" not in adp and "BDF" not in adp
    # the never-same-work caveat travels with them
    for html in (imp, adp):
        assert "never a same-work comparison" in html
    # the work-precision figure leads with its caption, like every other chart
    assert "<figure><figcaption>" in adp
    assert "derivative evaluations" in adp


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


def test_a_single_tolerance_rung_draws_marks_and_says_no_curve(monkeypatch, tmp_path):
    """The measured ladder has one rung per problem today. A chart that joined single
    points would imply a curve nobody measured, so the caption says which it is."""
    work = _env(monkeypatch, tmp_path)
    bench = _benchmark_fixture()
    bench["adaptive_results"] = [r for r in bench["adaptive_results"]
                                 if r["integrator"] != "RK45" or r["nfev"] == 68]
    _write_json(work / "benchmark" / "results.json", bench)
    out = tmp_path / "docs"
    build(_empty_arch(), out)
    adp = (out / "adaptive.html").read_text(encoding="utf-8")
    assert "each series is a single mark and no line is drawn" in adp
    assert "<path d=" not in adp.split("Library adaptive integrators")[1].split("</figure>")[0]

    # two rungs on one series: the line appears and the caption changes with it
    work2 = _env(monkeypatch, tmp_path / "second")
    _write_json(work2 / "benchmark" / "results.json", _benchmark_fixture())
    out2 = tmp_path / "docs2"
    build(_empty_arch(), out2)
    adp2 = (out2 / "adaptive.html").read_text(encoding="utf-8")
    assert "Each line joins the tolerance rungs" in adp2


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
    assert "Admitting one to the list is a decision for the owner" in imp


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
    for name in ("implicit.html", "adaptive.html", "sidetrack.html"):
        html = (out / name).read_text(encoding="utf-8")
        assert block in html, name
    # and the constant is what both class pages and the ledger render
    assert len(sitegen._NOT_THESE_NUMBERS) >= 4
    assert "Not scored." in block and "Not Q15." in block


def test_the_ledger_page_keeps_its_filename_and_its_provenance_role(
        monkeypatch, tmp_path):
    """sidetrack.html has been published under that name long enough that the URL is a
    permanent link to anyone who bookmarked it, so the page changes role rather than
    being deleted and replaced."""
    work = _env(monkeypatch, tmp_path)
    _write_ledger(work, _ledger_fixture() + _banned_ledger_fixture()[2:])
    out = tmp_path / "docs"
    build(_empty_arch(), out)
    html = (out / "sidetrack.html").read_text(encoding="utf-8")
    assert "<title>measurement ledger</title>" in html
    assert "The whole ledger" in html and "The code-hash rule" in html
    assert "The retry policy" in html
    # every measured point appears, sorted by class, then job, then point: the adaptive
    # rows lead, and within a class the jobs run in name order
    for p in _ledger_fixture():
        assert html.count(f'>{p["key"]}<') >= 1, p["key"]
    order = [html.index(f'>{k}<') for k in ("a1o8_b0o1",       # adaptive.controller_gains
                                            "buck_converter",  # adaptive.suite_sweep
                                            "s04_t06",         # sdirk.gamma_a21_scan
                                            "servo_load_step")]  # sdirk.stiff_suite
    assert order == sorted(order), "sorted by (class, job, point), not by file order"
    # the failed point is reported here, not on a class page
    assert "did not complete" in html
    assert "shows the earliest new case" in html


def test_render_sidetrack_direct_is_deterministic_and_safe():
    data = {"ledger": [{"ts": "2026-09-04T00:00:00Z", "cycle": 3, "track": "implicit",
                        "job": "sdirk.newton_iters", "key": "n2", "status": "ok",
                        "code_hash": "abc123def456", "artifact": "sidetrack/x/n2.json",
                        "summary": {"newton_iters": 2}}],
            "artifacts": {}}
    html = render_sidetrack(data)
    assert html == render_sidetrack(data)
    check_banned(html)
    assert "<script" not in html.lower()


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
            cycles=None, grade="model", status="ok"):
        return {"class": cls, "side": side, "solver": solver, "problem": problem,
                "arithmetic": arith, "target_error": target,
                "achieved_error": achieved, "n_steps_accepted": steps, "nfev": nfev,
                "analytic_cycles_total": cycles, "cost_grade": grade, "status": status}
    return {
        "matched_accuracy": [
            row("explicit", "ours", "rk4", "dc_motor", "q15", 1e-3, 8.2e-4, 26, 104, 1716),
            row("implicit", "ours", "sdirk2_fd_jac", "enzyme_qssa", "float64",
                1e-3, 9.1e-4, 176, 900, 58000),
            row("implicit", "library", "Radau", "enzyme_qssa", "float64",
                1e-3, 4.0e-7, 88, 795, None, grade="absent"),
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
    for cls, html in pages.items():
        assert "Measured against real counterparts, at matched accuracy" in html, cls
        assert bench["verdicts"]["three_class"]["per_class"][cls] in html, cls
        check_banned(html)
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
    assert "The implicit anchor at the shared cycle budget" in implicit
    assert "enzyme_qssa" in implicit
    assert "The implicit anchor at the shared cycle budget" not in render_adaptive(
        benchmark=bench)


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
        assert "Nothing here is scored either" in sec, cls
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
        assert doc["statement"] in sec, cls
        assert "8896" in sec and "64" in sec, cls          # n_measured, n_ranked
        assert "candidates measured" in sec and "candidates ranked" in sec, cls
        assert "the document reports 2, and keeps at most 32" in sec, cls
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
    # the arithmetic sentence is the lane's own, because the two lanes differ
    assert "no Q15 LU factorization" in imp
    assert "the Q15 cost model prices an attempt" in adp


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
    assert "entries listed" in sec
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
    assert "A new advance" in imp
    assert "record design estimate" in imp          # the cost basis name, softened
    assert "earliest order difference" in imp       # a value inside a shape column
    adp = _lane_section((out / "adaptive.html").read_text(encoding="utf-8"))
    assert "<td>new</td>" in adp                    # the controller safety value


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
