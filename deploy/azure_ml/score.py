"""
Azure ML Managed Online Endpoint — vLLM Scoring Script for GLM 5.1

This script launches a vLLM OpenAI-compatible server when the endpoint
starts and proxies incoming requests to it. Azure ML health probes hit
the /health route; inference requests go to /v1/chat/completions.

The Azure ML runtime calls init() once at container startup and then
routes HTTP requests to run() for each inference call. Because vLLM
already owns the HTTP server, we start it in a background thread and
use httpx to proxy requests rather than reimplementing the OpenAI API.
"""

import json
import os
import subprocess
import threading
import time

import httpx

# ─── Configuration (all overridable via environment variables) ─────────────
_MODEL_ID = os.environ.get("MODEL_ID", "THUDM/GLM-5.1")
_PORT = int(os.environ.get("VLLM_PORT", "8000"))
_MAX_MODEL_LEN = os.environ.get("MAX_MODEL_LEN", "32768")
_TENSOR_PARALLEL_SIZE = os.environ.get("TENSOR_PARALLEL_SIZE", "2")
_GPU_MEMORY_UTILIZATION = os.environ.get("GPU_MEMORY_UTILIZATION", "0.92")
_DTYPE = os.environ.get("DTYPE", "bfloat16")

_VLLM_BASE = f"http://localhost:{_PORT}"
_STARTUP_TIMEOUT = int(os.environ.get("VLLM_STARTUP_TIMEOUT", "300"))

_vllm_process: subprocess.Popen | None = None


def _start_vllm() -> None:
    """Launch the vLLM OpenAI-compatible server as a subprocess."""
    global _vllm_process
    cmd = [
        "python", "-m", "vllm.entrypoints.openai.api_server",
        "--model", _MODEL_ID,
        "--port", str(_PORT),
        "--max-model-len", _MAX_MODEL_LEN,
        "--tensor-parallel-size", _TENSOR_PARALLEL_SIZE,
        "--gpu-memory-utilization", _GPU_MEMORY_UTILIZATION,
        "--dtype", _DTYPE,
        "--trust-remote-code",
        "--served-model-name", "FW-GLM-5.1",
    ]
    _vllm_process = subprocess.Popen(cmd)


def _wait_for_vllm(timeout: int = _STARTUP_TIMEOUT) -> None:
    """Block until the vLLM server is accepting requests or timeout expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"{_VLLM_BASE}/health", timeout=5)
            if r.status_code == 200:
                print(f"[score.py] vLLM server ready at {_VLLM_BASE}")
                return
        except Exception:
            pass
        time.sleep(5)
    raise RuntimeError(f"vLLM server did not start within {timeout}s")


def init() -> None:
    """Called once by Azure ML at container startup."""
    print(f"[score.py] Starting vLLM server for {_MODEL_ID}…")
    t = threading.Thread(target=_start_vllm, daemon=True)
    t.start()
    _wait_for_vllm()
    print("[score.py] init() complete — endpoint ready")


def run(raw_data: str) -> str:
    """
    Called by Azure ML for each inference request.

    Accepts an OpenAI-compatible chat completions request body and proxies
    it to the local vLLM server, returning the raw JSON response.
    """
    try:
        payload = json.loads(raw_data)
    except (json.JSONDecodeError, TypeError) as exc:
        return json.dumps({"error": {"message": f"Invalid JSON: {exc}", "type": "invalid_request_error"}})

    # Always target the served model name, regardless of what the caller sent
    payload.setdefault("model", "FW-GLM-5.1")

    path = "/v1/chat/completions"
    stream = payload.get("stream", False)

    try:
        with httpx.Client(timeout=120) as client:
            resp = client.post(f"{_VLLM_BASE}{path}", json=payload)
            if stream:
                # For streaming, return the raw SSE bytes as a string
                return resp.text
            return resp.text
    except httpx.RequestError as exc:
        return json.dumps({
            "error": {
                "message": f"vLLM proxy error: {exc}",
                "type": "internal_server_error",
            }
        })
