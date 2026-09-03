# Epoch 3 design: SDIRK on a stiff scored suite

Status: side-track design document (implicit track, 15% allocation per
docs/ROADMAP.md). Nothing here changes a verifier-pinned file. The working
prototype is `rk_harness/prototypes/sdirk.py` (float-only, off-archive) and its
measured artifact is `rk-work/prototypes/sdirk_curve.json`. Both are labeled
preliminary. The scored implementation lands only at the epoch-2 handover, in
one change set with a verifier hash re-pin.

## Why implicit, in this harness's terms

Every epoch-1 and epoch-2 method is explicit, so the step size is bounded by
the stability region, not by accuracy. `evaluator.stability_extents` measures
that region; rk4's real-axis extent is about -2.785. On a problem with a fast
rate `lambda`, an explicit method must take at least `|lambda| * t_end / 2.785`
steps before a single step is stable, no matter what error it can afford. The
prototype curve quantifies this on a two-rate linear system (rates -1000 and
-1, t_end 2): rk4 needs n >= 719 steps just to be stable, a floor of about
47,000 m0plus_fast cycles at any accuracy, while the 2-stage SDIRK below is
clean at n = 5 and reaches error 1e-3 near n = 10, about 3,500 cycles. The
frozen rc_thermal problem (stiffness ratio 70) shows the same shape in
miniature: euler and rk4 diverge below 32 and 24 steps respectively, SDIRK is
fine at 4. That gap is what an epoch-3 archive scores.

## Method family

Diagonally implicit Runge-Kutta (SDIRK): `A` lower triangular with one
repeated diagonal value `gamma`. The choice is driven by cost-model shape, not
taste: the Newton matrix `M = I - h*gamma*J` is the same for every stage, so
one small LU per step serves all stages, and the stages still resolve
sequentially the way `simulate.solve_q15`'s stage loop already works. Fully
implicit families (Radau) would need an `(s*n) x (s*n)` block solve per step;
they stay on the library-baseline side of the comparison
(`solve_ivp` Radau/BDF in the benchmark harness), not in the search space.

The anchor method is the standard 2-stage, order-2, L-stable SDIRK
(Alexander 1977) with `gamma = 1 - sqrt(2)/2 = 0.29289321881...`:

    c = [gamma, 1]        A = [[gamma,     0    ]      b = [1 - gamma, gamma]
                               [1 - gamma, gamma]]

It is stiffly accurate (`b` equals the last row of `A`), which is what makes it
L-stable: `R(z) = (1 + (1 - 2*gamma)*z) / (1 - gamma*z)^2`, numerator degree
below denominator degree, so `R(-inf) = 0`. The epoch-3 search space is the
SDIRK analogue of the epoch-1 space: lower-triangular `A` with a shared
dyadic-snapped diagonal, order conditions from the same
`orderconditions.achieved_order_symbolic` machinery (they hold for any tableau,
explicit or not), stages 2 to 4, orders 2 to 3. Candidate classical anchors for
the archive grid: the tableau above, the 3-stage order-3 L-stable SDIRK
(Alexander's gamma = 0.4358665...), and implicit Euler as the 1-stage floor.

One representability consequence up front: both useful gammas are irrational.
The epoch-1 ruling that `b` is solved exactly over Fractions cannot carry over
unchanged, because the order conditions now involve `gamma` itself. The epoch-3
plan is the reverse of epoch 1: begin by snapping `gamma` to a dyadic `m / 2^s`
(CoeffRep already expresses this, `s <= 20`), then solve the remaining `A` and
`b` entries exactly over Fractions from the order conditions with that dyadic
`gamma` substituted in. L-stability is then checked numerically for the snapped
tableau rather than assumed from the textbook value; a dyadic `gamma` near
0.29289 keeps `|R(inf)| = |1 - b A^{-1} 1|` small but not exactly zero, and the
verifier threshold below decides.

## Stage solves: fixed-count modified Newton

Per step, exactly as prototyped in `sdirk.sdirk2_step`:

1. Evaluate the Jacobian `J` once at `(t, y)` and freeze it for the step.
2. Form `M = I - h*gamma*J` and LU-factor it once (partial pivoting, n <= 4).
3. For each stage `i`: form the base `y + h * sum_{j<i} a_ij k_j` (one
   combination row, same shape `solve_q15` already prices), warm-start `k` from
   the previous stage, then run exactly `NEWTON_ITERS = 3` iterations: one rhs
   evaluation, residual `k - f(Y_i)`, one forward/back substitution, update.
4. Combine over `b` as usual.

The count is fixed, never adaptive, so cycles per step are a compile-time
constant: the property the whole cost model is built on. rhs evaluations per
step are exactly `s * NEWTON_ITERS` (plus `1 + n` with a finite-difference
Jacobian), and the T12 test asserts the count is independent of the data. Three
iterations is the prototype's setting, not a final ruling: on linear problems
the frozen Jacobian is exact and iteration one already solves the stage to
rounding; on the nonlinear scored problems the iteration error enters the
local error and the convergence study (`evaluator.measured_order`) will show
directly whether 2, 3, or 4 iterations preserve the claimed order. The number
becomes part of the pinned evaluator config and therefore of the epoch hash.

A diverging Newton iteration cannot be detected by residual tests without
making cost data-dependent, so it is not detected at all: it surfaces as a
Q15 overflow (`fixedpoint` primitives raise) or as garbage error, both of
which the verifier already rejects. That is the same philosophy as epoch 1,
where overflow is a verdict, not an exception path.

## Jacobian strategy for 1-4 state systems

Problems that are linear or have cheap closed-form derivatives provide an
analytic Jacobian callback next to `f` (an optional field on `Problem`;
`None` for the rest). Everything else gets a one-sided finite difference,
column `j` from perturbing `y_j` by a power-of-two delta so the divide is a
shift in the Q15 port. At n <= 4 states that is at most 5 rhs evaluations per
step, and the FD arithmetic itself is `n^2` subtract-and-shift operations.
The prototype's `fd_jacobian` reproduces the analytic Jacobian of the two-rate
system to rounding. Rosenbrock-style schemes that bake the Jacobian into the
tableau are explicitly out of scope for the search: they change the order
conditions, and the point of epoch 3 is to reuse the existing machinery.

## Q15 port questions the float prototype defers

- `M` entries: `h*gamma` is small, but `J` entries in physical units can be
  large (rc_thermal has -11 before scaling; the two-rate system has -1000).
  The same trick `DERIV_SCALE` already applies to `f` extends to `J`: the
  scaled Jacobian `J * DERIV_SCALE * h / DERIV_SCALE = h*J` keeps the product
  representable, and the range analysis per problem joins the fixture the way
  `max_at_2x` does today.
- Pivot reciprocals: one per state per step. Since `M` is fixed for the step,
  reciprocals are computed once; the estimate below prices them at 32 cycles
  each as a software routine. A Newton-Raphson reciprocal in Q15 with a small
  seed table is the likely implementation; it must be exact-deterministic like
  every other primitive.
- Elimination multipliers and substitution products are data-dependent, so the
  CSD shift-add reduction that makes constant tableau coefficients nearly free
  does not apply. On m0plus_slow (32-cycle multiply) the solver terms dominate
  badly (about 3,600 cycles/step at 3 states versus 255 for rk4). The implicit
  archive will look much better on cores with a fast multiplier, and that
  contrast is itself reportable.

## Cost model extension

`costmodel.cycle_count` keeps its conventions: rhs evaluations (and analytic
Jacobian evaluations, which are application code exactly like `f`) are
excluded; everything the solver does with the results is counted; costs scale
linearly in `n_states` except the solver terms, which are polynomial in `n`.
New per-step terms, with the prototype's estimates
(`sdirk.estimate_sdirk2_cycles`):

- `form_m`: `n^2` coefficient applies plus diagonal adds.
- `lu_factor`: Doolittle counts for tiny `n` (`(n^3 - n)/3` multiply-adds)
  plus `n` software reciprocals.
- per stage per iteration: form `Y_i`, residual, one forward/back
  substitution (`~n^2` multiply-adds), update `k`.
- `stage_base` and `combine_b`: the existing combination-row costs, unchanged.
- FD Jacobian assembly when no analytic Jacobian exists: `n^2` subtract/shift.

Estimated m0plus_fast cycles per step for the 2-stage SDIRK with 3 iterations,
against the exact explicit counts:

| n_states | sdirk2 (est) | rk4 | euler |
|---------:|-------------:|----:|------:|
| 1        | 150          | 33  | 5     |
| 2        | 348          | 66  | 10    |
| 3        | 598          | 99  | 15    |
| 4        | 904          | 132 | 20    |

Roughly 5x to 7x rk4 per step. The crossover argument in the artifact: on the
two-rate problem the 5x per-step premium buys out a 719-step stability floor,
so at tolerance 1e-3 the implicit method is about 13x cheaper end to end.
These estimates need the same assembly cross-check the explicit model got
(`fixtures/known_sequence.s` and `count_sequence`): a hand-written ARMv6-M
inner loop for the 2x2 solve is the epoch-3 analogue and becomes a new pinned
fixture at the boundary.

## The stiff scored suite

The scored problems change at the boundary (they are pinned). Selection
criteria: 1-4 states, a fast/slow rate ratio between 100 and 10,000 so that
explicit anchors at the same budget are stability-limited rather than
accuracy-limited, references computable to float64 accuracy (linear systems
via `expm` like rc_thermal and dc_motor today, nonlinear ones via mpmath as
vanderpol does), and physically grounded like the T8 validation set (candidate
sources: the battery_2rc branch dynamics tightened, chemical kinetics pairs,
a thermal network with a thin fast node, van der Pol with mu around 50 as the
nonlinear stress case). rc_thermal itself graduates from held-out to search
material or stays as the continuity link between epochs; either way its
epoch-1 numbers remain frozen in the epoch-1 archive. Search/held-out split
and `PEAK`/`scale`/`max_at_2x` conventions carry over from
`fixtures/problems.json` unchanged. The budget stays 65,536 cycles so cross-
epoch cost comparisons stay readable, but with the table above that is only
about 110 SDIRK steps at 3 states, so `t_end` per problem must be chosen so
the implicit anchor resolves the slow manifold inside the budget.

## Stability verification changes

`evaluator.stability_extents` measures how far a region extends; for stiff
scoring the verifier needs the opposite question, how little is left at
infinity. New checks, computed exactly from the tableau over Fractions:

- `R(z)` as a rational function via `det(I - z*A + z*1*b^T) / det(I - z*A)`
  (small determinants, exact arithmetic like `stability_polynomial`).
- A-stability: `|R(iy)| <= 1` sampled along the imaginary axis plus the
  maximum-modulus argument for the left half plane; numerically, the same
  bisection style already used.
- L-stability margin: `|R(inf)|`, the ratio of leading coefficients (zero when
  the numerator degree is lower). Verifier gate: a new `NOT_L_STABLE` verdict
  code with a threshold on the order of 0.05 for the dyadic-snapped gamma,
  replacing the epoch-1 `UNSTABLE` gate (`STABILITY_THRESHOLD = -0.5` on the
  real-axis extent, meaningless for a method stable on the whole axis).

`verifier.structural` currently rejects any nonzero entry on or above the
diagonal (`NOT_EXPLICIT`); epoch 3 replaces this with: lower triangular, all
diagonal entries equal and positive, `row_sums_consistent` unchanged.

## Verifier, golden tests, and migration at the handover

Pinned files touched at the epoch-3 boundary, all in one change set with the
hash re-pin: `coeffrep.py` (unchanged, but gamma snapping documented against
it), `orderconditions.py` (unchanged; conditions are tableau-generic),
`verifier.py` (structural rule, L-stability gate, Newton-iteration config),
`costmodel.py` (solver terms above), `evaluator.py` (implicit stepping in the
convergence study, budget-to-steps unchanged), `problems.py` +
`fixtures/problems.json` (stiff suite, optional Jacobian field),
`fixtures/classical.json` (SDIRK anchors added), a new assembly fixture for
the solve loop, and `simulate.py` (the Q15 implicit step next to
`solve_q15`). Golden tests re-derive: measured-order goldens for the SDIRK
anchors, cycle-count goldens against the new fixture, stability goldens for
`|R(inf)|`.

Sequencing per docs/ROADMAP.md: epoch 2 freezes by the saturation rule, its
archive becomes immutable evidence, the track letters re-map, and the implicit
track takes the scored slot. Until then everything in this document stays in
the three unpinned homes it occupies now: this file, the prototype module, and
the rk-work artifact. The prototype never migrates as code; the scored
implementation is written fresh against the pinned interfaces and the
prototype becomes its cross-check (same tableau, same iteration count, float
reference trajectories to compare against the Q15 port, the same role
`solve_float` plays for `solve_q15` today).

## Prototype and artifact status

`rk_harness/prototypes/sdirk.py` implements the 2-stage method above with the
fixed iteration count and both Jacobian paths; `tests/test_t12_sdirk.py`
covers order 2 on dahlquist, boundedness at `h*lambda = -100` where rk4
diverges, the closed-form `R(z)` match, the fixed evaluation count, FD versus
analytic Jacobian, and the artifact schema. `rk-work/prototypes/
sdirk_curve.json` holds the measured curves (euler, rk4, sdirk2 on rc_thermal
and the two-rate system) with per-step cost estimates and the crossover note.
All of it is float64 and off-archive: the numbers argue for the epoch, they do
not score in it.
