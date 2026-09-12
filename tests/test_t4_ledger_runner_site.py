"""T4 — ledger, quarantine, runner, archive recovery, and the site generator.

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
from rk_harness import policyab
from rk_harness import runner as runner_mod
from rk_harness import search as search_mod
from rk_harness.directive import fallback_directive
from rk_harness.verifier_hash import compute_verifier_hash
from rk_harness import methodology as methodology_mod
from rk_harness import sitegen as sg
from rk_harness.sitegen import (
    BANNED_WORDS, BANNER, AVR_NOTE, OVERVIEW_URL, BannedWordError, build, render_index,
    render_cell, render_hypotheses, render_validation,
    render_explicit, render_implicit, render_adaptive,
    check_banned, epoch_status_data,
)
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
        _rec(c["heun2"], _sv(13, 13, 0.005, 0.006, 2.0), "no_improvement", 3, None),
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
    append(_rec(c["rk4"], _sv(33, 85, 0.001, 0.002), "no_improvement", 3, None))
    append(_rec(c["rk38"], _sv(36, 64, 0.002, 0.003), "no_improvement", 4, None))
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
    append(_rec(_classical_8()["rk4"], _sv(33, 85, 0.001, 0.002), "no_improvement", 5, None))
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
    append(_rec(c["rk4"], _sv(33, 85, 0.001, 0.002), "no_improvement", 1, None))
    append(_rec(c["kutta3"], _sv(26, 65, 0.003, 0.004), "no_improvement", 2, None))
    append(_rec(c["heun2"], _sv(13, 13, 0.005, 0.006), "no_improvement", 3, None))
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
    append(_rec(c["rk4"], _sv(33, 85, 0.001, 0.002), "no_improvement", 1, None))
    append(_rec(c["kutta3"], _sv(26, 65, 0.003, 0.004), "no_improvement", 2, None))
    append(_rec(c["heun2"], _sv(13, 13, 0.005, 0.006), "no_improvement", 3, None))
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
        assert r.tier == "no_incumbent"
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
    """The elite table moved from index.html to explicit.html; both must still parse."""
    _work, arch = _site_archive(monkeypatch, tmp_path)
    out = tmp_path / "docs"
    build(arch, out)
    index = out / "index.html"
    assert index.is_file()
    idx_html = index.read_text(encoding="utf-8")
    assert idx_html.lower().lstrip().startswith("<!doctype html>")
    assert 'href="explicit.html"' in idx_html     # the hub links on to the archive
    html = (out / "explicit.html").read_text(encoding="utf-8")
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
    # The three site records cover the three tiers a search can write. "unreplicated" is
    # still in TIERS for the records already on disk, so the set is named, not derived.
    assert {e.tier for e in elites} == {"heldout_verified", "search_only", "no_improvement"}
    assert all(e.tier in TIERS for e in elites)
    for rec in elites:
        # The table prints a 12-character prefix and carries the full hash in the link's
        # title, so the row stays narrow without losing provenance.
        assert rec.tableau_hash[:12] in text
        assert rec.tableau_hash in html
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
    expected = {"index.html", "explicit.html", "implicit.html", "adaptive.html",
                "hypotheses.html", "methodology.html",
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


def test_B61_costmodel_section_has_the_anchor_numbers_and_avr_note(monkeypatch, tmp_path):
    """The cost model moved from costmodel.html into methodology.html#costmodel."""
    _work, arch = _site_archive(monkeypatch, tmp_path)
    out = tmp_path / "docs"
    build(arch, out)
    assert not (out / "costmodel.html").exists()
    cm = (out / "methodology.html").read_text(encoding="utf-8")
    assert '<h2 id="costmodel">Cost model</h2>' in cm
    section = cm.split('<h2 id="costmodel">', 1)[1].split("<h2 ", 1)[0]
    for n in ("33", "85", "36", "64", "66", "170", "72", "128", "132", "340", "144", "256"):
        assert n in section, n
    assert AVR_NOTE in section and "avr_approx" in section
    assert "rk4" in section and "rk38" in section
    for name in ("euler", "midpoint", "heun2", "ralston2", "heun3", "kutta3"):
        assert name in section, name
    assert BANNER in cm
    assert 'class="caption"' not in cm          # the undefined class is gone
    direct = sg._costmodel_section()
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
    assert "no_improvement" in h2_page
    assert heun2_rec.tableau_hash in h2_page


def test_a_seeded_classical_tableau_is_not_labelled_a_search_result():
    """Two archive cells are held by tableaus seeded before the search ran.

    Labelling those "search result" told the reader the search had found euler and rk38,
    and contradicted the overview site, which counts them as classical. The rule reads the
    record: no directive and cycle 0. heun2 carries no directive either but was archived
    in cycle 3, so the cycle half of the test is load-bearing rather than decorative.
    """
    c = _classical_8()
    seeded = _rec(c["euler"], _sv(5, 5, 0.32, 0.33, 1.0), "no_incumbent", 0, None)
    searched = _site_records()[2]               # heun2: no directive, cycle 3
    enumerated = _site_records()[0]             # rk4: D-E000001

    assert sg._is_seeded(seeded)
    assert not sg._is_seeded(searched) and not sg._is_seeded(enumerated)
    assert sg._phase_label(seeded) == "seeded classical baseline"
    assert sg._phase_label(searched) == "search result"
    assert sg._phase_label(enumerated) == sg._EXHAUSTIVE_LABEL

    page = render_cell(1, 1, 0, seeded)
    assert "seeded classical baseline" in page
    assert "search result" not in page
    check_banned(page)


def test_the_explicit_cards_and_elite_table_count_seeded_cells_apart(monkeypatch, tmp_path):
    """A seeded cell is occupied but it is not a thing the search found, so the coverage
    note and the elite table both say how many of the occupied cells each process holds.
    With no seeded record in the archive neither says anything about seeding."""
    _setup_env(monkeypatch, tmp_path)
    c = _classical_8()
    for r in _site_records():
        append(r)
    append(_rec(c["euler"], _sv(5, 5, 0.32, 0.33, 1.0), "no_incumbent", 0, None))
    arch = replay()
    html = render_explicit(arch)
    assert "4 cells hold an elite, 3 found by the search and 1 seeded classical" in html
    assert ("1 of these rows is a seeded classical baseline rather than a search result"
            in html)

    plain = render_explicit(_site_archive(monkeypatch, tmp_path / "plain")[1])
    assert "seeded classical" not in plain
    assert "of these rows is a seeded" not in plain


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


def test_B61_render_validation_and_index_on_empty_inputs():
    html = render_validation(None)
    assert html.lower().lstrip().startswith("<!doctype html>")
    assert BANNER in html and "<title>" in html.lower()
    # both merged sections keep their anchors and say in words that the source is absent
    assert '<h2 id="speed">' in html and '<h2 id="falsification">' in html
    assert "rk-work/benchmark/results.json has not been written" in html
    assert "rk-work/falsification.json has not been written" in html
    check_banned(html)
    idx = render_index(_empty_arch())
    assert idx.lower().lstrip().startswith("<!doctype html>")
    assert BANNER in idx and "<title>" in idx.lower()
    assert "<script" not in idx.lower()
    check_banned(idx)


_GLOSSARY_ANCHORS = ("q15", "lsb", "floor-rounding", "tableau", "stage", "cycle-budget",
                     "cost-bucket", "map-elites", "elite", "tiers", "held-out-set",
                     "anchor-methods", "cohens-d", "csd-weight", "dyadic-rational",
                     "order", "directive", "hypothesis-ledger", "verifier-hash")


def test_B61_glossary_section_defines_terms_with_anchors():
    """The glossary moved from glossary.html into methodology.html#glossary, every term
    keeping its anchor id so the deep links resolve at the new address."""
    section = sg._glossary_section()
    for anchor in _GLOSSARY_ANCHORS:
        assert f'id="{anchor}"' in section, anchor
    for anchor, _term, _paras in sg._GLOSSARY:     # every term, not only the pinned few
        assert f'<dt id="{anchor}">' in section, anchor
    for tier in TIERS:
        assert tier in section                    # the actual tier strings are defined
    check_banned(section)
    assert sg._glossary_section() == sg._glossary_section()  # deterministic
    html = methodology_mod.render_page(sg._page, sg._methodology_sections(None))
    assert html.lower().lstrip().startswith("<!doctype html>")
    assert BANNER in html and "<title>" in html.lower()
    assert "<script" not in html.lower()
    assert '<h2 id="glossary">Glossary</h2>' in html
    check_banned(html)


def test_B61_build_writes_the_glossary_into_methodology_and_the_index_deep_links_it(
        monkeypatch, tmp_path):
    _work, arch = _site_archive(monkeypatch, tmp_path)
    out = tmp_path / "docs"
    build(arch, out)
    assert not (out / "glossary.html").exists()
    meth = (out / "methodology.html").read_text(encoding="utf-8")
    check_banned(meth)
    for anchor in _GLOSSARY_ANCHORS:
        assert f'id="{anchor}"' in meth, anchor
    index = (out / "index.html").read_text(encoding="utf-8")
    # the hub deep-links the glossary from a term in its own table, not from a
    # sentence about the page (the note that said so is gone)
    assert 'href="methodology.html#' in index
    assert "Terms link to the" not in index
    assert 'href="methodology.html#q15"' in index  # explanations deep-link into the glossary
    # every _gloss link anywhere on the site resolves to an id on methodology.html
    ids = set(re.findall(r'id="([^"]+)"', meth))
    for page in sorted(out.glob("*.html")):
        for anchor in re.findall(r'href="methodology\.html#([^"]+)"',
                                 page.read_text(encoding="utf-8")):
            assert anchor in ids, (page.name, anchor)


SIX_TABS = ["index.html", "explicit.html", "implicit.html", "adaptive.html",
            "hypotheses.html", "methodology.html"]
SEVEN_TABS = ["index.html", "explicit.html", "implicit.html", "adaptive.html",
              "validation.html", "hypotheses.html", "methodology.html"]


def _nav_hrefs(html: str) -> list[str]:
    """Every nav link, in reading order.

    The nav is one row of at most seven tabs. Tests assert the whole ordered list so a
    page can neither vanish nor move unnoticed; the regex still reads every nav row, so
    a second row would show up here as extra entries."""
    rows = re.findall(r'<nav class="tabs[^"]*">(.*?)</nav>', html, re.S)
    assert rows, "no nav found"
    return [h for row in rows for h in re.findall(r'href="([^"]+)"', row)]


def test_B61_nav_lists_every_page_in_order():
    # With no evidence file the validation tab is absent everywhere. The three class
    # entries are unconditional and lead the nav in a fixed order.
    html = render_index(_empty_arch())
    assert _nav_hrefs(html) == SIX_TABS
    assert _nav_hrefs(sg.render_hypotheses([])) == SIX_TABS


def test_B61_folds_kept_only_where_depth_remains(monkeypatch, tmp_path):
    """Less-is-more: captions + glossary links replaced most "How to read this" folds.

    A fold survives only where it carries multi-paragraph interpretive depth: the
    archive-grid structure (the explicit class page), the score-metric definitions (cell
    pages), the hypothesis grammar, and the falsification protocol (now on validation).
    """
    work, arch = _site_archive(monkeypatch, tmp_path)
    append_hypothesis(_hyp())
    arch = replay()
    (work / "falsification.json").write_text(json.dumps({"verdict": "mixed"}),
                                             encoding="utf-8")
    out = tmp_path / "docs"
    build(arch, out)
    keeps = {"explicit.html": 1, "cell-p4-s4-b2.html": 1, "hypotheses.html": 1,
             "validation.html": 1}
    for name, n in keeps.items():
        html = (out / name).read_text(encoding="utf-8")
        assert html.count('<details class="explain">') == n, name
        assert "How to read this" in html, name
    for name in ("index.html", "methodology.html"):
        html = (out / name).read_text(encoding="utf-8")
        assert '<details class="explain">' not in html, name
    # the load-bearing sentences moved into always-visible captions and notes
    expl = (out / "explicit.html").read_text(encoding="utf-8")
    assert "down-left is better" in expl           # scatter fold merged into its caption
    assert "overfitting to the visible search set" in expl    # table fold became a note
    cm = (out / "methodology.html").read_text(encoding="utf-8")
    assert "swaps between the multiplier models" in cm        # anchor fold merged
    cell = (out / "cell-p4-s4-b2.html").read_text(encoding="utf-8")
    assert "held-out set" in cell                  # per-problem fold merged into caption


def test_rule10_no_findings_page_states_an_attention_split(monkeypatch, tmp_path):
    """Rule 10: the internal attention split is internal.

    It lives in rk-harness/docs/ROADMAP.md and must not reach a published page, in
    words or as a percentage triple. This turns the convention into a gate that runs on
    every build rather than a thing someone has to remember."""
    work, arch = _site_archive(monkeypatch, tmp_path)
    _write_validation(work, _stiff_validation_fixture())
    _write_benchmark(work, _benchmark_fixture())
    _write_sidetrack(work, _sidetrack_fixture())
    out = tmp_path / "docs"
    build(arch, out)
    triple = re.compile(r"\b\d{1,3}\s*/\s*\d{1,3}\s*/\s*\d{1,3}\b")
    # URLs are not prose. A journal citation whose href ends /3/2/127 has the same
    # shape as a percentage triple, and letting one block the whole site build would
    # be this gate failing at its own job. Attribute values come out before the match;
    # what is left is what a reader actually sees.
    attrs = re.compile(r'\s(?:href|src|content)="[^"]*"')
    seen = 0
    for page in sorted(out.rglob("*.html")):
        html = page.read_text(encoding="utf-8")
        seen += 1
        m = triple.search(attrs.sub(" ", html))
        assert m is None, f"{page.name} carries the percentage-triple shape {m.group(0)!r}"
        assert "attention split" not in html.lower(), page.name
    # seven tabbed pages (validation is present here) plus the three cell pages
    assert seen >= 10


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


def test_B61_hypotheses_page_gives_each_distinct_predicate_one_row():
    """One row per predicate, and the row's summary is the whole machine record.

    The planner re-poses predicates it has already settled, so the page groups by predicate
    and counts the repeats instead of printing near-identical rows."""
    hyps = [
        _hyp(id="H-001", verdict="supported", n_samples=250, effect_size=1.5, resolved_cycle=7),
        _hyp(id="H-002", verdict="supported", n_samples=260, effect_size=1.6, resolved_cycle=9),
        _hyp(id="H-003", predicate="fast.p2s2.heldout < fast.p2s3.heldout"),
    ]
    html = render_hypotheses(hyps)
    # H-001 and H-002 share the default predicate; H-003 has its own.
    assert html.count('<details class="led">') == 2
    m = re.search(r'<summary><span class="mono">H-001 <span class="rep">&times;2</span></span>'
                  r'<span class="badge badge-supported">supported</span>'
                  r'<span class="pred">slow\.p3s4\.heldout[^<]*</span>'
                  r'<span class="num">1\.6</span><span class="num">260</span>'
                  r'<span class="when">c9</span>', html)
    assert m is not None, "summary must carry id, repeat count, verdict, predicate, d, n, cycle"
    # the repeats are listed by id inside the row, not as a nested table of posings
    assert '<details class="repeats">' not in html
    assert '<dt>posed as</dt><dd class="mono">H-001, H-002</dd>' in html
    for hid in ("H-001", "H-002", "H-003"):
        assert hid in html                                   # no record is dropped
    assert "3 hypotheses over 2 distinct predicates" in html
    assert "c112+" in html                                   # H-003 open, shown by proposal cycle
    assert "Verdicts come from code, never from the model" in html
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
    """Both logs moved onto the research log (hypotheses.html): interpretation folds
    with the newest entry open, literature folds closed."""
    digests = [{"ts": "2026-09-21T10:00:00Z", "cycle": 5, "topic": "fixed point drift",
                "summary": "para one.\n\npara two.", "key_points": ["k1"],
                "sources": [{"title": "paper", "url": "https://example.org/x"}]}]
    lit = render_hypotheses([], digests=digests)
    lit_sec = lit.split('<h2 id="literature">', 1)[1]
    assert '<details class="fold entry">' in lit_sec
    assert '<details class="fold entry" open>' not in lit_sec
    assert "fixed point drift" in lit_sec and "2026-09-21 05:00 CT" in lit_sec
    assert "para two." in lit_sec and "example.org" in lit_sec
    check_banned(lit)
    interp = render_hypotheses([], interpretations=[{"ts": "2026-09-21T10:01:00Z", "cycle": 6,
                                                     "text": "reading one.\n\nreading two."}])
    interp_sec = interp.split('<h2 id="interpretation">', 1)[1].split('<h2 id="literature">')[0]
    assert '<details class="fold entry" open>' in interp_sec
    assert ("cycle 6" in interp_sec and "2026-09-21 05:01 CT" in interp_sec
            and "reading two." in interp_sec)
    # the model-written note is stated once for both logs
    assert interp.count("Model-written text") == 1
    check_banned(interp)


def test_D41_research_log_shows_the_newest_entries_and_points_at_the_rest():
    interps = [{"ts": f"2026-09-{10 + i:02d}T10:00:00Z", "cycle": 100 + i,
                "text": f"reading {i}."} for i in range(7)]
    digests = [{"ts": f"2026-09-{10 + i:02d}T11:00:00Z", "cycle": 100 + i,
                "topic": f"topic {i:02d}", "summary": "s."} for i in range(12)]
    html = render_hypotheses([], digests=digests, interpretations=interps)
    interp_sec = html.split('<h2 id="interpretation">', 1)[1].split('<h2 id="literature">')[0]
    lit_sec = html.split('<h2 id="literature">', 1)[1]
    assert interp_sec.count('<details class="fold entry"') == 5
    assert "cycle 106</strong>" in interp_sec and "cycle 101</strong>" not in interp_sec
    assert "2 older entries are not shown here" in interp_sec
    assert "rk-work/interpretation/interpretations.jsonl" in interp_sec
    assert lit_sec.count('<details class="fold entry"') == 10
    assert "topic 11" in lit_sec and "topic 01" not in lit_sec
    assert "2 older digests are not shown here" in lit_sec
    assert "rk-work/literature/digests.jsonl" in lit_sec
    assert html == render_hypotheses([], digests=digests, interpretations=interps)
    check_banned(html)


def test_B61_heatmap_rows_extend_to_occupied_stage_counts_outside_the_default_range():
    """Design review fix 1: an order-1 elite at stages=1 must get a heatmap row."""
    c = _classical_8()
    rec = _rec(c["euler"], _sv(13, 13, 0.005, 0.5942853, 1.0), "no_improvement", 1, None)
    arch = ArchiveState(n_records=1, last_cycle_id=1,
                        grids={1: {(1, 0): rec}, 2: {}, 3: {}, 4: {}},
                        open_hypotheses=(), refuted_hypotheses=())
    html = render_explicit(arch)
    i = html.index('aria-label="Order 1 elite grid heatmap, 1 of 48 cells occupied"')
    svg = html[html.rindex("<svg", 0, i):html.index("</svg>", i)]
    assert ">s=1<" in svg                          # the occupied row is rendered
    assert 'href="cell-p1-s1-b0.html"' in svg      # with its linked, filled cell
    assert ">0.594<" in svg                        # cell label at 3 significant figures
    assert "0.594285" in svg                       # full-precision value in the tooltip
    # the value wears the ink paired with its fill step rather than a haloed label, so
    # it reads on a pale cell and a deep one in either theme
    assert 'style="fill:var(--on-q' in svg and 'class="lbl"' not in svg
    # one figure for the grids: a caption that holds in both themes, one source line
    assert "further a cell's color is from the page background" in html
    assert "deeper blue" not in html.lower()
    assert html.count("Source: rk-work/archive, the elite in each cell") == 2  # scatter, grids
    assert '<p class="ptitle">Order 1</p>' in html
    # the explicit cards are about the explicit class: no telemetry, no research log
    assert "last cycle id" not in html and '<div class="k">hypotheses</div>' not in html
    for s in (2, 3, 4, 5, 6):
        assert f">s={s}<" in svg                   # default rows still render
    assert "counts run 2 to 6" not in html         # the old fixed-range claim is gone
    # the archive's own table expands the grids, so it folds under them rather
    # than at the foot of the page beside the library comparison
    order = [html.index(x) for x in ("<h2>Elite grids</h2>",
                                     "Every elite in one table",
                                     "at matched accuracy</h2>")]
    assert order == sorted(order)
    check_banned(html)


def test_B61_cell_bar_value_labels_stay_inside_the_viewbox():
    """Design review fix 2: a label after a long bar moves end-anchored inside the bar."""
    c = _classical_8()
    rec = _rec(c["euler"], _sv(13, 13, 4.53999e-05, 0.5942853, 1.0), "no_improvement", 1, None)
    html = render_cell(1, 1, 0, rec)
    i = html.index('aria-label="Per-problem error on ')
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


def _svg_blocks(html: str) -> list[tuple[float, float, str]]:
    """(viewBox width, viewBox height, body) for every inline SVG on a page."""
    out = []
    for m in re.finditer(r'<svg\b([^>]*)>(.*?)</svg>', html, re.S):
        attrs = dict(re.findall(r'([a-zA-Z-]+)="([^"]*)"', m.group(1)))
        vb = attrs.get("viewBox", "0 0 0 0").split()
        out.append((float(vb[2]), float(vb[3]), m.group(2)))
    return out


def _svg_label_boxes(svg_body: str) -> list[tuple[float, float, float, float, str]]:
    """Estimated (x0, x1, y0, y1, label) per non-rotated <text>: 7px/char at the
    site's 11px font, anchor-aware, baseline box of one line. The same estimate
    the chart-fit audit uses."""
    boxes = []
    for m in re.finditer(r'<text\b([^>]*)>([^<]*)</text>', svg_body):
        attrs = dict(re.findall(r'([a-zA-Z-]+)="([^"]*)"', m.group(1)))
        if "rotate" in attrs.get("transform", ""):
            continue
        label = m.group(2)
        w = 7.0 * len(label)
        x, y = float(attrs.get("x", 0)), float(attrs.get("y", 0))
        anchor = attrs.get("text-anchor", "start")
        x0 = x - w if anchor == "end" else (x - w / 2 if anchor == "middle" else x)
        boxes.append((x0, x0 + w, y - 8.8, y + 2.2, label))
    return boxes


def _assert_chart_fit(html: str) -> None:
    """The chart-fit audit as a regression check: every non-rotated SVG label stays
    inside its viewBox width, and no two labels sit closer than 4px in both axes."""
    for vb_w, _vb_h, body in _svg_blocks(html):
        boxes = _svg_label_boxes(body)
        for x0, x1, _y0, _y1, label in boxes:
            assert -1e-9 <= x0 and x1 <= vb_w + 1e-9, (label, x0, x1, vb_w)
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                hgap = max(a[0], b[0]) - min(a[1], b[1])
                vgap = max(a[2], b[2]) - min(a[3], b[3])
                assert hgap >= 4.0 or vgap >= 4.0, (a[4], b[4], hgap, vgap)


def test_B61_sweep_chart_y_labels_thin_and_clear_on_extreme_error_spans():
    """Chart-fit audit regression, worst offender: a falsification sweep whose error
    span covers 17 decades used to print a y tick label per decade at ~11px pitch
    (boxes 0.6px apart) and let the corner x tick collide with the bottom y label.
    Labels now thin to every k-th decade and the bottom label nudges clear."""
    sweep = []
    for k in range(8):
        h = 10.0 ** -(k % 4 + 1)
        sweep.append({"h": h, "n_steps": 8 * 2 ** k,
                      "q15_error": 0.5 / (k + 1), "float_error": 3.0e-17 * 10 ** k})
    data = {"verdict": "proceed",
            "coefficient_fraction": {"rk4": {"m0plus_fast": 0.41}},
            "methods": {"rk4": {"crossover_h": 0.01, "sweep": sweep}}}
    html = render_validation(None, falsification=data)
    i = html.index("rk4: Q15 and float64 error against step size")
    svg = html[html.rindex("<svg", 0, i):html.index("</svg>", i) + 6]
    # y tick labels are end-anchored in the left gutter; the span is 1e-17..1e0,
    # so an unthinned axis would print 18 of them at ~15px pitch
    ylabels = re.findall(r'<text x="50" y="([\d.]+)" text-anchor="end">(1e-?\d+)</text>', svg)
    assert 2 <= len(ylabels) < 18
    ys = sorted(float(y) for y, _t in ylabels)
    assert min(b - a for a, b in zip(ys, ys[1:])) >= 15.0   # 11px box + 4px gap
    assert max(ys) <= 300 - 34 - 2 + 1e-9                   # bottom label nudged clear
    _assert_chart_fit(html)
    check_banned(html)


def test_B61_index_and_cell_charts_pass_the_fit_audit(monkeypatch, tmp_path):
    """Chart-fit audit regression across the standing pages: the elite scatter's
    axis-corner labels, heatmap cells, per-problem bars, the anchor bars on the
    methodology page and the verdict chart on the research log all keep 4px clearance."""
    work, arch = _site_archive(monkeypatch, tmp_path)
    append_hypothesis(_hyp())
    append_hypothesis(_hyp(id="H-048", predicate="fast.p2s2.heldout < fast.p2s3.heldout",
                           verdict="inconclusive", n_samples=300, effect_size=0.1,
                           resolved_cycle=9))
    arch = replay()
    out = tmp_path / "docs"
    build(arch, out)
    for name in ("index.html", "explicit.html", "implicit.html", "adaptive.html",
                 "methodology.html", "hypotheses.html", "cell-p4-s4-b2.html"):
        _assert_chart_fit((out / name).read_text(encoding="utf-8"))
    assert 'aria-label="Distinct predicates in each verdict group' in (
        out / "hypotheses.html").read_text(encoding="utf-8")


def test_F2_class_page_charts_fit_their_frames_on_extreme_inputs():
    """The chart-fit audit on every class-page chart, with inputs chosen to break it: a
    problem name wider than a panel, error spans of twenty decades, counts near a
    billion, a cycle budget at the far end of its ladder, a bar that fills its frame."""
    long = "a_problem_name_well_past_the_width_of_one_small_multiple_panel"
    rows = [{"class": "implicit", "side": side, "solver": solver, "problem": prob,
             "arithmetic": "float64", "target_error": 1e-3, "nfev": nfev,
             "achieved_error": err, "status": "reached", "stiff": True}
            for prob in (long, "b")
            for side, solver, nfev, err in (("ours", "sdirk2_fd_jac", 3, 1e-17),
                                            ("library", "Radau", 2_000_000, 0.9),
                                            ("library", "BDF", 40, 1e-9))]
    sweep = [({"key": long}, {"problem": long, "arithmetic": "float64", "points": [
        {"tol": 1e-3, "n_fevals": 5, "achieved_error": 3e-2, "status": "ok"},
        {"tol": 1e-12, "n_fevals": 9_000_000, "achieved_error": 1e-19, "status": "ok"}]})]
    budget = {long: {"budget_cycles": 2 ** 21, "cost_grade": "design_estimate",
                     "arithmetic": "float64", "ladder": [
                         {"factor": "1/4", "analytic_cycles": 2 ** 19, "error": 1e-3,
                          "status": "ok"},
                         {"factor": "1/1", "analytic_cycles": 2 ** 21, "error": 1e-12,
                          "status": "ok"},
                         {"factor": "2/1", "analytic_cycles": 2 ** 22, "error": None,
                          "status": "diverged"}]}}
    gains = [({"summary": {"rejection_rate": r}},
              {"params": {"alpha": [a, 256], "beta": [b, 256]}, "arithmetic": "float64"})
             for a, b, r in ((255, 1, 0.5), (1, 255, 1e-5), (255, 255, 0.0),
                             (1, 1, 0.123456))]
    census = [({"key": long}, {"status": "ok", "arithmetic": "exact", "params": {},
                               "counts": {"matrices": 987654321,
                                          "order2_consistent": 987654321,
                                          "order3_consistent": 1, "both_solvable": 0}})]
    pac = {"stage": 9000, "estimate": 1, "controller": 1, "branch_allowance": 5000,
           "total": 9002}
    amt = [{"solver": "bs32_q15", "n_states": n, "per_attempt_cycles": pac,
            "cost_grade": "g", "arithmetic": "q15"} for n in (2, 3, 4)]
    jac = {"analytic": {"terms": {"newton_iterations": 50000, "lu_factor": 1,
                                  "stage_base": 1}},
           "finite_difference": {"terms": {"newton_iterations": 1,
                                           "fd_jacobian_arith": 99999}}}
    stab = [{"gamma": "1/1024", "gamma_float": 1 / 1024, "r_at_infinity_float": 1e6,
             "a_stable": False},
            {"gamma": "1023/1024", "gamma_float": 1023 / 1024,
             "r_at_infinity_float": -1e-6, "a_stable": True}]
    lane = [{"key": "k" * 16, "method": {"a21": a21, "gamma": "1/2", "jacobian": "analytic"},
             "score": {"median_cycles_at_target": m, "best_achieved_error": e},
             "controller": {}}
            for a21, m, e in (("-1000", 1.5, 1e-15), ("1000000", 123456789.0, 0.5))]
    charts = [sg._matched_chart(rows, "implicit"), sg._sweep_multiples(sweep),
              sg._budget_chart(budget), sg._gain_map(gains), sg._census_chart(census),
              sg._attempt_cost_chart({"adaptive_matched_tolerance": amt}),
              sg._jacobian_chart([({"key": long}, {"problem": long, "cycles": jac})]),
              sg._stability_chart([({}, {"rows": stab})]),
              sg._lane_strip_chart(lane, "rk-work/implicit_archive/elites.json"),
              sg._lane_scatter_chart(lane, "rk-work/adaptive_archive/elites.json")]
    for chart in charts:
        assert chart.startswith("<figure><figcaption>"), chart[:80]
        _assert_chart_fit(chart)
        check_banned(chart)


def test_B61_interpretation_folds_superseded_same_cycle_drafts():
    """Design review fix 4: one top-level entry per cycle; older drafts fold inside."""
    entries = [
        {"ts": "2026-09-21T08:00:00Z", "cycle": 5, "text": "cycle five reading."},
        {"ts": "2026-09-21T10:01:00Z", "cycle": 6, "text": "draft reading."},
        {"ts": "2026-09-21T10:05:00Z", "cycle": 6, "text": "final reading."},
    ]
    html = render_hypotheses([], interpretations=entries)
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
    assert (render_hypotheses([], interpretations=entries)
            == render_hypotheses([], interpretations=entries))
    check_banned(html)


def test_B61_falsification_section_reflects_falsification_json(monkeypatch, tmp_path):
    """falsification.html is retired; the experiment is validation.html#falsification,
    and falsification.json alone is enough to put the validation tab on the site."""
    work, arch = _site_archive(monkeypatch, tmp_path)
    (work / "falsification.json").write_text(
        json.dumps({"verdict": "proceed", "problem": "damped_osc",
                    "coefficient_fraction": {"rk4": {"m0plus_fast": 0.41}}}), encoding="utf-8")
    out = tmp_path / "docs"
    build(arch, out)
    assert not (out / "falsification.html").exists()
    page = (out / "validation.html").read_text(encoding="utf-8")
    assert BANNER in page
    section = page.split('<h2 id="falsification">', 1)[1]
    assert "proceed" in section
    assert "crossover" in section.lower()           # preflight K4 looks for this word
    assert "Raw data" not in page                   # no generic JSON dump any more
    assert _nav_hrefs(page) == SEVEN_TABS


def _validation_fixture() -> dict:
    """A minimal but structurally faithful validation results.json."""
    disc = "11e898cb" + "0" * 56
    return {
        "budget_cycles": 65536,
        "cost_model": "m0plus_fast",
        "rounding": "floor (ASRS), per HANDOFF 4.2",
        "generated_from": {"archive_records": 45973, "champion_hash": disc,
                           "verifier_hash": VH},
        "methods": [
            {"kind": "classical", "name_or_hash": "euler", "order": 1, "stages": 1,
             "roles": ["anchor"], "cycles_per_step": {"buck_converter": 10},
             "steps": {"buck_converter": 6553}},
            {"kind": "classical", "name_or_hash": "rk38", "order": 4, "stages": 4,
             "roles": ["anchor"], "cycles_per_step": {"buck_converter": 36},
             "steps": {"buck_converter": 1820}},
            {"kind": "discovered", "name_or_hash": disc, "order": 2, "stages": 3,
             "roles": ["champion"], "cycles_per_step": {"buck_converter": 44},
             "steps": {"buck_converter": 1489},
             "archive": {"cycle_id": 33, "tier": "no_improvement",
                         "heldout_error": 0.0286, "search_error": 0.0114,
                         "verifier_hash": VH}},
        ],
        "problems": [
            {"name": "buck_converter", "domain": "power electronics",
             "family": "oscillatory", "n_states": 2, "t_end": 25.0,
             "equation": "x1' = D - x2; x2' = x1 - x2/Qf",
             "reference": "closed form via matrix exponential",
             "source": "Averaged CCM buck converter model, Erickson and Maksimovic, 2001.",
             "scale": 0.25, "deriv_scale": 1.0, "peak": 0.6018,
             "per_state_peaks": [0.4685, 0.6018], "y0": [0.0, 0.0]},
            {"name": "pll_lock", "domain": "communications / clocking",
             "family": "nonlinear", "n_states": 2, "t_end": 40.0,
             "equation": "type-2 PLL with PI filter",
             "reference": "mpmath odefun at dps 30",
             "source": "Second-order type-2 analog PLL.",
             "scale": 0.25, "deriv_scale": 1.0, "peak": 1.0,
             "per_state_peaks": [1.0, 0.5], "y0": [0.0, 0.0]},
        ],
        "results": [
            {"problem": "buck_converter", "method": "euler", "steps": 6553,
             "cycles_per_step": 10, "q15_error": 0.0254, "float_error": 7.5e-05,
             "max_abs_q": 4743},
            {"problem": "buck_converter", "method": "rk38", "steps": 1820,
             "cycles_per_step": 36, "q15_error": 0.0208, "float_error": 2.1e-09,
             "max_abs_q": 4700},
            {"problem": "buck_converter", "method": disc, "steps": 1489,
             "cycles_per_step": 44, "q15_error": 0.00198, "float_error": 9.7e-07,
             "max_abs_q": 4699},
            {"problem": "pll_lock", "method": "euler", "steps": 6553,
             "cycles_per_step": 10, "q15_error": 0.0482, "float_error": 1.2e-05,
             "max_abs_q": 20130},
            {"problem": "pll_lock", "method": "rk38", "steps": 1820,
             "cycles_per_step": 36, "q15_error": 0.0128, "float_error": 3.3e-10,
             "max_abs_q": 20101},
            {"problem": "pll_lock", "method": disc, "steps": 1489,
             "cycles_per_step": 44, "q15_error": 0.0159, "float_error": 8.8e-07,
             "max_abs_q": 20098},
        ],
        "verdicts": {
            "problems_compared": 2,
            "problems_won_by_discovered": 1,
            "median_ratio_discovered_over_classical": 0.6678,
            "overall": "On this suite the best discovered method has lower Q15 error than "
                       "the best classical anchor on 1 of 2 problems.",
            "per_problem": {
                "buck_converter": {
                    "winner": disc, "winner_kind": "discovered",
                    "winner_q15_error": 0.00198, "best_classical": "rk38",
                    "best_classical_q15_error": 0.0208, "best_discovered": disc,
                    "best_discovered_q15_error": 0.00198,
                    "ratio_discovered_over_classical": 0.0952},
                "pll_lock": {
                    "winner": "rk38", "winner_kind": "classical",
                    "winner_q15_error": 0.0128, "best_classical": "rk38",
                    "best_classical_q15_error": 0.0128, "best_discovered": disc,
                    "best_discovered_q15_error": 0.0159,
                    "ratio_discovered_over_classical": 1.2404},
            },
        },
    }


def _write_validation(work: Path, data: dict) -> None:
    vdir = work / "validation"
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "results.json").write_text(json.dumps(data, sort_keys=True), encoding="utf-8")


def test_B64_validation_page_built_from_results_json(monkeypatch, tmp_path):
    work, arch = _site_archive(monkeypatch, tmp_path)
    _write_validation(work, _validation_fixture())
    out = tmp_path / "docs"
    build(arch, out)
    page = out / "validation.html"
    assert page.is_file()
    html = page.read_text(encoding="utf-8")
    assert html.lower().lstrip().startswith("<!doctype html>")
    assert BANNER in html and "<script" not in html.lower()
    check_banned(html)
    # the honest verdict line, verbatim from the results file
    assert "lower Q15 error than" in html and "1 of 2 problems" in html
    # chart with per-method dots, plus the always-visible caption leading the figure
    assert 'aria-label="Q15 final-state error per method' in html
    assert "<figure><figcaption>" in html
    # per-problem verdicts, the float-vs-Q15 comparison, and provenance as plain text
    assert "buck_converter" in html and "pll_lock" in html
    assert "float64 error" in html and "0.0254" in html and "7.5e-05" in html
    assert "power electronics" in html                       # problem domain
    assert "Erickson and Maksimovic" in html                 # problem source
    assert "11e898cb" in html                                # discovered method label
    assert "no_improvement" in html                          # archive provenance of champion
    # classical hold-out stays visible: pll_lock's winner row names rk38
    assert "rk38" in html


def test_B64_validation_nav_entry_present_only_with_results(monkeypatch, tmp_path):
    work, arch = _site_archive(monkeypatch, tmp_path)
    out1 = tmp_path / "docs1"
    build(arch, out1)
    assert not (out1 / "validation.html").exists()
    index = (out1 / "index.html").read_text(encoding="utf-8")
    assert 'href="validation.html"' not in index
    _write_validation(work, _validation_fixture())
    out2 = tmp_path / "docs2"
    build(arch, out2)
    assert (out2 / "validation.html").is_file()
    for name in ("index.html", "hypotheses.html", "methodology.html", "validation.html"):
        html = (out2 / name).read_text(encoding="utf-8")
        assert _nav_hrefs(html) == SEVEN_TABS, name
    # the active tab lands on the validation page itself
    val = (out2 / "validation.html").read_text(encoding="utf-8")
    assert '<a href="validation.html" class="on">validation</a>' in val


def test_B64_validation_build_is_deterministic_and_flag_resets(monkeypatch, tmp_path):
    work, arch = _site_archive(monkeypatch, tmp_path)
    _write_validation(work, _validation_fixture())
    d1, d2 = tmp_path / "v1", tmp_path / "v2"
    build(arch, d1)
    build(arch, d2)
    assert _snapshot(d1) == _snapshot(d2)
    # the nav flag never leaks out of build(): a direct render afterwards has no tab
    html = render_index(_empty_arch())
    assert 'href="validation.html"' not in html


def test_B64_render_validation_direct_is_banned_word_safe():
    html = render_validation(_validation_fixture())
    assert "<title>" in html.lower()
    check_banned(html)
    assert render_validation(_validation_fixture()) == render_validation(_validation_fixture())


# ======================================================================================
# Epoch-status panel (B65) and stiff validation grouping (B66)
# ======================================================================================

def _write_epoch_state(work: Path, *, consecutive=4, last_check="2026-09-21T09:30:00Z",
                       last_verdict="SATURATING") -> None:
    (work / "saturation_state.json").write_text(json.dumps({
        "consecutive": consecutive, "last_check": last_check,
        "last_verdict": last_verdict}), encoding="utf-8")


def _write_progress_events(work: Path) -> None:
    events = [
        {"ts": "2026-09-19T08:00:00Z", "kind": "accepted", "order": 2, "stages": 2,
         "bucket": 0, "tier": "no_improvement", "new_elite": False, "tableau_hash": "aa"},
        {"ts": "2026-09-20T10:00:00Z", "kind": "accepted", "order": 2, "stages": 2,
         "bucket": 0, "tier": "no_improvement", "new_elite": True, "tableau_hash": "bb"},
        {"ts": "2026-09-21T09:00:00Z", "kind": "cycle_done", "cycle_id": 9},
    ]
    (work / "events.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")


def test_B65_index_epoch_panel_shows_active_state_counter_and_progress(monkeypatch, tmp_path):
    work, arch = _site_archive(monkeypatch, tmp_path)
    monkeypatch.setenv("RK_SAT_CONSECUTIVE", "6")
    _write_progress_events(work)
    _write_epoch_state(work)
    (work / "falsification.json").write_text(json.dumps({"verdict": "mixed"}), encoding="utf-8")
    out = tmp_path / "docs"
    build(arch, out)
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "<strong>Epoch 1</strong>" in html
    assert 'badge badge-active">active</span>' in html
    # the newest progress event is the new_elite acceptance, displayed in Central time
    assert "2026-09-20 05:00 CT: a cell elite improved its held-out error" in html
    assert "(elite_improvement)" in html
    assert "4 consecutive saturating checks; 6 trigger a freeze" in html
    assert "2026-09-21 04:30 CT, verdict SATURATING" in html
    assert "<dt>falsification file</dt><dd>present</dd>" in html
    # absolute stamps only: no wall-clock delta phrasing anywhere in the panel
    assert "hours ago" not in html and "hours since" not in html
    check_banned(html)


def test_B65_epoch_panel_is_deterministic_given_the_same_state_files(monkeypatch, tmp_path):
    work, arch = _site_archive(monkeypatch, tmp_path)
    _write_progress_events(work)
    _write_epoch_state(work)
    d1, d2 = tmp_path / "e1", tmp_path / "e2"
    build(arch, d1)
    build(arch, d2)
    assert _snapshot(d1) == _snapshot(d2)


def test_B65_epoch_panel_frozen_state_from_epoch_status_json(monkeypatch, tmp_path):
    work, arch = _site_archive(monkeypatch, tmp_path)
    _write_progress_events(work)
    (work / "EPOCH_STATUS.json").write_text(json.dumps({
        "epoch": 1, "frozen_at": "2026-09-21T12:00:00Z",
        "reason": "saturation threshold reached"}), encoding="utf-8")
    out = tmp_path / "docs"
    build(arch, out)
    html = (out / "index.html").read_text(encoding="utf-8")
    assert 'badge badge-frozen">frozen</span>' in html
    assert "<dt>frozen at</dt><dd>2026-09-21 07:00 CT</dd>" in html
    assert "saturation threshold reached" in html
    check_banned(html)


def test_B65_epoch_status_data_defaults_on_a_fresh_work_dir(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    d = epoch_status_data()
    assert d["epoch"] == 1 and d["state"] == "active"
    assert d["last_progress_ts"] is None and d["last_progress_kind"] is None
    assert d["consecutive"] == 0 and d["consecutive_needed"] >= 1
    assert d["falsification_present"] is False
    html = render_index(_empty_arch())
    assert "no progress events recorded yet" in html
    assert "0 consecutive saturating checks" in html
    assert "not yet produced" in html
    check_banned(html)


def _stiff_validation_fixture() -> dict:
    """The B64 fixture extended with stiffness labels and one stiff problem where no
    discovered method finishes, mirroring the live results.json schema."""
    data = _validation_fixture()
    disc = data["methods"][2]["name_or_hash"]
    for p in data["problems"]:
        p["stiff"] = False
        p["stiffness_ratio"] = 1.0
        p["stiffness_basis"] = "single complex pole pair"
    data["problems"].append(
        {"name": "toy_stiff", "domain": "chemical kinetics", "family": "stiff",
         "n_states": 2, "t_end": 8.0, "equation": "y1' = -300 y1; y2' = y1 - y2",
         "reference": "mpmath odefun at dps 30", "source": "Local two-rate test system.",
         "scale": 0.25, "deriv_scale": 1.0, "peak": 1.0, "per_state_peaks": [1.0, 0.6],
         "y0": [1.0, 1.0], "stiff": True, "stiffness_ratio": 300.0,
         "stiffness_basis": "fast eigenvalue over slow eigenvalue"})
    data["results"] += [
        {"problem": "toy_stiff", "method": "euler", "steps": 6553, "cycles_per_step": 10,
         "q15_error": 0.19, "float_error": 0.002, "max_abs_q": 30011},
        {"problem": "toy_stiff", "method": "rk38", "steps": 1820, "cycles_per_step": 36,
         "q15_error": None, "float_error": 3.1e-09, "max_abs_q": None,
         "note": "q15 run failed: Q15OverflowError"},
        {"problem": "toy_stiff", "method": disc, "steps": 1489, "cycles_per_step": 44,
         "q15_error": None, "float_error": 8.0e-07, "max_abs_q": None,
         "note": "q15 run failed: Q15OverflowError"},
    ]
    v = data["verdicts"]
    for name in ("buck_converter", "pll_lock"):
        v["per_problem"][name]["stiff"] = False
    v["per_problem"]["toy_stiff"] = {
        "stiff": True, "winner": "euler", "winner_kind": "classical",
        "winner_q15_error": 0.19, "methods_evaluated": 3,
        "finishers_classical": 1, "finishers_discovered": 0}
    v.update({
        "practical_problems_total": 2, "practical_problems_compared": 2,
        "practical_problems_won_by_discovered": 1,
        "practical_median_ratio_discovered_over_classical": 0.6678,
        "stiff_problems_total": 1, "stiff_problems_compared": 0,
        "stiff_problems_won_by_discovered": 0,
        "stiff_problems_with_no_discovered_finisher": 1,
        "stiff_median_ratio_discovered_over_classical": None,
        "overall": "On the 2 non-stiff practical problems the best discovered method has "
                   "lower Q15 error on 1 of 2. On the 1 stiff problem no discovered "
                   "method finishes; every one overflows in Q15.",
    })
    return data


def test_B66_validation_page_groups_stiff_and_practical_problems(monkeypatch, tmp_path):
    work, arch = _site_archive(monkeypatch, tmp_path)
    _write_validation(work, _stiff_validation_fixture())
    out = tmp_path / "docs"
    build(arch, out)
    html = (out / "validation.html").read_text(encoding="utf-8")
    check_banned(html)
    assert "<h2>Q15 error per problem: practical (non-stiff)</h2>" in html
    assert "<h2>Q15 error per problem: stiff subset</h2>" in html
    assert "<h3>Practical (non-stiff)</h3>" in html and "<h3>Stiff</h3>" in html
    # grouped verdict cards from the practical_*/stiff_* keys
    assert "1 of 2" in html                          # practical wins card
    assert "no discovered finisher" in html
    # the stiff table row: finisher counts, the no-finisher marker, and the winner
    # standing in for the missing best_classical fields
    assert "finishers (classical / discovered)" in html
    assert "1 / 0" in html
    assert "none finished" in html
    assert html.index("toy_stiff") < html.index("none finished")
    stiff_row = html[html.index("<h3>Stiff</h3>"):]
    assert "euler" in stiff_row[:stiff_row.index("</table>")]
    assert "0.19" in stiff_row[:stiff_row.index("</table>")]
    # overflow rows surface their note verbatim in the full results table
    assert "q15 run failed: Q15OverflowError" in html
    # stiffness metadata reaches the problem descriptions
    assert "stiffness ratio 300" in html
    assert "fast eigenvalue over slow eigenvalue" in html


def test_B66_validation_without_stiff_flags_keeps_the_flat_layout(monkeypatch, tmp_path):
    html = render_validation(_validation_fixture())
    assert "<h2>Q15 error per problem</h2>" in html
    assert "stiff subset" not in html
    assert "<h3>Stiff</h3>" not in html
    assert "finishers (classical / discovered)" not in html
    check_banned(html)


def test_B66_stiff_grouped_render_is_deterministic_and_banned_word_safe():
    html = render_validation(_stiff_validation_fixture())
    assert html == render_validation(_stiff_validation_fixture())
    check_banned(html)


# ======================================================================================
# Benchmark page (B67): rendered from work_dir()/benchmark/results.json when present
# ======================================================================================

def _benchmark_fixture() -> dict:
    """A minimal but structurally faithful benchmark results.json: two methods,
    two problems, a speedup section, adaptive library rows and the environment."""
    champ = "11e898cb" + "0" * 56
    return {
        "budget_cycles": 65536,
        "cost_model": "m0plus_fast",
        "rounding": "floor (ASRS), per HANDOFF 4.2",
        "caveats": [
            "Adaptive scipy integrators choose their own step counts; their wall clock "
            "is reported for context and is never a same-work comparison with any "
            "fixed-step run.",
        ],
        "correlation": {"pearson_r": 0.9802, "n_points": 4},
        "environment": {"cpu": "TestCPU 9000", "implementation": "CPython",
                        "python": "3.13.5", "scipy": "1.14.1", "numpy": "2.1.3",
                        "os": "Windows 11", "machine": "AMD64",
                        "timing_caveat": "All timings are Python-level wall clock on one "
                                         "desktop machine."},
        "timing_protocol": {"clock": "time.perf_counter", "n_repeats": 15, "warmup": 3},
        "tolerance_rule": "rtol = 2**-15 and atol = 2**-15 / scale for every problem.",
        "methods": [
            {"kind": "classical", "name_or_hash": "rk4", "order": 4, "stages": 4,
             "roles": ["anchor"]},
            {"kind": "discovered", "name_or_hash": champ, "order": 2, "stages": 3,
             "roles": ["champion"]},
        ],
        "problems": [
            {"name": "dahlquist", "family": "linear", "n_states": 1, "t_end": 5.0},
            {"name": "damped_osc", "family": "oscillatory", "n_states": 2, "t_end": 20.0},
        ],
        "adaptive_results": [
            {"problem": "dahlquist", "integrator": "RK45", "error": 6.65e-06,
             "rtol": 3.05e-05, "atol": 1.22e-04, "n_steps_accepted": 11, "nfev": 68,
             "status": "ok", "timing": {"median_s": 0.000619}},
            {"problem": "damped_osc", "integrator": "Radau", "error": 1.4e-05,
             "rtol": 3.05e-05, "atol": 1.22e-04, "n_steps_accepted": 75, "nfev": 527,
             "status": "ok", "timing": {"median_s": 0.02863}},
        ],
        "speedup": {
            "baseline": "rk4",
            "champion": champ,
            "regime": "Fixed-step Q15 at the shared cycle budget; only the tableau "
                      "coefficients differ.",
            "caveat": "Both sides run the identical solve_q15 path; the per-step ratio "
                      "compares like against like.",
            "geomean_measured_speedup_rk4_over_champion": 1.45,
            "geomean_predicted_speedup_rk4_over_champion": 1.5,
            "champion_error_lower_count": 1,
            "error_comparisons": 2,
            "median_error_ratio_champion_over_rk4": 0.353,
            "n_problems_compared": 2,
            "per_method_us_per_step": {
                "rk4": {"median_us_per_step": 51.671, "min_us_per_step": 35.701,
                        "max_us_per_step": 80.993, "n_problems": 2,
                        "per_problem_us_per_step": {"dahlquist": 35.701,
                                                    "damped_osc": 56.399}},
                champ: {"median_us_per_step": 36.293, "min_us_per_step": 22.729,
                        "max_us_per_step": 54.632, "n_problems": 2,
                        "per_problem_us_per_step": {"dahlquist": 22.729,
                                                    "damped_osc": 33.994}},
            },
            "rows": [
                {"problem": "dahlquist", "status": "ok",
                 "champion_cycles_per_step": 22, "rk4_cycles_per_step": 33,
                 "predicted_ratio_rk4_over_champion": 1.5,
                 "measured_ratio_rk4_over_champion": 1.5707,
                 "champion_us_per_step": 22.729, "rk4_us_per_step": 35.701,
                 "champion_error": 0.000198741, "rk4_error": 0.00016747,
                 "champion_error_lower": False,
                 "champion_budget_seconds": 0.0677, "rk4_budget_seconds": 0.0709},
                {"problem": "damped_osc", "status": "ok",
                 "champion_cycles_per_step": 44, "rk4_cycles_per_step": 66,
                 "predicted_ratio_rk4_over_champion": 1.5,
                 "measured_ratio_rk4_over_champion": 1.6591,
                 "champion_us_per_step": 33.994, "rk4_us_per_step": 56.399,
                 "champion_error": 0.00393948, "rk4_error": 0.0159248,
                 "champion_error_lower": True,
                 "champion_budget_seconds": 0.0506, "rk4_budget_seconds": 0.0559},
            ],
        },
        "verdicts": {
            "cycle_model": "The correlation speaks to ordering, not to absolute scale.",
            "matched_tolerance": "Library wall clocks are informative but never "
                                 "same-work comparisons.",
            "overall": "The float64 side holds the accuracy margin in both tables.",
        },
        "generated_from": {"champion_hashes": [champ], "scipy": "1.14.1",
                           "validation_results": "rk-work/validation/results.json "
                                                 "(methods[].tableau)"},
    }


def _write_benchmark(work: Path, data: dict) -> None:
    bdir = work / "benchmark"
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / "results.json").write_text(json.dumps(data, sort_keys=True), encoding="utf-8")


def test_B67_speed_section_built_from_results_json(monkeypatch, tmp_path):
    """benchmark.html is retired; its timings are validation.html#speed, and a benchmark
    file alone is enough to put the validation tab on the site."""
    work, arch = _site_archive(monkeypatch, tmp_path)
    _write_benchmark(work, _benchmark_fixture())
    out = tmp_path / "docs"
    build(arch, out)
    assert not (out / "benchmark.html").exists()
    page = out / "validation.html"
    assert page.is_file()
    full = page.read_text(encoding="utf-8")
    assert full.lower().lstrip().startswith("<!doctype html>")
    assert BANNER in full and "<script" not in full.lower()
    check_banned(full)
    assert '<h2 id="speed">Measured time per step</h2>' in full
    html = full.split('<h2 id="speed">', 1)[1].split('<h2 id="falsification">', 1)[0]
    # the library table left with the benchmark page: matched accuracy is on the class tabs
    assert "Library accuracy at matched tolerance" not in full
    # the measured us/step chart, one row per method, with its visible caption
    assert 'aria-label="Measured microseconds per Q15 step, per method, 2 methods"' in html
    assert "<figure><figcaption>" in html
    # the speedup table: predicted and measured ratio columns plus the accuracy columns
    assert "predicted ratio" in html and "measured ratio" in html
    assert "1.500" in html and "1.571" in html and "1.659" in html
    assert "champion Q15 error" in html and "rk4 Q15 error" in html
    assert "0.000198741" in html and "0.0159248" in html
    assert "22.73" in html and "56.40" in html
    # the environment line sits in the timing fold with the rest of the fine print
    fine = html.split("<summary>How the timings were taken</summary>", 1)[1]
    assert "Environment: CPython 3.13.5" in fine.split("</details>", 1)[0]
    assert "TestCPU 9000" in html and "scipy 1.14.1" in html
    # every SVG on the page passes the chart-fit audit
    _assert_chart_fit(full)


def test_B67_benchmark_data_raises_the_validation_tab_and_no_benchmark_tab(
        monkeypatch, tmp_path):
    work, arch = _site_archive(monkeypatch, tmp_path)
    out1 = tmp_path / "docs1"
    build(arch, out1)
    assert not (out1 / "benchmark.html").exists()
    assert not (out1 / "validation.html").exists()
    idx1 = (out1 / "index.html").read_text(encoding="utf-8")
    assert 'href="benchmark.html"' not in idx1 and 'href="validation.html"' not in idx1
    # with both optional results files present there is still exactly one evidence tab
    _write_validation(work, _validation_fixture())
    _write_benchmark(work, _benchmark_fixture())
    out2 = tmp_path / "docs2"
    build(arch, out2)
    assert not (out2 / "benchmark.html").exists()
    for name in ("index.html", "validation.html", "methodology.html"):
        html = (out2 / name).read_text(encoding="utf-8")
        assert _nav_hrefs(html) == SEVEN_TABS, name
    val = (out2 / "validation.html").read_text(encoding="utf-8")
    assert '<a href="validation.html" class="on">validation</a>' in val
    # the validation suite leads, then the speed section, then the premise test
    assert (val.index("<h2>The champion against each anchor</h2>") < val.index('<h2 id="speed">')
            < val.index('<h2 id="falsification">') < val.index("<h2>Full tables</h2>"))


def test_B67_benchmark_build_is_deterministic_and_flag_resets(monkeypatch, tmp_path):
    work, arch = _site_archive(monkeypatch, tmp_path)
    _write_benchmark(work, _benchmark_fixture())
    d1, d2 = tmp_path / "b1", tmp_path / "b2"
    build(arch, d1)
    build(arch, d2)
    assert _snapshot(d1) == _snapshot(d2)
    # the nav flag never leaks out of build(): a direct render afterwards has no tab
    html = render_index(_empty_arch())
    assert 'href="validation.html"' not in html and 'href="benchmark.html"' not in html


def _write_sidetrack(work: Path, points: list[dict]) -> None:
    """A side-track ledger and its artifacts, in the layout rk_harness.sidetrack writes."""
    base = work / "sidetrack"
    for p in points:
        rel = f"sidetrack/{p['job']}/{p['key']}.json"
        art = work / rel
        art.parent.mkdir(parents=True, exist_ok=True)
        art.write_text(json.dumps(
            {"job": p["job"], "key": p["key"], "closes": p.get("closes", "a question"),
             "summary": p["summary"]}, sort_keys=True), encoding="utf-8")
        with open(base / "ledger.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": "2026-09-04T00:00:00Z", "cycle": 20,
                                 "track": p["track"], "job": p["job"], "key": p["key"],
                                 "code_hash": "0123456789abcdef", "status": "ok",
                                 "duration_s": 0.5, "artifact": rel,
                                 "summary": p["summary"]}, sort_keys=True) + "\n")


def _sidetrack_fixture() -> list[dict]:
    return [
        {"track": "adaptive", "job": "adaptive.suite_sweep", "key": "buck_converter",
         "summary": {"points": 6, "finished": 6, "fevals_max": 3571}},
        {"track": "implicit", "job": "sdirk.stiff_suite", "key": "servo_load_step",
         "summary": {"finishers": ["sdirk2"], "explicit_finishers": [],
                     "sdirk2_min_stable_n": 8}},
    ]


def test_B68_a_ledger_adds_no_tab_and_lands_in_methodology(monkeypatch, tmp_path):
    """sidetrack.html is retired: the ledger's rules and failures are
    methodology.html#ledger, and a ledger file no longer adds a tab."""
    work, arch = _site_archive(monkeypatch, tmp_path)
    out1 = tmp_path / "docs1"
    build(arch, out1)
    meth1 = (out1 / "methodology.html").read_text(encoding="utf-8")
    assert "rk-work/sidetrack/ledger.jsonl, has not been written" in meth1
    _write_sidetrack(work, _sidetrack_fixture())
    out2 = tmp_path / "docs2"
    build(arch, out2)
    assert not (out2 / "sidetrack.html").exists()
    for name in ("index.html", "methodology.html", "implicit.html"):
        html = (out2 / name).read_text(encoding="utf-8")
        assert _nav_hrefs(html) == SIX_TABS, name
        assert 'href="sidetrack.html"' not in html, name
    page = (out2 / "methodology.html").read_text(encoding="utf-8")
    assert '<a href="methodology.html" class="on">methodology</a>' in page
    assert '<h2 id="ledger">Measurement ledger</h2>' in page


def test_B68_sidetrack_build_is_deterministic_and_flag_resets(monkeypatch, tmp_path):
    work, arch = _site_archive(monkeypatch, tmp_path)
    _write_sidetrack(work, _sidetrack_fixture())
    d1, d2 = tmp_path / "s1", tmp_path / "s2"
    build(arch, d1)
    build(arch, d2)
    assert _snapshot(d1) == _snapshot(d2)
    html = render_index(_empty_arch())
    assert 'href="sidetrack.html"' not in html


def test_B68_the_ledger_section_counts_points_and_stays_static(monkeypatch, tmp_path):
    work, arch = _site_archive(monkeypatch, tmp_path)
    _write_sidetrack(work, _sidetrack_fixture())
    out = tmp_path / "docs"
    build(arch, out)
    page = (out / "methodology.html").read_text(encoding="utf-8")
    section = page.split('<h2 id="ledger">', 1)[1].split("<h2 ", 1)[0]
    # the counts come from the ledger, not from a hardcoded number
    assert "The ledger holds 2 points across 2 jobs" in section
    assert "0123456789ab" in section               # the newest code hash, 12 characters
    # no flattened whole-ledger dump: the points themselves are on the class pages
    for p in _sidetrack_fixture():
        assert p["key"] not in section, p["key"]
    assert "<script" not in page
    # each job's own reading is on its class page, filtered by track
    imp = (out / "implicit.html").read_text(encoding="utf-8")
    adp = (out / "adaptive.html").read_text(encoding="utf-8")
    assert "sdirk.stiff_suite" in imp and "sdirk.stiff_suite" not in adp
    assert "adaptive.suite_sweep" in adp and "adaptive.suite_sweep" not in imp
    for p in _sidetrack_fixture():
        cls_page = imp if p["track"] == "implicit" else adp
        assert p["key"] in cls_page, p["key"]


def test_B69_a_failed_point_message_cannot_block_the_site_build(monkeypatch, tmp_path):
    """A failed point records its exception string, the ledger is append-only, and
    check_banned refuses the whole site on one hit. One unlucky message would take the
    site down and keep it down, so the message is softened on the way to the page."""
    work, arch = _site_archive(monkeypatch, tmp_path)
    _write_sidetrack(work, _sidetrack_fixture())
    with open(work / "sidetrack" / "ledger.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": "2026-09-04T00:01:00Z", "cycle": 20,
                             "track": "implicit", "job": "sdirk.newton_iters", "key": "n1",
                             "code_hash": "0123456789abcdef", "status": "failed",
                             "duration_s": 0.1,
                             "error": "RuntimeError('this proves the first novel case')"},
                            sort_keys=True) + chr(10))
    out = tmp_path / "docs"
    build(arch, out)                       # build() runs check_banned before writing
    page = (out / "methodology.html").read_text(encoding="utf-8")
    check_banned(page)
    # softened, not dropped: the reader still gets the message
    section = page.split('<h2 id="ledger">', 1)[1].split("<h2 ", 1)[0]
    assert "Points that did not complete" in section
    assert "RuntimeError" in section
    assert "shows the earliest new case" in section


def test_B69_the_shipped_catalogue_prose_passes_the_guard(monkeypatch, tmp_path):
    """The job catalogue and the artifact strings are written in this repository, so
    nothing softens them, and they reach a published page. Guard them here, and put one
    real artifact's own schema and arithmetic strings under the same guard."""
    from rk_harness import sidetrack as sidetrack_mod

    for job in sidetrack_mod.JOBS:
        check_banned(job.closes)
        assert chr(0x2014) not in job.closes and chr(0x2013) not in job.closes, job.name

    work, arch = _site_archive(monkeypatch, tmp_path)
    job = sidetrack_mod.JOBS_BY_NAME["sdirk.gamma_dyadic_scan"]
    point = next(p for p in job.points() if p.key == "s04")
    sidetrack_mod.run_point(point, ts="fixed")     # cheapest real point: exact algebra
    out = tmp_path / "docs"
    build(arch, out)
    check_banned((out / "methodology.html").read_text(encoding="utf-8"))
    page = (out / "implicit.html").read_text(encoding="utf-8")
    check_banned(page)
    assert "sdirk.gamma_dyadic_scan" in page and "s04" in page


def test_B67_render_validation_with_only_benchmark_is_banned_word_safe():
    html = render_validation(None, benchmark=_benchmark_fixture())
    assert "<title>" in html.lower()
    check_banned(html)
    assert (render_validation(None, benchmark=_benchmark_fixture())
            == render_validation(None, benchmark=_benchmark_fixture()))


def test_B67_index_and_validation_carry_the_measured_speed_sentence(monkeypatch, tmp_path):
    """Verdict spots tie theory to measured time, and say what the timing supports.

    The sentence used to open "Measured wall clock agrees with the cycle model" on three
    pages, while the section it linked to said the correlation speaks to ordering and
    not to absolute scale: the model predicts one flat ratio and the measured ratios
    spread either side of it. What the benchmark supports is the ordering claim and the
    correlation it was computed from, so that is what the pages state.
    """
    work, arch = _site_archive(monkeypatch, tmp_path)
    _write_validation(work, _validation_fixture())
    _write_benchmark(work, _benchmark_fixture())
    out = tmp_path / "docs"
    build(arch, out)
    for name in ("index.html", "explicit.html", "validation.html"):
        html = (out / name).read_text(encoding="utf-8")
        assert "Measured wall clock agrees" not in html, name
        assert "preserves the cycle model's ordering" in html, name
        assert "modeled rather than measured on a Cortex-M0+" in html, name
    # the head-to-head and the correlation ride with the sentence on the pages that do
    # not hold the speed table. validation.html holds it a screen below, states the
    # correlation once in the benchmark's own verdict, and repeats neither
    for name in ("index.html", "explicit.html"):
        html = (out / name).read_text(encoding="utf-8")
        assert "(Pearson r 0.980 over 4 fixed-step Q15 runs)" in html, name
        assert "runs in 36.29 us per Q15 step against 51.67 us for rk4" in html, name
        assert "The per-step speedup of 1.450x is the geometric mean" in html, name
        assert 'href="validation.html#speed"' in html, name
        assert 'href="benchmark.html"' not in html, name
    val = (out / "validation.html").read_text(encoding="utf-8")
    assert "runs in 36.29 us per Q15 step" not in val and "(Pearson r" not in val
    assert ("preserves the cycle model's ordering, and the cycle counts are modeled"
            in val)
    assert "so their quotient is a different number again" in val
    # without benchmark results, no page invents a wall-clock figure
    out2 = tmp_path / "docs2"
    (work / "benchmark" / "results.json").unlink()
    build(arch, out2)
    for name in ("index.html", "validation.html"):
        assert "us per Q15 step" not in (out2 / name).read_text(encoding="utf-8"), name


def test_the_build_refuses_the_agreement_claim_and_an_unqualified_chip():
    """Two claim gates, run beside the banned-word check and failing the same way.

    Both regressions were live: the wall-clock sentence on three pages, and a hub whose
    only mention of the chip was the footer, with no word near it saying the cycle counts
    behind every number are modeled.
    """
    ok = ('<html><head><title>rk-harness findings</title>'
          '<meta name="description" content="a page about the search"></head>'
          "<body><p>Cost is counted against a modeled Cortex-M0+ cycle budget.</p>"
          "</body></html>")
    sg.check_claims("index.html", ok)
    sg.check_head("index.html", ok)
    with pytest.raises(sg.ClaimError):
        sg.check_claims("index.html", ok.replace("against a modeled Cortex",
                                                 "against a Cortex"))
    with pytest.raises(sg.ClaimError):
        sg.check_claims("validation.html",
                        "<p>Measured wall clock agrees with the cycle model.</p>")
    # the qualifier has to sit near the mention, not anywhere on the page
    far = ('<html><head><title>rk-harness findings</title>'
           '<meta name="description" content="a page about the search"></head><body><p>'
           "Cortex-M0+ " + "word " * 20 + "modeled.</p></body></html>")
    with pytest.raises(sg.ClaimError):
        sg.check_claims("index.html", far)
    # a page with no title of its own, or no description, is also not written
    with pytest.raises(sg.ClaimError):
        sg.check_head("validation.html", "<html><head><title>Validation</title></head>")
    with pytest.raises(sg.ClaimError):
        sg.check_head("validation.html",
                      "<html><head><title>Validation | rk-harness findings</title></head>")
    # ClaimError is a BannedWordError, so runner.py's existing handler catches it and
    # leaves the previous site standing rather than ending the cycle
    assert issubclass(sg.ClaimError, BannedWordError)


def test_every_page_ships_a_document_title_and_a_description(monkeypatch, tmp_path):
    """A shared link or a search result gets the page's own words, not a bare label."""
    work, _arch = _site_archive(monkeypatch, tmp_path)
    _full_work(work)
    out = tmp_path / "docs"
    build(replay(), out)
    seen = 0
    for page in sorted(out.glob("*.html")):
        html = page.read_text(encoding="utf-8")
        head = html.split("</head>", 1)[0]
        title = re.search(r"<title>([^<]+)</title>", head).group(1)
        desc = re.search(r'<meta name="description" content="([^"]+)"', head).group(1)
        assert "rk-harness findings" in title, page.name
        assert len(desc.split()) >= 8, (page.name, desc)
        for tag in ("og:title", "og:description", "og:url", "og:type"):
            assert f'property="{tag}"' in head, (page.name, tag)
        assert '<link rel="canonical"' in head, page.name
        assert "<script" not in html.lower(), page.name
        seen += 1
    assert seen >= 10
    idx = (out / "index.html").read_text(encoding="utf-8")
    # og:image is not claimed: this repository ships no image to point it at
    assert "og:image" not in idx
    assert 'content="https://jgoetzmann.github.io/rk-findings/"' in idx
    cell = (out / "cell-p4-s4-b2.html").read_text(encoding="utf-8")
    assert ('<link rel="canonical" href="https://jgoetzmann.github.io/rk-findings/'
            'cell-p4-s4-b2.html">') in cell


def test_the_hub_stamps_the_archive_state_it_was_built_from(monkeypatch, tmp_path):
    """A build stamp above the fold, from stored values only.

    The hub called itself the run's live record and carried no stamp at all, so a reader
    could not tell a paused run from a broken build. Whether the state is old stays the
    reader's subtraction against their own clock: an age computed here would need a
    wall-clock read, and two builds of one archive would stop matching.
    """
    _work, arch = _site_archive(monkeypatch, tmp_path)
    html = render_index(arch)
    assert "the run's live record" not in html
    assert "This site is rebuilt from the run's data at the end of every cycle" in html
    stamp = re.search(r'<p class="when">([^<]+)</p>', html).group(1)
    assert "Built from cycle 3 of the run, 3 archive records" in stamp
    assert sg._ct(CLOCK) in stamp
    assert render_index(arch) == html
    # an empty archive has no state to stamp, and says nothing rather than printing zeroes
    assert '<p class="when">' not in render_index(_empty_arch())


def test_each_panel_names_the_archive_state_its_document_was_built_at(monkeypatch, tmp_path):
    """benchmark/results.json carries no record count of its own, so the hub names the
    document it took its tableaus from and that document's state, which is not the live
    archive's. The two have differed by tens of thousands of records."""
    work, arch = _site_archive(monkeypatch, tmp_path)
    _write_validation(work, _validation_fixture())
    _write_benchmark(work, _benchmark_fixture())
    out = tmp_path / "docs"
    build(arch, out)
    idx = (out / "index.html").read_text(encoding="utf-8")
    assert "That document was built at 45,973 archive records" in idx
    val = (out / "validation.html").read_text(encoding="utf-8")
    assert ("The suite below reads validation/results.json, built at 45,973 archive "
            f"records, verifier hash {VH[:8]}.") in val
    exp = (out / "explicit.html").read_text(encoding="utf-8")
    assert '<p class="when">Built from cycle 3 of the run, 3 archive records' in exp


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
    assert sg._costmodel_section() == sg._costmodel_section()


def test_B62_different_archives_give_different_indexes(monkeypatch, tmp_path):
    _work, arch = _site_archive(monkeypatch, tmp_path)
    assert render_index(arch) != render_index(_empty_arch())


# ======================================================================================
# D41: seven tabs, the overview link, and a build that removes what it retired
# ======================================================================================

RETIRED = ("benchmark.html", "costmodel.html", "falsification.html", "glossary.html",
           "interpretation.html", "literature.html", "sidetrack.html")


def _full_work(work: Path) -> None:
    """Every optional source at once: the state in which the most pages and links exist."""
    _write_validation(work, _stiff_validation_fixture())
    _write_benchmark(work, _benchmark_fixture())
    _write_sidetrack(work, _sidetrack_fixture())
    (work / "falsification.json").write_text(json.dumps({
        "verdict": "mixed",
        "methods": {"rk4": {"crossover_h": 0.15, "crossover_practical": True,
                            "coefficient_fraction": {"m0plus_fast": 0.41},
                            "sweep": [{"h": 0.5, "q15_error": 0.01, "float_error": 0.02},
                                      {"h": 0.1, "q15_error": 0.004, "float_error": 1e-4},
                                      {"h": 0.01, "q15_error": 0.03, "float_error": 1e-8}]}}}),
        encoding="utf-8")
    append_hypothesis(_hyp())
    from rk_harness import literature
    literature.append_digest({"ts": CLOCK, "cycle": 5, "topic": "fixed point drift",
                              "summary": "s.", "key_points": [],
                              "sources": [{"title": "t", "url": "https://example.org/x"}]})
    literature.append_interpretation({"ts": CLOCK, "cycle": 6, "text": "a reading."})


def test_D41_the_nav_is_one_row_of_seven_tabs(monkeypatch, tmp_path):
    work, _arch = _site_archive(monkeypatch, tmp_path)
    _full_work(work)
    out = tmp_path / "docs"
    build(replay(), out)
    assert sg._CONDITIONAL == {"validation.html"}
    assert [h for h, _l in sg._NAV_ITEMS] == SEVEN_TABS
    assert len(sg._NAV_ITEMS) <= 8
    for page in sorted(out.glob("*.html")):
        html = page.read_text(encoding="utf-8")
        rows = re.findall(r'<nav class="tabs([^"]*)">', html)
        assert rows == [""], (page.name, rows)            # exactly one row, no sub-rows
        hrefs = _nav_hrefs(html)
        assert len(hrefs) <= 8, page.name
        assert hrefs == SEVEN_TABS, page.name
    # a cell page keeps the explicit tab active
    cell = (out / "cell-p4-s4-b2.html").read_text(encoding="utf-8")
    assert '<a href="explicit.html" class="on">explicit</a>' in cell
    hyp = (out / "hypotheses.html").read_text(encoding="utf-8")
    assert '<a href="hypotheses.html" class="on">research log</a>' in hyp


def test_D41_the_header_links_the_overview_site_outside_the_tabs(monkeypatch, tmp_path):
    _work, arch = _site_archive(monkeypatch, tmp_path)
    out = tmp_path / "docs"
    build(arch, out)
    for page in sorted(out.glob("*.html")):
        html = page.read_text(encoding="utf-8")
        header = html.split('<header class="site">', 1)[1].split("</header>", 1)[0]
        assert f'<a class="about" href="{OVERVIEW_URL}">About the project &#8599;</a>' in header, page.name
        nav = re.search(r'<nav class="tabs[^"]*">(.*?)</nav>', header, re.S).group(1)
        assert OVERVIEW_URL not in nav, page.name          # a link, not a tab
        assert f'href="{OVERVIEW_URL}"' in html.split("<footer>", 1)[1], page.name
    # the hub's lead links it too
    index = (out / "index.html").read_text(encoding="utf-8")
    lead = index.split('<p class="lead">', 1)[1].split("</p>", 1)[0]
    assert f'href="{OVERVIEW_URL}"' in lead


def test_D41_build_removes_retired_pages_and_stale_cells_and_nothing_else(
        monkeypatch, tmp_path):
    _work, arch = _site_archive(monkeypatch, tmp_path)
    out = tmp_path / "docs"
    out.mkdir()
    for name in RETIRED:
        (out / name).write_text("old page", encoding="utf-8")
    keep = {"README.md": "readme", "CNAME": "example.org", "notes.html": "mine",
            "cell-p9-s9-b9.txt": "not a page", "cell-p1-s1-b0.html.bak": "backup",
            "cell-extra.html": "not a cell name", ".nojekyll": ""}
    for name, text in keep.items():
        (out / name).write_text(text, encoding="utf-8")
    (out / "sub").mkdir()
    (out / "sub" / "cell-p1-s1-b0.html").write_text("nested", encoding="utf-8")
    (out / "cell-p1-s1-b0.html").write_text("stale cell", encoding="utf-8")   # not in arch
    (out / "cell-p4-s4-b2.html").write_text("stale copy", encoding="utf-8")   # in arch
    build(arch, out)
    names = {p.name for p in out.iterdir()}
    for name in RETIRED:
        assert name not in names, name
    assert "cell-p1-s1-b0.html" not in names                   # a cell the archive lost
    for name, text in keep.items():
        assert (out / name).read_text(encoding="utf-8") == text, name
    assert (out / "sub" / "cell-p1-s1-b0.html").read_text(encoding="utf-8") == "nested"
    # a current cell page is rewritten, not deleted
    assert rk4_cell_is_page((out / "cell-p4-s4-b2.html").read_text(encoding="utf-8"))
    expected = {"index.html", "explicit.html", "implicit.html", "adaptive.html",
                "hypotheses.html", "methodology.html", "cell-p4-s4-b2.html",
                "cell-p3-s3-b1.html", "cell-p2-s2-b0.html"}
    assert {n for n in names if n.endswith(".html")} == expected | {"notes.html",
                                                                   "cell-extra.html"}


def rk4_cell_is_page(html: str) -> bool:
    return html.lower().lstrip().startswith("<!doctype html>") and BANNER in html


def test_D41_a_build_that_fails_the_banned_word_check_deletes_nothing(monkeypatch, tmp_path):
    _work, _ = _site_archive(monkeypatch, tmp_path)
    out = tmp_path / "docs"
    out.mkdir()
    (out / "sidetrack.html").write_text("old page", encoding="utf-8")
    (out / "cell-p1-s1-b0.html").write_text("stale cell", encoding="utf-8")
    append_hypothesis(_hyp(id="H-009", statement="a novel method"))
    with pytest.raises(BannedWordError):
        build(replay(), out)
    assert (out / "sidetrack.html").read_text(encoding="utf-8") == "old page"
    assert (out / "cell-p1-s1-b0.html").read_text(encoding="utf-8") == "stale cell"


def test_D41_no_page_links_a_retired_url(monkeypatch, tmp_path):
    work, _arch = _site_archive(monkeypatch, tmp_path)
    _full_work(work)
    out = tmp_path / "docs"
    build(replay(), out)
    pages = sorted(out.glob("*.html"))
    assert len(pages) >= 10
    names = {p.name for p in pages}
    for page in pages:
        html = page.read_text(encoding="utf-8")
        for href in re.findall(r'href="([^"]+)"', html):
            target = href.split("#", 1)[0]
            assert target not in RETIRED, (page.name, href)
            if target and "://" not in target:             # every local link resolves
                assert target in names, (page.name, href)


def test_D41_the_verdict_chart_renders_deterministically_and_degrades_to_a_note():
    counts = {"supported": 47, "refuted": 162, "inconclusive": 20, "open": 160}
    chart = sg._verdict_chart(counts)
    assert chart == sg._verdict_chart(dict(counts))
    assert chart.startswith("<figure><figcaption>")
    assert 'aria-label="Distinct predicates in each verdict group' in chart
    assert "rk-work/hypotheses.jsonl" in chart            # the source note
    for key, n in counts.items():
        assert f">{key}<" in chart and f">{n}<" in chart, key
    # bar length is the count: the widths keep the ratios 47:162:20:160
    widths = {}
    for key in counts:
        m = re.search(r'width="([\d.]+)" height="18" rx="4" fill="var\(--s1\)" '
                      r'class="cellstroke"><title>' + key + ": ", chart)
        assert m, key
        widths[key] = float(m.group(1))
    for key, n in counts.items():
        assert abs(widths[key] / widths["refuted"] - n / counts["refuted"]) < 0.01, key
    _assert_chart_fit(chart)
    check_banned(chart)
    # a group with no predicate is left out, and no data means a note, never a frame
    partial = sg._verdict_chart({"supported": 3, "refuted": 0})
    assert ">refuted<" not in partial and ">supported<" in partial
    for empty in ({}, {"open": 0}, {"open": None}):
        note = sg._verdict_chart(empty)
        assert "<svg" not in note and ">0<" not in note
        assert "No hypotheses are recorded yet" in note
    page = render_hypotheses([])
    assert "No hypotheses are recorded yet" in page and "<svg" not in page


def test_D41_the_hub_puts_the_three_classes_side_by_side_in_words(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    html = render_index(_empty_arch())
    table = html.split("<h2>The three classes side by side</h2>", 1)[1].split("</table>", 1)[0]
    for label in ("method family", "how candidates are found", "the question asked",
                  "problems", "arithmetic", "what checks a result", "cost basis",
                  "source documents"):
        assert f'<th scope="row">{label}</th>' in table, label
    for cls in ("explicit", "implicit", "adaptive"):
        assert f'<th><a href="{cls}.html">{cls}</a></th>' in table, cls
    assert "float64" in table and "order-verified" in table and "not scored" in table
    # the old cross-class link list is gone; the boundary is one sentence
    assert "Evidence that spans the classes" not in html
    assert sg._CLASS_BOUNDARY.count(". ") == 0
    check_banned(html)


# ======================================================================================
# Runner: the archive checkpoint - R6
# ======================================================================================

def test_R6_the_cycle_writes_a_checkpoint_once_a_day_has_closed(monkeypatch, tmp_path):
    """One checkpoint per closed day, written at the start of a cycle while the current
    day file is still empty, and nothing written or logged on the next cycle that day."""
    work = _setup_env(monkeypatch, tmp_path, phase="0", clock="2026-12-04T10:00:00Z")
    for r in _site_records():
        append(r)
    monkeypatch.setenv("RK_CLOCK", "2026-12-05T10:00:00Z")
    c = _classical_8()
    append(_rec(c["heun3"], _sv(23, 50, 0.007, 0.008, 3.0), "no_improvement", 4, None))
    # the freeze date, so the cycle stops at the encourager instead of searching
    monkeypatch.setenv("RK_CLOCK", "2026-12-06T10:00:00Z")
    monkeypatch.setattr("rk_harness.runner.seed_baselines", lambda vh, known=(): 0)
    assert sorted(p.name for p in archive_dir().glob("*.jsonl")) == ["2026-12-04.jsonl", "2026-12-05.jsonl"]

    run_cycle(_runstate(cycle_id=5, phase=0))
    ckpt = work / "ARCHIVE_CHECKPOINT.json"
    assert ckpt.is_file()
    events = _read_jsonl(work / "events.jsonl")
    written = [e for e in events if e.get("kind") == "archive_checkpoint_written"]
    assert len(written) == 1
    assert written[0]["covered_files"] == 2
    assert written[0]["n_records"] == 4
    assert [e for e in events if e.get("kind") == "archive_checkpoint_rejected"] == []
    assert any(e.get("kind") == "frozen" for e in events)
    stamp = ckpt.stat().st_mtime_ns

    run_cycle(_runstate(cycle_id=6, phase=0))
    events = _read_jsonl(work / "events.jsonl")
    assert len([e for e in events if e.get("kind") == "archive_checkpoint_written"]) == 1
    assert [e for e in events if e.get("kind") == "archive_checkpoint_rejected"] == []
    assert ckpt.stat().st_mtime_ns == stamp


# ======================================================================================
# Exploration policy: rotation, warm starts, and the policy reader (P05, B84, B85)
# ======================================================================================

def _policy_env(monkeypatch, policy=None, block=None) -> None:
    for name, value in (("RK_SEARCH_POLICY", policy), ("RK_POLICY_BLOCK_CYCLES", block)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, str(value))


def _p05_arch() -> ArchiveState:
    """An order-4 grid holding rk4 as the elite of one four-stage cell."""
    rec = _rec(_classical_8()["rk4"], _sv(33, 85, 0.001, 0.002, 4.0), "heldout_verified", 1, "D-E000001")
    return ArchiveState(n_records=1, last_cycle_id=1,
                        grids={1: {}, 2: {}, 3: {}, 4: {(4, 2): rec}},
                        open_hypotheses=(), refuted_hypotheses=())


def _stub_island(monkeypatch, yields):
    """Replace the CMA-ES island with a generator that records how it was called."""
    calls: list[dict] = []

    def fake(order, stages, seed, constraints, budget, sigma=None):
        calls.append({"order": order, "stages": stages, "seed": seed, "budget": budget,
                      "constraints": dict(constraints), "sigma": sigma})
        for t in yields:
            yield t

    monkeypatch.setattr("rk_harness.search.cmaes_island", fake)
    return calls


def test_B84_search_policy_rotation_is_a_pure_function_of_cycle_id(monkeypatch):
    _policy_env(monkeypatch)
    for cycle in (0, 1, 7, 99, 100, 12345):
        assert runner_mod._search_policy(cycle) == "empty", cycle
    _policy_env(monkeypatch, "off")
    assert runner_mod._search_policy(3) == "empty"
    _policy_env(monkeypatch, "revisit")
    assert [runner_mod._search_policy(c) for c in (0, 10, 999)] == ["revisit"] * 3
    rot = runner_mod._POLICY_ROTATION
    assert len(rot) >= 2 and set(rot) <= set(runner_mod.POLICIES)
    _policy_env(monkeypatch, "rotate", 10)
    for i in range(len(rot) + 1):
        for cycle in range(i * 10, i * 10 + 10):
            assert runner_mod._search_policy(cycle) == rot[i % len(rot)], (i, cycle)
    # a garbage block length clamps rather than raising; an unknown policy name stays disarmed
    _policy_env(monkeypatch, "rotate", "junk")
    assert runner_mod._policy_block() == 100
    for bad in (0, -5, ""):
        _policy_env(monkeypatch, "rotate", bad)
        assert runner_mod._policy_block() >= 1
        assert runner_mod._search_policy(1) in runner_mod.POLICIES
    _policy_env(monkeypatch, "REVISIT+WARM ")
    assert runner_mod._search_policy(1) == "revisit+warm"
    _policy_env(monkeypatch, "not-a-policy")
    assert runner_mod._search_policy(1) == "empty"
    assert runner_mod._warm_start_enabled("warm")
    assert runner_mod._warm_start_enabled("revisit+warm")
    assert not runner_mod._warm_start_enabled("empty")
    assert not runner_mod._warm_start_enabled("revisit")


def test_B84_island_events_carry_the_policy_and_warm_start_flag(monkeypatch, tmp_path):
    work = _setup_env(monkeypatch, tmp_path, phase="2")
    arch = _p05_arch()
    calls = _stub_island(monkeypatch, [_classical_8()["kutta3"]])
    d = fallback_directive(arch, 2, 4, "revisit+warm")
    assert d["directive_id"] == "D-FR00004" and d["stages"] == [4]
    cands = runner_mod._cmaes_candidates(d, 4, arch, "revisit+warm")
    assert cands and all(c.directive_id == "D-FR00004" for c in cands)
    x0 = search_mod.x0_from_tableau(_classical_8()["rk4"])
    assert calls and [c["constraints"].get("x0") for c in calls] == [x0] * len(calls)
    events = _read_jsonl(work / "events.jsonl")
    starts = [e for e in events if e["kind"] == "island_start"]
    dones = [e for e in events if e["kind"] == "island_done"]
    assert len(starts) == len(dones) == d["islands"]
    for e in starts:
        assert e["policy"] == "revisit+warm"
        assert e["warm_start"] is True
        # the start point is recorded, because a warm start makes the candidate stream
        # depend on the archive as well as on the seed
        assert e["warm_start_hash"] == content_hash(_classical_8()["rk4"])
    assert all(e["policy"] == "revisit+warm" for e in dones)


def test_B84_warm_start_disarmed_never_sets_x0(monkeypatch, tmp_path):
    work = _setup_env(monkeypatch, tmp_path, phase="2")
    arch = _p05_arch()
    calls = _stub_island(monkeypatch, [_classical_8()["kutta3"]])
    d = fallback_directive(arch, 2, 4)
    assert d["directive_id"] == "D-FE00004"
    runner_mod._cmaes_candidates(d, 4, arch, "empty")
    assert calls
    for c in calls:
        assert c["constraints"] == search_mod.default_constraints()
        assert set(c["constraints"]) == {"force_zero", "dyadic_denominator_max", "c_fixed", "b_nonneg"}
        assert c["sigma"] is None       # the step size stays where it was until it is moved
    starts = [e for e in _read_jsonl(work / "events.jsonl") if e["kind"] == "island_start"]
    assert starts
    for e in starts:
        assert e["policy"] == "empty"
        assert e["warm_start"] is False
        assert e["warm_start_hash"] is None


@pytest.mark.slow
def test_B84_cycle_events_carry_the_policy_under_the_shipped_default(monkeypatch, tmp_path):
    """RK_SEARCH_POLICY unset is the shipped configuration: every cycle reads "empty" and
    the fallback keeps aiming at the emptiest cell, exactly as before P05."""
    work = _setup_env(monkeypatch, tmp_path, phase="2")
    _policy_env(monkeypatch)
    monkeypatch.setattr(runner_mod, "seed_baselines", lambda *a, **k: 0)
    _stub_island(monkeypatch, [_classical_8()["kutta3"]])
    out = run_cycle(_runstate(cycle_id=6, phase=2))
    assert out.cycle_id == 7
    events = _read_jsonl(work / "events.jsonl")
    assert "cycle_abandoned" not in {e["kind"] for e in events}
    fb = [e for e in events if e["kind"] == "directive_fallback"]
    assert len(fb) == 1
    assert fb[0]["directive_id"] == "D-FE00007" and fb[0]["policy"] == "empty"
    assert fb[0]["rationale"] == "fallback: empty cell"
    cp = [e for e in events if e["kind"] == "candidates_processed"]
    done = [e for e in events if e["kind"] == "cycle_done"]
    assert cp and cp[-1]["policy"] == "empty"
    assert done and done[-1]["policy"] == "empty"
    assert all(e["warm_start"] is False for e in events if e["kind"] == "island_start")
    assert [r.directive_id for r in read_all()] == ["D-FE00007"]


def _p05_event_rows() -> list[dict]:
    """Three cycles: one written before the policy field existed, then one block each of
    two policies, then an accepted record from a cycle that has not closed yet."""
    return [
        {"kind": "accepted", "directive_id": "D-F00001", "order": 4, "stages": 4, "bucket": 3, "new_elite": True},
        {"kind": "accepted", "directive_id": "D-T1", "order": 4, "stages": 4, "bucket": 3, "new_elite": False},
        {"kind": "candidates_processed", "accepted": 2, "rejected": 1, "skipped": 0, "total": 3},
        {"kind": "cycle_done", "cycle_id": 1, "improved": True},
        {"kind": "accepted", "directive_id": "D-FE00002", "order": 4, "stages": 5, "bucket": 2, "new_elite": True},
        {"kind": "accepted", "directive_id": "D-FE00002", "order": 4, "stages": 5, "bucket": 2, "new_elite": False},
        {"kind": "candidates_processed", "accepted": 2, "rejected": 0, "skipped": 3, "total": 5},
        {"kind": "cycle_done", "cycle_id": 2, "policy": "empty"},
        {"kind": "accepted", "directive_id": "D-FR00003", "order": 4, "stages": 6, "bucket": 1, "new_elite": False},
        {"kind": "accepted", "directive_id": "D-T9", "order": 4, "stages": 6, "bucket": 2, "new_elite": True},
        {"kind": "candidates_processed", "accepted": 2, "rejected": 1, "skipped": 5, "total": 8},
        {"kind": "cycle_done", "cycle_id": 3, "policy": "revisit+warm"},
        {"kind": "accepted", "directive_id": "D-FE00004", "order": 3, "stages": 3, "bucket": 0, "new_elite": True},
    ]


def _write_p05_events(path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for i, row in enumerate(_p05_event_rows()):
            fh.write(json.dumps(row) + "\n")
            if i == 3:
                fh.write("{not json at all\n")   # a torn line must not stop the pass


def test_B85_policyab_attributes_events_to_the_closing_cycle_policy(tmp_path):
    path = tmp_path / "events.jsonl"
    _write_p05_events(path)
    out = policyab.scan_policies(path)
    pol = out["policies"]
    assert list(pol) == ["empty", "revisit+warm", "unlabelled"]

    # Written before the policy field existed: attributed to nothing, not to the default.
    assert pol["unlabelled"] == {
        "cycles": 1, "accepted": 2, "rejected": 1, "skipped": 0, "new_elites": 1,
        "new_cells": 1, "cells": ((4, 4, 3),), "median_accepted": 2.0, "skipped_fraction": 0.0,
    }
    # The block of "empty" cycles, plus the trailing record whose D-FE id names its own
    # policy even though its cycle never closed.
    assert pol["empty"]["cycles"] == 1
    assert pol["empty"]["accepted"] == 3
    assert pol["empty"]["new_elites"] == 2
    assert pol["empty"]["cells"] == ((3, 3, 0), (4, 5, 2))
    assert pol["empty"]["skipped"] == 3 and pol["empty"]["rejected"] == 0
    assert pol["empty"]["skipped_fraction"] == 0.5
    assert pol["empty"]["median_accepted"] == 2.0
    # A D-FR record inside a revisit+warm cycle keeps the cycle's fuller name: the id letter
    # and the cycle policy agree about the cell scan, and the cycle policy says more.
    assert pol["revisit+warm"]["accepted"] == 2
    assert pol["revisit+warm"]["new_elites"] == 1
    assert pol["revisit+warm"]["new_cells"] == 2
    assert pol["revisit+warm"]["skipped"] == 5
    assert abs(pol["revisit+warm"]["skipped_fraction"] - 5 / 8) < 1e-12
    # a cell counts once, for the policy that reached it earliest
    seen = [c for row in pol.values() for c in row["cells"]]
    assert len(seen) == len(set(seen)) == 5
    assert out["events"] == len(_p05_event_rows())


def test_B85_policyab_reads_the_work_dir_and_prints_one_row_per_policy(monkeypatch, tmp_path, capsys):
    work = _setup_env(monkeypatch, tmp_path)
    _write_p05_events(work / "events.jsonl")
    assert policyab.main([]) == 0
    text = capsys.readouterr().out
    for needle in ("empty", "revisit+warm", "unlabelled", "cycles", "new cells", "events.jsonl"):
        assert needle in text, needle
    assert policyab.scan_policies()["policies"] == policyab.scan_policies(work / "events.jsonl")["policies"]
    # an absent log is an empty report, not a crash
    (work / "events.jsonl").unlink()
    assert policyab.scan_policies()["policies"] == {}
    assert policyab.main([]) == 0
    assert "(no events)" in capsys.readouterr().out


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


# ======================================================================================
# A2 fix round (2026-09-11): the hub, validation, the research log, methodology, CSS
# ======================================================================================

def test_A2_no_page_claims_q15_for_every_class(monkeypatch, tmp_path):
    """Implicit and adaptive runs are float64, so neither the footer on every page nor
    the hub's subtitle may call the whole site Q15 or equal-budget."""
    work, _arch = _site_archive(monkeypatch, tmp_path)
    _full_work(work)
    out = tmp_path / "docs"
    build(replay(), out)
    for page in sorted(out.glob("*.html")):
        foot = page.read_text(encoding="utf-8").split("<footer>", 1)[1]
        assert "Q15" not in foot and "equal cycle budget" not in foot, page.name
    index = (out / "index.html").read_text(encoding="utf-8")
    sub = index.split('<p class="sub">', 1)[1].split("</p>", 1)[0]
    assert sub.startswith("Explicit methods scored in Q15") and "outside the archive" in sub


def test_A2_the_hub_leads_with_the_classes_and_closes_with_the_epoch_panel(
        monkeypatch, tmp_path):
    work, arch = _site_archive(monkeypatch, tmp_path)
    _write_progress_events(work)
    _write_epoch_state(work)
    body = render_index(arch).split("</header>", 1)[1]
    marks = [body.index('<p class="lead">'), body.index("<h2>The three classes</h2>"),
             body.index("<h2>The three classes side by side</h2>"),
             body.index("<h2>Epoch status</h2>"), body.index("<strong>Epoch 1</strong>")]
    assert marks == sorted(marks)
    # the notes that described the page are gone; one no-comparison sentence stays
    assert "Each card carries one number" not in body
    assert "The class tabs carry the rest" not in body
    assert body.count("Numbers from different columns do not compare") == 1
    assert body.count("Off-archive by construction") == 0
    check_banned(body)


def test_A2_the_epoch_panel_names_a_progress_kind_once():
    base = {"epoch": 1, "state": "active", "consecutive": 0, "consecutive_needed": 6,
            "last_progress_ts": "2026-09-08T03:30:00Z", "falsification_present": True}
    ver = sg._epoch_panel(dict(base, last_progress_kind="heldout_verified"))
    assert "an acceptance at the heldout_verified tier" in ver
    assert "(heldout_verified)" not in ver                 # the label already names it
    imp = sg._epoch_panel(dict(base, last_progress_kind="elite_improvement"))
    assert "a cell elite improved its held-out error" in imp and "(elite_improvement)" in imp
    assert "Times are stored in UTC and shown in US Central." in ver


def _a2_validation_page() -> tuple[str, dict, dict]:
    data = _stiff_validation_fixture()
    data.setdefault("verdicts", {})["overall"] = (
        "On the non-stiff problems the discovered method leads; on the stiff one it "
        "overflows. The pattern is the stability tax of explicit methods: an expensive "
        "tableau takes larger steps. This is the motivating evidence for the epoch-3 "
        "implicit (SDIRK) track.")
    bench = _benchmark_fixture()
    bench["verdicts"]["cycle_model"] = (
        "Across 4 fixed-step Q15 runs the Pearson correlation between analytic cycles per "
        "step and measured seconds per step is 0.980. The correlation speaks to ordering, "
        "not to absolute scale.")
    bench["generated_from"]["where"] = ("on the host, on demand; it runs at --cpu-shares "
                                        "256 under a watchdog")
    return render_validation(data, benchmark=bench), data, bench


def test_A2_validation_says_each_thing_once_and_reaches_a_chart_early():
    html, data, bench = _a2_validation_page()
    body = html.split("</header>", 1)[1]
    # the lead is two sentences and signposts nothing
    lead = re.sub(r"<[^>]+>", "", body.split('<p class="lead">', 1)[1].split("</p>", 1)[0])
    assert len(re.findall(r"[.?!](?:\s|$)", lead)) == 2, lead
    assert "Further down" not in lead
    # the suite's verdict restates the cards, so it sits in a fold under Full tables,
    # and in the site's own words: validation.py calls the class an epoch-3 track and
    # its overflow story a stability tax, and no other page here says either
    verdict = sg._suite_verdict(data["verdicts"]["overall"])
    assert verdict != data["verdicts"]["overall"]
    assert "Explicit methods pay for stability:" in verdict
    assert "This is why the implicit (SDIRK) class is measured." in verdict
    assert body.count(verdict) == 1
    assert "stability tax" not in body and "epoch-3" not in body
    assert body.index("<h2>Full tables</h2>") < body.index(verdict)
    assert "<summary>The suite&#x27;s verdict, in words</summary>" in body
    # at most a short run of words comes before the first chart
    import html as htmlmod
    before = htmlmod.unescape(re.sub(r"<[^>]+>", " ", body[:body.index("<figure>")]))
    assert len(before.split()) < 200, len(before.split())
    # the stiff chart names what differs instead of repeating the practical caption
    assert body.count("One row per method within each problem") == 1
    assert "<figcaption>Same layout as the practical chart" in body
    assert "A method shows in a stiff row only if" not in body
    # Pearson once: the benchmark's verdict carries the number and its caveat
    assert body.count("Pearson") == 1 and body.count("0.980") == 1
    check_banned(html)


def test_A2_validation_keeps_one_wall_clock_caveat_in_view():
    html, _data, bench = _a2_validation_page()
    speed = html.split('<h2 id="speed">', 1)[1].split('<h2 id="falsification">', 1)[0]
    fold = speed.split("<summary>How the timings were taken</summary>", 1)[1]
    fold = fold.split("</details>", 1)[0]
    rest = speed.replace(fold, "")
    for text in ("Environment: CPython 3.13.5", bench["environment"]["timing_caveat"],
                 bench["speedup"]["caveat"]):
        assert text in fold and text not in rest, text
    assert "on one desktop machine" not in rest
    assert "vary from run to run" not in speed


def test_A2_validation_provenance_leaves_out_the_host_docstring_and_fits_one_line():
    html, _data, bench = _a2_validation_page()
    assert "<dt>where</dt>" not in html and "cpu-shares" not in html
    assert "<dt>champion_hashes</dt>" in html and "<dt>scipy</dt>" in html
    sub = html.split('<p class="sub">', 1)[1].split("</p>", 1)[0]
    assert len(sub) <= 70, sub                      # one line at 1280, so the tabs sit still


def test_A2_the_falsification_pair_reads_the_crossover_once():
    sweep = [{"h": 0.5, "q15_error": 0.01, "float_error": 0.02},
             {"h": 0.1, "q15_error": 0.004, "float_error": 1e-4},
             {"h": 0.01, "q15_error": 0.03, "float_error": 1e-8}]
    data = {"verdict": "mixed", "methods": {
        "heun2": {"crossover_h": 0.1, "coefficient_fraction": {"m0plus_fast": 0.2},
                  "sweep": sweep},
        "rk4": {"crossover_h": None, "coefficient_fraction": {"m0plus_fast": 0.4},
                "sweep": sweep}}}
    sec = "".join(sg._falsification_section(data))
    assert sec.count("turns back up") == 1           # the reading, once, above the pair
    assert sec.index("turns back up") < sec.index("<figure>")
    caps = re.findall(r"<figcaption>(.*?)</figcaption>", sec, re.S)
    assert len(caps) == 2 and caps[0] != caps[1]
    for cap in caps:
        assert len(re.sub(r"<[^>]+>", "", cap).split()) < 30, cap
    heun2, rk4 = (c for c in caps)
    assert "dashed line is the measured crossover" in heun2
    assert "dashed" not in rk4                       # no crossover, no dashed line
    _assert_chart_fit(sec)
    check_banned(sec)


def test_A2_research_log_shows_the_newest_predicates_and_points_at_the_rest():
    n = sg._HYP_SHOWN + 3
    hyps = [_hyp(id=f"H-{i:03d}", predicate=f"fast.p2s3.heldout < fast.p{i}s4.heldout",
                 verdict="refuted", n_samples=250, effect_size=0.9, resolved_cycle=i)
            for i in range(1, n + 1)]
    # H-001's predicate posed again as H-1000: numerically the newest, so its row leads
    # and its verdict speaks for the group, which moves the group between the folds
    hyps[0]["verdict"] = "inconclusive"
    hyps.append(_hyp(id="H-1000", predicate=hyps[0]["predicate"], verdict="refuted",
                     n_samples=260, effect_size=0.8, resolved_cycle=99))
    html = render_hypotheses(hyps)
    ids = re.findall(r'<details class="led"><summary><span class="mono">(H-\d+)', html)
    assert len(ids) == sg._HYP_SHOWN
    assert ids[0] == "H-001" and ids[1] == f"H-{n:03d}" and ids[2] == f"H-{n - 1:03d}"
    for dropped in ("H-002", "H-003", "H-004"):          # the three oldest predicates
        assert dropped not in ids
    # anchored on the tag: "23 older predicates" contains "3 older predicates", so a
    # bare substring passes for the whole group size as well as for the remainder
    assert f">{n - sg._HYP_SHOWN} older predicates are not shown here" in html
    assert "rk-work/hypotheses.jsonl" in html
    assert '<dt>posed as</dt><dd class="mono">H-001, H-1000</dd>' in html
    assert f"refuted ({n} predicates)" in html
    assert "H-001" in html.split("refuted (", 1)[1].split("</details>", 1)[0]
    # the group left the verdict of its oldest posing behind, and that group with it
    assert "inconclusive (" not in html
    # no verdict cards and no reconciling note: the chart caption carries the totals
    assert '<div class="cards">' not in html and "cards above" not in html
    assert "The cards count hypotheses" not in html
    cap = html.split("<figcaption>", 1)[1].split("</figcaption>", 1)[0]
    assert f"{n + 1} hypotheses over {n} distinct predicates; 1 was posed more than once." in cap
    check_banned(html)


def test_A2_the_cost_model_section_does_not_restate_the_glossary():
    sec = sg._costmodel_section()
    assert "cheaper of a shift-add" not in sec
    assert "comes from coefficient arithmetic alone" not in sec
    assert sec.count(AVR_NOTE) == 1 and "HANDOFF" not in sec
    check_banned(sec)


def test_A2_the_stylesheet_carries_the_layout_fixes():
    css = sg._STYLE
    phone = css.split("@media(max-width:640px){", 1)[1]
    assert "figure{overflow-x:auto}" in phone and "figure svg{max-width:none}" in phone
    assert ".charts.grid2>.panel{overflow-x:auto}" in phone
    # visual-1: a figure that scrolls says so, without JavaScript, and the
    # phone drawing of a chart replaces the wide one rather than joining it
    assert "background-attachment:local,local,scroll,scroll" in phone
    assert ".chart-wide{display:none}" in phone
    assert ".chart-phone{display:block}" in phone
    assert ".chart-phone{display:none}" in css.split("@media(max-width:640px){",
                                                    1)[0]
    assert "table.side{table-layout:fixed;width:100%}" in phone
    assert "table.side td{min-width:0;max-width:none;overflow-wrap:break-word}" in phone
    assert ".cards{display:grid;" in css
    # a panel sizes to its chart whether the figure holds one svg, the two the
    # phone variant emits, or a set of small multiples
    assert ".panel:has(>figure>svg)," in css
    assert ".panel:has(>figure>.chart-wide)," in css
    assert (".panel:has(>figure>.multiples)"
            "{width:fit-content;max-width:100%}") in css
    # visual-prose-3: fit-content on a wrapping flex box resolves to one long row and
    # then clamps to the column, so the multiples carry fixed tracks and size themselves
    assert ("grid-template-columns:repeat(var(--cols,3),minmax(0,var(--tw,272px)))"
            in css)
    assert "width:fit-content;max-width:100%}" in css.split(".multiples{", 1)[1]
    assert "display:flex" not in css.split(".multiples{", 1)[1].split("}", 1)[0]
    # visual-4: card values line up across a row even when a label wraps
    assert (".cards>.card:not(.klass){display:grid;grid-template-rows:subgrid;"
            "grid-row:span 3}") in css
    assert "align-items:stretch" in css.split(".charts.grid2{", 1)[1].split("}", 1)[0]
    assert "header.site .sub{max-width:none}" in css
    assert "p.note .hash{word-break:normal;white-space:nowrap}" in css
    about = css.split("a.about{", 1)[1].split("}", 1)[0]
    assert "var(--text-1)" in about and "13.5px" in about
    assert "border:1px solid var(--line)" in about
    assert "details.repeats" not in css                  # the nested repeats table is gone
    check_banned(css)


def test_A2_a_cell_page_names_its_cell_once_in_the_header():
    rec = _site_records()[0]
    html = render_cell(4, 4, 2, rec)
    header, body = html.split("</header>", 1)
    assert '<p class="sub">grid order 4, 4 stages, cycle bucket 2</p>' in header
    assert "grid order 4, 4 stages, cycle bucket 2" not in body
    assert AVR_NOTE in body and "HANDOFF \u00a74.5" not in html
    assert html.count(AVR_NOTE) == 1
    check_banned(html)


def test_A2_card_counts_carry_thousands_separators():
    assert sg._count(141364) == "141,364" and sg._count(7) == "7"
    assert sg._count(None) == "n/a" and sg._count(True) == "true"
    arch = ArchiveState(n_records=141364, last_cycle_id=1, grids={1: {}, 2: {}, 3: {}, 4: {}},
                        open_hypotheses=(), refuted_hypotheses=())
    assert '<div class="v">141,364</div>' in sg._stat_cards(arch)


# ===================================================================================
# round 2 (2026-09-11): one spelling, and page titles in sentence case
# ===================================================================================

def test_the_site_prose_uses_one_spelling():
    """American spelling in the two modules that write the site's own prose.

    The overview was normalized to American spelling, and the two sites are read as
    one. A published page can still carry a British spelling that came out of a lane
    document or a model-written log, so the scan reads the sources of our own prose
    rather than the built pages.
    """
    pat = re.compile(r"\b(colour\w*|behaviour\w*|modelled|labelled|neighbouring)\b",
                     re.IGNORECASE)
    for mod in (sg, methodology_mod):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        hits = sorted({m.group(0) for m in pat.finditer(src)})
        assert not hits, (mod.__name__, hits)


def test_page_titles_are_sentence_case_and_the_tabs_stay_lowercase(monkeypatch, tmp_path):
    """The h1 and the browser tab capitalize; the nav labels do not.

    The hub keeps the repository's own name, which is lowercase wherever it appears.
    """
    work, _arch = _site_archive(monkeypatch, tmp_path)
    _full_work(work)
    out = tmp_path / "docs"
    build(replay(), out)
    seen = 0
    for page in sorted(out.glob("*.html")):
        html = page.read_text(encoding="utf-8")
        h1 = re.search(r"<h1>([^<]+)</h1>", html).group(1)
        # the tab and the heading are deliberately no longer one string: a title of
        # "Validation" or "Cell p4 s4 b2" says nothing in a search result or a shared
        # link, so the document title keeps the heading and adds the site to it
        title = re.search(r"<title>([^<]+)</title>", html).group(1)
        assert title.startswith(h1), (page.name, title, h1)
        assert len(title) > len(h1), (page.name, title)
        assert "rk-harness findings" in title, page.name
        seen += 1
        if page.name == "index.html":
            assert h1 == "rk-harness findings"
            assert title == "rk-harness findings: Runge-Kutta methods in Q15 fixed point"
            continue
        assert h1[:1].isupper(), (page.name, h1)
    assert seen >= 10
    idx = (out / "index.html").read_text(encoding="utf-8")
    for label in ("explicit", "implicit", "adaptive", "validation", "research log",
                  "methodology"):
        assert f">{label}</a>" in idx, label
    cell = (out / "cell-p4-s4-b2.html").read_text(encoding="utf-8")
    assert "<h1>Cell p4 s4 b2</h1>" in cell


# ======================================================================================
# FIND-2 (2026-09-12): the compiled trace, the hover and tally gates, the bisection
# floor, and stage counts that read as English
# ======================================================================================

def _trace_fixture() -> dict:
    """A trace document in the shape rk_harness.tracecheck writes, with both scopes.

    Two methods: one whose matched-scope gap sits inside the band, one outside it, and
    the pair inverted between the analytic order and the traced order, which is the case
    the page has to name rather than hide.
    """
    def method(name, origin, stages, ana, traced, matched, muls):
        return {
            "name": name, "origin": origin, "stages": stages,
            "cycles_analytic": {"m0plus_fast": ana, "m0plus_slow": ana},
            "cycles_traced": {"m0plus_fast": traced, "m0plus_slow": traced * 2},
            "cycles_traced_model_scope": {"m0plus_fast": matched, "m0plus_slow": matched},
            "ratio_model_scope": {"m0plus_fast": matched / ana, "m0plus_slow": 1.0},
            "relative_gap_model_scope": {"m0plus_fast": abs(ana - matched) / matched,
                                         "m0plus_slow": 0.0},
            "instructions_per_step": traced // 2,
            "muls_per_step": muls, "muls_in_model_scope": 0,
            "crosscheck": {"cases": 8, "comparable": 8, "matched": 8,
                           "overflow_cases": 2, "trap_index_matched": 2},
        }
    return {
        "methods": [method("midpoint", "classical", 2, 11, 50, 14, 2),
                    method("rk4", "classical", 4, 33, 139, 67, 4),
                    method("11e898cb", "discovered", 3, 22, 86, 35, 3)],
        "accuracy": {
            "statement": "The emulator is instruction accurate and NOT cycle accurate.",
            "does_not_establish": ["wall clock time on any physical part"],
        },
        "assumptions": {"flash": "zero wait state", "branch_taken_cycles": 3,
                        "branch_not_taken_cycles": 1, "load_cycles": 2, "store_cycles": 2,
                        "muls_cycles_fast_variant": 1, "muls_cycles_small_variant": 32},
        "toolchain": {"compiler": "arm-none-eabi-gcc 13.2.1", "emulator": "unicorn 2.1.4",
                      "flags": ["-mcpu=cortex-m0plus", "-mthumb", "-O2"]},
        "verdicts": {"scope": "The analytic model prices the stage and b combinations "
                              "only."},
        "correlation": {
            "spearman_analytic_vs_traced_model_scope_fast": 0.9429,
            "spearman_analytic_vs_traced_slow": 1.0,
            "inversions_model_scope_fast": [{"pair": ["rk4", "rk38"],
                                             "analytic": {"rk4": 33, "rk38": 36},
                                             "traced": {"rk4": 67, "rk38": 59}}],
            "inversions_slow": [],
        },
        "generated_from": {"trace_hash": "17a00337" + "0" * 56},
    }


def test_the_trace_section_keeps_the_two_scopes_apart_and_publishes_the_failed_band():
    """F1 Tier A rendering. The document carries a whole-step traced count and a count
    matched to what the cost model actually prices, and quoting the first against
    cycles_analytic would report a scope difference as a model error. The comparison
    table takes the matched one, says what it excludes, and names the rows that miss the
    band rather than moving the band."""
    doc = _trace_fixture()
    html = render_validation(None, trace=doc)
    assert '<h2 id="trace">Compiled and traced against the cost model</h2>' in html
    sec = html.split('<h2 id="trace">', 1)[1].split('<h2 id="falsification">', 1)[0]
    # the comparison table is the matched scope: 11 against 14, not 11 against 50
    row = sec.split("<tr><td class=\"hash\">midpoint</td>", 1)[1].split("</tr>", 1)[0]
    assert ">11<" in row and ">14<" in row and "50" not in row
    assert "1.273" in row                                  # ratio at matched scope
    assert "excludes the derivative call" in sec and "loop control" in sec
    # instruction accurate, not cycle accurate, in the document's own words
    assert "instruction accurate and NOT cycle accurate" in sec
    # the TRM assumptions are named, and a one-cycle entry reads as one cycle
    assert "taken branch: 3 cycles" in sec and "MULS on the fast multiplier: 1 cycle" in sec
    assert "1 cycles" not in sec
    # 2 of the 3 rows miss the 0.25 band, and the page says which and by how much
    assert "2 of 3 methods sit outside the 0.25 band" in sec
    assert "rk4 at 0.507" in sec and "11e898cb at 0.371" in sec
    # the inversion is named with both numbers, and the small multiplier is not
    assert "One pair comes out the other way round" in sec
    assert "rk4" in sec and "rk38" in sec and "0.9429" in sec
    assert "no pair is inverted" in sec
    # the champion's MULS count and the dyadic assumption behind its cost
    assert "the trace contains 3 MULS per step" in sec
    assert "0 of them apply a tableau coefficient" in sec
    # the correction is an epoch decision, said in those terms
    assert "moves VERIFIER_HASH" in sec and "epoch decision" in sec
    # the whole-step numbers exist but sit in a fold that says what they include
    assert "The assumptions, the limits and the whole-step counts" in sec
    assert "cycles traced, whole step" in sec
    assert render_validation(None, trace=doc) == html
    check_banned(html)
    sg.check_hover("validation.html", html)
    sg.check_tallies("validation.html", html)


def test_a_missing_trace_document_says_so_and_draws_nothing():
    html = render_validation(None, trace={})
    assert '<h2 id="trace">' in html
    assert "rk-work/trace/results.json has not been written" in html
    assert "cycles_traced" not in html
    # and with no trace argument at all the section is absent, not empty
    assert '<h2 id="trace">' not in render_validation(None)


def test_no_page_tells_the_reader_to_hover(monkeypatch, tmp_path):
    """F12. The wide drawing is the only one carrying mark titles and the stylesheet
    hides it below 640px, so "hover a dot" was false on every phone and in every screen
    reader. The gate runs over emitted HTML with the stylesheet stripped, because the
    :hover rules in it are styling rather than an instruction."""
    work, arch = _site_archive(monkeypatch, tmp_path)
    _write_validation(work, _stiff_validation_fixture())
    _write_benchmark(work, _benchmark_fixture())
    _write_sidetrack(work, _sidetrack_fixture())
    out = tmp_path / "docs"
    build(arch, out)
    for page in sorted(out.glob("*.html")):
        html = page.read_text(encoding="utf-8")
        sg.check_hover(page.name, html)
        body = re.sub(r"<style>.*?</style>", "", html, flags=re.S)
        assert "hover" not in body.lower(), page.name
        assert ":hover" in html                     # the stylesheet keeps its own rules
    with pytest.raises(sg.ClaimError):
        sg.check_hover("x.html", "<p>hover a dot for exact values</p>")
    # a page whose only hover is inside the stylesheet passes
    sg.check_hover("x.html", "<style>a:hover{color:red}</style><p>select a row</p>")


def test_every_win_tally_carries_both_sample_sizes(monkeypatch, tmp_path):
    """F4 acceptance 5, as a gate. A win count over a maximum of one set against a
    maximum of another means nothing without the size of both sets."""
    work, arch = _site_archive(monkeypatch, tmp_path)
    _write_validation(work, _stiff_validation_fixture())
    _write_benchmark(work, _benchmark_fixture())
    out = tmp_path / "docs"
    build(arch, out)
    for page in sorted(out.glob("*.html")):
        sg.check_tallies(page.name, page.read_text(encoding="utf-8"))
    with pytest.raises(sg.ClaimError):
        sg.check_tallies("v.html", "<p>Discovered methods have the lower Q15 error on "
                                   "4 of 5 problems.</p>")
    sg.check_tallies("v.html", "<p>Discovered methods have the lower Q15 error on 4 of 5 "
                               "problems, 3 discovered against 5 classical.</p>")
    # a paragraph quoted from a source document cannot be rewritten, so it is exempt and
    # the note beside it carries the sizes instead
    sg.check_tallies("v.html", '<p class="quoted">the best discovered method has lower '
                               "Q15 error on 1 of 3 problems.</p>")


def test_the_cell_page_calls_stability_imag_a_bisection_floor():
    """F11. The scorer bisects a sampled grid, so a method whose stability region meets
    the imaginary axis only at the origin reports the smallest resolvable interval rather
    than 0, and every order-2 record carries the same value for that reason. evaluator.py
    is pinned, so the fix is at render time."""
    c = _classical_8()
    rec = _rec(c["midpoint"], _sv(11, 11, 0.02, 0.084, 1.2), "no_improvement", 2, None)
    html = render_cell(2, 2, 0, rec)
    assert "stability_imag <span class=\"note\">(bisection floor)</span>" in html
    assert "4000 samples and 200 bisection steps" in html
    assert "a floor rather than a measured extent" in html
    # the old sentence called both extents measurements of the region
    assert "are the extents of the stability region" not in html
    check_banned(html)


def test_a_stage_count_of_one_reads_as_one_stage():
    """F13 acceptance 5: euler is the only one-stage method and it wrote "1 stages" into
    eighteen published strings, one of them a visible subtitle."""
    c = _classical_8()
    rec = _rec(c["euler"], _sv(5, 5, 0.32, 0.594, 1.0), "no_improvement", 1, None)
    arch = ArchiveState(n_records=1, last_cycle_id=1,
                        grids={1: {(1, 0): rec}, 2: {}, 3: {}, 4: {}},
                        open_hypotheses=(), refuted_hypotheses=())
    pages = [render_explicit(arch), render_cell(1, 1, 0, rec)]
    for html in pages:
        assert "1 stages" not in html
        assert "1 stage" in html
    assert '<p class="sub">grid order 1, 1 stage, cycle bucket 0</p>' in pages[1]
    assert sg._stages(1) == "1 stage" and sg._stages(3) == "3 stages"


def test_every_chart_table_is_reachable_from_its_chart(monkeypatch, tmp_path):
    """F13 acceptance 3. role="img" collapses a chart to one announced node, so the
    numbers inside the marks are unreachable; the table is the path to them, and
    aria-describedby is what ties the two together."""
    work, arch = _site_archive(monkeypatch, tmp_path)
    _write_validation(work, _stiff_validation_fixture())
    _write_benchmark(work, _benchmark_fixture())
    out = tmp_path / "docs"
    build(arch, out)
    seen = 0
    for page in sorted(out.glob("*.html")):
        html = page.read_text(encoding="utf-8")
        ids = set(re.findall(r'id="([^"]+)"', html))
        for target in set(re.findall(r'aria-describedby="([^"]+)"', html)):
            assert target in ids, (page.name, target)
            seen += 1
            # chart first, then its table, which is the order the class pages already use
            assert html.index(f'aria-describedby="{target}"') < html.index(f'id="{target}"')
    assert seen >= 4
    # F13 remainder: every non-decorative chart on the site names how much it
    # plots and points at a table of its own numbers. The previous pass did the
    # nine charts the audit named and left 33 labels without a count, most of them
    # the per-problem panels that repeat once per problem. The rule is checked over
    # the whole build rather than chart by chart, so a new chart cannot arrive
    # without either half.
    charts = 0
    for page in sorted(out.glob("*.html")):
        html = page.read_text(encoding="utf-8")
        ids = set(re.findall(r'id="([^"]+)"', html))
        for attrs in re.findall(r'<svg\b([^>]*)>', html):
            if 'aria-hidden="true"' in attrs:
                continue
            charts += 1
            label = re.search(r'aria-label="([^"]*)"', attrs)
            assert label and label.group(1), (page.name, attrs[:90])
            assert re.search(r"\b\d[\d,]*\s+(?!of\b)[a-z]", label.group(1)), \
                (page.name, label.group(1))
            db = re.search(r'aria-describedby="([^"]+)"', attrs)
            assert db and db.group(1) in ids, (page.name, label.group(1))
    assert charts >= 8, charts
    val = (out / "validation.html").read_text(encoding="utf-8")
    assert 'aria-describedby="validation-non-stiff-values"' in val
    assert "<summary>Every plotted run, in one table (" in val
    exp = (out / "explicit.html").read_text(encoding="utf-8")
    assert 'aria-describedby="elite-table"' in exp        # the grids point at the elites
    assert re.search(r'aria-hidden="true"><title>order \d+, \d+ stages?, bucket \d+: empty', exp)


def test_a_chart_mark_that_is_a_link_gets_a_focus_ring():
    """F12's compliant half: the 54 links inside the explicit page's charts were tab
    stops with nothing to show for it. CSS only, so the site stays JavaScript-free."""
    assert "svg a:focus-visible rect" in sg._STYLE
    assert "outline:2px solid var(--s1)" in sg._STYLE
