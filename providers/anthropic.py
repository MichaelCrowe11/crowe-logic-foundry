"""
Anthropic provider — Claude models on Azure AI Foundry.

Azure AI Foundry now supports Anthropic's Claude models via a native
Anthropic-compatible surface at `/anthropic` (not OpenAI-compatible).
This provider uses the official `anthropic` SDK with Azure API-key auth.

Deployed models:
  - claude-opus-4-6 (CroweLM Opus) — frontier reasoning, 200K context
  - Claude Sonnet, Haiku variants (when deployed)

The endpoint pattern:
  https://<resource>.openai.azure.com/anthropic

Note: Anthropic's API differs from OpenAI's — no tool_choice, different
delta streaming format, reasoning surfaced as thinking blocks.
"""

import json
import time
from typing import Any, Optional, Union

from providers._shared import (
    build_forced_final_answer_prompt,
    build_tool_budget_warning,
    should_send_tool_budget_warning,
)
from tools import user_functions as _tools


class AnthropicProvider:
    """Anthropic SDK provider for Azure AI Foundry Claude deployments."""

    SUPPORTS_REASONING: bool = True
    MAX_ROUNDS: int = 20

    def __init__(self, model: str, system_instructions: str, endpoint: str, api_key: str,
                 label: str = "Claude"):
        from anthropic import Anthropic

        self.model = model
        self.label = label
        self.system_instructions = system_instructions
        self.endpoint = endpoint
        self.messages: list[dict[str, Any]] = []
        self._system_block = {"type": "text", "text": system_instructions}

        # Normalize endpoint: Azure expects /anthropic base path
        base_url = endpoint.rstrip("/")
        if not base_url.endswith("/anthropic"):
            base_url += "/anthropic"

        self.client = Anthropic(api_key=api_key, base_url=base_url)

    def add_user_message(self, content: str):
        self.messages.append({"role": "user", "content": content})

    def set_system_instructions(self, system_instructions: str) -> None:
        """Update the active system prompt for cached provider instances."""
        self.system_instructions = system_instructions
        self._system_block = {"type": "text", "text": system_instructions}

    @staticmethod
    def _decode_tool_input(raw_input: str) -> tuple[dict[str, Any], str | None]:
        """Decode a streamed tool JSON payload without crashing the session."""
        if not raw_input:
            return {}, None
        try:
            parsed = json.loads(raw_input)
        except json.JSONDecodeError as exc:
            return {}, f"{type(exc).__name__}: {exc}"
        if not isinstance(parsed, dict):
            return {}, f"TypeError: tool arguments must decode to an object, got {type(parsed).__name__}"
        return parsed, None

    def _build_tool_schemas(self) -> list[dict]:
        """Convert Foundry tools to Anthropic tool format."""
        import inspect

        tools = []
        for func in _tools:
            sig = inspect.signature(func)
            doc = (func.__doc__ or "").strip()
            description = doc.split("\n")[0] if doc else func.__name__

            properties = {}
            required = []
            for pname, param in sig.parameters.items():
                ptype = "string"
                annotation = param.annotation
                if annotation == int:
                    ptype = "integer"
                elif annotation == float:
                    ptype = "number"
                elif annotation == bool:
                    ptype = "boolean"

                pdesc = ""
                for line in doc.split("\n"):
                    line = line.strip()
                    if line.startswith(f":param {pname}:"):
                        pdesc = line.split(":", 2)[-1].strip()
                        break

                properties[pname] = {"type": ptype}
                if pdesc:
                    properties[pname]["description"] = pdesc

                if param.default is inspect.Parameter.empty:
                    required.append(pname)

            tools.append({
                "name": func.__name__,
                "description": description,
                "input_schema": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            })
        return tools

    def _force_final_response(self, renderer, session_state, full_response: str) -> str:
        """Run one final no-tools pass after the hard tool budget is exhausted."""
        self.messages.append({
            "role": "user",
            "content": build_forced_final_answer_prompt(self.MAX_ROUNDS),
        })

        try:
            renderer.set_spinner("finalizing answer...")
            stream = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=[self._system_block],
                messages=self.messages,
                stream=True,
            )

            for event in stream:
                if event.type == "content_block_delta":
                    if event.delta.type == "thinking_delta" and self.SUPPORTS_REASONING:
                        renderer.feed_reasoning(event.delta.thinking)
                    elif event.delta.type == "text_delta":
                        renderer.feed(event.delta.text)
                elif event.type == "message_stop":
                    break
        except KeyboardInterrupt:
            if hasattr(renderer, "abort"):
                renderer.abort(session_state=session_state)
            else:
                renderer.stop_spinner()
            raise
        except Exception:
            if hasattr(renderer, "abort"):
                renderer.abort(session_state=session_state)
            else:
                renderer.stop_spinner()
            raise

        renderer.stop_spinner()
        response_text = renderer.current_segment_text
        if not response_text.strip():
            raise RuntimeError(
                f"{self.label} exceeded {self.MAX_ROUNDS} tool rounds and did not produce a forced final response."
            )

        full_response += response_text
        self.messages.append({"role": "assistant", "content": response_text})
        renderer.finish(session_state=session_state)
        return full_response

    def stream_response(self, console, render_tool_card, session_state, _get_orchestrator,
                        renderer=None):
        """Stream a response with tool-calling loop using Anthropic API."""
        from tools import user_functions

        tool_map = {f.__name__: f for f in user_functions}
        tool_schemas = self._build_tool_schemas()

        if renderer is None:
            from cli.renderer import StreamRenderer
            favicon = session_state.get("favicon", "")
            renderer = StreamRenderer(console, self.label, favicon=favicon)

        full_response = ""
        budget_warning_sent = False

        for _round in range(self.MAX_ROUNDS):
            try:
                if _round == 0:
                    renderer.start()
                else:
                    renderer.set_spinner("thinking...")

                create_kwargs = {
                    "model": self.model,
                    "max_tokens": 4096,
                    "system": [self._system_block],
                    "messages": self.messages,
                    "stream": True,
                }
                if tool_schemas:
                    create_kwargs["tools"] = tool_schemas

                stream = self.client.messages.create(**create_kwargs)

                tool_use_blocks = []
                tool_use_blocks_by_index = {}

                for event in stream:
                    # Content block start
                    if event.type == "content_block_start":
                        if event.content_block.type == "thinking":
                            # Claude's reasoning/thinking blocks
                            pass
                        elif event.content_block.type == "tool_use":
                            block = {
                                "id": event.content_block.id,
                                "name": event.content_block.name,
                                "input": "",
                            }
                            tool_use_blocks.append(block)
                            block_index = getattr(event, "index", None)
                            if block_index is not None:
                                tool_use_blocks_by_index[block_index] = block

                    # Content block delta (streaming text/thinking)
                    elif event.type == "content_block_delta":
                        if event.delta.type == "thinking_delta":
                            if self.SUPPORTS_REASONING:
                                renderer.feed_reasoning(event.delta.thinking)
                        elif event.delta.type == "text_delta":
                            token = event.delta.text
                            renderer.feed(token)
                        elif event.delta.type == "input_json_delta":
                            # Accumulating tool arguments
                            if tool_use_blocks:
                                block_index = getattr(event, "index", None)
                                target_block = (
                                    tool_use_blocks_by_index.get(block_index)
                                    if block_index is not None else None
                                ) or tool_use_blocks[-1]
                                target_block["input"] += event.delta.partial_json

                    # Message stop
                    elif event.type == "message_stop":
                        break

            except KeyboardInterrupt:
                if hasattr(renderer, "abort"):
                    renderer.abort(session_state=session_state)
                else:
                    renderer.stop_spinner()
                raise
            except Exception:
                if hasattr(renderer, "abort"):
                    renderer.abort(session_state=session_state)
                else:
                    renderer.stop_spinner()
                raise

            response_text = renderer.current_segment_text
            if response_text.strip():
                full_response += response_text

            # If no tool calls, we're done
            if not tool_use_blocks:
                renderer.finish(session_state=session_state)
                self.messages.append({"role": "assistant", "content": response_text})
                break

            # Finalize segment before tool execution
            renderer.end_segment()
            renderer.stop_spinner()
            round_tool_names = [tb["name"] for tb in tool_use_blocks]

            for tb in tool_use_blocks:
                parsed_input, input_error = self._decode_tool_input(tb["input"])
                tb["parsed_input"] = parsed_input
                tb["input_error"] = input_error

            # Build assistant message with content + tool uses
            assistant_content = []
            if response_text.strip():
                assistant_content.append({"type": "text", "text": response_text})
            for tb in tool_use_blocks:
                assistant_content.append({
                    "type": "tool_use",
                    "id": tb["id"],
                    "name": tb["name"],
                    "input": tb["parsed_input"],
                })

            self.messages.append({"role": "assistant", "content": assistant_content})

            # Execute tools
            for tb in tool_use_blocks:
                name = tb["name"]
                args_json = tb["input"]

                renderer.set_spinner(f"running {name}...")
                _tool_start = time.monotonic()

                func = tool_map.get(name)
                failed = False
                if tb.get("input_error"):
                    result_str = json.dumps({
                        "error": f"Invalid tool arguments for {name}: {tb['input_error']}",
                        "raw_arguments": args_json[:2000],
                    })
                    failed = True
                elif func:
                    try:
                        args = tb.get("parsed_input", {})
                        result = func(**args)
                        result_str = str(result) if result is not None else ""
                    except Exception as e:
                        result_str = json.dumps({"error": f"{type(e).__name__}: {e}"})
                        failed = True
                else:
                    result_str = json.dumps({"error": f"Unknown tool: {name}"})
                    failed = True

                duration_ms = int((time.monotonic() - _tool_start) * 1000)
                renderer.stop_spinner()

                render_tool_card(
                    console, name, json.dumps(args_json) if isinstance(args_json, dict) else args_json,
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
                    args=json.dumps(args_json) if isinstance(args_json, dict) else args_json,
                )

                _get_orchestrator().record_execution(
                    tool_name=name,
                    arguments=json.dumps(args_json) if isinstance(args_json, dict) else args_json,
                    output=result_str[:10000],
                    duration_ms=duration_ms,
                )

                # Anthropic requires tool_result blocks
                self.messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tb["id"],
                        "content": result_str[:50000],
                    }],
                })

            if should_send_tool_budget_warning(_round + 1, round_tool_names, budget_warning_sent):
                self.messages.append({
                    "role": "user",
                    "content": build_tool_budget_warning(_round + 1, self.MAX_ROUNDS),
                })
                budget_warning_sent = True
        else:
            return self._force_final_response(renderer, session_state, full_response)

        if console is not None:
            console.print()
        return full_response
