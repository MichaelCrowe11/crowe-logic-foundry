"""Translate crowe-stream v0 events into canonical CMP v1 events (mesh B2).

v0 (control_plane/streaming.py) is the legacy SSE vocabulary. CMP v1 is the
canonical mesh wire format defined in `crowe_mesh_protocol`. This translator
is pure (no I/O) and emits plain dicts that conform to the CMP TypedDicts — it
deliberately does NOT import crowe_mesh_protocol at runtime so the control
plane keeps no hard dependency on the shared package.

Notes:
- Every emitted event carries `session_id`.
- v0 fires a single `tool` card *after* execution, so the `tool.started` we
  emit here is synthesized alongside `tool.result` rather than truly
  pre-invoke. Genuine pre-invoke timing needs a provider-level hook (future).
"""

from __future__ import annotations

from typing import Any

_OK_STATUSES = {"ok", "success", "done", "complete", "completed"}


class CmpTranslator:
    def __init__(self, session_id: str, model_tier: str = "auto"):
        self.session_id = session_id
        self.model_tier = model_tier
        self._reasoning_id = "r0"
        self._tool_n = 0

    def _base(self, **fields: Any) -> dict:
        return {"session_id": self.session_id, **fields}

    def translate(self, v0: dict) -> list[dict]:
        """Map one v0 event dict to zero or more CMP event dicts."""
        t = v0.get("type")

        if t == "ready":
            return [self._base(type="ready", model_tier=self.model_tier)]
        if t == "token":
            return [self._base(type="token", delta=v0.get("delta", ""))]
        if t == "reasoning":
            return [
                self._base(
                    type="reasoning.delta",
                    reasoning_id=self._reasoning_id,
                    delta=v0.get("delta", ""),
                )
            ]
        if t == "spinner":
            return [self._base(type="status", label=v0.get("label"))]
        if t == "segment_end":
            return [self._base(type="segment_end", reason="segment")]
        if t == "tool":
            self._tool_n += 1
            call_id = f"{v0.get('name', 'tool')}-{self._tool_n}"
            status = "ok" if v0.get("status") in _OK_STATUSES else "fail"
            return [
                self._base(
                    type="tool.started", tool_call_id=call_id, name=v0.get("name", "")
                ),
                self._base(type="tool.result", tool_call_id=call_id, status=status),
            ]
        if t == "done":
            return [
                self._base(
                    type="done",
                    tokens=v0.get("tokens", 0),
                    reasoning_tokens=v0.get("reasoning_tokens", 0),
                    elapsed_ms=v0.get("elapsed_ms", 0),
                    ttft_ms=v0.get("ttft_ms", 0),
                )
            ]
        if t == "error":
            return [
                self._base(
                    type="error",
                    code=v0.get("kind", "runtime"),
                    message=v0.get("message", ""),
                    recoverable=False,
                )
            ]
        # Unknown / non-CMP v0 events (e.g. keepalive) are dropped.
        return []
