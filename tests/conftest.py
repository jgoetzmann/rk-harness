"""Shared pytest configuration. Every test runs against a throwaway work dir so nothing
can touch a real archive; tests that set RK_WORK_DIR themselves override this."""
from __future__ import annotations

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: takes seconds (evaluate / verify / search / run_cycle)")


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("RK_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("RK_FINDINGS_DIR", str(tmp_path / "findings"))
    monkeypatch.setenv("RK_SITE", "off")
    monkeypatch.setenv("RK_LLM", "off")
    monkeypatch.delenv("RK_CLOCK", raising=False)
    monkeypatch.delenv("RK_PHASE", raising=False)
    yield
