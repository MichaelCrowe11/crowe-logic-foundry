"""
Video generation tools for CroweLM Motion on Azure AI Foundry.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from providers.azure_sora import AzureSoraClient


def sora_generate_video(
    prompt: str,
    output_path: str = "",
    size: str = "720x1280",
    seconds: int = 4,
    input_reference_path: str = "",
    wait_for_completion: bool = True,
    poll_interval_seconds: int = 20,
    timeout_seconds: int = 900,
    model: str = "",
) -> str:
    """
    Generate a video clip with CroweLM Motion and save it to disk.

    :param prompt: Text prompt that describes the video to generate.
    :param output_path: Optional destination path for the MP4 file.
    :param size: Output size such as "720x1280" or "1280x720".
    :param seconds: Clip duration in seconds.
    :param input_reference_path: Optional local image or video file to guide generation.
    :param wait_for_completion: If true, poll until the clip finishes and save the MP4.
    :param poll_interval_seconds: Poll interval while waiting for completion.
    :param timeout_seconds: Overall timeout while waiting for completion.
    :param model: Optional Azure deployment name override. Defaults to the
        CroweLM Motion deployment in AZURE_SORA_DEPLOYMENT_NAME or "sora-2".
    :return: JSON with job metadata and the saved output path when complete.
    :rtype: str
    """
    try:
        endpoint = os.environ.get("AZURE_SORA_ENDPOINT") or os.environ.get("AZURE_CORE_ENDPOINT", "")
        api_key = os.environ.get("AZURE_SORA_API_KEY") or os.environ.get("AZURE_CORE_API_KEY", "")
        deployment = model or os.environ.get("AZURE_SORA_DEPLOYMENT_NAME", "sora-2")

        client = AzureSoraClient(
            endpoint=endpoint,
            api_key=api_key,
            deployment_name=deployment,
            poll_interval_seconds=poll_interval_seconds,
            timeout_seconds=timeout_seconds,
        )

        target_path = output_path or _default_output_path(prompt)
        result = client.generate_to_file(
            prompt,
            target_path,
            size=size,
            seconds=seconds,
            input_reference_path=input_reference_path,
            wait_for_completion=wait_for_completion,
        )
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def _default_output_path(prompt: str) -> str:
    """Create a stable default output path under the local data directory."""
    slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")
    slug = slug[:48] or "video"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return str(Path("data/generated/videos") / f"{stamp}-{slug}.mp4")
