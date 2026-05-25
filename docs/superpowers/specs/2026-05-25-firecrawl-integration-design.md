# Firecrawl Integration — Program Design

Date: 2026-05-25
Status: Approved (design). F0 + Sub-project 1 detailed; Sub-projects 2–3 are roadmap.

## Goal

Give the Crowe stack a single, well-bounded way to turn web pages into clean,
LLM-ready markdown (Firecrawl: `scrape`, `crawl`, `map`, `search`), consumed by
multiple surfaces without any of them touching the Firecrawl API directly.

## Principle

One thin client per language; thin consumers. Auth, retries, and error handling
live in the client. Consumers depend only on its interface, so a consumer can be
understood and tested without knowing Firecrawl internals, and the client can be
swapped (cloud → self-host) without touching consumers.

## Decisions

- **Hosting:** Firecrawl Cloud (firecrawl.dev). A `FIRECRAWL_BASE_URL` override is
  supported in the client so a future self-host switch is a config change, not a
  code change.
- **Secret handling:** `FIRECRAWL_API_KEY` lives in `~/.env.secrets` (already
  sourced by `.zshrc`) and in each deploy env as its consumer ships. Edits to
  `~/.env.secrets` use Python read-modify-write with a `shutil.copy2` backup,
  never `sed` (per the 2026-05-19 env-secrets incident).
- **Key never enters customer sandboxes.** The in-sandbox agent reaches Firecrawl
  through a gateway endpoint that holds the key, mirroring the existing
  `cl-gateway` pattern in crowecode-platform. This preserves the invariant in
  `modal-client.ts` that real provider keys stay out of user sandboxes.

---

## F0 — Foundation (build first)

### Components

1. **Dependency:** add `firecrawl-py` to foundry's Python deps (pyproject /
   requirements-control-plane as appropriate). Managed with `uv`.
2. **Client:** `tools/firecrawl_client.py`, following the `tools/portfolio_tools.py`
   pattern (module-level lazy singleton, env-driven config, typed returns).

### Interface

```python
@dataclass
class ScrapedPage:
    url: str
    markdown: str
    metadata: dict          # title, description, status, etc. from Firecrawl
    fetched_at: str         # ISO8601 UTC

@dataclass
class SearchHit:
    url: str
    title: str
    markdown: str | None    # present when scrape-on-search is requested

class FirecrawlClient:
    def scrape(self, url: str) -> ScrapedPage: ...
    def crawl(self, url: str, *, limit: int = 50, max_depth: int | None = None
              ) -> list[ScrapedPage]: ...
    def map(self, url: str, *, search: str | None = None) -> list[str]: ...
    def search(self, query: str, *, limit: int = 10,
               scrape: bool = False) -> list[SearchHit]: ...

def get_client() -> FirecrawlClient: ...   # lazy singleton from env
```

### Behavior

- `get_client()` reads `FIRECRAWL_API_KEY` (and optional `FIRECRAWL_BASE_URL`).
  Missing key raises `FirecrawlConfigError` with a one-line remediation message.
- One retry with exponential backoff on transient failures (HTTP 429 / 5xx /
  network). Non-transient errors raise `FirecrawlError` with the upstream detail.
- All returns carry provenance (`url`, `fetched_at`) so downstream consumers can
  record where content came from.

### Tests

- Unit: mock the `firecrawl` SDK; assert `scrape`/`crawl`/`map`/`search` map SDK
  responses to the dataclasses, that a missing key raises `FirecrawlConfigError`,
  and that a 429 triggers exactly one retry then succeeds/raises.
- Smoke (manual, needs key): `scrape` a stable page, assert non-empty markdown.

### Done when

Client imports, unit tests pass, and a manual smoke scrape returns markdown with
the key set locally.

---

## Sub-project 1 — Knowledge Lake URL/crawl ingestor (Python, foundry)

Adds Firecrawl-backed ingestion to the existing knowledge lake (SQLite+FTS5 at
`~/.config/crowe-logic/knowledge.db`, plus pgvector). Follows the established
LaTeX/JSONL ingestor seam; reuses the existing chunk → FTS5 + embedding pipeline
unchanged.

### CLI

- `crowe-logic kb ingest-url <url>` — single page.
- `crowe-logic kb ingest-site <url> [--limit N] [--max-depth D]` — crawl.

### Flow

`ingest-url` → `firecrawl_client.scrape` → markdown → existing chunker →
FTS5 + pgvector. `ingest-site` → `crawl` → per-page same path. Each chunk records
provenance (source url + `fetched_at`). Re-ingesting the same url is idempotent
(replace prior chunks for that url).

### Open item to confirm at implementation time

The exact ingestor interface and chunker entry point in the current `kb` code —
to be read and matched, not assumed. The design boundary (markdown in → existing
pipeline) does not change regardless.

### Tests

Ingest a known docs page, assert chunks land in FTS5 and a `kb search` for a
phrase on that page returns it; re-ingest and assert no duplicate chunks.

---

## Sub-project 2 — Research Engine fetch stage (Python, crowe-research) [roadmap]

The deep-researcher's web-fetch stage gains a Firecrawl-backed fetcher (`scrape`
for known URLs, `search` for query expansion) implementing the existing fetcher
interface so the rest of the four-stage cached pipeline is untouched. The actual
fetcher abstraction in crowe-research will be read and matched before this
sub-project is specced in detail.

## Sub-project 3 — Sandbox agent web tool (TS + Python) [roadmap]

The terminal/notebook agent gains `web_scrape` / `web_crawl`:
- crowecode-platform side (`src/lib/ai/tools.ts`): a tool that calls a Firecrawl
  **gateway endpoint** (new, holds the key) — not the SDK with an inlined key.
- in-sandbox crowe-logic: tools that call the same gateway, so no Firecrawl key
  enters the sandbox.

---

## Build sequence

F0 → Sub-project 1 → Sub-project 2 → Sub-project 3. Each sub-project gets its own
implementation plan. This document is the program spec; the first implementation
plan covers F0 (and, once F0 lands, Sub-project 1).

## Dependencies / blockers

- `FIRECRAWL_API_KEY` (cloud) required to test F0 and everything downstream. Not
  yet provisioned. Client code can be written and unit-tested (mocked) without it.
