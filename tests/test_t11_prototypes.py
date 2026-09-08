"""T11 tests: off-archive prototypes for future epochs (rk_harness/prototypes/).

Two sections share this file, one per side track:

* adaptive (side track B, epoch 2): tests named test_B2x_* below, covering the
  Bogacki-Shampine 3(2) embedded pair, the dyadic PI step controller, and the
  work-precision curve artifact from rk_harness/prototypes/adaptive.py.
* adaptive in Q15 (side track B, epoch 2): tests named test_B7x_* in the second
  section, covering rk_harness/prototypes/adaptive_q15.py: the exact pair and its
  error weights, the instrumented floors, the bounded bit scan, the Q14 controller
  tables, the Q31 time register, and the ARMv6-M reference sequence.
* SDIRK (side track C, epoch 3): tests named test_C_* are added at the end of
  this file by a later stage; keep the sections separated by their headers.

Prototypes are float-only and off-archive; these tests touch no pinned file
and no real work dir (conftest isolates RK_WORK_DIR).
"""
from __future__ import annotations

import json
import math

import pytest

from rk_harness.paths import work_dir
from rk_harness.prototypes.adaptive import (
    BOGACKI_SHAMPINE_32,
    build_curve,
    solve_adaptive,
    solve_fixed,
    write_curve,
)


# =========================================================================== #
# --- adaptive (side track B): embedded pair + PI step controller ----------- #
# =========================================================================== #

_PAIR = BOGACKI_SHAMPINE_32


def _dahlquist(t: float, y: tuple[float, ...]) -> tuple[float, ...]:
    """The scalar test equation y' = -y, exact solution exp(-t)."""
    return (-y[0],)


def _slopes(weights) -> list[float]:
    """log2 error ratios on dahlquist over n = 16, 32, 64, 128 fixed steps."""
    exact = math.exp(-2.0)
    errs = []
    for n in (16, 32, 64, 128):
        y = solve_fixed(_PAIR, _dahlquist, (1.0,), 2.0, n, weights=weights)
        errs.append(abs(y[0] - exact))
    return [math.log2(errs[i] / errs[i + 1]) for i in range(len(errs) - 1)]


def test_B20_pair_consistency():
    """Structural checks: row sums, weight sums, order conditions for b at
    order 3, order conditions for b_hat at order 2 but not 3, FSAL shape."""
    A, b, bh, c = _PAIR.A, _PAIR.b, _PAIR.b_hat, _PAIR.c
    s = len(b)
    assert len(A) == len(bh) == len(c) == s
    for i in range(s):
        assert len(A[i]) == s
        assert math.isclose(sum(A[i]), c[i], abs_tol=1e-15)
        for j in range(i, s):
            assert A[i][j] == 0.0            # strictly lower triangular
    # order 1: sum of weights
    assert math.isclose(sum(b), 1.0, abs_tol=1e-15)
    assert math.isclose(sum(bh), 1.0, abs_tol=1e-15)
    # order 2: b.c = 1/2 for both
    assert math.isclose(sum(b[i] * c[i] for i in range(s)), 0.5, abs_tol=1e-15)
    assert math.isclose(sum(bh[i] * c[i] for i in range(s)), 0.5, abs_tol=1e-15)
    # order 3 for b: b.c^2 = 1/3 and b.(A c) = 1/6
    assert math.isclose(sum(b[i] * c[i] ** 2 for i in range(s)), 1.0 / 3.0, abs_tol=1e-15)
    bac = sum(b[i] * sum(A[i][j] * c[j] for j in range(s)) for i in range(s))
    assert math.isclose(bac, 1.0 / 6.0, abs_tol=1e-15)
    # b_hat must NOT reach order 3, or the error estimate degenerates
    assert abs(sum(bh[i] * c[i] ** 2 for i in range(s)) - 1.0 / 3.0) > 1e-3
    # FSAL: the last A row is b and c ends at 1
    assert A[s - 1] == b
    assert c[s - 1] == 1.0
    # the estimate weights are not all zero
    assert any(b[i] != bh[i] for i in range(s))


def test_B21_propagated_order_on_dahlquist():
    """The propagated formula (b) shows order 3 on y' = -y."""
    slopes = _slopes(None)
    avg = sum(slopes) / len(slopes)
    assert 2.7 < avg < 3.3, slopes


def test_B22_embedded_order_on_dahlquist():
    """The embedded formula (b_hat) shows order 2 on y' = -y."""
    slopes = _slopes(_PAIR.b_hat)
    avg = sum(slopes) / len(slopes)
    assert 1.7 < avg < 2.3, slopes


def test_B23_controller_converges_on_smooth_problem():
    """On the smooth dahlquist problem the controller reaches t_end, the
    achieved error tracks the tolerance, and tighter tolerance costs more
    function evaluations."""
    exact = math.exp(-2.0)
    prev_err = None
    prev_fev = 0
    for tol in (1e-4, 1e-6, 1e-8):
        r = solve_adaptive(_PAIR, _dahlquist, (1.0,), 0.0, 2.0, rtol=tol, atol=tol)
        assert math.isclose(r.t, 2.0, rel_tol=1e-9)
        err = abs(r.y[0] - exact)
        assert err < 100.0 * tol
        assert r.n_fevals > prev_fev
        if prev_err is not None:
            assert err < prev_err
        prev_err = err
        prev_fev = r.n_fevals


def test_B24_rejections_counted_on_rough_problem():
    """A fast oscillator started with a far-too-large h forces rejections;
    they are counted, and the function-evaluation count matches the exact
    FSAL accounting: 1 + 3 * (accepted + rejected) for a 4-stage FSAL pair."""
    omega2 = 625.0                     # y'' = -625 y, period ~0.25
    rhs = lambda t, y: (y[1], -omega2 * y[0])
    r = solve_adaptive(_PAIR, rhs, (1.0, 0.0), 0.0, 1.0, rtol=1e-5, atol=1e-5, h0=0.5)
    assert r.n_rejected > 0
    assert r.n_accepted > 0
    assert r.n_fevals == 1 + 3 * (r.n_accepted + r.n_rejected)
    assert abs(r.y[0] - math.cos(25.0)) < 1e-3


@pytest.mark.slow
def test_B25_curve_schema_and_shape():
    """write_curve produces the documented artifact schema (in the isolated
    work dir) and the buck_converter points behave like a work-precision
    curve: tighter tolerance gives more evaluations and less error."""
    path = write_curve(problems=("buck_converter",), tols=(1e-3, 1e-5))
    assert path == work_dir() / "prototypes" / "adaptive_curve.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    for key in ("schema", "status", "arithmetic", "caveats", "pair",
                "controller", "problems", "tolerances", "points", "generated_by"):
        assert key in doc, key
    assert doc["status"] == "preliminary"
    assert "float64" in doc["arithmetic"]
    assert doc["pair"]["name"] == "bogacki_shampine_32"
    assert doc["pair"]["order"] == 3 and doc["pair"]["embedded_order"] == 2
    assert "1989" in doc["pair"]["citation"]
    pts = doc["points"]
    assert len(pts) == 2
    for p in pts:
        assert p["problem"] == "buck_converter"
        assert isinstance(p["n_accepted"], int) and p["n_accepted"] > 0
        assert isinstance(p["n_rejected"], int) and p["n_rejected"] >= 0
        assert isinstance(p["n_fevals"], int) and p["n_fevals"] > 0
        assert math.isfinite(p["achieved_error"]) and p["achieved_error"] > 0.0
    loose, tight = pts[0], pts[1]
    assert loose["tol"] > tight["tol"]
    assert tight["n_fevals"] > loose["n_fevals"]
    assert tight["achieved_error"] < loose["achieved_error"]
    # deterministic: building the document again gives identical bytes
    again = json.dumps(build_curve(problems=("buck_converter",), tols=(1e-3, 1e-5)),
                       indent=2, sort_keys=True) + "\n"
    assert again == path.read_text(encoding="utf-8")


# =========================================================================== #
# --- adaptive in Q15 (side track B): the estimate, controller and register -- #
# =========================================================================== #

from fractions import Fraction                                    # noqa: E402

from rk_harness.prototypes import adaptive_q15 as AQ              # noqa: E402


def test_B70_pair_rationals_and_error_weights():
    """The rationals, not floats parsed back into rationals.

    Fraction(2.0 / 9.0) is not 2/9, and the design's d = (-5/72, 1/12, 1/9, -1/8)
    only comes out exactly from the rationals, so this is the check that the pair was
    transcribed rather than recovered from the float table.
    """
    for i in range(4):
        assert float(AQ.BS32_B[i]) == BOGACKI_SHAMPINE_32.b[i]
        assert float(AQ.BS32_B_HAT[i]) == BOGACKI_SHAMPINE_32.b_hat[i]
        assert float(AQ.BS32_C[i]) == BOGACKI_SHAMPINE_32.c[i]
        for j in range(4):
            assert float(AQ.BS32_A[i][j]) == BOGACKI_SHAMPINE_32.A[i][j]
    assert AQ.D_EXACT == (Fraction(-5, 72), Fraction(1, 12), Fraction(1, 9),
                          Fraction(-1, 8))
    assert [r.exact for r in AQ.D_REPS] == [False, False, False, True]
    for r, x in zip(AQ.D_REPS, AQ.D_EXACT):
        assert abs(r.m) <= 32767 and 0 <= r.s <= 20
        assert abs(float(x) - r.m / (1 << r.s)) < 1e-4


def test_B71_float_mode_reproduces_the_frozen_curve():
    """Before any Q15 number here is believed, the same entry point has to reproduce
    the float prototype exactly. One tolerance keeps the tier fast."""
    from rk_harness.prototypes.adaptive import curve_point
    from rk_harness.validation import SCALE

    tol = 1e-4
    want = curve_point("buck_converter", tol)
    got = AQ.solve_adaptive_q15("buck_converter", SCALE["buck_converter"], tol,
                                arithmetic="float", max_attempts=200_000)
    assert got["status"] == "ok"
    for key in ("n_accepted", "n_rejected", "n_fevals"):
        assert got[key] == want[key], key
    assert math.isclose(got["achieved_error"], want["achieved_error"], rel_tol=0.0,
                        abs_tol=0.0)


def test_B72_the_estimate_accumulates_in_int32():
    """Each term fits Q15; the sum does not have to, and is taken in an int32
    register before any comparison."""
    reps = AQ.D_REPS
    hk = [(30000,), (30000,), (30000,), (30000,)]
    want = sum((hk[i][0] * reps[i].m) >> reps[i].s for i in range(4))
    got = AQ.q15_estimate(hk, reps)
    assert got == (want,)

    # A sum whose terms each fit Q15 while the sum does not. The real weights are all
    # under 1/8, so this needs weights of 1 to show it, which is the general case the
    # int32 register exists for.
    from rk_harness.coeffrep import to_rep

    unit = tuple(to_rep(Fraction(1)) for _ in range(4))
    hk2 = [(32767,), (32767,), (32767,), (32767,)]
    assert AQ.q15_estimate(hk2, unit) == (4 * 32767,)
    assert 4 * 32767 > 32767


def test_B73_floor_statistics_are_exact_and_bounded():
    """The instrumentation is the deliverable, so it has to be exact: the recorded
    remainders reproduce a direct recomputation, every per-term mean bias is a real
    fraction of one LSB, and the histogram accounts for every floor."""
    stats = AQ.new_floor_stats()
    hk_seq = [[(v,), (v // 2,), (-v,), (v + 7,)] for v in range(1, 400)]
    direct = [0, 0, 0, 0]
    counts = [0, 0, 0, 0]
    for hk in hk_seq:
        AQ.q15_estimate(hk, AQ.D_REPS, stats)
        for i, rep in enumerate(AQ.D_REPS):
            direct[i] += (hk[i][0] * rep.m) & ((1 << rep.s) - 1)
            counts[i] += 1
    assert stats.remainder_sums == direct
    assert stats.counts == counts
    for i, mean in enumerate(stats.mean_bias_lsb()):
        assert mean is not None
        assert -1.0 <= mean <= 0.0, i
        assert mean == -float(Fraction(direct[i], counts[i] * (1 << AQ.D_REPS[i].s)))
    for i in range(4):
        assert sum(stats.histograms[i]) == counts[i]
    assert math.isclose(stats.total_bias_lsb(), sum(stats.mean_bias_lsb()))


def test_B74_controller_tables_are_representable():
    """The test that pins the Q14 decision.

    A Q15 table cannot hold this controller at all: a factor of 1.0 is 32768 and 2.0
    is 65536, both outside int16. Q14 puts the clamp [1/4, 2 - 2**-14] at [4096,
    32767]. If someone reverts the encoding to Q15 this fails rather than overflowing
    at run time.
    """
    assert AQ.FACTOR_Q_BITS == 14
    assert AQ.FAC_MAX == Fraction(2) - Fraction(1, 1 << AQ.FACTOR_Q_BITS)
    for table in (AQ.FTAB33, AQ.FTAB9):
        entries = table.entries
        assert len(entries) == (33 if table is AQ.FTAB33 else 9)
        for e in entries:
            assert isinstance(e, int) and 4096 <= e <= 32767
            assert AQ.FAC_MIN <= Fraction(e, 1 << AQ.FACTOR_Q_BITS) <= AQ.FAC_MAX
        assert all(a <= b for a, b in zip(entries, entries[1:])), "table must be monotone"
        assert AQ.step_factor(0, table) == round(16384 * 7 / 8)
        assert AQ.step_factor(-1000, table) == entries[0]
        assert AQ.step_factor(1000, table) == entries[-1]
    # the update is a Q14 shift with an int16 range check, not q15_mul
    assert AQ.apply_factor(16384, AQ.step_factor(0)) == (16384 * 14336) >> 14
    assert AQ.apply_factor(32767, AQ.step_factor(16)) == 32767


def test_B75_msb_is_a_bounded_loop():
    """ARMv6-M has no CLZ, so the leading-bit index is a software loop, and the cost
    model books it at its worst case. A bound nobody checks is not a bound."""
    for x in (1, 2, 3, 255, 256, 32767, 32768, (1 << 30), (1 << 31) - 1):
        got, iters = AQ.msb_and_iterations(x)
        assert got == x.bit_length() - 1
        assert 0 <= iters <= AQ.MSB_MAX_ITERS
    # the largest positive int32 needs 30 iterations, so the booked worst case of 31
    # is conservative by one rather than wrong
    assert AQ.msb_and_iterations((1 << 31) - 1)[1] == 30
    assert AQ.MSB_MAX_ITERS >= 30
    with pytest.raises(ValueError):
        AQ.msb(0)


def test_B76_q31_time_register_is_exact_and_monotone():
    """A sum of many Q15 steps leaves int16 at once, which is why elapsed time needs
    the int32 register. It has to stay inside int32, only move forward, and track the
    float time it stands for."""
    clock = AQ.TimeQ31(25.0)
    assert clock.rep.m > 0
    total = 0
    t_float = 0.0
    prev = -1
    for h_q in (32767, 20000, 12345, 7, 1, 4096):
        inc = clock.advance(h_q)
        total += inc
        t_float += h_q / 32768.0
        assert clock.reg == total
        assert clock.reg > prev and clock.reg <= AQ.TIME_LAST
        prev = clock.reg
        # one register LSB is t_end / 2**31 of time; the unit coefficient is itself
        # a rounded m / 2**s, so allow a step's worth of that rounding too
        slack = 25.0 / AQ.TIME_FULL + abs(t_float) * 1e-4
        assert abs(clock.recover_float(25.0) - t_float) <= slack
    assert clock.as_dict()["register_bits"] == 31


def test_B77_reference_sequence_is_priceable():
    """The listing has to be priceable by the pinned cost model, which knows eight
    mnemonics and no branch class at all. The branches are therefore reported beside
    the count and never folded into it."""
    from rk_harness.costmodel import _MNEMONIC_CLASS, count_sequence, M0PLUS_FAST

    lines = AQ.reference_lines()
    for raw in lines:
        line = raw.split("@", 1)[0].strip()
        if not line:
            continue
        assert line.split()[0].upper() in _MNEMONIC_CLASS, raw

    costs = AQ.sequence_costs()
    assert costs == AQ.sequence_costs()
    assert set(costs["total"]) == {"m0plus_fast", "m0plus_slow", "avr_approx"}
    assert len(set(costs["total"].values())) == 3
    assert costs["total"]["m0plus_fast"] == count_sequence(lines, M0PLUS_FAST)
    # the fast and slow m0plus models differ only in the cost of a multiply, so the
    # gap between them is exactly 31 cycles per MULS in the listing
    n_mul = sum(1 for raw in lines
                if raw.split("@", 1)[0].strip().split()[:1] == ["MULS"])
    assert costs["total"]["m0plus_slow"] - costs["total"]["m0plus_fast"] == 31 * n_mul
    assert "branch_allowance" in costs
    assert costs["branch_allowance"]["conditional_branches"] > 0
    assert "branch" not in str(costs["total"])
    assert set(costs["per_section"]) == {"estimate", "compare", "bit_scan",
                                         "table_load", "step_update"}


def test_B78_a_point_is_a_pure_function_of_code_and_params():
    """The T11 mirror of test_ST2. A short ladder: this checks reproducibility, not
    the sweep."""
    kw = dict(tols=(256, 512), max_attempts=2000)
    a = AQ.build_point("buck_converter", (1, 1), **kw)
    b = AQ.build_point("buck_converter", (1, 1), **kw)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert a["summary"]["points"] == 2
    assert a["scale_factor"] == "1/1"
    assert a["encoding"]["factor_q_bits"] == 14
    for row in a["tables"]["ftab33"]["points"]:
        assert row["status"] in ("ok", "attempt_cap", "step_underflow", "overflow",
                                "register_overflow")
        assert (row["achieved_error"] is None) == (row["status"] != "ok")
        assert -4.0 <= row["mean_bias_lsb_total"] <= 0.0


@pytest.mark.slow
def test_B79_the_headline_curves_are_not_overwritten():
    """Invariant I9: automation writes its own artifact and never the two published
    prototype curves."""
    out = work_dir() / "prototypes"
    path = AQ.write_curve(problems=("buck_converter",), scale_factors=((1, 1),),
                          tols=(512,))
    assert path == out / "adaptive_q15_curve.json"
    assert path.exists()
    assert not (out / "adaptive_curve.json").exists()
    assert not (out / "sdirk_curve.json").exists()
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert "preliminary" in doc["label"] and len(doc["points"]) == 1


# =========================================================================== #
# --- SDIRK (side track C): added by a later stage below this line ---------- #
# =========================================================================== #
