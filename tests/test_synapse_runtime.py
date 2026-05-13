"""Tests for the synapse runtime · dispatcher, tools, hooks, registry-extension.

These tests do not invoke any model. They cover the layer below the
network: routing rules, permission semantics, hook dispatch, and the
fact that the existing YAML agents still load after the schema extension.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from crowe_synapse_engine.agent_registry import AgentConfig, AgentRegistry
from crowe_synapse_engine.runtime import (
    HookRegistry,
    ModelProvider,
    ToolRegistry,
    select_runtime,
)
from crowe_synapse_engine.runtime.base import (
    ChunkKind,
    HookEvent,
    HookResult,
    ToolCall,
)
from crowe_synapse_engine.runtime.dispatcher import (
    select_provider,
)
from crowe_synapse_engine.runtime.tools import permission_allow, permission_deny

_REPO = Path(__file__).resolve().parents[1]


# ── Dispatcher: model → provider ────────────────────────────────────────


@pytest.mark.parametrize(
    "model,expected",
    [
        ("crowelm-pro", ModelProvider.AZURE_OPENAI),
        ("crowelm-talon", ModelProvider.AZURE_OPENAI),
        ("crowelm-aurora", ModelProvider.AZURE_OPENAI),
        ("crowelm-talon-nim", ModelProvider.NVIDIA),
        ("crowelm-pro-ollama", ModelProvider.OLLAMA),
        ("claude-opus-4-7", ModelProvider.ANTHROPIC),
        ("claude-sonnet-4-6", ModelProvider.ANTHROPIC),
        ("gpt-5.4-pro", ModelProvider.HOSTED_OPENAI),
        ("ollama/llama3", ModelProvider.OLLAMA),
        ("openrouter/some-model", ModelProvider.OPENROUTER),
        ("watsonx/mixtral", ModelProvider.WATSONX),
        ("unknown-model-name", ModelProvider.AZURE_OPENAI),  # default
    ],
)
def test_select_provider_routes(model, expected):
    assert select_provider(model) == expected


def test_runtime_hint_sdk_overrides_model_routing():
    assert select_provider("crowelm-pro", runtime_hint="sdk") == ModelProvider.SDK


def test_select_runtime_returns_synapse_for_crowelm():
    agent = AgentConfig(name="x", model="crowelm-pro")
    runtime = select_runtime(agent)
    assert type(runtime).__name__ == "SynapseRuntime"


def test_select_runtime_sdk_hint_returns_bridge():
    """runtime_hint='sdk' must return the SDK bridge regardless of install state."""
    agent = AgentConfig(name="x", model="claude-opus-4-7")
    runtime = select_runtime(agent, runtime_hint="sdk")
    assert type(runtime).__name__ == "SdkBridgeRuntime"


def test_synapse_runtime_rejects_anthropic_with_clear_message():
    """SynapseRuntime explicitly does not implement the native Anthropic path."""
    from crowe_synapse_engine.runtime.synapse import SynapseRuntime

    with pytest.raises(RuntimeError, match="runtime: sdk"):
        SynapseRuntime(provider=ModelProvider.ANTHROPIC)


# ── Tool registry: registration, resolution, permissions ────────────────


@pytest.fixture()
def filled_registry() -> ToolRegistry:
    reg = ToolRegistry()

    async def web_search(query: str) -> str:
        return f"results for {query}"

    async def grep_search(pattern: str, path: str = ".") -> str:
        return f"grep {pattern} {path}"

    async def talon_compose(intent: str) -> str:
        return f"composed {intent}"

    reg.register("web_search", "search the web", web_search)
    reg.register("grep_search", "search files", grep_search)
    reg.register("talon_compose", "talon compose", talon_compose)
    return reg


def test_tool_registry_resolves_globs(filled_registry):
    resolved = filled_registry.resolve(["talon_*", "web_search"])
    names = {t.name for t in resolved}
    assert names == {"talon_compose", "web_search"}


def test_tool_registry_sync_function_rejected():
    reg = ToolRegistry()

    def not_async(x: str) -> str:
        return x

    with pytest.raises(TypeError, match="must be an async coroutine"):
        reg.register("bad", "sync tool", not_async)  # type: ignore[arg-type]


def test_tool_registry_run_tool_with_permission_allow(filled_registry):
    async def allow_all(call: ToolCall):
        return permission_allow()

    call = ToolCall(id="t1", name="web_search", arguments={"query": "synapse"})
    result = asyncio.run(filled_registry.run_tool(call, permission_callback=allow_all))
    assert result.is_error is False
    assert "results for synapse" in result.content


def test_tool_registry_run_tool_with_permission_deny(filled_registry):
    async def deny_all(call: ToolCall):
        return permission_deny("blocked by policy")

    call = ToolCall(id="t2", name="web_search", arguments={"query": "x"})
    result = asyncio.run(filled_registry.run_tool(call, permission_callback=deny_all))
    assert result.is_error is True
    assert "Permission denied" in result.content
    assert "blocked by policy" in result.content


def test_tool_registry_unknown_tool_returns_error(filled_registry):
    call = ToolCall(id="t3", name="not_registered", arguments={})
    result = asyncio.run(filled_registry.run_tool(call))
    assert result.is_error is True
    assert "not registered" in result.content


# ── Hooks: dispatch order + blocking semantics ──────────────────────────


def test_hook_registry_dispatch_returns_block():
    reg = HookRegistry()

    async def blocker(event, payload):
        return HookResult(block=True, reason="vetoed")

    reg.register(HookEvent.PRE_TOOL_USE, blocker)
    result = asyncio.run(
        reg.dispatch(HookEvent.PRE_TOOL_USE, {"tool_name": "anything"})
    )
    assert result.block is True
    assert result.reason == "vetoed"


def test_hook_registry_matcher_filters_tool_name():
    reg = HookRegistry()
    seen: list[str] = []

    async def hook(event, payload):
        seen.append(payload["tool_name"])
        return HookResult(block=False)

    reg.register(HookEvent.PRE_TOOL_USE, hook, matcher="web_*")
    asyncio.run(reg.dispatch(HookEvent.PRE_TOOL_USE, {"tool_name": "web_search"}))
    asyncio.run(reg.dispatch(HookEvent.PRE_TOOL_USE, {"tool_name": "grep_search"}))
    assert seen == ["web_search"]  # grep_search did not match


# ── Schema extension back-compat: every existing YAML still loads ───────


def test_existing_yaml_agents_still_load_after_schema_extension():
    reg = AgentRegistry(agents_dir=str(_REPO / "agents"))
    agents = reg.list_agents()
    assert len(agents) >= 9, "expected at least the original 9 YAML agents to load"
    for agent in agents:
        # New optional fields must be present with safe defaults.
        assert hasattr(agent, "runtime")
        assert agent.permission_mode == "default"
        assert agent.mcp_servers == {}
        assert agent.subagents == []
        assert agent.hooks == []


def test_runtime_chunk_kinds_are_complete():
    """Brand Veil & wire-format sanity: kinds match documented vocabulary."""
    expected = {
        ChunkKind.TEXT,
        ChunkKind.REASONING,
        ChunkKind.TOOL_CALL,
        ChunkKind.TOOL_RESULT,
        ChunkKind.PERMISSION_DENIED,
        ChunkKind.HOOK_BLOCKED,
        ChunkKind.DONE,
        ChunkKind.ERROR,
    }
    assert set(ChunkKind) == expected
