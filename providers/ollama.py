"""
Ollama provider: Chat Completions with streaming and tool calling.

Uses the OpenAI Python SDK pointed at Ollama's OpenAI-compatible API.
Supports both local (localhost:11434) and remote (cloud GPU) Ollama
instances. The streaming + tool-calling loop lives in
BaseOpenAIProvider; this file only owns the Ollama-specific constructor
wiring plus a ``check_cloud_model_availability`` helper that dual-mode
preflight uses to detect paywalled :cloud tags before starting the
dual renderer.
"""

from __future__ import annotations

import json
import os
from typing import NamedTuple

import requests
from openai import OpenAI

from providers._shared import BaseOpenAIProvider


class ModelAvailability(NamedTuple):
    """Result of a lightweight availability probe against an Ollama model."""

    ok: bool
    reason: str | None
    paywalled: bool


class OllamaProvider(BaseOpenAIProvider):
    """Chat Completions provider for Ollama (local or remote)."""

    def __init__(self, model: str, system_instructions: str,
                 base_url: str = "http://localhost:11434/v1",
                 fallback_base_url: str | None = None,
                 label: str = "CroweLM"):
        super().__init__(model, system_instructions, label)
        base_url = select_ollama_base_url(
            model,
            primary_base_url=base_url,
            fallback_base_url=fallback_base_url,
        )
        # Ollama ignores the api_key but the OpenAI SDK requires one.
        self.client = OpenAI(api_key="ollama", base_url=base_url)
        self.base_url = base_url


_PROBE_TIMEOUT_S = 8
_SUBSCRIPTION_MARKER = "requires a subscription"


def _ollama_api_root(base_url: str) -> str:
    url = (base_url or "http://localhost:11434/v1").rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3].rstrip("/")
    return url


def _ollama_model_exists(base_url: str, model_name: str, timeout_s: float = 2.0) -> bool:
    try:
        resp = requests.post(
            f"{_ollama_api_root(base_url)}/api/show",
            json={"name": model_name},
            timeout=timeout_s,
        )
    except requests.RequestException:
        return False
    return resp.status_code == 200


def select_ollama_base_url(
    model_name: str,
    primary_base_url: str = "http://localhost:11434/v1",
    fallback_base_url: str | None = None,
) -> str:
    """Use local Ollama first, then nexus/remote Ollama when configured.

    ``NEXUS_OLLAMA_ENDPOINT`` is already present in the local Crowe Logic
    environment, while ``OLLAMA_FALLBACK_BASE_URL`` provides a generic override
    for other machines.
    """
    primary = primary_base_url or "http://localhost:11434/v1"
    fallback = (
        fallback_base_url
        or os.environ.get("OLLAMA_FALLBACK_BASE_URL", "")
        or os.environ.get("NEXUS_OLLAMA_ENDPOINT", "")
    )

    if not fallback or _ollama_api_root(fallback) == _ollama_api_root(primary):
        return primary
    if _ollama_model_exists(primary, model_name):
        return primary
    if _ollama_model_exists(fallback, model_name):
        return fallback
    return primary


def check_cloud_model_availability(
    model_name: str,
    base_url: str = "http://localhost:11434/v1",
    timeout_s: int = _PROBE_TIMEOUT_S,
) -> ModelAvailability:
    """Probe an Ollama model with a one-token generation to verify it's reachable.

    Detects the paywall error that Ollama Cloud returns as HTTP 200 with a
    ``{"error": "this model requires a subscription, upgrade for access: ..."}``
    body. The OpenAI SDK wouldn't surface this as a standard APIError because
    the response is 200 OK, so we hit the /v1 endpoint directly with requests.

    :param model_name: the Ollama model tag (e.g. ``kimi-k2.6:cloud``)
    :param base_url: OpenAI-compatible endpoint, with or without trailing /v1
    :param timeout_s: seconds to wait for the probe response
    :return: ModelAvailability(ok, reason, paywalled)
    """
    url = base_url.rstrip("/")
    if not url.endswith("/v1"):
        url = url + "/v1"
    url = url + "/chat/completions"
    try:
        resp = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json={
                "model": model_name,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
                "temperature": 0,
            },
            timeout=timeout_s,
        )
    except requests.exceptions.ConnectionError:
        return ModelAvailability(False, "Ollama daemon not reachable", False)
    except requests.exceptions.Timeout:
        return ModelAvailability(False, f"Ollama probe timed out after {timeout_s}s", False)
    except Exception as exc:
        return ModelAvailability(False, f"{type(exc).__name__}: {exc}", False)

    try:
        body = resp.json()
    except ValueError:
        return ModelAvailability(False, f"Non-JSON response (HTTP {resp.status_code})", False)

    if "error" in body and isinstance(body["error"], str):
        err = body["error"]
        paywalled = _SUBSCRIPTION_MARKER in err
        return ModelAvailability(False, err, paywalled)

    if "choices" in body:
        return ModelAvailability(True, None, False)

    return ModelAvailability(False, f"Unexpected response shape: {list(body.keys())[:5]}", False)
