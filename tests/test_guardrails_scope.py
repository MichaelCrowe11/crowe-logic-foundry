"""Tests for cli.guardrails.scope."""
from __future__ import annotations

import pytest

from cli.guardrails.scope import ScopeBudget


@pytest.fixture
def budget() -> ScopeBudget:
    return ScopeBudget()


def test_eclipse_2026_04_30_incident_triggers_interrupt(budget: ScopeBudget) -> None:
    """Reproduce the actual ratio from the failure transcript."""
    decision = budget.evaluate(reasoning_tokens=5856, output_tokens=698)
    assert decision.verdict == "INTERRUPT"
    assert decision.ratio > 5.0


def test_small_task_high_ratio_does_not_interrupt(budget: ScopeBudget) -> None:
    """Short answers can have high ratios legitimately. Don't fire on them."""
    decision = budget.evaluate(reasoning_tokens=1000, output_tokens=50)
    assert decision.verdict != "INTERRUPT"


def test_within_budget_returns_ok(budget: ScopeBudget) -> None:
    decision = budget.evaluate(reasoning_tokens=200, output_tokens=400)
    assert decision.verdict == "OK"


def test_warn_threshold(budget: ScopeBudget) -> None:
    decision = budget.evaluate(reasoning_tokens=15_000, output_tokens=2_000)
    assert decision.verdict in {"WARN", "INTERRUPT"}


def test_hard_cap_always_interrupts(budget: ScopeBudget) -> None:
    decision = budget.evaluate(reasoning_tokens=40_000, output_tokens=20_000)
    assert decision.verdict == "INTERRUPT"


def test_ratio_calculation(budget: ScopeBudget) -> None:
    decision = budget.evaluate(reasoning_tokens=2000, output_tokens=1000)
    assert decision.ratio == pytest.approx(2.0)


def test_zero_output_tokens_with_reasoning_is_infinite_ratio(budget: ScopeBudget) -> None:
    decision = budget.evaluate(reasoning_tokens=100, output_tokens=0)
    assert decision.ratio == float("inf")


def test_zero_zero_is_ok(budget: ScopeBudget) -> None:
    decision = budget.evaluate(reasoning_tokens=0, output_tokens=0)
    assert decision.verdict == "OK"


def test_custom_ratio_threshold() -> None:
    """Tighter limits trigger sooner."""
    strict = ScopeBudget(max_reasoning_to_output_ratio=2.0, min_output_tokens_for_ratio=100)
    decision = strict.evaluate(reasoning_tokens=1000, output_tokens=300)
    assert decision.verdict == "INTERRUPT"


def test_interrupt_prompt_is_actionable() -> None:
    prompt = ScopeBudget.interrupt_prompt()
    assert "summarize" in prompt.lower() or "summarise" in prompt.lower()
    assert "user" in prompt.lower()
