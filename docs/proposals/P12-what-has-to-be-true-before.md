# P12 - What has to be true before a second harness starts

## The problem

The coexistence rules that exist are about names: container, image, network, work dir, `STOP`
path, env prefix, and the watchdog filename that must not contain `watchdog.ps1`. The rule that
matters now is about CPU, and the machine already violates the assumption it rests on.

**The pause guard subtracts one container.** `scripts/watchdog.ps1:221-227` computes host load as
total processor time minus the `rk` container's own share, then pauses `rk` when what is left
stays high. Every other container on the daemon counts as foreground load. Today that includes
three supabase containers in a crash loop with 2553, 2621 and 2564 restarts over roughly 44
hours, plus a `vector` container marked unhealthy. `jobs-design.md` section 1.3 anticipates the
harness case and says the fix is a one-line change. With a crash-looping third-party stack
already present, that is optimistic.

**The subtraction uses an unbounded probe.** `watchdog.ps1:224` calls
`docker stats --no-stream` on every 10 s poll with no timeout. `status.py:42-45` records why
that is a risk:

> Docker on this machine has been observed taking 47 s and returning HTTP 500 on every
> endpoint, so nothing may block unbounded

which is why every probe in `status.py` has a hard cap and uses `docker inspect` instead. A 47 s
stats call stretches the watchdog's poll from 10 s to about 50 s, which quietly turns
`heartbeat_stale_seconds` = 120 from a twelve-sample threshold into a two-sample one and delays
every other trigger in the loop by the same amount.

**Two watchdogs will take turns.** jobs' Chromium reads as load to rk, so rk pauses; rk's four
CPUs read as load to jobs, so jobs pauses; whichever resumes first pauses the other.

## Sketch, in the order the work has to happen

1. **Bound the stats call before adding anything to it.** Run `docker stats --no-stream` through
   a job with a few seconds' timeout. On timeout, skip the pause decision for that pass and
   clear the sample buffer, which is what the loop already does after any pause or unpause so
   the spike rule and the average rule cannot flap. Never let an unbounded probe decide whether
   the run is paused.

2. **Subtract peers, not just self.** One
   `docker stats --no-stream --format "{{.Name}} {{.CPUPerc}}"` with no container argument
   returns every running container in a single call. Sum the rows whose name is in a
   `-PeerContainers` list (new config key `watchdog.peer_containers`, default `rk` only),
   subtract that sum, and print the resolved peer set in the startup line beside the other
   thresholds. This makes the supabase stack a deliberate decision: leave it out and it
   correctly reads as load the run should yield to.

3. **One sampler, both readers.** Rather than each watchdog running its own stats call, whichever
   samples writes the per-container percentages to a small host-side file with a timestamp, and
   both read it, treating anything older than a couple of polls as absent. Same pattern as the
   rest of the system: state on disk, derived on every read, no cross-process signalling. A
   second harness then costs no extra calls to a daemon that has been observed taking 47 s to
   answer.

4. **A census, so third-party noise is visible before it is blamed.** `status.py` already runs
   `docker inspect` with a timeout and already says "cannot tell" when the daemon is silent. Add
   a section listing every running container with its restart count and health, flagging any
   whose restart count is climbing. 2500 restarts is a fact about the machine that neither
   harness's status file mentions today, and it is the first thing a reader should see when the
   run looks slow.

5. **Two disks, not one.** `watchdog.ps1:207-212` checks free space on the work drive only.
   Docker's VHDX lives on the system drive, and a second harness plus a crash-looping stack
   write to it. `status.py:718-722` already flags any drive under ten percent free, and it only
   reports. Either the watchdog's floor covers both drives, or the docs say plainly that the
   system drive has a reporter and no guard.

6. **Then the acceptance test** that `harness-platform.md` section 9 already writes: every other
   harness's container status is identical before and after a full start, watch, stop and
   restart of the new one. Run it against the machine as it actually is, with the third-party
   stack running, not against a clean daemon.

## Cost

Moderate, and mostly in `watchdog.ps1`, which is hand-written on purpose and should stay that
way. Items 1 and 2 are worth doing whether or not a second harness ever ships. No epoch
boundary.

## Prerequisites

None for items 1 through 5. Item 6 needs the jobs harness to exist.

## Risks

- **`watchdog.ps1` stays pure ASCII,** and a timeout wrapper in PowerShell 5.1 means a job or a
  runspace, both of which are more code than they look. Keep it small and keep the failure mode
  boring: on timeout, decide nothing.
- **A peer list is a name list,** and a typo silently subtracts nothing. Print the resolved peers
  and their measured percentages in the startup line so a wrong name shows up on the first poll.
- **Subtracting more containers makes the guard less protective by design.** It must be a
  deliberate list, never "subtract every container", or the guard stops protecting the owner's
  foreground work, which is the reason it exists.
- **The shared sample file is another piece of cross-harness coupling.** Keep it advisory: a
  missing or stale file means each watchdog falls back to its own call.
