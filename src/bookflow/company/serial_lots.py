"""Serial and lot numbers on stock lines (R104 increment 4, design/advanced-inventory.md §4).

A tracked item's units carry numbers only through placements. This module decides, for one stock
line, which placements its quantity splits into -- serial numbers one unit each, a lot, and the
unassigned rest -- and checks a change's serial numbers as a whole before anything is written:

- **In** (receipt, found, customer return, transfer in): a number already in stock is refused. A
  serial number standing at -1 (sold before its receipt was recorded) comes back to 0 where it stands.
- **Out** (sale, vendor return, transfer out, build component): a numbered unit leaves from where the
  books hold it, and the result says so when that is not the line's place. A number that was in stock
  and has left is refused, naming the document that took it. A number never on file is accepted with a
  warning and shows -1 until a receipt or an assignment brings it in (open question 3's default).
- **Returns** give back only numbers their issue took; units the issue took unassigned come back
  unassigned. A return naming no numbers takes the issue's when that is the only answer.
- **Lots**: an incoming line names its lot (and ``lot_expiry`` when the lot is new); an outgoing line
  names its lot or leaves its units unassigned, and is told the earliest-expiring lot in stock there
  (open question 2's default). An expired lot warns, and a lot below zero at a place warns.

Nothing here is the last word: the database refuses a serial number twice in stock, a fractional
serial unit, and a number of another item, even when this module is bypassed (serial_lot_schema).
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_quantity_micro_units
from bookflow.core.ids import new_id

UNIT = 1_000_000
_RANK = {'before': 0, 'count': 1, 'after': 2}
NUMBER_KEYS = ('_lot_row', '_serial_row')


@dataclass(frozen=True)
class Numbers:
    """What one line says about numbers. ``serial_numbers`` None means the line said nothing about them."""

    lot: str | None = None
    lot_expiry: str | None = None
    serial_numbers: tuple | None = None
    bin: str | None = None          # the bin the line names (R104 increment 5); resolved at the line's site

    @property
    def stated(self):
        return self.lot is not None or self.serial_numbers is not None


def from_line(line):
    """The ``Numbers`` an input line (a model or a dict) states, or None when it states none."""
    get = (line.get if isinstance(line, dict) else lambda name, default=None: getattr(line, name, default))
    lot, expiry, serials, bin_ = get('lot'), get('lot_expiry'), get('serial_numbers'), get('bin')
    if lot is None and expiry is None and serials is None and bin_ is None:
        return None
    lot = str(lot).strip() if lot is not None else None
    bin_ = str(bin_).strip() if bin_ is not None else None
    return Numbers(lot=lot or None, lot_expiry=expiry,
                   serial_numbers=None if serials is None else tuple(str(n).strip() for n in serials if str(n).strip()),
                   bin=bin_ or None)


def number_key(text):
    return unicodedata.normalize('NFC', unicodedata.normalize('NFC', str(text).strip()).casefold())


def _invalid(field, problem, message=None, **details):
    return BookflowError('E_VALIDATION', message=message, details={'fields': [{'field': field, 'problem': problem}], **details})


def _q(units):
    return format_quantity_micro_units(units)


def order_key(row):
    return (row['effective_date'], _RANK.get(row.get('count_position') or 'before', 0), int(row['sequence']))


# ---------------------------------------------------------------- lookups

def tracking(s, item_id):
    return s.company.conn.execute(sa.select(c.items.c.tracking).where(c.items.c.id == item_id)).scalar_one_or_none() or 'none'


def find(s, kind, item_id, number):
    t = c.lots if kind == 'lot' else c.serial_numbers
    row = s.company.conn.execute(sa.select(t).where(t.c.item_id == item_id, t.c.number_key == number_key(number))).mappings().first()
    return dict(row) if row else None


def number_names(s, lot_ids=(), serial_ids=()):
    out = {}
    for table, ids in ((c.lots, lot_ids), (c.serial_numbers, serial_ids)):
        ids = [i for i in dict.fromkeys(ids) if i]
        for start in range(0, len(ids), 500):
            for row in s.company.conn.execute(sa.select(table).where(table.c.id.in_(ids[start:start + 500]))).mappings():
                out[row['id']] = dict(row)
    return out


def _placements_of(s, column, value):
    t = c.inventory_placements
    return [dict(row) for row in s.company.conn.execute(sa.select(t).where(getattr(t.c, column) == value)).mappings()]


def live(rows):
    """Placements no reversal among ``rows`` retires (and not themselves reversals)."""
    retired = {row['reverses_placement_id'] for row in rows if row.get('reverses_placement_id')}
    return [row for row in rows if not row.get('reverses_placement_id') and row['id'] not in retired]


def site_name(s, site_id):
    from bookflow.company import placements
    return placements.site_names(s).get(site_id, site_id)


def place_name(s, place):
    """A ``(site, bin)`` place in words: "Warehouse" or "Warehouse bin A:3"."""
    from bookflow.company import bins
    site, bin_id = place
    return site_name(s, site) + (f' bin {bins.bin_names(s, [bin_id]).get(bin_id, bin_id)}' if bin_id else '')


def describe(s, row):
    """The document a stored placement belongs to, in words: "invoice 1001", "inventory transfer 3"."""
    if row.get('movement_id'):
        found = s.company.conn.execute(sa.text(
            'SELECT t.type, t.number FROM inventory_movements m JOIN transactions t ON t.id = m.transaction_id '
            'WHERE m.id = :id'), {'id': row['movement_id']}).first()
        if found is not None:
            return f'{found.type.replace("_", " ")} {found.number}'
        return 'this document'
    if row.get('transfer_revision_id'):
        found = s.company.conn.execute(sa.text(
            'SELECT t.number FROM inventory_transfer_revisions r JOIN inventory_transfers t ON t.id = r.transfer_id '
            'WHERE r.id = :id'), {'id': row['transfer_revision_id']}).first()
        return f'inventory transfer {found.number}' if found else 'this transfer'
    if row.get('source') == 'assignment':
        return f'the inventory assignment of {row["effective_date"]}'
    if row.get('source') == 'count':
        found = s.company.conn.execute(sa.text(
            'SELECT k.number FROM inventory_count_lines l JOIN inventory_counts k ON k.id = l.count_id WHERE l.id = :id'),
            {'id': row.get('source_line_id')}).first()
        return f'count {found.number}' if found else 'a count'
    return 'a stock record'


# ---------------------------------------------------------------- one change's numbering

class Numbering:
    """The numbers one change names: new lots and serial numbers made once, warnings, and what this change
    has already placed, so two lines (or a reversal and its replacement) read each other."""

    def __init__(self, s):
        self.s = s
        self.new = {}
        self.warnings = []
        self.made = []
        self._tracking = {}

    def tracking(self, item_id):
        if item_id not in self._tracking:
            self._tracking[item_id] = tracking(self.s, item_id)
        return self._tracking[item_id]

    def warn(self, text):
        if text not in self.warnings:
            self.warnings.append(text)

    def number(self, kind, item_id, text, *, expiry=None, field='lines'):
        """``(id, row)`` of a lot or serial number of this item; a new one is made once per change."""
        key = (kind, item_id, number_key(text))
        if key in self.new:
            return self.new[key]['id'], self.new[key]
        if len(text) > 128:
            raise _invalid(field, f'a {kind.replace("_", " ")} number is at most 128 characters')
        row = find(self.s, 'lot' if kind == 'lot' else 'serial', item_id, text)
        if row is not None:
            if kind == 'lot' and expiry is not None and row['expiry_date'] != expiry:
                raise _invalid(field + '.lot_expiry', (
                    f'lot {row["number"]} already expires {row["expiry_date"] or "never"}; change it with `lot update`'))
            return row['id'], None
        row = dict(id=new_id(), version=1, item_id=item_id, number=text, number_key=key[2], memo=None)
        if kind == 'lot':
            row['expiry_date'] = expiry
        self.new[key] = row
        return row['id'], row

    def stored(self, serial_id):
        return _placements_of(self.s, 'serial_number_id', serial_id)

    def serial_rows(self, serial_id):
        return self.stored(serial_id) + [row for row in self.made if row.get('serial_number_id') == serial_id]

    def serial_place(self, serial_id, before, sign):
        """The place a serial number stands at ``sign`` (1 held, -1 owed) just before ``before``, or None."""
        held = {}
        for row in self.serial_rows(serial_id):
            if order_key(row) <= before:
                place = (row['site_id'], row.get('bin_id'))
                held[place] = held.get(place, 0) + int(row['quantity_microunits'])
        return next((place for place, quantity in sorted(held.items(), key=lambda kv: (kv[0][0], kv[0][1] or ''))
                     if quantity * sign > 0), None)


# ---------------------------------------------------------------- one line

def _issue_numbers(s, issue_id, made=()):
    """``(serials, lots, unassigned)`` an issue took and no return has given back yet, from its placements
    and the assignments made against it."""
    from bookflow.company import placements
    rows = placements.retirable(s, [issue_id]).get(issue_id, [])
    took_serial, took_lot, unassigned = {}, {}, 0
    for row in rows:
        quantity = -int(row['quantity_microunits'])
        if row.get('serial_number_id'):
            took_serial[row['serial_number_id']] = took_serial.get(row['serial_number_id'], 0) + quantity
        elif row.get('lot_id'):
            took_lot[row['lot_id']] = took_lot.get(row['lot_id'], 0) + quantity
        else:
            unassigned += quantity
    returned = s.company.conn.execute(sa.text(
        'SELECT p.serial_number_id, p.lot_id, p.quantity_microunits, p.id, p.reverses_placement_id FROM inventory_placements p '
        'JOIN inventory_movements m ON m.id = p.movement_id WHERE m.returns_movement_id = :issue'),
        {'issue': issue_id}).mappings().all()
    gone = [dict(row) for row in returned] + [row for row in made if row.get('_returns') == issue_id]
    for row in live(gone):
        quantity = int(row['quantity_microunits'])
        if row.get('serial_number_id') and row['serial_number_id'] in took_serial:
            took_serial[row['serial_number_id']] -= quantity
        elif row.get('lot_id') and row['lot_id'] in took_lot:
            took_lot[row['lot_id']] -= quantity
    return ({k: v for k, v in took_serial.items() if v > 0}, {k: v for k, v in took_lot.items() if v > 0}, unassigned)


def place(state, row, site_id, spec=None, *, item_name=None, returns_movement_id=None, field='lines', prior=None,
          bin_id=None):
    """The placements, without provenance, one movement's quantity splits into at ``site_id`` (and ``bin_id``).

    ``spec`` is what the line states; ``prior`` what the line it restates carried (kept when it states
    nothing). Each part: site, bin, lot, serial number and signed quantity, with any new number row embedded.
    """
    s = state.s
    item_id, quantity = row['item_id'], int(row['quantity_microunits'])
    name = item_name or _item_name(s, item_id)
    kind = state.tracking(item_id)
    base = dict(site_id=site_id, bin_id=bin_id, lot_id=None, serial_number_id=None)
    if spec is None or not spec.stated:
        spec = prior if prior is not None and kind != 'none' else spec
    if kind == 'none':
        if spec is not None and (spec.lot or spec.serial_numbers):
            raise _invalid(field, f'{name} is not tracked by lot or serial number; leave lot and serial_numbers out, '
                                  'or set its tracking with `item update`')
        return [dict(base, quantity_microunits=quantity)]
    incoming = quantity > 0
    position = order_key(row)
    if kind == 'serial':
        if quantity % UNIT:
            raise _invalid(field, f'{name} is tracked by serial number, so it moves in whole units')
        if spec is not None and spec.lot:
            raise _invalid(field, f'{name} is tracked by serial number, not by lot')
        serials = list(spec.serial_numbers or ()) if spec is not None and spec.serial_numbers is not None else None
        if serials is None and returns_movement_id:
            took, _, unassigned = _issue_numbers(s, returns_movement_id, state.made)
            if not unassigned and sum(took.values()) == quantity:
                serials = [number_names(s, serial_ids=took)[serial]['number'] for serial in took]
        serials = serials or []
        if len({number_key(n) for n in serials}) != len(serials):
            raise _invalid(field + '.serial_numbers', 'name each serial number once')
        if len(serials) * UNIT > abs(quantity):
            if spec is not None and spec.stated and spec is not prior:
                raise _invalid(field + '.serial_numbers', (
                    f'{len(serials)} serial numbers name more units than the line moves ({_q(abs(quantity))})'))
            serials = serials[:abs(quantity) // UNIT]
        allowed = None
        if returns_movement_id and serials:
            took, _, _ = _issue_numbers(s, returns_movement_id, state.made)
            allowed = set(took)
        parts = []
        for number in serials:
            serial_id, new = state.number('serial', item_id, number, field=field + '.serial_numbers')
            if allowed is not None and serial_id not in allowed:
                raise _invalid(field + '.serial_numbers', f'{number} was not taken by the sale this return gives back',
                               message=(f'A return gives back only serial numbers its sale took; {number} of {name} '
                                        'was not among them.'))
            at, at_bin = site_id, bin_id
            if incoming:
                owed = state.serial_place(serial_id, position, -1) if new is None else None
                if owed is not None and owed != (site_id, bin_id):
                    at, at_bin = owed
                    if owed[0] != site_id:
                        state.warn(f'{number} of {name} was sold before its receipt; it comes back to 0 at '
                                   f'{site_name(s, at)}, where it was sold.')
            else:
                held = state.serial_place(serial_id, position, 1) if new is None else None
                if held is not None and held != (site_id, bin_id):
                    at, at_bin = held
                    if held[0] != site_id:
                        state.warn(f'{number} of {name} left from {site_name(s, at)}, where the books hold it, '
                                   f'not {site_name(s, site_id)}.')
                    else:
                        state.warn(f'{number} of {name} left from {place_name(s, held)}, where the books hold it, '
                                   f'not {place_name(s, (site_id, bin_id))}.')
                elif held is None and (new is not None or not state.serial_rows(serial_id)):
                    state.warn(f'{number} of {name} is not on file; it shows -1 until a receipt or an '
                               '`inventory assign` brings it in.')
            part = dict(base, site_id=at, bin_id=at_bin, serial_number_id=serial_id,
                        quantity_microunits=UNIT if incoming else -UNIT)
            if new is not None:
                part['_serial_row'] = new
            parts.append(part)
        rest = quantity - sum(part['quantity_microunits'] for part in parts)
        if rest:
            parts.append(dict(base, quantity_microunits=rest))
        return parts
    # lot
    if spec is not None and spec.serial_numbers:
        raise _invalid(field + '.serial_numbers', f'{name} is tracked by lot, not by serial number')
    lot = spec.lot if spec is not None else None
    if lot is None and returns_movement_id:
        _, lots, unassigned = _issue_numbers(s, returns_movement_id, state.made)
        if len(lots) == 1 and not unassigned:
            lot = number_names(s, lot_ids=lots)[next(iter(lots))]['number']
    if lot is None:
        if not incoming:
            suggested = earliest_lot(s, item_id, site_id, row['effective_date'])
            if suggested is not None:
                state.warn(f'{name} left unassigned; the earliest-expiring lot in stock at {site_name(s, site_id)} is '
                           f'{suggested["number"]}' + (f' (expires {suggested["expiry_date"]})' if suggested['expiry_date'] else '')
                           + f'; name it as lot to take it.')
        return [dict(base, quantity_microunits=quantity)]
    expiry = spec.lot_expiry if spec is not None else None
    lot_id, new = state.number('lot', item_id, lot, expiry=expiry, field=field)
    record = new or number_names(s, lot_ids=[lot_id])[lot_id]
    if record.get('expiry_date') and record['expiry_date'] < row['effective_date']:
        state.warn(f'Lot {lot} of {name} expired on {record["expiry_date"]}.')
    part = dict(base, lot_id=lot_id, quantity_microunits=quantity)
    if new is not None:
        part['_lot_row'] = new
    return [part]


def earliest_lot(s, item_id, site_id, date):
    """The unexpired lot of this item in stock at the site on ``date`` that expires first."""
    rows = s.company.conn.execute(sa.text(
        'SELECT l.id, l.number, l.expiry_date, sum(p.quantity_microunits) AS q FROM inventory_placements p '
        'JOIN lots l ON l.id = p.lot_id WHERE p.item_id = :item AND p.site_id = :site AND p.effective_date <= :date '
        'GROUP BY l.id HAVING q > 0'), {'item': item_id, 'site': site_id, 'date': date}).mappings().all()
    rows = [dict(row) for row in rows if not row['expiry_date'] or row['expiry_date'] >= date]
    rows.sort(key=lambda row: (row['expiry_date'] is None, row['expiry_date'] or '', number_key(row['number'])))
    return rows[0] if rows else None


def _item_name(s, item_id):
    return s.company.conn.execute(sa.select(c.items.c.full_name).where(c.items.c.id == item_id)).scalar_one_or_none() or item_id


# ---------------------------------------------------------------- a change, as a whole

def check(state, rows, *, field='lines'):
    """Refuse a change whose serial numbers end up twice in stock or taken twice; warn of lots below zero.

    ``rows`` are the change's new placements. Reversed pairs are left out, so a correction that takes a
    number back and gives it again reads as the one placement that stands.
    """
    s = state.s
    serials = list(dict.fromkeys(row['serial_number_id'] for row in rows if row.get('serial_number_id')))
    names = number_names(s, serial_ids=[sid for sid in serials if not any(
        n['id'] == sid for n in state.new.values())]) if serials else {}
    names.update({row['id']: row for row in state.new.values()})
    fresh = {row['id'] for row in rows}
    for serial_id in serials:
        stored = {row['id']: row for row in state.stored(serial_id)}
        every = list(stored.values()) + [row for row in rows if row.get('serial_number_id') == serial_id
                                         and row['id'] not in stored]
        standing = sorted(live(every), key=lambda row: (order_key(row), int(row['quantity_microunits'])))
        groups, running, history = {}, 0, []
        for row in standing:
            groups.setdefault(order_key(row), []).append(row)
        number = names.get(serial_id, {}).get('number', serial_id)
        item = _item_name(s, standing[0]['item_id']) if standing else ''
        for key in sorted(groups):
            before = running
            running += sum(int(row['quantity_microunits']) for row in groups[key])
            mine = any(row['id'] in fresh for row in groups[key])
            if running > UNIT:
                holder = next((row for row in reversed(history) if int(row['quantity_microunits']) > 0), None)
                where = f' at {site_name(s, holder["site_id"])}, brought in by {describe(s, holder)}' if holder else ''
                raise _invalid(field + '.serial_numbers', f'{number} is already in stock',
                               message=f'{number} of {item} is already in stock{where}; a serial number is one unit.',
                               serial_number=number)
            if running < -UNIT or (running < 0 and history and before >= 0 and (mine or any(
                    r['id'] in fresh for r in history))):
                taker = next((row for row in reversed(history) if int(row['quantity_microunits']) < 0
                              and row['id'] not in fresh), None) or next(
                    (row for row in groups[key] if row['id'] not in fresh), None)
                took = f' with {describe(s, taker)} on {taker["effective_date"]}' if taker else ''
                raise _invalid(field + '.serial_numbers', f'{number} has already left stock',
                               message=f'{number} of {item} has already left stock{took}; it cannot be taken again.',
                               serial_number=number)
            history.extend(groups[key])
        # Late paperwork can leave one serial number owed at one site and held at another (a sale backdated
        # before the transfer that moved it, say). The ties hold; the places need a transfer or a count.
        places = {}
        for row in standing:
            places[row['site_id']] = places.get(row['site_id'], 0) + int(row['quantity_microunits'])
        split = {site: quantity for site, quantity in places.items() if quantity}
        if len(split) > 1:
            where = ' and '.join(f'{_q(quantity)} at {site_name(s, site)}'
                                 for site, quantity in sorted(split.items(), key=lambda kv: kv[1]))
            state.warn(f'{number} of {item} now shows {where}: its paperwork places it at two sites. An '
                       '`inventory-transfer` (or a count) of where the unit really is puts it right.')
    lots = {(row['item_id'], row['site_id'], row['lot_id']) for row in rows
            if row.get('lot_id') and int(row['quantity_microunits']) < 0}
    for item_id, site_id, lot_id in sorted(lots):
        held = int(s.company.conn.execute(sa.text(
            'SELECT coalesce(sum(quantity_microunits), 0) FROM inventory_placements WHERE item_id = :i AND site_id = :s '
            'AND lot_id = :l'), {'i': item_id, 's': site_id, 'l': lot_id}).scalar_one())
        held += sum(int(row['quantity_microunits']) for row in rows
                    if (row['item_id'], row['site_id'], row.get('lot_id')) == (item_id, site_id, lot_id))
        if held < 0:
            number = (names.get(lot_id) or number_names(s, lot_ids=[lot_id]).get(lot_id) or {}).get('number', lot_id)
            state.warn(f'Lot {number} of {_item_name(s, item_id)} at {site_name(s, site_id)} now shows {_q(held)}.')
    return state.warnings


def split(rows):
    """``(lot rows, serial rows, placements)``: the new numbers embedded in placement rows, made once each
    with the provenance of the first placement naming them, and the placements without them."""
    lots, serials, clean = {}, {}, []
    for row in rows:
        for key, bucket in (('_lot_row', lots), ('_serial_row', serials)):
            made = row.get(key)
            if made is not None and made['id'] not in bucket:
                bucket[made['id']] = dict(made, created_at=row['created_at'], created_by=row['created_by'],
                                          created_via=row['created_via'], updated_at=row['created_at'],
                                          updated_by=row['created_by'], updated_via=row['created_via'])
        clean.append({k: v for k, v in row.items() if k not in NUMBER_KEYS and k != '_returns'})
    return list(lots.values()), list(serials.values()), clean


def line_numbers(s, movement_ids):
    """``{movement_id: Numbers}`` each stored movement's units carry (with assignments made against it), so a
    correction that restates nothing about numbers keeps them."""
    from bookflow.company import placements
    out = {}
    stored = placements.retirable(s, movement_ids)
    ids = [row for rows in stored.values() for row in rows]
    names = number_names(s, [r['lot_id'] for r in ids], [r['serial_number_id'] for r in ids])
    for movement_id, rows in stored.items():
        serials, lots = {}, {}
        for row in rows:
            if row.get('serial_number_id'):
                serials[row['serial_number_id']] = serials.get(row['serial_number_id'], 0) + int(row['quantity_microunits'])
            elif row.get('lot_id'):
                lots[row['lot_id']] = lots.get(row['lot_id'], 0) + int(row['quantity_microunits'])
        serial_list = tuple(names[k]['number'] for k, v in serials.items() if v)
        lot = max((k for k, v in lots.items() if v), key=lambda k: abs(lots[k]), default=None)
        if serial_list or lot:
            out[movement_id] = Numbers(lot=names[lot]['number'] if lot else None,
                                       serial_numbers=serial_list if serial_list else None)
    return out


def semantic(numbers):
    """What a line's "has anything changed" comparison carries about numbers: nothing when it names none."""
    if numbers is None or not numbers.stated:
        return None
    out = {}
    if numbers.lot:
        out['lot'] = number_key(numbers.lot)
    if numbers.serial_numbers:
        out['serial_numbers'] = sorted(number_key(n) for n in numbers.serial_numbers)
    return out or None


def revision_numbers(s, revision_id):
    """``{line identity: semantic}`` the stock lines of one stored revision carry (R104 increment 4)."""
    if not revision_id:
        return {}
    rows = s.company.conn.execute(sa.text(
        "SELECT m.id, d.line_id FROM inventory_movements m JOIN document_lines d ON d.id = m.document_line_id "
        "WHERE m.revision_id = :revision AND m.kind IN ('receipt', 'issue', 'vendor_return')"),
        {'revision': revision_id}).all()
    carried = line_numbers(s, [row[0] for row in rows])
    return {line_id: semantic(carried[movement_id]) for movement_id, line_id in rows if movement_id in carried}


def numbers_changed(s, revision, lines):
    """Whether resolved ``lines`` (dicts with ``line_id`` and ``numbers``) restate numbers a stored revision's
    same lines do not carry: a change of serial numbers or lot alone is a change, and so is a change of bin
    (R104 increment 5)."""
    if revision is None:
        return False
    saved = places = None
    for line in lines:
        numbers = line.get('numbers')
        if numbers is None or not line.get('line_id'):
            continue
        if numbers.stated:
            saved = revision_numbers(s, revision['id']) if saved is None else saved
            if semantic(numbers) != saved.get(line['line_id']):
                return True
        if numbers.bin:
            from bookflow.company import bins, placements
            places = placements.revision_line_places(s, revision['id']) if places is None else places
            stored = bins.bin_row(s, (places.get(line['line_id']) or (None, None))[1] or '')
            if stored is None or not bins.names_bin(stored, numbers.bin):
                return True
    return False


def fingerprint_part(lines):
    """The fingerprint's numbers entry, only when some line states numbers or a bin (so nothing else moves)."""
    found = [semantic(line.get('numbers')) for line in lines]
    out = {'numbers': found} if any(found) else {}
    named = [number_key(line['numbers'].bin) if line.get('numbers') is not None and line['numbers'].bin else None
             for line in lines]
    if any(named):
        out['bins'] = named
    return out


def transfer_parts(state, item, quantity, numbers, from_site, to_site, date, position, sequence, field,
                   from_bin=None, to_bin=None):
    """``(lot_id, [(site_id, bin_id, lot_id, serial_id, signed quantity, new number row)])`` one transfer line
    moves: each number out of the from place and into the to place, the rest unassigned. A serial number leaves
    the bin of the from site the books hold it in."""
    s, name = state.s, item['full_name']
    kind = state.tracking(item['id'])
    out_, in_ = (from_site, from_bin), (to_site, to_bin)
    if kind == 'none':
        if numbers is not None and (numbers.lot or numbers.serial_numbers):
            raise _invalid(field, f'{name} is not tracked by lot or serial number')
        return None, [(*out_, None, None, -quantity, None), (*in_, None, None, quantity, None)]
    key = (date, _RANK.get(position, 0), sequence)
    if kind == 'serial':
        if quantity % UNIT:
            raise _invalid(field + '.quantity', f'{name} is tracked by serial number, so it moves in whole units')
        if numbers is not None and numbers.lot:
            raise _invalid(field + '.lot', f'{name} is tracked by serial number, not by lot')
        serials = list(numbers.serial_numbers or ()) if numbers is not None and numbers.serial_numbers else []
        if len({number_key(n) for n in serials}) != len(serials):
            raise _invalid(field + '.serial_numbers', 'name each serial number once')
        if len(serials) * UNIT > quantity:
            raise _invalid(field + '.serial_numbers', f'{len(serials)} serial numbers name more units than the line moves')
        parts = []
        for number in serials:
            serial_id, new = state.number('serial', item['id'], number, field=field + '.serial_numbers')
            held = state.serial_place(serial_id, key, 1) if new is None else None
            if held is None or held[0] != from_site:
                where = f'; the books hold it at {site_name(s, held[0])}' if held else ''
                raise _invalid(field + '.serial_numbers', f'{number} is not at {site_name(s, from_site)} on {date}',
                               message=f'{number} of {name} is not at {site_name(s, from_site)} on {date}{where}.',
                               serial_number=number)
            if held[1] != from_bin:
                state.warn(f'{number} of {name} left from {place_name(s, held)}, where the books hold it, '
                           f'not {place_name(s, out_)}.')
            parts += [(*held, None, serial_id, -UNIT, None), (*in_, None, serial_id, UNIT, None)]
        rest = quantity - UNIT * len(serials)
        if rest:
            parts += [(*out_, None, None, -rest, None), (*in_, None, None, rest, None)]
        return None, parts
    if numbers is not None and numbers.serial_numbers:
        raise _invalid(field + '.serial_numbers', f'{name} is tracked by lot, not by serial number')
    if numbers is None or not numbers.lot:
        suggested = earliest_lot(s, item['id'], from_site, date)
        if suggested is not None:
            state.warn(f'{name} moved unassigned; the earliest-expiring lot at {site_name(s, from_site)} is '
                       f'{suggested["number"]}; name it as lot to move it.')
        return None, [(*out_, None, None, -quantity, None), (*in_, None, None, quantity, None)]
    lot_id, new = state.number('lot', item['id'], numbers.lot, expiry=numbers.lot_expiry, field=field)
    record = new or number_names(s, lot_ids=[lot_id])[lot_id]
    if record.get('expiry_date') and record['expiry_date'] < date:
        state.warn(f'Lot {numbers.lot} of {name} expired on {record["expiry_date"]}.')
    return lot_id, [(*out_, lot_id, None, -quantity, new), (*in_, lot_id, None, quantity, None)]


def line_serials(rows):
    """``{source line: [serial ids]}`` a transfer revision's live placements carried into their to place."""
    out = {}
    for row in rows:
        if row.get('serial_number_id') and not row.get('reverses_placement_id') and int(row['quantity_microunits']) > 0:
            out.setdefault(row['source_line_id'], []).append(row['serial_number_id'])
    return out


def revision_display(s, revision_id):
    """``{line identity: {lot, lot_expiry, serial_numbers}}`` a stored revision's stock lines carry, for show
    and for printed lines; lines carrying no number are left out."""
    if not revision_id:
        return {}
    rows = s.company.conn.execute(sa.text(
        "SELECT m.id, d.line_id FROM inventory_movements m JOIN document_lines d ON d.id = m.document_line_id "
        "WHERE m.revision_id = :revision AND m.kind IN ('receipt', 'issue', 'vendor_return')"),
        {'revision': revision_id}).all()
    if not rows:
        return {}
    carried = line_numbers(s, [row[0] for row in rows])
    out = {}
    for movement_id, line_id in rows:
        numbers = carried.get(movement_id)
        if numbers is None:
            continue
        expiry = find(s, 'lot', _movement_item(s, movement_id), numbers.lot)['expiry_date'] if numbers.lot else None
        out[line_id] = {'lot': numbers.lot, 'lot_expiry': expiry,
                        'serial_numbers': list(numbers.serial_numbers) if numbers.serial_numbers else None}
    return out


def _movement_item(s, movement_id):
    return s.company.conn.execute(sa.select(c.inventory_movements.c.item_id).where(
        c.inventory_movements.c.id == movement_id)).scalar_one()


def printed(line):
    """The words a printed line adds for its numbers: "SN WH-1, WH-2" or "Lot L-100, expires 2017-09-30"."""
    parts = []
    if line.get('serial_numbers'):
        parts.append('SN ' + ', '.join(line['serial_numbers']))
    if line.get('lot'):
        parts.append(f'Lot {line["lot"]}' + (f', expires {line["lot_expiry"]}' if line.get('lot_expiry') else ''))
    return '; '.join(parts)
