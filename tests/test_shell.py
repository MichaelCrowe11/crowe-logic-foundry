"""Tests for tools/shell.py — execute_shell."""

import json
import os
import pytest


@pytest.fixture()
def shell_mod():
    from tools.shell import execute_shell
    return execute_shell


class TestExecuteShell:
    def test_runs_simple_command(self, shell_mod):
        result = json.loads(shell_mod("echo hello"))
        assert result["return_code"] == 0
        assert "hello" in result["stdout"]

    def test_captures_stderr(self, shell_mod):
        result = json.loads(shell_mod("echo err >&2"))
        assert "err" in result["stderr"]

    def test_returns_nonzero_exit_code(self, shell_mod):
        result = json.loads(shell_mod("exit 42"))
        assert result["return_code"] == 42

    def test_respects_working_directory(self, shell_mod, tmp_path):
        result = json.loads(shell_mod("pwd", working_directory=str(tmp_path)))
        assert result["stdout"].strip() == str(tmp_path)

    def test_timeout_produces_error(self, shell_mod):
        result = json.loads(shell_mod("sleep 10", timeout_seconds=1))
        assert "error" in result
        assert "timed out" in result["error"].lower()

    def test_timeout_capped_at_600(self, shell_mod):
        # This shouldn't hang — it verifies the cap logic runs without error
        result = json.loads(shell_mod("echo ok", timeout_seconds=9999))
        assert result["return_code"] == 0

    def test_large_stdout_truncated(self, shell_mod):
        # Generate > 50KB of output
        result = json.loads(shell_mod("python3 -c \"print('x' * 60000)\""))
        assert "truncated" in result["stdout"] or len(result["stdout"]) <= 51000

    def test_default_working_directory_is_home(self, shell_mod):
        result = json.loads(shell_mod("pwd"))
        assert result["stdout"].strip() == os.path.expanduser("~")
