# P4: a stiff suite that actually stops explicit methods

Status: proposal. The screen is off-archive. Adopting its result is an epoch-3 act.

## The question this answers

Which stiff problems are both representable in Q15 and hard enough that no explicit method
finishes them at the shared budget?

`EPOCH3-DESIGN.md:162-171` sets out selection criteria and names candidates in one sentence: "the
battery_2rc branch dynamics tightened, chemical kinetics pairs, a thermal network with a thin
fast node, van der Pol with mu around 50 as the nonlinear stress case". Nothing has screened
them.

The sharper reason to do this now is that the three stiff problems the suite has do not
demonstrate the wall epoch 3 is built on. On `robertson_scaled`, the stiffest of the three,
euler, heun2 and midpoint all finish in Q15 at the 65,536-cycle budget, at 4369, 1680 and 1985
steps respectively, and midpoint wins outright at `q15_error` 0.08940456770651534
(`rk-work/validation/results.json`). What is true and traceable is narrower:
`verdicts.per_problem.robertson_scaled.finishers_discovered` is 0, and
`stiff_problems_with_no_discovered_finisher` is 1 of 3. No discovered method finishes. Cheap
classical methods are not stability-limited there at all.

An epoch-3 archive scored on problems that midpoint finishes will not show the gap
`EPOCH3-DESIGN.md:14-23` predicts, and the paper will have to account for that.

## The screen

One candidate at a time, float64 and Q15, off-archive.

**1. Dynamic range.** Take the candidate's physical trajectory at a fine reference step. Compute
the largest per-state `|y|` over the window and the largest `|dy|` times the derivative scale.
Admit the candidate only if a power-of-two state scale exists that keeps both inside Q15 with the
overflow margin the verifier already demands (`ScoreVector.overflow_margin`, `types.py:64`,
"must exceed 1.0").

This is what the `robertson_scaled` entry did by hand: rate constants cut to `(0.04, 20, 250)`
and the intermediate stored at ten times its physical concentration "so the quasi-steady
intermediate stays resolvable in Q15" (`validation.py`, `robertson_scaled` source note). That is
a good decision recorded as prose. Make it a procedure with a recorded output, so the next
problem's scaling is derived rather than chosen.

**2. Stiffness through stability, not through range.** `validation.py:29-42` states the rule the
current suite follows: each run starts on or near the slow manifold, so derivatives stay
representable, and the step count a method can afford decides whether `h` times the fast
eigenvalue stays inside its stability interval. A candidate that only becomes stiff by leaving
the representable range is measuring Q15, not stiffness. Reject it, and record that as the
reason.

**3. The wall test.** At the 65,536-cycle budget under `m0plus_fast`, run every classical anchor
and the archive's elites through `solve_q15` at the budget-implied step count. A candidate counts
as a wall if none of them finishes, or if the ones that do are worse than a stated multiple of
the SDIRK2 float reference at the same analytic cost. The second clause matters: a problem where
euler finishes badly is still a problem where an implicit method has something to prove.

**4. Reference computability.** `expm` for linear systems, `mpmath` `odefun` at 30 digits for
nonlinear, which is what `validation.py` already does.

## The candidates

**Van der Pol at mu around 20 to 50.** The scored suite already carries `vanderpol_mild` at
`mu = 0.5` (`problems.py:101`), so the relaxation regime is the same equation at a different
parameter. That makes the cross-epoch comparison unusually clean: the same problem, the same
reference machinery, one number changed. The dynamic-range issue is real, because on the fast
branch the derivative is large while the state is not, which is exactly the case `DERIV_SCALE`
exists for (`problems.py:57-60`, `rc_thermal` at 0.125). Step 1 decides whether a scale exists.

**A scalar flame-type equation**, `y' = y^2 - y^3` with `y(0) = eps` and `t_end` of order
`1/eps`. One state, a sharp ignition, and stiffness tunable by one parameter. One state also
keeps the SDIRK solver terms small: `EPOCH3-DESIGN.md:142-150` puts the 2-stage SDIRK at 150
`m0plus_fast` cycles per step at `n = 1` against 33 for rk4, so this is the cheapest possible
wall test.

**A three-node thermal network with one thin fast node**, the tightened form of `rc_thermal`,
which sits at stiffness ratio 70 today (`EPOCH3-DESIGN.md:20-23`). Linear, so `expm` gives an
exact reference and the analytic Jacobian is free, which is also what J11 needs.

**`battery_2rc` with the fast branch time constant cut.** Already in the validation suite at
stiffness ratio 6.8 (`rk-work/validation/results.json`) with its sourcing, equations and peaks
written down, so tightening it is a parameter change on a documented problem rather than a new
one.

## Deliverable

A screen report per candidate under `rk-work`, a recommendation of three to four problems for the
epoch-3 pinned set, and the rejected candidates with their reasons kept. The rejections are worth
as much as the acceptances. "We could not put a stiff flame problem into Q15 at any scale" is a
finding about fixed-point integration, and no such record exists anywhere in the repository
today.

## Cost

The screen is about a day of work and runs in minutes per candidate. Bringing each surviving
problem up to the standard `validation.py` already sets, with a literature source, the equations,
a reference method, a window and measured peaks, is the larger half: about a day per problem that
survives.

## Prerequisites

1. J9 from P1, the matched-budget stiff ladder, so the wall test compares at analytic cost rather
   than at step count.
2. J11 from P1, for the analytic Jacobians the linear candidates supply.
3. A decision on whether `rc_thermal` graduates from held-out to search material or stays as the
   cross-epoch link; `EPOCH3-DESIGN.md:169-172` leaves both open.

## Risks

* The screen may reject every candidate. Then the honest statement is that Q15 dynamic range, not
  stability, is the binding constraint on stiff problems at 16 bits, and epoch 3's motivation
  changes shape. Better learned from a screen than from a frozen archive.
* A problem selected because it walls off explicit methods is a problem selected to make implicit
  methods look good. Keep the wall test separate from selection, and report how many candidates
  were screened against how many survived. This is the winner's curse at the level of problem
  choice, and the paper has to name it.
* Van der Pol's period moves with mu, so `t_end` and `PEAK` move with it. Fix the parameter
  first, then measure.

## Epoch boundary

The screen, no. Adopting any candidate into the scored suite, yes, and specifically the epoch-3
boundary: `EPOCH3-DESIGN.md:200-213` lists `problems.py` and `fixtures/problems.json` among the
files changed in one change set with the hash re-pin. The screen exists so that boundary is a
re-pin rather than a research project.
