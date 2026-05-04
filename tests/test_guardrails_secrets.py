"""Tests for cli.guardrails.secrets."""
from __future__ import annotations

import pytest

from cli.guardrails.secrets import SecretScrubber, StreamScrubber


@pytest.fixture
def scrubber() -> SecretScrubber:
    return SecretScrubber()


def test_resend_key_redacted(scrubber: SecretScrubber) -> None:
    """The exact 2026-04-30 Eclipse failure mode."""
    text = "Your Resend API key is already wired in: re_a5Vo7zdg_MHo49nsg8MDfp1cVMqNvigEt"
    cleaned, hits = scrubber.scrub(text)
    assert "re_a5Vo7zdg_MHo49nsg8MDfp1cVMqNvigEt" not in cleaned
    assert len(hits) == 1
    assert hits[0].label == "resend"
    assert "REDACTED" in cleaned


def test_openai_key_redacted(scrubber: SecretScrubber) -> None:
    text = "key=sk-proj-AbCdEf0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    cleaned, hits = scrubber.scrub(text)
    assert "sk-proj-AbCdEf" not in cleaned
    assert any(h.label == "openai" for h in hits)


def test_anthropic_key_redacted(scrubber: SecretScrubber) -> None:
    text = "sk-ant-api03-abc123def456ghi789jkl012mno345pqr678stu901"
    cleaned, hits = scrubber.scrub(text)
    assert "sk-ant-api03" not in cleaned
    assert any(h.label == "anthropic" for h in hits)


def test_aws_access_key_redacted(scrubber: SecretScrubber) -> None:
    text = "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"
    cleaned, hits = scrubber.scrub(text)
    assert "AKIAIOSFODNN7EXAMPLE" not in cleaned
    assert any(h.label == "aws_akid" for h in hits)


def test_github_pat_redacted(scrubber: SecretScrubber) -> None:
    text = "token: github_pat_11AAAAAA0_abcdefghijklmnopqrstuv"
    cleaned, hits = scrubber.scrub(text)
    assert "github_pat_11AAAAAA0" not in cleaned
    assert any(h.label == "github_pat" for h in hits)


def test_huggingface_token_redacted(scrubber: SecretScrubber) -> None:
    text = "HF_TOKEN=hf_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
    cleaned, hits = scrubber.scrub(text)
    assert "hf_AbCdEfGhIj" not in cleaned
    assert any(h.label == "hf" for h in hits)


def test_stripe_live_key_redacted(scrubber: SecretScrubber) -> None:
    text = "stripe: sk_live_4eC39HqLyjWDarjtT1zdp7dc"
    cleaned, hits = scrubber.scrub(text)
    assert "sk_live_4eC39HqLyjWDarjtT1zdp7dc" not in cleaned
    assert any(h.label == "stripe_live" for h in hits)


def test_clean_text_unchanged(scrubber: SecretScrubber) -> None:
    text = "this is fine, contains no secrets at all, just words"
    cleaned, hits = scrubber.scrub(text)
    assert cleaned == text
    assert hits == []


def test_redaction_preserves_shape_hint(scrubber: SecretScrubber) -> None:
    """Last 4 chars of the key are visible in the marker so the user can
    correlate logs without exposing the full credential."""
    text = "re_a5Vo7zdg_MHo49nsg8MDfp1cVMqNvigEt"
    cleaned, _ = scrubber.scrub(text)
    assert "igEt" in cleaned  # last 4 chars
    assert "[REDACTED:resend" in cleaned


def test_multiple_secrets_in_one_block(scrubber: SecretScrubber) -> None:
    text = (
        "RESEND=re_AbCdEfGhIjKlMnOpQrStUvWx "
        "OPENAI=sk-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789 "
        "AWS=AKIAIOSFODNN7EXAMPLE"
    )
    cleaned, hits = scrubber.scrub(text)
    assert len(hits) >= 3
    labels = {h.label for h in hits}
    assert {"resend", "openai", "aws_akid"}.issubset(labels)


def test_empty_input(scrubber: SecretScrubber) -> None:
    cleaned, hits = scrubber.scrub("")
    assert cleaned == ""
    assert hits == []


# ---- StreamScrubber -------------------------------------------------------


def test_stream_scrubber_holds_back_partial_match() -> None:
    """A key split across chunks must not leak through."""
    scrubber = StreamScrubber(holdback_chars=128)
    parts = ["hello world ", "re_a5Vo7zd", "g_MHo49nsg8MDfp1cVMqNvigEt", " more text"]
    emitted = ""
    for part in parts:
        emitted += scrubber.feed(part)
    emitted += scrubber.flush()
    assert "re_a5Vo7zdg_MHo49nsg8MDfp1cVMqNvigEt" not in emitted
    assert "[REDACTED:resend" in emitted


def test_stream_scrubber_emits_safe_prefix() -> None:
    scrubber = StreamScrubber(holdback_chars=64)
    long_clean = "x" * 200
    emitted = scrubber.feed(long_clean)
    assert len(emitted) >= 100  # most of it is safe to emit
    assert emitted == "x" * len(emitted)
    rest = scrubber.flush()
    assert emitted + rest == long_clean


def test_stream_scrubber_records_hits() -> None:
    scrubber = StreamScrubber()
    scrubber.feed("token: re_AbCdEfGhIjKlMnOpQrStUvWx ")
    scrubber.feed("x" * 400)  # push past holdback
    scrubber.flush()
    assert any(h.label == "resend" for h in scrubber.hits)
