"""Tests for the dispatcher's model-alias resolver.

The resolver loads entries from config.agent_config._BASE_MODEL_CHAIN
and config/models.extra.json, indexes them by canonical name + every
alias, and maps the config's provider string onto the runtime's
ModelProvider enum.

These tests pin three properties that callers rely on:
1. Aliases resolve to the same ResolvedModel as the canonical name.
2. select_provider() consults the resolver before falling back to
   glob patterns, so a catalog entry beats a pattern.
3. The CROWE_SYNAPSE_DISABLE_MODEL_RESOLVER env var lets a caller
   force the old pattern-only behavior (useful for tests of the
   pattern path itself).
"""

from __future__ import annotations

import pytest

from crowe_synapse_engine.runtime.dispatcher import (
    ModelProvider,
    ResolvedModel,
    _normalize_provider,
    reload_model_index,
    resolve_model,
    select_provider,
)


def test_resolve_crowelm_pro_via_alias():
    """The label-style alias "crowelm-pro" must resolve to CroweLM Apex."""
    reload_model_index()
    resolved = resolve_model("crowelm-pro")
    assert resolved is not None
    assert resolved.canonical_name == "gpt-5.4-pro"
    assert resolved.label == "CroweLM Apex"
    # The actual backend differs from the canonical name (Apex is served by
    # watsonx in the current config). That mismatch is precisely what the
    # resolver is for.
    assert resolved.backend_name != resolved.canonical_name


def test_resolve_unknown_model_returns_none():
    reload_model_index()
    assert resolve_model("not-a-real-model-2026") is None


def test_resolve_canonical_name_matches_alias_lookup():
    reload_model_index()
    by_canonical = resolve_model("gpt-5.4-pro")
    by_alias = resolve_model("crowelm-pro")
    assert by_canonical is not None and by_alias is not None
    assert by_canonical.canonical_name == by_alias.canonical_name
    assert by_canonical.backend_name == by_alias.backend_name


def test_select_provider_consults_resolver_before_patterns():
    """Without the resolver, "crowelm-pro" would route to AZURE_OPENAI by
    the pattern table. With the resolver, it routes to WATSONX (the real
    backend per agent_config). The resolver must win."""
    reload_model_index()
    assert select_provider("crowelm-pro") == ModelProvider.WATSONX


def test_select_provider_falls_back_to_patterns_when_resolver_misses():
    """Pattern-only names (no catalog entry) still route via the table."""
    reload_model_index()
    # "ollama/llama3" isn't a catalog entry; the glob "ollama/*" handles it.
    assert select_provider("ollama/llama3") == ModelProvider.OLLAMA


def test_disable_env_falls_through_to_patterns(monkeypatch):
    """Setting CROWE_SYNAPSE_DISABLE_MODEL_RESOLVER=1 disables the catalog."""
    reload_model_index()
    monkeypatch.setenv("CROWE_SYNAPSE_DISABLE_MODEL_RESOLVER", "1")
    # Now "crowelm-pro" should route by glob (AZURE_OPENAI per pattern).
    assert select_provider("crowelm-pro") == ModelProvider.AZURE_OPENAI


@pytest.mark.parametrize(
    "config_provider,endpoint_env,expected",
    [
        ("azure_openai", None, ModelProvider.AZURE_OPENAI),
        ("nvidia", None, ModelProvider.NVIDIA),
        ("ollama", None, ModelProvider.OLLAMA),
        ("watsonx", None, ModelProvider.WATSONX),
        ("anthropic", None, ModelProvider.ANTHROPIC),
        ("openai_compat", "AZURE_OPENAI_ENDPOINT", ModelProvider.AZURE_OPENAI),
        ("openai_compat", "NVIDIA_NIM_ENDPOINT", ModelProvider.NVIDIA),
        ("openai_compat", "OLLAMA_BASE_URL", ModelProvider.OLLAMA),
        ("openai_compat", "OPENROUTER_BASE_URL", ModelProvider.OPENROUTER),
        ("openai_compat", "UNKNOWN_ENDPOINT", ModelProvider.HOSTED_OPENAI),
        ("openai_compat", None, ModelProvider.HOSTED_OPENAI),
        (None, None, ModelProvider.AZURE_OPENAI),  # DEFAULT_PROVIDER
    ],
)
def test_normalize_provider(config_provider, endpoint_env, expected):
    assert _normalize_provider(config_provider, endpoint_env) == expected


def test_resolved_model_frozen():
    """ResolvedModel is immutable so consumers can cache it safely."""
    resolved = ResolvedModel(
        canonical_name="x",
        backend_name="y",
        provider=ModelProvider.AZURE_OPENAI,
    )
    with pytest.raises(Exception):  # FrozenInstanceError
        resolved.backend_name = "z"  # type: ignore[misc]


# ── Endpoint env override (Task #20) ────────────────────────────────────


def test_resolve_client_honors_resolved_endpoint_env(monkeypatch):
    """When ResolvedModel.endpoint_env is set, _resolve_client must read THAT
    env var, not the per-provider default. This is what makes crowelm-kernel
    (endpoint_env=AZURE_CORE_ENDPOINT) work alongside crowelm-aurora
    (endpoint_env=AZURE_OPENAI_ENDPOINT) under one ModelProvider class.
    """
    from crowe_synapse_engine.runtime.synapse import _resolve_client

    # Clear both possible env vars so the test isn't contaminated by the user's
    # environment. Then set ONLY the catalog-specified one.
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AZURE_CORE_ENDPOINT", "https://core.example.com/openai")
    monkeypatch.setenv("AZURE_CORE_API_KEY", "secret-kernel")

    resolved = ResolvedModel(
        canonical_name="crowelm-kernel",
        backend_name="crowelm-kernel-v3",
        provider=ModelProvider.AZURE_OPENAI,
        endpoint_env="AZURE_CORE_ENDPOINT",
        api_key_env="AZURE_CORE_API_KEY",
    )
    client = _resolve_client(ModelProvider.AZURE_OPENAI, resolved=resolved)
    # The OpenAI client was constructed; base_url reflects the catalog endpoint.
    assert "core.example.com" in str(client.base_url)


def test_resolve_client_default_env_when_no_override(monkeypatch):
    """When ResolvedModel is None, the per-provider defaults still apply."""
    from crowe_synapse_engine.runtime.synapse import _resolve_client

    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://default.example.com/openai")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "secret-default")

    client = _resolve_client(ModelProvider.AZURE_OPENAI, resolved=None)
    assert "default.example.com" in str(client.base_url)


def test_resolve_client_missing_env_raises_with_catalog_name(monkeypatch):
    """The error message names the catalog-specified env var, not the default,
    so the user knows which env var to actually set."""
    from crowe_synapse_engine.runtime.synapse import _resolve_client

    monkeypatch.delenv("AZURE_CORE_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_CORE_API_KEY", raising=False)

    resolved = ResolvedModel(
        canonical_name="crowelm-kernel",
        backend_name="crowelm-kernel-v3",
        provider=ModelProvider.AZURE_OPENAI,
        endpoint_env="AZURE_CORE_ENDPOINT",
        api_key_env="AZURE_CORE_API_KEY",
    )
    with pytest.raises(RuntimeError, match="AZURE_CORE_ENDPOINT"):
        _resolve_client(ModelProvider.AZURE_OPENAI, resolved=resolved)
