"""Compiled, traced and TRM-priced cycle check for the analytic cost model.

The cost model in `costmodel.py` is analytic: it derives an instruction sequence from a
tableau's sparsity pattern and each coefficient's CSD weight, and prices it. Nothing in the
harness has ever compiled that sequence or executed it. This module does, on the one
question that can be answered without hardware: does a real compiler, emitting a real Q15
step for a real tableau, produce the instruction mix the analytic model assumes?

The method has two stages, because no free tool is cycle accurate for Cortex-M0+:

1. Get a real executed-instruction trace. `costmodel.emit_c` already emits a freestanding
   Q15 `rk_step` in C; it is imported and its output is used verbatim. arm-none-eabi-gcc
   compiles it for cortex-m0plus, the unicorn engine executes it, and every executed
   address is mapped back to a mnemonic and a source line through arm-none-eabi-objdump.
2. Price that histogram offline against the Cortex-M0+ TRM table, through the same
   mnemonic-to-class mapping `costmodel` uses, so both sides are priced by one table.

The emulator is instruction accurate and NOT cycle accurate. Nothing here measures time on
any physical part; it measures which instructions run, and prices them by a published table
under stated assumptions.

Correctness comes before any timing number, so the C is checked against the pinned Python
evaluator before it is trusted. That check is exact and it is total: the final int16 state,
every stage input, and the overflow verdict. The pinned primitives range-check every operand
and every result and raise; nothing wraps and nothing saturates. A C port on natural int16
wraparound agrees on clean cases and diverges on exactly the cases that matter, so a second,
checked entry point mirrors the Python primitive for primitive and traps at the same
operation index. The gate fails closed: a missing toolchain is a failure, not a warning.

Run it: `python -m rk_harness.tracecheck`. Output: `rk-work/trace/results.json` plus the
generated C under `rk-work/trace/c/`. This module and its pin are covered by TRACE_HASH,
never by VERIFIER_HASH: see `rk_harness/trace_hash.py` and DECISIONS.md D42.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from rk_harness import trace_hash
from rk_harness.coeffrep import to_rep
from rk_harness.costmodel import _MNEMONIC_CLASS as PINNED_MNEMONIC_CLASS
from rk_harness.costmodel import PRIMARY_MODELS, cycle_count, emit_c
from rk_harness.fixedpoint import (
    Q15OverflowError,
    q15_add,
    q15_apply,
    q15_from_float,
    q15_mul,
)
from rk_harness.paths import archive_dir, work_dir
from rk_harness.problems import DERIV_SCALE, PROBLEMS
from rk_harness.tableau import classical, content_hash, from_json
from rk_harness.types import CostModel, Tableau
from rk_harness.verifier_hash import pinned_verifier_hash


class TraceCheckError(Exception):
    """Any failure of the compile, execute, attribute or cross-check chain."""


# --------------------------------------------------------------------------- configuration

DEFAULT_METHODS: tuple[str, ...] = ("euler", "midpoint", "heun2", "rk4", "rk38", "11e898cb")

#: Compiler flags. Fixed, and recorded in the output document.
CFLAGS: tuple[str, ...] = ("-mcpu=cortex-m0plus", "-mthumb", "-O2", "-ffreestanding", "-nostdlib")
#: -g is needed for the line table the region attribution reads; it emits no instructions.
DEBUG_FLAGS: tuple[str, ...] = ("-g",)

#: Cases run through both the Python evaluator and the compiled step. Deterministic, fixed.
#: Step counts are above t_end on both problems, because h_q is q15_from_float(h) and a step
#: of 1.0 or more has no Q15 representation. An overflow probe drives h_q to the rail so the
#: trap contract is exercised rather than assumed; it is a contract probe, not an integration
#: any of these methods would run.
CASES: tuple[dict, ...] = (
    {"id": "dahlquist_48", "problem": "dahlquist", "steps": 48, "kind": "solve"},
    {"id": "dahlquist_96", "problem": "dahlquist", "steps": 96, "kind": "solve"},
    {"id": "dahlquist_144", "problem": "dahlquist", "steps": 144, "kind": "solve"},
    {"id": "damped_osc_48", "problem": "damped_osc", "steps": 48, "kind": "solve"},
    {"id": "damped_osc_96", "problem": "damped_osc", "steps": 96, "kind": "solve"},
    {"id": "damped_osc_144", "problem": "damped_osc", "steps": 144, "kind": "solve"},
    {"id": "dahlquist_rail", "problem": "dahlquist", "steps": 2, "kind": "overflow_probe",
     "y0": (32000,), "h_q": 32767},
    {"id": "damped_osc_rail", "problem": "damped_osc", "steps": 2, "kind": "overflow_probe",
     "y0": (32000, -32000), "h_q": 32767},
)

#: The runs the histogram is read from. A small state and a small step keep every method's
#: arithmetic well inside the rails, which matters because euler is unstable on damped_osc at
#: any step this budget would use and would overflow rather than finish. Nothing is lost by
#: that: rk_step is straight-line code with no data-dependent branch, so which instructions
#: execute does not depend on the values, and LINEARITY_STEPS is what checks that claim
#: rather than assuming it.
TRACE_CASES: tuple[dict, ...] = (
    {"id": "trace_n1", "problem": "dahlquist", "y0": (1000,), "h_q": 2048},
    {"id": "trace_n2", "problem": "damped_osc", "y0": (1000, 500), "h_q": 2048},
)

#: Step counts whose rk_step instruction totals must stand in exact proportion. Straight-line
#: code with no data-dependent branch executes the same instructions every step; if that ever
#: stops being true the histogram is not a per-step quantity and the gate says so.
LINEARITY_STEPS: tuple[int, int] = (4, 12)

K_CAP = 2048                     # recorded derivative and stage-input slots per run
IMAGE_BASE = 0x00000000
IMAGE_SIZE = 0x00010000
STACK_BASE = 0x20000000
STACK_SIZE = 0x00010000


# --------------------------------------------------------------------------- TRM pricing

#: Cortex-M0+ TRM assumptions. Every one of these is an assumption about the part, not a
#: measurement of it, and the output document states them where the numbers are read.
ASSUMPTIONS: dict[str, object] = {
    "flash": "zero wait state",
    "bus": "no contention",
    "interrupts": "none",
    "branch_taken_cycles": 3,
    "branch_not_taken_cycles": 1,
    "load_cycles": 2,
    "store_cycles": 2,
    "muls_cycles_fast_variant": 1,
    "muls_cycles_small_variant": 32,
    "data_processing_cycles": 1,
    "bl_cycles": 4,
    "multi_register_cycles": (
        "1 + N for N registers, plus 2 more when the register list contains PC, "
        "which is the same pipeline refill that makes a taken branch cost 3"
    ),
}

#: Mnemonics the compiler emits that the pinned five-class mapping does not carry. The
#: pinned mapping covers the ten-line fixture the harness cross-checks against; real
#: compiler output is wider. Shared mnemonics keep the pinned class and the pinned price.
TRACE_MNEMONIC_CLASS: dict[str, str] = {
    # loads and stores of every width
    "LDRSH": "load", "LDRH": "load", "LDRB": "load", "LDRSB": "load",
    "STRH": "store", "STRB": "store",
    # single-cycle data processing and moves
    "MOV": "alu", "MOVS": "alu", "ADD": "alu", "SUB": "alu", "ADR": "alu",
    "NEGS": "alu", "RSBS": "alu", "CMP": "alu", "CMN": "alu", "TST": "alu",
    "SXTH": "alu", "SXTB": "alu", "UXTH": "alu", "UXTB": "alu",
    "ANDS": "alu", "ORRS": "alu", "EORS": "alu", "BICS": "alu", "MVNS": "alu",
    "ADCS": "alu", "SBCS": "alu", "RORS": "alu", "NOP": "alu",
    "LSL": "alu", "LSR": "alu", "ASR": "alu", "MULS.W": "mul",
    # multi-register transfers
    "PUSH": "multi", "POP": "multi", "STM": "multi", "STMIA": "multi",
    "LDM": "multi", "LDMIA": "multi",
    # control flow
    "B": "branch", "BX": "branch", "BL": "call", "BLX": "call",
}

_CONDITIONS = ("EQ", "NE", "CS", "HS", "CC", "LO", "MI", "PL", "VS", "VC",
               "HI", "LS", "GE", "LT", "GT", "LE", "AL")


def mnemonic_class(mnemonic: str) -> str:
    """Class for one executed mnemonic. Unknown mnemonic raises, so pricing cannot guess."""
    m = mnemonic.upper()
    if m.endswith(".N") or m.endswith(".W"):
        m = m[:-2]
    if m in PINNED_MNEMONIC_CLASS:
        return PINNED_MNEMONIC_CLASS[m]
    if m in TRACE_MNEMONIC_CLASS:
        return TRACE_MNEMONIC_CLASS[m]
    if m.startswith("B") and m[1:] in _CONDITIONS:
        return "branch"
    raise TraceCheckError(f"unknown mnemonic {mnemonic!r}: refusing to price it")


def instruction_cycles(mnemonic: str, operands: str, model: CostModel, taken: bool) -> int:
    """TRM cycles for one executed instruction under `model`.

    load, store, mul, shift and add are priced straight out of the cost model's own table,
    so the traced and the analytic side cannot disagree about what a multiply costs.
    """
    cls = mnemonic_class(mnemonic)
    if cls in model.cycles:
        return int(model.cycles[cls])
    if cls == "alu":
        return int(ASSUMPTIONS["data_processing_cycles"])
    if cls == "call":
        return int(ASSUMPTIONS["bl_cycles"])
    if cls == "branch":
        return int(ASSUMPTIONS["branch_taken_cycles"] if taken
                   else ASSUMPTIONS["branch_not_taken_cycles"])
    if cls == "multi":
        regs = _register_count(operands)
        extra = 2 if _has_pc(operands) else 0
        return 1 + regs + extra
    raise TraceCheckError(f"unpriced class {cls!r} for {mnemonic!r}")


def _register_count(operands: str) -> int:
    inner = operands[operands.find("{") + 1:operands.rfind("}")] if "{" in operands else ""
    n = 0
    for part in inner.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            n += max(1, _reg_index(hi) - _reg_index(lo) + 1)
        else:
            n += 1
    return n


def _reg_index(name: str) -> int:
    name = name.strip().lower()
    if name.startswith("r") and name[1:].isdigit():
        return int(name[1:])
    return {"sp": 13, "lr": 14, "pc": 15}.get(name, 0)


def _has_pc(operands: str) -> bool:
    return "pc" in operands.lower()


# --------------------------------------------------------------------------- C generation

_REGION_MODEL_SCOPE: tuple[str, ...] = ("load", "coeff", "store")
_REGION_ORDER: tuple[str, ...] = ("load", "coeff", "store", "hscale", "rhs_call",
                                  "loop", "frame", "decl", "other")


def classify_step_source(src: str) -> dict[int, str]:
    """Map every 1-based line of `emit_c` output to the region of work it belongs to.

    The analytic model prices the combination only: one load, the coefficient terms, one
    add each and one store, per stage row with any nonzero entry and once for b. It prices
    no derivative call, no h times k scaling, no loop control and no stack frame. Those are
    separated here so the comparison can be made at a matched scope as well as whole step.

    An unrecognised line raises: `costmodel.emit_c` is pinned, and if its shape ever moves
    this attribution must be re-read rather than silently mis-bucketed.
    """
    regions: dict[int, str] = {}
    for lineno, line in enumerate(src.splitlines(), 1):
        s = line.strip()
        indent = len(line) - len(line.lstrip())
        if not s:
            region = "other"
        elif s.startswith("/*"):
            region = "other"
        elif s.startswith("#") or s.startswith("extern "):
            region = "other"
        elif s.startswith("void rk_step"):
            region = "frame"
        elif s == "{" and indent == 0:
            region = "frame"
        elif s == "}" and indent == 0:
            region = "frame"
        elif s == "}":
            region = "loop"
        elif s.startswith("for (m = 0;"):
            region = "loop"
        elif s.startswith(("int16_t ", "int32_t ")) or s == "int m;":
            region = "decl"
        elif "rk_rhs(" in s:
            region = "rhs_call"
        elif s.startswith("hk["):
            region = "hscale"
        elif s.startswith("tmp = (int32_t)y[m]"):
            region = "load"
        elif s.startswith(("tmp +=", "tmp -=")):
            region = "coeff"
        elif s.startswith("acc[m] = (int16_t)tmp") or s.startswith("y[m] = (int16_t)tmp"):
            region = "store"
        else:
            raise TraceCheckError(f"unclassified emit_c line {lineno}: {line!r}")
        regions[lineno] = region
    return regions


def _reps(t: Tableau):
    """(A, b) coefficient representations, None where the entry is zero, as solve_q15 takes them."""
    reps_A = [[(to_rep(x) if x != 0 else None) for x in row] for row in t.A]
    reps_b = [(to_rep(x) if x != 0 else None) for x in t.b]
    return reps_A, reps_b


def driver_c(t: Tableau, n_states: int) -> str:
    """The tracer's own C: the derivative stub, the checked step, and the entry point.

    The derivative stub replays derivative values recorded from the pinned Python evaluator
    and verifies the stage input it was handed, because the cost model prices no derivative
    and a float right-hand side has no place in a Q15 instruction trace. It is `noinline`
    so it keeps its own address range and stays out of the histogram.

    `rk_step_checked` mirrors `simulate.solve_q15` primitive for primitive, including the
    range check on every operand and every result, and reports the index of the operation
    that failed. `rk_step`, the one that is traced, is `emit_c` output compiled as its own
    translation unit, so nothing here can be inlined into it.
    """
    s = len(t.b)
    reps_A, reps_b = _reps(t)
    out: list[str] = []
    a = out.append
    a("/* Generated by rk_harness/tracecheck.py. Pinned by TRACE_HASH, never by VERIFIER_HASH. */")
    a("#include <stdint.h>")
    a("")
    a(f"#define N_STATES {int(n_states)}")
    a(f"#define N_STAGES {s}")
    a(f"#define K_CAP {K_CAP}")
    a("")
    a("void rk_step(int16_t *y, int16_t h_q);")
    a("")
    a("/* Recorded from the pinned Python evaluator, written into memory before the run. */")
    a("int16_t k_table[K_CAP];")
    a("int16_t acc_table[K_CAP];")
    a("int32_t k_index;")
    a("int32_t acc_mismatch;")
    a("int32_t k_overrun;")
    a("")
    a("int16_t g_y[N_STATES];")
    a("int16_t g_hq;")
    a("int32_t g_steps;")
    a("int32_t g_mode;")
    a("int32_t g_trap;")
    a("int32_t g_ops;")
    a("int32_t g_shift_ok;")
    a("int32_t g_stopped;")
    a("")
    a("static int32_t rk_op;")
    a("static int32_t rk_trap;")
    a("")
    a("__attribute__((noinline, noclone))")
    a("void rk_rhs(const int16_t *y, int16_t *dy)")
    a("{")
    a("    int m;")
    a("    for (m = 0; m < N_STATES; m++) {")
    a("        if (k_index + m >= K_CAP) { k_overrun = 1; dy[m] = 0; continue; }")
    a("        if (y[m] != acc_table[k_index + m]) acc_mismatch = k_index + m + 1;")
    a("        dy[m] = k_table[k_index + m];")
    a("    }")
    a("    k_index += N_STATES;")
    a("}")
    a("")
    a("/* The pinned Q15 primitives range-check operands and results and raise. Nothing")
    a("   wraps and nothing saturates, so the C has to trap where they raise. */")
    a("static int ck(int32_t v) { return (v < -32768 || v > 32767); }")
    a("")
    a("int rk_step_checked(int16_t *y, int16_t h_q)")
    a("{")
    a("    int16_t hk[N_STAGES][N_STATES];")
    a("    int16_t acc[N_STATES];")
    a("    int16_t yn[N_STATES];")
    a("    int16_t k[N_STATES];")
    a("    int32_t r, va, vb;")
    a("    int m;")
    a("")
    for i in range(s):
        row = reps_A[i]
        nonzero = [(j, row[j]) for j in range(len(row)) if row[j] is not None]
        a(f"    /* stage {i} */")
        a("    for (m = 0; m < N_STATES; m++) acc[m] = y[m];")
        for j, rep in nonzero:
            a("    for (m = 0; m < N_STATES; m++) {")
            a("        rk_op++;                                  /* q15_apply */")
            a(f"        r = ((int32_t)hk[{j}][m] * (int32_t){rep.m}) >> {rep.s};")
            a("        if (ck(r)) { rk_trap = rk_op; return 1; }")
            a("        rk_op++;                                  /* q15_add */")
            a("        va = (int32_t)acc[m];")
            a("        if (ck(va) || ck(r) || ck(va + r)) { rk_trap = rk_op; return 1; }")
            a("        acc[m] = (int16_t)(va + r);")
            a("    }")
        a("    rk_rhs(acc, k);")
        a("    for (m = 0; m < N_STATES; m++) {")
        a("        rk_op++;                                  /* q15_mul */")
        a("        va = (int32_t)k[m];")
        a("        vb = (int32_t)h_q;")
        a("        if (ck(va) || ck(vb)) { rk_trap = rk_op; return 1; }")
        a("        r = (va * vb) >> 15;")
        a("        if (ck(r)) { rk_trap = rk_op; return 1; }")
        a(f"        hk[{i}][m] = (int16_t)r;")
        a("    }")
        a("")
    a("    /* final combination over b */")
    a("    for (m = 0; m < N_STATES; m++) yn[m] = y[m];")
    for i in range(s):
        rep = reps_b[i]
        if rep is None:
            continue
        a("    for (m = 0; m < N_STATES; m++) {")
        a("        rk_op++;                                  /* q15_apply */")
        a(f"        r = ((int32_t)hk[{i}][m] * (int32_t){rep.m}) >> {rep.s};")
        a("        if (ck(r)) { rk_trap = rk_op; return 1; }")
        a("        rk_op++;                                  /* q15_add */")
        a("        va = (int32_t)yn[m];")
        a("        if (ck(va) || ck(r) || ck(va + r)) { rk_trap = rk_op; return 1; }")
        a("        yn[m] = (int16_t)(va + r);")
        a("    }")
    a("    for (m = 0; m < N_STATES; m++) y[m] = yn[m];")
    a("    return 0;")
    a("}")
    a("")
    a("void rk_done(void);")
    a("")
    a("void _start(void)")
    a("{")
    a("    int16_t y[N_STATES];")
    a("    int32_t s;")
    a("    int m;")
    a("    /* C leaves >> on a negative signed value to the implementation; the Q15 shift is")
    a("       arithmetic. If this compiler ever stopped agreeing, every result below is void. */")
    a("    g_shift_ok = ((((int32_t)-3) >> 1) == -2) ? 1 : 0;")
    a("    k_index = 0;")
    a("    acc_mismatch = 0;")
    a("    k_overrun = 0;")
    a("    rk_op = 0;")
    a("    rk_trap = 0;")
    a("    g_stopped = 0;")
    a("    for (m = 0; m < N_STATES; m++) y[m] = g_y[m];")
    a("    for (s = 0; s < g_steps; s++) {")
    a("        if (g_mode) {")
    a("            if (rk_step_checked(y, g_hq)) { g_stopped = s + 1; break; }")
    a("        } else {")
    a("            rk_step(y, g_hq);")
    a("        }")
    a("    }")
    a("    for (m = 0; m < N_STATES; m++) g_y[m] = y[m];")
    a("    g_trap = rk_trap;")
    a("    g_ops = rk_op;")
    a("    rk_done();")
    a("}")
    a("")
    a("void __attribute__((noinline, noclone)) rk_done(void) { __asm__ volatile(\"nop\"); }")
    a("")
    return "\n".join(out)


LINKER_SCRIPT = """ENTRY(_start)
MEMORY { IMAGE (rwx) : ORIGIN = 0x00000000, LENGTH = 256K }
SECTIONS {
  .text : { *(.text*) *(.rodata*) } > IMAGE
  .data : { *(.data*) } > IMAGE
  .bss  : { *(.bss*) *(COMMON) } > IMAGE
  _image_end = .;
}
"""


# --------------------------------------------------------------------------- Python replica

class Replay:
    """One run of the pinned primitives, with everything the C side needs to reproduce it."""

    def __init__(self) -> None:
        self.status = "ok"
        self.final: tuple[int, ...] | None = None
        self.max_abs = 0
        self.ops = 0
        self.trap_op = 0
        self.steps_done = 0
        self.acc_log: list[int] = []
        self.k_log: list[int] = []
        self.h_q = 0
        self.detail = ""


def replay_q15(t: Tableau, problem, n: int, y0: tuple[int, ...] | None = None,
               h_q: int | None = None) -> Replay:
    """`simulate.solve_q15`, operation for operation, recording what the C port needs.

    Identical arithmetic on identical inputs: the same `to_rep` coefficients, the same
    primitives from `fixedpoint`, the same order. What it adds is the log of stage inputs
    and derivative outputs, and a counter over checked primitive calls so an overflow has a
    position and not only a fact. `test_B87` pins the equivalence against `solve_q15`.
    """
    r = Replay()
    h = problem.t_end / n
    r.h_q = int(h_q) if h_q is not None else q15_from_float(h / DERIV_SCALE.get(problem.name, 1.0))
    hq = r.h_q
    s = len(t.b)
    reps_A, reps_b = _reps(t)
    c_f = [float(x) for x in t.c]
    states = range(len(problem.y0))

    y = tuple(int(v) for v in (y0 if y0 is not None else problem.y0))
    for v in y:
        r.max_abs = max(r.max_abs, abs(v))

    op = 0
    try:
        for step in range(n):
            tk = step * h
            hk: list[tuple[int, ...]] = []
            for i in range(s):
                acc = y
                row = reps_A[i]
                for j in range(i):
                    rep = row[j]
                    if rep is None:
                        continue
                    hkj = hk[j]
                    new = []
                    for m in states:
                        op += 1
                        applied = q15_apply(hkj[m], rep.m, rep.s)
                        op += 1
                        new.append(q15_add(acc[m], applied))
                    acc = tuple(new)
                for v in acc:
                    r.max_abs = max(r.max_abs, abs(v))
                r.acc_log.extend(int(v) for v in acc)
                try:
                    k_i = problem.f(tk + c_f[i] * h, acc)
                except Q15OverflowError as exc:
                    r.status = "rhs_overflow"
                    r.detail = str(exc)
                    r.trap_op = op
                    r.ops = op
                    r.steps_done = step
                    return r
                r.k_log.extend(int(v) for v in k_i)
                row_hk = []
                for m in states:
                    op += 1
                    row_hk.append(q15_mul(k_i[m], hq))
                hk.append(tuple(row_hk))
            y_new = y
            for i in range(s):
                rep = reps_b[i]
                if rep is None:
                    continue
                hki = hk[i]
                new = []
                for m in states:
                    op += 1
                    applied = q15_apply(hki[m], rep.m, rep.s)
                    op += 1
                    new.append(q15_add(y_new[m], applied))
                y_new = tuple(new)
            for v in y_new:
                r.max_abs = max(r.max_abs, abs(v))
            y = y_new
            r.steps_done = step + 1
    except Q15OverflowError as exc:
        r.status = "overflow"
        r.detail = str(exc)
        r.trap_op = op
        r.ops = op
        return r
    r.final = y
    r.ops = op
    return r


# --------------------------------------------------------------------------- toolchain

class Toolchain:
    """arm-none-eabi-* and the path translation needed to reach it.

    The tools may be native, or on the other side of a WSL boundary from the interpreter.
    Either way the failure mode is an exception: a comparison that silently skips because a
    tool is missing is worth less than no comparison at all.
    """

    def __init__(self, prefix: list[str], translate: bool, note: str) -> None:
        self.prefix = prefix
        self.translate = translate
        self.note = note
        self._versions: dict[str, str] = {}

    @classmethod
    def resolve(cls) -> "Toolchain":
        env = os.environ.get("RK_ARM_GCC")
        if env:
            tc = cls([env], False, f"RK_ARM_GCC={env}")
            if tc._probe("gcc"):
                return tc
            raise TraceCheckError(f"RK_ARM_GCC={env!r} did not answer --version")
        if shutil.which("arm-none-eabi-gcc"):
            tc = cls([], False, "arm-none-eabi-gcc on PATH")
            if tc._probe("gcc"):
                return tc
        wsl = shutil.which("wsl.exe") or shutil.which("wsl")
        if wsl:
            tc = cls([wsl], True, f"{Path(wsl).name} bridge to arm-none-eabi-gcc")
            if tc._probe("gcc"):
                return tc
        raise TraceCheckError(
            "no arm-none-eabi toolchain: set RK_ARM_GCC, put arm-none-eabi-gcc on PATH, "
            "or make it reachable through wsl.exe"
        )

    def _tool(self, tool: str) -> str:
        if self.prefix and not self.translate:
            return self.prefix[0]
        return f"arm-none-eabi-{tool}"

    def _probe(self, tool: str) -> bool:
        try:
            r = self.run(tool, ["--version"], check=False)
        except OSError:
            return False
        return r.returncode == 0

    @staticmethod
    def wsl_path(p: str | Path) -> str:
        """D:\\a\\b and D:/a/b both become /mnt/d/a/b; anything else is passed through."""
        text = str(p).replace("\\", "/")
        m = re.match(r"^([A-Za-z]):/(.*)$", text)
        if m:
            return f"/mnt/{m.group(1).lower()}/{m.group(2)}"
        return text

    def arg(self, value: str | Path) -> str:
        return self.wsl_path(value) if self.translate else str(value)

    def run(self, tool: str, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
        cmd = list(self.prefix) + [self._tool(tool)] + args
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        if check and proc.returncode != 0:
            raise TraceCheckError(
                f"{' '.join(cmd[:3])} failed ({proc.returncode})\n{proc.stdout}\n{proc.stderr}")
        return proc

    def version(self, tool: str) -> str:
        if tool not in self._versions:
            out = self.run(tool, ["--version"]).stdout.strip().splitlines()
            self._versions[tool] = out[0].strip() if out else "unknown"
        return self._versions[tool]


# --------------------------------------------------------------------------- build

class Build:
    """One compiled and linked method variant, with its symbol table and disassembly."""

    def __init__(self, key: str, image: bytes, syms: dict[str, int],
                 insns: dict[int, tuple[str, str, int]], step_src: str,
                 driver_src: str, step_path: Path, driver_path: Path) -> None:
        self.key = key
        self.image = image
        self.syms = syms
        self.insns = insns                # address -> (mnemonic, operands, source line)
        self.step_src = step_src
        self.driver_src = driver_src
        self.step_path = step_path
        self.driver_path = driver_path


_INSN_RE = re.compile(r"^\s*([0-9a-f]+):\s+(\S+)\s*(.*?)\s*$")
_LINE_RE = re.compile(r"^(\S*\.c):(\d+)(?:\s|$)")


def _parse_objdump(text: str, step_name: str) -> dict[int, tuple[str, str, int]]:
    """address -> (mnemonic, operands, line) for every instruction in the step's file.

    Lines from any other translation unit get line 0: they are outside the traced range and
    are only ever counted as a total.
    """
    out: dict[int, tuple[str, str, int]] = {}
    cur_line = 0
    for raw in text.splitlines():
        m = _LINE_RE.match(raw.strip())
        if m:
            cur_line = int(m.group(2)) if Path(m.group(1)).name == step_name else 0
            continue
        m = _INSN_RE.match(raw)
        if m and not raw.strip().endswith(":"):
            addr = int(m.group(1), 16)
            operands = m.group(3).split("@")[0].strip()
            out[addr] = (m.group(2), operands, cur_line)
    return out


def build_method(tc: Toolchain, tmp: Path, key: str, t: Tableau, n_states: int,
                 c_out: Path | None) -> Build:
    """Emit, compile and link one method at one state count, and read back what ran."""
    step_src = emit_c(t, n_states)
    drv_src = driver_c(t, n_states)
    step_path = tmp / f"{key}_step.c"
    drv_path = tmp / f"{key}_driver.c"
    ld_path = tmp / "image.ld"
    step_path.write_text(step_src, encoding="utf-8", newline="\n")
    drv_path.write_text(drv_src, encoding="utf-8", newline="\n")
    ld_path.write_text(LINKER_SCRIPT, encoding="utf-8", newline="\n")
    if c_out is not None:
        c_out.mkdir(parents=True, exist_ok=True)
        (c_out / step_path.name).write_text(step_src, encoding="utf-8", newline="\n")
        (c_out / drv_path.name).write_text(drv_src, encoding="utf-8", newline="\n")

    elf = tmp / f"{key}.elf"
    binimg = tmp / f"{key}.bin"
    tc.run("gcc", list(CFLAGS) + list(DEBUG_FLAGS) +
           ["-T", tc.arg(ld_path), "-o", tc.arg(elf), tc.arg(step_path), tc.arg(drv_path)])
    tc.run("objcopy", ["-O", "binary", tc.arg(elf), tc.arg(binimg)])
    nm = tc.run("nm", ["-n", tc.arg(elf)]).stdout
    syms: dict[str, int] = {}
    for line in nm.splitlines():
        parts = line.split()
        if len(parts) == 3:
            syms[parts[2]] = int(parts[0], 16)
    for need in ("rk_step", "rk_rhs", "rk_done", "_start", "g_y", "g_hq", "g_steps",
                 "g_mode", "g_trap", "g_ops", "g_shift_ok", "g_stopped",
                 "k_table", "acc_table", "k_index", "acc_mismatch", "k_overrun"):
        if need not in syms:
            raise TraceCheckError(f"{key}: symbol {need} missing from the link")
    dis = tc.run("objdump", ["-d", "-l", "--no-show-raw-insn", tc.arg(elf)]).stdout
    insns = _parse_objdump(dis, step_path.name)
    return Build(key, binimg.read_bytes(), syms, insns, step_src, drv_src, step_path, drv_path)


# --------------------------------------------------------------------------- emulation

def _step_range(b: Build) -> tuple[int, int]:
    """[start, end) of rk_step. The next symbol by address ends it."""
    start = b.syms["rk_step"]
    later = sorted(v for v in b.syms.values() if v > start)
    if not later:
        raise TraceCheckError(f"{b.key}: cannot bound rk_step")
    return start, later[0]


def emulate(b: Build, y0: tuple[int, ...], h_q: int, steps: int, mode: int,
            k_log: list[int], acc_log: list[int], n_states: int,
            trace_insns: bool = True) -> dict:
    """Run one build under the unicorn engine and return what it did.

    Instruction accurate, not cycle accurate: the engine says which instructions execute and
    in what order, and says nothing about how long any of them takes.
    """
    try:
        from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_MCLASS, UC_MODE_THUMB, Uc
        from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_SP
    except ImportError as exc:                                   # fail closed, never skip
        raise TraceCheckError(f"the unicorn engine is not importable: {exc}") from exc

    if len(k_log) > K_CAP or len(acc_log) > K_CAP:
        raise TraceCheckError(f"{b.key}: recorded log of {len(k_log)} exceeds K_CAP {K_CAP}")

    mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    # unicorn's mem_map probes the host allocator in a way Windows reports as an access
    # violation and unicorn then handles. It is not a fault and the run is unaffected, but
    # faulthandler is enabled under pytest and prints a full stack for every one, which
    # would bury a real crash in nineteen copies of a harmless one. The suppression covers
    # these two calls and is restored immediately, so a genuine fault anywhere else still
    # reports.
    import faulthandler
    was_enabled = faulthandler.is_enabled()
    if was_enabled:
        faulthandler.disable()
    try:
        mu.mem_map(IMAGE_BASE, IMAGE_SIZE)
        mu.mem_map(STACK_BASE, STACK_SIZE)
    finally:
        if was_enabled:
            faulthandler.enable()
    mu.mem_write(IMAGE_BASE, b.image)
    s = b.syms
    mu.mem_write(s["g_steps"], struct.pack("<i", int(steps)))
    mu.mem_write(s["g_mode"], struct.pack("<i", int(mode)))
    mu.mem_write(s["g_hq"], struct.pack("<h", int(h_q)))
    mu.mem_write(s["g_y"], struct.pack(f"<{n_states}h", *[int(v) for v in y0]))
    if k_log:
        mu.mem_write(s["k_table"], struct.pack(f"<{len(k_log)}h", *k_log))
    if acc_log:
        mu.mem_write(s["acc_table"], struct.pack(f"<{len(acc_log)}h", *acc_log))
    mu.reg_write(UC_ARM_REG_SP, STACK_BASE + STACK_SIZE - 64)
    mu.reg_write(UC_ARM_REG_LR, s["rk_done"] | 1)

    trace: list[tuple[int, int]] = []
    if trace_insns:
        mu.hook_add(UC_HOOK_CODE, lambda uc, addr, size, user: trace.append((addr, size)))
    mu.emu_start(s["_start"] | 1, s["rk_done"], count=8_000_000)

    def rd_i32(name: str) -> int:
        return struct.unpack("<i", bytes(mu.mem_read(s[name], 4)))[0]

    final = struct.unpack(f"<{n_states}h", bytes(mu.mem_read(s["g_y"], 2 * n_states)))
    return {
        "final": tuple(int(v) for v in final),
        "trap": rd_i32("g_trap"),
        "ops": rd_i32("g_ops"),
        "acc_mismatch": rd_i32("acc_mismatch"),
        "k_overrun": rd_i32("k_overrun"),
        "shift_ok": rd_i32("g_shift_ok"),
        "stopped": rd_i32("g_stopped"),
        "trace": trace,
    }


# --------------------------------------------------------------------------- attribution

def attribute(b: Build, trace: list[tuple[int, int]], regions: dict[int, str]) -> dict:
    """Bucket the executed instructions of one run by region, and price each bucket.

    Pricing needs to know whether a branch was taken, so it reads the next executed address
    from the whole trace rather than from the slice being attributed.
    """
    lo, hi = _step_range(b)
    by_region: dict[str, dict[str, int]] = {}
    histogram: dict[str, int] = {}
    hist_model: dict[str, int] = {}
    cycles = {m.name: 0 for m in PRIMARY_MODELS}
    cycles_model = {m.name: 0 for m in PRIMARY_MODELS}
    region_cycles = {m.name: {} for m in PRIMARY_MODELS}
    n_step_insns = 0

    for idx, (addr, size) in enumerate(trace):
        if not (lo <= addr < hi):
            continue
        entry = b.insns.get(addr)
        if entry is None:
            raise TraceCheckError(f"{b.key}: executed {addr:#x} has no disassembly")
        mnemonic, operands, lineno = entry
        region = regions.get(lineno, "other")
        nxt = trace[idx + 1][0] if idx + 1 < len(trace) else None
        taken = nxt is not None and nxt != addr + size
        key = mnemonic.lower()
        histogram[key] = histogram.get(key, 0) + 1
        n_step_insns += 1
        bucket = by_region.setdefault(region, {"instructions": 0})
        bucket["instructions"] += 1
        if region in _REGION_MODEL_SCOPE:
            hist_model[key] = hist_model.get(key, 0) + 1
        for model in PRIMARY_MODELS:
            c = instruction_cycles(mnemonic, operands, model, taken)
            cycles[model.name] += c
            rc = region_cycles[model.name]
            rc[region] = rc.get(region, 0) + c
            if region in _REGION_MODEL_SCOPE:
                cycles_model[model.name] += c
    return {
        "instructions": n_step_insns,
        "instructions_by_region": {r: by_region.get(r, {"instructions": 0})["instructions"]
                                   for r in _REGION_ORDER if r in by_region},
        "histogram": dict(sorted(histogram.items())),
        "histogram_model_scope": dict(sorted(hist_model.items())),
        "cycles": cycles,
        "cycles_model_scope": cycles_model,
        "cycles_by_region": {name: dict(sorted(region_cycles[name].items()))
                             for name in region_cycles},
    }


def _scale(d: dict, n: int) -> dict:
    return {k: (v // n if v % n == 0 else v / n) for k, v in d.items()}


# --------------------------------------------------------------------------- statistics

def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman rank correlation with average ranks for ties. None if either side is constant."""
    n = len(xs)
    if n < 2 or len(ys) != n:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx)
    dy = sum((b - my) ** 2 for b in ry)
    if dx <= 0 or dy <= 0:
        return None
    return num / ((dx ** 0.5) * (dy ** 0.5))


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


# --------------------------------------------------------------------------- methods

def load_method(name: str) -> tuple[str, Tableau, str]:
    """(origin, tableau, tableau_hash) for a classical name or an archive hash prefix."""
    ct = classical()
    if name in ct:
        t = ct[name]
        return "classical", t, content_hash(t)
    prefix = name.lower()
    if re.fullmatch(r"[0-9a-f]{6,64}", prefix):
        d = archive_dir()
        files = sorted(p for p in d.glob("*.jsonl")) if d.is_dir() else []
        for path in files:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    if prefix not in line:
                        continue
                    rec = json.loads(line)
                    h = rec.get("tableau_hash", "")
                    if h.startswith(prefix):
                        return "discovered", from_json(rec["tableau"]), h
        raise TraceCheckError(f"no archive record whose tableau hash starts with {name!r}")
    raise TraceCheckError(f"{name!r} is neither a classical name nor a tableau hash prefix")


# --------------------------------------------------------------------------- the run

def run(methods: tuple[str, ...] = DEFAULT_METHODS, c_out: Path | None = None) -> dict:
    """Compile, execute, attribute, price and cross-check every method. Raises on any failure."""
    tc = Toolchain.resolve()
    tmp = Path(tempfile.mkdtemp(prefix="rk-trace-"))
    try:
        return _run_in(tc, tmp, methods, c_out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_in(tc: Toolchain, tmp: Path, methods: tuple[str, ...], c_out: Path | None) -> dict:
    by_states: dict[str, list[dict]] = {}
    for case in CASES:
        p = PROBLEMS[case["problem"]]
        by_states.setdefault(str(p.n_states), []).append(case)

    rows: list[dict] = []
    for name in methods:
        origin, t, thash = load_method(name)
        regions = classify_step_source(emit_c(t, 1))
        row: dict = {
            "name": name,
            "origin": origin,
            "tableau_hash": thash,
            "stages": len(t.b),
            "cycles_analytic": {m.name: cycle_count(t, m, 1) for m in PRIMARY_MODELS},
            "coefficient_applications": sum(
                1 for r in list(t.A) + [list(t.b)] for x in r if x != 0),
        }
        crosscheck: list[dict] = []
        traced: dict | None = None
        scaling: dict = {}
        for n_states_key, cases in sorted(by_states.items()):
            n_states = int(n_states_key)
            key = f"{_safe(name)}_n{n_states}"
            b = build_method(tc, tmp, key, t, n_states, c_out)
            row.setdefault("c_sha256", {})[f"n{n_states}"] = {
                "step": _sha(b.step_src), "driver": _sha(b.driver_src)}
            for case in cases:
                crosscheck.append(_check_case(b, t, case, n_states))
            per_step = _trace_per_step(b, t, regions, n_states)
            scaling[f"n{n_states}"] = {
                "instructions_per_step": per_step["instructions"],
                "cycles_per_step": per_step["cycles"],
                "cycles_analytic": {m.name: cycle_count(t, m, n_states) for m in PRIMARY_MODELS},
            }
            if n_states == 1:
                traced = per_step
        if traced is None:
            raise TraceCheckError(f"{name}: no single-state trace was produced")
        row.update(_traced_fields(row, traced))
        row["state_scaling"] = scaling
        row["crosscheck"] = _crosscheck_summary(crosscheck)
        row["crosscheck_cases"] = crosscheck
        rows.append(row)

    rows.sort(key=lambda r: (r["cycles_analytic"]["m0plus_fast"], r["name"]))
    return _document(tc, rows)


def _safe(name: str) -> str:
    return re.sub(r"[^0-9a-zA-Z_]", "_", name)[:24]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _trace_per_step(b: Build, t: Tableau, regions: dict[int, str], n_states: int) -> dict:
    """Per-step instruction and cycle counts, from two runs that must stand in proportion."""
    case = next(c for c in TRACE_CASES if PROBLEMS[c["problem"]].n_states == n_states)
    problem = PROBLEMS[case["problem"]]
    y0 = tuple(int(v) for v in case["y0"])
    lo, hi = LINEARITY_STEPS
    out: dict[int, dict] = {}
    for steps in (lo, hi):
        rep = replay_q15(t, problem, steps, y0=y0, h_q=case["h_q"])
        if rep.status != "ok":
            raise TraceCheckError(f"{b.key}: the trace case overflowed in Python ({rep.detail})")
        res = emulate(b, y0, rep.h_q, steps, 0, rep.k_log, rep.acc_log, n_states)
        if res["final"] != rep.final:
            raise TraceCheckError(f"{b.key}: trace run disagreed with the Python evaluator")
        out[steps] = attribute(b, res["trace"], regions)
    if out[hi]["instructions"] * lo != out[lo]["instructions"] * hi:
        raise TraceCheckError(
            f"{b.key}: rk_step executed {out[lo]['instructions']} instructions over {lo} step(s) "
            f"and {out[hi]['instructions']} over {hi}; the step is not straight-line, so a "
            f"per-step histogram is not a fact about it")
    a = out[lo]
    n = lo
    return {
        "instructions": a["instructions"] // n,
        "instructions_by_region": _scale(a["instructions_by_region"], n),
        "histogram": _scale(a["histogram"], n),
        "histogram_model_scope": _scale(a["histogram_model_scope"], n),
        "cycles": _scale(a["cycles"], n),
        "cycles_model_scope": _scale(a["cycles_model_scope"], n),
        "cycles_by_region": {k: _scale(v, n) for k, v in a["cycles_by_region"].items()},
    }


def _traced_fields(row: dict, traced: dict) -> dict:
    analytic = row["cycles_analytic"]
    out: dict = {
        "cycles_traced": traced["cycles"],
        "cycles_traced_model_scope": traced["cycles_model_scope"],
        "instructions_per_step": traced["instructions"],
        "instructions_by_region": traced["instructions_by_region"],
        "opcode_histogram": traced["histogram"],
        "opcode_histogram_model_scope": traced["histogram_model_scope"],
        "cycles_by_region": traced["cycles_by_region"],
        "muls_per_step": traced["histogram"].get("muls", 0),
        "muls_in_model_scope": traced["histogram_model_scope"].get("muls", 0),
    }
    ratio, gap, ratio_m, gap_m = {}, {}, {}, {}
    for name, a in analytic.items():
        tr = traced["cycles"][name]
        tm = traced["cycles_model_scope"][name]
        ratio[name] = tr / a if a else None
        gap[name] = abs(a - tr) / tr if tr else None
        ratio_m[name] = tm / a if a else None
        gap_m[name] = abs(a - tm) / tm if tm else None
    out["ratio"] = ratio
    out["relative_gap"] = gap
    out["ratio_model_scope"] = ratio_m
    out["relative_gap_model_scope"] = gap_m
    return out


def _check_case(b: Build, t: Tableau, case: dict, n_states: int) -> dict:
    """One case through both the pinned evaluator and both compiled entry points."""
    problem = PROBLEMS[case["problem"]]
    y0 = tuple(case.get("y0") or [int(v) for v in problem.y0])
    rep = replay_q15(t, problem, case["steps"], y0=y0, h_q=case.get("h_q"))
    out = {
        "case": case["id"],
        "kind": case["kind"],
        "problem": problem.name,
        "steps": case["steps"],
        "python_status": rep.status,
        "python_final": list(rep.final) if rep.final is not None else None,
        "python_trap_op": rep.trap_op,
    }
    if rep.status == "rhs_overflow":
        # The derivative is float and the cost model prices none of it, so the C port
        # evaluates none. A case that fails inside the right-hand side has nothing for the
        # C to reproduce, and it is reported rather than counted.
        out["verdict"] = "not_comparable"
        return out

    checked = emulate(b, y0, rep.h_q, case["steps"], 1, rep.k_log, rep.acc_log, n_states,
                      trace_insns=False)
    out["c_checked_trap_op"] = checked["trap"]
    out["c_checked_final"] = list(checked["final"])
    out["stage_input_mismatch"] = checked["acc_mismatch"]
    out["shift_is_arithmetic"] = bool(checked["shift_ok"])
    problems: list[str] = []
    if not checked["shift_ok"]:
        problems.append("the compiler's >> on a negative value is not arithmetic")
    if checked["k_overrun"]:
        problems.append("the recorded derivative log ran out")

    if rep.status == "ok":
        plain = emulate(b, y0, rep.h_q, case["steps"], 0, rep.k_log, rep.acc_log, n_states,
                        trace_insns=False)
        out["c_plain_final"] = list(plain["final"])
        out["c_plain_stage_input_mismatch"] = plain["acc_mismatch"]
        if plain["acc_mismatch"]:
            problems.append(f"emit_c stage input differs at slot {plain['acc_mismatch']}")
        if tuple(plain["final"]) != rep.final:
            problems.append(f"emit_c final state {plain['final']} against Python {rep.final}")
        if checked["trap"] != 0:
            problems.append(f"the checked port trapped at op {checked['trap']} where Python did not")
        if tuple(checked["final"]) != rep.final:
            problems.append(f"checked final state {checked['final']} against Python {rep.final}")
        if checked["acc_mismatch"]:
            problems.append(f"checked stage input differs at slot {checked['acc_mismatch']}")
    else:
        # Python raised. The checked port has to trap at the same operation index. The
        # reference C carries no range check at all, which is a property of its source and
        # not something an emulation run needs to discover: it keeps int32 intermediates and
        # truncates at the store, so on this case it returns a value where the pinned
        # primitives raise. That is the whole reason a checked entry point exists.
        out["c_plain_trapped"] = False
        out["c_plain_note"] = (
            "the reference C from emit_c has no range check, so it cannot report this "
            "condition; its int32 intermediate truncates at the store and a value comes back")
        if "ck(" in b.step_src or "32767" in b.step_src:
            problems.append("the reference C unexpectedly carries a range check")
        if checked["trap"] != rep.trap_op:
            problems.append(
                f"checked port trapped at op {checked['trap']}, Python at op {rep.trap_op}")
    out["verdict"] = "match" if not problems else "mismatch"
    out["problems"] = problems
    return out


def _crosscheck_summary(cases: list[dict]) -> dict:
    comparable = [c for c in cases if c["verdict"] != "not_comparable"]
    return {
        "cases": len(cases),
        "comparable": len(comparable),
        "matched": sum(1 for c in comparable if c["verdict"] == "match"),
        "overflow_cases": sum(1 for c in comparable if c["python_status"] == "overflow"),
        "trap_index_matched": sum(1 for c in comparable if c["python_status"] == "overflow"
                                  and c["verdict"] == "match"),
        "not_comparable": len(cases) - len(comparable),
    }


# --------------------------------------------------------------------------- document

SCHEMA: dict[str, str] = {
    "cycles_analytic": "costmodel.cycle_count for this tableau at n_states = 1, unchanged",
    "cycles_traced": "TRM-priced cycles for every instruction executed inside rk_step in one "
                     "step, excluding the derivative routine's own body",
    "cycles_traced_model_scope": "the same pricing restricted to the instructions the "
                                 "analytic model prices: the load, the coefficient terms, "
                                 "the adds and the store",
    "ratio": "cycles_traced / cycles_analytic",
    "ratio_model_scope": "cycles_traced_model_scope / cycles_analytic",
    "relative_gap": "|cycles_analytic - cycles_traced| / cycles_traced",
    "instructions_by_region": "executed instructions per step by what the source line does: "
                              "load, coeff, store are the model's scope; hscale is the h "
                              "times k product the model excludes; rhs_call is the call to "
                              "the derivative; loop is loop control; frame is the stack frame",
    "opcode_histogram": "executed mnemonics per step inside rk_step",
    "muls_per_step": "MULS instructions executed per step inside rk_step",
    "muls_in_model_scope": "of those, the ones applying a tableau coefficient",
    "state_scaling": "the same counts at one and at two states, so the model's linear scaling "
                     "in state dimension can be read rather than assumed",
    "crosscheck": "agreement with the pinned Python evaluator: exact final int16 state, every "
                  "stage input, and the operation index at which an overflow trapped",
}


def _document(tc: Toolchain, rows: list[dict]) -> dict:
    analytic = [r["cycles_analytic"]["m0plus_fast"] for r in rows]
    traced = [r["cycles_traced"]["m0plus_fast"] for r in rows]
    traced_model = [r["cycles_traced_model_scope"]["m0plus_fast"] for r in rows]
    slow_analytic = [r["cycles_analytic"]["m0plus_slow"] for r in rows]
    slow_traced = [r["cycles_traced"]["m0plus_slow"] for r in rows]
    matched = sum(r["crosscheck"]["matched"] for r in rows)
    comparable = sum(r["crosscheck"]["comparable"] for r in rows)
    overflow = sum(r["crosscheck"]["overflow_cases"] for r in rows)

    doc = {
        "schema": SCHEMA,
        "generated_from": {
            "script": "rk_harness/tracecheck.py",
            "trace_hash": trace_hash.compute_trace_hash(),
            "trace_hash_pinned": trace_hash.pinned_trace_hash(),
            "verifier_hash": pinned_verifier_hash(),
            "emitter": "rk_harness.costmodel.emit_c, used verbatim and never edited",
            "methods": [r["name"] for r in rows],
            "n_states": 1,
        },
        "accuracy": {
            "statement": (
                "The emulator is instruction accurate and NOT cycle accurate. It reports which "
                "instructions execute and in what order. Every cycle count here comes from "
                "applying the Cortex-M0+ TRM table to that instruction stream offline, under "
                "the assumptions below. No number here was measured on any physical part."),
            "establishes": [
                "the compiled Q15 step executes the instruction mix the analytic model assumes, "
                "or differs from it in a way this document names",
                "whether dyadic coefficients compile to shifts and adds rather than to "
                "multiplies or library calls",
                "relative ordering and ratios between methods under the TRM table",
                "that the C agrees with the pinned Python evaluator bit for bit, including "
                "where the Q15 primitives raise",
            ],
            "does_not_establish": [
                "wall clock time on any physical part",
                "memory system behaviour: flash wait states, bus contention, buffering",
                "interrupt or RTOS overhead",
                "anything about a chip other than the one the TRM table describes",
            ],
        },
        "assumptions": ASSUMPTIONS,
        "mnemonic_class": {
            "from_cost_model": dict(sorted(PINNED_MNEMONIC_CLASS.items())),
            "trace_extension": dict(sorted(TRACE_MNEMONIC_CLASS.items())),
            "note": ("The five classes the cost model prices are taken from it unchanged, so "
                     "load, store, mul, shift and add cost the same on both sides. The "
                     "extension covers mnemonics a compiler emits that the pinned ten-line "
                     "fixture does not contain; alu is one cycle, branch and multi-register "
                     "transfers follow the assumptions block."),
        },
        "toolchain": {
            "compiler": tc.version("gcc"),
            "objdump": tc.version("objdump"),
            "emulator": f"unicorn {_unicorn_version()}",
            "flags": list(CFLAGS) + list(DEBUG_FLAGS),
            "linker_script": "text, data and bss placed contiguously from 0x0; a flat image",
            "access": tc.note,
            "debug_flag_note": "-g adds a line table and no instructions; the region "
                               "attribution reads it",
        },
        "cost_models": {m.name: dict(sorted(m.cycles.items())) for m in PRIMARY_MODELS},
        "cases": {
            "crosscheck": [dict(c) for c in CASES],
            "trace": [dict(c) for c in TRACE_CASES],
            "linearity_steps": list(LINEARITY_STEPS),
        },
        "methods": rows,
        "correlation": {
            "spearman_analytic_vs_traced_fast": spearman(analytic, traced),
            "spearman_analytic_vs_traced_model_scope_fast": spearman(analytic, traced_model),
            "spearman_analytic_vs_traced_slow": spearman(slow_analytic, slow_traced),
            "n_methods": len(rows),
            "inversions_fast": inversions(rows, "m0plus_fast", "cycles_traced"),
            "inversions_model_scope_fast": inversions(rows, "m0plus_fast",
                                                      "cycles_traced_model_scope"),
            "inversions_slow": inversions(rows, "m0plus_slow", "cycles_traced"),
        },
        "verdicts": _verdicts(rows, matched, comparable, overflow),
    }
    return doc


def _unicorn_version() -> str:
    try:
        import unicorn
        return str(getattr(unicorn, "__version__", "unknown"))
    except ImportError:
        return "unknown"


def inversions(rows: list[dict], model: str, traced_key: str) -> list[dict]:
    """Every pair the analytic model and the trace put in a different order.

    A rank correlation below 1.0 is only useful if the reader can see which pair caused it,
    so each one is named with both numbers rather than summarised into a coefficient.
    """
    out: list[dict] = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = rows[i], rows[j]
            aa, ab = a["cycles_analytic"][model], b["cycles_analytic"][model]
            ta, tb = a[traced_key][model], b[traced_key][model]
            if (aa - ab) * (ta - tb) < 0:
                cheaper_analytic, dearer_analytic = (a, b) if aa < ab else (b, a)
                cheaper_traced = a if ta < tb else b
                out.append({
                    "pair": [a["name"], b["name"]],
                    "analytic": {a["name"]: aa, b["name"]: ab},
                    "traced": {a["name"]: ta, b["name"]: tb},
                    "cheaper_analytic": cheaper_analytic["name"],
                    "cheaper_traced": cheaper_traced["name"],
                    "note": (f"the analytic model puts {cheaper_analytic['name']} below "
                             f"{dearer_analytic['name']} and the trace puts "
                             f"{cheaper_traced['name']} below the other"),
                })
    return out


def _verdicts(rows: list[dict], matched: int, comparable: int, overflow: int) -> dict:
    fast_ratios = [r["ratio"]["m0plus_fast"] for r in rows]
    model_ratios = [r["ratio_model_scope"]["m0plus_fast"] for r in rows]
    slow_model = [r["ratio_model_scope"]["m0plus_slow"] for r in rows]
    sp = spearman([r["cycles_analytic"]["m0plus_fast"] for r in rows],
                  [r["cycles_traced"]["m0plus_fast"] for r in rows])
    inv = inversions(rows, "m0plus_fast", "cycles_traced")
    worst = max(rows, key=lambda r: r["relative_gap_model_scope"]["m0plus_fast"])
    if sp is None:
        ordering = "rank correlation undefined"
    elif not inv:
        ordering = (f"Spearman rank correlation between the analytic and the traced cycles "
                    f"per step across {len(rows)} methods is {sp:.4f} under m0plus_fast, and "
                    f"the two orderings agree on every pair.")
    else:
        named = "; ".join(f"{d['pair'][0]} against {d['pair'][1]}, analytic "
                          f"{d['analytic'][d['pair'][0]]} and {d['analytic'][d['pair'][1]]}, "
                          f"traced {d['traced'][d['pair'][0]]} and {d['traced'][d['pair'][1]]}"
                          for d in inv)
        ordering = (f"Spearman rank correlation between the analytic and the traced cycles "
                    f"per step across {len(rows)} methods is {sp:.4f} under m0plus_fast. "
                    f"{len(inv)} pair(s) rank differently: {named}.")
    return {
        "crosscheck": (
            f"{matched} of {comparable} comparable cases agree with the pinned Python "
            f"evaluator on the exact final int16 state and on every stage input. "
            f"{overflow} of them are cases where the Q15 primitives raise, and on those the "
            f"checked C traps at the same operation index. The reference C from emit_c keeps "
            f"int32 intermediates and truncates at the store, so it returns a value instead "
            f"of trapping; that is why a checked entry point exists and why the comparison "
            f"reads the overflow verdict and not only the state."),
        "ordering": ordering,
        "inversions": inv,
        "scope": (
            "The analytic model prices the stage and b combinations only. It prices no "
            "derivative call, no h times k product, no loop control and no stack frame, so "
            "the whole-step traced number is larger by construction. Both are published: "
            "ratio is whole step, ratio_model_scope is the matched scope."),
        "ratio_range_fast": [min(fast_ratios), max(fast_ratios)],
        "ratio_range_model_scope_fast": [min(model_ratios), max(model_ratios)],
        "ratio_range_model_scope_slow": [min(slow_model), max(slow_model)],
        "widest_model_scope_gap": {
            "method": worst["name"],
            "relative_gap": worst["relative_gap_model_scope"]["m0plus_fast"],
        },
    }


# --------------------------------------------------------------------------- gate and CLI

def gate(doc: dict) -> None:
    """Fail closed on anything that would make the published numbers unreadable.

    A ratio far from 1.0 is not a failure here: publishing a disagreement is the point of the
    comparison. What is a failure is a C port that does not reproduce the pinned evaluator, an
    overflow contract asserted rather than exercised, or a method with nothing to compare.
    """
    failures: list[str] = []
    for row in doc["methods"]:
        cc = row["crosscheck"]
        if cc["comparable"] == 0:
            failures.append(f"{row['name']}: no comparable case, so nothing was checked")
        if cc["matched"] != cc["comparable"]:
            failures.append(f"{row['name']}: {cc['matched']} of {cc['comparable']} comparable "
                            f"cases reproduced the pinned evaluator")
            for case in row["crosscheck_cases"]:
                if case["verdict"] == "mismatch":
                    failures.append(f"{row['name']}/{case['case']}: "
                                    f"{'; '.join(case.get('problems') or ['unexplained'])}")
        if cc["overflow_cases"] == 0:
            failures.append(f"{row['name']}: no case reached the overflow trap, so the trap "
                            f"contract was asserted rather than exercised")
        if cc["trap_index_matched"] != cc["overflow_cases"]:
            failures.append(f"{row['name']}: an overflow trapped at a different operation index")
        for case in row["crosscheck_cases"]:
            if case.get("shift_is_arithmetic") is False:
                failures.append(f"{row['name']}: the compiler's right shift is not arithmetic")
    if failures:
        raise TraceCheckError("cross-check failed:\n  " + "\n  ".join(failures))


def default_out() -> Path:
    return work_dir() / "trace" / "results.json"


def write(doc: dict, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n",
                   encoding="utf-8", newline="\n")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="compile, trace and price the Q15 step")
    ap.add_argument("--methods", nargs="*", default=list(DEFAULT_METHODS))
    ap.add_argument("--out", default=None, help="destination JSON (default rk-work/trace/results.json)")
    ap.add_argument("--no-write", action="store_true", help="run the comparison and print only")
    args = ap.parse_args(argv)

    out = Path(args.out) if args.out else default_out()
    c_out = None if args.no_write else out.parent / "c"
    try:
        doc = run(tuple(args.methods), c_out=c_out)
        gate(doc)
    except TraceCheckError as exc:
        print(f"TRACE CHECK FAILED: {exc}", file=sys.stderr)
        return 1
    for row in doc["methods"]:
        print(f"{row['name']:>10}  analytic {row['cycles_analytic']['m0plus_fast']:>4}  "
              f"traced {row['cycles_traced']['m0plus_fast']:>4}  "
              f"model scope {row['cycles_traced_model_scope']['m0plus_fast']:>4}  "
              f"ratio {row['ratio']['m0plus_fast']:.3f}  "
              f"muls {row['muls_per_step']}")
    print(doc["verdicts"]["crosscheck"], file=sys.stderr)
    if not args.no_write:
        print(f"wrote {write(doc, out)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
