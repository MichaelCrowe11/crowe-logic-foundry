"""Architecture contracts for Crowe Logic Foundry package boundaries.

These tests codify the dependency directions the repo currently relies on so
new features do not quietly blur layers over time.
"""

from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

INTERNAL_PACKAGES = {
    "cli",
    "config",
    "control_plane",
    "crowe_synapse_engine",
    "dashboard",
    "domain",
    "iterm",
    "knowledge",
    "providers",
    "tools",
}

ALLOWED_PACKAGE_DEPENDENCIES = {
    "cli": {"config", "crowe_synapse_engine", "iterm", "providers", "tools"},
    "config": set(),
    "control_plane": {"config", "dashboard", "domain", "knowledge", "providers"},
    "crowe_synapse_engine": set(),
    "dashboard": set(),
    "domain": {"tools"},
    "iterm": set(),
    "knowledge": set(),
    "providers": {"cli", "config", "tools"},
    "tools": {"providers"},
}

# Temporary exception: providers still construct the Rich terminal renderer
# when callers do not pass an explicit renderer. We allow that coupling only in
# the current fallback-renderer files so it cannot spread further.
FILE_LEVEL_EXCEPTIONS = {
    ("providers", "cli"): {
        "providers/_shared.py",
        "providers/anthropic.py",
        "providers/azure_openai.py",
        "providers/watsonx.py",
    },
}


def _iter_internal_edges() -> dict[tuple[str, str], set[str]]:
    edges: dict[tuple[str, str], set[str]] = {}

    for package in sorted(INTERNAL_PACKAGES):
        for path in (REPO_ROOT / package).rglob("*.py"):
            rel_path = path.relative_to(REPO_ROOT).as_posix()
            src_pkg = path.relative_to(REPO_ROOT).parts[0]
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel_path)

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        target = alias.name.split(".", 1)[0]
                        if target in INTERNAL_PACKAGES and target != src_pkg:
                            edges.setdefault((src_pkg, target), set()).add(rel_path)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    target = node.module.split(".", 1)[0]
                    if target in INTERNAL_PACKAGES and target != src_pkg:
                        edges.setdefault((src_pkg, target), set()).add(rel_path)

    return edges


def test_internal_package_dependencies_match_contract():
    """All internal imports must respect the declared layer dependency map."""
    edges = _iter_internal_edges()
    violations: list[str] = []

    for edge, files in sorted(edges.items()):
        src_pkg, dst_pkg = edge
        allowed_targets = ALLOWED_PACKAGE_DEPENDENCIES[src_pkg]
        if dst_pkg not in allowed_targets:
            violations.append(
                f"{src_pkg} -> {dst_pkg} is not allowed (files: {', '.join(sorted(files))})"
            )
            continue

        allowed_files = FILE_LEVEL_EXCEPTIONS.get(edge)
        if allowed_files is not None:
            unexpected_files = sorted(set(files) - set(allowed_files))
            if unexpected_files:
                violations.append(
                    f"{src_pkg} -> {dst_pkg} is only allowed in "
                    f"{', '.join(sorted(allowed_files))}; found extra imports in "
                    f"{', '.join(unexpected_files)}"
                )

    assert not violations, "\n".join(violations)


def test_provider_to_cli_dependency_stays_narrow():
    """Provider-to-CLI coupling must remain a documented fallback-only exception."""
    edges = _iter_internal_edges()
    provider_cli_files = edges.get(("providers", "cli"), set())

    assert provider_cli_files <= FILE_LEVEL_EXCEPTIONS[("providers", "cli")]
