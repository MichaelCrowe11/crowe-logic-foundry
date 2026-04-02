#!/usr/bin/env python3
"""
Crowe Logic — MCP Server

Exposes the Crowe Logic platform (ai.southwestmushrooms.com) as MCP tools.
Any MCP client (Claude Code, Cursor, Windsurf, Gemini CLI, etc.) can connect.

Usage:
    python scripts/mcp_crowe_logic_ai.py                    # stdio transport (default)
    uvx crowe-logic-mcp                                  # after PyPI publish

Claude Code config:
    {"mcpServers": {"crowe-logic": {"command": "uvx", "args": ["crowe-logic-mcp"]}}}
"""

import json
import os

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "crowe-logic-ai",
    instructions="Crowe Logic — mycology expertise, photo analysis, grow logs, and SOP generation",
)


def _request(method: str, path: str, **kwargs) -> dict:
    """Send an authenticated request to the Crowe Logic AI platform."""
    url = os.environ.get("CROWE_LOGIC_URL", "https://ai.southwestmushrooms.com")
    key = os.environ.get("CROWE_LOGIC_KEY", "")

    headers = kwargs.pop("headers", {})
    if key:
        headers["Authorization"] = f"Bearer {key}"
    headers.setdefault("Content-Type", "application/json")

    response = httpx.request(method, f"{url}{path}", headers=headers, timeout=60.0, **kwargs)
    response.raise_for_status()
    return response.json()


@mcp.tool()
def crowe_chat(message: str, context: str = "") -> str:
    """Chat with CroweLM for mycology and cultivation expertise.

    Args:
        message: Your question or message about mushroom cultivation, mycology, or related topics.
        context: Optional conversation context for multi-turn conversations.
    """
    result = _request("POST", "/api/chat", json={"message": message, "context": context})
    return json.dumps(result, indent=2)


@mcp.tool()
def crowe_vision(image_base64: str, prompt: str = "Analyze this image") -> str:
    """Analyze an image using Crowe Vision — specialized for mushroom cultivation photos.

    Detects contamination, assesses mycelium health, identifies species, and evaluates growth stages.

    Args:
        image_base64: Base64-encoded image data.
        prompt: What to analyze about the image.
    """
    result = _request("POST", "/api/crowe-vision/analyze", json={"image": image_base64, "prompt": prompt})
    return json.dumps(result, indent=2)


@mcp.tool()
def crowe_grow_log(action: str, data: str = "{}") -> str:
    """Manage mushroom cultivation grow logs.

    Args:
        action: Operation — "list", "create", "read", or "update".
        data: JSON string with log data. For create: {"species": "shiitake", ...}. For read: {"id": "log_id"}.
    """
    parsed = json.loads(data)

    if action == "list":
        result = _request("GET", "/api/conversations")
    elif action == "create":
        result = _request("POST", "/api/conversations", json=parsed)
    elif action == "read":
        result = _request("GET", f"/api/conversations/{parsed.get('id', '')}")
    elif action == "update":
        log_id = parsed.pop("id", "")
        result = _request("PATCH", f"/api/conversations/{log_id}", json=parsed)
    else:
        return json.dumps({"error": f"Unknown action: {action}"})

    return json.dumps(result, indent=2)


@mcp.tool()
def crowe_sop(topic: str, parameters: str = "{}") -> str:
    """Generate a Standard Operating Procedure for mushroom cultivation tasks.

    Args:
        topic: The SOP topic (e.g., "substrate preparation", "fruiting chamber setup", "spawn production").
        parameters: Optional JSON with extra parameters like species, scale, or specific requirements.
    """
    parsed = json.loads(parameters)
    parsed["topic"] = topic
    result = _request("POST", "/api/chat", json={
        "message": f"Generate a detailed Standard Operating Procedure for: {topic}",
        "context": json.dumps(parsed),
    })
    return json.dumps(result, indent=2)


def main():
    """Entry point for the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
