"""Customer prepayments (R124): money a customer pays before the work bills, held until it is used.

**The ledger shape.** A deposit, down payment or retainer is a liability, never income: it sits on
the Customer Prepayments account (other current liability, system role ``customer_prepayments``)
until an invoice uses it or it is paid back. The account and its one item, "Customer Prepayment"
(an other charge crediting that account, non-taxable), are made by the first prepayment.

**Every effect is an ordinary document.** Bookflow writes nothing on the account but these:

====================  ===================================================  ======================
effect                document                                             posting
====================  ===================================================  ======================
receive               sales receipt with one Customer Prepayment line      Dr funds; Cr prepayments
draw                  credit memo, one Customer Prepayment line per receipt Dr prepayments; Cr A/R
apply                 ``customer-credit apply`` of the draw to an invoice  settlement only
refund                ``customer-refund post`` against the draw            Dr A/R; Cr bank
====================  ===================================================  ======================

A draw is applied or refunded in the transaction that writes it, so the money never sits as a
stray receivable credit. ``prepayment_links`` says who or what each receipt is held for and
``prepayment_draws`` which receipt each credit memo line drew from (prepayment_schema).

**Held.** Per receipt: its amount less its posted draws. Per document: the sum over the receipts
linked to it, and for a work order also to the estimate it came from (money held on an estimate
serves its billing owner). Per customer: everything held for it, which is the account balance.

**Guards.** Checked inside the write transaction, so a draw racing a receipt void sees the void:
a draw is dated on or after its receipts and never takes more than a receipt holds nor more than
the invoice owes; a drawn receipt cannot be voided, deleted, moved to another customer, redated
after its draws or reduced below what is drawn; a document holding money cannot be voided or
cancelled; the drawing credit memo and its application or refund are undone only together, by
``prepayment unapply``; only an invoice and a draw applied to it move held money to income.

**Assumptions about the anchor.** The anchor's older practice is exactly these documents (a sales
receipt on a prepayment item, the item deducted on the invoice, a credit memo plus a refund to
return it); its current version records the deposit on the order and applies it when invoiced.
Bookflow draws with a credit memo rather than a negative invoice line, because settlement, aging
and cash basis already handle an applied credit, and refuses the receivable-only option.
"""
from __future__ import annotations

import contextlib
import contextvars

import sqlalchemy as sa
from sqlalchemy import alias

from bookflow.company import accounts as account_service, schema as c
from bookflow.company import document_effects as effects, sales_defaults as defaults
from bookflow.company.journal_models import parse_domestic_amount
from bookflow.company.prepayment_models import (
    CustomerPrepaymentsReportOutput, DocumentNumber, DocumentRef, DrawRow, PrepaymentPageOutput,
    PrepaymentReportCustomer, PrepaymentReportRow, PrepaymentShowOutput, PrepaymentWriteOutput, ReceiptRow,
)
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError, require_reason
from bookflow.core.ids import is_ulid, new_id
from bookflow.core.money import Money
from bookflow.core.registry import Applied, Plan, Touched

ROLE = 'customer_prepayments'
ACCOUNT_NAME = 'Customer Prepayments'
ITEM_NAME = 'Customer Prepayment'
#: The customer-work kinds money can be held on. A sales order joins when its kind exists.
HOLDING_KINDS = ('estimate', 'work_order', 'sales_order')
SYNONYMS = 'deposit, down payment, retainer'

_SETUP = contextvars.ContextVar('prepayment_setup', default=False)
_WRITING = contextvars.ContextVar('prepayment_writing', default=False)


def setting_up():
    """True while the first prepayment is making its own item (items._owner_values asks)."""
    return _SETUP.get()


def writing():
    """True while a `prepayment` command writes its own documents, which the guards admit."""
    return _WRITING.get()


@contextlib.contextmanager
def _flag(var):
    token = var.set(True)
    try:
        yield
    finally:
        var.reset(token)


def _invalid(field, problem, **details):
    error = BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': problem}]})
    error.details.update(details)
    return error


def _currency(s):
    return defaults._info(s.company)['home_currency']


def _money(units, currency):
    from bookflow.company.prepayment_models import Money as MoneyModel
    return MoneyModel(**Money(units, currency).to_dict())


def _sale_money(units, currency):
    from bookflow.company.sales_outputs import MoneyOutput
    return MoneyOutput(**Money(units, currency).to_dict())


def _plain(units, currency):
    return Money(units, currency).to_dict()['amount']


def _amount(value, currency, field):
    units = parse_domestic_amount(value, currency, field).minor_units
    if units <= 0:
        raise _invalid(field, 'must be more than zero')
    return units


# ------------------------------------------------------------------ the account and item


def find_account(db):
    row = db.conn.execute(sa.select(c.accounts).where(c.accounts.c.system_role == ROLE)).mappings().first()
    return dict(row) if row is not None else None


def find_item(db, account=None):
    account = account or find_account(db)
    if account is None:
        return None
    row = db.conn.execute(sa.select(c.items).where(c.items.c.income_account_id == account['id'],
                                                   c.items.c.type == 'other_charge').order_by(c.items.c.created_at)).mappings().first()
    return dict(row) if row is not None else None


def item_id(db):
    item = find_item(db)
    return item['id'] if item else None


def _free_name(db, table, name):
    from bookflow.company.lists import normalize_display_name
    for candidate in [name] + [f'{name} {n}' for n in range(2, 50)]:
        _, key = normalize_display_name(candidate)
        if db.conn.execute(sa.select(table.c.id).where(table.c.full_name_key == key)).first() is None:
            return candidate
    raise _invalid('name', f'no free name for the {name} record')


def _set_up_names(s):
    """What the first prepayment creates, in words; empty once the account and item exist."""
    account = find_account(s.company)
    names = []
    if account is None:
        names.append(f'account {_free_name(s.company, c.accounts, ACCOUNT_NAME)} (other current liability)')
    if account is None or find_item(s.company, account) is None:
        names.append(f'item {_free_name(s.company, c.items, ITEM_NAME)} (other charge, non-taxable)')
    return names


def ensure(s, ctx):
    """The account and item, creating either on first use; returns (account, item, touched)."""
    from bookflow.company import items as item_service
    at = clock.now_iso()
    touched = []
    account = find_account(s.company)
    if account is None:
        mutation = account_service.plan_account_create(
            s.company, {'name': _free_name(s.company, c.accounts, ACCOUNT_NAME), 'type': 'other_current_liability',
                        'description': 'Customer deposits held until invoiced or refunded'},
            actor_id=s.actor.id, via=ctx.interface.value, at=at)
        after = dict(mutation.after, system_role=ROLE)
        mutation = account_service.AccountMutation(mutation.action, mutation.before, after)
        account_service.persist_account_mutation(s.company, mutation)
        account = find_account(s.company)
        touched.append(Touched('account', account['id'], 'create', None, account['version'], dict(account), None, db='company'))
    item = find_item(s.company, account)
    if item is None:
        code = s.company.conn.execute(sa.select(c.sales_tax_codes.c.id).where(
            c.sales_tax_codes.c.taxable.is_(False), c.sales_tax_codes.c.active.is_(True)).order_by(c.sales_tax_codes.c.code)).scalar()
        payload = {'name': _free_name(s.company, c.items, ITEM_NAME), 'type': 'other_charge', 'sales_enabled': True,
                   'description': 'Customer prepayment', 'income_account_id': account['id'], 'price': '0'}
        if code is not None:
            payload['sales_tax_code_id'] = code
        with _flag(_SETUP):
            mutation = item_service.plan_item_create(s.company, payload, actor_id=s.actor.id, via=ctx.interface.value, at=at)
        item_service.persist_item_mutation(s.company, mutation)
        touched.append(Touched(item_service.ITEM_RECORD_TYPE, str(mutation.after['id']), mutation.action, None,
                               int(mutation.after['version']), mutation.after_snapshot, mutation.before_snapshot, db='company'))
        item = find_item(s.company, account)
    if not account['active'] or not item['active']:
        raise _invalid('prepayment', f'the "{account["full_name"]}" account or the "{item["full_name"]}" item is inactive; '
                       'make it active again first')
    return account, item, touched


# ------------------------------------------------------------------ documents money is held on


def resolve_document(s, selector):
    t = c.work_documents
    text = selector.strip()
    kinds = HOLDING_KINDS
    if is_ulid(text):
        found = effects.rows(s, t, t.c.id == text.upper(), t.c.kind.in_(kinds))
    else:
        parts = text.split(None, 1)
        kind = parts[0].lower().replace('-', '_') if len(parts) == 2 else None
        if kind in kinds:
            found = effects.rows(s, t, t.c.kind == kind, t.c.number == parts[1].strip())
        else:
            found = effects.rows(s, t, t.c.number == text, t.c.kind.in_(kinds))
    if not found:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'work_document', 'selector': selector})
    if len(found) > 1:
        raise _invalid('document', f'{len(found)} documents carry number {text}; name the kind too, such as '
                       f'"estimate {text}" or "work-order {text}"')
    return found[0]


def document_customer(s, header):
    from bookflow.company import work
    return work.facts(work.revision(s, header)).profile.customer.id


def _ref(header):
    return DocumentRef(id=header['id'], kind=header['kind'], number=header['number'], status=header['status'])


def _holdable(s, header, field='document'):
    kind = header['kind'].replace('_', ' ')
    if header['status'] == 'voided':
        raise _invalid(field, f'{kind} {header["number"]} is voided and can hold no money')
    if header['kind'] == 'estimate' and header['status'] != 'accepted':
        raise _invalid(field, f'estimate {header["number"]} is {header["status"]}; money is held on an estimate once it '
                       'is accepted (`estimate update --status accepted`), or hold it for the customer')
    if header['status'] == 'cancelled':
        raise _invalid(field, f'{kind} {header["number"]} is cancelled and can hold no money')


def _pool_documents(s, header):
    """The documents whose money a document's invoices draw: itself, and for a work order the
    estimate it came from (an estimate's money serves its billing owner)."""
    ids = [header['id']]
    if header['kind'] != 'estimate':
        ids += [row['source_document_id'] for row in effects.rows(
            s, c.work_links, c.work_links.c.destination_document_id == header['id'],
            c.work_links.c.relation.in_(('estimate_work_order', 'estimate_sales_order')))]
    return ids


def _invoice_source_document(s, invoice_id):
    row = s.company.conn.execute(sa.select(c.work_billing_conversions.c.source_document_id).where(
        c.work_billing_conversions.c.destination_transaction_id == invoice_id)).first()
    if row is None:
        return None
    found = effects.rows(s, c.work_documents, c.work_documents.c.id == row[0], c.work_documents.c.kind.in_(HOLDING_KINDS))
    return found[0] if found else None


# ------------------------------------------------------------------ reading what is held


def _current_links():
    l, later = c.prepayment_links, alias(c.prepayment_links, 'later')
    return sa.select(l).where(~sa.exists(sa.select(later.c.id).where(later.c.supersedes_link_id == l.c.id))).subquery('link')


def draw_rows(s, *, receipt_ids=None, credit_memo_ids=None, invoice_ids=None, refund_ids=None, as_of=None,
              include_voided=False):
    """Draws with their amounts, oldest first. A draw counts while its credit memo is posted."""
    d, t, r = c.prepayment_draws, c.transactions, c.transaction_revisions
    dl, clp = c.document_lines, c.credit_line_profiles
    receipt, invoice, refund = alias(c.transactions, 'receipt'), alias(c.transactions, 'inv'), alias(c.transactions, 'ref')
    query = (sa.select(d, t.c.number.label('memo_number'), t.c.status.label('memo_status'), r.c.date,
                       clp.c.gross_minor_units.label('amount'), receipt.c.number.label('receipt_number'),
                       invoice.c.number.label('invoice_number'), refund.c.number.label('refund_number'))
             .join(t, t.c.id == d.c.credit_memo_id)
             .join(r, r.c.id == t.c.current_revision_id)
             .join(dl, sa.and_(dl.c.revision_id == r.c.id, dl.c.line_id == d.c.line_id))
             .join(clp, clp.c.document_line_id == dl.c.id)
             .join(receipt, receipt.c.id == d.c.receipt_id)
             .outerjoin(invoice, invoice.c.id == d.c.invoice_id)
             .outerjoin(refund, refund.c.id == d.c.refund_id))
    if not include_voided:
        query = query.where(t.c.status == 'posted')
    for column, values in ((d.c.receipt_id, receipt_ids), (d.c.credit_memo_id, credit_memo_ids),
                           (d.c.invoice_id, invoice_ids), (d.c.refund_id, refund_ids)):
        if values is not None:
            query = query.where(column.in_(sorted(set(values))))
    if as_of is not None:
        query = query.where(r.c.date <= as_of)
    return [dict(row) for row in s.company.conn.execute(query.order_by(r.c.date, d.c.id)).mappings()]


def receipt_rows(s, *, receipt_ids=None, customer_id=None, document_ids=None, customer_level=False, as_of=None,
                 include_voided=False):
    """Prepayment receipts with what each holds, oldest first (by date, then as written)."""
    link = _current_links()
    t, r = c.transactions, c.transaction_revisions
    query = (sa.select(link, t.c.number, t.c.status, r.c.date, r.c.total_minor_units.label('amount'),
                       c.customers.c.full_name.label('customer_name'))
             .join(t, t.c.id == link.c.receipt_id)
             .join(r, r.c.id == t.c.current_revision_id)
             .join(c.customers, c.customers.c.id == link.c.customer_id))
    if not include_voided:
        query = query.where(t.c.status == 'posted')
    if receipt_ids is not None:
        query = query.where(link.c.receipt_id.in_(sorted(set(receipt_ids))))
    if customer_id is not None:
        query = query.where(link.c.customer_id == customer_id)
    if document_ids is not None:
        query = query.where(link.c.work_document_id.in_(sorted(set(document_ids))))
    if customer_level:
        query = query.where(link.c.work_document_id.is_(None))
    if as_of is not None:
        query = query.where(r.c.date <= as_of)
    rows = [dict(row) for row in s.company.conn.execute(query.order_by(r.c.date, link.c.receipt_id)).mappings()]
    drawn = {}
    for draw in draw_rows(s, receipt_ids=[row['receipt_id'] for row in rows], as_of=as_of):
        drawn[draw['receipt_id']] = drawn.get(draw['receipt_id'], 0) + draw['amount']
    for row in rows:
        row['drawn'] = drawn.get(row['receipt_id'], 0) if row['status'] == 'posted' else 0
        row['held'] = row['amount'] - row['drawn'] if row['status'] == 'posted' else 0
    return rows


def is_prepayment_receipt(s, receipt_id):
    return s.company.conn.execute(sa.select(c.prepayment_links.c.id).where(
        c.prepayment_links.c.receipt_id == receipt_id).limit(1)).first() is not None


def is_draw(s, credit_memo_id):
    return s.company.conn.execute(sa.select(c.prepayment_draws.c.id).where(
        c.prepayment_draws.c.credit_memo_id == credit_memo_id).limit(1)).first() is not None


def held_for_customer(s, customer_id):
    return sum(row['held'] for row in receipt_rows(s, customer_id=customer_id))


def held_for_document(s, header):
    return sum(row['held'] for row in receipt_rows(s, document_ids=_pool_documents(s, header)))


def _documents(s, ids):
    ids = sorted({i for i in ids if i})
    if not ids:
        return {}
    return {row['id']: row for row in effects.rows(s, c.work_documents, c.work_documents.c.id.in_(ids))}


def _receipt_output(row, documents, currency):
    doc = documents.get(row['work_document_id'])
    return ReceiptRow(receipt_id=row['receipt_id'], receipt_number=row['number'], date=row['date'],
                      customer_id=row['customer_id'], customer=row['customer_name'],
                      document=_ref(doc) if doc else None, amount=_money(row['amount'], currency),
                      drawn=_money(row['drawn'], currency), held=_money(row['held'], currency), status=row['status'])


def _draw_output(row, documents, currency):
    doc = documents.get(row['work_document_id'])
    return DrawRow(draw_id=row['id'], credit_memo_id=row['credit_memo_id'], credit_memo_number=row['memo_number'],
                   date=row['date'], amount=_money(row['amount'], currency), receipt_id=row['receipt_id'],
                   receipt_number=row['receipt_number'], document=_ref(doc) if doc else None,
                   invoice_id=row['invoice_id'], invoice_number=row['invoice_number'], refund_id=row['refund_id'],
                   refund_number=row['refund_number'], status=row['memo_status'])


# ------------------------------------------------------------------ guards other writers call


def guard_line_item(s, item, *, nonposting, item_changed):
    """Refuse the Customer Prepayment item anywhere but on the prepayment commands' own documents."""
    account = find_account(s.company)
    if account is None or item.get('income_account_id') != account['id'] or writing():
        return
    if nonposting:
        raise _invalid('item', f'"{item["full_name"]}" is held money, not work to quote; take a deposit on this document '
                       'with `prepayment receive --document ...`')
    if item_changed:
        raise _invalid('item', f'"{item["full_name"]}" is written only by the prepayment commands: take a {SYNONYMS} with '
                       '`prepayment receive`, and use it on an invoice with `prepayment apply` (or `<kind> invoice`, which '
                       'applies it by default)')


def guard_sale(s, document_type, old_header, old_revision, resolved):
    """A prepayment receipt stays one Customer Prepayment line, for its customer, covering its draws."""
    if document_type != 'sales_receipt' or old_header is None or writing():
        return
    if not is_prepayment_receipt(s, old_header['id']):
        return
    prepayment_item = item_id(s.company)
    lines = resolved['lines']
    if len(lines) != 1 or lines[0]['profile'].item.id != prepayment_item:
        raise _invalid('lines', 'a prepayment receipt keeps its one Customer Prepayment line; to record a sale, void '
                       'this receipt (once nothing is drawn from it) and post the sale on its own')
    link = receipt_rows(s, receipt_ids=[old_header['id']])
    held = link[0] if link else None
    if held and resolved['profile'].customer.id != held['customer_id']:
        raise _invalid('customer', 'a prepayment receipt stays with its customer; void it and receive the money again '
                       'for the other customer')
    draws = draw_rows(s, receipt_ids=[old_header['id']])
    if draws:
        drawn = sum(row['amount'] for row in draws)
        if resolved['total'] < drawn:
            raise BookflowError('E_APPLIED_EXCEEDS_TOTAL', details={
                'sales_receipt_id': old_header['id'], 'drawn_minor_units': drawn, 'total_minor_units': resolved['total'],
                'next': f'{_plain(drawn, resolved["currency"])} of this prepayment is already drawn; undo the draws with '
                        '`prepayment unapply` first, or keep the amount at least that'})
        first = min(row['date'] for row in draws)
        if resolved['date'] > first:
            raise _invalid('date', f'this prepayment was drawn on {first}; a receipt cannot be dated after its draws')


def _blocked(s, receipt_id, verb):
    draws = draw_rows(s, receipt_ids=[receipt_id])
    if draws:
        raise BookflowError('E_HAS_APPLICATIONS', details={
            'sales_receipt_id': receipt_id, 'draw_ids': [row['credit_memo_id'] for row in draws],
            'action': 'unapply_first',
            'next': f'Money from this prepayment receipt is drawn by credit memo '
                    f'{", ".join(sorted({row["memo_number"] for row in draws}))}; undo each with `prepayment unapply`, '
                    f'then {verb} the receipt.'})


def guard_receipt_void(s, header):
    if not writing():
        _blocked(s, header['id'], 'void')


def guard_receipt_delete(s, receipt_id):
    if not writing():
        _blocked(s, receipt_id, 'delete')


def guard_draw_change(s, credit_memo_id, verb):
    """The drawing credit memo, its application and its refund are undone only together."""
    if writing() or not is_draw(s, credit_memo_id):
        return
    number = s.company.conn.execute(sa.select(c.transactions.c.number).where(c.transactions.c.id == credit_memo_id)).scalar()
    raise BookflowError('E_HAS_APPLICATIONS', details={
        'credit_memo_id': credit_memo_id, 'action': 'prepayment_unapply',
        'next': f'Credit memo {number} draws a customer prepayment; it cannot be {verb} on its own. '
                f'`prepayment unapply {number}` undoes the draw with its application or refund and returns the money '
                'to its receipts.'})


def guard_refund_change(s, refund_id, verb):
    if writing():
        return
    rows = draw_rows(s, refund_ids=[refund_id], include_voided=True)
    if rows:
        guard_draw_change(s, rows[0]['credit_memo_id'], verb)


def invoice_void_hint(s, blocking):
    """The `next` of an invoice void refused for its applications, when one of them is a draw."""
    ids = [row['paying_transaction_id'] for row in blocking]
    found = s.company.conn.execute(sa.select(c.transactions.c.number).join(
        c.prepayment_draws, c.prepayment_draws.c.credit_memo_id == c.transactions.c.id).where(
        c.transactions.c.id.in_(ids)).distinct()).scalars().all()
    if not found:
        return None
    return (f'This invoice is paid in part by a customer prepayment (credit memo {", ".join(sorted(found))}). Return it '
            'to its receipts with `prepayment unapply`, release any other payment with payment unapply, then void.')


def guard_work_status(s, old, value):
    """A document holding money is not voided, cancelled or superseded until the money moves."""
    if old is None or value['status'] == old['status'] or value['status'] not in ('voided', 'cancelled', 'superseded'):
        return
    rows = [row for row in receipt_rows(s, document_ids=[old['id']]) if row['held'] > 0]
    if rows:
        currency = _currency(s)
        held = sum(row['held'] for row in rows)
        raise BookflowError('E_HAS_APPLICATIONS', details={
            'work_document_id': old['id'], 'receipt_ids': [row['receipt_id'] for row in rows],
            'held_minor_units': held, 'action': 'move_prepayment_first',
            'next': f'{_plain(held, currency)} of customer prepayments is held on this {old["kind"].replace("_", " ")} '
                    f'(receipt {", ".join(row["number"] for row in rows)}). Use it with `prepayment apply`, pay it back '
                    'with `prepayment refund`, or move it with `prepayment attach`, then try again.'})


# ------------------------------------------------------------------ steering notes


def customer_note(s, customer_id, context):
    """Advice for invoice post, sales-receipt post and payment receive: never a refusal."""
    try:
        if find_account(s.company) is None:
            return None
        rows = [row for row in receipt_rows(s, customer_id=customer_id) if row['held'] > 0]
    except Exception:  # advice must never stop the write it rides on
        return None
    if not rows:
        return None
    currency = _currency(s)
    held = sum(row['held'] for row in rows)
    name = rows[0]['customer_name']
    if context == 'invoice':
        return (f'{name} has {_plain(held, currency)} held in customer prepayments (receipt '
                f'{", ".join(row["number"] for row in rows)}). Use it on this invoice with `prepayment apply --invoice <number>`; '
                'an invoice made from the estimate or work order (`<kind> invoice`) applies it by itself.')
    if context == 'payment':
        return (f'{name} has {_plain(held, currency)} held in customer prepayments. If it should pay an invoice, use it '
                'first with `prepayment apply --invoice <number>`, then receive only what is still owed.')
    return (f'{name} has {_plain(held, currency)} held in customer prepayments. A sales receipt is a sale; to take '
            f'another {SYNONYMS} use `prepayment receive`, and to use held money on a sale invoice it and `prepayment apply`.')


def add_note(s, output, customer_selector, context):
    """Append the steering note to a write's warnings; a note is advice and never refuses."""
    try:
        customer_id = defaults._row(s.company, 'customer', customer_selector, active=False)['id']
    except BookflowError:
        return output
    note = customer_note(s, customer_id, context)
    if note and output is not None and note not in output.warnings:
        output.warnings.append(note)
    return output


def noted(applier, context, field='customer'):
    """Wrap a command's applier so its result carries the steering note too."""
    def apply(plan, ctx, s):
        applied = applier(plan, ctx, s)
        inp = plan.data.get('input') if isinstance(plan.data, dict) else None
        selector = getattr(inp, field, None) if inp is not None else None
        if selector is not None and getattr(applied, 'output', None) is not None and hasattr(applied.output, 'warnings'):
            add_note(s, applied.output, selector, context)
        return applied
    return apply


# ------------------------------------------------------------------ the receipt


def _deposit_default(s):
    return s.company.conn.execute(sa.select(c.accounts.c.id).where(c.accounts.c.system_role == 'undeposited_funds')).scalar()


def _target(s, inp):
    """(customer row, document header or None) a receipt is held for."""
    if (inp.customer is None) == (inp.document is None):
        raise _invalid('customer', 'name the customer or the document the money is held for (one of them)')
    if inp.document is not None:
        header = resolve_document(s, inp.document)
        _holdable(s, header)
        customer = defaults._row(s.company, 'customer', document_customer(s, header), active=False)
        return customer, header
    return defaults._row(s.company, 'customer', inp.customer), None


def _receipt_input(s, inp, customer, header, item, amount, currency):
    from bookflow.company.sales_models import SalesLineInput, SalesReceiptPostInput
    words = f'{header["kind"].replace("_", " ")} {header["number"]}' if header else None
    description = f'Customer prepayment for {words}' if words else 'Customer prepayment'
    extra = {k: getattr(inp, k) for k in ('payment_method', 'class_id', 'number') if getattr(inp, k) is not None}
    if inp.reference is not None:
        extra['payment_reference'] = inp.reference
    return SalesReceiptPostInput(customer=customer['id'], date=inp.date or clock.today_iso(),
                                 deposit_to=inp.deposit_to or _deposit_default(s),
                                 memo=(inp.memo or description)[:2000],
                                 lines=[SalesLineInput(item=item['id'], quantity='1', unit_price=_plain(amount, currency),
                                                       description=description)], **extra)


def prepare_receive(s, ctx, inp):
    from bookflow.company import sales
    currency = _currency(s)
    customer, header = _target(s, inp)
    amount = _amount(inp.amount, currency, 'amount')
    date = inp.date or clock.today_iso()
    account = find_account(s.company)
    item = find_item(s.company, account)
    set_up = _set_up_names(s)
    warnings = []
    if item is not None:
        with _flag(_WRITING):
            plan = sales.prepare(s, ctx, _receipt_input(s, inp, customer, header, item, amount, currency), 'sales_receipt', 'post')
        receipt = DocumentNumber(id=plan.preview.id, number=plan.preview.number, type='sales_receipt',
                                 amount=_money(amount, currency), date=date)
        warnings += plan.preview.warnings
    else:
        # The account and item are set up by the write itself; the preview names the receipt
        # without pricing it through an item that does not exist yet.
        number, _ = effects.allocate(s, 'sales_receipt', inp.number)
        receipt = DocumentNumber(id=new_id(), number=number, type='sales_receipt', amount=_money(amount, currency), date=date)
    held = (held_for_document(s, header) if header else held_for_customer(s, customer['id'])) + amount
    where = f'{header["kind"].replace("_", " ")} {header["number"]}' if header else f'{customer["full_name"]}'
    output = PrepaymentWriteOutput(
        action='receive', customer_id=customer['id'], customer=customer['full_name'],
        document=_ref(header) if header else None, receipt=receipt, amount=_money(amount, currency),
        held_after=_money(held, currency), set_up=set_up, warnings=warnings,
        summary=(f'Received {_plain(amount, currency)} from {customer["full_name"]} as a customer prepayment on sales '
                 f'receipt {receipt.number}, held for {where} ({_plain(held, currency)} held now). It is a liability until an '
                 'invoice uses it (`prepayment apply`, or `<kind> invoice` by itself) or it is paid back (`prepayment refund`).'))
    return Plan(output, dict(input=inp, action='receive'))


def apply_receive(plan, ctx, s):
    from bookflow.company import sales
    inp = plan.data['input']
    currency = _currency(s)
    customer, header = _target(s, inp)
    amount = _amount(inp.amount, currency, 'amount')
    account, item, touched = ensure(s, ctx)
    with _flag(_WRITING):
        step = sales.prepare(s, ctx, _receipt_input(s, inp, customer, header, item, amount, currency), 'sales_receipt', 'post')
        receipt = sales.apply(step, ctx, s).output
    event = new_id()
    link = dict(id=new_id(), receipt_id=receipt.id, customer_id=customer['id'],
                work_document_id=header['id'] if header else None, supersedes_link_id=None,
                **_provenance(s, ctx, event))
    touched.append(Touched('prepayment_link', link['id'], 'create', None, 1, effects.decoded(link), db='company'))
    summary = f'prepayment receive: sales receipt {receipt.number}'
    _audit(s, ctx, 'prepayment receive', summary, touched, event)
    s.company.conn.execute(c.prepayment_links.insert().values(**link))
    held = held_for_document(s, header) if header else held_for_customer(s, customer['id'])
    output = plan.preview.model_copy(update=dict(
        receipt=DocumentNumber(id=receipt.id, number=receipt.number, type='sales_receipt',
                               amount=_money(amount, currency), date=inp.date or clock.today_iso()),
        held_after=_money(held, currency)))
    output.summary = output.summary.replace(plan.preview.receipt.number, receipt.number)
    return Applied(output, touched, summary, audited=True)


def _provenance(s, ctx, event):
    return dict(created_at=clock.now_iso(), created_by=s.actor.id, created_via=ctx.interface.value, audit_event_id=event)


def _audit(s, ctx, command, summary, touched, event):
    audit.write_event_to(s.company, ctx, command, summary, touched, actor_id=s.actor.id, actor_kind=s.actor.kind,
                         directive_code=getattr(s, 'directive_code', None), event_id=event)


# ------------------------------------------------------------------ attach


def _receipt_header(s, selector):
    from bookflow.company import sales
    header = sales.resolve(s, selector, 'sales_receipt')
    if not is_prepayment_receipt(s, header['id']):
        raise _invalid('receipt', f'sales receipt {header["number"]} is not a customer prepayment; take one with '
                       '`prepayment receive`')
    return header


def prepare_attach(s, ctx, inp):
    currency = _currency(s)
    receipt = _receipt_header(s, inp.receipt)
    if receipt['status'] != 'posted':
        raise BookflowError('E_APPLICATION_INACTIVE', details={'sales_receipt_id': receipt['id'], 'status': receipt['status']})
    row = receipt_rows(s, receipt_ids=[receipt['id']])[0]
    header = None
    if inp.document is not None:
        header = resolve_document(s, inp.document)
        _holdable(s, header)
        if document_customer(s, header) != row['customer_id']:
            raise _invalid('document', f'{header["kind"].replace("_", " ")} {header["number"]} is for another customer or '
                           f'job; this receipt\'s money is held for {row["customer_name"]}')
    if (header['id'] if header else None) == row['work_document_id']:
        raise _invalid('document', 'the money is already held there')
    output = PrepaymentWriteOutput(
        action='attach', customer_id=row['customer_id'], customer=row['customer_name'],
        document=_ref(header) if header else None,
        receipt=DocumentNumber(id=receipt['id'], number=receipt['number'], type='sales_receipt',
                               amount=_money(row['amount'], currency), date=row['date']),
        amount=_money(row['held'], currency), held_after=_money(row['held'], currency),
        summary=(f'Sales receipt {receipt["number"]}\'s {_plain(row["held"], currency)} still held is now held for '
                 + (f'{header["kind"].replace("_", " ")} {header["number"]}' if header else row['customer_name'])
                 + '; what was already drawn stays where it went.'))
    return Plan(output, dict(input=inp, action='attach', link_id=row['id'], header=header, receipt=receipt, row=row))


def apply_attach(plan, ctx, s):
    fresh = prepare_attach(s, ctx, plan.data['input'])
    data = fresh.data
    event = new_id()
    link = dict(id=new_id(), receipt_id=data['receipt']['id'], customer_id=data['row']['customer_id'],
                work_document_id=data['header']['id'] if data['header'] else None, supersedes_link_id=data['link_id'],
                **_provenance(s, ctx, event))
    touched = [Touched('prepayment_link', link['id'], 'create', None, 1, effects.decoded(link), db='company')]
    summary = f'prepayment attach: sales receipt {data["receipt"]["number"]}'
    _audit(s, ctx, 'prepayment attach', summary, touched, event)
    s.company.conn.execute(c.prepayment_links.insert().values(**link))
    return Applied(fresh.preview, touched, summary, audited=True)


# ------------------------------------------------------------------ drawing


def _source_rows(s, inp, *, customer_id, default_document=None):
    """The receipts a draw may take from, oldest first, and what was named (for held_after)."""
    named = [name for name in ('document', 'customer', 'receipts') if getattr(inp, name, None) is not None]
    if len(named) > 1:
        raise _invalid(named[1], 'name one source: a document, a customer or receipts')
    if getattr(inp, 'receipts', None) is not None:
        ids = [_receipt_header(s, selector)['id'] for selector in inp.receipts]
        rows = {row['receipt_id']: row for row in receipt_rows(s, receipt_ids=ids)}
        missing = [selector for selector, rid in zip(inp.receipts, ids) if rid not in rows]
        if missing:
            raise _invalid('receipts', f'receipt {missing[0]} is voided and holds nothing')
        return [rows[rid] for rid in dict.fromkeys(ids)], None, 'receipts'
    if getattr(inp, 'document', None) is not None or (not named and default_document is not None):
        header = resolve_document(s, inp.document) if getattr(inp, 'document', None) is not None else default_document
        return receipt_rows(s, document_ids=_pool_documents(s, header)), header, 'document'
    if getattr(inp, 'customer', None) is not None:
        customer_id = defaults._row(s.company, 'customer', inp.customer, active=False)['id']
    if customer_id is None:
        raise _invalid('customer', 'name the document, customer or receipts the money is held on')
    return receipt_rows(s, customer_id=customer_id, customer_level=True), None, 'customer'


def _allocate(rows, amount, date, customer_id):
    """Oldest receipt first; a receipt dated after the draw, or another customer's, is not drawn."""
    taken, left = [], amount
    for row in rows:
        if left == 0:
            break
        if row['held'] <= 0 or row['date'] > date or row['customer_id'] != customer_id:
            continue
        take = min(row['held'], left)
        taken.append((row, take))
        left -= take
    return taken


def _available(rows, date, customer_id):
    return sum(row['held'] for row in rows if row['date'] <= date and row['customer_id'] == customer_id)


def _credit_input(s, customer_id, date, taken, currency, ar_account, memo):
    from bookflow.company.credit_models import CreditLineInput, CreditMemoPostInput
    item = find_item(s.company)
    return CreditMemoPostInput(
        customer=customer_id, date=date, ar_account=ar_account, memo=memo[:2000],
        lines=[CreditLineInput(item=item['id'], quantity='1', unit_price=_plain(take, currency),
                               description=f'Customer prepayment from receipt {row["number"]}') for row, take in taken])


def _write_draws(s, ctx, *, customer_id, date, taken, currency, ar_account, invoice=None, refund=None, command):
    """Post the drawing credit memo, apply or refund it, and record each line's receipt."""
    from bookflow.company import credit_settlement, credits, refunds
    from bookflow.company.credit_settlement_models import CreditApplicationItem, CreditApplyInput
    total = sum(take for _, take in taken)
    target = f'invoice {invoice["number"]}' if invoice else 'a refund'
    with _flag(_WRITING):
        memo_plan = credits.prepare(s, ctx, _credit_input(s, customer_id, date, taken, currency, ar_account,
                                                          f'Customer prepayment drawn for {target}'))
        memo = credits.apply(memo_plan, ctx, s).output
        refund_out = None
        if invoice is not None:
            from bookflow.company import payment_queries
            version = payment_queries.invoice_facts(s, invoice['id'])['header']['version']
            step = credit_settlement.prepare(s, ctx, CreditApplyInput(
                credit_memo=memo.id, expected_version=memo.version, date=date,
                applications=[CreditApplicationItem(invoice=invoice['id'], expected_version=version,
                                                    amount=_plain(total, currency))]), 'apply')
            credit_settlement.apply(step, ctx, s)
        else:
            step = refunds.prepare(s, ctx, refund(memo, total), 'post')
            refund_out = refunds.apply(step, ctx, s).output
    event = new_id()
    rows, touched = [], []
    for (row, take), line in zip(taken, sorted(memo.revision.lines, key=lambda line: line.position), strict=True):
        draw = dict(id=new_id(), credit_memo_id=memo.id, line_id=line.line_id, receipt_id=row['receipt_id'],
                    work_document_id=row['work_document_id'], invoice_id=invoice['id'] if invoice else None,
                    refund_id=refund_out.id if refund_out else None, **_provenance(s, ctx, event))
        rows.append(draw)
        touched.append(Touched('prepayment_draw', draw['id'], 'create', None, 1, effects.decoded(draw), db='company'))
    summary = f'{command}: credit memo {memo.number}'
    _audit(s, ctx, command, summary, touched, event)
    s.company.conn.execute(c.prepayment_draws.insert(), rows)
    return memo, refund_out, touched


def _preview_draws(taken, date, currency, invoice=None):
    return [DrawRow(draw_id='', credit_memo_id='', credit_memo_number='', date=date, amount=_money(take, currency),
                    receipt_id=row['receipt_id'], receipt_number=row['number'], document=None,
                    invoice_id=invoice['id'] if invoice else None, invoice_number=invoice['number'] if invoice else None,
                    refund_id=None, refund_number=None, status='posted') for row, take in taken]


def _require_ledger(s):
    if find_account(s.company) is None or find_item(s.company) is None:
        raise _invalid('prepayment', 'no customer prepayment has been received yet; take one with `prepayment receive`')


def _invoice_facts(s, selector):
    from bookflow.company import payment_queries
    facts = payment_queries.invoice_facts(s, selector, write=True)
    if facts['header']['type'] != 'invoice':
        raise _invalid('invoice', 'held money pays an invoice')
    if facts['header']['status'] != 'posted':
        raise BookflowError('E_APPLICATION_INACTIVE', details={'invoice_id': facts['header']['id'], 'status': facts['header']['status']})
    return facts


def plan_draw_for_invoice(s, *, invoice, customer_id, due, date, rows, amount=None, field='amount'):
    """What a draw for this invoice takes: (taken, total). Raises on a cap when ``amount`` is named."""
    available = _available(rows, date, customer_id)
    if amount is None:
        amount = min(available, due)
    else:
        if amount > due:
            raise BookflowError('E_APPLIED_EXCEEDS_TOTAL', details={
                'field': field, 'requested_minor_units': amount, 'invoice_due_minor_units': due,
                'next': f'invoice {invoice["number"]} owes {_plain(due, _currency(s))}; draw at most that'})
        if amount > available:
            raise BookflowError('E_APPLIED_EXCEEDS_TOTAL', details={
                'field': field, 'requested_minor_units': amount, 'held_minor_units': available,
                'next': f'{_plain(available, _currency(s))} is held there on or before {date}; draw at most that, or name '
                        'another source (`prepayment show --customer ...` lists what is held)'})
    taken = _allocate(rows, amount, date, customer_id) if amount > 0 else []
    return taken, sum(take for _, take in taken)


def prepare_apply(s, ctx, inp):
    _require_ledger(s)
    currency = _currency(s)
    facts = _invoice_facts(s, inp.invoice)
    invoice, profile = facts['header'], facts['profile']
    customer_id = profile['customer_id']
    rows, header, kind = _source_rows(s, inp, customer_id=customer_id,
                                      default_document=_invoice_source_document(s, invoice['id']))
    if kind == 'document':
        if document_customer(s, header) != customer_id:
            raise _invalid('document', 'that document is for another customer or job than the invoice')
    others = {row['customer_id'] for row in rows} - {customer_id}
    if kind == 'receipts' and others:
        raise _invalid('receipts', 'a receipt held for another customer or job cannot pay this invoice')
    receipts_latest = max([row['date'] for row in rows if row['held'] > 0] or [facts['revision']['date']])
    date = inp.date or max(facts['revision']['date'], receipts_latest)
    if date < facts['revision']['date']:
        raise _invalid('date', 'a draw is dated on or after the invoice it pays')
    amount = _amount(inp.amount, currency, 'amount') if inp.amount is not None else None
    taken, total = plan_draw_for_invoice(s, invoice=invoice, customer_id=customer_id, due=facts['due'], date=date,
                                         rows=rows, amount=amount)
    if total == 0:
        raise _invalid('amount', 'nothing is held there on or before ' + date if facts['due'] else
                       f'invoice {invoice["number"]} owes nothing')
    from bookflow.company import journals
    journals.open_dates(s, [date])
    customer = defaults._row(s.company, 'customer', customer_id, active=False)
    held_after = sum(row['held'] for row in rows if row['customer_id'] == customer_id) - total
    output = PrepaymentWriteOutput(
        action='apply', customer_id=customer_id, customer=customer['full_name'], document=_ref(header) if header else None,
        invoice=DocumentNumber(id=invoice['id'], number=invoice['number'], type='invoice',
                               amount=_money(facts['gross'], currency), date=facts['revision']['date']),
        amount=_money(total, currency), draws=_preview_draws(taken, date, currency, invoice),
        balance_due=_money(facts['due'] - total, currency), held_after=_money(held_after, currency),
        summary=(f'Used {_plain(total, currency)} of {customer["full_name"]}\'s prepayment on invoice {invoice["number"]}, '
                 f'drawn from receipt {", ".join(row["number"] for row, _ in taken)} on {date}; the invoice owes '
                 f'{_plain(facts["due"] - total, currency)} now and {_plain(held_after, currency)} stays held.'))
    return Plan(output, dict(input=inp, action='apply'))


def apply_apply(plan, ctx, s):
    fresh = prepare_apply(s, ctx, plan.data['input'])
    preview = fresh.preview
    inp = plan.data['input']
    facts = _invoice_facts(s, inp.invoice)
    invoice = facts['header']
    rows, _, _ = _source_rows(s, inp, customer_id=preview.customer_id,
                              default_document=_invoice_source_document(s, invoice['id']))
    taken = _allocate(rows, preview.amount.minor_units, preview.draws[0].date, preview.customer_id)
    memo, _, touched = _write_draws(s, ctx, customer_id=preview.customer_id, date=preview.draws[0].date, taken=taken,
                                    currency=preview.amount.currency, ar_account=facts['profile']['control_account_id'],
                                    invoice=invoice, command='prepayment apply')
    return Applied(_finished(s, preview, memo, invoice_id=invoice['id']), touched, f'prepayment apply: credit memo {memo.number}',
                   audited=True)


def _finished(s, preview, memo, *, invoice_id=None, refund=None):
    currency = preview.amount.currency
    rows = draw_rows(s, credit_memo_ids=[memo.id])
    documents = _documents(s, [row['work_document_id'] for row in rows])
    update = dict(draws=[_draw_output(row, documents, currency) for row in rows],
                  credit_memo=DocumentNumber(id=memo.id, number=memo.number, type='credit_memo',
                                             amount=_money(sum(row['amount'] for row in rows), currency), date=rows[0]['date']))
    if invoice_id is not None:
        from bookflow.company import payment_queries
        update['balance_due'] = _money(payment_queries.invoice_facts(s, invoice_id)['due'], currency)
    if refund is not None:
        update['refund'] = DocumentNumber(id=refund.id, number=refund.number, type='customer_refund',
                                          amount=preview.amount, date=rows[0]['date'])
    return preview.model_copy(update=update)


# ------------------------------------------------------------------ the conversion hook


def conversion_preview(s, sale_preview, kind, source_selector, apply_prepayment):
    """Fill `prepayment_applied` and `balance_due` on a `<kind> invoice` preview."""
    if kind not in HOLDING_KINDS or find_account(s.company) is None:
        return
    currency = _currency(s)
    total = sale_preview.total_minor_units
    applied = 0
    if apply_prepayment:
        from bookflow.company import work
        source = work.resolve(s, source_selector, kind)
        customer_id = document_customer(s, source)
        rows = receipt_rows(s, document_ids=_pool_documents(s, source))
        applied = min(_available(rows, sale_preview.revision.date, customer_id), total)
    sale_preview.prepayment_applied = _sale_money(applied, currency)
    sale_preview.balance_due = _sale_money(total - applied, currency)
    if applied:
        sale_preview.warnings.append(f'{_plain(applied, currency)} held in customer prepayments on this work is applied '
                                     'to the invoice; pass apply_prepayment=false to leave it held.')


def conversion_draw(s, ctx, output, kind, source_selector, apply_prepayment=True):
    """Draw what the converted invoice's source holds, inside the conversion's transaction."""
    if kind not in HOLDING_KINDS or find_account(s.company) is None:
        return output
    from bookflow.company import work
    currency = _currency(s)
    source = work.resolve(s, source_selector, kind)
    facts = _invoice_facts(s, output.id)
    customer_id = facts['profile']['customer_id']
    rows = receipt_rows(s, document_ids=_pool_documents(s, source))
    taken, total = plan_draw_for_invoice(s, invoice=facts['header'], customer_id=customer_id, due=facts['due'],
                                         date=facts['revision']['date'], rows=rows) if apply_prepayment else ([], 0)
    if total:
        _write_draws(s, ctx, customer_id=customer_id, date=facts['revision']['date'], taken=taken, currency=currency,
                     ar_account=facts['profile']['control_account_id'], invoice=facts['header'], command='prepayment apply')
    from bookflow.company import payment_queries
    due = payment_queries.invoice_facts(s, output.id)['due']
    return output.model_copy(update=dict(prepayment_applied=_sale_money(total, currency), balance_due=_sale_money(due, currency)))


def conversion_replay(s, output):
    """What a replayed conversion's invoice took from prepayments."""
    if find_account(s.company) is None:
        return output
    from bookflow.company import payment_queries
    currency = _currency(s)
    applied = sum(row['amount'] for row in draw_rows(s, invoice_ids=[output.id]))
    due = payment_queries.invoice_facts(s, output.id)['due']
    return output.model_copy(update=dict(prepayment_applied=_sale_money(applied, currency), balance_due=_sale_money(due, currency)))


# ------------------------------------------------------------------ undo


def prepare_unapply(s, ctx, inp):
    from bookflow.company import credits, credit_settlement, payment_queries
    from bookflow.company.credit_settlement_models import CreditUnapplyInput, CreditUnapplyReference
    require_reason(ctx.reason)
    currency = _currency(s)
    memo = credits.resolve(s, inp.draw)
    rows = draw_rows(s, credit_memo_ids=[memo['id']], include_voided=True)
    if not rows:
        raise _invalid('draw', f'credit memo {memo["number"]} does not draw a customer prepayment')
    if memo['status'] != 'posted':
        raise BookflowError('E_APPLICATION_INACTIVE', details={
            'credit_memo_id': memo['id'], 'status': memo['status'],
            'next': 'This draw was already undone; its money is held again.'})
    total = sum(row['amount'] for row in rows)
    invoice_id, refund_id = rows[0]['invoice_id'], rows[0]['refund_id']
    unapply = None
    if invoice_id is not None:
        source = credits.facts(s, memo['id'], write=True)
        if source['applications']:
            refs = [CreditUnapplyReference(application_id=row['id'], invoice_expected_version=payment_queries.invoice_facts(
                s, row['paid_transaction_id'], write=True)['header']['version']) for row in source['applications']]
            unapply = CreditUnapplyInput(credit_memo=memo['id'], expected_version=memo['version'], applications=refs)
            try:
                with _flag(_WRITING):
                    credit_settlement.prepare(s, ctx, unapply, 'unapply')
            except BookflowError as error:
                if error.code == 'E_PERIOD_CLOSED':
                    error.details['next'] = (
                        'This draw was applied on or before the closing date, and undoing it is dated when it was applied. '
                        'A draw inside a closed period stays; correct it in an open period with a credit memo or a '
                        '`prepayment refund`, or a person can move the closing date back.')
                raise
    from bookflow.company import journals
    journals.open_dates(s, [rows[0]['date']])
    if refund_id is not None:
        refund_date = s.company.conn.execute(sa.select(c.transaction_revisions.c.date).join(
            c.transactions, c.transactions.c.current_revision_id == c.transaction_revisions.c.id).where(
            c.transactions.c.id == refund_id)).scalar()
        journals.open_dates(s, [refund_date])
    documents = _documents(s, [row['work_document_id'] for row in rows])
    first = effects.rows(s, c.prepayment_links, c.prepayment_links.c.receipt_id == rows[0]['receipt_id'])[0]
    customer = defaults._row(s.company, 'customer', first['customer_id'], active=False)
    held_after = held_for_customer(s, customer['id']) + total
    invoice = refund = None
    balance = None
    if invoice_id is not None:
        facts = payment_queries.invoice_facts(s, invoice_id)
        invoice = DocumentNumber(id=invoice_id, number=rows[0]['invoice_number'], type='invoice',
                                 amount=_money(facts['gross'], currency), date=facts['revision']['date'])
        balance = _money(facts['due'] + total, currency)
    if refund_id is not None:
        refund = DocumentNumber(id=refund_id, number=rows[0]['refund_number'], type='customer_refund',
                                amount=_money(total, currency), date=rows[0]['date'])
    output = PrepaymentWriteOutput(
        action='unapply', customer_id=customer['id'], customer=customer['full_name'],
        credit_memo=DocumentNumber(id=memo['id'], number=memo['number'], type='credit_memo', amount=_money(total, currency),
                                   date=rows[0]['date']),
        invoice=invoice, refund=refund, amount=_money(total, currency),
        draws=[_draw_output(row, documents, currency) for row in rows], balance_due=balance,
        held_after=_money(held_after, currency),
        summary=(f'Undid credit memo {memo["number"]}: {_plain(total, currency)} goes back to receipt '
                 f'{", ".join(sorted({row["receipt_number"] for row in rows}))}'
                 + (f', and invoice {invoice.number} owes {balance.amount} again' if invoice else '')
                 + (f'; refund {refund.number} is voided' if refund else '') + '.'))
    return Plan(output, dict(input=inp, action='unapply', memo=memo, unapply=unapply, refund_id=refund_id))


def apply_unapply(plan, ctx, s):
    from bookflow.company import credit_settlement, credits, refunds
    from bookflow.company.credit_models import CreditMemoVoidInput
    from bookflow.company.refund_models import CustomerRefundVoidInput
    fresh = prepare_unapply(s, ctx, plan.data['input'])
    data = fresh.data
    with _flag(_WRITING):
        if data['unapply'] is not None:
            step = credit_settlement.prepare(s, ctx, data['unapply'], 'unapply')
            credit_settlement.apply(step, ctx, s)
        if data['refund_id'] is not None:
            refund = refunds.resolve(s, data['refund_id'])
            step = refunds.prepare(s, ctx, CustomerRefundVoidInput(refund=refund['id'], expected_version=refund['version']), 'void')
            refunds.apply(step, ctx, s)
        memo = credits.resolve(s, data['memo']['id'])
        step = credits.prepare_void(s, ctx, CreditMemoVoidInput(credit_memo=memo['id'], expected_version=memo['version']))
        credits.apply(step, ctx, s)
    return Applied(fresh.preview, [], f'prepayment unapply: credit memo {data["memo"]["number"]}', audited=True)


# ------------------------------------------------------------------ refund


def _refund_input(inp, customer_id, date, currency, bank, method):
    from bookflow.company.refund_models import CustomerRefundPostInput, RefundSourceInput

    def build(memo, total):
        extra = {'check_number': inp.check_number} if inp.check_number else {}
        return CustomerRefundPostInput(
            date=date, sources=[RefundSourceInput(credit_memo=memo.id, amount=_plain(total, currency))],
            funding_account=bank, method=method, memo=(inp.memo or 'Customer prepayment paid back')[:2000],
            customer=customer_id, **extra)
    return build


def prepare_refund(s, ctx, inp):
    _require_ledger(s)
    currency = _currency(s)
    date = inp.date or clock.today_iso()
    named = [name for name in ('document', 'customer', 'receipts') if getattr(inp, name) is not None]
    if not named:
        raise _invalid('customer', 'name the document, customer or receipts the money is held on')
    rows, header, kind = _source_rows(s, inp, customer_id=None)
    customers = {row['customer_id'] for row in rows if row['held'] > 0}
    if kind == 'document':
        customers = {document_customer(s, header)}
    if kind == 'customer':
        customers = {defaults._row(s.company, 'customer', inp.customer, active=False)['id']}
    if len(customers) != 1:
        raise _invalid(named[0], 'nothing is held there' if not customers else
                       'those receipts are for more than one customer or job; refund each customer\'s separately')
    customer_id = customers.pop()
    available = _available(rows, date, customer_id)
    amount = _amount(inp.amount, currency, 'amount') if inp.amount is not None else available
    if amount == 0:
        raise _invalid('amount', f'nothing is held there on or before {date}')
    if amount > available:
        raise BookflowError('E_APPLIED_EXCEEDS_TOTAL', details={
            'field': 'amount', 'requested_minor_units': amount, 'held_minor_units': available,
            'next': f'{_plain(available, currency)} is held there on or before {date}; refund at most that'})
    bank = account_service.resolve_account(s.company, inp.bank_account)
    if bank['type'] != 'bank':
        raise _invalid('bank_account', f'"{bank["full_name"]}" is not a bank account')
    method = defaults._row(s.company, 'payment_method', inp.payment_method)
    from bookflow.company import journals
    journals.open_dates(s, [date])
    taken = _allocate(rows, amount, date, customer_id)
    customer = defaults._row(s.company, 'customer', customer_id, active=False)
    held_after = sum(row['held'] for row in rows if row['customer_id'] == customer_id) - amount
    output = PrepaymentWriteOutput(
        action='refund', customer_id=customer_id, customer=customer['full_name'], document=_ref(header) if header else None,
        amount=_money(amount, currency), draws=_preview_draws(taken, date, currency),
        held_after=_money(held_after, currency),
        summary=(f'Paid back {_plain(amount, currency)} of {customer["full_name"]}\'s prepayment from {bank["full_name"]} '
                 f'on {date}, drawn from receipt {", ".join(row["number"] for row, _ in taken)}; '
                 f'{_plain(held_after, currency)} stays held.'))
    return Plan(output, dict(input=inp, action='refund', customer_id=customer_id, bank=bank['id'], method=method['id'],
                             date=date))


def apply_refund(plan, ctx, s):
    fresh = prepare_refund(s, ctx, plan.data['input'])
    data, preview, inp = fresh.data, fresh.preview, plan.data['input']
    currency = preview.amount.currency
    rows, _, _ = _source_rows(s, inp, customer_id=None)
    taken = _allocate(rows, preview.amount.minor_units, data['date'], data['customer_id'])
    ar = s.company.conn.execute(sa.select(c.accounts.c.id).where(c.accounts.c.type == 'accounts_receivable',
                                                                 c.accounts.c.active.is_(True)).order_by(
        sa.case((c.accounts.c.system_role == 'accounts_receivable', 0), else_=1), c.accounts.c.full_name)).scalar()
    memo, refund, touched = _write_draws(
        s, ctx, customer_id=data['customer_id'], date=data['date'], taken=taken, currency=currency, ar_account=ar,
        refund=_refund_input(inp, data['customer_id'], data['date'], currency, data['bank'], data['method']),
        command='prepayment refund')
    return Applied(_finished(s, preview, memo, refund=refund), touched, f'prepayment refund: credit memo {memo.number}',
                   audited=True)


# ------------------------------------------------------------------ reading


def show(s, inp):
    currency = _currency(s)
    named = [name for name in ('receipt', 'document', 'customer') if getattr(inp, name) is not None]
    if len(named) != 1:
        raise _invalid('receipt', 'name one receipt, document or customer')
    header = customer_id = None
    if inp.receipt is not None:
        receipt = _receipt_header(s, inp.receipt)
        rows = receipt_rows(s, receipt_ids=[receipt['id']], include_voided=True)
        draws = draw_rows(s, receipt_ids=[receipt['id']], include_voided=True)
        customer_id = rows[0]['customer_id']
    elif inp.document is not None:
        header = resolve_document(s, inp.document)
        pool = _pool_documents(s, header)
        rows = receipt_rows(s, document_ids=pool, include_voided=True)
        draws = draw_rows(s, receipt_ids=[row['receipt_id'] for row in rows], include_voided=True)
        draws += [row for row in _draws_on_documents(s, pool) if row['id'] not in {d['id'] for d in draws}]
        customer_id = document_customer(s, header)
    else:
        customer_id = defaults._row(s.company, 'customer', inp.customer, active=False)['id']
        rows = receipt_rows(s, customer_id=customer_id, include_voided=True)
        draws = draw_rows(s, receipt_ids=[row['receipt_id'] for row in rows], include_voided=True)
    customer = defaults._row(s.company, 'customer', customer_id, active=False) if customer_id else None
    documents = _documents(s, [row['work_document_id'] for row in rows] + [row['work_document_id'] for row in draws])
    held = sum(row['held'] for row in rows)
    hints = []
    if held:
        hints = ['prepayment apply --invoice <number>: use it on an invoice', 'prepayment refund: pay it back',
                 'prepayment attach <receipt> --document <document>: move it to another document of the customer']
    if any(row['memo_status'] == 'posted' for row in draws):
        hints.append('prepayment unapply <credit memo>: undo a draw and hold the money again')
    return PrepaymentShowOutput(
        customer_id=customer_id, customer=customer['full_name'] if customer else None,
        document=_ref(header) if header else None, held=_money(held, currency),
        receipts=[_receipt_output(row, documents, currency) for row in rows],
        draws=[_draw_output(row, documents, currency) for row in sorted(draws, key=lambda r: (r['date'], r['id']))], next=hints)


def _draws_on_documents(s, ids):
    found = s.company.conn.execute(sa.select(c.prepayment_draws.c.credit_memo_id).where(
        c.prepayment_draws.c.work_document_id.in_(ids))).scalars().all()
    return draw_rows(s, credit_memo_ids=found, include_voided=True) if found else []


def query(s, inp):
    currency = _currency(s)
    customer_id = defaults._row(s.company, 'customer', inp.customer, active=False)['id'] if inp.customer else None
    document_ids = _pool_documents(s, resolve_document(s, inp.document)) if inp.document else None
    rows = receipt_rows(s, customer_id=customer_id, document_ids=document_ids, include_voided=not inp.held_only)
    rows = [row for row in rows if (not inp.held_only or row['held'] > 0)
            and (inp.date_from is None or row['date'] >= inp.date_from) and (inp.date_to is None or row['date'] <= inp.date_to)]
    try:
        offset = int(inp.cursor) if inp.cursor else 0
    except ValueError:
        raise _invalid('cursor', 'use the next_cursor of the previous page')
    page = rows[offset:offset + inp.limit]
    documents = _documents(s, [row['work_document_id'] for row in page])
    return PrepaymentPageOutput(items=[_receipt_output(row, documents, currency) for row in page], count=len(page),
                                next_cursor=str(offset + inp.limit) if offset + inp.limit < len(rows) else None)


def report(s, inp):
    """Receipts and draws to a date with running held balances, tied to the account."""
    currency = _currency(s)
    as_of = inp.as_of or clock.today_iso()
    account = find_account(s.company)
    customer_id = defaults._row(s.company, 'customer', inp.customer, active=False)['id'] if inp.customer else None
    receipts = receipt_rows(s, customer_id=customer_id, as_of=as_of) if account else []
    draws = draw_rows(s, receipt_ids=[row['receipt_id'] for row in receipts], as_of=as_of) if receipts else []
    documents = _documents(s, [row['work_document_id'] for row in receipts] + [row['work_document_id'] for row in draws])
    by_receipt = {row['receipt_id']: row for row in receipts}
    events = []
    for row in receipts:
        if row['held'] == 0 and not inp.include_drawn:
            continue
        events.append((row['customer_name'], row['customer_id'], row['date'], 0, row['receipt_id'], dict(
            kind='receipt', number=row['number'], applied_to=None, amount=row['amount'], receipt=row,
            document=documents.get(row['work_document_id']))))
        for draw in (d for d in draws if d['receipt_id'] == row['receipt_id']):
            events.append((row['customer_name'], row['customer_id'], draw['date'], 1, draw['id'], dict(
                kind='draw', number=draw['memo_number'], amount=-draw['amount'], receipt=row,
                applied_to=f'invoice {draw["invoice_number"]}' if draw['invoice_id'] else f'refund {draw["refund_number"]}',
                document=documents.get(draw['work_document_id']))))
    events.sort(key=lambda e: (e[0], e[1], e[2], e[3], e[4]))
    out_rows, running, customers = [], {}, {}
    for name, cid, date, _, _, event in events:
        running[cid] = running.get(cid, 0) + event['amount']
        receipt = event['receipt']
        doc = event['document']
        out_rows.append(PrepaymentReportRow(
            customer_id=cid, customer=name, document=_ref(doc) if doc else None, receipt_id=receipt['receipt_id'],
            receipt_number=receipt['number'], kind=event['kind'], date=date, number=event['number'],
            applied_to=event['applied_to'], amount=_money(event['amount'], currency), held=_money(running[cid], currency)))
        customers[cid] = name
    total = sum(row['held'] for row in by_receipt.values())
    balance = 0
    if account is not None:
        balance = s.company.conn.execute(sa.text(
            'SELECT COALESCE(SUM(l.credit_minor_units - l.debit_minor_units), 0) FROM posting_lines l '
            'JOIN posting_batches b ON b.id = l.batch_id WHERE l.account_id = :account AND b.effective_date <= :date'
            + (' AND l.name_id = :customer' if customer_id else '')),
            dict(account=account['id'], date=as_of, customer=customer_id)).scalar()
    if balance != total:
        raise BookflowError('E_INTERNAL', message=(
            f'The Customer Prepayments account is {_plain(balance, currency)} on {as_of} and the prepayment receipts hold '
            f'{_plain(total, currency)}. Something posted to the account without a prepayment record; find it with '
            '`report general-ledger` on that account and void it, then take the money with `prepayment receive`.'),
            details={'general_ledger_minor_units': balance, 'held_minor_units': total,
                     'difference_minor_units': balance - total, 'as_of': as_of})
    return CustomerPrepaymentsReportOutput(
        as_of=as_of, account_id=account['id'] if account else None, account=account['full_name'] if account else None,
        rows=out_rows, customers=[PrepaymentReportCustomer(customer_id=cid, customer=name, held=_money(running[cid], currency))
                                  for cid, name in sorted(customers.items(), key=lambda kv: kv[1])],
        total_held=_money(total, currency), account_balance=_money(balance, currency))
