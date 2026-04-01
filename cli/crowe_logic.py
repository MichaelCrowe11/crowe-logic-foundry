#!/usr/bin/env python3
"""
Crowe Logic CLI — Universal AI Agent

Usage:
    crowe-logic                       # Interactive chat (default)
    crowe-logic chat                  # Interactive chat session
    crowe-logic run "your prompt"     # Single prompt, get response
    crowe-logic deploy                # Create/recreate the agent
    crowe-logic status                # Show agent status
    crowe-logic tools                 # List available tools
"""

import os
import sys
import json
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import click
from rich.console import Console
from rich.table import Table
from rich.text import Text
from rich.markdown import Markdown
from rich.panel import Panel
from rich import box

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from cli.branding import (
    welcome_screen, show_welcome, show_inline_image, get_favicon,
    session_state, reset_session_state,
    render_tool_card, summarize_tool_result,
    show_retry_countdown, is_rate_limit_error,
    build_toolbar, SlashCompleter, create_chat_keybindings,
)
from config.agent_config import AGENT_VERSION

console = Console()
AGENT_ID_FILE = os.path.join(PROJECT_ROOT, ".agent_id")


def get_agent_id() -> str:
    if not os.path.exists(AGENT_ID_FILE):
        console.print("\n  [bold red]No agent found.[/bold red] Run: [bold]crowe-logic deploy[/bold]\n")
        sys.exit(1)
    try:
        with open(AGENT_ID_FILE) as f:
            return json.load(f)["agent_id"]
    except (json.JSONDecodeError, KeyError):
        console.print("\n  [bold red]Corrupt agent file.[/bold red] Run: [bold]crowe-logic deploy[/bold]\n")
        sys.exit(1)


def get_client():
    from azure.ai.agents import AgentsClient
    from azure.identity import DefaultAzureCredential
    from config.agent_config import PROJECT_ENDPOINT
    return AgentsClient(endpoint=PROJECT_ENDPOINT, credential=DefaultAzureCredential())


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
    console.print(Panel(
        f"[white]{msg}[/white]",
        title=f"[bold]{title}[/bold]",
        border_style="red",
        padding=(0, 1),
    ))


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
        from crowe_synapse import Orchestrator
        _orchestrator = Orchestrator(
            db_path=os.path.expanduser("~/.crowe-logic/memory.db"),
            agents_dir=os.path.join(PROJECT_ROOT, "agents"),
            templates_dir=os.path.join(PROJECT_ROOT, "crowe_synapse", "templates"),
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
    from rich.spinner import Spinner
    from rich.live import Live

    tool_map = _get_tool_map()
    text_chunks = []
    tool_calls_shown = set()
    spinner = None
    spin_live = None
    md_live = None
    streaming_started = False

    run_id = None
    pending_tool_calls = None

    def _start_spinner(label: str):
        nonlocal spinner, spin_live
        _stop_spinner()
        spinner = Spinner("dots", text=f"  [#bfa669]{label}[/#bfa669]", style="#bfa669")
        spin_live = Live(spinner, console=console, refresh_per_second=12, transient=True)
        spin_live.start()

    def _stop_spinner():
        nonlocal spinner, spin_live
        if spin_live:
            spin_live.stop()
            spin_live = None
            spinner = None

    def _stop_md_live():
        nonlocal md_live
        if md_live:
            # Final render with complete text
            full = "".join(text_chunks)
            if full.strip():
                md_live.update(Markdown(full))
            md_live.stop()
            md_live = None

    # ── Phase 1: Streaming with Live Markdown ─────────────────
    try:
        _start_spinner("thinking...")

        with client.runs.stream(thread_id=thread_id, agent_id=agent_id) as stream:
            for event_type, event_data, _ in stream:
                if isinstance(event_data, MessageDeltaChunk):
                    if not streaming_started:
                        _stop_spinner()
                        streaming_started = True
                        md_live = Live(
                            Markdown(""),
                            console=console,
                            refresh_per_second=8,
                            vertical_overflow="visible",
                        )
                        md_live.start()
                    if event_data.text:
                        text_chunks.append(event_data.text)
                        md_live.update(Markdown("".join(text_chunks)))

                elif isinstance(event_data, ThreadRun):
                    run_id = event_data.id
                    if event_data.status == "requires_action":
                        _stop_md_live()
                        _stop_spinner()
                        pending_tool_calls = (
                            event_data.required_action.submit_tool_outputs.tool_calls
                        )
                    elif event_data.status == "failed":
                        _stop_md_live()
                        _stop_spinner()
                        err_str = str(event_data.last_error)
                        if is_rate_limit_error(err_str):
                            session_state["api_status"] = "throttled"
                        _render_error(err_str, "Run Failed")
                    elif event_data.status in ("cancelled", "expired"):
                        _stop_md_live()
                        _stop_spinner()
                        _render_error(f"Run {event_data.status}.", "Run Stopped")

                elif isinstance(event_data, RunStep):
                    step_id = getattr(event_data, "id", None)
                    if event_data.type == "tool_calls" and event_data.status == "in_progress":
                        if step_id not in tool_calls_shown:
                            tool_calls_shown.add(step_id)
                            tools = _extract_tool_info(event_data.step_details)
                            _stop_md_live()
                            _stop_spinner()
                            # Only show spinner — Phase 2 renders the full hybrid cards
                            names = [t["name"] for t in tools] if tools else ["tools"]
                            _start_spinner(f"running {', '.join(names)}...")
                    elif event_data.status == "completed":
                        _stop_spinner()

                elif event_type == AgentStreamEvent.ERROR:
                    _stop_md_live()
                    _stop_spinner()
                    _render_error(str(event_data))

                elif event_type == AgentStreamEvent.DONE:
                    break
    finally:
        _stop_md_live()
        _stop_spinner()

    # ── Phase 2: Tool execution loop ──────────────────────────
    def _poll_run(rid):
        r = client.runs.get(thread_id=thread_id, run_id=rid)
        while r.status in ("queued", "in_progress"):
            time.sleep(0.5)
            r = client.runs.get(thread_id=thread_id, run_id=rid)
        return r

    tool_phase_ok = True
    while pending_tool_calls and run_id:
        _start_spinner("preparing tools...")
        try:
            run = _poll_run(run_id)
        except Exception as e:
            _stop_spinner()
            _render_error(str(e), "Run Status Error")
            tool_phase_ok = False
            break
        _stop_spinner()

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
                _start_spinner(f"running {tc.function.name}...")
                _tool_start = time.monotonic()
                result = _execute_tool_call(tool_map, tc.function.name, tc.function.arguments)
                duration_ms = int((time.monotonic() - _tool_start) * 1000)
                _stop_spinner()

                # Render completed hybrid card (single display per tool)
                failed = result.startswith('{"error"')
                render_tool_card(
                    console, tc.function.name, tc.function.arguments,
                    status="fail" if failed else "ok",
                    result=result,
                    duration_ms=duration_ms,
                )
                session_state["tool_count"] += 1

                _get_orchestrator().record_execution(
                    tool_name=tc.function.name,
                    arguments=tc.function.arguments,
                    output=result[:10000],
                    duration_ms=duration_ms,
                )
                tool_outputs.append(ToolOutput(tool_call_id=tc.id, output=result))

        _start_spinner("thinking...")
        try:
            client.runs.submit_tool_outputs(thread_id=thread_id, run_id=run_id, tool_outputs=tool_outputs)
            run = _poll_run(run_id)
        except Exception as e:
            _stop_spinner()
            _render_error(str(e), "Tool Submit Failed")
            tool_phase_ok = False
            break
        _stop_spinner()

        if run.status == "requires_action":
            pending_tool_calls = run.required_action.submit_tool_outputs.tool_calls
            continue
        elif run.status == "completed":
            break
        else:
            _render_error(str(getattr(run, "last_error", run.status)), f"Run {run.status.title()}")
            tool_phase_ok = False
            break

    # ── Phase 3: Post-tool response ───────────────────────────
    full_text = "".join(text_chunks)
    if not full_text.strip() and run_id and tool_phase_ok:
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
                        full_text = "\n\n".join(parts)
                        console.print(Markdown(full_text), highlight=False)
                    break
        except Exception:
            pass

    console.print()
    return full_text


@click.group(invoke_without_command=True)
@click.version_option(version=AGENT_VERSION, prog_name="crowe-logic")
@click.pass_context
def main(ctx):
    """Crowe Logic — Universal AI Agent powered by gpt-oss-120b"""
    if ctx.invoked_subcommand is None:
        ctx.invoke(chat)


@main.command()
def chat():
    """Start an interactive chat session with the agent."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.formatted_text import HTML

    agent_id = get_agent_id()
    client = get_client()
    thread = client.threads.create()

    orch = _get_orchestrator()
    session_id = orch.start_session(thread_id=thread.id)
    reset_session_state()

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

    while True:
        try:
            user_input = session.prompt(prompt_html, multiline=False)
        except (EOFError, KeyboardInterrupt):
            orch.end_session(summary="Session ended by user")
            console.print("\n  [bold #bfa669]Goodbye.[/bold #bfa669]\n")
            break

        user_input = user_input.strip()
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "/exit", "/quit"):
            orch.end_session(summary="Session ended by user")
            console.print("  [bold #bfa669]Goodbye.[/bold #bfa669]\n")
            break

        if user_input.lower() == "/tools":
            _list_tools_inline()
            continue
        if user_input.lower() == "/clear":
            console.clear()
            show_welcome(AGENT_VERSION)
            continue
        if user_input.lower() == "/status":
            _show_status_inline()
            continue
        if user_input.lower() == "/help":
            _show_help()
            continue

        try:
            ctx = orch.prepare(user_input, thread_id=thread.id)
            _cancel_active_runs(client, thread.id)
            client.messages.create(thread_id=thread.id, role="user", content=user_input)
            console.print()
            sys.stdout.write(f"  {favicon} ")
            sys.stdout.flush()
            console.print("[bold #bfa669]crowe-logic[/bold #bfa669]")

            # Retry with countdown bar on rate limits
            last_error = None
            for attempt in range(3):
                try:
                    stream_response(client, thread.id, agent_id)
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
                            _cancel_active_runs(client, thread.id)
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
    if not os.path.exists(AGENT_ID_FILE):
        console.print("  [dim]No agent deployed[/dim]")
        return
    with open(AGENT_ID_FILE) as f:
        data = json.load(f)

    table = Table(
        title="Agent Status",
        box=box.ROUNDED,
        border_style="#bfa669",
        title_style="bold #bfa669",
        show_header=False,
        padding=(0, 1),
    )
    table.add_column("Key", style="#bfa669 bold", min_width=10)
    table.add_column("Value", style="white")
    table.add_row("Agent ID", data.get("agent_id", "unknown"))
    table.add_row("Name", data.get("name", "unknown"))
    table.add_row("Model", data.get("model", "unknown"))
    table.add_row("Version", data.get("version", "unknown"))
    console.print()
    console.print(table)
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
    table.add_column("Command", style="#bfa669 bold", min_width=12)
    table.add_column("Action", style="white")
    table.add_row("/tools", "List available tools")
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
    agent_id = get_agent_id()
    client = get_client()
    thread = client.threads.create()
    client.messages.create(thread_id=thread.id, role="user", content=prompt)
    stream_response(client, thread.id, agent_id)


@main.command()
@click.option("--name", default="crowe-logic", help="Agent name")
def deploy(name: str):
    """Create or recreate the Crowe Logic agent."""
    from scripts.create_agent import create_agent
    create_agent(name=name, verbose=True)


@main.command()
def status():
    """Show current agent status."""
    _show_status_inline()


@main.command()
def tools():
    """List all available tools."""
    _list_tools_inline()


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

    agent_id = get_agent_id()
    client = get_client()
    orch.start_session(thread_id=thread_id)
    reset_session_state()

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

    while True:
        try:
            user_input = session.prompt(prompt_html, multiline=False)
        except (EOFError, KeyboardInterrupt):
            orch.end_session(summary="Resumed session ended by user")
            console.print("\n  [bold #bfa669]Goodbye.[/bold #bfa669]\n")
            break
        user_input = user_input.strip()
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "/exit", "/quit"):
            orch.end_session(summary="Resumed session ended by user")
            console.print("  [bold #bfa669]Goodbye.[/bold #bfa669]\n")
            break
        try:
            _cancel_active_runs(client, thread_id)
            client.messages.create(thread_id=thread_id, role="user", content=user_input)
            console.print()
            sys.stdout.write(f"  {favicon} ")
            sys.stdout.flush()
            console.print("[bold #bfa669]crowe-logic[/bold #bfa669]")

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


if __name__ == "__main__":
    main()
