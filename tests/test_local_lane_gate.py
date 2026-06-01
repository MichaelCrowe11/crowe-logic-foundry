"""Local Ollama tiers are a Mike-only personal lane, excluded from customer
auto-routing unless CROWE_LOGIC_PERSONAL_LANE is set.

_model_switch_error is mocked to "reachable" (None) so each test isolates the
new provider gate rather than the environment-dependent reachability probe.
"""

import cli.crowe_logic as cl

_LOCAL = {
    "name": "crowelm-unified-v2",
    "label": "CroweLM Mycelium Local",
    "provider": "ollama",
}
_AZURE = {"name": "gpt-5.5", "label": "CroweLM Supreme", "provider": "azure_openai"}


def test_local_excluded_from_customer_routing(monkeypatch):
    monkeypatch.delenv("CROWE_LOGIC_PERSONAL_LANE", raising=False)
    monkeypatch.setattr(cl, "_model_switch_error", lambda cfg: None)
    assert cl._auto_route_available(_LOCAL) is False


def test_local_allowed_under_personal_flag(monkeypatch):
    monkeypatch.setenv("CROWE_LOGIC_PERSONAL_LANE", "1")
    monkeypatch.setattr(cl, "_model_switch_error", lambda cfg: None)
    assert cl._auto_route_available(_LOCAL) is True


def test_non_local_tier_unaffected_by_gate(monkeypatch):
    monkeypatch.delenv("CROWE_LOGIC_PERSONAL_LANE", raising=False)
    monkeypatch.setattr(cl, "_model_switch_error", lambda cfg: None)
    assert cl._auto_route_available(_AZURE) is True
