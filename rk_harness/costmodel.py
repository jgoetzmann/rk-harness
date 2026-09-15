"""Analytic cycle-cost model — HANDOFF §4.5, SPEC ### rk_harness/costmodel.py.

Three cost models, a per-coefficient cost, a per-tableau cycle count, an
assembly-line counter for the fixtures/known_sequence.s cross-check, and a
reference C emitter that is a string for human review only and is never
executed.

Per-coefficient pricing (DECISIONS D45): under the two M0+ models each
coefficient costs what ``arm-none-eabi-gcc 13.2.1`` at ``tracecheck.CFLAGS``
emits for exactly the term ``_c_term`` writes, counted by op class from the
pinned fixture ``fixtures/m0plus_coeff_ops.json``. Under ``avr_approx`` each
coefficient costs the cheaper of a shift-add chain and a hardware multiply,
driven by CSD weight (never by the denominator).
"""
from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

from rk_harness.coeffrep import to_rep
from rk_harness.types import CostModel, Tableau

M0PLUS_FAST = CostModel("m0plus_fast", {"mul": 1, "add": 1, "shift": 1, "load": 2, "store": 2})
M0PLUS_SLOW = CostModel("m0plus_slow", {"mul": 32, "add": 1, "shift": 1, "load": 2, "store": 2})
AVR_APPROX = CostModel("avr_approx", {"mul": 14, "add": 2, "shift": 8, "load": 2, "store": 2})

COST_MODELS: dict[str, CostModel] = {
    M0PLUS_FAST.name: M0PLUS_FAST,
    M0PLUS_SLOW.name: M0PLUS_SLOW,
    AVR_APPROX.name: AVR_APPROX,
}

PRIMARY_MODELS: tuple[CostModel, CostModel] = (M0PLUS_FAST, M0PLUS_SLOW)

_ONE = Fraction(1)
_MINUS_ONE = Fraction(-1)
_ZERO = Fraction(0)

_MNEMONIC_CLASS: dict[str, str] = {
    "LDR": "load",
    "STR": "store",
    "MULS": "mul",
    "ASRS": "shift",
    "LSLS": "shift",
    "LSRS": "shift",
    "ADDS": "add",
    "SUBS": "add",
}

_M0PLUS_OPS_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "m0plus_coeff_ops.json"
_M0PLUS_OPS_OFFSET = 32767
_m0plus_ops_cache: dict[str, list] | None = None


def _m0plus_ops() -> dict[str, list]:
    """Pinned per-multiplier op-class counts, loaded lazily once (D45).

    Returns {"shifted": [...], "unshifted": [...]} where each list is indexed
    by m + 32767 and each slot holds a (mul, shift, add, load) tuple or None
    where the multiplier is outside that array's domain. Reads only the pinned
    fixture through Path(__file__), so pinned code imports nothing unpinned.
    """
    global _m0plus_ops_cache
    if _m0plus_ops_cache is None:
        doc = json.loads(_M0PLUS_OPS_PATH.read_text(encoding="utf-8"))
        tuples = [tuple(t) for t in doc["tuples"]]
        _m0plus_ops_cache = {
            key: [None if i is None else tuples[i] for i in doc[key]]
            for key in ("shifted", "unshifted")
        }
    return _m0plus_ops_cache


def _m0plus_table_cost(r, cyc: dict[str, int]) -> int:
    """Price one coefficient representation from the pinned table (D45).

    `r` is a CoeffRep with r.m != 0. The shifted array covers every odd m
    (to_rep returns odd m whenever s > 0); the unshifted array covers every m
    with 2 <= |m| <= 32767. A multiplier outside both domains raises KeyError,
    so the rule fails closed.
    """
    ops = _m0plus_ops()
    key = "shifted" if r.s > 0 else "unshifted"
    slot = r.m + _M0PLUS_OPS_OFFSET
    entry = ops[key][slot] if 0 <= slot < len(ops[key]) else None
    if entry is None:
        raise KeyError(f"coefficient multiplier m={r.m} s={r.s} has no {key} table entry")
    mul, shift, add, load = entry
    return mul * cyc["mul"] + shift * cyc["shift"] + add * cyc["add"] + load * cyc["load"]


def coeff_cost(x: Fraction, model: CostModel) -> int:
    """Cycles to apply one coefficient to one Q15 value under `model`.

    0 -> 0 (term omitted); +-1 -> 0 (copy, sign folds into ADDS/SUBS);
    a representation with m == 0 -> 0 (|x| below 2**-20: nothing to apply);
    s == 0 and |m| == 1 -> 0 (D45 corner: M = +-1 emits a bare adds/subs,
    which is the combination add already).
    Otherwise under the two M0+ models the pinned per-multiplier table price
    (D45), and under avr_approx min(csd_cost, mul_cost) with
    w = to_rep(x).csd_weight (unchanged).
    """
    x = Fraction(x)
    if x == _ZERO or x == _ONE or x == _MINUS_ONE:
        return 0
    cyc = model.cycles
    if model.name != AVR_APPROX.name:
        r = to_rep(x)
        if r.m == 0:
            # Representation collapsed to m == 0: nothing to shift-add.
            return 0
        if r.s == 0 and abs(r.m) == 1:
            return 0
        return _m0plus_table_cost(r, cyc)
    w = to_rep(x).csd_weight
    if w <= 0:
        # Representation collapsed to m == 0 (|x| below 2**-20): nothing to shift-add.
        return 0
    csd_cost = w * cyc["shift"] + (w - 1) * cyc["add"]
    mul_cost = cyc["mul"] + cyc["shift"]
    return csd_cost if csd_cost < mul_cost else mul_cost


def _combination_cost(coeffs: tuple[Fraction, ...], model: CostModel) -> int:
    """load + sum(coeff_cost + add over nonzero coeffs) + store, for one state."""
    cyc = model.cycles
    total = cyc["load"] + cyc["store"]
    add = cyc["add"]
    for a in coeffs:
        if a != 0:
            total += coeff_cost(a, model) + add
    return total


def cycle_count(t: Tableau, model: CostModel, n_states: int) -> int:
    """Analytic cycles per step for tableau `t` under `model` with `n_states` states.

    Per stage whose A row has any nonzero entry: one load, then coefficient
    cost plus one add per nonzero entry, then one store. The final combination
    over b is counted identically (all-zero b still costs load + store).
    Derivative evaluation is excluded. Scales linearly in n_states.
    """
    per_state = 0
    for row in t.A:
        if any(a != 0 for a in row):
            per_state += _combination_cost(tuple(row), model)
    per_state += _combination_cost(tuple(t.b), model)
    return per_state * int(n_states)


def count_sequence(ops: list[str], model: CostModel) -> int:
    """Cycle count for a literal list of ARMv6-M assembly lines.

    Each element is one line; everything from `@` onward is a comment; blank
    lines cost nothing; the mnemonic is the first whitespace token, uppercased.
    Unknown mnemonic -> ValueError.
    """
    cyc = model.cycles
    total = 0
    for raw in ops:
        line = raw.split("@", 1)[0].strip()
        if not line:
            continue
        mnemonic = line.split()[0].upper()
        cls = _MNEMONIC_CLASS.get(mnemonic)
        if cls is None:
            raise ValueError(f"unknown mnemonic {mnemonic!r} in line {raw!r}")
        total += cyc[cls]
    return total


def _c_term(dst: str, src: str, x: Fraction) -> list[str]:
    """C lines accumulating coefficient x times src into int32 dst."""
    if x == _ONE:
        return [f"        {dst} += (int32_t){src};"]
    if x == _MINUS_ONE:
        return [f"        {dst} -= (int32_t){src};"]
    r = to_rep(x)
    tag = "exact" if r.exact else "approx"
    return [
        f"        /* {x.numerator}/{x.denominator} ~ {r.m} / 2^{r.s} ({tag}, csd weight {r.csd_weight}) */",
        f"        {dst} += ((int32_t){src} * (int32_t){r.m}) >> {r.s};",
    ]


def emit_c(t: Tableau, n_states: int) -> str:
    """Reference C for one Q15 RK step: void rk_step(int16_t *y, int16_t h_q).

    Uses int32 intermediates and `>> 15` for the Q15 product with h_q. Exists so
    a human can cross-check the analytic model against real compiler output by
    hand; the harness never compiles or runs it.
    """
    s = len(t.b)
    out: list[str] = []
    out.append("/* Reference C for the analytic cost model (HANDOFF 4.5). Never executed by the harness. */")
    out.append("#include <stdint.h>")
    out.append("")
    out.append(f"#define N_STATES {int(n_states)}")
    out.append(f"#define N_STAGES {s}")
    out.append("")
    out.append("/* Q15 derivative dy = f(y), supplied by the application. */")
    out.append("extern void rk_rhs(const int16_t *y, int16_t *dy);")
    out.append("")
    out.append("void rk_step(int16_t *y, int16_t h_q)")
    out.append("{")
    out.append("    int16_t hk[N_STAGES][N_STATES];")
    out.append("    int16_t acc[N_STATES];")
    out.append("    int16_t k[N_STATES];")
    out.append("    int32_t tmp;")
    out.append("    int m;")
    out.append("")
    for i in range(s):
        row = t.A[i]
        nonzero = [(j, row[j]) for j in range(len(row)) if row[j] != 0]
        out.append(f"    /* stage {i}: c = {t.c[i].numerator}/{t.c[i].denominator} */")
        if nonzero:
            out.append("    for (m = 0; m < N_STATES; m++) {")
            out.append("        tmp = (int32_t)y[m];                     /* load */")
            for j, a in nonzero:
                out.extend(_c_term("tmp", f"hk[{j}][m]", a))
            out.append("        acc[m] = (int16_t)tmp;                   /* store */")
            out.append("    }")
            out.append("    rk_rhs(acc, k);")
        else:
            out.append("    /* row of A is all zero: stage input is y itself */")
            out.append("    rk_rhs(y, k);")
        out.append("    for (m = 0; m < N_STATES; m++) {")
        out.append(f"        hk[{i}][m] = (int16_t)(((int32_t)k[m] * (int32_t)h_q) >> 15);")
        out.append("    }")
        out.append("")
    out.append("    /* final combination over b */")
    out.append("    for (m = 0; m < N_STATES; m++) {")
    out.append("        tmp = (int32_t)y[m];                     /* load */")
    for i in range(s):
        if t.b[i] != 0:
            out.extend(_c_term("tmp", f"hk[{i}][m]", t.b[i]))
    out.append("        y[m] = (int16_t)tmp;                     /* store */")
    out.append("    }")
    out.append("}")
    out.append("")
    return "\n".join(out)
