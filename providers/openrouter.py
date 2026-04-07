"""
OpenRouter provider — Chat Completions with streaming and tool calling.

Uses the OpenAI Python SDK pointed at OpenRouter's API. Works with any
OpenAI-compatible endpoint (Together AI, Fireworks, Groq, etc).

The streaming + tool-calling loop lives in BaseOpenAIProvider; this
file only owns the OpenRouter-specific constructor wiring.
"""

from openai import OpenAI

from providers._shared import BaseOpenAIProvider


class OpenRouterProvider(BaseOpenAIProvider):
    """Chat Completions provider for OpenRouter (or any OpenAI-compatible API)."""

    def __init__(self, api_key: str, base_url: str, model: str, system_instructions: str,
                 label: str = "CroweLM"):
        super().__init__(model, system_instructions, label)
        self.client = OpenAI(api_key=api_key, base_url=base_url)
