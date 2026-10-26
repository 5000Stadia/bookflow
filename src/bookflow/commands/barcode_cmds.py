"""`barcode lookup` and `barcode generate` (R104 increment 3, design/advanced-inventory.md §7).

A scanned code needs neither command to be used: every `item` field resolves id, then name, then
barcode, so an agent passes the code as `item`. `barcode lookup` says what a code names, where it is
and which line fields to use; `barcode generate` numbers items that have no code.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bookflow.company import barcodes
from bookflow.core.registry import Applied, Plan, command


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BarcodeLookupInput(_Strict):
    code: str = Field(min_length=1, max_length=128, description="The code as scanned or typed, e.g. `0012345678905`.")
    as_of: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$",
                              description="Date the on-hand figures are read as of (ISO). Defaults to today.")


class SiteQuantity(_Strict):
    site_id: str
    site: str
    quantity: str


class BarcodeMatch(_Strict):
    kind: str = Field(description="What the code names: `item`, else `serial_number` or `lot` (a scanned unit label); bins join with bins.")
    id: str
    name: str = Field(description="The record's full name.")
    type: str = Field(description="The item type.")
    active: bool
    barcode: str | None
    on_hand: list[SiteQuantity] = Field(description="Quantity on hand by site as of as_of; sites holding none are left out.")
    line_fields: dict[str, Any] = Field(description="Fields to put on a document line for this code, e.g. {\"item\": \"Copper Coupling\"}, or {\"item\": \"Water Heater\", \"serial_numbers\": [\"WH-1\"]} for a serial label.")


class BarcodeLookupOutput(_Strict):
    code: str
    as_of: str
    found: bool = Field(description="True when the code names exactly one record (preferring active ones).")
    kind: str | None = Field(description="`item`, `serial_number`, `lot`, `ambiguous` when it names more than one, or null when nothing carries it.")
    match: BarcodeMatch | None = Field(description="The one record the code names; null when none or ambiguous.")
    matches: list[BarcodeMatch] = Field(description="Every record carrying the code, active first: two when a duplicate is kept.")
    ambiguous: bool
    valid_ean13: bool = Field(description="Whether the code is a 13-digit EAN with a correct check digit (other symbologies are still looked up).")
    message: str = Field(description="The answer in words; for an unknown code, how to assign it to an item.")


class BarcodeGenerateInput(_Strict):
    items: list[str] | None = Field(default=None, min_length=1, max_length=500, description=(
        "Items to number: ID, full name or barcode each. Omitted: every active inventory part, assembly and "
        "non-inventory part with no code."))


class AssignedCode(_Strict):
    item_id: str
    item: str
    barcode: str | None


class SkippedItem(AssignedCode):
    reason: str


class BarcodeGenerateOutput(_Strict):
    assigned: list[AssignedCode] = Field(description="Each item given a code, with its new EAN-13.")
    skipped: list[SkippedItem] = Field(description="Named items left as they are: one that has a code keeps it.")
    next_number: int = Field(description="The company counter after this run: the next code's serial number.")


barcode_lookup = command(
    "barcode lookup", scope="company", required_role="member", capability="item",
    description=("Say what a scanned or typed code names: the item carrying it as its barcode, its on-hand "
                 "quantity by site and the line fields to use (pass the code itself as `item` on any document "
                 "line; it resolves after id and name). A code on two active items (a duplicate kept from before "
                 "codes were checked) is ambiguous and both are listed. An unknown code is not an error: the "
                 "answer says so and how to assign it to an item."),
    input_model=BarcodeLookupInput, output_model=BarcodeLookupOutput, writes=set(), positional=[],
    error_codes=["E_VALIDATION"])


@barcode_lookup
def plan_lookup(inp: BarcodeLookupInput, ctx, s) -> Plan:
    return Plan(BarcodeLookupOutput.model_validate(barcodes.lookup(s, inp)))


barcode_generate = command(
    "barcode generate", scope="company", required_role="standard", capability="item",
    description=("Give items without a code an EAN-13 barcode in the restricted-circulation range (prefix 2, for "
                 "the company's own labels), numbered from the company counter with its check digit. Existing codes "
                 "never change: a named item that has one is skipped and listed. Each change is an item update in "
                 "the item's history. `--dry-run` shows the codes it would assign."),
    input_model=BarcodeGenerateInput, output_model=BarcodeGenerateOutput, writes={"company"}, positional=[],
    accepts_idempotency_key=True,
    error_codes=["E_RECORD_NOT_FOUND", "E_INACTIVE_REFERENCE", "E_VALIDATION"])


@barcode_generate
def plan_generate(inp: BarcodeGenerateInput, ctx, s) -> Plan:
    planned = barcodes.plan_generate(s, ctx, inp)
    preview = BarcodeGenerateOutput(assigned=planned["assigned"], skipped=planned["skipped"],
                                    next_number=planned["next_number"])
    return Plan(preview=preview, data={"plan": planned})


@barcode_generate.applier
def apply_generate(plan: Plan, ctx, s) -> Applied:
    planned: dict[str, Any] = plan.data["plan"]
    touched = barcodes.apply_generate(s, planned)
    count = len(planned["assigned"])
    return Applied(plan.preview, touched, f"generated {count} barcode{'' if count == 1 else 's'}" if count else "no change")


BARCODE_COMMANDS = [barcode_lookup, barcode_generate]
