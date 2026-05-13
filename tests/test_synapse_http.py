"""Tests for the FastAPI HTTP surface of the synapse runtime.

The OpenAI client is monkeypatched (same pattern as
``tests/test_synapse_runtime.py``) so the suite never hits a network.
MemoryStore is redirected to a tmp_path DB via the
``CROWE_SYNAPSE_MEMORY_DB`` env var the server module honors.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Return a TestClient with the model client mocked + memory redirected."""
    monkeypatch.setenv("CROWE_SYNAPSE_MEMORY_DB", str(tmp_path / "memory.db"))

    import crowe_synapse_engine.runtime.synapse as synapse_module

    class FakeCompletions:
        def create(self, **_kwargs):
            return [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(
                                content="Voice leading.", tool_calls=None
                            ),
                            finish_reason="stop",
                        )
                    ]
                )
            ]

    class FakeClient:
        chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(
        synapse_module, "_resolve_client", lambda _provider, **_kw: FakeClient()
    )

    from crowe_synapse_engine.http import app

    return TestClient(app)


def test_list_agents_returns_registry_entries(client):
    response = client.get("/agents")
    assert response.status_code == 200
    rows = response.json()
    assert isinstance(rows, list)
    assert len(rows) >= 9, "expected at least the bundled YAML agents"
    names = {row["name"] for row in rows}
    assert "research" in names
    assert "music-compose" in names
    # Every row has the contract shape.
    for row in rows:
        assert {"name", "model", "tools"}.issubset(row.keys())


def test_run_streams_sse_with_intent_and_commit(client):
    response = client.post(
        "/run",
        json={
            "agent_name": "music-compose",
            "prompt": "Define a good section transition.",
            "max_turns": 2,
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers.get("X-Session-Id"), "X-Session-Id header is missing"
    body = response.text
    assert "data: " in body
    assert "data: [DONE]" in body, "stream must terminate with the [DONE] sentinel"
    # AICL frames must appear at boundaries.
    assert '"act": "intent"' in body or '"act":"intent"' in body
    assert '"act": "commit"' in body or '"act":"commit"' in body


def test_run_persists_aicl_to_memory(client):
    response = client.post(
        "/run",
        json={
            "agent_name": "music-compose",
            "prompt": "Define a good section transition.",
            "max_turns": 2,
        },
    )
    session_id = response.headers["X-Session-Id"]

    aicl_response = client.get(f"/sessions/{session_id}/aicl")
    assert aicl_response.status_code == 200
    assert aicl_response.headers["content-type"].startswith("application/x-ndjson")
    lines = [line for line in aicl_response.text.splitlines() if line.strip()]
    assert len(lines) >= 2, "expected at least intent + commit messages"
    acts = []
    import json as _json

    for line in lines:
        msg = _json.loads(line)
        acts.append(msg["act"])
    assert "intent" in acts
    assert "commit" in acts


def test_run_rejects_missing_agent_identifier(client):
    response = client.post("/run", json={"prompt": "Hello."})
    assert response.status_code == 400
    assert "agent_name" in response.text or "agent_path" in response.text


def test_run_rejects_empty_prompt(client):
    response = client.post(
        "/run", json={"agent_name": "music-compose", "prompt": "   "}
    )
    assert response.status_code == 400


def test_run_unknown_agent_returns_404(client):
    response = client.post(
        "/run",
        json={"agent_name": "no-such-agent", "prompt": "Hello."},
    )
    assert response.status_code == 404
    assert "not found" in response.text.lower()


def test_aicl_endpoint_returns_404_for_unknown_session(client):
    response = client.get("/sessions/does-not-exist/aicl")
    assert response.status_code == 404
