# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Research Engine, proprietary and private.

"""Typer CLI for the crowe-research-engine package."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from threading import Lock

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.live import Live
from rich.table import Table

from .agent import research
from .models import ProgressEvent, Report

app = typer.Typer(add_completion=False, help="Crowe Research Engine.")
_console = Console(stderr=True)


class _ProgressTracker:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str | None], tuple[str, float]] = {}
        self._lock = Lock()

    def update(self, event: ProgressEvent) -> None:
        with self._lock:
            key = (event.stage, event.sub_question_id)
            self._rows[key] = (event.status, event.elapsed_seconds)

    def render(self) -> Table:
        table = Table(title="Crowe Research Engine", title_style="bold")
        table.add_column("Stage")
        table.add_column("Sub-question")
        table.add_column("Status")
        table.add_column("Elapsed")
        with self._lock:
            for (stage, sq), (status, elapsed) in self._rows.items():
                table.add_row(stage, sq or "", status, f"{elapsed:.1f}s")
        return table


def _render_report(report: Report) -> str:
    cost = report.usage.total_cost_usd
    duration = report.usage.total_duration_seconds
    n_stages = len(report.usage.stages)
    parts = [
        "# Crowe Research Engine",
        "",
        "Prepared by Crowe Research Engine, Crowe Logic, Inc.",
        "",
        "## Question",
        "",
        report.question,
        "",
        "## Report",
        "",
        report.body_markdown.rstrip(),
        "",
        "---",
        "",
        "## Sources",
        "",
    ]
    for src in report.sources:
        parts.append(f"- [{src.id}] {src.title} ({src.tier.value}): {src.url}")
    if report.confidence_gaps:
        parts.extend(["", "## Confidence and Gaps", ""])
        for gap in report.confidence_gaps:
            parts.append(f"- {gap}")
    parts.extend(
        [
            "",
            "---",
            "",
            "Produced by Crowe Research Engine. Crowe Logic, Inc., 2026.",
            "For custom research engagements, contact michael@crowelogic.com.",
            "",
            f"_Cost: ${cost:.4f}, duration: {duration:.1f}s, stages: {n_stages}_",
        ]
    )
    return "\n".join(parts) + "\n"


@app.command()
def main(
    question: str = typer.Argument(..., help="The research question."),
    depth: str = typer.Option("normal", help="quick | normal | deep"),
    out: Path | None = typer.Option(None, help="Write report to file instead of stdout."),  # noqa: B008
    budget: float | None = typer.Option(None, help="Hard cost ceiling in USD."),
    json_out: Path | None = typer.Option(  # noqa: B008
        None, "--json", help="Also dump the full typed Report as JSON."
    ),
    max_concurrent: int = typer.Option(3, help="Stage-2 parallel branches."),
    quiet: bool = typer.Option(False, help="Suppress progress logs."),
) -> None:
    load_dotenv()
    if depth not in ("quick", "normal", "deep"):
        typer.echo(f"Invalid depth: {depth}", err=True)
        raise typer.Exit(code=2)
    if not quiet:
        _console.print(f"[bold]Researching:[/bold] {question}")
        _console.print(f"Depth: {depth}  Budget: {budget or 'none'}")
        tracker_view = _ProgressTracker()
        with Live(tracker_view.render(), console=_console, refresh_per_second=4) as live:

            def on_prog(event: ProgressEvent) -> None:
                tracker_view.update(event)
                live.update(tracker_view.render())

            report = asyncio.run(
                research(
                    question,
                    depth=depth,  # type: ignore[arg-type]
                    budget_usd=budget,
                    max_concurrent=max_concurrent,
                    on_progress=on_prog,
                )
            )
    else:
        report = asyncio.run(
            research(
                question,
                depth=depth,  # type: ignore[arg-type]
                budget_usd=budget,
                max_concurrent=max_concurrent,
            )
        )
    rendered = _render_report(report)
    if out:
        out.write_text(rendered)
        if not quiet:
            _console.print(f"[green]Wrote[/green] {out}")
    else:
        sys.stdout.write(rendered)
    if json_out:
        json_out.write_text(report.model_dump_json(indent=2))
        if not quiet:
            _console.print(f"[green]Wrote JSON[/green] {json_out}")
