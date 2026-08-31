# Review notes — how `docs/REVIEW.md` was executed

`scripts/preflight.py` runs every machine-checkable item and writes `docs/REVIEW-REPORT.md`.
Run it as `.venv\Scripts\python.exe scripts\preflight.py --docker` (add `--reuse-suite` to skip
the 90 s pytest run when only non-suite items changed). Exit code 1 means a FAIL in a gating
section (0, A, B, C, K).

## Rulings made during the review

- **I5 vs I6.** I5 asks for zero hits of `supported/refuted/inconclusive` in the LLM prompt; I6
  (and HANDOFF §6) require every prompt to carry the refuted list *with verdicts*. Both cannot be
  literal. Applied reading: the template never instructs the model to emit a verdict and the
  directive schema has no verdict field; verdict words appear only as data labels in the refuted
  list. Checked by `preflight.py` I5/I6.
- **A6 / K13 and `credentials.py`.** The module necessarily names `OPENAI_API_KEY` (HANDOFF §2.2),
  so it is excluded from the literal `openai` text grep together with `runner.py`. No module other
  than `runner.py` imports an LLM client or opens a socket.
- **J4 (set the system clock forward).** Executed through the real code path with `RK_CLOCK`
  driving `runner.now()` (the same function the encourager receives) instead of changing the
  Windows clock. All three transitions fire.
- **F1 (`.wslconfig`).** `C:\Users\jacob\.wslconfig` carried another project's settings (a
  "governor" with processors=4, swap=0). With the owner's go-ahead it was overwritten with the §13
  file (`scripts/wslconfig.rk`); the previous version is kept at `~/.wslconfig.bak-governor`.
  After `wsl --shutdown`, the VM reports 7.8 GiB RAM, 8 CPUs, 4 GiB swap.
- **0.2 (`Q15_INEXACT` grep).** Zero hits in code, tests, scripts and fixtures. `docs/HANDOFF.md`
  mentions the string because it documents the v2 defect; that is the only hit in the repo.
- **A3 positive control.** The container gate prints its summary only when pytest is not run
  with `-qq`; `entrypoint.sh` now passes `-rN` without `-q` (pyproject already sets `-q`) so the
  log reads "55 passed, 888 deselected".

## Bug found by the review (E2 / R1)

Killing the runner three times mid-cycle and restarting left the archive with 17 records
instead of 22: the first run died after seeding 3 of the 8 classical baselines, and
`run_cycle` only seeded when the archive was empty, so the remaining five baselines were never
written and the phase advanced without them. `seed_baselines` now runs on every cycle (it is
idempotent per hash). The pre-flight E2 item reproduces the scenario locally
(`kill -9` × 3, then a clean run must reach 22 records with no duplicates).

## A2 — GitHub credential is host-only (owner's decision, 2026-08-29)

The PAT in `.env` has contents read/write on all three repos (the owner's choice: it is the
owner's general-purpose token). Rather than narrow the token, the credential simply never enters
the container: `scripts/run.ps1` passes a filtered env file (`scripts/container_env.ps1`,
GITHUB_TOKEN removed), the runner only commits into the mounted `/work` and `/findings`, and
`scripts/watchdog.ps1` pushes both repos from the host every `-PushMinutes` with the owner's own
git credentials. The property K5 exists to guarantee — the agent cannot push to `rk-harness` —
therefore holds independent of the token's scope, and the pre-flight A2 verifies it directly
(filtered file has no token; `env` inside a container started with it shows none).
`scripts/check_pat.ps1` (PATCH probe + dry-run push probe) is still run and reported as INFO.

## G6 — Codex in the container

`RK_LLM=codex` makes the runner call `codex exec --skip-git-repo-check --sandbox read-only`
with the mounted `/root/.codex/auth.json`; the image installs `@openai/codex@0.151.0`, the
egress allowlist gains `chatgpt.com` and `auth.openai.com`, and `run.ps1` defaults to codex
when `auth.json` exists. The pre-flight G6 item runs `codex login status` inside the container.

## A11 — egress allowlist on Docker Desktop

`scripts/network.sh` is written for a Linux host (iptables `DOCKER-USER` chain). Docker Desktop
on Windows runs dockerd inside its own `docker-desktop` WSL distro, which ships no `iptables`
binary in its shell, so the script cannot be applied there. Options: run the container on a
Linux/WSL-native Docker engine (where the script works as written), or accept that egress is
unrestricted and rely on the other boundaries (read-only harness, no GitHub credential in the
container, spend cap). Reported as MANUAL.

## Interpretation and literature on the findings site (owner's decision, 2026-08-30)

HANDOFF section 17 limited the site to "numbers and mechanically generated captions". The owner
directed that model-written interpretation and web-researched literature digests be published in
rk-findings (rk-overview stays human-written). Both pages carry the automatic banner plus a
"model-written, verify sources" note, and all text passes through literature.soften() before it
is stored, so E4/H2 (banned words, build() raising) still hold and are still tested.

## Incident 2026-08-30: watchdog killed a fresh container mid-gate

After the site-redesign restart, the watchdog's first poll saw a HEARTBEAT file predating the
restart (age > 120 s) and ran `docker kill` 14 s after start — before the entrypoint gate had
finished, so the runner never got to write a fresh heartbeat. The run then sat exited for ~90
minutes because nothing restarts a killed container. Three-part fix: the entrypoint now writes a
heartbeat at second zero; the watchdog never kills a container younger than the staleness
threshold; and the container runs with `--restart on-failure:5`, so a wrongful kill self-heals
while a graceful STOP exit (code 0) or an explicit watchdog `docker stop` stays down.

## Items that need the host or a human

A2 (fine-grained PAT in `.env`, then `scripts/check_pat.ps1`), A11 (`scripts/network.sh` in
WSL, then curl from the container), E4 (`docker pause` 60 s mid-evaluation), E6 (pull the
power once), F1–F4, F7, F8, G1 (dashboard cap screenshot), G6 (Codex `auth.json` on the host).
Everything else is executed by the script.
