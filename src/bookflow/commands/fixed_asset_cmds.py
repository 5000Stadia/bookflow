"""Fixed-asset depreciation and loans (R119).

Two nouns. ``fixed-asset`` sets up book depreciation on a fixed-asset item, shows its schedule,
posts depreciation through a date and disposes of the asset. ``loan`` keeps a loan's terms, shows
its amortization schedule and pays it with a check that splits principal, interest and escrow.
The work lives in ``company/fixed_assets.py``, ``company/loans.py`` and ``company/amortization.py``.
"""
from bookflow.company import fixed_assets, loans
from bookflow.company.fixed_asset_models import (
    FixedAssetDepreciateInput, FixedAssetDepreciateOutput, FixedAssetDisposeInput, FixedAssetDisposeOutput,
    FixedAssetListInput, FixedAssetListOutput, FixedAssetScheduleOutput, FixedAssetSelector, FixedAssetSetupInput,
    FixedAssetSetupOutput, LoanCreateInput, LoanListInput, LoanPageOutput, LoanPayInput, LoanPayOutput, LoanSelector,
    LoanShowOutput, LoanUpdateInput, LoanWriteOutput,
)
from bookflow.core.registry import Plan, command

_LEDGER_ERRORS = ['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_AMOUNT_PRECISION', 'E_VALUE_RANGE',
                  'E_PERIOD_CLOSED']


def plan_fixed_asset_setup(inp, ctx, s) -> Plan:
    return fixed_assets.prepare_setup(s, ctx, inp)


def plan_fixed_asset_schedule(inp, ctx, s) -> Plan:
    return Plan(fixed_assets.schedule(s, inp))


def plan_fixed_asset_list(inp, ctx, s) -> Plan:
    return Plan(fixed_assets.list_assets(s, inp))


def plan_fixed_asset_depreciate(inp, ctx, s) -> Plan:
    return fixed_assets.prepare_depreciate(s, ctx, inp)


def plan_fixed_asset_dispose(inp, ctx, s) -> Plan:
    return fixed_assets.prepare_dispose(s, ctx, inp)


def plan_loan_create(inp, ctx, s) -> Plan:
    return loans.prepare_create(s, ctx, inp)


def plan_loan_update(inp, ctx, s) -> Plan:
    return loans.prepare_update(s, ctx, inp)


def plan_loan_show(inp, ctx, s) -> Plan:
    return Plan(loans.show(s, inp))


def plan_loan_list(inp, ctx, s) -> Plan:
    return Plan(loans.page(s, inp))


def plan_loan_pay(inp, ctx, s) -> Plan:
    return loans.prepare_pay(s, ctx, inp)


fixed_asset_setup = command(
    'fixed-asset setup', scope='company',
    description=('Set up or change book depreciation on a fixed-asset item: method (straight_line, or declining_balance at'
                 ' 200 or 150 percent of the straight-line rate, switching to straight line when that is larger),'
                 ' salvage value, in-service date (default the purchase date), useful life in months, and month or year'
                 ' periods. Optionally sets the accumulated depreciation, depreciation expense and gain/loss accounts on'
                 ' the item. The cost basis is the item book basis, else its original cost. Returns the schedule.'
                 ' Periods start with the in-service month. Rounding: each period takes its share of what remains'
                 ' above salvage, rounded half to even to the cent, and the last period takes exactly the rest, so the'
                 ' schedule sums to cost less salvage. Tax bases, MACRS and units of production are not computed.'),
    input_model=FixedAssetSetupInput, output_model=FixedAssetSetupOutput, writes={'company'}, required_role='standard',
    capability='item', accepts_idempotency_key=True, positional=['item'],
    version_source=('fixed-asset schedule', 'item', 'version'),
    error_codes=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_AMOUNT_PRECISION', 'E_VALUE_RANGE',
                 'E_VERSION_CONFLICT'])(plan_fixed_asset_setup)
fixed_asset_setup.applier(fixed_assets.apply_setup)

fixed_asset_schedule = command(
    'fixed-asset schedule', scope='company',
    description=('Show a fixed asset depreciation schedule: every period with its dates, amount, the method that gave it,'
                 ' accumulated depreciation and book value, and whether it is posted (with its journal entry), pending'
                 ' or stopped by a disposal. The schedule sums exactly to cost less salvage.'),
    input_model=FixedAssetSelector, output_model=FixedAssetScheduleOutput, required_role='member', capability='ledger.read',
    positional=['item'], error_codes=['E_VALIDATION', 'E_RECORD_NOT_FOUND'])(plan_fixed_asset_schedule)

fixed_asset_list = command(
    'fixed-asset list', scope='company',
    description='List fixed-asset items with cost, depreciation posted, book value, periods pending and status.',
    input_model=FixedAssetListInput, output_model=FixedAssetListOutput, required_role='member', capability='ledger.read',
    error_codes=[])(plan_fixed_asset_list)

fixed_asset_depreciate = command(
    'fixed-asset depreciate', scope='company',
    description=('Post depreciation for every period ending on or before `through` that is not posted yet: debit the'
                 ' depreciation expense account, credit accumulated depreciation. per_period writes one journal entry'
                 ' per period end date; combined writes one dated `through`. Each asset period is linked to its entry,'
                 ' so running again posts nothing twice, and voiding an entry reopens its periods. Dry-run is the'
                 ' preview. A date in a closed period is refused: post it combined into an open period instead.'),
    input_model=FixedAssetDepreciateInput, output_model=FixedAssetDepreciateOutput, writes={'company'},
    required_role='standard', capability='ledger.post', accepts_idempotency_key=True,
    error_codes=_LEDGER_ERRORS)(plan_fixed_asset_depreciate)
fixed_asset_depreciate.ledger = True
fixed_asset_depreciate.applier(fixed_assets.apply_depreciate)

fixed_asset_dispose = command(
    'fixed-asset dispose', scope='company',
    description=('Sell or scrap a fixed asset: one journal entry debiting the net proceeds (proceeds less costs) to'
                 ' `deposit_to`, debiting the accumulated depreciation posted, crediting the asset account with the cost,'
                 ' and the gain (credit) or loss (debit) on the item gain/loss account. Depreciation through the date'
                 ' must be posted first; later periods stop. Marks the item sold or disposed of.'),
    input_model=FixedAssetDisposeInput, output_model=FixedAssetDisposeOutput, writes={'company'}, required_role='standard',
    capability='ledger.post', accepts_idempotency_key=True, positional=['item'],
    error_codes=_LEDGER_ERRORS)(plan_fixed_asset_dispose)
fixed_asset_dispose.ledger = True
fixed_asset_dispose.applier(fixed_assets.apply_dispose)

_LOAN_ERRORS = ['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_AMOUNT_PRECISION', 'E_VALUE_RANGE']

loan_create = command(
    'loan create', scope='company',
    description=('Add a loan, as the anchor Loan Manager does: liability account, interest account, amount borrowed,'
                 ' annual rate, number of payments, frequency, first due date, optional lender, payment account, balloon'
                 ' and escrow. The regular payment is the level payment unless given. Each payment\'s interest is the'
                 ' balance times the periodic rate (annual rate / payments per year), rounded half to even; the last'
                 ' payment pays exactly the balance left, absorbing the rounding. Creating a loan posts nothing:'
                 ' record the money borrowed into the liability account with a deposit or journal entry.'),
    input_model=LoanCreateInput, output_model=LoanWriteOutput, writes={'company'}, required_role='standard',
    capability='account', accepts_idempotency_key=True, error_codes=_LOAN_ERRORS + ['E_NAME_TAKEN'])(plan_loan_create)
loan_create.applier(loans.apply_create)

loan_update = command(
    'loan update', scope='company',
    description=('Change a loan: name, lender, interest, escrow or payment account, escrow amount, rate or regular'
                 ' payment. A new rate without a new payment recomputes the level payment over the payments left.'),
    input_model=LoanUpdateInput, output_model=LoanWriteOutput, writes={'company'}, required_role='standard',
    capability='account', accepts_idempotency_key=True, positional=['loan'], version_source=('loan show', 'loan', 'version'),
    error_codes=_LOAN_ERRORS + ['E_NAME_TAKEN', 'E_VERSION_CONFLICT'])(plan_loan_update)
loan_update.applier(loans.apply_update)

loan_show = command(
    'loan show', scope='company',
    description=('Show a loan and its amortization schedule: payments made (with their checks) and remaining, each split'
                 ' into principal, interest and escrow with the balance after it; the balance, the liability account'
                 ' balance and whether they tie, the next payment and the maturity date.'),
    input_model=LoanSelector, output_model=LoanShowOutput, required_role='member', capability='ledger.read',
    positional=['loan'], error_codes=['E_VALIDATION', 'E_RECORD_NOT_FOUND'])(plan_loan_show)

loan_list = command(
    'loan list', scope='company', description='List loans with balance, next payment and whether each ties to its liability account.',
    input_model=LoanListInput, output_model=LoanPageOutput, required_role='member', capability='ledger.read',
    error_codes=['E_VALIDATION'])(plan_loan_list)

loan_pay = command(
    'loan pay', scope='company',
    description=('Pay the next scheduled loan payment with a check (bank account) or card charge (credit card) whose'
                 ' lines split it: principal to the liability account, interest to the interest account, escrow to the'
                 ' escrow account. extra_principal adds principal (the rest of the schedule is recomputed: same'
                 ' payment, fewer payments); extra_only pays principal alone outside the schedule. Void the check to'
                 ' undo the payment. Dry-run is the preview.'),
    input_model=LoanPayInput, output_model=LoanPayOutput, writes={'company'}, required_role='standard',
    capability='ledger.post', accepts_idempotency_key=True, positional=['loan'],
    error_codes=_LEDGER_ERRORS + ['E_DUPLICATE_NUMBER'])(plan_loan_pay)
loan_pay.ledger = True
loan_pay.applier(loans.apply_pay)

FIXED_ASSET_COMMANDS = [fixed_asset_setup, fixed_asset_schedule, fixed_asset_list, fixed_asset_depreciate,
                        fixed_asset_dispose, loan_create, loan_update, loan_show, loan_list, loan_pay]
