"""Tests for Azure Sora endpoint normalization."""

from providers.azure_sora import normalize_azure_video_endpoint


class TestNormalizeAzureVideoEndpoint:
    def test_accepts_openai_v1_endpoint(self):
        endpoint = "https://crowelogicos-4667-resource.openai.azure.com/openai/v1/"
        assert normalize_azure_video_endpoint(endpoint) == (
            "https://crowelogicos-4667-resource.openai.azure.com/openai/v1"
        )

    def test_converts_services_host_to_openai_host(self):
        endpoint = "https://crowelogicos-4667-resource.services.ai.azure.com"
        assert normalize_azure_video_endpoint(endpoint) == (
            "https://crowelogicos-4667-resource.openai.azure.com/openai/v1"
        )

    def test_strips_project_path_before_normalizing(self):
        endpoint = (
            "https://crowelogicos-4667-resource.services.ai.azure.com/"
            "api/projects/crowelogicos-4667"
        )
        assert normalize_azure_video_endpoint(endpoint) == (
            "https://crowelogicos-4667-resource.openai.azure.com/openai/v1"
        )
