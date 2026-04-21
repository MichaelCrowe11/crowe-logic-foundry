"""Tests for the `crowe-logic models sync` CLI command."""

from __future__ import annotations

from click.testing import CliRunner

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
