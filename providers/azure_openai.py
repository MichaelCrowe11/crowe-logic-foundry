"""
Azure OpenAI provider — CroweLM models on Crowe Logic's own Azure AI Foundry.

Targets the OpenAI-compatible `/openai/v1/` surface of an Azure AI Foundry
resource (not the Azure AI Agents SDK). Authenticates with an API key, so it
works without any Azure identity setup.

This is the primary tier for the Crowe Logic Kernel stack — models deployed
inside the `crowelogicos-4667` resource (CroweLM Core = Kimi-K2.5,
CroweLM Kernel = gpt-5.4-nano).

The streaming + tool-calling loop lives in BaseOpenAIProvider; this file
only owns the Azure-specific URL normalization.
"""

from openai import OpenAI

from providers._shared import BaseOpenAIProvider


class AzureOpenAIProvider(BaseOpenAIProvider):
    """OpenAI-compatible provider for Azure AI Foundry deployments.

    Uses the `/openai/v1/` surface with API-key authentication — no
    DefaultAzureCredential, no Azure AI Agents SDK, no `.agent_id` file.
    """

    def __init__(self, model: str, system_instructions: str, endpoint: str, api_key: str,
                 label: str = "CroweLM"):
        super().__init__(model, system_instructions, label)

        # Azure surface looks like:
        #   https://<resource>.openai.azure.com/openai/v1/
        # The OpenAI SDK expects a base_url that points at the "v1" root so
        # it can append `/chat/completions`. Accept a few shapes and
        # normalize.
        base_url = endpoint.rstrip("/")
        if not base_url.endswith("/v1") and "/openai/v1" not in base_url:
            if base_url.endswith("/openai"):
                base_url += "/v1"
            else:
                base_url += "/openai/v1"

        self.client = OpenAI(api_key=api_key, base_url=base_url)
        # cli/crowe_logic.py reads .endpoint to detect provider-recreate
        # cases when the user changes models mid-session — keep the
        # original (not the normalized base_url) so equality comparisons
        # against the env var stay stable.
        self.endpoint = endpoint
