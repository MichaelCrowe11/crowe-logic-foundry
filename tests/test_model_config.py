"""Tests for branded CroweLM model routing and prompt composition."""

import importlib
import json
import os

import config.agent_config as agent_config
from config.agent_config import (
    MODEL_CHAIN,
    build_system_instructions,
    classify_task,
    provider_model_name,
    resolve_model_config,
    route_candidates_for_auto,
    route_for_auto,
)


def test_primary_models_are_crowelm_titan_and_sovereign():
    # Chain[0] is the Auto router; Titan is the first concrete tier.
    assert MODEL_CHAIN[0]["label"] == "CroweLM Auto"
    assert MODEL_CHAIN[0]["provider"] == "auto"
    assert MODEL_CHAIN[1]["name"] == "gpt-5.4"
    assert MODEL_CHAIN[1]["label"] == "CroweLM Titan"
    assert MODEL_CHAIN[1]["provider"] == "openai_compat"
    assert provider_model_name(MODEL_CHAIN[1]) == "z-ai/glm-5.1"
    assert MODEL_CHAIN[2]["name"] == "gpt-5.4-pro"
    assert MODEL_CHAIN[2]["label"] == "CroweLM Apex"
    assert MODEL_CHAIN[2]["provider"] == "openai_compat"
    assert provider_model_name(MODEL_CHAIN[2]) == "qwen/qwen3.5-397b-a17b"


def test_resolve_model_config_accepts_branded_aliases():
    assert resolve_model_config("titan")["name"] == "gpt-5.4"
    assert resolve_model_config("CroweLM Sovereign")["name"] == "claude-opus-4-6-2"
    assert resolve_model_config("nano")["name"] == "crowelm-nano"


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

    assert dense["provider"] == "nvidia"
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

    original_extra_models_path = os.environ.get("CROWE_LOGIC_EXTRA_MODELS_PATH")
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
        if original_extra_models_path is None:
            monkeypatch.delenv("CROWE_LOGIC_EXTRA_MODELS_PATH", raising=False)
        else:
            monkeypatch.setenv(
                "CROWE_LOGIC_EXTRA_MODELS_PATH",
                original_extra_models_path,
            )
        importlib.reload(agent_config)


def test_classify_task_handles_core_classes():
    assert classify_task("take a screenshot of the shopify editor") == "agentic"
    assert classify_task("click the Buy Now button") == "agentic"
    assert classify_task("write a python function to parse csv") == "code"
    assert classify_task("refactor this class") == "code"
    assert classify_task("write a poem about mushrooms") == "creative"
    assert classify_task("what is the best substrate for lion's mane cultivation") == "domain_qa"
    assert classify_task("summarize recent research on mycelium networks") == "research"
    assert classify_task("hello") == "chat"
    assert classify_task("hi how are you?") == "chat"
    assert classify_task("thanks") == "chat"
    assert classify_task("") == "default"


def test_classify_task_no_false_chat_on_substring():
    # 'nice' must not match inside 'venice'; 'hi' must not match inside 'historian'.
    assert classify_task("tell me about venice") != "chat"
    assert classify_task("who is a notable historian") != "chat"


def test_route_for_auto_selects_correct_tier():
    cfg, cls = route_for_auto("take a screenshot of the shopify editor")
    assert cls == "agentic"
    assert cfg["label"] == "CroweLM Maverick"

    cfg, cls = route_for_auto("write a python function to parse csv")
    assert cls == "code"
    assert cfg["label"] == "CroweLM Coder"

    cfg, cls = route_for_auto("hello")
    assert cls == "chat"
    assert cfg["label"] == "CroweLM Nexus"


def test_route_for_auto_never_returns_auto():
    cfg, _ = route_for_auto("any message")
    assert cfg["provider"] != "auto"


def test_route_for_auto_falls_back_when_primary_unavailable():
    # Block the primary agentic route; router must fall back to the next tier.
    blocked = {"CroweLM Maverick"}
    cfg, cls = route_for_auto(
        "take a screenshot of the page",
        availability_check=lambda c: c["label"] not in blocked,
    )
    assert cls == "agentic"
    assert cfg["label"] != "CroweLM Maverick"
    assert cfg["provider"] != "auto"


def test_route_candidates_for_auto_returns_same_turn_fallbacks():
    candidates, cls = route_candidates_for_auto("hello")

    assert cls == "chat"
    assert candidates
    assert candidates[0]["label"] == "CroweLM Nexus"
    assert all(cfg["provider"] != "auto" for cfg in candidates)


def test_route_candidates_for_auto_skips_blocked_providers():
    candidates, cls = route_candidates_for_auto(
        "hello",
        availability_check=lambda c: c.get("provider") != "watsonx",
    )

    assert cls == "chat"
    assert candidates
    assert candidates[0]["provider"] != "watsonx"
