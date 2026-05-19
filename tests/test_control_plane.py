"""
Tests for the Control Plane API.

Uses a lightweight in-memory mock DB so tests run without Postgres.
"""

import asyncio
from datetime import datetime, timezone

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
                MockRecord(id="personal", display_name="Personal",
                           max_seats=1, max_concurrent_sessions=1,
                           max_ide_hours_month=0, vision_quota_month=10,
                           storage_limit_gb=1, notebook_quota_month=0,
                           agent_jobs_month=100, token_budget_month=750000,
                           audit_retention_days=30,
                           features={"ide_enabled": False, "byok": True}),
                MockRecord(id="pro", display_name="Pro",
                           max_seats=1, max_concurrent_sessions=2,
                           max_ide_hours_month=100, vision_quota_month=500,
                           storage_limit_gb=10, notebook_quota_month=50,
                           agent_jobs_month=500, token_budget_month=3000000,
                           audit_retention_days=90,
                           features={"ide_enabled": True, "byok": True}),
                MockRecord(id="team", display_name="Team",
                           max_seats=25, max_concurrent_sessions=5,
                           max_ide_hours_month=500, vision_quota_month=5000,
                           storage_limit_gb=100, notebook_quota_month=500,
                           agent_jobs_month=5000, token_budget_month=15000000,
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
            "workspace_credits": [],
            "credit_transactions": [],
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
                    rec["plan_id"] = ws["plan_id"] if ws else "personal"
                    rec["ws_status"] = ws["status"] if ws else "active"
                    return rec
            return None
        if "from workspace_credits where workspace_id" in q:
            return next((r for r in self.tables["workspace_credits"] if r["workspace_id"] == args[0]), None)
        if q.startswith("update workspace_credits") and "returning balance" in q:
            for row in self.tables["workspace_credits"]:
                if row["workspace_id"] == args[0] and row["balance"] >= args[1] and row.get("active", True):
                    row["balance"] -= args[1]
                    return MockRecord(balance=row["balance"])
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
        if "from api_keys" in q and "workspace_id" in q:
            workspace_id = args[0]
            return [k for k in self.tables["api_keys"] if k["workspace_id"] == workspace_id]
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
            name = args[2] if len(args) > 2 else "Default"
            slug = args[3] if len(args) > 3 else "default"
            ws_type = args[4] if len(args) > 4 else "personal"
            plan_id = args[5] if len(args) > 5 else "personal"
            self.tables["workspaces"].append(MockRecord(
                id=args[0], org_id=args[1], name=name, slug=slug,
                ws_type=ws_type, plan_id=plan_id, status="active",
            ))
        elif q.startswith("insert into api_keys"):
            self.tables["api_keys"].append(MockRecord(
                id=args[0], workspace_id=args[1], user_id=args[2],
                key_hash=args[3], key_prefix=args[4], label=args[5],
                scopes=args[6], revoked=False, last_used_at=None,
            ))
        elif q.startswith("insert into usage_events"):
            self.tables["usage_events"].append(MockRecord(
                workspace_id=args[0], user_id=args[1],
                event_type=args[2], quantity=args[3], model=args[4] if len(args) > 4 else None,
            ))
        elif q.startswith("insert into workspace_credits"):
            tier_key = args[1] if len(args) > 1 else "personal"
            balance = args[2] if len(args) > 2 else 0
            reset_at = args[3] if len(args) > 3 else None
            existing = next((r for r in self.tables["workspace_credits"] if r["workspace_id"] == args[0]), None)
            if existing:
                existing["tier_key"] = tier_key
                existing["balance"] = balance
                existing["allocation"] = balance
                existing["active"] = True
            else:
                self.tables["workspace_credits"].append(MockRecord(
                    workspace_id=args[0], tier_key=tier_key, balance=balance,
                    allocation=balance, reset_at=reset_at,
                    active=True,
                ))
        elif q.startswith("insert into credit_transactions"):
            self.tables["credit_transactions"].append(MockRecord(
                workspace_id=args[0], amount=args[1],
                reason=args[2] if len(args) > 2 else "unknown",
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
        assert r.json()["version"] == "0.2.8"


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

    def test_me(self, client):
        reg = client.post("/api/auth/register", json={
            "email": "mike@crowelogic.com", "password": "pass123", "display_name": "Mike",
        })
        token = reg.json()["access_token"]
        r = client.get("/api/auth/me", headers={
            "Authorization": f"Bearer {token}",
        })
        assert r.status_code == 200
        assert r.json()["email"] == "mike@crowelogic.com"
        assert r.json()["display_name"] == "Mike"


class TestPlans:
    def test_list_plans(self, client):
        r = client.get("/api/plans")
        assert r.status_code == 200
        plans = r.json()
        assert len(plans) == 4
        ids = [p["id"] for p in plans]
        assert "personal" in ids
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
        assert workspaces[0]["plan_id"] == "personal"

    def test_create_workspace(self, client):
        reg = client.post("/api/auth/register", json={
            "email": "mike@crowelogic.com", "password": "pass123",
        })
        token = reg.json()["access_token"]
        r = client.post("/api/workspaces", json={
            "name": "Discovery Lab",
            "slug": "discovery-lab",
            "ws_type": "team",
            "plan_id": "pro",
        }, headers={
            "Authorization": f"Bearer {token}",
        })
        assert r.status_code == 201
        data = r.json()
        assert data["name"] == "Discovery Lab"
        assert data["plan_id"] == "pro"


class TestApiKeys:
    def test_create_and_list_api_keys(self, client, mock_db):
        reg = client.post("/api/auth/register", json={
            "email": "mike@crowelogic.com", "password": "pass123",
        })
        token = reg.json()["access_token"]
        ws_id = mock_db.tables["workspaces"][0]["id"]

        created = client.post(
            f"/api/workspaces/{ws_id}/keys",
            json={"label": "dashboard", "scopes": ["chat", "vision"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert created.status_code == 201
        created_data = created.json()
        assert created_data["key"].startswith("crowe_pat_")
        assert created_data["label"] == "dashboard"

        listed = client.get(
            f"/api/workspaces/{ws_id}/keys",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert listed.status_code == 200
        data = listed.json()
        assert len(data) == 1
        assert data[0]["key_prefix"] == created_data["key_prefix"]
        assert data[0]["label"] == "dashboard"
        assert data[0]["scopes"] == ["chat", "vision"]

    def test_pat_can_read_and_consume_workspace_credits(self, client, mock_db):
        reg = client.post("/api/auth/register", json={
            "email": "mike@crowelogic.com", "password": "pass123",
        })
        token = reg.json()["access_token"]
        ws_id = mock_db.tables["workspaces"][0]["id"]

        created = client.post(
            f"/api/workspaces/{ws_id}/keys",
            json={"label": "vscode", "scopes": ["chat"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        pat = created.json()["key"]
        mock_db.tables["workspace_credits"].append(MockRecord(
            workspace_id=ws_id, tier_key="personal",
            balance=10, allocation=10, reset_at=None, active=True,
        ))

        status = client.get(
            f"/api/workspaces/{ws_id}/credits",
            headers={"Authorization": f"Bearer {pat}"},
        )
        assert status.status_code == 200
        assert status.json()["balance"] == 10

        consumed = client.post(
            f"/api/workspaces/{ws_id}/credits/consume",
            json={"amount": 3, "reason": "turn", "model_label": "CroweLM"},
            headers={"Authorization": f"Bearer {pat}"},
        )
        assert consumed.status_code == 200
        assert consumed.json()["balance"] == 7

    def test_pat_cannot_consume_another_workspace(self, client, mock_db):
        reg = client.post("/api/auth/register", json={
            "email": "mike@crowelogic.com", "password": "pass123",
        })
        token = reg.json()["access_token"]
        ws_id = mock_db.tables["workspaces"][0]["id"]
        created = client.post(
            f"/api/workspaces/{ws_id}/keys",
            json={"label": "vscode", "scopes": ["chat"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        pat = created.json()["key"]

        denied = client.post(
            "/api/workspaces/not-this-workspace/credits/consume",
            json={"amount": 1},
            headers={"Authorization": f"Bearer {pat}"},
        )
        assert denied.status_code == 403


class TestResearchEndpoint:
    def _setup_pat_with_credits(self, client, mock_db, balance=100):
        reg = client.post("/api/auth/register", json={
            "email": "mike@crowelogic.com", "password": "pass123",
        })
        token = reg.json()["access_token"]
        ws_id = mock_db.tables["workspaces"][0]["id"]
        created = client.post(
            f"/api/workspaces/{ws_id}/keys",
            json={"label": "research", "scopes": ["research"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        pat = created.json()["key"]
        mock_db.tables["workspace_credits"].append(MockRecord(
            workspace_id=ws_id, tier_key="personal",
            balance=balance, allocation=balance, reset_at=None, active=True,
        ))
        return ws_id, pat

    def test_research_debits_credits_and_returns_report(self, client, mock_db, monkeypatch):
        from control_plane._research_engine.models import (
            Report, Source, SourceTier, Usage, StageUsage,
        )

        async def fake_research(question, *, depth="normal", budget_usd=None):
            return Report(
                question=question,
                body_markdown="# Answer\nMinimum wage in 2023 was $7.25/hr federal.",
                sources=[Source(
                    id="s1", url="https://dol.gov/x", title="DOL",
                    accessed_at=datetime.now(timezone.utc), tier=SourceTier.PRIMARY,
                )],
                contradictions=[],
                confidence_gaps=[],
                usage=Usage(
                    stages=[StageUsage(
                        stage="decompose", model="claude-sonnet-4-6",
                        input_tokens=100, output_tokens=50,
                        cache_read_tokens=0, cache_creation_tokens=0,
                        cost_usd=0.001, duration_seconds=0.5,
                    )],
                    total_cost_usd=0.001, total_duration_seconds=0.5,
                ),
            )

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
        monkeypatch.setattr("control_plane._research_engine.research", fake_research)

        ws_id, pat = self._setup_pat_with_credits(client, mock_db, balance=100)

        r = client.post(
            "/api/research",
            json={"workspace_id": ws_id, "question": "What was the federal min wage in 2023?", "depth": "quick"},
            headers={"Authorization": f"Bearer {pat}"},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["depth"] == "quick"
        assert data["credits_consumed"] == 5
        assert data["balance_remaining"] == 95
        assert data["report"]["body_markdown"].startswith("# Answer")
        assert len(data["report"]["sources"]) == 1

    def test_research_rejects_insufficient_credits(self, client, mock_db, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
        ws_id, pat = self._setup_pat_with_credits(client, mock_db, balance=2)
        r = client.post(
            "/api/research",
            json={"workspace_id": ws_id, "question": "x?", "depth": "quick"},
            headers={"Authorization": f"Bearer {pat}"},
        )
        assert r.status_code == 402
        assert "Insufficient credits" in r.json()["detail"]

    def test_research_503_when_key_unset_does_not_debit(self, client, mock_db, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        ws_id, pat = self._setup_pat_with_credits(client, mock_db, balance=100)
        r = client.post(
            "/api/research",
            json={"workspace_id": ws_id, "question": "x?", "depth": "quick"},
            headers={"Authorization": f"Bearer {pat}"},
        )
        assert r.status_code == 503
        # Balance unchanged: 503 must not charge.
        bal = client.get(
            f"/api/workspaces/{ws_id}/credits",
            headers={"Authorization": f"Bearer {pat}"},
        )
        assert bal.json()["balance"] == 100

    def test_research_rejects_unknown_depth(self, client, mock_db, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
        ws_id, pat = self._setup_pat_with_credits(client, mock_db, balance=100)
        r = client.post(
            "/api/research",
            json={"workspace_id": ws_id, "question": "x?", "depth": "encyclopedia"},
            headers={"Authorization": f"Bearer {pat}"},
        )
        assert r.status_code == 400

    def test_research_rejects_cross_workspace_pat(self, client, mock_db, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
        _ws_id, pat = self._setup_pat_with_credits(client, mock_db, balance=100)
        r = client.post(
            "/api/research",
            json={"workspace_id": "some-other-ws", "question": "x?", "depth": "quick"},
            headers={"Authorization": f"Bearer {pat}"},
        )
        assert r.status_code == 403


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
        assert data["remaining"] == 750000

    def test_ide_not_in_personal_plan(self, client, mock_db):
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


class RecordingProvisionDatabase:
    def __init__(self):
        self.fetches = []
        self.executes = []

    async def fetchrow(self, query: str, *args):
        self.fetches.append((query, args))
        return None

    async def fetch(self, query: str, *args):
        self.fetches.append((query, args))
        return []

    async def execute(self, query: str, *args):
        self.executes.append((query, args))


class TestCheckoutProvisioning:
    def test_checkout_provisions_launch_pat_and_subscription_without_workspace_customer_column(self):
        from control_plane import _provision_from_checkout

        db = RecordingProvisionDatabase()
        asyncio.run(_provision_from_checkout(db, {
            "id": "cs_test_launch",
            "customer_email": "buyer@example.com",
            "customer": "cus_123",
            "subscription": "sub_123",
            "metadata": {"tier_key": "studio"},
        }))

        workspace_writes = [
            query
            for query, _args in db.executes
            if "workspaces" in query.lower()
        ]
        assert workspace_writes
        assert all("stripe_customer_id" not in query for query in workspace_writes)

        workspace_insert = next(
            args for query, args in db.executes
            if "insert into workspaces" in query.lower()
        )
        assert workspace_insert[2] == "pro"
        assert workspace_insert[3] == "sub_123"

        subscription_insert = [
            args for query, args in db.executes
            if "insert into subscriptions" in query.lower()
        ]
        assert subscription_insert
        assert subscription_insert[0][2] == "pro"
        assert subscription_insert[0][3] == "sub_123"

        provision_insert = next(
            args for query, args in db.executes
            if "insert into checkout_provisions" in query.lower()
        )
        assert provision_insert[2] == "pro"
        assert provision_insert[4].startswith("crowe_pat_")
