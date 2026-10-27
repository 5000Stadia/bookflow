"""Shipment returns (R181 increment 2; design/pick-pack-ship.md §6).

Units a shipment took that no invoice has billed come back on a **shipment return**: a small dated stock document of
its own, so the shipment's history stays as shipped. Each line writes one ``receipt`` movement naming the shipment
line's issue (``returns_movement_id``), so the replay values it as any return is -- the endpoint share of that issue's
cost -- and a later recost of the issue recosts the return with it. The entry posts Dr Inventory Asset, Cr Shipped Not
Invoiced: the return's offset account *is* Shipped Not Invoiced, so its later corrections post there too.

The shipment line's derived balance (``shipments.LineBalance``) then holds its net cost (cost less what its returns
took back) less the share billed of what is still out. ``reopen`` (default true) leaves the units to ship again on the
order; false cancels them on it, in the same transaction, as ``release_to_order: cancel`` does. A void reverses the
movement and the entry, and undoes the cancellation.

Units an invoice billed come back on a credit memo instead (``credits._stock_entries`` names the shipment line's issue
through ``shipments.billed_issue``), and the order stays invoiced.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from types import SimpleNamespace

import sqlalchemy as sa

from bookflow.company import inventory, inventory_effects, journals, placements, shipments
from bookflow.company import schema as c
from bookflow.company.journal_models import JournalLineInput, JournalPostInput, JournalVoidInput
from bookflow.company.shipment_models import (
    ShipmentReturnHistoryOutput, ShipmentReturnLineOutput, ShipmentReturnListOutput, ShipmentReturnListRow,
    ShipmentReturnOutput,
)
from bookflow.company.shipment_return_schema import SEQUENCE
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_quantity_micro_units, parse_quantity_micro_units
from bookflow.core.ids import is_ulid, new_id
from bookflow.core.money import Money
from bookflow.core.registry import Applied, Plan

KIND = 'shipment_return'
_invalid = shipments._invalid
_plain = shipments._plain


def _money(minor, currency):
    return Money(int(minor), currency).to_dict()


# ---------------------------------------------------------------- reads


def _current_revision(s, return_id):
    return dict(s.company.conn.execute(sa.select(c.shipment_return_revisions).where(
        c.shipment_return_revisions.c.return_id == return_id).order_by(
        c.shipment_return_revisions.c.revision_number.desc()).limit(1)).mappings().one())


def resolve(s, selector):
    t = c.shipment_returns
    text = str(selector).strip()
    row = None
    if is_ulid(text):
        row = s.company.conn.execute(sa.select(t).where(t.c.id == text.upper())).mappings().first()
    if row is None:
        row = s.company.conn.execute(sa.select(t).where(t.c.number == text)).mappings().first()
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'shipment_return', 'selector': text})
    header = dict(row)
    header['revision'] = _current_revision(s, header['id'])
    return header


def _lines(s, return_id):
    return [dict(row) for row in s.company.conn.execute(sa.select(c.shipment_return_lines).where(
        c.shipment_return_lines.c.return_id == return_id).order_by(c.shipment_return_lines.c.position)).mappings()]


# ---------------------------------------------------------------- planning


@dataclass
class _Line:
    key: str
    shipment_line: dict
    base: int
    numbers: object = None
    cost: int = 0


@dataclass
class _Return:
    shipment: dict
    order: dict
    order_rev: dict
    date: str
    lines: list
    change: object = None
    journal_lines: list = field(default_factory=list)
    line_of: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


def _returnable(s, rows):
    """``{identity: base microunits still out and not invoiced}`` for the shipment's current lines."""
    identities = [row['line_identity'] for row in rows]
    billed = {}
    for row in shipments.live_billings(s, identities):
        billed[row['shipment_line_identity']] = billed.get(row['shipment_line_identity'], 0) + int(row['quantity_microunits'])
    returned = shipments.returned_by_identity(s, identities)
    return {row['line_identity']: int(row['quantity_microunits']) - billed.get(row['line_identity'], 0)
            - returned.get(row['line_identity'], 0) for row in rows}


def _pick(rows, entry, index, s):
    if entry.shipment_line is not None:
        text = entry.shipment_line.strip()
        found = [row for row in rows if str(row['position']) == text or row['line_identity'] == text.upper()]
        if not found:
            raise _invalid(f'lines.{index}.shipment_line', 'no line of this shipment has this position or identity')
        return found[0]
    if entry.item is None:
        raise _invalid(f'lines.{index}', 'name shipment_line or item')
    from bookflow.company import sales_defaults
    item = sales_defaults._row(s.company, 'item', entry.item, active=False)['id']
    found = [row for row in rows if row['item_id'] == item]
    if len(found) != 1:
        raise _invalid(f'lines.{index}.item', f'{len(found)} lines of this shipment carry this item; name the shipment_line')
    return found[0]


def _names(s, item_ids):
    return dict(s.company.conn.execute(sa.select(c.items.c.id, c.items.c.full_name).where(
        c.items.c.id.in_(list(item_ids) or ['']))).all())


def _plan(s, inp, sni_id):
    from bookflow.company import serial_lots, work
    shipment = shipments.resolve(s, inp.shipment)
    rev = shipment['revision']
    if inp.expected_version is not None and inp.expected_version != rev['revision_number']:
        raise BookflowError('E_VERSION_CONFLICT', details={'record_id': shipment['id'],
                                                          'expected_version': inp.expected_version,
                                                          'current_version': rev['revision_number']})
    if rev['status'] != 'shipped':
        raise _invalid('shipment', f'this shipment is {rev["status"]}; nothing on it can come back')
    date = inp.date or clock.today_iso()
    if date < rev['ship_date']:
        raise _invalid('date', f'before the ship date {rev["ship_date"]}', message=(
            f'Shipment {shipment["number"]} shipped on {rev["ship_date"]}; its units cannot come back before that.'),
            ship_date=rev['ship_date'])
    order = work.resolve(s, shipment['sales_order_id'], 'sales_order')
    order_rev = work.revision(s, order)
    rows = shipments._revision_lines(s, rev['id'])
    left = _returnable(s, rows)
    names = _names(s, {row['item_id'] for row in rows})
    chosen = []
    if inp.lines is None:
        chosen = [(row, left[row['line_identity']], None) for row in rows if left[row['line_identity']] > 0]
        if not chosen:
            raise _invalid('lines', 'nothing on this shipment is out and not invoiced', message=(
                f'Every unit of shipment {shipment["number"]} is invoiced or already back. Units an invoice billed '
                'come back on a credit memo that names the invoice line (`credit-memo post` with source_invoice '
                'and source_line).'), reason='nothing_to_return')
    else:
        seen = set()
        for index, entry in enumerate(inp.lines):
            row = _pick(rows, entry, index, s)
            if row['line_identity'] in seen:
                raise _invalid(f'lines.{index}', 'name each shipment line once')
            seen.add(row['line_identity'])
            room = left[row['line_identity']]
            if entry.quantity is not None:
                try:
                    base = parse_quantity_micro_units(entry.quantity)
                except BookflowError:
                    raise _invalid(f'lines.{index}.quantity', 'use a positive decimal quantity') from None
                if base <= 0:
                    raise _invalid(f'lines.{index}.quantity', 'must be positive')
            else:
                base = room
            if base > room or room <= 0:
                raise _invalid(f'lines.{index}.quantity', f'more than the {_plain(max(room, 0))} still out and not invoiced',
                               message=(
                    f'{names.get(row["item_id"])}: {_plain(base)} is more than the {_plain(max(room, 0))} of shipment '
                    f'{shipment["number"]} line {row["position"]} still out and not invoiced. Units an invoice billed '
                    'come back on a credit memo naming the invoice line.'), reason='return_exceeds_out')
            chosen.append((row, base, serial_lots.from_line(entry)))
    lines = [_Line(key=f'r{index}', shipment_line=row, base=base, numbers=numbers)
             for index, (row, base, numbers) in enumerate(chosen)]
    site_id = placements.site_id_for(s, inp.site) if getattr(inp, 'site', None) is not None else None
    entries = []
    for line in lines:
        row = line.shipment_line
        entries.append(inventory_effects.Entry(
            key=line.key, item_id=row['item_id'], item_name=names.get(row['item_id'], row['item_id']), kind='receipt',
            quantity_microunits=line.base, asset_account_id=row['asset_account_id'], offset_account_id=sni_id,
            class_id=row['class_id'], returns_movement_id=row['movement_id'], site_id=site_id,
            numbers=line.numbers))
    change = inventory_effects.plan(s, entries=entries, date=date, currency=order_rev['currency'], field='lines',
                                    count_position=inp.count_position)
    plan = _Return(shipment=shipment, order=order, order_rev=order_rev, date=date, lines=lines, change=change)
    currency = order_rev['currency']
    for line in lines:
        line.cost = change.costs.get(line.key, 0)
        if not line.cost:
            continue
        row = line.shipment_line
        text = f'Back from shipment {shipment["number"]}: {_plain(line.base)} {names.get(row["item_id"])}'
        common = dict(amount=Money(line.cost, currency).to_dict(), name_type='customer', name_id=shipment['customer_id'],
                      description=text, class_id=row['class_id'])
        plan.journal_lines.append(JournalLineInput(account=row['asset_account_id'], side='debit', **common))
        plan.line_of[line.key] = len(plan.journal_lines)
        plan.journal_lines.append(JournalLineInput(account=sni_id, side='credit', **common))
    if not plan.journal_lines:
        raise _invalid('lines', 'the units returned are worth nothing', message=(
            'Every unit on this return is worth nothing in the books, so nothing moves out of Shipped Not Invoiced. '
            'Return them with a line that carries cost, or correct the shipment with `shipment update`.'),
            reason='return_without_value')
    plan.warnings = list(change.warnings)
    return plan


def _order_rows(s, order, order_rev):
    return {row['root']: row for row in shipments._order_lines(s, order, order_rev)}


def _released(s, order, order_rev, lines, sign=1):
    """The order lines and quantities (in each line's unit) a return cancels, or a void un-cancels."""
    rows = _order_rows(s, order, order_rev)
    out = {}
    for line in lines:
        row = rows.get((line['root_document_id'], line['root_line_id']))
        if row is None:
            continue
        quantity = shipments._to_selected(int(line['quantity_microunits']), row['facts'].unit_factor_nanounits)
        key = row['line']['line_id']
        if key in out:
            out[key]['quantity'] += sign * quantity
        else:
            out[key] = dict(document=dict(order), line_id=key, item_id=row['facts'].item_id, quantity=sign * quantity,
                            row=row)
    return list(out.values())


# ---------------------------------------------------------------- writing


def _number(s):
    seq = s.company.conn.execute(sa.select(c.sequences).where(c.sequences.c.name == SEQUENCE)).mappings().first()
    number, prefix = (seq['next_number'], seq['prefix']) if seq else (1001, '')
    while s.company.conn.execute(sa.select(c.shipment_returns.c.id).where(
            c.shipment_returns.c.number == f'{prefix}{number}')).first():
        number += 1
    return f'{prefix}{number}', dict(name=SEQUENCE, next_number=number + 1, prefix=prefix)


def _post(s, ctx, inp, *, persist):
    account = shipments.find_account(s.company)
    if account is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'shipment', 'selector': inp.shipment})
    plan = _plan(s, inp, account['id'])
    journals.open_dates(s, [plan.date])
    inventory_effects.open_dates(s, plan.change)
    journal = JournalPostInput(date=plan.date, memo=inp.memo or f'Return from shipment {plan.shipment["number"]}',
                               lines=plan.journal_lines)
    fresh = journals.prepare(s, ctx, journal, 'post', owner=inventory.OWNER)
    holder = SimpleNamespace(change=plan.change, line_of=plan.line_of)
    shipments._bind(holder, fresh)
    inventory_effects.check(s, plan.change, fresh.data['pending']['posting_lines'])
    released = [] if inp.reopen else _released(s, plan.order, plan.order_rev, [
        dict(root_document_id=line.shipment_line['root_document_id'], root_line_id=line.shipment_line['root_line_id'],
             quantity_microunits=line.base) for line in plan.lines])
    if not persist:
        return _preview(s, plan, inp, released), [], 'preview'
    at = clock.now_iso()
    event = fresh.data['event']
    provenance = dict(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value, audit_event_id=event)
    tx = fresh.data['header']
    number, sequence = _number(s)
    return_id = new_id()
    extra = [('inventory_documents', 'inventory_document', 'transaction_id',
              [dict(transaction_id=tx['id'], type='journal_entry', kind=KIND, **provenance)]),
             ('shipment_returns', 'shipment_return', 'id', [dict(
                 id=return_id, number=number, shipment_id=plan.shipment['id'], sales_order_id=plan.order['id'],
                 customer_id=plan.shipment['customer_id'], return_date=plan.date, reopen=bool(inp.reopen),
                 transaction_id=tx['id'], **provenance)]),
             ('shipment_return_revisions', 'shipment_return_revision', 'id', [dict(
                 id=new_id(), return_id=return_id, revision_number=1, status='returned',
                 reason=(ctx.reason or '').strip() or None, **provenance)])]
    movements = plan.change.rows(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value,
                                 audit_event_id=event)
    extra.extend(placements.extras(placements.placements_for_rows(s, movements, plan.change.sites,
                                                                  plan.change.placements)))
    extra.append(('inventory_movements', 'inventory_movement', 'id', movements))
    by_key = {m.key: m.values['id'] for m in plan.change.movements if m.key is not None}
    extra.append(('shipment_return_lines', 'shipment_return_line', 'id', [dict(
        id=new_id(), return_id=return_id, position=position, shipment_line_identity=line.shipment_line['line_identity'],
        item_id=line.shipment_line['item_id'], quantity_microunits=line.base, movement_id=by_key[line.key],
        **provenance) for position, line in enumerate(plan.lines, 1)]))
    applied = journals.persist_prepared(fresh, ctx, s, command_name='shipment return', extra=tuple(extra),
                                        noun='shipment return', number=number)
    touched = list(applied.touched)
    touched += inventory_effects.write_corrections(s, ctx, plan.change, command_name='shipment return', created_at=at)
    from sqlalchemy.dialects.sqlite import insert
    s.company.conn.execute(insert(c.sequences).values(**sequence).on_conflict_do_update(
        index_elements=['name'], set_=sequence))
    if released:
        from bookflow.company import order_releases
        order_releases.cancel(s, ctx, released)
    return show_output(s, return_id, warnings=plan.warnings), touched, f'return shipment {plan.shipment["number"]}'


def _preview(s, plan, inp, released):
    currency = plan.order_rev['currency']
    names = _names(s, {line.shipment_line['item_id'] for line in plan.lines})
    lines = [ShipmentReturnLineOutput(
        position=position, shipment_line_identity=line.shipment_line['line_identity'],
        shipment_line_position=line.shipment_line['position'], item_id=line.shipment_line['item_id'],
        item_name=names.get(line.shipment_line['item_id'], line.shipment_line['item_id']), quantity=_plain(line.base),
        bin=line.numbers.bin if line.numbers else None, lot=line.numbers.lot if line.numbers else None,
        serial_numbers=list(line.numbers.serial_numbers) if line.numbers and line.numbers.serial_numbers else None,
        value=_money(line.cost, currency)) for position, line in enumerate(plan.lines, 1)]
    after = {}
    cancelled = {row['line_id']: row['quantity'] for row in released}
    for row in _order_rows(s, plan.order, plan.order_rev).values():
        back = sum(shipments._to_selected(line.base, row['facts'].unit_factor_nanounits) for line in plan.lines
                   if (line.shipment_line['root_document_id'], line.shipment_line['root_line_id']) == row['root'])
        left = row['to_ship'] + (0 if row['line']['line_id'] in cancelled else back)
        after[row['line']['line_id']] = _plain(max(left, 0))
    customer = s.company.conn.execute(sa.select(c.customers.c.full_name).where(
        c.customers.c.id == plan.shipment['customer_id'])).scalar_one_or_none()
    return ShipmentReturnOutput(
        id=None, number=None, version=None, status='will_return', shipment_id=plan.shipment['id'],
        shipment_number=plan.shipment['number'], sales_order_id=plan.order['id'], sales_order_number=plan.order['number'],
        customer_id=plan.shipment['customer_id'], customer_name=customer, date=plan.date, reopen=bool(inp.reopen),
        memo=inp.memo, lines=lines, value=_money(sum(line.cost for line in plan.lines), currency), order_to_ship=after,
        warnings=list(dict.fromkeys(plan.warnings)))


def _void(s, ctx, inp, *, persist):
    if not ctx.reason or not ctx.reason.strip():
        raise BookflowError('E_REASON_REQUIRED')
    header = resolve(s, inp.shipment_return)
    rev = header['revision']
    if inp.expected_version is not None and inp.expected_version != rev['revision_number']:
        raise BookflowError('E_VERSION_CONFLICT', details={'record_id': header['id'],
                                                          'expected_version': inp.expected_version,
                                                          'current_version': rev['revision_number']})
    if rev['status'] == 'voided':
        raise _invalid('shipment_return', 'this return is already voided')
    from bookflow.company import work
    order = work.resolve(s, header['sales_order_id'], 'sales_order')
    order_rev = work.revision(s, order)
    shipment = shipments.resolve(s, header['shipment_id'])
    lines = _lines(s, header['id'])
    shipment_rows = {row['line_identity']: row for row in shipments._revision_lines(s, shipment['revision']['id'])}
    roots = [dict(root_document_id=shipment_rows[line['shipment_line_identity']]['root_document_id'],
                  root_line_id=shipment_rows[line['shipment_line_identity']]['root_line_id'],
                  quantity_microunits=line['quantity_microunits'])
             for line in lines if line['shipment_line_identity'] in shipment_rows]
    back = _released(s, order, order_rev, roots)
    if header['reopen']:
        # The units go back out, shipped and not invoiced: the order must still have them to ship.
        for row in back:
            if row['row']['to_ship'] < row['quantity']:
                raise BookflowError('E_WORK_DEPENDENCY', message=(
                    f'Sales order {order["number"]} line {row["line_id"]} has {_plain(row["row"]["to_ship"])} to ship, '
                    f'fewer than the {_plain(row["quantity"])} this return reopened: they were shipped again or billed '
                    'since. Void that shipment or invoice first, or leave this return standing.'),
                    details=dict(problem='the reopened units were shipped or billed since', order=order['number'],
                                 order_line=row['line_id'], reason='return_reopened_units_taken'))
    else:
        if order['status'] != 'open' or not order['active']:
            raise BookflowError('E_WORK_DEPENDENCY', details=dict(source_id=order['id'], problem=(
                'this return cancelled its units on the order, which is no longer open; reopen the order first')))
        for row in back:
            cancelled = Fraction(row['row']['facts'].cancelled_quantity_microunits)
            if cancelled < int(row['quantity']):
                raise BookflowError('E_WORK_DEPENDENCY', message=(
                    f'This return cancelled {_plain(row["quantity"])} on sales order {order["number"]} line '
                    f'{row["line_id"]}, which now has only {_plain(cancelled)} cancelled; correct the order first.'),
                    details=dict(problem='the order no longer carries the cancellation', order=order['number'],
                                 reason='return_cancellation_changed'))
    journals.open_dates(s, [header['return_date']])
    change = inventory_effects.plan(s, entries=[], reversing=inventory_effects.own_movements(s, header['transaction_id']),
                                    currency=order_rev['currency'], field='lines')
    inventory_effects.open_dates(s, change, [header['return_date']])
    version = s.company.conn.execute(sa.select(c.transactions.c.version).where(
        c.transactions.c.id == header['transaction_id'])).scalar_one()
    fresh = journals.prepare(s, ctx, JournalVoidInput(journal=header['transaction_id'], expected_version=version),
                             'void', owner=inventory.OWNER)
    if not persist:
        shown = show_output(s, header['id'])
        return shown.model_copy(update=dict(status='will_void', warnings=list(change.warnings))), [], 'preview'
    inventory_effects.bind_reversals(change, fresh.data['pending']['posting_lines'], fresh.data['pending']['posting_batches'])
    inventory_effects.check(s, change, fresh.data['pending']['posting_lines'])
    at = clock.now_iso()
    event = fresh.data['event']
    provenance = dict(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value, audit_event_id=event)
    extra = [('shipment_return_revisions', 'shipment_return_revision', 'id', [dict(
        id=new_id(), return_id=header['id'], revision_number=rev['revision_number'] + 1, status='voided',
        reason=ctx.reason.strip(), **provenance)])]
    movements = change.rows(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value, audit_event_id=event)
    if movements:
        extra.extend(placements.extras(placements.placements_for_rows(s, movements, change.sites, change.placements)))
        extra.append(('inventory_movements', 'inventory_movement', 'id', movements))
    applied = journals.persist_prepared(fresh, ctx, s, command_name='shipment-return void', extra=tuple(extra),
                                        noun='shipment return', number=header['number'])
    touched = list(applied.touched)
    touched += inventory_effects.write_corrections(s, ctx, change, command_name='shipment-return void', created_at=at)
    if not header['reopen'] and back:
        from bookflow.company import order_releases
        order_releases.cancel(s, ctx, [dict(row, quantity=-row['quantity']) for row in back])
    return show_output(s, header['id'], warnings=change.warnings), touched, f'void shipment return {header["number"]}'


# ---------------------------------------------------------------- output


def show_output(s, return_id, warnings=()):
    from bookflow.company import bins, serial_lots, work
    header = resolve(s, return_id)
    rev = header['revision']
    shipment = s.company.conn.execute(sa.select(c.shipments).where(c.shipments.c.id == header['shipment_id'])).mappings().one()
    order = work.resolve(s, header['sales_order_id'], 'sales_order')
    order_rev = work.revision(s, order)
    currency = order_rev['currency']
    lines = _lines(s, header['id'])
    names = _names(s, {line['item_id'] for line in lines})
    movements = {row['id']: dict(row) for row in s.company.conn.execute(sa.select(c.inventory_movements).where(
        c.inventory_movements.c.id.in_([line['movement_id'] for line in lines] or ['']))).mappings()}
    numbers = serial_lots.line_numbers(s, list(movements))
    value_of = _values(s, list(movements))
    positions = dict(s.company.conn.execute(sa.text(
        'SELECT line_identity, max(position) FROM shipment_lines WHERE shipment_id = :id GROUP BY line_identity'),
        {'id': header['shipment_id']}).all())
    out_lines = []
    total = 0
    for line in lines:
        place = placements.place_of_movement(s, line['movement_id'])
        number = numbers.get(line['movement_id'])
        value = value_of.get(line['movement_id'], 0) if rev['status'] == 'returned' else 0
        total += value
        out_lines.append(ShipmentReturnLineOutput(
            position=line['position'], shipment_line_identity=line['shipment_line_identity'],
            shipment_line_position=positions.get(line['shipment_line_identity']), item_id=line['item_id'],
            item_name=names.get(line['item_id'], line['item_id']), quantity=_plain(line['quantity_microunits']),
            bin=bins.bin_names(s, [place[1]]).get(place[1]) if place and place[1] else None,
            lot=number.lot if number else None,
            serial_numbers=list(number.serial_numbers) if number and number.serial_numbers else None,
            value=_money(value, currency)))
    history = [ShipmentReturnHistoryOutput(version=h['revision_number'], status=h['status'], reason=h['reason'],
                                           created_at=h['created_at'], created_by=h['created_by'],
                                           created_via=h['created_via'])
               for h in s.company.conn.execute(sa.select(c.shipment_return_revisions).where(
                   c.shipment_return_revisions.c.return_id == header['id']).order_by(
                   c.shipment_return_revisions.c.revision_number)).mappings()]
    customer = s.company.conn.execute(sa.select(c.customers.c.full_name).where(
        c.customers.c.id == header['customer_id'])).scalar_one_or_none()
    after = {row['line']['line_id']: _plain(row['to_ship']) for row in shipments._order_lines(s, order, order_rev)}
    memo = s.company.conn.execute(sa.text(
        'SELECT r.memo FROM transactions t JOIN transaction_revisions r ON r.id = t.current_revision_id WHERE t.id = :id'),
        {'id': header['transaction_id']}).scalar()
    next_commands = []
    if rev['status'] == 'returned' and header['reopen'] and any(Fraction(v) > 0 for v in after.values()):
        next_commands.append(f'bookflow shipment post --sales-order {order["number"]} --json')
    return ShipmentReturnOutput(
        id=header['id'], number=header['number'], version=rev['revision_number'], status=rev['status'],
        shipment_id=header['shipment_id'], shipment_number=shipment['number'], sales_order_id=order['id'],
        sales_order_number=order['number'], customer_id=header['customer_id'], customer_name=customer,
        date=header['return_date'], reopen=bool(header['reopen']), memo=memo, transaction_id=header['transaction_id'],
        lines=out_lines, value=_money(total, currency), order_to_ship=after, history=history,
        warnings=list(dict.fromkeys(warnings)), next_commands=next_commands)


def _values(s, movement_ids):
    """Each return movement's value now: its own and every correction of it."""
    if not movement_ids:
        return {}
    params = {}
    marks = shipments._marks('mv', movement_ids, params)
    out = {}
    for row in s.company.conn.execute(sa.text(
            f'SELECT coalesce(corrects_movement_id, id) AS owner, value_minor_units FROM inventory_movements '
            f'WHERE id IN ({marks}) OR corrects_movement_id IN ({marks})'), params).mappings():
        out[row['owner']] = out.get(row['owner'], 0) + int(row['value_minor_units'])
    return out


# ---------------------------------------------------------------- the command surface


def prepare(s, ctx, inp, operation):
    run = _post if operation == 'return' else _void
    output, _, _ = run(s, ctx, inp, persist=False)
    return Plan(output, {'input': inp, 'operation': operation})


def apply(plan, ctx, s):
    run = _post if plan.data['operation'] == 'return' else _void
    output, touched, summary = run(s, ctx, plan.data['input'], persist=True)
    return Applied(output, touched, summary, audited=True)


def show(s, inp):
    return show_output(s, resolve(s, inp.shipment_return)['id'])


def query(s, inp):
    params = {}
    where = ['1 = 1']
    if inp.shipment is not None:
        where.append('h.shipment_id = :shipment')
        params['shipment'] = shipments.resolve(s, inp.shipment)['id']
    if inp.customer is not None:
        from bookflow.company import sales_defaults
        where.append('h.customer_id = :customer')
        params['customer'] = sales_defaults._row(s.company, 'customer', inp.customer, active=False)['id']
    if inp.status is not None:
        where.append('cur.status = :status')
        params['status'] = inp.status
    if inp.date_from is not None:
        where.append('h.return_date >= :date_from')
        params['date_from'] = inp.date_from
    if inp.date_to is not None:
        where.append('h.return_date <= :date_to')
        params['date_to'] = inp.date_to
    found = shipments._rows(s, f"""SELECT h.*, cur.revision_number, cur.status, sh.number AS shipment_number,
            w.number AS order_number, cu.full_name AS customer_name,
            (SELECT sum(l.quantity_microunits) FROM shipment_return_lines l WHERE l.return_id = h.id) AS quantity
        FROM shipment_returns h
        JOIN shipment_return_revisions cur ON cur.return_id = h.id AND cur.revision_number = (
            SELECT max(x.revision_number) FROM shipment_return_revisions x WHERE x.return_id = h.id)
        JOIN shipments sh ON sh.id = h.shipment_id
        JOIN work_documents w ON w.id = h.sales_order_id
        LEFT JOIN customers cu ON cu.id = h.customer_id
        WHERE {' AND '.join(where)} ORDER BY h.return_date DESC, h.number DESC""", params)
    currency = shipments._home(s)
    rows = []
    for row in found[:inp.limit]:
        movements = [line['movement_id'] for line in _lines(s, row['id'])]
        value = sum(_values(s, movements).values()) if row['status'] == 'returned' else 0
        rows.append(ShipmentReturnListRow(
            id=row['id'], number=row['number'], version=row['revision_number'], status=row['status'],
            date=row['return_date'], shipment_number=row['shipment_number'], sales_order_number=row['order_number'],
            customer_name=row['customer_name'], reopen=bool(row['reopen']), quantity=_plain(row['quantity'] or 0),
            value=_money(value, currency)))
    return ShipmentReturnListOutput(rows=rows, total=len(found))
