"""
Contract tests for CroweLM pipeline infrastructure.

Human-maintained only. Agents cannot modify these.
Verify structural invariants that must hold across all pipeline operations.
"""

import json
import pytest
import tools.staging_pipeline as staging_mod
import tools.audit_log as audit_mod


@pytest.fixture
def pipeline_env(tmp_path):
    """Isolated environment for contract tests."""
    old_staging = staging_mod.STAGING_DIR
    old_logs = audit_mod.LOGS_DIR
    staging_mod.STAGING_DIR = str(tmp_path / "staging")
    audit_mod.LOGS_DIR = str(tmp_path / "logs")
    yield tmp_path
    staging_mod.STAGING_DIR = old_staging
    audit_mod.LOGS_DIR = old_logs


class TestStagingStructureContracts:
    """Staging directory structure is always preserved."""

    def test_ensure_creates_all_four_subdirs(self, pipeline_env):
        staging_mod.ensure_staging_dirs()
        staging = pipeline_env / "staging"
        assert (staging / "pending").is_dir()
        assert (staging / "approved").is_dir()
        assert (staging / "review").is_dir()
        assert (staging / "rejected").is_dir()

    def test_stage_item_only_writes_to_pending(self, pipeline_env):
        staging_mod.stage_item("agent", {"instruction": "q", "response": "a"})
        staging = pipeline_env / "staging"
        assert len(list((staging / "pending").iterdir())) == 1
        assert len(list((staging / "approved").iterdir())) == 0
        assert len(list((staging / "review").iterdir())) == 0
        assert len(list((staging / "rejected").iterdir())) == 0


class TestApprovedItemContracts:
    """Approved examples always have a quality score attached."""

    def test_approved_item_has_quality_score(self, pipeline_env):
        staged = staging_mod.stage_item("agent", {"instruction": "q", "response": "a"})
        staging_mod.apply_gate(staged["id"], 0.9)
        path = pipeline_env / "staging" / "approved" / f"{staged['id']}.jsonl"
        item = json.loads(path.read_text().strip())
        assert "quality_score" in item
        assert isinstance(item["quality_score"], (int, float))

    def test_review_item_has_quality_score(self, pipeline_env):
        staged = staging_mod.stage_item("agent", {"instruction": "q", "response": "a"})
        staging_mod.apply_gate(staged["id"], 0.7)
        path = pipeline_env / "staging" / "review" / f"{staged['id']}.jsonl"
        item = json.loads(path.read_text().strip())
        assert "quality_score" in item


class TestAuditLogContracts:
    """Audit logs always have agent_id, timestamp, action fields."""

    def test_log_entry_has_required_fields(self, pipeline_env):
        entry = audit_mod.log_action("agent-x", "test_action")
        assert "agent_id" in entry
        assert "timestamp" in entry
        assert "action" in entry
        assert isinstance(entry["timestamp"], float)

    def test_persisted_entries_are_valid_jsonl(self, pipeline_env):
        audit_mod.log_action("agent-y", "action_1")
        audit_mod.log_action("agent-y", "action_2")
        log_file = pipeline_env / "logs" / "agent-y.jsonl"
        for line in log_file.read_text().strip().split("\n"):
            entry = json.loads(line)
            assert "agent_id" in entry
            assert "timestamp" in entry
            assert "action" in entry


class TestDatasetSchemaContracts:
    """Dataset JSONL schema: instruction and response required."""

    def test_promoted_example_has_instruction_and_response(self, pipeline_env):
        staged = staging_mod.stage_item("agent", {
            "instruction": "How to grow oyster mushrooms?",
            "response": "Start with straw substrate.",
            "category": "mycology",
        })
        staging_mod.apply_gate(staged["id"], 0.9)
        staging_mod.promote_approved()
        curated = pipeline_env / "curated" / "mycology.jsonl"
        example = json.loads(curated.read_text().strip())
        assert "instruction" in example
        assert "response" in example
        assert len(example["instruction"]) > 0
        assert len(example["response"]) > 0
