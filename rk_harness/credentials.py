"""Credential handling — HANDOFF §2.2. Hand-written (HANDOFF §16.1).

`.env` is gitignored and never baked into the image; the container receives it through
`--env-file`. This module only reads values; it never logs or echoes them.
"""
from __future__ import annotations

import os
from pathlib import Path

from rk_harness.paths import HARNESS_DIR

DEFAULT_CAP_USD = 50.0
_KNOWN_KEYS = ("GITHUB_TOKEN", "OPENAI_API_KEY", "OPENAI_MONTHLY_CAP_USD")


def load_env(path: Path | None = None) -> dict[str, str]:
    """Parse a KEY=VALUE file. Missing file -> {}. Values are not exported to os.environ."""
    if path is None:
        path = Path(os.environ.get("RK_ENV_FILE", str(HARNESS_DIR / ".env")))
    out: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def _get(key: str) -> str | None:
    value = os.environ.get(key)
    if value:
        return value
    value = load_env().get(key)
    return value or None


def openai_key() -> str | None:
    return _get("OPENAI_API_KEY")


def github_token() -> str | None:
    return _get("GITHUB_TOKEN")


def monthly_cap_usd() -> float:
    raw = _get("OPENAI_MONTHLY_CAP_USD")
    if raw is None:
        return DEFAULT_CAP_USD
    try:
        cap = float(raw)
    except ValueError:
        return DEFAULT_CAP_USD
    return cap if cap > 0 else DEFAULT_CAP_USD


def redacted_summary() -> dict[str, bool]:
    """Which credentials are present. Never the values."""
    return {key: _get(key) is not None for key in _KNOWN_KEYS}
