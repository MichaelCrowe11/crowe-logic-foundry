"""Provider construction shared by non-terminal runtime surfaces."""

from __future__ import annotations

import os


class NoopOrchestrator:
    """Drop-in orchestrator for surfaces that only need provider streaming."""

    def record_execution(self, **_kwargs):
        return None


_SUPPORTED_PROVIDERS = frozenset({
    "openrouter",
    "ollama",
    "nvidia",
    "openai_compat",
    "azure_openai",
    "anthropic",
    "deepparallel",
})


def _is_provider_credentialed(cfg: dict) -> bool:
    kind = cfg.get("provider", "openrouter")
    if kind == "openrouter":
        return bool(os.environ.get("OPENROUTER_API_KEY", ""))
    if kind == "ollama":
        return True
    if kind == "nvidia":
        endpoint_var = cfg.get("endpoint_env", "NVIDIA_NIM_ENDPOINT")
        api_key_var = cfg.get("api_key_env", "NVIDIA_API_KEY")
        return bool(os.environ.get(endpoint_var, "")) and bool(os.environ.get(api_key_var, ""))
    if kind == "openai_compat":
        endpoint_var = cfg.get("endpoint_env", "CROWE_OPEN_ENDPOINT")
        return bool(os.environ.get(endpoint_var, ""))
    if kind == "azure_openai":
        from config.agent_config import azure_openai_runtime_config
        return not azure_openai_runtime_config(cfg)["missing"]
    if kind == "anthropic":
        endpoint_var = cfg.get("endpoint_env", "AZURE_ANTHROPIC_ENDPOINT")
        api_key_var = cfg.get("api_key_env", "AZURE_ANTHROPIC_API_KEY")
        return bool(os.environ.get(endpoint_var, "")) and bool(os.environ.get(api_key_var, ""))
    if kind == "deepparallel":
        # DeepParallel runs cluster-mode through crowe_deepparallel, which
        # itself dispatches to multiple Foundry deployments. Credentials are
        # checked per-cluster at run time by the adapter layer; here we only
        # confirm the package is importable and at least the primary Foundry
        # anchor (Kimi) has env vars set.
        try:
            import crowe_deepparallel  # noqa: F401
        except ImportError:
            return False
        return bool(os.environ.get("AZURE_KIMI_ENDPOINT", "")) and bool(
            os.environ.get("AZURE_KIMI_API_KEY", "")
        )
    return False


def _pick_auto_model(chain: list[dict]) -> dict:
    for cfg in chain:
        if cfg.get("provider", "openrouter") not in _SUPPORTED_PROVIDERS:
            continue
        if not _is_provider_credentialed(cfg):
            continue
        return cfg
    return chain[0]


def build_provider(model_id: str, *, session_id: str = ""):
    """Construct a configured provider for a headless or streaming surface."""
    from config.agent_config import (
        MODEL_CHAIN,
        OLLAMA_BASE_URL,
        OPENROUTER_API_KEY,
        OPENROUTER_BASE_URL,
        build_system_instructions,
        provider_model_name,
        resolve_model_config,
    )

    chain = list(MODEL_CHAIN)
    if not chain:
        raise RuntimeError("MODEL_CHAIN is empty in config/agent_config.py")

    if model_id == "auto":
        cfg = _pick_auto_model(chain)
    else:
        cfg = resolve_model_config(model_id)
        if cfg is None:
            raise RuntimeError(
                f"Unknown model '{model_id}'. Use 'auto' or one of: "
                + ", ".join(m["name"] for m in chain[:10])
                + ("..." if len(chain) > 10 else "")
            )
        if cfg.get("provider") == "auto":
            cfg = _pick_auto_model(chain)

    provider_kind = cfg.get("provider", "openrouter")
    label = cfg.get("label", "CroweLM")
    name = provider_model_name(cfg)
    system_instructions = build_system_instructions(cfg)
    if session_id:
        system_instructions += f"\n\n## Runtime Session\nSession id: {session_id}"

    if provider_kind == "openrouter":
        from providers.openrouter import OpenRouterProvider
        if not OPENROUTER_API_KEY:
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        return OpenRouterProvider(
            api_key=OPENROUTER_API_KEY,
            base_url=OPENROUTER_BASE_URL,
            model=name,
            system_instructions=system_instructions,
            label=label,
        )

    if provider_kind == "ollama":
        from providers.ollama import OllamaProvider
        return OllamaProvider(
            model=name,
            system_instructions=system_instructions,
            base_url=OLLAMA_BASE_URL,
            label=label,
        )

    if provider_kind == "nvidia":
        from providers.nvidia import NvidiaProvider
        endpoint_var = cfg.get("endpoint_env", "NVIDIA_NIM_ENDPOINT")
        api_key_var = cfg.get("api_key_env", "NVIDIA_API_KEY")
        endpoint = os.environ.get(endpoint_var, "")
        api_key = os.environ.get(api_key_var, "")
        if not endpoint or not api_key:
            raise RuntimeError(
                f"{endpoint_var} and {api_key_var} must both be set "
                "to use the nvidia provider"
            )
        return NvidiaProvider(
            model=name,
            system_instructions=system_instructions,
            endpoint=endpoint,
            api_key=api_key,
            label=label,
        )

    if provider_kind == "openai_compat":
        from providers.hosted_openai import HostedOpenAIProvider
        endpoint_var = cfg.get("endpoint_env", "CROWE_OPEN_ENDPOINT")
        api_key_var = cfg.get("api_key_env", "CROWE_OPEN_API_KEY")
        endpoint = os.environ.get(endpoint_var, "")
        api_key = os.environ.get(api_key_var, "")
        if not endpoint:
            raise RuntimeError(f"Hosted model '{label}' is missing an endpoint ({endpoint_var})")
        return HostedOpenAIProvider(
            model=name,
            system_instructions=system_instructions,
            endpoint=endpoint,
            api_key=api_key,
            label=label,
        )

    if provider_kind == "azure_openai":
        from providers.azure_openai import AzureOpenAIProvider, AzureResponsesProvider
        from config.agent_config import azure_openai_runtime_config
        runtime = azure_openai_runtime_config(cfg)
        if runtime["missing"]:
            raise RuntimeError(
                f"Azure model '{label}' is missing credentials "
                f"({' / '.join(runtime['missing'])})"
            )
        provider_cls = AzureResponsesProvider if cfg.get("surface") == "responses" else AzureOpenAIProvider
        return provider_cls(
            model=runtime["model"],
            system_instructions=system_instructions,
            endpoint=runtime["endpoint"],
            api_key=runtime["api_key"],
            label=label,
        )

    if provider_kind == "anthropic":
        from providers.anthropic import AnthropicProvider
        endpoint_var = cfg.get("endpoint_env", "AZURE_ANTHROPIC_ENDPOINT")
        api_key_var = cfg.get("api_key_env", "AZURE_ANTHROPIC_API_KEY")
        endpoint = os.environ.get(endpoint_var, "")
        api_key = os.environ.get(api_key_var, "")
        if not endpoint or not api_key:
            raise RuntimeError(
                f"Anthropic model '{label}' is missing credentials ({endpoint_var} / {api_key_var})"
            )
        return AnthropicProvider(
            model=name,
            system_instructions=system_instructions,
            endpoint=endpoint,
            api_key=api_key,
            label=label,
        )

    if provider_kind == "deepparallel":
        from providers.deepparallel import DeepParallelProvider
        # backend_name carries the cluster-preset name to load
        # (e.g. "crowelm-cluster-multilineage-v1"). Falls back to the
        # baseline single-anchor preset if not specified.
        preset = cfg.get("backend_name") or "crowelm-cluster-v1"
        judge_backend = cfg.get("judge_backend") or os.environ.get(
            "MULTIMODEL_JUDGE_BACKEND",
        )
        try:
            import crowe_deepparallel  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "DeepParallel tier requires the crowe-deepparallel package. "
                "Install with: pip install -e ~/Projects/crowe-logic-foundry-deepparallel-impl"
            ) from exc
        return DeepParallelProvider(
            preset=preset,
            system_instructions=system_instructions,
            label=label,
            judge_backend=judge_backend,
        )

    raise RuntimeError(f"Runtime surface does not support provider kind '{provider_kind}'")
