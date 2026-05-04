"""Tests for cli.guardrail_pipeline."""
from __future__ import annotations

from pathlib import Path

from cli.guardrail_pipeline import (
    apply_to_block,
    apply_to_stream,
    flush_stream,
    pipeline_for_session,
    record_tool_call,
    telemetry_summary,
)
from cli.guardrails import PathPolicy


def test_pipeline_factory_returns_chain() -> None:
    chain = pipeline_for_session()
    assert hasattr(chain, "scrub_output")
    assert chain.events == []


def test_apply_to_block_redacts_secrets() -> None:
    chain = pipeline_for_session()
    cleaned = apply_to_block(
        "the key is re_AbCdEfGhIjKlMnOpQrStUvWx and so on", chain
    )
    assert "re_AbCdEf" not in cleaned
    summary = telemetry_summary(chain)
    assert summary["total_events"] >= 1
    assert "resend" in summary["redacted_secrets"]


def test_apply_to_block_rewrites_em_dash() -> None:
    chain = pipeline_for_session()
    cleaned = apply_to_block("before — after", chain)
    assert "—" not in cleaned


def test_record_tool_call_blocks_home_dir_write(tmp_path: Path) -> None:
    chain = pipeline_for_session()
    chain._paths = PathPolicy(home=tmp_path)
    decision = record_tool_call(
        "Write",
        {"file_path": str(tmp_path / "campaign_blast.py")},
        chain,
    )
    assert not decision.proceed
    assert "campaign_blast.py" in decision.refusal_reason


def test_record_tool_call_allows_safe_writes(tmp_path: Path) -> None:
    chain = pipeline_for_session()
    chain._paths = PathPolicy(
        home=tmp_path,
        allowed_prefixes=("/tmp",),
    )
    decision = record_tool_call(
        "Write",
        {"file_path": "/tmp/scratch.txt"},
        chain,
    )
    assert decision.proceed


def test_record_tool_call_passes_through_non_write_tools() -> None:
    chain = pipeline_for_session()
    decision = record_tool_call("Read", {"file_path": "/anywhere"}, chain)
    assert decision.proceed
    assert chain.events == []


def test_telemetry_summary_shape() -> None:
    chain = pipeline_for_session()
    apply_to_block("re_AbCdEfGhIjKlMnOpQrStUvWx and an em-dash —", chain)
    summary = telemetry_summary(chain)
    assert "total_events" in summary
    assert "by_code" in summary
    assert "by_severity" in summary
    assert "redacted_secrets" in summary


def test_streaming_flow() -> None:
    chain = pipeline_for_session()
    parts = ["safe ", "text ", "but key re_AbCdEfGhIjKlMnOpQrStUvWx ", "more " * 80]
    out = "".join(apply_to_stream(p, chain) for p in parts) + flush_stream(chain)
    assert "re_AbCdEfGhIjKlMnOpQrStUvWx" not in out
    summary = telemetry_summary(chain)
    assert "resend" in summary["redacted_secrets"]
