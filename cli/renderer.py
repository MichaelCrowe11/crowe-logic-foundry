"""
CroweLM Scientific Streaming Renderer

High-fidelity terminal rendering with real-time telemetry:
  - Avatar displayed during thinking and streaming phases
  - Token-by-token streaming with live markdown at 20fps
  - Response header: raw favicon (iTerm2 protocol) + Rich label (cursor-tracked)
  - Post-response telemetry: tokens/sec, total tokens, TTFT, elapsed
  - Reasoning block display for thinking models (Kimi K2.5)
"""

import os
import sys
import time
import re
from rich.text import Text
from rich.live import Live
from rich.spinner import Spinner

from cli.branding import (
    DOT,
    GUTTER,
    GOLD_HEX as GOLD,
    GOLD_DIM_HEX as DIM_GOLD,
    build_reasoning_panel,
    build_transcript_markdown,
)
from cli.session_runtime import update_session_runtime

# Refresh rate for live displays (frames per second).
#
# Rich's Live widget throttles how often the *current* renderable is
# drawn to the terminal — but constructing a Markdown renderable parses
# the full string each time, and Live does NOT throttle that. We must
# throttle the parse step ourselves, otherwise feed() does an O(n) join
# + Markdown parse on every single token (250x more parsing work than
# the user can ever see at 20fps).
_STREAM_FPS = 20
# Reasoning panels render plain text at a lower rate — reasoning streams
# can be very long, and 8 FPS feels smooth without burning CPU on
# re-renders.
_REASONING_FPS = 8
_COMPACT_REASONING_LABELS = {"CroweLM Apex", "CroweLM Titan"}
_COMPACT_REASONING_MAX_CHARS = 420
_COMPACT_REASONING_LIVE_CHARS = 240


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
        renderer = StreamRenderer(console, model_label, favicon)
        renderer.start()             # shows avatar + label + spinner
        renderer.begin_stream()      # transitions to live markdown
        renderer.feed(token)         # append token
        renderer.feed_reasoning(t)   # append reasoning token (thinking models)
        renderer.finish()            # renders telemetry footer
    """

    def __init__(self, console, model_label: str, favicon: str = ""):
        self.console = console
        self.model_label = model_label
        self.favicon = favicon

        # Segmented text state — a "segment" is one streaming pass between
        # tool calls. We clear _text_chunks at the start of each segment so
        # the Live markdown only renders the current segment, never duplicating
        # previously-rendered content from earlier tool-calling rounds. The
        # provider loop reads current_segment_text BEFORE end_segment() is
        # called, then end_segment() clears the buffer for the next round.
        self._text_chunks: list[str] = []
        self._reasoning_chunks: list[str] = []
        self._full_text_chunks: list[str] = []
        self._full_reasoning_chunks: list[str] = []
        self._token_count = 0
        self._reasoning_token_count = 0
        # Throttles for the Live widgets. Rich's Live throttles redraw at
        # refresh_per_second but we must also throttle the construction of
        # the renderable itself, since parsing Markdown / re-joining the
        # chunk list per token would dominate the streaming hot path.
        self._last_md_update: float = 0.0
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
        self._compact_reasoning = model_label in _COMPACT_REASONING_LABELS

        self._t_start = 0.0
        self._t_first_token = 0.0
        self._t_end = 0.0

        # Quality Stack guardrail chain. Created on demand only when
        # CROWELM_GUARDRAILS=on is set in the environment, so existing
        # behavior is unchanged unless explicitly opted in.
        self._guardrail_chain = None
        self._guardrail_telemetry: dict | None = None
        if os.environ.get("CROWELM_GUARDRAILS", "").lower() in {"on", "1", "true", "yes"}:
            try:
                from cli.guardrail_pipeline import pipeline_for_session
                self._guardrail_chain = pipeline_for_session()
            except ImportError:
                pass  # guardrail module not available; behave as before

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

        # Keep the live widget transient so scrollback only contains the
        # finalized panel, not intermediate redraw frames. During the live
        # phase we can safely crop to the viewport; _stop_md_live() prints the
        # full final panel once streaming ends.
        self._md_live = Live(
            self._build_answer_panel("", live=True),
            console=self.console,
            refresh_per_second=_STREAM_FPS,
            vertical_overflow="crop",
            transient=True,
        )
        self._md_live.start()
        self._last_md_update = 0.0

    def feed(self, token: str):
        """Append a content token to the live stream.

        The Markdown re-parse is throttled to _STREAM_FPS - see the
        comment on _STREAM_FPS for why this matters. The unthrottled
        token is always appended to _text_chunks; _stop_md_live does
        a final flush so nothing is lost between the last throttled
        tick and the end of the stream.
        """
        if not self._streaming:
            self.begin_stream()
        if self._t_first_token == 0.0:
            self._t_first_token = time.monotonic()

        # Quality Stack: run the token through the guardrail chain before
        # it touches the live widget. The chain holds back a tail buffer
        # so partial credentials at chunk boundaries cannot leak. The
        # final flush in finish() drains the buffer.
        if self._guardrail_chain is not None:
            token = self._guardrail_chain.stream(token)
            if not token:
                return

        self._text_chunks.append(token)
        self._full_text_chunks.append(token)
        self._token_count += 1
        if self._md_live is not None:
            now = time.monotonic()
            if now - self._last_md_update >= (1.0 / _STREAM_FPS):
                self._last_md_update = now
                self._md_live.update(self._build_answer_panel("".join(self._text_chunks), live=True))

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
            # A transient live panel prevents repeated reasoning redraw frames
            # from being left behind in scrollback. We print one finalized
            # "captured" panel when the reasoning phase ends.
            self._reasoning_live = Live(
                self._build_reasoning_panel("", live=True),
                console=self.console,
                refresh_per_second=_REASONING_FPS,
                vertical_overflow="crop",
                transient=True,
            )
            self._reasoning_live.start()
            self._last_reason_update = 0.0

        self._reasoning_chunks.append(token)
        self._full_reasoning_chunks.append(token)
        self._reasoning_token_count += 1

        if self._reasoning_live is not None:
            now = time.monotonic()
            if now - self._last_reason_update >= (1.0 / _REASONING_FPS):
                self._last_reason_update = now
                self._reasoning_live.update(
                    self._build_reasoning_panel("".join(self._reasoning_chunks), live=True)
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

        # Quality Stack: drain the scrubber's hold-back buffer. Anything
        # remaining is safe to emit because no more tokens are coming.
        # Then capture per-turn guardrail telemetry for downstream consumers.
        if self._guardrail_chain is not None:
            tail = self._guardrail_chain.flush_stream()
            if tail:
                self._text_chunks.append(tail)
                self._full_text_chunks.append(tail)
            # Run the final accumulated output through block-level scrub as a
            # belt-and-suspenders safety net for anything the streaming buffer
            # missed (e.g. very short outputs that fit entirely in hold-back).
            full_text = "".join(self._text_chunks)
            cleaned = self._guardrail_chain.scrub_output(full_text)
            if cleaned != full_text:
                self._text_chunks = [cleaned]
                self._full_text_chunks = [cleaned]
            # Scope budget check at end of turn. WARN/INTERRUPT events are
            # recorded on the chain; downstream consumers can act on them.
            self._guardrail_chain.check_budget(
                reasoning_tokens=self._reasoning_token_count,
                output_tokens=self._token_count,
            )
            try:
                from cli.guardrail_pipeline import telemetry_summary
                self._guardrail_telemetry = telemetry_summary(self._guardrail_chain)
            except ImportError:
                self._guardrail_telemetry = None

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
        footer.append(" " * GUTTER, style="dim")
        for i, part in enumerate(parts):
            if i > 0:
                footer.append(f" {DOT} ", style="dim")
            footer.append(part, style=DIM_GOLD)
        self.console.print(footer)

        # Push stats to session_state for toolbar display
        if session_state is not None:
            session_state["last_tokens"] = self._token_count
            session_state["last_tps"] = tps
            session_state["total_tokens"] = session_state.get("total_tokens", 0) + self._token_count
            self._persist_transcript(session_state)

    def abort(self, session_state=None):
        """Best-effort cleanup when a turn is interrupted mid-stream."""
        self._t_end = time.monotonic()
        self.end_segment()
        self._stop_spinner()
        if session_state is not None:
            self._persist_transcript(session_state)

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

    def _build_answer_panel(self, text: str, *, live: bool) -> object:
        """Build the live/final answer renderable."""
        meta = "streaming" if live else "final"
        return build_transcript_markdown(self.console, text, title="answer", meta=meta)

    def _build_reasoning_panel(self, text: str, *, live: bool) -> object:
        """Build the live/final reasoning renderable."""
        reasoning_text, meta = self._render_reasoning_text(text, live=live)
        return build_reasoning_panel(self.console, reasoning_text, live=live, meta=meta)

    def _start_spinner(self, label: str):
        self._stop_spinner()
        self._spinner = Spinner("dots", text=f"  [{GOLD}]{label}[/]", style=GOLD)
        self._spin_live = Live(self._spinner, console=self.console, refresh_per_second=12, transient=True)
        self._spin_live.start()

    def _persist_transcript(self, session_state: dict) -> None:
        """Store the latest answer/reasoning transcript in memory and on disk."""
        answer_text = "".join(self._full_text_chunks).strip()
        reasoning_text = "".join(self._full_reasoning_chunks).strip()
        session_state["last_answer_text"] = answer_text
        session_state["last_reasoning_text"] = reasoning_text
        session_state["active_model"] = self.model_label
        session_id = session_state.get("session_id", "")
        if session_id:
            update_session_runtime(
                session_id,
                last_answer_text=answer_text,
                last_reasoning_text=reasoning_text,
                last_model=self.model_label,
            )

    @staticmethod
    def _normalize_reasoning_text(text: str) -> str:
        """Normalize streamed reasoning into readable plain-text summary prose."""
        text = text.replace("\r\n", "\n")
        text = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    @classmethod
    def _compact_reasoning_text(cls, text: str, *, live: bool) -> str:
        """Collapse verbose reasoning into a short summary snippet."""
        normalized = cls._normalize_reasoning_text(text)
        if not normalized:
            return ""

        limit = _COMPACT_REASONING_LIVE_CHARS if live else _COMPACT_REASONING_MAX_CHARS
        if len(normalized) <= limit:
            return normalized

        sentences = re.split(r"(?<=[.!?])\s+", normalized)
        compact: list[str] = []
        total = 0
        for sentence in sentences:
            if not sentence:
                continue
            next_total = total + len(sentence) + (1 if compact else 0)
            if compact and next_total > limit:
                break
            compact.append(sentence)
            total = next_total
            if total >= limit:
                break

        if not compact:
            return normalized[: limit - 1].rstrip() + "…"

        compact_text = " ".join(compact).strip()
        if len(compact_text) < len(normalized):
            compact_text = compact_text.rstrip(". ") + "…"
        return compact_text

    def _render_reasoning_text(self, text: str, *, live: bool) -> tuple[str, str]:
        """Select full vs compact reasoning text and panel metadata."""
        if not self._compact_reasoning:
            return text, "live" if live else "captured"
        return self._compact_reasoning_text(text, live=live), "live" if live else "summary"

    def _stop_spinner(self):
        if self._spin_live:
            self._spin_live.stop()
            self._spin_live = None
            self._spinner = None

    def _stop_md_live(self):
        """Stop the markdown Live and clear the segment buffer.

        Tears down the transient live widget, then prints one finalized panel
        so scrollback contains a single stable transcript block instead of a
        series of redraw frames. Callers must read current_segment_text BEFORE
        calling this.
        """
        if self._md_live:
            full = "".join(self._text_chunks)
            self._md_live.stop()
            self._md_live = None
            if full.strip():
                self.console.print(self._build_answer_panel(full, live=False))
        # Reset unconditionally so _streaming and _text_chunks stay in sync
        # with the widget state even across idempotent calls.
        self._text_chunks = []
        self._streaming = False

    def _stop_reasoning_live(self):
        """Stop the live reasoning panel if active.

        When a transient live panel was active, print one finalized reasoning
        panel after stopping it so scrollback captures a single stable block.
        When no live panel was active (reasoning arrived after content
        streaming), chunks are left in place for _flush_pending_reasoning() to
        render inline.
        """
        if self._reasoning_live:
            full = "".join(self._reasoning_chunks).strip()
            self._reasoning_live.stop()
            self._reasoning_live = None
            if full:
                self.console.print(self._build_reasoning_panel(full, live=False))
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
        self.console.print(self._build_reasoning_panel(text, live=False))
