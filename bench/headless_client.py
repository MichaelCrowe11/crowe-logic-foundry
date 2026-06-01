"""Drive cli/headless.py as a subprocess and parse its JSON event stream."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass, field

# Substrings marking a *transient* throughput throttle (TPM/RPM) — these clear
# in seconds, so a bounded backoff usually recovers them. Distinct from hard
# failures (quota=0, unsupported provider) where retrying only wastes time.
_TRANSIENT_MARKERS = (
    "429",
    "rate limit",
    "ratelimitreached",
    "too many requests",
)


def _is_transient(error: str | None) -> bool:
    if not error:
        return False
    low = error.lower()
    return any(marker in low for marker in _TRANSIENT_MARKERS)


@dataclass
class RunResult:
    answer: str = ""
    reasoning: str = ""
    tokens: int = 0
    reasoning_tokens: int = 0
    elapsed_ms: int = 0
    ttft_ms: int = 0
    error: str | None = None
    raw_events: list[dict] = field(default_factory=list)


def parse_event_stream(text: str) -> RunResult:
    r = RunResult()
    answer, reasoning = [], []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        r.raw_events.append(ev)
        etype = ev.get("type")
        if etype == "token":
            answer.append(ev.get("delta", ""))
        elif etype == "reasoning":
            reasoning.append(ev.get("delta", ""))
        elif etype == "error":
            r.error = ev.get("message", "unknown error")
        elif etype == "done":
            r.tokens = ev.get("tokens", 0)
            r.reasoning_tokens = ev.get("reasoning_tokens", 0)
            r.elapsed_ms = ev.get("elapsed_ms", 0)
            r.ttft_ms = ev.get("ttft_ms", 0)
    r.answer = "".join(answer)
    r.reasoning = "".join(reasoning)
    return r


def _run_once(
    prompt: str, model: str, *, tools: bool = True, timeout: int = 300
) -> RunResult:
    """One headless subprocess invocation; return a parsed RunResult."""
    args = [
        sys.executable,
        "-m",
        "cli.headless",
        "--model",
        model,
        "--tools" if tools else "--no-tools",
    ]
    payload = json.dumps(
        {"messages": [{"role": "user", "content": prompt}], "model": model}
    )
    try:
        proc = subprocess.run(
            args, input=payload, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return RunResult(error=f"timeout after {timeout}s")
    result = parse_event_stream(proc.stdout)
    if result.error is None and proc.returncode != 0:
        result.error = (proc.stderr or "nonzero exit").strip()[:500]
    return result


def run_headless(
    prompt: str,
    model: str,
    *,
    tools: bool = True,
    timeout: int = 300,
    retries: int = 2,
    backoff_base: float = 2.0,
) -> RunResult:
    """Invoke crowe-logic headless for one question; return a parsed RunResult.

    Retries up to ``retries`` times on *transient* throughput throttles (429 /
    rate-limit), with exponential backoff. Hard errors (unsupported provider,
    quota=0, timeout) fail fast — retrying them only wastes time and tokens.
    """
    result = _run_once(prompt, model, tools=tools, timeout=timeout)
    attempt = 0
    while _is_transient(result.error) and attempt < retries:
        time.sleep(backoff_base * (2**attempt))
        attempt += 1
        result = _run_once(prompt, model, tools=tools, timeout=timeout)
    return result
