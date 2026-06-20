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
        def __init__(self, *, model, system_instructions, endpoint, api_key, label, extra_headers=None):
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

    turn = asyncio.run(
        gateway_mod._call_provider(
            "gpt-5.4",
            [{"role": "user", "content": "hello"}],
        )
    )

    assert captured["init"]["model"] == "z-ai/glm5.1"
    assert captured["init"]["endpoint"] == "https://models.crowe.logic"
    assert captured["init"]["api_key"] == ""
    # _call_provider now returns a _ProviderTurn (Phase 1 contract change).
    assert turn.content == "OK"
    assert turn.prompt_tokens == 12
    assert turn.completion_tokens == 7
    assert turn.finish_reason == "stop"
    assert turn.tool_calls == []


def test_gateway_sends_model_persona_system_prompt(monkeypatch):
    """The gateway must compose the per-model persona (build_system_instructions),
    not a generic assistant prompt — otherwise free-tier turns leak the
    underlying foundation-model branding (e.g. "trained by Google")."""
    captured = {}

    cfg = {
        "name": "crowelm-mycelium",
        "label": "CroweLM Mycelium",
        "type": "fast",
        "provider": "openai_compat",
        "backend_name": "Mcrowe1210/gemma-4-mycelium-e4b",
        "endpoint_env": "CROWELM_MYCELIUM_ENDPOINT",
        "api_key_env": "CROWELM_MYCELIUM_API_KEY",
    }

    monkeypatch.setenv("CROWELM_MYCELIUM_ENDPOINT", "https://mycelium.test")
    monkeypatch.delenv("CROWELM_MYCELIUM_API_KEY", raising=False)
    monkeypatch.setattr(agent_config, "resolve_model_config", lambda _model: cfg)
    monkeypatch.setattr(agent_config, "MODEL_CHAIN", [cfg])

    class _FakeHostedProvider:
        def __init__(self, *, model, system_instructions, endpoint, api_key, label, extra_headers=None):
            captured["system_instructions"] = system_instructions
            self.model = model

            def _create(**kwargs):
                captured["create_kwargs"] = kwargs
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))],
                    usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
                )

            self.client = SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=_create))
            )

    monkeypatch.setattr(hosted_mod, "HostedOpenAIProvider", _FakeHostedProvider)

    asyncio.run(
        gateway_mod._call_provider(
            "crowelm-mycelium",
            [{"role": "user", "content": "who are you?"}],
        )
    )

    sys_msg = captured["create_kwargs"]["messages"][0]
    assert sys_msg["role"] == "system"
    # Persona present, generic placeholder gone.
    assert "CroweLM Mycelium" in sys_msg["content"]
    assert sys_msg["content"] != "You are a helpful assistant."
    # Provider construction gets the same composed instructions.
    assert "CroweLM Mycelium" in captured["system_instructions"]
    # Toolless turn: the agent tool catalog must NOT be present, or the model
    # emits <tool_code> calls for tools the gateway can't run and returns empty
    # content. (Regression: free-tier answers were blank for tool-seeking
    # prompts because the full ~15.5k agent prompt was sent.)
    assert "crowe_knowledge_base" not in sys_msg["content"]
    assert "## Core Tools" not in sys_msg["content"]
    assert "MCP Ecosystem" not in sys_msg["content"]


def test_build_system_instructions_omits_tools_when_disabled():
    """The lean gateway prompt keeps identity + brand policy but drops the agent
    tool catalog; the CLI default keeps it."""
    cfg = {
        "name": "crowelm-mycelium",
        "label": "CroweLM Mycelium",
        "type": "fast",
        "endpoint_env": "CROWELM_MYCELIUM_ENDPOINT",
    }
    full = agent_config.build_system_instructions(cfg)
    lean = agent_config.build_system_instructions(cfg, include_agent_tools=False)

    # CLI default carries the tool catalog; lean omits it and is much smaller.
    assert "## Core Tools" in full
    assert "## Core Tools" not in lean
    assert "crowe_knowledge_base" not in lean
    assert len(lean) < len(full)
    # Both keep brand identity.
    assert "CroweLM Mycelium" in lean
    assert "first-party Crowe Logic" in lean
