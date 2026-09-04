"""Routed compensating undo for eligible Row 5 list events."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from bookflow.company import undo
from bookflow.core.context import Context
from bookflow.core.models import WriteOutput
from bookflow.core.registry import Applied, Plan, command
from bookflow.core.session import Session, now_iso


class UndoInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    event_id: str = Field(description="Company audit event id to compensate")


class UndoAffectedRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    record_type: str
    record_id: str
    action: str
    version_before: int
    version_after: int
    restored_fields: list[str]


class UndoOutput(WriteOutput):
    original_event_id: str
    undo_event_id: str
    affected_records: list[UndoAffectedRecord]
    versions: dict[str, int]


def _output(plan: undo.UndoPlan, results) -> UndoOutput:
    affected = [
        UndoAffectedRecord(
            record_type=result.touched.record_type,
            record_id=result.touched.record_id,
            action=result.action,
            version_before=int(result.touched.version_before),
            version_after=int(result.touched.version_after),
            restored_fields=list(result.restored_fields),
        )
        for result in results
    ]
    return UndoOutput(
        original_event_id=plan.request.event["id"],
        undo_event_id=plan.event_id,
        affected_records=affected,
        versions={
            f"{item.record_type}:{item.record_id}": item.version_after
            for item in affected
        },
    )


undo_command = command(
    "undo",
    scope="company",
    description="Compensate an eligible Row 5 list audit event without deleting history.",
    input_model=UndoInput,
    output_model=UndoOutput,
    writes={"company"},
    required_role="admin",
    positional=["event_id"],
    error_codes=[
        "E_EVENT_NOT_FOUND",
        "E_NOT_UNDOABLE",
        "E_ALREADY_UNDONE",
        "E_UNDO_CONFLICT",
    ],
    accepts_idempotency_key=True,
    capability="undo",
    authorization="admin and the original event's current role threshold",
)


@undo_command
def plan_undo(inp: UndoInput, ctx: Context, s: Session) -> Plan:
    planned = undo.plan_undo(
        s,
        inp.event_id,
        actor_id=s.actor.id,
        via=ctx.interface.value,
        at=now_iso(),
    )
    return Plan(preview=_output(planned, planned.results), data={"undo": planned})


@undo_command.applier
def apply_undo(plan: Plan, ctx: Context, s: Session) -> Applied:
    planned = plan.data["undo"]
    undo.assert_not_compensated(s.company.conn, planned.request.event["id"])
    results = undo.apply_inverse(
        s.company.conn,
        planned.request,
        actor_id=s.actor.id,
        via=ctx.interface.value,
        at=planned.at,
    )
    undo.write_undo_event(s, ctx, planned, results)
    return Applied(
        output=_output(planned, results),
        touched=[result.touched for result in results],
        summary=f"undid event {planned.request.event['id']}",
        audited=True,
    )


UNDO_COMMAND = undo_command.cmd

__all__ = ["UNDO_COMMAND", "UndoAffectedRecord", "UndoInput", "UndoOutput"]
