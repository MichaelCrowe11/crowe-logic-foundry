"""
CroweLM eval harness.

Replays user turns against any variant under any guardrail/prompt config,
scores against a rubric of failure modes derived from real session
transcripts, and emits a delta report.

Public surface:
    eval.rubric  - per-metric scorers and the aggregate rubric runner
    eval.replay  - transcript replay harness
"""
from eval.rubric import (
    METRIC_REGISTRY,
    Metric,
    MetricResult,
    Rubric,
    RubricReport,
    score_transcript,
)

__all__ = [
    "METRIC_REGISTRY",
    "Metric",
    "MetricResult",
    "Rubric",
    "RubricReport",
    "score_transcript",
]
