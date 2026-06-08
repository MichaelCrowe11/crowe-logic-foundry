-- Reverse 011: project free_usage's device rows back onto anon_usage, then drop.
-- anon_usage was left intact by the up migration, so this restores any turns the
-- gateway recorded under device:<id> while free_usage was live.
INSERT INTO anon_usage (device_id, day, turns)
SELECT substring(principal_id FROM 8), day, turns
FROM free_usage
WHERE principal_id LIKE 'device:%'
ON CONFLICT (device_id, day) DO UPDATE SET turns = EXCLUDED.turns;

DROP TABLE IF EXISTS free_usage;
