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
from bookflow.company import sales_tax_payments
from bookflow.company.sales_tax_payment_models import (
    SalesTaxPayInput, SalesTaxPaymentPageOutput, SalesTaxPaymentQueryInput,
    SalesTaxPaymentShowInput, SalesTaxPaymentOutput, SalesTaxPaymentVoidInput,
    SalesTaxPaymentWriteOutput,
)
from bookflow.company.sales_tax_reports import (
    SalesTaxLiabilityInput, SalesTaxLiabilityOutput, sales_tax_liability,
)

_LIABILITY = (
    'What each sales tax agency is owed as of a date, from the tax the books recorded. Each'
    ' row is one agency with the tax charged on posted sales, the tax taken back by credit'
    ' memos, what has been remitted, and the balance still owed; an effect on the sales tax'
    ' payable account that names no agency -- a journal entry posted straight at it -- is its'
    ' own row rather than dropped, so the total is that account\'s balance on the balance'
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
    ' the remainder owed, and remitting more than is owed is refused rather than posted,'
    ' because paying an agency more than the books owe it is a sales tax adjustment and that'
    ' is a separate document. `check_number` is the number on the paper check and is accepted'
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

SALES_TAX_COMMANDS = [plan_sales_tax_liability, sales_tax_pay, sales_tax_payment_show,
                      sales_tax_payment_query, sales_tax_payment_void]
