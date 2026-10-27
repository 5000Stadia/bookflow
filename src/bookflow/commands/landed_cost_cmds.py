"""Landed cost (R104 increment 6): `landed-cost setup`, `charges`, `post`, `void`, `show` and `query`.

The work lives in ``company/landed_costs.py``; replay's part (a landed cost walked at its receipt's position and
recognised at its own date) in ``company/inventory_costing.py``.
"""
from bookflow.company import landed_cost_models as m
from bookflow.company import landed_costs as service
from bookflow.core.registry import command

_WRITE_ERRORS = ['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_PERIOD_CLOSED',
                 'E_WORK_DEPENDENCY', 'E_DUPLICATE_NUMBER', 'E_REASON_REQUIRED', 'E_VALUE_RANGE']

_POST = (
    'Spread freight, duty or another landed cost over the stock it came with. Charges are Landed Cost Clearing '
    'lines on bills, checks, card charges and vendor credits (`landed-cost charges` lists what is unallocated); '
    'targets are item receipts and the item lines of bills, checks and card charges that brought the stock in, whole '
    'or by line or item. basis: quantity (default), value, weight, volume or amounts. Each target receipt\'s share '
    'joins its cost as if it had been on the receipt, but is recognised at the allocation date: stock already sold '
    'before then owes a forward true-up to cost of goods sold dated at the allocation, stock still on hand keeps it. '
    'The date defaults to the latest charge date, never before a target receipt, moved to the first open date when '
    'the period is closed, so a freight bill for stock received before the close posts wholly in the open period. '
    'A charge may be allocated in parts; a freight credit allocates negative amounts. The dry run shows each '
    'target\'s share split into stock and issued. Example: `bookflow landed-cost post --charges '
    '\'[{"document":"FRT-88"}]\' --targets \'[{"document":"IR-1001"},{"document":"IR-1002"}]\' --basis quantity '
    '--reason "Freight for last week\'s deliveries"`.')

_CHARGES = (
    'Unallocated landed-cost charges by vendor, document, date and amount: every Landed Cost Clearing line of a bill, '
    'check, card charge or vendor credit (a freight credit is negative) less what allocations took. Ties the total to '
    'the clearing account on every date and refuses when they differ. Example: `bookflow landed-cost charges`.')


def plan_landed_cost_setup(inp, ctx, s):
    return service.plan_setup(s, ctx, inp)


def plan_landed_cost_charges(inp, ctx, s):
    return service.charges_report(s, ctx, inp)


def plan_landed_cost_post(inp, ctx, s):
    return service.prepare_post(s, ctx, inp)


def plan_landed_cost_void(inp, ctx, s):
    return service.prepare_void(s, ctx, inp)


def plan_landed_cost_show(inp, ctx, s):
    return service.show(s, ctx, inp)


def plan_landed_cost_query(inp, ctx, s):
    return service.query(s, ctx, inp)


setup = command(
    'landed-cost setup', scope='company', description=(
        'Make the Landed Cost Clearing account (other current asset) if it does not exist yet: charge freight and '
        'duty to it on bill, check, card charge and vendor credit expense lines, then spread it with `landed-cost '
        'post`. Example: `bookflow landed-cost setup --reason "Start tracking freight on stock"`.'),
    input_model=m.LandedCostSetupInput, output_model=m.LandedCostSetupOutput, writes={'company'},
    required_role='standard', capability='ledger.post', accepts_idempotency_key=True,
    error_codes=['E_VALIDATION', 'E_REASON_REQUIRED'])(plan_landed_cost_setup)
setup.applier(service.apply_setup)

charges = command(
    'landed-cost charges', scope='company', description=_CHARGES, input_model=m.LandedCostChargesInput,
    output_model=m.ChargesOutput, required_role='member', capability='ledger.read',
    error_codes=['E_VALIDATION', 'E_RECORD_NOT_FOUND'])(plan_landed_cost_charges)

post = command(
    'landed-cost post', scope='company', description=_POST, input_model=m.LandedCostPostInput,
    output_model=m.LandedCostWriteOutput, writes={'company'}, required_role='standard', capability='ledger.post',
    accepts_idempotency_key=True, error_codes=_WRITE_ERRORS)(plan_landed_cost_post)
post.applier(service.apply_post)

void = command(
    'landed-cost void', scope='company', description=(
        'Void a landed-cost allocation: reversing lines, its journal entry reversed, and the forward true-ups it owed '
        'backed out at its date. Refused when a freight credit allocated later needs it, and in a closed period. '
        'Example: `bookflow landed-cost void LC-3 --reason "Allocated to the wrong receipt"`.'),
    input_model=m.LandedCostVoidInput, output_model=m.LandedCostWriteOutput, writes={'company'},
    required_role='standard', capability='ledger.post', accepts_idempotency_key=True, positional=['allocation'],
    error_codes=_WRITE_ERRORS)(plan_landed_cost_void)
void.applier(service.apply_void)

show = command(
    'landed-cost show', scope='company', description=(
        'One allocation: its charges, targets, basis, measures and shares, each target split into stock and issued, '
        'and the forward true-ups it owns. Example: `bookflow landed-cost show LC-3`.'),
    input_model=m.LandedCostShowInput, output_model=m.LandedCostOutput, required_role='member',
    capability='ledger.read', positional=['allocation'],
    error_codes=['E_RECORD_NOT_FOUND', 'E_VALIDATION'])(plan_landed_cost_show)

query = command(
    'landed-cost query', scope='company', description=(
        'Landed-cost allocations, newest first; status, kind (receipt_shipping lists item receipts\' own shipping), '
        'document (a receipt or charge document) and dates narrow it.'),
    input_model=m.LandedCostQueryInput, output_model=m.LandedCostListOutput, required_role='member',
    capability='ledger.read', error_codes=['E_RECORD_NOT_FOUND', 'E_VALIDATION'])(plan_landed_cost_query)
