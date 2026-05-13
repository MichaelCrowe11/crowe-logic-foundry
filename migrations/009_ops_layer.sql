-- 009_ops_layer.sql
--
-- Operations + research-lab data model for the Crowe Logic / Mycelium EI
-- Engine platform. Backs the cultivation lifecycle, FSMA 204 traceability,
-- HACCP verification logs, strain library DAG, and order-to-lot fulfillment
-- chain that drives forward and backward recall queries.
--
-- See docs/OPS_SCHEMA.md for the design rationale.
--
-- Idempotent where possible. Enum creation guarded by pg_type lookup so this
-- migration can run twice without breaking. Tables use CREATE TABLE IF NOT
-- EXISTS for the same reason.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";


-- ╭──────────────────────────────────────────────────────────────────────╮
-- │ Enums                                                                │
-- ╰──────────────────────────────────────────────────────────────────────╯

DO $$ BEGIN
    CREATE TYPE batch_stage AS ENUM (
        'inoculation', 'colonization', 'fruiting',
        'harvested', 'failed', 'discarded'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE event_type AS ENUM (
        'receive', 'grow', 'harvest', 'transform', 'ship'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE severity AS ENUM ('low', 'medium', 'high', 'critical');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE haccp_result AS ENUM ('pass', 'fail', 'deviation');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE sop_execution_result AS ENUM ('completed', 'skipped', 'failed');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE contamination_type AS ENUM (
        'trichoderma', 'bacterial', 'mites', 'mold_other', 'unknown'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


-- ╭──────────────────────────────────────────────────────────────────────╮
-- │ strains : library of genetic lines, DAG via parent FKs               │
-- ╰──────────────────────────────────────────────────────────────────────╯

CREATE TABLE IF NOT EXISTS strains (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    species TEXT NOT NULL,
    parent_strain_id UUID REFERENCES strains(id) ON DELETE SET NULL,
    secondary_parent_strain_id UUID REFERENCES strains(id) ON DELETE SET NULL,
    origin TEXT,
    notes TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_strains_parent ON strains(parent_strain_id);
CREATE INDEX IF NOT EXISTS idx_strains_species ON strains(species);
CREATE INDEX IF NOT EXISTS idx_strains_active ON strains(archived_at) WHERE archived_at IS NULL;


-- ╭──────────────────────────────────────────────────────────────────────╮
-- │ batches : one fruiting block / sterile run / cultivation cycle       │
-- ╰──────────────────────────────────────────────────────────────────────╯

CREATE TABLE IF NOT EXISTS batches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code TEXT NOT NULL UNIQUE,
    strain_id UUID NOT NULL REFERENCES strains(id) ON DELETE RESTRICT,
    stage batch_stage NOT NULL DEFAULT 'inoculation',
    substrate_recipe TEXT,
    substrate_lot_code TEXT,
    started_at TIMESTAMPTZ NOT NULL,
    expected_harvest_at TIMESTAMPTZ,
    location TEXT NOT NULL,
    operator_id TEXT,
    content_hash TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_batches_strain ON batches(strain_id);
CREATE INDEX IF NOT EXISTS idx_batches_stage ON batches(stage);
CREATE INDEX IF NOT EXISTS idx_batches_started ON batches(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_batches_active ON batches(archived_at) WHERE archived_at IS NULL;


-- ╭──────────────────────────────────────────────────────────────────────╮
-- │ lots : harvest output, the unit that ships                           │
-- ╰──────────────────────────────────────────────────────────────────────╯

CREATE TABLE IF NOT EXISTS lots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id UUID NOT NULL REFERENCES batches(id) ON DELETE RESTRICT,
    code TEXT NOT NULL UNIQUE,
    harvested_at TIMESTAMPTZ NOT NULL,
    weight_kg NUMERIC(10,3) NOT NULL CHECK (weight_kg > 0),
    grade TEXT,
    destination TEXT,
    content_hash TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_lots_batch ON lots(batch_id);
CREATE INDEX IF NOT EXISTS idx_lots_harvested ON lots(harvested_at DESC);


-- ╭──────────────────────────────────────────────────────────────────────╮
-- │ tracking_events : FSMA 204 CTEs, append-only                         │
-- ╰──────────────────────────────────────────────────────────────────────╯

CREATE TABLE IF NOT EXISTS tracking_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type event_type NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    location TEXT NOT NULL,
    batch_id UUID REFERENCES batches(id) ON DELETE RESTRICT,
    lot_id UUID REFERENCES lots(id) ON DELETE RESTRICT,
    supplier TEXT,
    recipient TEXT,
    product TEXT NOT NULL,
    quantity NUMERIC(10,3),
    quantity_unit TEXT,
    reference_doc TEXT,
    recorded_by TEXT,
    replaces_event_id UUID REFERENCES tracking_events(id) ON DELETE SET NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_te_type ON tracking_events(event_type);
CREATE INDEX IF NOT EXISTS idx_te_occurred ON tracking_events(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_te_batch ON tracking_events(batch_id);
CREATE INDEX IF NOT EXISTS idx_te_lot ON tracking_events(lot_id);
CREATE INDEX IF NOT EXISTS idx_te_replaces ON tracking_events(replaces_event_id);


-- ╭──────────────────────────────────────────────────────────────────────╮
-- │ haccp_checks : CCP verification log                                  │
-- ╰──────────────────────────────────────────────────────────────────────╯

CREATE TABLE IF NOT EXISTS haccp_checks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id UUID REFERENCES batches(id) ON DELETE RESTRICT,
    ccp_name TEXT NOT NULL,
    target TEXT NOT NULL,
    actual TEXT NOT NULL,
    result haccp_result NOT NULL,
    operator_id TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    corrective_action TEXT,
    sop_execution_id UUID,  -- FK added after sop_executions exists
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_haccp_batch ON haccp_checks(batch_id);
CREATE INDEX IF NOT EXISTS idx_haccp_recorded ON haccp_checks(recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_haccp_ccp ON haccp_checks(ccp_name);


-- ╭──────────────────────────────────────────────────────────────────────╮
-- │ environmental_readings : sensor time-series, append-only             │
-- ╰──────────────────────────────────────────────────────────────────────╯

CREATE TABLE IF NOT EXISTS environmental_readings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id UUID NOT NULL REFERENCES batches(id) ON DELETE RESTRICT,
    recorded_at TIMESTAMPTZ NOT NULL,
    temp_c NUMERIC(5,2),
    humidity_pct NUMERIC(5,2),
    co2_ppm NUMERIC(7,1),
    light_lux NUMERIC(8,1),
    source TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_envread_batch_time
    ON environmental_readings(batch_id, recorded_at DESC);


-- ╭──────────────────────────────────────────────────────────────────────╮
-- │ contamination_events                                                 │
-- ╰──────────────────────────────────────────────────────────────────────╯

CREATE TABLE IF NOT EXISTS contamination_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id UUID NOT NULL REFERENCES batches(id) ON DELETE RESTRICT,
    contamination_type contamination_type NOT NULL,
    severity severity NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL,
    photo_url TEXT,
    contained_action TEXT,
    operator_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_contam_batch ON contamination_events(batch_id);
CREATE INDEX IF NOT EXISTS idx_contam_detected ON contamination_events(detected_at DESC);


-- ╭──────────────────────────────────────────────────────────────────────╮
-- │ sops + sop_executions                                                │
-- ╰──────────────────────────────────────────────────────────────────────╯

CREATE TABLE IF NOT EXISTS sops (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code TEXT NOT NULL,
    version INT NOT NULL,
    title TEXT NOT NULL,
    body_md TEXT NOT NULL,
    is_current BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at TIMESTAMPTZ,
    UNIQUE (code, version)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sops_current
    ON sops(code) WHERE is_current;


CREATE TABLE IF NOT EXISTS sop_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sop_id UUID NOT NULL REFERENCES sops(id) ON DELETE RESTRICT,
    batch_id UUID REFERENCES batches(id) ON DELETE RESTRICT,
    operator_id TEXT NOT NULL,
    executed_at TIMESTAMPTZ NOT NULL,
    result sop_execution_result NOT NULL,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sopexec_batch ON sop_executions(batch_id);
CREATE INDEX IF NOT EXISTS idx_sopexec_sop ON sop_executions(sop_id);
CREATE INDEX IF NOT EXISTS idx_sopexec_executed ON sop_executions(executed_at DESC);


-- Now that sop_executions exists, add the haccp_checks FK to it.
DO $$ BEGIN
    ALTER TABLE haccp_checks
        ADD CONSTRAINT fk_haccp_sop_exec
        FOREIGN KEY (sop_execution_id)
        REFERENCES sop_executions(id) ON DELETE SET NULL;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


-- ╭──────────────────────────────────────────────────────────────────────╮
-- │ trials : research experiments                                        │
-- ╰──────────────────────────────────────────────────────────────────────╯

CREATE TABLE IF NOT EXISTS trials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    hypothesis TEXT,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    primary_strain_id UUID REFERENCES strains(id) ON DELETE SET NULL,
    batch_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
    treatments JSONB NOT NULL DEFAULT '[]'::jsonb,
    outcome TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_trials_strain ON trials(primary_strain_id);
CREATE INDEX IF NOT EXISTS idx_trials_batches ON trials USING GIN (batch_ids);
CREATE INDEX IF NOT EXISTS idx_trials_started ON trials(started_at DESC);


-- ╭──────────────────────────────────────────────────────────────────────╮
-- │ customers + orders + order_lots                                      │
-- ╰──────────────────────────────────────────────────────────────────────╯

CREATE TABLE IF NOT EXISTS customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_id TEXT UNIQUE,
    email TEXT,
    name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_customers_email ON customers(email);


CREATE TABLE IF NOT EXISTS orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_id TEXT UNIQUE,
    customer_id UUID REFERENCES customers(id) ON DELETE SET NULL,
    placed_at TIMESTAMPTZ NOT NULL,
    shipped_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'placed',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_placed ON orders(placed_at DESC);


CREATE TABLE IF NOT EXISTS order_lots (
    order_id UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    lot_id UUID NOT NULL REFERENCES lots(id) ON DELETE RESTRICT,
    quantity NUMERIC(10,3) NOT NULL CHECK (quantity > 0),
    quantity_unit TEXT NOT NULL,
    PRIMARY KEY (order_id, lot_id)
);

CREATE INDEX IF NOT EXISTS idx_orderlots_lot ON order_lots(lot_id);
