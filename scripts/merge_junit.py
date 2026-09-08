"""Merge the sharded junit XMLs CI produces into the one file preflight reads, plus a
sidecar that says where the evidence came from.

Standard library only: the CI job that runs this does a checkout, a setup-python and a
download-artifact, and installs nothing.

    python scripts/merge_junit.py --dir "$RUNNER_TEMP/junit" \\
        --expect fixedpoint-coeff-cost,order-eval-verify,... \\
        --out-xml preflight-junit.xml --out-json preflight-evidence.json

The merged root is `<testsuites>` holding each shard's `<testsuite>` element unchanged.
preflight reads it with `root.iter('testcase')`, which walks the whole tree regardless of
the root tag, so nothing in preflight's parser has to know this file was assembled.

Every timestamp written here is UTC and is stored the way it was read. Display is US
Central and happens at display time (rk_harness/timefmt.py); nothing is converted on the
way into a file.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

_COUNTERS = ("tests", "failures", "errors", "skipped")


def utc_now() -> str:
    """The current instant, UTC, second resolution, with an explicit Z."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def shard_paths(directory: Path, expect: list[str]) -> list[tuple[str, Path]]:
    """Resolve `junit-<name>.xml` for every expected shard, or fail naming the missing ones.

    A partial merge is the dangerous outcome: it parses, it counts, and it reports a green
    suite for tests that never ran. So a missing shard is an error and never a warning.
    """
    found, missing = [], []
    for name in expect:
        p = directory / f"junit-{name}.xml"
        if p.exists():
            found.append((name, p))
        else:
            missing.append(name)
    if missing:
        have = sorted(q.name for q in directory.glob("junit-*.xml")) if directory.exists() else []
        raise SystemExit(
            f"missing shard artifact(s): {', '.join(missing)}. Present in {directory}: "
            f"{have or 'nothing'}. Refusing to merge: a partial merge reports a green suite "
            "for shards that never ran.")
    return found


def merge(paths: list[tuple[str, Path]], out_xml: Path, out_json: Path,
          meta: dict | None = None) -> dict:
    """Write the merged XML and its provenance sidecar; return the sidecar document."""
    root = ET.Element("testsuites")
    totals = {k: 0 for k in _COUNTERS}
    shards = []
    for name, path in paths:
        tree = ET.parse(path)
        node = tree.getroot()
        children = [node] if node.tag == "testsuite" else list(node)
        for suite in children:
            root.append(suite)
            for key in _COUNTERS:
                try:
                    totals[key] += int(suite.get(key, 0))
                except (TypeError, ValueError):
                    pass
        shards.append(name)
    out_xml.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(out_xml, encoding="utf-8", xml_declaration=True)

    meta = dict(meta or {})
    doc = {
        "commit": meta.get("commit", ""),
        "ref": meta.get("ref", ""),
        "run_id": meta.get("run_id", ""),
        "run_number": meta.get("run_number", ""),
        "run_attempt": meta.get("run_attempt", ""),
        "run_url": meta.get("run_url", ""),
        "workflow": meta.get("workflow", ""),
        "python_version": meta.get("python_version", ""),
        "runner_os": meta.get("runner_os", ""),
        "shards": shards,
        # The moment of the merge step, not the moment the suite started. Named for what it
        # is so no reader can mistake it for a test start time; the shard <testsuite>
        # elements carry their own timestamps if that is what is wanted.
        "merged_at_utc": meta.get("merged_at_utc") or utc_now(),
        "totals": totals,
        "junit_xml": out_xml.name,
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return doc


def meta_from_env(env: dict | None = None) -> dict:
    """Read the GitHub context the workflow passes in as environment variables."""
    e = os.environ if env is None else env
    return {
        "commit": e.get("RK_CI_COMMIT", ""),
        "ref": e.get("RK_CI_REF", ""),
        "run_id": e.get("RK_CI_RUN_ID", ""),
        "run_number": e.get("RK_CI_RUN_NUMBER", ""),
        "run_attempt": e.get("RK_CI_RUN_ATTEMPT", ""),
        "run_url": e.get("RK_CI_RUN_URL", ""),
        "workflow": e.get("RK_CI_WORKFLOW", ""),
        "python_version": e.get("RK_CI_PYTHON", "") or ".".join(str(n) for n in sys.version_info[:3]),
        "runner_os": e.get("RK_CI_RUNNER_OS", ""),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="merge sharded junit XML and record its provenance")
    ap.add_argument("--dir", default=".", help="directory holding junit-<shard>.xml")
    ap.add_argument("--expect", default="",
                    help="comma-separated shard names that must all be present")
    ap.add_argument("--out-xml", default="preflight-junit.xml")
    ap.add_argument("--out-json", default="preflight-evidence.json")
    args = ap.parse_args(argv)

    directory = Path(args.dir)
    expect = [s.strip() for s in args.expect.split(",") if s.strip()]
    if not expect:
        expect = sorted(p.name[len("junit-"):-len(".xml")]
                        for p in directory.glob("junit-*.xml"))
        if not expect:
            raise SystemExit(f"no junit-*.xml under {directory} and no --expect given")
    paths = shard_paths(directory, expect)
    doc = merge(paths, Path(args.out_xml), Path(args.out_json), meta_from_env())
    t = doc["totals"]
    print(f"merged {len(doc['shards'])} shards: {t['tests']} tests, {t['failures']} failures, "
          f"{t['errors']} errors, {t['skipped']} skipped")
    print(f"  {args.out_xml}\n  {args.out_json} (merged_at_utc {doc['merged_at_utc']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
