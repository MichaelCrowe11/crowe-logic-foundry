from tools import crowe_terminal as ct


def test_auth_key_prefers_env(monkeypatch, tmp_path):
    monkeypatch.setenv("WAVETERM_AUTH_KEY", "env-key")
    token = tmp_path / "agent-authkey"
    token.write_text("file-key")
    monkeypatch.setattr(ct, "_TOKEN_FILE", token)
    assert ct._auth_key() == "env-key"


def test_auth_key_falls_back_to_token_file(monkeypatch, tmp_path):
    monkeypatch.delenv("WAVETERM_AUTH_KEY", raising=False)
    token = tmp_path / "agent-authkey"
    token.write_text("file-key\n")
    monkeypatch.setattr(ct, "_TOKEN_FILE", token)
    assert ct._auth_key() == "file-key"


def test_auth_key_none_when_absent(monkeypatch, tmp_path):
    monkeypatch.delenv("WAVETERM_AUTH_KEY", raising=False)
    monkeypatch.setattr(ct, "_TOKEN_FILE", tmp_path / "missing")
    assert ct._auth_key() is None


def test_headers_include_authkey_from_file(monkeypatch, tmp_path):
    monkeypatch.delenv("WAVETERM_AUTH_KEY", raising=False)
    token = tmp_path / "agent-authkey"
    token.write_text("file-key")
    monkeypatch.setattr(ct, "_TOKEN_FILE", token)
    headers = ct._headers()
    assert headers["X-AuthKey"] == "file-key"
    assert headers["Content-Type"] == "application/json"


def test_headers_omit_authkey_when_absent(monkeypatch, tmp_path):
    monkeypatch.delenv("WAVETERM_AUTH_KEY", raising=False)
    monkeypatch.setattr(ct, "_TOKEN_FILE", tmp_path / "missing")
    assert "X-AuthKey" not in ct._headers()


def test_discover_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("CROWE_AGENT_TOOLS", raising=False)
    assert ct.discover_and_register() == []


def test_auth_key_none_for_whitespace_file(monkeypatch, tmp_path):
    monkeypatch.delenv("WAVETERM_AUTH_KEY", raising=False)
    token = tmp_path / "agent-authkey"
    token.write_text("   \n")
    monkeypatch.setattr(ct, "_TOKEN_FILE", token)
    assert ct._auth_key() is None
