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
    assert first_run.ensure_first_run(Console(file=None, quiet=True), session_state={}) is True


def test_ensure_first_run_blocks_on_none(monkeypatch):
    from rich.console import Console
    from io import StringIO

    def boom():
        raise OSError("network down")

    monkeypatch.setattr(first_run, "_bootstrap_anonymous", boom)
    buf = StringIO()
    console = Console(file=buf, width=100)
    assert first_run.ensure_first_run(console, session_state={}) is False
    out = buf.getvalue()
    assert "crowe-logic login" in out
    assert "crowe-logic init --node" in out
    assert "Welcome to Crowe Logic" in out
    assert "crowelogic.com/docs/cli/getting-started" in out


def test_none_state_bootstraps_anonymous(monkeypatch):
    from io import StringIO
    from rich.console import Console

    calls = {}

    monkeypatch.setattr(
        first_run, "_bootstrap_anonymous",
        lambda: calls.setdefault("registered", {"token": "crowe_anon_x.y", "free_model": "crowelm-mycelium", "daily_turn_cap": 20}),
    )
    state = {}
    console = Console(file=StringIO(), width=100)
    assert first_run.ensure_first_run(console, session_state=state) is True
    assert state["anon_device_token"] == "crowe_anon_x.y"
    assert state["anon_free_model"] == "crowelm-mycelium"


def test_none_state_degrades_to_card_when_gateway_down(monkeypatch):
    from io import StringIO
    from rich.console import Console

    def boom():
        raise OSError("network down")

    monkeypatch.setattr(first_run, "_bootstrap_anonymous", boom)
    buf = StringIO()
    console = Console(file=buf, width=100)
    assert first_run.ensure_first_run(console, session_state={}) is False
    assert "crowe-logic login" in buf.getvalue()


def test_init_node_writes_template(tmp_path, monkeypatch):
    target = tmp_path / ".crowe-logic.env"
    path = first_run.scaffold_node_env(str(target))
    assert path == str(target)
    text = target.read_text()
    assert "CROWE_LOGIC_AUTO_ROUTE=1" in text
    assert "CROWE_OPEN_API_KEY=" in text
    assert "set -a" in text  # sourcing instructions present
    # Key NAMES only - never values.
    for line in text.splitlines():
        if "=" in line and not line.startswith("#"):
            assert line.endswith("=") or line.endswith("=1")
    assert oct(target.stat().st_mode & 0o777) == "0o600"


def test_init_node_refuses_overwrite(tmp_path):
    target = tmp_path / ".crowe-logic.env"
    target.write_text("existing")
    with pytest.raises(FileExistsError):
        first_run.scaffold_node_env(str(target))
    assert target.read_text() == "existing"
