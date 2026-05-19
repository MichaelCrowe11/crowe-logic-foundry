# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
"""
Agent <-> tool dependency cross-reference.

Each `agents/*.yaml` declares a `tools:` list. This module:
  - Loads every agent YAML and collects the declared tool names
  - Walks `tools/*.py` and collects every public top-level function as a
    candidate registered tool (the same surface the auto-discovery uses)
  - Cross-references them:
      * Orphans: agent declares a tool that no tools/ module defines
      * Shadows: tools/ module defines a function no agent references
      * Stats:  how many agents reference each tool, which is most used

Pure stdlib + PyYAML. No agent_config import (which is heavy).
"""
from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_INFRASTRUCTURE_TOOL_FILES: frozenset[str] = frozenset({
    "registry",
    "control_center",
    "mobile_signaling",
    "audit_log",
    "mcp_client",
    "staging_pipeline",
})


@dataclass
class AgentDeps:
    """Per-agent tool declarations and orphan diagnosis."""
    agent: str
    declared: list[str] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)


@dataclass
class DepsReport:
    agents: list[AgentDeps]
    registered_tools: list[str]
    shadows: list[str]  # in registry, no agent uses them
    usage: dict[str, int]  # tool name -> agent count


def _collect_registered_tools() -> set[str]:
    """Scan tools/*.py and return public top-level function names.

    Mirrors the heuristic in cli/doctor.check_tool_docstrings: skip
    infrastructure files and modules that import FastAPI/Starlette.
    """
    tools_dir = PROJECT_ROOT / "tools"
    registered: set[str] = set()
    if not tools_dir.is_dir():
        return registered
    for path in sorted(tools_dir.glob("*.py")):
        if path.name == "__init__.py" or path.stem in _INFRASTRUCTURE_TOOL_FILES:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        is_infra = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in ("fastapi", "starlette"):
                    is_infra = True
                    break
            elif isinstance(node, ast.Import):
                if any(a.name.split(".")[0] in ("fastapi", "starlette") for a in node.names):
                    is_infra = True
                    break
        if is_infra:
            continue
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith("_"):
                continue
            registered.add(node.name)
    return registered


def _collect_agent_tools() -> dict[str, list[str]]:
    """Return {agent_stem: [tool_name, ...]} for every agents/*.yaml."""
    import yaml

    agents_dir = PROJECT_ROOT / "agents"
    out: dict[str, list[str]] = {}
    if not agents_dir.is_dir():
        return out
    for yf in sorted(agents_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(yf.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        tools = data.get("tools") or []
        if not isinstance(tools, list):
            continue
        clean: list[str] = []
        for entry in tools:
            if isinstance(entry, str):
                # Some yamls inline "name: gloss" — strip after first ":"
                clean.append(entry.split(":")[0].strip())
            elif isinstance(entry, dict) and entry:
                # YAML mapping form: take the only key
                clean.append(next(iter(entry.keys())))
        out[yf.stem] = clean
    return out


def _resolves(declared: str, registered: set[str]) -> bool:
    """A declared tool resolves if it's an exact match OR a wildcard
    (`prefix_*`) that matches at least one registered tool.
    """
    if declared in registered:
        return True
    if declared.endswith("*"):
        prefix = declared[:-1]
        return any(r.startswith(prefix) for r in registered)
    return False


def compute_deps() -> DepsReport:
    registered = _collect_registered_tools()
    agent_tools = _collect_agent_tools()
    agents: list[AgentDeps] = []
    usage: dict[str, int] = {}
    used_names: set[str] = set()
    for name in sorted(agent_tools):
        declared = agent_tools[name]
        orphans = [t for t in declared if not _resolves(t, registered)]
        agents.append(AgentDeps(agent=name, declared=declared, orphans=orphans))
        for t in declared:
            usage[t] = usage.get(t, 0) + 1
            if t in registered:
                used_names.add(t)
            elif t.endswith("*"):
                prefix = t[:-1]
                used_names.update(r for r in registered if r.startswith(prefix))
    shadows = sorted(registered - used_names)
    return DepsReport(
        agents=agents,
        registered_tools=sorted(registered),
        shadows=shadows,
        usage=usage,
    )


def render_table(report: DepsReport, console: Any = None) -> None:
    from rich.console import Console
    from rich.table import Table

    console = console or Console()

    # Per-agent table
    agent_table = Table(
        title="[bold]agent → tool declarations[/]",
        show_header=True,
        header_style="bold",
        title_justify="left",
        padding=(0, 1),
    )
    agent_table.add_column("agent")
    agent_table.add_column("declared", justify="right")
    agent_table.add_column("orphans", justify="right")
    agent_table.add_column("orphan names", overflow="fold")
    for a in report.agents:
        n = len(a.orphans)
        orphan_str = ", ".join(a.orphans) if a.orphans else ""
        agent_table.add_row(
            a.agent,
            str(len(a.declared)),
            f"[red]{n}[/]" if n else "0",
            orphan_str,
        )
    console.print(agent_table)
    console.print()

    # Shadow tools (registered but unused)
    shadow_table = Table(
        title=f"[bold]shadow tools[/] ({len(report.shadows)} registered but no agent references)",
        show_header=False,
        title_justify="left",
        padding=(0, 1),
    )
    shadow_table.add_column("tool")
    for s in report.shadows:
        shadow_table.add_row(s)
    if not report.shadows:
        shadow_table.add_row("[dim](none)[/]")
    console.print(shadow_table)
    console.print()

    # Top usage
    top = sorted(report.usage.items(), key=lambda kv: (-kv[1], kv[0]))[:15]
    usage_table = Table(
        title="[bold]top-used tools across agents[/]",
        show_header=True,
        header_style="bold",
        title_justify="left",
        padding=(0, 1),
    )
    usage_table.add_column("tool")
    usage_table.add_column("agents", justify="right")
    for tool, count in top:
        usage_table.add_row(tool, str(count))
    console.print(usage_table)
    console.print()

    orphan_total = sum(len(a.orphans) for a in report.agents)
    if orphan_total:
        console.print(
            f"[red bold]warning:[/] {orphan_total} orphan tool reference(s) — "
            "agents declare tools that have no implementation."
        )


def render_json(report: DepsReport) -> str:
    return json.dumps(
        {
            "agents": [asdict(a) for a in report.agents],
            "registered_tools": report.registered_tools,
            "shadows": report.shadows,
            "usage": report.usage,
        },
        indent=2,
    )


def has_orphans(report: DepsReport) -> bool:
    return any(a.orphans for a in report.agents)


__all__ = [
    "AgentDeps",
    "DepsReport",
    "compute_deps",
    "render_table",
    "render_json",
    "has_orphans",
]
