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
    welcome_screen, show_welcome, show_inline_image, get_favicon,
    session_state, reset_session_state,
    render_tool_card, render_error as render_error_block, render_transcript_markdown,
    render_session_hud, render_recent_actions, record_action,
    show_retry_countdown, is_rate_limit_error,
    build_toolbar, SlashCompleter, create_chat_keybindings,
)
from config.agent_config import AGENT_VERSION, MODEL_CHAIN
from iterm import iterm_set_var, install_iterm, uninstall_iterm, iterm_status

console = Console()
AGENT_ID_FILE = os.path.join(PROJECT_ROOT, ".agent_id")

# Smart routing state — tracks current model position in the chain
_model_state = {
    "chain_index": 0,         # current position in MODEL_CHAIN
    "active_model": None,     # deployment name of the currently active model
    "failures": {},           # model_name -> consecutive failure count
    "agent_id": None,         # cached agent ID (may change on fallback)
    "openrouter_provider": None,  # OpenRouterProvider instance (reused for conversation)
}


def _current_model() -> dict:
    """Get the current model config from the chain."""
    idx = _model_state["chain_index"]
    if idx < len(MODEL_CHAIN):
        return MODEL_CHAIN[idx]
    return MODEL_CHAIN[0]  # wrap around to primary


def _advance_model() -> dict | None:
    """Move to the next model in the fallback chain. Returns new model or None if exhausted."""
    _model_state["chain_index"] += 1
    if _model_state["chain_index"] >= len(MODEL_CHAIN):
        _model_state["chain_index"] = 0  # reset to primary for next attempt
        return None
    return _current_model()


def _reset_model_chain():
    """Reset to the primary model (e.g., at session start)."""
    _model_state["chain_index"] = 0
    _model_state["failures"] = {}
    _model_state["nvidia_provider"] = None
    _model_state["openrouter_provider"] = None
    _model_state["ollama_provider"] = None
    _model_state["azure_openai_provider"] = None
    _model_state["anthropic_provider"] = None


def _get_openrouter_provider(model_cfg: dict):
    """Get or create an OpenRouterProvider for the given model."""
    from config.agent_config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, build_system_instructions
    from providers.openrouter import OpenRouterProvider

    model_name = model_cfg["name"]
    label = model_cfg["label"]
    current = _model_state.get("openrouter_provider")
    if current and current.model == model_name:
        return current
    if not OPENROUTER_API_KEY:
        raise RuntimeError(
            f"OpenRouter model '{label}' is missing credentials — "
            "set OPENROUTER_API_KEY in .env"
        )

    provider = OpenRouterProvider(
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        model=model_name,
        system_instructions=build_system_instructions(model_cfg),
        label=label,
    )
    _model_state["openrouter_provider"] = provider
    return provider


def _get_ollama_provider(model_cfg: dict):
    """Get or create an OllamaProvider for the given model."""
    from config.agent_config import OLLAMA_BASE_URL, build_system_instructions
    from providers.ollama import OllamaProvider

    model_name = model_cfg["name"]
    label = model_cfg["label"]
    current = _model_state.get("ollama_provider")
    if current and current.model == model_name:
        return current

    provider = OllamaProvider(
        model=model_name,
        system_instructions=build_system_instructions(model_cfg),
        base_url=OLLAMA_BASE_URL,
        label=label,
    )
    _model_state["ollama_provider"] = provider
    return provider


def _get_nvidia_provider(model_cfg: dict):
    """Get or create a NvidiaProvider for the given model."""
    from config.agent_config import NVIDIA_NIM_ENDPOINT, NVIDIA_API_KEY, build_system_instructions
    from providers.nvidia import NvidiaProvider

    model_name = model_cfg["name"]
    label = model_cfg["label"]
    current = _model_state.get("nvidia_provider")
    if current and current.model == model_name:
        return current
    if not NVIDIA_NIM_ENDPOINT or not NVIDIA_API_KEY:
        raise RuntimeError(
            f"NVIDIA model '{label}' is missing credentials — "
            "set NVIDIA_NIM_ENDPOINT and NVIDIA_API_KEY in .env"
        )

    provider = NvidiaProvider(
        model=model_name,
        system_instructions=build_system_instructions(model_cfg),
        endpoint=NVIDIA_NIM_ENDPOINT,
        api_key=NVIDIA_API_KEY,
        label=label,
    )
    _model_state["nvidia_provider"] = provider
    return provider


def _get_azure_openai_provider(model_cfg: dict):
    """
    Get or create an AzureOpenAIProvider for the given model config.

    Unlike the other providers (which share one endpoint for all models), each
    Azure model carries its own endpoint_env / api_key_env in the MODEL_CHAIN
    entry — so multiple Azure Foundry resources can coexist in the same chain.
    The provider is cached by (model, endpoint) since both determine identity.
    """
    from config.agent_config import build_system_instructions
    from providers.azure_openai import AzureOpenAIProvider, AzureResponsesProvider

    model_name = model_cfg["name"]
    label = model_cfg["label"]
    endpoint_var = model_cfg.get("endpoint_env", "AZURE_CORE_ENDPOINT")
    api_key_var = model_cfg.get("api_key_env", "AZURE_CORE_API_KEY")

    endpoint = os.environ.get(endpoint_var, "")
    api_key = os.environ.get(api_key_var, "")

    if not endpoint or not api_key:
        raise RuntimeError(
            f"Azure model '{label}' is missing credentials — "
            f"set {endpoint_var} and {api_key_var} in .env"
        )

    current = _model_state.get("azure_openai_provider")
    if (current and current.model == model_name
            and current.endpoint == endpoint):
        return current

    provider_cls = AzureResponsesProvider if model_cfg.get("surface") == "responses" else AzureOpenAIProvider
    provider = provider_cls(
        model=model_name,
        system_instructions=build_system_instructions(model_cfg),
        endpoint=endpoint,
        api_key=api_key,
        label=label,
    )
    _model_state["azure_openai_provider"] = provider
    return provider


def _get_anthropic_provider(model_cfg: dict):
    """
    Get or create an AnthropicProvider for the given model config.

    Uses Azure AI Foundry's native Anthropic API surface at /anthropic.
    Caches by (model, endpoint) like the Azure OpenAI provider.
    """
    from config.agent_config import build_system_instructions
    from providers.anthropic import AnthropicProvider

    model_name = model_cfg["name"]
    label = model_cfg["label"]
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
    if (current and current.model == model_name
            and current.endpoint == endpoint):
        return current

    provider = AnthropicProvider(
        model=model_name,
        system_instructions=build_system_instructions(model_cfg),
        endpoint=endpoint,
        api_key=api_key,
        label=label,
    )
    _model_state["anthropic_provider"] = provider
    return provider


def _is_model_error(error_str: str) -> bool:
    """Detect errors that indicate the model itself is failing (not user error)."""
    indicators = [
        "server_error", "Sorry, something went wrong",
        "InternalServerError", "502", "503", "504",
        "model_error", "overloaded", "capacity",
        "The server had an error", "run failed",
    ]
    lower = error_str.lower()
    return any(ind.lower() in lower for ind in indicators)


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
        json.dump({
            "agent_id": agent.id,
            "name": AGENT_NAME,
            "version": AGENT_VERSION,
            "model": model_name,
        }, f, indent=2)

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
            tools.append({
                "name": tc.function.name,
                "args": getattr(tc.function, "arguments", ""),
            })
        elif tc.type == "code_interpreter":
            tools.append({"name": "code_interpreter", "args": ""})
        else:
            tools.append({"name": tc.type, "args": ""})
    return tools


def _render_tool_card_old(tool_info: dict):
    """Legacy tool card — replaced by hybrid cards from branding module."""
    render_tool_card(console, tool_info["name"], tool_info.get("args", ""), status="running")


def _render_error(msg: str, title: str = "Error"):
    """Print a styled error panel."""
    console.print()
    render_error_block(console, title, msg)


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
            templates_dir=os.path.join(PROJECT_ROOT, "crowe_synapse_engine", "templates"),
        )
    return _orchestrator


def _execute_tool_call(tool_map: dict, name: str, arguments_json: str) -> str:
    """Execute a single tool function by name and return the result as a string."""
    func = tool_map.get(name)
    if not func:
        return json.dumps({"error": f"Unknown tool: {name}"})
    try:
        args = json.loads(arguments_json) if isinstance(arguments_json, str) else arguments_json
        result = func(**args)
        return str(result) if result is not None else ""
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def stream_response(client, thread_id: str, agent_id: str):
    from azure.ai.agents.models import (
        MessageDeltaChunk, ThreadRun, RunStep, AgentStreamEvent, ToolOutput,
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
                    if event_data.type == "tool_calls" and event_data.status == "in_progress":
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
            _render_error(str(getattr(run, "last_error", run.status)), f"Run {run.status.title()}")
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
                    console, tool_name, tc.function.arguments,
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
            client.runs.submit_tool_outputs(thread_id=thread_id, run_id=run_id, tool_outputs=tool_outputs)
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
            _render_error(str(getattr(run, "last_error", run.status)), f"Run {run.status.title()}")
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
                        full_text = f"{full_text}\n\n{final_text}".strip() if full_text.strip() else final_text
                        render_transcript_markdown(console, final_text, title="answer", meta="final")
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

    def _active_thread_id() -> str:
        t = azure_state["thread"]
        return t.id if t is not None else synthetic_thread_id

    orch = _get_orchestrator()
    session_id = orch.start_session(thread_id=synthetic_thread_id)
    reset_session_state()
    _reset_model_chain()
    iterm_set_var("crowe_logic_active", "1")
    session_state["active_model"] = _current_model()["label"]

    show_welcome(AGENT_VERSION)

    history_file = os.path.join(PROJECT_ROOT, ".chat_history")
    kb = create_chat_keybindings()
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

            user_input = session.prompt(prompt_html, multiline=False)
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
            render_session_hud(console, state=session_state, cwd=os.getcwd(), meta="ready")
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
        if user_input.lower().startswith("/model"):
            parts = user_input.strip().split(maxsplit=1)
            if len(parts) == 1:
                # Show current model and available chain
                _show_models()
            else:
                # Switch to specified model. Strip surrounding angle brackets,
                # quotes, and whitespace — users often type the literal `<2>`
                # placeholder syntax from the help hint.
                target = parts[1].strip().strip("<>").strip("'\"").strip()
                _switch_model(azure_state, target)
            continue

        try:
            model_cfg = _current_model()
            ctx = orch.prepare(user_input, thread_id=_active_thread_id())
            render_session_hud(console, state=session_state, cwd=os.getcwd(), meta="turn")
            console.print()

            # Smart routing: try current model, fallback on failure
            succeeded = False
            while not succeeded:
                model_cfg = _current_model()
                last_error = None

                for attempt in range(2):
                    try:
                        if model_cfg.get("provider") == "azure_openai":
                            # ── Crowe Logic's own Azure Foundry (OpenAI-compat, key auth) ──
                            provider = _get_azure_openai_provider(model_cfg)
                            provider.add_user_message(user_input)
                            provider.stream_response(
                                console, render_tool_card, session_state, _get_orchestrator,
                            )
                        elif model_cfg.get("provider") == "anthropic":
                            # ── Azure AI Foundry Anthropic (native Anthropic API) ──
                            provider = _get_anthropic_provider(model_cfg)
                            provider.add_user_message(user_input)
                            provider.stream_response(
                                console, render_tool_card, session_state, _get_orchestrator,
                            )
                        elif model_cfg.get("provider") == "nvidia":
                            # ── NVIDIA NIM path (production CroweLM) ──
                            provider = _get_nvidia_provider(model_cfg)
                            provider.add_user_message(user_input)
                            provider.stream_response(
                                console, render_tool_card, session_state, _get_orchestrator,
                            )
                        elif model_cfg.get("provider") == "openrouter":
                            # ── OpenRouter path (Chat Completions) ──
                            provider = _get_openrouter_provider(model_cfg)
                            provider.add_user_message(user_input)
                            provider.stream_response(
                                console, render_tool_card, session_state, _get_orchestrator,
                            )
                        elif model_cfg.get("provider") == "ollama":
                            # ── Ollama path (local CroweLM fallback) ──
                            provider = _get_ollama_provider(model_cfg)
                            provider.add_user_message(user_input)
                            provider.stream_response(
                                console, render_tool_card, session_state, _get_orchestrator,
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
                                    thread_id=thread.id, role="user", content=user_input,
                                )
                            stream_response(client, thread.id, agent_id)

                        session_state["api_status"] = "ok"
                        iterm_set_var("crowe_logic_api", "ok")
                        session_state["active_model"] = model_cfg["label"]
                        iterm_set_var("crowe_logic_model", model_cfg["label"])
                        succeeded = True
                        break
                    except Exception as stream_err:
                        error_msg = str(stream_err)
                        if is_rate_limit_error(error_msg):
                            last_error = error_msg
                            if attempt < 1:
                                wait = 3
                                show_retry_countdown(console, wait, attempt + 2, 2)
                                if (model_cfg.get("provider") == "azure"
                                        and azure_state["client"] is not None
                                        and azure_state["thread"] is not None):
                                    _cancel_active_runs(
                                        azure_state["client"], azure_state["thread"].id
                                    )
                                continue
                        elif _is_model_error(error_msg):
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
                next_model = _advance_model()
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
                        console.print(f"  [dim red]Fallback deploy failed: {_rich_escape(str(deploy_err))}[/dim red]")
                        continue

            console.print(f"  [dim #bfa669]{'─' * min(60, console.width)}[/dim #bfa669]")
        except Exception as e:
            error_msg = str(e)
            if (azure_state["client"] is not None
                    and azure_state["thread"] is not None
                    and "while a run" in error_msg and "is active" in error_msg):
                client = azure_state["client"]
                thread = azure_state["thread"]
                agent_id = azure_state["agent_id"]
                _cancel_active_runs(client, thread.id)
                console.print("  [dim]Cancelled stale run — retrying...[/dim]")
                time.sleep(1)
                try:
                    stream_response(client, thread.id, agent_id)
                    console.print(f"  [dim #bfa669]{'─' * min(60, console.width)}[/dim #bfa669]")
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

    table.add_row("[dim]code_interpreter[/dim]", "[dim]Run Python in sandbox (Azure built-in)[/dim]")
    console.print()
    console.print(table)
    console.print()


def _show_status_inline():
    model_cfg = _current_model()
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

    # CroweLM training data summary
    try:
        manifest_path = os.path.join(PROJECT_ROOT, "data", "crowelm-unified", "DATASET_MANIFEST.json")
        if os.path.exists(manifest_path):
            with open(manifest_path) as f:
                manifest = json.load(f)
            summary = manifest.get("summary", {})
            table.add_row("", "")
            table.add_row("[dim]Training Data[/dim]", "")
            table.add_row("Raw Samples", f"{summary.get('total_raw_samples', 0):,}")
            table.add_row("Training Entries", f"{summary.get('crowelm_training_entries', 0):,}")
            table.add_row("Dataset Size", f"{summary.get('total_size_gb', 0):.2f} GB")
            table.add_row("Domains", summary.get("domains", ""))
    except Exception:
        pass

    # Model chain overview
    table.add_row("", "")
    table.add_row("[dim]CroweLM Models[/dim]", "")
    for i, m in enumerate(MODEL_CHAIN):
        marker = "[bold #6fbf73]>[/bold #6fbf73] " if i == _model_state["chain_index"] else "  "
        status_note = _model_status_note(m)
        fail_str = f"  [#bf6f6f]({status_note})[/#bf6f6f]" if status_note else ""
        table.add_row(f"{marker}{m['label']}", f"{m.get('type', 'general')}{fail_str}")

    console.print()
    console.print(table)
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
            else ("[bold #6fbf73]ACTIVE[/bold #6fbf73]" if is_active else "[dim]standby[/dim]")
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

    if provider == "azure_openai":
        endpoint_var = model_cfg.get("endpoint_env", "AZURE_CORE_ENDPOINT")
        api_key_var = model_cfg.get("api_key_env", "AZURE_CORE_API_KEY")
        missing = [
            var for var in (endpoint_var, api_key_var)
            if not os.environ.get(var, "").strip()
        ]
        if missing:
            return (
                f"Cannot switch to {label} — missing "
                + ", ".join(missing)
                + " in .env"
            )

    if provider == "anthropic":
        endpoint_var = model_cfg.get("endpoint_env", "AZURE_ANTHROPIC_ENDPOINT")
        api_key_var = model_cfg.get("api_key_env", "AZURE_ANTHROPIC_API_KEY")
        missing = [
            var for var in (endpoint_var, api_key_var)
            if not os.environ.get(var, "").strip()
        ]
        if missing:
            return (
                f"Cannot switch to {label} — missing "
                + ", ".join(missing)
                + " in .env"
            )

    if provider == "nvidia":
        missing = [
            var for var in ("NVIDIA_NIM_ENDPOINT", "NVIDIA_API_KEY")
            if not os.environ.get(var, "").strip()
        ]
        if missing:
            return (
                f"Cannot switch to {label} — missing "
                + ", ".join(missing)
                + " in .env"
            )

    if provider == "openrouter" and not os.environ.get("OPENROUTER_API_KEY", "").strip():
        return f"Cannot switch to {label} — missing OPENROUTER_API_KEY in .env"

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
        if provider in ("anthropic", "azure_openai", "nvidia", "openrouter", "ollama"):
            # Reset cached provider so the next turn rebuilds with the new model
            cache_key = f"{provider}_provider"
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
                console.print(f"  [red]Failed to activate {model['label']}: {_rich_escape(str(e))}[/red]")
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
    manifest_path = os.path.join(PROJECT_ROOT, "data", "crowelm-unified", "DATASET_MANIFEST.json")
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
    header.add_row("Training Entries", f"{summary.get('crowelm_training_entries', 0):,}")
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

        sorted_domains = sorted(top_domains.items(), key=lambda x: x[1], reverse=True)[:10]
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
    table.add_row("/data", "CroweLM training data telemetry")
    table.add_row("/status", "Show agent info")
    table.add_row("/clear", "Clear screen")
    table.add_row("/help", "Show this help")
    table.add_row("/exit", "Quit")
    table.add_row("", "")
    table.add_row("[dim]Ctrl+E[/dim]", "[dim]Multi-line editor[/dim]")
    table.add_row("[dim]Tab[/dim]", "[dim]Complete / commands[/dim]")
    console.print()
    console.print(table)
    console.print()


@main.command()
@click.argument("prompt")
def run(prompt: str):
    """Run a single prompt and print the response."""
    # Route through the primary model in the chain.
    # No Azure Agents thread/client needed unless the chain falls through to
    # the legacy "azure" provider.
    reset_session_state()
    _reset_model_chain()
    model_cfg = _current_model()
    favicon = get_favicon()
    session_state["favicon"] = favicon
    session_state["active_model"] = model_cfg["label"]
    orch = _get_orchestrator()
    import uuid
    synthetic_thread_id = f"local-{uuid.uuid4().hex[:16]}"
    orch.start_session(thread_id=synthetic_thread_id)
    orch.prepare(prompt, thread_id=synthetic_thread_id)
    render_session_hud(console, state=session_state, cwd=os.getcwd(), meta="run")
    console.print()

    provider_kind = model_cfg.get("provider")
    try:
        if provider_kind == "anthropic":
            provider = _get_anthropic_provider(model_cfg)
        elif provider_kind == "azure_openai":
            provider = _get_azure_openai_provider(model_cfg)
        elif provider_kind == "nvidia":
            provider = _get_nvidia_provider(model_cfg)
        elif provider_kind == "openrouter":
            provider = _get_openrouter_provider(model_cfg)
        elif provider_kind == "ollama":
            provider = _get_ollama_provider(model_cfg)
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
            console, render_tool_card, session_state, _get_orchestrator,
        )
    except Exception as e:
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


@main.command()
def deploy():
    """Verify all CroweLM providers and run health checks."""
    from openai import OpenAI
    from config.agent_config import (
        MODEL_CHAIN, OLLAMA_BASE_URL, NVIDIA_NIM_ENDPOINT, NVIDIA_API_KEY,
        OPENROUTER_API_KEY, OPENROUTER_BASE_URL, NEON_DATABASE_URL,
        AGENT_NAME, AGENT_VERSION,
        AZURE_CORE_ENDPOINT, AZURE_CORE_API_KEY,
        AZURE_GLM_ENDPOINT, AZURE_GLM_API_KEY,
        AZURE_ANTHROPIC_ENDPOINT, AZURE_ANTHROPIC_API_KEY,
    )
    import requests

    console.print(f"\n{'='*60}")
    console.print(f"  CROWE LOGIC — DEPLOY HEALTH CHECK")
    console.print(f"  {AGENT_NAME} v{AGENT_VERSION}")
    console.print(f"  request timeout {int(_deploy_timeout_seconds())}s")
    console.print(f"{'='*60}\n")

    test_msg = [
        {"role": "system", "content": "You are helpful. Be brief."},
        {"role": "user", "content": "Reply with exactly: OK"},
    ]

    def _token_limit_kwargs(model_name: str) -> dict:
        """
        GPT-5 and o-series reasoning models reject `max_tokens` and require
        `max_completion_tokens`. Everything else still uses `max_tokens`.
        """
        lname = model_name.lower()
        if lname.startswith(("gpt-5", "o1", "o3", "o4")) or "gpt-5" in lname:
            return {"max_completion_tokens": 50}
        return {"max_tokens": 50}

    results = []
    timeout_seconds = _deploy_timeout_seconds()

    for model in MODEL_CHAIN:
        name = model["name"]
        label = model["label"]
        provider = model.get("provider", "unknown")
        status = "skip"
        latency = 0

        try:
            import time
            start = time.monotonic()

            if provider == "azure_openai":
                endpoint_var = model.get("endpoint_env", "AZURE_CORE_ENDPOINT")
                api_key_var = model.get("api_key_env", "AZURE_CORE_API_KEY")
                endpoint = os.environ.get(endpoint_var, "")
                api_key = os.environ.get(api_key_var, "")
                if not endpoint or not api_key:
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
                    if model.get("surface") == "responses":
                        resp = client.responses.create(
                            model=name,
                            input="Reply with exactly: OK",
                            max_output_tokens=50,
                        )
                        status = "live" if getattr(resp, "output_text", "").strip() else "empty"
                    else:
                        resp = client.chat.completions.create(
                            model=name, messages=test_msg, **_token_limit_kwargs(name),
                        )
                        status = "live" if resp.choices else "empty"

            elif provider == "anthropic":
                from anthropic import Anthropic

                endpoint_var = model.get("endpoint_env", "AZURE_ANTHROPIC_ENDPOINT")
                api_key_var = model.get("api_key_env", "AZURE_ANTHROPIC_API_KEY")
                endpoint = os.environ.get(endpoint_var, "")
                api_key = os.environ.get(api_key_var, "")
                if not endpoint or not api_key:
                    status = "no credentials"
                else:
                    base_url = endpoint.rstrip("/")
                    if not base_url.endswith("/anthropic"):
                        base_url += "/anthropic"
                    client = Anthropic(api_key=api_key, base_url=base_url, timeout=timeout_seconds)
                    resp = client.messages.create(
                        model=name,
                        max_tokens=50,
                        messages=[{"role": "user", "content": "Reply with exactly: OK"}],
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
                        model=name, messages=test_msg, **_token_limit_kwargs(name),
                    )
                    content = (resp.choices[0].message.content or "").strip()
                    status = "live" if resp.choices else "empty"

            elif provider == "ollama":
                client = OpenAI(
                    api_key="ollama",
                    base_url=OLLAMA_BASE_URL,
                    timeout=timeout_seconds,
                    max_retries=0,
                )
                resp = client.chat.completions.create(
                    model=name, messages=test_msg, max_tokens=50,
                )
                content = (resp.choices[0].message.content or "").strip()
                status = "live" if resp.choices else "empty"

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
                        model=name, messages=test_msg, **_token_limit_kwargs(name),
                    )
                    content = (resp.choices[0].message.content or "").strip()
                    status = "live" if resp.choices else "empty"

            else:
                status = "unknown provider"

            latency = int((time.monotonic() - start) * 1000)

        except Exception as e:
            status = _classify_deploy_error(e)

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
        elif status in ("no credentials", "no key", "auth failed"):
            status_fmt = f"[yellow]{status}[/yellow]"
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
                console.print("  [#6fbf73]Neon Postgres[/#6fbf73]  [dim]connected[/dim]")
            else:
                console.print(f"  [#bf6f6f]Neon Postgres[/#bf6f6f]  [dim]HTTP {resp.status_code}[/dim]")
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
    total = len(results)
    console.print(f"\n  {live_count}/{total} models online")

    if live_count > 0:
        console.print(f"\n{'='*60}")
        console.print(f"  READY -- Run: crowe-logic chat")
        console.print(f"{'='*60}\n")
    else:
        console.print(f"\n  [bold red]No models available. Check credentials and connectivity.[/bold red]\n")


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
        raise click.UsageError("Provide either --input or both --account and --resource-group")

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
    console.print("  [dim]The updated registry will be picked up on the next crowe-logic run.[/dim]\n")


@main.command()
def status():
    """Show current agent status."""
    _show_status_inline()


@main.command()
def tools():
    """List all available tools."""
    _list_tools_inline()


@main.command(name="headless")
@click.option("--input", "input_path", help="Read headless JSON input from this file instead of stdin")
@click.option("--model", default="auto", show_default=True,
              help="Model id from MODEL_CHAIN to run in headless mode")
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
    console.print(f"  [#bfa669]Resuming session:[/#bfa669] {last.get('summary', 'no summary')}")
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

    history_file = os.path.join(PROJECT_ROOT, ".chat_history")
    kb = create_chat_keybindings()
    session = PromptSession(
        history=FileHistory(history_file),
        completer=SlashCompleter(),
        key_bindings=kb,
        bottom_toolbar=build_toolbar,
    )
    prompt_html = HTML('<style fg="#bfa669">\u276f </style>')
    favicon = get_favicon()
    session_state["favicon"] = favicon
    session_state["active_model"] = "crowe-logic"
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

            user_input = session.prompt(prompt_html, multiline=False)
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
        try:
            _cancel_active_runs(client, thread_id)
            client.messages.create(thread_id=thread_id, role="user", content=user_input)
            render_session_hud(console, state=session_state, cwd=os.getcwd(), meta="turn")
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
            if last_error:
                session_state["api_status"] = "down"
                _render_error(last_error, "Run Failed (after 3 attempts)")

            console.print(f"  [dim #bfa669]{'─' * min(60, console.width)}[/dim #bfa669]")
        except Exception as e:
            error_msg = str(e)
            if "while a run" in error_msg and "is active" in error_msg:
                _cancel_active_runs(client, thread_id)
                console.print("  [dim]Cancelled stale run — retrying...[/dim]")
                time.sleep(1)
                try:
                    stream_response(client, thread_id, agent_id)
                    console.print(f"  [dim #bfa669]{'─' * min(60, console.width)}[/dim #bfa669]")
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
            console.print("  [dim]Enable at: Preferences > General > Magic > Enable Python API[/dim]\n")


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


if __name__ == "__main__":
    main()
