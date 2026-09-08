# CI: verification off the workstation

Workflow: `.github/workflows/ci.yml`. Platform rule this implements: `harness-platform.md` P13
and section 9.1.

## Why this exists

The harness occupies the machine it is developed on. The container holds 4 CPUs, and the host
watchdog pauses it whenever non-container host CPU stays above `cpu_pause_high_percent`. Running
the full suite locally is exactly such a load: on 2026-09-04 a full-suite run drove the host
past the threshold and the watchdog paused the run, which is the correct behaviour and also means
**verifying the harness stops the harness**. A suite that takes half an hour and costs the
research half an hour of progress will not get run, and a suite that does not get run is not a
gate.

Moving it to GitHub-hosted runners removes the conflict. All four repos are public, so Actions
minutes are free and unmetered; the only cost is the workflow file.

## What runs

Seven jobs. `gate` runs first and most of the others wait on it, because a failure there is the
one that would stop the container from starting at all. `hygiene` and `pins` do not wait: neither
installs anything, both finish in seconds, and when one of them is the thing that broke you want
to know before the gate has finished.

| Job | What it proves | Rough time |
| --- | --- | --- |
| `gate` | The verifier hash matches its pin (K3), then the golden and canary tests G1-G20, K1-K2. This is `entrypoint.sh`'s check, run in the same order. | 32 s |
| `suite` | The full suite, sharded five ways by test file. `--durations=10` in every shard so the log carries its own balance data. | 25-87 s per shard, in parallel |
| `determinism` | Both prototype curves and every side-track artifact the catalogue declares reproduce byte for byte across two independent runs, and no scored file is created. The expected count is read from the catalogue in the job rather than written here. Invariants I5 and D5 in `SIDETRACK-AUTOMATION.md`. | 130 s |
| `image` | The Dockerfile still builds, the entrypoint gate passes **inside the image on Python 3.12**, and the harness mount is read-only (K4). | 156 s cold, less once cached |
| `pins` | The Dockerfile and `pyproject.toml` pin the same versions of the same nine packages. | 4 s |
| `hygiene` | The rules this repository states about itself: every `.ps1` is pure ASCII, `entrypoint.sh` is LF with the right shebang, `VERIFIER_FILES.txt` matches the pinned tuple, the golden-gate `-k` expression is identical in `entrypoint.sh` and both copies in this file, every `.ps1` parses, and every test tier is described in rk-overview. Checkout only, no `pip install`. | |
| `suite-evidence` | Merges the five shards' junit XML into one `preflight-junit.xml` and writes `preflight-evidence.json` beside it, so the host can reuse the suite instead of re-running it. Uploaded as the `preflight-suite` artifact. | |

Job times are measured, not estimated; the two new rows stay blank until a run fills them in.

Three of those deserve their reasons stated.

**`image` is not redundant with `suite`.** The suite runs on `setup-python` 3.12 because it is
fast; the image job runs the real container. A developer's host can be newer than the container
(3.13 against 3.12 as of this writing), and this is the job that catches something that works on
the host and breaks the runner. It is also the only check that the image still builds at all,
which nothing else covers because the harness is mounted rather than copied (P1).

**`pins` exists because the pins are written twice.** `Dockerfile` installs them and
`pyproject.toml` declares them. If they drift, the container runs one set while every local test
and every CI run uses another, and results stop being comparable across the two. They agree today;
this keeps them agreeing.

**`hygiene` is a byte scan and a parse, and they are not the same check.** `pwsh` on a runner is
PowerShell 7. It catches syntax errors, and it accepts a smart quote and an em dash that Windows
PowerShell 5.1 on the host will not parse at all. So the ASCII scan is a separate step and stays
one: the parse step would go green on exactly the file that stops `start.ps1` from running.

The gate-coverage check sits in `gate` rather than in `hygiene`, because collection needs the
package importable and `gate` has already installed the dependencies. It compares what the `-k`
expression collects against `tests/golden_gate.txt`. Recording the gate is not an argument for
widening it: every test the gate selects is time the container spends before the runner starts,
and G21-G27 and K7-K16 are outside it deliberately. A node id that disappears from that file is
either a rename or a test that stopped running before the container starts, so read the diff
rather than regenerating reflexively. One command updates it when the change is intended:

    python scripts/hygiene.py --gate-coverage --write

## What CI cannot check, and what it must not touch

CI sees a checkout. It does not see the live run, the archive, the host watchdog, Docker Desktop
on the owner's machine, or the Codex plan. Anything about *this* run's state stays with
`scripts/preflight.py` and the watcher.

**No CI in `rk-work`, `rk-findings` or `rk-overview`, deliberately.** `rk-findings` receives a push
from the host watchdog roughly every ten minutes for as long as the run is alive; a workflow there
would fire hundreds of times a day to re-check generated output that the container already refused
to publish if it failed its own guards. `rk-work` is an append-only data repo. Neither has code to
test. Putting a workflow in either would produce a permanent stream of runs and no information.
This is a design decision, not an omission.

CI also never writes to any of them: it has no credentials, and the only push-capable token in
this system stays on the host (P5).

**The workspace scripts are outside this checkout.** `start.ps1`, `stop.ps1`, `watcher.ps1`,
`stats.ps1` and `configure.py` live in the parent workspace repository. `scripts/workspace/` holds
a restorable copy of each so the workspace can be rebuilt from a clone, and those copies are only
worth anything while they are byte-identical to the ones that run. CI sees one checkout and cannot
compare them, so that check is a HOST row in `scripts/preflight.py` (HOST6), which is the one place
both trees exist. `python scripts/hygiene.py --workspace-copies=<workspace root>` runs it alone.

**The HOST section is preflight's, not CI's.** HOST1 to HOST6 cover the running watchdog and its
arguments, the freshness of `stats.txt`, the Codex usage window, the side-track ledger's
accounting, the running container's `RK_*` against `config.json`, and the workspace copies above.
None of it survives a checkout. It is also deliberately outside the gating set: a dead watchdog is
a loud FAIL and exit 0, because preflight's exit code answers whether the code is fit to run, not
whether the machine is currently running it.

## One cross-repo coupling to know about

`rk-overview` derives its published test count and its tier table by running
`pytest tests --collect-only -q` against this repo at site-build time. Before 2026-09-04 those were
hardcoded constants, and they had drifted 25 tests behind without anything noticing, which is how
a site ends up publishing a number that is quietly wrong.

The consequence for work in *this* repo: **adding or removing a `tests/test_tN_*.py` file fails the
rk-overview build** until `_SUITE_DESC` in `rk-overview/tools/generate.py` has a one-line
description for it. That is deliberate. It converts a silent stale number into a build failure with
an instruction, and it costs one line.

That coupling is now checked here as well. The `hygiene` job fetches
`tools/generate.py` from rk-overview's pushed `main` over `raw.githubusercontent.com` and asserts
that every `tests/test_tN_*.py` tier in this repo has a `_SUITE_DESC` entry. A raw fetch rather
than a second `actions/checkout`, so no token is involved and CI still lives only in the repository
it checks. Three failures are possible and the messages distinguish them: a tier with no
description, a fetch that did not return `generate.py` at all, and a `_SUITE_DESC` entry that
exists locally but has not been pushed. The last one is the one that surprises people, and it is
why the message says so. The suite's own copy of this check (C36) reads the local file instead, so
running the tests never needs a network.

It does not affect this repo, this workflow, or the container. Nothing here reads rk-overview, and
the `determinism` job deliberately points only at the prototype curves and the side-track
artifacts. The findings site is the artifact with a byte-determinism guarantee (asserted in T4);
the overview build reads the live archive and the live test count, so it is *not* byte-stable
across those changing, by design. Do not add it to a determinism check.

The same failure mode is worth watching for in prose. A hand-kept count in a document drifts the
moment someone adds a test, so exact totals belong in one place with a date on them, not scattered
through the reasoning. This file states none.

## Enabling it

The workflow is committed but does nothing until it is pushed to `main`:

    git -C rk-harness add .github/workflows/ci.yml
    git -C rk-harness commit -m "ci: run the suite off the workstation"
    git -C rk-harness push

Actions is on by default for public repositories. The first run populates the buildx cache, so the
`image` job is slowest the first time and cached afterwards. `workflow_dispatch` is enabled, so it
can also be run by hand from the Actions tab without a push.

## Rebalancing the shards, and why not to yet

The five groups were balanced by test count, which the first run confirmed is a poor proxy for
time: t1 has 332 tests and is the *fastest* shard at 25 s, while the 225 tests of
t4/t11/t12/t13 take 87 s. Sum of the shards is 275 s; slowest is 87 s.

Do not rebalance on that. The whole run finishes in 3 min 17 s and its critical path is the
`image` job at 156 s, not the suite. Moving a file between shards would shave nothing off wall
clock while the image build dominates. Revisit if the suite grows past the image build, and read
the `--durations=10` output from the slowest shard when you do; the groups are a plain matrix, so
it is a one-line edit.

The number worth keeping in view is the comparison, not the shard balance. The suite is 275 s of
runner CPU here against the twenty to forty minutes it takes on the workstation, where it is
contending with the container for the cores the watchdog is guarding. Same tests; the difference
is what else is running.

## Running the same checks locally

Nothing in CI is unavailable on the host; it is the same commands. When a local run is warranted
(before a push, or to reproduce a CI failure), the gate alone is usually enough and takes a couple
of minutes:

    python -m rk_harness.verifier_hash --check
    python -m pytest -q -k "G1_ or ... or K2_" tests

Run the whole suite locally only when there is a reason to, and expect the watchdog to pause the
container while it runs. That pause is the guard working; it is also the argument for this file.

The checkout-hygiene checks need nothing installed and take about a second:

    python scripts/hygiene.py --all

### Reusing CI's suite instead of re-running it

`scripts/preflight.py` normally runs the suite itself, which is the half hour this file exists to
avoid. When a CI run for the commit under test has finished, take its result instead:

    gh run download <run-id> -n preflight-suite -D .fullsend/
    .venv/Scripts/python.exe scripts/preflight.py --reuse-suite --docker

The artifact holds two files. `preflight-junit.xml` is the merged suite result, and preflight reads
it the same way it reads a local run. `preflight-evidence.json` is the provenance: the commit, the
ref, the run URL, the shards that were merged, the totals, and `merged_at_utc`. The report uses it
to date the suite rows separately from the host rows, so a report generated today from a suite that
ran three days ago says so on its face. Every item row in `docs/REVIEW-REPORT.md` names its origin
in an Evidence column, and the sign-off carries the report date, the suite date, and the local
checkout's commit with whether the working tree is clean.

Without the sidecar the suite rows still work and the provenance table says the provenance is
unrecorded, which is the honest reading of a junit file that arrived from nowhere in particular.

## What is deliberately not here

* **No deployment job.** The sites already deploy: the container commits into `rk-findings`, the
  host watchdog pushes, and GitHub Pages serves. Adding a deploy step would duplicate a path that
  works and put a second writer on a repo the run pushes to continuously.
* **No image publishing.** `start.ps1 -Build` builds locally in about a minute, and the image is
  not needed anywhere else. If a second machine ever runs this harness, pushing to GHCR from the
  `image` job is a five-line addition, and `harness-platform.md` section 9.1 notes it as the
  extension point.
* **No scheduled runs.** Every dependency is pinned exactly, so there is no drift for a nightly
  build to catch. The trigger is a push, because that is when the code changes.
* **No coverage gate.** The suite's discipline is golden values and canaries, not line coverage; a
  percentage would add a number nobody would act on.
