"""Tests for cli.tool_cache."""
from __future__ import annotations

import time

import pytest

from cli.tool_cache import STATEFUL_TOOLS, CacheDecision, ToolCache


@pytest.fixture
def cache() -> ToolCache:
    c = ToolCache()
    c.start_turn()
    return c


def test_first_call_is_a_miss(cache: ToolCache) -> None:
    decision = cache.lookup("read_file", {"path": "/tmp/x"})
    assert not decision.cached


def test_second_identical_call_is_a_hit(cache: ToolCache) -> None:
    cache.record("read_file", {"path": "/tmp/x"}, "contents")
    decision = cache.lookup("read_file", {"path": "/tmp/x"})
    assert decision.cached
    assert decision.result == "contents"
    assert cache.dedup_count == 1


def test_talon_2026_04_30_duplicate_deepparallel_call_is_NOT_cached(cache: ToolCache) -> None:
    """deepparallel_query is in STATEFUL_TOOLS because chain order matters.

    The Talon transcript called the same query twice. We cannot dedupe it
    because each invocation produces a different reasoning chain. But the
    cache should NOT silently return the first result either.
    """
    cache.record("deepparallel_query", {"prompt": "What is 2+2?"}, "first run")
    decision = cache.lookup("deepparallel_query", {"prompt": "What is 2+2?"})
    assert not decision.cached, "stateful tools must not cache"


def test_different_args_are_separate_keys(cache: ToolCache) -> None:
    cache.record("read_file", {"path": "/tmp/a"}, "A")
    cache.record("read_file", {"path": "/tmp/b"}, "B")
    assert cache.lookup("read_file", {"path": "/tmp/a"}).result == "A"
    assert cache.lookup("read_file", {"path": "/tmp/b"}).result == "B"


def test_whitespace_in_args_is_normalized(cache: ToolCache) -> None:
    cache.record("read_file", {"path": "/tmp/x"}, "value")
    decision = cache.lookup("read_file", {"path": "  /tmp/x  "})
    assert decision.cached


def test_arg_order_does_not_matter(cache: ToolCache) -> None:
    cache.record("rpc", {"a": 1, "b": 2}, "result")
    decision = cache.lookup("rpc", {"b": 2, "a": 1})
    assert decision.cached


def test_stateful_tools_never_cached(cache: ToolCache) -> None:
    for tool in ["Write", "Edit", "Bash", "execute_shell", "azure_agent_invoke"]:
        assert tool in STATEFUL_TOOLS
        cache.record(tool, {"x": 1}, "should not be saved")
        decision = cache.lookup(tool, {"x": 1})
        assert not decision.cached, f"{tool} must not cache"


def test_start_turn_clears_cache(cache: ToolCache) -> None:
    cache.record("read_file", {"path": "/x"}, "v")
    assert cache.lookup("read_file", {"path": "/x"}).cached
    cache.start_turn()
    assert not cache.lookup("read_file", {"path": "/x"}).cached


def test_dedup_counter_resets_per_turn(cache: ToolCache) -> None:
    cache.record("read_file", {"path": "/x"}, "v")
    cache.lookup("read_file", {"path": "/x"})  # +1
    cache.lookup("read_file", {"path": "/x"})  # +1
    assert cache.dedup_count == 2
    cache.start_turn()
    assert cache.dedup_count == 0


def test_age_seconds_set_on_hit(cache: ToolCache) -> None:
    cache.record("read_file", {"path": "/x"}, "v")
    time.sleep(0.01)
    decision = cache.lookup("read_file", {"path": "/x"})
    assert decision.cached
    assert decision.age_seconds > 0


def test_none_args_treated_as_empty_dict(cache: ToolCache) -> None:
    cache.record("ping", None, "pong")
    decision = cache.lookup("ping", None)
    assert decision.cached
    decision = cache.lookup("ping", {})
    assert decision.cached
