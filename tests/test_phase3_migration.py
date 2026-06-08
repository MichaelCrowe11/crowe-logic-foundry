"""Phase 3: migration 011 creates the principal-keyed free_usage table."""

from pathlib import Path

MIG = Path(__file__).resolve().parent.parent / "migrations"


def test_migration_011_creates_free_usage_and_rekeys():
    up = (MIG / "011_free_usage.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS free_usage" in up
    assert "principal_id" in up
    assert "PRIMARY KEY (principal_id, day)" in up
    # Rekey existing anon rows in place, prefixed device:<id> — not drop/recreate.
    assert "INSERT INTO free_usage" in up
    assert "'device:'" in up
    assert "FROM anon_usage" in up


def test_migration_011_has_reversible_down():
    down = (MIG / "011_free_usage.down.sql").read_text()
    assert "DROP TABLE IF EXISTS free_usage" in down
