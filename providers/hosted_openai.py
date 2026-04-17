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
    ):
        super().__init__(model, system_instructions, label)

        base_url = endpoint.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url += "/v1"

        self.client = OpenAI(
            api_key=api_key or "crowe-logic",
            base_url=base_url,
        )
        self.endpoint = endpoint
