-- Principal-keyed free-tier daily counter. Generalizes anon_usage (device-only)
-- so the same mechanism meters anonymous devices (device:<id>) and signed-in
-- free accounts (user:<sub>). anon_usage is left intact (frozen) so a rollback
-- to the prior image still has its data; the gateway now reads/writes free_usage.
CREATE TABLE IF NOT EXISTS free_usage (
    principal_id TEXT NOT NULL,
    day          DATE NOT NULL,
    turns        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (principal_id, day)
);

-- Rekey existing live anonymous rows in place (device:<device_id>). Idempotent:
-- ON CONFLICT DO NOTHING means re-running the migration will not double-insert.
INSERT INTO free_usage (principal_id, day, turns)
SELECT 'device:' || device_id, day, turns
FROM anon_usage
ON CONFLICT (principal_id, day) DO NOTHING;
