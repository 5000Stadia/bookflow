"""What is specific to a sales order (and shared with a work order's lines) in the customer-work family.

A sales order is the fifth customer-work kind: it is stored, revised, copied and shown by `work`,
and this module holds only what that kind adds -- quantities read from the billing ledger,
cancellation, the lifecycle a person decides, the void guard and the company switch. Nothing
here moves stock or posts: only the invoice made from an order does that.
"""
from __future__ import annotations

from fractions import Fraction

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.company.sales_models import _invalid
from bookflow.core.errors import BookflowError
from bookflow.core.money import Money

# The documents whose lines carry ordered, invoiced, cancelled and open quantities.
ORDER_KINDS = ('sales_order', 'work_order')
# A document in one of these states commits nothing and has nothing open.
NOT_LIVE = ('closed', 'voided', 'cancelled')


def _quantity(microunits):
    from bookflow.company import billing_math as math
    return math.format_fraction(Fraction(microunits, 1_000_000))


def require_enabled(s):
    """New orders and new estimate conversions to an order, refused while the company has them off."""
    row = s.company.conn.execute(sa.select(c.company_info.c.sales_orders_enabled)).scalar_one()
    if not row:
        from bookflow.company.work_preferences import disabled_error
        raise disabled_error('sales_orders', [])


def live(header):
    return bool(header['active']) and header['status'] not in NOT_LIVE


def _root(s, identities, line):
    identity = identities.get(line['line_id'])
    if identity is None:
        found = s.company.conn.execute(sa.select(c.work_line_identities).where(
            c.work_line_identities.c.id == line['line_id'])).mappings().first()
        identity = dict(found) if found else dict(root_document_id=line['document_id'], root_line_id=line['line_id'])
    return identity['root_document_id'], identity['root_line_id']


def invoiced_microunits(s, root, facts, policy):
    """What active sales have taken of one line, as an exact quantity in microunits (a Fraction)."""
    from bookflow.company import billing_allocations as alloc
    length, d = alloc.invoiced_length(s, root, facts, policy=policy)
    return Fraction(facts.quantity_microunits * length, d)


def line_quantities(s, header, rev, lines, identities, currency):
    """Each line's ordered, invoiced, cancelled and open figures, keyed by line identity.

    Subtotal and discount lines are carried with the lines they show or reduce and have none.
    """
    from bookflow.company import billing_allocations as alloc, tax_policy, work
    from bookflow.company.billing_selection import derived
    from bookflow.company.work_outputs import OrderLineQuantities
    policy = tax_policy.effective(work.facts(rev).profile)
    running = live(header)
    out = {}
    for line in lines:
        f = work.line_facts(line)
        if derived(f):
            continue
        root = _root(s, identities, line)
        invoiced = invoiced_microunits(s, root, f, policy)
        cancelled = f.cancelled_quantity_microunits
        remaining = f.quantity_microunits - invoiced - cancelled
        open_quantity = remaining if running else 0
        open_net = alloc.remaining(s, root, f, policy=policy)[1] if running else 0
        state = ('open' if not invoiced and remaining else 'partly_invoiced' if remaining
                 else 'invoiced' if not cancelled else 'closed')
        out[line['line_id']] = OrderLineQuantities(ordered=_quantity(f.quantity_microunits),
            invoiced=_quantity(invoiced), cancelled=_quantity(cancelled), open=_quantity(open_quantity),
            open_amount=Money(open_net, currency).to_dict(), state=state)
    return out


def progress(s, header, rev, lines, identities, sources=()):
    """A sales order's progress: closed and voided are decisions, the rest is read from its lines."""
    from bookflow.company.work_outputs import OrderProgress
    currency = rev['currency']
    figures = line_quantities(s, header, rev, lines, identities, currency)
    total = sum(value.open_amount.minor_units for value in figures.values())
    states = [value.state for value in figures.values()]
    any_open = any(Fraction(value.open) > 0 for value in figures.values())
    if header['status'] in ('closed', 'voided'):
        word = header['status']
    elif states and all(state in ('invoiced', 'closed') for state in states):
        word = 'invoiced' if all(state == 'invoiced' for state in states) else 'closed'
    elif any(Fraction(value.invoiced) > 0 for value in figures.values()):
        word = 'partly_invoiced'
    else:
        word = 'open'
    held = held_prepayment(s, header, sources)
    return OrderProgress(progress=word, is_open=header['status'] == 'open' and bool(header['active']) and any_open,
                         open_amount=Money(total, currency).to_dict(),
                         prepayment_held=Money(held, currency).to_dict() if held else None)


def apply_cancellations(s, kind, entered, out, old_rev, policy):
    """Turn `close: true` into the line's open quantity, and refuse cancelling invoiced quantity.

    ``entered`` are the line inputs in order (after group expansion), ``out`` the resolved
    ``{'line_id', 'facts'}`` values in the same order. Saving requires
    cancelled <= ordered - invoiced on every line, and releasing an invoice only frees more.
    """
    from bookflow.company import work
    from bookflow.company.billing_selection import derived
    if kind not in ORDER_KINDS:
        return
    identities = {}
    if old_rev is not None:
        keys = [value['line_id'] for value in out if value['line_id']]
        if keys:
            identities = {row['id']: row for row in work.rows(s, c.work_line_identities,
                          c.work_line_identities.c.id.in_(keys))}
    for index, (line, value) in enumerate(zip(entered, out)):
        facts = work.read_line(value['facts'])
        if derived(facts):
            if facts.cancelled_quantity_microunits or getattr(line, 'close', False):
                raise _invalid(f'lines.{index}.cancelled_quantity', 'a subtotal or discount line has no quantity to cancel')
            continue
        invoiced = 0
        key = value['line_id']
        if key and key in identities:
            identity = identities[key]
            invoiced = invoiced_microunits(s, (identity['root_document_id'], identity['root_line_id']), facts, policy)
        free = facts.quantity_microunits - invoiced
        if getattr(line, 'close', False):
            # The anchor's "Clsd": whatever is still open on the line is cancelled, exactly.
            if free.denominator != 1:
                raise _invalid(f'lines.{index}.close', 'the open quantity is not a whole number of millionths; enter cancelled_quantity')
            value['facts']['cancelled_quantity_microunits'] = int(free)
        elif facts.cancelled_quantity_microunits > free:
            raise _invalid(f'lines.{index}.cancelled_quantity',
                           f'at most {_quantity(free)} can be cancelled; {_quantity(invoiced)} is already invoiced')


def lifecycle(old, value, ctx):
    """open <-> closed with a reason; `voided` only through `sales-order void`."""
    status = value['status']
    if status not in ('open', 'closed'):
        raise _invalid('status', 'a sales order is open or closed; void it with sales-order void')
    if old is None:
        if status != 'open':
            raise _invalid('status', 'a new sales order begins open')
        return
    if status != old['status'] and (not ctx.reason or not ctx.reason.strip()):
        raise BookflowError('E_REASON_REQUIRED')


def consumed(s, header):
    """The roots of this order an active invoice or sales receipt consumes, as (root, line_id)."""
    from bookflow.company import billing_queries as query
    from bookflow.company.billing_allocations import occupied_roots
    identities = query.root_identities(s, header)
    roots = {(row['root_document_id'], row['root_line_id']): key for key, row in identities.items()}
    occupied = occupied_roots(s, list(roots))
    return {root: roots[root] for root in occupied if root in roots}


def protect(s, header, before, after):
    """A consumed line keeps its item, unit, quantity and price and stays; the customer stays fixed.

    Memo, ship date and method, tax code, custom values, cancelled quantity and added lines stay
    editable, so unlike work orders a consumed order's header is not frozen.
    """
    from bookflow.company import work_tax
    from bookflow.company.billing import dependency
    taken = consumed(s, header)
    if not taken:
        return
    if before['facts']['profile']['customer']['id'] != after['facts']['profile']['customer']['id']:
        dependency('the customer of an order an invoice consumes cannot change', source_id=header['id'])
    old = {entry['line_id']: entry['facts'] for entry in before['lines']}
    new = {entry['line_id']: entry['facts'] for entry in after['lines']}
    for key in taken.values():
        if key not in old:
            continue
        if key not in new or work_tax.economics(old[key]) != work_tax.economics(new[key]):
            dependency('an invoiced order line keeps its item, unit, quantity and price and cannot be removed; '
                       'cancel what is left or add a line', source_line_id=key)


def held_prepayment(s, header, sources=()):
    """What customer prepayments hold for this order, in minor units: on the order itself and on the
    estimate it came from, which is the order's money once the estimate converts.

    Invoicing from an order (increment 4) draws it: the order's invoice command joins BILLING_KINDS and the
    billing conversion's `apply_prepayment` calls `prepayments.conversion_preview` / `conversion_draw`
    with kind `sales_order`, which `prepayments.HOLDING_KINDS` and `_pool_documents` already admit.
    """
    from bookflow.company import prepayments
    if prepayments.find_account(s.company) is None:
        return 0
    if not sources:
        return prepayments.held_for_document(s, header)
    # A conversion still being written: its estimate link is pending, so name the source here.
    ids = list(dict.fromkeys(prepayments._pool_documents(s, header) + list(sources)))
    return sum(row['held'] for row in prepayments.receipt_rows(s, document_ids=ids))


def void_guard(s, header):
    """`sales-order void` is refused while an active invoice consumes any root or a prepayment is held."""
    taken = consumed(s, header)
    if taken:
        raise BookflowError('E_WORK_DEPENDENCY', details=dict(source_id=header['id'],
            problem='an invoice consumes this order; void or remove that invoice first',
            source_line_ids=sorted(taken.values())))
    held = held_prepayment(s, header)
    if held:
        from bookflow.core.money import Money
        raise BookflowError('E_WORK_DEPENDENCY', details=dict(source_id=header['id'], held=Money(held, s.company_info_row['home_currency']).to_dict(),
            problem='a customer prepayment is held on this order; apply it to an invoice or pay it back with '
                    '`prepayment refund` first'))
