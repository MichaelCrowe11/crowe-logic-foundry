"""
Azure OpenAI provider — CroweLM models on Crowe Logic's own Azure AI Foundry.

Targets the OpenAI-compatible `/openai/v1/` surface of an Azure AI Foundry
resource (not the Azure AI Agents SDK). Authenticates with an API key, so it
works without any Azure identity setup.

This is the primary tier for the CroweLM stack — models deployed inside the
`crowelogicos-4667` resource (CroweLM Pro = gpt-5.4-pro,
CroweLM Core = Kimi-K2.5, CroweLM Kernel = gpt-5.4-nano).

Most models use Chat Completions. `gpt-5.4-pro` currently requires the
Responses API, so this module exposes both code paths behind the same Azure
endpoint normalization.
"""

import json
import time
from types import SimpleNamespace
from typing import Any

from openai import OpenAI

from providers._shared import BaseOpenAIProvider, load_tools


class AzureOpenAIProvider(BaseOpenAIProvider):
    """OpenAI-compatible provider for Azure AI Foundry deployments.

    Uses the `/openai/v1/` surface with API-key authentication — no
    DefaultAzureCredential, no Azure AI Agents SDK, no `.agent_id` file.
    """

    def __init__(self, model: str, system_instructions: str, endpoint: str, api_key: str,
                 label: str = "CroweLM"):
        super().__init__(model, system_instructions, label)

        # Azure surface looks like:
        #   https://<resource>.openai.azure.com/openai/v1/
        # The OpenAI SDK expects a base_url that points at the "v1" root so
        # it can append `/chat/completions`. Accept a few shapes and
        # normalize.
        base_url = endpoint.rstrip("/")
        if not base_url.endswith("/v1") and "/openai/v1" not in base_url:
            if base_url.endswith("/openai"):
                base_url += "/v1"
            else:
                base_url += "/openai/v1"

        self.client = OpenAI(api_key=api_key, base_url=base_url)
        # cli/crowe_logic.py reads .endpoint to detect provider-recreate
        # cases when the user changes models mid-session — keep the
        # original (not the normalized base_url) so equality comparisons
        # against the env var stay stable.
        self.endpoint = endpoint


class AzureResponsesProvider:
    """Responses-API provider for Azure-hosted CroweLM reasoning models."""

    SUPPORTS_REASONING: bool = True
    MAX_ROUNDS: int = 20
    REASONING_CONFIG: dict[str, str] = {
        "effort": "medium",
        "summary": "auto",
    }

    def __init__(self, model: str, system_instructions: str, endpoint: str, api_key: str,
                 label: str = "CroweLM"):
        self.model = model
        self.label = label
        self.system_instructions = system_instructions
        self.messages: list[dict[str, Any]] = []
        self.previous_response_id: str | None = None

        base_url = endpoint.rstrip("/")
        if not base_url.endswith("/v1") and "/openai/v1" not in base_url:
            if base_url.endswith("/openai"):
                base_url += "/v1"
            else:
                base_url += "/openai/v1"

        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.endpoint = endpoint

    def add_user_message(self, content: str):
        self.messages.append({"role": "user", "content": content})

    def set_system_instructions(self, system_instructions: str) -> None:
        """Update the active system prompt for cached provider instances."""
        self.system_instructions = system_instructions

    @staticmethod
    def _to_response_tools(tool_schemas: list[dict]) -> list[dict]:
        """Convert chat-completions tool schemas into Responses API tool schemas."""
        response_tools = []
        for schema in tool_schemas:
            fn = schema.get("function", {})
            response_tools.append({
                "type": "function",
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {}),
            })
        return response_tools

    def _build_input_items(self) -> list[dict]:
        """Convert queued chat-style messages into Responses API input items."""
        items = []
        for msg in self.messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ("user", "system", "developer"):
                items.append({
                    "type": "message",
                    "role": role,
                    "content": [{"type": "input_text", "text": str(content)}],
                })
            elif role == "assistant":
                # Responses API doesn't accept a bare assistant role in Message
                # input items, so preserve prior assistant turns as developer
                # context on the first call.
                items.append({
                    "type": "message",
                    "role": "developer",
                    "content": [{
                        "type": "input_text",
                        "text": f"Previous assistant reply for context:\n{content}",
                    }],
                })
        self.messages = []
        return items

    @staticmethod
    def _emit_final_reasoning(response: Any, renderer) -> None:
        """Flush reasoning summaries from the final response if streaming missed them."""
        for item in (getattr(response, "output", []) or []):
            if getattr(item, "type", None) != "reasoning":
                continue
            for summary in (getattr(item, "summary", []) or []):
                text = getattr(summary, "text", "") or ""
                if text:
                    renderer.feed_reasoning(text)

    @staticmethod
    def _record_stream_function_call(
        stream_function_calls: dict[int, dict[str, str]],
        output_index: int | None,
        *,
        item: Any = None,
        name: str = "",
        arguments: str = "",
    ) -> None:
        """Capture function-call metadata from streaming events.

        Azure's Responses API occasionally surfaces tool calls in stream events
        even when the final `response.output` omits them. Keeping a local event-
        level accumulator prevents us from dropping a required tool output and
        poisoning `previous_response_id` for the next user turn.
        """
        if output_index is None:
            output_index = len(stream_function_calls)

        entry = stream_function_calls.setdefault(
            output_index,
            {"call_id": "", "name": "", "arguments": ""},
        )

        if item is not None:
            if getattr(item, "type", None) != "function_call":
                return
            call_id = getattr(item, "call_id", "") or ""
            item_name = getattr(item, "name", "") or ""
            item_arguments = getattr(item, "arguments", "") or ""
            if call_id:
                entry["call_id"] = call_id
            if item_name:
                entry["name"] = item_name
            if item_arguments:
                entry["arguments"] = item_arguments

        if name:
            entry["name"] = name
        if arguments:
            entry["arguments"] = arguments

    @classmethod
    def _extract_function_calls(
        cls,
        response: Any,
        stream_function_calls: dict[int, dict[str, str]],
    ) -> list[Any]:
        """Return function calls from the final response, or stream fallback."""
        function_calls = [
            item for item in (getattr(response, "output", []) or [])
            if getattr(item, "type", None) == "function_call"
        ]
        if function_calls:
            return function_calls

        fallback_calls = []
        for _, call in sorted(stream_function_calls.items()):
            if not any(call.values()):
                continue
            fallback_calls.append(SimpleNamespace(
                type="function_call",
                call_id=call["call_id"],
                name=call["name"],
                arguments=call["arguments"],
            ))
        return fallback_calls

    def stream_response(self, console, render_tool_card, session_state, _get_orchestrator,
                        renderer=None):
        """Run a Responses API loop with local function-tool execution."""
        tool_schemas, tool_map = load_tools()
        response_tools = self._to_response_tools(tool_schemas)

        if renderer is None:
            from cli.renderer import StreamRenderer
            favicon = session_state.get("favicon", "")
            renderer = StreamRenderer(console, self.label, favicon=favicon)

        full_response = ""
        pending_input = self._build_input_items()
        previous_response_id = self.previous_response_id

        for round_index in range(self.MAX_ROUNDS):
            saw_reasoning_delta = False
            saw_text_delta = False
            stream_function_calls: dict[int, dict[str, str]] = {}
            try:
                if round_index == 0:
                    renderer.start()
                else:
                    renderer.set_spinner("thinking...")

                with self.client.responses.stream(
                    model=self.model,
                    instructions=self.system_instructions,
                    input=pending_input,
                    previous_response_id=previous_response_id,
                    tools=response_tools,
                    max_output_tokens=4096,
                    reasoning=self.REASONING_CONFIG,
                ) as stream:
                    for event in stream:
                        event_type = getattr(event, "type", "")

                        if event_type == "response.reasoning_summary_text.delta":
                            delta = getattr(event, "delta", "") or ""
                            if delta:
                                saw_reasoning_delta = True
                                renderer.feed_reasoning(delta)
                            continue

                        if event_type == "response.output_text.delta":
                            delta = getattr(event, "delta", "") or ""
                            if delta:
                                saw_text_delta = True
                                renderer.feed(delta)
                            continue

                        if event_type == "response.output_item.added":
                            self._record_stream_function_call(
                                stream_function_calls,
                                getattr(event, "output_index", None),
                                item=getattr(event, "item", None),
                            )
                            continue

                        if event_type == "response.output_item.done":
                            self._record_stream_function_call(
                                stream_function_calls,
                                getattr(event, "output_index", None),
                                item=getattr(event, "item", None),
                            )
                            continue

                        if event_type == "response.function_call_arguments.done":
                            self._record_stream_function_call(
                                stream_function_calls,
                                getattr(event, "output_index", None),
                                name=getattr(event, "name", "") or "",
                                arguments=getattr(event, "arguments", "") or "",
                            )
                            continue

                    try:
                        response = stream.get_final_response()
                    except RuntimeError:
                        # Azure sometimes drops the stream before sending
                        # response.completed.  Build a synthetic response
                        # from the events we already consumed so the turn
                        # can still complete instead of crashing.
                        response = SimpleNamespace(
                            id=f"partial_{round_index}",
                            output=[
                                SimpleNamespace(**call)
                                for call in stream_function_calls.values()
                                if any(call.values())
                            ],
                            output_text=renderer.current_segment_text or "",
                        )
            except KeyboardInterrupt:
                if hasattr(renderer, "abort"):
                    renderer.abort(session_state=session_state)
                else:
                    renderer.stop_spinner()
                raise
            except Exception:
                renderer.stop_spinner()
                raise

            renderer.stop_spinner()
            response_id = response.id

            if not saw_reasoning_delta:
                self._emit_final_reasoning(response, renderer)

            if not saw_text_delta:
                response_text = getattr(response, "output_text", "") or ""
                if response_text:
                    renderer.feed(response_text)

            response_text = renderer.current_segment_text
            if response_text:
                full_response += response_text

            function_calls = self._extract_function_calls(response, stream_function_calls)

            if not function_calls:
                # Detect content-filter / policy refusals so we don't
                # silently swallow them or leave previous_response_id
                # pointing at a poisoned turn.
                refusal = getattr(response, "refusal", None) or ""
                if not refusal and response_text:
                    _lower = response_text.lower()
                    if any(phrase in _lower for phrase in (
                        "i cannot assist",
                        "i'm unable to",
                        "i can't help with",
                        "i'm not able to",
                        "as an ai",
                    )):
                        refusal = response_text

                if refusal:
                    renderer.finish(session_state=session_state)
                    self.previous_response_id = None
                    return full_response

                renderer.finish(session_state=session_state)
                self.previous_response_id = response_id
                break

            renderer.end_segment()
            pending_input = []

            for call in function_calls:
                name = call.name
                arguments_json = call.arguments
                call_id = getattr(call, "call_id", "") or ""

                renderer.set_spinner(f"running {name}...")
                tool_start = time.monotonic()

                func = tool_map.get(name)
                failed = False
                if func:
                    try:
                        args = json.loads(arguments_json) if isinstance(arguments_json, str) else arguments_json
                        result = func(**args)
                        result_str = str(result) if result is not None else ""
                    except Exception as exc:
                        result_str = json.dumps({"error": f"{type(exc).__name__}: {exc}"})
                        failed = True
                else:
                    result_str = json.dumps({"error": f"Unknown tool: {name}"})
                    failed = True

                duration_ms = int((time.monotonic() - tool_start) * 1000)
                renderer.stop_spinner()

                render_tool_card(
                    console,
                    name,
                    arguments_json,
                    status="fail" if failed else "ok",
                    result=result_str,
                    duration_ms=duration_ms,
                )
                session_state["tool_count"] += 1
                from cli.branding import record_action
                record_action(
                    session_state,
                    name=name,
                    status="fail" if failed else "ok",
                    result=result_str,
                    duration_ms=duration_ms,
                    args=arguments_json,
                )

                _get_orchestrator().record_execution(
                    tool_name=name,
                    arguments=arguments_json,
                    output=result_str[:10000],
                    duration_ms=duration_ms,
                )

                if not call_id:
                    raise RuntimeError(
                        "Responses API emitted a function call without a call_id; "
                        "cannot safely submit tool output."
                    )

                pending_input.append({
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": result_str[:50000],
                })

            previous_response_id = response_id
        else:
            raise RuntimeError(
                f"{self.label} exceeded {self.MAX_ROUNDS} tool rounds without a final response."
            )

        if console is not None:
            console.print()
        return full_response
