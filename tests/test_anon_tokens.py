"""Anonymous device token mint/verify."""
import pytest

from control_plane import tokens


@pytest.fixture(autouse=True)
def signing_secret(monkeypatch):
    monkeypatch.setenv("CROWE_ANON_SIGNING_SECRET", "test-secret")


def test_mint_and_verify_roundtrip():
    device_id, raw = tokens.make_device_token()
    assert raw.startswith(tokens.ANON_PREFIX)
    assert tokens.verify_device_token(raw) == device_id


def test_verify_rejects_tampered_sig():
    _, raw = tokens.make_device_token()
    assert tokens.verify_device_token(raw[:-4] + "0000") is None


def test_verify_rejects_foreign_prefixes():
    assert tokens.verify_device_token("crowe_pat_abc") is None
    assert tokens.verify_device_token("") is None
    assert tokens.verify_device_token("crowe_anon_nosig") is None


def test_verify_fails_closed_when_secret_unset(monkeypatch):
    _, raw = tokens.make_device_token()  # minted while secret present
    monkeypatch.delenv("CROWE_ANON_SIGNING_SECRET")
    assert tokens.verify_device_token(raw) is None


def test_verify_rejects_malformed_device_id():
    assert tokens.verify_device_token("crowe_anon_../../etc.deadbeef") is None
    assert tokens.verify_device_token("crowe_anon_UPPERCASE123456789012345.deadbeef") is None
