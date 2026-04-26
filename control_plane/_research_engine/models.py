# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Research Engine, proprietary and private.

"""Typed data contracts for the deep-researcher pipeline."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Priority(StrEnum):
    MUST = "must"
    SHOULD = "should"
    NICE = "nice"


class SourceTier(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    TERTIARY = "tertiary"


class SubQuestion(_Base):
    id: str
    text: str
    search_hints: list[str] = Field(default_factory=list)
    priority: Priority


class ResearchPlan(_Base):
    question: str
    sub_questions: list[SubQuestion]
    strategy: str


class Source(_Base):
    id: str
    url: str
    title: str
    accessed_at: datetime
    tier: SourceTier


class Claim(_Base):
    id: str
    text: str
    source_ids: list[str]
    quote: str | None = None


class SubQuestionBrief(_Base):
    sub_question_id: str
    claims: list[Claim]
    sources: list[Source]
    confidence: float
    error: str | None = None


class Contradiction(_Base):
    claim_a_id: str
    claim_b_id: str
    summary: str


class NormalizedEvidence(_Base):
    claims: list[Claim]
    contradictions: list[Contradiction]
    source_registry: dict[str, Source]


class StageUsage(_Base):
    stage: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    cost_usd: float
    duration_seconds: float


class Usage(_Base):
    stages: list[StageUsage]
    total_cost_usd: float
    total_duration_seconds: float


class Report(_Base):
    question: str
    body_markdown: str
    sources: list[Source]
    contradictions: list[Contradiction]
    confidence_gaps: list[str]
    usage: Usage


class ProgressEvent(_Base):
    stage: Literal["decompose", "investigate", "extract", "synthesize"]
    sub_question_id: str | None = None
    status: Literal["started", "progress", "completed", "skipped", "failed"]
    message: str | None = None
    elapsed_seconds: float
