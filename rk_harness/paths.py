"""Filesystem locations. Every path the harness touches comes from here.
Environment variables are read on every call so tests can redirect them."""
from __future__ import annotations

import os
from pathlib import Path

HARNESS_DIR: Path = Path(__file__).resolve().parent.parent      # repo root (rk-harness)
FIXTURES_DIR: Path = HARNESS_DIR / "fixtures"
PACKAGE_DIR: Path = Path(__file__).resolve().parent               # rk_harness/


def work_dir() -> Path:
    """rk-work checkout: archive/, hypotheses.jsonl, RUNSTATE.json, HEARTBEAT, STOP, quarantine/, events.jsonl."""
    return Path(os.environ.get("RK_WORK_DIR", "/work"))


def findings_dir() -> Path:
    """rk-findings checkout; the generated site goes to findings_dir()/docs."""
    return Path(os.environ.get("RK_FINDINGS_DIR", "/findings"))


def archive_dir() -> Path:
    return work_dir() / "archive"


def quarantine_dir() -> Path:
    return work_dir() / "quarantine"
