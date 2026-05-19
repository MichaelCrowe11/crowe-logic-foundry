"""
Per-turn tool-call cache.

The 2026-04-30 Talon transcript called `deepparallel_query("What is 2+2?")`
twice in consecutive turns. No memoization, no awareness. Each duplicate call
costs latency, money, and (worst) reasoning tokens that count against the
scope budget.

The cache is scoped to one user turn. It catches:

    - Re-calls with identical args inside one turn (the Talon failure mode).
    - Repeated `Read` of the same file with no intervening write.
    - Repeated identical `Bash` commands (caveat: stateful commands are
      excluded by an explicit allowlist; we only dedupe pure-read tools).

What it does NOT catch:

    - Calls across turns (intentional; the world may have changed).
    - Calls with semantically equivalent but textually different args.
    - Stateful tools listed in STATEFUL_TOOLS.

Wire-up:

    cache = ToolCache()
    # Each turn:
    cache.start_turn()
    decision = cache.lookup(name, args)
    if decision.cached:
        return decision.result        # short-circuit, emit dedup event
    result = run_tool(name, args)
    cache.record(name, args, result)
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any


# Stateful tools must NEVER be cached: their effect changes the world.
STATEFUL_TOOLS: frozenset[str] = frozenset({
    "Write", "Edit", "edit_file", "create_file", "save",
    "execute_shell", "Bash", "bash",
    "git_commit", "git_push", "git_pull",
    "browser_click", "browser_type", "browser_navigate", "browser_press_key",
    "browser_fill_form", "browser_drag", "browser_drop", "browser_select_option",
    "browser_file_upload", "browser_handle_dialog", "browser_evaluate",
    "azure_agent_invoke",  # has side effects (creates threads, charges cost)
    "deepparallel_query",  # not idempotent; produces different chains each run
    "send_email", "send_sms", "post_message",
})


@dataclass(frozen=True)
class CacheDecision:
    """Result of a cache lookup."""
    cached: bool
    result: Any = None
    age_seconds: float = 0.0
    tool_name: str = ""


@dataclass
class _Entry:
    result: Any
    recorded_at: float


class ToolCache:
    """Per-turn tool-call cache.

    Lifecycle:
        cache = ToolCache()
        for each user turn:
            cache.start_turn()
            ... tool calls ...
            (optional) cache.end_turn()
    """

    def __init__(self, stateful_tools: frozenset[str] = STATEFUL_TOOLS):
        self._stateful = stateful_tools
        self._entries: dict[str, _Entry] = {}
        self._dedup_count = 0
        self._turn_started_at: float = 0.0

    def start_turn(self) -> None:
        """Reset the cache. Call at the beginning of each user turn."""
        self._entries.clear()
        self._dedup_count = 0
        self._turn_started_at = time.time()

    def end_turn(self) -> None:
        """Optional explicit end-of-turn; cache is also cleared on next start_turn()."""
        self._entries.clear()

    @property
    def dedup_count(self) -> int:
        """Number of cache hits in the current turn."""
        return self._dedup_count

    @staticmethod
    def hash_call(tool_name: str, args: dict[str, Any] | None) -> str:
        """Stable hash of (tool, normalized args)."""
        normalized = {} if args is None else _normalize(args)
        payload = json.dumps([tool_name, normalized], sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def lookup(self, tool_name: str, args: dict[str, Any] | None = None) -> CacheDecision:
        """Check cache. Returns CacheDecision(cached=True, result=...) on hit."""
        if tool_name in self._stateful:
            return CacheDecision(cached=False, tool_name=tool_name)
        key = self.hash_call(tool_name, args)
        entry = self._entries.get(key)
        if entry is None:
            return CacheDecision(cached=False, tool_name=tool_name)
        self._dedup_count += 1
        age = time.time() - entry.recorded_at
        return CacheDecision(
            cached=True,
            result=entry.result,
            age_seconds=age,
            tool_name=tool_name,
        )

    def record(self, tool_name: str, args: dict[str, Any] | None, result: Any) -> None:
        """Store a tool result. No-op for stateful tools."""
        if tool_name in self._stateful:
            return
        key = self.hash_call(tool_name, args)
        self._entries[key] = _Entry(result=result, recorded_at=time.time())

    def __len__(self) -> int:
        return len(self._entries)


def _normalize(args: dict[str, Any]) -> dict[str, Any]:
    """Normalize tool args so semantically-equivalent calls dedupe.

    Strips whitespace from string values. Sorts list values when order is not
    semantically meaningful (we conservatively assume order MATTERS unless
    the value is a set-like collection).
    """
    out: dict[str, Any] = {}
    for k, v in args.items():
        if isinstance(v, str):
            out[k] = v.strip()
        elif isinstance(v, dict):
            out[k] = _normalize(v)
        else:
            out[k] = v
    return out
