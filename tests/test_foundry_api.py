"""Tests for cli/foundry_api.py credit enforcement client."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from cli.foundry_api import (
    AccountStatus,
    CreditDecision,
    FoundryAPIClient,
    get_client,
    reset_client,
)


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    reset_client()
    for key in ("CROWE_LOGIC_API_KEY", "CROWE_LOGIC_API_URL", "CROWE_LOGIC_BYOK"):
        monkeypatch.delenv(key, raising=False)
    yield
    reset_client()


class _MockResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text

    def json(self):
        return self._json


class _MockClient:
    def __init__(self, response=None, raise_on_request=None):
        self.response = response or _MockResponse()
        self.raise_on_request = raise_on_request
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, headers=None, json=None):
        self.calls.append(("POST", url, headers, json))
        if self.raise_on_request:
            raise self.raise_on_request
        return self.response

    def get(self, url, headers=None):
        self.calls.append(("GET", url, headers, None))
        if self.raise_on_request:
            raise self.raise_on_request
        return self.response


# ---- Mode detection -----------------------------------------------------

def test_client_disabled_without_api_key():
    client = FoundryAPIClient(api_key="", byok_mode=False)
    assert client.enabled is False
    assert client.workspace_id is None


def test_client_extracts_workspace_id_from_valid_key():
    client = FoundryAPIClient(api_key="clk_ws_abc123_secretpart")
    assert client.enabled is True
    assert client.workspace_id == "ws"


def test_client_extracts_workspace_id_from_launch_pat():
    client = FoundryAPIClient(api_key="crowe_pat_wsabc123_secretpart")
    assert client.enabled is True
    assert client.workspace_id == "wsabc123"


def test_client_older_pat_without_workspace_segment_stays_disabled():
    client = FoundryAPIClient(api_key="crowe_pat_0123456789abcdef")
    assert client.enabled is False
    assert client.workspace_id is None


def test_client_without_workspace_segment_stays_disabled():
    client = FoundryAPIClient(api_key="not_a_valid_key_format")
    assert client.enabled is False


def test_byok_env_var_overrides_to_byok_mode(monkeypatch):
    monkeypatch.setenv("CROWE_LOGIC_BYOK", "1")
    monkeypatch.setenv("CROWE_LOGIC_API_KEY", "clk_ws_abc_secret")
    reset_client()
    c = get_client()
    assert c.byok_mode is True


# ---- check_and_reserve --------------------------------------------------

def test_check_and_reserve_in_byok_mode_allows_without_network():
    client = FoundryAPIClient(byok_mode=True, api_key="")
    d = client.check_and_reserve(5)
    assert d.allowed
    assert d.tier == "byok"
    # No HTTP call should be made.


def test_check_and_reserve_fails_open_without_api_key():
    client = FoundryAPIClient(api_key="")
    d = client.check_and_reserve(10)
    assert d.allowed
    assert d.via_fallback
    assert "No API key" in d.reason


def test_check_and_reserve_200_marks_allowed():
    client = FoundryAPIClient(api_key="clk_ws_abc_secret")
    mock_client = _MockClient(_MockResponse(
        200, {"balance": 495, "workspace_id": "ws", "amount_consumed": 5},
    ))
    with patch("cli.foundry_api.httpx") as mock_httpx:
        mock_httpx.Client.return_value = mock_client
        d = client.check_and_reserve(5, model_label="CroweLM Supreme")
    assert d.allowed
    assert d.balance == 495
    # Verify payload shape
    method, url, headers, payload = mock_client.calls[0]
    assert method == "POST"
    assert "/credits/consume" in url
    assert headers["Authorization"] == "Bearer clk_ws_abc_secret"
    assert payload["amount"] == 5
    assert payload["model_label"] == "CroweLM Supreme"


def test_check_and_reserve_402_denies_with_reason():
    client = FoundryAPIClient(api_key="clk_ws_abc_secret")
    mock_client = _MockClient(_MockResponse(
        402, {"detail": "Insufficient credits: balance=0"},
    ))
    with patch("cli.foundry_api.httpx") as mock_httpx:
        mock_httpx.Client.return_value = mock_client
        d = client.check_and_reserve(5)
    assert d.allowed is False
    assert "Insufficient credits" in d.reason


def test_check_and_reserve_5xx_fails_open():
    client = FoundryAPIClient(api_key="clk_ws_abc_secret")
    mock_client = _MockClient(_MockResponse(503))
    with patch("cli.foundry_api.httpx") as mock_httpx:
        mock_httpx.Client.return_value = mock_client
        d = client.check_and_reserve(5)
    assert d.allowed
    assert d.via_fallback


def test_check_and_reserve_network_error_fails_open():
    client = FoundryAPIClient(api_key="clk_ws_abc_secret")

    class NetError(Exception):
        pass

    mock_client = _MockClient(raise_on_request=NetError("dns timeout"))
    with patch("cli.foundry_api.httpx") as mock_httpx:
        mock_httpx.Client.return_value = mock_client
        d = client.check_and_reserve(5)
    assert d.allowed
    assert d.via_fallback
    assert "NetError" in d.reason


def test_check_and_reserve_amount_clamped_to_minimum_1():
    """Amount 0 should be bumped to 1 so the control plane doesn't 400."""
    client = FoundryAPIClient(api_key="clk_ws_abc_secret")
    mock_client = _MockClient(_MockResponse(200, {"balance": 100}))
    with patch("cli.foundry_api.httpx") as mock_httpx:
        mock_httpx.Client.return_value = mock_client
        client.check_and_reserve(0)
    assert mock_client.calls[0][3]["amount"] == 1


# ---- account_status -----------------------------------------------------

def test_account_status_unauthenticated_is_user_friendly():
    client = FoundryAPIClient(api_key="")
    status = client.account_status()
    assert not status.authenticated
    assert not status.byok
    assert "CROWE_LOGIC_API_KEY" in status.message


def test_account_status_byok_mode_reports_cleanly():
    client = FoundryAPIClient(byok_mode=True, api_key="clk_ws_abc_secret")
    status = client.account_status()
    assert status.byok
    assert status.tier == "byok"


def test_account_status_200_surfaces_tier_balance_reset():
    client = FoundryAPIClient(api_key="clk_ws_abc_secret")
    mock_client = _MockClient(_MockResponse(200, {
        "workspace_id": "ws",
        "tier_key": "pro",
        "balance": 2650,
        "allocation": 3000,
        "reset_at": "2026-05-22T00:00:00+00:00",
        "active": True,
    }))
    with patch("cli.foundry_api.httpx") as mock_httpx:
        mock_httpx.Client.return_value = mock_client
        status = client.account_status()
    assert status.authenticated
    assert not status.byok
    assert status.tier == "pro"
    assert status.balance == 2650
    assert status.allocation == 3000
    assert status.reset_at.startswith("2026-05-22")
    assert status.active


def test_account_status_handles_network_error():
    client = FoundryAPIClient(api_key="clk_ws_abc_secret")

    class NetError(Exception):
        pass

    mock_client = _MockClient(raise_on_request=NetError("offline"))
    with patch("cli.foundry_api.httpx") as mock_httpx:
        mock_httpx.Client.return_value = mock_client
        status = client.account_status()
    assert status.authenticated is True
    assert status.active is False
    assert "Control plane unreachable" in status.message
