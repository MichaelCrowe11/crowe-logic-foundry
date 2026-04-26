# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Research Engine, proprietary and private.

"""Stage 1: Decompose a research question into typed sub-questions."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Literal

from ..budget import CostTracker
from ..caching import build_cached_system
from ..logging import extract_usage_tokens, get_logger
from ..models import ProgressEvent, ResearchPlan
from ..prompts import DECOMPOSE_SYSTEM, MASTER_FRAMING

_LOG = get_logger()

_MODEL = "claude-sonnet-4-6"

_DEPTH_TO_COUNT: dict[str, tuple[int, int]] = {
    "quick": (3, 3),
    "normal": (4, 5),
    "deep": (6, 7),
}

SUBMIT_PLAN_TOOL: dict[str, Any] = {
    "name": "submit_plan",
    "description": "Submit the sub-question decomposition for this research question.",
    "input_schema": {
        "type": "object",
        "required": ["strategy", "sub_questions"],
        "properties": {
            "strategy": {
                "type": "string",
                "description": "One or two sentences on the overall research angle.",
            },
            "sub_questions": {
                "type": "array",
                "minItems": 3,
                "maxItems": 7,
                "items": {
                    "type": "object",
                    "required": ["id", "text", "search_hints", "priority"],
                    "properties": {
                        "id": {"type": "string"},
                        "text": {"type": "string"},
                        "search_hints": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "maxItems": 3,
                        },
                        "priority": {
                            "type": "string",
                            "enum": ["must", "should", "nice"],
                        },
                    },
                },
            },
        },
    },
}


async def decompose(
    *,
    client: Any,
    question: str,
    depth: Literal["quick", "normal", "deep"],
    tracker: CostTracker,
    on_progress: Callable[[ProgressEvent], None] | None = None,
) -> ResearchPlan:
    stage_start = time.monotonic()
    if on_progress is not None:
        on_progress(ProgressEvent(stage="decompose", status="started", elapsed_seconds=0.0))
    min_count, max_count = _DEPTH_TO_COUNT[depth]
    user_prompt = (
        f"Research question: {question}\n\n"
        f"Produce between {min_count} and {max_count} sub-questions."
    )

    system_blocks = build_cached_system(
        [
            ("master", MASTER_FRAMING),
            ("stage", DECOMPOSE_SYSTEM),
        ]
    )

    started = time.monotonic()
    response = await client.messages.create(
        model=_MODEL,
        max_tokens=1500,
        system=system_blocks,
        tools=[SUBMIT_PLAN_TOOL],
        tool_choice={"type": "tool", "name": "submit_plan"},
        messages=[{"role": "user", "content": user_prompt}],
    )
    duration = time.monotonic() - started

    tokens = extract_usage_tokens(response.usage)
    await tracker.record(
        model=_MODEL,
        stage="decompose",
        duration_seconds=duration,
        **tokens,
    )

    tool_use = next(
        (
            b
            for b in response.content
            if getattr(b, "type", None) == "tool_use" and b.name == "submit_plan"
        ),
        None,
    )
    if tool_use is None:
        raise ValueError("decompose stage did not emit submit_plan tool call")

    payload = tool_use.input
    plan = ResearchPlan.model_validate(
        {
            "question": question,
            "sub_questions": payload["sub_questions"],
            "strategy": payload["strategy"],
        }
    )

    n = len(plan.sub_questions)
    if not (min_count <= n <= max_count):
        raise ValueError(f"decompose: expected {min_count} to {max_count} sub-questions, got {n}")

    _LOG.info("decompose: %d sub-questions, %.2fs", len(plan.sub_questions), duration)
    if on_progress is not None:
        on_progress(
            ProgressEvent(
                stage="decompose",
                status="completed",
                elapsed_seconds=time.monotonic() - stage_start,
            )
        )
    return plan
