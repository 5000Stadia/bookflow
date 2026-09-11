"""Paying a customer back: money out of a bank account, and the credit it uses up.

**The money.** One debit to the customer's Accounts Receivable and one credit to the bank
account the money left. That is the whole ledger effect, and it is posted once, at the
refund's date.

**No income leg and no tax leg, deliberately.** The credit memo being refunded already took
the income down and already took the sales tax liability down; a refund that touched either
again would reverse the same sale twice. What a refund does is settle the negative receivable
the credit left standing: the customer stops being owed money because they have been paid.

**Consumed once.** Every refunded cent names a credit memo and writes a
``customer_refund_consumptions`` row against it, so the same credit cannot be refunded and
then also applied to an invoice -- ``credits.facts`` subtracts consumptions and applications
from the same capacity. This is the concrete double-spend the document exists to close:
``registers.REGISTER_TYPES`` already admits an Accounts Receivable register, so a check
debiting AR for a customer posts the cash correctly and consumes nothing at all.

**Immutable, void only.** There is no ``update``. A refund is one customer, one amount, one
date and one account; changing any of them makes it a different refund. Voiding it reverses
the accounting at the refund's own date and releases every consumption, so the credits it
paid out are worth again exactly what they were worth before.
"""
from __future__ import annotations

import json

import sqlalchemy as sa

from bookflow.company import accounts, credits, journals, list_service, schema as c
from bookflow.company import document_effects as effects
from bookflow.company.journal_models import parse_domestic_amount
from bookflow.company.lists import get_list_definition
from bookflow.company.parties import resolve_party
from bookflow.company.refund_facts import (
    Account, Customer, CustomerRefundProfile, Origin, PaymentMethod, Reference, RefundSource,
)
from bookflow.company.refund_models import (
    CustomerRefundConsumptionOutput, CustomerRefundLineOutput, CustomerRefundOutput,
    CustomerRefundPageOutput, CustomerRefundRevisionOutput, CustomerRefundSummaryOutput,
    CustomerRefundWriteOutput,
)
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError
from bookflow.core.exact import INT64_MAX
from bookflow.core.ids import is_ulid, new_id
from bookflow.core.money import Money
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.hub.users import common

DOCUMENT_TYPE = 'customer_refund'

# Insert order matters: an attribution follows the posting line it hangs off, the profile
# follows the attribution it names, and a consumption follows the profile that owns it.
TABLE_KINDS = (
    ('transaction_revisions', 'transaction_revision', 'id'),
    ('document_line_identities', 'document_line_identity', 'id'),
    ('document_lines', 'document_line', 'id'),
    ('posting_batches', 'posting_batch', 'id'),
    ('posting_lines', 'posting_line', 'id'),
    ('posting_line_sources', 'posting_line_source', 'id'),
    ('customer_refund_profiles', 'customer_refund_profile', 'revision_id'),
    ('customer_refund_consumptions', 'customer_refund_consumption', 'id'),
)


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
    return effects.rows(s, c.customer_refund_profiles,
                        c.customer_refund_profiles.c.revision_id == revision['id'])[0]


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


def _funding(s, selector, currency):
    """A refund leaves a bank account. Nothing else is money you can hand somebody."""
    row = _account_row(s, selector, 'funding_account')
    if row['type'] != 'bank':
        raise _invalid('funding_account',
                       f'"{row["full_name"]}" is a {row["type"].replace("_", " ")} account; a customer '
                       'is refunded out of a bank account')
    if row['currency'] != currency:
        raise _invalid('funding_account', 'account must use the home currency')
    return _account_facts(row)


def _list_row(s, noun, table, selector, field, kind):
    row = list_service.resolve_selector(s.company, table, get_list_definition(noun), selector)
    if not row['active']:
        raise BookflowError('E_INACTIVE_REFERENCE',
                            details={'record_type': kind, 'record_id': row['id'], 'field': field})
    return row


def _method(s, selector):
    row = _list_row(s, 'payment-method', c.payment_methods, selector, 'method', 'payment method')
    return PaymentMethod(**_reference(row).model_dump(), kind=row['kind'])


def _check_number(inp, method):
    """A check number belongs to a check. Nothing else has one to write on."""
    if inp.check_number is None:
        return None
    number = inp.check_number.strip()
    if not number:
        raise _invalid('check_number', 'must not be blank')
    if method.kind != 'check':
        raise _invalid('check_number', f'"{method.label}" is not a check, so it carries no check number')
    return number


def _customer_facts(row):
    return Customer(**_reference(row).model_dump(),
                    company_name=row.get('company_name'), email=row.get('email'),
                    phone=row.get('phone'), account_number=row.get('account_number'))


# ---------------------------------------------------------------- what may be refunded


def _sources(s, inp, currency):
    """Resolve every named credit, check what it is still worth, and take exactly that much.

    The customer, receivable account and currency come from the credits rather than from the
    caller: a refund that named a different customer from the credit it spends would pay the
    wrong person, and `customer` is therefore a guard rather than a choice.
    """
    resolved, seen, key = [], set(), None
    for item in inp.sources:
        facts = credits.facts(s, item.credit_memo, write=True)
        header = facts['header']
        if header['id'] in seen:
            raise _invalid('sources', 'name each credit memo once')
        seen.add(header['id'])
        if header['status'] != 'posted':
            raise BookflowError('E_APPLICATION_INACTIVE', details={
                'credit_memo_id': header['id'], 'status': header['status'],
                'next': 'A voided credit memo is worth nothing and can refund nothing.'})
        if key is None:
            key = facts['key']
        elif (facts['key']['party_id'], facts['key']['ar_account_id'], facts['key']['currency']) != (
                key['party_id'], key['ar_account_id'], key['currency']):
            raise BookflowError('E_APPLICATION_INCOMPATIBLE', details={
                'credit_memo_id': header['id'],
                'next': 'One refund pays back one customer on one receivable account in one '
                        'currency; write a second refund for the other customer.'})
        available = facts['available']
        if item.amount is None:
            amount = available
        else:
            amount = parse_domestic_amount(item.amount, currency, 'sources.amount').minor_units
        if available <= 0 or amount > available:
            raise BookflowError('E_CREDIT_UNAVAILABLE', details={
                'credit_memo_id': header['id'], 'credit_memo_number': header['number'],
                'requested': Money(max(amount, 0), currency).to_dict(),
                'available': Money(available, currency).to_dict(),
                'next': 'This credit has already been applied or refunded; refund at most what '
                        'it is still worth.'})
        if amount <= 0:
            raise _invalid('sources.amount', 'refund a positive amount of each credit you name')
        resolved.append(dict(facts=facts, amount=amount))
    if inp.customer is not None:
        expected = resolve_party(s.company, 'customer', inp.customer)
        if expected['id'] != key['party_id']:
            raise _invalid('customer', 'these credits belong to a different customer')
    return resolved, key


def _draw(facts, wanted):
    """Take `wanted` from this credit's components in order; never more than one holds."""
    taken, remaining = [], dict(facts['remaining'])
    for component in facts['components']:
        if wanted <= 0:
            break
        share = min(wanted, remaining.get(component['id'], 0))
        if share <= 0:
            continue
        taken.append((component, share))
        wanted -= share
    if wanted > 0:  # pragma: no cover - the availability check runs first
        raise BookflowError('E_INTERNAL', message='Credit capacity is not attributable.')
    return taken


# ---------------------------------------------------------------- reads


def summary(header, revision, profile):
    currency = revision['currency']
    captured = CustomerRefundProfile.model_validate_json(profile['profile_snapshot'])
    return dict(header, date=revision['date'], customer_id=profile['party_id'],
                customer_name=captured.customer.label, ar_account_id=profile['ar_account_id'],
                funding_account_id=profile['funding_account_id'],
                payment_method_id=profile['payment_method_id'],
                payment_method_name=captured.payment_method.label,
                check_number=profile['check_number'], reference=profile['reference'],
                memo=revision['memo'], currency=currency,
                total_minor_units=revision['total_minor_units'],
                total=Money(revision['total_minor_units'], currency).to_dict())


def _consumption_output(s, rows, numbers):
    values = []
    for row in rows:
        currency = row['currency']
        values.append(CustomerRefundConsumptionOutput(
            **{key: row[key] for key in ('id', 'created_at', 'created_by', 'created_via', 'kind',
                                         'reverses_consumption_id', 'transaction_id', 'revision_id',
                                         'credit_source_key_id', 'credit_source_component_id',
                                         'amount_minor_units', 'currency', 'effective_date')},
            credit_memo_id=numbers[row['credit_source_key_id']][0],
            credit_memo_number=numbers[row['credit_source_key_id']][1],
            amount=Money(row['amount_minor_units'], currency).to_dict()))
    return values


def _credit_labels(s, rows):
    identifiers = {row['credit_source_key_id'] for row in rows}
    if not identifiers:
        return {}
    keys = effects.rows(s, c.credit_source_keys, c.credit_source_keys.c.id.in_(identifiers))
    headers = {row['id']: row for row in effects.rows(
        s, c.transactions, c.transactions.c.id.in_([key['transaction_id'] for key in keys]))}
    return {key['id']: (key['transaction_id'], headers[key['transaction_id']]['number']) for key in keys}


def revision_output(s, header, revision, profile, pending=None):
    pending = pending or {}
    envelopes = [line for line in pending.get('document_lines', []) if line['revision_id'] == revision['id']]
    if not envelopes:
        envelopes = effects.rows(s, c.document_lines, c.document_lines.c.revision_id == revision['id'],
                                 order=c.document_lines.c.position)
    currency = revision['currency']
    captured = CustomerRefundProfile.model_validate_json(profile['profile_snapshot'])
    saved_batches = effects.rows(s, c.posting_batches, c.posting_batches.c.revision_id == revision['id'],
                                 order=c.posting_batches.c.id)
    summaries = [journals.batch_output(s, batch) for batch in saved_batches]
    summaries += [journals.batch_output(s, batch, [line for line in pending['posting_lines']
                                                   if line['batch_id'] == batch['id']])
                  for batch in pending.get('posting_batches', []) if batch['revision_id'] == revision['id']]
    lines = [CustomerRefundLineOutput(
        **{key: envelope[key] for key in ('id', 'created_at', 'created_by', 'created_via',
                                          'transaction_id', 'revision_id', 'line_id', 'position',
                                          'kind', 'class_id', 'class_name', 'description')},
        customer_id=profile['party_id'], customer_name=captured.customer.label, currency=currency,
        amount=Money(revision['total_minor_units'], currency).to_dict(),
        amount_minor_units=revision['total_minor_units']) for envelope in envelopes]
    values = {k: v for k, v in revision.items() if not k.endswith('_snapshot')}
    values.update(total=Money(revision['total_minor_units'], currency).to_dict())
    return CustomerRefundRevisionOutput(
        **values, profile=json.loads(profile['profile_snapshot']),
        issuer_snapshot=json.loads(revision['issuer_snapshot']), lines=lines, batches=summaries)


def _output(s, header, revision, profile, pending=None, *, model=CustomerRefundOutput, **extra):
    rows = [row for row in (pending or {}).get('customer_refund_consumptions', [])]
    rows += effects.rows(s, c.customer_refund_consumptions,
                         c.customer_refund_consumptions.c.transaction_id == header['id'],
                         order=c.customer_refund_consumptions.c.id)
    labels = _credit_labels(s, rows)
    return model(**summary(header, revision, profile),
                 revision=revision_output(s, header, revision, profile, pending),
                 consumptions=_consumption_output(s, sorted(rows, key=lambda row: row['id']), labels),
                 **extra)


def show(s, inp):
    header = resolve(s, inp.refund)
    revision = journals.revision(s, header)
    return _output(s, header, revision, profile_row(s, revision))


def page(s, ctx, inp):
    from bookflow.company.query import continuation, page_state

    class Contract:
        cursor = inp.cursor
        query = None

        def model_dump(self, **kw):
            return inp.model_dump(**kw)

    state = page_state(s, 'customer-refund query', Contract(), ctx.on_behalf_of)
    t, r, p = c.transactions, c.transaction_revisions, c.customer_refund_profiles
    query = (sa.select(t.c.id).select_from(
        t.join(r, r.c.id == t.c.current_revision_id).join(p, p.c.revision_id == r.c.id))
        .where(t.c.type == DOCUMENT_TYPE))
    if inp.customer:
        query = query.where(p.c.party_id == resolve_party(s.company, 'customer', inp.customer)['id'])
    if inp.funding_account:
        query = query.where(p.c.funding_account_id == accounts.resolve_account(s.company, inp.funding_account)['id'])
    if inp.method:
        query = query.where(p.c.payment_method_id == list_service.resolve_selector(
            s.company, c.payment_methods, get_list_definition('payment-method'), inp.method)['id'])
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
        sa.select(c.customer_refund_profiles).where(
            c.customer_refund_profiles.c.revision_id.in_(revision_ids))).mappings()} if found else {}
    items = [CustomerRefundSummaryOutput(**summary(
        header, revisions[header['current_revision_id']],
        profiles[header['current_revision_id']])) for header in ordered]
    return CustomerRefundPageOutput(items=items, count=len(found), has_more=more,
                                    next_cursor=continuation(state, len(found), more),
                                    audit_watermark=state.sequence)


# ---------------------------------------------------------------- writing one refund


def _number(s, explicit):
    if explicit is not None:
        return effects.allocate(s, DOCUMENT_TYPE, explicit)[0], None
    saved = effects.rows(s, c.sequences, c.sequences.c.name == DOCUMENT_TYPE)
    next_number, prefix = (saved[0]['next_number'], saved[0]['prefix']) if saved else (1, '')
    occupied = set(s.company.conn.execute(sa.select(c.transactions.c.number).where(
        c.transactions.c.type == DOCUMENT_TYPE)).scalars())
    while True:
        if next_number >= INT64_MAX:
            raise BookflowError('E_VALUE_RANGE', details={'field': 'next_number'})
        candidate = f'{prefix}{next_number}'
        next_number += 1
        if candidate not in occupied:
            return candidate, dict(name=DOCUMENT_TYPE, next_number=next_number, prefix=prefix)


def prepare_post(s, ctx, inp):
    info = _info(s)
    currency = info['home_currency']
    sources, key = _sources(s, inp, currency)
    total = sum(row['amount'] for row in sources)
    if total <= 0 or total > INT64_MAX:
        raise BookflowError('E_VALUE_RANGE', details={'field': 'amount'})
    party_row = resolve_party(s.company, 'customer', key['party_id'])
    customer = _customer_facts(party_row)
    receivable = _account_facts(_account_row(s, key['ar_account_id'], 'ar_account'))
    funding = _funding(s, inp.funding_account, currency)
    method = _method(s, inp.method)
    check_number = _check_number(inp, method)
    klass = (_reference(_list_row(s, 'class', c.classes, inp.class_id, 'class_id', 'class'))
             if inp.class_id else None)
    journals.open_dates(s, [inp.date])
    for row in sources:
        if inp.date < row['facts']['revision']['date']:
            raise _invalid('date', 'a refund cannot be dated before the credit memo it pays out')
    number, sequence = _number(s, inp.number)
    at, event = clock.now_iso(), new_id()
    issuer = {key_: value for key_, value in info.items()
              if key_ in ('id', 'legal_name', 'home_currency')
              or key_.startswith(('address_', 'legal_address_'))}
    profile = CustomerRefundProfile(
        customer=customer, ar_account=receivable, funding_account=funding, payment_method=method,
        check_number=check_number, reference=inp.reference, amount_minor_units=total,
        currency=currency,
        sources=[RefundSource(credit_memo_id=row['facts']['header']['id'],
                              credit_memo_number=row['facts']['header']['number'],
                              credit_memo_date=row['facts']['revision']['date'],
                              credit_source_key_id=row['facts']['key']['id'],
                              amount_minor_units=row['amount'],
                              available_minor_units=row['facts']['available']) for row in sources],
        origins={'funding_account': Origin(kind='explicit'), 'method': Origin(kind='explicit'),
                 'amount': Origin(kind='explicit' if any(item.amount is not None for item in inp.sources)
                                  else 'default')})

    def created():
        return dict(id=new_id(), created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)

    header = dict(id=new_id(), **common(s.actor.id, ctx.interface.value, at), type=DOCUMENT_TYPE,
                  status='posted', voided_at=None, voided_by=None, void_reason=None,
                  void_posting_batch_id=None)
    pending = {table: [] for table, _, _ in TABLE_KINDS}
    revision = dict(**created(), transaction_id=header['id'], revision_number=1,
                    supersedes_revision_id=None, date=inp.date, number=number, name_type='customer',
                    name_id=customer.id, memo=inp.memo, total_minor_units=total, currency=currency,
                    issuer_snapshot=json_text(issuer), custom_fields_snapshot=json_text({}),
                    audit_event_id=event)
    header.update(number=number, current_revision_id=revision['id'])
    pending['transaction_revisions'].append(revision)
    identity = dict(**created(), transaction_id=header['id'])
    pending['document_line_identities'].append(identity)
    description = 'Refund of ' + ', '.join(row['facts']['header']['number'] for row in sources)
    envelope = dict(**created(), transaction_id=header['id'], revision_id=revision['id'],
                    line_id=identity['id'], position=1, kind='refund', account_id=None,
                    side=None, amount_minor_units=None, currency=currency, account_snapshot=None,
                    name_type='customer', name_id=customer.id, party_name=customer.label,
                    class_id=klass.id if klass else None, class_name=klass.label if klass else None,
                    description=description, **dict.fromkeys(journals.FACTS))
    pending['document_lines'].append(envelope)
    batch = dict(**created(), transaction_id=header['id'], revision_id=revision['id'],
                 kind='original', effective_date=inp.date, reverses_batch_id=None,
                 replaces_batch_id=None, audit_event_id=event)
    pending['posting_batches'].append(batch)

    def leg(account, amount, debit, line_no):
        value = dict(**created(), transaction_id=header['id'], batch_id=batch['id'], line_no=line_no,
                     account_id=account.id, account_snapshot=json_text(account.model_dump()),
                     currency=currency, name_type='customer', name_id=customer.id,
                     party_name=party_row['full_name'],
                     class_id=klass.id if klass else None, class_name=klass.label if klass else None,
                     description=inp.memo or description,
                     debit_minor_units=amount if debit else 0,
                     credit_minor_units=0 if debit else amount,
                     reversed_line_id=None, **dict.fromkeys(journals.FACTS))
        pending['posting_lines'].append(value)
        return value

    def attribute(posting, units):
        source = dict(**created(), transaction_id=header['id'], posting_line_id=posting['id'],
                      revision_id=revision['id'], document_line_id=envelope['id'],
                      amount_minor_units=units, currency=currency,
                      reversed_source_id=None, tax_component_id=None)
        pending['posting_line_sources'].append(source)
        return source

    # Dr the receivable, Cr the bank. No income leg and no tax leg: the credit memo already
    # reversed the sale, and doing it again here would take the revenue down twice.
    ar_source = attribute(leg(receivable, total, True, 1), total)
    attribute(leg(funding, total, False, 2), total)
    pending['customer_refund_profiles'].append(dict(
        transaction_id=header['id'], revision_id=revision['id'], created_at=at,
        created_by=s.actor.id, created_via=ctx.interface.value, audit_event_id=event,
        type=DOCUMENT_TYPE, party_id=customer.id, ar_account_id=receivable.id,
        funding_account_id=funding.id, payment_method_id=method.id, check_number=check_number,
        reference=inp.reference, amount_minor_units=total, currency=currency,
        ar_posting_source_id=ar_source['id'], profile_snapshot=json_text(profile.model_dump())))
    for row in sources:
        for component, share in _draw(row['facts'], row['amount']):
            pending['customer_refund_consumptions'].append(dict(
                **created(), audit_event_id=event, kind='consume', reverses_consumption_id=None,
                transaction_id=header['id'], revision_id=revision['id'],
                credit_source_key_id=row['facts']['key']['id'],
                credit_source_component_id=component['id'], amount_minor_units=share,
                currency=currency, effective_date=inp.date))
    preview = _output(s, header, revision, pending['customer_refund_profiles'][0], pending,
                      model=CustomerRefundWriteOutput, changed_fields=[])
    return Plan(preview, dict(input=inp, operation='post', header=header, before=None,
                              revision=revision, pending=pending, event=event, at=at,
                              sequence=sequence, currency=currency, changed=True, sources=sources))


def prepare_void(s, ctx, inp):
    header = resolve(s, inp.refund)
    if inp.expected_version is not None:
        journals.version_meta(s, header, inp.expected_version)
    revision = journals.revision(s, header)
    if header['status'] != 'posted':
        raise BookflowError('E_APPLICATION_INACTIVE', details={
            'refund_id': header['id'], 'status': header['status'],
            'next': 'A voided refund paid nothing and cannot be voided again.'})
    if not ctx.reason or not ctx.reason.strip():
        raise BookflowError('E_REASON_REQUIRED')
    if len(ctx.reason.strip()) > 140:
        raise _invalid('reason', 'must be at most 140 characters')
    journals.open_dates(s, [revision['date']])
    at, event = clock.now_iso(), new_id()

    def created():
        return dict(id=new_id(), created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)

    pending = {table: [] for table, _, _ in TABLE_KINDS}
    current_batch = effects.rows(s, c.posting_batches,
                                 c.posting_batches.c.revision_id == revision['id'],
                                 c.posting_batches.c.kind != 'reversal')[0]
    inverse = effects.reverse(s, header, revision, current_batch, event, created, pending)
    # Every consumption this refund made is released, exactly, so the credits it paid out are
    # worth again what they were worth before it was written.
    for row in credits.active_consumptions(s, refund_id=header['id']):
        pending['customer_refund_consumptions'].append(dict(
            row, **created(), audit_event_id=event, kind='release', reverses_consumption_id=row['id']))
    changed = dict(header, version=header['version'] + 1, updated_at=at, updated_by=s.actor.id,
                   updated_via=ctx.interface.value, status='voided', voided_at=at,
                   voided_by=s.actor.id, void_reason=ctx.reason.strip(),
                   void_posting_batch_id=inverse['id'])
    preview = _output(s, changed, revision, profile_row(s, revision), pending,
                      model=CustomerRefundWriteOutput, changed_fields=['status'])
    return Plan(preview, dict(input=inp, operation='void', header=changed, before=header,
                              revision=revision, pending=pending, event=event, at=at,
                              sequence=None, currency=revision['currency'], changed=True, sources=[]))


# ---------------------------------------------------------------- persistence


def prepare(s, ctx, inp, operation):
    return prepare_post(s, ctx, inp) if operation == 'post' else prepare_void(s, ctx, inp)


def apply(plan, ctx, s):
    # Rebuilt inside the writer transaction: what each credit is still worth, the numbering
    # and the open period are only decisive here, and the preview read them before anyone
    # held the lock.
    operation = plan.data['operation']
    fresh = prepare(s, ctx, plan.data['input'], operation)
    from bookflow.company.refund_validation import validate
    validate(fresh, s, ctx)
    data = fresh.data
    header, before, pending = data['header'], data['before'], data['pending']
    touched = [Touched('transaction', header['id'], 'update' if before else 'create',
                       before['version'] if before else None, header['version'],
                       header, before, db='company')]
    for table, kind, identity in TABLE_KINDS:
        touched.extend(Touched(kind, row[identity], 'create', None, 1, effects.decoded(row), db='company')
                       for row in pending[table])
    command_name = 'customer-refund ' + operation
    summary_text = f"{operation} customer refund {header['number']}"
    audit.write_event_to(s.company, ctx, command_name, summary_text, touched,
                         actor_id=s.actor.id, actor_kind=s.actor.kind,
                         directive_code=getattr(s, 'directive_code', None), event_id=data['event'])
    if before:
        s.company.conn.execute(c.transactions.update().where(
            c.transactions.c.id == header['id']).values(**header))
    else:
        s.company.conn.execute(c.transactions.insert().values(**header))
    for table, _, _ in TABLE_KINDS:
        if pending[table]:
            s.company.conn.execute(getattr(c, table).insert(), pending[table])
    if data['sequence']:
        from sqlalchemy.dialects.sqlite import insert
        statement = insert(c.sequences).values(**data['sequence'])
        s.company.conn.execute(statement.on_conflict_do_update(index_elements=['name'], set_=data['sequence']))
    return Applied(fresh.preview, touched, summary_text, audited=True)
