"""Tests for CLI model-switch validation."""

from __future__ import annotations

import config.agent_config as agent_config
from cli import crowe_logic as cli_mod
from config.agent_config import resolve_model_config


def _synthetic_openai_compat_cfg() -> dict:
    """Build a synthetic openai_compat config so tests don't depend on
    whichever model currently happens to use CROWE_OPEN_ENDPOINT."""
    return {
        "name": "synthetic-hosted",
        "label": "CroweLM Synthetic",
        "provider": "openai_compat",
        "endpoint_env": "CROWE_OPEN_ENDPOINT",
        "api_key_env": "CROWE_OPEN_API_KEY",
        "backend_name": "synthetic/some-model",
    }


def test_model_switch_error_reports_missing_endpoint(monkeypatch):
    """A hosted model without its endpoint env set must report a clear error."""
    monkeypatch.delenv("CROWE_OPEN_ENDPOINT", raising=False)

    cfg = _synthetic_openai_compat_cfg()
    error = cli_mod._model_switch_error(cfg)

    assert error is not None
    assert "CROWE_OPEN_ENDPOINT" in error


def test_model_status_note_marks_blocked_models(monkeypatch):
    """When required env is missing the status note must read 'blocked'."""
    monkeypatch.delenv("CROWE_OPEN_ENDPOINT", raising=False)

    cfg = _synthetic_openai_compat_cfg()
    assert cli_mod._model_status_note(cfg) == "blocked"


def test_model_status_note_prefers_failures_over_blocked(monkeypatch):
    monkeypatch.delenv("CROWE_OPEN_ENDPOINT", raising=False)
    cfg = resolve_model_config("crowelm-glm")
    cli_mod._model_state["failures"][cfg["name"]] = 2

    try:
        assert cli_mod._model_status_note(cfg) == "2 fails"
    finally:
        cli_mod._model_state["failures"].pop(cfg["name"], None)


def test_provider_detail_filter_hides_legacy_backend_aliases():
    assert cli_mod._contains_provider_detail("CroweLM Cohere Command A") is True
    assert cli_mod._contains_provider_detail("gpt-5.4") is True
    assert cli_mod._contains_provider_detail("lattice") is False
    assert cli_mod._contains_provider_detail("CroweLM Dense") is False


def test_model_switch_error_allows_hosted_models_with_endpoint_only(monkeypatch):
    """A hosted openai_compat model is OK when only the endpoint is set."""
    monkeypatch.setenv("CROWE_OPEN_ENDPOINT", "https://models.crowe.logic/v1")
    monkeypatch.delenv("CROWE_OPEN_API_KEY", raising=False)

    # Use the synthetic cfg rather than resolve_model_config("titan").
    # The "titan" alias used to point at the openai_compat tier; after
    # the Helio/Azure rebrand merged via config/models.extra.json,
    # resolve_model_config("titan") returns the Azure entry instead.
    # The intent of this test is to exercise the openai_compat branch
    # of _model_switch_error, so the cfg shape is what matters, not
    # the registry's current alias resolution.
    cfg = _synthetic_openai_compat_cfg()
    assert cli_mod._model_switch_error(cfg) is None


def test_kernel_uses_standard_azure_openai_triplet_when_core_missing(monkeypatch):
    from config.agent_config import azure_openai_runtime_config

    monkeypatch.delenv("AZURE_CORE_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_CORE_API_KEY", raising=False)
    monkeypatch.setattr(agent_config, "AZURE_CORE_ENDPOINT", "")
    monkeypatch.setattr(agent_config, "AZURE_CORE_API_KEY", "")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://fallback.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fallback-key")
    monkeypatch.setenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-5.4-mini")

    cfg = resolve_model_config("kernel")
    runtime = azure_openai_runtime_config(cfg)

    assert cli_mod._model_switch_error(cfg) is None
    assert runtime["endpoint"] == "https://fallback.openai.azure.com"
    assert runtime["api_key"] == "fallback-key"
    assert runtime["model"] == "gpt-5.4-mini"


def test_provider_wide_error_detects_watsonx_quota_exhaustion():
    err = (
        'watsonx HTTP 403: {"errors":[{"code":"token_quota_reached",'
        '"message":"Request of 1 token(s) from quota was rejected"}]}'
    )

    assert cli_mod._is_provider_wide_error(err) is True
    assert cli_mod._is_failover_eligible_error(err) is True


def test_advance_model_can_skip_a_provider_family():
    original_index = cli_mod._model_state["chain_index"]
    try:
        start_index = next(
            idx
            for idx, cfg in enumerate(cli_mod.MODEL_CHAIN)
            if cfg.get("provider") != "auto"
        )
        start_provider = cli_mod.MODEL_CHAIN[start_index]["provider"]
        cli_mod._model_state["chain_index"] = start_index

        next_model = cli_mod._advance_model(skip_provider=start_provider)

        assert next_model is not None
        assert next_model["provider"] != start_provider
    finally:
        cli_mod._model_state["chain_index"] = original_index


def test_next_auto_model_after_failure_skips_same_provider_on_provider_wide_error():
    candidates = [
        {"label": "CroweLM Nexus", "provider": "watsonx"},
        {"label": "CroweLM Nano", "provider": "watsonx"},
        {"label": "CroweLM Swift", "provider": "nvidia"},
    ]

    next_index, next_model = cli_mod._next_auto_model_after_failure(
        candidates,
        0,
        candidates[0],
        'watsonx HTTP 403: {"errors":[{"code":"token_quota_reached"}]}',
    )

    assert next_index == 2
    assert next_model == candidates[2]


def test_next_auto_model_after_failure_uses_immediate_next_candidate_on_model_error():
    candidates = [
        {"label": "CroweLM Nexus", "provider": "watsonx"},
        {"label": "CroweLM Swift", "provider": "nvidia"},
        {"label": "CroweLM Lite", "provider": "nvidia"},
    ]

    next_index, next_model = cli_mod._next_auto_model_after_failure(
        candidates,
        0,
        candidates[0],
        "503 backend overloaded",
    )

    assert next_index == 1
    assert next_model == candidates[1]


def test_model_switch_error_uses_recent_provider_health_block(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_mod.Path, "home", lambda: tmp_path)

    cli_mod._set_provider_health(
        "watsonx",
        status="blocked",
        reason="watsonx HTTP 403: token_quota_reached",
        model_label="CroweLM Nexus",
    )

    cfg = {"label": "CroweLM Nexus", "name": "crowelm-nexus", "provider": "watsonx"}
    error = cli_mod._model_switch_error(cfg)

    assert error is not None
    assert "recent health check marked watsonx unavailable" in error
    assert "token_quota_reached" in error


def test_get_nvidia_provider_uses_backend_name(monkeypatch):
    captured = {}

    class _FakeProvider:
        def __init__(self, *, model, system_instructions, endpoint, api_key, label):
            captured["model"] = model
            captured["endpoint"] = endpoint
            captured["api_key"] = api_key
            captured["label"] = label
            self.model = model

    import providers.nvidia as nvidia_mod

    monkeypatch.setenv("NVIDIA_NIM_ENDPOINT", "https://nim.example.com")
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setattr(nvidia_mod, "NvidiaProvider", _FakeProvider)
    cli_mod._model_state["nvidia_provider"] = None

    cfg = {
        "name": "CroweLM Test",
        "label": "CroweLM Test",
        "provider": "nvidia",
        "backend_name": "nvidia/test-backend",
    }
    provider = cli_mod._get_nvidia_provider(cfg, system_instructions="system")

    assert provider.model == "nvidia/test-backend"
    assert captured["model"] == "nvidia/test-backend"
    assert captured["label"] == "CroweLM Test"
