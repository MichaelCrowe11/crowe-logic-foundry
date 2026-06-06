"""Anonymous plan semantics + register endpoint + gateway principal/cap."""
import pytest

from control_plane import plans


def test_anon_plan_ranks_below_everything():
    assert plans.plan_rank(plans.ANON_PLAN_ID) == -1
    assert plans.plan_rank("byok") > plans.plan_rank(plans.ANON_PLAN_ID)


def test_anon_plan_not_in_launch_plans():
    # Stripe/pricing surfaces iterate LAUNCH_PLAN_IDS; anon must stay out.
    assert plans.ANON_PLAN_ID not in plans.LAUNCH_PLAN_IDS


def test_anon_cap_constant():
    assert plans.ANON_DAILY_TURN_CAP == 20


def test_mycelium_resolves_and_is_anon_accessible():
    from config.agent_config import resolve_model_config
    from control_plane.gateway import MODEL_PLAN_ACCESS

    cfg = resolve_model_config("crowelm-mycelium")
    assert cfg is not None
    assert cfg["api_key_env"] == "CROWELM_MYCELIUM_API_KEY"
    assert MODEL_PLAN_ACCESS["crowelm-mycelium"] == plans.ANON_PLAN_ID


def test_register_mints_token(monkeypatch):
    monkeypatch.setenv("CROWE_ANON_SIGNING_SECRET", "test-secret")
    from control_plane import anonymous, tokens
    import asyncio

    class FakeClient:
        host = "203.0.113.7"

    class FakeRequest:
        client = FakeClient()

    anonymous._register_log.clear()
    out = asyncio.run(anonymous.register_device(FakeRequest()))
    assert tokens.verify_device_token(out["token"]) == out["device_id"]
    assert out["free_model"] == "crowelm-mycelium"
    assert out["daily_turn_cap"] == plans.ANON_DAILY_TURN_CAP


def test_register_rate_limits_per_ip(monkeypatch):
    monkeypatch.setenv("CROWE_ANON_SIGNING_SECRET", "test-secret")
    from fastapi import HTTPException
    from control_plane import anonymous
    import asyncio

    class FakeClient:
        host = "203.0.113.8"

    class FakeRequest:
        client = FakeClient()

    anonymous._register_log.clear()
    for _ in range(anonymous._REGISTER_MAX_PER_IP):
        asyncio.run(anonymous.register_device(FakeRequest()))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(anonymous.register_device(FakeRequest()))
    assert exc.value.status_code == 429
