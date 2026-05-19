"""Tests for tools.crowe_logic_ai — Crowe Logic platform client."""

import json
from unittest.mock import patch
from tools.crowe_logic_ai import crowe_chat, crowe_vision, crowe_grow_log, crowe_generate_sop


class TestCroweAiChat:
    @patch("tools.crowe_logic_ai._crowe_request")
    def test_chat_sends_message(self, mock_req):
        mock_req.return_value = {"response": "Shiitake grow best on hardwood."}
        result = json.loads(crowe_chat("How do I grow shiitake?"))
        assert "response" in result
        mock_req.assert_called_once()

    def test_chat_returns_error_on_failure(self):
        with patch("tools.crowe_logic_ai._crowe_request", side_effect=Exception("connection refused")):
            result = json.loads(crowe_chat("test"))
            assert "error" in result


class TestCroweAiVision:
    @patch("tools.crowe_logic_ai._crowe_request")
    def test_vision_sends_image(self, mock_req, tmp_path):
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        mock_req.return_value = {"analysis": "A healthy mycelium colony"}
        result = json.loads(crowe_vision(str(img), "What do you see?"))
        assert "analysis" in result

    def test_vision_returns_error_for_missing_file(self):
        result = json.loads(crowe_vision("/nonexistent.png"))
        assert "error" in result


class TestCroweAiGrowLog:
    @patch("tools.crowe_logic_ai._crowe_request")
    def test_list_grow_logs(self, mock_req):
        mock_req.return_value = {"logs": [{"id": 1, "species": "shiitake"}]}
        result = json.loads(crowe_grow_log("list"))
        assert "logs" in result

    @patch("tools.crowe_logic_ai._crowe_request")
    def test_create_grow_log(self, mock_req):
        mock_req.return_value = {"created": True, "id": 42}
        result = json.loads(crowe_grow_log("create", '{"species": "lions mane"}'))
        assert result["created"] is True


class TestCroweAiSop:
    @patch("tools.crowe_logic_ai._crowe_request")
    def test_generate_sop(self, mock_req):
        mock_req.return_value = {"sop": "Standard Operating Procedure for substrate prep..."}
        result = json.loads(crowe_generate_sop("substrate preparation"))
        assert "sop" in result
