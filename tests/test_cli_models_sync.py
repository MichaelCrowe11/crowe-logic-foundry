"""Tests for the `crowe-logic models sync` CLI command."""

from __future__ import annotations

from click.testing import CliRunner

from cli import crowe_logic as cli_mod
from cli.crowe_logic import main


def test_models_sync_writes_default_output(monkeypatch, tmp_path):
    runner = CliRunner()
    output_path = tmp_path / "models.extra.json"

    monkeypatch.setattr("config.model_sync.DEFAULT_MODELS_PATH", output_path)

    def fake_parse_sync_source(*, input_path, account, resource_group):
        assert input_path is None
        assert account == "acct"
        assert resource_group == "rg"
        return [{"name": "gpt-5.4-mini"}]

    monkeypatch.setattr("config.model_sync.parse_sync_source", fake_parse_sync_source)

    result = runner.invoke(main, ["models", "sync", "--account", "acct", "--resource-group", "rg"])

    assert result.exit_code == 0
    assert output_path.exists()
    assert "Synced 1 models to" in result.output


def test_models_sync_supports_offline_input(tmp_path):
    runner = CliRunner()
    input_path = tmp_path / "deployments.json"
    output_path = tmp_path / "models.extra.json"
    input_path.write_text('[{"name":"claude-opus-4-6"}]', encoding="utf-8")

    result = runner.invoke(
        main,
        [
            "models",
            "sync",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
    )

    assert result.exit_code == 0
    rendered = output_path.read_text(encoding="utf-8")
    assert '"provider": "anthropic"' in rendered
    assert '"name": "claude-opus-4-6"' in rendered


def test_models_sync_requires_a_source():
    runner = CliRunner()

    result = runner.invoke(main, ["models", "sync"])

    assert result.exit_code != 0
    assert "Provide either --input or both --account and --resource-group" in result.output


def test_models_sync_warns_when_output_is_shadowed(monkeypatch, tmp_path):
    runner = CliRunner()
    output_path = tmp_path / "user" / "models.extra.json"

    monkeypatch.setattr("config.model_sync.DEFAULT_MODELS_PATH", output_path)

    def fake_parse_sync_source(*, input_path, account, resource_group):
        return [{"name": "gpt-5.4-mini"}]

    def fake_sync_output_warnings(output_path, *, project_root):
        assert project_root.name == "crowe-logic-foundry"
        return ["Runtime will prefer /tmp/project/config/models.extra.json over this file."]

    monkeypatch.setattr("config.model_sync.parse_sync_source", fake_parse_sync_source)
    monkeypatch.setattr("config.model_sync.sync_output_warnings", fake_sync_output_warnings)

    result = runner.invoke(main, ["models", "sync", "--account", "acct", "--resource-group", "rg"])

    assert result.exit_code == 0
    assert "prefer /tmp/project/config/models.extra.json" in result.output


def test_models_legend_operator_view_shows_provider_and_readiness(monkeypatch):
    runner = CliRunner()
    monkeypatch.delenv("LEGEND_TEST_ENDPOINT", raising=False)
    monkeypatch.delenv("LEGEND_TEST_KEY", raising=False)
    monkeypatch.setattr(
        cli_mod,
        "MODEL_CHAIN",
        [
            {
                "name": "crowelm-auto",
                "label": "CroweLM Auto",
                "provider": "auto",
                "type": "router",
                "aliases": ["auto"],
            },
            {
                "name": "crowelm-missing",
                "label": "CroweLM Missing",
                "provider": "azure_openai",
                "endpoint_env": "LEGEND_TEST_ENDPOINT",
                "api_key_env": "LEGEND_TEST_KEY",
                "aliases": ["missing"],
            },
        ],
    )

    result = runner.invoke(main, ["models", "legend"])

    assert result.exit_code == 0
    assert "CroweLM Auto" in result.output
    assert "CroweLM Missing" in result.output
    assert "config readiness" in result.output
    assert cli_mod._model_legend_status(cli_mod.MODEL_CHAIN[0]) == (
        "virtual",
        "available",
    )
    assert cli_mod._model_legend_status(cli_mod.MODEL_CHAIN[1]) == (
        "missing config",
        "operator-only",
    )


def test_models_legend_customer_view_hides_backend_details(monkeypatch):
    runner = CliRunner()
    monkeypatch.setattr(
        cli_mod,
        "MODEL_CHAIN",
        [
            {
                "name": "crowelm-kernel",
                "label": "CroweLM Kernel",
                "provider": "azure_openai",
                "type": "reasoning",
                "backend_name": "gpt-5.4-nano",
                "endpoint_env": "AZURE_CORE_ENDPOINT",
                "api_key_env": "AZURE_CORE_API_KEY",
                "aliases": ["kernel", "gpt-5.4-nano", "CroweLM Kernel"],
            },
        ],
    )

    result = runner.invoke(main, ["models", "legend", "--customer"])

    assert result.exit_code == 0
    assert "CroweLM Kernel" in result.output
    assert cli_mod._model_legend_use(cli_mod.MODEL_CHAIN[0]) == (
        "Cultivation and mycology"
    )
    assert "Provider" not in result.output
    assert "azure_openai" not in result.output
    assert "gpt-5.4-nano" not in result.output
    aliases = cli_mod._model_legend_aliases(cli_mod.MODEL_CHAIN[0], customer=True)
    assert "kernel" in aliases
    assert "gpt-5.4-nano" not in aliases


def test_models_legend_only_ready_filters_operator_only_models(monkeypatch):
    runner = CliRunner()
    monkeypatch.delenv("LEGEND_TEST_ENDPOINT", raising=False)
    monkeypatch.delenv("LEGEND_TEST_KEY", raising=False)
    monkeypatch.setattr(
        cli_mod,
        "MODEL_CHAIN",
        [
            {
                "name": "crowelm-auto",
                "label": "CroweLM Auto",
                "provider": "auto",
                "type": "router",
                "aliases": ["auto"],
            },
            {
                "name": "crowelm-missing",
                "label": "CroweLM Missing",
                "provider": "azure_openai",
                "endpoint_env": "LEGEND_TEST_ENDPOINT",
                "api_key_env": "LEGEND_TEST_KEY",
            },
        ],
    )

    result = runner.invoke(main, ["models", "legend", "--only-ready"])

    assert result.exit_code == 0
    assert "CroweLM Auto" in result.output
    assert "CroweLM Missing" not in result.output
