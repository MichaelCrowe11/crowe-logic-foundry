"""Internal development agent plan for the Crowe Logic Foundry.

This module is deliberately side-effect free. It defines the owner/staff-only
agent roster and deployment payloads that the CLI can inspect before anything
is created in Claude Console or another managed-agent surface.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import os
from typing import Any


OWNER_ENV = "CROWE_LOGIC_OWNER_PRINCIPALS"
STAFF_ENV = "CROWE_LOGIC_APPROVED_STAFF"
WORKSPACE_ENV = "CROWE_LOGIC_INTERNAL_WORKSPACE"
DEFAULT_WORKSPACE = "Crowe Logic Internal Development"


@dataclass(frozen=True)
class InternalAccessPolicy:
    """Owner/staff-only access contract for internal development agents."""

    scope: str
    workspace: str
    owner_principals: tuple[str, ...] = ()
    approved_staff_principals: tuple[str, ...] = ()
    required_claims: tuple[str, ...] = (
        "crowe_owner",
        "crowe_internal_staff",
    )
    default_tool_policy: str = "always_ask"
    destructive_tool_policy: str = "always_ask"

    @property
    def allowed_principals(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.owner_principals + self.approved_staff_principals))

    def allows(self, principal: str | None) -> bool:
        principal = (principal or "").strip().lower()
        return bool(principal and principal in {p.lower() for p in self.allowed_principals})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InternalAgentProfile:
    """Reusable profile for a high-skill internal development agent."""

    agent_id: str
    name: str
    charter: str
    model_preference: tuple[str, ...]
    tool_domains: tuple[str, ...]
    mission_tags: tuple[str, ...]
    self_heal_duties: tuple[str, ...] = ()
    prompt_constraints: tuple[str, ...] = (
        "First-party Crowe Logic identity only.",
        "Ground claims in code, telemetry, docs, or explicit assumptions.",
        "Never mutate production, secrets, billing, or staff access without owner approval.",
        "Prefer small audited patches, focused tests, and rollback notes.",
    )

    def system_prompt(self) -> str:
        duties = "\n".join(f"- {item}" for item in self.self_heal_duties)
        constraints = "\n".join(f"- {item}" for item in self.prompt_constraints)
        return (
            f"You are {self.name}, an owner/staff-only internal development agent "
            "inside the Crowe Logic Foundry.\n\n"
            f"Charter: {self.charter}\n\n"
            "Mission alignment: advance Crowe Logic as a rigorous, sovereign, "
            "multi-model AI foundry for complex scientific, engineering, operator, "
            "and business workflows.\n\n"
            f"Self-heal duties:\n{duties or '- No autonomous repair duties assigned.'}\n\n"
            f"Operating constraints:\n{constraints}\n"
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["system_prompt"] = self.system_prompt()
        return data


@dataclass(frozen=True)
class SelfHealLoop:
    name: str
    trigger: str
    evidence: tuple[str, ...]
    repair_path: tuple[str, ...]
    approval_gate: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InternalDevelopmentPlan:
    """Complete scaffold for internal agent deployment and CLI review."""

    workspace: str
    recommended_count: int
    access_policy: InternalAccessPolicy
    agents: tuple[InternalAgentProfile, ...]
    self_heal_loops: tuple[SelfHealLoop, ...]
    deployment_surface: str
    deployment_steps: tuple[str, ...]
    claude_agent_payloads: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    coordinator_payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace": self.workspace,
            "recommended_count": self.recommended_count,
            "access_policy": self.access_policy.to_dict(),
            "agents": [agent.to_dict() for agent in self.agents],
            "self_heal_loops": [loop.to_dict() for loop in self.self_heal_loops],
            "deployment_surface": self.deployment_surface,
            "deployment_steps": list(self.deployment_steps),
            "claude_agent_payloads": list(self.claude_agent_payloads),
            "coordinator_payload": self.coordinator_payload,
        }


def _split_env_list(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    parts = []
    for raw in value.replace(";", ",").split(","):
        item = raw.strip()
        if item:
            parts.append(item)
    return tuple(dict.fromkeys(parts))


def access_policy_from_environment() -> InternalAccessPolicy:
    """Build the owner/staff policy from environment and local identity."""

    owners = _split_env_list(os.environ.get(OWNER_ENV))
    staff = _split_env_list(os.environ.get(STAFF_ENV))
    if not owners:
        local_user = os.environ.get("USER") or os.environ.get("LOGNAME") or "local-owner"
        owners = (local_user,)
    workspace = os.environ.get(WORKSPACE_ENV, DEFAULT_WORKSPACE).strip() or DEFAULT_WORKSPACE
    return InternalAccessPolicy(
        scope="owner_and_approved_staff_only",
        workspace=workspace,
        owner_principals=owners,
        approved_staff_principals=staff,
    )


def recommended_internal_agents() -> tuple[InternalAgentProfile, ...]:
    """Return the recommended internal development agent team.

    Nine roles is the practical minimum for the current Foundry surface: it
    separates architecture, runtime, healing, security, quality, knowledge,
    browser deployment, tool integration, and release governance without
    creating redundant agents.
    """

    deep = ("claude-fable-5", "claude-opus-4-8", "claude-sonnet-4-6")
    balanced = ("claude-opus-4-8", "claude-sonnet-4-6")
    fast = ("claude-sonnet-4-6", "claude-haiku-4-5")
    return (
        InternalAgentProfile(
            agent_id="internal-architecture-steward",
            name="CL Internal Architecture Steward",
            charter=(
                "Review module boundaries, provider routing, Synapse orchestration, "
                "and long-horizon platform design before implementation work fans out."
            ),
            model_preference=deep,
            tool_domains=("repo_read", "architecture_docs", "dependency_graph", "design_review"),
            mission_tags=("architecture", "synapse", "mission_alignment"),
            self_heal_duties=(
                "Detect architectural drift between CLI, control plane, Synapse, and provider modules.",
                "Produce boundary fixes and migration plans before large refactors.",
            ),
        ),
        InternalAgentProfile(
            agent_id="internal-cli-runtime-engineer",
            name="CL Internal CLI Runtime Engineer",
            charter=(
                "Own Click commands, headless protocol, provider failover, streaming, "
                "runtime state, and local install health."
            ),
            model_preference=balanced,
            tool_domains=("python", "pytest", "provider_runtime", "headless_protocol"),
            mission_tags=("cli", "runtime", "failover"),
            self_heal_duties=(
                "Repair stale wrappers, broken interpreter paths, and provider failover regressions.",
                "Add focused CLI regression tests for every runtime repair.",
            ),
        ),
        InternalAgentProfile(
            agent_id="internal-self-heal-sre",
            name="CL Internal Self-Heal SRE",
            charter=(
                "Turn deploy checks, provider health, route telemetry, and failing tests "
                "into reversible repair proposals."
            ),
            model_preference=balanced,
            tool_domains=("health_checks", "logs", "telemetry", "rollback"),
            mission_tags=("self_heal", "reliability", "ops"),
            self_heal_duties=(
                "Classify failures as auth, quota, offline, timeout, regression, or environment drift.",
                "Propose repair actions with evidence, expected blast radius, and rollback command.",
            ),
        ),
        InternalAgentProfile(
            agent_id="internal-browser-deployment-operator",
            name="CL Internal Browser Deployment Operator",
            charter=(
                "Drive console-based deployment surfaces when no API path is safer, "
                "capture exact IDs, and stop before destructive actions without owner approval."
            ),
            model_preference=fast,
            tool_domains=("browser", "console_admin", "deployment_ids", "runbooks"),
            mission_tags=("deployment", "claude_console", "operator"),
            self_heal_duties=(
                "Verify console state against local manifests before creating or updating agents.",
                "Record every external deployment ID back into local operator notes or env guidance.",
            ),
        ),
        InternalAgentProfile(
            agent_id="internal-security-entitlements",
            name="CL Internal Security Entitlements Engineer",
            charter=(
                "Enforce owner/staff-only access, secrets boundaries, workspace membership, "
                "and tool permission policy for internal agents."
            ),
            model_preference=deep,
            tool_domains=("auth", "oidc", "secrets", "policy", "audit"),
            mission_tags=("security", "entitlements", "approved_staff"),
            self_heal_duties=(
                "Reject any internal-agent action when principal, workspace, or permission state is unknown.",
                "Require owner approval for staff changes, secret access, or production-impacting tools.",
            ),
        ),
        InternalAgentProfile(
            agent_id="internal-eval-quality-lead",
            name="CL Internal Eval Quality Lead",
            charter=(
                "Maintain benchmark coverage, prompt-quality gates, regression fixtures, "
                "and mission-specific eval rubrics."
            ),
            model_preference=balanced,
            tool_domains=("bench", "eval", "quality", "coverage"),
            mission_tags=("evaluation", "quality", "crowelm"),
            self_heal_duties=(
                "Convert repeated failures into permanent fixtures or benchmark cases.",
                "Gate self-improvement proposals on measurable quality deltas.",
            ),
        ),
        InternalAgentProfile(
            agent_id="internal-synapse-knowledge-curator",
            name="CL Internal Synapse Knowledge Curator",
            charter=(
                "Curate persistent memory, project knowledge, system prompts, and Synapse "
                "routing context so complex tasks stay grounded in Crowe Logic history."
            ),
            model_preference=balanced,
            tool_domains=("memory", "knowledge", "prompts", "routing"),
            mission_tags=("synapse", "memory", "grounding"),
            self_heal_duties=(
                "Detect stale or conflicting knowledge before it contaminates routing decisions.",
                "Promote verified operational lessons into durable memory proposals.",
            ),
        ),
        InternalAgentProfile(
            agent_id="internal-tooling-integrations",
            name="CL Internal Tooling Integrations Engineer",
            charter=(
                "Own tool registry, MCP integrations, browser/shell capability probes, "
                "and sandbox boundaries across local and hosted runtimes."
            ),
            model_preference=balanced,
            tool_domains=("tools", "mcp", "browser", "shell", "sandbox"),
            mission_tags=("tools", "mcp", "sandbox"),
            self_heal_duties=(
                "Disable or hide tools that cannot work in the active runtime.",
                "Add capability probes before advertising new tool surfaces.",
            ),
        ),
        InternalAgentProfile(
            agent_id="internal-release-steward",
            name="CL Internal Release Steward",
            charter=(
                "Prepare changes for shipping: diff review, tests, changelog notes, "
                "deployment proof, and rollback guidance."
            ),
            model_preference=fast,
            tool_domains=("git", "tests", "release_notes", "deploy_proof"),
            mission_tags=("release", "governance", "audit"),
            self_heal_duties=(
                "Keep unrelated dirty work untouched and call out partial verification clearly.",
                "Require proof before declaring a deploy or repair complete.",
            ),
        ),
    )


def self_heal_loops() -> tuple[SelfHealLoop, ...]:
    return (
        SelfHealLoop(
            name="Provider and model failover repair",
            trigger="deploy health check, route telemetry, or streaming turn records provider failure",
            evidence=("provider_health.json", "crowe-logic deploy", "route decision", "focused provider tests"),
            repair_path=(
                "classify failure",
                "select healthy fallback",
                "patch routing or config",
                "run focused tests",
                "record rollback note",
            ),
            approval_gate="owner approval before changing live provider credentials or quotas",
        ),
        SelfHealLoop(
            name="CLI install and runtime repair",
            trigger="installed crowe-logic wrapper, venv, or headless protocol fails",
            evidence=("which crowe-logic", ".venv/bin/crowe-logic", "headless smoke", "pytest family"),
            repair_path=(
                "verify live wrapper path",
                "repair editable install if stale",
                "patch failing runtime module",
                "exercise real CLI path",
            ),
            approval_gate="owner approval before reinstalling dependencies outside the repo sandbox",
        ),
        SelfHealLoop(
            name="Tool capability self-heal",
            trigger="tool is advertised but current runtime cannot execute it",
            evidence=("tool probe", "runtime env", "tool error", "registry entry"),
            repair_path=(
                "add capability probe",
                "hide unavailable tool",
                "surface explicit recovery instructions",
                "add regression test",
            ),
            approval_gate="owner approval before enabling external or destructive tools",
        ),
        SelfHealLoop(
            name="Quality regression improvement",
            trigger="tests, benchmarks, evals, or user review exposes repeated weakness",
            evidence=("failing test", "bench report", "eval rubric", "user correction"),
            repair_path=(
                "reduce to fixture",
                "patch prompt/code/data",
                "rerun focused gate",
                "document measured improvement",
            ),
            approval_gate="human review before promoting generated training data or broad prompt changes",
        ),
    )


def _agent_toolset(default_policy: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "agent_toolset_20260401",
            "default_config": {
                "permission_policy": {
                    "type": default_policy,
                },
            },
            "configs": [
                {
                    "name": "bash",
                    "permission_policy": {
                        "type": "always_ask",
                    },
                },
                {
                    "name": "write",
                    "permission_policy": {
                        "type": "always_ask",
                    },
                },
            ],
        }
    ]


def build_claude_agent_payloads(
    agents: tuple[InternalAgentProfile, ...],
    policy: InternalAccessPolicy,
) -> tuple[dict[str, Any], ...]:
    payloads: list[dict[str, Any]] = []
    for agent in agents:
        payloads.append(
            {
                "name": agent.name,
                "model": {"id": agent.model_preference[0]},
                "system": agent.system_prompt(),
                "description": agent.charter,
                "tools": _agent_toolset(policy.default_tool_policy),
                "metadata": {
                    "crowe_internal": "true",
                    "access_scope": policy.scope,
                    "agent_id": agent.agent_id,
                    "workspace": policy.workspace,
                    "mission_tags": ",".join(agent.mission_tags),
                },
            }
        )
    return tuple(payloads)


def build_coordinator_payload(
    agents: tuple[InternalAgentProfile, ...],
    policy: InternalAccessPolicy,
) -> dict[str, Any]:
    return {
        "name": "CL Internal Development Coordinator",
        "model": {"id": "claude-fable-5"},
        "system": (
            "You coordinate Crowe Logic's owner/staff-only internal development "
            "agents. Delegate only to the declared roster, require evidence before "
            "claims, and pause for owner approval before live deployment, staff "
            "access, production mutation, billing, or secret changes."
        ),
        "description": (
            "Owner/staff-only coordinator for Foundry architecture, self-heal, "
            "quality, deployment, security, and release work."
        ),
        "tools": _agent_toolset(policy.default_tool_policy),
        "multiagent": {
            "agents": [
                {
                    "type": "agent",
                    "id": f"${{{agent.agent_id}.claude_agent_id}}",
                }
                for agent in agents
            ]
        },
        "metadata": {
            "crowe_internal": "true",
            "access_scope": policy.scope,
            "workspace": policy.workspace,
            "recommended_delegate_count": str(len(agents)),
        },
    }


def claude_console_deployment_steps(policy: InternalAccessPolicy) -> tuple[str, ...]:
    return (
        f"Create or select Claude Console workspace: {policy.workspace}.",
        "Restrict workspace members to owner principals and approved staff only.",
        "Assign Workspace Admin only to the owner; approved staff should use the least role needed.",
        "Create the nine specialist Managed Agents from the generated payloads.",
        "Use permission_policy=always_ask for the agent toolset, with bash and write explicitly gated.",
        "Replace coordinator roster placeholders with the returned specialist agent IDs.",
        "Create the CL Internal Development Coordinator and attach the nine-agent roster.",
        "Run one non-destructive session per agent and record returned agent IDs locally.",
        "Only after owner approval, wire the IDs into Foundry secrets or gateway configuration.",
    )


def build_internal_development_plan(
    *,
    deployment_surface: str = "claude-console",
    access_policy: InternalAccessPolicy | None = None,
) -> InternalDevelopmentPlan:
    policy = access_policy or access_policy_from_environment()
    agents = recommended_internal_agents()
    payloads = build_claude_agent_payloads(agents, policy)
    coordinator = build_coordinator_payload(agents, policy)
    return InternalDevelopmentPlan(
        workspace=policy.workspace,
        recommended_count=len(agents),
        access_policy=policy,
        agents=agents,
        self_heal_loops=self_heal_loops(),
        deployment_surface=deployment_surface,
        deployment_steps=claude_console_deployment_steps(policy),
        claude_agent_payloads=payloads,
        coordinator_payload=coordinator,
    )
