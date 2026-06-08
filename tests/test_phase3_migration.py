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
    # Symmetric to the up migration: the down must project device rows back onto
    # anon_usage (strip the 7-char `device:` prefix), not merely drop the table.
    assert "INSERT INTO anon_usage" in down
    assert "FROM free_usage" in down
    assert "WHERE principal_id LIKE 'device:%'" in down
    assert "substring(principal_id FROM 8)" in down
