"""Pay a customer back what they are owed, and correct it when it is wrong.

The third of the three things that can be done with money a customer has standing on the
receivable -- retain it, apply it to an invoice, or refund it -- and the only one that moves
cash. That money comes from a credit memo or from a payment whose cash was more than the
invoices it settled, and one refund pays back either. The accounting lives in
``company/refunds.py``.
"""
from bookflow.core.registry import Plan, command
from bookflow.company import refunds, refund_history
from bookflow.company.refund_models import (
    CustomerRefundOutput, CustomerRefundPageOutput, CustomerRefundPostInput,
    CustomerRefundQueryInput, CustomerRefundShowInput, CustomerRefundUpdateInput,
    CustomerRefundVoidInput, CustomerRefundWriteOutput,
    CustomerRefundHistoryInput, CustomerRefundHistoryOutput,
)

_POST = (
    'Pay a customer back. The refund debits Accounts Receivable and credits the bank account'
    ' the money left, and posts nothing else: a credit memo already took the income and the'
    ' sales tax back down and an overpayment never recognised any, so a refund that touched'
    ' either would move revenue it is not entitled to move. Name what is being paid out in'
    ' `sources`: each source is a `credit_memo` or a `payment`, never both. A payment source'
    ' pays back the cash on that receipt which settled no invoice -- the 50.00 left standing'
    ' when a 150.00 cheque met a 100.00 invoice -- and that overage stops being available the'
    ' moment it is refunded. Leave an amount out and the whole of that source is refunded.'
    ' Money that has been refunded cannot then be applied to an invoice, and money that has'
    ' been applied cannot be refunded beyond what is left -- either way the refund is refused'
    ' and nothing is written. One refund pays back one customer on one receivable account in'
    ' one currency, taken from the sources themselves; `customer` is an optional guard rather'
    ' than a choice. `check_number` is the number on the paper check and is accepted only when'
    ' the method is a check.'
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
             ' own date, every credit memo and every payment overage it paid out is worth again'
             ' exactly what it was worth before, its number stays occupied and its history stays'
             ' readable. A refund that'
             ' was merely typed wrong is corrected with `customer-refund update` instead, which'
             ' keeps one document where one payment happened.'),
    'update': ('Correct a customer refund with a required reason and another immutable revision.'
               ' What you supply replaces what was captured and what you leave out stands, so a'
               ' wrong date, memo, reference, check number, bank account or payment method is'
               ' corrected on its own; supply `sources` to change which credit memos and'
               ' payments are paid out and how much of each, and the whole list is replaced.'
               ' The superseded'
               " revision's accounting is reversed at its own date and a replacement is posted"
               ' at the corrected date -- both periods must be open -- and every capacity the old'
               ' revision consumed is released and the corrected amounts taken again in the same'
               ' write, so nothing is ever spent twice in between and what a credit or an'
               ' overpayment is worth is never wrong. The number is kept unless you give a new'
               ' one. A correction'
               ' cannot change who is paid: the customer, receivable account and currency come'
               ' from the sources and must stay what they were, and `customer` remains a guard'
               ' rather than a choice. A voided refund cannot be corrected, and neither can one'
               ' a finished bank reconciliation already holds -- undo that reconciliation first.'
               ' An empty patch writes nothing and reports `changed` false. Pass'
               ' `expected_version` to refuse a write over somebody else, and reuse one'
               ' idempotency key to retry safely.'),
    'show': ('Show a customer refund: who was paid back, out of which bank account, by what'
             ' method and check number, which credit memos or payments it paid out and how much'
             ' of each,'
             ' and its posting batches. Give `revision_number` to read a superseded revision'
             ' rather than what the refund says now.'),
    'history': ('Page retained customer-refund revisions oldest first, with current document status.'
                ' Each revision includes captured facts, posting/reversal batches, source'
                ' consumptions/releases and audit attribution, including correction and void reasons.'
                ' A void adds effects to the last revision, not a new revision. Actor labels use'
                ' the company principal directory. Consumption source numbers are current labels;'
                ' the revision profile retains captured source facts. Writes invalidate cursors;'
                ' restart without cursor. Show still defaults to the current revision.'),
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
        if verb == 'history':
            return Plan(refund_history.history(s, ctx, inp))
        return Plan(refunds.show(s, inp) if verb == 'show' else refunds.page(s, ctx, inp))

    return command(
        'customer-refund ' + verb, scope='company', description=DESCRIPTIONS[verb],
        input_model=model, output_model=output_model,
        required_role='member', capability='ledger.read',
        positional=['refund'] if verb in ('show', 'history') else [],
        error_codes=['E_RECORD_NOT_FOUND'] + (['E_QUERY_STALE'] if verb in ('query', 'history') else []),
    )(planner)


customer_refund_post = _write('customer-refund post', 'post', CustomerRefundPostInput)
customer_refund_update = _write('customer-refund update', 'update', CustomerRefundUpdateInput)
customer_refund_void = _write('customer-refund void', 'void', CustomerRefundVoidInput)
customer_refund_show = _read('show', CustomerRefundShowInput, CustomerRefundOutput)
customer_refund_query = _read('query', CustomerRefundQueryInput, CustomerRefundPageOutput)

customer_refund_history = _read('history', CustomerRefundHistoryInput, CustomerRefundHistoryOutput)

REFUND_COMMANDS = [customer_refund_post, customer_refund_show, customer_refund_query,
                   customer_refund_update, customer_refund_void, customer_refund_history]
