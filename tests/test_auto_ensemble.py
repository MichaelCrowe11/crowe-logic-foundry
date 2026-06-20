"""Auto-ensemble policy + tool-cache wiring tests.

The policy tests are pure. The wiring tests import cli.crowe_logic and exercise
the real _execute_tool_call to prove the cache actually dedupes (no network).

    pytest tests/test_auto_ensemble.py -v
"""

from __future__ import annotations

from cli.ensemble import should_auto_ensemble, selectors_from_decision


class FakeDecision:
    def __init__(self, label, companions):
        self.selected_label = label
        self.companions = companions


class TestAutoEnsemblePolicy:
    def test_requires_both_enabled_and_companions(self):
        d = FakeDecision("Apex", ({"label": "DeepParallel"},))
        assert should_auto_ensemble(d, enabled=True) is True
        assert should_auto_ensemble(d, enabled=False) is False

    def test_no_companions_means_no_ensemble(self):
        assert should_auto_ensemble(FakeDecision("Apex", ()), enabled=True) is False

    def test_selectors_are_primary_then_companions_in_order(self):
        d = FakeDecision("Apex", ({"label": "DeepParallel"}, {"name": "titan"}))
        assert selectors_from_decision(d) == ["Apex", "DeepParallel", "titan"]


class TestToolCacheWiring:
    def test_pure_read_is_deduped_within_a_turn(self):
        from cli import crowe_logic

        crowe_logic._reset_tool_cache()
        calls = {"n": 0}

        def read_thing(path):
            calls["n"] += 1
            return f"body-{calls['n']}"

        tool_map = {"read_thing": read_thing}
        r1 = crowe_logic._execute_tool_call(tool_map, "read_thing", '{"path": "/a"}')
        r2 = crowe_logic._execute_tool_call(tool_map, "read_thing", '{"path": "/a"}')
        assert r1 == r2  # second call served from cache
        assert calls["n"] == 1  # underlying function ran exactly once

    def test_stateful_tool_is_not_deduped(self):
        from cli import crowe_logic

        crowe_logic._reset_tool_cache()
        calls = {"n": 0}

        def execute_shell(cmd):
            calls["n"] += 1
            return "out"

        tool_map = {"execute_shell": execute_shell}
        crowe_logic._execute_tool_call(tool_map, "execute_shell", '{"cmd": "ls"}')
        crowe_logic._execute_tool_call(tool_map, "execute_shell", '{"cmd": "ls"}')
        assert calls["n"] == 2  # stateful tools always re-run

    def test_new_turn_clears_cache(self):
        from cli import crowe_logic

        crowe_logic._reset_tool_cache()
        calls = {"n": 0}

        def read_thing(path):
            calls["n"] += 1
            return f"body-{calls['n']}"

        tool_map = {"read_thing": read_thing}
        crowe_logic._execute_tool_call(tool_map, "read_thing", '{"path": "/a"}')
        crowe_logic._reset_tool_cache()  # next user turn
        crowe_logic._execute_tool_call(tool_map, "read_thing", '{"path": "/a"}')
        assert calls["n"] == 2  # cache cleared between turns
