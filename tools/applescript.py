"""
AppleScript tool — macOS automation via osascript.
"""

import json
import subprocess


def run_applescript(script: str) -> str:
    """
    Execute an AppleScript and return the result. Can automate any macOS
    application — Finder, Safari, Terminal, System Events, etc.

    :param script: The AppleScript code to execute.
    :return: JSON with the script output or error.
    :rtype: str
    """
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return json.dumps({
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip() if result.stderr else "",
            "return_code": result.returncode,
        })
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "AppleScript timed out after 30s"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def open_application(app_name: str) -> str:
    """
    Open a macOS application by name. Works with any installed app.

    :param app_name: Name of the application (e.g. "Ableton Live", "Safari", "Terminal").
    :return: JSON confirmation.
    :rtype: str
    """
    script = f'tell application "{app_name}" to activate'
    return run_applescript(script)


def send_notification(title: str, message: str) -> str:
    """
    Send a macOS notification with a title and message.

    :param title: The notification title.
    :param message: The notification body text.
    :return: JSON confirmation.
    :rtype: str
    """
    script = f'display notification "{message}" with title "{title}"'
    return run_applescript(script)
