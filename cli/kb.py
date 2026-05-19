# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
"""
`crowe-logic kb` — knowledge-lake CLI surface.

Four subcommands:
  kb sources        — list registered sources (and which are ingested)
  kb status         — db path, total chunks, source breakdown
  kb ingest <name>  — run the right ingestor against the source
  kb search QUERY   — FTS5 search across (or scoped to) one source
"""
from __future__ import annotations

import json
from typing import Optional

import click


def register(main_group: click.Group, console) -> None:
    """Attach the `kb` subgroup to the foundry's main click.Group."""

    @main_group.group()
    def kb():
        """Knowledge-lake search and ingestion."""

    # ─── sources ──────────────────────────────────────────────

    @kb.command("sources")
    @click.option("--json", "as_json", is_flag=True)
    def sources_cmd(as_json: bool):
        """List registered knowledge-lake sources."""
        from knowledge_lake import KNOWN_SOURCES, Store
        store = Store()
        ingested = {s.name: s for s in store.list_sources()}
        items = []
        for src in sorted(KNOWN_SOURCES.values(), key=lambda s: s.name):
            row_state = ingested.get(src.name)
            items.append({
                "name": src.name,
                "kind": src.kind,
                "root": str(src.root),
                "root_exists": src.root.exists(),
                "ingested": bool(row_state),
                "last_ingested_at": row_state.last_ingested_at if row_state else None,
                "chunk_count": row_state.chunk_count if row_state else 0,
                "description": src.description,
            })
        if as_json:
            click.echo(json.dumps(items, indent=2))
            return
        _render_sources_table(items, console)

    # ─── status ───────────────────────────────────────────────

    @kb.command("status")
    @click.option("--json", "as_json", is_flag=True)
    def status_cmd(as_json: bool):
        """Show DB path, total chunks, per-source breakdown."""
        from knowledge_lake import Store
        store = Store()
        stats = store.stats()
        sources = [
            {
                "name": s.name,
                "kind": s.kind,
                "chunks": s.chunk_count,
                "last_ingested_at": s.last_ingested_at,
            }
            for s in store.list_sources()
        ]
        if as_json:
            click.echo(json.dumps({**stats, "sources": sources}, indent=2))
            return
        _render_status(stats, sources, console)

    # ─── ingest ───────────────────────────────────────────────

    @kb.command("ingest")
    @click.argument("name")
    @click.option("--json", "as_json", is_flag=True)
    def ingest_cmd(name: str, as_json: bool):
        """Run the ingestor for a registered source."""
        from knowledge_lake import KNOWN_SOURCES, Store
        from knowledge_lake.ingest import ingestor_for

        src = KNOWN_SOURCES.get(name)
        if not src:
            raise click.UsageError(
                f"Unknown source {name!r}. Try `crowe-logic kb sources`."
            )
        store = Store()
        try:
            ingestor = ingestor_for(src, store)
        except NotImplementedError as exc:
            click.echo(str(exc), err=True)
            raise SystemExit(2)
        try:
            stats = ingestor.run()
        except FileNotFoundError as exc:
            click.echo(str(exc), err=True)
            raise SystemExit(1)
        if as_json:
            click.echo(json.dumps({
                "source": stats.source,
                "files_seen": stats.files_seen,
                "files_ingested": stats.files_ingested,
                "chunks_written": stats.chunks_written,
                "skipped": stats.skipped_paths,
            }, indent=2))
            return
        console.print(
            f"[green]ingested[/] [bold]{stats.source}[/]: "
            f"{stats.files_ingested}/{stats.files_seen} files, "
            f"{stats.chunks_written} chunks"
        )
        for sk in stats.skipped_paths[:10]:
            console.print(f"  [dim]skip: {sk}[/]")
        if len(stats.skipped_paths) > 10:
            console.print(f"  [dim](+{len(stats.skipped_paths) - 10} more skips)[/]")

    # ─── search ───────────────────────────────────────────────

    @kb.command("search")
    @click.argument("query")
    @click.option("--source", default=None, help="Restrict to one source.")
    @click.option("--limit", type=int, default=10, show_default=True)
    @click.option("--json", "as_json", is_flag=True)
    def search_cmd(query: str, source: Optional[str], limit: int, as_json: bool):
        """FTS5 search across (or scoped to one) knowledge-lake source."""
        from knowledge_lake import search as kb_search
        hits = kb_search(query, source=source, limit=limit)
        if as_json:
            click.echo(json.dumps([h.to_dict() for h in hits], indent=2))
            return
        if not hits:
            console.print(f"[dim]no matches for {query!r}[/]")
            raise SystemExit(1)
        _render_hits(query, hits, console)


# ─── rich renderers ────────────────────────────────────────────

def _render_sources_table(items: list[dict], console) -> None:
    from rich.table import Table
    t = Table(
        title="[bold]knowledge-lake sources[/]",
        show_header=True,
        header_style="bold",
        title_justify="left",
        padding=(0, 1),
    )
    t.add_column("name")
    t.add_column("kind", width=8)
    t.add_column("chunks", justify="right", width=8)
    t.add_column("state", width=10)
    t.add_column("description", overflow="fold")
    for it in items:
        state = (
            "[green]ingested[/]" if it["ingested"]
            else ("[yellow]ready[/]" if it["root_exists"] else "[dim]missing[/]")
        )
        t.add_row(it["name"], it["kind"], str(it["chunk_count"]), state, it["description"])
    console.print(t)


def _render_status(stats: dict, sources: list[dict], console) -> None:
    from rich.table import Table
    console.print(
        f"[bold]db:[/] {stats['db_path']}  "
        f"[dim]({stats['size_bytes'] / 1024:.1f} kB, "
        f"{stats['chunk_count']} chunks, "
        f"{stats['source_count']} sources)[/]"
    )
    if not sources:
        console.print("[dim](no sources ingested yet — run `kb ingest <name>`)[/]")
        return
    t = Table(show_header=True, header_style="bold", padding=(0, 1))
    t.add_column("source")
    t.add_column("kind", width=8)
    t.add_column("chunks", justify="right", width=8)
    t.add_column("last ingested")
    for s in sources:
        t.add_row(s["name"], s["kind"], str(s["chunks"]), s["last_ingested_at"] or "—")
    console.print(t)


def _render_hits(query: str, hits, console) -> None:
    from rich.table import Table
    t = Table(
        title=f'[bold]kb search[/] [dim]"{query}"[/]  ({len(hits)} hit{"s" if len(hits) != 1 else ""})',
        show_header=True,
        header_style="bold",
        title_justify="left",
        padding=(0, 1),
    )
    t.add_column("score", width=7, justify="right")
    t.add_column("source", width=18)
    t.add_column("path", overflow="fold", max_width=44)
    t.add_column("snippet", overflow="fold")
    for h in hits:
        t.add_row(
            f"{h.score:+.2f}",
            h.source,
            f"{h.path}#{h.chunk_index}",
            h.snippet,
        )
    console.print(t)


__all__ = ["register"]
