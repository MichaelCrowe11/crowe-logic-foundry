# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Research Engine, proprietary and private.

"""Top-level orchestration of the four-stage research pipeline."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from typing import Literal

from anthropic import AsyncAnthropic

from .budget import BudgetExceeded, CostTracker
from .logging import get_logger
from .models import (
    NormalizedEvidence,
    ProgressEvent,
    Report,
    Usage,
)
from .stages.decompose import decompose
from .stages.extract import extract
from .stages.investigate import investigate
from .stages.synthesize import synthesize
from .traces import capture_trace

_LOG = get_logger()

Depth = Literal["quick", "normal", "deep"]


def _safe_emit(on_progress: Callable[[ProgressEvent], None] | None, event: ProgressEvent) -> None:
    if on_progress is None:
        return
    try:
        on_progress(event)
    except Exception as e:
        _LOG.warning("on_progress callback raised: %s", e)


class ResearchError(Exception):
    def __init__(
        self,
        message: str,
        *,
        reason: str,
        partial_report: Report | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.partial_report = partial_report


async def research(
    question: str,
    *,
    depth: Depth = "normal",
    budget_usd: float | None = None,
    max_concurrent: int | None = None,
    on_progress: Callable[[ProgressEvent], None] | None = None,
) -> Report:
    if budget_usd is None:
        env_cap = os.environ.get("RESEARCH_MAX_USD")
        if env_cap:
            budget_usd = float(env_cap)

    if max_concurrent is None:
        env_concur = os.environ.get("RESEARCH_MAX_CONCURRENT")
        max_concurrent = int(env_concur) if env_concur else 3

    tracker = CostTracker(budget_usd=budget_usd)
    client = AsyncAnthropic()
    started = time.monotonic()

    def _emit(stage: str, status: str, elapsed: float) -> None:
        _safe_emit(
            on_progress,
            ProgressEvent(
                stage=stage,  # type: ignore[arg-type]
                status=status,  # type: ignore[arg-type]
                elapsed_seconds=elapsed,
            ),
        )

    _emit("decompose", "started", 0.0)
    try:
        plan = await decompose(
            client=client,
            question=question,
            depth=depth,
            tracker=tracker,
            on_progress=on_progress,
        )
    except Exception as e:
        raise ResearchError(f"Decompose stage failed: {e}", reason="decompose_failed") from e
    _emit("decompose", "completed", time.monotonic() - started)

    _emit("investigate", "started", time.monotonic() - started)
    try:
        briefs, skipped = await investigate(
            client=client,
            plan=plan,
            depth=depth,
            tracker=tracker,
            max_concurrent=max_concurrent,
            on_progress=on_progress,
        )
    except BudgetExceeded as e:
        raise ResearchError(
            str(e),
            reason="budget_exceeded",
        ) from e
    _emit("investigate", "completed", time.monotonic() - started)
    if not briefs:
        raise ResearchError("investigation returned no briefs", reason="no_briefs")

    if len(briefs) == 1 and depth == "quick":
        # Skip extract for trivial runs: just pass a synthesized evidence.
        evidence = NormalizedEvidence(
            claims=briefs[0].claims,
            contradictions=[],
            source_registry={s.id: s for s in briefs[0].sources},
        )
    else:
        _emit("extract", "started", time.monotonic() - started)
        try:
            evidence = await extract(
                client=client,
                briefs=briefs,
                tracker=tracker,
                on_progress=on_progress,
            )
            _emit("extract", "completed", time.monotonic() - started)
        except Exception as e:
            _LOG.warning("extract failed, falling back to raw merge: %s", e)
            merged_claims = [c for b in briefs for c in b.claims]
            merged_sources = {s.id: s for b in briefs for s in b.sources}
            evidence = NormalizedEvidence(
                claims=merged_claims,
                contradictions=[],
                source_registry=merged_sources,
            )

    _emit("synthesize", "started", time.monotonic() - started)
    body = await synthesize(
        client=client,
        plan=plan,
        evidence=evidence,
        tracker=tracker,
        on_progress=on_progress,
    )
    _emit("synthesize", "completed", time.monotonic() - started)

    total_duration = time.monotonic() - started
    gaps: list[str] = []
    for s in skipped:
        gaps.append(f"Skipped sub-question {s.sub_question_id}: {s.reason}")
    for b in briefs:
        if b.error:
            gaps.append(f"{b.sub_question_id}: {b.error}")

    report = Report(
        question=question,
        body_markdown=body,
        sources=list(evidence.source_registry.values()),
        contradictions=evidence.contradictions,
        confidence_gaps=gaps,
        usage=Usage(
            stages=list(tracker.stage_usages),
            total_cost_usd=tracker.total_cost_usd,
            total_duration_seconds=total_duration,
        ),
    )
    capture_trace(
        question=question,
        depth=depth,
        budget_usd=budget_usd,
        plan=plan,
        briefs=briefs,
        evidence=evidence,
        report=report,
    )
    return report


def research_sync(
    question: str,
    *,
    depth: Depth = "normal",
    budget_usd: float | None = None,
    max_concurrent: int | None = None,
    on_progress: Callable[[ProgressEvent], None] | None = None,
) -> Report:
    return asyncio.run(
        research(
            question,
            depth=depth,
            budget_usd=budget_usd,
            max_concurrent=max_concurrent,
            on_progress=on_progress,
        )
    )
