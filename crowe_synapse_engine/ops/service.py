"""OpsService · business logic layer for the cultivation lifecycle.

The service composes ``Store`` (persistence) + ``stage_rules`` (domain
constraints) + ``aicl`` (audit emission) into the methods the API surface,
CLI, and voice-first agent all call. Every write produces:

1. A persisted row via the configured ``Store``.
2. An ``AICLMessage`` describing what happened (``act=COMMIT``), threaded
   to a parent INTENT message when one was provided by the caller. This
   gives FSMA 204 / HACCP inspectors a replayable transcript of every
   state change with operator, timestamp, and reasoning.

Threading:

* Callers may pass ``parent_message_id`` to attach this write to a higher
  conversation (e.g. a voice command that triggered the action).
* Otherwise each write is its own thread-root.

Concurrency: callers are expected to serialise writes per-batch. The
service itself does not lock; ``InMemoryStore`` is process-local and the
Postgres adapter will rely on row-level locking when it lands.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from crowe_synapse_engine.aicl import Act, AICLMessage
from crowe_synapse_engine.ops.models import (
    Batch,
    BatchStage,
    ContaminationEvent,
    ContaminationType,
    EnvironmentalReading,
    EventType,
    HACCPCheck,
    HACCPResult,
    Lot,
    Severity,
    Strain,
    TrackingEvent,
)
from crowe_synapse_engine.ops.stage_rules import (
    IllegalStageTransition,
    is_terminal,
    validate_transition,
)
from crowe_synapse_engine.ops.store import Store, iter_recall_forward


AiclSink = Callable[[AICLMessage], None]
"""Callback invoked once per write with the emitted AICLMessage.

Implementations: the router collects these into per-session Conversations,
the CLI prints a one-line summary, tests append to a list for assertion.
"""


@dataclass
class RecallTrace:
    """Forward-recall result for FSMA 204 queries."""

    batch: Batch
    lots: list[Lot]
    tracking_events: list[TrackingEvent]
    contamination_events: list[ContaminationEvent]
    haccp_checks: list[HACCPCheck]
    aicl_messages: list[AICLMessage] = field(default_factory=list)


class OpsService:
    """Business operations for the cultivation lifecycle.

    Every write returns the persisted entity AND emits an AICL message to
    the configured sink so callers can correlate API responses with the
    audit log without re-reading from the store.
    """

    def __init__(
        self,
        store: Store,
        *,
        aicl_sink: AiclSink | None = None,
        agent_name: str = "ops-service",
    ) -> None:
        self._store = store
        self._sink: AiclSink = aicl_sink or (lambda _msg: None)
        self._agent = agent_name

    # ── Helpers ─────────────────────────────────────────────────────

    def _emit(
        self,
        subject: str,
        *,
        payload: dict[str, Any] | None = None,
        evidence: list[str] | None = None,
        parent_message_id: str | None = None,
        act: Act = Act.COMMIT,
    ) -> AICLMessage:
        msg = AICLMessage(
            act=act,
            from_agent=self._agent,
            subject=subject,
            payload=payload or {},
            evidence=evidence or [],
            parent_message_id=parent_message_id,
        )
        self._sink(msg)
        return msg

    # ── Strains ─────────────────────────────────────────────────────

    def register_strain(
        self,
        *,
        name: str,
        species: str,
        parent_strain_id: UUID | None = None,
        origin: str | None = None,
        notes: str | None = None,
        metadata: dict[str, Any] | None = None,
        parent_message_id: str | None = None,
    ) -> tuple[Strain, AICLMessage]:
        strain = Strain(
            name=name,
            species=species,
            parent_strain_id=parent_strain_id,
            origin=origin,
            notes=notes,
            metadata=metadata or {},
        )
        self._store.save_strain(strain)
        msg = self._emit(
            f"strain registered: {name}",
            payload={"strain_id": str(strain.id), "species": species},
            parent_message_id=parent_message_id,
        )
        return strain, msg

    # ── Batches ─────────────────────────────────────────────────────

    def create_batch(
        self,
        *,
        code: str,
        strain_id: UUID,
        location: str,
        started_at: datetime | None = None,
        expected_harvest_at: datetime | None = None,
        substrate_recipe: str | None = None,
        substrate_lot_code: str | None = None,
        operator_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        parent_message_id: str | None = None,
    ) -> tuple[Batch, AICLMessage]:
        if self._store.get_batch_by_code(code) is not None:
            raise ValueError(f"batch code {code!r} already exists")
        if self._store.get_strain(strain_id) is None:
            raise ValueError(f"strain {strain_id} is not registered")
        batch = Batch(
            code=code,
            strain_id=strain_id,
            location=location,
            started_at=started_at or _now_utc(),
            expected_harvest_at=expected_harvest_at,
            substrate_recipe=substrate_recipe,
            substrate_lot_code=substrate_lot_code,
            operator_id=operator_id,
            metadata=metadata or {},
        )
        self._store.save_batch(batch)
        # Inoculation also is a Critical Tracking Event under FSMA 204.
        event = TrackingEvent(
            event_type=EventType.RECEIVE,
            occurred_at=batch.started_at,
            location=location,
            batch_id=batch.id,
            product=f"substrate:{substrate_recipe or 'default'}",
            quantity=None,
            recorded_by=operator_id,
            payload={"action": "inoculation", "batch_code": code},
        )
        self._store.add_tracking_event(event)
        msg = self._emit(
            f"batch created: {code}",
            payload={
                "batch_id": str(batch.id),
                "stage": batch.stage.value,
                "location": location,
            },
            evidence=[f"tracking_event:{event.id}"],
            parent_message_id=parent_message_id,
        )
        return batch, msg

    def advance_stage(
        self,
        batch_id: UUID,
        target: BatchStage,
        *,
        actor_id: str | None = None,
        notes: str | None = None,
        parent_message_id: str | None = None,
    ) -> tuple[Batch, AICLMessage]:
        batch = self._store.get_batch(batch_id)
        if batch is None:
            raise LookupError(f"batch {batch_id} not found")
        validate_transition(batch.stage, target)
        previous = batch.stage
        batch.stage = target
        self._store.save_batch(batch)
        event = TrackingEvent(
            event_type=EventType.GROW,
            occurred_at=_now_utc(),
            location=batch.location,
            batch_id=batch.id,
            product=f"batch:{batch.code}",
            recorded_by=actor_id,
            payload={
                "action": "stage_transition",
                "previous": previous.value,
                "current": target.value,
                "notes": notes,
            },
        )
        self._store.add_tracking_event(event)
        msg = self._emit(
            f"batch {batch.code} stage: {previous.value} to {target.value}",
            payload={
                "batch_id": str(batch.id),
                "previous_stage": previous.value,
                "stage": target.value,
                "terminal": is_terminal(target),
            },
            evidence=[f"tracking_event:{event.id}"],
            parent_message_id=parent_message_id,
        )
        return batch, msg

    # ── HACCP / Contamination / Environmental ───────────────────────

    def record_haccp_check(
        self,
        *,
        batch_id: UUID,
        ccp_name: str,
        target: str,
        actual: str,
        result: HACCPResult,
        operator_id: str,
        corrective_action: str | None = None,
        parent_message_id: str | None = None,
    ) -> tuple[HACCPCheck, AICLMessage]:
        if self._store.get_batch(batch_id) is None:
            raise LookupError(f"batch {batch_id} not found")
        check = HACCPCheck(
            batch_id=batch_id,
            ccp_name=ccp_name,
            target=target,
            actual=actual,
            result=result,
            operator_id=operator_id,
            recorded_at=_now_utc(),
            corrective_action=corrective_action,
        )
        self._store.add_haccp_check(check)
        msg = self._emit(
            f"haccp {ccp_name} {result.value}: actual {actual} (target {target})",
            payload={
                "batch_id": str(batch_id),
                "ccp": ccp_name,
                "result": result.value,
                "actual": actual,
                "target": target,
                "corrective_action": corrective_action,
            },
            evidence=[f"haccp_check:{check.id}"],
            parent_message_id=parent_message_id,
        )
        return check, msg

    def record_contamination(
        self,
        *,
        batch_id: UUID,
        contamination_type: ContaminationType,
        severity: Severity,
        photo_url: str | None = None,
        contained_action: str | None = None,
        operator_id: str | None = None,
        parent_message_id: str | None = None,
    ) -> tuple[ContaminationEvent, AICLMessage]:
        batch = self._store.get_batch(batch_id)
        if batch is None:
            raise LookupError(f"batch {batch_id} not found")
        event = ContaminationEvent(
            batch_id=batch_id,
            contamination_type=contamination_type,
            severity=severity,
            detected_at=_now_utc(),
            photo_url=photo_url,
            contained_action=contained_action,
            operator_id=operator_id,
        )
        self._store.add_contamination_event(event)
        msg = self._emit(
            f"contamination {contamination_type.value} ({severity.value}) on batch {batch.code}",
            payload={
                "batch_id": str(batch_id),
                "contamination_type": contamination_type.value,
                "severity": severity.value,
                "contained_action": contained_action,
            },
            evidence=[f"contamination_event:{event.id}"],
            parent_message_id=parent_message_id,
        )
        return event, msg

    def record_environmental(
        self,
        *,
        batch_id: UUID,
        temp_c: float | None = None,
        humidity_pct: float | None = None,
        co2_ppm: float | None = None,
        light_lux: float | None = None,
        source: str | None = None,
        parent_message_id: str | None = None,
    ) -> tuple[EnvironmentalReading, AICLMessage]:
        if self._store.get_batch(batch_id) is None:
            raise LookupError(f"batch {batch_id} not found")
        reading = EnvironmentalReading(
            batch_id=batch_id,
            recorded_at=_now_utc(),
            temp_c=temp_c,
            humidity_pct=humidity_pct,
            co2_ppm=co2_ppm,
            light_lux=light_lux,
            source=source,
        )
        self._store.add_environmental_reading(reading)
        readings_summary = []
        if temp_c is not None:
            readings_summary.append(f"{temp_c}C")
        if humidity_pct is not None:
            readings_summary.append(f"{humidity_pct}% RH")
        if co2_ppm is not None:
            readings_summary.append(f"{co2_ppm}ppm CO2")
        msg = self._emit(
            f"env {source or 'sensor'}: {', '.join(readings_summary) or 'reading'}",
            payload={
                "batch_id": str(batch_id),
                "source": source,
                "temp_c": temp_c,
                "humidity_pct": humidity_pct,
                "co2_ppm": co2_ppm,
                "light_lux": light_lux,
            },
            evidence=[f"environmental_reading:{reading.id}"],
            parent_message_id=parent_message_id,
        )
        return reading, msg

    # ── Harvest & Lots ──────────────────────────────────────────────

    def harvest_to_lot(
        self,
        *,
        batch_id: UUID,
        code: str,
        weight_kg: float,
        grade: str = "A",
        destination: str | None = None,
        actor_id: str | None = None,
        parent_message_id: str | None = None,
    ) -> tuple[Lot, AICLMessage]:
        batch = self._store.get_batch(batch_id)
        if batch is None:
            raise LookupError(f"batch {batch_id} not found")
        if batch.stage not in (BatchStage.FRUITING, BatchStage.HARVESTED):
            raise IllegalStageTransition(batch.stage, BatchStage.HARVESTED)
        if self._store.get_lot_by_code(code) is not None:
            raise ValueError(f"lot code {code!r} already exists")
        now = _now_utc()
        lot = Lot(
            batch_id=batch_id,
            code=code,
            harvested_at=now,
            weight_kg=weight_kg,
            grade=grade,
            destination=destination,
        )
        self._store.save_lot(lot)
        # FSMA 204: HARVEST event for the lot.
        harvest_event = TrackingEvent(
            event_type=EventType.HARVEST,
            occurred_at=now,
            location=batch.location,
            batch_id=batch_id,
            lot_id=lot.id,
            product=f"lot:{code}",
            quantity=weight_kg,
            quantity_unit="kg",
            recorded_by=actor_id,
            payload={"grade": grade, "batch_code": batch.code},
        )
        self._store.add_tracking_event(harvest_event)
        msg = self._emit(
            f"harvest: {weight_kg}kg grade {grade} from batch {batch.code} to lot {code}",
            payload={
                "batch_id": str(batch_id),
                "lot_id": str(lot.id),
                "weight_kg": weight_kg,
                "grade": grade,
            },
            evidence=[f"tracking_event:{harvest_event.id}"],
            parent_message_id=parent_message_id,
        )
        return lot, msg

    def ship_lot(
        self,
        *,
        lot_id: UUID,
        recipient: str,
        destination_location: str,
        quantity: float | None = None,
        actor_id: str | None = None,
        parent_message_id: str | None = None,
    ) -> tuple[TrackingEvent, AICLMessage]:
        lot = self._store.get_lot(lot_id)
        if lot is None:
            raise LookupError(f"lot {lot_id} not found")
        event = TrackingEvent(
            event_type=EventType.SHIP,
            occurred_at=_now_utc(),
            location=destination_location,
            batch_id=lot.batch_id,
            lot_id=lot.id,
            product=f"lot:{lot.code}",
            quantity=quantity if quantity is not None else lot.weight_kg,
            quantity_unit="kg",
            recipient=recipient,
            recorded_by=actor_id,
            payload={"lot_code": lot.code, "from_batch": str(lot.batch_id)},
        )
        self._store.add_tracking_event(event)
        msg = self._emit(
            f"ship: lot {lot.code} to {recipient} ({destination_location})",
            payload={
                "lot_id": str(lot_id),
                "recipient": recipient,
                "destination": destination_location,
            },
            evidence=[f"tracking_event:{event.id}"],
            parent_message_id=parent_message_id,
        )
        return event, msg

    # ── Recall / Trace ──────────────────────────────────────────────

    def recall_trace_forward(self, batch_id: UUID) -> RecallTrace:
        """FSMA 204 forward recall: given a batch, where did it go?

        Returns the batch, every lot harvested from it, every tracking
        event touching either the batch or its lots, every contamination
        event, and every HACCP check. Suitable for direct rendering to
        an FDA inspector or for feeding the blockchain attester.
        """
        batch = self._store.get_batch(batch_id)
        if batch is None:
            raise LookupError(f"batch {batch_id} not found")
        lots = self._store.lots_for_batch(batch_id)
        events = list(iter_recall_forward(self._store, batch_id))
        haccp = self._store.haccp_checks_for_batch(batch_id)
        contamination = self._store.contamination_events_for_batch(batch_id)
        trace = RecallTrace(
            batch=batch,
            lots=lots,
            tracking_events=events,
            contamination_events=contamination,
            haccp_checks=haccp,
        )
        msg = self._emit(
            f"recall trace: batch {batch.code}",
            payload={
                "batch_id": str(batch_id),
                "lots": [str(lot.id) for lot in lots],
                "tracking_event_count": len(events),
                "contamination_count": len(contamination),
                "haccp_count": len(haccp),
            },
        )
        trace.aicl_messages.append(msg)
        return trace


# ── Module-private helpers ──────────────────────────────────────────────


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)
