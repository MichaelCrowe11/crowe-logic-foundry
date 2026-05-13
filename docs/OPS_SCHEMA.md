# OPS_SCHEMA: Cultivation + Research Lab Operations Data Model

Status: design, 2026-05-13
Owner: Michael Crowe
Scope: data model only. No routes, no UI, no on-chain code.

## 1. Motivation

Southwest Mushrooms today runs on a stitched-together stack: Shopify for orders,
`agents/*.yaml` for capability descriptions, `drug_discovery/` for research, and
paper or ad-hoc SOPs for cultivation. There is no single source of truth that
can answer "where did the lot in this order come from, what conditions did it
grow in, who signed off on the HACCP checks, and what strain lineage produced
it." That is exactly the question FDA FSMA Section 204 (the Food Traceability
List Rule, compliance date 2026-01-20) requires us to answer in under 24 hours.

This schema is the operations layer of the Crowe Logic / Mycelium EI Engine
platform. It captures:

- Every fruiting block, sterile run, or cultivation cycle as a `batch`
- Every harvest pull as a `lot`
- Every environmental, contamination, and SOP-execution signal against a batch
- The five FSMA 204 Critical Tracking Events (CTEs) as `tracking_events`
- HACCP critical control point (CCP) verification logs
- The strain library as a DAG (parent_strain_id chains)
- Research trials referencing batches + strains + treatments
- Orders and the lots that fulfilled them, so we can run forward traceability
  ("who did we ship this contaminated lot to") and backward traceability ("which
  substrate run did this customer's bag come from") in a single SQL hop

The shape mirrors AICL's `Conversation` DAG (parent_message_id) on purpose:
the platform already understands DAGs-over-immutable-rows, so reusing that
shape for strain lineage and event amendments keeps the cognitive load low.

## 2. Design principles

1. Append-only where the regulator cares. `tracking_events`,
   `environmental_readings`, `haccp_checks`, `contamination_events`, and
   `sop_executions` are insert-only. Corrections are new rows with
   `replaces_event_id` pointing at the row being amended. We never UPDATE or
   DELETE a row a regulator might read.
2. Soft delete via `archived_at TIMESTAMPTZ`. Hard deletes break traceability
   chains. Even a "mistake" batch needs to stay queryable.
3. Stable content hashes on `batches` and `lots`. SHA-256 over the immutable
   subset of fields. This is what we will later anchor to Base L2 as a
   provenance attestation. The schema stores the hash today so anchoring is a
   pure append-only operation later.
4. Strain lineage is a DAG, not a tree. Hybrids can have two declared parents
   via the optional `secondary_parent_strain_id`. Same shape as AICL
   parent_message_id, same walking algorithm.
5. UUID primary keys. Postgres `gen_random_uuid()`. Avoids the surrogate-key
   contention problem when two greenhouses sync overnight.
6. TIMESTAMPTZ everywhere. We operate in Phoenix and our customers eat in
   four time zones. UTC at rest, render local at the edge.
7. One enum per concept. Postgres enums (not check constraints, not lookup
   tables) for `batch_stage`, `event_type`, `severity`, `haccp_result`,
   `sop_execution_result`, `contamination_type`. Cheap to add values via
   `ALTER TYPE ... ADD VALUE` and they reject typos at insert time.
8. Indexes on every column we know we will join or filter on for recall:
   `batch_id`, `lot_id`, `strain_id`, `event_type`, `recorded_at`.

## 3. Table-by-table design

### 3.1 strains

The strain library. The unit is "a genetic line we cultivate." Parent
relationships form a DAG: `parent_strain_id` (primary parent) and an optional
`secondary_parent_strain_id` for hybrids.

| column | type | notes |
|---|---|---|
| id | UUID PK | gen_random_uuid() |
| name | TEXT NOT NULL | "Lion's Mane CL-001" |
| species | TEXT NOT NULL | scientific name, e.g. "Hericium erinaceus" |
| parent_strain_id | UUID NULL FK strains(id) | primary parent, DAG edge |
| secondary_parent_strain_id | UUID NULL FK strains(id) | optional second parent |
| origin | TEXT | "wild collected, Coconino NF" / "USDA NRRL 6464" |
| notes | TEXT | free text |
| metadata | JSONB DEFAULT '{}' | lab data, sequencing pointers |
| created_at | TIMESTAMPTZ | gen at insert |
| archived_at | TIMESTAMPTZ NULL | soft delete |

We deliberately do NOT FK to the existing `strains` table in migration 003.
The 003 table is the knowledge-plane catalog (taxonomy + descriptions). This
one is the operational genetic line. They serve different reads. We put the
new table in the `ops` schema to avoid name collision. If the team later
wants one table, that is a backfill problem, not a schema problem.

### 3.2 batches

The center of gravity. One row per fruiting block / sterile run / inoculation
batch / fruiting chamber cycle. Tracks the live cycle through stages.

| column | type | notes |
|---|---|---|
| id | UUID PK | |
| code | TEXT UNIQUE NOT NULL | human label: "LM-2026-W19-003" |
| strain_id | UUID NOT NULL FK strains(id) | what genetics |
| stage | batch_stage NOT NULL | enum below |
| substrate_recipe | TEXT | reference name, not formula |
| substrate_lot_code | TEXT | upstream supplier lot, KDE for the Receive CTE |
| started_at | TIMESTAMPTZ NOT NULL | inoculation date |
| expected_harvest_at | TIMESTAMPTZ | planning aid |
| location | TEXT NOT NULL | "Phoenix - Room B - Rack 3" |
| operator_id | TEXT | who started it; soft FK to a future users table |
| content_hash | TEXT | SHA-256 of (id, code, strain_id, substrate_lot_code, started_at, location). Set once at insert by a trigger or by the application. Stable thereafter. |
| metadata | JSONB DEFAULT '{}' | |
| created_at | TIMESTAMPTZ | |
| archived_at | TIMESTAMPTZ NULL | |

`batch_stage` enum: `inoculation`, `colonization`, `fruiting`, `harvested`,
`failed`, `discarded`. The transition rules live in application code, not
in the database: state machines in SQL are brittle and the rules will change
quarterly.

### 3.3 lots

A `lot` is the output of one harvest pull from one batch. A single batch
typically yields 2-4 lots (first flush, second flush, etc). The lot is what
gets weighed, packaged, and shipped.

| column | type | notes |
|---|---|---|
| id | UUID PK | |
| batch_id | UUID NOT NULL FK batches(id) ON DELETE RESTRICT | upstream batch |
| code | TEXT UNIQUE NOT NULL | "LM-2026-W19-003-F1" |
| harvested_at | TIMESTAMPTZ NOT NULL | KDE for Harvest CTE |
| weight_kg | NUMERIC(10,3) NOT NULL CHECK (weight_kg > 0) | |
| grade | TEXT | "A", "B", "tincture-only" |
| destination | TEXT | "fresh sale", "dry", "tincture", "research" |
| content_hash | TEXT | SHA-256 of (id, batch_id, code, harvested_at, weight_kg) |
| metadata | JSONB | |
| created_at | TIMESTAMPTZ | |
| archived_at | TIMESTAMPTZ NULL | |

`ON DELETE RESTRICT` on the batch FK is intentional. The whole point of this
schema is that you cannot delete a batch out from under a shipped lot.

### 3.4 tracking_events

FSMA 204 Critical Tracking Events (CTEs). Append-only. One row per event.
A row carries the CTE type plus the Key Data Elements (KDEs) for that type.

| column | type | notes |
|---|---|---|
| id | UUID PK | |
| event_type | event_type NOT NULL | enum: receive, grow, harvest, transform, ship |
| occurred_at | TIMESTAMPTZ NOT NULL | KDE: date/time of event |
| location | TEXT NOT NULL | KDE: physical location |
| batch_id | UUID NULL FK batches(id) | populated for grow/harvest/transform |
| lot_id | UUID NULL FK lots(id) | populated for harvest/ship |
| supplier | TEXT | KDE for receive: who we got the input from |
| recipient | TEXT | KDE for ship: where it went |
| product | TEXT NOT NULL | the traceable food product name |
| quantity | NUMERIC(10,3) | KDE: amount moved |
| quantity_unit | TEXT | "kg", "lb", "bags" |
| reference_doc | TEXT | invoice, BOL, packing slip ID |
| recorded_by | TEXT | operator who logged the event |
| replaces_event_id | UUID NULL FK tracking_events(id) | amendment chain, append-only correction |
| payload | JSONB DEFAULT '{}' | overflow for type-specific KDEs |
| created_at | TIMESTAMPTZ | |

FSMA 204 KDE mapping:

| CTE | Primary table | Key KDEs captured |
|---|---|---|
| Receive (raw materials) | tracking_events.event_type='receive' | supplier, product, occurred_at, location, quantity, reference_doc (and link batches.substrate_lot_code) |
| Grow | tracking_events.event_type='grow' | batch_id, occurred_at, location |
| Harvest | tracking_events.event_type='harvest' | batch_id, lot_id, occurred_at, location, quantity |
| Transform (drying, tincturing, repacking) | tracking_events.event_type='transform' | batch_id and/or lot_id, occurred_at, location, payload describing transform |
| Ship | tracking_events.event_type='ship' | lot_id, recipient, occurred_at, reference_doc, quantity |

We use a single table rather than per-event-type tables because (a) the KDEs
overlap heavily, (b) FDA wants one chronological view of an item's life, and
(c) querying "the last 24 months of CTEs for lot X" is a single index scan.

### 3.5 haccp_checks

CCP verification log. The HACCP plan itself lives in `sops`. This table is
the daily run record.

| column | type | notes |
|---|---|---|
| id | UUID PK | |
| batch_id | UUID NULL FK batches(id) | CCP can be batch-level or facility-level |
| ccp_name | TEXT NOT NULL | "autoclave_temp_min_15min", "fruiting_co2_max" |
| target | TEXT NOT NULL | "121C, 15 min" |
| actual | TEXT NOT NULL | "122C, 17 min" |
| result | haccp_result NOT NULL | pass, fail, deviation |
| operator_id | TEXT NOT NULL | |
| recorded_at | TIMESTAMPTZ NOT NULL | when the check happened (NOT created_at) |
| corrective_action | TEXT | required when result != pass |
| sop_execution_id | UUID NULL FK sop_executions(id) | which SOP run produced this |
| created_at | TIMESTAMPTZ | |

`target` and `actual` are TEXT, not NUMERIC, because CCPs measure many things:
temps, times, pH, visual checks. We trade structured queries on these for
flexibility. If we later need histograms on autoclave temps, we add a typed
view on top of the JSONB overflow.

### 3.6 environmental_readings

Time-series sensor data. Append-only. High write volume; we expect 1 row per
batch every 60-300 seconds when sensors are wired up. Today these come from
manual log sheets at lower frequency.

| column | type | notes |
|---|---|---|
| id | UUID PK | |
| batch_id | UUID NOT NULL FK batches(id) | |
| recorded_at | TIMESTAMPTZ NOT NULL | sensor reading time |
| temp_c | NUMERIC(5,2) | |
| humidity_pct | NUMERIC(5,2) | |
| co2_ppm | NUMERIC(7,1) | |
| light_lux | NUMERIC(8,1) | |
| source | TEXT | "sensor:room-b-1", "manual:tech-jdoe" |
| created_at | TIMESTAMPTZ | |

Index on `(batch_id, recorded_at DESC)` for "last reading for this batch" and
range queries.

### 3.7 contamination_events

When a batch goes bad. Critical for both FSMA recall and research feedback.

| column | type | notes |
|---|---|---|
| id | UUID PK | |
| batch_id | UUID NOT NULL FK batches(id) | |
| contamination_type | contamination_type NOT NULL | trichoderma, bacterial, mites, mold_other, unknown |
| severity | severity NOT NULL | low, medium, high, critical |
| detected_at | TIMESTAMPTZ NOT NULL | |
| photo_url | TEXT | object storage pointer |
| contained_action | TEXT | "discarded, autoclaved, room sanitized" |
| operator_id | TEXT | |
| created_at | TIMESTAMPTZ | |

### 3.8 sops

SOP definitions. Versioned by row, not by column. A new version is a new row
sharing a `code` with a higher `version` integer.

| column | type | notes |
|---|---|---|
| id | UUID PK | |
| code | TEXT NOT NULL | "SOP-CULT-001" |
| version | INT NOT NULL | monotonic per code |
| title | TEXT NOT NULL | |
| body_md | TEXT NOT NULL | markdown of the SOP |
| is_current | BOOLEAN NOT NULL DEFAULT true | UNIQUE (code) WHERE is_current is enforced via partial unique index |
| created_at | TIMESTAMPTZ | |
| archived_at | TIMESTAMPTZ NULL | |

`UNIQUE (code, version)` and a partial unique index on `(code) WHERE is_current`
make "what is the current version of SOP-CULT-001" a single row lookup.

### 3.9 sop_executions

A record that an operator performed an SOP against a batch.

| column | type | notes |
|---|---|---|
| id | UUID PK | |
| sop_id | UUID NOT NULL FK sops(id) | exact version executed |
| batch_id | UUID NULL FK batches(id) | many SOPs are batch-scoped; a few are facility-scoped |
| operator_id | TEXT NOT NULL | |
| executed_at | TIMESTAMPTZ NOT NULL | |
| result | sop_execution_result NOT NULL | completed, skipped, failed |
| notes | TEXT | |
| created_at | TIMESTAMPTZ | |

### 3.10 trials

Research experiments. References batches and strains and a treatments JSONB.
Lightweight because the research side is still evolving; we lock in only what
we know we will always need.

| column | type | notes |
|---|---|---|
| id | UUID PK | |
| name | TEXT NOT NULL | |
| hypothesis | TEXT | |
| started_at | TIMESTAMPTZ NOT NULL | |
| ended_at | TIMESTAMPTZ NULL | |
| primary_strain_id | UUID NULL FK strains(id) | |
| batch_ids | UUID[] | array because trials span multiple batches |
| treatments | JSONB DEFAULT '[]' | array of {name, dose, applied_at, batch_id} |
| outcome | TEXT | free-text summary, plus links into `payload` |
| payload | JSONB DEFAULT '{}' | structured metrics, plot pointers |
| created_at | TIMESTAMPTZ | |
| archived_at | TIMESTAMPTZ NULL | |

Storing `batch_ids` as a UUID[] is a deliberate choice over a join table. The
N here is small (a trial usually involves 2-20 batches), the read pattern is
"give me the whole trial," and GIN-indexing UUID[] makes "find trials that
include this batch" still fast.

### 3.11 customers

Minimal local mirror. In production this is probably a foreign data wrapper
view onto the Shopify mirror or the Treasury customer table. We keep an FK
target here so `orders` is self-contained and the schema can be tested in
isolation.

| column | type | notes |
|---|---|---|
| id | UUID PK | |
| external_id | TEXT UNIQUE | shopify/stripe customer id |
| email | TEXT | |
| name | TEXT | |
| created_at | TIMESTAMPTZ | |

### 3.12 orders and order_lots

Orders are the customer-facing entity. `order_lots` is the many-to-many that
makes forward traceability possible: given a lot, which orders shipped it?
Given an order, which lots did it pull from?

orders:

| column | type | notes |
|---|---|---|
| id | UUID PK | |
| external_id | TEXT UNIQUE | shopify order id |
| customer_id | UUID NULL FK customers(id) | |
| placed_at | TIMESTAMPTZ NOT NULL | |
| shipped_at | TIMESTAMPTZ NULL | populated when fulfilled |
| status | TEXT NOT NULL | "placed", "fulfilled", "cancelled", "returned" |
| metadata | JSONB | |
| created_at | TIMESTAMPTZ | |

order_lots:

| column | type | notes |
|---|---|---|
| order_id | UUID NOT NULL FK orders(id) ON DELETE CASCADE | |
| lot_id | UUID NOT NULL FK lots(id) ON DELETE RESTRICT | |
| quantity | NUMERIC(10,3) NOT NULL CHECK (quantity > 0) | how much of the lot was used |
| quantity_unit | TEXT NOT NULL | |
| PRIMARY KEY (order_id, lot_id) | | composite |

CASCADE on order deletion is safe; RESTRICT on lot deletion is required for
traceability.

## 4. Content hash design (provenance, future blockchain anchoring)

Each `batch` and `lot` row carries `content_hash TEXT`. The hash is computed
once, at insert, over the immutable subset of the row:

- batches: SHA-256(id || code || strain_id || substrate_lot_code || started_at || location)
- lots: SHA-256(id || batch_id || code || harvested_at || weight_kg)

The hash uses a canonical encoding: hex(UUID) for UUID fields, ISO-8601
TIMESTAMPTZ in UTC, decimal string for numerics, raw UTF-8 for text, joined
with a single 0x1F (ASCII unit separator) between fields. This avoids any
ambiguity from JSON whitespace or key ordering.

Hash computation lives in the application layer (Pydantic model
`compute_content_hash()`), not in a Postgres trigger. The reason: we want the
hash to be reproducible from any client (Python today, a Rust ingestor
tomorrow) without relying on Postgres being in the loop.

When we are ready to anchor, a separate `provenance_anchors` table will hold
`(content_hash, chain, tx_hash, block_number, anchored_at)`. We do not create
that table today. The hash on the batch/lot row is enough to make anchoring a
pure append.

## 5. Amendments and append-only correction chains

`tracking_events` has `replaces_event_id`. When a regulator-facing event was
recorded incorrectly, the fix is:

1. INSERT a new tracking_events row with the corrected KDEs and
   `replaces_event_id = old_event.id`.
2. The application's "current view" filters: `WHERE id NOT IN (SELECT replaces_event_id FROM tracking_events WHERE replaces_event_id IS NOT NULL)`.

We do not surface this chain through a column on the old row, because
mutating the old row would defeat the audit purpose. The chain is walked at
read time. This is the same trick `aicl.Conversation.parent_of` uses.

## 6. FSMA 204 traceability queries (the ones that matter)

The schema is built around two queries that need to run in well under a
minute.

Backward trace (Recall, "this lot is contaminated, where did the substrate
come from"):

```
SELECT
  b.code AS batch,
  b.substrate_lot_code,
  b.started_at,
  s.name AS strain
FROM lots l
JOIN batches b ON b.id = l.batch_id
JOIN strains s ON s.id = b.strain_id
WHERE l.code = $1;
```

Forward trace (Recall, "this lot is contaminated, who did we ship it to"):

```
SELECT
  o.external_id AS order_id,
  o.shipped_at,
  c.email,
  ol.quantity, ol.quantity_unit
FROM order_lots ol
JOIN orders o ON o.id = ol.order_id
LEFT JOIN customers c ON c.id = o.customer_id
WHERE ol.lot_id = (SELECT id FROM lots WHERE code = $1);
```

Both queries hit indexed columns and require no scans.

## 7. HACCP integration

The HACCP plan is stored as a set of `sops` rows tagged in `metadata` as
HACCP-required. `sop_executions` is the run record. `haccp_checks` are the
CCP measurements taken during those runs. The chain is:

```
sops (HACCP plan, v3) ──> sop_executions (run on batch B, 2026-05-12)
                              │
                              └──> haccp_checks (autoclave_temp_min_15min, target/actual/pass)
```

Linking `haccp_checks.sop_execution_id` to `sop_executions(id)` ties every
measurement back to the exact SOP version it was taken against, which is the
auditor's first question.

## 8. Strain lineage as DAG (AICL parity)

The pattern is identical to `aicl.Conversation`:

- AICL: `AICLMessage.parent_message_id -> AICLMessage.id`
- Strains: `strains.parent_strain_id -> strains.id`

Walking ancestry uses the same algorithm as `Conversation.thread_ending_at`:
start at a strain, follow `parent_strain_id` upward, collect until NULL, reverse.

Hybrids get one extra edge via `secondary_parent_strain_id`. This keeps the
common case (asexual line) cheap and only pays the DAG cost when a hybrid is
declared. Cycle prevention is a CHECK at insert time: a strain cannot be its
own ancestor. We enforce this in application code (a recursive CTE check at
insert), not in a trigger, because the recursive CTE is easier to read and to
test.

## 9. Decisions deferred (not designed today)

1. Multi-tenancy / workspace scoping. Migration 003 has `workspace_id` on
   many tables. This ops layer does not, on purpose. Until a second tenant
   is real (the platform plan calls for it but it is not committed), we keep
   the table count and the join cost down. When we go multi-tenant, we add
   `workspace_id UUID NOT NULL` with a default backfill of the SWM workspace
   and partial-unique-index everything by workspace. That migration is on the
   roadmap, not on the critical path.
2. The `provenance_anchors` table and the on-chain anchoring job. We are
   only storing `content_hash` today. Designing the anchor table now would
   require committing to a chain (Base L2 is the plan), a contract, and a
   batching strategy. None of those are decided.
3. Sensor ingestion shape. `environmental_readings` is the destination, not
   the ingestion path. The MQTT/HTTP gateway that feeds it is a separate
   service spec.
4. Cost-of-goods accounting per lot. Yields, labor, electricity, and
   substrate cost per lot belong in a future `lot_costs` table tied 1:1 to
   `lots`. The shape depends on how the Treasury / accounting integration
   lands; deferring keeps this migration self-contained.
5. A `users` / `operators` table. `operator_id` and `recorded_by` are TEXT
   today because the identity story across SWM, the foundry platform, and
   the lab is still in flux. We will FK these once the identity model is
   stable. Until then, indexed TEXT columns serve.
6. Foreign data wrapper from `customers` and `orders` to Shopify. Today
   they are local tables. The mirror job is a separate concern.

## 10. Out of scope (explicitly)

- Routes, services, REST or GraphQL surfaces. This is the data layer.
- Next.js, dashboards, forms. None.
- Smart contracts, RPC, on-chain anchoring code.
- A `runtime/` or `aicl/` change. This layer plugs in next to those, not
  through them.
- The existing `strains` table from migration 003 is not modified. The new
  `ops.strains` table coexists. Convergence is a later refactor.
