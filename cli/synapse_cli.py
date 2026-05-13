"""cli/synapse_cli.py

Click subgroup that runs agents through the synapse runtime + AICL.

Wired into the main `crowe-logic` CLI by an import + registration call
at the bottom of cli/crowe_logic.py:

    from cli.synapse_cli import register
    register(main)

Commands:
    crowe-logic synapse run <agent-or-file> [prompt]
    crowe-logic synapse list
    crowe-logic synapse show <session-id>

The ``run`` command accepts either an agent name (resolved against
``agents/<name>.yaml``) or a path to a ``.synapse-agent`` source file.
Output streams to the terminal with light formatting; AICL messages
persist to the configured MemoryStore so the run can be replayed later
via ``show``.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import click
import yaml

from crowe_synapse_engine.agent_registry import AgentConfig, AgentRegistry
from crowe_synapse_engine.memory import MemoryStore
from crowe_synapse_engine.runtime import select_runtime
from crowe_synapse_engine.runtime.base import ChunkKind


# ── helpers ──────────────────────────────────────────────────────────────


def _agents_dir() -> str:
    """Locate the agents/ directory next to this repo's package root."""
    override = os.environ.get("CROWE_FOUNDRY_AGENTS_DIR")
    if override:
        return override
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        candidate = ancestor / "agents"
        if candidate.is_dir() and (ancestor / "crowe_synapse_engine").is_dir():
            return str(candidate)
    return str(Path.cwd() / "agents")


def _load_agent(agent_or_path: str) -> AgentConfig:
    """Resolve a name (looked up in agents/) or a .synapse-agent file path."""
    path = Path(agent_or_path)
    if path.suffix == ".synapse-agent" and path.is_file():
        from crowe_synapse_engine.synapse_dsl import compile_source

        compiled = compile_source(path.read_text(encoding="utf-8"))
        if not compiled:
            raise click.ClickException(f"No agent blocks found in {path}")
        return AgentConfig(**compiled[0])

    if path.suffix in (".yaml", ".yml") and path.is_file():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return AgentConfig(
            **{k: v for k, v in data.items() if k in AgentConfig.__dataclass_fields__}
        )

    # Lookup by name in the registry.
    registry = AgentRegistry(agents_dir=_agents_dir())
    agent = registry.get_agent(agent_or_path)
    if agent is None:
        names = ", ".join(a.name for a in registry.list_agents())
        raise click.ClickException(
            f"Agent {agent_or_path!r} not found. Known: {names or '(none)'}"
        )
    return agent


# ── click group ──────────────────────────────────────────────────────────


@click.group()
def synapse():
    """Run agents through the synapse runtime with AICL emission."""


@synapse.command("list")
def synapse_list():
    """List agents discovered under ``agents/``."""
    registry = AgentRegistry(agents_dir=_agents_dir())
    agents = registry.list_agents()
    if not agents:
        click.echo("(no agents found)")
        return
    width = max(len(a.name) for a in agents)
    for agent in sorted(agents, key=lambda a: a.name):
        cluster = f"  [{agent.cluster}]" if agent.cluster else ""
        click.echo(f"  {agent.name:<{width}}  model={agent.model}{cluster}")


@synapse.command("run")
@click.argument("agent_or_path")
@click.argument("prompt", required=False)
@click.option(
    "--runtime-hint",
    default=None,
    help="Force a runtime: 'sdk' for the Claude Agent SDK bridge.",
)
@click.option("--max-turns", default=20, show_default=True, type=int)
@click.option("--no-persist", is_flag=True, help="Skip persisting AICL to memory.")
@click.option("--thread-id", default="cli", help="Thread id for the memory session.")
def synapse_run(
    agent_or_path: str,
    prompt: str | None,
    runtime_hint: str | None,
    max_turns: int,
    no_persist: bool,
    thread_id: str,
):
    """Run an agent against PROMPT. Streams output and persists AICL."""
    agent = _load_agent(agent_or_path)
    if prompt is None:
        if sys.stdin.isatty():
            raise click.ClickException("No prompt provided. Pass as arg or via stdin.")
        prompt = sys.stdin.read().strip()
    if not prompt:
        raise click.ClickException("Empty prompt.")

    store = None
    session_id: str | None = None
    if not no_persist:
        store = MemoryStore()
        session_id = store.start_session(
            thread_id=thread_id, project_context=f"agent={agent.name}"
        )

    runtime = select_runtime(agent, runtime_hint=runtime_hint or agent.runtime)

    async def consume():
        try:
            async for chunk in runtime.run(
                agent_name=agent.name,
                user_prompt=prompt,
                system_prompt=agent.prompt_override,
                model=agent.model,
                tools=agent.tools,
                max_turns=max_turns,
            ):
                _render(chunk)
                if store is not None and chunk.kind == ChunkKind.AICL:
                    from crowe_synapse_engine.aicl import AICLMessage

                    msg = AICLMessage.from_dict(chunk.meta["aicl"])
                    store.record_aicl_message(session_id, msg)
        finally:
            if store is not None and session_id is not None:
                store.end_session(session_id, summary=f"agent={agent.name}")

    try:
        asyncio.run(consume())
    except KeyboardInterrupt:
        click.echo("\n[interrupted]", err=True)
        sys.exit(130)

    if session_id is not None:
        click.echo(f"\n[session {session_id}]", err=True)


@synapse.command("show")
@click.argument("session_id")
def synapse_show(session_id: str):
    """Replay the AICL transcript for SESSION_ID."""
    store = MemoryStore()
    conv = store.get_aicl_conversation(session_id)
    if len(conv) == 0:
        click.echo(f"(no AICL messages for session {session_id})")
        return
    for msg in conv:
        target = f" -> {msg.to_agent}" if msg.to_agent else ""
        confidence = f"  (c={msg.confidence:.2f})" if msg.confidence < 1.0 else ""
        click.echo(
            f"[{msg.timestamp}] {msg.act.value:<8}  {msg.from_agent}{target}{confidence}"
        )
        click.echo(f"    {msg.subject}")
        if msg.evidence:
            click.echo(f"    evidence: {', '.join(msg.evidence)}")


# ── chunk renderer ───────────────────────────────────────────────────────


def _render(chunk) -> None:
    """Render a RuntimeChunk to the terminal.

    Plain stdout for assistant text so the output composes with pipes;
    stderr for everything else so structured chatter doesn't pollute the
    captured response. Keeps ``crowe-logic synapse run ... > answer.txt``
    workable.
    """
    if chunk.kind == ChunkKind.TEXT:
        click.echo(chunk.text, nl=False)
        return
    if chunk.kind == ChunkKind.AICL:
        click.echo(f"\n  {chunk.text}", err=True)
        return
    if chunk.kind == ChunkKind.TOOL_CALL:
        click.echo(f"\n  -> {chunk.tool_name}({chunk.tool_args})", err=True)
        return
    if chunk.kind == ChunkKind.TOOL_RESULT:
        snippet = (chunk.tool_result or "")[:160]
        click.echo(f"  <- {chunk.tool_name}: {snippet}", err=True)
        return
    if chunk.kind == ChunkKind.REASONING:
        click.echo(f"  {chunk.text}", err=True)
        return
    if chunk.kind == ChunkKind.ERROR:
        click.echo(f"\n  [error] {chunk.text}", err=True)
        return
    if chunk.kind == ChunkKind.DONE:
        click.echo("", err=True)


# ── registration ─────────────────────────────────────────────────────────


def register(main_group) -> None:
    """Attach the `synapse` subgroup to the main crowe-logic CLI group."""
    main_group.add_command(synapse)
