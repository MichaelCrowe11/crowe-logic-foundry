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

    def _raise_not_logged_in():
        raise auth.NotLoggedIn("no store")

    monkeypatch.setattr(first_run, "_load_creds", _raise_not_logged_in)


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


def test_ensure_first_run_passes_with_creds(monkeypatch):
    monkeypatch.setattr(first_run, "_load_creds", lambda: {"access_token": "x"})
    from rich.console import Console
    assert first_run.ensure_first_run(Console(file=None, quiet=True)) is True


def test_ensure_first_run_blocks_on_none(monkeypatch):
    from rich.console import Console
    from io import StringIO
    buf = StringIO()
    console = Console(file=buf, width=100)
    assert first_run.ensure_first_run(console) is False
    out = buf.getvalue()
    assert "crowe-logic login" in out
    assert "crowe-logic init --node" in out
