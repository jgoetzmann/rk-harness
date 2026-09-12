"""Trace hash — the second pin, modelled on verifier_hash.py and deliberately separate.

sha256 over the concatenation, in this exact order, of the two files that produce
`rk-work/trace/results.json`: the tracer and this module. The compiled-and-traced
comparison is only worth reading if the code that produced it is fixed, so it gets the
same guarantee the scorer has.

It is a SECOND pin rather than two more entries in `VERIFIER_FILES` on purpose. That
tuple's sha256 is `VERIFIER_HASH`, every archived record carries the hash that scored
it, and moving it invalidates every score in the archive. That is an epoch boundary
(CLAUDE.md hard rule 1), and the trace measures the cost model rather than changing it,
so it has no business charging one. Nothing here is read by the scorer, the search or
the site build.
"""
from __future__ import annotations

import hashlib
import sys

from rk_harness.paths import HARNESS_DIR

TRACE_FILES: tuple[str, ...] = (
    "rk_harness/tracecheck.py",
    "rk_harness/trace_hash.py",
)

PIN_FILE = HARNESS_DIR / "TRACE_HASH"


def compute_trace_hash() -> str:
    h = hashlib.sha256()
    for rel in TRACE_FILES:
        with open(HARNESS_DIR / rel, "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()


def pinned_trace_hash() -> str | None:
    try:
        text = PIN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def matches() -> bool:
    """True iff a pinned value exists and equals the computed one."""
    pinned = pinned_trace_hash()
    return pinned is not None and pinned == compute_trace_hash()


def check_or_exit() -> None:
    """Exit 1, loudly, unless the computed hash equals the pinned one."""
    pinned = pinned_trace_hash()
    actual = compute_trace_hash()
    if pinned is None:
        print("TRACE HASH: no pinned value in TRACE_HASH — refusing to run", file=sys.stderr)
        sys.exit(1)
    if pinned != actual:
        print("TRACE HASH MISMATCH — refusing to run", file=sys.stderr)
        print(f"  pinned:   {pinned}", file=sys.stderr)
        print(f"  computed: {actual}", file=sys.stderr)
        sys.exit(1)
    print(f"trace hash ok: {actual}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--pin"]:
        value = compute_trace_hash()
        PIN_FILE.write_text(value + "\n", encoding="utf-8")
        print(value)
        return 0
    if args == ["--check"]:
        check_or_exit()
        return 0
    print(compute_trace_hash())
    return 0


if __name__ == "__main__":
    sys.exit(main())
