"""Intuitive work-to-sale commands over shared company authority and accounting."""
from bookflow.core.registry import command, Plan
from bookflow.company import billing, billing_queries, billing_models as models
from bookflow.company.billing_outputs import BillingOutput
from bookflow.company.sales_outputs import SalesWriteOutput


def register(kind, prefix, verb):
    destination = verb.replace('-', '_')
    read = verb == 'billing'
    model = getattr(models, prefix + ''.join(w.title() for w in verb.split('-')) + 'Input')
    def planner(inp, ctx, s):
        return Plan(billing_queries.billing(s, ctx, inp, kind)) if read else billing.prepare(s, ctx, inp, kind, destination)
    cmd = command(kind.replace('_', '-') + ' ' + verb, scope='company',
        description=(
            'Show quoted, completed, billed and remaining whole work lines, their current billing owner and linked invoices/receipts. Zero-price unallocated work has no charge remaining.' if read else
            'This work is finished; make an invoice from selected whole unbilled lines using captured agreed facts and a permanent retry key. Completion is independent; paying this invoice and sending it remain later operations.' if verb == 'invoice' else
            'They paid; make a sales receipt from selected whole unbilled work lines with the exact amount received and deposit account. This creates a paid sale, never payment of an existing invoice.'),
        input_model=model, output_model=BillingOutput if read else SalesWriteOutput,
        writes=set() if read else {'company'}, required_role='member' if read else 'standard',
        capability='ledger.read' if read else 'ledger.post', accepts_idempotency_key=not read,
        positional=[kind], version_source=None if read else (kind.replace('_', '-') + ' show', kind, 'version'),
        authorization='ledger and customer-work membership' if read else 'ledger.post and customer-work standard role',
        error_codes=['E_RECORD_NOT_FOUND', 'E_QUERY_STALE'] if read else ['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT',
            'E_WORK_DEPENDENCY', 'E_CONVERSION_KEY_REUSED', 'E_PERIOD_CLOSED', 'E_DUPLICATE_NUMBER',
            'E_INACTIVE_REFERENCE', 'E_VALUE_RANGE', 'E_AMOUNT_PRECISION', 'E_PREVIEW_STALE'],
    )(planner)
    cmd.resource_requirements = (('customer-work', 'member' if read else 'standard'),)
    if not read:
        cmd.ledger = True
        cmd.applier(billing.apply)
        cmd.replay = lambda inp, ctx, s, hit: billing.replay_conversion(s, ctx, inp, kind, destination, hit)
    return cmd


BILLING_COMMANDS = [register(kind, prefix, verb) for kind, prefix in (('estimate', 'Estimate'), ('work_order', 'WorkOrder'))
    for verb in ('invoice', 'sales-receipt', 'billing')]
