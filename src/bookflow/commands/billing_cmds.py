"""Intuitive work-to-sale commands over shared company authority and accounting."""
from bookflow.core.errors import BookflowError
from bookflow.core.registry import command, Plan
from bookflow.company import billing, billing_queries, billing_models as models
from bookflow.company.billing_outputs import BillingOutput
from bookflow.company.sales_outputs import SalesWriteOutput


def prepayments_kinds():
    from bookflow.company.prepayments import HOLDING_KINDS
    return HOLDING_KINDS


ORDER_DESCRIPTIONS = {
    'invoice': ('Invoice what has shipped on a sales order. With no selection it bills what can ship: each stocked line '
                'up to what is available to this order (on hand less what other orders and work orders commit), shared '
                'in line order among its lines for one item, and every other open line when anything ships or the order '
                'has no open stocked line; if nothing can ship it refuses with each line\'s open quantity and '
                'availability. all_open bills every open quantity whatever the stock; line_ids bills whole open '
                'remainders; selections take a quantity per line, naming line_id or an item that exactly one open line '
                'carries. Percent, net-amount and rebill selections are refused. The invoice is taxed as an ordinary '
                'invoice of its own date (defaulted tax selections resolved again, current rates); order_tax shows the '
                'difference from the order\'s quoted tax. Posting issues the stock and consumes the order lines in one '
                'transaction; apply_prepayment (default true) uses money held on the order or its estimate. A retried '
                'conversion key returns the original invoice.'),
    'billing': ('Show an order\'s lines with ordered, invoiced, cancelled and remaining quantities, remaining amounts and '
                'tax forecast, and the invoices made from it.'),
}


# Every other kind's invoice: steer an agent to the customer's open sales order rather than billing its
# lines again from other work (sales-orders design §4, "Agents bill from invoices").
ORDER_STEER = (' When the customer has an open sales order for these items, bill the order with `sales-order invoice` '
               'instead, so its backorder, committed stock and any deposit follow; warnings name the open orders and '
               'give the exact command.')


def order_advice(s, output):
    """Add the customer's open-sales-order note to an invoice's warnings: advice, never a refusal."""
    customer = getattr(getattr(output, 'revision', None), 'name_id', None)
    if output is None or customer is None or getattr(output, 'idempotent_replay', False):
        return output
    from bookflow.company.sales_orders import order_note
    try:
        note = order_note(s, customer, 'invoice', output)
    except BookflowError:
        note = None
    if note and note not in output.warnings:
        output.warnings.append(note)
    return output


def register(kind, prefix, verb):
    destination = verb.replace('-', '_')
    read = verb == 'billing'
    model = getattr(models, prefix + ''.join(w.title() for w in verb.split('-')) + 'Input')
    def planner(inp, ctx, s):
        from bookflow.company.billing_allocations import one_reading
        with one_reading(s):
            plan = Plan(billing_queries.billing(s, ctx, inp, kind)) if read else billing.prepare(s, ctx, inp, kind, destination)
        if verb == 'invoice' and kind != 'sales_order' and plan.data.get('changed', True):
            order_advice(s, plan.preview)
        if verb == 'invoice' and kind not in prepayments_kinds():
            return plan
        if verb == 'invoice' and plan.data.get('changed', True) and not plan.preview.idempotent_replay:
            from bookflow.company import prepayments
            prepayments.conversion_preview(s, plan.preview, kind, getattr(inp, kind), inp.apply_prepayment)
            if kind == 'sales_order':
                from bookflow.company import sales_orders, work
                note = sales_orders.prepayment_date_warning(s, work.resolve(s, inp.sales_order, kind), plan.preview, inp.date)
                if note:
                    plan.preview.warnings.append(note)
        elif verb == 'invoice':
            from bookflow.company import prepayments
            plan = Plan(prepayments.conversion_replay(s, plan.preview), plan.data)
        return plan
    cmd = command(kind.replace('_', '-') + ' ' + verb, scope='company',
        description=ORDER_DESCRIPTIONS[verb] if kind == 'sales_order' else (
            'Show quoted, completed, billed and remaining work, exact partial quantities, original-scope percentages, current billing owner and linked invoices/receipts. Quoted tax is informational; actual installments use the captured source tax policy. Remaining tax forecasts all remaining billable nets together in current source order. Uncharged physical scope is not a debt.' if read else
            'This work is finished; make an invoice for remaining work, selected quantities or net amounts, or percentages of original scope. Rebill a released allocation by its exact allocation ID. Permanent retries preserve the original bill. Extra charges are added as independent unlinked lines through invoice update; quoted scope stays capped at 100%. Completion, payment and sending remain separate operations.' + ORDER_STEER if verb == 'invoice' else
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
        if verb == 'invoice' and kind in prepayments_kinds():
            def apply(plan, ctx, s):
                from bookflow.company import prepayments
                applied = billing.apply(plan, ctx, s)
                inp = plan.data['input']
                if applied.output is not None and getattr(applied.output, 'changed', True):
                    applied.output = prepayments.conversion_draw(s, ctx, applied.output, kind, getattr(inp, kind),
                                                                 inp.apply_prepayment)
                    if kind == 'sales_order':
                        from bookflow.company import sales_orders, work
                        note = sales_orders.prepayment_date_warning(s, work.resolve(s, inp.sales_order, kind), applied.output, inp.date)
                        if note and note not in applied.output.warnings:
                            applied.output.warnings.append(note)
                    else:
                        order_advice(s, applied.output)
                elif applied.output is not None:
                    applied.output = prepayments.conversion_replay(s, applied.output)
                return applied
            cmd.applier(apply)
        elif verb == 'invoice':
            def apply(plan, ctx, s):
                applied = billing.apply(plan, ctx, s)
                if applied.output is not None and getattr(applied.output, 'changed', True):
                    order_advice(s, applied.output)
                return applied
            cmd.applier(apply)
        else:
            cmd.applier(billing.apply)
        if verb == 'invoice' and kind in prepayments_kinds():
            def replay(inp, ctx, s, hit):
                from bookflow.company import prepayments
                result = billing.replay_conversion(s, ctx, inp, kind, destination, hit)
                return prepayments.conversion_replay_dict(s, result)
            cmd.replay = replay
        else:
            cmd.replay = lambda inp, ctx, s, hit: billing.replay_conversion(s, ctx, inp, kind, destination, hit)
    return cmd


# The kinds come from billing_queries, which is where what can be billed is declared.
BILLING_COMMANDS = [register(kind, ''.join(word.title() for word in kind.split('_')), verb)
    for kind in billing_queries.BILLING_KINDS if kind != 'mileage'  # a trip's are `trip invoice` etc. (mileage_cmds)
    for verb in ('invoice', 'sales-receipt', 'billing')
    # An order becomes an invoice, never a sales receipt (the anchor offers none).
    if not (kind == 'sales_order' and verb == 'sales-receipt')]
