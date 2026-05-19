"""Tests for tools.vision — multi-backend image analysis."""

import json
from unittest.mock import patch
from tools.vision import analyze_image, screenshot_and_analyze, VISION_MODELS


class TestAnalyzeImage:
    def test_returns_error_for_missing_file(self):
        result = json.loads(analyze_image("/nonexistent/image.png"))
        assert "error" in result

    def test_returns_error_for_unsupported_format(self, tmp_path):
        bad_file = tmp_path / "test.xyz"
        bad_file.write_text("not an image")
        result = json.loads(analyze_image(str(bad_file)))
        assert "error" in result

    @patch("tools.vision._call_openrouter_vision")
    def test_auto_backend_tries_openrouter_first(self, mock_or, tmp_path):
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        mock_or.return_value = {"backend": "openrouter", "analysis": "a cat"}
        result = json.loads(analyze_image(str(img), backend="auto"))
        assert result["analysis"] == "a cat"
        mock_or.assert_called_once()

    @patch("tools.vision._call_openrouter_vision", side_effect=Exception("rate limited"))
    @patch("tools.vision._call_crowe_vision")
    def test_auto_falls_back_to_crowe(self, mock_crowe, mock_or, tmp_path):
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
        mock_crowe.return_value = {"backend": "crowe", "analysis": "a mushroom"}
        result = json.loads(analyze_image(str(img), backend="auto"))
        assert result["backend"] == "crowe"
        assert result["analysis"] == "a mushroom"

    @patch("tools.vision._call_openrouter_vision")
    def test_explicit_openrouter_backend(self, mock_or, tmp_path):
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        mock_or.return_value = {"backend": "openrouter", "analysis": "result"}
        result = json.loads(analyze_image(str(img), backend="openrouter"))
        mock_or.assert_called_once()

    def test_vision_models_list_is_not_empty(self):
        assert len(VISION_MODELS) >= 2


class TestScreenshotAndAnalyze:
    @patch("tools.vision.analyze_image")
    @patch("tools.vision.browser_screenshot")
    @patch("tools.vision.browser_navigate")
    def test_combines_screenshot_and_analysis(self, mock_nav, mock_shot, mock_analyze):
        mock_nav.return_value = json.dumps({"url": "https://example.com"})
        mock_shot.return_value = json.dumps({"path": "/tmp/shot.png"})
        mock_analyze.return_value = json.dumps({"analysis": "a webpage"})
        result = json.loads(screenshot_and_analyze("https://example.com"))
        assert "analysis" in result
        mock_nav.assert_called_once()
        mock_shot.assert_called_once()
