# Lanes: the three-way split as data (rk_harness/lanes.py)

Status: **shipped inert, read by the viewers**. No cycle runs a lane. What imports
`rk_harness.lanes` is the reporting side only: `status.py` reads the schedule and the
measured shares so `stats.txt` and the watcher can name the lane a cycle belonged to, and
`saturation.py` measures its no-progress window in explicit-lane seconds rather than wall
clock (D35). All three are read-only and all three fall back to today's behaviour when the
cycle log does not exist, which it does not. The live container behaves exactly as it did
before this file existed, and it will keep doing so until someone wires the runner and sets
a config value. This note says what is in the tree, what it costs to arm, and what has to
happen before arming would be honest.

## Why it exists

The roadmap has described a 70/15/15 split of attention across explicit, adaptive and
implicit work since 2026-09-02. A survey on 2026-09-09 measured what the machine actually
did over the preceding 186 cycles: 0.16 s of about 32,700 s reached the two side classes,
which is 0.0005 percent against an intended 30 percent. Neither number was wrong when it
was written. The problem is that the intention lived in a sentence in a document and the
outcome lived nowhere at all, so the two could drift apart with nothing in the tree in a
position to notice.

So the split becomes data, and the outcome becomes a document:

* `scheduled_shares(schedule)` is what the split asks for. Arithmetic on a string.
* `rk-work/schedule/shares.json` is what happened. Aggregated only from recorded per-cycle
  evidence, and when there is no evidence it says so rather than restating the intention.

## The schedule string

One letter per cycle, indexed by the cycle id: `E` explicit, `A` adaptive, `I` implicit.

| String | Meaning |
| --- | --- |
| `E` | every cycle explicit. Today's behaviour, and the default. |
| `EAI` | thirds. |
| `EEAEEIEEEAEEIEEEAEEI` | the roadmap's 70/15/15, as `lanes.LEGACY_70_15_15`. |

`parse_schedule` accepts a schedule whole or not at all. `"EAIX"` does not become `"EAI"`;
it becomes `"E"`. The discipline is `runner._search_policy`'s, one notch stricter: a typo in
the container environment must not arm a two-thirds restructure of the run that nobody
asked for. `schedule_problem(value)` returns the reason a value was refused, so a host tool
can complain out loud about a value the runner falls back on silently.

`literature_schedule()` maps a lane schedule onto `literature.TRACK_SCHEDULE` letters
(E to A, A to B, I to C), so one config value can set both the CPU split and the reading
split and the two cannot drift. Note the collision: `A` is adaptive as a lane and the lead
track in `literature.py`. That mapping function is the only place the two alphabets meet.

## The two documents

`schedule-cycles/1`, one JSON line per cycle in `rk-work/schedule/cycles.jsonl`, appended by
the runner as a cycle ends: `cycle`, `lane`, `ts` (UTC ISO-8601 Z), `seconds`,
`productive_seconds`, `records_appended`, `points_measured`, `lane_records_appended`,
`schedule`, `code_hash`. `records_appended` counts the scored archive and
`lane_records_appended` the lane's own parallel archive; they stay apart because a lane
cycle appends nothing to `rk-work/archive/` by design, and that is a fact to read rather
than a gap.

`schedule-shares/1`, `rk-work/schedule/shares.json`: `_meta` (schema, provenance, window
size, discarded rows counted by reason), `window`, `lanes` (measured), `scheduled_share`
(intended), `drift` (measured less intended, per lane) and `statement`. The document is a
pure function of its rows. No clock is read anywhere in this module: `append_cycle` takes
its stamp and its seconds from the caller, and `build_shares` defaults the generated stamp
to the newest row's own, so two builds over the same log are byte-identical.

The cycle log is read from the end. `load_cycles` seeks backwards for the last
`window_cycles` lines, so a log that grows by a line per cycle forever never costs more
than its window. This module never opens `events.jsonl` or the archive.

`shares.json` is **not** on the traceability rule's list of public-number sources
(key_findings.json, validation/results.json, benchmark/results.json, the side-track
ledger), and `_meta.not_a_public_page_source` says so inside the file. Adding it is a
deliberate decision for the owner.

## What wiring it would take

Not done here, and deliberately so. In outline:

1. Two config keys, `run.lane_schedule` (str, default `E`, env `RK_LANE_SCHEDULE`) and
   `run.lane_max_seconds` (int 30..420, default 100, env `RK_LANE_MAX_SECONDS`), through
   the six files that carry a key: `config.json`, both copies of `configure.py`, both
   copies of `start.ps1`, `scripts/run.ps1`, and `preflight.RK_ENV_MAP` (HOST5 fails
   without the last one). No pinned file and no `entrypoint.sh` edit.
2. The runner edits, including the one that is a genuine bug if it is skipped: the stall
   counter at `runner.py:1172` must not advance on a non-explicit lane, or the rotation
   silently drives the encourager ladder.
3. `rk-overview/tools/generate.py` `_SUITE_DESC["T16"]`. The overview build raises
   SystemExit on an undescribed tier, so `tests/test_t16_lanes.py` needs its entry.

The 420 s ceiling on the lane budget re-derives `docs/SIDETRACK-AUTOMATION.md` section
445-455 for a lane: worst cycle 642 s plus the budget plus the worst measured point 48 s
must stay inside `stop.ps1`'s 20 minute grace. 642 + 420 + 48 is 18.5 min. Re-derive it if
cycle time grows.

## The cost of arming, stated plainly

At `EAI` and a 100 s lane budget the cycle stays about 176 s and the total cycles per day
stay about 491, but explicit search cycles fall from about 491 to about 164, a 67 percent
reduction. That is the honest price of thirds and no version of thirds avoids it.

## Do not arm until the two lanes have open-ended work

The side-track catalogue is complete: 103 of 103 points measured under the current code
hash, and every firing since cycle 2560 logged `sidetrack_exhausted`. A third of the machine
aimed at two empty queues buys hours a day of `elapsed_s 0.05`. The lanes need either a
widened catalogue or their own searches before the schedule means anything.
