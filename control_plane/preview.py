"""
Local preview server for the Control Plane API.

Uses an in-memory SQLite backend so it runs without Postgres/Neon.
Start with:  .venv/bin/python control_plane/preview.py
Then open:   http://localhost:8001/docs
"""

import asyncio
import hashlib
import json
import secrets
import sqlite3
from contextlib import asynccontextmanager

import uvicorn


# ─── SQLite mock that quacks like asyncpg ─────────────────────────────

class _Record(dict):
    """dict with attribute access, mimicking asyncpg.Record."""
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)


class SqliteDatabase:
    """Synchronous SQLite wrapped in async methods matching control_plane.db.Database."""

    def __init__(self, path: str = ":memory:"):
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._bootstrap()

    def _bootstrap(self):
        c = self._conn
        c.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                display_name TEXT,
                password_hash TEXT,
                role TEXT DEFAULT 'researcher',
                avatar_url TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS organizations (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                slug TEXT UNIQUE NOT NULL,
                owner_id TEXT NOT NULL,
                stripe_customer_id TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS org_members (
                org_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                role TEXT DEFAULT 'researcher',
                PRIMARY KEY (org_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS plans (
                id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                stripe_price_id TEXT,
                max_seats INTEGER DEFAULT 1,
                max_concurrent_sessions INTEGER DEFAULT 1,
                max_ide_hours_month INTEGER DEFAULT 0,
                allowed_models TEXT DEFAULT '[]',
                vision_quota_month INTEGER DEFAULT 0,
                storage_limit_gb INTEGER DEFAULT 1,
                notebook_quota_month INTEGER DEFAULT 0,
                agent_jobs_month INTEGER DEFAULT 100,
                token_budget_month INTEGER DEFAULT 500000,
                audit_retention_days INTEGER DEFAULT 30,
                features TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS workspaces (
                id TEXT PRIMARY KEY,
                org_id TEXT NOT NULL,
                name TEXT NOT NULL,
                slug TEXT NOT NULL,
                ws_type TEXT DEFAULT 'personal',
                plan_id TEXT DEFAULT 'developer',
                stripe_subscription_id TEXT,
                status TEXT DEFAULT 'active',
                settings TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS api_keys (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                key_hash TEXT NOT NULL,
                key_prefix TEXT NOT NULL,
                label TEXT DEFAULT 'default',
                scopes TEXT DEFAULT '["chat","vision","agents"]',
                last_used_at TEXT,
                expires_at TEXT,
                revoked INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL,
                user_id TEXT,
                event_type TEXT NOT NULL,
                quantity INTEGER DEFAULT 1,
                model TEXT,
                metadata TEXT DEFAULT '{}',
                recorded_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS billing_events (
                id TEXT PRIMARY KEY,
                stripe_event_id TEXT UNIQUE,
                event_type TEXT NOT NULL,
                workspace_id TEXT,
                payload TEXT NOT NULL,
                processed INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS subscriptions (
                id TEXT PRIMARY KEY,
                workspace_id TEXT UNIQUE NOT NULL,
                plan_id TEXT NOT NULL,
                stripe_subscription_id TEXT,
                status TEXT DEFAULT 'active',
                current_period_start TEXT,
                current_period_end TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
        """)

        # Seed plans
        cur = c.execute("SELECT COUNT(*) FROM plans")
        if cur.fetchone()[0] == 0:
            c.executemany(
                """INSERT INTO plans (id, display_name, max_seats, max_concurrent_sessions,
                   max_ide_hours_month, vision_quota_month, storage_limit_gb,
                   notebook_quota_month, agent_jobs_month, token_budget_month,
                   audit_retention_days, features) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    ("developer", "Developer", 1, 1, 0, 10, 1, 0, 100, 500000, 30,
                     '{"ide_enabled": false, "byok": true}'),
                    ("studio", "Studio", 3, 2, 100, 500, 10, 50, 500, 5000000, 90,
                     '{"ide_enabled": true, "byok": true}'),
                    ("lab", "Lab", 10, 5, 500, 5000, 100, 500, 5000, 50000000, 365,
                     '{"ide_enabled": true, "byok": true, "private_datasets": true}'),
                    ("enterprise", "Enterprise", -1, -1, -1, -1, -1, -1, -1, -1, -1,
                     '{"ide_enabled": true, "sso": true, "dedicated_compute": true}'),
                ],
            )
            c.commit()

    def _row_to_record(self, row):
        if row is None:
            return None
        return _Record(dict(row))

    async def fetchrow(self, query: str, *args):
        q = self._pg_to_sqlite(query)
        cur = self._conn.execute(q, args)
        row = cur.fetchone()
        return self._row_to_record(row)

    async def fetch(self, query: str, *args):
        q = self._pg_to_sqlite(query)
        cur = self._conn.execute(q, args)
        return [self._row_to_record(r) for r in cur.fetchall()]

    async def execute(self, query: str, *args):
        q = self._pg_to_sqlite(query)
        self._conn.execute(q, args)
        self._conn.commit()

    async def executemany(self, query: str, args_list):
        q = self._pg_to_sqlite(query)
        self._conn.executemany(q, args_list)
        self._conn.commit()

    @staticmethod
    def _pg_to_sqlite(query: str) -> str:
        """Convert $1, $2 positional params to ? for SQLite."""
        import re
        return re.sub(r'\$\d+', '?', query)


# ─── Wire it up ──────────────────────────────────────────────────────

_db = SqliteDatabase()


async def _override_get_db():
    return _db


@asynccontextmanager
async def _lifespan(app):
    yield


def create_app():
    from control_plane import app
    from control_plane.db import get_db

    app.dependency_overrides[get_db] = _override_get_db
    app.router.lifespan_context = _lifespan

    # Include gateway routes
    from control_plane.gateway import router as gw
    app.include_router(gw)

    # Include billing routes
    from control_plane.billing import router as billing
    app.include_router(billing)

    # Include domain modules
    from domain.mycology import router as mycology
    from domain.vision import router as vision
    from domain.research import router as research
    from domain.compound import router as compound
    app.include_router(mycology)
    app.include_router(vision)
    app.include_router(research)
    app.include_router(compound)

    # Include knowledge plane
    from knowledge.search import router as knowledge
    app.include_router(knowledge)

    # Include dashboard
    from dashboard import router as dashboard
    app.include_router(dashboard)

    return app


app = create_app()

if __name__ == "__main__":
    print("\n  ◆ Crowe Logic Foundry — Control Plane Preview")
    print("  ─────────────────────────────────────────────")
    print("  Swagger UI:  http://localhost:8001/docs")
    print("  ReDoc:       http://localhost:8001/redoc")
    print("  Health:      http://localhost:8001/health\n")
    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="info")
