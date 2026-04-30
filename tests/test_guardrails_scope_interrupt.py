"""Tests for the mid-stream scope-budget interrupt path."""
from __future__ import annotations

import pytest

from cli.guardrails import GuardrailChain, ScopeBudget, ScopeBudgetExceeded


def test_evaluate_or_raise_returns_decision_when_ok() -> None:
    budget = ScopeBudget()
    decision = budget.evaluate_or_raise(reasoning_tokens=100, output_tokens=200)
    assert decision.verdict == "OK"


def test_evaluate_or_raise_returns_decision_when_warn() -> None:
    budget = ScopeBudget()
    # 15k reasoning at 4k output gives ratio 3.75 (under 5.0 INTERRUPT
    # threshold) but exceeds the 12k warn threshold.
    decision = budget.evaluate_or_raise(reasoning_tokens=15_000, output_tokens=4_000)
    assert decision.verdict == "WARN"


def test_evaluate_or_raise_raises_on_interrupt() -> None:
    budget = ScopeBudget()
    with pytest.raises(ScopeBudgetExceeded) as exc_info:
        budget.evaluate_or_raise(reasoning_tokens=5856, output_tokens=698)
    assert exc_info.value.decision.verdict == "INTERRUPT"
    assert "ratio" in exc_info.value.decision.reason or "8" in exc_info.value.decision.reason


def test_eclipse_2026_04_30_would_have_interrupted() -> None:
    """The Eclipse incident: ratio 8.39x. Mid-stream interrupt would fire."""
    budget = ScopeBudget()
    with pytest.raises(ScopeBudgetExceeded):
        budget.evaluate_or_raise(reasoning_tokens=5856, output_tokens=698)


def test_chain_check_budget_with_raise_flag() -> None:
    chain = GuardrailChain()
    with pytest.raises(ScopeBudgetExceeded):
        chain.check_budget(reasoning_tokens=5856, output_tokens=698, raise_on_interrupt=True)


def test_chain_check_budget_default_records_event() -> None:
    """Without raise_on_interrupt, an event is recorded but no exception."""
    chain = GuardrailChain()
    decision = chain.check_budget(reasoning_tokens=5856, output_tokens=698)
    assert decision.verdict == "INTERRUPT"
    codes = {e.code for e in chain.events}
    assert "scope-budget-exceeded" in codes


def test_interrupt_prompt_is_actionable() -> None:
    prompt = ScopeBudget.interrupt_prompt()
    assert "summarize" in prompt.lower()
    assert "user" in prompt.lower() or "request" in prompt.lower()
    assert "stop" in prompt.lower() or "do not" in prompt.lower()
