"""Cycle counts: classes, classification and what is due (R104 increment 5, design/advanced-inventory.md §6).

Counting a whole warehouse at once stops the business; cycle counting counts a few items each round so every
item is counted within its class's interval -- the fast movers often, the slow ones yearly.

- **Classes.** The ``cycle-class`` list (name, interval in days), seeded on first use -- the first write that
  needs one -- as A 30, B 90 and C 365. Items carry ``cycle_class_id``; an unclassified item is never due.
- **Classify** ranks the active inventory parts and assemblies by the cost of goods they issued over a period
  (default the year to today): sales, vendor-side issues and build components, net of corrections and
  reversals, leaving out count differences and inventory adjustments (shrinkage is not sales). Ranked
  highest first, an item is A while the items before it make up less than ``a_share`` percent (80) of the
  period's issued cost, B while less than ``a_share + b_share`` (95), and C after; an item that issued
  nothing is C. The dry run lists the changes; applying sets them, as ordinary item updates.
- **Due.** An item is due at a site when its class interval has passed since its last recorded count there
  (a count line of a recorded count, voided counts left out), or when it has never been counted there; only
  items with stock history at the site are listed. Most overdue first, never-counted first of all, then the
  class with the shorter interval, then name.
- **Rotation.** ``inventory-count create --cycle --limit N`` counts the first N due items; recording the count
  moves their due dates on, so the next round counts the next ones. Nothing runs by itself.
"""
from __future__ import annotations

from datetime import date as calendar_date, timedelta
from decimal import Decimal, InvalidOperation

import sqlalchemy as sa

from bookflow.company import inventory, placements, schema as c
from bookflow.company.bin_models import (
    ClassifyOutput, ClassifyRow, CycleClassOutput, CycleClassPageOutput, CycleClassWriteOutput, DueOutput, DueRow)
from bookflow.company.lists import normalize_display_name
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_quantity_micro_units
from bookflow.core.ids import is_ulid, new_id, normalize_ulid
from bookflow.core.money import Money
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.hub.users import common

DEFAULTS = (('A', 30, 'Counted monthly: the items making most of the cost of goods issued.'),
            ('B', 90, 'Counted quarterly.'),
            ('C', 365, 'Counted yearly: the slow movers.'))
DEFAULT_LIMIT = 25


def _invalid(field, problem, **details):
    return BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': problem}], **details})


def _today(s):
    from bookflow.company.memorized_schedule import today
    return today(getattr(s, 'company_tz', None))


# ---------------------------------------------------------------- the list

def _rows(s, *where):
    t = c.cycle_classes
    return [dict(row) for row in s.company.conn.execute(
        sa.select(t).where(*where).order_by(t.c.interval_days, t.c.name_key, t.c.id)).mappings()]


def seed_rows(s, ctx):
    """The default classes to store when the company has none yet (the first write that needs one stores them)."""
    if _rows(s):
        return []
    at = clock.now_iso()
    out = []
    for name, days, description in DEFAULTS:
        display, key = normalize_display_name(name)
        out.append(dict(id=new_id(), **common(s.actor.id, ctx.interface.value, at), name=display, name_key=key,
                        interval_days=days, description=description, active=True))
    return out


def insert_seed(s, rows):
    if not rows or _rows(s):
        return []
    s.company.conn.execute(c.cycle_classes.insert(), rows)
    return [Touched('cycle_class', row['id'], 'create', None, 1, row, db='company') for row in rows]


def resolve(s, selector, *, field='cycle_class', active=True, seed=()):
    """A class by id or name; ``seed`` (rows about to be stored) is searched when the company has none yet."""
    text = str(selector).strip()
    t = c.cycle_classes
    candidates = _rows(s) or list(seed)
    row = None
    if is_ulid(text):
        row = next((r for r in candidates if r['id'] == normalize_ulid(text)), None)
    if row is None and text:
        _, key = normalize_display_name(text, field=field)
        row = next((r for r in candidates if r['name_key'] == key), None)
    if row is None:
        names = [r['name'] for r in candidates if r['active']] or [name for name, _, _ in DEFAULTS]
        raise BookflowError('E_VALIDATION', message=(
            f'No cycle class is called "{text}". The classes are: {", ".join(names)}.'),
            details={'fields': [{'field': field, 'problem': f'no cycle class "{text}"'}], 'classes': names})
    if active and not row['active']:
        raise BookflowError('E_INACTIVE_REFERENCE', details={'record_type': 'cycle_class', 'record_id': row['id'],
                                                             'field': field})
    return row


def class_id_for(s, ctx, selector, *, field='cycle_class'):
    """``(class id or None, seed rows)`` for ``item update --cycle-class``. The default classes are stored by the
    same write when the company has none yet (``insert_seed`` before the item)."""
    text = (selector or '').strip()
    if not text:
        return None, []
    seed = seed_rows(s, ctx)
    row = resolve(s, text, field=field, seed=seed)
    return row['id'], (seed if any(r['id'] == row['id'] for r in seed) else [])


def _item_counts(s):
    t = c.items
    return dict(s.company.conn.execute(sa.select(t.c.cycle_class_id, sa.func.count()).where(
        t.c.cycle_class_id.is_not(None), t.c.active.is_(True)).group_by(t.c.cycle_class_id)).all())


def output(s, row, *, cls=CycleClassOutput, counts=None, **extra):
    counts = _item_counts(s) if counts is None else counts
    return cls(id=row['id'], version=row['version'], name=row['name'], interval_days=row['interval_days'],
               description=row['description'], active=bool(row['active']), item_count=counts.get(row['id'], 0), **extra)


def _version_check(row, expected):
    if expected is not None and expected != row['version']:
        raise BookflowError('E_VERSION_CONFLICT', details={
            'record_type': 'cycle_class', 'record_id': row['id'], 'current_version': row['version'],
            'expected_version': expected, 'updated_at': row['updated_at'], 'updated_by': row['updated_by']})


def _bumped(row, s, ctx):
    return dict(row, version=row['version'] + 1, updated_at=clock.now_iso(), updated_by=s.actor.id,
                updated_via=ctx.interface.value)


def _name(s, name, own=None, seed=()):
    display, key = normalize_display_name(name, field='name')
    for row in _rows(s) or list(seed):
        if row['name_key'] == key and row['id'] != own:
            raise BookflowError('E_NAME_TAKEN', details={'record_type': 'cycle_class', 'name': display,
                                                         'record_id': row['id']})
    return display, key


def plan_create(s, ctx, inp) -> Plan:
    seed = seed_rows(s, ctx)
    display, key = _name(s, inp.name, seed=seed)
    row = dict(id=new_id(), **common(s.actor.id, ctx.interface.value, clock.now_iso()), name=display, name_key=key,
               interval_days=inp.interval_days, description=(inp.description or '').strip() or None, active=True)
    return Plan(output(s, row, cls=CycleClassWriteOutput, counts={}), dict(seed=seed, rows=[(None, row)]))


def plan_update(s, ctx, inp) -> Plan:
    old = resolve(s, inp.cycle_class, active=False)
    _version_check(old, inp.expected_version)
    new = dict(old)
    if inp.name is not None:
        new['name'], new['name_key'] = _name(s, inp.name, old['id'])
    if inp.interval_days is not None:
        new['interval_days'] = inp.interval_days
    if 'description' in inp.model_fields_set:
        new['description'] = (inp.description or '').strip() or None
    changed = any(new[k] != old[k] for k in ('name', 'interval_days', 'description'))
    if changed:
        new = _bumped(new, s, ctx)
    return Plan(output(s, new, cls=CycleClassWriteOutput, changed=changed),
                dict(seed=[], rows=[(old, new)] if changed else []))


def plan_active(s, ctx, inp, active) -> Plan:
    old = resolve(s, inp.cycle_class, active=False)
    _version_check(old, inp.expected_version)
    if not active and _item_counts(s).get(old['id']):
        raise BookflowError('E_RECORD_IN_USE', message=(
            f'Cycle class {old["name"]} still has items; give them another class first (`item update --cycle-class` '
            'or `cycle-count classify`).'), details={'record_type': 'cycle_class', 'record_id': old['id']})
    changed = bool(old['active']) != active
    new = _bumped(dict(old, active=active), s, ctx) if changed else dict(old)
    return Plan(output(s, new, cls=CycleClassWriteOutput, changed=changed),
                dict(seed=[], rows=[(old, new)] if changed else []))


def apply(plan, ctx, s) -> Applied:
    touched = insert_seed(s, plan.data.get('seed') or [])
    t = c.cycle_classes
    for before, after in plan.data['rows']:
        if before is None:
            s.company.conn.execute(t.insert().values(**after))
            touched.append(Touched('cycle_class', after['id'], 'create', None, 1, after, db='company'))
            continue
        if s.company.conn.execute(t.update().where(t.c.id == before['id'], t.c.version == before['version'])
                                  .values(**after)).rowcount != 1:
            raise BookflowError('E_VERSION_CONFLICT', details={'record_id': before['id']})
        touched.append(Touched('cycle_class', after['id'], 'update', before['version'], after['version'], after,
                               before, db='company'))
    if not touched:
        return Applied(plan.preview, [], 'no change')
    return Applied(plan.preview, touched, f'cycle class {plan.preview.name}')


def show(s, inp):
    return output(s, resolve(s, inp.cycle_class, active=False))


def listing(s, inp):
    rows = _rows(s) if inp.include_inactive else _rows(s, c.cycle_classes.c.active.is_(True))
    if not _rows(s):
        # Nothing stored yet: these are the classes the first write that needs one stores.
        rows = [dict(id='', version=0, name=name, interval_days=days, description=description, active=True)
                for name, days, description in DEFAULTS]
        return CycleClassPageOutput(cycle_classes=[output(s, row, counts={}) for row in rows], seeded=False)
    counts = _item_counts(s)
    return CycleClassPageOutput(cycle_classes=[output(s, row, counts=counts) for row in rows])


# ---------------------------------------------------------------- classify

def _share(text, field):
    try:
        value = Decimal(str(text).strip())
    except (InvalidOperation, ValueError):
        raise _invalid(field, 'must be a percentage such as 80') from None
    if not (0 <= value <= 100):
        raise _invalid(field, 'must be between 0 and 100')
    return value


def issued_cost(s, date_from, date_to):
    """``{item_id: minor units}`` of goods issued over the period: issues (sales, build components) net of their
    reversals and cost corrections, leaving out count differences and inventory adjustments."""
    rows = s.company.conn.execute(sa.text(
        "SELECT target.item_id, sum(m.value_minor_units) FROM inventory_movements m "
        "JOIN inventory_movements target ON target.id = coalesce(m.reverses_movement_id, m.corrects_movement_id, m.id) "
        "WHERE target.kind = 'issue' AND target.count_position != 'count' "
        "AND target.transaction_id NOT IN (SELECT transaction_id FROM inventory_documents) "
        "AND m.effective_date BETWEEN :start AND :end "
        "AND (m.id = target.id OR m.reverses_movement_id = target.id OR m.corrects_movement_id = target.id) "
        "GROUP BY target.item_id"), {'start': date_from, 'end': date_to}).all()
    return {item: -int(value) for item, value in rows if value}


def classify(s, ctx, inp, *, apply_it=False):
    date_to = inp.date_to or _today(s)
    date_from = inp.date_from or (calendar_date.fromisoformat(date_to) - timedelta(days=365) + timedelta(days=1)).isoformat()
    if date_from > date_to:
        raise _invalid('date_from', 'must be on or before date_to')
    a_share, b_share = _share(inp.a_share, 'a_share'), _share(inp.b_share, 'b_share')
    if a_share + b_share > 100:
        raise _invalid('b_share', 'a_share and b_share together cannot pass 100')
    seed = seed_rows(s, ctx) if ctx is not None else []
    if inp.classes:
        chosen = [resolve(s, name, field=f'classes.{i}', seed=seed) for i, name in enumerate(inp.classes)]
    else:
        chosen = [resolve(s, name, field='classes', seed=seed) for name, _, _ in DEFAULTS]
    t = c.items
    stocked = [dict(row) for row in s.company.conn.execute(sa.select(t.c.id, t.c.full_name, t.c.cycle_class_id).where(
        t.c.type.in_(list(inventory.TRACKED_TYPES)), t.c.active.is_(True))).mappings()]
    cost = issued_cost(s, date_from, date_to)
    total = sum(max(cost.get(item['id'], 0), 0) for item in stocked)
    stocked.sort(key=lambda item: (-max(cost.get(item['id'], 0), 0), item['full_name'].casefold(), item['id']))
    names = {row['id']: row['name'] for row in (_rows(s) or seed)}
    currency = inventory._home(s)
    rows, before, changes = [], Decimal(0), []
    for item in stocked:
        value = max(cost.get(item['id'], 0), 0)
        share_before = before
        cumulative = (before + Decimal(value) * 100 / total) if total else Decimal(0)
        if value and share_before < a_share:
            proposed = chosen[0]
        elif value and share_before < a_share + b_share:
            proposed = chosen[1]
        else:
            proposed = chosen[2]
        before = cumulative
        differs = item['cycle_class_id'] != proposed['id']
        if differs:
            changes.append((item, proposed))
        rows.append(ClassifyRow(item_id=item['id'], item_name=item['full_name'], issued_cost=Money(value, currency).to_dict(),
                                cumulative_share=f'{cumulative.quantize(Decimal("0.01"))}',
                                current_class=names.get(item['cycle_class_id']) if item['cycle_class_id'] else None,
                                proposed_class=proposed['name'], changes=differs))
    message = None
    if not total:
        # Nothing was issued: there is nothing to rank, so nothing changes (every item would be C otherwise).
        changes = []
        rows = [row.model_copy(update={'proposed_class': row.current_class, 'changes': False}) for row in rows]
        message = (f'No goods were issued between {date_from} and {date_to}, so there is nothing to rank the items '
                   'by; no class changes. Choose a period with sales or build issues.')
    out = ClassifyOutput(date_from=date_from, date_to=date_to, total_issued_cost=Money(total, currency).to_dict(),
                         rows=rows, changes=len(changes), applied=apply_it and bool(changes), message=message)
    return out, changes, seed


def plan_classify(s, ctx, inp) -> Plan:
    out, changes, seed = classify(s, ctx, inp)
    return Plan(out, {'input': inp})


def apply_classify(plan, ctx, s) -> Applied:
    from bookflow.commands.item_cmds import _touched
    from bookflow.company import items as item_service
    out, changes, seed = classify(s, ctx, plan.data['input'], apply_it=True)
    touched = insert_seed(s, seed)
    for item, proposed in changes:
        update = item_service.plan_item_update(s.company, item['id'], {'cycle_class_id': proposed['id']},
                                               actor_id=s.actor.id, via=ctx.interface.value)
        if update.mutation is None:
            continue
        item_service.persist_item_update(s.company, update)
        touched.append(_touched(update.mutation))
    if not touched:
        return Applied(out, [], 'no change')
    return Applied(out, touched, f'classified {len(changes)} items for cycle counts')


# ---------------------------------------------------------------- due

def last_counted(s, site_id, item_ids=None, as_of=None):
    """``{item_id: date}`` of each item's last recorded count at the site (on or before ``as_of``).

    An item is counted by a count that is site-wide for it (every stocked item, its items, its category or a cycle
    count) when a line of it was counted. A count of bins counts it only when every bin holding it at the count
    (as the count read the shelf) has a counted line of it: counting A1 does not count what sits in A2."""
    import json
    from bookflow.company import inventory_counts
    k, l = c.inventory_counts, c.inventory_count_lines
    query = sa.select(k).where(k.c.site_id == site_id, k.c.status == 'recorded').order_by(k.c.count_date)
    if as_of is not None:
        query = query.where(k.c.count_date <= as_of)
    wanted = set(item_ids) if item_ids is not None else None
    out = {}
    for count in [dict(row) for row in s.company.conn.execute(query).mappings()]:
        lines = [dict(row) for row in s.company.conn.execute(sa.select(l).where(l.c.count_id == count['id'])).mappings()]
        counted = {}
        for line in lines:
            if line['counted_microunits'] is not None and (wanted is None or line['item_id'] in wanted):
                counted.setdefault(line['item_id'], set()).add(line['bin_id'])
        if not counted:
            continue
        if json.loads(count['selection'] or '{}').get('kind') == 'bins':
            held = inventory_counts.expected(s, count, list(counted), lines=lines)
            for item_id in list(counted):
                holding = {placements_bin(key) for (each, key), quantity in held.items() if each == item_id and quantity > 0}
                if not holding <= counted[item_id]:
                    del counted[item_id]
        for item_id in counted:
            out[item_id] = max(out.get(item_id, ''), count['count_date'])
    return out


def placements_bin(place_key):
    return place_key.split('|')[0] or None


def first_stocked(s, site_id, item_ids):
    """``{item_id: date}`` each item first had a placement at the site: a never-counted item is due from then."""
    t = c.inventory_placements
    ids = list(item_ids)
    if not ids:
        return {}
    return dict(s.company.conn.execute(sa.select(t.c.item_id, sa.func.min(t.c.effective_date)).where(
        t.c.site_id == site_id, t.c.item_id.in_(ids)).group_by(t.c.item_id)).all())


def due_rows(s, site_id, as_of, *, ahead=0):
    """``(due rows, not due count)`` at the site on ``as_of``, the most overdue for its class first.

    A counted item falls due its class interval after its last count; a never-counted one is due from its first
    stock date there. Rows rank by days overdue over the class interval, so an A item a week late outranks a C
    item a week late, and an overdue A item is never starved by a crowd of never-counted slow movers."""
    t, p = c.items, c.inventory_placements
    classes = {row['id']: row for row in _rows(s)}
    items = [dict(row) for row in s.company.conn.execute(sa.select(t.c.id, t.c.full_name, t.c.cycle_class_id).where(
        t.c.cycle_class_id.is_not(None), t.c.active.is_(True), t.c.type.in_(list(inventory.TRACKED_TYPES)),
        t.c.id.in_(sa.select(p.c.item_id).where(p.c.site_id == site_id, p.c.effective_date <= as_of)))).mappings()]
    ids = [item['id'] for item in items]
    last = last_counted(s, site_id, ids, as_of)
    first = first_stocked(s, site_id, ids)
    stock = placements.on_hand(s, as_of, item_ids=ids, site_id=site_id)
    day = calendar_date.fromisoformat(as_of)
    ranked, not_due = [], 0
    for item in items:
        klass = classes.get(item['cycle_class_id'])
        if klass is None or not klass['active']:
            continue
        counted = last.get(item['id'])
        if counted:
            due_date = (calendar_date.fromisoformat(counted) + timedelta(days=klass['interval_days'])).isoformat()
        else:
            due_date = first.get(item['id']) or as_of
        overdue = (day - calendar_date.fromisoformat(due_date)).days
        if overdue < -ahead:
            not_due += 1
            continue
        if overdue < 0:
            not_due += 1          # falls due within ``ahead`` days: listed after every due item, soonest first
        ranked.append((overdue / klass['interval_days'] if overdue >= 0 else -1 + overdue / 10 ** 6, DueRow(
            item_id=item['id'], item_name=item['full_name'], cycle_class=klass['name'],
            interval_days=klass['interval_days'], last_counted=counted, due_date=due_date, days_overdue=overdue,
            on_hand=format_quantity_micro_units(stock.get((item['id'], site_id), 0)))))
    ranked.sort(key=lambda pair: (-pair[0], pair[1].interval_days, pair[1].cycle_class.casefold(),
                                  pair[1].item_name.casefold(), pair[1].item_id))
    return [row for _, row in ranked], not_due


def due(s, inp) -> DueOutput:
    site = placements.resolve_site(s, inp.site, active=False) if inp.site else placements.default_site(s)
    as_of = inp.as_of or _today(s)
    rows, not_due = due_rows(s, site['id'], as_of)
    return DueOutput(site_id=site['id'], site_name=site['name'], as_of=as_of, rows=rows[:inp.limit], due=len(rows),
                     not_due=not_due)


ROUND_AHEAD = 6


def next_due_items(s, site_id, date, limit):
    """A cycle count's selection: the first ``limit`` items due at the site on ``date``, most overdue for its class
    first; places left over take items falling due within the next six days, soonest first. Without that, a round
    whose places a cohort of fast movers fills leaves the slower items it crowds out a round late, again and again
    (R104 increment 5 review: a 30-item rotation keeps every class within its interval plus one round)."""
    rows, _ = due_rows(s, site_id, date, ahead=ROUND_AHEAD)
    return [dict(id=row.item_id, full_name=row.item_name) for row in rows[:limit or DEFAULT_LIMIT]]
