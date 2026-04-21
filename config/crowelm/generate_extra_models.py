"""
Generate config/models.extra.json from the CroweLM brand registry, so the
existing Foundry MODEL_CHAIN merge picks up every watsonx-backed tier.

Usage:
    python -m config.crowelm.generate_extra_models > config/models.extra.json
"""
from __future__ import annotations
import json
import sys
from .brand_registry import ALL_BRANDS


_TYPE_BY_TIER = {
    "reasoning": "reasoning",
    "research": "reasoning",
    "code": "code",
    "vision": "vision",
    "multilingual": "reasoning",
    "embedding": "embedding",
    "rerank": "rerank",
    "time_series": "forecast",
    "safety": "moderation",
}


def _system_prompt(brand) -> str:
    return (f"You are {brand.display_name}, a Crowe Logic model. "
            f"{brand.description}")


def build() -> list[dict]:
    out: list[dict] = []
    for b in ALL_BRANDS:
        # Only the chat-capable brands belong in the conversational MODEL_CHAIN.
        if "text_chat" not in b.capabilities:
            continue
        out.append({
            "name": b.brand_id,
            "label": b.display_name,
            "type": _TYPE_BY_TIER.get(b.tier, "reasoning"),
            "provider": "watsonx",
            "backend_name": b.tuned_asset or b.base_model,
            "aliases": [
                b.brand_id,
                b.brand_id.replace("crowelm-", ""),
                b.display_name,
                b.display_name.replace("CroweLM ", "").lower(),
                b.base_model,
            ],
            "system_prompt": _system_prompt(b),
            "tier": b.tier,
            "tags": list(b.tags),
            "tunable": b.tunable,
        })
    return out


if __name__ == "__main__":
    json.dump({"models": build()}, sys.stdout, indent=2)
    sys.stdout.write("\n")
