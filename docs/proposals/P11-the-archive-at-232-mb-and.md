# P11 - The archive at 232 MB, and what a checkpoint has to promise

## The problem

Eleven daily files, 232 MB, roughly 102k records, growing 20 to 35 MB a day. Every consumer
replays all of it.

`archive.py:225 read_all` reads each file's full text into memory, parses every line and returns
every `Record`. `replay` (`archive.py:333`) then walks them again for grids and cell stats.
`archive.py:366-367` states the cost:

> at 71k records a replay is about 66 s against roughly 250 s of actual searching

That ratio is what motivated `fold` (`archive.py:361`), and the measured effect was 468.0 s to
349.5 s per cycle at matched workload.

`fold` removed the second and third passes of a cycle. It did not remove the first.
`runner.py:847` still replays at startup, and every host tool pays full price: once per
invocation for `key_findings.py:90-91` and `demo_data.py:91`, once per refresh for
`watch.py:495-496`.

Extrapolated honestly: 500 MB in about ten days, 1 GB in about three weeks. Replay is roughly
linear in records, so a startup replay heads for two or three minutes and memory for the full
record list grows with it. Nothing breaks suddenly. The run becomes progressively more
expensive to look at, which is the failure mode that ends with nobody looking.

## Sketch: a checkpoint over the closed files

The archive is append-only and partitioned by UTC date. Once a date rolls over, that file never
changes again. That is the property a checkpoint needs, and it is already true rather than
something to arrange.

- `rk-work/ARCHIVE_CHECKPOINT.json` holds a serialized `ArchiveState`, the list of files it
  covers with each file's size, and the verifier hash the records carried. The runner writes it
  once at the first cycle of a new UTC day, covering every file that is now closed,
  temp-then-`os.replace` like every other durable write.
- `replay()` becomes: load the checkpoint if it exists and every covered file still matches name
  and size exactly; then read only the files it does not cover and `fold` them in. `fold` is
  already the exact function for this, and `test_A80` already asserts that folding equals
  replaying.
- Any mismatch means ignore the checkpoint and do a full replay. **A checkpoint is an
  optimization, never a source of truth.**
- The full-replay path stays and stays tested, with one new test that checkpoint-plus-fold
  equals a from-scratch replay on a multi-file fixture.

The record list is the other half. Most consumers want elites, counts and per-cell statistics,
not every `Record`. Pair this with P9's `replay_with_records` split; after it, a viewer can hold
the state without the records.

## What does not change, as rulings

- **No compaction, rewriting or deletion of archive files.** The archive is the evidence for an
  epoch, and a record's `verifier_hash` is what makes two epochs distinguishable later
  (`DEVELOPMENT.md` section 9). A rewritten file breaks the one property the design buys.
- **No database.** SQLite over a Docker Desktop bind mount is exactly what `jobs-design.md` R3
  rules against, and it would put a second file format between the run and its evidence.
- **Rotation belongs to `events.jsonl`, not the archive.** It is 45 MB, grows about 4.5 MB a day
  with no rotation (`status.py:47-48` says so and works around it with a 512 KB tail), and is an
  audit trail rather than evidence for a score. Closing it monthly into `events-YYYY-MM.jsonl`
  is a separate, smaller change, and every current reader already reads a tail.
- **The real ceiling is the epoch.** An epoch boundary starts a new archive. An archive too
  large for a checkpoint to help is information about the epoch, not a storage problem.

## Cost

Moderate. About 80 lines in `archive.py` plus the writer call in the runner, plus tests. No
epoch boundary: `archive.py` is outside `VERIFIER_FILES` and nothing about the records changes.

## Prerequisites

None. P9 step 1 makes this worth more, and neither depends on the other.

## Risks

- **A checkpoint that disagrees with the files is worse than no checkpoint, and silent.** Make
  the size-and-name match strict, verify it on every load, and log an event when a checkpoint is
  rejected so the rejection is visible rather than merely safe.
- **A checkpoint written over a file still being appended to** is the bug this design avoids by
  covering only closed files. Do not relax that for a fresher checkpoint.
- **One record encoding, not two.** Reuse `record_to_json` (`archive.py:42`).
- **It is derived state in `/work`,** which is committed and pushed. It is reconstructible from
  the files beside it, so gitignoring it is defensible and keeps a large daily blob out of the
  history.
