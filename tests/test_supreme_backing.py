"""CroweLM Supreme must resolve to a live Azure frontier (gpt-5.5 on AZURE_CORE).

It was hardwired to the unset AZURE_ANTHROPIC_* endpoint (claude-opus-4-7), which
is the "Supreme -> 18s timeout -> Talon" behavior. Repoint to live gpt-5.5.
"""

from config.agent_config import resolve_model_config


def _supreme():
    return resolve_model_config("CroweLM Supreme")


def test_supreme_is_azure_openai_gpt5():
    cfg = _supreme()
    assert cfg is not None
    assert cfg["provider"] == "azure_openai"
    assert cfg["name"] == "gpt-5.5"
    assert cfg.get("endpoint_env") == "AZURE_CORE_ENDPOINT"


def test_supreme_available_when_azure_core_configured(monkeypatch):
    monkeypatch.setenv("AZURE_CORE_ENDPOINT", "https://x.openai.azure.com")
    monkeypatch.setenv("AZURE_CORE_API_KEY", "k")
    from cli.crowe_logic import _auto_route_available

    assert _auto_route_available(_supreme()) is True
