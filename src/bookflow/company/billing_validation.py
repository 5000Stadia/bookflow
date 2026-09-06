"""Independent source ownership and allocation checks before billing persistence."""
import json
from pydantic import ValidationError
from fractions import Fraction
from bookflow.company import schema as c, work, sales, billing_queries as query
from bookflow.company.work_tax_facts import read_line, read_facts
from bookflow.company import work_tax, tax_attribution
from bookflow.company import billing_allocations as alloc, billing_math as math, billing_checks as checks
from bookflow.company.sales_facts import SalesLineProfile, SalesProfile
from bookflow.core.errors import BookflowError


def require(value, problem):
    if not value:
        raise BookflowError('E_INTERNAL', message='Invalid billing aggregate: ' + problem)


def validate_sale_allocations(plan, s):
    data = plan.data
    if data.get('billing_source'):
        return  # The conversion validator verifies its newly created source snapshot.
    expected = []
    if data['before']:
        prior = query.revision_allocations(s, data['old_revision']['id'])
        old_lines = {row['id']: row for row in sales.saved_lines(s, data['old_revision'])}
        current = {row['line_id']: row for row in data['pending']['document_lines']}
        if data['operation'] != 'void':
            for row in prior:
                line = current.get(old_lines[row['document_line_id']]['line_id'])
                if line:
                    projected=next(r for r in data['pending']['sales_line_profiles'] if r['document_line_id']==line['id'])
                    expected.append(dict(row, document_line_id=line['id'], revision_id=line['revision_id'],tax_minor_units=projected['tax_minor_units'],gross_minor_units=projected['gross_minor_units']))
    actual = data.get('billing_allocations', [])
    strip = lambda row: {k: v for k, v in row.items() if k not in ('id', 'created_at', 'created_by', 'created_via')}
    require(sorted((strip(x) for x in actual), key=str) == sorted((strip(x) for x in expected), key=str), 'correction changed or lost source allocations')
    seen = set()
    for row in actual:
        root = row['root_document_id'], row['root_line_id']
        require(root not in seen, 'same root repeated in a sale')
        seen.add(root)
        facts = alloc.captured_line(row)
        proof = alloc.read_proof(row)
        d = math.denominator(facts.quantity_microunits, facts.net_minor_units)
        spans = proof.intervals() if proof else ((0,d),)
        require(math.spans_available(spans, alloc.free_spans(s, root, facts, excluding=data['header']['id'],policy=alloc.captured_policy(row)), d),
                'allocation overlaps another sale')
        require(row['created_by'] == data['header']['updated_by'] and row['created_via'] == data['header']['updated_via']
            and row['created_at'] == data['header']['updated_at'], 'allocation provenance')


def validate(plan, s, ctx):
    try:
        return _validate(plan, s, ctx)
    except (ValidationError, json.JSONDecodeError) as exc:
        raise BookflowError('E_INTERNAL', message='Invalid billing aggregate: malformed captured facts') from exc


def _validate(plan, s, ctx):
    if not plan.data['changed']:
        return
    from bookflow.company import billing
    from bookflow.company.sales_validation import validate as validate_sale
    validate_sale(plan, s, ctx)
    data = plan.data
    inp, kind, dest = data['input'], data['kind'], data['destination']
    source = work.resolve(s, getattr(inp,kind), kind)
    require(source['version'] == inp.expected_version and source['active'], 'stale or inactive source')
    require(query.current_owner(s,source)['id'] == source['id'], 'ancestor is not current billing owner')
    require(source['status'] == 'accepted' if kind == 'estimate' else source['status'] != 'cancelled', 'ineligible source')
    rev = work.revision(s,source)
    identities = query.root_identities(s,source)
    source_lines = work.saved_lines(s,rev)
    from bookflow.company import work_preferences as policy
    policy.check_selection(s, inp, source, rev, source_lines, identities)
    selected_ids = checks.selected_identities(s,inp,source_lines,identities)
    selected = [(line,(identities[line['line_id']]['root_document_id'],identities[line['line_id']]['root_line_id']),
                 work.line_facts(line)) for line in source_lines if line['line_id'] in selected_ids]
    header, created = data['header'], data['pending']['transaction_revisions'][0]
    wh, wb, wp = data['work_header'], data['work_before'], data['work_pending']
    require(wb == source and wh['id'] == source['id'] and wh['version'] == source['version'] + 1, 'wrong source/version')
    require(all(wh[k] == source[k] for k in source if k not in ('active', 'version', 'updated_at', 'updated_by', 'updated_via', 'current_revision_id')), 'source header facts changed')
    require(wh['updated_at'] == header['updated_at'] and wh['updated_by'] == s.actor.id and wh['updated_via'] == ctx.interface.value, 'source provenance')
    require(len(wp['work_revisions']) == 1 and not wp['work_line_identities'] and not wp['work_links'], 'unexpected operational effects')
    newrev = wp['work_revisions'][0]
    require(newrev['id'] == wh['current_revision_id'] and newrev['document_id'] == source['id'], 'source revision ownership')
    require(newrev['revision_number'] == rev['revision_number'] + 1 and newrev['supersedes_revision_id'] == rev['id'], 'source revision ancestry')
    require(newrev['audit_event_id'] == data['event'], 'source event')
    require(set(wp) == {name for name, _ in work.TABLE_KINDS}, 'unexpected source persistence table')
    for row in wp['work_revisions'] + wp['work_lines'] + wp['work_tax_line_keys'] + wp['work_tax_attributions'] + wp['work_tax_attribution_lines']:
        require(row['created_at'] == header['updated_at'] and row['created_by'] == s.actor.id
            and row['created_via'] == ctx.interface.value, 'source history provenance')
    unchanged = set(rev) - {'facts_snapshot', 'active', 'id', 'revision_number', 'supersedes_revision_id', 'audit_event_id', 'created_at', 'created_by', 'created_via'}
    require(all(newrev[k] == rev[k] for k in unchanged), 'billing rewrote source facts')
    require(work_tax.normalized(work._semantic(rev,[]))['facts']==work_tax.normalized(work._semantic(newrev,[]))['facts'],'billing rewrote source agreement')
    require(all(row['revision_id']==newrev['id'] for name in ('work_tax_attributions','work_tax_attribution_lines') for row in wp[name]),'orphan source tax fact')
    work_tax.validate(s,newrev,wp['work_lines'],wp,require)
    old_lines = work.saved_lines(s, rev)
    stripline = lambda row: {k: v for k, v in row.items() if k not in ('id', 'revision_id', 'created_at', 'created_by', 'created_via')}
    require([stripline(x) for x in wp['work_lines']] == [stripline(x) for x in old_lines], 'billing rewrote source lines')
    require(all(x['revision_id'] == newrev['id'] and x['document_id'] == wh['id'] for x in wp['work_lines']), 'source line owner')
    conv = data['billing_conversion']
    key, request = billing.hashes(inp, kind, dest)
    expected = dict(source_document_id=source['id'], source_revision_id=rev['id'], source_version=source['version'],
        destination_transaction_id=header['id'], destination_revision_id=created['id'], destination_type=dest,
        relation=kind + '_' + dest, conversion_key_hash=key, request_hash=request,
        created_at=header['updated_at'], created_by=s.actor.id, created_via=ctx.interface.value)
    require({k:v for k,v in conv.items() if k != 'id'} == expected, 'conversion identity or intent')
    require(not work.rows(s, c.work_links, c.work_links.c.conversion_key_hash == key) and
        not work.rows(s, c.work_billing_conversions, c.work_billing_conversions.c.conversion_key_hash == key), 'duplicate durable key')
    allocs, envelopes = data['billing_allocations'], data['pending']['document_lines']
    # Reconstruct required closure from all current roots and pending amounts.
    # The resolver's closure flag and output are not evidence for this check.
    remaining = sum(alloc.remaining(s, (identities[line['line_id']]['root_document_id'],
        identities[line['line_id']]['root_line_id']), work.line_facts(line),policy=alloc.source_policy(s,line))[1]
        for line in source_lines if work.line_facts(line).billable)
    selected_net = sum(row['net_minor_units'] for row in allocs)
    settings = s.company.conn.execute(c.company_info.select()).mappings().one()
    closes = (kind == 'estimate' and source['status'] == 'accepted' and source['active']
        and not settings['progress_billing_enabled'] and settings['close_estimates_after_billing']
        and remaining > 0 and selected_net == remaining)
    require(wh['active'] == (not closes) and newrev['active'] == wh['active'], 'required automatic closure differs')
    require(plan.preview.source_effect == billing.source_effect(source, rev, newrev, source['version'])
        and plan.preview.source_current == billing.source_current(wh), 'source effect/current projection differs')
    require(len(allocs) == len(selected) == len(envelopes), 'missing selected allocation')
    profiles = {row['document_line_id']: row for row in data['pending']['sales_line_profiles']}
    captured_header = work.facts(rev)
    posted_header = SalesProfile.model_validate_json(data['pending']['sales_profiles'][0]['profile_snapshot'])
    for field in type(captured_header.profile).model_fields:
        if field in ('origins', 'schema_version', 'sales_tax_calculation', 'tax_policy_origin') or (field == 'terms' and (dest == 'sales_receipt' or 'terms' in inp.model_fields_set)):
            continue
        require(getattr(posted_header, field) == getattr(captured_header.profile, field),
            'captured commercial header differs: ' + field)
    from bookflow.company import tax_policy
    require(tax_policy.effective(posted_header) == tax_policy.effective(captured_header.profile)
        and tax_policy.origin(posted_header) == tax_policy.origin(captured_header.profile), 'captured tax policy differs')
    if dest == 'sales_receipt':
        require(posted_header.terms is None, 'paid receipt cannot carry invoice credit terms')
    require(json.loads(created['issuer_snapshot']) == captured_header.issuer_snapshot, 'captured issuer differs')
    exact_cells=tax_attribution.validate_sales(s,header,created,posted_header,data['pending'],require)
    roots = set()
    for actual, envelope, (line, root, lf) in zip(allocs, envelopes, selected):
        require(root not in roots, 'duplicate root in conversion')
        roots.add(root)
        d = math.denominator(lf.quantity_microunits,lf.net_minor_units)
        spans = checks.selected_spans(s,inp,line,root,lf,rev['currency'])
        proof = None if spans == ((0,d),) and lf.schema_version==1 else alloc.make_proof(source,rev,line,root,lf,spans)
        width = sum(b-a for a,b in spans)
        net = sum(round(Fraction(lf.net_minor_units*b,d))-round(Fraction(lf.net_minor_units*a,d)) for a,b in spans)
        tax = sum(exact_cells[envelope['id'],t.rule.id] for t in lf.taxes)
        q, bq = Fraction(lf.quantity_microunits*width,d), Fraction(lf.base_quantity_microunits*width,d)
        quantity = q.numerator if q.denominator == 1 else None
        base_quantity = bq.numerator if bq.denominator == 1 else None
        expected = dict(transaction_id=header['id'], revision_id=created['id'], document_line_id=envelope['id'],
            source_document_id=source['id'], source_revision_id=rev['id'], source_line_id=line['id'],
            root_document_id=root[0], root_line_id=root[1], quantity_microunits=quantity,
            net_minor_units=net, tax_minor_units=tax, gross_minor_units=net+tax, **alloc.stored_proof(proof),
            facts_snapshot=sales.json_text(dict(line=lf.model_dump(mode='json'), document=work.facts(rev).model_dump(mode='json'),
                title=rev['title'], source_number=rev['number'])),
            created_at=header['updated_at'], created_by=s.actor.id, created_via=ctx.interface.value)
        require({k:v for k,v in actual.items() if k != 'id'} == expected, 'wrong captured allocation')
        snapshot = json.loads(actual['facts_snapshot'])
        require(read_line(snapshot['line']) == lf and read_facts(snapshot['document']) == work.facts(rev), 'snapshot types')
        projected = profiles[envelope['id']]
        require(all(projected[k] == getattr(lf,k) for k in ('item_id','unit_id','unit_factor_nanounits','unit_price_minor_units')),
                'captured line units/item/rate differ')
        require(projected['quantity_microunits'] == quantity and projected['base_quantity_microunits'] == base_quantity
                and projected['net_minor_units'] == net and projected['tax_minor_units'] == tax
                and projected['gross_minor_units'] == net+tax, 'allocated financial amounts differ')
        require(envelope['description'] == lf.description, 'source description')
        posted_line = SalesLineProfile.model_validate_json(projected['item_snapshot'])
        changed_representation = {'schema_version', 'pricing_basis', 'net_amount_minor_units', 'allocation_proof', 'origins'}
        if lf.pricing_basis == 'amount':
            changed_representation |= {'price_rule', 'price_basis_minor_units'}
        for field in type(lf.profile).model_fields:
            if field not in changed_representation:
                require(getattr(posted_line, field) == getattr(lf.profile, field),
                    'captured line classification differs: ' + field)
        require(posted_line.pricing_basis == ('allocated' if proof else 'amount' if lf.pricing_basis == 'amount' else 'unit'), 'line price basis')
        require(posted_line.allocation_proof == proof, 'sale and source allocation proofs differ')
        taxes = [row for row in data['pending']['sales_tax_components'] if row['document_line_id'] == envelope['id']]
        require(len(taxes) == len(lf.taxes), 'captured tax components missing')
        for posted_tax, source_tax in zip(taxes, lf.taxes):
            rule = source_tax.rule
            captured_tax = json.loads(posted_tax['component_snapshot'])
            require(posted_tax['tax_item_id'] == rule.id and posted_tax['agency_id'] == rule.agency.id
                and posted_tax['liability_account_id'] == rule.liability_account.id
                and posted_tax['rate_percent_millionths'] == rule.rate_percent_millionths
                and captured_tax['tax_item'] == rule.model_dump(mode='json', include={'id', 'label', 'version'})
                and captured_tax['agency'] == rule.agency.model_dump(mode='json')
                and captured_tax['liability_account'] == rule.liability_account.model_dump(mode='json')
                and posted_tax['taxable_minor_units'] == net
                and posted_tax['tax_minor_units'] == exact_cells[envelope['id'],rule.id], 'captured tax classification differs')
