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
    -- JSON-encoded list[float]. NULL when no embedding provider is
    -- configured (knowledge_lake.embeddings.is_configured() is False).
    embedding     TEXT,
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


def _blend(
    fts_rows: list[dict[str, Any]],
    vec_rows: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """Min-max normalize BM25 scores, then blend 50/50 with cosine.

    BM25 is negative (lower=better); we flip sign and scale to [0,1]
    within the FTS hit set. Cosine is already in [-1, 1]; clamped to
    [0, 1] so the blend is monotonic. Rows that hit only one signal
    still appear with their single-signal score doubled-and-halved
    (so a vector-only row scores `0.5 * cosine`, fts-only row scores
    `0.5 * norm_bm25`).
    """
    bm25 = [r["score"] for r in fts_rows]
    if bm25:
        lo, hi = min(bm25), max(bm25)
        span = (hi - lo) or 1.0

        def _norm(s: float) -> float:
            # Flip so higher = better; scale to [0, 1].
            return (hi - s) / span
    else:
        def _norm(_s: float) -> float:
            return 0.0

    by_id: dict[int, dict[str, Any]] = {}
    for r in fts_rows:
        r2 = dict(r)
        r2["_bm25_norm"] = _norm(r["score"])
        r2["_cos"] = 0.0
        by_id[r["id"]] = r2
    for r in vec_rows:
        cos_clamped = max(0.0, min(1.0, float(r["score"])))
        if r["id"] in by_id:
            by_id[r["id"]]["_cos"] = cos_clamped
        else:
            r2 = dict(r)
            r2["_bm25_norm"] = 0.0
            r2["_cos"] = cos_clamped
            by_id[r["id"]] = r2
    blended = list(by_id.values())
    for r in blended:
        r["score"] = 0.5 * r["_bm25_norm"] + 0.5 * r["_cos"]
        r.pop("_bm25_norm", None)
        r.pop("_cos", None)
    blended.sort(key=lambda r: -r["score"])
    return blended[:limit]


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
            # Additive column-add for databases that pre-date the
            # embedding feature. ALTER TABLE ADD COLUMN with a NULL
            # default is a metadata-only op in SQLite and is safe to
            # run on every open. Wrapped in try/except because the
            # second call would raise "duplicate column" otherwise.
            try:
                c.execute("ALTER TABLE chunks ADD COLUMN embedding TEXT")
            except sqlite3.OperationalError:
                pass

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
        *,
        embedder: Optional[Any] = None,
    ) -> int:
        """Atomically replace all chunks for `source`.

        `chunks` yields (path, chunk_index, content, metadata).
        When `embedder` is provided, it must be a callable
        `(text: str) -> Optional[list[float]]` (e.g.
        `knowledge_lake.embeddings.embed_text`). Embeddings are
        computed inline and stored as JSON in the `embedding` column.
        A None return is treated as "skip vector storage" so the row
        still lands without embedding — FTS keeps working regardless.

        Returns the number of rows written.
        """
        # Lazy import to avoid a circular dep when embeddings.py
        # eventually imports from store.py.
        from knowledge_lake.embeddings import serialize as _ser

        n = 0
        with self._conn() as c:
            c.execute("DELETE FROM chunks WHERE source = ?", (source,))
            for path, idx, content, metadata in chunks:
                embedding_blob: Optional[str] = None
                if embedder is not None:
                    try:
                        vec = embedder(content)
                    except Exception:
                        vec = None
                    if vec:
                        embedding_blob = _ser(vec)
                c.execute(
                    """INSERT INTO chunks
                           (source, path, chunk_index, content, metadata, embedding)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (source, path, idx, content,
                     json.dumps(metadata or {}), embedding_blob),
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
        mode: str = "fts",
        query_vector: Optional[list[float]] = None,
    ) -> list[dict[str, Any]]:
        """Search the corpus.

        Modes:
          - "fts"     (default): FTS5 BM25 only. `query` is passed
            straight to MATCH so the caller can use FTS operators
            (AND, OR, NEAR, "phrase").
          - "vector": cosine similarity against `query_vector`. The
            caller is responsible for embedding the query (typically
            with knowledge_lake.embeddings.embed_text). Rows without
            an embedding are skipped.
          - "hybrid": run both, then blend. BM25 is min-max normalized
            within the FTS hit set, cosine is already in [-1, 1];
            final score is 0.5*norm_bm25 + 0.5*cosine. Rows that hit
            either signal can surface.

        Returns ranked rows. Each row carries id, source, path,
        chunk_index, content, decoded metadata, and `score` (lower is
        better in fts mode; higher is better in vector/hybrid modes).
        """
        limit = max(1, min(limit, 200))
        if mode not in ("fts", "vector", "hybrid"):
            raise ValueError(f"unknown mode {mode!r}")
        if mode in ("vector", "hybrid") and query_vector is None:
            raise ValueError(f"mode={mode!r} requires query_vector")

        # FTS path is unchanged for backward compat. Vector / hybrid
        # paths are layered on top.
        fts_rows: list[dict[str, Any]] = []
        if mode in ("fts", "hybrid"):
            sql = (
                "SELECT c.id, c.source, c.path, c.chunk_index, c.content, "
                "       c.metadata, c.embedding, bm25(chunks_fts) AS score "
                "FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
                "WHERE chunks_fts MATCH ? "
            )
            params: list[Any] = [query]
            if source:
                sql += " AND c.source = ?"
                params.append(source)
            # In hybrid mode we pull more rows so the blend has signal
            # to work with; the final top-N is computed after merging.
            sql += " ORDER BY score LIMIT ?"
            params.append(limit if mode == "fts" else max(limit * 4, 40))
            with self._conn() as c:
                fts_rows = [dict(r) for r in c.execute(sql, params).fetchall()]

        vec_rows: list[dict[str, Any]] = []
        if mode in ("vector", "hybrid"):
            from knowledge_lake.embeddings import cosine, deserialize
            sql = (
                "SELECT id, source, path, chunk_index, content, metadata, "
                "       embedding FROM chunks WHERE embedding IS NOT NULL"
            )
            params2: list[Any] = []
            if source:
                sql += " AND source = ?"
                params2.append(source)
            with self._conn() as c:
                candidates = c.execute(sql, params2).fetchall()
            for row in candidates:
                d = dict(row)
                vec = deserialize(d.pop("embedding", None))
                if vec is None:
                    continue
                sim = cosine(query_vector or [], vec)
                d["score"] = sim
                vec_rows.append(d)
            vec_rows.sort(key=lambda r: -r["score"])
            vec_rows = vec_rows[: max(limit * 4, 40) if mode == "hybrid" else limit]

        # Combine.
        if mode == "fts":
            ranked = fts_rows
        elif mode == "vector":
            ranked = vec_rows
        else:
            # min-max normalize BM25 inside the FTS hit set. Lower
            # bm25 = better; flip sign to align with cosine.
            ranked = _blend(fts_rows, vec_rows, limit)

        out: list[dict[str, Any]] = []
        for r in ranked[:limit]:
            r.pop("embedding", None)
            try:
                r["metadata"] = (
                    json.loads(r["metadata"]) if r.get("metadata") else {}
                )
            except (json.JSONDecodeError, TypeError):
                r["metadata"] = {}
            out.append(r)
        return out

    def get_chunk(
        self,
        *,
        source: str,
        path: str,
        chunk_index: int,
    ) -> Optional[dict[str, Any]]:
        """Look up an exact chunk by its citation triple.

        Citations rendered in chat responses carry source + path +
        chunk_index. The chip drawer uses this to fetch the full
        content without an FTS round trip.

        Returns the chunk's id, source, path, chunk_index, content,
        metadata (decoded JSON), and created_at, or None when nothing
        matches.
        """
        with self._conn() as c:
            row = c.execute(
                """SELECT id, source, path, chunk_index, content,
                          metadata, created_at
                   FROM chunks
                   WHERE source = ? AND path = ? AND chunk_index = ?""",
                (source, path, chunk_index),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["metadata"] = json.loads(d["metadata"]) if d["metadata"] else {}
        except json.JSONDecodeError:
            d["metadata"] = {}
        return d

    def get_chunk_by_rowid(self, rowid: int) -> Optional[dict[str, Any]]:
        """Look up a chunk by its internal rowid. Used by ranked search
        result pipelines that already carry the id.
        """
        with self._conn() as c:
            row = c.execute(
                """SELECT id, source, path, chunk_index, content,
                          metadata, created_at
                   FROM chunks WHERE id = ?""",
                (rowid,),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["metadata"] = json.loads(d["metadata"]) if d["metadata"] else {}
        except json.JSONDecodeError:
            d["metadata"] = {}
        return d

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
