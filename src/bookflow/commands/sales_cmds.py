"""Invoice and paid-sale lifecycle commands shared by every adapter."""
from bookflow.core.registry import command, Plan
from bookflow.company import sales
from bookflow.company.sales_models import (
    InvoicePostInput, InvoiceUpdateInput, InvoiceVoidInput, InvoiceShowInput,
    InvoiceHistoryInput, SalesReceiptPostInput, SalesReceiptUpdateInput,
    SalesReceiptVoidInput, SalesReceiptShowInput, SalesReceiptHistoryInput,
    SalesQueryInput,
)
from bookflow.company.sales_outputs import (
    SalesOutput, SalesWriteOutput, SalesPageOutput, SalesHistoryOutput,
)


def _write(document_type, verb, model):
    noun = document_type.replace('_', '-')

    def planner(inp, ctx, s):
        plan = sales.prepare(s, ctx, inp, document_type, verb)
        if document_type == 'sales_receipt' and verb in ('update', 'void') and plan.preview.changed:
            from bookflow.company.deposit_dependencies import require_unclaimed
            require_unclaimed(s, plan.data['header']['id'])
        if s.dry_run and document_type == 'invoice' and verb == 'update' and plan.preview.settlement is not None and not plan.data.get('recovered'):
            from bookflow.company.payment_pages import invoice_preview_output
            plan.preview.settlement = invoice_preview_output(s, ctx, inp, plan.preview.settlement)
        return plan

    cmd = command(
        noun + ' ' + verb, scope='company', description={
            'post': 'Post a home-currency service sale with captured commercial facts and typed custom fields; dry-run previews defaults, which resolve atomically at execution unless expected_facts_fingerprint is supplied. Use payment receive or payment apply to settle an existing invoice.',
            'update': 'Append an immutable sale correction with an exact old-date reversal and a full new-date replacement; dry-run previews resolved facts for optional expected_facts_fingerprint verification.',
            'void': 'Void a sale with a required context reason and an exact reversal at its current accounting date.',
        }[verb] + (' sales_tax_calculation captures legacy separate-component, combined-line or combined-invoice rounding. Omission retains policy on corrections; use_defaults reselects the company policy. Combined rounding attributes cents by stable tax ordinals separately from payment settlement keys.' if verb != 'void' else '') + (' A gross-changing receipt with linked-work history requires amount_received equal to the new gross; any supplied amount_received must match.'
                   if document_type == 'sales_receipt' and verb == 'update' else ''),
        input_model=model, output_model=SalesWriteOutput, writes={'company'},
        required_role='standard', capability='ledger.post', accepts_idempotency_key=True,
        positional=[] if verb == 'post' else [document_type], clearable=verb == 'update',
        version_source=None if verb == 'post' else (noun + ' show', document_type, 'version'),
        error_codes=['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_PERIOD_CLOSED',
                     'E_DUPLICATE_NUMBER', 'E_INACTIVE_REFERENCE', 'E_VALUE_RANGE',
                     'E_AMOUNT_PRECISION', 'E_REASON_REQUIRED', 'E_HAS_APPLICATIONS', 'E_APPLIED_EXCEEDS_TOTAL', 'E_PAYMENT_OPERATION_KEY_REUSED']
                    + (['E_DEPOSIT_DEPENDENCY'] if document_type == 'sales_receipt' and verb != 'post' else [])
                    + (['E_PREVIEW_STALE', 'E_WORK_DEPENDENCY'] if verb != 'void' else ['E_WORK_DEPENDENCY']),
    )(planner)
    if verb != 'post':
        from bookflow.company.billing_queries import authorize_sale
        cmd.authorize_input = lambda inp, ctx, s: authorize_sale(inp, ctx, s, document_type, True)
        cmd.authorization = 'standard ledger.post; customer-work standard when linked work is consumed'
    cmd.ledger = True
    if document_type == 'invoice' and verb == 'update':
        from bookflow.company.payment_operations import recover
        cmd.permanent_recovery = lambda inp, ctx, s: recover(inp, ctx, s, 'invoice update')
    cmd.applier(sales.apply)
    return cmd


def _read(document_type, verb, model, output_model):
    noun = document_type.replace('_', '-')

    def planner(inp, ctx, s):
        if verb == 'show':
            return Plan(sales.show(s, inp, document_type))
        return Plan(sales.page(s, ctx, inp, document_type, history=verb == 'history'))

    cmd = command(
        noun + ' ' + verb, scope='company', description={
            'show': 'Show a sale and its current or selected immutable revision, captured commercial and custom facts, ordered lines, tax components and separate posting batch totals.',
            'query': 'Page sales in accounting-date and stable-id order with exact customer, date, status and number filters; restart on company audit changes.',
            'history': 'Page immutable sale revisions in revision-number order with current header/version and correction and void batches; restart on company audit changes.',
        }[verb],
        input_model=model, output_model=output_model,
        required_role='member', capability='ledger.read',
        positional=[] if verb == 'query' else [document_type],
        error_codes=['E_RECORD_NOT_FOUND'] + (['E_QUERY_STALE'] if verb != 'show' else []),
    )(planner)
    if verb != 'query':
        from bookflow.company.billing_queries import authorize_sale
        cmd.authorize_input = lambda inp, ctx, s: authorize_sale(inp, ctx, s, document_type, False)
        cmd.authorization = 'member ledger.read; customer-work member before linked source details'
    return cmd


invoice_post = _write('invoice', 'post', InvoicePostInput)
invoice_update = _write('invoice', 'update', InvoiceUpdateInput)
invoice_void = _write('invoice', 'void', InvoiceVoidInput)
invoice_show = _read('invoice', 'show', InvoiceShowInput, SalesOutput)
invoice_query = _read('invoice', 'query', SalesQueryInput, SalesPageOutput)
invoice_history = _read('invoice', 'history', InvoiceHistoryInput, SalesHistoryOutput)

sales_receipt_post = _write('sales_receipt', 'post', SalesReceiptPostInput)
sales_receipt_update = _write('sales_receipt', 'update', SalesReceiptUpdateInput)
sales_receipt_void = _write('sales_receipt', 'void', SalesReceiptVoidInput)
sales_receipt_show = _read('sales_receipt', 'show', SalesReceiptShowInput, SalesOutput)
sales_receipt_query = _read('sales_receipt', 'query', SalesQueryInput, SalesPageOutput)
sales_receipt_history = _read('sales_receipt', 'history', SalesReceiptHistoryInput, SalesHistoryOutput)

SALES_COMMANDS = [
    invoice_post, invoice_show, invoice_update, invoice_void, invoice_query, invoice_history,
    sales_receipt_post, sales_receipt_show, sales_receipt_update, sales_receipt_void,
    sales_receipt_query, sales_receipt_history,
]
