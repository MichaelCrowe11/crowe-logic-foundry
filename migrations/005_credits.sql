-- Credit accounting for the Gate 1 pricing model.
--
-- Adds per-workspace credit balance with a monthly allocation and reset,
-- plus an audit trail of every consume/refill for support and analytics.
--
-- Kept separate from the legacy plans/usage tables so either scheme can
-- be run until the full cutover to credit-based billing lands in
-- production.

CREATE TABLE IF NOT EXISTS workspace_credits (
    workspace_id  TEXT PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
    tier_key      TEXT NOT NULL DEFAULT 'personal',
    balance       INTEGER NOT NULL DEFAULT 0,
    allocation    INTEGER NOT NULL DEFAULT 0,
    reset_at      TIMESTAMPTZ,
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS credit_transactions (
    id            BIGSERIAL PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    amount        INTEGER NOT NULL,
    reason        TEXT NOT NULL,
    model_label   TEXT,
    metadata      JSONB DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_credit_tx_workspace_created
    ON credit_transactions(workspace_id, created_at DESC);
