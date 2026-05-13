"""Streaming-time em-dash strip in providers._shared.

The batched ``cli.guardrails.style.StyleEnforcer`` runs after the full turn,
so any em-dashes that reached the renderer during streaming have already
been shown to the user. ``_strip_em_dash`` plugs that gap by cleaning each
chunk before it hits ``renderer.feed()``.
"""

from __future__ import annotations

from providers._shared import (
    _InlineReasoningSplitter,
    _dispatch_content,
    _flush_content,
    _strip_em_dash,
)


class _CapturingRenderer:
    """Records every ``feed`` / ``feed_reasoning`` call so tests can inspect."""

    def __init__(self) -> None:
        self.content: list[str] = []
        self.reasoning: list[str] = []

    def feed(self, piece: str) -> None:
        self.content.append(piece)

    def feed_reasoning(self, piece: str) -> None:
        self.reasoning.append(piece)


def test_strip_em_dash_with_surrounding_space() -> None:
    assert _strip_em_dash("before — after") == "before ,  after"


def test_strip_em_dash_no_surrounding_space() -> None:
    assert _strip_em_dash("before—after") == "before, after"


def test_strip_em_dash_multiple_occurrences() -> None:
    assert _strip_em_dash("a—b—c") == "a, b, c"


def test_strip_em_dash_empty_input() -> None:
    assert _strip_em_dash("") == ""


def test_strip_em_dash_no_match_returns_same_object() -> None:
    """Fast path: identity-preserving when there is nothing to do."""
    text = "perfectly normal text with no em dashes"
    assert _strip_em_dash(text) is text


def test_dispatch_strips_em_dash_in_content() -> None:
    renderer = _CapturingRenderer()
    splitter = _InlineReasoningSplitter()
    _dispatch_content(renderer, splitter, "scaling commercial — go big")
    _flush_content(renderer, splitter)
    out = "".join(renderer.content)
    assert "—" not in out
    assert "go big" in out


def test_dispatch_preserves_reasoning_em_dash() -> None:
    """Em-dashes inside ``<think>`` blocks belong to the model's reasoning
    trace, which is internal and not customer-facing. The strip only applies
    to the content channel.
    """
    renderer = _CapturingRenderer()
    splitter = _InlineReasoningSplitter()
    _dispatch_content(renderer, splitter, "<think>weighing X — Y</think>final answer")
    _flush_content(renderer, splitter)
    assert "—" in "".join(renderer.reasoning)
    assert "—" not in "".join(renderer.content)


def test_dispatch_handles_em_dash_at_chunk_boundary() -> None:
    """When the em-dash arrives alone in a later chunk, the per-chunk
    replace still catches it. Python strings are unicode so the boundary
    concern is at the byte/decode layer above this function.
    """
    renderer = _CapturingRenderer()
    splitter = _InlineReasoningSplitter()
    _dispatch_content(renderer, splitter, "first piece ")
    _dispatch_content(renderer, splitter, "— second piece")
    _flush_content(renderer, splitter)
    out = "".join(renderer.content)
    assert "—" not in out
    assert "first piece" in out
    assert "second piece" in out


def test_flush_drains_pending_buffer_and_strips() -> None:
    """The splitter holds a partial buffer until terminators land; the
    final flush still needs to strip em-dashes from whatever it emits.
    """
    renderer = _CapturingRenderer()
    splitter = _InlineReasoningSplitter()
    _dispatch_content(renderer, splitter, "tail content—end")
    _flush_content(renderer, splitter)
    out = "".join(renderer.content)
    assert "—" not in out
    assert "tail content" in out
    assert "end" in out
