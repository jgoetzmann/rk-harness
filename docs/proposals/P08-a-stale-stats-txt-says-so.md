# P8 - A stale stats.txt says so on its own first line

## The problem

`stats.txt` exists to answer "is it actually running" without opening a terminal. The copy on
disk right now was written `2026-09-05 17:04:37` local time. It reports:

```
  [OK] RUNNING - the container is up and the heartbeat is fresh.
  started       2026-09-05 18:49 CT   up 15m 5s
  heartbeat     2026-09-05 19:04 CT   0s old
  refreshed     written once by hand. stats.ps1 -Loop keeps it current.
```

Every one of those lines was true when it was written and none of them is true now, and the
file does not say so. `rk_harness/status.py:604` prints an instruction where an expiry belongs.

The expiry already exists. `status.py:596-603` prints a staleness deadline in the host's own
timezone, with the sentence explaining what it means, but only when `--refresh` is passed,
which only happens on the `-Loop` path.

The failure is structural. A one-shot snapshot has no writer keeping it honest, so it has to
carry its own shelf life, and something has to be running for the file to mean anything at all.

## Sketch

### 1. The one-shot path gets a deadline too

`status.py:590 render_text` already takes `refresh_s: int | None`. In the `None` branch, print
the same two lines the loop branch prints, with a fixed shelf life. Fifteen minutes is longer
than any cycle at the current cadence and short enough that nobody trusts a stale file. The
wording stays as it is on the loop path: the deadline in the host's own timezone, because it
exists to be compared against the reader's taskbar clock rather than against Central.

This is a pure function of `written_at`. It touches neither the UTC-storage rule nor the
no-value-carried-forward rule.

### 2. The age is answerable without rewriting the file

`python -m rk_harness.status --age` reads `stats.txt`'s header, prints the age of its
`written_at`, and exits nonzero past the deadline. It runs no probe, so it cannot perturb the
machine, and P10's host section gets a one-line way to assert the file is current.

### 3. Keep it current by construction

`start.ps1` already launches one background process and `stop.ps1` already stops one. Add the
stats writer to both:

- `start.ps1`: after the watchdog, launch `stats.ps1 -Loop -Background -Interval 60` and print
  the file path in the closing block.
- `stop.ps1`: stop it by **full script path**, never by wildcard, for exactly the reason the
  jobs harness's watchdog must not contain the substring `watchdog.ps1`.

At 60 s the writer costs one `docker inspect` capped at 6 s, one 0.25 s CPU sample and a
512 KB tail read per minute, which is small beside a container holding four cores. Pass
`-NoGpu` unless the GPU rows are wanted: `probe_gpu` wakes the discrete GPU, which is why
`status.py` already skips it on battery.

### Considered and not recommended

Having the watchdog write `stats.txt` on its 10 s poll. It has the right paths and already
shells out to Python for the saturation check. Rejected: the watchdog is the one hand-written
safety-critical component, and a status write is not a safety function. A separate writer that
can die without consequence is the right shape, and its death is exactly what the deadline line
makes visible.

## Cost

Small. About twenty lines across `status.py`, `start.ps1` and `stop.ps1`, plus a T5 test that
`render_text` with `refresh_s=None` contains a deadline. No epoch boundary.

## Prerequisites

None.

## Risks

- **A third minimized window.** Title it, the way `stats.ps1:49` titles the loop window.
- **File locking.** The writer holds `stats.txt` briefly; `status.write` (`status.py:759`)
  already falls back to a non-atomic write on `PermissionError`, so an open Notepad does not
  lose the write.
- **Script drift.** `start.ps1` and its versioned copy under `rk-harness/scripts/workspace/`
  have already diverged (P10). A change to one is a change to both.
- **ASCII.** PowerShell stays pure ASCII, and the rendered file is written with
  `encoding="ascii"` at `status.py:762`, which bounds the wording already.
