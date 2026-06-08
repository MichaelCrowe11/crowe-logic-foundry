-- Reverse 011: project free_usage's device rows back onto anon_usage, then drop.
-- anon_usage was left intact by the up migration, so this restores any turns the
-- gateway recorded under device:<id> while free_usage was live.
INSERT INTO anon_usage (device_id, day, turns)
SELECT substring(principal_id FROM 8), day, turns
FROM free_usage
WHERE principal_id LIKE 'device:%'
-- DO UPDATE (not DO NOTHING): while 011 was live the gateway wrote only to
-- free_usage, so its device counts are the authoritative post-migration state
-- and must win over the frozen anon_usage rows. (Trades off any direct hotfix
-- writes made straight to anon_usage during the window — none are expected.)
ON CONFLICT (device_id, day) DO UPDATE SET turns = EXCLUDED.turns;

DROP TABLE IF EXISTS free_usage;
