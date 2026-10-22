"""A vendor's refund check for an open vendor credit: banked as a deposit, settled by the credit.

The anchor's way, kept: the refund is a deposit whose line comes *from* Accounts Payable with the
vendor's name on it. That line credits Accounts Payable -- the payable rises by exactly what the
credit lowered it by -- and debits the bank. The credit is then applied to that deposit line the
way it is applied to a bill, so both close: the vendor's balance is right the moment the deposit
posts, and the application only says which credit the money answered.

**The deposit line is a payable.** A bill owns an ``ap_obligation_keys`` row from the moment it
posts. A deposit does not carry one until a credit is first pointed at it: the key for
``(deposit, vendor, payable account)`` is minted by that first application, inside the same
write, so the deposit's own aggregate (``deposit_persistence``) is untouched. What is open on it
is read here, from the deposit's current posting -- the net Accounts Payable credit carrying
that vendor -- less what is applied, so a deposit voided or corrected to less reads less.

**Voiding the refund reopens the credit.** ``deposit void`` and ``deposit delete`` release every
credit standing on the deposit in the same company transaction, each release dated at the
application it takes back. A ``deposit update`` that would leave a deposit owing a vendor less
than the credits already applied to it is refused; unapply first.

``vendor-credit refund`` is the one-step path: one deposit, one line, and the application, in
one write. The two-step path -- ``deposit post`` with the A/P line, then ``vendor-credit apply``
with ``deposits`` -- lands on exactly the same rows.
"""
from __future__ import annotations

import sqlalchemy as sa

from bookflow.company import ap_settlement, journals, schema as c
from bookflow.company import document_effects as effects
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.money import Money
from bookflow.core.registry import Touched

OBLIGATION_TABLE = ('ap_obligation_keys', 'ap_obligation_key', 'id')


def _invalid(field, problem):
    return BookflowError('E_VALIDATION', message=f'{field}: {problem}',
                         details={'fields': [{'field': field, 'problem': problem}]})


def resolve_deposit(s, selector):
    t = c.transactions
    found = effects.rows(s, t, t.c.id == selector, t.c.type == 'deposit')
    if not found:
        found = effects.rows(s, t, t.c.number == selector, t.c.type == 'deposit')
    if len(found) != 1:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'deposit': selector})
    return found[0]


def payable_lines(s, deposit_id):
    """What the deposit's current posting owes each ``(vendor, payable account)``: net A/P credit.

    Read off the current revision's own posting batch, never its reversal, so a voided deposit
    owes nothing and a corrected one owes what the correction says.
    """
    header = effects.rows(s, c.transactions, c.transactions.c.id == deposit_id)
    if not header or header[0]['status'] != 'posted' or header[0]['type'] != 'deposit':
        return {}
    l, b, a = c.posting_lines, c.posting_batches, c.accounts
    query = (sa.select(l.c.name_id, l.c.account_id,
                       sa.func.sum(l.c.credit_minor_units - l.c.debit_minor_units).label('net'))
             .select_from(l.join(b, b.c.id == l.c.batch_id).join(a, a.c.id == l.c.account_id))
             .where(l.c.transaction_id == deposit_id, b.c.revision_id == header[0]['current_revision_id'],
                    b.c.kind != 'reversal', a.c.type == 'accounts_payable', l.c.name_type == 'vendor')
             .group_by(l.c.name_id, l.c.account_id))
    return {(row['name_id'], row['account_id']): row['net']
            for row in s.company.conn.execute(query).mappings() if row['net'] > 0}


def obligation_gross(s, key, header=None):
    """What a payable owes before any settlement: a bill's total, a refund deposit's A/P line."""
    if header is None:
        header = effects.rows(s, c.transactions, c.transactions.c.id == key['transaction_id'])[0]
    if header['type'] == 'deposit':
        return payable_lines(s, header['id']).get((key['vendor_id'], key['ap_account_id']), 0)
    revision = effects.rows(s, c.transaction_revisions,
                            c.transaction_revisions.c.id == header['current_revision_id'])[0]
    return revision['total_minor_units']


def deposit_keys(s, deposit_id):
    return effects.rows(s, c.ap_obligation_keys, c.ap_obligation_keys.c.transaction_id == deposit_id,
                        order=c.ap_obligation_keys.c.ordinal)


def selected_deposits(s, rows, date, source, *, capacity, pending, created):
    """Every refund deposit named, what is open on its line for this vendor, and what is taken.

    The deposit mirror of ``bill_payments._selected``: an unnamed amount means what is open or
    what the credit still has free, whichever is less; a named amount above what is open is
    refused. A key this deposit does not carry yet is added to ``pending``.
    """
    from bookflow.company.journal_models import parse_domestic_amount

    currency = source['currency']
    chosen, remaining = [], capacity
    for index, row in enumerate(rows):
        field = f'deposits.{index}'
        header = resolve_deposit(s, row.deposit)
        if header['status'] != 'posted':
            raise BookflowError('E_APPLICATION_INACTIVE', details={
                'field': field, 'deposit_id': header['id'], 'deposit_number': header['number'],
                'reason': 'voided_deposit',
                'next': 'A voided deposit owes the vendor nothing; a credit cannot be applied to it.'})
        if row.expected_version is not None:
            journals.version_meta(s, header, row.expected_version)
        revision = journals.revision(s, header)
        triple = (source['vendor_id'], source['ap_account_id'])
        gross = payable_lines(s, header['id']).get(triple, 0)
        if not gross:
            raise BookflowError('E_APPLICATION_INCOMPATIBLE', details={
                'field': field, 'deposit_id': header['id'], 'deposit_number': header['number'],
                'next': 'This deposit has no line drawn from this credit’s Accounts Payable '
                        'account with this vendor’s name; a credit settles only a refund '
                        'deposited that way.'})
        if date < revision['date']:
            raise _invalid(field, f'a settlement dated {date} cannot settle deposit '
                                  f'{header["number"]}, which is dated {revision["date"]}')
        key = next((k for k in deposit_keys(s, header['id'])
                    if (k['vendor_id'], k['ap_account_id']) == triple), None)
        if key is None:
            key = next((k for k in pending['ap_obligation_keys']
                        if k['transaction_id'] == header['id']
                        and (k['vendor_id'], k['ap_account_id']) == triple), None)
        if key is None:
            ordinals = [k['ordinal'] for k in deposit_keys(s, header['id'])]
            key = dict(id=new_id(), transaction_id=header['id'], ordinal=max(ordinals, default=0) + 1,
                       vendor_id=triple[0], ap_account_id=triple[1], currency=currency, **created())
            pending['ap_obligation_keys'].append(key)
            applied = 0
        else:
            applied = ap_settlement.applied_totals(s, [key['id']])[key['id']]
        open_amount = gross - applied
        if row.amount is None:
            amount = open_amount if remaining is None else min(open_amount, remaining)
        else:
            amount = parse_domestic_amount(row.amount, currency, field + '.amount').minor_units
        if amount <= 0:
            raise _invalid(field + '.amount', 'must be more than zero; deposit '
                           f'{header["number"]} has {Money(open_amount, currency).to_dict()["amount"]} '
                           f'{currency} open')
        if amount > open_amount:
            raise BookflowError('E_APPLICATION_CAPACITY', details={
                'field': field + '.amount', 'deposit_id': header['id'],
                'deposit_number': header['number'],
                'requested_minor_units': amount, 'available_minor_units': open_amount,
                'requested': Money(amount, currency).to_dict(),
                'available': Money(open_amount, currency).to_dict(),
                'next': 'Apply at most what is still open on this refund deposit.'})
        if remaining is not None:
            remaining -= amount
        chosen.append(dict(header=header, revision=revision, obligation=key, amount=amount,
                           discount=0, open=open_amount))
    return chosen


def check_minted(s, keys, header_id_event):
    """A key minted by a settlement write names a posted deposit that owes that vendor."""
    event = header_id_event
    for key in keys:
        header = effects.rows(s, c.transactions, c.transactions.c.id == key['transaction_id'])
        if not (header and header[0]['type'] == 'deposit' and header[0]['status'] == 'posted'
                and key['audit_event_id'] == event
                and payable_lines(s, key['transaction_id']).get((key['vendor_id'], key['ap_account_id']))
                and not any((k['vendor_id'], k['ap_account_id']) == (key['vendor_id'], key['ap_account_id'])
                            or k['ordinal'] == key['ordinal']
                            for k in deposit_keys(s, key['transaction_id']))):
            raise BookflowError('E_INTERNAL', message='Invalid vendor credit aggregate: '
                                                      'a payable minted on something that owes nothing')


# ------------------------------------------------------------------ the deposit's own writes


def _standing(s, deposit_id):
    a = c.ap_applications
    rows = [dict(r) for r in s.company.conn.execute(
        sa.select(a).where(a.c.obligation_transaction_id == deposit_id)
        .order_by(a.c.effective_date, a.c.id)).mappings()]
    reversed_ids = {r['reverses_application_id'] for r in rows if r['kind'] == 'unapply'}
    return [r for r in rows if r['kind'] == 'apply' and r['id'] not in reversed_ids]


def applied_by_key(s, deposit_id):
    totals = {}
    for row in _standing(s, deposit_id):
        totals[row['obligation_key_id']] = totals.get(row['obligation_key_id'], 0) + row['amount_minor_units']
    return totals


def guard_update(s, deposit_id, additional, date=None):
    """Refuse a correction that leaves a refund deposit owing less than the credits applied to it.

    Nor may it move the deposit after a credit already applied to it: an application can never
    precede what it settles, and one left dated before its deposit could no longer be released
    once its own period closed, though the deposit's period is open.
    """
    if not deposit_id:
        return
    standing = _standing(s, deposit_id)
    if not standing:
        return
    earliest = min(row['effective_date'] for row in standing)
    if date is not None and date > earliest:
        raise BookflowError('E_DEPOSIT_DEPENDENCY', message=(
            f'A vendor credit was applied to this refund deposit on {earliest}; the deposit cannot '
            f'be moved to {date}, after that settlement. Run vendor-credit unapply with deposits '
            'naming this deposit first, correct the date, then apply the credit again.'),
            details={'field': 'document.date', 'deposit_id': deposit_id, 'applied_on': earliest,
                     'next': 'vendor-credit unapply <credit> --deposits <deposit>'})
    applied = applied_by_key(s, deposit_id)
    owed = {}
    for row in additional:
        if row.account.type == 'accounts_payable' and row.dimensions.party_kind == 'vendor':
            pair = (row.dimensions.party_id, row.account.id)
            owed[pair] = owed.get(pair, 0) + row.units
    for key in deposit_keys(s, deposit_id):
        standing = applied.get(key['id'], 0)
        if standing > owed.get((key['vendor_id'], key['ap_account_id']), 0):
            raise BookflowError('E_DEPOSIT_DEPENDENCY', message=(
                'A vendor credit is applied to this refund deposit; the correction would leave it '
                'owing the vendor less than the credit already settled. Run vendor-credit unapply '
                'with deposits naming this deposit first, then correct it.'),
                details={'deposit_id': deposit_id, 'applied_minor_units': standing,
                         'next': 'vendor-credit unapply <credit> --deposits <deposit>'})


def check_release(s, deposit_id):
    """What ``release`` will need, asked before a void or deletion writes: each date it reopens is open."""
    standing = _standing(s, deposit_id)
    if standing:
        journals.open_dates(s, sorted({row['effective_date'] for row in standing}))


def release(s, ctx, deposit_id, command_name):
    """Take every credit standing on a deposit back off it: voiding the refund reopens the credit.

    Run inside the same company transaction as the void or deletion, after it. Each release is
    the exact inverse of its application at that application's own date, so those dates have to
    be open. The credits' versions move; the deposit's own was already moved by its void.
    """
    standing = _standing(s, deposit_id)
    if not standing:
        return []
    journals.open_dates(s, sorted({row['effective_date'] for row in standing}))
    at, event = clock.now_iso(), new_id()
    edges = [dict(id=new_id(), created_at=at, created_by=s.actor.id, created_via=ctx.interface.value,
                  audit_event_id=event, kind='unapply',
                  **{k: row[k] for k in ('source_transaction_id', 'source_key_id', 'source_component_id',
                                         'obligation_transaction_id', 'obligation_key_id',
                                         'amount_minor_units', 'currency', 'effective_date')},
                  reverses_application_id=row['id']) for row in standing]
    touched, headers = [], []
    for credit_id in sorted({row['source_transaction_id'] for row in standing}):
        old = effects.rows(s, c.transactions, c.transactions.c.id == credit_id)[0]
        new = dict(old, version=old['version'] + 1, updated_at=at, updated_by=s.actor.id,
                   updated_via=ctx.interface.value)
        headers.append(new)
        touched.append(Touched('transaction', credit_id, 'update', old['version'], new['version'],
                               new, old, db='company'))
    touched.extend(Touched('ap_application', row['id'], 'create', None, 1, effects.decoded(row), db='company')
                   for row in edges)
    number = effects.rows(s, c.transactions, c.transactions.c.id == deposit_id)[0]['number']
    audit.write_event_to(s.company, ctx, command_name,
                         f'vendor credits released by deposit {number}', touched,
                         actor_id=s.actor.id, actor_kind=s.actor.kind,
                         directive_code=getattr(s, 'directive_code', None), event_id=event)
    s.company.conn.execute(c.ap_applications.insert(), edges)
    for new in headers:
        s.company.conn.execute(c.transactions.update().where(c.transactions.c.id == new['id']).values(**new))
    return touched


# ------------------------------------------------------------------ vendor-credit refund


def _refund_facts(s, ctx, inp):
    """Everything the one-step refund decides before it writes, read fresh each time it is asked."""
    from bookflow.company import bill_payments, vendor_credits
    from bookflow.company.journal_models import parse_domestic_amount

    header, revision = vendor_credits._credit_for_write(s, inp)
    currency = revision['currency']
    source = vendor_credits.source_row(s, header['id'])
    if source is None:
        raise BookflowError('E_INTERNAL', message='This credit carries no settlement source')
    free = sum(units for _, units in bill_payments._free_capacity(s, header, revision) if units > 0)
    if inp.amount is None:
        amount = free
    else:
        amount = parse_domestic_amount(inp.amount, currency, 'amount').minor_units
        if amount <= 0:
            raise _invalid('amount', 'must be more than zero')
    if amount <= 0 or amount > free:
        raise BookflowError('E_APPLICATION_CAPACITY', details={
            'field': 'amount', 'credit_id': header['id'], 'credit_number': header['number'],
            'requested_minor_units': amount, 'available_minor_units': free,
            'requested': Money(amount, currency).to_dict(), 'available': Money(free, currency).to_dict(),
            'next': 'A refund can be at most what the credit still has free. A refund for more than '
                    'the vendor owes is not a refund of this credit: record the rest as ordinary '
                    'deposit income.' if free else
                    'This credit has nothing free to refund; vendor-credit unapply frees what it holds.'})
    from bookflow.company import sales_defaults
    bank = sales_defaults._row(s.company, 'account', inp.deposit_to, active=False)
    if bank['type'] != 'bank' or bank['system_role'] is not None:
        raise _invalid('deposit_to', f'must be a bank account; "{bank["full_name"]}" is '
                                     + ('Undeposited Funds' if bank['system_role'] == 'undeposited_funds'
                                        else 'a ' + bank['type'].replace('_', ' ') + ' account')
                                     + '. Name the bank account the refund check was deposited into.')
    if inp.date < revision['date']:
        raise _invalid('date', f'a refund dated {inp.date} cannot come before credit '
                               f'{header["number"]}, which is dated {revision["date"]}')
    journals.open_dates(s, [inp.date])
    vendor = effects.rows(s, c.vendors, c.vendors.c.id == source['vendor_id'])[0]
    return dict(header=header, revision=revision, source=source, currency=currency, free=free,
                amount=amount, vendor=vendor)


def _deposit_input(inp, facts, operation_key):
    from bookflow.company.deposit_lifecycle_models import PostInput
    line = dict(received_from=dict(kind='vendor', id=facts['source']['vendor_id']),
                from_account=facts['source']['ap_account_id'],
                amount=Money(facts['amount'], facts['currency']).to_dict()['amount'],
                memo=inp.memo or f'Refund of vendor credit {facts["header"]["number"]}')
    if inp.check_number is not None:
        line['check_number'] = inp.check_number
    if inp.payment_method is not None:
        line['payment_method'] = inp.payment_method
    document = dict(mode='inline', deposit_to=inp.deposit_to, date=inp.date, additional=[line])
    for name in ('number', 'memo'):
        if getattr(inp, name) is not None:
            document[name] = getattr(inp, name)
    return PostInput.model_validate(dict(operation_key=operation_key, document=document))


def _output(facts, inp, *, deposit_id, deposit_number, deposit_to_id, credit_version, changed=True):
    from bookflow.company.vendor_credit_models import VendorCreditRefundOutput
    currency, amount = facts['currency'], facts['amount']
    vendor = facts['vendor']
    return VendorCreditRefundOutput(
        changed=changed, credit_id=facts['header']['id'], credit_number=facts['header']['number'],
        credit_version=credit_version, vendor_id=vendor['id'],
        vendor_name=vendor.get('full_name') or vendor['name'],
        deposit_id=deposit_id, deposit_number=deposit_number, deposit_to_id=deposit_to_id,
        date=inp.date, amount=Money(amount, currency).to_dict(), amount_minor_units=amount,
        credit_free_before=Money(facts['free'], currency).to_dict(),
        credit_free_after=Money(facts['free'] - amount, currency).to_dict(),
        credit_free_after_minor_units=facts['free'] - amount)


def _operation_key(ctx):
    import re
    key = getattr(ctx, 'idempotency_key', None)
    if key:
        return 'vendor-refund:' + re.sub(r'[^A-Za-z0-9._:-]', '-', key)[:110]
    return 'vendor-refund:' + new_id()


def plan_refund(s, ctx, inp, binding):
    from bookflow.company import deposit_lifecycle, deposit_persistence
    from bookflow.core.registry import Plan

    facts = _refund_facts(s, ctx, inp)
    prepared = deposit_lifecycle.prepare(s, ctx, _deposit_input(inp, facts, _operation_key(ctx)),
                                         'post', binding=binding)
    preview = deposit_persistence.preview(s, ctx, prepared)
    output = _output(facts, inp, deposit_id=preview.current.id, deposit_number=preview.current.number,
                     deposit_to_id=_bank_id(preview),
                     credit_version=facts['header']['version'] + 1)
    return Plan(output, dict(input=inp))


def _bank_id(lifecycle_output):
    return lifecycle_output.effect.financial.intent.bank.id


def apply_refund(plan, ctx, s, binding):
    """Bank the refund and settle the credit, in the caller's one company transaction."""
    from bookflow.company import deposit_lifecycle, deposit_persistence, vendor_credits
    from bookflow.company.deposit_lifecycle_models import LifecycleOutput
    from bookflow.company.vendor_credit_models import VendorCreditApplyInput
    from bookflow.core.registry import Applied

    inp = plan.data['input']
    facts = _refund_facts(s, ctx, inp)
    prepared = deposit_lifecycle.prepare(s, ctx, _deposit_input(inp, facts, _operation_key(ctx)),
                                         'post', binding=binding)
    if isinstance(prepared, LifecycleOutput):
        raise BookflowError('E_IDEMPOTENCY_MISMATCH', message='This refund was already recorded.')
    deposit = deposit_persistence.execute(s, ctx, prepared)
    amount = Money(facts['amount'], facts['currency']).to_dict()['amount']
    settle = VendorCreditApplyInput(credit=facts['header']['id'], date=inp.date,
                                    deposits=[dict(deposit=deposit.current.id, amount=amount)])
    settled = vendor_credits.apply(vendor_credits.prepare_apply(s, ctx, settle), ctx, s)
    credit = effects.rows(s, c.transactions, c.transactions.c.id == facts['header']['id'])[0]
    output = _output(facts, inp, deposit_id=deposit.current.id, deposit_number=deposit.current.number,
                     deposit_to_id=_bank_id(deposit), credit_version=credit['version'])
    return Applied(output, settled.touched,
                   f"refund of vendor credit {facts['header']['number']} banked as deposit "
                   f"{deposit.current.number}", audited=True)


def obligation_types(s, identifiers):
    """Which of these settled payables are refund deposits rather than bills."""
    identifiers = sorted(identifiers)
    if not identifiers:
        return {}
    t = c.transactions
    return {row['id']: 'deposit' for row in s.company.conn.execute(
        sa.select(t.c.id).where(t.c.id.in_(identifiers), t.c.type == 'deposit')).mappings()}
