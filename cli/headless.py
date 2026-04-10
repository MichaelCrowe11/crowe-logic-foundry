"""
Headless Crowe Logic Command runner for external hosts that want to
drive the agent over a JSON event stream.

Reads a single conversation turn (full message history) as JSON on
stdin and emits one JSON event per line to stdout for the assistant's
response. Each line is a complete JSON object terminated by ``\n``;
hosts read until they see ``{"type":"done"}`` or ``{"type":"error"}``.

This is the contract that lets a non-terminal host reuse the same agent
loop the CLI runs without parsing Rich/ANSI output. The renderer is just
swapped: ``BaseOpenAIProvider.stream_response`` accepts an optional
renderer instance, and ``JsonStreamRenderer`` here conforms to the same
interface ``StreamRenderer`` exposes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time


_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, _PACKAGE_ROOT)


def emit(event_type: str, **fields) -> None:
    """Write one newline-delimited JSON event to stdout."""
    sys.stdout.write(json.dumps({"type": event_type, **fields}) + "\n")
    sys.stdout.flush()


def emit_error(message: str, kind: str = "runtime") -> None:
    emit("error", message=message, kind=kind)


class JsonStreamRenderer:
    """Renderer that emits JSON events instead of terminal UI."""

    def __init__(self) -> None:
        self._text_chunks: list[str] = []
        self._token_count = 0
        self._reasoning_token_count = 0
        self._t_start = 0.0
        self._t_first_token = 0.0
        self._t_end = 0.0

    def start(self) -> None:
        self._t_start = time.monotonic()
        emit("ready")

    def set_spinner(self, label: str) -> None:
        self.end_segment()
        emit("spinner", label=label)

    def stop_spinner(self) -> None:
        emit("spinner", label=None)

    def feed(self, token: str) -> None:
        if self._t_first_token == 0.0:
            self._t_first_token = time.monotonic()
        self._text_chunks.append(token)
        self._token_count += 1
        emit("token", delta=token)

    def feed_reasoning(self, token: str) -> None:
        self._reasoning_token_count += 1
        emit("reasoning", delta=token)

    def end_segment(self) -> None:
        self._text_chunks = []
        emit("segment_end")

    def finish(self, session_state=None) -> None:
        self._t_end = time.monotonic()
        ttft_ms = (
            int((self._t_first_token - self._t_start) * 1000)
            if self._t_first_token > 0
            else 0
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


def json_render_tool_card(console, name, args_json, status, result, duration_ms):
    """Emit tool activity on the JSON event stream."""
    emit(
        "tool",
        name=name,
        args=args_json,
        status=status,
        result=(result or "")[:5000],
        duration_ms=duration_ms,
    )


class _NoopOrchestrator:
    """Fallback execution logger when Crowe Synapse is unavailable."""

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


def _build_provider(model_id: str):
    """Construct a provider for a requested model in MODEL_CHAIN."""
    from config.agent_config import MODEL_CHAIN, SYSTEM_INSTRUCTIONS

    chain = list(MODEL_CHAIN)
    if not chain:
        raise RuntimeError("MODEL_CHAIN is empty in config/agent_config.py")

    if model_id == "auto":
        cfg = chain[0]
    else:
        cfg = next((m for m in chain if m["name"] == model_id), None)
        if cfg is None:
            raise RuntimeError(
                f"Unknown model '{model_id}'. Use 'auto' or one of: "
                + ", ".join(m["name"] for m in chain[:10])
                + ("..." if len(chain) > 10 else "")
            )

    provider_kind = cfg.get("provider", "openrouter")
    label = cfg.get("label", "CroweLM")
    name = cfg["name"]

    if provider_kind == "openrouter":
        from config.agent_config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL
        from providers.openrouter import OpenRouterProvider

        if not OPENROUTER_API_KEY:
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        return OpenRouterProvider(
            api_key=OPENROUTER_API_KEY,
            base_url=OPENROUTER_BASE_URL,
            model=name,
            system_instructions=SYSTEM_INSTRUCTIONS,
            label=label,
        )

    if provider_kind == "ollama":
        from config.agent_config import OLLAMA_BASE_URL
        from providers.ollama import OllamaProvider

        return OllamaProvider(
            model=name,
            system_instructions=SYSTEM_INSTRUCTIONS,
            base_url=OLLAMA_BASE_URL,
            label=label,
        )

    if provider_kind == "nvidia":
        from config.agent_config import NVIDIA_API_KEY, NVIDIA_NIM_ENDPOINT
        from providers.nvidia import NvidiaProvider

        if not NVIDIA_NIM_ENDPOINT or not NVIDIA_API_KEY:
            raise RuntimeError(
                "NVIDIA_NIM_ENDPOINT and NVIDIA_API_KEY must both be set "
                "to use the nvidia provider"
            )
        return NvidiaProvider(
            model=name,
            system_instructions=SYSTEM_INSTRUCTIONS,
            endpoint=NVIDIA_NIM_ENDPOINT,
            api_key=NVIDIA_API_KEY,
            label=label,
        )

    if provider_kind == "azure_openai":
        from providers.azure_openai import AzureOpenAIProvider

        endpoint_var = cfg.get("endpoint_env", "AZURE_CORE_ENDPOINT")
        api_key_var = cfg.get("api_key_env", "AZURE_CORE_API_KEY")
        endpoint = os.environ.get(endpoint_var, "")
        api_key = os.environ.get(api_key_var, "")
        if not endpoint or not api_key:
            raise RuntimeError(
                f"Azure model '{label}' is missing credentials "
                f"({endpoint_var} / {api_key_var})"
            )
        return AzureOpenAIProvider(
            model=name,
            system_instructions=SYSTEM_INSTRUCTIONS,
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
            system_instructions=SYSTEM_INSTRUCTIONS,
            endpoint=endpoint,
            api_key=api_key,
            label=label,
        )

    raise RuntimeError(f"Headless mode does not support provider kind '{provider_kind}'")


def _read_input(args) -> dict:
    if args.input:
        with open(args.input, "r", encoding="utf-8") as handle:
            return json.load(handle)
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
    parser.add_argument(
        "--model",
        default="auto",
        help="Model id from MODEL_CHAIN (default: first entry)",
    )
    args = parser.parse_args()

    try:
        payload = _read_input(args)
    except Exception as exc:
        emit_error(f"Failed to read input: {exc}", kind="input")
        return 2

    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        emit_error("Input must include a non-empty 'messages' array", kind="input")
        return 2
    if not isinstance(messages[-1], dict) or messages[-1].get("role") != "user":
        emit_error("Input messages must end with a user turn", kind="input")
        return 2

    model_id = payload.get("model") or args.model

    try:
        provider = _build_provider(model_id)
    except Exception as exc:
        emit_error(str(exc), kind="config")
        return 3

    for msg in messages[:-1]:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role in ("user", "assistant"):
            provider.messages.append({"role": role, "content": msg.get("content") or ""})
    provider.add_user_message(messages[-1].get("content") or "")

    renderer = JsonStreamRenderer()
    session_state = {
        "favicon": "",
        "tool_count": 0,
        "session_id": payload.get("session") or "headless",
    }

    try:
        provider.stream_response(
            console=None,
            render_tool_card=json_render_tool_card,
            session_state=session_state,
            _get_orchestrator=_get_orchestrator,
            renderer=renderer,
        )
    except KeyboardInterrupt:
        emit_error("Interrupted", kind="cancelled")
        return 130
    except Exception as exc:
        emit_error(f"{type(exc).__name__}: {exc}", kind="provider")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
