"""cli/ops_cli.py

Click subgroup for the operations + research-lab service.

Wired into the main `crowe-logic` CLI by the registration call at the
bottom of cli/crowe_logic.py:

    from cli.ops_cli import register
    register(main)

Commands:
    crowe-logic ops register-strain <name> <species>
    crowe-logic ops create-batch <code> <strain_id> <location>
    crowe-logic ops advance <batch_id> <stage>
    crowe-logic ops haccp <batch_id> <ccp> <target> <actual> <result>
    crowe-logic ops harvest <batch_id> <lot_code> <weight_kg>
    crowe-logic ops ship <lot_id> <recipient> <destination>
    crowe-logic ops recall <batch_id>
    crowe-logic ops list-batches

Backed by the same ``OpsService`` that the HTTP router uses. AICL messages
print to stderr by default so the JSON results on stdout stay machine-pipeable
(e.g. ``crowe-logic ops list-batches | jq``).

Persistence: the CLI defaults to a process-local in-memory store, so each
invocation starts empty. This is intentional for demos and quick iteration;
to persist across invocations set ``CROWE_OPS_STORE=postgres`` once the
adapter ships.
"""

from __future__ import annotations

import json
import sys

import click

from crowe_synapse_engine.aicl import AICLMessage
from crowe_synapse_engine.ops import (
    BatchStage,
    ContaminationType,
    HACCPResult,
    InMemoryStore,
    OpsService,
    Severity,
)


# ── helpers ──────────────────────────────────────────────────────────────


def _make_service() -> OpsService:
    """Construct a fresh service backed by InMemoryStore + stderr AICL sink.

    Each CLI invocation gets a fresh store; this keeps the surface honest
    until a persistent backend lands. Down the road this seam is where the
    Postgres adapter and shared MemoryStore session log get wired in.
    """
    store = InMemoryStore()

    def _sink(msg: AICLMessage) -> None:
        click.echo(f"  [aicl] {msg.subject}", err=True)

    return OpsService(store, aicl_sink=_sink)


def _emit(payload) -> None:
    """Print a model or list as pretty JSON to stdout."""
    if hasattr(payload, "model_dump"):
        click.echo(payload.model_dump_json(indent=2))
    elif isinstance(payload, list):
        normalized = [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item
            for item in payload
        ]
        click.echo(json.dumps(normalized, indent=2, default=str))
    else:
        click.echo(json.dumps(payload, indent=2, default=str))


# ── group ────────────────────────────────────────────────────────────────


@click.group()
def ops():
    """Cultivation operations + research-lab commands."""


@ops.command("register-strain")
@click.argument("name")
@click.argument("species")
@click.option("--origin", default=None)
@click.option("--notes", default=None)
def cmd_register_strain(name: str, species: str, origin: str | None, notes: str | None):
    """Register a new strain in the strain library."""
    service = _make_service()
    strain, _ = service.register_strain(
        name=name, species=species, origin=origin, notes=notes
    )
    _emit(strain)


@ops.command("create-batch")
@click.argument("code")
@click.argument("strain_id")
@click.argument("location")
@click.option("--operator", "operator_id", default=None)
@click.option("--recipe", "substrate_recipe", default=None)
def cmd_create_batch(
    code: str,
    strain_id: str,
    location: str,
    operator_id: str | None,
    substrate_recipe: str | None,
):
    """Inoculate a new batch.

    Note: because the CLI uses an in-memory store, the strain_id must be
    registered in the same invocation, OR you must run this against a
    persistent backend. For interactive flows prefer the HTTP API.
    """
    from uuid import UUID

    service = _make_service()
    try:
        batch, _ = service.create_batch(
            code=code,
            strain_id=UUID(strain_id),
            location=location,
            operator_id=operator_id,
            substrate_recipe=substrate_recipe,
        )
    except ValueError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)
    _emit(batch)


@ops.command("advance")
@click.argument("batch_id")
@click.argument("stage", type=click.Choice([s.value for s in BatchStage]))
@click.option("--actor", "actor_id", default=None)
@click.option("--notes", default=None)
def cmd_advance(batch_id: str, stage: str, actor_id: str | None, notes: str | None):
    """Advance a batch to a new stage."""
    from uuid import UUID

    service = _make_service()
    try:
        batch, _ = service.advance_stage(
            UUID(batch_id), BatchStage(stage), actor_id=actor_id, notes=notes
        )
    except (LookupError, ValueError) as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)
    _emit(batch)


@ops.command("haccp")
@click.argument("batch_id")
@click.argument("ccp_name")
@click.argument("target")
@click.argument("actual")
@click.argument("result", type=click.Choice([r.value for r in HACCPResult]))
@click.option("--operator", "operator_id", required=True)
@click.option("--corrective", "corrective_action", default=None)
def cmd_haccp(
    batch_id: str,
    ccp_name: str,
    target: str,
    actual: str,
    result: str,
    operator_id: str,
    corrective_action: str | None,
):
    """Record a HACCP critical-control-point check."""
    from uuid import UUID

    service = _make_service()
    try:
        check, _ = service.record_haccp_check(
            batch_id=UUID(batch_id),
            ccp_name=ccp_name,
            target=target,
            actual=actual,
            result=HACCPResult(result),
            operator_id=operator_id,
            corrective_action=corrective_action,
        )
    except (LookupError, ValueError) as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)
    _emit(check)


@ops.command("contamination")
@click.argument("batch_id")
@click.argument(
    "contamination_type", type=click.Choice([c.value for c in ContaminationType])
)
@click.argument("severity", type=click.Choice([s.value for s in Severity]))
@click.option("--operator", "operator_id", default=None)
@click.option("--action", "contained_action", default=None)
def cmd_contamination(
    batch_id: str,
    contamination_type: str,
    severity: str,
    operator_id: str | None,
    contained_action: str | None,
):
    """Record a contamination event on a batch."""
    from uuid import UUID

    service = _make_service()
    try:
        event, _ = service.record_contamination(
            batch_id=UUID(batch_id),
            contamination_type=ContaminationType(contamination_type),
            severity=Severity(severity),
            operator_id=operator_id,
            contained_action=contained_action,
        )
    except LookupError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)
    _emit(event)


@ops.command("harvest")
@click.argument("batch_id")
@click.argument("lot_code")
@click.argument("weight_kg", type=float)
@click.option("--grade", default="A")
@click.option("--actor", "actor_id", default=None)
def cmd_harvest(
    batch_id: str, lot_code: str, weight_kg: float, grade: str, actor_id: str | None
):
    """Harvest a batch into a new lot."""
    from uuid import UUID

    service = _make_service()
    try:
        lot, _ = service.harvest_to_lot(
            batch_id=UUID(batch_id),
            code=lot_code,
            weight_kg=weight_kg,
            grade=grade,
            actor_id=actor_id,
        )
    except (LookupError, ValueError) as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)
    _emit(lot)


@ops.command("ship")
@click.argument("lot_id")
@click.argument("recipient")
@click.argument("destination_location")
@click.option("--actor", "actor_id", default=None)
def cmd_ship(
    lot_id: str, recipient: str, destination_location: str, actor_id: str | None
):
    """Record a shipment of a lot to a customer."""
    from uuid import UUID

    service = _make_service()
    try:
        event, _ = service.ship_lot(
            lot_id=UUID(lot_id),
            recipient=recipient,
            destination_location=destination_location,
            actor_id=actor_id,
        )
    except LookupError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)
    _emit(event)


@ops.command("recall")
@click.argument("batch_id")
def cmd_recall(batch_id: str):
    """Run an FSMA 204 forward-recall trace for a batch."""
    from uuid import UUID

    service = _make_service()
    try:
        trace = service.recall_trace_forward(UUID(batch_id))
    except LookupError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)
    click.echo(
        json.dumps(
            {
                "batch": trace.batch.model_dump(mode="json"),
                "lots": [lot.model_dump(mode="json") for lot in trace.lots],
                "tracking_events": [
                    e.model_dump(mode="json") for e in trace.tracking_events
                ],
                "contamination_events": [
                    e.model_dump(mode="json") for e in trace.contamination_events
                ],
                "haccp_checks": [c.model_dump(mode="json") for c in trace.haccp_checks],
                "aicl_messages": [m.to_dict() for m in trace.aicl_messages],
            },
            indent=2,
            default=str,
        )
    )


@ops.command("list-batches")
@click.option("--archived", is_flag=True, default=False)
def cmd_list_batches(archived: bool):
    """List batches in the configured store."""
    service = _make_service()
    batches = service._store.list_batches(archived=archived)  # noqa: SLF001
    _emit(batches)


@ops.command("allowed-next")
@click.argument("stage", type=click.Choice([s.value for s in BatchStage]))
def cmd_allowed_next(stage: str):
    """Show which stage transitions are allowed from the given stage."""
    from crowe_synapse_engine.ops import allowed_next

    targets = sorted(s.value for s in allowed_next(BatchStage(stage)))
    _emit(targets)


# ── registration ─────────────────────────────────────────────────────────


def register(main_group) -> None:
    """Attach the `ops` subgroup to the main crowe-logic CLI group."""
    main_group.add_command(ops)
