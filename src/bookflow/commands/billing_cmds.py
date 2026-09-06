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
            'Show quoted, completed, billed and remaining work, exact partial quantities, original-scope percentages, current billing owner and linked invoices/receipts. Quoted tax is informational; actual installments use the captured source tax policy. Remaining tax forecasts all remaining billable nets together in current source order. Uncharged physical scope is not a debt.' if read else
            'This work is finished; make an invoice for remaining work, selected quantities or net amounts, or percentages of original scope. Rebill a released allocation by its exact allocation ID. Permanent retries preserve the original bill. Extra charges are added as independent unlinked lines through invoice update; quoted scope stays capped at100%. Completion, payment and sending remain separate operations.' if verb == 'invoice' else
            'They paid; make a sales receipt for remaining or selected partial work with the exact gross received, payment method and deposit account. Percentages refer to original scope; tax is calculated per bill. Rebill an exact released allocation when entirely free. This creates a paid sale, never payment of an existing invoice.') +
            ' Forecasts reserve no work. can_bill_together=false labels a hypothetical total above 200 spans per line or 2000 per bill; use the exact recommended net amount or select fewer complete lines. Bounded installments round independently. Rebilling keeps exact scope/net; invoice rounding preserves total tax for the same scope/rates, but different destination order can move cents between lines or agencies. Line rounding treats each line separately. Inspect the new attribution; earlier bills remain exact history. Company progress preferences preserve remaining-line billing and exact current bounded-recovery recommendations. Disabled ordinary partial modes return E_FEATURE_DISABLED before preview comparison; failed net-only recovery with a fingerprint returns E_PREVIEW_STALE. Authorized matching permanent replay precedes new-work gates. With progress disabled and automatic closure enabled, only final positive net billing directly from an estimate makes it inactive, preserving acceptance. source_effect records the immutable conversion; source_current reports current availability.',
        input_model=model, output_model=BillingOutput if read else SalesWriteOutput,
        writes=set() if read else {'company'}, required_role='member' if read else 'standard',
        capability='ledger.read' if read else 'ledger.post', accepts_idempotency_key=not read,
        positional=[kind], version_source=None if read else (kind.replace('_', '-') + ' show', kind, 'version'),
        authorization='ledger and customer-work membership' if read else 'ledger.post and customer-work standard role',
        error_codes=['E_RECORD_NOT_FOUND', 'E_QUERY_STALE'] if read else ['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT',
            'E_WORK_DEPENDENCY', 'E_CONVERSION_KEY_REUSED', 'E_PERIOD_CLOSED', 'E_DUPLICATE_NUMBER',
            'E_INACTIVE_REFERENCE', 'E_VALUE_RANGE', 'E_AMOUNT_PRECISION', 'E_PREVIEW_STALE', 'E_FEATURE_DISABLED'],
    )(planner)
    cmd.resource_requirements = (('customer-work', 'member' if read else 'standard'),)
    if not read:
        cmd.ledger = True
        cmd.applier(billing.apply)
        cmd.replay = lambda inp, ctx, s, hit: billing.replay_conversion(s, ctx, inp, kind, destination, hit)
    return cmd


BILLING_COMMANDS = [register(kind, prefix, verb) for kind, prefix in (('estimate', 'Estimate'), ('work_order', 'WorkOrder'))
    for verb in ('invoice', 'sales-receipt', 'billing')]
