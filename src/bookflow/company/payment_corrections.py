"""Commercial receipt revisions with fixed owner capacities and source restatement."""
import json

import sqlalchemy as sa

from bookflow.company import schema as c, sales, journals, sales_defaults as defaults
from bookflow.company import document_effects as effects, journal_custom_fields as custom
from bookflow.company import payments, payment_queries as query, payment_operations as operations
from bookflow.company import payment_dependencies as dependencies
from bookflow.company.payment_cancellation import live_allocations
from bookflow.company.payment_outputs import PaymentProfileOutput, PaymentWriteOutput, PaymentSourceOutput
from bookflow.company.sales_models import money, _invalid
from bookflow.core import clock
from bookflow.core.ids import new_id
from bookflow.core.errors import BookflowError
from bookflow.core.money import Money
from bookflow.core.registry import Plan


def prepare(s, ctx, inp):
    from bookflow.company.payment_models import PaymentUpdateIntent, EffectProvenance
    intent = PaymentUpdateIntent.model_validate(inp.model_dump(exclude_unset=True, exclude={'operation_key'}))
    plan = prepare_effect(s, ctx, intent, EffectProvenance(at=clock.now_iso(), event_id=new_id(), operation_id=new_id()))
    plan.preview = PaymentWriteOutput(**plan.preview.model_dump(), operation_key=inp.operation_key)
    plan.data['input'] = inp
    return plan


def prepare_effect(s, ctx, inp, provenance):
    """Complete correction graph without payment-key lookup or child receipt."""
    from bookflow.company.payment_models import PaymentUpdateIntent, EffectProvenance
    if type(inp) is not PaymentUpdateIntent or type(provenance) is not EffectProvenance:
        raise BookflowError('E_VALIDATION')
    funding = query.payment_facts(s, inp.payment, write=True)
    old, prior, saved = funding['header'], funding['revision'], funding['profile']
    dependencies.payment_version(s, old, inp.expected_version)
    if old['status'] != 'posted':
        raise BookflowError('E_VALIDATION', details={'state': 'voided'})
    if not ctx.reason or not ctx.reason.strip() or len(ctx.reason) > 140:
        raise BookflowError('E_REASON_REQUIRED')
    profile = PaymentProfileOutput.model_validate_json(saved['profile_snapshot'])
    original_profile = profile.model_dump(mode='json')
    if inp.payment_method is not None:
        method = defaults._row(s.company, 'payment_method', inp.payment_method, active=False)
        if method['id'] != profile.payment_method.id:
            profile.payment_method = defaults._ref(defaults._active(method, 'payment_method'))
    if inp.deposit_to is not None:
        raw = defaults._row(s.company, 'account', inp.deposit_to, active=False)
        if raw['id'] != profile.deposit_account.id:
            account = defaults._account(s.company, raw['id'], 'deposit_to', {'bank', 'other_current_asset'})
            if account.type != 'bank' and raw['system_role'] != 'undeposited_funds':
                raise _invalid('deposit_to', 'select a bank or system Undeposited Funds account')
            profile.deposit_account = account
    amount = money(inp.amount, prior['currency'], 'amount').minor_units if inp.amount is not None else prior['total_minor_units']
    if amount <= 0:
        raise _invalid('amount', 'receipt total must remain positive')
    date, number = inp.date or prior['date'], inp.number or prior['number']
    memo = inp.memo if 'memo' in inp.model_fields_set else prior['memo']
    reference = inp.reference if 'reference' in inp.model_fields_set else saved['reference']
    custom.validate_kinds(s.company, inp.custom_fields, inp.expected_custom_field_kinds, record_type='payment')
    custom_plan = custom.prepare(s.company, old['id'], inp.custom_fields, json.loads(prior['custom_fields_snapshot']),
                                creating=False, record_type='payment')
    semantic = [date, amount, number, memo, reference, profile.model_dump(mode='json'), sales._custom_semantic(custom_plan.snapshot)]
    previous_semantic = [prior['date'], prior['total_minor_units'], prior['number'], prior['memo'], saved['reference'],
                         original_profile, sales._custom_semantic(json.loads(prior['custom_fields_snapshot']))]
    changed = semantic != previous_semantic or custom_plan.changed
    targets = {}
    # Authorize all related invoices before checking caller-supplied versions.
    for app in funding['applications']:
        identifier = app['paid_transaction_id']
        if identifier not in targets:
            targets[identifier] = query.invoice_facts(s, identifier, write=True)
    if changed:
        # Reversal retains old facts, but every replacement posting account
        # must still admit a posting. Do not refresh its captured display data.
        defaults._row(s.company, 'account', profile.deposit_account.id)
        defaults._row(s.company, 'account', profile.ar_account.id)
        journals.open_dates(s, [prior['date'], date, *(app['effective_date'] for app in funding['applications'])])
        if any(date > app['effective_date'] for app in funding['applications']):
            raise BookflowError('E_HAS_APPLICATIONS', details={'field': 'date', 'payment_id': old['id']})
        if inp.settlement_guard:
            dependencies.validate(s, inp.settlement_guard, 'payment', old['id'])
        else:
            supplied = {}
            for ref in inp.invoice_versions:
                invoice = query.invoice_facts(s, ref.invoice, write=True)
                if invoice['header']['id'] in supplied:
                    raise _invalid('invoice_versions', 'supply each related invoice once')
                supplied[invoice['header']['id']] = ref.expected_version
            if set(supplied) != set(targets):
                raise BookflowError('E_PREVIEW_STALE', details={'reason': 'settlement_dependencies',
                    'settlement_guard': dependencies.issue(s, 'payment', old['id'])})
            for identifier, facts in targets.items():
                sales._version(s, facts['header'], supplied[identifier])
        duplicate = s.company.conn.execute(sa.select(c.transactions.c.id).where(c.transactions.c.type == 'payment',
            c.transactions.c.number == number, c.transactions.c.id != old['id'])).first()
        if duplicate:
            raise BookflowError('E_DUPLICATE_NUMBER')
    capacities = {key: 0 for key in funding['keys']}
    capacities.update({row['component_key_id']: row['amount_minor_units'] for row in funding['components']})
    payer_key = next((key for key, row in funding['keys'].items() if row['party_id'] == saved['payer_id']), None)
    delta = amount - prior['total_minor_units']
    payer_capacity = capacities.get(payer_key, 0) + delta
    payer_applied = sum(app['amount_minor_units'] for app in funding['applications'] if app['source_component_key_id'] == payer_key)
    if payer_capacity < payer_applied or payer_capacity < 0:
        raise BookflowError('E_APPLIED_EXCEEDS_TOTAL', details={'party_id': saved['payer_id'], 'minimum_minor_units': payer_applied})
    at, event, operation_id = provenance.at, provenance.event_id, provenance.operation_id
    created = lambda: dict(id=new_id(), created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)
    pending = {table: [] for table, _, _ in payments.TABLE_KINDS}
    header = dict(old)
    revision = prior
    old_allocations = live_allocations(s, [app['id'] for app in funding['applications']])
    output_allocations, changed_headers = [], []
    current = payments.current_output(s, old['id'], complete_components=True)
    if changed:
        header.update(version=old['version'] + 1, updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value, number=number)
        revision = dict(prior, **created(), revision_number=prior['revision_number'] + 1,
            supersedes_revision_id=prior['id'], date=date, number=number, memo=memo, total_minor_units=amount,
            custom_fields_snapshot=query.canonical(custom_plan.snapshot), audit_event_id=event)
        header['current_revision_id'] = revision['id']
        pending['transaction_revisions'].append(revision)
        old_line = effects.rows(s, c.document_lines, c.document_lines.c.revision_id == prior['id'])[0]
        line = dict(old_line, **created(), revision_id=revision['id'], description=memo)
        pending['document_lines'].append(line)
        pending['payment_profiles'].append(dict(saved, revision_id=revision['id'], reference=reference,
            payment_method_id=profile.payment_method.id, deposit_account_id=profile.deposit_account.id,
            profile_snapshot=query.canonical(profile.model_dump(mode='json')), created_at=at, created_by=s.actor.id,
            created_via=ctx.interface.value, audit_event_id=event))
        batches = effects.rows(s, c.posting_batches, c.posting_batches.c.revision_id == prior['id'], c.posting_batches.c.kind != 'reversal')
        if len(batches) != 1:
            raise _invalid('payment', 'ambiguous current receipt posting')
        effects.reverse(s, header, prior, batches[0], event, created, pending)
        batch = dict(**created(), transaction_id=old['id'], revision_id=revision['id'], kind='replacement', effective_date=date,
            reverses_batch_id=None, replaces_batch_id=batches[0]['id'], audit_event_id=event)
        pending['posting_batches'].append(batch)
        keys = dict(funding['keys'])
        if payer_key is None and payer_capacity > 0:
            key = dict(**created(), transaction_id=old['id'], line_id=old_line['line_id'], party_id=saved['payer_id'],
                ar_account_id=saved['ar_account_id'], currency=prior['currency'], audit_event_id=event)
            pending['payment_component_keys'].append(key)
            payer_key = key['id']
            keys[payer_key] = key
        if payer_key is not None:
            capacities[payer_key] = payer_capacity
        old_components = {row['component_key_id']: row for row in funding['components']}
        def leg(account, units, debit, party, position):
            row = dict(**created(), transaction_id=old['id'], batch_id=batch['id'], line_no=position,
                account_id=account.id, account_snapshot=query.canonical(account.model_dump()),
                debit_minor_units=units if debit else 0, credit_minor_units=0 if debit else units,
                currency=prior['currency'], name_type='customer', name_id=party['id'], party_name=party['label'],
                class_id=None, class_name=None, description=memo, reversed_line_id=None, **dict.fromkeys(journals.FACTS))
            pending['posting_lines'].append(row)
            return row
        cash = leg(profile.deposit_account, amount, True, profile.payer.model_dump(), 1)
        new_components, sources = {}, {}
        for key_id, key in sorted(keys.items(), key=lambda pair: (pair[1]['party_id'], pair[0])):
            capacity = capacities.get(key_id, 0)
            if capacity == 0:
                continue
            captured = (json.loads(old_components[key_id]['component_snapshot']) if key_id in old_components else
                        dict(party=profile.payer.model_dump(), ar_account=profile.ar_account.model_dump(),
                             lineage=[row.model_dump() for row in profile.lineage]))
            component = dict(**created(), transaction_id=old['id'], revision_id=revision['id'], document_line_id=line['id'],
                component_key_id=key_id, amount_minor_units=capacity, currency=prior['currency'],
                component_snapshot=query.canonical(captured), audit_event_id=event)
            pending['payment_components'].append(component)
            new_components[key_id] = component
            ar = leg(profile.ar_account, capacity, False, captured['party'], len(new_components) + 1)
            for posting in (cash, ar):
                source = dict(**created(), transaction_id=old['id'], revision_id=revision['id'], document_line_id=line['id'],
                    posting_line_id=posting['id'], amount_minor_units=capacity, currency=prior['currency'],
                    reversed_source_id=None, tax_component_id=None, payment_component_id=component['id'])
                pending['posting_line_sources'].append(source)
                if posting is ar:
                    sources[key_id] = source
        for app in funding['applications']:
            for old_alloc in [row for row in old_allocations if row['application_id'] == app['id']]:
                pending['application_allocations'].append(dict(old_alloc, **created(), kind='reversal',
                    reverses_allocation_id=old_alloc['id'], audit_event_id=event))
                replacement = dict(old_alloc, **created(), source_revision_id=revision['id'],
                    source_component_id=new_components[app['source_component_key_id']]['id'],
                    source_posting_source_id=sources[app['source_component_key_id']]['id'], audit_event_id=event)
                pending['application_allocations'].append(replacement)
                output_allocations.append(dict(allocation_id=replacement['id'], application_id=app['id'],
                    invoice_id=app['paid_transaction_id'], target_ordinal=old_alloc['target_ordinal'],
                    logical_kind=old_alloc['logical_kind'], tax_item_id=old_alloc['tax_item_id'],
                    amount=Money(old_alloc['amount_minor_units'], prior['currency']).to_dict()))
        for target in targets.values():
            before = target['header']
            changed_headers.append((before, dict(before, version=before['version'] + 1, updated_at=at,
                updated_by=s.actor.id, updated_via=ctx.interface.value)))
        components = []
        for key_id, key in sorted(keys.items(), key=lambda pair: (pair[1]['party_id'], pair[0])):
            capacity = capacities.get(key_id, 0)
            applied = sum(app['amount_minor_units'] for app in funding['applications'] if app['source_component_key_id'] == key_id)
            party = defaults._row(s.company, 'customer', key['party_id'], active=False)
            components.append(dict(component_key_id=key_id, component_id=new_components[key_id]['id'] if key_id in new_components else None,
                party_id=key['party_id'], party_name=party['full_name'], ar_account_id=key['ar_account_id'], currency=key['currency'],
                received_minor_units=capacity, applied_minor_units=applied, available_minor_units=capacity-applied))
        current.update(version=header['version'], revision_id=revision['id'], received_minor_units=amount,
            effective_received_minor_units=amount, available_minor_units=amount-current['applied_minor_units'],
            components=components, component_count=len(components))
    if any(after['version'] > 9223372036854775807 for after in [header, *(new for before, new in changed_headers)]):
        raise BookflowError('E_VALUE_RANGE')
    fp = query.digest([operations.request(inp, ctx, s, 'payment update'), semantic, old,
        funding['applications'], old_allocations, [facts['header'] for facts in targets.values()],
        defaults._info(s.company)['closing_date']])
    if inp.expected_facts_fingerprint is not None and inp.expected_facts_fingerprint != fp:
        raise BookflowError('E_PREVIEW_STALE', details={'reason': 'payment_facts'})
    changes = [dict(query.invoice_current(s, before['id']), version=after['version']) for before, after in changed_headers]
    output_allocations = [dict(kind=row['kind'], reverses_allocation_id=row['reverses_allocation_id'],
        allocation_id=row['id'], application_id=row['application_id'], invoice_id=row['target_transaction_id'],
        target_ordinal=row['target_ordinal'], logical_kind=row['logical_kind'], tax_item_id=row['tax_item_id'],
        amount=Money(row['amount_minor_units'], row['currency']).to_dict()) for row in pending['application_allocations']]
    effect = dict(kind='update', financial_changed=changed, operation_id=operation_id, payment_id=old['id'],
        audit_event_id=event, before_header=payments.effect_header(old, prior), after_header=payments.effect_header(header, revision),
        preferences=profile.preferences.model_dump(),
        source_components=current['components'], applications=[], allocations=output_allocations, document_changes=changes)
    output = PaymentSourceOutput(id=old['id'], version=header['version'], changed=changed, new_effect=changed,
        facts_fingerprint=fp, effect=effect, current=current,
        effect_counts={key: len(effect[key]) for key in ('source_components', 'applications', 'allocations', 'document_changes')})
    return Plan(output, dict(input=inp, operation='update', header=header, before=old, pending=pending,
        changed_headers=changed_headers, event=event, operation_id=operation_id, selected=None,
        custom_plan=custom_plan if changed else None, sequence=None, context={'currency': prior['currency']}, targets=[],
        fingerprint=fp, at=at, funding=funding, old_allocations=old_allocations, semantic=semantic))


def validate(plan, s, ctx):
    data = plan.data
    pending, funding = data['pending'], query.payment_facts(s, data['header']['id'], write=True)
    def require(value):
        if not value:
            raise BookflowError('E_INTERNAL', message='Invalid receipt correction aggregate.')
    require(not pending['applications'] and not pending['document_line_identities'])
    if not plan.preview.changed:
        require(data['header'] == data['before'] and all(not rows for rows in pending.values()))
        return
    revision = pending['transaction_revisions'][0]
    require(revision['revision_number'] == funding['revision']['revision_number'] + 1)
    require(data['header']['version'] == funding['header']['version'] + 1)
    journals.open_dates(s, [funding['revision']['date'], revision['date'], *(row['effective_date'] for row in funding['applications'])])
    require(all(revision['date'] <= row['effective_date'] for row in funding['applications']))
    require(sum(row['amount_minor_units'] for row in pending['payment_components']) == revision['total_minor_units'])
    keys = dict(funding['keys']) | {row['id']: row for row in pending['payment_component_keys']}
    old_capacity = {row['component_key_id']: row['amount_minor_units'] for row in funding['components']}
    new_capacity = {row['component_key_id']: row['amount_minor_units'] for row in pending['payment_components']}
    for key, facts in keys.items():
        capacity = new_capacity.get(key, 0)
        require(capacity >= sum(row['amount_minor_units'] for row in funding['applications'] if row['source_component_key_id'] == key))
        if facts['party_id'] != funding['profile']['payer_id']:
            require(capacity == old_capacity.get(key, 0))
    old_allocs = {row['id']: row for row in live_allocations(s, [app['id'] for app in funding['applications']])}
    inverses = [row for row in pending['application_allocations'] if row['kind'] == 'reversal']
    replacements = [row for row in pending['application_allocations'] if row['kind'] == 'allocation']
    require({row['reverses_allocation_id'] for row in inverses} == set(old_allocs))
    provenance = {'id', 'created_at', 'created_by', 'created_via', 'audit_event_id'}
    for inverse in inverses:
        original = old_allocs[inverse['reverses_allocation_id']]
        ignored = provenance | {'kind', 'reverses_allocation_id'}
        require({k: v for k, v in inverse.items() if k not in ignored} == {k: v for k, v in original.items() if k not in ignored})
    ignored = provenance | {'source_revision_id', 'source_component_id', 'source_posting_source_id'}
    normalize = lambda row: query.canonical({k: v for k, v in row.items() if k not in ignored})
    require(sorted(map(normalize, replacements)) == sorted(map(normalize, old_allocs.values())))
    for batch in pending['posting_batches']:
        legs = [row for row in pending['posting_lines'] if row['batch_id'] == batch['id']]
        require(sum(row['debit_minor_units'] - row['credit_minor_units'] for row in legs) == 0)
        require(all(type(row['debit_minor_units']) is int and type(row['credit_minor_units']) is int and
                    ((row['debit_minor_units'] > 0 and row['credit_minor_units'] == 0) or
                     (row['credit_minor_units'] > 0 and row['debit_minor_units'] == 0)) for row in legs))
        if batch['kind'] == 'reversal':
            old_legs = effects.rows(s, c.posting_lines, c.posting_lines.c.batch_id == batch['reverses_batch_id'])
            require(len(legs) == len(old_legs))
            require({row['reversed_line_id'] for row in legs} == {row['id'] for row in old_legs})
            for row in legs:
                old_leg = next(value for value in old_legs if value['id'] == row['reversed_line_id'])
                ignored = provenance | {'batch_id', 'reversed_line_id', 'debit_minor_units', 'credit_minor_units'}
                require({k: v for k, v in row.items() if k not in ignored} == {k: v for k, v in old_leg.items() if k not in ignored})
                require(row['debit_minor_units'] == old_leg['credit_minor_units'] and row['credit_minor_units'] == old_leg['debit_minor_units'])
            old_sources = effects.rows(s, c.posting_line_sources, c.posting_line_sources.c.posting_line_id.in_([row['id'] for row in old_legs]))
            new_sources = [row for row in pending['posting_line_sources'] if row['reversed_source_id'] is not None]
            require({row['reversed_source_id'] for row in new_sources} == {row['id'] for row in old_sources})
            for row in new_sources:
                old_source = next(value for value in old_sources if value['id'] == row['reversed_source_id'])
                ignored = provenance | {'posting_line_id', 'reversed_source_id'}
                require({k: v for k, v in row.items() if k not in ignored} == {k: v for k, v in old_source.items() if k not in ignored})
                require(next(value for value in legs if value['id'] == row['posting_line_id'])['reversed_line_id'] == old_source['posting_line_id'])
        else:
            require(batch['kind'] == 'replacement' and batch['effective_date'] == revision['date'])
            profile = pending['payment_profiles'][0]
            debits = [row for row in legs if row['debit_minor_units']]
            require(len(debits) == 1 and debits[0]['account_id'] == profile['deposit_account_id'] and
                    debits[0]['name_id'] == funding['profile']['payer_id'] and debits[0]['debit_minor_units'] == revision['total_minor_units'])
            expected = sorted((keys[row['component_key_id']]['party_id'], row['amount_minor_units']) for row in pending['payment_components'])
            require(sorted((row['name_id'], row['credit_minor_units']) for row in legs if row['credit_minor_units']) == expected)
            require(all(row['account_id'] == funding['profile']['ar_account_id'] for row in legs if row['credit_minor_units']))
            for component in pending['payment_components']:
                sources = [row for row in pending['posting_line_sources'] if row['payment_component_id'] == component['id']]
                require(len(sources) == 2 and all(row['amount_minor_units'] == component['amount_minor_units'] and
                    row['revision_id'] == revision['id'] and row['document_line_id'] == component['document_line_id'] and
                    row['currency'] == revision['currency'] for row in sources))
                require(debits[0]['id'] in {row['posting_line_id'] for row in sources})
    for table, rows in pending.items():
        for row in rows:
            require(row['created_by'] == s.actor.id and row['created_via'] == ctx.interface.value)
            if 'audit_event_id' in row:
                require(row['audit_event_id'] == data['event'])
    require({old['id'] for old, new in data['changed_headers']} == {row['paid_transaction_id'] for row in funding['applications']})
    for old, new in data['changed_headers']:
        ignored = {'version', 'updated_at', 'updated_by', 'updated_via'}
        require(new['version'] == old['version'] + 1)
        require({k: v for k, v in old.items() if k not in ignored} == {k: v for k, v in new.items() if k not in ignored})
    custom.validate(s.company, data['custom_plan'], data['header']['id'], data['custom_plan'].snapshot, record_type='payment')
