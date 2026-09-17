"""Write a customer a credit, and read what it is worth.

A credit memo is an invoice read backwards, verb for verb: what you learned entering one you
already know here. The accounting lives in ``company/credits.py``.
"""
from bookflow.core.registry import Plan, command
from bookflow.company import credits
from bookflow.company.credit_models import (
    CreditMemoHistoryInput, CreditMemoHistoryOutput, CreditMemoOutput, CreditMemoPageOutput,
    CreditMemoPostInput, CreditMemoQueryInput, CreditMemoShowInput, CreditMemoVoidInput,
    CreditMemoWriteOutput, CreditMemoUpdateInput,
)

_LINES = (
    ' Each row of `lines` is either an item you name -- `item`, `quantity`, and a price exactly'
    ' as an invoice line takes one -- or a return: `source_invoice` and `source_line` naming a'
    ' line of a posted invoice, with `quantity` saying how much of it comes back. A returned row'
    ' is priced entirely from what that invoice captured, so it takes no price, no amount and no'
    ' tax code, and changing the item price or the tax rate afterwards moves none of its cents.'
    ' Return the same line again and you get the next units of it; ask for more than that line'
    ' has left to give back and the whole credit is refused, so nothing is written.'
    ' One credit memo is all returns or all named items: the tax calculation rounds across a'
    ' whole document, so a document holding both would carry a tax total that is neither.'
)
_STOCK = (
    ' Returning a line whose item carries stock puts the goods back with the money, in this one'
    ' document: the quantity goes back on hand and its cost comes out of cost of goods sold and'
    ' back into the inventory asset, at what that invoice line actually took out of stock --'
    ' not at the price on the credit and not at what the item is worth today. Return half a'
    ' line and half that cost comes back; return the rest later and the two halves add up to'
    ' exactly what went out. Voiding the credit takes the quantity and the cost out again, and'
    ' is refused, naming the date, if the goods have already been sold on.'
)
_LIMITS = (
    ' Not supported in this release, and refused rather than approximated: a price allowance'
    ' against a source line without returning any of it; a line that names a stock-carrying'
    ' item instead of returning the invoice line it was sold on, because a price is not a cost'
    ' and nothing here guesses one -- name `source_invoice` and `source_line` to return it, or'
    ' credit the money with a service or non-stock item and bring the quantity back with'
    ' `inventory adjust`, which takes the value you say it is worth; a credit from one customer'
    ' settling another customer or job; cash-basis tax filing treatment'
    ' today; and printing, which the document print work owns.'
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
             + _NUMBERING + _LINES + _STOCK + _LIMITS),
    'show': ('Show a credit memo: its current or a selected immutable revision, captured customer,'
             ' receivable account and custom facts, its credited lines with the tax components and'
             ' the source invoice quantities they claim, its posting batches, and what the credit'
             ' is worth now after anything applied from it.'),
    'history': ('Page immutable credit memo revisions in revision-number order with the current'
                ' header and version and their posting batches; restart on company audit changes.'),
    'void': ('Void a credit memo with a required reason. Its accounting is reversed at its own'
             ' date, so the income and the sales tax it took back go back where they were and'
             ' the customer owes the full amount again; any stock it brought back goes out'
             ' again at exactly what it came in at, leaving the quantity and the cost where the'
             ' sale left them; every source invoice quantity it claimed is released and can be'
             ' returned again; its number stays occupied and every revision stays readable. A'
             ' credit still applied to an invoice is refused with `E_HAS_APPLICATIONS` and one a'
             ' refund has paid out with `E_HAS_REFUND`: take the credit back off the invoice, or'
             ' void the refund, first. A void that would take returned stock back out of an'
             ' empty shelf is refused naming the date, and writes nothing.'),
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

def _delete():
    from bookflow.company import credit_deletions as deletion
    from bookflow.company.credit_deletion_models import CreditMemoDeleteInput, CreditMemoDeleteOutput
    from bookflow.core.deletion_families import capability

    def planner(inp, ctx, s):
        return deletion.prepare(s, ctx, inp)

    def recover(inp, ctx, s):
        return deletion.recover(inp, ctx, s)

    cmd = command(
        'credit-memo delete', scope='company',
        description='Delete this credit memo with a required reason and exact expected_version.'
                    ' Cancel the income, the sales tax and the receivable it took back at their'
                    ' original dates, and release every source invoice quantity it claimed so'
                    ' that quantity can be returned again; retain immutable history and its'
                    ' number. Requires the explicit family Delete grant and ledger.read,'
                    ' independently of ledger.post. A credit still applied to an invoice refuses'
                    ' atomically with `E_HAS_APPLICATIONS` naming those invoices, and one a'
                    ' customer refund was paid out of with `E_HAS_REFUND` naming the refunds:'
                    ' take the credit back off the invoice, or void the refund, first. A closed'
                    ' period refuses.',
        input_model=CreditMemoDeleteInput, output_model=CreditMemoDeleteOutput, writes={'company'},
        required_role='standard', capability=capability('credit_memo'), explicit_grant_only=True,
        accepts_idempotency_key=True, positional=['credit_memo'],
        version_source=('credit-memo show', 'credit_memo', 'version'),
        error_codes=['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_VALIDATION', 'E_REASON_REQUIRED',
                     'E_PERIOD_CLOSED', 'E_RECONCILIATION_DEPENDENCY', 'E_IDEMPOTENCY_MISMATCH',
                     'E_HAS_APPLICATIONS', 'E_HAS_REFUND'])(planner)
    cmd.resource_requirements = (('ledger.read', 'member'),)
    cmd.ledger = True
    cmd.permanent_recovery = recover
    cmd.applier(deletion.apply)
    return cmd


credit_memo_delete = _delete()

CREDIT_MEMO_COMMANDS = [credit_memo_post, credit_memo_show, credit_memo_query,
                        credit_memo_history, credit_memo_void, credit_memo_delete]


def _update(inp, ctx, s):
    from bookflow.company.credit_corrections import prepare_update
    return prepare_update(s, ctx, inp)


credit_memo_update = command(
    'credit-memo update', scope='company',
    description=('Correct a credit memo with an immutable revision and exact reversal/replacement'
                 ' postings. Omitted lines retain their captured amounts and source intervals; supplied'
                 ' lines replace the grid, retaining named line_id values. Return claims are released'
                 ' and retaken atomically. Existing applications and refunds are retained with'
                 ' replacement attribution when their combined use fits the corrected total. A used'
                 ' credit requires a reason, unchanged customer/receivable/currency, and a date no later'
                 ' than its earliest use; all affected dates must be open. Related invoice and refund'
                 ' versions advance. If a use spans replacement lines, its application is cancelled'
                 ' and replaced by one application per line; invoice settlement exposes the current'
                 ' application IDs for later unapply. An unused standalone credit may change customer'
                 ' or receivable account; linked returns keep exact source ownership.'
                 ' Stock follows the corrected grid: whatever the previous revision brought back'
                 ' is taken out again and the corrected grid brings back what it now returns, at'
                 ' the cost its source invoice lines took out -- including a correction that'
                 ' leaves the grid out and only moves the date, which moves the goods to that'
                 ' date too. A correction that would post a line *naming* a stock-carrying item'
                 ' is refused, whether you supply it or it is retained, because a price is not a'
                 ' cost: return the invoice line instead, or put the credit on a non-stock item.'
                 ' A correction that would leave stock below zero on any date is refused naming'
                 ' that date, and writes nothing. Preview with'
                 ' expected_version, then save with expected_facts_fingerprint and an idempotency key'
                 ' reused for retries.'),
    input_model=CreditMemoUpdateInput, output_model=CreditMemoWriteOutput, writes={'company'},
    required_role='standard', capability='ledger.post', accepts_idempotency_key=True,
    positional=['credit_memo'], clearable=True,
    version_source=('credit-memo show', 'credit_memo', 'version'),
    error_codes=POST_ERRORS + ['E_VERSION_CONFLICT', 'E_REASON_REQUIRED',
        'E_APPLICATION_INCOMPATIBLE', 'E_APPLIED_EXCEEDS_TOTAL', 'E_HAS_APPLICATIONS'])(_update)
credit_memo_update.ledger = True
credit_memo_update.applier(credits.apply)
CREDIT_MEMO_COMMANDS.append(credit_memo_update)
