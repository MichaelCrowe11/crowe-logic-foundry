"""Schema-side autonomy filtering: filter_functions / filter_schemas / the
active-level state, plus a wiring test proving providers._shared.load_tools()
hides forbidden tools from the model's schema under a restricted level.

    pytest tests/test_autonomy_schema.py -v
"""

from __future__ import annotations

import pytest

from cli.autonomy import (
    filter_functions,
    filter_schemas,
    get_active_level,
    set_active_level,
)


def read_file():
    pass


def write_file():
    pass


def execute_shell():
    pass


class TestFilterFunctions:
    def test_read_only_keeps_only_reads(self):
        out = filter_functions([read_file, write_file, execute_shell], "read_only")
        assert {f.__name__ for f in out} == {"read_file"}

    def test_full_is_passthrough(self):
        funcs = [read_file, write_file]
        assert filter_functions(funcs, "full") == funcs


class TestFilterSchemas:
    SCHEMAS = [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "write_file"}},
        {"type": "function", "function": {"name": "execute_shell"}},
    ]

    def test_read_only_filters_by_schema_name(self):
        out = filter_schemas(self.SCHEMAS, "read_only")
        assert [s["function"]["name"] for s in out] == ["read_file"]

    def test_edit_keeps_reads_and_edits(self):
        out = filter_schemas(self.SCHEMAS, "edit")
        assert [s["function"]["name"] for s in out] == ["read_file", "write_file"]


class TestActiveLevel:
    def test_set_get_roundtrip_and_reset(self):
        try:
            set_active_level("edit")
            assert get_active_level() == "edit"
        finally:
            set_active_level("full")
        assert get_active_level() == "full"

    def test_invalid_level_raises(self):
        with pytest.raises(ValueError):
            set_active_level("nope")


class TestLoadToolsWiring:
    def test_load_tools_schema_honors_active_level(self):
        from providers._shared import load_tools

        try:
            set_active_level("read_only")
            schemas, name_map = load_tools()
            names = {s["function"]["name"] for s in schemas}
            assert "read_file" in name_map  # a safe read is visible
            assert "write_file" not in name_map  # hidden from execution map
            assert "write_file" not in names  # AND hidden from the model schema
            assert "execute_shell" not in names
        finally:
            set_active_level("full")

        schemas2, map2 = load_tools()
        assert "write_file" in map2  # full level = unrestricted again
