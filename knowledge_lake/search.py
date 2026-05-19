# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
"""
Search facade.

Thin wrapper around `Store.search` that returns typed hits + a
content snippet trimmed to a useful preview. CLI and (future)
HTTP surfaces both call through this so ranking / preview logic
stays in one place.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Optional

from knowledge_lake.store import Store


@dataclass(frozen=True)
class SearchHit:
    source: str
    path: str
    chunk_index: int
    score: float        # bm25 — lower is better; we expose it raw
    snippet: str
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_SNIPPET_CHARS = 320


def _snippet(content: str, query: str) -> str:
    """Pick a window around the first keyword hit; fall back to head."""
    if not content:
        return ""
    q_tokens = [t for t in query.split() if t and not t.startswith('"')]
    if q_tokens:
        lower = content.lower()
        for tok in q_tokens:
            i = lower.find(tok.lower())
            if i >= 0:
                start = max(0, i - 80)
                end = min(len(content), i + _SNIPPET_CHARS - 80)
                prefix = "…" if start > 0 else ""
                suffix = "…" if end < len(content) else ""
                return prefix + content[start:end].strip() + suffix
    return content[:_SNIPPET_CHARS].strip() + ("…" if len(content) > _SNIPPET_CHARS else "")


def search(
    query: str,
    *,
    store: Optional[Store] = None,
    source: Optional[str] = None,
    limit: int = 10,
) -> list[SearchHit]:
    store = store or Store()
    rows = store.search(query, source=source, limit=limit)
    return [
        SearchHit(
            source=r["source"],
            path=r["path"],
            chunk_index=r["chunk_index"],
            score=float(r["score"]),
            snippet=_snippet(r["content"], query),
            metadata=r["metadata"] if isinstance(r["metadata"], dict) else {},
        )
        for r in rows
    ]
