# P3: second-pass metrics on the epoch-1 archive

Status: proposal. Read-only analysis on the host. Nothing here touches a verifier-pinned file.

## The question this answers

Do the two changes epochs 2 and 3 are built on survive contact with the data already on disk?

The archive holds roughly 100,000 accepted records, each carrying a full `ScoreVector`
(`types.py:53-65`): measured order, error constant, real and imaginary stability extents, cycles
under three cost models, CSD weight, coefficient quantization error, search and held-out error,
overflow margin, and per-problem errors under every model. The published findings read five
things out of it (`key_findings.json`: efficiency, floor bias flip, crossover, rc_thermal
collapse, phase-0 exhaustive). Two of the columns nobody has read answer the two open epoch
questions.

## Series (a): work-precision, measured now

`EPOCH2-DESIGN.md:12-19` changes the scored metric from fixed-budget error to cycles consumed to
reach a target tolerance, and says that metric change is "exactly why it needs an epoch
boundary". Fair. But the metric is not exclusive to adaptive methods. For a fixed-step method,
cycles-to-tolerance is well defined without any controller at all: bisect on the step count `n`,
run `solve_q15`, take the smallest `n` whose error is at or under the target, and report
`n * cycles_per_step`.

So the question "does the metric change the ranking" is answerable today, on epoch-1 methods,
with epoch-1 arithmetic.

Run it for the archive's elites and the eight classical anchors, on the three search problems and
the four held-out problems, at a power-of-two ladder in the scaled norm. Report:

* the rank correlation between fixed-budget held-out error and cycles-to-tolerance at each rung;
* the methods whose rank moves most between the two readings;
* the rungs where a method never reaches the target at any step count, which is where the Q15
  floor bites and is the fixed-step analogue of the flattening P2 measures for the adaptive case.

This is also the coexistence answer the roadmap needs. Fixed-budget error and cycles-to-tolerance
are two readings of the same run, so an epoch-2 archive can carry both, and
`EPOCH2-DESIGN.md:118-120` already keeps fixed-budget error as a secondary column. What the
analysis decides is whether that secondary column carries any information the primary one does
not.

If the two metrics rank methods nearly identically, the boundary buys less than the design
assumes, and the case for embedded pairs rests on adaptivity itself rather than on the metric. If
they disagree, the disagreement is a paper result and the strongest available argument for
making the change.

## Series (b): the stability frontier

`EPOCH3-DESIGN.md:11-23` argues the stability tax from a two-rate linear system and from
`rc_thermal`. Meanwhile `evaluator.stability_polynomial` and `evaluator.stability_extents`
(`evaluator.py:115-140`) compute the region exactly for every candidate, and both extents are
stored on every record. Nobody has plotted them against error.

The run's own literature rotation supplies the hypothesis. From
`rk-work/literature/digests.jsonl`, cycle 1248, ts `2026-09-06T09:48:21Z`: "Order p fixes the
stability-polynomial coefficients through degree p. Four-stage order four has no free
stability-polynomial coefficient; four-stage order three has one."

So: for each `(order, stages)` with `stages > order`, take the Pareto set over
`(stability_real, heldout_error)` from the archived records. Order 4 at 4 stages should collapse
to a point. Order 3 at 4 stages should be a curve. Order 3 at 5 and 6 stages should be a wider
curve. If the archive reproduces that shape, it has independently confirmed a textbook fact from
100,000 measurements, which is a methodology check worth printing next to the results. If it does
not, either `stability_extents` or the search has a problem, and finding that before epoch 3
pins a stability-region check into the verifier is worth the day.

One caveat belongs in the verdict line: the frontier is censored. `verifier.py:24` sets
`STABILITY_THRESHOLD = -0.5` and `verifier.py:123-125` rejects anything with `stability_real`
above it, so records on the weak-stability side were never archived.

## Sketch

One read-only host tool, `rk-overview/tools/second_pass.py` or the equivalent under
`rk-harness/scripts/`. It reads `rk-work/archive/*.jsonl` through `archive.read_all` and
`archive.record_order`, imports the pinned evaluator and cost model, and writes one JSON in the
shape `key_findings.json` uses: a top-level key per finding, each with `verdict`, `numbers` and
`series` (`key_findings.json` `_meta.schema`).

It runs on the host and out of band, for the same reason `validation.py` and `benchmark.py` do.
It has no timing in it, so it could run in the container, but a full `read_all` is expensive
(`archive.py:365-372` puts it at about 66 s at 71k records) and the container's time is the
search's.

## Cost

One to two days of work. Then minutes to hours of host CPU per run, dominated by the bisection:
each probe is a full `solve_q15` run. Bound it by restricting to the elites (at most 40 records
per order grid) plus the eight anchors, and by capping the bisection at a stated number of probes
per `(method, problem, target)`.

## Prerequisites

None blocking. Run it once now, for the design decisions, and again at the epoch-1 freeze, for
the paper's figures.

## Risks

* The archive is live, so the numbers move between runs. Stamp `archive_records` and
  `archive_last_cycle_id` the way `key_findings.json` `_meta` does, and re-run at the freeze.
* The archive is what the search found. Any frontier drawn from it is a frontier of what was
  searched, and the verdict must say so rather than imply coverage.
* Methods that overflow at some step counts drop out of one metric and not the other. Count them
  rather than dropping them silently; the count is itself a Q15 result.
* A close agreement between the two metrics weakens the epoch-2 case. That is still the right
  thing to know before the boundary.

## Epoch boundary

No. Reading the archive and importing pinned modules changes no score.
