# Side-track automation: letting the container carry the adaptive and implicit tracks

Status: **Tier A complete 2026-09-04, shipped disabled** (`run.sidetrack_every_cycles = 0`).
The executor, the five jobs, the runner hook, the config surface, the findings-site page and 25
tests are all in place. Section 8 marks which acceptance criteria have passed. What remains is a
decision, not work: enabling it is one `configure.py set` plus a restart.
Scope: operational configuration plus one unpinned module. No verifier-pinned file changes,
no epoch boundary, no archive schema change, no re-pin.
Companions: `docs/ROADMAP.md` (epoch model, 70/15/15 rotation), `docs/EPOCH2-DESIGN.md` and
`docs/EPOCH3-DESIGN.md` (the scored designs this work feeds).

## 0. The question this answers

Owner, 2026-09-04: *would the container run some of these tests eventually, or is it up to the
orchestrator to decide if it wants to pursue it?*

Today it is the orchestrator, entirely, and not by scheduling accident. Three independent locks
stop the running container from doing adaptive or implicit work at all:

1. **The cycle loop never calls the code.** `rk_harness/runner.py:22-37` imports archive,
   directive, enumeration, evaluator, ledger, literature, search, sitegen and verifier. It does
   not import `prototypes`, `validation` or `benchmark`. Those three have their own `__main__`
   blocks and are operator-run CLIs. `sitegen` only *publishes* `validation.html` and
   `benchmark.html`, and only when the results file already exists on disk
   (`sitegen.py:2096-2117`).
2. **The verifier rejects the method classes.** `verifier.py:85-87` returns `NOT_EXPLICIT` for a
   nonzero entry on or above the diagonal of A. An SDIRK tableau has a nonzero diagonal by
   definition. `search.py` fills only strictly lower triangular A.
3. **The directive cannot express the request.** The schema is `target_order`, `stages`, and
   `constraints{force_zero, dyadic_denominator_max, c_fixed, b_nonneg}`
   (`directive.py:210-228`). There is no field for method class, embedded pair or error
   estimator, so the LLM planner can only steer where in the explicit grid to look.

Consequence: the container's share of the 70/15/15 obligation is currently met by reading alone
(`literature.TRACK_SCHEDULE`, 3 of 20 slots to each side track). Every measured side-track
artifact in `rk-work/prototypes/` was produced by a human-driven session on 2026-09-02 and has
not changed since.

This document plans the smallest change that lets the container carry the side tracks itself,
and states how to tell whether it worked.

### 0.1 What this is not

It does not put adaptive or implicit methods in the scored archive. That is an epoch boundary
and it needs pinned-file changes, new golden fixtures and a verifier hash re-pin; those are
already specified in `EPOCH2-DESIGN.md` sections 2-9 and `EPOCH3-DESIGN.md`. Section 2 below
draws the line, and section 8 phase 5 states the gate. Automating the side tracks is a
prerequisite for those epochs (it produces their missing inputs); it is not a step into them.

## 1. Measurements this plan rests on

All taken 2026-09-04 on the owner's host against the live run at cycle 999.

| # | Measurement | Value | Method |
| --- | --- | --- | --- |
| M1 | Cycle wall clock, cycles 993-999 | 489, 504, 570, 642, 564, 549, 595 s (median 564 s, 9.4 min) | `cycle_done` timestamps in `events.jsonl` |
| M2 | Full adaptive curve (3 problems x 6 tolerances = 18 points) | 24.8 s, about 1.4 s per point | `python -m rk_harness.prototypes.adaptive` with `RK_WORK_DIR` redirected to scratch |
| M3 | Full SDIRK curve (2 problems x 3 methods x 11-13 step counts) | 1.3 s | same |
| M4 | Prototype output determinism | byte-identical across reruns; the fresh `adaptive_curve.json` matches the committed one byte for byte | `cmp` of two runs, and of the run against `rk-work/prototypes/` |
| M5 | Archive size | 66,555 records, 147 MB, 7 daily JSONL files | `cat rk-work/archive/*.jsonl \| wc -l`, `du -sh` |
| M6 | Test suite | 1,104 collected | `pytest --collect-only -q tests` |
| M7 | Existing inline blocking call in a cycle | literature review runs `codex exec` with `timeout=900` inside `_run_cycle` (`runner.py:590-624`) | source |
| M8 | Graceful stop grace period | `stop.ps1 -WaitMinutes 20`, then force | source |

Three of these drive the design and should be read before section 3.

**M4 is the one that changes the shape of the plan.** The prototype artifacts are pure functions
of the code: rerunning them produces the same bytes. A naive cron ("rerun the prototypes every N
cycles") would therefore burn CPU and produce exactly zero new information and zero git churn.
Automation is only worth building if each firing measures a point that has not been measured yet.
That is design decision D3, and it is what the job catalogue in section 4 is built around.

**M2 and M3 say the work is cheap.** A whole curve costs less than half a minute against a
median cycle of 9.4 minutes (M1). Side-track work at one job per 20 cycles costs well under 1
percent of wall clock.

**M7 and M8 bound the risk.** A cycle already blocks for up to 900 s on a literature call, and
the graceful stop already tolerates that badly: worst observed cycle 642 s plus a 900 s call is
25.7 min, above the 20 min grace, so `stop.ps1` would force-stop on a literature cycle today. A
side-track cap of 300 s is strictly more conservative than what the run already does, and it
keeps 642 + 300 = 15.7 min inside the grace.

## 2. Two tiers, and the line between them

**Tier A, this document: unpinned side-track execution.** The container runs off-archive
experiments on a schedule, appends the results to a ledger in `rk-work`, and publishes them.
Nothing it does can change a score, because nothing it touches is in `VERIFIER_FILES`
(`verifier_hash.py:15-26`). Reversible with one config key. No epoch boundary.

**Tier B, out of scope here: scored epochs.** Adaptive pairs or SDIRK tableaus entering the
archive. That means the tableau type, the simulator, the cost model, the evaluator, the verifier
and the problem fixtures, all of which are pinned, all in one change set with a re-pin. Specified
in `EPOCH2-DESIGN.md` section 9 and `EPOCH3-DESIGN.md`; the gate is restated in section 8 phase 5
so nobody mistakes automation for promotion.

The relationship is one-directional: Tier A produces the measurements that Tier B's designs
currently list as open (the tolerance ladder, the controller gains, the dyadic gamma threshold,
the Newton iteration count). Doing Tier A first makes Tier B a smaller, better-evidenced change.

## 3. Design decisions

**D1. In-container, inside the cycle loop, not on the host watchdog.**
The container carries the pinned environment (numpy 2.1.3, scipy 1.14.1, sympy 1.13.3, mpmath
1.3.0 from the Dockerfile), so a result produced there is reproducible in a way a host-venv result
is not. Its CPU is already bounded by `--cpus`, `--cpu-shares` and `--pids-limit`, and the battery
guard and CPU-pause guards act on the whole container, so side-track work inherits every existing
throttle for free. The heartbeat runs on its own daemon thread (`runner.py:99-108`), so a blocking
job cannot trip `heartbeat_stale_seconds`.
Rejected: running jobs from `scripts/watchdog.ps1` the way the saturation check does
(`watchdog.ps1:119-135`). That path uses the host venv, is invisible to the container's resource
limits, and would compete with the search rather than be scheduled against it.

**D2. Unpinned by construction.** The new module is `rk_harness/sidetrack.py`, which is not in
`VERIFIER_FILES` and must never be added to it. Jobs import pinned modules read-only. Enforced by
canary test, not by convention (I1, I2 in section 9).

**D3. A sweep plan, not a rerun.** Every job declares a deterministic, finite, ordered list of
parameter points. A firing takes points absent from the ledger, measures them until its budget is
spent, and appends each. When the list is exhausted the job logs `sidetrack_exhausted` and does
nothing. This is what turns automation into new information, given M4.

The budget gates *starting* a point, never finishing one, so an artifact is always a complete
document. A firing therefore overruns by at most the length of the point already running, and
every point is bounded by a deterministic work cap rather than a clock (D11). Measured worst point
in the current catalogue: 48 s (`sdirk.stiff_suite:robertson_scaled`), and an end-to-end cycle at a
30 s budget was observed overrunning to 75 s because of it.

Almost all of that cost is first-touch reference-solution computation, which the validation module
caches per process. The runner is long-lived, so it is paid once per container lifetime, not once
per firing: in a three-cycle end-to-end run the firings measured 6 points in 55 s, then 9 in 75 s,
then the remaining **25 in 2.0 s**. Budget the first firing after a restart generously and expect
later ones to be nearly free.

The plan originally called for exactly one point per firing. Filling the budget instead is
strictly better: points cost between 0.01 s and 44 s, so a one-point firing would leave almost
all of a 180 s budget unused and stretch a 40-point plan over five days for no benefit. The
whole plan now runs in about 80 s of CPU, which means the interesting question is not how to
spread it out but what to add next once a track is exhausted.

**D11. Failure is bounded, in both directions.** A failed point is recorded and does *not* count
as measured, so a transient failure is retried. Two caps stop that from becoming a loop: a point
is attempted at most once per firing, and after `MAX_FAILURES_PER_POINT` (3) failures under one
code hash it is set aside and reported in `status()`. Without the first cap a deterministically
broken point starves the firing, retrying until the budget expires; that is not hypothetical,
it is what `test_ST11` caught on the first run of the test suite.

**D12. Provenance by content hash.** Every ledger line carries `code_hash`, a digest over
`sidetrack.py` and the three prototype files, and a point counts as measured only under the hash
that measured it. Editing a prototype therefore re-opens its points instead of leaving stale
numbers standing next to fresh ones. This mirrors `verifier_hash.py` deliberately: the pinned
hash protects scores, this one protects side-track measurements, and they are separate because
the two bodies of code are separate on purpose (D2).

**D4. Append-only ledger plus one artifact per point.**
`/work/sidetrack/ledger.jsonl` gets one line per completed point:
`{ts, cycle, track, job, point_key, params, summary, artifact, duration_s}`.
`/work/sidetrack/<job>/<point_key>.json` holds the full result. This mirrors `hypotheses.jsonl`
and `literature/digests.jsonl`, which the site already renders. Write order is artifact first
(atomically), then the ledger line, so a crash can leave an orphan artifact but never a ledger
line without data.

**D5. The published headline curves stay frozen.** `prototypes/adaptive_curve.json` and
`prototypes/sdirk_curve.json` are cited in both design docs and published on rk-overview. The
sweep never overwrites them. Regenerating them stays a deliberate manual act, so the numbers in
the design docs keep their provenance.

**D6. One job per firing, cooperative deadline, never raises.** The job checks the deadline
between points and abandons cleanly, writing nothing for an incomplete point. The call site wraps
everything in `except Exception` and logs `sidetrack_failed`, exactly as
`_maybe_literature_review` does (`runner.py:608-610`). The STOP killfile is checked before a job
starts.

**D7. Off by default.** `run.sidetrack_every_cycles` ships at 0. With it at 0 the run behaves
exactly as it does today, so the change can be merged, mounted and left dormant. Enabling is one
`configure.py set` plus a container restart.

**D8. Publication is derived, never authored.** A new `sidetrack.html` renders from the ledger and
artifacts only: no clock, no host detail, no model prose. It is gated on file existence the same
way `validation.html` and `benchmark.html` are (`sitegen.py:2120-2160`), must pass `check_banned`,
and must stay byte-deterministic and script-free like every other page.

**D9. Wall-clock benchmarking stays out of the container.** `benchmark.py` measures real time. The
container runs at `--cpu-shares 256` and is paused by the watchdog under host load, so
in-container timings would be noise dressed as data. It remains a host-run, operator-invoked tool
and is explicitly not a side-track job.

**D10. No image rebuild is needed for these changes.** The Dockerfile mounts the harness read-only
at `/harness` rather than copying it in, so a pure-Python change takes effect on the next
container start. `start.ps1 -Build` is only required if the Dockerfile or `entrypoint.sh` changes.
(The `-Build` hint in `start.ps1`'s header comment is over-cautious for this change set; leave the
comment alone, just do not pay for a rebuild.)

## 4. Job catalogue

Every job exists to close a question that one of the two design documents currently leaves open.
Jobs that do not close a named question do not belong here.

### J1 `adaptive.suite_sweep` (track B, adaptive)

Closes: EPOCH2-DESIGN section 5, the scored tolerance ladder is unchosen, and section 10, the
existing curve covers 3 of the 8 validation problems.
Plan: 8 points, one per validation problem (`battery_2rc`, `bicycle_lateral`, `buck_converter`,
`enzyme_qssa`, `glucose_minimal`, `pll_lock`, `robertson_scaled`, `servo_load_step`), each running
the full 6-tolerance ladder 1e-3 to 1e-8 through `adaptive.curve_point`.
Result per point: per-tolerance `n_accepted`, `n_rejected`, `n_fevals`, `achieved_error`.
Estimated cost: about 9 s per point on the smooth problems (M2); unknown but capped on the three
stiff ones, which is itself the interesting measurement.
Value: whether the work-precision shape survives stiffness, and where the rejection rate breaks.

### J2 `adaptive.controller_gains` (track B)

Closes: EPOCH2-DESIGN section 4, where the claim that the dyadic gains alpha = 1/4, beta = 1/8,
safety = 7/8 "converge cleanly" rests on one measurement, and the classical alpha for a 3(2) pair
is 1/3.
Plan: 16 points over alpha in {1/8, 1/4, 3/8, 1/2} and beta in {0, 1/16, 1/8, 1/4}, each across
three reference problems at a fixed tolerance.
Requires a small unpinned change: `ALPHA`, `BETA`, `SAFETY` are module constants
(`prototypes/adaptive.py:163-165`) and `solve_adaptive` (line 193) does not take them as
arguments. Thread them through as keyword arguments defaulting to the current constants, so every
existing test and the frozen curve are unaffected.
Value: picks the gains on evidence before they are frozen into the Q15 controller table.

### J3 `sdirk.stiff_suite` (track C, implicit)

Closes: EPOCH3-DESIGN, "the stiff scored suite", where the case currently rests on `rc_thermal`
plus a synthetic two-rate system, while the harness now has three purpose-built stiff validation
problems (`servo_load_step` ratio 546, `enzyme_qssa` 1030, `robertson_scaled` 292) on which the
explicit elites are known to overflow.
Plan: 3 points, one per stiff problem, each running the step-count ladder for
{euler, heun2, midpoint, rk4, sdirk2} in float with the analytic or FD Jacobian.
Value: the epoch-3 argument made on the harness's own stiff problems instead of a synthetic one.
This is the highest-value job in the catalogue.

### J4 `sdirk.gamma_dyadic_scan` (track C)

Closes: EPOCH3-DESIGN, "L-stability is then checked numerically for the snapped tableau rather
than assumed", and the `NOT_L_STABLE` threshold currently stated as "on the order of 0.05", which
is a guess.
Plan: 9 points, one per dyadic denominator exponent s in 4..12; each evaluates every dyadic
gamma = m / 2^s within a window around 1 - sqrt(2)/2, computing exactly over Fractions the
stability function R(z), |R(inf)|, an A-stability check, and the measured order on dahlquist.
Cost: pure algebra, sub-second per point.
Value: converts a guessed verifier threshold into a measured one before it is pinned.

### J5 `sdirk.newton_iters` (track C)

Closes: EPOCH3-DESIGN, "Three iterations is the prototype's setting, not a final ruling ... the
convergence study will show directly whether 2, 3, or 4 iterations preserve the claimed order."
Plan: 4 points, one per iteration count in {1, 2, 3, 4}; each measures order on dahlquist plus
error and estimated cycles per step on the stiff problems.
Value: the iteration count becomes part of the pinned evaluator config at the epoch boundary, so
it should be chosen on data.

### J6 `adaptive.q15_estimate_floor` (track B, phase 3, not a sweep)

Closes: EPOCH2-DESIGN section 10's stated next step, where the Q15 error estimate's roughly 2 LSB
bias floor flattens the work-precision curve, which is the last open input to the tolerance
ladder.
This one needs new prototype code (a Q15 realization of the estimate and the table-driven
controller), not a parameter sweep. It is listed so the catalogue is complete, and it is gated
behind phase 3. Do not start it as part of the configuration change.

Ordering across tracks is a two-letter rotation `"BC"`, so consecutive firings alternate adaptive
and implicit. Within a track, jobs run in catalogue order and each job runs to exhaustion before
the next begins. Total plan size for J1-J5 is 8 + 16 + 3 + 9 + 4 = 40 points, which at one firing
per 20 cycles (about 3.1 hours at M1's median) is roughly five days of unattended work.

## 5. Change set

| # | File | Change | Done |
| --- | --- | --- | --- |
| C1 | `rk_harness/sidetrack.py` (new) | Job registry, deterministic plans, ledger IO, code hash, `next_point` / `run_point` / `run_until` / `status`, and a `--status` / `--plan` / `--run-one` / `--run-until` CLI in the style of `saturation.py:202-212` | yes |
| C2 | `rk_harness/prototypes/adaptive.py` | `alpha`, `beta`, `safety` threaded through `solve_adaptive` as keyword arguments defaulting to the existing constants; new `sweep_point` records failure as a status, and `curve_point` is now a narrowing of it that keeps the frozen artifact's exact six keys | yes |
| C3 | `rk_harness/prototypes/sdirk.py` | `Sdirk2Spec` (gamma, a21, b, c, newton_iters) threaded through `sdirk2_step` and `solve_sdirk2`, defaulting to Alexander's method; `diverge_at` bound; exact `order2_tableau_exact` / `spec_from_exact` / `dyadic_neighbours` over Fractions | yes |
| C4 | `rk_harness/runner.py` | `_sidetrack_every()`, `_sidetrack_max_seconds()`, `_sidetrack_tracks()`, `_maybe_sidetrack()`; the hook is step 5b, between hypothesis resolution and the site build; `_commit_outputs` reworked (below) | yes |
| C5 | `rk_harness/sitegen.py` | `_load_sidetrack()`, `render_sidetrack()`, `_ST_KEY_ORDER`, `_st_cell()`, `_HAS_SIDETRACK` flag, and the tier-1 nav entry `("sidetrack.html", "side tracks", 1)`, all mirroring the validation/benchmark pattern | yes |
| C6 | `configure.py` | Three `SCHEMA` rows (section 6), restart class `container` | yes |
| C7 | `start.ps1` | Reads the three keys, passes them to `scripts/run.ps1` | yes |
| C8 | `scripts/run.ps1` | Three parameters and three `-e RK_SIDETRACK_*` flags in the `$envFlags` block, plus a startup line reporting the setting | yes |
| C9 | `tests/test_t13_sidetrack.py` (new) | 22 tests, `test_ST1_` to `test_ST21_`, including the invariant canaries (ST is used because T9 already owns `test_S*`) | yes |
| C13 | `tests/test_t4_ledger_runner_site.py` | Three `test_B68_` tests for the page: nav gating, build determinism with the flag resetting, and the page reporting every ledger point | yes |
| C10 | `docs/ROADMAP.md` | Records the ruling and links this document | yes |
| C11 | `rk_harness/watch.py` | One row in the live view: points measured of planned, code hash, last job | yes |
| C12 | `tests/test_t5_config_watch.py` | `test_C14`'s read-only assertion now ignores `HEARTBEAT` (see below) | yes |

Nothing in `VERIFIER_FILES` appears in this table, which is the point. The verifier hash is
identical before and after the entire change set (A1.2, verified).

**Hook placement.** The plan first put the call next to `_maybe_literature_review` and
`_maybe_interpret`, before candidate generation. It is instead step 5b, after hypothesis
resolution and before the site build. That spot is better on both sides: the cycle's scored work
is already verified and appended, so a side-track job cannot cost it anything, and the site build
and commit that follow still publish whatever the job produced in the same cycle rather than a
cycle later.

**The commit gap, now closed.** `_commit_outputs` used to stage completed archive JSONL files and
nothing else. Since `Push-Repo` in the watchdog pushes commits but never makes them,
`hypotheses.jsonl`, `LAST_DIRECTIVE.json`, `literature/digests.jsonl` and
`interpretation/interpretations.jsonl` were written by the container and then sat dirty in the
`rk-work` checkout until a human committed them, indefinitely. The findings site carried their
content, so this was a durability gap rather than a publication one, but it meant the run's own
record of its reasoning lived on one machine's working tree. `_WORK_EXTRA_PATHS` now stages those
four plus `falsification.json`, `EPOCH_STATUS.json`, `prototypes/`, `validation/`, `benchmark/`
and `sidetrack/` on every cycle. Today's archive file is still deliberately excluded, for the
reason it always was: it is tens of MB and grows every cycle, so it is committed once the day
rolls over. Staging a path that is absent or ignored costs nothing, and a commit with nothing
staged fails harmlessly and creates no empty commit, so the churn is one commit per cycle on which
something actually changed. `test_ST20` and `test_ST21` hold both halves of that.

**A pre-existing flake found on the way (C12).** `test_C14_watch_renders_and_is_clean` asserts that
rendering the live view writes nothing into the work dir. `runner.heartbeat()`, exercised by T4,
starts a daemon thread that writes `HEARTBEAT` every 10 s into whatever `RK_WORK_DIR` points at
*when the timer fires*, and that thread outlives the test file. T4's own docstring notes the hazard
and mitigates it by making the heartbeat test last in its file, but the thread keeps running into
every later file, so T5's assertion passes or fails depending on where the 10 s timer happens to
land. Reproduced directly: start `heartbeat()`, repoint `RK_WORK_DIR`, wait 11 s, and `HEARTBEAT`
appears in the new directory. The assertion now ignores that one filename, which is unrelated to
what `render_once()` writes. Nothing about the harness itself changed; this is a test-only fix to a
failure mode that predates this work.

## 6. Configuration surface

Three new keys, all in the `run` section, all restart class `container`.

| Key | Type | Range | Default | Meaning |
| --- | --- | --- | --- | --- |
| `run.sidetrack_every_cycles` | int | 0..100000 | **0 (off)** | Run one side-track job every N cycles. 0 disables the feature entirely. |
| `run.sidetrack_max_seconds` | int | 30..600 | 180 | Budget for one firing. It gates starting a point, so a firing can overrun by the length of the point already running (D3). Out-of-range and unparseable values are clamped in the runner, not trusted from the environment. |
| `run.sidetrack_tracks` | str | `both`, `adaptive`, `implicit`, `off` | `both` | Which side tracks are eligible. `off` is equivalent to the cadence being 0, and exists so a track can be silenced without losing the cadence setting. |

Environment variables, following the existing naming: `RK_SIDETRACK_EVERY`,
`RK_SIDETRACK_MAX_SECONDS`, `RK_SIDETRACK_TRACKS`.

Recommended values when enabling: `sidetrack_every_cycles=20`, `sidetrack_max_seconds=180`,
`sidetrack_tracks=both`. At M1's median that is one firing every 3.1 hours and under 2 percent of
wall clock. Since the whole 40-point plan measures in about 80 s of CPU (section 6a), a single
firing at that budget very nearly clears it; the cadence matters mainly for what comes after the
plan is extended.

**Upper-bound rule for `sidetrack_max_seconds`.** A graceful stop waits 20 minutes
(`stop.ps1 -WaitMinutes 20`) and the STOP killfile is only read at a cycle boundary, so the worst
case is one full cycle, plus the budget, plus the longest point. With M1's worst observed cycle of
642 s, a 180 s budget and the measured 48 s worst point, that is 14.5 min and stays inside the
grace. A 600 s budget would give 21.5 min and be force-stopped, which is why the runner clamps the
value rather than trusting it. Re-derive this if cycle time grows. (A literature cycle already
exceeds the grace today, per M7. That is pre-existing and is not made worse by this change.)

### 6a. What the first full run measured

The catalogue was run end to end once on the host, into a scratch work directory, on 2026-09-04.
All 40 points completed with status `ok` in **77.9 s** of wall clock, and re-measuring every point
reproduced all 40 artifacts byte for byte. Costs are dominated by reference-solution computation
on first touch, not by integration: `robertson_scaled` alone accounts for 44 s of the 78, and it
is cheap on every subsequent point in the same process.

Three results are worth reading before the site page exists, because they answer questions the
design documents left open:

* **The explicit wall is total on the stiff suite.** On all three stiff validation problems
  (`servo_load_step`, `enzyme_qssa`, `robertson_scaled`), euler, heun2, midpoint and rk4 diverge at
  every step count in the ladder up to 256. SDIRK2 finishes all three: from n=8 on servo
  (error 8.6e-7), n=8 on enzyme (6.9e-7), and n=256 on robertson (1.0e-5). Epoch 1 established
  that no discovered explicit method finishes robertson; this establishes that an implicit one
  does. That is the epoch-3 case, made on the harness's own problems rather than on a synthetic
  two-rate system.
* **The dyadic-gamma threshold has a number now.** EPOCH3-DESIGN puts the `NOT_L_STABLE` gate "on
  the order of 0.05" as a placeholder. Measured |R(inf)| for the best A-stable dyadic gamma at each
  denominator exponent: 0.28 at s=4, 0.21 at s=5, 0.064 at s=6 and s=7, and 0.00124 at s=8 and
  finer, all at measured order 2.007. So a 0.05 gate implies s >= 8, and s=8 clears it by a factor
  of 40. Every candidate scanned is A-stable and none is L-stable, which is the expected cost of
  the snap.
* **Three of the sixteen controller-gain settings are unusable.** alpha=1/8 with beta=1/8 or 1/4,
  and alpha=1/4 with beta=1/4, drive the step size into underflow on all three reference problems.
  Among the settings that work, rejection rates by alpha are: 0.80 to 1.02 percent at alpha=3/8,
  1.39 to 1.66 percent at alpha=1/4, 1.79 to 2.53 percent at alpha=1/8, and 1.27 to 15.75 percent
  at alpha=1/2, where beta=1/4 is a sharp outlier. So alpha=3/8 is the best of the four and
  alpha=1/2 is the least predictable, while the currently proposed alpha=1/4, beta=1/8 sits
  mid-field. EPOCH2-DESIGN section 4 should be revisited against the full artifact before those
  gains are frozen into the Q15 table.

The construction J4 uses needs stating, because it is a ruling the design document does not make.
Stiff accuracy (b equal to the last row of A) and order 2 can only hold together at the irrational
gamma = 1 - sqrt(2)/2. With a dyadic gamma one of them has to be given up. The scan keeps order:
a21 is fixed at 1 - gamma so that c2 = 1, and b is solved exactly from the two order conditions.
At the exact gamma this reproduces Alexander's tableau, stiff accuracy included, so it is a
generalization rather than a substitution. A consequence worth carrying into epoch 3: because the
snapped tableau is no longer stiffly accurate, R(inf) stops being identically zero, which is what
makes an L-stability *margin* a meaningful thing to gate on at all.

New event kinds in `events.jsonl`: `sidetrack_started`, `sidetrack_done`, `sidetrack_failed`,
`sidetrack_exhausted`, `sidetrack_skipped` (STOP present, or track disabled), `sidetrack_timeout`.

## 7. Phases

| Phase | Content | Risk | State |
| --- | --- | --- | --- |
| P0 | Baseline capture, suite green | none | done |
| P1 | C1, C2, C3, C9: module, prototype parameterization, tests. Nothing wired. | none | done |
| P2 | C4, C6, C7, C8, C11, C12: wiring and config, default off | low | code done, restart pending |
| P3 | Enable at `every=20`. Observe 24 hours. | low | not started |
| P4 | C5, C10, C13: publish `sidetrack.html`, record the ruling | low | done |
| P5 | Tier B gate, not part of this change set | n/a | n/a |

P1 was done entirely on the host against a scratch `RK_WORK_DIR` with the container untouched,
since none of it was imported by the runner yet.

The container mounts `rk-harness` read-only rather than copying it into the image (D10), so the new
code is picked up on the next container start and needs no rebuild. Until that restart the running
process keeps the code it loaded, so nothing changes mid-flight. The startup gate was run against
the new tree (`entrypoint.sh`'s G1-G20 and K1-K2 subset, 55 tests, green) and the verifier hash is
unchanged, so a restart, whether deliberate or from the watchdog, is safe. With the feature at its
default of 0 the restarted container behaves exactly as the current one does.

## 8. Acceptance criteria

Each criterion is a statement, a command, and a pass condition. A phase is not done until every
one of its criteria passes. Commands are given from the workspace root
(`D:\Programming-Projects\Integration-Harness`) with the host venv
(`rk-harness\.venv\Scripts\python.exe`).

### P0, baseline

**A0.1** The baseline is recorded before anything changes: verifier hash, archive record count,
cycle id, test count.
`python -m rk_harness.verifier_hash` ; `cat rk-work/archive/*.jsonl | wc -l` ;
`cat rk-work/RUNSTATE.json` ; `pytest --collect-only -q tests`
Pass: all four values written into the phase log. Reference values as of 2026-09-04: 66,555
records, cycle 999, 1,104 tests.

**A0.2** The suite is green before any edit.
`pytest -q tests`
Pass: 1,104 passed, 0 failed.

**A0.3** Both prototypes reproduce their committed artifacts byte for byte.
Run each with `RK_WORK_DIR` pointed at a scratch directory, then `cmp` against
`rk-work/prototypes/`.
Pass: `cmp` reports no difference for `adaptive_curve.json`. (Verified 2026-09-04, M4. Re-run
after C2 and C3 to prove the parameterization changed no default behavior.)

### P1, module and tests, unwired

**A1.1** The side-track module is not pinned.
`python -c "from rk_harness import verifier_hash as v; assert 'rk_harness/sidetrack.py' not in v.VERIFIER_FILES"`
Pass: exits 0.

**A1.2** The verifier hash is unchanged from A0.1 after the entire change set.
`python -m rk_harness.verifier_hash`
Pass: identical string. This is the most important criterion in the document; if it fails, the
change set has touched a pinned file and the archive is at risk.

**A1.3** [PASSED] Every job plan is finite, deterministic and duplicate-free (`test_ST1_`).
Pass: two calls to `plan()` return equal lists; no repeated point key; total across J1-J5 is 40
points; every job names the design-doc question it closes.

**A1.4** [PASSED] A point measured twice writes byte-identical artifacts (`test_ST2_`), and the
whole catalogue does (verified by re-measuring all 40 and diffing the tree).
Pass: `cmp` equal. This is what will make the published page reproducible.

**A1.5** [PASSED] The budget gates starting a point, not finishing one (`test_ST4_`).
Pass: a zero budget measures exactly one point and then stops, so a firing always makes progress
and never abandons a half-written artifact. The worst-case overrun is one point (measured: 44 s),
which section 6's upper-bound rule accounts for.

**A1.6** [PASSED] An exception inside a job never propagates, and a broken point cannot starve a
firing (`test_ST11_`, `test_ST11b_`).
Pass: a job registered to raise produces a failure ledger line, no artifact, exactly one
`sidetrack_failed` event and exactly one attempt in that firing; after
`MAX_FAILURES_PER_POINT` failures the point is set aside and reported in `status()`.

**A1.7** [PASSED] Canary: running every job to exhaustion writes nothing pinned or scored
(`test_ST13_`).
Pass: in a temp `RK_WORK_DIR`, `compute_verifier_hash()` is unchanged and nothing exists under
`archive/`, `quarantine/`, `hypotheses.jsonl`, `falsification.json`, `RUNSTATE.json` or
`EPOCH_STATUS.json`; the only thing written is `sidetrack/`.

**A1.8** [PASSED] The suite grows and stays green.
`pytest -q tests`
Pass: 1,104 + 22 in T13 + 3 in T4 = 1,129 collected as of 2026-09-04, 0 failed. One pre-existing
environmental skip, `test_t10_benchmark.py:110`, which skips when `RK_WORK_DIR` is unset at import
time.

Verified in two passes, because this change landed while a concurrent session was reworking the
site renderer in the same tree. First, five separate runs covering all thirteen files
(t1+t6+t7+t9, t3, t8+t10, t2, then t4+t5+t11+t12+t13), every one green; that is weaker than one
process on cross-file ordering effects, which is the class the `HEARTBEAT` flake in section 5
belongs to. Then a single full run over the combined tree, both workstreams together, reporting
1,129 passed. Collection on the combined tree independently confirms 13 files and 1,129 tests.

This criterion should not be met by hand again. Once `.github/workflows/ci.yml` is pushed, a
single-process run over the whole suite happens on every push, sharded, without costing the
research a pause. That is the argument in `docs/CI.md`, and this criterion is the case in point:
it took two sessions and six local runs to establish something CI would have reported in ten
minutes.

From now on this criterion is CI's, not the operator's: `.github/workflows/ci.yml` runs the whole
suite sharded five ways on every push, along with the gate, the image build and a determinism job
that re-derives all 40 side-track artifacts and diffs them (A1.4 at catalogue scale). Running the
suite locally loads the host enough that the watchdog pauses the container, which is why it moved.
See `docs/CI.md` and `harness-platform.md` P13.

**A1.9** [PASSED] The catalogue's hardcoded problem names still match the suite they name
(`test_ST3_`). The names are hardcoded so that `plan()` stays cheap and importing `sidetrack` does
not drag in the validation module; this is the guard against them drifting.

### P2, wiring, still default off

**A2.1** With the feature off, a cycle is inert (`test_S6_` plus one live cycle).
`docker logs rk --since 20m | grep sidetrack` after one cycle with `RK_SIDETRACK_EVERY` unset.
Pass: no `sidetrack_*` events in `events.jsonl`, and `/work/sidetrack` does not exist.

**A2.2** The config surface is complete and round-trips.
`python configure.py explain | grep sidetrack` ; `python configure.py set run.sidetrack_every_cycles=20` ;
`python configure.py show | grep sidetrack` ; `python configure.py reset run.sidetrack_every_cycles`
Pass: three keys documented with ranges, defaults and the container restart hint; set and reset
both round-trip; an out-of-range value is refused with a message.

**A2.3** The values reach the container.
`docker inspect rk --format '{{json .Config.Env}}'`
Pass: contains `RK_SIDETRACK_EVERY`, `RK_SIDETRACK_MAX_SECONDS`, `RK_SIDETRACK_TRACKS` with the
configured values.

**A2.4** The disabled path costs nothing measurable.
Compare five `cycle_done` intervals after the restart against M1.
Pass: median inside the 489-642 s band.

**A2.5** Restart hygiene: the container comes up clean.
`docker logs rk | head -40`
Pass: `verifier hash ok:` printed, golden and canary tests pass, runner starts, no K4 mount
warning.

### P3, enabled

Observation window: 24 hours at `every=20`, which is about 7 firings at M1's median.

**A3.1** A firing happens on exactly the eligible cycles, and only there.
Count `sidetrack_firing` events against cycles where `cycle_id % 20 == 0`.
Pass: equal, and no firing on any other cycle. (`test_ST16_` holds this at unit level.)

**A3.2** Ledger and artifacts stay in step.
`wc -l < rk-work/sidetrack/ledger.jsonl` against `find rk-work/sidetrack -name '*.json' | wc -l`
Pass: one artifact per `ok` ledger line, and no orphan ledger line. Failed lines have no artifact
by design. (An orphan artifact is allowed by D4 and must be explainable by a crash.)

**A3.3** Track rotation holds.
Count `track` values in the ledger.
Pass: adaptive and implicit counts differ by at most 1 while both tracks have points left.
(`test_ST5_` holds the alternation directly.)

**A3.4** No cycle is abandoned because of a job.
`grep cycle_abandoned rk-work/events.jsonl | tail`
Pass: no new `cycle_abandoned` events in the window.

**A3.5** Wall-clock impact is under 5 percent.
Median `cycle_done` interval over the window against M1's 564 s.
Pass: median at most 592 s. A firing cycle may of course be longer; the median over all cycles is
what is bounded.

**A3.6** The heartbeat never goes stale.
Watchdog window output for the period.
Pass: no `docker kill` and no heartbeat-stale message. (Expected by construction, since the
heartbeat is a separate thread, but it is the failure that would hurt most.)

**A3.7** Results are committed and pushed.
`git -C rk-work log --name-only -5` ; `git -C rk-work status --short`
Pass: `sidetrack/` paths appear in commits within one cycle of each firing, and no untracked files
remain under `sidetrack/`. A push follows within `watchdog.push_minutes`.

**A3.8** Exhaustion is graceful.
Force a track to exhaustion in a scratch run.
Pass: `sidetrack_exhausted` is logged, no artifact is written, the cycle completes normally, and
the event does not repeat more than once per firing attempt.

**A3.9** A graceful stop still finishes inside the grace period.
`.\stop.ps1`, timed, ideally on a firing cycle.
Pass: "container stopped at a cycle boundary", not "still running after 20 min; forcing".

**A3.10** The measurements are actually new.
Read the ledger.
Pass: every line's `point_key` is distinct, and at least one line reports a result that is not
already in `prototypes/adaptive_curve.json` or `prototypes/sdirk_curve.json`. This is the
criterion that says the automation earned its keep rather than recomputing M4.

### P4, publication

**A4.1** [PASSED] The page appears only when there is data, and the nav matches the pages
written (`test_B68_sidetrack_nav_entry_present_only_with_a_ledger`).
Pass: with no ledger, no `sidetrack.html` and no nav entry anywhere; with a ledger, the page
exists, sits in nav tier 1 beside validation and benchmark, and the whole ordered href list
matches on every page checked.

**A4.2** [PASSED] The page is byte-deterministic
(`test_B68_sidetrack_build_is_deterministic_and_flag_resets`, plus two builds under
`PYTHONHASHSEED=1` and `PYTHONHASHSEED=99999`).
Pass: identical bytes, and `_HAS_SIDETRACK` does not leak out of `build()`. Every container the
renderer walks is sorted explicitly, which is the trap the peer session hit independently while
grouping hypotheses.

**A4.3** [PASSED] The page passes the banned-word guard.
`sitegen.build` calls `check_banned` on every page before writing any of them.
Pass: no `BannedWordError` across a 27-page build, and no `site_build_failed` event.

**A4.4** [PASSED] Every number on the page traces to a ledger line
(`test_B68_sidetrack_page_reports_every_point_and_stays_static`).
Pass: each job and point key in the ledger appears on the page, and the counts are computed from
the ledger rather than hardcoded. Columns are the sorted union of the summary keys the jobs
actually emitted, so a job that changes its summary changes the table rather than silently
dropping a value.

**A4.5** The page reaches the live site.
`curl -sS -o /dev/null -w '%{http_code}' https://jgoetzmann.github.io/rk-findings/sidetrack.html`
Pass: 200 within one `watchdog.push_minutes` interval plus GitHub Pages build time.

**A4.6** [PASSED] The no-JavaScript rule is preserved.
`grep -c '<script' rk-findings/docs/sidetrack.html`
Pass: 0, asserted in `test_B68_sidetrack_page_reports_every_point_and_stays_static`.
Interactivity stays `<details>` and anchors, as on every other page. The page also carries no em
dash, which keeps the findings site clean of them.

**A4.7** The ruling is written down.
Pass: `docs/ROADMAP.md` records that the container carries the side tracks, at what cadence, and
links this document; the project memory note is updated in the same pass.

### P5, the Tier B gate (stated, not executed here)

A track moves from side to scored only when all of the following hold. None of them is delivered
by this document.

1. Epoch 1 is frozen by the owner (`watchdog.auto_freeze` is false by the 2026-09-03 ruling, so
   this is a deliberate act), and `EPOCH_STATUS.json` is written and pushed.
2. The epoch-1 paper-facing analysis is published: validation, benchmarks, trade-offs matrix.
3. The pinned change set for the target epoch lands in one commit: `EPOCH2-DESIGN.md` section 9
   items 3-6, or `EPOCH3-DESIGN.md`'s "Verifier, golden tests, and migration" list.
4. New golden fixtures exist and the whole suite plus the new goldens pass before the new run
   starts.
5. `VERIFIER_HASH` is re-pinned and the epoch-1 archive files are left untouched on disk.
6. The side-track evidence this document produces is cited in the design's open slots: the
   tolerance ladder (J1, J6), the controller gains (J2), the stiff case (J3), the L-stability
   threshold (J4), the Newton iteration count (J5).

## 9. Invariants and the canaries that enforce them

| # | Invariant | Enforced by |
| --- | --- | --- |
| I1 | No side-track file is ever in `VERIFIER_FILES` | A1.1, `test_S5_` |
| I2 | Side-track code never writes under `archive/`, `quarantine/`, `hypotheses.jsonl`, `falsification.json`, `RUNSTATE.json`, `EPOCH_STATUS.json` | `test_S5_` |
| I3 | A side-track failure never fails a cycle | `test_S4_`, A3.4 |
| I4 | STOP is honored: no job starts once the killfile exists; a running job delays a stop by at most `sidetrack_max_seconds` | `test_S10_`, A3.9 |
| I5 | Artifacts are pure functions of code and parameters: no clock, no host detail, no unseeded RNG | A1.4, A4.2 |
| I6 | The findings site stays byte-deterministic, banned-word clean and script-free | A4.2, A4.3, A4.6 |
| I7 | With the feature off, the run is behaviorally identical to today | A2.1, A2.4 |
| I8 | At most one job per cycle | A3.1, `test_S11_` |
| I9 | The headline prototype curves are never overwritten by automation | `test_ST13_` (jobs write only under `sidetrack/`); both curves re-verified byte-identical after C2 and C3 |
| I10 | A failing point cannot starve a firing, and cannot be retried forever | `test_ST11_`, `test_ST11b_` |
| I11 | A measurement is only valid under the code that produced it | `test_ST9_` |

## 10. Risks and rollback

| # | Risk | Likelihood | Mitigation | Detected by |
| --- | --- | --- | --- | --- |
| R1 | CPU contention slows the search | medium | 300 s cap, 1-in-20 cadence, container CPU limits already bind | A3.5 |
| R2 | A single point runs away inside a solve and ignores the deadline | medium | every sweep point carries a hard step-count or attempt cap in addition to the deadline; the deadline is checked between points | A3.9, `test_S3_` |
| R3 | Git growth in `rk-work` | low | one small JSON plus one ledger line per firing, roughly 3 KB per day against a 147 MB archive (M5) | A3.2 |
| R4 | Scope creep into pinned files | low | I1, I2, and A1.2 as a hard gate | A1.2 |
| R5 | A stiff problem makes the adaptive pair thrash and the point never completes | medium | this is a real result, not a failure: record it as a capped point with the attempt count, and do not retry | A3.10 |
| R6 | The side-track page drifts from the ledger | low | rendered only from on-disk artifacts | A4.4 |

Rollback, in increasing order of severity:

1. `python configure.py set run.sidetrack_every_cycles=0 --apply`. The feature stops; every
   artifact already written stays valid and published.
2. Move the ledger aside; the page disappears on the next site build.
3. Revert C4 (the runner hook) and restart. The module stays on disk, unreferenced.
4. Full revert of the change set. A1.2 guarantees this needs no re-pin and no archive action,
   because nothing pinned was touched.

## 11. What is left, and what the owner still decides

Outstanding work, in order:

1. **Enable it.** `python configure.py set run.sidetrack_every_cycles=20 --apply`. Nothing runs
   until this happens; every phase-3 criterion is waiting on it, and so is the page, which stays
   absent until a ledger exists.
2. **Extend the catalogue.** The 40 points measure in about 60 s once the references are warm, so
   the plan exhausts within a firing or two of being enabled. J6 (the Q15 estimate floor) is the
   obvious next job and the last open input to the epoch-2 tolerance ladder, but it needs
   hand-written prototype code rather than a sweep.

Decisions that are the owner's:

1. **Cadence.** `every=20` is the recommendation, but see the exhaustion point above: the binding
   constraint is catalogue size, not cadence.
2. **Whether the gains ruling changes.** J2 says alpha=3/8 gives lower rejection rates than the
   alpha=1/4 that EPOCH2-DESIGN section 4 proposes freezing. Revisiting that is a design decision,
   not a measurement one.
3. **J6 timing.** Schedule it as a side-track job, or hold it for the epoch-2 change set itself?
4. **Publication surface.** `sitegen` builds the findings site every cycle, so `sidetrack.html`
   will update itself once C5 lands. The rk-overview `tracks.html` page is regenerated by hand
   (`rk-overview/tools/generate.py`) and is currently stale. Should that regeneration become part
   of the same routine, or stay a deliberate act?
