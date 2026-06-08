"""CroweLM Supreme must remain a distinct Vertex Claude frontier tier."""

from config.agent_config import (
    MODEL_CHAIN,
    anthropic_runtime_config,
    resolve_model_config,
)


def _supreme():
    return resolve_model_config("CroweLM Supreme")


def test_supreme_is_vertex_claude_opus():
    cfg = _supreme()
    assert cfg is not None
    assert cfg["provider"] == "anthropic"
    assert cfg["backend_name"] == "claude-opus-4-8"
    assert cfg["name"] == "crowelm-supreme"
    assert cfg["vertex_project"] == "crowe-workspaces"
    assert cfg["vertex_region"] == "us-east5"


def test_supreme_label_survives_merge():
    # Regression: the Supreme label must remain a distinct tier in the chain.
    assert "CroweLM Supreme" in {m["label"] for m in MODEL_CHAIN}


def test_supreme_available_when_vertex_adc_configured(monkeypatch):
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/fake-adc.json")
    from cli.crowe_logic import _auto_route_available

    assert _auto_route_available(_supreme()) is True


def test_supreme_runtime_uses_vertex_endpoint(monkeypatch):
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/fake-adc.json")
    runtime = anthropic_runtime_config(_supreme())

    assert runtime["missing"] == ()
    assert runtime["endpoint"] == "vertex:crowe-workspaces/us-east5"
