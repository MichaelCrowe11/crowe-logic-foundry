"""Tests for the shared OpenAI-compatible provider loop."""

from __future__ import annotations

import re
from types import SimpleNamespace

import providers._shared as shared_mod


class _FakeRenderer:
    def __init__(self):
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
        return None

    def end_segment(self):
        self.tokens = []

    def finish(self, session_state=None):
        self.finished = True

    @property
    def current_segment_text(self) -> str:
        return "".join(self.tokens)


def _chunk(*, content=None, tool_calls=None, finish_reason=None):
    delta = SimpleNamespace(
        content=content,
        tool_calls=tool_calls,
        reasoning=None,
        reasoning_content=None,
    )
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


class _DummyProvider(shared_mod.BaseOpenAIProvider):
    def __init__(self, rounds, captured_kwargs):
        super().__init__("dummy-model", "system", "CroweLM Test")
        self._rounds = rounds
        self._captured_kwargs = captured_kwargs
        self.client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=self._create),
            ),
        )

    def _create(self, **kwargs):
        self._captured_kwargs.append(kwargs)
        return iter(self._rounds.pop(0))


def _noop_orchestrator():
    return SimpleNamespace(record_execution=lambda **kwargs: None)


def test_base_provider_recovers_from_missing_tool_name_and_id(monkeypatch):
    captured = []
    rounds = [
        [
            _chunk(
                tool_calls=[
                    SimpleNamespace(
                        index=0,
                        id="",
                        function=SimpleNamespace(name="", arguments='{"url":"https://example.com"}'),
                    ),
                ],
                finish_reason="tool_calls",
            ),
        ],
        [
            _chunk(content="Recovered", finish_reason="stop"),
        ],
    ]

    monkeypatch.setattr(shared_mod, "load_tools", lambda: ([], {}))

    provider = _DummyProvider(rounds, captured)
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
    assert tool_cards == [{
        "name": "invalid_tool_call",
        "status": "fail",
        "result": '{"error": "Model emitted a tool call without a function name.", "raw_arguments": "{\\"url\\":\\"https://example.com\\"}"}',
    }]
    assert session_state["recent_actions"][0]["name"] == "invalid_tool_call"
    assert session_state["recent_actions"][0]["status"] == "fail"

    second_round_messages = captured[1]["messages"]
    assistant_msg = next(
        message for message in second_round_messages
        if message.get("role") == "assistant" and message.get("tool_calls")
    )
    tool_msg = next(
        message for message in second_round_messages
        if message.get("role") == "tool"
    )

    tool_call_id = assistant_msg["tool_calls"][0]["id"]
    assert re.fullmatch(r"[A-Za-z0-9]{9}", tool_call_id)
    assert assistant_msg["tool_calls"][0]["function"]["name"] == "invalid_tool_call"
    assert tool_msg["tool_call_id"] == tool_call_id
