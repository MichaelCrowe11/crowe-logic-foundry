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
from crowe_synapse_engine.ops.service import AiclSink, OpsService, RecallTrace
from crowe_synapse_engine.ops.stage_rules import (
    TERMINAL_STAGES,
    IllegalStageTransition,
    allowed_next,
    is_terminal,
    validate_transition,
)
from crowe_synapse_engine.ops.store import (
    InMemoryStore,
    Store,
    get_default_store,
    iter_recall_forward,
)

__all__ = [
    # Models
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
    # Service + stage rules
    "AiclSink",
    "IllegalStageTransition",
    "OpsService",
    "RecallTrace",
    "TERMINAL_STAGES",
    "allowed_next",
    "is_terminal",
    "validate_transition",
    # Store
    "InMemoryStore",
    "Store",
    "get_default_store",
    "iter_recall_forward",
]
