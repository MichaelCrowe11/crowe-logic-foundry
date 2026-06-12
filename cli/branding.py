"""
Crowe Logic CLI — Branding & Terminal Art
"""

import os
import sys
import shutil
import subprocess
import json
import html
import math

from cli.session_runtime import format_transcript_markdown, load_session_runtime

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
MARK = "\u25c6"  # signature mark (fallback when no inline image)
RULE = "\u2500"  # hairline horizontal rule
RULE_HEAVY = "\u2501"  # heavy horizontal rule
DOT = "\u00b7"  # inline separator
BAR = "\u2502"  # vertical rail
CHECK = "\u2713"  # success
CROSS = "\u2717"  # failure
ARROW = "\u203a"  # prompt continuation, running tool

# Layout
GUTTER = 2  # left indent for non-centered content
TRANSCRIPT_MAX_WIDTH = 96
HUD_MAX_WIDTH = 112
RECENT_ACTION_LIMIT = 6


# ── Thinking spinner (animated, color-drifting) ──────────────
# A warm-gold shimmer, on-brand for Crowe Logic. The travelling crest drifts
# through these palette stops as it works.
_SPINNER_PALETTE = ("#bfa669", "#d4a645", "#e8c87a", "#a8915a")
_PULSE_BLOCKS = "▁▂▃▄▅▆▇█"  # ▁▂▃▄▅▆▇█


def _lerp_hex(a: str, b: str, t: float) -> str:
    """Linear blend between two #rrggbb colors; t in 0..1. Pure."""
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    ar, ag, ab = int(a[1:3], 16), int(a[3:5], 16), int(a[5:7], 16)
    br, bg, bb = int(b[1:3], 16), int(b[3:5], 16), int(b[5:7], 16)
    r = round(ar + (br - ar) * t)
    g = round(ag + (bg - ag) * t)
    bl = round(ab + (bb - ab) * t)
    return f"#{r:02x}{g:02x}{bl:02x}"


def _crest_color(phase: float) -> str:
    """The crest hue at a continuous phase, cycling smoothly around the palette."""
    n = len(_SPINNER_PALETTE)
    pos = (phase % n + n) % n  # wrap into 0..n
    i = int(pos)
    return _lerp_hex(_SPINNER_PALETTE[i], _SPINNER_PALETTE[(i + 1) % n], pos - i)


class ThinkingSpinner:
    """Crowe Logic 'working' animation: reasoning lanes pulsing in parallel with
    a warm-gold crest that drifts in color as it travels, anchored by the ◆ mark.

    Motion and color derive from the wall clock (not a frame counter), so the
    animation stays continuous whether the renderable persists across refreshes
    (single-pane Live) or is rebuilt every frame (dual-pane layout).
    """

    def __init__(
        self,
        label: str = "thinking",
        *,
        lanes: int = 20,
        rows: int = 3,
        speed: float = 2.6,
        spread: float = 0.45,
        row_phase: float = 1.1,
        hue_speed: float = 0.35,
    ):
        self._label = label
        self._lanes = lanes
        self._rows = rows  # reasoning lanes stacked into a field
        self._speed = speed  # radians/sec of pulse travel
        self._spread = spread  # phase offset between lanes (crest travel)
        self._row_phase = row_phase  # phase offset between rows (they desync)
        self._hue_speed = hue_speed  # palette stops advanced per second

    def frame(self, now: float):
        """Build the frame for an absolute wall-clock time (pure; render + tests).

        An amplified multi-row field of gold reasoning lanes, each row's crest
        travelling at a desynced phase so the whole field shimmers. Row 0 carries
        the anchoring mark + label, so the first span stays the ◆ crest hue.
        """
        from rich.text import Text

        text = Text()
        crest = _crest_color(now * self._hue_speed)  # this frame's drifting hue
        for r in range(self._rows):
            if r == 0:
                text.append(f"{MARK} ", style=f"bold {crest}")
            else:
                text.append("\n  ")  # continuation rows indent under the mark
            for i in range(self._lanes):
                level = (
                    math.sin(now * self._speed - i * self._spread - r * self._row_phase)
                    + 1
                ) / 2  # 0..1
                if level > 0.72:
                    style = f"bold {crest}"
                elif level > 0.4:
                    style = _lerp_hex("#4a4030", crest, 0.6)
                else:
                    style = GOLD_DIM_HEX
                text.append(
                    _PULSE_BLOCKS[round(level * (len(_PULSE_BLOCKS) - 1))], style=style
                )
            if r == 0:
                text.append(f"  {self._label}…", style="dim")
        return text

    def __rich__(self):
        return self.frame(_time.monotonic())


def thinking_spinner(label: str = "thinking"):
    """A fresh Crowe Logic thinking animation. Drive it inside a `rich.live.Live`.

    The reel style is selectable via the ``CROWE_SPINNER_STYLE`` env var:
    ``wordmark`` (default) · ``classic`` · ``cascade`` · ``hybrid`` · ``wave``.
    ``wave`` restores the original pulse-field ``ThinkingSpinner``. Unknown values
    and any import failure fall back to a working spinner, so the animation can
    never break a turn. Both the single-pane renderer and the dual-pane renderer
    route through here, so this one switch styles every thinking surface.
    """
    style = os.environ.get("CROWE_SPINNER_STYLE", "wordmark").strip().lower()
    if style in ("wave", "pulse", "legacy", "field"):
        return ThinkingSpinner(label)
    try:
        from cli.spinners import REGISTRY, get_spinner

        return get_spinner(style if style in REGISTRY else "wordmark", label)
    except Exception:
        # The slot module is optional; never let a spinner import brick a turn.
        return ThinkingSpinner(label)


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


def transcript_width(console, max_width: int = TRANSCRIPT_MAX_WIDTH) -> int:
    """Preferred width for transcript panels inside the terminal gutter."""
    width = getattr(console, "width", term_width())
    usable = max(24, width - (GUTTER * 2) - 2)
    return min(max_width, usable)


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def preview_tool_args(args: str, limit: int = 84) -> str:
    """Compact tool arguments into a short, scan-friendly preview."""
    if not args:
        return ""

    compact = " ".join(str(args).split())
    if compact.startswith("{") and compact.endswith("}"):
        try:
            data = json.loads(compact)
        except Exception:
            data = None
        if isinstance(data, dict):
            preview_parts = []
            for key, value in list(data.items())[:3]:
                if isinstance(value, str):
                    rendered = value
                else:
                    rendered = json.dumps(value, ensure_ascii=True)
                preview_parts.append(f"{key}={_truncate(rendered, 20)}")
            if len(data) > 3:
                preview_parts.append(f"+{len(data) - 3} more")
            return _truncate("  ".join(preview_parts), limit)

    return _truncate(compact, limit)


def _panel_title(label: str, meta: str = "", accent: str = GOLD_HEX):
    """Return a styled Rich title token for transcript panels."""
    from rich.text import Text

    title = Text()
    title.append(label.upper(), style=f"bold {accent}")
    if meta:
        title.append(f" {DOT} ", style="dim")
        title.append(meta, style="dim")
    return title


def build_transcript_markdown(
    console, text: str, *, title: str = "answer", meta: str = ""
):
    """Build the primary transcript renderable for assistant answer text."""
    from rich.markdown import Markdown
    from rich.padding import Padding
    from rich.panel import Panel
    from rich.text import Text
    from rich import box

    body = Markdown(text) if text.strip() else Text("thinking...", style="dim italic")
    panel = Panel(
        body,
        title=_panel_title(title, meta),
        border_style=GOLD_DIM_HEX,
        box=box.ROUNDED,
        padding=(0, 1),
        expand=False,
        width=transcript_width(console),
    )
    return Padding(panel, (0, 0, 0, GUTTER))


def render_transcript_markdown(
    console, text: str, *, title: str = "answer", meta: str = ""
):
    """Print a static transcript block."""
    console.print(build_transcript_markdown(console, text, title=title, meta=meta))


def build_reasoning_panel(
    console, text: str, *, live: bool = False, meta: str | None = None
):
    """Build the muted reasoning block used before or between answer segments."""
    from rich.padding import Padding
    from rich.panel import Panel
    from rich.text import Text
    from rich import box

    if meta is None:
        meta = "live" if live else "captured"
    body = (
        Text(text, style="dim")
        if text.strip()
        else Text("thinking...", style="dim italic")
    )
    panel = Panel(
        body,
        title=_panel_title("reasoning", meta),
        border_style=GOLD_DIM_HEX,
        box=box.ROUNDED,
        padding=(0, 1),
        expand=False,
        width=transcript_width(console),
    )
    return Padding(panel, (0, 0, 0, GUTTER))


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
            [
                "magick",
                icon_path,
                "-fuzz",
                "10%",
                "-fill",
                "none",
                "-draw",
                "color 0,0 floodfill",
                "-draw",
                "color 0,%[fx:h-1] floodfill",
                "-draw",
                "color %[fx:w-1],0 floodfill",
                "-draw",
                "color %[fx:w-1],%[fx:h-1] floodfill",
                clean_path,
            ],
            capture_output=True,
            timeout=5,
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


def welcome_screen(version: str = "0.2.7", avatar_seq: str = "") -> str:
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
    cmd_list = f"{DIM}/tools   /model   /data   /dataset   /steer   /transcript{RESET}"
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


def _animate_wordmark_ignite(wordmark_plain: str, w: int) -> None:
    """Ignite the typographic wordmark in place: a warm-bright band sweeps across
    the letters (twice), lighting each as it passes, then they settle to gold.

    Crowe Logic's distinct motion - a horizontal heat-shimmer over its typographic
    mark, versus the sibling CLIs' block sweep / drop-in. Single line, redrawn with
    carriage returns (terminal-safe), so it never stacks or ghosts.
    """
    import time

    pad = " " * max(0, (w - cell_width(wordmark_plain)) // 2)
    chars = list(wordmark_plain)
    n = len(chars)
    bright = "\033[38;2;255;240;200m\033[1m"  # warm bright ignition
    for _pass in range(2):
        for head in range(-3, n + 4):
            cells = []
            for i, ch in enumerate(chars):
                if head - 2 <= i <= head:
                    cells.append(f"{bright}{ch}{RESET}")
                else:
                    cells.append(f"{GOLD}{BOLD}{ch}{RESET}")
            sys.stdout.write("\r" + pad + "".join(cells))
            sys.stdout.flush()
            time.sleep(0.012)
    sys.stdout.write("\r" + pad + f"{GOLD}{BOLD}{wordmark_plain}{RESET}")
    sys.stdout.flush()


def show_welcome(version: str = "0.2.7"):
    """Print the full welcome: avatar inside the banner.

    On a real terminal the typographic wordmark ignites into place; otherwise the
    banner prints static. The animation only restyles the wordmark line, located
    by an exact match against the same string `welcome_screen` embeds, so any
    layout change falls back to the static print rather than mis-animating.
    """
    avatar_seq = _get_avatar_seq(width=8)
    full = welcome_screen(version, avatar_seq=avatar_seq)
    if not sys.stdout.isatty():
        print(full)
        return
    wordmark_plain = "C R O W E   L O G I C"
    wordmark_line = center(f"{GOLD}{BOLD}{wordmark_plain}{RESET}", term_width())
    before, sep, after = full.partition(wordmark_line)
    if not sep:  # layout drifted from what we expect: don't risk a broken reveal
        print(full)
        return
    sys.stdout.write(before)
    sys.stdout.flush()
    _animate_wordmark_ignite(wordmark_plain, term_width())
    sys.stdout.write(after)
    sys.stdout.flush()


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
    "api_status": "ok",  # ok | throttled | down
    "retry_seconds": 0,
    "session_id": "",
    "active_model": "",  # current model label for toolbar
    "steering_instruction": "",
    "dataset_selection": "all",
    "last_tokens": 0,  # tokens from last response
    "last_tps": 0.0,  # tokens/sec from last response
    "total_tokens": 0,  # cumulative tokens this session
    "last_answer_text": "",
    "last_reasoning_text": "",
    "recent_actions": [],  # newest action cards / timeline entries
}


def ensure_session_state(state: dict | None = None) -> dict:
    """Backfill any newly added session-state keys for older callers/tests."""
    if state is None:
        state = session_state
    state.setdefault("started_at", _time.monotonic())
    state.setdefault("tool_count", 0)
    state.setdefault("api_status", "ok")
    state.setdefault("retry_seconds", 0)
    state.setdefault("session_id", "")
    state.setdefault("active_model", "")
    state.setdefault("steering_instruction", "")
    state.setdefault("dataset_selection", "all")
    state.setdefault("last_tokens", 0)
    state.setdefault("last_tps", 0.0)
    state.setdefault("total_tokens", 0)
    state.setdefault("last_answer_text", "")
    state.setdefault("last_reasoning_text", "")
    state.setdefault("recent_actions", [])
    # Lazy-attach the cost tracker so every session accumulates upstream USD
    # cost and credits regardless of whether the operator ever looks at it.
    # The HUD renders rows from this tracker via _get_tracker_snapshot.
    if state.get("cost_tracker") is None:
        try:
            from cli.cost_model import SessionCostTracker

            state["cost_tracker"] = SessionCostTracker()
        except Exception:
            state["cost_tracker"] = None
    return state


def reset_session_state():
    ensure_session_state()
    session_state["started_at"] = _time.monotonic()
    session_state["tool_count"] = 0
    session_state["api_status"] = "ok"
    session_state["retry_seconds"] = 0
    session_state["session_id"] = ""
    session_state["active_model"] = ""
    session_state["steering_instruction"] = ""
    session_state["dataset_selection"] = "all"
    session_state["last_tokens"] = 0
    session_state["last_tps"] = 0.0
    session_state["total_tokens"] = 0
    session_state["last_answer_text"] = ""
    session_state["last_reasoning_text"] = ""
    session_state["recent_actions"] = []


def show_last_transcript(
    console, state: dict | None = None, *, use_pager: bool = False
) -> None:
    """Render the last captured answer/reasoning transcript for the session."""
    from rich.markdown import Markdown

    current = ensure_session_state(state)
    if not current.get("last_answer_text") and not current.get("last_reasoning_text"):
        session_id = current.get("session_id", "")
        if session_id:
            persisted = load_session_runtime(session_id)
            current["last_answer_text"] = persisted.get("last_answer_text", "")
            current["last_reasoning_text"] = persisted.get("last_reasoning_text", "")
            current["active_model"] = current.get("active_model") or persisted.get(
                "last_model", ""
            )

    model = current.get("active_model", "")
    answer_text = current.get("last_answer_text", "")
    reasoning_text = current.get("last_reasoning_text", "")
    markdown = format_transcript_markdown(
        {
            "last_model": model,
            "last_answer_text": answer_text,
            "last_reasoning_text": reasoning_text,
        }
    )
    if use_pager:
        with console.pager(styles=True):
            console.print(Markdown(markdown))
        return

    console.print()
    if answer_text.strip():
        render_transcript_markdown(console, answer_text, title="answer", meta="last")
    if reasoning_text.strip():
        console.print(
            build_reasoning_panel(console, reasoning_text, live=False, meta="full")
        )
    if not answer_text.strip() and not reasoning_text.strip():
        render_transcript_markdown(console, markdown, title="answer", meta="last")
    console.print()


def _action_summary(name: str, status: str, result: str) -> str:
    """Return the timeline summary for a completed action."""
    if status == "ok":
        return summarize_tool_result(name, result)
    if not result:
        return "failed"
    try:
        err = json.loads(result)
        return err.get("error", "failed")
    except (ValueError, AttributeError):
        first_line = result.strip().split("\n")[0].strip()
        return first_line or "failed"


def record_action(
    session_state: dict | None,
    *,
    name: str,
    status: str,
    result: str = "",
    duration_ms: int = 0,
    args: str = "",
) -> dict:
    """Append a recent action entry for HUD/status rendering."""
    state = ensure_session_state(session_state)
    entry = {
        "index": int(state.get("tool_count", 0)),
        "name": name,
        "status": status,
        "summary": _action_summary(name, status, result),
        "duration_ms": duration_ms,
        "args_preview": preview_tool_args(args),
    }
    recent = list(state.get("recent_actions", []))
    recent.append(entry)
    state["recent_actions"] = recent[-RECENT_ACTION_LIMIT:]
    return entry


def latest_action_summary(state: dict | None = None, limit: int = 28) -> str:
    """Compact latest-action summary for the toolbar."""
    current = ensure_session_state(state)
    recent = current.get("recent_actions", [])
    if not recent:
        return ""
    latest = recent[-1]
    summary = f"{latest.get('name', 'action')} {latest.get('status', '')}".strip()
    return _truncate(summary, limit)


def _format_session_duration(state: dict | None = None) -> str:
    """Render elapsed session duration."""
    current = ensure_session_state(state)
    elapsed = _time.monotonic() - current["started_at"]
    minutes = int(elapsed) // 60
    seconds = int(elapsed) % 60
    return f"{minutes}m {seconds:02d}s" if minutes > 0 else f"{seconds}s"


def _api_status_label(state: dict | None = None) -> tuple[str, str]:
    """Return display text + color for the current API state."""
    current = ensure_session_state(state)
    api_status = current["api_status"]
    if api_status == "ok":
        return ("LIVE", GREEN_HEX)
    if api_status == "throttled":
        retry = current.get("retry_seconds", 0)
        retry_str = f" retry {retry}s" if retry > 0 else ""
        return (f"THROTTLED{retry_str}", AMBER_HEX)
    return ("DOWN", RED_HEX)


def _metric_line(label: str, value: str, *, accent: str = WHITE_HEX):
    """Build a compact HUD metric line."""
    from rich.text import Text

    line = Text()
    line.append(f"{label} ", style=GOLD_DIM_HEX)
    line.append(value or "—", style=accent)
    return line


def _get_tracker_snapshot(session_state: dict) -> dict | None:
    """Pull the SessionCostTracker snapshot from session_state if present.

    The tracker is attached lazily by cli/crowe_logic.py on first use so
    existing call sites that don't need cost tracking aren't forced to
    know about it. Returns None when no tracker is bound.
    """
    tracker = session_state.get("cost_tracker")
    if tracker is None:
        return None
    try:
        return tracker.snapshot()
    except Exception:
        return None


def build_session_hud(
    console,
    *,
    state: dict | None = None,
    cwd: str | None = None,
    title: str = "session",
    meta: str = "live",
):
    """Build the compact session HUD shown ahead of transcript turns."""
    from rich.console import Group
    from rich.padding import Padding
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich import box

    current = ensure_session_state(state)
    status_text, status_color = _api_status_label(current)
    model_label = current.get("active_model", "") or "CroweLM"
    cwd_label = os.path.basename(cwd or os.getcwd()) or (cwd or os.getcwd())
    tokens = int(current.get("last_tokens", 0))
    tps = float(current.get("last_tps", 0.0))
    total_tokens = int(current.get("total_tokens", 0))
    latest_action = latest_action_summary(current, limit=32)

    # Cost + credit totals from the session tracker, if one was hooked in.
    tracker_snap = _get_tracker_snapshot(current)

    grid = Table.grid(expand=False, padding=(0, 3))
    grid.add_column(min_width=24)
    grid.add_column(min_width=24)
    grid.add_column(min_width=24)
    grid.add_row(
        _metric_line("MODEL", model_label, accent=BLUE_HEX),
        _metric_line("API", status_text, accent=status_color),
        _metric_line("SESSION", _format_session_duration(current), accent=GOLD_HEX),
    )
    grid.add_row(
        _metric_line("WORKSPACE", cwd_label, accent=WHITE_HEX),
        _metric_line("TOOLS", str(current.get("tool_count", 0)), accent=GOLD_HEX),
        _metric_line(
            "TOTAL",
            f"{total_tokens:,} tok" if total_tokens else "0 tok",
            accent=GOLD_HEX,
        ),
    )
    if tokens > 0 or latest_action:
        tps_str = f"{tps:.0f}" if tps >= 10 else f"{tps:.1f}"
        grid.add_row(
            _metric_line(
                "LAST",
                f"{tokens} tok @ {tps_str}/s" if tokens > 0 else "—",
                accent=WHITE_HEX,
            ),
            _metric_line("LATEST", latest_action or "no actions yet", accent=WHITE_HEX),
            Text(""),
        )
    if tracker_snap and tracker_snap["turn_count"] > 0:
        usd = tracker_snap["total_usd"]
        usd_str = f"~${usd:.4f}" if usd < 0.01 else f"~${usd:.3f}"
        credits_val = tracker_snap["total_credits"]
        credits_str = f"{credits_val} cr" if credits_val else "—"
        cache_ratio = ""
        if tracker_snap["cached_turns"]:
            cache_ratio = (
                f" · {tracker_snap['cached_turns']}/{tracker_snap['turn_count']} cached"
            )
        grid.add_row(
            _metric_line("COST", f"{usd_str}{cache_ratio}", accent=GOLD_HEX),
            _metric_line("CREDITS", credits_str, accent=GOLD_HEX),
            _metric_line("TURNS", str(tracker_snap["turn_count"]), accent=GOLD_HEX),
        )

    panel = Panel(
        Group(grid),
        title=_panel_title(title, meta),
        border_style=GOLD_DIM_HEX,
        box=box.ROUNDED,
        padding=(0, 1),
        expand=False,
        width=min(HUD_MAX_WIDTH, transcript_width(console, max_width=HUD_MAX_WIDTH)),
    )
    return Padding(panel, (0, 0, 0, GUTTER))


def render_session_hud(
    console,
    *,
    state: dict | None = None,
    cwd: str | None = None,
    title: str = "session",
    meta: str = "live",
):
    """Print the compact session HUD."""
    console.print(
        build_session_hud(console, state=state, cwd=cwd, title=title, meta=meta)
    )


def build_recent_actions_panel(
    console, *, state: dict | None = None, title: str = "timeline", meta: str = "recent"
):
    """Build the recent-actions timeline panel."""
    from rich.padding import Padding
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich import box

    current = ensure_session_state(state)
    recent = list(reversed(current.get("recent_actions", [])))

    if not recent:
        body = Text("No actions yet in this session.", style="dim italic")
    else:
        body = Table.grid(expand=False, padding=(0, 2))
        body.add_column(style="dim", width=4)
        body.add_column(style=WHITE_HEX, min_width=24)
        body.add_column(width=10)
        body.add_column(style="dim", width=8)
        body.add_column(style="dim", min_width=26)
        for entry in recent:
            status = entry.get("status", "")
            status_text = "OK" if status == "ok" else "FAIL"
            status_style = GREEN_HEX if status == "ok" else RED_HEX
            duration_ms = int(entry.get("duration_ms", 0))
            duration_str = f"{duration_ms / 1000:.1f}s" if duration_ms else "—"
            summary = _truncate(str(entry.get("summary", "")), 44)
            status_cell = Text(status_text, style=status_style)
            body.add_row(
                f"#{entry.get('index', 0)}",
                str(entry.get("name", "action")),
                status_cell,
                duration_str,
                summary,
            )

    panel = Panel(
        body,
        title=_panel_title(title, meta),
        border_style=GOLD_DIM_HEX,
        box=box.ROUNDED,
        padding=(0, 1),
        expand=False,
        width=min(HUD_MAX_WIDTH, transcript_width(console, max_width=HUD_MAX_WIDTH)),
    )
    return Padding(panel, (0, 0, 0, GUTTER))


def render_recent_actions(
    console, *, state: dict | None = None, title: str = "timeline", meta: str = "recent"
):
    """Print the recent-actions timeline."""
    console.print(
        build_recent_actions_panel(console, state=state, title=title, meta=meta)
    )


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
def render_tool_card(
    console,
    name: str,
    args: str,
    status: str = "running",
    result: str = "",
    duration_ms: int = 0,
):
    """Render a tool execution card.

    Three states:
      running: single line with arrow indicator
      ok:      two lines, gold left rail, check mark
      fail:    two lines, red left rail, cross mark

    All glyphs and colors come from the design token module.
    """
    from rich.console import Group
    from rich.padding import Padding
    from rich.panel import Panel
    from rich.text import Text
    from rich import box

    args_preview = preview_tool_args(args)
    duration_str = f"{duration_ms / 1000:.1f}s" if duration_ms else ""

    if status == "running":
        label = Text()
        label.append(" " * GUTTER + f"{ARROW} ", style=f"dim {AMBER_HEX}")
        label.append("ACTION", style=f"bold {AMBER_HEX}")
        label.append(f"  {name}", style=f"bold {GOLD_HEX}")
        if args_preview:
            label.append(f"  {args_preview}", style="dim")
        console.print(label)
        return

    if status == "ok":
        # Success stays on one line so a long agent turn reads as a tight
        # ledger of actions; only failures get a bordered block below.
        summary = summarize_tool_result(name, result)
        line = Text()
        line.append(" " * GUTTER)
        line.append(f"{CHECK} ", style=GREEN_HEX)
        line.append(name, style=f"bold {GOLD_HEX}")
        if duration_str:
            line.append(f" {DOT} {duration_str}", style="dim")
        if args_preview:
            line.append(f" {DOT} {args_preview}", style="dim")
        if summary:
            line.append(f" {DOT} {summary}", style="dim")
        console.print(line)
        return

    meta = "failed"
    title_accent = RED_HEX
    border_color = RED_HEX
    check_glyph = CROSS
    check_color = RED_HEX

    summary = ""
    if status == "fail" and result:
        try:
            err = json.loads(result)
            summary = err.get("error", result[:80])
        except (ValueError, AttributeError):
            summary = result.strip().split("\n")[0][:80]

    header = Text()
    header.append(name, style=f"bold {title_accent}")
    if duration_str:
        header.append(f"  {DOT}  {duration_str}", style="dim")

    rows = [header]
    if args_preview:
        arg_row = Text()
        arg_row.append("args", style=BLUE_HEX)
        arg_row.append("  ")
        arg_row.append(args_preview, style="dim")
        rows.append(arg_row)

    if summary or duration_str:
        summary_row = Text()
        summary_row.append(f"{check_glyph} ", style=check_color)
        if summary:
            summary_row.append(summary, style="dim")
        if duration_str and not summary:
            summary_row.append(duration_str, style="dim")
        rows.append(summary_row)

    panel = Panel(
        Group(*rows),
        title=_panel_title("action", meta, accent=title_accent),
        border_style=border_color,
        box=box.ROUNDED,
        padding=(0, 1),
        expand=False,
        width=transcript_width(console),
    )
    console.print(Padding(panel, (0, 0, 0, GUTTER)))


def render_error(console, title: str, detail: str | None = None):
    """Render a structured error block.

    Format mirrors a failed tool card: red left rail, cross glyph,
    title in bold red, optional detail lines below.
    """
    from rich.console import Group
    from rich.padding import Padding
    from rich.panel import Panel
    from rich.text import Text
    from rich import box

    head = Text()
    head.append(title, style=f"bold {RED_HEX}")

    rows = [head]
    if detail:
        for line in detail.strip().splitlines():
            row = Text()
            row.append(f"{CROSS} ", style=RED_HEX)
            row.append(line, style="dim")
            rows.append(row)

    panel = Panel(
        Group(*rows),
        title=_panel_title("error", accent=RED_HEX),
        border_style=RED_HEX,
        box=box.ROUNDED,
        padding=(0, 1),
        expand=False,
        width=transcript_width(console),
    )
    console.print(Padding(panel, (0, 0, 0, GUTTER)))


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
            display.append(
                f"  Rate limited \u2014 retry {attempt}/{max_attempts} in {remaining:.0f}s\n",
                style="#d4a645",
            )
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
    return any(
        s in lower
        for s in (
            "429",
            "rate limit",
            "too many requests",
            "throttl",
            "server_error",
            "sorry, something went wrong",
        )
    )


# ── Bottom toolbar builder ───────────────────────────────────
def build_toolbar():
    """Build the prompt_toolkit bottom toolbar HTML string.

    Format:
      CroweLM v0.2.5      45s · 3 tools · 1247 tok @ 89/s · CroweLM Nexus · LIVE

    All separators are the DOT token, applied uniformly. Status is
    color-coded: green for LIVE, amber for THROTTLED, red for DOWN.
    """
    from prompt_toolkit.formatted_text import HTML
    from config.agent_config import AGENT_VERSION

    current = ensure_session_state()
    duration = _format_session_duration(current)

    tool_count = current["tool_count"]
    status_text, status_color = _api_status_label(current)
    status_html = f'<style fg="{status_color}">{html.escape(status_text)}</style>'

    sep = f' <style fg="gray">{DOT}</style> '

    parts = [
        f'<style fg="{GOLD_HEX}">{duration}</style>',
        f'<style fg="{GOLD_HEX}">{tool_count} tools</style>',
    ]

    tokens = current.get("last_tokens", 0)
    tps = current.get("last_tps", 0)
    if tokens > 0:
        tps_str = f"{tps:.0f}" if tps >= 10 else f"{tps:.1f}"
        parts.append(f'<style fg="{GOLD_HEX}">{tokens} tok @ {tps_str}/s</style>')

    latest_action = latest_action_summary(current)
    if latest_action:
        parts.append(
            f'<style fg="{WHITE_HEX}">last {html.escape(latest_action)}</style>'
        )

    if current.get("steering_instruction", "").strip():
        parts.append(f'<style fg="{AMBER_HEX}">steer</style>')

    dataset_selection = str(current.get("dataset_selection", "all") or "all").strip()
    if dataset_selection != "all":
        label = (
            "data off" if dataset_selection == "off" else f"data {dataset_selection}"
        )
        parts.append(
            f'<style fg="{WHITE_HEX}">{html.escape(_truncate(label, 24))}</style>'
        )

    model_label = current.get("active_model", "")
    if model_label:
        parts.append(f'<style fg="{BLUE_HEX}">{html.escape(model_label)}</style>')

    parts.append(status_html)

    left = f'<style fg="{GOLD_HEX}">CroweLM v{AGENT_VERSION}</style>'
    right = sep.join(parts)

    return HTML(f" {left}      {right} ")


# ── Slash command completer ──────────────────────────────────
from prompt_toolkit.completion import Completer, Completion


class SlashCompleter(Completer):
    """Tab-complete for slash commands with descriptions."""

    COMMANDS = {
        "/tools": "List available tools",
        "/model": "Show/switch models",
        "/data": "CroweLM training data telemetry",
        "/dataset": "Show/set injected dataset context",
        "/steer": "Persist steering for this session",
        "/transcript": "Show last full answer/reasoning",
        "/status": "Show agent info",
        "/clear": "Clear screen",
        "/help": "Show commands",
        "/exit": "Quit session",
        "/quit": "Quit session",
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
def create_chat_keybindings(console=None, state: dict | None = None):
    """Create key bindings for the chat prompt."""
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys

    kb = KeyBindings()

    @kb.add(Keys.BracketedPaste)
    def _compress_paste(event):
        """Large pastes land as a [paste #N: X lines] placeholder; the REPL
        expands them back to the full text on submit (cli.paste)."""
        from cli.paste import paste_stash

        event.current_buffer.insert_text(paste_stash.compress(event.data))

    @kb.add("c-e")
    def _toggle_multiline(event):
        """Open multi-line editor: Ctrl+D to send, Esc to cancel."""
        from prompt_toolkit import prompt as pt_prompt
        from prompt_toolkit.formatted_text import HTML

        ml_prompt = HTML(
            '<style fg="#bfa669" bg="#1a1a1a">'
            "MULTI-LINE (Ctrl+D to send, Esc to cancel)\n"
            "</style>"
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

    @kb.add("c-t")
    def _show_transcript(event):
        """Open the last captured transcript in the terminal pager."""
        if console is None:
            return
        event.app.run_in_terminal(
            lambda: show_last_transcript(console, state, use_pager=True)
        )

    return kb
