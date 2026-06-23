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
