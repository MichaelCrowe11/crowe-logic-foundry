"""
SSE streaming for the control_plane model gateway.

Wraps the same agent loop the CLI runs, emitting crowe-stream v0 events
(see docs/protocols/crowe-stream-v0.md) over Server-Sent Events.

The renderer here conforms to the StreamRenderer interface that
BaseOpenAIProvider.stream_response expects (start, set_spinner,
stop_spinner, feed, feed_reasoning, end_segment, finish, abort, plus
the current_segment_text property), but instead of writing to stdout
it pushes JSON events onto an asyncio.Queue that the FastAPI
StreamingResponse drains.

Threading: the OpenAI-compatible provider's chat.completions.create
call is blocking. We run it on a worker thread and use
loop.call_soon_threadsafe to schedule each event back onto the
FastAPI event loop, where the async generator awaits the queue.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import AsyncIterator, Optional


# Reuse the provider construction logic the headless CLI already uses.
# Keeping this in one place guarantees the CLI's headless mode and the
# SSE endpoint can never drift on which models exist or how each
# provider authenticates. The underscore prefix is a heads-up that this
# is intra-foundry only; if a third surface wants the same factory we
# promote it to a public name.
from cli.headless import _build_provider, _NoopOrchestrator


class SseEventRenderer:
    """Renderer that posts crowe-stream v0 events onto an asyncio.Queue.

    Mirrors cli.headless.JsonStreamRenderer exactly, but cross-thread:
    the provider runs on a worker thread (since the OpenAI SDK call is
    blocking), and call_soon_threadsafe schedules each event back onto
    the FastAPI event loop where the SSE iterator awaits the queue.

    A None on the queue is the sentinel that terminates the iterator.
    """

    def __init__(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        queue: "asyncio.Queue[Optional[dict]]",
        session_id: str = "",
        model_label: str = "",
    ) -> None:
        self._loop = loop
        self._queue = queue
        self._session_id = session_id
        self._model_label = model_label
        self._text_chunks: list[str] = []
        self._token_count = 0
        self._reasoning_token_count = 0
        self._t_start = 0.0
        self._t_first_token = 0.0

    # ── Cross-thread emit ────────────────────────────────────────────

    def _emit(self, event_type: str, **fields) -> None:
        payload = {"type": event_type, **fields}
        self._loop.call_soon_threadsafe(self._queue.put_nowait, payload)

    def _close(self) -> None:
        """Sentinel that tells the SSE iterator to stop reading."""
        self._loop.call_soon_threadsafe(self._queue.put_nowait, None)

    # ── StreamRenderer interface ────────────────────────────────────

    def start(self) -> None:
        self._t_start = time.monotonic()
        self._emit("ready")

    def set_spinner(self, label: str) -> None:
        # Mirror StreamRenderer.set_spinner: finalize the segment, then
        # advertise the new spinner state.
        self.end_segment()
        self._emit("spinner", label=label)

    def stop_spinner(self) -> None:
        self._emit("spinner", label=None)

    def feed(self, token: str) -> None:
        if self._t_first_token == 0.0:
            self._t_first_token = time.monotonic()
        self._text_chunks.append(token)
        self._token_count += 1
        self._emit("token", delta=token)

    def feed_reasoning(self, token: str) -> None:
        self._reasoning_token_count += 1
        self._emit("reasoning", delta=token)

    def end_segment(self) -> None:
        # The provider reads current_segment_text BEFORE calling this,
        # exactly as it does with the Rich renderer. Clearing here lets
        # the next round start with an empty buffer.
        self._text_chunks = []
        self._emit("segment_end")

    def finish(self, session_state=None) -> None:
        ttft_ms = (
            int((self._t_first_token - self._t_start) * 1000)
            if self._t_first_token > 0 else 0
        )
        self._emit(
            "done",
            tokens=self._token_count,
            reasoning_tokens=self._reasoning_token_count,
            elapsed_ms=int((time.monotonic() - self._t_start) * 1000),
            ttft_ms=ttft_ms,
        )
        self._close()

    def abort(self, session_state=None) -> None:
        self._close()

    @property
    def current_segment_text(self) -> str:
        return "".join(self._text_chunks)

    # ── Helpers used by the worker thread on failure ────────────────

    def emit_error(self, message: str, kind: str = "runtime") -> None:
        self._emit("error", message=message, kind=kind)
        self._close()


def _render_tool_card_sse(renderer: SseEventRenderer):
    """Build a render_tool_card callable bound to the SSE renderer.

    Same call signature as cli.branding.render_tool_card so it can be
    passed straight into BaseOpenAIProvider.stream_response. Tool
    results are truncated to 5000 characters to match the headless
    contract documented in docs/protocols/crowe-stream-v0.md.
    """

    def _render(console, name, args_json, status, result, duration_ms):
        renderer._emit(
            "tool",
            name=name,
            args=args_json,
            status=status,
            result=(result or "")[:5000],
            duration_ms=duration_ms,
        )

    return _render


async def stream_agent_events(
    *,
    messages: list[dict],
    model_id: str = "auto",
    session_id: str = "http",
) -> AsyncIterator[dict]:
    """Run one agent turn and yield raw crowe-stream v0 event dicts.

    Caller is responsible for SSE framing and usage recording. Keeping
    those concerns out of this function lets the route handler reuse
    the same generator for non-SSE consumers (tests, in-process
    fan-out) without changing the contract.
    """
    loop = asyncio.get_running_loop()
    queue: "asyncio.Queue[Optional[dict]]" = asyncio.Queue()

    # Construct the provider on the calling thread so configuration
    # errors surface as a single config error event rather than a
    # cryptic runtime error mid-stream.
    try:
        provider = _build_provider(model_id, session_id=session_id)
    except Exception as exc:  # noqa: BLE001 - intentional catch-all
        yield {"type": "error", "message": str(exc), "kind": "config"}
        return

    # Replay prior turns into the provider's internal state, then add
    # the trailing user turn. Mirrors cli.headless.main's replay loop
    # so streaming sessions inherit the same context-handling rules.
    for msg in messages[:-1]:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role in ("user", "assistant"):
            provider.messages.append({"role": role, "content": msg.get("content") or ""})
    provider.add_user_message(messages[-1].get("content") or "")

    renderer = SseEventRenderer(
        loop=loop,
        queue=queue,
        session_id=session_id,
        model_label=getattr(provider, "label", ""),
    )
    session_state = {
        "favicon": "",
        "tool_count": 0,
        "session_id": session_id,
        "active_model": getattr(provider, "label", ""),
    }
    render_tool_card = _render_tool_card_sse(renderer)

    def _runner() -> None:
        # The provider's stream_response invokes renderer.finish() on
        # success, which closes the queue. On failure we emit an error
        # event explicitly so the iterator never hangs.
        try:
            provider.stream_response(
                console=None,
                render_tool_card=render_tool_card,
                session_state=session_state,
                _get_orchestrator=lambda: _NoopOrchestrator(),
                renderer=renderer,
            )
        except Exception as exc:  # noqa: BLE001
            renderer.emit_error(f"{type(exc).__name__}: {exc}", kind="provider")

    threading.Thread(
        target=_runner, name="crowe-stream-worker", daemon=True,
    ).start()

    while True:
        event = await queue.get()
        if event is None:
            return
        yield event


def sse_frame(event: dict) -> str:
    """Format one event dict as a Server-Sent Events frame.

    The SSE event field equals the payload's type so SSE-aware clients
    (EventSource in browsers, sse-starlette consumers, etc.) can route
    on event type without parsing the JSON.
    """
    data = json.dumps(event, separators=(",", ":"))
    return f"event: {event['type']}\ndata: {data}\n\n"
