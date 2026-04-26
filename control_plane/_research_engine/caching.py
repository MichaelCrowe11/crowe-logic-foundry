# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Research Engine, proprietary and private.

"""Prompt caching helpers.

Anthropic's prompt-cache API uses `cache_control` blocks. When you pass the
system prompt as a list of text blocks, you can place a `cache_control`
entry on any block. The block with `cache_control` (and all blocks before
it) forms the cache prefix.

We put exactly one `cache_control` entry on the last non-empty block of
the system prompt, which caches the whole system section.
"""

from __future__ import annotations

from typing import Any


def build_cached_system(
    labeled_blocks: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    """Build a list of system blocks with `cache_control` on the last one.

    Parameters
    ----------
    labeled_blocks
        Ordered list of (label, text) pairs. Labels are advisory only; only
        text is sent to the API. Empty text blocks are dropped.

    Returns
    -------
    list[dict]
        Blocks in the format the Anthropic SDK expects for `system=[...]`.
    """
    cleaned = [(label, text) for label, text in labeled_blocks if text]
    if not cleaned:
        return []
    out: list[dict[str, Any]] = []
    for i, (_, text) in enumerate(cleaned):
        block: dict[str, Any] = {"type": "text", "text": text}
        if i == len(cleaned) - 1:
            block["cache_control"] = {"type": "ephemeral"}
        out.append(block)
    return out
