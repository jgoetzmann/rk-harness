"""T16 tests: the open-ended lane search and its parallel unpinned archives
(rk_harness/lanesearch.py).

Same tier as tests/test_t16_lanes.py and a separate file on purpose: that file
states that it imports only the standard library, lanes and literature, so a
collection error there cannot come from a missing scientific dependency, and
importing lanesearch would break that property. A tier may span several files
(T5 already spans three), so no _SUITE_DESC entry is needed and no new tier
appears in the overview build.

Covers: the enumeration is a pure function of its index and never of a clock, an
environment or an archive; a candidate that is not an embedded pair, or whose
estimate estimates nothing, is skipped rather than measured; the adaptive
per-attempt cost reproduces validation_axes exactly for BS32, which is the one
candidate both modules can price; a zero budget measures exactly one candidate and
never raises; two firings at the same start and stamp produce identical records;
records are deduplicated by key so a coarse lattice value reappearing in a finer
shell is not measured twice; nothing is written under any scored path; the elite
rule is a total order and is invariant under a shuffled input; the record and
elites schemas reject damaged documents; no non-finite float reaches disk; and
every record says, in a field, that it is not comparable with a scored archive
record.

Every build here is tiny on purpose: one or two problems, one or two targets, a
short ladder and a small work cap. The lane firings the container would run are
seconds each and nothing in the suite runs one.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import re
from fractions import Fraction

import pytest

from rk_harness import lanesearch as LS
from rk_harness import validation as V
from rk_harness import validation_axes as VA
from rk_harness.paths import HARNESS_DIR, work_dir

BANNED = ("novel", "first", "beats", "outperforms", "breakthrough", "proves",
          "state-of-the-art", "best-ever")
EM_DASH = chr(0x2014)

# One problem, two targets, a three-rung ladder, a small attempt cap and a short
# step ladder: a whole record in a fraction of a second.
P1 = ["buck_converter"]
P2 = ["buck_converter", "battery_2rc"]
T2 = [2.0 ** -6, 2.0 ** -10]
LADDER = [512, 64, 8]
KW_A = dict(problems=P1, targets=T2, tol_ladder=LADDER, max_attempts=2000)
KW_I = dict(problems=P1, targets=T2, n_max=64, bisect_probes=3)


def _kw(lane):
    return dict(KW_A) if lane == "adaptive" else dict(KW_I)


def _one(lane, **kw):
    """One record, measured but not written, from a fixed start."""
    args = _kw(lane)
    args.update(kw)
    recs = LS.step(lane, budget_seconds=0.0, cycle=3, seed=5,
                   ts="2026-09-09T00:00:00Z", start=0, write=False, **args)
    assert len(recs) == 1
    return recs[0]


# --------------------------------------------------------------------------- the enumeration

@pytest.mark.parametrize("lane", list(LS.LANES))
def test_the_enumeration_is_a_pure_function_of_its_index(lane, monkeypatch):
    """No clock, no environment, no archive. The archive decides where a firing
    starts walking; it never decides what is at an index."""
    at = LS.CANDIDATE_AT[lane]
    monkeypatch.setenv("RK_WORK_DIR", "/nowhere-at-all")
    a = [at(i) for i in range(40)]
    monkeypatch.setenv("RK_WORK_DIR", "/somewhere-else")
    b = [at(i) for i in range(40)]
    strip = lambda c: None if c is None else {k: v for k, v in c.items()
                                              if not k.startswith("_")}
    assert [strip(c) for c in a] == [strip(c) for c in b]


@pytest.mark.parametrize("lane", list(LS.LANES))
def test_the_shells_partition_the_index_line(lane):
    """Every index lands in exactly one shell, and the offsets cover it."""
    size = LS.adaptive_shell_size if lane == "adaptive" else LS.implicit_shell_size
    base = 0
    for k in range(4):
        n = size(k)
        assert n > 0
        assert LS._shell_of(base, size) == (k, 0)
        assert LS._shell_of(base + n - 1, size) == (k, n - 1)
        base += n


@pytest.mark.parametrize("lane", list(LS.LANES))
def test_the_shells_do_not_stop(lane):
    """Unboundedness is the point: the lattice grows shell by shell, so there is no
    last candidate to run out of."""
    size = LS.adaptive_shell_size if lane == "adaptive" else LS.implicit_shell_size
    stride = len(LS.ADAPTIVE_SHELL_STAGES) if lane == "adaptive" else 1
    # Compared along one stage count, because the adaptive shells alternate between
    # four stages and three and a three-stage shell is smaller than the four-stage
    # one beside it.
    sizes = [size(k * stride) for k in range(5)]
    assert sizes == sorted(sizes) and sizes[-1] > sizes[0]
    assert size(10 * stride) > size(6 * stride) > size(2 * stride)


def test_an_index_past_the_shell_guard_is_refused_rather_than_looped():
    total = sum(LS.implicit_shell_size(k) for k in range(LS.MAX_SHELLS))
    with pytest.raises(ValueError):
        LS.implicit_candidate(total)


@pytest.mark.parametrize("bad", [-1, 1.5, True, "3"])
def test_a_non_index_is_refused(bad):
    with pytest.raises(ValueError):
        LS._shell_of(bad, LS.implicit_shell_size)


def test_the_adaptive_pair_is_admitted_only_when_it_is_a_pair():
    """Order 3 solvable, order 2 solvable with a free column so d is not forced to
    zero, and an estimate of genuinely lower order than the propagated formula."""
    found = 0
    for i in range(300):
        cand = LS.adaptive_candidate(i)
        if cand is None:
            continue
        found += 1
        m = cand["method"]
        assert m["order_hat"] < m["order"]
        assert m["nonzero_d_terms"] >= 1
        d = [Fraction(x) for x in m["d"]]
        b = [Fraction(x) for x in m["b"]]
        bh = [Fraction(x) for x in m["b_hat"]]
        assert d == [b[i] - bh[i] for i in range(len(b))]
        assert any(v != 0 for v in d)
    assert found > 0, "the leading adaptive shell produced no pair at all"


def test_exact_representability_is_recorded_and_not_required():
    """An order-3 b generally carries a denominator with a factor of three, and
    BS32's own b is not dyadic either, so exactness cannot be a filter."""
    keys = set()
    for i in range(300):
        cand = LS.adaptive_candidate(i)
        if cand is not None:
            keys |= {"b_exact", "b_hat_exact", "d_exact"} & set(cand["method"])
    assert keys == {"b_exact", "b_hat_exact", "d_exact"}


def test_no_dyadic_gamma_is_l_stable_and_the_record_says_so():
    """R at infinity vanishing needs a root that is irrational, so l_stable is false
    for every candidate this lane can construct. r_at_infinity is the margin."""
    seen = 0
    for i in range(200):
        cand = LS.implicit_candidate(i)
        if cand is None:
            continue
        seen += 1
        assert cand["stability"]["l_stable"] is False
        assert Fraction(cand["stability"]["r_at_infinity"]) != 0
        assert "irrational" in cand["stability"]["note"]
    assert seen > 0


# --------------------------------------------------------------------------- cost

@pytest.mark.parametrize("n_states", [1, 2, 3, 5])
def test_the_adaptive_attempt_cost_reproduces_validation_axes_for_bs32(n_states):
    """BS32 is the one candidate both modules can price, and they must agree on it.

    validation_axes prices the estimate as the whole four-term listing; this module
    prices one block per nonzero d term so a candidate pair with a different number
    of them can be priced at all. For BS32, which has four, the two are the same
    number, and the block is sliced out of the listing rather than retyped.
    """
    assert LS.pair_attempt_cost(VA.BS32_TABLEAU, 4, n_states) == \
        VA.adaptive_attempt_cost(n_states)


def test_the_estimate_block_is_one_term_of_the_reference_listing():
    from rk_harness.costmodel import M0PLUS_FAST, count_sequence
    from rk_harness.prototypes import adaptive_q15 as AQ
    whole = AQ.reference_sections()["estimate"]
    block = LS.estimate_block()
    assert len(block) * len(AQ.D_REPS) == len(whole)
    assert count_sequence(block, M0PLUS_FAST) * len(AQ.D_REPS) == \
        count_sequence(whole, M0PLUS_FAST)


def test_the_cost_basis_says_which_grade_of_number_it_is():
    for basis in LS.COST_BASES:
        doc = LS.COST_BASIS_DOC[basis]
        for key in ("grade", "direction", "includes", "excludes", "source"):
            assert isinstance(doc[key], str) and doc[key].strip()
    # The adaptive grade is the mixed one, and it says in a field why it is mixed.
    assert "why_mixed" in LS.COST_BASIS_DOC["float_trajectory_q15_attempt_cost"]
    assert LS.COST_BASIS_DOC["design_estimate_div32"] == \
        VA.COST_BASIS_DOC["design_estimate_div32"]


# --------------------------------------------------------------------------- one firing

@pytest.mark.parametrize("lane", list(LS.LANES))
def test_a_zero_budget_measures_exactly_one_candidate_and_never_raises(lane):
    """The budget gates starting a candidate, not finishing one, so a lane always
    produces something rather than nothing."""
    rec = _one(lane)
    assert rec["lane"] == lane
    assert rec["schema"] == LS.SCHEMAS[lane]
    LS.validate_record(rec)


@pytest.mark.parametrize("lane", list(LS.LANES))
def test_two_firings_at_the_same_start_and_stamp_are_identical(lane):
    a = _one(lane)
    b = _one(lane)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


@pytest.mark.parametrize("lane", list(LS.LANES))
def test_a_record_differs_only_in_its_stamp_and_its_cycle(lane):
    """The stamp is the caller's; everything else is a pure function of the code,
    the candidate and the work caps, which is what makes a record reproducible."""
    args = _kw(lane)
    a = LS.step(lane, budget_seconds=0.0, cycle=1, ts="2026-09-09T00:00:00Z",
                start=0, write=False, **args)[0]
    b = LS.step(lane, budget_seconds=0.0, cycle=2, ts="2026-09-10T05:00:00Z",
                start=0, write=False, **args)[0]
    assert sorted(k for k in a if a[k] != b[k]) == ["cycle", "ts"]
    assert a["record_hash"] == b["record_hash"]


@pytest.mark.parametrize("lane", list(LS.LANES))
def test_the_results_cover_every_problem_and_target_once(lane):
    args = _kw(lane)
    args["problems"] = P2
    rec = _one(lane, **args)
    got = sorted((r["problem"], r["target_key"]) for r in rec["results"])
    want = sorted((p, LS._target_key(t)) for p in P2 for t in T2)
    assert got == want
    assert rec["score"]["targets_total"] == len(want)


def test_an_adaptive_cycle_number_is_attempts_times_the_per_attempt_price():
    """Re-derived here from the row's own counters, not read back out of the module,
    so the published number stays checkable."""
    rec = _one("adaptive")
    for row in rec["results"]:
        if row["cycles"] is None:
            continue
        assert row["cycles"] == row["attempts"] * row["cycles_per_attempt"]
        assert row["cycles_accepted_only"] == row["n_accepted"] * row["cycles_per_attempt"]
        assert row["cycles_accepted_only"] <= row["cycles"]


def test_an_implicit_cycle_number_is_steps_times_the_per_step_price():
    rec = _one("implicit")
    iters = rec["method"]["newton_iters"]
    for row in rec["results"]:
        if row["cycles"] is None:
            continue
        assert row["cycles"] == row["steps"] * row["cycles_per_step"]
        assert row["njev"] == row["steps"] and row["nlu"] == row["steps"]
        assert row["nlinsolve"] == row["steps"] * 2 * iters


# --------------------------------------------------------------------------- the archive

@pytest.mark.parametrize("lane", list(LS.LANES))
def test_a_firing_writes_a_record_a_ledger_line_and_the_elites(lane):
    args = _kw(lane)
    recs = LS.step(lane, budget_seconds=0.0, cycle=11, ts="2026-09-09T00:00:00Z",
                   start=0, **args)
    assert len(recs) == 1
    day = LS.records_path(lane, "2026-09-09")
    assert day.exists() and LS.ledger_path(lane).exists()
    assert LS.load_records(lane) == recs
    line = LS.load_ledger(lane)[0]
    assert set(line) == set(LS.LEDGER_KEYS)
    assert line["record_hash"] == recs[0]["record_hash"]
    assert line["file"] == day.name
    doc = LS.load_elites(lane)
    LS.validate_elites(doc)
    assert doc["_meta"]["n_elites"] == 1


@pytest.mark.parametrize("lane", list(LS.LANES))
def test_a_measured_candidate_is_not_measured_twice(lane):
    """A coarse lattice value reappears inside a finer shell, so the lane
    deduplicates by key against its own ledger, exactly as sidetrack does."""
    args = _kw(lane)
    a = LS.step(lane, budget_seconds=0.0, cycle=1, start=0, **args)
    b = LS.step(lane, budget_seconds=0.0, cycle=2, start=0, **args)
    assert a[0]["key"] != b[0]["key"]
    assert b[0]["index"] > a[0]["index"]
    assert len({r["key"] for r in LS.load_records(lane)}) == 2


@pytest.mark.parametrize("lane", list(LS.LANES))
def test_a_firing_resumes_from_the_ledger_when_no_start_is_given(lane):
    args = _kw(lane)
    a = LS.step(lane, budget_seconds=0.0, cycle=1, **args)
    assert LS.next_index(lane) == a[0]["index"] + 1
    b = LS.step(lane, budget_seconds=0.0, cycle=2, **args)
    assert b[0]["index"] >= a[0]["index"] + 1


def test_a_ledger_line_under_another_code_hash_does_not_close_a_candidate():
    """A record counts as measured only under the code that measured it, which is
    the rule sidetrack's code_hash exists to hold."""
    args = _kw("adaptive")
    LS.step("adaptive", budget_seconds=0.0, cycle=1, start=0, **args)
    led = LS.load_ledger("adaptive")
    assert LS.measured_keys("adaptive", ch="0000000000000000", ledger=led) == set()
    assert LS.next_index("adaptive", ch="0000000000000000", ledger=led) == 0


def test_a_torn_ledger_line_is_discarded_rather_than_fatal():
    args = _kw("adaptive")
    LS.step("adaptive", budget_seconds=0.0, cycle=1, start=0, **args)
    path = LS.ledger_path("adaptive")
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write('{"ts": "2026-09-09T00:00')
    assert len(LS.load_ledger("adaptive")) == 1


def test_a_missing_archive_is_not_an_error():
    assert LS.load_ledger("adaptive") == []
    assert LS.load_records("implicit") == []
    assert LS.load_elites("adaptive") is None
    assert LS.next_index("adaptive") == 0


# --------------------------------------------------------------------------- the scored path

def _preflight():
    spec = importlib.util.spec_from_file_location(
        "rk_preflight_for_lanesearch", HARNESS_DIR / "scripts" / "preflight.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_guard_list_is_the_one_preflight_holds():
    """scripts/ is not an importable package, so the list is duplicated in the
    module as a guard that can actually run. This is what keeps the copy honest."""
    assert tuple(LS.SCORED_PATHS) == tuple(_preflight().SCORED_PATHS)


@pytest.mark.parametrize("name", list(LS.SCORED_PATHS))
def test_no_write_may_land_under_a_scored_path(name):
    with pytest.raises(ValueError):
        LS._guard_path(work_dir() / name / "anything.jsonl")


@pytest.mark.parametrize("lane", list(LS.LANES))
def test_a_firing_writes_only_inside_its_own_lane_directory(lane):
    args = _kw(lane)
    LS.step(lane, budget_seconds=0.0, cycle=1, start=0, **args)
    written = sorted(p.relative_to(work_dir()).parts[0]
                     for p in work_dir().rglob("*") if p.is_file())
    assert set(written) == {f"{lane}_archive"}
    assert not set(written) & set(LS.SCORED_PATHS)


# --------------------------------------------------------------------------- the elite rule

def _fake(hash_, median, reached, lane="adaptive"):
    return {
        "lane": lane, "record_hash": hash_, "results_digest": "d" + hash_,
        "key": "k" + hash_, "index": 0, "shell": 0, "cycle": 1,
        "ts": "2026-09-09T00:00:00Z", "cost_basis": LS.COST_BASES[0],
        "lanesearch_code_hash": "abc", "method": {"family": "x"},
        "score": {"median_cycles_at_target": median, "targets_reached": reached,
                  "reached_at_elite_target": 1, "problems_at_elite_target": 1,
                  "targets_total": 4, "worst_status": "reached",
                  "elite_target": float(LS.ELITE_TARGET),
                  "elite_target_key": LS._target_key(LS.ELITE_TARGET),
                  "best_achieved_error": 0.5},
        "controller": {"alpha": "1/4"},
    }


def test_the_elite_rule_is_a_total_order_and_ignores_the_input_order():
    recs = [_fake("aa", 900.0, 2), _fake("bb", 100.0, 4), _fake("cc", None, 1),
            _fake("dd", 100.0, 1), _fake("ee", 100.0, 4)]
    order = [r["record_hash"] for r in LS.rank(recs)]
    for rotation in range(len(recs)):
        shuffled = recs[rotation:] + recs[:rotation]
        assert [r["record_hash"] for r in LS.rank(shuffled)] == order
    assert [r["record_hash"] for r in LS.rank(list(reversed(recs)))] == order
    # lowest median leading, then most targets reached, then record_hash; a
    # candidate that reached the elite target nowhere ranks below every one that did.
    assert order == ["bb", "ee", "dd", "aa", "cc"]


def test_the_elite_rule_deduplicates_by_record_hash():
    recs = [_fake("aa", 100.0, 2), _fake("aa", 100.0, 2)]
    assert len(LS.rank(recs)) == 1


def test_the_elites_document_is_capped_and_ordered():
    recs = [_fake(f"{i:02d}", float(1000 - i), 2) for i in range(10)]
    doc = LS.build_elites("adaptive", recs, cap=4, ch="abc")
    LS.validate_elites(doc)
    assert doc["_meta"]["n_elites"] == 4 and doc["_meta"]["n_ranked"] == 10
    assert [e["record_hash"] for e in doc["elites"]] == ["09", "08", "07", "06"]


def test_the_elites_document_counts_only_the_current_code_hash():
    recs = [_fake("aa", 100.0, 2), dict(_fake("bb", 50.0, 2),
                                        lanesearch_code_hash="other")]
    doc = LS.build_elites("adaptive", recs, ch="abc")
    assert doc["_meta"]["n_ranked"] == 1
    assert doc["_meta"]["n_input"] == 2
    assert [e["record_hash"] for e in doc["elites"]] == ["aa"]


def test_two_builds_over_the_same_records_are_byte_identical():
    recs = [_fake(f"{i:02d}", float(500 + i), 3) for i in range(6)]
    a = json.dumps(LS.build_elites("adaptive", recs, ch="abc"), indent=1,
                   sort_keys=True, allow_nan=False)
    b = json.dumps(LS.build_elites("adaptive", list(reversed(recs)), ch="abc"),
                   indent=1, sort_keys=True, allow_nan=False)
    assert a == b


def test_an_empty_archive_reports_nothing_rather_than_an_intention():
    doc = LS.build_elites("implicit", [], ch="abc")
    LS.validate_elites(doc)
    assert doc["_meta"]["n_ranked"] == 0 and doc["elites"] == []
    assert doc["_meta"]["generated_ts"] == "" and doc["_meta"]["generated_cycle"] == 0
    assert "No candidate has been ranked yet" in doc["statement"]


def test_the_elites_document_reads_no_clock():
    """generated_ts is the newest ranked record's own stamp, so a page built from
    this document cannot move while its inputs stand still."""
    recs = [_fake("aa", 100.0, 2), dict(_fake("bb", 200.0, 2),
                                        ts="2026-09-10T09:00:00Z", cycle=99)]
    doc = LS.build_elites("adaptive", recs, ch="abc")
    assert doc["_meta"]["generated_ts"] == "2026-09-10T09:00:00Z"
    assert doc["_meta"]["generated_cycle"] == 99


def test_update_elites_merges_a_firing_into_what_is_already_there():
    LS.write_elites(LS.build_elites("adaptive", [_fake("aa", 900.0, 2)], ch="abc"))
    doc = LS.update_elites("adaptive", [_fake("bb", 100.0, 2)], ch="abc")
    assert [e["record_hash"] for e in doc["elites"]] == ["bb", "aa"]
    assert doc["_meta"]["n_ranked"] == 2
    # what the ledger says, which is its own question and is zero here because no
    # firing wrote one
    assert doc["_meta"]["n_measured"] == 0


def test_update_elites_drops_what_a_stale_code_hash_ranked():
    LS.write_elites(LS.build_elites("adaptive", [_fake("aa", 1.0, 9)], ch="old"))
    doc = LS.update_elites("adaptive", [_fake("bb", 100.0, 2)], ch="abc")
    assert [e["record_hash"] for e in doc["elites"]] == ["bb"]


# --------------------------------------------------------------------------- schemas and prose

@pytest.mark.parametrize("lane", list(LS.LANES))
def test_a_record_says_in_a_field_what_it_is_not(lane):
    """Two archives that look alike and mean different things is how a published
    number goes wrong, so the incomparability is a field and not a comment."""
    rec = _one(lane)
    for word in ("not comparable", "rk-work/archive/", "float64", "verifier.py"):
        assert word in rec["not_comparable"]
    assert "not on that list" in rec["not_a_page_source"]
    assert rec["cost_basis"] in LS.COST_BASES


@pytest.mark.parametrize("lane", list(LS.LANES))
def test_a_record_carries_its_work_caps_as_counts(lane):
    """A cap expressed in seconds would make an artifact depend on the machine it
    ran on. Every one of these is a count or a bound on the data."""
    rec = _one(lane)
    cap = rec["params"]["work_cap"]
    assert set(cap) == {"max_attempts", "tol_ladder_lsb", "n_max", "bisect_probes",
                        "diverge_at"}
    if lane == "adaptive":
        assert cap["max_attempts"] == KW_A["max_attempts"]
        assert cap["tol_ladder_lsb"] == LADDER
    else:
        assert cap["n_max"] == KW_I["n_max"] and cap["bisect_probes"] == KW_I["bisect_probes"]
        assert set(cap["diverge_at"]) == set(P1)
    assert "second" not in json.dumps(cap)


@pytest.mark.parametrize("mutate,reason", [
    (lambda r: r.update(schema="wrong/1"), "schema"),
    (lambda r: r.update(lane="explicit"), "lane"),
    (lambda r: r.update(cost_basis="assembly_verified"), "cost basis"),
    (lambda r: r.update(results=[]), "empty results"),
    (lambda r: r["results"].pop(), "missing row"),
    (lambda r: r["results"].append(dict(r["results"][0])), "duplicated row"),
    (lambda r: r["results"][0].update(status="fine"), "unknown status"),
    (lambda r: r["results"][0].update({"class": "explicit"}), "wrong class"),
    (lambda r: r.update(ts=""), "empty stamp"),
    (lambda r: r["params"].pop("work_cap"), "no work cap"),
    (lambda r: r.pop("controller"), "no controller"),
    (lambda r: r["score"].update(targets_total=99), "score disagrees with results"),
])
def test_validate_record_rejects_a_damaged_record(mutate, reason):
    rec = copy.deepcopy(_one("adaptive"))
    LS.validate_record(rec)
    mutate(rec)
    with pytest.raises(ValueError):
        LS.validate_record(rec)


@pytest.mark.parametrize("mutate", [
    lambda d: d["_meta"].update(schema="wrong/1"),
    lambda d: d["_meta"].update(lane="explicit"),
    lambda d: d["_meta"].update(n_elites=99),
    lambda d: d["_meta"].update(n_measured="two"),
    lambda d: d["_meta"].update(cost_basis="assembly_verified"),
    lambda d: d["_meta"].pop("cost_bases"),
    lambda d: d["_meta"].update(not_comparable=""),
    lambda d: d.update(rule=""),
    lambda d: d["elites"].reverse(),
    lambda d: d["elites"].append(dict(d["elites"][0])),
])
def test_validate_elites_rejects_a_damaged_document(mutate):
    doc = LS.build_elites("adaptive", [_fake("aa", 100.0, 2), _fake("bb", 200.0, 2)],
                          ch="abc")
    LS.validate_elites(doc)
    mutate(doc)
    with pytest.raises(ValueError):
        LS.validate_elites(doc)


def test_a_non_finite_float_never_reaches_disk():
    doc = LS.build_elites("adaptive", [_fake("aa", 100.0, 2)], ch="abc")
    LS.validate_elites(doc)
    doc["elites"][0]["score"]["median_cycles_at_target"] = float("inf")
    with pytest.raises(ValueError):
        LS.validate_elites(doc)


@pytest.mark.parametrize("lane", list(LS.LANES))
def test_nothing_written_carries_a_banned_word_or_an_em_dash(lane):
    args = _kw(lane)
    LS.step(lane, budget_seconds=0.0, cycle=1, start=0, **args)
    blob = "\n".join(p.read_text(encoding="utf-8")
                     for p in sorted(work_dir().rglob("*")) if p.is_file())
    low = blob.lower()
    for word in BANNED:
        assert not re.search(rf"(?<![a-z0-9-]){re.escape(word)}(?![a-z0-9-])", low), word
    assert EM_DASH not in blob


def test_every_number_written_is_finite():
    args = _kw("adaptive")
    args["problems"] = P2
    LS.step("adaptive", budget_seconds=0.0, cycle=1, start=0, **args)
    for path in sorted(work_dir().rglob("*.json*")):
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".jsonl":
            for line in text.splitlines():
                if line.strip():
                    json.loads(line, parse_constant=_no_constant)
        else:
            json.loads(text, parse_constant=_no_constant)


def _no_constant(name):
    raise AssertionError(f"a non-finite float reached disk: {name}")


# --------------------------------------------------------------------------- inertness

def test_nothing_in_the_package_calls_lanesearch():
    """This batch ships inert: the module exists, nothing runs it. When batch 9
    wires the lanes into the cycle loop this test is the one to change, deliberately.
    """
    pkg = HARNESS_DIR / "rk_harness"
    callers = sorted(p.name for p in pkg.rglob("*.py")
                     if p.name != "lanesearch.py"
                     and "lanesearch" in p.read_text(encoding="utf-8"))
    assert callers == [], f"lanesearch is referenced by {callers}"


def test_lanesearch_is_not_in_the_sidetrack_digest():
    """Adding it would move sidetrack.code_hash and re-open all the measured
    side-track points on every edit to this module, for no reason."""
    from rk_harness import sidetrack
    assert "rk_harness/lanesearch.py" not in sidetrack.SIDETRACK_FILES
    assert "rk_harness/validation_axes.py" not in sidetrack.SIDETRACK_FILES


def test_the_lane_digest_covers_every_module_that_decides_a_number():
    for rel in LS.LANESEARCH_FILES:
        assert (HARNESS_DIR / rel).exists(), rel
    for rel in ("rk_harness/lanesearch.py", "rk_harness/validation_axes.py",
                "rk_harness/secondpass.py", "rk_harness/validation.py",
                "rk_harness/prototypes/adaptive.py", "rk_harness/prototypes/sdirk.py"):
        assert rel in LS.LANESEARCH_FILES, rel
    assert isinstance(LS.code_hash(), str) and len(LS.code_hash()) == 16


def test_no_pinned_file_is_in_the_lane_digest():
    from rk_harness.verifier_hash import VERIFIER_FILES
    assert not set(LS.LANESEARCH_FILES) & set(VERIFIER_FILES)


def test_the_module_reads_the_clock_in_one_place_only():
    """A record is a pure function of (code, candidate, params) apart from its
    stamp, so the clock has exactly one caller and the caller can pass its own."""
    src = (HARNESS_DIR / "rk_harness" / "lanesearch.py").read_text(encoding="utf-8")
    body = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
    assert body.count("time.gmtime()") == 1
    assert "random." not in body and "utcnow" not in body
    # time.monotonic is the budget, which is a wall-clock question by definition and
    # never reaches an artifact.
    assert ".now(" not in body


def test_the_command_line_reports_status_without_measuring_anything(capsys):
    assert LS.main(["--lane", "implicit", "--status"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["lane"] == "implicit" and out["next_index"] == 0
    assert not list(work_dir().rglob("*.jsonl"))


def test_the_command_line_refuses_an_unknown_problem(capsys):
    assert LS.main(["--lane", "adaptive", "--problems", "not_a_problem"]) == 2
    assert "unknown problems" in capsys.readouterr().err


# --------------------------------------------------------------------------- the firing budget

def test_a_budget_measures_more_than_one_candidate():
    """The zero-budget case is the floor, not the shape. A real budget keeps going
    until the clock says stop, and the clock only ever gates STARTING a candidate."""
    args = _kw("adaptive")
    seen = []
    recs = LS.step("adaptive", budget_seconds=1.0, cycle=1, start=0, write=False,
                   log=lambda kind, **d: seen.append((kind, d)), **args)
    assert len(recs) > 1
    assert [r["index"] for r in recs] == sorted({r["index"] for r in recs})
    done = [d for kind, d in seen if kind == "lanesearch_done"][0]
    assert done["status"] in LS.STEP_STATUSES
    assert done["candidates"] == len(recs)


def test_the_candidate_cap_bounds_a_firing_whatever_the_budget():
    """The archive grows a file per record, so the count is the knob and not only
    the clock."""
    args = _kw("adaptive")
    recs = LS.step("adaptive", budget_seconds=60.0, cycle=1, start=0, write=False,
                   max_candidates=2, **args)
    assert len(recs) == 2


def test_a_scan_that_finds_nothing_gives_up_by_count_and_says_so():
    """A count and not a clock: a lane whose whole shell is already measured must
    not spin out its budget looking."""
    args = _kw("adaptive")
    seen = []
    recs = LS.step("adaptive", budget_seconds=5.0, cycle=1, start=0, write=False,
                   scan_cap=1, log=lambda kind, **d: seen.append((kind, d)), **args)
    assert recs == []
    assert [k for k, _ in seen].count("lanesearch_scan_cap") == 1
    assert [d for k, d in seen if k == "lanesearch_done"][0]["status"] == "scan_cap"


def test_a_stop_callback_ends_the_firing_at_a_candidate_boundary():
    args = _kw("adaptive")
    calls = {"n": 0}

    def stop():
        calls["n"] += 1
        return calls["n"] > 2

    recs = LS.step("adaptive", budget_seconds=60.0, cycle=1, start=0, write=False,
                   stop=stop, **args)
    assert len(recs) == 2


def test_the_daily_file_is_named_by_the_records_own_utc_stamp():
    """Storage is UTC. Nothing converts on the way into a file."""
    args = _kw("adaptive")
    LS.step("adaptive", budget_seconds=0.0, cycle=1, start=0,
            ts="2026-12-31T23:59:59Z", **args)
    assert LS.records_path("adaptive", "2026-12-31").exists()
    assert LS.load_ledger("adaptive")[0]["file"] == "2026-12-31.jsonl"


@pytest.mark.parametrize("lane", list(LS.LANES))
def test_a_record_round_trips_through_json_without_a_non_finite_float(lane):
    rec = _one(lane)
    assert json.loads(json.dumps(rec, sort_keys=True, allow_nan=False)) == rec
    assert isinstance(rec["nonfinite_written_as_null"], int)


def test_the_elites_document_keeps_three_counts_apart():
    """A capped document must not let the reader think the archive is as small as
    the cap. n_measured is the ledger's answer, n_ranked is this build's."""
    args = _kw("adaptive")
    for cycle in (1, 2, 3):
        LS.step("adaptive", budget_seconds=0.0, cycle=cycle, **args)
    doc = LS.update_elites("adaptive", [], cap=1)
    LS.validate_elites(doc)
    assert doc["_meta"]["n_measured"] == 3
    assert doc["_meta"]["n_elites"] == 1
    assert doc["_meta"]["n_ranked"] <= doc["_meta"]["n_measured"]
    assert "3 measured candidates" in doc["statement"]
