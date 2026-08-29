"""Verifier hash — HANDOFF §4.11. Hand-written (HANDOFF §16.1).

sha256 over the concatenation, in this exact order, of ten files. A change to any of
them silently changes every score in the archive, so the pinned value is checked at
container start (test K3) and every record carries the hash that produced it.
"""
from __future__ import annotations

import hashlib
import sys

from rk_harness.paths import HARNESS_DIR

VERIFIER_FILES: tuple[str, ...] = (
    "rk_harness/coeffrep.py",
    "rk_harness/orderconditions.py",
    "rk_harness/verifier.py",
    "rk_harness/costmodel.py",
    "rk_harness/evaluator.py",
    "rk_harness/problems.py",
    "fixtures/classical.json",
    "fixtures/problems.json",
    "fixtures/q15.json",
    "fixtures/known_sequence.s",
)

PIN_FILE = HARNESS_DIR / "VERIFIER_HASH"


def compute_verifier_hash() -> str:
    h = hashlib.sha256()
    for rel in VERIFIER_FILES:
        with open(HARNESS_DIR / rel, "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()


def pinned_verifier_hash() -> str | None:
    try:
        text = PIN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def check_or_exit() -> None:
    """Exit 1, loudly, unless the computed hash equals the pinned one (K3)."""
    pinned = pinned_verifier_hash()
    actual = compute_verifier_hash()
    if pinned is None:
        print("VERIFIER HASH: no pinned value in VERIFIER_HASH — refusing to run", file=sys.stderr)
        sys.exit(1)
    if pinned != actual:
        print("VERIFIER HASH MISMATCH — refusing to run", file=sys.stderr)
        print(f"  pinned:   {pinned}", file=sys.stderr)
        print(f"  computed: {actual}", file=sys.stderr)
        sys.exit(1)
    print(f"verifier hash ok: {actual}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--pin"]:
        value = compute_verifier_hash()
        PIN_FILE.write_text(value + "\n", encoding="utf-8")
        print(value)
        return 0
    if args == ["--check"]:
        check_or_exit()
        return 0
    print(compute_verifier_hash())
    return 0


if __name__ == "__main__":
    sys.exit(main())
