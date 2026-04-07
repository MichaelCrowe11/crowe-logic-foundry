"""
Crowe Logic CLI — Branding & Terminal Art
"""

import os
import sys
import shutil
import subprocess

# ── Design tokens ─────────────────────────────────────────────
# Color palette. Hex values mirror the Rich style strings used by
# the renderer; the ANSI escapes are used by the welcome banner
# which writes raw bytes to stdout (Rich is not in scope there).
GOLD_HEX = "#bfa669"
GOLD_DIM_HEX = "dim #bfa669"
WHITE_HEX = "#ffffff"
GREEN_HEX = "#6fbf73"
RED_HEX = "#bf6f6f"
AMBER_HEX = "#d4a645"
BLUE_HEX = "#8fa4bf"

GOLD = "\033[38;2;191;166;105m"
GOLD_BG = "\033[48;2;191;166;105m"
WHITE = "\033[97m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

# Glyph alphabet. Selected for legibility in monospace fonts and
# absence of ambiguous-width characters.
MARK = "\u25c6"          # signature mark (fallback when no inline image)
RULE = "\u2500"          # hairline horizontal rule
RULE_HEAVY = "\u2501"    # heavy horizontal rule
DOT = "\u00b7"           # inline separator
BAR = "\u2502"           # vertical rail
CHECK = "\u2713"         # success
CROSS = "\u2717"         # failure
ARROW = "\u203a"         # prompt continuation, running tool

# Layout
GUTTER = 2               # left indent for non-centered content

# ── Layout primitives ────────────────────────────────────────
def term_width() -> int:
    """Current terminal width in columns. Defaults to 80 if undetectable."""
    return shutil.get_terminal_size((80, 24)).columns


# Backwards-compat alias for older callers.
_term_width = term_width


def cell_width(text: str) -> int:
    """Visual width of a string in terminal cells.

    Uses wcwidth to handle double-width and ambiguous-width characters
    correctly. Falls back to len() if wcwidth is unavailable. This is
    the fix for "lines half sticking out": len() undercounts box
    drawing characters in some monospace fonts, which throws off any
    centering math that uses it.
    """
    try:
        from wcwidth import wcswidth
    except ImportError:
        return len(text)
    width = wcswidth(text)
    return width if width is not None and width >= 0 else len(text)


_ANSI_RE = None


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences for accurate width measurement."""
    global _ANSI_RE
    if _ANSI_RE is None:
        import re
        _ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07")
    return _ANSI_RE.sub("", text)


def center(text: str, width: int | None = None) -> str:
    """Center plain text against the terminal width (or override).

    Strips ANSI escape sequences before measuring so colored text
    centers correctly. The returned string preserves any escapes
    that were present in the input.
    """
    w = width if width is not None else term_width()
    plain = _strip_ansi(text)
    pad = max(0, (w - cell_width(plain)) // 2)
    return " " * pad + text


# Backwards-compat alias for older callers.
_center = center


def hairline(width: int | None = None, heavy: bool = False, dim: bool = True) -> str:
    """Return a horizontal rule spanning the full terminal width.

    The rule is rendered with the gold accent color, optionally dimmed.
    """
    w = width if width is not None else term_width()
    glyph = RULE_HEAVY if heavy else RULE
    style = f"{GOLD}{DIM}" if dim else GOLD
    return f"{style}{glyph * w}{RESET}"


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
def _get_avatar_seq(width: int = 8) -> str:
    """Get the centered avatar inline image sequence, or empty string."""
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
    if not _is_iterm_compatible() or not os.path.exists(icon_path):
        return ""
    avatar_path = _prepare_avatar(icon_path)
    return _inline_image_seq(avatar_path, width=width)


def welcome_screen(version: str = "0.1.0", avatar_seq: str = "") -> str:
    """Render the Crowe Logic signature welcome screen.

    Layout (top to bottom):
      - Hairline rule, full terminal width, dim gold
      - Centered mark (inline image avatar OR diamond glyph)
      - Centered wordmark "C R O W E   L O G I C" in bold gold
      - Centered version + active model line in dim
      - Centered tagline in white
      - Hairline rule
      - Commands hint in dim, indented

    All centering uses the full terminal width via `center()`. The
    rule spans the full terminal width via `hairline()`. There is no
    72-column clamp, no block-relative centering, and no ASCII art.
    """
    w = term_width()
    rule = hairline(w)

    # Wordmark: letter-spaced caps. "C R O W E   L O G I C" reads as
    # typography rather than decoration. Three spaces between words,
    # one between letters.
    wordmark_plain = "C R O W E   L O G I C"
    wordmark = f"{GOLD}{BOLD}{wordmark_plain}{RESET}"
    wordmark_line = center(wordmark, w)

    # Mark: inline image where supported, glyph fallback otherwise.
    # Both modes are visually equivalent: a single anchor point.
    if avatar_seq:
        # The avatar_seq is an iTerm2 inline image escape, which has
        # zero measurable width to len(). We approximate the visual
        # width as 4 cells (matching the width=4 setting in
        # _get_avatar_seq) and pad accordingly.
        mark_pad = max(0, (w - 4) // 2)
        mark_line = " " * mark_pad + avatar_seq
    else:
        mark_line = center(f"{GOLD}{BOLD}{MARK}{RESET}", w)

    # Build version + model line. The active model is read from
    # session_state if set; otherwise the line shows just the version.
    active_model = session_state.get("active_model", "") if session_state else ""
    if active_model:
        version_text = f"v{version}  {DOT}  {active_model}"
    else:
        version_text = f"v{version}"
    version_line = center(f"{DIM}{version_text}{RESET}", w)

    tagline_plain = f"Universal AI Agent  {DOT}  Crowe Logic, Inc."
    tagline_line = center(f"{WHITE}{tagline_plain}{RESET}", w)

    cmd_hint = f"{DIM}Type naturally. The agent selects tools automatically.{RESET}"
    cmd_list = f"{DIM}/tools   /model   /data   /status   /help   /exit{RESET}"
    indent = " " * GUTTER

    return (
        "\n"
        f"{rule}\n"
        "\n"
        "\n"
        f"{mark_line}\n"
        "\n"
        "\n"
        f"{wordmark_line}\n"
        f"{version_line}\n"
        "\n"
        "\n"
        f"{tagline_line}\n"
        "\n"
        "\n"
        f"{rule}\n"
        "\n"
        f"{indent}{cmd_hint}\n"
        "\n"
        f"{indent}{cmd_list}\n"
        "\n"
    )


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
    """Render a tool execution card.

    Three states:
      running: single line with arrow indicator
      ok:      two lines, gold left rail, check mark
      fail:    two lines, red left rail, cross mark

    All glyphs and colors come from the design token module.
    """
    from rich.text import Text

    # Truncate args for display (the full args go to the tool log).
    if args and len(args) > 70:
        args = args[:67] + "..."

    indent = " " * GUTTER

    if status == "running":
        label = Text()
        label.append(f"{indent}{ARROW} ", style=f"dim {GOLD_HEX}")
        label.append(name, style=f"bold {GOLD_HEX}")
        if args:
            label.append(f"  {args}", style="dim")
        console.print(label)
        return

    border_color = GOLD_HEX if status == "ok" else RED_HEX
    check_glyph = CHECK if status == "ok" else CROSS
    check_color = GREEN_HEX if status == "ok" else RED_HEX

    duration_str = f"{duration_ms / 1000:.1f}s" if duration_ms else ""

    summary = summarize_tool_result(name, result) if status == "ok" else ""
    if status == "fail" and result:
        try:
            import json as _json
            err = _json.loads(result)
            summary = err.get("error", result[:60])
        except (ValueError, AttributeError):
            summary = result.strip().split("\n")[0][:60]

    line1 = Text()
    line1.append(f"{indent}{BAR} ", style=border_color)
    line1.append(name, style=f"bold {border_color}")
    if args:
        line1.append(f"  {args}", style="dim")

    line2 = Text()
    line2.append(f"{indent}{BAR} ", style=border_color)
    line2.append(f"{check_glyph} ", style=check_color)
    if summary:
        line2.append(summary, style="dim")
    if duration_str:
        if summary:
            line2.append(f" {DOT} ", style="dim")
        line2.append(duration_str, style="dim")

    console.print(line1)
    console.print(line2)


def render_error(console, title: str, detail: str | None = None):
    """Render a structured error block.

    Format mirrors a failed tool card: red left rail, cross glyph,
    title in bold red, optional detail lines below.
    """
    from rich.text import Text

    indent = " " * GUTTER

    head = Text()
    head.append(f"{indent}{CROSS} ", style=RED_HEX)
    head.append(title, style=f"bold {RED_HEX}")
    console.print(head)

    if detail:
        for line in detail.strip().splitlines():
            row = Text()
            row.append(f"{indent}{BAR} ", style=RED_HEX)
            row.append(line, style="dim")
            console.print(row)


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
    """Build the prompt_toolkit bottom toolbar HTML string.

    Format:
      CroweLM v0.1.0      45s · 3 tools · 1247 tok @ 89/s · CroweLM Core · LIVE

    All separators are the DOT token, applied uniformly. Status is
    color-coded: green for LIVE, amber for THROTTLED, red for DOWN.
    """
    from prompt_toolkit.formatted_text import HTML
    from config.agent_config import AGENT_VERSION

    elapsed = _time.monotonic() - session_state["started_at"]
    minutes = int(elapsed) // 60
    seconds = int(elapsed) % 60
    duration = f"{minutes}m {seconds:02d}s" if minutes > 0 else f"{seconds}s"

    tool_count = session_state["tool_count"]
    api_status = session_state["api_status"]

    if api_status == "ok":
        status_html = f'<style fg="{GREEN_HEX}">LIVE</style>'
    elif api_status == "throttled":
        retry = session_state["retry_seconds"]
        retry_str = f" retry {retry}s" if retry > 0 else ""
        status_html = f'<style fg="{AMBER_HEX}">THROTTLED{retry_str}</style>'
    else:
        status_html = f'<style fg="{RED_HEX}">DOWN</style>'

    sep = f' <style fg="gray">{DOT}</style> '

    parts = [
        f'<style fg="{GOLD_HEX}">{duration}</style>',
        f'<style fg="{GOLD_HEX}">{tool_count} tools</style>',
    ]

    tokens = session_state.get("last_tokens", 0)
    tps = session_state.get("last_tps", 0)
    if tokens > 0:
        tps_str = f"{tps:.0f}" if tps >= 10 else f"{tps:.1f}"
        parts.append(f'<style fg="{GOLD_HEX}">{tokens} tok @ {tps_str}/s</style>')

    model_label = session_state.get("active_model", "")
    if model_label:
        parts.append(f'<style fg="{BLUE_HEX}">{model_label}</style>')

    parts.append(status_html)

    left = f'<style fg="{GOLD_HEX}">CroweLM v{AGENT_VERSION}</style>'
    right = sep.join(parts)

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
