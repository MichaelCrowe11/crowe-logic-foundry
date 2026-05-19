# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
"""
Registered knowledge-lake sources.

Each entry is the minimum the CLI needs to find a corpus and route
it to the right ingestor. New sources are added here by hand —
auto-discovery from the crowe-portfolio MCP is intentionally
deferred to Phase 2 so the first ingest path stays predictable.
"""
from __future__ import annotations

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
# machine are still registered — `crowe-logic kb ingest <name>` will
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
        # Placeholder — LaTeX ingestor lands in Phase 2; the entry
        # is here so `kb sources` reflects intent.
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
    """Add (or replace) a source at runtime — useful for tests."""
    KNOWN_SOURCES[source.name] = source


def get(name: str) -> Optional[Source]:
    return KNOWN_SOURCES.get(name)
