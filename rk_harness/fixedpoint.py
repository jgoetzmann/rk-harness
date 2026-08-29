"""Q15 fixed-point arithmetic — HANDOFF §4.2, SPEC ### rk_harness/fixedpoint.py.

States live in int16 with scale 2**-15. Multiply is an arithmetic shift (floors
toward negative infinity, matching ARM ASRS). Nothing saturates; every overflow
raises Q15OverflowError so it becomes a verifier rejection instead of a silent
wrong answer.
"""
from __future__ import annotations

import math

from rk_harness.types import Q15

Q15_MIN = -32768
Q15_MAX = 32767
Q15_ONE = 32768


class Q15OverflowError(Exception):
    """Raised whenever a Q15 value or intermediate leaves [-32768, 32767]."""


def _check(v: int, what: str) -> int:
    if v < Q15_MIN or v > Q15_MAX:
        raise Q15OverflowError(f"{what} {v} outside int16 [{Q15_MIN}, {Q15_MAX}]")
    return v


def q15_from_float(x: float) -> Q15:
    """round(x * 32768) half-to-even; raises on non-finite x or an out-of-range result."""
    if not math.isfinite(x):
        raise Q15OverflowError(f"non-finite input {x!r}")
    q = round(x * Q15_ONE)
    return _check(q, "q15_from_float result")


def q15_to_float(q: Q15) -> float:
    return q / 32768.0


def q15_mul(a: Q15, b: Q15) -> Q15:
    """(a*b) >> 15 with Python's arithmetic shift (floor). Inputs and result are range-checked."""
    _check(a, "q15_mul operand a")
    _check(b, "q15_mul operand b")
    r = (a * b) >> 15
    return _check(r, "q15_mul result")


def q15_add(a: Q15, b: Q15) -> Q15:
    """a + b. Inputs and result are range-checked; no wraparound, no saturation."""
    _check(a, "q15_add operand a")
    _check(b, "q15_add operand b")
    r = a + b
    return _check(r, "q15_add result")


def q15_apply(v: Q15, m: int, s: int) -> Q15:
    """Coefficient application: (v*m) >> s (floor). Raises if the result leaves int16."""
    r = (v * m) >> s
    return _check(r, "q15_apply result")
