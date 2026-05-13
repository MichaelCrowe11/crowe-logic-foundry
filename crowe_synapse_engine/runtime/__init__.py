"""
Crowe-Synapse runtime · backend-agnostic agent loop.

The runtime layer takes a loaded AgentConfig and a user prompt, dispatches to
the right model backend based on the agent's ``model`` field, runs a streaming
tool-call loop with permission gating and lifecycle hooks, and yields chunks
that callers can render however they want (CLI, web stream, headless tests).

The default runtime is ``SynapseRuntime``. It speaks the OpenAI-compatible
Chat Completions surface (CroweLM Pro/Core/Kernel on Azure, NVIDIA NIM, Ollama,
OpenRouter, hosted OpenAI) and the native Anthropic Messages surface (Claude
on Azure AI Foundry). The optional ``SdkBridgeRuntime`` plugs in the
``claude-agent-sdk`` package when callers want Anthropic-native MCP server
processes and built-in Read/Edit/Bash tools instead of in-process tools.
"""

from crowe_synapse_engine.runtime.base import (
    AgentRuntime,
    HookEvent,
    HookResult,
    PermissionResult,
    RuntimeChunk,
    RuntimeError,
    ToolCall,
    ToolResult,
)
from crowe_synapse_engine.runtime.dispatcher import (
    ModelProvider,
    select_runtime,
)
from crowe_synapse_engine.runtime.hooks import HookRegistry
from crowe_synapse_engine.runtime.synapse import SynapseRuntime
from crowe_synapse_engine.runtime.tools import (
    Tool,
    ToolRegistry,
    permission_allow,
    permission_deny,
)

__all__ = [
    "AgentRuntime",
    "HookEvent",
    "HookRegistry",
    "HookResult",
    "ModelProvider",
    "PermissionResult",
    "RuntimeChunk",
    "RuntimeError",
    "SynapseRuntime",
    "Tool",
    "ToolCall",
    "ToolRegistry",
    "ToolResult",
    "permission_allow",
    "permission_deny",
    "select_runtime",
]
