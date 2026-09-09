"""Deterministic measurement core for the three-class benchmark (T10).

``benchmark.py`` has an adaptive half that runs SciPy as a REFERENCE and a
fixed-step half that runs our Q15 explicit methods against float64 rk4. There is
no ours-against-theirs for adaptive and nothing at all for implicit, and the
stiffest problem it has ever seen is rc_thermal at a stiffness ratio of 70.1,
which is not a problem an implicit method exists for.

This module is the half of the answer that reads no clock. It holds every
pure-function measurement the three-class comparison needs (achieved error,
function evaluations, Jacobian evaluations, LU factorizations, accepted and
rejected steps, analytic cycles, the shared ladder) and it neither times anything
nor writes a BLAS thread variable. ``benchmark.py`` keeps ``measure``, the
environment stamp and the document assembly and imports this module. That split
is what would later let the counters run inside the container without breaking
docs/SIDETRACK-AUTOMATION.md D9, which keeps wall-clock measurement on the host.

WHAT IT DOES NOT ACHIEVE, said plainly: importing this module still pulls numpy
and scipy, because ``rk_harness.problems`` is a pinned file and imports both at
module scope. The property that is actually held here, and tested, is that
importing it changes no environment variable and starts no clock.

THE SHARED CONDITION IS MATCHED ACCURACY, NOT MATCHED WALL CLOCK.

Ours runs Q15 integer arithmetic through a Python loop that models a Cortex-M0+;
theirs runs float64 inside compiled SciPy on a desktop. A microsecond from one is
not a microsecond from the other, and no amount of care about the stopwatch fixes
that. So every row in ``matched_accuracy`` answers one question that both sides
can be asked honestly: what does it cost to bring THIS problem to THIS error,
where the error is measured by the problem set's own final-state metric for every
solver on both sides. Cost is then reported in each side's own currency, with the
currency named on the row:

* function evaluations, Jacobian evaluations, LU factorizations and linear solves,
  which are implementation-independent and are the work-precision currency the
  literature uses,
* accepted and rejected steps, which diagnose a step controller,
* analytic cycles under the pinned cost model, which exist only for the sides
  that have a cost model at all, carrying a ``cost_grade`` that says which of
  three grades of number it is,
* wall clock, measured by the caller, carrying a ``timing_family`` so that a
  ratio between two timings from different families can be refused by the
  validator instead of by a caveat nobody reads.

Every row carries ``controls_for`` and ``not_controlled``, both derived from the
row's own fields rather than written by hand, because a cross-class or
cross-library comparison is worth nothing unless the uncontrolled differences are
named in the document.

WHERE A NUMBER CANNOT BE MADE COMPARABLE IT IS NULL WITH A REASON. SciPy reports
no rejected-step count, so a Radau row's ``n_steps_rejected`` is null and
``n_steps_rejected_basis`` says why. For RK23 and RK45 the count is recoverable
exactly, because those two spend a fixed number of derivative evaluations per
attempt, and the row says that is where the number came from and refuses it when
the arithmetic does not come out whole. SciPy has no cycle model, so every
library row's analytic cycles are null with ``cost_grade`` "none" rather than a
plausible figure.

THE TIGHTEST HONEST COMPARISON IN THE PROJECT is our Bogacki-Shampine 3(2) pair
against SciPy RK23, because SciPy RK23 IS Bogacki-Shampine 3(2). The tableau is
identical, so every difference between the two is the step controller (our dyadic
PI with alpha 1/4, beta 1/8, safety 7/8 and a clamp of [1/4, 2], against SciPy's
I controller with safety 0.9 and a clamp of [0.2, 10]) plus the arithmetic. That
comparison gets its own section, ``adaptive_pairs``, with ``same_pair`` true, and
it is the adaptive headline rather than a row buried in a table. It is available
for adaptive and for nothing else in this document, because adaptive is the only
class where both sides share an accept test: our ``solve_adaptive`` accepts a step
when the RMS norm of err / (atol + rtol * max(|y|, |y_new|)) is at or under 1,
which is SciPy's own convention, so rtol = atol = tol is a genuine matched
condition against RK23 and RK45 and against nothing else here.

REUSE, NOT A SECOND MEASUREMENT PATH. The ours-side probes are
``validation_axes``'s: the fixed-step Q15 probe, the SDIRK2 probe and the adaptive
probe, plus its two cost functions, all imported read-only. The ladder is
``secondpass.ladder_scan``. The implicit budget ladder is ``sidetrack``'s own
divergence bound and fixed-step row. What is new here is the library side, the
pairing, the cost grades and the comparability fields. Two documents that
recompute the same quantity with two slightly different probes is exactly the
drift this avoids, and ``tests/test_t10_benchmark.py`` pins the agreement.

DETERMINISM. Nothing in this module reads a clock, samples anything or iterates an
unordered collection. Every budget is a work cap expressed in probes, rungs, step
counts or attempts, never in seconds, so an artifact stays a pure function of its
inputs. That is the same rule ``secondpass`` and ``sidetrack`` already hold.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from rk_harness import sidetrack as ST
from rk_harness import validation as V
from rk_harness import validation_axes as VA
from rk_harness.costmodel import M0PLUS_FAST
from rk_harness.problems import PROBLEMS as FROZEN_PROBLEMS
from rk_harness.problems import error_metric as frozen_error_metric
from rk_harness.problems import load_fixture
from rk_harness.prototypes import adaptive_q15 as AQ
from rk_harness.prototypes import sdirk as SD
from rk_harness.secondpass import BISECT_PROBES, TARGETS, _target_key, ladder_scan
from rk_harness.types import Tableau

BUDGET_CYCLES = 65536
COST_MODEL = M0PLUS_FAST

# Explicit leads, then implicit, then adaptive: the same tuple validation_axes
# iterates, so the two documents present the three classes in one order.
CLASS_ORDER: tuple[str, ...] = ("explicit", "implicit", "adaptive")
SIDES: tuple[str, ...] = ("ours", "library")
ARITHMETICS: tuple[str, ...] = ("q15", "float64", "compiled_float64")
CONTROLS: tuple[str, ...] = ("fixed_step", "tolerance")
TIMING_FAMILIES: tuple[str, ...] = ("python_loop", "compiled_scipy")

# Four grades of cycle number, and every row that carries a cycle number says
# which grade it is. Two of the three that exist would otherwise borrow the
# credibility of the one that is assembly-verified.
COST_GRADES: tuple[str, ...] = (
    "assembly_verified",
    "design_estimate",
    "upper_bound_unpriced_branches",
    "none",
)

# A superset of the five statuses the design named. A class's real failure mode is
# named rather than flattened into "never_reached": an adaptive run that hit its
# attempt cap and one that could not represent a small enough step are different
# facts, and both are results rather than gaps.
STATUSES: tuple[str, ...] = (
    "reached",
    "never_reached",
    "overflow_before_target",
    "diverged_before_target",
    "newton_nonconvergent",
    "attempt_cap",
    "step_underflow",
    "h_q_ceiling",
    "below_bias_floor",
    "failed",
    "skipped",
)

PROBLEM_SET_NAMES: tuple[str, ...] = ("frozen", "application")

# The benchmark's own ladder cap, deliberately below secondpass.N_MAX. This table
# carries a library side beside every ours row, so it pays for the ladder twice
# over; a target that needs more than this many fixed steps comes back
# never_reached with the cap stated on the row rather than silently absent.
N_MAX_BENCH = 4096

# The library tolerance ladder, coarsest leading, which is ascending cost order:
# ladder_scan answers with the earliest rung that meets the target.
LIB_TOL_LADDER: tuple[float, ...] = (
    2.0 ** -8, 2.0 ** -11, 2.0 ** -14, 2.0 ** -17, 2.0 ** -20, 2.0 ** -23, 2.0 ** -26,
)

# The shared tolerance ladder of the pair comparison, in the problems' own
# physical units, so all four solvers are asked for the same thing.
ADAPTIVE_TOLS: tuple[float, ...] = (
    2.0 ** -4, 2.0 ** -6, 2.0 ** -8, 2.0 ** -10, 2.0 ** -12,
)

# The quarter / half / one / two / four ladder around the budgeted step count.
# sidetrack.BUDGET_LADDER, not a copy of it, so the two cannot drift.
BUDGET_LADDER: tuple[tuple[int, int], ...] = ST.BUDGET_LADDER

SCIPY_ADAPTIVE: tuple[str, ...] = ("RK23", "RK45")
SCIPY_IMPLICIT: tuple[str, ...] = ("Radau", "BDF", "LSODA")

# Which integrator is which class, in the taxonomy this project uses: RK23 and
# RK45 are explicit Runge-Kutta pairs under a step controller, so they are the
# adaptive class; Radau, BDF and LSODA solve an implicit system every step, so
# they are the implicit class. The existing adaptive_results table files all five
# under one heading, which is where the implicit library half went missing.
SCIPY_CLASS: dict[str, str] = {name: "adaptive" for name in SCIPY_ADAPTIVE}
SCIPY_CLASS.update({name: "implicit" for name in SCIPY_IMPLICIT})

# Derivative evaluations SciPy spends before its stepping loop starts (one to form
# f at t0, one inside select_initial_step), and per step ATTEMPT for the two
# fixed-stage explicit pairs. RK23 evaluates two interior stages plus the FSAL
# evaluation at the end of the attempt; RK45 evaluates five plus that one.
SCIPY_INIT_FEVALS = 2
SCIPY_FEVALS_PER_ATTEMPT: dict[str, int] = {"RK23": 3, "RK45": 6}

Q15_FULL_SCALE = 32768.0

# Re-exported so a caller states a scope without reaching through this module into
# the prototypes. The values belong to the prototypes that measure them.
TOL_LADDER_LSB: tuple[int, ...] = AQ.TOL_LADDER_LSB
MAX_ATTEMPTS: int = AQ.Q15_MAX_ATTEMPTS
NEWTON_ITERS: int = SD.NEWTON_ITERS
STIFF_NAMES: tuple[str, ...] = tuple(V.STIFF_NAMES)

# The measured floor bias of the Q15 error estimate, in LSB, used only when a run
# produced no floor statistics of its own. A rung whose tolerance sits at or under
# the bias is not being controlled to a tolerance at all.
DEFAULT_BIAS_FLOOR_LSB = 2.0

TOLERANCE_RULE_ADAPTIVE = (
    "rtol = atol = tol in the problem's own physical units for the float64 and "
    "the compiled library runs, and tol_q = max(1, round(tol * scale * 2**15)) "
    "least significant bits of the scaled state for the Q15 run. A Q15 state "
    "holds y * scale in steps of 2**-15, so one physical unit of tolerance is "
    "scale * 2**15 LSB; the clamp at 1 exists because the solver takes a whole "
    "number of LSB and raises below 1. The conversion is exact only up to the "
    "rounding, which is why every Q15 row carries tol_q_lsb beside the tol it "
    "was asked for, and carries below_bias_floor when the result sits at or "
    "under the estimate's own measured floor bias. Under that floor the Q15 "
    "tolerance is not a tolerance, and a row that did not say so would mislead."
)

TOLERANCE_RULE_MATCHED = (
    "Every solver on the tolerance ladder is asked for rtol = atol = tol, which "
    "is the convention our own solve_adaptive uses and the convention SciPy "
    "uses, so the request is the same request. This is not the same rule as the "
    "tolerance_rule that governs adaptive_results, which pins rtol and atol to "
    "one Q15 least significant bit; both rules are in this document and each "
    "table names the one it ran under."
)

MATCHED_CONDITION = (
    "matched accuracy: the achieved final-state error is at or under the target, "
    "measured for every solver on both sides by the problem set's own error "
    "metric, from the same initial state over the same window"
)

NULL_RULE = (
    "a quantity that cannot be made comparable is written as null with a reason "
    "beside it, never as a plausible figure"
)

RATIO_RULE = (
    "a ratio between two measured wall clocks is emitted only when both sides "
    "carry the same timing_family. Our runs are Python loops over tuples and "
    "SciPy's steppers are compiled; a ratio across that boundary measures "
    "CPython, not the method. Ratios of function evaluations and of achieved "
    "error are emitted across families, because neither depends on the "
    "implementation."
)

TIMING_FAMILY_DOC: dict[str, str] = {
    "python_loop": (
        "a Python loop over tuples in this interpreter: our Q15 solver, our "
        "float64 prototypes and the hand-rolled float64 rk4. Per-primitive "
        "interpreter overhead dominates, so these timings compare like against "
        "like within the family and say nothing about the target"
    ),
    "compiled_scipy": (
        "SciPy's compiled stepping loop over numpy arrays, called once per run "
        "from Python. Nothing here runs the arithmetic the cost model prices"
    ),
}

ARITHMETIC_DOC: dict[str, str] = {
    "q15": "16-bit integer arithmetic with floor (ASRS) multiply, the arithmetic "
           "the cost model prices and the target runs",
    "float64": "double precision in this interpreter; the same method as the Q15 "
               "run where both are present, so the pair says how much of the "
               "error is quantization",
    "compiled_float64": "double precision inside compiled library code",
}

COST_GRADE_DOC: dict[str, dict] = {
    "assembly_verified": {
        "what": "the pinned analytic cycle model, cross-checked line for line "
                "against fixtures/known_sequence.s",
        "direction": "exact within the model",
        "excludes": "derivative evaluations, which the pinned model excludes for "
                    "every class, and every branch",
        "source": "costmodel.cycle_count",
    },
    "design_estimate": {
        "what": "a design estimate from the EPOCH3-DESIGN solver terms rather "
                "than an assembly-verified count. It rests on "
                "sdirk.DIV_CYCLES = 32, a stated guess for a software "
                "reciprocal, because the cost model prices no divide at all",
        "direction": "estimate, unsigned",
        "excludes": "derivative and analytic-Jacobian evaluations, and every branch",
        "source": "sdirk.estimate_sdirk2_cycles",
        "caveat": "the run that produced the error is float64, while the cycle "
                  "estimate prices the same step on the Q15 target. There is no "
                  "Q15 implicit run to price: fixedpoint has no reciprocal, so "
                  "there is no Q15 linear solve",
    },
    "upper_bound_unpriced_branches": {
        "what": "an upper bound assembled from the pinned model plus the priced "
                "reference listing in adaptive_q15, charged on every attempt "
                "whether it was accepted or rejected",
        "direction": "upper bound within the model",
        "excludes": "conditional branches, so the error-magnitude bit scan is "
                    "booked at its worst case and branch_allowance rides beside "
                    "the number; derivative evaluations, so the FSAL saving shows "
                    "up as a function-evaluation count and not as cycles",
        "source": "costmodel.cycle_count plus costmodel.count_sequence over "
                  "adaptive_q15.reference_sections",
    },
    "none": {
        "what": "no cycle number at all",
        "direction": "absent",
        "excludes": "the whole cost axis. The cost model prices Q15 integer "
                    "arithmetic on a Cortex-M0+, and neither a float64 run in "
                    "this interpreter nor a compiled library integrator is that "
                    "machine",
        "source": "not priced",
    },
}


# --------------------------------------------------------------------- problem sets


def _frozen_stiffness(name: str) -> float | None:
    """The fixture's own stiffness ratio, or null where the fixture states none."""
    entry = load_fixture()[name]
    r = entry.get("stiffness_ratio")
    return float(r) if isinstance(r, (int, float)) else None


def problem_names(problem_set: str) -> tuple[str, ...]:
    if problem_set == "frozen":
        return tuple(FROZEN_PROBLEMS.keys())
    if problem_set == "application":
        return tuple(V.VALIDATION_NAMES)
    raise ValueError(f"unknown problem set {problem_set!r}")


def problem_entry(problem_set: str, name: str) -> dict:
    """One problem's descriptor, in whichever set it belongs to."""
    if problem_set == "frozen":
        p = FROZEN_PROBLEMS[name]
        return {
            "problem_set": "frozen",
            "name": name,
            "n_states": p.n_states,
            "t_end": p.t_end,
            "scale": p.scale,
            "family": p.family,
            "peak": float(load_fixture()[name]["peak"]),
            "scored": True,
            "stiff": bool(p.family == "stiff"),
            "stiffness_ratio": _frozen_stiffness(name),
        }
    p = V.PROBLEMS[name]
    return {
        "problem_set": "application",
        "name": name,
        "n_states": p.n_states,
        "t_end": p.t_end,
        "scale": p.scale,
        "family": p.family,
        "peak": float(V.PEAK[name]),
        "scored": False,
        "stiff": bool(V.STIFF[name]),
        "stiffness_ratio": float(V.STIFFNESS_RATIO[name]),
    }


def problem_sets_block() -> dict:
    """The registry both sides of every comparison are drawn from.

    The application set is not scored and does not need to be: the benchmark is
    off-archive, so it imports validation.PROBLEMS read-only and no pinned file
    moves. Putting those eight into the scored set is what would need an epoch
    boundary, and that is a different question from benchmarking on them.
    """
    return {
        "frozen": {
            "source": "rk_harness.problems.PROBLEMS (pinned)",
            "names": list(problem_names("frozen")),
            "scored": True,
            "error_metric": "problems.error_metric: L2 final-state error over "
                            "PEAK, with the two conserved-quantity problems "
                            "(pendulum energy, quaternion norm) using their own "
                            "invariant",
            "stiff": [n for n in problem_names("frozen")
                      if FROZEN_PROBLEMS[n].family == "stiff"],
            "stiffness_ratio": {n: _frozen_stiffness(n)
                                for n in problem_names("frozen")},
            "problems": [problem_entry("frozen", n) for n in problem_names("frozen")],
        },
        "application": {
            "source": "rk_harness.validation.PROBLEMS (unpinned, off-archive)",
            "names": list(problem_names("application")),
            "scored": False,
            "error_metric": "validation.validation_error: L2 final-state error "
                            "over PEAK, the default branch of the frozen metric "
                            "applied to this suite",
            "stiff": list(V.STIFF_NAMES),
            "stiffness_ratio": {n: float(V.STIFFNESS_RATIO[n])
                                for n in problem_names("application")},
            "problems": [problem_entry("application", n)
                         for n in problem_names("application")],
        },
        "why_two_sets": (
            "The seven frozen problems are the scored set and the two existing "
            "tables run on them. The stiffest of the seven is rc_thermal at a "
            "ratio of 70.1, so an implicit method has nothing there it can pay "
            "for itself on. The three stiff application problems (ratios 546.4, "
            "1030.0 and 292.0) are why the three-class tables run on the "
            "application set."
        ),
    }


def float_rhs(problem_set: str, name: str):
    if problem_set == "frozen":
        from rk_harness.problems import FLOAT_RHS
        return FLOAT_RHS[name]
    return V.FLOAT_RHS[name]


def y0_phys(problem_set: str, name: str) -> tuple[float, ...]:
    if problem_set == "frozen":
        return tuple(float(v) for v in load_fixture()[name]["y0"])
    return tuple(float(v) for v in V.Y0_PHYS[name])


def t_end(problem_set: str, name: str) -> float:
    return float((FROZEN_PROBLEMS if problem_set == "frozen" else V.PROBLEMS)[name].t_end)


def error_of(problem_set: str, name: str, y_final_phys) -> float:
    """The problem set's own final-state error metric. One metric per set, and the
    same one on both sides of every comparison in that set."""
    if problem_set == "frozen":
        return frozen_error_metric(name, tuple(float(v) for v in y_final_phys))
    return V.validation_error(name, tuple(float(v) for v in y_final_phys))


# ------------------------------------------------------------------- tolerance rule


def q15_tolerance_lsb(tol: float, scale: float) -> int:
    """A physical tolerance as a whole number of LSB of the scaled state.

    A Q15 state holds y * scale in steps of 2**-15, so one physical unit is
    scale * 2**15 LSB. Clamped at 1 because solve_adaptive_q15 takes a whole
    number of LSB and raises below one.
    """
    if not (tol > 0.0):
        raise ValueError("tol must be positive")
    if not (scale > 0.0):
        raise ValueError("scale must be positive")
    return max(1, int(round(float(tol) * float(scale) * Q15_FULL_SCALE)))


def below_bias_floor(tol_q_lsb: int, bias_lsb: float | None) -> tuple[bool, str]:
    """Whether a Q15 tolerance sits at or under the estimate's own floor bias.

    The bias is reported signed, and floor rounding biases downward, so it is the
    MAGNITUDE that sets the floor: a tolerance smaller than the systematic error
    of the estimate is not a tolerance the controller can hold.
    """
    if bias_lsb is None or not math.isfinite(bias_lsb):
        return (tol_q_lsb <= DEFAULT_BIAS_FLOOR_LSB,
                f"default floor of {DEFAULT_BIAS_FLOOR_LSB} LSB, because this "
                "run reported no floor statistics of its own")
    return (tol_q_lsb <= abs(bias_lsb),
            f"the magnitude of this run's own four-term mean floor bias, "
            f"{abs(bias_lsb):.4g} LSB")


# -------------------------------------------------------------------------- cost


def explicit_step_cycles(t: Tableau, n_states: int) -> int:
    """Cycles one fixed explicit step costs, from the pinned model."""
    from rk_harness.costmodel import cycle_count
    return int(cycle_count(t, COST_MODEL, int(n_states)))


def adaptive_attempt_cycles(n_states: int) -> dict:
    """Cycles one adaptive attempt costs, accepted or rejected.

    validation_axes owns this arithmetic (stage work through the pinned
    cycle_count, the four-term estimate scaled by the state count, one controller
    update), and it is called rather than restated so the two documents cannot
    report different per-attempt costs for the same method.
    """
    return VA.adaptive_attempt_cost(int(n_states), COST_MODEL)


def sdirk_step_cycles(n_states: int, fd: bool, newton_iters: int = SD.NEWTON_ITERS) -> dict:
    """Cycles one SDIRK2 step costs, with its term breakdown carried verbatim."""
    return VA.sdirk_step_cost(int(n_states), fd, newton_iters=int(newton_iters),
                              model=COST_MODEL)


# ------------------------------------------------------------------ solver registry


@dataclass(frozen=True)
class SolverSpec:
    """One measurable solver: what it is, whose it is, and how it is priced."""
    key: str
    cls: str
    side: str
    arithmetic: str
    control: str
    cost_grade: str
    timing_family: str | None
    family: str
    tableau: Tableau | None = None
    library_method: str | None = None
    jacobian: str | None = None
    newton_iters: int | None = None
    roles: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "solver": self.key,
            "class": self.cls,
            "side": self.side,
            "arithmetic": self.arithmetic,
            "control": self.control,
            "cost_grade": self.cost_grade,
            "timing_family": self.timing_family,
            "family": self.family,
            "library_method": self.library_method,
            "jacobian": self.jacobian,
            "newton_iters": self.newton_iters,
            "roles": list(self.roles),
        }


M_BS32_Q15 = "bs32_q15"
M_BS32_FLOAT = "bs32_float"
M_SDIRK_ANALYTIC = "sdirk2_analytic_jac"
M_SDIRK_FD = "sdirk2_fd_jac"
M_RK4_FLOAT = "rk4_float64"


def explicit_spec(name_or_hash: str, t: Tableau, roles=()) -> SolverSpec:
    """One of our fixed-step Q15 methods, priced by the pinned model."""
    return SolverSpec(
        key=name_or_hash, cls="explicit", side="ours", arithmetic="q15",
        control="fixed_step", cost_grade="assembly_verified",
        timing_family="python_loop",
        family="explicit Runge-Kutta, fixed step, Q15 integer arithmetic",
        tableau=t, roles=tuple(roles))


def rk4_float_spec() -> SolverSpec:
    """The arithmetic control for the explicit class: the same tableau in float64,
    so the gap between the two rows is the cost of 16-bit floor arithmetic rather
    than a difference of method."""
    from rk_harness.tableau import classical
    return SolverSpec(
        key=M_RK4_FLOAT, cls="explicit", side="ours", arithmetic="float64",
        control="fixed_step", cost_grade="none", timing_family="python_loop",
        family="explicit Runge-Kutta, fixed step, float64 arithmetic control",
        tableau=classical()["rk4"], roles=("arithmetic_control",))


def prototype_specs(newton_iters: int = SD.NEWTON_ITERS) -> list[SolverSpec]:
    """Our adaptive and implicit prototypes."""
    return [
        SolverSpec(
            key=M_SDIRK_ANALYTIC, cls="implicit", side="ours", arithmetic="float64",
            control="fixed_step", cost_grade="design_estimate",
            timing_family="python_loop",
            family="implicit Runge-Kutta, 2-stage L-stable SDIRK2 (Alexander 1977), "
                   "fixed step, fixed modified-Newton iteration count",
            jacobian="analytic", newton_iters=int(newton_iters)),
        SolverSpec(
            key=M_SDIRK_FD, cls="implicit", side="ours", arithmetic="float64",
            control="fixed_step", cost_grade="design_estimate",
            timing_family="python_loop",
            family="implicit Runge-Kutta, 2-stage L-stable SDIRK2 (Alexander 1977), "
                   "fixed step, one-sided finite-difference Jacobian",
            jacobian="finite_difference", newton_iters=int(newton_iters)),
        SolverSpec(
            key=M_BS32_Q15, cls="adaptive", side="ours", arithmetic="q15",
            control="tolerance", cost_grade="upper_bound_unpriced_branches",
            timing_family="python_loop",
            family="explicit embedded pair, Bogacki-Shampine 3(2) under a dyadic "
                   "PI controller, Q15 integer arithmetic"),
        SolverSpec(
            key=M_BS32_FLOAT, cls="adaptive", side="ours", arithmetic="float64",
            control="tolerance", cost_grade="none", timing_family="python_loop",
            family="explicit embedded pair, Bogacki-Shampine 3(2) under a dyadic "
                   "PI controller, float64 arithmetic control"),
    ]


LIBRARY_FAMILY: dict[str, str] = {
    "RK23": "explicit embedded pair, Bogacki-Shampine 3(2) under an I controller "
            "(this is the same tableau as our pair)",
    "RK45": "explicit embedded pair, Dormand-Prince 5(4) under an I controller",
    "Radau": "implicit Runge-Kutta, Radau IIA of order 5, adaptive",
    "BDF": "implicit multistep, variable order 1 to 5, adaptive",
    "LSODA": "switching Adams and BDF (ODEPACK), adaptive",
}


def library_specs(names: tuple[str, ...] = SCIPY_ADAPTIVE + SCIPY_IMPLICIT) -> list[SolverSpec]:
    return [
        SolverSpec(
            key=name, cls=SCIPY_CLASS[name], side="library",
            arithmetic="compiled_float64", control="tolerance", cost_grade="none",
            timing_family="compiled_scipy", family=LIBRARY_FAMILY[name],
            library_method=name)
        for name in names
    ]


# --------------------------------------------------------------- comparability text


def controls_for(spec: SolverSpec) -> list[str]:
    return [
        MATCHED_CONDITION,
        "the same problem, the same window and the same initial state in physical "
        "units",
        "the same target set, keyed the way secondpass keys it, so the benchmark "
        "and the two-axis validation document name the same points",
    ]


def not_controlled(spec: SolverSpec) -> list[str]:
    """The uncontrolled differences of one row, derived from its own fields.

    Written as data rather than as a caveat because a comparison whose
    uncontrolled differences are only in a comment is not a comparison a reader
    can check.
    """
    out: list[str] = []
    if spec.arithmetic == "q15":
        out.append("arithmetic: 16-bit integer with floor multiply, against "
                   "double precision on every other row")
    elif spec.arithmetic == "compiled_float64":
        out.append("arithmetic: double precision inside compiled library code, "
                   "against 16-bit integer on the Q15 rows")
    else:
        out.append("arithmetic: double precision in this interpreter, against "
                   "16-bit integer on the Q15 rows")
    if spec.control == "fixed_step":
        out.append("step control: a fixed step count chosen off a ladder, against "
                   "a controller choosing its own steps on the tolerance rows")
    else:
        out.append("step control: a controller choosing its own steps, against a "
                   "fixed step count on the fixed-step rows")
    if spec.timing_family == "compiled_scipy":
        out.append("implementation: a compiled stepping loop, against a Python "
                   "loop over tuples on every ours row, so wall clock does not "
                   "cross the boundary")
    else:
        out.append("implementation: a Python loop over tuples, against a compiled "
                   "stepping loop on every library row, so wall clock does not "
                   "cross the boundary")
    if spec.cost_grade == "none":
        out.append("cost: no cycle model applies to this row, so its cycle "
                   "columns are null rather than estimated")
    elif spec.cost_grade != "assembly_verified":
        out.append(f"cost: the cycle number is {spec.cost_grade}, not the "
                   "assembly-verified grade the Q15 explicit rows carry")
    if spec.cls == "implicit" and spec.side == "ours":
        out.append("the error comes from a float64 run while the cycle estimate "
                   "prices the same step on the Q15 target, because there is no "
                   "Q15 linear solve to run")
    return out


def control_unit(spec: SolverSpec) -> str:
    """What one row's control_value is counted in.

    Three rows can carry control_value 32 and mean three different things, so the
    unit rides on the row rather than being inferred from the class.
    """
    if spec.control == "fixed_step":
        return "steps"
    if spec.side == "library":
        return "rtol = atol, in the problem's physical units"
    return ("tol_q, in whole LSB of the scaled state; the float64 twin is asked "
            "for the same count converted to physical units, so the two "
            "arithmetics are asked for the same thing")


def comparability_block() -> dict:
    return {
        "shared_condition": MATCHED_CONDITION,
        "not_matched": (
            "wall clock. Ours runs Q15 integer arithmetic through a Python loop "
            "that models a Cortex-M0+; theirs runs float64 inside compiled SciPy "
            "on this desktop. A microsecond from one is not a microsecond from "
            "the other, which is why matched accuracy and not matched wall clock "
            "is the shared condition of every table here."
        ),
        "ratio_rule": RATIO_RULE,
        "null_rule": NULL_RULE,
        "timing_families": dict(TIMING_FAMILY_DOC),
        "arithmetic": dict(ARITHMETIC_DOC),
        "cost_grades": {k: dict(v) for k, v in COST_GRADE_DOC.items()},
        "class_order": list(CLASS_ORDER),
        "tolerance_rule_matched": TOLERANCE_RULE_MATCHED,
        "tolerance_rule_adaptive": TOLERANCE_RULE_ADAPTIVE,
    }


# ------------------------------------------------------------------------- helpers


def _fin(v):
    """Finite number or None; the document is written with allow_nan=False."""
    return v if isinstance(v, (int, float)) and math.isfinite(v) else None


def _ladder_rows(ladder: list[dict]) -> list[dict]:
    """ladder_scan's rungs in this document's own key names."""
    return [{"control_value": r["n"], "status": r["status"],
             "achieved_error": _fin(r.get("error"))} for r in ladder]


def _error_at(ladder: list[dict], probed: list[dict], value) -> float | None:
    for row in ladder:
        if row["n"] == value:
            return _fin(row.get("error"))
    for row in probed or ():
        if row["n"] == value:
            return _fin(row.get("error"))
    return None


def target_keys(targets=TARGETS) -> list[str]:
    return [_target_key(t) for t in targets]


def _work_cap(*, n_max=None, bisect_probes=None, max_attempts=None,
              tol_ladder=None) -> dict:
    return {
        "n_max": int(n_max) if n_max is not None else None,
        "bisect_probes": int(bisect_probes) if bisect_probes is not None else None,
        "max_attempts": int(max_attempts) if max_attempts is not None else None,
        "tol_ladder": list(tol_ladder) if tol_ladder is not None else None,
    }


def _row(spec: SolverSpec, problem_set: str, pname: str, target: float, *,
         status: str, control_value, reason=None, achieved_error=None,
         best_achieved_error=None, n_steps_accepted=None, n_steps_rejected=None,
         n_steps_rejected_basis=None, nfev=None, njev=None, nlu=None,
         nlinsolve=None, analytic_cycles_per_step=None, analytic_cycles_total=None,
         probes=0, ladder_monotone=None, ladder=(), work_cap=None,
         note=None, extra=None) -> dict:
    """One matched-accuracy row. Every key is present on every row, null where the
    class has no such quantity, so a consumer never has to know which class it is
    reading before it can index the row."""
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    info = problem_entry(problem_set, pname)
    row = {
        "class": spec.cls,
        "solver": spec.key,
        "side": spec.side,
        "arithmetic": spec.arithmetic,
        "family": spec.family,
        "problem_set": problem_set,
        "problem": pname,
        "n_states": info["n_states"],
        "stiff": info["stiff"],
        "stiffness_ratio": info["stiffness_ratio"],
        "target_error": float(target),
        "target_key": _target_key(target),
        "control": spec.control,
        "control_value": control_value,
        "control_unit": control_unit(spec),
        "status": status,
        "reason": reason,
        "achieved_error": _fin(achieved_error),
        "best_achieved_error": _fin(best_achieved_error),
        "n_steps_accepted": n_steps_accepted,
        "n_steps_rejected": n_steps_rejected,
        "n_steps_rejected_basis": n_steps_rejected_basis,
        "nfev": nfev,
        "njev": njev,
        "nlu": nlu,
        "nlinsolve": nlinsolve,
        "analytic_cycles_per_step": analytic_cycles_per_step,
        "analytic_cycles_total": analytic_cycles_total,
        "cost_grade": spec.cost_grade,
        "probes": int(probes),
        "ladder_monotone": ladder_monotone,
        "ladder": list(ladder),
        "work_cap": work_cap or _work_cap(),
        "timing": None,
        "timing_family": spec.timing_family,
        "controls_for": controls_for(spec),
        "not_controlled": not_controlled(spec),
        "note": note,
    }
    if status != "reached" and row["reason"] is None:
        row["reason"] = f"the ladder produced no rung at or under {target!r}"
    if extra:
        row.update(extra)
    return row


# --------------------------------------------------------------------- ours: rows


def explicit_matched_rows(spec: SolverSpec, problem_set: str, pname: str,
                          targets=TARGETS, *, n_max: int = N_MAX_BENCH,
                          bisect_probes: int = BISECT_PROBES) -> list[dict]:
    """Our fixed-step method on the shared power-of-two step ladder.

    The Q15 probe is validation_axes's, which is secondpass's probe scored with
    the application suite's own error metric. The float64 control runs the same
    tableau through the same ladder with the pinned float solver.
    """
    if spec.tableau is None:
        raise ValueError(f"{spec.key}: an explicit row needs a tableau")
    info = problem_entry(problem_set, pname)
    per_step = explicit_step_cycles(spec.tableau, info["n_states"])
    priced = spec.arithmetic == "q15"
    cache: dict[int, tuple] = {}
    if spec.arithmetic == "q15":
        if problem_set != "application":
            raise ValueError("the Q15 ladder is defined on the application set here")
        probe = lambda n: VA._explicit_probe(spec.tableau, pname, n, cache)
    else:
        probe = lambda n: _float_fixed_probe(spec.tableau, problem_set, pname, n, cache)
    scan = ladder_scan(probe, targets, n_max=n_max, bisect_probes=bisect_probes,
                       per_unit_cost=per_step if priced else None)
    best = _best_error(scan["ladder"])
    cap = _work_cap(n_max=n_max, bisect_probes=bisect_probes)
    rows: list[dict] = []
    stages = len(spec.tableau.b)
    for target in targets:
        e = scan["targets"][_target_key(target)]
        n = e["n"]
        rows.append(_row(
            spec, problem_set, pname, target,
            status=e["status"], control_value=n,
            achieved_error=_error_at(scan["ladder"], e["probed"], n),
            best_achieved_error=best,
            n_steps_accepted=n, n_steps_rejected=0 if n is not None else None,
            n_steps_rejected_basis="a fixed-step run rejects nothing",
            nfev=n * stages if n is not None else None,
            njev=0, nlu=0, nlinsolve=0,
            analytic_cycles_per_step=per_step if priced else None,
            analytic_cycles_total=e["cycles"] if priced else None,
            probes=e["probes"], ladder_monotone=bool(e["ladder_monotone"]),
            ladder=_ladder_rows(scan["ladder"]), work_cap=cap,
            note="the smallest step count on the sampled ladder"))
    return rows


def _float_fixed_probe(t: Tableau, problem_set: str, pname: str, n: int,
                       cache: dict[int, tuple]) -> tuple:
    """One fixed-step float64 rung, scored with the problem set's own metric."""
    hit = cache.get(n)
    if hit is not None:
        return hit
    from rk_harness.simulate import solve_float
    try:
        y = solve_float(t, float_rhs(problem_set, pname), y0_phys(problem_set, pname),
                        t_end(problem_set, pname), n)
        err = error_of(problem_set, pname, y)
        row = ("ok", float(err), None) if math.isfinite(err) else ("nonfinite", None, None)
    except (OverflowError, ZeroDivisionError, ValueError):
        row = ("overflow", None, None)
    except Exception:
        row = ("error", None, None)
    cache[n] = row
    return row


def implicit_matched_rows(spec: SolverSpec, problem_set: str, pname: str,
                          targets=TARGETS, *, n_max: int = N_MAX_BENCH,
                          bisect_probes: int = BISECT_PROBES) -> list[dict]:
    """Our SDIRK2 on the shared step ladder, with the Jacobian its spec names."""
    if problem_set != "application":
        raise ValueError("the SDIRK2 ladder is defined on the application set here")
    info = problem_entry(problem_set, pname)
    fd = spec.jacobian == "finite_difference"
    analytic = V.ANALYTIC_JACOBIAN.get(pname)
    if not fd and analytic is None:
        # No hand-derived Jacobian for this problem: say so rather than quietly
        # substituting a finite difference and labelling it analytic.
        return [_row(spec, problem_set, pname, target, status="skipped",
                     control_value=None,
                     reason="no analytic Jacobian is derived for this problem",
                     work_cap=_work_cap(n_max=n_max, bisect_probes=bisect_probes))
                for target in targets]
    jac = None if fd else analytic
    newton = int(spec.newton_iters or SD.NEWTON_ITERS)
    est = sdirk_step_cycles(info["n_states"], fd, newton)
    per_step = int(est["total"])
    sspec = SD.Sdirk2Spec(gamma=SD.GAMMA, a21=1.0 - SD.GAMMA, b=SD._B, c=SD._C,
                          newton_iters=newton)
    bound = VA.diverge_bound(pname)
    cache: dict[int, tuple] = {}
    scan = ladder_scan(lambda n: VA._implicit_probe(pname, n, jac, sspec, bound, cache),
                       targets, n_max=n_max, bisect_probes=bisect_probes,
                       per_unit_cost=per_step)
    best = _best_error(scan["ladder"])
    cap = _work_cap(n_max=n_max, bisect_probes=bisect_probes)
    rows: list[dict] = []
    for target in targets:
        e = scan["targets"][_target_key(target)]
        n = e["n"]
        status = VA._implicit_target_status(e["status"], scan["ladder"])
        if status == "overflow_before_target":
            # The bound is on the data, not on a Q15 register: an implicit run
            # that passes it diverged, it did not overflow an int16.
            status = "diverged_before_target"
        rows.append(_row(
            spec, problem_set, pname, target,
            status=status, control_value=n,
            achieved_error=_error_at(scan["ladder"], e["probed"], n),
            best_achieved_error=best,
            n_steps_accepted=n, n_steps_rejected=0 if n is not None else None,
            n_steps_rejected_basis="a fixed-step run rejects nothing",
            nfev=n * int(est["f_evals_per_step"]) if n is not None else None,
            njev=n, nlu=n,
            nlinsolve=n * SD.STAGES * newton if n is not None else None,
            analytic_cycles_per_step=per_step, analytic_cycles_total=e["cycles"],
            probes=e["probes"], ladder_monotone=bool(e["ladder_monotone"]),
            ladder=_ladder_rows(scan["ladder"]), work_cap=cap,
            note="the smallest step count on the sampled ladder",
            extra={"cycle_terms": dict(est["terms"]),
                   "jacobian": spec.jacobian,
                   "newton_iters": newton,
                   "divergence_bound": bound}))
    return rows


def adaptive_matched_rows(spec: SolverSpec, problem_set: str, pname: str,
                          targets=TARGETS, *,
                          tol_ladder: tuple[int, ...] = AQ.TOL_LADDER_LSB,
                          max_attempts: int = AQ.Q15_MAX_ATTEMPTS) -> list[dict]:
    """Our embedded pair on the tolerance ladder, placed by ACHIEVED error.

    The Q15 tolerance is in LSB of the scaled state and the metric is L2
    final-state error over PEAK; there is no analytic conversion between them, so
    none is attempted. The row for a target is the coarsest rung whose achieved
    error is at or under it, which is secondpass's own rule read on a tolerance
    ladder. The rungs are whole LSB with nothing between them, so no bisection is
    taken and probes is 0 rather than a fiction.
    """
    if problem_set != "application":
        raise ValueError("the adaptive ladder is defined on the application set here")
    info = problem_entry(problem_set, pname)
    arith = "q15" if spec.arithmetic == "q15" else "float64"
    priced = spec.cost_grade == "upper_bound_unpriced_branches"
    per_attempt = adaptive_attempt_cycles(info["n_states"]) if priced else None
    ladder = VA.descending_ladder(tol_ladder)
    cache: dict[int, dict] = {}
    scan = ladder_scan(lambda q: VA._adaptive_probe(pname, q, arith, max_attempts, cache),
                       targets, unit_ladder=ladder, bisect=False, per_unit_cost=None)
    rungs = [cache[q] for q in ladder if q in cache]
    best = _best_error([{"status": ("ok" if r.get("status") == "ok" else "bad"),
                         "error": r.get("achieved_error")} for r in rungs])
    cap = _work_cap(max_attempts=max_attempts, tol_ladder=list(ladder))
    rows: list[dict] = []
    for target in targets:
        e = scan["targets"][_target_key(target)]
        tol_q = e["n"]
        res = cache.get(tol_q) if tol_q is not None else None
        status = VA._adaptive_target_status(e["status"], rungs, float(target), None)
        attempts = int(res.get("attempts") or 0) if res else None
        n_acc = int(res.get("n_accepted") or 0) if res else None
        n_rej = int(res.get("n_rejected") or 0) if res else None
        total = attempts * int(per_attempt["total"]) if (res and per_attempt) else None
        rows.append(_row(
            spec, problem_set, pname, target,
            status=status, control_value=tol_q,
            achieved_error=res.get("achieved_error") if res else None,
            best_achieved_error=best,
            n_steps_accepted=n_acc, n_steps_rejected=n_rej,
            n_steps_rejected_basis="counted by the solver",
            nfev=int(res["n_fevals"]) if res and res.get("n_fevals") is not None else None,
            njev=0, nlu=0, nlinsolve=0,
            analytic_cycles_per_step=None, analytic_cycles_total=total,
            probes=0, ladder_monotone=bool(e["ladder_monotone"]),
            ladder=_ladder_rows(scan["ladder"]), work_cap=cap,
            note="the coarsest rung on the sampled ladder",
            extra={"attempts": attempts,
                   "cycles_accepted_only":
                       n_acc * int(per_attempt["total"]) if (res and per_attempt) else None,
                   "per_attempt_cycles": dict(per_attempt) if per_attempt else None,
                   "branch_allowance":
                       int(per_attempt["branch_allowance"]) if per_attempt else None,
                   "fevals_saved_by_fsal": n_acc,
                   "h_q_final": res.get("h_q_final") if res else None,
                   "h_q_clamped_high": res.get("h_q_clamped_high") if res else None,
                   "overflow_rejected": res.get("overflow_rejected") if res else None}))
    return rows


def _best_error(ladder: list[dict]) -> float | None:
    vals = [r["error"] for r in ladder
            if r.get("status") == "ok" and isinstance(r.get("error"), (int, float))
            and math.isfinite(r["error"])]
    return min(vals) if vals else None


# ------------------------------------------------------------------ library: rows


def scipy_available() -> tuple[bool, str | None]:
    try:
        from scipy.integrate import solve_ivp  # noqa: F401
        return True, None
    except Exception as exc:                    # pragma: no cover - venv without scipy
        return False, f"{type(exc).__name__}: {exc}"


def implied_attempts(integrator: str, nfev) -> tuple[int | None, str]:
    """Step ATTEMPTS from the function-evaluation count, where that is exact.

    RK23 and RK45 spend a fixed number of derivative evaluations per attempt
    (two interior stages plus the trailing FSAL evaluation, and five plus that
    one), after two evaluations spent before the loop starts. So the attempt
    count, and with it the rejected-step count SciPy does not report, is
    recoverable exactly. It is refused rather than rounded when the arithmetic
    does not come out whole, and it is not available at all for the variable-cost
    integrators.
    """
    k = SCIPY_FEVALS_PER_ATTEMPT.get(integrator)
    if k is None:
        return None, ("SciPy reports no rejected-step count, and this integrator "
                      "does not spend a fixed number of derivative evaluations "
                      "per attempt, so the count cannot be recovered")
    if not isinstance(nfev, int):
        return None, "no function-evaluation count to derive attempts from"
    rem = nfev - SCIPY_INIT_FEVALS
    if rem < 0 or rem % k != 0:
        return None, (f"the function-evaluation count {nfev} is not "
                      f"{SCIPY_INIT_FEVALS} plus a whole multiple of {k}, so the "
                      "attempt count would be a rounding rather than a measurement")
    return rem // k, (f"derived exactly: ({nfev} function evaluations minus "
                      f"{SCIPY_INIT_FEVALS} spent before the loop) divided by "
                      f"{k} per attempt, minus the accepted steps")


def library_run(integrator: str, problem_set: str, pname: str, tol: float) -> dict:
    """One library run at rtol = atol = tol, with its counters and nothing timed."""
    ok, why = scipy_available()
    out: dict = {"integrator": integrator, "problem": pname, "tol": float(tol),
                 "rtol": float(tol), "atol": float(tol)}
    if not ok:
        out.update({"status": "skipped", "reason": f"scipy unavailable: {why}"})
        return out
    from scipy.integrate import solve_ivp
    rhs = float_rhs(problem_set, pname)
    y0 = y0_phys(problem_set, pname)
    end = t_end(problem_set, pname)
    try:
        sol = solve_ivp(rhs, (0.0, end), y0, method=integrator,
                        rtol=float(tol), atol=float(tol))
    except Exception as exc:
        out.update({"status": "failed", "reason": f"{type(exc).__name__}: {exc}"})
        return out
    if not sol.success:
        out.update({"status": "failed", "reason": str(sol.message)})
        return out
    err = _fin(error_of(problem_set, pname, tuple(float(v) for v in sol.y[:, -1])))
    n_acc = int(len(sol.t) - 1)
    nfev = int(sol.nfev)
    attempts, basis = implied_attempts(integrator, nfev)
    n_rej = (attempts - n_acc) if attempts is not None else None
    if n_rej is not None and n_rej < 0:
        n_rej, attempts = None, None
        basis = ("the derived attempt count came out below the accepted-step "
                 "count, so it is not a measurement of anything")
    out.update({
        "status": "ok" if err is not None else "failed",
        "achieved_error": err,
        "n_steps_accepted": n_acc,
        "n_steps_rejected": n_rej,
        "n_steps_rejected_basis": basis,
        "attempts": attempts,
        "nfev": nfev,
        "njev": int(sol.njev),
        "nlu": int(sol.nlu),
    })
    if err is None:
        out["reason"] = "the error metric did not produce a finite number"
    return out


def _library_probe(integrator: str, problem_set: str, pname: str, tol: float,
                   cache: dict[float, dict]) -> tuple:
    hit = cache.get(tol)
    if hit is None:
        hit = library_run(integrator, problem_set, pname, tol)
        cache[tol] = hit
    if hit.get("status") == "ok" and hit.get("achieved_error") is not None:
        return ("ok", float(hit["achieved_error"]), None)
    if hit.get("status") == "skipped":
        return ("error", None, None)
    return ("error", None, None)


def library_matched_rows(spec: SolverSpec, problem_set: str, pname: str,
                         targets=TARGETS, *,
                         tol_ladder: tuple[float, ...] = LIB_TOL_LADDER) -> list[dict]:
    """One library integrator on the tolerance ladder, placed by ACHIEVED error.

    Same rule as our own adaptive rows: the answer for a target is the coarsest
    tolerance whose achieved error is at or under it, read on the same metric.
    Asking two solvers for the same tolerance does not put them at the same
    accuracy, which is exactly why matched accuracy and not matched tolerance is
    the shared condition of this table.
    """
    ok, why = scipy_available()
    cap = _work_cap(tol_ladder=list(tol_ladder))
    if not ok:
        return [_row(spec, problem_set, pname, target, status="skipped",
                     control_value=None, reason=f"scipy unavailable: {why}",
                     work_cap=cap)
                for target in targets]
    cache: dict[float, dict] = {}
    scan = ladder_scan(
        lambda tol: _library_probe(spec.key, problem_set, pname, tol, cache),
        targets, unit_ladder=list(tol_ladder), bisect=False, per_unit_cost=None)
    rungs = [cache[t] for t in tol_ladder if t in cache]
    best = _best_error([{"status": ("ok" if r.get("status") == "ok" else "bad"),
                         "error": r.get("achieved_error")} for r in rungs])
    failed = [r for r in rungs if r.get("status") == "failed"]
    rows: list[dict] = []
    for target in targets:
        e = scan["targets"][_target_key(target)]
        tol = e["n"]
        res = cache.get(tol) if tol is not None else None
        status = e["status"]
        reason = None
        if status != "reached":
            status = "never_reached"
            if failed and len(failed) == len(rungs):
                status = "failed"
                reason = str(failed[0].get("reason"))
        rows.append(_row(
            spec, problem_set, pname, target,
            status=status, control_value=tol, reason=reason,
            achieved_error=res.get("achieved_error") if res else None,
            best_achieved_error=best,
            n_steps_accepted=res.get("n_steps_accepted") if res else None,
            n_steps_rejected=res.get("n_steps_rejected") if res else None,
            n_steps_rejected_basis=(res.get("n_steps_rejected_basis") if res
                                    else implied_attempts(spec.key, None)[1]),
            nfev=res.get("nfev") if res else None,
            njev=res.get("njev") if res else None,
            nlu=res.get("nlu") if res else None,
            nlinsolve=None,
            analytic_cycles_per_step=None, analytic_cycles_total=None,
            probes=0, ladder_monotone=bool(e["ladder_monotone"]),
            ladder=_ladder_rows(scan["ladder"]), work_cap=cap,
            note="the coarsest tolerance on the sampled ladder",
            extra={"attempts": res.get("attempts") if res else None,
                   "rtol": tol, "atol": tol,
                   "nlinsolve_basis": "SciPy reports no linear-solve count"}))
    return rows


def matched_rows_for(spec: SolverSpec, problem_set: str, pname: str,
                     targets=TARGETS, **kw) -> list[dict]:
    """One solver on one problem, dispatched on the spec rather than on a name.

    The two tolerance ladders are named apart on purpose: ``tol_ladder`` is in
    whole LSB of the scaled state and belongs to our own pair, ``lib_tol_ladder``
    is in physical units and belongs to the library side. One name for both would
    hand a library integrator a tolerance of 512.
    """
    if spec.side == "library":
        lib = kw.get("lib_tol_ladder")
        return library_matched_rows(spec, problem_set, pname, targets,
                                    **({"tol_ladder": lib} if lib is not None else {}))
    if spec.cls == "explicit":
        return explicit_matched_rows(spec, problem_set, pname, targets,
                                     **{k: v for k, v in kw.items()
                                        if k in ("n_max", "bisect_probes")})
    if spec.cls == "implicit":
        return implicit_matched_rows(spec, problem_set, pname, targets,
                                     **{k: v for k, v in kw.items()
                                        if k in ("n_max", "bisect_probes")})
    return adaptive_matched_rows(spec, problem_set, pname, targets,
                                 **{k: v for k, v in kw.items()
                                    if k in ("tol_ladder", "max_attempts")})


def build_matched_accuracy(specs: list[SolverSpec], problem_set: str,
                           problems: tuple[str, ...] | None = None,
                           targets=TARGETS, **kw) -> list[dict]:
    """The full (solver, problem, target) cross product, in a fixed order."""
    names = tuple(problems) if problems is not None else problem_names(problem_set)
    rows: list[dict] = []
    for cls in CLASS_ORDER:
        for spec in [s for s in specs if s.cls == cls]:
            for pname in names:
                rows.extend(matched_rows_for(spec, problem_set, pname, targets, **kw))
    return rows


# ------------------------------------------------- adaptive at matched tolerance


def adaptive_tolerance_row(spec: SolverSpec, problem_set: str, pname: str,
                           tol: float, *,
                           max_attempts: int = AQ.Q15_MAX_ATTEMPTS) -> dict:
    """One (solver, problem, tolerance) cell of the pair comparison.

    This is the one condition in the document under which our side and the
    library side are asked for the same thing and the request means the same
    thing: both accept a step when the scaled error norm is at or under 1.
    """
    info = problem_entry(problem_set, pname)
    row: dict = {
        "solver": spec.key,
        "class": spec.cls,
        "side": spec.side,
        "arithmetic": spec.arithmetic,
        "family": spec.family,
        "problem_set": problem_set,
        "problem": pname,
        "n_states": info["n_states"],
        "stiff": info["stiff"],
        "stiffness_ratio": info["stiffness_ratio"],
        "tol": float(tol),
        "rtol": float(tol),
        "atol": float(tol),
        "scale": info["scale"],
        "tol_q_lsb": None,
        "below_bias_floor": None,
        "bias_floor_basis": None,
        "status": "skipped",
        "reason": None,
        "achieved_error": None,
        "n_steps_accepted": None,
        "n_steps_rejected": None,
        "n_steps_rejected_basis": None,
        "attempts": None,
        "n_fevals": None,
        "h_final": None,
        "h_q_final": None,
        "overflow_rejected": None,
        "analytic_cycles_total": None,
        "cost_grade": spec.cost_grade,
        "timing": None,
        "timing_family": spec.timing_family,
        "controls_for": [
            "matched tolerance: rtol = atol = tol, and both sides accept a step "
            "when the scaled error norm is at or under 1",
            "the same problem, window and initial state",
        ],
        "not_controlled": not_controlled(spec) + [
            "the step controller: our dyadic PI with alpha 1/4, beta 1/8, safety "
            "7/8 and a clamp of [1/4, 2], against SciPy's I controller with "
            "safety 0.9 and a clamp of [0.2, 10]",
            "the initial step: ours is t_end / 64, SciPy runs its own "
            "select_initial_step",
        ],
    }
    if spec.side == "library":
        res = library_run(spec.key, problem_set, pname, tol)
        row.update({
            "status": res.get("status", "failed"),
            "reason": res.get("reason"),
            "achieved_error": _fin(res.get("achieved_error")),
            "n_steps_accepted": res.get("n_steps_accepted"),
            "n_steps_rejected": res.get("n_steps_rejected"),
            "n_steps_rejected_basis": res.get("n_steps_rejected_basis"),
            "attempts": res.get("attempts"),
            "n_fevals": res.get("nfev"),
        })
        return row

    if problem_set != "application":
        raise ValueError("our adaptive prototypes take application problem names")
    if spec.arithmetic == "q15":
        tol_q = q15_tolerance_lsb(tol, info["scale"])
        res = AQ.solve_adaptive_q15(pname, info["scale"], tol_q, arithmetic="q15",
                                    max_attempts=max_attempts)
        bias = res.get("mean_bias_lsb_total")
        flag, basis = below_bias_floor(tol_q, bias if isinstance(bias, (int, float)) else None)
        per_attempt = adaptive_attempt_cycles(info["n_states"])
        attempts = int(res.get("attempts") or 0)
        row.update({
            "tol_q_lsb": tol_q,
            "below_bias_floor": bool(flag),
            "bias_floor_basis": basis,
            "mean_bias_lsb_total": _fin(bias),
            "status": res.get("status", "failed"),
            "achieved_error": _fin(res.get("achieved_error")),
            "n_steps_accepted": res.get("n_accepted"),
            "n_steps_rejected": res.get("n_rejected"),
            "n_steps_rejected_basis": "counted by the solver",
            "attempts": attempts,
            "n_fevals": res.get("n_fevals"),
            "h_q_final": res.get("h_q_final"),
            "h_q_clamped_high": res.get("h_q_clamped_high"),
            "overflow_rejected": res.get("overflow_rejected"),
            "analytic_cycles_total": attempts * int(per_attempt["total"]),
            "cycles_accepted_only": int(res.get("n_accepted") or 0) * int(per_attempt["total"]),
            "per_attempt_cycles": dict(per_attempt),
            "branch_allowance": int(per_attempt["branch_allowance"]),
            "fevals_saved_by_fsal": res.get("n_accepted"),
        })
        if row["status"] != "ok" and row["reason"] is None:
            row["reason"] = f"the Q15 run ended with status {row['status']!r}"
        return row

    res = AQ.solve_adaptive_q15(pname, info["scale"], float(tol), arithmetic="float",
                               max_attempts=max_attempts)
    row.update({
        "status": res.get("status", "failed"),
        "achieved_error": _fin(res.get("achieved_error")),
        "n_steps_accepted": res.get("n_accepted"),
        "n_steps_rejected": res.get("n_rejected"),
        "n_steps_rejected_basis": "counted by the solver",
        "attempts": res.get("attempts"),
        "n_fevals": res.get("n_fevals"),
    })
    if row["status"] != "ok" and row["reason"] is None:
        row["reason"] = f"the float64 run ended with status {row['status']!r}"
    return row


PAIR_SOLVER_ORDER: tuple[str, ...] = (M_BS32_Q15, M_BS32_FLOAT, "RK23", "RK45")


def build_adaptive_matched_tolerance(problem_set: str = "application",
                                     problems: tuple[str, ...] | None = None,
                                     tols: tuple[float, ...] = ADAPTIVE_TOLS,
                                     *, max_attempts: int = AQ.Q15_MAX_ATTEMPTS,
                                     ) -> list[dict]:
    names = tuple(problems) if problems is not None else problem_names(problem_set)
    specs = {s.key: s for s in prototype_specs() + library_specs(SCIPY_ADAPTIVE)}
    rows: list[dict] = []
    for pname in names:
        for tol in tols:
            for key in PAIR_SOLVER_ORDER:
                rows.append(adaptive_tolerance_row(specs[key], problem_set, pname,
                                                   tol, max_attempts=max_attempts))
    return rows


SAME_PAIR_NOTE = (
    "SciPy RK23 is Bogacki-Shampine 3(2), which is the pair our prototype runs. "
    "The tableau is identical, so every difference in this entry is the step "
    "controller and the arithmetic. That is the tightest honest comparison "
    "available in this project: nothing else here holds the method constant."
)

RK45_NOTE = (
    "RK45 is Dormand-Prince 5(4), a different and higher-order pair, so its row "
    "is context rather than a controlled comparison."
)


def build_adaptive_pairs(tol_rows: list[dict]) -> list[dict]:
    """The four solvers side by side per (problem, tolerance), with the two ratios
    that are legitimate across a family boundary and no ratio that is not."""
    by_cell: dict[tuple[str, float], dict[str, dict]] = {}
    order: list[tuple[str, float]] = []
    for r in tol_rows:
        key = (r["problem"], float(r["tol"]))
        if key not in by_cell:
            by_cell[key] = {}
            order.append(key)
        by_cell[key][r["solver"]] = r
    out: list[dict] = []
    for pname, tol in order:
        cell = by_cell[(pname, tol)]
        solvers: dict[str, dict] = {}
        for key in PAIR_SOLVER_ORDER:
            r = cell.get(key)
            if r is None:
                continue
            acc = r.get("n_steps_accepted")
            rej = r.get("n_steps_rejected")
            rate = None
            if isinstance(acc, int) and isinstance(rej, int) and (acc + rej) > 0:
                rate = rej / (acc + rej)
            solvers[key] = {
                "status": r["status"],
                "arithmetic": r["arithmetic"],
                "timing_family": r["timing_family"],
                "achieved_error": r["achieved_error"],
                "n_fevals": r.get("n_fevals"),
                "n_steps_accepted": acc,
                "n_steps_rejected": rej,
                "rejection_rate": rate,
                "rejection_rate_basis": r.get("n_steps_rejected_basis"),
                "tol_q_lsb": r.get("tol_q_lsb"),
                "below_bias_floor": r.get("below_bias_floor"),
            }
        rk23 = solvers.get("RK23")
        entry = {
            "problem": pname,
            "tol": tol,
            "same_pair": True,
            "same_pair_note": SAME_PAIR_NOTE,
            "rk45_note": RK45_NOTE,
            "ours_solver": M_BS32_Q15,
            "float_twin": M_BS32_FLOAT,
            "counterpart": "RK23",
            "solvers": solvers,
            "fevals_ratio_ours_over_rk23": _ratio(
                solvers.get(M_BS32_Q15, {}).get("n_fevals"),
                (rk23 or {}).get("n_fevals")),
            "fevals_ratio_float_twin_over_rk23": _ratio(
                solvers.get(M_BS32_FLOAT, {}).get("n_fevals"),
                (rk23 or {}).get("n_fevals")),
            "error_ratio_ours_over_rk23": _ratio(
                solvers.get(M_BS32_Q15, {}).get("achieved_error"),
                (rk23 or {}).get("achieved_error")),
            "error_ratio_float_twin_over_rk23": _ratio(
                solvers.get(M_BS32_FLOAT, {}).get("achieved_error"),
                (rk23 or {}).get("achieved_error")),
            "time_ratio_ours_over_rk23": None,
            "time_ratio_reason": RATIO_RULE,
            "ratios_are": (
                "function evaluations and achieved error only. Both are "
                "implementation-independent, so they cross the boundary between "
                "a Python loop and compiled library code that a wall-clock ratio "
                "must not."
            ),
        }
        out.append(entry)
    return out


def _ratio(a, b):
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return None
    if not (math.isfinite(a) and math.isfinite(b)) or b == 0:
        return None
    return _fin(a / b)


# ---------------------------------------------------------------- implicit budget


IMPLICIT_BUDGET_WHY_NO_LIBRARY = (
    "There is no library column here and there should not be one. This section "
    "asks what a fixed Cortex-M0+ cycle budget buys, which needs a cycle model; "
    "SciPy has none, and pricing a compiled adaptive integrator under our model "
    "would be inventing a number. The library counterparts are compared against "
    "our SDIRK2 in matched_accuracy, on achieved error, where both sides can be "
    "asked the same question."
)


def implicit_budget(problems: tuple[str, ...] | None = None, *,
                    newton_iters: int = SD.NEWTON_ITERS,
                    budget_cycles: int = BUDGET_CYCLES) -> dict:
    """Our SDIRK2's step count at the shared cycle budget on the stiff problems,
    the error it reaches there, and the quarter / half / one / two / four ladder
    around it.

    Recomputed here rather than cited from the side-track artifact, but through
    sidetrack's own divergence bound and fixed-step row, so the two documents
    measure with one path and a test can hold them equal cell for cell.
    """
    names = tuple(problems) if problems is not None else tuple(V.STIFF_NAMES)
    out: dict = {}
    for pname in names:
        n_states = V.PROBLEMS[pname].n_states
        est = sdirk_step_cycles(n_states, True, newton_iters)
        per_step = int(est["total"])
        n_budget = budget_cycles // per_step
        bound = ST._divergence_bound(pname)
        ladder = sorted({max(1, n_budget * num // den) for num, den in BUDGET_LADDER})
        rows = []
        for n in ladder:
            r = ST._fixed_step_row("sdirk2", pname, n, bound)
            rows.append({"n": r["n"], "status": r["status"],
                         "error": _fin(r.get("error")),
                         "analytic_cycles": r["n"] * per_step,
                         "factor": _factor_label(r["n"], n_budget)})
        at_budget = next((r for r in rows if r["n"] == max(1, n_budget)), None)
        out[pname] = {
            "problem": pname,
            "n_states": n_states,
            "stiff": True,
            "stiffness_ratio": float(V.STIFFNESS_RATIO[pname]),
            "budget_cycles": int(budget_cycles),
            "cycles_per_step": per_step,
            "cycle_terms": dict(est["terms"]),
            "f_evals_per_step": int(est["f_evals_per_step"]),
            "steps_at_budget": int(n_budget),
            "error_at_budget": at_budget["error"] if at_budget else None,
            "status_at_budget": at_budget["status"] if at_budget else None,
            "ladder": rows,
            "jacobian": "finite_difference",
            "newton_iters": int(newton_iters),
            "arithmetic": "float64",
            "cost_grade": "design_estimate",
            "divergence_bound": bound,
            "library_column": None,
            "why_no_library": IMPLICIT_BUDGET_WHY_NO_LIBRARY,
        }
    return out


def _factor_label(n: int, n_budget: int) -> str:
    for num, den in BUDGET_LADDER:
        if max(1, n_budget * num // den) == n:
            return f"{num}/{den}"
    return "unlisted"


# ------------------------------------------------------------------ classes block


CLASS_STANDING: dict[str, str] = {
    "explicit": "scored in the archive",
    "implicit": "prototype, off-archive",
    "adaptive": "prototype, off-archive",
}

CLASS_LIBRARY_ABSENT: dict[str, str] = {
    "explicit": (
        "SciPy exposes no fixed-step integrator, so there is no library "
        "counterpart to run at a matched step count. The closest library code is "
        "an explicit Runge-Kutta pair under a step controller, which is measured "
        "in the adaptive class. The float64 rk4 row is the arithmetic control "
        "for this class and it is ours, not a library."
    ),
}


def classes_block(specs: list[SolverSpec], matched: list[dict],
                  tol_rows: list[dict], budget: dict) -> dict:
    """Per class: whose methods, whose counterparts, what was matched, and how
    many rows each side actually contributed. A class that vanished from a
    rebuild shows up here as a zero rather than as silence."""
    out: dict = {}
    for cls in CLASS_ORDER:
        ours = sorted({s.key for s in specs if s.cls == cls and s.side == "ours"})
        theirs = sorted({s.key for s in specs if s.cls == cls and s.side == "library"})
        rows = [r for r in matched if r["class"] == cls]
        conditions = ["matched accuracy (matched_accuracy)"]
        if cls == "adaptive":
            conditions.append("matched tolerance (adaptive_matched_tolerance and "
                              "adaptive_pairs)")
        if cls == "implicit":
            conditions.append("matched cycle budget, our side only (implicit_budget)")
        if cls == "explicit":
            conditions.append("identical step counts at the shared cycle budget "
                              "(fixed_step_results)")
        entry = {
            "our_methods": ours,
            "counterparts": theirs,
            "matched_conditions": conditions,
            "arithmetic": sorted({s.arithmetic for s in specs if s.cls == cls}),
            "problem_sets": sorted({r["problem_set"] for r in rows}),
            "n_rows": {
                "matched_accuracy": len(rows),
                "matched_accuracy_ours": sum(1 for r in rows if r["side"] == "ours"),
                "matched_accuracy_library": sum(1 for r in rows if r["side"] == "library"),
                "matched_accuracy_reached": sum(1 for r in rows if r["status"] == "reached"),
                "adaptive_matched_tolerance": sum(1 for r in tol_rows
                                                  if r["class"] == cls),
                "implicit_budget": len(budget) if cls == "implicit" else 0,
            },
            "cost_grades": sorted({s.cost_grade for s in specs if s.cls == cls}),
            "standing": CLASS_STANDING[cls],
        }
        if not theirs:
            entry["library_absent_reason"] = CLASS_LIBRARY_ABSENT[cls]
        out[cls] = entry
    return out


# ------------------------------------------------------------------------ replay


def replay_callable(spec: SolverSpec, row: dict) -> Callable[[], object] | None:
    """A zero-argument re-run of exactly the configuration a row selected, for the
    caller to time, or None when the row selected nothing.

    Timing is populated only on the rung the ladder chose and on the
    matched-tolerance rows. Fifteen repeats on every rung of every ladder would
    be hours of wall clock for numbers no reader would use.
    """
    value = row.get("control_value")
    if value is None or row.get("status") not in ("reached", "ok"):
        return None
    pset = row.get("problem_set", "application")
    pname = row["problem"]
    if spec.side == "library":
        return lambda: library_run(spec.key, pset, pname, float(value))
    if spec.cls == "explicit":
        n = int(value)
        if spec.arithmetic == "q15":
            from rk_harness.simulate import solve_q15
            p = V.PROBLEMS[pname] if pset == "application" else FROZEN_PROBLEMS[pname]
            return lambda: solve_q15(spec.tableau, p, n)
        from rk_harness.simulate import solve_float
        rhs, y0, end = (float_rhs(pset, pname), y0_phys(pset, pname), t_end(pset, pname))
        return lambda: solve_float(spec.tableau, rhs, y0, end, n)
    if spec.cls == "implicit":
        n = int(value)
        fd = spec.jacobian == "finite_difference"
        jac = None if fd else V.ANALYTIC_JACOBIAN.get(pname)
        newton = int(spec.newton_iters or SD.NEWTON_ITERS)
        sspec = SD.Sdirk2Spec(gamma=SD.GAMMA, a21=1.0 - SD.GAMMA, b=SD._B, c=SD._C,
                              newton_iters=newton)
        bound = VA.diverge_bound(pname)
        return lambda: SD.solve_sdirk2(V.FLOAT_RHS[pname], V.Y0_PHYS[pname],
                                       V.PROBLEMS[pname].t_end, n, jac=jac,
                                       spec=sspec, diverge_at=bound)
    scale = V.SCALE[pname]
    if spec.arithmetic == "q15":
        q = int(value)
        return lambda: AQ.solve_adaptive_q15(pname, scale, q, arithmetic="q15")
    tol = float(value)
    return lambda: AQ.solve_adaptive_q15(pname, scale, tol, arithmetic="float")


def tolerance_replay_callable(spec: SolverSpec, row: dict) -> Callable[[], object] | None:
    """The same, for an adaptive_matched_tolerance row."""
    if row.get("status") != "ok":
        return None
    pname = row["problem"]
    pset = row.get("problem_set", "application")
    if spec.side == "library":
        tol = float(row["tol"])
        return lambda: library_run(spec.key, pset, pname, tol)
    scale = float(row["scale"])
    if spec.arithmetic == "q15":
        q = int(row["tol_q_lsb"])
        return lambda: AQ.solve_adaptive_q15(pname, scale, q, arithmetic="q15")
    tol = float(row["tol"])
    return lambda: AQ.solve_adaptive_q15(pname, scale, tol, arithmetic="float")
