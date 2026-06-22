"""Side-by-side dual-pane renderer for concurrent multi-model turns.

Owns the single Rich ``Live`` widget that exists per terminal, with a
``Layout`` split 50/50 between two panes. Each pane tracks one model's
stream independently: content buffer, reasoning buffer, spinner label,
telemetry. Events arrive over a ``queue.Queue`` from ``QueueRenderer``
instances running inside the two provider worker threads.

Rendering is throttled to ~20fps globally (not per pane) so a chatty
model doesn't starve the renderer thread. The queue is drained in a
tight loop and the Live updates only when at least one pane changed
since the last frame.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field

from rich.console import Group
from rich.layout import Layout
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from cli.branding import GOLD_HEX, GOLD_DIM_HEX, DOT, GUTTER, MARK, thinking_spinner
from cli.queue_renderer import PaneEvent

# Pause the iTerm2 cursor-color pulse while the dual Live widget is active
# to prevent OSC 12 escape sequences from leaking into the rendered output.
try:
    from iterm import pause_cursor_pulse, resume_cursor_pulse
except ImportError:
    pause_cursor_pulse = resume_cursor_pulse = lambda *a, **kw: None


_FRAME_FPS = 20
_FRAME_PERIOD = 1.0 / _FRAME_FPS


@dataclass
class _PaneState:
    pane_id: str
    model_label: str
    content: list[str] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)
    spinner_label: str | None = "thinking..."
    status: str = "thinking"  # thinking | streaming | done | error | aborted
    tokens: int = 0
    reasoning_tokens: int = 0
    tps: float = 0.0
    ttft_ms: float = 0.0
    elapsed_ms: float = 0.0
    finished: bool = False
    dirty: bool = True
    error: str | None = None
    delivered_at: float = 0.0  # monotonic timestamp when pane finished (for pulse)


class DualPaneRenderer:
    """Live two-column renderer driven by a shared event queue.

    Usage::

        q: queue.Queue[PaneEvent] = queue.Queue()
        renderer = DualPaneRenderer(
            console, event_queue=q,
            left=("supreme", "CroweLM Supreme"),
            right=("prime",   "CroweLM Prime"),
        )
        renderer.start()   # enters Live context, begins draining q
        # ... spawn producer threads that write to q via QueueRenderer ...
        renderer.wait_until_both_finished()
        renderer.stop()    # prints final transcripts
    """

    def __init__(
        self,
        console,
        event_queue: "queue.Queue[PaneEvent]",
        left: tuple[str, str],
        right: tuple[str, str],
    ):
        self.console = console
        self._q = event_queue
        self._panes: dict[str, _PaneState] = {
            left[0]: _PaneState(pane_id=left[0], model_label=left[1]),
            right[0]: _PaneState(pane_id=right[0], model_label=right[1]),
        }
        self._left_id = left[0]
        self._right_id = right[0]

        self._live: Live | None = None
        self._drain_thread: threading.Thread | None = None
        self._stop_flag = threading.Event()
        self._done_flags: dict[str, threading.Event] = {
            left[0]: threading.Event(),
            right[0]: threading.Event(),
        }
        self._layout = self._build_layout()
        self._t_start = 0.0

    # ── Public lifecycle ─────────────────────────────────────────

    def start(self) -> None:
        self._t_start = time.monotonic()
        # Render an initial frame so the panels appear immediately.
        self._refresh_layout(force=True)
        pause_cursor_pulse()
        self._live = Live(
            self._layout,
            console=self.console,
            refresh_per_second=_FRAME_FPS,
            transient=True,
            vertical_overflow="crop",
        )
        self._live.start()
        self._drain_thread = threading.Thread(
            target=self._drain_loop,
            name="dual-renderer-drain",
            daemon=True,
        )
        self._drain_thread.start()

    def wait_until_both_finished(self, timeout: float | None = None) -> bool:
        """Block until both panes have emitted a finish/abort event.

        Returns True if both finished within the timeout, False on timeout.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        for event in self._done_flags.values():
            remaining = (
                None if deadline is None else max(0, deadline - time.monotonic())
            )
            if not event.wait(timeout=remaining):
                return False
        return True

    def stop(self) -> None:
        """Finalize the live widget and emit captured transcripts to scrollback."""
        self._stop_flag.set()
        if self._drain_thread is not None:
            self._drain_thread.join(timeout=2.0)
        if self._live is not None:
            self._live.stop()
            self._live = None
            resume_cursor_pulse()
        self._print_final_transcripts()

    def transcripts(self) -> dict[str, dict[str, str]]:
        """Return captured content/reasoning per pane for session history."""
        return {
            pid: {
                "model_label": pane.model_label,
                "content": "".join(pane.content),
                "reasoning": "".join(pane.reasoning),
                "tokens": pane.tokens,
                "elapsed_ms": pane.elapsed_ms,
                "error": pane.error,
            }
            for pid, pane in self._panes.items()
        }

    # ── Event drain loop (renderer thread) ───────────────────────

    def _drain_loop(self) -> None:
        """Pull events off the queue and update pane state until both finish.

        Uses a blocking queue.get with a short timeout so we stay responsive
        to the stop flag and can finalize cleanly if one provider errors out.
        """
        last_refresh = 0.0
        while not self._stop_flag.is_set():
            try:
                ev = self._q.get(timeout=0.05)
            except queue.Empty:
                now = time.monotonic()
                if now - last_refresh >= _FRAME_PERIOD and self._any_dirty():
                    self._refresh_layout()
                    last_refresh = now
                if all(e.is_set() for e in self._done_flags.values()):
                    break
                continue

            self._apply(ev)

            now = time.monotonic()
            if now - last_refresh >= _FRAME_PERIOD and self._any_dirty():
                self._refresh_layout()
                last_refresh = now

        # Final flush.
        self._refresh_layout(force=True)

    def _apply(self, ev: PaneEvent) -> None:
        pane = self._panes.get(ev.pane_id)
        if pane is None:
            return

        kind = ev.kind
        if kind == "start":
            pane.status = "thinking"
            pane.spinner_label = "thinking..."
        elif kind == "content":
            pane.content.append(ev.payload)
            pane.tokens += 1
            if pane.status != "streaming":
                pane.status = "streaming"
                pane.spinner_label = None
        elif kind == "reasoning":
            pane.reasoning.append(ev.payload)
            pane.reasoning_tokens += 1
        elif kind == "spinner":
            pane.spinner_label = ev.payload
            pane.status = "tooling"
        elif kind == "spinner_stop":
            pane.spinner_label = None
        elif kind == "end_segment":
            pass  # buffer stays; we keep the running transcript in the pane
        elif kind == "finish":
            pane.status = "done"
            pane.finished = True
            pane.spinner_label = None
            pane.delivered_at = time.monotonic()
            if isinstance(ev.payload, dict):
                pane.tokens = ev.payload.get("tokens", pane.tokens)
                pane.reasoning_tokens = ev.payload.get(
                    "reasoning_tokens", pane.reasoning_tokens
                )
                pane.tps = ev.payload.get("tps", 0.0)
                pane.ttft_ms = ev.payload.get("ttft_ms", 0.0)
                pane.elapsed_ms = ev.payload.get("elapsed_ms", 0.0)
            self._done_flags[ev.pane_id].set()
        elif kind == "abort":
            pane.status = "aborted"
            pane.finished = True
            pane.spinner_label = None
            self._done_flags[ev.pane_id].set()
        elif kind == "error":
            pane.status = "error"
            pane.finished = True
            pane.error = str(ev.payload)
            pane.spinner_label = None
            self._done_flags[ev.pane_id].set()

        pane.dirty = True

    # ── Layout construction ──────────────────────────────────────

    def _build_layout(self) -> Layout:
        root = Layout()
        root.split_row(
            Layout(name=self._left_id),
            Layout(name=self._right_id),
        )
        return root

    def _any_dirty(self) -> bool:
        # Also keep refreshing if any pane has an active delivered pulse.
        now = time.monotonic()
        for p in self._panes.values():
            if p.dirty:
                return True
            if p.delivered_at > 0 and (now - p.delivered_at) < 1.2:
                return True
        return False

    def _refresh_layout(self, force: bool = False) -> None:
        now = time.monotonic()
        for pane_id, pane in self._panes.items():
            # Force-refresh panes with an active delivered pulse.
            pulse_active = pane.delivered_at > 0 and (now - pane.delivered_at) < 1.2
            if pane.dirty or force or pulse_active:
                self._layout[pane_id].update(self._render_pane(pane))
                pane.dirty = False
        if self._live is not None:
            self._live.refresh()

    def _render_pane(self, pane: _PaneState):
        title = self._pane_title(pane)
        body = self._pane_body(pane)
        accent = GOLD_DIM_HEX
        if pane.status == "error":
            accent = "red"
        elif pane.status == "done":
            accent = GOLD_HEX
        return Panel(
            body,
            title=title,
            title_align="left",
            border_style=accent,
            padding=(0, 1),
        )

    def _pane_title(self, pane: _PaneState) -> Text:
        title = Text()
        # Delivered pulse: for ~1.2s after a pane finishes, show a pulsing ◆
        # that fades from warm-white to gold, matching the deepparallel
        # convergence result delivery aesthetic.
        if pane.delivered_at > 0:
            elapsed = time.monotonic() - pane.delivered_at
            if elapsed < 1.2:
                pulse_t = elapsed / 1.2
                # Pulse: bright at start, settling to gold
                from cli.branding import _lerp_hex

                pulse_color = _lerp_hex("#fff0c8", GOLD_HEX, pulse_t)
                title.append(f"{MARK} ", style=f"bold {pulse_color}")

        title.append(pane.model_label, style=f"bold {GOLD_HEX}")
        meta = self._pane_meta(pane)
        if meta:
            title.append(f"  {DOT}  ", style="dim")
            title.append(meta, style=GOLD_DIM_HEX)
        return title

    def _pane_meta(self, pane: _PaneState) -> str:
        if pane.status == "error":
            return "error"
        if pane.status == "aborted":
            return "aborted"
        if pane.finished:
            tokens = f"{pane.tokens} tok"
            tps = f"{int(pane.tps)} tok/s" if pane.tps >= 1 else ""
            parts = [p for p in (tokens, tps) if p]
            return f" {DOT} ".join(parts)
        if pane.status == "streaming":
            return f"streaming  {pane.tokens} tok"
        if pane.status == "tooling":
            return pane.spinner_label or "tooling"
        return "thinking"

    def _pane_body(self, pane: _PaneState):
        if pane.error:
            return Text(pane.error, style="red")

        text = "".join(pane.content).strip()
        reasoning = "".join(pane.reasoning).strip()

        blocks = []
        if reasoning and not text:
            blocks.append(Text(reasoning[-2000:], style=GOLD_DIM_HEX))
        if text:
            blocks.append(Markdown(text))
        if not blocks:
            blocks.append(thinking_spinner(pane.spinner_label or "thinking"))

        return Group(*blocks)

    # ── Final transcript printing ────────────────────────────────

    def _print_final_transcripts(self) -> None:
        self.console.print()
        for pane_id in (self._left_id, self._right_id):
            pane = self._panes[pane_id]
            transcript = "".join(pane.content).strip()
            if pane.error:
                header = Text()
                header.append("  ")
                header.append(pane.model_label, style=f"bold {GOLD_HEX}")
                header.append("  ·  ", style="dim")
                header.append("error", style="red")
                self.console.print(header)
                self.console.print(Text("    " + pane.error, style="red"))
                continue
            if not transcript:
                continue

            footer_parts = [
                f"{pane.tokens} tok",
                f"{int(pane.tps)} tok/s" if pane.tps >= 1 else "",
                f"TTFT {int(pane.ttft_ms)}ms" if pane.ttft_ms > 0 else "",
                f"total {int(pane.elapsed_ms)}ms" if pane.elapsed_ms > 0 else "",
            ]
            footer = f" {DOT} ".join(p for p in footer_parts if p)

            title = Text()
            title.append(pane.model_label, style=f"bold {GOLD_HEX}")
            if footer:
                title.append(f"  {DOT}  ", style="dim")
                title.append(footer, style=GOLD_DIM_HEX)

            panel = Panel(
                Markdown(transcript),
                title=title,
                title_align="left",
                border_style=GOLD_DIM_HEX,
                padding=(0, 1),
            )
            from rich.padding import Padding

            self.console.print(Padding(panel, (0, 0, 0, GUTTER)))
