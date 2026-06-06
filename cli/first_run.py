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
