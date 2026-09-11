"""Paying a bill: money out of an account, and the payables it answers.

Two things happen when a bill is paid and they are deliberately separate.

**The money.** One debit to Accounts Payable and one credit to the account the money came
from -- a bank, which is debit-normal and so falls, or a credit card, which is credit-normal
and so rises. That is the whole ledger effect, and it is posted once, at the payment's date.

**The answer.** Each selected bill gets an ``ap_applications`` row saying this much of this
payment settled that payable. An application posts nothing: the cash has already left. What it
decides is which bill is closed. So unapplying one restores the bill's open balance and leaves
the bank exactly where it was, and the payment is then an unapplied debit against the vendor
rather than money that went missing.

**One payee per payment.** Bills are grouped by ``(vendor, payable account, currency, funding
account, method)`` and each group is one document. The funding account and the method come
from the command, and the currency is the home currency, so what actually splits a selection is
the vendor and the payable it is owed from -- and two vendors never share a check.

**What this does not write.** A purchase discount and a vendor credit are their own documents
with their own accounts, and neither exists yet; every cent here is cash or card. That is why
``due = applied + open`` with no third term, and why a selection that would settle nothing is
refused rather than posted as a payment for zero.
"""
from __future__ import annotations

import json

import sqlalchemy as sa

from bookflow.company import accounts, ap_settlement, bills, journals, list_service, schema as c
from bookflow.company import document_effects as effects
from bookflow.company.bill_payment_facts import Account, BillPaymentProfile, Origin, PaymentMethod, Reference, Vendor
from bookflow.company.bill_payment_models import (
    BillApplicationOutput, BillPayOutput, BillPaymentHistoryOutput, BillPaymentLineOutput,
    BillPaymentOutput, BillPaymentPageOutput, BillPaymentRevisionOutput,
    BillPaymentRevisionSummaryOutput, BillPaymentSettlementOutput, BillPaymentSummaryOutput,
    BillPaymentWriteOutput,
)
from bookflow.company.journal_models import checked_sum, parse_domestic_amount
from bookflow.company.lists import get_list_definition
from bookflow.company.parties import resolve_party
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError
from bookflow.core.exact import INT64_MAX
from bookflow.core.ids import is_ulid, new_id
from bookflow.core.money import Money
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.hub.users import common

DOCUMENT_TYPE = 'bill_payment'

# Insert order matters: an attribution row follows the posting line it hangs off, a source
# component follows both the source it belongs to and the attribution it names, and an
# application follows the component whose capacity it consumes.
TABLE_KINDS = (
    ('transaction_revisions', 'transaction_revision', 'id'),
    ('document_line_identities', 'document_line_identity', 'id'),
    ('document_lines', 'document_line', 'id'),
    ('ap_payment_profiles', 'ap_payment_profile', 'revision_id'),
    ('posting_batches', 'posting_batch', 'id'),
    ('posting_lines', 'posting_line', 'id'),
    ('posting_line_sources', 'posting_line_source', 'id'),
    ('ap_source_keys', 'ap_source_key', 'id'),
    ('ap_source_components', 'ap_source_component', 'id'),
    ('ap_applications', 'ap_application', 'id'),
)

# What a bill payment may be drawn on, and what crediting it means. A debit card or an EFT is
# money out of the bank, not credit-card debt, so the account type decides this and not the
# method: the method is what the vendor was handed, the account is where the money is.
FUNDING_KIND = {'bank': 'bank_cash', 'credit_card': 'card_liability'}


def json_text(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _invalid(field, problem):
    return BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': problem}]})


def resolve(s, selector):
    t = c.transactions
    key = selector.upper() if is_ulid(selector) else selector
    found = effects.rows(s, t, t.c.type == DOCUMENT_TYPE, t.c.id == key)
    if not found:
        found = effects.rows(s, t, t.c.type == DOCUMENT_TYPE, t.c.number == selector)
    if not found:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': DOCUMENT_TYPE, 'selector': selector})
    return found[0]


def profile_row(s, revision):
    return effects.rows(s, c.ap_payment_profiles,
                        c.ap_payment_profiles.c.revision_id == revision['id'])[0]


def _info(s):
    return dict(s.company.conn.execute(sa.select(c.company_info)).mappings().one())


def _reference(row):
    return Reference(id=row['id'], label=row.get('full_name') or row.get('name') or row.get('code'),
                     version=row['version'])


def _account_facts(row):
    return Account(**{k: row[k] for k in ('id', 'name', 'full_name', 'number', 'type')},
                   normal_balance=accounts.NORMAL_BALANCE[row['type']])


def _account_row(s, selector, field):
    try:
        row = accounts.resolve_account(s.company, selector)
    except BookflowError as exc:
        if exc.code in ('E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE'):
            exc.details.setdefault('record_type', 'account')
            exc.details['field'] = field
        raise
    if not row['active']:
        raise BookflowError('E_INACTIVE_REFERENCE',
                            details={'record_type': 'account', 'record_id': row['id'], 'field': field})
    return row


def _list_row(s, noun, table, selector, field, kind):
    row = list_service.resolve_selector(s.company, table, get_list_definition(noun), selector)
    if not row['active']:
        raise BookflowError('E_INACTIVE_REFERENCE',
                            details={'record_type': kind, 'record_id': row['id'], 'field': field})
    return row


# ---------------------------------------------------------------- what a payment is drawn on


def _funding(s, selector, currency):
    row = _account_row(s, selector, 'funding_account')
    if row['type'] not in FUNDING_KIND:
        raise _invalid('funding_account',
                       f'"{row["full_name"]}" is a {row["type"].replace("_", " ")} account; a bill is '
                       'paid from a bank account or charged to a credit card account')
    if row['currency'] != currency:
        raise _invalid('funding_account', 'account must use the home currency')
    return _account_facts(row), FUNDING_KIND[row['type']]


def _method(s, selector):
    return PaymentMethod(**_reference(row := _list_row(
        s, 'payment-method', c.payment_methods, selector, 'method', 'payment method')).model_dump(),
        kind=row['kind'])


def _check_number(inp, method, funding_kind):
    """A check number belongs to a check. Nothing else has one to write on."""
    if inp.check_number is None:
        return None
    number = inp.check_number.strip()
    if not number:
        raise _invalid('check_number', 'must not be blank')
    if method.kind != 'check':
        raise _invalid('check_number', f'"{method.label}" is not a check, so it carries no check number')
    if funding_kind != 'bank_cash':
        raise _invalid('check_number', 'a check is written on a bank account, not a credit card')
    return number


# ---------------------------------------------------------------- what is being paid


def _selected(s, rows, date, currency, *, capacity=None):
    """Every bill named, what is open on it, and what this settlement takes off it.

    Both directions of money out read this: ``bill pay``, where ``date`` is the day the money
    left, and ``bill payment apply``, where it is the day free capacity was attached. The open
    balance, the per-row default, the refusal of a row that settles nothing and the refusal of
    one that settles past what is open are therefore one implementation, not two that agree.

    ``capacity`` is what the settlement has to spend, and only a row naming no amount feels it:
    ``bill pay`` decides its own amount from the selection and passes none, so an unnamed row
    means the whole open balance; ``bill payment apply`` is spending money that already exists,
    so an unnamed row means the open balance or what is left of the payment, whichever is less.
    A named amount is taken as named either way, and refused above what is open.
    """
    chosen, remaining = [], capacity
    for index, row in enumerate(rows):
        field = f'bills.{index}'
        header = bills.resolve(s, row.bill)
        revision = journals.revision(s, header)
        obligation = bills.obligation_row(s, header['id'])
        if header['status'] != 'posted' or obligation is None:
            raise BookflowError('E_APPLICATION_INACTIVE', details={
                'field': field, 'bill_id': header['id'], 'bill_number': header['number'],
                'reason': 'voided_bill' if header['status'] == 'voided' else 'no_payable'})
        if row.expected_version is not None:
            journals.version_meta(s, header, row.expected_version)
        if obligation['currency'] != currency:
            raise BookflowError('E_APPLICATION_INCOMPATIBLE', details={
                'field': field, 'bill_id': header['id'], 'currency': obligation['currency']})
        if date < revision['date']:
            raise _invalid(field, f'a settlement dated {date} cannot settle bill {header["number"]}, '
                                  f'which is dated {revision["date"]}')
        applied = ap_settlement.applied_totals(s, [obligation['id']])[obligation['id']]
        open_amount = revision['total_minor_units'] - applied
        if row.amount is None:
            amount = open_amount if remaining is None else min(open_amount, remaining)
        else:
            amount = parse_domestic_amount(row.amount, currency, field + '.amount').minor_units
        if amount <= 0 and remaining is not None and remaining <= 0 and row.amount is None:
            raise _invalid(field + '.amount', 'nothing is left to apply by the time this row is '
                                              f'reached; bill {header["number"]} has '
                                              f'{Money(open_amount, currency).to_dict()["amount"]} '
                                              f'{currency} open')
        if amount <= 0:
            # Nothing here writes a discount or a vendor credit, so a row that settles nothing
            # is a mistake rather than a zero-cash settlement; refusing it is also what keeps a
            # whole selection from posting a payment written for zero.
            raise _invalid(field + '.amount', f'must be more than zero; bill {header["number"]} has '
                                              f'{Money(open_amount, currency).to_dict()["amount"]} {currency} open')
        if amount > open_amount:
            raise BookflowError('E_APPLICATION_CAPACITY', details={
                'field': field + '.amount', 'bill_id': header['id'], 'bill_number': header['number'],
                'requested_minor_units': amount, 'available_minor_units': open_amount,
                'requested': Money(amount, currency).to_dict(),
                'available': Money(open_amount, currency).to_dict(),
                'next': 'Pay at most what is still open on this bill; a vendor credit is a separate document.'})
        if remaining is not None:
            remaining -= amount
        chosen.append(dict(header=header, revision=revision, obligation=obligation, amount=amount))
    return chosen


def _groups(chosen):
    """One payment per payee: same vendor, same payable, same currency."""
    ordered, seen = [], {}
    for row in chosen:
        key = (row['obligation']['vendor_id'], row['obligation']['ap_account_id'], row['obligation']['currency'])
        if key not in seen:
            seen[key] = []
            ordered.append(key)
        seen[key].append(row)
    return [(key, seen[key]) for key in ordered]


def _numbers(s, count, explicit):
    """``count`` free document numbers in one pass, and the sequence they leave behind.

    ``document_effects.allocate`` reads the database, which cannot see the numbers this same
    command is about to take; handing out several at once has to carry them itself.
    """
    if explicit is not None:
        if count != 1:
            raise _invalid('number', 'a number names one payment, and this selection makes '
                                     f'{count}; pay one payee at a time to number it yourself')
        return [effects.allocate(s, DOCUMENT_TYPE, explicit)[0]], None
    saved = effects.rows(s, c.sequences, c.sequences.c.name == DOCUMENT_TYPE)
    next_number, prefix = (saved[0]['next_number'], saved[0]['prefix']) if saved else (1, '')
    occupied = set(s.company.conn.execute(sa.select(c.transactions.c.number).where(
        c.transactions.c.type == DOCUMENT_TYPE)).scalars())
    numbers = []
    while len(numbers) < count:
        if next_number >= INT64_MAX:
            raise BookflowError('E_VALUE_RANGE', details={'field': 'next_number'})
        candidate = f'{prefix}{next_number}'
        next_number += 1
        if candidate not in occupied:
            numbers.append(candidate)
    return numbers, dict(name=DOCUMENT_TYPE, next_number=next_number, prefix=prefix)


# ---------------------------------------------------------------- reads


def settlement_output(header, revision, source, applied):
    amount = revision['total_minor_units'] if header['status'] == 'posted' else 0
    unapplied = amount - applied
    currency = revision['currency']
    return BillPaymentSettlementOutput(
        payment_id=header['id'], source_key_id=source['id'] if source else '',
        version=header['version'], revision_id=revision['id'],
        amount_minor_units=amount, applied_minor_units=applied, unapplied_minor_units=unapplied,
        amount=Money(amount, currency).to_dict(), applied=Money(applied, currency).to_dict(),
        unapplied=Money(unapplied, currency).to_dict(), currency=currency,
        status=('voided' if header['status'] == 'voided' else
                'applied' if unapplied == 0 else 'partial' if applied else 'unapplied'))


def summary(header, revision, profile, settlement):
    currency = revision['currency']
    captured = BillPaymentProfile.model_validate_json(profile['profile_snapshot'])
    return dict(header, date=revision['date'], vendor_id=profile['vendor_id'],
                vendor_name=captured.vendor.label, ap_account_id=profile['ap_account_id'],
                funding_account_id=profile['funding_account_id'], funding_kind=profile['funding_kind'],
                payment_method_id=profile['payment_method_id'],
                payment_method_name=captured.payment_method.label,
                check_number=profile['check_number'], reference=profile['reference'],
                memo=revision['memo'], currency=currency,
                total_minor_units=revision['total_minor_units'],
                total=Money(revision['total_minor_units'], currency).to_dict(),
                settlement_current=settlement)


def _bill_numbers(s, identifiers, pending=None):
    found = {row['transaction_id']: row['number'] for row in (pending or {}).get('_bill_headers', [])}
    missing = [identifier for identifier in identifiers if identifier not in found]
    if missing:
        found.update({row['id']: row['number'] for row in s.company.conn.execute(
            sa.select(c.transactions.c.id, c.transactions.c.number).where(
                c.transactions.c.id.in_(missing))).mappings()})
    return found


def application_outputs(rows, currency, numbers):
    return [BillApplicationOutput(
        **{k: v for k, v in row.items() if k != 'active'},
        bill_number=numbers.get(row['obligation_transaction_id'], ''),
        amount=Money(row['amount_minor_units'], currency).to_dict(),
        active=row['active']) for row in rows]


def _edges(s, header, pending=None):
    """Every settlement edge on this payment, including the ones a write is about to add.

    One list, whichever moment asks: the preview of a payment that does not exist yet, the
    preview of an unapply whose inverses are not stored yet, and the plain read afterwards all
    net the same way, so none of them can disagree about what is still attached.
    """
    saved = ap_settlement.applications(s, source_transaction_id=header['id'])
    combined = saved + [dict(row) for row in (pending or {}).get('ap_applications', [])]
    reversed_ids = {row['reverses_application_id'] for row in combined if row['kind'] == 'unapply'}
    for row in combined:
        row['active'] = row['kind'] == 'apply' and row['id'] not in reversed_ids
    return sorted(combined, key=lambda row: (row['effective_date'], row['id']))


def revision_output(s, header, revision, edges, pending=None):
    pending = pending or {}
    profile = next((row for row in pending.get('ap_payment_profiles', [])
                    if row['revision_id'] == revision['id']), None) or profile_row(s, revision)
    envelopes = [line for line in pending.get('document_lines', []) if line['revision_id'] == revision['id']]
    if not envelopes:
        envelopes = effects.rows(s, c.document_lines, c.document_lines.c.revision_id == revision['id'],
                                 order=c.document_lines.c.position)
    components = [row for row in pending.get('ap_source_components', [])
                  if row['revision_id'] == revision['id']]
    if not components:
        components = effects.rows(s, c.ap_source_components,
                                  c.ap_source_components.c.revision_id == revision['id'])
    by_line = {row['document_line_id']: row for row in components}
    # A line says where its capacity is attached now, not where it was entered: once an apply
    # can re-point a freed component at another bill, the entered target survives only in the
    # line's own description, and a component answering two bills names neither here.
    attached, targets = {}, {}
    for edge in edges:
        if not edge['active']:
            continue
        attached[edge['source_component_id']] = attached.get(edge['source_component_id'], 0) + edge['amount_minor_units']
        targets[edge['source_component_id']] = (edge['obligation_transaction_id']
                                                if edge['source_component_id'] not in targets
                                                or targets[edge['source_component_id']] == edge['obligation_transaction_id']
                                                else '')
    numbers = _bill_numbers(s, sorted({value for value in targets.values() if value}), pending)
    currency = revision['currency']
    saved_batches = effects.rows(s, c.posting_batches, c.posting_batches.c.revision_id == revision['id'],
                                 order=c.posting_batches.c.id)
    summaries = [journals.batch_output(s, batch) for batch in saved_batches]
    summaries += [journals.batch_output(s, batch, [line for line in pending['posting_lines']
                                                   if line['batch_id'] == batch['id']])
                  for batch in pending.get('posting_batches', []) if batch['revision_id'] == revision['id']]
    lines = []
    for envelope in envelopes:
        component = by_line[envelope['id']]
        bill_id = targets.get(component['id']) or ''
        applied = attached.get(component['id'], 0)
        lines.append(BillPaymentLineOutput(
            **{key: envelope[key] for key in ('id', 'created_at', 'created_by', 'created_via',
                                              'transaction_id', 'revision_id', 'line_id', 'position',
                                              'kind', 'class_id', 'class_name', 'description')},
            bill_id=bill_id, bill_number=numbers.get(bill_id, ''),
            source_component_id=component['id'], currency=currency,
            amount=Money(component['amount_minor_units'], currency).to_dict(),
            amount_minor_units=component['amount_minor_units'],
            applied_minor_units=applied, applied=Money(applied, currency).to_dict()))
    values = {k: v for k, v in revision.items() if not k.endswith('_snapshot')}
    values.update(total=Money(revision['total_minor_units'], currency).to_dict())
    return BillPaymentRevisionOutput(
        **values, profile=json.loads(profile['profile_snapshot']),
        issuer_snapshot=json.loads(revision['issuer_snapshot']), lines=lines, batches=summaries)


def revision_summary(s, revision, edges):
    """One history row: the revision, every batch it minted, every edge against its capacity.

    The edges are selected by the source components this revision owns, not by the payment,
    so a later correcting revision would carry its own and never another revision's.
    """
    currency = revision['currency']
    owned = {row['id'] for row in effects.rows(
        s, c.ap_source_components, c.ap_source_components.c.revision_id == revision['id'])}
    mine = [row for row in edges if row['source_component_id'] in owned]
    numbers = _bill_numbers(s, sorted({row['obligation_transaction_id'] for row in mine}))
    count = s.company.conn.execute(sa.select(sa.func.count()).select_from(c.document_lines)
                                   .where(c.document_lines.c.revision_id == revision['id'])).scalar_one()
    batches = effects.rows(s, c.posting_batches, c.posting_batches.c.revision_id == revision['id'],
                           order=c.posting_batches.c.id)
    values = {k: v for k, v in revision.items() if not k.endswith('_snapshot')}
    values.update(total=Money(revision['total_minor_units'], currency).to_dict())
    return BillPaymentRevisionSummaryOutput(
        **values, line_count=count,
        batches=[journals.batch_output(s, batch) for batch in batches],
        applications=application_outputs(mine, currency, numbers))


def _output(s, header, revision, profile, pending=None, *, model=BillPaymentOutput, **extra):
    edges = _edges(s, header, pending)
    source = ap_settlement.source_key_row(s, header['id'], pending)
    applied = sum(row['amount_minor_units'] for row in edges if row['active'])
    numbers = _bill_numbers(s, sorted({row['obligation_transaction_id'] for row in edges}), pending)
    return model(
        **summary(header, revision, profile, settlement_output(header, revision, source, applied)),
        revision=revision_output(s, header, revision, edges, pending),
        applications=application_outputs(edges, revision['currency'], numbers), **extra)


def show(s, inp):
    header = resolve(s, inp.payment)
    revision = journals.revision(s, header)
    return _output(s, header, revision, profile_row(s, revision))


def page(s, ctx, inp, *, history=False):
    from bookflow.company.query import continuation, page_state

    class Contract:
        cursor = inp.cursor
        query = None

        def model_dump(self, **kw):
            return inp.model_dump(**kw)

    state = page_state(s, 'bill payment ' + ('history' if history else 'query'), Contract(), ctx.on_behalf_of)
    if history:
        return _history_page(s, inp, state)
    t, r, p = c.transactions, c.transaction_revisions, c.ap_payment_profiles
    query = (sa.select(t.c.id).select_from(
        t.join(r, r.c.id == t.c.current_revision_id).join(p, p.c.revision_id == r.c.id))
        .where(t.c.type == DOCUMENT_TYPE))
    if inp.vendor:
        query = query.where(p.c.vendor_id == resolve_party(s.company, 'vendor', inp.vendor)['id'])
    if inp.funding_account:
        query = query.where(p.c.funding_account_id == accounts.resolve_account(s.company, inp.funding_account)['id'])
    if inp.method:
        query = query.where(p.c.payment_method_id == list_service.resolve_selector(
            s.company, c.payment_methods, get_list_definition('payment-method'), inp.method)['id'])
    if inp.bill:
        # "What paid this bill" is the read a person actually wants, so the bill is a filter
        # here rather than a separate command with its own paging rules.
        a = c.ap_applications
        query = query.where(t.c.id.in_(sa.select(a.c.source_transaction_id).where(
            a.c.obligation_transaction_id == bills.resolve(s, inp.bill)['id'])))
    if inp.date_from:
        query = query.where(r.c.date >= inp.date_from)
    if inp.date_to:
        query = query.where(r.c.date <= inp.date_to)
    if inp.status:
        query = query.where(t.c.status == inp.status)
    if inp.number:
        query = query.where(t.c.number.contains(inp.number, autoescape=True))
    if inp.check_number:
        query = query.where(p.c.check_number == inp.check_number)
    order = (r.c.date, t.c.id)
    query = query.order_by(*([column.desc() for column in order] if inp.direction == 'desc' else order))
    found = [dict(row) for row in s.company.conn.execute(
        query.offset(state.offset).limit(inp.limit + 1)).mappings()]
    more, found = len(found) > inp.limit, found[:inp.limit]
    identifiers = [row['id'] for row in found]
    headers = {row['id']: dict(row) for row in s.company.conn.execute(
        sa.select(c.transactions).where(c.transactions.c.id.in_(identifiers))).mappings()} if found else {}
    ordered = [headers[identifier] for identifier in identifiers]
    revision_ids = [header['current_revision_id'] for header in ordered]
    revisions = {row['id']: dict(row) for row in s.company.conn.execute(
        sa.select(c.transaction_revisions).where(
            c.transaction_revisions.c.id.in_(revision_ids))).mappings()} if found else {}
    profiles = {row['revision_id']: dict(row) for row in s.company.conn.execute(
        sa.select(c.ap_payment_profiles).where(
            c.ap_payment_profiles.c.revision_id.in_(revision_ids))).mappings()} if found else {}
    sources = {row['transaction_id']: dict(row) for row in s.company.conn.execute(
        sa.select(c.ap_source_keys).where(
            c.ap_source_keys.c.transaction_id.in_(identifiers))).mappings()} if found else {}
    applied = ap_settlement.source_applied_totals(s, [row['id'] for row in sources.values()])
    items = []
    for header in ordered:
        revision = revisions[header['current_revision_id']]
        source = sources.get(header['id'])
        total = applied.get(source['id'], 0) if source else 0
        items.append(BillPaymentSummaryOutput(**summary(
            header, revision, profiles[revision['id']],
            settlement_output(header, revision, source, 0 if header['status'] == 'voided' else total))))
    return BillPaymentPageOutput(items=items, count=len(found), has_more=more,
                                 next_cursor=continuation(state, len(found), more),
                                 audit_watermark=state.sequence)


def _history_page(s, inp, state):
    """The revision walk, oldest first, on the bill's own cursor discipline."""
    from bookflow.company.query import continuation
    header = resolve(s, inp.payment)
    revisions = [dict(row) for row in s.company.conn.execute(
        sa.select(c.transaction_revisions).where(
            c.transaction_revisions.c.transaction_id == header['id']).order_by(
            c.transaction_revisions.c.revision_number).offset(state.offset).limit(inp.limit + 1)).mappings()]
    more, revisions = len(revisions) > inp.limit, revisions[:inp.limit]
    edges = _edges(s, header)
    return BillPaymentHistoryOutput(
        **{key: header[key] for key in ('id', 'version', 'current_revision_id', 'number', 'status')},
        items=[revision_summary(s, revision, edges) for revision in revisions],
        count=len(revisions), has_more=more,
        next_cursor=continuation(state, len(revisions), more), audit_watermark=state.sequence)


# ---------------------------------------------------------------- writing one payment


def _document(s, ctx, inp, at, event, group, rows, number, resolved):
    """One payee's payment: its revision, its two legs, its capacity and its applications."""
    vendor_id, ap_account_id, currency = group

    def created():
        return dict(id=new_id(), created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)

    def audited():
        return dict(**created(), audit_event_id=event)

    provenance = dict(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)
    total = checked_sum((row['amount'] for row in rows), 'bills.total')
    vendor = resolved['vendors'][vendor_id]
    ap_account = resolved['payables'][ap_account_id]
    profile = BillPaymentProfile(
        vendor=vendor, ap_account=ap_account, funding_account=resolved['funding'],
        funding_kind=resolved['funding_kind'], payment_method=resolved['method'],
        check_number=resolved['check_number'], reference=inp.reference,
        amount_minor_units=total, currency=currency,
        origins={'funding_account': Origin(kind='explicit'), 'method': Origin(kind='explicit'),
                 'amount': Origin(kind='default')})
    header = dict(id=new_id(), **common(s.actor.id, ctx.interface.value, at), type=DOCUMENT_TYPE,
                  status='posted', voided_at=None, voided_by=None, void_reason=None,
                  void_posting_batch_id=None)
    pending = {table: [] for table, _, _ in TABLE_KINDS}
    revision = dict(**created(), transaction_id=header['id'], revision_number=1,
                    supersedes_revision_id=None, date=inp.date, number=number, name_type='vendor',
                    name_id=vendor_id, memo=inp.memo, total_minor_units=total, currency=currency,
                    issuer_snapshot=json_text(resolved['issuer']),
                    custom_fields_snapshot=json_text({}), audit_event_id=event)
    header.update(number=number, current_revision_id=revision['id'])
    pending['transaction_revisions'].append(revision)
    pending['ap_payment_profiles'].append(dict(
        transaction_id=header['id'], revision_id=revision['id'], **provenance, audit_event_id=event,
        type=DOCUMENT_TYPE, vendor_id=vendor_id, ap_account_id=ap_account_id,
        funding_account_id=resolved['funding'].id, funding_kind=resolved['funding_kind'],
        payment_method_id=resolved['method'].id, check_number=resolved['check_number'],
        reference=inp.reference, amount_minor_units=total,
        profile_snapshot=json_text(profile.model_dump())))
    klass = resolved['class_id']
    for position, row in enumerate(rows, 1):
        identity = dict(**created(), transaction_id=header['id'])
        pending['document_line_identities'].append(identity)
        pending['document_lines'].append(dict(
            **created(), transaction_id=header['id'], revision_id=revision['id'],
            line_id=identity['id'], position=position, kind=DOCUMENT_TYPE, account_id=None,
            side=None, amount_minor_units=None, currency=currency, account_snapshot=None,
            name_type='vendor', name_id=vendor_id, party_name=vendor.label,
            class_id=klass.id if klass else None, class_name=klass.label if klass else None,
            description=f'Bill {row["header"]["number"]}', **dict.fromkeys(journals.FACTS)))
    batch = dict(**audited(), transaction_id=header['id'], revision_id=revision['id'],
                 kind='original', effective_date=inp.date, reverses_batch_id=None,
                 replaces_batch_id=None)
    pending['posting_batches'].append(batch)
    line_no = 0

    def leg(account, amount, debit, description):
        nonlocal line_no
        line_no += 1
        value = dict(**created(), transaction_id=header['id'], batch_id=batch['id'], line_no=line_no,
                     account_id=account.id, account_snapshot=json_text(account.model_dump()),
                     currency=currency, name_type='vendor', name_id=vendor_id, party_name=vendor.label,
                     class_id=klass.id if klass else None, class_name=klass.label if klass else None,
                     description=description, debit_minor_units=amount if debit else 0,
                     credit_minor_units=0 if debit else amount,
                     reversed_line_id=None, **dict.fromkeys(journals.FACTS))
        pending['posting_lines'].append(value)
        return value

    def attribute(posting, envelope, units):
        source = dict(**created(), transaction_id=header['id'], posting_line_id=posting['id'],
                      revision_id=revision['id'], document_line_id=envelope['id'],
                      amount_minor_units=units, currency=currency,
                      reversed_source_id=None, tax_component_id=None)
        pending['posting_line_sources'].append(source)
        return source

    # The payable falls by the whole amount once, and the account that funded it rises or falls
    # by the same figure once; each entered row's share of both is an attribution on those legs,
    # which is the shape the bill used for its own payable and what an allocation later names.
    payable = leg(ap_account, total, True, inp.memo)
    funding = leg(resolved['funding'], total, False, inp.memo)
    source_key = dict(**audited(), transaction_id=header['id'], ordinal=1, source_type=DOCUMENT_TYPE,
                      vendor_id=vendor_id, ap_account_id=ap_account_id, currency=currency)
    pending['ap_source_keys'].append(source_key)
    for envelope, row in zip(pending['document_lines'], rows):
        payable_source = attribute(payable, envelope, row['amount'])
        attribute(funding, envelope, row['amount'])
        component = dict(**audited(), transaction_id=header['id'], revision_id=revision['id'],
                         key_id=source_key['id'], document_line_id=envelope['id'], ordinal=1,
                         posting_source_id=payable_source['id'], amount_minor_units=row['amount'],
                         currency=currency)
        pending['ap_source_components'].append(component)
        pending['ap_applications'].append(dict(
            **audited(), kind='apply', source_transaction_id=header['id'],
            source_key_id=source_key['id'], source_component_id=component['id'],
            obligation_transaction_id=row['header']['id'], obligation_key_id=row['obligation']['id'],
            amount_minor_units=row['amount'], currency=currency, effective_date=inp.date,
            reverses_application_id=None))
    return header, revision, pending


def _resolved(s, inp, currency):
    funding, funding_kind = _funding(s, inp.funding_account, currency)
    method = _method(s, inp.method)
    klass = (_reference(_list_row(s, 'class', c.classes, inp.class_id, 'class_id', 'class'))
             if inp.class_id else None)
    info = _info(s)
    return dict(funding=funding, funding_kind=funding_kind, method=method,
                check_number=_check_number(inp, method, funding_kind), class_id=klass,
                issuer={key: value for key, value in info.items()
                        if key in ('id', 'legal_name', 'home_currency')
                        or key.startswith(('address_', 'legal_address_'))})


def prepare_pay(s, ctx, inp):
    currency = _info(s)['home_currency']
    resolved = _resolved(s, inp, currency)
    chosen = _selected(s, inp.bills, inp.date, currency)
    journals.open_dates(s, [inp.date])
    groups = _groups(chosen)
    numbers, sequence = _numbers(s, len(groups), inp.number)
    at, event = clock.now_iso(), new_id()
    resolved['vendors'] = {key[0]: Vendor(**_reference(row := resolve_party(s.company, 'vendor', key[0])).model_dump(),
                                          **{k: row.get(k) for k in ('company_name', 'email', 'phone', 'account_number')})
                           for key, _ in groups}
    resolved['payables'] = {key[1]: _account_facts(_account_row(s, key[1], 'bills')) for key, _ in groups}
    documents, outputs, changed_headers = [], [], []
    seen_bills = {}
    for (group, rows), number in zip(groups, numbers):
        header, revision, pending = _document(s, ctx, inp, at, event, group, rows, number, resolved)
        pending['_bill_headers'] = [dict(transaction_id=row['header']['id'], number=row['header']['number'])
                                    for row in rows]
        documents.append(dict(header=header, revision=revision, pending=pending, rows=rows))
        outputs.append(_output(s, header, revision, pending['ap_payment_profiles'][0], pending))
        for row in rows:
            old = row['header']
            if old['id'] in seen_bills:
                continue
            changed = dict(old, version=old['version'] + 1, updated_at=at,
                           updated_by=s.actor.id, updated_via=ctx.interface.value)
            seen_bills[old['id']] = changed
            changed_headers.append((old, changed))
    total = checked_sum((row['amount'] for row in chosen), 'bills.total')
    preview = BillPayOutput(payments=outputs, group_count=len(groups), paid_minor_units=total,
                            paid=Money(total, currency).to_dict(), currency=currency,
                            bill_count=len(chosen))
    return Plan(preview, dict(input=inp, operation='pay', documents=documents, event=event, at=at,
                              sequence=sequence, changed_headers=changed_headers, currency=currency))


# ------------------------------------------------ re-pointing the money, and taking it back


def _payment_for_write(s, inp):
    header = resolve(s, inp.payment)
    if inp.expected_version is not None:
        journals.version_meta(s, header, inp.expected_version)
    revision = journals.revision(s, header)
    if header['status'] != 'posted':
        raise BookflowError('E_APPLICATION_INACTIVE', details={
            'payment_id': header['id'], 'status': header['status'],
            'next': 'A voided payment settles nothing and cannot be voided again.'})
    return header, revision, profile_row(s, revision)


def _free_capacity(s, header, revision, pending=None):
    """Each part of this payment's capacity, in entered order, and what nothing is holding.

    A component is capacity rather than a bill, so what is free on it is its own amount less
    the applications standing against it -- which is why an unapply gives capacity back
    instead of destroying it, and why this reads the same whether the payment has never been
    attached or has been attached and freed a dozen times.
    """
    components = effects.rows(s, c.ap_source_components,
                              c.ap_source_components.c.transaction_id == header['id'])
    envelopes = effects.rows(s, c.document_lines, c.document_lines.c.revision_id == revision['id'],
                             order=c.document_lines.c.position)
    by_line = {row['document_line_id']: row for row in components}
    attached = {}
    for edge in _edges(s, header, pending):
        if edge['active']:
            attached[edge['source_component_id']] = (attached.get(edge['source_component_id'], 0)
                                                     + edge['amount_minor_units'])
    return [(row, row['amount_minor_units'] - attached.get(row['id'], 0))
            for row in (by_line[envelope['id']] for envelope in envelopes)]


def _compatible(source, chosen, payment_id):
    """An application matches the vendor, the payable account and the currency, exactly.

    The storage trigger says the same thing and would abort the insert; saying it here names
    the row that is wrong and what about it, which an aborted insert cannot.
    """
    for index, row in enumerate(chosen):
        obligation = row['obligation']
        if all(obligation[key] == source[key] for key in ('vendor_id', 'ap_account_id', 'currency')):
            continue
        raise BookflowError('E_APPLICATION_INCOMPATIBLE', details={
            'field': f'bills.{index}', 'payment_id': payment_id,
            'bill_id': row['header']['id'], 'bill_number': row['header']['number'],
            'vendor_id': obligation['vendor_id'], 'ap_account_id': obligation['ap_account_id'],
            'currency': obligation['currency'],
            'next': 'A payment answers only the vendor, payable account and currency it was '
                    'written for; pay this bill with its own payment.'})


def prepare_apply(s, ctx, inp):
    """Attach what this payment still has free to more of the same vendor's open bills.

    Nothing is posted and no revision is written: the cash left when the payment posted, so
    the whole of this is settlement edges. Capacity is taken from the entered lines in order,
    which is why applying less than what is free leaves the rest free, and why one component
    can answer two bills when an amount straddles it.
    """
    header, revision, profile = _payment_for_write(s, inp)
    currency = revision['currency']
    date = inp.date or revision['date']
    if date < revision['date']:
        raise _invalid('date', f'an application dated {date} cannot come before payment '
                               f'{header["number"]}, which is dated {revision["date"]}')
    source = ap_settlement.source_key_row(s, header['id'])
    if source is None:
        raise BookflowError('E_INTERNAL', message='This payment carries no settlement source')
    supply = [[component, units] for component, units in _free_capacity(s, header, revision) if units > 0]
    available = sum(units for _, units in supply)

    def beyond(requested):
        return BookflowError('E_APPLICATION_CAPACITY', details={
            'payment_id': header['id'], 'payment_number': header['number'],
            'requested_minor_units': requested, 'available_minor_units': available,
            'requested': Money(requested, currency).to_dict(),
            'available': Money(available, currency).to_dict(),
            'next': 'Apply at most what this payment still has free; unapply what it holds, or '
                    'pay the rest with another payment.'})

    if not available:
        raise beyond(0)
    chosen = _selected(s, inp.bills, date, currency, capacity=available)
    _compatible(source, chosen, header['id'])
    # An application posts nothing, but it does change what the books say was open on a date,
    # so a closed period refuses it exactly as `payment apply` refuses one on the customer side.
    journals.open_dates(s, [date])
    total = checked_sum((row['amount'] for row in chosen), 'bills.total')
    if total > available:
        raise beyond(total)
    at, event = clock.now_iso(), new_id()
    pending = {table: [] for table, _, _ in TABLE_KINDS}
    position = 0
    for row in chosen:
        remaining = row['amount']
        while remaining:
            component, units = supply[position]
            if not units:
                position += 1
                continue
            taken = min(units, remaining)
            supply[position][1] -= taken
            remaining -= taken
            pending['ap_applications'].append(dict(
                id=new_id(), created_at=at, created_by=s.actor.id, created_via=ctx.interface.value,
                audit_event_id=event, kind='apply', source_transaction_id=header['id'],
                source_key_id=source['id'], source_component_id=component['id'],
                obligation_transaction_id=row['header']['id'],
                obligation_key_id=row['obligation']['id'], amount_minor_units=taken,
                currency=currency, effective_date=date, reverses_application_id=None))
    changed = dict(header, version=header['version'] + 1, updated_at=at,
                   updated_by=s.actor.id, updated_via=ctx.interface.value)
    changed_headers = [(row['header'], dict(row['header'], version=row['header']['version'] + 1,
                                            updated_at=at, updated_by=s.actor.id,
                                            updated_via=ctx.interface.value)) for row in chosen]
    return _plan(s, ctx, inp, 'apply', changed, header, revision, profile, pending, event, at,
                 changed_headers, ['applications'])


def prepare_unapply(s, ctx, inp):
    header, revision, profile = _payment_for_write(s, inp)
    active = ap_settlement.active_applications(s, header['id'])
    if inp.bills is not None:
        wanted = {bills.resolve(s, selector)['id'] for selector in inp.bills}
        missing = wanted - {row['obligation_transaction_id'] for row in active}
        if missing:
            raise BookflowError('E_APPLICATION_INACTIVE', details={
                'payment_id': header['id'], 'bill_ids': sorted(missing),
                'next': 'This payment has nothing still applied to that bill.'})
        active = [row for row in active if row['obligation_transaction_id'] in wanted]
    if not active:
        return _unchanged(s, header, revision, profile)
    # An unapply appends the exact negation of each application at that application's own
    # effective date, so each of those dates has to be open -- exactly as `payment unapply`
    # refuses on the customer side. Nothing here posts, but what the books say was open on a
    # closed date would change, which is the same rewrite of closed history a void is refused for.
    journals.open_dates(s, sorted({row['effective_date'] for row in active}))
    at, event = clock.now_iso(), new_id()
    pending = {table: [] for table, _, _ in TABLE_KINDS}
    for row in active:
        pending['ap_applications'].append(dict(
            id=new_id(), created_at=at, created_by=s.actor.id, created_via=ctx.interface.value,
            audit_event_id=event, kind='unapply',
            **{key: row[key] for key in ('source_transaction_id', 'source_key_id', 'source_component_id',
                                         'obligation_transaction_id', 'obligation_key_id',
                                         'amount_minor_units', 'currency', 'effective_date')},
            reverses_application_id=row['id']))
    changed = dict(header, version=header['version'] + 1, updated_at=at,
                   updated_by=s.actor.id, updated_via=ctx.interface.value)
    changed_headers = []
    for identifier in sorted({row['obligation_transaction_id'] for row in active}):
        old = effects.rows(s, c.transactions, c.transactions.c.id == identifier)[0]
        changed_headers.append((old, dict(old, version=old['version'] + 1, updated_at=at,
                                          updated_by=s.actor.id, updated_via=ctx.interface.value)))
    return _plan(s, ctx, inp, 'unapply', changed, header, revision, profile, pending, event, at,
                 changed_headers, ['applications'])


def prepare_void(s, ctx, inp):
    header, revision, profile = _payment_for_write(s, inp)
    if not ctx.reason or not ctx.reason.strip():
        raise BookflowError('E_REASON_REQUIRED')
    if len(ctx.reason.strip()) > 140:
        raise _invalid('reason', 'must be at most 140 characters')
    if ap_settlement.active_applications(s, header['id']):
        raise BookflowError('E_HAS_APPLICATIONS', details={
            'payment_id': header['id'],
            'next': 'Unapply what this payment settled before voiding it.'})
    journals.open_dates(s, [revision['date']])
    at, event = clock.now_iso(), new_id()

    def created():
        return dict(id=new_id(), created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)

    pending = {table: [] for table, _, _ in TABLE_KINDS}
    current_batch = effects.rows(s, c.posting_batches,
                                 c.posting_batches.c.revision_id == revision['id'],
                                 c.posting_batches.c.kind != 'reversal')[0]
    inverse = effects.reverse(s, header, revision, current_batch, event, created, pending)
    changed = dict(header, version=header['version'] + 1, updated_at=at, updated_by=s.actor.id,
                   updated_via=ctx.interface.value, status='voided', voided_at=at,
                   voided_by=s.actor.id, void_reason=ctx.reason.strip(),
                   void_posting_batch_id=inverse['id'])
    return _plan(s, ctx, inp, 'void', changed, header, revision, profile, pending, event, at, [], ['status'])


def _unchanged(s, header, revision, profile):
    return Plan(_output(s, header, revision, profile, model=BillPaymentWriteOutput, changed=False),
                dict(input=None, operation='unapply', changed=False))


def _plan(s, ctx, inp, operation, changed, header, revision, profile, pending, event, at,
          changed_headers, changed_fields):
    return Plan(_output(s, changed, revision, profile, pending, model=BillPaymentWriteOutput,
                        changed_fields=changed_fields),
                dict(input=inp, operation=operation, changed=True,
                     documents=[dict(header=changed, revision=revision, pending=pending,
                                     before=header, rows=[])],
                     event=event, at=at, sequence=None,
                     changed_headers=changed_headers, currency=revision['currency']))


# ---------------------------------------------------------------- persistence


def prepare(s, ctx, inp, operation):
    if operation == 'pay':
        return prepare_pay(s, ctx, inp)
    if operation == 'apply':
        return prepare_apply(s, ctx, inp)
    if operation == 'unapply':
        return prepare_unapply(s, ctx, inp)
    return prepare_void(s, ctx, inp)


def apply(plan, ctx, s):
    # Rebuilt inside the writer transaction, the way every document here does it: open
    # balances, numbering and dates are only decisive at the moment of the write.
    if plan.data.get('changed') is False:
        return Applied(plan.preview, [], 'no change')
    operation = plan.data['operation']
    fresh = prepare(s, ctx, plan.data['input'], operation)
    if fresh.data.get('changed') is False:
        return Applied(fresh.preview, [], 'no change')
    from bookflow.company.bill_payment_validation import validate
    validate(fresh, s, ctx)
    data = fresh.data
    touched, summaries = [], []
    for document in data['documents']:
        header, before = document['header'], document.get('before')
        touched.append(Touched('transaction', header['id'], 'update' if before else 'create',
                               before['version'] if before else None, header['version'],
                               header, before, db='company'))
        for table, kind, key in TABLE_KINDS:
            touched.extend(Touched(kind, row[key], 'create', None, 1, effects.decoded(row), db='company')
                           for row in document['pending'][table])
        summaries.append(header['number'])
    touched.extend(Touched('transaction', after['id'], 'update', old['version'], after['version'],
                           after, old, db='company') for old, after in data['changed_headers'])
    command_name = 'bill ' + ('pay' if operation == 'pay' else 'payment ' + operation)
    summary_text = f"{operation} bill payment {', '.join(summaries)}"
    audit.write_event_to(s.company, ctx, command_name, summary_text, touched,
                         actor_id=s.actor.id, actor_kind=s.actor.kind,
                         directive_code=getattr(s, 'directive_code', None), event_id=data['event'])
    for document in data['documents']:
        header, before = document['header'], document.get('before')
        if before:
            s.company.conn.execute(c.transactions.update().where(
                c.transactions.c.id == header['id']).values(**header))
        else:
            s.company.conn.execute(c.transactions.insert().values(**header))
        for table, _, _ in TABLE_KINDS:
            if document['pending'][table]:
                s.company.conn.execute(getattr(c, table).insert(), document['pending'][table])
    for _, after in data['changed_headers']:
        s.company.conn.execute(c.transactions.update().where(
            c.transactions.c.id == after['id']).values(**after))
    if data['sequence']:
        from sqlalchemy.dialects.sqlite import insert
        statement = insert(c.sequences).values(**data['sequence'])
        s.company.conn.execute(statement.on_conflict_do_update(index_elements=['name'], set_=data['sequence']))
    return Applied(fresh.preview, touched, summary_text, audited=True)
