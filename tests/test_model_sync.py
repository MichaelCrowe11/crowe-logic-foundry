"""Tests for Azure deployment sync helpers."""

from __future__ import annotations

from pathlib import Path

from config import model_sync


def test_build_extra_model_entry_infers_anthropic_and_responses():
    anthropic = model_sync.build_extra_model_entry({"name": "claude-opus-4-6"})
    responses = model_sync.build_extra_model_entry({"name": "gpt-5.4-mini"})

    assert anthropic["provider"] == "anthropic"
    assert anthropic["endpoint_env"] == "AZURE_ANTHROPIC_ENDPOINT"
    assert anthropic["api_key_env"] == "AZURE_ANTHROPIC_API_KEY"
    assert responses["provider"] == "azure_openai"
    assert responses["surface"] == "responses"
    assert responses["label"] == "CroweLM GPT 5.4 Mini"


def test_build_extra_models_payload_sorts_entries():
    payload = model_sync.build_extra_models_payload([
        {"name": "zeta-model"},
        {"name": "alpha-model"},
    ])

    assert [item["name"] for item in payload["models"]] == ["alpha-model", "zeta-model"]


def test_write_extra_models_payload_creates_parent_directory(tmp_path):
    output_path = tmp_path / "nested" / "models.extra.json"
    payload = {"models": [{"name": "gpt-4.1-mini"}]}

    written = model_sync.write_extra_models_payload(payload, output_path)

    assert written == output_path
    assert output_path.exists()
    assert '"name": "gpt-4.1-mini"' in output_path.read_text(encoding="utf-8")


def test_parse_sync_source_reads_input_file(tmp_path):
    input_path = tmp_path / "deployments.json"
    input_path.write_text('[{"name":"gpt-5.4-mini"}]', encoding="utf-8")

    deployments = model_sync.parse_sync_source(
        input_path=input_path,
        account=None,
        resource_group=None,
    )

    assert deployments == [{"name": "gpt-5.4-mini"}]


def test_resolve_output_path_defaults_to_user_config():
    assert model_sync.resolve_output_path() == model_sync.DEFAULT_MODELS_PATH
    assert model_sync.resolve_output_path(Path("~/custom.json")) == Path("~/custom.json").expanduser()


def test_sync_output_warnings_for_non_auto_loaded_path(tmp_path):
    warnings = model_sync.sync_output_warnings(
        tmp_path / "custom.json",
        project_root=tmp_path / "project",
        environ={},
    )

    assert len(warnings) == 1
    assert "not auto-loaded" in warnings[0]


def test_sync_output_warnings_for_shadowed_default_path(tmp_path):
    project_root = tmp_path / "project"
    project_models = project_root / "config" / "models.extra.json"
    project_models.parent.mkdir(parents=True)
    project_models.write_text('{"models":[]}', encoding="utf-8")

    warnings = model_sync.sync_output_warnings(
        model_sync.DEFAULT_MODELS_PATH,
        project_root=project_root,
        environ={},
    )

    assert len(warnings) == 1
    assert str(project_models) in warnings[0]
