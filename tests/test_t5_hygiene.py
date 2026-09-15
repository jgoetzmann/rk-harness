"""Checkout hygiene and the acceptance-evidence plumbing: scripts/hygiene.py,
scripts/merge_junit.py, and preflight's evidence-dated report.

Ids C30-C39. Two of these are tripwires rather than ordinary tests. C33 pins the exact node
ids the container's `-k` gate collects and C35 byte-compares the shipped workspace scripts
against the workspace root, so both go red the moment either moves. That is the point: the
gate's collected set and the restorable copies are things you want to hear about, not things
you want to discover from a container that will not start.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent.parent
WORKSPACE = HARNESS.parent
SCRIPTS = HARNESS / "scripts"


def _load(name: str, path: Path):
    """Import a scripts/ module by path; scripts/ is not a package and is not on sys.path."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


hygiene = _load("rk_hygiene_under_test", SCRIPTS / "hygiene.py")
merge_junit = _load("rk_merge_junit_under_test", SCRIPTS / "merge_junit.py")


# ------------------------------------------------------------------------------------ C30

def test_C30_every_powershell_file_is_pure_ascii(tmp_path):
    ok, detail = hygiene.check_ps1_ascii(HARNESS)
    assert ok, detail
    assert "pure ASCII" in detail

    # A planted em dash and a right single quote: both parse on PowerShell 7 and neither
    # parses on the 5.1 the host runs, which is why this is a byte scan and not a parser.
    bad = tmp_path / "scripts" / "planted.ps1"
    bad.parent.mkdir(parents=True)
    bad.write_text("Write-Host 'ok'\nWrite-Host 'a \u2014 b'\nWrite-Host 'it\u2019s'\n", encoding="utf-8")
    ok, detail = hygiene.check_ps1_ascii(tmp_path)
    assert not ok
    assert "scripts/planted.ps1:2:15" in detail, detail       # file, line, column
    assert "U+2014" in detail and "U+2019" in detail, detail   # the code points, named


# ------------------------------------------------------------------------------------ C31

def test_C31_entrypoint_sh_has_no_carriage_returns(tmp_path):
    ok, detail = hygiene.check_entrypoint_lf()
    assert ok, detail
    raw = (HARNESS / "entrypoint.sh").read_bytes()
    assert raw.startswith(b"#!/bin/sh")
    assert b"\r" not in raw

    crlf = tmp_path / "entrypoint.sh"
    crlf.write_bytes(raw.replace(b"\n", b"\r\n"))
    ok, detail = hygiene.check_entrypoint_lf(crlf)
    assert not ok
    assert "CR bytes" in detail and "exec: no such file or directory" in detail, detail

    headless = tmp_path / "headless.sh"
    headless.write_bytes(b"#!/bin/bash\necho hi\n")
    ok, detail = hygiene.check_entrypoint_lf(headless)
    assert not ok and "#!/bin/sh" in detail


# ------------------------------------------------------------------------------------ C32

def test_C32_the_golden_gate_expression_is_identical_in_entrypoint_and_ci():
    ok, detail = hygiene.check_gate_expression(HARNESS)
    assert ok, detail

    ep = hygiene.gate_expressions((HARNESS / "entrypoint.sh").read_text(encoding="utf-8"))
    ci = hygiene.gate_expressions((HARNESS / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    assert len(ep) == 1 and len(ci) == 2
    assert ep[0] == ci[0] == ci[1]
    assert ep[0].startswith("G1_ or ") and ep[0].endswith(" or K2_")

    # CRLF must be normalized before matching, or the container's own file never matches.
    crlf = hygiene.gate_expressions((HARNESS / "entrypoint.sh").read_text(encoding="utf-8").replace("\n", "\r\n"))
    assert crlf == ep

    # A planted third variant is caught: one term dropped from one of the two ci.yml copies.
    text = (HARNESS / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    planted = text.replace("G20_ or K1_ or K2_", "G20_ or K1_", 1)
    assert planted != text
    variants = hygiene.gate_expressions(planted)
    assert len(set(variants)) == 2, variants


# ------------------------------------------------------------------------------------ C33

@pytest.mark.slow
def test_C33_the_golden_gate_collects_the_declared_tests():
    ok, detail = hygiene.check_gate_coverage(sys.executable, HARNESS)
    assert ok, detail

    recorded = [ln.strip() for ln in
                (HARNESS / hygiene.GOLDEN_GATE).read_text(encoding="ascii").splitlines() if ln.strip()]
    assert recorded == sorted(recorded), "golden_gate.txt is stored sorted"
    prefixes = {re.sub(r"^test_([GK][0-9]+)_.*", r"\1", nid.split("::")[-1]) for nid in recorded}
    # Only the prefixes the gate expression is meant to select. G21-G27 and K7-K16 exist in
    # the suite and are deliberately outside the gate, so this asserts coverage of the gate's
    # intended set and never of every prefix under tests/.
    meant = {f"G{n}" for n in range(1, 21)} | {"K1", "K2"}
    assert prefixes == meant, (
        f"the gate is meant to select exactly {sorted(meant)}; golden_gate.txt records "
        f"{sorted(prefixes)}. G21-G27 and K7-K16 are outside the gate on purpose.")

    # The failure message has to name what moved, in both directions, and keep recording the
    # gate apart from widening it. Driven through collect_gate rather than by editing the
    # checked-in golden file: a test that rewrites repository state is a test that can leave
    # it rewritten.
    planted = "tests/test_t2_order_eval_verify.py::test_G9_planted_by_C33"
    monkey = sorted(recorded[1:] + [planted])
    real = hygiene.collect_gate
    hygiene.collect_gate = lambda python=None, harness=None: list(monkey)
    try:
        ok, detail = hygiene.check_gate_coverage(sys.executable, HARNESS)
    finally:
        hygiene.collect_gate = real
    assert not ok
    assert "entered the gate" in detail and "left the gate" in detail, detail
    assert planted in detail and recorded[0] in detail, detail
    assert "separate decision" in detail and "lengthens container start" in detail, detail


# ------------------------------------------------------------------------------------ C34

def test_C34_verifier_files_manifest_matches_the_pinned_tuple(tmp_path):
    declared = hygiene.read_verifier_files(HARNESS)
    assert len(declared) == 10
    manifest = HARNESS / "VERIFIER_FILES.txt"
    raw = manifest.read_bytes()
    assert b"\r" not in raw and raw.endswith(b"\n")
    assert tuple(raw.decode("ascii").split("\n")[:-1]) == declared
    for rel in declared:
        assert (HARNESS / rel).exists(), rel

    ok, detail = hygiene.check_verifier_manifest(HARNESS)
    assert ok, detail

    # The manifest is a second copy and nothing more: it is not read by verifier_hash, so it
    # cannot move the pinned hash. Asserted three ways, because "adding a file beside
    # VERIFIER_HASH is safe" is exactly the sort of claim that has to be checked, not assumed.
    src = (HARNESS / "rk_harness" / "verifier_hash.py").read_text(encoding="utf-8")
    assert "VERIFIER_FILES.txt" not in src
    assert "VERIFIER_FILES.txt" not in declared
    from rk_harness import verifier_hash
    h = hashlib.sha256()
    for rel in declared:
        h.update((HARNESS / rel).read_bytes())
    assert verifier_hash.compute_verifier_hash() == h.hexdigest()
    assert verifier_hash.pinned_verifier_hash() == h.hexdigest()

    # A manifest with an extra line must fail, and say what kind of change it would be.
    strayed = tmp_path / "VERIFIER_FILES.txt"
    strayed.write_bytes(raw + b"rk_harness/tableau.py\n")
    ok, detail = hygiene.check_verifier_manifest(HARNESS, strayed)
    assert not ok
    assert "epoch boundary" in detail, detail
    assert "rk_harness/tableau.py" in detail, detail


# ------------------------------------------------------------------------------------ C35

@pytest.mark.skipif(not (WORKSPACE / "start.ps1").exists(),
                    reason="workspace start.ps1 not present")
def test_C35_workspace_script_copies_match_the_workspace_root():
    ok, detail = hygiene.check_workspace_copies(WORKSPACE, HARNESS)
    assert ok, detail
    assert not detail.startswith(hygiene.SKIP)
    for name in hygiene.WORKSPACE_COPIES:
        live = (WORKSPACE / name).read_bytes()
        copy = (HARNESS / "scripts" / "workspace" / name).read_bytes()
        assert live == copy, (
            f"{name} differs between the workspace root and scripts/workspace/. The root copy "
            "is the one that runs; resync root -> repo.")


def test_C35b_the_workspace_copy_check_skips_rather_than_fails_without_a_root(tmp_path):
    ok, detail = hygiene.check_workspace_copies(tmp_path, HARNESS)
    assert ok and detail.startswith(hygiene.SKIP), detail


# ------------------------------------------------------------------------------------ C36

_GENERATE = WORKSPACE / "rk-overview" / "tools" / "generate.py"


@pytest.mark.skipif(not _GENERATE.exists(), reason="rk-overview/tools/generate.py not present")
def test_C36_every_test_tier_has_a_suite_desc_entry():
    # Reads the local file on purpose: CI fetches rk-overview's pushed main over https, and
    # the suite must never need a network.
    ok, detail = hygiene.check_suite_desc(_GENERATE, HARNESS)
    assert ok, detail
    tiers = hygiene.local_tiers(HARNESS)
    assert "T5" in tiers, "this file is a test_t5_*.py and must land in the existing T5 tier"
    assert tiers == sorted(tiers, key=lambda t: int(t[1:]))


def test_C36b_suite_desc_distinguishes_a_bad_fetch_from_an_undescribed_tier():
    ok, detail = hygiene.check_suite_desc("<html>404: Not Found</html>", HARNESS)
    assert not ok
    assert "not a tier-coverage failure" in detail, detail

    described = "\n".join(['_SUITE_DESC = {', '    "T1": "one",', '}'])
    ok, detail = hygiene.check_suite_desc(described, HARNESS)
    assert not ok
    assert "_SUITE_DESC" in detail and "not been pushed" in detail, detail


# ------------------------------------------------------------------------------------ C37

def test_C37_the_report_dates_every_row_by_its_own_evidence(tmp_path):
    pf = _load("rk_preflight_under_test", SCRIPTS / "preflight.py")
    pf.R.items.clear()
    pf.R.section = "0"
    pf.R.add("0.1", "PASS", "measured here, now", origin="host")
    pf.R.section = "B"
    pf.R.add("B1", "PASS", "B7 passed", origin="suite")

    evidence = {
        "commit": "1234567890abcdef1234567890abcdef12345678",
        "run_url": "https://github.com/jgoetzmann/rk-harness/actions/runs/99",
        "run_id": "99", "merged_at_utc": "2026-09-05T11:22:33Z",
        "shards": ["order-eval-verify"], "totals": {"tests": 1307, "failures": 0,
                                                    "errors": 0, "skipped": 2},
    }
    checkout = {"commit": "fedcba9876543210fedcba9876543210fedcba98", "dirty": True,
                "committed_utc": "2026-09-08T01:02:03Z", "error": ""}
    out = tmp_path / "REVIEW-REPORT.md"
    pf.write_report(out, True, False, evidence, checkout)
    text = out.read_text(encoding="utf-8")

    # An Evidence column, naming the origin of each row.
    assert "| Item | Status | Evidence | Measured / evidence |" in text
    assert "| 0.1 | PASS | host |" in text
    assert "| B1 | PASS | suite |" in text

    # A provenance table naming the suite commit and its own UTC timestamp.
    assert "### Provenance" in text
    assert "2026-09-05T11:22:33Z" in text
    assert "1234567890ab" in text
    assert "NOT the commit the suite artifact was built from" in text
    assert "dirty" in text

    # Two distinct dates in the sign-off, not one date over mixed evidence.
    signoff = text.split("## Sign-off", 1)[1]
    assert "Report generated (UTC):" in signoff and "Suite evidence (UTC):" in signoff
    generated = re.search(r"Report generated \(UTC\):\s+(\S+)", signoff).group(1)
    suite = re.search(r"Suite evidence \(UTC\):\s+(\S+)", signoff).group(1)
    assert suite == "2026-09-05T11:22:33Z"
    assert generated != suite
    assert generated.endswith("Z") and "T" in generated

    # The suite row is dated by the artifact, never by the calendar on this machine.
    import datetime as dt
    today = dt.date.today().isoformat()
    assert re.search(r"B green:\s+2026-09-05", signoff), signoff
    assert f"B green:                {today}" not in signoff

    # HOST is reported and does not gate.
    assert "HOST (non-gating):" in signoff
    assert "HOST does not gate" in text


def test_C37b_reuse_suite_without_the_artifact_says_what_to_download(tmp_path, capsys, monkeypatch):
    pf = _load("rk_preflight_under_test2", SCRIPTS / "preflight.py")
    monkeypatch.setattr(pf, "HARNESS", tmp_path)
    results, evidence = pf.run_suite(quick=True, reuse=True)
    assert results == {} and evidence == {}
    said = capsys.readouterr().out
    assert "gh run download" in said and "preflight-suite" in said, said
    assert "SKIP" in said


def test_C37c_preflight_helpers_derive_rather_than_retype():
    pf = _load("rk_preflight_under_test3", SCRIPTS / "preflight.py")
    # The RK_* table must name every `-e RK_...` line in run.ps1, or it passes forever on a
    # mapping nobody maintains.
    declared = pf.run_ps1_env_names((HARNESS / "scripts" / "run.ps1").read_text(encoding="utf-8"))
    assert declared, "no `-e RK_` lines found in scripts/run.ps1"
    assert not (declared - set(pf.RK_ENV_MAP)), sorted(declared - set(pf.RK_ENV_MAP))

    start = HARNESS / "scripts" / "workspace" / "start.ps1"
    want = pf.watchdog_expected_args(start.read_text(encoding="utf-8"),
                                     {"watchdog": {"cpu_pause_high_percent": 71}})
    assert want["CpuHigh"] == "71", want            # config.json wins
    assert want["PollSeconds"] == "10", want        # start.ps1's default fills the gap
    assert "CpuHighAvg" in want and "SaturationCheckSeconds" in want, sorted(want)


# ------------------------------------------------------------------------------------ C38

def _shard_xml(path: Path, suite_name: str, cases: list[str], failures: int = 0) -> None:
    parts = ['<?xml version="1.0" encoding="utf-8"?>',
             f'<testsuites><testsuite name="{suite_name}" tests="{len(cases)}" '
             f'failures="{failures}" errors="0" skipped="1" time="1.0">']
    for c in cases:
        parts.append(f'<testcase classname="tests.x" name="{c}" time="0.1"></testcase>')
    parts.append("</testsuite></testsuites>")
    path.write_text("".join(parts), encoding="utf-8")


def test_C38_merge_junit_combines_shards_and_records_provenance(tmp_path):
    d = tmp_path / "art"
    d.mkdir()
    _shard_xml(d / "junit-alpha.xml", "pytest", ["test_B7_alpha_one", "test_B7_alpha_two"])
    _shard_xml(d / "junit-beta.xml", "pytest", ["test_V10d_beta_one"], failures=1)

    out_xml = tmp_path / "preflight-junit.xml"
    out_json = tmp_path / "preflight-evidence.json"
    meta = {"commit": "deadbeef" * 5, "ref": "refs/heads/main", "run_id": "77",
            "run_number": "12", "run_attempt": "1",
            "run_url": "https://github.com/jgoetzmann/rk-harness/actions/runs/77",
            "workflow": "ci", "python_version": "3.12.7", "runner_os": "Linux"}
    doc = merge_junit.merge(merge_junit.shard_paths(d, ["alpha", "beta"]), out_xml, out_json, meta)

    # preflight reads the merged file with root.iter('testcase'); that walk must find both.
    names = sorted(tc.get("name") for tc in ET.parse(out_xml).getroot().iter("testcase"))
    assert names == ["test_B7_alpha_one", "test_B7_alpha_two", "test_V10d_beta_one"]
    assert ET.parse(out_xml).getroot().tag == "testsuites"

    assert doc["totals"] == {"tests": 3, "failures": 1, "errors": 0, "skipped": 2}
    assert doc["shards"] == ["alpha", "beta"]

    side = json.loads(out_json.read_text(encoding="utf-8"))
    assert side == doc
    assert side["commit"] == meta["commit"] and side["run_url"] == meta["run_url"]
    stamp = side["merged_at_utc"]
    assert stamp.endswith("Z") or stamp.endswith("+00:00"), stamp
    # Stored UTC, never converted on the way in (rule 6): parsing it back as UTC must land
    # within a minute of now, which a local-time write on this machine would not.
    import datetime as dt
    when = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    assert abs((dt.datetime.now(dt.timezone.utc) - when).total_seconds()) < 300, stamp

    # A missing shard is named and refused; a partial merge must never read as a green suite.
    with pytest.raises(SystemExit) as exc:
        merge_junit.shard_paths(d, ["alpha", "beta", "gamma"])
    assert "gamma" in str(exc.value)
    assert "junit-alpha.xml" in str(exc.value)
    assert "never ran" in str(exc.value)


def test_C38b_merge_junit_runs_as_a_command(tmp_path):
    d = tmp_path / "art"
    d.mkdir()
    _shard_xml(d / "junit-one.xml", "pytest", ["test_B7_one"])
    p = subprocess.run([sys.executable, str(SCRIPTS / "merge_junit.py"), "--dir", str(d),
                        "--expect", "one",
                        "--out-xml", str(tmp_path / "m.xml"),
                        "--out-json", str(tmp_path / "m.json")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert p.returncode == 0, p.stderr
    assert "merged 1 shards" in p.stdout
    assert json.loads((tmp_path / "m.json").read_text(encoding="utf-8"))["totals"]["tests"] == 1

    p = subprocess.run([sys.executable, str(SCRIPTS / "merge_junit.py"), "--dir", str(d),
                        "--expect", "one,two",
                        "--out-xml", str(tmp_path / "m2.xml"),
                        "--out-json", str(tmp_path / "m2.json")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert p.returncode != 0
    assert "two" in p.stderr
    assert not (tmp_path / "m2.xml").exists(), "a refused merge must not leave a merged file"
def test_C38c_the_evidence_job_expects_exactly_the_shard_matrix():
    """The merge refuses a missing shard by name, which only helps if it knows the names.

    Read out of ci.yml with a regex rather than a YAML parser: pyyaml is not a dependency of
    this project and the suite must not grow one to check a workflow file.
    """
    ci = (HARNESS / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    include = ci.split("      matrix:", 1)[1].split("    name: suite (", 1)[0]
    matrix = re.findall(r"^\s+- name: (\S+)\s*$", include, re.M)
    assert len(matrix) == 5, matrix

    expect = re.search(r'--expect "([^"]*)"', ci).group(1)
    assert [s.strip() for s in expect.split(",")] == matrix, (
        f"suite-evidence expects {expect!r} but the suite matrix is {matrix}. A shard that is "
        "expected under the wrong name fails the merge; a shard that is not expected at all is "
        "silently dropped, and the artifact then reports a smaller, greener suite.")

    # The uploaded name and the merged name have to be the same string.
    assert "junit-${{ matrix.name }}.xml" in ci
    assert "name: junit-${{ matrix.name }}" in ci
    assert "pattern: junit-*" in ci and "merge-multiple: true" in ci
    assert "name: preflight-suite" in ci
# ------------------------------------------------------------------------------------ C39

_KEY_FINDINGS = WORKSPACE / "rk-overview" / "tools" / "key_findings.json"
_WEIGHTINGS = (("magnitude", None),
               ("equal_median_anchor", "median_anchor"),
               ("equal_reference_norm", "reference_norm"))


@pytest.mark.skipif(not _KEY_FINDINGS.exists(),
                    reason="rk-overview/tools/key_findings.json not present")
def test_C39_the_counterfactual_shares_sum_to_one_and_its_aggregates_reconstruct():
    """The published weight shares and counterfactual aggregates are checked, not trusted.

    Invariants rather than pinned values, so a fresh archive does not turn this red: the
    shares of a sum of squares add to one, an RMS rebuilt from the per-problem errors
    reproduces the aggregate it is meant to be, every ratio is the best anchor over the
    champion, and the traced prices carry the two hashes that sit on disk. Pinning the
    values instead would pin them to one snapshot of a run that writes a new one every
    cycle, which is how a share table ends up disagreeing with the page beside it.
    """
    doc = json.loads(_KEY_FINDINGS.read_text(encoding="utf-8"))
    if "counterfactual" not in doc:
        pytest.skip("this key_findings.json predates the counterfactual finding; rerun "
                    "rk-overview/tools/key_findings.py")
    cf = doc["counterfactual"]
    n = cf["numbers"]
    rows = cf["series"]["per_method"]

    # Tied to the pins on disk. A counterfactual priced from a trace document that another
    # evaluator produced is a ratio between two different things.
    assert n["provenance"]["trace_hash"] == (HARNESS / "TRACE_HASH").read_text(
        encoding="ascii").strip()
    assert n["provenance"]["verifier_hash"] == (HARNESS / "VERIFIER_HASH").read_text(
        encoding="ascii").strip()
    assert not n["excluded"]["state_counts_without_a_traced_price"], (
        "a state count the problem set uses has no traced price, so something in the grid "
        "was priced by extrapolation")

    # Every share set sums to one.
    groups = {}
    for row in rows:
        for weighting, _scale in _WEIGHTINGS:
            key = (row["basis"], row["set"], row["method"], weighting)
            groups.setdefault(key, []).append(row["share_" + weighting])
    assert groups, "the counterfactual published no weight shares"
    for key, shares in sorted(groups.items()):
        assert len(shares) in (3, 4), key
        assert abs(sum(shares) - 1.0) < 1e-9, (key, sum(shares))

    # Every aggregate is the RMS of the weighted per-problem errors it is built from.
    scales = n["scales"]
    for weighting, scale_key in _WEIGHTINGS:
        for basis in ("analytic", "traced_whole_step"):
            for set_name, block_key in (("search", "search_set"), ("heldout", "heldout")):
                block = n[block_key][weighting][basis]
                for method, published in sorted(block["error"].items()):
                    vals = [r["error"] / (1.0 if scale_key is None
                                          else scales[scale_key][r["problem"]])
                            for r in rows
                            if r["basis"] == basis and r["set"] == set_name
                            and r["method"] == method]
                    assert len(vals) in (3, 4), (weighting, basis, set_name, method)
                    got = (sum(v * v for v in vals) / len(vals)) ** 0.5
                    assert abs(got - published) <= 1e-12 * max(abs(got), 1e-300), (
                        weighting, basis, set_name, method, got, published)

    # Every ratio is the best anchor over the champion, on the set the row keeps.
    for weighting, _scale in _WEIGHTINGS:
        for basis in ("analytic", "traced_whole_step"):
            for r in n["heldout"][weighting][basis]["leave_one_out"]:
                want = r["best_anchor_error"] / r["champion_error"]
                assert abs(r["ratio"] - want) <= 1e-12 * abs(want), (weighting, basis, r)

    # The magnitude and analytic corner is the published leave-one-out table, or the two
    # tables are not comparable and neither belongs beside the other.
    pub = doc["efficiency"]["numbers"]["leave_one_out"]
    mine = n["heldout"]["magnitude"]["analytic"]["leave_one_out"]
    assert [r["dropped"] for r in mine] == [r["dropped"] for r in pub]
    for a, b in zip(pub, mine):
        assert a["best_anchor_name"] == b["best_anchor_name"], (a, b)
        assert abs(a["ratio"] - b["ratio"]) <= 1e-12, (a, b)
    assert n["invariants"]["published_leave_one_out_reproduced"] is True
    assert n["invariants"]["every_step_count_is_the_budget_division"] is True


# ------------------------------------------------------------------------------------ C39b

_GENERATE_CACHE: dict = {}


def _overview_generate():
    """rk-overview/tools/generate.py, imported once per path under a private module name.

    Importing it puts rk-overview/tools and rk-harness at the front of sys.path. Both entries
    come back out afterwards, so a module in tools/ cannot shadow anything a later test
    imports; the names generate.py bound while importing stay bound.
    """
    if _GENERATE not in _GENERATE_CACHE:
        saved = list(sys.path)
        spec = importlib.util.spec_from_file_location("rk_overview_generate_under_test",
                                                      _GENERATE)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        finally:
            sys.path[:] = saved
        _GENERATE_CACHE[_GENERATE] = mod
    return _GENERATE_CACHE[_GENERATE]


def _folded_table(html_text: str, table_id: str):
    """The header cells and body rows of the folded table with this id, as plain text."""
    import html as html_mod
    m = re.search(r'<details class="fold" id="' + re.escape(table_id) + r'"><summary>(.*?)'
                  r"</summary>(.*?)</details>", html_text, re.S)
    assert m, f"{table_id} did not render"

    def text(cell: str) -> str:
        return html_mod.unescape(re.sub(r"<[^>]+>", "", cell)).strip()

    head = [text(c) for c in re.findall(r"<th[^>]*>(.*?)</th>", m.group(2), re.S)]
    tbody = m.group(2).split("<tbody>", 1)[1]
    body = [[text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            for tr in re.findall(r"<tr>(.*?)</tr>", tbody, re.S)]
    assert f"({len(body)} rows)" in m.group(1), (table_id, m.group(1), len(body))
    return head, body


def _counterfactual_or_skip():
    doc = json.loads(_KEY_FINDINGS.read_text(encoding="utf-8"))
    if "counterfactual" not in doc:
        pytest.skip("this key_findings.json predates the counterfactual finding; rerun "
                    "rk-overview/tools/key_findings.py")
    return doc


@pytest.mark.skipif(not (_KEY_FINDINGS.exists() and _GENERATE.exists()),
                    reason="rk-overview/tools/key_findings.json or generate.py not present")
def test_C39b_the_counterfactual_tables_render_from_the_current_document():
    """The overview's counterfactual tables are rendered from key_findings.json, row for row.

    C39 checks the document. This checks what the page is built from it: the overview's own
    renderers are called on the document on disk, and every grid cell, search-set rank and
    leave-one-out row in the document must come back as a printed row, with the value the
    document holds. The excluded table must name exactly methods_without_a_trace_row, in the
    document's order, and that list must still mean what it says: discovered frontier rows
    with no trace row, none of them a method the grid scores. Every count is read from the
    document and none is pinned (DECISIONS.md D44, D-b), so a refreshed analysis moves both
    sides together and a table that silently drops a row does not.
    """
    doc = _counterfactual_or_skip()
    gen = _overview_generate()
    cf = gen._cf_load(doc)
    n = cf["numbers"]
    wl, bl = gen._CF_WLABEL, gen._CF_BLABEL
    cells = [(w, b) for w, _l in gen._CF_WEIGHTINGS for b, _m in gen._CF_BASES]
    assert sorted(cells) == sorted((w, b) for w in n["heldout"] for b in n["heldout"][w]), (
        "the renderers and the document disagree about which cells the grid has")

    # The grid: one row per cell, in the renderers' order, carrying that cell's numbers.
    grid = gen._cf_grid_table(cf)
    rows = [re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
            for tr in re.findall(r"<tr>(.*?)</tr>", grid.split("<tbody>", 1)[1], re.S)]
    assert len(rows) == len(cells), (len(rows), len(cells))
    for (w, b), row in zip(cells, rows):
        c = n["heldout"][w][b]
        reduced = sum(1 for r in c["leave_one_out"] if r["dropped"])
        assert row[0] == wl[w] and row[1] == bl[b], (w, b, row[:2])
        assert row[3].startswith(c["best_anchor_name"] + " "), (w, b, row[3])
        assert row[4] == gen._cf_ratio(c["ratio"]) + "x", (w, b, row[4], c["ratio"])
        assert row[5].startswith(gen._cf_ratio(c["lowest_leave_one_out_ratio"]) + "x"), (w, b)
        assert row[6] == f'{c["reduced_sets_where_an_anchor_leads"]} of {reduced}', (w, b, row[6])

    # Leave-one-out: every row of every cell, and nothing else.
    head, body = _folded_table(gen._cf_loo_table(cf), "cf-loo-table")
    dropped = {r["dropped"] for w, b in cells for r in n["heldout"][w][b]["leave_one_out"]}
    want = sorted((wl[w], bl[b], r["dropped"] or "", r["best_anchor_name"],
                   gen._cf_ratio(r["ratio"]) + "x")
                  for w, b in cells for r in n["heldout"][w][b]["leave_one_out"])
    col = {h: i for i, h in enumerate(head)}
    got = sorted((r[col["weighting"]], r[col["cost basis"]],
                  r[col["dropped"]] if r[col["dropped"]] in dropped else "",
                  r[col["best classical anchor"]], r[col["ratio"]]) for r in body)
    assert got == want
    assert len(body) == len(cf["series"]["leave_one_out"])

    # Search set: every method's error and rank in every cell.
    head, body = _folded_table(gen._cf_search_table(cf), "cf-search-table")
    col = {h: i for i, h in enumerate(head)}
    want = sorted((wl[w], bl[b], m, gen._short(e),
                   f'{n["search_set"][w][b]["rank"][m]} of {len(n["search_set"][w][b]["error"])}')
                  for w, b in cells for m, e in n["search_set"][w][b]["error"].items())
    got = sorted((r[col["weighting"]], r[col["cost basis"]], r[col["method"]],
                  r[col["search-set RMS"]], r[col["rank of the nine scored"]]) for r in body)
    assert got == want

    # Held-out on both bases: each value is the RMS of that method's own held-out rows,
    # recomputed here rather than taken from the renderer.
    head, body = _folded_table(gen._cf_heldout_table(cf), "cf-heldout-table")
    per: dict = {}
    for r in cf["series"]["per_method"]:
        if r["set"] == "heldout":
            per.setdefault((r["method"], r["basis"]), []).append(r["error"])
    methods = sorted({m for m, _b in per})
    assert sorted(r[0] for r in body) == methods
    analytic = next(i for i, h in enumerate(head) if "analytic" in h)
    traced = next(i for i, h in enumerate(head) if "traced" in h)
    for r in body:
        for i, basis in ((analytic, "analytic"), (traced, "traced_whole_step")):
            v = per[(r[0], basis)]
            assert r[i] == gen._short((sum(x * x for x in v) / len(v)) ** 0.5), (r, basis)

    # Excluded: exactly the document's list, in its order.
    excluded = n["excluded"]["methods_without_a_trace_row"]
    head, body = _folded_table(gen._cf_excluded_table(cf), "cf-excluded-table")
    assert [r[0] for r in body] == [x["tableau_hash"] for x in excluded]
    assert [r[1] for r in body] == [str(x["cycles"]) for x in excluded]
    assert [r[2] for r in body] == [gen._short(x["heldout_error"]) for x in excluded]

    # And the list still means what its name says.
    frontier = doc["efficiency"]["series"]["frontier_cycles_vs_heldout"]
    discovered = {str(r["tableau_hash"])[:12] for r in frontier if r["kind"] == "discovered"}
    scored = {m["tableau_hash"][:12] for m in n["methods"]}
    assert all(m.get("trace_row") for m in n["methods"]), "a scored method has no trace row"
    assert {x["tableau_hash"] for x in excluded} <= discovered
    assert not {x["tableau_hash"] for x in excluded} & scored, (
        "a method the grid prices is also named as having no trace row")
    trace_doc = WORKSPACE / "rk-work" / "trace" / "results.json"
    if trace_doc.exists():
        traced_hashes = {m["tableau_hash"][:12] for m in
                         json.loads(trace_doc.read_text(encoding="utf-8"))["methods"]}
        untraced = sorted(((r["cycles"], str(r["tableau_hash"])[:12]) for r in frontier
                           if r["kind"] == "discovered"
                           and str(r["tableau_hash"])[:12] not in traced_hashes))
        assert [h for _c, h in untraced] == [x["tableau_hash"] for x in excluded], (
            "methods_without_a_trace_row no longer matches the trace document on disk; "
            "rerun rk-overview/tools/key_findings.py")


# ------------------------------------------------------------------------------------ C39c

def _share_faults(head, body, share_col: int, group_cols) -> list:
    """What is wrong with one share column: a cell that is not a percentage, or a group of
    rows (one method's rows in one set) whose shares do not add to 100 percent."""
    faults, sums = [], {}
    for r in body:
        cell = r[share_col]
        try:
            v = float(cell[:-1]) if cell.endswith("%") else None
        except ValueError:
            v = None
        if v is None or not 0.0 <= v <= 100.0:
            faults.append(f"{head[share_col]!r} holds {cell!r}, which is not a share")
            continue
        key = tuple(g(r) for g in group_cols)
        sums[key] = sums.get(key, 0.0) + v
    # _cf_pct prints two decimals, or two significant figures below 0.1 percent, so four
    # rounded shares can miss 100 by 0.02 at most.
    faults += [f"{head[share_col]!r} sums to {s:.3f}% over {key}"
               for key, s in sorted(sums.items()) if abs(s - 100.0) > 0.05]
    return faults


def _share_columns(head, *words):
    return [i for i, h in enumerate(head)
            if "share" in h and all(w in h for w in words)]


@pytest.mark.skipif(not (_KEY_FINDINGS.exists() and _GENERATE.exists()),
                    reason="rk-overview/tools/key_findings.json or generate.py not present")
def test_C39c_the_overview_rms_tables_keep_their_share_columns():
    """The overview tables whose rows feed an RMS carry a weight-share column, and it adds up.

    G1's acceptance 2, scoped by SPEC2 to the tables that feed an RMS aggregate. On the
    overview those are finding 2's floor against round-to-nearest table, which feeds both
    the search-set and the held-out RMS, and the counterfactual's per-problem table. rc-table
    is checked too, because finding 4 prints each method's rc_thermal error and that error
    is one term of the method's held-out RMS (D44, D-d). Each is rendered from the current
    key_findings.json, and each share column has to be present and hold a real share:
    within one method and one set the shares add to 100 percent, and on rc-table each share
    is the document's own. The findings half of the acceptance, the cell pages, is tested
    beside sitegen. Finding 1's per-problem sentence also carries shares but only renders
    with the live archive loaded, so it is not checked here.
    """
    doc = _counterfactual_or_skip()
    gen = _overview_generate()
    cf = gen._cf_load(doc)
    set_of = {r["problem"]: r["set"] for r in cf["series"]["per_method"]}

    head, body = _folded_table(gen.flip_problem_chart(doc), "flip-problem-table")
    col = {h: i for i, h in enumerate(head)}
    by_method_and_set = (lambda r: r[col["method"]], lambda r: set_of[r[col["problem"]]])
    for words in (("floor",), ("round",)):
        found = _share_columns(head, *words)
        assert len(found) == 1, f"flip-problem-table has no {words[0]} share column: {head}"
        assert not _share_faults(head, body, found[0], by_method_and_set), (
            _share_faults(head, body, found[0], by_method_and_set))

    head, body = _folded_table(gen._cf_permethod_fold(cf, gen._cf_degeneracy(cf, doc)),
                               "cf-per-method-table")
    col = {h: i for i, h in enumerate(head)}
    groups = (lambda r: r[col["cost basis"]], lambda r: r[col["set"]],
              lambda r: r[col["method"]])
    for words in (("magnitude",), ("median",), ("reference",)):
        found = _share_columns(head, *words)
        assert len(found) == 1, f"cf-per-method-table has no {words[0]} share column: {head}"
        assert not _share_faults(head, body, found[0], groups), (
            _share_faults(head, body, found[0], groups))

    head, body = _folded_table(gen.rc_chart(doc), "rc-table")
    found = _share_columns(head)
    assert len(found) == 1, f"rc-table has no share column: {head}"
    share = {r["method"]: r["share_magnitude"] for r in cf["series"]["per_method"]
             if r["problem"] == "rc_thermal" and r["basis"] == "analytic"}
    printed = [r for r in body if r[0] in share]
    assert printed, "rc-table prints no method the counterfactual scores"
    for r in body:
        want = gen._cf_pct(share[r[0]]) if r[0] in share else gen._NA
        assert r[found[0]] == want, (r, want)

    # The check is not vacuous. A table that loses the column, or whose column stops adding
    # up, is reported, and that is exactly what the assertions above turn red on.
    head, body = _folded_table(gen.flip_problem_chart(doc), "flip-problem-table")
    col = {h: i for i, h in enumerate(head)}
    i = _share_columns(head, "floor")[0]
    lost = head[:i] + head[i + 1:]
    assert not _share_columns(lost, "floor")
    bent = [list(r) for r in body]
    bent[0][i] = "50.00%" if bent[0][i] != "50.00%" else "49.00%"
    assert _share_faults(head, bent, i, (lambda r: r[col["method"]],
                                         lambda r: set_of[r[col["problem"]]]))
    assert _share_faults(head, [r[:i] + ["n/a"] + r[i + 1:] for r in body], i,
                         (lambda r: r[col["method"]],))
