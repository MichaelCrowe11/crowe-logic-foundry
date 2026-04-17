"""Tests for branded CroweLM model routing and prompt composition."""

import importlib
import json

import config.agent_config as agent_config
from config.agent_config import MODEL_CHAIN, build_system_instructions, provider_model_name, resolve_model_config


def test_primary_models_are_crowelm_titan_and_sovereign():
    assert MODEL_CHAIN[0]["name"] == "gpt-5.4"
    assert MODEL_CHAIN[0]["label"] == "CroweLM Titan"
    assert MODEL_CHAIN[0]["provider"] == "openai_compat"
    assert provider_model_name(MODEL_CHAIN[0]) == "z-ai/glm-5.1"
    assert MODEL_CHAIN[1]["name"] == "gpt-5.4-pro"
    assert MODEL_CHAIN[1]["label"] == "CroweLM Apex"
    assert MODEL_CHAIN[1]["provider"] == "openai_compat"
    assert provider_model_name(MODEL_CHAIN[1]) == "qwen/qwen3.5-397b-a17b"


def test_resolve_model_config_accepts_branded_aliases():
    assert resolve_model_config("titan")["name"] == "gpt-5.4"
    assert resolve_model_config("CroweLM Sovereign")["name"] == "claude-opus-4-6-2"
    assert resolve_model_config("nano")["name"] == "gpt-5.4-nano"


def test_resolve_model_config_accepts_legacy_aliases():
    assert resolve_model_config("crowelm-pro")["name"] == "gpt-5.4-pro"
    # crowelm-glm resolves to the upgraded GLM 5.1 deployment
    assert resolve_model_config("crowelm-glm")["name"] == "FW-GLM-5.1"
    # The legacy GLM 5 entry is still accessible via its own alias
    assert resolve_model_config("glm5")["name"] == "FW-GLM-5"


def test_build_system_instructions_includes_crowelm_tier_prompt():
    cfg = resolve_model_config("apex")
    instructions = build_system_instructions(cfg)

    assert "CroweLM Apex" in instructions
    assert "peak-performance reasoning tier" in instructions


def test_open_source_first_tiers_keep_stable_public_ids_with_backend_mapping():
    dense = resolve_model_config("crowelm-glm")
    sovereign = resolve_model_config("CroweLM Sovereign")

    assert dense["provider"] == "openai_compat"
    assert provider_model_name(dense) == "z-ai/glm-5.1"
    assert sovereign["provider"] == "openai_compat"
    assert provider_model_name(sovereign) == "deepseek/deepseek-v3.2"


def test_model_chain_loads_extra_models_from_json_file(tmp_path, monkeypatch):
    extra_path = tmp_path / "models.extra.json"
    extra_path.write_text(json.dumps({
        "models": [
            {
                "name": "gpt-4.1-mini",
                "label": "CroweLM Scout",
                "aliases": ["scout"],
            }
        ]
    }))

    monkeypatch.setenv("CROWE_LOGIC_EXTRA_MODELS_PATH", str(extra_path))
    reloaded = importlib.reload(agent_config)
    try:
        cfg = reloaded.resolve_model_config("scout")
        assert cfg is not None
        assert cfg["name"] == "gpt-4.1-mini"
        assert cfg["provider"] == "azure_openai"
        assert cfg["endpoint_env"] == "AZURE_CORE_ENDPOINT"
        assert cfg["api_key_env"] == "AZURE_CORE_API_KEY"
    finally:
        monkeypatch.delenv("CROWE_LOGIC_EXTRA_MODELS_PATH", raising=False)
        importlib.reload(agent_config)
