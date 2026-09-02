# Research roadmap

Owner direction (2026-09-02): extend the search beyond explicit fixed-step Runge-Kutta
into adaptive and implicit methods, add stiffer and more practical test problems, compare
against real library implementations with measured wall-clock time, and drive the whole
program toward a publishable paper with an honest trade-offs analysis. Timing of the
transitions is delegated ("it's really up to you"); this document is the written answer.

## The epoch model

Every score in the archive is a function of the ten verifier-pinned files. Changing the
evaluator, the problem suite, or the method class silently changes every number, so those
changes only happen at an epoch boundary: the run is frozen, the archive becomes immutable
evidence for that epoch, the verifier hash is re-pinned, and a new archive starts. Work
that does not touch the pinned files (validation suites, benchmarks, literature, site,
analysis) proceeds at any time, out of band.

## Attention rotation: 70/15/15

Owner direction (2026-09-02): do not work the tracks strictly in sequence. Roughly 70% of
each work wave goes to the lead track (the current scored epoch and its paper analysis)
and 15% to each side track, so all three show steady, visible progress at all times. The
scored archive still holds one epoch at a time; the side tracks advance through things
that need no scored slot: design documents, working prototypes, off-archive experiments on
the validation problems, and literature. Every side-track allocation must land a showable
artifact (a doc, a passing prototype test, a measured preliminary curve), not just
thinking. The Codex literature rotation implements the same split
(`literature.TRACK_SCHEDULE`, 14/3/3 over a 20-slot cycle). When the scored slot hands
over at a freeze, the track letters re-map and the lead becomes the new epoch.

The progress loop is public (owner, 2026-09-02): both sites surface it. The overview
carries a research-tracks page (the three tracks, current milestones, orchestrator state);
the findings site carries an epoch-status panel on its index (epoch, active or frozen,
last progress event, saturation counter), rendered from the state files on disk so it
stays deterministic and refreshes every cycle.

## Epoch 1: explicit fixed-step (current, closing)

The running search over explicit fixed-step tableaus at a 65,536-cycle budget under Q15
floor arithmetic. Its scientific yield is documented on the sites: the floor-bias
mechanism, the anchor reversal, the rc_thermal quantization floor, the phase-0 closed
result, and 13/14 grid cells with discovered methods ahead of every cheaper-or-equal
classical anchor.

**Freeze rule (implemented in `rk_harness/saturation.py`, executed by the watchdog):**
progress means a first record in an empty archive cell, an elite improving its cell, or a
heldout_verified acceptance. When the newest progress event is older than 48 hours and the
falsification protocol has produced its file, an assessment is "saturating"; six
consecutive half-hourly saturating checks trigger a graceful freeze (STOP killfile at a
cycle boundary, final push, `EPOCH_STATUS.json`). State lives on disk, so watchdog
restarts adopt it. As of 2026-09-02 the run is past the window (last progress 54 h ago,
falsification concluded), so epoch 1 is expected to freeze shortly. A manual restart after
a freeze is deliberate and is left alone.

## Out-of-band track (any time, no epoch break)

- **Practical validation suite** (`rk_harness/validation.py`): real-application equations
  evaluated at the same budget against epoch champions and classical anchors. Extend with
  moderately stiff problems (the regime where explicit methods visibly pay a stability
  tax); document each equation's source. This is both a generalization test for epoch-1
  results and the motivating evidence for epoch 3.
- **Library benchmark harness**: run the same problems through real library integrators
  (SciPy `solve_ivp` RK45/Radau/BDF/LSODA at matched tolerances, plus hand-rolled float
  rk4 and the Q15 champions) and record accuracy AND measured wall-clock. Two layers of
  cost claim: analytic Cortex-M0+ cycles (portable, the paper's core claim) and measured
  time on a stated host (empirical corroboration). Timing methodology: pinned CPU
  conditions where possible, median of repeated runs, report the spread; Python-level
  timings compare like against like and are labeled as such.
- **Trade-offs matrix**: per method (discovered, classical, library): held-out accuracy at
  budget, cycles/step on both cost models, coefficient memory and CSD weight (code-size
  proxy), measured time/step, behavior on the stiff set, and qualitative notes. This
  becomes the paper's central table.
- **Literature rotation**: topics extended (2026-09-02) to implicit families (SDIRK,
  Radau, Rosenbrock), embedded pairs and step control in fixed point, stability regions
  of low-cost methods, and how production libraries implement their integrators.

## Epoch 2: adaptive explicit (embedded pairs)

Search over embedded pairs (b and b-hat sharing one A) with a Q15 step-size controller
using shift-friendly gains. The scored metric changes from fixed-budget error to
work-precision: cycles consumed to reach a target tolerance, which is how adaptive methods
earn their keep. Requires: tableau type extension, controller in the simulator, cost model
terms for the controller and rejected steps, new archive dimensions, re-pinned hash.
Candidate anchors: Bogacki-Shampine 3(2), Fehlberg and Dormand-Prince pairs, cheap 2(1)
pairs. **Scored-slot trigger: epoch 1 frozen and its paper-facing analysis (validation +
benchmarks + trade-offs) published. Under the 70/15/15 rotation, prototype work (tableau
pair types, the Q15 controller, off-archive work-precision runs on the validation
problems) starts immediately and accumulates ahead of the handover.**

## Epoch 3: implicit for stiff problems

Diagonally implicit (SDIRK) families first: one LU factorization shape, a fixed Newton
iteration count so cycle cost stays deterministic and analyzable. Scored on a stiff suite
where explicit methods are stability-limited at any budget. Cost model gains solver terms
(Jacobian, factorization, back-substitution per stage). Compare against Radau/BDF library
baselines from the benchmark harness. Stability-region computation joins the verifier.
**Scored-slot trigger: epoch 2 saturated by the same rule, or earlier if epoch-2 results
are thin and the stiff validation data shows a large explicit-method gap worth chasing.
Under the rotation, the SDIRK prototype and stiff off-archive experiments start now at the
15% level.**

## Wall-clock as an objective

Cycles stay the primary, portable objective. Measured time enters in three steps:
(1) now: reported alongside cycles in the benchmark harness; (2) epoch 2+: a secondary
archive dimension (median measured ns/step on a stated reference host) so selection can
see it; (3) only if the data shows cycles and measured time diverging materially: a
dedicated time-optimized search. Never silently replace cycles; the divergence itself
would be a finding.

## Paper

Assembled from frozen epochs; drafting starts once epoch 1 freezes. Skeleton: motivation
(fixed-point integrators on tiny MCUs); related work (RK searches, fixed-point ODE
literature, library implementations); methodology (the harness, verification, statistics);
epoch 1 results (floor bias, anchor reversal, discovered methods with the 13/14 result and
its caveats); practical validation and library benchmarks; the trade-offs matrix;
limitations and the winner's-curse discussion; future epochs. Every number traceable to a
frozen archive plus a rerunnable script. Venue-neutral draft lives in the repo; the
rk-overview site remains the public companion.
