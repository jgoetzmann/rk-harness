# Epoch 2 design: adaptive explicit pairs

Status: side-track design under the 70/15/15 rotation (docs/ROADMAP.md). A working
float prototype exists at `rk_harness/prototypes/adaptive.py` with tests in
`tests/test_t11_prototypes.py` (the `test_B2x_*` group) and a measured preliminary
work-precision curve at `rk-work/prototypes/adaptive_curve.json`. Nothing below
touches a pinned file before the epoch-1 freeze; this document is the plan for the
changes that happen at the boundary, grounded in the code as it stands today.

## 1. What changes and why

Epoch 1 scores a fixed-step tableau by the error it reaches after spending a fixed
budget of 65,536 analytic cycles (`evaluator.DEFAULT_BUDGET_CYCLES`, mapped to a
step count by `simulate.steps_for_budget`). Adaptive methods cannot be scored that
way: their whole value is spending fewer steps where the solution is easy. Epoch 2
therefore changes three things at once, which is exactly why it needs an epoch
boundary: the method class (embedded pairs), the integrator (a step controller
inside the Q15 loop), and the metric (cycles consumed to reach a target tolerance).
Every one of those lives behind the verifier hash.

## 2. Tableau type: one A, two weight vectors

`types.Tableau` is a frozen dataclass `(A, b, c)` over `Fraction`. The pair type
adds one field:

    @dataclass(frozen=True)
    class EmbeddedTableau:
        A: tuple[tuple[Fraction, ...], ...]   # square, strictly lower triangular
        b: tuple[Fraction, ...]               # order p, propagated
        b_hat: tuple[Fraction, ...]           # order p-1, estimate only
        c: tuple[Fraction, ...]

b and b_hat share A and c, so the stage computation is identical to epoch 1 and
`tableau.is_explicit` / `row_sums_consistent` apply unchanged. The pair order rule
is fixed at `order(b_hat) = order(b) - 1`, checked symbolically with
`orderconditions.achieved_order_symbolic` on the `(A, b_hat, c)` tableau; a b_hat
that reaches order p would make the estimate degenerate and is rejected. The
canonical form behind `tableau.content_hash` gains a `b_hat` key, so epoch-2 hashes
can never collide with epoch-1 hashes even for the same A and b.

Search-space note: epoch 1 snaps A to dyadics and solves b exactly over Fractions
from the b-linear order conditions. Epoch 2 keeps that machinery and solves twice
against the same A: once for b at order p, once for b_hat at order p-1 with at
least one free parameter left so d = b - b_hat is not forced to zero. FSAL pairs
(last A row equal to b, c ending at 1, like Bogacki-Shampine 3(2)) are worth a
dedicated enumeration branch because they get one stage evaluation free on every
accepted step.

## 3. The Q15 error estimate

`simulate.solve_q15` already stores, for each stage i, the vector
`hk[i] = q15_mul(k_i, h_q)`, and applies weights with `q15_apply(v, m, s)` which is
`(v*m) >> s` with floor. The estimate needs nothing new from the stage loop. With
`d_i = b_i - b_hat_i` run through the existing `coeffrep.to_rep`:

    E_m = sum_i q15_apply(hk[i][m], rep(d_i).m, rep(d_i).s)     for each state m

The sum accumulates in an int32 register before any comparison, the same pattern as
the int32 `tmp` in `costmodel.emit_c`. For the BS32 anchor the weights are
d = (-5/72, 1/12, 1/9, -1/8); everything fits CoeffRep, only -1/8 is exact.

The floor semantics that produced the epoch-1 floor-bias finding apply directly to
E: each nonzero d term floors, contributing about -0.5 LSB of bias, so a four-term
estimate sits on a bias floor of roughly 2 LSB. Consequence: E is meaningful only
above a few LSB of the scaled state, and the scored tolerance ladder must sit above
that floor. This is a measurement limit of Q15 itself, the same mechanism that put
rc_thermal on a quantization floor in epoch 1, and it must be documented with the
scores rather than papered over.

Acceptance is division-free: accept iff `max_m |E_m| <= tol_q`, with tol_q a
precomputed Q15 constant per (problem, target). A relative component would add one
`q15_mul` and one `q15_add` per state; the initial epoch-2 ladder uses absolute
tolerances in the scaled state to keep the acceptance test at a compare.

## 4. The step controller

The prototype validates the plain PI form (Hairer/Wanner II.4):

    fac = safety * err^(-alpha) * err_prev^(beta)

with every constant dyadic so the fixed-point port changes nothing: alpha = 1/4
(the classical value for a 3(2) pair is 1/3), beta = 1/8, safety = 7/8, factor
clamped to [1/4, 2], and no growth on a rejected step. The measured curve shows
these rounded-to-shifts gains converge cleanly (section 9).

The Q15 realization avoids powers and division entirely by working in the log2
domain. Let `e = msb(max|E_m|) - msb(tol_q)`, where msb is the leading-bit index.
ARMv6-M has no CLZ instruction, so msb is a short bounded loop (at most 15
iterations on an int16, at most 31 on the int32 accumulator); the cost model books
it at its worst case to keep cycle counts deterministic. With alpha = 1/4 and
beta = 1/8 the PI exponent is the integer `nu = e_prev - 2*e` in units of eighths:

    h_q' = q15_mul(h_q, ftab[clamp(nu)])

where ftab is a small precomputed table of Q15 values `round(32768 * (7/8) *
2^(nu/8))`, clamped so the factor stays inside [1/4, 2] (33 entries at most, and a
coarser 9-entry quarter-step table is an option if the archive shows no
sensitivity). Total controller cost per attempted step: two bit scans, two adds,
one table load, one `q15_mul`. Rejected attempts loop with the same table; the
bounded factor plus the 1-LSB minimum step gives a provable worst-case number of
rejections per step, which section 6 needs.

Step-size bounds: `h_q` stays the Q15 scalar that `solve_q15` already builds from
`h / DERIV_SCALE`. The floor is 1 LSB; a controller that wants less has hit the
Q15 wall and the run fails loudly, the same philosophy as `Q15OverflowError`
(reject, never silently saturate). Elapsed time needs an int32 accumulator; the
harness passes float t into `make_q15_rhs` wrappers, but the hardware story and
the cost model both carry the Q31 time register.

## 5. Scoring: work-precision

The epoch-2 score for a pair on a problem is the analytic cycle count consumed to
bring the final-state error at or under a target tolerance, integrating start to
`t_end` with the controller live. Per problem the evaluator runs a short ladder of
targets (a power-of-two ladder in the scaled norm, e.g. 2^-6, 2^-8, 2^-10, chosen
per problem to sit above the LSB floor from section 3) and records for each target
either the cycles spent or a failure marker. Lower is better; a method that never
reaches the target at any step count scores no entry for it. The fixed-budget error
of epoch 1 is kept as a secondary column for continuity with the published epoch-1
results, not as the selection fitness.

`ScoreVector` changes (types.py): add `cycles_to_tol: dict[str, int | None]`
(key "problem@target"), `n_rejected: dict[str, int]`, and keep the existing
fields. `archive._SCORE_KEYS` is derived from the dataclass fields and
`record_from_json` validates shape, so the record schema changes mechanically with
the type; old records fail the new schema, which is correct because they belong to
the frozen epoch-1 archive (section 7).

Function evaluations stay out of the analytic cycle count, as in epoch 1
(`costmodel.cycle_count` excludes derivative evaluation by design, since rhs cost
is application-specific). But under work-precision the eval count no longer washes
out between methods, so `n_fevals` becomes an explicit recorded metric and the
paper-facing analysis reports cost as `cycles_overhead + F * n_fevals` with the
per-eval cost F left as a stated parameter. The prototype already counts fevals
exactly, including FSAL reuse.

## 6. Cost model additions

`costmodel.cycle_count` charges, per stage with a nonzero A row and for the b
combination, `_combination_cost`: load + (coeff_cost + add per nonzero entry) +
store, per state. Epoch 2 adds, all in the same style:

* Estimate combination: `_combination_cost(d)` per state with d = b - b_hat,
  charged on every attempted step. For a FSAL pair the b combination and the last
  stage combination are the same computation, so the accept path charges it once.
* Acceptance test: one compare per state plus the int32 accumulate, booked as
  add-class cycles.
* Controller: a fixed constant per attempted step, from the worst-case bit-scan
  loops plus table load plus one mul, expressed with the existing per-model
  `cycles` table (mul/add/shift/load/store) so all three cost models price it
  consistently. The `count_sequence` assembly cross-check gains a reference
  ARMv6-M sequence for the controller in `fixtures/known_sequence.s` style.
* Rejected steps: a rejected attempt costs the stage combinations, the estimate,
  and the controller, but no state store-back. Total trajectory cost is
  `n_accepted * accept_cost + n_rejected * reject_cost`, and both counters come
  out of the integrator run, so the scored cycles are a property of the trajectory,
  not the tableau alone. The static per-attempt cost stays available for the
  archive key (section 7).

## 7. Archive changes

Today: one grid per order 1..4, cell key `(stages, cycle_bucket(fast cycles))`,
fitness heldout_error, bucket table <16 -> 0 up to >=1024 -> 7. Epoch 2 keeps the
MAP-Elites shape with these changes:

* Grids per propagated order p in 2..4 (a pair needs p >= 2 by construction).
* Cell key: `(stages, cycle_bucket(static per-attempt cycles under m0plus_fast))`.
  The static per-attempt cost is a pure function of the tableau, so cell placement
  stays replayable from the record alone.
* Fitness within a cell: heldout cycles-to-target at the reference target (the
  middle of the ladder), with fixed-budget heldout error as the tiebreak.
* Records carry the pair (`tableau` JSON gains `b_hat`), the new score fields, and
  the re-pinned `verifier_hash`. `archive.replay` filters records by the pinned
  hash it is built against, so epoch-1 lines are inert without deletion; on disk,
  epoch 2 opens a fresh dated JSONL series and the epoch-1 files stay as frozen
  evidence per the epoch model.

## 8. Verifier and golden tests

Additions to the pinned verifier at the boundary:

* Shape checks: b_hat length matches, shared A strictly lower triangular
  (existing checks), d = b - b_hat not identically zero, every d_i representable
  by `to_rep` with its exactness recorded like any coefficient.
* Order checks: symbolic order of (A, b, c) is the claimed p; symbolic order of
  (A, b_hat, c) is exactly p - 1.
* Overflow scan: the int32 estimate accumulator joins the tracked quantities the
  same way stage intermediates feed max|q| in `solve_q15` today.
* Golden fixtures: a classical-pairs fixture (BS32 at minimum, a cheap 2(1) pair
  as a second anchor) with pinned measured orders on dahlquist; a controller trace
  fixture (a synthetic error sequence in, the exact h_q ladder out, byte for
  byte); a rejected-step accounting fixture (a pinned rough problem, exact
  accepted/rejected/feval counts). The T-suite grows a golden test per fixture.
* `VERIFIER_HASH` re-pinned over the changed files by the existing
  `verifier_hash.py` mechanism.

## 9. Migration at the epoch-1 freeze

1. The watchdog freeze completes per `saturation.py`: STOP at a cycle boundary,
   final push, `EPOCH_STATUS.json` written by `--mark-frozen`.
2. Epoch-1 paper-facing analysis (validation, benchmarks, trade-offs matrix)
   published; this is the scored-slot trigger from the roadmap.
3. Promote the surviving prototype design into the package: tableau type, the
   adaptive Q15 solver next to `solve_q15`, controller, cost terms, evaluator and
   verifier changes from sections 2 through 6.
4. Add the pair fixtures and golden tests; re-pin `VERIFIER_HASH`.
5. Update archive read/write for the new record schema; epoch-1 archive files
   remain untouched on disk.
6. Re-run the full T-suite plus the new golden tests before the new run starts.
7. Sites: the epoch panel flips to epoch 2 active; the epoch-1 pages keep their
   frozen numbers.
8. Track letters re-map per the rotation: the lead becomes epoch 2, the adaptive
   side track closes, the implicit (epoch 3) side track continues at 15%.

## 10. Prototype evidence so far

`rk_harness/prototypes/adaptive.py` implements the float64 BS32 pair (Bogacki and
Shampine, Applied Mathematics Letters 2(4):321-325, 1989) with the dyadic PI
controller exactly as specified in section 4's float form. Tests (T11, `test_B2x_*`)
confirm: the order conditions for b at order 3 and b_hat at order 2 and not 3; a
measured order of 3.0 for b and 2.1 for b_hat on dahlquist; controller convergence
on a smooth problem with error tracking tolerance; rejections happening and being
counted exactly (fevals = 1 + 3 * attempts for the 4-stage FSAL pair) on a fast
oscillator started with an oversized step.

The measured artifact, `rk-work/prototypes/adaptive_curve.json` (preliminary,
float-only, labeled as such in the file), runs three of the T8 practical problems
at six tolerances from 1e-3 to 1e-8. Behavior is exactly what work-precision
scoring wants to reward: on buck_converter the achieved error follows the
tolerance within about one order of magnitude across five decades while function
evaluations grow from 91 to 3,571; pll_lock and glucose_minimal show the same
shape; rejections stay in single digits everywhere on these smooth problems. The
curve gives the epoch-2 evaluator a known-good reference to reproduce once the
same pair runs under Q15, where the section-3 tolerance floor should appear as a
flattening of the curve at tight tolerances. Measuring where that flattening sits,
per problem and per scale, is the next prototype step and the last open input to
the tolerance-ladder choice in section 5.
