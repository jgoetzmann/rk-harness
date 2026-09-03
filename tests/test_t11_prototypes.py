"""T11 tests: off-archive prototypes for future epochs (rk_harness/prototypes/).

Two sections share this file, one per side track:

* adaptive (side track B, epoch 2): tests named test_B2x_* below, covering the
  Bogacki-Shampine 3(2) embedded pair, the dyadic PI step controller, and the
  work-precision curve artifact from rk_harness/prototypes/adaptive.py.
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
# --- SDIRK (side track C): added by a later stage below this line ---------- #
# =========================================================================== #
