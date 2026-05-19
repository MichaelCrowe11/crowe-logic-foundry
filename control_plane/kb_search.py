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
    key_info: dict = Depends(_resolve_api_key),
) -> dict:
    """Search the knowledge lake. Returns ranked chunks with snippets."""
    if key_info.get("ws_status") != "active":
        raise HTTPException(status_code=403, detail="Workspace suspended")

    # Lazy import so the foundry control-plane bootstrap doesn't pay
    # the SQLite-open cost when nobody is using the lake yet.
    from knowledge_lake.search import search as _search

    try:
        hits = _search(q, source=source, limit=limit)
    except Exception as exc:  # noqa: BLE001
        # FTS5 has terse error messages on malformed MATCH expressions;
        # surface them as 400 so the client can iterate.
        raise HTTPException(status_code=400, detail=f"Bad query: {exc}")

    return {
        "query": q,
        "source": source,
        "count": len(hits),
        "hits": [h.to_dict() for h in hits],
    }


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
