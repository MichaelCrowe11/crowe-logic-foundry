"""
ChatGPT Agents Studio (Responses API) tools.

Invoke an OpenAI-hosted agent (created in ChatGPT Agents Studio) from the
Foundry CLI. Uses the newer Responses API (``POST /v1/responses``) rather
than the older Assistants v2 threads/runs flow, so there is no thread
lifecycle to manage — each call is one-shot, with optional
``previous_response_id`` for multi-turn continuity when the caller wants it.

This module is deliberately channel-agnostic: ChatGPT Agents Studio exposes
agents on multiple channels (chatgpt.com UI, Slack, API). We call the API
channel directly. If the agent's API channel is not enabled in Studio, the
Responses API returns a 404 / permission error — we surface that verbatim.

Env contract:
    OPENAI_API_KEY        Required. API key on the same OpenAI account
                          that owns the agent.
    CHATGPT_AGENT_ID      Optional default agent id (agt_...). If set,
                          chatgpt_agent_invoke can be called without
                          specifying agent_id each turn.
    OPENAI_API_BASE       Optional base URL override (default
                          https://api.openai.com/v1). For Azure-hosted
                          OpenAI or proxies.
    CHATGPT_AGENT_TIMEOUT Optional HTTP timeout seconds (default 120).

Response schema:
    On success:
        {"output": str, "response_id": str, "agent_id": str, "usage": dict}
    On failure:
        {"error": str, "status": int, "detail": str|dict, "endpoint": str}
"""

from __future__ import annotations

import json
import os

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]


DEFAULT_API_BASE = "https://api.openai.com/v1"
DEFAULT_TIMEOUT_S = 120.0


def _api_base() -> str:
    return os.environ.get("OPENAI_API_BASE", DEFAULT_API_BASE).rstrip("/")


def _auth_headers() -> dict[str, str]:
    key = os.environ.get("OPENAI_API_KEY", "")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _timeout() -> float:
    try:
        return float(os.environ.get("CHATGPT_AGENT_TIMEOUT", DEFAULT_TIMEOUT_S))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_S


def _extract_output_text(data: dict) -> str:
    """Pull the primary text output from a Responses API payload.

    The Responses API is still shifting shapes across model families. We cover
    the three observed forms: (1) top-level ``output_text`` convenience field,
    (2) structured ``output[].content[].text`` for message outputs, and (3)
    a flat string in ``output``. Extra output items (tool calls, etc.) are
    ignored here; callers that need them should parse the raw payload.
    """
    flat = data.get("output_text")
    if isinstance(flat, str) and flat:
        return flat

    pieces: list[str] = []
    output = data.get("output", [])
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content", [])
            if isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") in ("output_text", "text"):
                        text = c.get("text", "")
                        if isinstance(text, str):
                            pieces.append(text)
            elif isinstance(content, str):
                pieces.append(content)
    elif isinstance(output, str):
        pieces.append(output)

    return "".join(pieces)


def chatgpt_agent_invoke(
    message: str,
    agent_id: str = "",
    previous_response_id: str = "",
    temperature: float = 0.7,
    max_output_tokens: int = 2048,
) -> str:
    """Send a message to a ChatGPT Agents Studio agent via the OpenAI Responses API.

    :param message: User message to send to the agent.
    :param agent_id: Agent id (agt_...). Falls back to CHATGPT_AGENT_ID env var when empty.
    :param previous_response_id: Optional id from a prior response to continue the conversation statefully.
    :param temperature: Sampling temperature 0.0-2.0 (default 0.7).
    :param max_output_tokens: Cap on output tokens (default 2048).
    :return: JSON string with the agent's reply and metadata.
    """
    if httpx is None:
        return json.dumps({
            "error": "httpx not installed in this environment",
            "status": -1,
        })

    if not isinstance(message, str) or not message.strip():
        return json.dumps({"error": "Empty message"})

    try:
        temperature = float(temperature)
        max_output_tokens = int(max_output_tokens)
    except (TypeError, ValueError):
        return json.dumps({
            "error": f"Invalid numeric args: temperature={temperature!r} max_output_tokens={max_output_tokens!r}",
        })

    if not os.environ.get("OPENAI_API_KEY", "").strip():
        return json.dumps({
            "error": "OPENAI_API_KEY not set in environment",
            "status": -1,
            "detail": "Add OPENAI_API_KEY=sk-... to the Foundry .env and restart the CLI.",
        })

    agent = (agent_id or "").strip() or os.environ.get("CHATGPT_AGENT_ID", "").strip()
    if not agent:
        return json.dumps({
            "error": "No agent_id provided and CHATGPT_AGENT_ID not set",
            "status": -1,
            "detail": "Pass agent_id (e.g. 'agt_...') or add CHATGPT_AGENT_ID to .env.",
        })

    body: dict = {
        "model": agent,
        "input": message,
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
    }
    prev = (previous_response_id or "").strip()
    if prev:
        body["previous_response_id"] = prev

    url = f"{_api_base()}/responses"
    try:
        with httpx.Client(timeout=_timeout()) as client:
            resp = client.post(url, headers=_auth_headers(), json=body)
    except httpx.TimeoutException:
        return json.dumps({"error": "Request timed out", "status": 408, "endpoint": url})
    except httpx.RequestError as exc:
        return json.dumps({"error": f"Network error: {exc}", "status": -1, "endpoint": url})

    if resp.status_code >= 400:
        try:
            detail: object = resp.json().get("error", resp.json())
        except Exception:
            detail = resp.text[:800]
        return json.dumps({
            "error": f"HTTP {resp.status_code}",
            "status": resp.status_code,
            "detail": detail,
            "endpoint": url,
        })

    try:
        data = resp.json()
    except Exception:
        return json.dumps({
            "error": "Non-JSON response from Responses API",
            "status": resp.status_code,
            "body": resp.text[:800],
            "endpoint": url,
        })

    return json.dumps({
        "output": _extract_output_text(data),
        "response_id": data.get("id", ""),
        "agent_id": agent,
        "usage": data.get("usage", {}),
    })


def chatgpt_agent_health(agent_id: str = "") -> str:
    """Verify the OpenAI Responses API is reachable and the agent will respond.

    Sends a 4-token ping to the target agent. Useful as a first-call smoke
    test before starting a real conversation, and for diagnosing whether a
    blank reply means "agent unreachable" or "agent chose not to answer".

    :param agent_id: Optional agent id. Falls back to CHATGPT_AGENT_ID env var.
    :return: JSON with {"reachable": bool, "agent_id": str, "detail": str, ...}.
    """
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        return json.dumps({
            "reachable": False,
            "detail": "OPENAI_API_KEY not set in environment",
        })

    agent = (agent_id or "").strip() or os.environ.get("CHATGPT_AGENT_ID", "").strip()
    if not agent:
        return json.dumps({
            "reachable": False,
            "detail": "No agent_id provided and CHATGPT_AGENT_ID not set",
        })

    raw = chatgpt_agent_invoke("ping", agent_id=agent, max_output_tokens=4)
    try:
        parsed = json.loads(raw)
    except Exception:
        return json.dumps({"reachable": False, "detail": raw[:200]})

    if "error" in parsed:
        return json.dumps({
            "reachable": False,
            "agent_id": agent,
            "detail": parsed.get("detail", parsed.get("error", "unknown error")),
            "status": parsed.get("status", -1),
        })

    return json.dumps({
        "reachable": True,
        "agent_id": agent,
        "response_id": parsed.get("response_id", ""),
    })
