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
