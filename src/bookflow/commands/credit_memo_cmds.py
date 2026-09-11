"""Write a customer a credit, and read what it is worth.

A credit memo is an invoice read backwards, verb for verb: what you learned entering one you
already know here. The accounting lives in ``company/credits.py``.
"""
from bookflow.core.registry import Plan, command
from bookflow.company import credits
from bookflow.company.credit_models import (
    CreditMemoHistoryInput, CreditMemoHistoryOutput, CreditMemoOutput, CreditMemoPageOutput,
    CreditMemoPostInput, CreditMemoQueryInput, CreditMemoShowInput, CreditMemoVoidInput,
    CreditMemoWriteOutput,
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
    'void': ('Void a credit memo with a required reason. Its accounting is reversed at its own'
             ' date, so the income and the sales tax it took back go back where they were and'
             ' the customer owes the full amount again; every source invoice quantity it'
             ' claimed is released and can be returned again; its number stays occupied and'
             ' every revision stays readable. A credit still applied to an invoice is refused'
             ' with `E_HAS_APPLICATIONS` and one a refund has paid out with `E_HAS_REFUND`:'
             ' take the credit back off the invoice, or void the refund, first.'),
    'query': ('Page credit memos in accounting-date and stable-id order, oldest first or newest'
              ' first, with exact customer, receivable-account, date, number, origin and status'
              ' filters. Each row carries what the credit is still worth -- its total less what'
              ' has been applied to invoices and less what has been refunded -- and'
              ' `available_only` keeps just the credits still worth something, which is how you'
              " find what a customer has in hand. Restart on company audit changes."),
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

def _void(inp, ctx, s):
    return credits.prepare_void(s, ctx, inp)


def _query(inp, ctx, s):
    return Plan(credits.query_page(s, ctx, inp))


credit_memo_void = command(
    'credit-memo void', scope='company', description=DESCRIPTIONS['void'],
    input_model=CreditMemoVoidInput, output_model=CreditMemoWriteOutput, writes={'company'},
    required_role='standard', capability='ledger.post', accepts_idempotency_key=True,
    positional=['credit_memo'], version_source=('credit-memo show', 'credit_memo', 'version'),
    error_codes=['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_VALIDATION', 'E_REASON_REQUIRED',
                 'E_PERIOD_CLOSED', 'E_HAS_APPLICATIONS', 'E_HAS_REFUND'])(_void)
credit_memo_void.ledger = True
credit_memo_void.applier(credits.apply)

credit_memo_query = command(
    'credit-memo query', scope='company', description=DESCRIPTIONS['query'],
    input_model=CreditMemoQueryInput, output_model=CreditMemoPageOutput,
    required_role='member', capability='ledger.read', positional=[],
    error_codes=['E_RECORD_NOT_FOUND', 'E_QUERY_STALE'])(_query)

credit_memo_history = command(
    'credit-memo history', scope='company', description=DESCRIPTIONS['history'],
    input_model=CreditMemoHistoryInput, output_model=CreditMemoHistoryOutput,
    required_role='member', capability='ledger.read', positional=['credit_memo'],
    error_codes=['E_RECORD_NOT_FOUND', 'E_QUERY_STALE'])(_history)

CREDIT_MEMO_COMMANDS = [credit_memo_post, credit_memo_show, credit_memo_query,
                        credit_memo_history, credit_memo_void]
