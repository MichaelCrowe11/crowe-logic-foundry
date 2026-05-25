"""Tests for the shared Firecrawl client (F0 foundation).

The client wraps the `firecrawl.Firecrawl` SDK. To test behavior without network
or an API key, we inject a fake SDK that mimics the surface we depend on
(scrape/crawl/map/search and their pydantic-ish return shapes).
"""

from types import SimpleNamespace

import pytest

from tools.firecrawl_client import (
    FirecrawlClient,
    FirecrawlConfigError,
    ScrapedPage,
    SearchHit,
    get_client,
)


class FakeSDK:
    """Mimics the slice of firecrawl.Firecrawl that FirecrawlClient uses."""

    def __init__(self):
        self.calls = []

    def scrape(self, url, formats=None):
        self.calls.append(("scrape", url, formats))
        return SimpleNamespace(
            markdown="# Hello", metadata={"title": "Hi", "sourceURL": url}
        )

    def crawl(self, url, limit=None, max_discovery_depth=None):
        self.calls.append(("crawl", url, limit, max_discovery_depth))
        return SimpleNamespace(
            data=[
                SimpleNamespace(markdown="# A", metadata={"sourceURL": "https://x/a"}),
                SimpleNamespace(markdown="# B", metadata={"sourceURL": "https://x/b"}),
            ]
        )

    def map(self, url, search=None):
        self.calls.append(("map", url, search))
        return SimpleNamespace(
            links=[
                SimpleNamespace(url="https://x/1", title="1", description=""),
                SimpleNamespace(url="https://x/2", title="2", description=""),
            ]
        )

    def search(self, query, limit=None):
        self.calls.append(("search", query, limit))
        return SimpleNamespace(
            web=[SimpleNamespace(url="https://r/1", title="R1", description="d")],
            news=[],
            images=[],
        )


def test_get_client_raises_without_api_key(monkeypatch):
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    import tools.firecrawl_client as fc

    fc._client = None  # reset lazy singleton
    with pytest.raises(FirecrawlConfigError):
        get_client()


def test_scrape_maps_document_to_scraped_page():
    c = FirecrawlClient(api_key="k", sdk=FakeSDK())
    page = c.scrape("https://x/a")
    assert isinstance(page, ScrapedPage)
    assert page.markdown == "# Hello"
    assert page.url == "https://x/a"
    assert page.metadata["title"] == "Hi"
    assert page.fetched_at  # ISO timestamp populated


def test_scrape_requests_markdown_format():
    sdk = FakeSDK()
    FirecrawlClient(api_key="k", sdk=sdk).scrape("https://x/a")
    assert sdk.calls[0] == ("scrape", "https://x/a", ["markdown"])


def test_crawl_maps_job_data_to_pages():
    c = FirecrawlClient(api_key="k", sdk=FakeSDK())
    pages = c.crawl("https://x", limit=2)
    assert [p.url for p in pages] == ["https://x/a", "https://x/b"]
    assert all(isinstance(p, ScrapedPage) for p in pages)


def test_map_returns_url_strings():
    c = FirecrawlClient(api_key="k", sdk=FakeSDK())
    assert c.map("https://x") == ["https://x/1", "https://x/2"]


def test_search_maps_web_results_to_hits():
    c = FirecrawlClient(api_key="k", sdk=FakeSDK())
    hits = c.search("q", limit=5)
    assert len(hits) == 1
    assert isinstance(hits[0], SearchHit)
    assert hits[0].url == "https://r/1"
    assert hits[0].title == "R1"
