# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Research Engine, proprietary and private.

"""Stage 3: Normalize evidence across sub-question briefs (Haiku)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from ..budget import CostTracker
from ..caching import build_cached_system
from ..logging import extract_usage_tokens, get_logger
from ..models import NormalizedEvidence, ProgressEvent, SubQuestionBrief
from ..prompts import EXTRACT_SYSTEM, MASTER_FRAMING

_LOG = get_logger()
_MODEL = "claude-haiku-4-5-20251001"

SUBMIT_EVIDENCE_TOOL: dict[str, Any] = {
    "name": "submit_evidence",
    "description": "Submit normalized evidence with merged sources and flagged contradictions.",
    "input_schema": {
        "type": "object",
        "required": ["claims", "contradictions", "source_registry"],
        "properties": {
            "claims": {"type": "array", "items": {"type": "object"}},
            "contradictions": {"type": "array", "items": {"type": "object"}},
            "source_registry": {"type": "object"},
        },
    },
}


async def extract(
    *,
    client: Any,
    briefs: list[SubQuestionBrief],
    tracker: CostTracker,
    on_progress: Callable[[ProgressEvent], None] | None = None,
) -> NormalizedEvidence:
    stage_start = time.monotonic()
    if on_progress is not None:
        on_progress(ProgressEvent(stage="extract", status="started", elapsed_seconds=0.0))
    system_blocks = build_cached_system([("master", MASTER_FRAMING), ("stage", EXTRACT_SYSTEM)])
    payload = {"briefs": [b.model_dump(mode="json") for b in briefs]}
    user_prompt = (
        f"Normalize the following research briefs.\n\n```json\n{json.dumps(payload, indent=2)}\n```"
    )

    started = time.monotonic()
    response = await client.messages.create(
        model=_MODEL,
        max_tokens=4000,
        system=system_blocks,
        tools=[SUBMIT_EVIDENCE_TOOL],
        tool_choice={"type": "tool", "name": "submit_evidence"},
        messages=[{"role": "user", "content": user_prompt}],
    )
    duration = time.monotonic() - started

    tokens = extract_usage_tokens(response.usage)
    await tracker.record(
        model=_MODEL,
        stage="extract",
        duration_seconds=duration,
        **tokens,
    )

    tool_use = next(
        (
            b
            for b in response.content
            if getattr(b, "type", None) == "tool_use" and b.name == "submit_evidence"
        ),
        None,
    )
    if tool_use is None:
        raise ValueError("extract stage did not emit submit_evidence tool call")
    result = NormalizedEvidence.model_validate(tool_use.input)
    if on_progress is not None:
        on_progress(
            ProgressEvent(
                stage="extract",
                status="completed",
                elapsed_seconds=time.monotonic() - stage_start,
            )
        )
    return result
