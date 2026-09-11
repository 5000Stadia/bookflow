"""Order goods from a vendor, read the order, correct it, close it, and withdraw it.

A purchase order is the bill's form before anything is owed. The verbs are the bill's, so what
you learned entering one you already know here; the difference is that nothing posts. The
document lives in ``company/purchase_orders.py``.
"""
from bookflow.core.registry import Plan, command
from bookflow.company import purchase_orders
from bookflow.company.purchase_order_models import (
    PurchaseOrderHistoryInput, PurchaseOrderHistoryOutput, PurchaseOrderOutput,
    PurchaseOrderPageOutput, PurchaseOrderPostInput, PurchaseOrderQueryInput,
    PurchaseOrderShowInput, PurchaseOrderUpdateInput, PurchaseOrderVoidInput,
    PurchaseOrderWriteOutput,
)

_NOTHING_POSTS = (
    ' A purchase order posts nothing: no account moves, no payable is created and the trial'
    ' balance is exactly what it was before. It is a commitment to buy, and the books learn'
    ' about it only when a bill is entered from it.'
)
_LINES = (
    ' `lines` is one to 200 ordered rows. Each row names exactly one of `item` or `account`:'
    ' an item row takes the item’s own purchase account and its purchase description, an'
    ' account row names the account outright. Give `quantity` and `rate` together and the'
    ' amount is their product; give `amount` alone for a lump sum, or give it alongside the'
    ' pair to have the arithmetic checked rather than trusted. A row’s own `class_id` is that'
    ' row’s class and a row without one takes the order’s; set `class_mode` to `none` to leave'
    ' one row unclassified. `customer` attributes the cost to a customer or job and `billable`'
    ' marks it to be passed on to them later, which requires naming one.'
)
_HEADER = (
    ' `terms` defaults to the vendor’s own and travels to the bill. `expected_date` is when the'
    ' vendor promised it and is never before the order date. `ship_to` defaults to the'
    ' company’s own shipping address. `reference` is the vendor’s quotation or contract number,'
    ' kept as typed.'
)

_ENTRY_ERRORS = ['E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_VALIDATION', 'E_VALUE_RANGE',
                 'E_AMOUNT_PRECISION', 'E_DUPLICATE_NUMBER']
WRITE_ERRORS = {
    'post': _ENTRY_ERRORS,
    'update': _ENTRY_ERRORS + ['E_VERSION_CONFLICT', 'E_WORK_DEPENDENCY'],
    'void': ['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_VALIDATION', 'E_REASON_REQUIRED',
             'E_WORK_DEPENDENCY'],
}

DESCRIPTIONS = {
    'post': ('Order goods or services from a vendor.' + _NOTHING_POSTS + _HEADER + _LINES),
    'update': ('Correct a purchase order, or move where it stands. The whole order is replaced and'
               ' a new immutable revision is appended; every earlier revision stays readable.'
               ' Supply `lines` to replace the whole grid, carrying each surviving row’s'
               ' `line_id`; leave it out to correct the header alone and keep the lines exactly as'
               ' they were captured. `status` is where the order stands: `open` while nothing has'
               ' arrived, `partly_received` while some of it has, `closed` when nothing more is'
               ' coming. Closing is a decision a person makes, not one the books make — item'
               ' receipts do not exist yet — and it is reversible: set the status back to `open`.'
               ' An order that has already become a bill can no longer be corrected.'),
    'void': ('Withdraw a purchase order with a required reason. An order posts nothing, so nothing'
             ' is reversed and no money moves; what changes is that it can no longer become a'
             ' bill, and it stays readable in full — every revision, line and captured fact, with'
             ' the reason recorded. Terminal: a voided order cannot be updated or voided again,'
             ' and it is refused once a bill has already been entered from it.'),
    'show': ('Show a purchase order: its current or a selected immutable revision, captured vendor,'
             ' delivery, terms and class, its ordered lines, and the bill it became if it has'
             ' become one.'),
    'query': ('Page purchase orders in order-date and stable-id order, oldest first or newest first,'
              ' with exact vendor, order-date, expected-date, status, number and reference filters.'
              ' `open_only` narrows to the orders that can still become a bill. Restart on company'
              ' audit changes.'),
    'history': ('Page immutable purchase order revisions in revision-number order with the current'
                ' header, state and version; restart on company audit changes.'),
}


def _write(verb, model):
    def planner(inp, ctx, s):
        return purchase_orders.prepare(s, ctx, inp, verb)

    cmd = command(
        'purchase-order ' + verb, scope='company', description=DESCRIPTIONS[verb],
        input_model=model, output_model=PurchaseOrderWriteOutput, writes={'company'},
        required_role='standard', capability='ledger.post', accepts_idempotency_key=True,
        positional=[] if verb == 'post' else ['purchase_order'], clearable=verb == 'update',
        version_source=None if verb == 'post' else ('purchase-order show', 'purchase_order', 'version'),
        error_codes=WRITE_ERRORS[verb])(planner)
    cmd.applier(purchase_orders.apply)
    return cmd


def _read(verb, model, output_model):
    def planner(inp, ctx, s):
        if verb == 'show':
            return Plan(purchase_orders.show(s, inp))
        return Plan(purchase_orders.page(s, ctx, inp, history=verb == 'history'))

    return command(
        'purchase-order ' + verb, scope='company', description=DESCRIPTIONS[verb],
        input_model=model, output_model=output_model,
        required_role='member', capability='ledger.read',
        positional=[] if verb == 'query' else ['purchase_order'],
        error_codes=['E_RECORD_NOT_FOUND'] + (['E_QUERY_STALE'] if verb != 'show' else []),
    )(planner)


purchase_order_post = _write('post', PurchaseOrderPostInput)
purchase_order_update = _write('update', PurchaseOrderUpdateInput)
purchase_order_void = _write('void', PurchaseOrderVoidInput)
purchase_order_show = _read('show', PurchaseOrderShowInput, PurchaseOrderOutput)
purchase_order_query = _read('query', PurchaseOrderQueryInput, PurchaseOrderPageOutput)
purchase_order_history = _read('history', PurchaseOrderHistoryInput, PurchaseOrderHistoryOutput)

PURCHASE_ORDER_COMMANDS = [purchase_order_post, purchase_order_show, purchase_order_update,
                           purchase_order_void, purchase_order_query, purchase_order_history]
