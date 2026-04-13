"""
Tests for the Control Plane API.

Uses a lightweight in-memory mock DB so tests run without Postgres.
"""

import hashlib
import json
import secrets
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest


# ─── Mock DB ──────────────────────────────────────────────────────────

class MockRecord(dict):
    """dict subclass that supports attribute-style access like asyncpg.Record."""
    def __getitem__(self, key):
        return super().__getitem__(key)


class MockDatabase:
    """In-memory store that mimics control_plane.db.Database."""

    def __init__(self):
        self.tables = {
            "users": [],
            "organizations": [],
            "org_members": [],
            "plans": [
                MockRecord(id="developer", display_name="Developer",
                           max_seats=1, max_concurrent_sessions=1,
                           max_ide_hours_month=0, vision_quota_month=10,
                           storage_limit_gb=1, notebook_quota_month=0,
                           agent_jobs_month=100, token_budget_month=500000,
                           audit_retention_days=30,
                           features={"ide_enabled": False, "byok": True}),
                MockRecord(id="studio", display_name="Studio",
                           max_seats=3, max_concurrent_sessions=2,
                           max_ide_hours_month=100, vision_quota_month=500,
                           storage_limit_gb=10, notebook_quota_month=50,
                           agent_jobs_month=500, token_budget_month=5000000,
                           audit_retention_days=90,
                           features={"ide_enabled": True, "byok": True}),
                MockRecord(id="lab", display_name="Lab",
                           max_seats=10, max_concurrent_sessions=5,
                           max_ide_hours_month=500, vision_quota_month=5000,
                           storage_limit_gb=100, notebook_quota_month=500,
                           agent_jobs_month=5000, token_budget_month=50000000,
                           audit_retention_days=365,
                           features={"ide_enabled": True}),
                MockRecord(id="enterprise", display_name="Enterprise",
                           max_seats=-1, max_concurrent_sessions=-1,
                           max_ide_hours_month=-1, vision_quota_month=-1,
                           storage_limit_gb=-1, notebook_quota_month=-1,
                           agent_jobs_month=-1, token_budget_month=-1,
                           audit_retention_days=-1,
                           features={"ide_enabled": True, "sso": True}),
            ],
            "workspaces": [],
            "api_keys": [],
            "usage_events": [],
            "billing_events": [],
            "subscriptions": [],
        }

    async def fetchrow(self, query: str, *args):
        q = query.lower().strip()

        if "from users where email" in q:
            return next((r for r in self.tables["users"] if r["email"] == args[0]), None)
        if "from users where id" in q:
            return next((r for r in self.tables["users"] if r["id"] == args[0]), None)
        if "from organizations where owner_id" in q:
            return next((r for r in self.tables["organizations"] if r["owner_id"] == args[0]), None)
        if "from org_members where org_id" in q:
            return next(
                (r for r in self.tables["org_members"]
                 if r["org_id"] == args[0] and r["user_id"] == args[1]),
                None,
            )
        if "from workspaces where id" in q:
            return next((r for r in self.tables["workspaces"] if r["id"] == args[0]), None)
        if "from plans where id" in q:
            return next((r for r in self.tables["plans"] if r["id"] == args[0]), None)
        if "from api_keys" in q and "key_hash" in q:
            for k in self.tables["api_keys"]:
                if k["key_hash"] == args[0] and not k.get("revoked", False):
                    # Join workspace plan_id
                    ws = next((w for w in self.tables["workspaces"] if w["id"] == k["workspace_id"]), None)
                    rec = MockRecord(**k)
                    rec["plan_id"] = ws["plan_id"] if ws else "developer"
                    rec["ws_status"] = ws["status"] if ws else "active"
                    return rec
            return None
        if "sum(quantity)" in q:
            total = sum(
                e["quantity"] for e in self.tables["usage_events"]
                if e["workspace_id"] == args[0] and e["event_type"] == args[1]
            )
            return MockRecord(used=total)
        return None

    async def fetch(self, query: str, *args):
        q = query.lower().strip()
        if "from plans" in q:
            return self.tables["plans"]
        if "from workspaces" in q and "org_members" in q:
            user_id = args[0]
            orgs = [m["org_id"] for m in self.tables["org_members"] if m["user_id"] == user_id]
            return [w for w in self.tables["workspaces"] if w["org_id"] in orgs and w["status"] == "active"]
        if "from usage_events" in q and "group by" in q:
            ws_id = args[0]
            groups = {}
            for e in self.tables["usage_events"]:
                if e["workspace_id"] == ws_id:
                    groups.setdefault(e["event_type"], 0)
                    groups[e["event_type"]] += e["quantity"]
            return [MockRecord(event_type=k, total=v) for k, v in groups.items()]
        return []

    async def execute(self, query: str, *args):
        q = query.lower().strip()
        if q.startswith("insert into users"):
            self.tables["users"].append(MockRecord(
                id=args[0], email=args[1], display_name=args[2],
                password_hash=args[3], role="researcher",
            ))
        elif q.startswith("insert into organizations"):
            self.tables["organizations"].append(MockRecord(
                id=args[0], name=args[1], slug=args[2], owner_id=args[3],
            ))
        elif q.startswith("insert into org_members"):
            self.tables["org_members"].append(MockRecord(
                org_id=args[0], user_id=args[1], role=args[2] if len(args) > 2 else "owner",
            ))
        elif q.startswith("insert into workspaces"):
            self.tables["workspaces"].append(MockRecord(
                id=args[0], org_id=args[1], name="Default", slug="default",
                ws_type="personal", plan_id="developer", status="active",
            ))
        elif q.startswith("insert into api_keys"):
            self.tables["api_keys"].append(MockRecord(
                id=args[0], workspace_id=args[1], user_id=args[2],
                key_hash=args[3], key_prefix=args[4], label=args[5],
                scopes=args[6], revoked=False,
            ))
        elif q.startswith("insert into usage_events"):
            self.tables["usage_events"].append(MockRecord(
                workspace_id=args[0], user_id=args[1],
                event_type=args[2], quantity=args[3], model=args[4] if len(args) > 4 else None,
            ))
        elif "update api_keys set revoked" in q:
            for k in self.tables["api_keys"]:
                if k["id"] == args[0] and k["workspace_id"] == args[1]:
                    k["revoked"] = True
        elif "update api_keys set last_used_at" in q:
            pass  # no-op for tests

    async def executemany(self, query, args_list):
        for args in args_list:
            await self.execute(query, *args)


# ─── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def mock_db():
    return MockDatabase()


@pytest.fixture
def client(mock_db):
    """Create a TestClient with the mock DB injected."""
    from fastapi.testclient import TestClient
    from control_plane import app
    from control_plane.db import get_db

    async def override_get_db():
        return mock_db

    app.dependency_overrides[get_db] = override_get_db
    c = TestClient(app, raise_server_exceptions=False)
    yield c
    app.dependency_overrides.clear()


# ─── Tests ────────────────────────────────────────────────────────────

class TestHealth:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "healthy"
        assert r.json()["version"] == "0.2.5"


class TestAuth:
    def test_register(self, client):
        r = client.post("/api/auth/register", json={
            "email": "mike@crowelogic.com",
            "password": "securepass123",
            "display_name": "Mike Crowe",
        })
        assert r.status_code == 200
        data = r.json()
        assert "access_token" in data
        assert data["email"] == "mike@crowelogic.com"
        assert data["user_id"]

    def test_register_duplicate(self, client):
        client.post("/api/auth/register", json={
            "email": "mike@crowelogic.com", "password": "pass123",
        })
        r = client.post("/api/auth/register", json={
            "email": "mike@crowelogic.com", "password": "pass456",
        })
        assert r.status_code == 409

    def test_login(self, client):
        client.post("/api/auth/register", json={
            "email": "mike@crowelogic.com", "password": "securepass123",
        })
        r = client.post("/api/auth/login", json={
            "email": "mike@crowelogic.com", "password": "securepass123",
        })
        assert r.status_code == 200
        assert "access_token" in r.json()

    def test_login_wrong_password(self, client):
        client.post("/api/auth/register", json={
            "email": "mike@crowelogic.com", "password": "securepass123",
        })
        r = client.post("/api/auth/login", json={
            "email": "mike@crowelogic.com", "password": "wrongpass",
        })
        assert r.status_code == 401

    def test_refresh(self, client):
        reg = client.post("/api/auth/register", json={
            "email": "mike@crowelogic.com", "password": "pass123",
        })
        token = reg.json()["access_token"]
        r = client.post("/api/auth/refresh", headers={
            "Authorization": f"Bearer {token}",
        })
        assert r.status_code == 200
        assert "access_token" in r.json()
        assert r.json()["email"] == "mike@crowelogic.com"


class TestPlans:
    def test_list_plans(self, client):
        r = client.get("/api/plans")
        assert r.status_code == 200
        plans = r.json()
        assert len(plans) == 4
        ids = [p["id"] for p in plans]
        assert "developer" in ids
        assert "enterprise" in ids


class TestWorkspaces:
    def test_list_workspaces(self, client):
        reg = client.post("/api/auth/register", json={
            "email": "mike@crowelogic.com", "password": "pass123",
        })
        token = reg.json()["access_token"]
        r = client.get("/api/workspaces", headers={
            "Authorization": f"Bearer {token}",
        })
        assert r.status_code == 200
        workspaces = r.json()
        assert len(workspaces) == 1
        assert workspaces[0]["plan_id"] == "developer"


class TestEntitlements:
    def test_token_entitlement(self, client, mock_db):
        reg = client.post("/api/auth/register", json={
            "email": "mike@crowelogic.com", "password": "pass123",
        })
        token = reg.json()["access_token"]
        ws_id = mock_db.tables["workspaces"][0]["id"]

        r = client.get(
            f"/api/workspaces/{ws_id}/entitlements/tokens",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["allowed"] is True
        assert data["remaining"] == 500000

    def test_ide_not_in_developer_plan(self, client, mock_db):
        reg = client.post("/api/auth/register", json={
            "email": "mike@crowelogic.com", "password": "pass123",
        })
        token = reg.json()["access_token"]
        ws_id = mock_db.tables["workspaces"][0]["id"]

        r = client.get(
            f"/api/workspaces/{ws_id}/entitlements/ide",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["allowed"] is False
        assert "not included" in data["reason"]


class TestUsage:
    def test_record_and_get_usage(self, client, mock_db):
        reg = client.post("/api/auth/register", json={
            "email": "mike@crowelogic.com", "password": "pass123",
        })
        token = reg.json()["access_token"]
        ws_id = mock_db.tables["workspaces"][0]["id"]

        # Record some usage
        client.post(
            f"/api/workspaces/{ws_id}/usage?event_type=tokens&quantity=1500&model=gpt-5.4-nano",
            headers={"Authorization": f"Bearer {token}"},
        )

        r = client.get(
            f"/api/workspaces/{ws_id}/usage",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["tokens"] == 1500
