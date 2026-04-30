"""
Pipeline glue for wiring the GuardrailChain into the streaming renderer.

The chain itself lives in cli.guardrails. This module is the thin adapter
that the renderer and session_runtime call. Keeping the integration here
(rather than in renderer.py) means renderer.py is unmodified at integration
time; the change to renderer.py is a single import + a few call sites.

Public entry points:
    pipeline_for_session(session_id, ...) - factory returning a configured
        GuardrailChain plus telemetry hooks suitable for a single user turn.
    apply_to_block(text, chain) - block-level wrapper for the final answer.
    apply_to_stream(chunk, chain) - per-chunk wrapper for token streams.
    record_tool_call(call, chain) - intercept tool calls; returns either
        the original call to proceed, or a redacted refusal.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cli.guardrails import (
    GuardrailChain,
    GuardrailEvent,
    PathPolicy,
    ScopeBudget,
    SecretScrubber,
    StyleEnforcer,
)


@dataclass
class PipelineToolDecision:
    """The decision returned for a tool call. The caller proceeds or refuses."""

    proceed: bool
    refusal_reason: str = ""
    sanitized_args: dict[str, Any] | None = None


def pipeline_for_session(
    project_root: Path | None = None,
    user_provided_paths: frozenset[str] = frozenset(),
    rewrite_em_dash: bool = True,
    strip_emoji: bool = False,
    max_reasoning_to_output_ratio: float = 5.0,
) -> GuardrailChain:
    """Build a GuardrailChain configured for the active session.

    The renderer calls this once per turn. Per-turn lifecycle keeps the chain's
    `events` list scoped to a single user message.
    """
    return GuardrailChain(
        secrets=SecretScrubber(),
        style=StyleEnforcer(rewrite_em_dash=rewrite_em_dash, strip_emoji=strip_emoji),
        paths=PathPolicy(
            user_provided_paths=user_provided_paths,
            project_root=project_root,
        ),
        budget=ScopeBudget(max_reasoning_to_output_ratio=max_reasoning_to_output_ratio),
    )


def apply_to_block(text: str, chain: GuardrailChain) -> str:
    """Final-answer scrub. Use this for non-streaming completions."""
    return chain.scrub_output(text)


def apply_to_stream(chunk: str, chain: GuardrailChain) -> str:
    """Per-chunk scrub for streaming output. Returns safe-to-emit prefix."""
    return chain.stream(chunk)


def flush_stream(chain: GuardrailChain) -> str:
    """Flush remaining buffered output at end of stream."""
    return chain.flush_stream()


_PATH_KEYS = ("file_path", "path", "filename", "target", "destination")
_WRITE_TOOLS = {"Write", "write", "Edit", "edit", "edit_file", "create_file", "save"}


def record_tool_call(
    tool_name: str, args: dict[str, Any], chain: GuardrailChain
) -> PipelineToolDecision:
    """Apply path policy to a tool call before it executes.

    Returns:
        PipelineToolDecision(proceed=True) if allowed.
        PipelineToolDecision(proceed=False, refusal_reason=...) on DENY.
        PipelineToolDecision(proceed=True) with chain.events containing
        path-confirm-required for REQUIRE_CONFIRM (caller decides UX).
    """
    if tool_name not in _WRITE_TOOLS:
        return PipelineToolDecision(proceed=True)

    candidate_path: str | None = None
    for key in _PATH_KEYS:
        if key in args:
            candidate_path = str(args[key])
            break
    if candidate_path is None:
        return PipelineToolDecision(proceed=True)

    decision = chain.check_path(candidate_path)
    if decision.verdict == "DENY":
        return PipelineToolDecision(
            proceed=False,
            refusal_reason=(
                f"Refusing {tool_name} to {decision.path}: {decision.reason}"
            ),
        )
    return PipelineToolDecision(proceed=True)


def telemetry_summary(chain: GuardrailChain) -> dict[str, Any]:
    """Per-turn rollup of guardrail events for telemetry / CSEP emission."""
    events = chain.events
    return {
        "total_events": len(events),
        "by_code": _count_by(events, "code"),
        "by_severity": _count_by(events, "severity"),
        "blocked_paths": [
            e.detail.get("path") for e in events if e.code == "path-denied"
        ],
        "redacted_secrets": [
            e.detail.get("label") for e in events if e.code == "secret-redacted"
        ],
        "scope_budget_exceeded": any(
            e.code == "scope-budget-exceeded" for e in events
        ),
    }


def _count_by(events: list[GuardrailEvent], attr: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        key = getattr(event, attr)
        counts[key] = counts.get(key, 0) + 1
    return counts
