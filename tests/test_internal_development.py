from __future__ import annotations

import json

from click.testing import CliRunner

from cli import crowe_logic as cli_mod
from crowe_synapse_engine.internal_development import (
    InternalAccessPolicy,
    build_claude_agent_payloads,
    build_internal_development_plan,
    recommended_internal_agents,
)


def test_recommended_internal_agent_roster_is_nine_distinct_roles():
    agents = recommended_internal_agents()

    assert len(agents) == 9
    assert len({agent.agent_id for agent in agents}) == 9
    assert "internal-architecture-steward" in {agent.agent_id for agent in agents}
    assert "internal-security-entitlements" in {agent.agent_id for agent in agents}
    assert all(agent.system_prompt().startswith("You are CL Internal") for agent in agents)


def test_access_policy_allows_owner_and_approved_staff_only():
    policy = InternalAccessPolicy(
        scope="owner_and_approved_staff_only",
        workspace="Crowe Logic Internal Development",
        owner_principals=("owner@crowelogic.com",),
        approved_staff_principals=("staff@crowelogic.com",),
    )

    assert policy.allows("owner@crowelogic.com")
    assert policy.allows("STAFF@crowelogic.com")
    assert not policy.allows("customer@example.com")
    assert not policy.allows("")


def test_claude_payloads_are_internal_and_permission_gated():
    policy = InternalAccessPolicy(
        scope="owner_and_approved_staff_only",
        workspace="Crowe Logic Internal Development",
        owner_principals=("owner@crowelogic.com",),
    )

    payloads = build_claude_agent_payloads(recommended_internal_agents(), policy)

    assert len(payloads) == 9
    for payload in payloads:
        assert payload["metadata"]["crowe_internal"] == "true"
        assert payload["metadata"]["access_scope"] == "owner_and_approved_staff_only"
        toolset = payload["tools"][0]
        assert toolset["default_config"]["permission_policy"]["type"] == "always_ask"
        gated = {item["name"]: item for item in toolset["configs"]}
        assert gated["bash"]["permission_policy"]["type"] == "always_ask"
        assert gated["write"]["permission_policy"]["type"] == "always_ask"


def test_internal_development_plan_includes_coordinator_and_self_heal_loops():
    policy = InternalAccessPolicy(
        scope="owner_and_approved_staff_only",
        workspace="Crowe Logic Internal Development",
        owner_principals=("owner@crowelogic.com",),
    )

    plan = build_internal_development_plan(access_policy=policy)

    assert plan.recommended_count == 9
    assert len(plan.self_heal_loops) >= 4
    assert "CL Internal Development Coordinator" == plan.coordinator_payload["name"]
    assert len(plan.coordinator_payload["multiagent"]["agents"]) == 9
    assert any("Claude Console workspace" in step for step in plan.deployment_steps)


def test_internal_agents_cli_json(monkeypatch):
    monkeypatch.setenv("CROWE_LOGIC_OWNER_PRINCIPALS", "owner@crowelogic.com")
    runner = CliRunner()

    result = runner.invoke(cli_mod.main, ["internal", "agents", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["recommended_count"] == 9
    assert payload["access_policy"]["owner_principals"] == ["owner@crowelogic.com"]
    assert payload["agents"][0]["agent_id"].startswith("internal-")


def test_internal_plan_cli_shows_safe_deployment_boundary(monkeypatch):
    monkeypatch.setenv("CROWE_LOGIC_OWNER_PRINCIPALS", "owner@crowelogic.com")
    runner = CliRunner()

    result = runner.invoke(cli_mod.main, ["internal", "plan"])

    assert result.exit_code == 0
    assert "Claude Console Deployment Plan" in result.output
    assert "does not create external" in result.output
    assert "agents or change Console membership" in result.output
