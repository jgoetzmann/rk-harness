#!/bin/sh
# Container entrypoint — HANDOFF §13.1. Hand-written (HANDOFF §16.1).
# 1. Recompute the verifier hash, compare to the pinned value; mismatch -> exit 1 (K3).
# 2. Run golden tests G1-G20 and canaries K1-K2; any failure -> exit 1.
# 3. Only then start the runner.
set -eu

HARNESS="${RK_HARNESS_DIR:-/harness}"
cd "$HARNESS"

# K4: the harness mount must be read-only.
if touch "$HARNESS/.rw-probe" 2>/dev/null; then
  rm -f "$HARNESS/.rw-probe"
  echo "FATAL: $HARNESS is writable; mount it read-only (:ro)" >&2
  exit 1
fi

python -m rk_harness.verifier_hash --check || exit 1

python -m pytest -q -p no:cacheprovider -o cache_dir=/tmp/pytest-cache \
  -k "G1_ or G2_ or G3_ or G4_ or G5_ or G6_ or G7_ or G8_ or G9_ or G10_ or G11_ or G12_ or G13_ or G14_ or G15_ or G16_ or G17_ or G18_ or G19_ or G20_ or K1_ or K2_" \
  tests || { echo "FATAL: golden/canary tests failed; refusing to run" >&2; exit 1; }

exec python -m rk_harness.runner "$@"
