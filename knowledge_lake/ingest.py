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

        n_written = self.store.replace_chunks(
            self.source.name, chunks, embedder=_get_embedder(),
        )
        return IngestStats(
            source=self.source.name,
            files_seen=files_seen,
            files_ingested=files_ingested,
            chunks_written=n_written,
            skipped_paths=skipped,
        )


def _get_embedder():
    """Return the configured embedder callable, or None if no
    provider is configured. Lazy import so tests that don't touch
    embeddings don't pay the import cost.
    """
    try:
        from knowledge_lake.embeddings import embed_text, is_configured
    except ImportError:
        return None
    if not is_configured():
        return None
    return embed_text


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


# ─── latex ─────────────────────────────────────────────────────

class LatexIngestor(Ingestor):
    """Reads .tex files. Strips common formatting macros and comments,
    chunks by \\section / \\subsection boundaries, falls back to
    paragraph chunking when a section is longer than the chunk target.

    This is deliberately a syntactic pass, not a full LaTeX parser:
    the goal is to surface searchable English text, not preserve
    typography. Math is kept verbatim ($...$ and \\[...\\] survive).
    """

    # Preamble runs from the file start to \\begin{document}. We drop
    # it entirely — \\usepackage, \\title, \\author etc. add noise to
    # search results.
    _PREAMBLE = re.compile(r"\A.*?\\begin\{document\}", re.DOTALL)
    _END_DOC = re.compile(r"\\end\{document\}.*\Z", re.DOTALL)

    # Single-line LaTeX comments. % is the comment marker unless
    # escaped (\%). We don't try to handle the escape — the strip is
    # an approximation, not a parser.
    _COMMENT = re.compile(r"(?<!\\)%[^\n]*")

    # Section heading markers. Order matters for the splitter: chapter
    # outranks section outranks subsection.
    _SECTION = re.compile(
        r"\\(chapter|section|subsection|subsubsection)\*?\{([^}]+)\}"
    )

    # Macros we replace with their visible argument (e.g. \textbf{x} -> x).
    # The set is small on purpose; unknown macros are passed through.
    _VISIBLE_ARG_MACROS = (
        "textbf", "textit", "emph", "underline", "texttt", "textsc",
        "textsf", "textrm", "uppercase", "lowercase",
    )
    _VISIBLE_RE = re.compile(
        r"\\(" + "|".join(_VISIBLE_ARG_MACROS) + r")\{([^{}]*)\}"
    )

    # Macros to drop entirely with their args (cite/ref/label/index/etc).
    _DROP_WITH_ARG = (
        "cite", "citep", "citet", "ref", "eqref", "label", "index",
        "footnote", "bibliography", "bibliographystyle",
    )
    _DROP_RE = re.compile(
        r"\\(" + "|".join(_DROP_WITH_ARG) + r")\{[^{}]*\}"
    )

    # Bare control sequences with no arg that we just want gone.
    _BARE_DROP = re.compile(r"\\(maketitle|tableofcontents|newpage|clearpage|noindent|par|hfill|vfill)\b")

    # \begin/\end environments — drop the markers, keep the inner text
    # so e.g. itemize lists still produce searchable content.
    _ENV_MARK = re.compile(r"\\(?:begin|end)\{[^}]+\}")

    # itemize \item markers become bullets.
    _ITEM = re.compile(r"\\item\b\s*")

    def _iter_files(self) -> Iterator[Path]:
        seen: set[Path] = set()
        if self.source.include_globs:
            patterns = self.source.include_globs
        else:
            patterns = ("*.tex", "**/*.tex")
        for pattern in patterns:
            for p in sorted(self.source.root.glob(pattern)):
                if p.is_file() and p not in seen:
                    seen.add(p)
                    if not self._is_excluded(p):
                        yield p

    def _is_excluded(self, path: Path) -> bool:
        rel = path.relative_to(self.source.root)
        rel_str = str(rel)
        for pattern in self.source.exclude_globs:
            if _glob_match(rel_str, pattern):
                return True
        return False

    def _strip(self, text: str) -> str:
        # Drop preamble entirely if \\begin{document} is present.
        text = self._PREAMBLE.sub("", text, count=1)
        text = self._END_DOC.sub("", text, count=1)
        text = self._COMMENT.sub("", text)
        # Macros that just wrap content — keep the inner text.
        # Apply twice for nested cases like \textbf{\emph{x}}.
        text = self._VISIBLE_RE.sub(lambda m: m.group(2), text)
        text = self._VISIBLE_RE.sub(lambda m: m.group(2), text)
        text = self._DROP_RE.sub("", text)
        text = self._BARE_DROP.sub("", text)
        text = self._ENV_MARK.sub("", text)
        text = self._ITEM.sub("- ", text)
        # Collapse runs of whitespace inside lines (preserve paragraph breaks).
        text = re.sub(r"[ \t]+", " ", text)
        return text.strip()

    @staticmethod
    def _heading_replacement(match: "re.Match[str]") -> str:
        level = match.group(1)
        title = match.group(2).strip()
        # Two newlines before and after force the paragraph splitter to
        # treat each section as a hard chunk boundary.
        return f"\n\n[{level}] {title}\n\n"

    def _extract_text(self, path: Path) -> str:
        raw = path.read_text(encoding="utf-8", errors="replace")
        stripped = self._strip(raw)
        # Section markers become explicit paragraph breaks so the base
        # class's `_chunk_paragraphs` naturally splits on them.
        return self._SECTION.sub(self._heading_replacement, stripped)


# ─── jsonl ─────────────────────────────────────────────────────

class JsonlIngestor(Ingestor):
    """Reads .jsonl files. Each non-empty line is a JSON object; the
    text-bearing field is configurable via Source.extras['text_field']
    (default 'text'). One record per chunk — no further splitting,
    since training-corpus records are usually already sentence- or
    paragraph-scoped.

    Other top-level fields land in chunk metadata so they can be
    surfaced via the search facade later (e.g. label, source_url,
    category).
    """

    DEFAULT_FIELD = "text"

    def _text_field(self) -> str:
        # Source.extras isn't a real attr today — defer to a per-name
        # convention. If a future Source wants a different field,
        # they can subclass JsonlIngestor and override.
        return getattr(self.source, "text_field", None) or self.DEFAULT_FIELD

    def _iter_files(self) -> Iterator[Path]:
        seen: set[Path] = set()
        patterns = self.source.include_globs or ("*.jsonl", "**/*.jsonl")
        for pattern in patterns:
            for p in sorted(self.source.root.glob(pattern)):
                if p.is_file() and p not in seen:
                    seen.add(p)
                    if not self._is_excluded(p):
                        yield p

    def _is_excluded(self, path: Path) -> bool:
        rel = path.relative_to(self.source.root)
        rel_str = str(rel)
        for pattern in self.source.exclude_globs:
            if _glob_match(rel_str, pattern):
                return True
        return False

    def _extract_text(self, path: Path) -> str:
        # JsonlIngestor doesn't actually need this — we override `run`
        # so we have direct access to per-record metadata. Provide a
        # safe fallback to satisfy the base interface.
        return ""

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

        import json as _json

        text_field = self._text_field()
        files_seen = 0
        files_ingested = 0
        skipped: list[str] = []
        chunks: list[tuple[str, int, str, dict]] = []

        for path in self._iter_files():
            files_seen += 1
            try:
                rel = str(path.relative_to(self.source.root))
            except ValueError:
                rel = str(path)
            file_had_record = False
            for idx, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines()):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = _json.loads(line)
                except _json.JSONDecodeError as exc:
                    skipped.append(f"{rel}:{idx + 1}: invalid json ({exc.msg})")
                    continue
                if not isinstance(record, dict):
                    skipped.append(f"{rel}:{idx + 1}: not a JSON object")
                    continue
                content = record.get(text_field)
                if not isinstance(content, str) or not content.strip():
                    skipped.append(f"{rel}:{idx + 1}: missing/empty field {text_field!r}")
                    continue
                meta = {k: v for k, v in record.items() if k != text_field}
                # Stringify nested values for SQLite friendliness.
                meta = {k: (v if isinstance(v, (str, int, float, bool, type(None))) else str(v))
                        for k, v in meta.items()}
                meta.setdefault("file", rel)
                chunks.append((rel, idx, content.strip(), meta))
                file_had_record = True
            if file_had_record:
                files_ingested += 1
            else:
                skipped.append(f"{rel}: zero usable records")

        n_written = self.store.replace_chunks(
            self.source.name, chunks, embedder=_get_embedder(),
        )
        return IngestStats(
            source=self.source.name,
            files_seen=files_seen,
            files_ingested=files_ingested,
            chunks_written=n_written,
            skipped_paths=skipped,
        )


# ─── dispatch table ────────────────────────────────────────────

_KINDS: dict[str, type[Ingestor]] = {
    "markdown": MarkdownIngestor,
    "latex": LatexIngestor,
    "jsonl": JsonlIngestor,
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
