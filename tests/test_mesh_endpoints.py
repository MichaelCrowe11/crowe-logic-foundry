"""Tests for the mesh-visibility endpoints (/mesh/tools, /mesh/surfaces, WS /mesh/attach)."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from control_plane import app


def _client() -> TestClient:
    return TestClient(app)


def test_mesh_tools_lists_runtime_tools():
    resp = _client().get("/mesh/tools")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) > 0
    entry = data[0]
    assert {"name", "description", "surface"} <= set(entry)
    assert all(t["surface"] in {"foundry-runtime", "terminal"} for t in data)


def test_mesh_surfaces_includes_self():
    resp = _client().get("/mesh/surfaces")
    assert resp.status_code == 200
    data = resp.json()
    ids = {s["id"] for s in data}
    assert "foundry-runtime" in ids
    runtime = next(s for s in data if s["id"] == "foundry-runtime")
    assert runtime["reachable"] is True
    assert runtime["tool_count"] >= 1
    assert "cmp_version" in runtime


def test_mesh_attach_handshake_and_ping():
    client = _client()
    with client.websocket_connect("/mesh/attach") as ws:
        ws.send_json({"type": "attach", "session_id": "s1", "surface_id": "cla"})
        ack = ws.receive_json()
        assert ack["type"] == "attach_ack"
        assert ack["session_id"] == "s1"
        joined = ws.receive_json()
        assert joined["type"] == "surface_joined"
        ws.send_json({"type": "ping", "ts": 1})
        pong = ws.receive_json()
        assert pong["type"] == "pong"


def test_mesh_stream_emits_cmp_sse(monkeypatch):
    """POST /mesh/stream translates v0 -> CMP and frames as SSE."""
    import control_plane.streaming as streaming_mod
    from control_plane.gateway import _resolve_api_key

    async def _fake_events(*, messages, model_id, session_id):
        for ev in [
            {"type": "ready"},
            {"type": "token", "delta": "hi"},
            {
                "type": "done",
                "tokens": 1,
                "reasoning_tokens": 0,
                "elapsed_ms": 1,
                "ttft_ms": 1,
            },
        ]:
            yield ev

    monkeypatch.setattr(streaming_mod, "stream_agent_events", _fake_events)
    monkeypatch.setenv("CROWE_STREAM_ENABLED", "1")
    # Re-evaluate the module-level flag the endpoint reads.
    import control_plane.mesh as mesh_mod

    monkeypatch.setattr(mesh_mod, "CROWE_STREAM_ENABLED", True)

    app.dependency_overrides[_resolve_api_key] = lambda: {
        "workspace_id": "ws-abc123456789",
        "user_id": "u1",
    }
    try:
        with _client().stream(
            "POST",
            "/mesh/stream",
            json={"messages": [{"role": "user", "content": "hi"}], "model": "auto"},
        ) as resp:
            assert resp.status_code == 200
            body = "".join(resp.iter_text())
    finally:
        app.dependency_overrides.clear()

    assert '"type":"ready"' in body
    assert '"model_tier":"auto"' in body
    assert '"type":"token"' in body and '"delta":"hi"' in body
    assert '"session_id"' in body  # every CMP event is session-scoped
