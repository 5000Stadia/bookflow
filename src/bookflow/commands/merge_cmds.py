"""`customer merge`, `customer unmerge`, `vendor merge`, `vendor unmerge` (R117).

A merge records that one entry is merged into another and changes nothing posted; every read
resolves the merged entry to its survivor. People only, with a reason. `--dry-run` is the
preview: what moves and the survivor's balance after.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from bookflow.company import party_merges
from bookflow.core.registry import Applied, Plan, command


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MergeInput(_Strict):
    merged: str = Field(min_length=1, max_length=1000, description="The duplicate entry to merge away: ID or canonical full name.")
    into: str = Field(min_length=1, max_length=1000, description="The entry it is merged into, which survives: ID or canonical full name.")


class UnmergeInput(_Strict):
    merged: str = Field(min_length=1, max_length=1000, description="The merged-away entry whose merge is undone: ID or canonical full name.")


class MoneyOut(_Strict):
    amount: str
    currency: str
    minor_units: int


class DocumentCount(_Strict):
    type: str
    count: int


class MergeOutput(_Strict):
    merge_id: str | None = Field(description="The merge; null only in a dry run of a new merge.")
    changed: bool = Field(description="False when this exact merge already stands; nothing was written.")
    merged_id: str
    merged_name: str
    survivor_id: str
    survivor_name: str
    documents: list[DocumentCount] = Field(description="Documents naming the merged entry, by type; they now read as the survivor's.")
    document_count: int
    merged_balance: MoneyOut = Field(description="Open balance of the merged entry's own posted lines.")
    survivor_balance: MoneyOut = Field(description="Survivor's open balance before this merge.")
    survivor_balance_after: MoneyOut = Field(description="Survivor's open balance with the merged entry folded in.")


class UnmergeOutput(_Strict):
    merge_id: str
    changed: bool = Field(description="False when the merge was already undone; nothing was written.")
    merged_id: str
    survivor_id: str
    reactivated: bool = Field(description="Whether the merged entry was made active again.")


def _commands(kind: str):
    balance = "A/R" if kind == "customer" else "A/P"

    merge = command(f"{kind} merge", scope="company", required_role="standard", capability=kind,
        description=(f"Merge a duplicate {kind} into another. Nothing posted changes: every report, balance, aging "
                     f"and {balance} read shows the merged {kind}'s documents under the survivor, and the merged "
                     f"{kind} is made inactive and hidden. People only, with a reason; `--dry-run` previews what "
                     f"moves and the balance after. Undo with `{kind} unmerge`."),
        input_model=MergeInput, output_model=MergeOutput, writes={"company"}, positional=["merged", "into"],
        accepts_idempotency_key=True,
        error_codes=["E_RECORD_NOT_FOUND", "E_MERGE_REFUSED", "E_REASON_REQUIRED", "E_ACTIVE_DEPENDENTS",
                     "E_RECORD_IN_USE"])

    @merge
    def plan_merge(inp: MergeInput, ctx, s) -> Plan:
        party_merges.require_person(s, kind, "merge")
        planned = party_merges.plan_merge(s.company, kind, inp.merged, inp.into)
        return Plan(preview=MergeOutput(**party_merges.preview(s.company, planned)), data={"plan": planned})

    @merge.applier
    def apply_merge(plan: Plan, ctx, s) -> Applied:
        planned = plan.data["plan"]
        if not planned.changed:
            return Applied(plan.preview, [], "already merged")
        merge_id, touched = party_merges.apply_merge(s, ctx, planned, f"{kind} merge")
        out = plan.preview.model_copy(update={"merge_id": merge_id})
        return Applied(out, touched, f"merged {kind} {planned.merged['name']} into {planned.survivor['name']}",
                       audited=True)

    unmerge = command(f"{kind} unmerge", scope="company", required_role="standard", capability=kind,
        description=(f"Undo a {kind} merge: the merged {kind} reads as itself again and is made active again if "
                     f"the merge made it inactive. Nothing posted changes. People only, with a reason."),
        input_model=UnmergeInput, output_model=UnmergeOutput, writes={"company"}, positional=["merged"],
        accepts_idempotency_key=True,
        error_codes=["E_RECORD_NOT_FOUND", "E_MERGE_REFUSED", "E_REASON_REQUIRED", "E_INACTIVE_REFERENCE"])

    @unmerge
    def plan_unmerge(inp: UnmergeInput, ctx, s) -> Plan:
        party_merges.require_person(s, kind, "unmerge")
        planned = party_merges.plan_unmerge(s.company, kind, inp.merged)
        row = planned.merge or planned.last
        return Plan(preview=UnmergeOutput(merge_id=row["id"], changed=planned.merge is not None,
                                          merged_id=row["merged_id"], survivor_id=row["survivor_id"],
                                          reactivated=bool(planned.merge and planned.merge["deactivated_merged"])),
                    data={"plan": planned})

    @unmerge.applier
    def apply_unmerge(plan: Plan, ctx, s) -> Applied:
        planned = plan.data["plan"]
        if planned.merge is None:
            return Applied(plan.preview, [], "already unmerged")
        touched = party_merges.apply_unmerge(s, ctx, planned, f"{kind} unmerge")
        return Applied(plan.preview, touched, f"unmerged {kind} {planned.merged['name']}", audited=True)

    return merge, unmerge


customer_merge, customer_unmerge = _commands("customer")
vendor_merge, vendor_unmerge = _commands("vendor")

MERGE_COMMANDS = [customer_merge, customer_unmerge, vendor_merge, vendor_unmerge]
