"""Every chat model in MODEL_CHAIN must resolve to a real system-prompt file.

This is the contract that keeps routing wired: when a model is added or
rebranded, this test fails until a prompt file (or a base-alias collapse)
exists for it. Non-chat models (embeddings, image/video, meta-router) are
explicitly excluded — they have no chat persona.
"""

from config import agent_config as ac
from config.prompt_loader import slug_for, variant_prompt_text, normalize_slug

NONCHAT_BACKENDS = {
    "Cohere-embed-v4",  # CroweLM Filament Pro (embeddings)
    "text-embedding-3-large",  # CroweLM Embed Large (embeddings)
    "sora-2",  # CroweLM Reel (video)
    "model-router",  # CroweLM Model Router (meta-routing)
}


def _chat_models():
    return [c for c in ac.MODEL_CHAIN if c.get("name") not in NONCHAT_BACKENDS]


def test_every_chat_model_resolves_to_a_prompt_file():
    missing = []
    for cfg in _chat_models():
        slug = slug_for(cfg)
        if not variant_prompt_text(slug):
            missing.append(
                f"{cfg.get('label')} (slug={slug!r}, name={cfg.get('name')})"
            )
    assert not missing, "Chat models with no resolvable prompt:\n  " + "\n  ".join(
        missing
    )


def test_nonchat_backends_are_present_in_chain():
    names = {c.get("name") for c in ac.MODEL_CHAIN}
    stale = NONCHAT_BACKENDS - names
    assert not stale, f"NONCHAT_BACKENDS lists models no longer in the chain: {stale}"


def test_normalize_collapses_tier_suffixed_duplicates():
    assert normalize_slug("titan-premium") == "titan"
    assert normalize_slug("apex-premium") == "apex"
    assert normalize_slug("sovereign-premium") == "sovereign"
    assert normalize_slug("prime-premium") == "prime"
    assert normalize_slug("dense-managed") == "dense"
    assert normalize_slug("dense-legacy") == "dense"
    assert normalize_slug("talon-super") == "talon"
    assert normalize_slug("talon-nano") == "talon"
    assert normalize_slug("vanguard-super") == "vanguard"
    assert normalize_slug("vanguard-nano") == "vanguard"
    assert normalize_slug("titan") == "titan"
    assert normalize_slug("frontier") == "frontier"
