# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
"""
Registered knowledge-lake sources.

Each entry is the minimum the CLI needs to find a corpus and route
it to the right ingestor. Hand-coded entries live below; the
auto-discovery overlay from `kb discover --register` is merged in
at the bottom of this module via `_apply_overlay()`.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class Source:
    name: str                      # unique slug
    kind: str                      # markdown | jsonl | latex | ...
    root: Path                     # filesystem path (or URL placeholder)
    description: str
    include_globs: tuple[str, ...] = field(default_factory=tuple)
    exclude_globs: tuple[str, ...] = field(default_factory=tuple)


# Filesystem roots resolved at import. Paths that don't exist on this
# machine are still registered; `crowe-logic kb ingest <name>` will
# fail with a clear "root missing" message instead of silently
# vanishing from the registry.
_HOME = Path.home()
_FOUNDRY = _HOME / "Projects" / "crowe-logic-foundry"


KNOWN_SOURCES: dict[str, Source] = {
    "foundry-docs": Source(
        name="foundry-docs",
        kind="markdown",
        root=_FOUNDRY,
        description="Crowe Logic Foundry's own .md documentation.",
        include_globs=("*.md", "docs/**/*.md", "control_plane/**/*.md"),
        exclude_globs=(
            "node_modules/**",
            ".venv/**",
            ".pi/**",                 # pi extension docs (not foundry-canonical)
            "**/test_*.md",
        ),
    ),
    "crowelm-unified-dataset": Source(
        name="crowelm-unified-dataset",
        kind="markdown",
        root=_HOME / "crowelm-unified-dataset",
        description="NVIDIA Biotech + Mycology AI training pipeline notes.",
        include_globs=("*.md", "**/*.md"),
        exclude_globs=("node_modules/**", ".venv/**"),
    ),
    "mushroom-cultivators-masterclass": Source(
        name="mushroom-cultivators-masterclass",
        kind="latex",
        root=_HOME / "Projects" / "mushroom-cultivators-masterclass",
        description="28-chapter, 192K-word commercial cultivation masterclass.",
        # LaTeX ingestor exists since Phase 2; left here so the
        # registered-state of this corpus stays explicit.
    ),
    "michael-crowe-mushroom-cultivation-handbook": Source(
        name="michael-crowe-mushroom-cultivation-handbook",
        kind="latex",
        root=_HOME / "Projects" / "michael-crowe-mushroom-cultivation-handbook",
        description="286K-word comprehensive cultivation guide (LaTeX).",
    ),
    "themushroomgrower": Source(
        name="themushroomgrower",
        kind="latex",
        root=_HOME / "Projects" / "themushroomgrower",
        description="Two-volume Mushroom Grower book (LaTeX).",
    ),
}


def register(source: Source) -> None:
    """Add (or replace) a source at runtime. Useful for tests."""
    KNOWN_SOURCES[source.name] = source


def get(name: str) -> Optional[Source]:
    return KNOWN_SOURCES.get(name)


# ── Overlay (auto-discovered sources persisted to disk) ───────────

def _overlay_path() -> Path:
    """Location of the JSON overlay file. Honors XDG_CONFIG_HOME and
    falls back to ~/.config/crowe-logic/kb-sources.json.
    """
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base).expanduser() if base else (Path.home() / ".config")
    return root / "crowe-logic" / "kb-sources.json"


def _load_overlay(path: Optional[Path] = None) -> list[Source]:
    """Parse the overlay JSON into `Source` dataclasses. Missing file
    returns []. Malformed entries are skipped, not raised, so a single
    bad row can't take down the foundry's CLI.
    """
    p = path or _overlay_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return []
    items = data.get("sources") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    out: list[Source] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        try:
            out.append(Source(
                name=str(raw["name"]),
                kind=str(raw["kind"]),
                root=Path(raw["root"]).expanduser(),
                description=str(raw.get("description", "")),
                include_globs=tuple(raw.get("include_globs", ()) or ()),
                exclude_globs=tuple(raw.get("exclude_globs", ()) or ()),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _apply_overlay() -> None:
    """Merge overlay entries into KNOWN_SOURCES. Hand-coded entries win
    on conflict so the hardcoded canonical list is never overridden by
    a discovery accident.
    """
    for src in _load_overlay():
        KNOWN_SOURCES.setdefault(src.name, src)


def save_overlay(sources: list[Source], path: Optional[Path] = None) -> Path:
    """Persist `sources` to the overlay JSON. Returns the file path
    written. The caller is responsible for merging vs replacing; this
    function unconditionally rewrites the file.
    """
    p = path or _overlay_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"sources": [
        {
            "name": s.name,
            "kind": s.kind,
            "root": str(s.root),
            "description": s.description,
            "include_globs": list(s.include_globs),
            "exclude_globs": list(s.exclude_globs),
        }
        for s in sources
    ]}
    p.write_text(json.dumps(payload, indent=2))
    return p


# Apply overlay at import time. Tests that need a clean registry can
# clear KNOWN_SOURCES and re-register the canonical entries directly.
_apply_overlay()
