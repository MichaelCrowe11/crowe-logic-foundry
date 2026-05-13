"""
Runtime base types · protocols, dataclasses, error types.

These types are deliberately small and stable. The rest of the runtime layer
and the synapse-DSL compiler both depend on them, so they live in a leaf
module with no internal imports.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class HookEvent(str, Enum):
    """Lifecycle events emitted by the runtime."""

    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    ASSISTANT_TEXT = "AssistantText"
    STOP = "Stop"


class ChunkKind(str, Enum):
    """The kind of streaming chunk the runtime yields to its caller."""

    TEXT = "text"
    REASONING = "reasoning"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    PERMISSION_DENIED = "permission_denied"
    HOOK_BLOCKED = "hook_blocked"
    AICL = "aicl"
    DONE = "done"
    ERROR = "error"


@dataclass
class RuntimeChunk:
    """One element of the runtime's output stream.

    Yielding small typed records (instead of raw strings) keeps the wire format
    stable across runtimes and lets every caller, from the CLI renderer to a
    web stream, decide what to surface.
    """

    kind: ChunkKind
    text: str = ""
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_result: str | None = None
    reason: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCall:
    """A model-requested tool invocation, before execution."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    """The result of executing a ToolCall."""

    id: str
    name: str
    content: str
    is_error: bool = False


@dataclass
class PermissionResult:
    """Outcome of a permission check on a ToolCall."""

    allowed: bool
    reason: str = ""
    updated_arguments: dict[str, Any] | None = None


@dataclass
class HookResult:
    """Outcome of a hook callback.

    Hooks can block the action that triggered them by returning ``block=True``.
    The runtime surfaces the ``reason`` to the model so it can adjust strategy.
    """

    block: bool = False
    reason: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


class RuntimeError(Exception):
    """Base error type for the runtime layer."""


HookCallback = Callable[[HookEvent, dict[str, Any]], Awaitable[HookResult]]
PermissionCallback = Callable[[ToolCall], Awaitable[PermissionResult]]


@runtime_checkable
class AgentRuntime(Protocol):
    """Protocol every runtime implementation satisfies.

    The runtime owns the streaming + tool-call loop, permission gating, hook
    dispatch, and provider selection. Callers consume an ``AsyncIterator`` of
    ``RuntimeChunk`` and decide what to do with each.
    """

    async def run(
        self,
        *,
        agent_name: str,
        user_prompt: str,
        system_prompt: str,
        model: str,
        tools: list[str],
        max_turns: int = 20,
        meta: dict[str, Any] | None = None,
    ) -> AsyncIterator[RuntimeChunk]:
        """Run the agent loop and yield chunks until the turn finishes."""
        ...  # pragma: no cover  (Protocol method)
