# tests/test_agent_runner.py
"""Tests for tools.agent_runner -- CroweLM secure agent execution."""

import json
import os
import pytest
from unittest.mock import patch, MagicMock
import tools.agent_runner as runner_mod
import tools.audit_log as audit_mod
import tools.staging_pipeline as staging_mod


@pytest.fixture
def runner_env(tmp_path):
    """Set up isolated environment for agent runner tests."""
    old_data = runner_mod.DATA_DIR
    old_agents = runner_mod.AGENTS_DIR
    old_staging = staging_mod.STAGING_DIR
    old_logs = audit_mod.LOGS_DIR

    runner_mod.DATA_DIR = str(tmp_path / "data")
    runner_mod.AGENTS_DIR = str(tmp_path / "agents")
    staging_mod.STAGING_DIR = str(tmp_path / "staging")
    audit_mod.LOGS_DIR = str(tmp_path / "logs")

    os.makedirs(tmp_path / "data")
    os.makedirs(tmp_path / "agents")

    yield tmp_path

    runner_mod.DATA_DIR = old_data
    runner_mod.AGENTS_DIR = old_agents
    staging_mod.STAGING_DIR = old_staging
    audit_mod.LOGS_DIR = old_logs


def _write_agent_script(runner_env, agent_id="test_agent", code=None):
    """Write a minimal test agent script."""
    if code is None:
        code = (
            "import json, os\n"
            "print(json.dumps({\n"
            '    "status": "complete",\n'
            '    "items_staged": 0,\n'
            '    "agent_id": os.environ.get("CROWELM_AGENT_ID", ""),\n'
            '    "run_id": os.environ.get("CROWELM_RUN_ID", ""),\n'
            "}))\n"
        )
    script = runner_env / "agents" / f"{agent_id}.py"
    script.write_text(code)


class TestRunAgent:
    def test_returns_run_id_and_agent_id(self, runner_env):
        _write_agent_script(runner_env)
        result = runner_mod.run_agent("test_agent", "do something")
        assert "run_id" in result
        assert result["agent_id"] == "test_agent"

    def test_script_not_found(self, runner_env):
        result = runner_mod.run_agent("nonexistent", "task")
        assert result["status"] == "error"
        assert "not found" in result["message"]

    def test_logs_start_and_complete(self, runner_env):
        _write_agent_script(runner_env)
        result = runner_mod.run_agent("test_agent", "task")
        entries = audit_mod.get_run_log("test_agent", result["run_id"])
        actions = [e["action"] for e in entries]
        assert "run_start" in actions
        assert "run_complete" in actions

    def test_unknown_mode_returns_error(self, runner_env):
        result = runner_mod.run_agent("x", "task", mode="quantum")
        assert "error" in result


class TestRunLocal:
    def test_restricted_env_hides_secrets(self, runner_env):
        code = (
            "import json, os\n"
            "print(json.dumps({\n"
            '    "has_openrouter": "OPENROUTER_API_KEY" in os.environ,\n'
            '    "has_project": "PROJECT_ENDPOINT" in os.environ,\n'
            '    "has_agent_id": "CROWELM_AGENT_ID" in os.environ,\n'
            "}))\n"
        )
        _write_agent_script(runner_env, code=code)
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-secret", "PROJECT_ENDPOINT": "https://x"}):
            result = runner_mod.run_agent("test_agent", "check env")
        output = result.get("output", {})
        assert output.get("has_openrouter") is False
        assert output.get("has_project") is False
        assert output.get("has_agent_id") is True

    def test_agent_receives_task_in_env(self, runner_env):
        code = (
            "import json, os\n"
            'print(json.dumps({"task": os.environ.get("CROWELM_TASK", "")}))\n'
        )
        _write_agent_script(runner_env, code=code)
        result = runner_mod.run_agent("test_agent", "grow shiitake")
        assert result["output"]["task"] == "grow shiitake"


class TestRunDocker:
    def test_docker_command_has_isolation_flags(self, runner_env):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout='{"status": "complete", "items_staged": 0}',
                stderr="",
            )
            runner_mod.run_agent("test_agent", "task", mode="docker")

            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "docker"
            assert "--rm" in cmd
            v_indices = [i for i, a in enumerate(cmd) if a == "-v"]
            mounts = [cmd[i + 1] for i in v_indices]
            assert any(m.endswith(":ro") for m in mounts), "Data must be read-only"
            assert any(m.endswith(":rw") for m in mounts), "Staging must be read-write"


class TestCrowelmRunAgent:
    def test_returns_json_string(self, runner_env):
        _write_agent_script(runner_env)
        result = json.loads(runner_mod.crowelm_run_agent("test_agent", "task"))
        assert "run_id" in result
