"""Retry-policy tests for the tenacity-based DeepParallel dispatch.

Injects a fake requests.Session so the retry semantics are verified with no
Ollama and no real backoff sleep. Proves the refactor preserved behavior:
retry on 5xx/timeout, surface 4xx/connection/unknown immediately.

    pytest tests/test_deepparallel_retry.py -v
"""

from __future__ import annotations

import json

import pytest
import requests

import tools.deepparallel as dp


class FakeResp:
    def __init__(self, status=200, content="answer", reason="OK", text=""):
        self.status_code = status
        self.reason = reason
        self._content = content
        self.text = text

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} {self.reason}")


class FakeSession:
    """Replays a list of behaviors; each is a FakeResp to return or an
    Exception to raise. The last behavior repeats once exhausted."""

    def __init__(self, behaviors):
        self.behaviors = list(behaviors)
        self.calls = 0

    def post(self, *a, **k):
        b = self.behaviors[min(self.calls, len(self.behaviors) - 1)]
        self.calls += 1
        if isinstance(b, Exception):
            raise b
        return b


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    # tenacity calls _RETRY_SLEEP between attempts; make tests instant.
    monkeypatch.setattr(dp, "_RETRY_SLEEP", lambda *_a, **_k: None)


def _dispatch(session):
    return dp._dispatch_with_retry(
        [{"role": "user", "content": "q"}], 0.7, 256, session=session
    )


def test_retries_timeout_then_succeeds():
    s = FakeSession(
        [
            requests.exceptions.Timeout(),
            requests.exceptions.Timeout(),
            FakeResp(200, "ok"),
        ]
    )
    assert _dispatch(s) == "ok"
    assert s.calls == 3  # two transient failures, then success — within budget


def test_5xx_retries_until_budget_then_reports_exhaustion():
    s = FakeSession([FakeResp(503, reason="Service Unavailable")])
    out = json.loads(_dispatch(s))
    assert "retry budget exhausted" in out["error"]
    assert s.calls == dp._MAX_RETRIES  # exactly the cap, no more


def test_4xx_is_surfaced_immediately_no_retry():
    s = FakeSession([FakeResp(400, reason="Bad Request", text="nope")])
    out = json.loads(_dispatch(s))
    assert "HTTPError 400" in out["error"]
    assert s.calls == 1  # 4xx must not retry


def test_connection_refused_is_terminal():
    s = FakeSession([requests.exceptions.ConnectionError()])
    out = json.loads(_dispatch(s))
    assert "Ollama not running" in out["error"]
    assert s.calls == 1
