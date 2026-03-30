"""
Shell tool — execute commands locally.
"""

import json
import subprocess


def execute_shell(command: str, working_directory: str = "", timeout_seconds: int = 120) -> str:
    """
    Execute a shell command and return stdout, stderr, and exit code.
    Commands run in a bash shell with a configurable timeout.

    :param command: The shell command to execute.
    :param working_directory: Directory to run the command in (default: home dir).
    :param timeout_seconds: Max execution time in seconds (default 120, max 600).
    :return: JSON with stdout, stderr, and return_code.
    :rtype: str
    """
    import os

    cwd = working_directory or os.path.expanduser("~")
    timeout_seconds = min(timeout_seconds, 600)

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout_seconds,
            env={**os.environ, "TERM": "dumb"},
        )
        stdout = result.stdout
        if len(stdout) > 50000:
            stdout = stdout[:50000] + "\n... (output truncated at 50KB)"

        return json.dumps({
            "stdout": stdout,
            "stderr": result.stderr[:10000] if result.stderr else "",
            "return_code": result.returncode,
        })
    except subprocess.TimeoutExpired:
        return json.dumps({"error": f"Command timed out after {timeout_seconds}s", "return_code": -1})
    except Exception as e:
        return json.dumps({"error": str(e), "return_code": -1})
