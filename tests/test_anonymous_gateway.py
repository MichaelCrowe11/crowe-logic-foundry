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
