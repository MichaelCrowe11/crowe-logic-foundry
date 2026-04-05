"""
CroweLM Scientific Streaming Renderer

High-fidelity terminal rendering with real-time telemetry:
  - Avatar displayed during thinking and streaming phases
  - Token-by-token streaming with live markdown at 20fps
  - Response header: raw favicon (iTerm2 protocol) + Rich label (cursor-tracked)
  - Post-response telemetry: tokens/sec, total tokens, TTFT, elapsed
  - Reasoning block display for thinking models (Kimi K2.5)
"""

import sys
import time
from rich.markdown import Markdown
from rich.text import Text
from rich.panel import Panel
from rich.live import Live
from rich.spinner import Spinner
from rich.columns import Columns
from rich import box

# ── Colors ────────────────────────────────────────────────────
GOLD = "#bfa669"
GREEN = "#6fbf73"
RED = "#bf6f6f"
BLUE = "#8fa4bf"
DIM_GOLD = "dim #bfa669"

# Refresh rate for live displays (frames per second)
_STREAM_FPS = 20


def _format_duration(ms: float) -> str:
    if ms < 1000:
        return f"{int(ms)}ms"
    return f"{ms / 1000:.1f}s"


def _format_tokens_per_sec(tokens: int, elapsed_s: float) -> str:
    if elapsed_s <= 0:
        return "--"
    tps = tokens / elapsed_s
    if tps >= 100:
        return f"{int(tps)} tok/s"
    return f"{tps:.1f} tok/s"


class StreamRenderer:
    """Manages the full lifecycle of a streaming model response.

    Avatar rendering strategy (iTerm2 + Rich hybrid):
      - Favicon is a raw iTerm2 inline image escape sequence (~244KB).
        Rich cannot parse or track it, so it goes through sys.stdout.write().
      - Model label is printed via Rich console.print() immediately after,
        landing on the SAME terminal line as the favicon.
      - This keeps Rich's cursor tracking accurate, preventing the
        transient Live spinner from clobbering the header on cleanup.

    Usage:
        renderer = StreamRenderer(console, model_label, provider_name, favicon)
        renderer.start()             # shows avatar + label + spinner
        renderer.begin_stream()      # transitions to live markdown
        renderer.feed(token)         # append token
        renderer.feed_reasoning(t)   # append reasoning token (thinking models)
        renderer.finish()            # renders telemetry footer
    """

    def __init__(self, console, model_label: str, provider_name: str, favicon: str = ""):
        self.console = console
        self.model_label = model_label
        self.provider_name = provider_name.upper()
        self.favicon = favicon

        self._text_chunks: list[str] = []
        self._reasoning_chunks: list[str] = []
        self._token_count = 0
        self._reasoning_token_count = 0

        self._spinner = None
        self._spin_live = None
        self._md_live = None
        self._streaming = False
        self._header_shown = False

        self._t_start = 0.0
        self._t_first_token = 0.0
        self._t_end = 0.0

    def _show_header(self):
        """Print the avatar + model label header (once per response).

        Favicon goes through raw sys.stdout.write() because it's an iTerm2
        inline image escape sequence that Rich can't parse. The model label
        goes through Rich console.print() so its cursor tracking stays
        accurate — this prevents Live widgets (spinner, markdown) from
        overwriting the header during transient cleanup.
        """
        if self._header_shown:
            return
        self._header_shown = True

        # Write favicon as raw escape sequence — Rich can't handle iTerm2 protocol
        if self.favicon:
            sys.stdout.write(f"  {self.favicon} ")
            sys.stdout.flush()
        else:
            sys.stdout.write("  ")
            sys.stdout.flush()

        # Label via Rich — keeps cursor tracking accurate for Live widgets below
        self.console.print(f"[bold {GOLD}]{self.model_label}[/{GOLD}]")

    def start(self):
        """Show avatar header + thinking spinner."""
        self._t_start = time.monotonic()
        self._show_header()
        self._start_spinner("thinking...")

    def begin_stream(self):
        """Transition from spinner to live streaming markdown."""
        if self._streaming:
            return
        self._stop_spinner()
        self._streaming = True
        self._show_header()

        self._md_live = Live(
            Markdown(""),
            console=self.console,
            refresh_per_second=_STREAM_FPS,
            vertical_overflow="visible",
        )
        self._md_live.start()

    def feed(self, token: str):
        """Append a content token to the live stream."""
        if not self._streaming:
            self.begin_stream()
        if self._t_first_token == 0.0:
            self._t_first_token = time.monotonic()
        self._text_chunks.append(token)
        self._token_count += 1
        if self._md_live:
            self._md_live.update(Markdown("".join(self._text_chunks)))

    def feed_reasoning(self, token: str):
        """Append a reasoning/thinking token (displayed separately)."""
        self._reasoning_chunks.append(token)
        self._reasoning_token_count += 1

    def set_spinner(self, label: str):
        """Update the spinner label (e.g. during tool execution)."""
        self._stop_md_live()
        self._start_spinner(label)

    def stop_spinner(self):
        self._stop_spinner()

    def finish(self, session_state=None):
        """Finalize the stream and render telemetry footer."""
        self._t_end = time.monotonic()
        self._stop_md_live()
        self._stop_spinner()

        # Show reasoning block if present
        reasoning_text = "".join(self._reasoning_chunks).strip()
        if reasoning_text:
            self.console.print()
            self.console.print(Panel(
                Markdown(reasoning_text),
                title="[dim]reasoning[/dim]",
                border_style="dim #bfa669",
                box=box.ROUNDED,
                padding=(0, 1),
                expand=False,
            ))

        # Telemetry footer
        elapsed = self._t_end - self._t_start
        ttft = (self._t_first_token - self._t_start) if self._t_first_token > 0 else 0
        stream_time = (self._t_end - self._t_first_token) if self._t_first_token > 0 else elapsed
        tps = self._token_count / stream_time if stream_time > 0 else 0

        parts = []
        if self._token_count > 0:
            parts.append(f"{self._token_count} tokens")
            parts.append(_format_tokens_per_sec(self._token_count, stream_time))
        if ttft > 0:
            parts.append(f"TTFT {_format_duration(ttft * 1000)}")
        parts.append(f"total {_format_duration(elapsed * 1000)}")
        if self._reasoning_token_count > 0:
            parts.append(f"{self._reasoning_token_count} reasoning")

        footer = Text()
        footer.append("  ", style="dim")
        for i, part in enumerate(parts):
            if i > 0:
                footer.append(" \u00b7 ", style="dim")
            footer.append(part, style=DIM_GOLD)
        self.console.print(footer)

        # Push stats to session_state for toolbar display
        if session_state is not None:
            session_state["last_tokens"] = self._token_count
            session_state["last_tps"] = tps
            session_state["total_tokens"] = session_state.get("total_tokens", 0) + self._token_count

    @property
    def full_text(self) -> str:
        return "".join(self._text_chunks)

    @property
    def token_count(self) -> int:
        return self._token_count

    @property
    def elapsed_ms(self) -> float:
        end = self._t_end if self._t_end else time.monotonic()
        return (end - self._t_start) * 1000

    # ── Internal ──────────────────────────────────────────────

    def _start_spinner(self, label: str):
        self._stop_spinner()
        self._spinner = Spinner("dots", text=f"  [{GOLD}]{label}[/{GOLD}]", style=GOLD)
        self._spin_live = Live(self._spinner, console=self.console, refresh_per_second=12, transient=True)
        self._spin_live.start()

    def _stop_spinner(self):
        if self._spin_live:
            self._spin_live.stop()
            self._spin_live = None
            self._spinner = None

    def _stop_md_live(self):
        if self._md_live:
            full = "".join(self._text_chunks)
            if full.strip():
                self._md_live.update(Markdown(full))
            self._md_live.stop()
            self._md_live = None
