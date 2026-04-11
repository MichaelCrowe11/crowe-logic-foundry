"""
CroweLM Motion client for the Azure OpenAI v1 video API.

Accepts a few Azure endpoint shapes and normalizes them to the OpenAI-
compatible `/openai/v1` base URL used by the underlying `sora-2` deployment.
"""

from __future__ import annotations

import mimetypes
import time
from pathlib import Path

import httpx


ACTIVE_VIDEO_STATUSES = {
    "queued",
    "in_progress",
    "preprocessing",
    "running",
    "processing",
}
SUCCESS_VIDEO_STATUSES = {"completed", "succeeded"}
FAILED_VIDEO_STATUSES = {"failed", "cancelled", "canceled"}


def normalize_azure_video_endpoint(endpoint: str) -> str:
    """Normalize Azure video endpoint shapes to an `/openai/v1` base URL."""
    base_url = (endpoint or "").strip()
    if not base_url:
        raise ValueError("CroweLM Motion endpoint is not configured")

    base_url = base_url.split("?", 1)[0].rstrip("/")

    for suffix in ("/videos", "/content", "/video/generations/jobs"):
        if base_url.endswith(suffix):
            base_url = base_url[: -len(suffix)]

    if "/api/projects/" in base_url:
        base_url = base_url.split("/api/projects/", 1)[0]

    if base_url.endswith("/openai/v1"):
        return base_url

    if base_url.endswith("/openai"):
        return f"{base_url}/v1"

    if ".services.ai.azure.com" in base_url:
        base_url = base_url.replace(".services.ai.azure.com", ".openai.azure.com")

    if "/openai/v1" in base_url:
        return base_url

    return f"{base_url}/openai/v1"


def _extract_video(payload: dict) -> dict:
    """Accept either a single video object or list-wrapper response."""
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        if not payload["data"]:
            raise ValueError("Video API returned an empty data list")
        first = payload["data"][0]
        if isinstance(first, dict) and first.get("id"):
            return first

    if isinstance(payload, dict) and payload.get("id"):
        return payload

    raise ValueError(f"Unexpected video API response: {payload!r}")


class AzureSoraClient:
    """Minimal client for CroweLM Motion video generation."""

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        deployment_name: str = "sora-2",
        poll_interval_seconds: int = 20,
        timeout_seconds: int = 900,
    ) -> None:
        if not api_key.strip():
            raise ValueError("CroweLM Motion API key is not configured")

        self.base_url = normalize_azure_video_endpoint(endpoint)
        self.api_key = api_key.strip()
        self.deployment_name = (deployment_name or "sora-2").strip()
        self.poll_interval_seconds = max(1, int(poll_interval_seconds))
        self.timeout_seconds = max(self.poll_interval_seconds, int(timeout_seconds))
        self.request_timeout = httpx.Timeout(120.0, connect=30.0)

    def generate_to_file(
        self,
        prompt: str,
        output_path: str,
        *,
        size: str = "720x1280",
        seconds: int = 4,
        input_reference_path: str = "",
        wait_for_completion: bool = True,
    ) -> dict:
        """Create a video job, optionally wait for completion, and save output."""
        video = self.create_video(
            prompt,
            size=size,
            seconds=seconds,
            input_reference_path=input_reference_path,
        )

        result = {
            "video_id": video["id"],
            "status": video.get("status", "queued"),
            "model": video.get("model", self.deployment_name),
            "size": video.get("size", size),
            "seconds": video.get("seconds", str(seconds)),
            "endpoint": self.base_url,
        }

        if not wait_for_completion:
            return result

        final_video = self.poll_video(video["id"])
        content = self.download_video(video["id"])

        output_file = Path(output_path).expanduser()
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_bytes(content)

        result.update({
            "status": final_video.get("status", "completed"),
            "progress": final_video.get("progress"),
            "output_path": str(output_file),
            "expires_at": final_video.get("expires_at"),
        })
        return result

    def create_video(
        self,
        prompt: str,
        *,
        size: str = "720x1280",
        seconds: int = 4,
        input_reference_path: str = "",
    ) -> dict:
        """Submit a text-to-video or local-reference video job."""
        url = f"{self.base_url}/videos"

        with httpx.Client(timeout=self.request_timeout, follow_redirects=True) as client:
            if input_reference_path:
                ref_path = Path(input_reference_path).expanduser()
                if not ref_path.exists():
                    raise FileNotFoundError(f"Input reference not found: {ref_path}")

                mime_type = mimetypes.guess_type(ref_path.name)[0] or "application/octet-stream"
                with ref_path.open("rb") as handle:
                    response = client.post(
                        url,
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        data={
                            "model": self.deployment_name,
                            "prompt": prompt,
                            "size": size,
                            "seconds": str(seconds),
                        },
                        files={"input_reference": (ref_path.name, handle, mime_type)},
                    )
            else:
                response = client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.deployment_name,
                        "prompt": prompt,
                        "size": size,
                        "seconds": str(seconds),
                    },
                )

        response.raise_for_status()
        return _extract_video(response.json())

    def poll_video(self, video_id: str) -> dict:
        """Poll until the video reaches a terminal state."""
        started = time.monotonic()

        while True:
            video = self.retrieve_video(video_id)
            status = (video.get("status") or "").lower()

            if status in SUCCESS_VIDEO_STATUSES:
                return video

            if status in FAILED_VIDEO_STATUSES:
                detail = video.get("error") or video.get("failure_reason") or "unknown error"
                raise RuntimeError(f"CroweLM Motion video generation failed: {detail}")

            if status not in ACTIVE_VIDEO_STATUSES:
                raise RuntimeError(f"Unexpected CroweLM Motion video status: {status or 'missing'}")

            if time.monotonic() - started >= self.timeout_seconds:
                raise TimeoutError(
                    f"CroweLM Motion video generation timed out after {self.timeout_seconds} seconds"
                )

            time.sleep(self.poll_interval_seconds)

    def retrieve_video(self, video_id: str) -> dict:
        """Fetch the latest metadata for a video generation job."""
        url = f"{self.base_url}/videos/{video_id}"
        response = httpx.get(
            url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.request_timeout,
            follow_redirects=True,
        )
        response.raise_for_status()
        return _extract_video(response.json())

    def download_video(self, video_id: str) -> bytes:
        """Download the rendered MP4 bytes for a completed video."""
        urls = (
            f"{self.base_url}/videos/{video_id}/content",
            f"{self.base_url}/videos/{video_id}/content/video",
        )

        last_error: Exception | None = None
        for url in urls:
            try:
                response = httpx.get(
                    url,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    params={"variant": "video"},
                    timeout=self.request_timeout,
                    follow_redirects=True,
                )
                response.raise_for_status()
                return response.content
            except httpx.HTTPError as exc:
                last_error = exc

        raise RuntimeError(f"Unable to download CroweLM Motion video content: {last_error}")
