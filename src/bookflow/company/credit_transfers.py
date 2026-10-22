"""Moving a customer's credit from one job to another, the way the anchor's guides do it, in one step.

**What the anchor does.** It has no ready-made transfer. Its guides move a job's credit with a pair
of entries through a clearing account: one takes the credit off the first job's receivable, the
other gives it to the second job's, and the clearing account nets to zero. Accountants do the same
with documents, so each job's open items stay right: a charge on the job that has the credit,
which that credit then pays, and a credit memo on the job that gets it.

**What this writes.** Exactly that pair, as ordinary documents on one Credit Transfer item that
posts to a Credit Transfer Clearing account (both set up on first use, as the anchor's guide asks
the reader to create the clearing account):

1. an invoice on the source for the amount (debit Accounts Receivable for the source, credit the
   clearing account);
2. the source's own credit applied to that invoice -- a credit memo through ``customer-credit
   apply``, an unapplied receipt through ``payment apply`` -- oldest first, so the source's credit
   and its charge both close and nothing posts;
3. a credit memo on the target for the amount (debit the clearing account, credit Accounts
   Receivable for the target), which the target then uses like any credit memo, here at once
   through ``apply_to`` or later through ``customer-credit apply``.

The books balance at every step; the receivable moves from one job to the other and the clearing
account is back at zero. Aging, customer balance detail and statements read ordinary documents, so
each job's balance is right with no special case. Everything is written together or nothing is.

**Reversal.** ``transfer-void`` unapplies everything that stands on the two documents (the target
invoices the moved credit paid are owed again, the source's credit is free again) and voids both,
each at its own date, so a transfer inside a closed period stays put.
"""
from __future__ import annotations

import sqlalchemy as sa

from bookflow.company import accounts as account_service, credits, document_effects as effects, journals
from bookflow.company import list_service, payment_queries as query, sales, schema as c
from bookflow.company import sales_defaults as defaults
from bookflow.company.credit_transfer_models import (
    CreditTransferOutput, CreditTransferVoidOutput, TransferApplied, TransferDocument, TransferSource,
)
from bookflow.company.journal_models import parse_domestic_amount
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError, require_reason
from bookflow.core.ids import new_id
from bookflow.core.money import Money
from bookflow.core.registry import Applied, Plan, Touched

ITEM_NAME = 'Credit Transfer'
ACCOUNT_NAME = 'Credit Transfer Clearing'
#: A clearing account that nets to zero sits on the balance sheet; an Other Charge item may post there.
ACCOUNT_TYPE = 'other_current_asset'


def _invalid(field, problem, **details):
    error = BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': problem}]})
    error.details.update(details)
    return error


def _money(units, currency):
    return Money(units, currency).to_dict()


def _plain(units, currency):
    return Money(units, currency).to_dict()['amount']


# ------------------------------------------------------------------ who and what


def _root(s, row):
    ancestors = list_service.hierarchy_ancestors(s.company, c.customers, row)
    return (ancestors[-1] if ancestors else row)['id']  # nearest first: the last is the customer


def _payment_ids(s, party_id):
    k, t, r = c.payment_component_keys, c.transactions, c.transaction_revisions
    return [row[0] for row in s.company.conn.execute(
        sa.select(t.c.id).select_from(k.join(t, t.c.id == k.c.transaction_id).join(r, r.c.id == t.c.current_revision_id))
        .where(k.c.party_id == party_id, t.c.type == 'payment', t.c.status == 'posted')
        .order_by(r.c.date, t.c.number, t.c.id).distinct())]


def _credits(s, party, inp):
    """The source's credits with money left, oldest first: (type, id, number, date, available, ar, version)."""
    from bookflow.company.payment_deletions import require_not_deleted
    found = []
    named = inp.credit_memo is not None or inp.payment is not None
    if inp.credit_memo is not None or not named:
        ids = [credits.resolve(s, inp.credit_memo)['id']] if inp.credit_memo is not None else [
            row['id'] for row in credits.open_credits(s, party['id'])]
        for identifier in ids:
            facts = credits.facts(s, identifier, write=True)
            if facts['key']['party_id'] != party['id']:
                raise _invalid('credit_memo', f"credit memo {facts['header']['number']} is not {party['full_name']}'s; "
                               'name a credit memo of `from_customer`')
            if facts['header']['status'] != 'posted':
                raise BookflowError('E_APPLICATION_INACTIVE', details={'credit_memo_id': identifier})
            found.append(dict(type='credit_memo', id=identifier, number=facts['header']['number'],
                              date=facts['revision']['date'], available=facts['available'],
                              ar=facts['key']['ar_account_id'], version=facts['header']['version']))
    if inp.payment is not None or not named:
        ids = [sales.resolve(s, inp.payment, 'payment')['id']] if inp.payment is not None else _payment_ids(s, party['id'])
        for identifier in ids:
            try:
                require_not_deleted(s, identifier)
            except BookflowError:
                if inp.payment is not None:
                    raise
                continue
            facts = query.payment_facts(s, identifier, write=True)
            key = next((row for row in facts['keys'].values() if row['party_id'] == party['id']), None)
            if key is None:
                raise _invalid('payment', f"receipt {facts['header']['number']} holds no credit for {party['full_name']}; "
                               'name a receipt of `from_customer`')
            if facts['header']['status'] != 'posted':
                raise BookflowError('E_APPLICATION_INACTIVE', details={'payment_id': identifier})
            found.append(dict(type='payment', id=identifier, number=facts['header']['number'],
                              date=facts['revision']['date'], available=facts['available'].get(key['id'], 0),
                              ar=key['ar_account_id'], version=facts['header']['version']))
    return sorted((row for row in found if row['available'] > 0), key=lambda row: (row['date'], row['number'], row['id']))


def _draws(rows, units, date, currency, party):
    """Take ``units`` from the credits dated on or before ``date``, oldest first, on one receivable account."""
    eligible = [row for row in rows if row['date'] <= date]
    if eligible:
        ar = eligible[0]['ar']
        eligible = [row for row in eligible if row['ar'] == ar]
    available = sum(row['available'] for row in eligible)
    if available < units:
        later = sum(row['available'] for row in rows if row['date'] > date)
        raise BookflowError('E_CREDIT_UNAVAILABLE', details={
            'customer_id': party['id'], 'requested': _money(units, currency), 'available': _money(available, currency),
            'next': (f"{party['full_name']} has {Money(available, currency)} of credit dated on or before {date}"
                     + (f' ({Money(later, currency)} more is dated later; transfer on or after its date)' if later else '')
                     + '. `payment invoices` lists credit memos as available_credits; `payment query` with '
                       'has_available_credit lists receipts with unapplied money.')})
    draws, left = [], units
    for row in eligible:
        if not left:
            break
        take = min(left, row['available'])
        draws.append(dict(row, take=take))
        left -= take
    return draws


def _named(s, table, name):
    from bookflow.company.lists import normalize_display_name
    _, key = normalize_display_name(name)
    row = s.company.conn.execute(sa.select(table).where(table.c.full_name_key == key)).mappings().first()
    return dict(row) if row else None


def _transfer_item(s):
    """The Credit Transfer item as it stands, or None and what the write sets up first."""
    item = _named(s, c.items, ITEM_NAME)
    if item is not None:
        if not item['active'] or item['type'] != 'other_charge' or not item.get('sales_enabled', True):
            raise _invalid('from_customer', f'the "{ITEM_NAME}" item must be an active Other Charge item that sells '
                           '(it carries every credit transfer); make it so, or rename the item that has its name')
        return item['id'], None
    account = _named(s, c.accounts, ACCOUNT_NAME)
    if account is not None and (not account['active'] or account['type'] in ('accounts_receivable', 'accounts_payable', 'bank')
                                or account.get('system_role')):
        raise _invalid('from_customer', f'the "{ACCOUNT_NAME}" account cannot carry credit transfers (it is inactive or '
                       'belongs to another ledger); rename it, or create the Credit Transfer item on an account that can')
    return None, dict(account=account)


def _create_item(s, ctx, create):
    from bookflow.company import items as item_service
    at = clock.now_iso()
    touched = []
    account = create['account']
    if account is None:
        mutation = account_service.plan_account_create(
            s.company, {'name': ACCOUNT_NAME, 'type': ACCOUNT_TYPE,
                        'description': 'Customer credit moved between jobs; nets to zero'},
            actor_id=s.actor.id, via=ctx.interface.value, at=at)
        account_service.persist_account_mutation(s.company, mutation)
        account = mutation.after
        touched.append(Touched('account', account['id'], 'create', None, account['version'], dict(account), None, db='company'))
    code = s.company.conn.execute(sa.select(c.sales_tax_codes.c.id).where(
        c.sales_tax_codes.c.taxable.is_(False), c.sales_tax_codes.c.active.is_(True)).order_by(c.sales_tax_codes.c.code)).scalar()
    payload = {'name': ITEM_NAME, 'type': 'other_charge', 'sales_enabled': True,
               'description': 'Credit moved between jobs', 'income_account_id': account['id'], 'price': '0'}
    if code is not None:
        payload['sales_tax_code_id'] = code
    mutation = item_service.plan_item_create(s.company, payload, actor_id=s.actor.id, via=ctx.interface.value, at=at)
    item_service.persist_item_mutation(s.company, mutation)
    touched.append(Touched(item_service.ITEM_RECORD_TYPE, str(mutation.after['id']), mutation.action, None,
                           int(mutation.after['version']), mutation.after_snapshot, mutation.before_snapshot, db='company'))
    return mutation.after['id'], touched


def _documents(intent, item_id):
    from bookflow.company.credit_models import CreditLineInput, CreditMemoPostInput
    from bookflow.company.sales_models import InvoicePostInput, SalesLineInput
    source, target, currency = intent['source'], intent['target'], intent['currency']
    price = _plain(intent['units'], currency)
    memo = intent['memo']
    charge = InvoicePostInput(customer=source['id'], date=intent['date'], ar_account=intent['ar'], memo=memo,
        lines=[SalesLineInput(item=item_id, quantity='1', unit_price=price,
                              description=f"Credit moved to {target['full_name']}"[:2000])])
    credit = CreditMemoPostInput(customer=target['id'], date=intent['date'], ar_account=intent['ar'], memo=memo,
        lines=[CreditLineInput(item=item_id, quantity='1', unit_price=price,
                               description=f"Credit moved from {source['full_name']}"[:2000])])
    return charge, credit


def _targets(s, intent, apply_to):
    """The target invoices the moved credit pays at once, checked now, in the order given."""
    rows, left, currency = [], intent['units'], intent['currency']
    for index, entry in enumerate(apply_to):
        field = f'apply_to.{index}'
        header = sales.resolve(s, entry.invoice, ('invoice', 'statement_charge'))
        sales._version(s, header, entry.expected_version)
        facts = query.invoice_facts(s, header['id'], write=True)
        if facts['profile']['customer_id'] != intent['target']['id']:
            raise _invalid(field + '.invoice', f"{header['number']} is not {intent['target']['full_name']}'s; the moved "
                           'credit pays the target\'s own invoices')
        if entry.amount is None:
            units = min(facts['due'], left)
        else:
            raw = entry.amount if isinstance(entry.amount, str) else entry.amount.amount
            units = parse_domestic_amount(raw, currency, field + '.amount').minor_units
        if units <= 0 or units > facts['due'] or units > left:
            raise _invalid(field + '.amount', f"{header['number']} owes {Money(facts['due'], currency)} and "
                           f"{Money(left, currency)} of the moved credit is left; apply more than zero and no more than either")
        left -= units
        rows.append(dict(invoice_id=header['id'], number=header['number'], units=units, due=facts['due'] - units))
    return rows


def _intent(s, ctx, inp):
    require_reason(ctx.reason)
    currency = defaults._info(s.company)['home_currency']
    source = defaults._row(s.company, 'customer', inp.from_customer)
    target = defaults._row(s.company, 'customer', inp.to_customer)
    if source['id'] == target['id']:
        raise _invalid('to_customer', 'the credit is already there; name another job of the same customer')
    if _root(s, source) != _root(s, target):
        raise _invalid('to_customer', f"{target['full_name']} and {source['full_name']} are not jobs of the same customer; "
                       'credit moves only within one customer (to pay someone else back, use `customer-refund post`)')
    units = parse_domestic_amount(inp.amount, currency, 'amount').minor_units
    if units <= 0:
        raise _invalid('amount', 'move more than zero')
    if inp.credit_memo is not None and inp.payment is not None:
        raise _invalid('payment', 'name a credit memo or a receipt to draw on, not both')
    draws = _draws(_credits(s, source, inp), units, inp.date, currency, source)
    journals.open_dates(s, [inp.date])
    memo = inp.memo or f"Credit transfer from {source['full_name']} to {target['full_name']}"
    intent = dict(source=source, target=target, units=units, currency=currency, date=inp.date, draws=draws,
                  ar=draws[0]['ar'], memo=memo[:2000])
    intent['applied'] = _targets(s, intent, inp.apply_to)
    return intent


# ------------------------------------------------------------------ the transfer


def _output(intent, inp, *, transfer_id, charge, credit, fingerprint, clearing, status='posted', replay=False):
    currency = intent['currency']
    source, target = intent['source'], intent['target']
    applied = [TransferApplied(invoice_id=row['invoice_id'], number=row['number'], amount=_money(row['units'], currency),
                               still_due=_money(row['due'], currency)) for row in intent['applied']]
    words = [f"Moved {Money(intent['units'], currency)} of credit from {source['full_name']} to {target['full_name']} "
             f"on {intent['date']}: invoice {charge.number} charges {source['full_name']} and is paid by its credit ("
             + ', '.join(('credit memo ' if row['type'] == 'credit_memo' else 'receipt ') + row['number']
                         for row in intent['draws'])
             + f"); credit memo {credit.number} gives {target['full_name']} the credit."]
    if applied:
        words.append('Used at once on ' + ', '.join(f'{row.number} ({row.amount.amount} applied, {row.still_due.amount} still due)'
                                                    for row in applied) + '.')
    else:
        words.append(f"Apply it to {target['full_name']}'s invoices with `customer-credit apply` (credit memo {credit.number}).")
    words.append(f'{clearing} nets to zero. Undo it with `customer-credit transfer-void`.')
    return CreditTransferOutput(
        changed=not replay, idempotent_replay=replay, transfer_id=transfer_id, operation_key=inp.operation_key,
        status=status, date=intent['date'], from_customer=source['full_name'], to_customer=target['full_name'],
        amount=_money(intent['units'], currency),
        sources=[TransferSource(type=row['type'], id=row['id'], number=row['number'], amount=_money(row['take'], currency))
                 for row in intent['draws']],
        charge=charge, credit_memo=credit, applied=applied, clearing_account=clearing,
        facts_fingerprint=fingerprint, summary=' '.join(words))


def _replay(s, inp):
    t = c.customer_credit_transfers
    row = s.company.conn.execute(sa.select(t).where(t.c.operation_key == inp.operation_key)).mappings().first()
    if row is None:
        return None
    output = _recorded(s, dict(row), inp.operation_key)
    source = defaults._row(s.company, 'customer', inp.from_customer, active=False)
    target = defaults._row(s.company, 'customer', inp.to_customer, active=False)
    if (source['id'], target['id']) != (row['from_party_id'], row['to_party_id']):
        raise BookflowError('E_PAYMENT_OPERATION_KEY_REUSED')
    return Plan(output, dict(input=inp, replay=True))


def _recorded(s, row, operation_key):
    """A transfer as the records hold it now."""
    currency = row['currency']
    names = {party: defaults._row(s.company, 'customer', party, active=False)['full_name']
             for party in (row['from_party_id'], row['to_party_id'])}
    docs = {}
    for name in ('charge_invoice_id', 'credit_memo_id'):
        docs[name] = s.company.conn.execute(sa.select(c.transactions).where(c.transactions.c.id == row[name])).mappings().one()
    status = 'posted' if docs['charge_invoice_id']['status'] == 'posted' else 'voided'
    document = lambda key, kind, party: TransferDocument(id=docs[key]['id'], number=docs[key]['number'], type=kind,
                                                         customer=names[party], amount=_money(row['amount_minor_units'], currency))
    app = c.applications
    drawn = s.company.conn.execute(sa.select(app.c.paying_transaction_id, sa.func.sum(app.c.amount_minor_units))
        .where(app.c.paid_transaction_id == row['charge_invoice_id'], app.c.kind == 'apply')
        .group_by(app.c.paying_transaction_id)).all()
    sources = []
    for paying, units in drawn:
        header = s.company.conn.execute(sa.select(c.transactions).where(c.transactions.c.id == paying)).mappings().one()
        sources.append(TransferSource(type=header['type'], id=paying, number=header['number'], amount=_money(units, currency)))
    clearing = _named(s, c.accounts, ACCOUNT_NAME)
    return CreditTransferOutput(
        changed=False, idempotent_replay=True, transfer_id=row['id'], operation_key=operation_key, status=status,
        date=row['transfer_date'], from_customer=names[row['from_party_id']], to_customer=names[row['to_party_id']],
        amount=_money(row['amount_minor_units'], currency), sources=sources,
        charge=document('charge_invoice_id', 'invoice', row['from_party_id']),
        credit_memo=document('credit_memo_id', 'credit_memo', row['to_party_id']),
        applied=[], clearing_account=clearing['full_name'] if clearing else ACCOUNT_NAME,
        facts_fingerprint=query.digest([row['id'], status]),
        summary=(f"This transfer was already recorded ({status}); nothing was written again. "
                 f"{Money(row['amount_minor_units'], currency)} from {names[row['from_party_id']]} to {names[row['to_party_id']]} "
                 f"on {row['transfer_date']}."))


def prepare(s, ctx, inp):
    from bookflow.hub.access import one_authorization
    replay = _replay(s, inp)
    if replay is not None:
        return replay
    with one_authorization(s):
        intent = _intent(s, ctx, inp)
        item_id, create = _transfer_item(s)
        currency = intent['currency']
        if item_id is not None:
            charge_in, credit_in = _documents(intent, item_id)
            charge_plan = sales.prepare(s, ctx, charge_in, 'invoice', 'post')
            credit_plan = credits.prepare(s, ctx, credit_in)
            charge_number, credit_number = charge_plan.preview.number, credit_plan.preview.number
        else:
            charge_number, _ = effects.allocate(s, 'invoice', None)
            credit_number, _ = effects.allocate(s, 'credit_memo', None)
    clearing = (_named(s, c.accounts, ACCOUNT_NAME) or {}).get('full_name') or f'{ACCOUNT_NAME} (set up with this write)'
    charge = TransferDocument(id=None, number=charge_number, type='invoice', customer=intent['source']['full_name'],
                              amount=_money(intent['units'], currency))
    credit = TransferDocument(id=None, number=credit_number, type='credit_memo', customer=intent['target']['full_name'],
                              amount=_money(intent['units'], currency))
    fingerprint = query.digest([intent['source']['id'], intent['target']['id'], intent['units'], intent['date'],
        [(row['id'], row['version'], row['take']) for row in intent['draws']],
        [(row['invoice_id'], row['units']) for row in intent['applied']], item_id or 'new:' + ITEM_NAME,
        defaults._info(s.company)['closing_date']])
    if inp.expected_facts_fingerprint is not None and inp.expected_facts_fingerprint != fingerprint:
        raise BookflowError('E_PREVIEW_STALE', details={'reason': 'transfer_facts', 'current_facts_fingerprint': fingerprint})
    output = _output(intent, inp, transfer_id=None, charge=charge, credit=credit, fingerprint=fingerprint, clearing=clearing)
    return Plan(output, dict(input=inp, intent=intent, fingerprint=fingerprint, create=create, item_id=item_id))


def apply(plan, ctx, s):
    from bookflow.company import credit_settlement, payments
    from bookflow.company.credit_settlement_models import CreditApplicationItem, CreditApplyInput
    from bookflow.company.payment_models import InlineApplications, InvoiceAmount, PaymentApplyInput
    if plan.data.get('replay'):
        return Applied(plan.preview, [], 'recovered credit transfer')
    fresh = prepare(s, ctx, plan.data['input'])
    if fresh.data.get('replay'):
        return Applied(fresh.preview, [], 'recovered credit transfer')
    if fresh.data['fingerprint'] != plan.data['fingerprint']:
        raise BookflowError('E_PREVIEW_STALE', details={'reason': 'transfer_facts'})
    inp, intent = fresh.data['input'], fresh.data['intent']
    currency = intent['currency']
    # Each step is prepared from the books as the step before it left them and written under its own
    # audit event; they share this request, and a refusal in any of them rolls back all.
    item_id, created = fresh.data['item_id'], []
    if item_id is None:
        item_id, created = _create_item(s, ctx, fresh.data['create'])
    charge_in, credit_in = _documents(intent, item_id)
    charge_out = sales.apply(sales.prepare(s, ctx, charge_in, 'invoice', 'post'), ctx, s).output
    for position, draw in enumerate(intent['draws'], 1):
        version = sales.resolve(s, charge_out.id, 'invoice')['version']
        amount = _plain(draw['take'], currency)
        if draw['type'] == 'credit_memo':
            step = CreditApplyInput(credit_memo=draw['id'], expected_version=credits.resolve(s, draw['id'])['version'],
                                    applications=[CreditApplicationItem(invoice=charge_out.id, expected_version=version, amount=amount)],
                                    date=inp.date)
            credit_settlement.apply(credit_settlement.prepare(s, ctx, step, 'apply'), ctx, s)
        else:
            step = PaymentApplyInput(payment=draw['id'], expected_version=sales.resolve(s, draw['id'], 'payment')['version'],
                                     date=inp.date, operation_key=f'{inp.operation_key}.draw{position}',
                                     applications=InlineApplications(items=[InvoiceAmount(invoice=charge_out.id,
                                                                                          expected_version=version, amount=amount)]))
            payments.apply(payments.prepare(s, ctx, step, 'apply'), ctx, s)
    credit_out = credits.apply(credits.prepare(s, ctx, credit_in), ctx, s).output
    if intent['applied']:
        step = CreditApplyInput(credit_memo=credit_out.id, expected_version=credits.resolve(s, credit_out.id)['version'],
            applications=[CreditApplicationItem(invoice=row['invoice_id'],
                                                expected_version=sales.resolve(s, row['invoice_id'], ('invoice', 'statement_charge'))['version'],
                                                amount=_plain(row['units'], currency)) for row in intent['applied']],
            date=inp.date)
        credit_settlement.apply(credit_settlement.prepare(s, ctx, step, 'apply'), ctx, s)
    event, at = new_id(), clock.now_iso()
    record = dict(id=new_id(), operation_key=inp.operation_key, from_party_id=intent['source']['id'],
                  to_party_id=intent['target']['id'], transfer_date=inp.date, amount_minor_units=intent['units'],
                  currency=currency, charge_invoice_id=charge_out.id, credit_memo_id=credit_out.id,
                  reason=ctx.reason.strip()[:500], created_at=at, created_by=s.actor.id, created_via=ctx.interface.value,
                  audit_event_id=event)
    touched = created + [Touched('customer_credit_transfer', record['id'], 'create', None, 1, effects.decoded(record), db='company')]
    summary = f"move credit {Money(intent['units'], currency)} from {intent['source']['full_name']} to {intent['target']['full_name']}"
    audit.write_event_to(s.company, ctx, 'customer-credit transfer', summary, touched, actor_id=s.actor.id,
                         actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None), event_id=event)
    s.company.conn.execute(c.customer_credit_transfers.insert().values(**record))
    clearing = _named(s, c.accounts, ACCOUNT_NAME)['full_name']
    document = lambda out, kind, party: TransferDocument(id=out.id, number=out.number, type=kind, customer=party['full_name'],
                                                         amount=_money(intent['units'], currency))
    output = _output(intent, inp, transfer_id=record['id'], charge=document(charge_out, 'invoice', intent['source']),
                     credit=document(credit_out, 'credit_memo', intent['target']), fingerprint=fresh.data['fingerprint'],
                     clearing=clearing)
    return Applied(output, touched, summary, audited=True)


# ------------------------------------------------------------------ taking it back


def _live_applications(s, *, paid=None, paying=None):
    app, inverse = c.applications, sa.alias(c.applications, 'transfer_inverse')
    where = [app.c.kind == 'apply', ~sa.exists(sa.select(inverse.c.id).where(inverse.c.reverses_application_id == app.c.id))]
    if paid is not None:
        where.append(app.c.paid_transaction_id == paid)
    if paying is not None:
        where.append(app.c.paying_transaction_id == paying)
    return [dict(row) for row in s.company.conn.execute(sa.select(app).where(*where).order_by(app.c.id)).mappings()]


def _transfer_row(s, selector):
    t = c.customer_credit_transfers
    row = s.company.conn.execute(sa.select(t).where(t.c.id == str(selector).strip())).mappings().first()
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'customer_credit_transfer', 'field': 'transfer'})
    return dict(row)


def prepare_void(s, ctx, inp):
    from bookflow.hub.access import one_authorization
    require_reason(ctx.reason)
    with one_authorization(s):
        row = _transfer_row(s, inp.transfer)
        charge = sales.resolve(s, row['charge_invoice_id'], 'invoice')
        currency = row['currency']
        if charge['status'] != 'posted':
            output = CreditTransferVoidOutput(changed=False, transfer_id=row['id'], status='voided',
                                              summary='This transfer was already voided; nothing was written again.')
            return Plan(output, dict(input=inp, replay=True))
        facts = credits.facts(s, row['credit_memo_id'], write=True)
        if facts['consumptions']:
            raise BookflowError('E_HAS_REFUND', details={
                'credit_memo_id': row['credit_memo_id'],
                'refund_ids': sorted({item['transaction_id'] for item in facts['consumptions']}),
                'next': 'Part of the moved credit was refunded to the customer. Void that refund (`customer-refund void`), '
                        'then void the transfer.'})
        reopened = []
        for app in _live_applications(s, paying=row['credit_memo_id']):
            invoice = query.invoice_facts(s, app['paid_transaction_id'])
            reopened.append(TransferApplied(invoice_id=app['paid_transaction_id'], number=invoice['header']['number'],
                                            amount=_money(app['amount_minor_units'], currency),
                                            still_due=_money(invoice['due'] + app['amount_minor_units'], currency)))
        journals.open_dates(s, [row['transfer_date']])
    names = [defaults._row(s.company, 'customer', party, active=False)['full_name'] for party in (row['from_party_id'], row['to_party_id'])]
    words = [f"Voids the transfer of {Money(row['amount_minor_units'], currency)} from {names[0]} to {names[1]} on "
             f"{row['transfer_date']}: the charge invoice and the credit memo are voided at that date and "
             f"{names[0]}'s credit is free again."]
    if reopened:
        words.append('Owed again: ' + ', '.join(f'{item.number} ({item.still_due.amount})' for item in reopened) + '.')
    output = CreditTransferVoidOutput(transfer_id=row['id'], status='voided', reopened=reopened, summary=' '.join(words))
    return Plan(output, dict(input=inp, row=row, fingerprint=query.digest([row['id'], charge['version'], facts['header']['version']])))


def apply_void(plan, ctx, s):
    from bookflow.company import credit_settlement, payments
    from bookflow.company.credit_models import CreditMemoVoidInput
    from bookflow.company.credit_settlement_models import CreditUnapplyInput, CreditUnapplyReference
    from bookflow.company.payment_models import PaymentUnapplyInput, UnapplyReference
    from bookflow.company.sales_models import InvoiceVoidInput
    if plan.data.get('replay'):
        return Applied(plan.preview, [], 'credit transfer already voided')
    fresh = prepare_void(s, ctx, plan.data['input'])
    if fresh.data.get('replay'):
        return Applied(fresh.preview, [], 'credit transfer already voided')
    if fresh.data['fingerprint'] != plan.data['fingerprint']:
        raise BookflowError('E_PREVIEW_STALE', details={'reason': 'transfer_facts'})
    row = fresh.data['row']
    version = lambda identifier: s.company.conn.execute(sa.select(c.transactions.c.version).where(
        c.transactions.c.id == identifier)).scalar_one()

    def unapply_credit(credit_id, apps):
        step = CreditUnapplyInput(credit_memo=credit_id, expected_version=version(credit_id), applications=[
            CreditUnapplyReference(application_id=app['id'], invoice_expected_version=version(app['paid_transaction_id']))
            for app in apps])
        credit_settlement.apply(credit_settlement.prepare(s, ctx, step, 'unapply'), ctx, s)

    # The target's uses of the moved credit first, then the source's credit off the charge.
    target_apps = _live_applications(s, paying=row['credit_memo_id'])
    if target_apps:
        unapply_credit(row['credit_memo_id'], target_apps)
    by_source = {}
    for app in _live_applications(s, paid=row['charge_invoice_id']):
        by_source.setdefault(app['paying_transaction_id'], []).append(app)
    for position, (paying, apps) in enumerate(sorted(by_source.items()), 1):
        kind = s.company.conn.execute(sa.select(c.transactions.c.type).where(c.transactions.c.id == paying)).scalar_one()
        if kind == 'credit_memo':
            unapply_credit(paying, apps)
        else:
            for app in apps:
                step = PaymentUnapplyInput(payment=paying, expected_version=version(paying),
                    operation_key=f"xfer-void-{row['id']}-{position}-{app['id']}"[:128],
                    applications=[UnapplyReference(application_id=app['id'],
                                                   invoice_expected_version=version(row['charge_invoice_id']))])
                payments.apply(payments.prepare(s, ctx, step, 'unapply'), ctx, s)
    credits.apply(credits.prepare_void(s, ctx, CreditMemoVoidInput(credit_memo=row['credit_memo_id'])), ctx, s)
    sales.apply(sales.prepare(s, ctx, InvoiceVoidInput(invoice=row['charge_invoice_id']), 'invoice', 'void'), ctx, s)
    return Applied(fresh.preview, [], f"void credit transfer {row['id']}", audited=True)
