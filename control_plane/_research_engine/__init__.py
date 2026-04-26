# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Research Engine, proprietary and private.

"""Crowe Research Engine: staged, cached, parallel research agent."""

from .agent import ResearchError, research, research_sync
from .models import (
    Claim,
    Contradiction,
    NormalizedEvidence,
    Priority,
    ProgressEvent,
    Report,
    ResearchPlan,
    Source,
    SourceTier,
    StageUsage,
    SubQuestion,
    SubQuestionBrief,
    Usage,
)

__all__ = [
    "Claim",
    "Contradiction",
    "NormalizedEvidence",
    "Priority",
    "ProgressEvent",
    "Report",
    "ResearchError",
    "ResearchPlan",
    "Source",
    "SourceTier",
    "StageUsage",
    "SubQuestion",
    "SubQuestionBrief",
    "Usage",
    "research",
    "research_sync",
]
