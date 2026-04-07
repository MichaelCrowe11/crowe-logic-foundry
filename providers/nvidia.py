"""
NVIDIA NIM provider — Production inference for CroweLM models.

Uses the OpenAI-compatible API exposed by NVIDIA NIM containers on DGX
Cloud, NVIDIA AI Enterprise, or self-hosted GPU infrastructure. The
streaming + tool-calling loop lives in BaseOpenAIProvider; this file
only owns the NIM-specific URL normalization.
"""

from openai import OpenAI

from providers._shared import BaseOpenAIProvider


class NvidiaProvider(BaseOpenAIProvider):
    """Production inference provider for CroweLM models on NVIDIA NIM."""

    def __init__(self, model: str, system_instructions: str, endpoint: str, api_key: str,
                 label: str = "CroweLM"):
        super().__init__(model, system_instructions, label)

        base_url = endpoint.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url += "/v1"
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.endpoint = endpoint
