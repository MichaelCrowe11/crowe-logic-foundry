-- Control Plane Schema — Users, Orgs, Workspaces, Plans, Entitlements, Usage
--
-- Extends the existing Crowe-Synapse schema (001_initial.sql).
-- Designed for Neon Postgres; uses TIMESTAMPTZ, UUID defaults, and JSONB.

-- ─── Identity ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    email         TEXT UNIQUE NOT NULL,
    display_name  TEXT,
    password_hash TEXT,                        -- bcrypt; NULL for SSO-only users
    role          TEXT NOT NULL DEFAULT 'researcher',  -- owner | admin | researcher | operator | viewer
    avatar_url    TEXT,
    created_at    TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS organizations (
    id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    name          TEXT NOT NULL,
    slug          TEXT UNIQUE NOT NULL,
    owner_id      TEXT NOT NULL REFERENCES users(id),
    stripe_customer_id TEXT,                   -- Stripe customer object
    created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS org_members (
    org_id   TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role     TEXT NOT NULL DEFAULT 'researcher',
    PRIMARY KEY (org_id, user_id)
);

-- ─── Plans & Entitlements ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS plans (
    id                    TEXT PRIMARY KEY,     -- developer | studio | lab | enterprise
    display_name          TEXT NOT NULL,
    stripe_price_id       TEXT,                 -- Stripe recurring price
    max_seats             INTEGER DEFAULT 1,
    max_concurrent_sessions INTEGER DEFAULT 1,
    max_ide_hours_month   INTEGER DEFAULT 0,    -- 0 = no hosted IDE
    allowed_models        JSONB DEFAULT '[]',   -- model name allowlist; empty = all
    vision_quota_month    INTEGER DEFAULT 0,
    storage_limit_gb      INTEGER DEFAULT 1,
    notebook_quota_month  INTEGER DEFAULT 0,
    agent_jobs_month      INTEGER DEFAULT 100,
    token_budget_month    BIGINT DEFAULT 500000,
    audit_retention_days  INTEGER DEFAULT 30,
    features              JSONB DEFAULT '{}',   -- boolean flags: ide_enabled, private_datasets, etc.
    created_at            TIMESTAMPTZ DEFAULT now()
);

-- Seed the four plans from the blueprint
INSERT INTO plans (id, display_name, max_seats, max_concurrent_sessions,
                   max_ide_hours_month, vision_quota_month, storage_limit_gb,
                   notebook_quota_month, agent_jobs_month, token_budget_month,
                   audit_retention_days, features)
VALUES
  ('developer', 'Developer', 1, 1, 0, 10, 1, 0, 100, 500000, 30,
   '{"ide_enabled": false, "byok": true, "private_datasets": false}'),
  ('studio', 'Studio', 3, 2, 100, 500, 10, 50, 500, 5000000, 90,
   '{"ide_enabled": true, "byok": true, "private_datasets": false}'),
  ('lab', 'Lab', 10, 5, 500, 5000, 100, 500, 5000, 50000000, 365,
   '{"ide_enabled": true, "byok": true, "private_datasets": true}'),
  ('enterprise', 'Enterprise', -1, -1, -1, -1, -1, -1, -1, -1, -1,
   '{"ide_enabled": true, "byok": true, "private_datasets": true, "sso": true, "dedicated_compute": true, "private_gateway": true}')
ON CONFLICT (id) DO NOTHING;

-- ─── Workspaces ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS workspaces (
    id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    org_id        TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    slug          TEXT NOT NULL,
    ws_type       TEXT NOT NULL DEFAULT 'personal',  -- personal | lab | company | enterprise
    plan_id       TEXT NOT NULL REFERENCES plans(id) DEFAULT 'developer',
    stripe_subscription_id TEXT,
    status        TEXT NOT NULL DEFAULT 'active',     -- active | suspended | cancelled
    settings      JSONB DEFAULT '{}',
    created_at    TIMESTAMPTZ DEFAULT now(),
    UNIQUE (org_id, slug)
);

-- ─── API Keys ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS api_keys (
    id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    workspace_id  TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id       TEXT NOT NULL REFERENCES users(id),
    key_hash      TEXT NOT NULL,               -- SHA-256 of the raw key
    key_prefix    TEXT NOT NULL,               -- first 8 chars for display: "cl_xxxx…"
    label         TEXT DEFAULT 'default',
    scopes        JSONB DEFAULT '["chat", "vision", "agents"]',
    last_used_at  TIMESTAMPTZ,
    expires_at    TIMESTAMPTZ,
    revoked       BOOLEAN DEFAULT FALSE,
    created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys (key_hash) WHERE NOT revoked;

-- ─── Usage Ledger ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS usage_events (
    id            BIGSERIAL PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES workspaces(id),
    user_id       TEXT REFERENCES users(id),
    event_type    TEXT NOT NULL,               -- tokens | tool_call | vision_job | ide_hour | agent_job | storage
    quantity      BIGINT NOT NULL DEFAULT 1,
    model         TEXT,                        -- which CroweLM tier was used
    metadata      JSONB DEFAULT '{}',
    recorded_at   TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_usage_workspace_time
    ON usage_events (workspace_id, recorded_at DESC);

CREATE INDEX IF NOT EXISTS idx_usage_type_time
    ON usage_events (event_type, recorded_at DESC);

-- ─── Billing Events (Stripe webhook log) ─────────────────────────────

CREATE TABLE IF NOT EXISTS billing_events (
    id                TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    stripe_event_id   TEXT UNIQUE NOT NULL,
    event_type        TEXT NOT NULL,           -- invoice.paid, subscription.updated, etc.
    workspace_id      TEXT REFERENCES workspaces(id),
    payload           JSONB NOT NULL,
    processed         BOOLEAN DEFAULT FALSE,
    created_at        TIMESTAMPTZ DEFAULT now()
);

-- ─── Subscriptions (denormalized for fast plan checks) ───────────────

CREATE TABLE IF NOT EXISTS subscriptions (
    id                    TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    workspace_id          TEXT UNIQUE NOT NULL REFERENCES workspaces(id),
    plan_id               TEXT NOT NULL REFERENCES plans(id),
    stripe_subscription_id TEXT,
    status                TEXT NOT NULL DEFAULT 'active',  -- active | past_due | cancelled | trialing
    current_period_start  TIMESTAMPTZ,
    current_period_end    TIMESTAMPTZ,
    created_at            TIMESTAMPTZ DEFAULT now(),
    updated_at            TIMESTAMPTZ DEFAULT now()
);

-- ─── Helper views ────────────────────────────────────────────────────

-- Monthly usage rollup per workspace
CREATE OR REPLACE VIEW workspace_usage_monthly AS
SELECT
    workspace_id,
    event_type,
    date_trunc('month', recorded_at) AS month,
    SUM(quantity) AS total
FROM usage_events
GROUP BY workspace_id, event_type, date_trunc('month', recorded_at);
