"""Tests for gateway_client device-token support and defaults."""
import importlib


def test_default_gateway_is_api_crowelogic(monkeypatch):
    monkeypatch.delenv("CROWE_LOGIC_GATEWAY_URL", raising=False)
    import cli.gateway_client as gc
    importlib.reload(gc)
    assert gc.GATEWAY_BASE == "https://api.crowelogic.com"
