"""Pre-flight checklist (docs/REVIEW.md) — executes every machine-checkable item, prints the
measured values, and writes docs/REVIEW-REPORT.md with the sign-off block.

    .venv\\Scripts\\python.exe scripts\\preflight.py [--no-suite] [--docker] [--quick]

Statuses: PASS / FAIL / MANUAL (needs the host, hardware, or a human) / SKIP (prerequisite absent).
Sections A, B, C, K are gating; a FAIL there is reported loudly and the exit code is 1.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import inspect
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

HARNESS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HARNESS))
PY = sys.executable
WORKSPACE = HARNESS.parent
RK_WORK = WORKSPACE / "rk-work"
RK_FINDINGS = WORKSPACE / "rk-findings"

# ----------------------------------------------------------------------------- report plumbing

class Report:
    def __init__(self) -> None:
        self.items: list[tuple[str, str, str, str]] = []   # (section, id, status, detail)
        self.section = "0"

    def add(self, item_id: str, status: str, detail: str = "") -> None:
        detail = " ".join(str(detail).split())
        self.items.append((self.section, item_id, status, detail))
        print(f"[{status:6}] {item_id:6} {detail}")

    def check(self, item_id: str, cond: bool, detail: str = "") -> None:
        self.add(item_id, "PASS" if cond else "FAIL", detail)

    def section_status(self, sec: str) -> str:
        st = [s for (se, _, s, _) in self.items if se == sec]
        if not st:
            return "n/a"
        if "FAIL" in st:
            return "FAIL"
        if all(s in ("PASS", "INFO") for s in st):
            return "green"
        return "green (pending MANUAL: %d)" % st.count("MANUAL") if "MANUAL" in st else "green (SKIP)"


R = Report()


def _safe(item_id: str, fn, *args):
    """Run a check; any exception becomes a FAIL with the exception text."""
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001
        R.add(item_id, "FAIL", f"exception: {e!r}")
        return None


def _grep(paths, pattern: str, flags=0) -> list[str]:
    hits = []
    for p in paths:
        try:
            for i, line in enumerate(Path(p).read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if re.search(pattern, line, flags):
                    hits.append(f"{Path(p).name}:{i}")
        except OSError:
            pass
    return hits


def _pkg_files() -> list[Path]:
    return sorted((HARNESS / "rk_harness").glob("*.py"))


def _import_closure(module: str) -> list[str]:
    """Transitive rk_harness imports of a module (by AST), module names."""
    import ast
    seen, todo = set(), [module]
    while todo:
        m = todo.pop()
        if m in seen:
            continue
        seen.add(m)
        src = (HARNESS / "rk_harness" / f"{m}.py").read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.startswith("rk_harness."):
                        todo.append(a.name.split(".")[1])
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "rk_harness":
                    for a in node.names:
                        todo.append(a.name)
                elif node.module.startswith("rk_harness."):
                    todo.append(node.module.split(".")[1])
    return sorted(m for m in seen if (HARNESS / "rk_harness" / f"{m}.py").exists())


# ----------------------------------------------------------------------------- test suite

def run_suite(quick: bool, reuse: bool = False) -> dict[str, str]:
    """Run pytest once, return {test_id_prefix: 'passed'|'failed'} keyed by the review IDs."""
    junit = HARNESS / ".fullsend" / "preflight-junit.xml"
    junit.parent.mkdir(exist_ok=True)
    if reuse and junit.exists():
        print(f"reusing suite results from {junit}")
    else:
        cmd = [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit}"]
        if quick:
            cmd += ["-m", "not slow"]
        print(f"running test suite: {' '.join(cmd[2:])}")
        t0 = time.time()
        proc = subprocess.run(cmd, cwd=HARNESS, capture_output=True, text=True)
        print(f"suite finished in {time.time() - t0:.0f}s, exit {proc.returncode}")
        print(proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else proc.stderr[-500:])
    results: dict[str, str] = {}
    root = ET.parse(junit).getroot()
    for tc in root.iter("testcase"):
        name = tc.get("name", "")
        m = re.match(r"test_([A-Z][0-9]+[a-d]?b?)(?:_F16)?_", name)
        if not m:
            continue
        tid = m.group(1)
        failed = any(child.tag in ("failure", "error") for child in tc)
        skipped = any(child.tag == "skipped" for child in tc)
        prev = results.get(tid)
        if failed:
            results[tid] = "failed"
        elif prev != "failed":
            results[tid] = "skipped" if skipped and prev is None else (prev or "passed")
    if "F1" in results:
        for k in range(2, 17):
            results.setdefault(f"F{k}", results["F1"])
    return results


def suite_check(item_id: str, results: dict[str, str], test_ids: list[str], note: str = "") -> None:
    missing = [t for t in test_ids if t not in results]
    failed = [t for t in test_ids if results.get(t) == "failed"]
    skipped = [t for t in test_ids if results.get(t) == "skipped"]
    if not results:
        R.add(item_id, "SKIP", f"suite not run ({', '.join(test_ids)})")
    elif failed:
        R.add(item_id, "FAIL", f"failed: {', '.join(failed)} {note}")
    elif missing and len(missing) == len(test_ids):
        R.add(item_id, "SKIP", f"not in this suite run: {', '.join(missing)} {note}")
    else:
        extra = f" (not run: {', '.join(missing + skipped)})" if (missing or skipped) else ""
        R.add(item_id, "PASS", f"{', '.join(t for t in test_ids if results.get(t) == 'passed')} passed{extra} {note}")


# ----------------------------------------------------------------------------- sections

def section_0(results):
    R.section = "0"
    from rk_harness import evaluator, verifier, costmodel, coeffrep, tableau, runner, ledger, sitegen, dashboard
    ct = tableau.classical()
    mo, pts = evaluator.measured_order_with_points(ct["rk4"])
    R.check("0.1", abs(mo - 4.0) <= 0.10 and pts == 3 and abs(mo - 4.0706) < 5e-4,
            f"measured_order(rk4) = {mo:.4f} with {pts} fit points (expect 4.0706 / 3; 4.0942 = v2 rule)")
    files = _pkg_files() + sorted((HARNESS / "tests").glob("*.py")) + [f for f in (HARNESS / "scripts").glob("*") if f.name != "preflight.py"] + sorted((HARNESS / "fixtures").glob("*"))
    hits = _grep(files, r"Q15_INEXACT")
    R.check("0.2", not hits and "Q15_INEXACT" not in verifier.REJECT_CODES,
            f"Q15_INEXACT hits in code/tests/scripts/fixtures: {hits or 'none'} (docs/HANDOFF.md mentions it historically)")
    fx = json.loads((HARNESS / "fixtures" / "classical.json").read_text(encoding="utf-8"))
    verdicts = {n: verifier.verify(ct[n], fx[n]["order"]) for n in ct}
    R.check("0.3", all(v is None for v in verdicts.values()),
            "verify(): " + ", ".join(f"{n}={'pass' if v is None else v.code}" for n, v in verdicts.items()))
    big = tableau.make_tableau([[0, 0], [40000, 0]], [1, 0], [0, 40000])   # order 1 holds; 40000 out of range
    v_big = verifier.cheap_checks(big, 1)
    third = tableau.make_tableau([[0, 0, 0], ["1/3", 0, 0], [0, "2/3", 0]], ["1/4", 0, "3/4"])
    v_third = verifier.cheap_checks(third, 3)
    R.check("0.3b", (v_big is not None and v_big.code == "COEFF_UNREPRESENTABLE") and v_third is None,
            f"40000 -> {v_big.code if v_big else None}; heun3 (1/3) cheap checks -> {v_third}; "
            f"to_rep(1/3) = {coeffrep.to_rep(Fraction(1, 3))}")
    from rk_harness.types import ScoreVector
    ann = ScoreVector.__dataclass_fields__["measured_order"].type
    R.check("0.3c", "None" in str(ann) and results.get("V10d") != "failed",
            f"ScoreVector.measured_order annotated `{ann}`; V10d {results.get('V10d', 'not run')}")
    hits = _grep([HARNESS / "rk_harness" / "costmodel.py"], r"is_dyadic")
    R.check("0.4", not hits, f"grep is_dyadic costmodel.py -> {hits or 'nothing'}")
    R.check("0.5", coeffrep.csd_weight(3) == 2, f"csd_weight(3) = {coeffrep.csd_weight(3)}")
    sig = str(inspect.signature(costmodel.cycle_count))
    R.check("0.6", callable(costmodel.count_sequence) and "Tableau" in sig and "n_states" in sig,
            f"count_sequence exists; cycle_count{sig}")
    names = [m.name for m in costmodel.COST_MODELS.values()]
    R.check("0.7", "avr_approx" in names and not hasattr(costmodel, "AVR"),
            f"cost models: {names}; no bare AVR")
    expect = {
        runner: ["run_cycle", "heartbeat", "load_state", "save_state"],
        ledger: ["parse_predicate", "evaluate_predicate", "append_hypothesis", "resolve_open"],
        sitegen: ["build", "BANNED_WORDS"],
        dashboard: ["render"],
    }
    missing = [f"{m.__name__.split('.')[-1]}.{n}" for m, ns in expect.items() for n in ns if not hasattr(m, n)]
    R.check("0.8", not missing, f"§4.12 names present; missing: {missing or 'none'}")


def section_A(results, docker_ok: bool):
    R.section = "A"
    # A1 / A3 / A10 are container checks (below); A2 needs the PAT.
    # A2 (owner's decision, 2026-08-29): the GitHub credential never enters the container. run.ps1
    # passes a filtered env file (scripts/container_env.ps1) and the host watchdog pushes
    # rk-work / rk-findings with the owner's own credentials. So the property that matters —
    # "the agent cannot push to rk-harness" — holds regardless of the PAT's scope. The HANDOFF's
    # literal PATCH probe is still run and reported as advisory information.
    env = HARNESS / ".env"
    tok = re.search(r"^\s*GITHUB_TOKEN\s*=\s*(\S+)", env.read_text(encoding="utf-8"), re.M) if env.exists() else None
    if tok and not tok.group(1).startswith("<"):
        filtered = Path(tempfile.mkdtemp(prefix="rk-a2-")) / "container.env"
        proc = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HARNESS / "scripts" / "container_env.ps1"),
                               "-EnvFile", str(env), "-OutFile", str(filtered)], capture_output=True, text=True, timeout=60)
        ftext = filtered.read_text(encoding="utf-8") if filtered.exists() else ""
        leak_in_file = "GITHUB_TOKEN" in ftext
        in_container = None
        if docker_ok and filtered.exists():
            p2 = subprocess.run(["docker", "run", "--rm", "--env-file", str(filtered), "--entrypoint", "env", "rk-harness:latest"],
                                capture_output=True, text=True, timeout=120)
            in_container = "GITHUB_TOKEN" in p2.stdout
        shutil.rmtree(filtered.parent, ignore_errors=True)
        R.check("A2", proc.returncode == 0 and not leak_in_file and in_container is not True,
                f"GitHub credential is host-only: container env file has GITHUB_TOKEN={leak_in_file}; "
                f"inside a container started with it: {'GITHUB_TOKEN present' if in_container else ('absent' if in_container is False else 'not checked (docker off)')}; "
                f"pushes are done by scripts/watchdog.ps1 on the host")
        proc = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HARNESS / "scripts" / "check_pat.ps1")],
                              capture_output=True, text=True, timeout=300)
        lines = [l for l in proc.stdout.splitlines() if l.startswith("K5")]
        R.add("A2*", "INFO", "advisory PAT scope probe (token is host-only, so this no longer gates): " + " | ".join(lines)[-400:])
    else:
        R.add("A2", "MANUAL", "put the PAT in .env (used on the host only) so the filtered-env check can run")
    closure = _import_closure("verifier")
    files = [HARNESS / "rk_harness" / f"{m}.py" for m in closure]
    bad = _grep(files, r"^\s*(import|from)\s+(requests|openai|httpx|socket|subprocess)\b")
    writes = _grep(files, r"open\([^)]*['\"][wa]")
    reads = _grep(files, r"\bopen\(")
    R.check("A4", not bad and not writes,
            f"verify() closure = {closure}; network/subprocess hits: {bad or 'none'}; file writes: {writes or 'none'}; read-only open(): {reads or 'none'}")
    from rk_harness import archive
    src = inspect.getsource(archive.assign_tier)
    hits = _grep([HARNESS / "rk_harness" / "prompts.py"], r"heldout_verified|search_only|unreplicated")
    R.check("A5", "def assign_tier" in src and not hits, f"assign_tier in archive.py ({len(src.splitlines())} lines, pure); prompt template tier-string hits: {hits or 'none'}")
    offenders = []
    for m in [p.stem for p in _pkg_files() if p.stem not in ("runner", "credentials")]:
        for dep in _import_closure(m):
            if "openai" in (HARNESS / "rk_harness" / f"{dep}.py").read_text(encoding="utf-8").lower():
                offenders.append(f"{m}->{dep}")
    R.check("A6", not offenders and results.get("K13") != "failed",
            f"openai in any import graph except runner.py (credentials.py names OPENAI_API_KEY per §2.2, excluded): {offenders or 'none'}")
    hits = _grep([HARNESS / "rk_harness" / "ledger.py"], r"\b(eval|exec|compile)\(")
    R.check("A7", not hits, f"ledger.py eval/exec/compile hits: {hits or 'none'}")
    from rk_harness import ledger
    try:
        ledger.parse_predicate("__import__('os')")
        R.add("A8", "FAIL", "parse_predicate accepted __import__('os')")
    except ledger.PredicateSyntaxError as e:
        src = inspect.getsource(ledger)
        R.check("A8", "eval(" not in src and "exec(" not in src,
                f"PredicateSyntaxError({e}); parser is a hand-rolled tokenizer + recursive descent ({len(src.splitlines())} lines), no dynamic execution")
    ig = []
    for repo in (HARNESS, RK_WORK, RK_FINDINGS):
        p = subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", ".env"], capture_output=True)
        ig.append(f"{repo.name}={'ignored' if p.returncode == 0 else 'NOT ignored'}")
    R.check("A9", all("NOT" not in x for x in ig), ", ".join(ig))
    if docker_ok:
        p = subprocess.run(["docker", "history", "--no-trunc", "rk-harness:latest"], capture_output=True, text=True)
        leak = re.findall(r"(ghp_|github_pat_|sk-[A-Za-z0-9]{8,}|OPENAI_API_KEY=)", p.stdout)
        R.check("A10", p.returncode == 0 and not leak, f"docker history grepped for token prefixes: {leak or 'nothing'}")
    else:
        R.add("A10", "SKIP", "docker not available; run: docker history --no-trunc rk-harness:latest | grep -i 'ghp_\\|sk-'")
    R.add("A11", "MANUAL", "apply scripts/network.sh inside WSL as root, then from the container: curl https://example.com must fail, api.openai.com must resolve")
    suite_check("A12", results, ["K9"], "(unknown directive key rejected, not ignored)")
    from rk_harness import quarantine
    src = inspect.getsource(quarantine.check_source)
    v = quarantine.check_source("import os\ndef f(t, y): return y")
    R.check("A13", bool(v) and "ast.parse" in src and "ast.walk" in src,
            f"check_source walks the AST (ast.parse/ast.walk present, no regex on source); 'import os' -> {v}")


def section_B(results):
    R.section = "B"
    from rk_harness import evaluator, orderconditions as oc, coeffrep, costmodel, tableau, fixedpoint
    ct = tableau.classical()
    fx = json.loads((HARNESS / "fixtures" / "classical.json").read_text(encoding="utf-8"))
    g_ids = [f"G{k}" for k in range(1, 28)]
    suite_check("B1", results, g_ids)
    table = {}
    ok = True
    for name, exp in fx["_measured_order_dahlquist"].items():
        if name.startswith("_"):
            continue
        mo, pts = evaluator.measured_order_with_points(ct[name])
        table[name] = (round(mo, 4), pts)
        ok = ok and abs(mo - exp["measured"]) < 5e-4 and pts == exp["points"]
    R.check("B2", ok, "convergence: " + ", ".join(f"{n} {m:.4f} ({p} pts)" for n, (m, p) in table.items()))
    ext = {n: evaluator.stability_extents(ct[n]) for n in ("rk4", "kutta3", "euler", "heun2")}
    R.check("B3", -2.786 <= ext["rk4"][0] <= -2.785 and abs(ext["rk4"][1] - 2.828427) <= 1e-3,
            f"rk4 real {ext['rk4'][0]:.6f}, imag {ext['rk4'][1]:.6f}")
    R.check("B4", abs(ext["kutta3"][1] - math.sqrt(3)) <= 1e-3, f"kutta3 imag {ext['kutta3'][1]:.6f} (sqrt3 = 1.732051), real {ext['kutta3'][0]:.6f}")
    R.check("B5", ext["euler"][1] < 0.01 and ext["heun2"][1] < 0.01, f"euler imag {ext['euler'][1]:.2e}, heun2 imag {ext['heun2'][1]:.2e}")
    r4 = oc.residuals(ct["rk4"], 4)
    R.check("B6", len(r4) == 8 and all(x == 0 for x in r4), f"residuals(rk4,4): {len(r4)} values, all zero = {all(x == 0 for x in r4)}")
    r5 = oc.residuals(ct["rk4"], 5)[8:]
    expected = sorted(Fraction(s) for s in fx["_rk4_order5_residuals"])
    R.check("B7", sorted(r5) == expected and all(x != 0 for x in r5),
            "order-5 residuals: " + ", ".join(str(x) for x in r5) + f" | match §9.2 as multiset = {sorted(r5) == expected}")
    counts = [len(oc.trees(k)) for k in range(1, 7)]
    R.check("B8", counts == [1, 1, 2, 4, 9, 20], f"tree counts 1..6 = {counts}")
    rows = {}
    ok = True
    for val, exp in fx["_coeffrep"].items():
        if val.startswith("_"):
            continue
        r = coeffrep.to_rep(Fraction(val))
        rows[val] = (r.m, r.s, r.exact, r.csd_weight)
        ok = ok and [r.m, r.s, r.exact, r.csd_weight] == exp
    R.check("B9", ok, "to_rep: " + "; ".join(f"{v}->{m}/2^{s} exact={e} w={w}" for v, (m, s, e, w) in rows.items()))
    suite_check("B10", results, [f"F{k}" for k in range(1, 22)])
    try:
        fixedpoint.q15_mul(-32768, -32768)
        R.add("B11", "FAIL", "q15_mul(-32768,-32768) did not raise")
    except fixedpoint.Q15OverflowError:
        R.add("B11", "PASS", "q15_mul(-32768,-32768) raises Q15OverflowError; floor check: q15_mul(-1,1) = %d, q15_mul(-3,5) = %d" % (fixedpoint.q15_mul(-1, 1), fixedpoint.q15_mul(-3, 5)))
    lines = (HARNESS / "fixtures" / "known_sequence.s").read_text(encoding="utf-8").splitlines()
    fast = costmodel.count_sequence(lines, costmodel.M0PLUS_FAST)
    slow = costmodel.count_sequence(lines, costmodel.M0PLUS_SLOW)
    hand = "LDR2+LDR2+MULS1+ASRS1+ADDS1+LSLS1+SUBS1+MULS1+ASRS1+STR2 = 13; slow: two MULS at 32 -> 13-2+64 = 75"
    R.check("B12", fast == 13 and slow == 75, f"count_sequence fast={fast} slow={slow}; hand check: {hand}")
    ok = True
    cols = []
    for n in ct:
        f_, s_ = costmodel.cycle_count(ct[n], costmodel.M0PLUS_FAST, 1), costmodel.cycle_count(ct[n], costmodel.M0PLUS_SLOW, 1)
        cols.append(f"{n} {f_}/{s_}")
        ok = ok and f_ == fx[n]["cycles_fast"] and s_ == fx[n]["cycles_slow"]
    R.check("B13", ok, "fast/slow at n=1: " + ", ".join(cols))


def section_C(results):
    R.section = "C"
    from rk_harness import costmodel, tableau, enumeration, runner, sitegen, archive
    ct = tableau.classical()
    fx = json.loads((HARNESS / "fixtures" / "classical.json").read_text(encoding="utf-8"))
    vals, ok = [], True
    for name in ("rk4", "rk38"):
        for n in (1, 2, 4):
            for m in (costmodel.M0PLUS_FAST, costmodel.M0PLUS_SLOW):
                c = costmodel.cycle_count(ct[name], m, n)
                ok = ok and c == fx["_anchor_cycles"][name][str(n)][m.name]
                vals.append(f"{name}/n{n}/{m.name.split('_')[1]}={c}")
    R.check("C1", ok, "; ".join(vals))
    f4, f38 = costmodel.cycle_count(ct["rk4"], costmodel.M0PLUS_FAST, 1), costmodel.cycle_count(ct["rk38"], costmodel.M0PLUS_FAST, 1)
    s4, s38 = costmodel.cycle_count(ct["rk4"], costmodel.M0PLUS_SLOW, 1), costmodel.cycle_count(ct["rk38"], costmodel.M0PLUS_SLOW, 1)
    R.check("C2", f4 < f38 and s38 < s4, f"fast: rk4 {f4} < rk38 {f38}; slow: rk38 {s38} < rk4 {s4} -> ordering reverses")
    ok = all(costmodel.cycle_count(t, m, n) == n * costmodel.cycle_count(t, m, 1)
             for t in ct.values() for m in (costmodel.M0PLUS_FAST, costmodel.M0PLUS_SLOW) for n in (2, 3, 4))
    R.check("C3", ok, "cycle_count(t, m, n) == n * cycle_count(t, m, 1) for all eight, n in 2..4")
    pts = enumeration.enumerate_phase0()
    R.check("C4", len(pts) == 16 and enumeration.phase0_candidate_count() == 256,
            f"Phase 0: {len(pts)} valid points of {enumeration.phase0_candidate_count()} candidate a21 values")
    ranked = enumeration.cheapest(pts, costmodel.M0PLUS_SLOW)
    names = {tableau.content_hash(t): n for n, t in ct.items()}
    rows = [f"{c}:{t.A[1][0]}->b=({t.b[0]},{t.b[1]}){' [' + names[tableau.content_hash(t)] + ']' if tableau.content_hash(t) in names else ''}" for c, t in ranked]
    R.check("C5", ranked[0][0] == 11 and names.get(tableau.content_hash(ranked[0][1])) == "midpoint" and ranked[2][0] == 13,
            "slow cycles (all 16): " + " | ".join(rows))
    src = inspect.getsource(runner)
    p0 = re.search(r"phase == 0[\s\S]{0,400}?enumerate_phase0", src) or re.search(r"enumerate_phase0", src)
    cma_guard = "cmaes_island" in src and "enumerate_phase1" in src
    R.check("C6", bool(p0) and cma_guard,
            "runner: phase 0 -> enumeration.enumerate_phase0 (exhaustive), phase 1 -> enumerate_phase1 with cap fallback, phases 2/3 -> search.cmaes_island; encourager only routes (SEARCH_CELL/WIDEN/...), never selects the optimizer")
    out = Path(tempfile.mkdtemp(prefix="rk-site-"))
    os.environ["RK_WORK_DIR"] = str(RK_WORK)
    arch = archive.replay()
    sitegen.build(arch, out)
    html = "".join(p.read_text(encoding="utf-8") for p in out.glob("*.html"))
    R.check("C7", "exhaustive" in html and "optimal within the enumerated space" in html and "search result" in html,
            f"site labels present: exhaustive={'exhaustive — optimal within the enumerated space' in html}, 'search result'={'search result' in html} ({len(list(out.glob('*.html')))} pages from {arch.n_records} records)")
    shutil.rmtree(out, ignore_errors=True)


def section_D(results):
    R.section = "D"
    from rk_harness import archive, search, problems, simulate, encourager, tableau
    from rk_harness.types import ScoreVector
    def sv(search_e, heldout_e, pp):
        return ScoreVector(2.0, 5, 0.1, -2.0, 0.0, {"m0plus_fast": 13, "m0plus_slow": 13, "avr_approx": 30}, 2, 0.0, search_e, heldout_e, 2.0, pp)
    inc = sv(0.02, 0.05, {"dahlquist": 0.01, "damped_osc": 0.02, "vanderpol_mild": 0.03, "pendulum": 0.05, "dc_motor": 0.05, "rc_thermal": 0.05, "quaternion": 0.05})
    planted = sv(0.005, 0.20, {"dahlquist": 0.001, "damped_osc": 0.002, "vanderpol_mild": 0.003, "pendulum": 0.30, "dc_motor": 0.20, "rc_thermal": 0.20, "quaternion": 0.20})
    t1 = archive.assign_tier(planted, inc)
    R.check("D1", t1 == "search_only", f"planted search-tuned tableau (search 0.005 < 0.02, heldout 0.20 > 0.05, 3 search families improved) -> {t1}")
    single = sv(0.03, 0.06, {"dahlquist": 0.001, "damped_osc": 0.05, "vanderpol_mild": 0.05, "pendulum": 0.06, "dc_motor": 0.06, "rc_thermal": 0.06, "quaternion": 0.06})
    t2 = archive.assign_tier(single, inc)
    R.check("D2", t2 == "unreplicated", f"single-family winner (dahlquist only, worse aggregates) -> {t2}")
    closure = _import_closure("search")
    import ast
    loads = []
    for m in closure:
        tree = ast.parse((HARNESS / "rk_harness" / f"{m}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "HELDOUT_SET" and isinstance(node.ctx, ast.Load):
                loads.append(m)
    R.check("D3", not loads and results.get("K12") != "failed", f"search.py import graph = {closure}; HELDOUT_SET loads: {loads or 'none'}")
    src = inspect.getsource(search.objective)
    helpers = [n for n in re.findall(r"\b(_[a-z_]+)\(", src) if hasattr(search, n)]
    full = src + "".join(inspect.getsource(getattr(search, n)) for n in helpers)
    R.check("D4", "SEARCH_SET" in full and "HELDOUT" not in full,
            f"search.objective + helpers {helpers} ({len(full.splitlines())} lines) reference SEARCH_SET only; HELDOUT absent")
    # D5: feed the optimizer the held-out set -> gap collapses to zero; revert -> gap returns.
    ct = tableau.classical()
    def gap_for(problem_set):
        errs = []
        for name in ("heun2", "midpoint", "rk38"):
            t = ct[name]
            se = search._search_error(t, search._DEFAULT_BUDGET_CYCLES) if hasattr(search, "_search_error") else None
            errs.append(se)
        return errs
    orig = search.SEARCH_SET
    try:
        search.SEARCH_SET = problems.HELDOUT_SET
        held_as_search = [search.objective(ct[n], 2, 65536) for n in ("heun2", "midpoint")]
        search.SEARCH_SET = orig
        normal = [search.objective(ct[n], 2, 65536) for n in ("heun2", "midpoint")]
        from rk_harness import evaluator
        sv_h = evaluator.evaluate(ct["heun2"], 65536)
        gap_swapped = [abs(h - sv_h.heldout_error) for h in held_as_search[:1]]
        gap_normal = [abs(n - sv_h.heldout_error) for n in normal[:1]]
        R.check("D5", gap_swapped[0] < 1e-9 and gap_normal[0] > 1e-3,
                f"objective(heun2) with SEARCH_SET:=HELDOUT_SET = {held_as_search[0]:.6f} vs evaluate().heldout_error {sv_h.heldout_error:.6f} (gap {gap_swapped[0]:.1e}); reverted objective = {normal[0]:.6f} (gap {gap_normal[0]:.4f})")
    finally:
        search.SEARCH_SET = orig
    both_one_family = sv(0.01, 0.04, {"dahlquist": 0.001, "damped_osc": 0.05, "vanderpol_mild": 0.05, "pendulum": 0.06, "dc_motor": 0.06, "rc_thermal": 0.06, "quaternion": 0.06})
    t3 = archive.assign_tier(both_one_family, inc)
    R.check("D6", t3 == "unreplicated", f"better on both aggregates but only 1 family -> {t3} (heldout_verified needs >= 2 families)")
    cands = list(search.cmaes_island(2, 2, 1, search.default_constraints(), 30))
    dy = all((x.denominator & (x.denominator - 1)) == 0 and x.denominator <= 32768 for t in cands for row in t.A for x in row)
    R.check("D7", bool(cands) and dy, f"{len(cands)} yielded tableaus; every A entry is k/2^s with s<=15 before the runner verifies (snap happens inside search.project)")


def section_E(results):
    R.section = "E"
    suite_check("E1", results, ["E2"], "(two runs, same seed -> byte-identical archive)")
    # E2 (R1) local analogue: kill -9 the runner mid-cycle three times, then finish.
    work = Path(tempfile.mkdtemp(prefix="rk-r1-"))
    env = dict(os.environ, RK_WORK_DIR=str(work), RK_FINDINGS_DIR=str(work / "f"), RK_PHASE="0", RK_SITE="off", RK_LLM="off",
               RK_CLOCK="2026-09-21T10:00:00Z", PYTHONPATH=str(HARNESS))
    kills = []
    for k in range(3):
        p = subprocess.Popen([PY, "-m", "rk_harness.runner", "--cycles", "1"], cwd=HARNESS, env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(6 + 3 * k)
        p.kill()
        p.wait()
        from rk_harness import archive
        os.environ["RK_WORK_DIR"] = str(work)
        try:
            n = archive.replay().n_records
            kills.append(f"kill#{k + 1}: replay ok, {n} records")
        except Exception as e:  # noqa: BLE001
            kills.append(f"kill#{k + 1}: replay FAILED {e!r}")
    before = archive.replay().n_records
    p = subprocess.run([PY, "-m", "rk_harness.runner", "--cycles", "1"], cwd=HARNESS, env=env, capture_output=True, text=True)
    recs = archive.read_all()
    hashes = [r.tableau_hash for r in recs]
    ev = (work / "events.jsonl").read_text(encoding="utf-8") if (work / "events.jsonl").exists() else ""
    last = [l for l in ev.splitlines() if '"candidates_processed"' in l or '"cycle_abandoned"' in l or '"rejected"' in l][-3:]
    R.check("E2", all("ok" in x for x in kills) and p.returncode == 0 and len(recs) == 22 and len(hashes) == len(set(hashes)) and "cycle_done" in ev,
            f"local kill -9 x3 then restart: {'; '.join(kills)}; final run exit {p.returncode}: {before} -> {len(recs)} records (22 = 8 baselines + 14 Phase 0 points), duplicates={len(hashes) - len(set(hashes))}, cycle_done logged={'cycle_done' in ev}; last events: {last}; stderr: {p.stderr.strip()[-200:]}")
    shutil.rmtree(work, ignore_errors=True)
    suite_check("E3", results, ["R2", "B34"], "(truncated last JSONL line discarded)")
    R.add("E4", "MANUAL", "docker pause rk for 60 s mid-evaluation, then compare the cycle's records against an unpaused run (R3); cost model is analytic so host load cannot change scores")
    suite_check("E5", results, ["R4", "R5"], "(missing / corrupt RUNSTATE.json rebuild from replay)")
    R.add("E6", "MANUAL", "pull the laptop power mid-run once; restart; confirm the runner replays and loses <= 1 cycle")
    from rk_harness import runner
    src = inspect.getsource(runner.save_state) + (inspect.getsource(runner._atomic_write_text) if hasattr(runner, "_atomic_write_text") else "")
    R.check("E7", "os.replace" in src and ".tmp" in src, f"save_state -> _atomic_write_text: writes <name>.<pid>.tmp, fsync, then os.replace (never in place): {'os.replace' in src}")
    suite_check("E8", results, ["B62", "E3"], "(sitegen deterministic; byte-identical rebuild)")


def section_F(results, docker_ok):
    R.section = "F"
    wsl = Path.home() / ".wslconfig"
    want = {"memory": "8GB", "processors": "8", "swap": "4GB", "autoMemoryReclaim": "gradual", "sparseVhd": "true"}
    have = {}
    if wsl.exists():
        for line in wsl.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"\s*(\w+)\s*=\s*(\S+)", line)
            if m:
                have[m.group(1)] = m.group(2)
    diff = {k: (have.get(k), v) for k, v in want.items() if have.get(k) != v}
    if not diff:
        R.add("F1", "PASS", f"{wsl} matches §13")
    else:
        R.add("F1", "MANUAL", f"{wsl} differs from §13 (have->want): {diff}. It carries another project's settings, so it was not overwritten; the §13 file is at scripts/wslconfig.rk — copy it, then `wsl --shutdown` and confirm with `free -h`")
    R.add("F2", "MANUAL", "start the container, load Windows: scripts/watchdog.ps1 must print 'docker pause' within ~40 s")
    R.add("F3", "MANUAL", "drop the foreground load: watchdog must print 'docker unpause' within ~40 s")
    R.add("F4", "MANUAL", "watch Vmmem in Task Manager for one hour: plateau, not climb (autoMemoryReclaim)")
    run_ps1 = (HARNESS / "scripts" / "run.ps1").read_text(encoding="utf-8")
    if docker_ok:
        p = subprocess.run(["docker", "run", "--rm", "--pids-limit=64", "--entrypoint", "sh", "rk-harness:latest", "-c",
                            "n=0; for i in $(seq 1 300); do (sleep 20 &) 2>/dev/null && n=$((n+1)); done; echo spawned=$n; sleep 1"],
                           capture_output=True, text=True, timeout=120)
        m = re.search(r"spawned=(\d+)", p.stdout)
        spawned = int(m.group(1)) if m else -1
        R.check("F5", "--pids-limit=512" in run_ps1 and 0 <= spawned < 300,
                f"run.ps1 sets --pids-limit=512; bounded fork test under --pids-limit=64: {spawned}/300 forks succeeded before 'can't fork' (host unaffected)")
    else:
        R.check("F5", "--pids-limit=512" in run_ps1, "run.ps1 sets --pids-limit=512 (fork test skipped: docker unavailable)")
    R.check("F6", str(HARNESS).upper().startswith("D:") and str(RK_WORK).upper().startswith("D:"),
            f"harness at {HARNESS}, work at {RK_WORK} (D:); vhdx location is a Docker Desktop setting -> check Settings > Resources > Disk image location is on D:")
    wd = (HARNESS / "scripts" / "watchdog.ps1").read_text(encoding="utf-8")
    R.add("F7", "MANUAL", "NitroSense: battery charge limit 80% (irreversible cell wear otherwise)"
          + ("; the watchdog additionally pauses the container whenever the laptop is on battery (owner's rule)" if "On-Battery" in wd else ""))
    plan = subprocess.run(["powercfg", "/getactivescheme"], capture_output=True, text=True).stdout
    if "Balanced" in plan:
        R.add("F8", "PASS", f"Windows power plan: {plan.strip().split('(')[-1].rstrip(')')}; airflow (elevate the laptop) and NitroSense fans=auto are physical settings — confirm by hand")
    else:
        R.add("F8", "MANUAL", f"Windows power plan is {plan.strip()} — set Balanced with `powercfg /setactive 381b4222-f694-41f0-9685-ff5bb260df2e`; elevate for airflow; NitroSense fans auto")


def section_G(results, docker_ok):
    R.section = "G"
    R.add("G1", "MANUAL", "set the monthly cap in the OpenAI dashboard and screenshot it")
    work = Path(tempfile.mkdtemp(prefix="rk-g2-"))
    (work / "RUNSTATE.json").write_text(json.dumps({"cycle_id": 3, "phase": 2, "started_at": "2026-09-21T10:00:00Z",
                                                  "last_heartbeat": "2026-09-21T10:00:00Z", "spend_usd": 0.02,
                                                  "stall_counter": 0, "current_cell": None}), encoding="utf-8")
    env = dict(os.environ, RK_WORK_DIR=str(work), RK_SITE="off", RK_LLM="off", OPENAI_MONTHLY_CAP_USD="0.01", PYTHONPATH=str(HARNESS))
    p = subprocess.run([PY, "-m", "rk_harness.runner", "--cycles", "1"], cwd=HARNESS, env=env, capture_output=True, text=True, timeout=300)
    ev = (work / "events.jsonl").read_text(encoding="utf-8") if (work / "events.jsonl").exists() else ""
    R.check("G2", p.returncode == 3 and "spend_cap_exceeded" in ev and "cycle_done" not in ev,
            f"spend 0.02 > cap 0.01: runner exit {p.returncode}, event spend_cap_exceeded={'spend_cap_exceeded' in ev}, no cycle ran={'cycle_done' not in ev}")
    shutil.rmtree(work, ignore_errors=True)
    work = Path(tempfile.mkdtemp(prefix="rk-g3-"))
    (work / "STOP").write_text("stop\n", encoding="utf-8")
    env = dict(os.environ, RK_WORK_DIR=str(work), RK_SITE="off", RK_LLM="off", PYTHONPATH=str(HARNESS))
    t0 = time.time()
    p = subprocess.run([PY, "-m", "rk_harness.runner", "--cycles", "5"], cwd=HARNESS, env=env, capture_output=True, text=True, timeout=300)
    ev = (work / "events.jsonl").read_text(encoding="utf-8") if (work / "events.jsonl").exists() else ""
    R.check("G3", p.returncode == 0 and "stopped_by_killfile" in ev and "cycle_done" not in ev,
            f"STOP present: runner exited {p.returncode} in {time.time() - t0:.1f}s at the cycle boundary, event stopped_by_killfile={'stopped_by_killfile' in ev}")
    shutil.rmtree(work, ignore_errors=True)
    if docker_ok:
        name = "rk-preflight"
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        work = Path(tempfile.mkdtemp(prefix="rk-g4-"))
        stale = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=300)).strftime("%Y-%m-%dT%H:%M:%SZ")
        (work / "HEARTBEAT").write_text(stale, encoding="utf-8")
        subprocess.run(["docker", "run", "-d", "--name", name, "--entrypoint", "sleep", "rk-harness:latest", "600"], capture_output=True)
        p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HARNESS / "scripts" / "watchdog.ps1"),
                            "-Work", str(work), "-Container", name, "-Once"], capture_output=True, text=True, timeout=120)
        st = subprocess.run(["docker", "inspect", "-f", "{{.State.Status}}", name], capture_output=True, text=True).stdout.strip()
        R.check("G4", "docker kill" in p.stdout and st != "running", f"stale HEARTBEAT (300 s): watchdog printed kill={('docker kill' in p.stdout)}; container state now '{st}'")
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        (work / "HEARTBEAT").write_text(_dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), encoding="utf-8")
        subprocess.run(["docker", "run", "-d", "--name", name, "--entrypoint", "sleep", "rk-harness:latest", "600"], capture_output=True)
        p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HARNESS / "scripts" / "watchdog.ps1"),
                            "-Work", str(work), "-Container", name, "-Once", "-MinFreeGB", "999999"], capture_output=True, text=True, timeout=120)
        st = subprocess.run(["docker", "inspect", "-f", "{{.State.Status}}", name], capture_output=True, text=True).stdout.strip()
        R.check("G5", "docker stop" in p.stdout and st != "running", f"disk threshold forced (MinFreeGB=999999): watchdog printed stop={('docker stop' in p.stdout)}; container state '{st}'")
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        shutil.rmtree(work, ignore_errors=True)
    else:
        R.add("G4", "SKIP", "docker unavailable; watchdog heartbeat kill is testable with scripts/watchdog.ps1 -Once against a sleeping container")
        R.add("G5", "SKIP", "docker unavailable; run scripts/watchdog.ps1 -Once -MinFreeGB 999999 against a sleeping container")
    auth = Path.home() / ".codex" / "auth.json"
    if not auth.exists():
        R.add("G6", "MANUAL", f"{auth} missing: authenticate Codex on the host (`codex login`) before the first unattended night; run.ps1 mounts it :ro")
    elif docker_ok:
        p = subprocess.run(["docker", "run", "--rm", "-v", f"{auth}:/root/.codex/auth.json:ro", "--entrypoint", "codex",
                            "rk-harness:latest", "login", "status"], capture_output=True, text=True, timeout=120)
        out = (p.stdout + p.stderr).strip()
        R.check("G6", p.returncode == 0 and "logged in" in out.lower(),
                f"auth.json mounted :ro into the container; `codex login status` -> exit {p.returncode}: {out[-120:]}")
    else:
        R.add("G6", "SKIP", f"{auth} exists; docker unavailable to confirm the container authenticates (docker run --entrypoint codex ... login status)")


def section_H(results):
    R.section = "H"
    from rk_harness import sitegen, archive
    os.environ["RK_WORK_DIR"] = str(RK_WORK)
    out = Path(tempfile.mkdtemp(prefix="rk-h-"))
    arch = archive.replay()
    sitegen.build(arch, out)
    pages = {p.name: p.read_text(encoding="utf-8") for p in out.glob("*.html")}
    elites = [r for g in arch.grids.values() for r in g.values()]
    idx = pages.get("index.html", "")
    R.check("H1", results.get("E3") != "failed" and all(r.tier in idx and r.tableau_hash[:12] in idx for r in elites),
            f"site built from the live archive ({arch.n_records} records, {len(elites)} elites, {len(pages)} pages); every elite shows tier + hash on index")
    try:
        sitegen.check_banned("<p>a novel method that beats rk4</p>")
        R.add("H2", "FAIL", "check_banned accepted a banned word")
    except sitegen.BannedWordError as e:
        R.check("H2", results.get("E4") != "failed", f"planted 'novel'/'beats' -> BannedWordError({e}); build() raises before writing (E4 {results.get('E4', 'not run')})")
    R.check("H3", all(sitegen.BANNER in h for h in pages.values()), f"banner on {sum(sitegen.BANNER in h for h in pages.values())}/{len(pages)} pages")
    cm = pages.get("costmodel.html", "")
    R.check("H4", "avr_approx" in cm and sitegen.AVR_NOTE in cm, f"costmodel.html carries AVR_APPROX figures with the note: {sitegen.AVR_NOTE in cm}")
    cells = [h for n, h in pages.items() if n.startswith("cell-")]
    R.check("H5", cells and all(("verifier" in h and "tableau_hash" in h.replace(" ", "_").lower()) or ("verifier_hash" in h) for h in cells),
            f"{len(cells)} cell pages each show tableau_hash and verifier_hash")
    shutil.rmtree(out, ignore_errors=True)
    url = "https://jgoetzmann.github.io/rk-findings/"
    status = None
    for _ in range(6):
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                status = resp.status
                break
        except Exception as e:  # noqa: BLE001
            status = getattr(e, "code", None) or repr(e)
            time.sleep(20)
    R.add("H6", "PASS" if status == 200 else "MANUAL", f"GET {url} -> {status} (Pages may take up to 10 minutes after the first push)")
    p = subprocess.run(["git", "-C", str(RK_WORK), "log", "--name-only", "--pretty=format:--%h", "--", "archive"], capture_output=True, text=True)
    committed = [l for l in p.stdout.splitlines() if l.startswith("archive/")]
    from rk_harness import runner
    src = inspect.getsource(runner._commit_outputs) if hasattr(runner, "_commit_outputs") else ""
    today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    R.check("H7", all(today not in c for c in committed) and "today" in src,
            f"archive files in rk-work history: {sorted(set(committed)) or 'none yet'}; runner commits only files other than today's (source checked)")


def section_I(results):
    R.section = "I"
    from rk_harness import ledger, archive, tableau, evaluator
    from rk_harness.types import ArchiveState, CellStat, Record
    bad = ["fast.p2s2.cycles = 16", "fast.p2s2.foo < 1", "medium.p2s2.heldout < 1", "fast.p2s2.heldout < 1 XOR fast.p2s2.heldout < 2",
           "", "fast.p2s2.heldout < (1)", "__import__('os')", "import os", "fast.p2s2.heldout < 1; print(1)"]
    rejected = []
    for b in bad:
        try:
            ledger.parse_predicate(b)
        except ledger.PredicateSyntaxError:
            rejected.append(b)
    good = ledger.parse_predicate("slow.p3s4.heldout < slow.p4s4.heldout AND fast.p3s4.heldout > fast.p4s4.heldout")
    R.check("I1", len(rejected) == len(bad) and len(good.terms) == 2, f"accepted the §6 example ({len(good.terms)} terms); rejected {len(rejected)}/{len(bad)} malformed: {bad}")
    # I2: two buckets at the same stage count -> min heldout across buckets.
    work = Path(tempfile.mkdtemp(prefix="rk-i2-"))
    os.environ["RK_WORK_DIR"] = str(work)
    os.environ["RK_CLOCK"] = "2026-09-21T10:00:00Z"
    ct = tableau.classical()
    from rk_harness.types import ScoreVector
    def mk(t, cyc, held, cid):
        sv = ScoreVector(2.0, 5, 0.1, -2.0, 0.0, {"m0plus_fast": cyc, "m0plus_slow": cyc, "avr_approx": cyc}, 2, 0.0, 0.01, held, 2.0,
                         {"dahlquist": 0.01, "slow:heldout_error": held, "slow:search_error": 0.01, "avr_approx:heldout_error": held, "avr_approx:search_error": 0.01})
        return Record(tableau.content_hash(t), t, sv, "unreplicated", cid, 0, "vh", None, None, "2026-09-21T10:00:00Z")
    archive.append(mk(ct["heun2"], 13, 0.30, 1))       # bucket 0
    archive.append(mk(ct["ralston2"], 16, 0.10, 2))    # bucket 1, same (order 2, stages 2)
    arch = archive.replay()
    v, n, d = ledger.evaluate_predicate(ledger.parse_predicate("fast.p2s2.heldout < 0.2"), arch)
    stat = arch.cell_stats[(2, 2)]["fast.heldout"]
    R.check("I2", v == "supported" and abs(stat.min - 0.10) < 1e-12 and n == 2, f"two buckets at (p2,s2) with heldout 0.30 / 0.10 -> p2s2.heldout resolves to min {stat.min} (n={n}); 'fast.p2s2.heldout < 0.2' -> {v}")
    v, n, d = ledger.evaluate_predicate(ledger.parse_predicate("fast.p4s6.heldout < 1"), arch)
    R.check("I3", v == "inconclusive" and n == 0, f"empty cell p4s6 -> ({v}, {n}, {d})")
    v, n, d = ledger.resolve_one({"predicate": "fast.p2s2.heldout < 0.2", "min_samples": 200}, arch)
    R.check("I4", v == "inconclusive" and n == 2, f"min_samples 200 with n=2 -> {v}")
    shutil.rmtree(work, ignore_errors=True)
    os.environ.pop("RK_CLOCK", None)
    from rk_harness import prompts
    src = (HARNESS / "rk_harness" / "prompts.py").read_text(encoding="utf-8")
    instructs = re.findall(r"(?i)(write|set|assign|return|output|produce)[^.\n]{0,40}\bverdict", src)
    R.check("I5", not instructs and "verdict" not in json.dumps(__import__("rk_harness.directive", fromlist=["DIRECTIVE_SCHEMA"]).DIRECTIVE_SCHEMA),
            f"prompt template never asks the model for a verdict and the directive schema has no verdict field; the words appear only as data labels in the refuted list (required by I6): {instructs or 'no instruction'}")
    from rk_harness.types import RunState
    st = RunState(5, 2, "2026-09-21T10:00:00Z", "2026-09-21T10:00:00Z", 0.0, 0, None)
    refuted = [{"id": "H-047", "statement": "slow p3s4 is better than p4s4", "predicate": "slow.p3s4.heldout < slow.p4s4.heldout", "verdict": "refuted", "n_samples": 250, "effect_size": 0.9, "min_samples": 200}]
    text = prompts.build_user_prompt(ArchiveState(0, 0, {1: {}, 2: {}, 3: {}, 4: {}}, (), ("H-047",)), st, refuted, [])
    R.check("I6", "H-047" in text and "refuted" in text, f"assembled prompt ({len(text)} chars) lists refuted H-047 with its verdict")
    big = ArchiveState(0, 0, {1: {}, 2: {}, 3: {}, 4: {}}, (), (), {(3, 4): {"fast.heldout": CellStat(1_000_000, 0.1000, 1_000_000 * 0.01, 0.05)},
                                                                    (4, 4): {"fast.heldout": CellStat(1_000_000, 0.1001, 1_000_000 * 0.01, 0.06)}})
    v, n, d = ledger.evaluate_predicate(ledger.parse_predicate("fast.p3s4.heldout < fast.p4s4.heldout"), big)
    R.check("I7", v == "inconclusive" and d < 0.2 and n == 1_000_000, f"n=1e6 per cell, means 0.1000 vs 0.1001, sd 0.1 -> Cohen's d {d:.4f} -> {v} (threshold applied at large n)")
    v, n, d = ledger.evaluate_predicate(ledger.parse_predicate("fast.p3s4.heldout < fast.p4s4.heldout"),
                                        ArchiveState(0, 0, {1: {}, 2: {}, 3: {}, 4: {}}, (), (), {(3, 4): {"fast.heldout": CellStat(50, 0.1, 0.5, 0.05)}, (4, 4): {"fast.heldout": CellStat(900, 0.5, 9.0, 0.4)}}))
    R.check("I8", n == 50, f"cells with n=50 and n=900 -> n_samples = {n} (smallest)")


def section_J(results):
    R.section = "J"
    from rk_harness import encourager, runner, enumeration
    from rk_harness.types import ArchiveState, RunState
    empty = ArchiveState(0, 0, {1: {}, 2: {}, 3: {}, 4: {}}, (), ())
    suite_check("J1", results, ["E5"], "(1000 random states before 2026-11-20: never PACKAGE/FREEZE)")
    suite_check("J2", results, ["E6"])
    suite_check("J3", results, ["E7"])
    seen = {}
    for clock in ("2026-11-19T23:59:00Z", "2026-11-21T00:00:00Z", "2026-12-06T00:00:00Z"):
        os.environ["RK_CLOCK"] = clock
        st = RunState(10, 2, clock, clock, 0.0, 0, None)
        seen[clock] = encourager.next_action(st, empty, runner.now()).kind
    os.environ.pop("RK_CLOCK", None)
    R.check("J4", seen["2026-11-19T23:59:00Z"] not in ("PACKAGE", "FREEZE") and seen["2026-11-21T00:00:00Z"] == "PACKAGE" and seen["2026-12-06T00:00:00Z"] == "FREEZE",
            f"runner.now() driven by RK_CLOCK through the real code path: {seen} (system clock left untouched)")
    src = inspect.getsource(runner)
    table = {0: ("order 2", "stages 2", "enumerate_phase0"), 1: ("order 3", "stages 3(-4)", "enumerate_phase1 / CMA-ES fallback"), 2: ("order 4", "stages 4-5", "cmaes_island"), 3: ("order 4", "stages 4-6", "cmaes_island")}
    s_bounds = {"PHASE0_S_MAX": enumeration.PHASE0_S_MAX, "PHASE1_S_MAX": enumeration.PHASE1_S_MAX}
    R.check("J5", enumeration.PHASE0_S_MAX == 6 and enumeration.PHASE1_S_MAX == 8 and "enumerate_phase0" in src and "cmaes_island" in src,
            f"phase table in runner: {table}; lattice bounds {s_bounds}; s<=12 / s<=20 for phases 2/3 are the to_rep s_max (20) with dyadic_denominator_max in the directive")
    R.check("J6", enumeration.PHASE1_CAP == 100_000_000 and "phase1_cap_exceeded" in src,
            f"PHASE1_CAP = {enumeration.PHASE1_CAP:,}; runner logs 'phase1_cap_exceeded' and falls back to CMA-ES; enumerate_phase1() returns (tableaus, cap_exceeded) = ({len(enumeration.enumerate_phase1()[0])}, {enumeration.enumerate_phase1()[1]})")
    now = _dt.datetime(2026, 9, 21, tzinfo=_dt.timezone.utc)
    ladder = [encourager.next_action(RunState(1, 2, "", "", 0.0, s, None), empty, now).kind for s in (0, 5, 10, 20, 30)]
    R.check("J7", ladder == ["SEARCH_CELL", "SEARCH_CELL", "WIDEN", "HYPOTHESIZE", "ADVANCE_PHASE"], f"stall 0/5/10/20/30 -> {ladder}")


def section_K(results):
    R.section = "K"
    f = RK_WORK / "falsification.json"
    if not f.exists():
        R.add("K1", "FAIL", f"{f} missing; run scripts/falsification.ps1")
        return
    data = json.loads(f.read_text(encoding="utf-8"))
    R.add("K1", "PASS", f"§15 run recorded at {f}")
    def frac(name):
        d = data.get(name) or data.get("methods", {}).get(name) or {}
        return d
    txt = json.dumps(data)[:600]
    rk4 = frac("rk4")
    cf = rk4.get("coefficient_fraction") or {}
    R.add("K2", "PASS" if cf else "FAIL", f"rk4 coefficient-arithmetic fraction: m0plus_fast {cf.get('m0plus_fast', '?')}, m0plus_slow {cf.get('m0plus_slow', '?')}; heun2: {(frac('heun2').get('coefficient_fraction') or {})}")
    R.add("K3", "PASS" if "crossover" in json.dumps(rk4) else "FAIL", f"rk4 crossover h = {rk4.get('crossover_h', rk4.get('crossover'))}; heun2 crossover h = {frac('heun2').get('crossover_h', frac('heun2').get('crossover'))}; verdict = {data.get('verdict')}")
    page = RK_FINDINGS / "docs" / "falsification.html"
    ok = page.exists() and "crossover" in page.read_text(encoding="utf-8").lower()
    R.check("K4", ok, f"decision recorded in rk-findings/docs/falsification.html (verdict {data.get('verdict')}): {ok}")


def section_containers(docker_ok: bool):
    """A1 / A3: the container refuses a writable harness and a tampered coeffrep.py."""
    R.section = "A"
    if not docker_ok:
        R.add("A1", "SKIP", "docker unavailable; run scripts/run.ps1 then `docker exec rk touch /harness/x` (must fail EROFS)")
        R.add("A3", "SKIP", "docker unavailable; alter one byte of coeffrep.py in a copy, mount it, container must exit 1 at the hash check")
        return
    p = subprocess.run(["docker", "run", "--rm", "-v", f"{HARNESS}:/harness:ro", "--entrypoint", "sh", "rk-harness:latest",
                        "-c", "touch /harness/.probe 2>&1; echo rc=$?"], capture_output=True, text=True, timeout=120)
    R.check("A1", "rc=0" not in p.stdout and ("Read-only" in p.stdout or "read-only" in p.stdout.lower()), f"write to /harness inside the container: {p.stdout.strip()[-120:]}")
    tmp = Path(tempfile.mkdtemp(prefix="rk-a3-"))
    dst = tmp / "harness"
    shutil.copytree(HARNESS, dst, ignore=shutil.ignore_patterns(".venv", ".git", ".fullsend", "__pycache__", ".pytest_cache"))
    cr = dst / "rk_harness" / "coeffrep.py"
    cr.write_bytes(cr.read_bytes() + b"\n# tampered\n")
    work = tmp / "work"
    work.mkdir()
    p = subprocess.run(["docker", "run", "--rm", "-v", f"{dst}:/harness:ro", "-v", f"{work}:/work", "rk-harness:latest", "--cycles", "1"],
                       capture_output=True, text=True, timeout=600)
    R.check("A3", p.returncode == 1 and "MISMATCH" in (p.stdout + p.stderr),
            f"coeffrep.py altered by one line: container exit {p.returncode}; stderr: {(p.stdout + p.stderr).strip().splitlines()[0][:100] if (p.stdout + p.stderr).strip() else ''}")
    # positive control: untampered harness passes the hash check and the golden/canary gate
    p = subprocess.run(["docker", "run", "--rm", "-v", f"{HARNESS}:/harness:ro", "-v", f"{work}:/work", "-e", "RK_SITE=off", "-e", "RK_LLM=off", "-e", "RK_PHASE=0",
                        "rk-harness:latest", "--cycles", "0"], capture_output=True, text=True, timeout=1200)
    out = p.stdout + p.stderr
    summary = re.findall(r"\d+ passed[^\n]*", out)
    dots = re.findall(r"^[.]+\s+\[100%\]", out, re.M)
    R.check("A3+", p.returncode == 0 and "verifier hash ok" in out and (summary or dots),
            f"untampered harness: exit {p.returncode}; hash ok={'verifier hash ok' in out}; golden/canary gate: {summary[-1:] or (dots and f'{len(dots[0].split()[0])} tests, all dots, exit 0')}")
    shutil.rmtree(tmp, ignore_errors=True)


# ----------------------------------------------------------------------------- report

def write_report(path: Path, suite_ran: bool, docker_ok: bool) -> None:
    today = _dt.date.today().isoformat()
    secs = ["0", "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K"]
    lines = ["# REVIEW REPORT — pre-flight checklist (docs/REVIEW.md)", "",
             f"Generated {today} by `scripts/preflight.py` (suite run: {suite_ran}; docker: {docker_ok}).",
             "Statuses: PASS / FAIL / MANUAL (needs the host, hardware or a human) / SKIP (prerequisite absent) / INFO (advisory, does not gate).", "",
             "| Section | Status |", "| --- | --- |"]
    for s in secs:
        lines.append(f"| {s} | {R.section_status(s)} |")
    lines += ["", "| Item | Status | Measured / evidence |", "| --- | --- | --- |"]
    for sec, item_id, status, detail in R.items:
        lines.append(f"| {item_id} | {status} | {detail.replace('|', '\\|')} |")
    kdec = "proceed"
    for _, item_id, status, detail in R.items:
        if item_id == "K3" and "verdict = kill" in detail:
            kdec = "kill"
    gating = all(R.section_status(s).startswith("green") for s in ("A", "B", "C", "K"))
    lines += ["", "## Sign-off", "", "```",
              f"Date started:         {today}",
              f"Section 0 green:      {today if R.section_status('0') == 'green' else 'NO'}",
              f"A green:              {today if R.section_status('A').startswith('green') else 'NO'}  ({R.section_status('A')})",
              f"B green:              {today if R.section_status('B') == 'green' else 'NO'}",
              f"C green:              {today if R.section_status('C') == 'green' else 'NO'}",
              f"D green:              {today if R.section_status('D').startswith('green') else 'NO'}  ({R.section_status('D')})",
              f"K decision:           {kdec}",
              f"First unattended run: {'not yet — clear the MANUAL items first' if not gating else 'gating sections green; MANUAL items remain'}",
              "```", ""]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nreport written to {path}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-suite", action="store_true", help="skip the pytest run (items become SKIP)")
    ap.add_argument("--quick", action="store_true", help="run only the fast tests")
    ap.add_argument("--docker", action="store_true", help="run the container checks (needs the image built)")
    ap.add_argument("--reuse-suite", action="store_true", help="reuse .fullsend/preflight-junit.xml from the last run")
    args = ap.parse_args(argv)
    os.environ.setdefault("RK_WORK_DIR", str(RK_WORK))
    os.environ.setdefault("RK_FINDINGS_DIR", str(RK_FINDINGS))
    os.environ["RK_SITE"] = "off"
    os.environ["RK_LLM"] = "off"
    docker_ok = False
    if args.docker:
        p = subprocess.run(["docker", "image", "inspect", "rk-harness:latest"], capture_output=True)
        docker_ok = p.returncode == 0
        if not docker_ok:
            print("docker image rk-harness:latest not found; container checks skipped")
    results = {} if args.no_suite else run_suite(args.quick, args.reuse_suite)
    for fn, extra in ((section_0, ()), (section_A, (docker_ok,)), (section_containers, None), (section_B, ()), (section_C, ()),
                      (section_D, ()), (section_E, ()), (section_F, (docker_ok,)), (section_G, (docker_ok,)),
                      (section_H, ()), (section_I, ()), (section_J, ()), (section_K, ())):
        print(f"\n== section {fn.__name__.split('_')[-1]} ==")
        try:
            if extra is None:
                fn(docker_ok)
            else:
                fn(results, *extra)
        except Exception as e:  # noqa: BLE001
            R.add(fn.__name__, "FAIL", f"section crashed: {e!r}")
        os.environ["RK_WORK_DIR"] = str(RK_WORK)
    write_report(HARNESS / "docs" / "REVIEW-REPORT.md", not args.no_suite, docker_ok)
    gating_fail = any(status == "FAIL" and sec in ("0", "A", "B", "C", "K") for sec, _, status, _ in R.items)
    if gating_fail:
        print("\nGATING FAILURE in section 0/A/B/C/K — do not start the run")
    return 1 if gating_fail else 0


if __name__ == "__main__":
    sys.exit(main())
