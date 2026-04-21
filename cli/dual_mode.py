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

# Automatic fallback chain for the right pane when the preferred alias is
# paywalled or unreachable. Ordered from most-desired to most-available.
# Each entry must resolve to a distinct model from DEFAULT_LEFT_ALIAS.
RIGHT_FALLBACK_CHAIN = ["eclipse", "crescent", "prime"]

# Default synthesizer config. Supreme is the most capable tier so it produces
# the best merged output, and the user has already paid for the two-model turn,
# so adding a single Supreme turn for synthesis is the smallest incremental
# cost for the largest quality jump.
DEFAULT_SYNTH_ALIAS = "supreme"
SYNTH_MODES = ("merge", "judge", "diff")
DEFAULT_SYNTH_MODE = "merge"


SYNTH_PROMPTS = {
    "merge": (
        "You are CroweLM's synthesis layer. Two peer models just answered the "
        "same user question independently. Produce a single merged response "
        "that keeps the strongest claims from both, resolves contradictions by "
        "reasoning from evidence, and drops filler. Do not mention the two "
        "source models. Do not narrate the merging process. Write as if this "
        "were the original answer."
    ),
    "judge": (
        "You are CroweLM's synthesis layer. Two peer models answered the same "
        "user question. Pick the stronger answer and explain in two sentences "
        "why. Then restate that answer in clean final form. Do not hedge. Do "
        "not say 'both are good'. Commit."
    ),
    "diff": (
        "You are CroweLM's synthesis layer. Two peer models answered the same "
        "user question. Identify: (1) where they agree (2 bullets max), "
        "(2) where they disagree and which is more defensible with one "
        "sentence of reasoning, (3) what both missed. Be terse. No filler."
    ),
}


class DualModeState:
    """Persistent toggle + resolved model configs for the active session."""

    def __init__(self) -> None:
        self.active: bool = False
        self.left_cfg: dict | None = None
        self.right_cfg: dict | None = None
        # Synthesis knobs. Default off so users opt in and accept the extra
        # per-turn API cost knowingly.
        self.synth_active: bool = False
        self.synth_cfg: dict | None = None
        self.synth_mode: str = DEFAULT_SYNTH_MODE

    def summary(self) -> str:
        if not self.active:
            return "dual mode: off"
        left = self.left_cfg["label"] if self.left_cfg else "?"
        right = self.right_cfg["label"] if self.right_cfg else "?"
        base = f"dual mode: on  ·  {left}  ‖  {right}"
        if self.synth_active and self.synth_cfg:
            base += f"  ‖  synth: {self.synth_cfg['label']} ({self.synth_mode})"
        return base


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
            left_cfg = _resolve_single(DEFAULT_LEFT_ALIAS)
            right_cfg = _resolve_right_with_fallback(left_cfg, console)
        except ValueError as exc:
            console.print(f"  [red]{exc}[/red]")
            return True
        state.active = True
        state.left_cfg = left_cfg
        state.right_cfg = right_cfg
        session_state["dual_active"] = True
        session_state["active_model"] = f"{left_cfg['label']}  ‖  {right_cfg['label']}"
        console.print(f"  [#6fbf73]{state.summary()}[/#6fbf73]")
        return True

    if text.lower() == "/dual off":
        state.active = False
        state.synth_active = False
        session_state["dual_active"] = False
        console.print("  [#bfa669]dual mode: off[/#bfa669]")
        return True

    # Synthesis subcommands: /dual synth on|off, /dual synth mode <name>,
    # /dual synth model <alias>. Handled before the generic /dual <a> <b>
    # path so "synth" isn't misread as a model alias.
    lower = text.lower()
    if lower.startswith("/dual synth"):
        return _handle_synth_command(text, state, console)

    if lower.startswith("/dual "):
        parts = text.split()
        if len(parts) == 3:
            try:
                left, right = _resolve_pair(parts[1], parts[2])
            except ValueError as exc:
                console.print(f"  [red]{exc}[/red]")
                return True

            # Preflight both explicitly-chosen models. For a user-chosen
            # pair we don't walk the fallback chain; we report and abort
            # so the user sees why their pick didn't take.
            for side_label, cfg in (("left", left), ("right", right)):
                ok, reason = _preflight_model(cfg)
                if not ok:
                    console.print(
                        f"  [red]{cfg['label']} ({side_label}) unavailable: {reason}[/red]"
                    )
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


def _handle_synth_command(text: str, state: DualModeState, console) -> bool:
    """Handle ``/dual synth ...`` subcommands.

    Grammar:
        /dual synth              Show current synth status.
        /dual synth on           Enable with the last-configured synthesizer.
        /dual synth off          Disable.
        /dual synth mode <name>  Set synthesis style (merge, judge, diff).
        /dual synth model <a>    Set synthesizer model alias.
    """
    parts = text.split()
    # parts[0] = "/dual", parts[1] = "synth"
    tail = parts[2:] if len(parts) > 2 else []

    if not tail:
        if state.synth_active and state.synth_cfg:
            console.print(
                f"  [#bfa669]synth on · {state.synth_cfg['label']} · mode={state.synth_mode}[/#bfa669]"
            )
        else:
            console.print("  [#bfa669]synth off[/#bfa669]")
        console.print(
            "  [dim]/dual synth on           enable post-stream synthesis\n"
            "  /dual synth off          disable\n"
            "  /dual synth mode <m>     merge | judge | diff\n"
            "  /dual synth model <a>    set synthesizer model alias[/dim]"
        )
        return True

    head = tail[0].lower()

    if head == "on":
        if state.synth_cfg is None:
            try:
                state.synth_cfg = _resolve_single(DEFAULT_SYNTH_ALIAS)
            except ValueError as exc:
                console.print(f"  [red]{exc}[/red]")
                return True
        state.synth_active = True
        console.print(
            f"  [#6fbf73]synth on · {state.synth_cfg['label']} · mode={state.synth_mode}[/#6fbf73]"
        )
        return True

    if head == "off":
        state.synth_active = False
        console.print("  [#bfa669]synth off[/#bfa669]")
        return True

    if head == "mode":
        if len(tail) < 2:
            console.print(f"  [red]usage: /dual synth mode {'|'.join(SYNTH_MODES)}[/red]")
            return True
        mode = tail[1].lower()
        if mode not in SYNTH_MODES:
            console.print(
                f"  [red]unknown mode '{mode}'. Pick from: {', '.join(SYNTH_MODES)}[/red]"
            )
            return True
        state.synth_mode = mode
        console.print(f"  [#6fbf73]synth mode = {mode}[/#6fbf73]")
        return True

    if head == "model":
        if len(tail) < 2:
            console.print("  [red]usage: /dual synth model <alias>[/red]")
            return True
        try:
            state.synth_cfg = _resolve_single(tail[1])
        except ValueError as exc:
            console.print(f"  [red]{exc}[/red]")
            return True
        console.print(f"  [#6fbf73]synth model = {state.synth_cfg['label']}[/#6fbf73]")
        return True

    console.print(f"  [red]unknown synth subcommand: {head}[/red]")
    return True


def _resolve_single(alias: str) -> dict:
    """Resolve one alias to a config dict, raising a friendly error on miss."""
    from config.agent_config import resolve_model_config

    cfg = resolve_model_config(alias)
    if cfg is None:
        raise ValueError(f"Unknown model alias: {alias}")
    return cfg


def _resolve_pair(left_alias: str, right_alias: str) -> tuple[dict, dict]:
    """Resolve two model aliases to config dicts, raising on miss or collision."""
    left = _resolve_single(left_alias)
    right = _resolve_single(right_alias)
    if left is right:
        raise ValueError(
            f"Both aliases resolved to {left['label']}. Pick two different models."
        )
    return left, right


def _resolve_right_with_fallback(left_cfg: dict, console) -> dict:
    """Walk RIGHT_FALLBACK_CHAIN, returning the first available model.

    For each candidate alias, resolve it, probe availability (only for
    Ollama :cloud tags), and return the first one that either doesn't
    need probing or passes the probe. If a candidate is paywalled or
    unreachable, print a one-line notice and try the next.
    """
    from config.agent_config import resolve_model_config

    last_reason: str | None = None
    for alias in RIGHT_FALLBACK_CHAIN:
        cfg = resolve_model_config(alias)
        if cfg is None:
            continue
        if cfg is left_cfg:
            continue

        ok, reason = _preflight_model(cfg)
        if ok:
            return cfg
        last_reason = reason
        console.print(
            f"  [dim #bfa669]{cfg['label']} unavailable: {reason}. Trying fallback.[/dim #bfa669]"
        )

    raise ValueError(
        f"No right-pane model available in fallback chain {RIGHT_FALLBACK_CHAIN!r}"
        + (f" (last reason: {last_reason})" if last_reason else "")
    )


def _preflight_model(cfg: dict) -> tuple[bool, str | None]:
    """Lightweight availability probe. Only probes Ollama :cloud tags today.

    Other providers return (True, None) without hitting the network: their
    auth failures are caught at provider construction time by the existing
    ``_get_*_provider`` helpers, and their free-tier endpoints don't have
    the specific "looks OK but is actually paywalled" trap that Ollama
    Cloud has.
    """
    if cfg.get("provider") != "ollama":
        return True, None
    backend = cfg.get("backend_name") or cfg.get("name", "")
    if ":cloud" not in backend:
        return True, None

    from providers.ollama import check_cloud_model_availability

    result = check_cloud_model_availability(backend)
    if result.ok:
        return True, None
    if result.paywalled:
        return False, "requires an Ollama Cloud subscription"
    return False, result.reason or "unknown error"


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

    # Post-stream synthesis, if enabled. Runs as a single fresh turn on the
    # synthesizer model using a stateless provider (no history) so the turn
    # is cheap and reproducible. Tool calls are disabled for the synth turn
    # because the synthesizer is fusing text, not acting on the world.
    if state.synth_active and state.synth_cfg is not None:
        _run_synthesis_turn(
            user_input=user_input,
            state=state,
            transcripts=transcripts,
            console=console,
            session_state=session_state,
            get_provider=get_provider,
        )


def _run_synthesis_turn(
    *,
    user_input: str,
    state: DualModeState,
    transcripts: dict,
    console,
    session_state: dict,
    get_provider: Callable[[dict, str], Any],
) -> None:
    """Run a single synthesizer turn and stream it to scrollback."""
    # Skip synth if neither pane produced usable content (both errored).
    content_by_pane = {
        pid: t for pid, t in transcripts.items() if t.get("content", "").strip()
    }
    if len(content_by_pane) < 2:
        console.print(
            "  [dim #bfa669]synth skipped: need both panes to finish with content[/dim #bfa669]"
        )
        return

    left_pane = transcripts.get("left", {})
    right_pane = transcripts.get("right", {})
    left_label = left_pane.get("model_label", "A")
    right_label = right_pane.get("model_label", "B")

    synth_prompt = SYNTH_PROMPTS.get(state.synth_mode, SYNTH_PROMPTS[DEFAULT_SYNTH_MODE])

    # Frame the two answers as structured input. The synthesizer model
    # sees this as a single user message so we don't have to add the
    # peer answers to its history (stateless turn).
    synth_input = (
        f"User's original question:\n{user_input}\n\n"
        f"--- Answer from {left_label} ---\n{left_pane.get('content', '').strip()}\n\n"
        f"--- Answer from {right_label} ---\n{right_pane.get('content', '').strip()}\n\n"
        f"Produce the final synthesis now."
    )

    from cli.renderer import StreamRenderer
    from cli.branding import GOLD_HEX, GOLD_DIM_HEX

    console.print()
    console.print(
        f"  [bold {GOLD_HEX}]CroweLM Synthesis[/bold {GOLD_HEX}]"
        f"  [dim]· {state.synth_cfg['label']} · {state.synth_mode}[/dim]"
    )

    renderer = StreamRenderer(console=console, model_label=state.synth_cfg["label"])

    try:
        provider = get_provider(state.synth_cfg, synth_prompt)
        # Do not bind peer-message history. Stateless single turn.
        provider.add_user_message(synth_input)
        renderer.start()
        provider.stream_response(
            console=console,
            render_tool_card=_noop_tool_card,
            session_state=session_state,
            _get_orchestrator=None,
            renderer=renderer,
        )
        renderer.finish(session_state=session_state)
    except Exception as exc:
        console.print(f"  [red]synth failed: {type(exc).__name__}: {exc}[/red]")
        return

    # StreamRenderer accumulates every streamed token in _full_text_chunks.
    # That internal is the only way to recover the synth text for history.
    synth_text = "".join(getattr(renderer, "_full_text_chunks", []))
    if synth_text.strip():
        session_state["last_synth_text"] = synth_text
        # Append the synth to the dual-mode "last answer" so /transcript and
        # any downstream consumers see all three outputs.
        session_state["last_answer_text"] = (
            session_state.get("last_answer_text", "")
            + f"\n\n---\n\n**CroweLM Synthesis ({state.synth_mode})**\n\n{synth_text}"
        )


def _noop_tool_card(*args, **kwargs) -> None:
    """Tool-card renderer for dual mode (intentionally silent).

    Tool invocations still show up as spinner labels inside each pane
    via the QueueRenderer's ``set_spinner`` event, so users can see
    when a model is running a tool. The separate tool result card
    would clobber the dual layout, so we drop it on the floor.
    """
    return None
