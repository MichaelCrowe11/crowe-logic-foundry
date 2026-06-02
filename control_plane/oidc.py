"""Crowe ID (Keycloak) OIDC token verification for the gateway.

Verifies RS256 access tokens against the issuer's JWKS (fetched + cached by
PyJWT's PyJWKClient, which refreshes on an unknown kid) and maps Crowe ID tiers
to gateway plan ids. The only side effect is the cached JWKS fetch.
"""

from __future__ import annotations

import os

import jwt
from jwt import PyJWKClient

CROWE_ID_ISSUER = os.environ.get(
    "CROWE_ID_ISSUER", "https://id.crowelogic.com/realms/crowe"
)
CROWE_ID_AUDIENCE = os.environ.get("CROWE_ID_AUDIENCE") or None

_TIER_PLAN = {
    "free": "personal",
    "pro": "pro",
    "studio": "team",
    "enterprise": "enterprise",
}

# JWKS clients are cached per issuer so key material is fetched once and
# refreshed automatically on an unknown kid (Keycloak key rotation).
_jwks_clients: dict[str, PyJWKClient] = {}


def tier_to_plan(crowe_tier: str | None) -> str:
    """Map a Crowe ID tier to a gateway plan id. Unknown/missing -> least privilege."""
    return _TIER_PLAN.get((crowe_tier or "").lower(), "personal")


def looks_like_jwt(token: str) -> bool:
    """Cheap structural check: a JWT has three non-empty dot-separated segments."""
    parts = token.split(".")
    return len(parts) == 3 and all(parts)


def _signing_key_for(token: str, issuer: str):
    """Return the PyJWK signing key matching the token's kid (cached, refresh on miss)."""
    uri = f"{issuer.rstrip('/')}/protocol/openid-connect/certs"
    client = _jwks_clients.get(uri)
    if client is None:
        client = PyJWKClient(uri)
        _jwks_clients[uri] = client
    return client.get_signing_key_from_jwt(token)


def verify_token(
    token: str,
    issuer: str = CROWE_ID_ISSUER,
    audience: str | None = CROWE_ID_AUDIENCE,
) -> dict:
    """Verify an RS256 access token and return its claims. Raises jwt.* on failure."""
    signing_key = _signing_key_for(token, issuer)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        issuer=issuer,
        audience=audience,
        options={"verify_aud": audience is not None},
    )
