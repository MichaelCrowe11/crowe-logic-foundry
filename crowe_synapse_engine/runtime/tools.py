"""
Tool registry · in-process tool definitions for the synapse runtime.

A ``Tool`` here is a Python coroutine plus a JSON-schema description of its
arguments. Tools are registered globally (process-wide) and selected per-agent
by the YAML ``tools:`` list, which supports glob patterns like ``talon_*``.

Permission gating runs before every tool execution. The default policy is
allow-all; callers wire in stricter policies via ``permission_callback`` on
``ToolRegistry.run_tool``.
"""

from __future__ import annotations

import fnmatch
import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from crowe_synapse_engine.runtime.base import (
    PermissionResult,
    ToolCall,
    ToolResult,
)

ToolFunc = Callable[..., Awaitable[Any]]
PermissionCallback = Callable[[ToolCall], Awaitable[PermissionResult]]


@dataclass
class Tool:
    """A registered tool callable by the model."""

    name: str
    description: str
    func: ToolFunc
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_openai_schema(self) -> dict[str, Any]:
        """Convert to OpenAI Chat Completions tool format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters
                or {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        }

    def to_anthropic_schema(self) -> dict[str, Any]:
        """Convert to Anthropic Messages tool format."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters
            or {
                "type": "object",
                "properties": {},
                "required": [],
            },
        }


def _infer_parameters_from_signature(func: ToolFunc) -> dict[str, Any]:
    """Build a JSON-schema params block from a coroutine's signature.

    Used when ``register_tool`` is called without an explicit schema. Mirrors
    the convention in ``providers/_shared.build_tool_schemas`` so tool authors
    can write Python type hints and have the runtime generate the schema.
    """
    sig = inspect.signature(func)
    properties: dict[str, Any] = {}
    required: list[str] = []
    type_map: dict[type, str] = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        dict: "object",
    }
    for pname, param in sig.parameters.items():
        annotation = param.annotation
        ptype = type_map.get(annotation, "string")
        properties[pname] = {"type": ptype}
        if param.default is inspect.Parameter.empty:
            required.append(pname)
    return {"type": "object", "properties": properties, "required": required}


class ToolRegistry:
    """Process-global registry of tools available to any agent."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(
        self,
        name: str,
        description: str,
        func: ToolFunc,
        parameters: dict[str, Any] | None = None,
    ) -> Tool:
        """Register a tool. Overwrites any existing tool with the same name."""
        if not inspect.iscoroutinefunction(func):
            raise TypeError(
                f"Tool {name!r} must be an async coroutine function, got {type(func).__name__}"
            )
        tool = Tool(
            name=name,
            description=description,
            func=func,
            parameters=parameters or _infer_parameters_from_signature(func),
        )
        self._tools[name] = tool
        return tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools.keys())

    def resolve(self, patterns: list[str]) -> list[Tool]:
        """Resolve a YAML ``tools:`` list (supports glob patterns)."""
        resolved: dict[str, Tool] = {}
        for pattern in patterns:
            if "*" in pattern or "?" in pattern:
                for name, tool in self._tools.items():
                    if fnmatch.fnmatch(name, pattern):
                        resolved[name] = tool
            elif pattern in self._tools:
                resolved[pattern] = self._tools[pattern]
        return list(resolved.values())

    async def run_tool(
        self,
        call: ToolCall,
        *,
        permission_callback: PermissionCallback | None = None,
    ) -> ToolResult:
        """Execute one tool call after running its permission check."""
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult(
                id=call.id,
                name=call.name,
                content=f"Tool {call.name!r} is not registered.",
                is_error=True,
            )

        arguments = dict(call.arguments)
        if permission_callback is not None:
            decision = await permission_callback(call)
            if not decision.allowed:
                return ToolResult(
                    id=call.id,
                    name=call.name,
                    content=f"Permission denied: {decision.reason or 'no reason given'}",
                    is_error=True,
                )
            if decision.updated_arguments is not None:
                arguments = decision.updated_arguments

        try:
            result = await tool.func(**arguments)
        except Exception as exc:  # the model needs to see the error to recover
            return ToolResult(
                id=call.id,
                name=call.name,
                content=f"Tool raised {type(exc).__name__}: {exc}",
                is_error=True,
            )

        if isinstance(result, str):
            content = result
        else:
            try:
                content = json.dumps(result, default=str)
            except TypeError:
                content = repr(result)
        return ToolResult(id=call.id, name=call.name, content=content)


def permission_allow(
    updated_arguments: dict[str, Any] | None = None,
) -> PermissionResult:
    """Convenience constructor for an allow decision."""
    return PermissionResult(allowed=True, updated_arguments=updated_arguments)


def permission_deny(reason: str) -> PermissionResult:
    """Convenience constructor for a deny decision."""
    return PermissionResult(allowed=False, reason=reason)
