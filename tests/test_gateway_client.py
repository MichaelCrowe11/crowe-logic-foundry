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
