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
.venv\Scripts\python.exe -m rk_harness.dashboard           # read-only TUI
```

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
  -EnvFile  D:/Programming-Projects/Integration-Harness/rk-harness/.env
```

Watch it: `docker logs -f rk` (entrypoint prints the hash check and the golden gate, then
the runner is quiet — the event stream is `rk-work\events.jsonl`), or the TUI
`$env:RK_WORK_DIR="D:/Programming-Projects/Integration-Harness/rk-work"; .venv\Scripts\python.exe -m rk_harness.dashboard`.
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
