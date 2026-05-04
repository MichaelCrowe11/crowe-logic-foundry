"""Portfolio knowledge plane tools for Foundry agents.

Adds portfolio-wide awareness (registry, agent catalog, dataset catalog, and
code KB hybrid search) to every agent that has access to the tool registry.

Calls the crowe-portfolio HTTP server (see ~/Projects/crowe-portfolio).

Required env (set on Railway alongside the existing AZURE_CORE_* etc.):
    CROWE_PORTFOLIO_URL    e.g. https://crowe-portfolio.up.railway.app
    CROWE_PORTFOLIO_TOKEN  bearer token (matches PORTFOLIO_HTTP_TOKEN on the server)

Each tool returns JSON-formatted strings, matching the Foundry's existing
mcp_call_tool / mcp_list_tools convention. Errors return JSON with an "error"
key rather than raising, so model-side error handling stays consistent.
"""

from __future__ import annotations

import json
import os

import httpx

DEFAULT_TIMEOUT = 30.0


def _env() -> tuple[str, str] | None:
    url = os.environ.get("CROWE_PORTFOLIO_URL", "").strip()
    token = os.environ.get("CROWE_PORTFOLIO_TOKEN", "").strip()
    if not url or not token:
        return None
    return url.rstrip("/"), token


def _err(reason: str, **extra) -> str:
    return json.dumps({"error": reason, **extra})


def _get(path: str, params: dict | None = None) -> str:
    creds = _env()
    if creds is None:
        return _err(
            "portfolio_not_provisioned",
            hint="Set CROWE_PORTFOLIO_URL and CROWE_PORTFOLIO_TOKEN on Railway.",
        )
    url, token = creds
    try:
        resp = httpx.get(
            url + path,
            params=params or {},
            headers={"Authorization": f"Bearer {token}"},
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        return _err("upstream_error", path=path, detail=str(exc))
    return json.dumps(resp.json(), indent=2)


def _post(path: str, body: dict) -> str:
    creds = _env()
    if creds is None:
        return _err(
            "portfolio_not_provisioned",
            hint="Set CROWE_PORTFOLIO_URL and CROWE_PORTFOLIO_TOKEN on Railway.",
        )
    url, token = creds
    try:
        resp = httpx.post(
            url + path,
            json=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        return _err("upstream_error", path=path, detail=str(exc))
    return json.dumps(resp.json(), indent=2)


# --- Tool surface -----------------------------------------------------------


def portfolio_search_code(
    query: str,
    domain: str = "",
    repo: str = "",
    limit: int = 10,
) -> str:
    """Hybrid semantic + BM25 search across every canonical repo in the Crowe Logic portfolio.

    Returns ranked code/doc chunks with repo, path, line range, snippet, and
    similarity score. Use when answering 'where do we do X' or 'show me how
    we handle Y' across the whole codebase.

    :param query: Natural-language or code-like query.
    :param domain: Restrict to one domain (mycology, ai_platform, drug_discovery,
        quantum, voice_ai, content, books, legal, infrastructure, psychedelics,
        unclassified). Empty = all.
    :param repo: Restrict to one repo by exact name. Empty = all.
    :param limit: Max results (default 10, soft cap 100).
    :return: JSON list of hits with score, snippet, and citation.
    :rtype: str
    """
    return _post(
        "/search",
        {
            "query": query,
            "domain": domain or None,
            "repo": repo or None,
            "limit": min(int(limit), 100),
        },
    )


def portfolio_find_canonical(query: str) -> str:
    """Resolve a fuzzy repo name or concept to the canonical (shippable) repo.

    Use when the user mentions a project loosely ("the SW mushrooms storefront",
    "the Foundry control plane") and you need the actual GitHub repo name and
    its deploy URL. Returns the resolved canonical or {"found": false, ...}.

    :param query: Free-form name, alias, or concept.
    :return: JSON with canonical repo info or not-found marker.
    :rtype: str
    """
    return _post("/find_canonical", {"query": query})


def portfolio_list_repos(domain: str = "", status: str = "", limit: int = 50) -> str:
    """List repositories in the Crowe Logic portfolio with filters.

    :param domain: Filter by domain. Empty = all.
    :param status: Filter by canonical status (canonical, superseded, experiment,
        archive_candidate, solo, untriaged). Empty = all.
    :param limit: Max rows.
    :return: JSON list of repo records.
    :rtype: str
    """
    return _get(
        "/repos",
        {"domain": domain, "status": status, "limit": int(limit)},
    )


def portfolio_show_repo(name: str) -> str:
    """Full registry record for a repo by exact name.

    :param name: GitHub repo name (case-sensitive).
    :return: JSON record including supersedes/superseded_by, deploy_url, owner.
    :rtype: str
    """
    creds = _env()
    if creds is None:
        return _err("portfolio_not_provisioned")
    return _get(f"/repos/{name}")


def portfolio_list_clusters(min_size: int = 2) -> str:
    """List duplicate-suspect repo clusters.

    Each entry has cluster_key, size, the canonical pick (if assigned), and
    the member list with their canonical status. Use to audit naming / drift
    or to find candidates for archival.

    :param min_size: Minimum cluster size to include (default 2).
    :return: JSON array of clusters sorted by size desc.
    :rtype: str
    """
    return _get("/clusters", {"min_size": int(min_size)})


def portfolio_list_agents() -> str:
    """List every agent / model definition in the Crowe Logic Foundry framework.

    Returns name, label, provider, backend, aliases, tier, tags. Source: the
    Foundry's models.extra.json. Use to introspect the agent catalog when
    routing work or comparing tiers.

    :return: JSON list of agent definitions.
    :rtype: str
    """
    return _get("/agents")


def portfolio_get_agent(name: str) -> str:
    """Get a single agent's full definition including system prompt.

    :param name: Agent name or alias (e.g. 'crowelm-talon', 'apex-premium',
        'talon-nano').
    :return: JSON record with system prompt and KB-document rendering.
    :rtype: str
    """
    return _get(f"/agents/{name}")


def portfolio_list_datasets() -> str:
    """List structured datasets across the portfolio.

    Includes the Postgres knowledge graphs (the-record, epstein-database,
    prison-industrial-complex), training corpora (crowelm-unified-dataset,
    parallel-synth-dataset), LaTeX books (mushroom-grower volumes,
    cultivation handbooks), and curated research data.

    :return: JSON list of dataset entries (name, kind, repo, location, summary).
    :rtype: str
    """
    return _get("/datasets")


def portfolio_stale_repos(days: int = 180) -> str:
    """List repos with no recent push, no deploy URL, and not marked canonical.

    These are archive candidates pending review.

    :param days: Push-age threshold in days (default 180).
    :return: JSON list of stale candidates.
    :rtype: str
    """
    return _get("/stale", {"days": int(days)})
