"""
Headless Crowe Logic Command runner for external hosts that want to
drive the agent over a JSON event stream.

Reads a single conversation turn (full message history) as JSON on
stdin and emits one JSON event per line to stdout for the assistant's
response. Each line is a complete JSON object terminated by ``\n``;
hosts read until they see ``{"type":"done"}`` or ``{"type":"error"}``.

This is the contract that lets a non-terminal host (the VS Code chat
participant, an HTTP gateway, a test runner) reuse the same agent loop
the CLI runs without parsing Rich/ANSI output. The renderer is just
swapped: ``BaseOpenAIProvider.stream_response`` accepts an optional
renderer instance, and ``JsonStreamRenderer`` here conforms to the same
interface ``StreamRenderer`` exposes.

Input schema (one JSON object on stdin)::

    {
      "messages": [
        {"role": "user",      "content": "..."},
        {"role": "assistant", "content": "..."},
        ...
      ],
      "model":   "auto",            # optional, defaults to MODEL_CHAIN[0]
      "session": "vscode-abc123"    # optional, opaque tag for telemetry
    }

Output events (line-delimited JSON on stdout)::

    {"type":"ready"}
    {"type":"reasoning","delta":"..."}
    {"type":"token","delta":"..."}
    {"type":"tool","name":"...","args":"...","status":"ok|fail","duration_ms":N,"result":"..."}
    {"type":"spinner","label":"..."}        # transient state hints
    {"type":"segment_end"}                   # boundary between rounds
    {"type":"done","tokens":N,"reasoning_tokens":N,"elapsed_ms":N,"ttft_ms":N}
    {"type":"error","message":"...","kind":"..."}
"""

from __future__ import annotations

import json
import os
import sys
import time
import argparse

from cli.session_runtime import (
    build_runtime_system_instructions,
    handle_local_control_command,
    update_session_runtime,
)


# Make sure the Foundry root is importable when this module is invoked as
# ``python -m cli.headless`` from anywhere (e.g., the VS Code extension
# spawning it from a different cwd).
_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, _PACKAGE_ROOT)


# ── Wire protocol ────────────────────────────────────────────────────────


def emit(event_type: str, **fields) -> None:
    """Write one JSON event to stdout, terminated by newline + flush.

    Newline-delimited JSON is the simplest framing that lets the host
    parse incrementally without a length prefix. We flush per event so
    streaming actually streams instead of buffering until the process
    exits.
    """
    sys.stdout.write(json.dumps({"type": event_type, **fields}) + "\n")
    sys.stdout.flush()


def emit_error(message: str, kind: str = "runtime") -> None:
    emit("error", message=message, kind=kind)


# ── Renderer ─────────────────────────────────────────────────────────────


class JsonStreamRenderer:
    """Renderer that emits JSON events instead of drawing to a terminal.

    Conforms to the interface ``BaseOpenAIProvider.stream_response``
    expects from ``StreamRenderer``: ``start``, ``set_spinner``,
    ``stop_spinner``, ``feed``, ``feed_reasoning``, ``end_segment``,
    ``finish``, plus the ``current_segment_text`` property. The host
    consumes the resulting event stream and renders it natively (the VS
    Code extension uses the Chat API; an HTTP gateway could SSE it).

    Segment semantics mirror the Rich renderer exactly so the provider
    loop's calls into ``current_segment_text`` and ``end_segment``
    behave identically. ``_text_chunks`` is the per-segment buffer;
    ``end_segment`` clears it after the provider has read its contents.
    """

    def __init__(self, *, session_id: str = "", model_label: str = "") -> None:
        self._text_chunks: list[str] = []
        self._full_text_chunks: list[str] = []
        self._full_reasoning_chunks: list[str] = []
        self._token_count = 0
        self._reasoning_token_count = 0
        self._t_start = 0.0
        self._t_first_token = 0.0
        self._t_end = 0.0
        self._session_id = session_id
        self._model_label = model_label

    def start(self) -> None:
        self._t_start = time.monotonic()
        emit("ready")

    def set_spinner(self, label: str) -> None:
        # Mirror StreamRenderer.set_spinner: finalize the segment, then
        # advertise the new spinner state. The host can render this as a
        # status line, a progress message, or ignore it entirely.
        self.end_segment()
        emit("spinner", label=label)

    def stop_spinner(self) -> None:
        emit("spinner", label=None)

    def feed(self, token: str) -> None:
        if self._t_first_token == 0.0:
            self._t_first_token = time.monotonic()
        self._text_chunks.append(token)
        self._full_text_chunks.append(token)
        self._token_count += 1
        emit("token", delta=token)

    def feed_reasoning(self, token: str) -> None:
        self._full_reasoning_chunks.append(token)
        self._reasoning_token_count += 1
        emit("reasoning", delta=token)

    def end_segment(self) -> None:
        # The provider reads current_segment_text BEFORE calling this,
        # exactly as it does with the Rich renderer. Clearing here lets
        # the next round start with an empty buffer.
        self._text_chunks = []
        emit("segment_end")

    def finish(self, session_state=None) -> None:
        self._t_end = time.monotonic()
        self._persist_transcript()
        ttft_ms = (
            int((self._t_first_token - self._t_start) * 1000)
            if self._t_first_token > 0 else 0
        )
        emit(
            "done",
            tokens=self._token_count,
            reasoning_tokens=self._reasoning_token_count,
            elapsed_ms=int((self._t_end - self._t_start) * 1000),
            ttft_ms=ttft_ms,
        )

    @property
    def current_segment_text(self) -> str:
        return "".join(self._text_chunks)

    def abort(self, session_state=None) -> None:
        self._persist_transcript()

    def _persist_transcript(self) -> None:
        if not self._session_id:
            return
        update_session_runtime(
            self._session_id,
            last_answer_text="".join(self._full_text_chunks).strip(),
            last_reasoning_text="".join(self._full_reasoning_chunks).strip(),
            last_model=self._model_label,
        )


def json_render_tool_card(console, name, args_json, status, result, duration_ms):
    """Tool-card renderer for the JSON event stream.

    Same call signature as ``cli.branding.render_tool_card`` so it can
    be passed straight into ``BaseOpenAIProvider.stream_response`` as the
    ``render_tool_card`` argument. Tool results are truncated to keep
    the wire payload bounded; the host can request the full result via
    a follow-up tool history view if it cares.
    """
    emit(
        "tool",
        name=name,
        args=args_json,
        status=status,
        result=(result or "")[:5000],
        duration_ms=duration_ms,
    )


# ── Orchestrator stub ────────────────────────────────────────────────────


class _NoopOrchestrator:
    """Drop-in for crowe_synapse_engine.Orchestrator when it isn't available.

    The CLI uses the orchestrator to log tool executions to a SQLite
    history DB. Headless mode tries to load the real one for parity, but
    falls back to a no-op so the agent never crashes if Crowe Synapse
    isn't installed in the host environment (e.g., a slimmed-down
    container image).
    """

    def record_execution(self, **_kwargs):
        return None


_orchestrator_singleton = None


def _get_orchestrator():
    global _orchestrator_singleton
    if _orchestrator_singleton is not None:
        return _orchestrator_singleton
    try:
        from crowe_synapse_engine import Orchestrator
        _orchestrator_singleton = Orchestrator(
            db_path=os.path.expanduser("~/.crowe-logic/memory.db"),
            agents_dir=os.path.join(_PACKAGE_ROOT, "agents"),
            templates_dir=os.path.join(_PACKAGE_ROOT, "crowe_synapse_engine", "templates"),
        )
    except Exception:
        _orchestrator_singleton = _NoopOrchestrator()
    return _orchestrator_singleton


# ── Provider selection ──────────────────────────────────────────────────


def _build_provider(model_id: str, *, session_id: str = ""):
    """Construct a provider for the requested model.

    ``model_id`` is either ``"auto"`` (use the first entry in
    MODEL_CHAIN) or the ``name`` field of a MODEL_CHAIN entry. We
    deliberately reuse MODEL_CHAIN so the headless mode and the
    interactive CLI agree on which models exist and how to authenticate
    against each provider type. Drift here would be very confusing.
    """
    from config.agent_config import MODEL_CHAIN, resolve_model_config

    chain = list(MODEL_CHAIN)
    if not chain:
        raise RuntimeError("MODEL_CHAIN is empty in config/agent_config.py")

    if model_id == "auto":
        cfg = chain[0]
    else:
        cfg = resolve_model_config(model_id)
        if cfg is None:
            raise RuntimeError(
                f"Unknown model '{model_id}'. Use 'auto' or one of: "
                + ", ".join(m["name"] for m in chain[:10])
                + ("..." if len(chain) > 10 else "")
            )

    provider_kind = cfg.get("provider", "openrouter")
    label = cfg.get("label", "CroweLM")
    name = cfg["name"]
    system_instructions = build_runtime_system_instructions(cfg, session_id=session_id)

    if provider_kind == "openrouter":
        from config.agent_config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL
        from providers.openrouter import OpenRouterProvider
        if not OPENROUTER_API_KEY:
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        return OpenRouterProvider(
            api_key=OPENROUTER_API_KEY,
            base_url=OPENROUTER_BASE_URL,
            model=name,
            system_instructions=system_instructions,
            label=label,
        )

    if provider_kind == "ollama":
        from config.agent_config import OLLAMA_BASE_URL
        from providers.ollama import OllamaProvider
        return OllamaProvider(
            model=name,
            system_instructions=system_instructions,
            base_url=OLLAMA_BASE_URL,
            label=label,
        )

    if provider_kind == "nvidia":
        from config.agent_config import NVIDIA_NIM_ENDPOINT, NVIDIA_API_KEY
        from providers.nvidia import NvidiaProvider
        if not NVIDIA_NIM_ENDPOINT or not NVIDIA_API_KEY:
            raise RuntimeError(
                "NVIDIA_NIM_ENDPOINT and NVIDIA_API_KEY must both be set "
                "to use the nvidia provider"
            )
        return NvidiaProvider(
            model=name,
            system_instructions=system_instructions,
            endpoint=NVIDIA_NIM_ENDPOINT,
            api_key=NVIDIA_API_KEY,
            label=label,
        )

    if provider_kind == "azure_openai":
        from providers.azure_openai import AzureOpenAIProvider, AzureResponsesProvider
        endpoint_var = cfg.get("endpoint_env", "AZURE_CORE_ENDPOINT")
        api_key_var = cfg.get("api_key_env", "AZURE_CORE_API_KEY")
        endpoint = os.environ.get(endpoint_var, "")
        api_key = os.environ.get(api_key_var, "")
        if not endpoint or not api_key:
            raise RuntimeError(
                f"Azure model '{label}' is missing credentials "
                f"({endpoint_var} / {api_key_var})"
            )
        provider_cls = AzureResponsesProvider if cfg.get("surface") == "responses" else AzureOpenAIProvider
        return provider_cls(
            model=name,
            system_instructions=system_instructions,
            endpoint=endpoint,
            api_key=api_key,
            label=label,
        )

    if provider_kind == "anthropic":
        from providers.anthropic import AnthropicProvider
        endpoint_var = cfg.get("endpoint_env", "AZURE_ANTHROPIC_ENDPOINT")
        api_key_var = cfg.get("api_key_env", "AZURE_ANTHROPIC_API_KEY")
        endpoint = os.environ.get(endpoint_var, "")
        api_key = os.environ.get(api_key_var, "")
        if not endpoint or not api_key:
            raise RuntimeError(
                f"Anthropic model '{label}' is missing credentials "
                f"({endpoint_var} / {api_key_var})"
            )
        return AnthropicProvider(
            model=name,
            system_instructions=system_instructions,
            endpoint=endpoint,
            api_key=api_key,
            label=label,
        )

    raise RuntimeError(f"Headless mode does not support provider kind '{provider_kind}'")


# ── Main ────────────────────────────────────────────────────────────────


def _read_input(args) -> dict:
    if args.input:
        with open(args.input, "r", encoding="utf-8") as f:
            return json.load(f)
    raw = sys.stdin.read()
    if not raw.strip():
        raise RuntimeError("No input on stdin (expected a JSON object)")
    return json.loads(raw)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="crowe-logic-command",
        description="Run one Crowe Logic Command turn and emit JSON events.",
    )
    parser.add_argument("--input", help="Read JSON input from this file instead of stdin")
    parser.add_argument("--model", default="auto",
                        help="Model id from MODEL_CHAIN (default: first entry)")
    args = parser.parse_args()

    try:
        payload = _read_input(args)
    except Exception as e:
        emit_error(f"Failed to read input: {e}", kind="input")
        return 2

    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        emit_error("Input must include a non-empty 'messages' array", kind="input")
        return 2
    if not isinstance(messages[-1], dict) or messages[-1].get("role") != "user":
        emit_error("Input messages must end with a user turn", kind="input")
        return 2

    model_id = payload.get("model") or args.model
    session_id = payload.get("session") or "headless"

    local_response = handle_local_control_command(messages[-1].get("content") or "", session_id=session_id)
    if local_response is not None:
        emit("ready")
        emit("token", delta=local_response)
        emit("done", tokens=max(1, len(local_response.split())), reasoning_tokens=0, elapsed_ms=0, ttft_ms=0)
        return 0

    try:
        provider = _build_provider(model_id, session_id=session_id)
    except Exception as e:
        emit_error(str(e), kind="config")
        return 3

    # Replay the prior turns into the provider's internal state, then
    # call add_user_message for the trailing user turn. The provider
    # was just constructed so its messages list contains only the
    # system prompt; we append every prior turn so the model sees the
    # full conversation context VS Code already tracks for us. Tool
    # messages on input are ignored — the host doesn't track them,
    # the provider will recreate them as the agent runs tools.
    for msg in messages[:-1]:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role in ("user", "assistant"):
            provider.messages.append({"role": role, "content": msg.get("content") or ""})
    provider.add_user_message(messages[-1].get("content") or "")

    session_state = {
        "favicon": "",
        "tool_count": 0,
        "session_id": session_id,
        "active_model": getattr(provider, "label", ""),
    }
    renderer = JsonStreamRenderer(session_id=session_id, model_label=getattr(provider, "label", ""))

    try:
        provider.stream_response(
            console=None,
            render_tool_card=json_render_tool_card,
            session_state=session_state,
            _get_orchestrator=_get_orchestrator,
            renderer=renderer,
        )
    except KeyboardInterrupt:
        renderer.abort(session_state=session_state)
        emit_error("Interrupted", kind="cancelled")
        return 130
    except Exception as e:
        emit_error(f"{type(e).__name__}: {e}", kind="provider")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
