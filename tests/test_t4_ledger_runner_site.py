"""T4 — ledger, quarantine, runner, archive recovery, site generator, dashboard.

Written from .fullsend/SPEC.md and .fullsend/HANDOFF.md only; no implementation was
read. Every test name carries the behaviour ID it arbitrates. Tests that call
run_cycle (or otherwise evaluate real tableaus) are marked ``slow``.

The heartbeat test is deliberately the LAST test in this file: heartbeat() starts a
daemon thread that keeps writing work_dir()/HEARTBEAT for the rest of the process.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import math
import re
import time
from html.parser import HTMLParser
from pathlib import Path

import pytest
from rich.layout import Layout

from rk_harness.ledger import (
    PredicateSyntaxError, Predicate, Term, Field, parse_predicate, evaluate_predicate,
    hypotheses_path, load_hypotheses, append_hypothesis, resolve_open, resolve_one,
)
from rk_harness.quarantine import (
    QuarantineError, check_source, stage, load_staged, admit, admitted_problems,
)
from rk_harness.runner import (
    now, iso_now, heartbeat, load_state, save_state, log_event, seed_baselines, run_cycle,
)
from rk_harness.archive import read_all, replay, append, record_to_json
from rk_harness.verifier_hash import compute_verifier_hash
from rk_harness.sitegen import (
    BANNED_WORDS, BANNER, AVR_NOTE, BannedWordError, build, render_index, render_cell,
    render_hypotheses, render_costmodel, render_falsification, render_glossary,
    render_literature, render_interpretation, check_banned,
)
from rk_harness.dashboard import read_events, build_layout, render
from rk_harness.tableau import make_tableau, content_hash
from rk_harness.types import (
    Tableau, ScoreVector, Record, ArchiveState, RunState, CellStat, TIERS,
)
from rk_harness.paths import work_dir, findings_dir, archive_dir, PACKAGE_DIR

UTC = dt.timezone.utc
CLOCK = "2026-09-21T10:00:00Z"
CLOCK_DT = dt.datetime(2026, 9, 21, 10, 0, 0, tzinfo=UTC)
VH = "ab" * 32  # a 64-char hex string standing in for a verifier hash on hand-made records


# --------------------------------------------------------------------------------------
# Inline fixtures (duplicated on purpose; reconcile collapses duplication)
# --------------------------------------------------------------------------------------

def _setup_env(monkeypatch, tmp_path, phase="0", clock=CLOCK):
    """Point every path at tmp_path, switch the site/LLM/git off, freeze the clock."""
    work = tmp_path / "work"
    findings = tmp_path / "findings"
    work.mkdir(parents=True, exist_ok=True)
    findings.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("RK_WORK_DIR", str(work))
    monkeypatch.setenv("RK_FINDINGS_DIR", str(findings))
    monkeypatch.setenv("RK_SITE", "off")
    monkeypatch.setenv("RK_LLM", "off")
    monkeypatch.setenv("RK_PHASE", phase)
    if clock is None:
        monkeypatch.delenv("RK_CLOCK", raising=False)
    else:
        monkeypatch.setenv("RK_CLOCK", clock)
    monkeypatch.delenv("RK_GIT_COMMIT", raising=False)
    monkeypatch.delenv("RK_EVAL_BUDGET", raising=False)
    assert work_dir() == work
    assert findings_dir() == findings
    assert archive_dir() == work / "archive"
    return work


def _classical_8() -> dict[str, Tableau]:
    """HANDOFF §9.1 coefficients, exact."""
    return {
        "euler": make_tableau([[0]], [1], [0]),
        "midpoint": make_tableau([[0, 0], ["1/2", 0]], [0, 1], [0, "1/2"]),
        "heun2": make_tableau([[0, 0], [1, 0]], ["1/2", "1/2"], [0, 1]),
        "ralston2": make_tableau([[0, 0], ["2/3", 0]], ["1/4", "3/4"], [0, "2/3"]),
        "heun3": make_tableau([[0, 0, 0], ["1/3", 0, 0], [0, "2/3", 0]],
                              ["1/4", 0, "3/4"], [0, "1/3", "2/3"]),
        "kutta3": make_tableau([[0, 0, 0], ["1/2", 0, 0], [-1, 2, 0]],
                               ["1/6", "2/3", "1/6"], [0, "1/2", 1]),
        "rk4": make_tableau([[0, 0, 0, 0], ["1/2", 0, 0, 0], [0, "1/2", 0, 0], [0, 0, 1, 0]],
                            ["1/6", "1/3", "1/3", "1/6"], [0, "1/2", "1/2", 1]),
        "rk38": make_tableau([[0, 0, 0, 0], ["1/3", 0, 0, 0], ["-1/3", 1, 0, 0], [1, -1, 1, 0]],
                             ["1/8", "3/8", "3/8", "1/8"], [0, "1/3", "2/3", 1]),
    }


def _sv(fast: int, slow: int, search: float, heldout: float, measured=4.0) -> ScoreVector:
    """A complete 12-field ScoreVector with consistent per_problem keys."""
    per: dict[str, float] = {}
    for p in ("dahlquist", "damped_osc", "vanderpol_mild"):
        per[p] = search
        per[f"slow:{p}"] = search * 1.1
        per[f"avr_approx:{p}"] = search * 1.2
    for p in ("pendulum", "dc_motor", "rc_thermal", "quaternion"):
        per[p] = heldout
        per[f"slow:{p}"] = heldout * 1.1
        per[f"avr_approx:{p}"] = heldout * 1.2
    per["slow:search_error"] = search * 1.1
    per["slow:heldout_error"] = heldout * 1.1
    per["avr_approx:search_error"] = search * 1.2
    per["avr_approx:heldout_error"] = heldout * 1.2
    return ScoreVector(
        measured_order=measured,
        order_fit_points=3,
        error_constant=0.01,
        stability_real=-2.5,
        stability_imag=1.5,
        cycles={"m0plus_fast": fast, "m0plus_slow": slow, "avr_approx": fast * 3},
        csd_weight_total=10,
        coeff_quant_error=5.086e-06,
        search_error=search,
        heldout_error=heldout,
        overflow_margin=2.0,
        per_problem=per,
    )


def _rec(t: Tableau, sv: ScoreVector, tier: str, cycle_id: int, directive_id,
         hypothesis_id=None, vh: str = VH) -> Record:
    return Record(
        tableau_hash=content_hash(t), tableau=t, score=sv, tier=tier, cycle_id=cycle_id,
        seed=0, verifier_hash=vh, directive_id=directive_id, hypothesis_id=hypothesis_id,
        timestamp=CLOCK,
    )


def _site_records() -> list[Record]:
    """Three elites in three different grids: rk4 (p4,s4,b2), kutta3 (p3,s3,b1), heun2 (p2,s2,b0)."""
    c = _classical_8()
    return [
        _rec(c["rk4"], _sv(33, 85, 0.001, 0.002, 4.0), "heldout_verified", 1, "D-E000001"),
        _rec(c["kutta3"], _sv(26, 65, 0.003, 0.004, 3.0), "search_only", 2, "D-0112", "H-047"),
        _rec(c["heun2"], _sv(13, 13, 0.005, 0.006, 2.0), "unreplicated", 3, None),
    ]


def _site_archive(monkeypatch, tmp_path):
    """Append the three site records to a temp work dir and replay them."""
    work = _setup_env(monkeypatch, tmp_path)
    for r in _site_records():
        append(r)
    arch = replay()
    assert arch.n_records == 3
    assert (4, 2) in arch.grids[4]
    assert (3, 1) in arch.grids[3]
    assert (2, 0) in arch.grids[2]
    return work, arch


def _empty_arch() -> ArchiveState:
    return ArchiveState(n_records=0, last_cycle_id=0, grids={1: {}, 2: {}, 3: {}, 4: {}},
                        open_hypotheses=(), refuted_hypotheses=())


def _b53_arch(mean_b: float = 20.5, min_b: float = 20.0, third: bool = False) -> ArchiveState:
    stats = {
        (2, 2): {"fast.cycles": CellStat(250, 11.5, 250 * 0.25, 11.0)},
        (2, 3): {"fast.cycles": CellStat(300, mean_b, 300 * 0.25, min_b)},
    }
    if third:
        stats[(2, 4)] = {"fast.cycles": CellStat(400, 30.5, 400 * 0.25, 30.0)}
    return ArchiveState(n_records=550, last_cycle_id=1, grids={1: {}, 2: {}, 3: {}, 4: {}},
                        open_hypotheses=(), refuted_hypotheses=(), cell_stats=stats)


def _hyp(**over) -> dict:
    """HANDOFF §6 example (statement reworded to avoid the banned word 'beats')."""
    h = {
        "id": "H-047",
        "cycle_proposed": 112,
        "statement": "Under M0PLUS_SLOW, best(p=3,s=4) ranks above best(p=4,s=4) at equal budget",
        "mechanism": "extra order buys less accuracy than extra multiplies cost",
        "control": "inequality should reverse under M0PLUS_FAST",
        "predicate": "slow.p3s4.heldout < slow.p4s4.heldout AND fast.p3s4.heldout > fast.p4s4.heldout",
        "min_samples": 200,
        "verdict": None,
        "n_samples": None,
        "effect_size": None,
        "resolved_cycle": None,
    }
    h.update(over)
    return h


def _runstate(**over) -> RunState:
    st = dict(cycle_id=1, phase=0, started_at=CLOCK, last_heartbeat=CLOCK, spend_usd=0.0,
              stall_counter=0, current_cell=None)
    st.update(over)
    return RunState(**st)


class _Collector(HTMLParser):
    """Collects text, tags, footer provenance paragraphs and script tags from a page."""

    def __init__(self):
        super().__init__()
        self.text: list[str] = []
        self.tags: list[str] = []
        self.prov_p = 0
        self.scripts = 0

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        if tag == "p" and dict(attrs).get("class") == "prov":
            self.prov_p += 1
        if tag == "script":
            self.scripts += 1

    def handle_data(self, data):
        self.text.append(data)


def _snapshot(root: Path) -> dict[str, str]:
    """name -> sha256 for every file under root (HEARTBEAT files ignored: a daemon
    heartbeat thread from an earlier test file may still be alive)."""
    out: dict[str, str] = {}
    if not root.exists():
        return out
    for p in root.rglob("*"):
        if p.is_file() and "HEARTBEAT" not in p.name:
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _parse_iso(s: str) -> dt.datetime:
    d = dt.datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


_BANNED_RE = re.compile(r"\b(" + "|".join(re.escape(w) for w in BANNED_WORDS) + r")\b", re.IGNORECASE)


# ======================================================================================
# Ledger — K10, K14, K15, K16, B52, B53, B54, B55
# ======================================================================================

def test_K10_parse_predicate_rejects_dunder_import():
    with pytest.raises(PredicateSyntaxError):
        parse_predicate("__import__('os')")


@pytest.mark.parametrize("src", [
    "__import__('os').system('echo hi')",
    "import os",
    "os.system('x')",
    "fast.p2s2.heldout < __import__('os')",
    "print(1)",
    "1 + 1",
    "fast.p2s2.heldout < 1 OR __import__('os')",
    "fast.p2s2.heldout < 1; import os",
    "lambda: 1",
    "fast.p2s2.heldout.__class__ < 1",
])
def test_K14_parse_predicate_rejects_python_expressions(src):
    with pytest.raises(PredicateSyntaxError):
        parse_predicate(src)


def test_K15_ledger_source_never_uses_eval_exec_compile():
    src = (PACKAGE_DIR / "ledger.py").read_text(encoding="utf-8")
    assert len(src) > 0
    for needle in ("eval(", "exec(", "compile("):
        assert needle not in src, f"ledger.py contains {needle!r}"


def test_B52_parse_two_term_and_predicate():
    pr = parse_predicate(
        "slow.p3s4.heldout < slow.p4s4.heldout AND fast.p3s4.heldout > fast.p4s4.heldout")
    assert isinstance(pr, Predicate)
    assert isinstance(pr.terms, tuple) and len(pr.terms) == 2
    assert pr.ops == ("AND",)
    t0, t1 = pr.terms
    assert isinstance(t0, Term) and isinstance(t1, Term)
    assert t0 == Term(left=Field(model="slow", order=3, stages=4, metric="heldout"), op="<",
                      right=Field(model="slow", order=4, stages=4, metric="heldout"))
    assert t1.left == Field(model="fast", order=3, stages=4, metric="heldout")
    assert t1.op == ">"
    assert t1.right == Field(model="fast", order=4, stages=4, metric="heldout")


def test_B52_parse_number_on_right():
    pr = parse_predicate("fast.p2s2.cycles <= 16")
    assert len(pr.terms) == 1
    assert pr.ops == ()
    term = pr.terms[0]
    assert term.left == Field(model="fast", order=2, stages=2, metric="cycles")
    assert term.op == "<="
    assert not isinstance(term.right, Field)
    assert float(term.right) == 16.0


@pytest.mark.parametrize("src, n_terms, ops", [
    ("fast.p2s2.heldout < 1", 1, ()),
    ("fast.p2s2.heldout > 1", 1, ()),
    ("fast.p2s2.heldout >= 1.5", 1, ()),
    ("fast.p2s2.heldout == 0.25", 1, ()),
    ("avr_approx.p4s6.search == 0", 1, ()),
    ("slow.p1s2.order >= 1", 1, ()),
    ("fast.p2s2.heldout < 1 OR fast.p2s3.heldout < 2", 2, ("OR",)),
    ("fast.p2s2.heldout < 1 AND fast.p2s3.heldout < 2 OR slow.p3s3.cycles > 5", 3, ("AND", "OR")),
])
def test_B52_parse_accepts_every_grammar_production(src, n_terms, ops):
    pr = parse_predicate(src)
    assert len(pr.terms) == n_terms
    assert pr.ops == ops
    assert len(pr.ops) == len(pr.terms) - 1


def test_B52_parse_records_fields_for_every_model_and_metric():
    pr = parse_predicate("avr_approx.p4s6.search == 0")
    assert pr.terms[0].left == Field(model="avr_approx", order=4, stages=6, metric="search")
    assert pr.terms[0].op == "=="
    pr = parse_predicate("slow.p1s2.order >= 1")
    assert pr.terms[0].left == Field(model="slow", order=1, stages=2, metric="order")
    assert pr.terms[0].op == ">="


@pytest.mark.parametrize("src", [
    "fast.p2s2.cycles = 16",
    "fast.p2s2.foo < 1",
    "medium.p2s2.heldout < 1",
    "fast.p2s2.heldout < 1 XOR fast.p2s2.heldout < 2",
    "",
    "   ",
    "fast.p2s2.heldout < (1)",
    "fast.p2s2.heldout",
    "fast.p2s2.heldout <",
    "< 1",
    "1 < fast.p2s2.heldout",
    "fast.p2s2.heldout < 1 AND",
    "AND fast.p2s2.heldout < 1",
    "fast.p2s2.heldout < 1 fast.p2s2.heldout < 2",
    "fast.p2s2.heldout < 1 AND AND fast.p2s2.heldout < 2",
    "fast.p22s2.heldout < 1",
    "fast.ps2.heldout < 1",
    "fast.p2s2 < 1",
    "fast.p2s2.heldout.x < 1",
    "fast.p2s2.heldout < 'a'",
    "fast.p2s2.heldout <> 1",
    "fast.p2s2.heldout << 1",
    "fast.p2s2.heldout < fast",
    "fast.p2s2.heldout < 1 AND (fast.p2s2.heldout < 2)",
    "NOT fast.p2s2.heldout < 1",
    "fast.p2s2.heldout < 1 AND slow",
    "fast.p2s2.heldout < 1 , fast.p2s2.heldout < 2",
])
def test_B52_parse_rejects_anything_outside_the_grammar(src):
    with pytest.raises(PredicateSyntaxError):
        parse_predicate(src)


def test_B53_resolve_one_supported_when_true_and_enough_samples():
    arch = _b53_arch()
    verdict, n, d = resolve_one(
        {"predicate": "fast.p2s2.cycles < fast.p2s3.cycles", "min_samples": 200}, arch)
    assert verdict == "supported"
    assert n == 250
    assert isinstance(d, float) and d >= 0.2


def test_B53_resolve_one_inconclusive_when_min_samples_not_met():
    arch = _b53_arch()
    verdict, n, _d = resolve_one(
        {"predicate": "fast.p2s2.cycles < fast.p2s3.cycles", "min_samples": 300}, arch)
    assert verdict == "inconclusive"
    assert n == 250


def test_B53_evaluate_predicate_uses_smallest_cell_count_and_cohens_d():
    arch = _b53_arch()
    verdict, n, d = evaluate_predicate(parse_predicate("fast.p2s2.cycles < fast.p2s3.cycles"), arch)
    assert verdict == "supported"
    assert n == 250
    assert d >= 0.2


@pytest.mark.parametrize("src", [
    "fast.p2s2.cycles > fast.p2s3.cycles",
    "fast.p2s2.cycles >= fast.p2s3.cycles",
    "fast.p2s2.cycles == fast.p2s3.cycles",
    "fast.p2s3.cycles <= fast.p2s2.cycles",
])
def test_B53_refuted_when_false_with_enough_samples(src):
    arch = _b53_arch()
    verdict, n, _d = resolve_one({"predicate": src, "min_samples": 200}, arch)
    assert verdict == "refuted"
    assert n == 250


def test_B53_small_effect_size_is_inconclusive_even_when_true():
    # min 11.0 < 11.5 is true and n=250 >= 200, but the populations barely differ.
    arch = _b53_arch(mean_b=11.55, min_b=11.5)
    verdict, n, d = resolve_one(
        {"predicate": "fast.p2s2.cycles < fast.p2s3.cycles", "min_samples": 200}, arch)
    assert verdict == "inconclusive"
    assert n == 250
    assert d < 0.2


def test_B53_and_or_evaluate_left_to_right_without_precedence():
    arch = _b53_arch(third=True)
    # A: p2s2 < p2s3 (11 < 20, true); B: p2s3 > p2s4 (20 > 30, false); C: p2s2 > p2s4 (false)
    # left to right: (A OR B) AND C == false. With AND-precedence it would be A OR (B AND C) == true.
    verdict, n, _ = resolve_one(
        {"predicate": "fast.p2s2.cycles < fast.p2s3.cycles OR fast.p2s3.cycles > fast.p2s4.cycles "
                      "AND fast.p2s2.cycles > fast.p2s4.cycles", "min_samples": 200}, arch)
    assert verdict == "refuted"
    assert n == 250
    # B OR A == true; A AND C == false
    verdict, n, _ = resolve_one(
        {"predicate": "fast.p2s3.cycles > fast.p2s4.cycles OR fast.p2s2.cycles < fast.p2s3.cycles",
         "min_samples": 200}, arch)
    assert verdict == "supported"
    assert n == 250
    verdict, n, _ = resolve_one(
        {"predicate": "fast.p2s2.cycles < fast.p2s3.cycles AND fast.p2s2.cycles > fast.p2s4.cycles",
         "min_samples": 200}, arch)
    assert verdict == "refuted"
    assert n == 250


def test_B53_number_comparison_uses_cell_minimum():
    arch = _b53_arch()
    verdict, n, _ = resolve_one({"predicate": "fast.p2s2.cycles < 12", "min_samples": 200}, arch)
    assert n == 250
    assert verdict != "refuted"
    verdict, n, _ = resolve_one({"predicate": "fast.p2s2.cycles > 12", "min_samples": 200}, arch)
    assert n == 250
    assert verdict != "supported"


def test_K16_predicate_naming_an_empty_cell_is_inconclusive_never_refuted():
    arch = _empty_arch()
    pr = parse_predicate("fast.p3s4.heldout < fast.p4s4.heldout")
    assert evaluate_predicate(pr, arch) == ("inconclusive", 0, 0.0)
    verdict, n, _ = resolve_one({"predicate": "fast.p3s4.heldout < fast.p4s4.heldout", "min_samples": 1}, arch)
    assert verdict == "inconclusive"
    assert n == 0
    # a comparison against a number on a missing cell is also inconclusive
    verdict, n, d = evaluate_predicate(parse_predicate("fast.p2s2.cycles < 1000"), arch)
    assert (verdict, n, d) == ("inconclusive", 0, 0.0)


def test_K16_partially_missing_cell_is_inconclusive():
    arch = _b53_arch()
    verdict, n, d = evaluate_predicate(parse_predicate("fast.p2s2.cycles < fast.p3s4.cycles"), arch)
    assert (verdict, n, d) == ("inconclusive", 0, 0.0)
    verdict, n, d = evaluate_predicate(
        parse_predicate("fast.p2s2.cycles < fast.p2s3.cycles AND fast.p3s4.heldout < 1"), arch)
    assert verdict == "inconclusive"
    assert n == 0
    # cell exists but the requested metric was never recorded there
    verdict, n, _ = evaluate_predicate(parse_predicate("fast.p2s2.heldout < 1"), arch)
    assert verdict == "inconclusive"


def test_B54_append_then_load_round_trips(monkeypatch, tmp_path):
    work = _setup_env(monkeypatch, tmp_path)
    assert hypotheses_path() == work / "hypotheses.jsonl"
    h = _hyp()
    append_hypothesis(h)
    assert hypotheses_path().exists()
    loaded = load_hypotheses()
    assert len(loaded) == 1
    assert all(loaded[0][k] == v for k, v in h.items())
    h2 = _hyp(id="H-048", predicate="fast.p2s2.cycles <= 16", min_samples=10)
    append_hypothesis(h2)
    loaded = load_hypotheses()
    assert [x["id"] for x in loaded] == ["H-047", "H-048"]
    assert all(loaded[1][k] == v for k, v in h2.items())
    raw = _read_jsonl(hypotheses_path())
    assert len(raw) == 2 and raw[0]["id"] == "H-047"


def test_B54_unparseable_predicate_raises_and_is_not_stored(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    with pytest.raises(PredicateSyntaxError):
        append_hypothesis(_hyp(id="H-100", predicate="__import__('os')"))
    with pytest.raises(PredicateSyntaxError):
        append_hypothesis(_hyp(id="H-101", predicate="fast.p2s2.cycles = 16"))
    with pytest.raises(PredicateSyntaxError):
        append_hypothesis(_hyp(id="H-102", predicate=""))
    assert all(h["id"] not in ("H-100", "H-101", "H-102") for h in load_hypotheses())


@pytest.mark.parametrize("missing", ["id", "statement", "min_samples"])
def test_B54_missing_required_key_raises_value_error(monkeypatch, tmp_path, missing):
    _setup_env(monkeypatch, tmp_path)
    h = _hyp()
    del h[missing]
    with pytest.raises(ValueError):
        append_hypothesis(h)
    assert load_hypotheses() == []


def test_B55_resolve_open_writes_resolution_lines_and_load_merges(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    append_hypothesis(_hyp(id="H-001", predicate="fast.p2s2.cycles < fast.p2s3.cycles", min_samples=200))
    append_hypothesis(_hyp(id="H-002", predicate="fast.p2s2.cycles > fast.p2s3.cycles", min_samples=200))
    arch = _b53_arch()

    ids = resolve_open(arch, 7)
    assert sorted(ids) == ["H-001", "H-002"]

    lines = _read_jsonl(hypotheses_path())
    assert len(lines) == 4
    res = {ln["id"]: ln for ln in lines[2:]}
    assert set(res) == {"H-001", "H-002"}
    for ln in lines[2:]:
        assert set(ln) == {"id", "verdict", "n_samples", "effect_size", "resolved_cycle"}
        assert ln["resolved_cycle"] == 7
        assert ln["n_samples"] == 250
        assert isinstance(ln["effect_size"], float) and ln["effect_size"] >= 0.2
    assert res["H-001"]["verdict"] == "supported"
    assert res["H-002"]["verdict"] == "refuted"

    merged = {h["id"]: h for h in load_hypotheses()}
    assert len(load_hypotheses()) == 2
    assert merged["H-001"]["verdict"] == "supported"
    assert merged["H-001"]["n_samples"] == 250
    assert merged["H-001"]["resolved_cycle"] == 7
    assert merged["H-001"]["effect_size"] >= 0.2
    assert merged["H-001"]["predicate"] == "fast.p2s2.cycles < fast.p2s3.cycles"
    assert merged["H-001"]["statement"] == _hyp()["statement"]
    assert merged["H-002"]["verdict"] == "refuted"

    # replay() derives open/refuted from the merged ledger
    arch2 = replay()
    assert "H-002" in arch2.refuted_hypotheses
    assert "H-001" not in arch2.open_hypotheses
    assert "H-002" not in arch2.open_hypotheses
    assert "H-001" not in arch2.refuted_hypotheses

    # nothing is left open: a second pass resolves nothing and writes nothing
    assert resolve_open(arch, 8) == []
    assert len(_read_jsonl(hypotheses_path())) == 4


def test_B55_last_resolution_line_wins(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    append_hypothesis(_hyp(id="H-001", predicate="fast.p2s2.cycles < fast.p2s3.cycles", min_samples=200))
    assert resolve_open(_b53_arch(), 7) == ["H-001"]
    with hypotheses_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "H-001", "verdict": "refuted", "n_samples": 999,
                             "effect_size": 0.5, "resolved_cycle": 9}) + "\n")
    merged = {h["id"]: h for h in load_hypotheses()}
    assert len(merged) == 1
    assert merged["H-001"]["verdict"] == "refuted"
    assert merged["H-001"]["resolved_cycle"] == 9
    assert merged["H-001"]["n_samples"] == 999
    assert "H-001" in replay().refuted_hypotheses


def test_B55_resolve_open_with_no_hypotheses_resolves_nothing(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    assert resolve_open(_b53_arch(), 1) == []
    assert resolve_open(_empty_arch(), 1) == []


def test_B55_open_hypotheses_appear_in_replay_until_resolved(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    append_hypothesis(_hyp(id="H-010", predicate="fast.p2s2.cycles < fast.p2s3.cycles", min_samples=200))
    arch = replay()
    assert "H-010" in arch.open_hypotheses
    assert "H-010" not in arch.refuted_hypotheses


# ======================================================================================
# Quarantine — K11, B56
# ======================================================================================

GOOD_SRC = "import math\n\ndef f(t, y):\n    return (math.sin(y[0]),)\n"
BAD_OS_SRC = "import os\n\ndef f(t, y):\n    return y\n"
DECAY_SRC = (
    "import math\n\n"
    "def f(t, y):\n    return (-y[0],)\n\n"
    "def reference(t):\n    return (math.exp(-t),)\n"
)
DECAY_SPEC = {"name": "decay2", "family": "linear", "n_states": 1, "y0": [1.0], "t_end": 5.0,
              "scale": 0.25, "peak": 1.0, "max_at_2x": 0.5}


def test_K11_check_source_rejects_os_import():
    violations = check_source("import os\ndef f(t, y): return y")
    assert isinstance(violations, list)
    assert len(violations) >= 1
    assert all(isinstance(v, str) for v in violations)


def test_K11_check_source_accepts_math_only():
    assert check_source("import math\ndef f(t, y): return (math.sin(y[0]),)") == []
    assert check_source(GOOD_SRC) == []
    assert check_source(DECAY_SRC) == []


@pytest.mark.parametrize("src", [
    "import math\ndef f(t, y):\n    open('x')\n    return y\n",
    "def f(t, y):\n    return y.__class__\n",
    "def f(t, y):\n    import math\n    return y\n",
    "def f(t, y):\n    exec('1')\n    return y\n",
    "def f(t, y):\n    return getattr(y, 'x')\n",
    "f = lambda t, y: y.__len__()\n",
    "import sys\ndef f(t, y): return y\n",
    "import subprocess\ndef f(t, y): return y\n",
    "import socket\ndef f(t, y): return y\n",
    "from os import path\ndef f(t, y): return y\n",
    "import math, os\ndef f(t, y): return y\n",
    "def f(t, y):\n    return __import__('os')\n",
    "def f(t, y):\n    return eval('1')\n",
    "def f(t, y):\n    return f.__globals__\n",
    "import math\ndef f(t, y):\n    return (math.__dict__,)\n",
    "def f(t, y):\n    from math import sin\n    return (sin(y[0]),)\n",
    "def f(t, y):\n    return (y[0].__add__(1),)\n",
])
def test_K11_check_source_rejects(src):
    assert check_source(src) != []


def test_K11_stage_writes_under_quarantine_dir_and_load_staged_returns_callable(monkeypatch, tmp_path):
    work = _setup_env(monkeypatch, tmp_path)
    p = stage("good", GOOD_SRC)
    assert isinstance(p, Path)
    assert p == work / "quarantine" / "good.py"
    assert p.read_text(encoding="utf-8") == GOOD_SRC
    fn = load_staged("good")
    assert callable(fn)
    out = fn(0.0, (0.5,))
    assert abs(out[0] - math.sin(0.5)) < 1e-12


def test_K11_load_staged_refuses_source_that_fails_the_ast_check(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    with pytest.raises(QuarantineError):
        stage("bad", BAD_OS_SRC)
        load_staged("bad")
    with pytest.raises(QuarantineError):
        stage("dunder", "def f(t, y):\n    return y.__class__\n")
        load_staged("dunder")


def test_B56_admitted_problems_is_empty_on_a_fresh_work_dir(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    assert admitted_problems() == ()


def test_B56_admit_accepts_a_clean_deterministic_bounded_in_range_problem(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    stage("decay2", DECAY_SRC)
    ok, reasons = admit("decay2", DECAY_SPEC)
    assert isinstance(ok, bool) and isinstance(reasons, list)
    assert ok is True
    assert reasons == []
    probs = admitted_problems()
    names = [p.name for p in probs]
    assert "decay2" in names
    p = probs[names.index("decay2")]
    assert p.n_states == 1
    assert p.scale == 0.25
    assert p.t_end == 5.0
    assert p.family == "linear"
    assert abs(p.reference(5.0)[0] - math.exp(-5.0)) < 1e-9
    # Problem.f is a Q15 rhs: y = 0.25 (physical 1.0) -> f = -1.0 physical -> -0.25 in Q15
    assert p.y0 == (8192,)
    assert p.f(0.0, (8192,)) == (-8192,)


def test_B56_admit_rejects_a_problem_that_leaves_the_q15_range(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    stage("growth", "import math\n\ndef f(t, y):\n    return (y[0],)\n\n"
                    "def reference(t):\n    return (math.exp(t),)\n")
    ok, reasons = admit("growth", {**DECAY_SPEC, "name": "growth", "peak": 148.41, "max_at_2x": 74.2})
    assert ok is False
    assert len(reasons) >= 1
    assert all(p.name != "growth" for p in admitted_problems())


def test_B56_admit_rejects_a_banned_import(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    try:
        stage("evil", "import os\n\ndef f(t, y):\n    return (-y[0],)\n\n"
                      "def reference(t):\n    return (1.0,)\n")
        ok, reasons = admit("evil", {**DECAY_SPEC, "name": "evil"})
    except QuarantineError:
        ok, reasons = False, ["QuarantineError"]
    assert ok is False
    assert len(reasons) >= 1
    assert all(p.name != "evil" for p in admitted_problems())


@pytest.mark.slow
def test_B56_admit_rejects_an_unbounded_time_problem(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    stage("slowf", "import math\n\ndef f(t, y):\n    s = 0.0\n    for i in range(5000):\n"
                   "        s += math.sin(i)\n    return (-y[0] + 0.0 * s,)\n\n"
                   "def reference(t):\n    return (math.exp(-t),)\n")
    ok, reasons = admit("slowf", {**DECAY_SPEC, "name": "slowf"})
    assert ok is False
    assert len(reasons) >= 1
    assert all(p.name != "slowf" for p in admitted_problems())


# ======================================================================================
# Runner — R2, R4, R5, B57, B58, E2, B59
# ======================================================================================

def test_E2_now_and_iso_now_honour_rk_clock(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    n = now()
    assert isinstance(n, dt.datetime)
    assert n.tzinfo is not None and n.utcoffset() == dt.timedelta(0)
    assert n == CLOCK_DT
    s = iso_now()
    assert isinstance(s, str)
    assert s.startswith("2026-09-21T10:00:00")
    assert _parse_iso(s) == CLOCK_DT
    # frozen clock is stable across calls
    assert iso_now() == s
    assert now() == n


def test_E2_rk_clock_accepts_offset_form(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path, clock="2026-10-02T03:04:05+00:00")
    assert now() == dt.datetime(2026, 10, 2, 3, 4, 5, tzinfo=UTC)


def test_B60_now_without_rk_clock_is_utc_wall_clock(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path, clock=None)
    before = dt.datetime.now(UTC)
    n = now()
    after = dt.datetime.now(UTC)
    assert n.tzinfo is not None and n.utcoffset() == dt.timedelta(0)
    assert before - dt.timedelta(seconds=1) <= n <= after + dt.timedelta(seconds=1)
    stamp = _parse_iso(iso_now())
    assert before - dt.timedelta(seconds=1) <= stamp <= dt.datetime.now(UTC) + dt.timedelta(seconds=1)


def test_R4_load_state_rebuilds_from_replay_when_runstate_absent(monkeypatch, tmp_path):
    work = _setup_env(monkeypatch, tmp_path, phase="2")
    c = _classical_8()
    append(_rec(c["rk4"], _sv(33, 85, 0.001, 0.002), "unreplicated", 3, None))
    append(_rec(c["rk38"], _sv(36, 64, 0.002, 0.003), "unreplicated", 4, None))
    assert not (work / "RUNSTATE.json").exists()
    st = load_state()
    assert isinstance(st, RunState)
    assert replay().last_cycle_id == 4
    assert st.cycle_id == 4
    assert st.phase == 2
    assert st.spend_usd == 0.0
    assert st.stall_counter == 0
    assert st.current_cell is None
    assert st.started_at == iso_now()


def test_R4_load_state_on_empty_archive_starts_at_cycle_zero(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path, phase="0")
    st = load_state()
    assert st.cycle_id == 0
    assert st.phase == 0
    assert st.stall_counter == 0
    assert st.spend_usd == 0.0
    assert st.current_cell is None


def test_R4_load_state_phase_defaults_to_zero_without_rk_phase(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    monkeypatch.delenv("RK_PHASE", raising=False)
    assert load_state().phase == 0


@pytest.mark.parametrize("garbage", [
    b'{"cycle_id": 3, "pha',
    b"[1, 2, 3]",
    b"\x00\x01\x02 not json at all",
    b"",
    b"null",
])
def test_R5_load_state_falls_back_to_replay_on_corrupt_runstate(monkeypatch, tmp_path, capsys, garbage):
    work = _setup_env(monkeypatch, tmp_path, phase="1")
    append(_rec(_classical_8()["rk4"], _sv(33, 85, 0.001, 0.002), "unreplicated", 5, None))
    (work / "RUNSTATE.json").write_bytes(garbage)
    st = load_state()
    assert isinstance(st, RunState)
    assert st.cycle_id == 5
    assert st.phase == 1
    assert st.stall_counter == 0
    assert capsys.readouterr().err.strip() != "", "corruption must be reported on stderr"


def test_R2_truncated_trailing_archive_line_is_discarded_and_state_rebuilds(monkeypatch, tmp_path):
    work = _setup_env(monkeypatch, tmp_path)
    c = _classical_8()
    append(_rec(c["rk4"], _sv(33, 85, 0.001, 0.002), "unreplicated", 1, None))
    append(_rec(c["kutta3"], _sv(26, 65, 0.003, 0.004), "unreplicated", 2, None))
    append(_rec(c["heun2"], _sv(13, 13, 0.005, 0.006), "unreplicated", 3, None))
    files = sorted(archive_dir().glob("*.jsonl"))
    assert len(files) == 1
    assert files[0].name == "2026-09-21.jsonl"
    lines = files[0].read_bytes().splitlines(keepends=True)
    assert len(lines) == 3
    last = lines[-1]
    files[0].write_bytes(b"".join(lines[:-1]) + last[: len(last) // 2])

    recs = read_all()
    assert len(recs) == 2
    assert [r.cycle_id for r in recs] == [1, 2]
    assert not (work / "RUNSTATE.json").exists()
    st = load_state()
    assert st.cycle_id == 2
    arch = replay()
    assert arch.n_records == 2
    assert arch.last_cycle_id == 2
    assert (4, 2) in arch.grids[4] and (3, 1) in arch.grids[3]
    assert arch.grids[2] == {}


def test_R2_corrupt_middle_line_is_discarded_with_a_warning(monkeypatch, tmp_path, capsys):
    _setup_env(monkeypatch, tmp_path)
    c = _classical_8()
    append(_rec(c["rk4"], _sv(33, 85, 0.001, 0.002), "unreplicated", 1, None))
    append(_rec(c["kutta3"], _sv(26, 65, 0.003, 0.004), "unreplicated", 2, None))
    append(_rec(c["heun2"], _sv(13, 13, 0.005, 0.006), "unreplicated", 3, None))
    f = sorted(archive_dir().glob("*.jsonl"))[0]
    lines = f.read_bytes().splitlines(keepends=True)
    mid = lines[1]
    f.write_bytes(lines[0] + mid[: len(mid) // 2] + b"\n" + lines[2])
    recs = read_all()
    assert [r.cycle_id for r in recs] == [1, 3]
    assert capsys.readouterr().err.strip() != ""
    assert load_state().cycle_id == 3


def test_B57_save_state_then_load_state_round_trips_and_leaves_no_temp_file(monkeypatch, tmp_path):
    work = _setup_env(monkeypatch, tmp_path)
    before = {p.name for p in work.iterdir() if p.is_file()}
    st = RunState(cycle_id=12, phase=2, started_at="2026-09-21T10:00:00Z",
                  last_heartbeat="2026-09-21T10:05:00Z", spend_usd=1.25, stall_counter=7,
                  current_cell=(3, 2))
    save_state(st)
    assert (work / "RUNSTATE.json").is_file()
    json.loads((work / "RUNSTATE.json").read_text(encoding="utf-8"))
    loaded = load_state()
    assert loaded == st
    assert loaded.current_cell == (3, 2) and isinstance(loaded.current_cell, tuple)
    after = {p.name for p in work.iterdir() if p.is_file()}
    leftovers = after - before - {"RUNSTATE.json"}
    leftovers = {n for n in leftovers if "HEARTBEAT" not in n}
    assert leftovers == set(), f"temp files left behind: {leftovers}"

    st2 = dataclasses.replace(st, cycle_id=13, current_cell=None, stall_counter=0)
    save_state(st2)
    assert load_state() == st2
    after2 = {p.name for p in work.iterdir() if p.is_file()}
    assert {n for n in after2 - before - {"RUNSTATE.json"} if "HEARTBEAT" not in n} == set()


def test_B59_log_event_appends_json_lines_with_ts_and_kind(monkeypatch, tmp_path):
    work = _setup_env(monkeypatch, tmp_path)
    log_event("alpha", a=1)
    log_event("beta", b="x", n=None, code="ORDER_NOT_MET")
    lines = _read_jsonl(work / "events.jsonl")
    assert len(lines) == 2
    assert lines[0]["kind"] == "alpha" and lines[0]["a"] == 1
    assert lines[1]["kind"] == "beta" and lines[1]["b"] == "x" and lines[1]["n"] is None
    assert lines[1]["code"] == "ORDER_NOT_MET"
    for ln in lines:
        assert isinstance(ln["ts"], str)
        _parse_iso(ln["ts"])


def test_B59_run_cycle_abandons_cleanly_when_replay_raises(monkeypatch, tmp_path):
    work = _setup_env(monkeypatch, tmp_path)

    def boom(*_a, **_k):
        raise RuntimeError("replay exploded")

    monkeypatch.setattr("rk_harness.archive.replay", boom)
    st = _runstate(cycle_id=3, phase=0, stall_counter=2, current_cell=(2, 1))
    out = run_cycle(st)
    assert isinstance(out, RunState)
    assert out.stall_counter == 3
    assert out.cycle_id == 3
    assert out.phase == 0
    assert out.current_cell == (2, 1)
    events = _read_jsonl(work / "events.jsonl")
    abandoned = [e for e in events if e.get("kind") == "cycle_abandoned"]
    assert len(abandoned) >= 1
    assert "replay exploded" in str(abandoned[-1].get("error"))
    assert all("ts" in e for e in events)
    assert read_all() == []


def test_B59_run_cycle_abandons_when_verifier_hash_raises(monkeypatch, tmp_path):
    work = _setup_env(monkeypatch, tmp_path)

    def boom(*_a, **_k):
        raise OSError("hash files unreadable")

    monkeypatch.setattr("rk_harness.verifier_hash.compute_verifier_hash", boom)
    st = _runstate(cycle_id=0, stall_counter=0)
    out = run_cycle(st)
    assert out.cycle_id == 0
    assert out.stall_counter == 1
    kinds = [e.get("kind") for e in _read_jsonl(work / "events.jsonl")]
    assert "cycle_abandoned" in kinds


@pytest.mark.slow
def test_B58_run_cycle_phase0_seeds_baselines_and_enumerates_idempotently(monkeypatch, tmp_path):
    from rk_harness.enumeration import enumerate_phase0

    work = _setup_env(monkeypatch, tmp_path, phase="0")
    st0 = load_state()
    assert st0.cycle_id == 0 and st0.phase == 0

    st1 = run_cycle(st0)
    assert isinstance(st1, RunState)
    assert st1.cycle_id == 1

    recs = read_all()
    assert len(recs) >= 9
    hashes = [r.tableau_hash for r in recs]
    assert len(set(hashes)) == len(hashes), "duplicate tableau_hash in archive"
    vh = compute_verifier_hash()
    baseline_hashes = {content_hash(t) for t in _classical_8().values()}
    assert baseline_hashes <= set(hashes)
    for r in recs:
        assert r.tier in TIERS
        assert r.verifier_hash == vh
        assert r.tableau_hash == content_hash(r.tableau)
        if r.tableau_hash in baseline_hashes:
            assert r.directive_id is None
            assert r.cycle_id == 0
        else:
            assert isinstance(r.directive_id, str) and r.directive_id.startswith("D-E")
            assert r.cycle_id == 1

    events = _read_jsonl(work / "events.jsonl")
    rejected = {e.get("tableau_hash") for e in events if e.get("kind") == "rejected"}
    archived = set(hashes)
    for t in enumerate_phase0():
        h = content_hash(t)
        assert h in archived or h in rejected, f"phase-0 point {h} neither archived nor rejected"

    assert (work / "RUNSTATE.json").is_file()
    assert load_state().cycle_id == 1

    st2 = run_cycle(dataclasses.replace(st1, phase=0))
    assert st2.cycle_id == 2
    recs2 = read_all()
    assert len(recs2) == len(recs)
    assert {r.tableau_hash for r in recs2} == archived
    assert st2.stall_counter == st1.stall_counter + 1
    assert load_state().cycle_id == 2


@pytest.mark.slow
def test_B58_seed_baselines_adds_the_eight_classical_once(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    vh = compute_verifier_hash()
    assert isinstance(vh, str) and len(vh) == 64
    assert seed_baselines(vh) == 8
    recs = read_all()
    assert len(recs) == 8
    assert {r.tableau_hash for r in recs} == {content_hash(t) for t in _classical_8().values()}
    for r in recs:
        assert r.cycle_id == 0
        assert r.seed == 0
        assert r.tier == "unreplicated"
        assert r.directive_id is None
        assert r.hypothesis_id is None
        assert r.verifier_hash == vh
        assert math.isfinite(r.score.heldout_error)
    assert seed_baselines(vh) == 0
    assert len(read_all()) == 8


@pytest.mark.slow
def test_E2_two_fresh_runs_produce_byte_identical_archives(monkeypatch, tmp_path):
    outs = []
    for name in ("a", "b"):
        _setup_env(monkeypatch, tmp_path / name, phase="0")
        st = run_cycle(load_state())
        assert st.cycle_id == 1
        files = sorted(archive_dir().glob("*.jsonl"))
        assert files, "no archive file written"
        outs.append({f.name: f.read_bytes() for f in files})
    assert set(outs[0]) == set(outs[1])
    for name in outs[0]:
        assert len(outs[0][name]) > 0
        assert outs[0][name] == outs[1][name], f"{name} differs between runs"


# ======================================================================================
# Site generator — E3, E4, B61, B62
# ======================================================================================

def test_E3_index_parses_and_shows_every_elite_tier_next_to_its_hash(monkeypatch, tmp_path):
    _work, arch = _site_archive(monkeypatch, tmp_path)
    out = tmp_path / "docs"
    build(arch, out)
    index = out / "index.html"
    assert index.is_file()
    html = index.read_text(encoding="utf-8")
    col = _Collector()
    col.feed(html)
    col.close()
    text = " ".join(col.text)
    assert "title" in col.tags
    assert col.prov_p >= 1
    assert col.scripts == 0
    assert BANNER in text
    assert html.lower().lstrip().startswith("<!doctype html>")

    elites = [rec for grid in arch.grids.values() for rec in grid.values()]
    assert len(elites) == 3
    assert {e.tier for e in elites} == set(TIERS)
    for rec in elites:
        assert rec.tableau_hash in text
        assert rec.tier in text
        assert rec.verifier_hash in text
        i = html.index(rec.tableau_hash)
        window = html[max(0, i - 2000): i + 2000]
        assert rec.tier in window, f"tier of {rec.tableau_hash} not next to its hash"


def test_E3_every_elite_gets_a_cell_page(monkeypatch, tmp_path):
    _work, arch = _site_archive(monkeypatch, tmp_path)
    out = tmp_path / "docs"
    build(arch, out)
    for order, grid in arch.grids.items():
        for (stages, bucket), rec in grid.items():
            page = out / f"cell-p{order}-s{stages}-b{bucket}.html"
            assert page.is_file(), page.name
            html = page.read_text(encoding="utf-8")
            assert rec.tableau_hash in html
            assert rec.verifier_hash in html
            assert rec.tier in html
    expected = {"index.html", "hypotheses.html", "costmodel.html", "falsification.html",
                "cell-p4-s4-b2.html", "cell-p3-s3-b1.html", "cell-p2-s2-b0.html"}
    assert expected <= {p.name for p in out.iterdir()}


def test_E4_generated_site_contains_no_banned_word(monkeypatch, tmp_path):
    assert {"novel", "first", "beats", "outperforms", "breakthrough"} <= set(BANNED_WORDS)
    _work, arch = _site_archive(monkeypatch, tmp_path)
    out = tmp_path / "docs"
    build(arch, out)
    files = list(out.rglob("*.html"))
    assert len(files) >= 7
    for f in files:
        html = f.read_text(encoding="utf-8")
        assert _BANNED_RE.search(html) is None, f"{f.name} contains a banned word"
        check_banned(html)


@pytest.mark.parametrize("text", [
    "this is the first",
    "NOVEL",
    "a Novel approach",
    "<b>novel</b>",
    "it beats x",
    "outperforms",
    "a breakthrough.",
    "this proves it",
    "state-of-the-art",
    "best-ever",
    "first,second",
    "(first)",
    "the-first-one",
    "Beats!",
    "<td>state-of-the-art</td>",
])
def test_E4_check_banned_raises_on_whole_word_case_insensitive_hit(text):
    with pytest.raises(BannedWordError):
        check_banned(text)


@pytest.mark.parametrize("text", [
    "firstly",
    "",
    "novelty",
    "beat",
    "proved",
    "breakthroughs",
    "unbeatable",
    "<p>heldout_verified 0123abcd</p>",
    BANNER,
    AVR_NOTE,
])
def test_E4_check_banned_passes_on_non_matches(text):
    assert check_banned(text) is None


def test_E4_build_raises_and_writes_nothing_when_archive_text_contains_a_banned_word(monkeypatch, tmp_path):
    _work, _ = _site_archive(monkeypatch, tmp_path)
    append_hypothesis(_hyp(id="H-009", statement="a novel method"))
    arch = replay()
    assert "H-009" in arch.open_hypotheses
    out = tmp_path / "docs"
    with pytest.raises(BannedWordError):
        build(arch, out)
    assert (not out.exists()) or list(out.rglob("*.html")) == []


def test_E4_render_hypotheses_output_is_subject_to_check_banned():
    html = render_hypotheses([_hyp(id="H-009", statement="a novel method")])
    with pytest.raises(BannedWordError):
        check_banned(html)


def test_B61_every_page_has_provenance_title_doctype_and_no_javascript(monkeypatch, tmp_path):
    _work, arch = _site_archive(monkeypatch, tmp_path)
    out = tmp_path / "docs"
    build(arch, out)
    files = list(out.rglob("*.html"))
    assert len(files) >= 7
    for f in files:
        html = f.read_text(encoding="utf-8")
        assert html.lower().lstrip().startswith("<!doctype html>"), f.name
        assert "<title>" in html.lower(), f.name
        assert BANNER in html, f.name
        assert '<p class="prov">' in html, f.name
        assert "<script" not in html.lower(), f.name
        assert "javascript:" not in html.lower(), f.name


def test_B61_costmodel_page_has_the_anchor_numbers_and_avr_note(monkeypatch, tmp_path):
    _work, arch = _site_archive(monkeypatch, tmp_path)
    out = tmp_path / "docs"
    build(arch, out)
    cm = (out / "costmodel.html").read_text(encoding="utf-8")
    for n in ("33", "85", "36", "64", "66", "170", "72", "128", "132", "340", "144", "256"):
        assert n in cm, n
    assert AVR_NOTE in cm
    assert "rk4" in cm and "rk38" in cm
    for name in ("euler", "midpoint", "heun2", "ralston2", "heun3", "kutta3"):
        assert name in cm, name
    assert BANNER in cm
    direct = render_costmodel()
    assert AVR_NOTE in direct and "33" in direct and "64" in direct
    check_banned(direct)


def test_B61_cell_pages_carry_phase_label_tier_hashes_and_fractions(monkeypatch, tmp_path):
    _work, arch = _site_archive(monkeypatch, tmp_path)
    out = tmp_path / "docs"
    build(arch, out)
    rk4_rec = arch.grids[4][(4, 2)]
    kutta3_rec = arch.grids[3][(3, 1)]
    heun2_rec = arch.grids[2][(2, 0)]
    assert rk4_rec.directive_id == "D-E000001"
    assert kutta3_rec.directive_id == "D-0112"
    assert heun2_rec.directive_id is None

    rk4_page = (out / "cell-p4-s4-b2.html").read_text(encoding="utf-8")
    assert "exhaustive" in rk4_page
    assert "optimal within the enumerated space" in rk4_page
    assert "heldout_verified" in rk4_page
    assert rk4_rec.tableau_hash in rk4_page
    assert rk4_rec.verifier_hash in rk4_page
    assert "1/6" in rk4_page and "1/3" in rk4_page and "1/2" in rk4_page
    assert BANNER in rk4_page

    k3_page = (out / "cell-p3-s3-b1.html").read_text(encoding="utf-8")
    assert "search result" in k3_page
    assert "search_only" in k3_page
    assert kutta3_rec.tableau_hash in k3_page
    assert "2/3" in k3_page

    h2_page = (out / "cell-p2-s2-b0.html").read_text(encoding="utf-8")
    assert "search result" in h2_page
    assert "unreplicated" in h2_page
    assert heun2_rec.tableau_hash in h2_page


def test_B61_render_cell_direct():
    rec = _site_records()[0]
    html = render_cell(4, 4, 2, rec)
    assert isinstance(html, str)
    assert html.lower().lstrip().startswith("<!doctype html>")
    assert BANNER in html and '<p class="prov">' in html
    assert rec.tableau_hash in html and rec.verifier_hash in html and rec.tier in html
    assert "exhaustive" in html
    assert "1/6" in html
    assert "<script" not in html.lower()
    check_banned(html)
    search_rec = _site_records()[1]
    html2 = render_cell(3, 3, 1, search_rec)
    assert "search result" in html2 and "search_only" in html2 and search_rec.tableau_hash in html2


def test_B61_render_hypotheses_shows_ids_and_verdicts():
    hyps = [
        _hyp(id="H-001", verdict="supported", n_samples=250, effect_size=1.5, resolved_cycle=7),
        _hyp(id="H-002", verdict="refuted", n_samples=300, effect_size=0.9, resolved_cycle=8),
        _hyp(id="H-003"),
    ]
    html = render_hypotheses(hyps)
    assert html.lower().lstrip().startswith("<!doctype html>")
    assert BANNER in html and "<title>" in html.lower()
    for hid in ("H-001", "H-002", "H-003"):
        assert hid in html
    assert "supported" in html and "refuted" in html
    check_banned(html)
    empty = render_hypotheses([])
    assert BANNER in empty and "<title>" in empty.lower()
    check_banned(empty)


def test_B61_render_falsification_and_index_on_empty_inputs():
    html = render_falsification(None)
    assert html.lower().lstrip().startswith("<!doctype html>")
    assert BANNER in html and "<title>" in html.lower()
    check_banned(html)
    idx = render_index(_empty_arch())
    assert idx.lower().lstrip().startswith("<!doctype html>")
    assert BANNER in idx and "<title>" in idx.lower()
    assert "<script" not in idx.lower()
    check_banned(idx)


def test_B61_render_glossary_defines_terms_with_anchors():
    html = render_glossary()
    assert html.lower().lstrip().startswith("<!doctype html>")
    assert BANNER in html and "<title>" in html.lower()
    assert "<script" not in html.lower()
    for anchor in ("q15", "lsb", "floor-rounding", "tableau", "stage", "cycle-budget",
                   "cost-bucket", "map-elites", "elite", "tiers", "held-out-set",
                   "anchor-methods", "cohens-d", "csd-weight", "dyadic-rational",
                   "order", "directive", "hypothesis-ledger", "verifier-hash"):
        assert f'id="{anchor}"' in html, anchor
    for tier in TIERS:
        assert tier in html                       # the actual tier strings are defined
    check_banned(html)
    assert render_glossary() == render_glossary()  # deterministic


def test_B61_build_writes_glossary_and_index_deep_links_it(monkeypatch, tmp_path):
    _work, arch = _site_archive(monkeypatch, tmp_path)
    out = tmp_path / "docs"
    build(arch, out)
    gl = out / "glossary.html"
    assert gl.is_file()
    check_banned(gl.read_text(encoding="utf-8"))
    index = (out / "index.html").read_text(encoding="utf-8")
    assert 'href="glossary.html"' in index
    assert "glossary.html#" in index              # explanations deep-link into the glossary


def test_B61_nav_lists_every_page_in_order():
    html = render_costmodel()
    nav = html[html.index('<nav class="tabs">'):html.index("</nav>")]
    assert re.findall(r'href="([^"]+)"', nav) == [
        "index.html", "methodology.html", "costmodel.html", "falsification.html",
        "hypotheses.html", "literature.html", "interpretation.html", "glossary.html"]


def test_B61_major_pages_carry_collapsed_explanations(monkeypatch, tmp_path):
    _work, arch = _site_archive(monkeypatch, tmp_path)
    out = tmp_path / "docs"
    build(arch, out)
    for name in ("index.html", "costmodel.html", "falsification.html", "hypotheses.html",
                 "literature.html", "interpretation.html", "cell-p4-s4-b2.html"):
        html = (out / name).read_text(encoding="utf-8")
        assert '<details class="explain">' in html, name
        assert "How to read this" in html, name


def test_B61_every_chart_opens_with_a_visible_caption(monkeypatch, tmp_path):
    _work, arch = _site_archive(monkeypatch, tmp_path)
    out = tmp_path / "docs"
    build(arch, out)
    saw_figures = 0
    for f in out.rglob("*.html"):
        html = f.read_text(encoding="utf-8")
        n = html.count("<figure>")
        saw_figures += n
        assert n == html.count("<figcaption>"), f.name
        # every figure leads with its always-visible caption
        assert "<figure>" not in html.replace("<figure><figcaption>", ""), f.name
    assert saw_figures >= 3  # scatter + heatmaps + per-problem bars at minimum


def test_B61_hypotheses_page_folds_each_hypothesis_with_provenance():
    hyps = [
        _hyp(id="H-001", verdict="supported", n_samples=250, effect_size=1.5, resolved_cycle=7),
        _hyp(id="H-003"),
    ]
    html = render_hypotheses(hyps)
    assert html.count('<details class="fold">') == 2
    m = re.search(r'<summary><span class="mono">H-001</span> <span class="badge badge-supported">'
                  r"supported</span>[^<]*<span class=\"when\">effect size 1\.5, n = 250", html)
    assert m is not None, "summary must carry id + status + effect size"
    assert "slow.p3s4.heldout" in html            # full predicate text in the body
    assert "verdict computed by the ledger" in html and "at cycle 7" in html
    assert "open: the ledger resolves it automatically" in html   # H-003 is unresolved
    assert "112" in html                          # cycle_proposed is shown
    check_banned(html)


def test_B61_cell_page_shows_dyadic_coefficient_representation():
    rec = _site_records()[0]                      # rk4
    html = render_cell(4, 4, 2, rec)
    assert "Raw coefficient representation (m/2^s)" in html
    assert "A[1][0]" in html and "b[0]" in html   # nonzero entries are listed
    assert "A[2][0]" not in html                  # zero entries are skipped
    check_banned(html)


def test_B61_stored_utc_timestamps_display_in_central_time():
    rec = _site_records()[0]                      # timestamp CLOCK = 2026-09-21T10:00:00Z (CDT)
    html = render_cell(4, 4, 2, rec)
    assert "2026-09-21 05:00 CT" in html
    assert CLOCK in html                          # the stored UTC value stays visible


def test_B61_literature_and_interpretation_entries_fold_with_ct_dates():
    digests = [{"ts": "2026-09-21T10:00:00Z", "cycle": 5, "topic": "fixed point drift",
                "summary": "para one.\n\npara two.", "key_points": ["k1"],
                "sources": [{"title": "paper", "url": "https://example.org/x"}]}]
    lit = render_literature(digests)
    assert '<details class="fold entry" open>' in lit
    assert "fixed point drift" in lit and "2026-09-21 05:00 CT" in lit
    assert "para two." in lit and "example.org" in lit
    check_banned(lit)
    interp = render_interpretation([{"ts": "2026-09-21T10:01:00Z", "cycle": 6,
                                     "text": "reading one.\n\nreading two."}])
    assert '<details class="fold entry" open>' in interp
    assert "cycle 6" in interp and "2026-09-21 05:01 CT" in interp and "reading two." in interp
    check_banned(interp)


def test_B61_heatmap_rows_extend_to_occupied_stage_counts_outside_the_default_range():
    """Design review fix 1: an order-1 elite at stages=1 must get a heatmap row."""
    c = _classical_8()
    rec = _rec(c["euler"], _sv(13, 13, 0.005, 0.5942853, 1.0), "unreplicated", 1, None)
    arch = ArchiveState(n_records=1, last_cycle_id=1,
                        grids={1: {(1, 0): rec}, 2: {}, 3: {}, 4: {}},
                        open_hypotheses=(), refuted_hypotheses=())
    html = render_index(arch)
    i = html.index('aria-label="Order 1 elite grid heatmap"')
    svg = html[html.rindex("<svg", 0, i):html.index("</svg>", i)]
    assert ">s=1<" in svg                          # the occupied row is rendered
    assert 'href="cell-p1-s1-b0.html"' in svg      # with its linked, filled cell
    assert ">0.594<" in svg                        # cell label at 3 significant figures
    assert "0.594285" in svg                       # full-precision value in the tooltip
    for s in (2, 3, 4, 5, 6):
        assert f">s={s}<" in svg                   # default rows still render
    assert "counts run 2 to 6" not in html         # the old fixed-range claim is gone
    check_banned(html)


def test_B61_cell_bar_value_labels_stay_inside_the_viewbox():
    """Design review fix 2: a label after a long bar moves end-anchored inside the bar."""
    c = _classical_8()
    rec = _rec(c["euler"], _sv(13, 13, 4.53999e-05, 0.5942853, 1.0), "unreplicated", 1, None)
    html = render_cell(1, 1, 0, rec)
    i = html.index('aria-label="Per-problem error, log scale"')
    svg = html[html.rindex("<svg", 0, i):html.index("</svg>", i)]
    labels = re.findall(
        r'<text class="lbl" x="([\d.]+)" y="[\d.]+"( text-anchor="end")?>([^<]+)</text>', svg)
    assert labels
    saw_inside = False
    for x, end_anchor, txt in labels:
        end_x = float(x) if end_anchor else float(x) + len(txt) * 6.6
        assert end_x <= 560, (txt, end_x)          # estimated end within the viewBox
        saw_inside = saw_inside or bool(end_anchor)
    assert saw_inside                              # the longest bar's label sits inside
    check_banned(html)


def test_B61_interpretation_folds_superseded_same_cycle_drafts():
    """Design review fix 4: one top-level entry per cycle; older drafts fold inside."""
    entries = [
        {"ts": "2026-09-21T08:00:00Z", "cycle": 5, "text": "cycle five reading."},
        {"ts": "2026-09-21T10:01:00Z", "cycle": 6, "text": "draft reading."},
        {"ts": "2026-09-21T10:05:00Z", "cycle": 6, "text": "final reading."},
    ]
    html = render_interpretation(entries)
    assert html.count('<details class="fold entry"') == 2   # one entry per cycle
    assert html.count("<summary><strong>cycle 6</strong>") == 1
    assert "2026-09-21 05:05 CT" in html                    # newest draft speaks for cycle 6
    assert "superseded same-cycle drafts (1)" in html
    i_top = html.index("final reading.")
    i_fold = html.index("superseded same-cycle drafts")
    i_draft = html.index("draft reading.")
    assert i_top < i_fold < i_draft                         # older draft folded below
    assert html.index("cycle 6</strong>") < html.index("cycle 5</strong>")
    assert html.count('<details class="fold entry" open>') == 1
    assert render_interpretation(entries) == render_interpretation(entries)
    check_banned(html)


def test_B61_falsification_page_reflects_falsification_json(monkeypatch, tmp_path):
    work, arch = _site_archive(monkeypatch, tmp_path)
    (work / "falsification.json").write_text(
        json.dumps({"verdict": "proceed", "problem": "damped_osc",
                    "coefficient_fraction": {"rk4": {"m0plus_fast": 0.41}}}), encoding="utf-8")
    out = tmp_path / "docs"
    build(arch, out)
    page = (out / "falsification.html").read_text(encoding="utf-8")
    assert BANNER in page
    assert "proceed" in page


def test_B62_build_is_deterministic_and_creates_missing_out_dir(monkeypatch, tmp_path):
    _work, arch = _site_archive(monkeypatch, tmp_path)
    out1 = tmp_path / "d1" / "nested" / "docs"
    out2 = tmp_path / "d2"
    assert not out1.exists()
    build(arch, out1)
    assert out1.is_dir()
    build(arch, out2)
    s1 = _snapshot(out1)
    s2 = _snapshot(out2)
    assert len(s1) >= 7
    assert s1 == s2
    # rebuilding into the same directory leaves it byte-identical
    build(arch, out1)
    assert _snapshot(out1) == s1
    # a fresh replay of the same archive gives the same site
    build(replay(), tmp_path / "d3")
    assert _snapshot(tmp_path / "d3") == s1
    assert render_index(arch) == render_index(arch)
    assert render_costmodel() == render_costmodel()


def test_B62_different_archives_give_different_indexes(monkeypatch, tmp_path):
    _work, arch = _site_archive(monkeypatch, tmp_path)
    assert render_index(arch) != render_index(_empty_arch())


# ======================================================================================
# Dashboard — B63
# ======================================================================================

def test_B63_build_layout_returns_rich_layout(monkeypatch, tmp_path):
    _work, arch = _site_archive(monkeypatch, tmp_path)
    layout = build_layout(arch, _runstate())
    assert isinstance(layout, Layout)
    layout2 = build_layout(_empty_arch(), _runstate(phase=2, current_cell=(4, 2), stall_counter=12))
    assert isinstance(layout2, Layout)


def test_B63_render_prints_and_writes_nothing(monkeypatch, tmp_path, capsys):
    work, arch = _site_archive(monkeypatch, tmp_path)
    for i in range(3):
        log_event("rejected", code="ORDER_NOT_MET", tableau_hash=f"h{i}")
    save_state(_runstate())
    before = _snapshot(work)
    assert before  # archive + events + runstate exist

    render(arch, _runstate(phase=0))
    out = capsys.readouterr().out
    assert out.strip() != ""
    assert re.search(r"\d+\s*/\s*16", out), "phase 0 candidates panel must show visited/16"
    assert _snapshot(work) == before

    render(arch, _runstate(phase=2, current_cell=(4, 2), stall_counter=3))
    assert capsys.readouterr().out.strip() != ""
    assert _snapshot(work) == before
    assert not (work / "docs").exists()
    assert not (findings_dir() / "docs").exists()


def test_B63_render_on_fresh_work_dir_does_not_raise_or_write(monkeypatch, tmp_path, capsys):
    work = _setup_env(monkeypatch, tmp_path)
    before = _snapshot(work)
    render(_empty_arch(), _runstate(cycle_id=0))
    assert capsys.readouterr().out.strip() != ""
    assert _snapshot(work) == before
    assert not (work / "events.jsonl").exists()
    assert not (work / "RUNSTATE.json").exists()


def test_B63_read_events_returns_the_tail_in_order(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    for i in range(25):
        log_event("tick", i=i)
    ev = read_events()
    assert len(ev) == 20
    assert [e["i"] for e in ev] == list(range(5, 25))
    ev3 = read_events(limit=3)
    assert [e["i"] for e in ev3] == [22, 23, 24]
    assert all(e["kind"] == "tick" and "ts" in e for e in ev3)
    assert len(read_events(limit=100)) == 25
    assert [e["i"] for e in read_events(limit=1)] == [24]


# ======================================================================================
# Heartbeat — B60 (LAST: starts a daemon thread that outlives the test)
# ======================================================================================

def test_B60_heartbeat_writes_an_iso_timestamp_within_one_second(monkeypatch, tmp_path):
    work = _setup_env(monkeypatch, tmp_path, clock=None)
    hb = work / "HEARTBEAT"
    assert not hb.exists()
    t0 = dt.datetime.now(UTC)
    start = time.monotonic()
    heartbeat()
    assert time.monotonic() - start < 1.0, "heartbeat() must return immediately (daemon thread)"
    deadline = time.monotonic() + 2.0
    content = ""
    while time.monotonic() < deadline:
        if hb.exists():
            try:
                content = hb.read_text(encoding="utf-8").strip()
            except OSError:
                content = ""
            if content:
                break
        time.sleep(0.05)
    t1 = dt.datetime.now(UTC)
    assert content, "HEARTBEAT not written within 2 s"
    stamp = _parse_iso(content)
    assert t0 - dt.timedelta(seconds=1) <= stamp <= t1 + dt.timedelta(seconds=1)
    assert stamp.utcoffset() == dt.timedelta(0)
