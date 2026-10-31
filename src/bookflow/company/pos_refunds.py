"""POS returns compose the ordinary credit memo and refund inside one writer transaction."""
import hashlib
import json
import sqlalchemy as sa
from bookflow.company import schema as c, pos_layer, pos_sales, pos_void, sales, credits, refunds
from bookflow.company import pos_refund_models as m
from bookflow.company.credit_models import CreditMemoPostInput
from bookflow.company.refund_models import CustomerRefundPostInput
from bookflow.company.pos_models import Named, PaymentMethodRef
from bookflow.core import clock, audit, unit_price
from bookflow.core.exact import format_quantity_micro_units as quantity, round_ratio_half_even
from bookflow.core.ids import new_id
from bookflow.core.money import Money
from bookflow.core.errors import BookflowError
from bookflow.core.registry import Plan, Applied, Touched


def authorize(inp, ctx, s):
    pos_layer.require_enabled(s)
    pos_layer.require_seller(s, 'pos.refund')


def _invalid(field, problem):
    return BookflowError('E_VALIDATION', message=problem, details={'fields': [{'field': field, 'problem': problem}]})


def _hash(inp):
    value = inp.model_dump(mode='json', exclude={'expected_total'})
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _replay(s, inp):
    row = s.company.conn.execute(sa.select(c.pos_refunds).where(
        c.pos_refunds.c.operation_key == inp.operation_key)).mappings().first()
    if row is None:
        return None
    if row['input_sha256'] != _hash(inp):
        raise BookflowError('E_IDEMPOTENCY_MISMATCH', message='This operation key already completed a different return. '
                            'Read that refund before starting another.', details={'refund_id': row['id']})
    out = m.PosRefundOutput.model_validate_json(row['result_json'])
    return out.model_copy(update={'replayed': True})


def resolve(s, selector):
    p, t = c.pos_refunds, c.transactions
    rows = s.company.conn.execute(sa.select(p).join(t, t.c.id == p.c.credit_memo_id).where(sa.or_(
        p.c.id == selector, p.c.operation_key == selector, p.c.credit_memo_id == selector,
        p.c.refund_id == selector, t.c.number == selector)).limit(2)).mappings().all()
    if not rows:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'pos_refund', 'selector': selector})
    if len(rows) != 1:
        raise _invalid('refund', 'This selector names more than one refund; use its POS refund ID.')
    return dict(rows[0])


def show(s, inp):
    return m.PosRefundOutput.model_validate_json(resolve(s, inp.refund)['result_json'])


def _source(s, selector):
    found = pos_void.resolve(s, selector)
    sale = dict(s.company.conn.execute(sa.select(c.pos_sales).where(c.pos_sales.c.id == found['sale_id'])).mappings().one())
    header = s.company.conn.execute(sa.select(c.transactions).where(c.transactions.c.id == sale['document_id'])).mappings().one()
    if header['status'] != 'posted':
        raise BookflowError('E_WORK_DEPENDENCY', message=f"Sale {header['number']} is {header['status']} and cannot be refunded.",
                            details={'sale_id': sale['id'], 'status': header['status']})
    revision = s.company.conn.execute(sa.select(c.transaction_revisions).where(
        c.transaction_revisions.c.id == header['current_revision_id'])).mappings().one()
    if revision['revision_number'] != 1:
        raise BookflowError('E_WORK_DEPENDENCY', message='This sale was corrected after checkout. Ask the bookkeeper to '
                            'return its corrected lines with a credit memo.', details={'sale_id': sale['id']})
    return sale, header, revision


def lines(s, inp):
    sale, header, revision = _source(s, inp.sale)
    reg = pos_sales.register(s, sale['register_id'])
    cur = revision['currency']
    result = []
    from bookflow.company.credit_returns import remaining
    for line in sales.saved_lines(s, revision):
        facts = json.loads(line['item_snapshot'])
        base = line['base_quantity_microunits']
        if facts['item_type'] in ('subtotal', 'discount') or not base or base <= 0:
            continue
        claims = credits.active_source_claims(s, sale['document_id'], line['line_id'])
        available = remaining([(r['start_microunits'], r['end_microunits']) for r in claims], base)
        factor = line['unit_factor_nanounits']
        result.append(m.RefundLineOutput(source_line=line['line_id'], item_id=line['item_id'],
            item_name=facts['item']['label'], quantity=quantity(line['quantity_microunits']),
            tracking=s.company.conn.execute(sa.select(c.items.c.tracking).where(c.items.c.id == line['item_id'])).scalar_one() or 'none',
            available_quantity=quantity(available * 1000000000 // factor),
            unit_price=unit_price.to_dict(unit_price.row_units(line, 'unit_price'), cur)
                if line['unit_price_minor_units'] is not None else None,
            net=Money(line['net_minor_units'], cur).to_dict(), tax=Money(line['tax_minor_units'], cur).to_dict()))
    customer = s.company.conn.execute(sa.select(c.customers).where(c.customers.c.id == revision['name_id'])).mappings().one()
    method = json.loads(sale['tenders'])[0]['payment_method_id']
    method_row = s.company.conn.execute(sa.select(c.payment_methods).where(c.payment_methods.c.id == method)).mappings().one()
    return m.PosRefundLinesOutput(sale_id=sale['id'], receipt_number=('INV:' if sale['document_type'] == 'invoice' else '') + header['number'],
        pos_register=Named(id=reg['id'], name=reg['name']), customer=_customer(s, customer) if sale['customer_named'] else None,
        payment_method=PaymentMethodRef(id=method, name=method_row['name'], kind=method_row['kind']), lines=result)


def _walk_in(s, row):
    return bool(row['pos_walk_in'] or s.company.conn.execute(sa.select(c.pos_registers.c.id).where(
        c.pos_registers.c.walk_in_customer_id == row['id']).limit(1)).scalar())


def _customer(s, row):
    return None if _walk_in(s, row) else Named(id=row['id'], name=row['full_name'])


def _unlinked_cost(s, item, wanted, date):
    """Current company-wide average, or captured purchase cost when no positive stock exists."""
    t = c.inventory_movements
    stock, value = s.company.conn.execute(sa.select(sa.func.coalesce(sa.func.sum(t.c.quantity_microunits), 0),
        sa.func.coalesce(sa.func.sum(t.c.value_minor_units), 0)).where(t.c.item_id == item['id'],
        t.c.effective_date <= date)).one()
    if stock > 0 and value >= 0:
        return round_ratio_half_even(value * wanted, stock)
    else:
        return round_ratio_half_even((unit_price.row_units(item, 'cost') or 0) * wanted, 1000000 * unit_price.SCALE)


def _payouts(s, sale, reg, inp, total):
    if inp.settlement == 'store_credit':
        return []
    if not reg['refund_account_id']:
        raise _invalid('pos_register', 'Set a cash refund bank account on this register before refunding money.')
    refunds._funding(s, reg['refund_account_id'], total.currency)
    if inp.settlement == 'cash':
        offered = [row for row in pos_sales._offered(s, reg) if row['active'] and row['kind'] == 'cash']
        if not offered:
            raise _invalid('settlement', 'This register offers no active cash payment method.')
        allocations = [(None, offered[0]['id'], total.minor_units)]
    else:
        tenders = json.loads(sale['tenders'])
        spent = {}
        for raw in s.company.conn.execute(sa.select(c.pos_refunds.c.result_json).where(
                c.pos_refunds.c.sale_id == sale['id'])).scalars():
            previous = json.loads(raw)
            if previous['settlement'] != 'original':
                continue
            for tender in previous.get('tenders') or [dict(tender_index=0, amount=previous['total'])]:
                index = tender['tender_index']
                spent[index] = spent.get(index, 0) + tender['amount']['minor_units']
        allocations, left = [], total.minor_units
        for index, tender in enumerate(tenders):
            paid = tender.get('applied_minor_units', tender['amount_minor_units'] -
                              (sale['change_minor_units'] if tender['kind'] == 'cash' else 0))
            amount = min(left, max(0, paid - spent.get(index, 0)))
            if amount:
                allocations.append((index, tender['payment_method_id'], amount))
                left -= amount
            if not left:
                break
        if left:
            raise _invalid('settlement', 'The original tenders have no remaining capacity for this refund. Ask the bookkeeper to resolve earlier returns.')
    result = []
    for index, method_id, amount in allocations:
        method = refunds._method(s, method_id)
        result.append(m.RefundTenderOutput(tender_index=index,
            payment_method=PaymentMethodRef(id=method.id, name=method.label, kind=method.kind),
            amount=Money(amount, total.currency).to_dict(), reference=inp.reference))
    return result


def prepare(s, ctx, inp):
    replay = _replay(s, inp)
    if replay:
        return Plan(replay, {'replay': True, 'input': inp})
    sale = None
    if inp.sale:
        sale, header, revision = _source(s, inp.sale)
        reg = pos_sales.register(s, sale['register_id'])
        if inp.pos_register and pos_sales.register(s, inp.pos_register)['id'] != reg['id']:
            raise _invalid('pos_register', 'A receipt return uses the original sale register.')
        customer = pos_sales.customer(s, revision['name_id'])
    else:
        reg = pos_sales.register(s, inp.pos_register)
        customer = pos_sales.customer(s, inp.customer or reg['walk_in_customer_id'])
    if inp.settlement == 'store_credit' and (_walk_in(s, customer) or (sale and not sale['customer_named'])):
        raise _invalid('settlement', 'Store credit needs a named customer. The walk-in customer is shared by anonymous '
                       'sales, so credit left there cannot belong to this shopper. Choose cash or a named customer.')
    from bookflow.company import pos_day
    pos_day.active_shift(s, reg)
    date = pos_sales._today(s)
    currency = s.company.conn.execute(sa.select(c.company_info.c.home_currency)).scalar_one()
    entries, cost_items = [], {}
    from bookflow.company import items, inventory
    for line in inp.lines:
        entry = {'quantity': line.quantity, 'site': reg['site_id']}
        if line.source_line:
            entry.update(source_invoice=sale['document_id'], source_line=line.source_line)
        else:
            item = items.resolve_item(s.company, line.item)
            if item['type'] in ('group', 'subtotal', 'discount'):
                raise _invalid('lines.item', 'Choose the individual returned items, not groups, subtotals or discounts.')
            entry['item'] = item['id']
            if line.unit_price is not None:
                entry['unit_price'] = line.unit_price
            if item['type'] in inventory.TRACKED_TYPES:
                cost_items[len(entries)] = item
                entry['return_cost'] = '0.00'
        if line.serial_numbers is not None:
            entry['serial_numbers'] = line.serial_numbers
        entries.append(entry)
    credit_input = CreditMemoPostInput(customer=customer['id'], date=date, lines=entries)
    credit = credits.prepare(s, ctx, credit_input)
    if cost_items:
        # Resolve the item's selling unit through the credit core before valuing base stock.
        # A case returned at its current price brings every unit of the case back.
        for index, item in cost_items.items():
            base = credit.preview.revision.lines[index].base_quantity_microunits
            entries[index]['return_cost'] = Money(_unlinked_cost(s, item, base, date), currency).amount
        credit_input = CreditMemoPostInput(customer=customer['id'], date=date, lines=entries)
        credit = credits.prepare(s, ctx, credit_input)
    source_prices = {}
    if sale:
        for line in sales.saved_lines(s, revision):
            if line['unit_price_minor_units'] is not None:
                source_prices[line['line_id']] = unit_price.to_dict(unit_price.row_units(line, 'unit_price'), currency)
    total = credit.preview.total
    if total.minor_units <= 0:
        raise _invalid('lines', 'A POS return must have a positive total.')
    if inp.expected_total is not None and Money.parse(inp.expected_total, currency).minor_units != total.minor_units:
        raise BookflowError('E_PREVIEW_STALE', details={'total': total.amount, 'expected_total': inp.expected_total})
    payouts = _payouts(s, sale, reg, inp, total)
    method = payouts[0].payment_method if payouts else None
    out = m.PosRefundOutput(id=new_id(), operation_key=inp.operation_key, sale_id=sale['id'] if sale else None,
        pos_register=Named(id=reg['id'], name=reg['name']), customer=_customer(s, customer) if not sale or sale['customer_named'] else None,
        credit_memo=Named(id=credit.preview.id, name=credit.preview.number), date=date, created_at=clock.now_iso(),
        clerk_id=s.actor.id, settlement=inp.settlement, payment_method=method, reference=inp.reference, tenders=payouts,
        lines=[m.RefundLineOutput(source_line=line.source_line_id, item_id=line.item_id,
            item_name=line.item_snapshot.item.label, quantity=line.quantity, unit_price=source_prices.get(line.source_line_id) or (line.unit_price.model_dump() if line.unit_price else None),
            net=line.net.model_dump(), tax=line.tax.model_dump()) for line in credit.preview.revision.lines],
        subtotal=credit.preview.subtotal.model_dump(), tax=credit.preview.tax.model_dump(), total=total.model_dump(), warnings=credit.preview.warnings)
    return Plan(out, {'input': inp, 'credit': credit, 'register': reg, 'stock': credit.data.get('stock')})


def apply(plan, ctx, s):
    fresh = prepare(s, ctx, plan.data['input'])
    if fresh.data.get('replay'):
        return Applied(fresh.preview, [], 'no change')
    if fresh.preview.total != plan.preview.total:
        raise BookflowError('E_PREVIEW_STALE', details={'total': fresh.preview.total.amount})
    credit = credits.apply(fresh.data['credit'], ctx, s, command_name='pos refund')
    out = fresh.preview.model_copy(update={'credit_memo': Named(id=credit.output.id, name=credit.output.number)})
    payments = []
    for tender in out.tenders:
        refund_input = CustomerRefundPostInput(date=out.date, sources=[{
            'credit_memo': credit.output.id, 'amount': tender.amount.amount}],
            funding_account=fresh.data['register']['refund_account_id'], method=tender.payment_method.id,
            reference=tender.reference)
        paid = refunds.apply(refunds.prepare(s, ctx, refund_input, 'post',
            pos_credit=(credit.output.id, ctx.request_id)), ctx, s, command_name='pos refund')
        tender.refund = Named(id=paid.output.id, name=paid.output.number)
        payments.append(paid)
    out.refund = out.tenders[0].refund if out.tenders else None
    from bookflow.company import pos_day
    shift = pos_day.active_shift(s, fresh.data['register'])
    row = dict(shift_id=shift['id'] if shift else None, id=out.id, operation_key=out.operation_key, input_sha256=_hash(fresh.data['input']),
        register_id=out.pos_register.id, sale_id=out.sale_id, credit_memo_id=out.credit_memo.id,
        refund_id=out.refund.id if out.refund else None, customer_named=out.customer is not None,
        created_at=out.created_at, created_by=s.actor.id, created_via=ctx.interface.value,
        result_json=out.model_dump_json())
    s.company.conn.execute(c.pos_refunds.insert().values(**row))
    summary = f'POS refund {out.credit_memo.name} on register {out.pos_register.name}'
    audit.write_event_to(s.company, ctx, 'pos refund', summary,
        [Touched('pos_refund', out.id, 'create', None, 1, row, db='company')],
        actor_id=s.actor.id, actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None))
    hooks = [a.after_commit for a in (credit, *payments) if a and a.after_commit]
    def after_commit():
        for hook in hooks:
            hook()
    return Applied(out, [], summary, audited=True, after_commit=after_commit if hooks else None)
