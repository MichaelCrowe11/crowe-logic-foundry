"""
cli/cluster_dispatch.py

Specialist dispatch surface for the CroweLM-Music sub-cluster (and any
future sub-cluster). Lives in cli/ because it imports from both config/
(for model resolution) and crowe_synapse_engine/ (for the registry);
the architecture contract (tests/test_architecture_boundaries.py) only
allows that combination from cli/.

The orchestrator yaml describes routing in prose; this module is the
runtime that turns "dispatch to music-master" into an actual chat
completion against the right backend.

Responsibilities:
  * Resolve a specialist name to its AgentConfig + model backend
  * Build the chat-completion payload (system = agent.prompt_override,
    user = orchestrator's brief)
  * Pick the backend (Ollama for ollama-provider models, OpenAI-compat
    HTTP for NVIDIA NIM and other openai_compat providers)
  * Return a structured DispatchResult, including latency and token usage
  * Track per-session dispatch history for orchestrator state

This module deliberately does NOT do its own routing decisions. The
orchestrator (LLM) decides who to dispatch to and why; this module just
executes.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

from concurrent.futures import ThreadPoolExecutor, as_completed

from crowe_synapse_engine.agent_registry import AgentConfig, AgentRegistry
from config.agent_config import resolve_model_config


# ── Result types ─────────────────────────────────────────────────────────


@dataclass
class DispatchResult:
    """Outcome of one specialist invocation."""

    specialist: str
    answer: str = ""
    latency_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model_used: str = ""
    provider: str = ""
    error: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.error is None and bool(self.answer)


@dataclass
class ClusterSession:
    """Lightweight state holder for an orchestrator session.

    Tracks every dispatch the orchestrator has made within one logical
    operator request. Lets the orchestrator answer questions like "what
    have I asked already" without re-dispatching, and lets the operator
    inspect what the cluster did.
    """

    session_id: str
    cluster: str
    history: list[DispatchResult] = field(default_factory=list)

    def record(self, result: DispatchResult) -> None:
        self.history.append(result)

    def total_latency_s(self) -> float:
        return sum(r.latency_s for r in self.history)

    def total_tokens(self) -> int:
        return sum(r.total_tokens for r in self.history)

    def successful_dispatches(self) -> list[DispatchResult]:
        return [r for r in self.history if r.succeeded]

    def failed_dispatches(self) -> list[DispatchResult]:
        return [r for r in self.history if not r.succeeded]


# ── Backend selection ────────────────────────────────────────────────────


def _ollama_base_url() -> str:
    return os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")


_PROVIDER_DEFAULT_ENV = {
    "nvidia":        ("NVIDIA_NIM_ENDPOINT",  "NVIDIA_API_KEY"),
    "openai_compat": ("NVIDIA_NIM_ENDPOINT",  "NVIDIA_API_KEY"),
    "anthropic":     ("ANTHROPIC_BASE_URL",   "ANTHROPIC_API_KEY"),
    "azure":         ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY"),
}


def _resolve_endpoint(model_cfg: dict) -> tuple[str, Optional[str]]:
    """Return (base_url, api_key) for a resolved model config.

    Raises if the model needs an env var that is not set.
    """
    provider = model_cfg.get("provider", "")
    if provider == "ollama":
        return _ollama_base_url(), None

    endpoint_env = model_cfg.get("endpoint_env", "")
    api_key_env = model_cfg.get("api_key_env", "")
    if not endpoint_env:
        endpoint_env, default_api = _PROVIDER_DEFAULT_ENV.get(provider, ("", ""))
        if not api_key_env:
            api_key_env = default_api

    base_url = os.environ.get(endpoint_env, "") if endpoint_env else ""
    api_key = os.environ.get(api_key_env, "") if api_key_env else None
    if not base_url:
        raise RuntimeError(
            f"specialist requires {endpoint_env or 'an endpoint env var'}; not set"
        )
    return base_url, api_key


# ── HTTP call (synchronous, OpenAI-compat) ───────────────────────────────


def _post_chat(
    base_url: str,
    api_key: Optional[str],
    payload: dict,
    timeout_s: float,
) -> dict:
    """POST an OpenAI-compatible chat-completion request and return the
    decoded JSON response. Raises urllib errors on transport failure."""
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ── Public dispatch surface ──────────────────────────────────────────────


def dispatch_to_specialist(
    specialist: str,
    brief: str,
    *,
    registry: AgentRegistry,
    session: Optional[ClusterSession] = None,
    timeout_s: float = 120.0,
    temperature: float = 0.1,
    extra_user_context: str = "",
) -> DispatchResult:
    """Send a brief to one specialist and return its structured response.

    The orchestrator calls this function (typically via tool-use) every
    time it wants a specialist's output. The brief is the user-message
    payload; the agent's prompt_override becomes the system message.

    The dispatch is recorded in the session if one is supplied.
    """
    target = registry.resolve_alias(specialist)
    if target is None:
        result = DispatchResult(
            specialist=specialist,
            error=f"specialist not found in registry: {specialist!r}",
        )
        if session is not None:
            session.record(result)
        return result

    model_cfg = resolve_model_config(target.model)
    if model_cfg is None:
        result = DispatchResult(
            specialist=target.name,
            error=f"model not registered in MODEL_CHAIN: {target.model!r}",
        )
        if session is not None:
            session.record(result)
        return result

    try:
        base_url, api_key = _resolve_endpoint(model_cfg)
    except RuntimeError as exc:
        result = DispatchResult(
            specialist=target.name,
            error=str(exc),
            model_used=target.model,
            provider=model_cfg.get("provider", ""),
        )
        if session is not None:
            session.record(result)
        return result

    user_content = brief
    if extra_user_context:
        user_content = f"{brief}\n\n--- additional context ---\n{extra_user_context}"

    payload = {
        "model": model_cfg.get("backend_name", target.model),
        "messages": [
            {"role": "system", "content": target.prompt_override or ""},
            {"role": "user", "content": user_content},
        ],
        "stream": False,
        "temperature": temperature,
    }

    start = time.monotonic()
    try:
        data = _post_chat(base_url, api_key, payload, timeout_s=timeout_s)
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
        result = DispatchResult(
            specialist=target.name,
            latency_s=time.monotonic() - start,
            error=f"transport: {exc.__class__.__name__}: {exc}",
            model_used=target.model,
            provider=model_cfg.get("provider", ""),
        )
        if session is not None:
            session.record(result)
        return result
    latency = time.monotonic() - start

    try:
        answer = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        answer = ""
    usage = data.get("usage") or {}

    result = DispatchResult(
        specialist=target.name,
        answer=answer,
        latency_s=latency,
        prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
        completion_tokens=int(usage.get("completion_tokens", 0) or 0),
        total_tokens=int(usage.get("total_tokens", 0) or 0),
        model_used=target.model,
        provider=model_cfg.get("provider", ""),
        error=None if answer else "empty completion",
    )
    if session is not None:
        session.record(result)
    return result


def dispatch_in_parallel(
    specialists: list[str],
    brief: str,
    *,
    registry: AgentRegistry,
    session: Optional[ClusterSession] = None,
    timeout_s: float = 120.0,
    temperature: float = 0.1,
    max_workers: int = 4,
) -> list[DispatchResult]:
    """Dispatch the same brief to multiple specialists concurrently.

    Useful when independent specialists (music-web + music-native) can
    work in parallel. Results are returned in the order specialists were
    passed in, regardless of completion order.
    """
    results: dict[str, DispatchResult] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(specialists)))) as ex:
        futures = {
            ex.submit(
                dispatch_to_specialist,
                name,
                brief,
                registry=registry,
                session=None,  # we record in order at the end, not per-future
                timeout_s=timeout_s,
                temperature=temperature,
            ): name
            for name in specialists
        }
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                results[name] = fut.result()
            except Exception as exc:  # pragma: no cover - defensive
                results[name] = DispatchResult(
                    specialist=name, error=f"executor: {exc!r}"
                )

    ordered = [results[name] for name in specialists]
    if session is not None:
        for r in ordered:
            session.record(r)
    return ordered


# ── Critic gate (the standard cluster check) ─────────────────────────────


def run_critic_gate(
    diff: str,
    *,
    registry: AgentRegistry,
    session: Optional[ClusterSession] = None,
    critic_name: str = "music-critic",
    timeout_s: float = 120.0,
) -> tuple[bool, DispatchResult]:
    """Run the cluster's critic against a diff.

    Returns (passed, result). passed is True iff the critic returned
    exactly the string "PASS" (whitespace stripped). All other outputs
    are treated as findings (BLOCK / WARN / NOTE) and gate the change.
    """
    brief = f"Review the following diff for landing on main:\n\n{diff}"
    result = dispatch_to_specialist(
        critic_name, brief, registry=registry, session=session, timeout_s=timeout_s
    )
    passed = result.succeeded and result.answer.strip() == "PASS"
    return passed, result
