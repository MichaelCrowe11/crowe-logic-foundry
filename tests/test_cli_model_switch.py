"""Tests for CLI model-switch validation."""

from __future__ import annotations

from cli import crowe_logic as cli_mod
from config.agent_config import resolve_model_config


def test_model_switch_error_reports_missing_azure_credentials(monkeypatch):
    monkeypatch.delenv("CROWE_OPEN_ENDPOINT", raising=False)

    cfg = resolve_model_config("crowelm-glm")
    error = cli_mod._model_switch_error(cfg)

    assert error is not None
    assert "CROWE_OPEN_ENDPOINT" in error


def test_model_status_note_marks_blocked_models(monkeypatch):
    monkeypatch.delenv("CROWE_OPEN_ENDPOINT", raising=False)

    cfg = resolve_model_config("crowelm-glm")
    assert cli_mod._model_status_note(cfg) == "blocked"


def test_model_status_note_prefers_failures_over_blocked(monkeypatch):
    monkeypatch.delenv("CROWE_OPEN_ENDPOINT", raising=False)
    cli_mod._model_state["failures"]["FW-GLM-5.1"] = 2

    try:
        cfg = resolve_model_config("crowelm-glm")
        assert cli_mod._model_status_note(cfg) == "2 fails"
    finally:
        cli_mod._model_state["failures"].pop("FW-GLM-5.1", None)


def test_model_switch_error_allows_hosted_models_with_endpoint_only(monkeypatch):
    monkeypatch.setenv("CROWE_OPEN_ENDPOINT", "https://models.crowe.logic/v1")
    monkeypatch.delenv("CROWE_OPEN_API_KEY", raising=False)

    cfg = resolve_model_config("crowelm-glm")
    assert cli_mod._model_switch_error(cfg) is None
