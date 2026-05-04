"""Tests for config.telemetry — structured JSON-lines logging."""

import json
import os
import tempfile

import pytest

from config.telemetry import Telemetry, _safe_args


@pytest.fixture
def tmp_telemetry(tmp_path):
    """Create a Telemetry instance pointing at a temp directory."""
    return Telemetry(log_dir=str(tmp_path))


def _read_lines(t: Telemetry) -> list[dict]:
    """Read all log lines from the telemetry file."""
    path = t._path
    if not path.exists():
        return []
    lines = path.read_text().strip().splitlines()
    return [json.loads(line) for line in lines]


class TestToolCallLogging:
    def test_basic_tool_call(self, tmp_telemetry):
        tmp_telemetry.log_tool_call("web_search", {"query": "test"}, 142, True)
        records = _read_lines(tmp_telemetry)
        assert len(records) == 1
        r = records[0]
        assert r["type"] == "tool_call"
        assert r["name"] == "web_search"
        assert r["duration_ms"] == 142
        assert r["success"] is True
        assert r["error"] is None
        assert "ts" in r
        assert "epoch" in r

    def test_failed_tool_call(self, tmp_telemetry):
        tmp_telemetry.log_tool_call(
            "browse_url", {"url": "http://x"}, 50, False, error="TimeoutError"
        )
        records = _read_lines(tmp_telemetry)
        assert records[0]["success"] is False
        assert records[0]["error"] == "TimeoutError"

    def test_string_args_parsed(self, tmp_telemetry):
        tmp_telemetry.log_tool_call("test", '{"key": "val"}', 10, True)
        records = _read_lines(tmp_telemetry)
        assert records[0]["args"] == {"key": "val"}

    def test_multiple_calls_append(self, tmp_telemetry):
        for i in range(5):
            tmp_telemetry.log_tool_call(f"tool_{i}", None, i * 10, True)
        records = _read_lines(tmp_telemetry)
        assert len(records) == 5
        assert [r["name"] for r in records] == [f"tool_{i}" for i in range(5)]


class TestModelCallLogging:
    def test_basic_model_call(self, tmp_telemetry):
        tmp_telemetry.log_model_call(
            model="gpt-5.4",
            provider="azure_openai",
            tokens_in=500,
            tokens_out=1200,
            duration_ms=3400,
            ttft_ms=280,
        )
        records = _read_lines(tmp_telemetry)
        assert len(records) == 1
        r = records[0]
        assert r["type"] == "model_call"
        assert r["model"] == "gpt-5.4"
        assert r["provider"] == "azure_openai"
        assert r["tokens_in"] == 500
        assert r["tokens_out"] == 1200
        assert r["duration_ms"] == 3400
        assert r["ttft_ms"] == 280
        assert r["fallback_from"] is None

    def test_fallback_recorded(self, tmp_telemetry):
        tmp_telemetry.log_model_call(
            "claude-sonnet", "anthropic", fallback_from="gpt-5.4"
        )
        records = _read_lines(tmp_telemetry)
        assert records[0]["fallback_from"] == "gpt-5.4"


class TestEventLogging:
    def test_basic_event(self, tmp_telemetry):
        tmp_telemetry.log_event("session_start", {"model": "gpt-5.4"})
        records = _read_lines(tmp_telemetry)
        assert records[0]["type"] == "event"
        assert records[0]["category"] == "session_start"
        assert records[0]["data"]["model"] == "gpt-5.4"

    def test_event_no_data(self, tmp_telemetry):
        tmp_telemetry.log_event("heartbeat")
        records = _read_lines(tmp_telemetry)
        assert records[0]["data"] == {}


class TestDisableEnable:
    def test_disabled_no_writes(self, tmp_telemetry):
        tmp_telemetry.disable()
        tmp_telemetry.log_tool_call("test", None, 10, True)
        assert _read_lines(tmp_telemetry) == []

    def test_reenable(self, tmp_telemetry):
        tmp_telemetry.disable()
        tmp_telemetry.log_tool_call("test1", None, 10, True)
        tmp_telemetry.enable()
        tmp_telemetry.log_tool_call("test2", None, 10, True)
        records = _read_lines(tmp_telemetry)
        assert len(records) == 1
        assert records[0]["name"] == "test2"


class TestRotation:
    def test_rotation_at_threshold(self, tmp_telemetry):
        # Write enough data to exceed a very small threshold
        tmp_telemetry._path.parent.mkdir(parents=True, exist_ok=True)
        # Manually set a tiny threshold for testing
        import config.telemetry as mod
        original = mod._MAX_FILE_BYTES
        mod._MAX_FILE_BYTES = 100  # 100 bytes

        try:
            # Write enough to trigger rotation
            for i in range(10):
                tmp_telemetry.log_tool_call(f"tool_{i}", {"data": "x" * 50}, i, True)

            # Check that a rotated file exists
            files = list(tmp_telemetry._dir.glob("telemetry.*.jsonl"))
            assert len(files) >= 1, "Expected at least one rotated file"
        finally:
            mod._MAX_FILE_BYTES = original


class TestSafeArgs:
    def test_none(self):
        assert _safe_args(None) is None

    def test_dict_passthrough(self):
        assert _safe_args({"key": "val"}) == {"key": "val"}

    def test_long_string_truncated(self):
        result = _safe_args("x" * 3000)
        assert len(result) == 2000

    def test_long_dict_value_truncated(self):
        result = _safe_args({"big": "x" * 1000})
        assert "truncated" in result["big"]
        assert len(result["big"]) < 600

    def test_json_string_parsed(self):
        result = _safe_args('{"a": 1}')
        assert result == {"a": 1}

    def test_invalid_json_string(self):
        result = _safe_args("not json")
        assert result == "not json"
