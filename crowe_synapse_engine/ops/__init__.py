"""Operations + research-lab data models.

Pydantic mirrors of the tables defined in ``migrations/009_ops_layer.sql``.
See ``docs/OPS_SCHEMA.md`` for the design.

Imports are explicit and shallow so callers can pick exactly what they need:

    from crowe_synapse_engine.ops import Batch, Lot, TrackingEvent
"""

from crowe_synapse_engine.ops.models import (
    Batch,
    BatchStage,
    ContaminationEvent,
    ContaminationType,
    Customer,
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

__all__ = [
    "Batch",
    "BatchStage",
    "ContaminationEvent",
    "ContaminationType",
    "Customer",
    "EnvironmentalReading",
    "EventType",
    "HACCPCheck",
    "HACCPResult",
    "Lot",
    "Order",
    "OrderLot",
    "SOP",
    "SOPExecution",
    "SOPExecutionResult",
    "Severity",
    "Strain",
    "TrackingEvent",
    "Trial",
]
