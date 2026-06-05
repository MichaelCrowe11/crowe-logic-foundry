-- Agent-native payment rail: per-agent wallet + idempotent payment receipts.
CREATE TABLE IF NOT EXISTS agent_wallets (
    client_id     TEXT PRIMARY KEY,
    balance       BIGINT NOT NULL DEFAULT 0,        -- micro-USD
    funding       TEXT   NOT NULL DEFAULT 'crowe-credit',
    chain_address TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS payment_receipts (
    id          TEXT PRIMARY KEY,                   -- payment nonce (idempotency key)
    client_id   TEXT NOT NULL REFERENCES agent_wallets(client_id),
    scheme      TEXT NOT NULL,
    amount      BIGINT NOT NULL,
    resource    TEXT NOT NULL,
    tx_ref      TEXT,
    settled_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
