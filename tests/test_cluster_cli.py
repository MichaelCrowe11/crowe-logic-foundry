"""Tests for cli.cluster_cli (Click subgroup that exposes the CroweLM-Music
cluster on the command line). Uses Click's CliRunner with mocked HTTP so
tests don't hit real backends.
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from cli.cluster_cli import cluster


@pytest.fixture(autouse=True)
def agents_dir_env(monkeypatch):
    """Point the CLI at this repo's agents/ directory regardless of cwd."""
    repo_root = os.path.dirname(os.path.dirname(__file__))
    agents = os.path.join(repo_root, "agents")
    monkeypatch.setenv("CROWE_FOUNDRY_AGENTS_DIR", agents)


def _fake_ok(content: str, ptok: int = 100, ctok: int = 50) -> dict:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {
            "prompt_tokens": ptok,
            "completion_tokens": ctok,
            "total_tokens": ptok + ctok,
        },
    }


# ── list / show ──────────────────────────────────────────────────────────


class TestListShow:
    def test_list_shows_crowelm_music(self):
        result = CliRunner().invoke(cluster, ["list"])
        assert result.exit_code == 0
        assert "crowelm-music" in result.output
        assert "music-orchestrator" in result.output  # ai_panel_entry

    def test_show_lists_members(self):
        result = CliRunner().invoke(cluster, ["show", "crowelm-music"])
        assert result.exit_code == 0
        # All ten cluster members + the legacy 'music' alias should appear.
        for member in (
            "music-orchestrator", "music-compose", "music-mix", "music-master",
            "music-dsp", "music-native", "music-web", "music-provenance",
            "music-critic", "music-test", "music",
        ):
            assert member in result.output

    def test_show_unknown_cluster_errors(self):
        result = CliRunner().invoke(cluster, ["show", "does-not-exist"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()


# ── ask ──────────────────────────────────────────────────────────────────


class TestAsk:
    def test_ask_dispatches_and_prints_answer(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        with patch("cli.cluster_dispatch._post_chat") as fake:
            fake.return_value = _fake_ok("hello from critic")
            result = CliRunner().invoke(
                cluster, ["ask", "music-critic", "test brief"]
            )
        assert result.exit_code == 0
        assert "music-critic" in result.output
        assert "hello from critic" in result.output

    def test_ask_supports_stdin(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        with patch("cli.cluster_dispatch._post_chat") as fake:
            fake.return_value = _fake_ok("got it")
            result = CliRunner().invoke(
                cluster, ["ask", "music-critic"], input="brief from stdin"
            )
        assert result.exit_code == 0
        assert "got it" in result.output

    def test_ask_supports_file_input(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        f = tmp_path / "brief.txt"
        f.write_text("brief from file")
        with patch("cli.cluster_dispatch._post_chat") as fake:
            fake.return_value = _fake_ok("from-file response")
            result = CliRunner().invoke(
                cluster, ["ask", "music-critic", "-f", str(f)]
            )
        assert result.exit_code == 0
        assert "from-file response" in result.output

    def test_ask_json_mode_emits_structured_output(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        with patch("cli.cluster_dispatch._post_chat") as fake:
            fake.return_value = _fake_ok("PASS", ptok=120, ctok=2)
            result = CliRunner().invoke(
                cluster, ["ask", "music-critic", "trivial", "--json"]
            )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["specialist"] == "music-critic"
        assert payload["answer"] == "PASS"
        assert payload["total_tokens"] == 122

    def test_ask_no_brief_errors_when_stdin_is_tty(self):
        # CliRunner's stdin is empty by default; without a brief or -f, the
        # command should error rather than dispatch with empty content.
        result = CliRunner().invoke(cluster, ["ask", "music-critic"])
        assert result.exit_code != 0
        # Error message should guide the operator to one of the three input
        # methods.
        assert "brief" in result.output.lower()

    def test_ask_unknown_specialist_exit_nonzero(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        result = CliRunner().invoke(
            cluster, ["ask", "music-doesnotexist", "test"]
        )
        # Dispatch returns a result with an error; CLI exits 1 on failure.
        assert result.exit_code == 1
        assert "not found" in result.output.lower()


# ── gate ─────────────────────────────────────────────────────────────────


class TestGate:
    def test_gate_pass_exits_zero(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        with patch("cli.cluster_dispatch._post_chat") as fake:
            fake.return_value = _fake_ok("PASS")
            result = CliRunner().invoke(
                cluster, ["gate", "+ clean line"]
            )
        assert result.exit_code == 0
        assert "PASS" in result.output

    def test_gate_block_exits_one(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        with patch("cli.cluster_dispatch._post_chat") as fake:
            fake.return_value = _fake_ok(
                "BLOCK: em dash present\n  hero.html:42 has em dash."
            )
            result = CliRunner().invoke(
                cluster, ["gate"], input="+ Operators — signed by Talon."
            )
        assert result.exit_code == 1
        assert "BLOCK" in result.output

    def test_gate_json_mode(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        diff_file = tmp_path / "test.diff"
        diff_file.write_text("--- a\n+++ b\n+ new line\n")
        with patch("cli.cluster_dispatch._post_chat") as fake:
            fake.return_value = _fake_ok("PASS")
            result = CliRunner().invoke(
                cluster, ["gate", "-f", str(diff_file), "--json"]
            )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["passed"] is True
        assert payload["critic"] == "music-critic"


# ── parallel ─────────────────────────────────────────────────────────────


class TestParallel:
    def test_parallel_dispatch_prints_each_answer(self, monkeypatch):
        monkeypatch.setenv("NVIDIA_NIM_ENDPOINT", "http://nim.example")
        monkeypatch.setenv("NVIDIA_API_KEY", "test")

        def fake_post(base_url, api_key, payload, timeout_s):
            system = payload["messages"][0]["content"]
            if "Music-Web" in system:
                return _fake_ok("from web")
            if "Music-Native" in system:
                return _fake_ok("from native")
            return _fake_ok("?")

        with patch("cli.cluster_dispatch._post_chat", side_effect=fake_post):
            result = CliRunner().invoke(
                cluster, ["parallel", "music-web,music-native", "build it"]
            )
        assert result.exit_code == 0
        assert "from web" in result.output
        assert "from native" in result.output
        # Both specialists should appear as separate sections.
        assert result.output.count("specialist:") == 2

    def test_parallel_empty_list_errors(self):
        result = CliRunner().invoke(cluster, ["parallel", ",", "brief"])
        assert result.exit_code != 0
