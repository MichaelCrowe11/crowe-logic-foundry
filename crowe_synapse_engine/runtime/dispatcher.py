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
import json
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

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
    # Catalog lookup first: an exact-match entry in agent_config / models.extra
    # is authoritative because it knows the real backend deployment string.
    resolved = resolve_model(model)
    if resolved is not None:
        return resolved.provider
    # Fall back to glob patterns for models that aren't in the catalog yet.
    table = routes if routes is not None else DEFAULT_MODEL_ROUTES
    for pattern, provider in table:
        if fnmatch.fnmatch(model, pattern):
            return provider
    return DEFAULT_PROVIDER


# ──────────────────────────────────────────────────────────────────────────
# Model-alias resolver. Maps a logical CroweLM name (e.g. "crowelm-pro") to
# the real (provider, backend_name) pair the API client must use. Loads
# entries from config.agent_config._BASE_MODEL_CHAIN and config/models.extra.json
# once per process, then serves lookups from an in-memory index keyed by
# both canonical name and every alias.
# ──────────────────────────────────────────────────────────────────────────

# Maps the provider strings used in the config files to ModelProvider enum.
# "openai_compat" is a family label; the actual provider is inferred from the
# endpoint_env hint (Azure / NIM / hosted), with a safe default of HOSTED_OPENAI.
_CONFIG_PROVIDER_STRINGS: dict[str, ModelProvider] = {
    "azure_openai": ModelProvider.AZURE_OPENAI,
    "nvidia": ModelProvider.NVIDIA,
    "ollama": ModelProvider.OLLAMA,
    "openrouter": ModelProvider.OPENROUTER,
    "watsonx": ModelProvider.WATSONX,
    "anthropic": ModelProvider.ANTHROPIC,
    "hosted_openai": ModelProvider.HOSTED_OPENAI,
}


def _normalize_provider(
    config_provider: str | None, endpoint_env: str | None
) -> ModelProvider:
    """Map a config provider string + endpoint_env hint to a ModelProvider."""
    if config_provider in _CONFIG_PROVIDER_STRINGS:
        return _CONFIG_PROVIDER_STRINGS[config_provider]
    if config_provider == "openai_compat":
        hint = (endpoint_env or "").upper()
        if "AZURE" in hint:
            return ModelProvider.AZURE_OPENAI
        if "NVIDIA" in hint or "NIM" in hint:
            return ModelProvider.NVIDIA
        if "OLLAMA" in hint:
            return ModelProvider.OLLAMA
        if "OPENROUTER" in hint:
            return ModelProvider.OPENROUTER
        return ModelProvider.HOSTED_OPENAI
    return DEFAULT_PROVIDER


@dataclass(frozen=True)
class ResolvedModel:
    """The result of looking up a logical model name in the catalog."""

    canonical_name: str  # the entry's `name` field (e.g. "gpt-5.4-pro")
    backend_name: str  # what the API client sends (e.g. "meta-llama/llama-4...")
    provider: ModelProvider
    label: str = ""  # display name (e.g. "CroweLM Apex")
    endpoint_env: str | None = None
    api_key_env: str | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)


_MODEL_INDEX: dict[str, ResolvedModel] | None = None


def _repo_root() -> Path:
    """Find the repo root by walking up from this file."""
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        if (ancestor / "config").is_dir() and (ancestor / "agents").is_dir():
            return ancestor
    return Path.cwd()


def _load_model_entries() -> list[dict]:
    """Pull every model entry from agent_config + models.extra.json.

    Both sources use the same per-entry shape: name, label, provider,
    backend_name (optional, defaults to name), endpoint_env, api_key_env,
    aliases (optional). Anything missing is filled with sensible defaults.
    """
    entries: list[dict] = []
    try:
        from config.agent_config import _BASE_MODEL_CHAIN

        entries.extend(list(_BASE_MODEL_CHAIN))
    except Exception:
        # Config import may fail in slim packages; resolver degrades gracefully.
        pass

    extra_path = _repo_root() / "config" / "models.extra.json"
    if extra_path.is_file():
        try:
            data = json.loads(extra_path.read_text(encoding="utf-8"))
            entries.extend(list(data.get("models", [])))
        except (OSError, json.JSONDecodeError):
            pass
    return entries


def _build_index() -> dict[str, ResolvedModel]:
    """One-time index build. Keys include canonical name + every alias.

    Later entries with the same key overwrite earlier ones, so
    models.extra.json takes precedence over the base chain — useful for
    pinning a deployment without editing agent_config.py.
    """
    index: dict[str, ResolvedModel] = {}
    for entry in _load_model_entries():
        name = entry.get("name")
        if not name:
            continue
        resolved = ResolvedModel(
            canonical_name=name,
            backend_name=entry.get("backend_name") or name,
            provider=_normalize_provider(
                entry.get("provider"), entry.get("endpoint_env")
            ),
            label=entry.get("label", ""),
            endpoint_env=entry.get("endpoint_env"),
            api_key_env=entry.get("api_key_env"),
            aliases=tuple(entry.get("aliases") or []),
        )
        index[name] = resolved
        for alias in resolved.aliases:
            index[alias] = resolved
    return index


def resolve_model(name: str) -> ResolvedModel | None:
    """Look up a logical model name. Returns ``None`` if unknown."""
    global _MODEL_INDEX
    if _MODEL_INDEX is None:
        _MODEL_INDEX = _build_index()
    if os.environ.get("CROWE_SYNAPSE_DISABLE_MODEL_RESOLVER") == "1":
        return None
    return _MODEL_INDEX.get(name)


def reload_model_index() -> None:
    """Force the next ``resolve_model`` call to rebuild the index.

    Mainly for tests that monkeypatch the underlying config; also useful
    if a long-running process picks up a config edit at runtime.
    """
    global _MODEL_INDEX
    _MODEL_INDEX = None


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
