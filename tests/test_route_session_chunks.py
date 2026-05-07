# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Studio — proprietary, private repository.

"""Tests for tools.studio_route.route_session_chunks_to_tenant."""

import json
from pathlib import Path

import pytest
import yaml

import tools.capture as capture_mod
import tools.studio_route as route_mod


@pytest.fixture
def chunked_session(tmp_path, monkeypatch):
    """Fake a chunked live capture session under tmp_path.

    Writes a SESSIONS_DIR session JSON file with chunked=true and a
    chunk_dir containing 3 fake chunk-NNN.mp4 files.
    """
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    chunk_dir = tmp_path / "out" / "live-test"
    chunk_dir.mkdir(parents=True)

    chunk_paths = []
    for i in range(3):
        p = chunk_dir / f"chunk-{i:03d}.mp4"
        p.write_bytes(b"\x00\x00\x00\x20ftypisom" + bytes([i]) * 8)
        chunk_paths.append(p)

    session_id = "live-test"
    session_file = sessions_dir / f"{session_id}.json"
    session_file.write_text(json.dumps({
        "session_id": session_id,
        "pid": 99999,
        "path": str(chunk_dir / "chunk-%04d.mp4"),
        "log": str(sessions_dir / f"{session_id}.log"),
        "device_string": "0:0",
        "started_at": 1700000000.0,
        "cmd": [],
        "chunked": True,
        "chunk_seconds": 60,
    }, indent=2))

    monkeypatch.setattr(capture_mod, "SESSIONS_DIR", sessions_dir)
    return session_id, chunk_dir, sessions_dir


@pytest.fixture
def fake_tenant(tmp_path, monkeypatch):
    """Write a temp tenants yaml with raw_dir under tmp_path/inbox."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    tenant_root = tmp_path / "tenant_root"
    tenant_root.mkdir()
    tenants_path = tmp_path / "studio_tenants.yaml"
    tenants_path.write_text(yaml.safe_dump({
        "tenants": [
            {
                "name": "fake-tenant",
                "label": "Fake Tenant",
                "root": str(tenant_root),
                "raw_dir": str(inbox),
                "ingest_cmd": ["/bin/echo", "{session_id}", "{session_dir}"],
                "default_specs": {"width": 1920, "height": 1080},
                "platforms": [],
                "notes": "",
            },
        ],
    }))
    monkeypatch.setattr(route_mod, "TENANTS_PATH", tenants_path)
    return "fake-tenant", inbox


def test_route_session_chunks_bundles_and_runs_ingest_once(chunked_session, fake_tenant):
    session_id, chunk_dir, _ = chunked_session
    tenant_name, inbox = fake_tenant

    result_raw = route_mod.route_session_chunks_to_tenant(session_id, tenant_name)
    result = json.loads(result_raw)

    assert "error" not in result, f"unexpected error: {result}"
    assert result["tenant"] == tenant_name
    assert result["session_id"] == session_id
    assert len(result["chunks"]) == 3
    assert {c["name"] for c in result["chunks"]} == {
        "chunk-000.mp4", "chunk-001.mp4", "chunk-002.mp4",
    }
    assert result["total_bytes"] > 0
    assert result["moved"] is False

    session_dir = Path(result["session_dir"])
    assert session_dir.parent == inbox
    assert session_dir.name == session_id
    files = {p.name for p in session_dir.iterdir() if p.is_file()}
    assert {"chunk-000.mp4", "chunk-001.mp4", "chunk-002.mp4", "session.json"} <= files

    bundle_meta = json.loads((session_dir / "session.json").read_text())
    assert bundle_meta["chunked"] is True
    assert bundle_meta["chunk_count"] == 3
    assert bundle_meta["session_id"] == session_id
    assert bundle_meta["started_at"] == 1700000000.0
    assert "bundled_at" in bundle_meta

    ingest = result["ingest"]
    assert ingest is not None
    assert ingest["exit"] == 0
    assert session_id in ingest["stdout_tail"]
    # ingest_cmd ran ONCE, not once per chunk
    assert ingest["cmd"][0] == "/bin/echo"
    assert ingest["cmd"][1] == session_id
    assert ingest["cmd"][2] == str(session_dir)

    # Source chunks are still present (copy, not move)
    assert all((chunk_dir / f"chunk-{i:03d}.mp4").exists() for i in range(3))
