"""`item convert` (R154): a non-inventory part or other charge becomes an inventory part, one way.

People only, with a reason, like a merge: the change cannot be undone. `--dry-run` is the
preview: the accounts the item will carry and what the conversion means for its stock.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from bookflow.company import item_conversions
from bookflow.core.registry import Applied, Plan, command


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ItemConvertInput(_Strict):
    item: str = Field(min_length=1, max_length=1000, description="The non-inventory part or other charge to convert: ID or canonical full name.")
    to: Literal["inventory"] = Field(description="What it becomes: `inventory`, an inventory part. The only conversion there is.")
    date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$", description=(
        "First day the item carries stock (ISO date). Defaults to today, or to the last document using the item when "
        "that is later; it must be on or after that document, and no stock line may be dated before it afterwards."))
    cogs_account: str | None = Field(default=None, min_length=1, max_length=1000, description=(
        "Cost-of-goods-sold account its sales charge: ID or full name. Defaults to the item's expense account when that is a "
        "cost-of-goods account, else the company's only one."))
    asset_account: str | None = Field(default=None, min_length=1, max_length=1000, description="Inventory asset account: ID or full name. Defaults to the company's Inventory Asset account, the only one an inventory part may use.")
    income_account: str | None = Field(default=None, min_length=1, max_length=1000, description="Income account its sales credit: ID or full name. Defaults to the item's own; required when the item had no sales side.")
    description: str | None = Field(default=None, description="Sales description. Defaults to the item's own, else its purchase description.")
    purchase_description: str | None = Field(default=None, description="Purchase description. Defaults to the item's own, else its sales description.")
    price: Any | None = Field(default=None, description="Sales price. Defaults to the item's own, else 0.")
    cost: Any | None = Field(default=None, description="Standard purchase cost. Defaults to the item's own, else 0. Not the value of stock on hand, which starts at 0.")


class ItemConvertOutput(_Strict):
    conversion_id: str | None = Field(description="The conversion; null in a dry run.")
    item_id: str = Field(description="The item, which keeps its id: every document that named it still does.")
    item_name: str
    from_type: str = Field(description="What it was: non_inventory_part or other_charge.")
    to_type: str = Field(description="What it is now: inventory_part.")
    effective_date: str = Field(description="First day it carries stock.")
    version: int = Field(description="The item's version after the conversion.")
    asset_account_id: str | None
    asset_account: str | None
    cogs_account_id: str | None
    cogs_account: str | None
    income_account_id: str | None
    income_account: str | None
    dropped_expense_account: str | None = Field(description="The expense account its purchases posted to; an inventory part has none, so earlier documents keep it and later purchases go to stock.")
    changed_fields: list[str]
    quantity_on_hand: str = Field(description="Always 0: stock starts empty. Count what is on the shelf with `inventory adjust`.")
    last_document_date: str | None = Field(description="Date of the last document using the item before the conversion; null when none did.")
    one_way: bool = Field(description="Always true: an inventory part cannot be converted back.")
    notes: list[str] = Field(description="What the conversion means, in words: one-way, history unchanged, stock starts at 0.")


item_convert = command(
    "item convert", scope="company", required_role="standard", capability="item",
    description=("Convert a non-inventory part or other charge into an inventory part. One-way: it cannot be "
                 "converted back. The item keeps its id, so every document still names it. Nothing posted changes: "
                 "earlier purchases stay expensed and earlier sales stay without cost of goods. Stock on hand starts "
                 "at 0 on the conversion date; from then on purchases put it into stock and sales relieve stock at "
                 "average cost. If some is on the shelf now, count it with `inventory adjust`. Refused while an open "
                 "purchase order, estimate, proposal or work order names the item. People only, with a reason; "
                 "`--dry-run` previews the accounts it will carry."),
    input_model=ItemConvertInput, output_model=ItemConvertOutput, writes={"company"}, positional=["item"],
    accepts_idempotency_key=True,
    error_codes=["E_RECORD_NOT_FOUND", "E_TYPE_CHANGE", "E_RECORD_IN_USE", "E_VALIDATION", "E_REASON_REQUIRED",
                 "E_INACTIVE_REFERENCE", "E_AMOUNT_PRECISION"])


@item_convert
def plan_convert(inp: ItemConvertInput, ctx, s) -> Plan:
    from bookflow.company import party_merges
    party_merges.require_person(s, "item", "convert")
    planned = item_conversions.plan(s, ctx, inp)
    return Plan(preview=ItemConvertOutput(**item_conversions.preview(s, planned)), data={"plan": planned})


@item_convert.applier
def apply_convert(plan: Plan, ctx, s) -> Applied:
    planned = plan.data["plan"]
    conversion_id, touched = item_conversions.apply(s, ctx, planned, "item convert")
    out = plan.preview.model_copy(update={"conversion_id": conversion_id})
    return Applied(out, touched, f"converted item {planned.item['full_name']} to an inventory part", audited=True)


ITEM_CONVERT_COMMANDS = [item_convert]
