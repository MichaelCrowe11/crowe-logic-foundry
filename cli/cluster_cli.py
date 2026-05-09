"""
cli/cluster_cli.py

Click subgroup that exposes the CroweLM-Music sub-cluster (and any future
sub-cluster) on the command line. Wraps cli.cluster_dispatch so the
operator can run cluster work without writing a Python script.

Wired into the main `crowe-logic` CLI by an import + registration call
at the bottom of cli/crowe_logic.py:

    from cli.cluster_cli import register
    register(main)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

import click

from cli.cluster_dispatch import (
    ClusterSession,
    DispatchResult,
    dispatch_in_parallel,
    dispatch_to_specialist,
    run_critic_gate,
)
from crowe_synapse_engine.agent_registry import AgentRegistry


# ── helpers ──────────────────────────────────────────────────────────────


def _agents_dir() -> str:
    """Locate the agents/ directory next to this repo's package root.

    Resolves repo root by walking up from this file until a directory
    that contains both `agents/` and `crowe_synapse_engine/` is found.
    Falls back to env override CROWE_FOUNDRY_AGENTS_DIR if set.
    """
    override = os.environ.get("CROWE_FOUNDRY_AGENTS_DIR")
    if override:
        return override
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        candidate = ancestor / "agents"
        if (candidate.is_dir() and (ancestor / "crowe_synapse_engine").is_dir()):
            return str(candidate)
    return str(Path.cwd() / "agents")


def _registry() -> AgentRegistry:
    return AgentRegistry(agents_dir=_agents_dir())


def _print_dispatch_summary(result: DispatchResult, *, prefix: str = "") -> None:
    p = prefix
    click.echo(f"{p}specialist: {result.specialist}")
    click.echo(f"{p}provider:   {result.provider}")
    click.echo(f"{p}model:      {result.model_used}")
    click.echo(f"{p}tokens:     {result.total_tokens}  "
               f"(prompt={result.prompt_tokens}, completion={result.completion_tokens})")
    click.echo(f"{p}latency:    {result.latency_s:.1f}s")
    if result.error:
        click.echo(f"{p}error:      {result.error}")


def _read_brief(brief_arg: Optional[str], brief_file: Optional[str]) -> str:
    """Resolve the brief from positional arg, file, or stdin (in that order).

    Operator UX:
      crowe-logic cluster ask music-critic "the prompt text"
      crowe-logic cluster ask music-critic -f /path/to/brief.txt
      cat brief.txt | crowe-logic cluster ask music-critic
    """
    if brief_file:
        with open(brief_file) as f:
            return f.read()
    if brief_arg:
        return brief_arg
    if not sys.stdin.isatty():
        text = sys.stdin.read()
        if text.strip():
            return text
    raise click.UsageError(
        "no brief supplied; pass it as an argument, with -f <file>, or pipe to stdin"
    )


# ── commands ─────────────────────────────────────────────────────────────


@click.group()
def cluster():
    """Invoke the CroweLM agent clusters from the command line."""


@cluster.command(name="list")
def cluster_list():
    """List every cluster the registry knows about."""
    reg = _registry()
    clusters = reg.list_clusters()
    if not clusters:
        click.echo("no clusters registered")
        return
    for c in clusters:
        members = reg.agents_in_cluster(c.name)
        click.echo(f"{c.name:24s} v{c.version}  "
                   f"members={len(members)}  "
                   f"ai_panel_entry={c.ai_panel_entry or '-'}")
        click.echo(f"  {c.description}")


@cluster.command(name="show")
@click.argument("cluster_name")
def cluster_show(cluster_name: str):
    """Show one cluster's members, tier mapping, and entry point."""
    reg = _registry()
    c = reg.get_cluster(cluster_name)
    if c is None:
        raise click.ClickException(f"cluster not found: {cluster_name!r}")

    click.echo(f"cluster:        {c.name}  v{c.version}")
    click.echo(f"description:    {c.description}")
    click.echo(f"ai panel entry: {c.ai_panel_entry or '-'}")
    click.echo(f"pipelines:      {', '.join(c.pipelines) or '-'}")
    click.echo(f"style rules:    {', '.join(c.style_rules) or '-'}")
    click.echo()
    click.echo("members:")
    for a in reg.agents_in_cluster(c.name):
        alias = f"  (alias of {a.alias_of})" if a.alias_of else ""
        click.echo(f"  {a.name:24s}  model={a.model:25s}{alias}")
        if a.description:
            click.echo(f"    {a.description}")


@cluster.command(name="ask")
@click.argument("specialist")
@click.argument("brief", required=False)
@click.option("-f", "--file", "brief_file",
              type=click.Path(exists=True, dir_okay=False),
              help="read the brief from this file instead of an argument")
@click.option("--temperature", default=0.1, show_default=True, type=float,
              help="sampling temperature for the dispatch")
@click.option("--timeout", default=180.0, show_default=True, type=float,
              help="timeout in seconds")
@click.option("--json", "as_json", is_flag=True,
              help="emit a structured JSON result instead of human-readable output")
def cluster_ask(specialist: str, brief: Optional[str], brief_file: Optional[str],
                temperature: float, timeout: float, as_json: bool):
    """Dispatch a brief to one specialist; print its response.

    \b
    Examples:
      crowe-logic cluster ask music-compose "Write a bridge for Velvet Algorithm"
      crowe-logic cluster ask music-master -f manifest.txt
      cat diff.patch | crowe-logic cluster ask music-critic
    """
    text = _read_brief(brief, brief_file)
    reg = _registry()
    session = ClusterSession(session_id=f"cli-{os.getpid()}", cluster="cli-ad-hoc")
    result = dispatch_to_specialist(
        specialist, text, registry=reg, session=session,
        timeout_s=timeout, temperature=temperature,
    )
    if as_json:
        click.echo(json.dumps({
            "specialist": result.specialist,
            "provider": result.provider,
            "model_used": result.model_used,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.total_tokens,
            "latency_s": round(result.latency_s, 2),
            "succeeded": result.succeeded,
            "error": result.error,
            "answer": result.answer,
        }, indent=2))
        sys.exit(0 if result.succeeded else 1)

    _print_dispatch_summary(result)
    click.echo()
    click.echo("--- answer ---")
    click.echo(result.answer if result.answer else "(no answer returned)")
    sys.exit(0 if result.succeeded else 1)


@cluster.command(name="gate")
@click.argument("diff", required=False)
@click.option("-f", "--file", "diff_file",
              type=click.Path(exists=True, dir_okay=False),
              help="read the diff from this file instead of an argument or stdin")
@click.option("--critic", "critic_name", default="music-critic", show_default=True,
              help="which agent to use as the critic")
@click.option("--timeout", default=180.0, show_default=True, type=float)
@click.option("--json", "as_json", is_flag=True)
def cluster_gate(diff: Optional[str], diff_file: Optional[str],
                 critic_name: str, timeout: float, as_json: bool):
    """Run the cluster's critic against a diff; exit 0 on PASS, 1 otherwise.

    \b
    Examples:
      git diff | crowe-logic cluster gate
      crowe-logic cluster gate -f /tmp/proposed.patch
      crowe-logic cluster gate "+ new line"
    """
    text = _read_brief(diff, diff_file)
    reg = _registry()
    session = ClusterSession(session_id=f"cli-{os.getpid()}", cluster="cli-ad-hoc")
    passed, result = run_critic_gate(
        text, registry=reg, critic_name=critic_name,
        session=session, timeout_s=timeout,
    )
    if as_json:
        click.echo(json.dumps({
            "passed": passed,
            "critic": critic_name,
            "provider": result.provider,
            "model_used": result.model_used,
            "tokens": result.total_tokens,
            "latency_s": round(result.latency_s, 2),
            "answer": result.answer,
            "error": result.error,
        }, indent=2))
        sys.exit(0 if passed else 1)

    _print_dispatch_summary(result)
    click.echo()
    click.echo(f"--- verdict: {'PASS' if passed else 'BLOCK/WARN/NOTE'} ---")
    click.echo(result.answer)
    sys.exit(0 if passed else 1)


@cluster.command(name="parallel")
@click.argument("specialists")
@click.argument("brief", required=False)
@click.option("-f", "--file", "brief_file",
              type=click.Path(exists=True, dir_okay=False))
@click.option("--timeout", default=180.0, show_default=True, type=float)
@click.option("--max-workers", default=4, show_default=True, type=int)
def cluster_parallel(specialists: str, brief: Optional[str], brief_file: Optional[str],
                     timeout: float, max_workers: int):
    """Fan out the same brief to multiple specialists; print all answers.

    \b
    SPECIALISTS is a comma-separated list, e.g. music-web,music-native

    \b
    Examples:
      crowe-logic cluster parallel music-web,music-native "design the timeline"
    """
    names = [s.strip() for s in specialists.split(",") if s.strip()]
    if not names:
        raise click.UsageError("at least one specialist required")
    text = _read_brief(brief, brief_file)
    reg = _registry()
    session = ClusterSession(session_id=f"cli-{os.getpid()}", cluster="cli-ad-hoc")
    results = dispatch_in_parallel(
        names, text, registry=reg, session=session,
        timeout_s=timeout, max_workers=max_workers,
    )
    for r in results:
        click.echo("=" * 60)
        _print_dispatch_summary(r)
        click.echo("--- answer ---")
        click.echo(r.answer if r.answer else "(no answer)")
        click.echo()
    failed = [r for r in results if not r.succeeded]
    sys.exit(1 if failed else 0)


# ── registration ─────────────────────────────────────────────────────────


def register(main_group: click.Group) -> None:
    """Attach the `cluster` subgroup to the main crowe-logic CLI group."""
    main_group.add_command(cluster)
