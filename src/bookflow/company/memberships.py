"""Membership plans and dated customer enrollments (R188 increments 1 and 2).

A plan points at an existing price level. An enrollment lets one customer use one plan on sales dated
from ``start_date`` through ``end_date``, both inclusive company-local dates. A job uses its own
enrollment, else its nearest ancestor's, unless its inheritance is switched off (increment 2). The
sales resolver asks :func:`effective_fact` for the sold-to customer and the sale date of an invoice or
sales receipt (a POS sale is one); with the layer off, or no covering enrollment, prices resolve as
before. A customer merged into another lends its enrollments to the survivor, the stable entitlement
identity. Nothing here edits the customer's ordinary price level or any saved sale.

Enrolling many customers at once is one reversible batch (kind ``membership-enroll``): ``batch reverse``
deletes the enrollments it created, newest first, and refuses by name while a sale captured one or one
was changed since.
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


def _enrollable(db, customer: dict, field: str = 'customer') -> None:
    if not customer['active']:
        raise BookflowError('E_INACTIVE_REFERENCE', details={'field': field, 'record_id': customer['id']})
    walk_in = db.conn.execute(sa.select(c.pos_registers.c.name).where(
        c.pos_registers.c.walk_in_customer_id == customer['id'])).scalar()
    if walk_in is not None:
        raise BookflowError('E_VALIDATION', message=(
            f"{customer['full_name']} is register {walk_in}'s walk-in customer; a membership needs a named customer."),
            details={'fields': [{'field': field, 'problem': 'the register walk-in customer cannot hold a membership'}],
                     'record_id': customer['id']})


def membership_output(db, row: dict) -> m.MembershipOutput:
    plan = db.conn.execute(sa.select(c.membership_plans).where(c.membership_plans.c.id == row['plan_id'])).mappings().one()
    customer = db.conn.execute(sa.select(c.customers.c.id, c.customers.c.full_name).where(
        c.customers.c.id == row['customer_id'])).one()
    return m.MembershipOutput(id=row['id'], version=row['version'], customer=m.Named(id=customer[0], label=customer[1]),
        plan=m.Named(id=plan['id'], label=plan['name']),
        price_level=m.Named(id=plan['price_level_id'], label=_level_label(db, plan['price_level_id'])),
        start_date=row['start_date'], end_date=row['end_date'], status=row['status'], member_number=row['member_number'])


def family(db, customer_id: str) -> list[str]:
    """The customer and every customer merged into it (live merges): one entitlement identity."""
    from bookflow.company.party_merges import survivor
    root = survivor(db, 'customer', customer_id) or customer_id
    merged = db.conn.execute(sa.select(c.party_merges.c.merged_id).where(
        c.party_merges.c.party_kind == 'customer', c.party_merges.c.survivor_id == root,
        c.party_merges.c.undone_at.is_(None))).scalars().all()
    return [root, *sorted(merged)]


def _overlaps(db, row: dict, others: list[str] | None = None) -> list[dict]:
    """Other non-suspended enrollments of the customer's entitlement identity (itself and customers merged into
    it, or ``others``) whose inclusive interval meets this one."""
    t = c.customer_memberships
    owners = others if others is not None else family(db, row['customer_id'])
    query = sa.select(t).where(t.c.customer_id.in_(owners), t.c.id != row['id'], t.c.status != 'suspended',
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
        _enrollable(db, customer)
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
    effective = _enrollment(db, fact.enrollment_id) if fact else None
    setting = _setting(db, customer['id'])
    return m.CustomerMembershipOutput(customer=m.Named(id=customer['id'], label=customer['full_name']),
        layer_enabled=enabled(db), as_of=as_of, effective=membership_output(db, effective) if effective else None,
        history=[membership_output(db, r) for r in rows],
        effective_owner=m.Named(id=fact.owner.id, label=fact.owner.label) if fact and fact.owner else None,
        inherits=None if customer.get('parent_id') is None else (bool(setting['inherit']) if setting else True),
        inheritance_version=setting['version'] if setting else None)


# ---------------------------------------------------------------------------- job inheritance (increment 2)

INHERITANCE = 'customer_membership_inheritance'


def _setting(db, customer_id: str) -> dict | None:
    t = c.customer_membership_inheritance
    row = db.conn.execute(sa.select(t).where(t.c.customer_id == customer_id)).mappings().first()
    return dict(row) if row else None


def inheritance_set(s, ctx, inp) -> Plan:
    db = s.company
    _require_enabled(db)
    customer = _customer(db, inp.customer)
    if customer.get('parent_id') is None:
        raise _invalid('customer', 'only a job (a customer with a parent) inherits a membership')
    old = _setting(db, customer['id'])
    if old is None:
        if inp.expected_version is not None:
            raise _invalid('expected_version', 'this job has no inheritance setting yet; omit expected_version')
        row = dict(id=new_id(), **common(s.actor.id, ctx.interface.value, clock.now_iso()), customer_id=customer['id'],
                   inherit=inp.inherit)
        changed = not inp.inherit  # no row already means "inherits"
    else:
        _version_check(old, inp.expected_version, INHERITANCE)
        changed = bool(old['inherit']) != inp.inherit
        row = _bumped(dict(old, inherit=inp.inherit), s, ctx) if changed else old
    out = m.InheritanceOutput(customer=m.Named(id=customer['id'], label=customer['full_name']), inherit=inp.inherit,
                              version=row['version'] if (old is not None or changed) else None, changed=changed)
    return Plan(out, dict(row=row, before=old, changed=changed))


def apply_inheritance(plan: Plan, ctx, s) -> Applied:
    if not plan.data['changed']:
        return Applied(plan.preview, [], 'no change')
    db, row, before = s.company, plan.data['row'], plan.data['before']
    t = c.customer_membership_inheritance
    if before is None:
        db.conn.execute(t.insert().values(**row))
        touched = Touched(INHERITANCE, row['id'], 'create', None, 1, row, db='company')
    else:
        done = db.conn.execute(t.update().where(t.c.id == row['id'], t.c.version == before['version']).values(**row)).rowcount
        if done != 1:
            raise BookflowError('E_VERSION_CONFLICT', details={'record_type': INHERITANCE, 'record_id': row['id']})
        touched = Touched(INHERITANCE, row['id'], 'update', before['version'], row['version'], row, before, db='company')
    said = 'job inherits its ancestor membership' if row['inherit'] else 'job uses only its own membership'
    return Applied(plan.preview, [touched], said)


# ---------------------------------------------------------------------------- the member list (increment 2)

def members_list(s, inp) -> m.MemberPage:
    """Customers an effective enrollment prices on ``as_of``: the enrolled ones and, unless excluded, the jobs that
    inherit from them. Starts from the covering enrollments (an indexed interval read), never from every customer."""
    db = s.company
    as_of = inp.as_of or today(s)
    on = enabled(db)
    plan_id = resolve_plan(db, inp.plan)['id'] if inp.plan else None
    if not on:
        return m.MemberPage(items=[], as_of=as_of, layer_enabled=False)
    from bookflow.company.party_merges import survivor
    t, p = c.customer_memberships, c.membership_plans
    query = sa.select(t.c.customer_id).join(p, p.c.id == t.c.plan_id).where(
        t.c.status != 'suspended', p.c.active.is_(True), t.c.start_date <= as_of,
        sa.or_(t.c.end_date.is_(None), t.c.end_date >= as_of))
    if plan_id:
        query = query.where(t.c.plan_id == plan_id)
    owners = {survivor(db, 'customer', x) or x for x in db.conn.execute(query).scalars()}
    candidates = set(owners)
    if inp.include_inherited and owners:
        marks = ','.join('?' * len(owners))
        candidates |= {r[0] for r in db.raw.execute(
            f"WITH RECURSIVE down(id) AS (SELECT id FROM customers WHERE id IN ({marks}) "
            "UNION SELECT k.id FROM customers k JOIN down ON k.parent_id = down.id) SELECT id FROM down",
            tuple(sorted(owners))).fetchall()}
    rows = []
    for customer_id in candidates:
        fact = effective_fact(db, customer_id, as_of)
        if fact is None or (plan_id and fact.plan.id != plan_id):
            continue
        inherited = fact.owner is not None and fact.owner.id != customer_id
        if inherited and not inp.include_inherited:
            continue
        customer = db.conn.execute(sa.select(c.customers.c.full_name, c.customers.c.full_name_key, c.customers.c.active)
                                   .where(c.customers.c.id == customer_id)).one()
        if not customer[2]:
            continue
        rows.append((customer[1], customer_id, customer[0], fact, inherited))
    rows.sort(key=lambda r: (r[0], r[1]))
    if inp.cursor:
        keys = [r[1] for r in rows]
        if inp.cursor not in keys:
            raise _invalid('cursor', 'use next_cursor from the previous page')
        rows = rows[keys.index(inp.cursor) + 1:]
    more = len(rows) > inp.limit
    rows = rows[:inp.limit]
    items = [m.MemberRow(customer=m.Named(id=cid, label=name), membership=fact.enrollment_id,
                         plan=m.Named(id=fact.plan.id, label=fact.plan.label),
                         price_level=m.Named(id=fact.price_level_id, label=_level_label(db, fact.price_level_id)),
                         start_date=fact.start_date, end_date=fact.end_date,
                         owner=m.Named(id=fact.owner.id, label=fact.owner.label), inherited=inherited)
             for _, cid, name, fact, inherited in rows]
    return m.MemberPage(items=items, as_of=as_of, layer_enabled=True, next_cursor=rows[-1][1] if more else None)


# ---------------------------------------------------------------------------- enrolling many: one batch (increment 2)

BATCH_KIND = 'membership-enroll'


def enroll_many(s, ctx, inp) -> Plan:
    """Plan one enrollment per named customer; all or none. Overlaps are refused against existing enrollments
    and against each other (one customer named twice, or a customer and one merged into it)."""
    db = s.company
    _require_enabled(db)
    plan = resolve_plan(db, inp.plan)
    if not plan['active']:
        raise BookflowError('E_INACTIVE_REFERENCE', details={'field': 'plan', 'record_id': plan['id']})
    if inp.end_date is not None and inp.end_date < inp.start_date:
        raise _invalid('end_date', 'must be on or after start_date')
    rows, seen = [], {}
    stamp = common(s.actor.id, ctx.interface.value, clock.now_iso())
    for index, selector in enumerate(inp.customers):
        field = f'customers.{index}'
        customer = _customer(db, selector)
        _enrollable(db, customer, field)
        identity = family(db, customer['id'])[0]
        if identity in seen:
            raise _invalid(field, f"{customer['full_name']} is already in this list (as {seen[identity]})")
        seen[identity] = customer['full_name']
        row = dict(id=new_id(), **stamp, customer_id=customer['id'], plan_id=plan['id'], status='active',
                   start_date=inp.start_date, end_date=inp.end_date, member_number=None, member_number_key=None)
        _refuse_overlap(db, row)
        rows.append(row)
    out = m.EnrollManyOutput(batch=None, enrolled=[membership_output(db, r) for r in rows], count=len(rows))
    return Plan(out, dict(rows=rows, plan=plan['name']))


def apply_enroll_many(plan: Plan, ctx, s) -> Applied:
    from bookflow.company import batches
    db, rows = s.company, plan.data['rows']
    for row in rows:
        _refuse_overlap(db, row)  # rechecked inside the write transaction
    batch_id = batches._insert_batch(s, ctx, kind=BATCH_KIND, label=f"Enrolled {len(rows)} in {plan.data['plan']}"[:200],
                                     scope=BATCH_KIND, ordering='record', source_type=ENROLLMENT)
    touched = []
    for row in rows:
        db.conn.execute(c.customer_memberships.insert().values(**row))
        batches.record_effect(s, batch_id, record_type=ENROLLMENT, record_id=row['id'], action='created',
                              version_after=1, before={'customer_id': row['customer_id']})
        touched.append(Touched(ENROLLMENT, row['id'], 'create', None, 1, row, db='company'))
    out = plan.preview.model_copy(update={'batch': batch_id})
    return Applied(out, touched, f"enrolled {len(rows)} customers in membership plan {plan.data['plan']} (batch {batch_id})")


def merge_overlap(db, merged_id: str, survivor_id: str) -> None:
    """A merge joins two entitlement identities; overlapping enrollments must be resolved first."""
    t = c.customer_memberships
    others = family(db, survivor_id)
    for row in db.conn.execute(sa.select(t).where(t.c.customer_id == merged_id, t.c.status != 'suspended')).mappings():
        clashes = _overlaps(db, dict(row), others)
        if clashes:
            first = clashes[0]
            raise BookflowError('E_MERGE_REFUSED', message=(
                f"Merge refused: the customer's enrollment from {row['start_date']} through {row['end_date'] or 'no end date'} "
                f"overlaps the other customer's from {first['start_date']} through {first['end_date'] or 'no end date'}; "
                f"end one (`customer membership end {first['id']}`) before merging."),
                details={'membership': row['id'], 'overlapping': [x['id'] for x in clashes], 'problem': 'membership_overlap'})


# ---------------------------------------------------------------------------- the sales hook

def _own_fact(db, customer_id: str, on_date: str):
    t, p = c.customer_memberships, c.membership_plans
    return db.conn.execute(sa.select(t.c.id, t.c.version, t.c.start_date, t.c.end_date, p.c.id.label('plan_id'),
                                     p.c.name, p.c.version.label('plan_version'), p.c.price_level_id)
        .join(p, p.c.id == t.c.plan_id)
        .where(t.c.customer_id.in_(family(db, customer_id)), t.c.status != 'suspended', p.c.active.is_(True),
               t.c.start_date <= on_date, sa.or_(t.c.end_date.is_(None), t.c.end_date >= on_date))
        .order_by(t.c.start_date.desc(), t.c.id)).mappings().first()


def inherits(db, customer_id: str) -> bool:
    t = c.customer_membership_inheritance
    value = db.conn.execute(sa.select(t.c.inherit).where(t.c.customer_id == customer_id)).scalar()
    return True if value is None else bool(value)


def effective_fact(db, customer_id: str, on_date: str | None) -> MembershipFact | None:
    """The enrollment covering ``on_date`` for this customer, with an active plan: its own (or a customer's
    merged into it), else for a job its nearest ancestor's, climbing only while each job inherits. None when
    the layer is off. At most one exists per identity: overlapping non-suspended intervals are refused."""
    if on_date is None or not enabled(db):
        return None
    node, seen = customer_id, set()
    while node is not None and node not in seen:
        seen.add(node)
        row = _own_fact(db, node, on_date)
        if row is not None:
            owner = db.conn.execute(sa.select(c.customers.c.id, c.customers.c.full_name, c.customers.c.version)
                                    .where(c.customers.c.id == node)).one()
            return MembershipFact(enrollment_id=row['id'], enrollment_version=row['version'],
                                  plan=Reference(id=row['plan_id'], label=row['name'], version=row['plan_version']),
                                  price_level_id=row['price_level_id'], start_date=row['start_date'],
                                  end_date=row['end_date'], owner=Reference(id=owner[0], label=owner[1], version=owner[2]))
        parent = db.conn.execute(sa.select(c.customers.c.parent_id).where(c.customers.c.id == node)).scalar()
        if parent is None or not inherits(db, node):
            return None
        node = parent
    return None


def token(fact: MembershipFact | None) -> str:
    """The membership version a sale was priced with; a renewal, plan change or expiry gives another."""
    if fact is None:
        return 'none'
    owner = fact.owner.id if fact.owner else ''
    return ':'.join((fact.enrollment_id, str(fact.enrollment_version), fact.plan.id, str(fact.plan.version),
                     fact.price_level_id, owner))
