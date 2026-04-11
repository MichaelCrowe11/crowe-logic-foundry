"""Tests for tools.video_generation."""

import json

import tools.video_generation as video_mod


class TestSoraGenerateVideo:
    def test_uses_core_env_as_fallback(self, monkeypatch, tmp_path):
        monkeypatch.setenv(
            "AZURE_CORE_ENDPOINT",
            "https://crowelogicos-4667-resource.services.ai.azure.com",
        )
        monkeypatch.setenv("AZURE_CORE_API_KEY", "test-key")
        monkeypatch.delenv("AZURE_SORA_ENDPOINT", raising=False)
        monkeypatch.delenv("AZURE_SORA_API_KEY", raising=False)
        monkeypatch.delenv("AZURE_SORA_DEPLOYMENT_NAME", raising=False)

        captured = {}

        class FakeClient:
            def __init__(self, endpoint, api_key, deployment_name, poll_interval_seconds, timeout_seconds):
                captured["endpoint"] = endpoint
                captured["api_key"] = api_key
                captured["deployment_name"] = deployment_name
                captured["poll_interval_seconds"] = poll_interval_seconds
                captured["timeout_seconds"] = timeout_seconds

            def generate_to_file(self, prompt, output_path, **kwargs):
                return {
                    "video_id": "video_123",
                    "status": "completed",
                    "output_path": output_path,
                    "prompt": prompt,
                    "options": kwargs,
                }

        monkeypatch.setattr(video_mod, "AzureSoraClient", FakeClient)

        output_path = tmp_path / "clip.mp4"
        result = json.loads(
            video_mod.sora_generate_video(
                "A cat riding a motorcycle",
                output_path=str(output_path),
                seconds=8,
            )
        )

        assert captured["endpoint"] == "https://crowelogicos-4667-resource.services.ai.azure.com"
        assert captured["api_key"] == "test-key"
        assert captured["deployment_name"] == "sora-2"
        assert result["video_id"] == "video_123"
        assert result["output_path"] == str(output_path)
        assert result["options"]["seconds"] == 8

    def test_returns_error_when_credentials_are_missing(self, monkeypatch):
        monkeypatch.delenv("AZURE_CORE_ENDPOINT", raising=False)
        monkeypatch.delenv("AZURE_CORE_API_KEY", raising=False)
        monkeypatch.delenv("AZURE_SORA_ENDPOINT", raising=False)
        monkeypatch.delenv("AZURE_SORA_API_KEY", raising=False)

        result = json.loads(video_mod.sora_generate_video("A cat"))
        assert "error" in result
