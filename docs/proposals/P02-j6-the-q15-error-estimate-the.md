# P2: J6, the Q15 error estimate, the controller table, and the Q31 time register

Status: proposal. Off-archive prototype work. Nothing here touches a verifier-pinned file.

## The question this answers

Where does the Q15 embedded error estimate stop carrying information, and what does the
controller cost per attempted step?

Epoch 2 scores by cycles consumed to reach a target tolerance (`EPOCH2-DESIGN.md:110-120`). The
ladder of targets has to sit above the floor the Q15 estimate itself puts on measurable error.
Section 3 puts that floor at roughly 2 LSB for a four-term estimate, by argument:
each nonzero `d` term floors, contributing about -0.5 LSB of bias (`EPOCH2-DESIGN.md:60-68`).
Section 10 says measuring where the work-precision curve flattens is "the next prototype step and
the last open input to the tolerance-ladder choice" (`EPOCH2-DESIGN.md:233-237`).

Nothing has been written. `rk_harness/prototypes/` holds `adaptive.py` and `sdirk.py`, and every
side-track artifact they produce is stamped "float64 only; no Q15 effects are included"
(`sidetrack.py:474`, `:521`, `:577`, `:685`).

## Sketch

A new unpinned module, `rk_harness/prototypes/adaptive_q15.py`. It imports the pinned
`fixedpoint` and `coeffrep` primitives, which is allowed: the rule is that pinned files are not
edited, not that they are not used (`DEVELOPMENT.md:118-137`).

### 1. The estimate

With `d = b - b_hat` run through `coeffrep.to_rep`:

    E_m = sum_i q15_apply(hk[i][m], rep(d_i).m, rep(d_i).s)

accumulated in an int32 register before any comparison, which is the pattern
`EPOCH2-DESIGN.md:50-60` specifies and the same shape as the int32 `tmp` in `costmodel.emit_c`.
Acceptance stays a compare against a precomputed `tol_q`.

The part that makes this a measurement rather than a port: instrument every floor. Record, per
step and per term, the exact integer the floor discarded. The "about -0.5 LSB per nonzero term"
claim then becomes a measured distribution, per problem and per scale, and the 2 LSB figure
becomes either confirmed or replaced.

### 2. The controller

The log2-domain form from `EPOCH2-DESIGN.md:86-101`: `e = msb(max|E_m|) - msb(tol_q)`, `msb` as a
bounded loop because ARMv6-M has no CLZ, `nu = e_prev - 2*e` in units of eighths, and

    h_q' = q15_mul(h_q, ftab[clamp(nu)])

with `ftab[k] = round(32768 * (7/8) * 2**(k/8))` clamped so the factor stays inside `[1/4, 2]`.
Build both the 33-entry table and the 9-entry quarter-step variant the design offers as an
alternative, and run the ladder against both.

### 3. The Q31 elapsed-time register

`EPOCH2-DESIGN.md:104-108` states this and stops: `solve_q15` builds `h_q` from `h / DERIV_SCALE`,
the harness passes float `t` into the `make_q15_rhs` wrappers, and "the hardware story and the
cost model both carry the Q31 time register". Nothing prices it.

Concretely: `t` becomes an int32 in Q31 of the problem's time unit; each accepted step adds
`h_q31`; the rhs wrapper reads the register instead of a float. Two terms then need pricing that
`costmodel.cycle_count` does not price today:

* a 32-bit add per accepted step, which is unconditional; and
* a Q31-to-argument conversion for time-dependent right-hand sides, which is not.

Only `glucose_minimal` is time-dependent in the validation suite ("Only time-dependent RHS in the
suite", `validation.py` problem note), and the scored suite's autonomous problems do not read `t`
at all. So the second term should be reported as a per-problem term, not folded into the per-step
constant. Folding it in would overcharge every autonomous problem, which is most of them.

### 4. The cost sequence

Write the ARMv6-M reference lines for the estimate accumulation, the compare, the bit scan at its
worst case, the table load and the multiply, and count them with `costmodel.count_sequence`
(`costmodel.py:89-107`) under all three cost models. This is the analogue of
`fixtures/known_sequence.s`, which is ten lines with the cycle counts in the comments. It becomes
a pinned fixture only at the epoch-2 boundary; here it is a listing and a number.

### The artifact

One JSON per `(problem, scale)`: at each tolerance on the ladder, the achieved error, the
accepted and rejected counts, the function evaluations, the measured bias distribution, and the
tolerance at which the curve flattens. The flattening point answers section 5's open question and
is the reason to do the work.

## Cost

Two to four days plus a T11 test group. The integrator itself is a few hundred lines; the
instrumentation is the slow part, and it is the part that produces the result.

As a side-track job it is one plan function (one point per problem, or per problem and scale) and
one run function, so the executor needs no framework change.

## Prerequisites

1. A decision on `SIDETRACK-AUTOMATION.md` section 11 owner question 3: side-track job, or held
   for the epoch-2 change set.
2. The existing float prototype and its curve (`rk-work/prototypes/adaptive_curve.json`, three of
   eight problems at six tolerances from 1e-3 to 1e-8) as the reference the port reproduces in
   float mode before Q15 numbers are believed.
3. J2's gains ruling, or a decision to measure at `alpha = 1/4`, `beta = 1/8` and revisit.
   `prototypes/adaptive.py:163-165` holds the current constants.

## Risks

* `simulate.py` and `fixedpoint.py` are not in `VERIFIER_FILES`. The ten pinned files are listed
  at `DEVELOPMENT.md:120-127` and those two are not among them, so this prototype measures
  arithmetic that nothing pins and that can move without moving the hash. Adding them to
  `sidetrack.SIDETRACK_FILES` (`sidetrack.py:55-58`) makes the ledger re-open its points if they
  do, which closes the hole for these artifacts without touching the pin.
* The floor may be scale dependent enough that one ladder does not serve every problem. That is a
  result, and `EPOCH2-DESIGN.md:114-116` already allows a per-problem ladder.
* If the two controller tables are indistinguishable the cost model gets simpler; if they are
  not, the table size enters the pinned evaluator config, so the measurement has to be strong
  enough to defend at the boundary.
* A worst-case-booked bit scan makes the analytic cycle count a bound, not an exact figure. That
  is a genuine change to what the number means relative to `DESIGN.md`'s "static cycle counting"
  decision, which rests on the loop being branchless straight-line code. It belongs in the
  epoch-2 write-up as a stated change, and it is one reason P6 exists.

## Epoch boundary

No. Everything lives in `prototypes/` and `rk-work/sidetrack/`. The design lands inside the
epoch-2 change set later, and that is the boundary.
