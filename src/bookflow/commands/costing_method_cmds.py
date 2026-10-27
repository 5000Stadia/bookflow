"""Costing method (R104 increment 7): `costing-method set`, `withdraw` and `show`, and `report inventory-layers`.

The work lives in ``company/costing_methods.py``; FIFO by replay in ``company/inventory_costing.py``.
"""
from bookflow.company import costing_method_models as m
from bookflow.company import costing_methods as service
from bookflow.core.registry import command

_WRITE_ERRORS = ['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_PERIOD_CLOSED', 'E_PERMISSION', 'E_REASON_REQUIRED']

_SET = (
    'Change how stock is costed, for every stocked item, from a date: fifo (first in, first out: each sale takes the '
    'oldest units\' cost) or average (weighted average, the default). A person\'s act, on the accountant\'s advice: a '
    'method is meant to be kept. effective_from must be after the closing date and after the last switch. On that '
    'date the stock on hand becomes one opening FIFO layer at its average (switching back, the layers merge into the '
    'average): nothing is revalued and the switch posts nothing. Sales, returns and adjustments already dated on or '
    'after it are costed again by the new method, each corrected at its own date. The dry run shows, per item and in '
    'total, the change in stock value and cost of goods sold, and each correction entry. Example: `bookflow '
    'costing-method set --method fifo --from 2027-01-01 --reason "FIFO from the new year, on our '
    'accountant\'s advice"`.')


def plan_costing_method_set(inp, ctx, s):
    return service.plan_set(s, ctx, inp)


def plan_costing_method_withdraw(inp, ctx, s):
    return service.plan_withdraw(s, ctx, inp)


def plan_costing_method_show(inp, ctx, s):
    return service.show(s, ctx, inp)


def plan_inventory_layers(inp, ctx, s):
    return service.inventory_layers(s, ctx, inp)


set_method = command(
    'costing-method set', scope='company', description=_SET, input_model=m.CostingMethodSetInput,
    output_model=m.CostingMethodWriteOutput, writes={'company'}, required_role='admin', capability='company',
    accepts_idempotency_key=True, error_codes=_WRITE_ERRORS)(plan_costing_method_set)
set_method.applier(service.apply_set)

withdraw = command(
    'costing-method withdraw', scope='company', description=(
        'Take back a costing-method switch whose date has not come, by its id or date; anything already dated on or '
        'after it is costed again by the method before it. A person\'s act. Example: `bookflow costing-method '
        'withdraw 2027-01-01 --reason "Staying on average for now"`.'),
    input_model=m.CostingMethodWithdrawInput, output_model=m.CostingMethodWriteOutput, writes={'company'},
    required_role='admin', capability='company', accepts_idempotency_key=True, positional=['switch'],
    error_codes=_WRITE_ERRORS)(plan_costing_method_withdraw)
withdraw.applier(service.apply_withdraw)

show = command(
    'costing-method show', scope='company', description=(
        'The costing method in force today (average or fifo) and every switch, scheduled, past and withdrawn. '
        'Example: `bookflow costing-method show`.'),
    input_model=m.CostingMethodShowInput, output_model=m.CostingMethodOutput, required_role='member',
    capability='ledger.read', error_codes=['E_VALIDATION'])(plan_costing_method_show)

layers_report = command(
    'report inventory-layers', scope='company', description=(
        'Inventory Layers on as_of: each stocked item\'s cost layers, oldest first -- the document and date that '
        'brought the units in, the quantity left, its value and unit cost. Under FIFO a sale takes the oldest layer '
        'first; under average each item is one row. item narrows it to one item; items are paged (next_offset). '
        'Example: `bookflow report inventory-layers --as-of 2027-01-31`.'),
    input_model=m.InventoryLayersInput, output_model=m.InventoryLayersOutput, writes=set(), required_role='member',
    capability='reports', error_codes=['E_VALIDATION', 'E_RECORD_NOT_FOUND'])(plan_inventory_layers)
