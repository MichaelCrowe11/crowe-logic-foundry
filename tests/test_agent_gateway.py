import base64
import json

import httpx
import pytest
from fastapi import FastAPI

from control_plane import agent_gateway, agent_wallets, settlement, x402
from control_plane.db import get_db
from control_plane.preview import SqliteDatabase


@pytest.fixture
def db():
    return SqliteDatabase(":memory:")


@pytest.fixture
def app(db, monkeypatch):
    monkeypatch.setattr(
        agent_gateway,
        "resolve_agent_principal",
        lambda authorization: {
            "principal": "crowe-agent",
            "client_id": "agent-1",
            "workspace_id": "agent-1",
            "user_id": "svc",
            "plan_id": "pro",
            "subject": "service-account-agent-1",
        },
    )

    async def _fake_call(**kwargs):
        return ("hello from agent", 3, 5)

    monkeypatch.setattr(agent_gateway, "call_model", _fake_call)

    application = FastAPI()
    application.include_router(agent_gateway.router)
    application.dependency_overrides[get_db] = lambda: db
    return application


def _aclient(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    )


def _stub_facilitator(monkeypatch):
    class _Fac:
        async def verify_and_settle(self, payload, requirements, *, price):
            return settlement.Receipt(
                id=payload["nonce"], scheme="exact", amount=price, tx_ref="0xabc"
            )

    monkeypatch.setattr(settlement, "get_facilitator", lambda: _Fac())


BODY = {"model": "crowelm-apex", "messages": [{"role": "user", "content": "hi"}]}


@pytest.mark.asyncio
async def test_unfunded_call_returns_402(app):
    async with _aclient(app) as c:
        r = await c.post(
            "/api/agent/v1/chat", headers={"Authorization": "Bearer x"}, json=BODY
        )
    assert r.status_code == 402
    body = r.json()
    assert body["x402Version"] == 1
    assert "crowe-credit" in {a["scheme"] for a in body["accepts"]}


@pytest.mark.asyncio
async def test_prepaid_balance_serves_and_debits(app, db):
    await agent_wallets.ensure_wallet(db, "agent-1")
    await agent_wallets.credit(
        db,
        "agent-1",
        1000,
        receipt_id="seed",
        scheme="crowe-credit",
        resource="/seed",
        tx_ref=None,
    )
    async with _aclient(app) as c:
        r = await c.post(
            "/api/agent/v1/chat", headers={"Authorization": "Bearer x"}, json=BODY
        )
    assert r.status_code == 200
    assert r.json()["content"] == "hello from agent"
    row = await agent_wallets.ensure_wallet(db, "agent-1")
    assert row["balance"] == 1000 - x402.price_for("/api/agent/v1/chat")


@pytest.mark.asyncio
async def test_onchain_payment_serves(app, db, monkeypatch):
    _stub_facilitator(monkeypatch)
    price = x402.price_for("/api/agent/v1/chat")
    payload = {
        "scheme": "exact",
        "nonce": "n1",
        "amount": price,
        "resource": "/api/agent/v1/chat",
    }
    hdr = base64.b64encode(json.dumps(payload).encode()).decode()
    async with _aclient(app) as c:
        r = await c.post(
            "/api/agent/v1/chat",
            headers={"Authorization": "Bearer x", "X-PAYMENT": hdr},
            json=BODY,
        )
    assert r.status_code == 200
    assert "X-PAYMENT-RESPONSE" in r.headers
    row = await agent_wallets.ensure_wallet(db, "agent-1")
    assert row["balance"] == 0  # credited price, debited price


@pytest.mark.asyncio
async def test_replayed_payment_rejected(app, db, monkeypatch):
    _stub_facilitator(monkeypatch)
    price = x402.price_for("/api/agent/v1/chat")
    payload = {
        "scheme": "exact",
        "nonce": "dup",
        "amount": price,
        "resource": "/api/agent/v1/chat",
    }
    hdr = base64.b64encode(json.dumps(payload).encode()).decode()
    async with _aclient(app) as c:
        first = await c.post(
            "/api/agent/v1/chat",
            headers={"Authorization": "Bearer x", "X-PAYMENT": hdr},
            json=BODY,
        )
        assert first.status_code == 200
        second = await c.post(
            "/api/agent/v1/chat",
            headers={"Authorization": "Bearer x", "X-PAYMENT": hdr},
            json=BODY,
        )
    assert second.status_code == 402  # replayed nonce rejected
