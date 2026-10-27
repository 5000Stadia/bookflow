"""Pick lists (R181 increment 3, design/pick-pack-ship.md §4).

Picking is an optional layer on shipping (K3): a company turns it on with ``company update
--pick-and-pack-enabled true``; while it is off every `pick-list` command refuses with E_FEATURE_DISABLED and
`shipment post` works from the order alone.

**What to pick.** For each open stocked order line at the site: what it has to ship, less what other live lists
already hold for it ("on open picks"), limited by what the site can give the list's orders -- on hand at the site,
less what documents not on the list commit there, less what the list's orders already hold on other lists. Lines
competing for the last units go in order date, then number; the rest is a ``short`` line with no bin.

**Where from.** The item's default bin first, then other bins holding some (natural order of their full names), no
bin last, splitting a line over bins; within a bin, lots earliest-expiring unexpired first. The list is in pick
order: bin, item name, order date.

**Holding.** A list that is not cancelled holds, for each order not yet shipped from it, what is planned (open, at
least what was picked) or what was picked (done). A shipment posted from the list for an order consumes that order's
picks; voiding it gives them back to the list.

**Entries** append, as count entries do. Each order line's picks replay in order to chunks (bin, lot, serial number,
quantity): ``add`` appends one, ``set`` grows by the difference or takes back the newest first. With a staging bin at
the site, each entry moves the chunks it added from their bin to staging and the ones it took back from staging to
their bin, netting to zero (source ``pick``); cancelling a list moves its staged, unshipped units back the same way,
under the cancelling revision. Without one, picks move nothing and a count of a bin names the picked, unshipped units
in it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlalchemy as sa

from bookflow.company import inventory, placements, schema as c
from bookflow.company.pick_list_models import (
    PickHistoryOutput, PickLineOutput, PickListOutput, PickListPageOutput, PickListRow, PickOrderOutput,
)
from bookflow.company.pick_list_schema import FEATURE, SEQUENCE, SETTING
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_quantity_micro_units, parse_quantity_micro_units
from bookflow.core.ids import is_ulid, new_id
from bookflow.core.registry import Applied, Plan, Touched

UNIT = 1_000_000


def _invalid(field_name, problem, message=None, **details):
    return BookflowError('E_VALIDATION', message=message,
                         details={'fields': [{'field': field_name, 'problem': problem}], **details})


def _q(units):
    return format_quantity_micro_units(int(units))


def _today(s):
    from bookflow.company.memorized_schedule import today
    return today(getattr(s, 'company_tz', None))


# ---------------------------------------------------------------- the switch

def enabled(s):
    return bool(s.company.conn.execute(sa.select(getattr(c.company_info.c, SETTING))).scalar())


def disabled_error():
    return BookflowError('E_FEATURE_DISABLED', message=(
        'Picking is off for this company: pick lists are an optional layer on shipping. An admin turns it on in '
        'Company settings (Shipping and fulfillment: Use pick lists), or with `bookflow company update '
        '--pick-and-pack-enabled true`. Until then, ship straight from the order with `shipment post`.'),
        details=dict(feature=FEATURE, setting=SETTING, enable_command='company update'))


def require_enabled(s):
    if not enabled(s):
        raise disabled_error()


def check_turning_off(s):
    """Turning picking off is refused while a list is open, or any list (a done one too) still has staged units no
    shipment took, naming it: `pick-list cancel` puts them back, and it needs picking on."""
    if not s.company.conn.execute(sa.text("SELECT 1 FROM sqlite_schema WHERE name = 'pick_lists'")).first():
        return
    rows = _current_rows(s)
    staged = [row['number'] for row in rows if row['status'] == 'done' and staged_left(s, row)]
    if staged:
        names = ', '.join(staged[:10])
        raise BookflowError('E_WORK_DEPENDENCY', message=(
            f'Pick list {names} still has staged units no shipment took. Cancel it (`pick-list cancel {staged[0]}`) '
            'to move them back to their bins before turning picking off.'),
            details=dict(problem='a pick list has staged units not shipped', pick_lists=staged,
                         reason='pick_list_staged'))
    open_lists = [row['number'] for row in rows if row['status'] == 'open']
    if open_lists:
        names = ', '.join(open_lists[:10])
        raise BookflowError('E_WORK_DEPENDENCY', message=(
            f'Pick list {names} is still open. Ship from it, close it (`pick-list close {open_lists[0]}`) or cancel '
            'it before turning picking off.'),
            details=dict(problem='an open pick list exists', pick_lists=open_lists, reason='pick_list_open'))


# ---------------------------------------------------------------- reading

_CURRENT = """SELECT h.*, r.status, r.revision_number, r.reason AS revision_reason, r.id AS revision_id
    FROM pick_lists h JOIN pick_list_revisions r ON r.pick_list_id = h.id
    WHERE r.revision_number = (SELECT max(x.revision_number) FROM pick_list_revisions x WHERE x.pick_list_id = h.id)"""


def _current_rows(s, where='', params=None):
    return [dict(row) for row in s.company.conn.execute(sa.text(_CURRENT + where), params or {}).mappings()]


def resolve(s, selector):
    text = str(selector).strip()
    rows = _current_rows(s, ' AND (h.id = :id OR h.number = :n OR upper(h.number) = upper(:n))',
                         dict(id=text.upper() if is_ulid(text) else '', n=text))
    if not rows and text.isdigit():
        rows = _current_rows(s, ' AND h.number = :n', dict(n=f'P-{text}'))
    if not rows:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'pick_list', 'selector': text})
    return rows[0]


def _lines(s, list_ids):
    if not list_ids:
        return []
    t = c.pick_list_lines
    return [dict(row) for row in s.company.conn.execute(
        sa.select(t).where(t.c.pick_list_id.in_(list(list_ids))).order_by(t.c.pick_list_id, t.c.position)).mappings()]


def _entries(s, list_ids):
    if not list_ids:
        return []
    t = c.pick_entries
    return [dict(row) for row in s.company.conn.execute(
        sa.select(t).where(t.c.pick_list_id.in_(list(list_ids))).order_by(t.c.pick_list_id, t.c.ordinal)).mappings()]


def live_links(s, list_ids=None):
    """``[{pick_list_id, shipment_id, sales_order_id, number}]``: shipments posted from lists, not voided."""
    where = ''
    params = {}
    if list_ids is not None:
        if not list_ids:
            return []
        marks = []
        for index, value in enumerate(list_ids):
            params[f'l{index}'] = value
            marks.append(f':l{index}')
        where = f' AND l.pick_list_id IN ({", ".join(marks)})'
    return [dict(row) for row in s.company.conn.execute(sa.text(
        "SELECT l.pick_list_id, l.shipment_id, l.sales_order_id, s.number FROM pick_list_shipments l "
        "JOIN shipments s ON s.id = l.shipment_id WHERE (SELECT r.status FROM shipment_revisions r "
        "WHERE r.shipment_id = l.shipment_id ORDER BY r.revision_number DESC LIMIT 1) = 'shipped'" + where),
        params).mappings()]


@dataclass
class Chunk:
    bin_id: str | None
    lot_id: str | None
    serial_id: str | None
    quantity: int
    staging_bin_id: str | None = None


@dataclass
class LineState:
    line: dict
    chunks: list = field(default_factory=list)

    @property
    def picked(self):
        return sum(chunk.quantity for chunk in self.chunks)


def _apply_entry(chunks, entry):
    """Apply one entry to a line's chunks; ``(added, removed)`` chunks."""
    quantity = int(entry['quantity_microunits'])
    staging = entry.get('staging_bin_id')
    if entry.get('serial_number_id'):
        held = [chunk for chunk in chunks if chunk.serial_id == entry['serial_number_id']]
        if entry['mode'] == 'set' and quantity == 0:
            for chunk in held:
                chunks.remove(chunk)
            return [], held
        if held:
            return [], []
        added = Chunk(entry['bin_id'], None, entry['serial_number_id'], UNIT, staging)
        chunks.append(added)
        return [added], []
    total = sum(chunk.quantity for chunk in chunks)
    delta = quantity if entry['mode'] == 'add' else quantity - total
    if delta > 0:
        added = Chunk(entry['bin_id'], entry['lot_id'], None, delta, staging)
        chunks.append(added)
        return [added], []
    removed, left = [], -delta
    while left > 0 and chunks:
        last = chunks[-1]
        take = min(last.quantity, left)
        removed.append(Chunk(last.bin_id, last.lot_id, last.serial_id, take, last.staging_bin_id))
        if take == last.quantity:
            chunks.pop()
        else:
            last.quantity -= take
        left -= take
    return [], removed


def line_states(s, lists, *, extra_entries=()):
    """``{line_id: LineState}`` for every line of these lists, replaying their entries (and ``extra_entries``)."""
    ids = [row['id'] for row in lists]
    states = {line['id']: LineState(line) for line in _lines(s, ids)}
    for entry in list(_entries(s, ids)) + list(extra_entries):
        state = states.get(entry['line_id'])
        if state is not None:
            _apply_entry(state.chunks, entry)
    return states


def _held(s, *, exclude_list=None, roots=None, site_id=None):
    """``{(list row, root): (base microunits, item id, order id)}`` live lists hold per order line."""
    if not s.company.conn.execute(sa.text("SELECT 1 FROM pick_lists LIMIT 1")).first():
        return {}
    where, params = (' AND h.site_id = :site', dict(site=site_id)) if site_id else ('', None)
    lists = [row for row in _current_rows(s, where, params)
             if row['status'] != 'cancelled' and row['id'] != exclude_list]
    if not lists:
        return {}
    shipped = {(link['pick_list_id'], link['sales_order_id']) for link in live_links(s, [row['id'] for row in lists])}
    by_id = {row['id']: row for row in lists}
    planned, picked, facts = {}, {}, {}
    for state in line_states(s, lists).values():
        line = state.line
        if (line['pick_list_id'], line['sales_order_id']) in shipped:
            continue
        key = (line['pick_list_id'], (line['root_document_id'], line['root_line_id']))
        if roots is not None and key[1] not in roots:
            continue
        facts[key] = (line['item_id'], line['sales_order_id'])
        if not line['short']:
            planned[key] = planned.get(key, 0) + int(line['quantity_microunits'])
        picked[key] = picked.get(key, 0) + state.picked
    out = {}
    for key in facts:
        held = (max(planned.get(key, 0), picked.get(key, 0)) if by_id[key[0]]['status'] == 'open'
                else picked.get(key, 0))
        if held:
            out[(by_id[key[0]]['number'], key[1])] = (held,) + facts[key]
    return out


def holds(s, *, exclude_list=None, roots=None):
    """``{root: base microunits}`` live lists hold for order lines ("on open picks")."""
    out = {}
    for (_, root), (held, _, _) in _held(s, exclude_list=exclude_list, roots=roots).items():
        out[root] = out.get(root, 0) + held
    return out


def _held_by_others(s, site_id, order_ids):
    """``{item_id: base}`` live lists at the site hold for orders not among these."""
    out = {}
    for (_, _), (held, item_id, order_id) in _held(s, site_id=site_id).items():
        if order_id not in order_ids:
            out[item_id] = out.get(item_id, 0) + held
    return out


def holding(s, *, order_id=None, roots=None):
    """``{root: (base, [list numbers])}`` live lists hold of these order lines (or of this order), while picking is
    on: an order a list holds ships from the list, not past it."""
    if not s.company.conn.execute(sa.text("SELECT 1 FROM sqlite_schema WHERE name = 'pick_lists'")).first() \
            or not enabled(s):
        return {}
    if order_id is not None:
        t = c.pick_list_lines
        roots = {(row[0], row[1]) for row in s.company.conn.execute(sa.select(
            t.c.root_document_id, t.c.root_line_id).where(t.c.sales_order_id == order_id))}
        if not roots:
            return {}
    out = {}
    for (number, root), (held, _, _) in _held(s, roots=roots).items():
        quantity, numbers = out.get(root, (0, []))
        out[root] = (quantity + held, sorted(set(numbers) | {number}))
    return out


def refuse_held(found, doing, remedy=None):
    """E_WORK_DEPENDENCY naming the lists that hold an order's units (``found`` from ``holding``)."""
    numbers = sorted({number for _, names in found.values() for number in names}, key=_natural)
    first = numbers[0]
    raise BookflowError('E_WORK_DEPENDENCY', message=(
        f'Pick list {", ".join(numbers)} holds units of this order, so {doing}. '
        + (remedy or f'Ship from the list (`shipment post --pick-list {first}`) or cancel it '
                     f'(`pick-list cancel {first}`) first.')),
        details=dict(problem='a pick list holds units of this order', pick_lists=numbers, reason='picked_on_list'))


def holders(s, root, *, exclude_list=None):
    """Numbers of the live lists holding this order line."""
    lists = [row for row in _current_rows(s) if row['status'] != 'cancelled' and row['id'] != exclude_list]
    ids = {row['id']: row['number'] for row in lists}
    found = {line['pick_list_id'] for line in _lines(s, list(ids))
             if (line['root_document_id'], line['root_line_id']) == root}
    return sorted(ids[i] for i in found)


# ---------------------------------------------------------------- names and order

def _natural(text):
    return tuple((0, int(part), '') if part.isdigit() else (1, 0, part.casefold())
                 for part in re.split(r'(\d+)', text) if part)


def bin_key(name):
    """Pick order of a bin's full name: each level compared naturally ("A2" before "A10"); no bin last."""
    if name is None:
        return (1,)
    return (0, tuple(_natural(level) for level in name.split(':')))


def staging_bin(s, site_id):
    return s.company.conn.execute(sa.select(c.inventory_sites.c.staging_bin_id).where(
        c.inventory_sites.c.id == site_id)).scalar()


def _position(s, site_id, date):
    """``after`` any count of the site recorded on this date, else ``before``."""
    found = s.company.conn.execute(sa.select(c.inventory_counts.c.id).where(
        c.inventory_counts.c.site_id == site_id, c.inventory_counts.c.count_date == date,
        c.inventory_counts.c.status == 'recorded')).first()
    return 'after' if found else 'before'


# ---------------------------------------------------------------- create

def _orders(s, inp, site_id, date):
    from bookflow.company import shipments, work
    if inp.orders and inp.ready:
        raise _invalid('ready', 'name orders or ready, not both')
    if inp.orders:
        found, seen = [], set()
        for index, selector in enumerate(inp.orders):
            try:
                header, rev = shipments._order(s, selector)
            except BookflowError as error:
                if error.code == 'E_RECORD_NOT_FOUND':
                    raise _invalid(f'orders.{index}', f'no sales order {selector}') from None
                raise
            if header['status'] != 'open' or not header['active']:
                raise _invalid(f'orders.{index}', f'sales order {header["number"]} is {header["status"]}; only an '
                                                  'open order is picked')
            if header['id'] not in seen:
                seen.add(header['id'])
                found.append((header, rev))
        return found, True
    if not inp.ready:
        raise _invalid('orders', 'name the orders to pick, or ready: true for every order that can ship at the site')
    t = c.work_documents
    out = []
    for header in work.rows(s, t, t.c.kind == 'sales_order', t.c.status == 'open', t.c.active.is_(True)):
        out.append((header, work.revision(s, header)))
    return out, False


def _place_holdings(s, item_id, site_id, date, staging_id, reserved):
    """``[[bin_id, lot_id, quantity]]`` the site's places hold of the item, staging left out, less ``reserved``."""
    from bookflow.company import bins, serial_lots
    held = {}
    for (_, _, bin_id, lot_id, serial_id), quantity in bins.held(s, date, site_id=site_id, item_ids=[item_id]).items():
        if bin_id is not None and bin_id == staging_id:
            continue
        key = (bin_id, None if serial_id else lot_id)
        held[key] = held.get(key, 0) + quantity
    for bin_id, quantity in reserved.items():
        left = quantity
        for key in sorted((k for k in held if k[0] == bin_id), key=lambda k: k[1] is None):
            take = min(max(held[key], 0), left)
            held[key] -= take
            left -= take
    lots = serial_lots.number_names(s, lot_ids=[lot for _, lot in held if lot])
    names = bins.bin_names(s, [b for b, _ in held if b])
    home = bins.default_bin_id(s, item_id, site_id)

    def lot_key(lot_id):
        if lot_id is None:
            return (2, '', '')
        expiry = lots[lot_id].get('expiry_date')
        if expiry and expiry < date:
            return (3, expiry, lots[lot_id]['number'])
        return (0 if expiry else 1, expiry or '', serial_lots.number_key(lots[lot_id]['number']))
    ordered = sorted(((b, lot, q) for (b, lot), q in held.items() if q > 0),
                     key=lambda r: (r[0] is None, r[0] != home, bin_key(names.get(r[0])) if r[0] else (1,),
                                    lot_key(r[1])))
    return [list(row) for row in ordered]


def _reserved_by_bin(s, site_id, item_id, staged):
    """What live lists at the site still reserve in each bin for this item: unpicked planned units, and (without
    staging) picked ones still on the shelf."""
    lists = [row for row in _current_rows(s, ' AND h.site_id = :site', dict(site=site_id))
             if row['status'] != 'cancelled']
    if not lists:
        return {}
    shipped = {(link['pick_list_id'], link['sales_order_id']) for link in live_links(s, [row['id'] for row in lists])}
    status = {row['id']: row['status'] for row in lists}
    out = {}
    for state in line_states(s, lists).values():
        line = state.line
        if line['item_id'] != item_id or (line['pick_list_id'], line['sales_order_id']) in shipped:
            continue
        if status[line['pick_list_id']] == 'open' and not line['short']:
            out[line['bin_id']] = out.get(line['bin_id'], 0) + max(0, int(line['quantity_microunits']) - state.picked)
        for chunk in state.chunks:
            if not chunk.staging_bin_id:
                out[chunk.bin_id] = out.get(chunk.bin_id, 0) + chunk.quantity
    return out


def _committed_elsewhere(s, site_id, items, order_ids, default):
    """What documents not on the list commit at the site (only when the preference deducts orders), and never less
    than what other live lists there hold, whatever the preference: two lists never take the same units."""
    from bookflow.company import commitments
    out = {}
    if commitments.deducts_documents(s):
        for line in commitments.open_lines(s, items=list(items)):
            if line.document_id in order_ids:
                continue
            if line.site_id == site_id or (line.site_id is None and default):
                out[line.item_id] = out.get(line.item_id, 0) + line.open_base
    for item_id, held in _held_by_others(s, site_id, order_ids).items():
        if item_id in items:
            out[item_id] = max(out.get(item_id, 0), held)
    return out


def _suggest(s, inp, site, date):
    """Every pick line the list will hold, in pick order, and its notes; nothing written."""
    from bookflow.company import bins, serial_lots, shipments, work
    orders, named = _orders(s, inp, site['id'], date)
    orders.sort(key=lambda pair: (pair[1]['date'], _natural(pair[0]['number'])))
    staging_id = site.get('staging_bin_id')
    rows = []
    for header, rev in orders:
        customer = work.facts(rev).profile.customer
        for row in shipments._order_lines(s, header, rev):
            if row['to_ship'] <= 0 or row['line'].get('site_id') not in (None, site['id']):
                continue
            rows.append(dict(order=header, rev=rev, customer=customer.label if customer else None, **row))
    rows.sort(key=lambda r: (r['rev']['date'], _natural(r['order']['number']), r['line']['position']))
    items = {row['facts'].item_id for row in rows}
    held = holds(s, roots={row['root'] for row in rows})
    order_ids = {header['id'] for header, _ in orders}
    on_hand = placements.on_hand(s, date, site_id=site['id'], item_ids=list(items))
    elsewhere = _committed_elsewhere(s, site['id'], items, order_ids, bool(site['is_default']))
    pool = {}
    for item in items:
        pool[item] = on_hand.get((item, site['id']), 0) - elsewhere.get(item, 0) - sum(
            held.get(row['root'], 0) for row in rows if row['facts'].item_id == item)
    places = {item: _place_holdings(s, item, site['id'], date, staging_id,
                                    _reserved_by_bin(s, site['id'], item, staging_id)) for item in items}
    notes, plan, by_order, taken_orders = [], [], {}, []
    for row in rows:
        by_order.setdefault(row['order']['id'], []).append(row)
    limit = inp.limit or len(by_order)
    for header, rev in orders:
        mine = by_order.get(header['id'], [])
        if not mine:
            if named:
                notes.append(f'Sales order {header["number"]} has nothing left to ship from {site["name"]}.')
            continue
        if len(taken_orders) >= limit:
            break
        trial_pool = dict(pool)
        wants = []
        for row in mine:
            base = shipments._to_base(row['to_ship'], row['facts'].unit_factor_nanounits)
            want = base - held.get(row['root'], 0)
            if want <= 0:
                lists = holders(s, row['root'])
                if named and lists:
                    notes.append(f'{row["facts"].profile.item.label} on sales order {header["number"]} is already on '
                                 f'pick list {", ".join(lists)}.')
                continue
            take = max(0, min(want, trial_pool.get(row['facts'].item_id, 0)))
            trial_pool[row['facts'].item_id] = trial_pool.get(row['facts'].item_id, 0) - take
            wants.append((row, take, want - take))
        if not wants:
            continue
        if inp.complete_only and any(short for _, _, short in wants):
            notes.append(f'Sales order {header["number"]} cannot ship whole from {site["name"]} now; left off.')
            continue
        if not named and not any(take for _, take, _ in wants):
            continue
        pool = trial_pool
        taken_orders.append(header)
        for row, take, short in wants:
            item = row['facts'].item_id
            left = take
            for place in places[item]:
                if left <= 0:
                    break
                got = min(place[2], left)
                if got <= 0:
                    continue
                place[2] -= got
                left -= got
                plan.append(dict(row=row, bin_id=place[0], lot_id=place[1], quantity=got, short=False))
            if left > 0:
                plan.append(dict(row=row, bin_id=None, lot_id=None, quantity=left, short=False))
            if short > 0:
                plan.append(dict(row=row, bin_id=None, lot_id=None, quantity=short, short=True))
                other = {k: q for k, q in placements.on_hand(s, date, item_ids=[item]).items()
                         if k[1] != site['id'] and q > 0}
                names = placements.site_names(s)
                where = ', '.join(f'{names.get(k[1], k[1])} {_q(q)}' for k, q in sorted(other.items()))
                notes.append(f'{row["facts"].profile.item.label} on sales order {header["number"]} is short '
                             f'{_q(short)} at {site["name"]}' + (f'; held at {where}.' if where else '.'))
    if not plan:
        raise BookflowError('E_VALIDATION', message=(
            ' '.join(notes) if notes else
            f'Nothing on {"these orders" if named else "any open order"} can be picked at {site["name"]} now.'),
            details=dict(reason='nothing_to_pick', notes=notes))
    names = bins.bin_names(s, [p['bin_id'] for p in plan if p['bin_id']])
    plan.sort(key=lambda p: (bin_key(names.get(p['bin_id'])) if p['bin_id'] else (1,), p['short'],
                             p['row']['facts'].profile.item.label.casefold(), p['row']['rev']['date'],
                             _natural(p['row']['order']['number']), p['row']['line']['position']))
    return plan, notes


def _number(s):
    seq = s.company.conn.execute(sa.select(c.sequences).where(c.sequences.c.name == SEQUENCE)).mappings().first()
    number, prefix = (seq['next_number'], seq['prefix']) if seq else (1, 'P-')
    while s.company.conn.execute(sa.select(c.pick_lists.c.id).where(c.pick_lists.c.number == f'{prefix}{number}')).first():
        number += 1
    return f'{prefix}{number}', dict(name=SEQUENCE, next_number=number + 1, prefix=prefix)


def _site(s, selector):
    site = placements.resolve_site(s, selector) if selector else placements.default_site(s)
    return dict(site)


def _create(s, ctx, inp, *, persist):
    require_enabled(s)
    site = _site(s, inp.site)
    date = inp.date or _today(s)
    plan, notes = _suggest(s, inp, site, date)
    number, sequence = _number(s)
    at = clock.now_iso()
    event = new_id()
    provenance = dict(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value, audit_event_id=event)
    header = dict(id=new_id(), number=number, site_id=site['id'], pick_date=date,
                  picker=(inp.picker or '').strip() or None, memo=(inp.memo or '').strip() or None, **provenance)
    revision = dict(id=new_id(), pick_list_id=header['id'], revision_number=1, status='open',
                    reason=(ctx.reason or '').strip() or None, **provenance)
    lines = [dict(id=new_id(), pick_list_id=header['id'], position=position, sales_order_id=p['row']['order']['id'],
                  order_line_id=p['row']['line']['line_id'], root_document_id=p['row']['root'][0],
                  root_line_id=p['row']['root'][1], item_id=p['row']['facts'].item_id, quantity_microunits=p['quantity'],
                  bin_id=p['bin_id'], lot_id=p['lot_id'], short=p['short'], **provenance)
             for position, p in enumerate(plan, 1)]
    current = dict(header, status='open', revision_number=1, revision_reason=revision['reason'],
                   revision_id=revision['id'])
    if not persist:
        out = output(s, current, lines=lines, entries=[], links=[], history=[revision], status='will_create')
        return out.model_copy(update={'notes': notes}), []
    touched = [Touched('pick_list', header['id'], 'create', None, 1, header, db='company'),
               Touched('pick_list_revision', revision['id'], 'create', None, 1, revision, db='company')]
    touched += [Touched('pick_list_line', line['id'], 'create', None, 1, line, db='company') for line in lines]
    audit.write_event_to(s.company, ctx, 'pick-list create', f'create pick list {number}', touched,
                         actor_id=s.actor.id, actor_kind=s.actor.kind,
                         directive_code=getattr(s, 'directive_code', None), event_id=event)
    conn = s.company.conn
    conn.execute(c.pick_lists.insert().values(**header))
    conn.execute(c.pick_list_revisions.insert().values(**revision))
    conn.execute(c.pick_list_lines.insert(), lines)
    from sqlalchemy.dialects.sqlite import insert
    stmt = insert(c.sequences).values(**sequence)
    conn.execute(stmt.on_conflict_do_update(index_elements=['name'], set_=sequence))
    out = show_output(s, resolve(s, header['id']))
    return out.model_copy(update={'notes': notes, 'next_commands': [
        f'pick-list enter {number}', f'shipment post --pick-list {number}']}), touched


def plan_create(s, ctx, inp) -> Plan:
    out, _ = _create(s, ctx, inp, persist=False)
    return Plan(out, {'input': inp})


def apply_create(plan, ctx, s) -> Applied:
    out, touched = _create(s, ctx, plan.data['input'], persist=True)
    return Applied(out, touched, f'create pick list {out.number}', audited=True)


# ---------------------------------------------------------------- enter

def _require_open(current, verb):
    if current['status'] != 'open':
        raise _invalid('pick_list', f'pick list {current["number"]} is {current["status"]}; only an open list can be '
                                    f'{verb}')


def _resolve_code(s, code, field_name):
    """``(item row, lot text, serial text)`` a scanned code names: an item barcode, else a serial or lot label."""
    from bookflow.company import items as item_service, serial_lots
    text = code.strip()
    rows = [dict(r) for r in s.company.conn.execute(sa.select(c.items.c.id).where(
        c.items.c.barcode == text, c.items.c.active.is_(True))).mappings()]
    if len(rows) == 1:
        return item_service.resolve_item(s.company, rows[0]['id']), None, None
    key = serial_lots.number_key(text)
    for table, kind in ((c.serial_numbers, 'serial'), (c.lots, 'lot')):
        found = [dict(r) for r in s.company.conn.execute(sa.select(table.c.number, table.c.item_id).where(
            table.c.number_key == key)).mappings()]
        if len(found) == 1:
            item = item_service.resolve_item(s.company, found[0]['item_id'])
            return item, (found[0]['number'] if kind == 'lot' else None), (found[0]['number'] if kind == 'serial' else None)
    if rows:
        raise _invalid(field_name, f'the code "{text}" names more than one item; enter the item by name')
    try:
        return item_service.resolve_item(s.company, text), None, None
    except BookflowError:
        raise _invalid(field_name, f'no item, serial number or lot carries the code "{text}"', code=text) from None


def _target(s, current, states, entry, index, item, bin_id, orders):
    """The pick line an entry lands on."""
    prefix = f'entries.{index}.'
    lines = [state.line for state in states.values()]
    if entry.line is not None:
        text = entry.line.strip()
        found = [line for line in lines if str(line['position']) == text or line['id'] == text.upper()]
        if not found:
            raise _invalid(prefix + 'line', f'pick list {current["number"]} has no line {text}')
        return found[0]
    mine = [line for line in lines if line['item_id'] == item['id']]
    if entry.sales_order is not None:
        order = orders.get(entry.sales_order.strip()) or orders.get(entry.sales_order.strip().upper())
        mine = [line for line in mine if line['sales_order_id'] == order] if order else []
    if not mine:
        raise _invalid(prefix + ('code' if entry.code else 'item'),
                       f'{item["full_name"]} is not on pick list {current["number"]}',
                       message=f'{item["full_name"]} is not on pick list {current["number"]}; nothing was added.')
    if entry.sales_order is None and len({line['sales_order_id'] for line in mine}) > 1 and entry.serial_number is None \
            and entry.code is None and entry.mode == 'set':
        raise _invalid(prefix + 'sales_order', f'the list holds {item["full_name"]} for two orders; name the order')

    def unfilled(line):
        state = states[line['id']]
        return state.picked < int(line['quantity_microunits'])
    in_bin = [line for line in mine if line['bin_id'] == bin_id] if bin_id is not None else []
    for group in (in_bin, mine):
        for line in group:
            if not line['short'] and unfilled(line):
                return line
        for line in group:
            if unfilled(line):
                return line
    return (in_bin or mine)[-1]


def _enter(s, ctx, current, inp, *, persist):
    from bookflow.company import bins, serial_lots
    require_enabled(s)
    _require_open(current, 'picked')
    date = inp.date or _today(s)
    site_id = current['site_id']
    staging = staging_bin(s, site_id)
    states = line_states(s, [current])
    orders = {}
    for line in (state.line for state in states.values()):
        header = s.company.conn.execute(sa.select(c.work_documents.c.number).where(
            c.work_documents.c.id == line['sales_order_id'])).scalar()
        orders[header] = line['sales_order_id']
        orders[line['sales_order_id']] = line['sales_order_id']
    at = clock.now_iso()
    event = new_id()
    provenance = dict(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value, audit_event_id=event)
    ordinal = int(s.company.conn.execute(sa.select(sa.func.coalesce(sa.func.max(c.pick_entries.c.ordinal), 0)).where(
        c.pick_entries.c.pick_list_id == current['id'])).scalar_one())
    numbering = serial_lots.Numbering(s)
    sequence = inventory.next_sequence(s)
    position = _position(s, site_id, date)
    entries, made, warnings, touched_lines = [], [], [], []
    others = _picked_serials(s, exclude_list=current['id'])
    for index, entry in enumerate(inp.entries):
        prefix = f'entries.{index}.'
        lot_text, serial_text = entry.lot, entry.serial_number
        if entry.code is not None:
            item, code_lot, code_serial = _resolve_code(s, entry.code, prefix + 'code')
            lot_text, serial_text = lot_text or code_lot, serial_text or code_serial
        elif entry.item is not None:
            from bookflow.company.inventory_counts import resolve_item
            item = resolve_item(s, entry.item, None, prefix + 'item')
        elif entry.line is not None:
            line = next((st.line for st in states.values() if str(st.line['position']) == entry.line.strip()
                         or st.line['id'] == entry.line.strip().upper()), None)
            if line is None:
                raise _invalid(prefix + 'line', f'pick list {current["number"]} has no line {entry.line.strip()}')
            from bookflow.company import items as item_service
            item = item_service.resolve_item(s.company, line['item_id'])
        else:
            raise _invalid(prefix + 'line', 'name the line, the item or a scanned code')
        bin_id = None
        if entry.bin is not None:
            bin_id = None if bins.is_no_bin(entry.bin) else bins.resolve_bin(s, site_id, entry.bin,
                                                                               field=prefix + 'bin')['id']
        line = _target(s, current, states, entry, index, item, bin_id if entry.bin is not None else None, orders)
        if entry.bin is None:
            bin_id = line['bin_id']
        try:
            quantity = parse_quantity_micro_units(entry.quantity, field=prefix + 'quantity') if entry.quantity else UNIT
        except BookflowError:
            raise _invalid(prefix + 'quantity', 'use a decimal quantity of 0 or more') from None
        if quantity < 0:
            raise _invalid(prefix + 'quantity', 'use a decimal quantity of 0 or more')
        kind = numbering.tracking(item['id'])
        lot_id = serial_id = None
        if lot_text is not None and lot_text.strip():
            if kind != 'lot':
                raise _invalid(prefix + 'lot', f'{item["full_name"]} is not tracked by lot')
            lot_id, _ = numbering.number('lot', item['id'], lot_text.strip(), field=prefix + 'lot')
        if serial_text is not None and serial_text.strip():
            if kind != 'serial':
                raise _invalid(prefix + 'serial_number', f'{item["full_name"]} is not tracked by serial number')
            serial_id, new = numbering.number('serial', item['id'], serial_text.strip(), field=prefix + 'serial_number')
            if entry.mode == 'add' or quantity:
                quantity = UNIT
                held = numbering.serial_place(serial_id, (date, 2, 10 ** 18), 1) if new is None else None
                if serial_id in others:
                    raise _invalid(prefix + 'serial_number', f'{serial_text.strip()} is already picked on pick list '
                                   f'{others[serial_id]}', message=(f'{serial_text.strip()} of {item["full_name"]} is '
                                   f'already picked on pick list {others[serial_id]}; put it back there first.'))
                if held is None:
                    if staging:
                        raise _invalid(prefix + 'serial_number', f'the books do not hold {serial_text.strip()} at '
                                       'this site', message=(f'The books hold no {item["full_name"]} '
                                       f'{serial_text.strip()} here, so it cannot move to staging. Count it or post '
                                       'the transfer that brought it, then pick it.'))
                    warnings.append(f'The books hold no {item["full_name"]} {serial_text.strip()}; it was picked as '
                                    'in hand and ships as a sale of an unrecorded number would.')
                elif held[0] != site_id:
                    if staging:
                        raise _invalid(prefix + 'serial_number', f'{serial_text.strip()} is held at '
                                       f'{serial_lots.site_name(s, held[0])}', message=(
                                           f'The books hold {serial_text.strip()} of {item["full_name"]} at '
                                           f'{serial_lots.site_name(s, held[0])}; post the `inventory-transfer` that '
                                           'moved it, then pick it here.'))
                    warnings.append(f'The books hold {serial_text.strip()} of {item["full_name"]} at '
                                    f'{serial_lots.place_name(s, held)}; the unit in hand was picked.')
                elif held[1] != bin_id:
                    warnings.append(f'The books hold {serial_text.strip()} of {item["full_name"]} in '
                                    f'{serial_lots.place_name(s, held)}, not {bins.place_words(s, site_id, bin_id)}.')
                    bin_id = held[1]
        elif kind == 'serial' and quantity % UNIT:
            raise _invalid(prefix + 'quantity', f'{item["full_name"]} is tracked by serial number, so it is picked in '
                                                'whole units')
        elif kind == 'lot' and line['lot_id']:
            lot_id = line['lot_id']   # the picker took the lot the list suggested ("All" sends set, too)
        ordinal += 1
        row = dict(id=new_id(), pick_list_id=current['id'], line_id=line['id'], ordinal=ordinal, mode=entry.mode,
                   quantity_microunits=quantity, bin_id=bin_id, lot_id=lot_id, serial_number_id=serial_id,
                   scanned_code=entry.code.strip() if entry.code else None, staging_bin_id=None, entry_date=date,
                   **provenance)
        state = states[line['id']]
        before = state.picked
        added, removed = _apply_entry(state.chunks, dict(row, staging_bin_id=staging))
        moves = []
        if staging:
            for chunk in added:
                if chunk.bin_id == staging:
                    continue
                moves += [(chunk.bin_id, chunk.lot_id, chunk.serial_id, -chunk.quantity),
                          (staging, chunk.lot_id, chunk.serial_id, chunk.quantity)]
            for chunk in removed:
                if not chunk.staging_bin_id or chunk.bin_id == chunk.staging_bin_id:
                    continue
                moves += [(chunk.staging_bin_id, chunk.lot_id, chunk.serial_id, -chunk.quantity),
                          (chunk.bin_id, chunk.lot_id, chunk.serial_id, chunk.quantity)]
        if moves:
            row['staging_bin_id'] = staging
            for bin_, lot_, serial_, signed in moves:
                made.append(dict(id=new_id(), item_id=item['id'], site_id=site_id, bin_id=bin_, lot_id=lot_,
                                 serial_number_id=serial_, quantity_microunits=signed, effective_date=date,
                                 count_position=position, sequence=sequence, source='pick', movement_id=None,
                                 transfer_revision_id=None, source_line_id=row['id'], reverses_placement_id=None,
                                 **provenance))
            for chunk in added:
                held = bins.available(s, item['id'], site_id, date, position,
                                      lot_id=bins.ANY if kind != 'lot' else chunk.lot_id, made=made)
                if held.get(chunk.bin_id, 0) < 0:
                    warnings.append(f'{bins.place_words(s, site_id, chunk.bin_id).capitalize()} now shows fewer than '
                                    f'none of {item["full_name"]}; count it.')
        elif staging is None:
            for chunk in removed:
                if chunk.staging_bin_id:
                    warnings.append(f'{item["full_name"]} put back from staging: the site no longer names a staging '
                                    'bin, so move the units with an inventory transfer.')
        if state.picked > int(line['quantity_microunits']) and state.picked > before:
            warnings.append(f'{item["full_name"]} on line {line["position"]}: {_q(state.picked)} picked, '
                            f'{_q(line["quantity_microunits"])} to pick; the shipment takes at most what the order '
                            'has to ship.')
        entries.append(row)
        if line['id'] not in touched_lines:
            touched_lines.append(line['id'])
        if serial_id and row['quantity_microunits']:
            others[serial_id] = current['number']
    warnings += numbering.warnings
    shown = output(s, current, states=states, warnings=warnings)
    entered = [line for line in shown.lines if line.line_id in touched_lines]
    shown = shown.model_copy(update={'entered': entered})
    if not persist:
        return shown, []
    made_lots = [dict(row, **_number_provenance(provenance)) for key, row in numbering.new.items() if key[0] == 'lot']
    made_serials = [dict(row, **_number_provenance(provenance)) for key, row in numbering.new.items()
                    if key[0] == 'serial']
    touched = [Touched('lot', row['id'], 'create', None, 1, row, db='company') for row in made_lots]
    touched += [Touched('serial_number', row['id'], 'create', None, 1, row, db='company') for row in made_serials]
    touched += [Touched('inventory_placement', row['id'], 'create', None, 1, row, db='company') for row in made]
    touched += [Touched('pick_entry', row['id'], 'create', None, 1, row, db='company') for row in entries]
    audit.write_event_to(s.company, ctx, 'pick-list enter',
                         f'enter {len(entries)} picks on pick list {current["number"]}', touched,
                         actor_id=s.actor.id, actor_kind=s.actor.kind,
                         directive_code=getattr(s, 'directive_code', None), event_id=event)
    if made_lots:
        s.company.conn.execute(c.lots.insert(), made_lots)
    if made_serials:
        s.company.conn.execute(c.serial_numbers.insert(), made_serials)
    # Placements first: each entry row is refused unless its own placements net to zero.
    if made:
        s.company.conn.execute(c.inventory_placements.insert(), made)
    s.company.conn.execute(c.pick_entries.insert(), entries)
    return shown, touched


def _number_provenance(provenance):
    return dict(created_at=provenance['created_at'], created_by=provenance['created_by'],
                created_via=provenance['created_via'], updated_at=provenance['created_at'],
                updated_by=provenance['created_by'], updated_via=provenance['created_via'])


def _picked_serials(s, *, exclude_list=None):
    """``{serial_number_id: list number}`` units picked on live lists and not yet shipped from them."""
    lists = [row for row in _current_rows(s) if row['status'] != 'cancelled' and row['id'] != exclude_list]
    if not lists:
        return {}
    numbers = {row['id']: row['number'] for row in lists}
    shipped = {(link['pick_list_id'], link['sales_order_id']) for link in live_links(s, list(numbers))}
    out = {}
    for state in line_states(s, lists).values():
        if (state.line['pick_list_id'], state.line['sales_order_id']) in shipped:
            continue
        for chunk in state.chunks:
            if chunk.serial_id:
                out[chunk.serial_id] = numbers[state.line['pick_list_id']]
    return out


def plan_enter(s, ctx, inp) -> Plan:
    out, _ = _enter(s, ctx, resolve(s, inp.pick_list), inp, persist=False)
    return Plan(out, {'input': inp})


def apply_enter(plan, ctx, s) -> Applied:
    inp = plan.data['input']
    current = resolve(s, inp.pick_list)
    out, touched = _enter(s, ctx, current, inp, persist=True)
    return Applied(out, touched, f'enter picks on pick list {current["number"]}', audited=True)


# ---------------------------------------------------------------- close and cancel

def _end(s, ctx, inp, operation, *, persist):
    require_enabled(s)
    current = resolve(s, inp.pick_list)
    if inp.expected_version is not None and inp.expected_version != current['revision_number']:
        raise BookflowError('E_VERSION_CONFLICT', details={'record_id': current['id'],
                                                          'expected_version': inp.expected_version,
                                                          'current_version': current['revision_number']})
    if operation == 'cancel':
        if not ctx.reason or not ctx.reason.strip():
            raise BookflowError('E_REASON_REQUIRED')
        if current['status'] == 'cancelled':
            raise _invalid('pick_list', f'pick list {current["number"]} is already cancelled')
    else:
        _require_open(current, 'closed')
    status = 'done' if operation == 'close' else 'cancelled'
    at = clock.now_iso()
    event = new_id()
    provenance = dict(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value, audit_event_id=event)
    revision = dict(id=new_id(), pick_list_id=current['id'], revision_number=current['revision_number'] + 1,
                    status=status, reason=(ctx.reason or '').strip() or None, **provenance)
    made, warnings = [], []
    states = line_states(s, [current])
    if operation == 'cancel':
        latest = max((row['entry_date'] for row in _entries(s, [current['id']])), default=current['pick_date'])
        date = max(getattr(inp, 'date', None) or _today(s), latest)
        position = _position(s, current['site_id'], date)
        sequence = inventory.next_sequence(s)
        for line, chunk in staged_left(s, current, states):
            for bin_, signed in ((chunk.staging_bin_id, -chunk.quantity), (chunk.bin_id, chunk.quantity)):
                made.append(dict(id=new_id(), item_id=line['item_id'], site_id=current['site_id'],
                                 bin_id=bin_, lot_id=chunk.lot_id, serial_number_id=chunk.serial_id,
                                 quantity_microunits=signed, effective_date=date, count_position=position,
                                 sequence=sequence, source='pick', movement_id=None, transfer_revision_id=None,
                                 source_line_id=revision['id'], reverses_placement_id=None, **provenance))
        if made:
            warnings.append(f'Staged units not shipped go back to the bins they were picked from on {date}.')
    else:
        short = [state for state in states.values() if state.picked < int(state.line['quantity_microunits'])]
        for state in short:
            warnings.append(f'Line {state.line["position"]} ({inventory.item_name(s, state.line["item_id"])}) picked '
                            f'{_q(state.picked)} of {_q(state.line["quantity_microunits"])}; the rest stays to ship '
                            'on the order.')
    after = dict(current, status=status, revision_number=revision['revision_number'], revision_id=revision['id'],
                 revision_reason=revision['reason'])
    if not persist:
        return output(s, after, states=states, warnings=warnings, status=f'will_{operation}'), []
    touched = [Touched('inventory_placement', row['id'], 'create', None, 1, row, db='company') for row in made]
    touched.append(Touched('pick_list_revision', revision['id'], 'create', None, 1, revision, db='company'))
    audit.write_event_to(s.company, ctx, 'pick-list ' + operation, f'{operation} pick list {current["number"]}',
                         touched, actor_id=s.actor.id, actor_kind=s.actor.kind,
                         directive_code=getattr(s, 'directive_code', None), event_id=event)
    if made:
        s.company.conn.execute(c.inventory_placements.insert(), made)
    s.company.conn.execute(c.pick_list_revisions.insert().values(**revision))
    out = show_output(s, resolve(s, current['id']))
    return out.model_copy(update={'warnings': warnings}), touched


def staged_left(s, current, states=None):
    """``[(line, Chunk)]`` units the list moved to staging that no live shipment from it took: every staged chunk of
    an order not shipped from the list, and of a shipped order what its shipment did not issue from staging (picked
    beyond what the order had to ship). Cancelling the list moves these back to the bins they came from."""
    if states is None:
        states = line_states(s, [current])
    links = live_links(s, [current['id']])
    shipped = {link['sales_order_id'] for link in links}
    taken = {}
    if links:
        params = {}
        marks = []
        for index, link in enumerate(links):
            params[f's{index}'] = link['shipment_id']
            marks.append(f':s{index}')
        for row in s.company.conn.execute(sa.text(
                "SELECT h.sales_order_id, p.item_id, p.bin_id, p.lot_id, p.serial_number_id, "
                "sum(p.quantity_microunits) AS q FROM inventory_placements p "
                "JOIN inventory_movements m ON m.id = p.movement_id JOIN shipments h ON h.transaction_id = "
                f"m.transaction_id WHERE h.id IN ({', '.join(marks)}) GROUP BY 1, 2, 3, 4, 5"), params).mappings():
            key = (row['sales_order_id'], row['item_id'], row['bin_id'], row['lot_id'], row['serial_number_id'])
            taken[key] = taken.get(key, 0) - int(row['q'])
    out = []
    for state in sorted(states.values(), key=lambda st: st.line['position']):
        line = state.line
        for chunk in state.chunks:
            if not chunk.staging_bin_id or chunk.bin_id == chunk.staging_bin_id:
                continue
            quantity = chunk.quantity
            if line['sales_order_id'] in shipped:
                key = (line['sales_order_id'], line['item_id'], chunk.staging_bin_id, chunk.lot_id, chunk.serial_id)
                used = max(0, min(quantity, taken.get(key, 0)))
                taken[key] = taken.get(key, 0) - used
                quantity -= used
            if quantity > 0:
                out.append((line, Chunk(chunk.bin_id, chunk.lot_id, chunk.serial_id, quantity, chunk.staging_bin_id)))
    return out


def plan_end(operation):
    def planner(s, ctx, inp) -> Plan:
        out, _ = _end(s, ctx, inp, operation, persist=False)
        return Plan(out, {'input': inp, 'operation': operation})
    return planner


def apply_end(plan, ctx, s) -> Applied:
    operation = plan.data['operation']
    out, touched = _end(s, ctx, plan.data['input'], operation, persist=True)
    return Applied(out, touched, f'{operation} pick list {out.number}', audited=True)


# ---------------------------------------------------------------- output

def output(s, current, *, lines=None, entries=None, links=None, history=None, states=None, warnings=(),
           status=None):
    from bookflow.company import bins, serial_lots, work
    if states is None:
        if lines is None:
            states = line_states(s, [current])
        else:
            states = {line['id']: LineState(line) for line in lines}
            for entry in entries or ():
                if entry['line_id'] in states:
                    _apply_entry(states[entry['line_id']].chunks, entry)
    links = live_links(s, [current['id']]) if links is None and current.get('id') and status is None else (links or [])
    rows = sorted(states.values(), key=lambda st: st.line['position'])
    bin_ids = {st.line['bin_id'] for st in rows if st.line['bin_id']}
    lot_ids = {st.line['lot_id'] for st in rows if st.line['lot_id']}
    serial_ids, picked_lots = set(), set()
    for st in rows:
        for chunk in st.chunks:
            serial_ids.add(chunk.serial_id) if chunk.serial_id else None
            picked_lots.add(chunk.lot_id) if chunk.lot_id else None
    names = bins.bin_names(s, bin_ids)
    numbers = serial_lots.number_names(s, lot_ids=list(lot_ids | picked_lots), serial_ids=list(serial_ids))
    order_rows = {}
    for st in rows:
        if st.line['sales_order_id'] not in order_rows:
            header = work.resolve(s, st.line['sales_order_id'], 'sales_order')
            customer = work.facts(work.revision(s, header)).profile.customer
            order_rows[header['id']] = dict(number=header['number'], customer=customer.label if customer else None)
    hints = {}
    for st in rows:
        if st.line['bin_id'] and serial_lots.tracking(s, st.line['item_id']) == 'serial':
            key = (st.line['item_id'], st.line['bin_id'])
            if key not in hints:
                held = bins.held(s, current['pick_date'], site_id=current['site_id'], bin_ids=[st.line['bin_id']],
                                 item_ids=[st.line['item_id']])
                ids = [k[4] for k, q in held.items() if k[4] and q > 0]
                found = serial_lots.number_names(s, serial_ids=ids)
                hints[key] = sorted((found[i]['number'] for i in ids), key=serial_lots.number_key)
    out_lines = []
    for st in rows:
        line = st.line
        lot = numbers.get(line['lot_id']) if line['lot_id'] else None
        quantity = int(line['quantity_microunits'])
        order = order_rows[line['sales_order_id']]
        out_lines.append(PickLineOutput(
            line_id=line['id'], position=line['position'], sales_order_id=line['sales_order_id'],
            sales_order=order['number'], customer=order['customer'], order_line_id=line['order_line_id'],
            item_id=line['item_id'], item_name=inventory.item_name(s, line['item_id']), quantity=_q(quantity),
            picked=_q(st.picked), to_pick=_q(max(0, quantity - st.picked)), short=bool(line['short']),
            over=st.picked > quantity, bin=names.get(line['bin_id']) if line['bin_id'] else None,
            lot=lot['number'] if lot else None, lot_expiry=lot.get('expiry_date') if lot else None,
            serial_hints=hints.get((line['item_id'], line['bin_id']), []),
            picked_serial_numbers=[numbers[ch.serial_id]['number'] for ch in st.chunks if ch.serial_id],
            picked_lots=sorted({numbers[ch.lot_id]['number'] for ch in st.chunks if ch.lot_id})))
    shipped = {}
    for link in links:
        shipped.setdefault(link['sales_order_id'], []).append(link['number'])
    orders = [PickOrderOutput(sales_order_id=order_id, sales_order=row['number'], customer=row['customer'],
                              shipments=sorted(shipped.get(order_id, [])))
              for order_id, row in order_rows.items()]
    staging = staging_bin(s, current['site_id'])
    history_rows = history if history is not None else (_history(s, current['id']) if current.get('id') else [])
    return PickListOutput(
        id=current.get('id') if status != 'will_create' else None, number=current['number'],
        version=current['revision_number'], status=status or current['status'], site_id=current['site_id'],
        site_name=placements.site_names(s).get(current['site_id'], ''), date=current['pick_date'],
        picker=current.get('picker'), memo=current.get('memo'),
        staging_bin=bins.bin_names(s, [staging]).get(staging) if staging else None, lines=out_lines, orders=orders,
        warnings=list(dict.fromkeys(warnings)),
        history=[PickHistoryOutput(version=r['revision_number'], status=r['status'], reason=r['reason'],
                                   created_at=r['created_at'], created_by=r['created_by']) for r in history_rows])


def _history(s, list_id):
    t = c.pick_list_revisions
    return [dict(row) for row in s.company.conn.execute(
        sa.select(t).where(t.c.pick_list_id == list_id).order_by(t.c.revision_number)).mappings()]


def show_output(s, current):
    out = output(s, current)
    nexts = []
    if current['status'] != 'cancelled':
        shipped = {order.sales_order_id for order in out.orders if order.shipments}
        waiting = [order for order in out.orders if order.sales_order_id not in shipped]
        if current['status'] == 'open':
            nexts.append(f'pick-list enter {current["number"]}')
        for order in waiting:
            nexts.append(f'shipment post --pick-list {current["number"]}'
                         + (f' --sales-order {order.sales_order}' if len(out.orders) > 1 else ''))
    return out.model_copy(update={'next_commands': nexts})


def show(s, inp):
    require_enabled(s)
    return show_output(s, resolve(s, inp.pick_list))


def query(s, inp):
    require_enabled(s)
    where, params = '', {}
    if inp.status:
        where += ' AND r.status = :status'
        params['status'] = inp.status
    if inp.site:
        where += ' AND h.site_id = :site'
        params['site'] = placements.site_id_for(s, inp.site)
    rows = _current_rows(s, where, params)
    if inp.sales_order:
        from bookflow.company import work
        order = work.resolve(s, inp.sales_order, 'sales_order')
        having = {line['pick_list_id'] for line in _lines(s, [row['id'] for row in rows])
                  if line['sales_order_id'] == order['id']}
        rows = [row for row in rows if row['id'] in having]
    rows.sort(key=lambda row: (row['pick_date'], row['created_at']), reverse=True)
    total = len(rows)
    rows = rows[:inp.limit]
    states = line_states(s, rows)
    by_list = {}
    for state in states.values():
        by_list.setdefault(state.line['pick_list_id'], []).append(state)
    numbers = {}
    sites = placements.site_names(s)
    out = []
    for row in rows:
        mine = by_list.get(row['id'], [])
        order_ids = list(dict.fromkeys(st.line['sales_order_id'] for st in mine))
        for order_id in order_ids:
            if order_id not in numbers:
                numbers[order_id] = s.company.conn.execute(sa.select(c.work_documents.c.number).where(
                    c.work_documents.c.id == order_id)).scalar()
        out.append(PickListRow(id=row['id'], number=row['number'], status=row['status'],
                               site_name=sites.get(row['site_id'], ''), date=row['pick_date'], picker=row['picker'],
                               orders=[numbers[o] for o in order_ids], lines=len(mine),
                               picked_lines=sum(1 for st in mine if st.picked >= int(st.line['quantity_microunits']))))
    return PickListPageOutput(rows=out, total=total)


# ---------------------------------------------------------------- shipping from a list

def shipping_plan(s, list_selector, order_selector, site_selector):
    """What `shipment post --pick-list` ships: ``(current, order id, [(root, base, bin_id, lot_id, [serial ids])],
    notes)`` -- one part per order line, bin and lot the picks came from (staging when staged)."""
    require_enabled(s)
    current = resolve(s, list_selector)
    if current['status'] == 'cancelled':
        raise _invalid('pick_list', f'pick list {current["number"]} is cancelled')
    states = line_states(s, [current])
    order_ids = list(dict.fromkeys(st.line['sales_order_id'] for st in sorted(states.values(),
                                                                                key=lambda st: st.line['position'])))
    links = {link['sales_order_id']: link['number'] for link in live_links(s, [current['id']])}
    if order_selector is not None:
        from bookflow.company import work
        order = work.resolve(s, order_selector, 'sales_order')
        if order['id'] not in order_ids:
            raise _invalid('sales_order', f'sales order {order["number"]} is not on pick list {current["number"]}')
        order_id = order['id']
    else:
        waiting = [o for o in order_ids if o not in links]
        if len(order_ids) > 1:
            if len(waiting) != 1:
                raise _invalid('sales_order', f'pick list {current["number"]} covers {len(order_ids)} orders; name '
                                              'the sales_order to ship')
            order_id = waiting[0]
        else:
            order_id = order_ids[0]
    if order_id in links:
        raise BookflowError('E_WORK_DEPENDENCY', message=(
            f'This order already shipped from pick list {current["number"]} as shipment {links[order_id]}.'),
            details=dict(problem='already shipped from this pick list', shipment=links[order_id],
                         reason='pick_list_shipped'))
    if site_selector is not None and placements.site_id_for(s, site_selector) != current['site_id']:
        raise _invalid('site', f'pick list {current["number"]} was picked at '
                               f'{placements.site_names(s).get(current["site_id"])}')
    parts = {}
    for state in sorted(states.values(), key=lambda st: st.line['position']):
        line = state.line
        if line['sales_order_id'] != order_id:
            continue
        root = (line['root_document_id'], line['root_line_id'])
        for chunk in state.chunks:
            place = chunk.staging_bin_id or chunk.bin_id
            key = (root, place, chunk.lot_id)
            part = parts.setdefault(key, dict(root=root, base=0, bin_id=place, lot_id=chunk.lot_id, serials=[]))
            part['base'] += chunk.quantity
            if chunk.serial_id:
                part['serials'].append(chunk.serial_id)
    if not parts:
        raise _invalid('pick_list', f'nothing is picked for this order on pick list {current["number"]}',
                       message=(f'Nothing is picked for this order on pick list {current["number"]} yet; enter the '
                                'picks first, or ship from the order without the list.'))
    return current, order_id, list(parts.values())


def link_rows(s, current, shipment_id, order_id, provenance):
    """The rows a shipment from the list writes: its link, and ``done`` when every order has shipped from it."""
    rows = [('pick_list_shipments', 'pick_list_shipment', 'id', [dict(
        id=new_id(), pick_list_id=current['id'], shipment_id=shipment_id, sales_order_id=order_id, **provenance)])]
    if current['status'] == 'open':
        order_ids = {line['sales_order_id'] for line in _lines(s, [current['id']])}
        shipped = {link['sales_order_id'] for link in live_links(s, [current['id']])} | {order_id}
        if order_ids <= shipped:
            rows.append(('pick_list_revisions', 'pick_list_revision', 'id', [dict(
                id=new_id(), pick_list_id=current['id'], revision_number=current['revision_number'] + 1,
                status='done', reason='Every order on the list shipped', **provenance)]))
    return rows


# ---------------------------------------------------------------- counts

def picked_in_place(s, site_id, items=None, bin_ids=None):
    """Words for units picked and not shipped that still sit in their bins (no staging), for a count's create
    result and post preview: "3 Widget picked for sales order 1001, list P-4, not shipped"."""
    if not s.company.conn.execute(sa.text("SELECT 1 FROM pick_lists LIMIT 1")).first():
        return []
    lists = [row for row in _current_rows(s, ' AND h.site_id = :site', dict(site=site_id))
             if row['status'] != 'cancelled']
    if not lists:
        return []
    from bookflow.company import bins
    numbers = {row['id']: row['number'] for row in lists}
    shipped = {(link['pick_list_id'], link['sales_order_id']) for link in live_links(s, list(numbers))}
    totals = {}
    for state in line_states(s, lists).values():
        line = state.line
        if (line['pick_list_id'], line['sales_order_id']) in shipped:
            continue
        if items is not None and line['item_id'] not in items:
            continue
        for chunk in state.chunks:
            if chunk.staging_bin_id or (bin_ids is not None and chunk.bin_id not in bin_ids):
                continue
            key = (line['item_id'], line['sales_order_id'], line['pick_list_id'], chunk.bin_id)
            totals[key] = totals.get(key, 0) + chunk.quantity
    out = []
    for (item_id, order_id, list_id, bin_id), quantity in sorted(totals.items()):
        order = s.company.conn.execute(sa.select(c.work_documents.c.number).where(
            c.work_documents.c.id == order_id)).scalar()
        out.append(f'{_q(quantity)} {inventory.item_name(s, item_id)} picked for sales order {order}, list '
                   f'{numbers[list_id]}, not shipped' + (f' (bin {bins.bin_names(s, [bin_id])[bin_id]})' if bin_id else '')
                   + '.')
    return out
