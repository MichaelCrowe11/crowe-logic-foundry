# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
"""
Crowe Logic Knowledge Lake — local, cross-corpus searchable index.

Foundation for unifying the Crowe portfolio's heterogeneous datasets
(LaTeX books, JSONL training corpora, markdown documentation,
Postgres-backed knowledge graphs) behind one query surface.

Phase 1 (this commit):
  - SQLite + FTS5 backend at ~/.config/crowe-logic/knowledge.db
  - Pluggable ingestor base class + a markdown ingestor
  - One real source registered: this repo's own .md docs
  - CLI: `crowe-logic kb {status, sources, ingest, search}`

Phase 2 (planned, not in this commit):
  - LaTeX ingestor for the cultivation books
  - JSONL ingestor for crowelm-unified-dataset and parallel-synth-dataset
  - Postgres + pgvector backend as a swappable Store implementation
  - Embedding-based recall in parallel with BM25/FTS

The Store interface is intentionally narrow so swapping backends
(SQLite → Postgres+pgvector, or adding ChromaDB) doesn't ripple
into the ingestors or the CLI.
"""
from knowledge_lake.store import Store, default_db_path
from knowledge_lake.sources import KNOWN_SOURCES, Source, register
from knowledge_lake.ingest import IngestStats, Ingestor, MarkdownIngestor
from knowledge_lake.search import SearchHit, search

__all__ = [
    "Store",
    "default_db_path",
    "Source",
    "KNOWN_SOURCES",
    "register",
    "IngestStats",
    "Ingestor",
    "MarkdownIngestor",
    "SearchHit",
    "search",
]
