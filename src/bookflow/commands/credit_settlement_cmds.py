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


# ------------------------------------------------------------------ moving credit between jobs (R148)

TRANSFER_DESCRIPTION = (
    'Move a customer\'s credit from one job to another (or between the parent customer and a job), as the '
    'anchor\'s guides do with a pair of entries through a clearing account, in one step. The source is charged '
    'the amount on an invoice that its own credit pays at once (its open credit memos and unapplied receipts, '
    'oldest first, or the one named in `credit_memo` or `payment`), and the target gets a credit memo for it. Both '
    'use the Credit Transfer item posting to the Credit Transfer Clearing account (set up on first use), which nets to '
    'zero; Accounts Receivable moves from one job to the other, so aging, balances and statements read each job right. '
    'Give `apply_to` to use the credit on the target\'s invoices at once (as `customer-credit apply`), or apply the '
    'credit memo later. A reason is required. Everything is written together or nothing is; dry run previews it and '
    'gives `facts_fingerprint`. Undo it with `customer-credit transfer-void`. Example, use the 120.00 credit on job '
    'Kitchen for job Bath: {"from_customer": "Acme:Kitchen", "to_customer": "Acme:Bath", "amount": "120.00", "date": '
    '"2026-07-15", "operation_key": "xfer-1", "apply_to": [{"invoice": "1088", "expected_version": 1}]}.')

TRANSFER_VOID_DESCRIPTION = (
    'Undo a credit transfer: the target invoices the moved credit paid are owed again, the source\'s credit comes '
    'off the transfer charge, and the charge invoice and the credit memo are voided at the transfer\'s own date (so '
    'its period must be open). Refused while any of the moved credit was refunded (void the refund first). A '
    'reason is required. Voiding a transfer already voided writes nothing.')


def _transfer():
    from bookflow.company import credit_transfers
    from bookflow.company.credit_transfer_models import (
        CreditTransferInput, CreditTransferOutput, CreditTransferVoidInput, CreditTransferVoidOutput,
    )

    def planner(inp, ctx, s):
        return credit_transfers.prepare(s, ctx, inp)

    cmd = command(
        'customer-credit transfer', scope='company', description=TRANSFER_DESCRIPTION,
        input_model=CreditTransferInput, output_model=CreditTransferOutput, writes={'company'},
        required_role='standard', capability='ledger.post', accepts_idempotency_key=True,
        error_codes=['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_VALIDATION', 'E_VALUE_RANGE',
                     'E_AMOUNT_PRECISION', 'E_REASON_REQUIRED', 'E_PERIOD_CLOSED', 'E_PREVIEW_STALE',
                     'E_INACTIVE_REFERENCE', 'E_APPLICATION_CAPACITY', 'E_APPLICATION_INCOMPATIBLE',
                     'E_APPLICATION_INACTIVE', 'E_CREDIT_UNAVAILABLE', 'E_PAYMENT_OPERATION_KEY_REUSED',
                     'E_DUPLICATE_NUMBER'])(planner)
    cmd.ledger = True
    cmd.applier(credit_transfers.apply)

    def void_planner(inp, ctx, s):
        return credit_transfers.prepare_void(s, ctx, inp)

    void = command(
        'customer-credit transfer-void', scope='company', description=TRANSFER_VOID_DESCRIPTION,
        input_model=CreditTransferVoidInput, output_model=CreditTransferVoidOutput, writes={'company'},
        required_role='standard', capability='ledger.post', accepts_idempotency_key=True, positional=['transfer'],
        error_codes=['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_VALIDATION', 'E_REASON_REQUIRED',
                     'E_PERIOD_CLOSED', 'E_PREVIEW_STALE', 'E_HAS_REFUND', 'E_HAS_APPLICATIONS',
                     'E_APPLICATION_INACTIVE', 'E_DEPOSIT_DEPENDENCY'])(void_planner)
    void.ledger = True
    void.applier(credit_transfers.apply_void)
    return cmd, void


customer_credit_transfer, customer_credit_transfer_void = _transfer()
CREDIT_SETTLEMENT_COMMANDS = [customer_credit_apply, customer_credit_unapply, customer_credit_transfer,
                              customer_credit_transfer_void]
