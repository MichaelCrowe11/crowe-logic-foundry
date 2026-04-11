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
    MAX_ROUNDS: int = 10
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

                    response = stream.get_final_response()
            except Exception:
                renderer.stop_spinner()
                raise

            renderer.stop_spinner()
            self.previous_response_id = response.id
            previous_response_id = response.id

            if not saw_reasoning_delta:
                self._emit_final_reasoning(response, renderer)

            if not saw_text_delta:
                response_text = getattr(response, "output_text", "") or ""
                if response_text:
                    renderer.feed(response_text)

            response_text = renderer.current_segment_text
            if response_text:
                full_response += response_text

            function_calls = [
                item for item in (getattr(response, "output", []) or [])
                if getattr(item, "type", None) == "function_call"
            ]

            if not function_calls:
                renderer.finish(session_state=session_state)
                break

            renderer.end_segment()
            pending_input = []

            for call in function_calls:
                name = call.name
                arguments_json = call.arguments

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

                _get_orchestrator().record_execution(
                    tool_name=name,
                    arguments=arguments_json,
                    output=result_str[:10000],
                    duration_ms=duration_ms,
                )

                pending_input.append({
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": result_str[:50000],
                })

        if console is not None:
            console.print()
        return full_response
