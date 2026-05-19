"""
Database connection pool for the Control Plane API.

Uses asyncpg for async Postgres access against Neon.
Falls back to a lightweight mock for testing.
"""

import os
from contextlib import asynccontextmanager

# ─── Async Postgres pool (production) ─────────────────────────────────

_pool = None

DATABASE_URL = os.environ.get(
    "CONTROL_PLANE_DATABASE_URL",
    os.environ.get("NEON_DATABASE_URL", ""),
)


class Database:
    """Thin wrapper around asyncpg.Pool so the API layer doesn't import asyncpg directly."""

    def __init__(self, pool):
        self._pool = pool

    async def fetchrow(self, query: str, *args):
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def fetch(self, query: str, *args):
        async with self._pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def execute(self, query: str, *args):
        async with self._pool.acquire() as conn:
            return await conn.execute(query, *args)

    async def executemany(self, query: str, args_list):
        async with self._pool.acquire() as conn:
            return await conn.executemany(query, args_list)


async def init_pool():
    """Create the connection pool. Called once at startup."""
    global _pool
    if _pool is not None:
        return

    if not DATABASE_URL:
        raise RuntimeError(
            "No database URL configured. Set CONTROL_PLANE_DATABASE_URL or NEON_DATABASE_URL."
        )

    import asyncpg
    _pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=2,
        max_size=10,
        command_timeout=30,
    )


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def get_db() -> Database:
    """FastAPI dependency that returns a Database handle."""
    if _pool is None:
        await init_pool()
    return Database(_pool)


# ─── Lifespan (FastAPI >= 0.95) ──────────────────────────────────────

@asynccontextmanager
async def lifespan(app):
    await init_pool()
    yield
    await close_pool()
