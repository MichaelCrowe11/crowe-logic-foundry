---
title: Ops Service API
status: v0.1 (in-memory backend), Workstream 2 Phase 1
date: 2026-05-13
owner: Michael Crowe
implementation: crowe_synapse_engine/ops/
---

# Ops Service API

The cultivation lifecycle, FSMA 204 traceability, HACCP logging, and recall
traversal, all served by one ``OpsService`` and consumable from three surfaces:

1. Python (``from crowe_synapse_engine.ops import OpsService, InMemoryStore``)
2. HTTP REST (mount ``build_ops_router()`` into a FastAPI app)
3. CLI (``crowe-logic ops <command>``)

Every write produces a persisted row AND an ``AICLMessage`` describing the
action with timestamp, operator, and evidence references. The audit chain
is the differentiator: FDA, retail buyers, and downstream apps consume it
without re-deriving state.

## Architecture

```
                       OpsService
                           |
       +-------------------+-------------------+
       |                   |                   |
       v                   v                   v
   Store proto         stage_rules            aicl
       |              (state machine)    (audit trail)
       v
 InMemoryStore  <-- default, in-process
 PostgresStore  <-- WORKSTREAM 2 PHASE 2 (TODO)
```

The ``Store`` protocol is the seam. Tests, dev, and the first demo run
against ``InMemoryStore``; production swaps in a Postgres adapter that reads
and writes the tables defined in ``migrations/009_ops_layer.sql`` (the
migration already exists; the adapter does not).

## Mount points

### Python

```python
from crowe_synapse_engine.ops import InMemoryStore, OpsService

store = InMemoryStore()
service = OpsService(store, aicl_sink=lambda m: print(m.subject))

strain, _ = service.register_strain(name="Lions Mane CL-001", species="Hericium erinaceus")
batch, _ = service.create_batch(code="LM-001", strain_id=strain.id, location="Room B")
service.advance_stage(batch.id, BatchStage.COLONIZATION)
trace = service.recall_trace_forward(batch.id)
```

### HTTP

```python
from fastapi import FastAPI
from crowe_synapse_engine.ops.router import build_ops_router

app = FastAPI()
app.include_router(build_ops_router(), prefix="/ops")
```

Or to share a store and AICL sink with another part of the app:

```python
from crowe_synapse_engine.ops import InMemoryStore

store = InMemoryStore()
aicl_log = []
app.include_router(
    build_ops_router(store=store, aicl_sink=aicl_log.append),
    prefix="/ops",
)
```

Run locally:

```bash
uvicorn crowe_synapse_engine.http:app --reload
# Then visit http://localhost:8000/docs for the OpenAPI page.
```

### CLI

The ``crowe-logic ops`` subgroup is registered automatically.

```bash
crowe-logic ops register-strain "Lions Mane CL-001" "Hericium erinaceus"
crowe-logic ops create-batch LM-001 <strain_id> "Room B" --operator op-mike
crowe-logic ops advance <batch_id> colonization
crowe-logic ops haccp <batch_id> autoclave "121C/15min" "122C/17min" pass --operator op-mike
crowe-logic ops harvest <batch_id> LM-001-F1 1.2 --grade A
crowe-logic ops ship <lot_id> "Whole Foods AZ" "Phoenix, AZ"
crowe-logic ops recall <batch_id>
```

The CLI uses a process-local InMemoryStore, so each invocation starts
empty. This is intentional for demos. For real persistence, hit the HTTP
endpoint or wait for the Postgres adapter.

## REST endpoints

All endpoints below assume the router is mounted at ``/ops``.

| Method | Path | Purpose |
|--------|------|---------|
| POST | ``/ops/strains`` | Register a strain |
| GET  | ``/ops/strains/{strain_id}`` | Fetch a strain |
| POST | ``/ops/batches`` | Inoculate a new batch (FSMA RECEIVE event) |
| GET  | ``/ops/batches`` | List batches (``?archived=true`` for archived) |
| GET  | ``/ops/batches/{batch_id}`` | Fetch a batch |
| POST | ``/ops/batches/{batch_id}/stage`` | Advance the batch stage |
| POST | ``/ops/batches/{batch_id}/haccp`` | Record a HACCP CCP check |
| POST | ``/ops/batches/{batch_id}/contamination`` | Record a contamination event |
| POST | ``/ops/batches/{batch_id}/environmental`` | Record an environmental reading |
| POST | ``/ops/batches/{batch_id}/harvest`` | Harvest a batch into a Lot (FSMA HARVEST event) |
| GET  | ``/ops/batches/{batch_id}/recall`` | FSMA 204 forward recall trace |
| GET  | ``/ops/lots/{lot_id}`` | Fetch a lot |
| POST | ``/ops/lots/{lot_id}/ship`` | Record a shipment (FSMA SHIP event) |

## Status codes

| Code | Meaning |
|------|---------|
| 200 | OK |
| 400 | Path/body id mismatch |
| 404 | Resource not found |
| 409 | Conflict (duplicate code, strain not registered) |
| 422 | Validation failure (illegal stage transition, HACCP missing corrective action) |

## Stage machine

| From | Allowed targets |
|------|-----------------|
| ``inoculation`` | ``colonization``, ``failed``, ``discarded`` |
| ``colonization`` | ``fruiting``, ``failed``, ``discarded`` |
| ``fruiting`` | ``harvested``, ``failed``, ``discarded`` |
| ``harvested`` | (terminal) |
| ``failed`` | (terminal) |
| ``discarded`` | (terminal) |

To inspect rules from code:

```python
from crowe_synapse_engine.ops import allowed_next, BatchStage
allowed_next(BatchStage.FRUITING)
# frozenset({BatchStage.HARVESTED, BatchStage.FAILED, BatchStage.DISCARDED})
```

Rules live in ``crowe_synapse_engine/ops/stage_rules.py``. Adjust there
when your operation grows (secondary flush as a distinct stage, spent
substrate composting as a sink, etc.).

## AICL audit trail

Every write emits an ``AICLMessage`` (``act=COMMIT``) with:

* ``from_agent``: the ``agent_name`` configured on the service (default
  ``"ops-service"`` in Python, ``"ops-router"`` from the HTTP layer)
* ``subject``: human-readable one-liner (e.g.
  ``"batch LM-001 stage: inoculation to colonization"``)
* ``payload``: machine-readable JSON of the action and its parameters
* ``evidence``: references like ``"tracking_event:<uuid>"`` so audit
  consumers can join back to persisted rows
* ``parent_message_id`` (optional): threads this write under a higher
  conversation (a voice command, a UI submit, a workflow run)

The sink is a plain callable. Wire it to whatever audit pipe you want:
the synapse runtime's ``RuntimeChunk`` stream, the ``MemoryStore`` SQLite
audit table, a Slack webhook, a Sentry breadcrumb. The service does not
know or care.

## End-to-end demo (Python)

```python
from crowe_synapse_engine.ops import (
    BatchStage, ContaminationType, HACCPResult, InMemoryStore, OpsService, Severity,
)

aicl = []
service = OpsService(InMemoryStore(), aicl_sink=aicl.append)

strain, _ = service.register_strain(name="Lions Mane CL-001", species="Hericium erinaceus")
batch, _ = service.create_batch(code="LM-W19-003", strain_id=strain.id, location="Room B", operator_id="op-mike")
service.record_haccp_check(batch_id=batch.id, ccp_name="autoclave_temp",
                            target="121C/15min", actual="122C/17min",
                            result=HACCPResult.PASS, operator_id="op-mike")
service.advance_stage(batch.id, BatchStage.COLONIZATION, actor_id="op-mike")
service.advance_stage(batch.id, BatchStage.FRUITING, actor_id="op-mike")
service.record_contamination(batch_id=batch.id, contamination_type=ContaminationType.TRICHODERMA,
                              severity=Severity.LOW, operator_id="op-mike",
                              contained_action="quarantined block 3")
service.advance_stage(batch.id, BatchStage.HARVESTED, actor_id="op-mike")
lot, _ = service.harvest_to_lot(batch_id=batch.id, code="LM-W19-003-F1",
                                 weight_kg=1.2, grade="A", actor_id="op-mike")
service.ship_lot(lot_id=lot.id, recipient="Whole Foods AZ",
                  destination_location="Phoenix, AZ", actor_id="op-mike")

trace = service.recall_trace_forward(batch.id)
# trace.tracking_events has receive, 3x grow (one per stage transition), harvest, ship
# trace.haccp_checks has the autoclave PASS check
# trace.contamination_events has the trichoderma event
# aicl has 10 messages logging every action above
```

## Operator queue (to move beyond in-memory)

1. Apply migration: ``psql $NEON_DATABASE_URL < migrations/009_ops_layer.sql``
2. Implement ``PostgresStore`` against the migration tables (Workstream 2 Phase 2)
3. Set ``CROWE_OPS_STORE=postgres`` + ``DATABASE_URL`` so
   ``get_default_store()`` picks it up
4. Wire ``aicl_sink`` to ``MemoryStore.record_aicl_message`` so audit
   trail persists to the existing SQLite memory layer

After those four steps, every cultivation event in production is logged,
queryable, and recall-traceable.

## What attaches to this next

* **Voice-first ops agent** (Workstream 2 Phase 3): synapse agent with
  tools that call this service. Mike voice confirmation loop on every
  write. AICL trail captures the actual voice command as parent message.
* **Blockchain provenance** (Workstream 3): when a lot ships, hash the
  AICL conversation + photo + COA, write the hash to Base L2 via EAS,
  store the IPFS CID on the Lot row. ``recall_trace_forward()`` becomes
  the on-chain attestation payload.
* **Cultivation OS UI** (in ``crowe-logic-ai``): the
  ``/ops/batches/...`` routes are designed to be consumed directly by
  Next.js server components. Each Pydantic model has a Zod mirror you
  can generate via ``datamodel-codegen``.

## Schema and design

* SQL: ``migrations/009_ops_layer.sql``
* Pydantic: ``crowe_synapse_engine/ops/models.py``
* Design rationale: ``docs/OPS_SCHEMA.md``
* Stage rules: ``crowe_synapse_engine/ops/stage_rules.py`` (review when
  your operation evolves)

## Tests

```bash
.venv/bin/python -m pytest tests/test_ops_store.py tests/test_ops_service.py tests/test_ops_router.py -v
```

36 tests pass at three layers: store CRUD, service business rules, HTTP
status codes + full lifecycle.
