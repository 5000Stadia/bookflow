"""Ringing up and completing a POS sale (R114 increment 5, design/point-of-sale.md §3, §4).

**Same books as typing.** A POS sale is a sales receipt entered through a touch screen. `pos sale preview` and
`pos sale complete` turn the ticket into exactly the input `sales-receipt post` takes and run the same core
(``sales.prepare``, then ``sales.persist_prepared``), with the facts the register fixes: the customer is the chosen
one or the register's walk-in, ``deposit_to`` the register's deposit account, ``site`` the register's site, ``date``
today in the company's timezone, and the payment method one the register offers. So `pos.sell` alone can post a sale
and reach no other account, date or document. A price typed over an item's own price needs `pos.price-override` as well; an item with no price (0.00)
takes one typed at the till.

**Nothing lost, nothing doubled.** The ticket's key is the operation key. ``pos_sales`` holds it (unique), written
in the same transaction as the receipt; a retry with the same key and ticket returns the sale already posted
(``replayed``), and the same key with a different ticket is refused (E_IDEMPOTENCY_MISMATCH).

**Tender.** Single tender in this increment. Cash takes at least the total and gives the difference as change; any
other method takes the total, with an optional reference stored as the receipt's payment reference. Tendered and change
are facts on ``pos_sales``, never postings: the receipt debits the deposit account for the sale total.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import sqlalchemy as sa
from pydantic import ValidationError

from bookflow.company import pos_layer, schema as c
from bookflow.company.pos_models import Named, PaymentMethodRef
from bookflow.company.pos_sale_models import (CustomerAddOutput, CustomerSearchOutput, PosCustomer, PosLineOutput,
                                              PosSaleOutput, PosSalePreviewOutput, TenderOutput)
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.money import Money
from bookflow.core.registry import Applied, Plan, Touched

COMMAND = 'pos sale complete'
OVERRIDE = 'pos.price-override'


def _invalid(field: str, problem: str, **details) -> BookflowError:
    return BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': problem}], **details})


# ---------------------------------------------------------------------------- the register's fixed facts

def register(s, selector) -> dict[str, Any]:
    """The register named, or the company's only one."""
    if selector:
        return pos_layer.resolve(s, selector)
    t = c.pos_registers
    rows = s.company.conn.execute(sa.select(t).order_by(t.c.name_key).limit(2)).mappings().all()
    if len(rows) != 1:
        raise _invalid('pos_register', 'name the register: this company has more than one' if rows else
                       'the company has no register yet')
    return dict(rows[0])


def _offered(s, reg) -> list[dict[str, Any]]:
    t = c.payment_methods
    ids = json.loads(reg['payment_method_ids'])
    found = {r['id']: dict(r) for r in s.company.conn.execute(
        sa.select(t.c.id, t.c.name, t.c.kind, t.c.active).where(t.c.id.in_(ids))).mappings()}
    return [found[i] for i in ids if i in found]


def payment_method(s, reg, selector) -> dict[str, Any]:
    offered = _offered(s, reg)
    if selector is None:
        live = [m for m in offered if m['active']]
        if not live:
            raise _invalid('payment_method', f"register {reg['name']} offers no active payment method")
        return live[0]
    row = pos_layer._method(s, selector)
    if row['id'] not in {m['id'] for m in offered}:
        raise _invalid('payment_method', f"register {reg['name']} does not offer {row['name']}",
                       offered=[m['name'] for m in offered])
    return dict(row)


def customer(s, selector) -> dict[str, Any] | None:
    if selector is None:
        return None
    from bookflow.company import parties
    row = parties.resolve_party(s.company, 'customer', selector)
    if not row.get('active'):
        raise BookflowError('E_INACTIVE_REFERENCE', details={'field': 'customer', 'record_id': row['id']})
    return dict(row)


def _today(s) -> str:
    from bookflow.company.memorized_schedule import today
    return today(s.company_tz)


def receipt_payload(s, inp, reg, method, chosen, reference=None) -> dict[str, Any]:
    """The `sales-receipt post` input this ticket is: the twin a person would type into the sales receipt form."""
    lines = []
    for line in inp.lines:
        entry: dict[str, Any] = {'item': line.item, 'quantity': line.quantity}
        if line.unit_price is not None:
            entry['unit_price'] = line.unit_price
        if line.serial_numbers:
            entry['serial_numbers'] = list(line.serial_numbers)
        lines.append(entry)
    payload = dict(customer=chosen['id'] if chosen else reg['walk_in_customer_id'], date=_today(s),
                   deposit_to=reg['deposit_account_id'], payment_method=method['id'], site=reg['site_id'],
                   lines=lines)
    if reference:
        payload['payment_reference'] = reference
    return payload


def _receipt_input(payload):
    from bookflow.company.sales_models import SalesReceiptPostInput
    try:
        return SalesReceiptPostInput.model_validate(payload)
    except ValidationError as exc:
        raise BookflowError('E_VALIDATION', details={'fields': [
            {'field': '.'.join(map(str, error['loc'])) or 'input', 'problem': error['msg']}
            for error in exc.errors(include_url=False)]}) from None


def authorize(inp, ctx, s) -> None:
    """Before planning: the switch, a seller, and a typed price's own capability."""
    pos_layer.require_enabled(s)
    pos_layer.require_seller(s)
    # A posted ticket is read-only: planning returns it or names the mismatched receipt.
    if getattr(inp, 'ticket', None) and _existing(s, inp.ticket) is not None:
        return
    typed = [line for line in getattr(inp, 'lines', ()) or () if line.unit_price is not None]
    if not typed:
        return
    if any(_has_price(s, line.item) for line in typed):
        require_price_override(s, ctx)
        return
    # A zero item price may still inherit a nonzero customer level or price rule.
    # Resolve the same defaulted receipt lines, without the clerk's typed prices,
    # so quantity breaks and the customer's defaults use the sales engine itself.
    from bookflow.company import sales_defaults
    from bookflow.core import unit_price
    reg = register(s, inp.pos_register)
    chosen = customer(s, inp.customer)
    payload = receipt_payload(s, inp, reg, payment_method(s, reg, None), chosen)
    for line in payload['lines']:
        line.pop('unit_price', None)
    entered = _receipt_input(payload)
    header, _ = sales_defaults.resolve_header(s, entered, 'sales_receipt')
    for original, line in zip(inp.lines, entered.lines):
        if original.unit_price is None:
            continue
        resolved, _ = sales_defaults.resolve_line(
            s, line, header, date=payload['date'], defer_tax=True, line_kinds=True)
        if unit_price.row_units(resolved, 'unit_price'):
            require_price_override(s, ctx)
            return


def _has_price(s, selector) -> bool:
    """Use the exact stored unit price, including fractions smaller than a cent."""
    from bookflow.company.items import resolve_item
    from bookflow.core import unit_price
    try:
        return bool(unit_price.row_units(resolve_item(s.company, selector), 'price'))
    except BookflowError as exc:
        if exc.code in ('E_RECORD_NOT_FOUND', 'E_VALIDATION'):
            return False   # the sale's own validation names it
        raise


def require_price_override(s, ctx) -> None:
    from bookflow.hub import access, capabilities
    capabilities.require_company(s, COMMAND, OVERRIDE, field='lines.unit_price')
    access.require_resource(s, OVERRIDE, 'member')


# ---------------------------------------------------------------------------- preview

def _money(value: dict) -> dict:
    return {'amount': value['amount'], 'currency': value['currency'], 'minor_units': value['minor_units']}


def _lines(revision) -> list[PosLineOutput]:
    out = []
    for line in revision.get('lines') or []:
        snap = (line.get('item_snapshot') or {}).get('item') or {}
        out.append(PosLineOutput(position=line.get('position') or len(out) + 1, item_id=line.get('item_id'),
                                 item_name=snap.get('label'), description=line.get('description'),
                                 quantity=line.get('quantity'),
                                 unit_price=_money(line['unit_price']) if line.get('unit_price') else None,
                                 net=_money(line['net']), tax=_money(line['tax'])))
    return out


def _currency(s) -> str:
    return s.company.conn.execute(sa.select(c.company_info.c.home_currency)).scalar_one()


def _zero(s) -> dict:
    return Money(0, _currency(s)).to_dict()


def _sale_plan(s, ctx, payload):
    from bookflow.company import sales
    return sales.prepare(s, ctx, _receipt_input(payload), 'sales_receipt', 'post')


def preview(s, ctx, inp) -> PosSalePreviewOutput:
    reg = register(s, inp.pos_register)
    method = payment_method(s, reg, inp.payment_method)
    chosen = customer(s, inp.customer)
    named = Named(id=chosen['id'], name=chosen['full_name']) if chosen else None
    base = dict(pos_register=Named(id=reg['id'], name=reg['name']), customer=named,
                payment_method=PaymentMethodRef(id=method['id'], name=method['name'], kind=method['kind']),
                date=_today(s))
    if not inp.lines:
        zero = _zero(s)
        return PosSalePreviewOutput(**base, lines=[], subtotal=zero, tax=zero, total=zero)
    plan = _sale_plan(s, ctx, receipt_payload(s, inp, reg, method, chosen))
    out = plan.preview.model_dump(mode='json')
    stock = plan.data.get('stock') if isinstance(plan.data, dict) else None
    warnings = list(out.get('warnings') or [])
    if stock is not None:
        warnings += [w for w in dict.fromkeys(getattr(stock, 'warnings', None) or ()) if w not in warnings]
    return PosSalePreviewOutput(**base, lines=_lines(out.get('revision') or {}), subtotal=_money(out['subtotal']),
                                tax=_money(out['tax']), total=_money(out['total']), warnings=warnings)


# ---------------------------------------------------------------------------- complete

def _input_sha(inp) -> str:
    canonical = json.dumps(inp.model_dump(mode='json', include={'pos_register', 'customer', 'lines'}), sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _existing(s, ticket) -> dict[str, Any] | None:
    t = c.pos_sales
    row = s.company.conn.execute(sa.select(t).where(t.c.ticket == ticket)).mappings().first()
    return dict(row) if row else None


def _replay(s, row, inp) -> PosSaleOutput:
    if row['input_sha256'] != _input_sha(inp):
        raise _mismatch(s, row)
    return sale_output(s, row, replayed=True)


def _mismatch(s, row):
    receipt = sale_output(s, row).sales_receipt
    return BookflowError('E_IDEMPOTENCY_MISMATCH', message=(
            f"Ticket {row['ticket']} already posted as receipt {receipt.name} ({receipt.id}). "
            'This cart differs from that sale. Do not ring it again; inspect the posted receipt.'),
            details={'key': row['ticket'], 'stored_command': COMMAND,
                     'sales_receipt_id': receipt.id, 'sales_receipt_number': receipt.name})


def _minor(amount: str, currency: str) -> int:
    return Money.parse(amount, currency).minor_units


def tender_facts(s, reg, inp, total: dict) -> dict[str, Any]:
    method = payment_method(s, reg, inp.tender.payment_method)
    if not method.get('active', True):
        raise BookflowError('E_INACTIVE_REFERENCE', details={'field': 'tender.payment_method', 'record_id': method['id']})
    currency, due = total['currency'], total['minor_units']
    if due < 0:
        raise _invalid('lines', 'the sale totals less than zero; a return is a refund, which arrives later')
    cash = method['kind'] == 'cash'
    given = _minor(inp.tender.amount, currency) if inp.tender.amount is not None else due
    if cash and given < due:
        raise _invalid('tender.amount', f"{Money(given, currency).amount} is less than the total "
                       f"{Money(due, currency).amount}", total=Money(due, currency).amount)
    if not cash and given != due:
        raise _invalid('tender.amount', f"a {method['name']} payment takes the total, {Money(due, currency).amount}; "
                       'paying part of it another way (split tender) arrives later', total=Money(due, currency).amount)
    return dict(method=method, currency=currency, total=due, tendered=given, change=given - due,
                reference=(inp.tender.reference or '').strip() or None)


def plan_complete(s, ctx, inp) -> Plan:
    found = _existing(s, inp.ticket)
    if found is not None:
        out = _replay(s, found, inp)
        return Plan(out, dict(replay=found['id']))
    if not inp.lines:
        raise _invalid('lines', 'the cart is empty')
    reg = register(s, inp.pos_register)
    chosen = customer(s, inp.customer)
    method = payment_method(s, reg, inp.tender.payment_method)
    payload = receipt_payload(s, inp, reg, method, chosen, (inp.tender.reference or '').strip() or None)
    plan = _sale_plan(s, ctx, payload)
    total = plan.preview.total.model_dump(mode='json') if hasattr(plan.preview.total, 'model_dump') else \
        dict(plan.preview.total)
    if Money.parse(inp.expected_total, total['currency']).minor_units != total['minor_units']:
        raise BookflowError('E_PREVIEW_STALE', message=(
            f"The sale now totals {total['amount']}, not the {inp.expected_total} shown. Nothing was posted; show the "
            'new total and charge again.'), details={'total': total['amount'], 'expected_total': inp.expected_total})
    facts = tender_facts(s, reg, inp, total)
    header = plan.preview
    preview = PosSaleOutput(
        id=new_id(), ticket=inp.ticket, pos_register=Named(id=reg['id'], name=reg['name']),
        sales_receipt=Named(id=header.id, name=header.number), customer=_named_customer(chosen),
        clerk_id=s.actor.id, created_at=clock.now_iso(), date=payload['date'], total=Money(facts['total'], facts['currency']).to_dict(),
        tendered=Money(facts['tendered'], facts['currency']).to_dict(),
        change=Money(facts['change'], facts['currency']).to_dict(), tenders=[_tender_out(facts)],
        warnings=list(header.warnings or []))
    return Plan(preview, dict(payload=payload, register=reg, customer=chosen, facts=facts, sha=_input_sha(inp)))


def _named_customer(chosen) -> Named | None:
    return Named(id=chosen['id'], name=chosen['full_name']) if chosen else None


def _tender_out(facts) -> TenderOutput:
    m = facts['method']
    return TenderOutput(payment_method=PaymentMethodRef(id=m['id'], name=m['name'], kind=m['kind']),
                        amount=Money(facts['tendered'], facts['currency']).to_dict(), reference=facts['reference'])


def apply_complete(plan: Plan, ctx, s) -> Applied:
    from bookflow.company import sales
    from bookflow.core import audit
    inp_sha = plan.data.get('sha')
    if plan.data.get('replay'):
        return Applied(plan.preview, [], 'no change')
    # Inside the write transaction: a retry that raced this one and won is found here, and answered with its sale.
    found = _existing(s, plan.preview.ticket)
    if found is not None:
        if found['input_sha256'] != inp_sha:
            raise _mismatch(s, found)
        return Applied(sale_output(s, found, replayed=True), [], 'no change')
    fresh = sales.prepare(s, ctx, _receipt_input(plan.data['payload']), 'sales_receipt', 'post')
    total = fresh.preview.total
    if total.minor_units != plan.data['facts']['total']:
        raise BookflowError('E_PREVIEW_STALE', details={'total': total.amount})
    applied = sales.persist_prepared(fresh, ctx, s, command_name=COMMAND)
    header = fresh.data['header']
    facts, reg, chosen = plan.data['facts'], plan.data['register'], plan.data['customer']
    row = dict(id=plan.preview.id, created_at=clock.now_iso(), created_by=s.actor.id, created_via=ctx.interface.value,
               ticket=plan.preview.ticket, input_sha256=inp_sha, register_id=reg['id'], document_type='sales_receipt',
               document_id=header['id'], customer_named=chosen is not None, currency=facts['currency'],
               total_minor_units=facts['total'], tendered_minor_units=facts['tendered'],
               change_minor_units=facts['change'], tenders=json.dumps([dict(
                   payment_method_id=facts['method']['id'], kind=facts['method']['kind'],
                   amount_minor_units=facts['tendered'], reference=facts['reference'])]))
    s.company.conn.execute(c.pos_sales.insert().values(**row))
    summary = f"POS sale on register {reg['name']}: sales receipt {header['number']}"
    audit.write_event_to(s.company, ctx, COMMAND, summary,
                         [Touched('pos_sale', row['id'], 'create', None, 1, row, db='company')],
                         actor_id=s.actor.id, actor_kind=s.actor.kind,
                         directive_code=getattr(s, 'directive_code', None))
    output = sale_output(s, row, warnings=list(getattr(applied.output, 'warnings', None) or []))
    return Applied(output, list(applied.touched), summary, audited=True, after_commit=applied.after_commit)


def sale_output(s, row, *, replayed=False, warnings=None) -> PosSaleOutput:
    t, r = c.transactions, c.transaction_revisions
    header = s.company.conn.execute(sa.select(t.c.id, t.c.number, r.c.date, r.c.name_id).select_from(
        t.join(r, r.c.id == t.c.current_revision_id)).where(t.c.id == row['document_id'])).mappings().first()
    reg = s.company.conn.execute(sa.select(c.pos_registers.c.id, c.pos_registers.c.name).where(
        c.pos_registers.c.id == row['register_id'])).first()
    chosen = None
    if row['customer_named']:
        name = s.company.conn.execute(sa.select(c.customers.c.full_name).where(
            c.customers.c.id == header['name_id'])).scalar()
        chosen = Named(id=header['name_id'], name=name or '')
    cur = row['currency']
    methods = {r[0]: r for r in s.company.conn.execute(sa.select(c.payment_methods.c.id, c.payment_methods.c.name,
                                                                  c.payment_methods.c.kind))}
    tenders = []
    for tender in json.loads(row['tenders']):
        m = methods.get(tender['payment_method_id'])
        tenders.append(TenderOutput(payment_method=PaymentMethodRef(
            id=tender['payment_method_id'], name=m[1] if m else '', kind=m[2] if m else tender['kind']),
            amount=Money(tender['amount_minor_units'], cur).to_dict(), reference=tender.get('reference')))
    return PosSaleOutput(id=row['id'], ticket=row['ticket'], pos_register=Named(id=reg[0], name=reg[1]),
                         sales_receipt=Named(id=header['id'], name=header['number']), customer=chosen,
                         clerk_id=row['created_by'], created_at=row['created_at'], date=header['date'],
                         total=Money(row['total_minor_units'], cur).to_dict(),
                         tendered=Money(row['tendered_minor_units'], cur).to_dict(),
                         change=Money(row['change_minor_units'], cur).to_dict(), tenders=tenders, replayed=replayed,
                         warnings=warnings or [])


# ---------------------------------------------------------------------------- customers at the till

def _digits(text: str) -> str:
    return ''.join(ch for ch in text if ch.isdigit())


def _contacts(s, ids) -> dict[str, tuple[str | None, str | None, list[str]]]:
    """customer id -> (phone, email, every phone and email it holds) from its active contacts."""
    if not ids:
        return {}
    k, p = c.customer_contacts, c.customer_contact_points
    found: dict[str, list] = {i: [None, None, []] for i in ids}
    rows = s.company.conn.execute(sa.select(k.c.id, k.c.customer_id, k.c.role, k.c.work_phone, k.c.mobile_phone,
                                            k.c.home_phone, k.c.primary_email, k.c.secondary_email).where(
        k.c.customer_id.in_(ids), k.c.active.is_(True)).order_by(k.c.position, k.c.id)).mappings().all()
    owner = {}
    for r in rows:
        entry = found[r['customer_id']]
        owner[r['id']] = r['customer_id']
        values = [v for v in (r['work_phone'], r['mobile_phone'], r['home_phone'], r['primary_email'],
                              r['secondary_email']) if v]
        entry[2] += values
        if r['role'] == 'primary' or entry[0] is None and entry[1] is None:
            entry[0] = entry[0] or r['work_phone'] or r['mobile_phone'] or r['home_phone']
            entry[1] = entry[1] or r['primary_email']
    if owner:
        for r in s.company.conn.execute(sa.select(p.c.contact_id, p.c.value).where(
                p.c.contact_id.in_(list(owner)), p.c.active.is_(True))):
            found[owner[r[0]]][2].append(r[1])
    return {i: (v[0], v[1], v[2]) for i, v in found.items()}


def search_customers(s, inp) -> CustomerSearchOutput:
    pos_layer.require_enabled(s)
    pos_layer.require_seller(s)
    t = c.customers
    q = inp.q.strip().casefold()
    if len(q) < 3:
        raise _invalid('q', 'enter at least 3 characters')
    # Match in SQLite, including formatted phone numbers; only fetch contacts for the bounded result.
    s.company.conn.connection.driver_connection.create_function('pos_digits', 1, lambda v: _digits(v or ''),
                                                               deterministic=True)
    digits = _digits(q)
    k, p = c.customer_contacts, c.customer_contact_points

    def matches(column):
        text = sa.func.lower(column).contains(q, autoescape=True)
        if len(digits) >= 3:
            return sa.or_(text, sa.func.pos_digits(column).contains(digits, autoescape=True))
        return text

    point_hit = sa.exists(sa.select(p.c.id).where(
        p.c.contact_id == k.c.id, p.c.active.is_(True), matches(p.c.value)))
    contact_hit = sa.exists(sa.select(k.c.id).where(
        k.c.customer_id == t.c.id, k.c.active.is_(True), sa.or_(point_hit, *[
            matches(column) for column in (k.c.work_phone, k.c.mobile_phone, k.c.home_phone,
                                           k.c.primary_email, k.c.secondary_email)])))
    rows = s.company.conn.execute(sa.select(t.c.id, t.c.full_name).where(
        t.c.active.is_(True), t.c.pos_walk_in.is_(False),
        sa.or_(t.c.full_name_key.contains(q, autoescape=True), contact_hit)
    ).order_by(t.c.full_name_key, t.c.id).limit(inp.limit)).all()
    contacts = _contacts(s, [r[0] for r in rows])
    return CustomerSearchOutput(items=[PosCustomer(id=cid, name=name, phone=contacts[cid][0],
                                                   email=contacts[cid][1]) for cid, name in rows])


def plan_add_customer(s, ctx, inp) -> Plan:
    pos_layer.require_enabled(s)
    pos_layer.require_seller(s)
    from bookflow.company import parties
    payload = {'name': inp.name.strip()}
    if inp.phone and inp.phone.strip():
        payload['phone'] = inp.phone.strip()
    if inp.email and inp.email.strip():
        payload['email'] = inp.email.strip()
    mutation = parties.plan_party_create(s.company, 'customer', payload, actor_id=s.actor.id,
                                         via=ctx.interface.value)
    out = CustomerAddOutput(id=str(mutation.after['id']), name=str(mutation.after['full_name']),
                            phone=payload.get('phone'), email=payload.get('email'))
    return Plan(out, dict(mutation=mutation))


def apply_add_customer(plan: Plan, ctx, s) -> Applied:
    from bookflow.company import parties
    mutation = plan.data['mutation']
    parties.persist_party_mutation(s.company, mutation)
    touched = Touched('customer', str(mutation.after['id']), 'create', None, 1, dict(mutation.after_snapshot),
                      db='company')
    return Applied(plan.preview, [touched], f"added customer {mutation.after['name']} at the till")

