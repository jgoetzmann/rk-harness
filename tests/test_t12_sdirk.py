"""T12: SDIRK prototype (rk_harness/prototypes/sdirk.py).

Float-only, off-archive; nothing pinned is touched. Covers: order 2 on dahlquist,
L-stability where rk4 blows up, the closed-form stability function, the fixed
Newton iteration count (deterministic cycle cost), the one-sided FD Jacobian,
and the sdirk_curve.json artifact schema.
"""
from __future__ import annotations

import json
import math

from rk_harness.paths import work_dir
from rk_harness.prototypes import sdirk
from rk_harness.simulate import solve_float
from rk_harness.tableau import classical


def _dahlquist_rhs(t, y):
    return (-y[0],)


def _dahlquist_jac(t, y):
    return [[-1.0]]


def test_order_two_on_dahlquist():
    t_end = 5.0
    errs = []
    for n in (40, 80, 160):
        y = sdirk.solve_sdirk2(_dahlquist_rhs, (1.0,), t_end, n, jac=_dahlquist_jac)
        errs.append(abs(y[0] - math.exp(-t_end)))
    for e_n, e_2n in zip(errs, errs[1:]):
        slope = math.log2(e_n / e_2n)
        assert 1.8 < slope < 2.2, f"halving h gave slope {slope}, expected ~2"


def test_l_stable_large_step_where_rk4_diverges():
    # h = 0.1 puts the fast rate at z = -100: far outside every explicit stability
    # region, comfortably inside the SDIRK one.
    n = 20
    y_sd = sdirk.solve_sdirk2(sdirk.stiff_rhs, sdirk.STIFF_Y0, sdirk.STIFF_T_END, n,
                              jac=sdirk.stiff_jac)
    ref = sdirk.stiff_reference(sdirk.STIFF_T_END)
    err_sd = math.sqrt(sum((a - b) ** 2 for a, b in zip(y_sd, ref)))
    assert all(math.isfinite(v) for v in y_sd)
    assert err_sd < 0.05

    y_rk4 = solve_float(classical()["rk4"], sdirk.stiff_rhs, sdirk.STIFF_Y0,
                        sdirk.STIFF_T_END, n)
    norm = sum(abs(v) for v in y_rk4)
    assert not math.isfinite(norm) or norm > 1.0e3


def test_stability_function_closed_form_and_l_stability():
    # One step at h = 1 on y' = z*y from y = 1 must reproduce R(z): the problem is
    # linear, the frozen Jacobian is exact, so the fixed iteration solves the stage
    # equations to rounding.
    for z in (-0.5, -5.0, -50.0):
        (r_num,) = sdirk.sdirk2_step(lambda t, y: (z * y[0],), 0.0, (1.0,), 1.0,
                                     jac=lambda t, y: [[z]])
        assert abs(r_num - sdirk.stability_function(z)) < 1.0e-12
    # A-stability samples on the negative real axis, and decay at -infinity.
    for z in (-0.1, -1.0, -10.0, -1.0e4):
        assert abs(sdirk.stability_function(z)) < 1.0
    assert abs(sdirk.stability_function(-1.0e8)) < 1.0e-6


def test_newton_iteration_count_fixed():
    calls = {"n": 0}

    def counted(t, y):
        calls["n"] += 1
        return sdirk.stiff_rhs(t, y)

    steps = 7
    # Analytic Jacobian: exactly stages * NEWTON_ITERS rhs evaluations per step,
    # independent of the data.
    for y0 in ((1.0, 1.0), (0.25, -0.5)):
        calls["n"] = 0
        sdirk.solve_sdirk2(counted, y0, sdirk.STIFF_T_END, steps, jac=sdirk.stiff_jac)
        assert calls["n"] == steps * sdirk.STAGES * sdirk.NEWTON_ITERS
    # FD Jacobian adds 1 + n_states evaluations per step, still a fixed count.
    calls["n"] = 0
    sdirk.solve_sdirk2(counted, (1.0, 1.0), sdirk.STIFF_T_END, steps, jac=None)
    assert calls["n"] == steps * (sdirk.STAGES * sdirk.NEWTON_ITERS + 1 + 2)


def test_fd_jacobian_matches_analytic():
    jac = sdirk.fd_jacobian(sdirk.stiff_rhs, 0.0, (0.7, -0.3))
    exact = sdirk.stiff_jac(0.0, (0.7, -0.3))
    for i in range(2):
        for j in range(2):
            tol = 1.0e-3 * max(1.0, abs(exact[i][j]))
            assert abs(jac[i][j] - exact[i][j]) <= tol


def test_curve_artifact_schema_and_claims():
    sdirk.main()   # conftest points RK_WORK_DIR at a throwaway dir
    path = work_dir() / "prototypes" / "sdirk_curve.json"
    with open(path, "r", encoding="utf-8") as fh:
        d = json.load(fh)

    assert "preliminary" in d["label"] and "float-only" in d["label"]
    assert d["method"]["newton_iters"] == sdirk.NEWTON_ITERS
    assert d["cost_model"] == "m0plus_fast"
    assert set(d["problems"]) == {"rc_thermal", "stiff_two_rate"}

    for pname, prob in d["problems"].items():
        assert set(prob["methods"]) == {"euler", "rk4", "sdirk2"}
        for mname, m in prob["methods"].items():
            assert isinstance(m["est_cycles_per_step"], int) and m["est_cycles_per_step"] > 0
            assert m["f_evals_per_step"] > 0
            for p in m["points"]:
                assert set(p) == {"n", "h", "error", "status"}
                assert p["status"] in ("ok", "diverged")
                assert (p["error"] is None) == (p["status"] == "diverged")
        # The claim the artifact exists to document: SDIRK2 is clean at every
        # step size tried while the explicit methods destabilize on coarse grids.
        sd = prob["methods"]["sdirk2"]
        assert all(p["status"] == "ok" for p in sd["points"])
        assert sd["min_stable_n"] < prob["methods"]["rk4"]["min_stable_n"]
        assert sd["min_stable_n"] < prob["methods"]["euler"]["min_stable_n"]

    stiff = d["problems"]["stiff_two_rate"]["methods"]
    assert any(p["status"] == "diverged" for p in stiff["rk4"]["points"])
    assert any(p["status"] == "diverged" for p in stiff["euler"]["points"])
    assert "cycles/step" in d["cost_note"]
