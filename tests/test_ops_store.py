"""Tests for the InMemoryStore: CRUD round-trip + recall traversal."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from crowe_synapse_engine.ops import (
    Batch,
    ContaminationEvent,
    ContaminationType,
    EnvironmentalReading,
    EventType,
    HACCPCheck,
    HACCPResult,
    InMemoryStore,
    Lot,
    Severity,
    Strain,
    TrackingEvent,
    iter_recall_forward,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture()
def store() -> InMemoryStore:
    return InMemoryStore()


def test_strain_round_trip(store: InMemoryStore) -> None:
    strain = Strain(name="Lions Mane CL-001", species="Hericium erinaceus")
    store.save_strain(strain)
    assert store.get_strain(strain.id) == strain


def test_batch_round_trip_and_lookup_by_code(store: InMemoryStore) -> None:
    strain = Strain(name="Oyster Pearl", species="Pleurotus ostreatus")
    store.save_strain(strain)
    batch = Batch(
        code="OYS-W19-001",
        strain_id=strain.id,
        location="Room A",
        started_at=_now(),
    )
    store.save_batch(batch)
    assert store.get_batch(batch.id) == batch
    assert store.get_batch_by_code("OYS-W19-001") == batch
    assert store.get_batch_by_code("missing") is None


def test_list_batches_excludes_archived_by_default(store: InMemoryStore) -> None:
    strain = Strain(name="X", species="Y")
    store.save_strain(strain)
    active = Batch(code="A", strain_id=strain.id, location="L", started_at=_now())
    archived = Batch(
        code="B",
        strain_id=strain.id,
        location="L",
        started_at=_now(),
        archived_at=_now(),
    )
    store.save_batch(active)
    store.save_batch(archived)
    assert store.list_batches() == [active]
    assert store.list_batches(archived=True) == [archived]


def test_tracking_events_are_append_only(store: InMemoryStore) -> None:
    e1 = TrackingEvent(
        event_type=EventType.RECEIVE,
        occurred_at=_now(),
        location="L",
        product="substrate",
    )
    e2 = TrackingEvent(
        event_type=EventType.GROW,
        occurred_at=_now(),
        location="L",
        product="batch",
    )
    store.add_tracking_event(e1)
    store.add_tracking_event(e2)
    assert store._all_tracking_events() == [e1, e2]


def test_iter_recall_forward_dedupes_events_seen_via_batch_and_lot(
    store: InMemoryStore,
) -> None:
    strain = Strain(name="X", species="Y")
    store.save_strain(strain)
    batch = Batch(
        code="A",
        strain_id=strain.id,
        location="L",
        started_at=_now(),
    )
    store.save_batch(batch)
    lot = Lot(batch_id=batch.id, code="A-F1", harvested_at=_now(), weight_kg=1.0)
    store.save_lot(lot)

    # An event carrying BOTH batch_id and lot_id (a harvest) must surface once.
    harvest = TrackingEvent(
        event_type=EventType.HARVEST,
        occurred_at=_now(),
        location="L",
        batch_id=batch.id,
        lot_id=lot.id,
        product="lot",
    )
    store.add_tracking_event(harvest)
    # A batch-only event also belongs in the trace.
    grow = TrackingEvent(
        event_type=EventType.GROW,
        occurred_at=_now(),
        location="L",
        batch_id=batch.id,
        product="batch",
    )
    store.add_tracking_event(grow)

    trace_events = list(iter_recall_forward(store, batch.id))
    ids = [e.id for e in trace_events]
    assert harvest.id in ids
    assert grow.id in ids
    assert len(ids) == len(set(ids))  # no duplicates


def test_haccp_check_query_by_batch(store: InMemoryStore) -> None:
    strain = Strain(name="X", species="Y")
    store.save_strain(strain)
    batch = Batch(code="A", strain_id=strain.id, location="L", started_at=_now())
    store.save_batch(batch)
    check = HACCPCheck(
        batch_id=batch.id,
        ccp_name="autoclave",
        target="121C/15m",
        actual="122C/17m",
        result=HACCPResult.PASS,
        operator_id="op-1",
        recorded_at=_now(),
    )
    store.add_haccp_check(check)
    assert store.haccp_checks_for_batch(batch.id) == [check]


def test_contamination_query_by_batch(store: InMemoryStore) -> None:
    strain = Strain(name="X", species="Y")
    store.save_strain(strain)
    batch = Batch(code="A", strain_id=strain.id, location="L", started_at=_now())
    store.save_batch(batch)
    event = ContaminationEvent(
        batch_id=batch.id,
        contamination_type=ContaminationType.TRICHODERMA,
        severity=Severity.LOW,
        detected_at=_now(),
    )
    store.add_contamination_event(event)
    assert store.contamination_events_for_batch(batch.id) == [event]


def test_environmental_reading_query_by_batch(store: InMemoryStore) -> None:
    strain = Strain(name="X", species="Y")
    store.save_strain(strain)
    batch = Batch(code="A", strain_id=strain.id, location="L", started_at=_now())
    store.save_batch(batch)
    reading = EnvironmentalReading(
        batch_id=batch.id,
        recorded_at=_now(),
        temp_c=22.0,
        humidity_pct=90.0,
    )
    store.add_environmental_reading(reading)
    assert store.environmental_readings_for_batch(batch.id) == [reading]
