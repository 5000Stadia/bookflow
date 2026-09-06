"""Independent checks of commercial intent, attribution and exact reversals."""
from collections import Counter
import json
from pydantic import ValidationError

from bookflow.company import schema as c, document_effects as effects
from bookflow.company import tax_attribution as tax_facts
from bookflow.company import journal_custom_fields as custom, sales_calculations as calc
from bookflow.company.sales_facts import SalesProfile, SalesLineProfile, SalesTaxComponent
from bookflow.core.errors import BookflowError
from bookflow.core.exact import INT64_MAX


def require(condition, problem):
    if not condition:
        raise BookflowError('E_INTERNAL', message='Invalid sales aggregate: ' + problem)


def amount(value, *, positive=False):
    require(type(value) is int and (0 < value if positive else 0 <= value) and value <= INT64_MAX, 'invalid exact amount')
    return value


def validate(plan, s, ctx):
    try:
        return _validate(plan, s, ctx)
    except (ValidationError, json.JSONDecodeError) as exc:
        raise BookflowError('E_INTERNAL', message='Invalid sales aggregate: malformed captured facts') from exc


def _validate(plan, s, ctx):
    from bookflow.company import sales
    data = plan.data
    if not data['changed']:
        return
    header, pending, old = data['header'], data['pending'], data['before']
    operation, document_type = data['operation'], data['document_type']
    require(document_type in ('invoice', 'sales_receipt') and header['type'] == document_type, 'wrong document type')
    require(operation in ('post', 'update', 'void'), 'wrong operation')
    require(header['updated_by'] == s.actor.id and header['updated_via'] == ctx.interface.value, 'header writer attribution')
    require(set(pending) == {table for table, _, _ in sales.TABLE_KINDS}, 'incomplete graph')
    indexed = {}
    for name, _, key in sales.TABLE_KINDS:
        values = pending[name]
        indexed[name] = {row[key]: row for row in values}
        require(len(indexed[name]) == len(values), 'duplicate history identity')
        require(all(row['transaction_id'] == header['id'] for row in values), 'cross-document history')
        require(all(row['created_by'] == s.actor.id and row['created_at'] == header['updated_at']
                    and row['created_via'] == header['updated_via'] for row in values), 'incorrect creation provenance')
    if old:
        current = sales.resolve(s, getattr(data['input'], document_type), document_type)
        require(current == old and current['id'] == header['id'] and current['status'] == 'posted', 'stale or wrong prior document')
        require(header['version'] == old['version'] + 1, 'wrong document version')
        require(all(header[key] == old[key] for key in ('created_at', 'created_by', 'created_via')), 'changed creation provenance')
    else:
        require(operation == 'post' and header['version'] == 1, 'invalid new document')
        require(not effects.rows(s, c.transactions, c.transactions.c.id == header['id']), 'reused document id')
    batches = pending['posting_batches']
    business = [batch for batch in batches if batch['kind'] != 'reversal']
    inverses = [batch for batch in batches if batch['kind'] == 'reversal']
    require(len(inverses) == (1 if old else 0), 'wrong reversal count')
    require(len(business) == (0 if operation == 'void' else 1), 'wrong business batch count')
    require(all(batch['audit_event_id'] == data['event'] for batch in batches), 'wrong posting event')
    sales.journals.open_dates(s, [batch['effective_date'] for batch in batches])
    legs = pending['posting_lines']
    sources = pending['posting_line_sources']
    require(all(leg['batch_id'] in indexed['posting_batches'] for leg in legs), 'unowned posting line')
    require(all(source['posting_line_id'] in indexed['posting_lines'] for source in sources), 'unowned source')
    currency = s.company.conn.execute(c.company_info.select()).mappings().one()['home_currency']
    for batch in batches:
        own = [leg for leg in legs if leg['batch_id'] == batch['id']]
        require(bool(own), 'empty batch')
        require(sorted(leg['line_no'] for leg in own) == list(range(1, len(own) + 1)), 'non-contiguous batch lines')
        for leg in own:
            debit, credit = amount(leg['debit_minor_units']), amount(leg['credit_minor_units'])
            require(bool(debit) != bool(credit), 'a posting must have exactly one positive side')
            require(leg['currency'] == currency, 'foreign posting in domestic sale')
            require(all(leg[field] is None for field in ('original_minor_units', 'original_currency', 'rate_used', 'rate_source')),
                    'foreign facts in domestic sale')
            attribution = [source for source in sources if source['posting_line_id'] == leg['id']]
            require(bool(attribution), 'unattributed posting')
            require(all(source['currency'] == currency for source in attribution), 'source currency differs')
            require(calc.total(amount(source['amount_minor_units'], positive=True) for source in attribution) == debit + credit,
                    'source amounts do not reconcile')
        require(calc.total(leg['debit_minor_units'] for leg in own) == calc.total(leg['credit_minor_units'] for leg in own), 'unbalanced batch')
    for inverse in inverses:
        originals = effects.rows(s, c.posting_batches, c.posting_batches.c.id == inverse['reverses_batch_id'])
        require(len(originals) == 1, 'missing reversed batch')
        target = originals[0]
        require(target['transaction_id'] == header['id'] and target['kind'] != 'reversal', 'wrong reversed batch')
        require(target['revision_id'] == old['current_revision_id'], 'not reversing current business revision')
        require(inverse['revision_id'] == target['revision_id'] and inverse['effective_date'] == target['effective_date']
                and inverse['replaces_batch_id'] is None, 'changed reversal attribution or date')
        old_legs = effects.rows(s, c.posting_lines, c.posting_lines.c.batch_id == target['id'])
        reversed_legs = [leg for leg in legs if leg['batch_id'] == inverse['id']]
        require(len(old_legs) == len(reversed_legs), 'incomplete reversal')
        by_old = {leg['reversed_line_id']: leg for leg in reversed_legs}
        require(len(by_old) == len(old_legs), 'duplicate reversed line')
        for original in old_legs:
            require(original['id'] in by_old, 'missing reversed line')
            actual = by_old[original['id']]
            expected = dict(original, id=actual['id'], batch_id=inverse['id'], created_at=actual['created_at'],
                created_by=actual['created_by'], created_via=actual['created_via'], debit_minor_units=original['credit_minor_units'],
                credit_minor_units=original['debit_minor_units'], reversed_line_id=original['id'])
            require(actual == expected, 'reversal is not the exact inverse')
            previous_sources = effects.rows(s, c.posting_line_sources, c.posting_line_sources.c.posting_line_id == original['id'])
            actual_sources = [source for source in sources if source['posting_line_id'] == actual['id']]
            by_source = {source['reversed_source_id']: source for source in actual_sources}
            require(len(by_source) == len(actual_sources) == len(previous_sources), 'incomplete reversed source set')
            require(calc.total(source['amount_minor_units'] for source in previous_sources) == original['debit_minor_units'] + original['credit_minor_units'],
                    'corrupt original attribution')
            for source in previous_sources:
                require(source['id'] in by_source, 'missing reversed source')
                new = by_source[source['id']]
                expected_source = dict(source, id=new['id'], posting_line_id=actual['id'], reversed_source_id=source['id'],
                    created_at=new['created_at'], created_by=new['created_by'], created_via=new['created_via'])
                require(new == expected_source, 'reversal changed its source')
    from bookflow.company.billing_validation import validate_sale_allocations
    validate_sale_allocations(plan, s)
    if operation == 'void':
        require(all(not pending[name] for name in ('transaction_revisions', 'document_line_identities', 'document_lines',
            'sales_profiles', 'sales_line_profiles', 'sales_tax_components', 'sales_tax_line_keys',
            'sales_tax_attributions', 'sales_tax_attribution_lines')), 'void created commercial history')
        require(header['status'] == 'voided' and header['current_revision_id'] == old['current_revision_id']
                and header['void_posting_batch_id'] == inverses[0]['id'], 'wrong void header')
        require(bool(ctx.reason and ctx.reason.strip()) and header['void_reason'] == ctx.reason.strip()
                and header['voided_by'] == s.actor.id and header['voided_at'] == header['updated_at']
                and header['number'] == old['number'], 'wrong void reason or provenance')
        return
    require(len(pending['transaction_revisions']) == len(pending['sales_profiles']) == 1, 'wrong revision/profile count')
    revision, row = pending['transaction_revisions'][0], pending['sales_profiles'][0]
    if document_type == 'sales_receipt' and operation == 'update':
        from bookflow.company.sales_models import money
        received = data['input'].amount_received
        linked = s.company.conn.execute(c.work_billing_allocations.select().with_only_columns(
            c.work_billing_allocations.c.id).where(
                c.work_billing_allocations.c.transaction_id == header['id']).limit(1)).first()
        prior_gross = s.company.conn.execute(c.transaction_revisions.select().with_only_columns(
            c.transaction_revisions.c.total_minor_units).where(
                c.transaction_revisions.c.id == current['current_revision_id'],
                c.transaction_revisions.c.transaction_id == current['id'])).scalar_one()
        if linked and revision['total_minor_units'] != prior_gross:
            require(received is not None, 'changed linked receipt lacks received total confirmation')
        if received is not None:
            require(money(received, currency, 'amount_received').minor_units == revision['total_minor_units'],
                    'receipt received total differs from gross')
    profile = SalesProfile.model_validate_json(row['profile_snapshot'])
    require(header['status'] == 'posted' and header['current_revision_id'] == revision['id'], 'wrong current header')
    require(revision['revision_number'] == (data['old_revision']['revision_number'] + 1 if old else 1)
            and revision['supersedes_revision_id'] == (old['current_revision_id'] if old else None)
            and header['number'] == revision['number'], 'wrong commercial revision chain')
    require(all(header[key] is None for key in ('voided_at', 'voided_by', 'void_reason', 'void_posting_batch_id')), 'void metadata on posted sale')
    require(revision['audit_event_id'] == data['event'] and revision['currency'] == currency, 'wrong revision provenance or currency')
    require(revision['name_type'] == 'customer' and revision['name_id'] == profile.customer.id == row['customer_id'], 'wrong customer')
    require(row['revision_id'] == revision['id'] and row['type'] == document_type
            and row['control_account_id'] == profile.control_account.id and row['due_date'] == profile.due_date, 'profile columns differ from facts')
    require(business[0]['kind'] == ('replacement' if old else 'original') and business[0]['revision_id'] == revision['id']
            and business[0]['effective_date'] == revision['date'], 'wrong business effect')
    require(business[0]['replaces_batch_id'] == (inverses[0]['reverses_batch_id'] if old else None), 'wrong replacement predecessor')
    expected_type = {'accounts_receivable'} if document_type == 'invoice' else {'bank', 'other_current_asset'}
    require(profile.control_account.type in expected_type, 'wrong control account type')
    envelopes, lines, components = pending['document_lines'], pending['sales_line_profiles'], pending['sales_tax_components']
    require(1 <= len(envelopes) <= 200 and len(envelopes) == len(lines), 'commercial line count')
    require({line['document_line_id'] for line in lines} == set(indexed['document_lines']), 'line-profile ownership')
    require(all(line['revision_id'] == revision['id'] for line in envelopes + lines + components), 'cross-revision commercial facts')
    require(sorted(line['position'] for line in envelopes) == list(range(1, len(envelopes)+1)), 'line ordering')
    require(all(component['document_line_id'] in indexed['document_lines'] for component in components), 'unowned tax component')
    line_profiles = indexed['sales_line_profiles']
    expected_cells = tax_facts.validate_sales(s, header, revision, profile, pending, require)
    expected_legs = []
    semantic_lines = []
    new_ids = set(indexed['document_line_identities'])
    used_ids = [line['line_id'] for line in envelopes]
    require(len(set(used_ids)) == len(used_ids) and new_ids <= set(used_ids), 'duplicate or unused commercial line identity')
    old_ids = {line['line_id'] for line in sales.saved_lines(s, data['old_revision'])} if old else set()
    require(set(used_ids) - new_ids <= old_ids, 'unowned or retired line identity')
    require(not effects.rows(s, c.document_line_identities, c.document_line_identities.c.id.in_(new_ids)), 'reused new line identity')
    for envelope in sorted(envelopes, key=lambda line: line['position']):
        line = line_profiles[envelope['id']]
        facts = SalesLineProfile.model_validate_json(line['item_snapshot'])
        require(envelope['kind'] == 'sale' and all(envelope[field] is None for field in ('account_id', 'account_snapshot', 'side', 'amount_minor_units',
            'original_minor_units', 'original_currency', 'rate_used', 'rate_source')), 'journal fields on a commercial line')
        require(envelope['currency'] == currency and envelope['name_type'] == 'customer'
                and envelope['name_id'] == profile.customer.id and envelope['party_name'] == profile.customer.label, 'line party/currency')
        require(envelope['class_id'] == (facts.class_id.id if facts.class_id else None)
                and envelope['class_name'] == (facts.class_id.label if facts.class_id else None), 'line class facts')
        require(line['item_id'] == facts.item.id and facts.income_account.type in ('income', 'other_income'), 'item/account facts')
        for name in ('quantity_microunits', 'base_quantity_microunits', 'unit_factor_nanounits'):
            if line[name] is not None or facts.pricing_basis != 'allocated' or name == 'unit_factor_nanounits':
                amount(line[name], positive=True)
        for name in sales.MONEY_COLUMNS:
            if name != 'unit_price_minor_units' or line[name] is not None or facts.pricing_basis == 'unit':
                amount(line[name])
        require(line['unit_id'] == (facts.unit.id if facts.unit else None)
                and line['unit_factor_nanounits'] == (facts.unit.factor_nanounits if facts.unit else 1_000_000_000), 'unit facts')
        if facts.pricing_basis != 'allocated':
            require(line['base_quantity_microunits'] == calc.base_quantity(line['quantity_microunits'], line['unit_factor_nanounits']), 'base quantity')
        require(line['pricing_basis'] == facts.pricing_basis, 'price basis projection')
        if facts.pricing_basis == 'allocated':
            from bookflow.company.billing_checks import numeric_projection
            require(data.get('billing_source') or (data['before'] and any(
                a['document_line_id'] == envelope['id'] for a in data.get('billing_allocations',[]))),
                'ordinary sale cannot create an allocation proof')
            numeric_projection(s,line,facts.allocation_proof)
        elif facts.pricing_basis == 'amount':
            require(line['unit_price_minor_units'] is None and line['net_minor_units'] == facts.net_amount_minor_units, 'amount extension')
        else:
            require(line['net_minor_units'] == calc.extension(line['quantity_microunits'], line['unit_price_minor_units']), 'line extension')
        own_taxes = sorted((comp for comp in components if comp['document_line_id'] == envelope['id']),
            key=lambda comp: json.loads(comp['component_snapshot'])['position'])
        taxable = profile.preferences.sales_tax_enabled and bool(facts.tax_code and facts.tax_code.taxable)
        exempt = profile.customer_tax_code is not None and not profile.customer_tax_code.taxable
        rules = profile.tax_rules if taxable and not exempt else []
        require(rules is not None and len(own_taxes) == len(rules), 'tax-rule coverage')
        for position, (component, rule) in enumerate(zip(own_taxes, rules), 1):
            captured = SalesTaxComponent.model_validate_json(component['component_snapshot'])
            require(captured.position == position and captured.tax_item.id == rule.id
                    and captured.agency == rule.agency and captured.liability_account == rule.liability_account, 'component captured facts')
            require(component['tax_item_id'] == rule.id and component['agency_id'] == rule.agency.id
                    and component['liability_account_id'] == rule.liability_account.id
                    and component['rate_percent_millionths'] == rule.rate_percent_millionths, 'component references/rate')
            require(component['taxable_minor_units'] == line['net_minor_units']
                    and component['tax_minor_units'] == (expected_cells[envelope['id'], rule.id] if expected_cells is not None else calc.tax(line['net_minor_units'], rule.rate_percent_millionths)), 'tax arithmetic')
        require(line['tax_minor_units'] == calc.total(comp['tax_minor_units'] for comp in own_taxes)
                and line['gross_minor_units'] == calc.total((line['net_minor_units'], line['tax_minor_units'])), 'gross arithmetic')
        attribution = tuple([(envelope['id'], None, line['net_minor_units'])] if line['net_minor_units'] else []) + tuple(
            (envelope['id'], comp['id'], comp['tax_minor_units']) for comp in own_taxes if comp['tax_minor_units'])
        def expect(account, debit, credit, allocation):
            if debit or credit:
                expected_legs.append((account.id, debit, credit, envelope['class_id'], envelope['class_name'], envelope['description'],
                    sales.json_text(account.model_dump()), tuple(sorted(allocation, key=str))))
        expect(profile.control_account, line['gross_minor_units'], 0, attribution)
        expect(facts.income_account, 0, line['net_minor_units'], [(envelope['id'], None, line['net_minor_units'])])
        for component in own_taxes:
            captured = SalesTaxComponent.model_validate_json(component['component_snapshot'])
            expect(captured.liability_account, 0, component['tax_minor_units'], [(envelope['id'], component['id'], component['tax_minor_units'])])
        semantic_lines.append(sales._line_semantic(dict(envelope, **{k: v for k, v in line.items() if k not in envelope},
            line_id=None if envelope['line_id'] in new_ids else envelope['line_id'], profile=facts)))
    actual_legs = []
    for leg in (leg for leg in legs if leg['batch_id'] == business[0]['id']):
        require(leg['reversed_line_id'] is None and leg['name_type'] == 'customer' and leg['name_id'] == profile.customer.id
                and leg['party_name'] == profile.customer.label, 'business posting dimensions')
        own_sources = [source for source in sources if source['posting_line_id'] == leg['id']]
        require(all(source['revision_id'] == revision['id'] and source['reversed_source_id'] is None for source in own_sources), 'business attribution history')
        actual_legs.append((leg['account_id'], leg['debit_minor_units'], leg['credit_minor_units'], leg['class_id'], leg['class_name'],
            leg['description'], leg['account_snapshot'], tuple(sorted(((source['document_line_id'], source['tax_component_id'],
                source['amount_minor_units']) for source in own_sources), key=str))))
    require(Counter(actual_legs) == Counter(expected_legs), 'postings differ from commercial components')
    require(row['subtotal_minor_units'] == calc.total(line['net_minor_units'] for line in lines)
            and row['tax_minor_units'] == calc.total(line['tax_minor_units'] for line in lines)
            and revision['total_minor_units'] == calc.total(line['gross_minor_units'] for line in lines), 'commercial totals')
    custom_plan = data['custom_plan']
    require(custom_plan is not None and (data.get('billing_source') is not None or json.loads(custom_plan.patch_json) == data['input'].custom_fields.root)
            and custom_plan.refresh == data['input'].refresh_defaults, 'custom intent mismatch')
    custom.validate(s.company, custom_plan, header['id'], json.loads(revision['custom_fields_snapshot']), record_type=document_type)
    actual = dict(date=revision['date'], number=revision['number'], memo=revision['memo'], issuer=json.loads(revision['issuer_snapshot']),
        profile=tax_facts.semantic_profile(profile), lines=semantic_lines, custom_fields=sales._custom_semantic(json.loads(revision['custom_fields_snapshot'])))
    expected = sales.commercial(s, data['input'], document_type, old, data['old_revision'], document_id=header['id'], billing_source=data.get('billing_source'))
    sales._posting_accounts_active(s, expected)
    require(data['sequence'] == expected['sequence'], 'wrong number allocation')
    require(actual == expected['semantic'] == data['semantic'], 'derived facts differ from original command intent')
    require(data.get('settlement_commercial_fingerprint', plan.preview.facts_fingerprint) == expected['fingerprint'],
            'incorrect commercial preview fingerprint')
    if data.get('settlement_commercial_fingerprint') is not None:
        require(plan.preview.settlement is not None and plan.preview.settlement.facts_fingerprint == plan.preview.facts_fingerprint,
                'incorrect composite settlement fingerprint')
    require(plan.preview.revision.total_minor_units == revision['total_minor_units'], 'incorrect rendered total')
