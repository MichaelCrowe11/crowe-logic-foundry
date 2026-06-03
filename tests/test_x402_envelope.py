import base64
import json

import pytest

from control_plane import x402


def test_price_catalog_has_slice1_endpoint():
    assert x402.price_for("/api/agent/v1/chat") > 0


def test_unknown_resource_raises():
    with pytest.raises(KeyError):
        x402.price_for("/nope")


def test_envelope_credit_scheme_always_present():
    env = x402.build_payment_required("/api/agent/v1/chat")
    assert env["x402Version"] == 1
    schemes = {a["scheme"] for a in env["accepts"]}
    assert "crowe-credit" in schemes
    for a in env["accepts"]:
        assert a["resource"] == "/api/agent/v1/chat"
        assert int(a["maxAmountRequired"]) == x402.price_for("/api/agent/v1/chat")


def test_chain_scheme_only_when_real_treasury_configured(monkeypatch):
    monkeypatch.delenv("X402_BASE_PAYTO", raising=False)
    env = x402.build_payment_required("/api/agent/v1/chat")
    assert "exact" not in {
        a["scheme"] for a in env["accepts"]
    }  # no fake address advertised

    monkeypatch.setenv("X402_BASE_PAYTO", "0x1111111111111111111111111111111111111111")
    env2 = x402.build_payment_required("/api/agent/v1/chat")
    chain = [a for a in env2["accepts"] if a["scheme"] == "exact"]
    assert len(chain) == 1
    assert chain[0]["payTo"] == "0x1111111111111111111111111111111111111111"
    assert chain[0]["asset"] == "USDC"
    assert chain[0]["network"] == "base"


def test_parse_x_payment_roundtrip():
    payload = {
        "scheme": "crowe-credit",
        "nonce": "n1",
        "resource": "/api/agent/v1/chat",
        "amount": 50,
        "grant": "abc",
    }
    header = base64.b64encode(json.dumps(payload).encode()).decode()
    assert x402.parse_x_payment(header) == payload


def test_parse_x_payment_rejects_garbage():
    with pytest.raises(ValueError):
        x402.parse_x_payment("!!!not-base64-json!!!")
