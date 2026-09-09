"""Library benchmark with measured wall clock (T10, lead track part 2).

Evaluates, on the seven frozen scored problems from ``rk_harness.problems``:

* SciPy ``solve_ivp`` with RK45, Radau, BDF and LSODA at tolerances matched to
  the Q15 error scale (matching rule: ``rtol = 2**-15`` and ``atol = 2**-15 /
  scale``, one Q15 least significant bit, the atol expressed in physical units),
* a hand-rolled float64 fixed-step rk4 at the step counts the 65,536-cycle
  budget implies for each Q15 method,
* the Q15 champion methods and every classical anchor (euler, heun2,
  midpoint, rk4, rk38) through the pinned ``solve_q15`` machinery, with
  tableaus fetched from ``rk-work/validation/results.json``
  ``methods[].tableau`` (exact fraction strings; discovered hashes re-verified
  against ``tableau.content_hash``).

For every (method, problem) cell: the final-state error against the harness
reference via ``problems.error_metric``, and measured wall clock as the median
and interquartile range of at least ``N_REPEATS`` repeated runs of
``time.perf_counter`` after ``N_WARMUP`` warmups, single process, thread caps
exported before NumPy or SciPy load.

Honesty rules baked into the document:

* adaptive integrators choose their own steps, so their wall clock is never a
  same-work comparison; the two result tables keep the regimes apart
  (``adaptive_results`` for accuracy at matched tolerance with wall clock,
  ``fixed_step_results`` for float64 rk4 against the Q15 methods at identical
  step counts),
* every timing is Python-level and compares like against like only,
* everything in the document is deterministic except the timing numbers,
  which are measured; nothing random is seeded because nothing random runs.

The analytic cycle model is checked against reality with a Pearson correlation
between analytic cycles per step (m0plus_fast) and measured seconds per step
across the fixed-step Q15 runs.

The ``speedup`` section makes the wall-clock claim concrete as an explicit
measured head-to-head: for every problem, the champion tableau and classical
rk4 through the identical pinned ``solve_q15`` path (same arithmetic, only the
tableau differs), with measured seconds per step, their ratio next to the
cycle-model predicted ratio, seconds to complete the shared budget, the Q15
errors cited from the fixed-step table, a geometric-mean measured speedup, and
per-method median microseconds per step for direct charting.

THE THREE-CLASS SECTIONS. Everything above is explicit against float or against
a library reference: the two tables are disjoint in who they measure rather than
being two views of one comparison, and no row carries a side. The sections
``matched_accuracy``, ``adaptive_matched_tolerance``, ``adaptive_pairs`` and
``implicit_budget`` add the missing half, on the eight application problems,
whose three stiff members (stiffness ratios 546.4, 1030.0 and 292.0) are the
regime an implicit method exists for and which this document had never seen.

Their shared condition is matched ACCURACY, not matched wall clock: ours runs
Q15 integer arithmetic through a Python loop that models a Cortex-M0+, theirs
runs float64 inside compiled SciPy on this desktop, and a microsecond from one
is not a microsecond from the other. Every row states what it controls for and
what it does not, in fields; every cycle number carries the grade of number it
is; and where a quantity cannot be made comparable it is null with a reason
rather than a plausible figure. The deterministic half of that work lives in
``rk_harness.benchcounts``, which reads no clock and writes no environment
variable; this module keeps the stopwatch, the environment stamp and the
document assembly.

WHERE THIS RUNS: on the host, on demand. Importing this module exports five
BLAS thread variables and pulls in numpy before anything else happens, and it
measures real time, which the container cannot do at --cpu-shares 256 under a
watchdog that pauses it (docs/SIDETRACK-AUTOMATION.md D9). Nothing the
container runs imports this module or benchcounts; the runner only commits the
output directory.
"""
from __future__ import annotations

import os

# Cap BLAS/OpenMP threads before NumPy or SciPy load so every timed run is
# single-threaded. Harmless if the libraries are already resident.
_THREAD_VARS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")
for _v in _THREAD_VARS:
    os.environ.setdefault(_v, "1")

import dataclasses
import gc
import json
import math
import platform
import re
import statistics
import time
from pathlib import Path

import numpy as np

from rk_harness import benchcounts as BC
from rk_harness.coeffmem import coefficient_memory
from rk_harness.costmodel import M0PLUS_FAST, cycle_count
from rk_harness.paths import work_dir
from rk_harness.problems import DERIV_SCALE, FLOAT_RHS, PROBLEMS, error_metric, load_fixture
from rk_harness.simulate import problem_error, steps_for_budget
from rk_harness.tableau import content_hash, from_json, to_json
from rk_harness.types import Tableau

try:  # degrade gracefully if the venv loses scipy
    import scipy
    from scipy.integrate import solve_ivp
    _SCIPY_VERSION: str | None = scipy.__version__
    _SCIPY_ERROR: str | None = None
except Exception as _exc:  # pragma: no cover - exercised only without scipy
    solve_ivp = None
    _SCIPY_VERSION = None
    _SCIPY_ERROR = f"{type(_exc).__name__}: {_exc}"

BUDGET_CYCLES = 65536
COST_MODEL = M0PLUS_FAST
N_REPEATS = 15
N_WARMUP = 3
# RK23 joins the reference table because it is Bogacki-Shampine 3(2), which is the
# pair our own adaptive prototype runs: the same tableau on the library side is the
# one comparison in this document where the method is held constant.
SCIPY_INTEGRATORS = ("RK45", "RK23", "Radau", "BDF", "LSODA")
PROBLEM_NAMES: tuple[str, ...] = tuple(PROBLEMS.keys())

TOLERANCE_RULE = (
    "rtol = 2**-15 and atol = 2**-15 / scale for every problem: one Q15 least "
    "significant bit as a relative tolerance, and one Q15 least significant bit "
    "converted to physical units (a Q15 state stores y * scale in steps of "
    "2**-15) as the absolute tolerance. The library integrators are therefore "
    "asked for local error at the resolution the Q15 representation can hold."
)

_BANNED = ("novel", "first", "beats", "outperforms", "breakthrough", "proves",
           "state-of-the-art", "best-ever")

_INF = float("inf")


# ------------------------------------------------------------------ tolerances


def tolerances(name: str) -> tuple[float, float]:
    """(rtol, atol) for a frozen problem under the matching rule above."""
    return (2.0 ** -15, 2.0 ** -15 / PROBLEMS[name].scale)


def physical_y0(name: str) -> tuple[float, ...]:
    """Exact physical initial state from fixtures/problems.json (the float
    baselines start here; the Q15 runs necessarily start from the quantized
    Problem.y0)."""
    return tuple(float(v) for v in load_fixture()[name]["y0"])


# ------------------------------------------------------------- champion fetch


def validation_results_path() -> Path:
    return work_dir() / "validation" / "results.json"


def load_validation_doc(path: Path | str | None = None) -> dict:
    p = Path(path) if path is not None else validation_results_path()
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def select_benchmark_methods(doc: dict) -> list[dict]:
    """Every method in a validation results document: all of its classical
    anchors plus every discovered method. Tableaus are parsed from the exact
    fraction strings; a discovered entry whose content hash disagrees with its
    recorded name is a provenance failure and raises ValueError.

    The classical set used to be narrowed to rk4 alone, which left the
    trade-offs matrix with four measured rows and five dashes. euler, heun2,
    midpoint and rk38 are already carried by the validation document with
    verified tableaus, so benchmarking them costs only host time and fills
    the cheap end of the cycle range the correlation is fitted over."""
    out: list[dict] = []
    for m in doc["methods"]:
        name = m["name_or_hash"]
        kind = m["kind"]
        # A validation document that carries implicit or adaptive entries (its
        # two-axis sibling does) must not feed them to this pool: every method
        # here is run through the explicit pinned solve_q15, so an SDIRK entry
        # would come back as a number labelled sdirk2 that is not SDIRK at all,
        # and an embedded pair would lose b_hat on the way in and be priced as a
        # plain fixed-step method. The derived fixed-step control is skipped for
        # the same reason: it is a control, not the adaptive method.
        if m.get("class", "explicit") != "explicit" or m.get("derived_fixed_step"):
            continue
        t = from_json(m["tableau"])
        if kind == "discovered" and content_hash(t) != name:
            raise ValueError(
                f"discovered method {name!r}: tableau hashes to {content_hash(t)!r}")
        entry = {
            "name_or_hash": name,
            "kind": kind,
            "roles": list(m.get("roles", [])),
            "order": int(m["order"]),
            "stages": int(m["stages"]),
            "tableau": t,
        }
        if "archive" in m:
            entry["archive"] = m["archive"]
        out.append(entry)
    if not any(e["name_or_hash"] == "rk4" for e in out):
        raise ValueError("validation results document carries no rk4 anchor")
    if not any(e["kind"] == "discovered" for e in out):
        raise ValueError("validation results document carries no discovered method")
    return out


# ------------------------------------------------------------ float64 rk4


def rk4_step_float(rhs, t: float, y: tuple[float, ...], h: float) -> tuple[float, ...]:
    """One classical rk4 step, hand-rolled in float64."""
    n = len(y)
    k1 = rhs(t, y)
    k2 = rhs(t + 0.5 * h, tuple(y[m] + 0.5 * h * k1[m] for m in range(n)))
    k3 = rhs(t + 0.5 * h, tuple(y[m] + 0.5 * h * k2[m] for m in range(n)))
    k4 = rhs(t + h, tuple(y[m] + h * k3[m] for m in range(n)))
    return tuple(y[m] + (h / 6.0) * (k1[m] + 2.0 * (k2[m] + k3[m]) + k4[m])
                 for m in range(n))


def solve_rk4_float(rhs, y0: tuple[float, ...], t_end: float, n: int) -> tuple[float, ...]:
    """n fixed rk4 steps of size t_end / n from t = 0."""
    h = t_end / n
    y = tuple(float(v) for v in y0)
    for k in range(n):
        y = rk4_step_float(rhs, k * h, y, h)
    return y


# ----------------------------------------------------------------- stopwatch


def measure(fn, n_repeats: int, warmup: int) -> dict:
    """{median_s, iqr_s, min_s, n, warmup} over n_repeats timed calls of fn()
    after warmup unrecorded calls. perf_counter, gc paused during the loop."""
    for _ in range(warmup):
        fn()
    times: list[float] = []
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        for _ in range(n_repeats):
            t0 = time.perf_counter()
            fn()
            t1 = time.perf_counter()
            times.append(t1 - t0)
    finally:
        if was_enabled:
            gc.enable()
    med = statistics.median(times)
    if len(times) >= 2:
        q = statistics.quantiles(times, n=4, method="inclusive")
        iqr = q[2] - q[0]
    else:
        iqr = 0.0
    return {"median_s": med, "iqr_s": iqr, "min_s": min(times),
            "n": len(times), "warmup": warmup}


def _fin(v):
    """Finite number or None (the document is written with allow_nan=False)."""
    return v if isinstance(v, (int, float)) and math.isfinite(v) else None


# ------------------------------------------------------------- adaptive rows


def adaptive_row(integrator: str, name: str, n_repeats: int, warmup: int) -> dict:
    """One (scipy integrator, problem) cell: accuracy at matched tolerance,
    accepted-step count, nfev, and measured wall clock."""
    rtol, atol = tolerances(name)
    row: dict = {"integrator": integrator, "problem": name,
                 "rtol": rtol, "atol": atol,
                 # RK45 and RK23 are explicit pairs under a step controller and
                 # Radau, BDF and LSODA solve an implicit system every step. The
                 # table used to file all of them under one heading that said
                 # adaptive, which is where the implicit library half went
                 # missing; the class is data now so a page can split them.
                 "class": BC.SCIPY_CLASS[integrator],
                 "side": "library",
                 "arithmetic": "compiled_float64",
                 "timing_family": "compiled_scipy",
                 "problem_set": "frozen",
                 "family": BC.LIBRARY_FAMILY[integrator]}
    if solve_ivp is None:
        row.update({"status": "skipped",
                    "reason": f"scipy unavailable: {_SCIPY_ERROR}"})
        return row
    rhs = FLOAT_RHS[name]
    p = PROBLEMS[name]
    y0 = physical_y0(name)

    def run():
        return solve_ivp(rhs, (0.0, p.t_end), y0, method=integrator,
                         rtol=rtol, atol=atol)

    try:
        sol = run()
    except Exception as exc:
        row.update({"status": "failed", "reason": f"{type(exc).__name__}: {exc}"})
        return row
    if not sol.success:
        row.update({"status": "failed", "reason": str(sol.message)})
        return row
    err = _fin(error_metric(name, tuple(float(v) for v in sol.y[:, -1])))
    n_acc = int(len(sol.t) - 1)
    timing = measure(run, n_repeats, warmup)
    row.update({
        "status": "ok",
        "error": err,
        "n_steps_accepted": n_acc,
        "nfev": int(sol.nfev),
        "timing": timing,
        "per_step_median_s": (timing["median_s"] / n_acc) if n_acc > 0 else None,
    })
    return row


# ----------------------------------------------------------- fixed-step rows


def fixed_step_row(method: dict, name: str, n_repeats: int, warmup: int) -> dict:
    """One (Q15 method, problem) cell at the budgeted step count, paired with
    hand-rolled float64 rk4 at the identical step count."""
    t: Tableau = method["tableau"]
    p = PROBLEMS[name]
    cps = cycle_count(t, COST_MODEL, p.n_states)
    n = steps_for_budget(t, COST_MODEL, p.n_states, BUDGET_CYCLES)
    row: dict = {"method": method["name_or_hash"], "problem": name,
                 "n_steps": n, "cycles_per_step": cps, "total_cycles": cps * n,
                 # Stated rather than implied, so a reader of one row knows which
                 # problem set and which side it came from without a join.
                 "class": "explicit", "side": "ours", "problem_set": "frozen"}
    if n <= 0:
        row["q15"] = {"status": "skipped",
                      "reason": "method too expensive for the budget"}
        row["float_rk4"] = {"status": "skipped",
                            "reason": "no budgeted step count to match"}
        return row

    q15: dict = {}
    try:
        err, max_q = problem_error(t, p, n)
        q15["error"] = _fin(err)
        q15["max_abs_q"] = int(max_q)
        q15["status"] = "ok" if q15["error"] is not None else "failed"
        if q15["error"] is None:
            q15["reason"] = "error metric not finite"
    except Exception as exc:
        q15 = {"status": "overflow", "error": None,
               "reason": f"{type(exc).__name__}: {exc}"}
    if q15["status"] == "ok":
        timing = measure(lambda: problem_error(t, p, n), n_repeats, warmup)
        q15["timing"] = timing
        q15["per_step_median_s"] = timing["median_s"] / n
    row["q15"] = q15

    rhs = FLOAT_RHS[name]
    y0 = physical_y0(name)
    fr: dict = {}
    try:
        yf = solve_rk4_float(rhs, y0, p.t_end, n)
        fr["error"] = _fin(error_metric(name, yf))
        fr["status"] = "ok" if fr["error"] is not None else "failed"
        if fr["error"] is None:
            fr["reason"] = "error metric not finite"
    except Exception as exc:
        fr = {"status": "failed", "error": None,
              "reason": f"{type(exc).__name__}: {exc}"}
    if fr["status"] == "ok":
        timing = measure(lambda: solve_rk4_float(rhs, y0, p.t_end, n),
                         n_repeats, warmup)
        fr["timing"] = timing
        fr["per_step_median_s"] = timing["median_s"] / n
    row["float_rk4"] = fr
    return row


# -------------------------------------------------------------- correlation


def cycles_time_correlation(fixed_rows: list[dict]) -> dict:
    """Pearson correlation between analytic cycles per step and measured median
    seconds per step across the fixed-step Q15 runs."""
    xs: list[float] = []
    ys: list[float] = []
    for r in fixed_rows:
        q = r.get("q15", {})
        if q.get("status") == "ok" and "per_step_median_s" in q:
            xs.append(float(r["cycles_per_step"]))
            ys.append(float(q["per_step_median_s"]))
    out = {
        "x": "analytic cycles per step under m0plus_fast (stages, states and "
             "coefficient CSD weight all included; derivative evaluation excluded)",
        "y": "measured median seconds per step of the Q15 fixed-step runs",
        "n_points": len(xs),
    }
    if len(xs) >= 3 and len(set(xs)) > 1 and len(set(ys)) > 1:
        out["pearson_r"] = _fin(statistics.correlation(xs, ys))
        out["median_s_per_cycle"] = _fin(statistics.median(
            y / x for x, y in zip(xs, ys)))
    else:
        out["pearson_r"] = None
        out["median_s_per_cycle"] = None
    return out


# ------------------------------------------------------------------- speedup


SPEEDUP_BASELINE = "rk4"

_SPEEDUP_REGIME = (
    "Fixed-step Q15 at the shared cycle budget: the champion and rk4 both run "
    "the identical pinned solve_q15 path with the same 16-bit floor "
    "arithmetic and the same per-step machinery; only the tableau "
    "coefficients differ, so the per-step wall-clock ratio isolates tableau "
    "cost. Ratios above 1.0 mean the champion needs less time per step."
)

_SPEEDUP_CAVEAT = (
    "This head-to-head is valid because both sides run the identical "
    "solve_q15 path in the same interpreter: same arithmetic, same per-step "
    "machinery, only the tableau differs, so the per-step ratio compares "
    "like against like. Budget wall-clock totals also carry constant Python "
    "per-step overhead, which favors methods that take fewer, larger steps "
    "inside the budget; on the Cortex-M0+ target the analytic cycle count is "
    "the time, and the predicted ratio column states what that model expects "
    "of the measured ratio beside it."
)


def champion_hash(methods: list[dict]) -> str:
    """The discovered method carrying the champion role (fallback: the first
    discovered entry)."""
    disc = [m for m in methods if m["kind"] == "discovered"]
    for m in disc:
        if "champion" in m.get("roles", []):
            return m["name_or_hash"]
    return disc[0]["name_or_hash"]


def _geomean(vals: list[float]):
    if not vals:
        return None
    return _fin(math.exp(sum(math.log(v) for v in vals) / len(vals)))


def per_method_us_per_step(fixed_rows: list[dict]) -> dict:
    """Absolute measured time per method: median microseconds per step across
    the problems whose Q15 run finished, plus the per-problem values, so a
    site can chart measured time directly.

    min/max carry the problem-to-problem spread; median_relative_iqr carries
    the repeat-to-repeat spread, the median over the same problems of
    iqr_s / median_s. The two answer different questions and a reader who has
    only the min-max range cannot tell a method that is genuinely cheaper on
    one problem from a machine that was noisy while it was timed."""
    per: dict[str, dict[str, float]] = {}
    rel_iqr: dict[str, list[float]] = {}
    for r in fixed_rows:
        q = r["q15"]
        if q.get("status") == "ok" and "per_step_median_s" in q:
            per.setdefault(r["method"], {})[r["problem"]] = round(
                q["per_step_median_s"] * 1e6, 3)
            tm = q["timing"]
            if tm["median_s"] > 0:
                rel_iqr.setdefault(r["method"], []).append(
                    tm["iqr_s"] / tm["median_s"])
    out: dict[str, dict] = {}
    for m, vals in per.items():
        us = sorted(vals.values())
        spread = rel_iqr.get(m, [])
        out[m] = {
            "per_problem_us_per_step": vals,
            "median_us_per_step": _fin(statistics.median(us)),
            "min_us_per_step": us[0],
            "max_us_per_step": us[-1],
            "median_relative_iqr": _fin(statistics.median(spread)) if spread else None,
            "n_problems": len(us),
        }
    return out


def build_speedup(fixed_rows: list[dict], champion: str) -> dict:
    """The measured head-to-head: champion vs classical rk4 through the same
    solve_q15 path, per problem, with the cycle-model prediction beside the
    measurement and the Q15 errors cited from the fixed-step table."""
    cells = {(r["method"], r["problem"]): r for r in fixed_rows}
    rows: list[dict] = []
    measured: list[float] = []
    predicted: list[float] = []
    err_ratios: list[float] = []
    lower = 0
    err_pairs = 0
    for name in PROBLEM_NAMES:
        c = cells[(champion, name)]
        k = cells[(SPEEDUP_BASELINE, name)]
        row: dict = {
            "problem": name,
            "champion_cycles_per_step": c["cycles_per_step"],
            "rk4_cycles_per_step": k["cycles_per_step"],
            "predicted_ratio_rk4_over_champion":
                k["cycles_per_step"] / c["cycles_per_step"],
            "champion_n_steps": c["n_steps"],
            "rk4_n_steps": k["n_steps"],
            "champion_total_cycles": c["total_cycles"],
            "rk4_total_cycles": k["total_cycles"],
        }
        cq, kq = c["q15"], k["q15"]
        ok = cq.get("status") == "ok" and kq.get("status") == "ok"
        row["status"] = "ok" if ok else "incomplete"
        if not ok:
            row["reason"] = (f"champion q15 status {cq.get('status')!r}, "
                             f"rk4 q15 status {kq.get('status')!r}")
        if cq.get("status") == "ok":
            row["champion_per_step_median_s"] = cq["per_step_median_s"]
            row["champion_us_per_step"] = round(cq["per_step_median_s"] * 1e6, 3)
            row["champion_budget_seconds"] = cq["timing"]["median_s"]
            row["champion_error"] = cq["error"]
        if kq.get("status") == "ok":
            row["rk4_per_step_median_s"] = kq["per_step_median_s"]
            row["rk4_us_per_step"] = round(kq["per_step_median_s"] * 1e6, 3)
            row["rk4_budget_seconds"] = kq["timing"]["median_s"]
            row["rk4_error"] = kq["error"]
        if ok:
            ratio = _fin(kq["per_step_median_s"] / cq["per_step_median_s"])
            row["measured_ratio_rk4_over_champion"] = ratio
            if ratio is not None:
                measured.append(ratio)
                predicted.append(row["predicted_ratio_rk4_over_champion"])
            row["champion_error_lower"] = cq["error"] < kq["error"]
            err_pairs += 1
            if row["champion_error_lower"]:
                lower += 1
            if kq["error"] > 0:
                er = _fin(cq["error"] / kq["error"])
                row["error_ratio_champion_over_rk4"] = er
                if er is not None:
                    err_ratios.append(er)
        rows.append(row)

    g_meas = _geomean(measured)
    g_pred = _geomean(predicted)
    med_er = _fin(statistics.median(err_ratios)) if err_ratios else None

    if g_meas is not None:
        prose = (
            f"Measured head-to-head in the fixed-step Q15 regime: across "
            f"{len(measured)} problems the geometric-mean measured per-step "
            f"speedup of the champion over classical rk4 is {g_meas:.3f}x, "
            f"next to a cycle-model prediction of {g_pred:.3f}x under "
            f"{COST_MODEL.name}."
        )
        if err_pairs and med_er is not None:
            acc = (
                f" At the same {BUDGET_CYCLES}-cycle budget the champion "
                f"reaches the lower held-out Q15 error in {lower} of "
                f"{err_pairs} problems with a median error ratio (champion "
                f"over rk4) of {med_er:.3g}"
            )
            if med_er < 1.0 and 2 * lower >= err_pairs:
                acc += (", so the per-step time saving is not bought with "
                        "accuracy.")
            else:
                acc += ("; accuracy and speed trade off here, so read the "
                        "per-problem rows.")
            prose += acc
    else:
        prose = "No comparable timed cells for a speedup table."

    return {
        "champion": champion,
        "baseline": SPEEDUP_BASELINE,
        "regime": _SPEEDUP_REGIME,
        "rows": rows,
        "n_problems_compared": len(measured),
        "geomean_measured_speedup_rk4_over_champion": g_meas,
        "geomean_predicted_speedup_rk4_over_champion": g_pred,
        "champion_error_lower_count": lower,
        "error_comparisons": err_pairs,
        "median_error_ratio_champion_over_rk4": med_er,
        "per_method_us_per_step": per_method_us_per_step(fixed_rows),
        "caveat": _SPEEDUP_CAVEAT,
        "prose": prose,
    }


# ------------------------------------------------------------------ verdicts


def _median_or_none(vals: list[float]):
    return statistics.median(vals) if vals else None


def build_verdicts(adaptive_rows: list[dict], fixed_rows: list[dict],
                   corr: dict) -> dict:
    per_problem: dict[str, dict] = {}
    tol_ratios: list[float] = []
    for name in PROBLEM_NAMES:
        lib = [r for r in adaptive_rows
               if r["problem"] == name and r.get("status") == "ok"
               and r.get("error") is not None]
        q15 = [r for r in fixed_rows
               if r["problem"] == name and r["q15"].get("status") == "ok"
               and r["q15"].get("error") is not None]
        entry: dict = {}
        if lib:
            best = min(lib, key=lambda r: r["error"])
            entry["best_library"] = best["integrator"]
            entry["best_library_error"] = best["error"]
            entry["best_library_median_s"] = best["timing"]["median_s"]
        if q15:
            best = min(q15, key=lambda r: r["q15"]["error"])
            entry["best_q15_method"] = best["method"]
            entry["best_q15_error"] = best["q15"]["error"]
            entry["best_q15_median_s"] = best["q15"]["timing"]["median_s"]
        if lib and q15 and entry["best_library_error"] > 0:
            ratio = entry["best_q15_error"] / entry["best_library_error"]
            entry["ratio_q15_over_library"] = _fin(ratio)
            if entry["ratio_q15_over_library"] is not None:
                tol_ratios.append(ratio)
        per_problem[name] = entry

    fixed_ratios: list[float] = []
    q15_lower = 0
    comparable = 0
    for r in fixed_rows:
        qe = r["q15"].get("error")
        fe = r["float_rk4"].get("error")
        if qe is None or fe is None or fe <= 0:
            continue
        comparable += 1
        fixed_ratios.append(qe / fe)
        if qe < fe:
            q15_lower += 1

    med_tol = _median_or_none(tol_ratios)
    med_fixed = _median_or_none(fixed_ratios)
    r_val = corr.get("pearson_r")

    if med_tol is not None:
        matched = (
            f"At tolerances matched to one Q15 least significant bit, the "
            f"median per-problem ratio of best Q15 error to best library "
            f"error is {med_tol:.3g} (above 1.0 means the library side is "
            f"more accurate). Library wall clocks in the same table are "
            f"informative but never same-work comparisons, because adaptive "
            f"integrators choose their own step counts."
        )
    else:
        matched = ("No comparable matched-tolerance cells; the library side or "
                   "the Q15 side is empty.")

    if med_fixed is not None:
        fixed_v = (
            f"At identical step counts implied by the {BUDGET_CYCLES}-cycle "
            f"budget under {COST_MODEL.name}, the median ratio of Q15 error "
            f"to float64 rk4 error is {med_fixed:.3g}, and the Q15 side has "
            f"the lower error in {q15_lower} of {comparable} comparable "
            f"cells. The gap between the two columns is the cost of 16-bit "
            f"floor arithmetic (a -0.5 LSB bias per multiply in the pinned "
            f"solver), not of the tableaus themselves."
        )
    else:
        fixed_v = "No comparable fixed-step cells."

    if r_val is not None:
        cycle_v = (
            f"Across {corr['n_points']} fixed-step Q15 runs the Pearson "
            f"correlation between analytic cycles per step and measured "
            f"seconds per step is {r_val:.3f}. The analytic model counts "
            f"Cortex-M0+ arithmetic while the measurement is Python "
            f"interpreter time, so the correlation speaks to ordering, not to "
            f"absolute scale."
        )
    else:
        cycle_v = "Too few valid fixed-step Q15 timings to correlate."

    parts = []
    if med_tol is not None:
        parts.append(f"median Q15-to-library error ratio {med_tol:.3g} at "
                     f"matched tolerance")
    if med_fixed is not None:
        parts.append(f"median Q15-to-float-rk4 error ratio {med_fixed:.3g} at "
                     f"matched step counts")
    if parts and (med_tol is None or med_tol > 1.0) and (med_fixed is None or med_fixed > 1.0):
        lead = "The float64 side holds the accuracy margin in both tables"
    elif parts:
        lead = "The accuracy comparison is mixed"
    else:
        lead = "No comparable accuracy cells were produced"
    overall = (
        f"{lead}" + (f" ({'; '.join(parts)})" if parts else "") + ". "
        "What the Q15 methods offer is not accuracy parity: they run in "
        "16-bit integer arithmetic at a fixed, budgeted cycle cost, which no "
        "library measured here does. Within the Q15 regime the analytic cycle "
        "model and measured time agree on ordering to the extent the reported "
        "correlation shows."
    )
    return {
        "per_problem": per_problem,
        "median_ratio_q15_over_library_at_matched_tolerance": _fin(med_tol) if med_tol is not None else None,
        "median_ratio_q15_over_float_rk4_at_matched_steps": _fin(med_fixed) if med_fixed is not None else None,
        "fixed_step_cells_compared": comparable,
        "fixed_step_cells_where_q15_error_lower": q15_lower,
        "matched_tolerance": matched,
        "fixed_step": fixed_v,
        "cycle_model": cycle_v,
        "overall": overall,
    }


# -------------------------------------------------------------- three classes


@dataclasses.dataclass(frozen=True)
class ThreeClassScope:
    """What the three-class sections cover, and every work cap they run under.

    It is a stamped block in the document rather than a set of defaults buried in
    the code, because the coverage assertion in ``validate_results`` checks the
    rows against what the document says it covered. A cheap scope produces a small
    document that is still complete against its own declaration; it never produces
    a document that quietly dropped a class.
    """
    problems: tuple[str, ...] | None = None
    targets: tuple[float, ...] = BC.TARGETS
    n_max: int = BC.N_MAX_BENCH
    bisect_probes: int = BC.BISECT_PROBES
    tol_ladder: tuple[int, ...] = BC.TOL_LADDER_LSB
    max_attempts: int = BC.MAX_ATTEMPTS
    lib_tol_ladder: tuple[float, ...] = BC.LIB_TOL_LADDER
    adaptive_tols: tuple[float, ...] = BC.ADAPTIVE_TOLS
    budget_problems: tuple[str, ...] | None = None
    explicit_solvers: tuple[str, ...] = ("rk4", "champion")
    float_control: bool = True
    library: tuple[str, ...] = BC.SCIPY_ADAPTIVE + BC.SCIPY_IMPLICIT
    newton_iters: int = BC.NEWTON_ITERS
    time_selected: bool = True

    def resolved_problems(self) -> tuple[str, ...]:
        return (tuple(self.problems) if self.problems is not None
                else BC.problem_names("application"))

    def resolved_budget_problems(self) -> tuple[str, ...]:
        return (tuple(self.budget_problems) if self.budget_problems is not None
                else BC.STIFF_NAMES)

    def as_dict(self) -> dict:
        out = {k: (list(v) if isinstance(v, tuple) else v)
               for k, v in dataclasses.asdict(self).items()}
        out["resolved_problems"] = list(self.resolved_problems())
        out["resolved_budget_problems"] = list(self.resolved_budget_problems())
        out["problem_set"] = "application"
        return out


FULL_SCOPE = ThreeClassScope()

MATCHED_TIMING_POLICY = (
    "Wall clock is measured on one configuration per (solver, problem): the rung "
    "that the tightest reached target selected, under the same protocol as every "
    "other timing here. Rows that selected the same configuration carry the same "
    "measurement, because it is the same run. Timing every rung of every ladder "
    "at the full repeat count would be hours of clock for numbers no reader "
    "would use, and it would not make any cross-family comparison legitimate."
)


def _three_class_specs(methods: list[dict], scope: ThreeClassScope) -> list[BC.SolverSpec]:
    """The solvers the three-class tables measure, ours and theirs.

    Our explicit side is drawn from the document's own method pool, so the
    matched-accuracy table names the same tableaus the rest of the document does.
    """
    by_name = {m["name_or_hash"]: m for m in methods}
    champ = champion_hash(methods)
    specs: list[BC.SolverSpec] = []
    for token in scope.explicit_solvers:
        name = champ if token == "champion" else token
        m = by_name.get(name)
        if m is None:
            continue
        specs.append(BC.explicit_spec(name, m["tableau"], roles=tuple(m.get("roles", []))))
    if scope.float_control:
        specs.append(BC.rk4_float_spec())
    specs.extend(BC.prototype_specs(scope.newton_iters))
    specs.extend(BC.library_specs(tuple(scope.library)))
    return specs


def _tightest_reached(rows: list[dict]) -> dict[tuple[str, str], dict]:
    """Per (solver, problem), the reached row with the smallest target."""
    best: dict[tuple[str, str], dict] = {}
    for r in rows:
        if r.get("status") != "reached" or r.get("control_value") is None:
            continue
        key = (r["solver"], r["problem"])
        cur = best.get(key)
        if cur is None or r["target_error"] < cur["target_error"]:
            best[key] = r
    return best


def _time_matched_rows(rows: list[dict], specs: list[BC.SolverSpec],
                       n_repeats: int, warmup: int) -> None:
    """Populate `timing` in place, on the selected configurations only."""
    by_key = {s.key: s for s in specs}
    for (solver, problem), row in sorted(_tightest_reached(rows).items()):
        spec = by_key.get(solver)
        if spec is None:
            continue
        fn = BC.replay_callable(spec, row)
        if fn is None:
            continue
        try:
            timing = measure(fn, n_repeats, warmup)
        except Exception as exc:                # a run that fails is not a timing
            row["timing_error"] = f"{type(exc).__name__}: {exc}"
            continue
        for other in rows:
            if (other["solver"] == solver and other["problem"] == problem
                    and other.get("control_value") == row["control_value"]
                    and other.get("status") == "reached"):
                other["timing"] = timing


def _time_tolerance_rows(rows: list[dict], specs: list[BC.SolverSpec],
                         n_repeats: int, warmup: int) -> None:
    by_key = {s.key: s for s in specs}
    best: dict[tuple[str, str], dict] = {}
    for r in rows:
        if r.get("status") != "ok":
            continue
        key = (r["solver"], r["problem"])
        cur = best.get(key)
        if cur is None or r["tol"] < cur["tol"]:
            best[key] = r
    for (solver, problem), row in sorted(best.items()):
        spec = by_key.get(solver)
        if spec is None:
            continue
        fn = BC.tolerance_replay_callable(spec, row)
        if fn is None:
            continue
        try:
            row["timing"] = measure(fn, n_repeats, warmup)
        except Exception as exc:
            row["timing_error"] = f"{type(exc).__name__}: {exc}"


def build_three_class(methods: list[dict], scope: ThreeClassScope = FULL_SCOPE,
                      n_repeats: int = N_REPEATS, warmup: int = N_WARMUP) -> dict:
    """The three-class block: one row shape, one condition, three classes, both
    sides. Deterministic except the timing objects."""
    specs = _three_class_specs(methods, scope)
    names = scope.resolved_problems()
    matched = BC.build_matched_accuracy(
        specs, "application", names, scope.targets,
        n_max=scope.n_max, bisect_probes=scope.bisect_probes,
        tol_ladder=scope.tol_ladder, max_attempts=scope.max_attempts,
        lib_tol_ladder=scope.lib_tol_ladder)
    tol_rows = BC.build_adaptive_matched_tolerance(
        "application", names, scope.adaptive_tols, max_attempts=scope.max_attempts)
    if scope.time_selected:
        _time_matched_rows(matched, specs, n_repeats, warmup)
        pair_specs = BC.prototype_specs(scope.newton_iters) + BC.library_specs(BC.SCIPY_ADAPTIVE)
        _time_tolerance_rows(tol_rows, pair_specs, n_repeats, warmup)
    pairs = BC.build_adaptive_pairs(tol_rows)
    budget = BC.implicit_budget(scope.resolved_budget_problems(),
                                newton_iters=scope.newton_iters,
                                budget_cycles=BUDGET_CYCLES)
    return {
        "three_class_scope": scope.as_dict(),
        "solvers": [s.as_dict() for s in specs],
        "matched_accuracy": matched,
        "adaptive_matched_tolerance": tol_rows,
        "adaptive_pairs": pairs,
        "implicit_budget": budget,
        "classes": BC.classes_block(specs, matched, tol_rows, budget),
        "comparability": BC.comparability_block(),
        "problem_sets": BC.problem_sets_block(),
        "timing_policy": MATCHED_TIMING_POLICY,
    }


def _class_sentence(cls: str, rows: list[dict]) -> str:
    ours = [r for r in rows if r["side"] == "ours"]
    lib = [r for r in rows if r["side"] == "library"]
    o_hit = sum(1 for r in ours if r["status"] == "reached")
    l_hit = sum(1 for r in lib if r["status"] == "reached")
    cyc = [r["analytic_cycles_total"] for r in rows
           if r["status"] == "reached" and isinstance(r["analytic_cycles_total"], int)]
    med = _median_or_none(cyc)
    text = (f"The {cls} class carries {len(ours)} rows of ours and {len(lib)} "
            f"library rows at matched accuracy; {o_hit} of ours and {l_hit} of "
            f"theirs reached their target on the sampled ladder.")
    if not lib:
        text += (" There is no library counterpart for this class, which the "
                 "classes block states with its reason rather than leaving an "
                 "empty column.")
    if med is not None:
        text += (f" Where a cycle model applies, the median analytic cycle cost "
                 f"of a reached row is {med:.6g}, and every such row carries the "
                 f"grade of number it is.")
    else:
        text += (" No row in this class carries a cycle number, so none is "
                 "reported for it.")
    return text


def _pair_sentence(pairs: list[dict]) -> str:
    fev = [p["fevals_ratio_ours_over_rk23"] for p in pairs
           if isinstance(p.get("fevals_ratio_ours_over_rk23"), (int, float))]
    err = [p["error_ratio_ours_over_rk23"] for p in pairs
           if isinstance(p.get("error_ratio_ours_over_rk23"), (int, float))]
    if not fev and not err:
        return ("No cell had both our Q15 pair and RK23 finish, so the "
                "same-tableau comparison has nothing to report.")
    f_med = _median_or_none(fev)
    e_med = _median_or_none(err)
    parts = []
    if f_med is not None:
        parts.append(f"a median function-evaluation ratio of {f_med:.3g} "
                     f"(ours over RK23) over {len(fev)} cells")
    if e_med is not None:
        parts.append(f"a median achieved-error ratio of {e_med:.3g}")
    return (
        "SciPy RK23 runs the same Bogacki-Shampine 3(2) tableau our pair runs, so "
        "at rtol = atol = tol the two differ only in the step controller and the "
        "arithmetic: " + " and ".join(parts) + ". A ratio above 1.0 means our "
        "side spent more or landed further from the reference. Asking two solvers "
        "for the same tolerance does not put them at the same accuracy, which is "
        "why the matched-accuracy table exists beside this one and why no "
        "wall-clock ratio is formed across the two implementations."
    )


def _short(name: str) -> str:
    """A 64-hex tableau hash shortened the way the rest of the document prints
    one; every other solver name is already short and passes through."""
    s = str(name)
    return s[:12] if len(s) == 64 and all(c in "0123456789abcdef" for c in s) else s


def _stiff_sentence(rows: list[dict], targets) -> str:
    tight = min(float(t) for t in targets) if targets else None
    if tight is None:
        return "No targets were requested, so nothing is reported on the stiff set."
    stiff_rows = [r for r in rows if r["stiff"] and r["target_error"] == tight]
    if not stiff_rows:
        return ("The sampled scope carried no stiff problem at the tightest "
                "target, so nothing is reported about the stiff regime.")
    hit = sorted({_short(r["solver"]) for r in stiff_rows if r["status"] == "reached"})
    miss = sorted({_short(r["solver"]) for r in stiff_rows if r["status"] != "reached"})
    n_probs = len({r["problem"] for r in stiff_rows})
    lead = (f"On the {_plural(n_probs, 'stiff application problem')} at the "
            f"tightest target of {tight:.6g}, ")
    if hit and miss:
        return lead + (f"{len(hit)} of the {len(hit) + len(miss)} solvers "
                       f"reached it ({', '.join(hit)}) and the rest did not "
                       f"({', '.join(miss)}), with each row carrying the status "
                       f"that says why.")
    if hit:
        return lead + f"every solver reached it ({', '.join(hit)})."
    return lead + ("no solver reached it, and each row carries the status that "
                   "says why.")


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def three_class_verdicts(block: dict) -> dict:
    """Prose derived from the rows, written so it reads correctly whichever way
    the numbers fall, and swept for the banned words before it is written."""
    rows = block["matched_accuracy"]
    per_class = {cls: _class_sentence(cls, [r for r in rows if r["class"] == cls])
                 for cls in BC.CLASS_ORDER}
    scope = block["three_class_scope"]
    counts = {cls: block["classes"][cls]["n_rows"] for cls in BC.CLASS_ORDER}
    reached = sum(c["matched_accuracy_reached"] for c in counts.values())
    overall = (
        f"Matched accuracy is the shared condition: {len(rows)} rows over "
        f"{len(scope['resolved_problems'])} application problems and "
        f"{len(scope['targets'])} targets, {reached} of which reached their "
        "target on the sampled ladder. The three classes are measured against "
        "library counterparts where a counterpart exists, and every row states "
        "the arithmetic it ran, the grade of its cycle number and the "
        "differences the comparison does not control for. Wall clock is "
        "reported inside a timing family and never across one."
    )
    return {
        "per_class": per_class,
        "adaptive_pair": _pair_sentence(block["adaptive_pairs"]),
        "stiff": _stiff_sentence(rows, scope["targets"]),
        "cost_grades": (
            "Three grades of cycle number sit in this document and each row says "
            "which it is: the pinned model cross-checked against the reference "
            "listing for Q15 explicit rows, a design estimate resting on a "
            "software-reciprocal guess for the implicit rows, and an upper bound "
            "with unpriced branches for the Q15 adaptive rows. Library rows carry "
            "no cycle number at all, because SciPy has no cost model and inventing "
            "one for it would be inventing the comparison."
        ),
        "overall": overall,
    }


# ------------------------------------------------------------------ document


_SCHEMA_DOC: dict[str, str] = {
    "budget_cycles": "shared cycle budget that sets every fixed-step run's step count",
    "cost_model": "cost model used for the budget to step-count mapping",
    "tolerance_rule": "how scipy rtol/atol were matched to the Q15 error scale",
    "rounding": "Q15 multiply semantics of the pinned solver (floor / ASRS)",
    "generated_from": "where: this document is host-on-demand work and says why; "
                      "validation_results: file the method tableaus came from; "
                      "champion_hashes: discovered tableau hashes carried over; "
                      "scipy: version string, or null with scipy_error set when "
                      "the import failed and the adaptive table was skipped",
    "environment": "timing environment: python/numpy/scipy versions, cpu and os "
                   "via platform, perf_counter resolution, the thread caps "
                   "exported before numpy loaded, and the like-against-like "
                   "timing caveat",
    "timing_protocol": "clock, warmup runs, and repeat count n behind every "
                       "timing object; timing objects carry median_s, iqr_s "
                       "(quartile 3 minus quartile 1), min_s, n and warmup",
    "problems": "the seven frozen scored problems: name, n_states, t_end, "
                "scale, deriv_scale, family, peak, and the matched rtol/atol",
    "methods": "fixed-step methods under benchmark: the classical anchors "
               "plus every discovered method from the validation document, "
               "with kind, roles, order, stages, tableau (exact fraction "
               "strings), cycles_per_step and steps per problem, and archive "
               "provenance for discovered entries; coefficient_memory holds "
               "the int16 constant words a tableau would need if every "
               "non-trivial entry of A and b were held in a table (words, "
               "bytes = 2 * words, max_shift, entries, trivial), which is an "
               "upper bound on such a table and not a measurement of the "
               "reference C, since costmodel.emit_c inlines the mantissa and "
               "the shift as literals and builds no table at all. An entry whose "
               "class is not explicit, and the derived fixed-step control of an "
               "embedded pair, are skipped on the way in: every method here is "
               "run through the pinned explicit solver, which would misprice "
               "both",
    "adaptive_results": "table 1, one row per (scipy integrator, problem): "
                        "accuracy at matched tolerance with wall clock. Fields: "
                        "rtol, atol, status ok|failed|skipped (reason on "
                        "non-ok), error (final-state error_metric, null when "
                        "unavailable), n_steps_accepted, nfev, timing, "
                        "per_step_median_s. Adaptive integrators choose their "
                        "own steps, so these timings are never same-work "
                        "comparisons against the fixed-step table. Every row "
                        "also carries class (RK45 and RK23 are explicit pairs "
                        "under a step controller, Radau, BDF and LSODA solve an "
                        "implicit system every step), side, arithmetic, "
                        "timing_family and problem_set",
    "fixed_step_results": "table 2, one row per (Q15 method, problem) at the "
                          "budgeted step count n_steps: like-for-like cells "
                          "holding a q15 object (pinned solve_q15 run: status, "
                          "error, max_abs_q, timing, per_step_median_s) and a "
                          "float_rk4 object (hand-rolled float64 rk4 at the "
                          "identical step count: status, error, timing, "
                          "per_step_median_s), plus cycles_per_step and "
                          "total_cycles from the analytic model, and the class, "
                          "side and problem_set the row belongs to",
    "correlation": "Pearson correlation between analytic cycles per step and "
                   "measured seconds per step across the ok fixed-step Q15 "
                   "runs, with the point count and median seconds per analytic "
                   "cycle",
    "speedup": "the measured head-to-head: champion vs classical rk4 through "
               "the identical pinned solve_q15 path, one row per problem with "
               "measured seconds and microseconds per step for both, the "
               "measured per-step ratio beside the cycle-model predicted "
               "ratio (rk4 over champion; above 1.0 means the champion needs "
               "less time per step), seconds to complete the shared budget, "
               "and the Q15 errors cited from fixed_step_results; plus the "
               "geometric-mean measured and predicted speedups, the count of "
               "problems where the champion error is lower, and "
               "per_method_us_per_step (median measured microseconds per step "
               "per method across problems, with the problem-to-problem "
               "min-max range and median_relative_iqr, the median over that "
               "method's finished problems of the timing iqr_s divided by "
               "median_s, which is the repeat-to-repeat spread of the clock "
               "rather than a difference between problems) for direct "
               "charting",
    "verdicts": "per_problem best library and best Q15 entries with error "
                "ratios, the two median ratios, and honest prose for the "
                "matched-tolerance table, the fixed-step table, the cycle "
                "model, the three-class tables, and overall",
    "caveats": "the reading rules: what these numbers do and do not support",
    "problem_sets": "the two problem sets every row is drawn from: frozen (the "
                    "seven pinned scored problems) and application (the eight "
                    "unpinned validation problems, three of them stiff at "
                    "ratios 546.4, 1030.0 and 292.0). Each carries its source, "
                    "its error metric, its stiffness labels and a per-problem "
                    "descriptor. The three-class tables run on the application "
                    "set because the stiffest of the frozen seven is rc_thermal "
                    "at 70.1, where an implicit method cannot pay for itself",
    "solvers": "every solver the three-class tables measure, ours and theirs, "
               "with its class, side, arithmetic, control, cost grade, timing "
               "family and the method family it actually is",
    "three_class_scope": "what the three-class tables cover and every work cap "
                         "they ran under (problems, targets, ladder caps, "
                         "attempt cap, tolerance ladders, which solvers). The "
                         "coverage assertion checks the rows against this block, "
                         "so a cheap scope yields a small document that is "
                         "complete against its own declaration and never one "
                         "that quietly dropped a class",
    "matched_accuracy": "table 3, one row per (class, solver, problem, target): "
                        "what it costs each solver to bring a problem to a "
                        "target error, with the error measured the same way for "
                        "every solver on both sides. Fields: class, solver, "
                        "side (ours|library), arithmetic, problem_set, problem, "
                        "stiffness_ratio, target_error, target_key, control and "
                        "control_unit, control_value, status, reason, "
                        "achieved_error, best_achieved_error, accepted and "
                        "rejected step counts with the basis of the rejected "
                        "count, nfev, njev, nlu, nlinsolve, "
                        "analytic_cycles_per_step, analytic_cycles_total, "
                        "cost_grade, probes, ladder_monotone, the ladder, the "
                        "work cap, timing, timing_family, and controls_for and "
                        "not_controlled, which say in fields what the row does "
                        "and does not hold constant",
    "adaptive_matched_tolerance": "table 4, the adaptive-only extra condition: "
                                  "rtol = atol = tol for our pair in Q15 and in "
                                  "float64 and for RK23 and RK45. It exists for "
                                  "adaptive alone because adaptive is the only "
                                  "class where both sides share an accept test, "
                                  "so the same request means the same thing. The "
                                  "Q15 rows carry tol_q_lsb, the conversion that "
                                  "produced it and below_bias_floor",
    "adaptive_pairs": "the four adaptive solvers side by side per (problem, "
                      "tolerance), with same_pair true because SciPy RK23 runs "
                      "the same Bogacki-Shampine 3(2) tableau our pair runs: "
                      "the tableau is held constant and only the controller and "
                      "the arithmetic differ. Function-evaluation and "
                      "achieved-error ratios are reported because neither "
                      "depends on the implementation; the wall-clock ratio is "
                      "null with its reason",
    "implicit_budget": "our SDIRK2 on the three stiff problems: the step count "
                       "the shared cycle budget buys under the analytic model, "
                       "the error there, and the quarter, half, one, two and "
                       "four ladder around it. Our side only, with "
                       "why_no_library stating that SciPy has no cycle model, "
                       "rather than an empty library column",
    "classes": "per class: our methods, their counterparts, the matched "
               "conditions, the arithmetics, the row counts per side and the "
               "class's standing (scored in the archive, or prototype and "
               "off-archive). A class that vanished from a rebuild appears here "
               "as a zero rather than as silence",
    "comparability": "the reading rules of the three-class tables as data: the "
                     "shared condition, what is not matched, the rule that a "
                     "wall-clock ratio stays inside a timing family, the rule "
                     "that an incomparable quantity is null with a reason, and "
                     "the meaning of every timing family, arithmetic and cost "
                     "grade",
    "timing_policy": "which configurations carry a measured wall clock in the "
                     "three-class tables and why the rest do not",
}

_CAVEATS = [
    "Adaptive scipy integrators choose their own step counts; their wall clock "
    "is reported for context and is never a same-work comparison with any "
    "fixed-step run.",
    "All timings are Python-level wall clock on one desktop machine: the Q15 "
    "runs pay Python interpreter overhead per primitive while scipy runs "
    "compiled internals, so timings compare like against like only within a "
    "regime and do not transfer to microcontroller cycle counts.",
    "Accuracy numbers are deterministic and reproducible; timing numbers are "
    "measured and vary run to run within the reported IQR.",
    "The float baselines start from the exact physical initial state; the Q15 "
    "runs start from the quantized Q15 initial state, as on the target.",
    "The three-class tables share matched accuracy, not matched wall clock. Our "
    "side runs Q15 integer arithmetic in a Python loop that models a "
    "Cortex-M0+; the library side runs float64 inside compiled code on this "
    "desktop. Every row names the arithmetic it ran and the differences the "
    "comparison does not control for, and a wall-clock ratio is never formed "
    "across two timing families.",
    "Where a quantity cannot be made comparable it is null with a reason beside "
    "it: SciPy reports no rejected-step count except where a fixed "
    "per-attempt evaluation count makes it exactly recoverable, and SciPy has "
    "no cycle model at all, so every library row's cycle columns are empty "
    "rather than estimated.",
]


def build_results(validation_doc: dict | None = None,
                  n_repeats: int = N_REPEATS,
                  warmup: int = N_WARMUP,
                  scope: ThreeClassScope = FULL_SCOPE) -> dict:
    """The full benchmark document. Deterministic except the timing objects.

    ``scope`` bounds the three-class tables and is stamped into the document, so
    the coverage assertion checks the rows against the document's own
    declaration. The default is the whole thing, which is host work on a quiet
    machine; the tests run a small scope for speed and check it just as strictly.
    """
    if validation_doc is None:
        validation_doc = load_validation_doc()
    methods = select_benchmark_methods(validation_doc)

    fix = load_fixture()
    problems_out = []
    for name in PROBLEM_NAMES:
        p = PROBLEMS[name]
        rtol, atol = tolerances(name)
        problems_out.append({
            "name": name,
            "n_states": p.n_states,
            "t_end": p.t_end,
            "scale": p.scale,
            "deriv_scale": DERIV_SCALE[name],
            "family": p.family,
            "peak": float(fix[name]["peak"]),
            "rtol": rtol,
            "atol": atol,
        })

    methods_out = []
    for m in methods:
        t = m["tableau"]
        entry = {
            "name_or_hash": m["name_or_hash"],
            "kind": m["kind"],
            "roles": m["roles"],
            "order": m["order"],
            "stages": m["stages"],
            "tableau": to_json(t),
            "cycles_per_step": {
                name: cycle_count(t, COST_MODEL, PROBLEMS[name].n_states)
                for name in PROBLEM_NAMES},
            "steps": {
                name: steps_for_budget(t, COST_MODEL, PROBLEMS[name].n_states,
                                       BUDGET_CYCLES)
                for name in PROBLEM_NAMES},
            "coefficient_memory": coefficient_memory(t),
        }
        if "archive" in m:
            entry["archive"] = m["archive"]
        methods_out.append(entry)

    adaptive_rows = [adaptive_row(integ, name, n_repeats, warmup)
                     for integ in SCIPY_INTEGRATORS
                     for name in PROBLEM_NAMES]
    fixed_rows = [fixed_step_row(m, name, n_repeats, warmup)
                  for m in methods
                  for name in PROBLEM_NAMES]
    corr = cycles_time_correlation(fixed_rows)

    environment = {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "cpu": platform.processor(),
        "machine": platform.machine(),
        "os": f"{platform.system()} {platform.release()}",
        "numpy": np.__version__,
        "scipy": _SCIPY_VERSION,
        "perf_counter_resolution_s": time.get_clock_info("perf_counter").resolution,
        "thread_env": {k: os.environ.get(k) for k in _THREAD_VARS},
        "timing_caveat": _CAVEATS[1],
    }

    three = build_three_class(methods, scope, n_repeats, warmup)

    doc = {
        "schema": _SCHEMA_DOC,
        "budget_cycles": BUDGET_CYCLES,
        "cost_model": COST_MODEL.name,
        "tolerance_rule": TOLERANCE_RULE,
        "rounding": "floor (ASRS), per HANDOFF 4.2",
        "generated_from": {
            "where": (
                "on the host, on demand, and nowhere else. This module exports "
                "five BLAS thread variables and imports numpy before anything "
                "else happens, and it measures real time, which the container "
                "cannot do: it runs at --cpu-shares 256 under a watchdog that "
                "pauses it (docs/SIDETRACK-AUTOMATION.md D9). The deterministic "
                "half lives in rk_harness.benchcounts, which reads no clock and "
                "sets no environment variable, so the counters could run "
                "anywhere; the stopwatch stays here."
            ),
            "validation_results": "rk-work/validation/results.json (methods[].tableau)",
            "champion_hashes": [m["name_or_hash"] for m in methods
                                if m["kind"] == "discovered"],
            "scipy": _SCIPY_VERSION,
        },
        "environment": environment,
        "timing_protocol": {"clock": "time.perf_counter",
                            "warmup": warmup, "n_repeats": n_repeats},
        "problems": problems_out,
        "methods": methods_out,
        "adaptive_results": adaptive_rows,
        "fixed_step_results": fixed_rows,
        "correlation": corr,
        "speedup": build_speedup(fixed_rows, champion_hash(methods)),
        "verdicts": build_verdicts(adaptive_rows, fixed_rows, corr),
        "caveats": _CAVEATS,
    }
    doc.update(three)
    doc["verdicts"]["three_class"] = three_class_verdicts(three)
    if _SCIPY_ERROR is not None:
        doc["generated_from"]["scipy_error"] = _SCIPY_ERROR
    return doc


# ---------------------------------------------------------------- validation


def _check_timing(obj: dict, where: str, n_repeats: int, fail) -> None:
    for k in ("median_s", "iqr_s", "min_s", "n", "warmup"):
        if k not in obj:
            fail(f"{where}: timing missing {k!r}")
    if not (isinstance(obj["median_s"], (int, float)) and obj["median_s"] > 0):
        fail(f"{where}: median_s must be positive")
    if not (isinstance(obj["iqr_s"], (int, float)) and obj["iqr_s"] >= 0):
        fail(f"{where}: iqr_s must be non-negative")
    if obj["n"] != n_repeats or obj["n"] < 1:
        fail(f"{where}: timing n {obj['n']} != protocol n_repeats {n_repeats}")


def validate_results(doc: dict) -> None:
    """Raise ValueError if the document violates the schema described above."""
    def fail(msg: str):
        raise ValueError(f"benchmark schema: {msg}")

    for key in ("schema", "budget_cycles", "cost_model", "tolerance_rule",
                "rounding", "generated_from", "environment", "timing_protocol",
                "problems", "methods", "adaptive_results", "fixed_step_results",
                "correlation", "speedup", "verdicts", "caveats",
                "problem_sets", "solvers", "three_class_scope",
                "matched_accuracy", "adaptive_matched_tolerance",
                "adaptive_pairs", "implicit_budget", "classes",
                "comparability", "timing_policy"):
        if key not in doc:
            fail(f"missing top-level key {key!r}")
    if doc["budget_cycles"] != BUDGET_CYCLES:
        fail(f"budget_cycles must be {BUDGET_CYCLES}")

    pnames = [p["name"] for p in doc["problems"]]
    if sorted(pnames) != sorted(PROBLEM_NAMES):
        fail(f"problems must cover {PROBLEM_NAMES}, got {pnames}")
    for p in doc["problems"]:
        for k in ("n_states", "t_end", "scale", "family", "peak", "rtol", "atol"):
            if k not in p:
                fail(f"problem {p['name']!r} missing {k!r}")
        if p["rtol"] != 2.0 ** -15 or p["atol"] != 2.0 ** -15 / p["scale"]:
            fail(f"problem {p['name']!r} breaks the tolerance matching rule")

    mnames = [m["name_or_hash"] for m in doc["methods"]]
    if len(set(mnames)) != len(mnames):
        fail("duplicate method entries")
    if "rk4" not in mnames:
        fail("methods must include classical rk4")
    for m in doc["methods"]:
        if m["kind"] not in ("classical", "discovered"):
            fail(f"method {m['name_or_hash']!r} has bad kind {m['kind']!r}")
        for k in ("roles", "order", "stages", "tableau", "cycles_per_step",
                  "steps", "coefficient_memory"):
            if k not in m:
                fail(f"method {m['name_or_hash']!r} missing {k!r}")
        cm = m["coefficient_memory"]
        for k in ("words", "bytes", "max_shift", "entries", "trivial"):
            if k not in cm:
                fail(f"method {m['name_or_hash']!r} coefficient_memory missing {k!r}")
        if not (isinstance(cm["words"], int) and cm["words"] >= 0):
            fail(f"method {m['name_or_hash']!r} coefficient_memory words "
                 "must be a non-negative integer")
        if cm["bytes"] != 2 * cm["words"]:
            fail(f"method {m['name_or_hash']!r} coefficient_memory bytes "
                 "must be 2 * words")
        if cm["entries"] < cm["words"]:
            fail(f"method {m['name_or_hash']!r} coefficient_memory entries "
                 "below words")
        if cm["max_shift"] < 0:
            fail(f"method {m['name_or_hash']!r} coefficient_memory max_shift "
                 "must be non-negative")
        t = from_json(m["tableau"])
        if m["kind"] == "discovered" and content_hash(t) != m["name_or_hash"]:
            fail(f"method {m['name_or_hash']!r} tableau hash mismatch")

    n_rep = doc["timing_protocol"]["n_repeats"]
    seen = set()
    for r in doc["adaptive_results"]:
        for k in ("integrator", "problem", "rtol", "atol", "status"):
            if k not in r:
                fail(f"adaptive row missing {k!r}: {r}")
        if r["problem"] not in pnames:
            fail(f"adaptive row references unknown problem {r['problem']!r}")
        key = (r["integrator"], r["problem"])
        if key in seen:
            fail(f"duplicate adaptive row {key}")
        seen.add(key)
        if r["status"] == "ok":
            for k in ("error", "n_steps_accepted", "nfev", "timing",
                      "per_step_median_s"):
                if k not in r:
                    fail(f"ok adaptive row {key} missing {k!r}")
            if r["error"] is not None and not math.isfinite(r["error"]):
                fail(f"adaptive row {key} error must be finite or null")
            _check_timing(r["timing"], f"adaptive {key}", n_rep, fail)
        elif "reason" not in r:
            fail(f"non-ok adaptive row {key} missing reason")
    if len(seen) != len(SCIPY_INTEGRATORS) * len(pnames):
        fail("adaptive_results must cover every (integrator, problem) pair once")

    seen = set()
    for r in doc["fixed_step_results"]:
        for k in ("method", "problem", "n_steps", "cycles_per_step",
                  "total_cycles", "q15", "float_rk4"):
            if k not in r:
                fail(f"fixed row missing {k!r}: {r}")
        if r["method"] not in mnames:
            fail(f"fixed row references unknown method {r['method']!r}")
        if r["problem"] not in pnames:
            fail(f"fixed row references unknown problem {r['problem']!r}")
        key = (r["method"], r["problem"])
        if key in seen:
            fail(f"duplicate fixed row {key}")
        seen.add(key)
        if r["total_cycles"] != r["cycles_per_step"] * r["n_steps"]:
            fail(f"fixed row {key} total_cycles inconsistent")
        if r["n_steps"] > 0 and r["total_cycles"] > BUDGET_CYCLES:
            fail(f"fixed row {key} exceeds the cycle budget")
        for side in ("q15", "float_rk4"):
            cell = r[side]
            if "status" not in cell:
                fail(f"fixed row {key} {side} missing status")
            if cell["status"] == "ok":
                if cell.get("error") is None or not math.isfinite(cell["error"]):
                    fail(f"fixed row {key} {side} ok but error not finite")
                _check_timing(cell["timing"], f"fixed {key} {side}", n_rep, fail)
                if not cell.get("per_step_median_s", 0) > 0:
                    fail(f"fixed row {key} {side} per_step_median_s must be positive")
            elif "reason" not in cell:
                fail(f"non-ok fixed row {key} {side} missing reason")
    if len(seen) != len(mnames) * len(pnames):
        fail("fixed_step_results must cover every (method, problem) pair once")

    corr = doc["correlation"]
    for k in ("x", "y", "n_points", "pearson_r"):
        if k not in corr:
            fail(f"correlation missing {k!r}")
    if corr["pearson_r"] is not None and not -1.0 <= corr["pearson_r"] <= 1.0:
        fail("correlation pearson_r outside [-1, 1]")

    sp = doc["speedup"]
    for k in ("champion", "baseline", "regime", "rows", "n_problems_compared",
              "geomean_measured_speedup_rk4_over_champion",
              "geomean_predicted_speedup_rk4_over_champion",
              "champion_error_lower_count", "error_comparisons",
              "median_error_ratio_champion_over_rk4",
              "per_method_us_per_step", "caveat", "prose"):
        if k not in sp:
            fail(f"speedup missing {k!r}")
    if sp["baseline"] != SPEEDUP_BASELINE:
        fail(f"speedup baseline must be {SPEEDUP_BASELINE!r}")
    if sp["champion"] not in mnames:
        fail(f"speedup champion {sp['champion']!r} not among methods")
    sp_names = [r["problem"] for r in sp["rows"]]
    if sorted(sp_names) != sorted(pnames):
        fail(f"speedup rows must cover {sorted(pnames)}, got {sp_names}")
    cells = {(r["method"], r["problem"]): r for r in doc["fixed_step_results"]}
    sp_meas: list[float] = []
    sp_pred: list[float] = []
    lower_expect = 0
    for row in sp["rows"]:
        c = cells.get((sp["champion"], row["problem"]))
        k = cells.get((SPEEDUP_BASELINE, row["problem"]))
        if c is None or k is None:
            fail(f"speedup row {row['problem']!r} has no matching fixed rows")
        if (row["champion_cycles_per_step"] != c["cycles_per_step"]
                or row["rk4_cycles_per_step"] != k["cycles_per_step"]):
            fail(f"speedup row {row['problem']!r} cycles_per_step mismatch")
        pred = k["cycles_per_step"] / c["cycles_per_step"]
        if not math.isclose(row["predicted_ratio_rk4_over_champion"], pred,
                            rel_tol=1e-12):
            fail(f"speedup row {row['problem']!r} predicted ratio inconsistent "
                 "with cycles_per_step")
        for side, cell in (("champion", c["q15"]), ("rk4", k["q15"])):
            if cell.get("status") != "ok":
                continue
            if row.get(f"{side}_per_step_median_s") != cell["per_step_median_s"]:
                fail(f"speedup row {row['problem']!r} {side} per-step seconds "
                     "do not match the fixed-step table")
            if row.get(f"{side}_error") != cell["error"]:
                fail(f"speedup row {row['problem']!r} {side} error does not "
                     "match the fixed-step table")
            if row.get(f"{side}_budget_seconds") != cell["timing"]["median_s"]:
                fail(f"speedup row {row['problem']!r} {side} budget seconds "
                     "do not match the fixed-step table")
            us = row.get(f"{side}_us_per_step")
            if us is None or abs(us - cell["per_step_median_s"] * 1e6) > 5e-4:
                fail(f"speedup row {row['problem']!r} {side} us_per_step "
                     "inconsistent with per-step seconds")
        if row.get("status") == "ok":
            expect = (k["q15"]["per_step_median_s"]
                      / c["q15"]["per_step_median_s"])
            got = row.get("measured_ratio_rk4_over_champion")
            if got is None or not math.isclose(got, expect, rel_tol=1e-9):
                fail(f"speedup row {row['problem']!r} measured ratio "
                     "inconsistent with the timing rows")
            sp_meas.append(got)
            sp_pred.append(row["predicted_ratio_rk4_over_champion"])
            if row.get("champion_error_lower"):
                lower_expect += 1
        elif "reason" not in row:
            fail(f"non-ok speedup row {row['problem']!r} missing reason")
    if sp["n_problems_compared"] != len(sp_meas):
        fail("speedup n_problems_compared inconsistent with its rows")
    if sp["champion_error_lower_count"] != lower_expect:
        fail("speedup champion_error_lower_count inconsistent with its rows")
    for key, vals in (("geomean_measured_speedup_rk4_over_champion", sp_meas),
                      ("geomean_predicted_speedup_rk4_over_champion", sp_pred)):
        got = sp[key]
        if vals:
            expect = math.exp(sum(math.log(v) for v in vals) / len(vals))
            if got is None or not math.isclose(got, expect, rel_tol=1e-9):
                fail(f"speedup {key} inconsistent with its rows")
        elif got is not None:
            fail(f"speedup {key} must be null with no compared rows")
    for m, s in sp["per_method_us_per_step"].items():
        if m not in mnames:
            fail(f"per_method_us_per_step references unknown method {m!r}")
        vals = list(s["per_problem_us_per_step"].values())
        if not vals or s["n_problems"] != len(vals):
            fail(f"per_method_us_per_step {m!r} n_problems inconsistent")
        if not math.isclose(s["median_us_per_step"], statistics.median(vals),
                            rel_tol=1e-9):
            fail(f"per_method_us_per_step {m!r} median inconsistent")
        if "median_relative_iqr" not in s:
            fail(f"per_method_us_per_step {m!r} missing median_relative_iqr")
        riqr = s["median_relative_iqr"]
        # null when no row carried a usable spread. A number that is not available says
        # so rather than reporting 0.0, which would read as perfectly repeatable timing.
        if riqr is not None and not (isinstance(riqr, (int, float))
                                     and math.isfinite(riqr) and riqr >= 0):
            fail(f"per_method_us_per_step {m!r} median_relative_iqr must be "
                 "null or finite and non-negative")

    v = doc["verdicts"]
    for k in ("per_problem", "matched_tolerance", "fixed_step", "cycle_model",
              "overall", "three_class"):
        if k not in v:
            fail(f"verdicts missing {k!r}")

    three_prose = _validate_three_class(doc, n_rep, fail)

    prose = [v["matched_tolerance"], v["fixed_step"], v["cycle_model"],
             v["overall"], sp["regime"], sp["caveat"], sp["prose"],
             *doc["caveats"], *three_prose]
    for text in prose:
        low = str(text).lower()
        for w in _BANNED:
            if re_search_word(w, low):
                fail(f"banned word {w!r} in verdict/caveat prose")
        if "—" in str(text):
            fail("em dash in verdict/caveat prose")


_MATCHED_ROW_KEYS = (
    "class", "solver", "side", "arithmetic", "family", "problem_set", "problem",
    "n_states", "stiff", "stiffness_ratio", "target_error", "target_key",
    "control", "control_unit", "control_value", "status", "reason",
    "achieved_error", "best_achieved_error", "n_steps_accepted",
    "n_steps_rejected", "n_steps_rejected_basis", "nfev", "njev", "nlu",
    "nlinsolve", "analytic_cycles_per_step", "analytic_cycles_total",
    "cost_grade", "probes", "ladder_monotone", "ladder", "work_cap", "timing",
    "timing_family", "controls_for", "not_controlled", "note",
)


def _validate_three_class(doc: dict, n_rep: int, fail) -> list[str]:
    """The three-class tables, and the honesty rules that are machine-checkable.

    Returns every distinct prose string the tables carry, for the caller's
    banned-word sweep. Row prose comes from a fixed set of templates, so the
    distinct set is small and sweeping it covers every row.
    """
    scope = doc["three_class_scope"]
    for k in ("resolved_problems", "targets", "resolved_budget_problems",
              "problem_set", "n_max", "bisect_probes", "tol_ladder",
              "max_attempts", "lib_tol_ladder", "adaptive_tols"):
        if k not in scope:
            fail(f"three_class_scope missing {k!r}")
    pnames = list(scope["resolved_problems"])
    targets = [float(t) for t in scope["targets"]]

    solvers = doc["solvers"]
    keys = [s["solver"] for s in solvers]
    if len(set(keys)) != len(keys):
        fail("duplicate solver entries")
    for s in solvers:
        if s["class"] not in BC.CLASS_ORDER:
            fail(f"solver {s['solver']!r} has bad class {s['class']!r}")
        if s["side"] not in BC.SIDES:
            fail(f"solver {s['solver']!r} has bad side {s['side']!r}")
        if s["arithmetic"] not in BC.ARITHMETICS:
            fail(f"solver {s['solver']!r} has bad arithmetic {s['arithmetic']!r}")
        if s["control"] not in BC.CONTROLS:
            fail(f"solver {s['solver']!r} has bad control {s['control']!r}")
        if s["cost_grade"] not in BC.COST_GRADES:
            fail(f"solver {s['solver']!r} has bad cost_grade {s['cost_grade']!r}")
        if s["timing_family"] not in BC.TIMING_FAMILIES:
            fail(f"solver {s['solver']!r} has bad timing_family "
                 f"{s['timing_family']!r}")

    prose: set[str] = {str(s["family"]) for s in solvers}

    seen: set[tuple] = set()
    for r in doc["matched_accuracy"]:
        for k in _MATCHED_ROW_KEYS:
            if k not in r:
                fail(f"matched_accuracy row missing {k!r}: "
                     f"{r.get('solver')} {r.get('problem')}")
        key = (r["solver"], r["problem"], r["target_key"])
        if key in seen:
            fail(f"duplicate matched_accuracy row {key}")
        seen.add(key)
        if r["solver"] not in keys:
            fail(f"matched_accuracy row names unknown solver {r['solver']!r}")
        if r["problem"] not in pnames:
            fail(f"matched_accuracy row names unlisted problem {r['problem']!r}")
        if r["status"] not in BC.STATUSES:
            fail(f"matched_accuracy row {key} has bad status {r['status']!r}")
        if r["cost_grade"] not in BC.COST_GRADES:
            fail(f"matched_accuracy row {key} has bad cost_grade")
        if r["target_key"] != repr(float(r["target_error"])):
            fail(f"matched_accuracy row {key} target_key does not round-trip")
        # A cycle number whose grade says there is no cycle model would be a
        # figure with nothing behind it.
        if r["cost_grade"] == "none" and r["analytic_cycles_total"] is not None:
            fail(f"matched_accuracy row {key} carries cycles with cost_grade none")
        if r["cost_grade"] == "none" and r["analytic_cycles_per_step"] is not None:
            fail(f"matched_accuracy row {key} carries per-step cycles with "
                 "cost_grade none")
        if r["status"] != "reached" and not r["reason"]:
            fail(f"matched_accuracy row {key} missed its target with no reason")
        if r["status"] == "reached" and r["control_value"] is None:
            fail(f"matched_accuracy row {key} reached its target with no control value")
        for k in ("controls_for", "not_controlled"):
            if not isinstance(r[k], list) or not r[k]:
                fail(f"matched_accuracy row {key} has an empty {k}")
            prose.update(str(x) for x in r[k])
        for k in ("note", "n_steps_rejected_basis", "reason", "control_unit"):
            if r.get(k):
                prose.add(str(r[k]))
        if r["timing"] is not None:
            _check_timing(r["timing"], f"matched_accuracy {key}", n_rep, fail)
    expect = {(s["solver"], p, repr(float(t)))
              for s in solvers for p in pnames for t in targets}
    if seen != expect:
        missing = sorted(expect - seen)[:3]
        extra = sorted(seen - expect)[:3]
        fail("matched_accuracy must cover every declared (solver, problem, "
             f"target) cell once; missing {missing}, unexpected {extra}")

    tol_rows = doc["adaptive_matched_tolerance"]
    tols = [float(t) for t in scope["adaptive_tols"]]
    seen_t: set[tuple] = set()
    for r in tol_rows:
        for k in ("solver", "class", "side", "arithmetic", "problem", "tol",
                  "rtol", "atol", "status", "achieved_error", "n_fevals",
                  "n_steps_accepted", "n_steps_rejected", "timing",
                  "timing_family", "cost_grade", "controls_for", "not_controlled"):
            if k not in r:
                fail(f"adaptive_matched_tolerance row missing {k!r}")
        key = (r["solver"], r["problem"], repr(float(r["tol"])))
        if key in seen_t:
            fail(f"duplicate adaptive_matched_tolerance row {key}")
        seen_t.add(key)
        if r["rtol"] != r["tol"] or r["atol"] != r["tol"]:
            fail(f"adaptive_matched_tolerance row {key} breaks rtol = atol = tol")
        if r["arithmetic"] == "q15":
            if r["tol_q_lsb"] != BC.q15_tolerance_lsb(r["tol"], r["scale"]):
                fail(f"adaptive_matched_tolerance row {key} tol_q_lsb does not "
                     "follow the stated conversion")
            if r["below_bias_floor"] is None or not r["bias_floor_basis"]:
                fail(f"adaptive_matched_tolerance row {key} has no bias floor verdict")
        if r["status"] != "ok" and not r["reason"]:
            fail(f"adaptive_matched_tolerance row {key} failed with no reason")
        if r["timing"] is not None:
            _check_timing(r["timing"], f"tolerance {key}", n_rep, fail)
        prose.update(str(x) for x in r["controls_for"] + r["not_controlled"])
    expect_t = {(k, p, repr(float(t))) for k in BC.PAIR_SOLVER_ORDER
                for p in pnames for t in tols}
    if seen_t != expect_t:
        fail("adaptive_matched_tolerance must cover every (solver, problem, "
             "tolerance) cell once")

    seen_p: set[tuple] = set()
    for e in doc["adaptive_pairs"]:
        key = (e["problem"], repr(float(e["tol"])))
        if key in seen_p:
            fail(f"duplicate adaptive_pairs entry {key}")
        seen_p.add(key)
        if not e.get("same_pair"):
            fail(f"adaptive_pairs entry {key} must state same_pair")
        fams = {v["timing_family"] for v in e["solvers"].values()}
        # Ours is a Python loop and RK23 is compiled; a wall-clock ratio between
        # them measures the interpreter, so the field exists and stays null.
        for k, val in e.items():
            if "time_ratio" in k and k != "time_ratio_reason" and val is not None:
                if len(fams) > 1 and not e.get("same_timing_family"):
                    fail(f"adaptive_pairs entry {key} carries {k} across two "
                         "timing families")
        if len(fams) > 1 and e.get("time_ratio_ours_over_rk23") is not None:
            fail(f"adaptive_pairs entry {key} times ours against RK23")
        for k in ("fevals_ratio_ours_over_rk23", "error_ratio_ours_over_rk23"):
            got = e[k]
            src = "n_fevals" if k.startswith("fevals") else "achieved_error"
            a = e["solvers"].get(e["ours_solver"], {}).get(src)
            b = e["solvers"].get(e["counterpart"], {}).get(src)
            want = (a / b if isinstance(a, (int, float)) and isinstance(b, (int, float))
                    and b else None)
            if want is None:
                if got is not None:
                    fail(f"adaptive_pairs entry {key} {k} has no rows behind it")
            elif got is None or not math.isclose(got, want, rel_tol=1e-9):
                fail(f"adaptive_pairs entry {key} {k} does not match its rows")
        for k in ("same_pair_note", "rk45_note", "ratios_are", "time_ratio_reason"):
            prose.add(str(e[k]))
    if seen_p != {(p, repr(float(t))) for p in pnames for t in tols}:
        fail("adaptive_pairs must cover every (problem, tolerance) cell once")

    budget = doc["implicit_budget"]
    if sorted(budget) != sorted(scope["resolved_budget_problems"]):
        fail("implicit_budget must cover exactly the declared problems")
    for name, e in sorted(budget.items()):
        for k in ("budget_cycles", "cycles_per_step", "steps_at_budget",
                  "error_at_budget", "ladder", "jacobian", "newton_iters",
                  "cost_grade", "library_column", "why_no_library"):
            if k not in e:
                fail(f"implicit_budget {name} missing {k!r}")
        if e["library_column"] is not None:
            fail(f"implicit_budget {name} must state no library column")
        if e["budget_cycles"] != BUDGET_CYCLES:
            fail(f"implicit_budget {name} budget_cycles must be {BUDGET_CYCLES}")
        if e["steps_at_budget"] != e["budget_cycles"] // e["cycles_per_step"]:
            fail(f"implicit_budget {name} steps_at_budget does not follow the "
                 "cycle estimate")
        for row in e["ladder"]:
            if row["analytic_cycles"] != row["n"] * e["cycles_per_step"]:
                fail(f"implicit_budget {name} ladder cycles are inconsistent")
        prose.add(str(e["why_no_library"]))

    classes = doc["classes"]
    if sorted(classes) != sorted(BC.CLASS_ORDER):
        fail(f"classes must cover {BC.CLASS_ORDER}")
    for cls, e in sorted(classes.items()):
        n = e["n_rows"]
        if n["matched_accuracy_ours"] < 1:
            fail(f"class {cls} contributed no rows of ours")
        # A class with no counterpart says so once, in data. Silence would let a
        # class vanish from a rebuild without anything noticing.
        if n["matched_accuracy_library"] < 1 and not e.get("library_absent_reason"):
            fail(f"class {cls} has no library row and no stated reason")
        if e["standing"] not in ("scored in the archive", "prototype, off-archive"):
            fail(f"class {cls} has an unknown standing")
        prose.add(str(e.get("library_absent_reason") or ""))

    comp = doc["comparability"]
    for k in ("shared_condition", "not_matched", "ratio_rule", "null_rule",
              "timing_families", "arithmetic", "cost_grades",
              "tolerance_rule_matched", "tolerance_rule_adaptive"):
        if k not in comp:
            fail(f"comparability missing {k!r}")
    prose.update(str(comp[k]) for k in ("shared_condition", "not_matched",
                                        "ratio_rule", "null_rule",
                                        "tolerance_rule_matched",
                                        "tolerance_rule_adaptive"))
    prose.update(str(x) for x in comp["timing_families"].values())
    prose.update(str(x) for x in comp["arithmetic"].values())
    for block in comp["cost_grades"].values():
        prose.update(str(x) for x in block.values())
    prose.add(str(doc["timing_policy"]))
    prose.add(str(doc["problem_sets"]["why_two_sets"]))

    tv = doc["verdicts"]["three_class"]
    for k in ("per_class", "adaptive_pair", "stiff", "cost_grades", "overall"):
        if k not in tv:
            fail(f"verdicts.three_class missing {k!r}")
    prose.update(str(x) for x in tv["per_class"].values())
    prose.update(str(tv[k]) for k in ("adaptive_pair", "stiff", "cost_grades",
                                      "overall"))
    return sorted(x for x in prose if x)


def re_search_word(word: str, text: str) -> bool:
    """Whole-word presence test (text passed lowercased)."""
    return re.search(rf"(?<![a-z0-9-]){re.escape(word)}(?![a-z0-9-])", text) is not None


# --------------------------------------------------------------------- write


def write_results(doc: dict, path: Path | str | None = None) -> Path:
    out = Path(path) if path is not None else work_dir() / "benchmark" / "results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, indent=1, sort_keys=True, allow_nan=False) + "\n"
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return out


def main() -> None:
    doc = build_results()
    validate_results(doc)
    out = write_results(doc)
    print(f"wrote {out}")
    print(f"methods: {', '.join(m['name_or_hash'][:12] for m in doc['methods'])}")
    v = doc["verdicts"]
    print(f"median ratio q15/library at matched tolerance: "
          f"{v['median_ratio_q15_over_library_at_matched_tolerance']}")
    print(f"median ratio q15/float rk4 at matched steps:   "
          f"{v['median_ratio_q15_over_float_rk4_at_matched_steps']}")
    print(f"cycles vs time pearson r: {doc['correlation']['pearson_r']} "
          f"over {doc['correlation']['n_points']} runs")
    sp = doc["speedup"]
    print(f"speedup {sp['champion'][:12]} vs rk4 (per step, geomean over "
          f"{sp['n_problems_compared']} problems): measured "
          f"{sp['geomean_measured_speedup_rk4_over_champion']} predicted "
          f"{sp['geomean_predicted_speedup_rk4_over_champion']}; champion "
          f"error lower in {sp['champion_error_lower_count']} of "
          f"{sp['error_comparisons']}, median error ratio "
          f"{sp['median_error_ratio_champion_over_rk4']}")
    pm = sp["per_method_us_per_step"]
    for mname in (sp["champion"], sp["baseline"]):
        if mname in pm:
            print(f"  {mname[:12]}: median {pm[mname]['median_us_per_step']} "
                  f"us/step over {pm[mname]['n_problems']} problems")
    for row in sp["rows"]:
        print(f"  {row['problem']}: champ {row.get('champion_us_per_step')} "
              f"us/step vs rk4 {row.get('rk4_us_per_step')} us/step, measured "
              f"{row.get('measured_ratio_rk4_over_champion')} predicted "
              f"{row['predicted_ratio_rk4_over_champion']:.3f}")
    for name, entry in v["per_problem"].items():
        print(f"  {name}: best library {entry.get('best_library')} "
              f"err {entry.get('best_library_error')}, best q15 "
              f"{str(entry.get('best_q15_method'))[:12]} err {entry.get('best_q15_error')}")
    print(v["overall"])
    tv = v["three_class"]
    print(f"matched accuracy: {len(doc['matched_accuracy'])} rows over "
          f"{len(doc['three_class_scope']['resolved_problems'])} application "
          f"problems")
    for cls in BC.CLASS_ORDER:
        c = doc["classes"][cls]
        print(f"  {cls}: ours {c['n_rows']['matched_accuracy_ours']} rows, "
              f"library {c['n_rows']['matched_accuracy_library']} rows, "
              f"reached {c['n_rows']['matched_accuracy_reached']}")
    print(tv["adaptive_pair"])
    print(tv["stiff"])
    for name, e in sorted(doc["implicit_budget"].items()):
        print(f"  sdirk2 on {name}: {e['steps_at_budget']} steps at "
              f"{e['budget_cycles']} cycles, error {e['error_at_budget']}")


if __name__ == "__main__":
    main()
