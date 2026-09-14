"""Own received goods, sharing captured purchases and the existing stock/GL writer."""
import json
import sqlalchemy as sa
from bookflow.company import bills, check_items, document_effects as effects, inventory, inventory_effects, journals, purchase_orders as orders, schema as c
from bookflow.company.bill_facts import BillItemProfile, BillProfile
from bookflow.company.bill_models import BillPostInput, BillUpdateInput
from bookflow.company.journal_models import JournalLineInput, JournalVoidInput
from bookflow.company.receiving_models import ReceiptOutput, ReceiptWriteOutput, ReceiptPageOutput, ReceiptHistoryOutput
from bookflow.company.parties import resolve_party
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_quantity_micro_units
from bookflow.core.ids import new_id
from bookflow.hub.users import common
from bookflow.core.money import Money
from bookflow.core.registry import Plan, Applied, Touched


def _rows(s, table, *where):
    return effects.rows(s, table, *where)


def resolve(s, selector):
    rows = _rows(s, c.item_receipts, sa.or_(c.item_receipts.c.id == selector, c.item_receipts.c.number == selector))
    if len(rows) != 1:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'item_receipt', 'selector': selector})
    return rows[0]


def revision(s, header, number=None):
    criteria = (c.item_receipt_revisions.c.id == header['current_revision_id'] if number is None else
                c.item_receipt_revisions.c.revision_number == number)
    found = _rows(s, c.item_receipt_revisions, c.item_receipt_revisions.c.receipt_id == header['id'], criteria)
    if not found:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'item_receipt_revision'})
    return found[0]


def active_bill_claims(s, *, line_ids=None, bill_id=None):
    t = c.receipt_bill_claims
    if not sa.inspect(s.company.conn).has_table(t.name):
        return []
    where = [~sa.exists(sa.select(c.receipt_bill_releases.c.id).where(c.receipt_bill_releases.c.claim_id == t.c.id))]
    if line_ids is not None:
        where.append(t.c.receipt_line_id.in_(line_ids))
    if bill_id is not None:
        where.append(t.c.bill_id == bill_id)
    return _rows(s, t, *where)


def physical_claims(s, *, line_ids=None, order_line_ids=None):
    t = c.purchase_order_receipt_claims
    if not sa.inspect(s.company.conn).has_table(t.name):
        return []
    where = [~sa.exists(sa.select(c.purchase_order_receipt_releases.c.id).where(c.purchase_order_receipt_releases.c.claim_id == t.c.id))]
    if line_ids is not None:
        where.append(t.c.receipt_line_id.in_(line_ids))
    if order_line_ids is not None:
        where.append(t.c.order_line_id.in_(order_line_ids))
    return _rows(s, t, *where)


def output(s, header, rev, *, snapshot=None):
    data = json.loads(rev['snapshot']) if snapshot is None else snapshot
    lines = [dict(line) for line in data['items']]
    current_data = data if rev['id'] == header['current_revision_id'] else json.loads(revision(s, header)['snapshot'])
    current_ids = {line['id'] for line in current_data['items']}
    claimed, claimed_value = {}, {}
    for claim in active_bill_claims(s, line_ids=list(current_ids)):
        key = claim['receipt_line_id']
        claimed[key] = claimed.get(key, 0) + claim['end_microunits'] - claim['start_microunits']
        claimed_value[key] = claimed_value.get(key, 0) + claim['original_minor_units']
    currency = data['profile']['currency']
    for line in lines:
        live = header['status'] != 'voided' and line['id'] in current_ids
        line['unbilled_quantity_microunits'] = line['quantity_microunits'] - claimed.get(line['id'], 0) if live else 0
        line['unbilled_value'] = Money(line['amount']['minor_units'] - claimed_value.get(line['id'], 0) if live else 0, currency).to_dict()
    liability = sum(line['amount']['minor_units'] - claimed_value.get(line['id'], 0)
                    for line in current_data['items']) if header['status'] != 'voided' else 0
    return ReceiptOutput(id=header['id'], number=header['number'], version=header['version'],
        status=header['status'], revision_number=rev['revision_number'],
        current_revision_id=header['current_revision_id'], revision_id=rev['id'],
        transaction_id=header['transaction_id'], financial_revision_id=rev['financial_revision_id'],
        date=rev['date'], reference=data.get('reference'), memo=data.get('memo'),
        void_reason=data.get('void_reason'), purchase_order_id=data.get('purchase_order_id'),
        profile=data['profile'], total=data['total'], receipt_liability_current=Money(liability, currency).to_dict(), items=lines)


def show(s, inp):
    header = resolve(s, inp.receipt)
    return output(s, header, revision(s, header, inp.revision_number))


def history(s, inp):
    header = resolve(s, inp.receipt)
    rows = _rows(s, c.item_receipt_revisions, c.item_receipt_revisions.c.receipt_id == header['id'])
    return ReceiptHistoryOutput(id=header['id'], version=header['version'],
        items=[output(s, header, row) for row in sorted(rows, key=lambda r: r['revision_number'])])


def page(s, inp, ctx):
    from bookflow.company.query import continuation, page_state

    class Contract:
        cursor = inp.cursor
        query = None

        def model_dump(self, **kw):
            return inp.model_dump(**kw)

    state = page_state(s, 'item-receipt query', Contract(), ctx.on_behalf_of)
    t, r = c.item_receipts, c.item_receipt_revisions
    statement = sa.select(t).join(r, r.c.id == t.c.current_revision_id)
    if inp.vendor:
        statement = statement.where(r.c.vendor_id == resolve_party(s.company, 'vendor', inp.vendor)['id'])
    if inp.date_from:
        statement = statement.where(r.c.date >= inp.date_from)
    if inp.date_to:
        statement = statement.where(r.c.date <= inp.date_to)
    if inp.status:
        statement = statement.where(t.c.status == inp.status)
    if inp.unbilled_only:
        line, claim, release = c.item_receipt_lines, c.receipt_bill_claims, c.receipt_bill_releases
        used = sa.select(sa.func.coalesce(sa.func.sum(claim.c.end_microunits - claim.c.start_microunits), 0)).where(
            claim.c.receipt_line_id == line.c.id,
            ~sa.exists(sa.select(release.c.id).where(release.c.claim_id == claim.c.id))).scalar_subquery()
        statement = statement.where(t.c.status == 'posted', sa.exists(sa.select(line.c.id).where(
            line.c.receipt_id == t.c.id, line.c.financial_revision_id == r.c.financial_revision_id,
            line.c.quantity_microunits > used)))
    headers = list(s.company.conn.execute(statement.order_by(r.c.date, t.c.id)
        .offset(state.offset).limit(inp.limit + 1)).mappings())
    more = len(headers) > inp.limit
    found = [output(s, h, revision(s, h)) for h in headers[:inp.limit]]
    return ReceiptPageOutput(items=found, count=len(found), has_more=more,
        next_cursor=continuation(state, len(found), more), audit_watermark=state.sequence)


def _dependency(problem, **details):
    raise BookflowError('E_WORK_DEPENDENCY', message=problem, details=details)


def _order(s, selector, expected, profile, items, old_line_ids):
    if selector is None:
        if any(line.get('order_line_id') for line in items):
            raise journals.invalid('purchase_order', 'ordered line selections require a purchase order')
        return None
    header = orders.resolve(s, selector)
    if expected is None or expected != header['version']:
        raise BookflowError('E_VERSION_CONFLICT', details={'record_id': header['id'], 'current_version': header['version']})
    if header['status'] == 'voided' or orders.conversion_row(s, header['id']):
        _dependency('This purchase order is withdrawn or already consumed by its historical whole-order bill.', purchase_order_id=header['id'])
    rev = orders.revision(s, header)
    if rev['vendor_id'] != profile.vendor.id:
        raise journals.invalid('vendor', 'must match the selected purchase order vendor')
    lines = {line['line_id']: line for line in orders.saved_lines(s, rev)}
    used = {}
    for claim in physical_claims(s, order_line_ids=list(lines)):
        if claim['receipt_line_id'] not in old_line_ids:
            key = claim['order_line_id']
            used[key] = used.get(key, 0) + claim['quantity_microunits']
    for item in items:
        key = item.get('order_line_id')
        line = lines.get(key)
        if line is None or line['quantity_microunits'] is None:
            raise journals.invalid('items.order_line_id', 'must select a current quantity-bearing ordered item line')
        facts = item['profile']
        if facts.item.id != line['item_id']:
            raise journals.invalid('items.item', 'must match the selected ordered item')
        used[key] = used.get(key, 0) + facts.quantity_microunits
        if used[key] > line['quantity_microunits']:
            _dependency('Received quantity exceeds this ordered line’s remaining quantity.', order_line_id=key)
    return header, rev


def prepare(s, ctx, inp, operation):
    old = resolve(s, inp.receipt) if operation != 'post' else None
    old_rev = revision(s, old) if old else None
    saved = json.loads(old_rev['snapshot']) if old else None
    if old and inp.expected_version is not None and inp.expected_version != old['version']:
        raise BookflowError('E_VERSION_CONFLICT', details={'record_id': old['id'], 'current_version': old['version']})
    if old and old['status'] == 'voided':
        if operation != 'void':
            raise journals.invalid('receipt', 'a voided receipt cannot be updated')
        return Plan(ReceiptWriteOutput(**output(s, old, old_rev).model_dump(), changed=False),
                    dict(input=inp, operation=operation, changed=False))
    date = getattr(inp, 'date', None) or (old_rev['date'] if old else None)
    previous_items = [] if saved is None else [dict(line_id=line['line_id'], family='item',
        amount_minor_units=line['amount']['minor_units'], memo=line['description'],
        profile=BillItemProfile.model_validate(line['profile']), order_line_id=line.get('order_line_id')) for line in saved['items']]
    metadata = old is not None and operation == 'update' and not any(
        field in inp.model_fields_set for field in ('date', 'vendor', 'ap_account', 'items'))
    if operation == 'void' or metadata:
        profile = BillProfile.model_validate(saved['profile'])
        items = previous_items
    else:
        fields = {field: getattr(inp, field) for field in ('vendor', 'ap_account') if field in inp.model_fields_set}
        if old:
            typed = BillUpdateInput(bill=old['transaction_id'], **fields)
        else:
            typed = BillPostInput(date=date, items=[i.model_dump(exclude={'order_line_id'}, exclude_unset=True) for i in inp.items], **fields)
        header_facts, _ = bills.resolve_header(s, typed, date, BillProfile.model_validate(saved['profile']) if saved else None,
                                       old_rev['date'] if old_rev else None)
        profile = BillProfile(**header_facts, expense_total_minor_units=0, item_total_minor_units=0)
        currency = profile.currency
        inputs = getattr(inp, 'items', None)
        if inputs is None:
            items = previous_items
        else:
            items = check_items.resolve(s, inputs, previous_items, None, currency)
            for item, entered in zip(items, inputs):
                item['order_line_id'] = entered.order_line_id
        for item in items:
            if item['profile'].item_type not in inventory.TRACKED_TYPES:
                raise journals.invalid('items', 'item receipts receive stock items; enter other costs on a bill')
    profile = profile.model_copy(update={'item_total_minor_units': journals.checked_sum((i['amount_minor_units'] for i in items), 'items')})
    currency = profile.currency
    old_line_ids = [line['id'] for line in saved['items']] if saved else []
    order_id = saved.get('purchase_order_id') if saved else getattr(inp, 'purchase_order', None)
    order = None
    if not metadata and operation != 'void':
        order = _order(s, order_id, getattr(inp, 'purchase_order_version', None), profile, items, old_line_ids)
        if order:
            order_id = order[0]['id']
    if not metadata and old and active_bill_claims(s, line_ids=old_line_ids):
        _dependency('Release linked bill claims before changing or voiding this physical receipt.', receipt_id=old['id'])
    at, event = clock.now_iso(), new_id()
    provenance = dict(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value, audit_event_id=event)
    header = dict(old) if old else dict(id=new_id(), **common(s.actor.id, ctx.interface.value, at),
        number=inp.number or 'IR-' + new_id(), status='posted')
    if not old and _rows(s, c.item_receipts, c.item_receipts.c.number == header['number']):
        raise BookflowError('E_DUPLICATE_NUMBER', details={'number': header['number']})
    if old:
        header.update(version=old['version'] + 1, updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value)
    memo = getattr(inp, 'memo', None) if 'memo' in inp.model_fields_set else saved.get('memo') if saved else None
    reference = getattr(inp, 'reference', None) if 'reference' in inp.model_fields_set else saved.get('reference') if saved else None
    journal, stock, physical_rows = None, None, []
    view_lines = saved['items'] if saved else []
    if not metadata:
        if operation == 'void':
            journal = journals.prepare(s, ctx, JournalVoidInput(journal=old['transaction_id']), 'void', owner='inventory', purchase_items=[])
            selected_items = []
        else:
            total = journals.checked_sum((item['amount_minor_units'] for item in items), 'items')
            lines = []
            if total:
                lines.append(JournalLineInput(account=profile.ap_account.id, side='credit',
                    amount=Money(total, currency).to_dict(), name_type='vendor', name_id=profile.vendor.id))
            for item in items:
                if item['amount_minor_units']:
                    facts = item['profile']
                    lines.append(JournalLineInput(**({'line_id': item['line_id']} if item['line_id'] else {}), account=facts.account.id,
                        side='debit', amount=Money(item['amount_minor_units'], currency).to_dict(),
                        name_type='vendor', name_id=profile.vendor.id,
                        class_id=facts.class_id.id if facts.class_id else None, description=item['memo']))
            values = dict(date=date, memo=memo, lines=lines)
            typed = check_items.PurchaseUpdate(journal=old['transaction_id'], **values) if old else check_items.PurchasePost(**values)
            journal = journals.prepare(s, ctx, typed, 'update' if old else 'post', owner='inventory', force_revision=bool(old), purchase_items=items)
            selected_items = items
        journal.data['purchase_funding'] = {'account_id': profile.ap_account.id}
        _, stock, _ = check_items.attach(s, journal, selected_items)
        header['transaction_id'] = journal.data['header']['id']
        financial_revision = journal.preview.revision.id
        if operation != 'void':
            envelopes = {row['id']: row for row in journal.data['pending']['document_lines']}
            view_lines = []
            for item, movement in zip(items, [m for m in stock.movements if m.key is not None]):
                envelope = envelopes[movement.key]
                facts = item['profile']
                line = dict(id=envelope['id'], line_id=envelope['line_id'], movement_id=movement.values['id'],
                    quantity=format_quantity_micro_units(facts.quantity_microunits),
                    quantity_microunits=facts.quantity_microunits,
                    amount=Money(item['amount_minor_units'], currency).to_dict(),
                    unbilled_quantity_microunits=facts.quantity_microunits,
                    description=item['memo'], profile=facts.model_dump(), order_line_id=item.get('order_line_id'))
                view_lines.append(line)
                physical_rows.append(dict(id=line['id'], receipt_id=header['id'],
                    financial_revision_id=financial_revision, movement_id=line['movement_id'], item_id=facts.item.id,
                    quantity_microunits=facts.quantity_microunits, value_minor_units=item['amount_minor_units'],
                    snapshot=json.dumps(line, sort_keys=True), **provenance))
    else:
        financial_revision = old_rev['financial_revision_id']
    snapshot = dict(reference=reference, memo=memo, purchase_order_id=order_id,
        profile=profile.model_dump(), total=Money(sum(item['amount_minor_units'] for item in items), currency).to_dict(), items=view_lines)
    if operation == 'void':
        header['status'] = 'voided'
        snapshot['void_reason'] = ctx.reason.strip()
    if metadata and snapshot == saved:
        return Plan(ReceiptWriteOutput(**output(s, old, old_rev).model_dump(), changed=False), dict(input=inp, operation=operation, changed=False))
    rev = dict(id=new_id(), receipt_id=header['id'], revision_number=old_rev['revision_number'] + 1 if old else 1,
        financial_revision_id=financial_revision, date=date, vendor_id=profile.vendor.id, ap_account_id=profile.ap_account.id,
        snapshot=json.dumps(snapshot, sort_keys=True), **provenance)
    header['current_revision_id'] = rev['id']
    pending = {'item_receipt_revisions': [rev], 'item_receipt_lines': physical_rows,
               'purchase_order_receipt_claims': [], 'purchase_order_receipt_releases': []}
    if not metadata:
        pending['purchase_order_receipt_releases'] = [dict(id=new_id(), claim_id=claim['id'], **provenance)
            for claim in physical_claims(s, line_ids=old_line_ids)]
        if order and operation != 'void':
            pending['purchase_order_receipt_claims'] = [dict(id=new_id(), order_line_id=line['order_line_id'],
                order_revision_id=order[1]['id'], receipt_line_id=line['id'], quantity_microunits=line['quantity_microunits'],
                **provenance) for line in view_lines]
    result = ReceiptWriteOutput(**output(s, header, rev, snapshot=snapshot).model_dump())
    return Plan(result, dict(input=inp, operation=operation, changed=True, header=header, before=old,
                             pending=pending, journal=journal, stock=stock, event=event,
                             order_transition=_order_transition(s, order_id, pending, provenance) if not metadata else None))


def apply(plan, ctx, s):
    fresh = prepare(s, ctx, plan.data['input'], plan.data['operation'])
    data = fresh.data
    if not data['changed']:
        return Applied(fresh.preview, [], 'no change')
    header, old = data['header'], data['before']
    command = 'item-receipt ' + data['operation']
    touched = []
    if data['journal']:
        accounting = journals.persist_prepared(data['journal'], ctx, s, command_name=command, noun='item receipt')
        accounting = inventory_effects.settle(accounting, data['stock'], ctx, s, command_name=command,
            summary='Stock received by ' + header['number'], created_at=header['updated_at'])
        touched.extend(accounting.touched)
    own = [Touched('item_receipt', header['id'], 'update' if old else 'create',
        old['version'] if old else None, header['version'], header, old, db='company')]
    for name, rows in data['pending'].items():
        own.extend(Touched(name, row['id'], 'create', None, 1, row, db='company') for row in rows)
    transition = data['order_transition']
    if transition:
        old_order, new_order, order_rev, order_lines = transition
        own.append(Touched('purchase_order', new_order['id'], 'update', old_order['version'], new_order['version'], new_order, old_order, db='company'))
        own.append(Touched('purchase_order_revision', order_rev['id'], 'create', None, 1, order_rev, db='company'))
        own.extend(Touched('purchase_order_line', line['id'], 'create', None, 1, line, db='company') for line in order_lines)
    summary = data['operation'] + ' item receipt ' + header['number']
    audit.write_event_to(s.company, ctx, command, summary, own, actor_id=s.actor.id,
        actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None), event_id=data['event'])
    if old:
        n = s.company.conn.execute(c.item_receipts.update().where(c.item_receipts.c.id == old['id'],
            c.item_receipts.c.version == old['version']).values(**header)).rowcount
        if n != 1:
            raise BookflowError('E_VERSION_CONFLICT', details={'record_id': old['id']})
    else:
        s.company.conn.execute(c.item_receipts.insert().values(**header))
    for name, rows in data['pending'].items():
        if rows:
            s.company.conn.execute(getattr(c, name).insert(), rows)
    if transition:
        n = s.company.conn.execute(c.purchase_orders.update().where(c.purchase_orders.c.id == old_order['id'], c.purchase_orders.c.version == old_order['version']).values(**new_order)).rowcount
        if n != 1:
            raise BookflowError('E_VERSION_CONFLICT', details={'record_id': old_order['id']})
        s.company.conn.execute(c.purchase_order_revisions.insert().values(**order_rev))
        s.company.conn.execute(c.purchase_order_lines.insert(), order_lines)
    return Applied(fresh.preview, touched + own, summary, audited=True)


def order_remaining(s, header, rev):
    lines = orders.saved_lines(s, rev)
    used = {}
    for claim in physical_claims(s, order_line_ids=[line['line_id'] for line in lines]):
        key = claim['order_line_id']
        used[key] = used.get(key, 0) + claim['quantity_microunits']
    legacy = orders.conversion_row(s, header['id']) is not None
    return [dict(order_line_id=line['line_id'], item_id=line['item_id'],
                 ordered_quantity_microunits=line['quantity_microunits'],
                 received_quantity_microunits=used.get(line['line_id'], 0),
                 remaining_quantity_microunits=0 if legacy else line['quantity_microunits'] - used.get(line['line_id'], 0))
            for line in lines if line['item_id'] and line['quantity_microunits'] is not None]


def _order_transition(s, order_id, pending, provenance):
    if order_id is None:
        return None
    header = orders.resolve(s, order_id)
    rev = orders.revision(s, header)
    lines = orders.saved_lines(s, rev)
    active = physical_claims(s, order_line_ids=[line['line_id'] for line in lines])
    released = {row['claim_id'] for row in pending['purchase_order_receipt_releases']}
    active = [row for row in active if row['id'] not in released] + pending['purchase_order_receipt_claims']
    used = {}
    for claim in active:
        key = claim['order_line_id']
        used[key] = used.get(key, 0) + claim['quantity_microunits']
    complete = all(line['quantity_microunits'] is not None and
                   used.get(line['line_id'], 0) == line['quantity_microunits'] for line in lines)
    state = 'closed' if complete else 'partly_received' if active else 'open'
    created = {k: provenance[k] for k in ('created_at', 'created_by', 'created_via')}
    new_rev = dict(rev, id=new_id(), **created, status=state,
        revision_number=rev['revision_number'] + 1, supersedes_revision_id=rev['id'], audit_event_id=provenance['audit_event_id'])
    new_header = dict(header, status=state, version=header['version'] + 1, current_revision_id=new_rev['id'],
        updated_at=created['created_at'], updated_by=created['created_by'], updated_via=created['created_via'])
    carried = [dict(line, id=new_id(), **created, revision_id=new_rev['id']) for line in lines]
    return header, new_header, new_rev, carried
