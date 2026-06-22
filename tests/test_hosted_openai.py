"""Tests for the self-hosted OpenAI-compatible provider."""

from __future__ import annotations

import providers.hosted_openai as hosted_mod


def test_hosted_openai_provider_normalizes_v1_and_supplies_placeholder_key(monkeypatch):
    captured = {}

    class _FakeOpenAI:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(hosted_mod, "OpenAI", _FakeOpenAI)

    hosted_mod.HostedOpenAIProvider(
        model="z-ai/glm5.1",
        system_instructions="system",
        endpoint="https://models.crowe.logic",
        api_key="",
        label="CroweLM Dense",
    )

    assert captured["base_url"] == "https://models.crowe.logic/v1"
    assert captured["api_key"] == "crowe-logic"


def test_hosted_openai_provider_preserves_existing_versioned_api_root(monkeypatch):
    captured = {}

    class _FakeOpenAI:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(hosted_mod, "OpenAI", _FakeOpenAI)

    hosted_mod.HostedOpenAIProvider(
        model="future/vendor",
        system_instructions="system",
        endpoint="https://models.example.com/api/paas/v4",
        api_key="key",
        label="CroweLM",
    )

    assert captured["base_url"] == "https://models.example.com/api/paas/v4"
