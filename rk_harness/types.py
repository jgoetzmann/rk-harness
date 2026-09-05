"""Type definitions — HANDOFF §3, verbatim. Nothing else may be invented,
except CellStat (per-cell running statistics needed by the hypothesis ledger)."""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Callable, Literal

Q15 = int                      # int16 domain [-32768, 32767], scale 2**-15
CostModelName = Literal["m0plus_fast", "m0plus_slow", "avr_approx"]
Tier = Literal["heldout_verified", "search_only", "unreplicated"]
Verdict = Literal["supported", "refuted", "inconclusive"]

TIERS: tuple[str, ...] = ("heldout_verified", "search_only", "unreplicated")
VERDICTS: tuple[str, ...] = ("supported", "refuted", "inconclusive")
COST_MODEL_NAMES: tuple[str, ...] = ("m0plus_fast", "m0plus_slow", "avr_approx")


@dataclass(frozen=True)
class Tableau:
    A: tuple[tuple[Fraction, ...], ...]      # square, strictly lower triangular
    b: tuple[Fraction, ...]
    c: tuple[Fraction, ...]


@dataclass(frozen=True)
class Q15Tableau:
    A: tuple[tuple[Q15, ...], ...]
    b: tuple[Q15, ...]
    c: tuple[Q15, ...]
    exact: bool                 # True iff every Fraction was exactly representable


@dataclass(frozen=True)
class CostModel:
    name: CostModelName
    cycles: dict[str, int]      # keys: mul, add, shift, load, store


@dataclass(frozen=True)
class Problem:
    name: str
    n_states: int
    f: Callable[[float, tuple[Q15, ...]], tuple[Q15, ...]]
    y0: tuple[Q15, ...]
    t_end: float
    scale: float                # power of two, see HANDOFF §11
    reference: Callable[[float], tuple[float, ...]]
    family: Literal["linear", "oscillatory", "nonlinear", "stiff", "geometric"]


@dataclass(frozen=True)
class ScoreVector:
    measured_order: float | None   # None when no asymptotic window exists
    order_fit_points: int          # points in the fitted slope run
    error_constant: float          # L2 norm of residuals at achieved_order+1
    stability_real: float
    stability_imag: float
    cycles: dict[CostModelName, int]
    csd_weight_total: int          # sum of CSD weights over non-trivial coefficients
    coeff_quant_error: float       # max |exact - m/2**s| over all coefficients
    search_error: float
    heldout_error: float
    overflow_margin: float         # 1.0 / max|state| at 2x amplitude; must exceed 1.0
    per_problem: dict[str, float]


@dataclass(frozen=True)
class CoeffRep:
    m: int                         # signed integer, |m| <= 32767
    s: int                         # shift, 0 <= s <= 20; value = m / 2**s
    exact: bool
    csd_weight: int


@dataclass(frozen=True)
class VerdictReason:
    code: str
    detail: str


@dataclass(frozen=True)
class Record:
    tableau_hash: str
    tableau: Tableau
    score: ScoreVector
    tier: Tier
    cycle_id: int
    seed: int
    verifier_hash: str
    directive_id: str | None
    hypothesis_id: str | None
    timestamp: str              # ISO 8601 UTC


@dataclass(frozen=True)
class Island:
    island_id: int
    order: int
    stages: int
    seed: int
    generation: int
    best: Record | None


@dataclass(frozen=True)
class CellStat:
    """Running statistics of one metric over every record in one (order, stages) cell.
    Welford form: n, mean, m2 (sum of squared deviations), and the minimum seen."""
    n: int
    mean: float
    m2: float
    min: float


@dataclass(frozen=True)
class ArchiveState:
    n_records: int
    last_cycle_id: int
    grids: dict[int, dict[tuple[int, int], Record]]   # order -> (stages,bucket) -> elite
    open_hypotheses: tuple[str, ...]
    refuted_hypotheses: tuple[str, ...]
    # (order, stages) -> "<model>.<metric>" -> CellStat.  model in fast|slow|avr_approx,
    # metric in heldout|search|cycles|order.  Filled by archive.replay().
    cell_stats: dict[tuple[int, int], dict[str, CellStat]] = field(default_factory=dict)
    # Every archived tableau_hash. Carried here so a cycle can skip candidates it has
    # already seen without a second full pass over the archive; replay() has the records
    # in hand anyway. Filled by archive.replay() and maintained by archive.fold().
    record_hashes: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class RunState:
    cycle_id: int
    phase: int
    started_at: str
    last_heartbeat: str
    spend_usd: float
    stall_counter: int
    current_cell: tuple[int, int] | None


@dataclass(frozen=True)
class Action:
    kind: Literal["SEARCH_CELL", "WIDEN", "HYPOTHESIZE",
                  "ADVANCE_PHASE", "ROTATE_PROBLEMS", "PACKAGE", "FREEZE"]
    payload: dict
