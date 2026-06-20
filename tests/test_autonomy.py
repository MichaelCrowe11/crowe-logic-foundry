"""Tests for the autonomy gate (cli/autonomy.py). Pure logic.

pytest tests/test_autonomy.py -v
"""

from __future__ import annotations

import pytest

from cli.autonomy import (
    AUTONOMY_LEVELS,
    classify_tool,
    tool_allowed,
    filter_tools,
    SPEC_SYSTEM_PROMPT,
)


SAMPLE_MAP = {
    "read_file": lambda: None,
    "grep_search": lambda: None,
    "write_file": lambda: None,
    "edit_file": lambda: None,
    "execute_shell": lambda: None,
    "git_commit": lambda: None,
    "crowe_grow_log": lambda: None,  # unknown/domain -> treated as execute
}


class TestClassify:
    def test_reads_edits_and_execute(self):
        assert classify_tool("read_file") == "read"
        assert classify_tool("write_file") == "edit"
        assert classify_tool("execute_shell") == "execute"

    def test_unknown_tool_is_fail_closed_execute(self):
        assert classify_tool("crowe_grow_log") == "execute"
        assert classify_tool("totally_made_up") == "execute"


class TestToolAllowed:
    def test_read_only_allows_only_reads(self):
        assert tool_allowed("read_file", "read_only") is True
        assert tool_allowed("write_file", "read_only") is False
        assert tool_allowed("execute_shell", "read_only") is False
        assert tool_allowed("crowe_grow_log", "read_only") is False

    def test_edit_allows_reads_and_edits_not_execute(self):
        assert tool_allowed("read_file", "edit") is True
        assert tool_allowed("write_file", "edit") is True
        assert tool_allowed("execute_shell", "edit") is False

    def test_execute_allows_actions_including_unknown(self):
        assert tool_allowed("execute_shell", "execute") is True
        assert tool_allowed("crowe_grow_log", "execute") is True

    def test_full_allows_everything(self):
        for name in SAMPLE_MAP:
            assert tool_allowed(name, "full") is True

    def test_unknown_level_raises(self):
        with pytest.raises(ValueError):
            tool_allowed("read_file", "banana")


class TestFilterTools:
    def test_read_only_filters_to_reads(self):
        out = filter_tools(SAMPLE_MAP, "read_only")
        assert set(out) == {"read_file", "grep_search"}

    def test_edit_adds_file_writers(self):
        out = filter_tools(SAMPLE_MAP, "edit")
        assert set(out) == {"read_file", "grep_search", "write_file", "edit_file"}

    def test_execute_includes_everything_actionable(self):
        out = filter_tools(SAMPLE_MAP, "execute")
        assert set(out) == set(SAMPLE_MAP)  # all sample tools are <= execute

    def test_full_is_passthrough_copy(self):
        out = filter_tools(SAMPLE_MAP, "full")
        assert set(out) == set(SAMPLE_MAP)
        assert out is not SAMPLE_MAP  # a copy, not the original


class TestSpecPrompt:
    def test_spec_prompt_states_read_only_and_no_code(self):
        assert "read-only" in SPEC_SYSTEM_PROMPT.lower()
        assert "Specification Mode" in SPEC_SYSTEM_PROMPT
        assert "read_only" in AUTONOMY_LEVELS
