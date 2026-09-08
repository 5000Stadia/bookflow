"""Registered projected audit contracts; execution requires an authenticated reader."""
from bookflow.core.context import Context
from bookflow.core.errors import BookflowError
from bookflow.core.registry import command, Plan
from bookflow.core.session import Session
from bookflow.core.history_inputs import AuditListInput, AuditTailInput, EventSelector
from bookflow.core.history_models import HistoryList as AuditListOutput, HistoryTail as AuditTailOutput, HistoryShow


for hub, prefix in ((True, "hub audit"), (False, "audit")):
    scope = "hub" if hub else "company"
    role = None if hub else "member"

    def _mk(hub=hub, prefix=prefix, scope=scope, role=role):
        @command(f"{prefix} list", scope=scope, description=("List hub audit events the acting user may see, newest first." if hub else "List this company's audit events, newest first."),
                 input_model=AuditListInput, output_model=AuditListOutput, required_role=role, error_codes=["E_VALIDATION"])
        def plan_list(inp: AuditListInput, ctx: Context, s: Session) -> Plan:
            raise BookflowError("E_INTERNAL", message="History requires authenticated reader execution")

        @command(f"{prefix} show", scope=scope, description=("Show one hub audit event with its entries and field diffs." if hub else "Show one of this company's audit events with its entries and field diffs."),
                 input_model=EventSelector, output_model=HistoryShow, required_role=role, positional=["event"], error_codes=["E_EVENT_NOT_FOUND"])
        def plan_show(inp: EventSelector, ctx: Context, s: Session) -> Plan:
            raise BookflowError("E_INTERNAL", message="History requires authenticated reader execution")

        @command(f"{prefix} tail", scope=scope, description=("Hub audit events newer than a cursor, oldest first; the event feed." if hub else "This company's audit events newer than a cursor, oldest first; the event feed."),
                 input_model=AuditTailInput, output_model=AuditTailOutput, required_role=role, streams=True, error_codes=["E_VALIDATION"])
        def plan_tail(inp: AuditTailInput, ctx: Context, s: Session) -> Plan:
            raise BookflowError("E_INTERNAL", message="History requires authenticated reader execution")

    _mk()
