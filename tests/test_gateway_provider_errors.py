"""Tests for clean gateway error surfaces when the provider SDK call fails.

Regression context: a user pasted ~7,148 lines of code; the provider rejected
the oversized prompt (context-window overflow) and the SDK exception bubbled
straight through ``_call_provider`` to FastAPI's default 500. Three behaviours
are required instead:

  * oversize / context-length errors  -> 413, "your input is too large"
  * any other provider failure         -> 503 tier_unavailable (preserve f59dede)
  * empty ``choices`` in the response  -> no IndexError/500; clean empty content
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import config.agent_config as agent_config
import providers.hosted_openai as hosted_mod

pytest.importorskip("fastapi")

from fastapi import HTTPException

import control_plane.gateway as gateway_mod


def _install_fake_provider(monkeypatch, *, create):
    """Wire a fake openai_compat provider whose SDK ``create`` is ``create``."""
    cfg = {
        "name": "gpt-5.4",
        "label": "CroweLM Titan",
        "provider": "openai_compat",
        "backend_name": "z-ai/glm5.1",
        "endpoint_env": "CROWE_OPEN_ENDPOINT",
        "api_key_env": "CROWE_OPEN_API_KEY",
    }
    monkeypatch.setenv("CROWE_OPEN_ENDPOINT", "https://models.crowe.logic")
    monkeypatch.delenv("CROWE_OPEN_API_KEY", raising=False)
    monkeypatch.setattr(agent_config, "resolve_model_config", lambda _model: cfg)
    monkeypatch.setattr(agent_config, "MODEL_CHAIN", [cfg])

    class _FakeHostedProvider:
        def __init__(self, *, model, system_instructions, endpoint, api_key, label, extra_headers=None):
            self.model = model
            self.client = SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(create=create)
                )
            )

    monkeypatch.setattr(hosted_mod, "HostedOpenAIProvider", _FakeHostedProvider)


def _call(model="gpt-5.4"):
    return asyncio.run(
        gateway_mod._call_provider(model, [{"role": "user", "content": "x" * 100}])
    )


@pytest.mark.parametrize(
    "message",
    [
        "This model's maximum context length is 128000 tokens, however you requested 200000",
        "context_length_exceeded: input too long",
        "Request exceeds the maximum context window for this deployment",
    ],
)
def test_oversize_prompt_surfaces_413(monkeypatch, message):
    """A context-window overflow must be a clean 413 telling the user to trim
    the input — NOT a bare 500, and NOT a misleading 503 'retry another tier'
    (every tier will reject an over-budget paste)."""

    def _create(**kwargs):
        raise RuntimeError(message)

    _install_fake_provider(monkeypatch, create=_create)

    with pytest.raises(HTTPException) as excinfo:
        _call()

    exc = excinfo.value
    assert exc.status_code == 413
    detail = exc.detail
    assert isinstance(detail, dict)
    assert detail.get("error") == "input_too_large"
    # Honest, actionable copy: the input is too big, trim/reduce it.
    assert "large" in detail.get("message", "").lower()
    assert any(
        word in detail.get("hint", "").lower() for word in ("trim", "reduce", "shorten")
    )


def test_generic_provider_failure_surfaces_503_tier_unavailable(monkeypatch):
    """Any non-oversize provider failure preserves f59dede: clean 503
    tier_unavailable that the client-side fallback hops from."""

    def _create(**kwargs):
        raise RuntimeError("connection reset by peer")

    _install_fake_provider(monkeypatch, create=_create)

    with pytest.raises(HTTPException) as excinfo:
        _call()

    exc = excinfo.value
    assert exc.status_code == 503
    assert isinstance(exc.detail, dict)
    assert exc.detail.get("error") == "tier_unavailable"
    assert exc.detail.get("tier") == "z-ai/glm5.1"


def test_httpexception_from_sync_call_passes_through(monkeypatch):
    """Credential/unsupported HTTPExceptions raised inside _sync_call (e.g. a
    503 'missing credentials') must NOT be swallowed and re-wrapped — the
    `except HTTPException: raise` passthrough preserves the original status."""

    def _create(**kwargs):
        raise HTTPException(status_code=503, detail="Missing credentials for gpt-5.4")

    _install_fake_provider(monkeypatch, create=_create)

    with pytest.raises(HTTPException) as excinfo:
        _call()

    exc = excinfo.value
    assert exc.status_code == 503
    assert exc.detail == "Missing credentials for gpt-5.4"


def test_empty_choices_does_not_raise_indexerror(monkeypatch):
    """A response with empty ``choices`` must not blow up with IndexError/500.
    The gateway returns clean empty content (token counts still honoured)."""

    def _create(**kwargs):
        return SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(prompt_tokens=5, completion_tokens=0),
        )

    _install_fake_provider(monkeypatch, create=_create)

    content, prompt_tokens, completion_tokens = _call()
    assert content == ""
    assert prompt_tokens == 5
    assert completion_tokens == 0
