"""AICLMessage · the unit of agent communication.

Immutable. Every field round-trips through ``to_dict`` / ``from_dict``.
Construction validates the speech-act contract (e.g. REPORT must have a
parent, confidence must be in [0, 1]).

``aicl_chunk()`` wraps a message into a ``RuntimeChunk`` so AICL flows
through the same async stream the runtime already emits for text and
tool events.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from crowe_synapse_engine.aicl.acts import REPLY_ACTS, Act
from crowe_synapse_engine.runtime.base import ChunkKind, RuntimeChunk


class AICLValidationError(ValueError):
    """Raised when a constructed message violates the act contract."""


def _new_id() -> str:
    return uuid.uuid4().hex


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class AICLMessage:
    act: Act
    from_agent: str
    subject: str
    to_agent: str | None = None
    confidence: float = 1.0
    evidence: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    requires_human: bool = False
    parent_message_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    dialect: str = "core"
    id: str = field(default_factory=_new_id)
    timestamp: str = field(default_factory=_now_iso)

    def __post_init__(self) -> None:
        if not self.from_agent:
            raise AICLValidationError("from_agent is required")
        if not 0.0 <= self.confidence <= 1.0:
            raise AICLValidationError(
                f"confidence must be in [0.0, 1.0], got {self.confidence}"
            )
        if self.act in REPLY_ACTS and self.parent_message_id is None:
            raise AICLValidationError(
                f"act={self.act.value} requires parent_message_id; "
                "REPORT/DISPUTE must thread to the message they reply to"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "act": self.act.value,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "subject": self.subject,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
            "constraints": list(self.constraints),
            "requires_human": self.requires_human,
            "parent_message_id": self.parent_message_id,
            "payload": dict(self.payload),
            "dialect": self.dialect,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AICLMessage:
        return cls(
            act=Act(data["act"]),
            from_agent=data["from_agent"],
            to_agent=data.get("to_agent"),
            subject=data["subject"],
            confidence=float(data.get("confidence", 1.0)),
            evidence=list(data.get("evidence", [])),
            constraints=list(data.get("constraints", [])),
            requires_human=bool(data.get("requires_human", False)),
            parent_message_id=data.get("parent_message_id"),
            payload=dict(data.get("payload", {})),
            dialect=data.get("dialect", "core"),
            id=data.get("id", _new_id()),
            timestamp=data.get("timestamp", _now_iso()),
        )


def aicl_chunk(message: AICLMessage) -> RuntimeChunk:
    """Wrap an AICL message into a RuntimeChunk for emission.

    The chunk's text field carries a one-line digest for renderers that
    don't speak AICL; the full message lives in ``meta['aicl']``.
    """
    digest = f"[{message.act.value}] {message.from_agent}"
    if message.to_agent:
        digest += f" -> {message.to_agent}"
    digest += f": {message.subject}"
    return RuntimeChunk(
        kind=ChunkKind.AICL,
        text=digest,
        meta={"aicl": message.to_dict()},
    )
