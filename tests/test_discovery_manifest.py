"""Discovery manifests: agents crawl /.well-known/x402 + /.well-known/agent to
learn what Crowe services exist and what they cost, with no human in the loop."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from control_plane import agent_gateway, x402


def _client():
    app = FastAPI()
    app.include_router(agent_gateway.router)
    return TestClient(app)


def test_x402_manifest_lists_priced_endpoints():
    r = _client().get("/.well-known/x402")
    assert r.status_code == 200
    body = r.json()
    assert body["x402Version"] == 1
    assert body["unit"] == "micro-usd"
    entry = next(e for e in body["resources"] if e["resource"] == "/api/agent/v1/chat")
    assert entry["pricing"] == "per-model"
    assert "crowe-credit" in entry["schemes"]
    # per-model prices are advertised so an agent learns frontier != nano cost
    assert body["models"]["claude-opus-4-6"] == x402.price_for_model("claude-opus-4-6")
    assert body["models"]["claude-opus-4-6"] > body["models"]["FW-GLM-5"]


def test_x402_manifest_omits_chain_scheme_without_treasury(monkeypatch):
    monkeypatch.delenv("X402_BASE_PAYTO", raising=False)
    r = _client().get("/.well-known/x402")
    entry = r.json()["resources"][0]
    assert entry["schemes"] == ["crowe-credit"]  # no fake on-chain advertised

    monkeypatch.setenv("X402_BASE_PAYTO", "0x1111111111111111111111111111111111111111")
    r2 = _client().get("/.well-known/x402")
    assert "exact" in r2.json()["resources"][0]["schemes"]


def test_agent_card_describes_service():
    r = _client().get("/.well-known/agent")
    assert r.status_code == 200
    body = r.json()
    assert body["name"]
    assert body["payments"]["protocol"] == "x402"
    assert "/api/agent/v1/chat" in [s["resource"] for s in body["payments"]["priced"]]
