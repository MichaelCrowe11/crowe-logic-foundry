"""A charged agent call whose upstream provider fails must be refunded — the agent
should never pay for a completion it didn't receive."""

import httpx
import pytest
from fastapi import FastAPI

from control_plane import agent_gateway, agent_wallets, x402
from control_plane.db import get_db
from control_plane.preview import SqliteDatabase

BODY = {"model": "crowelm-apex", "messages": [{"role": "user", "content": "hi"}]}


@pytest.fixture
def db():
    return SqliteDatabase(":memory:")


def _agent_app(db, monkeypatch, call_impl):
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
    monkeypatch.setattr(agent_gateway, "call_model", call_impl)
    app = FastAPI()
    app.include_router(agent_gateway.router)
    app.dependency_overrides[get_db] = lambda: db
    return app


@pytest.mark.asyncio
async def test_refund_primitive_restores_balance(db):
    await agent_wallets.ensure_wallet(db, "agent-1")
    await agent_wallets.credit(
        db,
        "agent-1",
        100,
        receipt_id="r",
        scheme="crowe-credit",
        resource="/s",
        tx_ref=None,
    )
    await agent_wallets.debit(db, "agent-1", 40)
    new_balance = await agent_wallets.refund(db, "agent-1", 40)
    assert new_balance == 100


@pytest.mark.asyncio
async def test_provider_failure_refunds_the_charge(db, monkeypatch):
    async def _boom(**kwargs):
        raise RuntimeError("provider exploded")

    app = _agent_app(db, monkeypatch, _boom)
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
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post(
            "/api/agent/v1/chat", headers={"Authorization": "Bearer x"}, json=BODY
        )
    assert r.status_code >= 500  # provider failure surfaced, not a silent success
    row = await agent_wallets.ensure_wallet(db, "agent-1")
    assert row["balance"] == 1000  # charge fully refunded — agent paid nothing


@pytest.mark.asyncio
async def test_successful_call_still_charges(db, monkeypatch):
    async def _ok(**kwargs):
        return ("hello", 3, 5)

    app = _agent_app(db, monkeypatch, _ok)
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
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post(
            "/api/agent/v1/chat", headers={"Authorization": "Bearer x"}, json=BODY
        )
    assert r.status_code == 200
    row = await agent_wallets.ensure_wallet(db, "agent-1")
    assert row["balance"] == 1000 - x402.price_for(
        "/api/agent/v1/chat"
    )  # charged once, no refund
