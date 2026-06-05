"""Admin top-up: fund an agent's prepaid wallet out-of-band (crowe-credit) so an
agent-paid completion can happen before on-chain settlement is wired.

Fail-closed: the route is disabled unless X402_ADMIN_TOKEN is set, and requires a
matching X-Admin-Token. Idempotent on the caller-supplied key."""

import httpx
import pytest
from fastapi import FastAPI

from control_plane import agent_gateway, agent_wallets
from control_plane.db import get_db
from control_plane.preview import SqliteDatabase


@pytest.fixture
def db():
    return SqliteDatabase(":memory:")


@pytest.fixture
def app(db):
    application = FastAPI()
    application.include_router(agent_gateway.router)
    application.dependency_overrides[get_db] = lambda: db
    return application


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    )


@pytest.mark.asyncio
async def test_topup_credits_wallet_with_valid_admin_token(app, db, monkeypatch):
    monkeypatch.setenv("X402_ADMIN_TOKEN", "s3cret-admin")
    async with _client(app) as c:
        r = await c.post(
            "/api/agent/v1/wallet/topup",
            headers={"X-Admin-Token": "s3cret-admin"},
            json={"client_id": "agent-1", "amount": 50000, "idempotency_key": "k1"},
        )
    assert r.status_code == 200
    assert r.json()["applied"] is True
    row = await agent_wallets.ensure_wallet(db, "agent-1")
    assert row["balance"] == 50000


@pytest.mark.asyncio
async def test_topup_rejects_wrong_token(app, monkeypatch):
    monkeypatch.setenv("X402_ADMIN_TOKEN", "s3cret-admin")
    async with _client(app) as c:
        r = await c.post(
            "/api/agent/v1/wallet/topup",
            headers={"X-Admin-Token": "nope"},
            json={"client_id": "agent-1", "amount": 50000, "idempotency_key": "k1"},
        )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_topup_disabled_when_unset(app, monkeypatch):
    monkeypatch.delenv("X402_ADMIN_TOKEN", raising=False)
    async with _client(app) as c:
        r = await c.post(
            "/api/agent/v1/wallet/topup",
            headers={"X-Admin-Token": "anything"},
            json={"client_id": "agent-1", "amount": 50000, "idempotency_key": "k1"},
        )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_topup_is_idempotent(app, db, monkeypatch):
    monkeypatch.setenv("X402_ADMIN_TOKEN", "s3cret-admin")
    body = {"client_id": "agent-1", "amount": 50000, "idempotency_key": "dup"}
    h = {"X-Admin-Token": "s3cret-admin"}
    async with _client(app) as c:
        first = await c.post("/api/agent/v1/wallet/topup", headers=h, json=body)
        second = await c.post("/api/agent/v1/wallet/topup", headers=h, json=body)
    assert first.json()["applied"] is True
    assert second.json()["applied"] is False  # replay: no double credit
    row = await agent_wallets.ensure_wallet(db, "agent-1")
    assert row["balance"] == 50000  # credited once


@pytest.mark.asyncio
async def test_topup_rejects_nonpositive_amount(app, monkeypatch):
    monkeypatch.setenv("X402_ADMIN_TOKEN", "s3cret-admin")
    async with _client(app) as c:
        r = await c.post(
            "/api/agent/v1/wallet/topup",
            headers={"X-Admin-Token": "s3cret-admin"},
            json={"client_id": "agent-1", "amount": 0, "idempotency_key": "k1"},
        )
    assert r.status_code == 400
