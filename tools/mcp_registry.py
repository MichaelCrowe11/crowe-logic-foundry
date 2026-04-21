"""
MCP Registry Client — Discover any MCP server from the official registry.

The registry at registry.modelcontextprotocol.io catalogs 5,800+ MCP servers
covering databases, APIs, cloud providers, dev tools, and more.
"""

import json

REGISTRY_API = "https://registry.modelcontextprotocol.io/v0/servers"


def mcp_search(query: str, limit: int = 10) -> str:
    """
    Search the MCP server registry for servers matching a query.
    Returns server names, descriptions, package info, and transport types.
    Use this to discover what MCP capabilities are available before calling them.

    :param query: Search query (e.g. "postgres", "slack", "github", "s3").
    :param limit: Maximum number of results (default 10, max 50).
    :return: JSON list of matching MCP servers with install instructions.
    :rtype: str
    """
    import httpx

    limit = min(int(limit), 50)

    try:
        resp = httpx.get(
            REGISTRY_API,
            params={"search": query, "limit": limit},
            timeout=15,
            headers={"User-Agent": "CroweLogic/0.1"},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return json.dumps({"error": f"Registry search failed: {e}"})

    servers = data.get("servers", [])
    results = []
    for entry in servers:
        server = entry.get("server", entry)
        name = server.get("name", "unknown")
        desc = server.get("description", "")
        version = server.get("version", "")
        packages = server.get("packages", [])

        pkg_info = []
        for pkg in packages:
            reg_type = pkg.get("registryType", "")
            identifier = pkg.get("identifier", "")
            transport = pkg.get("transport", {}).get("type", "unknown")
            pkg_info.append({
                "type": reg_type,
                "package": identifier,
                "transport": transport,
            })

        results.append({
            "name": name,
            "description": desc,
            "version": version,
            "packages": pkg_info,
        })

    return json.dumps({
        "query": query,
        "count": len(results),
        "servers": results,
    })
