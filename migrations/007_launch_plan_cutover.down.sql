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

-- Strand any workspaces still pointing at byok/enterprise on the safest legacy
-- equivalent so the FK delete below succeeds. Operators should reconcile these
-- manually after rolling back.
UPDATE workspaces     SET plan_id = 'developer' WHERE plan_id IN ('byok', 'enterprise');
UPDATE subscriptions  SET plan_id = 'developer' WHERE plan_id IN ('byok', 'enterprise');

DELETE FROM plans WHERE id IN ('byok', 'personal', 'pro', 'team', 'enterprise');

UPDATE plans SET is_public = TRUE WHERE id IN ('developer', 'studio', 'lab');

ALTER TABLE workspaces ALTER COLUMN plan_id SET DEFAULT 'developer';
