"""
NemoClaw sandbox tools.

Talon's hybrid deployment: NIM inference on NVIDIA's hosted endpoint
(``integrate.api.nvidia.com``, shared with other CroweLM NIM tiers via
NVIDIA_NIM_ENDPOINT + NVIDIA_API_KEY) and OpenShell execution on a
self-hosted Brev VM (wired here via NEMOCLAW_SANDBOX_URL). The two halves
are independent: inference is always on, sandbox is optional, and neither
falls back to the other.

This module owns only the sandbox side: shell execution and health
checks that run inside the OpenShell isolation boundary instead of on the
operator's host.

Env contract:
    NEMOCLAW_SANDBOX_URL          Base URL of the OpenShell sandbox
                                  (e.g. https://<vm>.brevlab.com). No
                                  default; when unset, legacy NEMOCLAW_ENDPOINT
                                  is accepted for older deployments.
    NEMOCLAW_API_KEY              Bearer token for the sandbox (often the
                                  Brev access token). Unset sends no auth
                                  header, which works for VMs that expose
                                  OpenShell behind Brev port auth only.
    NEMOCLAW_SANDBOX_EXEC_PATH    Path for exec (default /openshell/v1/exec).
                                  If your NemoClaw build serves OpenShell at a
                                  different path, set this after running
                                  scripts/nemoclaw_recon.sh on the VM.
    NEMOCLAW_SANDBOX_HEALTH_PATH  Path for health (default /openshell/v1/health).
    NEMOCLAW_SANDBOX_TIMEOUT      HTTP-level timeout floor in seconds (default 180).

Response schema (mirrors tools/shell.py::execute_shell so prompts are stable):
    {"stdout": str, "stderr": str, "return_code": int, "sandbox": "nemoclaw"}
    {"error": str, "return_code": -1, "endpoint": str}
"""

import json
import os

try:
    import httpx
except ImportError:  # httpx is already a project dep; keep the guard for clarity.
    httpx = None


DEFAULT_EXEC_PATH = "/openshell/v1/exec"
DEFAULT_HEALTH_PATH = "/openshell/v1/health"


def _sandbox_base_url() -> str:
    # Prefer the explicit sandbox URL. Keep NEMOCLAW_ENDPOINT as a legacy
    # fallback for older VM deployments and tests that used one URL for both
    # OpenAI-compatible inference and OpenShell.
    return (
        os.environ.get("NEMOCLAW_SANDBOX_URL", "")
        or os.environ.get("NEMOCLAW_ENDPOINT", "")
    ).rstrip("/")


def _auth_headers() -> dict:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    key = os.environ.get("NEMOCLAW_API_KEY", "")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _http_timeout_floor() -> float:
    try:
        return float(os.environ.get("NEMOCLAW_SANDBOX_TIMEOUT", "180"))
    except ValueError:
        return 180.0


def nemoclaw_health() -> str:
    """
    Check whether the NemoClaw sandbox is reachable and ready.

    :return: JSON with reachable, status_code, and body.
    :rtype: str
    """
    base = _sandbox_base_url()
    if not base:
        return json.dumps({
            "reachable": False,
            "error": "NEMOCLAW_SANDBOX_URL not set",
        })
    if httpx is None:
        return json.dumps({"reachable": False, "error": "httpx not installed"})

    path = os.environ.get("NEMOCLAW_SANDBOX_HEALTH_PATH", DEFAULT_HEALTH_PATH)
    url = base + path
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(url, headers=_auth_headers())
        return json.dumps({
            "reachable": response.status_code < 500,
            "status_code": response.status_code,
            "body": response.text[:2000],
            "url": url,
        })
    except Exception as exc:
        return json.dumps({
            "reachable": False,
            "error": f"{type(exc).__name__}: {exc}",
            "url": url,
        })


def nemoclaw_shell(command: str, working_directory: str = "", timeout_seconds: int = 120) -> str:
    """
    Execute a shell command inside the NemoClaw OpenShell sandbox.

    Use this when the agent needs isolation from the operator's filesystem.
    Response schema matches tools/shell.py::execute_shell so agent prompts
    do not need to branch on which shell ran.

    :param command: Shell command to execute inside the sandbox.
    :param working_directory: Working directory inside the sandbox (default: sandbox default).
    :param timeout_seconds: Max execution time in seconds (default 120, max 600).
    :return: JSON with stdout, stderr, return_code, sandbox.
    :rtype: str
    """
    base = _sandbox_base_url()
    if not base:
        return json.dumps({
            "error": "NEMOCLAW_SANDBOX_URL not set in environment.",
            "return_code": -1,
        })
    if httpx is None:
        return json.dumps({
            "error": "httpx is required for NemoClaw sandbox calls (pip install httpx).",
            "return_code": -1,
        })

    timeout_seconds = max(1, min(timeout_seconds, 600))
    path = os.environ.get("NEMOCLAW_SANDBOX_EXEC_PATH", DEFAULT_EXEC_PATH)
    url = base + path
    payload = {"command": command, "timeout_seconds": timeout_seconds}
    if working_directory:
        payload["working_directory"] = working_directory

    # HTTP-level timeout sits above the execution timeout so the sandbox has
    # headroom to return a well-formed timeout response before httpx cuts the
    # socket.
    http_timeout = max(_http_timeout_floor(), timeout_seconds + 30)

    try:
        with httpx.Client(timeout=http_timeout) as client:
            response = client.post(url, headers=_auth_headers(), json=payload)
    except httpx.TimeoutException as exc:
        return json.dumps({
            "error": f"Sandbox request timed out: {exc}",
            "return_code": -1,
            "endpoint": url,
        })
    except Exception as exc:
        return json.dumps({
            "error": f"Sandbox transport error: {type(exc).__name__}: {exc}",
            "return_code": -1,
            "endpoint": url,
        })

    if response.status_code == 404:
        return json.dumps({
            "error": (
                f"Sandbox exec path {path} returned 404. The NemoClaw build on "
                "this VM may expose OpenShell at a different path. Run "
                "scripts/nemoclaw_recon.sh on the VM to discover the correct "
                "path and set NEMOCLAW_SANDBOX_EXEC_PATH."
            ),
            "return_code": -1,
            "endpoint": url,
        })

    if response.status_code >= 400:
        return json.dumps({
            "error": f"Sandbox returned HTTP {response.status_code}",
            "body": response.text[:2000],
            "return_code": -1,
            "endpoint": url,
        })

    try:
        data = response.json()
    except Exception:
        return json.dumps({
            "error": "Sandbox returned non-JSON body",
            "body": response.text[:2000],
            "return_code": -1,
            "endpoint": url,
        })

    stdout = data.get("stdout", data.get("output", ""))
    stderr = data.get("stderr", "")
    return_code = data.get("exit_code", data.get("return_code", 0))

    if isinstance(stdout, str) and len(stdout) > 50000:
        stdout = stdout[:50000] + "\n... (output truncated at 50KB)"
    if isinstance(stderr, str) and len(stderr) > 10000:
        stderr = stderr[:10000]

    return json.dumps({
        "stdout": stdout,
        "stderr": stderr,
        "return_code": return_code,
        "sandbox": "nemoclaw",
    })
