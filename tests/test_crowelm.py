"""Tests for tools.crowelm — CroweLM training data management."""

import json
import os
import pytest

# We'll patch DATA_DIR in the module to point at a temp dir
import tools.crowelm as crowelm_mod


@pytest.fixture
def data_dir(tmp_path):
    """Set up a mock data directory with manifest and curated dir."""
    manifest = {
        "manifest_version": "1.0",
        "summary": {
            "total_raw_samples": 201000,
            "crowelm_training_entries": 137875,
            "total_size_gb": 4.29,
            "domains": "biotech, pharma, mycology"
        },
        "datasets_acquired": {
            "specialized_rqa": "50,000 samples - STEM Q&A",
            "specialized_reasoning": "50,000 samples - Cross-domain reasoning"
        },
        "top_domains": {"gene": 91974, "rna": 74916}
    }
    (tmp_path / "DATASET_MANIFEST.json").write_text(json.dumps(manifest))

    unified = {"total_entries": 137875, "format": "jsonl"}
    unified_dir = tmp_path / "unified_training"
    unified_dir.mkdir()
    (unified_dir / "UNIFIED_MANIFEST.json").write_text(json.dumps(unified))

    nemo_dir = tmp_path / "nemo_training"
    nemo_dir.mkdir()
    (nemo_dir / "training_config.yaml").write_text("model: llama-3.1-8b\nepochs: 3\nbatch_size: 4\n")

    curated_dir = tmp_path / "curated"
    curated_dir.mkdir()

    old_dir = crowelm_mod.DATA_DIR
    crowelm_mod.DATA_DIR = str(tmp_path)
    yield tmp_path
    crowelm_mod.DATA_DIR = old_dir


class TestQueryTier:
    def test_list_datasets(self, data_dir):
        result = json.loads(crowelm_mod.crowelm_list_datasets())
        assert result["summary"]["total_raw_samples"] == 201000
        assert "datasets_acquired" in result

    def test_dataset_stats(self, data_dir):
        result = json.loads(crowelm_mod.crowelm_dataset_stats())
        assert result["total_raw_samples"] == 201000
        assert "top_domains" in result

    def test_inspect_config(self, data_dir):
        result = json.loads(crowelm_mod.crowelm_inspect_config())
        assert "nemo" in result
        assert "epochs" in result["nemo"]

    def test_search_examples_empty_curated(self, data_dir):
        result = json.loads(crowelm_mod.crowelm_search_examples("mushroom"))
        assert result["results"] == []
        assert result["count"] == 0


class TestCurationTier:
    def test_add_example(self, data_dir):
        result = json.loads(crowelm_mod.crowelm_add_example(
            instruction="How do I grow shiitake?",
            response="Start with hardwood sawdust blocks...",
            category="mycology"
        ))
        assert result["added"] is True
        assert result["category"] == "mycology"
        assert os.path.exists(data_dir / "curated" / "mycology.jsonl")

    def test_search_finds_added_example(self, data_dir):
        crowelm_mod.crowelm_add_example("shiitake substrate", "oak sawdust", "mycology")
        result = json.loads(crowelm_mod.crowelm_search_examples("shiitake"))
        assert result["count"] == 1
        assert "shiitake" in result["results"][0]["instruction"]

    def test_remove_example(self, data_dir):
        add_result = json.loads(crowelm_mod.crowelm_add_example("test", "response", "general"))
        example_id = add_result["example_id"]
        remove_result = json.loads(crowelm_mod.crowelm_remove_example(example_id))
        assert remove_result["removed"] is True

    def test_export_curated(self, data_dir):
        crowelm_mod.crowelm_add_example("q1", "a1", "general")
        crowelm_mod.crowelm_add_example("q2", "a2", "mycology")
        result = json.loads(crowelm_mod.crowelm_export_curated())
        assert result["count"] == 2
        assert result["format"] == "jsonl"
        assert os.path.exists(result["path"])


class TestPipelineTier:
    def test_prepare_training(self, data_dir):
        crowelm_mod.crowelm_add_example("q1", "a1", "general")
        result = json.loads(crowelm_mod.crowelm_prepare_training())
        assert "total_examples" in result
        assert result["total_examples"] >= 1

    def test_training_status_when_idle(self, data_dir):
        result = json.loads(crowelm_mod.crowelm_training_status())
        assert result["running"] is False
