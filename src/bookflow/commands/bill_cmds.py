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
    ' A bill has two grids and needs at least one row across them. `expenses` is up to 200 rows'
    ' of account, amount, memo, optional customer or job, optional `billable` and optional'
    ' class, saying what was bought against an account you name. `items` is up to 200 rows of'
    ' item, optional `quantity` (default 1), optional `unit_cost` or `amount`, optional'
    ' `description`, customer or job, `billable` and class, saying what was bought as a thing'
    ' the company buys; an item row names no account because it debits the item’s own expense'
    ' account. Give `unit_cost` and the amount is quantity times it; give `amount` and that is'
    ' the amount; give neither and the item’s standard cost is used. Every row debits its own'
    " account and the bill's total is the sum of both grids. A row's own `class_id` is that"
    " row's class and a row without one takes the bill's `class_id`; set `class_mode` to `none`"
    ' to leave one row unclassified even when the bill carries a class. `billable` marks a cost'
    ' to pass on to the named customer later and requires one; naming a job without it simply'
    ' attributes the cost. Every item kind the company buys can be bought here, and an'
    ' inventory part also moves its quantity on hand and its cost -- an item row is the only'
    ' row that can, because an expense row names no item.'
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
    # A bill entered from a purchase order can be refused for that order's sake: it was
    # withdrawn, or another bill already consumed it.
    'post': _ENTRY_ERRORS + ['E_WORK_DEPENDENCY'],
    'update': _ENTRY_ERRORS + ['E_VERSION_CONFLICT', 'E_HAS_APPLICATIONS'],
    'void': ['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_VALIDATION', 'E_REASON_REQUIRED',
             'E_PERIOD_CLOSED', 'E_HAS_APPLICATIONS'],
}

DESCRIPTIONS = {
    'post': ('Enter a vendor bill. Each entered line debits its own account and Accounts Payable is'
             ' credited the total, so the bill stands open at that total until it is paid.'
             ' `purchase_order` enters this bill from an order: the order fills in the vendor,'
             ' the terms, the class, the memo and its own lines -- an ordered item becomes an'
             ' item row and an ordered account line becomes an expense row. A header field'
             ' supplied here wins over the order on its own. The two line grids are one answer:'
             ' write either `expenses` or `items`, an empty one included, and the bill is'
             ' exactly the lines written here, because a caller who writes a line is saying'
             ' what arrived. To change one grid and keep the other, send both. The order is then'
             ' closed and permanently recorded as consumed by this bill, so it can never be'
             ' billed twice -- in full, even when this bill covers only part of what was'
             ' ordered; there is no remaining quantity and no backorder. A withdrawn order is'
             ' refused. Voiding the bill does not free the'
             ' order again: enter the replacement bill outright.'
             ' Alternatively, `receipts` selects immutable received-line IDs, current receipt versions and quantities. The bill date is explicit, including when receipt dates differ. Matched items transfer receipt-owned AP to this bill without receiving stock again. Give a product-only unit_cost or amount, excluding retained receipt shipping, to correct acquisition value at each receipt date and affected issue costs at their sale dates. Omit cost to transfer exact original product and shipping interval values. Shipping from the same receipt vendor is retained exactly once, including when product cost is zero. The selected receipts must share vendor, AP account and currency; new items and a whole-order source cannot be combined with receipt selections.'
             + _HEADER + _LINES),
    'update': ('Correct a bill. The old accounting is reversed at its original date and replaced in'
               ' full at the new one; every earlier revision stays readable. Supply `expenses` or'
               ' `items` to replace that whole grid, carrying each surviving row’s `line_id`; the'
               ' grid you leave out keeps its lines exactly as they were captured, and an empty'
               ' list clears that grid. Leave both out to correct the header alone.'
               ' A linked bill retains its receipt mappings when `receipts` is omitted; supply selections to replace them. Unchanged acquisition costs retain their original correction identities, so a header edit does not reopen unaffected historical dates.'
               + _HEADER),
    'void': ('Void a bill with a required reason. Its accounting is reversed at its own date, its'
             ' number stays occupied and its history stays readable. A linked bill releases its exact financial intervals and reverses its own acquisition-price corrections; physical receipts and PO quantity claims remain.'),
    'show': ('Show a bill: its current or a selected immutable revision, captured vendor, payable,'
             ' terms and custom facts, its expense lines and item lines, its posting batches, what'
             ' is still open on it, and any other bill from this vendor carrying the same supplier'
             ' reference. A deleted bill is not returned unless `include_deleted` asks for it, and'
             ' then it reads `deleted` and carries who deleted it, when and why.'),
    'query': ('Page bills in accounting-date and stable-id order, oldest first or newest first, with'
              ' exact vendor, bill-date, due-date, status, number and supplier-reference filters;'
              ' restart on company audit changes. Deleted bills are omitted unless `include_deleted`'
              ' asks for them.'),
    'history': ('Page immutable bill revisions in revision-number order with the current header and'
                ' version and the correction and void batches; restart on company audit changes. A'
                ' deleted bill is not returned unless `include_deleted` asks for it.'),
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


def _delete():
    from bookflow.company import bill_deletions as deletion
    from bookflow.company.bill_deletion_models import BillDeleteInput, BillDeleteOutput
    from bookflow.core.deletion_families import capability

    def planner(inp, ctx, s):
        return deletion.prepare(s, ctx, inp)

    def recover(inp, ctx, s):
        return deletion.recover(inp, ctx, s)

    cmd = command(
        'bill delete', scope='company',
        description='Delete this bill with a required reason and exact expected_version. Cancel its'
                    ' expense, payable and stock effects at their original dates and release the'
                    ' received lines it claimed; retain immutable history and its number. Requires'
                    ' the explicit family Delete grant and ledger.read, independently of ledger.post.'
                    ' A live bill payment or vendor credit refuses atomically and names the settlement'
                    ' holding it; a closed period refuses. A purchase order this bill consumed stays'
                    ' consumed, exactly as it does on void, and is named in the result.',
        input_model=BillDeleteInput, output_model=BillDeleteOutput, writes={'company'},
        required_role='standard', capability=capability('bill'), explicit_grant_only=True,
        accepts_idempotency_key=True, positional=['bill'],
        version_source=('bill show', 'bill', 'version'),
        error_codes=['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_VALIDATION', 'E_REASON_REQUIRED',
                     'E_PERIOD_CLOSED', 'E_RECONCILIATION_DEPENDENCY', 'E_IDEMPOTENCY_MISMATCH',
                     'E_HAS_APPLICATIONS'])(planner)
    cmd.resource_requirements = (('ledger.read', 'member'),)
    cmd.ledger = True
    cmd.permanent_recovery = recover
    cmd.applier(deletion.apply)
    return cmd


bill_delete = _delete()
bill_post = _write('post', BillPostInput)
bill_update = _write('update', BillUpdateInput)
bill_void = _write('void', BillVoidInput)
bill_show = _read('show', BillShowInput, BillOutput)
bill_query = _read('query', BillQueryInput, BillPageOutput)
bill_history = _read('history', BillHistoryInput, BillHistoryOutput)

BILL_COMMANDS = [bill_post, bill_show, bill_update, bill_void, bill_delete, bill_query, bill_history]
