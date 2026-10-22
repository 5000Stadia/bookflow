"""`customer`, `vendor`, `account` and `item` `merge` and `unmerge` (R117).

A merge records that one entry is merged into another and changes nothing posted; every read
resolves the merged entry to its survivor. People only, with a reason. `--dry-run` is the
preview: what moves and the survivor's balance after.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from bookflow.company import list_merges, party_merges
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


class ListDocumentCount(_Strict):
    type: str
    count: int = Field(description="Documents of this type that name the merged entry.")
    postings: int = Field(description="Posting lines (accounts) or item lines (items) on the merged entry in them.")


class ReferenceOut(_Strict):
    table: str
    record_id: str
    field: str
    action: str = Field(description="repoint: the row now names the survivor; end: the survivor already has its own row, which is kept, and this one is made inactive.")
    owner_type: str
    owner_id: str
    label: str
    conflict: str | None = Field(description="Why the survivor's own row is kept, for an ended row.")


class ListMergeOutput(_Strict):
    merge_id: str | None = Field(description="The merge; null only in a dry run of a new merge.")
    changed: bool = Field(description="False when this exact merge already stands; nothing was written.")
    merged_id: str
    merged_name: str
    survivor_id: str
    survivor_name: str
    documents: list[ListDocumentCount] = Field(description="Documents naming the merged entry, by type; they now read as the survivor's.")
    document_count: int
    merged_balance: MoneyOut | None = Field(description="Accounts: the merged account's own balance (normal side). Null for items.")
    survivor_balance: MoneyOut | None = Field(description="Accounts: the survivor's balance before this merge.")
    survivor_balance_after: MoneyOut | None = Field(description="Accounts: the survivor's balance with the merged account folded in.")
    references: list[ReferenceOut] = Field(description="Every editable list row naming the merged entry, re-pointed to the survivor (or ended where the survivor has its own); undo puts each back.")
    notes: list[str] = Field(default_factory=list, description="What the merge also means, in plain words: for example that reports of closed periods read the merged entry as the survivor too.")


class ListUnmergeOutput(_Strict):
    merge_id: str
    changed: bool = Field(description="False when the merge was already undone; nothing was written.")
    merged_id: str
    survivor_id: str
    reactivated: bool = Field(description="Whether the merged entry was made active again.")
    restored: int = Field(description="Re-pointed references put back to the merged entry.")
    kept: int = Field(description="Re-pointed references changed since the merge, left as they now stand.")


_LIST_EFFECTS = {
    "account": ("every report, balance, register and filter shows the merged account's postings under the survivor; "
                "item, vendor, account and preference fields naming it are re-pointed to the survivor"),
    "item": ("sales and purchases by item and every item filter show the merged item's lines under the survivor; "
             "group and assembly members, price levels and vendor profiles naming it are re-pointed to the survivor"),
}


def _list_commands(kind: str):
    merge = command(f"{kind} merge", scope="company", required_role="standard", capability=kind,
        description=(f"Merge a duplicate {kind} into another of the same type. Nothing posted changes: "
                     f"{_LIST_EFFECTS[kind]}, and the merged {kind} is made inactive and hidden. A saved document "
                     f"that names it saves with the survivor when next edited. People only, with a reason; "
                     f"`--dry-run` previews what moves, every reference re-pointed and any conflict. Undo with "
                     f"`{kind} unmerge`."),
        input_model=MergeInput, output_model=ListMergeOutput, writes={"company"}, positional=["merged", "into"],
        accepts_idempotency_key=True,
        error_codes=["E_RECORD_NOT_FOUND", "E_MERGE_REFUSED", "E_REASON_REQUIRED"])

    @merge
    def plan_merge(inp: MergeInput, ctx, s) -> Plan:
        party_merges.require_person(s, kind, "merge")
        planned = list_merges.plan_merge(s.company, kind, inp.merged, inp.into)
        return Plan(preview=ListMergeOutput(**list_merges.preview(s.company, planned)), data={"plan": planned})

    @merge.applier
    def apply_merge(plan: Plan, ctx, s) -> Applied:
        planned = plan.data["plan"]
        if not planned.changed:
            return Applied(plan.preview, [], "already merged")
        merge_id, touched = list_merges.apply_merge(s, ctx, planned, f"{kind} merge")
        out = plan.preview.model_copy(update={"merge_id": merge_id})
        return Applied(out, touched, f"merged {kind} {planned.merged['full_name']} into {planned.survivor['full_name']}",
                       audited=True)

    unmerge = command(f"{kind} unmerge", scope="company", required_role="standard", capability=kind,
        description=(f"Undo an {kind} merge: the merged {kind} reads as itself again, is made active again if the "
                     f"merge made it inactive, and every reference the merge re-pointed is put back unless it has "
                     f"been changed since. Nothing posted changes. People only, with a reason."),
        input_model=UnmergeInput, output_model=ListUnmergeOutput, writes={"company"}, positional=["merged"],
        accepts_idempotency_key=True,
        error_codes=["E_RECORD_NOT_FOUND", "E_MERGE_REFUSED", "E_REASON_REQUIRED"])

    @unmerge
    def plan_unmerge(inp: UnmergeInput, ctx, s) -> Plan:
        party_merges.require_person(s, kind, "unmerge")
        planned = list_merges.plan_unmerge(s.company, kind, inp.merged)
        row = planned.merge or planned.last
        return Plan(preview=ListUnmergeOutput(merge_id=row["id"], changed=planned.merge is not None,
                                              merged_id=row["merged_id"], survivor_id=row["survivor_id"],
                                              reactivated=bool(planned.merge and planned.merge["deactivated_merged"]),
                                              restored=len(planned.restores), kept=len(planned.kept)),
                    data={"plan": planned})

    @unmerge.applier
    def apply_unmerge(plan: Plan, ctx, s) -> Applied:
        planned = plan.data["plan"]
        if planned.merge is None:
            return Applied(plan.preview, [], "already unmerged")
        touched = list_merges.apply_unmerge(s, ctx, planned, f"{kind} unmerge")
        return Applied(plan.preview, touched, f"unmerged {kind} {planned.merged['full_name']}", audited=True)

    return merge, unmerge


account_merge, account_unmerge = _list_commands("account")
item_merge, item_unmerge = _list_commands("item")

MERGE_COMMANDS = [customer_merge, customer_unmerge, vendor_merge, vendor_unmerge,
                  account_merge, account_unmerge, item_merge, item_unmerge]
