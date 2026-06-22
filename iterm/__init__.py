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

# Dynamic profile: iTerm2 auto-loads any JSON dropped here, no restart/API needed.
DYNAMIC_PROFILES_DIR = os.path.expanduser(
    "~/Library/Application Support/iTerm2/DynamicProfiles"
)
DYNAMIC_PROFILE_DEST = os.path.join(DYNAMIC_PROFILES_DIR, "crowe-logic.json")
PROFILE_GUID = "com.crowelogic.profile.crowe-logic"
PROFILE_NAME = "Crowe Logic"
PROFILE_FONT = "JetBrainsMono-Regular 14"
# Corner watermark of the real mark; bundled in the package, copied to a stable
# runtime path that iTerm2 reads as an absolute background-image location.
WATERMARK_SOURCE = os.path.join(ITERM_PACKAGE_DIR, "assets", "crowe-watermark.png")
WATERMARK_DEST = os.path.expanduser("~/.crowe-logic/crowe-watermark.png")

# Profile color scheme
PROFILE_COLORS = {
    "background": {
        "Red Component": 0.051,
        "Green Component": 0.051,
        "Blue Component": 0.051,
    },
    "foreground": {
        "Red Component": 0.878,
        "Green Component": 0.878,
        "Blue Component": 0.878,
    },
    "bold": {"Red Component": 1.0, "Green Component": 1.0, "Blue Component": 1.0},
    "cursor": {
        "Red Component": 0.749,
        "Green Component": 0.651,
        "Blue Component": 0.412,
    },
    "cursor_text": {
        "Red Component": 0.051,
        "Green Component": 0.051,
        "Blue Component": 0.051,
    },
    "selection": {
        "Red Component": 0.749,
        "Green Component": 0.651,
        "Blue Component": 0.412,
        "Alpha Component": 0.3,
    },
    "badge": {
        "Red Component": 0.749,
        "Green Component": 0.651,
        "Blue Component": 0.412,
        "Alpha Component": 0.15,
    },
}

# Maps our internal color names to iTerm2 profile keys.
_PROFILE_COLOR_KEYS = {
    "background": "Background Color",
    "foreground": "Foreground Color",
    "bold": "Bold Color",
    "cursor": "Cursor Color",
    "cursor_text": "Cursor Text Color",
    "selection": "Selection Color",
    "badge": "Badge Color",
}


def _color(spec: dict, alpha: float | None = None) -> dict:
    """Return an iTerm2 color dict (sRGB) from a PROFILE_COLORS entry."""
    c = {
        "Red Component": spec["Red Component"],
        "Green Component": spec["Green Component"],
        "Blue Component": spec["Blue Component"],
        "Color Space": "sRGB",
    }
    a = alpha if alpha is not None else spec.get("Alpha Component")
    if a is not None:
        c["Alpha Component"] = a
    return c


def build_profile_dict() -> dict:
    """Build the Crowe Logic dynamic-profile dict (one entry, no I/O)."""
    profile = {
        "Name": PROFILE_NAME,
        "Guid": PROFILE_GUID,
        # A sharper coding font with ligatures. Michael already has JetBrains
        # Mono installed locally; iTerm2 accepts the PostScript-style face here.
        "Normal Font": PROFILE_FONT,
        "Non Ascii Font": PROFILE_FONT,
        "Use Non-ASCII Font": False,
        "Use Ligatures": True,
        "ASCII Ligatures": True,
        # Gold vertical bar cursor + guide line so the prompt feels more like an
        # instrument panel and less like the stock Terminal block cursor. The
        # cursor color is also pulsed dynamically by apply_terminal_chrome() /
        # _start_cursor_pulse(), drifting through the crest hue range to match
        # the deepparallel spinner's color temperature.
        "Cursor Type": 1,
        "Blinking Cursor": True,
        "Show Cursor Guide": True,
        "Cursor Boost": 0.3,
        # Brand badge: a gold "CROWE LOGIC" watermark in the corner of every
        # session. Survives even when Claude Code's own header scrolls away.
        "Badge Text": "CROWE LOGIC",
        # Native status bar on (the Crowe Logic component is added once via the
        # GUI; this API/format can't place a custom component automatically).
        "Show Status Bar": True,
    }
    for name, key in _PROFILE_COLOR_KEYS.items():
        # Badge color gets a higher alpha than the faint watermark default so
        # the corner text stays legible.
        alpha = 0.55 if name == "badge" else None
        profile[key] = _color(PROFILE_COLORS[name], alpha)
    # Faded corner mark behind the text, if the watermark asset is installed.
    if os.path.exists(WATERMARK_DEST):
        profile["Background Image Location"] = WATERMARK_DEST
        profile["Background Image Mode"] = 2  # scale aspect fit (keeps corner)
        profile["Blend"] = 0.5
    return profile


def _write_dynamic_profile() -> None:
    """Write the Crowe Logic dynamic profile; iTerm2 auto-loads it live."""
    import json

    os.makedirs(DYNAMIC_PROFILES_DIR, exist_ok=True)
    # Install the watermark asset to a stable runtime path first.
    if os.path.exists(WATERMARK_SOURCE):
        os.makedirs(os.path.dirname(WATERMARK_DEST), exist_ok=True)
        shutil.copyfile(WATERMARK_SOURCE, WATERMARK_DEST)
    with open(DYNAMIC_PROFILE_DEST, "w") as f:
        json.dump({"Profiles": [build_profile_dict()]}, f, indent=2)


def _remove_dynamic_profile() -> None:
    """Remove the dynamic profile and watermark asset."""
    for path in (DYNAMIC_PROFILE_DEST, WATERMARK_DEST):
        if os.path.exists(path):
            os.remove(path)


def iterm_set_var(name: str, value: str) -> None:
    """Set an iTerm2 user variable via escape sequence.

    No-op on non-iTerm2 terminals. Safe to call unconditionally.
    """
    if os.environ.get("TERM_PROGRAM") not in ("iTerm.app", "WezTerm"):
        return
    encoded = base64.b64encode(f"{name}={value}".encode()).decode()
    sys.stdout.write(f"\033]1337;SetUserVar={encoded}\a")
    sys.stdout.flush()


def apply_terminal_chrome() -> None:
    """Apply best-effort cursor styling for the active terminal session.

    Sets a gold blinking vertical bar cursor and launches a background thread
    that periodically pulses the cursor color through the crest hue range,
    matching the deepparallel spinner's color temperature drift. Safe no-op
    for redirected output and can be disabled with
    CROWE_LOGIC_TERMINAL_CHROME=0.
    """
    if os.environ.get("CROWE_LOGIC_TERMINAL_CHROME", "").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return
    if not sys.stdout.isatty():
        return
    # OSC 12 sets cursor color; DECSCUSR 5 sets blinking vertical bar cursor.
    sys.stdout.write("\033]12;#bfa669\a\033[5 q")
    sys.stdout.flush()
    # Start the cursor pulse thread (daemon, auto-exits with the process).
    _start_cursor_pulse()


def _crest_hex(phase: float) -> str:
    """Return a hex color from the crest hue cycle (matches cli.branding._crest_color)."""
    import math

    r = 0.749 + 0.06 * math.sin(phase)
    g = 0.651 + 0.04 * math.sin(phase + 1.0)
    b = 0.412 + 0.03 * math.sin(phase + 2.0)
    r = max(0, min(1, r))
    g = max(0, min(1, g))
    b = max(0, min(1, b))
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


_cursor_pulse_thread = None
_cursor_pulse_stop = None
_cursor_pulse_paused = None


def _start_cursor_pulse():
    """Launch a daemon thread that pulses the cursor color through the crest range.

    Uses OSC 12 escape sequences to update the cursor color ~every 0.8s, drifting
    through the gold hue cycle. The thread is a daemon so it exits automatically
    when the main process ends. Only active in iTerm2/WezTerm/ghostty.

    The pulse is paused while Rich Live widgets are active (via
    pause_cursor_pulse/resume_cursor_pulse) to prevent OSC 12 escape
    sequences from racing with Live's terminal redraws — without this,
    the ESC byte gets stripped by Rich's output capture, leaving
    visible ``]12;#bb9c62`` artifacts in the rendered output.
    """
    import threading

    global _cursor_pulse_thread, _cursor_pulse_stop, _cursor_pulse_paused

    if _cursor_pulse_thread is not None:
        return  # already running

    if os.environ.get("TERM_PROGRAM") not in ("iTerm.app", "WezTerm", "ghostty"):
        return

    _cursor_pulse_stop = threading.Event()
    _cursor_pulse_paused = threading.Event()
    phase = 0.0

    def _pulse():
        nonlocal phase
        while not _cursor_pulse_stop.is_set():
            # Skip writing while a Live widget is active — the OSC
            # sequence would interleave with Rich's redraw and the
            # ESC byte would be eaten, leaving visible ]12;#hex junk.
            if not _cursor_pulse_paused.is_set():
                color = _crest_hex(phase)
                try:
                    sys.stdout.write(f"\033]12;{color}\a")
                    sys.stdout.flush()
                except (BrokenPipeError, OSError):
                    break
                phase += 0.35 * 0.8  # match hue_speed * interval
            _cursor_pulse_stop.wait(0.8)

    _cursor_pulse_thread = threading.Thread(
        target=_pulse, name="crowe-cursor-pulse", daemon=True
    )
    _cursor_pulse_thread.start()


def pause_cursor_pulse():
    """Pause the cursor pulse so OSC 12 sequences don't race with Rich Live.

    Called by StreamRenderer before starting any Live widget (spinner,
    markdown, reasoning panel, convergence flash). Idempotent and safe
    to call when the pulse thread isn't running.
    """
    if _cursor_pulse_paused is not None:
        _cursor_pulse_paused.set()


def resume_cursor_pulse():
    """Resume the cursor pulse after a Live widget has been stopped.

    Called by StreamRenderer after tearing down Live widgets. Idempotent.
    """
    if _cursor_pulse_paused is not None:
        _cursor_pulse_paused.clear()


def stop_cursor_pulse():
    """Stop the cursor pulse thread (called on clean shutdown)."""
    global _cursor_pulse_stop, _cursor_pulse_thread, _cursor_pulse_paused
    if _cursor_pulse_stop is not None:
        _cursor_pulse_stop.set()
    _cursor_pulse_thread = None
    _cursor_pulse_stop = None
    _cursor_pulse_paused = None


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
            check=True,
            capture_output=True,
        )

    # 2. Install iterm2 package into the venv
    pip_path = os.path.join(ITERM_VENV, "bin", "pip3")
    subprocess.run(
        [pip_path, "install", "--quiet", "iterm2"],
        check=True,
        capture_output=True,
    )

    # 3. Create AutoLaunch directory
    os.makedirs(AUTOLAUNCH_DIR, exist_ok=True)

    # 4. Copy daemon with correct shebang
    python_path = os.path.join(ITERM_VENV, "bin", "python3")
    _write_daemon_with_shebang(DAEMON_SOURCE, DAEMON_DEST, python_path)

    # 5. Make daemon executable
    os.chmod(DAEMON_DEST, 0o755)

    # 6. Write the Crowe Logic dynamic profile (colors, badge, watermark,
    #    status bar enabled). iTerm2 auto-loads it; no restart needed for this.
    _write_dynamic_profile()

    return True, (
        "Installed the Crowe Logic profile (colors, badge, corner watermark) and "
        "companion daemon. Restart iTerm2 to activate the daemon, then add the "
        "'Crowe Logic' status bar component once via Settings > Profiles > Crowe "
        "Logic > Session > Configure Status Bar."
    )


def _enable_python_api() -> None:
    """Enable iTerm2's Python API via defaults if not already enabled."""
    try:
        result = subprocess.run(
            ["defaults", "read", "com.googlecode.iterm2", "EnableAPIServer"],
            capture_output=True,
            text=True,
        )
        if result.stdout.strip() == "1":
            return  # already enabled
    except Exception:
        pass

    subprocess.run(
        [
            "defaults",
            "write",
            "com.googlecode.iterm2",
            "EnableAPIServer",
            "-bool",
            "true",
        ],
        capture_output=True,
    )


def _is_python_api_enabled() -> bool:
    """Check if iTerm2's Python API is enabled."""
    try:
        result = subprocess.run(
            ["defaults", "read", "com.googlecode.iterm2", "EnableAPIServer"],
            capture_output=True,
            text=True,
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

    # Remove the dynamic profile + watermark asset
    if os.path.exists(DYNAMIC_PROFILE_DEST) or os.path.exists(WATERMARK_DEST):
        _remove_dynamic_profile()
        removed_anything = True

    if not removed_anything:
        return True, "Crowe Logic iTerm2 integration is not installed."

    return True, "Uninstalled. Restart iTerm2 to complete removal."


def iterm_status() -> dict:
    """Check the status of the iTerm2 integration.

    Returns a dict with keys: iterm_detected, daemon_installed, venv_exists,
    python_api_enabled, profile_installed.
    """
    return {
        "iterm_detected": os.environ.get("TERM_PROGRAM") == "iTerm.app",
        "daemon_installed": os.path.exists(DAEMON_DEST),
        "venv_exists": os.path.exists(ITERM_VENV),
        "python_api_enabled": _is_python_api_enabled(),
        "profile_installed": os.path.exists(DYNAMIC_PROFILE_DEST),
    }
