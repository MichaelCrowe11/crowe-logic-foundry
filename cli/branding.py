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


def _get_avatar_seq(width: int = 8) -> str:
    """Get the centered avatar inline image sequence, or empty string."""
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
    if not _is_iterm_compatible() or not os.path.exists(icon_path):
        return ""
    avatar_path = _prepare_avatar(icon_path)
    return _inline_image_seq(avatar_path, width=width)


def welcome_screen(version: str = "0.1.0", avatar_seq: str = ""):
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

    # Center as a BLOCK (align to widest line), not per-line
    crowe_max = max(len(l) for l in crowe_lines)
    logic_max = max(len(l) for l in logic_lines)
    crowe_pad = max(0, (tw - crowe_max) // 2)
    logic_pad = max(0, (tw - logic_max) // 2)

    centered_logo = "\n".join(
        f"{GOLD}{BOLD}{' ' * crowe_pad}{l}{RESET}" for l in crowe_lines
    )
    centered_logic = "\n".join(
        f"{GOLD}{BOLD}{' ' * logic_pad}{l}{RESET}" for l in logic_lines
    )
    version_tag = _center(f"v{version}", tw)

    tagline_text = "Universal AI Agent  ---  Crowe Logic, Inc."
    centered_tagline = _center(tagline_text, tw)

    cmd_line1 = "Type naturally --- the agent selects tools automatically."
    cmd_line2 = "/tools  /status  /clear  /help  /exit"

    # Avatar sits centered inside the banner, right above the ASCII art.
    # Only include the avatar line if we have an actual inline image.
    if avatar_seq:
        avatar_block = f"\n{' ' * max(0, (tw - 8) // 2)}{avatar_seq}"
    else:
        avatar_block = ""

    return f"""
{bar}{avatar_block}

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
    """Print the full welcome: avatar inside the banner."""
    avatar_seq = _get_avatar_seq(width=8)
    print(welcome_screen(version, avatar_seq=avatar_seq))


# ── Legacy compat ─────────────────────────────────────────────
def show_inline_image(path: str, width: int = 10):
    if _is_iterm_compatible():
        seq = _inline_image_seq(path, width=width)
        if seq:
            sys.stdout.write(seq)
            sys.stdout.flush()


# ── Session state (shared between branding and CLI) ──────────
import time as _time

session_state = {
    "started_at": 0.0,
    "tool_count": 0,
    "api_status": "ok",       # ok | throttled | down
    "retry_seconds": 0,
    "active_model": "",       # current model label for toolbar
    "last_tokens": 0,         # tokens from last response
    "last_tps": 0.0,          # tokens/sec from last response
    "total_tokens": 0,        # cumulative tokens this session
}

def reset_session_state():
    session_state["started_at"] = _time.monotonic()
    session_state["tool_count"] = 0
    session_state["api_status"] = "ok"
    session_state["retry_seconds"] = 0
    session_state["active_model"] = ""
    session_state["last_tokens"] = 0
    session_state["last_tps"] = 0.0
    session_state["total_tokens"] = 0


# ── Tool result summarizer ───────────────────────────────────
def summarize_tool_result(tool_name: str, result: str) -> str:
    """Generate a contextual one-line summary based on tool type."""
    if not result:
        return "done"

    name = tool_name.lower()

    if "search" in name:
        # Count results by looking for common patterns
        lines = [l for l in result.strip().split("\n") if l.strip()]
        count = len(lines)
        if count > 1:
            return f"{count} results"
        return "1 result"

    if name in ("read_file", "read"):
        lines = result.count("\n") + 1
        return f"{lines} lines"

    if name in ("write_file", "write"):
        return f"{len(result.encode())} bytes written"

    if name in ("edit_file", "edit"):
        return "applied"

    if name == "execute_shell":
        lines = result.strip().split("\n")
        last = lines[-1].strip() if lines else ""
        if last.startswith("exit"):
            return last
        return f"exit 0 ({len(lines)} lines)"

    if name.startswith("git_"):
        if name == "git_commit":
            # Try to extract short hash
            for word in result.split():
                if len(word) >= 7 and all(c in "0123456789abcdef" for c in word[:7]):
                    return f"committed {word[:7]}"
            return "committed"
        if name == "git_status":
            lines = [l for l in result.strip().split("\n") if l.strip()]
            return f"{len(lines)} changes"
        return "done"

    if name in ("browse_url", "browser_navigate"):
        return f"loaded ({len(result)} chars)"

    if name in ("list_directory", "list_dir"):
        items = [l for l in result.strip().split("\n") if l.strip()]
        return f"{len(items)} items"

    if name.startswith("talon_"):
        return "generated"

    if name.startswith("mcp_"):
        return "done"

    # Default: first 60 chars of output
    first_line = result.strip().split("\n")[0][:60]
    if first_line:
        return first_line
    return "done"


# ── Hybrid tool card renderer ────────────────────────────────
def render_tool_card(console, name: str, args: str,
                     status: str = "running",
                     result: str = "", duration_ms: int = 0):
    """Render a hybrid tool execution card.

    status: 'running' | 'ok' | 'fail'
    """
    from rich.text import Text

    # Truncate args for display
    if args and len(args) > 70:
        args = args[:67] + "..."

    if status == "running":
        label = Text()
        label.append("  > ", style="dim #bfa669")
        label.append(name, style="bold #bfa669")
        if args:
            label.append(f"  {args}", style="dim")
        console.print(label)
        return

    # Completed card — two lines with left border
    border_color = "#bfa669" if status == "ok" else "#bf6f6f"
    check = "\u2713" if status == "ok" else "\u2717"
    check_color = "#6fbf73" if status == "ok" else "#bf6f6f"

    duration_str = f"{duration_ms / 1000:.1f}s" if duration_ms else ""

    summary = summarize_tool_result(name, result) if status == "ok" else ""
    if status == "fail" and result:
        # Extract error message
        try:
            import json as _json
            err = _json.loads(result)
            summary = err.get("error", result[:60])
        except (ValueError, AttributeError):
            summary = result.strip().split("\n")[0][:60]

    line1 = Text()
    line1.append("  \u2503 ", style=border_color)
    line1.append(name, style=f"bold {border_color}")
    if args:
        line1.append(f"  {args}", style="dim")

    line2 = Text()
    line2.append("  \u2503 ", style=border_color)
    line2.append(f"{check} ", style=check_color)
    if summary:
        line2.append(summary, style="dim")
    if duration_str:
        if summary:
            line2.append(" \u00b7 ", style="dim")
        line2.append(duration_str, style="dim")

    console.print(line1)
    console.print(line2)


# ── Rate limit countdown bar ─────────────────────────────────
def show_retry_countdown(console, wait_seconds: float, attempt: int, max_attempts: int):
    """Show a progress bar countdown during rate limit retry."""
    from rich.live import Live
    from rich.text import Text
    from rich.progress_bar import ProgressBar

    session_state["api_status"] = "throttled"
    session_state["retry_seconds"] = int(wait_seconds)

    start = _time.monotonic()
    end = start + wait_seconds

    with Live(console=console, refresh_per_second=4, transient=True) as live:
        while _time.monotonic() < end:
            elapsed = _time.monotonic() - start
            remaining = max(0, wait_seconds - elapsed)
            pct = min(elapsed / wait_seconds, 1.0)

            display = Text()
            display.append(f"  Rate limited \u2014 retry {attempt}/{max_attempts} in {remaining:.0f}s\n", style="#d4a645")
            # Build a text-based progress bar
            bar_width = min(40, _term_width() - 8)
            filled = int(bar_width * pct)
            bar_str = "\u2588" * filled + "\u2591" * (bar_width - filled)
            display.append(f"  {bar_str}", style="#d4a645")

            session_state["retry_seconds"] = int(remaining)
            live.update(display)
            _time.sleep(0.25)

    session_state["retry_seconds"] = 0


# ── 429 detection helper ─────────────────────────────────────
def is_rate_limit_error(error_msg: str) -> bool:
    """Check if an error message indicates rate limiting."""
    lower = error_msg.lower()
    return any(s in lower for s in (
        "429", "rate limit", "too many requests", "throttl",
        "server_error", "sorry, something went wrong",
    ))


# ── Bottom toolbar builder ───────────────────────────────────
def build_toolbar():
    """Build the prompt_toolkit bottom toolbar HTML string."""
    from prompt_toolkit.formatted_text import HTML
    from config.agent_config import AGENT_VERSION

    elapsed = _time.monotonic() - session_state["started_at"]
    minutes = int(elapsed) // 60
    seconds = int(elapsed) % 60
    if minutes > 0:
        duration = f"{minutes}m {seconds:02d}s"
    else:
        duration = f"{seconds}s"

    tool_count = session_state["tool_count"]
    api_status = session_state["api_status"]

    if api_status == "ok":
        status_html = '<style fg="#6fbf73">LIVE</style>'
    elif api_status == "throttled":
        retry = session_state["retry_seconds"]
        retry_str = f" retry {retry}s" if retry > 0 else ""
        status_html = f'<style fg="#d4a645">THROTTLED{retry_str}</style>'
    else:  # down
        status_html = '<style fg="#bf6f6f">DOWN</style>'

    model_label = session_state.get("active_model", "")
    model_html = f' <style fg="gray">\u00b7</style> <style fg="#8fa4bf">{model_label}</style>' if model_label else ""

    # Token stats from last response
    tokens = session_state.get("last_tokens", 0)
    tps = session_state.get("last_tps", 0)
    token_html = ""
    if tokens > 0:
        tps_str = f"{tps:.0f}" if tps >= 10 else f"{tps:.1f}"
        token_html = (
            f' <style fg="gray">\u00b7</style> '
            f'<style fg="#bfa669">{tokens} tok @ {tps_str}/s</style>'
        )

    left = f'<style fg="#bfa669">CroweLM v{AGENT_VERSION}</style>'
    right = (
        f'<style fg="#bfa669">{duration}</style>'
        f' <style fg="gray">\u00b7</style> '
        f'<style fg="#bfa669">{tool_count} tools</style>'
        f'{token_html}'
        f'{model_html}'
        f' <style fg="gray">\u00b7</style> '
        f'{status_html}'
    )

    return HTML(f' {left}      {right} ')


# ── Slash command completer ──────────────────────────────────
from prompt_toolkit.completion import Completer, Completion

class SlashCompleter(Completer):
    """Tab-complete for slash commands with descriptions."""

    COMMANDS = {
        "/tools":  "List available tools",
        "/model":  "Show/switch models",
        "/data":   "CroweLM training data telemetry",
        "/status": "Show agent info",
        "/clear":  "Clear screen",
        "/help":   "Show commands",
        "/exit":   "Quit session",
        "/quit":   "Quit session",
    }

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor.lstrip()
        if not text.startswith("/"):
            return
        for cmd, desc in self.COMMANDS.items():
            if cmd.startswith(text):
                yield Completion(
                    cmd,
                    start_position=-len(text),
                    display_meta=desc,
                )


# ── Multi-line key bindings ──────────────────────────────────
def create_chat_keybindings():
    """Create key bindings for the chat prompt (Ctrl+E for multi-line)."""
    from prompt_toolkit.key_binding import KeyBindings

    kb = KeyBindings()

    @kb.add("c-e")
    def _toggle_multiline(event):
        """Open multi-line editor: Ctrl+D to send, Esc to cancel."""
        from prompt_toolkit import prompt as pt_prompt
        from prompt_toolkit.formatted_text import HTML

        ml_prompt = HTML(
            '<style fg="#bfa669" bg="#1a1a1a">'
            'MULTI-LINE (Ctrl+D to send, Esc to cancel)\n'
            '</style>'
            '<style fg="#bfa669">\u00b7 </style>'
        )
        try:
            text = pt_prompt(ml_prompt, multiline=True)
            if text and text.strip():
                # Insert the multi-line text into the current buffer
                event.app.current_buffer.text = text.strip()
                event.app.current_buffer.validate_and_handle()
        except (EOFError, KeyboardInterrupt):
            pass

    return kb
