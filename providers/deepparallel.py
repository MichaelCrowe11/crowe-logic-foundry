"""DeepParallel provider — orchestrates the cluster-mode pipeline.

From the foundry CLI's perspective this is "just another model tier."
The user picks ``CroweLM DeepParallel`` from ``/model``, types a prompt,
and sees a streamed answer like any other tier. Under the hood, the
prompt fans out into 8 persona-driven clusters (decomposer → specialists
→ aggregator) anchored across multiple sovereign Foundry deployments,
then synthesizes through a confidence-weighted judge. The brand-mask
layer (see ``crowe_deepparallel.parallel.branding``) keeps every
upstream vendor / model identifier off the customer-facing surface.

Latency: cluster mode typically takes 150-250s wall-clock — substantially
slower than a single-model tier — but produces materially more rigorous
output (decomposer scoping + multiple specialist perspectives + judge
synthesis with explicit confidence weighting). The provider keeps the
spinner alive with branded progress updates ("integrating perspectives...",
"weighing evidence...") so the wait feels purposeful.

Conversation history is captured in ``self.messages`` for transcript
persistence, but cluster mode is single-turn — each invocation reads the
last user message and runs the full pipeline fresh. Multi-turn cluster
mode (history-aware decomposition) is a Phase B concern.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any


class DeepParallelProvider:
    """Bridges the foundry CLI's provider interface to cluster-mode orchestration."""

    # The renderer expects feed() calls to look like streamed tokens. Cluster
    # mode produces a complete answer after the wall time, so we re-stream it
    # word-by-word for parity with the other tiers. Pacing is fast (~25ms/word
    # by default) so the wait between cluster completion and full render is
    # bounded and the typewriter effect doesn't add meaningful latency.
    _STREAM_PACE_S = 0.025

    def __init__(
        self,
        *,
        preset: str,
        system_instructions: str,
        label: str,
        judge_backend: str | None = None,
        grounding_enabled: bool = True,
        timeout_s: float = 600.0,
    ) -> None:
        self.preset = preset
        self.system_instructions = system_instructions
        self.label = label
        self.judge_backend = judge_backend
        self.grounding_enabled = grounding_enabled
        self.timeout_s = timeout_s

        # Conversation transcript. Cluster mode is single-turn so only the
        # latest user message drives the pipeline, but we keep the full
        # history for transcript persistence + future multi-turn support.
        self.messages: list[dict[str, Any]] = []

        # Lazy import the cluster entry point; defers the optional dependency
        # so the provider module can be imported even when crowe_deepparallel
        # isn't installed (the runtime factory will raise a clean error then).
        self._cluster_query = None

    # ------------------------------------------------------------------
    # Foundry provider interface (matches AnthropicProvider, NvidiaProvider)

    def add_user_message(self, content: str) -> None:
        """Append a user turn to the conversation transcript."""
        self.messages.append({"role": "user", "content": content})

    def stream_response(
        self,
        console,
        render_tool_card,  # noqa: ARG002 - DeepParallel doesn't surface tool calls per stage
        session_state,
        _get_orchestrator,
        renderer=None,
        tools_enabled=True,  # noqa: ARG002 - cluster mode never surfaces tools
    ) -> str:
        """Run cluster mode for the latest user message, stream the answer back."""
        if not self.messages:
            raise RuntimeError(
                "DeepParallelProvider.stream_response called with no user message"
            )
        prompt = self.messages[-1]["content"]

        # Honor session-level system instructions by prepending them to the
        # user prompt. Cluster mode's per-stage system prompts handle their
        # own brand discipline, but user-set steering (e.g. "always respond
        # in markdown", "be concise") still flows through this prefix.
        if self.system_instructions:
            effective_prompt = (
                f"Session context:\n{self.system_instructions}\n\nQuestion:\n{prompt}"
            )
        else:
            effective_prompt = prompt

        if renderer is None:
            from cli.renderer import StreamRenderer

            favicon = (
                session_state.get("favicon", "")
                if isinstance(session_state, dict)
                else ""
            )
            renderer = StreamRenderer(console, self.label, favicon=favicon)

        renderer.start()
        renderer.set_spinner(
            "CroweLM DeepParallel — analyzing across persona clusters..."
        )

        # Resolve the cluster entry point. This import lives inside
        # stream_response so the provider module load doesn't fail when
        # crowe_deepparallel isn't installed; the error path is clean.
        if self._cluster_query is None:
            try:
                from crowe_deepparallel import multimodel_cluster_query
            except ImportError as exc:
                renderer.stop_spinner()
                msg = (
                    f"CroweLM DeepParallel tier is not installed. "
                    f"Install crowe-deepparallel into this venv: "
                    f"`pip install -e ~/Projects/crowe-logic-foundry-deepparallel-impl`. "
                    f"({type(exc).__name__}: {exc})"
                )
                renderer.feed(msg)
                renderer.end_segment()
                renderer.finish(session_state=session_state)
                return msg
            self._cluster_query = multimodel_cluster_query

        # Background heartbeat: rotate spinner messages so the user sees
        # something happening through the multi-minute cluster run.
        heartbeat_stop = {"stop": False}

        async def _run_cluster_with_heartbeat():
            cluster_task = asyncio.create_task(
                self._cluster_query(
                    prompt=effective_prompt,
                    preset=self.preset,
                    budget_usd=5.00,  # cluster cost ceiling; sized for ~10x typical
                    timeout_s=self.timeout_s,
                    grounding_enabled=self.grounding_enabled,
                    judge_backend=self.judge_backend,
                )
            )
            heartbeat_task = asyncio.create_task(
                self._heartbeat(renderer, heartbeat_stop)
            )
            try:
                result = await cluster_task
            finally:
                heartbeat_stop["stop"] = True
                heartbeat_task.cancel()
                # Drain the cancellation cleanly
                try:
                    await heartbeat_task
                except (asyncio.CancelledError, BaseException):
                    pass
            return result

        try:
            result = asyncio.run(_run_cluster_with_heartbeat())
        except Exception as exc:
            # Don't embed errors as if they were the assistant's answer —
            # doing so pollutes the conversation transcript (the bad text
            # gets appended to self.messages on the next turn) and the
            # foundry CLI's tier-fallback chain can't engage because the
            # provider "succeeded" with a stringly-typed failure.
            #
            # Instead: log the failure with a correlation ID for support
            # lookup, record it on session_state for telemetry, stop the
            # renderer cleanly, and raise so the CLI dispatch layer can
            # fall back to the next tier in MODEL_CHAIN.
            import logging
            import uuid

            error_id = uuid.uuid4().hex[:12]
            logging.getLogger(__name__).error(
                "deepparallel.cluster_failure error_id=%s exc=%s: %s",
                error_id,
                type(exc).__name__,
                exc,
            )
            if isinstance(session_state, dict):
                session_state["deepparallel_last_error"] = {
                    "error_id": error_id,
                    "type": type(exc).__name__,
                    "message": str(exc)[:500],
                }
            renderer.stop_spinner()
            renderer.abort(session_state=session_state) if hasattr(
                renderer, "abort"
            ) else renderer.finish(session_state=session_state)
            # Re-raise so the foundry CLI's existing exception-handling
            # path (tier fallback, error display, retry-with-different-model)
            # can engage. The error_id is in the log for support correlation.
            raise RuntimeError(
                f"CroweLM DeepParallel cluster execution failed "
                f"(error_id={error_id}): {type(exc).__name__}: {exc}"
            ) from exc

        renderer.stop_spinner()
        renderer.begin_stream()

        text = (
            result.synthesized_answer
            or "(CroweLM DeepParallel returned no synthesized answer.)"
        )

        # Surface synthesis-fallback signal: when the judge call failed and
        # the orchestrator fell back to cluster concatenation (or single-
        # cluster passthrough), the customer is seeing a degraded answer.
        # Append a one-line footer so they know — and log + record in
        # session_state for operator-side observability.
        fallback_active = bool(result.synthesis_metadata.get("fallback"))
        synthesis_mode = result.synthesis_metadata.get("synthesis")
        if fallback_active or synthesis_mode == "single_cluster_passthrough":
            import logging

            reason = result.synthesis_metadata.get("fallback_reason") or synthesis_mode
            logging.getLogger(__name__).warning(
                "deepparallel.synthesis_fallback ledger_id=%s reason=%s",
                result.ledger_id,
                reason,
            )
            text = text + (
                "\n\n---\n*CroweLM DeepParallel synthesis backstop engaged; "
                "final answer reflects degraded synthesis path.*"
            )

        for chunk in self._chunk_for_stream(text):
            renderer.feed(chunk)
            if self._STREAM_PACE_S > 0:
                time.sleep(self._STREAM_PACE_S)

        self.messages.append({"role": "assistant", "content": text})

        # Record audit-tier metadata on session_state for telemetry consumers.
        # The brand-mask render keeps customer-visible fields branded; the
        # _internal_* keys are present for cost reconciliation only.
        if isinstance(session_state, dict):
            session_state.setdefault("deepparallel_runs", []).append(
                {
                    "preset": self.preset,
                    "total_cost_usd": result.total_cost_usd,
                    "total_latency_ms": result.total_latency_ms,
                    "surviving_clusters": result.synthesis_metadata.get(
                        "surviving_clusters"
                    ),
                    "cluster_count": result.synthesis_metadata.get("cluster_count"),
                    "dropped_personas": list(result.dropped_cluster_personas),
                    "judge_model": result.synthesis_metadata.get("judge_model"),
                    "ledger_id": result.ledger_id,
                    "synthesis_fallback": fallback_active,
                }
            )

        renderer.end_segment()
        renderer.finish(session_state=session_state)
        return text

    # ------------------------------------------------------------------
    # Streaming helpers

    @staticmethod
    def _chunk_for_stream(text: str) -> list[str]:
        """Split ``text`` into word-with-trailing-whitespace chunks.

        Matches the chunk shape a real LLM stream emits — preserves spacing,
        no chunk straddles a word boundary mid-character. The renderer feeds
        each chunk through its accumulator without extra logic on our side.
        """
        return re.findall(r"\S+\s*", text) or [text]

    @staticmethod
    async def _heartbeat(renderer, stop_flag: dict) -> None:
        """Rotate spinner copy during a long cluster run.

        The messages are deliberately persona-neutral and brand-disciplined:
        no mention of how many models, which vendors, or what's happening
        inside each cluster. They explain *what the system is doing* in
        terms a customer would understand without leaking architecture.
        """
        messages = [
            "CroweLM DeepParallel — analyzing across persona clusters...",
            "CroweLM DeepParallel — gathering specialist perspectives...",
            "CroweLM DeepParallel — weighing evidence across reasoning chains...",
            "CroweLM DeepParallel — integrating high-confidence findings...",
            "CroweLM DeepParallel — resolving disagreements...",
            "CroweLM DeepParallel — synthesizing final response...",
        ]
        idx = 0
        try:
            while not stop_flag["stop"]:
                await asyncio.sleep(8.0)
                if stop_flag["stop"]:
                    return
                idx = (idx + 1) % len(messages)
                try:
                    renderer.set_spinner(messages[idx])
                except Exception:  # noqa: BLE001 - renderer may have moved on
                    return
        except asyncio.CancelledError:
            return
