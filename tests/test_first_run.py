"""First-run credential-state detection and onboarding card."""
import pytest

from cli import first_run
from cli.first_run import CredState


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    # Simulate a machine with no creds: strip every api_key_env in the chain.
    from config.agent_config import MODEL_CHAIN
    for entry in MODEL_CHAIN:
        for key in ("api_key_env", "endpoint_env"):
            env = entry.get(key)
            if env:
                monkeypatch.delenv(env, raising=False)
    monkeypatch.delenv("CROWE_LOGIC_GATEWAY_URL", raising=False)
    # No Crowe ID session on disk.
    from cli import auth
    monkeypatch.setattr(
        first_run, "_load_creds",
        lambda: (_ for _ in ()).throw(auth.NotLoggedIn("no store")),
    )


def test_none_when_nothing_present():
    assert first_run.detect_credential_state() is CredState.NONE


def test_signed_in_wins(monkeypatch):
    monkeypatch.setattr(first_run, "_load_creds", lambda: {"access_token": "x"})
    assert first_run.detect_credential_state() is CredState.SIGNED_IN


def test_env_creds(monkeypatch):
    from config.agent_config import MODEL_CHAIN
    env = next(e["api_key_env"] for e in MODEL_CHAIN if e.get("api_key_env"))
    monkeypatch.setenv(env, "test-key")
    assert first_run.detect_credential_state() is CredState.ENV_CREDS


def test_gateway_only(monkeypatch):
    monkeypatch.setenv("CROWE_LOGIC_GATEWAY_URL", "https://example.test")
    assert first_run.detect_credential_state() is CredState.GATEWAY_ONLY
