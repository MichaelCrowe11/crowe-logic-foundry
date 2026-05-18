# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
"""
`crowe-logic portfolio` subcommand group.

Surfaces the crowe-portfolio MCP (242+ repos, 9+ datasets, agent
catalog) inside the foundry CLI. Delegates to tools/portfolio_tools.py
which hits the HTTP server at $CROWE_PORTFOLIO_URL. When the env vars
aren't set, every command short-circuits with a clear setup hint so
nothing blows up offline.

This is the local-developer mirror of what an agent gets when it calls
`portfolio_list_repos` / `portfolio_search_code` / etc. mid-turn.
"""
from __future__ import annotations

import json
from typing import Any

import click


def _maybe_decode(raw: str) -> Any:
    """portfolio_tools.* return JSON strings; some upstream calls already
    return JSON-encoded JSON (a stringified array). Try to flatten once.
    """
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return raw
    if isinstance(data, dict) and "result" in data and isinstance(data["result"], str):
        try:
            return json.loads(data["result"])
        except json.JSONDecodeError:
            return data["result"]
    return data


def _emit(data: Any, as_json: bool, console) -> None:
    """Print as JSON or rich; falls through gracefully on errors."""
    if as_json:
        click.echo(json.dumps(data, indent=2))
        return

    if isinstance(data, dict) and data.get("error"):
        console.print(f"[red]error:[/] {data.get('error')}")
        if "hint" in data:
            console.print(f"  [dim]{data['hint']}[/]")
        return

    if isinstance(data, list):
        _render_list(data, console)
    elif isinstance(data, dict):
        _render_dict(data, console)
    else:
        console.print(data)


def _render_list(items: list, console) -> None:
    from rich.table import Table

    if not items:
        console.print("[dim](no results)[/]")
        return
    if not isinstance(items[0], dict):
        for it in items:
            console.print(it)
        return

    # Build a table from union-of-common-keys (cap width).
    priority_cols = ["name", "kind", "domain", "status", "repo", "visibility",
                     "language", "pushed_at", "summary", "description", "location",
                     "score", "path", "snippet"]
    seen_keys: list[str] = []
    for it in items:
        for k in priority_cols:
            if k in it and k not in seen_keys:
                seen_keys.append(k)
        for k in it.keys():
            if k not in seen_keys and not k.startswith("_"):
                seen_keys.append(k)
    # Trim noisy columns
    seen_keys = [k for k in seen_keys if k not in ("tags", "deploy_url", "indexable")]

    table = Table(
        show_header=True,
        header_style="bold",
        padding=(0, 1),
    )
    for k in seen_keys[:6]:  # cap columns for terminal width
        table.add_column(k, overflow="fold", max_width=44)
    for it in items:
        row = []
        for k in seen_keys[:6]:
            v = it.get(k, "")
            if isinstance(v, list):
                v = ", ".join(str(x) for x in v[:3]) + (f" +{len(v)-3}" if len(v) > 3 else "")
            row.append(str(v) if v is not None else "")
        table.add_row(*row)
    console.print(table)
    console.print(f"[dim]{len(items)} row(s)[/]")


def _render_dict(d: dict, console) -> None:
    from rich.table import Table

    table = Table(
        show_header=False,
        padding=(0, 1),
    )
    table.add_column("key", style="bold")
    table.add_column("value", overflow="fold")
    for k, v in d.items():
        if isinstance(v, (list, dict)):
            v = json.dumps(v, indent=2)
        table.add_row(k, str(v) if v is not None else "")
    console.print(table)


def register(main_group: click.Group, console) -> None:
    """Attach the `portfolio` subcommand group to the foundry's main click.Group.

    Call from cli/crowe_logic.py once at module load:
        from cli.portfolio import register as _register_portfolio
        _register_portfolio(main, console)
    """

    @main_group.group()
    def portfolio():
        """Query the crowe-portfolio knowledge plane (repos / datasets / agents)."""

    @portfolio.command("repos")
    @click.option("--domain", default="", help="Filter: ai_platform, mycology, drug_discovery, content, ...")
    @click.option("--status", default="", help="Filter: canonical, superseded, experiment, archive_candidate.")
    @click.option("--limit", type=int, default=50, show_default=True)
    @click.option("--json", "as_json", is_flag=True)
    def repos_cmd(domain: str, status: str, limit: int, as_json: bool):
        """List repositories in the portfolio."""
        from tools.portfolio_tools import portfolio_list_repos
        data = _maybe_decode(portfolio_list_repos(domain=domain, status=status, limit=limit))
        _emit(data, as_json, console)

    @portfolio.command("datasets")
    @click.option("--json", "as_json", is_flag=True)
    def datasets_cmd(as_json: bool):
        """List structured datasets (Neon DBs, training corpora, books)."""
        from tools.portfolio_tools import portfolio_list_datasets
        data = _maybe_decode(portfolio_list_datasets())
        _emit(data, as_json, console)

    @portfolio.command("agents")
    @click.option("--json", "as_json", is_flag=True)
    def agents_cmd(as_json: bool):
        """List agents in the catalog (Foundry + external)."""
        from tools.portfolio_tools import portfolio_list_agents
        data = _maybe_decode(portfolio_list_agents())
        _emit(data, as_json, console)

    @portfolio.command("search")
    @click.argument("query")
    @click.option("--domain", default="", help="Restrict to one domain.")
    @click.option("--repo", default="", help="Restrict to one repo by exact name.")
    @click.option("--limit", type=int, default=10, show_default=True)
    @click.option("--json", "as_json", is_flag=True)
    def search_cmd(query: str, domain: str, repo: str, limit: int, as_json: bool):
        """Semantic + BM25 hybrid search across canonical repos."""
        from tools.portfolio_tools import portfolio_search_code
        data = _maybe_decode(portfolio_search_code(query=query, domain=domain, repo=repo, limit=limit))
        _emit(data, as_json, console)

    @portfolio.command("show")
    @click.argument("name")
    @click.option("--json", "as_json", is_flag=True)
    def show_cmd(name: str, as_json: bool):
        """Show full registry record for a repo by exact name."""
        from tools.portfolio_tools import portfolio_show_repo
        data = _maybe_decode(portfolio_show_repo(name=name))
        _emit(data, as_json, console)

    @portfolio.command("clusters")
    @click.option("--min-size", type=int, default=2, show_default=True)
    @click.option("--json", "as_json", is_flag=True)
    def clusters_cmd(min_size: int, as_json: bool):
        """List duplicate-suspect clusters (repos sharing a normalized name)."""
        from tools.portfolio_tools import portfolio_list_clusters
        data = _maybe_decode(portfolio_list_clusters(min_size=min_size))
        _emit(data, as_json, console)

    @portfolio.command("stale")
    @click.option("--days", type=int, default=180, show_default=True)
    @click.option("--json", "as_json", is_flag=True)
    def stale_cmd(days: int, as_json: bool):
        """List repos with no push activity for N days."""
        from tools.portfolio_tools import portfolio_stale_repos
        data = _maybe_decode(portfolio_stale_repos(days=days))
        _emit(data, as_json, console)

    @portfolio.command("find-canonical")
    @click.argument("query")
    @click.option("--json", "as_json", is_flag=True)
    def canonical_cmd(query: str, as_json: bool):
        """Find the canonical repo for a concept query."""
        from tools.portfolio_tools import portfolio_find_canonical
        data = _maybe_decode(portfolio_find_canonical(query=query))
        _emit(data, as_json, console)


__all__ = ["register"]
