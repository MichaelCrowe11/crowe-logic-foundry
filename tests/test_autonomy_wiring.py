"""Wiring test: the live tool map (cli.crowe_logic._get_tool_map) honors the
autonomy level. Imports the real registry (no network). Resets to 'full' after
so the module global does not leak into other tests.

    pytest tests/test_autonomy_wiring.py -v
"""

from __future__ import annotations


def test_get_tool_map_restricts_under_read_only():
    from cli import crowe_logic

    try:
        crowe_logic._set_autonomy("read_only")
        m = crowe_logic._get_tool_map()
        assert "read_file" in m  # a safe read survives
        assert "write_file" not in m  # file write blocked
        assert "execute_shell" not in m  # shell blocked
    finally:
        crowe_logic._set_autonomy("full")

    full = crowe_logic._get_tool_map()
    assert "write_file" in full and "execute_shell" in full  # full = unrestricted


def test_invalid_autonomy_level_rejected():
    import pytest

    from cli import crowe_logic

    with pytest.raises(ValueError):
        crowe_logic._set_autonomy("banana")
