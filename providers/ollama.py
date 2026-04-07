"""
Ollama provider — Chat Completions with streaming and tool calling.

Uses the OpenAI Python SDK pointed at Ollama's OpenAI-compatible API.
Supports both local (localhost:11434) and remote (cloud GPU) Ollama
instances. The streaming + tool-calling loop lives in
BaseOpenAIProvider; this file only owns the Ollama-specific constructor
wiring.
"""

from openai import OpenAI

from providers._shared import BaseOpenAIProvider


class OllamaProvider(BaseOpenAIProvider):
    """Chat Completions provider for Ollama (local or remote)."""

    def __init__(self, model: str, system_instructions: str,
                 base_url: str = "http://localhost:11434/v1",
                 label: str = "CroweLM"):
        super().__init__(model, system_instructions, label)
        # Ollama ignores the api_key but the OpenAI SDK requires one.
        self.client = OpenAI(api_key="ollama", base_url=base_url)
        self.base_url = base_url
