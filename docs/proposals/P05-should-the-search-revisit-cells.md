# P5: should the search revisit cells

Status: proposal. All of it in unpinned files.

## The question this answers

Is "always target the emptiest cell" the right exploration policy for a MAP-Elites archive on a
cost grid, and how would we know?

The run is producing very little. Median accepted records per cycle fell from 103 on 09-05 to 7
on 09-07. Recent `candidates_processed` events read accepted 3 to 5, skipped 25 to 35, total 38
to 39 (`rk-work/events.jsonl`). `stall_counter` in `rk-work/RUNSTATE.json` is 329. The immediate
cause is a fallback asking for a cell that cannot exist, and fixing that is an unblock, not
research. The question underneath is what the target should be once the fallback works, and
nothing in the documents decides it.

## Four facts, all in unpinned code

**1. Emptiest-cell targeting has no reachability model.** `directive._emptiest_cell`
(`directive.py:193-207`) and `encourager.emptiest_cell` (`encourager.py:34-48`) both scan stages
2 to 6 crossed with buckets 0 to 7 and return the lowest absent cell in scan order. An explicit
`s`-stage method cannot exceed order `s`, and order 4 needs at least 4 stages, so at order 4
every cell at 2 and 3 stages is empty by arithmetic and always will be. The order-4 grid holds
three cells today, `(4,2)`, `(6,2)` and `(6,3)`, out of the 18 cell pages published under
`rk-findings/docs`. Reachable and empty order-4 cells exist; the scan never gets to them.

**2. The archive's memory does not reach the generator.** `runner.py:876` builds `seen` from the
archived hashes plus the rejected hashes, and `runner.py:929-936` skips on it. But candidates are
generated first, by `search.cmaes_island` (`search.py:219-256`), which knows nothing about it, so
the evaluation budget is spent before the skip happens. `cmaes_island` also yields only on
improvement (`search.py:248`) and keeps a `seen` set that lives for one call.

**3. Warm starts are unreachable.** `search.cmaes_island` reads `constraints["x0"]`
(`search.py:221-223`). `DIRECTIVE_SCHEMA` declares `additionalProperties: false` with exactly
four constraint keys (`directive.py:50-71`) and `validate_directive` rejects any other
(`directive.py:117-119`). `search.default_constraints` (`search.py:38-39`) supplies none. So in
production every island starts from `_default_x0` (`search.py:211-216`), a fixed point, for every
cell, on every cycle. Restarting from the same point against a space whose good members are
already archived is the mechanism behind the skip rate.

**4. Cell quality is recorded but unused for targeting.** `archive.fold` and `replay` keep Welford
statistics per `(order, stages)` per cost model and metric (`archive.py:386-397`). Nothing reads
them when choosing a cell. And `archive._better` (`archive.py:261-263`) ranks strictly by
held-out error and ignores tier, so an unreplicated record can evict a `heldout_verified` elite.

## Three policies

**R, reachable emptiest.** Filter the scan to cells that can exist. `s >= p`, with `s >= 4` at
`p = 4`, and a bucket range bounded at each stage count, which is checkable from the cost model
because the cheapest and most expensive `s`-stage tableaus under `m0plus_fast` bound it. One
function, used by both `directive.py` and `encourager.py` so the two cannot disagree.

**V, revisit by value.** Target the reachable occupied cell whose elite has the highest held-out
error. Both files already implement exactly this as their grid-full branch
(`directive.py:199-207`, `encourager.py:39-48`). Neither ever reaches it, because the grid is
never full and never can be.

**W, warm start.** Whatever the target, seed `x0` from that cell's incumbent elite. One new
directive-schema key and a matching entry in `search.default_constraints`.

## One measurement

None of these should be adopted on argument. The run already logs what is needed: `island_start`
and `island_done` with `yielded` and `achieved_orders` (`runner.py:441-455`), and
`candidates_processed` with accepted, rejected, skipped and total (`runner.py:976`).

Add a policy field to the fallback directive, rotate the policies over a stated number of cycles
each, and compare accepted per cycle, skipped fraction, and new-cell rate. That is an A/B test
inside the run, it costs no epoch boundary, and it produces something the paper does not have: a
measured account of what an autonomous MAP-Elites search does after its easy cells are full. The
current answer to that question, which is "it re-proposes tableaus it has already archived at a
rate above 80 percent", is a methodology result in its own right and is worth reporting whichever
policy wins.

## Cost

The reachability filter is an afternoon. The warm start is about a day including the schema change
and tests in `tests/test_t3_archive_search_directive.py`. The rotation and its analysis are about
a day.

## Prerequisites

1. The fallback livelock fixed. A policy comparison against a broken baseline measures nothing.
2. The codex cap latch cleared, because the model directive path is what the fallback substitutes
   for, and a policy test that only ever sees fallbacks tests the fallback.
3. A ruling on `archive._better` and tier, since a revisit policy interacts with it directly.
4. A decision on whether the saturation freeze rule follows the policy, made before the rotation
   runs rather than after.

## Risks

* Revisiting trades coverage for depth. The saturation rule counts a record in a previously empty
  cell as progress (`DEVELOPMENT.md:246-250`), so a revisit policy will read as less progress by
  the run's own definition even if it produces better methods.
* `archive._better` ignoring tier means a revisit that lowers held-out error can replace a
  replicated elite with an unreplicated one. That is why the published top-tier count differs
  between the two sites. If revisiting is adopted, the tie-break needs deciding and writing down.
* Warm starting narrows the search around an archived point, which raises the skip rate rather
  than lowering it unless sigma widens to match. Sigma is 0.3 (`search.py:236`) and is not a
  directive parameter.
* An A/B test inside a live run mixes policies in one archive. `directive_id` on every record
  makes the attribution recoverable, but the archive is no longer a single-policy sample and the
  paper has to say so.

## Epoch boundary

No. `directive.py`, `encourager.py`, `search.py` and `runner.py` are all outside `VERIFIER_FILES`
(`DEVELOPMENT.md:120-127`). Nothing here changes a score.
