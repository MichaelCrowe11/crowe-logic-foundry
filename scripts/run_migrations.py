#!/usr/bin/env python3
"""
Apply SQL migrations in order to $CONTROL_PLANE_DATABASE_URL (or
$NEON_DATABASE_URL or $DATABASE_URL — Railway exposes the last one when a
Postgres plugin is attached, so we accept all three).

Idempotent: every migration uses `CREATE TABLE IF NOT EXISTS` etc. We also
record applied filenames in a `_migrations` table so reruns are cheap.

Usage:
    python scripts/run_migrations.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIG_DIR = ROOT / "migrations"


def _resolve_db_url() -> str:
    for var in ("CONTROL_PLANE_DATABASE_URL", "NEON_DATABASE_URL", "DATABASE_URL"):
        v = os.environ.get(var)
        if v:
            return v
    raise SystemExit(
        "No database URL configured. Set CONTROL_PLANE_DATABASE_URL, "
        "NEON_DATABASE_URL, or DATABASE_URL."
    )


async def _apply(url: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(url)
    try:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS _migrations (
                filename   TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ DEFAULT now()
            );
            """
        )

        applied = {
            r["filename"]
            for r in await conn.fetch("SELECT filename FROM _migrations")
        }

        skip = {
            s.strip()
            for s in os.environ.get(
                "CONTROL_PLANE_MIGRATION_SKIP", "001_initial.sql"
            ).split(",")
            if s.strip()
        }
        optional = {
            s.strip()
            for s in os.environ.get(
                "CONTROL_PLANE_MIGRATION_OPTIONAL", "003_knowledge_plane.sql"
            ).split(",")
            if s.strip()
        }

        files = sorted(MIG_DIR.glob("*.sql"))
        if not files:
            print(f"  ! no migrations found under {MIG_DIR}")
            return

        for path in files:
            name = path.name
            if name.endswith(".down.sql"):
                continue
            if name in skip:
                print(f"  ⤼ {name} — skipped (SKIP list)")
                continue
            if name in applied:
                print(f"  · {name} — already applied")
                continue
            sql = path.read_text()
            print(f"  ▶ applying {name} ({len(sql):,} bytes)")
            try:
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute(
                        "INSERT INTO _migrations (filename) VALUES ($1)", name
                    )
                print(f"    ✓ {name}")
            except Exception as e:  # noqa: BLE001
                if name in optional:
                    print(f"    ⚠ {name} failed (optional, continuing): {e}")
                    continue
                raise
    finally:
        await conn.close()


def main() -> int:
    url = _resolve_db_url()
    redacted = url.split("@")[-1] if "@" in url else url
    print(f"  ◆ Migrating against {redacted}")
    asyncio.run(_apply(url))
    print("  ✓ migrations complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
