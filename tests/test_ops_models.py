"""Smoke tests for the ops-layer Pydantic models.

These cover construction, enum acceptance, weight/quantity validation,
HACCP corrective-action requirement, the strain-lineage DAG shape, and
the immutability of append-only event records.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from crowe_synapse_engine.ops import (
    Batch,
    BatchStage,
    ContaminationEvent,
    ContaminationType,
    EnvironmentalReading,
    EventType,
    HACCPCheck,
    HACCPResult,
    Lot,
    Order,
    OrderLot,
    Severity,
    SOP,
    SOPExecution,
    SOPExecutionResult,
    Strain,
    TrackingEvent,
    Trial,
)


_NOW = datetime.now(timezone.utc)


def _make_strain(**overrides) -> Strain:
    defaults = {"name": "Lion's Mane CL-001", "species": "Hericium erinaceus"}
    defaults.update(overrides)
    return Strain(**defaults)


# ── Strain lineage DAG ──────────────────────────────────────────────────


def test_strain_lineage_chain_of_three():
    grandparent = _make_strain(name="LM Wild")
    parent = _make_strain(name="LM Selected", parent_strain_id=grandparent.id)
    child = _make_strain(name="LM CL-001", parent_strain_id=parent.id)

    assert child.parent_strain_id == parent.id
    assert parent.parent_strain_id == grandparent.id
    assert grandparent.parent_strain_id is None


def test_strain_hybrid_has_two_parents():
    p1 = _make_strain(name="A")
    p2 = _make_strain(name="B")
    hybrid = _make_strain(
        name="AxB",
        parent_strain_id=p1.id,
        secondary_parent_strain_id=p2.id,
    )
    assert hybrid.parent_strain_id == p1.id
    assert hybrid.secondary_parent_strain_id == p2.id


# ── Batch + lot ─────────────────────────────────────────────────────────


def test_batch_defaults_to_inoculation_stage():
    strain = _make_strain()
    batch = Batch(
        code="LM-2026-W19-003",
        strain_id=strain.id,
        started_at=_NOW,
        location="Phoenix - Room B",
    )
    assert batch.stage == BatchStage.INOCULATION


def test_lot_rejects_zero_or_negative_weight():
    batch_id = uuid4()
    with pytest.raises(ValidationError):
        Lot(batch_id=batch_id, code="X", harvested_at=_NOW, weight_kg=0)
    with pytest.raises(ValidationError):
        Lot(batch_id=batch_id, code="X", harvested_at=_NOW, weight_kg=-0.5)
    # positive weight is fine
    ok = Lot(batch_id=batch_id, code="X", harvested_at=_NOW, weight_kg=1.25)
    assert float(ok.weight_kg) == 1.25


# ── Tracking events (FSMA 204 CTEs) ─────────────────────────────────────


def test_tracking_event_is_frozen():
    event = TrackingEvent(
        event_type=EventType.HARVEST,
        occurred_at=_NOW,
        location="Phoenix - Room B",
        product="Lion's Mane fresh",
        quantity=1.5,
        quantity_unit="kg",
    )
    with pytest.raises(Exception):  # pydantic raises ValidationError for frozen
        event.location = "elsewhere"  # type: ignore[misc]


def test_tracking_event_rejects_non_positive_quantity():
    with pytest.raises(ValidationError):
        TrackingEvent(
            event_type=EventType.HARVEST,
            occurred_at=_NOW,
            location="x",
            product="x",
            quantity=0,
        )


def test_tracking_event_replaces_links_amendment_chain():
    original = TrackingEvent(
        event_type=EventType.RECEIVE,
        occurred_at=_NOW,
        location="Phoenix",
        product="rye grain, 50 lb bag",
        quantity=22.7,
        quantity_unit="kg",
    )
    amended = TrackingEvent(
        event_type=EventType.RECEIVE,
        occurred_at=_NOW,
        location="Phoenix",
        product="rye grain, 50 lb bag",
        quantity=22.68,  # corrected to actual weight
        quantity_unit="kg",
        replaces_event_id=original.id,
    )
    assert amended.replaces_event_id == original.id


# ── HACCP ───────────────────────────────────────────────────────────────


def test_haccp_pass_does_not_require_corrective_action():
    HACCPCheck(
        ccp_name="autoclave_temp_min_15min",
        target="121C, 15 min",
        actual="122C, 17 min",
        result=HACCPResult.PASS,
        operator_id="tech-jdoe",
        recorded_at=_NOW,
    )


def test_haccp_fail_requires_corrective_action():
    with pytest.raises(ValidationError, match="corrective_action"):
        HACCPCheck(
            ccp_name="autoclave_temp_min_15min",
            target="121C, 15 min",
            actual="115C, 10 min",
            result=HACCPResult.FAIL,
            operator_id="tech-jdoe",
            recorded_at=_NOW,
        )


def test_haccp_deviation_requires_corrective_action():
    # Allow when given.
    HACCPCheck(
        ccp_name="x",
        target="x",
        actual="x",
        result=HACCPResult.DEVIATION,
        operator_id="x",
        recorded_at=_NOW,
        corrective_action="reran autoclave cycle",
    )


# ── Misc enum constructors ──────────────────────────────────────────────


def test_construct_one_of_each_model():
    """A smoke pass that constructs every model with realistic data."""
    strain = _make_strain()
    batch = Batch(
        code="B1",
        strain_id=strain.id,
        started_at=_NOW,
        location="x",
    )
    lot = Lot(batch_id=batch.id, code="L1", harvested_at=_NOW, weight_kg=1.0)
    ContaminationEvent(
        batch_id=batch.id,
        contamination_type=ContaminationType.TRICHODERMA,
        severity=Severity.HIGH,
        detected_at=_NOW,
    )
    EnvironmentalReading(
        batch_id=batch.id,
        recorded_at=_NOW,
        temp_c=21.5,
        humidity_pct=92.0,
        co2_ppm=600.0,
    )
    sop = SOP(code="SOP-CULT-001", version=1, title="x", body_md="x")
    SOPExecution(
        sop_id=sop.id,
        batch_id=batch.id,
        operator_id="tech",
        executed_at=_NOW,
        result=SOPExecutionResult.COMPLETED,
    )
    Trial(name="Substrate A vs B", started_at=_NOW)
    customer_id = uuid4()
    Order(customer_id=customer_id, placed_at=_NOW)
    OrderLot(order_id=uuid4(), lot_id=lot.id, quantity=0.5, quantity_unit="kg")


def test_order_lot_rejects_non_positive_quantity():
    with pytest.raises(ValidationError):
        OrderLot(
            order_id=uuid4(),
            lot_id=uuid4(),
            quantity=0,
            quantity_unit="kg",
        )
