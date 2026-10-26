"""Physical counts: a count is a recorded input, and its difference is derived (R104 increment 2, §6).

The anchor prints a Physical Inventory Worksheet, asks the counters to hold deliveries while they
count, and has the bookkeeper type the variances into Adjust Quantity/Value on Hand [M 544-549].
Bookflow keeps what was *counted* instead, and derives the difference at the count's place in its
day, the way an issue's cost is derived:

- **The count's position.** Every movement and placement carries ``count_position``: ``before``
  (the default, and every row written before counts existed), ``count`` for a count's own
  difference, or ``after``. A count reads only what is dated before its date, or on its date and
  ``before`` it. So the plumber who counts the van at four and has the agent bill the day's jobs at
  nine leaves the default and the jobs land before the count: the van stays exactly as counted.
- **Derived differences.** Per counted line, the difference is counted less what the placements
  hold at that place at the count's position. Per item the net posts as one movement -- an ``issue``
  for less, ``found`` at the average for more -- whose placements carry each place's difference;
  where the places cancel only placements move, so misplaced stock is moved, never lost and found.
  The movements post as one journal entry dated the count date, marked ``count``. Uncounted places
  are no part of the input.
- **Later changes.** A movement, transfer or assignment later placed before the count changes what
  the place held, so the difference is derived again: ``settle_in_command`` runs inside every
  company write (dispatch calls it after the command applies, as it settles statement effects),
  finds the recorded counts the command's stock rows reach, reverses each one's posted difference
  for the items concerned and posts the new one, at the count's own date and position. Cost
  corrections follow as for any backdated change. A count dated on or before the closing date is
  refused, and so is anything that would derive one again, since that change is dated before it.
- **Void** retires the input: its posted differences are reversed and nothing is derived for it
  again. **Cancel** ends an open count with nothing recorded.

Entries are append-only rows, each command under its own idempotency key, so two counters -- or a
person scanning on a phone and an agent typing -- count one count at once.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import sqlalchemy as sa

from bookflow.company import inventory, items as item_service, journals, placements, schema as c
from bookflow.company.inventory_count_models import (
    CountDifferenceOutput, CountDocumentOutput, CountEnterOutput, CountLineOutput, CountOutput, CountPageOutput,
    CountPostOutput, SameDayDocument,
)
from bookflow.company.inventory_schema import INPUT_KINDS
from bookflow.company.journal_models import JournalPostInput, JournalUpdateInput, JournalVoidInput
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_quantity_micro_units, parse_quantity_micro_units
from bookflow.core.ids import is_ulid, new_id, normalize_ulid
from bookflow.core.money import Money
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.hub.users import common

SEQUENCE = 'inventory_count'
UNASSIGNED = placements.place_key()


def _invalid(field_name, problem, **details):
    return BookflowError('E_VALIDATION', details={'fields': [{'field': field_name, 'problem': problem}], **details})


def _q(units):
    return format_quantity_micro_units(int(units))


def _today(s):
    from bookflow.company.memorized_schedule import today
    return today(getattr(s, 'company_tz', None))


# ---------------------------------------------------------------- reading


def resolve(s, selector):
    t = c.inventory_counts
    text = str(selector).strip()
    row = None
    if is_ulid(text):
        row = s.company.conn.execute(sa.select(t).where(t.c.id == normalize_ulid(text))).mappings().first()
    if row is None:
        row = s.company.conn.execute(sa.select(t).where(t.c.number == text)).mappings().first()
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'inventory_count', 'selector': text})
    return dict(row)


def count_lines(s, count_id):
    t = c.inventory_count_lines
    return [dict(row) for row in s.company.conn.execute(
        sa.select(t).where(t.c.count_id == count_id).order_by(t.c.position)).mappings()]


def count_entries(s, count_id):
    t = c.inventory_count_entries
    return [dict(row) for row in s.company.conn.execute(
        sa.select(t).where(t.c.count_id == count_id).order_by(t.c.ordinal)).mappings()]


def counted_by_line(entries):
    """``{line_id: quantity}`` from entries read in order: ``set`` replaces, ``add`` adds."""
    out = {}
    for entry in entries:
        quantity = int(entry['quantity_microunits'])
        out[entry['line_id']] = quantity if entry['mode'] == 'set' else out.get(entry['line_id'], 0) + quantity
    return out


def postings(s, count_id):
    """The count's journal entries in the order written, each with its status and version."""
    t, x = c.inventory_count_postings, c.transactions
    return [dict(row) for row in s.company.conn.execute(
        sa.select(t.c.transaction_id, t.c.purpose, x.c.number, x.c.status, x.c.version)
        .join(x, x.c.id == t.c.transaction_id).where(t.c.count_id == count_id)
        .order_by(t.c.created_at, t.c.transaction_id)).mappings()]


def live_posting(s, count):
    """The count's journal entry that stands, or None: at most one is ever not voided."""
    standing = [row for row in postings(s, count['id']) if row['status'] != 'voided']
    return standing[-1] if standing else None


def own_movements(s, count):
    """Every movement the count's journal entries wrote, retired ones and reversals included."""
    ids = [row['transaction_id'] for row in postings(s, count['id'])]
    if not ids:
        return []
    t = c.inventory_movements
    return [dict(row) for row in s.company.conn.execute(
        sa.select(t).where(t.c.transaction_id.in_(ids)).order_by(t.c.sequence)).mappings()]


def _live_movements(rows):
    retired = {row['reverses_movement_id'] for row in rows if row['kind'] == 'reversal'}
    return [row for row in rows if row['kind'] in INPUT_KINDS and row['id'] not in retired]


def _own_line_placements(s, line_ids):
    """The count's placements that move no quantity of a movement (places that cancelled)."""
    if not line_ids:
        return []
    t = c.inventory_placements
    return [dict(row) for row in s.company.conn.execute(
        sa.select(t).where(t.c.source == 'count', t.c.source_line_id.in_(list(line_ids)))
        .order_by(t.c.sequence, t.c.id)).mappings()]


def _key(row):
    return (row['item_id'], placements.place_key(row.get('bin_id'), row.get('lot_id'), row.get('serial_number_id')))


def live_by_place(s, count, lines, movements_rows=None):
    """``{(item, place): quantity}`` the count currently has posted at its places."""
    movements_rows = own_movements(s, count) if movements_rows is None else movements_rows
    out = {}
    ids = [row['id'] for row in movements_rows]
    for rows in placements.movement_placements(s, ids).values():
        for row in rows:
            out[_key(row)] = out.get(_key(row), 0) + int(row['quantity_microunits'])
    for row in _own_line_placements(s, [line['id'] for line in lines]):
        out[_key(row)] = out.get(_key(row), 0) + int(row['quantity_microunits'])
    return {key: value for key, value in out.items() if value}


def expected(s, count, item_ids, movements_rows=None, lines=None):
    """What the books hold at each place at the count's position, leaving the count's own rows out."""
    movements_rows = own_movements(s, count) if movements_rows is None else movements_rows
    lines = count_lines(s, count['id']) if lines is None else lines
    return placements.held(s, item_ids, count['site_id'], count['count_date'], ('before',),
                           exclude_movements=[row['id'] for row in movements_rows],
                           exclude_lines=[line['id'] for line in lines])


# ---------------------------------------------------------------- outputs


def _names(s, item_ids):
    t = c.items
    ids = list(dict.fromkeys(item_ids))
    out = {}
    for start in range(0, len(ids), 500):
        for row in s.company.conn.execute(sa.select(t.c.id, t.c.full_name, t.c.description).where(
                t.c.id.in_(ids[start:start + 500]))).mappings():
            out[row['id']] = dict(row)
    return out


def _place_label(line, names=None):
    """A count line's place in words: unassigned, a lot or a serial number (R104 increment 4)."""
    if line['place_key'] == UNASSIGNED:
        return 'unassigned'
    names = names or {}
    if line.get('serial_number_id'):
        return 'serial ' + (names.get(line['serial_number_id']) or {}).get('number', line['serial_number_id'])
    if line.get('lot_id'):
        return 'lot ' + (names.get(line['lot_id']) or {}).get('number', line['lot_id'])
    return line['place_key']


def line_outputs(s, count, lines, counted, *, show_expected, held=None, pending=()):
    names = _names(s, [line['item_id'] for line in lines])
    if show_expected and held is None:
        held = expected(s, count, [line['item_id'] for line in lines], lines=lines)
    entries = {}
    for entry in list(count_entries(s, count['id'])) + list(pending):
        entries[entry['line_id']] = entries.get(entry['line_id'], 0) + 1
    from bookflow.company import serial_lots
    numbers = serial_lots.number_names(s, [line.get('lot_id') for line in lines], [line.get('serial_number_id') for line in lines])
    out = []
    for line in lines:
        quantity = counted.get(line['id'])
        books = (held or {}).get((line['item_id'], line['place_key']), 0)
        out.append(CountLineOutput(
            line_id=line['id'], position=line['position'], item_id=line['item_id'],
            item_name=names.get(line['item_id'], {}).get('full_name', line['item_id']),
            description=names.get(line['item_id'], {}).get('description'),
            place=_place_label(line, numbers), origin=line['origin'],
            expected=_q(books) if show_expected else None,
            counted=None if quantity is None else _q(quantity),
            difference=_q(quantity - books) if show_expected and quantity is not None else None,
            entries=entries.get(line['id'], 0)))
    return out


def _counted_now(s, count, lines):
    if count['status'] in ('recorded', 'voided'):
        return {line['id']: int(line['counted_microunits']) for line in lines if line['counted_microunits'] is not None}
    return counted_by_line(count_entries(s, count['id']))


def count_output(s, count, *, cls=CountOutput, status=None, lines=None, reveal=False, **extra):
    lines = count_lines(s, count['id']) if lines is None else lines
    counted = _counted_now(s, count, lines)
    show = reveal or not count['blind']
    names = placements.site_names(s)
    documents = [CountDocumentOutput(transaction_id=row['transaction_id'], number=row['number'],
                                     purpose=row['purpose'], date=count['count_date'])
                 for row in postings(s, count['id'])] if count.get('id') and count['status'] != 'open' else []
    rendered = line_outputs(s, count, lines, counted, show_expected=show)
    return cls(id=count.get('id'), number=count['number'], version=count['version'],
               status=status or count['status'], site_id=count['site_id'], site_name=names.get(count['site_id'], ''),
               date=count['count_date'], blind=bool(count['blind']), selection=json.loads(count['selection']),
               memo=count['memo'], adjustment_account_id=count.get('adjustment_account_id'),
               recorded_at=count.get('recorded_at'), recorded_by=count.get('recorded_by'),
               lines=rendered, counted_lines=sum(1 for line in lines if line['id'] in counted),
               uncounted_lines=sum(1 for line in lines if line['id'] not in counted),
               documents=documents, **extra)


def show(s, inp):
    return count_output(s, resolve(s, inp.count))


def sheet(s, inp):
    """The Physical Inventory Worksheet for this count: expected and difference omitted when blind."""
    return count_output(s, resolve(s, inp.count))


def query(s, inp):
    t = c.inventory_counts
    q = sa.select(t)
    if inp.site:
        q = q.where(t.c.site_id == placements.resolve_site(s, inp.site, active=False)['id'])
    if inp.status:
        q = q.where(t.c.status == inp.status)
    if inp.date_from:
        q = q.where(t.c.count_date >= inp.date_from)
    if inp.date_to:
        q = q.where(t.c.count_date <= inp.date_to)
    rows = [dict(row) for row in s.company.conn.execute(
        q.order_by(t.c.count_date.desc(), t.c.created_at.desc()).limit(inp.limit)).mappings()]
    out = [count_output(s, row).model_copy(update={'lines': []}) for row in rows]
    return CountPageOutput(counts=out, count=len(out))


# ---------------------------------------------------------------- create


def _number(s, explicit):
    t = c.inventory_counts

    def taken(number):
        return s.company.conn.execute(sa.select(t.c.id).where(t.c.number == number)).first() is not None
    if explicit is not None:
        if taken(explicit):
            raise BookflowError('E_DUPLICATE_NUMBER', details={'number': explicit, 'type': 'inventory_count'})
        return explicit, None
    seq = s.company.conn.execute(sa.select(c.sequences).where(c.sequences.c.name == SEQUENCE)).mappings().first()
    number, prefix = (seq['next_number'], seq['prefix']) if seq else (1, 'C-')
    while taken(f'{prefix}{number}'):
        number += 1
    return f'{prefix}{number}', dict(name=SEQUENCE, next_number=number + 1, prefix=prefix)


def resolve_item(s, selector=None, code=None, field_name='item'):
    """An item by id or name, else by barcode; a scanned ``code`` by barcode only."""
    t = c.items
    if code is not None:
        rows = [dict(r) for r in s.company.conn.execute(sa.select(t.c.id).where(
            t.c.barcode == code.strip(), t.c.active.is_(True))).mappings()]
        if not rows:
            raise _invalid(field_name, f'no active item carries the code "{code.strip()}"', code=code.strip())
        if len(rows) > 1:
            raise _invalid(field_name, f'the code "{code.strip()}" names more than one item; enter the item by name',
                           items=[inventory.item_name(s, row['id']) for row in rows])
        return item_service.resolve_item(s.company, rows[0]['id'])
    try:
        return item_service.resolve_item(s.company, selector)
    except BookflowError as error:
        if error.code != 'E_RECORD_NOT_FOUND':
            raise
        rows = [dict(r) for r in s.company.conn.execute(sa.select(t.c.id).where(
            t.c.barcode == str(selector).strip(), t.c.active.is_(True))).mappings()]
        if len(rows) != 1:
            raise
        return item_service.resolve_item(s.company, rows[0]['id'])


def _stocked(s, item, field_name):
    if item['type'] not in inventory.TRACKED_TYPES:
        raise _invalid(field_name, f'"{item["full_name"]}" is a {item["type"].replace("_", " ")} item and carries no '
                                   'stock to count')
    return item


def _selected_items(s, inp, site_id, date):
    if inp.bins is not None:
        raise _invalid('bins', 'bins arrive with a later increment; until then a count covers the whole site, '
                               'or the items or category it names')
    if inp.cycle:
        raise _invalid('cycle', 'cycle counts arrive with bins and cycle classes; name items or a category for now')
    if inp.items is not None:
        out = []
        for index, selector in enumerate(inp.items):
            item = _stocked(s, resolve_item(s, selector, field_name=f'items.{index}'), f'items.{index}')
            if item['id'] not in {row['id'] for row in out}:
                out.append(item)
        return out, {'kind': 'items', 'items': [item['id'] for item in out]}, True
    t = c.items
    query = sa.select(t.c.id).where(t.c.type.in_(list(inventory.TRACKED_TYPES)), t.c.active.is_(True))
    if inp.category is not None:
        from bookflow.company import list_service
        from bookflow.company.lists import get_list_definition
        category = list_service.resolve_selector(s.company, c.item_categories, get_list_definition('item-category'),
                                                 inp.category)
        query = query.where(t.c.category_id == category['id'])
        selection = {'kind': 'category', 'category_id': category['id']}
    else:
        p = c.inventory_placements
        query = query.where(t.c.id.in_(sa.select(p.c.item_id).where(p.c.site_id == site_id,
                                                                      p.c.effective_date <= date)))
        selection = {'kind': 'all'}
    ids = list(s.company.conn.execute(query).scalars())
    names = _names(s, ids)
    ids.sort(key=lambda item_id: (names[item_id]['full_name'].casefold(), item_id))
    return [dict(id=item_id, full_name=names[item_id]['full_name']) for item_id in ids], selection, False


def _sheet_lines(s, count, items, *, named):
    held = placements.held(s, [item['id'] for item in items], count['site_id'], count['count_date'], ('before',))
    out = []
    for item in items:
        places = sorted(key for (each, key), quantity in held.items() if each == item['id'] and quantity)
        if not places:
            places = [UNASSIGNED]
        for key in places:
            out.append((item['id'], key))
    return out


def plan_create(s, ctx, inp) -> Plan:
    site = placements.resolve_site(s, inp.site) if inp.site else placements.default_site(s)
    date = inp.date or _today(s)
    journals.open_dates(s, [date])
    items, selection, named = _selected_items(s, inp, site['id'], date)
    number, sequence = _number(s, inp.number)
    at = clock.now_iso()
    count = dict(id=new_id(), **common(s.actor.id, ctx.interface.value, at), number=number, status='open',
                 site_id=site['id'], count_date=date, blind=bool(inp.blind), selection=json.dumps(selection),
                 adjustment_account_id=None, class_id=None, memo=(inp.memo or '').strip() or None,
                 reason=(ctx.reason or '').strip() or None, recorded_at=None, recorded_by=None, recorded_via=None,
                 record_sequence=None, end_reason=None)
    lines = [dict(id=new_id(), count_id=count['id'], position=position, item_id=item_id,
                  **_place_ids(key), place_key=key, origin='sheet',
                  counted_microunits=None, created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)
             for position, (item_id, key) in enumerate(_sheet_lines(s, count, items, named=named), 1)]
    if not lines:
        raise _invalid('site', f'{site["name"]} holds nothing to count on {date}; name the items to count')
    return Plan(count_output(s, count, status='will_create', lines=lines),
                {'count': count, 'lines': lines, 'sequence': sequence})


def apply_create(plan, ctx, s) -> Applied:
    count, lines, sequence = plan.data['count'], plan.data['lines'], plan.data['sequence']
    if sequence is not None:
        # Numbered again inside the writer: another count may have taken the number meanwhile.
        number, sequence = _number(s, None)
        count = dict(count, number=number)
    event = new_id()
    lines = [dict(line, audit_event_id=event) for line in lines]
    touched = [Touched('inventory_count', count['id'], 'create', None, 1, count, db='company')]
    touched += [Touched('inventory_count_line', line['id'], 'create', None, 1, line, db='company') for line in lines]
    audit.write_event_to(s.company, ctx, 'inventory-count create', f'create inventory count {count["number"]}',
                         touched, actor_id=s.actor.id, actor_kind=s.actor.kind,
                         directive_code=getattr(s, 'directive_code', None), event_id=event)
    conn = s.company.conn
    conn.execute(c.inventory_counts.insert().values(**count))
    conn.execute(c.inventory_count_lines.insert(), lines)
    if sequence:
        from sqlalchemy.dialects.sqlite import insert
        stmt = insert(c.sequences).values(**sequence)
        conn.execute(stmt.on_conflict_do_update(index_elements=['name'], set_=sequence))
    return Applied(count_output(s, count), touched, f'create inventory count {count["number"]}', audited=True)


# ---------------------------------------------------------------- enter


def _require_open(count, verb):
    if count['status'] != 'open':
        raise _invalid('count', f'count {count["number"]} is {count["status"]}; only an open count can be {verb}')


def plan_enter(s, ctx, inp) -> Plan:
    count = resolve(s, inp.count)
    _require_open(count, 'counted')
    return Plan(_enter(s, ctx, count, inp, persist=False)[0], {'input': inp})


def _enter(s, ctx, count, inp, *, persist):
    lines = count_lines(s, count['id'])
    by_place = {(line['item_id'], line['place_key']): line for line in lines}
    at = clock.now_iso()
    event = new_id()
    provenance = dict(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value, audit_event_id=event)
    ordinal = int(s.company.conn.execute(sa.select(sa.func.coalesce(sa.func.max(c.inventory_count_entries.c.ordinal), 0))
                                         .where(c.inventory_count_entries.c.count_id == count['id'])).scalar_one())
    new_lines, entries, touched_lines = [], [], []
    from bookflow.company import serial_lots
    numbering = serial_lots.Numbering(s)
    for index, entry in enumerate(inp.entries):
        prefix = f'entries.{index}.'
        if entry.bin is not None:
            raise _invalid(prefix + 'bin', 'bins arrive with a later increment; count the units at the site')
        item = _stocked(s, resolve_item(s, entry.item, entry.code, field_name=prefix + ('code' if entry.code else 'item')),
                        prefix + 'item')
        lot_id, serial_id = _entry_number(s, numbering, count, item, entry, prefix)
        place = placements.place_key(None, lot_id, serial_id)
        key = (item['id'], place)
        line = by_place.get(key)
        if line is None:
            line = dict(id=new_id(), count_id=count['id'], position=len(lines) + len(new_lines) + 1,
                        item_id=item['id'], bin_id=None, lot_id=lot_id, serial_number_id=serial_id, place_key=place,
                        origin='found', counted_microunits=None, **provenance)
            new_lines.append(line)
            by_place[key] = line
        ordinal += 1
        entries.append(dict(id=new_id(), count_id=count['id'], line_id=line['id'], ordinal=ordinal, mode=entry.mode,
                            quantity_microunits=parse_quantity_micro_units(entry.quantity, field=prefix + 'quantity'),
                            scanned_code=entry.code.strip() if entry.code else None, **provenance))
        if line['id'] not in touched_lines:
            touched_lines.append(line['id'])
    all_lines = lines + new_lines
    counted = counted_by_line(count_entries(s, count['id']) + entries)
    for line in all_lines:
        if line.get('serial_number_id') and counted.get(line['id']) not in (None, 0, serial_lots.UNIT):
            label = _place_label(line, {**{row['id']: row for row in numbering.new.values()},
                                        **serial_lots.number_names(s, serial_ids=[line['serial_number_id']])})
            raise _invalid('entries', f'a serial number is one unit; {label} is counted 0 or 1')
    rendered = {row.line_id: row for row in line_outputs(s, count, all_lines, counted, show_expected=not count['blind'],
                                                         pending=entries)}
    output = count_output(s, count, cls=CountEnterOutput, lines=all_lines)
    output = output.model_copy(update={
        'lines': list(rendered.values()),
        'counted_lines': sum(1 for line in all_lines if line['id'] in counted),
        'uncounted_lines': sum(1 for line in all_lines if line['id'] not in counted),
        'entered': [rendered[line_id] for line_id in touched_lines],
        'uncounted': [rendered[line['id']] for line in all_lines if line['id'] not in counted]})
    if not persist:
        return output, []
    made_lots = [dict(row, **_number_provenance(provenance)) for key, row in numbering.new.items() if key[0] == 'lot']
    made_serials = [dict(row, **_number_provenance(provenance)) for key, row in numbering.new.items() if key[0] == 'serial']
    touched = [Touched('lot', row['id'], 'create', None, 1, row, db='company') for row in made_lots]
    touched += [Touched('serial_number', row['id'], 'create', None, 1, row, db='company') for row in made_serials]
    touched += [Touched('inventory_count_line', line['id'], 'create', None, 1, line, db='company') for line in new_lines]
    touched += [Touched('inventory_count_entry', entry['id'], 'create', None, 1, entry, db='company') for entry in entries]
    audit.write_event_to(s.company, ctx, 'inventory-count enter',
                         f'enter {len(entries)} count entries on inventory count {count["number"]}', touched,
                         actor_id=s.actor.id, actor_kind=s.actor.kind,
                         directive_code=getattr(s, 'directive_code', None), event_id=event)
    if made_lots:
        s.company.conn.execute(c.lots.insert(), made_lots)
    if made_serials:
        s.company.conn.execute(c.serial_numbers.insert(), made_serials)
    if new_lines:
        s.company.conn.execute(c.inventory_count_lines.insert(), new_lines)
    s.company.conn.execute(c.inventory_count_entries.insert(), entries)
    return output, touched


def _number_provenance(provenance):
    return dict(created_at=provenance['created_at'], created_by=provenance['created_by'],
                created_via=provenance['created_via'], updated_at=provenance['created_at'],
                updated_by=provenance['created_by'], updated_via=provenance['created_via'])


def _entry_number(s, numbering, count, item, entry, prefix):
    """``(lot_id, serial_number_id)`` an entry counts (R104 increment 4); both None for unassigned units.

    A number not on file is made, as a receipt makes it. A serial number the books hold at another site is
    refused: it was moved without paperwork, so the transfer comes first."""
    from bookflow.company import serial_lots
    if entry.lot is None and entry.serial_number is None:
        return None, None
    kind = numbering.tracking(item['id'])
    if kind == 'none':
        raise _invalid(prefix + ('lot' if entry.lot is not None else 'serial_number'),
                       f'{item["full_name"]} is not tracked by lot or serial number; count its units without one')
    if entry.lot is not None and kind != 'lot':
        raise _invalid(prefix + 'lot', f'{item["full_name"]} is tracked by serial number, not by lot')
    if entry.serial_number is not None and kind != 'serial':
        raise _invalid(prefix + 'serial_number', f'{item["full_name"]} is tracked by lot, not by serial number')
    if entry.lot is not None:
        return numbering.number('lot', item['id'], entry.lot, field=prefix + 'lot')[0], None
    serial_id, new = numbering.number('serial', item['id'], entry.serial_number, field=prefix + 'serial_number')
    if new is None:
        key = (count['count_date'], 0, 10 ** 18)
        held = numbering.serial_place(serial_id, key, 1)
        if held is not None and held[0] != count['site_id']:
            raise _invalid(prefix + 'serial_number', f'{entry.serial_number} is held at {serial_lots.site_name(s, held[0])}',
                           message=(f'The books hold {entry.serial_number} of {item["full_name"]} at '
                                    f'{serial_lots.site_name(s, held[0])} on {count["count_date"]}; post the '
                                    '`inventory-transfer` that moved it, then count it here.'))
    return None, serial_id


def apply_enter(plan, ctx, s) -> Applied:
    inp = plan.data['input']
    count = resolve(s, inp.count)
    _require_open(count, 'counted')
    output, touched = _enter(s, ctx, count, inp, persist=True)
    return Applied(output, touched, f'enter count entries on inventory count {count["number"]}', audited=True)


# ---------------------------------------------------------------- deriving and posting


@dataclass
class _Derived:
    """What one count should have posted for one item, and what it has."""

    item_id: str
    desired: dict            # place key -> difference
    live: dict               # place key -> what is posted now
    live_movements: list     # its live movements for the item
    live_line_placements: list
    expected: int = 0
    counted: int = 0
    line_ids: dict = field(default_factory=dict)   # place key -> count line id


def derive(s, count, lines, counted):
    """Per item, the derived difference at each counted place, beside what the count has posted."""
    movements_rows = own_movements(s, count)
    live_rows = _live_movements(movements_rows)
    chosen = [line for line in lines if line['id'] in counted]
    ids = list(dict.fromkeys([line['item_id'] for line in chosen] + [row['item_id'] for row in live_rows]))
    held = expected(s, count, ids, movements_rows, lines) if ids else {}
    live = live_by_place(s, count, lines, movements_rows)
    line_placements = _own_line_placements(s, [line['id'] for line in lines])
    retired = {row['reverses_placement_id'] for row in line_placements if row['reverses_placement_id']}
    out = {}
    for item_id in ids:
        out[item_id] = _Derived(item_id, {}, {key: value for (each, key), value in live.items() if each == item_id},
                                [row for row in live_rows if row['item_id'] == item_id],
                                [row for row in line_placements if row['item_id'] == item_id
                                 and row['reverses_placement_id'] is None and row['id'] not in retired])
    for line in chosen:
        entry = out[line['item_id']]
        books = held.get((line['item_id'], line['place_key']), 0)
        difference = int(counted[line['id']]) - books
        entry.expected += books
        entry.counted += int(counted[line['id']])
        entry.line_ids[line['place_key']] = line['id']
        if difference:
            entry.desired[line['place_key']] = difference
    return out


def _place_ids(key):
    bin_id, lot_id, serial_id = (part or None for part in key.split('|'))
    return dict(bin_id=bin_id, lot_id=lot_id, serial_number_id=serial_id)


def _units(key, difference):
    """A place's difference as placements: a serial number's one unit at a time (a unit owed at -1 and counted
    present differs by two), anything else whole."""
    if _place_ids(key)['serial_number_id'] is None:
        return [difference]
    from bookflow.company.serial_lots import UNIT
    return [UNIT if difference > 0 else -UNIT] * (abs(difference) // UNIT)


# A journal entry carries at most 200 lines, so a count posts its differences in entries of at most this
# many items, two lines each (R104 increment 2: a 500-line count posts in several).
ITEMS_PER_ENTRY = 99


def build_change(s, ctx, count, derived, *, purpose, account_id, class_id):
    """The count's journal entries, posted, corrected or voided so they carry what ``derived`` says.

    A count's differences post in journal entries of at most ``ITEMS_PER_ENTRY`` items. Deriving them
    again corrects only the entries holding an item whose difference moved, the way a sale's correction
    does -- that entry's movements reversed exactly in its own transaction and its items' differences
    restated at the count's date and position -- and an entry left with no difference is voided. Items
    with no posted difference yet post in new entries. Returns ``(change, differences)``; ``change`` is
    None when what is posted already stands.
    """
    currency = inventory._home(s)
    sequence = inventory.next_sequence(s)
    date = count['count_date']
    if purpose == 'void':
        for entry in derived.values():
            entry.desired = {}
    changed = [item_id for item_id, entry in derived.items() if entry.desired != entry.live]
    if not changed:
        return None, []
    standing = {row['transaction_id']: row for row in postings(s, count['id']) if row['status'] != 'voided'}
    group_of = {item_id: next((row['transaction_id'] for row in entry.live_movements), None)
                for item_id, entry in derived.items()}
    touched = {group_of[item_id] for item_id in changed if group_of[item_id] is not None}
    restated = [item_id for item_id in derived if group_of[item_id] in touched
                or (group_of[item_id] is None and item_id in changed)]
    items = {item_id: item_service.resolve_item(s.company, item_id) for item_id in restated}
    site_name = placements.site_names(s).get(count['site_id'], '')
    reversals, planned, explicit, loose = {}, {}, {}, []
    for item_id in restated:
        entry = derived[item_id]
        for row in entry.live_movements:
            reversals.setdefault(item_id, []).append(inventory.reversal_values(row, sequence))
            sequence += 1
        if entry.desired != entry.live:
            for row in entry.live_line_placements:
                loose.append(dict(row, id=new_id(), quantity_microunits=-int(row['quantity_microunits']),
                                  sequence=sequence, reverses_placement_id=row['id']))
                sequence += 1
        net = sum(entry.desired.values())
        if net:
            asset = inventory._asset_account(s, items[item_id])
            values = inventory.movement_values(
                item_id=item_id, kind='issue' if net < 0 else 'found', quantity=net, value=-1, date=date,
                sequence=sequence, currency=currency, asset_id=asset['id'], offset_id=account_id, class_id=class_id,
                position='count', fallback=inventory.fallback_cost(s, item_id, currency))
            sequence += 1
            planned[item_id] = values
            explicit[item_id] = [dict(id=new_id(), item_id=item_id, site_id=count['site_id'], **_place_ids(key),
                                      quantity_microunits=part, effective_date=date, count_position='count',
                                      sequence=values['sequence'], source='movement', movement_id=values['id'],
                                      transfer_revision_id=None, source_line_id=None, reverses_placement_id=None)
                                 for key, difference in sorted(entry.desired.items()) for part in _units(key, difference)]
        elif entry.desired and entry.desired != entry.live:
            # The places cancel: stock was misplaced, not lost. Placements alone move it.
            for key, difference in sorted(entry.desired.items()):
                for part in _units(key, difference):
                    loose.append(dict(id=new_id(), item_id=item_id, site_id=count['site_id'], **_place_ids(key),
                                      quantity_microunits=part, effective_date=date, count_position='count',
                                      sequence=sequence, source='count', movement_id=None, transfer_revision_id=None,
                                      source_line_id=entry.line_ids[key], reverses_placement_id=None))
                    sequence += 1

    def refuse(refusal, item):
        return inventory.refuse_stock(refusal, item, quantity_field='entries', value_field='entries')
    flat_reversals = [values for rows in reversals.values() for values in rows]
    # Reversals first, so each new difference replays on the shelf without the old one.
    working, states = inventory.value_movements(s, flat_reversals + list(planned.values()), items, refuse) \
        if flat_reversals or planned else ({}, {})
    differences = [(item_id, derived[item_id], planned.get(item_id)) for item_id in restated]
    posted_lines = {row['id']: row['posting_line_id'] for entry in derived.values() for row in entry.live_movements}
    memo = f'Physical count {count["number"]}' + (', derived again' if purpose == 'rederive' else '')

    def valued(item_id):
        return item_id in planned and int(planned[item_id]['value_minor_units']) != 0

    def document_for(group, members, skip=(), extra=()):
        lines, moved, placed = [], [], []
        for item_id in members:
            for values in reversals.get(item_id, ()):
                moved.append(inventory._Movement(values, None, posted_lines.get(values['reverses_movement_id'])))
            placed += placements.for_movements(s, reversals.get(item_id, []))
        for item_id in [m for m in members if m not in skip] + list(extra):
            values = planned.get(item_id)
            if values is None:
                continue
            index = None
            if int(values['value_minor_units']):
                lines.extend(inventory.pair(
                    values['asset_account_id'], values['offset_account_id'], values['value_minor_units'], currency,
                    values['class_id'], f'Count {count["number"]}: {items[item_id]["full_name"]} '
                                        f'{_q(values["quantity_microunits"])} at {site_name}'))
                index = len(lines) - 1
            moved.append(inventory._Movement(values, index, None))
            placed += explicit[item_id]
        live = standing.get(group) if group else None
        document = None
        if live is not None and lines:
            document = inventory._Document(JournalUpdateInput(
                journal=live['transaction_id'], expected_version=live['version'], date=date, memo=memo, lines=lines),
                'update', 'count', moved)
        elif live is not None and moved:
            document = inventory._Document(JournalVoidInput(journal=live['transaction_id'],
                                                            expected_version=live['version']), 'void', 'count', moved)
            document.journal_date = date
            document.reason = (f'Count {count["number"]} voided' if purpose == 'void'
                               else f'Count {count["number"]}: no difference remains')
        elif lines:
            document = inventory._Document(JournalPostInput(date=date, memo=memo, lines=lines), 'post', 'count', moved)

            def after(fresh, rows, provenance):
                return [('inventory_count_postings', 'inventory_count_posting', 'transaction_id', [dict(
                    transaction_id=fresh.data['header']['id'], count_id=count['id'],
                    purpose='record' if purpose == 'record' else 'rederive', **provenance)])]
            document.after_persist = after
        if document is not None:
            document.placements = placed
        return document

    groups = [(group, [item_id for item_id in restated if group_of[item_id] == group]) for group in sorted(touched)]
    fresh = [item_id for item_id in restated if group_of[item_id] is None and item_id in planned]
    groups += [(None, fresh[start:start + ITEMS_PER_ENTRY]) for start in range(0, len(fresh), ITEMS_PER_ENTRY)]
    # A difference worth nothing (no average and no purchase cost) has no journal line. It rides in an entry
    # that has one; an entry of nothing but such differences cannot exist, so they move to another entry of
    # this change, and only when none posts anything are they left unrecorded, with a warning.
    skips, homeless = {}, []
    for index, (group, members) in enumerate(groups):
        if not any(valued(item_id) for item_id in members):
            skips[index] = {item_id for item_id in members if item_id in planned}
            homeless += [item_id for item_id in members if item_id in planned]
    target = next((index for index, (_, members) in enumerate(groups)
                   if any(valued(item_id) for item_id in members)), None)
    documents = [document_for(group, members, skips.get(index, ()), homeless if index == target else ())
                 for index, (group, members) in enumerate(groups)]
    documents = [document for document in documents if document is not None]
    zero_warnings = []
    for item_id in [m for m in restated if m in planned and not valued(m)]:
        name = items[item_id]['full_name']
        if target is None:
            zero_warnings.append(
                f'Count {count["number"]}: the difference for {name} is worth nothing at the average cost (it has no '
                'cost on record) and nothing else in the count posts, so it is not recorded yet; give the item a cost '
                'and the count is derived again with the next stock change it reaches.')
        else:
            zero_warnings.append(f'Count {count["number"]}: the difference for {name} is worth nothing at the average '
                                 'cost; its quantity is recorded with no journal line.')
    warnings = []
    for item_id, state in states.items():
        own = {planned[item_id]['id']} if item_id in planned else set()
        history = [row for row in working[item_id] if row['id'] not in own]
        warnings += inventory._warnings(items[item_id], history, state, own)
        warnings += inventory.found_warnings(items[item_id], state, own)
    # Serial numbers stay one unit, never twice in stock or taken twice, through every derivation (R104 increment 4).
    from bookflow.company import serial_lots
    numbering = serial_lots.Numbering(s)
    serial_lots.check(numbering, [row for document in documents for row in document.placements] + loose, field='entries')
    first_item = items[restated[0]]
    change = inventory._Change(documents, first_item, states.get(first_item['id']), currency,
                               items=items, states=states, warnings=warnings + zero_warnings + numbering.warnings)
    change.placements = [row for document in documents for row in document.placements]
    change.loose_placements = loose
    if documents:
        inventory.complete(s, change)
    return change, differences


def fingerprint(count, differences, account_id, class_id):
    payload = json.dumps(dict(count=count['id'], version=count['version'], account=account_id, klass=class_id,
                              items=sorted((item_id, sorted(entry.desired.items()),
                                            None if values is None else (values['kind'], int(values['quantity_microunits']),
                                                                         int(values['value_minor_units'])))
                                           for item_id, entry, values in differences)), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def _same_day(s, count):
    """Stock documents dated the count's date at its site, entered after the count was created."""
    p = c.inventory_placements
    rows = s.company.conn.execute(sa.select(p.c.movement_id, p.c.transfer_revision_id, p.c.count_position,
                                            p.c.created_at).where(
        p.c.site_id == count['site_id'], p.c.effective_date == count['count_date'],
        p.c.created_at >= count['created_at'], p.c.count_position != 'count')).mappings().all()
    seen, out = set(), []
    for row in rows:
        if row['movement_id']:
            movement = s.company.conn.execute(sa.select(c.inventory_movements.c.transaction_id).where(
                c.inventory_movements.c.id == row['movement_id'])).scalar_one_or_none()
            if movement is None or movement in seen:
                continue
            seen.add(movement)
            header = s.company.conn.execute(sa.select(c.transactions.c.type, c.transactions.c.number).where(
                c.transactions.c.id == movement)).mappings().first()
            out.append(SameDayDocument(kind=header['type'] if header else 'document', id=movement,
                                       number=header['number'] if header else None, position=row['count_position'],
                                       entered_at=row['created_at']))
        elif row['transfer_revision_id']:
            revision = s.company.conn.execute(sa.select(c.inventory_transfer_revisions.c.transfer_id).where(
                c.inventory_transfer_revisions.c.id == row['transfer_revision_id'])).scalar_one()
            if revision in seen:
                continue
            seen.add(revision)
            number = s.company.conn.execute(sa.select(c.inventory_transfers.c.number).where(
                c.inventory_transfers.c.id == revision)).scalar_one()
            out.append(SameDayDocument(kind='inventory_transfer', id=revision, number=number,
                                       position=row['count_position'], entered_at=row['created_at']))
    return sorted(out, key=lambda row: row.entered_at)


def _version(count, expected_version):
    if expected_version != count['version']:
        raise BookflowError('E_VERSION_CONFLICT', details={'record_id': count['id'], 'expected_version': expected_version,
                                                           'current_version': count['version']})


def _difference_outputs(s, differences, currency):
    names = _names(s, [item_id for item_id, _, _ in differences])
    out = []
    for item_id, entry, values in differences:
        out.append(CountDifferenceOutput(
            item_id=item_id, item_name=names.get(item_id, {}).get('full_name', item_id),
            expected=_q(entry.expected), counted=_q(entry.counted), difference=_q(sum(entry.desired.values())),
            value=Money(int(values['value_minor_units']) if values else 0, currency).to_dict(),
            movement_kind=values['kind'] if values else 'none'))
    return out


def _post(s, ctx, inp, *, persist):
    count = resolve(s, inp.count)
    _require_open(count, 'recorded')
    _version(count, inp.expected_version)
    journals.open_dates(s, [count['count_date']])
    account = inventory.offset_account(s, inp.adjustment_account)
    klass = inventory.class_row(s, inp.class_id)
    lines = count_lines(s, count['id'])
    counted = counted_by_line(count_entries(s, count['id']))
    if not counted:
        raise _invalid('count', f'nothing has been counted on count {count["number"]}; enter what was counted, '
                                'or cancel the count')
    _refuse_overlap(s, count, lines, counted)
    derived = derive(s, count, lines, counted)
    change, differences = build_change(s, ctx, count, derived, purpose='record', account_id=account['id'],
                                       class_id=klass['id'] if klass else None)
    currency = inventory._home(s)
    stamp = fingerprint(count, differences, account['id'], klass['id'] if klass else None)
    valued = {item_id: values for item_id, _, values in differences}
    all_differences = [(item_id, entry, valued.get(item_id)) for item_id, entry in derived.items()]
    total = sum(int(values['value_minor_units']) for _, _, values in differences if values)
    warnings = list(dict.fromkeys(change.warnings)) if change else []
    status = 'will_record'
    touched = []
    if persist:
        if inp.preview_fingerprint != stamp:
            raise BookflowError('E_PREVIEW_STALE', message=(
                f'What count {count["number"]} would post is not what the preview showed (or no preview_fingerprint '
                'was given). Run the post as a dry run again, check the differences, and pass its preview_fingerprint.'),
                details={'reason': 'count_preview', 'preview_fingerprint': stamp})
        touched = _record(s, ctx, count, lines, counted, account['id'], klass['id'] if klass else None, change)
        count = resolve(s, count['id'])
        status = None
    output = count_output(s, count, cls=CountPostOutput, status=status, reveal=True, preview_fingerprint=stamp,
                          differences=_difference_outputs(s, all_differences, currency),
                          total_value=Money(total, currency).to_dict(), after_counting_began=_same_day(s, count),
                          warnings=warnings)
    return output, touched


def _refuse_overlap(s, count, lines, counted):
    """Two recorded counts of one place on one date cannot both be the shelf at that position."""
    t, ln = c.inventory_counts, c.inventory_count_lines
    rows = s.company.conn.execute(sa.select(ln.c.item_id, ln.c.place_key, t.c.number).join(t, t.c.id == ln.c.count_id).where(
        t.c.site_id == count['site_id'], t.c.count_date == count['count_date'], t.c.status == 'recorded',
        ln.c.counted_microunits.is_not(None), t.c.id != count['id'])).all()
    taken = {(item, key): number for item, key, number in rows}
    for line in lines:
        if line['id'] in counted and (line['item_id'], line['place_key']) in taken:
            raise _invalid('count', f'count {taken[(line["item_id"], line["place_key"])]} already recorded '
                                    f'{inventory.item_name(s, line["item_id"])} at this site on {count["count_date"]}; '
                                    'void it, or date this count another day')


def _write_postings(s, ctx, change, command_name):
    """Persist a count change: its journal entries, and any placements that move between places alone.

    The order keeps every serial number one unit at every step (R104 increment 4): the count's old loose
    placements are retired first, then its entries (which retire and restate its movements), then its new
    loose placements -- so a unit the count now posts as found never meets the loose placement it replaces.
    """
    if change is None:
        return []
    loose = getattr(change, 'loose_placements', [])
    retiring = [row for row in loose if row.get('reverses_placement_id')]
    placing = [row for row in loose if not row.get('reverses_placement_id')]
    touched, rows = [], []
    if loose:
        at = clock.now_iso()
        event = new_id()
        rows = placements.with_provenance(loose, created_at=at, created_by=s.actor.id, created_via=ctx.interface.value,
                                          audit_event_id=event)
        moved = [Touched('inventory_placement', row['id'], 'create', None, 1, row, db='company') for row in rows]
        audit.write_event_to(s.company, ctx, command_name, 'move counted stock between places', moved,
                             actor_id=s.actor.id, actor_kind=s.actor.kind,
                             directive_code=getattr(s, 'directive_code', None), event_id=event)
        touched += moved
    by_id = {row['id']: row for row in rows}
    placements.insert(s, [by_id[row['id']] for row in retiring])
    if change.documents:
        _, written = inventory.persist_change(s, ctx, change, command_name=command_name, persist=True)
        touched = written + touched
    placements.insert(s, [by_id[row['id']] for row in placing])
    return touched


def _record(s, ctx, count, lines, counted, account_id, class_id, change):
    at = clock.now_iso()
    event = new_id()
    after = dict(count, status='recorded', version=count['version'] + 1, updated_at=at, updated_by=s.actor.id,
                 updated_via=ctx.interface.value, adjustment_account_id=account_id, class_id=class_id,
                 recorded_at=at, recorded_by=s.actor.id, recorded_via=ctx.interface.value,
                 record_sequence=inventory.next_sequence(s))
    touched = [Touched('inventory_count', count['id'], 'update', count['version'], after['version'], after, count,
                       db='company')]
    conn = s.company.conn
    for line in lines:
        if line['id'] in counted:
            fixed = dict(line, counted_microunits=int(counted[line['id']]))
            touched.append(Touched('inventory_count_line', line['id'], 'update', None, None, fixed, line, db='company'))
    audit.write_event_to(s.company, ctx, 'inventory-count post', f'record inventory count {count["number"]}', touched,
                         actor_id=s.actor.id, actor_kind=s.actor.kind,
                         directive_code=getattr(s, 'directive_code', None), event_id=event)
    if conn.execute(c.inventory_counts.update().where(c.inventory_counts.c.id == count['id'],
                                                      c.inventory_counts.c.version == count['version'])
                    .values(**{k: v for k, v in after.items() if k != 'id'})).rowcount != 1:
        raise BookflowError('E_VERSION_CONFLICT', details={'record_id': count['id']})
    for line in lines:
        if line['id'] in counted:
            conn.execute(c.inventory_count_lines.update().where(c.inventory_count_lines.c.id == line['id'])
                         .values(counted_microunits=int(counted[line['id']])))
    return touched + _write_postings(s, ctx, change, 'inventory-count post')


def plan_post(s, ctx, inp) -> Plan:
    output, _ = _post(s, ctx, inp, persist=False)
    return Plan(output, {'input': inp})


def apply_post(plan, ctx, s) -> Applied:
    output, touched = _post(s, ctx, plan.data['input'], persist=True)
    return Applied(output, touched, f'record inventory count {output.number}', audited=True)


# ---------------------------------------------------------------- cancel and void


def _end(s, ctx, inp, operation, *, persist):
    count = resolve(s, inp.count)
    _version(count, inp.expected_version)
    if not ctx.reason or not ctx.reason.strip():
        raise BookflowError('E_REASON_REQUIRED')
    if operation == 'cancel':
        _require_open(count, 'cancelled')
    elif count['status'] != 'recorded':
        raise _invalid('count', f'count {count["number"]} is {count["status"]}; only a recorded count can be voided '
                                '(an open one is cancelled)')
    change = None
    if operation == 'void':
        journals.open_dates(s, [count['count_date']])
        lines = count_lines(s, count['id'])
        derived = derive(s, count, lines, {line['id']: int(line['counted_microunits']) for line in lines
                                           if line['counted_microunits'] is not None})
        change, _ = build_change(s, ctx, count, derived, purpose='void', account_id=count['adjustment_account_id'],
                                 class_id=count['class_id'])
    status = {'cancel': 'cancelled', 'void': 'voided'}[operation]
    if not persist:
        return count_output(s, count, status=f'will_{operation}',
                            warnings=list(dict.fromkeys(change.warnings)) if change else []), []
    at = clock.now_iso()
    event = new_id()
    after = dict(count, status=status, version=count['version'] + 1, updated_at=at, updated_by=s.actor.id,
                 updated_via=ctx.interface.value, end_reason=ctx.reason.strip())
    touched = [Touched('inventory_count', count['id'], 'update', count['version'], after['version'], after, count,
                       db='company')]
    audit.write_event_to(s.company, ctx, 'inventory-count ' + operation, f'{operation} inventory count {count["number"]}',
                         touched, actor_id=s.actor.id, actor_kind=s.actor.kind,
                         directive_code=getattr(s, 'directive_code', None), event_id=event)
    if s.company.conn.execute(c.inventory_counts.update().where(
            c.inventory_counts.c.id == count['id'], c.inventory_counts.c.version == count['version'])
            .values(**{k: v for k, v in after.items() if k != 'id'})).rowcount != 1:
        raise BookflowError('E_VERSION_CONFLICT', details={'record_id': count['id']})
    touched += _write_postings(s, ctx, change, 'inventory-count void')
    return count_output(s, resolve(s, count['id']),
                        warnings=list(dict.fromkeys(change.warnings)) if change else []), touched


def plan_end(operation):
    def planner(s, ctx, inp) -> Plan:
        output, _ = _end(s, ctx, inp, operation, persist=False)
        return Plan(output, {'input': inp, 'operation': operation})
    return planner


def apply_end(plan, ctx, s) -> Applied:
    operation = plan.data['operation']
    output, touched = _end(s, ctx, plan.data['input'], operation, persist=True)
    return Applied(output, touched, f'{operation} inventory count {output.number}', audited=True)


# ---------------------------------------------------------------- deriving again, inside every write


def watermark(s):
    """The stock sequence before a command runs; what it writes is numbered from here."""
    if s.company is None:
        return None
    return inventory.next_sequence(s)


def settle_in_command(s, ctx, mark):
    """Derive again every recorded count the command's stock rows reached; returns result warnings.

    Called by dispatch after a company write applies, inside its transaction. Cheap when the company
    has no recorded count, and when the command wrote no placement.
    """
    if mark is None or s.company is None:  # a command that closed or replaced the company (rename, upgrade)
        return []
    conn = s.company.conn
    k = c.inventory_counts
    if conn.execute(sa.select(k.c.id).where(k.c.status.in_(('recorded', 'open'))).limit(1)).first() is None:
        return []
    p = c.inventory_placements
    rows = conn.execute(sa.select(p.c.item_id, p.c.site_id, sa.func.min(p.c.effective_date), sa.func.max(p.c.effective_date))
                        .where(p.c.sequence >= mark).group_by(p.c.item_id, p.c.site_id)).all()
    if not rows:
        return []
    reach = {}
    for item_id, site_id, low, high in rows:
        reach.setdefault(site_id, {})[item_id] = (low, high)
    warnings = _placed_against(s, mark, reach)
    sites = list(reach)
    lowest = min(low for items in reach.values() for low, _ in items.values())
    candidates = [dict(row) for row in conn.execute(sa.select(k).where(
        k.c.status == 'recorded', k.c.site_id.in_(sites), k.c.count_date >= lowest)
        .order_by(k.c.count_date, k.c.record_sequence)).mappings()]
    for count in candidates:
        touched_items = {item_id for item_id, (low, _) in reach.get(count['site_id'], {}).items()
                         if low <= count['count_date']}
        if not touched_items:
            continue
        lines = count_lines(s, count['id'])
        if not any(line['item_id'] in touched_items and line['counted_microunits'] is not None for line in lines):
            continue
        counted = {line['id']: int(line['counted_microunits']) for line in lines
                   if line['counted_microunits'] is not None}
        derived = derive(s, count, lines, counted)
        change, differences = build_change(s, ctx, count, derived, purpose='rederive',
                                           account_id=count['adjustment_account_id'], class_id=count['class_id'])
        if change is None:
            continue
        before = {item_id: sum(entry.live.values()) for item_id, entry in derived.items()}
        _write_postings(s, ctx, change, 'inventory-count rederive')
        names = _names(s, [item_id for item_id, _, _ in differences])
        site = placements.site_names(s).get(count['site_id'], '')
        for item_id, entry, _ in differences:
            if entry.desired == entry.live:
                continue        # restated unchanged with the entry's new revision
            name = names.get(item_id, {}).get('full_name', item_id)
            warnings.append(
                f'Count {count["number"]} of {site} on {count["count_date"]} was derived again for {name}, so the '
                'shelf stays as counted.' if count['blind'] else
                f'Count {count["number"]} of {site} on {count["count_date"]} was derived again: '
                f'{name} now differs by {_q(sum(entry.desired.values()))} '
                f'(was {_q(before.get(item_id, 0))}), so the shelf stays as counted.')
        warnings.extend(change.warnings)
        # What this re-derivation wrote can reach a later count of the same place.
        new_rows = conn.execute(sa.select(p.c.item_id, sa.func.min(p.c.effective_date)).where(
            p.c.sequence >= mark, p.c.site_id == count['site_id']).group_by(p.c.item_id)).all()
        for item_id, low in new_rows:
            current = reach[count['site_id']].get(item_id)
            reach[count['site_id']][item_id] = (min(low, current[0]) if current else low, count['count_date'])
    return list(dict.fromkeys(warnings))


def _placed_against(s, mark, reach):
    """"Placed before count C-3 ..." for the command's rows dated on a count's date at its site."""
    conn = s.company.conn
    k, p = c.inventory_counts, c.inventory_placements
    out = []
    for site_id, items in reach.items():
        dates = {low for low, _ in items.values()} | {high for _, high in items.values()}
        counts = conn.execute(sa.select(k.c.number, k.c.count_date).where(
            k.c.site_id == site_id, k.c.count_date.in_(sorted(dates)), k.c.status.in_(('open', 'recorded')))).all()
        for number, date in counts:
            positions = set(conn.execute(sa.select(p.c.count_position).where(
                p.c.sequence >= mark, p.c.site_id == site_id, p.c.effective_date == date,
                p.c.count_position != 'count').distinct()).scalars())
            site = placements.site_names(s).get(site_id, '')
            for position in sorted(positions):
                out.append(f'Placed {position} count {number} of {site} on {date}.')
    return out
