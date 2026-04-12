"""Tests for the deploy health-check command."""

from __future__ import annotations

from types import SimpleNamespace

import config.agent_config as agent_config
from cli import crowe_logic as cli_mod


def test_deploy_timeout_seconds_reads_env(monkeypatch):
    monkeypatch.setenv("CROWE_LOGIC_DEPLOY_TIMEOUT_SECONDS", "3.5")
    assert cli_mod._deploy_timeout_seconds() == 3.5


def test_deploy_passes_timeouts_to_provider_clients(monkeypatch):
    from click.testing import CliRunner
    import anthropic
    import openai
    import requests

    runner = CliRunner()
    captured: dict[str, dict] = {}

    monkeypatch.setenv("CROWE_LOGIC_DEPLOY_TIMEOUT_SECONDS", "4")
    monkeypatch.setenv("AZURE_CORE_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_CORE_API_KEY", "core-key")
    monkeypatch.setenv("AZURE_ANTHROPIC_ENDPOINT", "https://example.anthropic.azure.com")
    monkeypatch.setenv("AZURE_ANTHROPIC_API_KEY", "anthropic-key")
    monkeypatch.setattr(
        agent_config,
        "MODEL_CHAIN",
        [
            {"name": "gpt-5.4-pro", "label": "CroweLM Pro", "provider": "azure_openai", "surface": "responses"},
            {"name": "claude-opus-4-6", "label": "CroweLM Opus", "provider": "anthropic"},
        ],
    )
    monkeypatch.setattr(agent_config, "NEON_DATABASE_URL", "")

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["openai"] = kwargs
            self.responses = SimpleNamespace(create=lambda **_: SimpleNamespace(output_text="OK"))
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **_: SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))]
                    )
                )
            )

    class FakeAnthropic:
        def __init__(self, **kwargs):
            captured["anthropic"] = kwargs
            self.messages = SimpleNamespace(
                create=lambda **_: SimpleNamespace(content=[SimpleNamespace(type="text", text="OK")])
            )

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    monkeypatch.setattr(requests, "head", lambda *_, **__: SimpleNamespace(status_code=200))
    monkeypatch.setattr(requests, "get", lambda *_, **__: SimpleNamespace(status_code=200))

    result = runner.invoke(cli_mod.main, ["deploy"])

    assert result.exit_code == 0
    assert "request timeout 4s" in result.output
    assert captured["openai"]["timeout"] == 4.0
    assert captured["openai"]["max_retries"] == 0
    assert captured["anthropic"]["timeout"] == 4.0
    assert "LIVE" in result.output


def test_deploy_reports_timeout_status(monkeypatch):
    from click.testing import CliRunner
    import openai
    import requests

    runner = CliRunner()

    monkeypatch.setenv("AZURE_CORE_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_CORE_API_KEY", "core-key")
    monkeypatch.setattr(
        agent_config,
        "MODEL_CHAIN",
        [
            {"name": "gpt-4.1-mini", "label": "CroweLM Scout", "provider": "azure_openai"},
        ],
    )
    monkeypatch.setattr(agent_config, "NEON_DATABASE_URL", "")

    class TimeoutOpenAI:
        def __init__(self, **kwargs):
            self.responses = SimpleNamespace(create=lambda **_: None)
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_: (_ for _ in ()).throw(TimeoutError("request timed out")))
            )

    monkeypatch.setattr(openai, "OpenAI", TimeoutOpenAI)
    monkeypatch.setattr(requests, "head", lambda *_, **__: SimpleNamespace(status_code=200))
    monkeypatch.setattr(requests, "get", lambda *_, **__: SimpleNamespace(status_code=200))

    result = runner.invoke(cli_mod.main, ["deploy"])

    assert result.exit_code == 0
    assert "timeout" in result.output
    assert "No models available" in result.output
