"""Billing part of a purchase order, and closing an ordered line by hand (R153).

In the anchor, Enter Bills against an open order lets the bill take part of a line's quantity;
the order keeps how much of each line has been received and billed, a line is done when all of
it has arrived, and a line can be closed by hand when the rest is never coming. Here:

* ``bill post`` with ``purchase_order`` and ``order_lines`` takes exactly the named parts. The
  bill receives what it bills (an inventory item moves into stock through the bill's own item
  line, as any item line does), so a part taken this way counts against the line together with
  the physical receipts `item-receipt` makes. Each part is one ``purchase_order_bill_claims``
  row; a database trigger refuses a claim that would take more than the line has open.
* Voiding or deleting the bill writes one ``purchase_order_bill_releases`` row per claim, so its
  quantity is open on the order again. History is never rewritten.
* ``purchase-order close-lines`` closes (or reopens) ordered lines; the latest
  ``purchase_order_line_closures`` row per line decides.

After every one of these the order's own status follows from its lines -- ``closed`` when each
line is fully received or closed by hand, ``partly_received`` while anything has arrived,
``open`` otherwise -- written as a new order revision in the same audit event, exactly as a
receipt moves it. ``line_states`` is the one owner of a line's received, billed and open
quantities; ``receiving.order_remaining`` (stock status's on-order) and the open purchase
orders report read through it.
"""
from __future__ import annotations

import re

import sqlalchemy as sa

from bookflow.core import unit_price
from bookflow.company import document_effects as effects, schema as c
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_quantity_micro_units, parse_quantity_micro_units
from bookflow.core.ids import new_id
from bookflow.core.registry import Touched


def _invalid(field, problem):
    return BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': problem}]})


# ---------------------------------------------------------------- reading a line's state


def _active_receipt_claims(s, line_ids):
    t, r = c.purchase_order_receipt_claims, c.purchase_order_receipt_releases
    if not line_ids:
        return []
    return [dict(row) for row in s.company.conn.execute(sa.select(t).where(
        t.c.order_line_id.in_(line_ids), ~sa.exists().where(r.c.claim_id == t.c.id))).mappings()]


def active_bill_claims(s, *, line_ids=None, bill_id=None):
    """Claims bills entered from orders still hold: not given back by a void or a delete."""
    t, r = c.purchase_order_bill_claims, c.purchase_order_bill_releases
    query = sa.select(t).where(~sa.exists().where(r.c.claim_id == t.c.id))
    if line_ids is not None:
        if not line_ids:
            return []
        query = query.where(t.c.order_line_id.in_(line_ids))
    if bill_id is not None:
        query = query.where(t.c.bill_id == bill_id)
    return [dict(row) for row in s.company.conn.execute(query).mappings()]


def closed_lines(s, line_ids):
    """The ordered lines whose latest closure row closes them."""
    t = c.purchase_order_line_closures
    latest = {}
    if not line_ids:
        return set()
    for row in s.company.conn.execute(sa.select(t).where(t.c.order_line_id.in_(line_ids))).mappings():
        if row['order_line_id'] not in latest or row['sequence'] > latest[row['order_line_id']]['sequence']:
            latest[row['order_line_id']] = row
    return {key for key, row in latest.items() if row['closed']}


def _billed_through_receipts(s, receipt_claims):
    """Quantity billed against the receipts that received each ordered line."""
    by_receipt = {row['receipt_line_id']: row['order_line_id'] for row in receipt_claims}
    if not by_receipt:
        return {}
    t, r = c.receipt_bill_claims, c.receipt_bill_releases
    result = {}
    for row in s.company.conn.execute(sa.select(t).where(
            t.c.receipt_line_id.in_(list(by_receipt)), ~sa.exists().where(r.c.claim_id == t.c.id))).mappings():
        key = by_receipt[row['receipt_line_id']]
        result[key] = result.get(key, 0) + row['end_microunits'] - row['start_microunits']
    return result


def line_states(s, header, rev, *, pending_claims=(), released=(), closures=None):
    """Each ordered line with what has been received, billed and is still open.

    ``received`` counts physical receipts and bills entered from the order (a bill against the
    order receives what it bills); ``billed`` counts those bills and bills entered from the
    receipts. An item line with a quantity is measured in quantity; an amount-only line in
    money. A closed line, a line on a voided order and every line of an order consumed whole by
    one bill have nothing open. ``pending_claims``/``released``/``closures`` overlay a write
    being prepared, so its own status transition is computed before anything is written.
    """
    from bookflow.company import purchase_orders as orders
    lines = orders.saved_lines(s, rev)
    ids = [line['line_id'] for line in lines]
    receipts = _active_receipt_claims(s, ids)
    released = set(released)
    bills = [row for row in active_bill_claims(s, line_ids=ids) if row['id'] not in released]
    bills += [row for row in pending_claims if row['order_line_id'] in set(ids)]
    through = _billed_through_receipts(s, receipts)
    closed = closed_lines(s, ids) if closures is None else closures
    whole = orders.conversion_row(s, header['id']) is not None
    result = []
    for position, line in enumerate(lines, 1):
        key = line['line_id']
        received = sum(r['quantity_microunits'] for r in receipts if r['order_line_id'] == key)
        direct = [b for b in bills if b['order_line_id'] == key]
        quantity_line = line['quantity_microunits'] is not None
        direct_quantity = sum(b['quantity_microunits'] or 0 for b in direct)
        direct_amount = sum(b['amount_minor_units'] for b in direct)
        state = dict(line_id=key, position=line['position'] if 'position' in line else position,
                     item_id=line['item_id'], account_id=line['account_id'], description=line['description'],
                     ordered_quantity_microunits=line['quantity_microunits'],
                     rate_minor_units=line['rate_minor_units'], rate_fraction=line.get('rate_fraction'),
                     amount_minor_units=line['amount_minor_units'],
                     received_quantity_microunits=received + direct_quantity,
                     billed_quantity_microunits=direct_quantity + through.get(key, 0),
                     billed_amount_minor_units=direct_amount,
                     closed=key in closed, measured='quantity' if quantity_line else 'amount')
        if whole or header['status'] == 'voided' or key in closed:
            state['open_quantity_microunits'] = 0 if quantity_line else None
            state['open_amount_minor_units'] = None if quantity_line else 0
        elif quantity_line:
            state['open_quantity_microunits'] = max(0, line['quantity_microunits'] - received - direct_quantity)
            state['open_amount_minor_units'] = None
        else:
            state['open_quantity_microunits'] = None
            state['open_amount_minor_units'] = max(0, line['amount_minor_units'] - direct_amount)
        state['done'] = (state['closed'] or (state['open_quantity_microunits'] == 0 if quantity_line
                                             else state['open_amount_minor_units'] == 0 and direct_amount > 0))
        state['active'] = bool(received or direct)
        result.append(state)
    return result


def order_status(states):
    """The order's status from its lines: closed, partly received, or open."""
    if states and all(line['done'] for line in states):
        return 'closed'
    return 'partly_received' if any(line['active'] for line in states) else 'open'


def line_output(state):
    """A line's tracked quantities in public form (decimal quantities, exact minor units)."""
    q = lambda v: None if v is None else format_quantity_micro_units(v)
    return dict(line_id=state['line_id'], position=state['position'], item_id=state['item_id'],
                account_id=state['account_id'], description=state['description'],
                measured=state['measured'], ordered_quantity=q(state['ordered_quantity_microunits']),
                received_quantity=q(state['received_quantity_microunits']) if state['measured'] == 'quantity' else None,
                billed_quantity=q(state['billed_quantity_microunits']) if state['measured'] == 'quantity' else None,
                open_quantity=q(state['open_quantity_microunits']),
                amount_minor_units=state['amount_minor_units'],
                billed_amount_minor_units=state['billed_amount_minor_units'],
                open_amount_minor_units=state['open_amount_minor_units'], closed=state['closed'])


# ---------------------------------------------------------------- the order's status revision


def transition(s, header, rev, status, provenance, *, force=False):
    """A new order revision carrying ``status``, or None when the status does not change.

    ``force`` writes the revision even when it does not, for a change to the order itself
    (closing a line) that must move its version."""
    from bookflow.company import purchase_orders as orders
    if status == header['status'] and not force:
        return None
    lines = orders.saved_lines(s, rev)
    created = {k: provenance[k] for k in ('created_at', 'created_by', 'created_via')}
    new_rev = dict(rev, id=new_id(), **created, status=status, revision_number=rev['revision_number'] + 1,
                   supersedes_revision_id=rev['id'], audit_event_id=provenance['audit_event_id'])
    new_header = dict(header, status=status, version=header['version'] + 1, current_revision_id=new_rev['id'],
                      updated_at=created['created_at'], updated_by=created['created_by'],
                      updated_via=created['created_via'])
    carried = [dict(line, id=new_id(), **created, revision_id=new_rev['id']) for line in lines]
    return header, new_header, new_rev, carried


class OrderSide:
    """The order's side of one write: claims, releases or closures, and its status revision.

    Passed to the owning writer as its companion so one audit event records both documents.
    """

    def __init__(self, *, claims=(), releases=(), closures=(), moved=None):
        self.claims, self.releases, self.closures = list(claims), list(releases), list(closures)
        self.moved = moved

    @property
    def touches(self):
        result = [Touched('purchase_order_bill_claim', row['id'], 'create', None, 1, dict(row), db='company')
                  for row in self.claims]
        result += [Touched('purchase_order_bill_release', row['id'], 'create', None, 1, dict(row), db='company')
                   for row in self.releases]
        result += [Touched('purchase_order_line_closure', row['id'], 'create', None, 1, dict(row), db='company')
                   for row in self.closures]
        if self.moved:
            old, new, rev, lines = self.moved
            result += [Touched('purchase_order', new['id'], 'update', old['version'], new['version'], new, old,
                               db='company'),
                       Touched('purchase_order_revision', rev['id'], 'create', None, 1, effects.decoded(rev),
                               db='company'),
                       *[Touched('purchase_order_line', row['id'], 'create', None, 1, effects.decoded(row),
                                 db='company') for row in lines]]
        return result

    def write(self, s):
        if self.moved:
            old, new, rev, lines = self.moved
            changed = s.company.conn.execute(c.purchase_orders.update().where(
                c.purchase_orders.c.id == old['id'], c.purchase_orders.c.version == old['version']).values(**new)).rowcount
            if changed != 1:
                raise BookflowError('E_VERSION_CONFLICT', details={'record_id': old['id']})
            s.company.conn.execute(c.purchase_order_revisions.insert().values(**rev))
            if lines:
                s.company.conn.execute(c.purchase_order_lines.insert(), lines)
        if self.closures:
            s.company.conn.execute(c.purchase_order_line_closures.insert(), self.closures)
        if self.releases:
            s.company.conn.execute(c.purchase_order_bill_releases.insert(), self.releases)
        if self.claims:
            # After the status revision: the capacity trigger reads the order's current lines.
            try:
                s.company.conn.execute(c.purchase_order_bill_claims.insert(), self.claims)
            except sa.exc.DBAPIError as exc:
                if 'remains on the ordered line' not in str(exc):
                    raise
                raise _invalid('order_lines', 'more than remains open on the ordered line') from exc


# ---------------------------------------------------------------- a bill taking part of an order


_POSITION = re.compile(r'^[0-9]{1,4}$')


def _find_line(states, selector, index):
    if _POSITION.match(selector):
        position = int(selector)
        found = [st for st in states if st['position'] == position]
    else:
        found = [st for st in states if st['line_id'] == selector.upper()]
    if not found:
        raise _invalid(f'order_lines[{index}].line', 'no such line on this purchase order; use a line id '
                       'from `purchase-order show` or its 1-based position')
    return found[0]


def source(s, inp):
    """The parts of the order this bill takes, and the bill's input filled in from them.

    Returns ``(partial, entry)``: ``partial`` carries the order, its revision and one pending
    claim per selection (bill ids filled in when the bill is built); ``entry`` is ``inp`` with
    the order's header facts and exactly the selected lines.
    """
    from bookflow.company import purchase_orders as orders
    from bookflow.company.bill_models import BillExpenseInput, BillItemInput
    from bookflow.company.parties import resolve_party
    from bookflow.company.sales_calculations import extension
    from bookflow.company.sales_models import money as sales_money
    header = orders.resolve(s, inp.purchase_order)
    if header['status'] == 'voided':
        raise _invalid('purchase_order', 'a voided purchase order cannot become a bill')
    existing = orders.conversion_row(s, header['id'])
    if existing is not None:
        raise BookflowError('E_WORK_DEPENDENCY', details={
            'purchase_order_id': header['id'], 'bill_id': existing['destination_transaction_id'],
            'problem': 'this purchase order has already become one bill in full',
            'next': 'Correct that bill instead.'})
    rev = orders.revision(s, header)
    states = line_states(s, header, rev)
    lines = {line['line_id']: line for line in orders.saved_lines(s, rev)}
    currency = rev['currency']
    chosen, claims, seen = [], [], set()
    unit_costs = {}
    for index, selection in enumerate(inp.order_lines):
        state = _find_line(states, selection.line, index)
        field = f'order_lines[{index}]'
        if state['line_id'] in seen:
            raise _invalid(field + '.line', 'take each ordered line once per bill')
        seen.add(state['line_id'])
        if state['closed']:
            raise _invalid(field + '.line', 'this ordered line is closed; reopen it with '
                           '`purchase-order close-lines --reopen` before billing it')
        line = dict(lines[state['line_id']])
        if state['measured'] == 'quantity':
            if selection.amount is not None:
                raise _invalid(field + '.amount', 'an item line with a quantity is taken by quantity')
            open_quantity = state['open_quantity_microunits']
            quantity = (parse_quantity_micro_units(selection.quantity) if selection.quantity is not None
                        else open_quantity)
            if quantity <= 0:
                raise _invalid(field + '.quantity', 'nothing remains open on this line' if open_quantity == 0
                               else 'must be more than zero')
            if quantity > open_quantity:
                raise _invalid(field + '.quantity', (
                    f'only {format_quantity_micro_units(open_quantity)} of '
                    f'{format_quantity_micro_units(state["ordered_quantity_microunits"])} remain open on this line '
                    f'({format_quantity_micro_units(state["received_quantity_microunits"])} already received or billed)'))
            rate = unit_price.row_units(line, 'rate')  # R150: the exact ordered rate
            if selection.unit_cost is not None:
                rate = unit_price.parse(selection.unit_cost, currency, field + '.unit_cost')
                unit_costs[len(chosen)] = rate
            line['quantity_microunits'] = quantity
            amount = unit_price.extension(quantity, rate)
            line['amount_minor_units'] = amount
        else:
            if selection.quantity is not None or selection.unit_cost is not None:
                raise _invalid(field + '.quantity', 'an account line is taken by amount')
            open_amount = state['open_amount_minor_units']
            amount = (sales_money(selection.amount, currency, field + '.amount').minor_units
                      if selection.amount is not None else open_amount)
            if amount <= 0:
                raise _invalid(field + '.amount', 'nothing remains open on this line' if open_amount == 0
                               else 'must be more than zero')
            if amount > open_amount:
                raise _invalid(field + '.amount', f'only {open_amount} minor units remain open on this line')
            quantity = None
            line['amount_minor_units'] = amount
        chosen.append(line)
        claims.append(dict(order_id=header['id'], order_line_id=state['line_id'],
                           quantity_microunits=quantity, amount_minor_units=amount))
    carried = orders.bill_entry(rev, chosen)
    # bill_entry lists items then expenses; a unit-cost override follows its own item line.
    items, position = [], 0
    for index, line in enumerate(chosen):
        if line['item_id'] is not None and line['quantity_microunits'] is not None:
            row = carried['items'][position]
            position += 1
            if index in unit_costs:
                row = row.model_copy(update={'unit_cost': unit_price.to_dict(unit_costs[index], currency)})
            items.append(row)
        elif line['item_id'] is not None:
            items.append(carried['items'][position])
            position += 1
    carried['items'] = items
    if inp.vendor is not None and resolve_party(s.company, 'vendor', inp.vendor)['id'] != carried['vendor']:
        raise _invalid('vendor', 'the bill is owed to the vendor the purchase order was placed with; omit vendor')
    supplied = inp.model_fields_set
    update = {key: value for key, value in carried.items()
              if key in ('items', 'expenses') or key not in supplied or getattr(inp, key) is None}
    for key in ('items', 'expenses'):
        update[key] = update[key] or []
    entry = inp.model_copy(update=update)
    return dict(header=header, revision=rev, claims=claims), entry


def post_side(s, ctx, partial, bill_id, bill_revision_id, at, event):
    """The order's side of a bill taking part of it: claims and the order's new status."""
    provenance = dict(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value, audit_event_id=event)
    claims = [dict(id=new_id(), **row, bill_id=bill_id, bill_revision_id=bill_revision_id, **provenance)
              for row in partial['claims']]
    header, rev = partial['header'], partial['revision']
    status = order_status(line_states(s, header, rev, pending_claims=claims))
    return OrderSide(claims=claims, moved=transition(s, header, rev, status, provenance))


def release_side(s, ctx, bill_id, at, event):
    """Give back every part of an order a voided or deleted bill held; None when it held none."""
    from bookflow.company import purchase_orders as orders
    held = active_bill_claims(s, bill_id=bill_id)
    if not held:
        return None
    provenance = dict(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value, audit_event_id=event)
    releases = [dict(id=new_id(), claim_id=row['id'], **provenance) for row in held]
    moved = None
    order_ids = sorted({row['order_id'] for row in held})
    if len(order_ids) != 1:
        raise BookflowError('E_INTERNAL', message='a bill takes part of one purchase order')
    header = orders.resolve(s, order_ids[0])
    if header['status'] != 'voided':
        rev = orders.revision(s, header)
        status = order_status(line_states(s, header, rev, released=[row['id'] for row in held]))
        moved = transition(s, header, rev, status, provenance)
    return OrderSide(releases=releases, moved=moved)


def refuse_correction(s, bill_id):
    """A bill holding part of an order is corrected by voiding it and entering it again."""
    held = active_bill_claims(s, bill_id=bill_id)
    if held:
        raise BookflowError('E_WORK_DEPENDENCY', details={
            'bill_id': bill_id, 'purchase_order_id': held[0]['order_id'],
            'problem': 'this bill took part of a purchase order, so its lines are fixed to that order',
            'next': 'Void the bill (its part returns to the order) and enter it again with order_lines.'})


def order_for_bill(s, bill_ids):
    """The order each of these bills took part of, where it did."""
    ids = list(bill_ids)
    if not ids:
        return {}
    t = c.purchase_order_bill_claims
    return {row['bill_id']: row['order_id'] for row in s.company.conn.execute(
        sa.select(t.c.bill_id, t.c.order_id).where(t.c.bill_id.in_(ids))).mappings()}


# ---------------------------------------------------------------- closing lines by hand


def _close_plan(s, ctx, inp):
    from bookflow.company import purchase_orders as orders
    from bookflow.core import clock
    header = orders.resolve(s, inp.purchase_order)
    warnings = []
    meta = orders._version(s, header, inp.expected_version)
    from bookflow.company import list_service
    if meta and (w := list_service.blind_write_warning(meta)):
        warnings.append(w)
    if header['status'] == 'voided':
        raise _invalid('purchase_order', 'a voided purchase order has no open lines to close')
    if orders.conversion_row(s, header['id']) is not None:
        raise BookflowError('E_WORK_DEPENDENCY', details={
            'purchase_order_id': header['id'],
            'problem': 'this purchase order became one bill in full; it has no open lines',
            'next': 'Nothing to close.'})
    rev = orders.revision(s, header)
    states = line_states(s, header, rev)
    closed = {st['line_id'] for st in states if st['closed']}
    wanted, chosen = set(closed), []
    for index, selector in enumerate(inp.lines):
        state = _find_line(states, selector, index)
        chosen.append(state['line_id'])
        if inp.reopen:
            wanted.discard(state['line_id'])
        else:
            wanted.add(state['line_id'])
    changes = sorted(wanted ^ closed)
    at, event = clock.now_iso(), new_id()
    provenance = dict(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value, audit_event_id=event)
    t = c.purchase_order_line_closures
    rows = []
    for key in changes:
        last = s.company.conn.execute(sa.select(sa.func.max(t.c.sequence)).where(t.c.order_line_id == key)).scalar()
        rows.append(dict(id=new_id(), order_id=header['id'], order_line_id=key, closed=key in wanted,
                         sequence=(last or 0) + 1, **provenance))
    side = None
    if rows:
        status = order_status(line_states(s, header, rev, closures=wanted))
        side = OrderSide(closures=rows, moved=transition(s, header, rev, status, provenance, force=True))
    return header, rev, side, warnings, event


def close_lines_plan(inp, ctx, s):
    from bookflow.company import purchase_orders as orders
    from bookflow.company.purchase_order_models import PurchaseOrderWriteOutput
    from bookflow.core.registry import Plan
    header, rev, side, warnings, _ = _close_plan(s, ctx, inp)
    view = orders.output(s, header, rev).model_dump()
    if side is not None:
        new = side.moved[1]
        view.update(status=new['status'], version=new['version'])
        closing = {row['order_line_id']: row['closed'] for row in side.closures}
        for line in view['line_tracking']:
            if line['line_id'] in closing:
                line['closed'] = closing[line['line_id']]
    return Plan(PurchaseOrderWriteOutput(**view, changed=side is not None, warnings=warnings,
                                         changed_fields=['lines.closed'] if side else []),
                dict(input=inp))


def close_lines_apply(plan, ctx, s):
    from bookflow.company import purchase_orders as orders
    from bookflow.company.purchase_order_models import PurchaseOrderWriteOutput
    from bookflow.core import audit
    from bookflow.core.registry import Applied
    inp = plan.data['input']
    header, rev, side, warnings, event = _close_plan(s, ctx, inp)
    if side is None:
        return Applied(plan.preview, [], 'no change')
    touched = side.touches
    verb = 'reopen' if inp.reopen else 'close'
    summary = f"{verb} {len(side.closures)} line(s) on purchase order {header['number']}"
    audit.write_event_to(s.company, ctx, 'purchase-order close-lines', summary, touched,
                         actor_id=s.actor.id, actor_kind=s.actor.kind,
                         directive_code=getattr(s, 'directive_code', None), event_id=event)
    side.write(s)
    current = orders.resolve(s, header['id'])
    view = orders.output(s, current, orders.revision(s, current)).model_dump()
    return Applied(PurchaseOrderWriteOutput(**view, changed=True, warnings=warnings,
                                            changed_fields=['lines.closed']), touched, summary, audited=True)


# ---------------------------------------------------------------- changing an order bills hold


def guard_order_change(s, header, rev, operation, resolved=None):
    """Refuse an order change that would strand what bills took from it; return the new status.

    A void is refused while any bill holds part of the order (void the bills first, as a
    receipt must be). An update may not drop or re-item a line a bill took from, nor cut it
    below what was billed. When bills hold parts or lines are closed, the updated order's
    status follows its new lines unless the caller set one. ``None`` when nothing holds it.
    """
    lines = {line['line_id']: line for line in line_states(s, header, rev)}
    held = {key: st for key, st in lines.items() if st['billed_amount_minor_units'] or st['closed']}
    if not held:
        return None
    billed = {key: st for key, st in held.items() if st['billed_amount_minor_units']}
    if operation == 'void':
        if billed:
            raise BookflowError('E_WORK_DEPENDENCY', details={
                'purchase_order_id': header['id'],
                'bill_ids': sorted({row['bill_id'] for row in active_bill_claims(s, line_ids=list(billed))}),
                'problem': 'bills took part of this purchase order',
                'next': 'Void those bills first (their parts return to the order), then void the order.'})
        return None
    new = {line['line_id']: line for line in resolved['lines'] if line['line_id']}
    states = []
    for key, st in lines.items():
        line = new.get(key)
        if key in billed:
            if line is None:
                raise _invalid('lines', f'line {st["position"]} was billed in part; it cannot be removed')
            item = line['profile'].item.id if line['profile'].item else None
            if item != st['item_id']:
                raise _invalid('lines', f'line {st["position"]} was billed in part; its item cannot change')
            if st['measured'] == 'quantity':
                if line['quantity_microunits'] is None or line['quantity_microunits'] < st['received_quantity_microunits']:
                    raise _invalid('lines', (f'line {st["position"]} cannot be cut below the '
                                   f'{format_quantity_micro_units(st["received_quantity_microunits"])} already received or billed'))
            elif line['quantity_microunits'] is not None or line['amount_minor_units'] < st['billed_amount_minor_units']:
                raise _invalid('lines', f'line {st["position"]} cannot be cut below the {st["billed_amount_minor_units"]} minor units already billed')
    for line in resolved['lines']:
        st = lines.get(line['line_id'])
        done_quantity = st['received_quantity_microunits'] if st else 0
        done_amount = st['billed_amount_minor_units'] if st else 0
        closed = bool(st and st['closed'])
        if line['quantity_microunits'] is not None:
            done = closed or done_quantity >= line['quantity_microunits']
        else:
            done = closed or (done_amount > 0 and done_amount >= line['amount_minor_units'])
        states.append(dict(done=done, active=bool(st and st['active'])))
    return order_status(states)


def remaining_selection(s, inp):
    """A whole-order ``bill post`` on an order already billed in part, as the parts still open.

    Unchanged when the bill names no order, when no bill holds part of it and no line is closed
    (the whole-order conversion stands), or when receipts hold it (that path refuses on its own).
    """
    from bookflow.company import purchase_orders as orders
    from bookflow.company.bill_models import OrderLineSelection
    from bookflow.company.receiving import physical_claims
    if getattr(inp, 'purchase_order', None) is None or getattr(inp, 'receipts', None) is not None:
        return inp
    header = orders.resolve(s, inp.purchase_order)
    if header['status'] == 'voided' or orders.conversion_row(s, header['id']) is not None:
        return inp
    rev = orders.revision(s, header)
    states = line_states(s, header, rev)
    if not any(st['billed_amount_minor_units'] or st['closed'] for st in states):
        return inp
    if physical_claims(s, order_line_ids=[st['line_id'] for st in states]):
        return inp
    if inp.expenses is not None or inp.items is not None:
        raise BookflowError('E_WORK_DEPENDENCY', details={
            'purchase_order_id': header['id'],
            'problem': 'bills already took part of this purchase order',
            'next': 'Name the parts this bill takes with order_lines, or leave the lines out to bill what is still open.'})
    selections = [OrderLineSelection(line=st['line_id']) for st in states
                  if not st['closed'] and (st['open_quantity_microunits'] or st['open_amount_minor_units'])]
    if not selections:
        raise BookflowError('E_WORK_DEPENDENCY', details={
            'purchase_order_id': header['id'],
            'problem': 'nothing is still open on this purchase order: every line is billed, received or closed',
            'next': 'Reopen a line with `purchase-order close-lines --reopen`, or void a bill to return its part.'})
    return inp.model_copy(update={'order_lines': selections})
