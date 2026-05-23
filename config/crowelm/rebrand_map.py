# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
"""
Azure deployment rebrand map — single source of truth.

When `crowe-logic models sync` pulls deployments from Azure, the raw
deployment names ("gpt-5.4", "Kimi-K2-6", "DeepSeek-R1-0528") would
otherwise be rendered verbatim in the deploy table, leaking the upstream
stack. This module owns the rename: deployment name -> Crowe Logic
display label.

REBRAND_MAP keys are case-insensitive in lookups; the canonical key is
the exact `name` field as it appears in the synced registry. New Azure
deployments should be added here at the same time their `name` is added
to `models.extra.json` (or model_sync.py picks them up automatically).

Leak detection (`is_leaky_label`) enforces the doctrine: no upstream
provider/model token in a customer-visible label.

Related:
  - config/model_sync.py uses `display_label(name, fallback)`
  - cli/crowe_logic.py:doctor() scans for unrebranded entries
  - .claude memory: project-crowe-logic-model-rebrand
"""

from __future__ import annotations


REBRAND_MAP: dict[str, str] = {
    # OpenAI family
    "gpt-4o": "CroweLM Beacon",
    "gpt-5.4": "CroweLM Helio",
    "gpt-5.4-mini": "CroweLM Helio Mini",
    "gpt-5.4-nano": "CroweLM Cinder",
    "gpt-5.4-pro": "CroweLM Helio Pro",
    "gpt-5.5": "CroweLM Quasar",
    "gpt-chat-latest": "CroweLM Chat",
    # xAI Grok family
    "grok-4-1-fast-non-r": "CroweLM Swift Raw",
    "grok-4-1-fast-reasoning": "CroweLM Swift Reason",
    "grok-4-20-reasoning": "CroweLM Spire",
    "grok-4-3": "CroweLM Crest",
    # Moonshot Kimi family
    "Kimi-K2-6": "CroweLM Hyphae",
    "Kimi-K2.5": "CroweLM Hyphae Legacy",
    "kimi-k2.6:cloud": "CroweLM Hyphae Nexus",  # Ollama Cloud route via Nexus edge node
    # Meta Llama family
    "Llama-3-3-70B": "CroweLM Bastion",
    "Llama-4-Maverick": "CroweLM Maverick Raw",
    "Llama-4-Scout": "CroweLM Scout",
    # Mistral / Codestral
    "Codestral-2501": "CroweLM Anvil",
    # Cohere family
    "Cohere-Command-A": "CroweLM Lattice",
    "Cohere-embed-v4": "CroweLM Filament Pro",
    "Cohere-rerank-v4-fast": "CroweLM Sift Fast",
    "Cohere-rerank-v4-pro": "CroweLM Sift Pro",
    # DeepSeek family
    "DeepSeek-R1-0528": "CroweLM Cipher",
    "DeepSeek-V3-1": "CroweLM Cipher Legacy",
    "DeepSeek-V4-Flash": "CroweLM Flash",
    # Sora video
    "sora-2": "CroweLM Reel",
    # OpenAI embeddings
    "text-embedding-3-large": "CroweLM Embed Large",
}


# Provider tokens that must never appear in a display label.
# Includes lowercase substrings checked against the lowered label.
_LEAKY_TOKENS: tuple[str, ...] = (
    "gpt",
    "grok",
    "llama",
    "kimi",
    "deepseek",
    "cohere",
    "codestral",
    "sora",
    "mistral",
    "qwen",
    "claude",
    "anthropic",
    "command-a",
    "command-r",
    "embed-v",
    "rerank-v",
)


# Tokens we intentionally allow — open-source hackathon release or
# explicit branded mention. Override per-deployment if needed.
_LEAK_ALLOWLIST: frozenset[str] = frozenset(
    {
        "Gemma 4 Mycelium",
    }
)


def is_leaky_label(label: str) -> bool:
    """Return True if `label` contains an upstream provider/model token.

    Allowlisted labels (open-source releases we credit by name) return False.
    """
    if not label:
        return False
    if label in _LEAK_ALLOWLIST:
        return False
    lowered = label.lower()
    return any(tok in lowered for tok in _LEAKY_TOKENS)


def display_label(name: str, fallback: str) -> str:
    """Return the canonical Crowe Logic display label for a deployment.

    Lookup is case-insensitive on `name` against REBRAND_MAP. If no mapping
    exists, `fallback` is returned (typically the mechanically generated
    label from model_sync.label_for). Callers should still pass the result
    through `is_leaky_label` and surface a warning if True.
    """
    # Exact match first (preserves the canonical case in the map).
    if name in REBRAND_MAP:
        return REBRAND_MAP[name]
    # Case-insensitive fallback.
    lowered = name.lower()
    for key, label in REBRAND_MAP.items():
        if key.lower() == lowered:
            return label
    return fallback


def unmapped_leaky_names(registry_entries: list[dict]) -> list[tuple[str, str]]:
    """Scan a synced registry and return (name, label) tuples that leak.

    Used by `crowe-logic doctor` to enforce the no-leak doctrine and to
    surface candidates that should be added to REBRAND_MAP.
    """
    leaks: list[tuple[str, str]] = []
    for entry in registry_entries:
        name = str(entry.get("name", "")).strip()
        label = str(entry.get("label", "")).strip()
        if name and label and is_leaky_label(label):
            leaks.append((name, label))
    return leaks


# Sanity check: every value in REBRAND_MAP must itself pass the leak filter,
# minus the explicit allowlist. Raises at import time on regression.
def _self_check() -> None:
    for name, label in REBRAND_MAP.items():
        if is_leaky_label(label):
            raise AssertionError(
                f"REBRAND_MAP[{name!r}] = {label!r} contains a leaky token. "
                "Rename the label or add it to _LEAK_ALLOWLIST."
            )
    # Also: every label should start with 'CroweLM ' for consistency.
    for name, label in REBRAND_MAP.items():
        if not label.startswith("CroweLM ") and label not in _LEAK_ALLOWLIST:
            raise AssertionError(
                f"REBRAND_MAP[{name!r}] = {label!r} must start with 'CroweLM '."
            )


_self_check()


__all__ = [
    "REBRAND_MAP",
    "display_label",
    "is_leaky_label",
    "unmapped_leaky_names",
]
