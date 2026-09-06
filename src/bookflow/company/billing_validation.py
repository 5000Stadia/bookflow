"""Independent source ownership and allocation checks before billing persistence."""
import json
from bookflow.company import schema as c, work, sales, billing_queries as query
from bookflow.company.work_facts import WorkFacts, WorkLineFacts
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
                    expected.append(dict(row, document_line_id=line['id'], revision_id=line['revision_id']))
    actual = data.get('billing_allocations', [])
    strip = lambda row: {k: v for k, v in row.items() if k not in ('id', 'created_at', 'created_by', 'created_via')}
    require(sorted((strip(x) for x in actual), key=str) == sorted((strip(x) for x in expected), key=str), 'correction changed or lost source allocations')
    seen = set()
    for row in actual:
        root = row['root_document_id'], row['root_line_id']
        require(root not in seen, 'same root repeated in a sale')
        seen.add(root)
        require(not query.active_allocations(s, [root], excluding=data['header']['id']), 'root already consumed by another sale')
        require(row['created_by'] == data['header']['updated_by'] and row['created_via'] == data['header']['updated_via']
            and row['created_at'] == data['header']['updated_at'], 'allocation provenance')


def validate(plan, s, ctx):
    if not plan.data['changed']:
        return
    from bookflow.company import billing
    from bookflow.company.sales_validation import validate as validate_sale
    validate_sale(plan, s, ctx)
    data = plan.data
    inp, kind, dest = data['input'], data['kind'], data['destination']
    source, rev, selected = billing.source_selection(s, inp, kind)
    header, created = data['header'], data['pending']['transaction_revisions'][0]
    wh, wb, wp = data['work_header'], data['work_before'], data['work_pending']
    require(wb == source and wh['id'] == source['id'] and wh['version'] == source['version'] + 1, 'wrong source/version')
    require(all(wh[k] == source[k] for k in source if k not in ('version', 'updated_at', 'updated_by', 'updated_via', 'current_revision_id')), 'source header facts changed')
    require(wh['updated_at'] == header['updated_at'] and wh['updated_by'] == s.actor.id and wh['updated_via'] == ctx.interface.value, 'source provenance')
    require(len(wp['work_revisions']) == 1 and not wp['work_line_identities'] and not wp['work_links'], 'unexpected operational effects')
    newrev = wp['work_revisions'][0]
    require(newrev['id'] == wh['current_revision_id'] and newrev['document_id'] == source['id'], 'source revision ownership')
    require(newrev['revision_number'] == rev['revision_number'] + 1 and newrev['supersedes_revision_id'] == rev['id'], 'source revision ancestry')
    require(newrev['audit_event_id'] == data['event'], 'source event')
    unchanged = set(rev) - {'id', 'revision_number', 'supersedes_revision_id', 'audit_event_id', 'created_at', 'created_by', 'created_via'}
    require(all(newrev[k] == rev[k] for k in unchanged), 'billing rewrote source facts')
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
    require(len(allocs) == len(selected) == len(envelopes), 'missing selected allocation')
    profiles = {row['document_line_id']: row for row in data['pending']['sales_line_profiles']}
    roots = set()
    for actual, envelope, (line, root, lf) in zip(allocs, envelopes, selected):
        require(root not in roots and not query.active_allocations(s, [root]), 'duplicate active consumption')
        roots.add(root)
        expected = dict(transaction_id=header['id'], revision_id=created['id'], document_line_id=envelope['id'],
            source_document_id=source['id'], source_revision_id=rev['id'], source_line_id=line['id'],
            root_document_id=root[0], root_line_id=root[1], quantity_microunits=lf.quantity_microunits,
            net_minor_units=lf.net_minor_units, tax_minor_units=lf.tax_minor_units, gross_minor_units=lf.gross_minor_units,
            facts_snapshot=sales.json_text(dict(line=lf.model_dump(mode='json'), document=work.facts(rev).model_dump(mode='json'),
                title=rev['title'], source_number=rev['number'])),
            created_at=header['updated_at'], created_by=s.actor.id, created_via=ctx.interface.value)
        require({k:v for k,v in actual.items() if k != 'id'} == expected, 'wrong captured allocation')
        snapshot = json.loads(actual['facts_snapshot'])
        require(WorkLineFacts.model_validate(snapshot['line']) == lf and WorkFacts.model_validate(snapshot['document']) == work.facts(rev), 'snapshot types')
        projected = profiles[envelope['id']]
        require(all(projected[k] == getattr(lf, k) for k in ('item_id', 'quantity_microunits', 'unit_id', 'unit_factor_nanounits',
            'base_quantity_microunits', *sales.MONEY_COLUMNS)), 'financial line differs from source')
        require(envelope['description'] == lf.description, 'source description')
