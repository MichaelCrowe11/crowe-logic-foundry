"""Tests for published version metadata staying aligned."""

from __future__ import annotations

import json
import re
from pathlib import Path

from config.agent_config import AGENT_VERSION


REPO_ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"$', text, flags=re.MULTILINE)
    assert match is not None
    return match.group(1)


def _package_json_version() -> str:
    payload = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    return str(payload["version"])


def _semver_tuple(version: str) -> tuple[int, int, int]:
    major, minor, patch = version.split(".")
    return int(major), int(minor), int(patch)


def test_primary_version_metadata_stays_aligned_and_above_release_floor():
    versions = {
        "pyproject": _pyproject_version(),
        "package_json": _package_json_version(),
        "agent": AGENT_VERSION,
    }

    assert len(set(versions.values())) == 1
    assert _semver_tuple(AGENT_VERSION) > (0, 5, 0)
