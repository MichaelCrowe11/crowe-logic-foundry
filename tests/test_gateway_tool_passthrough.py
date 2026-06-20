"""Phase 1 — gateway client-executed tool-call passthrough (Approach B).

The hosted gateway is single-shot and TOOL-LESS at execution: it cannot run the
79-tool loop itself. Instead it passes tool *schemas* to the model and returns
the model's tool-call *requests* to the client, which executes them locally and
loops back (echoing tool_call ``id``s in round 2).

These tests pin the gateway-side contract:

  * a ``finish_reason == "tool_calls"`` response from the provider is surfaced as
    ``GatewayResponse.tool_calls`` (populated) + ``finish_reason == "tool_calls"``
    + ``content == ""`` (the model produced tool requests, not an answer).
  * absent ``tools`` -> unchanged single-shot behavior, ``finish_reason == "stop"``.
  * sending ``tools`` to a tool-incapable (reasoning-only) tier -> clean 400.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import config.agent_config as agent_config
import providers.hosted_openai as hosted_mod

pytest.importorskip("fastapi")

from fastapi import HTTPException

import control_plane.gateway as gateway_mod


def _install_fake_provider(monkeypatch, *, create, cfg=None):
    """Wire a fake openai_compat provider whose SDK ``create`` is ``create``."""
    cfg = cfg or {
        "name": "gpt-5.4",
        "label": "CroweLM Titan",
        "type": "fast",
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
            self.model = model
            self.client = SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=create))
            )

    monkeypatch.setattr(hosted_mod, "HostedOpenAIProvider", _FakeHostedProvider)
    return cfg


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    }
]


# ── _call_provider returns the tool-call request when finish_reason==tool_calls ──


def test_call_provider_passes_tools_to_create(monkeypatch):
    """When ``tools`` is provided it must be forwarded into the SDK create()."""
    captured = {}

    def _create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content="hi", tool_calls=None),
                )
            ],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
        )

    _install_fake_provider(monkeypatch, create=_create)
    result = asyncio.run(
        gateway_mod._call_provider(
            "gpt-5.4", [{"role": "user", "content": "hi"}], tools=TOOLS
        )
    )
    assert captured.get("tools") == TOOLS
    # Result carries content + finish_reason cleanly.
    assert result.content == "hi"
    assert result.finish_reason == "stop"
    assert result.tool_calls == []


def test_call_provider_no_tools_omits_kwarg(monkeypatch):
    """tools=None must NOT inject an empty ``tools`` kwarg (regression guard)."""
    captured = {}

    def _create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content="ok", tool_calls=None),
                )
            ],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )

    _install_fake_provider(monkeypatch, create=_create)
    result = asyncio.run(
        gateway_mod._call_provider("gpt-5.4", [{"role": "user", "content": "hi"}])
    )
    assert "tools" not in captured
    assert result.finish_reason == "stop"
    assert result.content == "ok"
    assert result.tool_calls == []


def test_call_provider_extracts_tool_calls(monkeypatch):
    """A ``finish_reason == 'tool_calls'`` response is extracted to a structured
    list of (id, name, arguments) requests; content is NOT read as the answer."""

    def _create(**kwargs):
        tc = SimpleNamespace(
            id="call_abc123",
            function=SimpleNamespace(
                name="read_file", arguments='{"path": "/tmp/x"}'
            ),
        )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="tool_calls",
                    # content may be a stray empty string or None when the model
                    # only emits tool calls; it must NOT be surfaced as the answer.
                    message=SimpleNamespace(content=None, tool_calls=[tc]),
                )
            ],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=4),
        )

    _install_fake_provider(monkeypatch, create=_create)
    result = asyncio.run(
        gateway_mod._call_provider(
            "gpt-5.4", [{"role": "user", "content": "read it"}], tools=TOOLS
        )
    )
    assert result.finish_reason == "tool_calls"
    assert result.content == ""
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call.id == "call_abc123"
    assert call.name == "read_file"
    assert call.arguments == '{"path": "/tmp/x"}'
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 4


# ── route-level: gateway_chat surfaces the tool-call contract ──


class _FakeDB:
    """Minimal Database stand-in. The tool-call/regression paths use a metered
    workspace principal but a generous budget so no row writes are required for
    the access/budget gates beyond what we stub here."""

    async def fetchrow(self, query, *args):
        # plan budget lookup -> unlimited
        if "FROM plans" in query:
            return {"token_budget_month": -1}
        return None

    async def execute(self, query, *args):
        return None


def _crowe_id_key_info(plan="pro"):
    """A Crowe ID principal: NOT metered, so no usage_events write is attempted."""
    return {
        "plan_id": plan,
        "workspace_id": "kc-sub-123",
        "user_id": "kc-sub-123",
        "principal": "crowe-id",
        "subject": "tester",
    }


def test_gateway_chat_surfaces_tool_calls(monkeypatch):
    def _create(**kwargs):
        tc = SimpleNamespace(
            id="call_xyz",
            function=SimpleNamespace(name="read_file", arguments='{"path":"a"}'),
        )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="tool_calls",
                    message=SimpleNamespace(content="", tool_calls=[tc]),
                )
            ],
            usage=SimpleNamespace(prompt_tokens=8, completion_tokens=2),
        )

    _install_fake_provider(monkeypatch, create=_create)
    req = gateway_mod.GatewayRequest(
        model="gpt-5.4", messages=[{"role": "user", "content": "go"}], tools=TOOLS
    )
    resp = asyncio.run(
        gateway_mod.gateway_chat(req, key_info=_crowe_id_key_info(), db=_FakeDB())
    )
    assert resp.finish_reason == "tool_calls"
    assert resp.content == ""
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].id == "call_xyz"
    assert resp.tool_calls[0].name == "read_file"
    assert resp.tool_calls[0].arguments == '{"path":"a"}'
    assert resp.usage["prompt_tokens"] == 8
    assert resp.usage["completion_tokens"] == 2


def test_gateway_chat_single_shot_unchanged(monkeypatch):
    """No tools in the request -> classic single-shot answer, finish_reason stop,
    empty tool_calls (backward-compatible default)."""

    def _create(**kwargs):
        assert "tools" not in kwargs
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content="the answer", tool_calls=None),
                )
            ],
            usage=SimpleNamespace(prompt_tokens=4, completion_tokens=3),
        )

    _install_fake_provider(monkeypatch, create=_create)
    req = gateway_mod.GatewayRequest(
        model="gpt-5.4", messages=[{"role": "user", "content": "q"}]
    )
    resp = asyncio.run(
        gateway_mod.gateway_chat(req, key_info=_crowe_id_key_info(), db=_FakeDB())
    )
    assert resp.content == "the answer"
    assert resp.finish_reason == "stop"
    assert resp.tool_calls == []
    assert resp.usage["total_tokens"] == 7


def test_gateway_chat_tools_on_reasoning_only_tier_400(monkeypatch):
    """Sending tools to a tier whose backend can't do function calling
    (DeepSeek-R1 reasoning-only) must be a clean 400, not a provider error."""
    cfg = {
        "name": "DeepSeek-R1",
        "label": "CroweLM Reason",
        "type": "reasoning",
        "provider": "azure_openai",
        "backend_name": "DeepSeek-R1-0528",
        "endpoint_env": "AZURE_CORE_ENDPOINT",
        "api_key_env": "AZURE_CORE_API_KEY",
    }

    def _create(**kwargs):  # pragma: no cover - must never be reached
        raise AssertionError("provider must not be called for a 400 guardrail")

    _install_fake_provider(monkeypatch, create=_create, cfg=cfg)
    # DeepSeek-R1 is in MODEL_PLAN_ACCESS at pro tier; use a pro principal so the
    # plan gate passes and we reach the capability guardrail.
    req = gateway_mod.GatewayRequest(
        model="DeepSeek-R1",
        messages=[{"role": "user", "content": "go"}],
        tools=TOOLS,
    )
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            gateway_mod.gateway_chat(req, key_info=_crowe_id_key_info("pro"), db=_FakeDB())
        )
    assert excinfo.value.status_code == 400
    assert "tool" in str(excinfo.value.detail).lower()


def test_reasoning_only_tier_without_tools_still_works(monkeypatch):
    """The capability guardrail only fires when tools are requested; a plain
    single-shot turn on a reasoning-only tier is unaffected."""
    cfg = {
        "name": "DeepSeek-R1",
        "label": "CroweLM Reason",
        "type": "reasoning",
        "provider": "azure_openai",
        "backend_name": "DeepSeek-R1-0528",
        "endpoint_env": "AZURE_CORE_ENDPOINT",
        "api_key_env": "AZURE_CORE_API_KEY",
    }
    # azure_openai branch needs endpoint+key present to reach create()
    monkeypatch.setenv("AZURE_CORE_ENDPOINT", "https://azure.test")
    monkeypatch.setenv("AZURE_CORE_API_KEY", "k")

    def _create(**kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content="reasoned answer", tool_calls=None),
                )
            ],
            usage=SimpleNamespace(prompt_tokens=2, completion_tokens=2),
        )

    monkeypatch.setattr(agent_config, "resolve_model_config", lambda _m: cfg)
    monkeypatch.setattr(agent_config, "MODEL_CHAIN", [cfg])

    import providers.azure_openai as az_mod

    class _FakeAzureProvider:
        def __init__(self, *, model, system_instructions, endpoint, api_key, label):
            self.model = model
            self.client = SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=_create))
            )

    monkeypatch.setattr(az_mod, "AzureOpenAIProvider", _FakeAzureProvider)

    req = gateway_mod.GatewayRequest(
        model="DeepSeek-R1", messages=[{"role": "user", "content": "go"}]
    )
    resp = asyncio.run(
        gateway_mod.gateway_chat(req, key_info=_crowe_id_key_info("pro"), db=_FakeDB())
    )
    assert resp.content == "reasoned answer"
    assert resp.finish_reason == "stop"
