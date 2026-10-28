"""The fulfillment worksheet and the shipments report (R181 increment 5, design/pick-pack-ship.md §2, §8).

`report order-fulfillment` is the anchor's Sales Order Fulfillment Worksheet: every open, active sales order with
something stocked to ship or shipped and not invoiced, its fulfillment status beside its billing progress, and what
can ship now. **Can ship now is what `shipment post --sales-order <n>` would ship**, worked out by the same steps
`shipments._plan` takes with no lines named: each order line's to-ship (`shipments._order_lines`), less what live
drafts hold, at the site `shipment post` picks (or the one asked for), taken in line order from what is available to
the order (on hand less what every other document commits, as `commitments.available_to` reads it). An order a live
pick list holds ships from the list, so `shipment post` from the order alone is refused and its can-ship-now is zero,
with a note naming the list. Availability is read once for the whole company, not once per order.

Fulfillment status (§2): `shipped` when nothing is left to ship; else, while picking and packing are on, `packed` (a
live draft with every unit in a box), `picking` (a live open list still has units to pick for it), `picked` (picked
units waiting to ship); else `partly_shipped` once something shipped, and `to_pick` before that.

`report shipments` lists posted shipments in a date range with boxes, weights and tracking numbers.
"""
from __future__ import annotations

import json
from datetime import date as calendar_date
from fractions import Fraction

import sqlalchemy as sa

from bookflow.company import ledger_reports as ledger, placements, schema as c
from bookflow.company.fulfillment_models import (
    STATUSES, FulfillmentLine, OrderFulfillmentOutput, OrderFulfillmentRow, OrderFulfillmentTotals, ShipmentsReportBox,
    ShipmentsReportOutput, ShipmentsReportRow, ShipmentsReportTotals, SiteShippable,
)
from bookflow.company.ledger_reports import money
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_quantity_micro_units

NANO = 1_000_000_000
UNIT = 1_000_000


def _q(micro):
    return format_quantity_micro_units(int(micro))


def _length(f, base):
    """``base`` units of a line as a length of its billing span (`billing_math.denominator` coordinates)."""
    if not base or not f['facts'].quantity_microunits:
        return 0
    return round(Fraction(f['d'] * int(base) * NANO, int(f['factor']) * int(f['facts'].quantity_microunits)))


def _billed(f, skip, base):
    """What the next invoice bills for ``base`` units of a line after the first ``skip`` units still to bill: the
    invoice takes the line's free span from its start and prices each piece by the difference of rounded cumulative
    endpoints (`billing_math.portion`), so these cents are the invoice's own, never a per-line rounding."""
    from bookflow.company import billing_math as math
    skip, take = _length(f, skip), _length(f, base)
    amount = 0
    for start, end in f['spans']:
        if take <= 0:
            break
        width = end - start
        if skip >= width:
            skip -= width
            continue
        start, skip = start + skip, 0
        stop = min(end, start + take)
        amount += math.portion(int(f['facts'].net_minor_units), start, stop, f['d'])
        take -= stop - start
    return amount


def _natural(text):
    from bookflow.company.pick_lists import _natural as natural
    return natural(str(text))


def _invalid(field_name, problem):
    return BookflowError('E_VALIDATION', details={'fields': [{'field': field_name, 'problem': problem}]})


# ---------------------------------------------------------------- what holds an order's units


def _picks(s):
    """``{root: dict(held, picked, unpicked, lists)}`` live pick lists hold, while picking is on (as
    `pick_lists.holding` reads them: an open list holds max(planned, picked), a done one what was picked; nothing once
    the order shipped from the list)."""
    from bookflow.company import pick_lists
    if not pick_lists.enabled(s):
        return {}
    lists = [row for row in pick_lists._current_rows(s) if row['status'] != 'cancelled']
    if not lists:
        return {}
    by_id = {row['id']: row for row in lists}
    shipped = {(link['pick_list_id'], link['sales_order_id']) for link in pick_lists.live_links(s, list(by_id))}
    planned, picked = {}, {}
    for state in pick_lists.line_states(s, lists).values():
        line = state.line
        if (line['pick_list_id'], line['sales_order_id']) in shipped:
            continue
        key = (line['pick_list_id'], (line['root_document_id'], line['root_line_id']))
        planned[key] = planned.get(key, 0) + (0 if line['short'] else int(line['quantity_microunits']))
        picked[key] = picked.get(key, 0) + state.picked
    out = {}
    for key in planned:
        list_id, root = key
        is_open = by_id[list_id]['status'] == 'open'
        held = max(planned[key], picked[key]) if is_open else picked[key]
        if not held:
            continue
        found = out.setdefault(root, dict(held=0, picked=0, unpicked=0, lists=set()))
        found['held'] += held
        found['picked'] += picked[key]
        found['unpicked'] += max(planned[key] - picked[key], 0) if is_open else 0
        found['lists'].add(by_id[list_id]['number'])
    return out


def _drafts(s):
    """``(by_root, by_order)``: what live drafts hold and have boxed per order line, and per order its draft numbers
    and whether one is wholly packed."""
    from bookflow.company import shipment_packing as packing
    drafts = [row for row in packing._current_drafts(s) if row['status'] == 'draft']
    by_root, by_order = {}, {}
    for draft in drafts:
        lines = packing.draft_lines(s, draft['id'])
        boxed = {}
        for box in packing.load(s, draft['revision_id']):
            for identity, (quantity, _) in box['contents'].items():
                boxed[identity] = boxed.get(identity, 0) + int(quantity)
        whole = bool(lines)
        for line in lines:
            root = (line['root_document_id'], line['root_line_id'])
            quantity = int(line['quantity_microunits'])
            packed = min(boxed.get(line['line_identity'], 0), quantity)
            whole = whole and packed >= quantity
            found = by_root.setdefault(root, [0, 0])
            found[0] += quantity
            found[1] += packed
        order = by_order.setdefault(draft['sales_order_id'], dict(numbers=[], packed=False, packed_numbers=[]))
        order['numbers'].append(draft['number'])
        order['packed'] = order['packed'] or whole
        if whole:
            order['packed_numbers'].append(draft['number'])
    return by_root, by_order


class _Stock:
    """Available to each order, read once: on hand less what every other document commits and pending builds
    reserve, as `commitments.available_to` gives it (on hand alone while the preferences are off)."""

    def __init__(self, s, items, as_of=None):
        from bookflow.company import commitments
        self.on_hand = commitments.on_hand(s, items, as_of) if items else {}
        self.value = {}
        if items:
            m = c.inventory_movements
            query = sa.select(m.c.item_id, sa.func.sum(m.c.value_minor_units)).where(
                m.c.item_id.in_(sorted(items))).group_by(m.c.item_id)
            if as_of is not None:
                query = query.where(m.c.effective_date <= as_of)
            self.value = {item: int(value or 0) for item, value in s.company.conn.execute(query)}
        self.deducts = commitments.deducts_documents(s)
        self.builds = commitments.for_assemblies(s) if commitments.deducts_builds(s) else {}
        self.total, self.own = {}, {}
        if self.deducts and items:
            for line in commitments.open_lines(s, items=sorted(items)):
                self.total[line.item_id] = self.total.get(line.item_id, 0) + line.open_base
                key = (line.document_id, line.item_id)
                self.own[key] = self.own.get(key, 0) + line.open_base

    def to(self, order_id, items):
        return {item: self.on_hand.get(item, 0)
                - ((self.total.get(item, 0) - self.own.get((order_id, item), 0)) if self.deducts else 0)
                - self.builds.get(item, 0) for item in items}


def _ship_now(lines, order_id, site_id, stock):
    """``{root: base}`` `shipment post` ships from the order alone at this site: `shipments._plan`'s steps. Units
    worth nothing in the books ship nothing: `shipment post` refuses a shipment with no cost to hold
    (``shipment_without_value``), so an order whose every unit it would ship is of an item carried at no value reads
    ``{}`` (`_worthless`)."""
    found = _plan_now(lines, order_id, site_id, stock)
    return {} if _worthless(found, lines, stock) else found


def _worthless(now, lines, stock):
    """Whether every unit ``now`` ships is of an item whose stock is carried at no value, so `shipment post` would
    post no cost and refuse it (``shipment_without_value``)."""
    if not now:
        return False
    items = {line['facts'].item_id for line in lines if line['root'] in now}
    return all(stock.value.get(item, 0) == 0 for item in items)


def _plan_now(lines, order_id, site_id, stock):
    from bookflow.company.shipments import _to_base
    eligible = [line for line in lines if line['left'] > 0 and line['line'].get('site_id') in (None, site_id)]
    left = stock.to(order_id, {line['facts'].item_id for line in eligible})
    out = {}
    for line in eligible:
        item = line['facts'].item_id
        base = min(_to_base(line['left'], line['facts'].unit_factor_nanounits), max(0, left.get(item, 0)))
        if base <= 0:
            continue
        left[item] = left.get(item, 0) - base
        out[line['root']] = out.get(line['root'], 0) + base
    return out


# ---------------------------------------------------------------- the worksheet


def _open_orders(s, customer_id):
    where = "d.kind = 'sales_order' AND d.status = 'open' AND d.active = 1"
    params = {}
    if customer_id is not None:
        where += ' AND r.customer_id = :customer'
        params['customer'] = customer_id
    return [dict(row) for row in s.company.conn.execute(sa.text(
        f"""SELECT d.*, r.date AS order_date, r.customer_id, r.facts_snapshot AS order_facts, cu.full_name AS customer_name
            FROM work_documents d JOIN work_revisions r ON r.id = d.current_revision_id
            JOIN customers cu ON cu.id = r.customer_id WHERE {where}"""), params).mappings()]


def _shipped_from(s):
    """``{order id: {site ids}}`` its posted shipments left from."""
    from bookflow.company import shipments
    if not shipments.has_shipments(s):
        return {}
    out = {}
    for order, site in s.company.conn.execute(sa.text(
            f"SELECT h.sales_order_id, r.site_id FROM shipments h JOIN ({shipments._CURRENT}) r "
            "ON r.shipment_id = h.id WHERE r.status = 'shipped'")).all():
        out.setdefault(order, set()).add(site)
    return out


def worksheet(s, inp, *, principal_id=None):
    with ledger._snapshot(s.company):
        return _worksheet(s, inp, principal_id)


def _worksheet(s, inp, principal_id):
    from bookflow.company import (
        billing_allocations as alloc, billing_math as math, commitments, pick_lists, sales_defaults, shipments,
        tax_policy, work,
    )
    from bookflow.company.shipments import _to_base
    statuses = None
    if inp.status is not None:
        statuses = [part.strip() for part in inp.status.split(',') if part.strip()]
        if not statuses or any(part not in STATUSES for part in statuses):
            raise _invalid('status', 'name statuses from ' + ', '.join(STATUSES) + ', comma-separated')
    site_id = placements.site_id_for(s, inp.site) if inp.site is not None else None
    customer_id = sales_defaults._row(s.company, 'customer', inp.customer, active=False)['id'] \
        if inp.customer is not None else None
    currency = shipments._home(s)
    picking = pick_lists.enabled(s)
    candidates = {line.document_id for line in commitments.open_lines(s, kinds=('sales_order',))}
    orders = [row for row in _open_orders(s, customer_id) if row['id'] in candidates]
    picks = _picks(s)
    drafted, draft_orders = _drafts(s)
    parts = shipments.waiting_parts(s, order_ids=None) if shipments.has_shipments(s) else {}
    shipped_from = _shipped_from(s) if site_id is not None else {}
    names = placements.site_names(s)
    multi = placements.multi_site(s)
    # Every order's lines first, so availability is read once for every item they carry.
    loaded = []
    items = set()
    for header in orders:
        rev = work.revision(s, header)
        lines = shipments._order_lines(s, header, rev)
        for line in lines:
            held = drafted.get(line['root'], (0, 0))[0]
            line['left'] = max(line['to_ship'] - shipments._to_selected(held, line['facts'].unit_factor_nanounits),
                               Fraction(0)) if held else line['to_ship']
            items.add(line['facts'].item_id)
        loaded.append((header, rev, lines))
    stock = _Stock(s, items)
    before = _Stock(s, items, inp.newly_shippable) if inp.newly_shippable else None
    found = []
    for header, rev, lines in loaded:
        figures = []
        policy = tax_policy.effective(work.facts(rev).profile) if lines else None
        for line in lines:
            facts = line['facts']
            factor = facts.unit_factor_nanounits
            out = [p for p in parts.get(line['root'], ())]
            figures.append(dict(
                line=line, facts=facts, factor=factor, root=line['root'],
                spans=tuple(alloc.free_spans(s, line['root'], facts, policy=policy)),
                d=math.denominator(facts.quantity_microunits, facts.net_minor_units),
                ordered=_to_base(facts.quantity_microunits, factor), to_ship=_to_base(line['to_ship'], factor),
                shipped=sum(p.out for p in out), waiting=sum(p.waiting for p in out),
                drafted=drafted.get(line['root'], (0, 0))[0], packed=drafted.get(line['root'], (0, 0))[1],
                pick=picks.get(line['root'])))
        to_ship = sum(f['to_ship'] for f in figures)
        waiting = sum(f['waiting'] for f in figures)
        if not to_ship and not waiting:
            continue
        here = site_id if site_id is not None else shipments._site(s, None, lines)
        if site_id is not None:
            ships_here = any(f['to_ship'] > 0 and f['line']['line'].get('site_id') in (None, site_id) for f in figures)
            if not ships_here and not (waiting and site_id in shipped_from.get(header['id'], ())):
                continue
        planned = _plan_now(lines, header['id'], here, stock)
        worthless = _worthless(planned, lines, stock)
        now = {} if worthless else planned
        held = {f['root']: f['pick'] for f in figures if f['pick']}
        refused = sorted({n for root in planned if root in held for n in held[root]['lists']}, key=_natural)
        if refused:
            now = {}
        if before is not None and not refused:
            then = _ship_now(lines, header['id'], here, before)
            newly = any(base > then.get(root, 0) for root, base in now.items())
        else:
            newly = False
        complete = bool(to_ship) and not refused and all(
            f['to_ship'] <= 0 or (f['line']['line'].get('site_id') in (None, here)
                                  and now.get(f['root'], 0) + f['drafted'] >= f['to_ship']) for f in figures)
        order_drafts = draft_orders.get(header['id'], dict(numbers=[], packed=False, packed_numbers=[]))
        lists = sorted({n for f in figures if f['pick'] for n in f['pick']['lists']}, key=_natural)
        if not to_ship:
            status = 'shipped'
        elif picking and order_drafts['packed']:
            status = 'packed'
        elif picking and any(f['pick'] and f['pick']['unpicked'] for f in figures):
            status = 'picking'
        elif picking and any(f['pick'] and f['pick']['picked'] for f in figures):
            status = 'picked'
        elif any(f['shipped'] for f in figures):
            status = 'partly_shipped'
        else:
            status = 'to_pick'
        shippable = sum(_billed(f, f['waiting'], now.get(f['root'], 0)) for f in figures)
        if statuses is not None and status not in statuses:
            continue
        if inp.complete_only and not complete:
            continue
        if inp.can_ship and not now:
            continue
        if inp.shipped_not_invoiced and not waiting:
            continue
        if inp.newly_shippable and not newly:
            continue
        profile = json.loads(header['order_facts']).get('profile', {})
        found.append(dict(header=header, rev=rev, figures=figures, now=now, here=here, refused=refused,
                          worthless=worthless and not refused, complete=complete, status=status, shippable=shippable, lists=lists,
                          drafts=sorted(order_drafts['numbers'], key=_natural),
                          packed_drafts=sorted(order_drafts['packed_numbers'], key=_natural),
                          promised=profile.get('ship_date'),
                          to_ship_amount=sum(_billed(f, f['waiting'], f['to_ship']) for f in figures),
                          waiting_amount=sum(_billed(f, 0, f['waiting']) for f in figures),
                          sites=_by_site(s, lines, header, here, site_id, stock, figures, names) if multi else []))
    if inp.sort == 'shippable_amount':
        found.sort(key=lambda o: (-o['shippable'], o['header']['order_date'], _natural(o['header']['number'])))
    elif inp.sort == 'customer':
        found.sort(key=lambda o: (o['header']['customer_name'].casefold(), o['header']['order_date'],
                                  _natural(o['header']['number'])))
    else:
        found.sort(key=lambda o: (o['header']['order_date'], _natural(o['header']['number'])))
    page = ledger.page_of(s, inp, 'order-fulfillment', found, date_to=inp.as_of, principal_id=principal_id)
    items_named = dict(s.company.conn.execute(sa.text('SELECT id, full_name FROM items')).all())
    rows = [_row(s, order, inp.as_of, currency, names, items_named) for order in page['rows']]
    return OrderFulfillmentOutput(**dict(page, rows=rows), as_of=inp.as_of, sort=inp.sort, pick_and_pack=picking,
                                  totals=OrderFulfillmentTotals(
                                      orders=len(found),
                                      to_ship_amount=money(sum(o['to_ship_amount'] for o in found), currency),
                                      shippable_amount=money(sum(o['shippable'] for o in found), currency),
                                      shipped_not_invoiced_amount=money(sum(o['waiting_amount'] for o in found),
                                                                        currency)))


def _by_site(s, lines, header, here, site_id, stock, figures, names):
    """Can ship now from each site the order could ship from (`shipment post --site`): the row's site, every site a
    line still to ship names, and the default site when a line names none."""
    if site_id is not None:
        sites = [site_id]
    else:
        named = {f['line']['line'].get('site_id') for f in figures if f['to_ship'] > 0}
        if None in named:
            named.discard(None)
            named.add(placements.default_site_id(s))
        sites = list(dict.fromkeys([here] + sorted(named, key=lambda i: names.get(i, i))))
    out = []
    for site in sites:
        now = _ship_now(lines, header['id'], site, stock)
        out.append(dict(site=site, lines=len(now), amount=sum(_billed(f, f['waiting'], now.get(f['root'], 0))
                                                              for f in figures), complete=all(f['to_ship'] <= 0 or (
                f['line']['line'].get('site_id') in (None, site) and now.get(f['root'], 0) + f['drafted'] >= f['to_ship'])
                for f in figures)))
    return out


def _row(s, order, as_of, currency, names, items):
    from bookflow.company import sales_orders, work
    from bookflow.company.billing_queries import root_identities
    header, rev = order['header'], order['rev']
    progress = sales_orders.progress(s, header, rev, work.saved_lines(s, rev), root_identities(s, header)).progress
    to_ship = any(f['to_ship'] > 0 for f in order['figures'])
    promised = order['promised']
    late = None
    if promised:
        late = max((calendar_date.fromisoformat(as_of) - calendar_date.fromisoformat(promised)).days, 0) \
            if to_ship else 0
    lines = []
    for f in order['figures']:
        line = f['line']['line']
        pick = f['pick'] or {}
        lines.append(FulfillmentLine(
            order_line_id=line['line_id'], position=line['position'], item_id=f['facts'].item_id,
            item_name=items.get(f['facts'].item_id, f['facts'].item_id),
            site_name=names.get(line['site_id']) if line.get('site_id') else None, ordered=_q(f['ordered']),
            shipped=_q(f['shipped']), shipped_not_invoiced=_q(f['waiting']), to_ship=_q(f['to_ship']),
            can_ship_now=_q(order['now'].get(f['root'], 0)), on_picks=_q(pick.get('held', 0)),
            picked=_q(pick.get('picked', 0)), drafted=_q(f['drafted']), packed=_q(f['packed']),
            shippable_amount=money(_billed(f, f['waiting'], order['now'].get(f['root'], 0)), currency)))
    note = None
    if order['refused']:
        first = order['refused'][0]
        note = (f'Pick list {", ".join(order["refused"])} holds units of this order: ship from it '
                f'(`shipment post --pick-list {first}`) or cancel it first.')
    elif order['worthless']:
        note = ('Every unit this order would ship is carried at no value in the books, so `shipment post` refuses it '
                '(shipment_without_value): enter the bill that bought the stock, or record what it is worth with '
                '`inventory adjust`, then ship.')
    return OrderFulfillmentRow(
        sales_order_id=header['id'], sales_order_number=header['number'], sales_order_version=header['version'],
        date=header['order_date'],
        customer_id=header['customer_id'], customer_name=header['customer_name'], promised_ship_date=promised,
        days_late=late, fulfillment_status=order['status'], invoice_progress=progress, site_id=order['here'],
        site_name=names.get(order['here']), can_ship_complete=order['complete'],
        to_ship_amount=money(order['to_ship_amount'], currency), shippable_amount=money(order['shippable'], currency),
        shipped_not_invoiced_amount=money(order['waiting_amount'], currency), pick_lists=order['lists'],
        drafts=order['drafts'], packed_drafts=order['packed_drafts'],
        by_site=[SiteShippable(site_id=site['site'], site_name=names.get(site['site'], site['site']),
                               lines=site['lines'], shippable_amount=money(site['amount'], currency),
                               complete=site['complete']) for site in order['sites']],
        lines=lines, note=note)


# ---------------------------------------------------------------- the shipments report


def shipments_report(s, inp, *, principal_id=None):
    with ledger._snapshot(s.company):
        return _shipments_report(s, inp, principal_id)


def _shipments_report(s, inp, principal_id):
    from bookflow.company import sales_defaults, shipment_packing as packing, shipments
    from bookflow.core.exact import parse_quantity_micro_units
    currency = shipments._home(s)
    unit = packing.weight_unit(s)
    found = []
    if shipments.has_shipments(s):
        where, params = ["r.status = 'shipped'", 'r.ship_date <= :date_to'], {'date_to': inp.date_to}
        if inp.date_from is not None:
            where.append('r.ship_date >= :date_from')
            params['date_from'] = inp.date_from
        if inp.customer is not None:
            where.append('h.customer_id = :customer')
            params['customer'] = sales_defaults._row(s.company, 'customer', inp.customer, active=False)['id']
        if inp.site is not None:
            where.append('r.site_id = :site')
            params['site'] = placements.site_id_for(s, inp.site)
        if inp.ship_method is not None:
            where.append('r.ship_method_id = :method')
            params['method'] = sales_defaults._row(s.company, 'ship_method', inp.ship_method, active=False)['id']
        found = [dict(row) for row in s.company.conn.execute(sa.text(
            f"""SELECT h.id, h.number, w.number AS order_number, cu.full_name AS customer_name, r.id AS revision_id,
                       r.ship_date, r.site_id, m.name AS ship_method, r.carrier, r.tracking_numbers,
                       r.freight_minor_units
                FROM shipments h JOIN ({shipments._CURRENT}) r ON r.shipment_id = h.id
                JOIN work_documents w ON w.id = h.sales_order_id LEFT JOIN customers cu ON cu.id = h.customer_id
                LEFT JOIN ship_methods m ON m.id = r.ship_method_id
                WHERE {' AND '.join(where)}"""), params).mappings()]
    found.sort(key=lambda row: (row['ship_date'], _natural(row['number'])))
    names = placements.site_names(s)
    rows, boxes, weight = [], 0, 0
    for row in found:
        lines = packing.shipment_lines(s, row['revision_id'])
        packed = packing.output(s, packing.load(s, row['revision_id']), lines, show_empty=True)
        tracking = list(json.loads(row['tracking_numbers'] or '[]'))
        tracking += [box.tracking_number for box in packed.packages if box.tracking_number]
        boxes += len(packed.packages)
        weight += parse_quantity_micro_units(packed.total_weight)
        rows.append(ShipmentsReportRow(
            shipment_id=row['id'], shipment_number=row['number'], ship_date=row['ship_date'],
            sales_order_number=row['order_number'], customer_name=row['customer_name'],
            site_name=names.get(row['site_id']), ship_method=row['ship_method'], carrier=row['carrier'],
            tracking_numbers=list(dict.fromkeys(tracking)), quantity=_q(sum(line.quantity for line in lines)),
            boxes=len(packed.packages),
            packages=[ShipmentsReportBox(box=box.box, package_type=box.package_type, weight=box.weight,
                                         tracking_number=box.tracking_number) for box in packed.packages],
            weight=packed.total_weight, unweighed=packed.unweighed,
            freight=money(int(row['freight_minor_units']), currency) if row['freight_minor_units'] else None))
    page = ledger.page_of(s, inp, 'shipments', rows, date_to=inp.date_to, date_from=inp.date_from,
                          principal_id=principal_id)
    return ShipmentsReportOutput(**page, date_from=inp.date_from, date_to=inp.date_to, weight_unit=unit,
                                 totals=ShipmentsReportTotals(shipments=len(rows), boxes=boxes, weight=_q(weight)))
