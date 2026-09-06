"""Independent allocation checks against stored source facts and original intent."""
from fractions import Fraction
from itertools import zip_longest
import json

from bookflow.company import schema as c, work, billing_allocations as alloc, billing_math as math
from bookflow.company.work_facts import WorkLineFacts
from bookflow.core.errors import BookflowError
from bookflow.core.exact import parse_percentage_millionths, parse_quantity_micro_units
from bookflow.company.sales_models import money


def require(value, problem):
    if not value:
        raise BookflowError('E_INTERNAL', message='Invalid allocation proof: '+problem)


def source_facts(s, proof):
    rows = work.rows(s, c.work_lines, c.work_lines.c.document_id == proof.source_document_id,
        c.work_lines.c.revision_id == proof.source_revision_id, c.work_lines.c.id == proof.source_line_id)
    require(len(rows) == 1, 'source revision line does not exist')
    row = rows[0]
    identities = work.rows(s, c.work_line_identities, c.work_line_identities.c.id == row['line_id'],
                          c.work_line_identities.c.document_id == proof.source_document_id)
    require(len(identities) == 1, 'source identity does not exist')
    root = identities[0]['root_document_id'], identities[0]['root_line_id']
    require(root == (proof.root_document_id,proof.root_line_id), 'source root differs')
    facts = WorkLineFacts.model_validate_json(row['facts_snapshot'])
    require(proof.source_basis_hash == alloc.basis(facts, root), 'basis hash differs from stored source')
    require((proof.quoted_quantity_microunits, proof.quoted_base_quantity_microunits, proof.quoted_net_minor_units) ==
            (facts.quantity_microunits, facts.base_quantity_microunits, facts.net_minor_units), 'quoted numeric basis differs')
    return facts


def numeric_projection(s, line, proof):
    """Recalculate with Fraction rounding, independently of the allocation resolver."""
    facts = source_facts(s, proof)
    d = int(proof.denominator)
    spans = proof.intervals()
    length = sum(b-a for a,b in spans)
    net = sum(round(Fraction(facts.net_minor_units*b,d))-round(Fraction(facts.net_minor_units*a,d)) for a,b in spans)
    require(line['net_minor_units'] == net, 'allocated net differs')
    for field, source in [('quantity_microunits', facts.quantity_microunits),
                          ('base_quantity_microunits', facts.base_quantity_microunits)]:
        exact = Fraction(source*length,d)
        expected = exact.numerator if exact.denominator == 1 else None
        require(line[field] == expected and (expected is None or type(line[field]) is int), 'allocated '+field+' differs')
    require(line['unit_price_minor_units'] == facts.unit_price_minor_units, 'quoted unit price differs')
    return facts


def selected_identities(s, inp, lines, identities):
    explicit = {x.line_id for x in inp.selections} if inp.selections else set(inp.line_ids) if inp.line_ids else None
    selected = set()
    for line in lines:
        key = line['line_id']
        if explicit is not None and key not in explicit:
            continue
        facts = work.line_facts(line)
        root = identities[key]['root_document_id'],identities[key]['root_line_id']
        length, _ = alloc.remaining(s,root,facts)
        if explicit is not None:
            require(facts.billable and length > 0, 'explicit line is ineligible or consumed')
            selected.add(key)
        elif facts.billable and length > 0:
            selected.add(key)
    require(explicit is None or selected == explicit, 'explicit line identity is missing')
    return selected


def selected_spans(s, inp, line, root, facts, currency):
    """Check selection semantics directly, never calling the commercial resolver."""
    d = math.denominator(facts.quantity_microunits,facts.net_minor_units)
    entered = next((x for x in inp.selections or [] if x.line_id == line['line_id']),None)
    if entered and entered.rebill_allocation_id:
        prior = work.rows(s,c.work_billing_allocations,c.work_billing_allocations.c.id == entered.rebill_allocation_id)
        require(len(prior) == 1, 'rebill allocation missing')
        row = prior[0]
        require((row['root_document_id'],row['root_line_id']) == root, 'rebill belongs to another root')
        require(alloc.basis(alloc.captured_line(row),root) == alloc.basis(facts,root), 'rebill basis differs')
        old_proof = alloc.read_proof(row)
        spans = old_proof.intervals() if old_proof else ((0,d),)
        require(math.spans_available(spans,alloc.free_spans(s,root,facts),d), 'rebill spans are occupied')
        return spans
    net = None
    if entered and entered.net_amount is not None:
        net = money(entered.net_amount,currency,'net_amount').minor_units
        require(net > 0 and facts.net_minor_units > 0, 'zero net request')
        length = None
    elif entered and entered.quantity is not None:
        length = parse_quantity_micro_units(entered.quantity)*d//facts.quantity_microunits
    elif (entered and entered.percent is not None) or inp.percent is not None:
        length = parse_percentage_millionths(entered.percent if entered else inp.percent)*d//100_000_000
    else:
        # Bounded selection rejects before returning an oversized financial result.
        return math.canonical_spans(alloc.free_spans(s,root,facts),d)
    return math.allocate(alloc.free_spans(s,root,facts),denominator=d,
                         source_net=facts.net_minor_units,length=length,net_amount=net)
