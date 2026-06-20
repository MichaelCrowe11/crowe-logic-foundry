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

from config.telemetry import telemetry


def _coerce_tool_args(func, args: dict) -> dict:
    """Coerce tool-call arguments to the types declared in function annotations.

    Local/smaller LLMs frequently emit all values as JSON strings even when the
    schema specifies integer, number, or boolean.  For example limit="10"
    instead of limit=10.  This causes TypeErrors deep inside tool code (e.g.
    ``min("10", 50)``).  By inspecting the function signature and casting here,
    we fix the mismatch once for every tool instead of patching each one.
    """
    sig = inspect.signature(func)
    coerced = {}
    for key, value in args.items():
        param = sig.parameters.get(key)
        if param is not None and isinstance(value, str):
            ann = param.annotation
            try:
                if ann is int:
                    value = int(value)
                elif ann is float:
                    value = float(value)
                elif ann is bool:
                    value = value.lower() not in ("false", "0", "no", "")
            except (ValueError, AttributeError):
                pass  # leave as-is; the function will raise its own error
        coerced[key] = value
    return coerced


# Module-level memoization for tool schemas. Keyed by id() of the
# user_functions set, which tools/__init__.py builds once at import and
# never mutates — so identity is stable for the life of the process and
# all four providers share one cached schema list, even when the user
# switches models mid-session.
_TOOL_CACHE: dict[int, tuple[list[dict], dict]] = {}
SOFT_TOOL_BUDGET_ROUND: int = 6
_RESEARCH_LOOP_TOOLS = frozenset(
    {
        "web_search",
        "browse_url",
        "browser_navigate",
        "browser_click",
        "browser_type_text",
        "browser_snapshot",
        "browser_screenshot",
    }
)


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

        tools.append(
            {
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
            }
        )
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
    if cached is None:
        schemas = build_tool_schemas(user_functions)
        name_map = {f.__name__: f for f in user_functions}
        _TOOL_CACHE[key] = (schemas, name_map)
        cached = _TOOL_CACHE[key]

    schemas, name_map = cached
    # Apply the active autonomy level so the model only SEES tools it may use
    # (restricted levels hide write/shell/etc from the schema itself). The full
    # build stays memoized; filtering a per-call view is cheap. Fail-open if the
    # autonomy module is unavailable (e.g. providers used outside the CLI).
    try:
        from cli.autonomy import get_active_level, filter_schemas, filter_tools

        level = get_active_level()
    except Exception:
        return schemas, name_map
    if level == "full":
        return schemas, name_map
    return filter_schemas(schemas, level), filter_tools(name_map, level)


def should_send_tool_budget_warning(
    rounds_used: int, tool_names: list[str], warning_sent: bool
) -> bool:
    """Return True when the model appears stuck in repetitive web research."""
    if warning_sent or rounds_used < SOFT_TOOL_BUDGET_ROUND:
        return False
    normalized = {(name or "").strip() for name in tool_names if (name or "").strip()}
    return bool(normalized) and normalized.issubset(_RESEARCH_LOOP_TOOLS)


def build_tool_budget_warning(rounds_used: int, max_rounds: int) -> str:
    """Prompt the model to stop broad browsing before it burns the full budget."""
    return (
        f"You have already used {rounds_used} of {max_rounds} tool rounds. "
        "Stop broad searching or repeated browsing. "
        "If you can answer with the information already gathered, answer now. "
        "If one fact is still missing, make at most one more tightly targeted tool call. "
        "If more than one additional call would be needed, ask the user for the exact missing "
        "address, document, case number, or name instead of continuing to search."
    )


def build_forced_final_answer_prompt(max_rounds: int) -> str:
    """Prompt used when the hard tool budget is exhausted."""
    return (
        f"Tool budget exhausted after {max_rounds} tool rounds. "
        "Do not call any more tools. "
        "Using only the information already gathered in this conversation, provide the best final answer now. "
        "State any uncertainty clearly and list the exact missing facts or documents the user should provide next."
    )


class _InlineReasoningSplitter:
    """Split inline ``<think>…</think>`` blocks out of a streamed content channel.

    Nemotron (NVIDIA NIM), Qwen3 *thinking* variants, GPT-OSS, and several other
    open reasoning models emit chain-of-thought tokens inline in ``delta.content``
    wrapped in ``<think>…</think>`` rather than on a dedicated ``reasoning_content``
    field. Without separation those tokens leak into the rendered final answer.
    This splitter buffers just enough bytes to classify partial tags across chunk
    boundaries and yields ``(kind, text)`` pairs where ``kind`` is ``"content"``
    or ``"reasoning"``.
    """

    OPEN = "<think>"
    CLOSE = "</think>"

    def __init__(self) -> None:
        self.in_think = False
        self._buf = ""

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        self._buf += chunk
        out: list[tuple[str, str]] = []
        while self._buf:
            if self.in_think:
                i = self._buf.find(self.CLOSE)
                if i == -1:
                    # Keep the tail that might be the start of CLOSE.
                    safe = max(0, len(self._buf) - (len(self.CLOSE) - 1))
                    if safe:
                        out.append(("reasoning", self._buf[:safe]))
                        self._buf = self._buf[safe:]
                    break
                if i:
                    out.append(("reasoning", self._buf[:i]))
                self._buf = self._buf[i + len(self.CLOSE) :]
                self.in_think = False
            else:
                i = self._buf.find(self.OPEN)
                if i == -1:
                    safe = max(0, len(self._buf) - (len(self.OPEN) - 1))
                    if safe:
                        out.append(("content", self._buf[:safe]))
                        self._buf = self._buf[safe:]
                    break
                if i:
                    out.append(("content", self._buf[:i]))
                self._buf = self._buf[i + len(self.OPEN) :]
                self.in_think = True
        return out

    def flush(self) -> list[tuple[str, str]]:
        if not self._buf:
            return []
        kind = "reasoning" if self.in_think else "content"
        text, self._buf = self._buf, ""
        return [(kind, text)]


def _dispatch_content(renderer, splitter: _InlineReasoningSplitter, text: str) -> None:
    """Route a streamed content chunk to ``feed`` / ``feed_reasoning`` via the splitter."""
    for kind, piece in splitter.feed(text):
        if not piece:
            continue
        if kind == "reasoning":
            renderer.feed_reasoning(piece)
        else:
            renderer.feed(piece)


def _flush_content(renderer, splitter: _InlineReasoningSplitter) -> None:
    for kind, piece in splitter.flush():
        if not piece:
            continue
        if kind == "reasoning":
            renderer.feed_reasoning(piece)
        else:
            renderer.feed(piece)


# Phrases that indicate the model announced a next action without executing it.
# When an assistant turn ends with one of these (and no tool_calls), the runtime
# auto-continues instead of handing control back to the user.
_ACTION_INTENT_PATTERNS = (
    "let me ",
    "let's ",
    "i'll ",
    "i will ",
    "i am going to ",
    "i'm going to ",
    "i'm about to ",
    "i am about to ",
    "now i'll",
    "now i will",
    "now let",
    "next, i",
    "next i'll",
    "next i will",
    "first, i",
    "first i'll",
    "starting with",
    "i'll start",
    "let me start",
    "proceeding to",
    "moving on to",
    "i'll now",
    "going to ",
)


def looks_like_stalled_announcement(text: str) -> bool:
    """Return True when the assistant message announces intent but took no action.

    The model emitted a natural-language statement like "Let me take a snapshot..."
    and then stopped without calling the tool. We want to silently re-invoke so the
    user doesn't have to type "continue".
    """
    if not text:
        return False
    stripped = text.strip()
    if not stripped:
        return False
    # Very short "…" / ":" tails almost always signal a pending action.
    tail = stripped[-3:]
    if tail.endswith(":") or tail.endswith("..."):
        return True
    # Scan the last ~400 chars for an action-intent phrase.
    window = stripped[-400:].lower()
    return any(p in window for p in _ACTION_INTENT_PATTERNS)


# Synthetic nudge appended to message history when auto-continuing. Kept short
# so it doesn't bias the model's next output, and framed as a system reminder
# rather than a user turn so the transcript reads cleanly. Avoids echoing
# litigation triggers like "do not narrate intent" — the model otherwise
# spends the next turn re-reasoning over that exact phrase.
AUTO_CONTINUE_NUDGE = (
    "(auto-continue) Issue the tool call now, or produce the final answer."
)


def _canonical_tool_call_key(name: str, args_json: str) -> str:
    """Stable hash for a tool call, used for in-session deduplication.

    Args are normalized through json so semantically-equal payloads with
    different whitespace or key order collapse to the same key.
    """
    try:
        parsed = json.loads(args_json) if args_json else {}
    except (json.JSONDecodeError, TypeError):
        parsed = args_json
    canonical = json.dumps(parsed, sort_keys=True, default=str, separators=(",", ":"))
    return f"{name}::{canonical}"


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
    MAX_ROUNDS: int = 20

    def __init__(self, model: str, system_instructions: str, label: str = "CroweLM"):
        self.model = model
        self.label = label
        self.system_instructions = system_instructions
        self.messages = [{"role": "system", "content": system_instructions}]
        self._tool_call_seq = 0
        # Maps canonical tool-call key -> cached result string. Populated as
        # tools execute; consulted on every new call so the same action is
        # never run twice in one session unless the user explicitly retries.
        self._recent_tool_results: dict[str, str] = {}
        # Optional MODEL_CHAIN entry. When set, stream_response merges
        # tier_runtime_params (temperature, top_p, max_tokens) into the
        # chat-completions request so each tier runs at its tuned profile.
        # Provider factories assign this after construction.
        self.model_cfg: dict | None = None

    def add_user_message(self, content: str):
        # Each new user turn resets the in-session tool-call dedupe cache:
        # repeating a tool call within a single user turn is almost always
        # a model loop, but the user may legitimately ask for the same
        # action again on a subsequent turn.
        self._recent_tool_results.clear()
        self.messages.append({"role": "user", "content": content})

    def set_system_instructions(self, system_instructions: str) -> None:
        """Update the active system prompt for cached provider instances."""
        self.system_instructions = system_instructions
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = system_instructions

    def _translate_provider_error(self, exc: Exception) -> Exception | None:
        """Convert backend-specific errors into actionable messages.

        Default is a no-op; the original exception is re-raised. Subclasses
        override this to recognize provider-specific failure shapes (e.g.
        a retired NVCF function on NVIDIA NIM) and return a clearer
        ``Exception`` to raise in its place. Returning ``None`` keeps the
        original behaviour.
        """
        return None

    def _next_tool_call_id(self) -> str:
        """Generate backend-safe tool_call_ids for local message history.

        Some OpenAI-compatible backends emit blank or backend-specific ids during
        streamed tool calls. We control the assistant/tool message history we send
        back on the next round, so we synthesize compact alphanumeric ids and keep
        them consistent within the local transcript.
        """
        self._tool_call_seq += 1
        return f"tc{self._tool_call_seq:07d}"

    def _force_final_response(self, renderer, session_state, full_response: str) -> str:
        """Run one no-tools pass so the turn can finish gracefully at the budget cap."""
        self.messages.append(
            {
                "role": "user",
                "content": build_forced_final_answer_prompt(self.MAX_ROUNDS),
            }
        )

        try:
            renderer.set_spinner("finalizing answer...")
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=self.messages,
                stream=True,
            )

            splitter = _InlineReasoningSplitter()
            for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if not delta:
                    continue

                if self.SUPPORTS_REASONING:
                    reasoning = getattr(delta, "reasoning", None) or getattr(
                        delta, "reasoning_content", None
                    )
                    if reasoning:
                        renderer.feed_reasoning(reasoning)

                if delta.content:
                    _dispatch_content(renderer, splitter, delta.content)

                finish = chunk.choices[0].finish_reason if chunk.choices else None
                if finish in ("stop", "tool_calls"):
                    _flush_content(renderer, splitter)
                    break
        except KeyboardInterrupt:
            if hasattr(renderer, "abort"):
                renderer.abort(session_state=session_state)
            else:
                renderer.stop_spinner()
            raise
        except Exception as exc:
            if hasattr(renderer, "abort"):
                renderer.abort(session_state=session_state)
            else:
                renderer.stop_spinner()
            translated = self._translate_provider_error(exc)
            if translated is not None:
                raise translated from exc
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

    def stream_response(
        self,
        console,
        render_tool_card,
        session_state,
        _get_orchestrator,
        renderer=None,
        tools_enabled=True,
    ):
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

        ``tools_enabled`` (default True) gates tool/MCP availability. When
        False, no tool schemas are loaded or offered to the model, yielding
        a bare answer — used by grounded-vs-bare benchmarks.
        """
        tool_schemas, tool_map = load_tools() if tools_enabled else ([], {})

        if renderer is None:
            from cli.renderer import StreamRenderer

            favicon = session_state.get("favicon", "")
            renderer = StreamRenderer(console, self.label, favicon=favicon)

        full_response = ""
        budget_warning_sent = False
        auto_continues_used = 0
        # One auto-continue is enough to recover from a stalled announcement.
        # Two creates a redundant-tool-call loop when the model keeps re-issuing
        # the same call after each nudge.
        MAX_AUTO_CONTINUES = 1
        _session_start = time.monotonic()

        for _round in range(self.MAX_ROUNDS):
            tool_calls_accumulator: dict[int, dict] = {}
            _round_start = time.monotonic()

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

                # Apply tier-specific runtime params (temperature, max_tokens, etc.)
                # when the provider factory has attached a model_cfg. Per-call
                # values already in create_kwargs win, so providers can still
                # override on a per-request basis if they need to.
                if self.model_cfg is not None:
                    from config.agent_config import tier_runtime_params

                    for k, v in tier_runtime_params(self.model_cfg).items():
                        create_kwargs.setdefault(k, v)

                stream = self.client.chat.completions.create(**create_kwargs)

                splitter = _InlineReasoningSplitter()
                for chunk in stream:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if not delta:
                        continue

                    # Thinking models surface chain-of-thought on a separate
                    # field. Backends that never emit it can flip
                    # SUPPORTS_REASONING off as a micro-optimization.
                    if self.SUPPORTS_REASONING:
                        reasoning = getattr(delta, "reasoning", None) or getattr(
                            delta, "reasoning_content", None
                        )
                        if reasoning:
                            renderer.feed_reasoning(reasoning)

                    if delta.content:
                        _dispatch_content(renderer, splitter, delta.content)

                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_accumulator:
                                tool_calls_accumulator[idx] = {
                                    "id": self._next_tool_call_id(),
                                    "raw_id": tc.id or "",
                                    "name": tc.function.name
                                    if tc.function and tc.function.name
                                    else "",
                                    "arguments": "",
                                }
                            if tc.id:
                                tool_calls_accumulator[idx]["raw_id"] = tc.id
                            if tc.function:
                                if tc.function.name:
                                    tool_calls_accumulator[idx]["name"] = (
                                        tc.function.name
                                    )
                                if tc.function.arguments:
                                    tool_calls_accumulator[idx]["arguments"] += (
                                        tc.function.arguments
                                    )

                    finish = chunk.choices[0].finish_reason if chunk.choices else None
                    if finish in ("stop", "tool_calls"):
                        _flush_content(renderer, splitter)
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

            # Capture THIS round's content only — current_segment_text reads
            # the live _text_chunks BEFORE end_segment() clears them. Using a
            # cross-segment accumulator here would corrupt message history
            # (the model would echo prior content back, causing the
            # duplication this whole refactor was built to fix).
            response_text = renderer.current_segment_text
            if response_text.strip():
                full_response += response_text

            if not tool_calls_accumulator:
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
                    renderer.stop_spinner()
                    continue

                _total_ms = int((time.monotonic() - _session_start) * 1000)
                telemetry.log_model_call(
                    model=self.model,
                    provider=self.label,
                    tokens_in=0,  # exact counts require usage response parsing
                    tokens_out=0,
                    duration_ms=_total_ms,
                )
                telemetry.log_event(
                    "stream_complete",
                    {
                        "model": self.model,
                        "rounds": _round + 1,
                        "tool_calls": session_state.get("tool_count", 0),
                        "auto_continues": auto_continues_used,
                        "duration_ms": _total_ms,
                    },
                )
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
            round_tool_names: list[str] = []
            for idx in ordered_indices:
                tc = tool_calls_accumulator[idx]
                name = (tc["name"] or "").strip() or "invalid_tool_call"
                round_tool_names.append(name)
                assistant_msg["tool_calls"].append(
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": name, "arguments": tc["arguments"]},
                    }
                )
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
                cache_hit = False
                cache_key: str | None = None
                if raw_name:
                    cache_key = _canonical_tool_call_key(raw_name, args_json or "")
                    cached = self._recent_tool_results.get(cache_key)
                    if cached is not None:
                        # Same call already executed in this session. Return
                        # the cached result with a note so the model knows
                        # not to retry, and surface it visibly to the user.
                        result_str = json.dumps(
                            {
                                "cached": True,
                                "note": "Same tool+args already executed this session; returning prior result. Do not re-issue this call.",
                                "previous_result": cached[:8000],
                            }
                        )
                        cache_hit = True

                if cache_hit:
                    pass  # result_str already set
                elif not raw_name:
                    result_str = json.dumps(
                        {
                            "error": "Model emitted a tool call without a function name.",
                            "raw_arguments": args_json[:2000],
                        }
                    )
                    failed = True
                elif func:
                    try:
                        # Resilient parse, then type coercion. Order matters:
                        # parse_tool_arguments may unwrap content_b64 into
                        # content, and _coerce_tool_args then normalizes
                        # numeric/bool types smaller models emit as strings.
                        from cli.tool_args import parse_tool_arguments

                        args, _recovered = (
                            parse_tool_arguments(args_json)
                            if args_json
                            else ({}, False)
                        )
                        args = _coerce_tool_args(func, args)
                        result = func(**args)
                        result_str = str(result) if result is not None else ""
                    except Exception as e:
                        result_str = json.dumps({"error": f"{type(e).__name__}: {e}"})
                        failed = True
                else:
                    result_str = json.dumps({"error": f"Unknown tool: {name}"})
                    failed = True

                if cache_key and not failed and not cache_hit:
                    self._recent_tool_results[cache_key] = result_str

                duration_ms = int((time.monotonic() - _tool_start) * 1000)
                renderer.stop_spinner()

                telemetry.log_tool_call(
                    name=name,
                    args=args_json,
                    duration_ms=duration_ms,
                    success=not failed,
                    error=result_str[:500] if failed else None,
                )

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
                        "tool_call_id": tc["id"],
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

        # Headless callers pass console=None — only the Rich CLI needs
        # this trailing newline to flush its prompt back into place.
        if console is not None:
            console.print()
        return full_response
