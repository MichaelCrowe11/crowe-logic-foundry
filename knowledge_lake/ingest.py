# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
"""
Ingestor framework + the markdown implementation.

Each Ingestor walks a Source's root, yields (path, idx, text, metadata)
tuples, and hands them to the Store. The base class enforces:
  - root-exists check (caller-friendly error)
  - chunking strategy (paragraph blocks, soft cap at ~1200 chars)
  - per-file metadata (relative path, size, mtime)

Adding a new corpus type (latex, jsonl, html, ...) is a subclass that
overrides `_iter_files` and `_extract_text`.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from knowledge_lake.sources import Source
from knowledge_lake.store import Store


# Soft target. Paragraphs are kept intact when possible — splitting
# only kicks in if a single block is longer than this.
_CHUNK_TARGET_CHARS = 1200
# Hard floor: micro-chunks (< this many chars) get folded into the
# next block so FTS hits aren't on three-word fragments.
_CHUNK_MIN_CHARS = 200


@dataclass(frozen=True)
class IngestStats:
    source: str
    files_seen: int
    files_ingested: int
    chunks_written: int
    skipped_paths: list[str]


def _chunk_paragraphs(text: str) -> list[str]:
    """Paragraph-aware splitter. Folds small adjacent paragraphs
    together until the running chunk hits the target size, then
    flushes. A single oversize paragraph gets split on sentence-ish
    boundaries as a fallback.
    """
    # Normalize Windows line endings + collapse 3+ blank lines.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    buf: list[str] = []
    buflen = 0

    def flush() -> None:
        nonlocal buf, buflen
        if buf:
            chunks.append("\n\n".join(buf).strip())
            buf = []
            buflen = 0

    for para in paragraphs:
        if len(para) > _CHUNK_TARGET_CHARS:
            flush()
            # Sentence-ish split on '. ' / '! ' / '? '. Greedy
            # accumulator inside the long paragraph.
            sentences = re.split(r"(?<=[.!?])\s+", para)
            sub_buf: list[str] = []
            sub_len = 0
            for s in sentences:
                if sub_len + len(s) > _CHUNK_TARGET_CHARS and sub_buf:
                    chunks.append(" ".join(sub_buf).strip())
                    sub_buf = [s]
                    sub_len = len(s)
                else:
                    sub_buf.append(s)
                    sub_len += len(s) + 1
            if sub_buf:
                chunks.append(" ".join(sub_buf).strip())
            continue
        if buflen + len(para) > _CHUNK_TARGET_CHARS and buflen >= _CHUNK_MIN_CHARS:
            flush()
        buf.append(para)
        buflen += len(para) + 2  # account for the \n\n joiner
    flush()
    return [c for c in chunks if c]


class Ingestor:
    """Base class. Subclasses override `_iter_files` and `_extract_text`."""

    def __init__(self, store: Store, source: Source) -> None:
        self.store = store
        self.source = source

    # ─── hooks ─────────────────────────────────────────────────

    def _iter_files(self) -> Iterator[Path]:
        raise NotImplementedError

    def _extract_text(self, path: Path) -> str:
        raise NotImplementedError

    # ─── orchestration ─────────────────────────────────────────

    def run(self) -> IngestStats:
        if not self.source.root.exists():
            raise FileNotFoundError(
                f"Source root not found: {self.source.root}. "
                f"Clone the corpus or update sources.py."
            )
        self.store.upsert_source(
            self.source.name,
            self.source.kind,
            root=str(self.source.root),
            description=self.source.description,
        )

        files_seen = 0
        files_ingested = 0
        skipped: list[str] = []
        chunks: list[tuple[str, int, str, dict]] = []

        for path in self._iter_files():
            files_seen += 1
            try:
                text = self._extract_text(path)
            except Exception as exc:  # noqa: BLE001
                skipped.append(f"{path}: {type(exc).__name__}: {exc}")
                continue
            blocks = _chunk_paragraphs(text)
            if not blocks:
                skipped.append(f"{path}: empty after chunking")
                continue
            try:
                rel = str(path.relative_to(self.source.root))
            except ValueError:
                rel = str(path)
            stat = path.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
            for idx, content in enumerate(blocks):
                chunks.append((rel, idx, content, {
                    "size": stat.st_size,
                    "mtime": mtime,
                    "ext": path.suffix.lstrip("."),
                }))
            files_ingested += 1

        n_written = self.store.replace_chunks(self.source.name, chunks)
        return IngestStats(
            source=self.source.name,
            files_seen=files_seen,
            files_ingested=files_ingested,
            chunks_written=n_written,
            skipped_paths=skipped,
        )


# ─── markdown ──────────────────────────────────────────────────

class MarkdownIngestor(Ingestor):
    """Reads .md (and .markdown) files. Strips YAML frontmatter and
    common-prefix `> ` quotes. Anything else is passed through to the
    paragraph splitter.
    """

    _FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)

    def _iter_files(self) -> Iterator[Path]:
        # If the source declared include_globs, prefer those (precise).
        # Otherwise default to .md / .markdown anywhere under root.
        seen: set[Path] = set()
        if self.source.include_globs:
            for pattern in self.source.include_globs:
                for p in sorted(self.source.root.glob(pattern)):
                    if p.is_file() and p not in seen:
                        seen.add(p)
                        if not self._is_excluded(p):
                            yield p
        else:
            for ext in ("md", "markdown"):
                for p in sorted(self.source.root.rglob(f"*.{ext}")):
                    if p.is_file() and p not in seen:
                        seen.add(p)
                        if not self._is_excluded(p):
                            yield p

    def _is_excluded(self, path: Path) -> bool:
        rel = path.relative_to(self.source.root)
        rel_str = str(rel)
        for pattern in self.source.exclude_globs:
            # Path.match doesn't handle ** the way globs do — emulate.
            if _glob_match(rel_str, pattern):
                return True
        return False

    def _extract_text(self, path: Path) -> str:
        raw = path.read_text(encoding="utf-8", errors="replace")
        # Strip YAML frontmatter.
        raw = self._FRONTMATTER.sub("", raw, count=1)
        return raw


def _glob_match(rel: str, pattern: str) -> bool:
    """Glob match that respects `**` as `match any sub-path including slashes`."""
    import fnmatch
    # `**/x` and `x/**` and `a/**/b`. Convert the recursive form to a
    # regex so fnmatch-style ** works.
    regex = re.escape(pattern)
    regex = regex.replace(r"\*\*/", "(?:.*/)?")
    regex = regex.replace(r"/\*\*", "(?:/.*)?")
    regex = regex.replace(r"\*\*", ".*")
    regex = regex.replace(r"\*", "[^/]*")
    regex = regex.replace(r"\?", ".")
    return bool(re.fullmatch(regex, rel))


# ─── dispatch table ────────────────────────────────────────────

_KINDS: dict[str, type[Ingestor]] = {
    "markdown": MarkdownIngestor,
}


def ingestor_for(source: Source, store: Store) -> Ingestor:
    """Return the right Ingestor for a registered source's kind, or
    raise NotImplementedError so the CLI can surface "ingestor not
    available yet for kind=latex" instead of crashing.
    """
    cls = _KINDS.get(source.kind)
    if cls is None:
        raise NotImplementedError(
            f"No ingestor implemented for kind={source.kind!r} "
            f"(source={source.name!r}). Add one to knowledge_lake/ingest.py."
        )
    return cls(store, source)
