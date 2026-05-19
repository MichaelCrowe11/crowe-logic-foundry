# tests/test_staging_pipeline.py
"""Tests for tools.staging_pipeline -- CroweLM staging and tiered gate."""

import json
import pytest
import tools.staging_pipeline as staging_mod
import tools.audit_log as audit_mod


@pytest.fixture
def staging_env(tmp_path):
    """Redirect staging and logs to temp directories."""
    old_staging = staging_mod.STAGING_DIR
    old_logs = audit_mod.LOGS_DIR
    staging_mod.STAGING_DIR = str(tmp_path / "staging")
    audit_mod.LOGS_DIR = str(tmp_path / "logs")
    yield tmp_path
    staging_mod.STAGING_DIR = old_staging
    audit_mod.LOGS_DIR = old_logs


class TestEnsureStagingDirs:
    def test_creates_all_subdirs(self, staging_env):
        staging_mod.ensure_staging_dirs()
        staging = staging_env / "staging"
        for subdir in ["pending", "approved", "review", "rejected"]:
            assert (staging / subdir).is_dir()


class TestStageItem:
    def test_creates_pending_file(self, staging_env):
        item = staging_mod.stage_item("gen-agent", {"instruction": "test", "response": "resp"})
        pending = staging_env / "staging" / "pending" / f"{item['id']}.jsonl"
        assert pending.exists()

    def test_stage_item_rejects_invalid_agent_id(self, staging_env):
        with pytest.raises(ValueError, match="Invalid agent_id"):
            staging_mod.stage_item("../../etc/evil", {"instruction": "q"})
        with pytest.raises(ValueError, match="Invalid agent_id"):
            staging_mod.stage_item("", {"instruction": "q"})
        with pytest.raises(ValueError, match="Invalid agent_id"):
            staging_mod.stage_item("has spaces", {"instruction": "q"})

    def test_returns_metadata(self, staging_env):
        item = staging_mod.stage_item("gen-agent", {"instruction": "q"}, run_id="run-1")
        assert "id" in item
        assert isinstance(item["timestamp"], float)
        assert item["agent_id"] == "gen-agent"
        assert item["run_id"] == "run-1"
        assert item["data"]["instruction"] == "q"


class TestApplyGate:
    def test_auto_approve_above_085(self, staging_env):
        staged = staging_mod.stage_item("agent", {"instruction": "q", "response": "a"})
        result = staging_mod.apply_gate(staged["id"], 0.9)
        assert result["destination"] == "approved"
        assert not (staging_env / "staging" / "pending" / f"{staged['id']}.jsonl").exists()
        assert (staging_env / "staging" / "approved" / f"{staged['id']}.jsonl").exists()

    def test_review_between_05_and_085(self, staging_env):
        staged = staging_mod.stage_item("agent", {"instruction": "q", "response": "a"})
        result = staging_mod.apply_gate(staged["id"], 0.7)
        assert result["destination"] == "review"
        assert (staging_env / "staging" / "review" / f"{staged['id']}.jsonl").exists()

    def test_reject_below_05(self, staging_env):
        staged = staging_mod.stage_item("agent", {"instruction": "q", "response": "a"})
        result = staging_mod.apply_gate(staged["id"], 0.3)
        assert result["destination"] == "rejected"
        assert (staging_env / "staging" / "rejected" / f"{staged['id']}.jsonl").exists()

    def test_boundary_085_is_approved(self, staging_env):
        staged = staging_mod.stage_item("agent", {"instruction": "q", "response": "a"})
        assert staging_mod.apply_gate(staged["id"], 0.85)["destination"] == "approved"

    def test_boundary_050_is_review(self, staging_env):
        staged = staging_mod.stage_item("agent", {"instruction": "q", "response": "a"})
        assert staging_mod.apply_gate(staged["id"], 0.5)["destination"] == "review"

    def test_boundary_049_is_rejected(self, staging_env):
        staged = staging_mod.stage_item("agent", {"instruction": "q", "response": "a"})
        assert staging_mod.apply_gate(staged["id"], 0.49)["destination"] == "rejected"

    def test_boundary_084_is_review(self, staging_env):
        staged = staging_mod.stage_item("agent", {"instruction": "q", "response": "a"})
        assert staging_mod.apply_gate(staged["id"], 0.84)["destination"] == "review"

    def test_approved_item_has_quality_score(self, staging_env):
        staged = staging_mod.stage_item("agent", {"instruction": "q", "response": "a"})
        staging_mod.apply_gate(staged["id"], 0.9)
        path = staging_env / "staging" / "approved" / f"{staged['id']}.jsonl"
        item = json.loads(path.read_text().strip())
        assert item["quality_score"] == 0.9

    def test_missing_item_returns_error(self, staging_env):
        staging_mod.ensure_staging_dirs()
        result = staging_mod.apply_gate("nonexistent", 0.9)
        assert "error" in result

    def test_apply_gate_rejects_invalid_item_id(self, staging_env):
        staging_mod.ensure_staging_dirs()
        result = staging_mod.apply_gate("../../etc/evil", 0.9)
        assert "error" in result
        assert "Invalid item_id" in result["error"]
        result2 = staging_mod.apply_gate("", 0.9)
        assert "error" in result2
        result3 = staging_mod.apply_gate("has spaces", 0.9)
        assert "error" in result3


class TestListStaged:
    def test_lists_pending_items(self, staging_env):
        staging_mod.stage_item("agent", {"instruction": "q1"})
        staging_mod.stage_item("agent", {"instruction": "q2"})
        assert len(staging_mod.list_staged("pending")) == 2

    def test_invalid_status_returns_empty(self, staging_env):
        assert staging_mod.list_staged("invalid") == []


class TestPromoteApproved:
    def test_moves_to_curated(self, staging_env):
        staged = staging_mod.stage_item("agent", {
            "instruction": "How to grow shiitake?",
            "response": "Use hardwood sawdust blocks.",
            "category": "mycology",
        })
        staging_mod.apply_gate(staged["id"], 0.9)
        result = staging_mod.promote_approved()
        assert result["promoted"] == 1
        curated = staging_env / "curated" / "mycology.jsonl"
        assert curated.exists()
        example = json.loads(curated.read_text().strip())
        assert example["instruction"] == "How to grow shiitake?"
        assert example["quality_score"] == 0.9

    def test_removes_from_approved_after_promote(self, staging_env):
        staged = staging_mod.stage_item("agent", {"instruction": "q", "response": "a"})
        staging_mod.apply_gate(staged["id"], 0.9)
        staging_mod.promote_approved()
        assert len(list((staging_env / "staging" / "approved").iterdir())) == 0

    def test_promote_sanitizes_invalid_category(self, staging_env):
        staged = staging_mod.stage_item("agent", {
            "instruction": "Test traversal",
            "response": "Should land in general.",
            "category": "../../etc/evil",
        })
        staging_mod.apply_gate(staged["id"], 0.9)
        result = staging_mod.promote_approved()
        assert result["promoted"] == 1
        curated = staging_env / "curated" / "general.jsonl"
        assert curated.exists()
        evil = staging_env / "curated" / "..%2F..%2Fetc%2Fevil.jsonl"
        assert not evil.exists()

    def test_returns_zero_when_empty(self, staging_env):
        staging_mod.ensure_staging_dirs()
        assert staging_mod.promote_approved()["promoted"] == 0


class TestCrowelmWrappers:
    def test_list_staging_returns_json(self, staging_env):
        staging_mod.stage_item("agent", {"instruction": "q"})
        result = json.loads(staging_mod.crowelm_list_staging("pending"))
        assert result["count"] == 1

    def test_promote_returns_json(self, staging_env):
        staging_mod.ensure_staging_dirs()
        result = json.loads(staging_mod.crowelm_promote_approved())
        assert result["promoted"] == 0
