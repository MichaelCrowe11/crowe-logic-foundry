# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
"""
Multi-device chat history routes.

Backs `chat.crowelogic.com` (and any future Crowe surface) with a
durable transcript. Authentication is the same `_resolve_api_key`
the gateway uses, so both workspace API keys and browser session
JWTs work transparently.

Routes (mounted at /api/sessions):
  POST   /api/sessions                 create a new session
  GET    /api/sessions                 list this user's sessions (paginated)
  GET    /api/sessions/{id}            full session + messages
  POST   /api/sessions/{id}/messages   append a message
  DELETE /api/sessions/{id}            delete a session (cascades to messages)

Workspace scoping: every row is keyed on (workspace_id, user_id). The
auth resolver gives us both, so cross-tenant reads are impossible
without admin tooling.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .db import Database, get_db
from .gateway import _resolve_api_key


router = APIRouter(prefix="/api/sessions", tags=["chat-history"])


# ─── Models ────────────────────────────────────────────────────────

class CreateSessionRequest(BaseModel):
    title: Optional[str] = None
    model: Optional[str] = None  # CroweLM codename


class AppendMessageRequest(BaseModel):
    role: str = Field(..., pattern="^(user|assistant|system)$")
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionSummary(BaseModel):
    id: str
    title: str
    model: Optional[str]
    created_at: str
    updated_at: str


class ChatMessage(BaseModel):
    id: str
    role: str
    content: str
    metadata: dict[str, Any]
    created_at: str


class SessionDetail(SessionSummary):
    messages: list[ChatMessage]


# ─── Helpers ───────────────────────────────────────────────────────

def _iso(dt) -> str:
    """Format a TIMESTAMPTZ value as ISO 8601 with the trailing Z so JS
    Date can parse it consistently across timezones.
    """
    return dt.isoformat().replace("+00:00", "Z") if dt else ""


async def _own_session_or_404(
    session_id: str, key_info: dict, db: Database,
) -> dict:
    """Fetch a session that belongs to the resolved principal, else
    return 404 (not 403 — leaks fewer existence bits)."""
    row = await db.fetchrow(
        """SELECT * FROM chat_sessions
           WHERE id = $1 AND workspace_id = $2 AND user_id = $3""",
        session_id, key_info["workspace_id"], key_info["user_id"],
    )
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    return dict(row)


# ─── Routes ────────────────────────────────────────────────────────

@router.post("", status_code=201)
async def create_session(
    req: CreateSessionRequest,
    key_info: dict = Depends(_resolve_api_key),
    db: Database = Depends(get_db),
) -> SessionSummary:
    """Create an empty chat session and return its summary."""
    row = await db.fetchrow(
        """INSERT INTO chat_sessions (workspace_id, user_id, title, model)
           VALUES ($1, $2, COALESCE($3, 'New chat'), $4)
           RETURNING id, title, model, created_at, updated_at""",
        key_info["workspace_id"], key_info["user_id"],
        req.title, req.model,
    )
    return SessionSummary(
        id=row["id"], title=row["title"], model=row["model"],
        created_at=_iso(row["created_at"]), updated_at=_iso(row["updated_at"]),
    )


@router.get("")
async def list_sessions(
    limit: int = 50,
    offset: int = 0,
    key_info: dict = Depends(_resolve_api_key),
    db: Database = Depends(get_db),
) -> dict:
    """List this user's sessions, most-recent first."""
    limit = max(1, min(limit, 200))
    rows = await db.fetch(
        """SELECT id, title, model, created_at, updated_at
           FROM chat_sessions
           WHERE workspace_id = $1 AND user_id = $2
           ORDER BY updated_at DESC
           LIMIT $3 OFFSET $4""",
        key_info["workspace_id"], key_info["user_id"], limit, offset,
    )
    items = [
        SessionSummary(
            id=r["id"], title=r["title"], model=r["model"],
            created_at=_iso(r["created_at"]), updated_at=_iso(r["updated_at"]),
        ).model_dump()
        for r in rows
    ]
    return {"items": items, "limit": limit, "offset": offset}


@router.get("/{session_id}")
async def get_session(
    session_id: str,
    key_info: dict = Depends(_resolve_api_key),
    db: Database = Depends(get_db),
) -> SessionDetail:
    """Full session with messages, oldest first."""
    session = await _own_session_or_404(session_id, key_info, db)
    msgs = await db.fetch(
        """SELECT id, role, content, metadata, created_at
           FROM chat_messages
           WHERE session_id = $1
           ORDER BY created_at ASC""",
        session_id,
    )
    return SessionDetail(
        id=session["id"],
        title=session["title"],
        model=session["model"],
        created_at=_iso(session["created_at"]),
        updated_at=_iso(session["updated_at"]),
        messages=[
            ChatMessage(
                id=m["id"], role=m["role"], content=m["content"],
                metadata=dict(m["metadata"]) if m["metadata"] else {},
                created_at=_iso(m["created_at"]),
            )
            for m in msgs
        ],
    )


@router.post("/{session_id}/messages", status_code=201)
async def append_message(
    session_id: str,
    req: AppendMessageRequest,
    key_info: dict = Depends(_resolve_api_key),
    db: Database = Depends(get_db),
) -> ChatMessage:
    """Append one message. The chat_messages trigger bumps the session's
    updated_at so the sidebar's recency ordering stays correct without
    an explicit UPDATE here.
    """
    await _own_session_or_404(session_id, key_info, db)

    import json as _json
    row = await db.fetchrow(
        """INSERT INTO chat_messages (session_id, role, content, metadata)
           VALUES ($1, $2, $3, $4::jsonb)
           RETURNING id, role, content, metadata, created_at""",
        session_id, req.role, req.content, _json.dumps(req.metadata or {}),
    )

    # First message often makes a better title than the placeholder.
    # Best-effort: if the session is still "New chat", derive a title
    # from the first user message (first 60 chars, single line).
    if req.role == "user":
        await db.execute(
            """UPDATE chat_sessions
               SET title = CASE
                       WHEN title = 'New chat' THEN substring(regexp_replace($2, E'\\s+', ' ', 'g') from 1 for 60)
                       ELSE title
                   END
               WHERE id = $1""",
            session_id, req.content,
        )

    metadata = row["metadata"]
    if isinstance(metadata, str):
        metadata = _json.loads(metadata)
    return ChatMessage(
        id=row["id"], role=row["role"], content=row["content"],
        metadata=dict(metadata) if metadata else {},
        created_at=_iso(row["created_at"]),
    )


@router.delete("/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    key_info: dict = Depends(_resolve_api_key),
    db: Database = Depends(get_db),
) -> None:
    await _own_session_or_404(session_id, key_info, db)
    await db.execute("DELETE FROM chat_sessions WHERE id = $1", session_id)
    return None
