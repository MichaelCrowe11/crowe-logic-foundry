"""Tests for config.prompt_loader."""
from __future__ import annotations

from pathlib import Path


from config import prompt_loader
from config.prompt_loader import (
    base_policy,
    known_slugs,
    slug_for,
    slugs_for_chain,
    system_prompt_for,
)


def test_slug_prefers_clean_alias() -> None:
    cfg = {
        "name": "kimi-k2.6:cloud",
        "label": "CroweLM Eclipse",
        "aliases": ["eclipse", "crowelm-eclipse", "k26"],
    }
    assert slug_for(cfg) == "eclipse"


def test_slug_prefers_concrete_deployment_prompt_over_legacy_alias(
    tmp_path: Path, monkeypatch
) -> None:
    prompts_dir = tmp_path / "system_prompts"
    prompts_dir.mkdir()
    (prompts_dir / "apex.md").write_text("legacy apex")
    (prompts_dir / "crowelm_gpt_5_4_pro.md").write_text("synced helio pro")

    monkeypatch.setattr(prompt_loader, "PROMPTS_DIR", prompts_dir)

    cfg = {
        "name": "gpt-5.4-pro",
        "label": "CroweLM Helio Pro",
        "aliases": ["apex", "CroweLM GPT 5.4 Pro", "gpt-5.4-pro"],
    }
    assert slug_for(cfg) == "crowelm_gpt_5_4_pro"


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


def test_live_smoke_variants_have_prompt_files_in_real_repo() -> None:
    """Live CLI smoke paths should not fall back to inline prompt warnings."""
    slugs = known_slugs()
    assert "kernel" in slugs
    assert "nexus" in slugs


def test_slugs_for_chain_produces_unique_mapping() -> None:
    chain = [
        {"name": "a:cloud", "label": "CroweLM A", "aliases": ["a"]},
        {"name": "b:cloud", "label": "CroweLM B", "aliases": ["b"]},
    ]
    out = slugs_for_chain(chain)
    assert out == {"CroweLM A": "a", "CroweLM B": "b"}
