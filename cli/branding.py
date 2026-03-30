"""
Crowe Logic CLI — Branding & Terminal Art
"""

import os
import sys
import shutil
import subprocess

# ── Colors ────────────────────────────────────────────────────
GOLD = "\033[38;2;191;166;105m"
GOLD_BG = "\033[48;2;191;166;105m"
WHITE = "\033[97m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

# ── Dimensions ────────────────────────────────────────────────
def _term_width():
    return shutil.get_terminal_size((60, 24)).columns


# ── Inline image helpers ──────────────────────────────────────
def _is_iterm_compatible():
    term = os.environ.get("TERM_PROGRAM", "")
    return term in ("iTerm.app", "WezTerm", "ghostty")


def _inline_image_seq(path: str, width: int = 10, inline: bool = True) -> str:
    """Return the iTerm2/compatible inline image escape sequence."""
    import base64
    if not os.path.exists(path):
        return ""
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    return f"\033]1337;File=inline={1 if inline else 0};width={width};preserveAspectRatio=1:{data}\a"


# ── Avatar preprocessing ─────────────────────────────────────
_clean_avatar_cache = None

def _prepare_avatar(icon_path: str) -> str:
    """Remove outer white background while keeping the face inside the circle."""
    global _clean_avatar_cache
    if _clean_avatar_cache and os.path.exists(_clean_avatar_cache):
        return _clean_avatar_cache

    clean_path = "/tmp/.crowe-logic-avatar.png"
    try:
        subprocess.run(
            ["magick", icon_path,
             "-fuzz", "10%",
             "-fill", "none",
             "-draw", "color 0,0 floodfill",
             "-draw", "color 0,%[fx:h-1] floodfill",
             "-draw", "color %[fx:w-1],0 floodfill",
             "-draw", "color %[fx:w-1],%[fx:h-1] floodfill",
             clean_path],
            capture_output=True, timeout=5
        )
        if os.path.exists(clean_path):
            _clean_avatar_cache = clean_path
            return clean_path
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return icon_path


# ── Mini favicon (inline cursor icon) ────────────────────────
_favicon_cache = None

def get_favicon() -> str:
    """Return a tiny inline avatar for use next to the agent label."""
    global _favicon_cache
    if _favicon_cache is not None:
        return _favicon_cache

    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")

    if _is_iterm_compatible() and os.path.exists(icon_path):
        clean = _prepare_avatar(icon_path)
        _favicon_cache = _inline_image_seq(clean, width=2)
    else:
        _favicon_cache = f"{GOLD}{BOLD}\u28ff{RESET}"

    return _favicon_cache


# ── Welcome screen ───────────────────────────────────────────
def _center(text: str, width: int) -> str:
    """Center a plain-text line within *width* columns."""
    pad = max(0, (width - len(text)) // 2)
    return " " * pad + text


def welcome_screen(version: str = "0.1.0"):
    tw = min(_term_width(), 72)
    bar = f"{GOLD}{'━' * tw}{RESET}"
    thin = f"{GOLD}{DIM}{'─' * tw}{RESET}"

    # Raw logo lines (no ANSI, no leading spaces) — centered below
    crowe_lines = [
        " ██████╗██████╗  ██████╗ ██╗    ██╗███████╗",
        "██╔════╝██╔══██╗██╔═══██╗██║    ██║██╔════╝",
        "██║     ██████╔╝██║   ██║██║ █╗ ██║█████╗",
        "██║     ██╔══██╗██║   ██║██║███╗██║██╔══╝",
        "╚██████╗██║  ██║╚██████╔╝╚███╔███╔╝███████╗",
        " ╚═════╝╚═╝  ╚═╝ ╚═════╝  ╚══╝╚══╝ ╚══════╝",
    ]
    logic_lines = [
        "██╗      ██████╗  ██████╗ ██╗ ██████╗",
        "██║     ██╔═══██╗██╔════╝ ██║██╔════╝",
        "██║     ██║   ██║██║  ███╗██║██║",
        "██║     ██║   ██║██║   ██║██║██║",
        "███████╗╚██████╔╝╚██████╔╝██║╚██████╗",
        "╚══════╝ ╚═════╝  ╚═════╝ ╚═╝ ╚═════╝",
    ]

    centered_logo = "\n".join(
        f"{GOLD}{BOLD}{_center(l, tw)}{RESET}" for l in crowe_lines
    )
    centered_logic = "\n".join(
        f"{GOLD}{BOLD}{_center(l, tw)}{RESET}" for l in logic_lines
    )
    version_tag = _center(f"v{version}", tw)

    tagline_text = f"Universal AI Agent  ---  Crowe Logic, Inc."
    centered_tagline = _center(tagline_text, tw)

    cmd_line1 = "Type naturally --- the agent selects tools automatically."
    cmd_line2 = "/tools  /status  /clear  /help  /exit"

    return f"""
{bar}

{centered_logo}

{centered_logic}
{DIM}{version_tag}{RESET}
{thin}
{WHITE}{centered_tagline}{RESET}
{thin}

{DIM}{_center(cmd_line1, tw)}{RESET}
{DIM}{_center(cmd_line2, tw)}{RESET}
{bar}
"""


def show_welcome(version: str = "0.1.0"):
    """Print the full welcome: avatar + banner."""
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")

    if _is_iterm_compatible():
        avatar_path = _prepare_avatar(icon_path)
        seq = _inline_image_seq(avatar_path, width=10)
        if seq:
            sys.stdout.write(seq + "\n")
            sys.stdout.flush()

    print(welcome_screen(version))


# ── Legacy compat ─────────────────────────────────────────────
def show_inline_image(path: str, width: int = 10):
    if _is_iterm_compatible():
        seq = _inline_image_seq(path, width=width)
        if seq:
            sys.stdout.write(seq)
            sys.stdout.flush()
