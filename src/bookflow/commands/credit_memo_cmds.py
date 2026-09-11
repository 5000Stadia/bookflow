"""Write a customer a credit, and read what it is worth.

A credit memo is an invoice read backwards, verb for verb: what you learned entering one you
already know here. The accounting lives in ``company/credits.py``.
"""
from bookflow.core.registry import Plan, command
from bookflow.company import credits
from bookflow.company.credit_models import (
    CreditMemoHistoryInput, CreditMemoHistoryOutput, CreditMemoOutput, CreditMemoPostInput,
    CreditMemoShowInput, CreditMemoWriteOutput,
)

_LINES = (
    ' Each row of `lines` is either an item you name -- `item`, `quantity`, and a price exactly'
    ' as an invoice line takes one -- or a return: `source_invoice` and `source_line` naming a'
    ' line of a posted invoice, with `quantity` saying how much of it comes back. A returned row'
    ' is priced entirely from what that invoice captured, so it takes no price, no amount and no'
    ' tax code, and changing the item price or the tax rate afterwards moves none of its cents.'
    ' Return the same line again and you get the next units of it; return more than is left and'
    ' the credit is refused whole with `E_RETURN_EXHAUSTED`.'
    ' One credit memo is all returns or all named items: the tax calculation rounds across a'
    ' whole document, so a document holding both would carry a tax total that is neither.'
)
_LIMITS = (
    ' Not supported in this release, and refused rather than approximated: a price allowance'
    ' against a source line without returning any of it; a stocked return, which moves no'
    ' inventory and restores no cost because no stocked sale exists to return against; a credit'
    ' from one customer settling another customer or job; cash-basis treatment, since every'
    ' report is accrual today; and printing, which the document print work owns.'
)
_NUMBERING = (
    ' A credit memo takes the next number from the invoice series, so invoices and credits read'
    ' as one unbroken run of numbers to a customer; give `number` to set one yourself, and a'
    ' number already used by an invoice or another credit is refused.'
)

# Each verb declares only what it can actually raise: a new credit has no version to be stale,
# nothing applied against it, and no reason to give.
POST_ERRORS = ['E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_VALIDATION', 'E_VALUE_RANGE',
               'E_AMOUNT_PRECISION', 'E_PERIOD_CLOSED', 'E_DUPLICATE_NUMBER', 'E_PREVIEW_STALE',
               'E_RETURN_EXHAUSTED', 'E_SOURCE_CORRECTION_CONFLICT']

DESCRIPTIONS = {
    'post': ('Credit a customer. Each line debits the income account the sale used and the tax'
             ' liabilities it charged, and Accounts Receivable is credited the total, so the'
             ' customer owes that much less. Saving it applies nothing to any invoice: the credit'
             ' stands available until it is applied or refunded.'
             + _NUMBERING + _LINES + _LIMITS),
    'show': ('Show a credit memo: its current or a selected immutable revision, captured customer,'
             ' receivable account and custom facts, its credited lines with the tax components and'
             ' the source invoice quantities they claim, its posting batches, and what the credit'
             ' is worth now after anything applied from it.'),
    'history': ('Page immutable credit memo revisions in revision-number order with the current'
                ' header and version and their posting batches; restart on company audit changes.'),
}


def _planner(inp, ctx, s):
    return credits.prepare(s, ctx, inp)


credit_memo_post = command(
    'credit-memo post', scope='company', description=DESCRIPTIONS['post'],
    input_model=CreditMemoPostInput, output_model=CreditMemoWriteOutput, writes={'company'},
    required_role='standard', capability='ledger.post', accepts_idempotency_key=True,
    positional=[], error_codes=POST_ERRORS)(_planner)
credit_memo_post.ledger = True
credit_memo_post.applier(credits.apply)


def _show(inp, ctx, s):
    return Plan(credits.show(s, inp))


def _history(inp, ctx, s):
    return Plan(credits.page(s, ctx, inp))


credit_memo_show = command(
    'credit-memo show', scope='company', description=DESCRIPTIONS['show'],
    input_model=CreditMemoShowInput, output_model=CreditMemoOutput,
    required_role='member', capability='ledger.read', positional=['credit_memo'],
    error_codes=['E_RECORD_NOT_FOUND'])(_show)

credit_memo_history = command(
    'credit-memo history', scope='company', description=DESCRIPTIONS['history'],
    input_model=CreditMemoHistoryInput, output_model=CreditMemoHistoryOutput,
    required_role='member', capability='ledger.read', positional=['credit_memo'],
    error_codes=['E_RECORD_NOT_FOUND', 'E_QUERY_STALE'])(_history)

CREDIT_MEMO_COMMANDS = [credit_memo_post, credit_memo_show, credit_memo_history]
