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

## A2 — the PAT is over-scoped (found 2026-08-29, still open)

`scripts/check_pat.ps1` now runs two probes. The HANDOFF's literal one (PATCH the description)
returns 403 — but only because the token lacks the *Administration* permission. The intent
probe (`git push --dry-run`, which never updates a ref) shows the token **can push to
rk-harness**. Re-issue the fine-grained PAT with *Only select repositories* = rk-work, rk-findings
and *Contents: Read and write*; A2 stays FAIL until both probes pass.

## G6 — Codex in the container

`RK_LLM=codex` makes the runner call `codex exec --skip-git-repo-check --sandbox read-only`
with the mounted `/root/.codex/auth.json`; the image installs `@openai/codex@0.151.0`, the
egress allowlist gains `chatgpt.com` and `auth.openai.com`, and `run.ps1` defaults to codex
when `auth.json` exists. The pre-flight G6 item runs `codex login status` inside the container.

## Items that need the host or a human

A2 (fine-grained PAT in `.env`, then `scripts/check_pat.ps1`), A11 (`scripts/network.sh` in
WSL, then curl from the container), E4 (`docker pause` 60 s mid-evaluation), E6 (pull the
power once), F1–F4, F7, F8, G1 (dashboard cap screenshot), G6 (Codex `auth.json` on the host).
Everything else is executed by the script.
