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
# Reasoning panels render plain text at a lower rate — reasoning streams
# can be very long, and 8 FPS feels smooth without burning CPU on Markdown
# re-parsing 20 times per second.
_REASONING_FPS = 8


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

        # Segmented text state — a "segment" is one streaming pass between
        # tool calls. We clear _text_chunks at the start of each segment so
        # the Live markdown only renders the current segment, never duplicating
        # previously-rendered content from earlier tool-calling rounds. The
        # provider loop reads current_segment_text BEFORE end_segment() is
        # called, then end_segment() clears the buffer for the next round.
        self._text_chunks: list[str] = []
        self._reasoning_chunks: list[str] = []
        self._token_count = 0
        self._reasoning_token_count = 0
        # Throttle for reasoning Live updates — Markdown re-parsing is
        # expensive, and reasoning streams can be 1k+ tokens. We update at
        # most ~_REASONING_FPS times per second; the Live widget picks up
        # the latest renderable on its next tick anyway.
        self._last_reason_update: float = 0.0

        self._spinner = None
        self._spin_live = None
        self._md_live = None
        # Live panel for reasoning tokens during the thinking phase. Set when
        # the first reasoning token arrives before any content tokens; cleared
        # (and the panel finalized in place) once content streaming begins.
        self._reasoning_live = None

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

        # Label via Rich — keeps cursor tracking accurate for Live widgets below.
        # Use [/] (close-most-recent) so the open/close tags don't have to match
        # by name — `[bold #bfa669]...[/#bfa669]` is malformed in Rich's strict parser.
        self.console.print(f"[bold {GOLD}]{self.model_label}[/]")

    def start(self):
        """Show avatar header + thinking spinner."""
        self._t_start = time.monotonic()
        self._show_header()
        self._start_spinner("thinking...")

    def begin_stream(self):
        """Transition from spinner/reasoning panel to live streaming markdown.

        Called implicitly by the first feed() of each segment. A segment is
        one streaming pass between tool calls — each begins with an empty
        _text_chunks so the Live widget renders only this segment, never
        duplicating previously-streamed content.
        """
        if self._streaming:
            return
        self._stop_spinner()
        # Finalize any in-progress reasoning panel before content starts.
        # The panel stays printed in scrollback above the upcoming content.
        self._stop_reasoning_live()
        self._streaming = True
        self._text_chunks = []
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
        """Append a reasoning/thinking token.

        If reasoning arrives during the thinking phase (before any content
        token has been streamed in this segment), we replace the spinner with
        a live Panel that updates as tokens arrive, so the user can watch
        the model think in real time. Once content begins streaming for this
        segment, the panel is finalized in place. Reasoning tokens that
        arrive AFTER content streaming are accumulated and flushed inline
        at the next segment boundary by _flush_pending_reasoning().

        The per-token Live update is throttled to _REASONING_FPS — Rich's
        Markdown/Text rendering is expensive and the Live widget only ticks
        at refresh_per_second anyway, so faster updates are pure waste.
        """
        # Start a live reasoning panel if we're not yet streaming content
        # for this segment and no panel is active.
        if self._reasoning_live is None and not self._streaming:
            self._stop_spinner()
            self._show_header()
            self._reasoning_live = Live(
                self._build_reasoning_panel(""),
                console=self.console,
                refresh_per_second=_REASONING_FPS,
                vertical_overflow="visible",
            )
            self._reasoning_live.start()
            self._last_reason_update = 0.0

        self._reasoning_chunks.append(token)
        self._reasoning_token_count += 1

        if self._reasoning_live is not None:
            now = time.monotonic()
            if now - self._last_reason_update >= (1.0 / _REASONING_FPS):
                self._last_reason_update = now
                self._reasoning_live.update(
                    self._build_reasoning_panel("".join(self._reasoning_chunks))
                )

    def end_segment(self):
        """Finalize the current streaming segment without starting a spinner.

        Public API for the provider loop to call between rounds. Stops the
        markdown Live (preserving its content in scrollback), finalizes any
        active reasoning Live, and flushes any pending post-content reasoning
        as an inline panel. After this returns, the next feed() will begin
        a fresh segment with empty buffers.
        """
        self._stop_md_live()
        self._stop_reasoning_live()
        self._flush_pending_reasoning()

    def set_spinner(self, label: str):
        """Update the spinner label (e.g. during tool execution).

        Finalizes the current segment, then starts a new spinner. Resets
        _streaming so the next feed() call begins a fresh segment — without
        this reset, round 2's content would re-render round 1's chunks via
        the new Live widget, duplicating output on screen.
        """
        self.end_segment()
        self._start_spinner(label)

    def stop_spinner(self):
        self._stop_spinner()

    def finish(self, session_state=None):
        """Finalize the stream and render telemetry footer."""
        self._t_end = time.monotonic()
        # end_segment() handles md_live, reasoning_live, and flushes any
        # tail-end reasoning that arrived after content but was never shown
        # in a live panel.
        self.end_segment()
        self._stop_spinner()

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
    def current_segment_text(self) -> str:
        """Text streamed in the active segment (works while Live is running).

        Provider loops read this BEFORE calling end_segment() to build the
        per-round assistant message. Using an accumulated cross-segment
        buffer here would corrupt the message history with duplicated
        content — the model would echo it back, causing visible duplication.
        """
        return "".join(self._text_chunks)

    @property
    def token_count(self) -> int:
        return self._token_count

    @property
    def elapsed_ms(self) -> float:
        end = self._t_end if self._t_end else time.monotonic()
        return (end - self._t_start) * 1000

    # ── Internal ──────────────────────────────────────────────

    def _build_reasoning_panel(self, text: str) -> Panel:
        """Build the reasoning panel widget for live updates.

        Uses Rich Text (plain prose rendering) rather than Markdown —
        reasoning is typically internal monologue without tables or code
        blocks, and Text rendering is an order of magnitude cheaper than
        Markdown parsing when streaming at high token rates.
        """
        body = Text(text, style="dim") if text.strip() else Text("thinking...", style="dim italic")
        return Panel(
            body,
            title="[dim]reasoning[/dim]",
            border_style="dim #bfa669",
            box=box.ROUNDED,
            padding=(0, 1),
            expand=False,
        )

    def _start_spinner(self, label: str):
        self._stop_spinner()
        self._spinner = Spinner("dots", text=f"  [{GOLD}]{label}[/]", style=GOLD)
        self._spin_live = Live(self._spinner, console=self.console, refresh_per_second=12, transient=True)
        self._spin_live.start()

    def _stop_spinner(self):
        if self._spin_live:
            self._spin_live.stop()
            self._spin_live = None
            self._spinner = None

    def _stop_md_live(self):
        """Stop the markdown Live and clear the segment buffer.

        Does one final update with the completed text so scrollback captures
        the full segment, then tears down the Live widget and resets state
        so the next segment starts fresh. Callers must read
        current_segment_text BEFORE calling this.
        """
        if self._md_live:
            full = "".join(self._text_chunks)
            if full.strip():
                self._md_live.update(Markdown(full))
            self._md_live.stop()
            self._md_live = None
        # Reset unconditionally so _streaming and _text_chunks stay in sync
        # with the widget state even across idempotent calls.
        self._text_chunks = []
        self._streaming = False

    def _stop_reasoning_live(self):
        """Stop the live reasoning panel if active.

        Only clears _reasoning_chunks when a live panel WAS active — the
        reasoning was already shown to the user on screen, so the chunks
        are not needed. When no live panel was active (reasoning arrived
        after content streaming), chunks are left in place for
        _flush_pending_reasoning() to render inline.
        """
        if self._reasoning_live:
            full = "".join(self._reasoning_chunks).strip()
            if full:
                self._reasoning_live.update(self._build_reasoning_panel(full))
            self._reasoning_live.stop()
            self._reasoning_live = None
            self._reasoning_chunks = []
            self._last_reason_update = 0.0

    def _flush_pending_reasoning(self):
        """Render any accumulated reasoning chunks as an inline panel.

        Used at segment boundaries to display reasoning that arrived AFTER
        content streaming in the previous segment (no live panel was active
        to show it in real time). Without this, tail-end reasoning from
        round N would leak into round N+1's live panel, visually mashing
        the two together.
        """
        if not self._reasoning_chunks:
            return
        text = "".join(self._reasoning_chunks).strip()
        self._reasoning_chunks = []
        if not text:
            return
        self.console.print()
        self.console.print(self._build_reasoning_panel(text))
