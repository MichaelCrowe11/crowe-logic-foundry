"""Tests for cli.guardrails.style."""
from __future__ import annotations

import pytest

from cli.guardrails.style import StyleEnforcer


@pytest.fixture
def enforcer() -> StyleEnforcer:
    return StyleEnforcer()


def test_em_dash_rewritten(enforcer: StyleEnforcer) -> None:
    text = "this is a test — with em dashes — throughout"
    cleaned, issues = enforcer.enforce(text)
    assert "—" not in cleaned
    assert " - " in cleaned
    assert any(i.kind == "em_dash" for i in issues)
    em_issue = next(i for i in issues if i.kind == "em_dash")
    assert em_issue.count == 2


def test_em_dash_rewrite_collapses_spaces(enforcer: StyleEnforcer) -> None:
    """' — ' should become ' - ', not '  -  '."""
    text = "before — after"
    cleaned, _ = enforcer.enforce(text)
    assert cleaned == "before - after"


def test_em_dash_no_surrounding_space(enforcer: StyleEnforcer) -> None:
    text = "before—after"
    cleaned, _ = enforcer.enforce(text)
    assert "—" not in cleaned
    assert " - " in cleaned


def test_em_dash_disabled(enforcer: StyleEnforcer) -> None:
    enforcer = StyleEnforcer(rewrite_em_dash=False)
    text = "leave — alone"
    cleaned, issues = enforcer.enforce(text)
    assert "—" in cleaned
    assert any(i.kind == "em_dash" for i in issues)  # still reported


def test_emoji_detected_not_stripped_by_default(enforcer: StyleEnforcer) -> None:
    text = "great work!"  # ASCII only, baseline
    cleaned, issues = enforcer.enforce(text)
    assert cleaned == text
    assert all(i.kind != "emoji" for i in issues)


def test_emoji_reported() -> None:
    enforcer = StyleEnforcer(strip_emoji=False)
    text = "great work \U0001f389 keep going"
    cleaned, issues = enforcer.enforce(text)
    assert "\U0001f389" in cleaned  # not stripped
    assert any(i.kind == "emoji" for i in issues)


def test_emoji_stripped_when_configured() -> None:
    enforcer = StyleEnforcer(strip_emoji=True)
    text = "great \U0001f389 work \U0001f680 done"
    cleaned, issues = enforcer.enforce(text)
    assert "\U0001f389" not in cleaned
    assert "\U0001f680" not in cleaned
    assert any(i.kind == "emoji" for i in issues)


def test_clean_text_unchanged(enforcer: StyleEnforcer) -> None:
    text = "perfectly normal text with no violations at all"
    cleaned, issues = enforcer.enforce(text)
    assert cleaned == text
    assert issues == []


def test_empty_input(enforcer: StyleEnforcer) -> None:
    cleaned, issues = enforcer.enforce("")
    assert cleaned == ""
    assert issues == []


def test_sample_excerpt_not_empty_when_violation_present(enforcer: StyleEnforcer) -> None:
    text = "preamble preamble — a long em-dash violation here continues"
    _, issues = enforcer.enforce(text)
    em_issue = next(i for i in issues if i.kind == "em_dash")
    assert em_issue.sample
    assert len(em_issue.sample) <= 80
