# P1: a second side-track catalogue (J7 to J11)

Status: proposal. Nothing here touches a verifier-pinned file.

## The question this answers

What does the container measure once the current catalogue is done?

`SIDETRACK-AUTOMATION.md:677-684` is explicit: the 40 points measure in about 60 s once the
references are warm, so the plan exhausts within a firing or two of being enabled. It is not
enabled yet (`config.json`, `run.sidetrack_every_cycles = 0`). Enabling it without extending it
buys a few minutes of work and then a permanently exhausted ledger.

The catalogue has a governance rule already, and it is worth keeping: the `closes` field on the
`Job` dataclass (`sidetrack.py:85-95`). A job exists to close a question one of the two design
documents leaves open. Jobs that close nothing do not belong. Five such questions are open.

## The jobs

### J7 `sdirk.gamma_dyadic_scan_wide` (implicit)

Closes: the width bound on J4, and the second free parameter it never varies.

`sidetrack.py:374` pins `GAMMA_WIDTH = 8`, and `sdirk.py:185-195` returns the dyadics `m / 2**s`
within that many steps either side of `round(target * 2**s)`. So every J4 conclusion, including
any statement of the form "beyond exponent s the margin is small", is a statement about a
17-candidate window, not about the dyadics at that exponent. Nothing pins the width.

The deeper limit is the family. `order2_tableau_exact` (`sdirk.py:121-133`) fixes
`a21 = 1 - gamma`, so `c2 = 1` and `b` is solved from the two order-2 conditions. That is a
one-parameter family. The general 2-stage SDIRK has `A = [[g, 0], [a21, g]]` with `g` and `a21`
both free, which is two dyadic degrees of freedom, and the epoch-3 search will enumerate the
larger space (`EPOCH3-DESIGN.md:52-61`).

Plan: one point per `(s, a21 exponent)` pair over `s` in 4..12, with the gamma window widened to
the full representable interval at small `s` and to a stated multiple of the current width at
large `s`. Each point reports, exactly over `Fraction`, `R(z)`, `|R(inf)|`, A-stability,
stiff accuracy, the order-3 residuals, and the measured order on dahlquist.

Value: the `NOT_L_STABLE` threshold that `EPOCH3-DESIGN.md:190-194` calls "on the order of 0.05"
becomes a measured choice over the family the search will actually enumerate. It also settles
whether the A-stability of scanned candidates is a property of the family or of the window; the
recomputation from the shipped code says 18 of 149 candidates in the current window are not
A-stable, so it is not free.

Cost: pure algebra plus one order fit per candidate. Sub-second per point, as J4 already is.

### J8 `adaptive.pair_census` (adaptive)

Closes: `EPOCH2-DESIGN.md:41-47`, the search-space note.

Epoch 2 plans to keep epoch 1's machinery and solve twice against the same `A`: once for `b` at
order p, once for `b_hat` at order p-1 "with at least one free parameter left so d = b - b_hat is
not forced to zero". Whether that is possible often, rarely, or almost never for a
dyadic-snapped `A` has not been measured. If the answer is "rarely", the epoch-2 space is thin
and the design should change before it is pinned, not after.

Plan: one point per `(stages, lattice exponent)`. Each walks `enumeration.lattice`
(`enumeration.py:24-33`) over strictly lower-triangular `A` at 3 and 4 stages, solves `b` at
order 3 and `b_hat` at order 2 with `search._solve_exact` (`search.py:57-100`), and counts:
matrices admitting both solves, cases where `d` is identically zero, `b_hat` entries exactly
representable by `coeffrep.to_rep`, and FSAL structure (last `A` row equal to `b`, `c` ending at
1), which `EPOCH2-DESIGN.md:45-47` wants as a dedicated enumeration branch.

Value: the epoch-2 search space is either non-empty at dyadics or it is not, measured before the
boundary that would freeze the decision.

Cost: the largest of the five, because the lattice needs a deterministic cap.
`enumeration.PHASE1_CAP = 100_000_000` (`enumeration.py:20`) is the existing precedent for how
that is written.

### J9 `sdirk.stiff_suite_budget` (implicit)

Closes: a defect in J3.

`STIFF_LADDER` is `(8, 16, 32, 64, 128, 256)` (`sidetrack.py:370`), so J3 compares explicit
anchors against SDIRK2 at matched step counts up to 256. The harness scores at a matched cycle
budget, not a matched step count, and at that budget the cheap explicit methods take far more
steps than the ladder reaches: on `robertson_scaled`, euler runs 4369 steps, heun2 1680 and
midpoint 1985 (`rk-work/validation/results.json`). The ladder therefore stops before the explicit
methods get to the step counts they would actually use, which reads as a stability wall that the
Q15 suite does not show.

Plan: same three problems, same methods, but the ladder is the budget-implied step count per
method, from `simulate.steps_for_budget` under `M0PLUS_FAST` for the explicit anchors and from
`estimate_sdirk2_cycles` (`sdirk.py:374`) for SDIRK2, plus a small ladder around it. Report error
at matched analytic cost rather than at matched `n`.

Value: the epoch-3 argument stated in the harness's own currency. It is also the honest version:
an implicit method that wins at matched cost has won something; one that wins at matched step
count has only shown that it takes fewer steps.

### J10 `validation.stiff_screen` (implicit)

Closes: `EPOCH3-DESIGN.md:162-171`, where the stiff scored suite is a sentence of candidate names.

This is the executable half of P4. One point per candidate problem; the screen is defined there.

### J11 `sdirk.jacobian_cost` (implicit)

Closes: `EPOCH3-DESIGN.md:92-103`, the Jacobian strategy.

That section wants an optional analytic Jacobian field on `Problem`, with a finite difference for
everything else. Every SDIRK number the side track produces is priced with the finite difference,
because "the validation problems supply no analytic one" (`sidetrack.py:562-570`). So the cost of
the analytic path, which is the path the linear problems would take, has never been measured.

Plan: one point per stiff validation problem. Each carries a hand-derived analytic Jacobian,
checks it against `fd_jacobian` (`sdirk.py:239-260`) to rounding, and reports
`estimate_sdirk2_cycles` with `fd=True` and `fd=False` alongside the error at each rung.

Value: whether the optional field is worth pinning at the boundary, decided on numbers.

## The refill rule

The catalogue exhausting is structural, not a one-off. Two small unpinned additions:

* `sidetrack.status` reports points remaining and an estimated number of firings to exhaustion.
* `watch.py`'s side-track row shows it, next to the points-measured count it already carries
  (`SIDETRACK-AUTOMATION.md` change set row C11).

And a written rule: extend the catalogue while at least one firing of headroom remains.

## Cost

Roughly a week for all five. Each job is one plan function, one run function, one `JOBS` row and
a test in `tests/test_t13_sidetrack.py`, which is the shape the existing five already have. No
new test tier, so the overview build's `_SUITE_DESC` gate is unaffected.

## Prerequisites

1. The codex cap latch cleared and the directive fallback livelock fixed. Side-track firings
   interleave with the cycle loop; there is no point adding research to a loop that is not
   producing.
2. `run.sidetrack_every_cycles` set above 0. This is `SIDETRACK-AUTOMATION.md` section 11 item 1
   and nothing runs without it.
3. P4's screen defined, for J10.
4. The whole extension in one change set, landed before the executor is enabled. Editing
   `sidetrack.py` or a prototype changes `code_hash` (`sidetrack.py:52-67`), which re-opens every
   point measured under the old hash. Extending after enabling means paying to re-measure.

## Risks

* J8's enumeration is the one that can run away. Cap it explicitly and deterministically.
* A plan that depends on the archive's current contents, or on which dated archive files exist,
  is not finite and deterministic and breaks the ledger's core invariant. Archive-reading
  analysis belongs in P3, on the host.
* A firing is capped by `run.sidetrack_max_seconds` (180). More jobs does not cost more per
  firing, but one slow point can consume a whole firing. `MAX_FAILURES_PER_POINT`
  (`sidetrack.py:178`) catches failures, not slowness.
* J7 will produce numbers that disagree with prose already written about the narrow scan. That
  prose has no artifact behind it, so it can only be replaced, not reconciled.

## Epoch boundary

No. Nothing in `VERIFIER_FILES` is touched. `sidetrack.py` is unpinned by construction and must
stay that way (`sidetrack.py:6-11`).
