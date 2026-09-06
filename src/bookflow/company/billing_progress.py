"""Exact before/current/after projections for an uncommitted work conversion."""
from fractions import Fraction

from bookflow.company import work, billing_allocations as alloc, billing_math as math
from bookflow.company import billing_queries as query, sales_calculations as calc
from bookflow.company.billing_facts import ExactFraction
from bookflow.company.sales_outputs import BillingProgressAmount, BillingProgressLine


def amount(quantity, scope, net, tax):
    return BillingProgressAmount(quantity=math.format_fraction(quantity),
        quantity_fraction=ExactFraction.of(quantity), scope_percent=math.format_fraction(scope),
        scope_percent_fraction=ExactFraction.of(scope), net_minor_units=net,
        tax_minor_units=tax, gross_minor_units=net+tax)


def projection(s, source, revision, allocations):
    """Use validated pending allocations and current posted facts; reserve nothing.

    Remaining tax is a fresh forecast from remaining net and captured rates.
    Previous/current/cumulative tax uses actual allocated installment tax.
    Independent extra sale lines do not contribute to quoted scope or these totals.
    """
    pending = {(row['root_document_id'],row['root_line_id']): row for row in allocations}
    identities = query.root_identities(s, source)
    from bookflow.company import tax_forecasts
    forecast,remaining_values=tax_forecasts.remaining(s,source,revision,pending=allocations)
    result = []
    for line in work.saved_lines(s, revision):
        identity = identities[line['line_id']]
        root = identity['root_document_id'], identity['root_line_id']
        facts = work.line_facts(line)
        d = math.denominator(facts.quantity_microunits, facts.net_minor_units)
        free_length, free_net = alloc.remaining(s, root, facts,policy=alloc.source_policy(s,line))
        prior = alloc.active_totals(s, root)
        row = pending.get(root)
        width = net = tax = 0
        if row is not None:
            proof = alloc.read_proof(row)
            width = sum(b-a for a,b in proof.intervals()) if proof else d
            net, tax = row['net_minor_units'], row['tax_minor_units']
        before_width = d-free_length
        after_width = free_length-width
        remaining_net = free_net-net if facts.billable else 0
        remaining_tax = remaining_values[line['line_id']]['tax']
        def part(length, net, tax):
            return amount(Fraction(facts.quantity_microunits*length,d*1_000_000),
                          Fraction(100*length,d),net,tax)
        result.append(BillingProgressLine(line_id=line['line_id'],root_document_id=root[0],root_line_id=root[1],
            previous=part(before_width,prior['net'],prior['tax']), current=part(width,net,tax),
            cumulative=part(before_width+width,prior['net']+net,prior['tax']+tax),
            remaining=part(after_width,remaining_net,remaining_tax)))
    return result,forecast
