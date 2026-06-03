"""Unit tests for control_plane.oidc (Crowe ID token verification + tier mapping).

The JWKS fetch is patched out (_signing_key_for) so these tests run offline and
exercise only our verification contract: signature alg, issuer, expiry, claims.
"""

import datetime

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from control_plane import oidc

ISSUER = "https://id.crowelogic.com/realms/crowe"


@pytest.fixture
def keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _token(key, claims, kid="testkid"):
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


def _patch_signing_key(monkeypatch, key):
    class _SK:  # mimics jwt.PyJWK signing key (exposes .key)
        def __init__(self, k):
            self.key = k

    monkeypatch.setattr(
        oidc, "_signing_key_for", lambda token, issuer: _SK(key.public_key())
    )


def test_tier_to_plan_table():
    assert oidc.tier_to_plan("free") == "personal"
    assert oidc.tier_to_plan("pro") == "pro"
    assert oidc.tier_to_plan("studio") == "team"
    assert oidc.tier_to_plan("enterprise") == "enterprise"
    assert oidc.tier_to_plan(None) == "personal"
    assert oidc.tier_to_plan("bogus") == "personal"


def test_looks_like_jwt():
    assert oidc.looks_like_jwt("aaa.bbb.ccc") is True
    assert oidc.looks_like_jwt("cl_livekeynotjwt") is False
    assert oidc.looks_like_jwt("a.b") is False
    assert oidc.looks_like_jwt("a..c") is False


def test_verify_valid_token(monkeypatch, keypair):
    _patch_signing_key(monkeypatch, keypair)
    now = datetime.datetime.now(datetime.timezone.utc)
    tok = _token(
        keypair,
        {
            "iss": ISSUER,
            "sub": "abc",
            "exp": now + datetime.timedelta(minutes=5),
            "crowe_tier": "enterprise",
            "preferred_username": "michael@crowelogic.com",
        },
    )
    claims = oidc.verify_token(tok, ISSUER)
    assert claims["crowe_tier"] == "enterprise"
    assert claims["preferred_username"] == "michael@crowelogic.com"


def test_verify_expired_token(monkeypatch, keypair):
    _patch_signing_key(monkeypatch, keypair)
    now = datetime.datetime.now(datetime.timezone.utc)
    tok = _token(
        keypair,
        {"iss": ISSUER, "sub": "abc", "exp": now - datetime.timedelta(minutes=1)},
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        oidc.verify_token(tok, ISSUER)


def test_verify_wrong_issuer(monkeypatch, keypair):
    _patch_signing_key(monkeypatch, keypair)
    now = datetime.datetime.now(datetime.timezone.utc)
    tok = _token(
        keypair,
        {
            "iss": "https://evil.example/realms/x",
            "sub": "abc",
            "exp": now + datetime.timedelta(minutes=5),
        },
    )
    with pytest.raises(jwt.InvalidIssuerError):
        oidc.verify_token(tok, ISSUER)
