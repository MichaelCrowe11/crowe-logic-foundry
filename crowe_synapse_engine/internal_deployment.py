"""Multi-backend deployment driver for the internal development agent plan.

One source of truth — the owner/staff-only plan from ``internal_development`` —
fans out to four deployment surfaces:

- ``sovereign``  : Crowe's own Synapse ``AgentRegistry`` (YAML personas routed
                   through the foundry gateway; models stay on the cloud-provider
                   sourcing path). Fully appl(writes files) locally.
- ``anthropic``  : Claude Managed Agents direct API (``POST /v1/agents``).
- ``aws``        : Claude Managed Agents on Claude Platform on AWS (same shape,
                   AWS-routed base URL + AWS auth).
- ``browser``    : a Playwright runbook that drives the Console UI by hand.

External backends (anthropic/aws/browser) are render-only here: applying them
creates live, externally visible agents and is intentionally gated on an
authenticated credential/session plus explicit owner approval. Only the
``sovereign`` backend is auto-applied, because its artifacts are local and
reversible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Any

import yaml

from .internal_development import InternalDevelopmentPlan

# Placeholder used in dry-run plans so a real key never lands in rendered output.
ANTHROPIC_API_KEY_REF = "$ANTHROPIC_API_KEY"

# --- Sovereign target contract -------------------------------------------------

# Internal agents are deployed branded + sovereign-routed, never pinned to a raw
# vendor model id (keeps them on the cloud-provider sourcing path).
SOVEREIGN_MODEL = "crowelm-pro"

# Internal agents start advisory/read-only. write_file/edit_file/execute_shell/
# git_commit are deliberately NOT here — enabling them is an explicit owner step.
SAFE_SOVEREIGN_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "list_directory",
        "grep_search",
        "git_status",
        "git_diff",
        "git_log",
    }
)

# Subdirectory that the flat AgentRegistry loader (os.listdir, non-recursive)
# deliberately skips, so these personas never leak into the public roster.
SOVEREIGN_SUBDIR = "internal"

# --- External (Managed Agents) target contract ---------------------------------

ANTHROPIC_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION_HEADER = "2023-06-01"
ANTHROPIC_BETA_HEADER = "managed-agents-2026-04-01"
AGENTS_ENDPOINT = "/v1/agents"

# Env var that names the AWS-routed base URL for Claude Platform on AWS.
AWS_BASE_URL_ENV = "CROWE_ANTHROPIC_AWS_BASE_URL"


@dataclass(frozen=True)
class DeploymentAction:
    """One concrete, inspectable step toward deploying an internal agent."""

    backend: str  # sovereign | anthropic | aws | browser
    kind: str  # file_write | http_request | browser_steps
    summary: str
    target: str  # path, URL, or console URL
    payload: dict[str, Any]
    requires_approval: bool
    gate: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "kind": self.kind,
            "summary": self.summary,
            "target": self.target,
            "payload": self.payload,
            "requires_approval": self.requires_approval,
            "gate": self.gate,
        }


# --- Sovereign backend ---------------------------------------------------------


def _sovereign_yaml(profile: Any) -> str:
    """Render one InternalAgentProfile as a Synapse AgentRegistry persona."""

    intended = ", ".join(profile.tool_domains)
    prompt = (
        f"{profile.system_prompt()}\n"
        f"Intended tool domains (owner-gated to enable beyond read-only): "
        f"{intended}\n"
    )
    spec = {
        "name": profile.agent_id,
        "description": profile.charter,
        "model": SOVEREIGN_MODEL,
        "tools": sorted(SAFE_SOVEREIGN_TOOLS),
        "prompt_override": prompt,
        "pipelines": [],
    }
    return yaml.safe_dump(spec, sort_keys=False, allow_unicode=True)


def sovereign_actions(
    plan: InternalDevelopmentPlan, agents_dir: str = "agents"
) -> list[DeploymentAction]:
    base = os.path.join(agents_dir, SOVEREIGN_SUBDIR)
    actions: list[DeploymentAction] = []
    for profile in plan.agents:
        path = os.path.join(base, f"{profile.agent_id}.yaml")
        actions.append(
            DeploymentAction(
                backend="sovereign",
                kind="file_write",
                summary=f"Write sovereign persona: {profile.agent_id}",
                target=path,
                payload={"content": _sovereign_yaml(profile)},
                requires_approval=False,
                gate="owner-gated AgentRegistry must point at this subdir to load",
            )
        )
    return actions


def apply_sovereign(actions: list[DeploymentAction]) -> list[str]:
    """Write sovereign file_write actions to disk. Returns written paths."""

    written: list[str] = []
    for action in actions:
        if action.backend != "sovereign" or action.kind != "file_write":
            continue
        os.makedirs(os.path.dirname(action.target), exist_ok=True)
        with open(action.target, "w", encoding="utf-8") as handle:
            handle.write(action.payload["content"])
        written.append(action.target)
    return written


# --- Managed Agents backends (anthropic / aws) ---------------------------------


def _http_actions(
    plan: InternalDevelopmentPlan,
    *,
    backend: str,
    base_url: str,
    gate: str,
    extra_headers: dict[str, str] | None = None,
) -> list[DeploymentAction]:
    headers = {
        "x-api-key": ANTHROPIC_API_KEY_REF,
        "anthropic-version": ANTHROPIC_VERSION_HEADER,
        "anthropic-beta": ANTHROPIC_BETA_HEADER,
        "content-type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    url = f"{base_url.rstrip('/')}{AGENTS_ENDPOINT}"
    actions: list[DeploymentAction] = []
    for payload in plan.claude_agent_payloads:
        actions.append(
            DeploymentAction(
                backend=backend,
                kind="http_request",
                summary=f"POST create agent: {payload['name']}",
                target=url,
                payload={
                    "method": "POST",
                    "url": url,
                    "headers": headers,
                    "json": payload,
                },
                requires_approval=True,
                gate=gate,
            )
        )
    # Coordinator created last: its roster references the specialist IDs returned
    # by the calls above, so those placeholders must be resolved before applying.
    actions.append(
        DeploymentAction(
            backend=backend,
            kind="http_request",
            summary=f"POST create coordinator: {plan.coordinator_payload['name']}",
            target=url,
            payload={
                "method": "POST",
                "url": url,
                "headers": headers,
                "json": plan.coordinator_payload,
            },
            requires_approval=True,
            gate=f"{gate}; fill roster placeholders with returned specialist IDs first",
        )
    )
    return actions


def anthropic_actions(
    plan: InternalDevelopmentPlan, base_url: str = ANTHROPIC_BASE_URL
) -> list[DeploymentAction]:
    return _http_actions(
        plan,
        backend="anthropic",
        base_url=base_url,
        gate="ANTHROPIC_API_KEY (standard Claude API key) + owner approval",
    )


def aws_actions(
    plan: InternalDevelopmentPlan, base_url: str | None = None
) -> list[DeploymentAction]:
    resolved = base_url or os.environ.get(
        AWS_BASE_URL_ENV, "https://<claude-platform-on-aws-endpoint>"
    )
    return _http_actions(
        plan,
        backend="aws",
        base_url=resolved,
        gate=(
            "AWS credentials for Claude Platform on AWS (Bedrock-routed) + owner "
            f"approval; set {AWS_BASE_URL_ENV}"
        ),
        extra_headers={"x-crowe-routing": "aws"},
    )


# --- Browser backend -----------------------------------------------------------

CONSOLE_URL = "https://platform.claude.com"


def _browser_steps_for(name: str, payload: dict[str, Any]) -> list[dict[str, str]]:
    model_id = payload.get("model", {}).get("id", "claude-opus-4-8")
    return [
        {"action": "navigate", "to": f"{CONSOLE_URL}/agents"},
        {"action": "click", "selector": "button:has-text('Create agent')"},
        {"action": "fill", "field": "name", "value": name},
        {"action": "select", "field": "model", "value": model_id},
        {
            "action": "fill",
            "field": "system prompt",
            "value": "<system prompt from payload>",
        },
        {
            "action": "configure",
            "field": "tools",
            "value": "agent_toolset_20260401 (always_ask)",
        },
        {"action": "click", "selector": "button:has-text('Save')"},
        {"action": "capture", "field": "agent id", "into": "returned agent id"},
    ]


def browser_actions(plan: InternalDevelopmentPlan) -> list[DeploymentAction]:
    actions: list[DeploymentAction] = []
    payloads = list(plan.claude_agent_payloads) + [plan.coordinator_payload]
    for payload in payloads:
        actions.append(
            DeploymentAction(
                backend="browser",
                kind="browser_steps",
                summary=f"Console UI create: {payload['name']}",
                target=f"{CONSOLE_URL}/agents",
                payload={"steps": _browser_steps_for(payload["name"], payload)},
                requires_approval=True,
                gate="logged-in platform.claude.com session (Playwright) + per-run approval",
            )
        )
    return actions


# --- Dispatcher / rendering ----------------------------------------------------

_BACKENDS = ("sovereign", "anthropic", "aws", "browser")


def build_actions(
    plan: InternalDevelopmentPlan, backend: str, **opts: Any
) -> list[DeploymentAction]:
    if backend == "sovereign":
        return sovereign_actions(plan, **opts)
    if backend == "anthropic":
        return anthropic_actions(plan, **opts)
    if backend == "aws":
        return aws_actions(plan, **opts)
    if backend == "browser":
        return browser_actions(plan)
    if backend == "all":
        out: list[DeploymentAction] = []
        out += sovereign_actions(plan)
        out += anthropic_actions(plan)
        out += aws_actions(plan)
        out += browser_actions(plan)
        return out
    raise ValueError(
        f"unknown backend: {backend!r} (expected one of {_BACKENDS} or 'all')"
    )


def render_plan(actions: list[DeploymentAction]) -> str:
    lines: list[str] = []
    for idx, action in enumerate(actions, start=1):
        marker = "APPROVAL" if action.requires_approval else "auto"
        lines.append(f"{idx:>2}. [{action.backend}/{marker}] {action.summary}")
        if action.kind == "http_request":
            lines.append(f"      {action.payload['method']} {action.payload['url']}")
            headers = action.payload["headers"]
            lines.append(
                f"      auth: x-api-key={headers['x-api-key']} "
                f"anthropic-beta={headers['anthropic-beta']}"
            )
        elif action.kind == "file_write":
            lines.append(f"      write {action.target}")
        elif action.kind == "browser_steps":
            lines.append(
                f"      {len(action.payload['steps'])} UI steps @ {action.target}"
            )
        if action.gate:
            lines.append(f"      gate: {action.gate}")
    return "\n".join(lines)


# --- Execution layer (creates live Managed Agents) -----------------------------

DEFAULT_MANIFEST_PATH = os.path.expanduser(
    "~/.crowe-logic/runtime/internal_agents_manifest.json"
)


def _default_transport(
    method: str, url: str, headers: dict[str, str], body: dict[str, Any]
) -> dict[str, Any]:
    """Real HTTP transport (stdlib, no extra deps). Returns parsed JSON."""

    import json as _json
    import urllib.request

    data = _json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 (trusted host)
        return _json.loads(resp.read().decode("utf-8"))


def _resolve_coordinator_roster(
    coordinator_payload: dict[str, Any], created: list[dict[str, Any]]
) -> dict[str, Any]:
    """Replace ``${<agent_id>.claude_agent_id}`` roster placeholders with real ids."""

    import copy

    resolved = copy.deepcopy(coordinator_payload)
    by_agent_id = {c["agent_id"]: c["id"] for c in created}
    for member in resolved.get("multiagent", {}).get("agents", []):
        ref = member.get("id", "")
        if ref.startswith("${") and ref.endswith(".claude_agent_id}"):
            agent_id = ref[2 : -len(".claude_agent_id}")]
            if agent_id in by_agent_id:
                member["id"] = by_agent_id[agent_id]
    return resolved


def execute_managed_agents(
    plan: InternalDevelopmentPlan,
    *,
    backend: str = "anthropic",
    base_url: str | None = None,
    api_key: str | None = None,
    confirm: bool = False,
    transport: Any = None,
    manifest_path: str | None = None,
) -> dict[str, Any]:
    """Create the specialist agents, then the coordinator with a resolved roster.

    Outward-facing: refuses unless ``confirm=True`` AND an ``api_key`` is given.
    ``transport`` is injectable so tests never touch the network. Returns a
    manifest dict and also writes it to ``manifest_path``.
    """

    if not confirm:
        raise PermissionError(
            "refusing to create live external agents without confirm=True"
        )
    if not api_key:
        raise PermissionError("no API key provided for external deployment")

    base = base_url or (
        ANTHROPIC_BASE_URL
        if backend == "anthropic"
        else os.environ.get(AWS_BASE_URL_ENV, "")
    )
    if not base:
        raise ValueError(f"no base URL resolved for backend {backend!r}")

    send = transport or _default_transport
    url = f"{base.rstrip('/')}{AGENTS_ENDPOINT}"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION_HEADER,
        "anthropic-beta": ANTHROPIC_BETA_HEADER,
        "content-type": "application/json",
    }
    if backend == "aws":
        headers["x-crowe-routing"] = "aws"

    created: list[dict[str, Any]] = []
    for payload in plan.claude_agent_payloads:
        resp = send("POST", url, headers, payload)
        created.append(
            {
                "agent_id": payload["metadata"]["agent_id"],
                "name": payload["name"],
                "id": resp.get("id"),
                "version": resp.get("version"),
            }
        )

    coordinator_payload = _resolve_coordinator_roster(plan.coordinator_payload, created)
    coord_resp = send("POST", url, headers, coordinator_payload)
    coordinator = {
        "name": coordinator_payload["name"],
        "id": coord_resp.get("id"),
        "version": coord_resp.get("version"),
    }

    manifest = {
        "backend": backend,
        "base_url": base,
        "workspace": plan.workspace,
        "created": created,
        "coordinator": coordinator,
    }

    target = manifest_path or DEFAULT_MANIFEST_PATH
    os.makedirs(os.path.dirname(target), exist_ok=True)
    import json as _json

    with open(target, "w", encoding="utf-8") as handle:
        handle.write(_json.dumps(manifest, indent=2))  # api_key never included
    manifest["manifest_path"] = target
    return manifest


# --- Browser backend: emit a runnable Playwright script ------------------------


def emit_browser_script(plan: InternalDevelopmentPlan) -> str:
    """Generate a standalone Playwright (Python) script to create the agents."""

    import json as _json

    specs = []
    for payload in list(plan.claude_agent_payloads) + [plan.coordinator_payload]:
        specs.append(
            {
                "name": payload["name"],
                "model": payload.get("model", {}).get("id", "claude-opus-4-8"),
                "system": payload["system"],
            }
        )
    specs_literal = _json.dumps(specs, indent=4)
    return f'''#!/usr/bin/env python3
"""Auto-generated by `crowe-logic internal deploy --backend browser`.

Drives a logged-in {CONSOLE_URL} session to create the owner/staff-only internal
development agents in the Console UI. Run with an authenticated browser profile:

    pip install playwright && playwright install chromium
    python deploy_internal_agents_browser.py

This creates LIVE external agents. Review before running.
"""

from playwright.sync_api import sync_playwright

CONSOLE_URL = "{CONSOLE_URL}"
AGENTS = {specs_literal}


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir="~/.crowe-logic/playwright-console",
            headless=False,
        )
        page = browser.new_page()
        for agent in AGENTS:
            page.goto(f"{{CONSOLE_URL}}/agents")
            page.get_by_role("button", name="Create agent").click()
            page.get_by_label("Name").fill(agent["name"])
            page.get_by_label("Model").fill(agent["model"])
            page.get_by_label("System prompt").fill(agent["system"])
            page.get_by_role("button", name="Save").click()
            print(f"Created (verify id in UI): {{agent['name']}}")
        browser.close()


if __name__ == "__main__":
    main()
'''
