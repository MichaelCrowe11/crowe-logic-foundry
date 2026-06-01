"""
IBM watsonx.ai provider — non-streaming chat with tool calling.

Wraps ``config.crowelm.watsonx_adapter`` so CroweLM brands marked
``provider: "watsonx"`` (in ``_BASE_MODEL_CHAIN`` and ``models.extra.json``)
flow through the same dispatcher contract as the OpenAI-compatible
providers. The adapter is HTTP-only (no SSE), so this provider buffers
each round into a single ``renderer.feed`` call. Tool calls returned by
watsonx in OpenAI-compatible shape are executed locally and the loop
continues until the model produces a final assistant message.

Credentials are loaded from ``~/.crowe-logic/ibm.env`` by the adapter.
"""

from __future__ import annotations

import json
import time
from typing import Any

from providers._shared import (
    AUTO_CONTINUE_NUDGE,
    build_forced_final_answer_prompt,
    build_tool_budget_warning,
    load_tools,
    looks_like_stalled_announcement,
    should_send_tool_budget_warning,
)


class WatsonxProvider:
    """Non-streaming watsonx.ai chat provider with OpenAI-style tool calling."""

    SUPPORTS_REASONING: bool = False
    MAX_ROUNDS: int = 20

    def __init__(
        self,
        model: str,
        system_instructions: str,
        label: str = "CroweLM",
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ):
        # `model` here is the CroweLM brand_id (e.g. "crowelm-nexus") OR the
        # upstream watsonx model id (e.g. "ibm/granite-3-8b-instruct").
        # `watsonx_adapter.resolve` accepts either form.
        self.model = model
        self.label = label
        self.system_instructions = system_instructions
        self.messages: list[dict] = [{"role": "system", "content": system_instructions}]
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._tool_call_seq = 0

    # ---------------------------------------------------------------- public
    def add_user_message(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def set_system_instructions(self, system_instructions: str) -> None:
        self.system_instructions = system_instructions
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = system_instructions

    # --------------------------------------------------------------- helpers
    def _next_tool_call_id(self) -> str:
        self._tool_call_seq += 1
        return f"wxtc{self._tool_call_seq:07d}"

    def _call_watsonx(self, tools: list[dict] | None) -> dict:
        from config.crowelm import watsonx_adapter

        env = watsonx_adapter._load_env()
        from config.crowelm.brand_registry import resolve as _resolve

        brand = _resolve(self.model)
        if brand is None:
            raise watsonx_adapter.WatsonxError(
                f"watsonx provider: unknown brand or base model {self.model!r}. "
                "Add it to config/crowelm/brand_registry.py or use a known brand id."
            )
        project_id = env.get("WATSONX_PROJECT_ID")
        if not project_id:
            raise watsonx_adapter.WatsonxError(
                "WATSONX_PROJECT_ID missing from ~/.crowe-logic/ibm.env"
            )

        payload: dict[str, Any] = {
            "model_id": brand.tuned_asset or brand.base_model,
            "project_id": project_id,
            "messages": self.messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice_option"] = "auto"

        return watsonx_adapter._post(
            watsonx_adapter._wx_url(env, "/ml/v1/text/chat"),
            payload,
            env,
        )

    # --------------------------------------------------------- forced finish
    def _force_final_response(self, renderer, session_state, full_response: str) -> str:
        self.messages.append(
            {
                "role": "user",
                "content": build_forced_final_answer_prompt(self.MAX_ROUNDS),
            }
        )

        try:
            renderer.set_spinner("finalizing answer...")
            data = self._call_watsonx(tools=None)
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

        msg = (data.get("choices") or [{}])[0].get("message", {}) or {}
        text = (msg.get("content") or "").strip()
        if text:
            renderer.feed(text)
        renderer.stop_spinner()

        if not text:
            raise RuntimeError(
                f"{self.label} exceeded {self.MAX_ROUNDS} tool rounds and did not "
                "produce a forced final response."
            )

        full_response += text
        self.messages.append({"role": "assistant", "content": text})
        renderer.finish(session_state=session_state)
        return full_response

    # ----------------------------------------------------------- main entry
    def stream_response(
        self, console, render_tool_card, session_state, _get_orchestrator, renderer=None
    ):
        tool_schemas, tool_map = load_tools()

        if renderer is None:
            from cli.renderer import StreamRenderer

            favicon = session_state.get("favicon", "")
            renderer = StreamRenderer(console, self.label, favicon=favicon)

        full_response = ""
        budget_warning_sent = False
        auto_continues_used = 0
        MAX_AUTO_CONTINUES = 2

        for _round in range(self.MAX_ROUNDS):
            try:
                if _round == 0:
                    renderer.start()
                else:
                    renderer.set_spinner("thinking...")
                renderer.set_spinner("contacting watsonx...")
                data = self._call_watsonx(tools=tool_schemas or None)
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

            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message", {}) or {}
            content = msg.get("content") or ""
            tool_calls = msg.get("tool_calls") or []

            if content:
                renderer.feed(content)

            response_text = renderer.current_segment_text
            if response_text.strip():
                full_response += response_text

            if not tool_calls:
                if (
                    auto_continues_used < MAX_AUTO_CONTINUES
                    and looks_like_stalled_announcement(response_text)
                ):
                    self.messages.append(
                        {"role": "assistant", "content": response_text}
                    )
                    self.messages.append(
                        {"role": "user", "content": AUTO_CONTINUE_NUDGE}
                    )
                    auto_continues_used += 1
                    renderer.end_segment()
                    continue

                renderer.finish(session_state=session_state)
                self.messages.append({"role": "assistant", "content": response_text})
                break

            renderer.end_segment()

            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": response_text or None,
                "tool_calls": [],
            }
            normalized_calls: list[dict] = []
            for tc in tool_calls:
                fn = tc.get("function") or {}
                name = (fn.get("name") or "").strip() or "invalid_tool_call"
                args_json = fn.get("arguments") or ""
                if not isinstance(args_json, str):
                    args_json = json.dumps(args_json)
                local_id = self._next_tool_call_id()
                assistant_msg["tool_calls"].append(
                    {
                        "id": local_id,
                        "type": "function",
                        "function": {"name": name, "arguments": args_json},
                    }
                )
                normalized_calls.append(
                    {"id": local_id, "name": name, "arguments": args_json}
                )
            self.messages.append(assistant_msg)

            round_tool_names: list[str] = []
            for call in normalized_calls:
                name = call["name"]
                args_json = call["arguments"]
                round_tool_names.append(name)

                renderer.set_spinner(f"running {name}...")
                _tool_start = time.monotonic()

                func = tool_map.get(name)
                failed = False
                if name == "invalid_tool_call":
                    result_str = json.dumps(
                        {
                            "error": "Model emitted a tool call without a function name.",
                            "raw_arguments": args_json[:2000],
                        }
                    )
                    failed = True
                elif func:
                    try:
                        args = json.loads(args_json) if args_json else {}
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
                    console,
                    name,
                    args_json,
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
                    args=args_json,
                )

                _get_orchestrator().record_execution(
                    tool_name=name,
                    arguments=args_json,
                    output=result_str[:10000],
                    duration_ms=duration_ms,
                )

                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": result_str[:50000],
                    }
                )

            if should_send_tool_budget_warning(
                _round + 1, round_tool_names, budget_warning_sent
            ):
                self.messages.append(
                    {
                        "role": "user",
                        "content": build_tool_budget_warning(
                            _round + 1, self.MAX_ROUNDS
                        ),
                    }
                )
                budget_warning_sent = True
        else:
            return self._force_final_response(renderer, session_state, full_response)

        if console is not None:
            console.print()
        return full_response
