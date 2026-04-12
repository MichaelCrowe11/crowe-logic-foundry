"""Tests for Anthropic provider behavior on Azure-hosted CroweLM Prime."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import providers.anthropic as anthropic_mod
import tools


class _FakeRenderer:
    def __init__(self):
        self.reasoning: list[str] = []
        self.tokens: list[str] = []
        self.started = False
        self.finished = False

    def start(self):
        self.started = True

    def set_spinner(self, label: str):
        self.end_segment()

    def stop_spinner(self):
        return None

    def feed(self, token: str):
        self.tokens.append(token)

    def feed_reasoning(self, token: str):
        self.reasoning.append(token)

    def end_segment(self):
        self.tokens = []

    def finish(self, session_state=None):
        self.finished = True

    @property
    def current_segment_text(self) -> str:
        return "".join(self.tokens)


def _fake_anthropic_module(rounds, captured_kwargs):
    class _FakeClient:
        def __init__(self, *args, **kwargs):
            self.messages = SimpleNamespace(create=self._create)

        def _create(self, **kwargs):
            captured_kwargs.append(kwargs)
            return rounds.pop(0)

    return SimpleNamespace(Anthropic=_FakeClient)


def _noop_orchestrator():
    return SimpleNamespace(record_execution=lambda **kwargs: None)


def test_anthropic_provider_recovers_from_invalid_tool_json(monkeypatch):
    rounds = [
        [
            SimpleNamespace(
                type="content_block_start",
                index=0,
                content_block=SimpleNamespace(type="tool_use", id="tool_1", name="compile_correlation"),
            ),
            SimpleNamespace(
                type="content_block_delta",
                index=0,
                delta=SimpleNamespace(
                    type="input_json_delta",
                    partial_json='{"query":"Book of Enoch","chapter":"all"',
                ),
            ),
            SimpleNamespace(type="message_stop"),
        ],
        [
            SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="text_delta", text="Recovered"),
            ),
            SimpleNamespace(type="message_stop"),
        ],
    ]
    captured = []

    monkeypatch.setitem(sys.modules, "anthropic", _fake_anthropic_module(rounds, captured))

    provider = anthropic_mod.AnthropicProvider(
        model="claude-opus-4-6",
        system_instructions="system",
        endpoint="https://example.openai.azure.com/anthropic",
        api_key="test-key",
        label="CroweLM Prime",
    )
    provider.add_user_message("hello")

    tool_cards = []
    session_state = {"favicon": "", "tool_count": 0, "recent_actions": []}
    full_response = provider.stream_response(
        console=None,
        render_tool_card=lambda console, name, args_json, status, result, duration_ms: tool_cards.append({
            "name": name,
            "status": status,
            "result": result,
        }),
        session_state=session_state,
        _get_orchestrator=_noop_orchestrator,
        renderer=_FakeRenderer(),
    )

    assert full_response == "Recovered"
    assert tool_cards[0]["name"] == "compile_correlation"
    assert tool_cards[0]["status"] == "fail"
    assert "Invalid tool arguments" in tool_cards[0]["result"]


def test_anthropic_provider_routes_json_deltas_by_index(monkeypatch):
    rounds = [
        [
            SimpleNamespace(
                type="content_block_start",
                index=0,
                content_block=SimpleNamespace(type="tool_use", id="tool_1", name="tool_a"),
            ),
            SimpleNamespace(
                type="content_block_start",
                index=1,
                content_block=SimpleNamespace(type="tool_use", id="tool_2", name="tool_b"),
            ),
            SimpleNamespace(
                type="content_block_delta",
                index=0,
                delta=SimpleNamespace(type="input_json_delta", partial_json='{"value":"a"}'),
            ),
            SimpleNamespace(
                type="content_block_delta",
                index=1,
                delta=SimpleNamespace(type="input_json_delta", partial_json='{"value":"b"}'),
            ),
            SimpleNamespace(type="message_stop"),
        ],
        [
            SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="text_delta", text="done"),
            ),
            SimpleNamespace(type="message_stop"),
        ],
    ]
    captured = []

    def tool_a(value):
        """Tool A."""
        return f"A:{value}"

    def tool_b(value):
        """Tool B."""
        return f"B:{value}"

    monkeypatch.setitem(sys.modules, "anthropic", _fake_anthropic_module(rounds, captured))
    monkeypatch.setattr(tools, "user_functions", {tool_a, tool_b})
    monkeypatch.setattr(anthropic_mod, "_tools", {tool_a, tool_b})

    provider = anthropic_mod.AnthropicProvider(
        model="claude-opus-4-6",
        system_instructions="system",
        endpoint="https://example.openai.azure.com/anthropic",
        api_key="test-key",
        label="CroweLM Prime",
    )
    provider.add_user_message("hello")

    tool_cards = []
    session_state = {"favicon": "", "tool_count": 0, "recent_actions": []}
    full_response = provider.stream_response(
        console=None,
        render_tool_card=lambda console, name, args_json, status, result, duration_ms: tool_cards.append({
            "name": name,
            "status": status,
            "result": result,
        }),
        session_state=session_state,
        _get_orchestrator=_noop_orchestrator,
        renderer=_FakeRenderer(),
    )

    assert full_response == "done"
    assert tool_cards == [
        {"name": "tool_a", "status": "ok", "result": "A:a"},
        {"name": "tool_b", "status": "ok", "result": "B:b"},
    ]
    assert [entry["name"] for entry in session_state["recent_actions"]] == ["tool_a", "tool_b"]
