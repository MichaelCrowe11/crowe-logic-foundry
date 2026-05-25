"""Shared Firecrawl client (F0 foundation).

A thin wrapper over the `firecrawl.Firecrawl` SDK so that every Crowe consumer
(knowledge-lake ingestor, research-engine fetch stage, sandbox agent) reaches
Firecrawl through one place. Auth, config, and the SDK's own retry/backoff live
here; consumers depend only on the typed returns below.

Design ref: docs/superpowers/specs/2026-05-25-firecrawl-integration-design.md
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

DEFAULT_API_URL = "https://api.firecrawl.dev"


class FirecrawlConfigError(RuntimeError):
    """Raised when the client is used without a configured API key."""


class FirecrawlError(RuntimeError):
    """Raised when a Firecrawl call fails non-transiently."""


@dataclass
class ScrapedPage:
    """One page of clean content plus provenance."""

    url: str
    markdown: str
    metadata: dict = field(default_factory=dict)
    fetched_at: str = ""


@dataclass
class SearchHit:
    url: str
    title: str
    markdown: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _page_url(doc, fallback: str) -> str:
    """Crawl results carry their own source URL in metadata; fall back to the
    requested URL when absent."""
    meta = getattr(doc, "metadata", None) or {}
    return meta.get("sourceURL") or meta.get("url") or fallback


class FirecrawlClient:
    """Wraps the Firecrawl SDK. Inject `sdk` in tests to avoid network/keys."""

    def __init__(self, api_key: str, api_url: str | None = None, sdk=None):
        if sdk is not None:
            self._fc = sdk
            return
        from firecrawl import Firecrawl

        # The SDK retries transient failures itself (max_retries/backoff_factor).
        self._fc = Firecrawl(api_key=api_key, api_url=api_url or DEFAULT_API_URL)

    def scrape(self, url: str) -> ScrapedPage:
        doc = self._fc.scrape(url, formats=["markdown"])
        return ScrapedPage(
            url=url,
            markdown=getattr(doc, "markdown", "") or "",
            metadata=getattr(doc, "metadata", None) or {},
            fetched_at=_now(),
        )

    def crawl(
        self, url: str, *, limit: int = 50, max_depth: int | None = None
    ) -> list[ScrapedPage]:
        job = self._fc.crawl(url, limit=limit, max_discovery_depth=max_depth)
        fetched = _now()
        return [
            ScrapedPage(
                url=_page_url(doc, url),
                markdown=getattr(doc, "markdown", "") or "",
                metadata=getattr(doc, "metadata", None) or {},
                fetched_at=fetched,
            )
            for doc in (getattr(job, "data", None) or [])
        ]

    def map(self, url: str, *, search: str | None = None) -> list[str]:
        res = self._fc.map(url, search=search) if search else self._fc.map(url)
        return [link.url for link in (getattr(res, "links", None) or [])]

    def search(self, query: str, *, limit: int = 10) -> list[SearchHit]:
        res = self._fc.search(query, limit=limit)
        return [
            SearchHit(url=r.url, title=getattr(r, "title", "") or "")
            for r in (getattr(res, "web", None) or [])
        ]


_client: FirecrawlClient | None = None


def get_client() -> FirecrawlClient:
    """Lazy singleton built from FIRECRAWL_API_KEY (+ optional FIRECRAWL_BASE_URL)."""
    global _client
    if _client is None:
        key = os.environ.get("FIRECRAWL_API_KEY")
        if not key:
            raise FirecrawlConfigError(
                "FIRECRAWL_API_KEY is not set. Add it to ~/.env.secrets."
            )
        _client = FirecrawlClient(
            api_key=key, api_url=os.environ.get("FIRECRAWL_BASE_URL")
        )
    return _client
