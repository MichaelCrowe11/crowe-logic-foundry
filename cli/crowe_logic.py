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

from cli.branding import welcome_screen, show_welcome, show_inline_image, get_favicon
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


def setup_toolset(client):
    from azure.ai.agents.models import FunctionTool, ToolSet, CodeInterpreterTool
    from tools import user_functions
    toolset = ToolSet()
    toolset.add(FunctionTool(user_functions))
    toolset.add(CodeInterpreterTool())
    client.enable_auto_function_calls(toolset)
    return toolset


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


def _render_tool_card(tool_info: dict):
    """Print a styled tool call indicator."""
    name = tool_info["name"]
    args = tool_info.get("args", "")

    # Truncate long args for display
    if args and len(args) > 80:
        args = args[:77] + "..."

    label = Text()
    label.append("  > ", style="dim #bfa669")
    label.append(name, style="bold #bfa669")
    if args:
        label.append(f"  {args}", style="dim")
    console.print(label)


def _render_error(msg: str, title: str = "Error"):
    """Print a styled error panel."""
    console.print()
    console.print(Panel(
        f"[white]{msg}[/white]",
        title=f"[bold]{title}[/bold]",
        border_style="red",
        padding=(0, 1),
    ))


def _build_tool_map():
    """Build a name -> function lookup from registered user_functions."""
    from tools import user_functions
    return {f.__name__: f for f in user_functions}


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

    tool_map = _build_tool_map()
    full_text = ""
    tool_calls_shown = set()
    spinner = None
    live = None
    streaming_started = False

    # Track requires_action state from the stream
    run_id = None
    pending_tool_calls = None  # set when run enters requires_action

    def _start_spinner(label: str):
        nonlocal spinner, live
        _stop_spinner()
        spinner = Spinner("dots", text=f"  [#bfa669]{label}[/#bfa669]", style="#bfa669")
        live = Live(spinner, console=console, refresh_per_second=12, transient=True)
        live.start()

    def _stop_spinner():
        nonlocal spinner, live
        if live:
            live.stop()
            live = None
            spinner = None

    # ── Phase 1: Initial streaming run ────────────────────────
    # Use a Live display so Markdown renders progressively as text arrives.
    text_live = None  # Rich Live for streaming text

    def _start_text_live():
        nonlocal text_live
        if text_live is None:
            text_live = Live(Markdown(""), console=console, refresh_per_second=8, vertical_overflow="visible")
            text_live.start()

    def _stop_text_live():
        nonlocal text_live
        if text_live:
            # Final render with complete text
            if full_text.strip():
                text_live.update(Markdown(full_text.strip()))
            text_live.stop()
            text_live = None

    try:
        _start_spinner("thinking...")

        with client.runs.stream(thread_id=thread_id, agent_id=agent_id) as stream:
            for event_type, event_data, _ in stream:
                if isinstance(event_data, MessageDeltaChunk):
                    if not streaming_started:
                        _stop_spinner()
                        streaming_started = True
                        _start_text_live()
                    if event_data.text:
                        full_text += event_data.text
                        if text_live:
                            text_live.update(Markdown(full_text.strip()))

                elif isinstance(event_data, ThreadRun):
                    run_id = event_data.id
                    if event_data.status == "requires_action":
                        _stop_text_live()
                        _stop_spinner()
                        pending_tool_calls = (
                            event_data.required_action.submit_tool_outputs.tool_calls
                        )
                    elif event_data.status == "failed":
                        _stop_text_live()
                        _stop_spinner()
                        _render_error(str(event_data.last_error), "Run Failed")
                    elif event_data.status in ("cancelled", "expired"):
                        _stop_text_live()
                        _stop_spinner()
                        _render_error(f"Run {event_data.status}.", "Run Stopped")

                elif isinstance(event_data, RunStep):
                    step_id = getattr(event_data, "id", None)
                    if event_data.type == "tool_calls" and event_data.status == "in_progress":
                        if step_id not in tool_calls_shown:
                            tool_calls_shown.add(step_id)
                            tools = _extract_tool_info(event_data.step_details)
                            _stop_text_live()
                            _stop_spinner()
                            for t in tools:
                                _render_tool_card(t)
                            names = [t["name"] for t in tools] if tools else ["tools"]
                            _start_spinner(f"running {', '.join(names)}...")
                    elif event_data.status == "completed":
                        _stop_spinner()

                elif event_type == AgentStreamEvent.ERROR:
                    _stop_text_live()
                    _stop_spinner()
                    _render_error(str(event_data))

                elif event_type == AgentStreamEvent.DONE:
                    break
    finally:
        _stop_text_live()
        _stop_spinner()

    # ── Phase 2: Tool execution loop ──────────────────────────
    # If the run entered requires_action, execute tools and submit results.
    # Loop in case the agent chains multiple rounds of tool calls.
    while pending_tool_calls and run_id:
        # Execute each pending tool call
        tool_outputs = []
        for tc in pending_tool_calls:
            if tc.type == "function":
                _render_tool_card({"name": tc.function.name, "args": tc.function.arguments})
                _start_spinner(f"running {tc.function.name}...")
                result = _execute_tool_call(tool_map, tc.function.name, tc.function.arguments)
                _stop_spinner()
                tool_outputs.append(ToolOutput(
                    tool_call_id=tc.id,
                    output=result,
                ))

        pending_tool_calls = None  # reset before checking again

        # Submit tool outputs (non-streaming) and get updated run
        _start_spinner("thinking...")
        try:
            run = client.runs.submit_tool_outputs(
                thread_id=thread_id,
                run_id=run_id,
                tool_outputs=tool_outputs,
            )
        except Exception as e:
            _stop_spinner()
            _render_error(str(e), "Tool Submit Failed")
            break

        # Poll until the run reaches a terminal state or requires_action
        while run.status in ("queued", "in_progress"):
            time.sleep(0.5)
            run = client.runs.get(thread_id=thread_id, run_id=run.id)

        _stop_spinner()

        if run.status == "requires_action":
            pending_tool_calls = run.required_action.submit_tool_outputs.tool_calls
            continue  # another round of tool execution
        elif run.status == "completed":
            break  # done — fetch messages below
        else:
            _render_error(
                str(getattr(run, "last_error", run.status)),
                f"Run {run.status.title()}",
            )
            break

    # ── Phase 3: Render the response ──────────────────────────
    # Phase 1 text was already rendered live. If tools ran in Phase 2,
    # fetch the assistant's final message from the thread.
    if not full_text.strip() and run_id:
        # Text was generated after tool execution — fetch from thread
        try:
            messages = client.messages.list(thread_id=thread_id)
            # Handle both paginated (.data) and plain list returns
            msg_list = getattr(messages, "data", None) or list(messages)
            # Find the latest assistant message (API usually returns newest first)
            for msg in msg_list:
                if msg.role == "assistant":
                    parts = []
                    for content_item in msg.content:
                        if hasattr(content_item, "text"):
                            txt = content_item.text
                            text_val = getattr(txt, "value", None) or str(txt)
                            if text_val.strip():
                                parts.append(text_val.strip())
                    if parts:
                        full_text = "\n\n".join(parts)
                        console.print(Markdown(full_text), highlight=False)
                    break
        except Exception:
            pass  # graceful — the tool output was still submitted

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
    setup_toolset(client)
    thread = client.threads.create()

    show_welcome(AGENT_VERSION)

    history_file = os.path.join(PROJECT_ROOT, ".chat_history")
    session = PromptSession(history=FileHistory(history_file))

    prompt_html = HTML('<style fg="#bfa669">\u276f </style>')
    favicon = get_favicon()

    while True:
        try:
            user_input = session.prompt(prompt_html, multiline=False)
        except (EOFError, KeyboardInterrupt):
            console.print("\n  [bold #bfa669]Goodbye.[/bold #bfa669]\n")
            break

        user_input = user_input.strip()
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "/exit", "/quit"):
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
            client.messages.create(thread_id=thread.id, role="user", content=user_input)
            console.print()
            sys.stdout.write(f"  {favicon} ")
            sys.stdout.flush()
            console.print("[bold #bfa669]crowe-logic[/bold #bfa669]")
            stream_response(client, thread.id, agent_id)
            console.print(f"  [dim #bfa669]{'─' * min(60, console.width)}[/dim #bfa669]")
        except Exception as e:
            error_msg = str(e)
            if "while a run" in error_msg and "is active" in error_msg:
                _render_error(
                    "Agent is still thinking — wait a moment and try again.",
                    "Busy",
                )
                time.sleep(3)
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
    console.print()
    console.print(table)
    console.print()


@main.command()
@click.argument("prompt")
def run(prompt: str):
    """Run a single prompt and print the response."""
    agent_id = get_agent_id()
    client = get_client()
    setup_toolset(client)
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


if __name__ == "__main__":
    main()
