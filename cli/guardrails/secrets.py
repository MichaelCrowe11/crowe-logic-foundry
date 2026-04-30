"""
SecretScrubber: redact credentials before they reach the renderer.

Two modes:
    scrub_block(text)  - one-shot scan of an accumulated buffer.
    StreamScrubber     - online scrubber for token streams; holds back a tail
                         buffer so partial matches at the chunk boundary cannot
                         leak.

The pattern catalog covers the credentials this codebase actually issues or
consumes. Adding a new shape requires extending PATTERNS plus a test.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

# Longest known credential pattern, used to size the streaming hold-back buffer.
# 256 chars covers GitHub fine-grained PATs (~93) and any reasonable JWT stub.
_HOLDBACK_CHARS = 256

# Each entry is (label, compiled regex). Anchors are intentionally permissive:
# we want recall, false positives are acceptable for redaction (the user can
# always disable a pattern; a leaked key cannot be unleaked).
_PATTERN_SPECS: list[tuple[str, str]] = [
    # Resend (this is the exact shape that leaked in the 2026-04-30 Eclipse session)
    ("resend", r"re_[A-Za-z0-9_]{20,}"),
    # Anthropic listed BEFORE openai because both start with sk-; without this
    # ordering, the openai regex matches sk-ant- prefixes and mislabels them.
    ("anthropic", r"sk-ant-(?:api[0-9]+-)?[A-Za-z0-9_\-]{20,}"),
    # Stripe variants listed BEFORE openai for the same prefix-collision reason.
    ("stripe_live", r"sk_live_[A-Za-z0-9]{20,}"),
    ("stripe_test", r"sk_test_[A-Za-z0-9]{20,}"),
    ("stripe_restricted", r"rk_(?:live|test)_[A-Za-z0-9]{20,}"),
    # OpenAI / xAI
    ("openai", r"sk-(?!ant-)(?:proj-|live-|test-)?[A-Za-z0-9_\-]{20,}"),
    ("xai", r"xai-[A-Za-z0-9]{20,}"),
    # GitHub: classic PAT, fine-grained, OAuth, app
    ("github_pat", r"github_pat_[A-Za-z0-9_]{20,}"),
    ("github_classic", r"ghp_[A-Za-z0-9]{30,}"),
    ("github_oauth", r"gho_[A-Za-z0-9]{30,}"),
    ("github_app_user", r"ghu_[A-Za-z0-9]{30,}"),
    ("github_app_server", r"ghs_[A-Za-z0-9]{30,}"),
    ("github_refresh", r"ghr_[A-Za-z0-9]{30,}"),
    # AWS access keys
    ("aws_akid", r"AKIA[0-9A-Z]{16}"),
    ("aws_asia", r"ASIA[0-9A-Z]{16}"),
    # Hugging Face
    ("hf", r"hf_[A-Za-z0-9]{30,}"),
    # Slack
    ("slack_bot", r"xoxb-[A-Za-z0-9-]{20,}"),
    ("slack_user", r"xoxp-[A-Za-z0-9-]{20,}"),
    # Google API key
    ("google_api", r"AIza[0-9A-Za-z\-_]{35}"),
    # NVIDIA NIM
    ("nvidia", r"nvapi-[A-Za-z0-9_\-]{40,}"),
    # Generic JWT (header.payload.signature) - keep last so more-specific keys
    # match first.
    ("jwt", r"eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
]

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (label, re.compile(pat)) for label, pat in _PATTERN_SPECS
]


@dataclass(frozen=True)
class SecretHit:
    """A single redaction event."""

    label: str
    start: int
    end: int
    redacted_with: str


def _redaction_for(label: str, original: str) -> str:
    """Produce a deterministic redaction marker that preserves shape hints."""
    suffix = original[-4:] if len(original) > 8 else ""
    tail = f"...{suffix}" if suffix else ""
    return f"[REDACTED:{label}{tail}]"


class SecretScrubber:
    """Scan a block of text for credentials and redact them in place.

    Designed for the post-stream final scrub. Use StreamScrubber for live
    token streams.
    """

    def __init__(self, patterns: Iterable[tuple[str, re.Pattern[str]]] = _PATTERNS):
        self._patterns = list(patterns)

    def scrub(self, text: str) -> tuple[str, list[SecretHit]]:
        """Return (cleaned_text, hits). Cleaned text is safe to render."""
        if not text:
            return text, []
        hits: list[SecretHit] = []
        # Apply each pattern in order. We rebuild the string after each pass
        # so positions in `hits` reference the original text via re.finditer.
        cleaned = text
        offset_map: list[int] = []  # not currently used; reserved for future caret math
        for label, pattern in self._patterns:
            new_parts: list[str] = []
            cursor = 0
            for match in pattern.finditer(cleaned):
                start, end = match.span()
                original = match.group(0)
                replacement = _redaction_for(label, original)
                new_parts.append(cleaned[cursor:start])
                new_parts.append(replacement)
                hits.append(
                    SecretHit(label=label, start=start, end=end, redacted_with=replacement)
                )
                cursor = end
            new_parts.append(cleaned[cursor:])
            cleaned = "".join(new_parts)
        return cleaned, hits


class StreamScrubber:
    """Online secret scrubber for token streams.

    Holds back the trailing _HOLDBACK_CHARS so that a credential split across
    chunk boundaries cannot leak. Call `feed(chunk)` for each token, then
    `flush()` once the stream ends.

    Usage:
        scrubber = StreamScrubber()
        for chunk in provider_stream:
            safe = scrubber.feed(chunk)
            if safe:
                renderer.write(safe)
        renderer.write(scrubber.flush())
    """

    def __init__(
        self,
        block_scrubber: SecretScrubber | None = None,
        holdback_chars: int = _HOLDBACK_CHARS,
    ):
        self._block = block_scrubber or SecretScrubber()
        self._holdback = holdback_chars
        self._buffer = ""
        self._hits: list[SecretHit] = []

    @property
    def hits(self) -> list[SecretHit]:
        return list(self._hits)

    def feed(self, chunk: str) -> str:
        """Append chunk to buffer. Return the prefix that is safe to emit."""
        if not chunk:
            return ""
        self._buffer += chunk
        if len(self._buffer) <= self._holdback:
            return ""
        # Everything older than holdback is safe to scan-and-emit.
        safe_len = len(self._buffer) - self._holdback
        head = self._buffer[:safe_len]
        self._buffer = self._buffer[safe_len:]
        cleaned, hits = self._block.scrub(head)
        self._hits.extend(hits)
        return cleaned

    def flush(self) -> str:
        """Scrub and return any remaining buffered content."""
        if not self._buffer:
            return ""
        cleaned, hits = self._block.scrub(self._buffer)
        self._hits.extend(hits)
        self._buffer = ""
        return cleaned
