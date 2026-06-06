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


def ensure_first_run(console) -> bool:
    """Gate a session start. Returns True to proceed, False to exit cleanly.

    Phase 2 turns the NONE branch into anonymous free-tier bootstrap; until
    then NONE shows the card and exits.
    """
    state = detect_credential_state()
    if state is not CredState.NONE:
        return True
    render_first_run_card(console)
    return False
