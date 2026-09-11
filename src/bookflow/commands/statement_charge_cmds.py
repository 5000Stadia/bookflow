"""Charge a customer's account directly, with no invoice, and read it back.

A statement charge is one line of an invoice with no invoice around it: the customer is
debited Accounts Receivable and the item's income account is credited, which is exactly
what an invoice line posts. What it is *for* is the bill that is assembled a few minutes
at a time -- a quarter hour here, a quarter hour there -- and read as one total at the
end of the month, on the statement ``report statement`` prints. The accounting is the
invoice's own, in ``company/sales.py``; only the entry surface is different.
"""
from bookflow.core.registry import Plan, command
from bookflow.company import sales
from bookflow.company.sales_models import (
    SalesQueryInput, StatementChargePostInput, StatementChargeShowInput, StatementChargeVoidInput,
)
from bookflow.company.sales_outputs import SalesOutput, SalesPageOutput, SalesWriteOutput

DOCUMENT = 'statement_charge'

DESCRIPTIONS = {
    'post': (
        'Charge one customer directly, with no invoice. Accounts Receivable is debited and'
        " the item's income account is credited, exactly as one invoice line posts, so the"
        ' charge is receivable from the moment it is entered: it ages on `report ar-aging`'
        ' by its own date and appears on `report statement` in date order among the'
        ' invoices, payments and credits. Enter it the way the charge reads: an `item`, a'
        ' `quantity` and a `rate`, or an `amount` instead of a rate when the charge is a'
        ' flat sum; omit both and the item’s own price is used. `description` is what the'
        ' charge was for and is what the customer reads on the statement unless a separate'
        ' `memo` is given. A job is its own customer, so bill a job by naming it as the'
        ' `customer`. `class_id` classes the charge. `ar_account` defaults to the only'
        ' active Accounts Receivable account when the company has exactly one. Statement'
        ' charges take their own number series; they do not share the invoice series.'
        ' A charge cannot be settled by `payment receive` or `payment apply` yet -- it'
        ' stands, ages and shows on the statement, and nothing can pay it off.'
    ),
    'void': (
        'Void a statement charge with a required reason. Its accounting is reversed at its'
        ' own date, so the customer’s balance, their aging and their statement all go back'
        ' to what they were; its number stays occupied and its history stays readable.'
    ),
    'show': (
        'Show a statement charge: its current or a selected immutable revision, the captured'
        ' customer, receivable account and item facts, and its posting batches.'
    ),
    'query': (
        'Page statement charges in accounting-date and stable-id order, oldest first or newest'
        ' first, with exact customer, date, status and number filters; restart on company audit'
        ' changes.'
    ),
}

ERRORS = {
    'post': ['E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_VALIDATION', 'E_VALUE_RANGE',
             'E_AMOUNT_PRECISION', 'E_PERIOD_CLOSED', 'E_DUPLICATE_NUMBER', 'E_PREVIEW_STALE'],
    'void': ['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_VALIDATION', 'E_REASON_REQUIRED',
             'E_PERIOD_CLOSED'],
}


def _write(verb, model):
    def planner(inp, ctx, s):
        return sales.prepare(s, ctx, inp, DOCUMENT, verb)

    cmd = command(
        'statement-charge ' + verb, scope='company', description=DESCRIPTIONS[verb],
        input_model=model, output_model=SalesWriteOutput, writes={'company'},
        required_role='standard', capability='ledger.post', accepts_idempotency_key=True,
        positional=[] if verb == 'post' else [DOCUMENT],
        version_source=None if verb == 'post' else ('statement-charge show', DOCUMENT, 'version'),
        error_codes=ERRORS[verb])(planner)
    cmd.ledger = True
    cmd.applier(sales.apply)
    return cmd


def _read(verb, model, output_model):
    def planner(inp, ctx, s):
        if verb == 'show':
            return Plan(sales.show(s, inp, DOCUMENT))
        return Plan(sales.page(s, ctx, inp, DOCUMENT))

    return command(
        'statement-charge ' + verb, scope='company', description=DESCRIPTIONS[verb],
        input_model=model, output_model=output_model,
        required_role='member', capability='ledger.read',
        positional=[] if verb == 'query' else [DOCUMENT],
        error_codes=['E_RECORD_NOT_FOUND'] + (['E_QUERY_STALE'] if verb == 'query' else []),
    )(planner)


statement_charge_post = _write('post', StatementChargePostInput)
statement_charge_void = _write('void', StatementChargeVoidInput)
statement_charge_show = _read('show', StatementChargeShowInput, SalesOutput)
statement_charge_query = _read('query', SalesQueryInput, SalesPageOutput)

STATEMENT_CHARGE_COMMANDS = [statement_charge_post, statement_charge_show,
                             statement_charge_query, statement_charge_void]
