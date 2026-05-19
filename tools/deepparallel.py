"""DeepParallel Local Reasoning Tool: 8-chain parallel analysis via Ollama.

Dispatches a prompt to the locally running DeepParallel model and returns
structured reasoning output. Usable from any CroweLM tier as a tool.

Production hardening (v0.2.8):
  - ``requests.Session`` with HTTP keepalive + connection pooling.
  - TTL-bounded dedup cache keyed by (model, prompt_hash, system_hash,
    temp, max_tokens, chains). Identical calls within 30s return the
    cached result instead of re-dispatching to Ollama. This kills the
    retry-storm where the model re-issues the same tool call after a
    transient 400.
  - Exponential backoff + jitter on transient faults only (ConnectionError,
    Timeout, HTTP 5xx). 4xx responses are returned as-is; they're model
    errors, not network errors, and retrying them is a waste of quota.
  - Single-flight: concurrent callers with identical args share one
    in-flight request instead of racing.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import threading
import time

import requests


_raw_ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_BASE = _raw_ollama_url.replace("/v1", "").rstrip("/")
DEEPPARALLEL_MODEL = os.environ.get("DEEPPARALLEL_MODEL", "Mcrowe1210/DeepParallel:latest")

_REQUEST_TIMEOUT_S = 120
_MAX_RETRIES = 3
_BACKOFF_BASE_S = 0.8
_BACKOFF_MAX_S = 8.0
_CACHE_TTL_S = 30.0

_session_lock = threading.Lock()
_session: requests.Session | None = None

_cache_lock = threading.Lock()
_response_cache: dict[str, tuple[float, str]] = {}
_inflight: dict[str, threading.Event] = {}


def _http_session() -> requests.Session:
    """Lazy-create a shared HTTPS session with a small connection pool.

    Ollama is almost always on loopback so a 10-connection pool is
    generous; the important win is avoiding a fresh TCP + TLS handshake
    per call when the model auto-retries a tool invocation.
    """
    global _session
    with _session_lock:
        if _session is None:
            s = requests.Session()
            adapter = requests.adapters.HTTPAdapter(
                pool_connections=4, pool_maxsize=10, max_retries=0,
            )
            s.mount("http://", adapter)
            s.mount("https://", adapter)
            _session = s
        return _session


def _cache_key(
    model: str, prompt: str, system: str,
    temperature: float, max_tokens: int, reasoning_chains: str,
) -> str:
    blob = json.dumps(
        [model, prompt, system, round(float(temperature), 3), int(max_tokens), reasoning_chains],
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.blake2b(blob, digest_size=16).hexdigest()


def _cache_get(key: str) -> str | None:
    now = time.monotonic()
    with _cache_lock:
        entry = _response_cache.get(key)
        if entry is None:
            return None
        stored_at, value = entry
        if now - stored_at > _CACHE_TTL_S:
            _response_cache.pop(key, None)
            return None
        return value


def _cache_put(key: str, value: str) -> None:
    now = time.monotonic()
    with _cache_lock:
        _response_cache[key] = (now, value)
        # Opportunistic eviction: keep cache small.
        if len(_response_cache) > 64:
            cutoff = now - _CACHE_TTL_S
            stale = [k for k, (ts, _) in _response_cache.items() if ts < cutoff]
            for k in stale:
                _response_cache.pop(k, None)


def deepparallel_query(
    prompt: str,
    system: str = "",
    temperature: float = 0.55,
    max_tokens: int = 4096,
    reasoning_chains: str = "all",
) -> str:
    """Run a query through DeepParallel's 8-chain parallel reasoning.

    :param prompt: The question or task to reason about.
    :param system: Optional system override (default: DeepParallel v2.2 persona).
    :param temperature: Sampling temperature 0.0-1.0 (default 0.55).
    :param max_tokens: Maximum tokens to generate (default 4096).
    :param reasoning_chains: Comma-separated chains to activate, or "all".
    :return: DeepParallel's reasoning output as text.
    :rtype: str
    """
    if not prompt.strip():
        return json.dumps({"error": "Empty prompt"})

    try:
        temperature = float(temperature)
        max_tokens = int(max_tokens)
    except (TypeError, ValueError):
        return json.dumps({"error": f"Invalid numeric args: temperature={temperature!r} max_tokens={max_tokens!r}"})

    key = _cache_key(
        DEEPPARALLEL_MODEL, prompt, system, temperature, max_tokens, reasoning_chains,
    )

    cached = _cache_get(key)
    if cached is not None:
        return cached

    # Single-flight: if another thread is already running the exact same
    # query, wait for it and reuse the cached result instead of racing.
    with _cache_lock:
        event = _inflight.get(key)
        if event is None:
            event = threading.Event()
            _inflight[key] = event
            is_owner = True
        else:
            is_owner = False

    if not is_owner:
        event.wait(timeout=_REQUEST_TIMEOUT_S + 5)
        cached = _cache_get(key)
        if cached is not None:
            return cached
        # Owner failed without caching. Fall through and try ourselves.

    try:
        messages = _build_messages(prompt, system, reasoning_chains)
        result = _dispatch_with_retry(messages, temperature, max_tokens)
        _cache_put(key, result)
        return result
    finally:
        with _cache_lock:
            ev = _inflight.pop(key, None)
        if ev is not None:
            ev.set()


def _build_messages(prompt: str, system: str, reasoning_chains: str) -> list[dict]:
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    else:
        chain_spec = ""
        if reasoning_chains.lower() != "all":
            chains = [c.strip().upper() for c in reasoning_chains.split(",")]
            chain_spec = f"\n\nFocus specifically on these chains: {', '.join(chains)}"
        messages.append({
            "role": "system",
            "content": (
                "You are DeepParallel, Crowe Logic's local 8-chain parallel reasoning engine. "
                "Apply thorough multi-chain analysis to the query."
                f"{chain_spec}"
            ),
        })
    messages.append({"role": "user", "content": prompt})
    return messages


def _dispatch_with_retry(messages: list[dict], temperature: float, max_tokens: int) -> str:
    """POST to Ollama with bounded exponential backoff.

    Retries ONLY on transient faults: ConnectionError, Timeout, and HTTP
    5xx. 4xx responses are structured model errors; retrying would
    reproduce them and waste quota.
    """
    session = _http_session()
    payload = {
        "model": DEEPPARALLEL_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    last_error: Exception | None = None

    for attempt in range(_MAX_RETRIES):
        try:
            resp = session.post(
                f"{OLLAMA_BASE}/v1/chat/completions",
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=_REQUEST_TIMEOUT_S,
            )
            if 500 <= resp.status_code < 600:
                last_error = requests.HTTPError(
                    f"{resp.status_code} {resp.reason}", response=resp,
                )
                _sleep_backoff(attempt)
                continue
            if 400 <= resp.status_code < 500:
                # Structured model/client error. Surface immediately.
                return json.dumps({
                    "error": f"HTTPError {resp.status_code} {resp.reason}",
                    "body": resp.text[:2000],
                    "model": DEEPPARALLEL_MODEL,
                })
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

        except requests.exceptions.ConnectionError:
            return json.dumps({
                "error": "Ollama not running. Start with: ollama serve",
                "model": DEEPPARALLEL_MODEL,
            })
        except (requests.exceptions.Timeout, requests.exceptions.ChunkedEncodingError) as exc:
            last_error = exc
            _sleep_backoff(attempt)
            continue
        except Exception as exc:
            # Unknown error. Don't retry blindly, surface it.
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"})

    return json.dumps({
        "error": f"DeepParallel retry budget exhausted after {_MAX_RETRIES} attempts",
        "last_error": f"{type(last_error).__name__}: {last_error}" if last_error else "unknown",
    })


def _sleep_backoff(attempt: int) -> None:
    delay = min(_BACKOFF_BASE_S * (2 ** attempt), _BACKOFF_MAX_S)
    delay += random.uniform(0, delay * 0.3)
    time.sleep(delay)


def deepparallel_status() -> str:
    """Check DeepParallel model availability and Ollama server status.

    :return: JSON status of DeepParallel model and Ollama connection.
    :rtype: str
    """
    try:
        resp = _http_session().get(f"{OLLAMA_BASE}/api/tags", timeout=5)
        resp.raise_for_status()
        models = resp.json().get("models", [])
        dp_models = [
            m for m in models
            if "deepparallel" in m.get("name", "").lower()
            or "deep-parallel" in m.get("name", "").lower()
        ]
        return json.dumps({
            "ollama_running": True,
            "deepparallel_available": len(dp_models) > 0,
            "models": [
                {
                    "name": m["name"],
                    "size_gb": round(m.get("size", 0) / 1e9, 2),
                    "modified": m.get("modified_at", ""),
                }
                for m in dp_models
            ],
        }, indent=2)
    except Exception as e:
        return json.dumps({
            "ollama_running": False,
            "error": str(e),
        })
