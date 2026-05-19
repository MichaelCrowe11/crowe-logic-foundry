"""Tests for the OpenAI-compatible /v1/chat/completions endpoint.

We mock `stream_agent_events` so the tests don't require Azure
credentials or a real model. The adapter contract is what we're
asserting: crowe-stream v0 -> OpenAI chat.completion.chunk deltas.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import pytest

pytest.importorskip("fastapi")

import control_plane.gateway as gateway_mod
import control_plane.streaming as streaming_mod


# ---------------------------------------------------------------------
# Adapter unit tests (no FastAPI / no DB)
# ---------------------------------------------------------------------

async def _fake_events(events: list[dict]) -> AsyncIterator[dict]:
    for ev in events:
        yield ev


def _frames_to_chunks(frames: list[str]) -> list[dict | str]:
    """Parse a list of `data: <json>\n\n` frames into payloads."""
    out: list[dict | str] = []
    for f in frames:
        assert f.startswith("data: "), f"frame missing data: prefix: {f!r}"
        assert f.endswith("\n\n"), f"frame missing trailing \\n\\n: {f!r}"
        body = f[len("data: "):-2]
        if body == "[DONE]":
            out.append("[DONE]")
        else:
            out.append(json.loads(body))
    return out


def test_openai_adapter_translates_ready_token_done(monkeypatch):
    """ready -> assistant-role chunk; token -> content delta; done -> [DONE]."""
    events = [
        {"type": "ready"},
        {"type": "token", "delta": "Hello, "},
        {"type": "token", "delta": "world!"},
        {"type": "done", "tokens": 2, "reasoning_tokens": 0, "elapsed_ms": 50, "ttft_ms": 10},
    ]
    monkeypatch.setattr(
        streaming_mod, "stream_agent_events",
        lambda **kwargs: _fake_events(events),
    )

    import asyncio
    async def _collect():
        out = []
        async for f in streaming_mod.stream_openai_compatible(
            messages=[{"role": "user", "content": "hi"}],
            model_id="CroweLM-Helio",
            session_id="t1",
        ):
            out.append(f)
        return out
    frames = asyncio.run(_collect())
    parsed = _frames_to_chunks(frames)

    # Sequence: role-chunk, "Hello, " delta, "world!" delta, stop, [DONE]
    assert len(parsed) == 5
    assert parsed[0]["choices"][0]["delta"] == {"role": "assistant"}
    assert parsed[1]["choices"][0]["delta"] == {"content": "Hello, "}
    assert parsed[2]["choices"][0]["delta"] == {"content": "world!"}
    assert parsed[3]["choices"][0]["finish_reason"] == "stop"
    assert parsed[3]["choices"][0]["delta"] == {}
    assert parsed[4] == "[DONE]"

    # All non-terminal chunks share an id and the requested model name.
    for chunk in parsed[:-1]:
        assert chunk["object"] == "chat.completion.chunk"
        assert chunk["model"] == "CroweLM-Helio"
        assert chunk["id"].startswith("chatcmpl-")


def test_openai_adapter_emits_error_finish_reason(monkeypatch):
    """An upstream error becomes finish_reason=error then [DONE]."""
    events = [
        {"type": "ready"},
        {"type": "error", "message": "upstream 429", "kind": "rate_limit"},
    ]
    monkeypatch.setattr(
        streaming_mod, "stream_agent_events",
        lambda **kwargs: _fake_events(events),
    )

    import asyncio
    async def _collect():
        return [f async for f in streaming_mod.stream_openai_compatible(
            messages=[{"role": "user", "content": "hi"}],
            model_id="CroweLM-Helio",
            session_id="t2",
        )]
    parsed = _frames_to_chunks(asyncio.run(_collect()))

    # ready -> role chunk; error -> error finish_reason; [DONE]
    assert parsed[-1] == "[DONE]"
    assert parsed[-2]["choices"][0]["finish_reason"] == "error"


def test_openai_adapter_handles_missing_ready(monkeypatch):
    """Even if the provider doesn't emit `ready`, the first token still
    gets the assistant role chunk so the OpenAI client doesn't choke.
    """
    events = [
        {"type": "token", "delta": "ok"},
        {"type": "done", "tokens": 1, "reasoning_tokens": 0},
    ]
    monkeypatch.setattr(
        streaming_mod, "stream_agent_events",
        lambda **kwargs: _fake_events(events),
    )

    import asyncio
    async def _collect():
        return [f async for f in streaming_mod.stream_openai_compatible(
            messages=[{"role": "user", "content": "hi"}],
            model_id="X",
            session_id="t3",
        )]
    parsed = _frames_to_chunks(asyncio.run(_collect()))

    assert parsed[0]["choices"][0]["delta"] == {"role": "assistant"}
    assert parsed[1]["choices"][0]["delta"] == {"content": "ok"}


def test_openai_adapter_translates_tool_event_to_tool_calls(monkeypatch):
    """A `tool` event becomes one OpenAI chunk with delta.tool_calls
    in OpenAI shape plus a crowe_tool_result sidecar for the
    server-side execution result.
    """
    events = [
        {"type": "ready"},
        {"type": "token", "delta": "Looking up. "},
        {
            "type": "tool",
            "name": "search_kb",
            "args": '{"query":"mycorrhiza"}',
            "status": "ok",
            "result": "Mycorrhizal fungi form symbiosis...",
            "duration_ms": 142,
        },
        {"type": "token", "delta": "Found references."},
        {"type": "done", "tokens": 3, "reasoning_tokens": 0},
    ]
    monkeypatch.setattr(
        streaming_mod, "stream_agent_events",
        lambda **kwargs: _fake_events(events),
    )

    import asyncio
    async def _collect():
        return [f async for f in streaming_mod.stream_openai_compatible(
            messages=[{"role": "user", "content": "hi"}],
            model_id="CroweLM-Helio",
            session_id="t-tool",
        )]
    parsed = _frames_to_chunks(asyncio.run(_collect()))

    # role + content + tool_calls + content + stop + [DONE]
    assert len(parsed) == 6
    tool_chunk = parsed[2]
    delta = tool_chunk["choices"][0]["delta"]
    assert "tool_calls" in delta, delta
    assert delta["tool_calls"][0]["index"] == 0
    assert delta["tool_calls"][0]["type"] == "function"
    assert delta["tool_calls"][0]["id"].startswith("call_")
    assert delta["tool_calls"][0]["function"]["name"] == "search_kb"
    assert delta["tool_calls"][0]["function"]["arguments"] == \
        '{"query":"mycorrhiza"}'
    # The non-standard sidecar carries the server-side result.
    assert delta["crowe_tool_result"]["status"] == "ok"
    assert "Mycorrhizal fungi" in delta["crowe_tool_result"]["result"]
    assert delta["crowe_tool_result"]["duration_ms"] == 142
    # The sidecar's id matches the tool_call id so consumers can
    # match the result to its call.
    assert delta["crowe_tool_result"]["id"] == delta["tool_calls"][0]["id"]
    # Crucially, NO finish_reason=tool_calls. We continued the turn.
    assert tool_chunk["choices"][0]["finish_reason"] is None
    # The text content after the tool call is preserved.
    assert parsed[3]["choices"][0]["delta"] == {"content": "Found references."}
    # Final stop chunk is emitted normally.
    assert parsed[4]["choices"][0]["finish_reason"] == "stop"
    assert parsed[5] == "[DONE]"


def test_openai_adapter_increments_tool_call_index_across_calls(monkeypatch):
    """Multiple tool events within one turn get monotonically
    increasing index values (mirrors OpenAI's contract for parallel
    tool calls).
    """
    events = [
        {"type": "ready"},
        {"type": "tool", "name": "a", "args": "{}", "status": "ok"},
        {"type": "tool", "name": "b", "args": "{}", "status": "ok"},
        {"type": "tool", "name": "c", "args": "{}", "status": "ok"},
        {"type": "done"},
    ]
    monkeypatch.setattr(
        streaming_mod, "stream_agent_events",
        lambda **kwargs: _fake_events(events),
    )

    import asyncio
    async def _collect():
        return [f async for f in streaming_mod.stream_openai_compatible(
            messages=[{"role": "user", "content": "hi"}],
            model_id="X", session_id="t-multi",
        )]
    parsed = _frames_to_chunks(asyncio.run(_collect()))

    tool_chunks = [
        p for p in parsed
        if isinstance(p, dict) and "tool_calls" in p["choices"][0]["delta"]
    ]
    assert len(tool_chunks) == 3
    assert [c["choices"][0]["delta"]["tool_calls"][0]["index"]
            for c in tool_chunks] == [0, 1, 2]
    assert [c["choices"][0]["delta"]["tool_calls"][0]["function"]["name"]
            for c in tool_chunks] == ["a", "b", "c"]


def test_openai_adapter_skips_tool_event_with_no_name(monkeypatch):
    """A malformed `tool` event without `name` is silently dropped
    rather than emitting a malformed chunk.
    """
    events = [
        {"type": "ready"},
        {"type": "tool", "name": "", "args": "{}"},
        {"type": "token", "delta": "ok"},
        {"type": "done"},
    ]
    monkeypatch.setattr(
        streaming_mod, "stream_agent_events",
        lambda **kwargs: _fake_events(events),
    )

    import asyncio
    async def _collect():
        return [f async for f in streaming_mod.stream_openai_compatible(
            messages=[{"role": "user", "content": "hi"}],
            model_id="X", session_id="t-bad",
        )]
    parsed = _frames_to_chunks(asyncio.run(_collect()))

    deltas = [p["choices"][0]["delta"] for p in parsed if isinstance(p, dict)]
    assert all("tool_calls" not in d for d in deltas)
    # role + content + stop = 3 chunks (+ [DONE] string)
    assert len(parsed) == 4


def test_openai_adapter_tool_before_token_still_emits_role_first(monkeypatch):
    """If the agent fires a tool before any text token, the assistant
    role chunk still has to come first so OpenAI clients see a valid
    delta sequence.
    """
    events = [
        {"type": "ready"},
        {"type": "tool", "name": "first", "args": "{}", "status": "ok"},
        {"type": "done"},
    ]
    monkeypatch.setattr(
        streaming_mod, "stream_agent_events",
        lambda **kwargs: _fake_events(events),
    )

    import asyncio
    async def _collect():
        return [f async for f in streaming_mod.stream_openai_compatible(
            messages=[{"role": "user", "content": "hi"}],
            model_id="X", session_id="t-early",
        )]
    parsed = _frames_to_chunks(asyncio.run(_collect()))

    # ready emits role, so role is on parsed[0]. parsed[1] is the
    # tool chunk. parsed[2] is the stop chunk. parsed[3] is [DONE].
    assert parsed[0]["choices"][0]["delta"] == {"role": "assistant"}
    assert "tool_calls" in parsed[1]["choices"][0]["delta"]


def test_openai_adapter_drops_reasoning_and_spinner(monkeypatch):
    """Reasoning + spinner + segment_end events are filtered out."""
    events = [
        {"type": "ready"},
        {"type": "reasoning", "delta": "internal thought"},
        {"type": "spinner", "label": "thinking..."},
        {"type": "token", "delta": "answer"},
        {"type": "segment_end"},
        {"type": "done", "tokens": 1, "reasoning_tokens": 5},
    ]
    monkeypatch.setattr(
        streaming_mod, "stream_agent_events",
        lambda **kwargs: _fake_events(events),
    )

    import asyncio
    async def _collect():
        return [f async for f in streaming_mod.stream_openai_compatible(
            messages=[{"role": "user", "content": "hi"}],
            model_id="X",
            session_id="t4",
        )]
    parsed = _frames_to_chunks(asyncio.run(_collect()))
    # role + content + stop + [DONE]
    assert len(parsed) == 4
    assert parsed[1]["choices"][0]["delta"] == {"content": "answer"}


# ---------------------------------------------------------------------
# HTTP-level test (FastAPI TestClient)
# ---------------------------------------------------------------------

def test_v1_chat_completions_503_when_streaming_disabled(monkeypatch):
    """When CROWE_STREAM_ENABLED is unset, the endpoint must refuse with
    503 — protects unfinished deployments from accidentally exposing
    the surface.
    """
    monkeypatch.setattr(gateway_mod, "CROWE_STREAM_ENABLED", False)

    # The control_plane lifespan tries to open a real Neon connection;
    # stub it out so the TestClient can start.
    import contextlib
    import control_plane.db as db_mod

    @contextlib.asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    async def _noop_init():
        return None

    monkeypatch.setattr(db_mod, "init_pool", _noop_init)
    monkeypatch.setattr(db_mod, "lifespan", _noop_lifespan)

    # Importing control_plane.main triggers router registration.
    import control_plane.main  # noqa: F401

    async def _fake_resolver():
        return {"plan_id": "scale", "workspace_id": "w_test", "user_id": "u_test"}

    class _FakeDb:
        async def fetchrow(self, *args, **kwargs):
            return None

        async def execute(self, *args, **kwargs):
            return None

    async def _fake_db():
        return _FakeDb()

    from control_plane import app
    from control_plane.db import get_db
    app.dependency_overrides[gateway_mod._resolve_api_key] = _fake_resolver
    app.dependency_overrides[get_db] = _fake_db

    # Replace the app-level lifespan so TestClient doesn't hit the real DB.
    app.router.lifespan_context = _noop_lifespan

    from fastapi.testclient import TestClient
    try:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": "CroweLM-Helio",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                },
            )
            assert resp.status_code == 503
            assert "disabled" in resp.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()
