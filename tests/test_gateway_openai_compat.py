"""Tests for gateway dispatch to the hosted OpenAI-compatible provider."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import config.agent_config as agent_config
import providers.hosted_openai as hosted_mod

pytest.importorskip("fastapi")

import control_plane.gateway as gateway_mod


def test_gateway_calls_hosted_openai_backend(monkeypatch):
    captured = {}
    cfg = {
        "name": "gpt-5.4",
        "label": "CroweLM Titan",
        "provider": "openai_compat",
        "backend_name": "z-ai/glm5.1",
        "endpoint_env": "CROWE_OPEN_ENDPOINT",
        "api_key_env": "CROWE_OPEN_API_KEY",
    }

    monkeypatch.setenv("CROWE_OPEN_ENDPOINT", "https://models.crowe.logic")
    monkeypatch.delenv("CROWE_OPEN_API_KEY", raising=False)
    monkeypatch.setattr(agent_config, "resolve_model_config", lambda _model: cfg)
    monkeypatch.setattr(agent_config, "MODEL_CHAIN", [cfg])

    class _FakeHostedProvider:
        def __init__(self, *, model, system_instructions, endpoint, api_key, label):
            captured["init"] = {
                "model": model,
                "endpoint": endpoint,
                "api_key": api_key,
                "label": label,
            }
            self.model = model
            self.client = SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(
                        create=lambda **kwargs: SimpleNamespace(
                            choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))],
                            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=7),
                        )
                    )
                )
            )

    monkeypatch.setattr(hosted_mod, "HostedOpenAIProvider", _FakeHostedProvider)

    content, prompt_tokens, completion_tokens = asyncio.run(
        gateway_mod._call_provider(
            "gpt-5.4",
            [{"role": "user", "content": "hello"}],
        )
    )

    assert captured["init"]["model"] == "z-ai/glm5.1"
    assert captured["init"]["endpoint"] == "https://models.crowe.logic"
    assert captured["init"]["api_key"] == ""
    assert content == "OK"
    assert prompt_tokens == 12
    assert completion_tokens == 7
