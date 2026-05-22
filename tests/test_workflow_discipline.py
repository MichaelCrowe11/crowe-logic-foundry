"""Tests for the workflow-discipline rewrite and tool-call dedupe.

Locks in the fixes from the meta-paralysis transcript:
- Execution-discipline rules no longer contradict themselves.
- Auto-continue nudge doesn't echo the "narrate intent" trigger phrase.
- Tier overlays apply per model type.
- In-session tool-call cache deduplicates identical calls within a turn.
"""

from __future__ import annotations

from config.agent_config import (
    SYSTEM_INSTRUCTIONS,
    _TIER_OVERLAYS,
    build_system_instructions,
)
from providers._shared import (
    AUTO_CONTINUE_NUDGE,
    _canonical_tool_call_key,
)


def test_system_prompt_drops_old_contradictory_rules():
    text = SYSTEM_INSTRUCTIONS.lower()
    # The old rule that triggered meta-litigation must be gone.
    assert "never narrate intent" not in text
    # The "any prose must describe what you JUST did" rule that fought
    # rule 1 must also be gone.
    assert "must describe what you" not in text
    # Reasoning budget must be present.
    assert "reasoning budget" in text or "internal deliberation" in text


def test_system_prompt_keeps_load_bearing_rules():
    text = SYSTEM_INSTRUCTIONS.lower()
    # Parallel tool calls.
    assert "parallel tool calls" in text or "independent tool calls" in text
    # Verify after writes.
    assert "verify" in text
    # Autonomy keywords.
    assert "continue" in text and "go" in text


def test_tier_overlay_applied_for_each_known_type():
    for tier_type in ("fast", "reasoning", "vision", "code", "voice", "instruct"):
        cfg = {"label": "CroweLM Test", "type": tier_type, "prompt": "tier test"}
        rendered = build_system_instructions(cfg).lower()
        assert f"tier behavior: {tier_type}" in rendered, (
            f"Missing overlay for tier '{tier_type}'"
        )


def test_tier_overlay_skipped_for_unknown_type():
    cfg = {"label": "CroweLM Test", "type": "exotic-tier", "prompt": "p"}
    rendered = build_system_instructions(cfg).lower()
    assert "tier behavior:" not in rendered


def test_auto_continue_nudge_does_not_echo_litigation_trigger():
    # The old nudge ended with "Do not narrate intent again" — the model
    # would then spend the next turn re-litigating that exact phrase.
    text = AUTO_CONTINUE_NUDGE.lower()
    assert "narrate intent" not in text
    # But it must still tell the model what to do.
    assert "tool call" in text or "final answer" in text


def test_canonical_tool_call_key_stable_across_arg_order():
    a = _canonical_tool_call_key("read_file", '{"path": "/x", "limit": 10}')
    b = _canonical_tool_call_key("read_file", '{"limit": 10, "path": "/x"}')
    assert a == b


def test_canonical_tool_call_key_distinguishes_different_calls():
    a = _canonical_tool_call_key("read_file", '{"path": "/x"}')
    b = _canonical_tool_call_key("read_file", '{"path": "/y"}')
    c = _canonical_tool_call_key("write_file", '{"path": "/x"}')
    assert a != b
    assert a != c


def test_canonical_tool_call_key_handles_empty_args():
    a = _canonical_tool_call_key("noop", "")
    b = _canonical_tool_call_key("noop", "{}")
    assert a == b


def test_canonical_tool_call_key_tolerates_invalid_json():
    # Should not raise; falls back to raw string.
    key = _canonical_tool_call_key("weird", "not valid json {")
    assert isinstance(key, str)
    assert key.startswith("weird::")


def test_overlay_keys_match_model_chain_types():
    """Every type used in MODEL_CHAIN should have an overlay defined."""
    from config.agent_config import MODEL_CHAIN

    used_types = {m.get("type") for m in MODEL_CHAIN if m.get("type")}
    overlay_types = set(_TIER_OVERLAYS.keys())
    missing = used_types - overlay_types
    assert not missing, f"MODEL_CHAIN types without overlays: {missing}"
