# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
"""
SQLite + FTS5 store for the knowledge lake.

The schema is deliberately small. Two real tables (sources, chunks)
plus an FTS5 virtual table over the chunk content. Triggers keep
the FTS index in sync on insert/update/delete so the rest of the
code never has to think about it.

Connections are short-lived (one per public operation) and use
`PRAGMA journal_mode=WAL` so concurrent reads (e.g. the chat app
hitting search while an ingest runs) don't block each other.

Why SQLite + FTS5 first:
  - Zero deploy deps (the Python stdlib ships sqlite3).
  - FTS5 BM25 is genuinely good for "find me the paragraph about X"
    queries at the scale of the foundry's docs.
  - When we need vectors, the Store interface (this module's public
    API) is small enough to add a sibling pgvector backend that
    implements the same shape.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    name              TEXT PRIMARY KEY,
    kind              TEXT NOT NULL,           -- markdown | jsonl | latex | ...
    root              TEXT,                    -- filesystem root or URL
    description       TEXT,
    last_ingested_at  TEXT,                    -- ISO-8601, NULL = never
    chunk_count       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS chunks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL REFERENCES sources(name) ON DELETE CASCADE,
    path          TEXT NOT NULL,
    chunk_index   INTEGER NOT NULL,
    content       TEXT NOT NULL,
    metadata      TEXT NOT NULL DEFAULT '{}',   -- JSON
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS chunks_by_source ON chunks(source, path, chunk_index);

-- FTS5 over content. external-content keeps the actual text in the
-- chunks table and stores only the index here; the triggers below
-- mirror writes/deletes/updates so FTS stays consistent.
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    content,
    content='chunks',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;

CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO chunks_fts(rowid, content) VALUES (new.id, new.content);
END;
"""


def default_db_path() -> Path:
    """`~/.config/crowe-logic/knowledge.db` unless overridden by env."""
    override = os.environ.get("CROWE_KB_DB", "").strip()
    if override:
        return Path(override).expanduser()
    base = Path.home() / ".config" / "crowe-logic"
    base.mkdir(parents=True, exist_ok=True)
    return base / "knowledge.db"


@dataclass(frozen=True)
class SourceRow:
    name: str
    kind: str
    root: Optional[str]
    description: Optional[str]
    last_ingested_at: Optional[str]
    chunk_count: int


class Store:
    """Thin wrapper around a SQLite+FTS5 database. Stateless beyond
    holding the path; every call opens its own connection so tests
    can share a path across processes if needed.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.path = (db_path or default_db_path()).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ─── lifecycle ────────────────────────────────────────────────

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as c:
            c.executescript(_SCHEMA)

    # ─── source registry ─────────────────────────────────────────

    def upsert_source(
        self,
        name: str,
        kind: str,
        *,
        root: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO sources (name, kind, root, description)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                       kind = excluded.kind,
                       root = excluded.root,
                       description = excluded.description""",
                (name, kind, root, description),
            )

    def list_sources(self) -> list[SourceRow]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT name, kind, root, description, last_ingested_at, chunk_count "
                "FROM sources ORDER BY name"
            ).fetchall()
        return [SourceRow(**dict(r)) for r in rows]

    def get_source(self, name: str) -> Optional[SourceRow]:
        with self._conn() as c:
            row = c.execute(
                "SELECT name, kind, root, description, last_ingested_at, chunk_count "
                "FROM sources WHERE name = ?",
                (name,),
            ).fetchone()
        return SourceRow(**dict(row)) if row else None

    def delete_source(self, name: str) -> int:
        """Drop a source and all its chunks. Returns chunks deleted."""
        with self._conn() as c:
            # chunks cascade via FK; count first for the return.
            n = c.execute(
                "SELECT COUNT(*) AS n FROM chunks WHERE source = ?", (name,)
            ).fetchone()["n"]
            c.execute("DELETE FROM sources WHERE name = ?", (name,))
        return int(n)

    # ─── chunk write path ────────────────────────────────────────

    def replace_chunks(
        self,
        source: str,
        chunks: Iterable[tuple[str, int, str, dict[str, Any]]],
    ) -> int:
        """Atomically replace all chunks for `source`. Yields tuples of
        (path, chunk_index, content, metadata). Returns count written.

        This is the only write path. Ingestors always re-ingest by
        wholesale-replace; partial updates aren't worth the
        bookkeeping cost at the scale of one corpus.
        """
        n = 0
        with self._conn() as c:
            c.execute("DELETE FROM chunks WHERE source = ?", (source,))
            for path, idx, content, metadata in chunks:
                c.execute(
                    """INSERT INTO chunks (source, path, chunk_index, content, metadata)
                       VALUES (?, ?, ?, ?, ?)""",
                    (source, path, idx, content, json.dumps(metadata or {})),
                )
                n += 1
            c.execute(
                """UPDATE sources
                   SET chunk_count = ?,
                       last_ingested_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                   WHERE name = ?""",
                (n, source),
            )
        return n

    # ─── search ──────────────────────────────────────────────────

    def search(
        self,
        query: str,
        *,
        source: Optional[str] = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """FTS5 BM25 search. `query` is passed as-is to MATCH, so the
        caller can use FTS operators (AND, OR, NEAR, "phrase").
        """
        limit = max(1, min(limit, 200))
        sql = (
            "SELECT c.id, c.source, c.path, c.chunk_index, c.content, c.metadata, "
            "       bm25(chunks_fts) AS score "
            "FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
            "WHERE chunks_fts MATCH ? "
        )
        params: list[Any] = [query]
        if source:
            sql += " AND c.source = ?"
            params.append(source)
        sql += " ORDER BY score LIMIT ?"
        params.append(limit)
        with self._conn() as c:
            rows = c.execute(sql, params).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            try:
                d["metadata"] = json.loads(d["metadata"]) if d["metadata"] else {}
            except json.JSONDecodeError:
                d["metadata"] = {}
            out.append(d)
        return out

    def stats(self) -> dict[str, Any]:
        with self._conn() as c:
            total = c.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
            sources = c.execute("SELECT COUNT(*) AS n FROM sources").fetchone()["n"]
        size_bytes = self.path.stat().st_size if self.path.exists() else 0
        return {
            "db_path": str(self.path),
            "chunk_count": int(total),
            "source_count": int(sources),
            "size_bytes": int(size_bytes),
        }
