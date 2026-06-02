"""Tests for cli.auth — the Crowe ID token store + refresh lifecycle.

STORE_PATH is monkeypatched to a tmp file so no real ~/.config is touched, and
the network refresh grant is patched so these run offline.
"""

import os
import time

import pytest

from cli import auth


def test_store_roundtrip_mode_0600(tmp_path, monkeypatch):
    p = tmp_path / "auth.json"
    monkeypatch.setattr(auth, "STORE_PATH", str(p))
    auth.save_creds(
        {
            "access_token": "a",
            "refresh_token": "r",
            "expires_at": time.time() + 300,
            "username": "u",
            "crowe_tier": "enterprise",
        }
    )
    assert (os.stat(p).st_mode & 0o777) == 0o600
    got = auth.load_creds()
    assert got["username"] == "u" and got["crowe_tier"] == "enterprise"


def test_not_logged_in_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "STORE_PATH", str(tmp_path / "nope.json"))
    with pytest.raises(auth.NotLoggedIn):
        auth.current_access_token()


def test_returns_token_when_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "STORE_PATH", str(tmp_path / "auth.json"))
    auth.save_creds(
        {
            "access_token": "fresh",
            "refresh_token": "r",
            "expires_at": time.time() + 300,
            "username": "u",
            "crowe_tier": "pro",
        }
    )

    def boom(_):  # refresh must NOT be called when the token is fresh
        raise AssertionError("refresh should not run for a fresh token")

    monkeypatch.setattr(auth, "_refresh_grant", boom)
    assert auth.current_access_token() == "fresh"


def test_refreshes_when_near_expiry(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "STORE_PATH", str(tmp_path / "auth.json"))
    auth.save_creds(
        {
            "access_token": "old",
            "refresh_token": "r",
            "expires_at": time.time() + 5,  # within skew -> refresh
            "username": "u",
            "crowe_tier": "enterprise",
        }
    )

    def fake_refresh(refresh_token):
        assert refresh_token == "r"
        return {"access_token": "new", "refresh_token": "r2", "expires_in": 300}

    monkeypatch.setattr(auth, "_refresh_grant", fake_refresh)
    assert auth.current_access_token() == "new"
    assert auth.load_creds()["refresh_token"] == "r2"


def test_whoami_and_logout(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "STORE_PATH", str(tmp_path / "auth.json"))
    auth.save_creds(
        {
            "access_token": "a",
            "refresh_token": "r",
            "expires_at": time.time() + 300,
            "username": "u",
            "crowe_tier": "pro",
        }
    )
    assert auth.whoami() == {"username": "u", "crowe_tier": "pro"}
    auth.logout()
    with pytest.raises(auth.NotLoggedIn):
        auth.whoami()
