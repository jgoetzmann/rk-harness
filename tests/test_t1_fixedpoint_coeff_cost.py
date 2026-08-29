"""T1 spec tests: fixedpoint, coeffrep, tableau, costmodel.

Every test name carries the SPEC ``## Behaviors`` ID it arbitrates. Fixtures are
loaded by path from ``fixtures/`` and never regenerated. Tableaus are built three
independent ways (``classical()``, ``make_tableau`` from the HANDOFF section 9.1
coefficients, and a direct ``Tableau`` from the fixture JSON) so a broken loader
cannot hide behind a broken constructor or vice versa.
"""
from __future__ import annotations

import hashlib
import json
import random
from fractions import Fraction

import pytest

from rk_harness.paths import FIXTURES_DIR, PACKAGE_DIR
from rk_harness.types import CoeffRep, CostModel, Q15Tableau, Tableau
from rk_harness.fixedpoint import (
    Q15_MAX,
    Q15_MIN,
    Q15OverflowError,
    q15_add,
    q15_apply,
    q15_from_float,
    q15_mul,
    q15_to_float,
)
from rk_harness.coeffrep import (
    csd_weight,
    is_trivial,
    quant_error,
    rep_value,
    tableau_csd_total,
    tableau_quant_error,
    to_rep,
)
from rk_harness.tableau import (
    all_dyadic,
    canonical,
    classical,
    content_hash,
    from_json,
    is_explicit,
    make_tableau,
    row_sums_consistent,
    stages,
    to_json,
    to_q15,
)
from rk_harness.costmodel import (
    AVR_APPROX,
    COST_MODELS,
    M0PLUS_FAST,
    M0PLUS_SLOW,
    coeff_cost,
    count_sequence,
    cycle_count,
    emit_c,
)

F = Fraction

# ---------------------------------------------------------------------------
# Fixtures (inline, loaded by path)
# ---------------------------------------------------------------------------

_Q15_FIXTURE = json.loads((FIXTURES_DIR / "q15.json").read_text(encoding="utf-8"))
_CLASSICAL_FIXTURE = json.loads((FIXTURES_DIR / "classical.json").read_text(encoding="utf-8"))

CLASSICAL_NAMES = ("euler", "midpoint", "heun2", "ralston2", "heun3", "kutta3", "rk4", "rk38")

_MUL_ROWS = _Q15_FIXTURE["mul"]
_MUL_IDS = [f"F{i + 1}" for i in range(len(_MUL_ROWS))]

_COEFFREP_ROWS = [
    (key, row[0], row[1], row[2], row[3])
    for key, row in _CLASSICAL_FIXTURE["_coeffrep"].items()
    if not key.startswith("_")
]

_ANCHOR_ROWS = [
    (name, int(n), model_name, cycles)
    for name, per_n in _CLASSICAL_FIXTURE["_anchor_cycles"].items()
    if not name.startswith("_")
    for n, per_model in per_n.items()
    for model_name, cycles in per_model.items()
]


def _tab(A, b, c) -> Tableau:
    """Direct Tableau construction (bypasses make_tableau and classical())."""
    return Tableau(
        A=tuple(tuple(F(x) for x in row) for row in A),
        b=tuple(F(x) for x in b),
        c=tuple(F(x) for x in c),
    )


def _fixture_tableau(name: str) -> Tableau:
    d = _CLASSICAL_FIXTURE[name]
    return _tab(d["A"], d["b"], d["c"])


def _inline_classical() -> dict[str, Tableau]:
    """HANDOFF section 9.1 coefficients, exact, through make_tableau with Fractions."""
    return {
        "euler": make_tableau([[F(0)]], [F(1)], [F(0)]),
        "midpoint": make_tableau([[F(0), F(0)], [F(1, 2), F(0)]], [F(0), F(1)], [F(0), F(1, 2)]),
        "heun2": make_tableau([[F(0), F(0)], [F(1), F(0)]], [F(1, 2), F(1, 2)], [F(0), F(1)]),
        "ralston2": make_tableau(
            [[F(0), F(0)], [F(2, 3), F(0)]], [F(1, 4), F(3, 4)], [F(0), F(2, 3)]
        ),
        "kutta3": make_tableau(
            [[F(0), F(0), F(0)], [F(1, 2), F(0), F(0)], [F(-1), F(2), F(0)]],
            [F(1, 6), F(2, 3), F(1, 6)],
            [F(0), F(1, 2), F(1)],
        ),
        "heun3": make_tableau(
            [[F(0), F(0), F(0)], [F(1, 3), F(0), F(0)], [F(0), F(2, 3), F(0)]],
            [F(1, 4), F(0), F(3, 4)],
            [F(0), F(1, 3), F(2, 3)],
        ),
        "rk4": make_tableau(
            [
                [F(0), F(0), F(0), F(0)],
                [F(1, 2), F(0), F(0), F(0)],
                [F(0), F(1, 2), F(0), F(0)],
                [F(0), F(0), F(1), F(0)],
            ],
            [F(1, 6), F(1, 3), F(1, 3), F(1, 6)],
            [F(0), F(1, 2), F(1, 2), F(1)],
        ),
        "rk38": make_tableau(
            [
                [F(0), F(0), F(0), F(0)],
                [F(1, 3), F(0), F(0), F(0)],
                [F(-1, 3), F(1), F(0), F(0)],
                [F(1), F(-1), F(1), F(0)],
            ],
            [F(1, 8), F(3, 8), F(3, 8), F(1, 8)],
            [F(0), F(1, 3), F(2, 3), F(1)],
        ),
    }


def _all_entries(t: Tableau):
    for row in t.A:
        yield from row
    yield from t.b
    yield from t.c


# ===========================================================================
# Fixed point
# ===========================================================================


@pytest.mark.parametrize("a,b,expected", _MUL_ROWS, ids=_MUL_IDS)
def test_F1_F16_q15_mul_fixture_row(a, b, expected):
    if isinstance(expected, str):
        assert expected == "Q15OverflowError"
        with pytest.raises(Q15OverflowError):
            q15_mul(a, b)
    else:
        got = q15_mul(a, b)
        assert got == expected
        assert isinstance(got, int) and not isinstance(got, bool)
        assert Q15_MIN <= got <= Q15_MAX


def test_F16_last_fixture_row_is_the_overflow_row():
    a, b, expected = _MUL_ROWS[-1]
    assert (a, b, expected) == (-32768, -32768, "Q15OverflowError")
    with pytest.raises(Q15OverflowError):
        q15_mul(a, b)


@pytest.mark.parametrize(
    "row", _Q15_FIXTURE["floor_vs_trunc"], ids=lambda r: f"{r['a']}x{r['b']}"
)
def test_F17_floor_vs_trunc_rows_give_floor_result(row):
    got = q15_mul(row["a"], row["b"])
    assert got == row["floor"]
    assert got == -1
    assert got != row["trunc"]


def test_F18_mul_min_times_min_raises():
    assert Q15_MIN == -32768 and Q15_MAX == 32767
    with pytest.raises(Q15OverflowError):
        q15_mul(Q15_MIN, Q15_MIN)


def test_F18_overflow_error_is_an_exception_subclass():
    assert issubclass(Q15OverflowError, Exception)
    with pytest.raises(Exception):
        q15_mul(-32768, -32768)


@pytest.mark.parametrize(
    "a,b",
    [(32768, 1), (1, 32768), (-32769, 1), (1, -32769), (65536, 0), (0, -65536)],
)
def test_F18_mul_out_of_range_inputs_raise(a, b):
    with pytest.raises(Q15OverflowError):
        q15_mul(a, b)


def test_F18_mul_min_times_max_does_not_raise():
    # The only raising pair is (-32768, -32768); its neighbour is fine.
    assert q15_mul(-32768, 32767) == -32767
    assert q15_mul(32767, -32768) == -32767


@pytest.mark.parametrize("a,b", _Q15_FIXTURE["add_raises"], ids=["max+1", "min-1"])
def test_F19_add_fixture_rows_raise(a, b):
    with pytest.raises(Q15OverflowError):
        q15_add(a, b)


@pytest.mark.parametrize("a,b", [(32768, 0), (0, 32768), (-32769, 0), (0, -32769)])
def test_F19_add_out_of_range_inputs_raise(a, b):
    with pytest.raises(Q15OverflowError):
        q15_add(a, b)


@pytest.mark.parametrize("a,b", [(16384, 16384), (-16384, -16385), (32767, 32767), (-32768, -32768)])
def test_F19_add_result_overflow_raises(a, b):
    with pytest.raises(Q15OverflowError):
        q15_add(a, b)


def test_F19_add_in_range_boundaries_succeed():
    assert q15_add(32767, 0) == 32767
    assert q15_add(-32768, 0) == -32768
    assert q15_add(16384, 16383) == 32767
    assert q15_add(-16384, -16384) == -32768
    assert q15_add(32767, -32768) == -1
    assert q15_add(0, 0) == 0


def test_F20_roundtrip_max_error_over_100k_samples():
    rng = random.Random(0)
    worst = 0.0
    overflowed = 0
    for _ in range(100_000):
        x = rng.uniform(-1, 0.99999)
        try:
            q = q15_from_float(x)
        except Q15OverflowError:
            # Legitimate only when the scaled sample rounds past Q15_MAX (the
            # spec's sampler can reach 0.99999 > 32767.5/32768). Anything else
            # is a bug in q15_from_float. See spec-gaps note.
            assert round(x * 32768) > Q15_MAX
            overflowed += 1
            continue
        assert isinstance(q, int) and not isinstance(q, bool)
        assert Q15_MIN <= q <= Q15_MAX
        worst = max(worst, abs(x - q15_to_float(q)))
    assert overflowed <= 10
    assert worst <= _Q15_FIXTURE["roundtrip_max_error"]  # 1.526e-05
    assert worst <= _Q15_FIXTURE["roundtrip_bound"]  # 3.052e-05
    # The round-trip is lossy by construction; 100k samples fill the LSB.
    assert worst >= 1.52e-05


def test_F20_q15_to_float_exact_values():
    assert q15_to_float(16384) == 0.5
    assert q15_to_float(-32768) == -1.0
    assert q15_to_float(32767) == 32767 / 32768.0
    assert q15_to_float(0) == 0.0
    assert q15_to_float(1) == 2.0 ** -15
    assert isinstance(q15_to_float(1), float)


def test_F21_mul_by_zero_is_zero_for_random_int16():
    rng = random.Random(0)
    for _ in range(1000):
        x = rng.randint(Q15_MIN, Q15_MAX)
        assert q15_mul(x, 0) == 0
        assert q15_mul(0, x) == 0
    assert q15_mul(Q15_MIN, 0) == 0
    assert q15_mul(Q15_MAX, 0) == 0


def test_B1_from_float_values():
    assert q15_from_float(-1.0) == -32768
    assert q15_from_float(0.5) == 16384
    assert q15_from_float(0.0) == 0
    assert q15_from_float(-0.5) == -16384
    assert q15_from_float(0.25) == 8192
    assert q15_from_float(32767 / 32768) == 32767
    got = q15_from_float(0.5)
    assert isinstance(got, int) and not isinstance(got, bool)


def test_B1_from_float_rounds_half_to_even():
    # x*32768 is exact in float64 for these dyadic inputs.
    assert q15_from_float(1.5 / 32768) == 2
    assert q15_from_float(2.5 / 32768) == 2
    assert q15_from_float(0.5 / 32768) == 0
    assert q15_from_float(-0.5 / 32768) == 0
    assert q15_from_float(-1.5 / 32768) == -2
    assert q15_from_float(-2.5 / 32768) == -2
    # -32768.5 rounds half-to-even to -32768, which is in range.
    assert q15_from_float(-32768.5 / 32768) == -32768


@pytest.mark.parametrize(
    "x",
    [
        1.0,
        2.0,
        -2.0,
        float("nan"),
        float("inf"),
        float("-inf"),
        32767.5 / 32768,  # rounds half-to-even to 32768: out of range
        -1.0 - 2.0 ** -15,  # -32769
        1e300,
        -1e300,
    ],
)
def test_B1_from_float_raises_out_of_range_or_non_finite(x):
    with pytest.raises(Q15OverflowError):
        q15_from_float(x)


def test_B2_apply_values():
    assert q15_apply(16384, 3, 2) == 12288
    assert q15_apply(16384, -3, 2) == -12288
    assert q15_apply(32767, 1, 0) == 32767
    assert q15_apply(-32768, 1, 0) == -32768
    assert q15_apply(0, 32767, 0) == 0
    assert q15_apply(1, 1, 1) == 0
    assert q15_apply(8192, 21845, 16) == 2730  # 1/4 * ~1/3, floored
    got = q15_apply(16384, 3, 2)
    assert isinstance(got, int) and not isinstance(got, bool)


def test_B2_apply_floors_toward_negative_infinity():
    assert q15_apply(-1, 1, 1) == -1
    assert q15_apply(-3, 5, 4) == -1  # -15 >> 4
    assert q15_apply(-8192, 21845, 16) == -2731  # floor(-2730.625)
    assert q15_apply(-1, 1, 15) == -1


@pytest.mark.parametrize(
    "v,m,s",
    [(32767, 2, 0), (-32768, 2, 0), (-32768, -1, 0), (16384, 32767, 0), (32767, 3, 1)],
)
def test_B2_apply_raises_when_result_leaves_int16(v, m, s):
    with pytest.raises(Q15OverflowError):
        q15_apply(v, m, s)


# ===========================================================================
# Coefficient representation
# ===========================================================================


@pytest.mark.parametrize("key,m,s,exact,w", _COEFFREP_ROWS, ids=[r[0] for r in _COEFFREP_ROWS])
def test_G24_to_rep_fixture_rows(key, m, s, exact, w):
    r = to_rep(F(key))
    assert isinstance(r, CoeffRep)
    assert (r.m, r.s, r.exact, r.csd_weight) == (m, s, exact, w)
    assert r == CoeffRep(m, s, exact, w)


@pytest.mark.parametrize("key,m,s,exact,w", _COEFFREP_ROWS, ids=[r[0] for r in _COEFFREP_ROWS])
def test_G24_rep_value_and_quant_error_agree_with_fixture_rows(key, m, s, exact, w):
    x = F(key)
    r = to_rep(x)
    assert rep_value(r) == F(m, 2 ** s)
    assert isinstance(rep_value(r), Fraction)
    expected_err = abs(float(x - F(m, 2 ** s)))
    assert abs(quant_error(x) - expected_err) <= 1e-15
    if exact:
        assert rep_value(r) == x
        assert quant_error(x) == 0.0
    else:
        assert rep_value(r) != x
        assert quant_error(x) > 0.0


@pytest.mark.parametrize("key", ["1/3", "1/6", "2/3", "-1/3"])
def test_G24_thirds_are_inexact_with_weight_8(key):
    r = to_rep(F(key))
    assert r.exact is False
    assert abs(r.m) == 21845
    assert r.csd_weight == 8
    assert quant_error(F(key)) > 0.0


def test_G24_fixture_has_eleven_coeffrep_rows():
    assert len(_COEFFREP_ROWS) == 11


def test_G25_csd_weight_of_3_and_of_3_over_8_scaled():
    assert csd_weight(3) == 2
    assert csd_weight(int(3 / 8 * 2 ** 3)) == 2


@pytest.mark.parametrize(
    "m,w",
    [
        (21845, 8),
        (0, 0),
        (-7, 2),
        (1, 1),
        (2, 1),
        (-1, 1),
        (5, 2),
        (6, 2),
        (7, 2),
        (15, 2),
        (16385, 2),
        (32767, 2),
        (-32767, 2),
        (1024, 1),
        (-21845, 8),
    ],
)
def test_B3_csd_weight_values(m, w):
    got = csd_weight(m)
    assert got == w
    assert isinstance(got, int) and not isinstance(got, bool)


def test_B3_csd_weight_is_sign_symmetric_and_never_exceeds_popcount():
    rng = random.Random(0)
    for _ in range(500):
        m = rng.randint(-32767, 32767)
        assert csd_weight(m) == csd_weight(-m)
        assert csd_weight(m) <= bin(abs(m)).count("1")
        assert csd_weight(m) >= (1 if m else 0)
    for k in range(0, 15):
        assert csd_weight(2 ** k) == 1
    for k in range(2, 15):
        assert csd_weight(2 ** k - 1) == 2


def test_B4_to_rep_clamps_large_value_to_m_max():
    r = to_rep(F(40000))
    assert r.m == 32767
    assert r.exact is False
    assert r.s == 0
    assert r.csd_weight == csd_weight(32767)


def test_B4_to_rep_negative_large_value_clamps_symmetrically():
    r = to_rep(F(-40000))
    assert r.m == -32767
    assert r.exact is False
    assert r.s == 0


def test_B4_to_rep_32768_is_just_outside_m_max():
    r = to_rep(F(32768))
    assert r.exact is False
    assert r.m == 32767
    assert r.s == 0
    r2 = to_rep(F(32767))
    assert r2 == CoeffRep(32767, 0, True, 2)


def test_B4_to_rep_tiny_value_underflows_to_zero_inexact():
    r = to_rep(F(1, 2 ** 21))
    assert r.m == 0
    assert r.exact is False
    assert r.csd_weight == 0
    assert rep_value(r) == 0


def test_B4_to_rep_zero_is_exact():
    r = to_rep(F(0))
    assert r == CoeffRep(0, 0, True, 0)
    assert (r.m, r.s, r.exact, r.csd_weight) == (0, 0, True, 0)


def test_B4_to_rep_exact_at_smallest_shift():
    assert to_rep(F(4)) == CoeffRep(4, 0, True, 1)
    assert to_rep(F(3, 2)) == CoeffRep(3, 1, True, 2)
    assert to_rep(F(1, 2 ** 20)) == CoeffRep(1, 20, True, 1)
    assert to_rep(F(-3, 8)) == CoeffRep(-3, 3, True, 2)
    assert to_rep(F(16385, 32768)) == CoeffRep(16385, 15, True, 2)


def test_B4_to_rep_keyword_s_max_limits_the_search_and_ties_go_to_smaller_s():
    # 1/3 with s <= 2: errors 1/3, 1/6, 1/12 -> s = 2, m = 1
    assert to_rep(F(1, 3), s_max=2) == CoeffRep(1, 2, False, 1)
    # 3/8 with s <= 2: s=1 -> 1/2 (err 1/8), s=2 -> round(3/2)=2 -> 1/2 (err 1/8); tie -> s = 1
    assert to_rep(F(3, 8), s_max=2) == CoeffRep(1, 1, False, 1)


def test_B4_to_rep_keyword_m_max_clamps():
    r = to_rep(F(5), m_max=4)
    assert r == CoeffRep(4, 0, False, 1)


def test_B4_to_rep_never_raises_and_stays_in_bounds_on_random_fractions():
    rng = random.Random(0)
    samples = [F(rng.randint(-100_000, 100_000), rng.randint(1, 100_000)) for _ in range(300)]
    samples += [F(10 ** 12), F(-(10 ** 12)), F(1, 10 ** 12), F(-1, 10 ** 12), F(32767), F(-32767)]
    for x in samples:
        r = to_rep(x)
        assert isinstance(r, CoeffRep)
        assert -32767 <= r.m <= 32767
        assert 0 <= r.s <= 20
        assert r.csd_weight == csd_weight(r.m)
        assert r.exact == (rep_value(r) == x)
        assert isinstance(quant_error(x), float)
        assert quant_error(x) >= 0.0


@pytest.mark.parametrize("name", CLASSICAL_NAMES)
def test_B5_classical_csd_total_and_quant_error_match_fixture(name):
    fx = _CLASSICAL_FIXTURE[name]
    t = classical()[name]
    total = tableau_csd_total(t)
    assert isinstance(total, int) and not isinstance(total, bool)
    assert total == fx["csd_total"]
    qe = tableau_quant_error(t)
    assert isinstance(qe, float)
    assert abs(qe - fx["quant_err"]) <= 1e-8


def test_B5_exact_tableaus_have_quant_error_exactly_zero():
    cl = classical()
    for name in ("euler", "midpoint", "heun2"):
        assert tableau_quant_error(cl[name]) == 0.0
    t = _tab([[0, 0], ["1/2", 0]], ["3/8", "5/8"], [0, "1/2"])
    assert tableau_quant_error(t) == 0.0
    # 1/2 (w1) + 3/8 (w2) + 5/8 (w2: 101 -> 8 - 3 = weight 2) = 5; c excluded
    assert tableau_csd_total(t) == 5


def test_B5_c_is_excluded_from_csd_total_and_quant_error():
    # Same A and b, wildly different c: c contributes nothing.
    t1 = _tab([[0, 0], ["1/2", 0]], ["1/2", "1/2"], [0, "1/2"])
    t2 = _tab([[0, 0], ["1/2", 0]], ["1/2", "1/2"], ["1/3", "2/3"])
    assert tableau_csd_total(t1) == tableau_csd_total(t2) == 3
    assert tableau_quant_error(t1) == tableau_quant_error(t2) == 0.0


def test_B5_is_trivial_only_for_zero_and_plus_minus_one():
    for x in (F(0), F(1), F(-1)):
        assert is_trivial(x) is True
    for x in (F(2), F(-2), F(1, 2), F(-1, 2), F(3, 8), F(1, 3), F(32767)):
        assert is_trivial(x) is False


def test_B5_named_quant_errors():
    assert abs(quant_error(F(1, 3)) - 5.086e-06) <= 1e-8
    assert abs(quant_error(F(1, 6)) - 2.543e-06) <= 1e-8
    assert abs(quant_error(F(2, 3)) - 1.017e-05) <= 1e-8
    assert quant_error(F(1, 2)) == 0.0
    assert quant_error(F(2)) == 0.0
    assert quant_error(F(0)) == 0.0


# ===========================================================================
# Tableau
# ===========================================================================


def test_B6_content_hash_is_64_lowercase_hex_and_stable():
    cl = classical()
    h1 = content_hash(cl["rk4"])
    h2 = content_hash(cl["rk4"])
    assert isinstance(h1, str)
    assert len(h1) == 64
    assert h1 == h1.lower()
    assert all(ch in "0123456789abcdef" for ch in h1)
    assert h1 == h2
    assert h1 == content_hash(_inline_classical()["rk4"])
    assert h1 == content_hash(_fixture_tableau("rk4"))


def test_B6_content_hash_differs_between_rk4_and_rk38_and_when_only_c_differs():
    cl = classical()
    assert content_hash(cl["rk4"]) != content_hash(cl["rk38"])
    rk4 = cl["rk4"]
    c_changed = Tableau(A=rk4.A, b=rk4.b, c=(rk4.c[0], F(1, 3), rk4.c[2], rk4.c[3]))
    assert content_hash(c_changed) != content_hash(rk4)
    assert canonical(c_changed) != canonical(rk4)
    hashes = {content_hash(t) for t in cl.values()}
    assert len(hashes) == 8


def test_B6_canonical_euler_literal():
    assert canonical(classical()["euler"]) == '{"A":[["0/1"]],"b":["1/1"],"c":["0/1"]}'
    assert canonical(_tab([[0]], [1], [0])) == '{"A":[["0/1"]],"b":["1/1"],"c":["0/1"]}'


@pytest.mark.parametrize("name", CLASSICAL_NAMES)
def test_B6_canonical_matches_handoff_4_11_formula_and_hash_is_sha256(name):
    t = classical()[name]
    expected = json.dumps(
        {
            "A": [[f"{x.numerator}/{x.denominator}" for x in row] for row in t.A],
            "b": [f"{x.numerator}/{x.denominator}" for x in t.b],
            "c": [f"{x.numerator}/{x.denominator}" for x in t.c],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    assert canonical(t) == expected
    assert content_hash(t) == hashlib.sha256(expected.encode("utf-8")).hexdigest()


def test_B6_canonical_kutta3_renders_negative_and_integer_entries():
    s = canonical(classical()["kutta3"])
    assert '"-1/1"' in s
    assert '"2/1"' in s
    assert '"1/6"' in s
    assert " " not in s


def test_B7_make_tableau_defaults_c_to_row_sums():
    t = make_tableau([[0, 0], ["1/2", 0]], [0, 1])
    assert isinstance(t, Tableau)
    assert t.c == (F(0), F(1, 2))
    assert t.A == ((F(0), F(0)), (F(1, 2), F(0)))
    assert t.b == (F(0), F(1))
    assert isinstance(t.A, tuple) and all(isinstance(row, tuple) for row in t.A)
    assert isinstance(t.b, tuple) and isinstance(t.c, tuple)
    for x in _all_entries(t):
        assert isinstance(x, Fraction)
    assert t == classical()["midpoint"]


def test_B7_make_tableau_honours_explicit_c_and_mixed_entry_forms():
    t = make_tableau([[0, 0, 0], ["1/2", 0, 0], [-1, "2", 0]], [F(1, 6), "2/3", "1/6"], [0, "1/2", 1])
    assert t == classical()["kutta3"]
    ovf = make_tableau([[0, 0], [100, 0]], [1, 0], [0, 100])
    assert ovf.c == (F(0), F(100))
    assert ovf.A[1][0] == F(100)
    neg = make_tableau([[0, 0], ["-1/3", 0]], ["1", "0"])
    assert neg.A[1][0] == F(-1, 3)
    assert neg.c == (F(0), F(-1, 3))
    assert neg.b == (F(1), F(0))


@pytest.mark.parametrize(
    "A,b,c",
    [
        ([[0, 0], [0.5, 0]], [0, 1], None),
        ([[0, 0], [1, 0]], [0.5, 0.5], None),
        ([[0, 0], [1, 0]], [0, 1], [0, 1.0]),
        ([[0.0]], [1], [0]),
    ],
    ids=["float-in-A", "float-in-b", "float-in-c", "float-zero-in-A"],
)
def test_B7_make_tableau_float_entry_raises_TypeError(A, b, c):
    with pytest.raises(TypeError):
        if c is None:
            make_tableau(A, b)
        else:
            make_tableau(A, b, c)


@pytest.mark.parametrize("name", CLASSICAL_NAMES)
def test_B7_json_roundtrip_all_classical(name):
    t = classical()[name]
    d = to_json(t)
    assert isinstance(d, dict)
    assert set(d.keys()) == {"A", "b", "c"}
    assert from_json(d) == t
    assert from_json(json.loads(json.dumps(d))) == t
    # to_json uses the same strings as canonical
    assert json.dumps(d, sort_keys=True, separators=(",", ":")) == canonical(t)


def test_B7_to_json_shape_euler():
    assert to_json(classical()["euler"]) == {"A": [["0/1"]], "b": ["1/1"], "c": ["0/1"]}
    d = to_json(classical()["rk4"])
    assert d["b"] == ["1/6", "1/3", "1/3", "1/6"]
    assert d["c"] == ["0/1", "1/2", "1/2", "1/1"]
    assert d["A"][3] == ["0/1", "0/1", "1/1", "0/1"]


def test_B7_from_json_accepts_short_strings_ints_and_fractions():
    d = {"A": [["0", 0], ["1", F(0)]], "b": ["1/2", F(1, 2)], "c": [0, "1"]}
    t = from_json(d)
    assert t == classical()["heun2"]
    for x in _all_entries(t):
        assert isinstance(x, Fraction)
    d2 = {"A": [["0", "0"], ["-1/3", "0"]], "b": ["1", "0"], "c": ["0", "-1/3"]}
    assert from_json(d2).A[1][0] == F(-1, 3)


@pytest.mark.parametrize("name", CLASSICAL_NAMES)
def test_B7_from_json_of_fixture_entry_equals_classical(name):
    fx = _CLASSICAL_FIXTURE[name]
    t = from_json({"A": fx["A"], "b": fx["b"], "c": fx["c"]})
    assert t == classical()[name]
    assert t == _fixture_tableau(name)


def test_B8_classical_has_exactly_eight_named_entries():
    cl = classical()
    assert isinstance(cl, dict)
    assert len(cl) == 8
    assert set(cl.keys()) == set(CLASSICAL_NAMES)
    assert not any(k.startswith("_") for k in cl)
    for t in cl.values():
        assert isinstance(t, Tableau)
        for x in _all_entries(t):
            assert isinstance(x, Fraction)


@pytest.mark.parametrize("name", CLASSICAL_NAMES)
def test_B8_classical_predicates_match_fixture(name):
    fx = _CLASSICAL_FIXTURE[name]
    t = classical()[name]
    assert stages(t) == fx["stages"]
    assert row_sums_consistent(t) is True
    assert is_explicit(t) is True
    assert all_dyadic(t) is fx["all_dyadic"]
    assert fx["row_sums_ok"] is True


@pytest.mark.parametrize("name", CLASSICAL_NAMES)
def test_B8_classical_equals_inline_handoff_and_fixture_constructions(name):
    loaded = classical()[name]
    inline = _inline_classical()[name]
    direct = _fixture_tableau(name)
    assert loaded == inline
    assert loaded == direct
    assert inline == direct


def test_B8_all_dyadic_known_answers():
    cl = classical()
    assert all_dyadic(cl["midpoint"]) is True
    assert all_dyadic(cl["heun2"]) is True
    assert all_dyadic(cl["euler"]) is True
    assert all_dyadic(cl["rk4"]) is False
    assert all_dyadic(cl["rk38"]) is False
    assert all_dyadic(cl["ralston2"]) is False
    assert all_dyadic(_tab([[0, 0], ["3/8", 0]], ["5/8", "3/8"], [0, "3/8"])) is True
    assert all_dyadic(_tab([[0, 0], ["1/2", 0]], ["1/2", "1/2"], [0, "1/3"])) is False  # c counts
    assert all_dyadic(_tab([[0, 0], ["1/2", 0]], ["1/3", "2/3"], [0, "1/2"])) is False  # b counts
    assert all_dyadic(_tab([[0, 0], ["1/5", 0]], ["1/2", "1/2"], [0, "1/5"])) is False  # A counts


def test_B8_row_sums_inconsistent_detected():
    rk4 = classical()["rk4"]
    bad = Tableau(A=rk4.A, b=rk4.b, c=(rk4.c[0], F(1, 3), rk4.c[2], rk4.c[3]))
    assert row_sums_consistent(bad) is False
    ovf_bad_c = _tab([[0, 0], [100, 0]], [1, 0], [0, 99])
    assert row_sums_consistent(ovf_bad_c) is False
    ovf = _tab([[0, 0], [100, 0]], [1, 0], [0, 100])
    assert row_sums_consistent(ovf) is True


def test_B8_row_sums_consistent_rejects_length_mismatch_and_non_square():
    A = ((F(0), F(0)), (F(1), F(0)))
    assert row_sums_consistent(Tableau(A=A, b=(F(1),), c=(F(0), F(1)))) is False
    assert row_sums_consistent(Tableau(A=A, b=(F(1, 2), F(1, 2)), c=(F(0),))) is False
    non_square = ((F(0), F(0)), (F(1), F(0)), (F(0), F(0)))
    assert row_sums_consistent(Tableau(A=non_square, b=(F(1), F(0), F(0)), c=(F(0), F(1), F(0)))) is False


@pytest.mark.parametrize(
    "A",
    [
        [[0, "1/2"], [0, 0]],  # above diagonal
        [["1/2", 0], [0, 0]],  # on diagonal
        [[0, 0], [1, "1/2"]],  # on diagonal, last row
        [[0, 0, "1/8"], ["1/2", 0, 0], [0, "1/2", 0]],  # far above diagonal
    ],
    ids=["above", "diag0", "diag1", "far-above"],
)
def test_B8_not_explicit_detected(A):
    n = len(A)
    t = _tab(A, [1] + [0] * (n - 1), [sum(F(x) for x in row) for row in A])
    assert is_explicit(t) is False


def test_B8_is_explicit_false_for_non_square_A():
    non_square = ((F(0), F(0)), (F(1), F(0)), (F(0), F(0)))
    t = Tableau(A=non_square, b=(F(1), F(0), F(0)), c=(F(0), F(1), F(0)))
    assert is_explicit(t) is False
    wide = ((F(0), F(0), F(0)), (F(1), F(0), F(0)))
    t2 = Tableau(A=wide, b=(F(1), F(0)), c=(F(0), F(1)))
    assert is_explicit(t2) is False


def test_B8_stages_counts_rows():
    cl = classical()
    assert stages(cl["euler"]) == 1
    assert stages(cl["heun2"]) == 2
    assert stages(cl["kutta3"]) == 3
    assert stages(cl["rk4"]) == 4
    assert stages(_tab([[0] * 6 for _ in range(6)], [1, 0, 0, 0, 0, 0], [0] * 6)) == 6


def test_B9_to_q15_exact_true_for_a_fully_representable_tableau():
    t = _tab([[0, 0], ["1/2", 0]], ["1/4", "3/4"], [0, "1/2"])
    q = to_q15(t)
    assert isinstance(q, Q15Tableau)
    assert q.exact is True
    assert q.A == ((0, 0), (16384, 0))
    assert q.b == (8192, 24576)
    assert q.c == (0, 16384)
    for row in q.A:
        for v in row:
            assert isinstance(v, int) and not isinstance(v, bool)


def test_B9_to_q15_rk4_is_not_exact_and_rounds_half_to_even():
    q = to_q15(classical()["rk4"])
    assert q.exact is False
    assert q.A[1][0] == 16384
    assert q.A[2][1] == 16384
    assert q.b[0] == 5461  # round(32768/6 = 5461.33)
    assert q.b[1] == 10923  # round(32768/3 = 10922.67)
    assert q.b == (5461, 10923, 10923, 5461)
    assert q.c[1] == 16384


def test_B9_to_q15_kutta3_clamps_two_to_int16_max():
    q = to_q15(classical()["kutta3"])
    assert q.A[2][1] == 32767
    assert q.A[2][0] == -32768
    assert q.A[1][0] == 16384
    assert q.exact is False
    assert all(Q15_MIN <= v <= Q15_MAX for row in q.A for v in row)
    assert all(Q15_MIN <= v <= Q15_MAX for v in q.b)
    assert all(Q15_MIN <= v <= Q15_MAX for v in q.c)


def test_B9_to_q15_clamps_one_and_large_negative_values():
    q = to_q15(classical()["heun2"])
    assert q.A[1][0] == 32767  # 1 * 32768 clamped
    assert q.b == (16384, 16384)
    big = _tab([[0, 0], [100, 0]], [1, 0], [0, 100])
    qb = to_q15(big)
    assert qb.A[1][0] == 32767
    assert qb.c[1] == 32767
    assert qb.exact is False
    neg = _tab([[0, 0], [-100, 0]], [1, 0], [0, -100])
    qn = to_q15(neg)
    assert qn.A[1][0] == -32768
    assert qn.exact is False


def test_B9_to_q15_third_is_inexact():
    t = _tab([[0, 0], ["1/3", 0]], ["1/2", "1/2"], [0, "1/3"])
    q = to_q15(t)
    assert q.exact is False
    assert q.A[1][0] == 10923


# ===========================================================================
# Cost model
# ===========================================================================


def _known_sequence_lines() -> list[str]:
    return (FIXTURES_DIR / "known_sequence.s").read_text().splitlines()


def test_C1_count_sequence_known_sequence_fast_and_slow():
    lines = _known_sequence_lines()
    assert len(lines) == 10
    fast = count_sequence(lines, M0PLUS_FAST)
    slow = count_sequence(lines, M0PLUS_SLOW)
    assert fast == 13
    assert slow == 75
    assert isinstance(fast, int) and not isinstance(fast, bool)
    assert isinstance(slow, int) and not isinstance(slow, bool)


def test_C1_count_sequence_known_sequence_avr_from_mapping():
    # 2 LDR (2 each) + 2 MULS (14) + 2 ASRS (8) + ADDS (2) + LSLS (8) + SUBS (2) + STR (2)
    assert count_sequence(_known_sequence_lines(), AVR_APPROX) == 62


def test_C2_rk4_slow_strictly_greater_than_fast():
    rk4 = classical()["rk4"]
    assert cycle_count(rk4, M0PLUS_SLOW, 1) > cycle_count(rk4, M0PLUS_FAST, 1)
    inline = _inline_classical()["rk4"]
    assert cycle_count(inline, M0PLUS_SLOW, 1) > cycle_count(inline, M0PLUS_FAST, 1)


def test_C2_cost_model_constants_and_registry():
    assert isinstance(M0PLUS_FAST, CostModel)
    assert M0PLUS_FAST == CostModel("m0plus_fast", {"mul": 1, "add": 1, "shift": 1, "load": 2, "store": 2})
    assert M0PLUS_SLOW == CostModel("m0plus_slow", {"mul": 32, "add": 1, "shift": 1, "load": 2, "store": 2})
    assert AVR_APPROX == CostModel("avr_approx", {"mul": 14, "add": 2, "shift": 8, "load": 2, "store": 2})
    assert set(COST_MODELS.keys()) == {"m0plus_fast", "m0plus_slow", "avr_approx"}
    assert COST_MODELS["m0plus_fast"] == M0PLUS_FAST
    assert COST_MODELS["m0plus_slow"] == M0PLUS_SLOW
    assert COST_MODELS["avr_approx"] == AVR_APPROX
    for name, model in COST_MODELS.items():
        assert model.name == name


def test_C3_low_csd_weight_is_strictly_cheaper_under_slow_model():
    assert coeff_cost(F(1, 2), M0PLUS_SLOW) < coeff_cost(F(16385, 32768), M0PLUS_SLOW)
    assert coeff_cost(F(1, 2), M0PLUS_FAST) < coeff_cost(F(16385, 32768), M0PLUS_FAST)
    # The slow-model gap is the thesis: CSD expansion beats a 32-cycle multiply.
    assert coeff_cost(F(1, 3), M0PLUS_SLOW) > coeff_cost(F(1, 3), M0PLUS_FAST)


@pytest.mark.parametrize(
    "x,model,expected",
    [
        (F(1, 2), M0PLUS_SLOW, 1),
        (F(1, 2), M0PLUS_FAST, 1),
        (F(1, 2), AVR_APPROX, 8),
        (F(16385, 32768), M0PLUS_SLOW, 3),
        (F(16385, 32768), M0PLUS_FAST, 2),
        (F(3, 8), M0PLUS_SLOW, 3),
        (F(3, 8), M0PLUS_FAST, 2),
        (F(3, 8), AVR_APPROX, 18),
        (F(3, 4), M0PLUS_SLOW, 3),
        (F(1, 3), M0PLUS_SLOW, 15),
        (F(1, 3), M0PLUS_FAST, 2),
        (F(1, 3), AVR_APPROX, 22),
        (F(-1, 3), M0PLUS_SLOW, 15),
        (F(1, 6), M0PLUS_SLOW, 15),
        (F(2, 3), M0PLUS_SLOW, 15),
        (F(2), M0PLUS_SLOW, 1),
        (F(2), M0PLUS_FAST, 1),
        (F(2), AVR_APPROX, 8),
        (F(1, 8), M0PLUS_SLOW, 1),
    ],
    ids=lambda v: (v.name if isinstance(v, CostModel) else str(v)),
)
def test_C3_coeff_cost_is_min_of_csd_and_multiply_paths(x, model, expected):
    got = coeff_cost(x, model)
    assert got == expected
    assert isinstance(got, int) and not isinstance(got, bool)


@pytest.mark.parametrize("model", [M0PLUS_FAST, M0PLUS_SLOW, AVR_APPROX], ids=lambda m: m.name)
def test_C4_zero_coefficient_costs_nothing(model):
    assert coeff_cost(F(0), model) == 0


@pytest.mark.parametrize("model", [M0PLUS_FAST, M0PLUS_SLOW, AVR_APPROX], ids=lambda m: m.name)
@pytest.mark.parametrize("x", [F(1), F(-1)], ids=["+1", "-1"])
def test_C5_unit_coefficient_costs_nothing(x, model):
    assert coeff_cost(x, model) == 0


def test_C6_cycle_count_is_deterministic():
    cl = classical()
    for name in CLASSICAL_NAMES:
        for model in (M0PLUS_FAST, M0PLUS_SLOW, AVR_APPROX):
            first = cycle_count(cl[name], model, 1)
            second = cycle_count(cl[name], model, 1)
            assert first == second
    rk4 = cl["rk4"]
    assert cycle_count(rk4, M0PLUS_SLOW, 3) == cycle_count(rk4, M0PLUS_SLOW, 3)


def test_C7_rk38_cheaper_than_rk4_under_slow_model():
    cl = classical()
    slow_rk38 = cycle_count(cl["rk38"], M0PLUS_SLOW, 1)
    slow_rk4 = cycle_count(cl["rk4"], M0PLUS_SLOW, 1)
    assert slow_rk38 == 64
    assert slow_rk4 == 85
    assert slow_rk38 < slow_rk4


def test_C7b_rk4_cheaper_than_rk38_under_fast_model_reversal():
    cl = classical()
    fast_rk4 = cycle_count(cl["rk4"], M0PLUS_FAST, 1)
    fast_rk38 = cycle_count(cl["rk38"], M0PLUS_FAST, 1)
    assert fast_rk4 == 33
    assert fast_rk38 == 36
    assert fast_rk4 < fast_rk38
    # And the same reversal from the inline HANDOFF coefficients.
    inline = _inline_classical()
    assert cycle_count(inline["rk4"], M0PLUS_FAST, 1) < cycle_count(inline["rk38"], M0PLUS_FAST, 1)
    assert cycle_count(inline["rk38"], M0PLUS_SLOW, 1) < cycle_count(inline["rk4"], M0PLUS_SLOW, 1)


def test_C8_all_classical_computable_under_avr_and_fast():
    cl = classical()
    avr = {}
    fast = {}
    for name in CLASSICAL_NAMES:
        a = cycle_count(cl[name], AVR_APPROX, 1)
        f = cycle_count(cl[name], M0PLUS_FAST, 1)
        assert isinstance(a, int) and not isinstance(a, bool)
        assert isinstance(f, int) and not isinstance(f, bool)
        assert a > 0 and f > 0
        avr[name] = a
        fast[name] = f
    ranked_avr = sorted(CLASSICAL_NAMES, key=lambda n: (avr[n], n))
    ranked_fast = sorted(CLASSICAL_NAMES, key=lambda n: (fast[n], n))
    assert ranked_avr[0] == "euler"
    assert ranked_fast[0] == "euler"
    # Derived by hand from the section 4.5 rules with AVR costs.
    assert avr["euler"] == 6
    assert avr["midpoint"] == 20
    assert avr["heun2"] == 30


def test_C9_costmodel_source_does_not_contain_is_dyadic():
    src = (PACKAGE_DIR / "costmodel.py").read_text(encoding="utf-8")
    assert len(src) > 0
    assert "is_dyadic" not in src


@pytest.mark.parametrize("model", [M0PLUS_FAST, M0PLUS_SLOW], ids=lambda m: m.name)
@pytest.mark.parametrize("n", [1, 2, 4])
@pytest.mark.parametrize("name", CLASSICAL_NAMES)
def test_C10_cycle_count_scales_linearly_in_n_states(name, n, model):
    t = classical()[name]
    assert cycle_count(t, model, n) == n * cycle_count(t, model, 1)


def test_C10_all_zero_b_still_costs_load_and_store_and_scales():
    t = _tab([[0, 0], ["1/2", 0]], [0, 0], [0, "1/2"])
    # stage 1: load 2 + (coeff 1 + add 1) + store 2 = 6; b: load 2 + store 2 = 4
    assert cycle_count(t, M0PLUS_FAST, 1) == 10
    assert cycle_count(t, M0PLUS_SLOW, 1) == 10
    assert cycle_count(t, M0PLUS_FAST, 2) == 20
    assert cycle_count(t, M0PLUS_FAST, 4) == 40
    assert cycle_count(t, AVR_APPROX, 1) == 2 + (8 + 2) + 2 + 2 + 2


def test_C10_all_zero_stage_rows_are_skipped():
    # Two all-zero rows and a trivial b: only the b combination costs anything.
    t = _tab([[0, 0], [0, 0]], [1, 0], [0, 0])
    assert cycle_count(t, M0PLUS_FAST, 1) == 5
    assert cycle_count(t, M0PLUS_SLOW, 1) == 5
    assert cycle_count(t, M0PLUS_FAST, 3) == 15


@pytest.mark.parametrize("name", CLASSICAL_NAMES)
def test_G20_tableau_csd_total_matches_fixture(name):
    assert tableau_csd_total(classical()[name]) == _CLASSICAL_FIXTURE[name]["csd_total"]
    assert tableau_csd_total(_inline_classical()[name]) == _CLASSICAL_FIXTURE[name]["csd_total"]


def test_G20_known_totals_rk4_and_rk38():
    cl = classical()
    assert tableau_csd_total(cl["rk4"]) == 34
    assert tableau_csd_total(cl["rk38"]) == 22
    assert tableau_csd_total(cl["euler"]) == 0


def test_G21_fixture_has_twelve_anchor_values():
    assert len(_ANCHOR_ROWS) == 12
    assert {r[0] for r in _ANCHOR_ROWS} == {"rk4", "rk38"}
    assert {r[1] for r in _ANCHOR_ROWS} == {1, 2, 4}
    assert {r[2] for r in _ANCHOR_ROWS} == {"m0plus_fast", "m0plus_slow"}


@pytest.mark.parametrize(
    "name,n,model_name,expected",
    _ANCHOR_ROWS,
    ids=[f"{r[0]}-n{r[1]}-{r[2]}" for r in _ANCHOR_ROWS],
)
def test_G21_anchor_cycles(name, n, model_name, expected):
    model = COST_MODELS[model_name]
    assert cycle_count(classical()[name], model, n) == expected
    assert cycle_count(_inline_classical()[name], model, n) == expected
    assert cycle_count(_fixture_tableau(name), model, n) == expected


def test_G21_anchor_ordering_reverses_between_models_at_every_n():
    cl = classical()
    for n in (1, 2, 4):
        assert cycle_count(cl["rk4"], M0PLUS_FAST, n) < cycle_count(cl["rk38"], M0PLUS_FAST, n)
        assert cycle_count(cl["rk38"], M0PLUS_SLOW, n) < cycle_count(cl["rk4"], M0PLUS_SLOW, n)


@pytest.mark.parametrize("model_key,fixture_key", [("m0plus_fast", "cycles_fast"), ("m0plus_slow", "cycles_slow")])
@pytest.mark.parametrize("name", CLASSICAL_NAMES)
def test_G22_classical_cycles_at_n1_match_fixture(name, model_key, fixture_key):
    expected = _CLASSICAL_FIXTURE[name][fixture_key]
    model = COST_MODELS[model_key]
    assert cycle_count(classical()[name], model, 1) == expected
    assert cycle_count(_inline_classical()[name], model, 1) == expected


@pytest.mark.parametrize("line", ["FOO r0", "NOP", "MOV r0, r1", "BX lr", "    foo r0"])
def test_B14_unknown_mnemonic_raises_ValueError(line):
    with pytest.raises(ValueError):
        count_sequence([line], M0PLUS_FAST)


def test_B14_unknown_mnemonic_raises_even_after_valid_lines():
    with pytest.raises(ValueError):
        count_sequence(["LDR r0, [r4]", "MULS r0, r0, r1", "FOO r0"], M0PLUS_SLOW)


def test_B14_blank_and_comment_only_lines_cost_zero():
    assert count_sequence([], M0PLUS_FAST) == 0
    assert count_sequence(["", "   ", "\t"], M0PLUS_FAST) == 0
    assert count_sequence(["@ only a comment", "  @ MULS r0, r0, r1", "\t@ FOO"], M0PLUS_FAST) == 0
    assert count_sequence(["", "@ x", "   ", "@ FOO BAR"], AVR_APPROX) == 0


def test_B14_comment_stripping_and_case_insensitive_mnemonics():
    assert count_sequence(["STR r2, [r6, #0] @ FOO BAR"], M0PLUS_FAST) == 2
    assert count_sequence(["ldr r0, [r4]"], M0PLUS_FAST) == 2
    assert count_sequence(["Muls r0, r0, r1"], M0PLUS_SLOW) == 32
    assert count_sequence(["MULS r0, r0, r1"], M0PLUS_FAST) == 1
    assert count_sequence(["\tLDR\tr0, [r4]"], M0PLUS_FAST) == 2
    assert count_sequence(["LSRS r0, r0, #1", "LSLS r1, r1, #2", "ASRS r2, r2, #3"], AVR_APPROX) == 24
    assert count_sequence(["ADDS r0, r0, r1", "SUBS r0, r0, r1"], AVR_APPROX) == 4
    assert count_sequence(["ADDS r0, r0, r1", "SUBS r0, r0, r1"], M0PLUS_FAST) == 2
    # Only the first whitespace token is the mnemonic.
    assert count_sequence(["ADDS MULS"], M0PLUS_SLOW) == 1


def test_B15_emit_c_rk4_contains_function_name_and_shift():
    src = emit_c(classical()["rk4"], 2)
    assert isinstance(src, str)
    assert "rk_step" in src
    assert ">> 15" in src
    assert "int16_t" in src
    assert "h_q" in src


def test_B15_emit_c_is_a_nonempty_string_for_every_classical():
    for name in CLASSICAL_NAMES:
        src = emit_c(classical()[name], 1)
        assert isinstance(src, str)
        assert len(src) > 0
        assert "rk_step" in src
