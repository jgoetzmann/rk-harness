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
`RK_PHASE` (initial phase), `RK_LLM=on|off`, `RK_SITE=on|off`, `RK_GIT_COMMIT=on|off`,
`RK_CLOCK` (fixed ISO timestamp for deterministic runs), `RK_EVAL_BUDGET`
(CMA-ES fitness evaluations per island). Credentials come from `.env` (see `.env.example`).

## Container

`scripts/run.ps1 -Build` builds the image and starts `rk` with the HANDOFF §13.2 flags;
`scripts/watchdog.ps1` runs the host-side kill switch and pause watchdog;
`scripts/network.sh` (inside WSL) applies the egress allowlist. `entrypoint.sh` refuses
to start unless the verifier hash matches and the golden/canary tests pass.
