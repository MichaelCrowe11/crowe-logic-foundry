"""
CroweLM Unified Gateway.

A small, stable surface that all Crowe Logic surfaces (VS Code chat
participant, Foundry CLI, control-plane HTTP gateway, southwestmushrooms
storefront) call into. It hides the difference between watsonx.ai,
Anthropic, Azure OpenAI, NVIDIA NIM, and Ollama backends behind a single
brand-id namespace.

Responsibilities:
  * Resolve a Crowe brand-id to its provider + base model
  * For watsonx: dispatch to watsonx_adapter.chat
  * For other providers: defer to the existing MODEL_CHAIN dispatch (lazy
    import to avoid pulling the giant cli/crowe_logic stack on cold start)
  * Provide a deterministic listing for the VS Code model picker
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from . import watsonx_adapter
from .brand_registry import ALL_BRANDS, BY_BRAND, resolve


def list_brands(*, capability: Optional[str] = None) -> list[dict]:
    out = []
    for b in ALL_BRANDS:
        if capability and capability not in b.capabilities:
            continue
        out.append({
            "id": b.brand_id,
            "name": b.display_name,
            "tier": b.tier,
            "tags": list(b.tags),
            "tunable": b.tunable,
            "tuned": bool(b.tuned_asset),
            "base_model": b.base_model,
            "provider": b.upstream_provider,
            "description": b.description,
        })
    return out


def chat(brand_id: str, messages: list[dict], **opts: Any) -> dict:
    """Send a chat completion to whichever provider backs the brand.

    Currently:
      * Every brand in the CroweLM registry is watsonx-backed (since the
        registry was built from /ml/v1/foundation_model_specs).
      * If a brand id is unknown to the registry but lives in the legacy
        MODEL_CHAIN, we delegate (best-effort).
    """
    brand = resolve(brand_id)
    if brand is not None:
        return watsonx_adapter.chat(brand.brand_id, messages, **opts)
    # legacy fallback
    raise watsonx_adapter.WatsonxError(
        f"unknown brand: {brand_id!r}. Known brands: "
        + ", ".join(sorted(BY_BRAND))[:600]
    )


def embed(brand_id: str, inputs: list[str], **opts: Any) -> dict:
    return watsonx_adapter.embed(brand_id, inputs, **opts)


def rerank(brand_id: str, query: str, documents: Iterable[str], **opts: Any) -> dict:
    return watsonx_adapter.rerank(brand_id, query, documents, **opts)


def health() -> dict:
    return watsonx_adapter.health_check()


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "list":
        print(json.dumps(list_brands(), indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "health":
        print(json.dumps(health(), indent=2))
    else:
        print("Usage: python -m config.crowelm.gateway {list,health}")
