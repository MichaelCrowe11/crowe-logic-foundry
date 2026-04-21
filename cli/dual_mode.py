"""Dual-model orchestrator: runs two CroweLM tiers in parallel with side-by-side rendering.

Entered via the ``/dual on`` session command. While active, each user
turn fans out to both models simultaneously (one worker thread per
model), and their streams flow into a single
:class:`cli.dual_renderer.DualPaneRenderer` on the main thread.

Design choices:

* The providers keep their own message history across turns. That
  means follow-up questions include each model's prior answer in its
  own context, which is what users expect when comparing models.
* Tool calls are **allowed** in dual mode. They surface as spinner
  labels inside each pane. Both models sharing the filesystem is a
  user-facing trade-off, not a bug. Users who need sandboxed tools
  should toggle dual mode off first.
* The default pairing is ``CroweLM Supreme`` (flagship reasoning)
  paired with ``CroweLM Eclipse`` (flagship cloud reasoning). Both
  are resolved at command time via ``resolve_model_config`` so
  alias changes in ``config/agent_config.py`` flow through without
  edits here.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Any, Callable

from cli.dual_renderer import DualPaneRenderer
from cli.queue_renderer import QueueRenderer, PaneEvent


DEFAULT_LEFT_ALIAS = "supreme"
DEFAULT_RIGHT_ALIAS = "eclipse"


class DualModeState:
    """Persistent toggle + resolved model configs for the active session."""

    def __init__(self) -> None:
        self.active: bool = False
        self.left_cfg: dict | None = None
        self.right_cfg: dict | None = None

    def summary(self) -> str:
        if not self.active:
            return "dual mode: off"
        left = self.left_cfg["label"] if self.left_cfg else "?"
        right = self.right_cfg["label"] if self.right_cfg else "?"
        return f"dual mode: on  ·  {left}  ‖  {right}"


def handle_dual_command(
    user_input: str,
    state: DualModeState,
    console,
    session_state: dict,
) -> bool:
    """Handle ``/dual`` commands. Returns True if the input was a /dual command."""
    text = user_input.strip()
    if text.lower() == "/dual":
        console.print(f"  [#bfa669]{state.summary()}[/#bfa669]")
        if not state.active:
            console.print(
                f"  [dim]/dual on    enable side-by-side {DEFAULT_LEFT_ALIAS} + {DEFAULT_RIGHT_ALIAS}\n"
                "  /dual off   disable\n"
                "  /dual <left> <right>   custom pairing (aliases or model names)[/dim]"
            )
        return True

    if text.lower() == "/dual on":
        try:
            left, right = _resolve_pair(DEFAULT_LEFT_ALIAS, DEFAULT_RIGHT_ALIAS)
        except ValueError as exc:
            console.print(f"  [red]{exc}[/red]")
            return True
        state.active = True
        state.left_cfg = left
        state.right_cfg = right
        session_state["dual_active"] = True
        session_state["active_model"] = f"{left['label']}  ‖  {right['label']}"
        console.print(f"  [#6fbf73]{state.summary()}[/#6fbf73]")
        return True

    if text.lower() == "/dual off":
        state.active = False
        session_state["dual_active"] = False
        console.print("  [#bfa669]dual mode: off[/#bfa669]")
        return True

    lower = text.lower()
    if lower.startswith("/dual "):
        parts = text.split()
        if len(parts) == 3:
            try:
                left, right = _resolve_pair(parts[1], parts[2])
            except ValueError as exc:
                console.print(f"  [red]{exc}[/red]")
                return True
            state.active = True
            state.left_cfg = left
            state.right_cfg = right
            session_state["dual_active"] = True
            session_state["active_model"] = f"{left['label']}  ‖  {right['label']}"
            console.print(f"  [#6fbf73]{state.summary()}[/#6fbf73]")
            return True
        console.print("  [red]usage: /dual <left> <right>[/red]")
        return True

    return False


def _resolve_pair(left_alias: str, right_alias: str) -> tuple[dict, dict]:
    """Resolve two model aliases to config dicts, raising a friendly error on miss."""
    from config.agent_config import resolve_model_config

    left = resolve_model_config(left_alias)
    right = resolve_model_config(right_alias)
    if left is None:
        raise ValueError(f"Unknown model alias: {left_alias}")
    if right is None:
        raise ValueError(f"Unknown model alias: {right_alias}")
    if left is right:
        raise ValueError(
            f"Both aliases resolved to {left['label']}. Pick two different models."
        )
    return left, right


# ─── Turn execution ────────────────────────────────────────────────

def run_dual_turn(
    user_input: str,
    state: DualModeState,
    *,
    console,
    session_state: dict,
    get_provider: Callable[[dict, str], Any],
    runtime_instructions: Callable[[dict, dict], str],
    get_orchestrator: Callable[[], Any],
) -> None:
    """Execute one user turn across two models concurrently.

    :param user_input: the raw user prompt for this turn
    :param state: the session's dual-mode state (resolved configs live here)
    :param get_provider: callable ``(model_cfg, system_instructions) -> provider``
        that dispatches to ``_get_anthropic_provider`` / ``_get_hosted_openai_provider``
        / etc. based on the provider kind
    :param runtime_instructions: callable ``(model_cfg, session_state) -> str``
        that builds per-turn system instructions
    :param get_orchestrator: callable returning the Crowe-Synapse orchestrator
    """
    if not state.active or state.left_cfg is None or state.right_cfg is None:
        raise RuntimeError("run_dual_turn called without an active dual pair")

    event_queue: "queue.Queue[PaneEvent]" = queue.Queue()
    renderer = DualPaneRenderer(
        console,
        event_queue=event_queue,
        left=("left", state.left_cfg["label"]),
        right=("right", state.right_cfg["label"]),
    )

    left_result: dict[str, Any] = {}
    right_result: dict[str, Any] = {}

    def _worker(
        pane_id: str,
        model_cfg: dict,
        result_slot: dict,
    ) -> None:
        q_renderer = QueueRenderer(pane_id, event_queue, model_cfg["label"])
        try:
            sys_instructions = runtime_instructions(model_cfg, session_state)
            provider = get_provider(model_cfg, sys_instructions)
            provider.add_user_message(user_input)
            provider.stream_response(
                console=None,
                render_tool_card=_noop_tool_card,
                session_state=session_state,
                _get_orchestrator=get_orchestrator,
                renderer=q_renderer,
            )
            result_slot["answer"] = q_renderer.full_answer
            result_slot["reasoning"] = q_renderer.full_reasoning
            result_slot["tokens"] = q_renderer.token_count
        except Exception as exc:
            result_slot["error"] = f"{type(exc).__name__}: {exc}"
            event_queue.put(PaneEvent(pane_id, "error", result_slot["error"], time.monotonic()))

    renderer.start()
    try:
        t_left = threading.Thread(
            target=_worker,
            args=("left", state.left_cfg, left_result),
            name=f"dual-{state.left_cfg['label']}",
            daemon=True,
        )
        t_right = threading.Thread(
            target=_worker,
            args=("right", state.right_cfg, right_result),
            name=f"dual-{state.right_cfg['label']}",
            daemon=True,
        )
        t_left.start()
        t_right.start()
        renderer.wait_until_both_finished(timeout=15 * 60)
        t_left.join(timeout=5)
        t_right.join(timeout=5)
    finally:
        renderer.stop()

    # Persist last-turn transcripts on session_state so /transcript shows both.
    transcripts = renderer.transcripts()
    session_state["last_dual_transcripts"] = transcripts
    session_state["last_answer_text"] = "\n\n---\n\n".join(
        f"**{t['model_label']}**\n\n{t['content']}".strip()
        for t in transcripts.values()
        if t["content"].strip()
    )


def _noop_tool_card(*args, **kwargs) -> None:
    """Tool-card renderer for dual mode (intentionally silent).

    Tool invocations still show up as spinner labels inside each pane
    via the QueueRenderer's ``set_spinner`` event, so users can see
    when a model is running a tool. The separate tool result card
    would clobber the dual layout, so we drop it on the floor.
    """
    return None
