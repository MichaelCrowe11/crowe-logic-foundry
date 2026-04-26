-- Reverse 007_launch_plan_cutover.sql.
--
-- Order matters: remap workspace and subscription references off the launch
-- plans before deleting them, otherwise the FK from workspaces.plan_id and
-- subscriptions.plan_id will fail.

UPDATE subscriptions SET plan_id = 'developer' WHERE plan_id = 'personal';
UPDATE subscriptions SET plan_id = 'studio'    WHERE plan_id = 'pro';
UPDATE subscriptions SET plan_id = 'lab'       WHERE plan_id = 'team';

UPDATE workspaces SET plan_id = 'developer' WHERE plan_id = 'personal';
UPDATE workspaces SET plan_id = 'studio'    WHERE plan_id = 'pro';
UPDATE workspaces SET plan_id = 'lab'       WHERE plan_id = 'team';

-- Strand any workspace/subscription still pointing at byok onto developer so
-- the byok plan delete below succeeds. enterprise pre-existed 007 and must
-- survive the rollback, so leave enterprise references untouched.
UPDATE workspaces     SET plan_id = 'developer' WHERE plan_id = 'byok';
UPDATE subscriptions  SET plan_id = 'developer' WHERE plan_id = 'byok';

-- Remove only the rows 007 introduced as net-new. enterprise existed in the
-- seed before 007 and is a valid public plan after rollback, so don't drop it.
DELETE FROM plans WHERE id IN ('byok', 'personal', 'pro', 'team');

UPDATE plans SET is_public = TRUE WHERE id IN ('developer', 'studio', 'lab');

ALTER TABLE workspaces ALTER COLUMN plan_id SET DEFAULT 'developer';
