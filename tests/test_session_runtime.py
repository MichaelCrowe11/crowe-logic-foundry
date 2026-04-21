"""Tests for session-scoped steering, datasets, and transcript persistence."""

from __future__ import annotations

import json

import cli.session_runtime as runtime_mod


def _write_manifest(path):
    manifest = {
        "summary": {
            "total_raw_samples": 201000,
            "crowelm_training_entries": 137875,
            "total_size_gb": 4.29,
            "domains": "biotech, pharma, mycology",
        },
        "datasets_acquired": {
            "specialized_rqa": "50,000 samples - STEM Q&A",
            "specialized_reasoning": "50,000 samples - Cross-domain reasoning",
        },
        "top_domains": {
            "gene": 91974,
            "rna": 74916,
            "mushroom": 18000,
        },
    }
    path.write_text(json.dumps(manifest), encoding="utf-8")


def test_handle_local_control_command_sets_and_clears_steering(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_mod, "_RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(runtime_mod, "_DATASET_MANIFEST_PATH", tmp_path / "DATASET_MANIFEST.json")

    session_id = "test-steering"
    response = runtime_mod.handle_local_control_command(
        "/steer Keep the work tightly scoped and factual.",
        session_id=session_id,
    )

    assert "Updated session steering." in response
    assert runtime_mod.load_session_runtime(session_id)["steering_instruction"] == (
        "Keep the work tightly scoped and factual."
    )

    cleared = runtime_mod.handle_local_control_command("/steer clear", session_id=session_id)
    assert cleared == "Cleared session steering."
    assert runtime_mod.load_session_runtime(session_id)["steering_instruction"] == ""


def test_handle_local_control_command_sets_dataset_by_partial_name(tmp_path, monkeypatch):
    manifest_path = tmp_path / "DATASET_MANIFEST.json"
    _write_manifest(manifest_path)
    monkeypatch.setattr(runtime_mod, "_RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(runtime_mod, "_DATASET_MANIFEST_PATH", manifest_path)

    session_id = "test-dataset"
    response = runtime_mod.handle_local_control_command(
        "/dataset reasoning",
        session_id=session_id,
    )

    assert response == "Using dataset-focused context for this session: specialized_reasoning"
    state = runtime_mod.load_session_runtime(session_id)
    assert state["dataset_selection"] == "specialized_reasoning"

    listing = runtime_mod.handle_local_control_command("/dataset", session_id=session_id)
    assert "Active dataset context: specialized_reasoning" in listing
    assert "specialized_reasoning" in listing


def test_build_runtime_system_instructions_includes_runtime_sections(tmp_path, monkeypatch):
    manifest_path = tmp_path / "DATASET_MANIFEST.json"
    _write_manifest(manifest_path)
    monkeypatch.setattr(runtime_mod, "_RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(runtime_mod, "_DATASET_MANIFEST_PATH", manifest_path)

    session_id = "test-instructions"
    runtime_mod.update_session_runtime(
        session_id,
        steering_instruction="Prioritize terminal-safe actions and concise status.",
        dataset_selection="specialized_reasoning",
    )

    instructions = runtime_mod.build_runtime_system_instructions(
        {"label": "CroweLM Apex", "name": "test-model"},
        session_id=session_id,
    )

    assert "## Active Operator Steering" in instructions
    assert "Prioritize terminal-safe actions and concise status." in instructions
    assert "## CroweLM Dataset Context" in instructions
    assert "Active dataset focus: specialized_reasoning" in instructions
    assert "Primary domains: biotech, pharma, mycology." in instructions


def test_format_transcript_markdown_renders_model_answer_and_reasoning():
    markdown = runtime_mod.format_transcript_markdown({
        "last_model": "CroweLM Apex",
        "last_answer_text": "Answer body",
        "last_reasoning_text": "Reasoning body",
    })

    assert "# Last Transcript" in markdown
    assert "Model: CroweLM Apex" in markdown
    assert "## Answer" in markdown
    assert "Answer body" in markdown
    assert "## Full Reasoning" in markdown
    assert "Reasoning body" in markdown
