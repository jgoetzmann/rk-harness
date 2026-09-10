"""Open-ended search for the adaptive and implicit lanes, into parallel UNPINNED
archives. Nothing pinned is modified; ``runner._run_lane_cycle`` calls ``step``
once per adaptive or implicit cycle, under the lane time budget (D37, D38).

WHY IT EXISTS. The explicit search has CMA-ES: a continuous space that never runs
out, so a cycle can hand it any budget and get work back. The adaptive and
implicit tracks have ``sidetrack.JOBS``, a finite catalogue of 103 points that has
been fully measured since cycle 2540; every firing since then has logged
``sidetrack_exhausted`` in about five hundredths of a second. Giving those two
lanes a third of the machine before they have open-ended work would hand them a
third of the clock and an empty queue. This module is the queue: a deterministic,
unbounded enumeration per lane, an evaluation on the shared axis-T metric, and an
archive of what came back.

WHAT THESE ARCHIVES ARE NOT, and this is the part to read before quoting a number
out of one. They are UNPINNED and OFF THE SCORED PATH, and a record here is NOT
comparable with a record in ``rk-work/archive/``:

* different metric. The scored archive scores error at a fixed 65,536-cycle
  budget through ``evaluator.py``. A record here is cycles-to-tolerance on the
  axis-T ladder, which is the opposite question.
* different problems. The scored archive runs the seven frozen problems in
  ``problems.py``. This runs the eight application problems in ``validation.py``,
  under ``validation.validation_error``.
* different arithmetic. Every run here is float64. The adaptive lane's cycle
  number is the Q15 cost model applied to an attempt count measured in float64,
  because ``adaptive_q15.solve_adaptive_q15`` is hard-wired to the BS32 pair and
  cannot run a candidate pair at all; the implicit lane has no Q15 run available
  because ``fixedpoint.py`` has no reciprocal and so there is no Q15 LU.
* different verification. Nothing here goes through ``verifier.py``. No candidate
  is order-verified against the pinned checker, no candidate holds an archive
  cell, and no candidate has a ``Score``.

Both facts ride on every record and on the elites document in a FIELD, not in a
comment, because two archives that look alike and mean different things is exactly
how a published number goes wrong.

THE TWO SPACES.

ADAPTIVE, an embedded pair plus a controller. The pair comes from the same dyadic
lattice ``pair_census`` counts over: a strictly lower-triangular A of dyadic
entries, c the row sums, b the order-3 particular solution of
``orderconditions.b_linear_system``, b_hat the order-2 particular solution. A
candidate is admitted when both systems are consistent, the order-2 system has a
free column so that d = b - b_hat is not forced to zero, and the estimate is
genuinely lower order than the propagated formula. Exact CoeffRep representability
is NOT required and could not be: an order-3 b carries a denominator with a factor
of three unless c cancels it, and BS32's own b = (2/9, 1/3, 4/9, 0) is not dyadic
either. It is recorded per candidate as ``b_exact`` instead of used as a filter.
The controller is (alpha, beta, safety) over a dyadic set, passed to
``adaptive.solve_adaptive``. The factor clamp and the Q15 factor table are carried
as fixed values rather than searched, because ``solve_adaptive`` does not take
them and this module does not edit the prototype.

IMPLICIT, an SDIRK2 tableau. (gamma, a21) over a dyadic grid, then
``sdirk.order2_tableau_exact_a21`` solves b and c exactly over Fractions and
returns the exact stability data with them, times a Newton iteration count and a
Jacobian policy. One honest fact falls out of that construction and is stated on
every record: ``l_stable`` is FALSE for every dyadic gamma, because R at infinity
vanishing needs 1/2 - 2g + g*g = 0 and that root is irrational.
``r_at_infinity`` is the margin, and A-stability is the gate that is actually
reachable.

BOTH LANES ARE UNBOUNDED. The enumeration walks shells of growing lattice
resolution, so there is no last candidate. A coarse value reappears inside a finer
shell, so candidates are deduplicated by key against the lane's own ledger under
the current code hash, which is exactly ``sidetrack.done_idents``.

WORK CAPS ARE COUNTS AND NEVER CLOCKS. ``budget_seconds`` gates STARTING a
candidate, the way ``sidetrack.run_until`` gates starting a point, so an overrun is
bounded by one candidate. Every candidate is bounded by deterministic counts of
its own: ``max_attempts`` for an adaptive run, ``n_max`` and ``bisect_probes`` for
an implicit ladder, and ``diverge_at`` on the data. That keeps an archive record a
pure function of (code, candidate, params), which is the same rule
``sidetrack.SWEEP_MAX_ATTEMPTS`` exists to hold.

CODE HASH. Records carry ``lanesearch_code_hash`` over ``LANESEARCH_FILES``, a
tuple LOCAL to this module. ``lanesearch.py`` and ``validation_axes.py`` are
deliberately NOT added to ``sidetrack.SIDETRACK_FILES``: doing so would move
``sidetrack.code_hash()`` and re-open all 103 measured side-track points every time
either file is edited, for no reason at all.

COST, measured on this host at the default work caps over all eight problems and
all four targets: 0.49 s per adaptive candidate and 1.77 s per implicit candidate,
once the reference solutions are warm. Those are the figures the live run produced
over its first 200 armed cycles and they replace the 0.54 and 2.06 measured in a
fresh process before the lanes were wired; runner._lane_budget_seconds is set from
them (D41). Warming them costs about 38 s in a fresh
process, paid once by ``functools.lru_cache`` inside validation, and it is the
dominant cost of a short firing.

WHAT MAY BE PUBLISHED, and it is one file per lane. The traceability rule admits
``rk-work/{lane}_archive/elites.json`` (D40). That document is capped at 32 entries,
ranked by a total order that reads no clock, validated against ``lane-elites/1``, and
it carries the ``rule`` it ranked by and a ``statement`` of what it ranked and over
what. The per-day record files are NOT admitted and stay off, nor is
``rk-work/validation/axes.json`` (D39): a daily file is a search log, and its
per-candidate rows sit at the grain of a scored archive record while meaning
something else, which is the comparison the section above exists to prevent.
A number quoted out of an elites document is rendered with its metric
(cycles-to-tolerance on the axis-T ladder), its arithmetic (float64) and the fact
that nothing here is order-verified against the pinned checker.
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import json
import math
import os
import sys
import time
from fractions import Fraction
from pathlib import Path
from typing import Callable, Iterable, Sequence

from rk_harness import orderconditions as OC
from rk_harness import validation as V
from rk_harness import validation_axes as VA
from rk_harness.costmodel import M0PLUS_FAST, count_sequence, cycle_count
from rk_harness.paths import HARNESS_DIR, work_dir
from rk_harness.prototypes import adaptive as A
from rk_harness.prototypes import adaptive_q15 as AQ
from rk_harness.prototypes import pair_census as PC
from rk_harness.prototypes import sdirk as SD
from rk_harness.secondpass import TARGETS, Floats, _check_prose, _target_key, ladder_scan
from rk_harness.types import Tableau

# The two lanes this module stocks. Explicit is not here: it has CMA-ES.
LANES: tuple[str, ...] = ("adaptive", "implicit")

SCHEMAS: dict[str, str] = {
    "adaptive": "adaptive-record/1",
    "implicit": "implicit-record/1",
}
ELITES_SCHEMA = "lane-elites/1"

# Digest inputs. Local to this module on purpose: see the module docstring. Every
# entry decides a number that lands in a lane record, and none of them is pinned.
LANESEARCH_FILES: tuple[str, ...] = (
    "rk_harness/lanesearch.py",
    # the axis-T cost model, the shared error metric wiring and the implicit probe
    "rk_harness/validation_axes.py",
    # the ladder itself
    "rk_harness/secondpass.py",
    # the problems, the reference solutions and validation_error
    "rk_harness/validation.py",
    # the dyadic lattice the adaptive pairs are drawn from
    "rk_harness/enumeration.py",
    "rk_harness/prototypes/__init__.py",
    "rk_harness/prototypes/adaptive.py",
    "rk_harness/prototypes/adaptive_q15.py",
    "rk_harness/prototypes/pair_census.py",
    "rk_harness/prototypes/sdirk.py",
)


def code_hash() -> str:
    """sha256 over LANESEARCH_FILES, truncated the way sidetrack.code_hash is.

    A candidate counts as measured only under the code that measured it, so editing
    any input re-opens its records instead of leaving a stale number in the archive.
    """
    h = hashlib.sha256()
    for rel in LANESEARCH_FILES:
        with open(HARNESS_DIR / rel, "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()[:16]


# --------------------------------------------------------------------------- what this is not

NOT_COMPARABLE = (
    "a record here is not comparable with a record in rk-work/archive/. The scored "
    "archive scores error at a fixed 65536-cycle budget through the pinned evaluator "
    "on the seven frozen problems; this measures cycles to tolerance on the axis-T "
    "ladder over the eight validation problems, every run is float64, and nothing "
    "here passes through verifier.py or holds an archive cell")

NOT_A_PAGE_SOURCE = (
    "the traceability rule admits this lane's elites.json and not the per-day records "
    "behind it; nothing here is scored, order-verified, Q15 or comparable with a record "
    "in rk-work/archive/, so a number quoted from the elites document is rendered with "
    "its cycles-to-tolerance metric and its float64 arithmetic")

# Cost bases, LOCAL to this module. validation_axes.COST_BASES names the four grades
# that document uses; the adaptive lane needs a fifth, because its cycle number
# prices a Q15 attempt but counts the attempts of a float64 trajectory.
COST_BASES: tuple[str, ...] = (
    "float_trajectory_q15_attempt_cost",
    "design_estimate_div32",
)

COST_BASIS_DOC: dict[str, dict] = {
    "float_trajectory_q15_attempt_cost": {
        "grade": "an upper bound in the Q15 cost model over an attempt count measured "
                 "in float64, which is a mixed number and is labelled as one",
        "direction": "upper bound within the model, over a trajectory the model did "
                     "not produce",
        "includes": "the stage work of the propagating formula through the pinned "
                    "cycle_count, the error estimate at one priced block per nonzero "
                    "d term scaled by the state count, and one controller update, "
                    "charged on every attempt whether accepted or rejected",
        "excludes": "conditional branches, which costmodel prices for no class, so the "
                    "bit scan is booked at its worst case; derivative evaluations, so "
                    "FSAL is worth zero cycles here; and the Q31 time register terms",
        "why_mixed": "adaptive_q15.solve_adaptive_q15 is hard-wired to the BS32 pair "
                     "through A_REPS, B_REPS and D_REPS, so a candidate pair has no "
                     "Q15 run available. The attempt count comes from the float64 "
                     "controller and the price of an attempt comes from the Q15 model. "
                     "A Q15 run of the same pair would reject more attempts than the "
                     "float run does, so the attempt count is the optimistic half of "
                     "the figure and the per-attempt price is the pessimistic half",
        "source": "costmodel.cycle_count plus costmodel.count_sequence over "
                  "adaptive_q15.reference_sections, with adaptive.solve_adaptive "
                  "supplying the attempt count",
    },
    "design_estimate_div32": dict(VA.COST_BASIS_DOC["design_estimate_div32"]),
}


# --------------------------------------------------------------------------- parameters

COST_MODEL = M0PLUS_FAST
DEFAULT_PROBLEMS: tuple[str, ...] = tuple(sorted(V.VALIDATION_NAMES))
DEFAULT_TARGETS: tuple[float, ...] = tuple(TARGETS)

# The target the elite rule reads. One of DEFAULT_TARGETS, named in a field rather
# than folded into a key name, so a consumer never has to parse it out of a string.
ELITE_TARGET: float = 2.0 ** -10
ELITE_CAP: int = 32

# Adaptive work caps. The attempt cap is deliberately below adaptive_q15's 50,000:
# a firing that walks eight problems and ten rungs pays it eighty times over in the
# worst case, and 20,000 attempts is about a fifth of a second per rung.
ADAPTIVE_TOL_LADDER: tuple[int, ...] = tuple(AQ.TOL_LADDER_LSB)
ADAPTIVE_MAX_ATTEMPTS: int = 20_000

# Implicit work caps. n_max 4096 rather than secondpass's 16384 because a lane
# firing wants candidates, not a deeper ladder on one of them; a run that reaches
# no target at 4096 comes back never_reached and says so.
IMPLICIT_N_MAX: int = 4096
IMPLICIT_BISECT_PROBES: int = 8
# Both lead with the prototype's own setting, so the leading pass over a shell is
# every tableau at the settings EPOCH3-DESIGN measured.
NEWTON_ITER_CHOICES: tuple[int, ...] = (SD.NEWTON_ITERS, 2, 4)
JACOBIAN_POLICIES: tuple[str, ...] = ("analytic", "finite_difference")

# How many raw enumeration indices one firing may examine before it gives up and
# reports why. A count and not a clock: without it a lane whose whole shell is
# already measured would spin to the end of its budget.
SCAN_CAP: int = 20_000

# How many candidates one firing may measure even if the budget allows more. The
# archive is jsonl on disk and a lane record is a few kilobytes, so this is the
# knob that bounds how fast it grows.
MAX_CANDIDATES_PER_STEP: int = 32

STEP_STATUSES: tuple[str, ...] = ("ok", "budget", "cap", "scan_cap", "stopped")

# The controller axis. Dyadic throughout, following the design rule that a gain
# which survives a fixed-point port is a gain expressed in shifts. The reference
# gains lead: EPOCH2-DESIGN's alpha 1/4, beta 1/8, safety 7/8 are what
# adaptive.solve_adaptive defaults to and what the frozen curve was measured at, so
# the leading pass over a shell is "every pair at the gains already measured" and
# the controller sweep comes after it.
REFERENCE_GAINS: tuple[Fraction, Fraction, Fraction] = (
    Fraction(1, 4), Fraction(1, 8), Fraction(7, 8))

_CONTROLLER_GRID: tuple[tuple[Fraction, Fraction, Fraction], ...] = tuple(
    (Fraction(alpha_num, 16), Fraction(beta_num, 16), Fraction(safety_num, 16))
    for alpha_num in (2, 3, 4, 5, 6)
    for beta_num in (0, 1, 2, 3)
    for safety_num in (12, 14, 15)
)

CONTROLLERS: tuple[tuple[Fraction, Fraction, Fraction], ...] = (
    (REFERENCE_GAINS,) + tuple(g for g in _CONTROLLER_GRID if g != REFERENCE_GAINS))

# The adaptive shells: stage count and lattice resolution. Four stages leads
# because a four-stage A generally admits an order-3 b (four unknowns, four
# conditions) while a three-stage one rarely does, so the leading shell is the
# productive one.
ADAPTIVE_SHELL_STAGES: tuple[int, ...] = (4, 3)
ADAPTIVE_S_MAX_MIN: int = 1

# The implicit shells: dyadic denominator 2**s for both gamma and a21.
IMPLICIT_S_MIN: int = 2

# A guard against an index so large the shell walk would not terminate in a useful
# time. A count, and generous: shell 20 already holds more candidates than the
# machine will see.
MAX_SHELLS: int = 24


def default_params(lane: str) -> dict:
    """The work caps a firing runs under, as a document. Stamped on every record so
    a number can be reproduced without reading this file."""
    _check_lane(lane)
    if lane == "adaptive":
        return {
            "problems": list(DEFAULT_PROBLEMS),
            "targets": [float(t) for t in DEFAULT_TARGETS],
            "tol_ladder_lsb": list(ADAPTIVE_TOL_LADDER),
            "max_attempts": ADAPTIVE_MAX_ATTEMPTS,
            "n_max": None,
            "bisect_probes": None,
        }
    return {
        "problems": list(DEFAULT_PROBLEMS),
        "targets": [float(t) for t in DEFAULT_TARGETS],
        "tol_ladder_lsb": None,
        "max_attempts": None,
        "n_max": IMPLICIT_N_MAX,
        "bisect_probes": IMPLICIT_BISECT_PROBES,
    }


def _check_lane(lane: str) -> None:
    if lane not in LANES:
        raise ValueError(f"lane must be one of {LANES}, got {lane!r}")


# --------------------------------------------------------------------------- paths

# The scored paths a lane must never write into. Same list scripts/preflight.py
# holds as SCORED_PATHS and the determinism job in .github/workflows/ci.yml asserts
# about; duplicated here as a guard rather than imported, because scripts/ is not
# an importable package and a guard that cannot run is not a guard.
SCORED_PATHS: tuple[str, ...] = (
    "archive", "quarantine", "hypotheses.jsonl", "RUNSTATE.json", "EPOCH_STATUS.json")


def lane_dir(lane: str) -> Path:
    _check_lane(lane)
    return work_dir() / f"{lane}_archive"


def ledger_path(lane: str) -> Path:
    """The compact index: one short line per measured candidate.

    Same split sidetrack uses. The daily files hold the full records and can grow;
    the cursor and the duplicate check read only this file.
    """
    return lane_dir(lane) / "ledger.jsonl"


def records_path(lane: str, day: str) -> Path:
    return lane_dir(lane) / f"{day}.jsonl"


def elites_path(lane: str) -> Path:
    return lane_dir(lane) / "elites.json"


def _guard_path(path: Path) -> Path:
    """Refuse to write anywhere on the scored path. Checked per component, so a
    directory named adaptive_archive is not confused with archive."""
    parts = set(Path(path).parts)
    hit = sorted(parts & set(SCORED_PATHS))
    if hit:
        raise ValueError(f"lanesearch refuses to write under a scored path: {hit}")
    return path


def _day_of(ts: str) -> str:
    """The UTC date a stamp belongs to. Storage is UTC; display is somebody else's
    problem (rk_harness/timefmt.py)."""
    return str(ts)[:10]


# --------------------------------------------------------------------------- shells

def _shell_of(index: int, size_of: Callable[[int], int]) -> tuple[int, int]:
    """(shell, offset inside it) for a global candidate index.

    A plain accumulation over shells, which terminates quickly because shell sizes
    grow geometrically. MAX_SHELLS is a guard, not a bound on the search: shell 20
    already holds more candidates than the machine will ever reach.
    """
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise ValueError(f"index must be a non-negative integer, got {index!r}")
    base = 0
    for k in range(MAX_SHELLS):
        n = size_of(k)
        if index < base + n:
            return k, index - base
        base += n
    raise ValueError(f"index {index} lies past shell {MAX_SHELLS}; widen MAX_SHELLS "
                     "deliberately rather than by accident")


def _digits(value: int, radix: int, count: int) -> list[int]:
    """`count` mixed-radix digits, least significant leading. A bijection, so an
    offset and a digit tuple name each other."""
    out: list[int] = []
    for _ in range(count):
        out.append(value % radix)
        value //= radix
    return out


def _fracs(seq) -> tuple[Fraction, ...]:
    return tuple(Fraction(x) for x in seq)


def _strs(seq) -> list[str]:
    return [str(Fraction(x)) for x in seq]


# --------------------------------------------------------------------------- adaptive space

def adaptive_shell(k: int) -> tuple[int, int]:
    """(stages, s_max) for adaptive shell k."""
    stages = ADAPTIVE_SHELL_STAGES[k % len(ADAPTIVE_SHELL_STAGES)]
    s_max = ADAPTIVE_S_MAX_MIN + k // len(ADAPTIVE_SHELL_STAGES)
    return stages, s_max


@functools.lru_cache(maxsize=None)
def _lattice(s_max: int) -> tuple[Fraction, ...]:
    """pair_census.census_lattice, memoized. The shell walk asks for it once per
    shell per index, and rebuilding a few thousand Fractions each time would make
    the scan cost grow with the shell rather than with the work."""
    return tuple(PC.census_lattice(s_max))


@functools.lru_cache(maxsize=None)
def adaptive_shell_size(k: int) -> int:
    stages, s_max = adaptive_shell(k)
    entries = stages * (stages - 1) // 2
    return len(_lattice(s_max)) ** entries * len(CONTROLLERS)


def controller_block(slot: int) -> dict:
    """The controller as data, with the searched axes and the fixed ones separated.

    The clamp lives in adaptive.py module constants and the Q15 factor table belongs
    to a solver that cannot run a candidate pair, so neither is searched. Both are
    carried so a record says what the run actually used.
    """
    alpha, beta, safety = CONTROLLERS[slot]
    return {
        "alpha": str(alpha),
        "beta": str(beta),
        "safety": str(safety),
        "clamp_lo": str(Fraction(A.FAC_MIN).limit_denominator(1 << 20)),
        "clamp_hi": str(Fraction(A.FAC_MAX).limit_denominator(1 << 20)),
        "table_bits": int(AQ.FACTOR_Q_BITS),
        "searched": ["alpha", "beta", "safety"],
        "fixed": ["clamp_lo", "clamp_hi", "table_bits"],
    }


def adaptive_candidate(index: int) -> dict | None:
    """The candidate at `index`, or None when the lattice point is not a pair.

    A pure function of the index and this module. No clock, no environment, no
    archive: the archive only decides WHERE a firing starts, never what it finds.
    """
    k, offset = _shell_of(index, adaptive_shell_size)
    stages, s_max = adaptive_shell(k)
    lat = _lattice(s_max)
    entries = stages * (stages - 1) // 2
    # The pair is the fast digit and the controller the slow one, so a firing sees
    # many pairs at the reference gains rather than one pair at sixty controllers.
    pairs = len(lat) ** entries
    pair_offset = offset % pairs
    slot = offset // pairs
    combo = tuple(lat[d] for d in _digits(pair_offset, len(lat), entries))
    A_mat, c = PC._matrix_from(combo, stages)

    g3, r3 = OC.b_linear_system(A_mat, c, 3)
    _rank3, ok3, _free3, b = PC._rank_and_consistency(g3, r3)
    if not ok3 or b is None:
        return None                                   # no order-3 weight vector
    g2, r2 = OC.b_linear_system(A_mat, c, 2)
    _rank2, ok2, free2, b_hat = PC._rank_and_consistency(g2, r2)
    if not ok2 or b_hat is None or free2 < 1:
        return None                                   # b_hat forced to equal b
    d = tuple(b[i] - b_hat[i] for i in range(stages))
    if all(v == 0 for v in d):
        return None                                   # no error estimate at all
    order_hat = 3 if PC.contains_vector(g3, r3, b_hat) else 2
    g4, r4 = OC.b_linear_system(A_mat, c, 4)
    order = 4 if PC.contains_vector(g4, r4, b) else 3
    if order_hat >= order:
        return None                                   # the estimate estimates nothing

    fsal = bool(c[-1] == 1 and PC.contains_vector(g3, r3, A_mat[-1]))
    method = {
        "family": f"embedded_pair_s{stages}_dyadic",
        "A": [_strs(row) for row in A_mat],
        "b": _strs(b),
        "b_hat": _strs(b_hat),
        "c": _strs(c),
        "d": _strs(d),
        "order": int(order),
        "order_hat": int(order_hat),
        "stages": int(stages),
        "fsal": fsal,
        "nonzero_d_terms": sum(1 for v in d if v != 0),
        "b_exact": all(PC._rep_is_exact(v) for v in b),
        "b_hat_exact": all(PC._rep_is_exact(v) for v in b_hat),
        "d_exact": all(v == 0 or PC._rep_is_exact(v) for v in d),
    }
    return {
        "lane": "adaptive",
        "index": int(index),
        "shell": int(k),
        "s_max": int(s_max),
        "method": method,
        "controller": controller_block(slot),
        "_A": A_mat,
        "_b": _fracs(b),
        "_b_hat": _fracs(b_hat),
        "_c": c,
        "_gains": CONTROLLERS[slot],
    }


# --------------------------------------------------------------------------- implicit space

def implicit_shell(k: int) -> int:
    """The dyadic exponent s for implicit shell k: gamma and a21 both on m / 2**s."""
    return IMPLICIT_S_MIN + k


@functools.lru_cache(maxsize=None)
def implicit_shell_size(k: int) -> int:
    s = implicit_shell(k)
    n_gamma = (1 << s) - 1                   # gamma in (0, 1)
    n_a21 = 2 << s                           # a21 in (0, 2]
    return n_gamma * n_a21 * len(NEWTON_ITER_CHOICES) * len(JACOBIAN_POLICIES)


_L_STABLE_NOTE = (
    "no dyadic gamma is L-stable, because R at infinity vanishing needs "
    "1/2 - 2g + g*g = 0 and that root is irrational; r_at_infinity is the margin "
    "and a_stable is the gate that is reachable")


def implicit_candidate(index: int) -> dict | None:
    """The candidate at `index`, or None when the exact solve refuses it.

    A pure function of the index. gamma = 0 and a21 = 0 cannot be reached by
    construction, so the None branch covers only a solve that raises.
    """
    k, offset = _shell_of(index, implicit_shell_size)
    s = implicit_shell(k)
    n_a21 = 2 << s
    # The tableau grid is the fast digit and the solver settings the slow one, for
    # the same reason the adaptive lane walks pairs before controllers.
    n_pairs = ((1 << s) - 1) * n_a21
    tab_slot = offset % n_pairs
    rest = offset // n_pairs
    iters = NEWTON_ITER_CHOICES[rest % len(NEWTON_ITER_CHOICES)]
    jac = JACOBIAN_POLICIES[rest // len(NEWTON_ITER_CHOICES)]
    m_a21 = tab_slot % n_a21 + 1
    m_gamma = tab_slot // n_a21 + 1
    gamma = Fraction(m_gamma, 1 << s)
    a21 = Fraction(m_a21, 1 << s)
    try:
        tab = SD.order2_tableau_exact_a21(gamma, a21)
    except (ValueError, AssertionError):
        return None
    method = {
        "family": "sdirk2_dyadic_gamma",
        "gamma": str(gamma),
        "a21": str(a21),
        "b": _strs(tab["b"]),
        "c": _strs(tab["c"]),
        "newton_iters": int(iters),
        "jacobian": jac,
        "arithmetic": "float64",
        "stages": int(SD.STAGES),
        "order": 2,
        "stiffly_accurate": bool(tab["stiffly_accurate"]),
        "order3_residuals": _strs(tab["order3_residuals"]),
        "div_cycles": int(SD.DIV_CYCLES),
    }
    stability = {
        "l_stable": bool(tab["l_stable"]),
        "r_at_infinity": str(tab["r_at_infinity"]),
        "a_stable": bool(tab["a_stable"]),
        "a_stable_exact": bool(tab["a_stable_exact"]),
        "note": _L_STABLE_NOTE,
    }
    return {
        "lane": "implicit",
        "index": int(index),
        "shell": int(k),
        "s": int(s),
        "method": method,
        "stability": stability,
        "_exact": tab,
        "_iters": int(iters),
        "_jacobian": jac,
    }


CANDIDATE_AT: dict[str, Callable[[int], dict | None]] = {
    "adaptive": adaptive_candidate,
    "implicit": implicit_candidate,
}


def candidate_key(cand: dict) -> str:
    """A short identity for a candidate, independent of the index it was found at.

    Coarse lattice values reappear inside finer shells, so two indices can name the
    same method. The key is what the ledger deduplicates on.
    """
    lane = cand["lane"]
    m = cand["method"]
    if lane == "adaptive":
        parts = [lane, m["family"], json.dumps(m["A"], sort_keys=True),
                 json.dumps(m["b"]), json.dumps(m["b_hat"]),
                 cand["controller"]["alpha"], cand["controller"]["beta"],
                 cand["controller"]["safety"]]
    else:
        parts = [lane, m["family"], m["gamma"], m["a21"], str(m["newton_iters"]),
                 m["jacobian"]]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- cost

def estimate_block() -> list[str]:
    """One priced block of the Q15 error-estimate listing: the five lines
    adaptive_q15.reference_sections emits per d term.

    Sliced out of the reference listing rather than retyped, so a change to the
    listing moves this number with it. A candidate pair has its own number of
    nonzero d terms, which is what the BS32-shaped listing cannot express on its own.
    """
    est = AQ.reference_sections()["estimate"]
    per = len(est) // len(AQ.D_REPS)
    return est[:per]


def pair_attempt_cost(t: Tableau, nonzero_d_terms: int, n_states: int,
                      model=COST_MODEL) -> dict:
    """Cycles one adaptive ATTEMPT of `t` costs, accepted or rejected.

    validation_axes.adaptive_attempt_cost with the BS32 assumption removed: the
    stage work is the pinned cycle_count of the propagating formula, whose A is
    strictly lower triangular so the pinned function is used inside its contract;
    the estimate is one priced block per nonzero d term, per state; the controller
    is one compare, one worst-case bit scan, one table load and one step update,
    independent of the state count. For BS32, which has four nonzero d terms, this
    returns the same numbers validation_axes does.
    """
    sec = AQ.reference_sections()
    stage = cycle_count(t, model, int(n_states))
    estimate = count_sequence(estimate_block(), model) * int(nonzero_d_terms) * int(n_states)
    controller = count_sequence(
        sec["compare"] + sec["bit_scan"] + sec["table_load"] + sec["step_update"], model)
    return {
        "stage": int(stage),
        "estimate": int(estimate),
        "controller": int(controller),
        "total": int(stage + estimate + controller),
        "branch_allowance": int(AQ.BRANCH_ALLOWANCE["conditional_branches"]),
    }


# --------------------------------------------------------------------------- probes

def _pair_of(cand: dict) -> A.EmbeddedPair:
    m = cand["method"]
    return A.EmbeddedPair(
        name=f"lanesearch_{cand['index']}",
        A=tuple(tuple(float(x) for x in row) for row in cand["_A"]),
        b=tuple(float(x) for x in cand["_b"]),
        b_hat=tuple(float(x) for x in cand["_b_hat"]),
        c=tuple(float(x) for x in cand["_c"]),
        order=int(m["order"]),
        order_hat=int(m["order_hat"]),
        fsal=bool(m["fsal"]),
    )


def adaptive_run(pair: A.EmbeddedPair, name: str, tol_q: int,
                 gains: tuple[Fraction, Fraction, Fraction],
                 max_attempts: int) -> dict:
    """One adaptive run on one validation problem at one rung of the LSB ladder.

    float64 throughout: adaptive_q15.solve_adaptive_q15 is hard-wired to BS32 and
    cannot run a candidate pair. The rung is asked for in the same units the Q15
    ladder uses, tol_q LSB of the scaled state expressed in the problem's own
    physical units, so a lane row and a validation_axes float-twin row are asking
    for the same thing. Failure is a status, never an exception.
    """
    alpha, beta, safety = gains
    p = V.PROBLEMS[name]
    tol = float(tol_q) * VA.lsb_physical(name)
    out: dict = {"tol_q": int(tol_q), "status": "ok", "achieved_error": None,
                 "attempts": None, "n_accepted": None, "n_rejected": None,
                 "n_fevals": None, "h_final": None}
    try:
        res = A.solve_adaptive(pair, V.FLOAT_RHS[name], V.Y0_PHYS[name], 0.0, p.t_end,
                               rtol=tol, atol=tol, h0=p.t_end / 64.0,
                               max_attempts=int(max_attempts),
                               alpha=float(alpha), beta=float(beta),
                               safety=float(safety))
    except RuntimeError as e:
        out["status"] = "underflow" if "underflow" in str(e) else "attempt_cap"
        return out
    except (OverflowError, ZeroDivisionError, ValueError, ArithmeticError):
        out["status"] = "overflow"
        return out
    except Exception:                       # a broken pair must not stop the sweep
        out["status"] = "error"
        return out
    err = V.validation_error(name, res.y)
    out.update({
        "attempts": int(res.n_accepted + res.n_rejected),
        "n_accepted": int(res.n_accepted),
        "n_rejected": int(res.n_rejected),
        "n_fevals": int(res.n_fevals),
        "h_final": float(res.h_final) if math.isfinite(res.h_final) else None,
        "achieved_error": float(err) if math.isfinite(err) else None,
    })
    if out["achieved_error"] is None:
        out["status"] = "nonfinite"
    return out


def _adaptive_probe(pair, name, tol_q, gains, max_attempts, cache) -> tuple:
    """One rung for ladder_scan. The full run stays in the caller's cache, because
    the cycle number is attempts times a per-attempt price and the attempt count is
    not the tolerance."""
    hit = cache.get(tol_q)
    if hit is None:
        hit = adaptive_run(pair, name, tol_q, gains, max_attempts)
        cache[tol_q] = hit
    if hit["status"] == "ok" and hit["achieved_error"] is not None:
        return ("ok", float(hit["achieved_error"]), None)
    if hit["status"] == "overflow":
        return ("overflow", None, None)
    if hit["status"] == "nonfinite":
        return ("nonfinite", None, None)
    return ("error", None, None)


# --------------------------------------------------------------------------- statuses

# Most severe leading. A candidate's worst_status is the worst row it produced, so
# one broken problem is visible in the score without opening the results list.
STATUS_SEVERITY: tuple[str, ...] = (
    "overflow_before_target", "newton_nonconvergent", "step_underflow",
    "attempt_cap", "never_reached", "reached",
)


def worst_status(rows: Sequence[dict]) -> str:
    """The most severe status among the rows, or 'never_reached' when there are none."""
    seen = {str(r.get("status")) for r in rows}
    for s in STATUS_SEVERITY:
        if s in seen:
            return s
    return "never_reached"


def _median(vals: list[float]) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else 0.5 * (s[mid - 1] + s[mid])


def _best_error(rungs: Sequence[dict]) -> float | None:
    vals = [r["achieved_error"] for r in rungs
            if r.get("status") == "ok" and r.get("achieved_error") is not None]
    return min(vals) if vals else None


def build_score(rows: list[dict], fl: Floats) -> dict:
    """The comparable summary of one candidate, and the only thing the elite rule
    reads. Deterministic: medians over sorted values, no clock, no randomness."""
    key = _target_key(ELITE_TARGET)
    at_target = [r for r in rows if r["target_key"] == key]
    reached = [r for r in at_target if r["status"] == "reached" and r["cycles"] is not None]
    errs = [r["achieved_error"] for r in rows
            if r.get("achieved_error") is not None and math.isfinite(r["achieved_error"])]
    return {
        "elite_target": float(ELITE_TARGET),
        "elite_target_key": key,
        "targets_reached": sum(1 for r in rows if r["status"] == "reached"),
        "targets_total": len(rows),
        "problems_at_elite_target": len(at_target),
        "reached_at_elite_target": len(reached),
        "median_cycles_at_target": fl(_median([float(r["cycles"]) for r in reached])),
        "worst_status": worst_status(rows),
        "best_achieved_error": fl(min(errs)) if errs else None,
    }


# --------------------------------------------------------------------------- evaluation

def _error_at(scan: dict, entry: dict, control) -> float | None:
    if control is None:
        return None
    for row in scan["ladder"]:
        if row["n"] == control:
            return row["error"]
    for row in entry["probed"]:
        if row["n"] == control:
            return row["error"]
    return None


def evaluate_adaptive(cand: dict, problems: Sequence[str], targets: Sequence[float],
                      tol_ladder: Sequence[int], max_attempts: int,
                      fl: Floats) -> tuple[list[dict], dict]:
    """One adaptive candidate over every (problem, target). Rows plus a cost block.

    The tolerance ladder runs coarsest leading, which is cheapest leading, so
    ladder_scan's rule "the earliest rung that meets the target" reads as the
    coarsest rung, exactly as it does in validation_axes. Nothing is bisected: the
    ladder is dyadic in whole LSB with nothing between adjacent rungs, so probes
    comes back 0 rather than as a fiction.
    """
    pair = _pair_of(cand)
    gains = cand["_gains"]
    tab = Tableau(A=tuple(cand["_A"]), b=tuple(cand["_b"]), c=tuple(cand["_c"]))
    ladder = VA.descending_ladder(tol_ladder)
    n_d = int(cand["method"]["nonzero_d_terms"])
    rows: list[dict] = []
    cost: dict[str, dict] = {}
    for pname in sorted(problems):
        n_states = V.PROBLEMS[pname].n_states
        per_attempt = pair_attempt_cost(tab, n_d, n_states)
        cost[pname] = per_attempt
        cache: dict[int, dict] = {}
        scan = ladder_scan(
            lambda q: _adaptive_probe(pair, pname, q, gains, max_attempts, cache),
            targets, unit_ladder=ladder, bisect=False, per_unit_cost=None)
        rungs = [cache[q] for q in ladder if q in cache]
        best = _best_error(rungs)
        for target in targets:
            entry = scan["targets"][_target_key(target)]
            tol = entry["n"]
            res = cache.get(tol) if tol is not None else None
            cycles = accepted = None
            if res is not None and res.get("attempts") is not None:
                cycles = int(res["attempts"]) * per_attempt["total"]
                accepted = int(res["n_accepted"]) * per_attempt["total"]
            rows.append({
                "problem": pname,
                "class": "adaptive",
                "target": float(target),
                "target_key": _target_key(target),
                "control_kind": "tol_q",
                "control_value": tol,
                "cycles": cycles,
                "cycles_accepted_only": accepted,
                "cycles_per_attempt": per_attempt["total"],
                "status": VA._adaptive_target_status(entry["status"], rungs,
                                                     float(target), None),
                "achieved_error": fl(res["achieved_error"]) if res is not None else None,
                "best_achieved_error": fl(best),
                "ladder_monotone": bool(entry["ladder_monotone"]),
                "probes": 0,
                "attempts": (res or {}).get("attempts"),
                "n_accepted": (res or {}).get("n_accepted"),
                "n_rejected": (res or {}).get("n_rejected"),
                "n_fevals": (res or {}).get("n_fevals"),
                # FSAL saves one derivative evaluation per accepted step and the
                # cost model prices no derivative evaluation for any class, so the
                # saving is reported beside the cycle number and never inside it.
                "fevals_saved_by_fsal": ((res or {}).get("n_accepted")
                                         if cand["method"]["fsal"] else 0),
            })
    return rows, cost


def evaluate_implicit(cand: dict, problems: Sequence[str], targets: Sequence[float],
                      n_max: int, bisect_probes: int,
                      fl: Floats) -> tuple[list[dict], dict]:
    """One implicit candidate over every (problem, target). Rows plus a cost block.

    The probe is validation_axes' own SDIRK probe, so a lane rung and an axis-T
    rung of the validation document are the same measurement with the same failure
    vocabulary. The analytic Jacobian policy falls back to the finite difference on
    the five problems with no hand-derived matrix, and the row says so per problem
    rather than pretending it had one.
    """
    spec = SD.spec_from_exact(cand["_exact"], newton_iters=cand["_iters"])
    policy = cand["_jacobian"]
    rows: list[dict] = []
    cost: dict[str, dict] = {}
    for pname in sorted(problems):
        use_analytic = policy == "analytic" and V.ANALYTIC_JACOBIAN[pname] is not None
        effective = "analytic" if use_analytic else "finite_difference"
        jac = V.ANALYTIC_JACOBIAN[pname] if use_analytic else None
        est = VA.sdirk_step_cost(V.PROBLEMS[pname].n_states, fd=not use_analytic,
                                 newton_iters=cand["_iters"])
        per_step = int(est["total"])
        cost[pname] = {"total": per_step, "terms": dict(est["terms"]),
                       "f_evals_per_step": int(est["f_evals_per_step"]),
                       "jacobian": effective}
        cache: dict[int, tuple] = {}
        scan = ladder_scan(
            lambda n: VA._implicit_probe(pname, n, jac, spec, VA.diverge_bound(pname),
                                         cache),
            targets, n_max=n_max, bisect_probes=bisect_probes, per_unit_cost=per_step)
        best = _best_error([{"status": r["status"], "achieved_error": r["error"]}
                            for r in scan["ladder"]])
        for target in targets:
            entry = scan["targets"][_target_key(target)]
            n = entry["n"]
            rows.append({
                "problem": pname,
                "class": "implicit",
                "target": float(target),
                "target_key": _target_key(target),
                "control_kind": "steps",
                "control_value": n,
                "cycles": entry["cycles"],
                "cycles_accepted_only": entry["cycles"],
                "cycles_per_step": per_step,
                "status": VA._implicit_target_status(entry["status"], scan["ladder"]),
                "achieved_error": fl(_error_at(scan, entry, n)),
                "best_achieved_error": fl(best),
                "ladder_monotone": bool(entry["ladder_monotone"]),
                "probes": int(entry["probes"]),
                "steps": n,
                "njev": n,
                "nlu": n,
                "nlinsolve": None if n is None else n * SD.STAGES * cand["_iters"],
                "jacobian": effective,
            })
    return rows, cost


# --------------------------------------------------------------------------- records

def _identity(rec: dict) -> dict:
    """The part of a record that identifies the candidate under this code.

    Deliberately excludes ts, cycle, seed and index: the same method measured from a
    different cycle, or found at a different index in a finer shell, is the same
    measurement and must hash the same.
    """
    return {k: rec[k] for k in ("schema", "lane", "lanesearch_code_hash", "method",
                                "params") if k in rec} | {
        "controller": rec.get("controller"),
        "stability": rec.get("stability"),
    }


def record_hash(rec: dict) -> str:
    blob = json.dumps(_identity(rec), sort_keys=True, allow_nan=False,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def results_digest(rows: Sequence[dict]) -> str:
    blob = json.dumps(list(rows), sort_keys=True, allow_nan=False,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def build_record(cand: dict, *, cycle: int = 0, seed: int = 0, ts: str = "",
                 problems: Sequence[str] | None = None,
                 targets: Sequence[float] | None = None,
                 tol_ladder: Sequence[int] | None = None,
                 max_attempts: int | None = None,
                 n_max: int | None = None,
                 bisect_probes: int | None = None,
                 ch: str | None = None) -> dict:
    """Measure one candidate and return its archive record.

    A pure function of (code, candidate, params) apart from ``ts``, which the caller
    supplies. Two builds of the same candidate with the same stamp are byte
    identical; two builds with different stamps differ in ``ts`` and nowhere else.
    """
    lane = cand["lane"]
    _check_lane(lane)
    fl = Floats()
    defaults = default_params(lane)
    problems = list(defaults["problems"] if problems is None else problems)
    targets = [float(t) for t in (defaults["targets"] if targets is None else targets)]
    params = {
        "problems": sorted(problems),
        "targets": targets,
        "target_keys": [_target_key(t) for t in targets],
        "cost_model": COST_MODEL.name,
        "error_metric": "validation.validation_error, L2 final state over PEAK",
        "arithmetic": "float64",
    }
    if lane == "adaptive":
        ladder = list(defaults["tol_ladder_lsb"] if tol_ladder is None else tol_ladder)
        cap = int(defaults["max_attempts"] if max_attempts is None else max_attempts)
        params["work_cap"] = {"max_attempts": cap, "tol_ladder_lsb": ladder,
                              "n_max": None, "bisect_probes": None, "diverge_at": None}
        rows, cost = evaluate_adaptive(cand, params["problems"], targets, ladder, cap, fl)
        cost_basis = "float_trajectory_q15_attempt_cost"
        extra = {"controller": cand["controller"], "cycles_per_attempt": cost}
    else:
        nm = int(defaults["n_max"] if n_max is None else n_max)
        bp = int(defaults["bisect_probes"] if bisect_probes is None else bisect_probes)
        params["work_cap"] = {
            "max_attempts": None, "tol_ladder_lsb": None, "n_max": nm,
            "bisect_probes": bp,
            "diverge_at": {p: VA.diverge_bound(p) for p in params["problems"]}}
        rows, cost = evaluate_implicit(cand, params["problems"], targets, nm, bp, fl)
        cost_basis = "design_estimate_div32"
        extra = {"stability": cand["stability"], "cycles_per_step": cost}

    rec = {
        "schema": SCHEMAS[lane],
        "lane": lane,
        "ts": str(ts),
        "cycle": int(cycle),
        "seed": int(seed),
        "index": int(cand["index"]),
        "shell": int(cand["shell"]),
        "lanesearch_code_hash": code_hash() if ch is None else str(ch),
        "method": cand["method"],
        "params": params,
        "results": rows,
        "cost_basis": cost_basis,
        "not_comparable": NOT_COMPARABLE,
        "not_a_page_source": NOT_A_PAGE_SOURCE,
        "nonfinite_written_as_null": 0,
    }
    rec.update(extra)
    rec["score"] = build_score(rows, fl)
    rec["nonfinite_written_as_null"] = int(fl.nonfinite)
    rec["key"] = candidate_key(cand)
    rec["record_hash"] = record_hash(rec)
    rec["results_digest"] = results_digest(rows)
    return rec


# --------------------------------------------------------------------------- archive io

LEDGER_KEYS: tuple[str, ...] = (
    "ts", "cycle", "seed", "index", "shell", "key", "record_hash", "results_digest",
    "lanesearch_code_hash", "schema", "lane", "file", "worst_status",
    "targets_reached", "median_cycles_at_target",
)


def ledger_line(rec: dict, file_name: str) -> dict:
    """The compact index line for one record.

    Everything the cursor, the duplicate check and a status view need, and nothing
    else, so a firing never reads the daily files. Same split sidetrack keeps
    between its ledger and its artifacts.
    """
    s = rec["score"]
    out = {
        "ts": rec["ts"],
        "cycle": int(rec["cycle"]),
        "seed": int(rec["seed"]),
        "index": int(rec["index"]),
        "shell": int(rec["shell"]),
        "key": rec["key"],
        "record_hash": rec["record_hash"],
        "results_digest": rec["results_digest"],
        "lanesearch_code_hash": rec["lanesearch_code_hash"],
        "schema": rec["schema"],
        "lane": rec["lane"],
        "file": file_name,
        "worst_status": s["worst_status"],
        "targets_reached": int(s["targets_reached"]),
        "median_cycles_at_target": s["median_cycles_at_target"],
    }
    assert set(out) == set(LEDGER_KEYS), "the ledger line and LEDGER_KEYS disagree"
    return out


def _append_jsonl(path: Path, obj: dict) -> Path:
    _guard_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(obj, sort_keys=True, allow_nan=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return path


def append_record(rec: dict) -> tuple[Path, Path]:
    """Write the full record, then its ledger line.

    In that order, for sidetrack's reason: a crash can leave a record nothing points
    at, never a ledger line with no record behind it.
    """
    lane = rec["lane"]
    day = _day_of(rec["ts"]) or "undated"
    rpath = _append_jsonl(records_path(lane, day), rec)
    lpath = _append_jsonl(ledger_path(lane), ledger_line(rec, rpath.name))
    return rpath, lpath


def _read_jsonl(path: Path) -> list[dict]:
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
            continue                        # a torn last line is discarded, not fatal
        if isinstance(obj, dict):
            out.append(obj)
    return out


def load_ledger(lane: str) -> list[dict]:
    return _read_jsonl(ledger_path(lane))


def load_records(lane: str, day: str | None = None) -> list[dict]:
    """Full records for one day, or for every day in sorted file order."""
    _check_lane(lane)
    if day is not None:
        return _read_jsonl(records_path(lane, day))
    out: list[dict] = []
    d = lane_dir(lane)
    if not d.exists():
        return out
    for path in sorted(p for p in d.glob("*.jsonl") if p.name != "ledger.jsonl"):
        out.extend(_read_jsonl(path))
    return out


def measured_keys(lane: str, ch: str | None = None,
                  ledger: Sequence[dict] | None = None) -> set[str]:
    """Candidate keys already measured under this code hash. sidetrack.done_idents,
    one lane at a time."""
    cur = code_hash() if ch is None else ch
    led = load_ledger(lane) if ledger is None else ledger
    return {str(e.get("key")) for e in led
            if e.get("lanesearch_code_hash") == cur and e.get("key")}


def next_index(lane: str, ch: str | None = None,
               ledger: Sequence[dict] | None = None) -> int:
    """Where the next firing starts walking: one past the highest index measured
    under this code hash, or 0 when there is nothing.

    The archive chooses WHERE to start, never WHAT is found: the enumeration itself
    is a pure function of the index.
    """
    cur = code_hash() if ch is None else ch
    led = load_ledger(lane) if ledger is None else ledger
    idx = [int(e["index"]) for e in led
           if e.get("lanesearch_code_hash") == cur and isinstance(e.get("index"), int)]
    return max(idx) + 1 if idx else 0


# --------------------------------------------------------------------------- elites

ELITE_RULE = (
    "lowest median cycles at the elite target across the problems where the status "
    "is reached; a candidate that reached the elite target nowhere ranks below every "
    "candidate that reached it somewhere; ties broken by the count of targets "
    "reached, highest leading, then by record_hash. No clock is read and no value is "
    "sampled, so the order is total and two runs over the same records agree")


def rank_key(rec: dict) -> tuple:
    """The total order the elite rule describes. Sorting ascending puts the best
    leading."""
    s = rec.get("score") or {}
    med = s.get("median_cycles_at_target")
    unreached = 1 if med is None else 0
    return (unreached, float(med) if med is not None else 0.0,
            -int(s.get("targets_reached") or 0), str(rec.get("record_hash") or ""))


def rank(records: Sequence[dict]) -> list[dict]:
    """Records best leading, deduplicated by record_hash. Invariant under the input
    order, which is what makes a merge of old elites and new records deterministic."""
    seen: dict[str, dict] = {}
    for rec in records:
        h = str(rec.get("record_hash") or "")
        if h not in seen:
            seen[h] = rec
    return sorted(seen.values(), key=rank_key)


def elite_entry(rec: dict) -> dict:
    """One elites row: enough to describe the method and its score without opening
    the daily file, plus the digest that ties it back to one."""
    out = {
        "record_hash": rec["record_hash"],
        "results_digest": rec["results_digest"],
        "key": rec["key"],
        "index": int(rec["index"]),
        "shell": int(rec["shell"]),
        "cycle": int(rec["cycle"]),
        "ts": rec["ts"],
        "method": rec["method"],
        "score": rec["score"],
        "cost_basis": rec["cost_basis"],
    }
    for k in ("controller", "stability"):
        if k in rec:
            out[k] = rec[k]
    return out


def _elites_statement(lane: str, entries: list[dict], n_measured: int,
                      n_ranked: int) -> str:
    """One sentence derived from the numbers, reading correctly whichever way they
    fall, including the case this module exists for: an archive with nothing in it."""
    noun = "candidate" if n_measured == 1 else "candidates"
    held = (f"The {lane} lane archive holds {n_measured} measured {noun} under the "
            f"current code hash, of which this document ranked {n_ranked}.")
    if not entries:
        return held + " No candidate has been ranked yet."
    best = entries[0]["score"]
    med = best.get("median_cycles_at_target")
    where = (f"a median of {med:.0f} cycles at the elite target over "
             f"{best['reached_at_elite_target']} of {best['problems_at_elite_target']} "
             f"problems" if med is not None else
             "no problem reached at the elite target")
    return (held + f" The leading candidate shows {where}. These records are off the "
            "scored path and are not comparable with a scored archive record.")


def build_elites(lane: str, records: Sequence[dict], *, cap: int = ELITE_CAP,
                 ch: str | None = None, generated_cycle: int | None = None,
                 generated_ts: str | None = None,
                 n_measured: int | None = None) -> dict:
    """The lane-elites/1 document. Pure function of the records plus this module.

    No wall clock: generated_ts defaults to the newest ranked record's own stamp, so
    two builds over the same records are byte identical.

    Three counts, kept apart because they answer different questions. ``n_input`` is
    what was handed to this build; ``n_ranked`` is how many of those carried the
    current code hash and entered the order; ``n_measured`` is how many candidates
    the lane has measured under that hash, which the caller reads from the ledger and
    which is larger than ``n_ranked`` as soon as the cap bites. Folding them into one
    number would let a capped document claim the archive is smaller than it is.
    """
    _check_lane(lane)
    cur = code_hash() if ch is None else str(ch)
    mine = [r for r in records if r.get("lanesearch_code_hash") == cur]
    ordered = rank(mine)
    entries = [elite_entry(r) for r in ordered[:max(0, int(cap))]]
    stamps = sorted(str(r.get("ts") or "") for r in mine if r.get("ts"))
    cycles = [int(r["cycle"]) for r in mine if isinstance(r.get("cycle"), int)]
    return {
        "_meta": {
            "schema": ELITES_SCHEMA,
            "lane": lane,
            "lanesearch_code_hash": cur,
            "n_ranked": len(mine),
            "n_input": len(list(records)),
            "n_measured": len(mine) if n_measured is None else int(n_measured),
            "n_elites": len(entries),
            "elite_cap": int(cap),
            "elite_target": float(ELITE_TARGET),
            "elite_target_key": _target_key(ELITE_TARGET),
            "generated_cycle": (max(cycles) if cycles else 0) if generated_cycle is None
                               else int(generated_cycle),
            "generated_ts": (stamps[-1] if stamps else "") if generated_ts is None
                            else str(generated_ts),
            "cost_basis": COST_BASES[0] if lane == "adaptive" else COST_BASES[1],
            "cost_bases": COST_BASIS_DOC,
            "not_comparable": NOT_COMPARABLE,
            "not_a_page_source": NOT_A_PAGE_SOURCE,
        },
        "elites": entries,
        "rule": ELITE_RULE,
        "statement": _elites_statement(
            lane, entries, len(mine) if n_measured is None else int(n_measured),
            len(mine)),
    }


# --------------------------------------------------------------------------- validation

def _walk_floats(node, path: str, fail) -> None:
    """No non-finite float may reach disk: they are written as null and counted."""
    if isinstance(node, float):
        if not math.isfinite(node):
            fail(f"{path} is a non-finite float; write null and count it")
    elif isinstance(node, dict):
        for k, v in node.items():
            _walk_floats(v, f"{path}.{k}", fail)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk_floats(v, f"{path}[{i}]", fail)


def validate_record(rec: dict) -> None:
    """Raise ValueError if the record violates its schema."""
    def fail(msg: str):
        raise ValueError(f"lane record: {msg}")

    lane = rec.get("lane")
    if lane not in LANES:
        fail(f"lane must be one of {LANES}, got {lane!r}")
    if rec.get("schema") != SCHEMAS[lane]:
        fail(f"schema must be {SCHEMAS[lane]!r}, got {rec.get('schema')!r}")
    for key in ("ts", "key", "record_hash", "results_digest", "lanesearch_code_hash",
                "cost_basis", "not_comparable", "not_a_page_source"):
        if not isinstance(rec.get(key), str) or not rec[key].strip():
            fail(f"{key} must be a non-empty string")
    if rec["cost_basis"] not in COST_BASES:
        fail(f"cost_basis must be one of {COST_BASES}, got {rec['cost_basis']!r}")
    for key in ("cycle", "seed", "index", "shell", "nonfinite_written_as_null"):
        v = rec.get(key)
        if not isinstance(v, int) or isinstance(v, bool):
            fail(f"{key} must be an integer")
    if not isinstance(rec.get("method"), dict) or not rec["method"]:
        fail("method must be a non-empty object")
    params = rec.get("params")
    if not isinstance(params, dict):
        fail("params must be an object")
    for key in ("problems", "targets", "target_keys"):
        if not isinstance(params.get(key), list) or not params[key]:
            fail(f"params.{key} must be a non-empty list")
    if not isinstance(params.get("work_cap"), dict):
        fail("params.work_cap must be an object: a work cap is a count, not a clock")
    rows = rec.get("results")
    if not isinstance(rows, list) or not rows:
        fail("results must be a non-empty list")
    want = {(p, k) for p in params["problems"] for k in params["target_keys"]}
    got: set[tuple[str, str]] = set()
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            fail(f"results[{i}] must be an object")
        for key in ("problem", "target_key", "status", "control_kind"):
            if not isinstance(row.get(key), str):
                fail(f"results[{i}].{key} must be a string")
        if row["status"] not in STATUS_SEVERITY:
            fail(f"results[{i}].status {row['status']!r} is not a known status")
        if row.get("class") != lane:
            fail(f"results[{i}].class must be {lane!r}")
        ident = (row["problem"], row["target_key"])
        if ident in got:
            fail(f"results covers {ident} more than once")
        got.add(ident)
    if got != want:
        fail("results must cover every (problem, target) exactly once")
    score = rec.get("score")
    if not isinstance(score, dict):
        fail("score must be an object")
    for key in ("targets_reached", "targets_total", "reached_at_elite_target",
                "problems_at_elite_target"):
        if not isinstance(score.get(key), int) or isinstance(score.get(key), bool):
            fail(f"score.{key} must be an integer")
    if score.get("worst_status") not in STATUS_SEVERITY:
        fail("score.worst_status is not a known status")
    if score.get("targets_total") != len(rows):
        fail("score.targets_total must be the number of result rows")
    if lane == "adaptive" and not isinstance(rec.get("controller"), dict):
        fail("an adaptive record must carry its controller")
    if lane == "implicit" and not isinstance(rec.get("stability"), dict):
        fail("an implicit record must carry its stability block")
    _check_prose(rec["not_comparable"], "not_comparable", fail)
    _walk_floats(rec, "record", fail)


def validate_elites(doc: dict) -> None:
    """Raise ValueError if the elites document violates lane-elites/1."""
    def fail(msg: str):
        raise ValueError(f"lane elites: {msg}")

    meta = doc.get("_meta")
    if not isinstance(meta, dict):
        fail("missing _meta")
    if meta.get("schema") != ELITES_SCHEMA:
        fail(f"_meta.schema must be {ELITES_SCHEMA!r}")
    if meta.get("lane") not in LANES:
        fail(f"_meta.lane must be one of {LANES}")
    for key in ("n_ranked", "n_input", "n_measured", "n_elites", "elite_cap",
                "generated_cycle"):
        if not isinstance(meta.get(key), int) or isinstance(meta.get(key), bool):
            fail(f"_meta.{key} must be an integer")
    for key in ("lanesearch_code_hash", "elite_target_key", "cost_basis",
                "not_comparable", "not_a_page_source"):
        if not isinstance(meta.get(key), str) or not meta[key].strip():
            fail(f"_meta.{key} must be a non-empty string")
    if not isinstance(meta.get("elite_target"), float):
        fail("_meta.elite_target must be a float")
    if meta.get("cost_basis") not in COST_BASES:
        fail("_meta.cost_basis is not a known cost basis")
    if not isinstance(meta.get("cost_bases"), dict) or not meta["cost_bases"]:
        fail("_meta.cost_bases must say what each grade of number is")
    elites = doc.get("elites")
    if not isinstance(elites, list):
        fail("elites must be a list")
    if len(elites) != meta["n_elites"]:
        fail("_meta.n_elites must be the length of elites")
    if len(elites) > meta["elite_cap"]:
        fail("elites is longer than the cap")
    hashes = []
    for i, e in enumerate(elites):
        if not isinstance(e, dict):
            fail(f"elites[{i}] must be an object")
        for key in ("record_hash", "results_digest", "key", "cost_basis"):
            if not isinstance(e.get(key), str) or not e[key].strip():
                fail(f"elites[{i}].{key} must be a non-empty string")
        if not isinstance(e.get("method"), dict) or not e["method"]:
            fail(f"elites[{i}].method must be a non-empty object")
        if not isinstance(e.get("score"), dict):
            fail(f"elites[{i}].score must be an object")
        hashes.append(e["record_hash"])
    if len(set(hashes)) != len(hashes):
        fail("elites carries the same record_hash twice")
    if [rank_key(e) for e in elites] != sorted(rank_key(e) for e in elites):
        fail("elites is not in the order the rule describes")
    for key in ("rule", "statement"):
        if not isinstance(doc.get(key), str) or not doc[key].strip():
            fail(f"{key} must be a non-empty string")
        _check_prose(doc[key], key, fail)
    _check_prose(meta["not_comparable"], "_meta.not_comparable", fail)
    _walk_floats(doc, "doc", fail)


def write_elites(doc: dict, path: Path | str | None = None) -> Path:
    out = Path(path) if path is not None else elites_path(doc["_meta"]["lane"])
    _guard_path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, indent=1, sort_keys=True, allow_nan=False) + "\n"
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return out


def load_elites(lane: str) -> dict | None:
    path = elites_path(lane)
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def update_elites(lane: str, new_records: Sequence[dict], *, cap: int = ELITE_CAP,
                  ch: str | None = None, path: Path | str | None = None) -> dict:
    """Merge the records a firing produced into the elites document and write it.

    The merge reads the existing elites rather than every daily file, so the cost of
    a firing does not grow with the archive. The order is total, so merging the same
    set in any order gives the same document; what the document depends on is which
    records have ever been ranked, exactly as the scored archive's elites do.
    """
    _check_lane(lane)
    cur = code_hash() if ch is None else str(ch)
    old = load_elites(lane)
    carried: list[dict] = []
    if old is not None and (old.get("_meta") or {}).get("lanesearch_code_hash") == cur:
        for e in old.get("elites") or []:
            carried.append(dict(e, lanesearch_code_hash=cur,
                                cycle=int(e.get("cycle") or 0)))
    # n_measured is what the LEDGER says, and it is the honest number even when it
    # disagrees with what was handed in: a document built without a ledger line
    # behind every record should say so by the two counts differing, not by
    # borrowing the larger one.
    doc = build_elites(lane, carried + list(new_records), cap=cap, ch=cur,
                       n_measured=len(measured_keys(lane, cur)))
    validate_elites(doc)
    write_elites(doc, path)
    return doc


# --------------------------------------------------------------------------- the lane step

def _noop_log(kind: str, **detail) -> None:
    return None


def _utc_stamp() -> str:
    """The one clock read in this module, and only when a caller does not pass a
    stamp. Storage is UTC; Central is a display concern (rk_harness/timefmt.py)."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def next_candidate(lane: str, start: int, skip: Iterable[str] = (), *,
                   scan_cap: int = SCAN_CAP) -> tuple[dict | None, int, int]:
    """The next admissible, unmeasured candidate at or after `start`.

    Returns (candidate or None, the index it was found at, indices examined). A pure
    function of (start, skip, this module): the ledger decides where to look, never
    what is there. `scan_cap` is a COUNT, so a lane whose whole shell is already
    measured gives up after a bounded walk instead of spinning out its budget.
    """
    _check_lane(lane)
    at = CANDIDATE_AT[lane]
    seen = set(skip)
    index = int(start)
    for examined in range(1, int(scan_cap) + 1):
        try:
            cand = at(index)
        except ValueError:                  # past MAX_SHELLS: the walk stops here
            return None, index, examined
        index += 1
        if cand is None:
            continue
        if candidate_key(cand) in seen:
            continue
        return cand, index - 1, examined
    return None, index, int(scan_cap)


def step(lane: str, seed: int = 0, budget_seconds: float = 0.0, cycle: int = 0,
         stop: Callable[[], bool] | None = None, *,
         log: Callable[..., None] = _noop_log,
         start: int | None = None,
         ts: str | None = None,
         max_candidates: int = MAX_CANDIDATES_PER_STEP,
         scan_cap: int = SCAN_CAP,
         write: bool = True,
         problems: Sequence[str] | None = None,
         targets: Sequence[float] | None = None,
         tol_ladder: Sequence[int] | None = None,
         max_attempts: int | None = None,
         n_max: int | None = None,
         bisect_probes: int | None = None) -> list[dict]:
    """Measure candidates until the budget is spent, and return their records.

    The budget gates STARTING a candidate, not finishing one, exactly as
    sidetrack.run_until does, so a firing overruns by at most one candidate and each
    candidate is bounded by its own deterministic work caps. A zero budget therefore
    measures exactly one candidate rather than none, which is what makes a lane
    always produce something.

    `start` defaults to one past the highest index this lane has measured under the
    current code hash. Pass it to make a firing a pure function of its arguments.
    """
    _check_lane(lane)
    started = time.monotonic()
    ch = code_hash()
    led = load_ledger(lane)
    skip = measured_keys(lane, ch, led)
    index = next_index(lane, ch, led) if start is None else int(start)
    stamp = _utc_stamp() if ts is None else str(ts)
    out: list[dict] = []
    examined = 0
    status = "ok"
    log("lanesearch_started", lane=lane, cycle=int(cycle), seed=int(seed),
        start=index, budget_seconds=float(budget_seconds), code_hash=ch)
    while True:
        if stop is not None and stop():
            status = "stopped"
            break
        if out and time.monotonic() - started >= float(budget_seconds):
            status = "budget"
            break
        if len(out) >= int(max_candidates):
            status = "cap"
            break
        cand, found_at, walked = next_candidate(lane, index, skip, scan_cap=scan_cap)
        examined += walked
        if cand is None:
            status = "scan_cap"
            log("lanesearch_scan_cap", lane=lane, cycle=int(cycle), start=index,
                examined=walked)
            break
        index = found_at + 1
        rec = build_record(cand, cycle=cycle, seed=seed, ts=stamp, ch=ch,
                           problems=problems, targets=targets, tol_ladder=tol_ladder,
                           max_attempts=max_attempts, n_max=n_max,
                           bisect_probes=bisect_probes)
        validate_record(rec)
        skip.add(rec["key"])
        if write:
            append_record(rec)
        out.append(rec)
        log("lanesearch_candidate", lane=lane, cycle=int(cycle), index=rec["index"],
            key=rec["key"], record_hash=rec["record_hash"],
            worst_status=rec["score"]["worst_status"],
            targets_reached=rec["score"]["targets_reached"])
    if write and out:
        update_elites(lane, out, ch=ch)
    assert status in STEP_STATUSES, status
    log("lanesearch_done", lane=lane, cycle=int(cycle), status=status,
        candidates=len(out), examined=examined, next_index=index,
        elapsed_s=round(time.monotonic() - started, 3))
    return out


def adaptive_step(seed: int = 0, budget_seconds: float = 0.0, cycle: int = 0,
                  stop: Callable[[], bool] | None = None, **kw) -> list[dict]:
    """The adaptive lane's open-ended work. See `step`."""
    return step("adaptive", seed, budget_seconds, cycle, stop, **kw)


def implicit_step(seed: int = 0, budget_seconds: float = 0.0, cycle: int = 0,
                  stop: Callable[[], bool] | None = None, **kw) -> list[dict]:
    """The implicit lane's open-ended work. See `step`."""
    return step("implicit", seed, budget_seconds, cycle, stop, **kw)


STEP: dict[str, Callable[..., list[dict]]] = {
    "adaptive": adaptive_step,
    "implicit": implicit_step,
}


# --------------------------------------------------------------------------- status and cli

def status(lane: str) -> dict:
    """What this lane's archive holds, without opening a daily file."""
    _check_lane(lane)
    cur = code_hash()
    led = load_ledger(lane)
    mine = [e for e in led if e.get("lanesearch_code_hash") == cur]
    return {
        "lane": lane,
        "lanesearch_code_hash": cur,
        "ledger_lines": len(led),
        "measured_under_current_code": len(mine),
        "measured_under_other_code": len(led) - len(mine),
        "next_index": next_index(lane, cur, led),
        "dir": str(lane_dir(lane)),
        "elites": str(elites_path(lane)),
        "not_comparable": NOT_COMPARABLE,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Open-ended lane search into the parallel unpinned archives. "
                    "Nothing here touches the scored archive.")
    ap.add_argument("--lane", choices=list(LANES), default="adaptive")
    ap.add_argument("--status", action="store_true", help="print the lane status and stop")
    ap.add_argument("--budget", type=float, default=0.0,
                    help="seconds; gates starting a candidate, not finishing one")
    ap.add_argument("--cycle", type=int, default=0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--start", type=int, default=None,
                    help="enumeration index to start at (default: resume from the ledger)")
    ap.add_argument("--max-candidates", type=int, default=MAX_CANDIDATES_PER_STEP)
    ap.add_argument("--problems", default=None,
                    help="comma separated validation problem names (default: all eight)")
    ap.add_argument("--dry-run", action="store_true",
                    help="measure but write nothing; print the records")
    a = ap.parse_args(argv)

    if a.status:
        print(json.dumps(status(a.lane), indent=1, sort_keys=True, allow_nan=False))
        return 0
    problems = None
    if a.problems:
        problems = [p.strip() for p in a.problems.split(",") if p.strip()]
        bad = sorted(set(problems) - set(V.VALIDATION_NAMES))
        if bad:
            print(f"unknown problems: {bad}", file=sys.stderr)
            return 2
    recs = step(a.lane, seed=a.seed, budget_seconds=a.budget, cycle=a.cycle,
                start=a.start, max_candidates=a.max_candidates,
                write=not a.dry_run, problems=problems)
    if a.dry_run:
        print(json.dumps(recs, indent=1, sort_keys=True, allow_nan=False))
    for rec in recs:
        s = rec["score"]
        print(f"{rec['lane']} index {rec['index']} key {rec['key']} "
              f"reached {s['targets_reached']}/{s['targets_total']} "
              f"worst {s['worst_status']} median {s['median_cycles_at_target']}")
    print(f"{len(recs)} candidates measured; {NOT_A_PAGE_SOURCE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
