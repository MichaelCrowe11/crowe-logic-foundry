"""Phase 3: the `free` signed-in plan tier."""

from control_plane import plans
from control_plane import oidc


def test_free_plan_ranks_below_personal_above_anon():
    assert plans.plan_rank("free") == 0
    assert plans.plan_rank("free") < plans.plan_rank("personal")
    assert plans.plan_rank("free") > plans.plan_rank(plans.ANON_PLAN_ID)


def test_free_plan_is_canonical_passthrough():
    # `free` must not be aliased away to another plan id.
    assert plans.canonical_plan_id("free") == "free"


def test_free_plan_has_display_name():
    assert plans.display_plan_name("free") == "Free"


def test_tier_to_plan_unknown_and_free_resolve_to_free():
    # No subscription / unknown tier -> least privilege = the free plan.
    assert oidc.tier_to_plan(None) == "free"
    assert oidc.tier_to_plan("") == "free"
    assert oidc.tier_to_plan("free") == "free"
    assert oidc.tier_to_plan("totally-unknown") == "free"


def test_tier_to_plan_preserves_paid_tiers():
    # Genuine paid tiers are untouched (no downgrade).
    assert oidc.tier_to_plan("personal") == "personal"
    assert oidc.tier_to_plan("pro") == "pro"
    assert oidc.tier_to_plan("studio") == "team"
    assert oidc.tier_to_plan("enterprise") == "enterprise"
