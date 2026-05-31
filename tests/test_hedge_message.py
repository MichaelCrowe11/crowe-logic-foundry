"""The routing hedge banner must read as a calm, intentional reroute — stating
the target tier and a human-readable reason — never the alarming "failed".
"""

from cli.crowe_logic import _hedge_banner


def test_hedge_banner_states_calm_reason_and_target():
    msg = _hedge_banner(target_label="CroweLM Cinder", reason="timeout")
    assert "CroweLM Cinder" in msg
    assert "slow start" in msg.lower()  # calm phrasing for a timeout
    assert "failed" not in msg.lower()  # no alarming language for a routine hedge


def test_hedge_banner_passthrough_unknown_reason():
    msg = _hedge_banner(target_label="CroweLM Swift", reason="provider error")
    assert "CroweLM Swift" in msg
    assert "provider error" in msg.lower()
