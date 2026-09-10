# Decision register

Append-only. One level-2 entry per ruling, newest at the bottom. Each entry states the ruling in
its heading, then Decision, Why (naming the alternative it rejects), Evidence as file:line,
Consequence, what it Closes, and an epoch-impact line. The epoch-impact line is required: a change
that would move `VERIFIER_HASH` cannot be filed here as an ordinary edit.

Numbering starts at D13. D1 to D12 are taken in this directory by `SIDETRACK-AUTOMATION.md`
section 3 and are not restated here. Other number spaces in use: J1 to J6 for side-track jobs
(`SIDETRACK-AUTOMATION.md` section 4), R1 to R18 for the jobs-harness rulings
(`docs/handoffs/jobs-design.md`), and the letter-prefixed test ids.

The fourteen pre-build decisions published on the overview
(`rk-overview/tools/pages_text.py` `DECISIONS`) are a separate, closed list. Nothing here is added
to it; its lead paragraph says nothing in it was written after the fact, and its count is
hand-typed in three places.

Rulings D13 to D25 were made 2026-09-07/08 alongside `UNBLOCK-2026-09-07.md`, which carries the
measurements they rest on. D26 and D29 onward came out of implementing `docs/proposals/`.
The numbering is dense: D26 through D31 all came from that work, and the file is kept in numeric
order even though entries were written in batch order.

---

## D13 - The plan, the register and the proposals each get one home, and the overview's decisions list stays closed (2026-09-07)

**Decision.** The plan is `rk-harness/docs/UNBLOCK-2026-09-07.md`, shaped like
`SIDETRACK-AUTOMATION.md`: numbered sections, a design-decisions section, a change set, phases,
acceptance, invariants, risks, and what the owner still decides. Rulings are this file, numbered
from D13. Proposals are one file each under `rk-harness/docs/proposals/`.

**Why.** Three precedents agree on shape: `SIDETRACK-AUTOMATION.md:100-191` numbers design
decisions with a bold ruling sentence and a `Rejected:` line; `docs/handoffs/jobs-design.md:78`
names the same register format outright; `REVIEW-NOTES.md` is the working example of an
append-only dated ledger. Rejected: appending to `REVIEW-NOTES.md`, because
`docs/DEVELOPMENT.md:50-57` defines that file as the record of rulings that depart from the
handoff, which most of these are not. Rejected: starting at D1, because D1 to D12 are taken in the
same directory and a reader greps `D4` across `rk-harness/docs/`. Rejected: `docs/proposals/` at
the workspace root, because `docs/README.md:3` organises that folder as two folders split by
audience, and a third that is neither a frozen record nor a giveaway spec breaks the claim;
`rk-harness/docs/proposals/` needs no change to that file.

**Evidence.** `rk-harness/docs/SIDETRACK-AUTOMATION.md:100-191`; `docs/handoffs/jobs-design.md:76-78`;
`rk-harness/docs/REVIEW-NOTES.md:1,8`; `docs/DEVELOPMENT.md:50-57`; `docs/README.md:1-8`;
`rk-overview/tools/pages_text.py:106,730,734,741-866`.

**Consequence.** `docs/DEVELOPMENT.md` gains one bullet naming the register and the plan, or its
routing table is wrong from the moment the files exist. `REVIEW-NOTES.md` gains one dated section
pointing here so the two ledgers do not fork silently. Nothing published changes.

**Closes.** Nothing. Housekeeping for the rest of the register.

**Epoch impact.** None. Markdown only.

---

## D14 - The PI controller gains stay provisional until a J2 artifact exists (2026-09-07)

**Decision.** Do not freeze the epoch-2 gains at either value. Keep `alpha = 1/4, beta = 1/8,
safety = 7/8` as the prototype defaults, mark `EPOCH2-DESIGN.md` section 4's gains provisional,
and record that the 2026-09-04 rejection-rate comparison has no artifact and is not citable. Close
the question after J2 has run under version control.

**Why.** The number that would change the ruling, the 0.80 to 1.02 percent band at `alpha = 3/8`,
exists only as prose in `SIDETRACK-AUTOMATION.md:374-379`. The run that produced it went to a
scratch `RK_WORK_DIR` that is not in the repository. Rejected: switching to 3/8 on that evidence,
because the gains are frozen into a pinned Q15 controller table at the epoch-2 boundary and cannot
be revised inside an epoch. Rejected: freezing 1/4 as designed, because `EPOCH2-DESIGN.md`'s own
support for it is one measurement and the classical alpha for a 3(2) pair is 1/3, so the design
value is not the conservative choice either. One correction while the section is open: J2's stated
prerequisite is already done. `SIDETRACK-AUTOMATION.md:216-221` asks for `alpha`, `beta` and
`safety` to be threaded through `solve_adaptive`; `prototypes/adaptive.py:196-197` already has
them as keyword arguments defaulting to the module constants.

**Evidence.** `rk-harness/docs/SIDETRACK-AUTOMATION.md:210-221,374-379`;
`rk-harness/rk_harness/prototypes/adaptive.py:163-165,196-197,213,221-222,275-276`;
`rk-harness/rk_harness/sidetrack.py:367-368,495-501,533-534`;
`rk-harness/docs/EPOCH2-DESIGN.md` section 4.

**Consequence.** The epoch-2 change set cannot be declared complete until J2 has an artifact.
`prototypes/adaptive.py:163-165` is left untouched, so the frozen `adaptive_curve.json` stays
byte-identical. One thing the J2 artifact must say about itself: status `underflow` is the float
step floor at `adaptive.py:213`, not the Q15 minimum step described at `adaptive.py:47-49`. A float
sweep cannot separate the two failures, and a reader who assumes it can will draw a Q15 conclusion
from a float measurement.

**Closes.** `EPOCH2-DESIGN.md` section 4's open gains slot, provisionally; and
`SIDETRACK-AUTOMATION.md:692-694`, owner decision 2, answered as "not yet, and not on that
evidence".

**Epoch impact.** Unpinned. `adaptive.py`, `sidetrack.py` and both design documents are outside
`VERIFIER_FILES` (`verifier_hash.py:14-25`). Freezing the gains into the pinned controller table
is an epoch-2 act that this record only prepares.

---

## D15 - The epoch-3 L-stability gate is a magnitude bound of 1/64, kept separate from the A-stability verdict (2026-09-07)

**Decision.** Replace `EPOCH3-DESIGN.md`'s placeholder with a concrete gate: reject a candidate
unless the magnitude of `R(inf)` is at most `1/64 = 0.015625`, computed exactly over Fractions from
the verified tableau, with the measured value recorded on the record next to the verdict. Keep
A-stability as its own verdict rather than folding it into the L-margin. Do not pair the gate with
a minimum denominator exponent.

**Why.** A recomputation of the J4 scan from the shipped code gives the distribution the
placeholder was guessing at: 149 candidates over exponents 4 to 12 in the plus-or-minus-8-ulp
window, 131 A-stable, 18 not. Best A-stable magnitudes per exponent are 0.28 (gamma 5/16), 0.209877
(9/32), 0.0637119 (19/64, at both 6 and 7), and 0.00124444 (75/256) from 8 onward. A dyadic
threshold suits a project that snaps every other constant; 0.05 is not dyadic, so a Q15 compare
against it is a rounded constant of its own. Rejected: keeping 0.05, which admits 41 of the scanned
window and leaves the gate a guess. Rejected: pairing the gate with a minimum exponent, which
encodes the scan window into the verifier; the window is `GAMMA_WIDTH = 8` at
`sidetrack.py:374` and nothing pins it, so a wider scan could change which exponents qualify
without changing the physics. Rejected: folding A-stability into the L-margin, because a small
L-margin does not imply A-stability outside the two-stage family.

**Evidence.** `rk-harness/docs/EPOCH3-DESIGN.md:186-194`; `rk-harness/rk_harness/verifier.py:19-24`
(`REJECT_CODES` has no L-stability code; the only numeric gate is `STABILITY_THRESHOLD = -0.5`);
`rk-harness/rk_harness/verifier_hash.py:17`; `rk-harness/rk_harness/prototypes/sdirk.py:185-195`;
`rk-harness/rk_harness/sidetrack.py:373-374`.

**Consequence.** The threshold is four times stricter than the placeholder, which cuts admitted
candidates in the scanned window from 41 to 16, so the epoch-3 gamma search is narrower than
`EPOCH3-DESIGN.md` currently implies and its search-space section should say so. Recording the
measured magnitude on the record lets the threshold be re-tuned later from the archive, which
matters because a pinned threshold cannot be revised inside an epoch. Two verdicts means the
epoch-3 verifier gains an A-stability code as well, and both need golden fixtures.

**Not taken.** Extending `GAMMA_EXPONENTS` at `sidetrack.py:373` beyond its shipped 4 to 12. It
would take J4 from 9 points to 13 and the catalogue from 40 to 44, breaking the plan-size assertion
at `tests/test_t13_sidetrack.py:51`, and it moves `sidetrack.code_hash()`, which re-opens every
measured point. Run J4 once at the shipped width, then widen deliberately.

**Confirmed 2026-09-08 by the artifact.** J4 has now run in the container and the nine artifacts
under `rk-work/sidetrack/sdirk.gamma_dyadic_scan/` reproduce the recomputation above exactly:
149 candidates, 131 A-stable and 18 not (3 of 13 at exponent 4, then 7, 5, 3 and none from 8
on), `l_stable` zero throughout, best magnitudes 0.280000, 0.209877, 0.063712 at both 6 and 7,
and 0.001244 at 8 and finer with gamma 75/256. Measured order is 2.0062, 2.0066 and 2.0069,
confirming that the design document's "all at measured order 2.007" was wrong at exponent 4.
The recommended 1/64 gate admits exponent 8 (0.001244) and rejects 7 (0.063712) as intended.
`EPOCH3-DESIGN.md` can now cite the artifact rather than a recomputation.

**Closes.** `EPOCH3-DESIGN.md`'s L-stability threshold slot, now on a committed artifact rather
than a recommendation.

**Epoch impact.** Epoch boundary to implement. `verifier.py` is pinned (`verifier_hash.py:17`), so
writing the verdict or the constant moves `VERIFIER_HASH` and invalidates every epoch-1 score.
Fixing the number in the design document is documentation and may land now.

---

## D16 - The Newton iteration count stays at three until J5 reports error at a matched cycle budget (2026-09-07)

**Decision.** Do not rule on the modified-Newton iteration count now. Keep three as the prototype
setting. Close the question by running J5 after one unpinned change to the job: report error at a
matched analytic cycle budget, not only at matched step counts.

**Why.** There is nothing to rule from, and the job as written compares iteration counts at unequal
cost. It runs the same step ladder for every count while recording estimated cycles per step
separately, and per-step cost is linear in the count. At two states with a finite-difference
Jacobian the estimate gives 236, 304, 372 and 440 cycles for one through four iterations, so one
iteration affords 1.58 times the steps of three at equal budget. For an order-2 method that is
roughly 2.5 times lower error from the step count alone, which a fixed-step-count table cannot
show. Epoch 3 scores on a cycle budget, so the comparison belongs in the metric that will select.
Rejected: ruling from dahlquist, which `EPOCH3-DESIGN.md` already explains cannot separate the
counts, because on a linear problem the frozen Jacobian is exact.

**Evidence.** `rk-harness/rk_harness/sidetrack.py:663-699,675`;
`rk-harness/rk_harness/prototypes/sdirk.py`; `rk-harness/docs/EPOCH3-DESIGN.md:63-84,142-150`.

**Consequence.** `EPOCH3-DESIGN.md`'s Newton section gains an acceptance criterion instead of an
open question. If the artifact shows two iterations winning at matched budget, the epoch-3 cost
table and the crossover argument both move. One documentation mismatch is worth fixing in the same
pass: the cycles table in `EPOCH3-DESIGN.md` is the analytic-Jacobian column, while the side-track
job prices with a finite-difference Jacobian.

**Closes.** Nothing yet. It sets the acceptance criterion for `EPOCH3-DESIGN.md`'s Newton slot.

**Epoch impact.** Unpinned. `sdirk.py` and `sidetrack.py` are outside `VERIFIER_FILES`. The count
enters the pinned evaluator config at the epoch-3 boundary, so the ruling itself is an epoch-3 act.

---

## D17 - Write the Q15 estimate prototype before the boundary, sweep it after, and price the time register now (2026-09-07)

**Decision.** Take neither option the owner posed. Do not schedule J6 as a side-track job in its
present shape, and do not hold it for the epoch-2 change set. Write the Q15 error estimate and the
table-driven controller as unpinned prototype code, then add J6 to the catalogue as an ordinary
sweep once there is something to sweep. Separately, record the consequence that follows.

**Why.** The catalogue's own rule disqualifies J6 as written: a job declares a deterministic finite
ordered list of parameter points, and `SIDETRACK-AUTOMATION.md:254-263` says J6 needs new prototype
code rather than a parameter sweep. A job with no points is a piece of work wearing a job's name.
Holding it for the change set inverts the dependency: `EPOCH2-DESIGN.md` requires the scored
tolerance ladder to sit above the LSB floor, names measuring where the flattening sits as the last
open input to that choice, and the ladder lives in the pinned evaluator. Measuring inside the
change set means writing the pinned constant and discovering its correct value in the same act,
with no revision available until the next epoch.

**The consequence nobody had written down.** `prototypes/adaptive.py`'s module docstring records
that elapsed time needs a Q31 accumulator, because a sum of many Q15 steps leaves int16 at once.
The harness sidesteps it today because `make_q15_rhs` takes a float `t`. Hardware and an eventual
adaptive Q15 solver need the int32 time register, and it has to be priced. `costmodel.cycle_count`
sums per-state costs and returns a per-state figure multiplied by the state count
(`costmodel.py:73-86`), so it has nowhere to put a cost that does not scale with the state count.
The function signature changes at the epoch-2 boundary, and `EPOCH2-DESIGN.md`'s cost-model list
does not mention it.

**Evidence.** `rk-harness/rk_harness/prototypes/adaptive.py:23-53`;
`rk-harness/rk_harness/costmodel.py:73-86`; `rk-harness/rk_harness/verifier_hash.py:18`;
`rk-harness/docs/SIDETRACK-AUTOMATION.md:254-263,695`; `rk-harness/docs/EPOCH2-DESIGN.md:116,139-158`.

**Consequence.** The epoch-2 change set cannot be declared complete until J6 has run, because the
ladder is contingent on a measurement that does not exist. The prototype build is specified in
`docs/proposals/P2` rather than here; this record decides the timing and names the cost-model gap.

**Closes.** `SIDETRACK-AUTOMATION.md:695`, owner decision 3, answered as "neither: build first,
sweep second, and price the time register before the boundary".

**Epoch impact.** Unpinned for what this record authorizes. The consequence it surfaces does need a
boundary: adding a per-step term to `costmodel.cycle_count` changes a pinned file.

---

## D18 - A Codex rate-limit snapshot expires once its own window has passed (2026-09-07)

**Decision.** Treat a snapshot whose `resets_at` has already passed, or that is older than its own
`window_minutes`, as absent rather than as a cap hit. Make every gate that suppresses a call write
one line to `events.jsonl` naming which gate it was. Clear the current latch by restarting the
container, not by hand.

**Why.** The gate is self-sustaining: the only writer of a fresh snapshot is a Codex call, and the
gate blocks Codex calls. The same dict already carries `resets_at`, and nothing reads it. The
window that closed the gate reset at 2026-09-07T02:39:24Z, so the reading that is still gating
describes a window that no longer exists. Rejected: deleting the rollout files inside the running
container. It works, but it reopens the gate against the old `directive.py`, which is the one
combination to avoid, because D19 is what stops the reopened directives from being spent on an
impossible cell; and the harness is a read-only bind mount running one long-lived process, so a
D19 edit cannot be in force before a restart. Rejected: raising `run.codex_usage_cap_percent` to
100, because the cap is read from the environment at container start and so needs the same restart,
and it disables a real protection permanently to discard one stale reading.

**Evidence.** `rk_harness/runner.py`: `_codex_rate_limits`, `_codex_usage_cap`, and the four
gates in `_llm_directive`, `_codex_capped`, `_maybe_literature_review`, `_maybe_interpret` and
`_maybe_propose_hypothesis` (symbols rather than line numbers: this change set moves the lines);
`rk-harness/scripts/run.ps1:56,62,67,79`; `rk-harness/entrypoint.sh:35`;
`rk-work/events.jsonl` (last `codex_usage` 2026-09-06T10:40:45Z, `used_percent` 80.0,
`window_minutes` 10080, `resets_at` 1788748764; 1,108 `llm_skipped`).

**Consequence.** The run gets model directives again after more than a thousand cycles without one.
That is also the risk. The encourager sits on the escalation rung at a stall counter above 300, and
escalations bypass the `run.llm_every_cycles` throttle, so `llm_due` is true on nearly every cycle.
At the measured cycle time that is roughly 250 directive calls a day plus a hypothesis call each
cycle plus a literature digest every eight. The cap still binds, correctly, against a fresh
reading. Whether the cadence should change before the window fills again is the owner's call and is
listed in the plan's section 10.

**Closes.** The largest live problem in the run.

**Epoch impact.** Unpinned. `runner.py`, `watch.py` and `tests/` are absent from `VERIFIER_FILES`,
so the hash does not move and no archived score changes.

---

## D19 - The emptiest-cell scan gets a stage domain that can hold the order it is searching for (2026-09-07)

**Decision.** Restrict the emptiest-cell scan to stage counts that can support the requested order:
for explicit Runge-Kutta, stages at or above the order, clamped into the searchable stage set.
Apply it to both loops in `directive._emptiest_cell` and to the duplicate in
`encourager.emptiest_cell`, from one shared helper. Fix the two sibling occurrences of the same
domain error in `prompts.py` and `sitegen.py` in the same pass.

**Why.** An explicit two-stage method cannot exceed order 2, so the order-4 grid can never hold a
two-stage cell, and the scan returns `(2, 0)` on every call forever. The archive keeps accepting
order-2 records into a stage-2 grid that is already saturated, so the search re-proposes hashes it
has archived and never sets a new elite. Rejected: capping the scan by what the grid already holds,
which is circular, because an empty reachable cell is exactly what the scan is looking for.
Rejected: leaving the encourager its own copy, since the two functions differ only in loop style
and carry the same defect. Rejected: leaving the second loop on the wide domain, which would raise
a KeyError the moment the restricted domain fills. Rejected: fixing it in
`search.project_or_lower`, which is behaving correctly; it lowers the order to what is exactly
solvable, and the bug is upstream of it.

**Evidence.** `rk_harness/directive.py`: `_TARGET_ORDER_BY_PHASE`, `_emptiest_cell`,
`fallback_directive`;
`rk_harness/encourager.py`: `_STAGES`, `_BUCKETS`, `emptiest_cell`, and the `stage_domain` /
`min_stages_for_order` pair this record adds; `rk_harness/archive.py`: `record_order`,
`_grids_from`;
`rk_harness/prompts.py`: `_grid_section`; `rk_harness/sitegen.py`: `_stat_cards`;
live `directive_fallback` events D-F02304 to D-F02306, all `target_order 4, stages [2]`.

**Consequence, including a published number that moves.** `_emptiest_cell(arch, 4)` starts
returning a four-stage cell, which is the space RK4 lives in. `project_or_lower` will still drop to
order 3 or 2 when the exact order-4 conditions are unsolvable for a snapped A, but those drops land
at stage 4 in grids that are far less exhausted than stage 2.

The findings site's coverage card currently reads 18 of 160. Both halves are wrong in the same way
and for the same reason. The denominator is `40 * len(arch.grids)`, which assumes every order can
use all five searchable stage counts; the numerator counts every occupied cell, including the
order-1 single-stage cell that holds a seeded classical baseline outside the searchable stage set
(`encourager._STAGES` is 2 through 6). After the fix the card reads **17 of 136** over one domain:
40 cells each at orders 1 and 2, 32 at order 3, 24 at order 4. The seeded single-stage cell is
stated separately rather than counted into a coverage fraction it was never part of. The same
correction applies to the model-facing text in `prompts.py`, which prints "of 40 cells filled" for
every order while enumerating only stages 2 through 6.

**Closes.** The mechanism behind the throughput collapse recorded in `UNBLOCK-2026-09-07.md`
section 2. It does not guarantee new elites; it removes the reason there could not be any.

**Epoch impact.** Unpinned. `directive.py`, `encourager.py`, `prompts.py` and `sitegen.py` are all
absent from `VERIFIER_FILES`. No archived record changes: every record keeps its held-out error,
its tier and its verifier hash. Only the choice of which cell to search next changes.

---

## D20 - The CPU pause guard returns to its shipped thresholds, on a measured baseline (2026-09-07, resolved 2026-09-08)

**Decision.** Do not restore the shipped 70/30/60/40 thresholds. Measure the non-container idle
baseline once the host is quiet, then choose thresholds from that measurement. Also replace the
per-poll `docker stats` call in the watchdog before tuning anything, because it sets the sampling
cadence the thresholds are judged against.

**Why.** Restoring the defaults on the machine as it stands would pause the run and never release
it. The guard measures non-container host CPU. With an idle baseline near 64 percent the rolling
average trips the 60 percent pause threshold; resuming needs the average below 40 or a spike below
30 sustained for 30 seconds, neither of which a 64 percent baseline can reach. A container paused
indefinitely is worse than a near-inert guard. Rejected: picking an intermediate set today, such as
84/54/76/68, because it would be derived from a baseline measured on a machine in a known-bad
state. Rejected: removing the guard, which throws away real protection for the owner's foreground
work.

**The host, diagnosed.** Three supabase containers are in a restart loop at 2553, 2621 and 2564
restarts over roughly 44 hours. The cause is not load: their database container
`supabase_db_admin` exited 127 nine days ago, so the three fail DNS resolution for it at boot and
their restart policy restarts them forever. `supabase_kong_admin` exited 127 at the same time. The
stack cannot work in that state, so the loop is pure background load. Stopping or repairing it is
an owner action on the owner's other software; this plan does not take it.

**Evidence.** `config.json:35-40`; `configure.py:58-62,205-209,216-219,229-234`;
`rk-harness/scripts/watchdog.ps1:224,227`; `start.ps1:76-80`; `docker logs` for the three
containers, all reporting an unresolvable `supabase_db_admin`; `docker inspect` restart counts.

**Consequence.** The run keeps a near-inert guard, which is now a stated risk rather than an
oversight. When the guard is restored, use the keyed form of `reset`, never a bare one: a bare
`reset` rewrites every key and would flip `run.litreview_every_cycles` from 8 back to its shipped
50. Prefer `set` over `reset` for tuned values, because `cmd_set` enforces low below high and
`cmd_reset` does not. Either way `--apply` bounces the container, so it is a deployment under D25.

**Amended 2026-09-08, with a measurement.** The description this record inherited from commit
`0ad6348`, that the guard is "close to disabled in both directions", is too strong and is
withdrawn. The guard fired on its own during this session: a host-side overview build pushed
non-container CPU to a flat 100 percent, the watchdog paused the container, and it stayed paused
for about 35 minutes while the machine remained saturated. Once the load was cleared, host CPU
fell to a median of 80.6 percent and the watchdog resumed the container **within 80 seconds**,
with no restart and no lost cycle. Note what that 80.6 percent is and is not: it is total host CPU
including the container's own four cores, sampled over 16 seconds with `Get-Counter`. The guard
compares a different quantity, host CPU minus the container's share, which is what the "roughly 64
percent idle baseline" above refers to. The two are not comparable and neither is a substitute for
the other. Deriving thresholds needs the guard's own metric sampled over its own 300-second window
on a quiet host, which is still not measured; P07's durable watchdog log is what would record it. The accurate statement is that the guard rarely fires and
releases quickly once load drops, which is a different claim from the one this record made.

Two things follow. The pause is real protection rather than a formality, so restoring the shipped
thresholds on a saturated host would strand the run exactly as this record predicted. And the
resume path works unattended, which the record could not previously assert, because the watchdog
logs pause and resume to its own console rather than to `events.jsonl` and no artifact recorded
one. Closing that gap is `docs/proposals/P07`.

**The host, as found on 2026-09-08.** The saturation was neither the run nor the container. It was
24 stale pytest and coverage processes from unrelated projects holding 1.27 GB, the dead supabase
stack (716 MB, its analytics container burning about 78 percent of a core against a database that
exited 127 nine days earlier), and a vite dev server left running from a temporary directory since
2026-09-01. With those stopped, by owner decision, the host went from 1.13 GB free at 100 percent
CPU to roughly 3 GB free at a median of 80.6 percent. That is the baseline a future threshold
choice should be derived from, once it is sampled in the guard's own metric rather than in total
host CPU.

**Resolved 2026-09-08. The thresholds are back to 70/30/60/40.** Both conditions this record set
have now been met, and the measurement reverses its own decision.

*The prerequisite, cleared.* This record said to replace the per-poll `docker stats` call before
tuning anything, because it sets the sampling cadence the thresholds are judged against, and
`CLAUDE.md` records that call at 47 s on this host. Measured over five consecutive calls on the
cleaned machine: **1.41 s min, 2.01 s median**. With `Get-Counter` at 1.63 s and a 10 s poll sleep,
the watchdog's real period is about 13 s, so `CpuSustainSeconds = 30` is roughly three consecutive
samples and the 300 s window holds about 22. That is the cadence the thresholds assume, so nothing
needs replacing. The 47 s figure is not withdrawn, because it was plausibly real: this host was
observed at 100 percent CPU with 1 GB free earlier the same day, and a saturated Docker Desktop is
exactly where a 47 s `docker stats` comes from. It is a saturated-host number being used as a
quiet-host constant, which is the same error as the one below.

*The baseline, measured in the guard's own metric.* Thirty samples over 5.9 minutes at the
watchdog's own cadence, computing what `watchdog.ps1` computes: total processor time minus the
container's share, both normalised to whole-machine percent.

| min | p10 | p25 | median | p75 | p90 | max | mean |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 9.1 | 10.9 | 14.2 | **25.0** | 31.5 | 39.2 | 54.2 | 24.7 |

Total CPU ran a median of 30.6 with the container taking 6.2 of it.

*What that overturns.* The "roughly 64 percent idle baseline" this record argued from was not a
property of the machine. It was the crash-looping supabase stack, 24 orphaned pytest processes and
an abandoned vite dev server, all since stopped. The real baseline is a median of 25. So the
premise for raising the thresholds is gone, and every objection this record raised against
restoring them fails against the distribution: a 70 percent spike gate sits 16 points above the
observed maximum, so it will not fire on ambient load; a 30 percent resume gate sits above the
median, so more than half of samples clear it and three consecutive are ordinary; and the 40
percent average resume sits 15 points above the mean, which makes the average path a reliable
backstop even when spikes do not line up. The run cannot strand.

*Applied without touching the container.* `configure.py set` rather than `reset`, because `cmd_set`
enforces low below high and `cmd_reset` does not, and because a bare `reset` would also have
flipped `run.litreview_every_cycles` from 8 back to 50. Thresholds are watchdog launch parameters,
so only the watchdog needed restarting, not the run: `--apply` would have bounced a container that
had no reason to stop. The watchdog is built for this, adopting an existing pause rather than
stranding or killing it. Verified: six minutes under the restored thresholds with no pause, the
container still at zero restarts and never touched.

**Closes.** Itself. The guard is back to the protection it was designed to give, on a number
measured in the metric it actually uses rather than inherited from a machine full of dead
processes.

**Epoch impact.** None. Config and host state only.

---

## D21 - The overview stays a deliberate act, and its staleness becomes visible from data (2026-09-07)

**Decision.** Regenerating the overview stays a deliberate act, not a cadence. Three things change
so that staleness is visible rather than discovered: derive the snapshot date from the newest
archive record instead of hand-pinning it, report the snapshot's lag in `stats.txt` through
`rk_harness.status`, and put the three documented commands behind one workspace-root script.

**Why.** The whole nine-page snapshot goes stale together, so a per-page answer has nothing to
attach to: one `build()` writes all nine and every file carries the same mtime. Automation is the
wrong shape for a different reason: the overview carries human-edited prose that has to be re-read
whenever the numbers move, so an automatic build would publish current numbers under stale
sentences. Rejected: a watchdog cadence, because the build is a heavy host-side job, the watchdog's
push covers only the work and findings repositories so the result would never be pushed, and the
build's own host CPU is exactly the foreground load the guard exists to notice. Rejected: building
it in the container, which would need the container to run node and to hold a checkout of a
repository it does not otherwise touch.

**Evidence.** `rk-overview/tools/generate.py:44,276,405,409,1367-1371,2282,2293,2295-2322`;
`rk-overview/tools/key_findings.json` `_meta` (`archive_records` 80748, `archive_last_cycle_id`
1146, against a live cycle above 2350); `rk-harness/scripts/watchdog.ps1:44-53`;
`rk_harness/status.py`.

**Consequence.** The footer, the two SVG labels and the results description stop being able to
disagree with the data on the page, because they read one derived value. `stats.txt` gains a line
saying how far behind the snapshot is, and past a stated threshold that line lands in PROBLEMS like
any other failed probe.

**Not taken.** Turning the missing-node warning into a hard failure. It would make the overview
unbuildable on any machine without node in order to settle a mismatch between the code and a
sentence in `CLAUDE.md`, and `build()` writes every page before it would raise. Correct `CLAUDE.md`
instead: it is cheaper and equally honest.

**Closes.** `SIDETRACK-AUTOMATION.md:696-699`, owner decision 4, answered as "a deliberate act, with
the staleness made visible and the three commands behind one script".

**Epoch impact.** Unpinned. The overview tools never run in the container and `status.py` is
host-only. Regenerating republishes numbers that have moved, which is the point.

---

## D22 - Pin the Q15 arithmetic at the next epoch boundary, and gate it behaviourally now (2026-09-07)

**Decision.** (a) `tableau.py`, `simulate.py` and `fixedpoint.py` join `VERIFIER_FILES`, scheduled
as the opening act of the next epoch boundary rather than made today. (b) Extend the container
start-up filter in `entrypoint.sh` with the Q15 fixture tests now, and add a coverage test so the
filter cannot drift again.

**Why.** The pin exists for one stated reason: a change to a listed file silently changes every
score in the archive. All three unlisted files meet that test. `fixedpoint`'s Q15 primitives are
every arithmetic operation the object of study performs; `simulate.solve_q15` produces every error
number in the archive; `tableau.canonical` and `content_hash` produce the archive key. `tableau.py`
is the most dangerous of the three, because a change there does not silently rescore the archive,
it silently empties it: the record reader recomputes the hash on every read and the archive reader
discards a line whose hash no longer matches. Separately, the start-up gate exercises none of this:
its filter selects only G and K prefixes, and no G or K test calls `solve_q15`; the F series, the
only tests that read the pinned Q15 fixture, is outside the gate. Rejected: pinning the three
today, because adding files to the tuple re-pins the hash and splits the archive into two
generations, which is the definition of an epoch boundary. Rejected: leaving the gate alone,
because widening the filter costs nothing and closes the larger of the two holes immediately.

`EPOCH3-DESIGN.md` already lists `simulate.py` among the pinned files touched at the epoch-3
boundary even though it is not in `VERIFIER_FILES` today, so this is a correction of an existing
inconsistency rather than a new proposal.

**Evidence.** `rk-harness/rk_harness/verifier_hash.py:3-5,14-25,30-35`;
`rk-harness/rk_harness/fixedpoint.py:38-60`; `rk-harness/rk_harness/simulate.py:85-131`;
`rk-harness/rk_harness/tableau.py:101-107`; `rk-harness/rk_harness/archive.py:60-72,146-152,249-255`;
`rk-harness/entrypoint.sh:32`; `rk-harness/tests/test_t1_fixedpoint_coeff_cost.py`;
`rk-harness/docs/EPOCH3-DESIGN.md:202-213`.

**Consequence.** Part (b) adds the F tokens to a 170-character filter line, well inside any limit
that applies, and lengthens container start-up by whatever the F series costs, which is measured
once before the change lands. `entrypoint.sh` must stay LF, so the patch normalizes line endings
before matching. The coverage test asserts only that every prefix the gate is meant to select is
present, and says so in its assertion message: G21 onward and K7 onward exist and are deliberately
not in the gate, so a test that demanded total coverage would fail the moment it landed.

**Closes.** A gap in the integrity story that no document had stated.

**Epoch impact.** (a) epoch boundary. (b) none: `entrypoint.sh` is not in `VERIFIER_FILES`, though
it is a rule-1 file that needs a stated reason, which this record is.

---

## D23 - Tier stays an insertion-time label, and the elite grid keeps ranking on held-out error alone (2026-09-07)

**Decision.** Do not make `archive._better` tier-aware. Keep publishing the top-tier count, and add
the sentence that explains why it can fall, on the findings-site card and in the glossary.

**Why.** `assign_tier` compares a candidate against whatever record occupied its cell at the moment
of insertion. It is a statement about one comparison at one time, not a grade on the method, and
the overview already says exactly that in prose the project wrote deliberately. `_better`
implements the archive's stated fitness: lower held-out error wins. Making it tier-aware would mean
the grid stops ranking on the objective and a higher-error record holds a cell. Worse, it closes a
loop: incumbents feed `assign_tier`, so refusing a lower-error record the cell would leave later
candidates tiered against a weaker incumbent. Rejected: dropping the published count, which hides a
real property rather than explaining it.

**Evidence.** `rk-harness/rk_harness/archive.py:1-7,261-263,424-434,436-446`;
`rk-overview/tools/pages_text.py`, the tiers paragraph; `rk_harness/sitegen.py`: `_stat_cards`
and the `tiers` entry in the glossary table.

**Consequence.** No archived score changes, and none would have changed under the alternative
either; the archive is append-only. What a tier-aware `_better` would change is which record
occupies each cell, and that is what makes it expensive: elite pages, cell URLs, `demo_data.json`
and `key_findings.json` would all move under a replay of the same unchanged files, so a reader who
cited a cell page last week would find a different tableau there. The card gains a short label,
"top tier at insertion", and the explanation goes in the glossary entry where there is room for it.

**Closes.** The unexplained fall in the published top-tier count. Note that the two figures in
circulation, 6 on the overview and 4 on the findings site, are over different denominators: the
overview counts discovered elites only, the findings card counts all elites including seeded
classical cells. They are comparable only if no seeded elite carries the label, which should be
stated wherever both appear.

**Epoch impact.** None. `_better` is not changed and the edits are site prose.

---

## D24 - Section 6a becomes a labelled unreproduced appendix, corrected now and deleted when artifacts exist (2026-09-07)

**Decision.** Relabel `SIDETRACK-AUTOMATION.md` section 6a as an unreproduced appendix with its
scratch-directory provenance stated, correct the two wrong statements in it now, and delete it when
the container's own side-track run writes the same points under `rk-work/sidetrack/`. Nothing in it
reaches a public site or the paper before then. Replace the stiff-wall claim everywhere with the
traceable form, and adopt a comparability rule.

**Why.** Section 6a records a run made into a scratch `RK_WORK_DIR` that is not in the repository,
so every number in it is prose with no artifact. House rule 10 settles the site question but not
the document question, and the document feeds the paper. Two of its statements are wrong against
the shipped code, so leaving them while waiting for a real run would carry the errors forward.
Rejected: deleting the section outright, because the J4 construction ruling in the same section is
design content with no other home, and the corrected numbers are the reason to run the scan again.

**The two corrections.** "All at measured order 2.007" is wrong at the coarsest exponent: the
measured values are 2.0062, 2.0066 and 2.0069 across the range. "Every candidate scanned is
A-stable" is wrong: 18 of the 149 scanned candidates are not, being 3 of 13, 7 of 17, 5 of 17 and 3
of 17 at exponents 4 through 7, and none above. "None is L-stable" stands. The conclusion that a
0.05 gate implies exponent 8 stands, and gains the scan-window caveat.

**The wording rule.** "The explicit wall is total on the stiff suite" is too strong.
`rk-work/validation/results.json` shows euler, heun2 and midpoint all finishing `robertson_scaled`
in Q15 at the 65,536-cycle budget, at 4369, 1680 and 1985 steps, with midpoint winning outright.
The traceable statement is that no discovered method finishes it. The SDIRK2 result is float64 with
a finite-difference Jacobian on a ladder capped at 256 steps, and the job stamps it as float64 only
with no Q15 effects. A side-track number and a Q15 number may not appear in one claim without their
arithmetic labels.

**Evidence.** `rk-harness/docs/SIDETRACK-AUTOMATION.md:349-352,359,363-365,367-370,383-391`;
`rk-harness/rk_harness/sidetrack.py:373-374,577`;
`rk-work/validation/results.json` `verdicts.per_problem.robertson_scaled`;
recomputation from `rk-harness/rk_harness/prototypes/sdirk.py` over Fractions.

**Consequence.** The design document stops carrying two false statements, and the appendix carries
a deletion condition so it does not become permanent. The comparability rule joins the prose rules.

**Confirmed and superseded 2026-09-08.** The deletion condition this record set has been met, and
faster than it anticipated. The container measured all 40 catalogue points at cycle 2380 under code
hash `a98acb39fb4f31ca`, and the artifacts are committed under `rk-work/sidetrack/`. Both
corrections above are confirmed by that data rather than by the recomputation that found them: the
nine `sdirk.gamma_dyadic_scan` artifacts give 18 of 149 candidates not A-stable and measured orders
2.0062, 2.0066 and 2.0069, and `sdirk.stiff_suite/servo_load_step.json` reproduces the prose figure
exactly at `min_stable_n` 8 and error 8.637e-07.

Section 6a is therefore no longer the source for any of it. Its provenance paragraph is rewritten
to say so and to point at the artifacts, and the sentence claiming house rule 10 forbids publishing
the numbers is removed, because the artifacts are data files and both sites now publish from them.
What is kept is the J4 construction ruling, which has no other home, and the corrected figures
beside it as a record that correction and measurement agree.

The comparability rule stands unchanged and is now enforced in code rather than by convention: each
job's own `arithmetic` string is rendered next to its numbers on both sites, because four jobs are
float64 and the stability scan is exact over Fractions, so a page-level claim would be false.

**Closes.** The traceability problem in `SIDETRACK-AUTOMATION.md` section 6a, now by replacement
rather than by correction.

**Epoch impact.** None. Documentation and prose only.

---

## D25 - A restart is a code deployment, and the running-versus-on-disk gap becomes visible (2026-09-07)

**Decision.** Log a harness code hash at `runner_started`, carry the same value in `RUNSTATE.json`
as a derived key, and have `status.py` recompute it on the host and report the comparison as one
row in `stats.txt`. Adopt the operational rule that a restart deploys whatever is on disk, so the
harness diff since the last `runner_started` is reviewed before any restart.

**Why.** The running interpreter holds a `runner.py` written 21 minutes after the process started,
and nothing in the system can report that. The harness is a read-only bind mount and the entrypoint
execs one long-lived process, so an edit on disk changes nothing until a restart and then changes
everything at once. That is fine as a design; what is missing is a way to see it. The project
already has the right shape for the hash in `sidetrack.code_hash()`, a sha256 over a fixed ordered
file list truncated to 16 hex, which is stable across a checkout and does not depend on mtimes.
Rejected: comparing mtimes, which is what this session had to do by hand and which a fresh checkout
breaks.

**The review this record calls for, done.** The only change to `runner.py` since the running
process started is one line adding `saturation_state.json` to the list of work paths the container
commits, plus a whole-file conversion from CRLF to LF. Nothing behavioural. The restart in the
plan's phase 4 is therefore safe on this count, and the general rule stands for next time.

**Evidence.** `rk_harness/runner.py`: `_WORK_EXTRA_PATHS` and the `runner_started` event in
`main`; `rk-harness/scripts/run.ps1:62`;
`rk-harness/entrypoint.sh:35`; `rk-harness/rk_harness/sidetrack.py:54-69`;
`rk_harness/status.py`; commit `7396f4b`.

**Consequence.** `status.py` gains one row, and it must keep the three properties that file already
keeps: no value carried forward, so the row is computed from fresh reads every time and prints
"unknown" into PROBLEMS if either read fails; nothing claimed about the container unless Docker
answered, so the row is gated on a Docker answer and a running state; and the staleness deadline
stays in the host's own timezone. `RUNSTATE.json` gains one derived key that no reader requires.

**Closes.** A blind spot that four other decisions in this register were deferring to.

**Epoch impact.** Unpinned. The hash is advisory: it gates nothing, enters no score and appears in
no archive record. Copying the harness into the image so the running code is immutable is a
Dockerfile change and an image rebuild, not an epoch boundary; the Dockerfile is not in
`VERIFIER_FILES` and does not move the hash.

---

## D26 - The exploration policy ships as machinery, disarmed, and two questions stay with the owner (2026-09-08)

**Decision.** Build the P05 machinery and leave it off. `run.search_policy` defaults to `empty`,
which is the behaviour every cycle up to now had, and the rotation is not armed. A policy chooses
two things and nothing else: which cell the deterministic fallback aims at (`empty` or `revisit`),
and whether CMA-ES starts from that cell's incumbent (`warm`). Both are additions to a directive,
never contradictions of one. The only levers are `d['stages']` on the fallback directive, which the
harness writes itself, and an `x0` entry in the constraints dict the runner builds, which
`validate_directive` never sees.

**Why the proposal is reshaped.** Two live facts, read out of the event log on 2026-09-08.
(1) The fallback path no longer fires. Over the recent window there were 48 `directive_accepted`
events, every one with `source: llm`, and zero `directive_fallback`. A policy attached only to the
fallback directive, which is what P05 specifies, is inert in the live configuration, so shipping the
rotation armed would produce blocks of identical behaviour and a null result that means nothing.
(2) The chosen cell never reached the generator. `fallback_directive` discarded the bucket half of
the cell it picked, and the encourager's SEARCH_CELL payload is logged and read nowhere else, so
"target a cell" can only be implemented as "target a stage count, and start from that cell's
incumbent". A bucket cannot be targeted at all: `archive.cycle_bucket` is a function of the finished
tableau.

**Rejected.** Arming the rotation in the change that builds it, which would spend cycles measuring a
difference the live configuration cannot express. Rejected: moving sigma and adding warm starts
together, which would make the A/B uninterpretable; sigma is now a parameter and its default stays
exactly 0.3. Rejected: re-opening `archive._better` so that revisiting reads as progress, which D23
settled.

**Two questions that stay with the owner. The rotation must not start before both are answered.**
(a) May a policy override an accepted LLM directive's `target_order` and `stages`? Today it may not:
under an LLM directive a policy can only add `x0`. (b) Does the saturation freeze rule follow the
policy? The rule counts a record in a previously empty cell as progress (DEVELOPMENT.md 246-250), so
a revisit policy reads as less progress by the run's own definition, and deciding that after the
rotation runs would be choosing the answer from the result.

**Evidence.** `rk_harness/search.py`: `x0_from_tableau`, the `sigma` parameter on `cmaes_island`;
`rk_harness/encourager.py`: `revisit_cell`, `target_cell`, `cell_policy`, `POLICIES`;
`rk_harness/directive.py`: `_cell`, `_POLICY_LETTER`, `fallback_directive`;
`rk_harness/runner.py`: `_search_policy`, `_policy_block`, `_warm_start_enabled`,
`_warm_start_record`, `_cmaes_candidates`; `rk_harness/policyab.py`; tests B81-B83 in
`tests/test_t3_archive_search_directive.py` and B84-B85 in `tests/test_t4_ledger_runner_site.py`.

**Consequence.** The fallback directive id gains a policy letter: `D-F00007` becomes `D-FE00007`,
and a revisit-targeted fallback is `D-FR00007`. The id still matches `^D-[A-Za-z0-9]+$` and still
starts with `D-F`, so `sitegen._phase_label` keeps labelling those records as search records. Four
`log_event` calls gain a `policy` field, and `island_start` also records `warm_start` and the
incumbent's `warm_start_hash`, because a warm start makes the candidate stream depend on the archive
as well as on the seed and the start point has to be recoverable.

**A reproducibility limit, to be stated wherever this comparison is published.**
`rk-work/.gitignore` ignores `events.jsonl`, so the event log is host-local and is never pushed.
Attribution for records produced under an LLM directive rests entirely on that file. The part that
survives in the published archive is the `D-FE` / `D-FR` letter on fallback-sourced records.

**Epoch impact.** Unpinned. Nothing edited appears in `verifier_hash.VERIFIER_FILES`, and the pinned
hash is byte-identical before and after
(`de5bec229ffa404a85b9172fdc808ef6035716c3df8b308c816e90ffb54fd6e9`). No archived record changes.

---

---

## D27 - Every row of the review report names the evidence it rests on, and the two dates are allowed to differ (2026-09-08)

**Decision.** `Report.items` carries an origin per row, `host` or `suite`, and
`docs/REVIEW-REPORT.md` gains three things that follow from it: an Evidence column on the item
table, a Provenance table that gives each source its own UTC timestamp, and a sign-off that prints
`Report generated (UTC)` and `Suite evidence (UTC)` as separate lines with the suite commit, the run
URL and the local checkout's sha and clean/dirty state beside them. Per-section dates come from the
newest evidence that contributed to the section, not from the calendar. `date.today()` is gone from
the report entirely.

**Why.** The report used to print one date, taken from the clock at the moment it was written, over
every row on the page. That was true while preflight ran the suite itself. It stopped being true
the moment `--reuse-suite` existed, because the suite rows can now come out of a CI artifact built
on another machine, on another commit, days earlier, while the host rows are measured in the same
second the file is written. One date over mixed evidence is a claim about the whole page that is
true of only part of it, and the part it is false about is the part a reader is most likely to
trust. So the origin column and the provenance table land before the first HOST row, not after.

`scripts/merge_junit.py` is what makes the suite half answerable. It merges the five shards into one
`<testsuites>` document, which preflight's existing `root.iter('testcase')` walk reads without a
parser change, and writes `preflight-evidence.json` beside it carrying the commit, the ref, the run
URL, the shards actually merged, the totals and `merged_at_utc`. The field is named for what it is:
the moment of the merge step, not the moment the suite started. A shard that did not upload fails
the merge by name, because a partial merge parses cleanly, counts cleanly, and reports a green suite
for tests that never ran.

**Rejected.** Dating the whole report by the suite artifact, which would make the host rows lie
instead of the suite rows. Rejected: refusing to reuse an artifact from a different commit, because
the useful case is exactly a report generated from a working tree that has moved on, and the honest
answer is to say so rather than to forbid it; the provenance table prints `NOT the commit the suite
artifact was built from` and lets the reader decide. Rejected: making the HOST rows gate. Preflight's
exit code answers whether the code is fit to run, not whether this machine happens to be running it,
and a dead watchdog is a loud FAIL and exit 0.

**Evidence.** `scripts/preflight.py`: `Report.add(..., origin=)`, `run_suite` returning
`(results, evidence)`, `--suite-evidence`, `_checkout_provenance`, `section_HOST` with HOST1 to
HOST6, and the rewritten `write_report`. `scripts/merge_junit.py`. `.github/workflows/ci.yml`: the
`suite` job's `--junitxml` and artifact upload, and the `suite-evidence` job. Tests C37, C37b, C37c,
C38 and C38b in `tests/test_t5_hygiene.py`.

**Consequence.** `Report.items` widened from four fields to five and is unpacked in five places; all
five moved together. The host procedure is now `gh run download <run-id> -n preflight-suite -D
.fullsend/` followed by `scripts/preflight.py --reuse-suite --docker`, which is the docker and HOST
checks only rather than half an hour of contended CPU. HOST5 keeps its own copy of the
config-to-`RK_*` mapping honest by asserting the table names every `-e RK_...` line in
`scripts/run.ps1`; without that assertion the check would pass forever on a mapping nobody
maintains. HOST1 derives the expected watchdog arguments by reading them out of `start.ps1` rather
than retyping them, for the same reason.

**Still open.** `docs/REVIEW-REPORT.md` has not been regenerated. Doing so changes the PASS/FAIL/
MANUAL counts that `rk-overview` publishes as prose, and those counts trace to none of the four
sources rule 10 allows. The report and the routing of its counts through `key_findings.json` have to
land together or the public page states a number that is both stale and untraceable.

**Epoch impact.** None. `scripts/`, `tests/`, `.github/` and `docs/`; no verifier-pinned file, no
`VERIFIER_HASH`, no `Dockerfile`, no `entrypoint.sh`, no score.

---

---

## D28 - The golden gate and the restorable workspace copies are recorded as they are, and recording is kept apart from widening (2026-09-08)

**Decision.** `scripts/hygiene.py` machine-checks the rules this repository states about itself, and
two of its checks are tripwires. `tests/golden_gate.txt` pins the 55 node ids the container's `-k`
expression collects today, and `check_workspace_copies` byte-compares
`scripts/workspace/{start,stop,watcher,stats}.ps1` and `configure.py` against the workspace root.
Both go red the moment anything moves. `VERIFIER_FILES.txt` joins them as a plain manifest of the
ten pinned paths: a second copy whose only job is to make changing the pinned set loud.

**Why these are tripwires and not targets.** The gate currently selects G1 through G20 and K1, K2.
G21 to G27 and K7 to K16 exist in the suite and are outside the gate deliberately, because every
test the gate selects is time the container spends before the runner starts. A coverage check
invites the reflex of widening the gate until it covers everything, which would buy nothing and cost
container start time on every restart. So the check records the gate rather than arguing for it, and
the failure message says as much in the same breath as it names what entered and what left. A node
id that disappears is either a rename or a test that stopped running before the container starts;
those want different responses, and only a person reading the diff can tell them apart.
`--gate-coverage --write` makes the intended update one command.

The workspace copies are the same shape of problem. The root copies are the ones that run; the repo
copies exist so the workspace can be rebuilt from a clone, which they can only do while they are
identical. That check cannot run in CI, because the root scripts live in the parent repository and
CI sees one checkout, so it is HOST6 in preflight, where both trees are present.

**Rejected.** Asserting that every test prefix under `tests/` is covered by the gate filter. It is
the obvious form of the check and it is wrong: it fails immediately on G21 to G27 and K7 to K16, and
the only ways to make it pass are to widen the gate or to delete tests. C33 asserts coverage of the
prefixes the gate is *meant* to select, and says so in the assertion message. Rejected: folding the
ASCII scan into the PowerShell parse step. `pwsh` on a runner is PowerShell 7, which parses a smart
quote and an em dash that Windows PowerShell 5.1 on the host will not, so the parse step would go
green on exactly the file that stops `start.ps1` from running. Rejected: checking the workspace
copies from CI by fetching them out of the parent repository, which would need either that repo to
be public, which is not stated anywhere in the workspace, or a read-only credential, in a system
whose rule is that the only push-capable token stays on the host.

**Evidence.** `scripts/hygiene.py` with `check_ps1_ascii`, `check_entrypoint_lf`,
`check_verifier_manifest`, `check_gate_expression`, `check_gate_coverage`, `check_workspace_copies`
and `check_suite_desc`. `VERIFIER_FILES.txt`, `tests/golden_gate.txt` (55 ids, generated). Tests C30
to C36 in `tests/test_t5_hygiene.py`. The `hygiene` job and the `gate` job's gate-coverage step in
`.github/workflows/ci.yml`. Negative controls run by hand on 2026-09-08: an em dash planted in a
scratch `.ps1` is reported as `scripts/b.ps1:1:16 U+2014`; a CRLF copy of `entrypoint.sh` is refused
by line number; a line removed from `tests/golden_gate.txt` is named in the failure.

**Consequence.** Three copies of the golden-gate `-k` expression, in `entrypoint.sh` and twice in
`ci.yml`, are now asserted identical, so CI can no longer go green on a set the container never
runs. Renaming a test inside the gate churns `tests/golden_gate.txt`, which is intended. Changing
the pinned verifier set now requires editing two files, one of which exists only to make that edit
loud; the manifest is not read by `verifier_hash.compute_verifier_hash` and does not move the hash,
which C34 asserts three ways rather than assuming. The `hygiene` job installs nothing and has no
`needs`, so it does not wait on the gate.

**Epoch impact.** None. The pinned hash is byte-identical before and after
(`de5bec229ffa404a85b9172fdc808ef6035716c3df8b308c816e90ffb54fd6e9`); `VERIFIER_FILES.txt` sits
beside `VERIFIER_HASH` and is read by nothing that computes it.

---

## D32 - A paused container may not be killed for the silence the pause caused (2026-09-09)

**Decision.** Stamp `$resumedAt` on every unpause, and suppress the heartbeat-stale kill for one
staleness window afterwards. Both resume paths, the CPU guard and the battery guard, stamp it.

**What happened, from the watchdog's own log.** 08:11:25Z host CPU 71.99 percent above 70 for
30 s, docker pause. 08:35:50Z host CPU 16.11 percent below 30 for 30 s, docker unpause.
08:36:00Z heartbeat stale 1474.8 s, docker kill. Exit 137, not an OOM. The run was down for
about thirty minutes.

**Why the existing guard did not cover it.** The stale check already skipped while `$isPaused`,
and there is a startup grace for a container younger than the threshold. Neither covers the ten
seconds after a resume, when `$isPaused` is correctly false, container uptime is twelve hours,
and the heartbeat age still carries the whole pause. A paused process is frozen, so it cannot
have written one. The guard paused the container and then killed it for the silence it had
itself caused.

**This was reachable because of D20.** The defect is as old as the two guards, but it needs a
pause longer than `heartbeat_stale_seconds`, and the guard sat near-inert at 97/85/90/88 until
D20 restored the shipped 70/30/60/40 the day before. D20's measurement was sound as far as it
went: the baseline really is a median of 25 percent and the guard really does not fire on
ambient load. What it did not ask was what happens after the guard fires legitimately.

**Why it was diagnosed in one read.** P07 gave the watchdog a log file and `status.py` the
ability to read it back, landed the previous day. Before that this sequence existed only in a
minimised console window that `start.ps1` had already replaced, and the evidence would have
been an exit code and a guess.

**Evidence.** `rk-harness/scripts/watchdog.ps1`: `$resumedAt`, the `$resumeGrace` term in the
stale check, and the two unpause sites. `watchdog.log` lines 144, 147, 148.

**Consequence.** A resume is followed by one quiet window in which a genuinely dead container
is not killed. That is the right trade: the stale kill exists for a hung runner, and a hung
runner stays hung for the next window too.

**Epoch impact.** None. A host script.

---

## D33 - The lane-search inertness gate exempts read-only viewers, by name and by function (2026-09-09)

**Decision.** `test_nothing_in_the_package_runs_the_lane_search` now allows a named viewer to
call a named read-only surface of `lanesearch`, and fails on anything else. `_LANE_VIEWERS` is
`{watch.py}`; `_LANE_READERS` is the eight functions that answer a question about a lane archive
without being able to create one. A second test asserts every name in that surface is a real
function and that none of them opens a file for writing, so the exemption cannot widen by typo
or by a reader quietly becoming a writer.

**Why, and the alternative it rejects.** The gate exists so no cycle runs a lane before batch 9
wires the rotation. A viewer that renders one row per existing lane archive cannot start a
search, so it does not arm anything. The alternative was to drop `_lanesearch_rows` from
`watch.py` until the lanes are wired, and that defeats the point of doing the viewers first:
the whole reason batch 7 precedes batch 10 is so that no cycle spent on a lane ever reads as a
stalled explicit cycle. Removing the row would guarantee exactly one release where it does.

**Evidence.** `rk_harness/watch.py:478-506` calls `lanesearch.lane_dir` and `lanesearch.status`
and nothing else; verified by injecting `lanesearch.step` into `watch.py` (gate fails) and a
bare mention into `runner.py` (gate fails), both restored.

**Consequence.** `runner.py`, `islands.py` and every other module in the package remain unable to
mention the module at all. When batch 9 wires the loop, the change is to add `runner.py` to
`_LANE_VIEWERS` with the driver functions it needs, deliberately, and the second test will then
refuse to call those functions readers.

**Closes.** The one red test blocking batches 6 and 7.

**Epoch impact.** None. A test file and an unpinned viewer.

---

## D34 - An off-archive count is points, not ledger lines, and the code-hash card is the latest measurement (2026-09-09)

**Decision.** Two one-line rules applied in three places. `_distinct_points` counts distinct
`(job, key)` pairs, so a point re-measured under a later code hash is one point.
`_newest_code_hash` picks the hash of the entry with the greatest timestamp rather than the
alphabetically last. `rk-overview/tools/key_findings.py` stops recomputing both and takes its
totals from `rk_harness.sidetrack.status()`, which already drew the distinction correctly.

**What was published.** The measurement ledger holds 143 ok lines and 103 distinct points: 40 were
re-measured after the executor changed. Every card captioned "one per parameter point in the plan"
showed the line count. The findings site said adaptive 80 (56), implicit 63 (47), ledger 143 (103);
the overview said `143/40`, a hand-typed plan size of 40 against a line count of 143, which reads
as more work done than the plan holds. Both sites showed code hash `a98acb39fb4f`, the superseded
digest, while the executor had been on `305c692a1b5c085a` since cycle 2540. It sorted higher, and
these are digests, so their lexicographic order carries no information at all.

**Why it mattered more today.** Both defects predate this work and both were live. The split into
three class pages lifts the adaptive figure onto the hub as one of three headline class numbers,
where a reader sets it beside the archive record count, and puts the code-hash card on two more
pages. Promoting a wrong number is a reason to fix it, not a reason to call it pre-existing.

**Consequence.** A caption now says so when the two differ: "24 of them were measured again after
the executor changed". `points_planned` comes from the catalogue and is absent rather than wrong
when the catalogue cannot be read. `ledger_lines` and `points_remeasured` are new keys in
`key_findings.json`, so the raw count is still available to anything that wants it.

**Evidence.** `rk_harness/sitegen.py` `_distinct_points`, `_newest_code_hash`, `_points_caption`;
`rk-overview/tools/key_findings.py` side-tracks block; `rk-overview/tools/generate.py`
`sidetrack_section`. Confirmed against the live ledger: 56 / 47 / 103 and `305c692a1b5c`.

**Epoch impact.** None. Nothing scored is read or written; these are off-archive counts.

---

## D35 - The saturation window is measured in the lane the rule is about (2026-09-09)

**Decision.** `saturation.search_hours_since` sums the recorded seconds of explicit-lane cycles
since the last progress event, read from `rk-work/schedule/cycles.jsonl`, and `assess` compares
that to the 48-hour window instead of wall clock. With no lane log it returns None and the caller
falls back to wall clock, stating which measure ran in `window_basis`.

**Why.** The rule was written when every cycle was an explicit search, so wall clock and search
time were one quantity and nobody had to say which one the window meant. Under a rotation they
part company: at a third of the cycles the explicit lane accumulates 48 hours of searching in
about 144 hours of wall clock. A window still counted in wall clock would reach FREEZE on a third
of the evidence the rule was designed to require, and would do it while the machine was doing
exactly what it had been told. `auto_freeze` is false so the verdict is advisory, but a wrong
advisory is worse than none: it is the thing a reader checks instead of thinking.

**Why not scale the threshold by the scheduled share.** The scheduled share is what was asked for;
the recorded seconds are what happened. Substituting the first for the second is the failure mode
`lanes._MEASURED_NOT_INTENDED` exists to name, and the survey behind this rotation found a run
whose intended split had in fact been 0.0005 percent.

**Direction of error.** The tail is bounded at `LANE_WINDOW_CYCLES = 6000`, about 293 hours at
the 176 s cadence measured 2026-09-09. When it does not reach back to the progress event the sum
is a floor and `window_basis_complete` is False. An undercount can only delay a freeze, never
trigger one early, which is the safe direction for a rule whose action is to stop the run.

**Consequence.** Today, with no lane log, the assessment is the old one unchanged: CONTINUE,
17.52 hours since progress, basis "wall clock; no lane log, so every cycle is an explicit search".
This lands before the rotation is armed, which is the point.

**Evidence.** `rk_harness/saturation.py` `search_hours_since`; `tests/test_t9_saturation.py`
S9 to S16.

**Epoch impact.** None. The orchestrator is operational, unpinned, and advisory.

---

## D36 - The system drive gets a guard, because the run cannot outlive the daemon (2026-09-09)

**Decision.** `watchdog.min_free_system_gb = 2.0`, up from 0, which was report-only.

**Why now.** The watchdog has been logging "system drive C: free 4.4 GB and unguarded" every
thirty minutes. C: is at 100 percent with about 4.3 GB free and falling; Docker's VHDX is 43.7 GB
and lives there, and `docker system df` reports 23 GB of build cache with 14.16 GB reclaimable
plus 7 GB of reclaimable images. None of that is the run's doing: `rk-work` is on D:, which has
200 GB free.

**What the guard buys.** At 2 GB the watchdog stops the container. A stopped run resumes with
start.ps1. A Docker daemon that hits a full VHDX does not necessarily, and takes the archive's
writer with it mid-append. Stopping deliberately at a threshold is the recoverable failure.

**What it does not fix.** The disk still fills. Reclaiming the build cache is a machine-wide action
that touches other projects' caches and is the owner's call, not a side effect of this work; it is
recorded here as the recommendation rather than taken.

**Evidence.** `rk-harness/scripts/watchdog.ps1:346-364`; `watchdog.log` 2026-09-09T20:18:07Z and
20:48:10Z; `docker system df`.

**Epoch impact.** None. A host config value.

---

## D37 - The cycle loop asks which lane it is on, and every cycle records the answer (2026-09-09)

**Decision.** `_run_cycle` resolves the schedule once at the top, `lanes.lane_for` names the lane,
and a lane that is not explicit branches to `_run_lane_cycle` immediately after the archive
replay. Every cycle, explicit included, appends a row to `rk-work/schedule/cycles.jsonl` and
refreshes `shares.json`. `cycle_done` gains a `lane` field. Shipped with
`run.lane_schedule = 'E'`, which is one explicit search per cycle: the behaviour the run has
always had, now taking the same code path an armed schedule takes.

**Where the branch sits, and why there.** After the replay, before the encourager. The site build
needs the archive state, so the replay is shared; nothing below that line does anything a lane
wants. A lane cycle does not call the encourager, because the encourager's domain is the archive
grid and a lane record does not live in a cell. It does not call the model, because a directive
names a target order and a stage count and this lane is not choosing one. It does not enumerate,
evaluate or verify, because nothing here goes through the pinned checker. It appends to the
lane's own unpinned archive and to nothing else.

**`stall_counter` is passed through untouched, and this is the edit that matters most.** It counts
cycles since the explicit search last improved a cell. A lane cycle neither advances it (the
explicit search did not fail, it did not run) nor clears it (nothing improved). Advancing it is
how a rotation manufactures a false stall, and the stall counter feeds the encourager's
escalation.

**Every cycle writes a row, not just the lanes.** A log that recorded only the lanes would leave
the explicit share unmeasurable, and an unmeasurable share is exactly how an intended 70/15/15
and a real 0.0005 percent drifted apart with nothing in the system in a position to notice. The
row separates `records_appended` (scored) from `lane_records_appended` (the lane's own archive),
because a lane cycle appending nothing scored is a fact to read rather than a gap.

**Reporting may not end a cycle.** Both writes are wrapped: a failure logs `lane_log_failed` or
`lane_shares_failed` and the cycle completes. The work is upstream of the record of it.

**The gate changed deliberately, which is what D33 said this moment would look like.** The
allowlist now has two roles. A VIEWER (`watch.py`) may call the read-only surface and nothing
else. A DRIVER (`runner.py`) may also call `step`, and exactly one module is allowed to be one,
because deciding how the machine spends its time belongs in the cycle loop. A second test asserts
the distinction survives: no viewer may call `step` even now that the runner can, since a live
view that could start a measurement would make the act of looking at the run change it.

**Measured end to end, off in a scratch directory, with the real search rather than a stub.** One
adaptive cycle: 59.3 s, one record. One implicit cycle: 45.9 s, fifteen records. `stall_counter`
41 before and 41 after both. No scored archive directory was created. Both rows validated,
`shares.json` written and schema-checked, no failure event.

**Evidence.** `rk_harness/runner.py` `_run_lane_cycle`, `_record_cycle`, `_lane_budget_seconds`;
`tests/test_t16_lane_cycle.py`; `tests/test_t16_lanesearch.py` `_LANE_VIEWERS` / `_LANE_DRIVERS`.

**Epoch impact.** None while disarmed, and none when armed: no pinned file is read or written by a
lane, nothing a lane produces is scored, and `VERIFIER_HASH` is not involved.

---

## D38 - The rotation is armed, and the three classes get equal turns (2026-09-09)

**Decision.** `run.lane_schedule = 'EAI'`. One cycle in three searches the explicit archive, one
measures the adaptive lane, one measures the implicit lane. `run.lane_max_seconds = 180`, matching
the side-track budget it replaces.

**Why now and not earlier.** The blocker was never the schedule, it was that the two side classes
had no open-ended work. `sidetrack.JOBS` is a finite catalogue of 103 points, complete since cycle
2540, and every firing since has logged `sidetrack_exhausted` in about five hundredths of a
second. Arming before B5 would have handed two thirds of the machine an empty queue.
`lanesearch.py` is that queue: a deterministic unbounded enumeration per lane. The end-to-end run
above measured 1 adaptive and 15 implicit candidates in two cycles, so the queue produces work.

**What the run gives up.** Two thirds of its cycles. The explicit archive will grow at about a
third of its previous rate. That is the request, stated plainly: equal time is not equal time if
one class keeps 97 percent of it.

**What protects the explicit half from being misread as broken.** All of it landed first, on
purpose. `saturation` measures its 48-hour window in explicit-lane seconds (D35), so it will not
reach FREEZE on a third of the evidence. `stall_counter` advances only on explicit cycles (D37).
The viewers name the lane a cycle belonged to, and `accept_rate` counts the explicit lane once a
cycle carries the field. `watchdog.no_candidate_minutes` stays 30: the longest run of non-explicit
letters in 'EAI' is two, so the worst gap between verified candidates is three cycles, 8.8 minutes
at the measured 176 s cadence and 15 at 300 s.

**Rule 10 is not relaxed, and the measured split does not go on a page.** The obvious way to make
the sites "reflect" the rotation would be to publish the realised share. That is exactly the
attention split CLAUDE.md rule 10 keeps internal, and B6 added a build gate that fails on a
percentage triple. The sites reflect the three classes by giving them a page, a nav entry and a
hub card each, with explicit leading, which is the structural claim. The time budget stays in
`rk-work/schedule/shares.json` and in the host viewers, where the people running it can see it.

**What the lane archives may say on a page, which is still only that they exist.**
`lanesearch`'s own records carry `NOT_A_PAGE_SOURCE`, and the traceability list in CLAUDE.md
the traceability list in CLAUDE.md does not name them. Arming does not change that: the class pages will flip their presence
line from "not written yet" to written, and will quote no number out of either archive. Whether
`rk-work/validation/axes.json` joins the list is a separate question and still the owner's,
because it is the one that would let a lane's PERFORMANCE be published.

**Side tracks are left switched on at every 20 cycles.** They now fire only on explicit cycles,
since `_maybe_sidetrack` sits below the branch. The catalogue is exhausted so each firing costs
about 0.05 s, and leaving the cadence set means a refilled catalogue (B8) resumes without a config
change.

**Reversal is one value.** `python configure.py set run.lane_schedule=E`, then a restart. Nothing
a lane wrote is in the scored archive, so reverting loses no scored work and invalidates no score.

**Epoch impact.** None. This is where the run spends its time, not what it considers correct.
`VERIFIER_HASH` is untouched and every archived score keeps its meaning.

---

## D39 - rk-work/validation/axes.json stays off the traceability list (2026-09-10)

**Decision.** `rk-work/validation/axes.json` is not a permitted source for a number on a public
page and stays off. This answers the question `docs/THIRDS-2026-09-09.md` section 5 left with the
owner rather than deferring it again.

**Why.** `rk-work/benchmark/results.json` already carries a matched-ACCURACY comparison across all
three classes on the same eight application problems. It fixes the achieved error and reports the
work each side spent to reach it, stating per row what it controls for and what it does not. That
document is on the list already and its numbers are published already. Axis T asks the same
question in a different shape, cycles to reach a tolerance rather than work at a fixed achieved
error. Admitting a second source for a claim the site can already make lengthens a list whose
value is its shortness: a reader checking a published number has to hold the whole list in their
head, and every entry costs everyone who reads one.

**The alternative this rejects.** "Admit it, the machinery is written." The loader, the presence
panel and the glossary entries for axis T are in place, so admission looks free. It is not.
Admitting the document means writing it, and its writer, `validation_axes.py`, sits inside
`LANESEARCH_FILES`. Editing that file moves `lanesearch.code_hash()` and re-opens every measured
lane candidate, which is a real bill for a document that has no rows in the work directory today.

**Evidence.** `rk_harness/benchmark.py:45-58`, the three-class sections and their shared
condition; `rk-work/benchmark/results.json`, key `matched_accuracy`;
`rk_harness/lanesearch.py:141`, `validation_axes.py` inside `LANESEARCH_FILES`;
`docs/THIRDS-2026-09-09.md:104-110`, the question as it was left; `rk-work/validation/` holds
`results.json` and no `axes.json`.

**Consequence.** The adaptive and implicit class pages carry ledger readings, the class definition
and, under D40, ranked elites. No page states an axis-T number sourced from `axes.json`.
`validation_axes.py` stays what it is, the cost model and the implicit probe that `lanesearch`
measures through, rather than the writer of a published document.

**What would reopen this.** A claim matched accuracy cannot support. Matched accuracy compares at
an error every side reached, so a tolerance that one class reaches and another cannot has no
matched row, and a question about that tolerance needs axis T. If such a claim is worth
publishing, this entry is revisited and `axes.json` is where the number comes from.

**Closes.** `docs/THIRDS-2026-09-09.md` section 5, owner question 1, answered as no.

**Epoch impact.** None. No scored record is read or written, no digest moves, and `VERIFIER_HASH`
is untouched. This decides what may be published, not what is measured.

---

## D40 - The lane elites documents join the traceability list, the per-day records do not (2026-09-10)

**Decision.** `rk-work/adaptive_archive/elites.json` and `rk-work/implicit_archive/elites.json`
are permitted sources for a number on a public page. `rk-work/{lane}_archive/YYYY-MM-DD.jsonl`
stays off. A number taken from an elites document is rendered with its metric
(cycles-to-tolerance on the axis-T ladder), its arithmetic (float64) and the fact that nothing in
it is order-verified against the pinned checker, the way a side-track number carries its job's own
`arithmetic` string (D24).

**Why the elites and not the records behind them.** The elites document is bounded at
`ELITE_CAP = 32`, ranked by a total order that reads no clock and samples nothing, validated
against `lane-elites/1`, and it carries `rule` and `statement` fields saying exactly what it
ranked and over what. It is 54.9 KB for adaptive and 45.3 KB for implicit. A per-day file is a
search log: about 18 KB per record, 164.1 MB of adaptive records and 168.4 MB of implicit records
on disk on 2026-09-10, and its per-candidate rows sit at the grain of a scored archive record
while meaning something else. That is the comparison `not_comparable` exists to prevent, and the
way to prevent it is to leave the log unquotable rather than to ask every page to be careful with
it.

**The alternative this rejects.** "Admit the lane archive whole, since the elites are derived from
the records, and a list that admits a summary while refusing its input is inconsistent." The list
is not a list of true files. It is a list of documents that can be quoted without misleading a
reader, and a capped, ranked, self-describing document meets that where the 8,900-line input
behind it does not.

**What it cost.** `NOT_A_PAGE_SOURCE` is record content: `build_record` writes it into every
record and `build_elites` into the elites `_meta`, so changing the publication contract changes
what a record says. Rewriting it moves `lanesearch.code_hash()` off `6a5be0d8f26639ea` and
re-opens every candidate measured under that digest, 8,928 adaptive and 8,958 implicit ledger
lines when this was written, about 17,900. At the measured 0.49 s per adaptive candidate and
1.77 s per implicit candidate that is roughly 1.2 hours of adaptive-lane seconds and 4.4 hours of
implicit-lane seconds to stand where the run stands today, spread across the rotation.

**Why that is the digest working rather than waste.** A record written under the old contract
asserts on its face that no page may quote this lane at all. A record written under the new one
does not. The two generations mean different things and should not be indistinguishable; the code
hash is what tells them apart, and a contract sentence that rides in a field is exactly the record
content the hash exists to cover. Nothing scored is touched, so the bill is paid in lane seconds.

**Evidence.** `rk_harness/lanesearch.py:178-182`, `NOT_A_PAGE_SOURCE`; `:1026` and `:1272`, where
it is written into a record and into `_meta`; `:229` `ELITE_CAP`; `:1161` `ELITE_RULE`; `:1230`
`build_elites`; `rk_harness/sitegen.py` `_lane_elites_path` and `_load_lane_archive`, the loader
that already reads the file; `rk-work/adaptive_archive/ledger.jsonl` and
`rk-work/implicit_archive/ledger.jsonl`, 8,928 and 8,958 lines under `6a5be0d8f26639ea`; the
CLAUDE.md Prose paragraph and `docs/LANESEARCH.md`.

**Consequence.** A class page may quote a ranked elite: its median cycles at the elite target, how
many problems it reached, its tableau. It may not quote a per-day record, and no loader for one
exists. `NOT_A_PAGE_SOURCE` states the split now instead of a blanket refusal, so a record says
which of the two documents it stands behind may be published.

**Closes.** `docs/THIRDS-2026-09-09.md` section 5, the question it raised separately for the two
lane archives, and `docs/LANESEARCH.md`'s "whether these numbers may ever be published".

**Epoch impact.** None on the scored archive. `VERIFIER_HASH` is untouched, no scored record is
read or written, and every archived score keeps its meaning. The lane digest does move, which
re-opens lane candidates and nothing else.

---

## D29 - A status file states its own expiry, and can be asked its age without being rewritten (2026-09-08)

**Decision.** `stats.txt` declares a staleness deadline on both paths, not only when a loop wrote
it. A file written once carries `ONE_SHOT_SHELF_LIFE_S`, fifteen minutes, computed from its own
`written_at` rather than from a clock read at render time. `rk_harness.status` gains
`read_written_at` and `age_report`, and `--age` answers the freshness question from the file's
header alone, running no probe and rewriting nothing: exit 0 current, 1 past the declared shelf
life, 2 unreadable. `stop.ps1` stops the background writer by this workspace's own full path and
then takes one final one-shot reading, so the last state on disk is the stopped container.

**Why.** The header used to tell a reader of a one-shot file that `stats.ps1 -Loop` would keep it
current. That is a fact about a writer that is not running, and it left the reader to work out for
themselves whether the numbers below it were two minutes or two days old, which is the arithmetic
the deadline exists to remove. Rejected: rendering the deadline from `_utcnow()`, which is simpler
and wrong, because the file would then claim a future for a document written last Tuesday and
`--age` and the header could disagree. The deadline is a pure function of `written_at`, which is
also what lets `--age` read it back and reach the same answer.

Fifteen minutes is longer than any cycle at the current cadence, so a one-shot file written between
cycles is still describing the present, and short enough that nobody builds a habit of trusting a
stale one. A file written by a loop keeps the six-interval rule it already had, and `age_report`
reads the interval out of the header so the two cannot drift.

**Evidence.** `rk_harness/status.py`: `ONE_SHOT_SHELF_LIFE_S`, the `refresh_s is None` branch of
`render_text`, `read_written_at`, `age_report`, and the `--age` branch of `main` which returns
before `collect` is reached. `stop.ps1`: the escaped-full-path matcher and the final write.
`stats.ps1 -Age`. Tests S24 to S29 in `tests/test_t5_status.py`; S12 updated in the same change,
because it asserted the previous behaviour.

**Consequence.** The stats writer is matched by full path and never by `*stats.ps1*`. A second
harness in this workspace may ship its own `stats.ps1`, exactly as rk's `*watchdog.ps1*` matcher
already forces that harness to name its watchdog `scripts/watchdog-jobs.ps1`.

**Closes.** A file that could be arbitrarily old while looking exactly like a fresh one.

**Epoch impact.** None. Host-side reporting only: no verifier-pinned file, no `VERIFIER_HASH`, no
score.

---

## D30 - Alert on the rate of work, not on signs of life (2026-09-08)

**Decision.** `stats.txt` reports acceptance rate and the model-directive gap alongside cadence.
`accept_rate` compares the median of the newest third of the `cycle_done` events in the tail
against the median of the oldest third and prints both medians with both sample counts;
`directive_gap` counts cycles since the last `directive_accepted` carrying `source="llm"` and reads
back the plan snapshot that gate last saw. `watchdog.ps1` gains `-LogFile`, and `status` reads the
tail of that log into a new section. The collapse verdict renders as a row inside PROGRESS, not in
PROBLEMS.

**Why.** The run was fast, green, heartbeating and scientifically dead for thirty-five hours and
nothing in the system had anything to say about it. D25's `progress_note` catches a run that has
stopped completing cycles; it cannot catch a run completing cycles that accept nothing, or one that
has not heard from the model in a thousand cycles because a usage snapshot latched a gate shut.
Both are rates, and neither is visible in any liveness signal.

The verdict needs both windows because a byte tail is not a cycle tail: phase 0 and 1 cycles
enumerate in bulk and occupy more bytes each, so the two thirds can straddle a phase change and a
ratio alone would read that as a collapse. Ten samples per window are required before any verdict
is offered, and the row prints the two medians either way, so a phase change can read as a phase
change. No division anywhere: a zero baseline is not an infinite collapse and a rise is not a
collapse at all.

Rejected: putting the collapse row in PROBLEMS. That section is headed PROBLEMS READING STATE, is
documented as where a failed probe lands, and its exact title is pinned by S9. `progress_note` is
the precedent for a derived judgement, and it renders as a STUCK row inside PROGRESS. Widening the
section is an owner call, not a side effect of this change.

The watchdog log exists because the watchdog runs in a minimized window nobody looks at, so an
ALERT or a pause is printed once and lost. Its stamps are UTC with a `Z`, not `Get-Date -Format s`,
which is local and unlabelled: `status._parse_ts` treats a naive stamp as UTC and would be seven
hours wrong on this machine. The section says in its own closing lines that these are lines the
watchdog printed when it acted, not a reading of the container's state now, which is what keeps
property 2 intact.

**Evidence.** `rk_harness/status.py`: `PRODUCTIVITY_TAIL_BYTES`, `ACCEPT_MIN_SAMPLES`,
`ACCEPT_COLLAPSE_RATIO`, the `kinds` gate in `tail_events`, `accept_rate`, `directive_gap`,
`read_watchdog_log`, the new PROGRESS rows and the WHAT THE WATCHDOG SAID section.
`scripts/watchdog.ps1`: `-LogFile`, `Say`, `Rotate-Log`. `runner.py:533` is where `source="llm"`
is written and `runner.py:715` where a gate names itself; neither file is touched by this change.
Tests S30 to S39. Measured on this host 2026-09-08: the four-megabyte tail read costs 33 ms and the
substring gate cuts it from 14,461 parsed dicts to 407.

**Consequence.** The productivity read is four megabytes rather than 512 KB. `watch.py` has read
four megabytes every few seconds for months, which is the precedent, and the kind filter keeps the
parse cost at a tenth of the unfiltered read. `PRODUCTIVITY_TAIL_BYTES` is the single knob if it
ever hurts.

**Closes.** A run that can be green on every existing signal while producing nothing.

**Epoch impact.** None. Host-side reporting and one host script; no verifier-pinned file, no
`VERIFIER_HASH`, no `entrypoint.sh`, no score.

---

## D31 - The pause guard's docker probe is bounded, its peer list is deliberate, and the rest of the daemon becomes visible (2026-09-08)

**Decision.** `docker stats` runs through one bounded helper, `Get-DockerCpuRows`, as a child
process the watchdog can kill after `-StatsTimeoutMs` (default 4000). On timeout the guard decides
nothing for that pass: it clears the sample buffer, says so once per outage, and skips the pause
decision. While the container is paused no probe is made at all, because a frozen cgroup has no CPU
share to subtract. `-PeerContainers` (default `rk`) names the containers whose CPU is subtracted
from host load; every other container counts as foreground load the run should yield to. The
watchdog publishes each sample to `docker-cpu.json` for a second harness to read, and reads none
back. The system drive is reported and only guarded when `-MinFreeSystemGB` is non-zero. `status`
gains a census of every container on the daemon with status, health and restart count.

**Why, and one number corrected.** P12's arithmetic rested on `status.py`'s note that `docker
stats` was measured at 47 s here, which would turn a 10 s poll into a 50 s one and a twelve-sample
heartbeat window into a two-sample one. That measurement was taken on a saturated machine. Measured
2026-09-08 on a quiet one over five consecutive calls, the cost is 1.41 s minimum and 2.01 s median,
so the real effect today is an effective poll near 12 s and about ten samples inside the 120 s
window instead of twelve. That is a skew, not a collapse, and the 47 s figure must not be repeated
as if it were typical. The case for bounding the call stands on the tail rather than the median:
this daemon has been seen wedged and answering HTTP 500 on every endpoint, and an unbounded call
there sits on the path that decides whether the run is paused. The `WHERE THESE NUMBERS COME FROM`
line in `stats.txt` that stated the 47 s figure as fact is rewritten to give the durable reason
instead, which is that `docker stats` samples every container and has no timeout of its own.

The peer list stays a deliberate name list. Subtracting every container would let a crash-looping
third-party stack run this machine hot while the guard reported it quiet, which is the opposite of
why the guard exists. The default of `rk` alone reproduces today's behaviour exactly. A wrong name
silently subtracts nothing, so the startup line names the requested set and the earliest
successful poll names the resolved one.

The census reports what it can see this pass and never a trend. Rule 1 forbids carrying a value
forward and this module keeps no cache, so a restart count that is rising is not something it can
honestly compute; it flags single-sample facts instead, a restarting status, an unhealthy health
check, or a count already past `RESTART_FLAG`. Do not add a history file to make "climbing" work.

`docker-cpu.json` is written and never read here. rk is what wrote it, so a reader in rk would only
be rk reusing its own last sample, which is the carried-forward value this project refuses
everywhere else. The reader contract is in the comment: a consumer treats a file older than about
2.5 polls as absent and samples for itself, and no decision may ever wait on it. The second harness
is the reader, and it does not exist in this workspace yet.

**Evidence.** `scripts/watchdog.ps1`: `Get-DockerCpuRows`, `Write-CpuSample`, the rewritten
pause-guard preamble and block 5b. `rk_harness/status.py`: `RESTART_FLAG`, `probe_containers`,
`_ascii`, the `with_census` switch and the OTHER CONTAINERS ON THIS DAEMON section. Tests S40 to
S42 and C25 to C28. The 1.41/2.01 s figures were measured on this host on 2026-09-08; the parsing
half of `Get-DockerCpuRows` was exercised against canned output rather than the live daemon.

**Consequence.** Three watchdog parameters need `configure.py` keys and `start.ps1` wiring, and
C25 fails until every `watchdog.*` key in `SCHEMA` is passed. C28 stays skipped until
`watchdog.peer_containers` exists. `min_free_system_gb` defaults to 0, so nothing stops the run on
the system drive without the owner turning it on. The census names third-party containers and
belongs in `stats.txt`, which is gitignored; it must never reach a findings page, where every
number has to trace to `key_findings.json`, `validation/results.json`, `benchmark/results.json` or
the side-track ledger, and a restart count traces to none of them.

**Closes.** An unbounded call on the pause path, a guard that could only ever subtract one
container, and a shared machine whose other containers no status file mentioned.

**Epoch impact.** None. Host scripts and host-side reporting; no verifier-pinned file, no
`VERIFIER_HASH`, no `Dockerfile`, no `entrypoint.sh`, no score.


---
