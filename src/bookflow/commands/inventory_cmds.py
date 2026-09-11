"""Adjust what the company holds, void an adjustment, and read one back.

Three verbs, because an inventory adjustment is a posted document like any other: enter it,
undo it, and read what it said. There is deliberately no ``update``: correcting an adjustment
means moving a quantity *and* the cost every later issue took from it, and that lands with
the purchase and sales wiring that makes those issues real documents. Voiding and re-entering
does the same job today and leaves the same audit trail.
"""
from bookflow.company import inventory
from bookflow.company.inventory_models import (
    InventoryAdjustInput, InventoryOutput, InventoryShowInput, InventoryVoidInput,
    InventoryWriteOutput,
)
from bookflow.core.registry import Plan, command

_ADJUST = (
    'Change what an inventory item holds and what it is worth, and post the matching entry. '
    'A positive `quantity_change` brings stock in and must say what it is worth in '
    '`value_change`; a negative one takes stock out and is worth what the weighted average '
    'says, so it supplies no value. `value_change` on its own writes the asset up, or down '
    'with `negative_value`, without moving quantity. This is how opening stock is set: the '
    'quantity you counted, what you paid for it, and an opening-balance or shrinkage account '
    'in `adjustment_account` to carry the other side. The inventory-asset account is the '
    "item's own and is never chosen here. "
    'Every affected date is checked before anything is written: an adjustment that would take '
    'the item below zero on any date is refused, and so is one that would change an entry or a '
    'valuation inside a closed period -- the whole adjustment, naming the period, never a delta '
    'moved to today. A backdated adjustment recosts every later issue of that item, each by its '
    "own dated correction at that issue's date, so no earlier report moves for a reason it "
    'cannot show. Inventory asset on the balance sheet always equals the total on '
    '`report inventory-valuation` for the same date.'
)

_VOID = (
    'Void an inventory adjustment with a required reason. Its accounting is reversed exactly, '
    "at the adjustment's own date, and its stock movements are reversed with it, so the "
    'quantity and the value go back together. Every later issue of that item is recosted by '
    'its own dated correction, and the corrections the voided adjustment had caused are backed '
    'out the same way. A void that would take the item below zero on any date, or that would '
    'touch a closed period, is refused whole.'
)

_SHOW = (
    'Show an inventory adjustment: its current or a selected earlier revision, the item it '
    'moved, what the quantity and the value did, what the item now holds and is worth, and '
    'every dated cost correction recorded against that item.'
)

_WRITE_ERRORS = ['E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_VALIDATION', 'E_VALUE_RANGE',
                 'E_AMOUNT_PRECISION', 'E_UNBALANCED_ENTRY', 'E_PERIOD_CLOSED', 'E_DUPLICATE_NUMBER']


@command('inventory adjust', scope='company', description=_ADJUST,
         input_model=InventoryAdjustInput, output_model=InventoryWriteOutput,
         writes={'company'}, required_role='standard', capability='ledger.post',
         accepts_idempotency_key=True, error_codes=_WRITE_ERRORS)
def plan_inventory_adjust(inp, ctx, s):
    return inventory.prepare(s, ctx, inp, 'post')


@command('inventory void', scope='company', description=_VOID,
         input_model=InventoryVoidInput, output_model=InventoryWriteOutput,
         writes={'company'}, required_role='standard', capability='ledger.post',
         accepts_idempotency_key=True, positional=['adjustment'],
         version_source=('inventory show', 'adjustment', 'version'),
         error_codes=['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_VALIDATION',
                      'E_REASON_REQUIRED', 'E_PERIOD_CLOSED', 'E_VALUE_RANGE'])
def plan_inventory_void(inp, ctx, s):
    return inventory.prepare(s, ctx, inp, 'void')


@command('inventory show', scope='company', description=_SHOW,
         input_model=InventoryShowInput, output_model=InventoryOutput,
         required_role='member', capability='ledger.read', positional=['adjustment'],
         error_codes=['E_RECORD_NOT_FOUND', 'E_VALIDATION'])
def plan_inventory_show(inp, ctx, s):
    return Plan(inventory.show(s, inp))


plan_inventory_adjust.ledger = True
plan_inventory_void.ledger = True
plan_inventory_adjust.applier(inventory.apply)
plan_inventory_void.applier(inventory.apply)
