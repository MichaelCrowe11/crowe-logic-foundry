#!/usr/bin/env python3
"""
Crowe Logic CLI — Universal AI Agent

Usage:
    crowe-logic                       # Interactive chat (default)
    crowe-logic chat                  # Interactive chat session
    crowe-logic run "your prompt"     # Single prompt, get response
    crowe-logic deploy                # Create/recreate the agent
    crowe-logic models sync           # Sync extra models from Azure CLI
    crowe-logic status                # Show agent status
    crowe-logic tools                 # List available tools
"""

import os
import sys
import json
import time
from pathlib import Path

_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PACKAGE_ROOT)

import click
from rich.console import Console
from rich.table import Table
from rich.text import Text
from rich.markup import escape as _rich_escape
from rich import box

from dotenv import load_dotenv

load_dotenv(os.path.join(_PACKAGE_ROOT, ".env"))

# Resolve the real project root (handles pipx installs where __file__ is in site-packages)
PROJECT_ROOT = os.environ.get("CROWE_LOGIC_PROJECT_ROOT", _PACKAGE_ROOT)

from cli.branding import (
    welcome_screen,
    show_welcome,
    show_inline_image,
    get_favicon,
    session_state,
    reset_session_state,
    render_tool_card,
    render_error as render_error_block,
    render_transcript_markdown,
    render_session_hud,
    render_recent_actions,
    record_action,
    show_last_transcript,
    show_retry_countdown,
    is_rate_limit_error,
    build_toolbar,
    SlashCompleter,
    create_chat_keybindings,
)
from cli.paste import paste_stash
from cli.session_runtime import (
    build_runtime_system_instructions,
    handle_local_control_command,
    load_session_runtime,
)
from config.agent_config import AGENT_VERSION, MODEL_CHAIN
from config.telemetry import telemetry
from iterm import iterm_set_var, install_iterm, uninstall_iterm, iterm_status

console = Console()
AGENT_ID_FILE = os.path.join(PROJECT_ROOT, ".agent_id")
_PROVIDER_HEALTH_TTL_SECONDS = 600

# Smart routing state — tracks current model position in the chain
_model_state = {
    "chain_index": 0,  # current position in MODEL_CHAIN
    "active_model": None,  # deployment name of the currently active model
    "failures": {},  # model_name -> consecutive failure count
    "agent_id": None,  # cached agent ID (may change on fallback)
    "openrouter_provider": None,  # OpenRouterProvider instance (reused for conversation)
}


def _current_model() -> dict:
    """Get the current model config from the chain."""
    idx = _model_state["chain_index"]
    if idx < len(MODEL_CHAIN):
        return MODEL_CHAIN[idx]
    return MODEL_CHAIN[0]  # wrap around to primary


def _provider_health_path() -> Path:
    return Path.home() / ".crowe-logic" / "runtime" / "provider_health.json"


def _load_provider_health() -> dict:
    path = _provider_health_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_provider_health(data: dict) -> None:
    path = _provider_health_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _set_provider_health(
    provider: str, *, status: str, reason: str = "", model_label: str = ""
) -> None:
    if not provider:
        return
    data = _load_provider_health()
    data[provider] = {
        "status": status,
        "reason": reason,
        "model_label": model_label,
        "updated_at": time.time(),
    }
    _save_provider_health(data)


def _clear_provider_health(provider: str) -> None:
    if not provider:
        return
    data = _load_provider_health()
    if provider in data:
        data.pop(provider, None)
        _save_provider_health(data)


def _provider_health_block(provider: str) -> str | None:
    if not provider or os.environ.get(
        "CROWE_LOGIC_IGNORE_PROVIDER_HEALTH", ""
    ).strip() in ("1", "true", "yes"):
        return None
    data = _load_provider_health()
    entry = data.get(provider)
    if not isinstance(entry, dict):
        return None
    updated_at = float(entry.get("updated_at", 0) or 0)
    if updated_at <= 0 or (time.time() - updated_at) > _PROVIDER_HEALTH_TTL_SECONDS:
        return None
    status = str(entry.get("status", "") or "").strip().lower()
    if status not in {"blocked", "down", "auth failed", "quota", "offline"}:
        return None
    reason = str(entry.get("reason", "") or "").strip()
    return reason or f"Recent health check marked provider '{provider}' unavailable."


def _advance_model(*, skip_provider: str | None = None) -> dict | None:
    """Move to the next fallback model, optionally skipping one provider family."""
    _model_state["chain_index"] += 1
    while _model_state["chain_index"] < len(MODEL_CHAIN):
        candidate = _current_model()
        if skip_provider and candidate.get("provider") == skip_provider:
            _model_state["chain_index"] += 1
            continue
        return candidate
    _model_state["chain_index"] = 0  # reset to primary for next attempt
    return None


def _advance_auto_candidate(
    candidates: list[dict],
    current_index: int,
    *,
    skip_provider: str | None = None,
) -> tuple[int, dict] | tuple[None, None]:
    """Advance through auto-route candidates, optionally skipping one provider."""
    next_index = current_index + 1
    while next_index < len(candidates):
        candidate = candidates[next_index]
        if skip_provider and candidate.get("provider") == skip_provider:
            next_index += 1
            continue
        return next_index, candidate
    return None, None


def _reset_model_chain():
    """Reset to the primary model (e.g., at session start)."""
    _model_state["chain_index"] = 0
    _model_state["failures"] = {}
    _model_state["nvidia_provider"] = None
    _model_state["openrouter_provider"] = None
    _model_state["ollama_provider"] = None
    _model_state["azure_openai_provider"] = None
    _model_state["hosted_openai_provider"] = None
    _model_state["anthropic_provider"] = None
    _model_state["watsonx_provider"] = None


# ─── Synapse Router: per-turn auto-routing ──────────────────────────


def _auto_route_enabled() -> bool:
    """Return True when per-turn routing is active. Default-on; set
    CROWE_LOGIC_AUTO_ROUTE=0 (or false/no/off) to pin the active model."""
    val = os.environ.get("CROWE_LOGIC_AUTO_ROUTE", "").strip().lower()
    if val in ("0", "false", "no", "off"):
        return False
    return True


def _personal_lane_enabled() -> bool:
    """True when the Mike-only local lane is opted in via CROWE_LOGIC_PERSONAL_LANE."""
    val = os.environ.get("CROWE_LOGIC_PERSONAL_LANE", "").strip().lower()
    return val in ("1", "true", "yes", "on")


def _auto_route_available(model_cfg: dict) -> bool:
    """Return True when a routed model is usable in the current environment.

    Local (Ollama) tiers are a Mike-only personal lane: excluded from customer
    auto-routing unless CROWE_LOGIC_PERSONAL_LANE is set.
    """
    if model_cfg.get("provider") == "ollama" and not _personal_lane_enabled():
        return False
    from config.health import registry

    if not registry.is_available(str(model_cfg.get("name", ""))):
        return False
    return _model_switch_error(model_cfg) is None


def _hedge_banner(*, target_label: str, reason: str) -> str:
    """Calm, intentional routing-hedge line (a reroute, not an error)."""
    reason_txt = {
        "timeout": "slow start",
        "error": "provider error",
        "unavailable": "tier unavailable",
    }.get(reason, reason)
    return (
        f"  [dim #bfa669]Routing onward → {target_label} ({reason_txt})[/dim #bfa669]"
    )


def _apply_route_decision(decision, session_state: dict, prompt: str = "") -> bool:
    """Switch the active chain index to the routed tier when it differs.

    Returns True when a tier swap actually occurred. Logs every decision
    to telemetry — including no-op routes — so operators can audit the
    classifier's behavior against real traffic. The prompt is truncated
    to 200 characters before logging; that's enough to re-classify it
    later (replay harness) without bloating the telemetry stream.
    """
    from config.agent_config import resolve_model_config
    from config.telemetry import telemetry

    payload = decision.to_dict()
    if prompt:
        payload["prompt_preview"] = prompt[:200]
        payload["prompt_length"] = len(prompt)
    telemetry.log_event("synapse_route", payload)

    routed_cfg = resolve_model_config(decision.selected_label) or resolve_model_config(
        decision.selected_name
    )
    if routed_cfg is None:
        return False

    # Find this cfg's position in MODEL_CHAIN.
    target_idx = next(
        (i for i, m in enumerate(MODEL_CHAIN) if m is routed_cfg),
        None,
    )
    if target_idx is None:
        return False

    if target_idx == _model_state["chain_index"]:
        return False  # already on the right tier

    _model_state["chain_index"] = target_idx
    # Bust cached providers so the next call rebuilds with the routed cfg.
    for key in (
        "nvidia_provider",
        "openrouter_provider",
        "ollama_provider",
        "azure_openai_provider",
        "hosted_openai_provider",
        "anthropic_provider",
    ):
        _model_state[key] = None
    session_state["active_model"] = routed_cfg["label"]
    return True


def _render_route_badge(console, decision) -> None:
    """Print a one-line badge for a Synapse route decision."""
    color = "#bfa669" if not decision.low_confidence else "#d97706"
    flag = " [LOW-CONF]" if decision.low_confidence else ""
    console.print(
        f"  [dim {color}]→ Synapse: {decision.intent} → "
        f"{decision.selected_label} (conf={decision.confidence:.2f}){flag}[/dim {color}]"
    )


def _sync_session_runtime(state: dict) -> None:
    """Refresh in-memory session state from the persisted runtime store."""
    session_id = state.get("session_id", "")
    if not session_id:
        return
    runtime = load_session_runtime(session_id)
    state["steering_instruction"] = runtime.get("steering_instruction", "")
    state["dataset_selection"] = runtime.get("dataset_selection", "all")
    state["last_answer_text"] = runtime.get("last_answer_text", "")
    state["last_reasoning_text"] = runtime.get("last_reasoning_text", "")
    if not state.get("active_model") and runtime.get("last_model"):
        state["active_model"] = runtime.get("last_model", "")


def _runtime_system_instructions(model_cfg: dict, state: dict) -> str:
    """Compose the per-session system prompt for the active model."""
    return build_runtime_system_instructions(
        model_cfg,
        session_id=state.get("session_id", ""),
    )


def _apply_provider_instructions(
    provider, system_instructions: str, model_cfg: dict | None = None
):
    """Refresh per-turn provider state: system instructions and the
    MODEL_CHAIN entry that drives tier_runtime_params (temperature,
    max_tokens, etc.). Used on both cache-hit and fresh-construction
    paths so tier params attach reliably either way.
    """
    if hasattr(provider, "set_system_instructions"):
        provider.set_system_instructions(system_instructions)
    if model_cfg is not None and hasattr(provider, "model_cfg"):
        provider.model_cfg = model_cfg
    return provider


def _get_openrouter_provider(
    model_cfg: dict, *, system_instructions: str | None = None
):
    """Get or create an OpenRouterProvider for the given model."""
    from config.agent_config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL
    from providers.openrouter import OpenRouterProvider

    model_name = model_cfg["name"]
    label = model_cfg["label"]
    system_instructions = system_instructions or build_runtime_system_instructions(
        model_cfg
    )
    current = _model_state.get("openrouter_provider")
    if current and current.model == model_name:
        return _apply_provider_instructions(current, system_instructions, model_cfg)
    if not OPENROUTER_API_KEY:
        raise RuntimeError(
            f"OpenRouter model '{label}' is missing credentials — "
            "set OPENROUTER_API_KEY in .env"
        )

    provider = OpenRouterProvider(
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        model=model_name,
        system_instructions=system_instructions,
        label=label,
    )
    _model_state["openrouter_provider"] = provider
    return _apply_provider_instructions(provider, system_instructions, model_cfg)


def _get_ollama_provider(model_cfg: dict, *, system_instructions: str | None = None):
    """Get or create an OllamaProvider for the given model."""
    from config.agent_config import OLLAMA_BASE_URL, provider_model_name
    from providers.ollama import OllamaProvider

    model_name = provider_model_name(model_cfg)
    label = model_cfg["label"]
    system_instructions = system_instructions or build_runtime_system_instructions(
        model_cfg
    )
    current = _model_state.get("ollama_provider")
    if current and current.model == model_name:
        return _apply_provider_instructions(current, system_instructions, model_cfg)

    provider = OllamaProvider(
        model=model_name,
        system_instructions=system_instructions,
        base_url=OLLAMA_BASE_URL,
        label=label,
    )
    _model_state["ollama_provider"] = provider
    return _apply_provider_instructions(provider, system_instructions, model_cfg)


def _get_nvidia_provider(model_cfg: dict, *, system_instructions: str | None = None):
    """Get or create a NvidiaProvider for the given model."""
    from config.agent_config import provider_model_name
    from providers.nvidia import NvidiaProvider

    model_name = provider_model_name(model_cfg)
    label = model_cfg["label"]
    endpoint_var = model_cfg.get("endpoint_env", "NVIDIA_NIM_ENDPOINT")
    api_key_var = model_cfg.get("api_key_env", "NVIDIA_API_KEY")
    endpoint = os.environ.get(endpoint_var, "")
    api_key = os.environ.get(api_key_var, "")
    system_instructions = system_instructions or build_runtime_system_instructions(
        model_cfg
    )
    current = _model_state.get("nvidia_provider")
    if current and current.model == model_name:
        return _apply_provider_instructions(current, system_instructions, model_cfg)
    if not endpoint or not api_key:
        raise RuntimeError(
            f"NVIDIA model '{label}' is missing credentials — "
            f"set {endpoint_var} and {api_key_var} in .env"
        )

    provider = NvidiaProvider(
        model=model_name,
        system_instructions=system_instructions,
        endpoint=endpoint,
        api_key=api_key,
        label=label,
    )
    _model_state["nvidia_provider"] = provider
    return _apply_provider_instructions(provider, system_instructions, model_cfg)


def _get_watsonx_provider(model_cfg: dict, *, system_instructions: str | None = None):
    """Get or create a WatsonxProvider for the given model."""
    from config.agent_config import provider_model_name
    from providers.watsonx import WatsonxProvider

    # The brand_id (model name) is the canonical identifier for the watsonx
    # adapter; backend_name (upstream model id) is also accepted by resolve().
    model_name = provider_model_name(model_cfg)
    label = model_cfg["label"]
    system_instructions = system_instructions or build_runtime_system_instructions(
        model_cfg
    )
    current = _model_state.get("watsonx_provider")
    if current and current.model == model_name:
        return _apply_provider_instructions(current, system_instructions, model_cfg)

    from pathlib import Path as _P

    if not (_P.home() / ".crowe-logic" / "ibm.env").exists():
        raise RuntimeError(
            f"watsonx model '{label}' is missing credentials — "
            "create ~/.crowe-logic/ibm.env via the IBM Cloud setup wizard."
        )

    provider = WatsonxProvider(
        model=model_name,
        system_instructions=system_instructions,
        label=label,
    )
    _model_state["watsonx_provider"] = provider
    return provider


def _get_hosted_openai_provider(
    model_cfg: dict, *, system_instructions: str | None = None
):
    """Get or create a self-hosted OpenAI-compatible provider for the given model."""
    from config.agent_config import provider_model_name
    from providers.hosted_openai import HostedOpenAIProvider

    model_name = provider_model_name(model_cfg)
    label = model_cfg["label"]
    system_instructions = system_instructions or build_runtime_system_instructions(
        model_cfg
    )
    endpoint_var = model_cfg.get("endpoint_env", "CROWE_OPEN_ENDPOINT")
    api_key_var = model_cfg.get("api_key_env", "CROWE_OPEN_API_KEY")

    endpoint = os.environ.get(endpoint_var, "")
    api_key = os.environ.get(api_key_var, "")

    if not endpoint:
        raise RuntimeError(
            f"Hosted model '{label}' is missing an endpoint — "
            f"set {endpoint_var} in .env"
        )

    current = _model_state.get("hosted_openai_provider")
    if current and current.model == model_name and current.endpoint == endpoint:
        return _apply_provider_instructions(current, system_instructions, model_cfg)

    provider = HostedOpenAIProvider(
        model=model_name,
        system_instructions=system_instructions,
        endpoint=endpoint,
        api_key=api_key,
        label=label,
    )
    _model_state["hosted_openai_provider"] = provider
    return _apply_provider_instructions(provider, system_instructions, model_cfg)


def _get_azure_openai_provider(
    model_cfg: dict, *, system_instructions: str | None = None
):
    """
    Get or create an AzureOpenAIProvider for the given model config.

    Unlike the other providers (which share one endpoint for all models), each
    Azure model carries its own endpoint_env / api_key_env in the MODEL_CHAIN
    entry — so multiple Azure Foundry resources can coexist in the same chain.
    The provider is cached by (model, endpoint) since both determine identity.
    """
    from config.agent_config import azure_openai_runtime_config
    from providers.azure_openai import AzureOpenAIProvider, AzureResponsesProvider

    runtime = azure_openai_runtime_config(model_cfg)
    model_name = runtime["model"]
    label = model_cfg["label"]
    system_instructions = system_instructions or build_runtime_system_instructions(
        model_cfg
    )
    endpoint = runtime["endpoint"]
    api_key = runtime["api_key"]

    if runtime["missing"]:
        raise RuntimeError(
            f"Azure model '{label}' is missing credentials — "
            f"set {' and '.join(runtime['missing'])} in .env"
        )

    current = _model_state.get("azure_openai_provider")
    if current and current.model == model_name and current.endpoint == endpoint:
        return _apply_provider_instructions(current, system_instructions, model_cfg)

    provider_cls = (
        AzureResponsesProvider
        if model_cfg.get("surface") == "responses"
        else AzureOpenAIProvider
    )
    provider = provider_cls(
        model=model_name,
        system_instructions=system_instructions,
        endpoint=endpoint,
        api_key=api_key,
        label=label,
    )
    _model_state["azure_openai_provider"] = provider
    return _apply_provider_instructions(provider, system_instructions, model_cfg)


def _get_anthropic_provider(model_cfg: dict, *, system_instructions: str | None = None):
    """
    Get or create an AnthropicProvider for the given model config.

    Uses Azure AI Foundry's native Anthropic API surface at /anthropic.
    Caches by (model, endpoint) like the Azure OpenAI provider.
    """
    from providers.anthropic import AnthropicProvider

    from config.agent_config import provider_model_name

    model_name = provider_model_name(model_cfg)
    label = model_cfg["label"]
    system_instructions = system_instructions or build_runtime_system_instructions(
        model_cfg
    )
    endpoint_var = model_cfg.get("endpoint_env", "AZURE_ANTHROPIC_ENDPOINT")
    api_key_var = model_cfg.get("api_key_env", "AZURE_ANTHROPIC_API_KEY")

    endpoint = os.environ.get(endpoint_var, "")
    api_key = os.environ.get(api_key_var, "")

    if not endpoint or not api_key:
        raise RuntimeError(
            f"Anthropic model '{label}' is missing credentials — "
            f"set {endpoint_var} and {api_key_var} in .env"
        )

    current = _model_state.get("anthropic_provider")
    if current and current.model == model_name and current.endpoint == endpoint:
        return _apply_provider_instructions(current, system_instructions, model_cfg)

    provider = AnthropicProvider(
        model=model_name,
        system_instructions=system_instructions,
        endpoint=endpoint,
        api_key=api_key,
        label=label,
    )
    _model_state["anthropic_provider"] = provider
    return _apply_provider_instructions(provider, system_instructions, model_cfg)


def _get_provider_for_dual(model_cfg: dict, system_instructions: str):
    """Dispatch to the correct ``_get_*_provider`` helper for dual-mode use.

    The normal chat loop has an if/elif ladder that picks a helper based
    on ``model_cfg["provider"]``; this is the same dispatch, collapsed
    into a callable so the dual orchestrator can stay provider-agnostic.
    Raises on unknown or legacy providers (``azure`` legacy Agents path
    is not supported in dual mode because its agent/thread model is single-
    turn).
    """
    provider_kind = model_cfg.get("provider")
    if provider_kind == "anthropic":
        return _get_anthropic_provider(
            model_cfg, system_instructions=system_instructions
        )
    if provider_kind == "azure_openai":
        return _get_azure_openai_provider(
            model_cfg, system_instructions=system_instructions
        )
    if provider_kind == "nvidia":
        return _get_nvidia_provider(model_cfg, system_instructions=system_instructions)
    if provider_kind == "watsonx":
        return _get_watsonx_provider(model_cfg, system_instructions=system_instructions)
    if provider_kind == "openai_compat":
        return _get_hosted_openai_provider(
            model_cfg, system_instructions=system_instructions
        )
    if provider_kind == "openrouter":
        return _get_openrouter_provider(
            model_cfg, system_instructions=system_instructions
        )
    if provider_kind == "ollama":
        return _get_ollama_provider(model_cfg, system_instructions=system_instructions)
    raise RuntimeError(
        f"Dual mode does not support provider '{provider_kind}' "
        f"(model {model_cfg.get('label')!r}). Pick a model served by "
        "anthropic, openai_compat, nvidia, azure_openai, watsonx, openrouter, or ollama."
    )


def _handle_replay_or_fork(
    user_input: str,
    turn_history,
    dual_state,
    console,
) -> str | None:
    """Parse ``/replay`` or ``/fork`` and return the user_input to reissue.

    Grammar:
        /replay N              Reissue turn N on the current model.
        /replay N <alias>      Reissue turn N, switching to <alias> first.
        /fork N                Reissue turn N, truncate turns N+1..end.
        /fork N <alias>        Fork and switch model.

    Returns None on parse error or bad index (caller continues loop).
    Returns the past user input string on success, caller proceeds
    with dispatch as if the user typed it fresh.
    """
    parts = user_input.strip().split()
    if len(parts) < 2:
        console.print("  [red]usage: /replay <N> [alias]  or  /fork <N> [alias][/red]")
        return None

    cmd = parts[0].lower()
    is_fork = cmd == "/fork"
    try:
        index = int(parts[1])
    except ValueError:
        console.print(f"  [red]turn index must be a number, got: {parts[1]!r}[/red]")
        return None

    record = turn_history.get(index)
    if record is None:
        console.print(
            f"  [red]no turn #{index} in history (have {len(turn_history)} turns)[/red]"
        )
        return None

    # Optional model switch. /replay 3 eclipse means "rerun turn 3 on Eclipse".
    if len(parts) >= 3:
        alias = parts[2]
        _switch_model({"agent_id": None, "client": None, "thread": None}, alias)

    # Fork semantics: drop subsequent turns so the replay overwrites the
    # tail. Useful for "try that differently" without the stale branch
    # cluttering the history.
    if is_fork:
        dropped = turn_history.truncate_after(index - 1)
        console.print(
            f"  [dim #bfa669]fork: truncated {len(dropped)} subsequent turn(s)[/dim #bfa669]"
        )

    verb = "forking" if is_fork else "replaying"
    preview = record.user_input[:80] + ("..." if len(record.user_input) > 80 else "")
    console.print(f"  [#6fbf73]{verb} turn #{index}: {preview}[/#6fbf73]")
    return record.user_input


def _show_account_status() -> None:
    """Print the workspace's credit balance, tier, and reset date."""
    try:
        from cli.foundry_api import get_client
    except Exception:
        console.print(
            "  [red]Account lookup unavailable: foundry_api import failed[/red]"
        )
        return

    status = get_client().account_status()
    console.print()

    if status.byok:
        console.print(f"  [bold #bfa669]BYOK mode[/bold #bfa669]")
        console.print(f"  [dim]{status.message}[/dim]")
        console.print()
        return

    if not status.authenticated:
        console.print("  [bold #bf6f6f]Not authenticated[/bold #bf6f6f]")
        console.print(f"  [dim]{status.message}[/dim]")
        console.print("  [dim]Set CROWE_LOGIC_API_KEY to enable credit tracking.[/dim]")
        console.print()
        return

    balance_str = f"{status.balance:,}" if status.balance >= 0 else "unknown"
    allocation_str = f"{status.allocation:,}" if status.allocation else "0"
    tier_str = status.tier or "unknown"
    active_str = "active" if status.active else "paused"

    console.print(
        f"  [bold #bfa669]Account[/bold #bfa669]  ·  tier: {tier_str}  ·  {active_str}"
    )
    console.print(f"  [dim]balance:[/dim]    {balance_str} / {allocation_str} credits")
    if status.reset_at:
        console.print(f"  [dim]resets:[/dim]     {status.reset_at}")
    if status.workspace_id:
        console.print(f"  [dim]workspace:[/dim]  {status.workspace_id}")
    if status.message and status.message != "ok":
        console.print(f"  [dim]note:[/dim]       {status.message}")
    console.print()


def _estimate_credits_for_turn(model_cfg: dict, dual_state=None) -> tuple[int, str]:
    """Compute the credit cost of an upcoming turn for pre-flight reservation.

    Returns (credits, model_label_for_audit). Uses the real estimator
    in cli.cost_model so the number matches what the HUD reports.
    """
    try:
        from cli.cost_model import estimate_turn_credits
    except Exception:
        return (1, model_cfg.get("label", "?"))

    peer = (
        dual_state.right_cfg
        if (dual_state and getattr(dual_state, "active", False))
        else None
    )
    synth = bool(dual_state and getattr(dual_state, "synth_active", False))
    primary = (
        dual_state.left_cfg
        if (dual_state and getattr(dual_state, "active", False))
        else model_cfg
    )
    cc = estimate_turn_credits(primary, dual_mode_peer_cfg=peer, synthesis=synth)
    label = primary.get("label", "?")
    if peer:
        label = f"{primary.get('label', '?')} + {peer.get('label', '?')}"
        if synth:
            label += " + synth"
    return (cc.credits, label)


def _preflight_credits(model_cfg: dict, dual_state=None) -> bool:
    """Reserve credits for the upcoming turn. Returns True when the
    turn may proceed, False when the control plane denied it.

    On denial, prints a clean user-facing message and returns False.
    On network failure or disabled client, returns True (fail-open).
    """
    try:
        from cli.foundry_api import get_client
    except Exception:
        return True

    client = get_client()
    if not client.enabled and not client.byok_mode:
        # No API key set. Do not gate the user.
        return True

    credits, label = _estimate_credits_for_turn(model_cfg, dual_state=dual_state)
    decision = client.check_and_reserve(credits, model_label=label)
    if decision.allowed:
        return True

    console.print()
    console.print(f"  [bold #bf6f6f]Credits unavailable[/bold #bf6f6f]")
    console.print(f"  [dim]{decision.reason}[/dim]")
    console.print("  [dim]Check balance with /account or upgrade your plan.[/dim]")
    console.print()
    return False


def _record_turn_telemetry(model_cfg: dict, session_state: dict) -> None:
    """Record one completed turn's tokens, USD cost, and credit use.

    Reads any token counts the provider populated on ``session_state``
    during streaming (output tokens are always present; Anthropic also
    reports real input_tokens, cache_read, and cache_write counters),
    computes upstream cost via :mod:`cli.cost_model`, and pushes a
    TurnCost onto the session tracker so the HUD can render running
    totals on the next frame. Silently skips if cost-model import
    fails so telemetry accounting never crashes a turn.
    """
    try:
        from cli.cost_model import (
            SessionCostTracker,
            estimate_turn_cost,
            estimate_turn_credits,
        )
    except Exception:
        return

    tracker = session_state.get("cost_tracker")
    if tracker is None:
        tracker = SessionCostTracker()
        session_state["cost_tracker"] = tracker

    output_tokens = int(session_state.get("last_tokens", 0) or 0)
    # Providers that populate real usage counters set these in session_state;
    # others leave them at 0 and we fall back to an input-free estimate
    # that still surfaces the output-side cost honestly.
    input_tokens = int(session_state.get("last_input_tokens", 0) or 0)
    cached_input = int(session_state.get("last_cached_input_tokens", 0) or 0)
    cache_write = int(session_state.get("last_cache_write_tokens", 0) or 0)

    cost = estimate_turn_cost(
        model_cfg.get("provider", "unknown"),
        model_cfg.get("backend_name") or model_cfg.get("name", ""),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input,
        cache_write_tokens=cache_write,
    )
    credits = estimate_turn_credits(model_cfg)
    tracker.record_turn(model_cfg=model_cfg, cost=cost, credits=credits)

    # Clear the per-turn reservoirs so the next turn starts clean.
    session_state["last_input_tokens"] = 0
    session_state["last_cached_input_tokens"] = 0
    session_state["last_cache_write_tokens"] = 0


def _record_dual_turn_telemetry(dual_state, session_state: dict) -> None:
    """Record both dual-mode panes plus the optional synthesis turn.

    Reads per-pane transcripts from ``session_state["last_dual_transcripts"]``
    (populated by ``run_dual_turn``) and attributes tokens and cost to the
    correct model config. Synthesis adds one more flagship-cost entry.
    """
    try:
        from cli.cost_model import (
            SessionCostTracker,
            estimate_turn_cost,
            estimate_turn_credits,
        )
    except Exception:
        return

    tracker = session_state.get("cost_tracker")
    if tracker is None:
        tracker = SessionCostTracker()
        session_state["cost_tracker"] = tracker

    transcripts = session_state.get("last_dual_transcripts") or {}
    for pane_id, pane_cfg in (
        ("left", dual_state.left_cfg),
        ("right", dual_state.right_cfg),
    ):
        if pane_cfg is None:
            continue
        pane_data = transcripts.get(pane_id, {})
        tokens_out = int(pane_data.get("tokens", 0) or 0)
        cost = estimate_turn_cost(
            pane_cfg.get("provider", "unknown"),
            pane_cfg.get("backend_name") or pane_cfg.get("name", ""),
            input_tokens=0,
            output_tokens=tokens_out,
        )
        credits = estimate_turn_credits(pane_cfg)
        tracker.record_turn(
            model_cfg=pane_cfg,
            cost=cost,
            credits=credits,
            dual_pair=True,
        )

    if dual_state.synth_active and dual_state.synth_cfg is not None:
        synth_tokens = len(session_state.get("last_synth_text", ""))
        # Rough token estimate: 4 chars per token on English text.
        synth_out = max(synth_tokens // 4, 0)
        cost = estimate_turn_cost(
            dual_state.synth_cfg.get("provider", "unknown"),
            dual_state.synth_cfg.get("backend_name")
            or dual_state.synth_cfg.get("name", ""),
            input_tokens=0,
            output_tokens=synth_out,
        )
        credits = estimate_turn_credits(dual_state.synth_cfg)
        tracker.record_turn(model_cfg=dual_state.synth_cfg, cost=cost, credits=credits)


def _is_model_error(error_str: str) -> bool:
    """Detect errors that indicate the model itself is failing (not user error)."""
    indicators = [
        "server_error",
        "Sorry, something went wrong",
        "InternalServerError",
        "502",
        "503",
        "504",
        "model_error",
        "overloaded",
        "capacity",
        "The server had an error",
        "run failed",
        "response.completed",
    ]
    lower = error_str.lower()
    return any(ind.lower() in lower for ind in indicators)


def _is_provider_wide_error(error_str: str) -> bool:
    """Detect provider-level failures that should skip the same backend family."""
    lower = error_str.lower()
    indicators = (
        "token_quota_reached",
        "quota_reached",
        "quota exceeded",
        "quota was rejected",
        "insufficient credits",
        "credit balance",
        "subscription not registered",
        "entitlement_enforcement",
        "401",
        "403",
        "unauthorized",
        "authentication",
        "invalid api key",
        "missing credentials",
        "connection refused",
        "temporary failure in name resolution",
        "name or service not known",
        "nodename nor servname",
    )
    return any(ind in lower for ind in indicators)


def _is_failover_eligible_error(error_str: str) -> bool:
    """Detect provider failures that should trigger model fallback instead of aborting."""
    return (
        is_rate_limit_error(error_str)
        or _is_model_error(error_str)
        or _is_provider_wide_error(error_str)
    )


def _next_auto_model_after_failure(
    candidates: list[dict],
    current_index: int,
    failed_model: dict,
    error_str: str | None,
) -> tuple[int, dict] | tuple[None, None]:
    """Return the next same-turn auto fallback model after a failure."""
    skip_provider = None
    if error_str and _is_provider_wide_error(error_str):
        skip_provider = failed_model.get("provider")
    return _advance_auto_candidate(
        candidates,
        current_index,
        skip_provider=skip_provider,
    )


def _deploy_with_model(client, model_name: str) -> str:
    """Create a new agent with the specified model and return the agent ID."""
    from azure.ai.agents.models import CodeInterpreterTool, FunctionTool, ToolSet
    from config.agent_config import SYSTEM_INSTRUCTIONS, AGENT_NAME
    from tools import user_functions

    toolset = ToolSet()
    toolset.add(FunctionTool(user_functions))
    toolset.add(CodeInterpreterTool())
    client.enable_auto_function_calls(toolset)

    agent = client.create_agent(
        model=model_name,
        name=AGENT_NAME,
        instructions=SYSTEM_INSTRUCTIONS,
        toolset=toolset,
    )

    # Persist agent ID
    with open(AGENT_ID_FILE, "w") as f:
        json.dump(
            {
                "agent_id": agent.id,
                "name": AGENT_NAME,
                "version": AGENT_VERSION,
                "model": model_name,
            },
            f,
            indent=2,
        )

    _model_state["agent_id"] = agent.id
    _model_state["active_model"] = model_name
    return agent.id


def get_agent_id() -> str | None:
    """
    Load the Azure Agents agent_id from disk. Returns None if no .agent_id file
    exists or the file is corrupt — callers can decide whether that's fatal.

    The Azure Agents SDK path is now a legacy fallback; the primary providers
    (azure_openai, nvidia, openrouter, ollama) don't need an agent at all.
    """
    if _model_state["agent_id"]:
        return _model_state["agent_id"]
    if not os.path.exists(AGENT_ID_FILE):
        return None
    try:
        with open(AGENT_ID_FILE) as f:
            data = json.load(f)
            _model_state["agent_id"] = data["agent_id"]
            _model_state["active_model"] = data.get("model", "unknown")
            return data["agent_id"]
    except (json.JSONDecodeError, KeyError):
        return None


def get_client():
    """Build an Azure AI Agents client. Raises if the Azure identity isn't set up."""
    from azure.ai.agents import AgentsClient
    from azure.identity import DefaultAzureCredential
    from config.agent_config import PROJECT_ENDPOINT

    return AgentsClient(endpoint=PROJECT_ENDPOINT, credential=DefaultAzureCredential())


def _ensure_azure_agents(azure_state: dict):
    """
    Lazy-initialize the Azure AI Agents client, thread, and agent_id.

    Called on-demand only when a MODEL_CHAIN entry with provider="azure"
    (the legacy Agents SDK path) is actually selected. Raises RuntimeError
    if the agent or credentials are missing, so the chat loop can fall
    through to the next model in the chain.
    """
    if azure_state["client"] is not None and azure_state["agent_id"] is not None:
        return azure_state

    agent_id = get_agent_id()
    if not agent_id:
        raise RuntimeError(
            "Azure Agents path requires an agent — run `crowe-logic deploy` "
            "to create one, or switch to a non-Azure-Agents model in the chain."
        )

    client = get_client()
    thread = azure_state.get("thread") or client.threads.create()

    azure_state["agent_id"] = agent_id
    azure_state["client"] = client
    azure_state["thread"] = thread
    return azure_state


def _extract_tool_info(step_details) -> list[dict]:
    """Pull tool names and arguments from a RunStep's step_details."""
    tools = []
    tool_calls = getattr(step_details, "tool_calls", None)
    if not tool_calls:
        return tools
    for tc in tool_calls:
        if tc.type == "function" and hasattr(tc, "function"):
            tools.append(
                {
                    "name": tc.function.name,
                    "args": getattr(tc.function, "arguments", ""),
                }
            )
        elif tc.type == "code_interpreter":
            tools.append({"name": "code_interpreter", "args": ""})
        else:
            tools.append({"name": tc.type, "args": ""})
    return tools


def _render_tool_card_old(tool_info: dict):
    """Legacy tool card — replaced by hybrid cards from branding module."""
    render_tool_card(
        console, tool_info["name"], tool_info.get("args", ""), status="running"
    )


def _render_error(msg: str, title: str = "Error"):
    """Print a styled error panel."""
    console.print()
    render_error_block(console, title, msg)


def _handle_local_runtime_command(command_text: str, state: dict) -> bool:
    """Execute a local slash command without calling a model provider."""
    response = handle_local_control_command(
        command_text,
        session_id=state.get("session_id", ""),
    )
    if response is None:
        return False

    _sync_session_runtime(state)
    if command_text.strip().lower().startswith("/transcript"):
        show_last_transcript(console, state)
        console.print()
        return True
    render_transcript_markdown(console, response, title="answer", meta="local")
    console.print()
    return True


def _cancel_active_runs(client, thread_id: str):
    """Cancel any active (queued/in_progress/requires_action) runs on the thread."""
    try:
        runs = client.runs.list(thread_id=thread_id)
        run_list = getattr(runs, "data", None) or list(runs)
        for run in run_list:
            if run.status in ("queued", "in_progress", "requires_action"):
                try:
                    client.runs.cancel(thread_id=thread_id, run_id=run.id)
                except Exception:
                    pass  # best-effort cancellation
    except Exception:
        pass  # thread may have no runs yet


_tool_map_cache = None


def _get_tool_map() -> dict:
    """Cached name -> function lookup from registered user_functions."""
    global _tool_map_cache
    if _tool_map_cache is None:
        from tools import user_functions

        _tool_map_cache = {f.__name__: f for f in user_functions}
    return _tool_map_cache


_orchestrator = None


def _get_orchestrator():
    """Lazy-loaded Crowe-Synapse orchestrator."""
    global _orchestrator
    if _orchestrator is None:
        from crowe_synapse_engine import Orchestrator

        _orchestrator = Orchestrator(
            db_path=os.path.expanduser("~/.crowe-logic/memory.db"),
            agents_dir=os.path.join(PROJECT_ROOT, "agents"),
            templates_dir=os.path.join(
                PROJECT_ROOT, "crowe_synapse_engine", "templates"
            ),
        )
    return _orchestrator


def _execute_tool_call(tool_map: dict, name: str, arguments_json: str) -> str:
    """Execute a single tool function by name and return the result as a string.

    Uses the resilient parser in ``cli.tool_args`` so that tool calls whose
    ``content`` field contains raw newlines / tabs / Rich markup don't hard-fail
    with ``JSONDecodeError``. Recovery is best-effort; truly malformed calls
    still surface the exception as a structured error.
    """
    from cli.tool_args import parse_tool_arguments

    func = tool_map.get(name)
    if not func:
        return json.dumps({"error": f"Unknown tool: {name}"})
    try:
        # First: resilient parse so unescaped newlines / Rich markup / raw
        # tabs in string values don't hard-fail with JSONDecodeError, and
        # so ``content_b64`` gets unwrapped back into ``content``.
        args, _recovered = parse_tool_arguments(arguments_json)
        # Then: coerce string-typed values to their declared int/float/bool
        # so smaller local models that serialize everything as strings
        # (e.g. limit="10" instead of limit=10) don't raise TypeError.
        from providers._shared import _coerce_tool_args

        args = _coerce_tool_args(func, args)
        result = func(**args)
        return str(result) if result is not None else ""
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def stream_response(client, thread_id: str, agent_id: str):
    from azure.ai.agents.models import (
        MessageDeltaChunk,
        ThreadRun,
        RunStep,
        AgentStreamEvent,
        ToolOutput,
    )
    from cli.renderer import StreamRenderer

    tool_map = _get_tool_map()
    renderer = StreamRenderer(
        console,
        session_state.get("active_model") or "crowe-logic",
        favicon=session_state.get("favicon", ""),
    )
    full_text = ""
    tool_calls_shown = set()
    stream_ok = True

    run_id = None
    pending_tool_calls = None

    def _capture_segment():
        nonlocal full_text
        segment = renderer.current_segment_text
        if segment.strip():
            full_text += segment
        return segment

    def _end_segment():
        _capture_segment()
        renderer.end_segment()

    try:
        renderer.start()

        with client.runs.stream(thread_id=thread_id, agent_id=agent_id) as stream:
            for event_type, event_data, _ in stream:
                if isinstance(event_data, MessageDeltaChunk):
                    if event_data.text:
                        renderer.feed(event_data.text)

                elif isinstance(event_data, ThreadRun):
                    run_id = event_data.id
                    if event_data.status == "requires_action":
                        _end_segment()
                        renderer.stop_spinner()
                        pending_tool_calls = (
                            event_data.required_action.submit_tool_outputs.tool_calls
                        )
                    elif event_data.status == "failed":
                        _end_segment()
                        renderer.stop_spinner()
                        stream_ok = False
                        err_str = str(event_data.last_error)
                        if is_rate_limit_error(err_str):
                            session_state["api_status"] = "throttled"
                            iterm_set_var("crowe_logic_api", "throttled")
                        if _is_model_error(err_str):
                            raise RuntimeError(f"model_error: {err_str}")
                        _render_error(err_str, "Run Failed")
                    elif event_data.status in ("cancelled", "expired"):
                        _end_segment()
                        renderer.stop_spinner()
                        stream_ok = False
                        _render_error(f"Run {event_data.status}.", "Run Stopped")

                elif isinstance(event_data, RunStep):
                    step_id = getattr(event_data, "id", None)
                    if (
                        event_data.type == "tool_calls"
                        and event_data.status == "in_progress"
                    ):
                        if step_id not in tool_calls_shown:
                            tool_calls_shown.add(step_id)
                            tools = _extract_tool_info(event_data.step_details)
                            _end_segment()
                            names = [t["name"] for t in tools] if tools else ["tools"]
                            renderer.set_spinner(f"running {', '.join(names)}...")
                    elif event_data.status == "completed":
                        renderer.stop_spinner()

                elif event_type == AgentStreamEvent.ERROR:
                    _end_segment()
                    renderer.stop_spinner()
                    stream_ok = False
                    _render_error(str(event_data))

                elif event_type == AgentStreamEvent.DONE:
                    break
    finally:
        renderer.stop_spinner()

    if not pending_tool_calls:
        final_segment = _capture_segment()
        if stream_ok and final_segment.strip():
            renderer.finish(session_state=session_state)
        else:
            renderer.end_segment()
        console.print()
        return full_text

    def _poll_run(rid):
        r = client.runs.get(thread_id=thread_id, run_id=rid)
        while r.status in ("queued", "in_progress"):
            time.sleep(0.5)
            r = client.runs.get(thread_id=thread_id, run_id=rid)
        return r

    tool_phase_ok = True
    while pending_tool_calls and run_id:
        renderer.set_spinner("preparing actions...")
        try:
            run = _poll_run(run_id)
        except Exception as e:
            renderer.stop_spinner()
            _render_error(str(e), "Run Status Error")
            tool_phase_ok = False
            break
        renderer.stop_spinner()

        if run.status == "completed":
            break
        if run.status != "requires_action":
            _render_error(
                str(getattr(run, "last_error", run.status)), f"Run {run.status.title()}"
            )
            tool_phase_ok = False
            break

        pending_tool_calls = run.required_action.submit_tool_outputs.tool_calls
        tool_outputs = []
        for tc in pending_tool_calls:
            if tc.type == "function":
                tool_name = tc.function.name or "invalid_tool_call"
                renderer.set_spinner(f"running {tool_name}...")
                _tool_start = time.monotonic()
                result = _execute_tool_call(tool_map, tool_name, tc.function.arguments)
                duration_ms = int((time.monotonic() - _tool_start) * 1000)
                renderer.stop_spinner()

                failed = result.startswith('{"error"')
                render_tool_card(
                    console,
                    tool_name,
                    tc.function.arguments,
                    status="fail" if failed else "ok",
                    result=result,
                    duration_ms=duration_ms,
                )
                session_state["tool_count"] += 1
                record_action(
                    session_state,
                    name=tool_name,
                    status="fail" if failed else "ok",
                    result=result,
                    duration_ms=duration_ms,
                    args=tc.function.arguments,
                )
                iterm_set_var("crowe_logic_tools", str(session_state["tool_count"]))

                _get_orchestrator().record_execution(
                    tool_name=tool_name,
                    arguments=tc.function.arguments,
                    output=result[:10000],
                    duration_ms=duration_ms,
                )
                tool_outputs.append(ToolOutput(tool_call_id=tc.id, output=result))

        renderer.set_spinner("thinking...")
        try:
            client.runs.submit_tool_outputs(
                thread_id=thread_id, run_id=run_id, tool_outputs=tool_outputs
            )
            run = _poll_run(run_id)
        except Exception as e:
            renderer.stop_spinner()
            _render_error(str(e), "Tool Submit Failed")
            tool_phase_ok = False
            break
        renderer.stop_spinner()

        if run.status == "requires_action":
            pending_tool_calls = run.required_action.submit_tool_outputs.tool_calls
            continue
        elif run.status == "completed":
            break
        else:
            _render_error(
                str(getattr(run, "last_error", run.status)), f"Run {run.status.title()}"
            )
            tool_phase_ok = False
            break

    if run_id and tool_phase_ok:
        try:
            messages = client.messages.list(thread_id=thread_id)
            msg_list = getattr(messages, "data", None) or list(messages)
            for msg in msg_list:
                if msg.role == "assistant":
                    parts = []
                    for item in msg.content:
                        if hasattr(item, "text"):
                            val = getattr(item.text, "value", None) or str(item.text)
                            if val.strip():
                                parts.append(val.strip())
                    if parts:
                        final_text = "\n\n".join(parts)
                        full_text = (
                            f"{full_text}\n\n{final_text}".strip()
                            if full_text.strip()
                            else final_text
                        )
                        render_transcript_markdown(
                            console, final_text, title="answer", meta="final"
                        )
                    break
        except Exception:
            pass

    console.print()
    return full_text


@click.group(invoke_without_command=True)
@click.version_option(version=AGENT_VERSION, prog_name="crowe-logic")
@click.pass_context
def main(ctx):
    """Crowe Logic — Universal AI Agent with smart model routing"""
    if ctx.invoked_subcommand is None:
        ctx.invoke(chat)


@main.command()
def chat():
    """Start an interactive chat session with the agent."""
    import uuid
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.formatted_text import HTML

    # Lazy Azure Agents state — only populated if a chat turn hits the legacy
    # Azure Agents path. Primary providers (azure_openai, nvidia, openrouter,
    # ollama) don't need an agent/thread/client at all.
    azure_state = {"agent_id": None, "client": None, "thread": None}
    synthetic_thread_id = f"local-{uuid.uuid4().hex[:16]}"

    # Dual-mode state, toggled by /dual on|off. When active, each turn fans
    # out to two models in parallel with a side-by-side pane renderer.
    from cli.dual_mode import DualModeState, handle_dual_command, run_dual_turn
    from cli.history import ensure_history

    dual_state = DualModeState()
    turn_history = ensure_history(session_state)

    def _active_thread_id() -> str:
        t = azure_state["thread"]
        return t.id if t is not None else synthetic_thread_id

    orch = _get_orchestrator()
    session_id = orch.start_session(thread_id=synthetic_thread_id)
    reset_session_state()
    _reset_model_chain()
    iterm_set_var("crowe_logic_active", "1")
    session_state["session_id"] = synthetic_thread_id
    _sync_session_runtime(session_state)
    session_state["active_model"] = _current_model()["label"]

    show_welcome(AGENT_VERSION)
    telemetry.log_event(
        "session_start",
        {
            "model": _current_model().get("label", "unknown"),
            "session_id": synthetic_thread_id,
            "version": AGENT_VERSION,
        },
    )

    history_file = os.path.join(PROJECT_ROOT, ".chat_history")
    kb = create_chat_keybindings(console=console, state=session_state)
    session = PromptSession(
        history=FileHistory(history_file),
        completer=SlashCompleter(),
        key_bindings=kb,
        bottom_toolbar=build_toolbar,
    )

    prompt_html = HTML('<style fg="#bfa669">\u276f </style>')
    favicon = get_favicon()
    session_state["favicon"] = favicon
    render_session_hud(console, state=session_state, cwd=os.getcwd(), meta="ready")
    console.print()

    while True:
        try:
            # Update iTerm2 duration variable
            elapsed = time.monotonic() - session_state["started_at"]
            minutes = int(elapsed) // 60
            seconds = int(elapsed) % 60
            dur_str = f"{minutes}m {seconds:02d}s" if minutes > 0 else f"{seconds}s"
            iterm_set_var("crowe_logic_duration", dur_str)

            paste_stash.clear()
            user_input = paste_stash.expand(
                session.prompt(prompt_html, multiline=False)
            )
        except (EOFError, KeyboardInterrupt):
            iterm_set_var("crowe_logic_active", "0")
            orch.end_session(summary="Session ended by user")
            console.print("\n  [bold #bfa669]Goodbye.[/bold #bfa669]\n")
            break

        user_input = user_input.strip()
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "/exit", "/quit"):
            iterm_set_var("crowe_logic_active", "0")
            orch.end_session(summary="Session ended by user")
            console.print("  [bold #bfa669]Goodbye.[/bold #bfa669]\n")
            break

        if user_input.lower() == "/tools":
            _list_tools_inline()
            continue
        if user_input.lower() == "/clear":
            console.clear()
            show_welcome(AGENT_VERSION)
            render_session_hud(
                console, state=session_state, cwd=os.getcwd(), meta="ready"
            )
            console.print()
            continue
        if user_input.lower() == "/status":
            _show_status_inline()
            continue
        if user_input.lower() == "/help":
            _show_help()
            continue
        if user_input.lower() == "/data":
            _show_data_telemetry()
            continue
        if user_input.lower() == "/account":
            _show_account_status()
            continue
        if _handle_local_runtime_command(user_input, session_state):
            continue
        if handle_dual_command(user_input, dual_state, console, session_state):
            continue

        # /replay N and /fork N commands: reissue a past user turn. These
        # rewrite user_input to the past turn's input and fall through to
        # the normal dispatch code below. /fork also truncates subsequent
        # turns so the replay becomes the new tail.
        _lower = user_input.lower().strip()
        if _lower.startswith("/replay") or _lower.startswith("/fork"):
            replay_input = _handle_replay_or_fork(
                user_input,
                turn_history,
                dual_state,
                console,
            )
            if replay_input is None:
                continue  # error already reported
            user_input = replay_input
            # Fall through to normal dispatch with the replayed prompt.
        if user_input.lower().startswith("/model"):
            parts = user_input.strip().split(maxsplit=2)
            if len(parts) == 1:
                # Show current model and available chain
                _show_models()
            elif len(parts) >= 2 and parts[1].lower() == "resolve":
                # /model resolve <alias>: print what an alias currently maps to.
                # Useful for debugging the hidden layering between _BASE_MODEL_CHAIN
                # and models.extra.json overrides.
                target = (
                    parts[2].strip().strip("<>").strip("'\"").strip()
                    if len(parts) == 3
                    else ""
                )
                _resolve_model_alias(target)
            else:
                # Switch to specified model. Strip surrounding angle brackets,
                # quotes, and whitespace, users often type the literal `<2>`
                # placeholder syntax from the help hint.
                target = parts[1].strip().strip("<>").strip("'\"").strip()
                _switch_model(azure_state, target)
            continue

        # ── Synapse Router: per-turn auto-routing ──
        # Opt-in via CROWE_LOGIC_AUTO_ROUTE=1. When enabled, every user
        # turn classifies the prompt and silently switches tiers if a
        # different one is the better fit. Slash commands above are
        # excluded; we only route real prompts to models.
        if _auto_route_enabled():
            from config.router import route_prompt
            from config.quality import assess_response

            decision = route_prompt(user_input, availability=_auto_route_available)
            swapped = _apply_route_decision(decision, session_state, user_input)
            if swapped or decision.low_confidence:
                _render_route_badge(console, decision)

        try:
            _sync_session_runtime(session_state)

            # Dual-mode short-circuit: fan out the turn to two providers and
            # render them side-by-side. Bypasses the Auto router and the
            # single-model dispatch entirely; each provider keeps its own
            # message history so follow-ups retain context per-model.
            if dual_state.active:
                # Pre-flight credit reservation. Denies return early so
                # the user sees the deny reason without the HUD flashing
                # a partial turn. Fail-open when the control plane is
                # unreachable; paying users should never be blocked by
                # infra outages.
                if not _preflight_credits(_current_model(), dual_state=dual_state):
                    continue
                ctx = orch.prepare(user_input, thread_id=_active_thread_id())
                render_session_hud(
                    console, state=session_state, cwd=os.getcwd(), meta="dual"
                )
                console.print()
                try:
                    run_dual_turn(
                        user_input,
                        dual_state,
                        console=console,
                        session_state=session_state,
                        get_provider=_get_provider_for_dual,
                        runtime_instructions=_runtime_system_instructions,
                        get_orchestrator=_get_orchestrator,
                    )
                    session_state["api_status"] = "ok"
                    iterm_set_var("crowe_logic_api", "ok")
                    _record_dual_turn_telemetry(dual_state, session_state)
                    turn_history.append(
                        user_input,
                        model_label=session_state.get("active_model", ""),
                        dual_active=True,
                        synth_active=getattr(dual_state, "synth_active", False),
                        synth_mode=getattr(dual_state, "synth_mode", ""),
                    )
                except Exception as exc:
                    _render_error(str(exc), "Dual Mode Error")
                    session_state["api_status"] = "error"
                    iterm_set_var("crowe_logic_api", "error")
                continue

            model_cfg = _current_model()
            router_cfg = None
            auto_candidates = None
            auto_route_index = None
            task_class = None
            # CroweLM Auto: pick the best concrete tier for this user turn.
            if model_cfg.get("provider") == "auto":
                from config.agent_config import route_candidates_for_auto

                router_cfg = model_cfg
                auto_candidates, task_class = route_candidates_for_auto(
                    user_input,
                    availability_check=lambda c: _model_switch_error(c) is None,
                )
                auto_route_index = 0
                model_cfg = auto_candidates[auto_route_index]
                console.print(
                    f"  [dim #bfa669]auto → {model_cfg['label']} · {task_class}[/dim #bfa669]"
                )
            # ── Signed-in users route execution through the gateway (no local
            # keys, no cascade). Mirrors run(); Auto has already resolved a
            # concrete tier above, so the gateway gets a real model id.
            # CROWE_LOGIC_LOCAL=1 is the escape hatch back to local keys. ──
            use_local = os.environ.get("CROWE_LOGIC_LOCAL", "").strip().lower() in (
                "1",
                "true",
                "yes",
            )
            if not use_local:
                from cli import auth, gateway_client

                # Validate (and refresh) the token up front. A stale/unrefreshable
                # session must fall back to the local path, never brick the turn.
                try:
                    auth.current_access_token()
                    routed = True
                except auth.NotLoggedIn:
                    routed = False
                    if not session_state.get("_relogin_hinted"):
                        console.print(
                            "  [dim]Crowe ID session inactive — using local models. "
                            "Run `crowe-logic login` to reconnect.[/dim]"
                        )
                        session_state["_relogin_hinted"] = True

                if routed:
                    from rich.markdown import Markdown

                    render_session_hud(
                        console, state=session_state, cwd=os.getcwd(), meta="turn"
                    )
                    console.print()
                    try:
                        resp = gateway_client.chat(
                            model=model_cfg.get("name", model_cfg.get("label")),
                            messages=[{"role": "user", "content": user_input}],
                        )
                    except gateway_client.PlanDenied as exc:
                        _render_error(str(exc), "Plan does not allow this model")
                        continue
                    except auth.NotLoggedIn:
                        routed = False  # token died mid-call; fall through to local
                    if routed:
                        console.print(Markdown(resp.get("content", "")))
                        console.print()
                        session_state["api_status"] = "ok"
                        iterm_set_var("crowe_logic_api", "ok")
                        continue

            # Pre-flight credit reservation runs after Auto has resolved
            # to a concrete tier so the reservation matches the model
            # that actually answers.
            if not _preflight_credits(model_cfg):
                continue
            ctx = orch.prepare(user_input, thread_id=_active_thread_id())
            render_session_hud(
                console, state=session_state, cwd=os.getcwd(), meta="turn"
            )
            console.print()

            # Smart routing: try current model, fallback on failure
            succeeded = False
            while not succeeded:
                # Preserve the Auto-routed model_cfg across retries; only re-read
                # from _model_state when the user isn't on the router.
                if router_cfg is None:
                    model_cfg = _current_model()
                runtime_instructions = _runtime_system_instructions(
                    model_cfg, session_state
                )
                last_error = None

                for attempt in range(2):
                    try:
                        if model_cfg.get("provider") == "azure_openai":
                            # ── Crowe Logic's own Azure Foundry (OpenAI-compat, key auth) ──
                            provider = _get_azure_openai_provider(
                                model_cfg,
                                system_instructions=runtime_instructions,
                            )
                            provider.add_user_message(user_input)
                            provider.stream_response(
                                console,
                                render_tool_card,
                                session_state,
                                _get_orchestrator,
                            )
                        elif model_cfg.get("provider") == "anthropic":
                            # ── Azure AI Foundry Anthropic (native Anthropic API) ──
                            provider = _get_anthropic_provider(
                                model_cfg,
                                system_instructions=runtime_instructions,
                            )
                            provider.add_user_message(user_input)
                            provider.stream_response(
                                console,
                                render_tool_card,
                                session_state,
                                _get_orchestrator,
                            )
                        elif model_cfg.get("provider") == "nvidia":
                            # ── NVIDIA NIM path (production CroweLM) ──
                            provider = _get_nvidia_provider(
                                model_cfg,
                                system_instructions=runtime_instructions,
                            )
                            provider.add_user_message(user_input)
                            provider.stream_response(
                                console,
                                render_tool_card,
                                session_state,
                                _get_orchestrator,
                            )
                        elif model_cfg.get("provider") == "watsonx":
                            # ── IBM watsonx.ai path (Granite + hosted Llama/Mistral) ──
                            provider = _get_watsonx_provider(
                                model_cfg,
                                system_instructions=runtime_instructions,
                            )
                            provider.add_user_message(user_input)
                            provider.stream_response(
                                console,
                                render_tool_card,
                                session_state,
                                _get_orchestrator,
                            )
                        elif model_cfg.get("provider") == "openai_compat":
                            # ── Crowe-managed self-hosted OpenAI-compatible stack ──
                            provider = _get_hosted_openai_provider(
                                model_cfg,
                                system_instructions=runtime_instructions,
                            )
                            provider.add_user_message(user_input)
                            provider.stream_response(
                                console,
                                render_tool_card,
                                session_state,
                                _get_orchestrator,
                            )
                        elif model_cfg.get("provider") == "openrouter":
                            # ── OpenRouter path (Chat Completions) ──
                            provider = _get_openrouter_provider(
                                model_cfg,
                                system_instructions=runtime_instructions,
                            )
                            provider.add_user_message(user_input)
                            provider.stream_response(
                                console,
                                render_tool_card,
                                session_state,
                                _get_orchestrator,
                            )
                        elif model_cfg.get("provider") == "ollama":
                            # ── Ollama path (local CroweLM fallback) ──
                            provider = _get_ollama_provider(
                                model_cfg,
                                system_instructions=runtime_instructions,
                            )
                            provider.add_user_message(user_input)
                            provider.stream_response(
                                console,
                                render_tool_card,
                                session_state,
                                _get_orchestrator,
                            )
                        elif model_cfg.get("provider") == "deepparallel":
                            # ── CroweLM DeepParallel (cluster orchestrator) ──
                            # Built fresh per turn rather than cached because
                            # DeepParallelProvider tracks per-session message
                            # history internally and the cluster runtime is
                            # single-turn — no shared state to preserve.
                            from providers.runtime_factory import build_provider

                            provider = build_provider(model_cfg.get("name"))
                            provider.system_instructions = runtime_instructions
                            provider.add_user_message(user_input)
                            provider.stream_response(
                                console,
                                render_tool_card,
                                session_state,
                                _get_orchestrator,
                            )
                        else:
                            # ── Legacy Azure AI Agents Service path (provider="azure") ──
                            # Lazy-init the Agents client/thread/agent only when this
                            # branch is actually hit. Raises if .agent_id is missing.
                            _ensure_azure_agents(azure_state)
                            client = azure_state["client"]
                            thread = azure_state["thread"]
                            agent_id = azure_state["agent_id"]
                            _cancel_active_runs(client, thread.id)
                            if attempt == 0:
                                client.messages.create(
                                    thread_id=thread.id,
                                    role="user",
                                    content=user_input,
                                )
                            stream_response(client, thread.id, agent_id)

                        session_state["api_status"] = "ok"
                        iterm_set_var("crowe_logic_api", "ok")
                        # When routed via Auto, the HUD shows "Auto → Tier"
                        # so the user knows the router picked for this turn.
                        if router_cfg is not None:
                            display = f"{router_cfg['label']} → {model_cfg['label']}"
                        else:
                            display = model_cfg["label"]
                        session_state["active_model"] = display
                        iterm_set_var("crowe_logic_model", display)
                        _clear_provider_health(model_cfg.get("provider"))
                        _record_turn_telemetry(model_cfg, session_state)
                        turn_history.append(
                            user_input,
                            model_label=model_cfg.get("label", ""),
                            dual_active=False,
                        )
                        succeeded = True
                        break
                    except KeyboardInterrupt:
                        session_state["api_status"] = "ok"
                        iterm_set_var("crowe_logic_api", "ok")
                        if (
                            model_cfg.get("provider") == "azure"
                            and azure_state["client"] is not None
                            and azure_state["thread"] is not None
                        ):
                            _cancel_active_runs(
                                azure_state["client"], azure_state["thread"].id
                            )
                        console.print(
                            "\n  [dim]Interrupted current turn. You can steer or ask a new question.[/dim]\n"
                        )
                        succeeded = True
                        break
                    except Exception as stream_err:
                        error_msg = str(stream_err)
                        if is_rate_limit_error(error_msg):
                            last_error = error_msg
                            if attempt < 1:
                                wait = 3
                                show_retry_countdown(console, wait, attempt + 2, 2)
                                if (
                                    model_cfg.get("provider") == "azure"
                                    and azure_state["client"] is not None
                                    and azure_state["thread"] is not None
                                ):
                                    _cancel_active_runs(
                                        azure_state["client"], azure_state["thread"].id
                                    )
                                continue
                        elif _is_failover_eligible_error(error_msg):
                            last_error = error_msg
                            break
                        else:
                            raise

                if succeeded:
                    break

                # Model failed — record and try next in the chain
                _model_state["failures"][model_cfg["name"]] = (
                    _model_state["failures"].get(model_cfg["name"], 0) + 1
                )
                try:
                    from config.health import registry as _health_registry

                    _health_registry.record_failure(model_cfg["name"], last_error or "")
                except Exception:
                    pass
                skip_provider = None
                if last_error and _is_provider_wide_error(last_error):
                    _set_provider_health(
                        model_cfg.get("provider", ""),
                        status="blocked",
                        reason=last_error,
                        model_label=model_cfg.get("label", ""),
                    )
                    skip_provider = model_cfg.get("provider")

                if router_cfg is not None:
                    next_index, next_model = _next_auto_model_after_failure(
                        auto_candidates or [],
                        auto_route_index or 0,
                        model_cfg,
                        last_error,
                    )
                    if next_model is not None and next_index is not None:
                        auto_route_index = next_index
                        model_cfg = next_model
                        _reason = (
                            "timeout"
                            if (last_error and "ttft" in last_error.lower())
                            else "error"
                        )
                        console.print(
                            _hedge_banner(
                                target_label=next_model["label"], reason=_reason
                            )
                        )
                        continue

                    session_state["api_status"] = "down"
                    iterm_set_var("crowe_logic_api", "down")
                    _render_error(
                        f"{last_error}\n\nAuto exhausted all fallback tiers after "
                        f"{model_cfg['label']} failed.",
                        f"Auto route failed: {model_cfg['label']}",
                    )
                    break
                next_model = _advance_model(skip_provider=skip_provider)
                if next_model is None:
                    session_state["api_status"] = "down"
                    iterm_set_var("crowe_logic_api", "down")
                    _render_error(
                        f"{last_error}\n\nAll models in the fallback chain failed.",
                        "All Models Failed",
                    )
                    break

                console.print(
                    f"  [dim #bfa669]Model failed — switching to "
                    f"{next_model['label']}...[/dim #bfa669]"
                )

                # If switching to legacy Azure Agents, lazy-init and deploy
                if next_model.get("provider") == "azure":
                    try:
                        _ensure_azure_agents(azure_state)
                        agent_id = _deploy_with_model(
                            azure_state["client"], next_model["name"]
                        )
                        _cancel_active_runs(
                            azure_state["client"], azure_state["thread"].id
                        )
                        azure_state["thread"] = azure_state["client"].threads.create()
                        azure_state["agent_id"] = agent_id
                    except Exception as deploy_err:
                        console.print(
                            f"  [dim red]Fallback deploy failed: {_rich_escape(str(deploy_err))}[/dim red]"
                        )
                        continue

            # ── Synapse: post-response quality signal ──
            # Assess every response (regardless of auto-route flag) so the
            # telemetry stream carries an always-on quality signal. Used
            # to drive future adaptive-promotion logic; today it's
            # observe-only.
            try:
                if "provider" in locals() and getattr(provider, "messages", None):
                    last = provider.messages[-1]
                    if last.get("role") == "assistant":
                        from config.quality import assess_response

                        signal = assess_response(
                            last.get("content") or "",
                            prompt=user_input,
                        )
                        if signal.shallow:
                            telemetry.log_event(
                                "synapse_shallow_response",
                                {
                                    "model": model_cfg.get("label", ""),
                                    "tier": model_cfg.get("type", ""),
                                    "reasons": list(signal.reasons),
                                    "length": signal.length,
                                },
                            )
            except Exception:
                # Quality signal must never break the chat loop.
                pass

            console.print(
                f"  [dim #bfa669]{'─' * min(60, console.width)}[/dim #bfa669]"
            )
        except Exception as e:
            error_msg = str(e)
            if (
                azure_state["client"] is not None
                and azure_state["thread"] is not None
                and "while a run" in error_msg
                and "is active" in error_msg
            ):
                client = azure_state["client"]
                thread = azure_state["thread"]
                agent_id = azure_state["agent_id"]
                _cancel_active_runs(client, thread.id)
                console.print("  [dim]Cancelled stale run — retrying...[/dim]")
                time.sleep(1)
                try:
                    stream_response(client, thread.id, agent_id)
                    console.print(
                        f"  [dim #bfa669]{'─' * min(60, console.width)}[/dim #bfa669]"
                    )
                except Exception as retry_err:
                    _render_error(str(retry_err))
            else:
                _render_error(error_msg)


def _list_tools_inline():
    from tools import user_functions

    table = Table(
        title="Available Tools",
        box=box.ROUNDED,
        border_style="#bfa669",
        title_style="bold #bfa669",
        header_style="bold white",
        show_lines=False,
        padding=(0, 1),
    )
    table.add_column("Tool", style="#bfa669", min_width=22)
    table.add_column("Description", style="white")

    for func in sorted(user_functions, key=lambda f: f.__name__):
        doc = (func.__doc__ or "").strip().split("\n")[0]
        table.add_row(func.__name__, doc)

    table.add_row(
        "[dim]code_interpreter[/dim]",
        "[dim]Run Python in sandbox (Azure built-in)[/dim]",
    )
    console.print()
    console.print(table)
    console.print()


def _show_status_inline():
    model_cfg = _current_model()
    _sync_session_runtime(session_state)
    console.print()
    render_session_hud(console, state=session_state, cwd=os.getcwd(), meta="status")
    render_recent_actions(console, state=session_state)

    table = Table(
        title="CroweLM Foundry",
        box=box.ROUNDED,
        border_style="#bfa669",
        title_style="bold #bfa669",
        show_header=False,
        padding=(0, 1),
    )
    table.add_column("Key", style="#bfa669 bold", min_width=18)
    table.add_column("Value", style="white")
    table.add_row("Active Model", model_cfg["label"])
    table.add_row("Version", AGENT_VERSION)
    table.add_row(
        "Steering", session_state.get("steering_instruction", "") or "[dim]off[/dim]"
    )
    table.add_row("Dataset Context", session_state.get("dataset_selection", "all"))

    # CroweLM training data summary
    try:
        manifest_path = os.path.join(
            PROJECT_ROOT, "data", "crowelm-unified", "DATASET_MANIFEST.json"
        )
        if os.path.exists(manifest_path):
            with open(manifest_path) as f:
                manifest = json.load(f)
            summary = manifest.get("summary", {})
            table.add_row("", "")
            table.add_row("[dim]Training Data[/dim]", "")
            table.add_row("Raw Samples", f"{summary.get('total_raw_samples', 0):,}")
            table.add_row(
                "Training Entries", f"{summary.get('crowelm_training_entries', 0):,}"
            )
            table.add_row("Dataset Size", f"{summary.get('total_size_gb', 0):.2f} GB")
            table.add_row("Domains", summary.get("domains", ""))
    except Exception:
        pass

    # Model chain overview
    table.add_row("", "")
    table.add_row("[dim]CroweLM Models[/dim]", "")
    for i, m in enumerate(MODEL_CHAIN):
        marker = (
            "[bold #6fbf73]>[/bold #6fbf73] "
            if i == _model_state["chain_index"]
            else "  "
        )
        status_note = _model_status_note(m)
        fail_str = f"  [#bf6f6f]({status_note})[/#bf6f6f]" if status_note else ""
        table.add_row(f"{marker}{m['label']}", f"{m.get('type', 'general')}{fail_str}")

    console.print()
    console.print(table)
    console.print()


_PROVIDER_DETAIL_TERMS = (
    "anthropic",
    "claude",
    "cohere",
    "codestral",
    "deepseek",
    "gemini",
    "glm",
    "gpt",
    "granite",
    "grok",
    "kimi",
    "llama",
    "mistral",
    "nemotron",
    "nvidia",
    "openai",
    "opus",
    "qwen",
    "sora",
    "watsonx",
)


def _contains_provider_detail(value: str) -> bool:
    normalized = "".join(ch.lower() for ch in str(value) if ch.isalnum())
    return any(term.replace("-", "") in normalized for term in _PROVIDER_DETAIL_TERMS)


def _resolve_model_alias(alias: str):
    """Print what a model alias currently maps to.

    Read-only diagnostic. Reports the resolved label and public aliases by
    default. Set CROWE_LOGIC_EXPOSE_BACKENDS=1 to inspect provider/backend
    selectors while debugging registry migrations.
    """
    from config.agent_config import resolve_model_config, model_selectors

    if not alias:
        console.print("  [red]usage: /model resolve <alias-or-name>[/red]")
        return
    cfg = resolve_model_config(alias)
    if cfg is None:
        console.print(f"  [red]Unknown alias: {alias}[/red]")
        return

    chain_index = None
    for i, m in enumerate(MODEL_CHAIN):
        if m is cfg:
            chain_index = i
            break

    console.print()
    console.print(f"  [#bfa669 bold]{cfg['label']}[/#bfa669 bold]")
    expose_backend_details = os.environ.get(
        "CROWE_LOGIC_EXPOSE_BACKENDS", ""
    ).lower() in {"1", "true", "yes"}
    console.print(f"  [dim]alias queried:[/dim]  {alias}")
    if expose_backend_details:
        console.print(f"  [dim]provider:[/dim]       {cfg.get('provider', '?')}")
        console.print(
            f"  [dim]backend_name:[/dim]   {cfg.get('backend_name') or cfg.get('name', '?')}"
        )
    else:
        console.print("  [dim]surface:[/dim]        CroweLM managed")
    console.print(f"  [dim]type:[/dim]           {cfg.get('type', 'general')}")
    if chain_index is not None:
        console.print(f"  [dim]chain index:[/dim]    {chain_index + 1}")
    aliases = cfg.get("aliases") or []
    if aliases:
        if expose_backend_details:
            console.print(f"  [dim]aliases:[/dim]        {', '.join(aliases)}")
        else:
            public_aliases = [
                item for item in aliases if not _contains_provider_detail(item)
            ]
            if public_aliases:
                console.print(
                    f"  [dim]aliases:[/dim]        {', '.join(public_aliases)}"
                )
            hidden = len(aliases) - len(public_aliases)
            if hidden:
                console.print(
                    f"  [dim]internal aliases:[/dim] {hidden} hidden "
                    "[dim](set CROWE_LOGIC_EXPOSE_BACKENDS=1 to inspect)[/dim]"
                )
    if expose_backend_details:
        selectors = [s for s in model_selectors(cfg) if s]
        if selectors:
            console.print(f"  [dim]selectors:[/dim]      {', '.join(selectors)}")
    console.print()


def _show_models():
    """Display the model chain with current selection highlighted."""
    table = Table(
        title="CroweLM Models",
        box=box.ROUNDED,
        border_style="#bfa669",
        title_style="bold #bfa669",
        header_style="bold white",
        show_lines=False,
        padding=(0, 1),
    )
    table.add_column("#", style="dim", width=3)
    table.add_column("Model", style="#bfa669", min_width=22)
    table.add_column("Type", style="dim", min_width=10)
    table.add_column("Status", min_width=8)

    for i, m in enumerate(MODEL_CHAIN):
        is_active = i == _model_state["chain_index"]
        status_note = _model_status_note(m)
        status = (
            f"[#bf6f6f]{status_note}[/#bf6f6f]"
            if status_note
            else (
                "[bold #6fbf73]ACTIVE[/bold #6fbf73]"
                if is_active
                else "[dim]standby[/dim]"
            )
        )
        table.add_row(str(i + 1), m["label"], m.get("type", "general"), status)

    console.print()
    console.print(table)
    console.print("  [dim]Switch with: /model 2   or   /model kernel[/dim]")
    console.print()


def _model_status_note(model_cfg: dict) -> str:
    """Return a short status note when a model is blocked or failing."""
    failures = _model_state["failures"].get(model_cfg["name"], 0)
    if failures > 0:
        return f"{failures} fails"
    if _model_switch_error(model_cfg):
        return "blocked"
    return ""


def _model_switch_error(model_cfg: dict) -> str | None:
    """Return a configuration error string for a model, or None if ready."""
    provider = model_cfg.get("provider")
    label = model_cfg.get("label", model_cfg.get("name", "model"))

    # CroweLM Auto has no backend of its own — always available.
    if provider == "auto":
        return None

    provider_block = _provider_health_block(str(provider or ""))
    if provider_block:
        return (
            f"Cannot switch to {label} — recent health check marked {provider} unavailable: "
            f"{provider_block}"
        )

    if provider == "azure_openai":
        from config.agent_config import azure_openai_runtime_config

        runtime = azure_openai_runtime_config(model_cfg)
        if runtime["missing"]:
            return (
                f"Cannot switch to {label} — missing "
                + ", ".join(runtime["missing"])
                + " in .env"
            )

    if provider == "anthropic":
        endpoint_var = model_cfg.get("endpoint_env", "AZURE_ANTHROPIC_ENDPOINT")
        api_key_var = model_cfg.get("api_key_env", "AZURE_ANTHROPIC_API_KEY")
        missing = [
            var
            for var in (endpoint_var, api_key_var)
            if not os.environ.get(var, "").strip()
        ]
        if missing:
            return (
                f"Cannot switch to {label} — missing " + ", ".join(missing) + " in .env"
            )

    if provider == "nvidia":
        missing = [
            var
            for var in ("NVIDIA_NIM_ENDPOINT", "NVIDIA_API_KEY")
            if not os.environ.get(var, "").strip()
        ]
        if missing:
            return (
                f"Cannot switch to {label} — missing " + ", ".join(missing) + " in .env"
            )

    if provider == "openai_compat":
        endpoint_var = model_cfg.get("endpoint_env", "CROWE_OPEN_ENDPOINT")
        endpoint_val = os.environ.get(endpoint_var, "").strip()
        if not endpoint_val:
            return f"Cannot switch to {label} — missing {endpoint_var} in .env"
        # OpenRouter has been retired in favor of NVIDIA NIM + IBM watsonx.
        # Block any openai_compat model still pointed at openrouter.ai so the
        # auto-router skips it instead of producing 402 credit errors.
        if "openrouter.ai" in endpoint_val.lower():
            return (
                f"Cannot switch to {label} — OpenRouter is disabled. "
                "Repoint this model to NVIDIA NIM or IBM watsonx."
            )

    if provider == "watsonx":
        # watsonx.ai brands read credentials from ~/.crowe-logic/ibm.env via
        # config.crowelm.watsonx_adapter; we only verify the env file exists.
        from pathlib import Path as _P

        if not (_P.home() / ".crowe-logic" / "ibm.env").exists():
            return (
                f"Cannot switch to {label} — ~/.crowe-logic/ibm.env missing. "
                "Run the IBM Cloud setup wizard."
            )
        # IBM Granite Guardian / Llama Guard moderation models require a paid
        # watsonx plan ("subscription not registered" on Lite). Block them
        # unless the user explicitly opts in via WATSONX_GUARDIAN_ENABLED=1.
        backend = (model_cfg.get("backend_name") or "").lower()
        is_guardian = (
            "granite-guardian" in backend
            or "llama-guard" in backend
            or label in ("CroweLM Warden", "CroweLM Aegis")
        )
        if is_guardian and os.environ.get(
            "WATSONX_GUARDIAN_ENABLED", ""
        ).strip() not in ("1", "true", "yes"):
            return (
                f"Cannot switch to {label} — watsonx Guardian models require a "
                "paid IBM Cloud plan. Set WATSONX_GUARDIAN_ENABLED=1 once your "
                "subscription is upgraded."
            )

    if provider == "openrouter":
        # OpenRouter has been retired. Force-block so the auto-router never
        # picks it; users must switch to a NVIDIA or watsonx model instead.
        return (
            f"Cannot switch to {label} — OpenRouter has been retired. "
            "Use a NVIDIA NIM or IBM watsonx model instead."
        )

    if provider == "deepparallel":
        # CroweLM DeepParallel routes through the crowe-deepparallel package
        # which orchestrates Foundry-anchored cluster execution. Two prereqs:
        try:
            import crowe_deepparallel  # noqa: F401
        except ImportError:
            return (
                f"Cannot switch to {label} — crowe-deepparallel package not installed. "
                "Run: pip install -e ~/Projects/crowe-logic-foundry-deepparallel-impl"
            )
        # Foundry Kimi-K2.6 is the default judge + a primary cluster anchor.
        missing = [
            var
            for var in ("AZURE_KIMI_ENDPOINT", "AZURE_KIMI_API_KEY")
            if not os.environ.get(var, "").strip()
        ]
        if missing:
            return (
                f"Cannot switch to {label} — missing "
                + ", ".join(missing)
                + " in .env (DeepParallel needs Foundry Kimi-K2.6 as the cluster anchor)"
            )

    return None


def _switch_model(azure_state: dict, target: str):
    """Manually switch to a model by index (1-based) or deployment name."""

    def _activate(model, idx):
        console.print(f"  [#bfa669]Switching to {model['label']}...[/#bfa669]")
        config_error = _model_switch_error(model)
        if config_error:
            console.print(f"  [red]{_rich_escape(config_error)}[/red]")
            return

        _model_state["chain_index"] = idx
        provider = model.get("provider")
        if provider in (
            "anthropic",
            "azure_openai",
            "nvidia",
            "openai_compat",
            "openrouter",
            "ollama",
            "watsonx",
            "deepparallel",
        ):
            # Reset cached provider so the next turn rebuilds with the new model.
            # deepparallel doesn't use a shared client cache (each query builds
            # its own cluster orchestration internally), so the cache_key write
            # is a no-op for it but keeps the dispatch pattern uniform.
            cache_key = (
                "hosted_openai_provider"
                if provider == "openai_compat"
                else f"{provider}_provider"
            )
            _model_state[cache_key] = None
            session_state["active_model"] = model["label"]
            console.print(f"  [#6fbf73]Now using {model['label']}[/#6fbf73]")
        else:
            # Legacy Azure AI Agents — lazy-init and deploy a new agent
            try:
                _ensure_azure_agents(azure_state)
                _deploy_with_model(azure_state["client"], model["name"])
                session_state["active_model"] = model["label"]
                console.print(f"  [#6fbf73]Now using {model['label']}[/#6fbf73]")
            except Exception as e:
                console.print(
                    f"  [red]Failed to activate {model['label']}: {_rich_escape(str(e))}[/red]"
                )
        iterm_set_var("crowe_logic_model", model["label"])

    # Try numeric index first
    try:
        idx = int(target) - 1
        if 0 <= idx < len(MODEL_CHAIN):
            _activate(MODEL_CHAIN[idx], idx)
            return
    except ValueError:
        pass

    from config.agent_config import resolve_model_config

    resolved = resolve_model_config(target)
    if resolved is not None:
        for i, m in enumerate(MODEL_CHAIN):
            if m is resolved:
                _activate(m, i)
                return

    # Fall back to raw substring matching for legacy selectors
    for i, m in enumerate(MODEL_CHAIN):
        if target.lower() in m["name"].lower() or target.lower() in m["label"].lower():
            _activate(m, i)
            return

    console.print(f"  [red]Model not found: {target}[/red]")
    console.print("  [dim]Use /model to see available models[/dim]")


def _show_data_telemetry():
    """Display CroweLM training dataset telemetry."""
    manifest_path = os.path.join(
        PROJECT_ROOT, "data", "crowelm-unified", "DATASET_MANIFEST.json"
    )
    if not os.path.exists(manifest_path):
        console.print("  [dim]No dataset manifest found[/dim]")
        return

    with open(manifest_path) as f:
        manifest = json.load(f)

    summary = manifest.get("summary", {})
    top_domains = manifest.get("top_domains", {})
    datasets = manifest.get("datasets_acquired", {})

    # Header table
    header = Table(
        title="CroweLM Training Data",
        box=box.ROUNDED,
        border_style="#bfa669",
        title_style="bold #bfa669",
        show_header=False,
        padding=(0, 1),
    )
    header.add_column("Key", style="#bfa669 bold", min_width=20)
    header.add_column("Value", style="white")
    header.add_row("Raw Samples", f"{summary.get('total_raw_samples', 0):,}")
    header.add_row(
        "Training Entries", f"{summary.get('crowelm_training_entries', 0):,}"
    )
    header.add_row("Dataset Size", f"{summary.get('total_size_gb', 0):.2f} GB")
    header.add_row("Domains", summary.get("domains", ""))

    # Datasets table
    if datasets:
        header.add_row("", "")
        header.add_row("[dim]Acquired Datasets[/dim]", "")
        for name, desc in datasets.items():
            header.add_row(f"  {name}", str(desc))

    console.print()
    console.print(header)

    # Domain distribution — top 10 as horizontal bars
    if top_domains:
        domain_table = Table(
            title="Domain Distribution (top 10)",
            box=box.ROUNDED,
            border_style="#bfa669",
            title_style="bold #bfa669",
            header_style="bold white",
            show_lines=False,
            padding=(0, 1),
        )
        domain_table.add_column("Domain", style="#bfa669", min_width=14)
        domain_table.add_column("Count", style="white", min_width=8, justify="right")
        domain_table.add_column("Distribution", style="#8fa4bf", min_width=30)

        sorted_domains = sorted(top_domains.items(), key=lambda x: x[1], reverse=True)[
            :10
        ]
        max_count = sorted_domains[0][1] if sorted_domains else 1

        for domain, count in sorted_domains:
            bar_width = int((count / max_count) * 25)
            bar = "\u2588" * bar_width + "\u2591" * (25 - bar_width)
            domain_table.add_row(domain, f"{count:,}", bar)

        console.print()
        console.print(domain_table)

    # Curated examples count
    try:
        from tools.crowelm import _count_curated_examples

        curated = _count_curated_examples()
        if curated > 0:
            console.print(f"\n  [#bfa669]Curated examples:[/#bfa669] {curated:,}")
    except Exception:
        pass

    console.print()


def _show_help():
    table = Table(
        title="Commands",
        box=box.ROUNDED,
        border_style="#bfa669",
        title_style="bold #bfa669",
        show_header=False,
        padding=(0, 1),
    )
    table.add_column("Command", style="#bfa669 bold", min_width=16)
    table.add_column("Action", style="white")
    table.add_row("/tools", "List available tools")
    table.add_row("/model", "Show model chain and active model")
    table.add_row("/model <n>", "Switch to model by number or name")
    table.add_row("/model resolve <a>", "Show what an alias currently maps to")
    table.add_row("/dual", "Show dual-mode status and pairing")
    table.add_row("/dual on", "Enable side-by-side Supreme + Eclipse")
    table.add_row("/dual off", "Disable dual mode")
    table.add_row("/dual <a> <b>", "Pair two models by alias or number")
    table.add_row("/dual synth on", "Post-stream synthesis of both panes")
    table.add_row("/dual synth mode <m>", "merge | judge | diff")
    table.add_row("/dual synth model <a>", "Pick synthesizer model (default Supreme)")
    table.add_row("/replay <N>", "Re-run past turn #N on the current model")
    table.add_row("/replay <N> <a>", "Re-run past turn #N on model alias <a>")
    table.add_row("/fork <N>", "Re-run past turn #N, truncate subsequent turns")
    table.add_row("/account", "Show workspace tier, credit balance, reset date")
    table.add_row("/data", "CroweLM training data telemetry")
    table.add_row("/dataset", "Show or change dataset prompt context")
    table.add_row("/dataset <name>", "Focus the session on a named dataset")
    table.add_row("/dataset off", "Disable injected dataset context")
    table.add_row("/steer <text>", "Persist direction for the current session")
    table.add_row("/steer clear", "Clear persistent steering")
    table.add_row("/transcript", "Show the last full answer and reasoning")
    table.add_row("/status", "Show agent info")
    table.add_row("/clear", "Clear screen")
    table.add_row("/help", "Show this help")
    table.add_row("/exit", "Quit")
    table.add_row("", "")
    table.add_row("[dim]Ctrl+E[/dim]", "[dim]Multi-line editor[/dim]")
    table.add_row(
        "[dim]Ctrl+T[/dim]", "[dim]Open the last transcript in the pager[/dim]"
    )
    table.add_row("[dim]Ctrl+C[/dim]", "[dim]Interrupt the current turn[/dim]")
    table.add_row("[dim]Tab[/dim]", "[dim]Complete / commands[/dim]")
    console.print()
    console.print(table)
    console.print()


@main.command(name="route")
@click.argument("prompt")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit machine-readable JSON instead of a table.",
)
def route(prompt: str, as_json: bool):
    """Show which CroweLM tier the router would pick for PROMPT.

    Pure inspection — no model is invoked. Useful for verifying that
    routing rules behave as expected before wiring the router into
    the main chat loop.
    """
    from config.router import route_prompt
    from config.agent_config import tier_runtime_params

    decision = route_prompt(prompt)
    runtime = tier_runtime_params({"type": decision.selected_type})

    if as_json:
        import json as _json

        payload = decision.to_dict()
        payload["runtime_params"] = runtime
        click.echo(_json.dumps(payload, indent=2))
        return

    table = Table(title="CroweLM Router Decision", show_header=False, box=None)
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Prompt", prompt[:200] + ("..." if len(prompt) > 200 else ""))
    table.add_row("Intent", decision.intent)
    conf_marker = "[red]LOW[/red]" if decision.low_confidence else "[green]ok[/green]"
    table.add_row("Confidence", f"{decision.confidence:.2f} ({conf_marker})")
    table.add_row("Selected", f"{decision.selected_label} ({decision.selected_name})")
    table.add_row("Tier type", decision.selected_type or "(none)")
    if runtime:
        rp = ", ".join(f"{k}={v}" for k, v in runtime.items())
        table.add_row("Runtime params", rp)
    table.add_row("Reason", decision.reason)
    console.print()
    console.print(table)
    console.print()


@main.command(name="login")
def login_cmd():
    """Sign in to Crowe ID in the browser (PKCE)."""
    from cli import auth

    try:
        who = auth.login_pkce()
    except Exception as exc:  # surface any sign-in failure as a clean exit
        console.print(f"  [#bf6f6f]Sign-in failed:[/] {exc}")
        raise SystemExit(1)
    console.print(
        f"  [#bfa669]Signed in as {who['username']} ({who['crowe_tier']}).[/]"
    )


@main.command(name="logout")
def logout_cmd():
    """Forget the stored Crowe ID session."""
    from cli import auth

    auth.logout()
    console.print("  Signed out.")


@main.command(name="whoami")
def whoami_cmd():
    """Show the signed-in Crowe ID identity."""
    from cli import auth

    try:
        who = auth.whoami()
    except auth.NotLoggedIn:
        console.print("  Not signed in. Run `crowe-logic login`.")
        raise SystemExit(1)
    console.print(f"  {who['username']} (tier: {who['crowe_tier']})")


@main.command(name="synapse-doctor")
@click.option(
    "--telemetry-tail",
    "tail_n",
    default=200,
    show_default=True,
    help="How many trailing telemetry records to scan for the summary section.",
)
def synapse_doctor(tail_n: int):
    """Inspect the live Synapse Router configuration and recent telemetry.

    Prints:
    - Active env flags (auto-route, fallback)
    - Confidence ceiling per intent
    - Tier preferences (which models each intent prefers)
    - Tier runtime params (temperature/max_tokens per type)
    - DeepParallel fallback config (model, base URL, timeout)
    - Summary of recent synapse_route + synapse_shallow_response events
      from telemetry.jsonl

    Pure inspection. No model invoked. Safe to run anywhere.
    """
    from config.router import (
        LOW_CONFIDENCE_THRESHOLD,
        _INTENT_CONFIDENCE,
        _INTENT_PREFERENCES,
    )
    from config.agent_config import _TIER_RUNTIME_PARAMS
    from config import synapse_fallback as sf

    section = lambda title: console.print(f"\n[bold {GOLD_HEX}]{title}[/]")

    # Helpers — defer the GOLD_HEX import to avoid yet another top-level
    # change; reuse the route table style.
    from cli.branding import GOLD_HEX

    section("Synapse Router — Live Configuration")
    flags = Table(show_header=False, box=None, padding=(0, 2))
    flags.add_column("Flag", style="bold")
    flags.add_column("Value")
    flags.add_row("CROWE_LOGIC_AUTO_ROUTE", "on" if _auto_route_enabled() else "off")
    flags.add_row(
        "CROWE_LOGIC_SYNAPSE_FALLBACK", "on" if sf.fallback_enabled() else "off"
    )
    flags.add_row("LOW_CONFIDENCE_THRESHOLD", f"{LOW_CONFIDENCE_THRESHOLD:.2f}")
    flags.add_row("Fallback model", sf._model_name())
    flags.add_row("Fallback base URL", sf._base_url())
    flags.add_row("Fallback timeout", f"{sf._timeout_s():.1f}s")
    console.print(flags)

    section("Confidence ceiling by intent")
    conf_table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    conf_table.add_column("Intent")
    conf_table.add_column("Confidence")
    conf_table.add_column("Below threshold?")
    for intent, conf in sorted(_INTENT_CONFIDENCE.items(), key=lambda kv: -kv[1]):
        flag = (
            "[red]LOW[/red]" if conf < LOW_CONFIDENCE_THRESHOLD else "[green]ok[/green]"
        )
        conf_table.add_row(intent, f"{conf:.2f}", flag)
    console.print(conf_table)

    section("Tier preferences (first match wins)")
    pref_table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    pref_table.add_column("Intent")
    pref_table.add_column("Preferred selectors (in order)")
    for intent, selectors in _INTENT_PREFERENCES.items():
        pref_table.add_row(intent, " → ".join(selectors[:4]))
    console.print(pref_table)

    section("Tier runtime params")
    rt_table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    rt_table.add_column("Tier type")
    rt_table.add_column("temperature")
    rt_table.add_column("top_p")
    rt_table.add_column("max_tokens")
    for tier_type, params in _TIER_RUNTIME_PARAMS.items():
        rt_table.add_row(
            tier_type,
            f"{params.get('temperature', '-'):.2f}",
            f"{params.get('top_p', '-'):.2f}",
            str(params.get("max_tokens", "-")),
        )
    console.print(rt_table)

    section(f"Recent telemetry — last {tail_n} synapse events")
    summary = _summarize_synapse_telemetry(tail_n)
    if summary is None:
        console.print(
            "[dim]No telemetry file found at ~/.crowe-logic/runtime/telemetry.jsonl[/dim]"
        )
    elif summary["routes"] == 0 and summary["shallow"] == 0:
        console.print(
            "[dim]No synapse_route or synapse_shallow_response events yet.[/dim]"
        )
        console.print(
            "[dim]Run a chat session with CROWE_LOGIC_AUTO_ROUTE=1 to populate.[/dim]"
        )
    else:
        s_table = Table(show_header=False, box=None, padding=(0, 2))
        s_table.add_column("Metric", style="bold")
        s_table.add_column("Value")
        s_table.add_row("Total route decisions", str(summary["routes"]))
        s_table.add_row("Low-confidence routes", str(summary["low_conf"]))
        s_table.add_row("Fallback overrides", str(summary["fallback_used"]))
        s_table.add_row("Shallow responses", str(summary["shallow"]))
        if summary["intents"]:
            top = ", ".join(f"{k}={v}" for k, v in summary["intents"].items())
            s_table.add_row("Intent distribution", top)
        if summary["tiers"]:
            top = ", ".join(f"{k}={v}" for k, v in summary["tiers"].items())
            s_table.add_row("Tier distribution", top)
        console.print(s_table)
    console.print()


def _summarize_synapse_telemetry(tail_n: int) -> dict | None:
    """Read the last `tail_n` telemetry records and summarize Synapse events.

    Returns None when the telemetry file does not exist. Otherwise returns
    a dict with counts and basic distributions. Tolerant of malformed lines.
    """
    from pathlib import Path
    import json as _json
    from collections import Counter

    path = Path.home() / ".crowe-logic" / "runtime" / "telemetry.jsonl"
    if not path.exists():
        return None

    routes = 0
    low_conf = 0
    fallback_used = 0
    shallow = 0
    intent_counter: Counter = Counter()
    tier_counter: Counter = Counter()

    try:
        # Read tail efficiently for large files: grab last ~64KB then split.
        size = path.stat().st_size
        with path.open("rb") as f:
            if size > 65_536:
                f.seek(-65_536, 2)
                _ = f.readline()  # discard partial line
            lines = f.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return None

    for line in lines[-tail_n:]:
        line = line.strip()
        if not line:
            continue
        try:
            rec = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        if rec.get("type") != "event":
            continue
        cat = rec.get("category", "")
        data = rec.get("data") or {}
        if cat == "synapse_route":
            routes += 1
            if data.get("confidence", 1.0) < 0.60:
                low_conf += 1
            if "DeepParallel" in (data.get("reason") or ""):
                fallback_used += 1
            intent = data.get("intent")
            if intent:
                intent_counter[intent] += 1
            label = data.get("selected_label")
            if label:
                tier_counter[label] += 1
        elif cat == "synapse_shallow_response":
            shallow += 1

    return {
        "routes": routes,
        "low_conf": low_conf,
        "fallback_used": fallback_used,
        "shallow": shallow,
        "intents": dict(intent_counter.most_common(6)),
        "tiers": dict(tier_counter.most_common(6)),
    }


@main.command()
@click.argument("prompt")
def run(prompt: str):
    """Run a single prompt and print the response."""
    # Route through the primary model in the chain.
    # No Azure Agents thread/client needed unless the chain falls through to
    # the legacy "azure" provider.
    reset_session_state()
    _reset_model_chain()
    favicon = get_favicon()
    session_state["favicon"] = favicon
    orch = _get_orchestrator()
    import uuid

    synthetic_thread_id = f"local-{uuid.uuid4().hex[:16]}"
    session_state["session_id"] = synthetic_thread_id
    _sync_session_runtime(session_state)
    orch.start_session(thread_id=synthetic_thread_id)
    if _handle_local_runtime_command(prompt, session_state):
        return

    # Synapse auto-routing for the single-prompt path. Same opt-in flag
    # as chat(); when enabled the router picks the best tier before any
    # model gets instantiated.
    if _auto_route_enabled():
        from config.router import route_prompt

        decision = route_prompt(prompt, availability=_auto_route_available)
        _apply_route_decision(decision, session_state, prompt)
        _render_route_badge(console, decision)

    model_cfg = _current_model()
    session_state["active_model"] = model_cfg["label"]
    orch.prepare(prompt, thread_id=synthetic_thread_id)
    render_session_hud(console, state=session_state, cwd=os.getcwd(), meta="run")
    console.print()

    # ── Signed-in users route execution through the gateway (no local keys, no
    # cascade). The gateway holds every provider key server-side and owns
    # fallback, so a missing local key can never trigger the tier cascade.
    # CROWE_LOGIC_LOCAL=1 is the explicit escape hatch back to local keys. ──
    use_local = os.environ.get("CROWE_LOGIC_LOCAL", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if not use_local:
        from cli import auth, gateway_client

        # Validate (and refresh) the token up front. A stale/unrefreshable session
        # must fall back to the local provider path, never abort the run.
        try:
            auth.current_access_token()
            routed = True
        except auth.NotLoggedIn:
            routed = False  # fall through to the local provider path below
            console.print(
                "  [dim]Crowe ID session inactive — using local models. "
                "Run `crowe-logic login` to reconnect.[/dim]"
            )

        if routed:
            from rich.markdown import Markdown

            try:
                resp = gateway_client.chat(
                    model=model_cfg.get("name", model_cfg.get("label")),
                    messages=[{"role": "user", "content": prompt}],
                )
            except gateway_client.PlanDenied as exc:
                console.print(f"  [#bf6f6f]Plan does not allow this model:[/] {exc}")
                return
            except auth.NotLoggedIn:
                routed = False  # token died mid-call; fall through to local
            if routed:
                console.print(Markdown(resp.get("content", "")))
                console.print()
                return

    provider_kind = model_cfg.get("provider")
    try:
        runtime_instructions = _runtime_system_instructions(model_cfg, session_state)
        if provider_kind == "anthropic":
            provider = _get_anthropic_provider(
                model_cfg,
                system_instructions=runtime_instructions,
            )
        elif provider_kind == "azure_openai":
            provider = _get_azure_openai_provider(
                model_cfg,
                system_instructions=runtime_instructions,
            )
        elif provider_kind == "openai_compat":
            provider = _get_hosted_openai_provider(
                model_cfg,
                system_instructions=runtime_instructions,
            )
        elif provider_kind == "nvidia":
            provider = _get_nvidia_provider(
                model_cfg,
                system_instructions=runtime_instructions,
            )
        elif provider_kind == "watsonx":
            provider = _get_watsonx_provider(
                model_cfg,
                system_instructions=runtime_instructions,
            )
        elif provider_kind == "openrouter":
            provider = _get_openrouter_provider(
                model_cfg,
                system_instructions=runtime_instructions,
            )
        elif provider_kind == "ollama":
            provider = _get_ollama_provider(
                model_cfg,
                system_instructions=runtime_instructions,
            )
        else:
            # Legacy Azure AI Agents path
            azure_state = {"agent_id": None, "client": None, "thread": None}
            _ensure_azure_agents(azure_state)
            client = azure_state["client"]
            thread = azure_state["thread"]
            agent_id = azure_state["agent_id"]
            client.messages.create(thread_id=thread.id, role="user", content=prompt)
            stream_response(client, thread.id, agent_id)
            return

        provider.add_user_message(prompt)
        provider.stream_response(
            console,
            render_tool_card,
            session_state,
            _get_orchestrator,
        )
        _clear_provider_health(provider_kind)
    except Exception as e:
        if _is_provider_wide_error(str(e)):
            _set_provider_health(
                provider_kind,
                status="blocked",
                reason=str(e),
                model_label=model_cfg.get("label", ""),
            )
        _render_error(str(e))


def _deploy_timeout_seconds() -> float:
    """Return the per-provider timeout used by `crowe-logic deploy`."""
    raw = os.environ.get("CROWE_LOGIC_DEPLOY_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return 8.0
    try:
        value = float(raw)
    except ValueError:
        return 8.0
    return max(1.0, min(value, 60.0))


def _classify_deploy_error(error: Exception) -> str:
    """Normalize deploy-health failures into short status labels."""
    err = str(error).lower()
    if "timed out" in err or "timeout" in err:
        return "timeout"
    if "404" in err or "not found" in err:
        return "not found"
    if "401" in err or "403" in err or "auth" in err or "unauthorized" in err:
        return "auth failed"
    if (
        "connection" in err
        or "refused" in err
        or "nodename nor servname" in err
        or "name or service not known" in err
        or "temporary failure in name resolution" in err
    ):
        return "offline"
    return "error"


def _classify_deployment_kind(model_name: str) -> str:
    """Identify the API surface a deployment expects based on its name.

    Foundry MaaS hosts non-chat deployments (embedding, rerank, image,
    video) under the same resource as chat models. The deploy health check
    must use the right OpenAI SDK method for each — calling
    chat.completions.create against an embedding deployment fails with
    DeploymentNotFound, hiding what is in fact a live model.
    """
    lname = model_name.lower()
    if "rerank" in lname:
        return "rerank"
    if "embed" in lname or "embedding" in lname:
        return "embedding"
    if lname.startswith("sora") or "sora-" in lname:
        return "image"
    return "chat"


@main.command()
def deploy():
    """Verify all CroweLM providers and run health checks."""
    import openai
    from openai import OpenAI
    from config.agent_config import (
        MODEL_CHAIN,
        OLLAMA_BASE_URL,
        NVIDIA_NIM_ENDPOINT,
        NVIDIA_API_KEY,
        OPENROUTER_API_KEY,
        OPENROUTER_BASE_URL,
        NEON_DATABASE_URL,
        AGENT_NAME,
        AGENT_VERSION,
        AZURE_CORE_ENDPOINT,
        AZURE_CORE_API_KEY,
        AZURE_GLM_ENDPOINT,
        AZURE_GLM_API_KEY,
        AZURE_ANTHROPIC_ENDPOINT,
        AZURE_ANTHROPIC_API_KEY,
        azure_openai_runtime_config,
        provider_model_name,
    )
    import requests

    console.print(f"\n{'=' * 60}")
    console.print(f"  CROWE LOGIC — DEPLOY HEALTH CHECK")
    console.print(f"  {AGENT_NAME} v{AGENT_VERSION}")
    console.print(f"  request timeout {int(_deploy_timeout_seconds())}s")
    console.print(f"{'=' * 60}\n")

    test_msg = [
        {"role": "system", "content": "You are helpful. Be brief."},
        {"role": "user", "content": "Reply with exactly: OK"},
    ]

    def _token_limit_kwargs(model_name: str) -> dict:
        """
        Newer OpenAI-family deployments (gpt-5+, o-series, gpt-chat-latest)
        reject `max_tokens` and require `max_completion_tokens`. Everything
        else still uses `max_tokens`.
        """
        lname = model_name.lower()
        if (
            lname.startswith(("gpt-5", "o1", "o3", "o4"))
            or "gpt-5" in lname
            or "gpt-chat-latest" in lname
        ):
            return {"max_completion_tokens": 50}
        return {"max_tokens": 50}

    results = []
    timeout_seconds = _deploy_timeout_seconds()

    # Many CroweLM labels are first-party wrappers around the same upstream
    # deployment (e.g. CroweLM Vanguard / Edge / Frontier / Maverick all route
    # to Azure Llama-4-Maverick). Probing each label independently triggers
    # bursty 429s on the shared per-resource TPM pool. Cache by (provider,
    # routing-key) so each real deployment is hit at most once per run; sister
    # labels copy the cached result.
    probe_cache: dict[tuple, tuple[str, int]] = {}

    def _probe_key(model_cfg: dict) -> tuple:
        prov = model_cfg.get("provider", "unknown")
        # For Azure OpenAI the routing identity is `backend_name` (the upstream
        # foundation model on the shared resource); for everything else the
        # deployment id alone is sufficient. Include endpoint_env in the key so
        # two entries that share a backend but route through different
        # endpoints (e.g. CroweLM Talon vs Talon Sandbox via NEMOCLAW_ENDPOINT)
        # don't get collapsed into a single cached result.
        if prov == "azure_openai":
            ident = model_cfg.get("backend_name") or provider_model_name(model_cfg)
        else:
            ident = provider_model_name(model_cfg)
        endpoint_env = model_cfg.get("endpoint_env", "")
        return (prov, endpoint_env, ident)

    def _deploy_probe_mode(model_cfg: dict) -> str:
        raw = (
            str(model_cfg.get("deploy_probe") or model_cfg.get("health_probe") or "")
            .strip()
            .lower()
        )
        if raw in {"metadata", "model_metadata", "metadata_only"}:
            return "metadata"
        return "inference"

    for model in MODEL_CHAIN:
        name = provider_model_name(model)
        label = model["label"]
        provider = model.get("provider", "unknown")
        status = "skip"
        latency = 0

        cache_key = _probe_key(model)
        if cache_key in probe_cache:
            cached_status, cached_latency = probe_cache[cache_key]
            results.append((label, cached_status, cached_latency))
            continue

        try:
            import time

            start = time.monotonic()

            if provider == "azure_openai":
                runtime = azure_openai_runtime_config(model)
                name = runtime["model"]
                endpoint = runtime["endpoint"]
                api_key = runtime["api_key"]
                if runtime["missing"]:
                    status = "no credentials"
                else:
                    base_url = endpoint.rstrip("/")
                    if not base_url.endswith("/v1") and "/openai/v1" not in base_url:
                        if base_url.endswith("/openai"):
                            base_url += "/v1"
                        else:
                            base_url += "/openai/v1"
                    client = OpenAI(
                        api_key=api_key,
                        base_url=base_url,
                        timeout=timeout_seconds,
                        max_retries=0,
                    )
                    kind = _classify_deployment_kind(name)
                    probe_mode = _deploy_probe_mode(model)
                    if probe_mode == "metadata":
                        # Some shared MaaS deployments are real but very low
                        # quota. A metadata probe verifies the deployment is
                        # mounted without consuming TPM and turning the health
                        # check itself into a 429 source. Preserve the same
                        # "not found" signal the ollama and embedding paths
                        # emit so operators see the same status label whether
                        # a missing deployment is caught via metadata or
                        # inference probe.
                        try:
                            client.models.retrieve(name)
                            status = "live"
                        except openai.NotFoundError:
                            status = "not found"
                    elif model.get("surface") == "responses":
                        resp = client.responses.create(
                            model=name,
                            input="Reply with exactly: OK",
                            max_output_tokens=50,
                        )
                        status = (
                            "live"
                            if getattr(resp, "output_text", "").strip()
                            else "empty"
                        )
                    elif kind == "embedding":
                        # Cohere Embed v4 on Azure rejects bare-string input —
                        # the ``input`` must be a list, and the request must
                        # carry an ``input_type`` from {'text', 'query',
                        # 'document'} (not Cohere's standard search_document
                        # / classification / clustering / image values). The
                        # OpenAI text-embedding-* family accepts list-form
                        # ``input`` and ignores extra_body, so the same call
                        # path is safe for both deployment families.
                        resp = client.embeddings.create(
                            model=name,
                            input=["ping"],
                            extra_body={"input_type": "document"},
                        )
                        status = "live" if resp.data else "empty"
                    elif kind == "image":
                        # Image / video deployments (sora-2): a real generation
                        # call costs money and seconds. The cheapest health
                        # probe is to list the deployment's metadata via the
                        # generic models endpoint — if the deployment isn't
                        # reachable we'll see 404; otherwise the deployment
                        # is callable and we mark live without paying for a
                        # real generation.
                        client.models.retrieve(name)
                        status = "live"
                    elif kind == "rerank":
                        # Cohere Rerank V4 on Azure exposes a non-OpenAI-shaped
                        # endpoint at /openai/v1/rerank — the OpenAI SDK has no
                        # client.rerank, so fall back to a raw HTTP POST. A
                        # one-document rerank costs essentially nothing.
                        import httpx

                        rerank_url = f"{base_url}/rerank"
                        rerank_resp = httpx.post(
                            rerank_url,
                            headers={
                                "api-key": api_key,
                                "Content-Type": "application/json",
                            },
                            json={
                                "model": name,
                                "query": "ping",
                                "documents": ["pong"],
                            },
                            timeout=timeout_seconds,
                        )
                        if rerank_resp.status_code == 200:
                            status = "live"
                        elif rerank_resp.status_code == 404:
                            status = "not found"
                        else:
                            raise RuntimeError(
                                f"rerank probe HTTP {rerank_resp.status_code}: "
                                f"{rerank_resp.text[:200]}"
                            )
                    else:
                        resp = client.chat.completions.create(
                            model=name,
                            messages=test_msg,
                            **_token_limit_kwargs(name),
                        )
                        status = "live" if resp.choices else "empty"

            elif provider == "openai_compat":
                endpoint_var = model.get("endpoint_env", "CROWE_OPEN_ENDPOINT")
                api_key_var = model.get("api_key_env", "CROWE_OPEN_API_KEY")
                endpoint = os.environ.get(endpoint_var, "")
                api_key = os.environ.get(api_key_var, "")
                if not endpoint:
                    status = "no endpoint"
                else:
                    base_url = endpoint.rstrip("/")
                    if not base_url.endswith("/v1"):
                        base_url += "/v1"
                    client = OpenAI(
                        api_key=api_key or "crowe-logic",
                        base_url=base_url,
                        timeout=timeout_seconds,
                        max_retries=0,
                    )
                    resp = client.chat.completions.create(
                        model=name,
                        messages=test_msg,
                        **_token_limit_kwargs(name),
                    )
                    content = (resp.choices[0].message.content or "").strip()
                    status = "live" if resp.choices else "empty"

            elif provider == "anthropic":
                from anthropic import Anthropic

                endpoint_var = model.get("endpoint_env", "AZURE_ANTHROPIC_ENDPOINT")
                api_key_var = model.get("api_key_env", "AZURE_ANTHROPIC_API_KEY")
                endpoint = os.environ.get(endpoint_var, "")
                api_key = os.environ.get(api_key_var, "")
                # Fallback to direct Anthropic API when no Azure-Anthropic
                # surface is configured but a public ANTHROPIC_API_KEY is
                # available. The Azure-Anthropic surface lives at
                # <resource>.openai.azure.com/anthropic; the public API at
                # api.anthropic.com — different URL conventions, so detect
                # by hostname and skip the /anthropic suffix for the latter.
                if (not endpoint or not api_key) and os.environ.get(
                    "ANTHROPIC_API_KEY"
                ):
                    endpoint = "https://api.anthropic.com"
                    api_key = os.environ["ANTHROPIC_API_KEY"]
                if not endpoint or not api_key:
                    status = "no credentials"
                else:
                    base_url = endpoint.rstrip("/")
                    if "api.anthropic.com" not in base_url and not base_url.endswith(
                        "/anthropic"
                    ):
                        base_url += "/anthropic"
                    client = Anthropic(
                        api_key=api_key, base_url=base_url, timeout=timeout_seconds
                    )
                    resp = client.messages.create(
                        model=name,
                        max_tokens=50,
                        messages=[
                            {"role": "user", "content": "Reply with exactly: OK"}
                        ],
                    )
                    status = "live" if getattr(resp, "content", None) else "empty"

            elif provider == "nvidia":
                if not NVIDIA_NIM_ENDPOINT or not NVIDIA_API_KEY:
                    status = "no credentials"
                else:
                    client = OpenAI(
                        api_key=NVIDIA_API_KEY,
                        base_url=f"{NVIDIA_NIM_ENDPOINT.rstrip('/')}/v1",
                        timeout=timeout_seconds,
                        max_retries=0,
                    )
                    resp = client.chat.completions.create(
                        model=name,
                        messages=test_msg,
                        **_token_limit_kwargs(name),
                    )
                    content = (resp.choices[0].message.content or "").strip()
                    status = "live" if resp.choices else "empty"

            elif provider == "ollama":
                # Ollama first-load can take 15-30s for a multi-GB tag while
                # weights stream into memory — far past the 8s deploy budget.
                # The mount check we actually want is "does this tag exist in
                # the local registry?", which `/api/show` answers in <100ms
                # without touching the model. A failing tag yields HTTP 404.
                import requests as _rq
                from providers.ollama import select_ollama_base_url

                base_url = select_ollama_base_url(name, OLLAMA_BASE_URL)
                show_url = base_url.replace("/v1", "").rstrip("/") + "/api/show"
                resp = _rq.post(
                    show_url,
                    json={"name": name},
                    timeout=timeout_seconds,
                )
                if resp.status_code == 200:
                    status = "live"
                elif resp.status_code == 404:
                    status = "not found"
                else:
                    raise RuntimeError(
                        f"ollama probe HTTP {resp.status_code}: {resp.text[:200]}"
                    )

            elif provider == "openrouter":
                if not OPENROUTER_API_KEY:
                    status = "no key"
                else:
                    client = OpenAI(
                        api_key=OPENROUTER_API_KEY,
                        base_url=OPENROUTER_BASE_URL,
                        timeout=timeout_seconds,
                        max_retries=0,
                    )
                    resp = client.chat.completions.create(
                        model=name,
                        messages=test_msg,
                        **_token_limit_kwargs(name),
                    )
                    content = (resp.choices[0].message.content or "").strip()
                    status = "live" if resp.choices else "empty"

            elif provider == "watsonx":
                from pathlib import Path as _P

                if not (_P.home() / ".crowe-logic" / "ibm.env").exists():
                    status = "no credentials"
                else:
                    from providers.watsonx import WatsonxProvider

                    wx = WatsonxProvider(
                        model=name,
                        system_instructions="ping",
                        label=label,
                        max_tokens=8,
                        temperature=0.0,
                    )
                    wx.add_user_message("Reply with exactly: OK")
                    data = wx._call_watsonx(tools=None)
                    msg = (data.get("choices") or [{}])[0].get("message", {}) or {}
                    status = "live" if msg.get("content") else "empty"

            elif provider in ("auto", "router", "deepparallel"):
                # Virtual tiers: they fan out to other entries in the chain
                # rather than holding their own backend deployment. Surface a
                # neutral "virtual" status so the deploy table doesn't paint
                # them red — the underlying tiers they delegate to are probed
                # elsewhere in this same run.
                status = "virtual"

            else:
                status = "unknown provider"

            latency = int((time.monotonic() - start) * 1000)

        except Exception as e:
            status = _classify_deploy_error(e)
            # Surface the underlying exception when DEPLOY_VERBOSE is set, so
            # triage runs can see WHY a tier shows the generic "error" status.
            if os.environ.get("DEPLOY_VERBOSE", "").lower() in {"1", "true", "yes"}:
                console.print(
                    f"    [dim red]{label}: {type(e).__name__}: {e!s:.300}[/dim red]"
                )

        probe_cache[cache_key] = (status, latency)
        results.append((label, status, latency))

    # Display results
    table = Table(
        title="CroweLM Model Health",
        box=box.ROUNDED,
        border_style="#bfa669",
        title_style="bold #bfa669",
        header_style="bold white",
        show_lines=False,
        padding=(0, 1),
    )
    table.add_column("Model", style="#bfa669", min_width=22)
    table.add_column("Status", min_width=12)
    table.add_column("Latency", style="dim", min_width=8)

    for label, status, latency in results:
        if status == "live":
            status_fmt = "[bold #6fbf73]LIVE[/bold #6fbf73]"
        elif status in ("not found", "offline", "timeout"):
            status_fmt = f"[#bf6f6f]{status}[/#bf6f6f]"
        elif status in ("no credentials", "no key", "no endpoint", "auth failed"):
            status_fmt = f"[yellow]{status}[/yellow]"
        elif status == "virtual":
            status_fmt = "[dim cyan]virtual[/dim cyan]"
        else:
            status_fmt = f"[red]{status}[/red]"

        lat_str = f"{latency}ms" if latency > 0 else "-"
        table.add_row(label, status_fmt, lat_str)

    console.print(table)

    # Check Neon DB
    console.print()
    if NEON_DATABASE_URL:
        try:
            resp = requests.head(NEON_DATABASE_URL, timeout=timeout_seconds)
            if resp.status_code < 500:
                console.print(
                    "  [#6fbf73]Neon Postgres[/#6fbf73]  [dim]connected[/dim]"
                )
            else:
                console.print(
                    f"  [#bf6f6f]Neon Postgres[/#bf6f6f]  [dim]HTTP {resp.status_code}[/dim]"
                )
        except Exception:
            console.print("  [#bf6f6f]Neon Postgres[/#bf6f6f]  [dim]unreachable[/dim]")
    else:
        console.print("  [yellow]Neon Postgres[/yellow]  [dim]not configured[/dim]")

    # Check local inference engine
    try:
        resp = requests.get(OLLAMA_BASE_URL.replace("/v1", ""), timeout=timeout_seconds)
        console.print("  [#6fbf73]Local engine[/#6fbf73]    [dim]running[/dim]")
    except Exception:
        console.print("  [#bf6f6f]Local engine[/#bf6f6f]    [dim]not running[/dim]")

    live_count = sum(1 for _, s, _ in results if s == "live")
    virtual_count = sum(1 for _, s, _ in results if s == "virtual")
    total = len(results)
    if virtual_count:
        console.print(
            f"\n  {live_count}/{total} models online  "
            f"[dim](+{virtual_count} virtual routing tiers)[/dim]"
        )
    else:
        console.print(f"\n  {live_count}/{total} models online")

    if live_count > 0:
        console.print(f"\n{'=' * 60}")
        console.print(f"  READY -- Run: crowe-logic chat")
        console.print(f"{'=' * 60}\n")
    else:
        console.print(
            f"\n  [bold red]No models available. Check credentials and connectivity.[/bold red]\n"
        )


@main.group()
def models():
    """Manage the synced extra-model registry."""
    pass


@models.command(name="sync")
@click.option("--account", help="Azure Cognitive Services account name")
@click.option("--resource-group", help="Azure resource group for the account")
@click.option(
    "--input",
    "input_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Read deployment inventory from a saved Azure JSON file",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write the synced registry to this path",
)
def models_sync(
    account: str | None,
    resource_group: str | None,
    input_path: Path | None,
    output_path: Path | None,
):
    """Sync Azure deployments into the extra-model registry."""
    from config.model_sync import (
        sync_output_warnings,
        build_extra_models_payload,
        parse_sync_source,
        resolve_output_path,
        write_extra_models_payload,
    )

    if input_path is None and not (account and resource_group):
        raise click.UsageError(
            "Provide either --input or both --account and --resource-group"
        )

    try:
        deployments = parse_sync_source(
            input_path=input_path,
            account=account,
            resource_group=resource_group,
        )
        payload = build_extra_models_payload(deployments)
        destination = write_extra_models_payload(
            payload,
            resolve_output_path(output_path),
        )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    model_count = len(payload["models"])
    console.print(
        f"  [#6fbf73]Synced {model_count} models to "
        f"{_rich_escape(str(destination))}[/#6fbf73]"
    )
    for warning in sync_output_warnings(destination, project_root=Path(PROJECT_ROOT)):
        console.print(f"  [yellow]{_rich_escape(warning)}[/yellow]")
    console.print(
        "  [dim]The updated registry will be picked up on the next crowe-logic run.[/dim]\n"
    )


@main.command()
def status():
    """Show current agent status."""
    _show_status_inline()


@main.command()
def tools():
    """List all available tools."""
    _list_tools_inline()


@main.command(name="headless")
@click.option(
    "--input",
    "input_path",
    help="Read headless JSON input from this file instead of stdin",
)
@click.option(
    "--model",
    default="auto",
    show_default=True,
    help="Model id from MODEL_CHAIN to run in headless mode",
)
def headless_cmd(input_path: str | None, model: str):
    """Run one headless JSON-streaming turn for external hosts."""
    from cli.headless import main as headless_main

    argv = ["crowe-logic-headless"]
    if input_path:
        argv.extend(["--input", input_path])
    if model:
        argv.extend(["--model", model])

    old_argv = sys.argv[:]
    try:
        sys.argv = argv
        raise SystemExit(headless_main())
    finally:
        sys.argv = old_argv


@main.group(name="internal")
def internal_cmd():
    """Plan owner/staff-only internal development agents."""
    pass


@internal_cmd.command(name="agents")
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable JSON")
def internal_agents_cmd(json_output: bool):
    """Show the recommended internal development agent team."""
    from crowe_synapse_engine.internal_development import (
        build_internal_development_plan,
    )

    plan = build_internal_development_plan()
    if json_output:
        click.echo(json.dumps(plan.to_dict(), indent=2))
        return

    console.print()
    console.print(
        f"[#bfa669 bold]Crowe Logic Internal Agents[/] "
        f"[dim]recommended count: {plan.recommended_count}[/dim]"
    )
    console.print(
        f"[dim]Access: {plan.access_policy.scope} | Workspace: "
        f"{_rich_escape(plan.workspace)}[/dim]\n"
    )
    table = Table(
        box=box.ROUNDED,
        border_style="#bfa669",
        header_style="bold white",
        padding=(0, 1),
    )
    table.add_column("Agent", style="#bfa669", min_width=26)
    table.add_column("Charter", style="white", max_width=58)
    table.add_column("Model Pref", style="dim", max_width=26)
    table.add_column("Tags", style="dim", max_width=24)
    for agent in plan.agents:
        table.add_row(
            agent.agent_id,
            agent.charter,
            " > ".join(agent.model_preference),
            ", ".join(agent.mission_tags),
        )
    console.print(table)
    console.print()


@internal_cmd.command(name="plan")
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable JSON")
@click.option(
    "--surface",
    default="claude-console",
    show_default=True,
    help="Deployment surface to plan for",
)
def internal_plan_cmd(json_output: bool, surface: str):
    """Show self-heal architecture and Claude Console deployment scaffold."""
    from crowe_synapse_engine.internal_development import (
        build_internal_development_plan,
    )

    plan = build_internal_development_plan(deployment_surface=surface)
    if json_output:
        click.echo(json.dumps(plan.to_dict(), indent=2))
        return

    console.print()
    console.print("[#bfa669 bold]Internal Development Architecture[/]")
    console.print(
        f"[dim]Surface: {_rich_escape(plan.deployment_surface)} | "
        f"Workspace: {_rich_escape(plan.workspace)} | "
        f"Agents: {plan.recommended_count}[/dim]\n"
    )

    access_table = Table(
        title="Access Policy",
        box=box.ROUNDED,
        border_style="#bfa669",
        title_style="bold #bfa669",
        show_header=False,
        padding=(0, 1),
    )
    access_table.add_column("Key", style="#bfa669 bold", min_width=18)
    access_table.add_column("Value", style="white")
    access_table.add_row("Scope", plan.access_policy.scope)
    access_table.add_row(
        "Owners", ", ".join(plan.access_policy.owner_principals) or "unset"
    )
    access_table.add_row(
        "Approved staff",
        ", ".join(plan.access_policy.approved_staff_principals) or "none",
    )
    access_table.add_row("Tool policy", plan.access_policy.default_tool_policy)
    console.print(access_table)
    console.print()

    heal_table = Table(
        title="Self-Heal Loops",
        box=box.ROUNDED,
        border_style="#6fbf73",
        title_style="bold #6fbf73",
        padding=(0, 1),
    )
    heal_table.add_column("Loop", style="#6fbf73", min_width=26)
    heal_table.add_column("Trigger", style="white", max_width=44)
    heal_table.add_column("Approval Gate", style="dim", max_width=44)
    for loop in plan.self_heal_loops:
        heal_table.add_row(loop.name, loop.trigger, loop.approval_gate)
    console.print(heal_table)
    console.print()

    console.print("[#bfa669 bold]Claude Console Deployment Plan[/]")
    for idx, step in enumerate(plan.deployment_steps, start=1):
        console.print(f"  {idx}. {_rich_escape(step)}")
    console.print()
    console.print(
        "[dim]This command only scaffolds the plan and payloads. It does not "
        "create external agents or change Console membership.[/dim]\n"
    )


@internal_cmd.command(name="deploy")
@click.option(
    "--backend",
    type=click.Choice(["sovereign", "anthropic", "aws", "browser", "all"]),
    default="sovereign",
    show_default=True,
    help="Deployment surface to plan for",
)
@click.option(
    "--apply",
    "do_apply",
    is_flag=True,
    help="Execute the plan (sovereign only; external backends stay gated)",
)
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable JSON")
def internal_deploy_cmd(backend: str, do_apply: bool, json_output: bool):
    """Plan (and, for sovereign, apply) internal agent deployment."""
    from crowe_synapse_engine.internal_deployment import (
        apply_sovereign,
        build_actions,
        render_plan,
    )
    from crowe_synapse_engine.internal_development import (
        build_internal_development_plan,
    )

    plan = build_internal_development_plan()
    actions = build_actions(plan, backend)

    applied = False
    written: list[str] = []
    if do_apply:
        external = [a for a in actions if a.backend != "sovereign"]
        if external:
            msg = (
                f"Apply blocked: backend '{backend}' creates live external agents. "
                "This is gated on an authenticated credential/session and explicit "
                "owner approval. Run without --apply to inspect the plan, or use "
                "--backend sovereign to apply locally."
            )
            if json_output:
                click.echo(
                    json.dumps({"backend": backend, "applied": False, "error": msg})
                )
            else:
                console.print(f"[red]{_rich_escape(msg)}[/red]")
            raise SystemExit(2)
        written = apply_sovereign(actions)
        applied = True

    if json_output:
        click.echo(
            json.dumps(
                {
                    "backend": backend,
                    "applied": applied,
                    "written": written,
                    "actions": [a.to_dict() for a in actions],
                },
                indent=2,
            )
        )
        return

    console.print()
    console.print(
        f"[#bfa669 bold]Internal Deployment[/] [dim]backend: {backend} | "
        f"actions: {len(actions)} | applied: {applied}[/dim]\n"
    )
    console.print(_rich_escape(render_plan(actions)))
    console.print()
    if applied:
        console.print(f"[#6fbf73]Wrote {len(written)} sovereign persona(s):[/]")
        for path in written:
            console.print(f"  {_rich_escape(path)}")
        console.print(
            "\n[dim]These live under agents/internal/ and are NOT auto-loaded by the "
            "flat public registry. Point an owner-gated AgentRegistry at that subdir "
            "to enable them.[/dim]"
        )
    else:
        console.print(
            "[dim]Dry run. External backends (anthropic/aws/browser) require an "
            "authenticated path + owner approval before --apply.[/dim]"
        )
    console.print()


@main.command()
def agents():
    """List registered sub-agents."""
    orch = _get_orchestrator()
    agent_list = orch.list_agents()
    if not agent_list:
        console.print("  [dim]No agents configured[/dim]")
        return
    table = Table(
        title="Sub-Agents",
        box=box.ROUNDED,
        border_style="#bfa669",
        title_style="bold #bfa669",
        header_style="bold white",
        padding=(0, 1),
    )
    table.add_column("Agent", style="#bfa669", min_width=14)
    table.add_column("Description", style="white")
    table.add_column("Tools", style="dim")
    for a in agent_list:
        tools_str = ", ".join(a.tools[:4])
        if len(a.tools) > 4:
            tools_str += f" +{len(a.tools) - 4}"
        table.add_row(a.name, a.description, tools_str)
    console.print()
    console.print(table)
    console.print()


@main.command()
def pipelines():
    """List available pipeline templates."""
    orch = _get_orchestrator()
    pipe_list = orch.list_pipelines()
    if not pipe_list:
        console.print("  [dim]No pipelines configured[/dim]")
        return
    table = Table(
        title="Pipeline Templates",
        box=box.ROUNDED,
        border_style="#bfa669",
        title_style="bold #bfa669",
        header_style="bold white",
        padding=(0, 1),
    )
    table.add_column("Pipeline", style="#bfa669", min_width=14)
    table.add_column("Description", style="white")
    table.add_column("Trigger", style="dim")
    for p in pipe_list:
        table.add_row(p.name, p.description, p.trigger or "")
    console.print()
    console.print(table)
    console.print()


@main.command()
@click.option("--limit", default=10, help="Number of sessions to show")
def history(limit: int):
    """Show recent chat sessions."""
    orch = _get_orchestrator()
    sessions = orch.get_history(limit=limit)
    if not sessions:
        console.print("  [dim]No session history yet[/dim]")
        return
    table = Table(
        title="Session History",
        box=box.ROUNDED,
        border_style="#bfa669",
        title_style="bold #bfa669",
        header_style="bold white",
        padding=(0, 1),
    )
    table.add_column("Started", style="#bfa669", min_width=20)
    table.add_column("Thread", style="dim", max_width=20)
    table.add_column("Summary", style="white")
    for s in sessions:
        started = s.get("started_at", "")[:19]
        thread = s.get("thread_id", "")[:16] + "..."
        summary = (s.get("summary") or "[dim]no summary[/dim]")[:60]
        table.add_row(started, thread, summary)
    console.print()
    console.print(table)
    console.print()


@main.command()
def resume():
    """Resume the last chat session with context."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.formatted_text import HTML

    orch = _get_orchestrator()
    sessions = orch.get_history(limit=1)
    if not sessions:
        console.print("  [dim]No previous sessions to resume[/dim]")
        return
    last = sessions[0]
    thread_id = last["thread_id"]
    console.print(
        f"  [#bfa669]Resuming session:[/#bfa669] {last.get('summary', 'no summary')}"
    )
    console.print(f"  [dim]Thread: {thread_id}[/dim]")

    # Resume is inherently tied to a legacy Azure Agents thread. Synthetic
    # local-* thread IDs from the new provider system have no server-side
    # state to resume, so refuse them up front with a clear message.
    if thread_id.startswith("local-"):
        console.print(
            "  [yellow]This session has no server-side state to resume "
            "(it ran on a non-Azure-Agents model).[/yellow]"
        )
        console.print("  [dim]Start a fresh chat with `crowe-logic chat`.[/dim]\n")
        return

    agent_id = get_agent_id()
    if not agent_id:
        console.print(
            "  [red]No agent found.[/red] Run `crowe-logic deploy` to create one "
            "before resuming a legacy Azure Agents thread.\n"
        )
        return
    client = get_client()
    orch.start_session(thread_id=thread_id)
    reset_session_state()
    iterm_set_var("crowe_logic_active", "1")
    session_state["session_id"] = thread_id
    _sync_session_runtime(session_state)

    history_file = os.path.join(PROJECT_ROOT, ".chat_history")
    kb = create_chat_keybindings(console=console, state=session_state)
    session = PromptSession(
        history=FileHistory(history_file),
        completer=SlashCompleter(),
        key_bindings=kb,
        bottom_toolbar=build_toolbar,
    )
    prompt_html = HTML('<style fg="#bfa669">\u276f </style>')
    favicon = get_favicon()
    session_state["favicon"] = favicon
    session_state["active_model"] = session_state.get("active_model") or "crowe-logic"
    render_session_hud(console, state=session_state, cwd=os.getcwd(), meta="resume")
    console.print()

    while True:
        try:
            # Update iTerm2 duration variable
            elapsed = time.monotonic() - session_state["started_at"]
            minutes = int(elapsed) // 60
            seconds = int(elapsed) % 60
            dur_str = f"{minutes}m {seconds:02d}s" if minutes > 0 else f"{seconds}s"
            iterm_set_var("crowe_logic_duration", dur_str)

            paste_stash.clear()
            user_input = paste_stash.expand(
                session.prompt(prompt_html, multiline=False)
            )
        except (EOFError, KeyboardInterrupt):
            iterm_set_var("crowe_logic_active", "0")
            orch.end_session(summary="Resumed session ended by user")
            console.print("\n  [bold #bfa669]Goodbye.[/bold #bfa669]\n")
            break
        user_input = user_input.strip()
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "/exit", "/quit"):
            iterm_set_var("crowe_logic_active", "0")
            orch.end_session(summary="Resumed session ended by user")
            console.print("  [bold #bfa669]Goodbye.[/bold #bfa669]\n")
            break
        if user_input.lower() == "/tools":
            _list_tools_inline()
            continue
        if user_input.lower() == "/clear":
            console.clear()
            show_welcome(AGENT_VERSION)
            render_session_hud(
                console, state=session_state, cwd=os.getcwd(), meta="resume"
            )
            console.print()
            continue
        if user_input.lower() == "/status":
            _show_status_inline()
            continue
        if user_input.lower() == "/help":
            _show_help()
            continue
        if user_input.lower() == "/data":
            _show_data_telemetry()
            continue
        if _handle_local_runtime_command(user_input, session_state):
            continue
        try:
            _cancel_active_runs(client, thread_id)
            client.messages.create(thread_id=thread_id, role="user", content=user_input)
            render_session_hud(
                console, state=session_state, cwd=os.getcwd(), meta="turn"
            )
            console.print()

            last_error = None
            for attempt in range(3):
                try:
                    stream_response(client, thread_id, agent_id)
                    session_state["api_status"] = "ok"
                    last_error = None
                    break
                except Exception as stream_err:
                    error_msg = str(stream_err)
                    if is_rate_limit_error(error_msg):
                        last_error = error_msg
                        if attempt < 2:
                            wait = (attempt + 1) * 2
                            show_retry_countdown(console, wait, attempt + 2, 3)
                            _cancel_active_runs(client, thread_id)
                            continue
                    else:
                        raise
                except KeyboardInterrupt:
                    _cancel_active_runs(client, thread_id)
                    session_state["api_status"] = "ok"
                    console.print(
                        "\n  [dim]Interrupted current turn. You can steer or ask a new question.[/dim]\n"
                    )
                    last_error = None
                    break
            if last_error:
                session_state["api_status"] = "down"
                _render_error(last_error, "Run Failed (after 3 attempts)")

            console.print(
                f"  [dim #bfa669]{'─' * min(60, console.width)}[/dim #bfa669]"
            )
        except Exception as e:
            error_msg = str(e)
            if "while a run" in error_msg and "is active" in error_msg:
                _cancel_active_runs(client, thread_id)
                console.print("  [dim]Cancelled stale run — retrying...[/dim]")
                time.sleep(1)
                try:
                    stream_response(client, thread_id, agent_id)
                    console.print(
                        f"  [dim #bfa669]{'─' * min(60, console.width)}[/dim #bfa669]"
                    )
                except Exception as retry_err:
                    _render_error(str(retry_err))
            else:
                _render_error(error_msg)


@main.group()
def iterm():
    """Manage iTerm2 native integration."""
    pass


@iterm.command()
def install():
    """Install the iTerm2 companion daemon and Crowe Logic profile."""
    success, msg = install_iterm()
    if success:
        console.print(f"\n  [#6fbf73]{msg}[/#6fbf73]\n")
    else:
        console.print(f"\n  [bold red]{msg}[/bold red]\n")
        if "Python API" in msg:
            console.print(
                "  [dim]Enable at: Preferences > General > Magic > Enable Python API[/dim]\n"
            )


@iterm.command()
def uninstall():
    """Remove the iTerm2 companion daemon."""
    success, msg = uninstall_iterm()
    if success:
        console.print(f"\n  [#6fbf73]{msg}[/#6fbf73]\n")
    else:
        console.print(f"\n  [bold red]{msg}[/bold red]\n")


@iterm.command(name="status")
def iterm_status_cmd():
    """Show iTerm2 integration status."""
    from rich.table import Table
    from rich import box

    info = iterm_status()
    table = Table(
        title="iTerm2 Integration",
        box=box.ROUNDED,
        border_style="#bfa669",
        title_style="bold #bfa669",
        show_header=False,
        padding=(0, 1),
    )
    table.add_column("Check", style="#bfa669 bold", min_width=18)
    table.add_column("Status", style="white")

    def _yn(val):
        return "[#6fbf73]yes[/#6fbf73]" if val else "[#bf6f6f]no[/#bf6f6f]"

    table.add_row("iTerm2 detected", _yn(info["iterm_detected"]))
    table.add_row("Python API enabled", _yn(info["python_api_enabled"]))
    table.add_row("Daemon installed", _yn(info["daemon_installed"]))
    table.add_row("Venv exists", _yn(info["venv_exists"]))

    console.print()
    console.print(table)
    console.print()


# ── Substrate Album Commands ──────────────────────────────────────────────────


@main.group()
def substrate():
    """Substrate album engine — render, mix, and manage the 8-track concept album."""
    pass


@substrate.command(name="tracks")
def substrate_tracks_cmd():
    """List all 8 Substrate tracks with builder and render status."""
    from tools.substrate import substrate_list_tracks
    import json as _json

    data = _json.loads(substrate_list_tracks())
    table = Table(
        title="Substrate — Track Inventory",
        box=box.ROUNDED,
        border_style="#6fbf73",
        show_lines=True,
    )
    table.add_column("#", style="bold", width=3)
    table.add_column("Title", style="#bfa669 bold", min_width=20)
    table.add_column("Key", width=5)
    table.add_column("BPM", width=5)
    table.add_column("Dur", width=6)
    table.add_column("Inst", width=5)
    table.add_column("Builder", width=8)
    table.add_column("Rendered", width=10)
    for t in data["tracks"]:
        builder_ok = "[#6fbf73]✓[/]" if t["builder_exists"] else "[#bf6f6f]✗[/]"
        render_ok = "[#6fbf73]✓[/]" if t["rendered"] else "[dim]—[/]"
        table.add_row(
            str(t["track_number"]),
            t["title"],
            t["key"],
            str(t["bpm"]),
            t["duration"],
            str(t["instruments"]),
            builder_ok,
            render_ok,
        )
    console.print()
    console.print(table)
    console.print()


@substrate.command(name="render")
@click.argument("track", default="all")
@click.option(
    "--vocals/--no-vocals", default=False, help="Include ElevenLabs vocal generation"
)
def substrate_render_cmd(track, vocals):
    """Render a track (or 'all') using abletonctl builders."""
    from tools.substrate import substrate_render_track, substrate_render_album
    import json as _json

    console.print(
        f"\n[#bfa669 bold]Substrate Render[/] — {'full album' if track == 'all' else track}"
    )
    console.print(f"[dim]Mode: {'with vocals' if vocals else 'instrumental'}[/dim]\n")

    if track == "all":
        result = _json.loads(substrate_render_album(instrumental=not vocals))
        for t in result["tracks"]:
            status_icon = (
                "[#6fbf73]✓[/]" if t["status"] == "success" else "[#bf6f6f]✗[/]"
            )
            elapsed = f"{t.get('elapsed_seconds', 0):.0f}s"
            console.print(f"  {status_icon} {t['track']}  [dim]{elapsed}[/dim]")
        console.print(f"\n[dim]Total: {result['total_elapsed_seconds']:.0f}s[/dim]\n")
    else:
        result = _json.loads(substrate_render_track(track, instrumental=not vocals))
        if "error" in result:
            console.print(f"  [#bf6f6f]Error:[/] {result['error']}")
        else:
            console.print(f"  Status: {result['status']}")
            if result.get("master_mp3"):
                console.print(f"  Master: {result['master_mp3']}")
            console.print(f"  Elapsed: {result['elapsed_seconds']:.0f}s\n")


@substrate.command(name="status")
def substrate_status_cmd():
    """Check render status for all tracks."""
    from tools.substrate import substrate_render_status
    import json as _json

    data = _json.loads(substrate_render_status())
    table = Table(
        title="Substrate — Render Status",
        box=box.ROUNDED,
        border_style="#6fbf73",
    )
    table.add_column("#", width=3)
    table.add_column("Title", style="#bfa669 bold", min_width=18)
    table.add_column("WAV", width=5)
    table.add_column("MP3", width=5)
    table.add_column("Size", width=8)
    table.add_column("Stems", width=6)
    for t in data["tracks"]:
        wav_ok = "[#6fbf73]✓[/]" if t["has_master_wav"] else "[dim]—[/]"
        mp3_ok = "[#6fbf73]✓[/]" if t["has_master_mp3"] else "[dim]—[/]"
        size = f"{t['master_size_mb']} MB" if t["master_size_mb"] > 0 else "—"
        table.add_row(
            str(t["track_number"]),
            t["title"],
            wav_ok,
            mp3_ok,
            size,
            str(t["stem_count"]),
        )
    console.print()
    console.print(table)
    console.print()


@substrate.command(name="vocals")
def substrate_vocals_cmd():
    """Check vocal clip inventory."""
    from tools.substrate import substrate_vocal_status
    import json as _json

    data = _json.loads(substrate_vocal_status())
    console.print(
        f"\n[#bfa669 bold]Substrate Vocals[/] — {data['total_clips']} clips total\n"
    )
    for t in data["inventory"]:
        count = t["clip_count"]
        icon = "[#6fbf73]✓[/]" if count > 0 else "[dim]—[/]"
        console.print(f"  {icon} {t['track']}: {count} clips")
    console.print()


@substrate.command(name="open")
@click.argument("track", default="all")
def substrate_open_cmd(track):
    """Open a rendered track (or 'all') in the default audio player."""
    from tools.substrate import substrate_open_track
    import json as _json

    result = _json.loads(substrate_open_track(track))
    if "error" in result:
        console.print(f"  [#bf6f6f]{result['error']}[/]")
    elif "opened" in result and isinstance(result["opened"], list):
        console.print(f"\n  Opened {result['count']} tracks\n")
    else:
        console.print(f"\n  Opened {result['opened']}\n")


@substrate.command(name="dna")
def substrate_dna_cmd():
    """Display the Substrate DNA specification."""
    from tools.substrate import substrate_dna
    from rich.markdown import Markdown

    content = substrate_dna()
    if content.startswith("{"):
        console.print(f"  [#bf6f6f]{content}[/]")
    else:
        console.print(Markdown(content))


if __name__ == "__main__":
    main()
