"""Library benchmark with measured wall clock (T10, lead track part 2).

Evaluates, on the seven frozen scored problems from ``rk_harness.problems``:

* SciPy ``solve_ivp`` with RK45, Radau, BDF and LSODA at tolerances matched to
  the Q15 error scale (matching rule: ``rtol = 2**-15`` and ``atol = 2**-15 /
  scale``, one Q15 least significant bit, the atol expressed in physical units),
* a hand-rolled float64 fixed-step rk4 at the step counts the 65,536-cycle
  budget implies for each Q15 method,
* the Q15 champion methods and classical rk4 through the pinned ``solve_q15``
  machinery, with tableaus fetched from ``rk-work/validation/results.json``
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
"""
from __future__ import annotations

import os

# Cap BLAS/OpenMP threads before NumPy or SciPy load so every timed run is
# single-threaded. Harmless if the libraries are already resident.
_THREAD_VARS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")
for _v in _THREAD_VARS:
    os.environ.setdefault(_v, "1")

import gc
import json
import math
import platform
import re
import statistics
import time
from pathlib import Path

import numpy as np

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
SCIPY_INTEGRATORS = ("RK45", "Radau", "BDF", "LSODA")
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
    """Classical rk4 plus every discovered method from a validation results
    document. Tableaus are parsed from the exact fraction strings; a discovered
    entry whose content hash disagrees with its recorded name is a provenance
    failure and raises ValueError."""
    out: list[dict] = []
    for m in doc["methods"]:
        name = m["name_or_hash"]
        kind = m["kind"]
        if kind == "classical" and name != "rk4":
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
                 "rtol": rtol, "atol": atol}
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
                 "n_steps": n, "cycles_per_step": cps, "total_cycles": cps * n}
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
    site can chart measured time directly."""
    per: dict[str, dict[str, float]] = {}
    for r in fixed_rows:
        q = r["q15"]
        if q.get("status") == "ok" and "per_step_median_s" in q:
            per.setdefault(r["method"], {})[r["problem"]] = round(
                q["per_step_median_s"] * 1e6, 3)
    out: dict[str, dict] = {}
    for m, vals in per.items():
        us = sorted(vals.values())
        out[m] = {
            "per_problem_us_per_step": vals,
            "median_us_per_step": _fin(statistics.median(us)),
            "min_us_per_step": us[0],
            "max_us_per_step": us[-1],
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


# ------------------------------------------------------------------ document


_SCHEMA_DOC: dict[str, str] = {
    "budget_cycles": "shared cycle budget that sets every fixed-step run's step count",
    "cost_model": "cost model used for the budget to step-count mapping",
    "tolerance_rule": "how scipy rtol/atol were matched to the Q15 error scale",
    "rounding": "Q15 multiply semantics of the pinned solver (floor / ASRS)",
    "generated_from": "validation_results: file the method tableaus came from; "
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
    "methods": "fixed-step methods under benchmark: classical rk4 plus every "
               "discovered method from the validation document, with kind, "
               "roles, order, stages, tableau (exact fraction strings), "
               "cycles_per_step and steps per problem, and archive provenance "
               "for discovered entries",
    "adaptive_results": "table 1, one row per (scipy integrator, problem): "
                        "accuracy at matched tolerance with wall clock. Fields: "
                        "rtol, atol, status ok|failed|skipped (reason on "
                        "non-ok), error (final-state error_metric, null when "
                        "unavailable), n_steps_accepted, nfev, timing, "
                        "per_step_median_s. Adaptive integrators choose their "
                        "own steps, so these timings are never same-work "
                        "comparisons against the fixed-step table",
    "fixed_step_results": "table 2, one row per (Q15 method, problem) at the "
                          "budgeted step count n_steps: like-for-like cells "
                          "holding a q15 object (pinned solve_q15 run: status, "
                          "error, max_abs_q, timing, per_step_median_s) and a "
                          "float_rk4 object (hand-rolled float64 rk4 at the "
                          "identical step count: status, error, timing, "
                          "per_step_median_s), plus cycles_per_step and "
                          "total_cycles from the analytic model",
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
               "per method across problems) for direct charting",
    "verdicts": "per_problem best library and best Q15 entries with error "
                "ratios, the two median ratios, and honest prose for the "
                "matched-tolerance table, the fixed-step table, the cycle "
                "model, and overall",
    "caveats": "the reading rules: what these numbers do and do not support",
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
]


def build_results(validation_doc: dict | None = None,
                  n_repeats: int = N_REPEATS,
                  warmup: int = N_WARMUP) -> dict:
    """The full benchmark document. Deterministic except the timing objects."""
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

    doc = {
        "schema": _SCHEMA_DOC,
        "budget_cycles": BUDGET_CYCLES,
        "cost_model": COST_MODEL.name,
        "tolerance_rule": TOLERANCE_RULE,
        "rounding": "floor (ASRS), per HANDOFF 4.2",
        "generated_from": {
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
                "correlation", "speedup", "verdicts", "caveats"):
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
        for k in ("roles", "order", "stages", "tableau", "cycles_per_step", "steps"):
            if k not in m:
                fail(f"method {m['name_or_hash']!r} missing {k!r}")
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

    v = doc["verdicts"]
    for k in ("per_problem", "matched_tolerance", "fixed_step", "cycle_model",
              "overall"):
        if k not in v:
            fail(f"verdicts missing {k!r}")
    prose = [v["matched_tolerance"], v["fixed_step"], v["cycle_model"],
             v["overall"], sp["regime"], sp["caveat"], sp["prose"],
             *doc["caveats"]]
    for text in prose:
        low = str(text).lower()
        for w in _BANNED:
            if re_search_word(w, low):
                fail(f"banned word {w!r} in verdict/caveat prose")
        if "—" in str(text):
            fail("em dash in verdict/caveat prose")


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


if __name__ == "__main__":
    main()
