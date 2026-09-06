"""Independent integrity checks before a work aggregate reaches persistence."""
import json
from pydantic import ValidationError
from bookflow.company import schema as c, work, journal_custom_fields as custom
from bookflow.company import sales_calculations as calc
from bookflow.company.work_tax_facts import read_facts, read_line
from bookflow.company import work_tax
from bookflow.core.errors import BookflowError
from bookflow.core.exact import INT64_MAX


def require(condition, problem):
    if not condition:
        raise BookflowError('E_INTERNAL', message='Invalid work aggregate: ' + problem)


def amount(value, *, positive=False, nullable=False):
    if nullable and value is None:
        return
    require(type(value) is int and (0 < value if positive else 0 <= value) and value <= INT64_MAX, 'invalid exact quantity or amount')


def validate(plan, s, ctx):
    try:
        return _validate(plan, s, ctx)
    except (ValidationError, json.JSONDecodeError) as exc:
        raise BookflowError('E_INTERNAL', message='Invalid work aggregate: malformed captured facts') from exc


def _validate(plan, s, ctx):
    data = plan.data
    if not data['changed']:
        return
    pending, headers = data['pending'], {row['id']: row for row in data['headers']}
    require(len(headers) == len(data['headers']), 'duplicate document identity')
    require(set(pending) == {table for table, _ in work.TABLE_KINDS}, 'unexpected or missing persistence table')
    indexed = {}
    for name, _ in work.TABLE_KINDS:
        key=work_tax.TABLE_KEYS.get(name,'id')
        indexed[name] = {row[key]: row for row in pending[name]}
        require(len(indexed[name]) == len(pending[name]), 'duplicate immutable identity')
        require(not work.rows(s, getattr(c, name), getattr(c, name).c[key].in_(indexed[name])), 'reused immutable identity')
    for name in work_tax.TABLE_KEYS:
        for row in pending[name]:
            require(row['document_id'] in headers,'unowned work tax fact')
            owner=headers[row['document_id']]
            require((row['created_at'],row['created_by'],row['created_via'])==(owner['updated_at'],s.actor.id,ctx.interface.value),'wrong work tax provenance')
            if 'tax_ordinal' in row:require(type(row['tax_ordinal']) is int and row['tax_ordinal']>0,'invalid tax ordinal')
    revisions = indexed['work_revisions']
    for name in ('work_tax_attributions','work_tax_attribution_lines'):
        require(all(row['revision_id'] in revisions for row in pending[name]),'orphan work tax revision')
    require(len(revisions) == len(headers), 'one new revision per changed document')
    for header in headers.values():
        require(header['kind'] in work.KINDS and header['current_revision_id'] in revisions, 'wrong document/revision kind')
        rev = revisions[header['current_revision_id']]
        require(rev['document_id'] == header['id'], 'revision belongs to another document')
        require(header['updated_by'] == s.actor.id and header['updated_via'] == ctx.interface.value, 'incorrect writer')
        old = data['before'].get(header['id'])
        if old:
            require(work.resolve(s, header['id'], header['kind']) == old, 'stale prior document')
            require(header['version'] == old['version'] + 1, 'nonconsecutive document version')
            require(all(header[key] == old[key] for key in ('id', 'kind', 'created_at', 'created_by', 'created_via', 'estimate_group_id')), 'changed immutable header facts')
            require(rev['supersedes_revision_id'] == old['current_revision_id'], 'wrong revision predecessor')
        else:
            require(header['version'] == 1 and rev['supersedes_revision_id'] is None, 'invalid new document')
            require(not work.rows(s, c.work_documents, c.work_documents.c.id == header['id']), 'reused document identity')
        require(rev['revision_number'] == header['version'], 'revision/version mismatch')
        require(all(rev[key] == header[key] for key in ('number', 'status', 'active')), 'current projection differs from revision')
        require(rev['audit_event_id'] == data['event'], 'wrong audit event')
        require(rev['created_by'] == s.actor.id and rev['created_via'] == ctx.interface.value and rev['created_at'] == header['updated_at'], 'wrong revision provenance')
        f = read_facts(rev['facts_snapshot'])
        require(rev['customer_id'] == f.profile.customer.id and rev['currency'] == s.company_info_row['home_currency'], 'wrong customer/currency')
        own_lines = [row for row in pending['work_lines'] if row['revision_id'] == rev['id']]
        require(len(own_lines) <= 200 and (bool(own_lines) or header['kind'] != 'estimate'), 'invalid line count')
        require(sorted(row['position'] for row in own_lines) == list(range(1, len(own_lines) + 1)), 'line order is not contiguous')
        require(len({row['line_id'] for row in own_lines}) == len(own_lines), 'repeated stable line')
        expected_cells=work_tax.validate(s,rev,own_lines,pending,require)
        for line in own_lines:
            require(line['document_id'] == header['id'], 'line from another document')
            require(line['created_by'] == s.actor.id and line['created_via'] == ctx.interface.value and line['created_at'] == rev['created_at'], 'wrong line provenance')
            lf = read_line(line['facts_snapshot'])
            require(all(line[key] == getattr(lf, key) for key in work.LINE_COLUMNS), 'line projection differs from typed facts')
            require(lf.item_id == lf.profile.item.id, 'wrong item identity')
            require(lf.unit_id == (lf.profile.unit.id if lf.profile.unit else None), 'wrong selected unit')
            require(lf.unit_factor_nanounits == (lf.profile.unit.factor_nanounits if lf.profile.unit else 1_000_000_000), 'wrong unit factor')
            for field in ('quantity_microunits', 'unit_factor_nanounits', 'base_quantity_microunits'):
                amount(getattr(lf, field), positive=True)
            for field in ('completed_quantity_microunits', 'net_minor_units', 'tax_minor_units', 'gross_minor_units'):
                amount(getattr(lf, field))
            for field in ('unit_price_minor_units', 'estimated_unit_cost_minor_units', 'estimated_cost_minor_units'):
                amount(getattr(lf, field), nullable=True)
            require(lf.completed_quantity_microunits <= lf.quantity_microunits, 'overcompleted quantity')
            require(header['kind'] == 'work_order' or lf.completed_quantity_microunits == 0, 'completion on quote')
            require(lf.base_quantity_microunits == calc.base_quantity(lf.quantity_microunits, lf.unit_factor_nanounits), 'wrong base quantity')
            if lf.pricing_basis == 'amount':
                require(lf.unit_price_minor_units is None and lf.markup_percent_millionths is None, 'amount mode has a competing rate')
            else:
                require(lf.unit_price_minor_units is not None and lf.net_minor_units == calc.extension(lf.quantity_microunits, lf.unit_price_minor_units), 'wrong rate extension')
                if lf.pricing_basis == 'markup':
                    require(lf.estimated_unit_cost_minor_units is not None and lf.markup_percent_millionths is not None, 'markup has no basis')
                    require(lf.unit_price_minor_units == calc.adjusted_price(lf.estimated_unit_cost_minor_units, lf.markup_percent_millionths), 'wrong cost markup')
                else:
                    require(lf.markup_percent_millionths is None, 'unused markup in other mode')
            expected_cost = calc.extension(lf.quantity_microunits, lf.estimated_unit_cost_minor_units) if lf.estimated_unit_cost_minor_units is not None else None
            require(lf.estimated_cost_minor_units == expected_cost, 'wrong estimated cost extension')
            taxable = f.profile.preferences.sales_tax_enabled and bool(lf.profile.tax_code and lf.profile.tax_code.taxable)
            exempt = f.profile.customer_tax_code is not None and not f.profile.customer_tax_code.taxable
            rules = f.profile.tax_rules if taxable and not exempt else []
            require(rules is not None and len(rules) == len(lf.taxes), 'incomplete quoted tax rules')
            for component, rule in zip(lf.taxes, rules):
                require(component.rule == rule, 'tax rule differs from captured header')
                require(component.taxable_minor_units == lf.net_minor_units and component.tax_minor_units == expected_cells[line['id'],rule.id], 'incorrect quoted tax')
            require(lf.tax_minor_units == calc.total(component.tax_minor_units for component in lf.taxes), 'wrong line tax total')
            require(lf.gross_minor_units == calc.total((lf.net_minor_units, lf.tax_minor_units)), 'wrong line gross')
            identity = indexed['work_line_identities'].get(line['line_id'])
            if identity is None:
                saved = work.rows(s, c.work_line_identities, c.work_line_identities.c.id == line['line_id'])
                require(len(saved) == 1, 'unknown stable line')
                identity = saved[0]
                require(old is not None and any(row['line_id'] == line['line_id'] for row in work.saved_lines(s, work.revision(s, old))), 'retired line identity returned')
            require(identity['document_id'] == header['id'], 'line identity belongs to another document')
        require(rev['net_minor_units'] == calc.total(line['net_minor_units'] for line in own_lines), 'wrong net total')
        require(rev['tax_minor_units'] == calc.total(line['tax_minor_units'] for line in own_lines), 'wrong tax total')
        require(rev['gross_minor_units'] == calc.total(line['gross_minor_units'] for line in own_lines), 'wrong gross total')
        semantic = work._semantic(rev, sorted(own_lines, key=lambda row: row['position']))
        work._state_invariants(header['kind'], semantic)
        work._accepted_group(s, header, header['status'])
        if old:
            previous = work.revision(s, old)
            prior = work._semantic(previous, work.saved_lines(s, previous))
            work._dependencies(s, old, prior, semantic)
        if header['status'] == 'accepted':
            require(rev['accepted_revision_id'] is not None and rev['accepted_at'] and rev['accepted_by'], 'missing acceptance evidence')
            if rev['accepted_revision_id'] != rev['id']:
                accepted = work.rows(s, c.work_revisions, c.work_revisions.c.id == rev['accepted_revision_id'])
                require(len(accepted) == 1 and accepted[0]['document_id'] == header['id'] and accepted[0]['status'] == 'accepted', 'acceptance belongs to another document')
        else:
            require(all(rev[key] is None for key in ('accepted_revision_id', 'accepted_at', 'accepted_by')), 'unaccepted revision claims acceptance')
    for identity in pending['work_line_identities']:
        require(identity['document_id'] in headers, 'unowned line identity')
        own = [line for line in pending['work_lines'] if line['line_id'] == identity['id']]
        require(len(own) == 1, 'new identity lacks exactly one birth line')
        if identity['root_line_id'] == identity['id']:
            require(identity['root_document_id'] == identity['document_id'], 'root owner mismatch')
        else:
            root = work.rows(s, c.work_line_identities, c.work_line_identities.c.id == identity['root_line_id'])
            require(len(root) == 1 and root[0]['document_id'] == identity['root_document_id'] and root[0]['root_line_id'] == root[0]['id'], 'missing or noncanonical billing root')
        if identity['source_line_id']:
            source = work.rows(s, c.work_lines, c.work_lines.c.id == identity['source_line_id'])
            require(len(source) == 1, 'missing source line')
            linked = [link for link in pending['work_links'] if link['destination_document_id'] == identity['document_id']]
            require(len(linked) == 1 and source[0]['revision_id'] == linked[0]['source_revision_id'], 'line source differs from selected source revision')
            if linked[0]['relation'] == 'estimate_work_order':
                parent = work.rows(s, c.work_line_identities, c.work_line_identities.c.id == source[0]['line_id'])[0]
                require((identity['root_document_id'], identity['root_line_id']) == (parent['root_document_id'], parent['root_line_id']), 'conversion lost its billing root')
            else:
                require(identity['root_line_id'] == identity['id'], 'independent copy reused consumption')
    for link in pending['work_links']:
        require(link['destination_document_id'] in headers and link['destination_revision_id'] in revisions, 'unowned destination link')
        require(revisions[link['destination_revision_id']]['document_id'] == link['destination_document_id'], 'link birth revision owner')
        source = work.rows(s, c.work_revisions, c.work_revisions.c.id == link['source_revision_id'])
        require(len(source) == 1 and source[0]['document_id'] == link['source_document_id'], 'link source revision owner')
        require(link['source_version'] == source[0]['revision_number'], 'link source version differs')
        require(link['created_by'] == s.actor.id and link['created_via'] == ctx.interface.value, 'wrong link provenance')
        require((link['conversion_key_hash'] is None) == (link['relation'] == 'copy') and (link['request_hash'] is None) == (link['relation'] == 'copy'), 'missing or unexpected durable key')
    for custom_plan in data['customs']:
        owner = custom_plan.owner_plan
        require(owner.record_id in headers, 'custom fields belong to another document')
        rev = revisions[headers[owner.record_id]['current_revision_id']]
        custom.validate(s.company, custom_plan, owner.record_id, json.loads(rev['custom_fields_snapshot']), record_type=headers[owner.record_id]['kind'])
    destination = headers[plan.preview.id]
    destination_rev = revisions[destination['current_revision_id']]
    destination_lines = sorted((line for line in pending['work_lines'] if line['revision_id'] == destination_rev['id']), key=lambda row: row['position'])
    actual_semantic = work._semantic(destination_rev, destination_lines)
    new_ids = indexed['work_line_identities']
    for line in actual_semantic['lines']:
        if line['line_id'] in new_ids:
            line['line_id'] = None
    require(actual_semantic == data['semantic'], 'stored aggregate differs from resolved intent')
    # A conversion source receives only an immutable link revision, never a
    # hidden scope, acceptance, custom-value or quantity edit.
    for header in headers.values():
        if header['id'] == destination['id']:
            continue
        old = data['before'].get(header['id'])
        require(old is not None, 'unexpected additional new document')
        old_rev = work.revision(s, old)
        new_rev = revisions[header['current_revision_id']]
        new_lines = sorted((line for line in pending['work_lines'] if line['revision_id'] == new_rev['id']), key=lambda row: row['position'])
        require([read_line(row['facts_snapshot']).schema_version for row in new_lines]==[read_line(row['facts_snapshot']).schema_version for row in work.saved_lines(s,old_rev)],'conversion changed source line fact version')
        require(work_tax.normalized(work._semantic(old_rev, work.saved_lines(s, old_rev))) == work_tax.normalized(work._semantic(new_rev, new_lines)), 'conversion rewrote source facts')
        require(all(old_rev[key] == new_rev[key] for key in ('accepted_revision_id', 'accepted_at', 'accepted_by')), 'conversion changed source acceptance')
    require(plan.preview.id in headers and plan.preview.revision.id == headers[plan.preview.id]['current_revision_id'], 'preview shows another document')
    require(plan.preview.gross_minor_units == revisions[plan.preview.revision.id]['gross_minor_units'], 'preview total differs')
    # Re-resolve the original typed intent; generated identities/provenance are not
    # compared, but none of the independently checked business facts may diverge.
    again = work.prepare(s, ctx, data['input'], data['kind'], data['operation'])
    require(again.data['changed'], 'pending change is not the supplied intent')
    actual, expected = data['semantic'], again.data['semantic']
    if data['operation'] == 'complete' and 'actual_end' not in data['input'].model_fields_set:
        actual = json.loads(work.json_text(actual)); expected = json.loads(work.json_text(expected))
        actual['facts']['actual_end'] = expected['facts']['actual_end']
    require(actual == expected and data['sequence'] == again.data['sequence'], 'pending facts differ from typed intent')
    require(plan.preview.facts_fingerprint == again.preview.facts_fingerprint, 'preview fingerprint differs')
