"""Tests for /api/kb/search HTTP route.

Uses a per-test SQLite KB at tmp_path, then points the Store default
at it via the CROWE_KB_DB env var. Auth is stubbed identically to the
chat-history test suite.
"""

from __future__ import annotations

import contextlib

import pytest

pytest.importorskip("fastapi")

import control_plane.gateway as gateway_mod


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Point the knowledge-lake DB at a fresh sqlite file under tmp_path.
    db_path = tmp_path / "kb.db"
    monkeypatch.setenv("CROWE_KB_DB", str(db_path))

    # Seed the lake before the app boots so the route returns real
    # hits, not an empty set.
    from knowledge_lake.store import Store
    s = Store(db_path)
    s.upsert_source("test-src", "markdown", root=str(tmp_path), description="t")
    s.replace_chunks("test-src", [
        ("a.md", 0, "Mycelial succession across hardwood substrates.", {}),
        ("a.md", 1, "Pleurotus ostreatus colonizes oak in 14 days.", {}),
        ("b.md", 0, "Crowe Logic exposes /v1/chat/completions.", {}),
    ])

    # Stub the DB lifespan + the auth resolver.
    import control_plane.db as db_mod

    @contextlib.asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    async def _noop_init():
        return None

    monkeypatch.setattr(db_mod, "init_pool", _noop_init)
    monkeypatch.setattr(db_mod, "lifespan", _noop_lifespan)

    import control_plane.main  # noqa: F401

    async def _fake_resolver():
        return {
            "user_id": "u_test",
            "workspace_id": "ws_test",
            "plan_id": "personal",
            "ws_status": "active",
        }

    from control_plane import app
    app.dependency_overrides[gateway_mod._resolve_api_key] = _fake_resolver
    app.router.lifespan_context = _noop_lifespan

    from fastapi.testclient import TestClient
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def test_kb_search_returns_ranked_hits(client):
    resp = client.get("/api/kb/search", params={"q": "hardwood"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "hardwood"
    assert body["count"] == 1
    hit = body["hits"][0]
    assert hit["source"] == "test-src"
    assert hit["path"] == "a.md"
    assert "hardwood" in hit["snippet"].lower()
    assert isinstance(hit["score"], float)


def test_kb_search_scoped_by_source(client):
    resp = client.get(
        "/api/kb/search",
        params={"q": "crowe", "source": "test-src"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["hits"][0]["path"] == "b.md"


def test_kb_search_rejects_empty_query(client):
    # FastAPI's Query(min_length=1) validates before the handler runs.
    resp = client.get("/api/kb/search", params={"q": ""})
    assert resp.status_code == 422


def test_kb_search_respects_limit(client):
    resp = client.get("/api/kb/search", params={"q": "mycelial OR pleurotus", "limit": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["hits"]) == 1


def test_kb_search_returns_empty_for_no_match(client):
    resp = client.get("/api/kb/search", params={"q": "thiswordappearsnowhere"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 0
    assert body["hits"] == []


def test_kb_sources_lists_ingested(client):
    resp = client.get("/api/kb/sources")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] >= 1
    names = [s["name"] for s in body["sources"]]
    assert "test-src" in names


def test_kb_search_requires_active_workspace(client, monkeypatch):
    async def _suspended():
        return {
            "user_id": "u",
            "workspace_id": "w",
            "plan_id": "personal",
            "ws_status": "suspended",
        }
    from control_plane import app
    app.dependency_overrides[gateway_mod._resolve_api_key] = _suspended
    resp = client.get("/api/kb/search", params={"q": "anything"})
    assert resp.status_code == 403


def test_kb_search_malformed_fts_returns_400(client):
    # Unbalanced quote → FTS5 raises; we map to 400 with a useful message.
    resp = client.get("/api/kb/search", params={"q": '"unterminated'})
    assert resp.status_code == 400
    assert "bad query" in resp.json()["detail"].lower()


def test_kb_chunk_exact_lookup(client):
    resp = client.get(
        "/api/kb/chunk",
        params={"source": "test-src", "path": "a.md", "chunk_index": 0},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "test-src"
    assert body["path"] == "a.md"
    assert body["chunk_index"] == 0
    assert "Mycelial succession" in body["content"]
    assert isinstance(body["metadata"], dict)


def test_kb_chunk_404_when_not_found(client):
    resp = client.get(
        "/api/kb/chunk",
        params={"source": "test-src", "path": "no-such.md", "chunk_index": 99},
    )
    assert resp.status_code == 404


def test_kb_chunk_requires_active_workspace(client, monkeypatch):
    async def _suspended():
        return {
            "user_id": "u",
            "workspace_id": "w",
            "plan_id": "personal",
            "ws_status": "suspended",
        }
    from control_plane import app
    app.dependency_overrides[gateway_mod._resolve_api_key] = _suspended
    resp = client.get(
        "/api/kb/chunk",
        params={"source": "test-src", "path": "a.md", "chunk_index": 0},
    )
    assert resp.status_code == 403
