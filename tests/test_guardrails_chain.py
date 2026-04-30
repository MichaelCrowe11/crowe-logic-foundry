"""Tests for cli.guardrails.chain (the composed pipeline)."""
from __future__ import annotations

from pathlib import Path

import pytest

from cli.guardrails.chain import GuardrailChain
from cli.guardrails.paths import PathPolicy


@pytest.fixture
def chain() -> GuardrailChain:
    return GuardrailChain()


def test_eclipse_failure_modes_combined(chain: GuardrailChain) -> None:
    """The full set of failures from the 2026-04-30 transcript, in one block."""
    bad_output = (
        "Your Resend API key is already wired in: "
        "re_a5Vo7zdg_MHo49nsg8MDfp1cVMqNvigEt — "
        "this completes the campaign system."
    )
    cleaned = chain.scrub_output(bad_output)
    assert "re_a5Vo7zdg" not in cleaned
    assert "—" not in cleaned
    codes = {e.code for e in chain.events}
    assert "secret-redacted" in codes
    assert "style-rewritten" in codes


def test_path_policy_via_chain(tmp_path: Path) -> None:
    chain = GuardrailChain(paths=PathPolicy(home=tmp_path))
    candidate = str(tmp_path / "campaign_blast.py")
    decision = chain.check_path(candidate)
    assert decision.verdict == "DENY"
    assert any(e.code == "path-denied" for e in chain.events)


def test_budget_via_chain(chain: GuardrailChain) -> None:
    decision = chain.check_budget(reasoning_tokens=5856, output_tokens=698)
    assert decision.verdict == "INTERRUPT"
    assert any(e.code == "scope-budget-exceeded" for e in chain.events)
    interrupt_event = next(e for e in chain.events if e.code == "scope-budget-exceeded")
    assert "interrupt_prompt" in interrupt_event.detail


def test_streaming_path(chain: GuardrailChain) -> None:
    parts = [
        "starting work ",
        "with key re_AbCdEfGhIjKl",
        "MnOpQrStUvWxYz0123456789",
        " and continuing for many many many ",
        "more characters to push past hold-back" + ("." * 300),
    ]
    emitted = "".join(chain.stream(p) for p in parts)
    emitted += chain.flush_stream()
    assert "re_AbCdEfGhIjKl" not in emitted
    codes = {e.code for e in chain.events}
    assert "secret-redacted" in codes


def test_clean_block_emits_no_events(chain: GuardrailChain) -> None:
    clean = "this is a perfectly fine response with no violations whatsoever."
    cleaned = chain.scrub_output(clean)
    assert cleaned == clean
    assert chain.events == []


def test_event_severity_is_set(chain: GuardrailChain) -> None:
    chain.scrub_output("here is a key: re_AbCdEfGhIjKlMnOpQrStUvWx")
    secret_event = next(e for e in chain.events if e.code == "secret-redacted")
    assert secret_event.severity == "error"


def test_emoji_detected_via_chain(chain: GuardrailChain) -> None:
    cleaned = chain.scrub_output("great work \U0001f389 done")
    # default: detected, not stripped
    assert "\U0001f389" in cleaned
    assert any(e.code == "style-warning" for e in chain.events)
