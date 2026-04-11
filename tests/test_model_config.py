"""Tests for branded CroweLM model routing and prompt composition."""

from config.agent_config import MODEL_CHAIN, build_system_instructions, resolve_model_config


def test_primary_models_are_crowelm_pro_and_opus():
    assert MODEL_CHAIN[0]["name"] == "gpt-5.4-pro"
    assert MODEL_CHAIN[0]["label"] == "CroweLM Pro"
    assert MODEL_CHAIN[0]["surface"] == "responses"
    assert MODEL_CHAIN[1]["name"] == "claude-opus-4-6"
    assert MODEL_CHAIN[1]["label"] == "CroweLM Opus"


def test_resolve_model_config_accepts_branded_aliases():
    assert resolve_model_config("crowelm-pro")["name"] == "gpt-5.4-pro"
    assert resolve_model_config("CroweLM Opus")["name"] == "claude-opus-4-6"
    assert resolve_model_config("kernel")["name"] == "gpt-5.4-nano"


def test_build_system_instructions_includes_crowelm_tier_prompt():
    cfg = resolve_model_config("crowelm-pro")
    instructions = build_system_instructions(cfg)

    assert "CroweLM Pro" in instructions
    assert "first-party Crowe Logic infrastructure" in instructions
    assert "flagship reasoning tier" in instructions
