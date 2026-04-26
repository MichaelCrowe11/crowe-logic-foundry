# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Research Engine, proprietary and private.

"""Structured trace capture.

Every research call writes one JSONL line with the question, full report,
plan, briefs, evidence, and usage accounting. Traces are the training
corpus for future fine-tuned models. Never block a research call on a
trace write failure.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .logging import get_logger
from .models import NormalizedEvidence, Report, ResearchPlan, SubQuestionBrief

_LOG = get_logger()


def trace_dir() -> Path:
    override = os.environ.get("CROWE_RESEARCH_TRACE_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".crowe-research" / "traces"


def capture_trace(
    *,
    question: str,
    depth: str,
    budget_usd: float | None,
    plan: ResearchPlan,
    briefs: list[SubQuestionBrief],
    evidence: NormalizedEvidence,
    report: Report,
) -> Path | None:
    """Append one JSONL row for this research call. Never raises."""
    try:
        root = trace_dir()
        root.mkdir(parents=True, exist_ok=True)
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")  # noqa: UP017
        path = root / f"{day}.jsonl"
        row: dict[str, Any] = {
            "captured_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
            "schema_version": 1,
            "question": question,
            "depth": depth,
            "budget_usd": budget_usd,
            "plan": plan.model_dump(mode="json"),
            "briefs": [b.model_dump(mode="json") for b in briefs],
            "evidence": evidence.model_dump(mode="json"),
            "report": report.model_dump(mode="json"),
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
        return path
    except Exception as e:
        _LOG.warning("trace capture failed: %s", e)
        return None
