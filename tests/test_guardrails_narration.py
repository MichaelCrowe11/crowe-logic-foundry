"""Tests for cli.guardrails.narration."""
from __future__ import annotations

import pytest

from cli.guardrails.narration import ReasoningNarrationDetector


@pytest.fixture
def detector() -> ReasoningNarrationDetector:
    return ReasoningNarrationDetector()


def test_talon_2026_04_30_reasoning_is_detected_as_narration(
    detector: ReasoningNarrationDetector,
) -> None:
    """The actual reasoning prefix from the Talon transcript."""
    talon_reasoning = (
        "We need to interpret. We have just demonstrated parallel work with "
        "Azure agent and DeepParallel. The user now asks 'what is need in our "
        "underlying architecture?' We need to clarify. However, per execution "
        "discipline, we should not narrate intent; we should use tools if "
        "needed. Let me think about what we should do. Let's use DeepParallel "
        "to reason. Let's invoke deepparallel_query with a prompt. "
        "We'll do both in parallel."
    )
    report = detector.scan(talon_reasoning)
    assert report.total_hits >= 8
    assert report.hits_per_1k_chars > 10
    assert "first_person_plural" in report.by_label
    assert "imperative_to_self" in report.by_label


def test_clean_reasoning_passes(detector: ReasoningNarrationDetector) -> None:
    """A model that just states facts should not trigger."""
    clean = (
        "The repository contains a Quality Stack module at cli/guardrails/. "
        "It exposes SecretScrubber, StyleEnforcer, PathPolicy, ScopeBudget. "
        "92 tests pass on the current commit. The Eclipse seed transcript "
        "scores 0.668 against the rubric."
    )
    report = detector.scan(clean)
    assert report.total_hits == 0
    assert report.hits_per_1k_chars == 0.0


def test_first_person_plural_caught(detector: ReasoningNarrationDetector) -> None:
    text = "We need to do this. We should also check that. We can run a tool."
    report = detector.scan(text)
    assert report.by_label.get("first_person_plural", 0) >= 3


def test_imperative_to_self_caught(detector: ReasoningNarrationDetector) -> None:
    text = "Let's check the file. Let me read it. I'll then verify."
    report = detector.scan(text)
    assert report.by_label.get("imperative_to_self", 0) >= 3


def test_second_thought_caught(detector: ReasoningNarrationDetector) -> None:
    text = (
        "I will run the test. Wait, actually, I should check the file first. "
        "Hmm, on second thought, let me reconsider."
    )
    report = detector.scan(text)
    assert report.by_label.get("second_thought", 0) >= 2


def test_planning_phrase_caught(detector: ReasoningNarrationDetector) -> None:
    text = "My plan is the following. First, I'll read the file. Next, I'll edit it."
    report = detector.scan(text)
    assert report.by_label.get("planning_phrase", 0) >= 1


def test_hedge_to_action_caught(detector: ReasoningNarrationDetector) -> None:
    text = "Maybe I should run the test. Perhaps we could check the logs."
    report = detector.scan(text)
    assert report.by_label.get("hedge_to_action", 0) >= 2


def test_density_per_1k_chars(detector: ReasoningNarrationDetector) -> None:
    """Same number of hits, different text length, different density."""
    short = "We need to act. We should act."
    long = "We need to act. " + ("filler text. " * 100) + " We should act."
    short_report = detector.scan(short)
    long_report = detector.scan(long)
    assert short_report.hits_per_1k_chars > long_report.hits_per_1k_chars


def test_samples_truncated_to_5(detector: ReasoningNarrationDetector) -> None:
    text = "We need to act. " * 20
    report = detector.scan(text)
    assert len(report.samples) <= 5


def test_empty_input_clean(detector: ReasoningNarrationDetector) -> None:
    report = detector.scan("")
    assert report.total_hits == 0
    assert report.chars_scanned == 0


def test_chain_records_narration_event_on_threshold() -> None:
    """High-density reasoning produces a chain event."""
    from cli.guardrails import GuardrailChain

    chain = GuardrailChain()
    talon_reasoning = "We need to. We should. We could. Let's go. Let me think. " * 5
    report = chain.scan_reasoning(talon_reasoning, threshold_per_1k=5.0)
    assert report.hits_per_1k_chars >= 5.0
    codes = {e.code for e in chain.events}
    assert "reasoning-narration-detected" in codes


def test_chain_no_event_below_threshold() -> None:
    from cli.guardrails import GuardrailChain

    chain = GuardrailChain()
    sparse_reasoning = "We need to act." + " filler. " * 200
    chain.scan_reasoning(sparse_reasoning, threshold_per_1k=10.0)
    codes = {e.code for e in chain.events}
    assert "reasoning-narration-detected" not in codes
