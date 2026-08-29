"""T3 spec tests: archive, surrogate, encourager, search, enumeration, directive,
prompts, and the import-graph canaries (K12, K13).

Every test cites a SPEC `## Behaviors` ID in its name. Fixtures are built inline
from HANDOFF section 9.1 / SPEC `## Surface`; nothing here reads the implementation.
"""
from __future__ import annotations

import ast
import datetime
import json
import math
import random
from fractions import Fraction
from pathlib import Path

import pytest

from rk_harness.archive import (
    RecordSchemaError,
    RECORD_KEYS,
    record_to_json,
    record_from_json,
    record_order,
    cycle_bucket,
    today_path,
    append,
    read_all,
    elites,
    replay,
    assign_tier,
    metric_value,
    update_cell_stat,
)
from rk_harness.surrogate import should_train, features, train, predict, calibration_error
from rk_harness.encourager import next_action, emptiest_cell, heldout_gap, PACKAGE_DATE, FREEZE_DATE
from rk_harness.search import (
    free_parameters,
    snap,
    project,
    objective,
    cmaes_island,
    migrate,
    default_constraints,
)
from rk_harness.enumeration import (
    lattice,
    enumerate_phase0,
    phase0_candidate_count,
    enumerate_phase1,
    cheapest,
    PHASE0_S_MAX,
)
from rk_harness.directive import (
    DirectiveError,
    DIRECTIVE_SCHEMA,
    validate_directive,
    parse_directive,
    fallback_directive,
)
from rk_harness.prompts import SYSTEM_PROMPT, build_user_prompt
from rk_harness.costmodel import M0PLUS_SLOW, M0PLUS_FAST
from rk_harness.coeffrep import to_rep
from rk_harness.orderconditions import residuals
from rk_harness.tableau import make_tableau, content_hash, row_sums_consistent
from rk_harness.evaluator import evaluate
from rk_harness.paths import PACKAGE_DIR, archive_dir
from rk_harness.types import (
    Tableau,
    ScoreVector,
    Record,
    ArchiveState,
    RunState,
    Action,
    CellStat,
    TIERS,
)

UTC = datetime.timezone.utc
TIER_STRINGS = ("heldout_verified", "search_only", "unreplicated")

PROBLEM_NAMES = (
    "dahlquist", "damped_osc", "vanderpol_mild",
    "pendulum", "dc_motor", "rc_thermal", "quaternion",
)
# HANDOFF section 11 families.
FAMILY = {
    "dahlquist": "linear",
    "damped_osc": "oscillatory",
    "vanderpol_mild": "nonlinear",
    "pendulum": "nonlinear",
    "dc_motor": "linear",
    "rc_thermal": "stiff",
    "quaternion": "geometric",
}


# ---------------------------------------------------------------------------
# Inline fixtures: classical tableaus (HANDOFF section 9.1)
# ---------------------------------------------------------------------------

def _euler() -> Tableau:
    return make_tableau([[0]], [1])


def _midpoint() -> Tableau:
    return make_tableau([[0, 0], ["1/2", 0]], [0, 1])


def _heun2() -> Tableau:
    return make_tableau([[0, 0], [1, 0]], ["1/2", "1/2"])


def _heun3() -> Tableau:
    return make_tableau([[0, 0, 0], ["1/3", 0, 0], [0, "2/3", 0]], ["1/4", 0, "3/4"])


def _kutta3() -> Tableau:
    return make_tableau([[0, 0, 0], ["1/2", 0, 0], [-1, 2, 0]], ["1/6", "2/3", "1/6"])


def _rk4() -> Tableau:
    return make_tableau(
        [[0, 0, 0, 0], ["1/2", 0, 0, 0], [0, "1/2", 0, 0], [0, 0, 1, 0]],
        ["1/6", "1/3", "1/3", "1/6"],
    )


def _rk38() -> Tableau:
    return make_tableau(
        [[0, 0, 0, 0], ["1/3", 0, 0, 0], ["-1/3", 1, 0, 0], [1, -1, 1, 0]],
        ["1/8", "3/8", "3/8", "1/8"],
    )


# ---------------------------------------------------------------------------
# Inline fixtures: ScoreVector / Record / ArchiveState / RunState
# ---------------------------------------------------------------------------

def _per_problem(base: float, **overrides: float) -> dict[str, float]:
    d: dict[str, float] = {}
    for n in PROBLEM_NAMES:
        d[n] = base
        d[f"slow:{n}"] = base
        d[f"avr_approx:{n}"] = base
    for k in ("slow:search_error", "slow:heldout_error",
              "avr_approx:search_error", "avr_approx:heldout_error"):
        d[k] = base
    d.update(overrides)
    return d


def _sv(
    search: float = 0.01,
    heldout: float = 0.02,
    per_problem: dict[str, float] | None = None,
    cycles: dict[str, int] | None = None,
    measured_order: float | None = 4.07,
    stability_real: float = -2.785294,
    stability_imag: float = 2.828427,
    csd: int = 34,
    quant: float = 5.086e-06,
    overflow_margin: float = 2.0,
) -> ScoreVector:
    """All 12 ScoreVector fields, hand-built with plausible rk4-like values."""
    return ScoreVector(
        measured_order=measured_order,
        order_fit_points=3,
        error_constant=0.0123,
        stability_real=stability_real,
        stability_imag=stability_imag,
        cycles=cycles if cycles is not None else {"m0plus_fast": 33, "m0plus_slow": 85, "avr_approx": 150},
        csd_weight_total=csd,
        coeff_quant_error=quant,
        search_error=search,
        heldout_error=heldout,
        overflow_margin=overflow_margin,
        per_problem=per_problem if per_problem is not None else _per_problem(0.01),
    )


def _sv_for(t: Tableau, search: float = 0.01, heldout: float = 0.02) -> ScoreVector:
    """ScoreVector whose cycles/csd match the HANDOFF section 9.1 fixture row for t."""
    table = {
        content_hash(_euler()): ({"m0plus_fast": 5, "m0plus_slow": 5, "avr_approx": 20}, 0, 0.0, 0.98),
        content_hash(_midpoint()): ({"m0plus_fast": 11, "m0plus_slow": 11, "avr_approx": 40}, 1, 0.0, 2.0),
        content_hash(_heun2()): ({"m0plus_fast": 13, "m0plus_slow": 13, "avr_approx": 45}, 2, 0.0, 2.01),
        content_hash(_heun3()): ({"m0plus_fast": 23, "m0plus_slow": 50, "avr_approx": 90}, 19, 1.017e-05, 3.0),
        content_hash(_kutta3()): ({"m0plus_fast": 26, "m0plus_slow": 65, "avr_approx": 100}, 26, 1.017e-05, 3.04),
        content_hash(_rk4()): ({"m0plus_fast": 33, "m0plus_slow": 85, "avr_approx": 150}, 34, 5.086e-06, 4.07),
        content_hash(_rk38()): ({"m0plus_fast": 36, "m0plus_slow": 64, "avr_approx": 140}, 22, 5.086e-06, 4.06),
    }
    cycles, csd, quant, mo = table[content_hash(t)]
    return _sv(search=search, heldout=heldout, cycles=dict(cycles), csd=csd, quant=quant, measured_order=mo)


def _record(
    t: Tableau,
    sv: ScoreVector,
    cycle_id: int = 1,
    seed: int = 0,
    tier: str = "unreplicated",
    directive_id: str | None = None,
    hypothesis_id: str | None = None,
    timestamp: str = "2026-09-21T10:00:00Z",
    verifier_hash: str = "a" * 64,
) -> Record:
    return Record(
        tableau_hash=content_hash(t),
        tableau=t,
        score=sv,
        tier=tier,
        cycle_id=cycle_id,
        seed=seed,
        verifier_hash=verifier_hash,
        directive_id=directive_id,
        hypothesis_id=hypothesis_id,
        timestamp=timestamp,
    )


def _empty_arch() -> ArchiveState:
    return ArchiveState(0, 0, {1: {}, 2: {}, 3: {}, 4: {}}, (), ())


def _arch_with(grids: dict[int, dict[tuple[int, int], Record]]) -> ArchiveState:
    full = {1: {}, 2: {}, 3: {}, 4: {}}
    for k, v in grids.items():
        full[k] = dict(v)
    n = sum(len(g) for g in full.values())
    last = max((r.cycle_id for g in full.values() for r in g.values()), default=0)
    return ArchiveState(n, last, full, (), ())


def _state(stall: int = 0, phase: int = 0, cell: tuple[int, int] | None = None, cycle_id: int = 1) -> RunState:
    return RunState(
        cycle_id=cycle_id,
        phase=phase,
        started_at="2026-09-21T00:00:00Z",
        last_heartbeat="2026-09-21T00:00:00Z",
        spend_usd=0.0,
        stall_counter=stall,
        current_cell=cell,
    )


def _dt(y: int, m: int, d: int, h: int = 12) -> datetime.datetime:
    return datetime.datetime(y, m, d, h, 0, 0, tzinfo=UTC)


def _work(monkeypatch, tmp_path: Path, clock: str = "2026-09-21T10:00:00Z") -> None:
    monkeypatch.setenv("RK_WORK_DIR", str(tmp_path))
    monkeypatch.setenv("RK_CLOCK", clock)


def _all_zero(rs) -> bool:
    return all(r == 0 for r in rs)


# ===========================================================================
# Archive: assign_tier  (K1, K2, B31)
# ===========================================================================

def test_K1_planted_search_tuned_is_search_only_never_heldout_verified():
    inc = _sv(search=0.010, heldout=0.020, per_problem=_per_problem(0.010))
    # Better on SEARCH aggregate, worse on HELDOUT aggregate, and improves three
    # distinct families (linear, oscillatory, nonlinear) at the per-problem level.
    cand = _sv(
        search=0.005,
        heldout=0.050,
        per_problem=_per_problem(0.020, dahlquist=0.005, damped_osc=0.005, pendulum=0.005),
    )
    tier = assign_tier(cand, inc)
    assert tier != "heldout_verified"
    assert tier == "search_only"


def test_K1_search_only_requires_strictly_better_search_error():
    inc = _sv(search=0.010, heldout=0.020)
    cand = _sv(search=0.010, heldout=0.050,
               per_problem=_per_problem(0.020, dahlquist=0.005, damped_osc=0.005))
    # Equal search_error is not "beats"; falls through to unreplicated.
    assert assign_tier(cand, inc) == "unreplicated"


def test_K2_single_family_winner_worse_on_both_aggregates_is_unreplicated():
    inc = _sv(search=0.010, heldout=0.020, per_problem=_per_problem(0.010))
    cand = _sv(search=0.020, heldout=0.050,
               per_problem=_per_problem(0.020, dahlquist=0.001))
    assert assign_tier(cand, inc) == "unreplicated"


def test_K2_two_problems_of_the_same_family_count_as_one_family():
    inc = _sv(search=0.010, heldout=0.020, per_problem=_per_problem(0.010))
    # dahlquist and dc_motor are both "linear": still one family.
    cand = _sv(search=0.005, heldout=0.010,
               per_problem=_per_problem(0.020, dahlquist=0.001, dc_motor=0.001))
    assert assign_tier(cand, inc) == "unreplicated"


def test_B31_better_on_both_aggregates_and_two_families_is_heldout_verified():
    inc = _sv(search=0.010, heldout=0.020, per_problem=_per_problem(0.010))
    cand = _sv(search=0.005, heldout=0.010,
               per_problem=_per_problem(0.020, dahlquist=0.001, rc_thermal=0.001))
    assert assign_tier(cand, inc) == "heldout_verified"


def test_B31_better_on_both_aggregates_but_one_family_is_unreplicated():
    inc = _sv(search=0.010, heldout=0.020, per_problem=_per_problem(0.010))
    cand = _sv(search=0.005, heldout=0.010,
               per_problem=_per_problem(0.020, quaternion=0.001))
    assert assign_tier(cand, inc) == "unreplicated"


def test_B31_no_incumbent_is_unreplicated():
    cand = _sv(search=0.000001, heldout=0.000001, per_problem=_per_problem(0.0))
    assert assign_tier(cand, None) == "unreplicated"


def test_B31_better_on_heldout_only_is_unreplicated_not_search_only():
    inc = _sv(search=0.010, heldout=0.020, per_problem=_per_problem(0.010))
    cand = _sv(search=0.020, heldout=0.010,
               per_problem=_per_problem(0.001))
    assert assign_tier(cand, inc) == "unreplicated"


def test_B31_identical_scores_are_unreplicated():
    inc = _sv(search=0.010, heldout=0.020, per_problem=_per_problem(0.010))
    cand = _sv(search=0.010, heldout=0.020, per_problem=_per_problem(0.010))
    assert assign_tier(cand, inc) == "unreplicated"


def test_B31_family_count_only_looks_at_plain_problem_keys():
    inc = _sv(search=0.010, heldout=0.020, per_problem=_per_problem(0.010))
    # Only the "slow:" / "avr_approx:" keys improve; the plain problem keys do not.
    pp = _per_problem(0.020)
    for n in PROBLEM_NAMES:
        pp[f"slow:{n}"] = 0.001
        pp[f"avr_approx:{n}"] = 0.001
    cand = _sv(search=0.005, heldout=0.010, per_problem=pp)
    assert assign_tier(cand, inc) == "unreplicated"


def test_B31_missing_per_problem_keys_are_ignored_not_an_error():
    inc = _sv(search=0.010, heldout=0.020, per_problem={"dahlquist": 0.01, "rc_thermal": 0.01})
    cand = _sv(search=0.005, heldout=0.010, per_problem={"dahlquist": 0.001, "rc_thermal": 0.001})
    assert assign_tier(cand, inc) == "heldout_verified"


def test_B31_worse_on_everything_is_unreplicated():
    inc = _sv(search=0.010, heldout=0.020, per_problem=_per_problem(0.010))
    cand = _sv(search=0.100, heldout=0.200, per_problem=_per_problem(0.100))
    assert assign_tier(cand, inc) == "unreplicated"


def test_B31_beats_search_with_equal_heldout_is_search_only():
    inc = _sv(search=0.010, heldout=0.020, per_problem=_per_problem(0.010))
    cand = _sv(search=0.005, heldout=0.020,
               per_problem=_per_problem(0.001))
    # heldout is not strictly better, so heldout_verified is impossible; search_only applies.
    assert assign_tier(cand, inc) == "search_only"


def test_B31_result_is_always_one_of_TIERS():
    inc = _sv()
    for cand in (_sv(0.001, 0.001), _sv(0.5, 0.5), _sv(0.001, 0.5), _sv(0.5, 0.001)):
        assert assign_tier(cand, inc) in TIERS
    assert assign_tier(_sv(), None) in TIERS


# ===========================================================================
# Archive: cycle_bucket  (B32)
# ===========================================================================

@pytest.mark.parametrize("cycles,bucket", [
    (15, 0), (16, 1), (31, 1), (32, 2), (63, 2), (64, 3), (127, 3), (128, 4),
    (255, 4), (256, 5), (511, 5), (512, 6), (1023, 6), (1024, 7), (5000, 7),
])
def test_B32_cycle_bucket_boundaries(cycles, bucket):
    assert cycle_bucket(cycles) == bucket


def test_B32_cycle_bucket_small_values_are_bucket_zero():
    assert cycle_bucket(0) == 0
    assert cycle_bucket(1) == 0
    assert cycle_bucket(5) == 0
    assert cycle_bucket(11) == 0


def test_B32_cycle_bucket_range_is_0_to_7():
    for c in range(0, 3000, 7):
        assert 0 <= cycle_bucket(c) <= 7


# ===========================================================================
# Archive: record JSON schema  (K7, B33)
# ===========================================================================

def test_K7_RECORD_KEYS_is_the_frozen_tuple():
    assert RECORD_KEYS == (
        "tableau_hash", "tableau", "score", "tier", "cycle_id", "seed",
        "verifier_hash", "directive_id", "hypothesis_id", "timestamp",
    )


def test_K7_record_to_json_has_exactly_RECORD_KEYS():
    d = record_to_json(_record(_rk4(), _sv_for(_rk4())))
    assert set(d.keys()) == set(RECORD_KEYS)


def test_K7_model_supplied_tier_is_rejected():
    d = record_to_json(_record(_rk4(), _sv_for(_rk4())))
    d["tier"] = "verified_by_model"
    with pytest.raises(RecordSchemaError):
        record_from_json(d)


def test_K7_unknown_extra_key_is_rejected():
    d = record_to_json(_record(_rk4(), _sv_for(_rk4())))
    d["note"] = "looks great"
    with pytest.raises(RecordSchemaError):
        record_from_json(d)


def test_K7_mismatching_tableau_hash_is_rejected():
    d = record_to_json(_record(_rk4(), _sv_for(_rk4())))
    d["tableau_hash"] = "0" * 64
    with pytest.raises(RecordSchemaError):
        record_from_json(d)


def test_K7_hash_of_a_different_tableau_is_rejected():
    d = record_to_json(_record(_rk4(), _sv_for(_rk4())))
    d["tableau_hash"] = content_hash(_rk38())
    with pytest.raises(RecordSchemaError):
        record_from_json(d)


def test_K7_missing_key_is_rejected():
    d = record_to_json(_record(_rk4(), _sv_for(_rk4())))
    del d["seed"]
    with pytest.raises(RecordSchemaError):
        record_from_json(d)


def test_K7_empty_dict_is_rejected():
    with pytest.raises(RecordSchemaError):
        record_from_json({})


def test_K7_tier_None_is_rejected():
    d = record_to_json(_record(_rk4(), _sv_for(_rk4())))
    d["tier"] = None
    with pytest.raises(RecordSchemaError):
        record_from_json(d)


def test_K7_empty_tier_string_is_rejected():
    d = record_to_json(_record(_rk4(), _sv_for(_rk4())))
    d["tier"] = ""
    with pytest.raises(RecordSchemaError):
        record_from_json(d)


def test_K7_second_unknown_key_named_like_a_tier_is_rejected():
    d = record_to_json(_record(_rk4(), _sv_for(_rk4())))
    d["model_tier"] = "heldout_verified"
    with pytest.raises(RecordSchemaError):
        record_from_json(d)


def test_K7_uppercase_hash_does_not_match():
    d = record_to_json(_record(_rk4(), _sv_for(_rk4())))
    d["tableau_hash"] = d["tableau_hash"].upper()
    with pytest.raises(RecordSchemaError):
        record_from_json(d)


@pytest.mark.parametrize("tier", ["heldout_verified", "search_only", "unreplicated"])
def test_K7_every_legal_tier_round_trips(tier):
    r = _record(_rk4(), _sv_for(_rk4()), tier=tier)
    assert record_from_json(record_to_json(r)) == r


def test_B33_hand_built_record_round_trips_through_json_text():
    r = _record(_rk4(), _sv_for(_rk4()), cycle_id=5, seed=3,
                directive_id="D-0001", hypothesis_id="H-1")
    text = json.dumps(record_to_json(r))
    assert record_from_json(json.loads(text)) == r


def test_B33_record_with_measured_order_None_round_trips():
    sv = _sv(measured_order=None)
    r = _record(_heun2(), sv)
    back = record_from_json(json.loads(json.dumps(record_to_json(r))))
    assert back == r
    assert back.score.measured_order is None


@pytest.mark.slow
def test_B33_evaluate_derived_record_round_trips():
    t = _rk4()
    sv = evaluate(t, 65536)
    r = _record(t, sv, cycle_id=2, seed=7, directive_id="D-E000001")
    assert record_from_json(json.loads(json.dumps(record_to_json(r)))) == r


# ===========================================================================
# Archive: files, read_all, replay  (B34, B35, B36, B37)
# ===========================================================================

def _three_records():
    return [
        _record(_rk4(), _sv_for(_rk4(), 0.010, 0.020), cycle_id=1),
        _record(_rk38(), _sv_for(_rk38(), 0.011, 0.021), cycle_id=2),
        _record(_heun2(), _sv_for(_heun2(), 0.050, 0.060), cycle_id=3),
    ]


def test_B34_truncated_trailing_line_is_discarded_and_replay_survives(monkeypatch, tmp_path):
    _work(monkeypatch, tmp_path)
    for r in _three_records():
        append(r)
    path = today_path()
    assert path.is_file()
    data = path.read_bytes().rstrip(b"\r\n")
    idx = data.rfind(b"\n")
    head, last = data[: idx + 1], data[idx + 1:]
    assert len(last) > 10
    path.write_bytes(head + last[: len(last) // 2])

    recs = read_all()
    assert len(recs) == 2
    assert [r.tableau_hash for r in recs] == [content_hash(_rk4()), content_hash(_rk38())]
    arch = replay()
    assert arch.n_records == 2
    assert arch.last_cycle_id == 2


def test_B34_corrupt_middle_line_is_discarded(monkeypatch, tmp_path):
    _work(monkeypatch, tmp_path)
    for r in _three_records():
        append(r)
    path = today_path()
    lines = path.read_bytes().rstrip(b"\r\n").split(b"\n")
    assert len(lines) == 3
    lines[1] = b'{"tableau_hash": "garbage'
    path.write_bytes(b"\n".join(lines) + b"\n")
    recs = read_all()
    assert len(recs) == 2
    assert {r.tableau_hash for r in recs} == {content_hash(_rk4()), content_hash(_heun2())}


def test_B34_append_writes_one_json_line_per_record(monkeypatch, tmp_path):
    _work(monkeypatch, tmp_path)
    for r in _three_records():
        append(r)
    text = today_path().read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(lines) == 3
    for ln in lines:
        assert set(json.loads(ln).keys()) == set(RECORD_KEYS)


def test_B34_fully_truncated_file_yields_no_records(monkeypatch, tmp_path):
    _work(monkeypatch, tmp_path)
    for r in _three_records():
        append(r)
    today_path().write_bytes(b"")
    assert read_all() == []
    arch = replay()
    assert arch.n_records == 0
    assert arch.grids == {1: {}, 2: {}, 3: {}, 4: {}}


def test_B34_file_of_garbage_lines_yields_no_records(monkeypatch, tmp_path):
    _work(monkeypatch, tmp_path)
    archive_dir().mkdir(parents=True)
    (archive_dir() / "2026-09-20.jsonl").write_text("not json\n{\n[1,2\n", encoding="utf-8")
    assert read_all() == []
    assert replay().n_records == 0


def test_B34_non_jsonl_files_in_archive_dir_are_ignored(monkeypatch, tmp_path):
    _work(monkeypatch, tmp_path)
    append(_three_records()[0])
    (archive_dir() / "README.txt").write_text("this is not a record\n", encoding="utf-8")
    (archive_dir() / "notes.json").write_text("{}\n", encoding="utf-8")
    recs = read_all()
    assert len(recs) == 1
    assert recs[0].tableau_hash == content_hash(_rk4())


def test_B34_files_are_read_in_name_order(monkeypatch, tmp_path):
    monkeypatch.setenv("RK_WORK_DIR", str(tmp_path))
    monkeypatch.setenv("RK_CLOCK", "2026-09-22T10:00:00Z")
    append(_record(_rk38(), _sv_for(_rk38()), cycle_id=2))
    monkeypatch.setenv("RK_CLOCK", "2026-09-21T10:00:00Z")
    append(_record(_rk4(), _sv_for(_rk4()), cycle_id=1))
    assert [r.cycle_id for r in read_all()] == [1, 2]
    assert replay().last_cycle_id == 2


def test_B35_read_all_and_elites_on_absent_archive_dir(monkeypatch, tmp_path):
    _work(monkeypatch, tmp_path)
    assert read_all() == []
    for order in (1, 2, 3, 4):
        assert elites(order) == {}


def test_B35_replay_on_absent_archive_dir(monkeypatch, tmp_path):
    _work(monkeypatch, tmp_path)
    assert not archive_dir().exists()
    arch = replay()
    assert arch.n_records == 0
    assert arch.last_cycle_id == 0
    assert arch.grids == {1: {}, 2: {}, 3: {}, 4: {}}
    assert arch.open_hypotheses == ()
    assert arch.refuted_hypotheses == ()


def test_B35_replay_on_empty_archive_dir(monkeypatch, tmp_path):
    _work(monkeypatch, tmp_path)
    archive_dir().mkdir(parents=True)
    arch = replay()
    assert arch.n_records == 0
    assert arch.last_cycle_id == 0
    assert arch.grids == {1: {}, 2: {}, 3: {}, 4: {}}
    assert read_all() == []


def test_B35_replay_on_empty_jsonl_file(monkeypatch, tmp_path):
    _work(monkeypatch, tmp_path)
    archive_dir().mkdir(parents=True)
    (archive_dir() / "2026-09-20.jsonl").write_text("", encoding="utf-8")
    arch = replay()
    assert arch.n_records == 0
    assert arch.grids == {1: {}, 2: {}, 3: {}, 4: {}}


def test_B36_rk4_and_rk38_share_cell_4_2_and_lower_heldout_wins(monkeypatch, tmp_path):
    _work(monkeypatch, tmp_path)
    r_rk4 = _record(_rk4(), _sv_for(_rk4(), 0.010, 0.020), cycle_id=3)
    r_rk38 = _record(_rk38(), _sv_for(_rk38(), 0.012, 0.015), cycle_id=4)
    append(r_rk4)
    append(r_rk38)
    arch = replay()
    assert arch.n_records == 2
    assert arch.last_cycle_id == 4
    assert set(arch.grids.keys()) == {1, 2, 3, 4}
    assert list(arch.grids[4].keys()) == [(4, 2)]
    assert arch.grids[4][(4, 2)].tableau_hash == content_hash(_rk38())
    assert arch.grids[2] == {}
    assert arch.grids[1] == {}
    assert arch.grids[3] == {}
    cs = arch.cell_stats[(4, 4)]["fast.heldout"]
    assert cs.n == 2
    assert cs.min == 0.015
    assert abs(cs.mean - 0.0175) < 1e-12
    cyc = arch.cell_stats[(4, 4)]["fast.cycles"]
    assert cyc.n == 2
    assert cyc.min == 33


def test_B36_elites_matches_replay_grid(monkeypatch, tmp_path):
    _work(monkeypatch, tmp_path)
    append(_record(_rk4(), _sv_for(_rk4(), 0.010, 0.020), cycle_id=3))
    append(_record(_rk38(), _sv_for(_rk38(), 0.012, 0.015), cycle_id=4))
    e4 = elites(4)
    assert set(e4.keys()) == {(4, 2)}
    assert e4[(4, 2)].tableau_hash == content_hash(_rk38())
    assert elites(2) == {}
    assert elites(1) == {}


def test_B36_tie_on_heldout_error_keeps_the_earlier_record(monkeypatch, tmp_path):
    _work(monkeypatch, tmp_path)
    append(_record(_rk4(), _sv_for(_rk4(), 0.010, 0.020), cycle_id=3))
    append(_record(_rk38(), _sv_for(_rk38(), 0.010, 0.020), cycle_id=4))
    arch = replay()
    assert arch.grids[4][(4, 2)].tableau_hash == content_hash(_rk4())


def test_B36_records_of_different_orders_land_in_different_grids(monkeypatch, tmp_path):
    _work(monkeypatch, tmp_path)
    append(_record(_heun2(), _sv_for(_heun2(), 0.05, 0.06), cycle_id=1))
    append(_record(_kutta3(), _sv_for(_kutta3(), 0.02, 0.03), cycle_id=2))
    append(_record(_rk4(), _sv_for(_rk4(), 0.01, 0.02), cycle_id=3))
    arch = replay()
    assert arch.n_records == 3
    assert list(arch.grids[2].keys()) == [(2, 0)]        # heun2: 13 fast cycles -> bucket 0
    assert list(arch.grids[3].keys()) == [(3, 1)]        # kutta3: 26 fast cycles -> bucket 1
    assert list(arch.grids[4].keys()) == [(4, 2)]        # rk4: 33 fast cycles -> bucket 2
    assert arch.grids[1] == {}


def test_B36_record_order_is_symbolic_order_capped_at_4():
    assert record_order(_record(_rk4(), _sv_for(_rk4()))) == 4
    assert record_order(_record(_rk38(), _sv_for(_rk38()))) == 4
    assert record_order(_record(_kutta3(), _sv_for(_kutta3()))) == 3
    assert record_order(_record(_heun2(), _sv_for(_heun2()))) == 2
    assert record_order(_record(_euler(), _sv_for(_euler()))) == 1


def test_B36_metric_value_resolves_model_and_metric():
    sv = _sv(search=0.011, heldout=0.022,
             per_problem=_per_problem(0.01, **{"slow:heldout_error": 0.033, "slow:search_error": 0.044,
                                               "avr_approx:heldout_error": 0.055, "avr_approx:search_error": 0.066}))
    assert metric_value(sv, "fast", "heldout") == 0.022
    assert metric_value(sv, "fast", "search") == 0.011
    assert metric_value(sv, "slow", "heldout") == 0.033
    assert metric_value(sv, "slow", "search") == 0.044
    assert metric_value(sv, "avr_approx", "heldout") == 0.055
    assert metric_value(sv, "avr_approx", "search") == 0.066
    assert metric_value(sv, "fast", "cycles") == 33
    assert metric_value(sv, "slow", "cycles") == 85
    assert metric_value(sv, "avr_approx", "cycles") == 150
    assert metric_value(sv, "fast", "order") == 4.07
    assert metric_value(_sv(measured_order=None), "fast", "order") is None


def test_B36_update_cell_stat_is_welford():
    cs = update_cell_stat(None, 2.0)
    assert cs.n == 1
    assert cs.mean == 2.0
    assert cs.m2 == 0.0
    assert cs.min == 2.0
    cs = update_cell_stat(cs, 4.0)
    assert cs.n == 2
    assert abs(cs.mean - 3.0) < 1e-12
    assert abs(cs.m2 - 2.0) < 1e-12
    assert cs.min == 2.0
    cs = update_cell_stat(cs, 1.0)
    assert cs.n == 3
    assert abs(cs.mean - 7.0 / 3.0) < 1e-12
    assert cs.min == 1.0


def test_B37_today_path_follows_RK_CLOCK(monkeypatch, tmp_path):
    _work(monkeypatch, tmp_path, clock="2026-09-21T10:00:00Z")
    p = today_path()
    assert str(p).replace("\\", "/").endswith("2026-09-21.jsonl")
    assert p.name == "2026-09-21.jsonl"
    assert p.parent == archive_dir()


def test_B37_today_path_changes_with_RK_CLOCK(monkeypatch, tmp_path):
    _work(monkeypatch, tmp_path, clock="2026-10-05T00:30:00Z")
    assert today_path().name == "2026-10-05.jsonl"


def test_B37_today_path_without_RK_CLOCK_uses_utc_today(monkeypatch, tmp_path):
    monkeypatch.setenv("RK_WORK_DIR", str(tmp_path))
    monkeypatch.delenv("RK_CLOCK", raising=False)
    before = datetime.datetime.now(UTC).date().isoformat()
    name = today_path().name
    after = datetime.datetime.now(UTC).date().isoformat()
    assert name in (f"{before}.jsonl", f"{after}.jsonl")


# ===========================================================================
# Surrogate  (B38, B39, B40)
# ===========================================================================

def test_B38_should_train_threshold():
    assert should_train(4999) is False
    assert should_train(5000) is True
    assert should_train(0) is False
    assert should_train(10_000) is True


def test_B39_features_of_rk4():
    f = features(_rk4())
    assert len(f) == 12
    assert f[0] == 4          # stages
    assert f[1] == 34         # csd_weight_total
    assert f[3] == 33         # fast cycles, n=1
    assert f[4] == 85         # slow cycles, n=1
    assert f[5] == 1.0        # sum(b)
    assert f[11] == 4         # achieved_order_symbolic


def test_B39_features_of_rk4_remaining_entries():
    f = features(_rk4())
    assert abs(f[2] - 5.086e-06) < 1e-8                 # coeff_quant_error
    assert f[6] == 1.0                                   # max |A|
    assert f[7] == 3                                     # zeros in strictly lower A
    assert f[8] == 3                                     # entries (lower A + b) with csd_weight == 1
    assert abs(f[9] - (-2.785294)) < 1e-3                # stability_real
    assert abs(f[10] - 2.828427) < 1e-3                  # stability_imag
    assert all(isinstance(x, float) for x in f)


def test_B39_features_of_heun2_differ_from_rk4():
    f = features(_heun2())
    assert len(f) == 12
    assert f[0] == 2
    assert f[1] == 2
    assert f[3] == 13
    assert f[4] == 13
    assert f[5] == 1.0
    assert f[11] == 2
    assert f != features(_rk4())


def _synthetic_records(n: int = 30, seed: int = 0) -> list[Record]:
    rng = random.Random(seed)
    tabs = [_rk4(), _rk38(), _heun2(), _kutta3()]
    base = {0: 0.010, 1: 0.011, 2: 0.060, 3: 0.025}
    out = []
    for i in range(n):
        k = i % 4
        t = tabs[k]
        h = base[k] * (1.0 + 0.2 * rng.random())
        s = 0.8 * h * (1.0 + 0.2 * rng.random())
        out.append(_record(t, _sv_for(t, s, h), cycle_id=i + 1, seed=i))
    return out


def test_B40_train_predict_calibration_on_30_synthetic_records():
    recs = _synthetic_records(30)
    m = train(recs)
    assert m is not None
    p = predict(m, _rk4())
    assert isinstance(p, float)
    assert math.isfinite(p)
    ce = calibration_error(m, recs)
    assert math.isfinite(ce)
    assert ce >= 0.0


def test_B40_train_ignores_non_finite_heldout_records():
    recs = _synthetic_records(30)
    recs.append(_record(_rk4(), _sv_for(_rk4(), 0.01, float("inf")), cycle_id=99))
    recs.append(_record(_rk38(), _sv_for(_rk38(), 0.01, float("nan")), cycle_id=100))
    m = train(recs)
    assert m is not None
    assert math.isfinite(predict(m, _rk38()))


def test_B40_calibration_error_on_empty_holdout_is_zero():
    m = train(_synthetic_records(30))
    assert calibration_error(m, []) == 0.0


def test_B40_calibration_error_is_mean_absolute_error():
    m = train(_synthetic_records(30))
    r1 = _record(_rk4(), _sv_for(_rk4(), 0.01, 0.02))
    r2 = _record(_heun2(), _sv_for(_heun2(), 0.05, 0.06))
    expected = (abs(predict(m, _rk4()) - 0.02) + abs(predict(m, _heun2()) - 0.06)) / 2.0
    assert abs(calibration_error(m, [r1, r2]) - expected) < 1e-12


# ===========================================================================
# Encourager  (E5, E6, E7, B41, B42)
# ===========================================================================

def test_E5_never_PACKAGE_or_FREEZE_before_2026_11_20():
    rng = random.Random(0)
    arch = ArchiveState(0, 0, {1: {}, 2: {}, 3: {}, 4: {}}, (), ())
    start = datetime.date(2026, 9, 21)
    seen_kinds = set()
    for _ in range(1000):
        cell = None if rng.random() < 0.3 else (rng.randint(2, 6), rng.randint(0, 7))
        st = RunState(
            cycle_id=rng.randint(0, 500),
            phase=rng.randint(0, 3),
            started_at="2026-09-21T00:00:00Z",
            last_heartbeat="2026-09-21T00:00:00Z",
            spend_usd=rng.uniform(0.0, 50.0),
            stall_counter=rng.randint(0, 100),
            current_cell=cell,
        )
        day = start + datetime.timedelta(days=rng.randint(0, 59))   # 2026-09-21 .. 2026-11-19
        now = datetime.datetime(day.year, day.month, day.day, rng.randint(0, 23), rng.randint(0, 59), tzinfo=UTC)
        act = next_action(st, arch, now)
        assert isinstance(act, Action)
        assert act.kind not in ("PACKAGE", "FREEZE"), (st, now, act)
        assert isinstance(act.payload, dict)
        seen_kinds.add(act.kind)
    assert seen_kinds  # sanity: the loop actually ran


def test_E5_day_before_package_date_is_not_PACKAGE():
    act = next_action(_state(stall=0), _empty_arch(), datetime.datetime(2026, 11, 19, 23, 59, 59, tzinfo=UTC))
    assert act.kind not in ("PACKAGE", "FREEZE")


def test_E5_run_start_with_huge_stall_is_not_PACKAGE_or_FREEZE():
    act = next_action(_state(stall=100, phase=3), _empty_arch(), datetime.datetime(2026, 9, 21, 0, 0, 0, tzinfo=UTC))
    assert act.kind not in ("PACKAGE", "FREEZE")
    assert act.kind == "HYPOTHESIZE"


def test_E6_clock_at_2026_11_21_returns_PACKAGE():
    assert next_action(_state(), _empty_arch(), _dt(2026, 11, 21)).kind == "PACKAGE"


def test_E6_PACKAGE_on_the_package_date_itself():
    assert next_action(_state(), _empty_arch(), _dt(2026, 11, 20, h=0)).kind == "PACKAGE"


def test_E6_PACKAGE_wins_over_the_stall_ladder():
    assert next_action(_state(stall=35, phase=2), _empty_arch(), _dt(2026, 11, 25)).kind == "PACKAGE"


def test_E7_clock_at_2026_12_06_returns_FREEZE():
    assert next_action(_state(), _empty_arch(), _dt(2026, 12, 6)).kind == "FREEZE"


def test_E7_FREEZE_on_the_freeze_date_itself_and_after():
    assert next_action(_state(), _empty_arch(), _dt(2026, 12, 5, h=0)).kind == "FREEZE"
    assert next_action(_state(stall=50), _empty_arch(), _dt(2027, 3, 1)).kind == "FREEZE"


def test_E7_calendar_constants():
    assert PACKAGE_DATE == datetime.date(2026, 11, 20)
    assert FREEZE_DATE == datetime.date(2026, 12, 5)


def test_B41_stall_0_is_SEARCH_CELL():
    act = next_action(_state(stall=0, phase=0), _empty_arch(), _dt(2026, 10, 1))
    assert act.kind == "SEARCH_CELL"
    assert act.payload["order"] == 2
    assert tuple(act.payload["cell"]) == (2, 0)


def test_B41_low_stall_keeps_current_cell():
    act = next_action(_state(stall=2, phase=1, cell=(3, 4)), _empty_arch(), _dt(2026, 10, 1))
    assert act.kind == "SEARCH_CELL"
    assert act.payload["order"] == 3
    assert tuple(act.payload["cell"]) == (3, 4)


def test_B41_stall_5_to_9_moves_to_emptiest_cell():
    act = next_action(_state(stall=7, phase=1, cell=(3, 4)), _empty_arch(), _dt(2026, 10, 1))
    assert act.kind == "SEARCH_CELL"
    assert tuple(act.payload["cell"]) == (2, 0)


def test_B41_stall_12_is_WIDEN():
    act = next_action(_state(stall=12, phase=0), _empty_arch(), _dt(2026, 10, 1))
    assert act.kind == "WIDEN"
    assert act.payload["order"] == 2
    assert act.payload["dyadic_denominator_max"] == 32768
    assert "stages" in act.payload


def test_B41_stall_22_is_HYPOTHESIZE():
    act = next_action(_state(stall=22, phase=2), _empty_arch(), _dt(2026, 10, 1))
    assert act.kind == "HYPOTHESIZE"
    assert act.payload["order"] == 4


def test_B41_stall_35_phase_2_is_ADVANCE_PHASE():
    act = next_action(_state(stall=35, phase=2), _empty_arch(), _dt(2026, 10, 1))
    assert act.kind == "ADVANCE_PHASE"
    assert act.payload["from"] == 2
    assert act.payload["to"] == 3


def test_B41_stall_35_phase_3_is_HYPOTHESIZE():
    act = next_action(_state(stall=35, phase=3), _empty_arch(), _dt(2026, 10, 1))
    assert act.kind == "HYPOTHESIZE"


def test_B41_order_by_phase():
    for phase, order in ((0, 2), (1, 3), (2, 4), (3, 4)):
        act = next_action(_state(stall=0, phase=phase), _empty_arch(), _dt(2026, 10, 1))
        assert act.kind == "SEARCH_CELL"
        assert act.payload["order"] == order


def test_B41_widening_gap_at_stall_10_is_ROTATE_PROBLEMS():
    r = _record(_heun2(), _sv_for(_heun2(), 0.01, 0.10))   # gap 0.09 > 2 * 0.01
    arch = _arch_with({2: {(2, 0): r}})
    act = next_action(_state(stall=10, phase=0), arch, _dt(2026, 10, 1))
    assert act.kind == "ROTATE_PROBLEMS"
    assert abs(act.payload["gap"] - 0.09) < 1e-9


def test_B41_widening_gap_at_stall_11_is_not_ROTATE_PROBLEMS():
    r = _record(_heun2(), _sv_for(_heun2(), 0.01, 0.10))
    arch = _arch_with({2: {(2, 0): r}})
    act = next_action(_state(stall=11, phase=0), arch, _dt(2026, 10, 1))
    assert act.kind == "WIDEN"


def test_B41_no_gap_at_stall_10_is_WIDEN():
    r = _record(_heun2(), _sv_for(_heun2(), 0.01, 0.011))  # gap 0.001 < 2 * 0.01
    arch = _arch_with({2: {(2, 0): r}})
    act = next_action(_state(stall=10, phase=0), arch, _dt(2026, 10, 1))
    assert act.kind == "WIDEN"


def test_B42_emptiest_cell_on_empty_archive_is_2_0():
    arch = _empty_arch()
    for order in (1, 2, 3, 4):
        assert tuple(emptiest_cell(arch, order)) == (2, 0)


def test_B42_emptiest_cell_skips_occupied_cells_lowest_stages_then_bucket():
    r = _record(_heun2(), _sv_for(_heun2()))
    arch = _arch_with({2: {(2, 0): r, (2, 1): r}})
    assert tuple(emptiest_cell(arch, 2)) == (2, 2)
    # another order's grid is untouched
    assert tuple(emptiest_cell(arch, 3)) == (2, 0)


def test_B42_emptiest_cell_moves_to_next_stage_count_when_a_row_is_full():
    r = _record(_heun2(), _sv_for(_heun2()))
    arch = _arch_with({2: {(2, b): r for b in range(8)}})
    assert tuple(emptiest_cell(arch, 2)) == (3, 0)


def test_B42_full_grid_returns_cell_with_highest_heldout_error():
    grid = {}
    for s in range(2, 7):
        for b in range(8):
            h = 0.01 + 0.001 * (s * 8 + b)
            grid[(s, b)] = _record(_rk4(), _sv_for(_rk4(), 0.005, h), cycle_id=s * 8 + b + 1)
    grid[(4, 5)] = _record(_rk4(), _sv_for(_rk4(), 0.005, 0.9), cycle_id=999)
    arch = _arch_with({4: grid})
    assert tuple(emptiest_cell(arch, 4)) == (4, 5)


def test_B42_heldout_gap_is_zero_on_empty_archive():
    assert heldout_gap(_empty_arch()) == 0.0


def test_B42_heldout_gap_is_zero_when_every_elite_is_non_finite():
    r = _record(_rk4(), _sv_for(_rk4(), 0.01, float("inf")))
    arch = _arch_with({4: {(4, 2): r}})
    assert heldout_gap(arch) == 0.0


def test_B42_heldout_gap_is_mean_over_elites_ignoring_non_finite():
    r1 = _record(_heun2(), _sv_for(_heun2(), 0.01, 0.02))
    r2 = _record(_rk4(), _sv_for(_rk4(), 0.01, 0.04))
    r3 = _record(_rk38(), _sv_for(_rk38(), 0.01, float("inf")))
    arch = _arch_with({2: {(2, 0): r1}, 4: {(4, 2): r2, (4, 3): r3}})
    assert abs(heldout_gap(arch) - 0.02) < 1e-12


# ===========================================================================
# Enumeration  (G26, B43, B44)
# ===========================================================================

def test_G26_phase0_has_16_valid_points_out_of_256_candidates():
    p0 = enumerate_phase0()
    assert len(p0) == 16
    assert phase0_candidate_count() == 256
    assert PHASE0_S_MAX == 6
    assert len(lattice(6, 2)) == 256


def test_G26_cheapest_phase0_under_slow_is_midpoint_at_11_cycles():
    ch = cheapest(enumerate_phase0(), M0PLUS_SLOW)
    assert len(ch) == 16
    assert ch[0][0] == 11
    assert content_hash(ch[0][1]) == content_hash(_midpoint())
    assert [c for c, _ in ch[:5]] == [11, 13, 13, 13, 15]
    assert [c for c, _ in ch] == sorted(c for c, _ in ch)


def test_G26_five_cheapest_match_the_fixture_table():
    ch = cheapest(enumerate_phase0(), M0PLUS_SLOW)
    got = {(t.A[1][0], t.b) for _, t in ch[:5]}
    expected = {
        (Fraction(1, 2), (Fraction(0), Fraction(1))),
        (Fraction(-1, 2), (Fraction(2), Fraction(-1))),
        (Fraction(1), (Fraction(1, 2), Fraction(1, 2))),
        (Fraction(1, 4), (Fraction(-1), Fraction(2))),
        (Fraction(-1), (Fraction(3, 2), Fraction(-1, 2))),
    }
    assert got == expected


def test_G26_heun2_and_midpoint_are_phase0_points():
    hashes = {content_hash(t) for t in enumerate_phase0()}
    assert len(hashes) == 16
    assert content_hash(_midpoint()) in hashes
    assert content_hash(_heun2()) in hashes
    assert content_hash(_rk4()) not in hashes


def test_G26_lattice_is_sorted_distinct_dyadic_and_excludes_zero():
    lat = lattice(6, 2)
    assert lat == sorted(lat)
    assert len(set(lat)) == len(lat)
    assert Fraction(0) not in lat
    for x in lat:
        assert isinstance(x, Fraction)
        assert 0 < abs(x) <= 2
        assert (x * 2 ** 6).denominator == 1
    assert Fraction(2) in lat and Fraction(-2) in lat
    assert Fraction(1, 64) in lat and Fraction(-1, 64) in lat
    assert Fraction(1, 128) not in lat


def test_G26_cheapest_of_empty_list_is_empty():
    assert cheapest([], M0PLUS_SLOW) == []
    assert cheapest([], M0PLUS_FAST) == []


def test_G26_cheapest_orders_by_cycle_count_under_the_given_model():
    ch_fast = cheapest([_rk38(), _rk4(), _heun2()], M0PLUS_FAST)
    assert [c for c, _ in ch_fast] == [13, 33, 36]
    assert content_hash(ch_fast[1][1]) == content_hash(_rk4())
    ch_slow = cheapest([_rk38(), _rk4(), _heun2()], M0PLUS_SLOW)
    assert [c for c, _ in ch_slow] == [13, 64, 85]
    assert content_hash(ch_slow[1][1]) == content_hash(_rk38())


def test_G26_lattice_small_case():
    assert lattice(1, 1) == [Fraction(-1), Fraction(-1, 2), Fraction(1, 2), Fraction(1)]
    assert lattice(0, 2) == [Fraction(-2), Fraction(-1), Fraction(1), Fraction(2)]


def test_B43_every_phase0_tableau_is_order_2_and_row_sum_consistent():
    p0 = enumerate_phase0()
    assert len(p0) == 16
    for t in p0:
        assert isinstance(t, Tableau)
        assert len(t.A) == 2 and len(t.b) == 2 and len(t.c) == 2
        assert row_sums_consistent(t)
        assert _all_zero(residuals(t, 2)), t
        assert t.c[0] == 0
        assert t.A[1][0] == t.c[1]
        assert to_rep(t.b[0]).exact and to_rep(t.b[1]).exact


def test_B43_phase0_a21_values_lie_on_the_lattice():
    lat = set(lattice(6, 2))
    for t in enumerate_phase0():
        assert t.A[1][0] in lat
        assert t.b[1] == 1 / (2 * t.A[1][0])
        assert t.b[0] == 1 - t.b[1]


@pytest.mark.slow
def test_B44_phase1_enumeration_is_order_3_exact_and_contains_kutta3():
    tabs, cap = enumerate_phase1()
    assert cap is False
    assert len(tabs) > 0
    hashes = set()
    for t in tabs:
        assert len(t.A) == 3 and len(t.b) == 3 and len(t.c) == 3
        assert row_sums_consistent(t)
        assert _all_zero(residuals(t, 3)), t
        for row in t.A:
            for x in row:
                assert to_rep(x).exact, (x, t)
        hashes.add(content_hash(t))
    assert len(hashes) == len(tabs)
    assert content_hash(_kutta3()) in hashes


@pytest.mark.slow
def test_B44_phase1_excludes_heun3_whose_A_is_inexact():
    tabs, _ = enumerate_phase1()
    hashes = {content_hash(t) for t in tabs}
    assert content_hash(_heun3()) not in hashes


# ===========================================================================
# Search  (B45, B46, B47, B48)
# ===========================================================================

def test_B45_free_parameters():
    assert free_parameters(4) == 10
    assert free_parameters(2) == 3
    assert free_parameters(3) == 6
    assert free_parameters(6) == 21


def test_B45_snap_default_denominator():
    assert snap(0.333333) == Fraction(10923, 32768)
    assert snap(0.5) == Fraction(1, 2)
    assert snap(0.0) == Fraction(0)
    assert snap(-0.25) == Fraction(-1, 4)
    assert snap(1.0) == Fraction(1)
    assert snap(2.0) == Fraction(2)


def test_B45_snap_custom_denominator():
    assert snap(0.3, 16) == Fraction(5, 16)
    assert snap(0.3, 4) == Fraction(1, 4)
    assert snap(0.7, 2) == Fraction(1, 2)


def test_B45_snap_rounds_half_to_even():
    assert snap(1 / 65536) == Fraction(0)                # 0.5 -> 0
    assert snap(-1 / 65536) == Fraction(0)               # -0.5 -> 0
    assert snap(3 / 65536) == Fraction(2, 32768)         # 1.5 -> 2
    assert snap(-3 / 65536) == Fraction(-2, 32768)       # -1.5 -> -2
    assert snap(0.125, 4) == Fraction(0)                 # 0.5 -> 0
    assert snap(0.375, 4) == Fraction(1, 2)              # 1.5 -> 2 -> 2/4


def test_B45_default_constraints_shape():
    dc = default_constraints()
    assert dc == {"force_zero": [], "dyadic_denominator_max": 32768, "c_fixed": {}, "b_nonneg": False}
    assert validate_directive({**_example(), "constraints": dc}) is not None


def _constraints(**overrides) -> dict:
    """Copy of default_constraints() with overrides; never mutates the returned default."""
    base = default_constraints()
    out = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    out.update(overrides)
    return out


def _rk4_floats():
    A_free = [0.5, 0.0, 0.5, 0.0, 0.0, 1.0]           # strictly lower triangle row-major
    b_guess = [1 / 6, 1 / 3, 1 / 3, 1 / 6]
    return A_free, b_guess


def test_B46_project_recovers_rk4_exactly():
    A_free, b_guess = _rk4_floats()
    t = project(A_free, b_guess, 4, 4, default_constraints())
    rk4 = _rk4()
    assert t is not None
    assert t.A == rk4.A
    assert t.b == rk4.b
    assert t.c == rk4.c
    assert t == rk4
    assert _all_zero(residuals(t, 4))


def test_B46_project_output_is_exact_fractions():
    A_free, b_guess = _rk4_floats()
    t = project(A_free, b_guess, 4, 4, default_constraints())
    assert t is not None
    for row in t.A:
        for x in row:
            assert isinstance(x, Fraction)
    for x in t.b:
        assert isinstance(x, Fraction)
    for x in t.c:
        assert isinstance(x, Fraction)


def test_B46_project_returns_None_when_order_conditions_are_inconsistent():
    # A 2-stage explicit method cannot reach order 3 (b2/4 == 1/3 and b2/2 == 1/2 conflict).
    assert project([0.5], [0.0, 1.0], 2, 3, default_constraints()) is None


def test_B46_project_rk4_A_at_order_2_still_satisfies_order_2():
    A_free, b_guess = _rk4_floats()
    t = project(A_free, b_guess, 4, 2, default_constraints())
    assert t is not None
    assert _all_zero(residuals(t, 2))
    assert row_sums_consistent(t)


def test_B46_project_returns_None_for_order_4_with_3_stages():
    # No explicit 3-stage method has order 4; the b-system must be inconsistent.
    assert project([0.5, -1.0, 2.0], [1 / 6, 2 / 3, 1 / 6], 3, 4, default_constraints()) is None


def test_B46_project_returns_None_when_all_c_are_zero_at_order_2():
    # c = (0, 0): sum b*c == 1/2 is unsatisfiable.
    assert project([0.0], [0.5, 0.5], 2, 2, default_constraints()) is None


def test_B46_project_snaps_A_to_dyadic_denominator_max():
    c = _constraints(dyadic_denominator_max=4)
    t = project([0.3], [0.5, 0.5], 2, 1, c)
    assert t is not None
    assert t.A[1][0] == Fraction(1, 4)
    assert t.c[1] == Fraction(1, 4)
    assert _all_zero(residuals(t, 1))


def test_B47_force_zero_zeroes_the_entry():
    c = _constraints(force_zero=[[2, 0]])
    t = project([0.5, 0.25, 0.25], [0.2, 0.3, 0.5], 3, 2, c)
    assert t is not None
    assert t.A[2][0] == 0
    assert t.A[2][1] == Fraction(1, 4)
    assert t.c[2] == Fraction(1, 4)
    assert row_sums_consistent(t)
    assert _all_zero(residuals(t, 2))


def test_B47_c_fixed_pins_the_row_sum():
    c = _constraints(c_fixed={"1": "1/2"})
    t = project([0.3, 0.25, 0.25], [0.2, 0.3, 0.5], 3, 2, c)
    assert t is not None
    assert t.c[1] == Fraction(1, 2)
    assert t.A[1][0] == Fraction(1, 2)
    assert row_sums_consistent(t)
    assert _all_zero(residuals(t, 2))


def test_B47_c_fixed_on_row_2_adjusts_only_the_last_entry_of_the_row():
    c = _constraints(c_fixed={"2": "3/4"})
    t = project([0.5, 0.25, 0.25], [0.2, 0.3, 0.5], 3, 2, c)
    assert t is not None
    assert t.A[2][0] == Fraction(1, 4)
    assert t.A[2][1] == Fraction(1, 2)
    assert t.c[2] == Fraction(3, 4)
    assert _all_zero(residuals(t, 2))


@pytest.mark.slow
def test_B48_cmaes_island_yields_order_2_tableaus_deterministically():
    first = list(cmaes_island(2, 2, seed=1, constraints=default_constraints(), budget=40))
    assert len(first) >= 1
    for t in first:
        assert isinstance(t, Tableau)
        assert len(t.A) == 2
        assert _all_zero(residuals(t, 2)), t
        assert row_sums_consistent(t)
    hashes1 = [content_hash(t) for t in first]
    assert len(set(hashes1)) == len(hashes1)
    second = list(cmaes_island(2, 2, seed=1, constraints=default_constraints(), budget=40))
    hashes2 = [content_hash(t) for t in second]
    assert hashes1 == hashes2


# ===========================================================================
# Directive  (K9, B49, B50, B51)
# ===========================================================================

def _example() -> dict:
    """HANDOFF section 5 example directive (fresh copy each call)."""
    return {
        "directive_id": "D-0112",
        "hypothesis_id": "H-047",
        "target_order": 3,
        "stages": [3, 4],
        "constraints": {
            "force_zero": [[2, 0]],
            "dyadic_denominator_max": 16,
            "c_fixed": {"1": "1/2"},
            "b_nonneg": True,
        },
        "islands": 4,
        "budget_minutes": 45,
        "rationale": "cell (3, bucket 4) empty; forcing a[2][0]=0 removes one multiply",
    }


def test_K9_unknown_top_level_key_is_rejected():
    d = _example()
    d["priority"] = "high"
    with pytest.raises(DirectiveError):
        validate_directive(d)


def test_K9_unknown_key_inside_constraints_is_rejected():
    d = _example()
    d["constraints"]["a_max"] = 2
    with pytest.raises(DirectiveError):
        validate_directive(d)


def test_K9_unknown_key_is_rejected_even_when_value_is_null():
    d = _example()
    d["extra"] = None
    with pytest.raises(DirectiveError):
        validate_directive(d)


def test_K9_schema_forbids_additional_properties_at_every_level():
    assert isinstance(DIRECTIVE_SCHEMA, dict)
    assert DIRECTIVE_SCHEMA.get("additionalProperties") is False
    cons = DIRECTIVE_SCHEMA["properties"]["constraints"]
    assert cons.get("additionalProperties") is False


def test_B49_example_directive_validates_unchanged():
    d = _example()
    out = validate_directive(d)
    assert out == _example()
    assert isinstance(out, dict)


_BAD_DIRECTIVES = {
    "target_order_5": lambda d: d.update(target_order=5),
    "target_order_0": lambda d: d.update(target_order=0),
    "target_order_string": lambda d: d.update(target_order="3"),
    "stages_7": lambda d: d.update(stages=[7]),
    "stages_1": lambda d: d.update(stages=[1]),
    "stages_length_4": lambda d: d.update(stages=[2, 3, 4, 5]),
    "stages_empty": lambda d: d.update(stages=[]),
    "stages_not_a_list": lambda d: d.update(stages=3),
    "stages_string_entries": lambda d: d.update(stages=["3"]),
    "force_zero_upper_triangle": lambda d: d["constraints"].update(force_zero=[[0, 1]]),
    "force_zero_not_a_list": lambda d: d["constraints"].update(force_zero="2,0"),
    "force_zero_diagonal": lambda d: d["constraints"].update(force_zero=[[2, 2]]),
    "force_zero_i_beyond_max_stages": lambda d: d["constraints"].update(force_zero=[[4, 0]]),
    "force_zero_negative_j": lambda d: d["constraints"].update(force_zero=[[2, -1]]),
    "force_zero_triple": lambda d: d["constraints"].update(force_zero=[[2, 0, 0]]),
    "dyadic_3": lambda d: d["constraints"].update(dyadic_denominator_max=3),
    "dyadic_65536": lambda d: d["constraints"].update(dyadic_denominator_max=65536),
    "dyadic_1": lambda d: d["constraints"].update(dyadic_denominator_max=1),
    "dyadic_0": lambda d: d["constraints"].update(dyadic_denominator_max=0),
    "dyadic_negative": lambda d: d["constraints"].update(dyadic_denominator_max=-16),
    "dyadic_string": lambda d: d["constraints"].update(dyadic_denominator_max="16"),
    "c_fixed_not_object": lambda d: d["constraints"].update(c_fixed=["1/2"]),
    "c_fixed_key_x": lambda d: d["constraints"].update(c_fixed={"x": "1/2"}),
    "c_fixed_value_abc": lambda d: d["constraints"].update(c_fixed={"1": "abc"}),
    "c_fixed_key_0": lambda d: d["constraints"].update(c_fixed={"0": "1/2"}),
    "c_fixed_key_equals_max_stages": lambda d: d["constraints"].update(c_fixed={"4": "1/2"}),
    "b_nonneg_string": lambda d: d["constraints"].update(b_nonneg="yes"),
    "islands_9": lambda d: d.update(islands=9),
    "islands_0": lambda d: d.update(islands=0),
    "islands_string": lambda d: d.update(islands="4"),
    "budget_4": lambda d: d.update(budget_minutes=4),
    "budget_121": lambda d: d.update(budget_minutes=121),
    "budget_string": lambda d: d.update(budget_minutes="45"),
    "target_order_float_3_5": lambda d: d.update(target_order=3.5),
    "hypothesis_id_empty": lambda d: d.update(hypothesis_id=""),
    "directive_id_lowercase_prefix": lambda d: d.update(directive_id="d-0112"),
    "rationale_501": lambda d: d.update(rationale="x" * 501),
    "rationale_not_string": lambda d: d.update(rationale=12),
    "directive_id_wrong_prefix": lambda d: d.update(directive_id="X-0112"),
    "directive_id_empty_suffix": lambda d: d.update(directive_id="D-"),
    "directive_id_bad_chars": lambda d: d.update(directive_id="D-01 12"),
    "hypothesis_id_wrong_prefix": lambda d: d.update(hypothesis_id="X-1"),
    "hypothesis_id_letters": lambda d: d.update(hypothesis_id="H-abc"),
    "missing_islands": lambda d: d.pop("islands"),
    "missing_constraints": lambda d: d.pop("constraints"),
    "missing_directive_id": lambda d: d.pop("directive_id"),
    "missing_rationale": lambda d: d.pop("rationale"),
    "missing_target_order": lambda d: d.pop("target_order"),
    "missing_stages": lambda d: d.pop("stages"),
    "missing_budget_minutes": lambda d: d.pop("budget_minutes"),
    "constraints_not_object": lambda d: d.update(constraints=[]),
}


@pytest.mark.parametrize("name", sorted(_BAD_DIRECTIVES))
def test_B49_invalid_directive_raises_DirectiveError(name):
    d = _example()
    _BAD_DIRECTIVES[name](d)
    with pytest.raises(DirectiveError):
        validate_directive(d)


def test_B49_non_dict_input_raises_DirectiveError():
    with pytest.raises(DirectiveError):
        validate_directive([])            # type: ignore[arg-type]
    with pytest.raises(DirectiveError):
        validate_directive("D-0112")      # type: ignore[arg-type]


_GOOD_VARIANTS = {
    "hypothesis_id_null": lambda d: d.update(hypothesis_id=None),
    "hypothesis_id_absent": lambda d: d.pop("hypothesis_id"),
    "constraints_empty": lambda d: d.update(constraints={}),
    "rationale_exactly_500": lambda d: d.update(rationale="y" * 500),
    "rationale_empty": lambda d: d.update(rationale=""),
    "dyadic_2": lambda d: d["constraints"].update(dyadic_denominator_max=2),
    "dyadic_32768": lambda d: d["constraints"].update(dyadic_denominator_max=32768),
    "stages_length_3": lambda d: d.update(stages=[4, 5, 6]),
    "stages_2_and_6": lambda d: d.update(stages=[2, 6]),
    "islands_1": lambda d: d.update(islands=1),
    "islands_8": lambda d: d.update(islands=8),
    "budget_5": lambda d: d.update(budget_minutes=5),
    "budget_120": lambda d: d.update(budget_minutes=120),
    "force_zero_empty": lambda d: d["constraints"].update(force_zero=[]),
    "force_zero_i_3_under_max_4": lambda d: d["constraints"].update(force_zero=[[3, 0], [3, 2]]),
    "c_fixed_key_3_under_max_4": lambda d: d["constraints"].update(c_fixed={"3": "2/3"}),
    "c_fixed_integer_string_value": lambda d: d["constraints"].update(c_fixed={"1": "1"}),
    "target_order_1": lambda d: d.update(target_order=1),
    "target_order_4": lambda d: d.update(target_order=4),
    "directive_id_alnum": lambda d: d.update(directive_id="D-F00007"),
}


@pytest.mark.parametrize("name", sorted(_GOOD_VARIANTS))
def test_B49_valid_variant_is_returned_unchanged(name):
    d = _example()
    _GOOD_VARIANTS[name](d)
    snapshot = json.loads(json.dumps(d))
    assert validate_directive(d) == snapshot


def test_B50_parse_directive_extracts_the_first_json_object_from_prose():
    body = json.dumps(_example())
    text = "Here is my directive for this cycle:\n" + body + "\nGood luck, and remember to report back."
    assert parse_directive(text) == _example()


def test_B50_parse_directive_handles_nested_braces_and_a_trailing_object():
    body = json.dumps(_example(), indent=2)
    text = "prefix text " + body + ' suffix {"directive_id": "D-9999"}'
    assert parse_directive(text)["directive_id"] == "D-0112"


def test_B50_parse_directive_rejects_non_json():
    with pytest.raises(DirectiveError):
        parse_directive("not json")


def test_B50_parse_directive_rejects_empty_text():
    with pytest.raises(DirectiveError):
        parse_directive("")


def test_B50_parse_directive_rejects_valid_json_that_is_not_a_directive():
    with pytest.raises(DirectiveError):
        parse_directive('{"directive_id": "D-1"}')


def test_B50_parse_directive_rejects_json_array():
    with pytest.raises(DirectiveError):
        parse_directive("[1, 2, 3]")


def test_B50_parse_directive_rejects_object_with_unknown_key():
    d = _example()
    d["mood"] = "optimistic"
    with pytest.raises(DirectiveError):
        parse_directive(json.dumps(d))


def test_B50_parse_directive_rejects_unterminated_object():
    with pytest.raises(DirectiveError):
        parse_directive('{"directive_id": "D-0112", ')


def test_B50_parse_directive_rejects_braces_that_are_not_json():
    with pytest.raises(DirectiveError):
        parse_directive("I think {this one} is best")


def test_B50_first_object_is_the_one_validated():
    # The first {...} object is an invalid directive; a valid one follows. Spec: first wins.
    text = '{"directive_id": "D-1"} and then ' + json.dumps(_example())
    with pytest.raises(DirectiveError):
        parse_directive(text)


def test_B51_fallback_directive_phase_2_cycle_7():
    d = fallback_directive(_empty_arch(), 2, 7)
    assert validate_directive(d) == d
    assert d["stages"] == [2]
    assert d["target_order"] == 4
    assert d["directive_id"] == "D-F00007"
    assert d["hypothesis_id"] is None
    assert d["islands"] == 4
    assert d["budget_minutes"] == 5
    assert d["rationale"] == "fallback: emptiest cell"
    assert d["constraints"] == default_constraints()


def test_B51_fallback_directive_target_order_by_phase():
    for phase, order in ((0, 2), (1, 3), (2, 4), (3, 4)):
        d = fallback_directive(_empty_arch(), phase, 1)
        assert d["target_order"] == order
        assert validate_directive(d) == d


def test_B51_fallback_directive_id_is_zero_padded_and_deterministic():
    assert fallback_directive(_empty_arch(), 0, 0)["directive_id"] == "D-F00000"
    assert fallback_directive(_empty_arch(), 0, 12345)["directive_id"] == "D-F12345"
    assert fallback_directive(_empty_arch(), 3, 42) == fallback_directive(_empty_arch(), 3, 42)


def test_B51_fallback_directive_uses_emptiest_cell_stages():
    r = _record(_heun2(), _sv_for(_heun2()))
    arch = _arch_with({2: {(2, b): r for b in range(8)}})   # stage-2 row full -> (3, 0)
    d = fallback_directive(arch, 0, 3)
    assert d["stages"] == [3]
    assert validate_directive(d) == d


# ===========================================================================
# Prompts  (K8)
# ===========================================================================

def test_K8_prompts_source_contains_no_tier_strings():
    src = (PACKAGE_DIR / "prompts.py").read_text(encoding="utf-8")
    assert len(src) > 0
    for s in TIER_STRINGS:
        assert s not in src, s


def test_K8_system_prompt_contains_no_tier_strings():
    assert isinstance(SYSTEM_PROMPT, str)
    assert SYSTEM_PROMPT.strip()
    for s in TIER_STRINGS:
        assert s not in SYSTEM_PROMPT, s


def test_K8_user_prompt_for_empty_archive_mentions_refuted_but_no_tiers():
    text = build_user_prompt(_empty_arch(), _state(), [], [])
    assert isinstance(text, str)
    assert "refuted" in text
    for s in TIER_STRINGS:
        assert s not in text, s


def test_K8_user_prompt_lists_refuted_and_open_hypotheses_without_tiers():
    refuted = [{
        "id": "H-047", "cycle_proposed": 112,
        "statement": "Under M0PLUS_SLOW, best(p=3,s=4) is lower than best(p=4,s=4) at equal budget",
        "mechanism": "extra order buys less accuracy than extra multiplies cost",
        "control": "inequality should reverse under M0PLUS_FAST",
        "predicate": "slow.p3s4.heldout < slow.p4s4.heldout",
        "min_samples": 200, "verdict": "refuted", "n_samples": 250,
        "effect_size": 0.71, "resolved_cycle": 130,
    }]
    open_h = [{
        "id": "H-048", "cycle_proposed": 131,
        "statement": "fast.p2s2 cycles under 16",
        "mechanism": "m", "control": "c",
        "predicate": "fast.p2s2.cycles < 16",
        "min_samples": 50, "verdict": None, "n_samples": None,
        "effect_size": None, "resolved_cycle": None,
    }]
    r = _record(_rk4(), _sv_for(_rk4()), tier="heldout_verified")
    arch = _arch_with({4: {(4, 2): r}})
    text = build_user_prompt(arch, _state(phase=2), refuted, open_h)
    assert "H-047" in text
    assert "H-048" in text
    assert "refuted" in text
    for s in TIER_STRINGS:
        assert s not in text, s


def test_K8_prompt_includes_directive_schema_example():
    combined = SYSTEM_PROMPT + "\n" + build_user_prompt(_empty_arch(), _state(), [], [])
    assert "directive_id" in combined
    assert "target_order" in combined
    assert "JSON" in combined or "json" in combined


# ===========================================================================
# Import-graph canaries  (K12, K13)
# ===========================================================================

def _module_path(name: str) -> Path:
    return PACKAGE_DIR / f"{name}.py"


def _parse(name: str) -> ast.Module:
    p = _module_path(name)
    return ast.parse(p.read_text(encoding="utf-8"), filename=str(p))


def _rk_import_targets(tree: ast.Module) -> set[str]:
    """Names of rk_harness submodules imported by `tree` (absolute or relative)."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                parts = a.name.split(".")
                if parts[0] == "rk_harness":
                    out.add(parts[1] if len(parts) > 1 else "__init__")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level > 0:
                if mod == "":
                    out.update(a.name for a in node.names)
                else:
                    out.add(mod.split(".")[0])
            elif mod == "rk_harness":
                out.update(a.name for a in node.names)
            elif mod.startswith("rk_harness."):
                out.add(mod.split(".")[1])
    return out


def _reachable(root: str, skip: tuple[str, ...] = ()) -> list[str]:
    seen: list[str] = []
    todo = [root]
    while todo:
        name = todo.pop()
        if name in seen or name in skip or not _module_path(name).is_file():
            continue
        seen.append(name)
        for dep in sorted(_rk_import_targets(_parse(name))):
            if dep not in seen:
                todo.append(dep)
    return seen


def test_K12_search_import_graph_never_reads_HELDOUT_SET():
    assert _module_path("search").is_file()
    reach = _reachable("search")
    assert "search" in reach
    offenders = []
    for name in reach:
        tree = _parse(name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "HELDOUT_SET" and isinstance(node.ctx, ast.Load):
                offenders.append((name, "Name", node.lineno))
            elif isinstance(node, ast.Attribute) and node.attr == "HELDOUT_SET":
                offenders.append((name, "Attribute", node.lineno))
            elif isinstance(node, ast.ImportFrom) and any(a.name == "HELDOUT_SET" for a in node.names):
                offenders.append((name, "ImportFrom", node.lineno))
    assert offenders == []


def test_K12_search_import_graph_excludes_evaluator_verifier_archive_runner():
    reach = set(_reachable("search"))
    assert reach.isdisjoint({"evaluator", "verifier", "archive", "runner"}), reach


def test_K12_walker_sees_this_modules_own_imports():
    # Sanity check on the walker itself: it must follow both import styles.
    src = "from rk_harness.types import Tableau\nfrom rk_harness import evaluator\nimport rk_harness.costmodel\n"
    assert _rk_import_targets(ast.parse(src)) == {"types", "evaluator", "costmodel"}


def test_K13_no_module_but_runner_reaches_openai():
    roots = sorted(p.stem for p in PACKAGE_DIR.glob("*.py") if p.stem not in ("runner", "credentials"))
    expected = {
        "fixedpoint", "coeffrep", "tableau", "orderconditions", "costmodel", "problems",
        "simulate", "evaluator", "verifier", "archive", "surrogate", "encourager",
        "search", "enumeration", "directive", "prompts", "sitegen", "dashboard",
    }
    assert expected <= set(roots), sorted(expected - set(roots))
    offenders = []
    for root in roots:
        for name in _reachable(root, skip=("runner", "credentials")):
            src = _module_path(name).read_text(encoding="utf-8")
            if "openai" in src.lower():
                offenders.append((root, name))
    assert offenders == []


def test_K13_search_and_evaluator_graphs_are_openai_free():
    for root in ("search", "evaluator", "verifier", "archive"):
        for name in _reachable(root, skip=("runner", "credentials")):
            assert "openai" not in _module_path(name).read_text(encoding="utf-8").lower(), (root, name)
