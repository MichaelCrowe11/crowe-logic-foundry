"""
Prompt loader: assemble per-variant system prompts from filesystem.

Each variant has a markdown file at `config/system_prompts/<slug>.md` with the
variant-specific identity. The shared base policy at `_base.md` is prepended
automatically.

Backward compatibility: if no file exists for a variant, falls back to the
inline `prompt` field on the model config (existing behavior). Logs a warning
the first time the fallback fires per variant per process.

Usage:
    from config.prompt_loader import system_prompt_for

    cfg = {"name": "kimi-k2.6:cloud", "label": "CroweLM Eclipse",
           "aliases": ["eclipse", "k26"], "prompt": "..."}
    prompt = system_prompt_for(cfg)
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable

_REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = _REPO_ROOT / "config" / "system_prompts"
BASE_FILE = PROMPTS_DIR / "_base.md"

_warned_variants: set[str] = set()


def slug_for(model_cfg: dict) -> str:
    """Return the canonical filesystem slug for a model config.

    Preference order:
        1. The first alias that does not contain provider prefixes.
        2. The label, lowercased and stripped of "CroweLM " prefix.
        3. The full backend name with non-alphanumerics replaced by '-'.
    """
    aliases: list[str] = model_cfg.get("aliases") or []
    for alias in aliases:
        if "/" not in alias and ":" not in alias:
            return alias

    label = model_cfg.get("label", "")
    if label.startswith("CroweLM "):
        return label[len("CroweLM ") :].lower().replace(" ", "_")
    if label:
        return label.lower().replace(" ", "_")

    name = model_cfg.get("name", "unknown")
    return "".join(ch if ch.isalnum() else "_" for ch in name).strip("_").lower()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def base_policy() -> str:
    """Return the shared base policy text, or empty string if missing."""
    return _read_text(BASE_FILE)


def variant_prompt_text(slug: str) -> str:
    """Return variant-specific prompt text, or empty string if missing."""
    candidate = PROMPTS_DIR / f"{slug}.md"
    return _read_text(candidate)


def system_prompt_for(model_cfg: dict) -> str:
    """Return the assembled system prompt for a model config.

    Format: base policy, then a separator, then variant-specific identity.
    Falls back to the inline `prompt` field if no variant file exists.
    """
    slug = slug_for(model_cfg)
    base = base_policy()
    variant = variant_prompt_text(slug)

    if not variant:
        # Fall back to inline `prompt` field for backward compatibility.
        inline = model_cfg.get("prompt") or ""
        if not inline:
            return base
        if slug not in _warned_variants:
            _warned_variants.add(slug)
            print(
                f"[prompt_loader] no file for variant '{slug}', "
                f"using inline prompt fallback. Add {PROMPTS_DIR}/{slug}.md "
                "to silence.",
                file=sys.stderr,
            )
        if base:
            return f"{base}\n\n---\n\n## Variant\n\n{inline}\n"
        return inline

    if base:
        return f"{base}\n\n---\n\n{variant}\n"
    return variant


def known_slugs() -> list[str]:
    """List all variant slugs with prompt files on disk."""
    if not PROMPTS_DIR.exists():
        return []
    return sorted(
        p.stem for p in PROMPTS_DIR.glob("*.md") if not p.stem.startswith("_")
    )


def slugs_for_chain(model_chain: Iterable[dict]) -> dict[str, str]:
    """Map each variant's primary identifier to its prompt slug.

    Useful for diagnostics and ensuring every model in MODEL_CHAIN has a
    corresponding prompt file.
    """
    result: dict[str, str] = {}
    for cfg in model_chain:
        identifier = cfg.get("label") or cfg.get("name") or "unknown"
        result[identifier] = slug_for(cfg)
    return result
