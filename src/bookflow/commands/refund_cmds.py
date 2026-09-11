"""Pay a customer back what a credit memo says they are owed.

The third of the three things that can be done with an available credit -- retain it, apply it
to an invoice, or refund it -- and the only one that moves cash. The accounting lives in
``company/refunds.py``.
"""
from bookflow.core.registry import Plan, command
from bookflow.company import refunds
from bookflow.company.refund_models import (
    CustomerRefundOutput, CustomerRefundPageOutput, CustomerRefundPostInput,
    CustomerRefundQueryInput, CustomerRefundShowInput, CustomerRefundVoidInput,
    CustomerRefundWriteOutput,
)

_POST = (
    'Pay a customer back. The refund debits Accounts Receivable and credits the bank account'
    ' the money left, and posts nothing else: the credit memo already took the income and the'
    ' sales tax back down, so a refund that touched either again would reverse the same sale'
    ' twice. Name the credit memos being paid out in `sources`; leave an amount out and the'
    ' whole of that credit is refunded. A credit that has been refunded cannot then be applied'
    ' to an invoice, and one that has been applied cannot be refunded beyond what is left --'
    ' either way the answer is `E_CREDIT_UNAVAILABLE`. One refund pays back one customer on one'
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

DESCRIPTIONS = {
    'post': _POST,
    'void': ('Void a customer refund with a required reason. Its accounting is reversed at its'
             ' own date, every credit it paid out is worth again exactly what it was worth'
             ' before, its number stays occupied and its history stays readable. There is no'
             ' correction: a refund is one amount to one customer on one date out of one'
             ' account, so a wrong one is voided and written again.'),
    'show': ('Show a customer refund: who was paid back, out of which bank account, by what'
             ' method and check number, which credit memos it paid out and how much of each,'
             ' and its posting batches.'),
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
        positional=[] if verb == 'post' else ['refund'],
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
customer_refund_void = _write('customer-refund void', 'void', CustomerRefundVoidInput)
customer_refund_show = _read('show', CustomerRefundShowInput, CustomerRefundOutput)
customer_refund_query = _read('query', CustomerRefundQueryInput, CustomerRefundPageOutput)

REFUND_COMMANDS = [customer_refund_post, customer_refund_show, customer_refund_query,
                   customer_refund_void]
