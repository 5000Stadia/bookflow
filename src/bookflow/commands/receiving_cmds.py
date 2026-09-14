"""Public item receipt operations: goods now, payable bill separately."""
from bookflow.core.registry import command, Plan
from bookflow.company import receiving
from bookflow.company.receiving_models import (
    ReceiptPostInput, ReceiptUpdateInput, ReceiptVoidInput, ReceiptShowInput,
    ReceiptQueryInput, ReceiptHistoryInput, ReceiptWriteOutput, ReceiptOutput,
    ReceiptPageOutput, ReceiptHistoryOutput)


def _write(verb, model):
    def planner(inp, ctx, s):
        return receiving.prepare(s, ctx, inp, verb)
    cmd = command('item-receipt ' + verb, scope='company',
        description={'post': 'Receive inventory before a vendor bill. Items debit Inventory and credit receipt-owned Accounts Payable. Product unit costs and amounts exclude shipping. Optional Shipping is charged by this vendor and spread by received quantity; product plus shipping posts once. Zero-value quantities are valid. Purchase-order selections require the displayed version and ordered line identities.',
                     'update': 'Correct a receipt. Omitted items and shipping retain captured facts; enter zero to clear shipping; metadata edits preserve stock and claim identities. Release linked bills before changing physical receipt facts.',
                     'void': 'Void a receipt with a reason, reversing its exact stock and accounting and releasing its own physical order claims. Linked bills and insufficient historical stock refuse the whole change.'}[verb],
        input_model=model, output_model=ReceiptWriteOutput, writes={'company'},
        required_role='standard', capability='ledger.post', accepts_idempotency_key=True,
        positional=[] if verb == 'post' else ['receipt'], clearable=verb == 'update',
        version_source=None if verb == 'post' else ('item-receipt show', 'receipt', 'version'),
        error_codes=['E_RECORD_NOT_FOUND', 'E_VALIDATION', 'E_VERSION_CONFLICT', 'E_WORK_DEPENDENCY',
                     'E_PERIOD_CLOSED', 'E_REASON_REQUIRED', 'E_INACTIVE_REFERENCE', 'E_VALUE_RANGE',
                     'E_AMOUNT_PRECISION', 'E_DUPLICATE_NUMBER'])(planner)
    cmd.ledger = True
    cmd.applier(receiving.apply)
    return cmd


def _read(verb, model, output):
    def planner(inp, ctx, s):
        return Plan(receiving.page(s, inp, ctx) if verb == 'query' else getattr(receiving, verb)(s, inp))
    return command('item-receipt ' + verb, scope='company',
        description='Read captured received goods, their separately captured product/shipping amounts, physical identities and remaining unbilled quantities.',
        input_model=model, output_model=output, required_role='member', capability='ledger.read',
        positional=[] if verb == 'query' else ['receipt'], error_codes=['E_RECORD_NOT_FOUND', 'E_QUERY_STALE'])(planner)


receipt_post = _write('post', ReceiptPostInput)
receipt_update = _write('update', ReceiptUpdateInput)
receipt_void = _write('void', ReceiptVoidInput)
receipt_show = _read('show', ReceiptShowInput, ReceiptOutput)
receipt_query = _read('query', ReceiptQueryInput, ReceiptPageOutput)
receipt_history = _read('history', ReceiptHistoryInput, ReceiptHistoryOutput)
