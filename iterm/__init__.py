"""
Crowe Logic -- iTerm2 Native Integration

Provides the user variable bridge (escape sequences) and install/uninstall
commands for the companion daemon. The CLI uses iterm_set_var() to communicate
session state to the daemon. Non-iTerm2 terminals are handled gracefully (no-op).
"""

import os
import sys
import base64
import shutil
import subprocess

# Paths
ITERM_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
DAEMON_SOURCE = os.path.join(ITERM_PACKAGE_DIR, "daemon.py")
DAEMON_DEST = os.path.expanduser(
    "~/Library/Application Support/iTerm2/Scripts/AutoLaunch/crowe-logic-daemon.py"
)
AUTOLAUNCH_DIR = os.path.dirname(DAEMON_DEST)
ITERM_VENV = os.path.expanduser("~/.crowe-logic/iterm-env")

# Profile color scheme
PROFILE_COLORS = {
    "background": {"Red Component": 0.051, "Green Component": 0.051, "Blue Component": 0.051},
    "foreground": {"Red Component": 0.878, "Green Component": 0.878, "Blue Component": 0.878},
    "bold": {"Red Component": 1.0, "Green Component": 1.0, "Blue Component": 1.0},
    "cursor": {"Red Component": 0.749, "Green Component": 0.651, "Blue Component": 0.412},
    "cursor_text": {"Red Component": 0.051, "Green Component": 0.051, "Blue Component": 0.051},
    "selection": {"Red Component": 0.749, "Green Component": 0.651, "Blue Component": 0.412, "Alpha Component": 0.3},
    "badge": {"Red Component": 0.749, "Green Component": 0.651, "Blue Component": 0.412, "Alpha Component": 0.15},
}


def iterm_set_var(name: str, value: str) -> None:
    """Set an iTerm2 user variable via escape sequence.

    No-op on non-iTerm2 terminals. Safe to call unconditionally.
    """
    if os.environ.get("TERM_PROGRAM") not in ("iTerm.app", "WezTerm"):
        return
    encoded = base64.b64encode(f"{name}={value}".encode()).decode()
    sys.stdout.write(f"\033]1337;SetUserVar={encoded}\a")
    sys.stdout.flush()


def install_iterm() -> tuple[bool, str]:
    """Install the iTerm2 companion daemon and Crowe Logic profile.

    Returns (success: bool, message: str).
    """
    if os.environ.get("TERM_PROGRAM") != "iTerm.app":
        return False, "Not running in iTerm2. Install must be run from iTerm2."

    # 0. Enable Python API programmatically (requires restart to take effect)
    _enable_python_api()

    # 1. Create the isolated venv for the daemon
    if not os.path.exists(ITERM_VENV):
        os.makedirs(os.path.dirname(ITERM_VENV), exist_ok=True)
        subprocess.run(
            [sys.executable, "-m", "venv", ITERM_VENV],
            check=True, capture_output=True,
        )

    # 2. Install iterm2 package into the venv
    pip_path = os.path.join(ITERM_VENV, "bin", "pip3")
    subprocess.run(
        [pip_path, "install", "--quiet", "iterm2"],
        check=True, capture_output=True,
    )

    # 3. Create AutoLaunch directory
    os.makedirs(AUTOLAUNCH_DIR, exist_ok=True)

    # 4. Copy daemon with correct shebang
    python_path = os.path.join(ITERM_VENV, "bin", "python3")
    _write_daemon_with_shebang(DAEMON_SOURCE, DAEMON_DEST, python_path)

    # 5. Make daemon executable
    os.chmod(DAEMON_DEST, 0o755)

    return True, "Installed. Restart iTerm2 to activate the Crowe Logic integration."


def _enable_python_api() -> None:
    """Enable iTerm2's Python API via defaults if not already enabled."""
    try:
        result = subprocess.run(
            ["defaults", "read", "com.googlecode.iterm2", "EnableAPIServer"],
            capture_output=True, text=True,
        )
        if result.stdout.strip() == "1":
            return  # already enabled
    except Exception:
        pass

    subprocess.run(
        ["defaults", "write", "com.googlecode.iterm2", "EnableAPIServer", "-bool", "true"],
        capture_output=True,
    )


def _is_python_api_enabled() -> bool:
    """Check if iTerm2's Python API is enabled."""
    try:
        result = subprocess.run(
            ["defaults", "read", "com.googlecode.iterm2", "EnableAPIServer"],
            capture_output=True, text=True,
        )
        return result.stdout.strip() == "1"
    except Exception:
        return False


def _write_daemon_with_shebang(source: str, dest: str, python_path: str) -> None:
    """Copy daemon.py to dest, replacing the shebang with the venv python path."""
    with open(source, "r") as f:
        content = f.read()

    # Replace placeholder shebang
    if content.startswith("#!"):
        first_newline = content.index("\n")
        content = content[first_newline:]

    shebang = f"#!{python_path}\n"
    with open(dest, "w") as f:
        f.write(shebang + content)


def uninstall_iterm() -> tuple[bool, str]:
    """Remove the iTerm2 companion daemon.

    Returns (success: bool, message: str).
    """
    removed_anything = False

    # Remove daemon
    if os.path.exists(DAEMON_DEST):
        os.remove(DAEMON_DEST)
        removed_anything = True

    # Remove venv (optional cleanup)
    if os.path.exists(ITERM_VENV):
        shutil.rmtree(ITERM_VENV)
        removed_anything = True

    if not removed_anything:
        return True, "Crowe Logic iTerm2 integration is not installed."

    return True, "Uninstalled. Restart iTerm2 to complete removal."


def iterm_status() -> dict:
    """Check the status of the iTerm2 integration.

    Returns a dict with keys: iterm_detected, daemon_installed, venv_exists, python_api_enabled.
    """
    return {
        "iterm_detected": os.environ.get("TERM_PROGRAM") == "iTerm.app",
        "daemon_installed": os.path.exists(DAEMON_DEST),
        "venv_exists": os.path.exists(ITERM_VENV),
        "python_api_enabled": _is_python_api_enabled(),
    }
