"""Conversation · a thread of AICL messages with DAG navigation.

A Conversation is an append-only log plus indexes for thread navigation.
Storage is in-memory; pair with JSONL on disk via ``to_jsonl`` /
``from_jsonl`` for audit and replay. Wire to the existing MemoryStore
when persistence into the synapse-engine memory layer is wanted.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from crowe_synapse_engine.aicl.messages import AICLMessage


class Conversation:
    def __init__(self, topic: str = ""):
        self.topic = topic
        self._messages: list[AICLMessage] = []
        self._by_id: dict[str, AICLMessage] = {}
        self._children: dict[str, list[AICLMessage]] = {}

    def __len__(self) -> int:
        return len(self._messages)

    def __iter__(self) -> Iterator[AICLMessage]:
        return iter(self._messages)

    def append(self, message: AICLMessage) -> AICLMessage:
        """Add a message. Returns the message for convenient chaining."""
        if message.id in self._by_id:
            raise ValueError(f"message id {message.id!r} already in conversation")
        if message.parent_message_id and message.parent_message_id not in self._by_id:
            raise ValueError(
                f"parent_message_id {message.parent_message_id!r} not found in conversation"
            )
        self._messages.append(message)
        self._by_id[message.id] = message
        if message.parent_message_id:
            self._children.setdefault(message.parent_message_id, []).append(message)
        return message

    def parent_of(self, message: AICLMessage) -> AICLMessage | None:
        if message.parent_message_id is None:
            return None
        return self._by_id.get(message.parent_message_id)

    def children_of(self, message: AICLMessage) -> list[AICLMessage]:
        return list(self._children.get(message.id, []))

    def thread_ending_at(self, message: AICLMessage) -> list[AICLMessage]:
        """Walk parents up from ``message`` and return the thread root-first."""
        chain: list[AICLMessage] = []
        current: AICLMessage | None = message
        while current is not None:
            chain.append(current)
            current = self.parent_of(current)
        chain.reverse()
        return chain

    def to_jsonl(self) -> str:
        return "\n".join(json.dumps(m.to_dict()) for m in self._messages)

    def write_jsonl(self, path: str | Path) -> None:
        Path(path).write_text(self.to_jsonl() + "\n", encoding="utf-8")

    @classmethod
    def from_jsonl(cls, text: str, *, topic: str = "") -> Conversation:
        conv = cls(topic=topic)
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            conv.append(AICLMessage.from_dict(json.loads(line)))
        return conv

    @classmethod
    def read_jsonl(cls, path: str | Path, *, topic: str = "") -> Conversation:
        return cls.from_jsonl(Path(path).read_text(encoding="utf-8"), topic=topic)
