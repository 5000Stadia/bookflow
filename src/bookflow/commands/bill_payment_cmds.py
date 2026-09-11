"""Pay open bills, read back what was paid, and move or take back what a payment answered.

``bill pay`` is the verb; ``bill payment`` is the noun it writes. That split is deliberate --
you pay bills, and what you get is payments, one per payee.

``apply`` and ``unapply`` are the two directions of the same fact, and neither moves money:
which payables a payment answers is a decision that can be taken back and made again, so a
check pointed at the wrong bill is re-pointed rather than voided and rewritten.
"""
from bookflow.core.registry import Plan, command
from bookflow.company import bill_payments
from bookflow.company.bill_payment_models import (
    BillPayInput, BillPayOutput, BillPaymentApplyInput, BillPaymentHistoryInput,
    BillPaymentHistoryOutput, BillPaymentOutput, BillPaymentPageOutput, BillPaymentQueryInput,
    BillPaymentShowInput, BillPaymentUnapplyInput, BillPaymentVoidInput, BillPaymentWriteOutput,
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
    'apply': ['E_RECORD_NOT_FOUND', 'E_VALIDATION', 'E_AMOUNT_PRECISION', 'E_VALUE_RANGE',
              'E_PERIOD_CLOSED', 'E_VERSION_CONFLICT', 'E_APPLICATION_CAPACITY',
              'E_APPLICATION_INCOMPATIBLE', 'E_APPLICATION_INACTIVE'],
    'unapply': ['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_APPLICATION_INACTIVE', 'E_VALIDATION',
                'E_PERIOD_CLOSED'],
    'void': ['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_VALIDATION', 'E_REASON_REQUIRED',
             'E_PERIOD_CLOSED', 'E_APPLICATION_INACTIVE', 'E_HAS_APPLICATIONS'],
}

_APPLY = (
    'Attach what a bill payment still has free to one or more open bills of the same vendor.'
    ' Nothing is posted and no money moves: the cash left when the payment posted, so this only'
    ' decides which payables it answers, and the bills fall by exactly what is attached to them.'
    " Free capacity is the payment's amount less what it currently answers -- money never"
    ' applied, or freed by `bill payment unapply` -- so a payment pointed at the wrong bill is'
    ' re-pointed here rather than voided and written again. Each named bill takes what you name'
    ' for it, or everything still open on it when you name nothing; applying less than what is'
    ' free leaves the rest free. The vendor, the payable account and the currency must match the'
    ' payment exactly. `date` is the settlement date and defaults to the payment date; it may be'
    ' later but never earlier than the payment or than a bill it settles, and never on or before'
    ' the closing date.'
)

DESCRIPTIONS = {
    'pay': _PAY,
    'apply': _APPLY,
    'unapply': ('Take a payment back off the bills it settled, without moving any money. The'
                ' bills go back to open for what was applied and the bank is untouched, which'
                ' leaves the payment standing as an unapplied debit against the vendor. Name'
                ' `bills` to detach only those; leave it out to detach everything still applied.'
                ' Each detachment is dated at the settlement date it takes back, so an'
                ' application dated on or before the closing date cannot be undone here.'),
    'void': ('Void a bill payment with a required reason. Its accounting is reversed at its own'
             ' date, its number stays occupied and its history stays readable. Anything it still'
             ' settles must be unapplied first, so that voiding never silently reopens a bill.'),
    'show': ('Show a bill payment: who was paid, out of which account, by what method and check'
             ' number, its posting batches, the bills it settled and what it still has attached.'),
    'query': ('Page bill payments in accounting-date and stable-id order, oldest first or newest'
              ' first, with exact vendor, date, funding-account, method, number, check-number and'
              ' status filters, and a `bill` filter that answers what paid a given bill; restart'
              ' on company audit changes.'),
    'history': ('Page immutable bill-payment revisions in revision-number order with the current'
                ' header and version, every posting batch each revision minted -- the original'
                ' and the exact reversal a void wrote at its own date -- and every settlement'
                ' edge against the capacity it created, applies and their inverses alike, so a'
                ' payment re-pointed at another bill reads as the correction it is; restart on'
                ' company audit changes.'),
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
        if verb == 'show':
            return Plan(bill_payments.show(s, inp))
        return Plan(bill_payments.page(s, ctx, inp, history=verb == 'history'))

    return command(
        'bill payment ' + verb, scope='company', description=DESCRIPTIONS[verb],
        input_model=model, output_model=output_model,
        required_role='member', capability='ledger.read',
        positional=[] if verb == 'query' else ['payment'],
        error_codes=['E_RECORD_NOT_FOUND'] + (['E_QUERY_STALE'] if verb != 'show' else []),
    )(planner)


bill_pay = _write('bill pay', 'pay', BillPayInput, BillPayOutput)
bill_payment_apply = _write('bill payment apply', 'apply', BillPaymentApplyInput, BillPaymentWriteOutput)
bill_payment_unapply = _write('bill payment unapply', 'unapply', BillPaymentUnapplyInput, BillPaymentWriteOutput)
bill_payment_void = _write('bill payment void', 'void', BillPaymentVoidInput, BillPaymentWriteOutput)
bill_payment_show = _read('show', BillPaymentShowInput, BillPaymentOutput)
bill_payment_query = _read('query', BillPaymentQueryInput, BillPaymentPageOutput)
bill_payment_history = _read('history', BillPaymentHistoryInput, BillPaymentHistoryOutput)

BILL_PAYMENT_COMMANDS = [bill_pay, bill_payment_show, bill_payment_query, bill_payment_history,
                         bill_payment_apply, bill_payment_unapply, bill_payment_void]
