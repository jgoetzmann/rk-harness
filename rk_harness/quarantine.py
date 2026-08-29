"""Quarantine for model-authored problems — HANDOFF §7. Hand-written (HANDOFF §16.1).

The one place model-written code executes. A candidate derivative function is staged to
rk-work/quarantine/<name>.py and must pass all six admission checks before it is loaded
at all. The import allowlist is enforced by walking the AST, never by regex.

Staged source contract: a module defining `def f(t, y): ...` returning a tuple of floats
in physical units, using only `math` and a short list of builtins.
"""
from __future__ import annotations

import ast
import json
import math
import os
import time
from pathlib import Path

from rk_harness.paths import quarantine_dir
from rk_harness.types import Problem

ALLOWED_MODULES = frozenset({"math"})
ALLOWED_NAMES = frozenset({
    "math", "abs", "min", "max", "sum", "len", "range", "float", "int", "tuple", "list",
    "round", "pow", "zip", "enumerate", "True", "False", "None",
})
ALLOWED_NODES = (
    ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return, ast.Assign, ast.AugAssign,
    ast.AnnAssign, ast.Expr, ast.Import, ast.ImportFrom, ast.alias, ast.Name, ast.Load, ast.Store,
    ast.Constant, ast.Tuple, ast.List, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.Call,
    ast.Attribute, ast.Subscript, ast.Slice, ast.IfExp, ast.If, ast.For, ast.While, ast.Break,
    ast.Continue, ast.Pass, ast.ListComp, ast.GeneratorExp, ast.comprehension, ast.Lambda,
    ast.keyword,
    # operators
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow, ast.USub, ast.UAdd,
    ast.And, ast.Or, ast.Not, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Mult,
)
BANNED_CALLS = frozenset({
    "open", "exec", "eval", "compile", "getattr", "setattr", "delattr", "globals", "locals",
    "vars", "__import__", "input", "breakpoint", "exit", "quit", "help", "dir", "id", "object",
    "type", "super", "classmethod", "staticmethod", "property", "print", "memoryview",
    "bytearray", "bytes", "iter", "next", "isinstance", "issubclass", "hasattr", "callable",
})
MAX_SOURCE_BYTES = 20_000
ADMITTED_FILE = "admitted.json"
_SAFE_BUILTINS = {name: __builtins__[name] if isinstance(__builtins__, dict) else getattr(__builtins__, name)
                  for name in ("abs", "min", "max", "sum", "len", "range", "float", "int", "tuple",
                               "list", "round", "pow", "zip", "enumerate")}


class QuarantineError(Exception):
    """Raised when a staged problem fails admission or is loaded before passing."""


# --------------------------------------------------------------------------- AST check

def _collect_defined_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            names.add(node.name)
            for a in node.args.args + node.args.kwonlyargs + node.args.posonlyargs:
                names.add(a.arg)
            if node.args.vararg:
                names.add(node.args.vararg.arg)
            if node.args.kwarg:
                names.add(node.args.kwarg.arg)
        elif isinstance(node, ast.Lambda):
            for a in node.args.args:
                names.add(a.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.comprehension):
            for sub in ast.walk(node.target):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
    return names


def check_source(src: str) -> list[str]:
    """Return every violation of the HANDOFF §7 import allowlist. [] means clean."""
    problems: list[str] = []
    if not isinstance(src, str):
        return ["source is not a string"]
    if len(src.encode("utf-8", "replace")) > MAX_SOURCE_BYTES:
        return ["source too large"]
    try:
        tree = ast.parse(src, mode="exec")
    except (SyntaxError, ValueError) as exc:
        return [f"syntax error: {exc}"]

    defined = _collect_defined_names(tree)
    top_level_funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    if not any(n.name == "f" and len(n.args.args) == 2 for n in top_level_funcs):
        problems.append("no top-level `def f(t, y)` with exactly two parameters")

    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            problems.append(f"disallowed syntax: {type(node).__name__}")
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if node not in tree.body:
                problems.append("import inside a function body")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name not in ALLOWED_MODULES or alias.asname not in (None, alias.name):
                        problems.append(f"import of {alias.name!r} not allowed")
            else:
                if node.module not in ALLOWED_MODULES or node.level != 0:
                    problems.append(f"import from {node.module!r} not allowed")
                for alias in node.names:
                    if alias.name == "*" or alias.name.startswith("_"):
                        problems.append(f"import of {alias.name!r} from math not allowed")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                problems.append(f"underscore attribute access: {node.attr}")
            if not (isinstance(node.value, ast.Name) and node.value.id == "math"):
                problems.append("attribute access on something other than `math`")
        elif isinstance(node, ast.Name):
            if node.id.startswith("__") or node.id.endswith("__"):
                problems.append(f"dunder name: {node.id}")
            elif isinstance(node.ctx, ast.Load) and node.id not in defined and node.id not in ALLOWED_NAMES:
                problems.append(f"unknown global name: {node.id}")
            if node.id in BANNED_CALLS:
                problems.append(f"banned name: {node.id}")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in BANNED_CALLS:
                problems.append(f"banned call: {node.func.id}")
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, (bytes, complex)):
                problems.append("bytes/complex literal")
            if isinstance(node.value, str) and ("__" in node.value):
                problems.append("dunder in string literal")
    # deduplicate, keep order
    seen: set[str] = set()
    out: list[str] = []
    for p in problems:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


# --------------------------------------------------------------------------- staging/loading

def _safe_name(name: str) -> str:
    if not isinstance(name, str) or not name.isidentifier() or name.startswith("_") or len(name) > 40:
        raise QuarantineError(f"bad problem name: {name!r}")
    return name


def stage(name: str, src: str) -> Path:
    name = _safe_name(name)
    d = quarantine_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.py"
    path.write_text(src, encoding="utf-8")
    return path


def load_staged(name: str):
    """Load `f` from a staged source. Refuses anything that fails check_source."""
    name = _safe_name(name)
    path = quarantine_dir() / f"{name}.py"
    try:
        src = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise QuarantineError(f"not staged: {name}") from exc
    violations = check_source(src)
    if violations:
        raise QuarantineError("; ".join(violations))
    namespace: dict = {"__builtins__": dict(_SAFE_BUILTINS), "math": math}
    code = compile(src, f"<quarantine:{name}>", "exec")     # AST already validated above
    exec(code, namespace)                                     # noqa: S102 — the one sanctioned exec
    fn = namespace.get("f")
    if not callable(fn):
        raise QuarantineError("staged source did not define f")
    return fn


# --------------------------------------------------------------------------- admission

def _rk4_float(fn, y0: tuple[float, ...], t_end: float, n: int) -> tuple[tuple[float, ...], float]:
    h = t_end / n
    y = tuple(float(v) for v in y0)
    peak = max(abs(v) for v in y) if y else 0.0
    t = 0.0
    for i in range(n):
        k1 = fn(t, y)
        k2 = fn(t + h / 2, tuple(y[m] + h / 2 * k1[m] for m in range(len(y))))
        k3 = fn(t + h / 2, tuple(y[m] + h / 2 * k2[m] for m in range(len(y))))
        k4 = fn(t + h, tuple(y[m] + h * k3[m] for m in range(len(y))))
        y = tuple(y[m] + h / 6 * (k1[m] + 2 * k2[m] + 2 * k3[m] + k4[m]) for m in range(len(y)))
        t = (i + 1) * h
        peak = max(peak, max(abs(v) for v in y))
    return y, peak


def _check_spec(spec: dict) -> dict:
    required = {"n_states": int, "y0": list, "t_end": (int, float), "scale": (int, float), "family": str}
    for key, typ in required.items():
        if key not in spec or not isinstance(spec[key], typ):
            raise QuarantineError(f"spec key {key!r} missing or wrong type")
    if spec["family"] not in ("linear", "oscillatory", "nonlinear", "stiff", "geometric"):
        raise QuarantineError("bad family")
    if len(spec["y0"]) != spec["n_states"] or spec["n_states"] < 1 or spec["n_states"] > 8:
        raise QuarantineError("n_states/y0 mismatch")
    scale = float(spec["scale"])
    if scale <= 0 or math.log2(scale) != int(math.log2(scale)):
        raise QuarantineError("scale must be a power of two")
    if not (0 < float(spec["t_end"]) <= 1000):
        raise QuarantineError("t_end out of range")
    return spec


def admit(name: str, spec: dict) -> tuple[bool, list[str]]:
    """Run the six HANDOFF §7 admission checks. Returns (admitted, reasons)."""
    reasons: list[str] = []
    name = _safe_name(name)
    try:
        spec = _check_spec(spec)
    except QuarantineError as exc:
        return False, [str(exc)]
    path = quarantine_dir() / f"{name}.py"
    try:
        src = path.read_text(encoding="utf-8")
    except OSError:
        return False, ["not staged"]

    # 2. Import allowlist — AST-checked.
    violations = check_source(src)
    if violations:
        return False, [f"allowlist: {v}" for v in violations]
    try:
        fn = load_staged(name)
    except QuarantineError as exc:
        return False, [str(exc)]

    y0 = tuple(float(v) for v in spec["y0"])
    n = len(y0)
    t_end = float(spec["t_end"])
    scale = float(spec["scale"])

    def call(t: float, y: tuple[float, ...]) -> tuple[float, ...]:
        out = fn(t, y)
        if not isinstance(out, (tuple, list)) or len(out) != n:
            raise QuarantineError("f returned wrong shape")
        vals = tuple(float(v) for v in out)
        if any(not math.isfinite(v) for v in vals):
            raise QuarantineError("f returned non-finite value")
        return vals

    # 1. Determinism — run twice, byte-identical.
    try:
        probe = [(t_end * i / 97.0, tuple(y0[m] * math.cos(i + m) for m in range(n))) for i in range(100)]
        first = repr([call(t, y) for t, y in probe])
        second = repr([call(t, y) for t, y in probe])
        if first != second:
            reasons.append("determinism: two runs differ")
    except Exception as exc:  # noqa: BLE001 — model code may raise anything
        return False, [f"determinism: f raised {exc!r}"]

    # 3. Bounded time — 10,000 evaluations in < 2 seconds.
    try:
        t0 = time.perf_counter()
        for i in range(10_000):
            call(t_end * (i % 101) / 100.0, y0)
        elapsed = time.perf_counter() - t0
        if elapsed >= 2.0:
            reasons.append(f"bounded time: 10000 evaluations took {elapsed:.2f}s")
    except Exception as exc:  # noqa: BLE001
        return False, [f"bounded time: f raised {exc!r}"]

    # 4. Range — all states within [-1, 1) at 2x nominal amplitude.
    try:
        y2 = tuple(2.0 * v for v in y0)
        _, peak2 = _rk4_float(call, y2, t_end, 4000)
        if any(abs(v) >= 1.0 for v in (peak2 * scale, )):
            reasons.append(f"range: max|state|*scale at 2x = {peak2 * scale:.4f} >= 1")
    except Exception as exc:  # noqa: BLE001
        return False, [f"range: f raised {exc!r}"]

    # 5. Reference — analytic supplied, else mpmath at dps 30.
    reference = spec.get("reference")
    if reference is None:
        try:
            import mpmath as mp

            with mp.workdps(30):
                sol = mp.odefun(lambda t, y: [mp.mpf(v) for v in call(float(t), tuple(float(u) for u in y))],
                                0, [mp.mpf(v) for v in y0])
                ref_final = tuple(float(v) for v in sol(t_end))

            def reference(t: float, _final=ref_final, _sol=sol) -> tuple[float, ...]:
                if t == t_end:
                    return _final
                return tuple(float(v) for v in _sol(t))
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"reference: mpmath failed {exc!r}")
            reference = None
    elif not callable(reference):
        reasons.append("reference: not callable")
        reference = None

    # 6. Promotion gate — rk4, heun2, midpoint keep their relative order.
    if reference is not None and not reasons:
        try:
            from rk_harness import problems as _problems
            from rk_harness.costmodel import M0PLUS_FAST
            from rk_harness.evaluator import DEFAULT_BUDGET_CYCLES
            from rk_harness.simulate import solve_q15, steps_for_budget
            from rk_harness.tableau import classical

            cand = Problem(name=name, n_states=n, f=_problems.make_q15_rhs(call, scale),
                           y0=_problems.to_q15_state(y0, scale), t_end=t_end, scale=scale,
                           reference=reference, family=spec["family"])
            ref_final = tuple(reference(t_end))
            peak_ref = max(1e-9, max(abs(v) for v in ref_final))
            tabs = classical()
            new_err: dict[str, float] = {}
            for mname in ("rk4", "heun2", "midpoint"):
                tab = tabs[mname]
                steps = steps_for_budget(tab, M0PLUS_FAST, n, DEFAULT_BUDGET_CYCLES)
                final, _ = solve_q15(tab, cand, steps)
                phys = _problems.to_physical(final, scale)
                new_err[mname] = math.sqrt(sum((phys[m] - ref_final[m]) ** 2 for m in range(n))) / peak_ref
            base_err: dict[str, float] = {}
            for mname in ("rk4", "heun2", "midpoint"):
                tab = tabs[mname]
                errs = []
                for p in _problems.HELDOUT_SET:
                    steps = steps_for_budget(tab, M0PLUS_FAST, p.n_states, DEFAULT_BUDGET_CYCLES)
                    final, _ = solve_q15(tab, p, steps)
                    errs.append(_problems.error_metric(p.name, _problems.to_physical(final, p.scale)))
                base_err[mname] = math.sqrt(sum(e * e for e in errs) / len(errs))
            base_rank = sorted(base_err, key=base_err.get)
            new_rank = sorted(new_err, key=new_err.get)
            if base_rank != new_rank:
                reasons.append(f"promotion gate: baseline order {base_rank} vs new-problem order {new_rank}")
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"promotion gate: failed {exc!r}")

    if reasons:
        return False, reasons

    record = {"name": name, "n_states": n, "y0": list(y0), "t_end": t_end, "scale": scale,
              "family": spec["family"], "reference": "supplied" if spec.get("reference") else "mpmath",
              "shadow_cycles_remaining": 10}
    admitted_path = quarantine_dir() / ADMITTED_FILE
    entries = _read_admitted()
    entries = [e for e in entries if e.get("name") != name] + [record]
    tmp = admitted_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, admitted_path)
    return True, []


def _read_admitted() -> list[dict]:
    path = quarantine_dir() / ADMITTED_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [e for e in data if isinstance(e, dict) and isinstance(e.get("name"), str)] if isinstance(data, list) else []


def admitted_problems() -> tuple[Problem, ...]:
    """Admitted quarantine problems, rebuilt from admitted.json. They join HELDOUT only (runner)."""
    from rk_harness import problems as _problems

    out: list[Problem] = []
    for entry in _read_admitted():
        try:
            fn = load_staged(entry["name"])
        except QuarantineError:
            continue
        n = int(entry["n_states"])
        y0 = tuple(float(v) for v in entry["y0"])
        t_end = float(entry["t_end"])
        scale = float(entry["scale"])

        def call(t: float, y: tuple[float, ...], _fn=fn, _n=n) -> tuple[float, ...]:
            return tuple(float(v) for v in _fn(t, y))[:_n]

        try:
            import mpmath as mp

            with mp.workdps(30):
                sol = mp.odefun(lambda t, y, _c=call: [mp.mpf(v) for v in _c(float(t), tuple(float(u) for u in y))],
                                0, [mp.mpf(v) for v in y0])
                ref_final = tuple(float(v) for v in sol(t_end))
        except Exception:  # noqa: BLE001
            continue

        def reference(t: float, _final=ref_final) -> tuple[float, ...]:
            return _final

        out.append(Problem(name=entry["name"], n_states=n, f=_problems.make_q15_rhs(call, scale),
                           y0=_problems.to_q15_state(y0, scale), t_end=t_end, scale=scale,
                           reference=reference, family=entry["family"]))
    return tuple(out)
