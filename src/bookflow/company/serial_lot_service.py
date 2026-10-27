"""`inventory assign`, `lot` and `serial-number` show, list and update, and the three serial/lot reports
(R104 increment 4, design/advanced-inventory.md §4).

An assignment numbers units after the fact and posts nothing: at one place it moves units between the
unassigned bucket and lots or serial numbers -- units on hand on a date, or the units one document line
moved (which water heater a sale took), dated with that line. Its placements are written first and the
database refuses its row unless they net to zero at the place; a reversal of the line's movement later
retires them with it, so a corrected or voided sale takes its numbering back too.
"""
from __future__ import annotations

import sqlalchemy as sa

from bookflow.company import inventory, items as item_service, journals, placements, schema as c, serial_lots
from bookflow.company.serial_lot_models import AssignOutput, NumberOutput, NumberPageOutput, PlaceQuantity
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_quantity_micro_units, parse_quantity_micro_units
from bookflow.core.ids import is_ulid, new_id, normalize_ulid
from bookflow.core.registry import Applied, Plan, Touched

UNIT = serial_lots.UNIT


def _invalid(field, problem, message=None, **details):
    return BookflowError('E_VALIDATION', message=message, details={'fields': [{'field': field, 'problem': problem}], **details})


def _q(units):
    return format_quantity_micro_units(units)


def _today(s):
    from bookflow.company.memorized_schedule import today
    return today(getattr(s, 'company_tz', None))


def _tracked(s, selector, field='item'):
    item = item_service.resolve_item(s.company, selector)
    if item['type'] not in inventory.TRACKED_TYPES or (item.get('tracking') or 'none') == 'none':
        raise _invalid(field, f'{item["full_name"]} is not tracked by lot or serial number; set its tracking with '
                              '`item update --tracking serial` (or lot) first')
    return item


# ---------------------------------------------------------------- the document line an assignment numbers

def _document_movement(s, item, document, line):
    """The live stock movement of ``item`` one document line moved: ``(movement, transaction, label)``."""
    t = c.transactions
    text = document.strip()
    header = None
    if is_ulid(text):
        header = s.company.conn.execute(sa.select(t).where(t.c.id == normalize_ulid(text))).mappings().first()
    if header is None:
        parts = text.split()
        number = parts[-1]
        kind = '_'.join(parts[:-1]).casefold().replace('-', '_') if len(parts) > 1 else None
        query = sa.select(t).where(t.c.number == number)
        if kind:
            query = query.where(t.c.type == kind)
        found = [dict(row) for row in s.company.conn.execute(query).mappings()]
        if len(found) > 1:
            raise _invalid('document', f'"{text}" names {len(found)} documents; give its type too, e.g. "invoice {number}"')
        header = found[0] if found else None
    if header is None:
        raise _invalid('document', f'no document is "{text}"; give its type and number, e.g. "invoice 1001"')
    from bookflow.company import inventory_effects
    own = [row for row in inventory_effects.own_movements(s, header['id'])
           if row['item_id'] == item['id'] and row['kind'] in ('receipt', 'issue', 'vendor_return', 'found')]
    if line is not None:
        ids = dict(s.company.conn.execute(sa.select(c.document_lines.c.id, c.document_lines.c.line_id).where(
            c.document_lines.c.id.in_([row['document_line_id'] for row in own]))).all()) if own else {}
        key = line.upper() if is_ulid(line) else line
        own = [row for row in own if ids.get(row['document_line_id']) == key]
    label = f'{header["type"].replace("_", " ")} {header["number"]}'
    if not own:
        raise _invalid('document', f'{label} moved no {item["full_name"]}' + (' on that line' if line else ''))
    if len(own) > 1:
        raise _invalid('line', f'{label} has {len(own)} lines of {item["full_name"]}; name the line')
    return own[0], header, label


# ---------------------------------------------------------------- inventory assign

def _decide(s, ctx, inp):
    item = _tracked(s, inp.item)
    kind = item['tracking']
    if kind == 'serial':
        if inp.lot is not None or inp.quantity is not None:
            raise _invalid('lot', f'{item["full_name"]} is tracked by serial number; name serial_numbers')
        if not inp.serial_numbers:
            raise _invalid('serial_numbers', 'name the serial numbers to assign')
        if len({serial_lots.number_key(n) for n in inp.serial_numbers}) != len(inp.serial_numbers):
            raise _invalid('serial_numbers', 'name each serial number once')
    else:
        if inp.serial_numbers is not None:
            raise _invalid('serial_numbers', f'{item["full_name"]} is tracked by lot; name lot and quantity')
        if inp.lot is None or inp.quantity is None:
            raise _invalid('lot', 'name the lot and the quantity going into it (or out of it)')
    movement, label, sign = None, None, 1
    if inp.document is not None:
        movement, _, label = _document_movement(s, item, inp.document, inp.line)
        site_id = placements.site_of_movement(s, movement['id'])
        loose = [r for r in placements.movement_placements(s, [movement['id']]).get(movement['id'], [])
                 if not r['lot_id'] and not r['serial_number_id']]
        bin_id = (max(loose, key=lambda r: abs(int(r['quantity_microunits'])))['bin_id'] if loose
                  else (placements.place_of_movement(s, movement['id']) or (None, None))[1])
        if inp.bin is not None:
            from bookflow.company import bins
            if bins.resolve_bin(s, site_id, inp.bin)['id'] != bin_id:
                raise _invalid('bin', f'{label} moved its {item["full_name"]} at another bin')
        if inp.site is not None and placements.resolve_site(s, inp.site, active=False)['id'] != site_id:
            raise _invalid('site', f'{label} moved its {item["full_name"]} at {serial_lots.site_name(s, site_id)}')
        date, position = movement['effective_date'], movement.get('count_position') or 'before'
        if inp.date is not None and inp.date != date:
            raise _invalid('date', f'the units {label} moved are numbered on its own date, {date}')
        sign = 1 if int(movement['quantity_microunits']) > 0 else -1
        if position == 'count':
            position = 'before'
    else:
        site_id = placements.resolve_site(s, inp.site)['id'] if inp.site else placements.default_site_id(s)
        from bookflow.company import bins
        bin_id = bins.line_bin(s, item['id'], site_id, inp.bin)
        date = inp.date or _today(s)
        position = inp.count_position or 'before'
    journals.open_dates(s, [date])
    if kind == 'serial':
        quantity = UNIT * len(inp.serial_numbers)
    else:
        quantity = parse_quantity_micro_units(inp.quantity, field='quantity')
        if quantity <= 0:
            raise _invalid('quantity', 'must be above 0')
    return dict(item=item, kind=kind, movement=movement, label=label, sign=sign, site_id=site_id, date=date,
                position=position, quantity=quantity, bin_id=bin_id)


def _available(s, d, numbering, sequence):
    """Refuse an assignment its place cannot cover: the unassigned units (or the numbered ones, to unassign)
    on hand there, or those the document line moved."""
    item, quantity = d['item'], d['quantity']
    if d['movement'] is not None:
        rows = placements.retirable(s, [d['movement']['id']]).get(d['movement']['id'], [])
        loose = sum(int(r['quantity_microunits']) for r in rows if not r['lot_id'] and not r['serial_number_id'])
        if inp_direction(d) == 'assign' and loose * d['sign'] < quantity:
            raise _invalid('quantity', f'{d["label"]} moved {_q(abs(loose))} unassigned {item["full_name"]}; '
                                       f'{_q(quantity)} cannot be numbered')
        return
    if inp_direction(d) == 'assign':
        held = placements.held(s, [item['id']], d['site_id'], d['date'],
                               ('before',) if d['position'] == 'before' else ('before', 'count', 'after'))
        loose = held.get((item['id'], placements.place_key(d['bin_id'])), 0)
        if loose < quantity:
            raise _invalid('quantity', (
                f'{serial_lots.site_name(s, d["site_id"])} holds {_q(max(loose, 0))} unassigned {item["full_name"]} on '
                f'{d["date"]}; {_q(quantity)} cannot be numbered'))


def inp_direction(d):
    return d['direction']


def _run(s, ctx, inp, *, persist):
    d = _decide(s, ctx, inp)
    d['direction'] = inp.direction
    item = d['item']
    numbering = serial_lots.Numbering(s)
    sequence = inventory.next_sequence(s)
    _available(s, d, numbering, sequence)
    assignment_id = new_id()
    at = clock.now_iso()
    event = new_id()
    provenance = dict(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value, audit_event_id=event)
    # Assign: numbers take the units from unassigned. Unassign: the other way. On a document line that took units
    # out, the same holds with the signs of the line.
    direction = (1 if inp.direction == 'assign' else -1) * d['sign']
    base = dict(item_id=item['id'], site_id=d['site_id'], bin_id=d['bin_id'], effective_date=d['date'],
                count_position=d['position'], sequence=sequence, source='assignment', movement_id=None,
                transfer_revision_id=None, source_line_id=assignment_id, reverses_placement_id=None, **provenance)
    rows = []
    if d['kind'] == 'serial':
        for number in inp.serial_numbers:
            serial_id, new = numbering.number('serial', item['id'], number, field='serial_numbers')
            row = dict(base, id=new_id(), lot_id=None, serial_number_id=serial_id, quantity_microunits=direction * UNIT)
            if new is not None:
                row['_serial_row'] = new
            rows.append(row)
    else:
        lot_id, new = numbering.number('lot', item['id'], inp.lot, expiry=inp.lot_expiry, field='lot')
        row = dict(base, id=new_id(), lot_id=lot_id, serial_number_id=None, quantity_microunits=direction * d['quantity'])
        if new is not None:
            row['_lot_row'] = new
        rows.append(row)
    rows.append(dict(base, id=new_id(), lot_id=None, serial_number_id=None, quantity_microunits=-direction * d['quantity']))
    if inp.direction == 'unassign' and d['movement'] is None:
        for row in rows[:-1]:
            if row.get('_serial_row') or row.get('_lot_row'):
                raise _invalid('serial_numbers' if d['kind'] == 'serial' else 'lot', 'that number is not on file')
    numbering.made.extend(rows)
    serial_lots.check(numbering, rows, field='')
    if inp.direction == 'unassign' and d['movement'] is None:
        _require_held(s, d, rows[:-1])
    names = serial_lots.number_names(s, [r['lot_id'] for r in rows], [r['serial_number_id'] for r in rows])
    names.update({row['id']: row for row in numbering.new.values()})
    held = placements.held(s, [item['id']], d['site_id'], d['date'], ('before', 'count', 'after'))
    unassigned = held.get((item['id'], placements.place_key(d['bin_id'])), 0) + int(rows[-1]['quantity_microunits'])
    site = serial_lots.site_name(s, d['site_id'])
    numbered = [PlaceQuantity(site_id=r['site_id'], site_name=site, lot=(names.get(r['lot_id']) or {}).get('number'),
                              serial_number=(names.get(r['serial_number_id']) or {}).get('number'),
                              quantity=_q(int(r['quantity_microunits']))) for r in rows]
    output = AssignOutput(id=assignment_id if persist else None, item_id=item['id'], item_name=item['full_name'],
                          site_id=d['site_id'], site_name=site, date=d['date'], count_position=d['position'],
                          direction=inp.direction, document=d['label'], numbered=numbered,
                          unassigned_after=_q(unassigned), warnings=numbering.warnings,
                          status='assigned' if persist else 'will_assign')
    if not persist:
        return output, [], ''
    header = dict(id=assignment_id, item_id=item['id'], site_id=d['site_id'], bin_id=d['bin_id'], date=d['date'],
                  count_position=d['position'], direction=inp.direction,
                  movement_id=d['movement']['id'] if d['movement'] else None,
                  memo=(inp.memo or '').strip() or None, reason=(ctx.reason or '').strip() or None, **provenance)
    touched, clean = placements.number_touched(rows)
    touched += [Touched('inventory_placement', r['id'], 'create', None, 1, r, db='company') for r in clean]
    touched.append(Touched('inventory_assignment', assignment_id, 'create', None, 1, header, db='company'))
    summary = f'{inp.direction} numbers of {item["full_name"]} at {site}' + (f' moved by {d["label"]}' if d['label'] else '')
    audit.write_event_to(s.company, ctx, 'inventory assign', summary, touched, actor_id=s.actor.id,
                         actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None), event_id=event)
    placements.insert(s, rows)
    s.company.conn.execute(c.inventory_assignments.insert().values(**header))
    return output, touched, summary


def _require_held(s, d, rows):
    held = placements.held(s, [d['item']['id']], d['site_id'], d['date'],
                           ('before',) if d['position'] == 'before' else ('before', 'count', 'after'))
    for row in rows:
        key = (d['item']['id'], placements.place_key(d['bin_id'], row['lot_id'], row['serial_number_id']))
        if held.get(key, 0) < -int(row['quantity_microunits']):
            raise _invalid('serial_numbers' if row['serial_number_id'] else 'lot', (
                f'{serial_lots.site_name(s, d["site_id"])} does not hold that many under that number on {d["date"]}'))


def plan_assign(s, ctx, inp) -> Plan:
    output, _, _ = _run(s, ctx, inp, persist=False)
    return Plan(output, {'input': inp})


def apply_assign(plan, ctx, s) -> Applied:
    output, touched, summary = _run(s, ctx, plan.data['input'], persist=True)
    return Applied(output, touched, summary, audited=True)


# ---------------------------------------------------------------- lots and serial numbers

def _table(kind):
    return c.lots if kind == 'lot' else c.serial_numbers


def resolve_number(s, kind, selector, item=None):
    t = _table(kind)
    text = str(selector).strip()
    query = sa.select(t)
    if is_ulid(text):
        row = s.company.conn.execute(query.where(t.c.id == normalize_ulid(text))).mappings().first()
        if row is not None:
            return dict(row)
    query = query.where(t.c.number_key == serial_lots.number_key(text))
    if item is not None:
        query = query.where(t.c.item_id == item_service.resolve_item(s.company, item)['id'])
    rows = [dict(row) for row in s.company.conn.execute(query).mappings()]
    word = 'lot' if kind == 'lot' else 'serial number'
    if not rows:
        raise BookflowError('E_RECORD_NOT_FOUND', message=f'No {word} is "{text}".',
                            details={'record_type': kind, 'selector': text})
    if len(rows) > 1:
        raise _invalid('item', f'{len(rows)} items carry {word} "{text}"; name the item',
                       items=[inventory.item_name(s, row['item_id']) for row in rows])
    return rows[0]


def _places(s, kind, number_id, as_of):
    column = 'lot_id' if kind == 'lot' else 'serial_number_id'
    rows = s.company.conn.execute(sa.text(
        f'SELECT site_id, bin_id, sum(quantity_microunits) AS q FROM inventory_placements WHERE {column} = :id '
        'AND effective_date <= :as_of GROUP BY site_id, bin_id'), {'id': number_id, 'as_of': as_of}).mappings().all()
    return [dict(row) for row in rows if int(row['q'])]


def number_output(s, kind, row, as_of=None, status=None):
    as_of = as_of or _today(s)
    places = _places(s, kind, row['id'], as_of)
    column = 'lot_id' if kind == 'lot' else 'serial_number_id'
    last = s.company.conn.execute(sa.text(
        f'SELECT * FROM inventory_placements WHERE {column} = :id AND effective_date <= :as_of '
        "ORDER BY effective_date DESC, CASE count_position WHEN 'before' THEN 0 WHEN 'count' THEN 1 ELSE 2 END DESC, "
        'sequence DESC LIMIT 1'), {'id': row['id'], 'as_of': as_of}).mappings().first()
    expiry = row.get('expiry_date')
    return NumberOutput(
        id=row['id'], kind='lot' if kind == 'lot' else 'serial_number', item_id=row['item_id'],
        item_name=inventory.item_name(s, row['item_id']), number=row['number'], version=row['version'],
        expiry_date=expiry, expired=bool(expiry and expiry < as_of), memo=row.get('memo'), as_of=as_of,
        on_hand=_q(sum(int(p['q']) for p in places)),
        places=[PlaceQuantity(site_id=p['site_id'], site_name=serial_lots.site_name(s, p['site_id']), bin_id=p['bin_id'],
                              quantity=_q(int(p['q']))) for p in places],
        last_document=serial_lots.describe(s, dict(last)) if last else None, status=status)


def show(kind):
    def planner(s, ctx, inp) -> Plan:
        return Plan(number_output(s, kind, resolve_number(s, kind, inp.number, inp.item), inp.as_of))
    return planner


def listing(kind):
    def planner(s, ctx, inp) -> Plan:
        t = _table(kind)
        query = sa.select(t)
        if inp.item is not None:
            query = query.where(t.c.item_id == item_service.resolve_item(s.company, inp.item)['id'])
        if kind == 'lot' and inp.expiring_by is not None:
            query = query.where(t.c.expiry_date.is_not(None), t.c.expiry_date <= inp.expiring_by)
        rows = [dict(row) for row in s.company.conn.execute(query.order_by(t.c.item_id, t.c.number_key)).mappings()]
        site_id = placements.resolve_site(s, inp.site, active=False)['id'] if inp.site else None
        out = []
        for row in rows:
            shown = number_output(s, kind, row, inp.as_of)
            if (inp.in_stock or site_id) and not any(
                    p.site_id == (site_id or p.site_id) and not p.quantity.startswith('-') for p in shown.places):
                continue
            out.append(shown)
            if len(out) >= inp.limit:
                break
        out.sort(key=lambda n: (n.item_name.casefold(), n.expiry_date or '9999', serial_lots.number_key(n.number)))
        return Plan(NumberPageOutput(numbers=out, count=len(out)))
    return planner


def plan_update(kind):
    def planner(s, ctx, inp) -> Plan:
        row = resolve_number(s, kind, inp.number, inp.item)
        if inp.expected_version is not None and inp.expected_version != row['version']:
            raise BookflowError('E_VERSION_CONFLICT', details={'record_id': row['id'], 'current_version': row['version']})
        after = dict(row)
        if kind == 'lot':
            if inp.clear_expiry:
                after['expiry_date'] = None
            elif inp.expiry_date is not None:
                after['expiry_date'] = inp.expiry_date
        if 'memo' in inp.model_fields_set:
            after['memo'] = (inp.memo or '').strip() or None
        changed = after != row
        if changed:
            at = clock.now_iso()
            after.update(version=row['version'] + 1, updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value)
        return Plan(number_output(s, kind, after, status='updated' if changed else 'unchanged'),
                    {'before': row, 'after': after, 'changed': changed})
    return planner


def apply_update(kind):
    def applier(plan, ctx, s) -> Applied:
        before, after = plan.data['before'], plan.data['after']
        word = 'lot' if kind == 'lot' else 'serial number'
        if not plan.data['changed']:
            return Applied(plan.preview, [], f'{word} {before["number"]} unchanged')
        t = _table(kind)
        if s.company.conn.execute(t.update().where(t.c.id == before['id'], t.c.version == before['version'])
                                  .values(**{k: v for k, v in after.items() if k != 'id'})).rowcount != 1:
            raise BookflowError('E_VERSION_CONFLICT', details={'record_id': before['id']})
        touched = [Touched('lot' if kind == 'lot' else 'serial_number', before['id'], 'update', before['version'],
                           after['version'], after, before, db='company')]
        return Applied(number_output(s, kind, after, status='updated'), touched, f'update {word} {before["number"]}')
    return applier


# ---------------------------------------------------------------- reports

def stock_report(s, ctx, inp) -> Plan:
    from bookflow.company.serial_lot_models import StockReportOutput, StockRow
    as_of = inp.as_of or _today(s)
    params = {'as_of': as_of}
    where = ["p.effective_date <= :as_of", "i.tracking != 'none'"]
    if inp.item is not None:
        params['item'] = item_service.resolve_item(s.company, inp.item)['id']
        where.append('p.item_id = :item')
    if inp.site is not None:
        params['site'] = placements.resolve_site(s, inp.site, active=False)['id']
        where.append('p.site_id = :site')
    placements.check_tie(s, as_of)
    rows = s.company.conn.execute(sa.text(
        'SELECT p.item_id, i.full_name, i.tracking, p.site_id, p.bin_id, p.lot_id, p.serial_number_id, '
        'sum(p.quantity_microunits) AS q FROM inventory_placements p JOIN items i ON i.id = p.item_id '
        f'WHERE {" AND ".join(where)} GROUP BY p.item_id, p.site_id, p.bin_id, p.lot_id, p.serial_number_id'),
        params).mappings().all()
    names = serial_lots.number_names(s, [r['lot_id'] for r in rows], [r['serial_number_id'] for r in rows])
    sites = placements.site_names(s)
    out = []
    for row in rows:
        if not int(row['q']):
            continue
        number_id = row['lot_id'] or row['serial_number_id']
        if number_id is None and not inp.include_unassigned:
            continue
        record = names.get(number_id) or {}
        expiry = record.get('expiry_date')
        if inp.expiring_by is not None and not (expiry and expiry <= inp.expiring_by):
            continue
        out.append(StockRow(item_id=row['item_id'], item_name=row['full_name'], tracking=row['tracking'],
                            number=record.get('number'), site_id=row['site_id'], site_name=sites.get(row['site_id'], ''),
                            bin_id=row['bin_id'], quantity=_q(int(row['q'])), expiry_date=expiry,
                            expired=bool(expiry and expiry < as_of)))
    out.sort(key=lambda r: (r.item_name.casefold(), r.number is None, r.expiry_date or '9999',
                            serial_lots.number_key(r.number or ''), r.site_name.casefold()))
    out = out[:inp.limit]
    return Plan(StockReportOutput(as_of=as_of, rows=out, count=len(out)))


def _find_any(s, number, item, kind):
    kinds = [kind] if kind else ['serial', 'lot']
    found = []
    for each in kinds:
        try:
            found.append((each, resolve_number(s, each, number, item)))
        except BookflowError as error:
            if error.code != 'E_RECORD_NOT_FOUND':
                raise
    if not found:
        raise BookflowError('E_RECORD_NOT_FOUND', message=f'No serial or lot number is "{number}".',
                            details={'record_type': 'serial_number', 'selector': number})
    if len(found) > 1:
        raise _invalid('kind', f'"{number}" is both a serial number and a lot; say which with kind')
    return found[0]


def _document_rows(s, rows):
    """Each placement's document in words, its type, transaction and customer or vendor."""
    out = {}
    movements = [r['movement_id'] for r in rows if r.get('movement_id')]
    info = {}
    if movements:
        for m in s.company.conn.execute(sa.text(
                'SELECT m.id, t.id AS transaction_id, t.type, t.number, r.name_type, r.name_id FROM inventory_movements m '
                'JOIN transactions t ON t.id = m.transaction_id JOIN transaction_revisions r ON r.id = m.revision_id '
                'WHERE m.id IN (' + ','.join(f"'{i}'" for i in movements) + ')')).mappings():
            info[m['id']] = dict(m)
    for row in rows:
        m = info.get(row.get('movement_id'))
        name = None
        if m is not None and m['name_id']:
            table = {'customer': c.customers, 'vendor': c.vendors}.get(m['name_type'])
            if table is not None:
                name = s.company.conn.execute(sa.select(table.c.full_name if 'full_name' in table.c else table.c.name).where(
                    table.c.id == m['name_id'])).scalar_one_or_none()
        out[row['id']] = dict(document=serial_lots.describe(s, row), type=m['type'] if m else None,
                              transaction_id=m['transaction_id'] if m else None, name=name,
                              name_type=m['name_type'] if m else None)
    return out


def _history(s, kind, number):
    from bookflow.company.serial_lot_models import HistoryRow
    column = 'lot_id' if kind == 'lot' else 'serial_number_id'
    rows = [dict(r) for r in s.company.conn.execute(sa.text(
        f'SELECT * FROM inventory_placements WHERE {column} = :id'), {'id': number['id']}).mappings()]
    standing = sorted(serial_lots.live(rows), key=lambda r: (serial_lots.order_key(r), r['id']))
    docs = _document_rows(s, standing)
    sites = placements.site_names(s)
    balance, out = 0, []
    for row in standing:
        quantity = int(row['quantity_microunits'])
        balance += quantity
        doc = docs[row['id']]
        out.append(HistoryRow(date=row['effective_date'], count_position=row['count_position'], document=doc['document'],
                              document_type=doc['type'], transaction_id=doc['transaction_id'], name=doc['name'],
                              site_name=sites.get(row['site_id'], ''), quantity_in=_q(max(quantity, 0)),
                              quantity_out=_q(max(-quantity, 0)), balance=_q(balance)))
    return out, balance, standing, docs


def history_report(s, ctx, inp) -> Plan:
    from bookflow.company.serial_lot_models import HistoryOutput
    kind, number = _find_any(s, inp.number, inp.item, inp.kind)
    rows, balance, _, _ = _history(s, kind, number)
    return Plan(HistoryOutput(item_id=number['item_id'], item_name=inventory.item_name(s, number['item_id']), kind=kind,
                              number=number['number'], rows=rows, on_hand=_q(balance)))


def trace_report(s, ctx, inp) -> Plan:
    """A recall's answer: the number's history, who got it (directly or built into an assembly), who sold it to
    us, what it went into and what went into it."""
    from bookflow.company.serial_lot_models import TraceLink, TraceOutput
    kind, number = _find_any(s, inp.number, inp.item, inp.kind)
    rows, balance, standing, docs = _history(s, kind, number)
    customers, vendors = [], []
    for doc in docs.values():
        bucket = customers if doc['name_type'] == 'customer' else vendors if doc['name_type'] == 'vendor' else None
        if bucket is not None and doc['name'] and doc['name'] not in bucket:
            bucket.append(doc['name'])
    went_into, made_from = [], []
    build_ids = {docs[r['id']]['transaction_id'] for r in standing if docs[r['id']]['transaction_id']
                 and s.company.conn.execute(sa.select(c.assembly_builds.c.id).where(
                     c.assembly_builds.c.transaction_id == docs[r['id']]['transaction_id'])).first() is not None}
    for transaction_id in sorted(build_ids):
        own = [dict(r) for r in s.company.conn.execute(sa.text(
            'SELECT p.*, m.kind AS movement_kind FROM inventory_placements p JOIN inventory_movements m ON m.id = p.movement_id '
            'WHERE m.transaction_id = :t'), {'t': transaction_id}).mappings()]
        own = [r for r in serial_lots.live(own)]
        names = serial_lots.number_names(s, [r['lot_id'] for r in own], [r['serial_number_id'] for r in own])
        mine = [r for r in own if r.get('lot_id' if kind == 'lot' else 'serial_number_id') == number['id']]
        incoming = any(int(r['quantity_microunits']) > 0 for r in mine)
        label = serial_lots.describe(s, own[0]) if own else 'a build'
        date = own[0]['effective_date'] if own else ''
        others = [r for r in own if (int(r['quantity_microunits']) < 0) == incoming]
        by_item = {}
        for r in others:
            numbers = by_item.setdefault(r['item_id'], [])
            n = (names.get(r['lot_id'] or r['serial_number_id']) or {}).get('number')
            if n and n not in numbers:
                numbers.append(n)
        links = [TraceLink(document=label, date=date, item_name=inventory.item_name(s, item), numbers=numbers)
                 for item, numbers in by_item.items()]
        (made_from if incoming else went_into).extend(links)
        if not incoming:
            # Who bought the assemblies it went into: their numbers' customers, else every later sale of them.
            for link in links:
                for n in link.numbers:
                    for each in ('serial', 'lot'):
                        try:
                            other = resolve_number(s, each, n, None)
                        except BookflowError:
                            continue
                        _, _, _, more = _history(s, each, other)
                        for doc in more.values():
                            if doc['name_type'] == 'customer' and doc['name'] and doc['name'] not in customers:
                                customers.append(doc['name'])
    return Plan(TraceOutput(item_id=number['item_id'], item_name=inventory.item_name(s, number['item_id']), kind=kind,
                            number=number['number'], rows=rows, on_hand=_q(balance), customers=customers,
                            vendors=vendors, went_into=went_into, made_from=made_from))
