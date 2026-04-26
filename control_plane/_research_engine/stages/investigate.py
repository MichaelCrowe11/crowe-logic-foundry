# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Research Engine, proprietary and private.

"""Stage 2: Parallel per-sub-question investigation with server web tools."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from ..budget import BudgetExceeded, CostTracker, estimate_cost_usd
from ..caching import build_cached_system
from ..logging import extract_usage_tokens, get_logger
from ..models import Priority, ProgressEvent, ResearchPlan, SubQuestion, SubQuestionBrief
from ..prompts import INVESTIGATE_SYSTEM, MASTER_FRAMING

_LOG = get_logger()
_MODEL = "claude-sonnet-4-6"

_DEPTH_TO_USES: dict[str, tuple[int, int]] = {
    "quick": (3, 2),
    "normal": (5, 3),
    "deep": (8, 5),
}


@dataclass(frozen=True)
class SkippedBranch:
    sub_question_id: str
    reason: str


SUBMIT_BRIEF_TOOL: dict[str, Any] = {
    "name": "submit_brief",
    "description": "Submit your findings for this sub-question.",
    "input_schema": {
        "type": "object",
        "required": ["sub_question_id", "confidence", "sources", "claims"],
        "properties": {
            "sub_question_id": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "sources": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "url", "title", "accessed_at", "tier"],
                    "properties": {
                        "id": {"type": "string"},
                        "url": {"type": "string"},
                        "title": {"type": "string"},
                        "accessed_at": {"type": "string", "format": "date-time"},
                        "tier": {
                            "type": "string",
                            "enum": ["primary", "secondary", "tertiary"],
                        },
                    },
                },
            },
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "text", "source_ids"],
                    "properties": {
                        "id": {"type": "string"},
                        "text": {"type": "string"},
                        "source_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "quote": {"type": ["string", "null"]},
                    },
                },
            },
        },
    },
}


def _web_tools(depth: str) -> list[dict[str, Any]]:
    search_uses, fetch_uses = _DEPTH_TO_USES[depth]
    # NOTE: confirm exact tool `type` strings against current Anthropic API
    # docs at implementation time. Shape below reflects the documented
    # server-tool contract at spec-writing time.
    return [
        {"type": "web_search_20250305", "name": "web_search", "max_uses": search_uses},
        {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": fetch_uses},
    ]


async def _investigate_one(
    *,
    client: Any,
    sub_q: SubQuestion,
    depth: str,
    plan_question: str,
    tracker: CostTracker,
    semaphore: asyncio.Semaphore,
    on_progress: Callable[[ProgressEvent], None] | None = None,
) -> SubQuestionBrief | SkippedBranch:
    branch_start = time.monotonic()
    # Rough pre-flight estimate: one stage call with depth-scaled budget.
    estimate = estimate_cost_usd(
        model=_MODEL,
        input_tokens=3000,
        output_tokens=1500,
        cache_read_tokens=1000,
        cache_creation_tokens=0,
    )
    if not tracker.can_afford(estimate):
        if sub_q.priority == Priority.MUST:
            raise BudgetExceeded(
                spent_usd=tracker.total_cost_usd,
                budget_usd=tracker.budget_usd or 0.0,
            )
        _LOG.info("investigate: skipping %s (%s) for budget", sub_q.id, sub_q.priority)
        if on_progress is not None:
            on_progress(
                ProgressEvent(
                    stage="investigate",
                    sub_question_id=sub_q.id,
                    status="skipped",
                    message="over budget",
                    elapsed_seconds=time.monotonic() - branch_start,
                )
            )
        return SkippedBranch(sub_question_id=sub_q.id, reason="over budget")

    if on_progress is not None:
        on_progress(
            ProgressEvent(
                stage="investigate",
                sub_question_id=sub_q.id,
                status="started",
                elapsed_seconds=time.monotonic() - branch_start,
            )
        )

    system_blocks = build_cached_system(
        [
            ("master", MASTER_FRAMING),
            ("stage", INVESTIGATE_SYSTEM),
        ]
    )
    user_prompt = (
        f"Overall research question: {plan_question}\n\n"
        f"Sub-question to research: {sub_q.text}\n"
        f"Sub-question id: {sub_q.id}\n"
        f"Search hints (optional starting points): " + ", ".join(sub_q.search_hints)
    )

    async with semaphore:
        started = time.monotonic()
        response = await client.messages.create(
            model=_MODEL,
            max_tokens=3000,
            system=system_blocks,
            tools=[*_web_tools(depth), SUBMIT_BRIEF_TOOL],
            messages=[{"role": "user", "content": user_prompt}],
        )
        duration = time.monotonic() - started

    tokens = extract_usage_tokens(response.usage)
    await tracker.record(
        model=_MODEL,
        stage=f"investigate:{sub_q.id}",
        duration_seconds=duration,
        **tokens,
    )

    tool_use = next(
        (
            b
            for b in response.content
            if getattr(b, "type", None) == "tool_use" and b.name == "submit_brief"
        ),
        None,
    )
    if tool_use is None:
        _LOG.warning("investigate: %s returned no submit_brief tool call", sub_q.id)
        brief = SubQuestionBrief(
            sub_question_id=sub_q.id,
            claims=[],
            sources=[],
            confidence=0.0,
            error="no submit_brief tool call",
        )
        if on_progress is not None:
            on_progress(
                ProgressEvent(
                    stage="investigate",
                    sub_question_id=sub_q.id,
                    status="failed",
                    message=brief.error,
                    elapsed_seconds=time.monotonic() - branch_start,
                )
            )
        return brief

    brief = SubQuestionBrief.model_validate(tool_use.input)
    if brief.error:
        if on_progress is not None:
            on_progress(
                ProgressEvent(
                    stage="investigate",
                    sub_question_id=sub_q.id,
                    status="failed",
                    message=brief.error,
                    elapsed_seconds=time.monotonic() - branch_start,
                )
            )
    else:
        if on_progress is not None:
            on_progress(
                ProgressEvent(
                    stage="investigate",
                    sub_question_id=sub_q.id,
                    status="completed",
                    elapsed_seconds=time.monotonic() - branch_start,
                )
            )
    return brief


async def investigate(
    *,
    client: Any,
    plan: ResearchPlan,
    depth: Literal["quick", "normal", "deep"],
    tracker: CostTracker,
    max_concurrent: int = 3,
    on_progress: Callable[[ProgressEvent], None] | None = None,
) -> tuple[list[SubQuestionBrief], list[SkippedBranch]]:
    stage_start = time.monotonic()
    if on_progress is not None:
        on_progress(
            ProgressEvent(
                stage="investigate",
                status="started",
                elapsed_seconds=0.0,
            )
        )
    semaphore = asyncio.Semaphore(max_concurrent)
    coros = [
        _investigate_one(
            client=client,
            sub_q=sq,
            depth=depth,
            plan_question=plan.question,
            tracker=tracker,
            semaphore=semaphore,
            on_progress=on_progress,
        )
        for sq in plan.sub_questions
    ]
    results = await asyncio.gather(*coros)
    briefs: list[SubQuestionBrief] = []
    skipped: list[SkippedBranch] = []
    for r in results:
        if isinstance(r, SkippedBranch):
            skipped.append(r)
        else:
            briefs.append(r)
    if on_progress is not None:
        on_progress(
            ProgressEvent(
                stage="investigate",
                status="completed",
                elapsed_seconds=time.monotonic() - stage_start,
            )
        )
    return briefs, skipped
