"""Read what each tax agency is owed, and pay it.

``sales-tax liability`` is the read and ``sales-tax pay`` is the verb; ``sales-tax payment``
is the noun it writes. Sales tax collected on an invoice is money held for somebody else, and
until it is remitted the liability only grows -- so the read and the document that clears it
belong together under one noun.

A remittance is not a check written to a vendor who happens to be an agency. A check names an
account; it does not say whose liability fell, and the liability read is derived per agency
from what the postings say. The dedicated document is what carries that attribution.
"""
from bookflow.core.registry import Plan, command
from bookflow.company import sales_tax_adjustments, sales_tax_payments
from bookflow.company.sales_tax_adjustment_models import (
    SalesTaxAdjustInput, SalesTaxAdjustmentOutput, SalesTaxAdjustmentPageOutput,
    SalesTaxAdjustmentQueryInput, SalesTaxAdjustmentShowInput, SalesTaxAdjustmentVoidInput,
    SalesTaxAdjustmentWriteOutput,
)
from bookflow.company.sales_tax_payment_models import (
    SalesTaxPayInput, SalesTaxPaymentPageOutput, SalesTaxPaymentQueryInput,
    SalesTaxPaymentShowInput, SalesTaxPaymentOutput, SalesTaxPaymentVoidInput,
    SalesTaxPaymentWriteOutput,
)
from bookflow.company.sales_tax_reports import (
    SalesTaxLiabilityInput, SalesTaxLiabilityOutput, sales_tax_liability,
)

_LIABILITY = (
    'How much sales tax is owed, by agency, from the tax the books recorded -- for one date'
    ' (as_of) or for a period (date_from and date_to). For a quarter\'s tax, pass the quarter\'s'
    ' first and last days, for example date_from=2025-01-01 and date_to=2025-03-31; for the month'
    ' so far, pass the first of the month and today: tax_charged is the tax collected'
    ' in that period, tax_credited, remitted, adjusted (sales tax adjustments, an increase'
    ' positive) and unattributed are what moved in it, beginning_balance is what was owed the day before date_from and balance is what is'
    ' owed at date_to. With as_of alone every column runs from the start of the books and'
    ' balance is the running total owed, not one month\'s tax. Each'
    ' row is one agency with the tax charged on posted sales, the tax taken back by credit'
    ' memos, what has been remitted, what `sales-tax adjust` added or took off, and the balance'
    ' still owed; an effect on the sales tax payable account that names no agency -- a journal'
    ' entry posted straight at it -- is its own row rather than dropped, so the total is that account\'s balance on the balance'
    ' sheet for the same date. Accrual only: the liability is recorded when the invoice is,'
    ' which is the only basis on which this product records tax at all, and a company set to'
    ' the payment-receipt basis is refused rather than answered. Totals cover every agency and'
    ' rows are paged.'
)

_PAY = (
    'Remit sales tax to one agency. The payment debits the sales tax payable account and'
    ' credits the account the money came from -- a bank account, which falls, or a credit card'
    ' account, which rises. `amount` defaults to everything the agency is owed through'
    ' `through_date`, and `through_date` defaults to the payment date; remitting less leaves'
    ' the remainder owed, and remitting more than is owed is refused rather than posted:'
    ' when the agency is owed more than the tax the books recorded (a penalty, interest, a'
    ' rounding difference, a balance from earlier books), record that first with'
    ' `sales-tax adjust` and then pay it. `check_number` is the number on the paper check and is accepted'
    ' only when the method is a check drawn on a bank account. One agency per payment.'
)

ERRORS = {
    'pay': ['E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_VALIDATION', 'E_VALUE_RANGE',
            'E_AMOUNT_PRECISION', 'E_PERIOD_CLOSED', 'E_DUPLICATE_NUMBER', 'E_VERSION_CONFLICT',
            'E_APPLICATION_CAPACITY', 'E_TAX_BASIS_UNSUPPORTED'],
    'void': ['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_VALIDATION', 'E_REASON_REQUIRED',
             'E_PERIOD_CLOSED', 'E_APPLICATION_INACTIVE'],
}

DESCRIPTIONS = {
    'pay': _PAY,
    'void': ('Void a sales tax remittance with a required reason. Its accounting is reversed at'
             ' its own date, the agency goes back to being owed what it was owed before, its'
             ' number stays occupied and its history stays readable. There is no correction: a'
             ' remittance is one amount to one agency on one date, so a wrong one is voided and'
             ' written again.'),
    'show': ('Show a sales tax remittance: which agency was paid, out of which account, by what'
             ' method and check number, the period it answered, what the agency was owed when it'
             ' was written and what was left after it, and its posting batches.'),
    'query': ('Page sales tax remittances in accounting-date and stable-id order, oldest first or'
              ' newest first, with exact agency, date, funding-account, method, number,'
              ' check-number and status filters; restart on company audit changes.'),
}


def _write(name, verb, model, output_model):
    def planner(inp, ctx, s):
        return sales_tax_payments.prepare(s, ctx, inp, verb)

    cmd = command(
        name, scope='company', description=DESCRIPTIONS[verb],
        input_model=model, output_model=output_model, writes={'company'},
        required_role='standard', capability='ledger.post', accepts_idempotency_key=True,
        positional=[] if verb == 'pay' else ['payment'],
        version_source=None if verb == 'pay' else ('sales-tax payment show', 'payment', 'version'),
        error_codes=ERRORS[verb])(planner)
    cmd.ledger = True
    cmd.applier(sales_tax_payments.apply)
    return cmd


def _read(verb, model, output_model):
    def planner(inp, ctx, s):
        return Plan(sales_tax_payments.show(s, inp) if verb == 'show'
                    else sales_tax_payments.page(s, ctx, inp))

    return command(
        'sales-tax payment ' + verb, scope='company', description=DESCRIPTIONS[verb],
        input_model=model, output_model=output_model,
        required_role='member', capability='ledger.read',
        positional=['payment'] if verb == 'show' else [],
        error_codes=['E_RECORD_NOT_FOUND'] + (['E_QUERY_STALE'] if verb == 'query' else []),
    )(planner)


@command('sales-tax liability', scope='company', required_role='member', capability='reports',
    description=_LIABILITY, input_model=SalesTaxLiabilityInput, output_model=SalesTaxLiabilityOutput,
    error_codes=['E_QUERY_STALE', 'E_VALUE_RANGE', 'E_RECORD_NOT_FOUND', 'E_TAX_BASIS_UNSUPPORTED'])
def plan_sales_tax_liability(inp, ctx, s):
    return Plan(preview=sales_tax_liability(inp, s, principal_id=ctx.on_behalf_of))


sales_tax_pay = _write('sales-tax pay', 'pay', SalesTaxPayInput, SalesTaxPaymentWriteOutput)
sales_tax_payment_void = _write('sales-tax payment void', 'void', SalesTaxPaymentVoidInput,
                                SalesTaxPaymentWriteOutput)
sales_tax_payment_show = _read('show', SalesTaxPaymentShowInput, SalesTaxPaymentOutput)
sales_tax_payment_query = _read('query', SalesTaxPaymentQueryInput, SalesTaxPaymentPageOutput)

_ADJUST = (
    'Adjust sales tax due: increase or reduce what one tax agency is owed, against an adjustment'
    ' account -- the anchor\'s Adjust Sales Tax Due. `direction` increase credits the sales tax'
    ' payable account (the agency is owed more: a penalty or interest it charged, a rounding'
    ' difference, a balance brought in from earlier books) and debits `adjustment_account`;'
    ' reduce debits the liability (a timely-filing discount the agency allows, an over-recorded'
    ' amount) and credits `adjustment_account`. The adjustment is attributed to the agency, so'
    ' `sales-tax liability` shows it in that agency\'s `adjusted` column and `sales-tax pay` can'
    ' pay it. `adjustment_account` is usually an income or expense account; it cannot be the'
    ' sales tax payable account itself, a bank or credit card (paying the agency is'
    ' `sales-tax pay`), accounts receivable or payable, or the inventory asset. `number` is the'
    ' entry number and defaults to the next in the adjustment series. A company on the'
    ' payment_receipt sales tax basis is refused, as `sales-tax pay` is.'
)

ADJUSTMENT_ERRORS = {
    'adjust': ['E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_VALIDATION', 'E_VALUE_RANGE',
               'E_AMOUNT_PRECISION', 'E_PERIOD_CLOSED', 'E_DUPLICATE_NUMBER',
               'E_TAX_BASIS_UNSUPPORTED'],
    'void': ['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_VALIDATION', 'E_REASON_REQUIRED',
             'E_PERIOD_CLOSED', 'E_APPLICATION_INACTIVE'],
}

ADJUSTMENT_DESCRIPTIONS = {
    'adjust': _ADJUST,
    'void': ('Void a sales tax adjustment with a required reason. Its accounting is reversed at its'
             ' own date, the agency goes back to being owed what it was owed before, its number'
             ' stays occupied and its history stays readable. There is no correction: void it and'
             ' adjust again.'),
    'show': ('Show a sales tax adjustment: the agency, whether it increased or reduced what the'
             ' agency is owed, the adjustment account, the amount, what the agency was owed on the'
             ' adjustment date before and after it, and its posting batches.'),
    'query': ('Page sales tax adjustments in accounting-date and stable-id order, oldest first or'
              ' newest first, with exact agency, date, adjustment-account, number and status'
              ' filters; restart on company audit changes.'),
}


def _adjustment_write(name, verb, model):
    def planner(inp, ctx, s):
        return sales_tax_adjustments.prepare(s, ctx, inp, verb)

    cmd = command(
        name, scope='company', description=ADJUSTMENT_DESCRIPTIONS[verb],
        input_model=model, output_model=SalesTaxAdjustmentWriteOutput, writes={'company'},
        required_role='standard', capability='ledger.post', accepts_idempotency_key=True,
        positional=[] if verb == 'adjust' else ['adjustment'],
        version_source=None if verb == 'adjust' else ('sales-tax adjustment show', 'adjustment', 'version'),
        error_codes=ADJUSTMENT_ERRORS[verb])(planner)
    cmd.ledger = True
    cmd.applier(sales_tax_adjustments.apply)
    return cmd


def _adjustment_read(verb, model, output_model):
    def planner(inp, ctx, s):
        return Plan(sales_tax_adjustments.show(s, inp) if verb == 'show'
                    else sales_tax_adjustments.page(s, ctx, inp))

    return command(
        'sales-tax adjustment ' + verb, scope='company', description=ADJUSTMENT_DESCRIPTIONS[verb],
        input_model=model, output_model=output_model,
        required_role='member', capability='ledger.read',
        positional=['adjustment'] if verb == 'show' else [],
        error_codes=['E_RECORD_NOT_FOUND'] + (['E_QUERY_STALE'] if verb == 'query' else []),
    )(planner)


sales_tax_adjust = _adjustment_write('sales-tax adjust', 'adjust', SalesTaxAdjustInput)
sales_tax_adjustment_void = _adjustment_write('sales-tax adjustment void', 'void', SalesTaxAdjustmentVoidInput)
sales_tax_adjustment_show = _adjustment_read('show', SalesTaxAdjustmentShowInput, SalesTaxAdjustmentOutput)
sales_tax_adjustment_query = _adjustment_read('query', SalesTaxAdjustmentQueryInput, SalesTaxAdjustmentPageOutput)

SALES_TAX_COMMANDS = [plan_sales_tax_liability, sales_tax_pay, sales_tax_payment_show,
                      sales_tax_payment_query, sales_tax_payment_void, sales_tax_adjust,
                      sales_tax_adjustment_show, sales_tax_adjustment_query, sales_tax_adjustment_void]
