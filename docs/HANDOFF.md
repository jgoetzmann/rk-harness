# FINAL HANDOFF — Quantization-Aware Runge–Kutta Discovery Harness

**Status:** frozen. Phase 0 interface freeze for fullsend.
**Supersedes:** HANDOFF v2. Six defects fixed; all listed in §0.
**Owner:** jgoetzmann · **Build:** 7 days · **Run:** ~Sep 20 → Dec 10 2026

Every numeric value here was computed and verified, not recalled. Sections 9–12
are fixtures with exact values — do **not** regenerate them, do **not** let an
agent invent them. They are external ground truth and they are the only thing
standing between this project and confidently wrong results.

---

## 0. What changed from v2, and why

Six defects found by executing the spec's own tests against reference
implementations. Every fix below is verified, not asserted.

| # | Defect | Fix |
| --- | --- | --- |
| 1 | **G7 failed on a correct implementation.** The v2 convergence rule (three finest ratios in `[1e-13,1e-2]`) leaves RK4 two usable points and measures `4.0942`, outside the `±0.05` v2 demanded. | Longest-consistent-slope-run selection (§4.7); tolerance `±0.10` at order 4. |
| 2 | **`Q15_INEXACT` rejected every classical method.** RK4 holds `1/6` and `1/3`; `kutta3` holds `2`, outside Q15's `[-1,1)` entirely. Test V1 requires all eight to pass, so v2 contradicted itself. | Coefficients get their own representation `m / 2^s` (§4.2b). Inexactness is recorded, never rejected. `COEFF_UNREPRESENTABLE` is a **range** check only. |
| 3 | **Dyadic ≠ free; the v2 cost column was wrong.** `3/8` has a power-of-two denominator but costs two shifts and an add. v2 counted it free. | Cost is driven by **CSD weight** (§4.2b, §4.5). `is_dyadic` stays correct for the impossibility theorem and is banned from cost. |
| 4 | **Test C1 was unimplementable.** It asked `cycle_count` to score an assembly file; the signature takes a `Tableau`. | New `count_sequence(ops, model)` (§4.5). |
| 5 | **The AVR cost model is structurally wrong.** `CostModel` assumes op cost is width-independent — true on M0+, false on an 8-bit core where a 16×16 multiply is four `MUL`s and a 15-bit shift costs ~15 cycles. | AVR **dropped from primary results** (§4.5). The two M0+ multiplier variants carry target-dependence on identical ISA, a cleaner claim. |
| 6 | **Four modules had no interface** (`runner`, `ledger`, `sitegen`, `dashboard`) despite appearing in the build order. | Specified in §4.12. |

Three additions follow: the anchor result (§9.5), Phase 0 as exhaustive
enumeration rather than search (§8), and predicate-grammar disambiguation (§6).

---

## 1. The project

Search for explicit Runge–Kutta tableaus minimizing **end-to-end error in Q15
fixed-point arithmetic at a fixed cycle budget on Cortex-M0+**, rather than
asymptotic truncation error in exact arithmetic.

**Falsifiable claim:** classical tableaus were derived assuming exact real
arithmetic. In Q15 on an FPU-less MCU at practical step sizes, roundoff dominates
truncation, so classically-optimal coefficients are not practically-optimal, and
nobody has searched the gap.

**Scope lock.** All search budget goes to Q15 / Cortex-M0+. Breadth (other
formats, targets, orders) is recorded as archive columns, **never** a search
direction.

**Permanently out of scope:** adaptive step size, Newton-iteration implicit
methods, order > 4, linear multistep. The first two break the execution-time
certificate hard real-time requires; they are wrong for the target application,
not merely unaffordable.

**Prior art and the gap.** RKTK (Zhang 2019) already does unstructured numerical
search over Butcher order conditions and holds a stage-count record at order 10.
Numerical search over tableaus is not the contribution. That line of work
optimizes order, stages, and error constants. Nobody optimizes against a hardware
cost model, and nobody evaluates in fixed-point. That is the gap.

---

## 2. Repositories

| Repo | Contents | Container access |
| --- | --- | --- |
| `rk-harness` | Orchestrator, verifier, evaluator, cost model, tests, fixtures | **read-only** |
| `rk-work` | Archive JSONL, hypothesis ledger, run logs, quarantine | read-write |
| `rk-findings` | Generated Pages site | read-write |
| `rk-overview` | Human-written explainer | not touched |

`rk-harness` is read-only because it holds the verifier. If the agent can edit
the scorer, the shortest path to a high score is editing the scorer, and it will
look like a plausible refactor in the diff.

### 2.1 Bootstrap — `scripts/bootstrap.ps1`

```powershell
$ErrorActionPreference = "Stop"
gh auth status
foreach ($r in @("rk-harness","rk-work","rk-findings")) {
    gh repo create "jgoetzmann/$r" --public --clone
}
Set-Location rk-findings
New-Item -ItemType Directory -Force -Path docs | Out-Null
"# rk-findings" | Out-File -Encoding utf8 docs/index.md
git add -A; git commit -m "init"; git push
gh api -X POST "repos/jgoetzmann/rk-findings/pages" `
  -f "source[branch]=main" -f "source[path]=/docs"
Set-Location ..
```

**Acceptance:** all three repos cloneable; `https://jgoetzmann.github.io/rk-findings/`
returns 200 within 10 minutes.

### 2.2 Credentials — `.env`, gitignored, never in the image

```
GITHUB_TOKEN=<fine-grained PAT: write to rk-work + rk-findings ONLY>
OPENAI_API_KEY=<key with hard monthly cap set in the OpenAI dashboard>
OPENAI_MONTHLY_CAP_USD=50
```

The PAT must **not** reach `rk-harness`. Verify by actually running
`gh api repos/jgoetzmann/rk-harness --method PATCH -f description=x` with that
token; it must return 403. This is test K5, not a suggestion.

Codex OAuth needs a browser. Authenticate on the Windows host, then mount
`~/.codex/auth.json` read-only. It will not work inside the container.

---

## 3. Type definitions

Every type used anywhere in this spec. Nothing else may be invented.

```python
from dataclasses import dataclass
from fractions import Fraction
from typing import Callable, Iterator, Literal

Q15 = int                      # int16 domain [-32768, 32767], scale 2**-15
CostModelName = Literal["m0plus_fast", "m0plus_slow", "avr_approx"]
Tier = Literal["heldout_verified", "search_only", "unreplicated"]
Verdict = Literal["supported", "refuted", "inconclusive"]

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
    scale: float                # power of two, see §11
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
class ArchiveState:
    n_records: int
    last_cycle_id: int
    grids: dict[int, dict[tuple[int, int], Record]]   # order -> (stages,bucket) -> elite
    open_hypotheses: tuple[str, ...]
    refuted_hypotheses: tuple[str, ...]

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
```

---

## 4. Module interfaces

### 4.1 `rk_harness/tableau.py`

```python
def stages(t: Tableau) -> int
def row_sums_consistent(t: Tableau) -> bool      # exact equality, no tolerance
def is_explicit(t: Tableau) -> bool
def to_q15(t: Tableau) -> Q15Tableau
def content_hash(t: Tableau) -> str              # see §4.11
```

Coefficients are `fractions.Fraction`. **Never float.** Float coefficients are
the most likely source of a silent correctness bug in this project.

### 4.2 `rk_harness/fixedpoint.py`

```python
def q15_from_float(x: float) -> Q15               # round-half-to-even
def q15_to_float(q: Q15) -> float                 # q / 32768.0
def q15_mul(a: Q15, b: Q15) -> Q15                # (a*b) >> 15, arithmetic shift
def q15_add(a: Q15, b: Q15) -> Q15                # raises on out-of-range
class Q15OverflowError(Exception): ...
```

Exact semantics:

- **Multiply** is `(a * b) >> 15` with a Python arithmetic shift, which **floors
  toward negative infinity**. This matches ARM `ASRS`. It does **not** match C's
  `/` on negatives and does **not** match truncation toward zero. See §10 for the
  divergence vectors — getting this backwards biases every result in the project
  by a consistent amount.
- **Range check:** `q15_mul(-32768, -32768)` evaluates to `32768`, outside int16.
  Must raise `Q15OverflowError`. It is the only input pair that does.
- **Add** wraps in real hardware, but this implementation **raises** so overflow
  becomes a verifier rejection rather than a silent wrong answer.
- **No saturation.** Cortex-M0+ has no `SSAT`; saturating requires a
  compare-and-branch, destroying both branchlessness and exact cycle counting.

### 4.2b Coefficient representation — `rk_harness/coeffrep.py`

**States are Q15. Coefficients are not.** Q15 spans `[-1, 1)`. `rk4` contains `1`
and `kutta3` contains `2`; neither fits. Storing coefficients in Q15 is
impossible, and v2's `Q15_INEXACT` rejection would have rejected all eight
baselines that test V1 requires to pass.

Each coefficient is stored as an integer-and-shift pair, `value = m / 2**s`:

```
to_rep(x: Fraction, s_max=20, m_max=32767) -> CoeffRep
    Smallest s making m an exact integer with |m| <= m_max. If no exact s exists,
    the s minimizing |x - m/2**s|, with exact=False. Never raises.

csd_weight(m: int) -> int
    Non-adjacent-form weight: the minimum count of nonzero signed powers of two
    summing to m. NAF is canonical and minimal, so this is well defined.
```

Verified representations:

| Coefficient | `m / 2^s` | Exact | CSD weight |
| --- | --- | --- | --- |
| `1` | `1 / 2^0` | yes | 1 |
| `2` | `2 / 2^0` | yes | 1 |
| `-1` | `-1 / 2^0` | yes | 1 |
| `1/2` | `1 / 2^1` | yes | 1 |
| `1/4` | `1 / 2^2` | yes | 1 |
| `3/4` | `3 / 2^2` | yes | **2** |
| `3/8` | `3 / 2^3` | yes | **2** |
| `1/3` | `21845 / 2^16` | **no** | 8 |
| `1/6` | `21845 / 2^17` | **no** | 8 |
| `2/3` | `21845 / 2^15` | **no** | 8 |
| `-1/3` | `-21845 / 2^16` | **no** | 8 |

`3/4` and `3/8` are the rows that matter: exactly representable, power-of-two
denominators, and still **not free** — two shifts and an add each. That is the
distinction v2 got wrong.

Inexactness is a **recorded property** (`coeff_quant_error`), never a rejection.
Quantization error is part of what this project measures; rejecting it would
discard the entire baseline.

### 4.3 `rk_harness/orderconditions.py`

```python
def trees(order: int) -> list[tuple]      # rooted trees; () is the single node
def tree_order(t: tuple) -> int
def gamma(t: tuple) -> int
def residuals(t: Tableau, order: int) -> list[Fraction]
def achieved_order_symbolic(t: Tableau) -> int
def is_dyadic(x: Fraction) -> bool        # denominator is a power of two
def dyadic_order_bound() -> int           # returns 2
```

**Rooted tree formulation, stated exactly so there is no room to guess.** A tree
is a tuple of subtrees; `()` is the single node τ.

- `tree_order(()) = 1`; `tree_order(t) = 1 + sum(tree_order(s) for s in t)`
- `gamma(()) = 1`; `gamma(t) = tree_order(t) * prod(gamma(s) for s in t)`
- Internal weight: `g_()(i) = 1`, and for `t = (t1,…,tm)`,
  `g_t(i) = prod_k( sum_j A[i][j] * g_{t_k}(j) )`
- Elementary weight: `Phi(t) = sum_i b[i] * g_t(i)`
- Condition for tree `t`: `Phi(t) == Fraction(1, gamma(t))`

`residuals(t, order)` returns `Phi(tree) - 1/gamma(tree)` for every tree of order
≤ `order`, as exact `Fraction`. Tree counts are fixed and verified — §9.4. A
generator producing different counts is wrong.

**Dyadic impossibility.** Dyadic rationals are closed under + and ×. The order-3
condition for tree `((),())` is `sum_i b_i c_i^2 == 1/3`, and 1/3 is not dyadic.
Therefore **no explicit RK method of order ≥ 3 has all-dyadic coefficients.**
Order ≤ 2 does — Heun and midpoint are both fully dyadic (§9.1). The verifier
must reject order-≥3 all-dyadic candidates without evaluating them.

**Dyadic is not the same as free.** A dyadic rational has a power-of-two
denominator. A *free* coefficient — one costing a single shift — must be `0`,
`±1`, or `±2^-k`. `3/8` is dyadic and costs two shifts plus an add.

`is_dyadic` is the correct predicate for the impossibility theorem and the
**wrong** predicate for cost. Cost uses `csd_weight` (§4.2b) and nothing else.
`is_dyadic` must not appear anywhere in `costmodel.py`; test C9 greps for it.

### 4.4 `rk_harness/verifier.py`

```python
REJECT_CODES = frozenset({
    "NOT_EXPLICIT", "ROW_SUM_INCONSISTENT", "ORDER_NOT_MET",
    "DYADIC_IMPOSSIBLE", "COEFF_UNREPRESENTABLE", "Q15_OVERFLOW",
    "UNSTABLE", "NAN_OR_INF", "NO_ASYMPTOTIC_WINDOW",
})

def verify(t: Tableau, claimed_order: int) -> VerdictReason | None
```

`None` means pass. **Never raises. Never calls an LLM. Never opens a socket.
Never writes.** Check order:

1. `NOT_EXPLICIT` — any nonzero on or above the diagonal of A
2. `ROW_SUM_INCONSISTENT` — any `sum(A[i]) != c[i]` (exact)
3. `DYADIC_IMPOSSIBLE` — `claimed_order >= 3` and all coefficients dyadic
4. `ORDER_NOT_MET` — any residual up to `claimed_order` nonzero
5. `COEFF_UNREPRESENTABLE` — any coefficient with `|value| >= 32768`, or needing
   `s > 20` in `(m, s)` form. A **range** check, not an exactness check. Inexact
   coefficients are recorded in `coeff_quant_error` and pass — otherwise every
   classical baseline fails V1
6. `Q15_OVERFLOW` — any state or intermediate outside Q15 range on any problem at
   2× nominal amplitude, i.e. `overflow_margin <= 1.0`
7. `UNSTABLE` — real-axis stability extent > −0.5. A tunable knob; changing it
   invalidates prior records, so bump the schema version if you touch it
8. `NO_ASYMPTOTIC_WINDOW` — `measured_order` returns `None` (§4.7)
9. `NAN_OR_INF` — any non-finite value

Steps 1–5 are cheap and must run before 6–9.

### 4.5 `rk_harness/costmodel.py`

```python
M0PLUS_FAST = CostModel("m0plus_fast", {"mul":1,  "add":1,"shift":1,"load":2,"store":2})
M0PLUS_SLOW = CostModel("m0plus_slow", {"mul":32, "add":1,"shift":1,"load":2,"store":2})
AVR_APPROX  = CostModel("avr_approx",  {"mul":14, "add":2,"shift":8,"load":2,"store":2})

def cycle_count(t: Tableau, model: CostModel, n_states: int) -> int
def count_sequence(ops: list[str], model: CostModel) -> int
def emit_c(t: Tableau, n_states: int) -> str       # reference C, cross-check only
```

**Resolved ambiguity — this is analytic counting, not compile-and-disassemble.**
`cycle_count` is a pure function over the tableau. No compiler in the loop.
`emit_c` exists only so a human can cross-check the analytic model against real
`arm-none-eabi-gcc` output once, by hand, during review. It is never called
during a run and there is no toolchain in the container.

Counting rules. For coefficient `x` with `CoeffRep(m, s, exact, w)`:

| Coefficient | Cost |
| --- | --- |
| exactly `0` | term omitted entirely, 0 |
| exactly `±1` | 0 — a copy, and the sign folds into `ADDS`/`SUBS` |
| anything else | `min(csd_cost, mul_cost)` |

`csd_cost = w * shift + (w - 1) * add`
`mul_cost = mul + shift`

**Taking the minimum is the entire point.** On `m0plus_fast` (`mul = 1`) a
hardware multiply always wins and coefficient structure barely matters. On
`m0plus_slow` (`mul = 32`) the CSD expansion wins up to about `w = 10`, so
low-CSD-weight coefficients become dramatically cheaper. That gap is the thesis.

Per stage `i`, per state: one `load` of the accumulator; then for each nonzero
`A[i][j]`, the coefficient cost plus one `add`; then one `store`. Stages whose
row is entirely zero are skipped. The final combination consumes `b` identically.
Total scales linearly in `n_states`.

Derivative evaluation `f(y)` is **excluded** — identical across all methods at
the same stage count, so it only adds a constant offset.

Deterministic: same input, same output, always.

```
count_sequence(ops: list[str], model: CostModel) -> int
    Cycle count for a literal opcode list. Used only by test C1 against
    fixtures/known_sequence.s. Mapping:
      LDR -> load          STR -> store         MULS -> mul
      ASRS/LSLS/LSRS -> shift                   ADDS/SUBS -> add
```

`count_sequence` exists because C1 in v2 asked `cycle_count` to score an assembly
file, which its signature cannot do.

**`is_dyadic` must not appear in this module.** Test C9 greps for it. Dyadic is
the impossibility predicate; CSD weight is the cost predicate. Conflating them
is defect #3.

#### The AVR model is approximate and carries no headline result

`CostModel` maps an opcode class to a fixed cycle count, assuming cost is
independent of operand width. That holds on Cortex-M0+ (32-bit registers, Q15
operands fit in one register) and is **false on AVR**, an 8-bit core where:

- a 16×16→32 multiply is four `MUL`s plus accumulation, roughly 14 cycles
- shifts cost one cycle **per bit**, so a 15-bit shift is ~15 cycles, not 1
- a 16-bit add is two 8-bit adds

`AVR_APPROX` uses `mul=14, shift=8, add=2` as a rough correction, and the name
carries the caveat. It may appear as a third column on the findings site,
clearly labelled approximate. **No claim in the findings may rest on it.**

The two M0+ multiplier variants carry the target-dependence argument instead, and
they carry it better: same ISA, same compiler, same binary — only the silicon
multiplier differs. That is a cleaner claim than a cross-architecture comparison
built on a model this spec admits is wrong.

### 4.6 `rk_harness/problems.py`

```python
SEARCH_SET: tuple[Problem, ...]      # dahlquist, damped_osc, vanderpol_mild
HELDOUT_SET: tuple[Problem, ...]     # pendulum, dc_motor, rc_thermal, quaternion
QUARANTINE_SET: tuple[Problem, ...]  # LLM-authored, see §7
```

Full definitions with verified scale factors in §11.

**Structural rule:** `search.py` and everything it imports must have no code path
reading `HELDOUT_SET`. Enforced by test C3 and grep at review. Overfitting to the
test suite is the failure mode that would silently invalidate three months.

### 4.7 `rk_harness/evaluator.py`

```python
def evaluate(t: Tableau, budget_cycles: int) -> ScoreVector
def measured_order(t: Tableau) -> float
def stability_extents(t: Tableau) -> tuple[float, float]
```

Deterministic: same input, byte-identical output. No LLM, no network.

**Comparison at equal cycle budget, never equal step size.** For a method costing
`k` cycles per step and budget `B`, take `n = B // k` steps with `h = t_end / n`.
A 2-stage method gets twice the steps of a 4-stage method for the same cost;
comparing at equal `h` is the most common route to a wrong conclusion here.

`search_error` and `heldout_error` are the RMS of final-state error across each
set, in units of the reference solution's scale.

**Convergence study for `measured_order`:** float64, not Q15 — Q15 roundoff would
floor the observed order.

The v2 rule (three finest ratios in `[1e-13, 1e-2]`) is **broken**, and was
verified broken: on `dahlquist` it leaves RK4 two usable points and measures
`4.0942`, outside the `±0.05` tolerance G7 demanded. A correct implementation
would have failed the spec's own golden test.

Corrected rule — **longest consistent slope run:**

1. Evaluate at `n = 8 * 2^k` for `k = 0..11`.
2. Local slopes between consecutive halvings:
   `sl[i] = log(e[i]/e[i+1]) / log(h[i]/h[i+1])`.
3. Find the longest consecutive run with `max(sl) - min(sl) <= 0.08` where every
   error in the run exceeds `1e-12`.
4. Least-squares fit `log(err)` against `log(h)` over that run.
5. If no run of length ≥ 2 exists, return `None`; the verifier then rejects
   `NO_ASYMPTOTIC_WINDOW`.

This locates the asymptotic window instead of assuming where it sits. Report
`order_fit_points` alongside the value so a two-point fit is visible as such.

Verified on `dahlquist`, `t_end = 10`:

| Method | Measured | Points | Expected | Deviation |
| --- | --- | --- | --- | --- |
| `euler` | **0.9849** | 7 | 1 | 0.0151 |
| `heun2` | **2.0109** | 8 | 2 | 0.0109 |
| `kutta3` | **3.0404** | 5 | 3 | 0.0404 |
| `rk4` | **4.0706** | 3 | 4 | 0.0706 |

RK4 converges into the roundoff floor fast enough that only three points survive,
leaving mild pre-asymptotic contamination. **G7's tolerance is therefore `±0.10`,
not `±0.05`.** That still separates order 3 from 4 from 5 decisively; a broken
implementation reads 2.0 or 3.0, not 4.07.

**Stability extents:** `R(z) = 1 + sum_{k=1..s} z^k * (b^T A^{k-1} 1)`. Real
extent is the most negative `x` with `|R(t)| <= 1` for all `t` in `[x, 0]`;
imaginary is the largest `y` with `|R(iy)| <= 1` on `[0, y]`. Bisection, 4000
sample points, 200 iterations. Verified values in §9.3.

### 4.8 `rk_harness/archive.py`

```python
def append(r: Record) -> None                     # today's JSONL, write-then-fsync
def elites(order: int) -> dict[tuple[int,int], Record]
def replay() -> ArchiveState                      # discards partial trailing lines
def assign_tier(cand: ScoreVector, incumbent: ScoreVector | None) -> Tier
def cycle_bucket(cycles: int) -> int              # 0..7
```

MAP-Elites, one grid per order `p in {1,2,3,4}`. Descriptors: stage count (2–6)
and cycle bucket. Fitness `heldout_error`, lower better.

Cycle buckets, log-spaced, using `M0PLUS_FAST`:

| Bucket | Cycles |
| --- | --- |
| 0 | < 16 |
| 1 | 16–31 |
| 2 | 32–63 |
| 3 | 64–127 |
| 4 | 128–255 |
| 5 | 256–511 |
| 6 | 512–1023 |
| 7 | ≥ 1024 |

**Tier assignment is mechanical.** `assign_tier` is pure code; the strings
`heldout_verified`, `search_only`, `unreplicated` must never appear in any LLM
prompt template (test K8).

- `heldout_verified` — beats incumbent on **both** `search_error` and
  `heldout_error`, and improves on ≥ 2 problem families in `per_problem`
- `search_only` — beats on `search_error` but not `heldout_error`
- `unreplicated` — beats on exactly one problem family

Files: `archive/YYYY-MM-DD.jsonl`. Commit **completed days only**. Never commit a
file that mutates every cycle — 2.5M records in a rewritten file makes the repo
unusable.

### 4.9 `rk_harness/search.py`

```python
def cmaes_island(order: int, stages: int, seed: int,
                 constraints: dict, budget: int) -> Iterator[Tableau]
def migrate(islands: list[Island]) -> None
def free_parameters(stages: int) -> int           # len(A lower) + len(b)
```

CMA-ES over free parameters, order conditions as equality constraints via
quadratic penalty `1e6 * sum(r**2)`. 4 islands, migration every 50 generations,
multi-restart on stagnation.

**No LLM in this loop.** The space is ~6 continuous parameters with polynomial
constraints — it has gradients, unlike program space, so a classical optimizer
beats an LLM mutation operator decisively and for free.

Candidates are snapped to Q15-exact rationals before verification: each
coefficient rounds to the nearest `k/32768`.

### 4.10 `rk_harness/surrogate.py`

```python
def should_train(n_records: int) -> bool          # True at >= 5000
def features(t: Tableau) -> list[float]
def train(records: list[Record]) -> object
def predict(m: object, t: Tableau) -> float
def calibration_error(m: object, holdout: list[Record]) -> float
```

`sklearn.ensemble.HistGradientBoostingRegressor`, CPU, trains in seconds.
Predicts `heldout_error` from tableau structure; prefilters before spending
evaluation cycles. `calibration_error` goes on the dashboard — when the surrogate
stops calibrating, stop trusting it.

Features: stage count, `csd_weight_total`, `coeff_quant_error`, cycle counts
under `m0plus_fast` and `m0plus_slow`, `sum(b)`, `max(|A|)`, count of zeros in A,
count of coefficients with `csd_weight == 1`, `stability_real`,
`stability_imag`, achieved order.

`AVR_APPROX` cycles are excluded — feeding an admittedly-wrong cost model to the
surrogate would teach it to optimize for a fiction.

### 4.12 Remaining modules

Four modules appear in the build order and had no interface in v2.

```
rk_harness/runner.py
    run_cycle(state: RunState) -> RunState
        One idempotent cycle. Reads state, calls encourager, optionally calls the
        LLM, validates the directive, runs search, verifies, evaluates, appends,
        regenerates the site, commits, writes state. Never raises past the cycle
        boundary; any exception is logged and the cycle is abandoned cleanly.
    heartbeat() -> None
        Writes an ISO-8601 timestamp to /work/HEARTBEAT every 10 seconds from a
        daemon thread.
    load_state() -> RunState
        Reads RUNSTATE.json; on absence or corruption, rebuilds from archive replay.
    save_state(st: RunState) -> None
        Temp file then os.replace. Never writes in place.

rk_harness/ledger.py
    parse_predicate(src: str) -> Predicate
        Grammar in §6 only. Raises PredicateSyntaxError on anything else.
        Never uses eval, exec, or compile.
    evaluate_predicate(pr: Predicate, arch: ArchiveState) -> tuple[Verdict, int, float]
        Returns (verdict, n_samples, effect_size). Pure function.
    append_hypothesis(h: dict) -> None
    resolve_open(arch: ArchiveState, cycle_id: int) -> list[str]
        Returns ids resolved this cycle.

rk_harness/sitegen.py
    build(arch: ArchiveState, out_dir: Path) -> None
        Regenerates the whole site. Deterministic given the same archive.
    BANNED_WORDS = ("novel","first","beats","outperforms","breakthrough",
                    "proves","state-of-the-art","best-ever")
        build() raises if any appears in generated output. Test E4.

rk_harness/dashboard.py
    render(arch: ArchiveState, st: RunState) -> None
        rich-based TUI, reads the JSONL event stream. Read-only: never writes to
        the archive, never mutates state.
```

`runner.py` is the only module permitted to call the LLM. Everything else is
pure. Test K13 greps the import graph of every other module for `openai`.

### 4.11 Canonical hashing

```
canonical(t) = json.dumps({
  "A": [[f"{x.numerator}/{x.denominator}" for x in row] for row in t.A],
  "b": [f"{x.numerator}/{x.denominator}" for x in t.b],
  "c": [f"{x.numerator}/{x.denominator}" for x in t.c],
}, sort_keys=True, separators=(",", ":"))
content_hash(t) = sha256(canonical(t).encode("utf-8")).hexdigest()
```

Verifier hash: sha256 over the concatenation, in this exact order, of
`coeffrep.py`, `orderconditions.py`, `verifier.py`, `costmodel.py`,
`evaluator.py`, `problems.py`, `fixtures/classical.json`,
`fixtures/problems.json`, `fixtures/q15.json`, `fixtures/known_sequence.s`.

Ten files. `coeffrep.py` and the three fixture files are included because a
change to any of them silently changes every score in the archive.

---

## 5. Directive schema

The LLM returns exactly one JSON object per call. The runner validates before
anything downstream sees it. Malformed → discard, log, use the deterministic
fallback (search the emptiest cell in the current grid).

```json
{
  "directive_id": "D-0112",
  "hypothesis_id": "H-047",
  "target_order": 3,
  "stages": [3, 4],
  "constraints": {
    "force_zero": [[2, 0]],
    "dyadic_denominator_max": 16,
    "c_fixed": {"1": "1/2"},
    "b_nonneg": true
  },
  "islands": 4,
  "budget_minutes": 45,
  "rationale": "cell (3, bucket 4) empty; forcing a[2][0]=0 removes one multiply"
}
```

Validation rules, all mandatory:

- `target_order` ∈ {1,2,3,4}
- `stages` a list of ints in [2,6], length ≤ 3
- `force_zero` entries `[i,j]` with `0 <= j < i < max(stages)`
- `dyadic_denominator_max` a power of two in [2, 32768]
- `c_fixed` values parse as `Fraction`, keys are stage indices as strings
- `islands` ∈ [1,8]; `budget_minutes` ∈ [5,120]
- `rationale` ≤ 500 chars
- **Unknown keys are a rejection**, not ignored. Silent key-drop is how a
  directive quietly does something other than what the model intended.

The directive can only narrow the search. It cannot change the objective, the
problems, the cost model, or the tier rules.

---

## 6. Hypothesis ledger

`rk-work/hypotheses.jsonl`, append-only.

```json
{
  "id": "H-047",
  "cycle_proposed": 112,
  "statement": "Under M0PLUS_SLOW, best(p=3,s=4) beats best(p=4,s=4) at equal budget",
  "mechanism": "extra order buys less accuracy than extra multiplies cost",
  "control": "inequality should reverse under M0PLUS_FAST",
  "predicate": "slow.p3s4.heldout < slow.p4s4.heldout AND fast.p3s4.heldout > fast.p4s4.heldout",
  "min_samples": 200,
  "verdict": null,
  "n_samples": null,
  "effect_size": null,
  "resolved_cycle": null
}
```

**Predicate language.** A boolean expression over archive fields only:

```
expr    := term (("AND" | "OR") term)*
term    := field op field | field op number
field   := model "." cell "." metric
model   := "fast" | "slow" | "avr_approx"
cell    := "p" digit "s" digit
metric  := "heldout" | "search" | "cycles" | "order"
op      := "<" | ">" | "<=" | ">=" | "=="
```

Nothing else parses. No function calls, no arbitrary Python, no `eval`, no
`compile`. `parse_predicate` raises `PredicateSyntaxError` on anything outside
this grammar.

**`pXsY` resolution — v2 left this ambiguous.** A grid is indexed by
`(stages, cycle_bucket)`, so `p3s4` names up to eight cells, one per bucket. It
resolves to **the minimum `heldout_error` among all buckets at that stage count
in that order's grid**, i.e. the best method of that shape regardless of cost
bucket. If no record exists at that `(order, stages)`, the predicate is
`inconclusive`, never false. A missing cell is absence of evidence, and treating
it as refutation would let the model 'refute' hypotheses by never searching.

`n_samples` for a predicate is the **smallest** record count among the cells it
references.

**The verdict is computed by code, never written by the model.** The runner
evaluates the predicate against the archive after each cycle:

- `supported` — predicate true and `n_samples >= min_samples`
- `refuted` — predicate false and `n_samples >= min_samples`
- `inconclusive` — insufficient samples, or Cohen's d below 0.2 on the compared
  populations

`inconclusive` must be reachable. Without a third bucket everything drifts toward
confirmation.

**Every LLM call receives the full list of refuted hypotheses with verdicts.**
Without it the model re-proposes H-047 in slightly different words every eight
cycles for three months. It costs a few hundred tokens and it is the difference
between eighty days of progress and eighty days of one idea.

---

## 7. Quarantine for model-authored problems

The one place model-written code executes. If the encourager escalates to "we
need a harder problem," the LLM writes a derivative function.

Staged to `rk-work/quarantine/<name>.py`, **never** written into `problems.py`.
Admission requires all of:

1. **Determinism** — run twice, byte-identical output
2. **Import allowlist** — `math` only. No `os`, `sys`, `subprocess`, `socket`,
   `open`, no `import` inside the function body, no dunder access. **AST-checked,
   not regex-checked**
3. **Bounded time** — 10,000 evaluations in < 2 seconds
4. **Range** — all states within `[-1, 1)` at 2× nominal amplitude
5. **Reference** — analytic solution, or mpmath reference at `mp.dps = 30`
6. **Promotion gate** — RK4, Heun2, and midpoint must rank on the new problem in
   the same relative order they rank on existing held-out problems at equal cycle
   budget. A problem where Heun2 beats RK4 is suspicious and stays quarantined

Admitted problems join `HELDOUT_SET` only, never `SEARCH_SET`, so the optimizer
cannot be steered toward a problem the model invented. They run in shadow mode
for 10 cycles — evaluated and recorded, excluded from tier assignment — before
counting.

**If the model concludes the evaluator or cost model needs changing**, it writes
`rk-work/PROPOSAL.md` and stops. It cannot make the change. That is the human
gate on the one category of edit that could invalidate every result.

---

## 8. Encourager

```python
def next_action(state: RunState, arch: ArchiveState, now: datetime) -> Action
```

Pure function of state and time. Ladder, in order:

1. Cell improvement < 2% over 5 cycles → `SEARCH_CELL` on emptiest adjacent cell
2. Whole grid stalls → `WIDEN` (more stages, relax `dyadic_denominator_max`)
3. Still stalls → `HYPOTHESIZE`
4. Still stalls → `ADVANCE_PHASE`
5. `heldout_error − search_error` gap widening over 10 cycles → `ROTATE_PROBLEMS`

**Calendar rules, hard:**

- Before **2026-11-20**: may never return `PACKAGE` or `FREEZE`. Only redirect.
- On/after **2026-11-20**: `PACKAGE` — re-verify every record, re-run every
  `heldout_verified` entry, open no new directions.
- On/after **2026-12-05**: `FREEZE` — no new records accepted.

Phases, each inheriting the previous archive as seed. **The method changes with
the phase** — see §9.6.

| Phase | Space | Method |
| --- | --- | --- |
| 0 | order ≤ 2, stages 2–3, `s ≤ 6` | **exhaustive enumeration** |
| 1 | order ≤ 3, stages 3–4, `s ≤ 8` | **exhaustive enumeration** on a lattice |
| 2 | order ≤ 4, stages 4–5, `s ≤ 12` | CMA-ES, 4 islands |
| 3 | order ≤ 4, stages 4–6, `s ≤ 20` | CMA-ES, 4 islands |

Phase 0's space contains 16 valid points (§9.6). Running a stochastic optimizer
over sixteen points would report a discovery where a proof is available. When the
enumeration completes, the phase result is a **proof of optimality within the
enumerated space**, and the findings site must label it that way — a strictly
stronger claim than anything the search phases can produce.

Phase 1's lattice is enumerable but large; cap it at `1e8` candidates and fall
back to CMA-ES if the cap is exceeded, recording which happened.

---

## 9. FIXTURE — classical tableaus

`fixtures/classical.json`. **Computed and verified. Do not regenerate.**

### 9.1 Tableaus and properties

| Name | s | Order | Row sums | All dyadic | CSD total | quant err | fast | slow |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `euler` | 1 | 1 | ok | **yes** | 0 | 0 | **5** | **5** |
| `midpoint` | 2 | 2 | ok | **yes** | 1 | 0 | **11** | **11** |
| `heun2` | 2 | 2 | ok | **yes** | 2 | 0 | **13** | **13** |
| `ralston2` | 2 | 2 | ok | no | 11 | 1.017e-05 | **16** | **30** |
| `heun3` | 3 | 3 | ok | no | 19 | 1.017e-05 | **23** | **50** |
| `kutta3` | 3 | 3 | ok | no | 26 | 1.017e-05 | **26** | **65** |
| `rk4` | 4 | 4 | ok | no | 34 | 5.086e-06 | **33** | **85** |
| `rk38` | 4 | 4 | ok | no | 22 | 5.086e-06 | **36** | **64** |

Cycle counts at `n_states = 1` under §4.5 rules. v2's "non-dyadic mults" column
was **wrong** — it counted `3/8` free because its denominator is a power of two,
when `3/8` costs two shifts and an add. CSD weight is the correct driver (§4.2b).

`midpoint` and `heun2` are fully dyadic at order 2; nothing is fully dyadic at
order ≥ 3. That is the impossibility result appearing in data.

`midpoint` and `heun2` are fully dyadic at order 2; nothing is fully dyadic at
order ≥ 3. That pattern is the impossibility result showing up in data.

Coefficients, exact:

```
euler      A=[[0]]                                 b=[1]                    c=[0]
midpoint   A=[[0,0],[1/2,0]]                      b=[0,1]                  c=[0,1/2]
heun2      A=[[0,0],[1,0]]                        b=[1/2,1/2]              c=[0,1]
ralston2   A=[[0,0],[2/3,0]]                      b=[1/4,3/4]              c=[0,2/3]
kutta3     A=[[0,0,0],[1/2,0,0],[-1,2,0]]         b=[1/6,2/3,1/6]          c=[0,1/2,1]
heun3      A=[[0,0,0],[1/3,0,0],[0,2/3,0]]        b=[1/4,0,3/4]            c=[0,1/3,2/3]
rk4        A=[[0,0,0,0],[1/2,0,0,0],
              [0,1/2,0,0],[0,0,1,0]]              b=[1/6,1/3,1/3,1/6]      c=[0,1/2,1/2,1]
rk38       A=[[0,0,0,0],[1/3,0,0,0],
              [-1/3,1,0,0],[1,-1,1,0]]            b=[1/8,3/8,3/8,1/8]      c=[0,1/3,2/3,1]
```


### 9.2 RK4 residuals

`residuals(rk4, 4)` returns **8** values, all exactly `Fraction(0)`.

`residuals(rk4, 5)` order-5 subset returns **9** values, **all nonzero**:

```
1/120, 1/240, -1/240, 1/120, 1/80, -1/120, -1/240, 1/240, -1/120
```

If any is zero, the tree generator is broken and will silently accept anything.

### 9.3 Stability extents

Bisection, 4000 samples, 200 iterations.

| Method | R(z) coefficients | Real extent | Imag extent |
| --- | --- | --- | --- |
| `euler` | 1, 1 | **−2.000000** | **0** |
| `heun2` | 1, 1, 1/2 | **−2.000000** | **0** |
| `midpoint` | 1, 1, 1/2 | **−2.000000** | **0** |
| `kutta3` | 1, 1, 1/2, 1/6 | **−2.512745** | **1.732051** = √3 |
| `rk4` | 1, 1, 1/2, 1/6, 1/24 | **−2.785294** | **2.828427** = 2√2 |
| `rk38` | 1, 1, 1/2, 1/6, 1/24 | **−2.785294** | **2.828427** |

Euler, heun2, and midpoint have **zero** imaginary-axis stability — `|R(iy)| > 1`
for every `y != 0`. A bisection reports a small positive number like `0.0017`;
that is search resolution, not a real extent. Assert `< 0.01`, not equality.

`rk4` and `rk38` share a stability polynomial because every 4-stage order-4
explicit method does. Their costs differ (4 vs 2 non-dyadic mults). That pair is
the cleanest illustration of the project's thesis and belongs on the findings
site as an anchor example.

### 9.5 The anchor result — verified before any search runs

`rk4` and `rk38` are both 4-stage order-4 explicit methods, so they share a
stability polynomial exactly (§9.3). They differ only in coefficients.

| `n_states` | | `m0plus_fast` | `m0plus_slow` |
| --- | --- | --- | --- |
| 1 | `rk4` | **33** | 85 |
| 1 | `rk38` | 36 | **64** |
| 2 | `rk4` | **66** | 170 |
| 2 | `rk38` | 72 | **128** |
| 4 | `rk4` | **132** | 340 |
| 4 | `rk38` | 144 | **256** |

**The ordering reverses between two chips running the same instruction set.**
`rk4` wins by 8% on the fast multiplier; `rk38` wins by 25% on the slow one.
Same order, same stability region, same stage count — which method you should
ship depends on which multiplier your Cortex-M0+ shipped with.

The mechanism: `rk4`'s weights are `1/6, 1/3, 1/3, 1/6`, none exactly
representable and all of CSD weight 8. `rk38`'s are `1/8, 3/8, 3/8, 1/8`, all
exact with CSD weights `1, 2, 2, 1`. When a multiply costs 32 cycles, that
difference dominates; when it costs 1, `rk38`'s denser A matrix wins instead.

This is the project's thesis demonstrated in two textbook methods before a single
search candidate is generated. It is the opening example for the findings site,
and it is the strongest available check that the cost model measures something
real. **If an implementation does not reproduce these twelve numbers, the cost
model is wrong.** Test G21.

### 9.4 Tree counts — verified

| Order | Trees at this order | Cumulative conditions |
| --- | --- | --- |
| 1 | 1 | 1 |
| 2 | 1 | 2 |
| 3 | 2 | 4 |
| 4 | 4 | 8 |
| 5 | 9 | 17 |
| 6 | 20 | 37 |

OEIS A000081. A generator producing different counts is wrong; fail loudly.

### 9.6 Phase 0 is exhaustive, not a search — verified

At order 2 with 2 stages, the order conditions leave one free parameter:
`b2 = 1/(2*a21)`, `b1 = 1 - b2`. Enumerating `a21 = m / 2^s` over `s <= 6` and
`|m/2^s| <= 2` gives **256 candidate values**, of which exactly **16** yield
`b1, b2` both exactly representable.

Sixteen. That space is not searched, it is **enumerated**, and the result is a
proof of optimality rather than a search finding. Verified cheapest five under
`m0plus_slow`:

| slow cycles | `a21` | `b` |
| --- | --- | --- |
| **11** | `1/2` | `(0, 1)` — this is `midpoint` |
| 13 | `-1/2` | `(2, -1)` |
| 13 | `1` | `(1/2, 1/2)` — this is `heun2` |
| 13 | `1/4` | `(-1, 2)` |
| 15 | `-1` | `(3/2, -1/2)` |

So `midpoint` is **provably** the cheapest exactly-representable 2-stage order-2
method in this space, and the margin over `heun2` is 2 cycles. That is a real,
if modest, result available on day three.

**Phase 0 must therefore use exhaustive enumeration, not CMA-ES.** Running a
stochastic optimizer over sixteen points is absurd, and it would report a
"discovery" where a proof was available. Phase 1 (3 stages, order 3, two free
parameters) is enumerable over a coarse `(m, s)` lattice as well; CMA-ES only
becomes the right tool at Phase 2. See §8.

---

## 10. FIXTURE — Q15 vectors

`fixtures/q15.json`. **Computed and verified.**

| a | b | `q15_mul(a,b)` |
| --- | --- | --- |
| 16384 | 16384 | 8192 |
| 32767 | 32767 | 32766 |
| −16384 | 16384 | −8192 |
| −16384 | −16384 | 8192 |
| 8192 | 8192 | 2048 |
| 0 | 32767 | 0 |
| 1 | 1 | 0 |
| 32767 | 1 | 0 |
| −1 | −1 | 0 |
| 3 | 5 | 0 |
| −1 | 1 | **−1** |
| −1 | 32767 | **−1** |
| −3 | 5 | **−1** |
| −32767 | 1 | **−1** |
| −32768 | 32767 | −32767 |
| −32768 | −32768 | **raises Q15OverflowError** |

The last five rows are the whole point. Floor and truncate-toward-zero disagree
on every one of them:

| a | b | floor (correct) | trunc-toward-zero (wrong) |
| --- | --- | --- | --- |
| −1 | 1 | −1 | 0 |
| −1 | 32767 | −1 | 0 |
| −3 | 5 | −1 | 0 |
| −32767 | 1 | −1 | 0 |

Round-trip: max `|x − q15_to_float(q15_from_float(x))|` over 100,000 samples in
`[-1, 1)` is `1.526e-05`, bounded by `2^-15 = 3.052e-05`.

Additions that must raise: `q15_add(32767, 1)`, `q15_add(-32768, -1)`.

---

## 11. FIXTURE — problems

`fixtures/problems.json`. Peaks measured by float64 RK4 at 40,000 steps. Scale
factors are powers of two so state scaling is a shift.

### SEARCH_SET

**`dahlquist`** — `y' = -y`, `y(0) = 1`, `t_end = 10`, n=1, family `linear`
Analytic `y(t) = exp(-t)`. Peak 1.000000, **scale 2^-2 = 0.25**, max@2× 0.500

**`damped_osc`** — `x'' + 2ζωx' + ω²x = 0`, ζ=0.1, ω=1, `y0 = (1, 0)`,
`t_end = 40`, n=2, family `oscillatory`
Analytic `x(t) = e^{-ζωt}(cos(ω_d t) + (ζω/ω_d) sin(ω_d t))`, `ω_d = ω√(1-ζ²)`.
Peak 1.000000, **scale 2^-2 = 0.25**, max@2× 0.500

**`vanderpol_mild`** — `x'' = μ(1-x²)x' - x`, μ=0.5, `y0 = (1, 0)`, `t_end = 20`,
n=2, family `nonlinear`
Reference: mpmath DP at `mp.dps = 30`. Peak 2.226469,
**scale 2^-4 = 0.0625**, max@2× 0.278

### HELDOUT_SET

**`pendulum`** — `θ'' = -sin θ`, `y0 = (1.0 rad, 0)`, `t_end = 60`, n=2,
family `nonlinear`. Metric: energy drift `|E(t) - E(0)|`,
`E = ½ω² + (1 - cos θ)`. Peak 1.000000, **scale 2^-2 = 0.25**, max@2× 0.500

**`dc_motor`** — `di/dt = (-Ri - K_e ω + V)/L`, `dω/dt = (K_t i - Bω)/J`
R=2, L=0.5, K_e=K_t=0.1, B=0.02, J=0.02, V=1, `y0 = (0,0)`, `t_end = 5`, n=2,
family `linear`. Analytic via matrix exponential. Peak 1.996771,
**scale 2^-3 = 0.125**, max@2× 0.499

**`rc_thermal`** — `y' = Ay`, n=3, family `stiff`
```
A = [[-11, 10,  0],
     [  5, -6,  1],
     [  0,  2, -2]]
```
`y0 = (1,0,0)`, `t_end = 4`. Eigenvalues **−16.047758, −2.723435, −0.228807**;
stiffness ratio **70.137**. Analytic via matrix exponential.
Peak 1.000000, **scale 2^-2 = 0.25**, max@2× 0.500

**`quaternion`** — `q' = ½ Ω(ω) q`, body rates ω = (0.3, 0.2, 0.5),
`y0 = (1,0,0,0)`, `t_end = 30`, n=4, family `geometric`
```
q0' = ½(-wx·q1 - wy·q2 - wz·q3)
q1' = ½( wx·q0 + wz·q2 - wy·q3)
q2' = ½( wy·q0 - wz·q1 + wx·q3)
q3' = ½( wz·q0 + wy·q1 - wx·q2)
```
Metric: norm drift `| ||q|| - 1 |`. Peak 1.000000, **scale 2^-2 = 0.25**,
max@2× 0.500

---

## 12. FIXTURE — ARM cycle sequence

`fixtures/known_sequence.s`. One Q15 multiply-accumulate pair, straight-line,
branchless. Hand-counted against the ARMv6-M timing table.

```asm
    LDR   r0, [r4, #0]     @ 2
    LDR   r1, [r5, #0]     @ 2
    MULS  r0, r0, r1       @ 1   (32-cycle variant: 32)
    ASRS  r0, r0, #15      @ 1
    ADDS  r2, r2, r0       @ 1
    LSLS  r3, r3, #1       @ 1
    SUBS  r2, r2, r3       @ 1
    MULS  r3, r3, r1       @ 1   (32-cycle variant: 32)
    ASRS  r3, r3, #15      @ 1
    STR   r2, [r6, #0]     @ 2
```

| Model | Total cycles |
| --- | --- |
| `m0plus_fast` | **13** |
| `m0plus_slow` | **75** |

Test C1 asserts `count_sequence` reproduces both numbers exactly.

No AVR row: this is ARMv6-M assembly, and scoring it under an 8-bit cost model
would be meaningless. §4.5 explains why `AVR_APPROX` carries no headline result.

---

## 13. Container and host

Host: Acer Nitro AN515-57, i7-11800H (8C/16T), 32 GB installed / ~11 GB free,
Windows 11 Home, WSL2.

`C:\Users\jacob\.wslconfig`:
```
[wsl2]
memory=8GB
processors=8
swap=4GB
autoMemoryReclaim=gradual
sparseVhd=true
```

`autoMemoryReclaim` and `sparseVhd` both matter over 80 days — without them WSL2
hoards RAM it isn't using and the vhdx grows monotonically.

### 13.1 Dockerfile

```dockerfile
FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
      git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir \
      numpy==2.1.3 scipy==1.14.1 sympy==1.13.3 mpmath==1.3.0 \
      scikit-learn==1.5.2 cma==4.0.0 jsonschema==4.23.0
WORKDIR /work
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
```

No ARM toolchain — §4.5 resolved the cost model to analytic counting.

`entrypoint.sh` must, before anything else:
1. Recompute the verifier hash, compare to pinned value; mismatch → exit 1
2. Run golden tests G1–G20 and canaries K1–K2; any failure → exit 1
3. Only then start the runner

### 13.2 Run flags

```
docker run -d --name rk \
  --cpus=4 --memory=6g --pids-limit=512 --cpu-shares=256 \
  --tmpfs /scratch:size=2g \
  -v D:/rk/harness:/harness:ro \
  -v D:/rk/work:/work \
  -v ~/.codex/auth.json:/root/.codex/auth.json:ro \
  --env-file .env \
  --network rk-net \
  rk-harness:latest
```

No GPU flags. Integer CPU work; CMA-ES over six parameters and Q15 simulation
have no GPU component, and the surrogate trains on CPU in seconds.

`--cpu-shares=256` is a quarter of default weight, so the container loses CPU
contests against foreground work automatically.

### 13.3 Network allowlist

`rk-net` bridge, egress restricted to:

```
api.openai.com
github.com
api.github.com
codeload.github.com
pypi.org
files.pythonhosted.org
```

Everything else denied. Cheapest defense against a compromised dependency.

### 13.4 Kill switch — five triggers, all host-side

| Trigger | Action |
| --- | --- |
| Killfile `D:\rk\work\STOP` exists | graceful stop at cycle boundary |
| Heartbeat stale > 120s | `docker kill` |
| No verified candidate in 30 min | log alert, escalate to encourager |
| `spend_usd` > `OPENAI_MONTHLY_CAP_USD` | hard stop |
| Disk free on D: < 5 GB | hard stop |

### 13.5 Pause watchdog

Host PowerShell, polls every 10s. Non-container CPU > 50% for 30 consecutive
seconds → `docker pause rk`. Below 30% for 30s → `docker unpause`. Pause is
atomic; the process freezes exactly where it is. Because the cost model is
analytic, host load cannot corrupt the primary metric.

### 13.6 Crash recovery

Every cycle idempotent. Archive append is write-then-fsync. `RUNSTATE.json`
written to a temp file then `os.replace`. On start, replay the JSONL and discard
any partial trailing line. Hard power loss costs at most one cycle.

### 13.7 Laptop

Cap battery charge at **80%** in NitroSense before starting. Three months at 100%
on AC permanently degrades the cells; this is the only item here with
irreversible consequences. Elevate for airflow, Windows power profile Balanced,
NitroSense fans on auto. Put the vhdx and archive on **D:** — the pagefile is
already there, so it is a second volume, and this isolates 80 days of writes
from C:.

---

## 14. Acceptance criteria

CI runs all of these on every push. `entrypoint.sh` runs G and K at startup and
refuses to run on any failure.

### 14.1 Golden — the evaluator reproduces known truth

| ID | Test | Expected |
| --- | --- | --- |
| G1 | `achieved_order_symbolic(rk4)` | exactly `4` |
| G2 | `achieved_order_symbolic(heun2)` | exactly `2` |
| G3 | `achieved_order_symbolic(midpoint)` | exactly `2` |
| G4 | `achieved_order_symbolic(euler)` | exactly `1` |
| G5 | `len(residuals(rk4,4))`, all values | `8`, all `Fraction(0)` |
| G6 | order-5 residuals of `rk4` | the 9 values in §9.2, all nonzero |
| G7 | `measured_order(rk4)` | `4.00 ± 0.10` — verified **4.0706**, 3 fit points |
| G8 | `measured_order(heun2)` | `2.00 ± 0.05` — verified **2.0109**, 8 pts |
| G9 | `measured_order(kutta3)` | `3.00 ± 0.05` — verified **3.0404**, 5 pts |
| G10 | real extent, `rk4` | `−2.785294 ± 0.001` |
| G11 | imag extent, `rk4` | `2.828427 ± 0.001` |
| G12 | real extent, `euler` | `−2.000000 ± 0.001` |
| G13 | real extent, `heun2` | `−2.000000 ± 0.001` |
| G14 | imag extent, `euler` and `heun2` | `< 0.01` (zero; §9.3) |
| G15 | imag extent, `kutta3` | `1.732051 ± 0.001` |
| G16 | real extent, `kutta3` | `−2.512745 ± 0.001` |
| G17 | tree counts, orders 1–6 | `1,1,2,4,9,20` |
| G18 | `achieved_order_symbolic` for all 8 classical | matches §9.1 |
| G19 | `rk4` and `rk38` stability polynomials | identical |
| G20 | `csd_weight_total` for all 8 classical | matches §9.1 exactly |
| G21 | `cycle_count` for `rk4`/`rk38`, n∈{1,2,4}, fast and slow | all **12** values in §9.5 |
| G22 | `cycle_count` for all 8 classical, n=1 | matches §9.1 fast and slow columns |
| G23 | `measured_order(euler)` | `1.00 ± 0.05` — verified **0.9849**, 7 pts |
| G24 | `to_rep` on every row of §4.2b | `m`, `s`, `exact`, `csd_weight` all match |
| G25 | `csd_weight(3)` and `csd_weight(int(3/8 * 2**3))` | both **2** |
| G26 | Phase 0 enumeration | exactly **16** valid points; cheapest is `midpoint` at 11 |
| G27 | `order_fit_points` reported for all four convergence tests | 7, 8, 5, 3 |

G7 is load-bearing. If the evaluator cannot recover the textbook order of a
textbook method, nothing downstream means anything.

### 14.2 Fixed-point

| ID | Test | Expected |
| --- | --- | --- |
| F1–F16 | every row of the §10 table | exact match |
| F17 | the four floor-vs-trunc rows | floor result, differing from trunc |
| F18 | `q15_mul(-32768,-32768)` | raises `Q15OverflowError` |
| F19 | `q15_add(32767,1)`, `q15_add(-32768,-1)` | both raise |
| F20 | round-trip max error, 100k samples | `≤ 1.526e-05` |
| F21 | `q15_mul(x,0)` for 1000 random x | all `0` |

### 14.3 Verifier

| ID | Test | Expected |
| --- | --- | --- |
| V1 | all 8 tableaus in `fixtures/classical.json` | pass at their §9.1 order |
| V2 | `rk4` with `b[0] += 1/1000` | `ORDER_NOT_MET` |
| V3 | row sums ≠ c | `ROW_SUM_INCONSISTENT` |
| V4 | nonzero above diagonal | `NOT_EXPLICIT` |
| V5 | all-dyadic claiming order 3 | `DYADIC_IMPOSSIBLE`, **no evaluation** |
| V6 | `heun2` claiming order 2, all dyadic | **passes** |
| V7 | `midpoint` claiming order 2, all dyadic | **passes** |
| V8 | 10,000 random garbage inputs | never raises |
| V9 | candidate overflowing Q15 at 2× | `Q15_OVERFLOW` |
| V10 | coefficient `1/3` | **passes**, `coeff_quant_error` = 5.086e-06 |
| V10b | coefficient `40000` | `COEFF_UNREPRESENTABLE` |
| V10c | `kutta3` with `A[2][1] = 2` | **passes** — `2` is representable as `2/2^0` |
| V10d | tableau with no asymptotic window | `NO_ASYMPTOTIC_WINDOW` |
| V11 | dyadic reject fires before evaluation | assert no `evaluate` call |

V5–V7 encode the impossibility result: order ≤ 2 admits fully dyadic methods,
order ≥ 3 provably does not.

### 14.4 Cost model

| ID | Test | Expected |
| --- | --- | --- |
| C1 | `count_sequence` on `fixtures/known_sequence.s` | fast **13**, slow **75** |
| C2 | `rk4` fast vs slow | slow strictly greater |
| C3 | `b = 1/2` (w=1) vs `b = 16385/32768` (high w), slow model | low CSD strictly cheaper |
| C4 | coefficient `0` | 0 cycles |
| C5 | coefficient `1` | 0 cycles |
| C6 | `cycle_count` called twice | identical |
| C7 | `rk4` vs `rk38` under `m0plus_slow` | `rk38` cheaper: **64 < 85** |
| C7b | `rk4` vs `rk38` under `m0plus_fast` | `rk4` cheaper: **33 < 36** — reversal |
| C9 | grep `costmodel.py` for `is_dyadic` | **zero hits** (defect #3) |
| C10 | `cycle_count(t, m, n)` vs `n * cycle_count(t, m, 1)` | equal for all 8 classical |
| C8 | all 8 ranked under `AVR_APPROX` vs `M0PLUS_FAST` | both computed; result is advisory only |

### 14.5 Anti-hacking canaries

| ID | Test | Expected |
| --- | --- | --- |
| K1 | planted tableau tuned to SEARCH, bad on HELDOUT | tier `search_only`, never `heldout_verified` |
| K2 | planted single-family winner | tier `unreplicated` |
| K3 | verifier hash mismatch at startup | exit 1, loud error |
| K4 | write to `/harness` from inside container | fails EROFS |
| K5 | PAT write to `rk-harness` | HTTP 403 |
| K6 | `evaluate()` with network disabled | succeeds |
| K7 | archive record with model-supplied tier | schema rejection |
| K8 | grep LLM prompt templates for tier strings | zero hits |
| K9 | directive with an unknown key | rejected, not ignored |
| K10 | predicate containing `__import__` | parse failure |
| K11 | quarantine problem importing `os` | AST check rejects |
| K13 | grep import graph of every module except `runner.py` for `openai` | zero hits |
| K14 | `parse_predicate("__import__('os')")` | `PredicateSyntaxError` |
| K15 | `ledger.py` grep for `eval`, `exec`, `compile` | zero hits |
| K16 | predicate naming an empty cell | `inconclusive`, never `refuted` |
| K12 | `search.py` import graph | no path reaches `HELDOUT_SET` |

K1 is the most important test in this document. It catches the project silently
producing overfitted garbage for three months while the dashboard looks healthy.

### 14.6 Recovery

| ID | Test | Expected |
| --- | --- | --- |
| R1 | `docker kill -9` mid-cycle ×3, restart | ≤ 1 cycle lost each time |
| R2 | truncate last JSONL line, restart | partial discarded, no crash |
| R3 | `docker pause` 60s mid-evaluation | results byte-identical |
| R4 | delete `RUNSTATE.json` | rebuilds from replay |
| R5 | corrupt `RUNSTATE.json` | falls back to replay, warns |

### 14.7 End-to-end

| ID | Test | Expected |
| --- | --- | --- |
| E1 | 10-min run, fixed seed | ≥ 1 verified candidate. **Record the observed rate as the baseline; do not assert a threshold until it has been measured once.** An uncalibrated number here blocks the run for no reason |
| E2 | same run twice, same seed | byte-identical archive |
| E3 | site generator on fixture archive | valid HTML, every entry tiered |
| E4 | grep generated HTML | zero hits: novel/first/beats/outperforms/breakthrough |
| E5 | `next_action` before 2026-11-20, 1000 random states | never `PACKAGE` or `FREEZE` |
| E6 | clock at 2026-11-21 | returns `PACKAGE` |
| E7 | clock at 2026-12-06 | returns `FREEZE` |

E2 makes every other test meaningful. Without determinism you cannot distinguish
a real improvement from noise.

---

## 15. The falsification experiment — Day 2, before anything else

Implement `rk4` and `heun2` in Q15. Run on `damped_osc`. Measure:

1. Fraction of cycle count on coefficient arithmetic vs derivative evaluation
2. Sweep `h`; find where roundoff error overtakes truncation error

**Kill criterion:** coefficient arithmetic < 15% of cycles AND no roundoff-
dominated regime at practical `h`. Stop the project. You have a reusable
benchmark and you saved three months.

**Proceed criterion:** coefficient arithmetic ≥ 30% AND a clear crossover at `h`
values a real controller would use.

Write the result into `rk-findings` either way, with the numbers. A clean
negative is a real finding.

---

## 16. Build order

| Day | Deliverable | Fullsend |
| --- | --- | --- |
| 1 | `fixedpoint.py`, `coeffrep.py`, `costmodel.py`; F1–F21, C1–C10, G24–G25 | yes |
| 2 | `orderconditions.py`, `evaluator.py`; G1–G23, G27. **Run §15.** | yes |
| 3 | `verifier.py`, `archive.py`; V1–V11, K1–K2, K7–K8. **Run §9.6 enumeration** — it produces a real result in an hour | yes |
| 4 | `search.py`, islands, held-out split; K12 | yes |
| 5 | Dockerfile, wrapper, kill switch, watchdog; R1–R5 | **no** |
| 6 | `runner.py`, `ledger.py`, Codex integration, `encourager.py`, quarantine; K13–K16 | **no** |
| 7 | `dashboard.py`, `sitegen.py`; E1–E7 | yes |

Two checkpoints produce publishable output before the harness is finished: the
§15 falsification result on Day 2 and the §9.6 enumeration proof on Day 3. If the
week runs out after Day 3 you still have something real.

### 16.1 What not to fullsend

Fullsend's own guidance excludes *money, auth, PII, migrations, or shared
infrastructure*. That maps to: credential handling, container wrapper and mount
flags, kill switch and spend counter, verifier hash check, quarantine AST
checker, and `ledger.parse_predicate`. Hand-write these and read them line by
line — roughly 450 lines total.

`parse_predicate` joins the list because it parses untrusted model output. A
generated parser that quietly falls back to `eval` is an arbitrary-code-execution
path wearing a grammar as a disguise.

Fullsending the numerical core is safe **only because §9–§12 contain external
ground truth**. Spec-derived tests would just ratify the spec. The golden values
are what give the test agents real truth to check against.

### 16.2 Invocation

```
/fullsend

Fullsend this. Spec is docs/HANDOFF.md.
Sections 3, 4, 5, 6 are the interface freeze — do not deviate.
Sections 9-12 are verified fixtures — copy verbatim, never regenerate.
Section 14 is the test suite.
Section 0 lists six bugs fixed from v2; do not reintroduce them.
Skip everything listed in 16.1; those are hand-written.
```

---

## 17. Site generation

`rk-findings/docs/`, regenerated every cycle from the archive.

Pages: index (grid tables per order), per-cell detail, hypothesis ledger with
verdicts, cost-model comparison, and the §15 falsification result.

**Auto-publish rules:**

1. Numbers and mechanically generated captions only. Never prose claims of
   novelty, priority, or significance. `sitegen.BANNED_WORDS` is enforced at
   build time and `build()` raises on a hit.
2. Every entry carries its tier badge, assigned by `assign_tier`, never a model.
3. Every entry links to its `tableau_hash` and the `verifier_hash` that produced it.
4. Banner on every page: *"Automatically generated. Not reviewed by a human.
   See rk-overview for interpretation."*
5. Results derived from `AVR_APPROX` carry an additional inline note: *"Cost
   model approximate; see HANDOFF §4.5."*
6. Phase 0 and Phase 1 results are labelled **"exhaustive — optimal within the
   enumerated space"**; Phase 2 and 3 results are labelled **"search result"**.
   The distinction is the difference between a proof and a best-so-far.

An auto-published wrong result should be a wrong number in a table, not a public
claim requiring retraction.

---

## 18. Dashboard

Terminal UI (`rich`), rendered from the JSONL event stream so the same stream
also generates the site. One source of truth.

Panels: cycle/phase/uptime and spend · verified count, records set,
candidates/hour, spend remaining · per-cell best vs classical baseline at equal
budget · health (verifier hash, reject rate, escalations, disk) · promotion (grid
coverage, gain over last 5, stall counter) · **held-out gap** · surrogate
calibration error or "not yet trained (need 5000, have N)" · open and refuted
hypothesis counts · recent events tail.

During Phase 0 and 1 the candidates panel shows **enumeration progress** (points
visited / total) rather than a rate, because the space is finite and the
completion point is known. This is the one place a progress bar is honest.

**No progress bar.** There is no known endpoint, and a bar filling toward nothing
trains you to ignore the display. The records-set counter and the timestamp of
the last discovery are the honest signals.

Every panel answers "is it stuck?" differently: candidates/hour catches a crashed
loop, reject rate catches a broken search space, stall counter catches
convergence, escalations catch quota burn, verifier hash catches reward hacking,
held-out gap catches overfitting.
