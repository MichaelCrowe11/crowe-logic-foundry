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
