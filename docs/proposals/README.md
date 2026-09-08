# Proposals

One file per proposal. Each says what question it answers, sketches it
concretely enough to argue with, estimates cost, names prerequisites and risks, and states
whether it needs an epoch boundary. Nothing here is scheduled; the plan that produced them is
`../UNBLOCK-2026-09-07.md` and the rulings they follow from are in `../DECISIONS.md`.

Written 2026-09-07/08.

## Research

| # | Proposal | What it answers |
| --- | --- | --- |
| P1 | [A second side-track catalogue: J7 to J11](P01-a-second-side-track-catalogue-j7.md) | Five new side-track jobs plus a refill rule, so the container has research to do after the current 40 points are measured. |
| P2 | [J6: the Q15 error estimate, the controller table, and the Q31 time register](P02-j6-the-q15-error-estimate-the.md) | Build the Q15 realization of the embedded estimate and the shift-table controller off-archive, and measure the bias floor that sets epoch 2's tolerance ladder. |
| P3 | [Second-pass metrics on the epoch-1 archive: work-precision and the stability frontier](P03-second-pass-metrics-on-the-epoch.md) | Recompute cycles-to-tolerance and the stability/error frontier over the roughly 100,000 records already on disk, to test the premises of both future epochs before paying for either boundary. |
| P4 | [A stiff suite that actually stops explicit methods](P04-a-stiff-suite-that-actually-stops.md) | Screen candidate stiff problems for Q15 admissibility and for whether any explicit method finishes them at the budget, because the current three do not make the case epoch 3 rests on. |
| P5 | [Should the search revisit cells](P05-should-the-search-revisit-cells.md) | Turn the exploration policy into something measured: a reachability model, a revisit rule, warm starts, and an in-run comparison of the three. |
| P6 | [The trade-offs matrix and the cost claim under it](P06-the-trade-offs-matrix-and-the.md) | Fill the matrix's missing columns, and check the analytic cycle table once against a real Cortex-M0+ or a cycle-accurate simulator, because nothing has ever checked it against anything but itself. |

## Platform and engineering

| # | Proposal | What it answers |
| --- | --- | --- |
| P7 | [Alert on the rate of work, not on signs of life](P07-alert-on-the-rate-of-work.md) | Add a progress-rate signal, a gate-closed counter and a durable watchdog log, so a run that is fast, green and producing nothing announces itself. |
| P8 | [A stale stats.txt says so on its own first line](P08-a-stale-stats-txt-says-so.md) | Give the one-shot status snapshot an expiry, a way to ask its age, and a writer that start.ps1 launches and stop.ps1 stops. |
| P9 | [One viewer, and a refresh that does not read 232 MB](P09-one-viewer-and-a-refresh-that.md) | Retire dashboard.py after porting its one unique panel, and stop the watcher from replaying the whole archive twice on every refresh. |
| P10 | [Acceptance evidence that CI keeps current and the host only tops up](P10-acceptance-evidence-that-ci-keeps-current.md) | Feed preflight from CI's junit artifact instead of a local suite run, stamp every row with its provenance, and add a hygiene job for the four house rules nothing checks. |
| P11 | [The archive at 232 MB, and what a checkpoint has to promise](P11-the-archive-at-232-mb-and.md) | Write a daily checkpoint over the closed archive files so a replay becomes checkpoint plus fold, and write down the rulings that keep the archive evidence rather than a database. |
| P12 | [What has to be true before a second harness starts](P12-what-has-to-be-true-before.md) | Bound the docker stats call, subtract peer containers rather than only self, share one sample between watchdogs, and put a container census in the status file, because a third-party stack is already perturbing the run. |
