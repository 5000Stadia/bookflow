"""Bounded collection of unlinked attachment bodies."""
from pydantic import BaseModel, ConfigDict, Field

from bookflow.core.models import WriteOutput
from bookflow.core.registry import Applied, Plan, command


class CompactInput(BaseModel):
    model_config = ConfigDict(extra="forbid", defer_build=True)
    limit: int = Field(200, strict=True, ge=1, le=200, description="Maximum bodies collected in this invocation.")


class CompactOutput(WriteOutput):
    operation_id: str | None = None
    collected_count: int
    bytes_collected: int
    has_more: bool
    idempotent_replay: bool = False


company_compact = command("company compact", scope="company", description="Collect at most 200 unlinked attachment bodies and continue bounded orphan discovery.",
    input_model=CompactInput, output_model=CompactOutput, writes={"company"}, required_role="admin",
    capability="attachment", accepts_idempotency_key=True, error_codes=["E_IO", "E_DB_BUSY", "E_VALIDATION"])


@company_compact
def plan_company_compact(inp, ctx, s):
    from bookflow.company import attachment_gc as gc
    from bookflow.core import idempotency
    from bookflow.core.errors import BookflowError
    if gc.pending(s):
        raise BookflowError("E_DB_BUSY", "Attachment collection requires recovery.")
    # A real run chooses candidates only after acquiring filesystem exclusion.
    rows, more = gc.metadata_candidates(s, inp.limit) if s.dry_run else ([], False)
    items = gc.metadata_items(s, rows) if s.dry_run else []
    return Plan(CompactOutput(collected_count=sum(item["initial_present"] for item in items),
                              bytes_collected=sum(item["size_bytes"] for item in items),
                              has_more=more or s.dry_run, dry_run=s.dry_run,
                              warnings=["Projection includes metadata candidates; orphan discovery requires filesystem exclusion."] if s.dry_run else []),
                {"limit": inp.limit, "input_hash": idempotency.input_hash(inp.model_dump(mode="json"), s.company_id)})


@company_compact.applier
def apply_company_compact(plan, ctx, s):
    from bookflow.company.attachment_gc import collect
    output = collect(s, ctx, plan.data["limit"], plan.data["input_hash"])
    return Applied(CompactOutput(**output), [], "attachment collection completed", finalized=True, audited=True)
