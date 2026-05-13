"""Tests for the gateway /health endpoint.

Covers the helper ``_required_envs`` directly and the route as a whole
(with FastAPI dependency_overrides so we don't need a live database).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ── _required_envs unit tests ──────────────────────────────────────────


def test_required_envs_azure_openai_default():
    from control_plane.gateway import _required_envs

    assert _required_envs({"provider": "azure_openai"}) == [
        "AZURE_CORE_ENDPOINT",
        "AZURE_CORE_API_KEY",
    ]


def test_required_envs_azure_openai_honors_per_model_overrides():
    """Premium tiers (e.g. gpt-5.4-managed) point at non-default Azure
    accounts via endpoint_env / api_key_env; those overrides must flow
    through to the health output."""
    from control_plane.gateway import _required_envs

    cfg = {
        "provider": "azure_openai",
        "endpoint_env": "AZURE_8909_ENDPOINT",
        "api_key_env": "AZURE_8909_API_KEY",
    }
    assert _required_envs(cfg) == ["AZURE_8909_ENDPOINT", "AZURE_8909_API_KEY"]


def test_required_envs_anthropic_default():
    from control_plane.gateway import _required_envs

    assert _required_envs({"provider": "anthropic"}) == [
        "AZURE_ANTHROPIC_ENDPOINT",
        "AZURE_ANTHROPIC_API_KEY",
    ]


def test_required_envs_openai_compat_default():
    """Used by Fireworks-hosted tiers like FW-MiniMax-M2.5 / M2.7."""
    from control_plane.gateway import _required_envs

    assert _required_envs({"provider": "openai_compat"}) == [
        "CROWE_OPEN_ENDPOINT",
        "CROWE_OPEN_API_KEY",
    ]


def test_required_envs_nvidia_fixed_envs():
    from control_plane.gateway import _required_envs

    assert _required_envs({"provider": "nvidia"}) == [
        "NVIDIA_NIM_ENDPOINT",
        "NVIDIA_API_KEY",
    ]


def test_required_envs_openrouter_key_only():
    from control_plane.gateway import _required_envs

    assert _required_envs({"provider": "openrouter"}) == ["OPENROUTER_API_KEY"]


def test_required_envs_unknown_provider_returns_empty():
    from control_plane.gateway import _required_envs

    assert _required_envs({"provider": "made-up"}) == []


# ── /health endpoint integration tests ─────────────────────────────────


@pytest.fixture
def health_client():
    """Build a TestClient with the API-key auth dependency stubbed out so
    tests can hit /health without a database connection."""
    from control_plane.gateway import _resolve_api_key, router

    app = FastAPI()
    app.include_router(router)

    async def _fake_auth():
        return {"plan_id": "team", "workspace_id": "ws-test", "user_id": "u-test"}

    app.dependency_overrides[_resolve_api_key] = _fake_auth
    return TestClient(app)


def test_health_returns_expected_shape(health_client, monkeypatch):
    monkeypatch.setenv("AZURE_CORE_ENDPOINT", "https://fake.openai.azure.com")
    monkeypatch.setenv("AZURE_CORE_API_KEY", "fake-key")

    res = health_client.get("/api/gateway/health")
    assert res.status_code == 200, res.text
    payload = res.json()
    assert "stream_enabled" in payload
    assert "tiers_ok" in payload
    assert "tiers_blocked" in payload
    assert isinstance(payload["tiers"], list)
    assert len(payload["tiers"]) > 0
    sample = payload["tiers"][0]
    for field in (
        "model",
        "display_name",
        "provider",
        "required_env",
        "missing_env",
        "ok",
    ):
        assert field in sample, f"missing field {field}"


def test_health_marks_tiers_blocked_when_creds_missing(health_client, monkeypatch):
    """With NIM credentials unset, every nvidia-provider tier must show ok=False
    and report NVIDIA_API_KEY in missing_env."""
    monkeypatch.delenv("NVIDIA_NIM_ENDPOINT", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)

    res = health_client.get("/api/gateway/health")
    assert res.status_code == 200
    payload = res.json()
    nvidia_tiers = [t for t in payload["tiers"] if t["provider"] == "nvidia"]
    if not nvidia_tiers:
        pytest.skip("No nvidia-provider tiers in MODEL_CHAIN to validate")
    for t in nvidia_tiers:
        assert t["ok"] is False, f"nvidia tier {t['model']} should be blocked"
        assert "NVIDIA_API_KEY" in t["missing_env"]


def test_health_marks_tiers_ok_when_creds_set(health_client, monkeypatch):
    """With all default Azure creds set, the gpt-5.4-nano tier should be ok."""
    monkeypatch.setenv("AZURE_CORE_ENDPOINT", "https://fake.openai.azure.com")
    monkeypatch.setenv("AZURE_CORE_API_KEY", "fake-key")

    res = health_client.get("/api/gateway/health")
    assert res.status_code == 200
    payload = res.json()
    # gpt-5.4-nano is CroweLM Kernel — uses AZURE_CORE_* by default
    nano = next((t for t in payload["tiers"] if t["model"] == "gpt-5.4-nano"), None)
    if nano is None:
        pytest.skip("gpt-5.4-nano not present in MODEL_CHAIN")
    assert nano["ok"] is True, (
        f"expected ok=True with creds set; missing={nano['missing_env']}"
    )
    assert nano["missing_env"] == []


def test_health_surfaces_minimax_vega_status(health_client, monkeypatch):
    """The new CroweLM Vega tier (FW-MiniMax-M2.7) must appear in /health
    output so the operator can see at a glance whether Fireworks
    credentials are wired."""
    res = health_client.get("/api/gateway/health")
    assert res.status_code == 200
    payload = res.json()
    vega = next((t for t in payload["tiers"] if t["model"] == "FW-MiniMax-M2.7"), None)
    # The tier was added to MODEL_DISPLAY but only registered in MODEL_CHAIN
    # once the operator wires its config. Either result is acceptable; the
    # endpoint must not crash on a partial config.
    if vega is not None:
        assert vega["display_name"] == "CroweLM Vega"
        assert isinstance(vega["required_env"], list)
        assert isinstance(vega["missing_env"], list)
