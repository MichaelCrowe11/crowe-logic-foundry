"""FastAPI APIRouter for the ops service.

Mountable into the existing synapse HTTP app with one line:

    from crowe_synapse_engine.ops.router import build_ops_router
    app.include_router(build_ops_router(), prefix="/ops")

The router is intentionally a builder, not a module-level singleton, so the
caller can inject a specific ``Store`` and ``AiclSink``. Without those, the
factory falls back to ``get_default_store()`` and a no-op sink, which is
what local dev and CI use.

Authentication: this router does NOT enforce auth itself. The expectation
is that the host app mounts it behind whatever dependency the rest of the
HTTP surface uses (the parallel-session work in
``crowe_synapse_engine/http/server.py`` is adding HMAC bearer-token auth
that this router will inherit through ``app.include_router(...,
dependencies=[Depends(require_bearer)])``).

Errors translate to HTTP as follows:

* ``LookupError`` -> 404
* ``ValueError`` -> 409 (resource already exists, validation)
* ``IllegalStageTransition`` -> 422
* Anything else propagates as 500 (the host app's exception handler).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

try:
    from fastapi import APIRouter, HTTPException
    from pydantic import BaseModel, Field
except ImportError as exc:  # pragma: no cover - dep gating
    raise ImportError(
        "FastAPI + Pydantic are required for crowe_synapse_engine.ops.router. "
        "Install with: pip install 'crowe-logic[http]'"
    ) from exc

from crowe_synapse_engine.aicl import AICLMessage
from crowe_synapse_engine.ops.models import (
    Batch,
    BatchStage,
    ContaminationEvent,
    ContaminationType,
    EnvironmentalReading,
    HACCPCheck,
    HACCPResult,
    Lot,
    Severity,
    Strain,
    TrackingEvent,
)
from crowe_synapse_engine.ops.service import OpsService, RecallTrace
from crowe_synapse_engine.ops.stage_rules import IllegalStageTransition
from crowe_synapse_engine.ops.store import Store, get_default_store


# ── Request schemas ─────────────────────────────────────────────────────


class StrainCreateRequest(BaseModel):
    name: str
    species: str
    parent_strain_id: UUID | None = None
    origin: str | None = None
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BatchCreateRequest(BaseModel):
    code: str
    strain_id: UUID
    location: str
    substrate_recipe: str | None = None
    substrate_lot_code: str | None = None
    operator_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class StageTransitionRequest(BaseModel):
    target: BatchStage
    actor_id: str | None = None
    notes: str | None = None


class HACCPCheckRequest(BaseModel):
    ccp_name: str
    target: str
    actual: str
    result: HACCPResult
    operator_id: str
    corrective_action: str | None = None


class ContaminationRequest(BaseModel):
    contamination_type: ContaminationType
    severity: Severity
    photo_url: str | None = None
    contained_action: str | None = None
    operator_id: str | None = None


class EnvironmentalRequest(BaseModel):
    temp_c: float | None = None
    humidity_pct: float | None = None
    co2_ppm: float | None = None
    light_lux: float | None = None
    source: str | None = None


class HarvestRequest(BaseModel):
    code: str
    weight_kg: float = Field(gt=0)
    grade: str = "A"
    destination: str | None = None
    actor_id: str | None = None


class ShipRequest(BaseModel):
    lot_id: UUID
    recipient: str
    destination_location: str
    quantity: float | None = Field(default=None, gt=0)
    actor_id: str | None = None


# ── Response schemas ────────────────────────────────────────────────────


class RecallTraceResponse(BaseModel):
    batch: Batch
    lots: list[Lot]
    tracking_events: list[TrackingEvent]
    contamination_events: list[ContaminationEvent]
    haccp_checks: list[HACCPCheck]
    aicl_messages: list[dict[str, Any]]

    @classmethod
    def from_trace(cls, trace: RecallTrace) -> RecallTraceResponse:
        return cls(
            batch=trace.batch,
            lots=trace.lots,
            tracking_events=trace.tracking_events,
            contamination_events=trace.contamination_events,
            haccp_checks=trace.haccp_checks,
            aicl_messages=[m.to_dict() for m in trace.aicl_messages],
        )


# ── Router builder ──────────────────────────────────────────────────────


def build_ops_router(
    *,
    store: Store | None = None,
    aicl_sink: Callable[[AICLMessage], None] | None = None,
    agent_name: str = "ops-router",
) -> APIRouter:
    """Construct a router with the given store and AICL sink.

    Defaults: in-memory store, no-op sink. Callers in production should
    pass a Postgres-backed store and an AICL sink that persists to the
    MemoryStore session log.
    """
    if store is None:
        store = get_default_store()
    service = OpsService(store, aicl_sink=aicl_sink, agent_name=agent_name)

    router = APIRouter(tags=["ops"])

    def _wrap_value_error(exc: Exception) -> HTTPException:
        # 409 for "already exists", 422 for IllegalStageTransition.
        if isinstance(exc, IllegalStageTransition):
            return HTTPException(status_code=422, detail=str(exc))
        return HTTPException(status_code=409, detail=str(exc))

    # ── Strains ─────────────────────────────────────────────────────

    @router.post("/strains", response_model=Strain)
    def create_strain(req: StrainCreateRequest) -> Strain:
        strain, _ = service.register_strain(**req.model_dump())
        return strain

    @router.get("/strains/{strain_id}", response_model=Strain)
    def get_strain(strain_id: UUID) -> Strain:
        strain = store.get_strain(strain_id)
        if strain is None:
            raise HTTPException(status_code=404, detail=f"strain {strain_id} not found")
        return strain

    # ── Batches ─────────────────────────────────────────────────────

    @router.post("/batches", response_model=Batch)
    def create_batch(req: BatchCreateRequest) -> Batch:
        try:
            batch, _ = service.create_batch(**req.model_dump())
        except ValueError as exc:
            raise _wrap_value_error(exc) from exc
        return batch

    @router.get("/batches", response_model=list[Batch])
    def list_batches(archived: bool = False) -> list[Batch]:
        return store.list_batches(archived=archived)

    @router.get("/batches/{batch_id}", response_model=Batch)
    def get_batch(batch_id: UUID) -> Batch:
        batch = store.get_batch(batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail=f"batch {batch_id} not found")
        return batch

    @router.post("/batches/{batch_id}/stage", response_model=Batch)
    def advance_stage(batch_id: UUID, req: StageTransitionRequest) -> Batch:
        try:
            batch, _ = service.advance_stage(
                batch_id, req.target, actor_id=req.actor_id, notes=req.notes
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except IllegalStageTransition as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return batch

    @router.post("/batches/{batch_id}/haccp", response_model=HACCPCheck)
    def record_haccp(batch_id: UUID, req: HACCPCheckRequest) -> HACCPCheck:
        try:
            check, _ = service.record_haccp_check(batch_id=batch_id, **req.model_dump())
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return check

    @router.post(
        "/batches/{batch_id}/contamination",
        response_model=ContaminationEvent,
    )
    def record_contamination(
        batch_id: UUID, req: ContaminationRequest
    ) -> ContaminationEvent:
        try:
            event, _ = service.record_contamination(
                batch_id=batch_id, **req.model_dump()
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return event

    @router.post(
        "/batches/{batch_id}/environmental",
        response_model=EnvironmentalReading,
    )
    def record_environmental(
        batch_id: UUID, req: EnvironmentalRequest
    ) -> EnvironmentalReading:
        try:
            reading, _ = service.record_environmental(
                batch_id=batch_id, **req.model_dump()
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return reading

    @router.post("/batches/{batch_id}/harvest", response_model=Lot)
    def harvest(batch_id: UUID, req: HarvestRequest) -> Lot:
        try:
            lot, _ = service.harvest_to_lot(batch_id=batch_id, **req.model_dump())
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except IllegalStageTransition as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return lot

    @router.get(
        "/batches/{batch_id}/recall",
        response_model=RecallTraceResponse,
    )
    def recall_trace(batch_id: UUID) -> RecallTraceResponse:
        try:
            trace = service.recall_trace_forward(batch_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return RecallTraceResponse.from_trace(trace)

    # ── Lots ────────────────────────────────────────────────────────

    @router.get("/lots/{lot_id}", response_model=Lot)
    def get_lot(lot_id: UUID) -> Lot:
        lot = store.get_lot(lot_id)
        if lot is None:
            raise HTTPException(status_code=404, detail=f"lot {lot_id} not found")
        return lot

    @router.post("/lots/{lot_id}/ship", response_model=TrackingEvent)
    def ship_lot(lot_id: UUID, req: ShipRequest) -> TrackingEvent:
        if req.lot_id != lot_id:
            raise HTTPException(
                status_code=400,
                detail="lot_id in body must match lot_id in path",
            )
        try:
            event, _ = service.ship_lot(
                lot_id=lot_id,
                recipient=req.recipient,
                destination_location=req.destination_location,
                quantity=req.quantity,
                actor_id=req.actor_id,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return event

    return router
