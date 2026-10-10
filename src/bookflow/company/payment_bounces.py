"""A customer's check the bank returned: recorded in one step, as the anchor's Record Bounced Check.

**What the books do.** A check that was deposited and then came back leaves four things true at
once, and a person who records them one at a time gets one of them wrong:

1. the invoices the receipt paid are owed again, with their balances;
2. the cash the bank took back leaves the bank account the deposit went into, on the day it came
   back, as its own line (so a statement import finds it);
3. the bank's fee for returning the check is an expense paid out of the same account, as a second
   line;
4. the customer, if the business charges for a returned check, owes the fee on an open invoice.

**What stays as it was.** The receipt, the deposit it was banked on and the bank's deposit line
are not touched: the bank really did take the deposit, and a bounce is what happened to it
afterwards. Nothing is deleted or rewritten; each effect is an ordinary document with its own
number, and the bounce is the record that says they belong together.

**What the documents are.** The invoices reopen by ``payment unapply`` of every live application
of the receipt. The returned cash leaves the bank as a ``customer-refund`` of the receipt's cash
(it debits Accounts Receivable and credits the bank, which is exactly what a returned check does
to the books, and it uses up the receipt's capacity so the same money cannot be applied again). The
bank's fee is a register entry and the customer's fee is an invoice. All four are written in one
transaction, so a refused fee leaves the check exactly as it was.

**Reversal is the normal correction path.** There is no un-bounce command. Voiding the refund gives
the receipt its cash back (it is then an ordinary unapplied receipt again, to apply or void as any
other) and ends "bounced"; the fee entry and the fee invoice are voided as any entry and invoice
are. The receipt shows "bounced on ..." while its refund is posted.

**Assumptions about the anchor** (its guide is clear about the effect and silent on the
mechanics): the anchor records the bank fee and the customer fee in the same window, charges the
customer's fee as an item on a new invoice, and reopens the paid invoice; it keeps the original
deposit. What differs here is that the reopening is the receipt's unapply, which carries the
receipt's own application dates: a receipt applied inside a closed period cannot be bounced until
the closing date allows it, because reopening an invoice there would change a closed period.
"""
from __future__ import annotations

import json

import sqlalchemy as sa

from bookflow.company import accounts as account_service, schema as c
from bookflow.company import document_effects as effects, journals, payment_queries as query
from bookflow.company import sales_defaults as defaults
from bookflow.company.journal_models import parse_domestic_amount
from bookflow.company.payment_bounce_models import (
    BounceDocument, PaymentBounceOnReceipt, PaymentBounceOutput, ReopenedInvoice,
)
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError, require_reason
from bookflow.core.ids import new_id
from bookflow.core.money import Money
from bookflow.core.registry import Applied, Plan, Touched

EXPENSE_TYPES = ('expense', 'other_expense', 'cost_of_goods_sold')
FEE_ITEM_TYPES = ('other_charge', 'service')


def _invalid(field, problem, **details):
    error = BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': problem}]})
    error.details.update(details)
    return error


# ------------------------------------------------------------------ reading a bounce


def live_bounces(s, payment_ids):
    """{payment id: its newest bounce that still stands}: the refund it names is still posted."""
    ids = sorted(set(payment_ids))
    if not ids:
        return {}
    b, t = c.payment_bounces, c.transactions
    found = {}
    for offset in range(0, len(ids), 200):
        rows = s.company.conn.execute(
            sa.select(b, t.c.number.label('refund_number'))
            .join(t, t.c.id == b.c.refund_id)
            .where(b.c.payment_id.in_(ids[offset:offset + 200]), t.c.status == 'posted')
            .order_by(b.c.bounce_date, b.c.id)).mappings()
        for row in rows:
            found[row['payment_id']] = dict(row)
    return found


def on_receipt(row):
    """The record as a receipt shows it."""
    money = lambda units: Money(units, row['currency']).to_dict() if units is not None else None
    return PaymentBounceOnReceipt(
        bounce_id=row['id'], bounced_on=row['bounce_date'], returned=money(row['returned_minor_units']),
        refund_id=row['refund_id'], refund_number=row['refund_number'],
        bank_fee_id=row['bank_fee_journal_id'], bank_fee=money(row['bank_fee_minor_units']),
        customer_fee_invoice_id=row['customer_fee_invoice_id'], customer_fee=money(row['customer_fee_minor_units']),
        reason=row['reason'], note=f"bounced on {row['bounce_date']}")


def bounce_of_refund(s, refund_id):
    """The bounce a refund belongs to, whether or not it still stands."""
    row = s.company.conn.execute(sa.select(c.payment_bounces).where(c.payment_bounces.c.refund_id == refund_id)).mappings().first()
    return dict(row) if row else None


def bounced_on(s, payment_ids):
    """{payment id: bounce date} for the receipts whose check came back and still stands."""
    return {pid: row['bounce_date'] for pid, row in live_bounces(s, payment_ids).items()}


# ------------------------------------------------------------------ what the bounce is made of


def _bank_account(s, selector, currency, field):
    row = account_service.resolve_account(s.company, selector)
    if not row['active']:
        raise BookflowError('E_INACTIVE_REFERENCE', details={'record_type': 'account', 'record_id': row['id'], 'field': field})
    if row['type'] != 'bank':
        raise _invalid(field, f'"{row["full_name"]}" is a {row["type"].replace("_", " ")} account; a returned '
                       'check leaves a bank account')
    if row['currency'] != currency:
        raise _invalid(field, 'account must use the home currency')
    return row


def _deposited_to(s, payment_id, profile):
    """The bank account the receipt's cash was banked in, or None when it never reached a bank."""
    from bookflow.company.deposit_dependencies import active_claim
    claim = active_claim(s, payment_id)
    if claim is not None:
        deposit = s.company.conn.execute(sa.select(c.transactions).where(c.transactions.c.id == claim['transaction_id'])).mappings().one()
        profile_row = s.company.conn.execute(sa.select(c.deposit_profiles.c.bank_account_id).where(
            c.deposit_profiles.c.revision_id == deposit['current_revision_id'])).scalar_one()
        return profile_row, claim['transaction_id']
    account = s.company.conn.execute(sa.select(c.accounts).where(c.accounts.c.id == profile['deposit_account_id'])).mappings().one()
    if account['type'] == 'bank':
        return account['id'], None
    return None, None


def _fee_amount(value, currency, field):
    units = parse_domestic_amount(value, currency, field).minor_units
    if units <= 0:
        raise _invalid(field, 'a fee must be more than zero; leave it out when there is none')
    return units


def _fee_item(s, fee, field):
    """The item the customer's fee is billed through: the one named, or the one on the income account named."""
    if (fee.item is None) == (fee.account is None):
        raise _invalid(field, 'name the item the fee is billed through (`item`) or the income account it belongs to '
                       '(`account`), one of the two')
    items = c.items
    if fee.item is not None:
        row = defaults._row(s.company, 'item', fee.item)
        if row['type'] not in FEE_ITEM_TYPES or row.get('charge_percent') is not None or not row.get('sales_enabled', True):
            raise _invalid(field + '.item', f'"{row["full_name"]}" cannot bill a fixed fee (it is a {row["type"].replace("_", " ")} item'
                           + (' charged as a percentage' if row.get('charge_percent') is not None else '')
                           + '); name an Other Charge or service item with a fixed price')
        return row['id']
    account = account_service.resolve_account(s.company, fee.account)
    if account['type'] not in ('income', 'other_income'):
        raise _invalid(field + '.account', f'"{account["full_name"]}" is a {account["type"].replace("_", " ")} account; '
                       'a customer fee is billed to an income account such as Returned Check Charges')
    found = s.company.conn.execute(sa.select(items.c.id, items.c.full_name).where(
        items.c.income_account_id == account['id'], items.c.type.in_(FEE_ITEM_TYPES),
        items.c.active.is_(True), items.c.sales_enabled.is_(True)).order_by(items.c.full_name)).all()
    if not found:
        raise _invalid(field + '.account', f'no active item bills to "{account["full_name"]}"; create an Other Charge item '
                       'that posts to it with `item create` (type other_charge, income_account_id), then name that item')
    return found[0].id


def _intent(s, ctx, inp):
    """Everything the bounce needs, read and checked, with the exact input of each document it writes."""
    from bookflow.company.payment_dependencies import payment_version
    from bookflow.company.payment_models import UnapplyReference
    require_reason(ctx.reason)
    info = defaults._info(s.company)
    currency = info['home_currency']
    facts = query.payment_facts(s, inp.payment, write=True)
    header, revision, profile = facts['header'], facts['revision'], facts['profile']
    payment_version(s, header, inp.expected_version)
    if header['status'] != 'posted':
        raise BookflowError('E_APPLICATION_INACTIVE', details={
            'payment_id': header['id'], 'status': header['status'],
            'next': 'A voided receipt holds no money; there is no check to record as returned.'})
    standing = live_bounces(s, [header['id']]).get(header['id'])
    if standing is not None:
        raise _invalid('payment', f"this receipt was already recorded as bounced on {standing['bounce_date']}; "
                       'void its returned-check refund (`customer-refund void`) first if that was a mistake',
                       bounce_id=standing['id'], refund_id=standing['refund_id'])
    if facts['consumptions']:
        raise BookflowError('E_HAS_REFUND', details={
            'payment_id': header['id'], 'refund_ids': sorted({row['transaction_id'] for row in facts['consumptions']}),
            'action': 'void_the_refund_first',
            'next': 'Part of this receipt was already paid back to the customer. Void that refund (`customer-refund void`), '
                    'then record the bounce.'})
    if inp.date < revision['date']:
        raise _invalid('date', 'the check cannot come back before the receipt it was recorded on')
    keys = list(facts['keys'].values())
    if len(keys) != 1:
        raise _invalid('payment', 'this receipt paid invoices of more than one customer or job, and a refund pays back one '
                       'of them; unapply the invoices of each, then record the return with a `customer-refund post` for each '
                       'customer or job')
    key = keys[0]
    component = next(row for row in facts['components'] if row['component_key_id'] == key['id'])
    discount = s.company.conn.execute(sa.select(sa.func.coalesce(sa.func.sum(c.payment_discounts.c.amount_minor_units), 0)).where(
        c.payment_discounts.c.transaction_id == header['id'])).scalar_one()
    cash = component['amount_minor_units'] - discount
    if cash <= 0:
        raise _invalid('payment', 'this receipt took no cash (it is a write-off), so there is no check to record as returned')
    if inp.bank_account is not None:
        bank = _bank_account(s, inp.bank_account, currency, 'bank_account')
        deposit_id = None
    else:
        bank_id, deposit_id = _deposited_to(s, header['id'], profile)
        if bank_id is None:
            raise _invalid('payment', 'this receipt is still in Undeposited Funds, so the bank never held the check; '
                           'unapply it (`payment unapply`) and void it (`payment void`) instead, or name the bank '
                           'account it was deposited to as `bank_account`', next='payment void')
        bank = _bank_account(s, bank_id, currency, 'bank_account')
    payer = defaults._row(s.company, 'customer', profile['payer_id'], active=False)
    reference = (profile['reference'] or '').strip()
    check_words = f'check {reference}' if reference else f'check on receipt {header["number"]}'
    applied = sum(row['amount_minor_units'] for row in facts['applications'])
    unapply = None
    if facts['applications']:
        refs = [UnapplyReference(application_id=row['id'], invoice_expected_version=query.invoice_facts(
            s, row['paid_transaction_id'], write=True)['header']['version']) for row in facts['applications']]
        from bookflow.company.payment_models import PaymentUnapplyInput
        unapply = PaymentUnapplyInput(payment=header['id'], expected_version=header['version'],
                                      applications=refs, operation_key=inp.operation_key + '.unapply')
    method = defaults._row(s.company, 'payment_method', profile['payment_method_id'], active=False)
    if not method['active']:
        method = s.company.conn.execute(sa.select(c.payment_methods).where(
            c.payment_methods.c.kind == 'other', c.payment_methods.c.active.is_(True)).order_by(c.payment_methods.c.name)).mappings().first()
        if method is None:
            raise _invalid('payment', 'the receipt\'s payment method is inactive and the list has no active "other" method '
                           'to stand in for it; make one active first')
    from bookflow.company.refund_models import CustomerRefundPostInput, RefundSourceInput
    memo = f'Returned {check_words} from {payer["full_name"]}: {ctx.reason.strip()}'[:2000]
    refund = CustomerRefundPostInput(
        date=inp.date, sources=[RefundSourceInput(payment=header['id'], amount=_plain(cash, currency))],
        funding_account=bank['id'], method=method['id'], reference=f'Returned {check_words}'[:128],
        memo=memo, customer=key['party_id'])
    bank_fee = register = None
    if inp.bank_fee is not None:
        units = _fee_amount(inp.bank_fee.amount, currency, 'bank_fee.amount')
        expense = account_service.resolve_account(s.company, inp.bank_fee.account)
        if expense['type'] not in EXPENSE_TYPES:
            raise _invalid('bank_fee.account', f'"{expense["full_name"]}" is a {expense["type"].replace("_", " ")} account; '
                           'the bank\'s fee is charged to an expense account such as Bank Service Charges')
        from bookflow.company.register_models import RegisterPostInput
        bank_fee = dict(units=units, account=expense)
        register = RegisterPostInput(
            account=bank['id'], date=inp.date, direction='decrease', amount=_plain(units, currency),
            category=expense['id'], memo=(inp.bank_fee.memo or f'Returned item fee, {check_words} from {payer["full_name"]}')[:2000])
    customer_fee = invoice = None
    if inp.customer_fee is not None:
        units = _fee_amount(inp.customer_fee.amount, currency, 'customer_fee.amount')
        item_id = _fee_item(s, inp.customer_fee, 'customer_fee')
        from bookflow.company.sales_models import InvoicePostInput, SalesLineInput
        description = f'Returned {check_words} fee'
        extra = {'number': inp.customer_fee.number} if inp.customer_fee.number is not None else {}
        invoice = InvoicePostInput(
            customer=key['party_id'], date=inp.date, ar_account=profile['ar_account_id'],
            memo=(inp.customer_fee.memo or f'{description}; the check was returned by the bank on {inp.date}')[:2000],
            lines=[SalesLineInput(item=item_id, quantity='1', description=description,
                                  unit_price=_plain(units, currency))], **extra)
        customer_fee = dict(units=units, item=item_id)
    return dict(facts=facts, header=header, revision=revision, profile=profile, key=key, cash=cash, discount=discount,
                bank=bank, deposit_id=deposit_id, payer=payer, applied=applied, unapply=unapply, refund=refund,
                register=register, invoice=invoice, bank_fee=bank_fee, customer_fee=customer_fee, currency=currency,
                check_words=check_words)


# ------------------------------------------------------------------ preparing and persisting


def _money(units, currency):
    return Money(units, currency).to_dict()


def _plain(units, currency):
    """An amount as the decimal string every input takes."""
    return Money(units, currency).to_dict()['amount']


def _document(output, kind, account=None):
    total = output.total
    return BounceDocument(id=output.id, number=output.number, type=kind,
                          amount=total.model_dump() if hasattr(total, 'model_dump') else total, account=account)


def _reopened(s, unapply_plan, currency):
    """The invoices the unapply reopens: what the receipt had paid on each and what each owes now."""
    if unapply_plan is None:
        return []
    effect = unapply_plan.preview.effect.model_dump(mode='json')
    due = {row['invoice_id']: row['due_minor_units'] for row in effect['document_changes']}
    paid = {}
    for row in effect['applications']:
        paid[row['invoice_id']] = paid.get(row['invoice_id'], 0) + row['amount']['minor_units']
    numbers = {row['id']: row['number'] for row in effects.rows(s, c.transactions, c.transactions.c.id.in_(sorted(paid)))}
    return [ReopenedInvoice(invoice_id=invoice_id, number=numbers[invoice_id], reopened=_money(paid[invoice_id], currency),
                            due=_money(due[invoice_id], currency)) for invoice_id in sorted(paid, key=lambda i: numbers[i])]


def _summary(intent, date, reopened, fee_bank, fee_customer):
    currency = intent['currency']
    parts = [f"Recorded {intent['check_words']} from {intent['payer']['full_name']} as returned on {date}: "
             f"{Money(intent['cash'], currency)} left {intent['bank']['full_name']}."]
    if reopened:
        parts.append('Reopened invoice' + ('s ' if len(reopened) != 1 else ' ')
                     + ', '.join(f"{row.number} ({row.due.amount} owed)" for row in reopened) + '.')
    else:
        parts.append('The receipt had paid no invoice, so none reopened.')
    if fee_bank:
        parts.append(f"The bank's fee of {Money(intent['bank_fee']['units'], currency)} was charged to {intent['bank_fee']['account']['full_name']}.")
    if fee_customer:
        parts.append(f"The customer owes a returned-check fee of {Money(intent['customer_fee']['units'], currency)} on invoice {fee_customer.number}.")
    parts.append('The receipt and the deposit it was banked on are unchanged.')
    return ' '.join(parts)


def _replay(s, inp):
    """The first result of this key, rebuilt from the records, when it is asked for again."""
    row = s.company.conn.execute(sa.select(c.payment_bounces).where(c.payment_bounces.c.operation_key == inp.operation_key)).mappings().first()
    if row is None:
        return None
    from bookflow.company import sales
    header = sales.resolve(s, inp.payment, 'payment')
    if row['payment_id'] != header['id']:
        raise BookflowError('E_PAYMENT_OPERATION_KEY_REUSED')
    currency = row['currency']
    docs = {}
    for name in ('refund_id', 'bank_fee_journal_id', 'customer_fee_invoice_id'):
        if row[name]:
            docs[name] = s.company.conn.execute(sa.select(c.transactions.c.id, c.transactions.c.number, c.transactions.c.type,
                c.transaction_revisions.c.total_minor_units).join(c.transaction_revisions,
                c.transaction_revisions.c.id == c.transactions.c.current_revision_id).where(c.transactions.c.id == row[name])).mappings().one()
    document = lambda key, kind, account=None: (BounceDocument(id=docs[key]['id'], number=docs[key]['number'], type=kind,
        amount=_money(docs[key]['total_minor_units'], currency), account=account) if key in docs else None)
    operation = s.company.conn.execute(sa.select(c.payment_operations.c.effect_snapshot).where(
        c.payment_operations.c.operation_key == inp.operation_key + '.unapply')).scalar()
    reopened = []
    if operation:
        effect = json.loads(operation)['effect']
        due = {item['invoice_id']: item['due_minor_units'] for item in effect['document_changes']}
        paid = {}
        for item in effect['applications']:
            paid[item['invoice_id']] = paid.get(item['invoice_id'], 0) + item['amount']['minor_units']
        numbers = {r['id']: r['number'] for r in effects.rows(s, c.transactions, c.transactions.c.id.in_(sorted(paid)))}
        reopened = [ReopenedInvoice(invoice_id=i, number=numbers[i], reopened=_money(paid[i], currency), due=_money(due[i], currency))
                    for i in sorted(paid, key=lambda i: numbers[i])]
    facts = query.payment_facts(s, header['id'])
    bank = s.company.conn.execute(sa.select(c.customer_refund_profiles.c.funding_account_id).where(
        c.customer_refund_profiles.c.transaction_id == row['refund_id']).order_by(c.customer_refund_profiles.c.created_at.desc())).scalar()
    bank_name = s.company.conn.execute(sa.select(c.accounts.c.full_name).where(c.accounts.c.id == bank)).scalar()
    output = PaymentBounceOutput(
        changed=False, idempotent_replay=True, id=header['id'], version=header['version'], bounce_id=row['id'],
        bounced_on=row['bounce_date'], operation_key=inp.operation_key, facts_fingerprint=query.digest([row['id']]),
        returned=_money(row['returned_minor_units'], currency), bank_account=bank_name, reopened_invoices=reopened,
        refund=document('refund_id', 'customer_refund'), bank_fee=document('bank_fee_journal_id', 'journal_entry'),
        customer_fee=document('customer_fee_invoice_id', 'invoice'),
        credit_left=_money(sum(facts['available'].values()), currency),
        summary=f"This check was already recorded as returned on {row['bounce_date']}; nothing was written again.")
    return Plan(output, dict(input=inp, replay=True))


def prepare(s, ctx, inp, operation='bounce'):
    from bookflow.company import payments, refunds, registers, sales
    from bookflow.hub.access import one_authorization
    replay = _replay(s, inp)
    if replay is not None:
        return replay
    with one_authorization(s):
        intent = _intent(s, ctx, inp)
        currency = intent['currency']
        unapply = None
        if intent['unapply']:
            try:
                unapply = payments.prepare(s, ctx, intent['unapply'], 'unapply')
            except BookflowError as error:
                if error.code == 'E_PERIOD_CLOSED':
                    # The inverse of an application carries the application's own date.
                    error.details['next'] = ('Reopening the invoices this receipt paid is dated when it paid them, which is on or '
                                             'before the closing date. A person can move the closing date back to record the '
                                             'return, or bill the customer for the returned amount on a new invoice.')
                raise
        released = {('payment', intent['key']['id']): intent['applied']} if intent['applied'] else {}
        refund = refunds.prepare_post(s, ctx, intent['refund'], released=released)
        register = registers.prepare(s, ctx, intent['register'], 'post') if intent['register'] else None
        invoice = sales.prepare(s, ctx, intent['invoice'], 'invoice', 'post') if intent['invoice'] else None
    reopened = _reopened(s, unapply, currency)
    fee_bank = _document(register.preview, 'journal_entry', intent['bank_fee']['account']['full_name']) if register else None
    fee_customer = _document(invoice.preview, 'invoice') if invoice else None
    fingerprint = query.digest([intent['header']['id'], intent['header']['version'], inp.date, intent['cash'],
        intent['bank']['id'], [(row.invoice_id, row.reopened.minor_units) for row in reopened],
        intent['bank_fee'] and [intent['bank_fee']['units'], intent['bank_fee']['account']['id']],
        intent['customer_fee'] and [intent['customer_fee']['units'], intent['customer_fee']['item']],
        defaults._info(s.company)['closing_date']])
    if inp.expected_facts_fingerprint is not None and inp.expected_facts_fingerprint != fingerprint:
        raise BookflowError('E_PREVIEW_STALE', details={'reason': 'bounce_facts', 'current_facts_fingerprint': fingerprint})
    version = unapply.preview.version if unapply else intent['header']['version']
    credit_left = sum(intent['facts']['available'].values()) + intent['applied'] - intent['cash']
    output = PaymentBounceOutput(
        id=intent['header']['id'], version=version, bounce_id=None, bounced_on=inp.date, operation_key=inp.operation_key,
        facts_fingerprint=fingerprint, returned=_money(intent['cash'], currency), bank_account=intent['bank']['full_name'],
        reopened_invoices=reopened, refund=_document(refund.preview, 'customer_refund'), bank_fee=fee_bank,
        customer_fee=fee_customer, credit_left=_money(credit_left, currency),
        summary=_summary(intent, inp.date, reopened, fee_bank, fee_customer))
    return Plan(output, dict(input=inp, intent=intent, fingerprint=fingerprint))


def apply(plan, ctx, s):
    from bookflow.company import payments, refunds, registers, sales
    if plan.data.get('replay'):
        return Applied(plan.preview, [], 'recovered payment bounce')
    fresh = prepare(s, ctx, plan.data['input'])
    if fresh.data.get('replay'):
        return Applied(fresh.preview, [], 'recovered payment bounce')
    if fresh.data['fingerprint'] != plan.data['fingerprint']:
        raise BookflowError('E_PREVIEW_STALE', details={'reason': 'bounce_facts'})
    inp, intent = fresh.data['input'], fresh.data['intent']
    # Each step is prepared from the books as the step before it left them, and written under its
    # own audit event; they share this request, and a refusal in any of them rolls back all.
    version = intent['header']['version']
    if intent['unapply']:
        step = payments.prepare(s, ctx, intent['unapply'], 'unapply')
        version = step.preview.version
        payments.apply(step, ctx, s)
    refund = refunds.prepare(s, ctx, intent['refund'], 'post')
    refund_out = refunds.apply(refund, ctx, s).output
    register_out = invoice_out = None
    if intent['register']:
        step = registers.prepare(s, ctx, intent['register'], 'post')
        register_out = registers.apply(step, ctx, s).output
    if intent['invoice']:
        step = sales.prepare(s, ctx, intent['invoice'], 'invoice', 'post')
        invoice_out = sales.apply(step, ctx, s).output
    currency = intent['currency']
    event, at = new_id(), clock.now_iso()
    record = dict(
        id=new_id(), payment_id=intent['header']['id'], operation_key=inp.operation_key, refund_id=refund_out.id,
        bounce_date=inp.date, returned_minor_units=intent['cash'], currency=currency,
        bank_fee_journal_id=register_out.id if register_out else None,
        bank_fee_minor_units=intent['bank_fee']['units'] if register_out else None,
        customer_fee_invoice_id=invoice_out.id if invoice_out else None,
        customer_fee_minor_units=intent['customer_fee']['units'] if invoice_out else None,
        reason=ctx.reason.strip()[:500], created_at=at, created_by=s.actor.id, created_via=ctx.interface.value,
        audit_event_id=event)
    touched = [Touched('payment_bounce', record['id'], 'create', None, 1, effects.decoded(record), db='company')]
    summary_text = f"bounce payment {intent['header']['number']}"
    audit.write_event_to(s.company, ctx, 'payment bounce', summary_text, touched, actor_id=s.actor.id,
                         actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None), event_id=event)
    s.company.conn.execute(c.payment_bounces.insert().values(**record))
    output = fresh.preview.model_copy(update=dict(
        bounce_id=record['id'], version=version, refund=_document(refund_out, 'customer_refund'),
        bank_fee=_document(register_out, 'journal_entry', intent['bank_fee']['account']['full_name']) if register_out else None,
        customer_fee=_document(invoice_out, 'invoice') if invoice_out else None))
    return Applied(output, touched, summary_text, audited=True)
