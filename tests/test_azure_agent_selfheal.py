"""Self-healing for a stale cached Azure agent id.

When the agent id cached in ``.agent_id`` points at an assistant that no
longer exists on the Azure project (deleted server-side), the legacy Azure
Agents path used to surface a cryptic ``No assistant found with id 'asst_...'``
and abort the turn. These tests pin the desired behavior: detect the stale
id at agent-init time, clear the cache, and raise a clear, failover-eligible
error so the model chain falls through to the next tier.
"""

import json

import pytest
from azure.core.exceptions import ResourceNotFoundError

import cli.crowe_logic as cl

STALE_ID = "asst_TDeKMFheYi5QwGijBWTUX1R8"


class _FakeThreads:
    def create(self):  # pragma: no cover - should not be reached for a stale agent
        raise AssertionError("thread created before agent validated")


class _FakeAgentsClient:
    """Minimal AgentsClient stand-in whose agent lookup 404s."""

    def __init__(self):
        self.threads = _FakeThreads()

    def get_agent(self, agent_id):
        raise ResourceNotFoundError(message=f"No assistant found with id '{agent_id}'.")


@pytest.fixture
def stale_agent_file(tmp_path, monkeypatch):
    """Point AGENT_ID_FILE at a temp file holding the stale id; reset cache."""
    f = tmp_path / ".agent_id"
    f.write_text(
        json.dumps({"agent_id": STALE_ID, "name": "crowe-logic", "model": "gpt-5.5"})
    )
    monkeypatch.setattr(cl, "AGENT_ID_FILE", str(f))
    monkeypatch.setitem(cl._model_state, "agent_id", None)
    monkeypatch.setattr(cl, "get_client", lambda: _FakeAgentsClient())
    return f


def test_ensure_azure_agents_clears_stale_agent_and_raises(stale_agent_file):
    azure_state = {"client": None, "agent_id": None, "thread": None}

    with pytest.raises(RuntimeError) as exc:
        cl._ensure_azure_agents(azure_state)

    # The error must be actionable: name the remediation, not a raw SDK 404.
    msg = str(exc.value).lower()
    assert "deploy" in msg

    # Self-heal: the stale cache is cleared so a later `deploy` starts clean.
    assert not stale_agent_file.exists()
    assert cl._model_state["agent_id"] is None


def test_is_failover_eligible_recognizes_missing_assistant():
    err = "Azure error: No assistant found with id 'asst_TDeKMFheYi5QwGijBWTUX1R8'."
    assert cl._is_failover_eligible_error(err) is True
