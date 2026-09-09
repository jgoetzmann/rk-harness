"""Two-axis validation document (T17). New module; nothing pinned is modified, and
``rk_harness/validation.py`` is not modified either.

The out-of-band validation suite asks one question: what error does a method reach
when a fixed 65,536-cycle budget is spent on it. That is a fair question to ask a
fixed-step method and a meaningless one to ask an adaptive method, which is handed
a tolerance and chooses its own steps. So the suite has one axis and one class can
stand on it. This module adds the second axis, cycles-to-tolerance, and puts all
three classes on it under ONE shared error metric, ``validation.validation_error``.

* axis F, fixed budget: what error a fixed cycle budget buys. Control is a step
  count. Explicit and implicit can be asked; an adaptive method cannot.
* axis T, cycles to tolerance: what cycles a fixed error costs. Control is a step
  count for explicit and implicit and a tolerance for adaptive. All three can be
  asked, because the answer is read off the ACHIEVED error, which is measured the
  same way for every class.

A row on one axis is not a row on the other. The two touch at ``matched_point``
and nowhere else, and the document says so in a field rather than in a comment.

WHY A SIBLING DOCUMENT AND NOT A v2 OF results.json. ``rk-work/validation/results.json``
is read every cycle by the live ``sitegen``, and its numbers are published.
``sitegen._vlabel`` truncates any name whose kind is not "classical" to eight
characters and ``sitegen._validation_chart`` colours on kind rather than on class,
so an SDIRK row dropped into ``results`` would render as a mislabelled classical
row on a published page. A sibling file has no live reader, so this document and
the page work that renders it can land in either order without a wrong page ever
being published. results.json keeps its v1 shape, its
``len(results) == len(methods) * len(problems)`` invariant, and its bytes.

WHY NOT EDIT validation.py EITHER. ``sidetrack.SIDETRACK_FILES`` includes
``rk_harness/validation.py``, so editing it changes ``sidetrack.code_hash()`` the
instant the bytes land on the container's read-only mount, which re-opens all 103
measured side-track points. This module imports validation read-only and is
deliberately NOT added to SIDETRACK_FILES: it consumes prototypes, it does not
produce side-track points.

THE ADAPTIVE COST NUMBER, which is the hard part of this document.

``costmodel.cycle_count`` prices a STEP. An adaptive method spends a variable
number of accepted steps, plus rejected steps that cost real work and yield no
progress, plus an error estimate and a controller update on every attempt that the
pinned cost model has no term for. The comparable number is assembled here out of
machinery that already exists, without touching a pinned file:

    stage_cycles      = cycle_count(BS32 as a plain tableau, model, n_states)
    estimate_cycles   = count_sequence(reference_sections()["estimate"], model) * n_states
    controller_cycles = count_sequence(compare + bit_scan + table_load + step_update, model)
    per_attempt_total = stage_cycles + estimate_cycles + controller_cycles
    cycles               = attempts   * per_attempt_total    <- the published number
    cycles_accepted_only = n_accepted * per_attempt_total    <- the published lower bound

``cycle_count`` is used strictly inside its own contract: an embedded pair's A is
strictly lower triangular, so the propagating formula is an ordinary explicit
tableau and the pinned function prices it exactly as it prices any other. The
estimate section is per state and ``adaptive_q15.sequence_costs`` sums the sections
flat, which under-books a multi-state problem; the scaling by ``n_states`` is
applied here and only here.

Four things that number does NOT include, each carried in the document as a field
rather than left in a comment (see the ``cost_bases`` block):

1. Branches. ``costmodel`` prices eight mnemonics and none of them is a branch, so
   the bit scan's loop and the acceptance test are unpriced. The scan is booked at
   its worst case, so the figure is an UPPER BOUND, and
   ``adaptive_q15.BRANCH_ALLOWANCE["conditional_branches"]`` rides on every
   adaptive row as ``branch_allowance``.
2. Derivative evaluations. The pinned model excludes them for every class, so FSAL
   is worth exactly zero cycles here even though it saves one rhs call per accepted
   step. The saving is reported beside the cycle number as ``fevals_saved_by_fsal``
   and ``n_fevals``, where it is visible without being folded into a number the
   model cannot support.
3. The Q31 time register. ``adaptive_q15.TimeQ31`` names two terms the cost model
   does not price: an int32 add per accepted step and a unit conversion.
4. Rejected attempts are booked at FULL price, which is a choice and not an
   oversight: the stage work is already spent when the estimate is formed, and both
   the estimate and the controller run before the accept decision is known.

Three grades of number therefore sit in one document and every row says which it
is: ``assembly_verified`` for explicit (the pinned model, cross-checked against
fixtures/known_sequence.s), ``design_estimate_div32`` for implicit (the
EPOCH3-DESIGN solver terms, resting on ``sdirk.DIV_CYCLES = 32``, a stated guess
for a software reciprocal that the cost model does not price at all),
``upper_bound_unpriced_branches`` for the Q15 adaptive run, and
``unpriced_float64`` for the float twin, which is on the shared ERROR axis and off
the cost axis entirely.

PLACING ADAPTIVE ON THE SHARED AXIS. The Q15 tolerance is ``tol_q``, an integer in
LSB of the scaled state; the suite's metric is L2 final-state error over PEAK.
There is no analytic conversion through the scale, PEAK and the floor bias of the
estimate, so none is attempted. Adaptive is placed by ACHIEVED ERROR: for a target
tau the adaptive cost is the cycles of the COARSEST tol_q rung whose achieved
error is at or under tau. That is secondpass's own rule, "the smallest n on the
sampled set", read on a tolerance ladder instead of a step ladder, and because
achieved error is measured with ``validation.validation_error`` for every class,
the three classes read in the same units. The ladder is dyadic in whole LSB with
nothing between adjacent rungs, so no bisection is taken and ``probes`` is 0
rather than a fiction.

TWO ENCODING ARTIFACTS THAT ARE NOT PROPERTIES OF ADAPTIVITY, both already
measured by the solver and both carried as a status rather than as a silent miss:

* ``adaptive_q15._initial_h_q`` notes that h_q cannot reach 1.0 in a problem's own
  time units, so on robertson_scaled (t_end 200) and servo_load_step (t_end 260)
  the step saturates at the Q15 ceiling and the run needs at least t_end steps
  whatever the tolerance. Those are two of the three stiff problems, which is
  exactly where the implicit class is supposed to be measured against adaptive.
  Status ``h_q_ceiling`` with ``h_q_clamped_high`` says so.
* the estimate sits on a measured floor bias of roughly 2 LSB, so a target under
  that floor is not something the estimate can control to. Status
  ``below_bias_floor`` carries the measured ``mean_bias_lsb_total`` from the
  ``adaptive.q15_estimate_floor`` side-track artifacts when they are on disk, and
  from this run's own ladder when they are not; ``estimate_floor[problem].source``
  says which.

DETERMINISM. Every iteration is over sorted names and the fixed ``CLASS_ORDER``
tuple. Nothing is sampled. Every work cap is a deterministic count (probes,
``n_max``, ``max_attempts``, a divergence bound on the data) and never a clock,
following ``sidetrack.SWEEP_MAX_ATTEMPTS``. The document reads the clock exactly
once, for ``generated_from.generated_ts``, and only when the caller asks for a
stamp; ``build_axes`` with no stamp is a pure function of (code, params, inputs)
and two builds are byte-identical.

NOT A PUBLIC-PAGE SOURCE YET. The traceability rule lists key_findings.json,
validation/results.json, benchmark/results.json and the side-track ledger with its
artifacts. ``rk-work/validation/axes.json`` is not on that list, and putting it
there is a decision for the owner rather than a side effect of this module.

COST OF A FULL RUN. This is the heaviest host job in the tree, and it belongs on
the host on demand like validation.py and benchmark.py (docs/DEVELOPMENT.md).
Explicit costs one ladder to n_max plus up to ``bisect_probes`` probes per target
per (method, problem), which is what ``secondpass.work_precision`` already costs,
over eight problems instead of seven. Implicit costs the same ladder shape twice
per problem with an SDIRK step, a Jacobian and an LU in place of an RK step.
Adaptive costs eight problems times ten rungs times two arithmetics under a 50,000
attempt cap each. Budget tens of minutes rather than seconds, and pass --classes
when only one axis moved. Every ladder is probed through a per (method, problem)
cache, so one ladder serves all four targets.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import math
import sys
from pathlib import Path

from rk_harness import sidetrack
from rk_harness import validation as V
from rk_harness.costmodel import M0PLUS_FAST, count_sequence, cycle_count
from rk_harness.fixedpoint import Q15OverflowError
from rk_harness.paths import work_dir
from rk_harness.problems import to_physical
from rk_harness.prototypes import adaptive as A
from rk_harness.prototypes import adaptive_q15 as AQ
from rk_harness.prototypes import sdirk as SD
from rk_harness.secondpass import (
    BISECT_PROBES,
    N_MAX,
    TARGETS,
    Floats,
    _check_prose,
    _target_key,
    ladder_scan,
)
from rk_harness.simulate import solve_q15
from rk_harness.tableau import classical, to_json
from rk_harness.types import Tableau
from rk_harness.verifier_hash import compute_verifier_hash, pinned_verifier_hash

# Explicit leads, then implicit, then adaptive. Every iteration over classes uses
# this tuple rather than a set or a dict order, so the document is deterministic
# and reads in the order the sites are meant to present it.
CLASS_ORDER: tuple[str, ...] = ("explicit", "implicit", "adaptive")

BUDGET_CYCLES = V.BUDGET_CYCLES
COST_MODEL = M0PLUS_FAST
ROUNDING = "floor (ASRS), per HANDOFF 4.2"

TOL_LADDER: tuple[int, ...] = AQ.TOL_LADDER_LSB
MAX_ATTEMPTS: int = AQ.Q15_MAX_ATTEMPTS
NEWTON_ITERS: int = SD.NEWTON_ITERS

def descending_ladder(tol_ladder) -> tuple[int, ...]:
    """A tolerance ladder in ASCENDING COST order, which is descending tolerance.

    ladder_scan answers with the EARLIEST rung that meets the target, so the earliest
    rung has to be the cheapest one. On a step ladder that reads as the smallest n;
    on a tolerance ladder it reads as the coarsest tolerance, which is the same rule.
    """
    return tuple(sorted((int(v) for v in tol_ladder), reverse=True))


TOL_LADDER_DESCENDING: tuple[int, ...] = descending_ladder(TOL_LADDER)

CONTROL_KINDS: tuple[str, ...] = ("steps", "tol_q")
METHOD_KINDS: tuple[str, ...] = ("classical", "discovered", "prototype", "prototype_control")
SOURCES: tuple[str, ...] = ("fixture", "archive", "prototype")
ARITHMETICS: tuple[str, ...] = ("q15", "float64")

COST_BASES: tuple[str, ...] = (
    "assembly_verified",
    "design_estimate_div32",
    "upper_bound_unpriced_branches",
    "unpriced_float64",
)

TARGET_STATUSES: tuple[str, ...] = (
    "reached",
    "never_reached",
    "overflow_before_target",
    "attempt_cap",
    "step_underflow",
    "h_q_ceiling",
    "below_bias_floor",
    "newton_nonconvergent",
)

# The union of the per-class counter keys. Every row carries all of them, with
# null where the class has no such counter, so a consumer never has to know which
# class it is reading before it can index the dict.
COUNTER_KEYS: tuple[str, ...] = (
    "n_accepted", "n_rejected", "attempts", "n_fevals", "fevals_saved_by_fsal",
    "overflow_rejected", "h_q_initial", "h_q_final", "h_q_clamped_high",
    "max_abs_state", "steps", "newton_iters", "njev", "nlu", "jacobian",
)

# Method names owned by this module. Disjoint from the classical fixture names and
# from any tableau hash, so name_or_hash stays a key.
M_SDIRK_ANALYTIC = "sdirk2_analytic_jac"
M_SDIRK_FD = "sdirk2_fd_jac"
M_BS32_Q15 = "bs32_q15_adaptive"
M_BS32_FLOAT = "bs32_float_adaptive"
M_BS32_FIXED = "bs32_propagating_b"

PROTOTYPE_METHODS: tuple[str, ...] = (
    M_SDIRK_ANALYTIC, M_SDIRK_FD, M_BS32_Q15, M_BS32_FLOAT, M_BS32_FIXED,
)

# The BS32 propagating formula as a plain explicit tableau. Its A is strictly
# lower triangular, which is what lets cycle_count price it inside its contract.
BS32_TABLEAU = Tableau(A=AQ.BS32_A, b=AQ.BS32_B, c=AQ.BS32_C)

Q15_FULL_SCALE = 32768.0


def diverge_bound(name: str) -> float:
    """The divergence bound for an implicit run on `name`.

    A bound on the DATA, not on the clock, so a run's outcome stays a pure
    function of its inputs. Same rule stiffscreen.py uses: a thousand times the
    problem's own peak, floored at 1.0.
    """
    return 1.0e3 * max(V.PEAK[name], 1.0)


def lsb_physical(name: str) -> float:
    """One LSB of the scaled state, in the problem's physical units."""
    return 1.0 / (Q15_FULL_SCALE * V.SCALE[name])


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _median(vals: list[float]) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else 0.5 * (s[mid - 1] + s[mid])


def _counters(**kw) -> dict:
    """A counters block with every key present and null where it does not apply."""
    out: dict = {k: None for k in COUNTER_KEYS}
    for k, v in kw.items():
        if k not in out:
            raise KeyError(f"unknown counter {k!r}")
        out[k] = v
    return out


# --------------------------------------------------------------------------- cost

def adaptive_attempt_cost(n_states: int, model=COST_MODEL) -> dict:
    """Cycles one adaptive ATTEMPT costs, accepted or rejected.

    The three terms and what each is: the stage work of the pair read through the
    pinned cycle_count; the four-term error estimate, which is per state and so is
    scaled by n_states here (adaptive_q15.sequence_costs sums the sections flat and
    under-books a multi-state problem); and the controller, which is one compare,
    one worst-case bit scan, one table load and one step update, all independent of
    the state count.
    """
    sec = AQ.reference_sections()
    stage = cycle_count(BS32_TABLEAU, model, n_states)
    estimate = count_sequence(sec["estimate"], model) * int(n_states)
    controller = count_sequence(
        sec["compare"] + sec["bit_scan"] + sec["table_load"] + sec["step_update"], model)
    return {
        "stage": stage,
        "estimate": estimate,
        "controller": controller,
        "total": stage + estimate + controller,
        "branch_allowance": AQ.BRANCH_ALLOWANCE["conditional_branches"],
    }


def sdirk_step_cost(n_states: int, fd: bool, newton_iters: int = NEWTON_ITERS,
                    model=COST_MODEL) -> dict:
    """Cycles one SDIRK2 step costs, with its term breakdown carried verbatim."""
    return SD.estimate_sdirk2_cycles(int(n_states), model, newton_iters=newton_iters, fd=fd)


COST_BASIS_DOC: dict[str, dict] = {
    "assembly_verified": {
        "grade": "the pinned analytic model, cross-checked line for line against "
                 "fixtures/known_sequence.s",
        "direction": "exact within the model",
        "includes": "every combination row of A and the combination over b, priced "
                    "per state under the cost model",
        "excludes": "derivative evaluations, which the pinned model excludes for "
                    "every class, and every branch",
        "source": "costmodel.cycle_count",
    },
    "design_estimate_div32": {
        "grade": "a design estimate from the EPOCH3-DESIGN solver terms, not an "
                 "assembly-verified count",
        "direction": "estimate, unsigned",
        "includes": "forming M = I - h*gamma*J, one LU factorization per step, the "
                    "stage combination, the fixed Newton iterations with their back "
                    "substitutions, the combination over b, and the finite difference "
                    "assembly arithmetic when the Jacobian is not analytic",
        "excludes": "derivative and analytic-Jacobian evaluations, and every branch. "
                    "The LU rests on sdirk.DIV_CYCLES = 32, a stated guess for a "
                    "software reciprocal, because the cost model prices no divide",
        "source": "sdirk.estimate_sdirk2_cycles",
    },
    "upper_bound_unpriced_branches": {
        "grade": "an upper bound assembled from the pinned model plus a priced "
                 "reference listing",
        "direction": "upper bound within the model",
        "includes": "the stage work of the propagating formula, the four-term error "
                    "estimate scaled by the state count, and one controller update, "
                    "charged on every attempt whether it was accepted or rejected",
        "excludes": "conditional branches, which costmodel prices no class for, so the "
                    "bit scan is booked at its worst case and branch_allowance rides "
                    "beside the number; derivative evaluations, so the FSAL saving "
                    "shows up as fevals_saved_by_fsal and not as cycles; and the two "
                    "Q31 time register terms adaptive_q15.TimeQ31 names",
        "source": "costmodel.cycle_count plus costmodel.count_sequence over "
                  "adaptive_q15.reference_sections",
    },
    "unpriced_float64": {
        "grade": "no cycle number at all",
        "direction": "absent",
        "includes": "nothing. The float twin runs the same pair and the same "
                    "controller in float64, so it stands on the shared error axis and "
                    "says how much of the Q15 run error is quantization",
        "excludes": "the whole cost axis. The cost model prices Q15 integer arithmetic "
                    "on an M0+, and a float64 run is not that machine",
        "source": "not priced",
    },
}


# --------------------------------------------------------------------------- probes

def _explicit_probe(t: Tableau, name: str, n: int,
                    cache: dict[int, tuple]) -> tuple:
    """One fixed-step Q15 rung on a VALIDATION problem.

    This is secondpass._probe with one substitution, and the substitution is
    forced. secondpass runs on the seven frozen scored problems, so it scores with
    ``simulate.problem_error``, which calls the pinned ``problems.error_metric``,
    which indexes the FROZEN ``problems.PROBLEMS`` by name. The validation names are
    deliberately disjoint from those, so ``problem_error`` raises KeyError on every
    one of them. Scoring with ``validation.validation_error`` instead is not a
    weaker path: it is the same L2-over-PEAK formula, it is what validation.py and
    the whole of this document already use, and using it here is what puts all three
    classes on ONE error metric. The integrator is still the pinned
    ``simulate.solve_q15`` at the same step counts.
    """
    hit = cache.get(n)
    if hit is not None:
        return hit
    p = V.PROBLEMS[name]
    try:
        final_q, max_q = solve_q15(t, p, n)
        err = V.validation_error(name, to_physical(final_q, p.scale))
        row = ("ok", float(err), int(max_q)) if math.isfinite(err)             else ("nonfinite", None, int(max_q))
    except Q15OverflowError:
        row = ("overflow", None, None)
    except Exception:                       # a broken tableau must not stop the sweep
        row = ("error", None, None)
    cache[n] = row
    return row


def _explicit_scan(t: Tableau, name: str, targets, n_max: int,
                   bisect_probes: int) -> dict:
    """The shared power-of-two ladder from secondpass, over the fixed-step Q15 probe
    above. per_unit_cost is the pinned cycle_count, so cycles is n * cycles_per_step
    exactly as it is for a scored method."""
    cache: dict[int, tuple] = {}
    return ladder_scan(lambda n: _explicit_probe(t, name, n, cache), targets,
                       n_max=n_max, bisect_probes=bisect_probes,
                       per_unit_cost=cycle_count(t, COST_MODEL,
                                                 V.PROBLEMS[name].n_states))


def _implicit_probe(name: str, n: int, jac, spec, bound: float,
                    cache: dict[int, tuple]) -> tuple:
    """One SDIRK2 rung: n fixed steps, scored with the suite's own error metric.

    Failure is a status and never an exception. The divergence bound raises
    OverflowError, which is the implicit analogue of a Q15 overflow; a non-finite
    result is the fixed-count modified Newton iteration failing to produce a number.
    """
    hit = cache.get(n)
    if hit is not None:
        return hit
    if n <= 0:
        row = ("error", None, None)
        cache[n] = row
        return row
    try:
        y = SD.solve_sdirk2(V.FLOAT_RHS[name], V.Y0_PHYS[name], V.PROBLEMS[name].t_end,
                            n, jac=jac, spec=spec, diverge_at=bound)
        err = V.validation_error(name, y)
        row = ("ok", float(err), None) if math.isfinite(err) else ("nonfinite", None, None)
    except OverflowError:
        row = ("overflow", None, None)
    except (ValueError, ZeroDivisionError, ArithmeticError):
        row = ("nonfinite", None, None)
    except Exception:                       # a broken problem must not stop the sweep
        row = ("error", None, None)
    cache[n] = row
    return row


def _adaptive_probe(name: str, tol_q: int, arithmetic: str, max_attempts: int,
                    cache: dict[int, dict]) -> tuple:
    """One adaptive rung. The full solver result is kept in the caller's cache, keyed
    by the same control value, because the cycle number is attempts times the
    per-attempt cost and the attempt count is not the tolerance."""
    hit = cache.get(tol_q)
    if hit is None:
        scale = V.SCALE[name]
        if arithmetic == "float64":
            # The same rung asked for in the same units: tol_q LSB of the scaled
            # state, expressed in the problem's own physical units.
            tol: float | int = float(tol_q) * lsb_physical(name)
            mode = "float"
        else:
            tol = int(tol_q)
            mode = "q15"
        res = AQ.solve_adaptive_q15(name, scale, tol, arithmetic=mode,
                                    max_attempts=max_attempts)
        res.pop("floor_stats", None)        # a FloorStats object, not JSON
        res.pop("time_register", None)
        hit = res
        cache[tol_q] = hit
    err = hit.get("achieved_error")
    status = hit.get("status")
    if status == "ok" and err is not None and math.isfinite(err):
        return ("ok", float(err), None)
    if status in ("overflow", "register_overflow"):
        return ("overflow", None, None)
    return ("error", None, None)


def _adaptive_target_status(base: str, rungs: list[dict], target: float,
                            floor: float | None) -> str:
    """Refine ladder_scan's miss into the reason the adaptive run missed.

    The order is the order in which one explanation supersedes another: a target
    under the estimate's own bias floor explains the miss whatever the solver did;
    a ladder that failed the same way on every rung is named by that failure; a
    ladder that ran and hit the Q15 step ceiling on every rung was bounded by the
    encoding rather than by the controller.
    """
    if base == "reached":
        return "reached"
    if floor is not None and target < floor:
        return "below_bias_floor"
    statuses = {r.get("status") for r in rungs}
    if statuses and statuses <= {"attempt_cap"}:
        return "attempt_cap"
    if statuses and statuses <= {"step_underflow", "underflow"}:
        return "step_underflow"
    if statuses and statuses <= {"overflow", "register_overflow"}:
        return "overflow_before_target"
    if rungs and any(r.get("status") == "ok" for r in rungs)             and all((r.get("h_q_clamped_high") or 0) > 0 for r in rungs):
        # Every sampled rung saturated the Q15 step ceiling, and at least one of them
        # still produced a number, so the step size was bounded by the encoding
        # rather than by the controller. h_q cannot reach 1.0 in a problem's own time
        # units, so on a long window the run needs at least t_end steps whatever the
        # tolerance. The status names a constraint the run was under; it does not
        # claim the ceiling was the only reason for the miss, which is what
        # best_achieved_error and counters.h_q_clamped_high let a reader judge.
        return "h_q_ceiling"
    return base


def _implicit_target_status(base: str, ladder: list[dict]) -> str:
    """A ladder with no clean rung whose every failure was non-finite is the fixed
    count modified Newton iteration failing to produce a number, which is a
    different fact from a run that diverged past the bound."""
    if base == "reached":
        return "reached"
    if any(row["status"] == "ok" for row in ladder):
        return base
    bad = {row["status"] for row in ladder if row["status"] != "ok"}
    if bad and bad <= {"nonfinite"}:
        return "newton_nonconvergent"
    return base


# --------------------------------------------------------------------------- methods

def _explicit_methods(anchors: tuple[str, ...], records, champ) -> list[tuple[dict, Tableau]]:
    """The classical anchors, plus the archive elites validation.select_discovered
    picks, as (method entry, tableau) pairs. Selection and roles are validation's,
    not this module's, so the two documents name the same methods."""
    cls_tabs = classical()
    out: list[tuple[dict, Tableau]] = []
    for name in anchors:
        t = cls_tabs[name]
        out.append((_base_method(name, "explicit", "classical", ["anchor"], "fixture",
                                 "assembly_verified", "steps", t), t))
    if records is not None and champ is not None:
        for rec, roles in V.select_discovered(list(records), champ):
            entry = _base_method(rec.tableau_hash, "explicit", "discovered", list(roles),
                                 "archive", "assembly_verified", "steps", rec.tableau)
            entry["archive"] = {
                "cycle_id": rec.cycle_id,
                "tier": rec.tier,
                "heldout_error": _finite(rec.score.heldout_error),
                "search_error": _finite(rec.score.search_error),
                "verifier_hash": rec.verifier_hash,
            }
            out.append((entry, rec.tableau))
    return out


def _finite(v):
    return v if isinstance(v, (int, float)) and math.isfinite(v) else None


def _base_method(name: str, cls: str, kind: str, roles: list[str], source: str,
                 cost_basis: str, control: str, t: Tableau | None) -> dict:
    entry: dict = {
        "name_or_hash": name,
        "class": cls,
        "kind": kind,
        "roles": roles,
        "source": source,
        "cost_basis": cost_basis,
        "control": control,
        "arithmetic": "q15",
        "derived_fixed_step": False,
        "tableau": to_json(t) if t is not None else None,
    }
    if t is not None:
        entry["order"] = V.method_order(t)
        entry["stages"] = len(t.b)
        entry["cycles_per_step"] = {
            name_: cycle_count(t, COST_MODEL, V.PROBLEMS[name_].n_states)
            for name_ in sorted(V.VALIDATION_NAMES)
        }
        entry["steps"] = {
            name_: steps_for_budget_local(t, V.PROBLEMS[name_].n_states)
            for name_ in sorted(V.VALIDATION_NAMES)
        }
    return entry


def steps_for_budget_local(t: Tableau, n_states: int) -> int:
    """budget_cycles // cycle_count, the same mapping validation and the scored
    evaluator use. Named here rather than imported so the implicit path, whose cost
    does not come from a tableau at all, can use the same arithmetic."""
    cost = cycle_count(t, COST_MODEL, n_states)
    return BUDGET_CYCLES // cost if cost > 0 else 0


def _implicit_methods(newton_iters: int) -> list[dict]:
    """The two SDIRK2 variants. The analytic variant falls back to the finite
    difference on the five problems with no hand-derived Jacobian, and says so per
    problem rather than pretending it had one."""
    out: list[dict] = []
    for name, policy in ((M_SDIRK_ANALYTIC, "analytic"), (M_SDIRK_FD, "finite_difference")):
        jac_by_problem = {}
        cps: dict[str, int] = {}
        terms: dict[str, dict] = {}
        steps: dict[str, int] = {}
        for pname in sorted(V.VALIDATION_NAMES):
            use_analytic = policy == "analytic" and V.ANALYTIC_JACOBIAN[pname] is not None
            jac_by_problem[pname] = "analytic" if use_analytic else "finite_difference"
            est = sdirk_step_cost(V.PROBLEMS[pname].n_states, fd=not use_analytic,
                                  newton_iters=newton_iters)
            cps[pname] = int(est["total"])
            terms[pname] = dict(est["terms"])
            terms[pname]["f_evals_per_step"] = int(est["f_evals_per_step"])
            steps[pname] = BUDGET_CYCLES // cps[pname] if cps[pname] > 0 else 0
        entry = {
            "name_or_hash": name,
            "class": "implicit",
            "kind": "prototype",
            "roles": ["sdirk2_" + policy],
            "source": "prototype",
            "cost_basis": "design_estimate_div32",
            "control": "steps",
            "arithmetic": "float64",
            "derived_fixed_step": False,
            "tableau": None,
            "order": 2,
            "stages": SD.STAGES,
            "gamma": float(SD.GAMMA),
            "a21": float(1.0 - SD.GAMMA),
            "newton_iters": int(newton_iters),
            "jacobian": policy,
            "jacobian_by_problem": jac_by_problem,
            "cycles_per_step": cps,
            "cycles_per_step_terms": terms,
            "steps": steps,
            "div_cycles": int(SD.DIV_CYCLES),
            "work_cap": {"n_max": None, "bisect_probes": None, "max_attempts": None,
                         "diverge_at": {p: diverge_bound(p) for p in sorted(V.VALIDATION_NAMES)}},
            "note": ("float64 arithmetic throughout. fixedpoint.py has no reciprocal, "
                     "so there is no Q15 LU and no Q15 SDIRK run to report"),
        }
        out.append(entry)
    return out


def _pair_block() -> dict:
    return {
        "b": [str(x) for x in AQ.BS32_B],
        "b_hat": [str(x) for x in AQ.BS32_B_HAT],
        "order_hat": 2,
        "order": 3,
        "fsal": True,
        "name": "bogacki_shampine_32",
    }


def _controller_block() -> dict:
    """The controller as data. alpha and beta are the float prototype's dyadic PI
    gains; the Q15 controller applies them as nu = e_prev - 2*e in eighths of a
    power of two, which is the same controller in an integer encoding."""
    return {
        "alpha": float(A.ALPHA),
        "beta": float(A.BETA),
        "safety": float(AQ.SAFETY),
        "clamp_lo": float(AQ.FAC_MIN),
        "clamp_hi": float(AQ.FAC_MAX),
        "table_bits": int(AQ.FACTOR_Q_BITS),
        "table": AQ.FTAB33.name,
        "nu_min": int(AQ.FTAB33.nu_min),
        "nu_max": int(AQ.FTAB33.nu_max),
        "step_eighths": int(AQ.FTAB33.step_eighths),
        "note": ("the factor table is held in Q14 rather than Q15 because a Q15 table "
                 "cannot represent a factor of 1.0 at all; the encoding is an open "
                 "item in EPOCH2-DESIGN section 4"),
    }


def _adaptive_methods(arithmetics: tuple[str, ...], tol_ladder: tuple[int, ...],
                      max_attempts: int) -> list[dict]:
    out: list[dict] = []
    for arith in arithmetics:
        name = M_BS32_Q15 if arith == "q15" else M_BS32_FLOAT
        basis = "upper_bound_unpriced_branches" if arith == "q15" else "unpriced_float64"
        entry: dict = {
            "name_or_hash": name,
            "class": "adaptive",
            "kind": "prototype",
            "roles": ["embedded_pair"] + ([] if arith == "q15" else ["arithmetic_control"]),
            "source": "prototype",
            "cost_basis": basis,
            "control": "tol_q",
            "arithmetic": arith,
            "derived_fixed_step": False,
            "tableau": to_json(BS32_TABLEAU),
            "order": 3,
            "order_hat": 2,
            "stages": 4,
            "pair": _pair_block(),
            "controller": _controller_block(),
            "tolerance_ladder_lsb": list(tol_ladder),
            "work_cap": {"max_attempts": int(max_attempts), "n_max": None,
                         "bisect_probes": None, "diverge_at": None},
        }
        if arith == "q15":
            entry["cycles_per_attempt"] = {
                p: adaptive_attempt_cost(V.PROBLEMS[p].n_states)
                for p in sorted(V.VALIDATION_NAMES)
            }
            entry["branch_allowance"] = int(AQ.BRANCH_ALLOWANCE["conditional_branches"])
            entry["note"] = AQ.BRANCH_ALLOWANCE["consequence"]
        else:
            entry["cycles_per_attempt"] = None
            entry["branch_allowance"] = None
            entry["tolerance_map"] = (
                "a rung of tol_q LSB is asked of the float twin as "
                "tol_q / (32768 * scale) in the problem's own physical units, so the "
                "two arithmetics are asked for the same thing on the same rung")
            entry["note"] = ("no cycle number: the cost model prices Q15 integer "
                             "arithmetic on an M0+, and this run is float64")
        out.append(entry)
    return out


def _control_method() -> dict:
    """The propagating b vector of the pair run as a plain fixed-step method.

    It is a CONTROL and not an adaptive method: it says what the same tableau costs
    with the controller taken away. The verdicts never count it as an adaptive
    method, which is what derived_fixed_step marks.
    """
    entry = _base_method(M_BS32_FIXED, "adaptive", "prototype_control",
                         ["pair_propagating_formula"], "prototype",
                         "assembly_verified", "steps", BS32_TABLEAU)
    entry["derived_fixed_step"] = True
    entry["pair"] = _pair_block()
    entry["cycles_per_attempt"] = None      # a fixed-step run has no attempts
    entry["note"] = ("the pair's propagating formula with no error estimate and no "
                     "controller, so the difference against bs32_q15_adaptive on the "
                     "same rung is what the step control costs and buys")
    return entry


# --------------------------------------------------------------------------- axis F

def _sdirk_spec(newton_iters: int):
    return dataclasses.replace(SD.DEFAULT_SPEC, newton_iters=int(newton_iters))


def implicit_fixed_budget_row(entry: dict, pname: str, newton_iters: int,
                              fl: Floats) -> dict:
    """One implicit axis-F cell: the step count the SDIRK per-step estimate buys at
    BUDGET_CYCLES, and the error at that step count."""
    n = int(entry["steps"][pname])
    row = {
        "method": entry["name_or_hash"],
        "class": "implicit",
        "problem": pname,
        "steps": n,
        "cycles_per_step": int(entry["cycles_per_step"][pname]),
        "arithmetic": "float64",
        "q15_error": None,
        "float_error": None,
        "max_abs_q": None,
        "derived_fixed_step": False,
        "note": ("float64 arithmetic: there is no Q15 LU, so this row has no "
                 "q15_error and contributes nothing to the matched point"),
    }
    if n <= 0:
        row["note"] = "method too expensive for the budget"
        return row
    use_analytic = entry["jacobian_by_problem"][pname] == "analytic"
    jac = V.ANALYTIC_JACOBIAN[pname] if use_analytic else None
    cache: dict[int, tuple] = {}
    status, err, _ = _implicit_probe(pname, n, jac, _sdirk_spec(newton_iters),
                                     diverge_bound(pname), cache)
    if status == "ok":
        row["float_error"] = fl(err)
    else:
        row["note"] = row["note"] + f"; run status {status}"
    return row


def control_fixed_budget_row(pname: str, fl: Floats) -> dict:
    """The adaptive control row: the propagating formula as a plain fixed-step Q15
    method, through validation.evaluate_pair unchanged."""
    cell = V.evaluate_pair(BS32_TABLEAU, pname)
    row = {
        "method": M_BS32_FIXED,
        "class": "adaptive",
        "problem": pname,
        "steps": int(cell["steps"]),
        "cycles_per_step": int(cell["cycles_per_step"]),
        "arithmetic": "q15",
        "q15_error": fl(cell["q15_error"]),
        "float_error": fl(cell["float_error"]),
        "max_abs_q": cell["max_abs_q"],
        "derived_fixed_step": True,
        "note": cell.get("note", "a control, not an adaptive method"),
    }
    return row


def load_reference(path: Path | str | None = None) -> dict | None:
    """The v1 validation document, read only, for its explicit axis-F rows.

    Explicit axis-F rows are not duplicated here: results.json already publishes
    them and its numbers are the ones on the site. Absent, the matched point falls
    back to the axis-F rows this document builds and says so.
    """
    p = Path(path) if path is not None else work_dir() / "validation" / "results.json"
    if not p.is_file():
        return None
    try:
        with open(p, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def reference_rows(doc: dict | None, fl: Floats) -> list[dict]:
    """The referenced explicit axis-F rows, in this document's row shape."""
    if not doc:
        return []
    kinds = {m.get("name_or_hash"): m.get("kind") for m in doc.get("methods", [])}
    out: list[dict] = []
    for r in doc.get("results", []):
        name = r.get("method")
        if kinds.get(name) not in ("classical", "discovered"):
            continue
        out.append({
            "method": name,
            "class": "explicit",
            "problem": r.get("problem"),
            "steps": r.get("steps"),
            "cycles_per_step": r.get("cycles_per_step"),
            "arithmetic": "q15",
            "q15_error": fl(r.get("q15_error")),
            "float_error": fl(r.get("float_error")),
            "max_abs_q": r.get("max_abs_q"),
            "derived_fixed_step": False,
            "note": r.get("note", "referenced from validation/results.json"),
        })
    return out


def _finished(row: dict) -> bool:
    key = "q15_error" if row.get("arithmetic") == "q15" else "float_error"
    return row.get(key) is not None


# --------------------------------------------------------------------------- axis T

def _best_error(rungs: list[dict], probed_lists=()) -> float | None:
    """The lowest error anywhere on the sampled ladder.

    A row that missed its target carries a null achieved_error, because there is no
    answering rung. That leaves a reader unable to tell a method that missed by a
    factor of two from one that never ran, so the best error on the ladder rides
    beside it. It is a property of the ladder, not of the target.
    """
    vals = [r["error"] for r in rungs
            if r.get("status") == "ok" and r.get("error") is not None]
    for probed in probed_lists:
        vals += [r["error"] for r in probed
                 if r.get("status") == "ok" and r.get("error") is not None]
    return min(vals) if vals else None


def _error_at(ladder: list[dict], probed: list[dict], n) -> float | None:
    for row in ladder:
        if row["n"] == n:
            return row["error"]
    for row in probed:
        if row["n"] == n:
            return row["error"]
    return None


def _row(method: str, cls: str, pname: str, target: float, control_kind: str,
         cost_basis: str, **kw) -> dict:
    row = {
        "method": method,
        "class": cls,
        "problem": pname,
        "target": float(target),
        "target_key": _target_key(target),
        "control_kind": control_kind,
        "control_value": None,
        "cycles": None,
        "cycles_accepted_only": None,
        "status": "never_reached",
        "achieved_error": None,
        "best_achieved_error": None,
        "ladder_monotone": True,
        "probes": 0,
        "cost_basis": cost_basis,
        "counters": None,
        "counters_from": None,
        "derived_fixed_step": False,
        "note": "",
    }
    row.update(kw)
    return row


_FIXED_STEP_NOTE = "the smallest step count on the sampled ladder"
_ADAPTIVE_NOTE = "the coarsest rung on the sampled ladder"


def explicit_tolerance_rows(entry: dict, t: Tableau, pname: str, targets,
                            n_max: int, bisect_probes: int, fl: Floats) -> list[dict]:
    scan = _explicit_scan(t, pname, targets, n_max, bisect_probes)
    best = _best_error(scan["ladder"],
                       [scan["targets"][_target_key(t_)]["probed"] for t_ in targets])
    rows: list[dict] = []
    for target in targets:
        e = scan["targets"][_target_key(target)]
        n = e["n"]
        err = _error_at(scan["ladder"], e["probed"], n) if n is not None else None
        rows.append(_row(
            entry["name_or_hash"], "explicit", pname, target, "steps",
            entry["cost_basis"],
            control_value=n,
            cycles=e["cycles"],
            cycles_accepted_only=e["cycles"],
            status=e["status"],
            achieved_error=fl(err),
            best_achieved_error=fl(best),
            ladder_monotone=bool(e["ladder_monotone"]),
            probes=int(e["probes"]),
            counters=_counters(steps=n) if n is not None else _counters(),
            counters_from=n,
            note=_FIXED_STEP_NOTE,
        ))
    return rows


def implicit_tolerance_rows(entry: dict, pname: str, targets, n_max: int,
                            bisect_probes: int, newton_iters: int,
                            fl: Floats) -> list[dict]:
    use_analytic = entry["jacobian_by_problem"][pname] == "analytic"
    jac = V.ANALYTIC_JACOBIAN[pname] if use_analytic else None
    spec = _sdirk_spec(newton_iters)
    bound = diverge_bound(pname)
    cache: dict[int, tuple] = {}
    per_step = int(entry["cycles_per_step"][pname])
    scan = ladder_scan(lambda n: _implicit_probe(pname, n, jac, spec, bound, cache),
                       targets, n_max=n_max, bisect_probes=bisect_probes,
                       per_unit_cost=per_step)
    best = _best_error(scan["ladder"],
                       [scan["targets"][_target_key(t_)]["probed"] for t_ in targets])
    rows: list[dict] = []
    for target in targets:
        e = scan["targets"][_target_key(target)]
        n = e["n"]
        err = _error_at(scan["ladder"], e["probed"], n) if n is not None else None
        rows.append(_row(
            entry["name_or_hash"], "implicit", pname, target, "steps",
            entry["cost_basis"],
            control_value=n,
            cycles=e["cycles"],
            cycles_accepted_only=e["cycles"],
            status=_implicit_target_status(e["status"], scan["ladder"]),
            achieved_error=fl(err),
            best_achieved_error=fl(best),
            ladder_monotone=bool(e["ladder_monotone"]),
            probes=int(e["probes"]),
            counters=_counters(steps=n, newton_iters=int(newton_iters),
                               njev=n, nlu=n,
                               jacobian=entry["jacobian_by_problem"][pname]),
            counters_from=n,
            note=_FIXED_STEP_NOTE,
        ))
    return rows


def adaptive_tolerance_rows(entry: dict, pname: str, targets,
                            tol_ladder: tuple[int, ...], max_attempts: int,
                            floor: float | None, fl: Floats) -> tuple[list[dict], dict]:
    """Rows plus the per-rung solver cache, which the estimate floor reads back."""
    arith = entry["arithmetic"]
    cache: dict[int, dict] = {}
    ladder = descending_ladder(tol_ladder)      # coarsest, so cheapest, leading
    scan = ladder_scan(lambda q: _adaptive_probe(pname, q, arith, max_attempts, cache),
                       targets, unit_ladder=ladder, bisect=False, per_unit_cost=None)
    per_attempt = None
    if entry["cycles_per_attempt"] is not None:
        per_attempt = int(entry["cycles_per_attempt"][pname]["total"])
    rungs = [cache[q] for q in ladder if q in cache]
    ran = [q for q in ladder if q in cache and cache[q].get("achieved_error") is not None
           and cache[q].get("status") == "ok"]
    fallback = (min(ran, key=lambda q: (cache[q]["achieved_error"], q)) if ran
                else (ladder[-1] if ladder and ladder[-1] in cache else None))
    best = _best_error([{"status": ("ok" if r.get("status") == "ok" else "bad"),
                         "error": r.get("achieved_error")} for r in rungs])
    rows: list[dict] = []
    for target in targets:
        e = scan["targets"][_target_key(target)]
        tol = e["n"]
        res = cache.get(tol) if tol is not None else None
        # A row that missed has no answering rung, so its counters would be empty and
        # the reader would lose the attempt count, the rejection count and the step
        # ceiling count that say WHY it missed. The counters of the rung that came
        # closest are reported instead, and counters_from names which rung they are.
        counters_tol = tol if res is not None else fallback
        counters_res = res if res is not None else cache.get(fallback)
        cycles = None
        accepted_only = None
        if res is not None and per_attempt is not None:
            attempts = int(res.get("attempts") or 0)
            n_acc = int(res.get("n_accepted") or 0)
            cycles = attempts * per_attempt
            accepted_only = n_acc * per_attempt
        counters = _counters()
        if counters_res is not None:
            counters = _counters(
                n_accepted=counters_res.get("n_accepted"),
                n_rejected=counters_res.get("n_rejected"),
                attempts=counters_res.get("attempts"),
                n_fevals=counters_res.get("n_fevals"),
                # FSAL saves one rhs evaluation per accepted step, and the cost
                # model excludes rhs evaluations for every class, so the saving is
                # reported here rather than folded into the cycle number.
                fevals_saved_by_fsal=counters_res.get("n_accepted"),
                overflow_rejected=counters_res.get("overflow_rejected"),
                h_q_initial=counters_res.get("h_q_initial"),
                h_q_final=counters_res.get("h_q_final"),
                h_q_clamped_high=counters_res.get("h_q_clamped_high"),
                max_abs_state=counters_res.get("max_abs_state"),
            )
        else:
            counters_tol = None
        rows.append(_row(
            entry["name_or_hash"], "adaptive", pname, target, "tol_q",
            entry["cost_basis"],
            control_value=tol,
            cycles=cycles,
            cycles_accepted_only=accepted_only,
            status=_adaptive_target_status(e["status"], rungs, float(target), floor),
            achieved_error=fl(res.get("achieved_error")) if res is not None else None,
            best_achieved_error=fl(best),
            ladder_monotone=bool(e["ladder_monotone"]),
            probes=0,
            counters=counters,
            counters_from=counters_tol,
            note=_ADAPTIVE_NOTE,
        ))
    return rows, cache


def control_tolerance_rows(entry: dict, pname: str, targets, n_max: int,
                           bisect_probes: int, fl: Floats) -> list[dict]:
    """The propagating formula on axis T as a plain fixed-step method. Class stays
    adaptive so the pair is read in one place; derived_fixed_step keeps it out of
    every adaptive aggregate."""
    rows = explicit_tolerance_rows(entry, BS32_TABLEAU, pname, targets,
                                   n_max, bisect_probes, fl)
    for r in rows:
        r["class"] = "adaptive"
        r["derived_fixed_step"] = True
        r["note"] = _FIXED_STEP_NOTE + ", with the controller taken away"
    return rows


# --------------------------------------------------------------------------- estimate floor

_FLOOR_DERIVATION = (
    "mean_bias_lsb_total is the four-term mean floor bias of one Q15 error estimate, "
    "in LSB of the scaled state. lsb_of_scaled_state converts one LSB into the "
    "problem's physical units as 1 / (32768 * scale). "
    "tolerance_below_which_unreachable is |mean_bias_lsb_total| * lsb_of_scaled_state "
    "/ peak, which puts the floor in the same units as a target. It is a stated "
    "conversion and not a measured equivalence: the bias is a property of one local "
    "estimate and the target is a global final-state error, so read it as the "
    "resolution the controller has to work with rather than as a bound on the "
    "achieved error."
)


def load_floor_artifact(pname: str) -> dict | None:
    """The adaptive.q15_estimate_floor side-track artifact for this problem at the
    suite's own scale, when one is on disk."""
    path = sidetrack.artifact_path("adaptive.q15_estimate_floor", f"{pname}_s1")
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def estimate_floor_entry(pname: str, caches: dict[str, dict], fl: Floats) -> dict:
    """The floor for one problem, preferring the side-track measurement and falling
    back to this run's own ladder. `source` says which, always."""
    bias = None
    source = "unavailable"
    doc = load_floor_artifact(pname)
    if doc is not None:
        v = (doc.get("summary") or {}).get("mean_bias_lsb_total")
        if isinstance(v, (int, float)) and math.isfinite(v):
            bias = float(v)
            source = "sidetrack:adaptive.q15_estimate_floor"
    if bias is None:
        vals = [float(r["mean_bias_lsb_total"])
                for r in caches.get(pname, {}).values()
                if isinstance(r.get("mean_bias_lsb_total"), (int, float))
                and math.isfinite(r["mean_bias_lsb_total"])]
        med = _median(vals)
        if med is not None:
            bias = med
            source = "measured_in_this_run"
    lsb = lsb_physical(pname)
    unreachable = None
    if bias is not None:
        unreachable = abs(bias) * lsb / V.PEAK[pname]
    return {
        "problem": pname,
        "mean_bias_lsb_total": fl(bias),
        "lsb_of_scaled_state": fl(lsb),
        "tolerance_below_which_unreachable": fl(unreachable),
        "source": source,
        "derivation": _FLOOR_DERIVATION,
    }


# --------------------------------------------------------------------------- matched point

MATCHED_POINT_RULE = (
    "the coarsest target on the shared ladder that is at or below the lowest axis-F "
    "Q15 error any method reached on this problem at budget_cycles")

MATCHED_POINT_CAVEAT = (
    "the rows gathered here carry three grades of cycle number: assembly_verified for "
    "the explicit tableaus, design_estimate_div32 for the SDIRK rows, which rest on a "
    "stated guess for a software reciprocal, and upper_bound_unpriced_branches for the "
    "Q15 adaptive rows, whose bit scan is booked at its worst case and whose branches "
    "the cost model prices not at all. Reading them as one column reads three "
    "different kinds of number as one")


def matched_point(pname: str, axis_f: list[dict], tol_rows: list[dict],
                  targets, reference_present: bool, fl: Floats) -> dict:
    cells = [r for r in axis_f if r["problem"] == pname and r["q15_error"] is not None]
    entry = {
        "problem": pname,
        "rule": MATCHED_POINT_RULE,
        "budget_cycles": BUDGET_CYCLES,
        "axis_f_reference_method": None,
        "axis_f_reference_class": None,
        "axis_f_reference_error": None,
        "axis_f_reference_source": ("validation/results.json plus this document"
                                    if reference_present else "this document only"),
        "axis_f_rows_considered": len(cells),
        "target": None,
        "target_key": None,
        "comparable_rows": [],
        "caveat": MATCHED_POINT_CAVEAT,
    }
    if not cells:
        return entry
    best = min(cells, key=lambda r: (r["q15_error"], r["method"]))
    entry["axis_f_reference_method"] = best["method"]
    entry["axis_f_reference_class"] = best["class"]
    entry["axis_f_reference_error"] = fl(best["q15_error"])
    below = [float(t) for t in targets if float(t) <= best["q15_error"]]
    if not below:
        # No rung sits at or under that error. That is a result about the ladder,
        # not a gap in the table, and comparable_rows stays empty on purpose.
        return entry
    target = max(below)
    key = _target_key(target)
    entry["target"] = target
    entry["target_key"] = key
    rows = [r for r in tol_rows
            if r["problem"] == pname and r["target_key"] == key
            and r["status"] == "reached" and r["cycles"] is not None]
    entry["comparable_rows"] = [{
        "method": r["method"],
        "class": r["class"],
        "derived_fixed_step": bool(r.get("derived_fixed_step", False)),
        "cycles": r["cycles"],
        "cycles_over_budget": fl(r["cycles"] / float(BUDGET_CYCLES)),
        "cost_basis": r["cost_basis"],
    } for r in sorted(rows, key=lambda r: (r["cycles"], r["method"]))]
    return entry


# --------------------------------------------------------------------------- verdicts

NOT_COMPARABLE = (
    "axis F asks what error a fixed cycle budget buys; axis T asks what cycles a "
    "fixed error costs. A row on one axis is not a row on the other. The two touch "
    "at matched_point and nowhere else, and even there the cycle numbers carry three "
    "different cost bases, named on every row")


def _class_stats(cls: str, methods: list[dict], axis_f: list[dict],
                 tol_rows: list[dict], matched: dict, targets, controls: bool,
                 reference_present: bool, fl: Floats) -> dict:
    sel = [m for m in methods
           if m["class"] == cls and bool(m.get("derived_fixed_step")) == controls]
    f_rows = [r for r in axis_f
              if r["class"] == cls and bool(r.get("derived_fixed_step")) == controls]
    t_rows = [r for r in tol_rows
              if r["class"] == cls and bool(r.get("derived_fixed_step")) == controls]
    finishers = [r for r in f_rows if _finished(r)]
    stiff_rows = [r for r in f_rows if V.STIFF.get(r["problem"], False)]
    stiff_fin = [r for r in stiff_rows if _finished(r)]
    q15_errors = [r["q15_error"] for r in f_rows if r["q15_error"] is not None]
    reached = [r for r in t_rows if r["status"] == "reached"]
    at_matched: list[float] = []
    for pname, mp in sorted(matched.items()):
        key = mp.get("target_key")
        if key is None:
            continue
        for r in t_rows:
            if r["problem"] == pname and r["target_key"] == key \
                    and r["status"] == "reached" and r["cycles"] is not None:
                at_matched.append(float(r["cycles"]))
    bases = sorted({m["cost_basis"] for m in sel})
    stats = {
        "methods_evaluated": len(sel),
        "axis_f_rows": len(f_rows),
        "axis_f_finishers": len(finishers),
        "axis_f_median_q15_error": fl(_median([float(v) for v in q15_errors])),
        "axis_t_rows": len(t_rows),
        "axis_t_reached": len(reached),
        "axis_t_median_cycles_at_matched_point": fl(_median(at_matched)),
        "stiff_finishers": len(stiff_fin),
        "stiff_total": len(stiff_rows),
        "cost_basis": bases,
    }
    stats["statement"] = _class_statement(cls, stats, len(targets), controls,
                                          reference_present)
    return stats


def _class_statement(cls: str, s: dict, n_targets: int, controls: bool,
                     reference_present: bool) -> str:
    """A sentence derived from the numbers, which has to read correctly whichever
    way they fall. The shape follows secondpass, where the verdict never asserts a
    direction the numbers did not take."""
    noun = "control" if controls else "method"
    if s["methods_evaluated"] == 0:
        return f"No {cls} {noun} was evaluated in this run."
    parts = [f"This run carries {_plural(s['methods_evaluated'], cls + ' ' + noun)}."]
    if s["axis_f_rows"] == 0:
        if cls == "explicit":
            parts.append(
                "It has no fixed-budget rows here because explicit axis F rows are "
                "the ones validation/results.json already publishes, and "
                + ("that document was read for the matched point but carried no rows "
                   "for these problems." if reference_present
                   else "that document was not read in this run."))
        elif cls == "adaptive":
            parts.append(
                "It has no fixed-budget rows, because a fixed cycle budget is not a "
                "question an adaptive method can be asked; its axis F standing is the "
                "derived fixed-step control, counted separately.")
        else:
            parts.append("It has no fixed-budget rows in this run.")
    else:
        parts.append(
            f"On axis F it has {_plural(s['axis_f_rows'], 'row')} and "
            f"{s['axis_f_finishers']} of them finished"
            + (f", including {s['stiff_finishers']} of "
               f"{_plural(s['stiff_total'], 'row')} on the stiff problems."
               if s["stiff_total"] else ", and none of them was on a stiff problem."))
    if s["axis_t_rows"] == 0:
        parts.append("It has no cycles-to-tolerance rows in this run.")
    else:
        parts.append(
            f"On axis T it has {_plural(s['axis_t_rows'], 'row')} over "
            f"{_plural(n_targets, 'target')} and reached the target in "
            f"{s['axis_t_reached']} of them.")
        med = s["axis_t_median_cycles_at_matched_point"]
        parts.append(
            "No row of this class reached a matched-point target, so it has no "
            "median cycle figure there."
            if med is None else
            f"Its median cycle count at the matched points it reached is "
            f"{format(med, '.0f')}.")
    parts.append(
        "Its cycle numbers rest on " + ", ".join(s["cost_basis"])
        + ", and what each of those covers is written out in cost_bases.")
    return " ".join(parts)


def _per_problem(pname: str, tol_rows: list[dict], matched: dict, fl: Floats) -> dict:
    mp = matched.get(pname, {})
    key = mp.get("target_key")
    entry = {
        "problem": pname,
        "stiff": bool(V.STIFF[pname]),
        "stiffness_ratio": fl(V.STIFFNESS_RATIO[pname]),
        "at_target_key": key,
        "winner_class": None,
        "best_by_class": {},
        "caveat": MATCHED_POINT_CAVEAT,
    }
    if key is None:
        return entry
    best_by_class: dict[str, dict] = {}
    for cls in CLASS_ORDER:
        rows = [r for r in tol_rows
                if r["problem"] == pname and r["target_key"] == key
                and r["class"] == cls and not r.get("derived_fixed_step")
                and r["status"] == "reached" and r["cycles"] is not None]
        if not rows:
            continue
        best = min(rows, key=lambda r: (r["cycles"], r["method"]))
        best_by_class[cls] = {
            "method": best["method"],
            "cycles": best["cycles"],
            "target_key": key,
            "cost_basis": best["cost_basis"],
        }
    entry["best_by_class"] = best_by_class
    if best_by_class:
        # CLASS_ORDER breaks a tie, so the winner is a function of the numbers and
        # the fixed order rather than of dict insertion.
        entry["winner_class"] = min(
            (c for c in CLASS_ORDER if c in best_by_class),
            key=lambda c: (best_by_class[c]["cycles"], CLASS_ORDER.index(c)))
    return entry


def _overall(per_class: dict, controls: dict, tol_rows: list[dict],
             axis_f: list[dict], matched: dict, targets) -> str:
    covered = [c for c in CLASS_ORDER if per_class[c]["methods_evaluated"] > 0]
    n_matched = sum(1 for mp in matched.values() if mp.get("target_key") is not None)
    class_count = ("one method class" if len(covered) == 1
                   else f"{len(covered)} method classes")
    parts = [
        f"This document reads {class_count} "
        f"({', '.join(covered) if covered else 'none'}) on two axes: a fixed "
        f"{BUDGET_CYCLES}-cycle budget, which only a fixed-step method can be asked, "
        f"and cycles to tolerance at {_plural(len(targets), 'target')}, which all "
        f"three can, because the answer is read off the achieved error and the error "
        f"is measured the same way for every class.",
        f"Axis T carries {_plural(len(tol_rows), 'row')} and axis F carries "
        f"{_plural(len(axis_f), 'row')}.",
    ]
    parts.append(
        f"{n_matched} of {_plural(len(matched), 'problem')} have a matched point, "
        f"where a target on the shared ladder sits at or below the lowest axis-F Q15 "
        f"error any method reached at the budget; on the rest, no rung of the ladder "
        f"is that coarse, which is a result about the ladder rather than a gap.")
    if controls["methods_evaluated"]:
        parts.append(
            f"The {_plural(controls['methods_evaluated'], 'derived fixed-step control')} "
            f"is counted on its own and never inside the adaptive figures: it is the "
            f"same tableau with the controller taken away.")
    parts.append(
        "The three classes do not share a cost basis, so the cycle columns are not "
        "one column. Every row names its basis and cost_bases says what each covers "
        "and what each leaves out.")
    return " ".join(parts)


def build_verdicts(methods: list[dict], axis_f: list[dict], tol_rows: list[dict],
                   matched: dict, targets, problems: list[str],
                   reference_present: bool, fl: Floats) -> dict:
    per_class = {
        cls: _class_stats(cls, methods, axis_f, tol_rows, matched, targets,
                          False, reference_present, fl)
        for cls in CLASS_ORDER
    }
    controls = _class_stats("adaptive", methods, axis_f, tol_rows, matched, targets,
                            True, reference_present, fl)
    return {
        "per_class": per_class,
        "controls": controls,
        "per_problem": {p: _per_problem(p, tol_rows, matched, fl) for p in problems},
        "axes": {
            "comparable": False,
            "why": NOT_COMPARABLE,
            "matched_point_rule": MATCHED_POINT_RULE,
        },
        "overall": _overall(per_class, controls, tol_rows, axis_f, matched, targets),
    }


# --------------------------------------------------------------------------- document

SCHEMA_VERSION = "validation-axes/1"

_SCHEMA_DOC: dict = {
    "version": SCHEMA_VERSION,
    "generated_from": (
        "provenance: verifier_hash (the pinned VERIFIER_HASH, the same value "
        "validation/results.json carries), sidetrack_code_hash (so a document built "
        "against different prototype bytes is detectable), archive_records, "
        "champion_hash, validation_results (what was read for the referenced explicit "
        "axis F rows, or absent), generated_ts (null unless the caller asked for a "
        "stamp, which is what keeps two builds byte-identical), prototype_params, and "
        "nonfinite_written_as_null"),
    "class_order": (
        "explicit, implicit, adaptive. Every iteration over classes uses this order, "
        "so the document is deterministic and leads with explicit"),
    "axes": (
        "fixed_budget (axis F) and cycles_to_tolerance (axis T), each with the classes "
        "it applies to, its control parameter and its work caps, plus not_comparable, "
        "which states in the document what the two axes do and do not share"),
    "cost_bases": (
        "one entry per cost basis used anywhere in the document: its grade, whether it "
        "is exact, an estimate or an upper bound, what it includes and what it leaves "
        "unpriced. Every method and every axis T row names its basis, so a reader can "
        "join a number to what it covers"),
    "problems": (
        "one compact entry per validation problem: n_states, t_end, scale, "
        "deriv_scale, peak, per_state_peaks, y0, stiff, stiffness_ratio, "
        "lsb_of_scaled_state, diverge_at, and the starting Q15 step with the flag "
        "saying whether it already saturates, because a window long enough to "
        "saturate it forces at least t_end steps whatever the tolerance. The full "
        "prose entry, with domain and source, is in validation/results.json and is "
        "not duplicated"),
    "methods": (
        "one entry per evaluated method: name_or_hash, class explicit|implicit|adaptive, "
        "kind classical|discovered|prototype|prototype_control, control steps|tol_q, "
        "source fixture|archive|prototype, cost_basis, arithmetic q15|float64, roles, "
        "derived_fixed_step, and the tableau where one exists. Explicit and control "
        "entries carry cycles_per_step and steps; implicit entries carry "
        "cycles_per_step, cycles_per_step_terms, gamma, a21, newton_iters, jacobian and "
        "jacobian_by_problem; adaptive entries carry pair, controller, "
        "cycles_per_attempt and branch_allowance. work_cap holds the deterministic "
        "caps, which are counts and bounds on the data and never a clock"),
    "results_fixed_budget": (
        "axis F. One row per (method, problem) for every method whose control is steps "
        "or which is a derived fixed-step control, EXCEPT the explicit class, whose "
        "axis F rows are the ones validation/results.json publishes and are referenced "
        "rather than duplicated. Row shape follows results.json and adds class, "
        "arithmetic and derived_fixed_step. An implicit row runs in float64 and so "
        "carries float_error with q15_error null"),
    "results_tolerance": (
        "axis T. One row per (method, problem, target) for every method and every "
        "target. cycles is the comparable number and cycles_accepted_only is the "
        "lower bound that ignores rejected work; both are null when the target was "
        "not reached or the class carries no cycle number. achieved_error is the "
        "error at the rung that answered, null on a miss, and best_achieved_error is "
        "the lowest error anywhere on that sampled ladder, so a method that missed by "
        "a little is distinguishable from one that never ran. status says why a miss "
        "was a miss. counters carries every per-class counter key with null where it "
        "does not apply, so an adaptive cycles number can be re-derived as attempts "
        "times the per-attempt total. counters_from names the rung those counters "
        "belong to: the answering rung when the target was reached, and otherwise the "
        "rung that came closest, so a miss still says how many attempts it took and "
        "how often the step hit the ceiling"),
    "status_rules": (
        "a tolerance row is reached when a rung of its own ladder met the target. A "
        "miss is named by the reason it missed, in the order one reason supersedes "
        "another: below_bias_floor when the target sits under the measured floor "
        "bias of the Q15 error estimate, which only the Q15 adaptive run has; "
        "attempt_cap, step_underflow or overflow_before_target when every rung ended "
        "that way; h_q_ceiling when every sampled rung saturated the Q15 step ceiling "
        "and at least one of them still produced a number, so the step size was "
        "bounded by the encoding rather than by the controller, which names a "
        "constraint the run was under without claiming it was the only reason for the "
        "miss; newton_nonconvergent when an implicit ladder had no "
        "clean rung and every failure was a non-finite iterate; never_reached "
        "otherwise"),
    "estimate_floor": (
        "per problem, the measured floor bias of the Q15 error estimate and the "
        "target below which a miss is an artifact of the estimate rather than of the "
        "method, with the source of the measurement and the derivation of the "
        "conversion"),
    "matched_point": (
        "per problem, the one stated place the two axes touch, with the rule written "
        "out, the axis F row it was read from, the target it resolves to (null when "
        "no rung of the ladder is that coarse), the rows that reached that target "
        "with cycles_over_budget, and the caveat that names the three cost bases"),
    "verdicts": (
        "per_class for each of the three classes with the derived fixed-step control "
        "counted separately in controls; per_problem with the best row per class at "
        "the matched point and the class that spent the fewest cycles there; axes, "
        "which states that the two axes are not comparable; and overall. Every "
        "statement is derived from the numbers and reads correctly whichever way they "
        "fall, and every statement passes the banned-word and em-dash check"),
    "not_a_public_page_source": (
        "the traceability rule lists key_findings.json, validation/results.json, "
        "benchmark/results.json and the side-track ledger with its artifacts. This "
        "document is not on that list, and adding it is an owner decision"),
}


def _problem_entry(pname: str, fl: Floats) -> dict:
    p = V.PROBLEMS[pname]
    return {
        "name": pname,
        "n_states": p.n_states,
        "t_end": fl(p.t_end),
        "scale": fl(p.scale),
        "deriv_scale": fl(V.DERIV_SCALE[pname]),
        "family": p.family,
        "y0": [fl(v) for v in V.Y0_PHYS[pname]],
        "peak": fl(V.PEAK[pname]),
        "per_state_peaks": [fl(v) for v in V.PER_STATE_PEAKS[pname]],
        "stiff": bool(V.STIFF[pname]),
        "stiffness_ratio": fl(V.STIFFNESS_RATIO[pname]),
        "lsb_of_scaled_state": fl(lsb_physical(pname)),
        "diverge_at": fl(diverge_bound(pname)),
        "analytic_jacobian": V.ANALYTIC_JACOBIAN[pname] is not None,
        # h_q cannot reach 1.0 in a problem's own time units, so on a long window the
        # starting step is already the Q15 ceiling and the run needs at least t_end
        # steps whatever the tolerance is set to. That is a property of the encoding
        # and not of adaptivity, and it is stated per problem here rather than left to
        # be inferred from a run that happened to do badly. The function is
        # adaptive_q15's own, so the two cannot drift apart.
        "h_q_initial": int(AQ._initial_h_q(p.t_end)),
        "h_q_max": int(AQ.Q15_MAX),
        "h_q_saturates_at_start": bool(AQ._initial_h_q(p.t_end) >= AQ.Q15_MAX),
    }


def _restrict(entry: dict, problems: list[str]) -> dict:
    """Trim the per-problem maps on a method entry to the selection, so a partial run
    cannot be read as a full one."""
    keep = set(problems)
    for key in ("cycles_per_step", "steps", "cycles_per_step_terms",
                "cycles_per_attempt", "jacobian_by_problem"):
        v = entry.get(key)
        if isinstance(v, dict):
            entry[key] = {k: v[k] for k in sorted(v) if k in keep}
    cap = entry.get("work_cap")
    if isinstance(cap, dict) and isinstance(cap.get("diverge_at"), dict):
        cap["diverge_at"] = {k: cap["diverge_at"][k]
                             for k in sorted(cap["diverge_at"]) if k in keep}
    return entry


def _stamp_work_cap(entry: dict, n_max: int, bisect_probes: int,
                    max_attempts: int) -> dict:
    """Every method carries the deterministic caps that actually bounded it.

    A step-controlled method is bounded by the ladder and its bisection budget; a
    tolerance-controlled one by the attempt cap. None of them is a clock, which is
    the sidetrack.SWEEP_MAX_ATTEMPTS rule: an artifact bounded by wall time stops
    being a pure function of its inputs.
    """
    cap = dict(entry.get("work_cap") or {})
    steps = entry["control"] == "steps"
    cap["n_max"] = int(n_max) if steps else None
    cap["bisect_probes"] = int(bisect_probes) if steps else None
    cap["max_attempts"] = None if steps else int(max_attempts)
    cap.setdefault("diverge_at", None)
    entry["work_cap"] = cap
    return entry


_AUTO = object()


def build_axes(classes: tuple[str, ...] = CLASS_ORDER,
               problems: list[str] | tuple[str, ...] | None = None,
               targets=TARGETS,
               anchors: tuple[str, ...] = V.CLASSICAL_ANCHOR_NAMES,
               records=None, champ: str | None = None,
               n_max: int = N_MAX, bisect_probes: int = BISECT_PROBES,
               tol_ladder: tuple[int, ...] = TOL_LADDER,
               max_attempts: int = MAX_ATTEMPTS,
               adaptive_arithmetics: tuple[str, ...] = ARITHMETICS,
               newton_iters: int = NEWTON_ITERS,
               include_control: bool = True,
               reference=_AUTO,
               generated_ts: str | None = None) -> dict:
    """The two-axis document. Pure function of (this module, the prototypes, the
    archive slice, the referenced validation document) plus these parameters, with
    the single exception of generated_ts, which the caller supplies.

    Pass records/champ/reference explicitly for testing; nothing here reads the live
    archive unless it is asked to.
    """
    unknown = [c for c in classes if c not in CLASS_ORDER]
    if unknown:
        raise ValueError(f"unknown classes {unknown}, want a subset of {list(CLASS_ORDER)}")
    sel_classes = tuple(c for c in CLASS_ORDER if c in set(classes))
    if not sel_classes:
        raise ValueError("at least one method class must be selected")
    names = list(V.VALIDATION_NAMES if problems is None else problems)
    bad = sorted(set(names) - set(V.VALIDATION_NAMES))
    if bad:
        raise ValueError(f"unknown problems {bad}, want a subset of {list(V.VALIDATION_NAMES)}")
    pnames = [n for n in sorted(V.VALIDATION_NAMES) if n in set(names)]
    if not pnames:
        raise ValueError("at least one problem must be selected")
    tlist = [float(t) for t in targets]
    if not tlist:
        raise ValueError("at least one target must be selected")

    fl = Floats()
    ref_doc = load_reference() if reference is _AUTO else reference
    ref_rows = [r for r in reference_rows(ref_doc, fl) if r["problem"] in set(pnames)]

    methods: list[dict] = []
    tabs: dict[str, Tableau] = {}
    if "explicit" in sel_classes:
        for entry, t in _explicit_methods(tuple(anchors), records, champ):
            methods.append(entry)
            tabs[entry["name_or_hash"]] = t
    if "implicit" in sel_classes:
        methods.extend(_implicit_methods(newton_iters))
    if "adaptive" in sel_classes:
        methods.extend(_adaptive_methods(tuple(adaptive_arithmetics), tuple(tol_ladder),
                                         max_attempts))
        if include_control:
            methods.append(_control_method())
    methods = [_stamp_work_cap(_restrict(m, pnames), n_max, bisect_probes, max_attempts)
               for m in methods]

    axis_f: list[dict] = list(ref_rows)
    tol_rows: list[dict] = []
    adaptive_caches: dict[str, dict[str, dict]] = {}

    for m in methods:
        name = m["name_or_hash"]
        cls = m["class"]
        for pname in pnames:
            if cls == "explicit":
                tol_rows.extend(explicit_tolerance_rows(
                    m, tabs[name], pname, tlist, n_max, bisect_probes, fl))
            elif cls == "implicit":
                axis_f.append(implicit_fixed_budget_row(m, pname, newton_iters, fl))
                tol_rows.extend(implicit_tolerance_rows(
                    m, pname, tlist, n_max, bisect_probes, newton_iters, fl))
            elif m.get("derived_fixed_step"):
                axis_f.append(control_fixed_budget_row(pname, fl))
                tol_rows.extend(control_tolerance_rows(
                    m, pname, tlist, n_max, bisect_probes, fl))
            else:
                rows, cache = adaptive_tolerance_rows(
                    m, pname, tlist, tuple(tol_ladder), max_attempts, None, fl)
                adaptive_caches.setdefault(name, {})[pname] = cache
                tol_rows.extend(rows)

    # The floor is measured from the Q15 run, so it is known only after the ladders
    # have run; the adaptive statuses that depend on it are settled here in one pass
    # rather than guessed at row-build time.
    q15_caches = adaptive_caches.get(M_BS32_Q15, {})
    floors = {p: estimate_floor_entry(p, q15_caches, fl) for p in pnames}
    arith_of = {m["name_or_hash"]: m["arithmetic"] for m in methods}
    for r in tol_rows:
        if r["class"] != "adaptive" or r.get("derived_fixed_step") or r["status"] == "reached":
            continue
        # The floor bias is a property of the Q15 error estimate. The float twin runs
        # the same controller in float64 and has no such floor, so it is never told
        # its miss was below one.
        floor = (floors[r["problem"]]["tolerance_below_which_unreachable"]
                 if arith_of.get(r["method"]) == "q15" else None)
        cache = adaptive_caches.get(r["method"], {}).get(r["problem"], {})
        rungs = [cache[q] for q in sorted(cache, reverse=True)]
        r["status"] = _adaptive_target_status(r["status"], rungs, r["target"], floor)

    matched = {p: matched_point(p, axis_f, tol_rows, tlist, ref_doc is not None, fl)
               for p in pnames}

    axis_f.sort(key=lambda r: (CLASS_ORDER.index(r["class"]), r["method"], r["problem"]))
    tol_rows.sort(key=lambda r: (CLASS_ORDER.index(r["class"]), r["method"],
                                 r["problem"], tlist.index(r["target"])))

    doc = {
        "schema": _SCHEMA_DOC,
        "generated_from": {
            "verifier_hash": pinned_verifier_hash() or compute_verifier_hash(),
            "sidetrack_code_hash": sidetrack.code_hash(),
            "archive_records": 0 if records is None else len(list(records)),
            "champion_hash": champ,
            "validation_results": _reference_summary(ref_doc),
            "generated_ts": generated_ts,
            "classes": list(sel_classes),
            "prototype_params": {
                "bs32_table": AQ.FTAB33.name,
                "controller": _controller_block(),
                "newton_iters": int(newton_iters),
                "gamma": float(SD.GAMMA),
                "a21": float(1.0 - SD.GAMMA),
                "div_cycles": int(SD.DIV_CYCLES),
                "tol_ladder_lsb": [int(v) for v in tol_ladder],
                "n_max": int(n_max),
                "bisect_probes": int(bisect_probes),
                "max_attempts": int(max_attempts),
                "diverge_at": {
                    "rule": "1000 * max(peak, 1.0), a bound on the data and not a clock",
                    "values": {p: fl(diverge_bound(p)) for p in pnames},
                },
                "adaptive_arithmetics": list(adaptive_arithmetics),
            },
        },
        "cost_model": COST_MODEL.name,
        "rounding": ROUNDING,
        "class_order": list(CLASS_ORDER),
        "targets": tlist,
        "target_keys": [_target_key(t) for t in tlist],
        "tolerance_ladder_lsb": [int(v) for v in tol_ladder],
        "axes": {
            "fixed_budget": {
                "applies_to": ["explicit", "implicit"],
                "control": "steps",
                "budget_cycles": BUDGET_CYCLES,
                "cost_model": COST_MODEL.name,
                "metric": "L2 final-state error over PEAK",
                "note": ("an adaptive method appears here only as a derived "
                         "fixed-step control, marked derived_fixed_step"),
            },
            "cycles_to_tolerance": {
                "applies_to": list(CLASS_ORDER),
                "control": "steps or tol_q",
                "targets": tlist,
                "target_keys": [_target_key(t) for t in tlist],
                "n_max": int(n_max),
                "bisect_probes": int(bisect_probes),
                "tol_ladder_lsb": [int(v) for v in tol_ladder],
                "metric": "L2 final-state error over PEAK",
                "adaptive_rule": ("the coarsest rung on the sampled tolerance ladder "
                                  "whose achieved error is at or under the target, "
                                  "which is the step ladder rule read on a tolerance "
                                  "ladder; no bisection is taken because whole LSB "
                                  "rungs have nothing between them"),
            },
            "not_comparable": NOT_COMPARABLE,
        },
        "cost_bases": {k: dict(v) for k, v in sorted(COST_BASIS_DOC.items())},
        "problems": [_problem_entry(p, fl) for p in pnames],
        "methods": methods,
        "results_fixed_budget": axis_f,
        "results_tolerance": tol_rows,
        "estimate_floor": floors,
        "matched_point": matched,
    }
    doc["verdicts"] = build_verdicts(methods, axis_f, tol_rows, matched, tlist,
                                     pnames, ref_doc is not None, fl)
    doc["generated_from"]["nonfinite_written_as_null"] = fl.nonfinite
    return doc


def _reference_summary(doc: dict | None) -> dict:
    if not doc:
        return {"present": False,
                "note": "validation/results.json was not read in this run"}
    gf = doc.get("generated_from") or {}
    return {
        "present": True,
        "schema_keys": sorted(k for k in doc if isinstance(k, str)),
        "budget_cycles": doc.get("budget_cycles"),
        "cost_model": doc.get("cost_model"),
        "archive_records": gf.get("archive_records"),
        "champion_hash": gf.get("champion_hash"),
        "verifier_hash": gf.get("verifier_hash"),
        "methods": len(doc.get("methods") or []),
        "results": len(doc.get("results") or []),
    }


# --------------------------------------------------------------------------- validation

def _walk_floats(node, path: str, fail) -> None:
    if isinstance(node, dict):
        for k in sorted(node, key=str):
            _walk_floats(node[k], f"{path}.{k}", fail)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk_floats(v, f"{path}[{i}]", fail)
    elif isinstance(node, float) and not math.isfinite(node):
        fail(f"{path} must be finite or null, got {node!r}")


def _walk_prose(node, path: str, fail) -> None:
    """Every string in the document goes through the findings-site word rule. The
    verdicts are the part a person would paste somewhere, but a status enum or a
    schema sentence can reach a page just as easily."""
    if isinstance(node, dict):
        for k in sorted(node, key=str):
            _walk_prose(node[k], f"{path}.{k}", fail)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk_prose(v, f"{path}[{i}]", fail)
    elif isinstance(node, str):
        _check_prose(node, path, fail)


def validate_axes(doc: dict) -> None:
    """Raise ValueError if the document violates the schema described above."""
    def fail(msg: str):
        raise ValueError(f"axes schema: {msg}")

    for key in ("schema", "generated_from", "cost_model", "rounding", "class_order",
                "targets", "target_keys", "tolerance_ladder_lsb", "axes", "cost_bases",
                "problems", "methods", "results_fixed_budget", "results_tolerance",
                "estimate_floor", "matched_point", "verdicts"):
        if key not in doc:
            fail(f"missing top-level key {key!r}")
    if doc["schema"].get("version") != SCHEMA_VERSION:
        fail(f"schema.version must be {SCHEMA_VERSION!r}")
    if doc["class_order"] != list(CLASS_ORDER):
        fail(f"class_order must be {list(CLASS_ORDER)}")
    gf = doc["generated_from"]
    for k in ("verifier_hash", "sidetrack_code_hash"):
        if not isinstance(gf.get(k), str) or not gf[k]:
            fail(f"generated_from.{k} must be a non-empty string")
    if not isinstance(gf.get("archive_records"), int) or isinstance(gf["archive_records"], bool):
        fail("generated_from.archive_records must be an integer")
    if not isinstance(gf.get("nonfinite_written_as_null"), int):
        fail("generated_from.nonfinite_written_as_null must be an integer")
    if gf.get("generated_ts") is not None and not isinstance(gf["generated_ts"], str):
        fail("generated_from.generated_ts must be a string or null")

    axes = doc["axes"]
    for k in ("fixed_budget", "cycles_to_tolerance", "not_comparable"):
        if k not in axes:
            fail(f"axes.{k} is missing")
    if axes["fixed_budget"].get("budget_cycles") != BUDGET_CYCLES:
        fail("axes.fixed_budget.budget_cycles must be the shared budget")

    for name, block in doc["cost_bases"].items():
        if name not in COST_BASES:
            fail(f"cost_bases carries unknown basis {name!r}")
        for k in ("grade", "direction", "includes", "excludes", "source"):
            if not isinstance(block.get(k), str):
                fail(f"cost_bases.{name}.{k} must be a string")

    pnames = [p["name"] for p in doc["problems"]]
    if sorted(pnames) != sorted(set(pnames)):
        fail("duplicate problem entries")
    for p in doc["problems"]:
        if p["name"] not in V.VALIDATION_NAMES:
            fail(f"unknown problem {p['name']!r}")
    tkeys = doc["target_keys"]
    if len(tkeys) != len(doc["targets"]) or tkeys != [_target_key(t) for t in doc["targets"]]:
        fail("target_keys must be the repr of every target, in the same order")

    mnames = [m["name_or_hash"] for m in doc["methods"]]
    if len(set(mnames)) != len(mnames):
        fail("duplicate method entries")
    steps_methods: set[str] = set()
    for m in doc["methods"]:
        n = m["name_or_hash"]
        if m.get("class") not in CLASS_ORDER:
            fail(f"method {n!r} has bad class {m.get('class')!r}")
        if m.get("kind") not in METHOD_KINDS:
            fail(f"method {n!r} has bad kind {m.get('kind')!r}")
        if m.get("control") not in CONTROL_KINDS:
            fail(f"method {n!r} has bad control {m.get('control')!r}")
        if m.get("source") not in SOURCES:
            fail(f"method {n!r} has bad source {m.get('source')!r}")
        if m.get("cost_basis") not in COST_BASES:
            fail(f"method {n!r} has bad cost_basis {m.get('cost_basis')!r}")
        if m.get("arithmetic") not in ARITHMETICS:
            fail(f"method {n!r} has bad arithmetic {m.get('arithmetic')!r}")
        if not isinstance(m.get("derived_fixed_step"), bool):
            fail(f"method {n!r} derived_fixed_step must be a boolean")
        if not isinstance(m.get("work_cap"), dict):
            fail(f"method {n!r} must carry work_cap")
        for k in ("n_max", "bisect_probes", "max_attempts"):
            if k not in m["work_cap"]:
                fail(f"method {n!r} work_cap missing {k!r}")
        if (m["control"] == "steps" or m["derived_fixed_step"]) and m["class"] != "explicit":
            steps_methods.add(n)

    seen_f = set()
    for r in doc["results_fixed_budget"]:
        for k in ("method", "class", "problem", "q15_error", "float_error",
                  "arithmetic", "derived_fixed_step"):
            if k not in r:
                fail(f"fixed-budget row missing {k!r}: {r}")
        if r["problem"] not in pnames:
            fail(f"fixed-budget row references unknown problem {r['problem']!r}")
        key = (r["method"], r["problem"])
        if key in seen_f:
            fail(f"duplicate fixed-budget row {key}")
        seen_f.add(key)
    want_f = {(n, p) for n in steps_methods for p in pnames}
    have_f = {k for k in seen_f if k[0] in steps_methods}
    if want_f != have_f:
        fail("results_fixed_budget must cover every non-explicit step-controlled "
             f"(method, problem) pair exactly once; missing {sorted(want_f - have_f)}, "
             f"extra {sorted(have_f - want_f)}")

    seen_t = set()
    for r in doc["results_tolerance"]:
        for k in ("method", "class", "problem", "target", "target_key", "control_kind",
                  "control_value", "cycles", "cycles_accepted_only", "status",
                  "achieved_error", "best_achieved_error", "ladder_monotone",
                  "probes", "cost_basis", "counters_from",
                  "counters", "note"):
            if k not in r:
                fail(f"tolerance row missing {k!r}: {r}")
        if r["method"] not in mnames:
            fail(f"tolerance row references unknown method {r['method']!r}")
        if r["problem"] not in pnames:
            fail(f"tolerance row references unknown problem {r['problem']!r}")
        if r["target_key"] not in tkeys:
            fail(f"tolerance row references unknown target {r['target_key']!r}")
        if r["status"] not in TARGET_STATUSES:
            fail(f"tolerance row has bad status {r['status']!r}")
        if r["control_kind"] not in CONTROL_KINDS:
            fail(f"tolerance row has bad control_kind {r['control_kind']!r}")
        if r["cost_basis"] not in COST_BASES:
            fail(f"tolerance row {r['method']}/{r['problem']} has bad cost_basis "
                 f"{r['cost_basis']!r}")
        if isinstance(r["cycles"], (int, float)) and not r.get("cost_basis"):
            fail(f"tolerance row {r['method']}/{r['problem']} carries cycles with no "
                 "cost basis")
        if r["cycles"] is not None and r["cycles_accepted_only"] is not None \
                and r["cycles_accepted_only"] > r["cycles"]:
            fail(f"tolerance row {r['method']}/{r['problem']} has "
                 "cycles_accepted_only above cycles")
        if r["counters"] is not None:
            extra = sorted(set(r["counters"]) - set(COUNTER_KEYS))
            if extra:
                fail(f"tolerance row carries unknown counters {extra}")
        key = (r["method"], r["problem"], r["target_key"])
        if key in seen_t:
            fail(f"duplicate tolerance row {key}")
        seen_t.add(key)
    want_t = {(n, p, k) for n in mnames for p in pnames for k in tkeys}
    if want_t != seen_t:
        fail("results_tolerance must cover every (method, problem, target) exactly "
             f"once; missing {len(want_t - seen_t)}, extra {len(seen_t - want_t)}")

    for pname, mp in doc["matched_point"].items():
        if pname not in pnames:
            fail(f"matched_point carries unknown problem {pname!r}")
        for k in ("rule", "budget_cycles", "target", "target_key", "comparable_rows",
                  "caveat"):
            if k not in mp:
                fail(f"matched_point[{pname}] missing {k!r}")
        if mp["target"] is None and mp["comparable_rows"]:
            fail(f"matched_point[{pname}] has no target but carries comparable rows")
    for pname, ef in doc["estimate_floor"].items():
        if pname not in pnames:
            fail(f"estimate_floor carries unknown problem {pname!r}")
        for k in ("mean_bias_lsb_total", "lsb_of_scaled_state",
                  "tolerance_below_which_unreachable", "source", "derivation"):
            if k not in ef:
                fail(f"estimate_floor[{pname}] missing {k!r}")

    v = doc["verdicts"]
    for k in ("per_class", "controls", "per_problem", "axes", "overall"):
        if k not in v:
            fail(f"verdicts missing {k!r}")
    if v["axes"].get("comparable") is not False:
        fail("verdicts.axes.comparable must be false")
    if not isinstance(v["overall"], str) or not v["overall"].strip():
        fail("verdicts.overall must be a non-empty string")
    for cls in CLASS_ORDER:
        if cls not in v["per_class"]:
            fail(f"verdicts.per_class missing {cls!r}")
        block = v["per_class"][cls]
        for k in ("methods_evaluated", "axis_f_rows", "axis_f_finishers",
                  "axis_t_rows", "axis_t_reached", "stiff_finishers", "stiff_total",
                  "cost_basis", "statement"):
            if k not in block:
                fail(f"verdicts.per_class.{cls} missing {k!r}")
        if not isinstance(block["statement"], str) or not block["statement"].strip():
            fail(f"verdicts.per_class.{cls}.statement must be a non-empty string")

    _walk_floats(doc, "doc", fail)
    _walk_prose(doc, "doc", fail)


def write_axes(doc: dict, path: Path | str | None = None) -> Path:
    out = Path(path) if path is not None else work_dir() / "validation" / "axes.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, indent=1, sort_keys=True, allow_nan=False) + "\n"
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return out


def _utc_stamp() -> str:
    """UTC on the way into the file. Display in US Central is the site's job, through
    rk_harness.timefmt, and never this module's."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Two-axis validation document: fixed budget and cycles to "
                    "tolerance, for explicit, implicit and adaptive methods.")
    ap.add_argument("--classes", default=",".join(CLASS_ORDER),
                    help="comma-separated subset of " + ",".join(CLASS_ORDER))
    ap.add_argument("--problems", default=None,
                    help="comma-separated subset of " + ",".join(V.VALIDATION_NAMES))
    ap.add_argument("--out", default=None,
                    help="output path (default: <RK_WORK_DIR>/validation/axes.json)")
    ap.add_argument("--n-max", type=int, default=N_MAX,
                    help=f"top rung of the step ladder (default: {N_MAX})")
    ap.add_argument("--bisect-probes", type=int, default=BISECT_PROBES,
                    help=f"probes inside the bracketing octave (default: {BISECT_PROBES})")
    ap.add_argument("--max-attempts", type=int, default=MAX_ATTEMPTS,
                    help=f"adaptive work cap, in attempts (default: {MAX_ATTEMPTS})")
    ap.add_argument("--no-discovered", action="store_true",
                    help="anchors only; do not read the archive for elites")
    ap.add_argument("--no-stamp", action="store_true",
                    help="omit generated_ts, so two builds are byte-identical")
    a = ap.parse_args(argv)

    classes = tuple(s.strip() for s in a.classes.split(",") if s.strip())
    names = [s.strip() for s in a.problems.split(",") if s.strip()] if a.problems else None
    records = champ = None
    if "explicit" in classes and not a.no_discovered:
        from rk_harness.archive import read_all
        records = read_all()
        champ = V.champion_hash()
    doc = build_axes(classes=classes, problems=names, records=records, champ=champ,
                     n_max=a.n_max, bisect_probes=a.bisect_probes,
                     max_attempts=a.max_attempts,
                     generated_ts=None if a.no_stamp else _utc_stamp())
    validate_axes(doc)
    out = write_axes(doc, a.out)
    print(f"wrote {out}")
    print(f"methods: {len(doc['methods'])}, axis F rows: "
          f"{len(doc['results_fixed_budget'])}, axis T rows: "
          f"{len(doc['results_tolerance'])}")
    for cls in CLASS_ORDER:
        print(f"  {cls}: {doc['verdicts']['per_class'][cls]['statement']}")
    print(doc["verdicts"]["overall"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
