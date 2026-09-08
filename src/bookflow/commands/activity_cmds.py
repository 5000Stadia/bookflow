"""Projected activity contract and immutable annotation candidate selection."""
from bookflow.core.context import Context
from bookflow.core.errors import BookflowError
from bookflow.core.lazy import LazyModule
from bookflow.core.registry import Plan, command
from bookflow.core.history_inputs import ActivityInput as PublicActivityInput
from bookflow.core.history_models import HistoryActivity

sa = LazyModule("sqlalchemy")
c = LazyModule("bookflow.company.schema")
PAGE_BYTES = 262144


def _candidates(inp, key):
    """UNION deduplicates direct annotations; immutable target columns select history."""
    entries = c.audit_entries
    branches = []
    kinds = set(inp.kinds) if inp.kinds is not None else {"audit", "note", "attachment"}
    direct_kind = "note" if inp.record_type == "note" else "attachment" if inp.record_type in {"attachment", "attachment_link"} else "audit"
    if direct_kind in kinds:
        branches.append(sa.select(entries.c.id).where(entries.c.record_type == inp.record_type, entries.c.record_id == key))
    for kind, record_type, table in (("note", "note", c.notes), ("attachment", "attachment_link", c.attachment_links)):
        if kind in kinds:
            branches.append(sa.select(entries.c.id).select_from(table.join(entries,
                sa.and_(entries.c.record_type == record_type, entries.c.record_id == table.c.id))).where(
                    table.c.record_type == inp.record_type, table.c.record_id == key))
    if not branches:
        return sa.select(entries.c.id).where(sa.false())
    return sa.union(*branches) if len(branches) > 1 else branches[0]


activity = command("activity", scope="company", required_role="member", capability="activity",
    description="Page immutable record, note and file-link actions chronologically, with a fixed audit cutoff.",
    input_model=PublicActivityInput, output_model=HistoryActivity, positional=["record_type", "record_id"],
    error_codes=["E_RECORD_NOT_FOUND", "E_VALIDATION"])


@activity
def plan_activity(inp: PublicActivityInput, ctx: Context, s) -> Plan:
    raise BookflowError("E_INTERNAL", message="History requires authenticated reader execution")
