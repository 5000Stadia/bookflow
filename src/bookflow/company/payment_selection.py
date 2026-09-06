"""Audited shared payment drafts; immutable origins survive interface handoff."""
import json

import sqlalchemy as sa

from bookflow.company import schema as c, sales_defaults as defaults, sales, document_effects as effects
from bookflow.company import payment_calculations as calc, payment_queries as query
from bookflow.company.payment_authority import authorize
from bookflow.company.payment_outputs import SelectionOutput, SelectionWriteOutput
from bookflow.company.sales_models import money, _invalid
from bookflow.core import audit, clock, versioning
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.money import Money
from bookflow.core.registry import Plan, Applied, Touched
from bookflow.hub.users import common


def context(s, inp):
    info = defaults._info(s.company)
    if inp.mode == 'existing_credit':
        facts = query.payment_facts(s, inp.payment)
        payer = facts['profile']['payer_id']
        ar = facts['profile']['ar_account_id']
        payment = facts['header']['id']
        if facts['header']['status'] != 'posted':
            raise BookflowError('E_APPLICATION_INACTIVE')
    else:
        payer = defaults._row(s.company, 'customer', inp.customer)['id']
        ar = inp.ar_account
        if ar is None:
            candidates = s.company.conn.execute(sa.select(c.accounts.c.id).where(
                c.accounts.c.system_role == 'accounts_receivable', c.accounts.c.active.is_(True))).scalars().all()
            if len(candidates) != 1:
                raise _invalid('ar_account', 'select an active AR account when the system AR default is unavailable')
            ar = candidates[0]
        ar = defaults._account(s.company, ar, 'ar_account', {'accounts_receivable'}).id
        payment = None
    result = dict(mode=inp.mode, customer_id=payer, ar_account_id=ar, payment_id=payment,
        date=inp.date, currency=info['home_currency'], label=getattr(inp, 'label', None),
        automatically_calculate=bool(info['automatically_calculate_payments']))
    result.update(funding_version=facts['header']['version'] if payment else None,
        funding_date=facts['revision']['date'] if payment else None,
        funding_capacities={facts['keys'][key]['party_id']: value for key, value in facts['available'].items()} if payment else {})
    return result


def funding_calculation(s, context_, items):
    if not context_['payment_id']:
        return {}
    facts = query.payment_facts(s, context_['payment_id'])
    if facts['header']['version'] != context_.get('funding_version'):
        raise BookflowError('E_QUERY_STALE', details={'reason': 'funding_version', 'payment_id': facts['header']['id']})
    if facts['header']['status'] != 'posted' or context_['date'] < facts['revision']['date']:
        raise _invalid('date', 'select available credit on or after its receipt date')
    return dict(source_capacities={facts['keys'][key]['party_id']: units for key, units in facts['available'].items()},
        source_owners={row['invoice_id']: query.invoice_facts(s, row['invoice_id'])['profile']['customer_id'] for row in items})


def resolve(s, selector):
    found = effects.rows(s, c.payment_selections, c.payment_selections.c.id == selector)
    if not found:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'payment_selection', 'selector': selector})
    return found[0]


def saved(s, header, version=None):
    revisions = effects.rows(s, c.payment_selection_revisions,
        c.payment_selection_revisions.c.selection_id == header['id'],
        c.payment_selection_revisions.c.version == (version if version is not None else header['version']))
    if not revisions:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'payment_selection_revision'})
    revision = revisions[0]
    r, i = c.payment_selection_revisions, c.payment_selection_items
    events = s.company.conn.execute(sa.select(i).join(r, r.c.id == i.c.revision_id).where(
        i.c.selection_id == header['id'], r.c.version <= revision['version']).order_by(r.c.version, i.c.id)).mappings()
    items = {}
    for event in events:
        if event['kind'] == 'clear':
            items.clear()
        elif event['kind'] == 'remove':
            items.pop(event['invoice_id'], None)
        else:
            items[event['invoice_id']] = {key: event[key] for key in (
                'invoice_id', 'ordinal', 'expected_version', 'due_minor_units', 'amount_minor_units', 'amount_origin')}
    context_ = json.loads(revision['context_snapshot'])
    # Historical revision reads retain protection for all draft history, not
    # only the current selected subset after a clear.
    historical = s.company.conn.execute(sa.select(i.c.invoice_id).where(
        i.c.selection_id == header['id'], i.c.invoice_id.is_not(None)).distinct()).scalars().all()
    authorize(s, [*historical, *([context_['payment_id']] if context_['payment_id'] else [])])
    return revision, context_, sorted(items.values(), key=lambda row: (row['ordinal'], row['invoice_id']))


def _version(s, header, expected):
    def history(version):
        entries = versioning.history_from_entries(s.company, 'payment_selection', header['id'], version, sales._history_snapshot)
        for entry in entries:
            if entry.changed_columns is not None:
                entry.changed_columns = ['selection']
        return entries
    return versioning.check_update(current_version=header['version'], current_updated_at=header['updated_at'],
        current_writer=versioning.current_writer(s.company, 'payment_selection', header['id'], header),
        changes={'selection'}, expected_version=expected, history_since=history, actor_id=s.actor.id,
        window_seconds=s.company_info_row.get('recent_activity_window_seconds', 60))


def _rows(items):
    return [calc.DraftRow(row['invoice_id'], row['ordinal'], row['due_minor_units'], row['amount_minor_units'], row['amount_origin']) for row in items]


def output(header, revision, context_, items):
    # Rendering a stored revision validates its saved amounts; it must not run
    # a cash-only recalculation that replaces already funded derived amounts.
    stored = [calc.DraftRow(row['invoice_id'], row['ordinal'], row['due_minor_units'], row['amount_minor_units'],
                           'entered' if row['amount_minor_units'] is not None else 'unresolved') for row in items]
    funding = dict(source_capacities=context_['funding_capacities'], source_owners=context_['funding_owners']) if context_.get('funding_owners') is not None else {}
    result = calc.calculate(revision['amount_minor_units'], revision['amount_origin'], stored, **funding)
    return dict(id=header['id'], version=header['version'], revision_id=revision['id'],
        revision_version=revision['version'], state=header['state'], consumed_operation_id=header['consumed_operation_id'],
        context=context_, amount=Money(result.amount, context_['currency']).to_dict() if result.amount is not None else None,
        amount_origin=result.amount_origin, item_count=len(items), manifest_hash=revision['manifest_hash'],
        applied_minor_units=sum(row['amount_minor_units'] or 0 for row in items),
        unapplied_minor_units=result.unapplied, problems=list(result.problems))


def show(s, inp):
    header = resolve(s, inp.selection)
    revision, context_, items = saved(s, header, inp.revision)
    return SelectionOutput(**output(header, revision, context_, items))


def compatible(s, context_, facts):
    profile, revision, header = facts['profile'], facts['revision'], facts['header']
    if profile['control_account_id'] != context_['ar_account_id'] or revision['currency'] != context_['currency']:
        raise BookflowError('E_APPLICATION_INCOMPATIBLE', details={'invoice_id': header['id']})
    if header['status'] != 'posted' or revision['date'] > context_['date']:
        raise _invalid('invoice', 'select a posted invoice dated on or before the payment')
    if context_['mode'] == 'existing_credit':
        funding = query.payment_facts(s, context_['payment_id'])
        if not any(key['party_id'] == profile['customer_id'] and key['ar_account_id'] == context_['ar_account_id']
                   and key['currency'] == context_['currency'] for key in funding['keys'].values()):
            raise BookflowError('E_APPLICATION_INCOMPATIBLE', details={'invoice_id': header['id']})
    else:
        family = s.company.raw.execute('''WITH RECURSIVE family(id) AS (
            SELECT id FROM customers WHERE id=? UNION SELECT c.id FROM customers c JOIN family f ON c.parent_id=f.id)
            SELECT id FROM family''', (context_['customer_id'],)).fetchall()
        if profile['customer_id'] not in {row[0] for row in family}:
            raise BookflowError('E_APPLICATION_INCOMPATIBLE', details={'invoice_id': header['id']})


def prepare(s, ctx, inp, operation):
    at, event = clock.now_iso(), new_id()
    created = dict(created_at=at, created_by=s.actor.id, created_via=ctx.interface, audit_event_id=event)
    before = None
    if operation == 'create':
        context_ = context(s, inp)
        header = dict(id=new_id(), **common(s.actor.id, ctx.interface, at), state='open',
                      consumed_operation_id=None, current_revision_id=new_id())
        items, previous = [], None
        amount, origin = None, 'selection_total' if context_['automatically_calculate'] else 'unresolved'
    else:
        before = resolve(s, inp.selection)
        previous, context_, items = saved(s, before)
        authorize(s, [row['invoice_id'] for row in items] + ([context_['payment_id']] if context_['payment_id'] else []), write=True)
        _version(s, before, inp.expected_version)
        if before['state'] != 'open':
            raise BookflowError('E_SELECTION_CONSUMED', details={'selection_id': before['id']})
        header = dict(before, version=before['version'] + 1, updated_at=at, updated_by=s.actor.id,
                      updated_via=ctx.interface, current_revision_id=new_id())
        amount, origin = previous['amount_minor_units'], previous['amount_origin']
    prior_items = {row['invoice_id']: dict(row) for row in items}
    if operation == 'update' and inp.adopt_funding_version is not None:
        if not context_['payment_id']:
            raise _invalid('adopt_funding_version', 'only existing-credit drafts have funding versions')
        facts = query.payment_facts(s, context_['payment_id'], write=True)
        from bookflow.company.payment_dependencies import payment_version
        payment_version(s, facts['header'], inp.adopt_funding_version)
        context_ = dict(context_, funding_version=facts['header']['version'], funding_date=facts['revision']['date'],
            funding_capacities={facts['keys'][key]['party_id']: units for key, units in facts['available'].items()})
    if operation == 'clear':
        items = []
    elif operation == 'update':
        by_id = dict(prior_items)
        removes = [sales.resolve(s, selector, 'invoice')['id'] for selector in inp.remove_invoices]
        sets = [(entry, query.invoice_facts(s, entry.invoice, write=True)) for entry in inp.set_items]
        if len({facts['header']['id'] for _, facts in sets}) != len(sets) or len(set(removes)) != len(removes) or set(removes) & {f['header']['id'] for _, f in sets}:
            raise _invalid('set_items', 'invoice aliases must resolve to distinct disjoint targets')
        for identifier in removes:
            authorize(s, [identifier], write=True)
            if identifier not in by_id:
                raise _invalid('remove_invoices', 'invoice is not selected')
            by_id.pop(identifier)
        maximum = s.company.conn.execute(sa.select(sa.func.coalesce(sa.func.max(c.payment_selection_items.c.ordinal), 0)).where(
            c.payment_selection_items.c.selection_id == header['id'])).scalar_one()
        for entry, facts in sets:
            compatible(s, context_, facts)
            sales._version(s, facts['header'], entry.expected_version)
            identifier = facts['header']['id']
            old = by_id.get(identifier)
            if old is None:
                maximum += 1
            row_origin = entry.amount_origin or ('entered' if entry.amount is not None else 'unresolved')
            value = money(entry.amount, context_['currency'], 'amount').minor_units if entry.amount is not None else None
            if row_origin == 'calculated' and value is None:
                value = 0
            by_id[identifier] = dict(invoice_id=identifier, ordinal=old['ordinal'] if old else maximum,
                expected_version=entry.expected_version, due_minor_units=facts['due'], amount_minor_units=value, amount_origin=row_origin)
        items = list(by_id.values())
        if inp.adopt_calculation_policy is not None:
            context_ = dict(context_, automatically_calculate=inp.adopt_calculation_policy)
    if operation != 'clear':
        if 'amount' in inp.model_fields_set:
            amount = money(inp.amount, context_['currency'], 'amount').minor_units if inp.amount is not None else None
            origin = 'entered' if amount is not None else ('selection_total' if context_['automatically_calculate'] else 'unresolved')
        if getattr(inp, 'amount_origin', None) is not None:
            origin = inp.amount_origin
            if origin == 'unresolved':
                amount = None
    # All retained financial baselines are rechecked. Another interface never
    # silently adopts current invoice facts when reopening or changing a draft.
    for item in items:
        facts = query.invoice_facts(s, item['invoice_id'], write=True)
        sales._version(s, facts['header'], item['expected_version'])
        compatible(s, context_, facts)
        if facts['due'] != item['due_minor_units']:
            raise BookflowError('E_PREVIEW_STALE', details={'reason': 'selection_invoice_due', 'invoice_id': item['invoice_id']})
    try:
        funding = funding_calculation(s, context_, items)
        if funding:
            context_['funding_owners'] = funding['source_owners']
        calculated = calc.calculate(amount, origin, _rows(items), calculate_unresolved=context_['automatically_calculate'],
                                    **funding)
    except ValueError as exc:
        raise _invalid('amount_origin', str(exc)) from None
    by_id = {row['invoice_id']: row for row in items}
    items = [dict(by_id[row.invoice], amount_minor_units=row.amount, amount_origin=row.origin) for row in calculated.rows]
    revision = dict(id=header['current_revision_id'], selection_id=header['id'], version=header['version'],
        context_snapshot=query.canonical(context_), amount_minor_units=calculated.amount, amount_origin=calculated.amount_origin,
        currency=context_['currency'], manifest_hash=query.digest([context_, calculated.amount, calculated.amount_origin, items]),
        item_count=len(items), **created)
    events = []
    def event_row(kind, **values):
        events.append(dict(id=new_id(), selection_id=header['id'], revision_id=revision['id'], kind=kind,
            invoice_id=None, ordinal=None, expected_version=None, due_minor_units=None, amount_minor_units=None,
            amount_origin=None, **created) | values)
    if operation == 'clear':
        event_row('clear')
    else:
        for identifier in prior_items.keys() - {row['invoice_id'] for row in items}:
            event_row('remove', invoice_id=identifier)
        for item in items:
            if prior_items.get(item['invoice_id']) != item:
                event_row('set', **item)
    return Plan(SelectionWriteOutput(**output(header, revision, context_, items)), dict(
        header=header, before=before, revision=revision, events=events, event=event,
        operation=operation, input=inp, fingerprint=revision['manifest_hash']))


def apply(plan, ctx, s):
    # Resolve again under dispatch's company writer transaction; preserve only
    # the logical requested intent, never pre-lock generated IDs or facts.
    fresh = prepare(s, ctx, plan.data['input'], plan.data['operation'])
    if fresh.data['fingerprint'] != plan.data['fingerprint']:
        raise BookflowError('E_PREVIEW_STALE', details={'reason': 'selection_facts'})
    data = fresh.data
    header, before, revision = data['header'], data['before'], data['revision']
    touched = [Touched('payment_selection', header['id'], 'update' if before else 'create',
        before['version'] if before else None, header['version'], header, before, db='company'),
        Touched('payment_selection_revision', revision['id'], 'create', None, 1, revision, db='company')]
    touched.extend(Touched('payment_selection_item', row['id'], 'create', None, 1, row, db='company') for row in data['events'])
    command = 'payment selection ' + data['operation']
    audit.write_event_to(s.company, ctx, command, command, touched, actor_id=s.actor.id, actor_kind=s.actor.kind,
                        directive_code=getattr(s, 'directive_code', None), event_id=data['event'])
    if before:
        s.company.conn.execute(c.payment_selections.update().where(c.payment_selections.c.id == header['id']).values(**header))
    else:
        s.company.conn.execute(c.payment_selections.insert().values(**header))
    s.company.conn.execute(c.payment_selection_revisions.insert().values(**revision))
    if data['events']:
        s.company.conn.execute(c.payment_selection_items.insert(), data['events'])
    return Applied(fresh.preview, touched, command, audited=True)
