-- Crowe Logic Code launch plan cutover.
--
-- Keeps the old developer/studio/lab rows for compatibility, but hides
-- them from public pricing and creates the customer-facing launch plans:
-- personal, pro, team, enterprise.

ALTER TABLE workspaces ALTER COLUMN plan_id SET DEFAULT 'personal';

UPDATE plans SET is_public = FALSE WHERE id IN ('developer', 'studio', 'lab');

INSERT INTO plans (
    id, display_name, max_seats, max_concurrent_sessions,
    max_ide_hours_month, vision_quota_month, storage_limit_gb,
    notebook_quota_month, agent_jobs_month, token_budget_month,
    audit_retention_days, features, monthly_price_cents,
    annual_price_cents, overage_per_1k_cents, tagline,
    highlights, sort_order, is_public, cta_label
)
VALUES
  ('byok', 'BYOK', 1, 1, 0, 0, 1, 0, 100, 0, 30,
   '{"ide_enabled": false, "byok": true, "dual_mode": true, "synthesis": true, "user_provided_keys": true}'::jsonb,
   1900, 19200, 0,
   'Bring your own provider keys and use the Crowe Logic orchestration layer.',
   '[
      "Crowe Logic extension for VS Code",
      "Bring your own provider keys",
      "Dual-model orchestration",
      "Synthesis layer",
      "No hosted-model credit meter"
   ]'::jsonb,
   5, FALSE, 'Start BYOK'),
  ('personal', 'Personal', 1, 1, 0, 10, 1, 0, 100, 750000, 30,
   '{"ide_enabled": false, "byok": true, "dual_mode": true, "synthesis": true}'::jsonb,
   2900, 30000, 2,
   'Everyday multi-model coding for solo operators.',
   '[
      "Crowe Logic extension for VS Code",
      "750 monthly Crowe Logic credits",
      "Dual-model reasoning and synthesis",
      "BYOK provider routing",
      "Personal usage dashboard"
   ]'::jsonb,
   10, TRUE, 'Start Personal'),
  ('pro', 'Pro', 1, 2, 100, 500, 10, 50, 500, 3000000, 90,
   '{"ide_enabled": true, "byok": true, "dual_mode": true, "dual_mode_unmetered": true, "synthesis": true, "priority_queue": true}'::jsonb,
   9900, 102000, 2,
   'Power-user tier with hosted IDE and flagship model access.',
   '[
      "Everything in Personal",
      "3,000 monthly Crowe Logic credits",
      "Hosted remote IDE pool",
      "Priority model routing",
      "Pro support"
   ]'::jsonb,
   20, TRUE, 'Start Pro'),
  ('team', 'Team', 25, 5, 500, 5000, 100, 500, 5000, 15000000, 365,
   '{"ide_enabled": true, "byok": true, "shared_workspaces": true, "admin_cost_reporting": true, "sso": true, "private_datasets": true}'::jsonb,
   4900, 50400, 1,
   'Shared workspaces and controls for small teams.',
   '[
      "Everything in Pro",
      "1,500 monthly credits per seat",
      "Shared workspaces",
      "Team cost reporting",
      "SSO-ready access controls"
   ]'::jsonb,
   30, TRUE, 'Start Team'),
  ('enterprise', 'Enterprise', -1, -1, -1, -1, -1, -1, -1, -1, -1,
   '{"ide_enabled": true, "byok": true, "dedicated_compute": true, "private_gateway": true, "audit_logs": true, "sso": true, "white_label": true}'::jsonb,
   NULL, NULL, 0,
   'Dedicated deployment, compliance controls, and private model routing.',
   '[
      "Dedicated model gateway",
      "Private deployment options",
      "SAML/SCIM SSO",
      "Audit log retention controls",
      "Named engineering liaison"
   ]'::jsonb,
   40, TRUE, 'Talk to sales')
ON CONFLICT (id) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    max_seats = EXCLUDED.max_seats,
    max_concurrent_sessions = EXCLUDED.max_concurrent_sessions,
    max_ide_hours_month = EXCLUDED.max_ide_hours_month,
    vision_quota_month = EXCLUDED.vision_quota_month,
    storage_limit_gb = EXCLUDED.storage_limit_gb,
    notebook_quota_month = EXCLUDED.notebook_quota_month,
    agent_jobs_month = EXCLUDED.agent_jobs_month,
    token_budget_month = EXCLUDED.token_budget_month,
    audit_retention_days = EXCLUDED.audit_retention_days,
    features = EXCLUDED.features,
    monthly_price_cents = EXCLUDED.monthly_price_cents,
    annual_price_cents = EXCLUDED.annual_price_cents,
    overage_per_1k_cents = EXCLUDED.overage_per_1k_cents,
    tagline = EXCLUDED.tagline,
    highlights = EXCLUDED.highlights,
    sort_order = EXCLUDED.sort_order,
    is_public = EXCLUDED.is_public,
    cta_label = EXCLUDED.cta_label;

UPDATE workspaces SET plan_id = 'personal' WHERE plan_id = 'developer';
UPDATE workspaces SET plan_id = 'pro' WHERE plan_id = 'studio';
UPDATE workspaces SET plan_id = 'team' WHERE plan_id = 'lab';

UPDATE subscriptions SET plan_id = 'personal' WHERE plan_id = 'developer';
UPDATE subscriptions SET plan_id = 'pro' WHERE plan_id = 'studio';
UPDATE subscriptions SET plan_id = 'team' WHERE plan_id = 'lab';
