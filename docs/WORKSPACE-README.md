# Integration-Harness — the rk run

A three-month unattended search for explicit Runge–Kutta tableaus that minimise **end-to-end
error in Q15 fixed-point arithmetic at a fixed cycle budget on Cortex-M0+**, instead of the
textbook criterion (truncation error in exact arithmetic). The spec is `handoff.md`; the
pre-flight checklist is `review.md`. Both are implemented and green.

## Is it ready? — yes, and it is running

`.\start.ps1` was run on 2026-08-29. The container `rk` is working through Phase 1 and the
watchdog is up. Pre-flight (`rk-harness\docs\REVIEW-REPORT.md`): 91 PASS, 0 FAIL; every
gating section green; falsification verdict *proceed*. The only open items are physical
(NitroSense 80 % charge cap + fans auto, elevate the laptop) and optional confidence checks.

## Layout

```
handoff.md      the frozen spec (HANDOFF v3)             review.md   pre-flight checklist
start.ps1       start container + watchdog               stop.ps1    graceful / forced stop
watcher.ps1     live status window                       config.json + configure.py   settings
rk-harness/     the code: package rk_harness, tests (943), fixtures, scripts, Dockerfile.
                Mounted READ-ONLY into the container; the pinned VERIFIER_HASH guards it.
rk-work/        the run's state: archive/YYYY-MM-DD.jsonl (one record per verified tableau),
                events.jsonl (the event stream), hypotheses.jsonl, RUNSTATE.json, HEARTBEAT.
rk-findings/    the generated site (docs/) -> https://jgoetzmann.github.io/rk-findings/
```

All three `rk-*` directories are git repos pushed to github.com/jgoetzmann/.

## Start / stop / watch / configure

```powershell
.\start.ps1                 # start container + watchdog from config.json (add -Build after changing rk-harness)
.\stop.ps1                  # graceful stop at the next cycle boundary (-Force = now)
.\watcher.ps1               # opens the live status window (read-only; Ctrl+C there never touches the run)
python configure.py show    # settings;  explain = every key with meaning/range;  set key=value [--apply]
docker logs -f rk           # container log (quiet after the startup gate)
Get-Content rk-work\events.jsonl -Tail 20 -Wait      # the raw event stream
```

### Settings (`config.json`, edited with `configure.py`)

Operational knobs only — resources, limits, LLM mode, watchdog thresholds. Scientific thresholds
(UNSTABLE, overflow margin, cost tables) are deliberately not configurable: changing them
invalidates the archive and the verifier hash.

```
python configure.py set container.cpus=6 container.memory_gb=8      # container resources
python configure.py set run.auto_stop_minutes=480                   # stop after 8 h (at a cycle boundary)
python configure.py set run.auto_stop_cycles=20                     # or after N cycles
python configure.py set run.llm=off                                 # auto | codex | on | off
python configure.py set run.enum_per_cycle=200 run.eval_budget=400  # work per cycle
python configure.py set watchdog.battery_guard=false watchdog.push_minutes=5 watchdog.cpu_pause_high_percent=70
python configure.py set ... --apply      # stop + start so it takes effect (container/watchdog keys need a restart)
python configure.py reset                # back to the handoff defaults
```

### The watcher window

`.\watcher.ps1` opens a 170-column terminal that refreshes every `watcher.refresh_seconds` with:
container state, uptime, heartbeat age, watchdog presence; cycle/phase (with what the phase
means); the full settings plus the container's live RK_* environment and resource limits;
LLM mode, number of directive calls, Codex plan usage (% of the weekly limit, reset date, last
call's tokens), API spend vs cap; progress (cycles, accepted/rejected, rate, reject codes,
enumeration ETA, tier counts, grid coverage, last improvement, current cell); what it is
working on (encourager action, current directive with its rationale/constraints/hypothesis,
last island, open/refuted hypotheses with statements); health (verifier hash pin, abandoned
cycles, last stop reason, disk, last pushes, falsification summary); per-cell best vs classical
baselines and the best `heldout_verified` records; and the event tail. `-Once` prints a
snapshot; `-Here` runs it in the current terminal.

There is no Python entry point to run by hand: the container's `entrypoint.sh` is the
launcher (`python -m rk_harness.runner` inside it). After a reboot just run `.\start.ps1`
again — the runner replays its state from `rk-work` and loses at most one cycle.

## How it works

1. **Startup gate** (`rk-harness/entrypoint.sh`): the container refuses to start unless the
   harness mount is read-only, the sha256 of the ten verifier files equals the pinned
   `VERIFIER_HASH`, and the 55 golden/canary tests pass.
2. **Cycle loop** (`rk_harness/runner.py`, one idempotent cycle at a time):
   replay the archive → ask the encourager what to do → produce candidates → verify each one
   (explicit? row sums? order conditions exact? dyadic impossibility? Q15 range? stable?
   asymptotic window?) → score it in Q15 at equal cycle budget under three cost models →
   assign its tier mechanically (`heldout_verified` / `search_only` / `unreplicated`) →
   append to the archive → resolve open hypotheses → regenerate the site → commit → save state.
3. **Where candidates come from, by phase**:
   - Phase 0: exhaustive enumeration of the 16 exactly-representable 2-stage order-2 methods
     (done — `midpoint` is provably cheapest at 11 slow cycles).
   - Phase 1: exhaustive enumeration of 5,094 exact 3-stage order-3 methods, 500 per cycle.
   - Phases 2–3: CMA-ES over the free coefficients (A snapped to dyadics, b solved exactly),
     directed by JSON directives from the LLM — Codex via your mounted `~/.codex/auth.json`
     (`RK_LLM=codex`, plan-billed). A directive can only narrow the search; malformed or
     unknown-key directives are rejected and a deterministic fallback is used.
   - Calendar: `PACKAGE` (re-verify everything, no new directions) from 2026-11-20,
     `FREEZE` from 2026-12-05.
4. **Trust boundaries**: the scorer cannot be edited (read-only mount + pinned hash); no
   GitHub credential enters the container (the host watchdog pushes); the LLM never assigns
   tiers or verdicts (code does); model-written problems go through an AST-checked
   quarantine and only ever join the held-out set; the site prints numbers only and refuses
   to build if a banned word ("novel", "beats", …) appears.
5. **Watchdog** (`scripts/watchdog.ps1`, host side): kills the container on a stale
   heartbeat (>120 s), stops it on spend over the cap or <5 GB free disk, pauses it while the
   laptop is on battery or while you are using the CPU (>50 % for 30 s), resumes when the load
   drops, and pushes `rk-work` / `rk-findings` every 10 minutes.

## Results so far

- Anchor (HANDOFF §9.5): rk4 costs 33/85 cycles (fast/slow multiplier) vs rk38 36/64 — the
  ordering reverses between two chips with the same ISA.
- Phase 0 proof: `midpoint` is the cheapest exactly-representable 2-stage order-2 method
  (11 slow cycles); `a21 = -1/4, b = (3, -2)` has the best held-out error in that cell.
- Falsification (§15) on `damped_osc`: rk4 spends 56 % (fast) / 36 % (slow) of its cycles on
  coefficient arithmetic and its Q15 error stops improving below h ≈ 0.16 — roundoff dominates
  truncation at practical step sizes, so the project proceeds.
- Under floor (ASRS) arithmetic Euler beats rk4 on the search set and every method collapses
  `rc_thermal`; both are findings, not bugs (see `rk-harness/.fullsend/notes/test-corrections.md`).

## Changing things

Any edit under `rk-harness/rk_harness/` or `fixtures/` changes the verifier hash. Then:
`cd rk-harness; .venv\Scripts\python.exe -m pytest -q; .venv\Scripts\python.exe -m rk_harness.verifier_hash --pin;
.venv\Scripts\python.exe scripts\preflight.py --docker` and restart with `.\start.ps1 -Build`.
