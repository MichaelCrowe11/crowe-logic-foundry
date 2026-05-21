"""Tests for crowe_synapse_engine.pipeline — step execution engine."""

import json
import os
import pytest
from crowe_synapse_engine.pipeline import PipelineEngine, PipelineStep, PipelineRun


@pytest.fixture
def engine(tmp_path):
    templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "crowe_synapse_engine", "templates")
    return PipelineEngine(templates_dir=templates_dir)


def echo_tool(text: str = "") -> str:
    return json.dumps({"echoed": text})


def fail_tool() -> str:
    raise RuntimeError("tool failed")


def greet_tool(name: str = "world") -> str:
    return json.dumps({"greeting": f"hello {name}"})


class TestPipelineStep:
    def test_step_executes_tool(self):
        step = PipelineStep(tool_name="echo_tool", input_args={"text": "ping"})
        tool_map = {"echo_tool": echo_tool}
        result = step.execute(tool_map, context={})
        assert result.status == "success"
        assert "ping" in result.output

    def test_step_captures_failure(self):
        step = PipelineStep(tool_name="fail_tool", input_args={})
        tool_map = {"fail_tool": fail_tool}
        result = step.execute(tool_map, context={})
        assert result.status == "failed"
        assert "tool failed" in result.output

    def test_step_retries_on_failure(self):
        call_count = 0
        def flaky_tool() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("transient")
            return json.dumps({"ok": True})

        step = PipelineStep(tool_name="flaky", input_args={}, max_retries=3)
        tool_map = {"flaky": flaky_tool}
        result = step.execute(tool_map, context={})
        assert result.status == "success"
        assert call_count == 3


class TestPipelineRun:
    def test_run_executes_steps_in_order(self):
        steps = [
            PipelineStep(tool_name="echo_tool", input_args={"text": "first"}),
            PipelineStep(tool_name="greet_tool", input_args={"name": "crowe"}),
        ]
        tool_map = {"echo_tool": echo_tool, "greet_tool": greet_tool}
        run = PipelineRun(name="test", steps=steps)
        run.execute(tool_map)
        assert run.status == "completed"
        assert len(run.results) == 2
        assert "first" in run.results[0].output
        assert "crowe" in run.results[1].output

    def test_run_stops_on_failure(self):
        steps = [
            PipelineStep(tool_name="echo_tool", input_args={"text": "ok"}),
            PipelineStep(tool_name="fail_tool", input_args={}),
            PipelineStep(tool_name="echo_tool", input_args={"text": "never"}),
        ]
        tool_map = {"echo_tool": echo_tool, "fail_tool": fail_tool}
        run = PipelineRun(name="test", steps=steps)
        run.execute(tool_map)
        assert run.status == "failed"
        assert len(run.results) == 2  # third step never ran

    def test_run_passes_state_between_steps(self):
        steps = [
            PipelineStep(tool_name="echo_tool", input_args={"text": "data"}),
            PipelineStep(tool_name="greet_tool", input_args={"name": "{previous.echoed}"}),
        ]
        tool_map = {"echo_tool": echo_tool, "greet_tool": greet_tool}
        run = PipelineRun(name="test", steps=steps)
        run.execute(tool_map)
        assert run.status == "completed"
        assert "data" in run.results[1].output


class TestPipelineTemplate:
    def test_load_template_from_yaml(self, engine):
        templates = engine.list_templates()
        names = [t.name for t in templates]
        assert "refactor" in names
        assert "research" in names
        assert "compose" in names

    def test_template_has_trigger(self, engine):
        t = engine.get_template("refactor")
        assert t is not None
        assert t.trigger is not None

    def test_match_template_by_input(self, engine):
        match = engine.match_template("refactor the main function")
        assert match is not None
        assert match.name == "refactor"

    def test_no_match_returns_none(self, engine):
        match = engine.match_template("what is the weather today")
        assert match is None
