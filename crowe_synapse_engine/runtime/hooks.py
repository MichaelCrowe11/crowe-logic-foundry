"""
Hook registry · pluggable lifecycle callbacks.

The runtime fires hooks at well-defined points in the agent loop. A hook can
observe (log, audit) or block (raise a soft veto the model is told about).
This is the in-process analogue of Claude Code's settings.json hooks.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Awaitable, Callable
from typing import Any

from crowe_synapse_engine.runtime.base import HookEvent, HookResult

HookCallback = Callable[[HookEvent, dict[str, Any]], Awaitable[HookResult]]


class HookRegistry:
    """Per-agent (or per-run) collection of hook callbacks.

    Each entry has an event, an optional matcher (glob over the tool name for
    tool-related events, ignored otherwise), and the callback. Hooks for the
    same event fire in registration order; the first one to block wins.
    """

    def __init__(self) -> None:
        self._entries: list[tuple[HookEvent, str, HookCallback]] = []

    def register(
        self,
        event: HookEvent,
        callback: HookCallback,
        *,
        matcher: str = "*",
    ) -> None:
        self._entries.append((event, matcher, callback))

    async def dispatch(self, event: HookEvent, payload: dict[str, Any]) -> HookResult:
        """Fire every hook registered for ``event`` and return the first block, if any."""
        tool_name = payload.get("tool_name", "")
        result: HookResult = HookResult(block=False)
        for entry_event, matcher, callback in self._entries:
            if entry_event != event:
                continue
            if event in (HookEvent.PRE_TOOL_USE, HookEvent.POST_TOOL_USE):
                if not fnmatch.fnmatch(tool_name, matcher):
                    continue
            outcome = await callback(event, payload)
            if outcome.block:
                return outcome
            result = outcome
        return result
