"""Optional Claude Agent SDK bridge.

Delegates the agent loop to Anthropic's ``claude-agent-sdk`` instead of the
in-process SynapseRuntime. Use this when you want Anthropic-native MCP
server processes (separate subprocesses for each MCP server) and the SDK's
built-in Read/Edit/Bash/Glob/Grep tools rather than the synapse in-process
tool registry.

This module is intentionally lightweight: the heavy dependency (the
``claude-agent-sdk`` package) is imported lazily so the rest of the runtime
stays importable on systems without it. Activation requires:

    pip install claude-agent-sdk

The bridge translates RuntimeChunk events out of the SDK's message types
so callers consume a uniform stream regardless of which runtime ran.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from crowe_synapse_engine.runtime.base import ChunkKind, RuntimeChunk


_SDK_INSTALL_HINT = (
    "claude-agent-sdk is not installed. Install with: pip install claude-agent-sdk\n"
    "Then set ANTHROPIC_API_KEY (or one of CLAUDE_CODE_USE_BEDROCK / "
    "CLAUDE_CODE_USE_VERTEX / CLAUDE_CODE_USE_FOUNDRY) and ensure the agent's "
    "model is a claude-* identifier."
)


def _import_sdk():
    """Lazy import. Raises with an actionable message if the SDK is missing."""
    try:
        import claude_agent_sdk  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(_SDK_INSTALL_HINT) from exc
    return claude_agent_sdk


class SdkBridgeRuntime:
    """Adapter that runs an agent via claude-agent-sdk and yields RuntimeChunks.

    Tool registration is *not* propagated to the SDK in this minimal bridge:
    the SDK ships its own built-in toolset (Read/Edit/Bash/Glob/Grep/etc.)
    plus MCP servers configured at the SDK level. The ``tools`` argument
    passed to ``run`` is interpreted as the SDK's ``allowed_tools`` list.

    A future expansion can translate this runtime's ToolRegistry into an
    ``sdk_mcp_server`` so in-process tools are exposed to the SDK too.
    """

    async def run(
        self,
        *,
        agent_name: str,
        user_prompt: str,
        system_prompt: str,
        model: str,
        tools: list[str],
        max_turns: int = 20,
        meta: dict[str, Any] | None = None,
    ) -> AsyncIterator[RuntimeChunk]:
        sdk = _import_sdk()
        query = sdk.query
        options_cls = sdk.ClaudeAgentOptions

        options = options_cls(
            system_prompt=system_prompt or None,
            allowed_tools=list(tools) if tools else [],
            model=model or None,
            max_turns=max_turns,
        )

        try:
            async for message in query(prompt=user_prompt, options=options):
                # The SDK yields typed messages: AssistantMessage, ResultMessage,
                # SystemMessage, etc. Translate to RuntimeChunks.
                msg_type = type(message).__name__
                content = getattr(message, "content", None)
                if msg_type == "AssistantMessage" and isinstance(content, list):
                    for block in content:
                        block_type = type(block).__name__
                        if block_type == "TextBlock":
                            yield RuntimeChunk(
                                kind=ChunkKind.TEXT,
                                text=getattr(block, "text", ""),
                            )
                        elif block_type == "ThinkingBlock":
                            yield RuntimeChunk(
                                kind=ChunkKind.REASONING,
                                text=getattr(block, "thinking", ""),
                            )
                        elif block_type == "ToolUseBlock":
                            yield RuntimeChunk(
                                kind=ChunkKind.TOOL_CALL,
                                tool_name=getattr(block, "name", ""),
                                tool_args=getattr(block, "input", {}) or {},
                                meta={"tool_call_id": getattr(block, "id", "")},
                            )
                        elif block_type == "ToolResultBlock":
                            yield RuntimeChunk(
                                kind=ChunkKind.TOOL_RESULT,
                                tool_name=getattr(block, "tool_name", "") or "",
                                tool_result=str(getattr(block, "content", "")),
                                meta={
                                    "tool_call_id": getattr(block, "tool_use_id", ""),
                                    "is_error": bool(getattr(block, "is_error", False)),
                                },
                            )
                elif msg_type == "ResultMessage":
                    yield RuntimeChunk(
                        kind=ChunkKind.DONE,
                        meta={
                            "rounds_used": getattr(message, "num_turns", 0),
                            "stop_reason": getattr(message, "subtype", "stop"),
                            "total_cost_usd": getattr(message, "total_cost_usd", None),
                        },
                    )
                    return
        except Exception as exc:
            yield RuntimeChunk(
                kind=ChunkKind.ERROR,
                text=f"{type(exc).__name__}: {exc}",
                meta={"exception_type": type(exc).__name__},
            )
