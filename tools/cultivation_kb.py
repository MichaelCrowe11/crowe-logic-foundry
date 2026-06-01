"""Cultivation knowledge-base retrieval for the Crowe Logic agent.

Queries the proprietary CroweLM cultivation library (Lion's Mane SOP, The
Mushroom Grower, species data, contamination protocols) hosted by the live
``crowe-mycology`` MCP server at mycology.crowelogic.com. This is the grounding
source for cultivation answers — returns ranked corpus passages the model can
cite, rather than a pre-composed answer (that's what ``crowe_chat`` does).

The endpoint speaks streamable-HTTP MCP (JSON-RPC framed as Server-Sent
Events). A stateless ``tools/call`` POST is sufficient — no session handshake.
"""

import json
import os

import httpx

_DEFAULT_MCP_URL = "https://mycology.crowelogic.com/api/mcp/mcp"


def _parse_kb_hits(raw_text: str) -> list[dict]:
    """Extract the ``hits`` list from an SSE-framed MCP JSON-RPC response.

    The body looks like ``event: message\\ndata: {<json-rpc>}`` where the
    JSON-RPC ``result.content[0].text`` is itself a JSON string of ``{hits:[…]}``.
    Returns [] on any shape we don't recognise rather than raising.
    """
    for line in raw_text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        try:
            envelope = json.loads(line[len("data:") :].strip())
            content = envelope["result"]["content"]
            text = content[0]["text"]
            return json.loads(text).get("hits", [])
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            return []
    return []


def crowe_knowledge_base(query: str, limit: int = 5) -> str:
    """
    Search the proprietary CroweLM cultivation library by semantic similarity.

    Use for any mushroom-cultivation or mycology question that benefits from
    grounding in the Crowe corpus — species parameters, SOPs, contamination
    symptoms, substrate ratios. Returns the top matching passages with source
    titles and similarity scores; ground your answer in them and cite the
    titles.

    :param query: Natural-language query (species, technique, problem, symptom).
    :param limit: Maximum passages to return (1-10). Default 5.
    :return: JSON with a "hits" list of {title, similarity, content, tags}.
    :rtype: str
    """
    token = os.environ.get("CROWE_MYCOLOGY_MCP_TOKEN", "").strip()
    if not token:
        return json.dumps(
            {
                "error": "CROWE_MYCOLOGY_MCP_TOKEN not set — cultivation knowledge "
                "base is unavailable. Answer from general knowledge and say so.",
            }
        )
    url = os.environ.get("CROWE_MYCOLOGY_MCP_URL", _DEFAULT_MCP_URL)
    if not token.lower().startswith("bearer "):
        token = f"Bearer {token}"
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "queryKnowledgeBase",
            "arguments": {"query": query, "limit": max(1, min(10, int(limit)))},
        },
    }
    try:
        resp = httpx.request(
            "POST",
            url,
            json=body,
            headers={
                "Authorization": token,
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        return json.dumps({"hits": _parse_kb_hits(resp.text)})
    except Exception as e:  # noqa: BLE001 — surface any transport error to the model
        return json.dumps({"error": str(e)})
