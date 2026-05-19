"""Tests for the knowledge-lake foundation.

Covers the Store schema + write/read round trip, the markdown
ingestor (chunking, exclude globs, frontmatter strip), the search
facade, and end-to-end ingest + search against a tmp_path corpus.
"""

from __future__ import annotations


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


# ─── LaTeX ingestor ────────────────────────────────────────────

from knowledge_lake.ingest import JsonlIngestor, LatexIngestor


def test_latex_strips_macros_and_keeps_section_text(tmp_path):
    (tmp_path / "book.tex").write_text(
        "\\documentclass{book}\n"
        "\\usepackage{amsmath}\n"
        "\\title{Cultivation}\n"
        "\\begin{document}\n"
        "\\maketitle\n"
        "% this is a comment that should disappear\n"
        "\\section{Substrate}\n"
        "\\textbf{Hardwood} sawdust is the canonical \\emph{Pleurotus} substrate.\n"
        "It must be \\textit{pasteurized} before inoculation \\cite{stamets2000}.\n"
        "\\subsection{Moisture}\n"
        "Target 60 percent field capacity. See \\ref{fig:moisture}.\n"
        "\\end{document}\n"
        "\\appendix\n",
        encoding="utf-8",
    )
    src = Source(
        name="latex-test",
        kind="latex",
        root=tmp_path,
        description="t",
    )
    store = Store(tmp_path / "kb.db")
    stats = LatexIngestor(store, src).run()
    assert stats.files_ingested == 1
    assert stats.chunks_written >= 1

    # Macros stripped — the visible argument survives.
    hits = store.search("hardwood")
    assert len(hits) == 1
    assert "Hardwood" in hits[0]["content"]
    # \cite{stamets2000} should not appear anywhere.
    assert "stamets2000" not in hits[0]["content"]
    assert "cite" not in hits[0]["content"].lower()
    # Comment is gone.
    assert "this is a comment" not in hits[0]["content"]
    # Preamble dropped: usepackage / title metadata must not be searchable.
    assert store.search("amsmath") == []


def test_latex_section_breaks_chunk_boundaries(tmp_path):
    # Each section is >1200 chars so the paragraph splitter is forced
    # to give each its own chunk (rather than packing small sections
    # together — which is also correct behavior but harder to assert).
    big_filler = "Filler text about cultivation methods. " * 60  # ~2.4 kB
    body = "\n".join(
        f"\\section{{Chapter {i}}}\nIntro to topic {i}.\n\n{big_filler}"
        for i in range(5)
    )
    (tmp_path / "book.tex").write_text(
        "\\begin{document}\n" + body + "\n\\end{document}",
        encoding="utf-8",
    )
    src = Source(name="x", kind="latex", root=tmp_path, description="")
    store = Store(tmp_path / "kb.db")
    stats = LatexIngestor(store, src).run()
    # 5 oversize sections → at least 5 chunks, since each forces a
    # boundary AND each oversize section gets sentence-split inside.
    assert stats.chunks_written >= 5
    # The heading marker survives in searchable form.
    hits = store.search("Chapter 3")
    assert len(hits) >= 1


# ─── JSONL ingestor ────────────────────────────────────────────

def test_jsonl_reads_default_text_field(tmp_path):
    (tmp_path / "corpus.jsonl").write_text(
        '{"text": "Mycorrhizal succession is a stepwise community process."}\n'
        '{"text": "Hardwood sawdust supports Pleurotus ostreatus colonization."}\n'
        '{"text": "Lion\'s Mane fruits at 18 to 22 Celsius."}\n',
        encoding="utf-8",
    )
    src = Source(name="jsonl-test", kind="jsonl", root=tmp_path, description="")
    store = Store(tmp_path / "kb.db")
    stats = JsonlIngestor(store, src).run()
    assert stats.files_ingested == 1
    assert stats.chunks_written == 3

    hits = store.search("hardwood")
    assert len(hits) == 1


def test_jsonl_skips_malformed_lines(tmp_path):
    (tmp_path / "corpus.jsonl").write_text(
        '{"text": "good record"}\n'
        'not json at all\n'
        '{"text": ""}\n'                # empty text
        '"a string, not an object"\n'   # valid JSON but wrong type
        '{"text": "another good one"}\n',
        encoding="utf-8",
    )
    src = Source(name="jsonl-skip", kind="jsonl", root=tmp_path, description="")
    store = Store(tmp_path / "kb.db")
    stats = JsonlIngestor(store, src).run()
    assert stats.chunks_written == 2
    # The skips are reported with their line numbers.
    assert any("line " in s or ":" in s for s in stats.skipped_paths)


def test_jsonl_records_non_text_fields_as_metadata(tmp_path):
    (tmp_path / "corpus.jsonl").write_text(
        '{"text": "first", "label": "intro", "score": 0.9}\n'
        '{"text": "second", "label": "main"}\n',
        encoding="utf-8",
    )
    src = Source(name="jsonl-meta", kind="jsonl", root=tmp_path, description="")
    store = Store(tmp_path / "kb.db")
    JsonlIngestor(store, src).run()
    hits = store.search("first")
    assert len(hits) == 1
    assert hits[0]["metadata"].get("label") == "intro"


def test_dispatch_table_resolves_all_kinds(tmp_path):
    from knowledge_lake.ingest import ingestor_for
    store = Store(tmp_path / "kb.db")
    for kind, expected in (
        ("markdown", "MarkdownIngestor"),
        ("latex", "LatexIngestor"),
        ("jsonl", "JsonlIngestor"),
    ):
        src = Source(name=f"x-{kind}", kind=kind, root=tmp_path, description="")
        ing = ingestor_for(src, store)
        assert type(ing).__name__ == expected


# ─── ingest-all CLI ────────────────────────────────────────────

# ─── embeddings, vector + hybrid search ───────────────────────

from knowledge_lake.embeddings import cosine, deserialize, serialize


def test_cosine_orthogonal_zero_identical_one():
    assert abs(cosine([1.0, 0.0], [0.0, 1.0])) < 1e-9
    assert abs(cosine([1.0, 0.0, 1.0], [1.0, 0.0, 1.0]) - 1.0) < 1e-9
    # Zero-length / mismatched lengths should not crash.
    assert cosine([], [1.0]) == 0.0
    assert cosine([1.0, 2.0], [1.0]) == 0.0


def test_serialize_deserialize_round_trip():
    vec = [0.1, -0.25, 0.333333333]
    blob = serialize(vec)
    back = deserialize(blob)
    assert back is not None
    assert len(back) == 3
    # serialize() rounds to 6 sig figs.
    assert abs(back[2] - 0.333333) < 1e-9
    assert deserialize(None) is None
    assert deserialize("not json") is None
    assert deserialize('{"x": 1}') is None  # not a list of floats


def test_replace_chunks_stores_embeddings_when_embedder_provided(tmp_path):
    store = Store(tmp_path / "kb.db")
    store.upsert_source("t", "markdown")

    # Hand-rolled embedder: 2-dim, one axis per keyword.
    def fake_embed(text: str) -> list[float] | None:
        t = text.lower()
        return [1.0 if "alpha" in t else 0.0, 1.0 if "beta" in t else 0.0]

    n = store.replace_chunks(
        "t",
        [
            ("a.md", 0, "alpha alpha alpha", {}),
            ("a.md", 1, "beta beta", {}),
            ("a.md", 2, "neither word here", {}),
        ],
        embedder=fake_embed,
    )
    assert n == 3

    # Sanity: the row that was "neither" gets a [0, 0] embedding,
    # but the column is populated for every row.
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "kb.db"))
    rows = conn.execute("SELECT embedding FROM chunks ORDER BY chunk_index").fetchall()
    conn.close()
    assert all(r[0] is not None for r in rows)


def test_search_vector_mode(tmp_path):
    store = Store(tmp_path / "kb.db")
    store.upsert_source("t", "markdown")

    def fake_embed(text: str) -> list[float]:
        t = text.lower()
        return [1.0 if "alpha" in t else 0.0, 1.0 if "beta" in t else 0.0]

    store.replace_chunks(
        "t",
        [
            ("a.md", 0, "alpha alpha", {}),
            ("a.md", 1, "beta beta", {}),
        ],
        embedder=fake_embed,
    )
    # Query is an alpha vector; alpha row should rank higher.
    hits = store.search(
        "irrelevant fts",  # ignored in vector mode
        mode="vector",
        query_vector=[1.0, 0.0],
        limit=5,
    )
    assert len(hits) >= 1
    assert hits[0]["chunk_index"] == 0  # alpha row
    assert hits[0]["score"] >= 0.99


def test_search_hybrid_mode_blends_both_signals(tmp_path):
    store = Store(tmp_path / "kb.db")
    store.upsert_source("t", "markdown")

    def fake_embed(text: str) -> list[float]:
        t = text.lower()
        return [1.0 if "alpha" in t else 0.0, 1.0 if "beta" in t else 0.0]

    store.replace_chunks(
        "t",
        [
            ("a.md", 0, "alpha alpha cultivation note", {}),
            ("a.md", 1, "beta beta cultivation note", {}),
            ("a.md", 2, "no keywords here", {}),
        ],
        embedder=fake_embed,
    )
    # Hybrid query: FTS should find both alpha and beta rows;
    # vector should prefer the alpha row.
    hits = store.search(
        "cultivation",
        mode="hybrid",
        query_vector=[1.0, 0.0],
        limit=5,
    )
    assert len(hits) >= 2
    # Top hit should be the alpha row — wins on both signals.
    assert hits[0]["chunk_index"] == 0


def test_search_vector_rejects_missing_query_vector(tmp_path):
    store = Store(tmp_path / "kb.db")
    store.upsert_source("t", "markdown")
    store.replace_chunks("t", [("a.md", 0, "x", {})])
    with pytest.raises(ValueError):
        store.search("x", mode="vector")
    with pytest.raises(ValueError):
        store.search("x", mode="hybrid")


def test_get_chunk_exact_lookup(tmp_path):
    store = Store(tmp_path / "kb.db")
    store.upsert_source("t", "markdown")
    store.replace_chunks(
        "t",
        [("a.md", 0, "first", {"k": "v"}), ("a.md", 1, "second", {})],
    )
    chunk = store.get_chunk(source="t", path="a.md", chunk_index=1)
    assert chunk is not None
    assert chunk["content"] == "second"
    assert chunk["metadata"] == {}

    assert store.get_chunk(source="t", path="a.md", chunk_index=99) is None
    assert store.get_chunk(source="missing", path="a.md", chunk_index=0) is None


def test_embeddings_unconfigured_returns_none(monkeypatch):
    import knowledge_lake.embeddings as emb
    monkeypatch.delenv("CROWE_KB_EMBED_PROVIDER", raising=False)
    monkeypatch.delenv("AZURE_CORE_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_CORE_API_KEY", raising=False)
    assert emb.is_configured() is False
    assert emb.embed_text("anything") is None


def test_ingest_all_runs_each_registered_source(tmp_path, monkeypatch):
    """The CLI invokes the click command end-to-end against a fake
    KNOWN_SOURCES + isolated DB.
    """
    from click.testing import CliRunner
    from knowledge_lake import sources as sources_mod

    # Two ready sources + one missing-root, in an isolated registry.
    md_root = tmp_path / "md-corpus"
    md_root.mkdir()
    (md_root / "a.md").write_text("# Heading\n\nMycelium colonizes substrate.\n", encoding="utf-8")

    jsonl_root = tmp_path / "jsonl-corpus"
    jsonl_root.mkdir()
    (jsonl_root / "c.jsonl").write_text('{"text": "Pleurotus fruits at 18C."}\n', encoding="utf-8")

    fake = {
        "md-test": Source(name="md-test", kind="markdown", root=md_root, description=""),
        "jsonl-test": Source(name="jsonl-test", kind="jsonl", root=jsonl_root, description=""),
        "missing-test": Source(name="missing-test", kind="markdown", root=tmp_path / "nope", description=""),
    }
    monkeypatch.setattr(sources_mod, "KNOWN_SOURCES", fake)
    monkeypatch.setattr("knowledge_lake.KNOWN_SOURCES", fake, raising=False)
    monkeypatch.setenv("CROWE_KB_DB", str(tmp_path / "kb.db"))

    from cli.crowe_logic import main
    runner = CliRunner()
    result = runner.invoke(main, ["kb", "ingest-all", "--json"])
    assert result.exit_code == 0, result.output

    import json as _json
    payload = _json.loads(result.output)
    assert payload["ingested"] == 2
    assert payload["failed"] == 1
    by_name = {r["source"]: r for r in payload["sources"]}
    assert by_name["md-test"]["ok"] is True
    assert by_name["jsonl-test"]["ok"] is True
    assert by_name["missing-test"]["ok"] is False
    assert "root missing" in by_name["missing-test"]["reason"]
