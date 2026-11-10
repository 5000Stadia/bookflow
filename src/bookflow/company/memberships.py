"""Membership plans and dated customer enrollments (R188 increment 1).

A plan points at an existing price level. An enrollment lets one customer (or job, itself) use one
plan on invoices dated from ``start_date`` through ``end_date``, both inclusive company-local dates.
The sales resolver asks :func:`effective_fact` for the sold-to customer and the invoice date; with
the layer off, or no covering enrollment, it is never consulted and prices resolve as before.
Nothing here edits the customer's ordinary price level or any saved sale.
"""
from __future__ import annotations

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.company import membership_models as m
from bookflow.company.list_service import resolve_selector
from bookflow.company.lists import get_list_definition, normalize_display_name
from bookflow.company.membership_schema import FLAG
from bookflow.company.sales_facts import MembershipFact, Reference
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import is_ulid, new_id, normalize_ulid
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.hub.users import common

PLAN = 'membership_plan'
ENROLLMENT = 'customer_membership'
_PLAN_FIELDS = ('name', 'name_key', 'description', 'price_level_id', 'active')
_ENROLLMENT_FIELDS = ('customer_id', 'plan_id', 'start_date', 'end_date', 'status', 'member_number', 'member_number_key')


def _invalid(field: str, problem: str) -> BookflowError:
    return BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': problem}]})


def enabled(db) -> bool:
    return bool(db.conn.execute(sa.select(c.company_info.c[FLAG])).scalar())


def _require_enabled(db) -> None:
    if not enabled(db):
        raise BookflowError('E_FEATURE_DISABLED', details={
            'feature': 'membership_pricing', 'setting': FLAG,
            'enable_command': 'company update --membership-pricing-enabled true'})


def today(s) -> str:
    from bookflow.company.memorized_schedule import today as company_today
    return company_today(getattr(s, 'company_tz', None))


def _version_check(row: dict, expected: int | None, record_type: str) -> None:
    if expected is not None and expected != row['version']:
        raise BookflowError('E_VERSION_CONFLICT', details={
            'record_type': record_type, 'record_id': row['id'], 'current_version': row['version'],
            'expected_version': expected, 'updated_at': row['updated_at'], 'updated_by': row['updated_by']})


def _bumped(row: dict, s, ctx) -> dict:
    return dict(row, version=row['version'] + 1, updated_at=clock.now_iso(), updated_by=s.actor.id,
                updated_via=ctx.interface.value)


# ---------------------------------------------------------------------------- plans

def resolve_plan(db, selector: str) -> dict:
    t = c.membership_plans
    text = selector.strip()
    row = None
    if is_ulid(text):
        row = db.conn.execute(sa.select(t).where(t.c.id == normalize_ulid(text))).mappings().first()
    if row is None and text:
        _, key = normalize_display_name(text, field='plan')
        row = db.conn.execute(sa.select(t).where(t.c.name_key == key)).mappings().first()
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': PLAN, 'selector': selector, 'field': 'plan'})
    return dict(row)


def _price_level(db, selector: str) -> dict:
    row = resolve_selector(db, c.price_levels, get_list_definition('price-level'), selector)
    if not row['active']:
        raise BookflowError('E_INACTIVE_REFERENCE', details={'field': 'price_level', 'record_id': row['id']})
    home = db.conn.execute(sa.select(c.company_info.c.home_currency)).scalar_one()
    if (row.get('currency') or home) != home:  # null selects the home currency
        raise _invalid('price_level', 'price level must use home currency')
    return row


def _name(db, name: str, own: str | None = None) -> tuple[str, str]:
    display, key = normalize_display_name(name, field='name')
    query = sa.select(c.membership_plans.c.id).where(c.membership_plans.c.name_key == key)
    if own:
        query = query.where(c.membership_plans.c.id != own)
    clash = db.conn.execute(query).first()
    if clash:
        raise BookflowError('E_NAME_TAKEN', details={'record_type': PLAN, 'name': display, 'record_id': clash[0]})
    return display, key


def _level_label(db, level_id: str) -> str:
    return db.conn.execute(sa.select(c.price_levels.c.name).where(c.price_levels.c.id == level_id)).scalar_one()


def plan_output(db, row: dict, cls=m.PlanOutput, **extra):
    count = db.conn.execute(sa.select(sa.func.count()).select_from(c.customer_memberships).where(
        c.customer_memberships.c.plan_id == row['id'])).scalar_one()
    return cls(id=row['id'], version=row['version'], name=row['name'], description=row['description'],
               active=bool(row['active']), price_level=m.Named(id=row['price_level_id'], label=_level_label(db, row['price_level_id'])),
               enrollments=count, **extra)


def plan_create(s, ctx, inp) -> Plan:
    db = s.company
    _require_enabled(db)
    display, key = _name(db, inp.name)
    level = _price_level(db, inp.price_level)
    row = dict(id=new_id(), **common(s.actor.id, ctx.interface.value, clock.now_iso()), name=display, name_key=key,
               description=inp.description or None, price_level_id=level['id'], active=True)
    return Plan(plan_output(db, row, m.PlanWriteOutput), dict(row=row, before=None))


def plan_update(s, ctx, inp) -> Plan:
    db = s.company
    _require_enabled(db)
    old = resolve_plan(db, inp.plan)
    _version_check(old, inp.expected_version, PLAN)
    new = dict(old)
    if inp.name is not None:
        new['name'], new['name_key'] = _name(db, inp.name, old['id'])
    if 'description' in inp.model_fields_set:
        new['description'] = inp.description or None
    if inp.price_level is not None:
        new['price_level_id'] = _price_level(db, inp.price_level)['id']
    if 'active' in inp.model_fields_set:
        if inp.active is None:
            raise _invalid('active', 'give true or false')
        new['active'] = inp.active
    changed = any(new[k] != old[k] for k in _PLAN_FIELDS)
    if changed:
        new = _bumped(new, s, ctx)
    return Plan(plan_output(db, new, m.PlanWriteOutput, changed=changed), dict(row=new, before=old if changed else None, changed=changed))


def apply_plan(plan: Plan, ctx, s) -> Applied:
    row, before = plan.data['row'], plan.data['before']
    if before is None and plan.data.get('changed') is False:
        return Applied(plan.preview, [], 'no change')
    db = s.company
    if before is None:
        db.conn.execute(c.membership_plans.insert().values(**row))
        return Applied(plan.preview, [Touched(PLAN, row['id'], 'create', None, 1, row, db='company')],
                       f"added membership plan {row['name']}")
    done = db.conn.execute(c.membership_plans.update().where(
        c.membership_plans.c.id == row['id'], c.membership_plans.c.version == before['version']).values(**row)).rowcount
    if done != 1:
        raise BookflowError('E_VERSION_CONFLICT', details={'record_type': PLAN, 'record_id': row['id']})
    return Applied(plan.preview, [Touched(PLAN, row['id'], 'update', before['version'], row['version'], row, before,
                                          db='company')], f"updated membership plan {row['name']}")


def plan_show(s, inp) -> m.PlanOutput:
    return plan_output(s.company, resolve_plan(s.company, inp.plan))


def plan_list(s, inp) -> m.PlanPage:
    db = s.company
    t = c.membership_plans
    query = sa.select(t).order_by(t.c.name_key, t.c.id)
    if not inp.include_inactive:
        query = query.where(t.c.active.is_(True))
    if inp.cursor:
        after = db.conn.execute(sa.select(t.c.name_key, t.c.id).where(t.c.id == inp.cursor)).first()
        if after is None:
            raise _invalid('cursor', 'use next_cursor from the previous page')
        query = query.where(sa.tuple_(t.c.name_key, t.c.id) > sa.tuple_(sa.literal(after[0]), sa.literal(after[1])))
    rows = [dict(r) for r in db.conn.execute(query.limit(inp.limit + 1)).mappings()]
    more = len(rows) > inp.limit
    rows = rows[:inp.limit]
    return m.PlanPage(items=[plan_output(db, r) for r in rows], next_cursor=rows[-1]['id'] if more else None,
                      layer_enabled=enabled(db))


# ---------------------------------------------------------------------------- enrollments

def _customer(db, selector: str) -> dict:
    return resolve_selector(db, c.customers, get_list_definition('customer'), selector)


def _enrollment(db, selector: str) -> dict:
    text = selector.strip()
    row = None
    if is_ulid(text):
        row = db.conn.execute(sa.select(c.customer_memberships).where(
            c.customer_memberships.c.id == normalize_ulid(text))).mappings().first()
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': ENROLLMENT, 'selector': selector, 'field': 'membership'})
    return dict(row)


def membership_output(db, row: dict) -> m.MembershipOutput:
    plan = db.conn.execute(sa.select(c.membership_plans).where(c.membership_plans.c.id == row['plan_id'])).mappings().one()
    customer = db.conn.execute(sa.select(c.customers.c.id, c.customers.c.full_name).where(
        c.customers.c.id == row['customer_id'])).one()
    return m.MembershipOutput(id=row['id'], version=row['version'], customer=m.Named(id=customer[0], label=customer[1]),
        plan=m.Named(id=plan['id'], label=plan['name']),
        price_level=m.Named(id=plan['price_level_id'], label=_level_label(db, plan['price_level_id'])),
        start_date=row['start_date'], end_date=row['end_date'], status=row['status'], member_number=row['member_number'])


def _overlaps(db, row: dict) -> list[dict]:
    """Other non-suspended enrollments of the customer whose inclusive interval meets this one."""
    t = c.customer_memberships
    query = sa.select(t).where(t.c.customer_id == row['customer_id'], t.c.id != row['id'], t.c.status != 'suspended',
                               sa.or_(t.c.end_date.is_(None), t.c.end_date >= row['start_date']))
    if row['end_date'] is not None:
        query = query.where(t.c.start_date <= row['end_date'])
    return [dict(r) for r in db.conn.execute(query.order_by(t.c.start_date, t.c.id)).mappings()]


def _refuse_overlap(db, row: dict) -> None:
    clashes = _overlaps(db, row)
    if clashes:
        first = clashes[0]
        raise BookflowError('E_MEMBERSHIP_OVERLAP', message=(
            f"The customer is already enrolled from {first['start_date']} through {first['end_date'] or 'no end date'}; "
            f"end that enrollment (`customer membership end {first['id']}`) before a new one starts."),
            details={'membership': first['id'], 'start_date': first['start_date'], 'end_date': first['end_date'],
                     'overlapping': [x['id'] for x in clashes]})


def _number(db, number: str | None, own: str) -> tuple[str | None, str | None]:
    if number is None:
        return None, None
    key = number.casefold()
    t = c.customer_memberships
    clash = db.conn.execute(sa.select(t.c.id).where(t.c.member_number_key == key, t.c.id != own)).first()
    if clash:
        raise BookflowError('E_NAME_TAKEN', details={'record_type': ENROLLMENT, 'name': number, 'record_id': clash[0],
                                                     'field': 'member_number'})
    return number, key


def membership_set(s, ctx, inp) -> Plan:
    db = s.company
    _require_enabled(db)
    customer = _customer(db, inp.customer)
    plan = resolve_plan(db, inp.plan)
    if not plan['active']:
        raise BookflowError('E_INACTIVE_REFERENCE', details={'field': 'plan', 'record_id': plan['id']})
    if inp.end_date is not None and inp.end_date < inp.start_date:
        raise _invalid('end_date', 'must be on or after start_date')
    if inp.membership is None:
        if inp.expected_version is not None:
            raise _invalid('expected_version', 'only with membership, when changing an enrollment')
        if not customer['active']:
            raise BookflowError('E_INACTIVE_REFERENCE', details={'field': 'customer', 'record_id': customer['id']})
        old = None
        row = dict(id=new_id(), **common(s.actor.id, ctx.interface.value, clock.now_iso()), customer_id=customer['id'],
                   plan_id=plan['id'], status='active')
    else:
        old = _enrollment(db, inp.membership)
        _version_check(old, inp.expected_version, ENROLLMENT)
        if old['customer_id'] != customer['id']:
            raise _invalid('customer', 'an enrollment stays with its customer; enroll the other customer separately')
        if old['status'] == 'ended':
            raise _invalid('membership', 'an ended enrollment is history; enroll again for a new interval')
        row = dict(old, plan_id=plan['id'])
    row.update(start_date=inp.start_date, end_date=inp.end_date)
    row['member_number'], row['member_number_key'] = _number(db, inp.member_number, row['id'])
    _refuse_overlap(db, row)
    changed = old is None or any(row[k] != old[k] for k in _ENROLLMENT_FIELDS)
    if old is not None and changed:
        row = _bumped(row, s, ctx)
    return Plan(m.MembershipWriteOutput(membership=membership_output(db, row), changed=changed),
                dict(row=row, before=old if changed else None, changed=changed))


def membership_end(s, ctx, inp) -> Plan:
    db = s.company
    _require_enabled(db)
    old = _enrollment(db, inp.membership)
    _version_check(old, inp.expected_version, ENROLLMENT)
    if inp.end_date < old['start_date']:
        raise _invalid('end_date', f"must be on or after the start date {old['start_date']}")
    row = dict(old, end_date=inp.end_date, status='ended')
    changed = any(row[k] != old[k] for k in _ENROLLMENT_FIELDS)
    if changed:
        row = _bumped(row, s, ctx)
    return Plan(m.MembershipWriteOutput(membership=membership_output(db, row), changed=changed),
                dict(row=row, before=old if changed else None, changed=changed, action='end'))


def apply_membership(plan: Plan, ctx, s) -> Applied:
    row, before = plan.data['row'], plan.data['before']
    if before is None and plan.data.get('changed') is False:
        return Applied(plan.preview, [], 'no change')
    db = s.company
    # Rechecked inside the write transaction: a concurrent enrollment cannot slip between plan and write.
    if row['status'] != 'suspended':
        _refuse_overlap(db, row)
    if before is None:
        db.conn.execute(c.customer_memberships.insert().values(**row))
        return Applied(plan.preview, [Touched(ENROLLMENT, row['id'], 'create', None, 1, row, db='company')],
                       'enrolled customer in membership plan')
    done = db.conn.execute(c.customer_memberships.update().where(
        c.customer_memberships.c.id == row['id'], c.customer_memberships.c.version == before['version']).values(**row)).rowcount
    if done != 1:
        raise BookflowError('E_VERSION_CONFLICT', details={'record_type': ENROLLMENT, 'record_id': row['id']})
    action = plan.data.get('action', 'update')
    return Applied(plan.preview, [Touched(ENROLLMENT, row['id'], 'update', before['version'], row['version'], row, before,
                                          db='company')], 'ended membership enrollment' if action == 'end' else 'changed membership enrollment')


def membership_show(s, inp) -> m.CustomerMembershipOutput:
    db = s.company
    customer = _customer(db, inp.customer)
    as_of = inp.as_of or today(s)
    t = c.customer_memberships
    rows = [dict(r) for r in db.conn.execute(sa.select(t).where(t.c.customer_id == customer['id']).order_by(
        t.c.start_date.desc(), t.c.id.desc())).mappings()]
    fact = effective_fact(db, customer['id'], as_of)
    effective = next((r for r in rows if fact and r['id'] == fact.enrollment_id), None)
    return m.CustomerMembershipOutput(customer=m.Named(id=customer['id'], label=customer['full_name']),
        layer_enabled=enabled(db), as_of=as_of, effective=membership_output(db, effective) if effective else None,
        history=[membership_output(db, r) for r in rows])


# ---------------------------------------------------------------------------- the sales hook

def effective_fact(db, customer_id: str, on_date: str | None) -> MembershipFact | None:
    """The enrollment covering ``on_date`` for this customer itself, with an active plan; None when the
    layer is off. At most one exists: overlapping non-suspended intervals are refused on write."""
    if on_date is None or not enabled(db):
        return None
    t, p = c.customer_memberships, c.membership_plans
    row = db.conn.execute(sa.select(t.c.id, t.c.version, t.c.start_date, t.c.end_date, p.c.id.label('plan_id'),
                                    p.c.name, p.c.version.label('plan_version'), p.c.price_level_id)
        .join(p, p.c.id == t.c.plan_id)
        .where(t.c.customer_id == customer_id, t.c.status != 'suspended', p.c.active.is_(True),
               t.c.start_date <= on_date, sa.or_(t.c.end_date.is_(None), t.c.end_date >= on_date))
        .order_by(t.c.start_date.desc(), t.c.id)).mappings().first()
    if row is None:
        return None
    return MembershipFact(enrollment_id=row['id'], enrollment_version=row['version'],
                          plan=Reference(id=row['plan_id'], label=row['name'], version=row['plan_version']),
                          price_level_id=row['price_level_id'], start_date=row['start_date'], end_date=row['end_date'])
