"""Side-track executor: the container's own adaptive and implicit research.

Plan: docs/SIDETRACK-AUTOMATION.md. Designs this feeds: docs/EPOCH2-DESIGN.md
(adaptive embedded pairs) and docs/EPOCH3-DESIGN.md (implicit SDIRK).

Nothing here is verifier-pinned and nothing here writes to the scored archive.
This module is not in verifier_hash.VERIFIER_FILES and must never be added to
it: side-track work exists precisely so that the epoch-2 and epoch-3 designs
arrive with measurements instead of guesses, and it can only do that from
outside the frozen scoring path.

The unit of work is a *point*: one member of a job's finite, deterministic plan.
A firing takes points that are not in the ledger yet and measures them until its
deadline, so repeated firings walk the plan instead of recomputing it. That
matters because the prototype artifacts are pure functions of the code, verified
byte-identical across reruns, so a scheduler that simply re-ran them would spend
CPU to learn nothing.

Two write rules keep the results trustworthy:

* An artifact is a pure function of (code, params). No clock, no host detail, no
  unseeded randomness. Reproducing a point means running it again and getting the
  same bytes.
* Every ledger line carries `code_hash`, a digest over this module and the
  prototypes. A point counts as measured only under the code that measured it, so
  editing a prototype re-opens its points instead of leaving stale numbers on the
  site.

CLI, in the style of rk_harness.saturation:

    python -m rk_harness.sidetrack --status
    python -m rk_harness.sidetrack --plan
    python -m rk_harness.sidetrack --run-one [--tracks adaptive|implicit|both]
    python -m rk_harness.sidetrack --run-until SECONDS
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Callable, Iterable, Sequence

from rk_harness.paths import HARNESS_DIR, work_dir

TRACKS: tuple[str, ...] = ("adaptive", "implicit")

# Digest inputs: this module plus every prototype it drives. A change to any of
# them re-opens the points they produced (see the module docstring).
SIDETRACK_FILES: tuple[str, ...] = (
    "rk_harness/sidetrack.py",
    "rk_harness/prototypes/__init__.py",
    "rk_harness/prototypes/adaptive.py",
    "rk_harness/prototypes/sdirk.py",
)


def code_hash() -> str:
    h = hashlib.sha256()
    for rel in SIDETRACK_FILES:
        with open(HARNESS_DIR / rel, "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()[:16]


# --------------------------------------------------------------------------- types

@dataclass(frozen=True)
class Point:
    job: str
    track: str
    key: str
    params: dict

    @property
    def ident(self) -> tuple[str, str]:
        return (self.job, self.key)


@dataclass(frozen=True)
class Job:
    name: str
    track: str
    closes: str                                   # the design-doc question it answers
    plan: Callable[[], list[tuple[str, dict]]]    # (key, params), deterministic and finite
    run: Callable[[dict], dict]                   # params -> artifact document

    def points(self) -> list[Point]:
        return [Point(self.name, self.track, key, params) for key, params in self.plan()]


# --------------------------------------------------------------------------- paths and ledger

def sidetrack_dir() -> Path:
    return work_dir() / "sidetrack"


def ledger_path() -> Path:
    return sidetrack_dir() / "ledger.jsonl"


def artifact_path(job: str, key: str) -> Path:
    return sidetrack_dir() / job / f"{key}.json"


def load_ledger() -> list[dict]:
    path = ledger_path()
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _append_ledger(entry: dict) -> None:
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _jsonable(obj):
    """Fractions to strings, tuples to lists, floats left alone. Artifacts have to
    round-trip through JSON without losing the exact values the algebra produced."""
    if isinstance(obj, Fraction):
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def _write_artifact(job: str, key: str, doc: dict) -> Path:
    """Atomic write. The artifact lands before its ledger line, so a crash can
    leave an artifact nothing points at, never a ledger line without data."""
    path = artifact_path(job, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(_jsonable(doc), fh, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return path


# --------------------------------------------------------------------------- scheduling

def jobs_for(tracks: Iterable[str]) -> list[Job]:
    wanted = set(tracks)
    return [j for j in JOBS if j.track in wanted]


# A point that fails this many times under one code hash is set aside. Without a cap a
# deterministically broken point would be retried on every firing forever, and inside a
# firing it would be retried until the budget ran out, because a failure deliberately
# does not mark a point measured.
MAX_FAILURES_PER_POINT: int = 3


def done_idents(ledger: Sequence[dict] | None = None, ch: str | None = None) -> set[tuple[str, str]]:
    """(job, key) pairs already measured under the current code hash."""
    led = load_ledger() if ledger is None else ledger
    cur = code_hash() if ch is None else ch
    return {(str(e.get("job")), str(e.get("key")))
            for e in led if e.get("code_hash") == cur and e.get("status") == "ok"}


def poisoned_idents(ledger: Sequence[dict] | None = None, ch: str | None = None) -> set[tuple[str, str]]:
    """(job, key) pairs that have failed MAX_FAILURES_PER_POINT times under this code."""
    led = load_ledger() if ledger is None else ledger
    cur = code_hash() if ch is None else ch
    counts: dict[tuple[str, str], int] = {}
    for e in led:
        if e.get("code_hash") == cur and e.get("status") == "failed":
            ident = (str(e.get("job")), str(e.get("key")))
            counts[ident] = counts.get(ident, 0) + 1
    return {ident for ident, n in counts.items() if n >= MAX_FAILURES_PER_POINT}


def remaining(tracks: Iterable[str] = TRACKS, ledger: Sequence[dict] | None = None,
              skip: Iterable[tuple[str, str]] = ()) -> list[Point]:
    led = load_ledger() if ledger is None else ledger
    closed = done_idents(led) | poisoned_idents(led) | set(skip)
    out: list[Point] = []
    for job in jobs_for(tracks):
        out.extend(p for p in job.points() if p.ident not in closed)
    return out


def next_point(tracks: Iterable[str] = TRACKS, ledger: Sequence[dict] | None = None,
               skip: Iterable[tuple[str, str]] = ()) -> Point | None:
    """The next point to measure, balancing the two tracks.

    The track with fewer completed points goes first, so the rotation is a
    function of the ledger rather than of state this process has to keep. Within
    a track, jobs run in catalogue order and each job finishes before the next
    starts. `skip` carries the points already attempted in the current firing.
    """
    led = load_ledger() if ledger is None else ledger
    left = remaining(tracks, led, skip=skip)
    if not left:
        return None
    done = done_idents(led)
    counts = {t: 0 for t in TRACKS}
    for job in JOBS:
        for p in job.points():
            if p.ident in done:
                counts[job.track] = counts.get(job.track, 0) + 1
    order = sorted(TRACKS, key=lambda t: (counts.get(t, 0), TRACKS.index(t)))
    for track in order:
        for p in left:
            if p.track == track:
                return p
    return left[0]


# --------------------------------------------------------------------------- running

def _noop_log(kind: str, **detail) -> None:
    return None


def run_point(point: Point, cycle: int = 0, log: Callable[..., None] = _noop_log,
              ts: str | None = None) -> dict:
    """Measure one point and record it. Returns the ledger entry.

    A job that raises is recorded as a failed point rather than being allowed to
    reach the caller: a side-track failure must never cost a cycle. Failed
    entries do not mark the point done, so the next firing retries it.
    """
    job = JOBS_BY_NAME[point.job]
    started = time.monotonic()
    entry: dict = {
        "ts": ts or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cycle": int(cycle),
        "track": point.track,
        "job": point.job,
        "key": point.key,
        "code_hash": code_hash(),
        "status": "ok",
    }
    log("sidetrack_started", job=point.job, key=point.key, track=point.track, cycle=int(cycle))
    try:
        doc = job.run(dict(point.params))
    except Exception as e:                    # noqa: BLE001 - research must never fail the cycle
        entry["status"] = "failed"
        entry["error"] = repr(e)[:300]
        entry["duration_s"] = round(time.monotonic() - started, 3)
        _append_ledger(entry)
        log("sidetrack_failed", job=point.job, key=point.key, error=entry["error"])
        return entry
    doc = dict(doc)
    doc.setdefault("job", point.job)
    doc.setdefault("key", point.key)
    doc.setdefault("params", point.params)
    doc.setdefault("closes", job.closes)
    path = _write_artifact(point.job, point.key, doc)
    entry["duration_s"] = round(time.monotonic() - started, 3)
    entry["artifact"] = f"sidetrack/{point.job}/{point.key}.json"
    entry["summary"] = _jsonable(doc.get("summary", {}))
    _append_ledger(entry)
    log("sidetrack_done", job=point.job, key=point.key, track=point.track,
        duration_s=entry["duration_s"], artifact=entry["artifact"],
        summary=entry["summary"])
    return entry


def run_until(budget_seconds: float, tracks: Iterable[str] = TRACKS, cycle: int = 0,
              log: Callable[..., None] = _noop_log,
              stop: Callable[[], bool] | None = None) -> dict:
    """Measure points until the budget is spent, the plan is exhausted, or `stop`
    says to quit. Returns a summary of the firing.

    The budget gates *starting* a point, not finishing one: a point that has begun
    runs to completion so that its artifact stays a complete, reproducible
    document. Points are bounded by their own deterministic work caps, so the
    overrun is bounded too; the plan document carries the arithmetic.
    """
    started = time.monotonic()
    out = {"points": [], "exhausted": False, "budget_seconds": float(budget_seconds)}
    attempted: set[tuple[str, str]] = set()
    while True:
        if stop is not None and stop():
            log("sidetrack_skipped", reason="stop requested", cycle=int(cycle))
            break
        if time.monotonic() - started >= budget_seconds and out["points"]:
            break
        point = next_point(tracks, skip=attempted)
        if point is None:
            out["exhausted"] = True
            log("sidetrack_exhausted", tracks=sorted(set(tracks)), cycle=int(cycle))
            break
        attempted.add(point.ident)          # one attempt per point per firing
        entry = run_point(point, cycle=cycle, log=log)
        out["points"].append({k: entry.get(k) for k in ("job", "key", "track", "status", "duration_s")})
        if time.monotonic() - started >= budget_seconds:
            break
    out["elapsed_s"] = round(time.monotonic() - started, 3)
    return out


def status(tracks: Iterable[str] = TRACKS) -> dict:
    led = load_ledger()
    done = done_idents(led)
    dead = poisoned_idents(led)
    jobs = []
    for job in jobs_for(tracks):
        pts = job.points()
        jobs.append({
            "job": job.name,
            "track": job.track,
            "planned": len(pts),
            "done": sum(1 for p in pts if p.ident in done),
            "set_aside": sum(1 for p in pts if p.ident in dead),
            "closes": job.closes,
        })
    return {
        "code_hash": code_hash(),
        "ledger_lines": len(led),
        "planned_total": sum(j["planned"] for j in jobs),
        "done_total": sum(j["done"] for j in jobs),
        "set_aside_total": sum(j["set_aside"] for j in jobs),
        "jobs": jobs,
    }


# =========================================================================== the catalogue
#
# Every job closes a question one of the two design documents leaves open. A job
# that does not close a named question does not belong here.

# The T8 validation names, hardcoded so plan() stays cheap and importing this
# module does not drag in the validation suite. test_S13 asserts they still match
# validation.VALIDATION_NAMES.
VALIDATION_NAMES: tuple[str, ...] = (
    "buck_converter", "battery_2rc", "bicycle_lateral", "pll_lock",
    "glucose_minimal", "servo_load_step", "enzyme_qssa", "robertson_scaled",
)
STIFF_NAMES: tuple[str, ...] = ("servo_load_step", "enzyme_qssa", "robertson_scaled")

SWEEP_TOLS: tuple[float, ...] = (1e-3, 1e-4, 1e-5, 1e-6, 1e-7, 1e-8)
SWEEP_MAX_ATTEMPTS: int = 200_000        # deterministic work cap, not a clock

GAIN_PROBLEMS: tuple[str, ...] = ("buck_converter", "pll_lock", "glucose_minimal")
GAIN_TOL: float = 1e-6
ALPHAS: tuple[tuple[int, int], ...] = ((1, 8), (1, 4), (3, 8), (1, 2))
BETAS: tuple[tuple[int, int], ...] = ((0, 1), (1, 16), (1, 8), (1, 4))

STIFF_LADDER: tuple[int, ...] = (8, 16, 32, 64, 128, 256)
STIFF_METHODS: tuple[str, ...] = ("euler", "heun2", "midpoint", "rk4")
ORDER_LADDER: tuple[int, ...] = (16, 32, 64, 128)
GAMMA_EXPONENTS: tuple[int, ...] = (4, 5, 6, 7, 8, 9, 10, 11, 12)
GAMMA_WIDTH: int = 8
NEWTON_ITER_CHOICES: tuple[int, ...] = (1, 2, 3, 4)


def _divergence_bound(name: str) -> float:
    """A deterministic bound on the state, from the problem's own scale.

    Explicit methods on the stiff members of the suite blow up; without a bound
    they take tens of seconds to do it, because Python floats only overflow after
    a long climb. Stopping at a data threshold keeps the outcome a pure function
    of the inputs, which a wall-clock cutoff would not.
    """
    from rk_harness import validation as V

    prob = V.PROBLEMS[name]
    ref = prob.reference(prob.t_end)
    span = max([abs(v) for v in V.Y0_PHYS[name]] + [abs(v) for v in ref] + [1.0])
    return 1.0e6 * span


def _solve_float_guarded(tab, rhs, y0: tuple[float, ...], t_end: float, n: int,
                         bound: float) -> tuple[float, ...]:
    from rk_harness.simulate import float_tableau, rk_step_float

    A, b, c = float_tableau(tab)
    h = t_end / n
    y = tuple(float(v) for v in y0)
    for k in range(n):
        y = rk_step_float(A, b, c, rhs, k * h, y, h)
        for v in y:
            if not (abs(v) <= bound):          # catches NaN too
                raise OverflowError(f"state {v} past the divergence bound at step {k + 1}")
    return y


def _fixed_step_row(method: str, name: str, n: int, bound: float, spec=None) -> dict:
    """One (method, n) measurement on a validation problem, failure included."""
    from rk_harness import validation as V
    from rk_harness.prototypes.sdirk import DEFAULT_SPEC, solve_sdirk2
    from rk_harness.tableau import classical

    rhs = V.FLOAT_RHS[name]
    y0 = V.Y0_PHYS[name]
    t_end = V.PROBLEMS[name].t_end
    try:
        if method == "sdirk2":
            y = solve_sdirk2(rhs, y0, t_end, n, spec=spec or DEFAULT_SPEC, diverge_at=bound)
        else:
            y = _solve_float_guarded(classical()[method], rhs, y0, t_end, n, bound)
        err = V.validation_error(name, y)
    except (OverflowError, ZeroDivisionError, ValueError):
        return {"n": n, "status": "diverged", "error": None}
    if not math.isfinite(err):
        return {"n": n, "status": "diverged", "error": None}
    return {"n": n, "status": "ok", "error": err}


def _measured_order_dahlquist(spec) -> dict:
    """Order of an SDIRK2 spec on dahlquist, from consecutive halvings."""
    from rk_harness.problems import FLOAT_RHS, PROBLEMS
    from rk_harness.prototypes.sdirk import solve_sdirk2

    prob = PROBLEMS["dahlquist"]
    rhs = FLOAT_RHS["dahlquist"]
    y0 = prob.reference(0.0)
    ref = prob.reference(prob.t_end)
    errs: list[float | None] = []
    for n in ORDER_LADDER:
        try:
            y = solve_sdirk2(rhs, y0, prob.t_end, n, spec=spec, diverge_at=1.0e6)
        except (OverflowError, ZeroDivisionError, ValueError):
            errs.append(None)
            continue
        e = abs(y[0] - ref[0])
        errs.append(e if math.isfinite(e) else None)
    orders: list[float | None] = []
    for i in range(len(errs) - 1):
        a, b = errs[i], errs[i + 1]
        orders.append(round(math.log2(a / b), 4) if a and b and a > 0 and b > 0 else None)
    good = [o for o in orders if o is not None]
    return {"errors": errs, "orders": orders,
            "order_estimate": round(good[-1], 4) if good else None}


# ------------------------------------------------------------------ J1 adaptive.suite_sweep

def _plan_suite_sweep() -> list[tuple[str, dict]]:
    return [(name, {"problem": name}) for name in VALIDATION_NAMES]


def _run_suite_sweep(params: dict) -> dict:
    from rk_harness.prototypes.adaptive import sweep_point

    name = str(params["problem"])
    points = [sweep_point(name, tol, max_attempts=SWEEP_MAX_ATTEMPTS) for tol in SWEEP_TOLS]
    ok = [p for p in points if p["status"] == "ok"]
    return {
        "schema": "one entry per tolerance: status, and when the run finished, the "
                  "accepted/rejected step counts, the exact function-evaluation count "
                  "(FSAL reuse included) and the final error under the T8 metric",
        "arithmetic": "float64 only; no Q15 effects are included",
        "pair": "Bogacki-Shampine 3(2)",
        "problem": name,
        "tolerances": list(SWEEP_TOLS),
        "max_attempts": SWEEP_MAX_ATTEMPTS,
        "points": points,
        "summary": {
            "points": len(points),
            "finished": len(ok),
            "fevals_min": min([p["n_fevals"] for p in ok], default=None),
            "fevals_max": max([p["n_fevals"] for p in ok], default=None),
            "rejection_rate_max": max(
                [p["n_rejected"] / max(1, p["n_accepted"] + p["n_rejected"]) for p in ok],
                default=None),
            "statuses": sorted({p["status"] for p in points}),
        },
    }


# --------------------------------------------------------------- J2 adaptive.controller_gains

def _plan_controller_gains() -> list[tuple[str, dict]]:
    out = []
    for an, ad in ALPHAS:
        for bn, bd in BETAS:
            out.append((f"a{an}o{ad}_b{bn}o{bd}",
                        {"alpha": [an, ad], "beta": [bn, bd]}))
    return out


def _run_controller_gains(params: dict) -> dict:
    from rk_harness.prototypes.adaptive import sweep_point

    an, ad = params["alpha"]
    bn, bd = params["beta"]
    alpha, beta = an / ad, bn / bd
    points = [sweep_point(name, GAIN_TOL, alpha=alpha, beta=beta,
                          max_attempts=SWEEP_MAX_ATTEMPTS)
              for name in GAIN_PROBLEMS]
    ok = [p for p in points if p["status"] == "ok"]
    total_fev = sum(p["n_fevals"] for p in ok) if ok else None
    total_rej = sum(p["n_rejected"] for p in ok) if ok else None
    total_acc = sum(p["n_accepted"] for p in ok) if ok else None
    return {
        "schema": "one entry per reference problem at a fixed tolerance, under the "
                  "given PI controller gains; the epoch-2 question is which dyadic "
                  "gains to freeze into the Q15 controller table",
        "arithmetic": "float64 only; no Q15 effects are included",
        "pair": "Bogacki-Shampine 3(2)",
        "alpha": alpha, "beta": beta,
        "alpha_exact": f"{an}/{ad}", "beta_exact": f"{bn}/{bd}",
        "tolerance": GAIN_TOL,
        "problems": list(GAIN_PROBLEMS),
        "points": points,
        "summary": {
            "points": len(points), "finished": len(ok),
            "total_fevals": total_fev,
            "total_accepted": total_acc,
            "total_rejected": total_rej,
            "rejection_rate": (round(total_rej / (total_acc + total_rej), 5)
                               if total_acc is not None and (total_acc + total_rej) else None),
            "max_error": max([p["achieved_error"] for p in ok], default=None),
        },
    }


# ------------------------------------------------------------------- J3 sdirk.stiff_suite

def _plan_stiff_suite() -> list[tuple[str, dict]]:
    return [(name, {"problem": name}) for name in STIFF_NAMES]


def _run_stiff_suite(params: dict) -> dict:
    from rk_harness.costmodel import M0PLUS_FAST, cycle_count
    from rk_harness.prototypes.sdirk import estimate_sdirk2_cycles
    from rk_harness.tableau import classical
    from rk_harness import validation as V

    name = str(params["problem"])
    bound = _divergence_bound(name)
    n_states = V.PROBLEMS[name].n_states
    cl = classical()
    methods: dict[str, dict] = {}
    for m in STIFF_METHODS + ("sdirk2",):
        rows = [_fixed_step_row(m, name, n, bound) for n in STIFF_LADDER]
        stable = [r["n"] for r in rows if r["status"] == "ok"]
        if m == "sdirk2":
            est = estimate_sdirk2_cycles(n_states, M0PLUS_FAST, fd=True)
            cycles, fev = est["total"], est["f_evals_per_step"]
        else:
            cycles, fev = cycle_count(cl[m], M0PLUS_FAST, n_states), len(cl[m].b)
        methods[m] = {
            "est_cycles_per_step": cycles,
            "f_evals_per_step": fev,
            "min_stable_n": min(stable) if stable else None,
            "best_error": min([r["error"] for r in rows if r["error"] is not None], default=None),
            "points": rows,
        }
    finishers = [m for m, d in methods.items() if d["min_stable_n"] is not None]
    return {
        "schema": "step count vs error for the explicit anchors and SDIRK2 on one stiff "
                  "member of the T8 validation suite; a diverged entry is a result, not "
                  "a gap",
        "arithmetic": "float64 only; no Q15 effects are included",
        "note": "cycle estimates exclude rhs and Jacobian evaluations, matching "
                "costmodel.cycle_count; SDIRK2 is priced with a finite-difference "
                "Jacobian because the validation problems supply no analytic one",
        "problem": name,
        "stiffness_ratio": V.STIFFNESS_RATIO.get(name),
        "n_states": n_states,
        "divergence_bound": bound,
        "ladder": list(STIFF_LADDER),
        "methods": methods,
        "summary": {
            "finishers": sorted(finishers),
            "explicit_finishers": sorted(m for m in finishers if m != "sdirk2"),
            "sdirk2_min_stable_n": methods["sdirk2"]["min_stable_n"],
            "rk4_min_stable_n": methods["rk4"]["min_stable_n"],
            "sdirk2_best_error": methods["sdirk2"]["best_error"],
        },
    }


# -------------------------------------------------------------- J4 sdirk.gamma_dyadic_scan

def _plan_gamma_scan() -> list[tuple[str, dict]]:
    return [(f"s{s:02d}", {"s": s}) for s in GAMMA_EXPONENTS]


def _run_gamma_scan(params: dict) -> dict:
    from rk_harness.prototypes.sdirk import (
        GAMMA, dyadic_neighbours, order2_tableau_exact, spec_from_exact)

    s = int(params["s"])
    rows = []
    for gamma in dyadic_neighbours(GAMMA, s, GAMMA_WIDTH):
        try:
            t = order2_tableau_exact(gamma)
        except ValueError:
            continue
        order = _measured_order_dahlquist(spec_from_exact(t))
        rows.append({
            "gamma": gamma,
            "gamma_float": float(gamma),
            "numerator": gamma.numerator,
            "denominator_exponent": (gamma.denominator - 1).bit_length(),
            "b": list(t["b"]),
            "a21": t["a21"],
            "r_at_infinity": t["r_at_infinity"],
            "r_at_infinity_float": float(t["r_at_infinity"]),
            "l_stable": t["l_stable"],
            "a_stable": t["a_stable"],
            "a_stable_exact": t["a_stable_exact"],
            "stiffly_accurate": t["stiffly_accurate"],
            "order3_residuals": list(t["order3_residuals"]),
            "measured_order": order["order_estimate"],
        })
    a_stable = [r for r in rows if r["a_stable"]]
    best = min(a_stable, key=lambda r: abs(r["r_at_infinity_float"]), default=None)
    return {
        "schema": "one row per dyadic gamma near 1 - sqrt(2)/2 at this denominator "
                  "exponent: the exactly solved order-2 tableau, its L-stability margin "
                  "|R(inf)|, whether it is A-stable, and its measured order on dahlquist",
        "arithmetic": "tableau and stability algebra exact over Fractions; the measured "
                      "order is float64",
        "construction": "gamma is snapped to a dyadic, a21 is fixed at 1 - gamma so that "
                        "c2 = 1, and b is solved from the two order conditions. Stiff "
                        "accuracy and order 2 can only hold together at the irrational "
                        "gamma, so order is the one kept.",
        "denominator_exponent": s,
        "window": GAMMA_WIDTH,
        "rows": rows,
        "summary": {
            "candidates": len(rows),
            "a_stable": len(a_stable),
            "l_stable": sum(1 for r in rows if r["l_stable"]),
            "best_gamma": best["gamma"] if best else None,
            "best_r_at_infinity": best["r_at_infinity_float"] if best else None,
            "best_measured_order": best["measured_order"] if best else None,
        },
    }


# --------------------------------------------------------------- J5 sdirk.newton_iters

def _plan_newton_iters() -> list[tuple[str, dict]]:
    return [(f"it{it}", {"newton_iters": it}) for it in NEWTON_ITER_CHOICES]


def _run_newton_iters(params: dict) -> dict:
    from rk_harness.costmodel import M0PLUS_FAST
    from rk_harness.prototypes.sdirk import DEFAULT_SPEC, Sdirk2Spec, estimate_sdirk2_cycles
    from rk_harness import validation as V

    it = int(params["newton_iters"])
    spec = Sdirk2Spec(gamma=DEFAULT_SPEC.gamma, a21=DEFAULT_SPEC.a21, b=DEFAULT_SPEC.b,
                      c=DEFAULT_SPEC.c, newton_iters=it)
    order = _measured_order_dahlquist(spec)
    stiff: dict[str, dict] = {}
    for name in STIFF_NAMES:
        bound = _divergence_bound(name)
        rows = [_fixed_step_row("sdirk2", name, n, bound, spec=spec) for n in (32, 64, 128)]
        stiff[name] = {
            "points": rows,
            "best_error": min([r["error"] for r in rows if r["error"] is not None], default=None),
            "est_cycles_per_step": estimate_sdirk2_cycles(
                V.PROBLEMS[name].n_states, M0PLUS_FAST, newton_iters=it, fd=True)["total"],
        }
    return {
        "schema": "measured order on dahlquist plus stiff-suite error and per-step cycle "
                  "estimate at a fixed modified-Newton iteration count",
        "arithmetic": "float64 only; no Q15 effects are included",
        "question": "EPOCH3-DESIGN.md: three iterations is the prototype's setting, not a "
                    "ruling; the count enters the pinned evaluator config at the epoch "
                    "boundary, so it should be chosen on data",
        "newton_iters": it,
        "dahlquist": order,
        "stiff": stiff,
        "summary": {
            "newton_iters": it,
            "order_estimate": order["order_estimate"],
            "stiff_best_errors": {k: v["best_error"] for k, v in stiff.items()},
            "cycles_per_step_2_states": estimate_sdirk2_cycles(
                2, M0PLUS_FAST, newton_iters=it, fd=True)["total"],
        },
    }


# --------------------------------------------------------------------------- registry

JOBS: tuple[Job, ...] = (
    Job("adaptive.suite_sweep", "adaptive",
        "EPOCH2-DESIGN section 5: the scored tolerance ladder is unchosen, and the "
        "published curve covers 3 of the 8 validation problems",
        _plan_suite_sweep, _run_suite_sweep),
    Job("adaptive.controller_gains", "adaptive",
        "EPOCH2-DESIGN section 4: the dyadic PI gains rest on one measurement, and the "
        "classical alpha for a 3(2) pair is 1/3 rather than 1/4",
        _plan_controller_gains, _run_controller_gains),
    Job("sdirk.stiff_suite", "implicit",
        "EPOCH3-DESIGN: the stiff case rests on rc_thermal and a synthetic two-rate "
        "system, while the suite now has three purpose-built stiff problems",
        _plan_stiff_suite, _run_stiff_suite),
    Job("sdirk.gamma_dyadic_scan", "implicit",
        "EPOCH3-DESIGN: L-stability is to be checked numerically for the snapped gamma, "
        "and the NOT_L_STABLE threshold is a placeholder",
        _plan_gamma_scan, _run_gamma_scan),
    Job("sdirk.newton_iters", "implicit",
        "EPOCH3-DESIGN: three Newton iterations is the prototype's setting, not a ruling",
        _plan_newton_iters, _run_newton_iters),
)

JOBS_BY_NAME: dict[str, Job] = {j.name: j for j in JOBS}


# --------------------------------------------------------------------------- CLI

def parse_tracks(value: str) -> tuple[str, ...]:
    v = (value or "both").strip().lower()
    if v in ("both", "all", ""):
        return TRACKS
    if v == "off":
        return ()
    return tuple(t for t in TRACKS if t == v)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    tracks = TRACKS
    if "--tracks" in args:
        i = args.index("--tracks")
        tracks = parse_tracks(args[i + 1] if i + 1 < len(args) else "both")
        del args[i:i + 2]

    def echo(kind: str, **detail) -> None:
        print(json.dumps({"kind": kind, **_jsonable(detail)}), file=sys.stderr)

    if args[:1] == ["--status"]:
        print(json.dumps(status(tracks), indent=1))
        return 0
    if args[:1] == ["--plan"]:
        print(json.dumps([{"job": p.job, "track": p.track, "key": p.key, "params": p.params}
                          for p in (q for j in jobs_for(tracks) for q in j.points())], indent=1))
        return 0
    if args[:1] == ["--run-one"]:
        p = next_point(tracks)
        if p is None:
            print(json.dumps({"exhausted": True}))
            return 0
        print(json.dumps(_jsonable(run_point(p, log=echo)), indent=1))
        return 0
    if args[:1] == ["--run-until"]:
        seconds = float(args[1]) if len(args) > 1 else 60.0
        print(json.dumps(_jsonable(run_until(seconds, tracks, log=echo)), indent=1))
        return 0
    print(json.dumps(status(tracks), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
