"""Tests for CLI model-switch validation."""

from __future__ import annotations

from cli import crowe_logic as cli_mod
from config.agent_config import resolve_model_config


def test_model_switch_error_reports_missing_azure_credentials(monkeypatch):
    monkeypatch.delenv("AZURE_GLM_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_GLM_API_KEY", raising=False)

    cfg = resolve_model_config("crowelm-glm")
    error = cli_mod._model_switch_error(cfg)

    assert error is not None
    assert "AZURE_GLM_ENDPOINT" in error
    assert "AZURE_GLM_API_KEY" in error


def test_model_status_note_marks_blocked_models(monkeypatch):
    monkeypatch.delenv("AZURE_GLM_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_GLM_API_KEY", raising=False)

    cfg = resolve_model_config("crowelm-glm")
    assert cli_mod._model_status_note(cfg) == "blocked"


def test_model_status_note_prefers_failures_over_blocked(monkeypatch):
    monkeypatch.delenv("AZURE_GLM_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_GLM_API_KEY", raising=False)
    cli_mod._model_state["failures"]["FW-GLM-5"] = 2

    try:
        cfg = resolve_model_config("crowelm-glm")
        assert cli_mod._model_status_note(cfg) == "2 fails"
    finally:
        cli_mod._model_state["failures"].pop("FW-GLM-5", None)
