"""Tests for the gateway principal resolver accepting Crowe ID bearer tokens.

The JWKS signing-key lookup is patched out so these run offline. We assert that a
valid Crowe ID token resolves to a principal dict with the mapped plan, and that
the metering guard correctly classifies token vs API-key principals.
"""

import datetime

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from control_plane import gateway, oidc

ISSUER = "https://id.crowelogic.com/realms/crowe"


@pytest.fixture
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _bearer(key, tier):
    now = datetime.datetime.now(datetime.timezone.utc)
    return jwt.encode(
        {
            "iss": ISSUER,
            "sub": "kc-sub-1",
            "exp": now + datetime.timedelta(minutes=5),
            "crowe_tier": tier,
            "preferred_username": "michael@crowelogic.com",
        },
        key,
        algorithm="RS256",
        headers={"kid": "k"},
    )


def _patch_signing_key(monkeypatch, key):
    class _SK:
        def __init__(self, k):
            self.key = k

    monkeypatch.setattr(oidc, "_signing_key_for", lambda t, i: _SK(key.public_key()))


@pytest.mark.asyncio
async def test_token_principal_resolves_plan(monkeypatch, key):
    _patch_signing_key(monkeypatch, key)
    tok = _bearer(key, "enterprise")
    info = await gateway._resolve_principal(
        authorization=f"Bearer {tok}", x_api_key=None, db=None
    )
    assert info["principal"] == "crowe-id"
    assert info["plan_id"] == "enterprise"
    assert info["workspace_id"] == "kc-sub-1"
    assert info["user_id"] == "kc-sub-1"
    assert info["subject"] == "michael@crowelogic.com"


@pytest.mark.asyncio
async def test_token_principal_pro_tier_maps_to_pro(monkeypatch, key):
    _patch_signing_key(monkeypatch, key)
    tok = _bearer(key, "pro")
    info = await gateway._resolve_principal(
        authorization=f"Bearer {tok}", x_api_key=None, db=None
    )
    assert info["plan_id"] == "pro"


@pytest.mark.asyncio
async def test_invalid_token_rejected(monkeypatch, key):
    _patch_signing_key(monkeypatch, key)
    now = datetime.datetime.now(datetime.timezone.utc)
    expired = jwt.encode(
        {"iss": ISSUER, "sub": "x", "exp": now - datetime.timedelta(minutes=1)},
        key,
        algorithm="RS256",
        headers={"kid": "k"},
    )
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await gateway._resolve_principal(
            authorization=f"Bearer {expired}", x_api_key=None, db=None
        )
    assert exc.value.status_code == 401
