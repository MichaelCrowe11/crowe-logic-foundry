"""Model routing · model-name → backend provider.

The dispatcher is the only place the runtime maps a CroweLM-branded model
name (or a raw vendor name) to the concrete backend that serves it. Keeping
this lookup in one file is the Brand Veil seam: nothing else in the runtime
needs to know whether ``crowelm-pro`` is currently served by Azure OpenAI,
NIM, or watsonx.

Routes are matched by glob in declaration order; the first hit wins. The
default route at the end of the table catches anything unmatched and sends
it to the OpenAI-compatible client (which is the right call for the broad
ecosystem of OpenAI-compatible servers).

To extend: add a tuple to ``DEFAULT_MODEL_ROUTES`` before the catch-all.
"""

from __future__ import annotations

import fnmatch
from enum import Enum

from crowe_synapse_engine.agent_registry import AgentConfig
from crowe_synapse_engine.runtime.base import AgentRuntime


class ModelProvider(str, Enum):
    """Backend that ultimately serves the model.

    Names are internal. They MUST NOT appear in user-facing UI output;
    Brand Veil translates them through CroweLM variant labels at the
    surface layer.
    """

    AZURE_OPENAI = "azure_openai"  # CroweLM Pro/Core/Kernel on Azure AI Foundry
    AZURE_RESPONSES = "azure_responses"  # Azure Responses API for reasoning models
    ANTHROPIC = "anthropic"  # Claude models via the native Anthropic surface
    HOSTED_OPENAI = "hosted_openai"  # Self-hosted vLLM / SGLang / NIM-compatible
    NVIDIA = "nvidia"  # NVIDIA NIM (production CroweLM inference)
    OLLAMA = "ollama"  # Local or cloud Ollama
    OPENROUTER = "openrouter"  # OpenRouter aggregator
    WATSONX = "watsonx"  # IBM watsonx.ai
    SDK = "sdk"  # claude-agent-sdk (Anthropic-native MCP loop)


# ──────────────────────────────────────────────────────────────────────────
# Routing table. EDIT to align with your deployments.
#
# (pattern, provider) tuples evaluated in order; first match wins. Patterns
# are fnmatch globs against the agent's ``model`` field.
# ──────────────────────────────────────────────────────────────────────────
DEFAULT_MODEL_ROUTES: list[tuple[str, ModelProvider]] = [
    # CroweLM family — default tier is Azure OpenAI.
    ("crowelm-pro", ModelProvider.AZURE_OPENAI),
    ("crowelm-core", ModelProvider.AZURE_OPENAI),
    ("crowelm-kernel", ModelProvider.AZURE_OPENAI),
    ("crowelm-talon", ModelProvider.AZURE_OPENAI),
    ("crowelm-aurora", ModelProvider.AZURE_OPENAI),
    ("crowelm-*-nim", ModelProvider.NVIDIA),
    ("crowelm-*-ollama", ModelProvider.OLLAMA),
    # Claude family — default to the native Anthropic surface (your
    # providers/anthropic.py already wires this against Azure AI Foundry).
    # The Claude Agent SDK is opt-in via ``runtime: sdk`` in the YAML.
    ("claude-*", ModelProvider.ANTHROPIC),
    # OpenAI family on hosted OpenAI-compatible endpoints.
    ("gpt-*", ModelProvider.HOSTED_OPENAI),
    # Ollama prefix convention.
    ("ollama/*", ModelProvider.OLLAMA),
    # OpenRouter prefix convention.
    ("openrouter/*", ModelProvider.OPENROUTER),
    # watsonx prefix convention.
    ("watsonx/*", ModelProvider.WATSONX),
]

DEFAULT_PROVIDER: ModelProvider = ModelProvider.AZURE_OPENAI


def select_provider(
    model: str,
    *,
    runtime_hint: str | None = None,
    routes: list[tuple[str, ModelProvider]] | None = None,
) -> ModelProvider:
    """Resolve a model name to its backend provider.

    ``runtime_hint`` short-circuits the table when the YAML explicitly
    selects a runtime (e.g. ``runtime: sdk`` forces the Claude Agent SDK
    bridge regardless of model name).
    """
    if runtime_hint == "sdk":
        return ModelProvider.SDK
    table = routes if routes is not None else DEFAULT_MODEL_ROUTES
    for pattern, provider in table:
        if fnmatch.fnmatch(model, pattern):
            return provider
    return DEFAULT_PROVIDER


def select_runtime(
    agent: AgentConfig,
    *,
    runtime_hint: str | None = None,
) -> AgentRuntime:
    """Return a configured runtime ready to execute ``agent``.

    Default: ``SynapseRuntime`` — backend-agnostic loop that speaks
    OpenAI-compatible Chat Completions and native Anthropic Messages.

    Opt-in: ``SdkBridgeRuntime`` — delegates to ``claude-agent-sdk``.
    Only imported on demand so the extra dep stays optional.
    """
    provider = select_provider(agent.model, runtime_hint=runtime_hint)
    if provider == ModelProvider.SDK:
        from crowe_synapse_engine.runtime.sdk_bridge import SdkBridgeRuntime

        return SdkBridgeRuntime()
    # Lazy import to avoid circular reference (synapse imports from this file).
    from crowe_synapse_engine.runtime.synapse import SynapseRuntime

    return SynapseRuntime(provider=provider)
