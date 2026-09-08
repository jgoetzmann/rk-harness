# P9 - One viewer, and a refresh that does not read 232 MB

## The problem, in two halves

### `dashboard.py` is a program nothing runs

429 lines, no launcher. `watcher.ps1` runs `python -m rk_harness.watch`, and `watch.py` does
not import it. Its only callers are the T4 tests
(`tests/test_t4_ledger_runner_site.py:43`). `docs/DEVELOPMENT.md:199` says
"`rk_harness/dashboard.py` renders the panels" for the watcher, which is not true: `watch.py`
has its own.

It carries its own copies of `_now`, `_parse_ts`, `_fmt_td` and `_num`, and its coverage line
(`dashboard.py:334`, with `_CELLS_PER_ORDER = 5 * 8` at `:32` for stages 2..6) counts cells
over the same wrong stage domain as the other known instances of that error.

### The viewer that *is* used reads the whole archive twice per refresh

`watch.py:495-496`:

```python
arch = archive.replay()
records = archive.read_all()
```

Two full passes, once per refresh, at `watcher.refresh_seconds` = 5. `archive.py:366-367` puts
a number on one pass: about 66 s at 71k records. The archive is now 232 MB across eleven files
and roughly 102k records, so a refresh is on the order of three minutes of host CPU and the
loop never catches up. `read_all` (`archive.py:225-256`) also reads each file's full text into
memory before parsing, so peak memory is the largest file plus every `Record`.
`dashboard.py:419` runs the same replay every 5 s in its own loop.

This matters beyond the view. `watchdog.ps1:230-250` pauses the container when non-container
host CPU stays high. Watching the run is exactly that load. It is the CI argument (`CI.md`,
platform **P13**) applied to the watcher: the tool for seeing whether the run is progressing
suspends the run.

## Sketch

### Retire `dashboard.py`, keeping the one panel worth keeping

`_cells_panel` (`dashboard.py:265-300`) tabulates each occupied cell's elite against the best
classical method in the same cell. That is the project's own question, and no `watch.py` panel
shows it. Port it into `watch.py` as a results-panel mode behind `--cells`, using the records
`watch.py` already loads.

Move the T4 assertions that matter onto the ported panel. Keep the canary that a viewer does
not import `runner` where it is: that is the load-bearing test in that file, not the `Layout`
shape. Fix `DEVELOPMENT.md:199` and the `CLAUDE.md` rule 8 wording in the same change, since
both name `dashboard.py`.

The alternative, giving `dashboard.py` a launcher, is worse. Two TUIs over the same state files
drift apart, and the one with the launcher would be the one with a 5 s full-archive replay in
its loop.

### Make a refresh cheap, in this order

1. **Read once, not twice.** `replay()` already reads every record (`archive.py:333-334`) and
   the `ArchiveState` it returns does not carry them, so `watch.py` reads them again. Add
   `replay_with_records() -> tuple[ArchiveState, list[Record]]` and have `replay()` call it.
   Halves the cost with no cache and no new invariant.

2. **Cache on the archive directory's shape.** A module-level cache in `archive.py` keyed on
   the sorted tuple of `(name, size, mtime_ns)` for every `*.jsonl` in `archive_dir()`. Files
   are append-only and today's file changes on every accepted record, so a hit means the
   archive genuinely has not moved, and a miss is a full pass exactly as today. Give it to
   viewers, `key_findings.py` and `demo_data.py`. Leave the runner alone: it uses `fold`
   (`runner.py:981`) precisely because it knows what it appended, and a cache under it earns
   nothing.

3. **Where the cache still misses every time, use less.** `progress_panel` and `results_panel`
   want elites and counts, not every record. That is P11's checkpoint. Until it exists, steps 1
   and 2 take the common case from two passes to zero.

## Cost

Moderate. The deletion and port is contained. The cache is about thirty lines plus a test that
mutating any file invalidates it. No epoch boundary: `archive.py`, `watch.py` and
`dashboard.py` are all outside `VERIFIER_FILES`.

## Prerequisites

None. P11 supersedes step 3.

## Risks

- **Deleting a module deletes its tests.** Read what T4 asserts before removing anything, and
  re-home the viewers-do-not-import-runner canary rather than dropping it.
- **A cache keyed on mtime is wrong** if a file is rewritten within one filesystem timestamp
  tick. Include size, and note that the archive is append-only, so a rewrite preserving both
  size and mtime is not a case this system produces.
- **Any cache in a long-lived process is state that can go stale.** Only viewers and host tools
  get it, and that rule belongs in the docstring the way `fold` states its own.
- **`watch.py` must not import `runner`.** The ported panel imports only `archive` and
  `tableau`, which is what `dashboard.py` already did.
