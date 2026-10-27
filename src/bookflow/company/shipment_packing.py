"""Packing: draft shipments, boxes and weights (R181 increment 4; design/pick-pack-ship.md §5).

Packing is an optional layer under the company switch ``pick_and_pack_enabled`` (decision K3): while it is off,
`shipment create`, `pack` and `cancel`, and `shipment post`'s ``packages``, refuse with E_FEATURE_DISABLED, and
`shipment post` works exactly as before. Freight to bill, tracking numbers, invoice on ship and the packing slip are
shipping facts, there whatever the switch says.

**Drafts.** `shipment create` writes a draft: the lines, places and numbers that will ship, from an order (what
`shipment post` would propose), explicit lines or a pick list. A draft moves nothing and posts nothing; its units are
held from other shipments of the order while it is live. `shipment post --shipment <draft>` ships it, and the
shipment keeps the draft's id, number, line identities and boxes. `shipment cancel` ends it.

**Boxes.** A packing is a list of boxes, each with its contents: units of a shipment line (by the line's stable
identity) and the serial numbers among them. Every revision of a draft or a shipment carries its packing; a revision
that changes only packing writes the boxes and leaves the stock and the entry as they were. A box's computed weight
is its contents' quantity times each item's unit weight plus the tare; a measured weight, when entered, is what
prints and totals. Weights are in the company's one weight unit, a label never converted.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.company.shipment_models import (
    PackageContentOutput, PackageOutput, PackingOutput, UnboxedOutput,
)
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_quantity_micro_units, parse_quantity_micro_units, round_ratio_half_even
from bookflow.core.ids import is_ulid

UNIT = 1_000_000
FEATURE, SETTING = 'pick_and_pack', 'pick_and_pack_enabled'


def _invalid(field_name, problem, message=None, **details):
    return BookflowError('E_VALIDATION', message=message,
                         details={'fields': [{'field': field_name, 'problem': problem}], **details})


def _q(micro):
    return format_quantity_micro_units(int(micro))


# ---------------------------------------------------------------- the switch


def enabled(s):
    return bool(s.company.conn.execute(sa.select(getattr(c.company_info.c, SETTING))).scalar())


def disabled_error():
    return BookflowError('E_FEATURE_DISABLED', message=(
        'Packing is off for this company: draft shipments and boxes are an optional layer on shipping. An admin '
        'turns it on in Company settings (Shipping and fulfillment: Use pick lists and packing), or with `bookflow '
        'company update --pick-and-pack-enabled true`. Until then, ship straight from the order with `shipment '
        'post`; tracking numbers, freight to bill and the packing slip work without it.'),
        details=dict(feature=FEATURE, setting=SETTING, enable_command='company update'))


def require_enabled(s):
    if not enabled(s):
        raise disabled_error()


def weight_unit(s):
    return s.company.conn.execute(sa.select(c.company_info.c.weight_unit)).scalar() or 'lb'


def open_drafts(s):
    """Numbers of the drafts not shipped or cancelled."""
    return [row['number'] for row in _current_drafts(s) if row['status'] == 'draft']


# ---------------------------------------------------------------- drafts: reads


_CURRENT_DRAFT = """
    SELECT h.*, r.id AS revision_id, r.revision_number, r.status, r.ship_date, r.count_position, r.ship_method_id,
           r.carrier, r.tracking_numbers, r.memo, r.freight_minor_units, r.freight_item_id, r.reason
    FROM shipment_drafts h JOIN shipment_draft_revisions r ON r.draft_id = h.id
    WHERE r.revision_number = (SELECT max(x.revision_number) FROM shipment_draft_revisions x WHERE x.draft_id = h.id)
"""


def _current_drafts(s, where='', params=None):
    return [dict(row) for row in s.company.conn.execute(sa.text(_CURRENT_DRAFT + where), params or {}).mappings()]


def find_draft(s, selector):
    text = str(selector).strip()
    rows = []
    if is_ulid(text):
        rows = _current_drafts(s, ' AND h.id = :v', {'v': text.upper()})
    if not rows:
        rows = _current_drafts(s, ' AND h.number = :v', {'v': text})
    return rows[0] if rows else None


def draft_lines(s, draft_id):
    return [dict(row) for row in s.company.conn.execute(sa.select(c.shipment_draft_lines).where(
        c.shipment_draft_lines.c.draft_id == draft_id).order_by(c.shipment_draft_lines.c.position)).mappings()]


def draft_history(s, draft_id):
    return [dict(row) for row in s.company.conn.execute(sa.select(c.shipment_draft_revisions).where(
        c.shipment_draft_revisions.c.draft_id == draft_id).order_by(
        c.shipment_draft_revisions.c.revision_number)).mappings()]


def drafted(s, order_id, *, exclude=None):
    """``{root: base}`` live drafts of this order hold: other shipments of it plan around them."""
    rows = s.company.conn.execute(sa.text(
        "SELECT l.root_document_id, l.root_line_id, l.quantity_microunits FROM shipment_draft_lines l "
        "JOIN (" + _CURRENT_DRAFT + ") d ON d.id = l.draft_id WHERE d.status = 'draft' AND d.sales_order_id = :o "
        "AND d.id != :x"), {'o': order_id, 'x': exclude or ''}).all()
    out = {}
    for doc, line, quantity in rows:
        out[(doc, line)] = out.get((doc, line), 0) + int(quantity)
    return out


# ---------------------------------------------------------------- lines a packing reads


@dataclass
class PackLine:
    identity: str
    position: int
    item_id: str
    item_name: str
    quantity: int
    serials: list | None
    lot: str | None
    unit_weight: int | None


def _items(s, item_ids):
    rows = s.company.conn.execute(sa.select(c.items.c.id, c.items.c.full_name, c.items.c.unit_weight_microunits).where(
        c.items.c.id.in_(list(item_ids) or ['']))).all()
    return {row[0]: (row[1], row[2]) for row in rows}


def shipment_lines(s, revision_id):
    """The pack lines of a shipment revision: base quantity and the numbers its issue carries."""
    from bookflow.company import serial_lots
    rows = [dict(row) for row in s.company.conn.execute(sa.select(c.shipment_lines).where(
        c.shipment_lines.c.revision_id == revision_id).order_by(c.shipment_lines.c.position)).mappings()]
    numbers = serial_lots.line_numbers(s, [row['movement_id'] for row in rows if row['movement_id']])
    items = _items(s, {row['item_id'] for row in rows})
    out = []
    for row in rows:
        found = numbers.get(row['movement_id'])
        out.append(PackLine(row['line_identity'], row['position'], row['item_id'], items[row['item_id']][0],
                            int(row['quantity_microunits']),
                            list(found.serial_numbers) if found and found.serial_numbers else None,
                            found.lot if found else None, items[row['item_id']][1]))
    return out


def lines_of_draft(s, draft_id):
    rows = draft_lines(s, draft_id)
    items = _items(s, {row['item_id'] for row in rows})
    return [PackLine(row['line_identity'], row['position'], row['item_id'], items[row['item_id']][0],
                     int(row['quantity_microunits']), json.loads(row['serial_numbers']) if row['serial_numbers'] else None,
                     row['lot'], items[row['item_id']][1]) for row in rows]


def planned_lines(s, planned):
    """Pack lines of a shipment being planned (``shipments._Line``), by position, before identities exist."""
    items = _items(s, {line.facts.item_id for line in planned})
    return [PackLine(line.identity or f'#{position}', position, line.facts.item_id,
                     items[line.facts.item_id][0], line.base,
                     list(line.numbers.serial_numbers) if line.numbers and line.numbers.serial_numbers else None,
                     line.numbers.lot if line.numbers else None, items[line.facts.item_id][1])
            for position, line in enumerate(planned, 1)]


# ---------------------------------------------------------------- boxes


def load(s, revision_id):
    """The packing a revision carries: ``[box]``, each a dict with its facts and ``contents`` {identity: [qty, serials]}."""
    if revision_id is None:
        return []
    boxes = {}
    for row in s.company.conn.execute(sa.select(c.shipment_packages).where(
            c.shipment_packages.c.revision_id == revision_id)).mappings():
        boxes[row['id']] = dict(box=row['box_number'], package_type=row['package_type'], tare=row['tare_microunits'],
                                weight=row['weight_microunits'], dimensions=row['dimensions'],
                                tracking_number=row['tracking_number'], contents={})
    for row in s.company.conn.execute(sa.select(c.shipment_package_contents).where(
            c.shipment_package_contents.c.revision_id == revision_id)).mappings():
        boxes[row['package_id']]['contents'][row['line_identity']] = [
            int(row['quantity_microunits']), json.loads(row['serial_numbers']) if row['serial_numbers'] else None]
    return sorted(boxes.values(), key=lambda box: box['box'])


def rows(boxes, shipment_id, revision_id, provenance, *, identities=None):
    """``extra`` tuples writing ``boxes`` under ``revision_id``; ``identities`` maps placeholder line keys."""
    from bookflow.core.ids import new_id
    packages, contents = [], []
    for box in boxes:
        package_id = new_id()
        packages.append(dict(id=package_id, shipment_id=shipment_id, revision_id=revision_id, box_number=box['box'],
                             package_type=box.get('package_type'), tare_microunits=box.get('tare'),
                             weight_microunits=box.get('weight'), dimensions=box.get('dimensions'),
                             tracking_number=box.get('tracking_number'), **provenance))
        for identity, (quantity, serials) in box['contents'].items():
            if quantity <= 0:
                continue
            contents.append(dict(id=new_id(), package_id=package_id, revision_id=revision_id,
                                 line_identity=(identities or {}).get(identity, identity),
                                 quantity_microunits=quantity,
                                 serial_numbers=json.dumps(list(serials)) if serials else None, **provenance))
    out = []
    if packages:
        out.append(('shipment_packages', 'shipment_package', 'id', packages))
    if contents:
        out.append(('shipment_package_contents', 'shipment_package_content', 'id', contents))
    return out


def carry(boxes, lines):
    """The packing kept forward onto ``lines``: contents of lines no longer there drop, quantities clamp to what each
    line holds, serial numbers to those it carries. Boxes keep their facts, empty or not."""
    left = {line.identity: [line.quantity, set(line.serials) if line.serials else None] for line in lines}
    out = []
    for box in boxes:
        kept = {}
        for identity, (quantity, serials) in box['contents'].items():
            if identity not in left:
                continue
            room, numbers = left[identity]
            if serials and numbers is not None:
                serials = [n for n in serials if n in numbers]
                numbers.difference_update(serials)
                quantity = min(len(serials) * UNIT, room)
            else:
                serials = None
                quantity = min(quantity, room)
            if quantity > 0:
                left[identity][0] -= quantity
                kept[identity] = [quantity, serials or None]
        out.append(dict(box, contents=kept))
    return out


def _weight(text, field_name, *, zero=False):
    if text is None:
        return None
    try:
        value = parse_quantity_micro_units(str(text).strip())
    except BookflowError:
        raise _invalid(field_name, 'use a decimal weight') from None
    if value < 0 or (value == 0 and not zero):
        raise _invalid(field_name, 'must be positive')
    return value


def _line_for(lines, selector, field_name):
    text = str(selector).strip()
    for line in lines:
        if text == str(line.position) or text.upper() == line.identity.upper():
            return line
    raise _invalid(field_name, f'no line {text} on this shipment; name its position or line_identity')


def _item_id(s, selector, field_name):
    from bookflow.company import pick_lists
    item, _, _ = pick_lists._resolve_code(s, selector, field_name)
    return item['id'] if isinstance(item, dict) else item.id


def _lines_of_item(s, lines, selector, field_name):
    item = _item_id(s, selector, field_name)
    found = [line for line in lines if line.item_id == item]
    if not found:
        raise _invalid(field_name, 'no line of this shipment carries that item')
    return found


def _boxed(boxes, identity):
    quantity, serials = 0, []
    for box in boxes:
        held = box['contents'].get(identity)
        if held:
            quantity += held[0]
            serials += held[1] or []
    return quantity, serials


def validate(boxes, lines):
    """No line in boxes beyond what it holds, no serial number it does not carry or in two boxes."""
    by_identity = {line.identity: line for line in lines}
    numbers = set()
    seen = set()
    for box in boxes:
        if box['box'] in seen:
            raise _invalid('packages', f'box {box["box"]} is named twice')
        seen.add(box['box'])
    for identity, line in by_identity.items():
        quantity, serials = _boxed(boxes, identity)
        if quantity > line.quantity:
            raise _invalid('packages', f'line {line.position} holds {_q(line.quantity)}', message=(
                f'Line {line.position} ({line.item_name}) ships {_q(line.quantity)}; the boxes hold {_q(quantity)}.'))
        for number in serials:
            if line.serials is None or number not in line.serials:
                raise _invalid('packages', f'serial number {number} is not on line {line.position}', message=(
                    f'{number} is not one of the units line {line.position} ({line.item_name}) ships.'))
            if number in numbers:
                raise _invalid('packages', f'serial number {number} is in two boxes')
            numbers.add(number)
    for box in boxes:
        for identity in box['contents']:
            if identity not in by_identity:
                raise _invalid('packages', 'a box holds a line this shipment does not')


def _assign_serials(line, boxes, quantity, given, field_name):
    """Serial numbers a content of a numbered line holds: those given, else the line's first unboxed ones."""
    if line.serials is None:
        if given:
            raise _invalid(field_name, f'line {line.position} carries no serial numbers')
        return None
    if given:
        return list(given)
    _, boxed = _boxed(boxes, line.identity)
    free = [n for n in line.serials if n not in boxed]
    count = quantity // UNIT
    if count * UNIT != quantity or count > len(free):
        raise _invalid(field_name, f'line {line.position} has {len(free)} serial numbers left to box')
    return free[:count]


def from_packages(s, packages, lines):
    """A whole packing from input: boxes numbered as given (or next), contents by line, item or serial numbers."""
    boxes = []
    used = set()
    for index, package in enumerate(packages or ()):
        number = package.box or (max(used, default=0) + 1)
        if number in used:
            raise _invalid(f'packages.{index}.box', f'box {number} is named twice')
        used.add(number)
        box = dict(box=number, package_type=(package.package_type or '').strip() or None,
                   tare=_weight(package.tare, f'packages.{index}.tare', zero=True),
                   weight=_weight(package.weight, f'packages.{index}.weight'),
                   dimensions=(package.dimensions or '').strip() or None,
                   tracking_number=(package.tracking_number or '').strip() or None, contents={})
        for at, content in enumerate(package.contents):
            field_name = f'packages.{index}.contents.{at}'
            if content.line is not None:
                line = _line_for(lines, content.line, field_name + '.line')
            elif content.item is not None:
                found = _lines_of_item(s, lines, content.item, field_name + '.item')
                if len(found) > 1:
                    raise _invalid(field_name + '.item', f'{len(found)} lines carry this item; name the line')
                line = found[0]
            elif content.serial_numbers:
                found = [l for l in lines if l.serials and content.serial_numbers[0] in l.serials]
                if not found:
                    raise _invalid(field_name, f'no line ships {content.serial_numbers[0]}')
                line = found[0]
            else:
                raise _invalid(field_name, 'name the line, the item or the serial numbers')
            if content.quantity is not None:
                quantity = _weight(content.quantity, field_name + '.quantity')
            elif content.serial_numbers:
                quantity = len(content.serial_numbers) * UNIT
            else:
                quantity = line.quantity - _boxed(boxes + [box], line.identity)[0]
            if quantity <= 0:
                raise _invalid(field_name, f'line {line.position} has nothing left to box')
            serials = _assign_serials(line, boxes + [box], quantity, content.serial_numbers, field_name)
            if serials is not None and len(serials) * UNIT != quantity:
                raise _invalid(field_name + '.quantity', 'give as many serial numbers as units')
            held = box['contents'].setdefault(line.identity, [0, None])
            held[0] += quantity
            if serials:
                held[1] = (held[1] or []) + serials
        boxes.append(box)
    boxes.sort(key=lambda box: box['box'])
    validate(boxes, lines)
    return boxes


def _take(boxes, line, quantity, serials, skip_box):
    """Take ``quantity`` of ``line`` (``serials`` when numbered) out of boxes other than ``skip_box``, last box first."""
    for box in sorted(boxes, key=lambda b: -b['box']):
        if box['box'] == skip_box or quantity <= 0:
            continue
        held = box['contents'].get(line.identity)
        if not held:
            continue
        if serials is not None:
            out = [n for n in (held[1] or []) if n in serials]
            if not out:
                continue
            held[1] = [n for n in held[1] if n not in out]
            held[0] -= len(out) * UNIT
            quantity -= len(out) * UNIT
        else:
            moved = min(held[0], quantity)
            held[0] -= moved
            quantity -= moved
        if held[0] <= 0:
            del box['contents'][line.identity]
    return quantity


def move(s, boxes, lines, inp):
    """``shipment pack --unit|--line --box``: that many units of that line (or that numbered unit) into box N, out of
    their box with 0; or, with no unit, box N's facts."""
    boxes = [dict(box, contents={k: [v[0], list(v[1]) if v[1] else None] for k, v in box['contents'].items()})
             for box in boxes]
    if inp.box is None:
        raise _invalid('box', 'name the box (0 takes units out of their box)')
    facts = {name: getattr(inp, name) for name in ('package_type', 'weight', 'tare', 'dimensions', 'tracking_number')
             if getattr(inp, name) is not None}
    target = next((box for box in boxes if box['box'] == inp.box), None) if inp.box else None
    if inp.box and target is None:
        target = dict(box=inp.box, package_type=None, tare=None, weight=None, dimensions=None, tracking_number=None,
                      contents={})
        boxes.append(target)
    if facts:
        if not inp.box:
            raise _invalid('box', 'box facts name a box from 1')
        for name, value in facts.items():
            text = str(value).strip()
            if name == 'weight':
                target['weight'] = None if text in ('', '0') else _weight(text, 'weight')
            elif name == 'tare':
                target['tare'] = None if text == '' else _weight(text, 'tare', zero=True)
            else:
                target[name] = text or None
    if inp.unit is None and inp.line is None:
        if not facts:
            raise _invalid('unit', 'name the unit or line to move, or a fact of the box')
        boxes.sort(key=lambda box: box['box'])
        return boxes
    serials = None
    if inp.line is not None:
        line = _line_for(lines, inp.line, 'line')
    else:
        text = inp.unit.strip()
        key = text.casefold()
        line = next((l for l in lines if l.serials and any(n.casefold() == key for n in l.serials)), None)
        if line is not None:
            serials = [n for n in line.serials if n.casefold() == key]
        else:
            by_lot = [l for l in lines if l.lot and l.lot.casefold() == key]
            found = by_lot or _lines_of_item(s, lines, text, 'unit')
            # The line with units still to put where they are going, else the first.
            line = next((l for l in found if (l.quantity - _boxed(boxes, l.identity)[0] > 0 if inp.box else
                                              _boxed(boxes, l.identity)[0] > 0)), found[0])
    if serials is None:
        quantity = _weight(inp.quantity, 'quantity') if inp.quantity is not None else UNIT
        if line.serials is not None:
            # A numbered line moves named units: unboxed ones first into a box, the newest boxed ones out.
            _, boxed = _boxed(boxes, line.identity)
            count = quantity // UNIT
            if count * UNIT != quantity:
                raise _invalid('quantity', 'numbered units move whole')
            if inp.box:
                inside = set(target['contents'].get(line.identity, [0, None])[1] or [])
                pool = [n for n in line.serials if n not in boxed] + \
                    [n for n in line.serials if n in boxed and n not in inside]
            else:
                pool = list(reversed(boxed))
            if len(pool) < count:
                raise _invalid('quantity', f'line {line.position} has {len(pool)} units to move')
            serials = pool[:count]
    else:
        quantity = len(serials) * UNIT
    if inp.box:
        unboxed = line.quantity - _boxed(boxes, line.identity)[0]
        if serials is not None:
            already = target['contents'].get(line.identity, [0, None])[1] or []
            serials = [n for n in serials if n not in already]
            quantity = len(serials) * UNIT
            _take(boxes, line, quantity, serials, inp.box)
        else:
            short = quantity - max(unboxed, 0)
            if short > 0 and _take(boxes, line, short, None, inp.box) > 0:
                raise _invalid('quantity', f'line {line.position} ships {_q(line.quantity)}', message=(
                    f'Line {line.position} ({line.item_name}) ships {_q(line.quantity)}; there are not '
                    f'{_q(quantity)} more to put in box {inp.box}.'))
        if quantity > 0:
            held = target['contents'].setdefault(line.identity, [0, None])
            held[0] += quantity
            if serials:
                held[1] = (held[1] or []) + serials
    else:
        if _take(boxes, line, quantity, serials, None) > 0:
            raise _invalid('quantity', f'line {line.position} has fewer units in boxes', message=(
                f'Line {line.position} ({line.item_name}) has {_q(_boxed(boxes, line.identity)[0])} in boxes.'))
    boxes = [box for box in boxes if box['contents'] or any(box.get(k) for k in (
        'package_type', 'tare', 'weight', 'dimensions', 'tracking_number')) or box is target]
    boxes.sort(key=lambda box: box['box'])
    validate(boxes, lines)
    return boxes


def weight_of(quantity, unit_weight):
    return round_ratio_half_even(int(quantity) * int(unit_weight), UNIT) if unit_weight else 0


def output(s, boxes, lines, *, show_empty=False):
    """The packing as a reader sees it, weights worked out from the items; None when nothing is packed."""
    if not boxes and not show_empty:
        return None
    by_identity = {line.identity: line for line in lines}
    packages = []
    total = unweighed_total = 0
    for box in boxes:
        computed = box.get('tare') or 0
        unweighed = 0
        contents = []
        for identity, (quantity, serials) in sorted(box['contents'].items(),
                                                    key=lambda kv: by_identity[kv[0]].position if kv[0] in by_identity else 0):
            line = by_identity.get(identity)
            if line is None:
                continue
            weight = weight_of(quantity, line.unit_weight) if line.unit_weight else None
            if weight is None:
                unweighed += quantity
            else:
                computed += weight
            contents.append(PackageContentOutput(line_identity=identity, position=line.position,
                                                 item_name=line.item_name, quantity=_q(quantity),
                                                 serial_numbers=serials or None,
                                                 weight=_q(weight) if weight is not None else None))
        counted = box['weight'] if box.get('weight') else computed
        total += counted
        unweighed_total += unweighed if not box.get('weight') else 0
        packages.append(PackageOutput(
            box=box['box'], package_type=box.get('package_type'), dimensions=box.get('dimensions'),
            tracking_number=box.get('tracking_number'), tare=_q(box['tare']) if box.get('tare') is not None else None,
            measured_weight=_q(box['weight']) if box.get('weight') else None, computed_weight=_q(computed),
            weight=_q(counted), unweighed=_q(unweighed), contents=contents))
    unboxed = []
    for line in lines:
        quantity, serials = _boxed(boxes, line.identity)
        left = line.quantity - quantity
        if left <= 0:
            continue
        free = [n for n in line.serials if n not in serials] if line.serials else None
        unboxed.append(UnboxedOutput(line_identity=line.identity, position=line.position, item_name=line.item_name,
                                     quantity=_q(left), serial_numbers=free or None))
        if line.unit_weight:
            total += weight_of(left, line.unit_weight)
        else:
            unweighed_total += left
    return PackingOutput(weight_unit=weight_unit(s), packages=packages, unboxed=unboxed, packed=not unboxed,
                         total_weight=_q(total), unweighed=_q(unweighed_total))
