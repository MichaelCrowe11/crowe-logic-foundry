"""
OpenRouter provider — Chat Completions with streaming and tool calling.

Uses the OpenAI Python SDK pointed at OpenRouter's API.
Works with any OpenAI-compatible endpoint (Together AI, Fireworks, Groq, etc).
"""

import json
import inspect
from openai import OpenAI


def _build_tool_schemas(user_functions: set) -> list[dict]:
    """Convert the Foundry's function tools into OpenAI tool schemas."""
    tools = []
    for func in user_functions:
        sig = inspect.signature(func)
        doc = (func.__doc__ or "").strip()

        # Extract first line as description
        description = doc.split("\n")[0] if doc else func.__name__

        # Build parameters from signature
        properties = {}
        required = []
        for pname, param in sig.parameters.items():
            ptype = "string"  # default
            annotation = param.annotation
            if annotation == int:
                ptype = "integer"
            elif annotation == float:
                ptype = "number"
            elif annotation == bool:
                ptype = "boolean"

            # Extract param description from docstring :param lines
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


class OpenRouterProvider:
    """Chat Completions provider for OpenRouter (or any OpenAI-compatible API)."""

    def __init__(self, api_key: str, base_url: str, model: str, system_instructions: str):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
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

        Yields text chunks for live display. Handles tool calls automatically,
        executing them and feeding results back until the model is done.
        """
        from rich.markdown import Markdown
        from rich.live import Live
        from rich.spinner import Spinner
        import time

        tool_schemas, tool_map = self._get_tools()

        max_rounds = 10  # prevent infinite tool loops
        full_response = ""

        for _round in range(max_rounds):
            text_chunks = []
            tool_calls_accumulator = {}  # index -> {id, name, arguments}
            streaming_started = False
            md_live = None
            spinner = None
            spin_live = None

            def _start_spinner(label):
                nonlocal spinner, spin_live
                _stop_spinner()
                spinner = Spinner("dots", text=f"  [#bfa669]{label}[/#bfa669]", style="#bfa669")
                spin_live = Live(spinner, console=console, refresh_per_second=12, transient=True)
                spin_live.start()

            def _stop_spinner():
                nonlocal spinner, spin_live
                if spin_live:
                    spin_live.stop()
                    spin_live = None
                    spinner = None

            def _stop_md_live():
                nonlocal md_live
                if md_live:
                    full = "".join(text_chunks)
                    if full.strip():
                        md_live.update(Markdown(full))
                    md_live.stop()
                    md_live = None

            try:
                _start_spinner("thinking...")

                stream = self.client.chat.completions.create(
                    model=self.model,
                    messages=self.messages,
                    tools=tool_schemas if tool_schemas else None,
                    stream=True,
                )

                for chunk in stream:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if not delta:
                        continue

                    # Text content
                    if delta.content:
                        if not streaming_started:
                            _stop_spinner()
                            streaming_started = True
                            md_live = Live(
                                Markdown(""),
                                console=console,
                                refresh_per_second=8,
                                vertical_overflow="visible",
                            )
                            md_live.start()
                        text_chunks.append(delta.content)
                        md_live.update(Markdown("".join(text_chunks)))

                    # Tool calls (streamed incrementally)
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

                    # Finish reason
                    finish = chunk.choices[0].finish_reason if chunk.choices else None
                    if finish == "stop":
                        break
                    if finish == "tool_calls":
                        break

            finally:
                _stop_md_live()
                _stop_spinner()

            # Capture any text response
            response_text = "".join(text_chunks)
            if response_text.strip():
                full_response = response_text

            # If no tool calls, we're done
            if not tool_calls_accumulator:
                # Add assistant message to history
                self.messages.append({"role": "assistant", "content": response_text})
                break

            # Execute tool calls
            # First, add the assistant message with tool_calls to history
            assistant_msg = {"role": "assistant", "content": response_text or None, "tool_calls": []}
            for idx in sorted(tool_calls_accumulator.keys()):
                tc = tool_calls_accumulator[idx]
                assistant_msg["tool_calls"].append({
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"]},
                })
            self.messages.append(assistant_msg)

            # Execute each tool and add results
            for idx in sorted(tool_calls_accumulator.keys()):
                tc = tool_calls_accumulator[idx]
                name = tc["name"]
                args_json = tc["arguments"]

                _start_spinner(f"running {name}...")
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
                _stop_spinner()

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

                # Add tool result to messages
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_str[:50000],  # cap to avoid context overflow
                })

            # Loop back to get the model's response after tool execution

        console.print()
        return full_response
