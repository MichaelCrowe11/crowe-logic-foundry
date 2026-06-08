"""
Hosted OpenAI-compatible provider for Crowe Logic-managed open-model serving.

Targets self-hosted vLLM / SGLang / NIM-compatible endpoints that expose the
standard OpenAI chat completions API.
"""

from openai import OpenAI

from providers._shared import BaseOpenAIProvider


class HostedOpenAIProvider(BaseOpenAIProvider):
    """Provider for self-hosted OpenAI-compatible model endpoints."""

    def __init__(
        self,
        model: str,
        system_instructions: str,
        endpoint: str,
        api_key: str = "",
        label: str = "CroweLM",
        extra_headers: dict | None = None,
    ):
        super().__init__(model, system_instructions, label)

        base_url = endpoint.rstrip("/")
        # Bare hosts need /v1 appended; endpoints that already carry their own
        # OpenAI path (e.g. Gemini's /v1beta/openai) are used as-is.
        if not base_url.endswith("/v1") and "/openai" not in base_url:
            base_url += "/v1"

        self.client = OpenAI(
            api_key=api_key or "crowe-logic",
            base_url=base_url,
            # e.g. Modal proxy-auth (Modal-Key/Modal-Secret) — auth that lives
            # in headers rather than the bearer token.
            default_headers=extra_headers or None,
        )
        self.endpoint = endpoint
