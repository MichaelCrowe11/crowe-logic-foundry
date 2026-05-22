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


def test_chain_has_auto_router_and_core_tiers():
    """The chain must contain an Auto router plus the Supreme flagship.

    Positions drift as tiers get added/reshuffled; assert on contents,
    not on indices. If Auto or Supreme disappears, the routing layer
    is broken and the test should catch that.
    """
    labels = {m["label"] for m in MODEL_CHAIN}
    providers = {m["provider"] for m in MODEL_CHAIN}

    assert "CroweLM Auto" in labels
    assert "CroweLM Supreme" in labels
    # The chain must expose the full provider surface the CLI dispatches to.
    assert providers >= {"auto", "anthropic"}


def test_resolve_model_config_accepts_branded_aliases():
    """Core CroweLM aliases resolve by label and short alias.

    Tests the alias resolver, not the specific backends behind each tier
    (backends migrate frequently; aliases are more stable).
    """
    assert resolve_model_config("titan")["label"] == "CroweLM Helio"
    assert resolve_model_config("CroweLM Sovereign")["label"] == "CroweLM Sovereign"
    assert resolve_model_config("supreme")["label"] == "CroweLM Supreme"


def test_resolve_model_config_accepts_new_cloud_tier_aliases():
    """The Crescent/Eclipse cloud aliases resolve to their Ollama backends."""
    crescent = resolve_model_config("crescent")
    eclipse = resolve_model_config("eclipse")
    assert crescent is not None and crescent["label"] == "CroweLM Crescent"
    assert eclipse is not None and eclipse["label"] == "CroweLM Eclipse"
    assert crescent["provider"] == "ollama"
    assert eclipse["provider"] == "ollama"


def test_resolve_model_config_accepts_legacy_synapse_alias():
    """Legacy workstation settings still resolve after the Synapse rename."""
    synapse = resolve_model_config("CroweLM Synapse")
    assert synapse is not None
    assert synapse["label"] == "CroweLM Reason"


def test_build_system_instructions_includes_crowelm_tier_prompt():
    cfg = resolve_model_config("apex")
    instructions = build_system_instructions(cfg)

    assert "CroweLM Helio Pro" in instructions
    assert "Professional-grade Helio tier" in instructions


def test_provider_model_name_supports_env_var_interpolation(monkeypatch):
    """Backend names with ${ENV_VAR} syntax resolve at request time.

    This is what lets NemoClaw/Talon late-bind the model id discovered
    on the VM by scripts/nemoclaw_recon.sh instead of requiring a JSON
    edit per deployment.
    """
    monkeypatch.setenv("TEST_INTERP_MODEL", "resolved-at-runtime")
    cfg = {"backend_name": "${TEST_INTERP_MODEL}", "name": "fallback"}
    assert provider_model_name(cfg) == "resolved-at-runtime"


def test_provider_model_name_falls_back_to_name_when_backend_missing():
    """Legacy entries without backend_name still resolve via the name field."""
    cfg = {"name": "some-model-id"}
    assert provider_model_name(cfg) == "some-model-id"


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
    assert cfg["label"] == "CroweLM Hyphae Legacy"


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
    assert candidates[0]["label"] == "CroweLM Hyphae Legacy"
    assert all(cfg["provider"] != "auto" for cfg in candidates)


def test_route_candidates_for_auto_skips_blocked_providers():
    candidates, cls = route_candidates_for_auto(
        "hello",
        availability_check=lambda c: c.get("provider") != "watsonx",
    )

    assert cls == "chat"
    assert candidates
    assert candidates[0]["provider"] != "watsonx"
