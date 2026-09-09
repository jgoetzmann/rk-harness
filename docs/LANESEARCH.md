# Lane search: open-ended work for adaptive and implicit (rk_harness/lanesearch.py)

Status: **driven by the cycle loop**. `runner._run_lane_cycle` calls `step` once per
adaptive or implicit cycle with a `run.lane_max_seconds` budget (D37, D38). `watch.py` is
the only other caller and it may call the read-only surface only; the gate in
`tests/test_t16_lanesearch.py` holds that line by name and by function, and asserts no
viewer can reach `step` even now that the runner can.

No page reads either archive. The records still carry `NOT_A_PAGE_SOURCE` and the
traceability list in CLAUDE.md rule 11 still does not name them, so a class page reports
that an archive exists and quotes no number out of it. This note says what is in the tree,
what the two archives are and are not, and what would have to be decided before a number
from one could be published.

## Why it exists

`docs/LANES.md` ends with the blocker: do not arm a lane rotation until the two lanes have
open-ended work. The side-track catalogue is complete, 103 of 103 points measured under the
current code hash, and every firing since cycle 2560 has logged `sidetrack_exhausted` in
about five hundredths of a second. Explicit has CMA-ES, a continuous space that never runs
out, so it can absorb any budget forever. Adaptive and implicit had a finite plan. Arming
thirds against that would hand two lanes a third of the machine and an empty queue.

This module is the queue. Each lane gets a deterministic, unbounded enumeration, an
evaluation on the shared axis-T metric, and an archive of what came back.

## What the two archives are not

This is the part to read before quoting a number out of one. A record in
`rk-work/adaptive_archive/` or `rk-work/implicit_archive/` is **not comparable** with a
record in `rk-work/archive/`, on four separate counts:

| | scored archive | lane archive |
| --- | --- | --- |
| question | what error a fixed 65,536-cycle budget buys | what cycles a fixed error costs |
| problems | the seven frozen ones in `problems.py` | the eight application ones in `validation.py` |
| arithmetic | Q15 through the pinned `solve_q15` | float64 throughout |
| checking | `verifier.py`, a cell, a `Score` | none of those |

The incomparability rides on every record as `not_comparable` and on the elites document as
`_meta.not_comparable`, in a field rather than in a comment, because two archives that look
alike and mean different things is how a published number goes wrong.

Neither archive is on the traceability rule's list of public-number sources
(key_findings.json, validation/results.json, benchmark/results.json, the side-track ledger
with its artifacts). Every record says so in `not_a_page_source`. Adding one to that list is
a deliberate decision for the owner.

## The adaptive space

An embedded pair plus a controller.

The pair comes from the dyadic lattice `pair_census` counts over: a strictly lower
triangular A of dyadic entries, c the row sums, b the order-3 particular solution of
`orderconditions.b_linear_system`, and b_hat the order-2 particular solution. A candidate is
admitted when both systems are consistent, the order-2 system has a free column so that
d = b - b_hat is not forced to zero, and the estimate is genuinely lower order than the
propagated formula. Exact `CoeffRep` representability is recorded as `b_exact` and is
deliberately not a filter: an order-3 b carries a denominator with a factor of three unless
c cancels it, and BS32's own b = (2/9, 1/3, 4/9, 0) is not dyadic either, so requiring
exactness admits nothing at all. That was measured, not assumed.

The controller is (alpha, beta, safety) over a dyadic set of 60 triples, passed straight to
`adaptive.solve_adaptive`. The reference gains lead the set: alpha 1/4, beta 1/8, safety 7/8
are the prototype defaults and what the frozen curve was measured at, so the leading pass
over a shell is every pair at gains already studied and the controller sweep comes after it.
The factor clamp and the Q15 factor table are carried on the record but not searched,
because `solve_adaptive` does not take them and this module does not edit a prototype.

The run is float64. `adaptive_q15.solve_adaptive_q15` is hard-wired to BS32 through
`A_REPS`, `B_REPS` and `D_REPS`, so a candidate pair has no Q15 run available at all.

## The implicit space

`(gamma, a21)` over a dyadic grid, times a Newton iteration count, times a Jacobian policy.
`sdirk.order2_tableau_exact_a21` solves b and c exactly over Fractions with the stability
algebra attached, so every candidate arrives with `r_at_infinity`, `a_stable` and
`l_stable` already exact.

One fact falls out of that construction and is stated on every record: **no dyadic gamma is
L-stable**. R at infinity vanishing needs `1/2 - 2g + g*g = 0`, whose root is irrational, so
`l_stable` is false for every candidate this lane can construct. `r_at_infinity` is the
margin to read instead, and A-stability is the gate that is actually reachable. A-stability
is recorded rather than enforced: a candidate that is not A-stable simply fails on the stiff
problems and ranks low, which is data.

There is no Q15 implicit run either, for a different reason: `fixedpoint.py` has no
reciprocal, so there is no Q15 LU.

## The cost numbers, and which grade each one is

Two grades sit in these archives and every record says which it is.

`float_trajectory_q15_attempt_cost`, adaptive. `attempts * per_attempt_total`, where
`per_attempt_total` is the stage work of the propagating formula through the pinned
`cycle_count` (legitimate: a pair's A is strictly lower triangular, so the pinned function
is used inside its own contract), plus one priced block of the Q15 estimate listing per
nonzero d term per state, plus one controller update, charged on every attempt whether it
was accepted or rejected. It is a mixed number and is labelled as one: the attempt count
comes from a float64 controller and the price of an attempt comes from the Q15 model. A Q15
run of the same pair would reject more attempts than the float run does, so the attempt
count is the optimistic half and the per-attempt price, with its worst-case bit scan and its
unpriced branches, is the pessimistic half.

For BS32, which has four nonzero d terms, `pair_attempt_cost` returns exactly what
`validation_axes.adaptive_attempt_cost` returns. That equality is a test, not a claim: it is
the one candidate both modules can price, and the estimate block is sliced out of
`adaptive_q15.reference_sections` rather than retyped, so a change to the listing moves both
numbers together.

`design_estimate_div32`, implicit. `sdirk.estimate_sdirk2_cycles` unchanged, resting on
`DIV_CYCLES = 32`, a stated guess for a software reciprocal that the cost model does not
price at all.

Neither is `assembly_verified`. The explicit archive's number is; these are not, and the
`cost_bases` block in the elites document says so in full.

## Work caps are counts, never clocks

`budget_seconds` gates STARTING a candidate, not finishing one, exactly as
`sidetrack.run_until` does, so a firing overruns by at most one candidate. A zero budget
measures exactly one candidate rather than none, which is what makes a lane always produce
something.

Every candidate is bounded by counts of its own, which is what keeps a record a pure
function of `(code, candidate, params)`: `max_attempts` (20,000, below `adaptive_q15`'s
50,000 because a firing pays it once per rung per problem), `n_max` (4,096) and
`bisect_probes` (8) for the implicit ladder, and `diverge_at` as a bound on the data. The
firing itself is bounded by `SCAN_CAP`, indices examined, and `MAX_CANDIDATES_PER_STEP`.
None of these is a duration. `params.work_cap` carries all of them on every record.

## Measured cost

Measured on this host on 2026-09-09, running the shipped `step()` at the default work caps
over all eight problems and all four targets, references warm: **0.54 s per adaptive
candidate** (12 candidates in 6.5 s) and **2.06 s per implicit candidate** (6 in 12.4 s).
Warming the reference solutions costs about 38 s in a fresh process and is paid once by
`functools.lru_cache` inside validation, which makes it the dominant cost of a short firing
rather than of a long one.

At a 100 s lane budget that is roughly 180 adaptive candidates or 48 implicit candidates per
lane cycle, against explicit's roughly 10,000 tableaus a day. Equal time will not mean equal
candidate count, and the sites must not present the two counts side by side as though they
were the same kind of number.

## The archive layout

Per lane, under `rk-work/<lane>_archive/`:

* `YYYY-MM-DD.jsonl`, the full records, one per line, schema `adaptive-record/1` or
  `implicit-record/1`. UTC dates, from the record's own stamp.
* `ledger.jsonl`, one short line per record: stamp, cycle, index, key, hashes, code hash,
  file, worst status, targets reached, median cycles. Same split `sidetrack` keeps between
  its ledger and its artifacts, and for the same reason: the cursor and the duplicate check
  read only this file, so the cost of a firing does not grow with the archive.
* `elites.json`, schema `lane-elites/1`, capped at 32 entries.

The record is written before its ledger line, so a crash can leave a record nothing points
at and never a ledger line with no record behind it.

`lanesearch` refuses to write under any of `preflight.SCORED_PATHS`. The list is duplicated
in the module because `scripts/` is not an importable package and a guard that cannot run is
not a guard; a test asserts the copy still matches preflight's.

## The elite rule

Lowest median cycles at the elite target (2^-10) across the problems where the status is
`reached`. A candidate that reached the elite target nowhere ranks below every candidate
that reached it somewhere. Ties break by the count of targets reached, highest leading, then
by `record_hash`. No clock is read and nothing is sampled, so the order is total and two
runs over the same records agree. `rank()` deduplicates by `record_hash` and is invariant
under the input order, which is what makes merging a firing into an existing elites document
deterministic.

`generated_ts` defaults to the newest ranked record's own stamp rather than to the clock, so
two builds over the same records are byte-identical and a page built from one cannot move
while its inputs stand still.

The document keeps three counts apart because they answer different questions. `n_input` is
what was handed to the build, `n_ranked` is how many of those carried the current code hash
and entered the order, and `n_measured` is what the ledger says the lane has measured under
that hash. Once the cap bites, `n_measured` is the larger number, and folding them into one
would let a capped document claim the archive is smaller than it is.

## Resuming, and why coarse values repeat

The enumeration walks shells of growing lattice resolution, so there is no last candidate. A
coarse dyadic reappears inside a finer shell, so the same method can be reached from two
indices. Candidates are therefore deduplicated by `key` against the lane's own ledger under
the current code hash, which is exactly `sidetrack.done_idents` one lane at a time.

`next_index` is one past the highest index measured under the current code hash. The archive
chooses where a firing starts walking; it never decides what is at an index. The enumeration
itself is a pure function of the index, and a test holds that under two different
`RK_WORK_DIR` values.

`lanesearch_code_hash` covers `LANESEARCH_FILES`: this module, `validation_axes.py`,
`secondpass.py`, `validation.py`, `enumeration.py` and the four prototypes. Editing any of
them re-opens the lane's candidates rather than leaving a stale number in the archive.
`lanesearch.py` and `validation_axes.py` are deliberately **not** in
`sidetrack.SIDETRACK_FILES`: adding them would move `sidetrack.code_hash()` and re-open all
103 measured side-track points every time either file is edited, for no reason at all.

## What wiring it would take

Not done here, and deliberately so.

1. `runner._run_lane` (batch 9) calls `sidetrack.run_until(tracks=(lane,))` and falls
   through to `lanesearch.adaptive_step` or `lanesearch.implicit_step` when the ledger
   reports exhausted. That fall-through is what makes a lane absorb an arbitrary budget.
2. `_WORK_EXTRA_PATHS` in `runner.py` would need `adaptive_archive` and `implicit_archive`
   for the two directories to reach git, and the growth arithmetic below decided before it
   does.
3. `lanes.cycle_row`'s `lane_records_appended` is already the field for a lane's own
   archive count; the runner passes the length of the step's record list.
   `step()` takes `seed` and records it as provenance; where a firing STARTS walking comes
   from the ledger cursor, not from the cycle id, because a cycle-id index would skip or
   repeat candidates depending on how many each firing happened to fit. Pass `start=` to
   make a firing a pure function of its arguments.
4. A page (batch 6) reads `elites.json` and renders an absent file as "no records under the
   current code hash" rather than as an empty table.

## Two things to decide before arming

**Archive growth.** A record is a few kilobytes. At 32 candidates a firing and 164 lane
cycles a day that is roughly 5,000 records a day per lane. `MAX_CANDIDATES_PER_STEP` is the
knob, and a rotation or compaction policy for the daily files is a decision nobody has made
yet. The ledger stays small either way.

**Whether these numbers may ever be published.** They are off the scored path, they are
float64, and they carry two cost grades that are not assembly-verified. The module states
that in a field on every record. Putting either archive on the traceability rule's list is a
separate, deliberate act.
