"""
CroweLM Unified Dataset Loader.

Loads curated domain knowledge from the CroweLM Unified Dataset and injects
it into the system prompt for dataset-augmented tiers (e.g. CroweLM Supreme).

The loader reads from curated_export.jsonl (high-quality hand-picked examples)
and the DATASET_MANIFEST.json (domain statistics). This gives CroweLM Supreme
deep domain grounding without requiring fine-tuning on the full 145K sample set.
"""

import json
import os
from functools import lru_cache
from typing import Optional

_PACKAGE_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_PROJECT_ROOT = os.environ.get("CROWE_LOGIC_PROJECT_ROOT", _PACKAGE_ROOT)
_DATASET_DIR = os.environ.get(
    "CROWELM_UNIFIED_DATASET_DIR",
    os.path.join(_PROJECT_ROOT, "data", "crowelm-unified"),
)


@lru_cache(maxsize=1)
def _load_curated_examples() -> list[dict]:
    """Load curated Q&A pairs from the export file."""
    path = os.path.join(_DATASET_DIR, "curated_export.jsonl")
    if not os.path.exists(path):
        return []
    examples = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                examples.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return examples


@lru_cache(maxsize=1)
def _load_manifest() -> dict:
    """Load the dataset manifest for domain statistics."""
    path = os.path.join(_DATASET_DIR, "DATASET_MANIFEST.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_dataset_context(max_examples: int = 9, max_chars: int = 30000) -> str:
    """Build a condensed knowledge block from the curated dataset.

    Returns a string suitable for appending to a system prompt. Includes:
      - Domain expertise summary from the manifest
      - Top curated Q&A pairs (truncated to fit context budget)
    """
    manifest = _load_manifest()
    examples = _load_curated_examples()

    parts = []

    # Domain statistics header
    summary = manifest.get("summary", {})
    top_domains = manifest.get("top_domains", {})
    if summary:
        parts.append(
            "\n\n--- CroweLM Unified Knowledge Base ---\n"
            f"Training corpus: {summary.get('crowelm_training_entries', 'N/A')} samples, "
            f"{summary.get('total_size_gb', 'N/A')} GB\n"
            f"Domains: {summary.get('domains', 'N/A')}\n"
        )
    if top_domains:
        top_5 = list(top_domains.items())[:5]
        domain_str = ", ".join(f"{k} ({v:,} samples)" for k, v in top_5)
        parts.append(f"Top knowledge areas: {domain_str}\n")

    # Curated examples as grounding context
    if examples:
        parts.append("\n--- Domain Expertise (Curated Reference Examples) ---\n")
        total_chars = sum(len(p) for p in parts)
        included = 0
        for ex in examples[:max_examples]:
            instruction = ex.get("instruction", "")
            response = ex.get("response", "")
            # Truncate long responses to stay within budget
            if len(response) > 2000:
                response = response[:2000] + "..."
            block = f"\nQ: {instruction}\nA: {response}\n"
            if total_chars + len(block) > max_chars:
                break
            parts.append(block)
            total_chars += len(block)
            included += 1
        parts.append(f"\n[{included} of {len(examples)} curated examples loaded]\n")

    return "".join(parts)


def augment_system_prompt(base_prompt: str, model_cfg: Optional[dict] = None) -> str:
    """Mount the CroweLM dataset knowledge block on top of a tier's system prompt.

    Applied to EVERY tier by default ("dataset on top of each model"). Opt a
    single tier out with ``dataset_augmented=False`` in its config, or disable
    the mount globally with ``CROWELM_DATASET_MOUNT=0``.
    """
    if os.environ.get("CROWELM_DATASET_MOUNT", "1").strip().lower() in (
        "0",
        "false",
        "no",
    ):
        return base_prompt
    if model_cfg is not None and model_cfg.get("dataset_augmented") is False:
        return base_prompt

    context = build_dataset_context()
    if not context:
        return base_prompt

    return base_prompt + context
