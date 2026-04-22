"""Indexed turn history for /replay and /fork commands.

Lightweight append-only log keyed by turn number starting at 1. Each
entry records the user's raw input plus metadata about how the turn
was dispatched (single-model vs dual-mode, active model label, synth
mode if any). The CLI chat loop writes to the log after a turn
succeeds; ``/replay`` and ``/fork`` read from it.

Deliberately decoupled from the ``MemoryStore`` SQLite history in
``crowe_synapse_engine.memory``. That layer is for long-term cross-
session recall; this one is for in-session replay affordances and
doesn't need to survive a CLI restart.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TurnRecord:
    """One user turn, indexed for replay."""
    index: int
    user_input: str
    model_label: str = ""
    dual_active: bool = False
    synth_active: bool = False
    synth_mode: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


class TurnHistory:
    """Append-only turn log with numeric indexing from 1."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._turns: list[TurnRecord] = []

    def append(
        self,
        user_input: str,
        *,
        model_label: str = "",
        dual_active: bool = False,
        synth_active: bool = False,
        synth_mode: str = "",
        meta: dict[str, Any] | None = None,
    ) -> TurnRecord:
        with self._lock:
            record = TurnRecord(
                index=len(self._turns) + 1,
                user_input=user_input,
                model_label=model_label,
                dual_active=dual_active,
                synth_active=synth_active,
                synth_mode=synth_mode,
                meta=meta or {},
            )
            self._turns.append(record)
            return record

    def get(self, index: int) -> TurnRecord | None:
        """Return the turn at a 1-based index, or None."""
        with self._lock:
            if 1 <= index <= len(self._turns):
                return self._turns[index - 1]
        return None

    def truncate_after(self, index: int) -> list[TurnRecord]:
        """Drop all turns with index > ``index``. Returns the dropped entries.

        Used by /fork: replaying turn 3 with fork semantics discards turns
        4..N so the replayed turn becomes the new tail of the session.
        """
        with self._lock:
            if index < 0:
                index = 0
            dropped = self._turns[index:]
            self._turns = self._turns[:index]
            return list(dropped)

    def recent(self, limit: int = 10) -> list[TurnRecord]:
        with self._lock:
            return list(self._turns[-limit:])

    def __len__(self) -> int:
        with self._lock:
            return len(self._turns)


# Session-scoped singleton. Attached to session_state by the chat loop
# so tests can inject their own instance without monkeypatching.

def ensure_history(session_state: dict) -> TurnHistory:
    """Return the session's TurnHistory, creating it on first call."""
    history = session_state.get("turn_history")
    if history is None:
        history = TurnHistory()
        session_state["turn_history"] = history
    return history
