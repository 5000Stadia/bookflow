"""Resolve requested source entitlement without accepting caller-authored proofs."""
from dataclasses import dataclass
from fractions import Fraction

from bookflow.company import schema as c, work, billing_math as math, billing_allocations as alloc
from bookflow.company.billing_facts import AllocationProof
from bookflow.company.sales_models import _invalid, money
from bookflow.core.errors import BookflowError
from bookflow.core.exact import parse_percentage_millionths, parse_quantity_micro_units


@dataclass
class SelectedLine:
    line: dict
    root: tuple[str, str]
    facts: object
    spans: tuple[math.Span, ...]
    proof: AllocationProof | None

    def __iter__(self):
        # Whole-line eligibility/classification consumers still inspect the quote.
        return iter((self.line, self.root, self.facts))


def range_error(line, facts, length, net, denominator, **extra):
    return BookflowError('E_VALUE_RANGE', details=dict(line_id=line['line_id'],
        available_quantity=math.format_fraction(Fraction(facts.quantity_microunits*length, denominator*1_000_000)),
        available_net_minor_units=net,
        problem='requested work exceeds available scope; bill extra charges as an unlinked sale line', **extra))


def rebill_spans(s, reference, root, facts, policy=None):
    from bookflow.company.billing import dependency
    rows = work.rows(s, c.work_billing_allocations, c.work_billing_allocations.c.id == reference)
    if not rows:
        dependency('released allocation is not available in this company', allocation_id=reference)
    row = rows[0]
    if (row['root_document_id'], row['root_line_id']) != root or alloc.basis(alloc.captured_line(row), root, alloc.captured_policy(row)) != alloc.basis(facts, root, policy):
        dependency('released allocation does not match this source root and economic basis', allocation_id=reference)
    saved = alloc.read_proof(row)
    d = math.denominator(facts.quantity_microunits, facts.net_minor_units)
    spans = saved.intervals() if saved else ((0, d),)
    if not math.spans_available(spans, alloc.free_spans(s, root, facts, policy=policy), d):
        dependency('the exact released allocation is not entirely free', allocation_id=reference)
    return spans


def select(s, inp, source, revision, lines, identities):
    from bookflow.company import tax_policy
    policy=tax_policy.effective(work.facts(revision).profile)
    from bookflow.company.billing import dependency
    requested = set(inp.line_ids) if inp.line_ids is not None else None
    entered = {value.line_id: (i, value) for i, value in enumerate(inp.selections or [])}
    if entered:
        requested = set(entered)
    current = {line['line_id'] for line in lines}
    if requested is not None and requested-current:
        field = next((f'selections.{i}.line_id' for key, (i, _) in entered.items() if key not in current), None)
        if field is None:
            field = next(f'line_ids.{i}' for i, key in enumerate(inp.line_ids) if key not in current)
        raise _invalid(field, 'select only current stable line identities from this source')
    selected, span_count = [], 0
    for line in lines:
        key = line['line_id']
        if requested is not None and key not in requested:
            continue
        root = identities[key]['root_document_id'], identities[key]['root_line_id']
        facts = work.line_facts(line)
        if not facts.billable:
            if requested is not None:
                dependency('selected source line is not billable', source_line_id=key)
            continue
        d = math.denominator(facts.quantity_microunits, facts.net_minor_units)
        length, net = alloc.remaining(s, root, facts, policy=policy)
        entry = entered.get(key)
        if length == 0 and not entry:
            if requested is not None:
                dependency('selected line is already billed', source_line_id=key)
            continue
        request = entry[1] if entry else None
        if request and request.rebill_allocation_id:
            spans = rebill_spans(s, request.rebill_allocation_id, root, facts, policy)
        else:
            requested_net = None
            requested_length = length
            if request and request.net_amount is not None:
                field = f'selections.{entry[0]}.net_amount'
                requested_net = money(request.net_amount, revision['currency'], field).minor_units
                if requested_net <= 0 or facts.net_minor_units <= 0:
                    raise _invalid(field, 'enter a positive amount on a positive-price source line')
                requested_length = None
            elif request and request.quantity is not None:
                requested_length = parse_quantity_micro_units(request.quantity)*d//facts.quantity_microunits
            else:
                percent = request.percent if request else inp.percent
                if percent is not None:
                    requested_length = parse_percentage_millionths(percent)*d//100_000_000
            if (requested_net is not None and requested_net > net) or (
                    requested_length is not None and (requested_length <= 0 or requested_length > length)):
                raise range_error(line, facts, length, net, d)
            try:
                spans = math.allocate(alloc.free_spans(s, root, facts, policy=policy), denominator=d,
                    source_net=facts.net_minor_units, length=requested_length, net_amount=requested_net)
            except math.FragmentationError:
                recommendation = math.recommended_net(alloc.free_spans(s, root, facts, policy=policy), denominator=d,
                    source_net=facts.net_minor_units)
                raise range_error(line, facts, length, net, d, recommended_net_amount=money(
                    {'minor_units': recommendation, 'currency': revision['currency']}, revision['currency']).to_dict(),
                    recovery='bill the recommended net amount, then continue with remaining work') from None
        span_count += len(spans)
        if span_count > 2000:
            raise BookflowError('E_VALUE_RANGE', details=dict(line_id=key,
                problem='conversion exceeds2000 allocation spans', recovery='select fewer source lines'))
        proof = None if spans == ((0,d),) and facts.schema_version==1 else alloc.make_proof(source, revision, line, root, facts, spans)
        selected.append(SelectedLine(line, root, facts, spans, proof))
    if not selected or sum(item.proof.net() if item.proof else item.facts.net_minor_units for item in selected) <= 0:
        dependency('No charge remains; zero-price lines were not invoiced', source_id=source['id'])
    return selected
