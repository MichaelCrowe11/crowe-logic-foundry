"""
NVIDIA NIM provider. Production inference for CroweLM models.

Uses the OpenAI-compatible API exposed by NVIDIA NIM containers on DGX
Cloud, NVIDIA AI Enterprise, or self-hosted GPU infrastructure. The
streaming + tool-calling loop lives in BaseOpenAIProvider; this file
owns NIM-specific URL normalization and translates the NVCF-native 404
shape into an actionable registry-fix message.
"""

import re

from openai import NotFoundError, OpenAI

from providers._shared import BaseOpenAIProvider


# NVCF returns this exact body when a function deployment is gone:
#   {"status":404,"title":"Not Found",
#    "detail":"Function '<uuid>': Not found for account '<account>'"}
# A bare openai.NotFoundError surfaces this verbatim, which is unactionable
# unless the reader knows that NVIDIA leaves retired model names in
# /v1/models while removing the underlying NVCF function.
_NVCF_FUNCTION_404 = re.compile(
    r"Function '([0-9a-fA-F-]{36})':\s*Not found for account '([^']+)'"
)


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

    def _translate_provider_error(self, exc: Exception) -> Exception | None:
        if not isinstance(exc, NotFoundError):
            return None
        match = _NVCF_FUNCTION_404.search(str(exc))
        if not match:
            return None
        function_id, account = match.group(1), match.group(2)
        return RuntimeError(
            f"NVIDIA NIM model '{self.model}' is no longer deployed for this "
            f"account (NVCF function {function_id} on account {account} returned "
            "404). The model was likely retired or the API key belongs to a "
            "different organization. Update this entry's backend_name in "
            "config/models.extra.json to a model whose function is still live."
        )
