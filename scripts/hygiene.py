"""Checkout hygiene: the rules this repository states about itself, machine-checked.

Standard library only, on purpose. The CI job that runs it does a checkout and a
setup-python and nothing else, so it starts in seconds and cannot be broken by a
dependency resolution. Nothing here imports rk_harness either: the manifest check
ast-parses `verifier_hash.py` rather than importing it, so the job stays green on a
runner that has never installed numpy.

    python scripts/hygiene.py --all
    python scripts/hygiene.py --ascii --lf --manifest --gate-expression
    python scripts/hygiene.py --gate-coverage [--write]
    python scripts/hygiene.py --workspace-copies=/path/to/workspace
    python scripts/hygiene.py --suite-desc=/path/to/rk-overview/tools/generate.py

Every check returns `(ok, detail)`. A detail beginning with "skipped:" reports a
prerequisite that is absent rather than a rule that was broken; it prints SKIP and
does not fail the run. `main` prints one line per check and returns 1 if any failed.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HARNESS = Path(__file__).resolve().parent.parent
WORKSPACE = HARNESS.parent

SKIP = "skipped: "

# Directories that are checkout noise rather than checkout content.
_PRUNE = {".git", ".venv", "__pycache__", ".pytest_cache", ".fullsend", "node_modules"}

# The five files the workspace root owns and this repo keeps a restorable copy of.
WORKSPACE_COPIES = ("start.ps1", "stop.ps1", "watcher.ps1", "stats.ps1", "configure.py")


def _walk(root: Path, suffix: str) -> list[Path]:
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _PRUNE)
        for name in sorted(filenames):
            if name.endswith(suffix):
                out.append(Path(dirpath) / name)
    return out


def _rel(p: Path, root: Path) -> str:
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return p.as_posix()


# ------------------------------------------------------------------ rule 2: ASCII PowerShell

def check_ps1_ascii(root: Path = HARNESS) -> tuple[bool, str]:
    """Every .ps1 in the tree is pure ASCII (CLAUDE.md rule 2).

    Windows PowerShell 5.1 reads a file the console codepage's way, so a smart quote or
    an em dash is not a cosmetic problem: it is a parse error, at start-up, on the host
    that runs the container. This is a byte scan rather than a parse because a PowerShell
    7 parser on a Linux runner will happily accept both.
    """
    bad: list[str] = []
    files = _walk(root, ".ps1")
    for path in files:
        text = path.read_bytes().decode("utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            for col, ch in enumerate(line, 1):
                if ord(ch) > 0x7F:
                    bad.append(f"{_rel(path, root)}:{lineno}:{col} U+{ord(ch):04X} {ch!r}")
    if bad:
        return False, ("non-ASCII in PowerShell source; 5.1 will not parse it: "
                       + "; ".join(bad[:12]) + (f" (+{len(bad) - 12} more)" if len(bad) > 12 else ""))
    return True, f"{len(files)} .ps1 files, all pure ASCII"


# ------------------------------------------------------------------ rule 3: entrypoint stays LF

def check_entrypoint_lf(path: Path | None = None) -> tuple[bool, str]:
    """entrypoint.sh has LF endings and the expected shebang (CLAUDE.md rule 3).

    A CRLF entrypoint makes the container die with `exec: no such file or directory`,
    which reads like a missing file and is not one.
    """
    path = path if path is not None else HARNESS / "entrypoint.sh"
    if not path.exists():
        return False, f"{path} is absent"
    raw = path.read_bytes()
    if not raw.startswith(b"#!/bin/sh"):
        return False, f"{path.name} does not begin with #!/bin/sh: {raw[:20]!r}"
    if b"\r" in raw:
        lines = raw.split(b"\n")
        hits = [str(i) for i, ln in enumerate(lines, 1) if b"\r" in ln]
        return False, (f"{path.name} carries CR bytes on line(s) {', '.join(hits[:12])}; "
                       "CRLF makes the container exit with 'exec: no such file or directory'")
    return True, f"{path.name}: {len(raw)} bytes, LF only, #!/bin/sh"


# ------------------------------------------------------------------ the pinned-file manifest

def read_verifier_files(harness: Path = HARNESS) -> tuple[str, ...]:
    """The VERIFIER_FILES tuple, read by ast rather than by import.

    Importing it would drag in rk_harness.paths and the environment it expects. The
    assignment is a literal by design, so parsing it is exact.
    """
    src = (harness / "rk_harness" / "verifier_hash.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
        if target == "VERIFIER_FILES" and node.value is not None:
            return tuple(ast.literal_eval(node.value))
    raise ValueError("no VERIFIER_FILES assignment in rk_harness/verifier_hash.py")


def check_verifier_manifest(harness: Path = HARNESS, manifest: Path | None = None) -> tuple[bool, str]:
    """VERIFIER_FILES.txt lists exactly the pinned tuple, in order, and all ten exist.

    The manifest is a second copy on purpose. It is not read by
    `verifier_hash.compute_verifier_hash`, so it cannot change the hash; what it changes
    is how loud the edit is. Changing the pinned set is an epoch boundary, not an edit.
    """
    manifest = manifest if manifest is not None else harness / "VERIFIER_FILES.txt"
    declared = read_verifier_files(harness)
    if not manifest.exists():
        return False, (f"{manifest.name} is absent; it must list the {len(declared)} pinned paths. "
                       "Adding or dropping a pinned file is an epoch boundary, not an edit.")
    raw = manifest.read_bytes()
    if b"\r" in raw:
        return False, f"{manifest.name} has CRLF endings; it is stored LF"
    listed = tuple(ln for ln in raw.decode("ascii", errors="replace").split("\n") if ln.strip())
    if listed != declared:
        added = [p for p in listed if p not in declared]
        dropped = [p for p in declared if p not in listed]
        order = listed != declared and not added and not dropped
        why = "order differs" if order else f"extra: {added or 'none'}; missing: {dropped or 'none'}"
        return False, (f"{manifest.name} and verifier_hash.VERIFIER_FILES disagree ({why}). "
                       "Adding or dropping a pinned file is an epoch boundary, not an edit: it "
                       "invalidates every score in the archive, so it needs a new VERIFIER_HASH "
                       "and a decision record, not a manifest fix.")
    missing = [p for p in declared if not (harness / p).exists()]
    if missing:
        return False, (f"pinned path(s) do not exist: {', '.join(missing)}. "
                       "Dropping a pinned file is an epoch boundary, not an edit.")
    if not raw.endswith(b"\n"):
        return False, f"{manifest.name} has no trailing newline"
    return True, f"{len(declared)} pinned paths, manifest and tuple identical, all present"


# ------------------------------------------------------------------ the golden gate expression

_K_RE = re.compile(r'-k\s+"([^"]*)"')


def _collapse_continuations(text: str) -> str:
    """Normalize CRLF, join backslash-continued lines, so a quoted -k argument is one string."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\\[ \t]*\n[ \t]*", " ", text)


def gate_expressions(text: str) -> list[str]:
    """Every quoted -k expression in a file, whitespace collapsed."""
    return [" ".join(m.split()) for m in _K_RE.findall(_collapse_continuations(text))]


def check_gate_expression(harness: Path = HARNESS) -> tuple[bool, str]:
    """entrypoint.sh and both copies in ci.yml select the same tests.

    Three copies of one filter. If the container's gate and CI's gate drift, CI goes green
    on a set the container never runs, which is the one failure this whole workflow exists
    to prevent.
    """
    ep = (harness / "entrypoint.sh").read_text(encoding="utf-8", errors="replace")
    ci_path = harness / ".github" / "workflows" / "ci.yml"
    ci = ci_path.read_text(encoding="utf-8", errors="replace")
    ep_exprs = gate_expressions(ep)
    ci_exprs = gate_expressions(ci)
    if len(ep_exprs) != 1:
        return False, f"entrypoint.sh has {len(ep_exprs)} quoted -k expressions, expected exactly 1"
    if len(ci_exprs) != 2:
        return False, (f"ci.yml has {len(ci_exprs)} quoted -k expressions, expected exactly 2 "
                       "(the gate job and the image job)")
    want = ep_exprs[0]
    off = [i for i, e in enumerate(ci_exprs) if e != want]
    if off:
        detail = "; ".join(f"ci.yml copy {i + 1}: {ci_exprs[i]!r}" for i in off)
        return False, f"gate drift. entrypoint.sh: {want!r}; {detail}"
    return True, f"one expression in three places, {want.count(' or ') + 1} terms"


# ------------------------------------------------------------------ what the gate collects

GOLDEN_GATE = "tests/golden_gate.txt"


def collect_gate(python: str = sys.executable, harness: Path = HARNESS) -> list[str]:
    """Node ids the container's -k expression selects, sorted.

    `-o addopts=` is not optional: pyproject already puts `-q` in addopts, and a second
    `-q` switches pytest's collect output from node ids to per-file counts.
    """
    expr = gate_expressions((harness / "entrypoint.sh").read_text(encoding="utf-8", errors="replace"))[0]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(harness)
    env["RK_SITE"] = "off"
    env["RK_LLM"] = "off"
    cmd = [python, "-m", "pytest", "tests", "--collect-only", "-q",
           "-o", "addopts=", "-p", "no:cacheprovider", "-k", expr]
    # A throwaway work dir so collection can never see, let alone touch, a real archive.
    with tempfile.TemporaryDirectory(prefix="rk-hygiene-") as tmp:
        env["RK_WORK_DIR"] = str(Path(tmp) / "work")
        env["RK_FINDINGS_DIR"] = str(Path(tmp) / "findings")
        proc = subprocess.run(cmd, cwd=harness, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env)
    ids = sorted(ln.strip().replace("\\", "/") for ln in proc.stdout.splitlines()
                 if "::" in ln and ln.strip().startswith("tests/"))
    if not ids:
        raise RuntimeError(f"collection produced no node ids (exit {proc.returncode}): "
                           f"{proc.stdout[-400:]}{proc.stderr[-400:]}")
    return ids


def check_gate_coverage(python: str = sys.executable, harness: Path = HARNESS,
                        write: bool = False) -> tuple[bool, str]:
    """The gate collects exactly the node ids recorded in tests/golden_gate.txt.

    This records the gate as it is. It is not an argument for widening it: G21-G27 and
    K7-K16 are outside the gate deliberately, because every test the gate selects is time
    the container spends before the runner starts.
    """
    golden = harness / GOLDEN_GATE
    ids = collect_gate(python, harness)
    if write:
        golden.parent.mkdir(parents=True, exist_ok=True)
        with open(golden, "wb") as fh:
            fh.write(("\n".join(ids) + "\n").encode("ascii"))
        return True, f"wrote {golden.name} with {len(ids)} node ids"
    if not golden.exists():
        return False, (f"{GOLDEN_GATE} is absent; the gate collects {len(ids)} tests. "
                       "Regenerate with `python scripts/hygiene.py --gate-coverage --write`.")
    recorded = [ln.strip() for ln in golden.read_text(encoding="ascii").splitlines() if ln.strip()]
    added = [i for i in ids if i not in recorded]
    removed = [i for i in recorded if i not in ids]
    if added or removed:
        return False, (
            f"the golden gate now collects {len(ids)} tests, {GOLDEN_GATE} records {len(recorded)}. "
            f"entered the gate: {added or 'none'}; left the gate: {removed or 'none'}. "
            "A node id that disappeared is either a rename or a test that left the gate, so read "
            "the diff before regenerating. Widening the gate is a separate decision from recording "
            "it: it lengthens container start, and this check exists to make the decision visible, "
            "not to argue for it. Regenerate with "
            "`python scripts/hygiene.py --gate-coverage --write`.")
    return True, f"{len(ids)} node ids, identical to {GOLDEN_GATE}"


# ------------------------------------------------------------------ workspace script drift

def check_workspace_copies(root: Path, harness: Path = HARNESS) -> tuple[bool, str]:
    """The restorable copies under scripts/workspace/ match the workspace root byte for byte.

    The root copies are the ones that run. The repo copies exist so the workspace can be
    rebuilt from a clone, which they can only do while they are identical. This is a HOST
    check and not a CI check: CI sees one checkout and the workspace root is a different
    repository.
    """
    root = Path(root)
    if not (root / "start.ps1").exists():
        return True, SKIP + f"no start.ps1 under {root}; the workspace root is not present here"
    drift = []
    for name in WORKSPACE_COPIES:
        live = root / name
        copy = harness / "scripts" / "workspace" / name
        if not live.exists():
            drift.append(f"{name}: absent from the workspace root")
        elif not copy.exists():
            drift.append(f"{name}: absent from scripts/workspace/")
        elif live.read_bytes() != copy.read_bytes():
            drift.append(f"{name}: root {len(live.read_bytes())} bytes, repo copy {len(copy.read_bytes())} bytes")
    if drift:
        return False, ("scripts/workspace/ has drifted from the workspace root; a restore from "
                       "these copies would produce a different harness: " + "; ".join(drift)
                       + ". Resync root -> repo, not the other way round.")
    return True, f"{len(WORKSPACE_COPIES)} workspace scripts identical to {root}"


# ------------------------------------------------------------------ cross-repo tier coverage

_SUITE_DESC_RE = re.compile(r"_SUITE_DESC\s*=\s*\{(.*?)^\}", re.S | re.M)
_TIER_KEY_RE = re.compile(r"""["'](T\d+)["']\s*:""")
_TIER_FILE_RE = re.compile(r"^test_t(\d+)_.*\.py$")


def local_tiers(harness: Path = HARNESS) -> list[str]:
    tiers = set()
    for p in (harness / "tests").glob("test_t*.py"):
        m = _TIER_FILE_RE.match(p.name)
        if m:
            tiers.add("T" + m.group(1))
    return sorted(tiers, key=lambda t: int(t[1:]))


def check_suite_desc(generate_source: str | Path, harness: Path = HARNESS) -> tuple[bool, str]:
    """Every tests/test_tN_*.py tier here has a `_SUITE_DESC` entry in rk-overview.

    The overview publishes a tier table derived from this repo's collection, so a new
    tier file with no description fails that build. Catching it here turns a broken site
    build into a named line in a CI log.
    """
    src = Path(generate_source)
    looks_like_a_path = isinstance(generate_source, Path) or str(generate_source).endswith(".py")
    if src.exists():
        text = src.read_text(encoding="utf-8", errors="replace")
        where = str(src)
    elif looks_like_a_path:
        return False, (f"{generate_source} does not exist. This is not a tier-coverage failure: "
                       "either the fetch of rk-overview's generate.py did not happen or it did "
                       "not land where this was told to look.")
    else:
        text = str(generate_source)
        where = "the text supplied by the caller"
    m = _SUITE_DESC_RE.search(text)
    if not m:
        return False, (f"no _SUITE_DESC block found in {where}. This is not a tier-coverage "
                       "failure: either the fetch returned something that is not generate.py, "
                       "or the constant was renamed in rk-overview.")
    described = set(_TIER_KEY_RE.findall(m.group(1)))
    tiers = local_tiers(harness)
    undescribed = [t for t in tiers if t not in described]
    if undescribed:
        return False, (f"tier(s) {', '.join(undescribed)} have test files here and no _SUITE_DESC "
                       f"entry in {where}. The overview build fails on this, so add a one-line "
                       "description to _SUITE_DESC in rk-overview/tools/generate.py. If the entry "
                       "is already there locally, rk-overview has not been pushed yet.")
    return True, f"{len(tiers)} tiers ({', '.join(tiers)}), all described in {where}"


# ------------------------------------------------------------------ CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="checkout hygiene: this repo's own rules, machine-checked")
    ap.add_argument("--ascii", action="store_true", help="every .ps1 is pure ASCII (rule 2)")
    ap.add_argument("--lf", action="store_true", help="entrypoint.sh is LF with the right shebang (rule 3)")
    ap.add_argument("--manifest", action="store_true", help="VERIFIER_FILES.txt matches the pinned tuple")
    ap.add_argument("--gate-expression", action="store_true", help="one -k expression in entrypoint.sh and ci.yml")
    ap.add_argument("--gate-coverage", action="store_true", help="the gate collects tests/golden_gate.txt")
    ap.add_argument("--workspace-copies", metavar="PATH", default=None,
                    help="byte-compare scripts/workspace/ against this workspace root")
    ap.add_argument("--suite-desc", metavar="PATH", default=None,
                    help="every test tier here has a _SUITE_DESC entry in this generate.py")
    ap.add_argument("--all", action="store_true", help="every check, with the workspace root and rk-overview guessed")
    ap.add_argument("--write", action="store_true", help="with --gate-coverage: regenerate tests/golden_gate.txt")
    ap.add_argument("--json", action="store_true", help="print one JSON document instead of one line per check")
    args = ap.parse_args(argv)

    plan: list[tuple[str, object]] = []
    if args.all or args.ascii:
        plan.append(("ascii", lambda: check_ps1_ascii(HARNESS)))
    if args.all or args.lf:
        plan.append(("lf", lambda: check_entrypoint_lf()))
    if args.all or args.manifest:
        plan.append(("manifest", lambda: check_verifier_manifest(HARNESS)))
    if args.all or args.gate_expression:
        plan.append(("gate-expression", lambda: check_gate_expression(HARNESS)))
    if args.all or args.gate_coverage:
        plan.append(("gate-coverage", lambda: check_gate_coverage(sys.executable, HARNESS, args.write)))
    ws = args.workspace_copies or (str(WORKSPACE) if args.all else None)
    if ws is not None:
        plan.append(("workspace-copies", lambda p=ws: check_workspace_copies(Path(p), HARNESS)))
    # --all guesses the sibling checkout; a clone without rk-overview beside it gets no row
    # rather than a failure about a file it was never asked to look at.
    guessed = WORKSPACE / "rk-overview" / "tools" / "generate.py"
    sd = args.suite_desc or (str(guessed) if (args.all and guessed.exists()) else None)
    if sd is not None:
        plan.append(("suite-desc", lambda p=sd: check_suite_desc(p, HARNESS)))

    if not plan:
        ap.error("nothing selected; pass --all or one of the check flags")

    out = []
    failed = 0
    for name, fn in plan:
        try:
            ok, detail = fn()
        except Exception as exc:                                  # noqa: BLE001
            ok, detail = False, f"exception: {exc!r}"
        status = "SKIP" if (ok and detail.startswith(SKIP)) else ("PASS" if ok else "FAIL")
        if status == "FAIL":
            failed += 1
        out.append({"check": name, "status": status, "detail": detail})
        if not args.json:
            print(f"[{status:4}] {name:17} {detail}")
    if args.json:
        print(json.dumps({"checks": out, "failed": failed}, indent=2, sort_keys=True))
    elif failed:
        print(f"\n{failed} hygiene check(s) failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
