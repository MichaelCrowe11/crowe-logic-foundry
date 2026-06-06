"""First-run credential detection and onboarding for the crowe-logic CLI.

This module is the ONLY place that decides what a zero-credential start does.
Policy (caps, free-tier copy) lives server-side; this is protocol + rendering.
"""

from __future__ import annotations

import os
from enum import Enum


class CredState(str, Enum):
    SIGNED_IN = "signed_in"      # Crowe ID session on disk -> PR #45 gateway routing
    ENV_CREDS = "env_creds"      # provider env keys -> existing local routing
    GATEWAY_ONLY = "gateway_only"  # explicit gateway URL, no local keys
    NONE = "none"                # fresh install -> first-run flow


def _load_creds() -> dict:
    """Indirection point so tests can stub the auth store without touching disk."""
    from cli import auth

    return auth.load_creds()


def detect_credential_state() -> CredState:
    """Classify this machine's credentials. Priority: Crowe ID session > provider env keys > explicit gateway URL > nothing."""
    from cli import auth

    try:
        _load_creds()
        return CredState.SIGNED_IN
    except auth.NotLoggedIn:
        pass

    from config.agent_config import MODEL_CHAIN

    for entry in MODEL_CHAIN:
        key_env = entry.get("api_key_env")
        if key_env and os.environ.get(key_env):
            return CredState.ENV_CREDS

    if os.environ.get("CROWE_LOGIC_GATEWAY_URL"):
        return CredState.GATEWAY_ONLY

    return CredState.NONE


def render_first_run_card(console) -> None:
    """One clean card instead of the provider-error wall. No emojis (Crowe design)."""
    from rich.panel import Panel

    body = (
        "[bold]No credentials found.[/bold]\n\n"
        "Pick a path:\n\n"
        "  [bold #bfa669]crowe-logic login[/bold #bfa669]"
        "        Sign in with Crowe ID (recommended)\n"
        "  [bold #bfa669]crowe-logic init --node[/bold #bfa669]"
        "  Scaffold env-file credentials for a self-managed node\n\n"
        "Docs: https://crowelogic.com/docs/cli/getting-started"
    )
    console.print(Panel(body, title="Welcome to Crowe Logic", border_style="#bfa669"))


def _bootstrap_anonymous() -> dict:
    """Register (or reuse) an anonymous device token. Raises on network failure."""
    from cli import gateway_client

    device = gateway_client.load_device()
    if device and device.get("token"):
        return device
    return gateway_client.register_device()


def ensure_first_run(console, session_state: dict | None = None) -> bool:
    """Gate a session start. Returns True to proceed, False to exit cleanly.

    NONE -> anonymous free-tier bootstrap (device token via the gateway); on
    any failure, degrade to the setup card - never a stack trace, and never
    free inference without a server-verified token (deny-by-default).
    """
    state = detect_credential_state()
    if state is not CredState.NONE:
        return True

    try:
        device = _bootstrap_anonymous()
    except Exception:
        render_first_run_card(console)
        return False

    if session_state is not None:
        session_state["anon_device_token"] = device["token"]
        session_state["anon_free_model"] = device.get("free_model", "crowelm-mycelium")
    console.print(
        "  [dim]Free tier active - CroweLM Mycelium, "
        f"{device.get('daily_turn_cap', 20)} turns/day. "
        "Run [/dim][bold #bfa669]crowe-logic login[/bold #bfa669][dim] for full tiers.[/dim]"
    )
    return True


_NODE_ENV_TEMPLATE = """\
# Crowe Logic node credentials - fill in values, then load into your shell:
#   set -a; . ~/.crowe-logic.env; set +a
# (add that line to ~/.bashrc or ~/.zshrc for persistence)

# Required: resolve CroweLM Auto to a live tier per turn.
CROWE_LOGIC_AUTO_ROUTE=1

# Option A - direct Azure tiers (one key serves every alias on crowelm-prod-eastus2):
CROWE_OPEN_API_KEY=
CROWE_OPEN_ENDPOINT=
AZURE_CORE_API_KEY=
AZURE_CORE_ENDPOINT=

# Option B - route through the gateway instead of local keys:
# CROWE_LOGIC_GATEWAY_URL=https://api.crowelogic.com
"""


def scaffold_node_env(path: str | None = None) -> str:
    """Write the self-managed-node env template (key names only). 0600.

    Raises FileExistsError rather than clobbering an existing file.
    """
    target = path or os.path.expanduser("~/.crowe-logic.env")
    if os.path.exists(target):
        raise FileExistsError(target)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(_NODE_ENV_TEMPLATE)
    return target
