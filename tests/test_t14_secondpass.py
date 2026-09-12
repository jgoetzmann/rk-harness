"""T14 tests: the second-pass archive analysis (rk_harness/secondpass.py).

Covers: the streaming archive scan agrees with archive.replay record for record
and cell for cell on a synthetic archive (the guard that the re-implementation of
_grids_from and _better is identical rather than close, the same guarantee
test_A80 gives archive.fold), a partial trailing line is discarded the way
read_all discards one, cycles-to-tolerance returns the smallest step count on the
sampled set and says so when it never reaches the target or never runs cleanly,
the non-monotone ladder flag is set where Q15 error rises with the step count and
survives into the finding, cycles_to_tolerance still reproduces a golden captured
before ladder_scan was split out from under it, ladder_scan reads a caller's own
ladder without bisecting it, the local Spearman tie rule matches a hand computation,
the Pareto fronts are non-dominated and sorted and honour the cap, the frontier
verdict names the stability threshold it read out of verifier.py, the results
document validates and rejects damaged documents, two builds of the same document
are byte-identical, a partial run stamps its selection, an empty archive still
builds a valid document, the command line writes the artifact, and nothing pinned
moves.

Every archive here is synthetic, written with archive.record_to_json into a
throwaway directory. The live archive is never read.
"""
from __future__ import annotations

import copy
import json
import math
import re
from fractions import Fraction

import pytest

from rk_harness import archive
from rk_harness import problems as frozen_problems
from rk_harness import secondpass as S
from rk_harness.paths import HARNESS_DIR, archive_dir
from rk_harness.problems import PROBLEMS
from rk_harness.simulate import problem_error
from rk_harness.tableau import classical, content_hash, make_tableau
from rk_harness.types import Record, ScoreVector
from rk_harness.verifier_hash import compute_verifier_hash, pinned_verifier_hash


# --------------------------------------------------------------------------- helpers

def _score(heldout: float, stab: float = -1.0, cycles: int = 20,
           search: float = 0.5) -> ScoreVector:
    return ScoreVector(
        measured_order=2.0, order_fit_points=4, error_constant=0.1,
        stability_real=stab, stability_imag=1.0, cycles={"m0plus_fast": cycles},
        csd_weight_total=3, coeff_quant_error=0.0, search_error=search,
        heldout_error=heldout, overflow_margin=1.5,
        per_problem={name: heldout * 1.25 for name in S.PROBLEM_NAMES},
    )


def _rec(t, heldout: float, stab: float = -1.0, cycles: int = 20,
         cycle_id: int = 1, date: str = "2026-09-01") -> Record:
    return Record(
        tableau_hash=content_hash(t), tableau=t, score=_score(heldout, stab, cycles),
        tier="no_improvement", cycle_id=cycle_id, seed=0, verifier_hash="testhash",
        directive_id=None, hypothesis_id=None, timestamp=f"{date}T00:00:00Z",
    )


def _write(dirpath, name: str, records, extra_line: str | None = None):
    dirpath.mkdir(parents=True, exist_ok=True)
    path = dirpath / name
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for r in records:
            fh.write(json.dumps(archive.record_to_json(r)) + "\n")
        if extra_line is not None:
            fh.write(extra_line)
    return path


# Discovered families, all disjoint from the 8 fixture tableaus.
def _order2_two_stage(k: int):
    """c2 = k/32, b = (1 - 16/k, 16/k). Order 2, never order 3. k in 17..31 keeps
    both weights inside [0, 1]."""
    c = Fraction(k, 32)
    b2 = Fraction(1, 2) / c
    return make_tableau([["0", "0"], [str(c), "0"]], [str(1 - b2), str(b2)])


def _order1_two_stage(k: int):
    """b = (1/2, 1/2) with c2 = k/32: b.c = k/64, which is 1/2 only at k = 32."""
    return make_tableau([["0", "0"], [f"{k}/32", "0"]], ["1/2", "1/2"])


def _order2_three_stage(k: int):
    """Stages 2 and 3 both at c = 1/2, weights (0, k/32, 1 - k/32). Order 2, three
    stages, so it lands in a cell where the stability front spreads: the scores
    below trade stability against error across this family."""
    b2 = Fraction(k, 32)
    return make_tableau([["0", "0", "0"], ["1/2", "0", "0"], ["1/2", "0", "0"]],
                        ["0", str(b2), str(1 - b2)])


# A tableau that leaves the Q15 range on every problem: b sums to 1, but the
# weights are far outside it, so the state runs away inside a few steps.
_BLOWUP = make_tableau([["0", "0"], ["1/2", "0"]], ["-8", "9"])


def _synthetic_records():
    """8 classical seeds at cycle 0 plus three discovered families, on two dates."""
    day1: list[Record] = []
    for i, (_, t) in enumerate(sorted(classical().items())):
        day1.append(_rec(t, 0.02 + 0.001 * i, stab=-2.0 - 0.1 * i, cycles=10 + 3 * i,
                         cycle_id=0, date="2026-09-01"))
    day2: list[Record] = []
    for k in range(17, 32):
        day2.append(_rec(_order2_two_stage(k), 0.019 - 0.0004 * k,
                         stab=-1.2 - 0.03 * k, cycles=17 + k,
                         cycle_id=100 + k, date="2026-09-02"))
        day2.append(_rec(_order1_two_stage(k), 0.030 - 0.0002 * k,
                         stab=-0.9 - 0.01 * k, cycles=40 + k,
                         cycle_id=200 + k, date="2026-09-02"))
        # this family buys stability with error, so its cell holds a real front
        day2.append(_rec(_order2_three_stage(k), 0.010 + 0.0005 * k,
                         stab=-1.0 - 0.05 * k, cycles=70 + k,
                         cycle_id=300 + k, date="2026-09-02"))
    # An exact heldout_error tie inside one cell: the earlier record must hold it
    # in both the grids and the front.
    day2.append(_rec(_order2_two_stage(31), 0.019 - 0.0004 * 31, stab=-1.2 - 0.03 * 31,
                     cycles=17 + 31, cycle_id=999, date="2026-09-02"))
    return day1, day2


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    """(archive directory, ScanResult) over a synthetic two-day archive."""
    d = tmp_path_factory.mktemp("synth_archive")
    day1, day2 = _synthetic_records()
    _write(d, "2026-09-01.jsonl", day1)
    _write(d, "2026-09-02.jsonl", day2)
    return d, S.scan_archive(paths=sorted(d.iterdir()))


@pytest.fixture(scope="module")
def synth_doc(synth):
    """A full document over the synthetic archive, kept cheap: one problem, one
    target, a short ladder."""
    _, scan = synth
    return S.build_results(scan=scan, selection={"methods": "all",
                                                 "problems": ["dahlquist"]},
                           targets=(2.0 ** -6,), n_max=32, bisect_probes=6)


# --------------------------------------------------------------------------- scan

def test_scan_matches_archive_replay_on_a_synthetic_archive():
    day1, day2 = _synthetic_records()
    d = archive_dir()
    _write(d, "2026-09-01.jsonl", day1)
    _write(d, "2026-09-02.jsonl", day2)

    scan = S.scan_archive()
    state = archive.replay()

    assert scan.n_records == state.n_records == len(day1) + len(day2)
    assert scan.last_cycle_id == state.last_cycle_id
    assert scan.dates == ("2026-09-01", "2026-09-02")
    for order in (1, 2, 3, 4):
        mine = {cell: r.tableau_hash for cell, r in scan.grids[order].items()}
        theirs = {cell: r.tableau_hash for cell, r in state.grids[order].items()}
        assert mine == theirs, order
    # the tie went to the earlier record, in the grid and in the front
    tied = content_hash(_order2_two_stage(31))
    holders = [r.cycle_id for g in scan.grids.values() for r in g.values()
               if r.tableau_hash == tied]
    assert 999 not in holders
    assert all(r.cycle_id != 999 for front in scan.pareto.values() for r in front)


def test_scan_discards_a_partial_trailing_line(tmp_path):
    day1, _ = _synthetic_records()
    d = tmp_path / "arc"
    _write(d, "2026-09-01.jsonl", day1[:-1])
    whole = json.dumps(archive.record_to_json(day1[-1]))
    with open(d / "2026-09-01.jsonl", "a", encoding="utf-8", newline="\n") as fh:
        fh.write(whole[: len(whole) // 2])          # a crash mid-append
    scan = S.scan_archive(paths=[d / "2026-09-01.jsonl"])
    assert scan.n_records == len(day1) - 1
    assert scan.discarded_lines == 1


# --------------------------------------------------------------------------- ladder

def test_cycles_to_tolerance_returns_the_smallest_n_on_the_sampled_set():
    euler = classical()["euler"]
    target = 2.0 ** -6
    out = S.cycles_to_tolerance(euler, PROBLEMS["dahlquist"], target,
                                n_max=64, bisect_probes=12)
    entry = out["targets"][S._target_key(target)]
    assert entry["status"] == "reached"
    assert all(row["status"] in S.RUNG_STATUSES for row in out["ladder"])
    assert all(e["status"] in S.TARGET_STATUSES for e in out["targets"].values())
    n = entry["n"]
    assert entry["cycles"] == n * out["cycles_per_step"]

    # the answer meets the target, and every smaller step count this run actually
    # looked at does not, checked by rerunning the pinned integrator
    assert problem_error(euler, PROBLEMS["dahlquist"], n)[0] <= target
    looked_at = [row["n"] for row in out["ladder"]] + [p["n"] for p in entry["probed"]]
    for m in sorted(x for x in set(looked_at) if x < n):
        try:
            err, _ = problem_error(euler, PROBLEMS["dahlquist"], m)
        except Exception:
            continue                                 # overflowed, so it did not meet it
        assert not (math.isfinite(err) and err <= target), m


def test_cycles_to_tolerance_reports_never_reached():
    euler = classical()["euler"]
    out = S.cycles_to_tolerance(euler, PROBLEMS["rc_thermal"], 1e-9, n_max=128)
    entry = out["targets"][S._target_key(1e-9)]
    assert entry["status"] == "never_reached"
    assert entry["n"] is None and entry["cycles"] is None
    assert entry["probes"] == 0
    assert out["ladder"] and any(row["status"] == "ok" for row in out["ladder"])


def test_overflow_is_counted_not_dropped(synth):
    _, scan = synth
    out = S.cycles_to_tolerance(_BLOWUP, PROBLEMS["rc_thermal"], 1e-9, n_max=64)
    assert all(row["status"] == "overflow" for row in out["ladder"])
    assert out["targets"][S._target_key(1e-9)]["status"] == "overflow_before_target"

    # and the method stays in the finding rather than vanishing from it
    scan2 = copy.copy(scan)
    scan2.grids = {o: dict(g) for o, g in scan.grids.items()}
    blown = _rec(_BLOWUP, 0.001, stab=-3.0, cycles=25, cycle_id=500)
    scan2.grids[1][(2, archive.cycle_bucket(25))] = blown
    finding = S.work_precision(scan2, {"methods": "elites", "problems": ["rc_thermal"]},
                               targets=(1e-9,), n_max=64, bisect_probes=4)
    rows = [r for r in finding["series"]["runs"]
            if r["tableau_hash"] == content_hash(_BLOWUP)]
    assert len(rows) == 1
    assert rows[0]["status"] == "overflow_before_target"
    assert finding["numbers"]["readings_overflow_before_target"] >= 1


def test_ladder_monotone_flag_is_set_when_error_rises(synth):
    _, scan = synth
    euler = classical()["euler"]
    # rc_thermal is the quantization-floor case: past n = 64 the error climbs
    rising = S.cycles_to_tolerance(euler, PROBLEMS["rc_thermal"], 1e-9, n_max=128)
    ok = [row["error"] for row in rising["ladder"] if row["status"] == "ok"]
    assert len(ok) >= 2 and ok[-1] > ok[0]
    assert rising["ladder_monotone"] is False
    assert rising["targets"][S._target_key(1e-9)]["ladder_monotone"] is False

    steady = S.cycles_to_tolerance(euler, PROBLEMS["dahlquist"], 2.0 ** -6, n_max=64)
    assert steady["ladder_monotone"] is True

    finding = S.work_precision(scan, {"methods": "anchors", "problems": ["rc_thermal"]},
                               targets=(1e-9,), n_max=128, bisect_probes=4)
    assert finding["numbers"]["nonmonotone_ladders"] >= 1
    assert any(row["ladder_monotone"] is False for row in finding["series"]["runs"])
    assert any(row["nonmonotone_ladders"] >= 1 for row in finding["series"]["cells"])


# The output of cycles_to_tolerance, captured with json.dumps(..., sort_keys=False)
# so key order is pinned along with the values. A blob rather than a live re-run,
# because a golden that recomputes itself pins nothing.
#
# Provenance, because it matters and the obvious reading is wrong: this literal was
# emitted AFTER ``ladder_scan`` was lifted out, so it guards against future drift and
# is not itself evidence that the lift changed nothing. That evidence is separate: a
# document rebuilt byte-identically against a capture taken before the patch, and a
# reference implementation written from the docstring rather than the body, diffed
# unsorted over 168 cases with no mismatch.
_LADDER_GOLDEN: dict[str, str] = {
    "rk4 dahlquist n_max=256": (
        '{"problem":"dahlquist","n_max":256,"bisect_probes":12,"cycles_per_step":33'
        ',"ladder_monotone":true,"ladder":[{"n":1,"status":"overflow","error":null,'
        '"max_abs_q":null},{"n":2,"status":"overflow","error":null,"max_abs_q":null'
        '},{"n":4,"status":"overflow","error":null,"max_abs_q":null},{"n":8,"status'
        '":"overflow","error":null,"max_abs_q":null},{"n":16,"status":"ok","error":'
        '0.00016747024226248486,"max_abs_q":8192},{"n":32,"status":"ok","error":0.0'
        '0016747024226248486,"max_abs_q":8192},{"n":64,"status":"ok","error":4.5399'
        '929762484854e-05,"max_abs_q":8192},{"n":128,"status":"ok","error":4.539992'
        '9762484854e-05,"max_abs_q":8192},{"n":256,"status":"ok","error":4.53999297'
        '62484854e-05,"max_abs_q":8192}],"targets":{"0.015625":{"target":0.015625,"'
        'n":11,"cycles":363,"status":"reached","probes":3,"probed":[{"n":10,"status'
        '":"overflow","error":null},{"n":11,"status":"ok","error":4.539992976248485'
        '4e-05},{"n":12,"status":"ok","error":0.00016747024226248486}],"ladder_mono'
        'tone":true},"0.00390625":{"target":0.00390625,"n":11,"cycles":363,"status"'
        ':"reached","probes":3,"probed":[{"n":10,"status":"overflow","error":null},'
        '{"n":11,"status":"ok","error":4.5399929762484854e-05},{"n":12,"status":"ok'
        '","error":0.00016747024226248486}],"ladder_monotone":true},"0.0009765625":'
        '{"target":0.0009765625,"n":11,"cycles":363,"status":"reached","probes":3,"'
        'probed":[{"n":10,"status":"overflow","error":null},{"n":11,"status":"ok","'
        'error":4.5399929762484854e-05},{"n":12,"status":"ok","error":0.00016747024'
        '226248486}],"ladder_monotone":true},"0.000244140625":{"target":0.000244140'
        '625,"n":11,"cycles":363,"status":"reached","probes":3,"probed":[{"n":10,"s'
        'tatus":"overflow","error":null},{"n":11,"status":"ok","error":4.5399929762'
        '484854e-05},{"n":12,"status":"ok","error":0.00016747024226248486}],"ladder'
        '_monotone":true}}}'
    ),
    "euler rc_thermal n_max=128": (
        '{"problem":"rc_thermal","n_max":128,"bisect_probes":12,"cycles_per_step":1'
        '5,"ladder_monotone":false,"ladder":[{"n":1,"status":"overflow","error":nul'
        'l,"max_abs_q":null},{"n":2,"status":"overflow","error":null,"max_abs_q":nu'
        'll},{"n":4,"status":"overflow","error":null,"max_abs_q":null},{"n":8,"stat'
        'us":"overflow","error":null,"max_abs_q":null},{"n":16,"status":"overflow",'
        '"error":null,"max_abs_q":null},{"n":32,"status":"overflow","error":null,"m'
        'ax_abs_q":null},{"n":64,"status":"ok","error":0.002746482895528819,"max_ab'
        's_q":8192},{"n":128,"status":"ok","error":0.005489900090930159,"max_abs_q"'
        ':8192}],"targets":{"0.015625":{"target":0.015625,"n":34,"cycles":510,"stat'
        'us":"reached","probes":5,"probed":[{"n":33,"status":"ok","error":0.1164430'
        '6571586242},{"n":34,"status":"ok","error":0.012586638539218004},{"n":36,"s'
        'tatus":"ok","error":0.004385919215434468},{"n":40,"status":"ok","error":0.'
        '0039559633713092185},{"n":48,"status":"ok","error":0.003655416218107611}],'
        '"ladder_monotone":false},"0.00390625":{"target":0.00390625,"n":48,"cycles"'
        ':720,"status":"reached","probes":5,"probed":[{"n":40,"status":"ok","error"'
        ':0.0039559633713092185},{"n":44,"status":"ok","error":0.004298752049493257'
        '},{"n":46,"status":"ok","error":0.004509614130315321},{"n":47,"status":"ok'
        '","error":0.004587252284839524},{"n":48,"status":"ok","error":0.0036554162'
        '18107611}],"ladder_monotone":false},"0.0009765625":{"target":0.0009765625,'
        '"n":null,"cycles":null,"status":"never_reached","probes":0,"probed":[],"la'
        'dder_monotone":false},"0.000244140625":{"target":0.000244140625,"n":null,"'
        'cycles":null,"status":"never_reached","probes":0,"probed":[],"ladder_monot'
        'one":false},"1e-09":{"target":1e-09,"n":null,"cycles":null,"status":"never'
        '_reached","probes":0,"probed":[],"ladder_monotone":false}}}'
    ),
    "blowup rc_thermal n_max=64": (
        '{"problem":"rc_thermal","n_max":64,"bisect_probes":4,"cycles_per_step":45,'
        '"ladder_monotone":true,"ladder":[{"n":1,"status":"overflow","error":null,"'
        'max_abs_q":null},{"n":2,"status":"overflow","error":null,"max_abs_q":null}'
        ',{"n":4,"status":"overflow","error":null,"max_abs_q":null},{"n":8,"status"'
        ':"overflow","error":null,"max_abs_q":null},{"n":16,"status":"overflow","er'
        'ror":null,"max_abs_q":null},{"n":32,"status":"overflow","error":null,"max_'
        'abs_q":null},{"n":64,"status":"overflow","error":null,"max_abs_q":null}],"'
        'targets":{"0.015625":{"target":0.015625,"n":null,"cycles":null,"status":"o'
        'verflow_before_target","probes":0,"probed":[],"ladder_monotone":true}}}'
    ),
}

_GOLDEN_CASES = {
    "rk4 dahlquist n_max=256": ("rk4", "dahlquist", S.TARGETS, 256, 12),
    "euler rc_thermal n_max=128": ("euler", "rc_thermal", S.TARGETS + (1e-9,), 128, 12),
    "blowup rc_thermal n_max=64": (None, "rc_thermal", (2.0 ** -6,), 64, 4),
}


@pytest.mark.parametrize("label", sorted(_LADDER_GOLDEN))
def test_cycles_to_tolerance_matches_its_recorded_ladder_output(label):
    # Provenance, stated accurately: this literal was emitted AFTER the ladder split, so
    # it is a forward regression guard rather than pre-refactor evidence. What proves the
    # split changed nothing is separate and stronger: a byte-identical rebuild of the
    # whole document against a capture taken before the patch, plus a reference
    # implementation written from the docstring and diffed unsorted over 168 cases.
    name, pname, targets, n_max, probes = _GOLDEN_CASES[label]
    t = _BLOWUP if name is None else classical()[name]
    out = S.cycles_to_tolerance(t, PROBLEMS[pname], targets,
                                n_max=n_max, bisect_probes=probes)
    text = json.dumps(out, sort_keys=False, separators=(",", ":"), allow_nan=False)
    assert text == _LADDER_GOLDEN[label]


def test_ladder_scan_is_the_body_cycles_to_tolerance_runs():
    """The public function is the Q15 probe plugged into the shared ladder, so the
    two have to agree rung for rung on the same input."""
    euler = classical()["euler"]
    p = PROBLEMS["dahlquist"]
    per_step = S.cycle_count(euler, S.COST_MODEL, p.n_states)
    cache: dict = {}
    scan = S.ladder_scan(lambda n: S._probe(euler, p, n, cache), S.TARGETS,
                         n_max=64, bisect_probes=12, per_unit_cost=per_step)
    direct = S.cycles_to_tolerance(euler, p, S.TARGETS, n_max=64, bisect_probes=12)
    assert scan["ladder"] == direct["ladder"]
    assert scan["targets"] == direct["targets"]
    assert scan["ladder_monotone"] == direct["ladder_monotone"]
    assert scan["per_unit_cost"] == direct["cycles_per_step"]
    assert scan["n_max"] == direct["n_max"] == 64
    assert scan["bisect"] is True


def test_ladder_scan_reads_a_tolerance_ladder_without_bisecting():
    """A tolerance in whole LSB has nothing between adjacent rungs, so the caller
    hands in its own ladder and turns bisection off. probes comes back 0 because
    none were taken, which is the honest number rather than a fiction."""
    errors = {512: 0.40, 256: 0.20, 128: 0.10, 64: 0.05, 32: 0.02, 16: 0.01}
    rungs = [512, 256, 128, 64, 32, 16]      # ascending cost, coarsest leading
    seen: list[int] = []

    def probe(u):
        seen.append(u)
        return ("ok", errors[u], u)

    out = S.ladder_scan(probe, (0.5, 0.06, 0.001), unit_ladder=rungs, bisect=False)
    assert seen == rungs                          # one probe per rung, nothing else
    assert [row["n"] for row in out["ladder"]] == rungs
    assert out["n_max"] is None                   # n_max did not bound this ladder
    assert out["bisect"] is False
    assert out["ladder_monotone"] is True
    # the earliest rung that meets the target, which on a ladder written in
    # ascending cost order is the coarsest tolerance that still gets there
    assert out["targets"][S._target_key(0.5)]["n"] == 512
    assert out["targets"][S._target_key(0.06)]["n"] == 64
    assert out["targets"][S._target_key(0.001)]["status"] == "never_reached"
    assert all(e["probes"] == 0 and e["probed"] == [] for e in out["targets"].values())
    # no per-unit cost was given, so no row invents a cycle count
    assert all(e["cycles"] is None for e in out["targets"].values())
    assert all(e["status"] in S.TARGET_STATUSES for e in out["targets"].values())

    priced = S.ladder_scan(probe, 0.06, unit_ladder=rungs, bisect=False,
                           per_unit_cost=7)
    assert priced["targets"][S._target_key(0.06)]["cycles"] == 64 * 7


def test_ladder_scan_refuses_a_ladder_it_cannot_bisect():
    """Bisection walks the integers between two rungs, so it is only defined on an
    ascending integer ladder. Refusing is better than bisecting a tolerance."""
    def probe(u):
        return ("ok", 1.0, 0)

    with pytest.raises(ValueError):
        S.ladder_scan(probe, 1.0, unit_ladder=[])
    with pytest.raises(ValueError):
        S.ladder_scan(probe, 1.0, unit_ladder=[512, 256, 128])     # descending
    with pytest.raises(ValueError):
        S.ladder_scan(probe, 1.0, unit_ladder=[1.0, 2.0, 4.0])     # not integers
    with pytest.raises(ValueError):
        S.ladder_scan(probe, 1.0, unit_ladder=[1, 1, 2])           # not strict
    with pytest.raises(ValueError):
        S.ladder_scan(probe, 1.0, unit_ladder=[], bisect=False)
    # the orderings bisection rejects are fine once it is off
    out = S.ladder_scan(probe, 1.0, unit_ladder=[512, 256], bisect=False)
    assert out["targets"][S._target_key(1.0)]["n"] == 512


def test_ladder_scan_carries_the_overflow_and_never_reached_statuses_through():
    """The two missed statuses are a property of the ladder, not of the Q15 probe,
    so a caller with its own probe gets the same distinction."""
    over = S.ladder_scan(lambda u: ("overflow", None, None), 1.0,
                         unit_ladder=[1, 2, 4], bisect=False)
    assert over["targets"][S._target_key(1.0)]["status"] == "overflow_before_target"
    assert over["ladder_monotone"] is True        # no clean rung disagrees with any

    # a rung that raised rather than overflowing is a different fact: the method
    # never produced a number, but it did not leave the Q15 range either
    errored = S.ladder_scan(lambda u: ("error", None, None), 1.0,
                            unit_ladder=[1, 2, 4], bisect=False)
    assert errored["targets"][S._target_key(1.0)]["status"] == "never_reached"

    ran = S.ladder_scan(lambda u: ("ok", 9.0, 3), 1.0, unit_ladder=[1, 2, 4],
                        bisect=False)
    assert ran["targets"][S._target_key(1.0)]["status"] == "never_reached"


# --------------------------------------------------------------------------- statistics

def test_spearman_matches_hand_computed_values():
    assert S._spearman([1.0, 2.0, 3.0, 4.0], [10.0, 20.0, 30.0, 40.0]) == pytest.approx(1.0)
    assert S._spearman([1.0, 2.0, 3.0, 4.0], [40.0, 30.0, 20.0, 10.0]) == pytest.approx(-1.0)
    # ranks (1, 2, 3, 4) against (1, 2.5, 2.5, 4): 4.5 / sqrt(5 * 4.5)
    assert S._spearman([1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 2.0, 4.0]) == pytest.approx(
        4.5 / math.sqrt(22.5))
    assert S._average_ranks([5.0, 5.0, 5.0]) == [2.0, 2.0, 2.0]
    assert math.isnan(S._spearman([1.0], [2.0]))
    assert math.isnan(S._spearman([1.0, 1.0], [3.0, 4.0]))   # no spread on one side


# --------------------------------------------------------------------------- pareto

def test_pareto_front_is_nondominated_and_sorted(tmp_path, monkeypatch):
    # one (order, stages) cell: the four leading points trade stability against
    # error and are mutually non-dominated, the last two are dominated
    points = [(-1.0, 0.010), (-1.5, 0.020), (-2.0, 0.030), (-2.5, 0.040),
              (-1.2, 0.050), (-0.9, 0.060)]
    records = [_rec(_order2_two_stage(17 + i), err, stab=stab, cycles=20,
                    cycle_id=10 + i)
               for i, (stab, err) in enumerate(points)]
    d = tmp_path / "arc"
    _write(d, "2026-09-01.jsonl", records)
    scan = S.scan_archive(paths=[d / "2026-09-01.jsonl"])
    front = scan.pareto[(2, 2)]
    got = [(r.score.stability_real, r.score.heldout_error) for r in front]
    for a in got:
        for b in got:
            if a is b:
                continue
            assert not (b[0] <= a[0] and b[1] <= a[1] and b != a)
    assert len(got) == 4
    # the scan keeps insertion order (the cap rule depends on it); the finding is
    # what sorts, by (stability_real, heldout_error, tableau_hash)
    cell = [c for c in S.stability_frontier(scan)["series"]["cells"]
            if (c["order"], c["stages"]) == (2, 2)][0]
    keys = [(m["stability_real"], m["heldout_error"], m["tableau_hash"])
            for m in cell["members"]]
    assert keys == sorted(keys)
    assert (-1.2, 0.050) not in got and (-0.9, 0.060) not in got
    assert sum(scan.pareto_discarded.values()) == 0

    monkeypatch.setattr(S, "PARETO_CAP", 2)
    capped = S.scan_archive(paths=[d / "2026-09-01.jsonl"])
    assert len(capped.pareto[(2, 2)]) <= 2
    assert capped.pareto_discarded.get((2, 2), 0) >= 1


def test_stability_frontier_names_the_censoring(synth):
    _, scan = synth
    finding = S.stability_frontier(scan)
    assert str(S.STABILITY_THRESHOLD) in finding["verdict"]
    assert "frontier of what the search looked at" in finding["verdict"]
    # the cited lines are the ones that actually do the rejecting
    cited = (HARNESS_DIR / "rk_harness" / "verifier.py").read_text(
        encoding="utf-8").splitlines()[122:125]
    assert "STABILITY_THRESHOLD" in cited[0] and "UNSTABLE" in cited[1]
    assert S._UNSTABLE_CITE in finding["verdict"]
    assert finding["numbers"]["stability_threshold"] == float(S.STABILITY_THRESHOLD)
    cells = finding["series"]["cells"]
    assert cells and all(c["stages"] >= c["order"] for c in cells)
    for c in cells:
        assert c["collapse_predicted"] == (c["stages"] == c["order"])
        assert c["discovered_in_cell"] <= c["records_in_cell"]
        assert c["front_size"] == len(c["members"])
    # the three-stage order-2 family spreads, the fixture-only cells do not
    spread = [c for c in cells if (c["order"], c["stages"]) == (2, 3)]
    assert spread and spread[0]["front_size"] > 1


# --------------------------------------------------------------------------- document

_BANNED = re.compile(
    r"\b(novel|first|beats|outperforms|breakthrough|proves|state-of-the-art|best-ever)\b",
    re.IGNORECASE,
)


def test_results_schema_validates(synth_doc):
    S.validate_results(synth_doc)
    meta = synth_doc["_meta"]
    assert meta["archive_records"] > 0
    assert meta["archive_dates"] == ["2026-09-01", "2026-09-02"]
    assert meta["verifier_hash"] == compute_verifier_hash()
    assert meta["n_max"] == 32 and meta["pareto_cap"] == S.PARETO_CAP
    for key in ("work_precision", "stability_frontier"):
        finding = synth_doc[key]
        assert set(("verdict", "numbers", "series")) <= set(finding)
        assert not _BANNED.search(finding["verdict"]), key
        assert chr(0x2014) not in finding["verdict"], key   # no em dashes
    # every method carries a run row for every (problem, target) it was asked about
    runs = synth_doc["work_precision"]["series"]["runs"]
    n_methods = synth_doc["work_precision"]["numbers"]["methods_evaluated"]
    assert len(runs) == n_methods * 1 * 1
    assert len({r["tableau_hash"] for r in runs}) == n_methods


def test_results_schema_rejects_bad_docs(synth_doc):
    bad = copy.deepcopy(synth_doc)
    del bad["_meta"]["archive_records"]
    with pytest.raises(ValueError, match="archive_records"):
        S.validate_results(bad)

    bad2 = copy.deepcopy(synth_doc)
    bad2["work_precision"]["series"]["runs"] = []
    with pytest.raises(ValueError, match="work_precision.series.runs"):
        S.validate_results(bad2)

    bad3 = copy.deepcopy(synth_doc)
    bad3["work_precision"]["series"]["cells"][0]["spearman_heldout_vs_cycles"] = float("inf")
    with pytest.raises(ValueError, match="spearman_heldout_vs_cycles"):
        S.validate_results(bad3)

    bad4 = copy.deepcopy(synth_doc)
    del bad4["stability_frontier"]["verdict"]
    with pytest.raises(ValueError, match="stability_frontier missing 'verdict'"):
        S.validate_results(bad4)

    bad5 = copy.deepcopy(synth_doc)
    bad5["_meta"]["selection"]["problems"] = []
    with pytest.raises(ValueError, match="selection.problems"):
        S.validate_results(bad5)

    bad6 = copy.deepcopy(synth_doc)
    bad6["work_precision"]["verdict"] += " This is a breakthrough."
    with pytest.raises(ValueError, match="banned word"):
        S.validate_results(bad6)


def test_write_results_deterministic(tmp_path, synth, synth_doc):
    _, scan = synth
    again = S.build_results(scan=scan, selection={"methods": "all",
                                                  "problems": ["dahlquist"]},
                            targets=(2.0 ** -6,), n_max=32, bisect_probes=6)
    p1 = S.write_results(synth_doc, tmp_path / "a.json")
    p2 = S.write_results(again, tmp_path / "b.json")
    b1, b2 = p1.read_bytes(), p2.read_bytes()
    assert b1 == b2
    assert b"\r\n" not in b1                       # byte-deterministic LF output
    assert b"NaN" not in b1 and b"Infinity" not in b1


def test_selection_is_stamped(synth, synth_doc):
    _, scan = synth
    partial = S.build_results(scan=scan,
                              selection={"methods": "anchors", "problems": ["dahlquist"]},
                              targets=(2.0 ** -6,), n_max=32, bisect_probes=6)
    S.validate_results(partial)
    assert partial["_meta"]["selection"] == {"methods": "anchors",
                                             "problems": ["dahlquist"]}
    assert synth_doc["_meta"]["selection"]["methods"] == "all"
    assert partial["work_precision"]["numbers"]["methods_evaluated"] == 8
    assert (partial["work_precision"]["numbers"]["methods_evaluated"]
            < synth_doc["work_precision"]["numbers"]["methods_evaluated"])
    with pytest.raises(ValueError, match="unknown problems"):
        S.normalize_selection({"methods": "all", "problems": ["not_a_problem"]})
    with pytest.raises(ValueError, match="unknown method selection"):
        S.normalize_selection({"methods": "champions", "problems": ["dahlquist"]})


def test_empty_archive_builds_a_document_with_an_absent_series():
    scan = S.scan_archive(paths=[])
    assert scan.n_records == 0 and scan.dates == () and scan.pareto == {}
    doc = S.build_results(scan=scan, selection={"methods": "elites",
                                                "problems": ["dahlquist"]},
                          targets=(2.0 ** -6,), n_max=16, bisect_probes=2)
    S.validate_results(doc)                      # an empty series would raise here
    assert "absent" in doc["work_precision"]["series"]["runs"]
    assert "absent" in doc["stability_frontier"]["series"]["cells"]
    assert doc["work_precision"]["numbers"]["methods_evaluated"] == 0


def test_cli_writes_the_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "N_MAX", 16)
    monkeypatch.setattr(S, "TARGETS", (2.0 ** -6,))
    out = tmp_path / "results.json"
    rc = S.main(["--methods", "anchors", "--problems", "dahlquist", "--out", str(out)])
    assert rc == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    S.validate_results(doc)
    assert doc["_meta"]["n_max"] == 16
    assert doc["_meta"]["selection"] == {"methods": "anchors", "problems": ["dahlquist"]}
    assert doc["_meta"]["archive_records"] == 0      # the isolated work dir is empty


def test_nothing_pinned_changed(synth):
    _, scan = synth
    before = compute_verifier_hash()
    scales = dict(frozen_problems.DERIV_SCALE)
    doc = S.build_results(scan=scan, selection={"methods": "anchors",
                                                "problems": ["dahlquist"]},
                          targets=(2.0 ** -6,), n_max=16, bisect_probes=4)
    S.validate_results(doc)
    after = compute_verifier_hash()
    assert before == after == pinned_verifier_hash()
    assert dict(frozen_problems.DERIV_SCALE) == scales
