-- Anonymous free-tier daily usage. Device ids are HMAC-verified, not FK-backed.
CREATE TABLE IF NOT EXISTS anon_usage (
    device_id TEXT NOT NULL,
    day DATE NOT NULL,
    turns INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (device_id, day)
);
