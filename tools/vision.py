"""
Vision tool — multi-backend image analysis with automatic fallback.

Backends: OpenRouter (free vision models), Crowe Vision (ai.southwestmushrooms.com), local (future).
"""

import base64
import json
import mimetypes
import os

import httpx

from tools.playwright_browser import browser_navigate, browser_screenshot

# Vision-capable models on OpenRouter (free tier first, paid fallback)
VISION_MODELS = [
    "google/gemini-2.0-flash-exp:free",
    "meta-llama/llama-4-scout:free",
    "openai/gpt-4o-mini",
]

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"}


def analyze_image(image_path: str, prompt: str = "Describe this image in detail", backend: str = "auto") -> str:
    """
    Analyze an image using multi-backend vision with automatic fallback.

    :param image_path: Path to the image file on disk.
    :param prompt: What to analyze about the image.
    :param backend: Vision backend — "auto", "openrouter", "crowe", or "local".
    :return: JSON with analysis results.
    :rtype: str
    """
    try:
        if not os.path.exists(image_path):
            return json.dumps({"error": f"File not found: {image_path}"})

        ext = os.path.splitext(image_path)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            return json.dumps({"error": f"Unsupported image format: {ext}. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"})

        mime_type = mimetypes.guess_type(image_path)[0] or "image/png"
        with open(image_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("utf-8")

        if backend == "auto":
            return json.dumps(_auto_analyze(image_b64, mime_type, prompt))
        elif backend == "openrouter":
            return json.dumps(_call_openrouter_vision(image_b64, mime_type, prompt))
        elif backend == "crowe":
            return json.dumps(_call_crowe_vision(image_b64, mime_type, prompt))
        elif backend == "local":
            return json.dumps({"error": "Local vision backend not yet implemented"})
        else:
            return json.dumps({"error": f"Unknown backend: {backend}"})

    except Exception as e:
        return json.dumps({"error": str(e)})


def screenshot_and_analyze(url: str, prompt: str = "Describe what you see on this page") -> str:
    """
    Navigate to a URL, take a screenshot, and analyze it with vision.

    :param url: The URL to screenshot.
    :param prompt: What to analyze about the page.
    :return: JSON with URL, screenshot path, and analysis.
    :rtype: str
    """
    try:
        nav_result = json.loads(browser_navigate(url))
        if "error" in nav_result:
            return json.dumps({"error": f"Navigation failed: {nav_result['error']}"})

        shot_result = json.loads(browser_screenshot())
        if "error" in shot_result:
            return json.dumps({"error": f"Screenshot failed: {shot_result['error']}"})

        screenshot_path = shot_result.get("path", "")
        if not screenshot_path:
            return json.dumps({"error": "Screenshot path not returned"})

        analysis_result = json.loads(analyze_image(screenshot_path, prompt))

        return json.dumps({
            "url": url,
            "screenshot_path": screenshot_path,
            "analysis": analysis_result.get("analysis", analysis_result.get("error", "No analysis")),
            "backend": analysis_result.get("backend", "unknown"),
        })

    except Exception as e:
        return json.dumps({"error": str(e)})


def _auto_analyze(image_b64: str, mime_type: str, prompt: str) -> dict:
    """Try OpenRouter first, fall back to Crowe Vision."""
    try:
        return _call_openrouter_vision(image_b64, mime_type, prompt)
    except Exception:
        pass

    try:
        return _call_crowe_vision(image_b64, mime_type, prompt)
    except Exception as e:
        return {"error": f"All vision backends failed. Last error: {e}"}


def _call_openrouter_vision(image_b64: str, mime_type: str, prompt: str) -> dict:
    """Send image to OpenRouter vision model."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    last_error = None
    for model in VISION_MODELS:
        try:
            response = httpx.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}},
                        ],
                    }],
                    "max_tokens": 2048,
                },
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()
            analysis = data["choices"][0]["message"]["content"]
            return {"backend": "openrouter", "model": model, "analysis": analysis}
        except Exception as e:
            last_error = e
            continue

    raise RuntimeError(f"All OpenRouter vision models failed. Last: {last_error}")


def _call_crowe_vision(image_b64: str, mime_type: str, prompt: str) -> dict:
    """Send image to Crowe Logic AI Vision endpoint."""
    url = os.environ.get("CROWE_LOGIC_URL", "https://ai.southwestmushrooms.com")
    key = os.environ.get("CROWE_LOGIC_KEY", "")

    headers = {}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    response = httpx.post(
        f"{url}/api/crowe-vision/analyze",
        headers=headers,
        json={"image": image_b64, "mime_type": mime_type, "prompt": prompt},
        timeout=60.0,
    )
    response.raise_for_status()
    data = response.json()
    return {"backend": "crowe", "analysis": data.get("analysis", str(data))}
