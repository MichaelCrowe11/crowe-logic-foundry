"""Event-pump renderer for multi-pane / multi-model sessions.

Implements the same interface as ``cli.renderer.StreamRenderer`` but
never touches the console directly. Every lifecycle call turns into a
structured event pushed onto a shared ``queue.Queue``; a single
consumer (``DualPaneRenderer``) drains the queue and owns the one live
Rich widget that exists per terminal.

Event schema: ``(pane_id, kind, payload, timestamp)`` where ``kind`` is
one of:

  - ``"start"``        payload=None
  - ``"content"``      payload=token_string
  - ``"reasoning"``    payload=token_string
  - ``"spinner"``      payload=label_string
  - ``"spinner_stop"`` payload=None
  - ``"end_segment"``  payload=None
  - ``"finish"``       payload={"tokens": n, "reasoning": r, "elapsed_ms": e, ...}
  - ``"abort"``        payload=None
  - ``"error"``        payload=error_string
"""

from __future__ import annotations

import queue
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PaneEvent:
    """A single renderer event routed to one pane of the dual layout."""

    pane_id: str
    kind: str
    payload: Any
    ts: float


class QueueRenderer:
    """Renderer protocol implementation that forwards events to a queue.

    Mirrors the public surface of ``StreamRenderer`` so it can be dropped
    into any provider's ``stream_response(... renderer=...)`` call. All
    heavy rendering work (Markdown parsing, Rich layout updates) is
    deferred to the single consumer draining the queue on the main
    thread. That keeps the producer threads lean and means only one
    thread ever talks to the terminal.
    """

    def __init__(self, pane_id: str, event_queue: "queue.Queue[PaneEvent]", model_label: str):
        self.pane_id = pane_id
        self._q = event_queue
        self.model_label = model_label

        self._text_chunks: list[str] = []
        self._full_text_chunks: list[str] = []
        self._full_reasoning_chunks: list[str] = []
        self._token_count = 0
        self._reasoning_token_count = 0

        self._t_start = 0.0
        self._t_first_token = 0.0
        self._t_end = 0.0

    # ── Producer lifecycle ──────────────────────────────────────

    def _emit(self, kind: str, payload: Any = None) -> None:
        self._q.put(PaneEvent(self.pane_id, kind, payload, time.monotonic()))

    def start(self) -> None:
        self._t_start = time.monotonic()
        self._emit("start")

    def begin_stream(self) -> None:
        # The producer-side protocol treats begin_stream as implicit;
        # the first `feed` emits a content event which tells the
        # consumer this pane is now streaming.
        pass

    def feed(self, token: str) -> None:
        if self._t_first_token == 0.0:
            self._t_first_token = time.monotonic()
        self._text_chunks.append(token)
        self._full_text_chunks.append(token)
        self._token_count += 1
        self._emit("content", token)

    def feed_reasoning(self, token: str) -> None:
        self._full_reasoning_chunks.append(token)
        self._reasoning_token_count += 1
        self._emit("reasoning", token)

    def end_segment(self) -> None:
        self._text_chunks = []
        self._emit("end_segment")

    def set_spinner(self, label: str) -> None:
        self.end_segment()
        self._emit("spinner", label)

    def stop_spinner(self) -> None:
        self._emit("spinner_stop")

    def finish(self, session_state: dict | None = None) -> None:
        self._t_end = time.monotonic()
        elapsed_ms = (self._t_end - self._t_start) * 1000
        ttft_ms = (
            (self._t_first_token - self._t_start) * 1000
            if self._t_first_token > 0 else 0
        )
        stream_s = (
            self._t_end - self._t_first_token
            if self._t_first_token > 0 else max(self._t_end - self._t_start, 1e-6)
        )
        tps = self._token_count / stream_s if stream_s > 0 else 0
        self._emit("finish", {
            "tokens": self._token_count,
            "reasoning_tokens": self._reasoning_token_count,
            "elapsed_ms": elapsed_ms,
            "ttft_ms": ttft_ms,
            "tps": tps,
        })
        if session_state is not None:
            session_state["last_tokens"] = self._token_count
            session_state["last_tps"] = tps

    def abort(self, session_state: dict | None = None) -> None:
        self._t_end = time.monotonic()
        self._emit("abort")

    # ── Readback for provider loop ──────────────────────────────

    @property
    def current_segment_text(self) -> str:
        return "".join(self._text_chunks)

    @property
    def token_count(self) -> int:
        return self._token_count

    @property
    def elapsed_ms(self) -> float:
        end = self._t_end if self._t_end else time.monotonic()
        return (end - self._t_start) * 1000

    @property
    def full_answer(self) -> str:
        return "".join(self._full_text_chunks)

    @property
    def full_reasoning(self) -> str:
        return "".join(self._full_reasoning_chunks)
