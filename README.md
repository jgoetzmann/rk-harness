# rk-harness

Orchestrator, verifier, evaluator, cost model, tests and fixtures for the
quantization-aware Runge–Kutta discovery project. The spec is `docs/HANDOFF.md`;
this repo is mounted **read-only** inside the run container so the scorer cannot be
edited by the agent that is being scored.

## Layout

```
rk_harness/            the package (module interfaces: HANDOFF §4)
fixtures/              verified ground truth (HANDOFF §9–§12) — never regenerate
tests/                 HANDOFF §14 acceptance suite (pytest)
scripts/               bootstrap, container wrapper, watchdog, PAT check, falsification
Dockerfile, entrypoint.sh
VERIFIER_HASH          pinned sha256 over the ten verifier files (HANDOFF §4.11)
```

## Local use

```powershell
.venv\Scripts\python.exe -m pytest -q                      # full suite (slow tests take minutes)
.venv\Scripts\python.exe -m pytest -q -m "not slow"        # fast subset
.venv\Scripts\python.exe -m rk_harness.verifier_hash --pin # pin the verifier hash
.venv\Scripts\python.exe -m rk_harness.falsification       # HANDOFF §15 experiment
$env:RK_WORK_DIR="..\rk-work"; $env:RK_FINDINGS_DIR="..\rk-findings"
.venv\Scripts\python.exe -m rk_harness.runner --cycles 1   # one cycle
.venv\Scripts\python.exe -m rk_harness.watch               # read-only live view
```

The full suite runs in CI (`.github/workflows/ci.yml`), which is where it belongs: locally it
loads the host enough that the watchdog pauses the container, so verifying the harness suspends
the run. Prefer the fast subset, or the gate the container itself uses
(`python -m rk_harness.verifier_hash --check` plus the `G*`/`K*` tests), and let CI carry the
rest. Details and the reasoning: `docs/CI.md`.

Environment: `RK_WORK_DIR` (rk-work checkout), `RK_FINDINGS_DIR` (rk-findings checkout),
`RK_PHASE` (initial phase), `RK_LLM=off|on|codex` (`on` = API key via `OPENAI_API_KEY`; `codex` = `codex exec` with the host's `~/.codex/auth.json`, plan-billed, needs the Codex CLI which the image installs), `RK_SITE=on|off`, `RK_GIT_COMMIT=on|off`,
`RK_CLOCK` (fixed ISO timestamp for deterministic runs), `RK_EVAL_BUDGET`
(CMA-ES fitness evaluations per island). Credentials come from `.env` (see `.env.example`).

## Operating the run (Windows host, Docker Desktop)

Start (builds the image when `-Build` is given, mounts the harness read-only, filters
`GITHUB_TOKEN` out of the container's environment, mounts `~/.codex/auth.json` and selects
`RK_LLM=codex` when it exists):

```powershell
cd D:\Programming-Projects\Integration-Harness\rk-harness
.\scripts\run.ps1 -Build `
  -Harness  D:/Programming-Projects/Integration-Harness/rk-harness `
  -Work     D:/Programming-Projects/Integration-Harness/rk-work `
  -Findings D:/Programming-Projects/Integration-Harness/rk-findings `
  -EnvFile  D:/Programming-Projects/Integration-Harness/rk-harness/.env
# then, in its own window (kill switch, pause watchdog, host-side push every 10 min):
.\scripts\watchdog.ps1 -Work D:/Programming-Projects/Integration-Harness/rk-work `
  -Findings D:/Programming-Projects/Integration-Harness/rk-findings `
  -EnvFile  D:/Programming-Projects/Integration-Harness/rk-harness/.env `
  -LogFile  D:/Programming-Projects/Integration-Harness/watchdog.log
```

`-LogFile` is worth passing. The watchdog runs minimized and nobody looks at it, so an ALERT or a
pause decision is printed once and lost; with a log file, `rk_harness.status` reads the last few
lines back into `stats.txt`, with each line's own age. The stamps in it are UTC with a `Z`
(`Get-Date -Format s` is local and unlabelled, and would be read as UTC seven hours out). The
workspace `start.ps1` passes this and the file rotates itself at 1 MB.

Three more parameters matter on a machine that runs anything else. `-PeerContainers` (default
`rk`) is the list of containers whose CPU is subtracted from host load before the pause decision;
everything else on the daemon counts as foreground load the run should yield to, which is
deliberate. `-StatsTimeoutMs` (default 4000) bounds the `docker stats` probe: on timeout the guard
decides nothing for that pass rather than deciding on a stale number, and while the container is
paused the probe is skipped entirely. `-MinFreeSystemGB` (default 0, report only) watches the
system drive, where Docker's VHDX lives and which the work-drive floor does not cover.

Read its state without a terminal: the workspace `stats.ps1` writes `stats.txt`, and every copy of
that file states its own staleness deadline in this machine's timezone, whether a loop wrote it or
a single run did. `python -m rk_harness.status --age` (or `.\stats.ps1 -Age` from the workspace)
answers how old the file on disk is without running a probe or rewriting it: exit 0 current, 1 past
the shelf life it declares, 2 unreadable. The file also reports acceptance rate against the older
part of the same tail, how many cycles it has been since the model itself wrote a directive, and
every other container on the daemon with its restart count and health.

Watch it: `docker logs -f rk` (entrypoint prints the hash check and the golden gate, then
the runner is quiet — the event stream is `rk-work\events.jsonl`), or the TUI
`$env:RK_WORK_DIR="D:/Programming-Projects/Integration-Harness/rk-work"; .venv\Scripts\python.exe -m rk_harness.watch`.
The site is regenerated every cycle into `rk-findings\docs` and appears at
https://jgoetzmann.github.io/rk-findings/ a few minutes after the watchdog pushes.

Stop gracefully: `New-Item D:\Programming-Projects\Integration-Harness\rk-work\STOP` — the
runner exits at the next cycle boundary (delete the file before restarting). Hard stop:
`docker stop rk`. Restart after a reboot: the same `run.ps1` line without `-Build` (state is
replayed from `rk-work`; a partial cycle costs at most one cycle).

Phases advance on their own: 0 (done, 16 points) → 1 (5,094 exact order-3 points, 500 per
cycle ≈ 10 min/cycle) → 2/3 (CMA-ES with LLM directives, Codex when `auth.json` is mounted).
`PACKAGE` starts 2026-11-20 and `FREEZE` 2026-12-05 by the calendar rules.

Before changing anything under `rk_harness/` or `fixtures/`: the container refuses to start
unless `VERIFIER_HASH` matches, so re-pin with `python -m rk_harness.verifier_hash --pin`,
rerun `scripts\preflight.py --docker`, and restart with `-Build`.

## Container

`scripts/run.ps1 -Build` builds the image and starts `rk` with the HANDOFF §13.2 flags;
`scripts/watchdog.ps1` runs the host-side kill switch and pause watchdog;
`scripts/network.sh` (inside WSL) applies the egress allowlist. `entrypoint.sh` refuses
to start unless the verifier hash matches and the golden/canary tests pass.
