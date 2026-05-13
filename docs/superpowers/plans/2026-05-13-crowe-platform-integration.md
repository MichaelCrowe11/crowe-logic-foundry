# Crowe Logic Platform Integration Plan

Status: draft, 2026-05-13
Owner: Michael Crowe
Scope: four workstreams that wire the existing Crowe Logic surfaces into one end-to-end cultivation + research lab platform.

This plan is the execution target for future sessions. Each workstream is a sequence of phased tasks with file paths, effort estimates, dependencies, and a definition of done. Read the executive summary, then jump into the workstream you are executing.

## 0. Executive summary

**Goal.** Unify the scattered Crowe Logic surfaces (cultivation OS, voice agents, storefront, foundry CLI, synapse runtime, AICL, model gateway) into one end-to-end platform that runs Southwest Mushrooms as a regulated cultivation operation and supports the research-lab side (strains, trials, compound discovery).

**Foundation that already exists** (do not rebuild):
* `crowe_synapse_engine/` runtime, AICL protocol, DSL, MemoryStore, CLI
* `crowe_synapse_engine/http/` FastAPI service (just shipped)
* `crowe_synapse_engine/ops/` Pydantic models + `migrations/009_ops_layer.sql` (just shipped)
* `docs/AICL_SPEC.md`, `docs/OPS_SCHEMA.md`
* `~/Projects/crowe-logic-ai/` Next.js cultivation OS (Mycelium EI Engine rebrand in flight)
* `config/agent_config.py` + `config/models.extra.json` model catalog (44+ entries)
* 19 YAML agents under `agents/`
* Voice agents in production (Mike, Big O Tires)

**The four workstreams**:

| # | Workstream | Effort | Customer value | Priority |
|---|---|---|---|---|
| 1 | Mycelium EI Engine completion (Tasks 3-17 from existing plan) | 11-14 hrs | Welcome flow lands for $499 bundle buyers | HIGH (unblocks paying customers) |
| 2 | Operations layer + HACCP/FSMA 204 routes | 4-6 weeks | Regulated cultivation, retail-ready compliance | HIGH (B2B distribution unlock) |
| 3 | Blockchain provenance (Base L2 + IPFS) | 2-3 days | Customer-facing transparency, retail differentiation | MEDIUM (differentiation) |
| 4 | Research lab surfaces | 4-6 weeks | Strain library, trials, compound discovery integration | MEDIUM (parallel-track value) |

**Total effort**: 10-14 focused weeks across workstreams, achievable in 12-16 calendar weeks given operational interruptions.

**Recommended execution order**: 1 → 2 → 3 → 4. Workstream 1 is shortest and unblocks revenue. Workstream 2 is the spine that 3 and 4 plug into. Workstream 3 is a differentiator on top of 2. Workstream 4 is parallelizable with 3 once 2 is partially built.

**Single highest-leverage workstream**: Workstream 2. Everything downstream (regulatory readiness, blockchain, lab) attaches to the operations data model. Without it, the platform stays a collection of features instead of a system.

## 1. Workstream 1: Mycelium EI Engine completion

### Status

Tasks 0-2 of `~/Projects/crowe-logic-ai/docs/superpowers/plans/2026-05-10-mycelium-ei-engine-welcome.md` are committed. Tasks 3-17 are deferred. The welcome flow for $499 bundle buyers does not exist in code yet, even though the schema migration landed on 2026-05-10.

### Tasks (cite the existing plan; do not re-spec)

* [ ] **Task 3**: Magic-link library (mint, verify, hash) at `lib/mycelium/magic-link.ts`. JOSE for JWT, 72h TTL, hash-on-mint store in `mycelium_magic_links` table.
* [ ] **Task 4**: Welcome email template at `lib/mycelium/welcome-email.tsx`. Resend domain `southwestmushrooms.com` (already verified).
* [ ] **Task 5**: Welcome data loader at `lib/mycelium/welcome-loader.ts`. Server fn that pulls the bundle metadata + customer name.
* [ ] **Task 6**: Webhook integration in `app/api/webhooks/stripe/route.ts`. After the `user_subscriptions` insert, mint the magic link and send the email.
* [ ] **Task 7**: `/welcome` route (server component, token exchange) at `app/welcome/page.tsx`. Validates the token, marks it used, logs the customer in.
* [ ] **Task 8**: `HeroVideo` component (loop on landing pre-auth).
* [ ] **Task 9**: `Shelf` component (library card grid for the bundle assets).
* [ ] **Task 10**: `EngineSeed` component (chat starter with personalized seed prompt).
* [ ] **Task 11**: `RoadmapTease`, `PasswordOptional`, `WelcomeFooter` components.
* [ ] **Task 12**: `/welcome` support routes (loading, error, lost-token recovery).
* [ ] **Task 13**: `/admin/orders` page (ops monitoring for fulfillment + magic-link issuance state).
* [ ] **Task 14**: `/admin/health` page (env-var presence, recent error counts).
* [ ] **Task 15**: `/admin/library-files` page (storage browser for bundle assets).
* [ ] **Task 16**: Sentry wiring (mandatory before deploy). `lib/sentry.ts`, instrument webhook + welcome route.
* [ ] **Task 17**: Test-mode flow + manual QA + Railway deploy.

### Dependencies

* Railway env vars set: `AZURE_ANTHROPIC_ENDPOINT`, `AZURE_ANTHROPIC_API_KEY`, `AZURE_CORE_ENDPOINT`, `AZURE_CORE_API_KEY` (per the EI Engine diagnosis report). **This unblocks the site before any of Tasks 3-17 ship.**
* Supabase migration `017_mycelium_ei_engine.sql` confirmed applied to production.

### Definition of done

A $499 bundle purchase from `southwestmushrooms.com` triggers the Stripe webhook, the webhook mints a JWT magic link, the email arrives via Resend with the welcome URL, clicking the URL logs the customer in and lands them on `/welcome` with the seeded chat + library shelf. End-to-end QA on three test orders.

### Effort estimate

11-14 hours of focused work (per the existing plan). Single session, one operator.

### Smoke test

```
1. Stripe test mode purchase against the $499 payment link
2. Inspect webhook log: magic_link_id present
3. Confirm Resend delivery (Resend dashboard)
4. Open welcome URL; confirm logged-in state + content
```

## 2. Workstream 2: Operations layer + HACCP/FSMA 204

### Status

Schema designed (`docs/OPS_SCHEMA.md`), SQL migration written (`migrations/009_ops_layer.sql`), Pydantic models in place (`crowe_synapse_engine/ops/models.py`), smoke tests pass. The UI, business logic, and reports on top of the schema are the remaining work.

### Phase 1: Batch lifecycle CRUD UI

* [ ] **1.1** Apply `009_ops_layer.sql` to dev Neon. Verify with `\dt` and `\dT+ batch_stage`.
* [ ] **1.2** Add a server module at `~/Projects/crowe-logic-ai/lib/ops/batches.ts` with `createBatch`, `listBatches`, `getBatch`, `updateBatchStage`. Use the Supabase JS client. Mirror the Pydantic shape.
* [ ] **1.3** New route group `app/ops/(...)`. Pages: `app/ops/batches/page.tsx` (list), `app/ops/batches/new/page.tsx` (create), `app/ops/batches/[id]/page.tsx` (detail).
* [ ] **1.4** Forms use Server Actions; revalidate `/ops/batches` on mutation.
* [ ] **1.5** Add `app/ops/lots/page.tsx` and `app/ops/lots/[id]/page.tsx`.
* [ ] **1.6** Auth gate: only operators with role `ops.write` can mutate. Read access for `ops.read`.

Effort: 1 week. Definition of done: full batch + lot CRUD with role-gated UI on staging.

### Phase 2: HACCP CCP entry forms

* [ ] **2.1** Mobile-first form components in `components/ops/HACCPCheckForm.tsx`. One CCP per form load, optimized for tablet on the cleanroom rail.
* [ ] **2.2** Server action `app/ops/haccp/actions.ts` `recordHaccpCheck(input)`. Validates against the Pydantic shape (TS Zod schema mirroring `HACCPCheck`).
* [ ] **2.3** `app/ops/haccp/page.tsx` shows pending checks for the active shift, completed-today summary, and any `FAIL`/`DEVIATION` rows that need follow-up.
* [ ] **2.4** SOP-execution backlink: a check row knows which SOP execution produced it.

Effort: 4-5 days. Definition of done: tablet-friendly form posts a row, dashboard shows it, fail/deviation flow surfaces corrective-action requirement.

### Phase 3: FSMA 204 tracking event UI + report export

* [ ] **3.1** Capture forms for the five CTEs: `app/ops/events/receive/page.tsx`, `grow/page.tsx`, `harvest/page.tsx`, `transform/page.tsx`, `ship/page.tsx`. Each pre-fills the KDEs it knows from the linked batch/lot.
* [ ] **3.2** Lot-detail page (Phase 1.5) surfaces the chronological tracking_events list.
* [ ] **3.3** Export endpoint `app/api/ops/fsma-export/route.ts`. Accepts `lot_id` or `batch_id`, returns CSV per FDA's expected schema (CTEs as rows, KDEs as columns).
* [ ] **3.4** Admin-only `/ops/recall` page: enter a contaminated lot id, get the forward graph (orders that received it) and backward graph (which batches and substrate lots fed it).

Effort: 1 week. Definition of done: a recall query returns the full forward + backward graph in under one SQL hop. CSV export validated against an FDA template.

### Phase 4: Environmental sensor ingestion

* [ ] **4.1** Decide intake shape: webhook + auth header (sensor pushes) or pull from a hub (Home Assistant / Node-RED). Default recommendation: webhook intake.
* [ ] **4.2** Endpoint `app/api/ops/env-readings/route.ts` POST. Accepts `{batch_id, recorded_at, temp_c, humidity_pct, co2_ppm, light_lux, source}`. Auth via shared HMAC.
* [ ] **4.3** Backfill helper `scripts/env-ingest.ts` for replaying log files.
* [ ] **4.4** Realtime view on `app/ops/batches/[id]/page.tsx` showing last-24h env curves. Use a lightweight chart lib (recharts or visx).
* [ ] **4.5** Alert hook: if `co2_ppm` > threshold for >N minutes, post AICL `UNCERTAIN` event to the cultivation orchestrator agent.

Effort: 1 week. Definition of done: a real sensor (or a curl script) posts 100 readings, the chart renders, the alert fires when thresholds are exceeded.

### Phase 5: Contamination event capture + Crowe Vision

* [ ] **5.1** Photo upload component `components/ops/ContaminationCapture.tsx`. Drops the photo in Supabase Storage, opens a new `contamination_events` row.
* [ ] **5.2** Pass the photo through `app/api/crowe-vision/analyze` (already exists in the EI Engine codebase) to pre-fill `contamination_type` and `severity`. Operator confirms or overrides.
* [ ] **5.3** Link `contained_action` to a SOP execution (drop-down of relevant SOPs).
* [ ] **5.4** Dashboard surface on `/ops/batches/[id]` shows the contamination history with thumbnails.

Effort: 4-5 days. Definition of done: upload a real photo of contamination, get an AI-suggested type, save the event, see it on the batch page. Requires Crowe Vision env vars on Railway (workstream 1 dependency).

### Phase 6: Recall protocol + auditor export

* [ ] **6.1** `/ops/recall/[lot_id]/page.tsx` with forward and backward graphs (already partly in Phase 3.4).
* [ ] **6.2** One-click PDF export for FDA inspection: lot detail, batch lineage, all CTEs, all HACCP checks for the cycle, all contamination events. Use `@react-pdf/renderer` or similar.
* [ ] **6.3** Audit log of who accessed which recall page, when. Append-only into a new `recall_access_log` table.

Effort: 1 week. Definition of done: pick any shipped lot from the last 90 days, generate the PDF in under 5 seconds, hand to an FDA inspector.

### Dependencies between phases

Phase 1 must land first. Phases 2, 3, 4 can parallelize after Phase 1. Phase 5 needs Phase 1 + the Crowe Vision env vars from Workstream 1. Phase 6 needs Phase 3.

### Cross-workstream contract

The HTTP service in `crowe_synapse_engine/http/` is the agent-side API. The Next.js routes added here are the human-side UI. Both read and write the same Postgres tables. The agent and the UI never block on each other.

## 3. Workstream 3: Blockchain provenance

### Goal

Customer scans QR on package, sees the batch provenance: lab COA, fruiting-block photo, harvest timestamp, all timestamp-anchored on Base L2 so they can verify it was not edited after the fact. Customer-facing transparency without bespoke contract development.

### Architecture

* **Chain**: Base L2 (Coinbase chain, EVM-compatible, ~1 cent per write)
* **Off-chain storage**: IPFS via Pinata (Pinata is the simplest dev experience; web3.storage is an alternative)
* **Contract**: Use the existing public Ethereum Attestation Service (EAS) deployed on Base, do not write a custom contract. EAS exposes a generic `attest()` that takes a schema id + a packed payload. We define one schema for `BatchAttestation` and one for `LotAttestation`.
* **Wallet**: a single hot wallet on Base, funded with ~$10 of ETH. Private key in `~/.env.secrets`, never committed.

### Phase 1: schema + wallet

* [ ] **3.1.1** Create a dedicated Base wallet via Coinbase Wallet or viem. Fund with $10.
* [ ] **3.1.2** Register two EAS schemas on Base: `BatchAttestation(batch_id bytes32, content_hash bytes32, ipfs_cid string)` and `LotAttestation(lot_id bytes32, content_hash bytes32, ipfs_cid string)`. Record the schema UIDs.
* [ ] **3.1.3** Add env vars: `BASE_RPC_URL`, `BASE_WALLET_PRIVATE_KEY`, `EAS_BATCH_SCHEMA_UID`, `EAS_LOT_SCHEMA_UID`, `PINATA_JWT`.

Effort: 1 day. Definition of done: a manual `attest()` call from a script writes a test attestation, you can see it on basescan.org.

### Phase 2: anchoring service

* [ ] **3.2.1** New module `crowe_synapse_engine/provenance/` with `anchor.py`. Functions: `compute_batch_content_hash(batch)`, `pin_batch_artifact(batch_dict, photos: list[bytes]) -> ipfs_cid`, `attest_batch(batch_id, content_hash, ipfs_cid) -> tx_hash`.
* [ ] **3.2.2** Use `viem` (Node) or `web3.py` (Python). Recommend `web3.py` to keep the runtime monolingual.
* [ ] **3.2.3** Worker `scripts/anchor_provenance.py` that pulls every batch newer than the last-anchored watermark, anchors each, records `tx_hash` in `batches.metadata.provenance`.
* [ ] **3.2.4** Run worker as a cron (every 1 hour) on Railway or as a manual operator command.

Effort: 1 day. Definition of done: a real batch from the Postgres dev DB gets anchored, attestation visible on EAS scanner on Base.

### Phase 3: public verification page

* [ ] **3.3.1** Public route `southwestmushrooms.com/verify/[batch_id]/page.tsx`. SSR fetches:
    * `batches.content_hash` and `batches.metadata.provenance.tx_hash` from Postgres
    * The IPFS payload (COA, photos) from the CID
    * The on-chain attestation from Base via a public RPC
* [ ] **3.3.2** Render: timestamp proof, COA download, photo gallery, "verified on Base" badge with a link to the attestation.
* [ ] **3.3.3** Edge runtime; cache the on-chain read for 5 minutes.

Effort: 1 day. Definition of done: visiting `/verify/<a real batch>` renders the verification page with a working basescan link.

### Phase 4: packaging integration

* [ ] **3.4.1** QR code generation on packaging labels. Encode `https://southwestmushrooms.com/verify/{batch_id}`.
* [ ] **3.4.2** Wire QR into the existing Lulu/print pipeline used for bundle assets, or onto a small label printer at the packing station.

Effort: 0.5 days. Definition of done: a printed QR on a real package scans to a working verify page.

### Phase 5: strain lineage anchoring (optional v2)

* [ ] **3.5.1** Same shape: an EAS schema `StrainAttestation(strain_id, content_hash, parents)` anchors the strain lineage DAG.
* [ ] **3.5.2** Verify page `/verify-strain/[strain_id]` walks the lineage with each ancestor's on-chain attestation.

Effort: 0.5 days. Defer until customers ask.

### Total effort

2-3 days for Phases 1-4. Phase 5 is optional. No contract development; this is glue work between Postgres, Pinata, EAS, and Next.js.

### Smoke test

```
1. Run anchor worker against a single dev batch
2. Confirm Pinata pin shows the artifact
3. Confirm Base attestation visible via easscan.org
4. Open /verify/<batch_id> and verify the page renders all three pieces
```

## 4. Workstream 4: Research lab surfaces

### Phase 1: Strain library UI

* [ ] **4.1.1** Route group `app/lab/strains/`. List, detail, lineage tree visualization.
* [ ] **4.1.2** Server module `lib/lab/strains.ts` against the `strains` table from `009_ops_layer.sql`.
* [ ] **4.1.3** Lineage tree component `components/lab/StrainLineageTree.tsx`. Walks `parent_strain_id` + `secondary_parent_strain_id` recursively. Use a force-directed graph or a simple indented list for the v1.
* [ ] **4.1.4** Search + filter by genus/species/origin.

Effort: 1 week. Definition of done: 20 strains imported from existing notes, lineage tree renders, search works.

### Phase 2: Trial design + execution tracking

* [ ] **4.2.1** Route group `app/lab/trials/`. List, new-trial form, detail.
* [ ] **4.2.2** Form supports: name, hypothesis, primary strain, expected duration, treatments array (`{name, dose, applied_at, batch_ids}`).
* [ ] **4.2.3** Detail view shows linked batches with their stage transitions overlaid on a timeline.
* [ ] **4.2.4** Outcome capture: free text + structured metrics into `trials.payload`.

Effort: 1 week. Definition of done: a real ongoing trial (e.g. substrate-mix comparison) entered and tracked end-to-end.

### Phase 3: Sequencing data ingestion

* [ ] **4.3.1** Object storage bucket on Supabase Storage: `sequencing/{strain_id}/{run_id}/{file}.fastq.gz`.
* [ ] **4.3.2** Metadata table `sequencing_runs` (deferred to a future migration; not in 009).
* [ ] **4.3.3** Upload UI: drag-drop a FASTA/FASTQ file, fill metadata, save.
* [ ] **4.3.4** Display: file list on strain detail page with size, upload date, optional viewer link.

Effort: 4-5 days. Definition of done: upload one real FASTA, see it on the strain page, download works.

### Phase 4: Compound discovery integration

* [ ] **4.4.1** The existing `~/drug_discovery/`, `~/neurochem_discovery/`, `~/compound_discovery/` codebases get surfaced as synapse agents.
* [ ] **4.4.2** Write `agents/compound-discovery.yaml` for each codebase. Model `crowelm-talon`. Tools point to the existing CLI entry points (wrap via the synapse tool registry).
* [ ] **4.4.3** New CLI subgroup `crowe-logic synapse lab` exposes compound queries through the agent.

Effort: 1 week. Definition of done: a query like "find candidate ligands for serotonin 5-HT2A receptor with low BBB permeability" routes through the agent and returns results.

### Phase 5: Publication pipeline

* [ ] **4.5.1** Export endpoint that bundles a trial's data + outcome + supporting figures into a manuscript draft (`docx` or `tex`).
* [ ] **4.5.2** Citation handling: the existing `crowe-research` CLI already does this; reuse its bibliography module.

Effort: 4-5 days. Definition of done: one completed trial exports a publishable draft.

### Phase 6: Regulatory compliance for psychedelics

* [ ] **4.6.1** This is Crowe Psychedelics scope; see `~/crowe-psychedelics/` and the related memory entries.
* [ ] **4.6.2** Compliance is jurisdictional (DEA Schedule I licensing if applicable, IRB if humans involved, IACUC if animals).
* [ ] **4.6.3** Schema additions: `controlled_substance_log` table tracking every gram. New migration, not in 009.

Effort: 2-3 weeks. Highly variable; gated on legal counsel and licensing path. Treat as a plan-of-plan, not a code workstream.

### Cross-workstream contract

Phase 4 reuses the synapse agent runtime + AICL. Every compound discovery call emits AICL `DELEGATE` to the lab agent and `REPORT` with evidence (paper DOIs, binding affinity scores, etc.) back. The trace is persisted in MemoryStore the same way cultivation traces are.

## 5. Critical path

```
[Railway env fix (operator action)]
       │
       ▼
[Workstream 1: Mycelium Tasks 3-17]            ← unblocks customers immediately
       │
       ▼
[Workstream 2 Phase 1: Batch CRUD]             ← spine for everything below
       │
       ├──► [Workstream 2 Phase 2: HACCP]
       │
       ├──► [Workstream 2 Phase 3: FSMA tracking]
       │
       ├──► [Workstream 2 Phase 4: env ingestion]
       │
       ├──► [Workstream 2 Phase 5: contamination + Vision]
       │            │
       │            ▼
       │       [Workstream 3: blockchain anchoring]
       │
       ├──► [Workstream 2 Phase 6: recall + audit export]
       │
       └──► [Workstream 4 Phase 1+2: strains + trials]
                    │
                    ├──► [Workstream 4 Phase 3: sequencing]
                    │
                    └──► [Workstream 4 Phase 4: compound discovery]
                                  │
                                  ▼
                              [Phase 5: publication]
```

## 6. Risks

1. **Operator capacity.** This is a single-operator plan. 10-14 focused weeks assumes Michael as the sole engineer plus occasional Claude sessions. Real velocity will be slower because operational fires interrupt (storefront issues, customer support, content production).
2. **Schema drift between Pydantic models and SQL.** Mitigation: a future test that introspects the live DB and compares column types against the Pydantic models. Defer to Workstream 2 Phase 1.5.
3. **Crowe Vision availability for Phase 5.** Depends on Railway env vars being set (Workstream 1 prereq). If those slip, Phase 5 slips.
4. **EAS schema lock-in.** Once an attestation schema is registered on Base, changing it requires a new schema id and a coordinated migration. Get the schema right the first time. Mitigation: prototype on Base Sepolia testnet first.
5. **FDA FSMA 204 deadline movement.** Compliance date was originally 2026-01-20; the FDA extended it. Monitor for further changes; do not promise compliance to retail customers ahead of the rule taking effect.
6. **Customer expectations after the verify page lands.** Once customers see on-chain provenance for one batch, they will expect it for every batch. Plan the cron / anchoring rate accordingly. Anchoring 100 batches at $0.01 each on Base is $1; not a cost issue, but a process issue.

## 7. Definition of done for the whole plan

* [ ] A new customer can buy the $499 bundle, get the magic-link welcome, and use the platform end-to-end.
* [ ] An operator can log a batch from inoculation to harvest, complete every HACCP CCP, capture environmental data, and ship lots that link back to the originating substrate receive event.
* [ ] An FDA inspector can ask "what was lot X traceable to" and get a full audit-ready PDF in under one minute.
* [ ] A retail customer can scan a QR on a package and see the batch's on-chain attestation + COA + photos.
* [ ] A researcher can run a trial across multiple batches, ingest sequencing data for the candidate strains, and emit a manuscript draft.
* [ ] Every cross-agent exchange across the platform is captured as AICL and replayable from MemoryStore.

## 8. Where to start

If picking this up cold, do these in order:

1. Set the Railway env vars (operator action, no code).
2. Workstream 1 Task 3. Read `~/Projects/crowe-logic-ai/docs/superpowers/plans/2026-05-10-mycelium-ei-engine-welcome.md` first, then run Task 3 to completion. Each Mycelium task is 30-60 min of focused work plus a commit.
3. After Workstream 1 ships, start Workstream 2 Phase 1.

Anything else is premature. The Mycelium completion blocks revenue; everything downstream attaches to data the operations layer captures. Resist the urge to start the blockchain workstream first because it is the most novel; it has no value without the operations data feeding it.
