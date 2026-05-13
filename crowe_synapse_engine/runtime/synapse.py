"""SynapseRuntime · default backend-agnostic agent loop.

This runtime speaks OpenAI-compatible Chat Completions, which covers the
bulk of the CroweLM backend matrix (Azure OpenAI, NIM, Ollama, OpenRouter,
hosted vLLM/SGLang/NIM-compatible). Anthropic-native and watsonx paths are
intentionally NOT implemented here; route Claude work through the
``SdkBridgeRuntime`` (``runtime: sdk`` in the agent YAML) and watsonx work
through the legacy ``providers/watsonx.py`` until a dedicated runtime
adapter is written.

Brand Veil: provider identity is internal. The runtime never yields a
``ModelProvider`` name in a ``RuntimeChunk``; surfaces see only the agent's
declared model.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

from crowe_synapse_engine.runtime.base import (
    ChunkKind,
    HookEvent,
    RuntimeChunk,
    ToolCall,
)
from crowe_synapse_engine.runtime.dispatcher import ModelProvider
from crowe_synapse_engine.runtime.hooks import HookRegistry
from crowe_synapse_engine.runtime.tools import (
    PermissionCallback,
    ToolRegistry,
)


_OPENAI_COMPATIBLE_PROVIDERS: frozenset[ModelProvider] = frozenset(
    {
        ModelProvider.AZURE_OPENAI,
        ModelProvider.HOSTED_OPENAI,
        ModelProvider.NVIDIA,
        ModelProvider.OLLAMA,
        ModelProvider.OPENROUTER,
    }
)


def _resolve_client(provider: ModelProvider):
    """Construct an ``openai.OpenAI`` client from env for the given provider.

    Each provider exposes a different (base_url, api_key) pair. Resolution
    is env-only here; richer wiring (per-agent endpoint overrides) belongs
    in a future config layer, not the runtime hot path.
    """
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "The 'openai' package is required for SynapseRuntime. "
            "Install with: pip install openai"
        ) from exc

    if provider == ModelProvider.AZURE_OPENAI:
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
        api_key = os.environ.get("AZURE_OPENAI_API_KEY", "")
        if not endpoint or not api_key:
            raise RuntimeError(
                "AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY must be set "
                "for the Azure OpenAI provider."
            )
        base_url = endpoint.rstrip("/")
        if not (base_url.endswith("/v1") or "/openai/v1" in base_url):
            base_url = (
                f"{base_url}/v1"
                if base_url.endswith("/openai")
                else f"{base_url}/openai/v1"
            )
        return OpenAI(api_key=api_key, base_url=base_url)

    if provider == ModelProvider.HOSTED_OPENAI:
        base_url = os.environ.get("HOSTED_OPENAI_BASE_URL", "https://api.openai.com/v1")
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY must be set for the hosted OpenAI provider."
            )
        return OpenAI(api_key=api_key, base_url=base_url)

    if provider == ModelProvider.NVIDIA:
        base_url = os.environ.get(
            "NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"
        )
        api_key = os.environ.get("NVIDIA_API_KEY", "")
        if not api_key:
            raise RuntimeError("NVIDIA_API_KEY must be set for the NIM provider.")
        return OpenAI(api_key=api_key, base_url=base_url)

    if provider == ModelProvider.OLLAMA:
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        # Ollama doesn't require an API key, but the OpenAI SDK does.
        return OpenAI(
            api_key=os.environ.get("OLLAMA_API_KEY", "ollama"), base_url=base_url
        )

    if provider == ModelProvider.OPENROUTER:
        base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY must be set for the OpenRouter provider."
            )
        return OpenAI(api_key=api_key, base_url=base_url)

    raise NotImplementedError(
        f"Provider {provider.value!r} is not supported by SynapseRuntime. "
        "For Claude models use 'runtime: sdk' (SdkBridgeRuntime). "
        "For watsonx use the existing providers/watsonx.py path."
    )


class SynapseRuntime:
    """OpenAI-compatible streaming agent loop with tool calls and hooks."""

    def __init__(
        self,
        *,
        provider: ModelProvider,
        tool_registry: ToolRegistry | None = None,
        hook_registry: HookRegistry | None = None,
        permission_callback: PermissionCallback | None = None,
    ):
        if provider == ModelProvider.ANTHROPIC:
            raise RuntimeError(
                "SynapseRuntime does not implement the native Anthropic surface. "
                "Set 'runtime: sdk' in the agent YAML to route Claude models "
                "through the Claude Agent SDK bridge instead."
            )
        if provider not in _OPENAI_COMPATIBLE_PROVIDERS:
            raise NotImplementedError(
                f"Provider {provider.value!r} not implemented in SynapseRuntime."
            )
        self.provider = provider
        self.tool_registry = tool_registry or ToolRegistry()
        self.hook_registry = hook_registry or HookRegistry()
        self.permission_callback = permission_callback

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
        from crowe_synapse_engine.aicl import AICLMessage, Act, aicl_chunk

        from crowe_synapse_engine.runtime.dispatcher import resolve_model

        client = _resolve_client(self.provider)
        # Resolve the logical model name to the real deployment string. Logical
        # names like "crowelm-pro" never reach the upstream API; the resolver
        # returns the entry's backend_name (or the name itself when no alias
        # mapping exists). Brand Veil seam: the runtime never logs which
        # vendor model actually answered the call.
        resolved = resolve_model(model)
        upstream_model = resolved.backend_name if resolved is not None else model
        resolved_tools = self.tool_registry.resolve(tools) if tools else []
        tool_schemas = [tool.to_openai_schema() for tool in resolved_tools]

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt or ""},
            {"role": "user", "content": user_prompt},
        ]
        await self.hook_registry.dispatch(
            HookEvent.USER_PROMPT_SUBMIT,
            {"agent_name": agent_name, "user_prompt": user_prompt, "meta": meta or {}},
        )

        # Emit AICL INTENT at run start. Threads subsequent COMMIT to this id.
        intent_msg = AICLMessage(
            act=Act.INTENT,
            from_agent=agent_name,
            subject=user_prompt[:200],
            payload={"model": model, "max_turns": max_turns},
        )
        yield aicl_chunk(intent_msg)

        rounds_used = 0
        stop_reason = "max_turns"
        try:
            while rounds_used < max_turns:
                rounds_used += 1
                kwargs: dict[str, Any] = {
                    "model": upstream_model,
                    "messages": messages,
                    "stream": True,
                }
                if tool_schemas:
                    kwargs["tools"] = tool_schemas

                stream = client.chat.completions.create(**kwargs)
                text_buf: list[str] = []
                # Accumulator for streamed tool calls keyed by index.
                pending: dict[int, dict[str, Any]] = {}
                finish_reason: str | None = None

                for chunk in stream:
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    delta = choice.delta

                    reasoning = getattr(delta, "reasoning", None) or getattr(
                        delta, "reasoning_content", None
                    )
                    if reasoning:
                        yield RuntimeChunk(kind=ChunkKind.REASONING, text=reasoning)

                    if delta and delta.content:
                        text_buf.append(delta.content)
                        yield RuntimeChunk(kind=ChunkKind.TEXT, text=delta.content)

                    if delta and getattr(delta, "tool_calls", None):
                        for tc in delta.tool_calls:
                            slot = pending.setdefault(
                                tc.index,
                                {"id": "", "name": "", "arguments": ""},
                            )
                            if getattr(tc, "id", None):
                                slot["id"] = tc.id
                            fn = getattr(tc, "function", None)
                            if fn is not None:
                                if getattr(fn, "name", None):
                                    slot["name"] = fn.name
                                if getattr(fn, "arguments", None):
                                    slot["arguments"] += fn.arguments

                    if choice.finish_reason:
                        finish_reason = choice.finish_reason

                # Stream for this round is done. Commit assistant turn.
                assistant_msg: dict[str, Any] = {"role": "assistant"}
                text_out = "".join(text_buf)
                if text_out:
                    assistant_msg["content"] = text_out
                if pending:
                    assistant_msg["tool_calls"] = [
                        {
                            "id": slot["id"] or f"call_{uuid.uuid4().hex[:12]}",
                            "type": "function",
                            "function": {
                                "name": slot["name"],
                                "arguments": slot["arguments"] or "{}",
                            },
                        }
                        for slot in pending.values()
                        if slot["name"]
                    ]
                messages.append(assistant_msg)

                if finish_reason != "tool_calls" or not pending:
                    stop_reason = finish_reason or "stop"
                    break

                # Execute every tool call from this round, in order.
                for slot in pending.values():
                    if not slot["name"]:
                        continue
                    try:
                        arguments = json.loads(slot["arguments"] or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    call = ToolCall(
                        id=slot["id"] or f"call_{uuid.uuid4().hex[:12]}",
                        name=slot["name"],
                        arguments=arguments,
                    )
                    delegate_msg = AICLMessage(
                        act=Act.DELEGATE,
                        from_agent=agent_name,
                        to_agent=f"tool:{call.name}",
                        subject=f"execute tool {call.name}",
                        parent_message_id=intent_msg.id,
                        payload={"tool_call_id": call.id, "arguments": call.arguments},
                    )
                    yield aicl_chunk(delegate_msg)
                    yield RuntimeChunk(
                        kind=ChunkKind.TOOL_CALL,
                        tool_name=call.name,
                        tool_args=call.arguments,
                        meta={"tool_call_id": call.id},
                    )

                    hook_outcome = await self.hook_registry.dispatch(
                        HookEvent.PRE_TOOL_USE,
                        {
                            "agent_name": agent_name,
                            "tool_name": call.name,
                            "tool_args": call.arguments,
                        },
                    )
                    if hook_outcome.block:
                        block_reason = hook_outcome.reason or "blocked by hook"
                        yield RuntimeChunk(
                            kind=ChunkKind.HOOK_BLOCKED,
                            tool_name=call.name,
                            reason=block_reason,
                            meta={"tool_call_id": call.id},
                        )
                        yield aicl_chunk(
                            AICLMessage(
                                act=Act.DISPUTE,
                                from_agent=agent_name,
                                to_agent=f"tool:{call.name}",
                                subject=f"tool blocked by hook: {block_reason}",
                                parent_message_id=delegate_msg.id,
                                evidence=["hook:PreToolUse"],
                                payload={
                                    "tool_call_id": call.id,
                                    "reason": block_reason,
                                },
                            )
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call.id,
                                "content": f"Blocked by hook: {block_reason}",
                            }
                        )
                        continue

                    result = await self.tool_registry.run_tool(
                        call, permission_callback=self.permission_callback
                    )
                    permission_denied = result.is_error and result.content.startswith(
                        "Permission denied"
                    )
                    yield RuntimeChunk(
                        kind=(
                            ChunkKind.PERMISSION_DENIED
                            if permission_denied
                            else ChunkKind.TOOL_RESULT
                        ),
                        tool_name=result.name,
                        tool_result=result.content,
                        meta={"tool_call_id": result.id, "is_error": result.is_error},
                    )
                    yield aicl_chunk(
                        AICLMessage(
                            act=Act.REPORT,
                            from_agent=f"tool:{result.name}",
                            to_agent=agent_name,
                            subject=(
                                f"tool failed: {result.content[:160]}"
                                if result.is_error
                                else f"tool completed: {result.content[:160]}"
                            ),
                            parent_message_id=delegate_msg.id,
                            confidence=(
                                0.0
                                if permission_denied
                                else 0.25
                                if result.is_error
                                else 1.0
                            ),
                            evidence=[f"tool_call:{result.id}"],
                            requires_human=permission_denied,
                            payload={
                                "tool_call_id": result.id,
                                "is_error": result.is_error,
                            },
                        )
                    )
                    if permission_denied:
                        yield aicl_chunk(
                            AICLMessage(
                                act=Act.UNCERTAIN,
                                from_agent=agent_name,
                                subject=f"permission denied for tool {result.name}",
                                parent_message_id=delegate_msg.id,
                                confidence=0.0,
                                requires_human=True,
                                payload={"tool_call_id": result.id},
                            )
                        )
                    await self.hook_registry.dispatch(
                        HookEvent.POST_TOOL_USE,
                        {
                            "agent_name": agent_name,
                            "tool_name": call.name,
                            "tool_result": result.content,
                            "is_error": result.is_error,
                        },
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": result.id,
                            "content": result.content,
                        }
                    )

            if rounds_used >= max_turns and stop_reason == "max_turns":
                yield aicl_chunk(
                    AICLMessage(
                        act=Act.UNCERTAIN,
                        from_agent=agent_name,
                        subject=f"max turns reached before final answer: {max_turns}",
                        parent_message_id=intent_msg.id,
                        confidence=0.2,
                        payload={"rounds_used": rounds_used, "max_turns": max_turns},
                    )
                )
                yield RuntimeChunk(
                    kind=ChunkKind.ERROR,
                    text=f"Max turns ({max_turns}) reached without final answer.",
                )

        except Exception as exc:
            yield RuntimeChunk(
                kind=ChunkKind.ERROR,
                text=f"{type(exc).__name__}: {exc}",
                meta={"exception_type": type(exc).__name__},
            )
            return
        finally:
            await self.hook_registry.dispatch(
                HookEvent.STOP,
                {"agent_name": agent_name, "rounds_used": rounds_used},
            )

        # Emit AICL COMMIT at successful run end, threaded to the opening INTENT.
        commit_msg = AICLMessage(
            act=Act.COMMIT,
            from_agent=agent_name,
            subject=f"run complete: {stop_reason}",
            parent_message_id=intent_msg.id,
            payload={"rounds_used": rounds_used, "stop_reason": stop_reason},
        )
        yield aicl_chunk(commit_msg)

        yield RuntimeChunk(
            kind=ChunkKind.DONE,
            meta={"rounds_used": rounds_used, "stop_reason": stop_reason},
        )
