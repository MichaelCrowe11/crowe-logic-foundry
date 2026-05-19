# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
"""
Auto-discover knowledge-lake source candidates from the
crowe-portfolio MCP.

The portfolio knows about ~242 repos and ~9 datasets, but only a
hand-coded handful are registered as knowledge-lake sources. This
module walks every portfolio entry with a local clone, looks at its
file-extension mix, and emits a `DiscoveredSource` for anything that
has enough markdown / latex / jsonl content to be worth ingesting.

The output is *suggestions*, not auto-registration. The CLI command
prints the table; passing `--register` writes them to a JSON overlay
at `~/.config/crowe-logic/kb-sources.json` which is merged into
`KNOWN_SOURCES` at import time.

Tests inject canned portfolio responses via the `portfolio_loader`
parameter so the discovery logic is exercised without any HTTP.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, Iterable, Optional


# A repo is interesting only if it has at least this many candidate
# files of one kind. Lower numbers create noise from stray README's.
_MIN_FILES_PER_KIND = 3

# Extension -> ingestor kind. Anything not in this map is ignored.
_EXT_TO_KIND: dict[str, str] = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".tex": "latex",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
}

# Subdirs we never walk when counting (vendored / build output).
_SKIP_DIR_NAMES = {
    ".git", ".venv", "venv", "node_modules", "dist", "build",
    "__pycache__", ".next", ".turbo", "target", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".idea", ".vscode", ".pi",
}


@dataclass(frozen=True)
class DiscoveredSource:
    """A candidate ready to be promoted to a registered Source."""
    name: str
    kind: str
    root: Path
    description: str
    file_count: int
    origin: str  # "portfolio_repo" | "portfolio_dataset"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["root"] = str(self.root)
        return d


# ── Loader plumbing ───────────────────────────────────────────────

PortfolioLoader = Callable[[], dict]


def _default_portfolio_loader() -> dict:
    """Default loader: call the live portfolio MCP.

    Returns a single dict with `repos` and `datasets` keys. Each value
    is a list (possibly empty). On any failure the result is `{"repos":
    [], "datasets": []}` so callers don't need to special-case
    network errors. They just get zero candidates.
    """
    try:
        from tools.portfolio_tools import (
            portfolio_list_datasets,
            portfolio_list_repos,
        )
    except ImportError:
        return {"repos": [], "datasets": []}

    repos = _parse_listing(portfolio_list_repos(limit=500))
    datasets = _parse_listing(portfolio_list_datasets())
    return {"repos": repos, "datasets": datasets}


def _parse_listing(raw: str) -> list[dict]:
    """The portfolio tools return JSON strings. Defensively walk a few
    known shapes ({"items": [...]}, {"repos": [...]}, plain list).
    Unknown shapes return [] so a portfolio schema change doesn't
    crash discovery.
    """
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        if "error" in data:
            return []
        for key in ("items", "repos", "datasets", "results"):
            if isinstance(data.get(key), list):
                return [d for d in data[key] if isinstance(d, dict)]
    return []


# ── Filesystem inspection ─────────────────────────────────────────

def _count_kinds(root: Path) -> dict[str, int]:
    """Walk `root` (depth-first, skipping vendored dirs) and return a
    map of ingestor-kind to file count.
    """
    counts: dict[str, int] = {}
    if not root.exists() or not root.is_dir():
        return counts
    # os.walk is faster than rglob for trees this size and lets us
    # prune skip-dirs in place.
    import os
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES]
        for fname in filenames:
            ext = Path(fname).suffix.lower()
            kind = _EXT_TO_KIND.get(ext)
            if kind:
                counts[kind] = counts.get(kind, 0) + 1
    return counts


def _pick_kind(counts: dict[str, int]) -> Optional[tuple[str, int]]:
    """Choose the dominant kind for a repo: the one with the highest
    count, provided it clears the floor. Returns (kind, count) or None.
    """
    if not counts:
        return None
    kind, n = max(counts.items(), key=lambda kv: kv[1])
    if n < _MIN_FILES_PER_KIND:
        return None
    return kind, n


def _candidate_name(prefix: str, entry: dict) -> str:
    """Pick a stable slug from a portfolio entry. Prefers `slug`, then
    `name`, then a sanitized form of `local_path`'s basename.
    """
    for key in ("slug", "name", "id"):
        v = entry.get(key)
        if isinstance(v, str) and v.strip():
            return f"{prefix}{v.strip().lower()}"
    path = entry.get("local_path") or entry.get("path")
    if isinstance(path, str) and path:
        return f"{prefix}{Path(path).name.lower()}"
    return f"{prefix}unnamed"


def _local_root(entry: dict) -> Optional[Path]:
    """Resolve the on-disk root for a portfolio entry, or None if the
    entry has no `local_path` / the path doesn't exist.
    """
    for key in ("local_path", "path", "root"):
        v = entry.get(key)
        if isinstance(v, str) and v.strip():
            p = Path(v).expanduser()
            if p.exists() and p.is_dir():
                return p
    return None


# ── Public entry point ────────────────────────────────────────────

def discover_sources(
    *,
    portfolio_loader: Optional[PortfolioLoader] = None,
    already_registered: Iterable[str] = (),
) -> list[DiscoveredSource]:
    """Return the list of candidate sources, sorted by file count desc.

    `already_registered` is intersected against to skip repos that
    already have a hand-coded entry in `KNOWN_SOURCES`. Discovery is
    additive only. Replacing an existing entry is never proposed.
    """
    loader = portfolio_loader or _default_portfolio_loader
    data = loader() or {}
    registered = {n.lower() for n in already_registered}

    candidates: list[DiscoveredSource] = []

    for entry in data.get("repos", []):
        cand = _entry_to_candidate(entry, origin="portfolio_repo", prefix="")
        if cand and cand.name.lower() not in registered:
            candidates.append(cand)

    for entry in data.get("datasets", []):
        cand = _entry_to_candidate(
            entry, origin="portfolio_dataset", prefix="dataset-"
        )
        if cand and cand.name.lower() not in registered:
            candidates.append(cand)

    candidates.sort(key=lambda c: c.file_count, reverse=True)
    return candidates


def _entry_to_candidate(
    entry: dict, *, origin: str, prefix: str
) -> Optional[DiscoveredSource]:
    root = _local_root(entry)
    if root is None:
        return None
    counts = _count_kinds(root)
    picked = _pick_kind(counts)
    if picked is None:
        return None
    kind, n = picked
    name = _candidate_name(prefix, entry)
    description = (
        entry.get("description")
        or entry.get("summary")
        or f"Auto-discovered {origin.split('_')[-1]} from crowe-portfolio."
    )
    return DiscoveredSource(
        name=name,
        kind=kind,
        root=root,
        description=description.strip(),
        file_count=n,
        origin=origin,
    )


__all__ = [
    "DiscoveredSource",
    "PortfolioLoader",
    "discover_sources",
]
