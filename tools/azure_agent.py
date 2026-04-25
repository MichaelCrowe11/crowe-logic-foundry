"""
Azure AI Foundry Agents tools.

Replacement for the OpenAI ChatGPT Agents Studio integration, using the
``azure-ai-agents`` SDK against one of the Foundry project endpoints in
.env. Auth is via DefaultAzureCredential, which picks up the active
``az login`` session automatically — no API key required.

Unlike OpenAI's Responses API (stateless, optional ``previous_response_id``),
Azure AI Agents is thread-based: you create a thread, post messages to it,
run the agent to process them, and read the reply back. This module hides
that lifecycle behind three surface calls:

    azure_agent_list()                    → discover existing agents
    azure_agent_invoke(message, ...)      → send message, get reply
    azure_agent_create(name, model, ...)  → provision a new agent

Multi-turn continuity: callers carry ``thread_id`` across turns. Omit it
to start a fresh thread.

Env contract:
    AZURE_AGENT_PROJECT_ENDPOINT   Project endpoint to target. If unset,
                                   falls back to PROJECT_ENDPOINT.
    AZURE_AGENT_ID                 Default agent id (asst_...) used when
                                   invoke is called without an agent_id arg.
    PROJECT_ENDPOINT               Legacy primary project endpoint
                                   (Resource 7858 by default).

Response schema (mirrors the other Foundry tool modules):
    invoke on success:
        {"output": str, "agent_id": str, "thread_id": str,
         "run_id": str, "status": str, "usage": dict}
    invoke on failure:
        {"error": str, "status": str, "detail": str|dict}
    list/create return a plain JSON dump of the listing/creation result.
"""

from __future__ import annotations

import json
import os
from typing import Iterable

try:
    from azure.ai.agents import AgentsClient
    from azure.core.exceptions import AzureError
    from azure.identity import DefaultAzureCredential
except ImportError as _import_err:  # noqa: F841
    AgentsClient = None  # type: ignore[assignment]
    AzureError = Exception  # type: ignore[assignment]
    DefaultAzureCredential = None  # type: ignore[assignment]


_client_cache: dict[str, "AgentsClient"] = {}


def _resolve_project_endpoint(explicit: str = "") -> str:
    candidates = [
        explicit.strip(),
        os.environ.get("AZURE_AGENT_PROJECT_ENDPOINT", "").strip(),
        os.environ.get("PROJECT_ENDPOINT", "").strip(),
    ]
    for c in candidates:
        if c:
            return c
    raise RuntimeError(
        "No project endpoint: pass project_endpoint, or set "
        "AZURE_AGENT_PROJECT_ENDPOINT / PROJECT_ENDPOINT in .env."
    )


def _get_client(project_endpoint: str) -> "AgentsClient":
    if AgentsClient is None:
        raise RuntimeError(
            "azure-ai-agents not importable. Install with "
            "`pip install azure-ai-agents azure-identity`."
        )
    cached = _client_cache.get(project_endpoint)
    if cached is not None:
        return cached
    client = AgentsClient(
        endpoint=project_endpoint,
        credential=DefaultAzureCredential(),
    )
    _client_cache[project_endpoint] = client
    return client


def _extract_assistant_text(messages: Iterable) -> str:
    """Pull the most recent assistant message text from a thread listing."""
    for msg in messages:
        role = getattr(msg, "role", "")
        if role != "assistant":
            continue
        content = getattr(msg, "content", None) or []
        pieces: list[str] = []
        for item in content:
            # SDK returns typed objects; text items have .text.value.
            text_obj = getattr(item, "text", None)
            if text_obj is not None:
                val = getattr(text_obj, "value", None)
                if isinstance(val, str):
                    pieces.append(val)
                    continue
            # Dict-shaped fallbacks, in case the SDK shape shifts.
            if isinstance(item, dict):
                txt = item.get("text", {})
                if isinstance(txt, dict) and isinstance(txt.get("value"), str):
                    pieces.append(txt["value"])
                elif isinstance(txt, str):
                    pieces.append(txt)
        if pieces:
            return "".join(pieces)
    return ""


def azure_agent_list(project_endpoint: str = "") -> str:
    """List agents on an Azure AI Foundry project.

    :param project_endpoint: Project URL (services.ai.azure.com/api/projects/...).
        Falls back to AZURE_AGENT_PROJECT_ENDPOINT, then PROJECT_ENDPOINT env.
    :return: JSON string with ``{"agents": [{"id", "name", "model", "created_at"}, ...]}``.
    """
    try:
        endpoint = _resolve_project_endpoint(project_endpoint)
        client = _get_client(endpoint)
    except Exception as exc:
        return json.dumps({"error": str(exc), "status": "config"})

    try:
        agents = list(client.list_agents())
    except AzureError as exc:
        return json.dumps({"error": f"Azure API error: {exc}", "status": "azure"})
    except Exception as exc:
        return json.dumps({"error": f"{type(exc).__name__}: {exc}", "status": "unknown"})

    rows = []
    for a in agents:
        rows.append({
            "id": getattr(a, "id", ""),
            "name": getattr(a, "name", "") or "",
            "model": getattr(a, "model", "") or "",
            "created_at": str(getattr(a, "created_at", "") or ""),
        })
    return json.dumps({"agents": rows, "project_endpoint": endpoint, "count": len(rows)})


def azure_agent_invoke(
    message: str,
    agent_id: str = "",
    thread_id: str = "",
    project_endpoint: str = "",
) -> str:
    """Send a message to an Azure AI Foundry agent and return its reply.

    Thread-based: pass ``thread_id`` to continue an existing conversation;
    omit it to start a new one. The returned JSON always includes the
    ``thread_id`` used, so callers can carry it forward.

    :param message: User message text.
    :param agent_id: Agent id (asst_...). Falls back to AZURE_AGENT_ID env.
    :param thread_id: Optional existing thread id to continue.
    :param project_endpoint: Optional project URL override.
    :return: JSON with ``output``, ``agent_id``, ``thread_id``, ``run_id``, ``status``, ``usage``.
    """
    if not isinstance(message, str) or not message.strip():
        return json.dumps({"error": "Empty message"})

    try:
        endpoint = _resolve_project_endpoint(project_endpoint)
        client = _get_client(endpoint)
    except Exception as exc:
        return json.dumps({"error": str(exc), "status": "config"})

    agent = (agent_id or "").strip() or os.environ.get("AZURE_AGENT_ID", "").strip()
    if not agent:
        return json.dumps({
            "error": "No agent_id provided and AZURE_AGENT_ID not set",
            "status": "config",
            "detail": "Run azure_agent_list to see available agents, then set AZURE_AGENT_ID in .env or pass agent_id.",
        })

    try:
        thread = (thread_id or "").strip()
        if thread:
            # Continue existing thread: post new message, start a fresh run.
            client.messages.create(thread_id=thread, role="user", content=message)
            run = client.runs.create_and_process(thread_id=thread, agent_id=agent)
        else:
            # Fresh thread: one-shot create-thread-post-message-run.
            run = client.create_thread_and_process_run(
                agent_id=agent,
                thread={"messages": [{"role": "user", "content": message}]},
            )
            thread = getattr(run, "thread_id", "") or ""

        status = getattr(run, "status", "") or ""
        run_id = getattr(run, "id", "") or ""

        if status != "completed":
            last_error = getattr(run, "last_error", None)
            detail: object = getattr(last_error, "message", None) or str(last_error or "")
            return json.dumps({
                "error": f"Run did not complete (status={status})",
                "status": status,
                "detail": detail,
                "agent_id": agent,
                "thread_id": thread,
                "run_id": run_id,
            })

        # Most recent assistant message is the reply. Listing with order=desc
        # and bounded limit keeps this cheap even for long threads.
        try:
            msgs = client.messages.list(thread_id=thread, order="desc", limit=10)
        except TypeError:
            # Older SDK variants don't accept kwargs; fall back to default listing.
            msgs = client.messages.list(thread_id=thread)
        output = _extract_assistant_text(msgs)

        usage = getattr(run, "usage", None)
        usage_dict: dict = {}
        if usage is not None:
            for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
                val = getattr(usage, field, None)
                if val is not None:
                    usage_dict[field] = val

        return json.dumps({
            "output": output,
            "agent_id": agent,
            "thread_id": thread,
            "run_id": run_id,
            "status": status,
            "usage": usage_dict,
        })

    except AzureError as exc:
        return json.dumps({
            "error": f"Azure API error: {exc}",
            "status": "azure",
            "agent_id": agent,
            "thread_id": thread_id,
        })
    except Exception as exc:
        return json.dumps({
            "error": f"{type(exc).__name__}: {exc}",
            "status": "unknown",
            "agent_id": agent,
            "thread_id": thread_id,
        })


def azure_agent_create(
    name: str,
    instructions: str,
    model: str = "gpt-oss-120b",
    project_endpoint: str = "",
) -> str:
    """Provision a new agent on an Azure AI Foundry project.

    :param name: Human-readable agent name.
    :param instructions: System-prompt-equivalent instructions for the agent.
    :param model: Deployment name on the target resource (default gpt-oss-120b).
                  Must match a deployed model on the project's backing resource.
    :param project_endpoint: Optional project URL override.
    :return: JSON with the created agent's ``id``, ``name``, ``model``, and endpoint.
    """
    if not isinstance(name, str) or not name.strip():
        return json.dumps({"error": "Empty name"})
    if not isinstance(instructions, str) or not instructions.strip():
        return json.dumps({"error": "Empty instructions"})

    try:
        endpoint = _resolve_project_endpoint(project_endpoint)
        client = _get_client(endpoint)
    except Exception as exc:
        return json.dumps({"error": str(exc), "status": "config"})

    try:
        agent = client.create_agent(
            model=model,
            name=name,
            instructions=instructions,
        )
    except AzureError as exc:
        return json.dumps({"error": f"Azure API error: {exc}", "status": "azure"})
    except Exception as exc:
        return json.dumps({"error": f"{type(exc).__name__}: {exc}", "status": "unknown"})

    return json.dumps({
        "id": getattr(agent, "id", ""),
        "name": getattr(agent, "name", "") or name,
        "model": getattr(agent, "model", "") or model,
        "project_endpoint": endpoint,
    })
