"""
CroweLM Brand Registry — proprietary aliases over watsonx.ai foundation models.

Single source of truth for the Crowe Logic model catalog. Every model
exposed by the platform (chat, code, vision, embedding, rerank, time-series,
safety) routes through this registry. Inference adapters and the VS Code
extension consume `BRANDS` to render the user-facing menu and to translate
a Crowe brand id into the underlying watsonx.ai `model_id`.

IMPORTANT: We do **not** claim ownership of base weights. Each entry
distinguishes:
  - `base_model`: the watsonx.ai/IBM/Meta/Mistral upstream identifier
  - `tuned_asset`: a string slug for the Crowe Logic LoRA / prompt-tuning
    asset that wraps the base. When `tuned_asset` is None the brand is a
    pass-through alias; when set, the inference adapter loads the tuned
    deployment.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass(frozen=True)
class CroweBrand:
    brand_id: str
    display_name: str
    tier: str
    base_model: str
    upstream_provider: str
    capabilities: tuple[str, ...]
    description: str
    tunable: bool = False
    tuned_asset: Optional[str] = None
    deprecated: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)


REASONING: list[CroweBrand] = [
    CroweBrand(
        "crowelm-titan",
        "CroweLM Titan",
        "reasoning",
        "meta-llama/llama-3-3-70b-instruct",
        "Meta",
        ("text_chat", "rag", "sql_rag", "multilingual"),
        "Flagship reasoning model. 70B Llama 3.3 instruct. Best for hard plans, "
        "multi-step research, and complex agent runs.",
        tags=("flagship", "reasoning"),
    ),
    CroweBrand(
        "crowelm-apex",
        "CroweLM Apex",
        "reasoning",
        "meta-llama/llama-4-maverick-17b-128e-instruct-fp8",
        "Meta",
        ("text_chat", "image_chat", "rag", "multilingual"),
        "High-performance reasoning with vision. Llama 4 Maverick 17B/128E.",
        tags=("multimodal", "reasoning"),
    ),
    CroweBrand(
        "crowelm-oracle",
        "CroweLM Oracle",
        "reasoning",
        "mistralai/mistral-medium-2505",
        "Mistral AI",
        ("text_chat", "image_chat", "rag", "sql_rag", "multilingual"),
        "Balanced reasoning. Mistral Medium 2505 with strong tool use.",
        tags=("reasoning", "tool_use"),
    ),
    CroweBrand(
        "crowelm-sovereign",
        "CroweLM Sovereign",
        "reasoning",
        "mistral-large-2512",
        "Mistral AI",
        ("text_chat", "rag"),
        "Sovereign-grade reasoning. Mistral Large 2512.",
        tags=("reasoning",),
    ),
    CroweBrand(
        "crowelm-prime",
        "CroweLM Prime",
        "reasoning",
        "ibm/granite-4-h-small",
        "IBM",
        ("text_chat", "rag"),
        "Granite 4 H Small. Crowe Logic's primary fine-tune target — "
        "this is the brand the CroweLM unified dataset specializes.",
        tunable=True,
        tags=("reasoning", "fine_tuned_target"),
    ),
    CroweBrand(
        "crowelm-nexus",
        "CroweLM Nexus",
        "reasoning",
        "ibm/granite-3-8b-instruct",
        "IBM",
        ("text_chat", "rag"),
        "Granite 3.0 8B Instruct. Efficient general reasoning.",
        tags=("reasoning",),
    ),
    CroweBrand(
        "crowelm-reason",
        "CroweLM Reason",
        "reasoning",
        "meta-llama/llama-3-1-8b",
        "Meta",
        ("text_chat", "rag"),
        "Lightweight reasoning, LoRA-tunable. Llama 3.1 8B base.",
        tunable=True,
        tags=("reasoning", "tunable"),
    ),
    CroweBrand(
        "crowelm-hyphae-nexus",
        "CroweLM Hyphae Nexus",
        "reasoning",
        "kimi-k2.6:cloud",
        "Moonshot (Ollama Cloud via Nexus)",
        ("text_chat", "rag", "multilingual", "tool_use"),
        "Frontier trillion-parameter reasoning routed through the Nexus GPU "
        "node's Ollama Cloud bridge at http://nexus:11434/v1 (OpenAI-compatible). "
        "Sibling route to the Azure-deployed CroweLM Hyphae; this one keeps "
        "inference on the Crowe-controlled Nexus edge node.",
        tags=("reasoning", "frontier", "nexus", "cloud_proxy"),
    ),
]

SYNAPSE: list[CroweBrand] = [
    CroweBrand(
        "crowelm-synapse",
        "CroweLM Synapse",
        "research",
        "ibm/granite-3-1-8b-base",
        "IBM",
        ("base_completion", "lora_target"),
        "Raw Granite 3.1 8B base. The substrate for proprietary LoRA fine-tunes "
        "against the CroweLM unified dataset.",
        tunable=True,
        tags=("base", "tunable", "research"),
    ),
    CroweBrand(
        "crowelm-monolith",
        "CroweLM Monolith",
        "research",
        "meta-llama/llama-3-1-70b-gptq",
        "Meta",
        ("base_completion", "lora_target"),
        "70B Llama 3.1 GPTQ. Heavy-iron LoRA target for the largest Crowe "
        "specializations.",
        tunable=True,
        tags=("base", "tunable", "research"),
    ),
]

CODE: list[CroweBrand] = [
    CroweBrand(
        "crowelm-forge",
        "CroweLM Forge",
        "code",
        "ibm/granite-8b-code-instruct",
        "IBM",
        ("text_chat", "code_generation"),
        "Granite 8B Code Instruct. Default code generator inside the Foundry IDE "
        "chat participant.",
        tags=("code",),
    ),
    CroweBrand(
        "crowelm-architect",
        "CroweLM Architect",
        "code",
        "openai/gpt-oss-120b",
        "OpenAI",
        ("text_chat", "sql_rag"),
        "GPT-OSS 120B. Heavy-context architecture and refactor work.",
        tags=("code", "long_context"),
    ),
    CroweBrand(
        "crowelm-mason",
        "CroweLM Mason",
        "code",
        "mistralai/mistral-small-3-1-24b-instruct-2503",
        "Mistral AI",
        ("text_chat", "image_chat", "rag", "sql_rag"),
        "Mistral Small 3.1 24B Instruct. Workhorse for inline edits, test "
        "generation, and multi-file diffs.",
        tags=("code", "workhorse"),
    ),
]

VISION: list[CroweBrand] = [
    CroweBrand(
        "crowelm-lens",
        "CroweLM Lens",
        "vision",
        "meta-llama/llama-3-2-11b-vision-instruct",
        "Meta",
        ("image_chat", "text_chat"),
        "Llama 3.2 11B Vision Instruct. Default Crowe Vision backbone — "
        "substrate photos, plate reads, fruiting body ID.",
        tags=("vision",),
    ),
    CroweBrand(
        "crowelm-spectrum",
        "CroweLM Spectrum",
        "vision",
        "meta-llama/llama-3-2-90b-vision-instruct",
        "Meta",
        ("image_chat", "text_chat"),
        "Llama 3.2 90B Vision Instruct. High-fidelity vision for compound-"
        "discovery imagery and chromatography analysis.",
        tags=("vision", "flagship"),
    ),
]

MULTILINGUAL: list[CroweBrand] = [
    CroweBrand(
        "crowelm-polyglot",
        "CroweLM Polyglot",
        "multilingual",
        "mistralai/mistral-medium-2505",
        "Mistral AI",
        ("text_chat", "multilingual"),
        "Polyglot routes to Mistral Medium with non-English priors.",
        tags=("multilingual", "alias"),
    ),
]

EMBEDDING: list[CroweBrand] = [
    CroweBrand(
        "crowelm-tendril",
        "CroweLM Tendril",
        "embedding",
        "ibm/granite-embedding-278m-multilingual",
        "IBM",
        ("embedding", "multilingual"),
        "Granite multilingual 278M embeddings. Default for the knowledge plane "
        "and substrate document store.",
        tags=("embedding", "default"),
    ),
    CroweBrand(
        "crowelm-filament",
        "CroweLM Filament",
        "embedding",
        "ibm/slate-125m-english-rtrvr-v2",
        "IBM",
        ("embedding", "rerank", "similarity"),
        "Slate 125M English retriever v2. English-only, higher recall.",
        tags=("embedding", "english"),
    ),
    CroweBrand(
        "crowelm-strand",
        "CroweLM Strand",
        "embedding",
        "ibm/slate-30m-english-rtrvr-v2",
        "IBM",
        ("embedding", "rerank", "similarity"),
        "Slate 30M English retriever v2. Edge-deployable embeddings.",
        tags=("embedding", "edge"),
    ),
    CroweBrand(
        "crowelm-capillary",
        "CroweLM Capillary",
        "embedding",
        "intfloat/multilingual-e5-large",
        "intfloat",
        ("embedding", "rerank", "similarity", "multilingual"),
        "Multilingual E5 large. Cross-lingual retrieval substrate.",
        tags=("embedding", "multilingual"),
    ),
    CroweBrand(
        "crowelm-mote",
        "CroweLM Mote",
        "embedding",
        "sentence-transformers/all-minilm-l6-v2",
        "sentence-transformers",
        ("embedding", "rerank", "similarity"),
        "MiniLM L6 v2. Tiny, fast embeddings for prototypes.",
        tags=("embedding", "tiny"),
    ),
]

RERANK: list[CroweBrand] = [
    CroweBrand(
        "crowelm-sieve",
        "CroweLM Sieve",
        "rerank",
        "cross-encoder/ms-marco-minilm-l-12-v2",
        "cross-encoder",
        ("rerank",),
        "MS-MARCO MiniLM L12 cross-encoder. Final-stage reranker.",
        tags=("rerank",),
    ),
]

TIME_SERIES: list[CroweBrand] = [
    CroweBrand(
        "crowelm-tempo",
        "CroweLM Tempo",
        "time_series",
        "ibm/granite-ttm-512-96-r2",
        "IBM",
        ("time_series_forecast",),
        "Granite TTM 512/96 r2. Short-horizon forecasts (yield, moisture, climate cycles).",
        tags=("time_series", "short_horizon"),
    ),
    CroweBrand(
        "crowelm-cadence",
        "CroweLM Cadence",
        "time_series",
        "ibm/granite-ttm-1024-96-r2",
        "IBM",
        ("time_series_forecast",),
        "Granite TTM 1024/96 r2. Mid-horizon batch and supply.",
        tags=("time_series", "mid_horizon"),
    ),
    CroweBrand(
        "crowelm-pulse",
        "CroweLM Pulse",
        "time_series",
        "ibm/granite-ttm-1536-96-r2",
        "IBM",
        ("time_series_forecast",),
        "Granite TTM 1536/96 r2. Long-context market and seasonality.",
        tags=("time_series", "long_horizon"),
    ),
]

SAFETY: list[CroweBrand] = [
    CroweBrand(
        "crowelm-warden",
        "CroweLM Warden",
        "safety",
        "ibm/granite-guardian-3-8b",
        "IBM",
        ("text_chat", "moderation"),
        "Granite Guardian 3.0 8B. Content + jailbreak moderation for the Crowe "
        "Logic chat surface.",
        tags=("safety", "moderation"),
    ),
    CroweBrand(
        "crowelm-aegis",
        "CroweLM Aegis",
        "safety",
        "meta-llama/llama-guard-3-11b-vision",
        "Meta",
        ("image_chat", "text_chat", "moderation"),
        "Llama Guard 3 11B Vision. Multimodal safety for image inputs.",
        tags=("safety", "vision"),
    ),
]


ALL_BRANDS: list[CroweBrand] = (
    REASONING
    + SYNAPSE
    + CODE
    + VISION
    + MULTILINGUAL
    + EMBEDDING
    + RERANK
    + TIME_SERIES
    + SAFETY
)

BY_BASE = {b.base_model: b for b in ALL_BRANDS if b.tier != "multilingual"}
BY_BRAND = {b.brand_id: b for b in ALL_BRANDS}

# multilingual is intentionally an alias of an existing base; do not enforce uniqueness there.
assert len(BY_BRAND) == len(ALL_BRANDS), "duplicate brand_id in brand registry"


def resolve(brand_or_base: str) -> Optional[CroweBrand]:
    """Look up a brand by its Crowe brand_id or by upstream model_id."""
    if brand_or_base in BY_BRAND:
        return BY_BRAND[brand_or_base]
    if brand_or_base in BY_BASE:
        return BY_BASE[brand_or_base]
    return None


def chat_brands() -> list[CroweBrand]:
    return [b for b in ALL_BRANDS if "text_chat" in b.capabilities and not b.deprecated]


def to_dict(brand: CroweBrand) -> dict:
    d = asdict(brand)
    d["capabilities"] = list(d["capabilities"])
    d["tags"] = list(d["tags"])
    return d


if __name__ == "__main__":
    import json

    out = {
        "version": "1.0.0",
        "count": len(ALL_BRANDS),
        "brands": [to_dict(b) for b in ALL_BRANDS],
    }
    print(json.dumps(out, indent=2))
