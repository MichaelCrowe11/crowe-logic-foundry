"""Batch stage transition rules.

This module is the canonical place to encode cultivation domain knowledge:
which stage moves are valid, what conditions gate them, which are terminal.
The rules below match a typical mushroom-cultivation workflow (Lion's Mane,
oyster, reishi) where a substrate block moves linearly from inoculation to
fruiting to harvest, with failure or discard as off-ramps at any stage.

The rules are encoded as a directed graph: ``TRANSITIONS[current] = {
allowed_next_stages}``. If your operation has additional stages (e.g.
secondary fruiting flush, spent-substrate composting), extend the graph
here rather than scattering ``if stage ==`` checks across service code.

Operator note: review these rules against your SOPs. A future revision
should pull these from a per-facility config so multi-tenant deployments
can express their own workflow.
"""

from __future__ import annotations

from crowe_synapse_engine.ops.models import BatchStage

# ── Transition graph ────────────────────────────────────────────────────

# Forward progression through a healthy cultivation cycle.
_FORWARD = {
    BatchStage.INOCULATION: {BatchStage.COLONIZATION},
    BatchStage.COLONIZATION: {BatchStage.FRUITING},
    BatchStage.FRUITING: {BatchStage.HARVESTED},
}

# Off-ramps available from any non-terminal stage when something goes wrong.
_NEGATIVE_FROM_ACTIVE = {BatchStage.FAILED, BatchStage.DISCARDED}
_ACTIVE_STAGES = {
    BatchStage.INOCULATION,
    BatchStage.COLONIZATION,
    BatchStage.FRUITING,
}

# Terminal stages: nothing leaves them. Recall traversal still walks
# through them via lots, but the batch itself does not change stage.
TERMINAL_STAGES: frozenset[BatchStage] = frozenset(
    {BatchStage.HARVESTED, BatchStage.FAILED, BatchStage.DISCARDED}
)


def _build_transition_graph() -> dict[BatchStage, frozenset[BatchStage]]:
    graph: dict[BatchStage, set[BatchStage]] = {
        stage: set(_FORWARD.get(stage, set())) for stage in BatchStage
    }
    for stage in _ACTIVE_STAGES:
        graph[stage].update(_NEGATIVE_FROM_ACTIVE)
    return {stage: frozenset(targets) for stage, targets in graph.items()}


TRANSITIONS: dict[BatchStage, frozenset[BatchStage]] = _build_transition_graph()


class IllegalStageTransition(ValueError):
    """Raised when a caller asks for a transition the rules forbid."""

    def __init__(self, current: BatchStage, requested: BatchStage):
        self.current = current
        self.requested = requested
        super().__init__(
            f"Cannot move batch from {current.value!r} to {requested.value!r}. "
            f"Allowed targets from {current.value!r}: "
            f"{sorted(s.value for s in allowed_next(current))}"
        )


def allowed_next(current: BatchStage) -> frozenset[BatchStage]:
    """Return the set of stages the batch may move to from ``current``."""
    return TRANSITIONS.get(current, frozenset())


def is_terminal(stage: BatchStage) -> bool:
    """True when ``stage`` is a terminal state (no outgoing transitions)."""
    return stage in TERMINAL_STAGES


def validate_transition(current: BatchStage, requested: BatchStage) -> None:
    """Raise ``IllegalStageTransition`` if the move is not allowed.

    No-op transitions (``current == requested``) are rejected because they
    would create a confusing audit trail; the service layer treats them
    as user error rather than a silent success.
    """
    if current == requested:
        raise IllegalStageTransition(current, requested)
    if requested not in allowed_next(current):
        raise IllegalStageTransition(current, requested)
