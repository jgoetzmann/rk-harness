# P10 - Acceptance evidence that CI keeps current and the host only tops up

## The problem

`rk-harness/docs/REVIEW-REPORT.md` was generated **2026-08-30**. Since then the workspace gained
`sidetrack.py` and its T13 tier, the `fold` optimization (`archive.py:361`), `status.py` with
`tests/test_t5_status.py`, and the CI workflow. The report predates all of it, and
`preflight.py:775` writes `date.today()` into the sign-off block, so a regenerated report looks
like a dated statement about the system as it is regardless of where its evidence came from.

It is stale because it is expensive. `preflight.py:116-130` runs the whole suite locally by
default. That is exactly the load `CI.md` documents as pausing the container, so the executable
checklist is the one nobody can afford to execute.

Meanwhile CI covers the checkout well and covers the workspace's own rules not at all. Four
house rules have no automated check anywhere:

- **PowerShell must be pure ASCII** (rule 2). Nothing scans or parses a `.ps1` in CI.
- **`entrypoint.sh` must stay LF** (rule 3). A CRLF entrypoint kills the container with
  `exec: no such file or directory`.
- **The workspace scripts have versioned copies** under `rk-harness/scripts/workspace/`, whose
  own `README.md` tells you to copy them into a fresh workspace. They have drifted:

  ```
  $ diff start.ps1 rk-harness/scripts/workspace/start.ps1
  76c76 <  -CpuHigh ... 'cpu_pause_high_percent' 70   >  ... 50
  78,82c78  (copy is missing -CpuHighAvg, -CpuLowAvg,
             -CpuAvgWindowSeconds, -SaturationCheckSeconds)
  84d79     (copy is missing -AutoFreeze)
  ```

  A restore from the repo copy yields a watchdog with no rolling-average trigger and no
  saturation orchestrator.
- **The golden gate is a name filter.** `entrypoint.sh:31-33` selects
  `-k "G1_ or ... or G20_ or K1_ or K2_"`, and nothing asserts what that expression covers.

## Sketch

### Move one: preflight reuses CI's suite result

`preflight.py:120-122` already accepts `--reuse-suite` and reads
`.fullsend/preflight-junit.xml`. Have the CI `suite` job upload each shard's junit XML as an
artifact, with a small merge step. A host run then becomes:

```
download the artifact for the commit under test, drop it in place
.venv/Scripts/python.exe scripts/preflight.py --reuse-suite --docker
```

Half an hour of contended local CPU becomes a file copy plus the host-only checks.

Then make the report state its own provenance: which commit the suite result came from, when
that run happened, whether the local checkout matches it, and a sign-off block dated by the
evidence rather than by `date.today()`. A row whose evidence is a day old should say so rather
than inherit the report's generation date.

### Move two: a `hygiene` job, checkout-only, seconds long

| Check | Fails when |
| --- | --- |
| ASCII scan of every `*.ps1` | any byte above `0x7F` (rule 2, a PS 5.1 parse error) |
| `[ScriptBlock]::Create` per script | a syntax error, without executing anything |
| byte scan of `entrypoint.sh` for `\r` | rule 3 |
| diff the four workspace scripts against `scripts/workspace/` | the drift above |
| parse `-k` out of `entrypoint.sh`, collect the matching tests | the gate no longer covers a declared list of behaviours |
| `VERIFIER_FILES` against a checked-in list | a pinned file is added or dropped without anyone noticing it is an epoch boundary |
| every `tests/test_tN_*.py` has an entry in rk-overview's `_SUITE_DESC` | the cross-repo coupling `CI.md` describes, caught in CI instead of on the host |

Two notes. The gate-coverage check does **not** widen the gate; widening the `-k` filter is a
separate decision, and the check exists so the decision is visible. The `_SUITE_DESC` check
reaches into `rk-overview`, which deliberately has no workflow of its own, so do it as a
read-only checkout inside rk-harness's workflow and the rule that CI lives in the code repo
still holds.

### Move three: a HOST section in preflight

For what only the host can see, and what has changed since August:

- the watchdog is running, and its arguments match `config.json` (`start.ps1:76-88` passes them
  once at start, so a config edit without `--apply` changes nothing);
- `stats.txt` is inside its deadline, via P8's `--age`;
- the codex rate-limit snapshot is not older than its own `window_minutes`;
- `sidetrack.JOBS` still sums to its pinned total, and no side-track artifact has been written
  into a scored path;
- the running container's `RK_*` environment matches `config.json`, which the watcher shows and
  nothing asserts.

## Cost

Moderate: mostly CI YAML plus about 150 lines of preflight. No epoch boundary. The hygiene job
is worth doing on its own even if the artifact plumbing is deferred.

## Prerequisites

The workflow being live. `CI.md` reports measured per-job times and shard durations, so it has
run. P8's `--age` for one HOST row.

## Risks

- **Mixed provenance.** A report that blends fresh host checks with a day-old suite artifact is
  the same lie in a new place unless every row carries its source. Get that part right first.
- **`pwsh` on a runner is PowerShell 7,** not the 5.1 the host runs. It catches syntax errors
  and not every 5.1-specific parse failure, which is why the ASCII scan stays a separate check.
- **A gate-coverage check invites widening the gate,** which lengthens container start. Keep the
  check and the decision apart.
- **The cross-repo check must live in the code repo,** never as a workflow in `rk-overview`.
