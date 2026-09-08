# P6: the trade-offs matrix and the cost claim under it

Status: proposal. Part A is unpinned site and benchmark work. Part B is an external check.

## The question this answers

What does the paper's central table still lack, and is the number in its cost columns true?

## What the matrix has

`generate.tradeoffs_matrix` (`rk-overview/tools/generate.py:1652-1795`) prints, per method:
method, kind, order, stages, held-out error at budget, validation wins split practical and stiff,
cycles per step under `m0plus_fast` and `m0plus_slow`, CSD weight, measured microseconds per
step, behavior on the stiff validation subset, and notes, with a provenance footnote for each
column. That is most of what `ROADMAP.md:87-90` asks for.

## What is missing

**Coefficient memory.** The roadmap asks for "coefficient memory and CSD weight (code-size
proxy)". Only CSD weight is computed. Coefficient memory is directly available: each coefficient
is a `CoeffRep` with an int16 `m` and a shift `s` (`types.py:68-73`), and the cost model already
distinguishes coefficients that need no table entry at all, since 0, +1 and -1 cost zero cycles
(`costmodel.py:43-49`). A tableau's constant table is a count of int16 words plus their shifts.

**Measured time for four of the nine methods.** The benchmark run covers rk4 and three discovered
methods (`rk-work/benchmark/results.json`, `methods`). The matrix footnote says so: "euler,
midpoint, heun2 and rk38 were not part of the benchmark run". Those four rows print a dash in the
column the roadmap calls empirical corroboration.

**Spread.** `rk-work/benchmark/results.json` carries an IQR per timing cell. The matrix prints a
median alone, in the column most likely to be questioned.

## What is weaker than it looks

The cost model itself. `cycle_count` prices five operation classes from a table
(`costmodel.py:15-17`: `m0plus_fast` at mul 1, add 1, shift 1, load 2, store 2). The only
cross-check is `fixtures/known_sequence.s`, ten lines of ARMv6-M with the cycle counts written in
the comments, counted by `count_sequence` using the same table (`costmodel.py:89-107`,
`tests/test_t1_fixedpoint_coeff_cost.py:868`). That confirms the counter agrees with the table.
Nothing confirms the table.

The second check is the correlation: Pearson 0.980 between analytic cycles per step and measured
seconds per step across 28 fixed-step runs (`rk-work/benchmark/results.json`, `correlation`). The
verdict already states its limit: "the analytic model counts Cortex-M0+ arithmetic while the
measurement is Python interpreter time, so the correlation speaks to ordering, not to absolute
scale."

`DESIGN.md` rules hardware out deliberately: "Static cycle counting, not simulation, not
hardware. The integrator inner loop is branchless straight-line code, so summing ISA cycle costs
from the disassembly gives an exact count." That argument holds while the loop is branchless and
the operands are in registers and SRAM. Two things put pressure on it. Flash wait states and the
M0+ bus mean a load is not always two cycles in place. And epoch 2 breaks the branchless premise
outright: `EPOCH2-DESIGN.md:87-101` books a bit-scan loop at its worst case, and
`EPOCH2-DESIGN.md:153-158` makes the trajectory cost depend on the rejection count. A cost model
about to start pricing worst cases should have been checked once against a real one.

## Part A: complete the matrix

* Coefficient-memory column, computed in `generate.py` from the tableau the way the cycles and
  CSD columns already are.
* Spread next to the median in the measured column.
* Extend the benchmark run to euler, heun2, midpoint and rk38 so the measured column is full.
* The work-precision column, once P3 exists.
* A column counting the scored problems on which each method overflowed, which is visible today
  only for the three stiff validation problems.

Each is a change to `benchmark.py` and `generate.py`, both unpinned, and each new number needs
its entry in the provenance footnote list, which the existing code already models well.

## Part B: check the table once

Compile the emitted C from `costmodel.emit_c` ("a string for human review only and is never
executed", `costmodel.py:1-7`) for two or three tableaus, at a stated toolchain and optimization
level, and count cycles either on a part with a cycle counter or in a cycle-accurate simulator.
Report the ratio of measured to analytic per step, per tableau and per cost model, with the
disassembly next to the counted sequence.

Both outcomes are useful. The table is right within a stated percentage, and the paper's core
claim gains an external check it does not have. Or it is not, and the paper reports the
discrepancy and its cause. Neither outcome changes the objective: `ROADMAP.md:120-127` already
rules that cycles stay primary and that measured time never silently replaces them.

## Cost

Part A: two to three days across `benchmark.py`, `generate.py` and `pages_text.py`, plus the
`check_demo.js` and `check_hero.js` gates that `generate.py` enforces on every build.

Part B: a toolchain and either a board or a simulator, neither of which the workspace has today.
Budget a week. It is optional to the paper's schedule and not optional to its claims.

## Prerequisites

1. P3, for the work-precision column.
2. A written decision that a Part B result is reported as a calibration and does not move the
   pinned cost table outside an epoch boundary.
3. A full re-run of the benchmark table rather than four appended rows, because
   `rk-work/benchmark/results.json` stamps its own environment and appended rows would not match
   it.

## Risks

* Part B could find the table wrong. Then every cycle number in the archive is off by a factor,
  the ordering probably survives, and the fix is an epoch boundary since `costmodel.py` is
  pinned. The honest handling is to publish the ratio as a calibration and leave the archive
  alone; the epoch-1 numbers are internally consistent whatever the calibration is.
* Every number added to a published page needs a source in `key_findings.json`,
  `rk-work/validation/results.json` or `rk-work/benchmark/results.json`. Coefficient memory
  computed in `generate.py` from a tableau stands on the same footing as the existing cycles and
  CSD columns, and its footnote has to say so.
* Part B is the one item here that depends on hardware the workspace does not have, so it is the
  most likely to be dropped. It is also the one that most directly answers the question a
  reviewer will ask about the paper's central claim.

## Epoch boundary

No, for both parts. Acting on a Part B discrepancy by editing `costmodel.py` would be, and is out
of scope here.
