"""Enter a bill, correct it, void it, and read what is owed.

The payables mirror of the invoice, verb for verb: what you learned entering one you already
know here. The accounting lives in ``company/bills.py``.
"""
from bookflow.core.registry import Plan, command
from bookflow.company import bills
from bookflow.company.bill_models import (
    BillHistoryInput, BillHistoryOutput, BillOutput, BillPageOutput, BillPostInput,
    BillQueryInput, BillShowInput, BillUpdateInput, BillVoidInput, BillWriteOutput,
)

_LINES = (
    ' `expenses` is one to 200 rows of account, amount, memo, optional customer or job,'
    ' optional `billable` and optional class, saying what was bought; each row debits its own'
    " account and the bill's total is their sum. A row's own `class_id` is that row's class and"
    " a row without one takes the bill's `class_id`; set `class_mode` to `none` to leave one row"
    ' unclassified even when the bill carries a class. `billable` marks a cost to pass on to the'
    ' named customer later and requires one; naming a job without it simply attributes the cost.'
    ' Item lines are not entered here yet.'
)
_HEADER = (
    ' `terms` defaults to the vendor’s own terms and fixes `due_date`; give `due_date` to override'
    ' it outright. `ap_account` is the Accounts Payable account the bill is owed from and defaults'
    ' to the only active one when the company has exactly one. `supplier_reference` is the'
    ' vendor’s own document number, kept as typed; another bill from the same vendor carrying the'
    ' same reference is reported in `duplicate_references` and never refused.'
)

# Each verb declares only what it can actually raise: a new bill has no version to be stale
# and nothing applied against it yet, and a void reads no accounts and allocates no number.
_ENTRY_ERRORS = ['E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_VALIDATION', 'E_VALUE_RANGE',
                 'E_AMOUNT_PRECISION', 'E_PERIOD_CLOSED', 'E_DUPLICATE_NUMBER']
WRITE_ERRORS = {
    'post': _ENTRY_ERRORS,
    'update': _ENTRY_ERRORS + ['E_VERSION_CONFLICT', 'E_HAS_APPLICATIONS'],
    'void': ['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_VALIDATION', 'E_REASON_REQUIRED',
             'E_PERIOD_CLOSED', 'E_HAS_APPLICATIONS'],
}

DESCRIPTIONS = {
    'post': ('Enter a vendor bill. Each expense line debits its own account and Accounts Payable is'
             ' credited the total, so the bill stands open at that total until it is paid.'
             + _HEADER + _LINES),
    'update': ('Correct a bill. The old accounting is reversed at its original date and replaced in'
               ' full at the new one; every earlier revision stays readable. Supply `expenses` to'
               ' replace the whole grid, carrying each surviving row’s `line_id`; leave it out to'
               ' correct the header alone and keep the lines exactly as they were captured.'
               + _HEADER),
    'void': ('Void a bill with a required reason. Its accounting is reversed at its own date, its'
             ' number stays occupied and its history stays readable.'),
    'show': ('Show a bill: its current or a selected immutable revision, captured vendor, payable,'
             ' terms and custom facts, its expense lines, its posting batches, what is still open'
             ' on it, and any other bill from this vendor carrying the same supplier reference.'),
    'query': ('Page bills in accounting-date and stable-id order, oldest first or newest first, with'
              ' exact vendor, bill-date, due-date, status, number and supplier-reference filters;'
              ' restart on company audit changes.'),
    'history': ('Page immutable bill revisions in revision-number order with the current header and'
                ' version and the correction and void batches; restart on company audit changes.'),
}


def _write(verb, model):
    def planner(inp, ctx, s):
        return bills.prepare(s, ctx, inp, verb)

    cmd = command(
        'bill ' + verb, scope='company', description=DESCRIPTIONS[verb],
        input_model=model, output_model=BillWriteOutput, writes={'company'},
        required_role='standard', capability='ledger.post', accepts_idempotency_key=True,
        positional=[] if verb == 'post' else ['bill'], clearable=verb == 'update',
        version_source=None if verb == 'post' else ('bill show', 'bill', 'version'),
        error_codes=WRITE_ERRORS[verb])(planner)
    cmd.ledger = True
    cmd.applier(bills.apply)
    return cmd


def _read(verb, model, output_model):
    def planner(inp, ctx, s):
        if verb == 'show':
            return Plan(bills.show(s, inp))
        return Plan(bills.page(s, ctx, inp, history=verb == 'history'))

    return command(
        'bill ' + verb, scope='company', description=DESCRIPTIONS[verb],
        input_model=model, output_model=output_model,
        required_role='member', capability='ledger.read',
        positional=[] if verb == 'query' else ['bill'],
        error_codes=['E_RECORD_NOT_FOUND'] + (['E_QUERY_STALE'] if verb != 'show' else []),
    )(planner)


bill_post = _write('post', BillPostInput)
bill_update = _write('update', BillUpdateInput)
bill_void = _write('void', BillVoidInput)
bill_show = _read('show', BillShowInput, BillOutput)
bill_query = _read('query', BillQueryInput, BillPageOutput)
bill_history = _read('history', BillHistoryInput, BillHistoryOutput)

BILL_COMMANDS = [bill_post, bill_show, bill_update, bill_void, bill_query, bill_history]
