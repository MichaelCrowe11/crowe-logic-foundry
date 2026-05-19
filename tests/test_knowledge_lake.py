"""Tests for the knowledge-lake foundation.

Covers the Store schema + write/read round trip, the markdown
ingestor (chunking, exclude globs, frontmatter strip), the search
facade, and end-to-end ingest + search against a tmp_path corpus.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knowledge_lake.ingest import (
    MarkdownIngestor,
    _chunk_paragraphs,
    _glob_match,
)
from knowledge_lake.search import search
from knowledge_lake.sources import Source
from knowledge_lake.store import Store


# ─── Store basics ──────────────────────────────────────────────

def test_store_creates_schema_on_first_open(tmp_path):
    s = Store(tmp_path / "kb.db")
    assert (tmp_path / "kb.db").exists()
    # idempotent — re-opening must not error.
    Store(tmp_path / "kb.db")
    assert s.stats()["chunk_count"] == 0


def test_store_round_trip_and_search(tmp_path):
    s = Store(tmp_path / "kb.db")
    s.upsert_source("test-src", "markdown", root=str(tmp_path), description="t")
    n = s.replace_chunks("test-src", [
        ("a.md", 0, "Mycelial succession is a fungal community process.", {}),
        ("a.md", 1, "Substrate composition drives the colonization rate.", {}),
        ("b.md", 0, "Crowe Logic ships an OpenAI-compatible API.", {}),
    ])
    assert n == 3
    hits = s.search("mycelial")
    assert len(hits) == 1
    assert hits[0]["path"] == "a.md"
    assert hits[0]["chunk_index"] == 0

    scoped = s.search("crowe logic", source="test-src", limit=5)
    assert len(scoped) == 1
    assert scoped[0]["path"] == "b.md"


def test_replace_chunks_is_atomic(tmp_path):
    s = Store(tmp_path / "kb.db")
    s.upsert_source("t", "markdown")
    s.replace_chunks("t", [("a.md", 0, "first", {})])
    s.replace_chunks("t", [
        ("a.md", 0, "second one", {}),
        ("a.md", 1, "third one", {}),
    ])
    assert s.stats()["chunk_count"] == 2
    hits = s.search("second")
    assert len(hits) == 1


def test_delete_source_cascades(tmp_path):
    s = Store(tmp_path / "kb.db")
    s.upsert_source("t", "markdown")
    s.replace_chunks("t", [("a.md", 0, "hello world", {})])
    n = s.delete_source("t")
    assert n == 1
    assert s.stats()["chunk_count"] == 0
    assert s.get_source("t") is None


# ─── Chunking ──────────────────────────────────────────────────

def test_chunk_paragraphs_keeps_small_blocks_together():
    text = "Short paragraph one.\n\nShort two.\n\nShort three."
    chunks = _chunk_paragraphs(text)
    assert chunks == ["Short paragraph one.\n\nShort two.\n\nShort three."]


def test_chunk_paragraphs_splits_oversize_paragraph():
    long = ". ".join([f"Sentence {i} fills the buffer." for i in range(200)])
    chunks = _chunk_paragraphs(long)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c) < 1500


def test_chunk_paragraphs_empty_input():
    assert _chunk_paragraphs("") == []
    assert _chunk_paragraphs("\n\n\n") == []


# ─── glob matching ─────────────────────────────────────────────

def test_glob_match_double_star_prefix():
    assert _glob_match("docs/a/b.md", "**/b.md")
    assert _glob_match("b.md", "**/b.md")


def test_glob_match_double_star_suffix():
    assert _glob_match("node_modules/a/b/c.js", "node_modules/**")
    assert _glob_match("node_modules", "node_modules/**")


def test_glob_match_negative():
    assert not _glob_match("docs/a/b.md", "src/**/*.md")


# ─── Markdown ingestor end-to-end ──────────────────────────────

def test_markdown_ingest_walks_and_strips_frontmatter(tmp_path):
    # Build a tiny corpus.
    (tmp_path / "readme.md").write_text(
        "---\ntitle: x\n---\n# Hello\n\nThis is a paragraph about mycelium.",
        encoding="utf-8",
    )
    sub = tmp_path / "docs"
    sub.mkdir()
    (sub / "guide.md").write_text(
        "# Guide\n\nThe foundry exposes an OpenAI-compatible /v1 endpoint.",
        encoding="utf-8",
    )
    (sub / "skipme.md").write_text("# Skip me", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.md").write_text("noise", encoding="utf-8")

    src = Source(
        name="t",
        kind="markdown",
        root=tmp_path,
        description="test corpus",
        include_globs=("*.md", "docs/**/*.md"),
        exclude_globs=("node_modules/**", "docs/skipme.md"),
    )
    store = Store(tmp_path / "kb.db")
    stats = MarkdownIngestor(store, src).run()
    assert stats.files_seen == 2          # readme + guide (skipme excluded, node_modules excluded)
    assert stats.files_ingested == 2
    assert stats.chunks_written >= 2

    # Frontmatter stripped — "title: x" should not surface.
    matched_title = store.search("title")
    assert len(matched_title) == 0

    hits = store.search("mycelium")
    assert len(hits) == 1
    assert hits[0]["path"] == "readme.md"


def test_markdown_ingest_raises_when_root_missing(tmp_path):
    missing = tmp_path / "nope"
    src = Source(name="x", kind="markdown", root=missing, description="d")
    store = Store(tmp_path / "kb.db")
    with pytest.raises(FileNotFoundError):
        MarkdownIngestor(store, src).run()


# ─── search facade snippets ────────────────────────────────────

def test_search_facade_returns_snippet_around_query(tmp_path, monkeypatch):
    store = Store(tmp_path / "kb.db")
    store.upsert_source("t", "markdown")
    long = " ".join([f"word{i}" for i in range(100)])
    needle = long + " mycelium grows in substrate. " + long
    store.replace_chunks("t", [("a.md", 0, needle, {})])

    hits = search("mycelium", store=store)
    assert len(hits) == 1
    h = hits[0]
    assert "mycelium" in h.snippet.lower()
    assert h.path == "a.md"
    assert isinstance(h.score, float)


def test_search_returns_empty_for_no_match(tmp_path):
    store = Store(tmp_path / "kb.db")
    store.upsert_source("t", "markdown")
    store.replace_chunks("t", [("a.md", 0, "hello world", {})])
    hits = search("nonexistent", store=store)
    assert hits == []
