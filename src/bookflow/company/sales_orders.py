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
    """An exact quantity from microunits; signed, because availability can be below zero."""
    from bookflow.company import billing_math as math
    value = Fraction(microunits) / 1_000_000
    return ('-' if value < 0 else '') + math.format_fraction(abs(value))


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


# ------------------------------------------------------------------ invoicing from an order (§4)

class _WholeLine:
    """A selection entry that bills the line's whole open remainder (no quantity named)."""

    def __init__(self, line_id):
        self.line_id = line_id
        self.quantity = self.net_amount = self.percent = self.rebill_allocation_id = None


class _Choice:
    """An order invoice request in the billing selection's own terms."""

    def __init__(self, line_ids=None, selections=None, shipped=None):
        self.line_ids, self.selections, self.percent = line_ids, selections, None
        self.shipped = shipped or {}


def _micro(fraction):
    return int(fraction * 1_000_000) if (fraction * 1_000_000).denominator == 1 else None


def billing_choice(s, inp, header, rev, lines, identities):
    """What an order invoice bills: the explicit choice, every open quantity, or what can ship.

    With no selection each stocked line bills min(open, available to this order) -- on hand less
    what every other order and work order commits -- shared in line order among the order's lines
    for one item; every other open line bills when anything ships or the order has no open stocked
    line. When nothing can ship it refuses, giving each line's open quantity and availability and
    the explicit choices. The same request always translates to the same choice inside one reading,
    so the conversion's validation re-reads it exactly.
    """
    from bookflow.company import commitments, sales_defaults
    from bookflow.company.billing_models import BillingSelection
    from bookflow.company.billing_selection import derived
    from bookflow.company.items import TRACKED_TYPES
    from bookflow.company.sales_calculations import base_quantity
    from bookflow.company import work
    figures = line_quantities(s, header, rev, lines, identities, rev['currency'])
    rows = []
    for line in lines:
        facts = work.line_facts(line)
        if derived(facts) or line['line_id'] not in figures:
            continue
        rows.append((line, facts, Fraction(figures[line['line_id']].open)))
    open_rows = [row for row in rows if row[2] > 0]
    if inp.all_open:
        return _Choice()
    if inp.line_ids:
        return _Choice(line_ids=list(inp.line_ids))
    if inp.selections:
        chosen = []
        for index, entry in enumerate(inp.selections):
            line_id = entry.line_id
            if line_id is None:
                item = sales_defaults._row(s.company, 'item', entry.item, active=False)['id']
                matches = [line['line_id'] for line, facts, remaining in open_rows if facts.item_id == item]
                if len(matches) != 1:
                    raise _invalid(f'selections.{index}.item', f'{len(matches)} open lines carry this item; name the line_id')
                line_id = matches[0]
            if any(existing.line_id == line_id for existing in chosen):
                raise _invalid(f'selections.{index}', f'line {line_id} is already selected; name each order line once')
            chosen.append(BillingSelection(line_id=line_id, quantity=entry.quantity))
        return _Choice(selections=chosen)
    stocked = [row for row in open_rows if row[1].profile.item_type in TRACKED_TYPES]
    others = [row for row in open_rows if row[1].profile.item_type not in TRACKED_TYPES]
    available = commitments.available_to(s, {facts.item_id for _, facts, _ in stocked}, excluding=header['id'])
    left = dict(available)
    selections, shipped = [], {}
    for line, facts, remaining in stocked:
        factor = facts.unit_factor_nanounits
        can = max(0, left.get(facts.item_id, 0))
        quantity = min(remaining, Fraction(can * 1_000_000_000 // factor, 1_000_000))
        micro = _micro(quantity)
        if not micro or micro <= 0:
            continue
        left[facts.item_id] = left.get(facts.item_id, 0) - (base_quantity(micro, factor))
        shipped[line['line_id']] = quantity
        selections.append(_WholeLine(line['line_id']) if quantity == remaining
                          else BillingSelection(line_id=line['line_id'], quantity=_plain(micro)))
    if stocked and not selections:
        raise BookflowError('E_VALIDATION', details=dict(problem='nothing on this order can ship', lines=[
            dict(line_id=line['line_id'], item=facts.profile.item.label, open=_quantity(_micro(remaining) or 0),
                 available=_quantity(available.get(facts.item_id, 0)))
            for line, facts, remaining in stocked],
            choices=['all_open: true bills every open quantity whatever the stock',
                     'line_ids bills whole open remainders', 'selections names a quantity per line']))
    selections += [_WholeLine(line['line_id']) for line, facts, remaining in others]
    for line, facts, remaining in others:
        shipped[line['line_id']] = remaining
    if not selections:
        return _Choice()
    return _Choice(selections=selections, shipped=shipped)


def _plain(micro):
    from bookflow.core.exact import format_quantity_micro_units
    return format_quantity_micro_units(micro)


def _defaulted(origins, field):
    origin = (origins or {}).get(field)
    return origin is None or origin.kind == 'default'


def invoice_day_profile(s, rev, profile):
    """The order's header as an ordinary invoice of today would tax it.

    The customer tax code and sales tax item the order took by default are resolved again from the
    customer and the company; ones a person chose carry. Either way the tax item's rates are read as
    they stand now: Bookflow keeps no dated rates, so an invoice is taxed at its own day's rates.
    """
    from bookflow.company import sales_defaults as sd, work
    db = s.company
    info = sd._info(db)
    captured = work.facts(rev).profile
    origins = captured.origins
    customer = sd._row(db, 'customer', captured.customer.id, active=False)
    code = captured.customer_tax_code
    if _defaulted(origins, 'customer_tax_code'):
        selector = customer['sales_tax_code_id']
        code = sd._capture(sd._row(db, 'customer_tax_code', selector, active=False), 'customer_tax_code') if selector else None
    item, rules = None, None
    selector = captured.sales_tax_item.id if captured.sales_tax_item else None
    if _defaulted(origins, 'sales_tax_item'):
        selector = customer['sales_tax_item_id'] or info['default_sales_tax_item_id']
    # The company's sales tax setting and calculation policy as they stand today, unless a person
    # chose the policy on the order: an ordinary invoice of today would take both from the company.
    enabled = bool(info['sales_tax_enabled'])
    if enabled and selector:
        item, rules = sd._tax_rules(db, selector)
    update = dict(customer_tax_code=code, sales_tax_item=item, tax_rules=rules,
                  preferences=profile.preferences.model_copy(update=dict(sales_tax_enabled=enabled)))
    from bookflow.company import tax_policy
    if getattr(profile, 'schema_version', 1) == 2 and tax_policy.origin(captured).kind != 'explicit':
        update.update(sales_tax_calculation=info['sales_tax_calculation'],
                      tax_policy_origin=tax_policy.TaxOrigin(kind='default', source_id=info['id']))
    return profile.model_copy(update=update)


def line_tax(s, lf, header):
    """(tax code, tax rules) for an order line on an invoice of today, under `header` from invoice_day_profile."""
    from bookflow.company import sales_defaults as sd
    from bookflow.company.sales_adjustments import kind
    db = s.company
    code = lf.profile.tax_code
    if _defaulted(lf.profile.origins, 'tax_code') and kind(lf.profile) != 'subtotal':
        item = sd._row(db, 'item', lf.item_id, active=False)
        selector = item['sales_tax_code_id'] or (header.customer_tax_code.id if header.customer_tax_code else None)
        code = sd._capture(sd._row(db, 'tax_code', selector, active=False), 'tax_code') if selector else None
    exempt = header.customer_tax_code is not None and not header.customer_tax_code.taxable
    taxable = (header.preferences.sales_tax_enabled and code is not None and code.taxable and not exempt
               and kind(lf.profile) in ('item', 'charge'))
    if taxable and not header.tax_rules:
        raise sd._missing_tax_item(db, header)
    return code, (list(header.tax_rules) if taxable else [])


def retax(s, header):
    """The billed-line hook that taxes an order line as an ordinary invoice of today would."""
    from bookflow.company.sales_facts import SalesLineProfile

    def apply(entry, lf):
        code, rules = line_tax(s, lf, header)
        entry['profile'] = SalesLineProfile.model_validate(dict(entry['profile'].model_dump(),
                                                                tax_code=code.model_dump() if code else None))
        entry['taxes'] = [dict(rule=rule, taxable_minor_units=entry['net_minor_units'], tax_minor_units=0)
                          for rule in rules]
    return apply


def quoted_tax_difference(selected, invoice_taxes, currency):
    """Per billed line and in total, the invoice's tax against the order's quoted tax for the part billed.

    `invoice_taxes` is each selected line's tax on the invoice, in the same order."""
    from bookflow.company import billing_math as math
    from bookflow.company.sales_outputs import OrderTaxDifference, OrderTaxLine
    out, quoted_total, invoice_total = [], 0, 0
    for item, invoiced in zip(selected, invoice_taxes, strict=True):
        lf = item.facts
        d = math.denominator(lf.quantity_microunits, lf.net_minor_units)
        spans = item.spans or ((0, d),)
        quoted = sum(math.portion(lf.tax_minor_units, a, b, d) for a, b in spans)
        quoted_total += quoted
        invoice_total += invoiced
        out.append(OrderTaxLine(line_id=item.line['line_id'], quoted_tax=Money(quoted, currency).to_dict(),
                                invoice_tax=Money(invoiced, currency).to_dict(),
                                difference=Money(invoiced - quoted, currency).to_dict()))
    return OrderTaxDifference(lines=out, quoted_tax=Money(quoted_total, currency).to_dict(),
                              invoice_tax=Money(invoice_total, currency).to_dict(),
                              difference=Money(invoice_total - quoted_total, currency).to_dict())


# ------------------------------------------------------------------ steering notes (§4: agents bill from invoices)

def order_note(s, customer_id, context, output=None):
    """Advice for invoice post, sales-receipt post and payment receive: the customer's open orders.

    For a sale whose stocked items all sit on one open order, the note gives the exact command that
    bills the order instead; for a payment that would sit unapplied while the customer has an open
    order, work order or accepted estimate, it says how to take it as a deposit. Never a refusal.
    """
    from bookflow.company import commitments, work
    rows = commitments.open_lines(s, stocked_only=False, kinds=('sales_order',))
    rows = [row for row in rows if row.customer_id == customer_id]
    currency = s.company_info_row['home_currency'] if hasattr(s, 'company_info_row') else 'USD'
    if context in ('invoice', 'sales_receipt'):
        if not rows:
            return None
        orders = {}
        for row in rows:
            entry = orders.setdefault(row.number, dict(document_id=row.document_id, lines=0, amount=0, items=set()))
            entry['lines'] += 1
            entry['amount'] += row.open_net
            entry['items'].add(row.item_id)
        sold = {line.item_id for line in getattr(getattr(output, 'revision', None), 'lines', []) or []}
        listed = '; '.join(f"sales order {number} ({entry['lines']} open lines, {Money(entry['amount'], currency).to_dict()['amount']} open)"
                           for number, entry in orders.items())
        matching = [number for number, entry in orders.items() if sold and sold <= entry['items']]
        if matching:
            number = matching[0]
            header = work.resolve(s, orders[number]['document_id'], 'sales_order')
            date = getattr(getattr(output, 'revision', None), 'date', None) or '<date>'
            return (f'This customer has {listed}. These lines match sales order {number}: to bill the order (so its '
                    f'backorder, commitment and any deposit follow), use `sales-order invoice {number} --expected-version '
                    f'{header["version"]} --conversion-key <new key> --date {date}` instead.')
        return f'This customer has {listed}; bill shipped order lines with `sales-order invoice` so the order follows.'
    if context == 'payment':
        unapplied = (getattr(output, 'unapplied_minor_units', None)
                     or getattr(getattr(output, 'current', None), 'available_minor_units', None))
        if not unapplied:
            return None
        t = c.work_documents
        holding = work.rows(s, t, t.c.active.is_(True), t.c.status.in_(('open', 'accepted', 'draft', 'scheduled', 'in_progress', 'on_hold', 'complete')),
                            t.c.kind.in_(('sales_order', 'work_order', 'estimate')))
        mine = []
        for header in holding:
            if header['kind'] == 'estimate' and header['status'] != 'accepted':
                continue
            if header['kind'] == 'sales_order' and header['status'] != 'open':
                continue
            if work.facts(work.revision(s, header)).profile.customer.id == customer_id:
                mine.append(header)
        if not mine:
            return None
        first = mine[0]
        noun = first['kind'].replace('_', '-')
        return (f'Part of this payment is unapplied while the customer has {first["kind"].replace("_", " ")} {first["number"]} '
                f'open. If it is a deposit on that work, take it with `prepayment receive --document "{noun} {first["number"]}"` '
                'instead, so it is held for that work.')
    return None


def discount_cuts(total, shares, taxed, amounts, taxable):
    """A billed discount's cut from taxable sales per target position, or None, by the ordinary rule.

    The QuickBooks rule ordinary invoices follow (`sales_adjustments.apply`): a taxable discount over
    taxable and non-taxable lines reduces taxable sales by the whole discount, taken from the taxable
    lines by their bases; otherwise each share is its own cut. `shares`, `taxed` and `amounts` are
    keyed by target position.
    """
    from bookflow.company.sales_adjustments import distribute
    if not taxable or not total or all(taxed.get(j) for j, share in shares.items() if share):
        return None
    bases = {j: amounts[j] for j in shares if taxed.get(j) and amounts[j] > 0}
    if not bases:
        return {j: 0 for j in shares}
    cuts = dict(bases) if total >= sum(bases.values()) else distribute(total, bases)
    return {j: cuts.get(j, 0) for j in shares}


def recut_discounts(lines):
    """Re-derive each billed discount's taxable cut from the billed lines' current taxability."""
    from bookflow.company.sales_adjustments import kind
    for line in lines:
        adjustment = line['profile'].adjustment
        if adjustment is None or kind(line['profile']) != 'discount' or adjustment.applies_to != 'billed':
            continue
        shares = {t.position: t.amount_minor_units for t in adjustment.targets}
        taxed = {j: bool(lines[j - 1].get('adjustment_taxable')) for j in shares}
        amounts = {j: lines[j - 1]['net_minor_units'] + (shares[j] if lines[j - 1]['profile'].pricing_basis == 'allocated' else 0)
                   for j in shares}
        cuts = discount_cuts(sum(shares.values()), shares, taxed, amounts, bool(line.get('adjustment_taxable')))
        for target in adjustment.targets:
            target.taxable_minor_units = None if cuts is None else cuts[target.position]


def backdating_warnings(s, inp, header):
    """An order invoice dated in the past: what-can-ship reads today's stock (warning only)."""
    from bookflow.core import clock
    if inp.all_open or inp.line_ids or inp.selections or inp.date >= clock.today_iso():
        return []
    return [f'This invoice is dated {inp.date} but what can ship was read from stock as it stands today; '
            'name the quantities (selections) if that differs from what shipped then.']


def prepayment_date_warning(s, header, preview, date):
    """Money held on the order but received after the invoice date was not applied: say so."""
    from bookflow.company import prepayments
    if prepayments.find_account(s.company) is None:
        return None
    applied = getattr(preview, 'prepayment_applied', None)
    if applied is not None and applied.minor_units:
        return None
    later = [row for row in prepayments.receipt_rows(s, document_ids=prepayments._pool_documents(s, header))
             if row['held'] > 0 and row['date'] > date]
    if not later:
        return None
    return (f'Customer prepayment receipt {", ".join(row["number"] for row in later)} is dated after this invoice '
            f'({date}), so none of it was applied; date the invoice on or after the receipt, or apply it later '
            'with `prepayment apply`.')
