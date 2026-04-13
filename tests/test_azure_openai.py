"""Tests for Azure OpenAI provider behavior on CroweLM routes."""

from types import SimpleNamespace

import providers.azure_openai as azure_mod


class _FakeRenderer:
    def __init__(self):
        self.reasoning: list[str] = []
        self.tokens: list[str] = []
        self.started = False
        self.finished = False
        self.segment_count = 0
        self.spinners: list[str | None] = []

    def start(self):
        self.started = True

    def set_spinner(self, label: str):
        self.end_segment()
        self.spinners.append(label)

    def stop_spinner(self):
        self.spinners.append(None)

    def feed(self, token: str):
        self.tokens.append(token)

    def feed_reasoning(self, token: str):
        self.reasoning.append(token)

    def end_segment(self):
        self.segment_count += 1
        self.tokens = []

    def finish(self, session_state=None):
        self.finished = True

    @property
    def current_segment_text(self) -> str:
        return "".join(self.tokens)


class _FakeStream:
    def __init__(self, events, response):
        self._events = events
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def __iter__(self):
        return iter(self._events)

    def get_final_response(self):
        return self._response


def _fake_openai_factory(rounds, captured_kwargs):
    class _FakeResponsesApi:
        def stream(self, **kwargs):
            captured_kwargs.append(kwargs)
            round_data = rounds.pop(0)
            if isinstance(round_data, Exception):
                raise round_data
            return _FakeStream(round_data["events"], round_data["response"])

    class _FakeOpenAI:
        def __init__(self, *args, **kwargs):
            self.responses = _FakeResponsesApi()

    return _FakeOpenAI


def _noop_orchestrator():
    return SimpleNamespace(record_execution=lambda **kwargs: None)


def test_responses_provider_streams_reasoning_and_text(monkeypatch):
    captured = []
    rounds = [{
        "events": [
            SimpleNamespace(type="response.reasoning_summary_text.delta", delta="Think "),
            SimpleNamespace(type="response.reasoning_summary_text.delta", delta="deeply."),
            SimpleNamespace(type="response.output_text.delta", delta="OK"),
        ],
        "response": SimpleNamespace(id="resp_1", output=[], output_text="OK"),
    }]

    monkeypatch.setattr(azure_mod, "OpenAI", _fake_openai_factory(rounds, captured))
    monkeypatch.setattr(azure_mod, "load_tools", lambda: ([], {}))

    provider = azure_mod.AzureResponsesProvider(
        model="gpt-5.4-pro",
        system_instructions="system",
        endpoint="https://example.openai.azure.com",
        api_key="test-key",
        label="CroweLM Apex",
    )
    provider.add_user_message("hello")

    renderer = _FakeRenderer()
    full_response = provider.stream_response(
        console=None,
        render_tool_card=lambda *args, **kwargs: None,
        session_state={"favicon": "", "tool_count": 0},
        _get_orchestrator=_noop_orchestrator,
        renderer=renderer,
    )

    assert renderer.started is True
    assert renderer.finished is True
    assert "".join(renderer.reasoning) == "Think deeply."
    assert full_response == "OK"
    assert provider.previous_response_id == "resp_1"
    assert captured[0]["reasoning"] == {"effort": "medium", "summary": "auto"}


def test_responses_provider_executes_function_calls(monkeypatch):
    captured = []
    rounds = [
        {
            "events": [
                SimpleNamespace(type="response.reasoning_summary_text.delta", delta="Plan tool use."),
            ],
            "response": SimpleNamespace(
                id="resp_1",
                output=[
                    SimpleNamespace(
                        type="function_call",
                        name="echo_tool",
                        arguments='{"text": "hi"}',
                        call_id="call_1",
                    )
                ],
                output_text="",
            ),
        },
        {
            "events": [
                SimpleNamespace(type="response.output_text.delta", delta="done"),
            ],
            "response": SimpleNamespace(id="resp_2", output=[], output_text="done"),
        },
    ]

    def echo_tool(text):
        return f"tool:{text}"

    tool_schema = {
        "type": "function",
        "function": {
            "name": "echo_tool",
            "description": "Echo text",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    }

    tool_cards = []

    monkeypatch.setattr(azure_mod, "OpenAI", _fake_openai_factory(rounds, captured))
    monkeypatch.setattr(azure_mod, "load_tools", lambda: ([tool_schema], {"echo_tool": echo_tool}))

    provider = azure_mod.AzureResponsesProvider(
        model="gpt-5.4-pro",
        system_instructions="system",
        endpoint="https://example.openai.azure.com",
        api_key="test-key",
        label="CroweLM Apex",
    )
    provider.add_user_message("hello")

    session_state = {"favicon": "", "tool_count": 0, "recent_actions": []}
    full_response = provider.stream_response(
        console=None,
        render_tool_card=lambda console, name, args_json, status, result, duration_ms: tool_cards.append({
            "name": name,
            "args_json": args_json,
            "status": status,
            "result": result,
        }),
        session_state=session_state,
        _get_orchestrator=_noop_orchestrator,
        renderer=_FakeRenderer(),
    )

    assert full_response == "done"
    assert session_state["tool_count"] == 1
    assert session_state["recent_actions"][0]["name"] == "echo_tool"
    assert session_state["recent_actions"][0]["status"] == "ok"
    assert tool_cards == [{
        "name": "echo_tool",
        "args_json": '{"text": "hi"}',
        "status": "ok",
        "result": "tool:hi",
    }]
    assert captured[1]["previous_response_id"] == "resp_1"
    assert captured[1]["input"] == [{
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "tool:hi",
    }]


def test_responses_provider_falls_back_to_final_response_content(monkeypatch):
    captured = []
    rounds = [{
        "events": [],
        "response": SimpleNamespace(
            id="resp_1",
            output=[
                SimpleNamespace(
                    type="reasoning",
                    summary=[SimpleNamespace(text="Condensed reasoning.")],
                )
            ],
            output_text="OK",
        ),
    }]

    monkeypatch.setattr(azure_mod, "OpenAI", _fake_openai_factory(rounds, captured))
    monkeypatch.setattr(azure_mod, "load_tools", lambda: ([], {}))

    provider = azure_mod.AzureResponsesProvider(
        model="gpt-5.4-pro",
        system_instructions="system",
        endpoint="https://example.openai.azure.com",
        api_key="test-key",
        label="CroweLM Apex",
    )
    provider.add_user_message("hello")

    renderer = _FakeRenderer()
    full_response = provider.stream_response(
        console=None,
        render_tool_card=lambda *args, **kwargs: None,
        session_state={"favicon": "", "tool_count": 0},
        _get_orchestrator=_noop_orchestrator,
        renderer=renderer,
    )

    assert "".join(renderer.reasoning) == "Condensed reasoning."
    assert full_response == "OK"


def test_responses_provider_uses_streamed_function_call_fallback(monkeypatch):
    captured = []
    rounds = [
        {
            "events": [
                SimpleNamespace(
                    type="response.output_item.done",
                    output_index=0,
                    item=SimpleNamespace(
                        type="function_call",
                        name="echo_tool",
                        arguments='{"text": "hi"}',
                        call_id="call_1",
                    ),
                ),
            ],
            "response": SimpleNamespace(id="resp_1", output=[], output_text=""),
        },
        {
            "events": [
                SimpleNamespace(type="response.output_text.delta", delta="done"),
            ],
            "response": SimpleNamespace(id="resp_2", output=[], output_text="done"),
        },
    ]

    def echo_tool(text):
        return f"tool:{text}"

    tool_schema = {
        "type": "function",
        "function": {
            "name": "echo_tool",
            "description": "Echo text",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    }

    monkeypatch.setattr(azure_mod, "OpenAI", _fake_openai_factory(rounds, captured))
    monkeypatch.setattr(azure_mod, "load_tools", lambda: ([tool_schema], {"echo_tool": echo_tool}))

    provider = azure_mod.AzureResponsesProvider(
        model="gpt-5.4-pro",
        system_instructions="system",
        endpoint="https://example.openai.azure.com",
        api_key="test-key",
        label="CroweLM Apex",
    )
    provider.add_user_message("hello")

    session_state = {"favicon": "", "tool_count": 0, "recent_actions": []}
    full_response = provider.stream_response(
        console=None,
        render_tool_card=lambda *args, **kwargs: None,
        session_state=session_state,
        _get_orchestrator=_noop_orchestrator,
        renderer=_FakeRenderer(),
    )

    assert full_response == "done"
    assert session_state["tool_count"] == 1
    assert captured[1]["previous_response_id"] == "resp_1"
    assert captured[1]["input"] == [{
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "tool:hi",
    }]
    assert provider.previous_response_id == "resp_2"


def test_responses_provider_does_not_persist_incomplete_response_id(monkeypatch):
    captured = []
    rounds = [
        {
            "events": [],
            "response": SimpleNamespace(
                id="resp_1",
                output=[
                    SimpleNamespace(
                        type="function_call",
                        name="echo_tool",
                        arguments='{"text": "hi"}',
                        call_id="call_1",
                    )
                ],
                output_text="",
            ),
        },
        RuntimeError("upstream failure"),
    ]

    def echo_tool(text):
        return f"tool:{text}"

    tool_schema = {
        "type": "function",
        "function": {
            "name": "echo_tool",
            "description": "Echo text",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    }

    monkeypatch.setattr(azure_mod, "OpenAI", _fake_openai_factory(rounds, captured))
    monkeypatch.setattr(azure_mod, "load_tools", lambda: ([tool_schema], {"echo_tool": echo_tool}))

    provider = azure_mod.AzureResponsesProvider(
        model="gpt-5.4-pro",
        system_instructions="system",
        endpoint="https://example.openai.azure.com",
        api_key="test-key",
        label="CroweLM Apex",
    )
    provider.add_user_message("hello")

    try:
        provider.stream_response(
            console=None,
            render_tool_card=lambda *args, **kwargs: None,
            session_state={"favicon": "", "tool_count": 0, "recent_actions": []},
            _get_orchestrator=_noop_orchestrator,
            renderer=_FakeRenderer(),
        )
    except RuntimeError as exc:
        assert str(exc) == "upstream failure"
    else:
        raise AssertionError("Expected provider stream_response to raise")

    assert captured[1]["previous_response_id"] == "resp_1"
    assert provider.previous_response_id is None


def test_responses_provider_raises_when_tool_round_limit_is_exhausted(monkeypatch):
    captured = []
    rounds = [
        {
            "events": [],
            "response": SimpleNamespace(
                id=f"resp_{index}",
                output=[
                    SimpleNamespace(
                        type="function_call",
                        name="echo_tool",
                        arguments='{"text": "loop"}',
                        call_id=f"call_{index}",
                    )
                ],
                output_text="",
            ),
        }
        for index in range(1, azure_mod.AzureResponsesProvider.MAX_ROUNDS + 1)
    ]

    def echo_tool(text):
        return text

    tool_schema = {
        "type": "function",
        "function": {
            "name": "echo_tool",
            "description": "Echo text",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    }

    monkeypatch.setattr(azure_mod, "OpenAI", _fake_openai_factory(rounds, captured))
    monkeypatch.setattr(azure_mod, "load_tools", lambda: ([tool_schema], {"echo_tool": echo_tool}))

    provider = azure_mod.AzureResponsesProvider(
        model="gpt-5.4-pro",
        system_instructions="system",
        endpoint="https://example.openai.azure.com",
        api_key="test-key",
        label="CroweLM Apex",
    )
    provider.add_user_message("hello")

    try:
        provider.stream_response(
            console=None,
            render_tool_card=lambda *args, **kwargs: None,
            session_state={"favicon": "", "tool_count": 0, "recent_actions": []},
            _get_orchestrator=_noop_orchestrator,
            renderer=_FakeRenderer(),
        )
    except RuntimeError as exc:
        assert str(exc) == (
            f"CroweLM Apex exceeded {azure_mod.AzureResponsesProvider.MAX_ROUNDS} "
            "tool rounds without a final response."
        )
    else:
        raise AssertionError("Expected provider stream_response to raise")

    assert len(captured) == azure_mod.AzureResponsesProvider.MAX_ROUNDS
    assert provider.previous_response_id is None


# ---------------------------------------------------------------------------
# Stream-drop recovery (missing response.completed)
# ---------------------------------------------------------------------------

class _BrokenFakeStream:
    """Simulates a stream that delivers events then fails on get_final_response."""

    def __init__(self, events):
        self._events = events

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def __iter__(self):
        return iter(self._events)

    def get_final_response(self):
        raise RuntimeError("Didn't receive a `response.completed` event.")


def _broken_stream_factory(events_per_round, captured_kwargs):
    class _FakeResponsesApi:
        def stream(self, **kwargs):
            captured_kwargs.append(kwargs)
            return _BrokenFakeStream(events_per_round.pop(0))

    class _FakeOpenAI:
        def __init__(self, *args, **kwargs):
            self.responses = _FakeResponsesApi()

    return _FakeOpenAI


def test_responses_provider_recovers_from_missing_completed_event(monkeypatch):
    """When the stream drops without response.completed, the provider should
    use the text deltas it already received instead of crashing."""
    captured = []
    events = [[
        SimpleNamespace(type="response.reasoning_summary_text.delta", delta="Thinking..."),
        SimpleNamespace(type="response.output_text.delta", delta="Here is the answer."),
    ]]

    monkeypatch.setattr(azure_mod, "OpenAI", _broken_stream_factory(events, captured))
    monkeypatch.setattr(azure_mod, "load_tools", lambda: ([], {}))

    provider = azure_mod.AzureResponsesProvider(
        model="gpt-5.4-pro",
        system_instructions="system",
        endpoint="https://example.openai.azure.com",
        api_key="test-key",
        label="CroweLM Apex",
    )
    provider.add_user_message("hello")

    renderer = _FakeRenderer()
    result = provider.stream_response(
        console=None,
        render_tool_card=lambda *args, **kwargs: None,
        session_state={"favicon": "", "tool_count": 0, "recent_actions": []},
        _get_orchestrator=_noop_orchestrator,
        renderer=renderer,
    )

    assert result == "Here is the answer."
    assert renderer.finished is True
    # Partial response should NOT poison previous_response_id
    assert provider.previous_response_id == "partial_0"


def test_responses_provider_clears_state_on_refusal(monkeypatch):
    """Content-filter refusals should not persist previous_response_id."""
    captured = []
    rounds = [{
        "events": [
            SimpleNamespace(
                type="response.output_text.delta",
                delta="I'm sorry, but I cannot assist with that request.",
            ),
        ],
        "response": SimpleNamespace(
            id="resp_refusal",
            output=[],
            output_text="I'm sorry, but I cannot assist with that request.",
        ),
    }]

    monkeypatch.setattr(azure_mod, "OpenAI", _fake_openai_factory(rounds, captured))
    monkeypatch.setattr(azure_mod, "load_tools", lambda: ([], {}))

    provider = azure_mod.AzureResponsesProvider(
        model="gpt-5.4-pro",
        system_instructions="system",
        endpoint="https://example.openai.azure.com",
        api_key="test-key",
        label="CroweLM Apex",
    )
    provider.add_user_message("hello")

    renderer = _FakeRenderer()
    result = provider.stream_response(
        console=None,
        render_tool_card=lambda *args, **kwargs: None,
        session_state={"favicon": "", "tool_count": 0, "recent_actions": []},
        _get_orchestrator=_noop_orchestrator,
        renderer=renderer,
    )

    assert "cannot assist" in result
    assert renderer.finished is True
    # Refusal should clear previous_response_id so next turn starts fresh
    assert provider.previous_response_id is None
