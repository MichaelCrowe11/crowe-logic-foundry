"""Tests for cli.guardrails.paths."""
from __future__ import annotations

from pathlib import Path

import pytest

from cli.guardrails.paths import PathPolicy


@pytest.fixture
def policy(tmp_path: Path) -> PathPolicy:
    """Use a temp home so tests do not depend on the user's real $HOME state."""
    return PathPolicy(home=tmp_path)


def test_home_dir_root_file_denied(policy: PathPolicy, tmp_path: Path) -> None:
    """The exact 2026-04-30 failure mode: campaign_blast.py at /Users/crowelogic/."""
    candidate = str(tmp_path / "campaign_blast.py")
    decision = policy.evaluate(candidate)
    assert decision.verdict == "DENY"
    assert "home-dir" in decision.reason


def test_home_dir_root_json_denied(policy: PathPolicy, tmp_path: Path) -> None:
    candidate = str(tmp_path / "contacts.json")
    decision = policy.evaluate(candidate)
    assert decision.verdict == "DENY"


def test_user_provided_path_always_allowed(tmp_path: Path) -> None:
    """User said 'write to /Users/crowelogic/foo.txt' explicitly: allow it."""
    candidate = str(tmp_path / "explicit.py")
    policy = PathPolicy(home=tmp_path, user_provided_paths=frozenset({candidate}))
    decision = policy.evaluate(candidate)
    assert decision.verdict == "ALLOW"
    assert "user-provided" in decision.reason


def test_under_projects_directory_allowed(tmp_path: Path) -> None:
    projects = tmp_path / "Projects" / "myproject"
    projects.mkdir(parents=True)
    candidate = str(projects / "src" / "feature.py")
    policy = PathPolicy(
        home=tmp_path,
        allowed_prefixes=(str(tmp_path / "Projects"),),
    )
    decision = policy.evaluate(candidate)
    assert decision.verdict == "ALLOW"


def test_under_project_root_allowed(tmp_path: Path) -> None:
    project_root = tmp_path / "myrepo"
    project_root.mkdir()
    candidate = str(project_root / "deep" / "nested" / "file.py")
    policy = PathPolicy(
        home=tmp_path,
        allowed_prefixes=(),
        project_root=project_root,
    )
    decision = policy.evaluate(candidate)
    assert decision.verdict == "ALLOW"
    assert "project root" in decision.reason


def test_tmp_allowed(tmp_path: Path) -> None:
    policy = PathPolicy(
        home=tmp_path,
        allowed_prefixes=("/tmp",),
    )
    decision = policy.evaluate("/tmp/scratch.txt")
    assert decision.verdict == "ALLOW"


def test_unknown_external_path_requires_confirm(tmp_path: Path) -> None:
    candidate = str(tmp_path / "deep" / "elsewhere" / "thing.py")
    policy = PathPolicy(
        home=tmp_path,
        allowed_prefixes=(str(tmp_path / "Projects"),),
    )
    decision = policy.evaluate(candidate)
    assert decision.verdict == "REQUIRE_CONFIRM"


def test_filesystem_root_denied(policy: PathPolicy) -> None:
    decision = policy.evaluate("/etc/passwd")
    assert decision.verdict == "DENY"
    # The denial may surface as protected-prefix, shallow-path, or
    # home-dir depending on platform symlink resolution. All are valid.
    assert any(
        marker in decision.reason
        for marker in ("protected", "shallow", "home-dir")
    )


def test_protected_system_prefixes_denied(policy: PathPolicy) -> None:
    """Hard-deny common sensitive system locations regardless of segment count."""
    for sensitive in ["/etc/sudoers", "/usr/bin/python", "/System/Library/foo"]:
        decision = policy.evaluate(sensitive)
        assert decision.verdict == "DENY", f"expected DENY for {sensitive}, got {decision.verdict}"


def test_decision_carries_resolved_path(policy: PathPolicy, tmp_path: Path) -> None:
    candidate = str(tmp_path / "campaign_blast.py")
    decision = policy.evaluate(candidate)
    assert str(tmp_path) in decision.path
