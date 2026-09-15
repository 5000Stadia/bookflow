"""Pay a customer back what a credit memo says they are owed, and correct it when it is wrong.

The third of the three things that can be done with an available credit -- retain it, apply it
to an invoice, or refund it -- and the only one that moves cash. The accounting lives in
``company/refunds.py``.
"""
from bookflow.core.registry import Plan, command
from bookflow.company import refunds
from bookflow.company.refund_models import (
    CustomerRefundOutput, CustomerRefundPageOutput, CustomerRefundPostInput,
    CustomerRefundQueryInput, CustomerRefundShowInput, CustomerRefundUpdateInput,
    CustomerRefundVoidInput, CustomerRefundWriteOutput,
)

_POST = (
    'Pay a customer back. The refund debits Accounts Receivable and credits the bank account'
    ' the money left, and posts nothing else: the credit memo already took the income and the'
    ' sales tax back down, so a refund that touched either again would reverse the same sale'
    ' twice. Name the credit memos being paid out in `sources`; leave an amount out and the'
    ' whole of that credit is refunded. A credit that has been refunded cannot then be applied'
    ' to an invoice, and one that has been applied cannot be refunded beyond what is left --'
    ' either way the refund is refused and nothing is written. One refund pays back one customer'
    ' on one'
    ' receivable account in one currency, taken from the credits themselves; `customer` is an'
    ' optional guard rather than a choice. `check_number` is the number on the paper check and'
    ' is accepted only when the method is a check.'
)

ERRORS = {
    'post': ['E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_VALIDATION', 'E_VALUE_RANGE',
             'E_AMOUNT_PRECISION', 'E_PERIOD_CLOSED', 'E_DUPLICATE_NUMBER',
             'E_APPLICATION_INACTIVE', 'E_APPLICATION_INCOMPATIBLE', 'E_CREDIT_UNAVAILABLE'],
    'void': ['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_VALIDATION', 'E_REASON_REQUIRED',
             'E_PERIOD_CLOSED', 'E_APPLICATION_INACTIVE'],
}
# A correction can raise everything posting one can, plus what reading the saved one can:
# a stale version, no reason, and the bank reconciliation that will not let it move.
ERRORS['update'] = ERRORS['post'] + ['E_VERSION_CONFLICT', 'E_REASON_REQUIRED',
                                     'E_RECONCILIATION_DEPENDENCY']

DESCRIPTIONS = {
    'post': _POST,
    'void': ('Void a customer refund with a required reason. Its accounting is reversed at its'
             ' own date, every credit it paid out is worth again exactly what it was worth'
             ' before, its number stays occupied and its history stays readable. A refund that'
             ' was merely typed wrong is corrected with `customer-refund update` instead, which'
             ' keeps one document where one payment happened.'),
    'update': ('Correct a customer refund with a required reason and another immutable revision.'
               ' What you supply replaces what was captured and what you leave out stands, so a'
               ' wrong date, memo, reference, check number, bank account or payment method is'
               ' corrected on its own; supply `sources` to change which credit memos are paid'
               ' out and how much of each, and the whole list is replaced. The superseded'
               " revision's accounting is reversed at its own date and a replacement is posted"
               ' at the corrected date -- both periods must be open -- and every credit the old'
               ' revision consumed is released and the corrected amounts taken again in the same'
               ' write, so no credit is ever spent twice in between and what a credit is worth'
               ' is never wrong. The number is kept unless you give a new one. A correction'
               ' cannot change who is paid: the customer, receivable account and currency come'
               ' from the credits and must stay what they were, and `customer` remains a guard'
               ' rather than a choice. A voided refund cannot be corrected, and neither can one'
               ' a finished bank reconciliation already holds -- undo that reconciliation first.'
               ' An empty patch writes nothing and reports `changed` false. Pass'
               ' `expected_version` to refuse a write over somebody else, and reuse one'
               ' idempotency key to retry safely.'),
    'show': ('Show a customer refund: who was paid back, out of which bank account, by what'
             ' method and check number, which credit memos it paid out and how much of each,'
             ' and its posting batches. Give `revision_number` to read a superseded revision'
             ' rather than what the refund says now.'),
    'query': ('Page customer refunds in accounting-date and stable-id order, oldest first or'
              ' newest first, with exact customer, date, funding-account, method, number,'
              ' check-number and status filters; restart on company audit changes.'),
}


def _write(name, verb, model):
    def planner(inp, ctx, s):
        return refunds.prepare(s, ctx, inp, verb)

    cmd = command(
        name, scope='company', description=DESCRIPTIONS[verb],
        input_model=model, output_model=CustomerRefundWriteOutput, writes={'company'},
        required_role='standard', capability='ledger.post', accepts_idempotency_key=True,
        positional=[] if verb == 'post' else ['refund'], clearable=verb == 'update',
        version_source=None if verb == 'post' else ('customer-refund show', 'refund', 'version'),
        error_codes=ERRORS[verb])(planner)
    cmd.ledger = True
    cmd.applier(refunds.apply)
    return cmd


def _read(verb, model, output_model):
    def planner(inp, ctx, s):
        return Plan(refunds.show(s, inp) if verb == 'show' else refunds.page(s, ctx, inp))

    return command(
        'customer-refund ' + verb, scope='company', description=DESCRIPTIONS[verb],
        input_model=model, output_model=output_model,
        required_role='member', capability='ledger.read',
        positional=['refund'] if verb == 'show' else [],
        error_codes=['E_RECORD_NOT_FOUND'] + (['E_QUERY_STALE'] if verb == 'query' else []),
    )(planner)


customer_refund_post = _write('customer-refund post', 'post', CustomerRefundPostInput)
customer_refund_update = _write('customer-refund update', 'update', CustomerRefundUpdateInput)
customer_refund_void = _write('customer-refund void', 'void', CustomerRefundVoidInput)
customer_refund_show = _read('show', CustomerRefundShowInput, CustomerRefundOutput)
customer_refund_query = _read('query', CustomerRefundQueryInput, CustomerRefundPageOutput)

REFUND_COMMANDS = [customer_refund_post, customer_refund_show, customer_refund_query,
                   customer_refund_update, customer_refund_void]
