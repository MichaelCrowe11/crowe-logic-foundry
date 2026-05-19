"""Tests for /api/sessions chat history routes.

We mock the DB with an in-memory dict-of-lists and the auth resolver
with a static principal so the FastAPI route layer is exercised
end-to-end without needing Neon.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi")

import control_plane.gateway as gateway_mod
from control_plane.chat_history import _own_session_or_404  # noqa


# ---------------------------------------------------------------------
# In-memory DB stub
# ---------------------------------------------------------------------

class _MemDb:
    """Hand-rolled enough to satisfy the routes.

    Implements the SQL the chat_history module actually emits — by
    pattern-matching on the leading verb of the query. Keeps the test
    fixture self-contained without dragging in sqlite or testcontainers.
    """

    def __init__(self) -> None:
        self.sessions: dict[str, dict] = {}
        self.messages: dict[str, list[dict]] = {}  # session_id -> [msg]

    async def fetchrow(self, sql: str, *args):
        q = " ".join(sql.split()).lower()
        if q.startswith("insert into chat_sessions"):
            ws_id, user_id, title, model = args
            sid = uuid.uuid4().hex
            now = datetime.now(timezone.utc)
            row = {
                "id": sid,
                "workspace_id": ws_id,
                "user_id": user_id,
                "title": title or "New chat",
                "model": model,
                "created_at": now,
                "updated_at": now,
            }
            self.sessions[sid] = row
            self.messages[sid] = []
            return row
        if q.startswith("select * from chat_sessions where id ="):
            sid, ws_id, user_id = args
            row = self.sessions.get(sid)
            if not row or row["workspace_id"] != ws_id or row["user_id"] != user_id:
                return None
            return row
        if q.startswith("insert into chat_messages"):
            session_id, role, content, metadata_json = args
            import json as _json
            mid = uuid.uuid4().hex
            now = datetime.now(timezone.utc)
            row = {
                "id": mid,
                "session_id": session_id,
                "role": role,
                "content": content,
                "metadata": _json.loads(metadata_json) if metadata_json else {},
                "created_at": now,
            }
            self.messages.setdefault(session_id, []).append(row)
            # mirror the trigger
            if session_id in self.sessions:
                self.sessions[session_id]["updated_at"] = now
            return row
        return None

    async def fetch(self, sql: str, *args):
        q = " ".join(sql.split()).lower()
        if q.startswith("select id, title, model"):
            # list_sessions
            ws_id, user_id, limit, offset = args
            rows = [
                s for s in self.sessions.values()
                if s["workspace_id"] == ws_id and s["user_id"] == user_id
            ]
            rows.sort(key=lambda r: r["updated_at"], reverse=True)
            return rows[offset:offset + limit]
        if q.startswith("select id, role, content"):
            # get_session messages
            session_id, = args
            return sorted(
                self.messages.get(session_id, []),
                key=lambda m: m["created_at"],
            )
        return []

    async def execute(self, sql: str, *args):
        q = " ".join(sql.split()).lower()
        if q.startswith("delete from chat_sessions"):
            sid, = args
            self.sessions.pop(sid, None)
            self.messages.pop(sid, None)
        elif q.startswith("update chat_sessions"):
            sid, content = args
            s = self.sessions.get(sid)
            if s and s["title"] == "New chat":
                title = " ".join(content.split())[:60]
                s["title"] = title
        return None


# ---------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    import control_plane.db as db_mod

    @contextlib.asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    async def _noop_init():
        return None

    monkeypatch.setattr(db_mod, "init_pool", _noop_init)
    monkeypatch.setattr(db_mod, "lifespan", _noop_lifespan)

    # Triggers router registration (chat_history + gateway + openai).
    import control_plane.main  # noqa: F401

    from control_plane import app
    from control_plane.db import get_db

    mem = _MemDb()

    async def _fake_resolver():
        return {
            "user_id": "user_test",
            "workspace_id": "ws_test",
            "plan_id": "scale",
            "ws_status": "active",
        }

    async def _fake_db():
        return mem

    app.dependency_overrides[gateway_mod._resolve_api_key] = _fake_resolver
    app.dependency_overrides[get_db] = _fake_db
    app.router.lifespan_context = _noop_lifespan

    from fastapi.testclient import TestClient
    try:
        with TestClient(app) as c:
            c.mem = mem  # type: ignore[attr-defined]
            yield c
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------

def test_create_session_returns_summary(client):
    resp = client.post("/api/sessions", json={"title": "First", "model": "CroweLM Helio"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "First"
    assert body["model"] == "CroweLM Helio"
    assert "id" in body and "created_at" in body and "updated_at" in body


def test_create_session_default_title(client):
    resp = client.post("/api/sessions", json={})
    assert resp.status_code == 201
    assert resp.json()["title"] == "New chat"


def test_list_sessions_most_recent_first(client):
    s1 = client.post("/api/sessions", json={"title": "A"}).json()
    s2 = client.post("/api/sessions", json={"title": "B"}).json()
    s3 = client.post("/api/sessions", json={"title": "C"}).json()
    listing = client.get("/api/sessions").json()
    titles = [it["title"] for it in listing["items"]]
    # Most recently created first.
    assert titles[0] == "C"
    assert set(titles) == {"A", "B", "C"}
    assert listing["limit"] == 50
    assert listing["offset"] == 0


def test_append_message_round_trip(client):
    sid = client.post("/api/sessions", json={}).json()["id"]
    r1 = client.post(
        f"/api/sessions/{sid}/messages",
        json={"role": "user", "content": "What is mycorrhizal succession?", "metadata": {}},
    )
    assert r1.status_code == 201
    r2 = client.post(
        f"/api/sessions/{sid}/messages",
        json={
            "role": "assistant",
            "content": "Mycorrhizal succession describes...",
            "metadata": {"model": "CroweLM Helio", "tokens": 42},
        },
    )
    assert r2.status_code == 201

    detail = client.get(f"/api/sessions/{sid}").json()
    assert len(detail["messages"]) == 2
    assert detail["messages"][0]["role"] == "user"
    assert detail["messages"][1]["metadata"]["tokens"] == 42
    # First user message should overwrite the placeholder title.
    assert detail["title"].startswith("What is mycorrhizal succession")


def test_append_to_unknown_session_returns_404(client):
    resp = client.post(
        "/api/sessions/does-not-exist/messages",
        json={"role": "user", "content": "hi"},
    )
    assert resp.status_code == 404


def test_get_unknown_session_returns_404(client):
    assert client.get("/api/sessions/nope").status_code == 404


def test_delete_session_removes_it(client):
    sid = client.post("/api/sessions", json={}).json()["id"]
    assert client.delete(f"/api/sessions/{sid}").status_code == 204
    assert client.get(f"/api/sessions/{sid}").status_code == 404


def test_invalid_role_rejected(client):
    sid = client.post("/api/sessions", json={}).json()["id"]
    resp = client.post(
        f"/api/sessions/{sid}/messages",
        json={"role": "robot", "content": "x"},
    )
    assert resp.status_code == 422
