"""
Azure OpenAI provider — CroweLM models on Crowe Logic's own Azure AI Foundry.

Targets the OpenAI-compatible `/openai/v1/` surface of an Azure AI Foundry
resource (not the Azure AI Agents SDK). Authenticates with an API key, so it
works without any Azure identity setup.

This is the primary tier for the Crowe Logic Kernel stack — models deployed
inside the `crowelogicos-4667` resource (CroweLM Core = Kimi-K2.5,
CroweLM Kernel = gpt-5.4-nano). Because the endpoint is OpenAI-compatible,
this provider is structurally identical to `NvidiaProvider`.
"""

import json
import time
import inspect
from openai import OpenAI


def _build_tool_schemas(user_functions: set) -> list[dict]:
    """Convert the Foundry's function tools into OpenAI tool schemas."""
    tools = []
    for func in user_functions:
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
            "type": "function",
            "function": {
                "name": func.__name__,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        })
    return tools


class AzureOpenAIProvider:
    """
    OpenAI-compatible provider for Azure AI Foundry model deployments.

    Uses the `/openai/v1/` surface with API-key authentication — no
    DefaultAzureCredential, no Azure AI Agents SDK, no `.agent_id` file.
    """

    def __init__(self, model: str, system_instructions: str, endpoint: str, api_key: str,
                 label: str = "CroweLM"):
        # Azure surface looks like:
        #   https://<resource>.openai.azure.com/openai/v1/
        # The OpenAI SDK expects a base_url that points at the "v1" root so it can
        # append `/chat/completions`. We accept a few shapes and normalize.
        base_url = endpoint.rstrip("/")
        if not base_url.endswith("/v1") and "/openai/v1" not in base_url:
            if base_url.endswith("/openai"):
                base_url += "/v1"
            else:
                base_url += "/openai/v1"

        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.label = label
        self.endpoint = endpoint
        self.system_instructions = system_instructions
        self.messages = [{"role": "system", "content": system_instructions}]
        self._tool_schemas = None
        self._tool_map = None

    def _get_tools(self):
        """Lazy-load tool schemas and function map."""
        if self._tool_schemas is None:
            from tools import user_functions
            self._tool_schemas = _build_tool_schemas(user_functions)
            self._tool_map = {f.__name__: f for f in user_functions}
        return self._tool_schemas, self._tool_map

    def add_user_message(self, content: str):
        self.messages.append({"role": "user", "content": content})

    def stream_response(self, console, render_tool_card, session_state, _get_orchestrator):
        """
        Stream a response with tool calling loop.

        Same interface as NvidiaProvider/OllamaProvider/OpenRouterProvider —
        drop-in compatible with the CLI's smart routing.
        """
        from cli.renderer import StreamRenderer

        tool_schemas, tool_map = self._get_tools()
        favicon = session_state.get("favicon", "")
        renderer = StreamRenderer(console, self.label, "Azure", favicon=favicon)

        max_rounds = 10
        full_response = ""

        for _round in range(max_rounds):
            tool_calls_accumulator = {}

            try:
                if _round == 0:
                    renderer.start()
                else:
                    renderer.set_spinner("thinking...")

                create_kwargs = {
                    "model": self.model,
                    "messages": self.messages,
                    "stream": True,
                }
                if tool_schemas:
                    create_kwargs["tools"] = tool_schemas

                stream = self.client.chat.completions.create(**create_kwargs)

                for chunk in stream:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if not delta:
                        continue

                    # Kimi K2.5 returns reasoning on a separate field
                    reasoning = getattr(delta, "reasoning", None) or getattr(delta, "reasoning_content", None)
                    if reasoning:
                        renderer.feed_reasoning(reasoning)

                    if delta.content:
                        renderer.feed(delta.content)

                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_accumulator:
                                tool_calls_accumulator[idx] = {
                                    "id": tc.id or "",
                                    "name": tc.function.name if tc.function and tc.function.name else "",
                                    "arguments": "",
                                }
                            if tc.id:
                                tool_calls_accumulator[idx]["id"] = tc.id
                            if tc.function:
                                if tc.function.name:
                                    tool_calls_accumulator[idx]["name"] = tc.function.name
                                if tc.function.arguments:
                                    tool_calls_accumulator[idx]["arguments"] += tc.function.arguments

                    finish = chunk.choices[0].finish_reason if chunk.choices else None
                    if finish in ("stop", "tool_calls"):
                        break

            except Exception:
                renderer.stop_spinner()
                raise

            # Capture THIS round's content only — current_segment_text reads
            # the live _text_chunks before they're cleared by _stop_md_live().
            # Using full_text here would include prior rounds' content and
            # corrupt the message history (the model would echo it back,
            # causing the duplication the user reported).
            response_text = renderer.current_segment_text
            if response_text.strip():
                full_response += response_text

            if not tool_calls_accumulator:
                renderer.finish(session_state=session_state)
                self.messages.append({"role": "assistant", "content": response_text})
                break

            # Finalize the current segment before tool execution. This flushes
            # the markdown Live to scrollback, finalizes any reasoning panel,
            # and flushes any post-content tail reasoning — so the next round
            # starts with completely empty buffers.
            renderer.end_segment()
            renderer.stop_spinner()

            assistant_msg = {"role": "assistant", "content": response_text or None, "tool_calls": []}
            for idx in sorted(tool_calls_accumulator.keys()):
                tc = tool_calls_accumulator[idx]
                assistant_msg["tool_calls"].append({
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"]},
                })
            self.messages.append(assistant_msg)

            for idx in sorted(tool_calls_accumulator.keys()):
                tc = tool_calls_accumulator[idx]
                name = tc["name"]
                args_json = tc["arguments"]

                renderer.set_spinner(f"running {name}...")
                _tool_start = time.monotonic()

                func = tool_map.get(name)
                if func:
                    try:
                        args = json.loads(args_json) if args_json else {}
                        result = func(**args)
                        result_str = str(result) if result is not None else ""
                    except Exception as e:
                        result_str = json.dumps({"error": f"{type(e).__name__}: {e}"})
                else:
                    result_str = json.dumps({"error": f"Unknown tool: {name}"})

                duration_ms = int((time.monotonic() - _tool_start) * 1000)
                renderer.stop_spinner()

                failed = result_str.startswith('{"error"')
                render_tool_card(
                    console, name, args_json,
                    status="fail" if failed else "ok",
                    result=result_str,
                    duration_ms=duration_ms,
                )
                session_state["tool_count"] += 1

                _get_orchestrator().record_execution(
                    tool_name=name,
                    arguments=args_json,
                    output=result_str[:10000],
                    duration_ms=duration_ms,
                )

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_str[:50000],
                })

        console.print()
        return full_response
