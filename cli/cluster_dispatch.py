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


# ── Orchestrator tool-use loop ───────────────────────────────────────────


@dataclass
class OrchestratedRun:
    """Outcome of an orchestrator-driven session.

    `final_answer` is what the orchestrator's LLM returned when it stopped
    calling tools. `tool_calls` lists every dispatch the orchestrator made
    along the way (each is a DispatchResult, recorded in `session.history`
    as well). `iterations` is how many round-trips with the orchestrator's
    own model it took.
    """

    final_answer: str
    tool_calls: list[DispatchResult] = field(default_factory=list)
    iterations: int = 0
    orchestrator_tokens: int = 0
    error: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.error is None and bool(self.final_answer)


def _orchestrator_tools(registry: AgentRegistry, cluster_name: str) -> list[dict]:
    """Build the OpenAI-format tool schemas the orchestrator can call.

    Specialists are enumerated in the description so the model knows the
    valid set without us having to bake the list into its prompt.
    """
    specialists = sorted(
        a.name for a in registry.agents_in_cluster(cluster_name)
        if a.alias_of is None  # don't expose aliases as targets
    )
    spec_list = ", ".join(specialists)
    return [
        {
            "type": "function",
            "function": {
                "name": "list_specialists",
                "description": (
                    "Return the list of specialist names available in the "
                    f"{cluster_name} cluster. Useful when deciding who to "
                    "dispatch to."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "dispatch_to_specialist",
                "description": (
                    "Send a brief to one specialist and receive their "
                    "structured response. Specialists available: " + spec_list +
                    ". Brief should be specific and contain all context the "
                    "specialist needs to act."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "specialist": {
                            "type": "string",
                            "description": "Specialist name, e.g. music-compose, music-master.",
                            "enum": specialists,
                        },
                        "brief": {
                            "type": "string",
                            "description": "The task brief for the specialist.",
                        },
                    },
                    "required": ["specialist", "brief"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_critic_gate",
                "description": (
                    "Run the cluster's critic against a diff or proposed "
                    "change. Returns 'PASS' if the change is clean, or a "
                    "BLOCK/WARN/NOTE finding if it violates a cluster rule. "
                    "Use before recommending a change land."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "diff": {
                            "type": "string",
                            "description": "The diff or change content to review.",
                        },
                    },
                    "required": ["diff"],
                },
            },
        },
    ]


def _execute_tool_call(
    name: str,
    arguments: dict,
    *,
    registry: AgentRegistry,
    cluster_name: str,
    session: ClusterSession,
    timeout_s: float,
) -> tuple[str, Optional[DispatchResult]]:
    """Execute one tool call from the orchestrator and return (result_string, dispatch_result).

    The result_string is what gets sent back to the orchestrator as the
    tool's output. The dispatch_result is the structured artifact (None
    for tools that don't dispatch, like list_specialists).
    """
    if name == "list_specialists":
        specialists = sorted(
            a.name for a in registry.agents_in_cluster(cluster_name)
            if a.alias_of is None
        )
        return json.dumps({"specialists": specialists}), None

    if name == "dispatch_to_specialist":
        specialist = arguments.get("specialist", "")
        brief = arguments.get("brief", "")
        if not specialist or not brief:
            return json.dumps({"error": "specialist and brief are required"}), None
        result = dispatch_to_specialist(
            specialist, brief, registry=registry,
            session=session, timeout_s=timeout_s,
        )
        return json.dumps({
            "specialist": result.specialist,
            "succeeded": result.succeeded,
            "answer": result.answer,
            "tokens": result.total_tokens,
            "latency_s": round(result.latency_s, 2),
            "error": result.error,
        }), result

    if name == "run_critic_gate":
        diff = arguments.get("diff", "")
        if not diff:
            return json.dumps({"error": "diff is required"}), None
        passed, result = run_critic_gate(
            diff, registry=registry, session=session, timeout_s=timeout_s,
        )
        return json.dumps({
            "passed": passed,
            "verdict": "PASS" if passed else "BLOCK/WARN/NOTE",
            "critic_answer": result.answer,
            "tokens": result.total_tokens,
            "latency_s": round(result.latency_s, 2),
        }), result

    return json.dumps({"error": f"unknown tool: {name}"}), None


def dispatch_via_orchestrator(
    brief: str,
    *,
    registry: AgentRegistry,
    session: ClusterSession,
    cluster_name: str = "crowelm-music",
    orchestrator_name: str = "music-orchestrator",
    timeout_s: float = 240.0,
    max_iterations: int = 8,
    max_completion_tokens: int = 4096,
) -> OrchestratedRun:
    """Run a brief through the orchestrator with tool calling enabled.

    The orchestrator (premium tier, e.g. Talon flagship) receives the
    brief plus a tool catalog (list_specialists, dispatch_to_specialist,
    run_critic_gate). It decides what to do, calls tools as needed, and
    eventually returns a plain assistant message which becomes the final
    answer.

    Iteration cap prevents runaway loops. Completion-token cap per turn
    prevents one over-eager response from blowing the budget.
    """
    target = registry.resolve_alias(orchestrator_name)
    if target is None:
        return OrchestratedRun(final_answer="", error=f"orchestrator not found: {orchestrator_name!r}")

    model_cfg = resolve_model_config(target.model)
    if model_cfg is None:
        return OrchestratedRun(
            final_answer="",
            error=f"model not registered: {target.model!r}",
        )
    try:
        base_url, api_key = _resolve_endpoint(model_cfg)
    except RuntimeError as exc:
        return OrchestratedRun(final_answer="", error=str(exc))

    tools = _orchestrator_tools(registry, cluster_name)
    messages: list[dict] = [
        {"role": "system", "content": target.prompt_override or ""},
        {"role": "user", "content": brief},
    ]

    tool_call_results: list[DispatchResult] = []
    orchestrator_tokens = 0

    for iteration in range(1, max_iterations + 1):
        payload = {
            "model": model_cfg.get("backend_name", target.model),
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "stream": False,
            "max_tokens": max_completion_tokens,
        }

        try:
            data = _post_chat(base_url, api_key, payload, timeout_s=timeout_s)
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
            return OrchestratedRun(
                final_answer="",
                tool_calls=tool_call_results,
                iterations=iteration - 1,
                orchestrator_tokens=orchestrator_tokens,
                error=f"transport: {exc.__class__.__name__}: {exc}",
            )

        usage = data.get("usage") or {}
        orchestrator_tokens += int(usage.get("total_tokens", 0) or 0)

        try:
            choice = data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError):
            return OrchestratedRun(
                final_answer="", tool_calls=tool_call_results,
                iterations=iteration, orchestrator_tokens=orchestrator_tokens,
                error="malformed orchestrator response",
            )

        tool_calls = message.get("tool_calls") or []

        # Append the assistant's turn to history so subsequent calls have
        # the model's reasoning visible.
        assistant_turn = {
            "role": "assistant",
            "content": message.get("content") or "",
        }
        if tool_calls:
            assistant_turn["tool_calls"] = tool_calls
        messages.append(assistant_turn)

        if not tool_calls:
            # Model produced a final answer.
            return OrchestratedRun(
                final_answer=message.get("content") or "",
                tool_calls=tool_call_results,
                iterations=iteration,
                orchestrator_tokens=orchestrator_tokens,
            )

        for call in tool_calls:
            fn = call.get("function") or {}
            name = fn.get("name", "")
            try:
                arguments = json.loads(fn.get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                arguments = {}
            result_text, dispatch_result = _execute_tool_call(
                name, arguments,
                registry=registry, cluster_name=cluster_name,
                session=session, timeout_s=timeout_s,
            )
            if dispatch_result is not None:
                tool_call_results.append(dispatch_result)

            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id") or "",
                "content": result_text,
            })

    return OrchestratedRun(
        final_answer="",
        tool_calls=tool_call_results,
        iterations=max_iterations,
        orchestrator_tokens=orchestrator_tokens,
        error=f"max_iterations ({max_iterations}) reached without final answer",
    )
