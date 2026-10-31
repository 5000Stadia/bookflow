"""Drawer counts and reports, using original POS receipt and return facts."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import json
import sqlalchemy as sa
from bookflow.company import schema as c, pos_layer, pos_sales
from bookflow.company import pos_day_models as m
from bookflow.core import clock
from bookflow.core.ids import new_id
from bookflow.core.money import Money
from bookflow.core.errors import BookflowError
from bookflow.core.registry import Plan, Applied, Touched


def authorize(s):
    pos_layer.require_enabled(s)
    pos_layer.require_seller(s)


def active_shift(s, reg, *, required=True):
    row = s.company.conn.execute(sa.select(c.pos_shifts).where(
        c.pos_shifts.c.register_id == reg['id'], c.pos_shifts.c.closed_at.is_(None))).mappings().first()
    if row is None and required and reg['count_shifts']:
        raise BookflowError('E_WORK_DEPENDENCY', message='Open a shift before taking money on this register.',
                            details={'register_id': reg['id'], 'next': 'pos shift open'})
    return dict(row) if row else None


def resolve_shift(s, selector):
    row = s.company.conn.execute(sa.select(c.pos_shifts).where(c.pos_shifts.c.id == selector)).mappings().first()
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'pos_shift', 'selector': selector, 'suggestions': []})
    return dict(row)


def _local_day(s, timestamp):
    return datetime.fromisoformat(timestamp.replace('Z', '+00:00')).astimezone(ZoneInfo(s.company_tz)).date().isoformat()


def captured_method_kind(s, reference, fallback=None):
    """Sales references capture a list version; recover its kind from immutable list audit facts."""
    from bookflow.core.audit import decode_snapshot
    method = s.company.conn.execute(sa.select(c.payment_methods).where(
        c.payment_methods.c.id == reference['id'])).mappings().one()
    if method['version'] == reference['version']:
        return method['kind']
    for version, snapshot in ((c.audit_entries.c.version_after, c.audit_entries.c.after),
                              (c.audit_entries.c.version_before, c.audit_entries.c.before)):
        blob = s.company.conn.execute(sa.select(snapshot).where(
            c.audit_entries.c.record_type == 'payment_method', c.audit_entries.c.record_id == reference['id'],
            version == reference['version']).order_by(c.audit_entries.c.id).limit(1)).scalar()
        facts = decode_snapshot(blob)
        if facts and 'kind' in facts:
            return facts['kind']
    return fallback or method['kind']


def _totals(s, reg, date, shift=None):
    cur = pos_sales._currency(s)
    totals = dict(sales_count=0, gross_sales=0, discounts=0, tax=0, total_sales=0,
                  refunds_total=0, refund_tax=0, voids_count=0, voids_total=0, cash_sales=0, cash_refunds=0)
    methods = {r['id']: dict(r) for r in s.company.conn.execute(sa.select(c.payment_methods)).mappings()}
    tenders = {}
    def tender(mid, kind):
        key = (mid, kind)
        if key not in tenders:
            method = methods.get(mid, {'id': mid, 'name': mid})
            tenders[key] = dict(payment_method={'id': mid, 'name': method['name'], 'kind': kind}, count=0, amount=0, refund_count=0, refund_amount=0)
        return tenders[key]
    p,t,r,f = c.pos_sales,c.transactions,c.transaction_revisions,c.sales_profiles
    rows = s.company.conn.execute(sa.select(p, r.c.date.label('sale_date'), r.c.id.label('revision_id'),
        t.c.status, t.c.voided_at, r.c.revision_number, f.c.profile_snapshot, f.c.subtotal_minor_units, f.c.tax_minor_units).select_from(
        p.join(t,t.c.id == p.c.document_id).join(r,r.c.id == t.c.current_revision_id)
        .join(f,f.c.revision_id == r.c.id)).where(p.c.register_id == reg['id'])).mappings()
    for row in rows:
        if shift and row['shift_id'] != shift['id']:
            continue
        if not shift and row['sale_date'] != date:
            continue
        # Ordinary uncounted corrections follow current accounting facts; counted corrections are refused.
        sale_total = row['subtotal_minor_units'] + row['tax_minor_units']
        if row['status'] == 'voided':
            totals['voids_count'] += 1
            totals['voids_total'] += sale_total
            continue
        totals['sales_count'] += 1
        totals['total_sales'] += sale_total
        totals['tax'] += row['tax_minor_units']
        discount = 0
        for snapshot in s.company.conn.execute(sa.select(c.sales_line_profiles.c.item_snapshot).where(
                c.sales_line_profiles.c.revision_id == row['revision_id'])).scalars():
            adj = json.loads(snapshot).get('adjustment') or {}
            if adj.get('kind') == 'discount':
                discount += sum(x['amount_minor_units'] for x in adj.get('targets', []))
        totals['discounts'] += discount
        totals['gross_sales'] += row['subtotal_minor_units'] + discount
        change = row['change_minor_units']
        payments = json.loads(row['tenders'])
        if row['revision_number'] > 1 and row['document_type'] == 'sales_receipt':
            reference = json.loads(row['profile_snapshot'])['payment_method']
            method_id = reference['id']
            captured_kind = captured_method_kind(s, reference, next((p['kind'] for p in payments if p['payment_method_id'] == method_id), None))
            payments = [dict(payment_method_id=method_id, kind=captured_kind, amount_minor_units=sale_total)]
            change = 0
        for payment in payments:
            amount = payment['amount_minor_units']
            if payment['kind'] == 'cash':
                deduction = min(change, amount)
                amount -= deduction
                change -= deduction
                totals['cash_sales'] += amount
            bucket = tender(payment['payment_method_id'], payment['kind'])
            bucket['count'] += 1
            bucket['amount'] += amount
    for row in s.company.conn.execute(sa.select(c.pos_refunds).where(c.pos_refunds.c.register_id == reg['id'])).mappings():
        if shift and row['shift_id'] != shift['id']:
            continue
        memo = s.company.conn.execute(sa.select(c.transactions.c.status,c.transaction_revisions.c.date,
            c.credit_profiles.c.subtotal_minor_units,c.credit_profiles.c.tax_minor_units).select_from(
            c.transactions.join(c.transaction_revisions,c.transaction_revisions.c.id == c.transactions.c.current_revision_id)
            .join(c.credit_profiles,c.credit_profiles.c.revision_id == c.transaction_revisions.c.id))
            .where(c.transactions.c.id == row['credit_memo_id'])).mappings().one()
        if memo['status'] == 'posted' and (shift or memo['date'] == date):
            totals['refunds_total'] += memo['subtotal_minor_units'] + memo['tax_minor_units']
            totals['refund_tax'] += memo['tax_minor_units']
        # A split-tender return pays out once per original tender (increment 11); the row's refund_id is
        # only the first payout, so read every payout from the captured result, falling back to refund_id.
        payout_ids = [x['refund']['id'] for x in json.loads(row['result_json'] or '{}').get('tenders') or [] if x.get('refund')]
        if not payout_ids and row['refund_id']:
            payout_ids = [row['refund_id']]
        for payout_id in payout_ids:
            payout = s.company.conn.execute(sa.select(c.transactions.c.status,c.transaction_revisions.c.date,
                c.customer_refund_profiles.c.amount_minor_units,c.customer_refund_profiles.c.payment_method_id,
                c.customer_refund_profiles.c.profile_snapshot).select_from(
                c.transactions.join(c.transaction_revisions,c.transaction_revisions.c.id == c.transactions.c.current_revision_id)
                .join(c.customer_refund_profiles,c.customer_refund_profiles.c.revision_id == c.transaction_revisions.c.id))
                .where(c.transactions.c.id == payout_id)).mappings().one()
            if payout['status'] == 'posted' and (shift or payout['date'] == date):
                method = json.loads(payout['profile_snapshot'])['payment_method']
                amount = payout['amount_minor_units']
                bucket = tender(method['id'], method['kind'])
                bucket['refund_count'] += 1
                bucket['refund_amount'] += amount
                if method['kind'] == 'cash':
                    totals['cash_refunds'] += amount
    totals['cash_to_deposit'] = totals['cash_sales'] - totals['cash_refunds']
    for bucket in tenders.values():
        bucket['amount'] = Money(bucket['amount'], cur).to_dict()
        bucket['refund_amount'] = Money(bucket['refund_amount'], cur).to_dict()
    return totals, sorted(tenders.values(), key=lambda x: (x['payment_method']['name'], x['payment_method']['id']))


def shift_output(s, row, reg=None, *, replayed=False):
    reg = reg or pos_sales.register(s, row['register_id'])
    cur = row['currency']
    expected = row['expected_minor_units']
    if expected is None:
        totals, _ = _totals(s, reg, row['date'], row)
        expected = row['float_minor_units'] + totals['cash_to_deposit']
    money = lambda v: Money(v, cur).to_dict() if v is not None else None
    return m.ShiftOutput(id=row['id'], pos_register={'id': reg['id'], 'name': reg['name']},
        status='closed' if row['closed_at'] else 'open', date=row['date'], opened_at=row['opened_at'],
        opened_by=row['opened_by'], float=money(row['float_minor_units']), closed_at=row['closed_at'],
        closed_by=row['closed_by'], counted=money(row['counted_minor_units']), expected=money(expected),
        over_short=money(row['over_short_minor_units']), replayed=replayed)


def report(s, inp):
    pos_layer.require_enabled(s)
    selected = resolve_shift(s, inp.shift) if inp.shift else None
    reg = pos_sales.register(s, inp.pos_register or (selected['register_id'] if selected else None))
    if selected and selected['register_id'] != reg['id']:
        raise pos_sales._invalid('shift', 'this shift belongs to another register')
    date = inp.date or (selected['date'] if selected else pos_sales._today(s))
    if selected and inp.date and inp.date != selected['date']:
        raise pos_sales._invalid('date', 'a shift report uses its opening date')
    if selected and selected['report_json']:
        return m.DayOutput.model_validate_json(selected['report_json'])
    totals, tenders = _totals(s, reg, date, selected)
    cur = pos_sales._currency(s)
    shifts = [selected] if selected else [dict(x) for x in s.company.conn.execute(sa.select(c.pos_shifts).where(
        c.pos_shifts.c.register_id == reg['id']).order_by(c.pos_shifts.c.opened_at)).mappings()
        if x['date'] <= date <= (_local_day(s, x['closed_at']) if x['closed_at'] else pos_sales._today(s))]
    shift_outputs = [shift_output(s, x, reg) for x in shifts]
    current = next((x for x in shift_outputs if x.status == 'open'), None)
    expected = (current.expected.minor_units if current else
                shift_outputs[-1].expected.minor_units if selected or shift_outputs else totals['cash_to_deposit'])
    cutoff = datetime.fromisoformat(date).date() - timedelta(days=7)
    held = []
    for ticket in s.company.conn.execute(sa.select(c.pos_tickets).where(c.pos_tickets.c.register_id == reg['id'],
            c.pos_tickets.c.status.in_(['held','open'])).order_by(c.pos_tickets.c.updated_at)).mappings():
        held.append(dict(id=ticket['id'], label=ticket['label'], status=ticket['status'], held_at=ticket['created_at'],
            older_than_seven_days=_local_day(s,ticket['created_at']) < cutoff.isoformat(),
            total=Money(ticket['total_minor_units'], ticket['currency']).to_dict()))
    money_totals = {k: (v if k.endswith('_count') else Money(v, cur).to_dict()) for k,v in totals.items()}
    return m.DayOutput(date=date,pos_register={'id':reg['id'],'name':reg['name']},shift=selected['id'] if selected else None,
        **money_totals,expected_drawer=Money(expected,cur).to_dict(),by_tender=tenders,shifts=shift_outputs,held_tickets=held)


def _mismatch(key):
    raise BookflowError('E_IDEMPOTENCY_MISMATCH', message='This operation key already names a different drawer count.',
                        details={'operation_key': key})


def prepare_open(s, ctx, inp):
    authorize(s)
    reg = pos_sales.register(s, inp.pos_register)
    cur = pos_sales._currency(s)
    amount = Money.parse(inp.float,cur).minor_units
    existing = s.company.conn.execute(sa.select(c.pos_shifts).where(c.pos_shifts.c.open_key == inp.operation_key)).mappings().first()
    if existing:
        if existing['register_id'] != reg['id'] or existing['float_minor_units'] != amount:
            _mismatch(inp.operation_key)
        return Plan(shift_output(s,existing,reg,replayed=True), {'replay':True,'input':inp})
    if not reg['count_shifts']:
        raise pos_sales._invalid('pos_register', 'turn on counted shifts in this register first')
    active = active_shift(s,reg,required=False)
    if active:
        raise BookflowError('E_WORK_DEPENDENCY', message='This register already has an open shift.',details={'shift':active['id']})
    row = dict(id=new_id(),register_id=reg['id'],date=pos_sales._today(s),opened_at=clock.now_iso(),opened_by=s.actor.id,
        currency=cur,float_minor_units=amount,open_key=inp.operation_key,closed_at=None,closed_by=None,
        counted_minor_units=None,expected_minor_units=None,over_short_minor_units=None,close_key=None,report_json=None)
    return Plan(shift_output(s,row,reg),{'input':inp,'row':row})


def apply_open(plan,ctx,s):
    fresh = prepare_open(s,ctx,plan.data['input'])
    if fresh.data.get('replay'):
        return Applied(fresh.preview,[],'no change')
    row = fresh.data['row']
    s.company.conn.execute(c.pos_shifts.insert().values(**row))
    return Applied(fresh.preview,[Touched('pos_shift',row['id'],'create',None,1,row,db='company')], 'opened POS shift')


def prepare_close(s,ctx,inp):
    authorize(s)
    row = resolve_shift(s,inp.shift)
    amount = Money.parse(inp.counted,row['currency']).minor_units
    used = s.company.conn.execute(sa.select(c.pos_shifts.c.id).where(c.pos_shifts.c.close_key == inp.operation_key)).scalar()
    if used and used != row['id']:
        _mismatch(inp.operation_key)
    if row['closed_at']:
        if row['close_key'] != inp.operation_key:
            raise BookflowError('E_WORK_DEPENDENCY', message='This shift is already closed.', details={'shift':row['id']})
        if row['counted_minor_units'] != amount:
            _mismatch(inp.operation_key)
        return Plan(shift_output(s,row,replayed=True),{'input':inp,'replay':True})
    day = report(s,m.DayInput(shift=row['id']))
    row.update(closed_at=clock.now_iso(),closed_by=s.actor.id,counted_minor_units=amount,
        expected_minor_units=day.expected_drawer.minor_units,over_short_minor_units=amount-day.expected_drawer.minor_units,
        close_key=inp.operation_key)
    out = shift_output(s,row)
    day.shifts = [out]
    row['report_json'] = day.model_dump_json()
    return Plan(out,{'input':inp,'row':row})


def apply_close(plan,ctx,s):
    fresh = prepare_close(s,ctx,plan.data['input'])
    if fresh.data.get('replay'):
        return Applied(fresh.preview,[],'no change')
    row = fresh.data['row']
    s.company.conn.execute(c.pos_shifts.update().where(c.pos_shifts.c.id == row['id']).values(**row))
    return Applied(fresh.preview,[Touched('pos_shift',row['id'],'update',1,2,row,db='company')], 'closed POS shift')


def guard_closed_document(s, document_id, *, correcting=False):
    """A counted Z-report cannot be rewritten through the ordinary accounting forms."""
    shifts = c.pos_shifts
    sale_shift = s.company.conn.execute(sa.select(shifts.c.id).join(c.pos_sales,
        c.pos_sales.c.shift_id == shifts.c.id).where(c.pos_sales.c.document_id == document_id,
        sa.or_(sa.literal(correcting), shifts.c.closed_at.is_not(None)))).scalar()
    refund_shift = s.company.conn.execute(sa.select(shifts.c.id).join(c.pos_refunds,
        c.pos_refunds.c.shift_id == shifts.c.id).where(sa.or_(c.pos_refunds.c.refund_id == document_id,
        c.pos_refunds.c.credit_memo_id == document_id),sa.or_(sa.literal(correcting), shifts.c.closed_at.is_not(None)))).scalar()
    if not refund_shift:  # a split return's later payouts are named only in its captured tenders
        refund_shift = s.company.conn.execute(sa.select(shifts.c.id).join(c.pos_refunds,
            c.pos_refunds.c.shift_id == shifts.c.id).where(c.pos_refunds.c.result_json.contains(f'"refund":{{"id":"{document_id}"'),
            sa.or_(sa.literal(correcting), shifts.c.closed_at.is_not(None)))).scalar()
    if sale_shift or refund_shift:
        raise BookflowError('E_WORK_DEPENDENCY', message='A counted POS shift holds this document. Record a new return or adjustment instead of changing its recorded cash history.',
            details={'shift':sale_shift or refund_shift,'document_id':document_id})
