# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Research Engine, proprietary and private.

"""Stage 4: Synthesize the final markdown report."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from ..budget import CostTracker
from ..caching import build_cached_system
from ..logging import extract_usage_tokens, get_logger
from ..models import NormalizedEvidence, ProgressEvent, ResearchPlan
from ..prompts import MASTER_FRAMING, SYNTHESIZE_SYSTEM

_LOG = get_logger()
_MODEL = "claude-sonnet-4-6"


async def synthesize(
    *,
    client: Any,
    plan: ResearchPlan,
    evidence: NormalizedEvidence,
    tracker: CostTracker,
    on_progress: Callable[[ProgressEvent], None] | None = None,
) -> str:
    stage_start = time.monotonic()
    if on_progress is not None:
        on_progress(ProgressEvent(stage="synthesize", status="started", elapsed_seconds=0.0))
    system_blocks = build_cached_system([("master", MASTER_FRAMING), ("stage", SYNTHESIZE_SYSTEM)])
    user_prompt = (
        f"Research question: {plan.question}\n\n"
        f"Research plan:\n```json\n{plan.model_dump_json(indent=2)}\n```\n\n"
        f"Normalized evidence:\n```json\n{evidence.model_dump_json(indent=2)}\n```\n\n"
        "Write the report now."
    )

    started = time.monotonic()
    response = await client.messages.create(
        model=_MODEL,
        max_tokens=8000,
        system=system_blocks,
        messages=[{"role": "user", "content": user_prompt}],
    )
    duration = time.monotonic() - started

    tokens = extract_usage_tokens(response.usage)
    await tracker.record(
        model=_MODEL,
        stage="synthesize",
        duration_seconds=duration,
        **tokens,
    )

    body_parts = [
        getattr(b, "text", "") for b in response.content if getattr(b, "type", None) == "text"
    ]
    body = "\n".join(p for p in body_parts if p).strip()
    if not body:
        raise ValueError("synthesize stage returned empty body")
    if on_progress is not None:
        on_progress(
            ProgressEvent(
                stage="synthesize",
                status="completed",
                elapsed_seconds=time.monotonic() - stage_start,
            )
        )
    return body
