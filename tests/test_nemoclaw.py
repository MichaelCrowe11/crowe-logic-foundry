"""Unit tests for tools/nemoclaw.py.

Covers the full surface of nemoclaw_shell and nemoclaw_health without
requiring a live Brev VM. httpx is patched at the module level so every
network path is exercised in isolation.
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

import tools.nemoclaw as nemoclaw


# ---- Fixtures ------------------------------------------------------------

@pytest.fixture
def clean_env(monkeypatch):
    """Strip all NEMOCLAW_* vars so each test starts from a known state."""
    for key in list(os.environ):
        if key.startswith("NEMOCLAW_"):
            monkeypatch.delenv(key, raising=False)
    return monkeypatch


@pytest.fixture
def configured_env(clean_env):
    """Minimal env that satisfies the happy path."""
    clean_env.setenv("NEMOCLAW_ENDPOINT", "https://fake.brevlab.com")
    clean_env.setenv("NEMOCLAW_API_KEY", "test-token")
    return clean_env


class _MockResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text or json.dumps(self._json)

    def json(self):
        return self._json


class _MockClient:
    """Stand-in for httpx.Client that records calls and returns canned data."""

    def __init__(self, response=None, raise_on_request=None):
        self.response = response or _MockResponse()
        self.raise_on_request = raise_on_request
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, headers=None):
        self.calls.append(("GET", url, headers, None))
        if self.raise_on_request:
            raise self.raise_on_request
        return self.response

    def post(self, url, headers=None, json=None):
        self.calls.append(("POST", url, headers, json))
        if self.raise_on_request:
            raise self.raise_on_request
        return self.response


# ---- nemoclaw_health -----------------------------------------------------

def test_health_returns_error_when_endpoint_unset(clean_env):
    result = json.loads(nemoclaw.nemoclaw_health())
    assert result["reachable"] is False
    assert "NEMOCLAW_SANDBOX_URL" in result["error"]


def test_health_reports_reachable_on_200(configured_env):
    client = _MockClient(_MockResponse(status_code=200, text="ok"))
    with patch.object(nemoclaw, "httpx") as mock_httpx:
        mock_httpx.Client.return_value = client
        result = json.loads(nemoclaw.nemoclaw_health())
    assert result["reachable"] is True
    assert result["status_code"] == 200
    assert client.calls[0][1].endswith(nemoclaw.DEFAULT_HEALTH_PATH)


def test_health_honors_custom_health_path(configured_env):
    configured_env.setenv("NEMOCLAW_SANDBOX_HEALTH_PATH", "/healthz")
    client = _MockClient(_MockResponse(status_code=204))
    with patch.object(nemoclaw, "httpx") as mock_httpx:
        mock_httpx.Client.return_value = client
        result = json.loads(nemoclaw.nemoclaw_health())
    assert result["status_code"] == 204
    assert client.calls[0][1].endswith("/healthz")


def test_health_marks_unreachable_on_5xx(configured_env):
    client = _MockClient(_MockResponse(status_code=502))
    with patch.object(nemoclaw, "httpx") as mock_httpx:
        mock_httpx.Client.return_value = client
        result = json.loads(nemoclaw.nemoclaw_health())
    assert result["reachable"] is False
    assert result["status_code"] == 502


def test_health_handles_transport_exception(configured_env):
    class BoomError(Exception):
        pass
    client = _MockClient(raise_on_request=BoomError("dns failure"))
    with patch.object(nemoclaw, "httpx") as mock_httpx:
        mock_httpx.Client.return_value = client
        result = json.loads(nemoclaw.nemoclaw_health())
    assert result["reachable"] is False
    assert "BoomError" in result["error"]


# ---- nemoclaw_shell ------------------------------------------------------

def test_shell_returns_error_when_endpoint_unset(clean_env):
    result = json.loads(nemoclaw.nemoclaw_shell("echo hi"))
    assert result["return_code"] == -1
    assert "NEMOCLAW_SANDBOX_URL" in result["error"]


def test_shell_happy_path_returns_stdout_and_return_code(configured_env):
    response = _MockResponse(
        status_code=200,
        json_data={"stdout": "hello\n", "stderr": "", "return_code": 0},
    )
    client = _MockClient(response)
    with patch.object(nemoclaw, "httpx") as mock_httpx:
        mock_httpx.Client.return_value = client
        result = json.loads(nemoclaw.nemoclaw_shell("echo hello"))
    assert result["stdout"] == "hello\n"
    assert result["return_code"] == 0
    assert result["sandbox"] == "nemoclaw"
    method, url, headers, payload = client.calls[0]
    assert method == "POST"
    assert url.endswith(nemoclaw.DEFAULT_EXEC_PATH)
    assert payload["command"] == "echo hello"


def test_shell_accepts_legacy_output_and_exit_code_fields(configured_env):
    """Older OpenShell builds use output/exit_code instead of stdout/return_code."""
    response = _MockResponse(
        status_code=200,
        json_data={"output": "legacy body", "exit_code": 2},
    )
    client = _MockClient(response)
    with patch.object(nemoclaw, "httpx") as mock_httpx:
        mock_httpx.Client.return_value = client
        result = json.loads(nemoclaw.nemoclaw_shell("whoami"))
    assert result["stdout"] == "legacy body"
    assert result["return_code"] == 2


def test_shell_clamps_timeout_to_max_600(configured_env):
    response = _MockResponse(json_data={"stdout": "", "return_code": 0})
    client = _MockClient(response)
    with patch.object(nemoclaw, "httpx") as mock_httpx:
        mock_httpx.Client.return_value = client
        nemoclaw.nemoclaw_shell("sleep 1", timeout_seconds=9999)
    assert client.calls[0][3]["timeout_seconds"] == 600


def test_shell_clamps_timeout_to_min_1(configured_env):
    response = _MockResponse(json_data={"stdout": "", "return_code": 0})
    client = _MockClient(response)
    with patch.object(nemoclaw, "httpx") as mock_httpx:
        mock_httpx.Client.return_value = client
        nemoclaw.nemoclaw_shell("echo x", timeout_seconds=0)
    assert client.calls[0][3]["timeout_seconds"] == 1


def test_shell_forwards_working_directory(configured_env):
    response = _MockResponse(json_data={"stdout": "", "return_code": 0})
    client = _MockClient(response)
    with patch.object(nemoclaw, "httpx") as mock_httpx:
        mock_httpx.Client.return_value = client
        nemoclaw.nemoclaw_shell("pwd", working_directory="/workspace")
    assert client.calls[0][3]["working_directory"] == "/workspace"


def test_shell_omits_working_directory_when_empty(configured_env):
    response = _MockResponse(json_data={"stdout": "", "return_code": 0})
    client = _MockClient(response)
    with patch.object(nemoclaw, "httpx") as mock_httpx:
        mock_httpx.Client.return_value = client
        nemoclaw.nemoclaw_shell("pwd")
    assert "working_directory" not in client.calls[0][3]


def test_shell_surfaces_404_with_recon_hint(configured_env):
    """A 404 on the exec path is the single most common NemoClaw setup error."""
    response = _MockResponse(status_code=404, text="not found")
    client = _MockClient(response)
    with patch.object(nemoclaw, "httpx") as mock_httpx:
        mock_httpx.Client.return_value = client
        result = json.loads(nemoclaw.nemoclaw_shell("echo hi"))
    assert result["return_code"] == -1
    assert "404" in result["error"]
    assert "scripts/nemoclaw_recon.sh" in result["error"]


def test_shell_surfaces_generic_4xx_with_body(configured_env):
    response = _MockResponse(status_code=401, text="unauthorized")
    client = _MockClient(response)
    with patch.object(nemoclaw, "httpx") as mock_httpx:
        mock_httpx.Client.return_value = client
        result = json.loads(nemoclaw.nemoclaw_shell("echo hi"))
    assert result["return_code"] == -1
    assert "401" in result["error"]
    assert result["body"] == "unauthorized"


def test_shell_handles_non_json_response(configured_env):
    class _ExplodingJsonResponse(_MockResponse):
        def json(self):
            raise ValueError("not json")
    client = _MockClient(_ExplodingJsonResponse(status_code=200, text="<html>"))
    with patch.object(nemoclaw, "httpx") as mock_httpx:
        mock_httpx.Client.return_value = client
        result = json.loads(nemoclaw.nemoclaw_shell("echo hi"))
    assert result["return_code"] == -1
    assert "non-JSON" in result["error"]


def test_shell_truncates_large_stdout(configured_env):
    huge = "x" * 60000
    response = _MockResponse(json_data={"stdout": huge, "return_code": 0})
    client = _MockClient(response)
    with patch.object(nemoclaw, "httpx") as mock_httpx:
        mock_httpx.Client.return_value = client
        result = json.loads(nemoclaw.nemoclaw_shell("cat bigfile"))
    assert len(result["stdout"]) < len(huge)
    assert "truncated" in result["stdout"]


def test_shell_sends_authorization_header(configured_env):
    response = _MockResponse(json_data={"stdout": "", "return_code": 0})
    client = _MockClient(response)
    with patch.object(nemoclaw, "httpx") as mock_httpx:
        mock_httpx.Client.return_value = client
        nemoclaw.nemoclaw_shell("id")
    headers = client.calls[0][2]
    assert headers["Authorization"] == "Bearer test-token"


def test_shell_omits_authorization_when_key_unset(clean_env):
    clean_env.setenv("NEMOCLAW_ENDPOINT", "https://fake.brevlab.com")
    response = _MockResponse(json_data={"stdout": "", "return_code": 0})
    client = _MockClient(response)
    with patch.object(nemoclaw, "httpx") as mock_httpx:
        mock_httpx.Client.return_value = client
        nemoclaw.nemoclaw_shell("id")
    headers = client.calls[0][2]
    assert "Authorization" not in headers


def test_shell_uses_sandbox_url_when_set(configured_env):
    configured_env.setenv("NEMOCLAW_SANDBOX_URL", "http://10.0.0.5:8081")
    response = _MockResponse(json_data={"stdout": "", "return_code": 0})
    client = _MockClient(response)
    with patch.object(nemoclaw, "httpx") as mock_httpx:
        mock_httpx.Client.return_value = client
        nemoclaw.nemoclaw_shell("id")
    url = client.calls[0][1]
    assert url.startswith("http://10.0.0.5:8081")


# ---- Tool registry wiring ------------------------------------------------

def test_nemoclaw_tools_are_registered_in_user_functions():
    from tools import user_functions
    names = {f.__name__ for f in user_functions}
    assert "nemoclaw_shell" in names
    assert "nemoclaw_health" in names


def test_talon_alias_resolves_to_nemoclaw_provider():
    """Guard against future config reshuffles silently breaking Talon."""
    from config.agent_config import resolve_model_config

    cfg = resolve_model_config("talon-nemoclaw")
    assert cfg is not None, "talon-nemoclaw alias missing from model chain"
    assert cfg["provider"] == "openai_compat"
    assert cfg["endpoint_env"] == "NEMOCLAW_SANDBOX_URL"


def test_provider_model_name_interpolates_env_vars(monkeypatch):
    """The env-var interpolation is the only way Talon picks up recon-detected model ids."""
    from config.agent_config import provider_model_name

    monkeypatch.setenv("FAKE_MODEL", "detected-by-recon")
    cfg = {"backend_name": "${FAKE_MODEL}", "name": "fallback"}
    assert provider_model_name(cfg) == "detected-by-recon"


def test_provider_model_name_leaves_literal_when_env_missing(monkeypatch):
    from config.agent_config import provider_model_name

    monkeypatch.delenv("NEVER_SET_VAR", raising=False)
    cfg = {"backend_name": "${NEVER_SET_VAR}", "name": "fallback"}
    assert provider_model_name(cfg) == "${NEVER_SET_VAR}"
