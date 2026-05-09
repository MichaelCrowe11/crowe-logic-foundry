"""Tests for cli.cluster_dispatch.

Two layers:
  * Unit tests: mock the HTTP call surface so the routing, error handling,
    and session-recording logic are exercised without hitting a real backend.
  * Integration test: marked `live_ollama`; only runs when the local Ollama
    daemon answers and the env var ALLOW_LIVE_OLLAMA_TESTS=1 is set.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from crowe_synapse_engine.agent_registry import AgentRegistry
from cli.cluster_dispatch import (
    ClusterSession,
    DispatchResult,
    dispatch_in_parallel,
    dispatch_to_specialist,
    run_critic_gate,
)


@pytest.fixture
def registry():
    agents_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "agents")
    return AgentRegistry(agents_dir=agents_dir)


def _fake_ok_response(content: str, ptok: int = 100, ctok: int = 50) -> dict:
    """Build a fake OpenAI-compat chat response."""
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {
            "prompt_tokens": ptok,
            "completion_tokens": ctok,
            "total_tokens": ptok + ctok,
        },
    }


# ── Unit ─────────────────────────────────────────────────────────────────


class TestDispatchResolution:
    def test_unknown_specialist_returns_error(self, registry):
        result = dispatch_to_specialist(
            "music-doesnotexist", "test brief", registry=registry
        )
        assert not result.succeeded
        assert "not found" in result.error

    def test_alias_resolves_to_target(self, registry):
        # 'music' is a legacy alias for music-orchestrator. Dispatching to
        # 'music' should land on music-orchestrator's prompt.
        with patch("cli.cluster_dispatch._post_chat") as fake:
            fake.return_value = _fake_ok_response("hello from orchestrator")
            with patch.dict(os.environ, {"NVIDIA_NIM_ENDPOINT": "http://nim.example", "NVIDIA_API_KEY": "test"}):
                result = dispatch_to_specialist(
                    "music", "say hello", registry=registry
                )
        assert result.specialist == "music-orchestrator"
        assert result.succeeded
        assert "hello from orchestrator" in result.answer


class TestDispatchHttp:
    def test_records_token_usage(self, registry):
        with patch("cli.cluster_dispatch._post_chat") as fake:
            fake.return_value = _fake_ok_response("PASS", ptok=300, ctok=2)
            with patch.dict(
                os.environ,
                {"OLLAMA_BASE_URL": "http://localhost:11434/v1"},
            ):
                result = dispatch_to_specialist(
                    "music-critic", "trivial brief", registry=registry
                )
        assert result.succeeded
        assert result.prompt_tokens == 300
        assert result.completion_tokens == 2
        assert result.total_tokens == 302
        assert result.provider == "ollama"

    def test_transport_error_does_not_raise(self, registry):
        import urllib.error

        with patch("cli.cluster_dispatch._post_chat") as fake:
            fake.side_effect = urllib.error.URLError("connection refused")
            with patch.dict(
                os.environ, {"OLLAMA_BASE_URL": "http://localhost:11434/v1"}
            ):
                result = dispatch_to_specialist(
                    "music-critic", "test", registry=registry
                )
        assert not result.succeeded
        assert "transport" in (result.error or "")

    def test_missing_endpoint_env_returns_error(self, registry):
        # music-master uses crowelm-coder which is NVIDIA-NIM-backed. With
        # NVIDIA_NIM_ENDPOINT unset, dispatch should fail cleanly.
        env = {k: v for k, v in os.environ.items() if k != "NVIDIA_NIM_ENDPOINT"}
        with patch.dict(os.environ, env, clear=True):
            result = dispatch_to_specialist(
                "music-master", "test", registry=registry
            )
        assert not result.succeeded
        assert "NVIDIA_NIM_ENDPOINT" in (result.error or "")


class TestSessionTracking:
    def test_session_records_dispatches(self, registry):
        session = ClusterSession(session_id="test-1", cluster="crowelm-music")
        with patch("cli.cluster_dispatch._post_chat") as fake:
            fake.return_value = _fake_ok_response("PASS")
            with patch.dict(
                os.environ, {"OLLAMA_BASE_URL": "http://localhost:11434/v1"}
            ):
                dispatch_to_specialist(
                    "music-critic", "diff 1", registry=registry, session=session
                )
                dispatch_to_specialist(
                    "music-critic", "diff 2", registry=registry, session=session
                )
        assert len(session.history) == 2
        assert all(r.succeeded for r in session.history)
        assert len(session.successful_dispatches()) == 2

    def test_session_records_failures(self, registry):
        session = ClusterSession(session_id="test-2", cluster="crowelm-music")
        result = dispatch_to_specialist(
            "music-doesnotexist", "diff", registry=registry, session=session
        )
        assert not result.succeeded
        assert len(session.history) == 1
        assert len(session.failed_dispatches()) == 1


class TestParallelDispatch:
    def test_results_returned_in_input_order(self, registry):
        # Side-effect as a function so each call gets a deterministic
        # response based on which specialist's prompt is being sent. The
        # alternative (a list) is consumed in thread-call order, not
        # specialist order, so the test would be flaky.
        def fake_post(base_url, api_key, payload, timeout_s):
            system_prompt = payload["messages"][0]["content"]
            if "Music-Web" in system_prompt:
                return _fake_ok_response("from web")
            if "Music-Native" in system_prompt:
                return _fake_ok_response("from native")
            return _fake_ok_response("unknown")

        with patch("cli.cluster_dispatch._post_chat", side_effect=fake_post):
            with patch.dict(
                os.environ,
                {"NVIDIA_NIM_ENDPOINT": "http://nim.example", "NVIDIA_API_KEY": "test"},
            ):
                results = dispatch_in_parallel(
                    ["music-web", "music-native"], "build the timeline", registry=registry
                )
        assert [r.specialist for r in results] == ["music-web", "music-native"]
        assert results[0].answer == "from web"
        assert results[1].answer == "from native"


class TestCriticGate:
    def test_pass_means_passed(self, registry):
        with patch("cli.cluster_dispatch._post_chat") as fake:
            fake.return_value = _fake_ok_response("PASS")
            with patch.dict(
                os.environ, {"OLLAMA_BASE_URL": "http://localhost:11434/v1"}
            ):
                passed, result = run_critic_gate(
                    "--- a\n+++ b\n+ clean line\n", registry=registry
                )
        assert passed is True
        assert result.answer.strip() == "PASS"

    def test_block_means_not_passed(self, registry):
        with patch("cli.cluster_dispatch._post_chat") as fake:
            fake.return_value = _fake_ok_response(
                "BLOCK: em dash present\n  file:42 introduces an em dash."
            )
            with patch.dict(
                os.environ, {"OLLAMA_BASE_URL": "http://localhost:11434/v1"}
            ):
                passed, result = run_critic_gate(
                    "+ Built by operators — signed by Talon.", registry=registry
                )
        assert passed is False
        assert result.answer.startswith("BLOCK")


# ── Integration (live Ollama) ────────────────────────────────────────────


@pytest.mark.skipif(
    os.environ.get("ALLOW_LIVE_OLLAMA_TESTS") != "1",
    reason="live Ollama integration test; set ALLOW_LIVE_OLLAMA_TESTS=1 to run",
)
class TestLiveOllama:
    def test_critic_gate_against_real_kimi_k26_cloud(self, registry):
        """Exercises the full path against the real Ollama Pro tier."""
        clean_diff = (
            "--- a/site/index.html\n+++ b/site/index.html\n@@ -1,1 +1,1 @@\n"
            "-old line\n+new line that is clean\n"
        )
        passed, result = run_critic_gate(
            clean_diff, registry=registry, timeout_s=120.0
        )
        assert result.error is None, result.error
        assert result.specialist == "music-critic"
        assert result.provider == "ollama"
        assert result.total_tokens > 0
        assert result.latency_s > 0
        # Clean diff should pass; if not, model is misbehaving but the
        # transport worked.
        assert passed or "PASS" not in result.answer  # at least one must hold
