"""Pydantic v2 models for the operations + research-lab schema.

Mirrors ``migrations/009_ops_layer.sql`` field-for-field. Append-only event
records (``TrackingEvent``, ``HACCPCheck``, ``EnvironmentalReading``,
``ContaminationEvent``, ``SOPExecution``) are frozen so callers cannot mutate
them post-construction. The mutable rows (``Batch``, ``Lot``, ``Strain``,
``Order``, etc.) stay mutable because the application updates ``stage``,
``status``, and similar lifecycle fields in place.

These models do NOT depend on a database driver. They validate shape and
basic constraints (positive weights, well-formed enums); persistence is the
caller's job. That keeps ``ops`` importable in slim packages and trivially
testable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveFloat,
    field_validator,
    model_validator,
)


# ╭──────────────────────────────────────────────────────────────────────╮
# │ Enums                                                                │
# ╰──────────────────────────────────────────────────────────────────────╯


class BatchStage(str, Enum):
    INOCULATION = "inoculation"
    COLONIZATION = "colonization"
    FRUITING = "fruiting"
    HARVESTED = "harvested"
    FAILED = "failed"
    DISCARDED = "discarded"


class EventType(str, Enum):
    """FSMA 204 Critical Tracking Event types."""

    RECEIVE = "receive"
    GROW = "grow"
    HARVEST = "harvest"
    TRANSFORM = "transform"
    SHIP = "ship"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class HACCPResult(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    DEVIATION = "deviation"


class SOPExecutionResult(str, Enum):
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


class ContaminationType(str, Enum):
    TRICHODERMA = "trichoderma"
    BACTERIAL = "bacterial"
    MITES = "mites"
    MOLD_OTHER = "mold_other"
    UNKNOWN = "unknown"


# ╭──────────────────────────────────────────────────────────────────────╮
# │ Shared helpers                                                       │
# ╰──────────────────────────────────────────────────────────────────────╯


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


_MUTABLE = ConfigDict(use_enum_values=False, str_strip_whitespace=True)
_FROZEN = ConfigDict(use_enum_values=False, str_strip_whitespace=True, frozen=True)


# ╭──────────────────────────────────────────────────────────────────────╮
# │ Strains : DAG via parent FKs                                         │
# ╰──────────────────────────────────────────────────────────────────────╯


class Strain(BaseModel):
    model_config = _MUTABLE

    id: UUID = Field(default_factory=uuid4)
    name: str
    species: str
    parent_strain_id: UUID | None = None
    secondary_parent_strain_id: UUID | None = None
    origin: str | None = None
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now_utc)
    archived_at: datetime | None = None


# ╭──────────────────────────────────────────────────────────────────────╮
# │ Batches + lots                                                       │
# ╰──────────────────────────────────────────────────────────────────────╯


class Batch(BaseModel):
    model_config = _MUTABLE

    id: UUID = Field(default_factory=uuid4)
    code: str
    strain_id: UUID
    stage: BatchStage = BatchStage.INOCULATION
    substrate_recipe: str | None = None
    substrate_lot_code: str | None = None
    started_at: datetime
    expected_harvest_at: datetime | None = None
    location: str
    operator_id: str | None = None
    content_hash: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now_utc)
    archived_at: datetime | None = None


class Lot(BaseModel):
    model_config = _MUTABLE

    id: UUID = Field(default_factory=uuid4)
    batch_id: UUID
    code: str
    harvested_at: datetime
    weight_kg: PositiveFloat
    grade: str | None = None
    destination: str | None = None
    content_hash: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now_utc)
    archived_at: datetime | None = None


# ╭──────────────────────────────────────────────────────────────────────╮
# │ Append-only event records (frozen)                                   │
# ╰──────────────────────────────────────────────────────────────────────╯


class TrackingEvent(BaseModel):
    """FSMA 204 CTE. Frozen; corrections use replaces_event_id."""

    model_config = _FROZEN

    id: UUID = Field(default_factory=uuid4)
    event_type: EventType
    occurred_at: datetime
    location: str
    batch_id: UUID | None = None
    lot_id: UUID | None = None
    supplier: str | None = None
    recipient: str | None = None
    product: str
    quantity: float | None = None
    quantity_unit: str | None = None
    reference_doc: str | None = None
    recorded_by: str | None = None
    replaces_event_id: UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now_utc)

    @field_validator("quantity")
    @classmethod
    def _quantity_must_be_positive(cls, v: float | None) -> float | None:
        if v is not None and v <= 0:
            raise ValueError("quantity must be > 0 when provided")
        return v


class HACCPCheck(BaseModel):
    model_config = _FROZEN

    id: UUID = Field(default_factory=uuid4)
    batch_id: UUID | None = None
    ccp_name: str
    target: str
    actual: str
    result: HACCPResult
    operator_id: str
    recorded_at: datetime
    corrective_action: str | None = None
    sop_execution_id: UUID | None = None
    created_at: datetime = Field(default_factory=_now_utc)

    @model_validator(mode="after")
    def _corrective_action_required_on_non_pass(self) -> HACCPCheck:
        if self.result != HACCPResult.PASS and not self.corrective_action:
            raise ValueError("corrective_action is required when result is not pass")
        return self


class EnvironmentalReading(BaseModel):
    model_config = _FROZEN

    id: UUID = Field(default_factory=uuid4)
    batch_id: UUID
    recorded_at: datetime
    temp_c: float | None = None
    humidity_pct: float | None = None
    co2_ppm: float | None = None
    light_lux: float | None = None
    source: str | None = None
    created_at: datetime = Field(default_factory=_now_utc)


class ContaminationEvent(BaseModel):
    model_config = _FROZEN

    id: UUID = Field(default_factory=uuid4)
    batch_id: UUID
    contamination_type: ContaminationType
    severity: Severity
    detected_at: datetime
    photo_url: str | None = None
    contained_action: str | None = None
    operator_id: str | None = None
    created_at: datetime = Field(default_factory=_now_utc)


# ╭──────────────────────────────────────────────────────────────────────╮
# │ SOPs + executions                                                    │
# ╰──────────────────────────────────────────────────────────────────────╯


class SOP(BaseModel):
    model_config = _MUTABLE

    id: UUID = Field(default_factory=uuid4)
    code: str
    version: int = Field(ge=1)
    title: str
    body_md: str
    is_current: bool = True
    created_at: datetime = Field(default_factory=_now_utc)
    archived_at: datetime | None = None


class SOPExecution(BaseModel):
    model_config = _FROZEN

    id: UUID = Field(default_factory=uuid4)
    sop_id: UUID
    batch_id: UUID | None = None
    operator_id: str
    executed_at: datetime
    result: SOPExecutionResult
    notes: str | None = None
    created_at: datetime = Field(default_factory=_now_utc)


# ╭──────────────────────────────────────────────────────────────────────╮
# │ Trials (research)                                                    │
# ╰──────────────────────────────────────────────────────────────────────╯


class Trial(BaseModel):
    model_config = _MUTABLE

    id: UUID = Field(default_factory=uuid4)
    name: str
    hypothesis: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    primary_strain_id: UUID | None = None
    batch_ids: list[UUID] = Field(default_factory=list)
    treatments: list[dict[str, Any]] = Field(default_factory=list)
    outcome: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now_utc)
    archived_at: datetime | None = None


# ╭──────────────────────────────────────────────────────────────────────╮
# │ Customers + orders                                                   │
# ╰──────────────────────────────────────────────────────────────────────╯


class Customer(BaseModel):
    model_config = _MUTABLE

    id: UUID = Field(default_factory=uuid4)
    external_id: str | None = None
    email: str | None = None
    name: str | None = None
    created_at: datetime = Field(default_factory=_now_utc)


class Order(BaseModel):
    model_config = _MUTABLE

    id: UUID = Field(default_factory=uuid4)
    external_id: str | None = None
    customer_id: UUID | None = None
    placed_at: datetime
    shipped_at: datetime | None = None
    status: str = "placed"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now_utc)


class OrderLot(BaseModel):
    """Join row linking an order to one of the lots that fulfilled it.

    Forward traceability lives here: given a lot, enumerate every order_lot
    row to find which orders received it. Given an order, enumerate to find
    which lots shipped under it.
    """

    model_config = _FROZEN

    order_id: UUID
    lot_id: UUID
    quantity: PositiveFloat
    quantity_unit: str
