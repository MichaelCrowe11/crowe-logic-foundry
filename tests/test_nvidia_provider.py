"""Tests for NVIDIA NIM provider error translation."""

from __future__ import annotations

import httpx
from openai import APIError, NotFoundError

from providers.nvidia import NvidiaProvider


def _make_provider() -> NvidiaProvider:
    return NvidiaProvider(
        model="nvidia/llama-3.1-nemotron-ultra-253b-v1",
        system_instructions="test",
        endpoint="https://integrate.api.nvidia.com",
        api_key="test-key",
    )


def _nvcf_404(detail: str) -> NotFoundError:
    request = httpx.Request("POST", "https://integrate.api.nvidia.com/v1/chat/completions")
    response = httpx.Response(404, request=request)
    return NotFoundError(
        message=f"Error code: 404 - {{'status': 404, 'title': 'Not Found', 'detail': \"{detail}\"}}",
        response=response,
        body=None,
    )


def test_translates_nvcf_function_retired_to_actionable_runtime_error():
    provider = _make_provider()
    exc = _nvcf_404(
        "Function '84bf12ff-edbd-4435-baea-0fa6a7453d2e': Not found for account 'AcctXyz123'"
    )

    translated = provider._translate_provider_error(exc)

    assert translated is not None
    msg = str(translated)
    assert "NVIDIA NIM model 'nvidia/llama-3.1-nemotron-ultra-253b-v1'" in msg
    assert "84bf12ff-edbd-4435-baea-0fa6a7453d2e" in msg
    assert "AcctXyz123" in msg
    assert "config/models.extra.json" in msg


def test_passes_through_unrelated_404_messages():
    """An OpenAI-shape 404 (model truly invalid) should not be remapped here."""
    provider = _make_provider()
    exc = _nvcf_404(
        "The model `nvidia/does-not-exist` does not exist."
    )

    assert provider._translate_provider_error(exc) is None


def test_passes_through_non_404_errors():
    request = httpx.Request("POST", "https://integrate.api.nvidia.com/v1/chat/completions")
    response = httpx.Response(500, request=request)
    exc = APIError(message="server boom", request=request, body=None)
    exc.response = response

    provider = _make_provider()
    assert provider._translate_provider_error(exc) is None


def test_base_provider_translator_is_noop():
    """Default hook on the base class returns None so unrelated providers keep raising."""
    from providers._shared import BaseOpenAIProvider

    bp = BaseOpenAIProvider(model="x", system_instructions="y")
    assert bp._translate_provider_error(RuntimeError("anything")) is None
