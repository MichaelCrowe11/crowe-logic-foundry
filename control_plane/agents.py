"""Agent identity: distinguish Crowe ID client_credentials (machine) tokens from
human logins, and map them to a metered agent principal keyed by client_id.

Keycloak stamps service-account (client_credentials) tokens with
`preferred_username = "service-account-<clientId>"` and a `clientId`/`azp` claim,
and carries no human `email`. That is our discriminator.
"""

from __future__ import annotations

from . import oidc


def is_agent_token(claims: dict) -> bool:
    """True if the verified token is a machine (client_credentials) principal."""
    username = claims.get("preferred_username", "") or ""
    return username.startswith("service-account-")


def agent_principal(claims: dict) -> dict:
    """Map verified agent-token claims to a metered principal dict.

    client_id resolution order: clientId -> azp -> sub (always non-empty).
    workspace_id == client_id so the agent's wallet is keyed by its identity.
    """
    client_id = claims.get("clientId") or claims.get("azp") or claims["sub"]
    return {
        "principal": "crowe-agent",
        "client_id": client_id,
        "workspace_id": client_id,
        "user_id": claims["sub"],
        "plan_id": oidc.tier_to_plan(claims.get("crowe_tier")),
        "subject": claims.get("preferred_username"),
    }
