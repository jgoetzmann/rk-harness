#!/usr/bin/env python3
"""Pinned generator for fixtures/m0plus_coeff_ops.json (PLAN.md section 2.1, D45).

For every signed multiplier the harness can look up, compile exactly the term
costmodel.py:120 emits -- ``tmp += ((int32_t)src * (int32_t)M) >> S;`` -- with
arm-none-eabi-gcc 13.2.1 at tracecheck.py:81 CFLAGS for cortex-m0plus, and
count the coefficient instructions by op class:

    MULS -> mul;  LSLS, ASRS, LSRS -> shift;
    ADDS, SUBS, NEGS, RSBS, MVNS, MOVS, MOV -> add;
    PC-relative literal LDR -> load.
    Any other mnemonic fails the generator (fail closed).

Scope stripped per function (PLAN 2.1): the int16 source load (absent by
construction: src arrives by value), the single accumulator write (the
trailing adds into r0), the return (bx), alignment padding (nop) and literal
pool data (.word/.short). The >>S shift (asrs) IS counted, matching the old
model's mul_cost = mul + shift.

Output schema (PLAN 2.1): {"tuples": sorted distinct [mul,shift,add,load],
  "shifted": index array, "unshifted": index array, "_meta": {...}}.
Index arrays have length 65535; slot m+32767 holds the tuple index, or null
when m is outside that array's domain (the costmodel loader must raise on
null, so the rule fails closed). No timestamps, no paths, sorted iteration:
two builds are byte-identical. ``_meta.generator_sha256`` is the sha256 of
this file, so the fixture names the exact generator that built it.

Keying. ``shifted`` covers every odd m in [-32767, 32767] (canonical S=16,
the sweep shift, so the V0 positive-odd cross-check stays comparable);
``unshifted`` covers every m with 2 <= |m| <= 32767 at emit_c's literal >> 0.

Modes:
  sample   tiny key set, emit fixture-schema JSON (pipeline test, NOT a fixture)
  full     every shifted + every unshifted key, emit the pinned fixture
  check-s  S-independence stride sample + unshifted >>0-vs-no-shift check (V0)
  sweep    all 16383 positive odd m at S=16, MULS/sequence report (V0 cross-check)

Examples (from the repository root):
  python scripts/build_coeff_ops.py --mode sample --workdir /tmp/coeff-work --out /tmp/sample_fixture.json
  python scripts/build_coeff_ops.py --mode full --workdir /tmp/coeff-work
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "fixtures" / "m0plus_coeff_ops.json"

GCC = "arm-none-eabi-gcc"
OBJDUMP = "arm-none-eabi-objdump"
# tracecheck.py:81. DEBUG_FLAGS ("-g") deliberately excluded: results.json notes
# -g adds a line table and no instructions, and PLAN 2.1 pins CFLAGS only.
CFLAGS = ("-mcpu=cortex-m0plus", "-mthumb", "-O2", "-ffreestanding", "-nostdlib")

# Canonical shift for shifted keys. Matches the sweep shift (>> 16) so the V0
# positive-odd cross-check is apples-to-apples. V0's stride sample must (and
# does, via check-s) cover this S.
SHIFTED_S = 16
CHECK_S_VALUES = (1, 2, 6, 8, 15, 16, 17, 20)

CLASS_OF = {
    "MULS": "mul",
    "LSLS": "shift", "ASRS": "shift", "LSRS": "shift",
    "ADDS": "add", "SUBS": "add", "NEGS": "add", "RSBS": "add",
    "MVNS": "add", "MOVS": "add", "MOV": "add",
    "LDR": "load",
}
TUPLE_ORDER = ("mul", "shift", "add", "load")

SHIFTED_TEMPLATE = ("__attribute__((noinline)) int32_t {fn}(int32_t acc, int16_t x) "
                    "{{ acc += ((int32_t)x * (int32_t){m}) >> {s}; return acc; }}")
UNSHIFTED_TEMPLATE = ("__attribute__((noinline)) int32_t {fn}(int32_t acc, int16_t x) "
                      "{{ acc += ((int32_t)x * (int32_t){m}) >> 0; return acc; }}")
NOSHIFT_TEMPLATE = ("__attribute__((noinline)) int32_t {fn}(int32_t acc, int16_t x) "
                    "{{ acc += ((int32_t)x * (int32_t){m}); return acc; }}")


class GeneratorError(Exception):
    pass


FN_RE = re.compile(r"^[0-9a-f]+\s+<([^>]+)>:$")
LINE_RE = re.compile(r"^\s*[0-9a-f]+:\s*(.*?)\s*$")
HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
R0DEST_RE = re.compile(r"^\s*r0\s*,", re.IGNORECASE)


def base_mnemonic(mn):
    m = mn.upper()
    if m.endswith(".N") or m.endswith(".W"):
        m = m[:-2]
    return m


def split_body(lines):
    """objdump -d text -> {fn: [(mnemonic, operands)]} in file order."""
    funcs = {}
    cur = None
    for raw in lines:
        fm = FN_RE.match(raw)
        if fm:
            cur = fm.group(1)
            funcs[cur] = []
            continue
        if cur is None:
            continue
        lm = LINE_RE.match(raw)
        if not lm:
            continue
        rest = lm.group(1)
        if not rest:
            continue
        toks = rest.split()
        if toks[0].startswith("."):
            funcs[cur].append(("DATA", rest))
        elif HEX_RE.match(toks[0]) and len(toks) > 1 and toks[1].startswith("."):
            funcs[cur].append(("DATA", rest))
        else:
            funcs[cur].append((toks[0], " ".join(toks[1:])))
    return funcs


def classify(fn, insns):
    """Strip return/padding/data + accumulator write (adds/subs); count op classes.

    Returns ((mul, shift, add, load), seq) where seq is the classified
    mnemonic list, lowercased. Raises GeneratorError on any unexpected shape.
    """
    body = list(insns)
    while body and (body[-1][0] == "DATA" or body[-1][0].lower() in ("bx", "nop")):
        body.pop()
    for mn, ops in body:
        if mn == "DATA" or mn.lower() in ("bx", "nop"):
            raise GeneratorError("%s: return/padding/data inside body: %r" % (fn, body))
    if not body:
        raise GeneratorError("%s: empty body after stripping" % fn)
    mn, ops = body[-1]
    # The accumulator write: acc lives in r0 across the statement (it is also
    # the return register), so the last op must read AND write r0. It is ADDS
    # when the coefficient is positive and SUBS when GCC folds a negative
    # sign into it (observed: un_n2 ends 'subs r0, r0, r1'). A clobbering
    # write that does not read r0 fails closed instead of being stripped.
    if base_mnemonic(mn) not in ("ADDS", "ADD", "SUBS", "SUB"):
        raise GeneratorError("%s: last op is not the accumulator write: %r" % (fn, body[-1]))
    if not R0DEST_RE.match(ops) or len(re.findall(r"\br0\b", ops, re.IGNORECASE)) < 2:
        raise GeneratorError("%s: accumulator write does not read r0: %r" % (fn, body[-1]))
    body.pop()
    counts = {"mul": 0, "shift": 0, "add": 0, "load": 0}
    seq = []
    for mn, ops in body:
        b = base_mnemonic(mn)
        cls = CLASS_OF.get(b)
        if cls is None:
            raise GeneratorError("%s: unknown mnemonic %r in %r" % (fn, mn, body))
        counts[cls] += 1
        seq.append(mn.lower())
    return (counts["mul"], counts["shift"], counts["add"], counts["load"]), seq


def run(cmd, timeout_s=600):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=timeout_s)
    return (p.returncode,
            p.stdout.decode("utf-8", errors="replace"),
            p.stderr.decode("utf-8", errors="replace"))


def tool_version(cmd):
    rc, out, err = run([cmd, "--version"])
    if rc != 0:
        raise GeneratorError("%s --version failed: %s" % (cmd, err[:500]))
    return out.splitlines()[0].strip()


def fn_shifted(m):
    return "sh_p%d" % m if m >= 0 else "sh_n%d" % (-m)


def fn_unshifted(m):
    return "un_p%d" % m if m >= 0 else "un_n%d" % (-m)


def all_shifted_keys():
    return [m for m in range(-32767, 32768) if m % 2 == 1]


def all_unshifted_keys():
    return [m for m in range(-32767, 32768) if abs(m) >= 2]


def render_item(item):
    """item = (fn, class, m, s or None, noshift bool) -> one C function line."""
    fn, cls, m, s, noshift = item
    if noshift:
        return NOSHIFT_TEMPLATE.format(fn=fn, m=m)
    if cls == "sh":
        return SHIFTED_TEMPLATE.format(fn=fn, m=m, s=SHIFTED_S if s is None else s)
    return UNSHIFTED_TEMPLATE.format(fn=fn, m=m)


def build_all(items, workdir, jobs, tag="chunk"):
    """Compile items in deterministic chunks, classify every function.

    Returns {fn: ((mul,shift,add,load), seq)}. Fail closed on any anomaly.
    """
    os.makedirs(workdir, exist_ok=True)
    items = list(items)
    n = len(items)
    nchunks = max(1, min(n, max(1, jobs) * 4))
    per = (n + nchunks - 1) // nchunks
    chunks = [items[i:i + per] for i in range(0, n, per)]
    procs = []
    for k, ch in enumerate(chunks):
        cpath = os.path.join(workdir, "%s_%02d.c" % (tag, k))
        opath = os.path.join(workdir, "%s_%02d.o" % (tag, k))
        with open(cpath, "w", encoding="utf-8") as fh:
            fh.write("#include <stdint.h>\n")
            for it in ch:
                fh.write(render_item(it) + "\n")
        procs.append((cpath, opath, subprocess.Popen(
            [GCC] + list(CFLAGS) + ["-c", cpath, "-o", opath],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)))
    for cpath, opath, p in procs:
        try:
            _out, err = p.communicate(timeout=600)
        except subprocess.TimeoutExpired:
            p.kill()
            raise GeneratorError("gcc timed out on %s" % cpath)
        if p.returncode != 0:
            raise GeneratorError(
                "gcc failed on %s: %s" % (cpath, err.decode("utf-8", errors="replace")[:2000]))
    got = {}
    for k, ch in enumerate(chunks):
        opath = os.path.join(workdir, "%s_%02d.o" % (tag, k))
        rc, out, err = run([OBJDUMP, "-d", "--no-show-raw-insn", opath])
        if rc != 0:
            raise GeneratorError("objdump failed on %s: %s" % (opath, err[:500]))
        funcs = split_body(out.splitlines())
        want = [it[0] for it in ch]
        if sorted(funcs) != sorted(want):
            raise GeneratorError("function set mismatch in %s: missing=%s extra=%s"
                                 % (opath, sorted(set(want) - set(funcs))[:5],
                                    sorted(set(funcs) - set(want))[:5]))
        for it in ch:
            fn = it[0]
            tup, seq = classify(fn, funcs[fn])
            got[fn] = (tup, seq)
    return got


def emit_fixture(records, extra_meta=None):
    """records: {(m, cls): (mul,shift,add,load)} with cls in ("sh","un").

    Returns the fixture-schema dict. Keys not in a class domain are null.
    """
    for (m, cls) in records:
        if cls == "sh":
            assert m % 2 == 1 and abs(m) <= 32767, (m, cls)
        else:
            assert cls == "un" and 2 <= abs(m) <= 32767, (m, cls)
    tuples = sorted({tuple(v) for v in records.values()})
    index = {t: i for i, t in enumerate(tuples)}
    shifted = []
    unshifted = []
    for m in range(-32767, 32768):
        t = records.get((m, "sh"))
        shifted.append(None if t is None else index[tuple(t)])
        t = records.get((m, "un"))
        unshifted.append(None if t is None else index[tuple(t)])
    meta = {
        "compiler": tool_version(GCC),
        "objdump": tool_version(OBJDUMP),
        "cflags": list(CFLAGS),
        "shifted_template": SHIFTED_TEMPLATE,
        "shifted_canonical_s": SHIFTED_S,
        "unshifted_template": UNSHIFTED_TEMPLATE,
        "noshift_template": NOSHIFT_TEMPLATE,
        "tuple_order": list(TUPLE_ORDER),
        "class_map": dict(sorted(CLASS_OF.items())),
        "key_domains": {
            "shifted": "odd m in [-32767, 32767]",
            "unshifted": "m with 2 <= |m| <= 32767",
        },
        "generator_sha256": hashlib.sha256(
            open(os.path.abspath(__file__), "rb").read()).hexdigest(),
        "decision": "D45",
    }
    if extra_meta:
        meta.update(extra_meta)
    return {"tuples": [list(t) for t in tuples],
            "shifted": shifted, "unshifted": unshifted, "_meta": meta}


def dump_canonical(obj, path):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, sort_keys=True, indent=2)
        fh.write("\n")


def sample_items():
    ms_sh = [1, -1, 3, -3, 43, -43, 77, -77, 255, -255, 257,
             10923, -10923, 21845, -21845, 32767, -32767]
    ms_un = [2, -2, 3, -3, 6, -6, 43, -43, 100, -100, 10923, -10923,
             16384, -16384, 32766, -32766, 32767, -32767]
    return ([(fn_shifted(m), "sh", m, None, False) for m in ms_sh]
            + [(fn_unshifted(m), "un", m, None, False) for m in ms_un])


def check_s_items():
    odds = list(range(3, 32768, 2))
    step = 32
    sample = [odds[i] for i in range(0, len(odds), step)][:512]
    assert len(sample) == 512, len(sample)
    items = []
    for m in sample:
        for sign in (1, -1):
            for s in CHECK_S_VALUES:
                mm = sign * m
                items.append(("ck_%s%d_s%d" % (('p' if sign > 0 else 'n'), m, s),
                              "sh", mm, s, False))
    # unshifted: literal >> 0 must equal the no-shift form (V0)
    probe = [2, -2, 3, -3, 6, -6, 43, -43, 10923, -10923, 16384, -16384,
             21845, -21845, 32766, -32766, 32767, -32767]
    for m in probe:
        items.append(("uz_p%d" % abs(m) if m >= 0 else "uz_n%d" % abs(m),
                      "un", m, None, False))
        items.append(("nz_p%d" % abs(m) if m >= 0 else "nz_n%d" % abs(m),
                      "un", m, None, True))
    return items


def run_check_s(workdir, jobs):
    items = check_s_items()
    got = build_all(items, os.path.join(workdir, "check_s"), jobs, tag="checks")
    # S-independence: same tuple AND same mnemonic stream at every S
    by_key = {}
    for fn, (tup, seq) in got.items():
        if fn.startswith("ck_"):
            base = fn.rsplit("_s", 1)[0]
            by_key.setdefault(base, {})[fn.rsplit("_s", 1)[1]] = (tup, seq)
    s_viol = []
    for base in sorted(by_key):
        vals = {json.dumps([list(t), s]) for _s, (t, s) in by_key[base].items()}
        if len(vals) != 1:
            s_viol.append({base: {s: [list(t), se] for s, (t, se) in sorted(by_key[base].items())}})
    un_mismatch = []
    for m in [2, -2, 3, -3, 6, -6, 43, -43, 10923, -10923, 16384, -16384,
              21845, -21845, 32766, -32766, 32767, -32767]:
        tag = "p%d" % abs(m) if m >= 0 else "n%d" % abs(m)
        a = got["uz_" + tag]
        b = got["nz_" + tag]
        if a != b:
            un_mismatch.append([m, a, b])
    return {"n_functions": len(items),
            "s_values": list(CHECK_S_VALUES),
            "s_independence_violations": s_viol,
            "unshifted_shift0_vs_noshift_mismatches": un_mismatch,
            "ok": (not s_viol and not un_mismatch)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("sample", "full", "check-s", "sweep"),
                    default="sample")
    ap.add_argument("--workdir", default=None,
                    help="scratch build directory (default: a fresh mkdtemp outside the repo)")
    ap.add_argument("--out", default=str(DEFAULT_OUT),
                    help="output JSON path (default: fixtures/m0plus_coeff_ops.json)")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--verify-determinism", action="store_true")
    args = ap.parse_args()

    workdir = args.workdir or tempfile.mkdtemp(prefix="coeff_ops_")

    if args.mode == "check-s":
        rep = run_check_s(workdir, args.jobs)
        rep.update({"gcc": tool_version(GCC), "objdump": tool_version(OBJDUMP),
                    "cflags": list(CFLAGS)})
        dump_canonical(rep, args.out)
        print("check-s: %d functions, S-violations %d, unshifted mismatches %d -> %s"
              % (rep["n_functions"], len(rep["s_independence_violations"]),
                 len(rep["unshifted_shift0_vs_noshift_mismatches"]),
                 "OK" if rep["ok"] else "FAIL"))
        return

    if args.mode == "sweep":
        keys = list(range(3, 32768, 2))
        items = [("f%d" % m, "sh", m, None, False) for m in keys]
        got = build_all(items, os.path.join(workdir, "sweep"), args.jobs, tag="sweep")
        muls = sum(1 for _fn, (t, _s) in got.items() if t[0] > 0)
        dump_canonical({"n_keys": len(keys), "n_muls": muls,
                        "n_shiftadd": len(keys) - muls}, args.out)
        print("sweep: MULS %d / shift-add %d over %d" % (muls, len(keys) - muls, len(keys)))
        return

    if args.mode == "sample":
        items = sample_items()
        sub = os.path.join(workdir, "sample")
    else:  # full
        items = ([(fn_shifted(m), "sh", m, None, False) for m in all_shifted_keys()]
                 + [(fn_unshifted(m), "un", m, None, False) for m in all_unshifted_keys()])
        sub = os.path.join(workdir, "full")
    got = build_all(items, sub, args.jobs, tag=args.mode)
    records = {}
    for fn, (tup, _seq) in got.items():
        if fn.startswith("sh_"):
            m = int(fn[4:]) if fn[3] == "p" else -int(fn[4:])
            records[(m, "sh")] = tup
        else:
            m = int(fn[4:]) if fn[3] == "p" else -int(fn[4:])
            records[(m, "un")] = tup
    fixture = emit_fixture(records, {"mode": args.mode, "n_keys": len(records)})
    dump_canonical(fixture, args.out)
    seqs = {fn: seq for fn, (_tup, seq) in sorted(got.items())}
    dump_canonical(seqs, args.out + ".seqs.json")
    print("%s: %d keys, %d distinct tuples -> %s (+ .seqs.json audit)"
          % (args.mode, len(records), len(fixture["tuples"]), args.out))
    if args.verify_determinism:
        again = os.path.join(workdir, "sample_again")
        got2 = build_all(items, again, args.jobs, tag="sample2")
        assert got2 == got, "non-deterministic build!"
        print("determinism: two builds in different dirs, identical classifications")


if __name__ == "__main__":
    main()
