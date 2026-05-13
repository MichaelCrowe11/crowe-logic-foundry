"""Tests for OpsService: business rules, stage machine, AICL emission."""

from __future__ import annotations

import pytest

from crowe_synapse_engine.aicl import Act, AICLMessage
from crowe_synapse_engine.ops import (
    BatchStage,
    ContaminationType,
    HACCPResult,
    IllegalStageTransition,
    InMemoryStore,
    OpsService,
    Severity,
)


@pytest.fixture()
def setup():
    aicl_log: list[AICLMessage] = []
    store = InMemoryStore()
    service = OpsService(store, aicl_sink=aicl_log.append)
    return store, service, aicl_log


def _strain(service: OpsService):
    strain, _ = service.register_strain(
        name="Lions Mane CL-001", species="Hericium erinaceus"
    )
    return strain


# ── Registration ────────────────────────────────────────────────────────


def test_register_strain_emits_aicl(setup) -> None:
    _store, service, log = setup
    strain, msg = service.register_strain(name="X", species="Y")
    assert strain.name == "X"
    assert msg.act == Act.COMMIT
    assert msg in log
    assert "X" in msg.subject


def test_create_batch_requires_existing_strain(setup) -> None:
    _store, service, _log = setup
    from uuid import uuid4

    with pytest.raises(ValueError, match="not registered"):
        service.create_batch(code="A", strain_id=uuid4(), location="L")


def test_create_batch_rejects_duplicate_code(setup) -> None:
    _store, service, _log = setup
    strain = _strain(service)
    service.create_batch(code="A", strain_id=strain.id, location="L")
    with pytest.raises(ValueError, match="already exists"):
        service.create_batch(code="A", strain_id=strain.id, location="L")


def test_create_batch_emits_inoculation_tracking_event(setup) -> None:
    store, service, log = setup
    strain = _strain(service)
    batch, msg = service.create_batch(
        code="A",
        strain_id=strain.id,
        location="L",
        operator_id="op-1",
        substrate_recipe="masters mix",
    )
    events = store.tracking_events_for_batch(batch.id)
    assert len(events) == 1
    assert events[0].event_type.value == "receive"
    assert "substrate" in events[0].product
    # AICL evidence references the inoculation event.
    assert any(f"tracking_event:{events[0].id}" in m.evidence for m in log)


# ── Stage machine ───────────────────────────────────────────────────────


def test_happy_path_inoculation_to_harvested(setup) -> None:
    _store, service, _log = setup
    strain = _strain(service)
    batch, _ = service.create_batch(code="A", strain_id=strain.id, location="L")
    batch, _ = service.advance_stage(batch.id, BatchStage.COLONIZATION)
    assert batch.stage == BatchStage.COLONIZATION
    batch, _ = service.advance_stage(batch.id, BatchStage.FRUITING)
    batch, _ = service.advance_stage(batch.id, BatchStage.HARVESTED)
    assert batch.stage == BatchStage.HARVESTED


def test_illegal_transition_blocked(setup) -> None:
    _store, service, _log = setup
    strain = _strain(service)
    batch, _ = service.create_batch(code="A", strain_id=strain.id, location="L")
    # Cannot skip from INOCULATION straight to HARVESTED.
    with pytest.raises(IllegalStageTransition):
        service.advance_stage(batch.id, BatchStage.HARVESTED)


def test_failure_off_ramp_from_any_active_stage(setup) -> None:
    _store, service, _log = setup
    strain = _strain(service)
    batch, _ = service.create_batch(code="A", strain_id=strain.id, location="L")
    # Bail directly from inoculation to failed.
    batch, _ = service.advance_stage(batch.id, BatchStage.FAILED)
    assert batch.stage == BatchStage.FAILED


def test_terminal_stage_cannot_transition_further(setup) -> None:
    _store, service, _log = setup
    strain = _strain(service)
    batch, _ = service.create_batch(code="A", strain_id=strain.id, location="L")
    service.advance_stage(batch.id, BatchStage.DISCARDED)
    with pytest.raises(IllegalStageTransition):
        service.advance_stage(batch.id, BatchStage.COLONIZATION)


def test_aicl_carries_stage_transition_payload(setup) -> None:
    _store, service, log = setup
    strain = _strain(service)
    batch, _ = service.create_batch(code="A", strain_id=strain.id, location="L")
    _batch, msg = service.advance_stage(batch.id, BatchStage.COLONIZATION)
    assert msg.payload["previous_stage"] == "inoculation"
    assert msg.payload["stage"] == "colonization"
    assert msg.payload["terminal"] is False


def test_aicl_threading_via_parent_message_id(setup) -> None:
    _store, service, _log = setup
    strain = _strain(service)
    batch, create_msg = service.create_batch(
        code="A", strain_id=strain.id, location="L"
    )
    _, transition_msg = service.advance_stage(
        batch.id, BatchStage.COLONIZATION, parent_message_id=create_msg.id
    )
    assert transition_msg.parent_message_id == create_msg.id


# ── HACCP ───────────────────────────────────────────────────────────────


def test_haccp_check_requires_corrective_action_on_fail(setup) -> None:
    _store, service, _log = setup
    strain = _strain(service)
    batch, _ = service.create_batch(code="A", strain_id=strain.id, location="L")
    with pytest.raises(ValueError, match="corrective_action is required"):
        service.record_haccp_check(
            batch_id=batch.id,
            ccp_name="autoclave",
            target="121C/15m",
            actual="100C/10m",
            result=HACCPResult.FAIL,
            operator_id="op-1",
        )


def test_haccp_pass_emits_aicl_with_no_corrective_action(setup) -> None:
    _store, service, _log = setup
    strain = _strain(service)
    batch, _ = service.create_batch(code="A", strain_id=strain.id, location="L")
    check, msg = service.record_haccp_check(
        batch_id=batch.id,
        ccp_name="autoclave",
        target="121C/15m",
        actual="122C/17m",
        result=HACCPResult.PASS,
        operator_id="op-1",
    )
    assert msg.payload["result"] == "pass"
    assert check.corrective_action is None


# ── Harvest & ship ──────────────────────────────────────────────────────


def test_harvest_requires_fruiting_or_harvested(setup) -> None:
    _store, service, _log = setup
    strain = _strain(service)
    batch, _ = service.create_batch(code="A", strain_id=strain.id, location="L")
    # Still in INOCULATION; harvest must be blocked.
    with pytest.raises(IllegalStageTransition):
        service.harvest_to_lot(batch_id=batch.id, code="A-F1", weight_kg=1.0)


def test_harvest_creates_lot_and_tracking_event(setup) -> None:
    store, service, _log = setup
    strain = _strain(service)
    batch, _ = service.create_batch(code="A", strain_id=strain.id, location="L")
    service.advance_stage(batch.id, BatchStage.COLONIZATION)
    service.advance_stage(batch.id, BatchStage.FRUITING)
    lot, msg = service.harvest_to_lot(
        batch_id=batch.id, code="A-F1", weight_kg=1.2, grade="A"
    )
    assert lot.weight_kg == 1.2
    assert lot.grade == "A"
    assert msg.payload["weight_kg"] == 1.2
    # Tracking event got the lot id.
    events = store.tracking_events_for_lot(lot.id)
    assert len(events) == 1
    assert events[0].event_type.value == "harvest"
    assert events[0].quantity == 1.2


def test_ship_lot_creates_ship_tracking_event(setup) -> None:
    _store, service, _log = setup
    strain = _strain(service)
    batch, _ = service.create_batch(code="A", strain_id=strain.id, location="L")
    service.advance_stage(batch.id, BatchStage.COLONIZATION)
    service.advance_stage(batch.id, BatchStage.FRUITING)
    lot, _ = service.harvest_to_lot(batch_id=batch.id, code="A-F1", weight_kg=1.2)
    event, msg = service.ship_lot(
        lot_id=lot.id,
        recipient="Whole Foods AZ",
        destination_location="Phoenix, AZ",
    )
    assert event.event_type.value == "ship"
    assert event.recipient == "Whole Foods AZ"
    assert msg.payload["recipient"] == "Whole Foods AZ"


# ── Recall trace ────────────────────────────────────────────────────────


def test_recall_trace_collects_full_chain(setup) -> None:
    _store, service, _log = setup
    strain = _strain(service)
    batch, _ = service.create_batch(code="A", strain_id=strain.id, location="L")
    service.record_haccp_check(
        batch_id=batch.id,
        ccp_name="autoclave",
        target="121C/15m",
        actual="122C/17m",
        result=HACCPResult.PASS,
        operator_id="op-1",
    )
    service.advance_stage(batch.id, BatchStage.COLONIZATION)
    service.record_contamination(
        batch_id=batch.id,
        contamination_type=ContaminationType.TRICHODERMA,
        severity=Severity.LOW,
        operator_id="op-1",
    )
    service.advance_stage(batch.id, BatchStage.FRUITING)
    lot, _ = service.harvest_to_lot(batch_id=batch.id, code="A-F1", weight_kg=1.0)
    service.ship_lot(lot_id=lot.id, recipient="X", destination_location="Y")

    trace = service.recall_trace_forward(batch.id)
    assert trace.batch.code == "A"
    assert len(trace.lots) == 1
    assert len(trace.haccp_checks) == 1
    assert len(trace.contamination_events) == 1
    event_types = [e.event_type.value for e in trace.tracking_events]
    # receive (inoculation), grow x2 transitions, harvest, ship.
    assert "receive" in event_types
    assert "harvest" in event_types
    assert "ship" in event_types
    # No duplicates (harvest carries both batch_id and lot_id but appears once).
    ids = [e.id for e in trace.tracking_events]
    assert len(ids) == len(set(ids))
    # Recall trace itself emits an AICL message.
    assert len(trace.aicl_messages) == 1


def test_recall_trace_on_missing_batch_raises(setup) -> None:
    _store, service, _log = setup
    from uuid import uuid4

    with pytest.raises(LookupError):
        service.recall_trace_forward(uuid4())
