"""Shared held carts, strict versioned transitions, and completion guard."""
import json
import sqlalchemy as sa
from bookflow.company import schema as c, pos_layer, pos_sales
from bookflow.company import pos_ticket_models as m
from bookflow.company.pos_sale_models import PosSalePreviewInput
from bookflow.core import clock, versioning, audit
from bookflow.core.errors import BookflowError
from bookflow.core.money import Money
from bookflow.core.registry import Plan, Applied, Touched


def find(s, ticket):
    row = s.company.conn.execute(sa.select(c.pos_tickets).where(c.pos_tickets.c.id == ticket)).mappings().first()
    return dict(row) if row else None


def resolve(s, ticket):
    row = find(s, ticket)
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'pos_ticket', 'selector': ticket,
                                                         'suggestions': []})
    return row


def conflict(s, row, expected):
    from bookflow.hub.users import user_names
    names = user_names(s, {row['updated_by']})
    entries = versioning.history_from_entries(s.company, 'pos_ticket', row['id'], expected or 0, audit.decode_snapshot)
    fields = sorted({key for entry in entries for key in (entry.changed_columns or [])})
    details = dict(record_type='pos_ticket', record_id=row['id'], expected_version=expected,
        current_version=row['version'], updated_by=row['updated_by'], updated_by_name=names.get(row['updated_by']),
        updated_via=row['updated_via'], seconds_since_update=versioning._seconds_since(row['updated_at']),
        changed_fields=fields, status=row['status'], label=row['label'])
    return BookflowError('E_VERSION_CONFLICT', message=versioning._conflict_message(details, fields), details=details)


def check(s, row, expected, states):
    if row['version'] != expected:
        raise conflict(s, row, expected)
    if row['status'] not in states:
        raise BookflowError('E_WORK_DEPENDENCY', message=f"Ticket {row['label']} is {row['status']}; it cannot be used for this action.",
            details={'ticket': row['id'], 'label': row['label'], 'status': row['status']})


def output(s, row):
    from bookflow.hub.users import user_names
    reg = pos_layer.resolve(s, row['register_id'])
    chosen = None
    if row['customer_id']:
        name = s.company.conn.execute(sa.select(c.customers.c.full_name).where(c.customers.c.id == row['customer_id'])).scalar_one()
        chosen = dict(id=row['customer_id'], name=name)
    names = user_names(s, {row['updated_by']})
    return m.TicketOutput(**{key: row[key] for key in ('id', 'version', 'status', 'label', 'created_at', 'created_by',
        'created_via', 'updated_at', 'updated_by', 'updated_via')},
        pos_register=dict(id=reg['id'], name=reg['name']), customer=chosen, lines=json.loads(row['lines']),
        total=Money(row['total_minor_units'], row['currency']).to_dict(),
        clerk_id=row['updated_by'], clerk_name=names.get(row['updated_by'], row['updated_by']))


def authorize(inp, ctx, s):
    pos_layer.require_enabled(s)
    pos_layer.require_seller(s)


def listing(s):
    rows = s.company.conn.execute(sa.select(c.pos_tickets).where(c.pos_tickets.c.status == 'held').order_by(
        c.pos_tickets.c.updated_at.desc(), c.pos_tickets.c.id.desc())).mappings().all()
    return m.TicketList(items=[output(s, row) for row in rows], count=len(rows))


def plan_hold(s, ctx, inp):
    from bookflow.company.items import resolve_item
    if pos_sales._existing(s, inp.ticket):
        raise BookflowError('E_WORK_DEPENDENCY', message='This ticket has already completed.', details={'ticket': inp.ticket})
    before = find(s, inp.ticket)
    if before:
        check(s, before, inp.expected_version, ('open',))
    elif inp.expected_version is not None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'pos_ticket', 'selector': inp.ticket, 'suggestions': []})
    if not inp.lines:
        raise pos_sales._invalid('lines', 'the cart is empty')
    reg = pos_sales.register(s, inp.pos_register)
    if before and reg['id'] != before['register_id']:
        raise pos_sales._invalid('pos_register', 'a resumed ticket keeps its original register')
    chosen = pos_sales.customer(s, inp.customer)
    lines = []
    first_name = ''
    for line in inp.lines:
        item = resolve_item(s.company, line.item)
        first_name = first_name or item['name']
        lines.append(dict(line.model_dump(mode='json'), item=item['id']))
    preview = pos_sales.preview(s, ctx, PosSalePreviewInput(pos_register=reg['id'], customer=chosen['id'] if chosen else None,
                                                         lines=lines))
    now = clock.now_iso()
    after = dict(before or {}, id=inp.ticket, version=before['version'] + 1 if before else 1,
        register_id=reg['id'], customer_id=chosen['id'] if chosen else None,
        label=(inp.label or '').strip() or (before['label'] if before else f'{now} · {first_name}'), status='held',
        lines=json.dumps(lines), currency=preview.total.currency, total_minor_units=preview.total.minor_units,
        updated_at=now, updated_by=s.actor.id, updated_via=ctx.interface.value)
    if not before:
        after.update(created_at=now, created_by=s.actor.id, created_via=ctx.interface.value)
    return Plan(output(s, after), dict(before=before, after=after))


def plan_transition(s, ctx, inp, status):
    before = resolve(s, inp.ticket)
    check(s, before, inp.expected_version, ('held',) if status == 'open' else ('held', 'open'))
    after = dict(before, status=status, version=before['version'] + 1, updated_at=clock.now_iso(),
                 updated_by=s.actor.id, updated_via=ctx.interface.value)
    return Plan(output(s, after), dict(before=before, after=after))


def persist(s, before, after):
    table = c.pos_tickets
    current = find(s, after['id'])
    if before is None:
        if current:
            raise conflict(s, current, 0)
        s.company.conn.execute(table.insert().values(**after))
    else:
        check(s, current, before['version'], (before['status'],))
        changed = s.company.conn.execute(table.update().where(table.c.id == before['id'],
            table.c.version == before['version']).values(**after)).rowcount
        if changed != 1:
            raise conflict(s, resolve(s, before['id']), before['version'])
    return Touched('pos_ticket', after['id'], 'update' if before else 'create',
                   before['version'] if before else None, after['version'], after, before, db='company')


def apply(plan, ctx, s):
    pos_layer.require_enabled(s)
    if pos_sales._existing(s, plan.data['after']['id']):
        raise BookflowError('E_WORK_DEPENDENCY', message='This ticket has already completed.', details={'ticket': plan.data['after']['id']})
    touched = persist(s, plan.data['before'], plan.data['after'])
    return Applied(output(s, plan.data['after']), [touched], f"Ticket {plan.preview.label}: {plan.preview.status}")


def check_complete(s, ticket, version, register_id=None):
    row = find(s, ticket)
    if row:
        check(s, row, version, ('open',))
        if register_id and row['register_id'] != register_id:
            raise pos_sales._invalid('pos_register', 'a resumed ticket keeps its original register')
    elif version is not None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'pos_ticket', 'selector': ticket, 'suggestions': []})
    return row


def complete(s, ctx, ticket, version, register_id):
    before = check_complete(s, ticket, version, register_id)
    if before is None:
        return []
    after = dict(before, status='completed', version=before['version'] + 1, updated_at=clock.now_iso(),
                 updated_by=s.actor.id, updated_via=ctx.interface.value)
    return [persist(s, before, after)]
