-- Checkout provisioning buffer.
--
-- The Stripe checkout.session.completed webhook writes a row here with
-- the freshly-minted API key for the new user. The success page reads
-- that row exactly once (claimed=TRUE flips on first retrieval). After
-- that, the key is only recoverable via manual rotation from the
-- control plane dashboard.
--
-- The row also carries a small error column for cases where the
-- webhook couldn't provision (missing email, duplicate processing,
-- etc.) so the success page can surface a clean error instead of a
-- silent 404.

CREATE TABLE IF NOT EXISTS checkout_provisions (
    stripe_session_id  TEXT PRIMARY KEY,
    email              TEXT NOT NULL DEFAULT '',
    tier_key           TEXT NOT NULL,
    workspace_id       TEXT NOT NULL DEFAULT '',
    api_key            TEXT NOT NULL DEFAULT '',
    claimed            BOOLEAN NOT NULL DEFAULT FALSE,
    error              TEXT DEFAULT '',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_checkout_provisions_email
    ON checkout_provisions(email);

CREATE INDEX IF NOT EXISTS idx_checkout_provisions_created_at
    ON checkout_provisions(created_at DESC);
