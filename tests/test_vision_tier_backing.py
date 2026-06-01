"""CroweLM Vision tier must be backed by gpt-4o on the live AZURE_CORE resource.

The prior backing (NVIDIA nemotron-nano-12b-v2-vl) was a weak vision model on a
separate provider. gpt-4o on crowelm-prod-eastus2 is a far stronger multimodal
model, credit-funded and in-region, so customer image work (e.g. mycology grow
diagnostics) routes through Azure instead of NVIDIA NIM.
"""

from config.agent_config import resolve_model_config


def _vision():
    return resolve_model_config("CroweLM Vision")


def test_vision_tier_is_azure_gpt4o():
    cfg = _vision()
    assert cfg is not None
    assert cfg["provider"] == "azure_openai"
    assert cfg["backend_name"] == "gpt-4o"
    assert cfg.get("endpoint_env") == "AZURE_CORE_ENDPOINT"
    assert cfg.get("api_key_env") == "AZURE_CORE_API_KEY"
    assert cfg["type"] == "vision"


def test_vision_tier_keeps_first_party_brand_guard():
    # Leak-prevention is the per-tier system prompt. A managed gpt-4o tier
    # must carry the same "do not volunteer vendor names" guard as its peers.
    cfg = _vision()
    assert cfg is not None
    prompt = cfg.get("prompt") or ""
    assert prompt, "Vision tier must have a system prompt"
    assert "crowelm vision" in prompt.lower()
    assert "vendor" in prompt.lower()


def test_vision_tier_is_selectable_by_alias():
    # Users should be able to pick it by a short alias, like other tiers.
    assert resolve_model_config("vision") is not None
    assert resolve_model_config("crowelm-vision") is not None
