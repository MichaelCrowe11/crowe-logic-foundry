"""Unit tests for the v0 -> CMP translator (mesh B2)."""

from __future__ import annotations

import pytest

pytest.importorskip("crowe_mesh_protocol")

from crowe_mesh_protocol import CMP_EVENT_TYPES

from control_plane.cmp_translate import CmpTranslator


def _t() -> CmpTranslator:
    return CmpTranslator(session_id="s1", model_tier="auto")


def test_ready_carries_session_and_tier():
    out = _t().translate({"type": "ready"})
    assert len(out) == 1
    assert out[0]["type"] == "ready"
    assert out[0]["session_id"] == "s1"
    assert out[0]["model_tier"] == "auto"


def test_token_maps_delta():
    out = _t().translate({"type": "token", "delta": "hi"})
    assert out[0]["type"] == "token"
    assert out[0]["delta"] == "hi"
    assert out[0]["session_id"] == "s1"


def test_reasoning_becomes_reasoning_delta():
    out = _t().translate({"type": "reasoning", "delta": "think"})
    assert out[0]["type"] == "reasoning.delta"
    assert out[0]["delta"] == "think"
    assert "reasoning_id" in out[0]


def test_spinner_becomes_status():
    out = _t().translate({"type": "spinner", "label": "working"})
    assert out[0]["type"] == "status"
    assert out[0]["label"] == "working"


def test_segment_end():
    out = _t().translate({"type": "segment_end"})
    assert out[0]["type"] == "segment_end"
    assert out[0]["reason"] == "segment"


def test_tool_yields_started_and_result_with_stable_id():
    t = _t()
    out = t.translate(
        {
            "type": "tool",
            "name": "search_kb",
            "args": "{}",
            "status": "ok",
            "result": "r",
            "duration_ms": 12,
        }
    )
    types = [e["type"] for e in out]
    assert types == ["tool.started", "tool.result"]
    assert out[0]["name"] == "search_kb"
    assert out[0]["tool_call_id"] == out[1]["tool_call_id"]
    assert out[1]["status"] == "ok"


def test_tool_failure_maps_status_fail():
    out = _t().translate(
        {"type": "tool", "name": "x", "status": "error", "result": "boom"}
    )
    assert out[1]["status"] == "fail"


def test_done_passes_counts():
    out = _t().translate(
        {
            "type": "done",
            "tokens": 5,
            "reasoning_tokens": 2,
            "elapsed_ms": 100,
            "ttft_ms": 10,
        }
    )
    assert out[0]["type"] == "done"
    assert out[0]["tokens"] == 5
    assert out[0]["ttft_ms"] == 10


def test_error_maps_kind_to_code():
    out = _t().translate({"type": "error", "message": "bad", "kind": "runtime"})
    assert out[0]["type"] == "error"
    assert out[0]["code"] == "runtime"
    assert out[0]["recoverable"] is False


def test_all_emitted_types_are_valid_cmp():
    t = _t()
    seq = [
        {"type": "ready"},
        {"type": "token", "delta": "a"},
        {"type": "reasoning", "delta": "b"},
        {"type": "spinner", "label": "x"},
        {"type": "segment_end"},
        {"type": "tool", "name": "n", "status": "ok"},
        {
            "type": "done",
            "tokens": 1,
            "reasoning_tokens": 0,
            "elapsed_ms": 1,
            "ttft_ms": 1,
        },
        {"type": "error", "message": "e", "kind": "runtime"},
    ]
    for v0 in seq:
        for ev in t.translate(v0):
            assert ev["type"] in CMP_EVENT_TYPES, ev["type"]


def test_unknown_v0_event_is_dropped():
    assert _t().translate({"type": "keepalive"}) == []
