"""Whole-source-line conversion into the shared immutable sales ledger."""
from copy import deepcopy
import hashlib
import json
import sqlalchemy as sa
from bookflow.company import schema as c, work, sales, sales_defaults as defaults, sales_calculations as calc
from bookflow.company import billing_queries as query, document_effects as effects, journal_custom_fields as custom
from bookflow.company.sales_facts import SalesProfile, SalesLineProfile, Origin
from bookflow.company.sales_models import InvoiceUpdateInput, SalesReceiptUpdateInput, _invalid, money
from bookflow.company.sales_outputs import SalesWriteOutput
from bookflow.core import audit
from bookflow.core.ids import new_id
from bookflow.core.errors import BookflowError
from bookflow.core.registry import Plan, Applied, Touched
from bookflow.hub.access import require_resource
from bookflow.company import work_preferences as policy
from bookflow.company import tax_attribution as tax_facts


def dependency(problem, **details):
    raise BookflowError('E_WORK_DEPENDENCY', details=dict(problem=problem, **details))


def hashes(inp, kind, destination):
    key = hashlib.sha256(inp.conversion_key.encode()).hexdigest()
    intent = dict(kind=kind, destination=destination,
        input=inp.model_dump(mode='json', exclude={'expected_facts_fingerprint'}))
    return key, hashlib.sha256(sales.json_text(intent).encode()).hexdigest()


def source_selection(s, inp, kind):
    header = work.resolve(s, getattr(inp, kind), kind)
    work._version(s, header, inp.expected_version)
    if header['status'] == 'voided':
        # Before the availability refusal: "inactive" is a state work comes back from, and a
        # void is not, so the two must not answer with the same code.
        dependency('a voided ' + kind.replace('_', ' ') + ' cannot be billed', source_id=header['id'])
    if not header['active']:
        raise BookflowError('E_INACTIVE_REFERENCE', details={'record_type': kind, 'record_id': header['id']})
    owner = query.current_owner(s, header)
    if owner['id'] != header['id']:
        dependency('bill the work order associated with this estimate', destination_id=owner['id'])
    if kind == 'estimate' and header['status'] != 'accepted':
        dependency('accept the estimate before billing', source_id=header['id'])
    if kind == 'work_order' and header['status'] == 'cancelled':
        dependency('cancelled work cannot be billed', source_id=header['id'])
    rev = work.revision(s, header)
    lines = work.saved_lines(s, rev)
    identities = query.root_identities(s, header)
    policy.check_selection(s, inp, header, rev, lines, identities)
    from bookflow.company.billing_selection import select
    return header, rev, select(s, inp, header, rev, lines, identities)


def posting_eligibility(s, source, selected):
    profile = work.facts(source).profile
    warnings = work._carry_warnings(s, source, [row for row, _, _ in selected])
    warnings = [w.replace('for non-posting work', 'as descriptive history') for w in warnings]
    info = dict(s.company.conn.execute(c.company_info.select()).mappings().one())
    if info['sales_tax_liability_basis'] != 'invoice_date':
        raise _invalid('sales_tax_liability_basis', 'posting work requires invoice-date tax recognition')
    if info['sales_tax_enabled'] != profile.preferences.sales_tax_enabled:
        exempt = profile.customer_tax_code is not None and not profile.customer_tax_code.taxable
        if any(lf.profile.tax_code and lf.profile.tax_code.taxable and not exempt for _, _, lf in selected):
            raise _invalid('sales_tax_enabled', 'current tax policy conflicts with the captured taxable source facts')
        warnings.append('Current tax enablement differs; preserving captured exempt/non-taxable source facts.')
    defaults._row(s.company, 'customer', profile.customer.id)
    if profile.sales_tax_item:
        current = defaults._row(s.company, 'sales_tax_item', profile.sales_tax_item.id)
        if current['type'] not in ('sales_tax_item', 'sales_tax_group'):
            raise _invalid('sales_tax_item', 'captured tax item no longer has an eligible type')
    for row, root, lf in selected:
        item = defaults._row(s.company, 'item', lf.item_id)
        if item['type'] != lf.profile.item_type or not item['sales_enabled']:
            raise _invalid('item', 'current selling-item type differs from the quoted type')
        if item['type'] == 'other_charge' and item['other_charge_percent_millionths'] is not None:
            raise _invalid('item', 'percentage charges cannot replace captured fixed-charge work')
        defaults._account(s.company, lf.profile.income_account.id, 'income_account', {'income', 'other_income'})
        if item['income_account_id'] != lf.profile.income_account.id:
            warnings.append(f"{lf.item_id}: retaining captured income account despite current item mapping change")
        for component in lf.taxes:
            rule = component.rule
            tax = defaults._row(s.company, 'sales_tax_item', rule.id)
            vendor = defaults._row(s.company, 'agency', rule.agency.id)
            if tax['type'] != 'sales_tax_item' or not vendor['is_tax_agency']:
                raise _invalid('sales_tax_item', 'captured tax item or agency is no longer eligible')
            defaults._account(s.company, rule.liability_account.id, 'sales_tax_item', {'other_current_liability'}, role='sales_tax_payable')
            if tax['liability_account_id'] != rule.liability_account.id or tax['tax_agency_vendor_id'] != rule.agency.id:
                warnings.append(f'{rule.id}: retaining captured liability account and agency despite current mapping change')
    return warnings


def financial_profile(s, inp, document_type, source):
    captured = work.facts(source).profile
    if document_type == 'invoice':
        selected = inp.ar_account
        if selected is None:
            found = s.company.conn.execute(sa.select(c.accounts.c.id).where(c.accounts.c.type == 'accounts_receivable', c.accounts.c.active.is_(True))).scalars().all()
            if len(found) != 1:
                raise _invalid('ar_account', 'select an active AR account when there is not exactly one')
            selected = found[0]
        control = defaults._account(s.company, selected, 'ar_account', {'accounts_receivable'})
        financial = {key: getattr(inp, key) for key in ('ar_account', 'terms', 'due_date') if key in inp.model_fields_set}
        ordinary = InvoiceUpdateInput(invoice='billing', date=inp.date, **financial)
    else:
        control = defaults._account(s.company, inp.deposit_to, 'deposit_to', {'bank', 'other_current_asset'})
        row = defaults._row(s.company, 'account', control.id)
        if control.type != 'bank' and row['system_role'] != 'undeposited_funds':
            raise _invalid('deposit_to', 'select bank or system Undeposited Funds')
        financial = {key: getattr(inp, key) for key in ('payment_method', 'payment_reference') if key in inp.model_fields_set}
        ordinary = SalesReceiptUpdateInput(sales_receipt='billing', date=inp.date, deposit_to=control.id,
            use_defaults=[] if 'payment_method' in financial else ['payment_method'], **financial)
    original = SalesProfile(**captured.model_dump(), control_account=control)
    profile, warnings = defaults.resolve_header(s, ordinary, document_type, previous=original, old_date=None)
    # Selected financial references must be eligible even if their identity equals a saved reference.
    if 'terms' in inp.model_fields_set and inp.terms:
        defaults._row(s.company, 'terms', inp.terms)
    if document_type == 'sales_receipt' and profile.payment_method:
        defaults._row(s.company, 'payment_method', profile.payment_method.id)
    return profile, warnings


def resolved_line(lf, proof=None):
    facts = lf.profile.model_dump()
    facts.update(schema_version=2 if lf.pricing_basis == 'amount' else 1,
        pricing_basis='amount' if lf.pricing_basis == 'amount' else 'unit',
        net_amount_minor_units=lf.net_minor_units if lf.pricing_basis == 'amount' else None)
    if lf.pricing_basis == 'amount':
        facts['price_rule'] = None
        facts['price_basis_minor_units'] = None
        facts['origins'] = {k: v for k, v in facts['origins'].items() if k not in ('unit_price', 'price_level', 'price_basis_amount')}
        facts['origins']['net_amount'] = Origin(kind='explicit').model_dump()
    else:
        facts['origins'] = dict(facts['origins'], unit_price=Origin(kind='explicit').model_dump())
    if proof is not None:
        facts.update(schema_version=3, pricing_basis='allocated', net_amount_minor_units=None,
            allocation_proof=proof.model_dump(mode='json'))
    profile = SalesLineProfile.model_validate(facts)
    resolved = {key: getattr(lf, key) for key in ('item_id', 'description', 'quantity_microunits',
        'unit_id', 'unit_factor_nanounits', 'base_quantity_microunits', *sales.MONEY_COLUMNS)} | dict(
        line_id=None, profile=profile, taxes=[dict(rule=t.rule, taxable_minor_units=t.taxable_minor_units,
            tax_minor_units=t.tax_minor_units) for t in lf.taxes])
    if proof is not None:
        from bookflow.company import billing_math as math
        net = proof.net()
        taxes = [dict(rule=t.rule, taxable_minor_units=net,
                      tax_minor_units=calc.tax(net,t.rule.rate_percent_millionths)) for t in lf.taxes]
        tax = calc.total(t['tax_minor_units'] for t in taxes)
        resolved.update(quantity_microunits=math.exact_microunits(proof.quantity()),
            base_quantity_microunits=math.exact_microunits(proof.quantity(base=True)),
            net_minor_units=net, tax_minor_units=tax, gross_minor_units=calc.total((net,tax)), taxes=taxes)
    return resolved


def resolve_commercial(s, inp, document_type, *, document_id, kind):
    header, rev, selected = source_selection(s, inp, kind)
    warnings = posting_eligibility(s, rev, selected)
    profile, extra = financial_profile(s, inp, document_type, rev)
    warnings += extra
    lines = [resolved_line(item.facts, item.proof) if item.proof else resolved_line(item.facts) for item in selected]
    planned, extra = work._custom_plan(s, inp, document_type, document_id, carry=rev)
    warnings += extra
    number, sequence = effects.allocate(s, document_type, inp.number, document_id)
    ordinals, tax_keys = tax_facts.prospective(s.company, document_id, [None for _ in lines])
    attribution = tax_facts.calculate(lines, profile, rev['currency'], ordinals)
    tax_facts.apply(lines, attribution, ordinals)
    subtotal = calc.total(line['net_minor_units'] for line in lines)
    tax = calc.total(line['tax_minor_units'] for line in lines)
    total = calc.total((subtotal, tax))
    issuer = work.facts(rev).issuer_snapshot
    semantic = dict(date=inp.date, number=number, memo=inp.memo, issuer=issuer,
        profile=tax_facts.semantic_profile(profile), lines=[sales._line_semantic(line) for line in lines],
        custom_fields=sales._custom_semantic(planned.snapshot))
    identities = query.root_identities(s, header)
    source_roots = [(identities[line['line_id']]['root_document_id'], identities[line['line_id']]['root_line_id'])
        for line in work.saved_lines(s, rev)]
    from bookflow.company.billing_allocations import consumption_fingerprint
    consumption = consumption_fingerprint(s, source_roots)
    from bookflow.company import tax_forecasts
    remaining_forecast,_=tax_forecasts.remaining(s,header,rev)
    fingerprint = hashlib.sha256(sales.json_text(dict(company=s.company_row['id'], type=document_type,
        source_revision=rev['id'], source_version=header['version'],
        roots=[root for _, root, _ in selected], consumption=consumption, forecast=remaining_forecast.model_dump(mode="json"), content=semantic, warnings=warnings,
        tax_attribution=attribution.model_dump(mode='json'), preferences=policy.financial_projection(s, kind))).encode()).hexdigest()
    if inp.expected_facts_fingerprint and inp.expected_facts_fingerprint != fingerprint:
        raise BookflowError('E_PREVIEW_STALE', details=dict(facts_fingerprint=fingerprint,
            consumption_changes=query.latest_consumption_changes(s, source_roots),
            preference_changes=policy.changes(s, policy.financial_fields(s, kind))))
    if document_type == 'sales_receipt' and money(inp.amount_received, rev['currency'], 'amount_received').minor_units != total:
        raise _invalid('amount_received', 'must equal the exact gross amount of the selected work')
    if document_type == 'invoice':
        from bookflow.company.customer_balances import credit_warning
        warning = credit_warning(s.company, profile.customer.id, total)
        if warning:
            warnings.append(warning)
    return dict(profile=profile, date=inp.date, number=number, sequence=sequence, memo=inp.memo,
        issuer=issuer, lines=lines, custom_plan=planned, warnings=warnings, semantic=semantic,
        fingerprint=fingerprint, subtotal=subtotal, tax=tax, total=total, currency=rev['currency'],
        tax_attribution=attribution, tax_keys=tax_keys)


def replay_plan(s, ctx, inp, kind, destination):
    key, request = hashes(inp, kind, destination)
    existing = work.rows(s, c.work_billing_conversions, c.work_billing_conversions.c.conversion_key_hash == key)
    if existing:
        saved = existing[0]
        if saved['request_hash'] != request:
            raise BookflowError('E_CONVERSION_KEY_REUSED', details={'destination_id': saved['destination_transaction_id']})
        header = sales.resolve(s, saved['destination_transaction_id'], destination)
        revision = sales.journals.revision(s, header)
        source = work.resolve(s, saved['source_document_id'], kind)
        before = work.rows(s, c.work_revisions, c.work_revisions.c.id == saved['source_revision_id'])[0]
        after = work.rows(s, c.work_revisions,
            c.work_revisions.c.document_id == source['id'],
            c.work_revisions.c.supersedes_revision_id == before['id'])[0]
        return Plan(SalesWriteOutput(**sales.summary(header, revision, sales.profile_row(s, revision)),
            revision=sales.revision_output(s, revision), changed=False, idempotent_replay=True,
            source_effect=source_effect(source, before, after, saved['source_version']),
            source_current=source_current(source)),
            dict(changed=False, input=inp, kind=kind, destination=destination))
    if work.rows(s, c.work_links, c.work_links.c.conversion_key_hash == key):
        raise BookflowError('E_CONVERSION_KEY_REUSED', details={'problem': 'key already belongs to an operational conversion'})
    return None


def prepare(s, ctx, inp, kind, destination):
    replay = replay_plan(s, ctx, inp, kind, destination)
    if replay:
        return replay
    source, source_rev, selected = source_selection(s, inp, kind)
    sale = sales.prepare(s, ctx, inp, destination, 'post', billing_source=kind)
    data = sale.data
    header, rev = data['header'], data['pending']['transaction_revisions'][0]
    at, event = header['updated_at'], data['event']
    provenance = dict(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)
    current = dict(source, version=source['version'] + 1, updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value)
    pending_work = {table: [] for table, _ in work.TABLE_KINDS}
    source_value = work._semantic(source_rev, work.saved_lines(s, source_rev))
    if policy.closes(s, source, source_rev, sale.preview.subtotal_minor_units):
        source_value['active'] = False
    work._new_revision(s, ctx, current, source_value,
        json.loads(source_rev['custom_fields_snapshot']), source_rev, pending_work, event, at,
        acceptance={key: source_rev[key] for key in ('accepted_revision_id', 'accepted_at', 'accepted_by')})
    key, request = hashes(inp, kind, destination)
    conversion = dict(id=new_id(), source_document_id=source['id'], source_revision_id=source_rev['id'],
        source_version=source['version'], destination_transaction_id=header['id'], destination_revision_id=rev['id'],
        destination_type=destination, relation=kind + '_' + destination,
        conversion_key_hash=key, request_hash=request, **provenance)
    allocations = []
    profiles = {row['document_line_id']: row for row in data['pending']['sales_line_profiles']}
    from bookflow.company.billing_allocations import stored_proof
    for envelope, item in zip(data['pending']['document_lines'], selected):
        line, root, lf = item
        projected = profiles[envelope['id']]
        snapshot = dict(line=lf.model_dump(mode='json'), document=work.facts(source_rev).model_dump(mode='json'),
            title=source_rev['title'], source_number=source_rev['number'])
        allocations.append(dict(id=new_id(), transaction_id=header['id'], revision_id=rev['id'],
            document_line_id=envelope['id'], source_document_id=source['id'], source_revision_id=source_rev['id'],
            source_line_id=line['id'], root_document_id=root[0], root_line_id=root[1],
            quantity_microunits=projected['quantity_microunits'], net_minor_units=projected['net_minor_units'],
            tax_minor_units=projected['tax_minor_units'], gross_minor_units=projected['gross_minor_units'],
            **stored_proof(item.proof),
            facts_snapshot=sales.json_text(snapshot), **provenance))
    data.update(kind=kind, destination=destination, billing_allocations=allocations,
        billing_conversion=conversion, work_header=current, work_before=source, work_pending=pending_work)
    sale.preview.revision.billing_sources = query.sale_source_output(s, rev['id'], allocations)
    sale.preview.source_effect = source_effect(source, source_rev, pending_work['work_revisions'][0], source['version'])
    sale.preview.source_current = source_current(current)
    from bookflow.company.billing_validation import validate
    validate(sale, s, ctx)
    from bookflow.company.billing_progress import projection
    sale.preview.billing_progress, sale.preview.billing_forecast = projection(s, source, source_rev, allocations)
    return sale


def source_effect(source, before, after, version):
    from bookflow.company.sales_outputs import WorkBillingSourceEffect
    return WorkBillingSourceEffect(source_id=source['id'], source_kind=source['kind'],
        version_before=version, version_after=version + 1,
        active_before=before['active'], active_after=after['active'],
        automatically_closed=before['active'] and not after['active'])


def source_current(source):
    from bookflow.company.sales_outputs import WorkBillingCurrent
    return WorkBillingCurrent(source_id=source['id'], version=source['version'],
        active=source['active'], status=source['status'])


def replay_conversion(s, ctx, inp, kind, destination, hit):
    plan = replay_plan(s, ctx, inp, kind, destination)
    original = json.loads(hit['output']) if hit.get('output') else {}
    if plan is None or original.get('id') != plan.preview.id:
        raise BookflowError('E_INTERNAL', message='Financial conversion receipt does not match its permanent source link.')
    return plan.preview.model_dump(mode='json')


def persist(plan, ctx, s, *, command_name):
    data = plan.data
    if not data['changed']:
        return Applied(plan.preview, [], 'no change')
    if data.get('stock') is not None and data['stock'].moves_stock and 'work_header' in data:
        # A work order or an estimate cannot carry a stock item, so a conversion of one cannot
        # move stock; the conversion command writes through here without passing `sales.apply`,
        # which is where the movements would be written.
        raise BookflowError('E_INTERNAL', message=(
            'A work billing conversion cannot move stock; its source could not have carried a '
            'stock item.'))
    header, old = data['header'], data['before']
    touched = [Touched('transaction', header['id'], 'update' if old else 'create',
        old['version'] if old else None, header['version'], header, old, db='company')]
    inserts = [(getattr(c, table), data['pending'][table], kind, key) for table, kind, key in sales.TABLE_KINDS]
    inserts.append((c.work_billing_allocations, data.get('billing_allocations', []), 'work_billing_allocation', 'id'))
    if data.get('billing_conversion'):
        wh, wb = data['work_header'], data['work_before']
        touched.append(Touched('work_document', wh['id'], 'update', wb['version'], wh['version'], wh, wb, db='company'))
        inserts += [(getattr(c, table), data['work_pending'][table], kind, work.work_tax.TABLE_KEYS.get(table,'id')) for table, kind in work.TABLE_KINDS]
        inserts.append((c.work_billing_conversions, [data['billing_conversion']], 'work_billing_conversion', 'id'))
    for table, rows, kind, key in inserts:
        touched.extend(Touched(kind, row[key], 'create', None, 1, effects.decoded(row), db='company') for row in rows)
    planned = data.get('custom_plan')
    if planned:
        touched.extend(custom.touches(planned))
    summary = f"{command_name}: {header['number']}"
    audit.write_event_to(s.company, ctx, command_name, summary, touched, actor_id=s.actor.id,
        actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None), event_id=data['event'])
    if old:
        changed = s.company.conn.execute(c.transactions.update().where(c.transactions.c.id == header['id'],
            c.transactions.c.version == old['version']).values(**header)).rowcount
        if changed != 1:
            raise BookflowError('E_VERSION_CONFLICT', details={'record_id': header['id']})
    else:
        s.company.conn.execute(c.transactions.insert().values(**header))
    if data.get('billing_conversion'):
        changed = s.company.conn.execute(c.work_documents.update().where(c.work_documents.c.id == wh['id'],
            c.work_documents.c.version == wb['version']).values(**wh)).rowcount
        if changed != 1:
            raise BookflowError('E_VERSION_CONFLICT', details={'record_id': wh['id']})
    for table, rows, _, _ in inserts:
        if rows:
            s.company.conn.execute(table.insert(), rows)
    if planned:
        custom.apply(s.company, planned)
    if data['sequence']:
        from sqlalchemy.dialects.sqlite import insert
        stmt = insert(c.sequences).values(**data['sequence'])
        s.company.conn.execute(stmt.on_conflict_do_update(index_elements=['name'], set_=data['sequence']))
    return Applied(plan.preview, touched, summary, audited=True)


def apply(plan, ctx, s):
    fresh = prepare(s, ctx, plan.data['input'], plan.data['kind'], plan.data['destination'])
    from bookflow.company.billing_validation import validate
    validate(fresh, s, ctx)
    return persist(fresh, ctx, s, command_name=plan.data['kind'].replace('_', '-') + ' ' + plan.data['destination'].replace('_', '-'))


def coordinate_rows_and_touches(plan):
    """Only correction-carried allocations, never conversion or work writes."""
    if any(key in plan.data for key in ('billing_conversion', 'work_header', 'work_before', 'work_pending')):
        raise BookflowError('E_INTERNAL')
    rows = tuple(plan.data.get('billing_allocations', ()))
    return ('work_billing_allocations', rows, tuple(Touched('work_billing_allocation', row['id'],
        'create', None, 1, effects.decoded(row), db='company') for row in rows))
