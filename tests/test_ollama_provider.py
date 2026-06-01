"""Tests for local/nexus Ollama routing."""

from providers.ollama import select_ollama_base_url


class _Resp:
    def __init__(self, status_code: int):
        self.status_code = status_code


def test_select_ollama_base_url_prefers_local_when_model_exists(monkeypatch):
    calls = []

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        return _Resp(200)

    monkeypatch.setattr("providers.ollama.requests.post", fake_post)
    selected = select_ollama_base_url(
        "crowelm-unified-v2:latest",
        primary_base_url="http://localhost:11434/v1",
        fallback_base_url="http://nexus:11434/v1",
    )

    assert selected == "http://localhost:11434/v1"
    assert len(calls) == 1


def test_select_ollama_base_url_uses_nexus_when_local_missing(monkeypatch):
    calls = []

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        if "localhost" in url:
            return _Resp(404)
        return _Resp(200)

    monkeypatch.setattr("providers.ollama.requests.post", fake_post)
    selected = select_ollama_base_url(
        "crowelogic/mike-clone:latest",
        primary_base_url="http://localhost:11434/v1",
        fallback_base_url="http://nexus:11434/v1",
    )

    assert selected == "http://nexus:11434/v1"
    assert [call[0] for call in calls] == [
        "http://localhost:11434/api/show",
        "http://nexus:11434/api/show",
    ]

