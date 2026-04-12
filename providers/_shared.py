"""
Shared base for OpenAI-compatible providers.

Azure OpenAI, NVIDIA NIM, Ollama, OpenRouter — and any other backend that
exposes a `/v1/chat/completions` surface — all run the exact same per-round
streaming + tool-calling loop. Only the constructor (URL normalization +
auth) differs across providers. Hoisting the loop into a single base class
collapses ~600 lines of duplication and means new providers only need a
~20-line subclass with their own URL/auth wiring.
"""

import json
import time
import inspect


# Module-level memoization for tool schemas. Keyed by id() of the
# user_functions set, which tools/__init__.py builds once at import and
# never mutates — so identity is stable for the life of the process and
# all four providers share one cached schema list, even when the user
# switches models mid-session.
_TOOL_CACHE: dict[int, tuple[list[dict], dict]] = {}


def build_tool_schemas(user_functions) -> list[dict]:
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


def load_tools() -> tuple[list[dict], dict]:
    """Return (tool_schemas, name_to_function_map), memoized per process.

    The memo is keyed by id(user_functions). tools/__init__.py builds the
    set once at import time so identity is stable; switching providers
    mid-session reuses the cached schemas instead of rebuilding them, and
    skips paying the inspect.signature + docstring-parsing cost again.
    """
    from tools import user_functions
    key = id(user_functions)
    cached = _TOOL_CACHE.get(key)
    if cached is not None:
        return cached
    schemas = build_tool_schemas(user_functions)
    name_map = {f.__name__: f for f in user_functions}
    _TOOL_CACHE[key] = (schemas, name_map)
    return _TOOL_CACHE[key]


class BaseOpenAIProvider:
    """Base class for any provider that speaks OpenAI Chat Completions.

    Subclasses are expected to:
      * call ``super().__init__(model, system_instructions, label)``
      * assign ``self.client`` to a configured ``openai.OpenAI`` instance
      * (optionally) override ``SUPPORTS_REASONING = False`` if the
        backend never surfaces ``delta.reasoning`` / ``delta.reasoning_content``
        — leaving the default True is harmless on backends that don't
        emit those fields.
    """

    SUPPORTS_REASONING: bool = True
    MAX_ROUNDS: int = 10

    def __init__(self, model: str, system_instructions: str, label: str = "CroweLM"):
        self.model = model
        self.label = label
        self.system_instructions = system_instructions
        self.messages = [{"role": "system", "content": system_instructions}]
        self._tool_call_seq = 0

    def add_user_message(self, content: str):
        self.messages.append({"role": "user", "content": content})

    def _next_tool_call_id(self) -> str:
        """Generate backend-safe tool_call_ids for local message history.

        Some OpenAI-compatible backends emit blank or backend-specific ids during
        streamed tool calls. We control the assistant/tool message history we send
        back on the next round, so we synthesize compact alphanumeric ids and keep
        them consistent within the local transcript.
        """
        self._tool_call_seq += 1
        return f"tc{self._tool_call_seq:07d}"

    def stream_response(self, console, render_tool_card, session_state, _get_orchestrator,
                        renderer=None):
        """Stream a response with the tool-calling loop.

        Drop-in compatible across all OpenAI-compatible providers.

        ``renderer`` is optional; when omitted (the default for the
        terminal CLI) a Rich-based ``StreamRenderer`` is constructed
        from ``console`` and the favicon in ``session_state``. Headless
        callers (the VS Code extension's stdio bridge, future HTTP
        servers, tests) pass their own renderer that conforms to the
        same interface (``start``, ``set_spinner``, ``stop_spinner``,
        ``feed``, ``feed_reasoning``, ``end_segment``, ``finish``,
        ``current_segment_text``).
        """
        tool_schemas, tool_map = load_tools()

        if renderer is None:
            from cli.renderer import StreamRenderer
            favicon = session_state.get("favicon", "")
            renderer = StreamRenderer(console, self.label, favicon=favicon)

        full_response = ""

        for _round in range(self.MAX_ROUNDS):
            tool_calls_accumulator: dict[int, dict] = {}

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

                    # Thinking models surface chain-of-thought on a separate
                    # field. Backends that never emit it can flip
                    # SUPPORTS_REASONING off as a micro-optimization.
                    if self.SUPPORTS_REASONING:
                        reasoning = (
                            getattr(delta, "reasoning", None)
                            or getattr(delta, "reasoning_content", None)
                        )
                        if reasoning:
                            renderer.feed_reasoning(reasoning)

                    if delta.content:
                        renderer.feed(delta.content)

                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_accumulator:
                                tool_calls_accumulator[idx] = {
                                    "id": self._next_tool_call_id(),
                                    "raw_id": tc.id or "",
                                    "name": tc.function.name if tc.function and tc.function.name else "",
                                    "arguments": "",
                                }
                            if tc.id:
                                tool_calls_accumulator[idx]["raw_id"] = tc.id
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
            # the live _text_chunks BEFORE end_segment() clears them. Using a
            # cross-segment accumulator here would corrupt message history
            # (the model would echo prior content back, causing the
            # duplication this whole refactor was built to fix).
            response_text = renderer.current_segment_text
            if response_text.strip():
                full_response += response_text

            if not tool_calls_accumulator:
                renderer.finish(session_state=session_state)
                self.messages.append({"role": "assistant", "content": response_text})
                break

            # Finalize the segment before tool execution so the next round
            # starts with empty buffers.
            renderer.end_segment()
            renderer.stop_spinner()

            ordered_indices = sorted(tool_calls_accumulator.keys())

            assistant_msg = {
                "role": "assistant",
                "content": response_text or None,
                "tool_calls": [],
            }
            for idx in ordered_indices:
                tc = tool_calls_accumulator[idx]
                name = (tc["name"] or "").strip() or "invalid_tool_call"
                assistant_msg["tool_calls"].append({
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": name, "arguments": tc["arguments"]},
                })
            self.messages.append(assistant_msg)

            for idx in ordered_indices:
                tc = tool_calls_accumulator[idx]
                raw_name = (tc["name"] or "").strip()
                name = raw_name or "invalid_tool_call"
                args_json = tc["arguments"]

                renderer.set_spinner(f"running {name}...")
                _tool_start = time.monotonic()

                func = tool_map.get(name)
                failed = False
                if not raw_name:
                    result_str = json.dumps({
                        "error": "Model emitted a tool call without a function name.",
                        "raw_arguments": args_json[:2000],
                    })
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
                    console, name, args_json,
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

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_str[:50000],
                })

        # Headless callers pass console=None — only the Rich CLI needs
        # this trailing newline to flush its prompt back into place.
        if console is not None:
            console.print()
        return full_response
