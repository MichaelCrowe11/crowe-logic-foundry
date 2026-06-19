"""Tests for cli.gateway_client — bearer-authed turn routing to the foundry gateway.

httpx.post and the token accessor are patched, so these run offline and assert
the contract: bearer attached, 401 triggers one refresh+retry, 403 -> PlanDenied.
"""

import pytest

from cli import gateway_client as gc


class FakeResp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._p = payload or {}

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"unexpected raise_for_status at {self.status_code}")


def test_happy_path_attaches_bearer(monkeypatch):
    seen = {}

    def fake_post(url, json, headers, timeout):
        seen["auth"] = headers.get("Authorization")
        seen["url"] = url
        seen["body"] = json
        return FakeResp(200, {"content": "hi", "model": json["model"], "usage": {}})

    monkeypatch.setattr(gc, "_token", lambda: "TKN")
    monkeypatch.setattr(gc.httpx, "post", fake_post)

    out = gc.chat("gpt-5.4", [{"role": "user", "content": "hey"}])
    assert out["content"] == "hi"
    assert seen["auth"] == "Bearer TKN"
    assert seen["url"].endswith("/api/gateway/chat")
    assert seen["body"]["model"] == "gpt-5.4"


def test_401_refreshes_once_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake_post(url, json, headers, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeResp(401)
        return FakeResp(200, {"content": "ok"})

    # token forced stale on 401 so the retry picks a fresh one
    monkeypatch.setattr(gc, "_token", lambda: "T")
    monkeypatch.setattr(gc.auth, "load_creds", lambda: {"expires_at": 0})
    monkeypatch.setattr(gc.auth, "save_creds", lambda c: None)
    monkeypatch.setattr(gc.httpx, "post", fake_post)

    out = gc.chat("m", [])
    assert out["content"] == "ok"
    assert calls["n"] == 2


def test_second_401_raises_not_logged_in(monkeypatch):
    monkeypatch.setattr(gc, "_token", lambda: "T")
    monkeypatch.setattr(gc.auth, "load_creds", lambda: {"expires_at": 0})
    monkeypatch.setattr(gc.auth, "save_creds", lambda c: None)
    monkeypatch.setattr(
        gc.httpx, "post", lambda url, json, headers, timeout: FakeResp(401)
    )
    with pytest.raises(gc.auth.NotLoggedIn):
        gc.chat("m", [])


def test_403_raises_plan_denied(monkeypatch):
    monkeypatch.setattr(gc, "_token", lambda: "T")
    monkeypatch.setattr(
        gc.httpx,
        "post",
        lambda url, json, headers, timeout: FakeResp(
            403, {"detail": "requires team plan"}
        ),
    )
    with pytest.raises(gc.PlanDenied) as exc:
        gc.chat("m", [])
    assert "team" in str(exc.value)


# ── Streaming gateway (CSP / reasoning) ──────────────────────────────────────


class FakeStream:
    """Context-manager stand-in for httpx.stream()."""

    def __init__(self, status, lines=None, payload=None):
        self.status_code = status
        self._lines = lines or []
        self._p = payload or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_lines(self):
        for ln in self._lines:
            yield ln

    def read(self):
        return b""

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"unexpected raise at {self.status_code}")


def test_stream_base_env_precedence(monkeypatch):
    monkeypatch.delenv("CROWE_LOGIC_GATEWAY_STREAM_URL", raising=False)
    monkeypatch.delenv("FOUNDRY_BASE_URL", raising=False)
    assert gc.stream_base() is None
    assert gc.streaming_available() is False

    monkeypatch.setenv("FOUNDRY_BASE_URL", "https://foundry.example/")
    assert gc.stream_base() == "https://foundry.example"  # trailing slash stripped
    assert gc.streaming_available() is True

    # Explicit override wins over the inherited dp endpoint.
    monkeypatch.setenv("CROWE_LOGIC_GATEWAY_STREAM_URL", "https://csp.example")
    assert gc.stream_base() == "https://csp.example"


def test_parse_sse_lines_splits_thinking_and_content():
    lines = [
        'data: {"choices":[{"delta":{"reasoning_content":"weighing "}}]}',
        'data: {"choices":[{"delta":{"reasoning_content":"options"}}]}',
        'data: {"choices":[{"delta":{"content":"Hello"}}]}',
        "data: [DONE]",
        'data: {"choices":[{"delta":{"content":"never"}}]}',  # after DONE: ignored
    ]
    out = list(gc._parse_sse_lines(lines))
    assert out == [
        ("thinking", "weighing "),
        ("thinking", "options"),
        ("content", "Hello"),
    ]


def test_stream_chat_unavailable_without_base(monkeypatch):
    monkeypatch.delenv("CROWE_LOGIC_GATEWAY_STREAM_URL", raising=False)
    monkeypatch.delenv("FOUNDRY_BASE_URL", raising=False)
    with pytest.raises(gc.StreamingUnavailable):
        list(gc.stream_chat("crowelm-supreme", [{"role": "user", "content": "hi"}]))


def test_stream_chat_yields_thinking_then_content(monkeypatch):
    monkeypatch.setenv("FOUNDRY_BASE_URL", "https://foundry.example")
    seen = {}

    def fake_stream(method, url, json, headers, timeout):
        seen["method"] = method
        seen["url"] = url
        seen["auth"] = headers.get("Authorization")
        seen["stream"] = json.get("stream")
        return FakeStream(
            200,
            lines=[
                'data: {"choices":[{"delta":{"reasoning_content":"hmm"}}]}',
                'data: {"choices":[{"delta":{"content":"Hi"}}]}',
                "data: [DONE]",
            ],
        )

    monkeypatch.setattr(gc, "_token", lambda: "TKN")
    monkeypatch.setattr(gc.httpx, "stream", fake_stream)

    out = list(gc.stream_chat("crowelm-supreme", [{"role": "user", "content": "hey"}]))
    assert out == [("thinking", "hmm"), ("content", "Hi")]
    assert seen["method"] == "POST"
    assert seen["url"] == "https://foundry.example/v1/chat/completions"
    assert seen["auth"] == "Bearer TKN"
    assert seen["stream"] is True


def test_stream_chat_404_raises_unavailable(monkeypatch):
    monkeypatch.setenv("FOUNDRY_BASE_URL", "https://foundry.example")
    monkeypatch.setattr(gc, "_token", lambda: "T")
    monkeypatch.setattr(
        gc.httpx, "stream", lambda *a, **k: FakeStream(404)
    )
    with pytest.raises(gc.StreamingUnavailable):
        list(gc.stream_chat("crowelm-supreme", [{"role": "user", "content": "hi"}]))


def test_stream_chat_403_raises_plan_denied(monkeypatch):
    monkeypatch.setenv("FOUNDRY_BASE_URL", "https://foundry.example")
    monkeypatch.setattr(gc, "_token", lambda: "T")
    monkeypatch.setattr(
        gc.httpx,
        "stream",
        lambda *a, **k: FakeStream(403, payload={"detail": "tier locked"}),
    )
    with pytest.raises(gc.PlanDenied):
        list(gc.stream_chat("crowelm-supreme", [{"role": "user", "content": "hi"}]))
