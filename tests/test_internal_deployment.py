from __future__ import annotations

import json

import yaml
from click.testing import CliRunner

from cli import crowe_logic as cli_mod
from crowe_synapse_engine.agent_registry import AgentRegistry
from crowe_synapse_engine.internal_deployment import (
    ANTHROPIC_BETA_HEADER,
    SAFE_SOVEREIGN_TOOLS,
    SOVEREIGN_MODEL,
    apply_sovereign,
    build_actions,
    emit_browser_script,
    execute_managed_agents,
    render_plan,
)
from crowe_synapse_engine.internal_development import build_internal_development_plan


def _plan():
    return build_internal_development_plan()


class _FakeTransport:
    """Records requests and returns deterministic agent ids (no network)."""

    def __init__(self):
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append(
            {"method": method, "url": url, "headers": headers, "body": body}
        )
        slug = body["name"].replace(" ", "-").lower()
        return {"id": f"agt_{slug}", "version": 1}


def test_sovereign_actions_are_valid_registry_yaml():
    actions = build_actions(_plan(), "sovereign")

    # one file per agent (coordinator is an Anthropic/AWS concept, not a sovereign persona)
    assert len(actions) == 9
    for action in actions:
        assert action.backend == "sovereign"
        assert action.kind == "file_write"
        assert action.target.endswith(".yaml")
        assert "agents/internal/" in action.target.replace("\\", "/")
        data = yaml.safe_load(action.payload["content"])
        assert data["name"].startswith("internal-")
        assert data["model"] == SOVEREIGN_MODEL  # branded, sovereign-routed
        assert data["prompt_override"].startswith("You are CL Internal")
        # internal agents start read-only; write/shell/commit are owner-gated enables
        assert set(data["tools"]).issubset(SAFE_SOVEREIGN_TOOLS)


def test_sovereign_apply_writes_gated_subdir_not_loaded_by_flat_registry(tmp_path):
    agents_dir = tmp_path / "agents"
    (agents_dir / "internal").mkdir(parents=True)

    actions = build_actions(_plan(), "sovereign", agents_dir=str(agents_dir))
    written = apply_sovereign(actions)

    assert len(written) == 9

    # An owner-gated registry pointed at the internal subdir sees all 9.
    internal_reg = AgentRegistry(str(agents_dir / "internal"))
    assert len(internal_reg.list_agents()) == 9

    # The default flat registry over agents/ must NOT pick them up (no leak).
    public_reg = AgentRegistry(str(agents_dir))
    assert len(public_reg.list_agents()) == 0


def test_anthropic_actions_hit_create_endpoint_with_beta_header_and_redacted_key():
    actions = build_actions(_plan(), "anthropic")

    # 9 specialists + 1 coordinator
    assert len(actions) == 10
    for action in actions:
        assert action.kind == "http_request"
        req = action.payload
        assert req["method"] == "POST"
        assert req["url"].endswith("/v1/agents")
        assert req["headers"]["anthropic-beta"] == ANTHROPIC_BETA_HEADER
        # never embed a real key in a dry-run plan
        assert req["headers"]["x-api-key"] == "$ANTHROPIC_API_KEY"
        assert action.requires_approval is True


def test_aws_actions_use_configurable_base_and_flag_aws_auth():
    actions = build_actions(
        _plan(), "aws", base_url="https://bedrock.example.aws/anthropic"
    )

    assert len(actions) == 10
    for action in actions:
        assert action.backend == "aws"
        assert action.payload["url"].startswith("https://bedrock.example.aws/anthropic")
        assert "aws" in action.gate.lower()


def test_browser_actions_carry_steps_and_require_approval():
    actions = build_actions(_plan(), "browser")

    assert len(actions) == 10
    for action in actions:
        assert action.backend == "browser"
        assert action.kind == "browser_steps"
        steps = action.payload["steps"]
        assert isinstance(steps, list) and len(steps) >= 3
        assert action.requires_approval is True
        assert "platform.claude.com" in action.target


def test_build_actions_all_aggregates_every_backend():
    actions = build_actions(_plan(), "all")
    backends = {a.backend for a in actions}
    assert backends == {"sovereign", "anthropic", "aws", "browser"}


def test_render_plan_is_human_readable_and_redacts_secrets():
    text = render_plan(build_actions(_plan(), "anthropic"))
    assert "POST" in text
    assert "$ANTHROPIC_API_KEY" in text
    assert "sk-ant" not in text


def test_internal_deploy_cli_dryrun_json_defaults_to_sovereign(monkeypatch):
    monkeypatch.setenv("CROWE_LOGIC_OWNER_PRINCIPALS", "owner@crowelogic.com")
    result = CliRunner().invoke(cli_mod.main, ["internal", "deploy", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["backend"] == "sovereign"
    assert payload["applied"] is False
    assert len(payload["actions"]) == 9


def test_internal_deploy_cli_external_apply_is_blocked_without_creds(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = CliRunner().invoke(
        cli_mod.main, ["internal", "deploy", "--backend", "anthropic", "--apply"]
    )

    # Must refuse to create live external agents without an authenticated path.
    assert result.exit_code != 0
    assert "blocked" in result.output.lower() or "gated" in result.output.lower()


# --- execution layer (live create, mocked transport) --------------------------


def test_execute_requires_confirm_and_key():
    import pytest

    plan = _plan()
    with pytest.raises(PermissionError):
        execute_managed_agents(
            plan, api_key="sk-ant-test", confirm=False, transport=_FakeTransport()
        )
    with pytest.raises(PermissionError):
        execute_managed_agents(
            plan, api_key=None, confirm=True, transport=_FakeTransport()
        )


def test_execute_creates_specialists_then_coordinator_with_resolved_roster(tmp_path):
    plan = _plan()
    fake = _FakeTransport()
    manifest_path = tmp_path / "manifest.json"

    result = execute_managed_agents(
        plan,
        backend="anthropic",
        api_key="sk-ant-secret",
        confirm=True,
        transport=fake,
        manifest_path=str(manifest_path),
    )

    # 9 specialists + 1 coordinator created, in that order
    assert len(fake.calls) == 10
    assert all(c["url"].endswith("/v1/agents") for c in fake.calls)
    # real key is used on the wire, never the placeholder
    assert all(c["headers"]["x-api-key"] == "sk-ant-secret" for c in fake.calls)
    assert all(
        c["headers"]["anthropic-beta"] == ANTHROPIC_BETA_HEADER for c in fake.calls
    )

    # the LAST call is the coordinator, and its roster references resolved ids
    coordinator_body = fake.calls[-1]["body"]
    roster = coordinator_body["multiagent"]["agents"]
    created_ids = {r["agent_id"]: r["id"] for r in result["created"]}
    for member in roster:
        assert member["id"] in created_ids.values()
        assert "${" not in member["id"]  # placeholder fully resolved

    # manifest persisted with returned ids
    saved = json.loads(manifest_path.read_text())
    assert len(saved["created"]) == 9
    assert saved["coordinator"]["id"].startswith("agt_")
    assert "sk-ant-secret" not in manifest_path.read_text()  # never persist the key


def test_emit_browser_script_is_runnable_playwright_python():
    script = emit_browser_script(_plan())
    assert "playwright" in script.lower()
    assert "platform.claude.com" in script
    assert "Create agent" in script  # the UI button the script clicks
    # all 9 specialists + coordinator embedded in the AGENTS roster
    assert script.count('"name":') == 10
    assert "CL Internal Development Coordinator" in script
