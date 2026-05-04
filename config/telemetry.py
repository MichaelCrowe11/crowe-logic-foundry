"""
Crowe Logic Foundry — Structured Telemetry

Lightweight JSON-lines logger for tool calls, model invocations, and system
events.  Writes to ~/.crowe-logic/runtime/telemetry.jsonl with automatic
rotation at 50 MB.

Usage:
    from config.telemetry import telemetry

    telemetry.log_tool_call("web_search", {"query": "test"}, 142, True)
    telemetry.log_model_call("gpt-5.4", "azure_openai", 500, 1200, 3400, 280)
    telemetry.log_event("session_start", {"model": "gpt-5.4"})
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

_MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB


class Telemetry:
    """Append-only JSON-lines telemetry sink."""

    def __init__(self, log_dir: str | None = None):
        self._dir = Path(log_dir or os.path.expanduser("~/.crowe-logic/runtime"))
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "telemetry.jsonl"
        self._enabled = True

    # ── Public API ───────────────────────────────────────────────────────

    def log_tool_call(
        self,
        name: str,
        args: dict | str | None = None,
        duration_ms: int = 0,
        success: bool = True,
        error: str | None = None,
    ) -> None:
        """Record a single tool invocation."""
        self._write({
            "type": "tool_call",
            "name": name,
            "args": _safe_args(args),
            "duration_ms": duration_ms,
            "success": success,
            "error": error,
        })

    def log_model_call(
        self,
        model: str,
        provider: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
        duration_ms: int = 0,
        ttft_ms: int = 0,
        fallback_from: str | None = None,
    ) -> None:
        """Record a model inference call."""
        self._write({
            "type": "model_call",
            "model": model,
            "provider": provider,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "duration_ms": duration_ms,
            "ttft_ms": ttft_ms,
            "fallback_from": fallback_from,
        })

    def log_event(self, category: str, data: dict | None = None) -> None:
        """Record a general system event."""
        self._write({
            "type": "event",
            "category": category,
            "data": data or {},
        })

    def disable(self) -> None:
        self._enabled = False

    def enable(self) -> None:
        self._enabled = True

    # ── Internal ─────────────────────────────────────────────────────────

    def _write(self, record: dict) -> None:
        if not self._enabled:
            return
        record["ts"] = datetime.now(timezone.utc).isoformat()
        record["epoch"] = time.time()
        self._rotate_if_needed()
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except OSError:
            pass  # never crash the agent over telemetry

    def _rotate_if_needed(self) -> None:
        try:
            if self._path.exists() and self._path.stat().st_size >= _MAX_FILE_BYTES:
                rotated = self._path.with_suffix(
                    f".{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.jsonl"
                )
                self._path.rename(rotated)
        except OSError:
            pass


def _safe_args(args: dict | str | None) -> dict | str | None:
    """Ensure args is JSON-serializable and not excessively large."""
    if args is None:
        return None
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (json.JSONDecodeError, TypeError):
            return args[:2000] if len(args) > 2000 else args
    if isinstance(args, dict):
        # Truncate large values to keep log lines manageable
        truncated = {}
        for k, v in args.items():
            if isinstance(v, str) and len(v) > 500:
                truncated[k] = v[:500] + "...(truncated)"
            else:
                truncated[k] = v
        return truncated
    return str(args)[:2000]


# Singleton — import and use directly
telemetry = Telemetry()
