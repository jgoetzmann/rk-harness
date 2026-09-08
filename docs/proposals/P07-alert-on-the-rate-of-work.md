# P7 - Alert on the rate of work, not on signs of life

## The problem

For about 35 hours the run was fast, green, heartbeating and producing almost nothing, and
nothing alerted. Accepted records per cycle fell from a median of 103 on 09-05 to 7 on 09-07
while cycle time fell from 470 s to 118 s, so every dashboard number moved in the direction
that reads as healthy.

Every signal the system has is a liveness signal.

- `scripts/watchdog.ps1:184-190` kills the container when `HEARTBEAT` goes stale. The heartbeat
  was fresh throughout.
- `scripts/watchdog.ps1:194-197` is the only progress-shaped trigger, and it asks whether *any*
  candidate was accepted within `no_candidate_minutes` (30). Accepted has never been zero: the
  `cycle_done` at `2026-09-08T02:10:59Z` reads `accepted: 8`. The trigger fires on a stop, not
  on a collapse.
- `rk_harness/status.py:432` `progress_note` is the closest thing to a progress signal, and it
  measures cycle *cadence*, which improved. Faster cycles read as healthier.
- `rk_harness/status.py:689` prints `stall_counter` as a bare number with no threshold on it.
  `RUNSTATE.json` now reads 329, and every `cycle_done` in the recent tail carries
  `"improved": false`.

The run has a liveness monitor and no productivity monitor.

## Sketch

Three signals, each placed where it can be computed honestly.

### 1. Acceptance rate, in `status.py`

`cycle_done` already carries `accepted`, `improved` and `stall_counter`, and
`status.py:358 tail_events` already reads 512 KB of `events.jsonl`, which is roughly 80 cycles
at the current cadence.

Add `accept_rate(events)` beside `cycle_cadence` (`status.py:389`): the median `accepted` over
the newest third of the `cycle_done` events in the tail, against the median over the oldest
third, with both sample counts. Print it in the PROGRESS section next to `stall`. When the
recent median is below half the older one and both windows hold at least ten samples, append a
line to `problems`, which is how `status.py` already surfaces anything a reader must act on.

This keeps the three properties the file exists to keep: it is derived fresh from the tail on
every pass with nothing carried forward, it says nothing about the container, and it reports
its own sample count so "not enough data" is distinguishable from "no collapse".

### 2. Gate closed for N cycles, also in `status.py`

Computable today with no runner change: `llm_skipped` is already in the stream (four in the
last 20 KB), written by the directive gate at `runner.py:491`. Count `cycle_done` events since
the last model-written directive and report "the model has not written a directive in N
cycles".

The literature gate (`runner.py:669-670`, `_codex_capped`) and the hypothesis gate
(`runner.py:774`) log nothing at all. Add one `llm_skipped` line to each with its own `gate=`
field. Two lines in an unpinned file, and all four gates become countable from one query.

While there: `_codex_rate_limits` (`runner.py:285-316`) already returns `resets_at` and
`window_minutes`, and nothing reads either. Print them. A snapshot older than its own window is
the latch, stated in one line.

### 3. Watchdog alerts have to survive their window

`start.ps1:84` launches the watchdog with `Start-Process powershell -ArgumentList "-NoExit ..."
-WindowStyle Minimized`. The ALERT at `watchdog.ps1:196`, the saturation advisory at `:131`,
every push failure at `:51` and every pause and unpause exist only in the scrollback of a
minimized window.

Add a `-LogFile` parameter and a small `Say` helper that writes the same line to the console
and appends it to a file, and have `start.ps1` pass the path. Then `status.py` can tail its
last few lines into PROBLEMS. Put the log at the workspace root next to `stats.txt`, which is
gitignored: `rk-work` is committed and pushed, and host-side operational noise should not enter
the run's record.

### The ruling behind the split

The watchdog does not compute the science. It keeps its hard limits and its one informational
alert. The derived judgements live in `status.py`, which already reads the event stream and
already has a vocabulary for "cannot tell". The watchdog's part is to make what it says
survive.

## Cost

Moderate. One function and about fifteen lines of rendering in `status.py`; a parameter and a
helper in `watchdog.ps1`; two log lines in `runner.py`; T5 tests for `accept_rate` against a
synthetic event list.

No epoch boundary. `status.py`, `watchdog.ps1` and `runner.py` are all outside
`VERIFIER_FILES`, and no archived number changes.

## Prerequisites

None strictly. Do P8 first anyway: a signal inside a two-day-old file is not a signal.
`watchdog.ps1` stays pure ASCII.

## Risks

- **A threshold that fires on ordinary phase transitions.** Phases 0 and 1 enumerate and accept
  in bulk; 2 and 3 search. Comparing within one tail window mostly avoids this, but the check
  should quote both medians and both sample counts rather than only its verdict, so a phase
  change reads as a phase change.
- **The tail is a time window, not a cycle window,** and it shrinks as cycles get faster.
  Report how many `cycle_done` events were found, and when that is below the minimum say so
  instead of computing a ratio.
- **One more line in PROBLEMS is one more thing to ignore.** Keep the list short, which is also
  the argument for P8.
