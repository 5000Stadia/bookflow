"""Put a credit memo against an invoice, and take it back off again.

The verbs sit under ``customer-credit`` rather than under ``credit-memo`` because what they
change is the invoice, not the credit: the credit memo is written once and stands, and
applying it is a separate dated decision that can be undone without touching the document.
The accounting lives in ``company/credit_settlement.py``.
"""
from bookflow.core.registry import command
from bookflow.company import credit_settlement
from bookflow.company.credit_settlement_models import (
    CreditApplyInput, CreditSettlementOutput, CreditUnapplyInput,
)

_SHARED = (
    ' Applying a credit posts nothing: the credit memo already took the receivable and the'
    ' income down, so the trial balance is the same before and after and only what the invoice'
    ' still owes changes. The credit and the invoice must name the same customer, the same'
    ' Accounts Receivable account and the same currency, and the settlement date must be on or'
    ' after the credit memo.'
)

ERRORS = {
    'apply': ['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_VALIDATION', 'E_VALUE_RANGE',
              'E_AMOUNT_PRECISION', 'E_PERIOD_CLOSED', 'E_PREVIEW_STALE',
              'E_APPLICATION_CAPACITY', 'E_APPLICATION_INCOMPATIBLE', 'E_APPLICATION_INACTIVE',
              'E_CREDIT_UNAVAILABLE'],
    'unapply': ['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_VALIDATION', 'E_PERIOD_CLOSED',
                'E_PREVIEW_STALE', 'E_APPLICATION_INACTIVE'],
}

DESCRIPTIONS = {
    'apply': ('Use a customer credit against one or more of that customer\'s posted invoices.'
              ' Give the invoice and the amount, or leave the amount out and the invoice takes'
              ' as much of the credit as it still owes, invoices being taken in the order you'
              ' list them. Applying more than the credit is worth, or more than an invoice'
              ' still owes, is refused whole.' + _SHARED),
    'unapply': ('Take a credit application back off an invoice. The invoice owes what it owed'
                ' before and the credit is worth what it was worth before. The reversal is'
                ' dated on the original application\'s own date, never today, so a settlement'
                ' is undone in the period where it happened -- which means that period has to'
                ' be open. Name the applications by the ids `customer-credit apply` returned or'
                ' `credit-memo show` lists.'),
}


def _write(verb, model):
    def planner(inp, ctx, s):
        return credit_settlement.prepare(s, ctx, inp, verb)

    cmd = command(
        'customer-credit ' + verb, scope='company', description=DESCRIPTIONS[verb],
        input_model=model, output_model=CreditSettlementOutput, writes={'company'},
        required_role='standard', capability='ledger.post', accepts_idempotency_key=True,
        positional=['credit_memo'],
        version_source=('credit-memo show', 'credit_memo', 'version'),
        error_codes=ERRORS[verb])(planner)
    cmd.ledger = True
    cmd.applier(credit_settlement.apply)
    return cmd


customer_credit_apply = _write('apply', CreditApplyInput)
customer_credit_unapply = _write('unapply', CreditUnapplyInput)

CREDIT_SETTLEMENT_COMMANDS = [customer_credit_apply, customer_credit_unapply]
