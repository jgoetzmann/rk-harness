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
