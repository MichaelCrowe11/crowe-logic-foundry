"""Tests for config.prompt_loader."""
from __future__ import annotations

from pathlib import Path

import pytest

from config import prompt_loader
from config.prompt_loader import (
    base_policy,
    known_slugs,
    slug_for,
    slugs_for_chain,
    system_prompt_for,
    variant_prompt_text,
)


def test_slug_prefers_clean_alias() -> None:
    cfg = {
        "name": "kimi-k2.6:cloud",
        "label": "CroweLM Eclipse",
        "aliases": ["eclipse", "crowelm-eclipse", "k26"],
    }
    assert slug_for(cfg) == "eclipse"


def test_slug_skips_aliases_with_prefixes() -> None:
    cfg = {
        "name": "moonshotai/kimi-k2.5",
        "label": "CroweLM Lunar",
        "aliases": ["moonshot/kimi", "k25:cloud", "lunar"],
    }
    assert slug_for(cfg) == "lunar"


def test_slug_falls_back_to_label() -> None:
    cfg = {"name": "thudm/glm-4.6", "label": "CroweLM LocalMesh", "aliases": []}
    assert slug_for(cfg) == "localmesh"


def test_slug_falls_back_to_name_when_no_label() -> None:
    cfg = {"name": "experimental/foo-model", "aliases": []}
    assert slug_for(cfg) == "experimental_foo_model"


def test_base_policy_is_loaded_and_includes_critical_rules() -> None:
    base = base_policy()
    assert base, "base policy file must exist"
    # Critical rules must be present, paraphrasing is fine but the keywords must be there.
    assert "em-dash" in base.lower() or "em dash" in base.lower()
    assert "secret" in base.lower() or "credential" in base.lower()
    assert "scope" in base.lower()
    assert "verification" in base.lower() or "verify" in base.lower()


def test_system_prompt_concatenates_base_and_variant(tmp_path: Path, monkeypatch) -> None:
    base = "## Base Policy\nAlways be polite."
    variant = "## Variant\nYou are CroweLM Test, a test variant."
    prompts_dir = tmp_path / "system_prompts"
    prompts_dir.mkdir()
    (prompts_dir / "_base.md").write_text(base)
    (prompts_dir / "test.md").write_text(variant)

    monkeypatch.setattr(prompt_loader, "PROMPTS_DIR", prompts_dir)
    monkeypatch.setattr(prompt_loader, "BASE_FILE", prompts_dir / "_base.md")

    cfg = {"name": "test:cloud", "label": "CroweLM Test", "aliases": ["test"]}
    out = system_prompt_for(cfg)
    assert "Always be polite" in out
    assert "test variant" in out
    # Base must come first.
    assert out.index("Always be polite") < out.index("test variant")


def test_system_prompt_falls_back_to_inline_when_file_missing(
    tmp_path: Path, monkeypatch
) -> None:
    prompts_dir = tmp_path / "system_prompts"
    prompts_dir.mkdir()
    (prompts_dir / "_base.md").write_text("## Base\nbe direct.")

    monkeypatch.setattr(prompt_loader, "PROMPTS_DIR", prompts_dir)
    monkeypatch.setattr(prompt_loader, "BASE_FILE", prompts_dir / "_base.md")
    # Reset warned set so the warning fires once for this test variant.
    monkeypatch.setattr(prompt_loader, "_warned_variants", set())

    cfg = {
        "name": "missing:cloud",
        "label": "CroweLM Missing",
        "aliases": ["missing"],
        "prompt": "You are CroweLM Missing, a placeholder.",
    }
    out = system_prompt_for(cfg)
    assert "be direct" in out
    assert "placeholder" in out


def test_system_prompt_with_no_inline_and_no_file_returns_base(
    tmp_path: Path, monkeypatch
) -> None:
    prompts_dir = tmp_path / "system_prompts"
    prompts_dir.mkdir()
    (prompts_dir / "_base.md").write_text("just the base")

    monkeypatch.setattr(prompt_loader, "PROMPTS_DIR", prompts_dir)
    monkeypatch.setattr(prompt_loader, "BASE_FILE", prompts_dir / "_base.md")

    cfg = {"name": "x:cloud", "label": "CroweLM X", "aliases": ["x"]}
    out = system_prompt_for(cfg)
    assert out == "just the base"


def test_known_slugs_excludes_underscore_prefixed() -> None:
    slugs = known_slugs()
    assert "_base" not in slugs


def test_eclipse_has_a_prompt_file_in_real_repo() -> None:
    """The variant called out in the 2026-04-30 incident must have a file."""
    slugs = known_slugs()
    assert "eclipse" in slugs, (
        "eclipse.md must exist; this is the variant from the failure transcript"
    )


def test_slugs_for_chain_produces_unique_mapping() -> None:
    chain = [
        {"name": "a:cloud", "label": "CroweLM A", "aliases": ["a"]},
        {"name": "b:cloud", "label": "CroweLM B", "aliases": ["b"]},
    ]
    out = slugs_for_chain(chain)
    assert out == {"CroweLM A": "a", "CroweLM B": "b"}


def test_inline_prompts_use_current_label_not_legacy_codename() -> None:
    """Every model's inline `prompt` must self-identify with its own current label.

    Catches the PR #24 rebrand drift where labels were renamed but the
    inline 'You are CroweLM X' strings still referenced the prior codename
    (e.g. label='CroweLM Helio' but prompt='You are CroweLM Titan, ...').
    Self-maintaining: derives the expected name from each cfg, no hardcoded
    legacy list.
    """
    import re
    from config.agent_config import MODEL_CHAIN

    drift: list[str] = []
    pattern = re.compile(r"You are \*{0,2}(CroweLM [A-Z][\w\s]*?)\*{0,2}[,.]")
    for cfg in MODEL_CHAIN:
        prompt = (cfg.get("prompt") or "").strip()
        if not prompt:
            continue
        match = pattern.search(prompt)
        if not match:
            continue
        introduced = match.group(1).strip()
        expected = cfg["label"].strip()
        if introduced != expected:
            drift.append(f"{expected!r} introduces itself as {introduced!r}")

    assert not drift, (
        "Inline prompt self-identification drift detected.\n"
        + "\n".join(f"  - {item}" for item in drift)
    )


def test_stub_prompt_files_use_current_label_not_legacy_codename() -> None:
    """Stub files on disk must self-identify with the live model's label.

    Mirror of the inline check for the config/system_prompts/*.md files,
    since prompt_loader.py prefers file content over inline `prompt` when
    a file exists. A file with the wrong "You are CroweLM X" body would
    silently re-leak the legacy codename even though inline is correct.
    """
    import re
    from config.agent_config import MODEL_CHAIN

    chain_by_slug = {slug_for(c): c for c in MODEL_CHAIN}
    pattern = re.compile(r"You are \*{0,2}(CroweLM [A-Z][\w\s]*?)\*{0,2}[,.]")

    drift: list[str] = []
    for slug in known_slugs():
        cfg = chain_by_slug.get(slug)
        if not cfg:
            continue
        text = variant_prompt_text(slug)
        if not text:
            continue
        match = pattern.search(text)
        if not match:
            continue
        introduced = match.group(1).strip()
        expected = cfg["label"].strip()
        if introduced != expected:
            drift.append(f"system_prompts/{slug}.md introduces itself as {introduced!r}, expected {expected!r}")

    assert not drift, (
        "Stub prompt-file self-identification drift detected.\n"
        + "\n".join(f"  - {item}" for item in drift)
    )
