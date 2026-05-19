# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
"""
HTTP surface for the knowledge lake.

GET /api/kb/search?q=<query>&source=<name>&limit=<n>

Returns ranked SearchHit dicts. Authentication reuses the gateway
resolver (JWT or workspace API key). Plan-gating is permissive on
purpose: any active workspace can query the lake — this is internal
knowledge, not metered inference.

This endpoint lets `chat.crowelogic.com` do retrieval-augmented
chat (the frontend can prepend top-N hits as system context before
calling /v1/chat/completions) without giving browsers direct DB
access.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .gateway import _resolve_api_key


router = APIRouter(prefix="/api/kb", tags=["knowledge-lake"])


@router.get("/search")
async def kb_search(
    q: str = Query(..., min_length=1, description="FTS5 MATCH query"),
    source: Optional[str] = Query(None, description="Restrict to one source."),
    limit: int = Query(10, ge=1, le=50),
    mode: str = Query(
        "fts",
        pattern="^(fts|vector|hybrid)$",
        description="fts (default), vector, or hybrid recall.",
    ),
    key_info: dict = Depends(_resolve_api_key),
) -> dict:
    """Search the knowledge lake. Returns ranked chunks with snippets.

    `mode=vector` and `mode=hybrid` embed `q` server-side via Azure
    OpenAI (when configured) and blend BM25 + cosine similarity. When
    no embedding provider is configured the request transparently
    falls back to FTS-only so callers don't need to detect the
    capability up front.
    """
    if key_info.get("ws_status") != "active":
        raise HTTPException(status_code=403, detail="Workspace suspended")

    from knowledge_lake.embeddings import embed_text, is_configured
    from knowledge_lake.search import search as _search
    from knowledge_lake.store import Store

    effective_mode = mode
    query_vector = None
    if mode in ("vector", "hybrid"):
        if is_configured():
            query_vector = embed_text(q)
        if query_vector is None:
            # Graceful fallback. Caller's UI shouldn't break just
            # because the embedding provider isn't wired up.
            effective_mode = "fts"

    try:
        if effective_mode == "fts":
            hits = _search(q, source=source, limit=limit)
            return {
                "query": q,
                "source": source,
                "mode": effective_mode,
                "count": len(hits),
                "hits": [h.to_dict() for h in hits],
            }
        # vector / hybrid path: use Store directly so we can pass the
        # vector. Then wrap rows in the same snippet-aware shape the
        # facade produces.
        store = Store()
        rows = store.search(
            q, source=source, limit=limit, mode=effective_mode,
            query_vector=query_vector,
        )
        from knowledge_lake.search import _snippet  # noqa: PLC2701
        return {
            "query": q,
            "source": source,
            "mode": effective_mode,
            "count": len(rows),
            "hits": [
                {
                    "source": r["source"],
                    "path": r["path"],
                    "chunk_index": r["chunk_index"],
                    "score": float(r["score"]),
                    "snippet": _snippet(r["content"], q),
                    "metadata": r.get("metadata", {}),
                }
                for r in rows
            ],
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Bad query: {exc}")


@router.get("/sources")
async def kb_sources(
    key_info: dict = Depends(_resolve_api_key),
) -> dict:
    """List ingested sources visible to the chat surface."""
    if key_info.get("ws_status") != "active":
        raise HTTPException(status_code=403, detail="Workspace suspended")
    from knowledge_lake.store import Store

    store = Store()
    sources = [
        {
            "name": s.name,
            "kind": s.kind,
            "chunk_count": s.chunk_count,
            "last_ingested_at": s.last_ingested_at,
            "description": s.description,
        }
        for s in store.list_sources()
    ]
    return {"count": len(sources), "sources": sources}


@router.get("/chunk")
async def kb_chunk(
    source: str = Query(..., description="Source name (e.g. foundry-docs)."),
    path: str = Query(..., description="Path within the source."),
    chunk_index: int = Query(0, ge=0, description="Chunk index within the file."),
    key_info: dict = Depends(_resolve_api_key),
) -> dict:
    """Exact-chunk lookup by citation triple.

    The chip drawer on chat.crowelogic.com calls this when a user
    clicks a citation. Returns the full chunk content plus its
    metadata so the drawer can render it without an FTS round trip.
    """
    if key_info.get("ws_status") != "active":
        raise HTTPException(status_code=403, detail="Workspace suspended")
    from knowledge_lake.store import Store

    store = Store()
    chunk = store.get_chunk(source=source, path=path, chunk_index=chunk_index)
    if not chunk:
        raise HTTPException(status_code=404, detail="Chunk not found")
    return chunk
