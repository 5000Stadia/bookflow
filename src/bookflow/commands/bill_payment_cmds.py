"""Pay open bills, read back what was paid, and take a payment back.

``bill pay`` is the verb; ``bill payment`` is the noun it writes. That split is deliberate --
you pay bills, and what you get is payments, one per payee.
"""
from bookflow.core.registry import Plan, command
from bookflow.company import bill_payments
from bookflow.company.bill_payment_models import (
    BillPayInput, BillPayOutput, BillPaymentOutput, BillPaymentPageOutput,
    BillPaymentQueryInput, BillPaymentShowInput, BillPaymentUnapplyInput,
    BillPaymentVoidInput, BillPaymentWriteOutput,
)

_PAY = (
    'Pay open bills. Each selected bill is settled by what you name for it, or by everything'
    ' still open on it when you name nothing, and the payment debits Accounts Payable and'
    ' credits the account the money came from -- a bank account, which falls, or a credit card'
    ' account, which rises. Bills are grouped by vendor, payable account, currency, funding'
    ' account and method, and each group is one payment, so two vendors are never paid by one'
    ' check; `group_count` says how many the selection made. Paying less than what is open'
    ' leaves the remainder open. `check_number` is the number on the paper check and is'
    ' accepted only when the method is a check drawn on a bank account. Purchase discounts and'
    ' vendor credits are separate documents and are not entered here.'
)

ERRORS = {
    'pay': ['E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_VALIDATION', 'E_VALUE_RANGE',
            'E_AMOUNT_PRECISION', 'E_PERIOD_CLOSED', 'E_DUPLICATE_NUMBER', 'E_VERSION_CONFLICT',
            'E_APPLICATION_CAPACITY', 'E_APPLICATION_INCOMPATIBLE', 'E_APPLICATION_INACTIVE'],
    'unapply': ['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_APPLICATION_INACTIVE', 'E_VALIDATION'],
    'void': ['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_VALIDATION', 'E_REASON_REQUIRED',
             'E_PERIOD_CLOSED', 'E_APPLICATION_INACTIVE', 'E_HAS_APPLICATIONS'],
}

DESCRIPTIONS = {
    'pay': _PAY,
    'unapply': ('Take a payment back off the bills it settled, without moving any money. The'
                ' bills go back to open for what was applied and the bank is untouched, which'
                ' leaves the payment standing as an unapplied debit against the vendor. Name'
                ' `bills` to detach only those; leave it out to detach everything still applied.'),
    'void': ('Void a bill payment with a required reason. Its accounting is reversed at its own'
             ' date, its number stays occupied and its history stays readable. Anything it still'
             ' settles must be unapplied first, so that voiding never silently reopens a bill.'),
    'show': ('Show a bill payment: who was paid, out of which account, by what method and check'
             ' number, its posting batches, the bills it settled and what it still has attached.'),
    'query': ('Page bill payments in accounting-date and stable-id order, oldest first or newest'
              ' first, with exact vendor, date, funding-account, method, number, check-number and'
              ' status filters, and a `bill` filter that answers what paid a given bill; restart'
              ' on company audit changes.'),
}


def _write(name, verb, model, output_model):
    def planner(inp, ctx, s):
        return bill_payments.prepare(s, ctx, inp, verb)

    cmd = command(
        name, scope='company', description=DESCRIPTIONS[verb],
        input_model=model, output_model=output_model, writes={'company'},
        required_role='standard', capability='ledger.post', accepts_idempotency_key=True,
        positional=[] if verb == 'pay' else ['payment'],
        version_source=None if verb == 'pay' else ('bill payment show', 'payment', 'version'),
        error_codes=ERRORS[verb])(planner)
    cmd.ledger = True
    cmd.applier(bill_payments.apply)
    return cmd


def _read(verb, model, output_model):
    def planner(inp, ctx, s):
        return Plan(bill_payments.show(s, inp) if verb == 'show' else bill_payments.page(s, ctx, inp))

    return command(
        'bill payment ' + verb, scope='company', description=DESCRIPTIONS[verb],
        input_model=model, output_model=output_model,
        required_role='member', capability='ledger.read',
        positional=['payment'] if verb == 'show' else [],
        error_codes=['E_RECORD_NOT_FOUND'] + (['E_QUERY_STALE'] if verb == 'query' else []),
    )(planner)


bill_pay = _write('bill pay', 'pay', BillPayInput, BillPayOutput)
bill_payment_unapply = _write('bill payment unapply', 'unapply', BillPaymentUnapplyInput, BillPaymentWriteOutput)
bill_payment_void = _write('bill payment void', 'void', BillPaymentVoidInput, BillPaymentWriteOutput)
bill_payment_show = _read('show', BillPaymentShowInput, BillPaymentOutput)
bill_payment_query = _read('query', BillPaymentQueryInput, BillPaymentPageOutput)

BILL_PAYMENT_COMMANDS = [bill_pay, bill_payment_show, bill_payment_query,
                         bill_payment_unapply, bill_payment_void]
