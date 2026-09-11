"""Remitting sales tax: money out of an account, and the agency whose liability fell.

**The money.** One debit to the sales tax liability account and one credit to the account the
money came from -- a bank, which is debit-normal and so falls, or a credit card, which is
credit-normal and so rises. That is the whole ledger effect, and it is posted once, at the
remittance's date.

**No settlement.** Nothing on the other side of a remittance is a document. Sales tax is a
balance, derived from the tax components posted sales recorded, so a remittance does not
answer invoices and there is no application edge to make, take back or re-point. A partial
remittance leaves the remainder owed because the balance is the arithmetic of the postings --
which is also why the remainder is right whatever happens afterwards: a later invoice raises
it, a credit memo lowers it, and this document never has to be revisited.

**One agency per document.** The liability read attributes every effect on a sales-tax-payable
account to an agency, and this document's profile is what attributes its own. So a remittance
names one agency and carries one amount, exactly as the anchor's Pay Sales Tax window writes
one payment per agency.

**Immutable, void only.** There is no ``update``. A remittance has three facts -- the agency,
the amount and the date -- and changing any of them makes it a different remittance, so there
is nothing an edit could correct that voiding and writing again does not correct more
honestly. That also keeps the profile a true one-to-one header: one revision for the life of
the document, which is what lets ``liability_posting_source_id`` name one posting attribution
for ever rather than one per revision.
"""
from __future__ import annotations

import json

import sqlalchemy as sa

from bookflow.company import accounts, journals, list_service, sales_tax_reports, schema as c
from bookflow.company import document_effects as effects
from bookflow.company.journal_models import parse_domestic_amount
from bookflow.company.lists import get_list_definition
from bookflow.company.parties import resolve_party
from bookflow.company.sales_tax_payment_facts import (
    Account, Agency, Origin, PaymentMethod, Reference, SalesTaxPaymentProfile,
)
from bookflow.company.sales_tax_payment_models import (
    SalesTaxPaymentLineOutput, SalesTaxPaymentOutput, SalesTaxPaymentPageOutput,
    SalesTaxPaymentRevisionOutput, SalesTaxPaymentSummaryOutput, SalesTaxPaymentWriteOutput,
)
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError
from bookflow.core.exact import INT64_MAX
from bookflow.core.ids import is_ulid, new_id
from bookflow.core.money import Money
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.hub.users import common

DOCUMENT_TYPE = 'sales_tax_payment'

# Insert order matters: an attribution follows the posting line it hangs off, and the profile
# follows the attribution it names.
TABLE_KINDS = (
    ('transaction_revisions', 'transaction_revision', 'id'),
    ('document_line_identities', 'document_line_identity', 'id'),
    ('document_lines', 'document_line', 'id'),
    ('posting_batches', 'posting_batch', 'id'),
    ('posting_lines', 'posting_line', 'id'),
    ('posting_line_sources', 'posting_line_source', 'id'),
    ('sales_tax_payment_profiles', 'sales_tax_payment_profile', 'revision_id'),
)

# What a remittance may be drawn on, and what crediting it means. A debit card or an EFT is
# money out of the bank, not credit-card debt, so the account type decides this and not the
# method: the method is what the agency was handed, the account is where the money is.
FUNDING_KIND = {'bank': 'bank_cash', 'credit_card': 'card_liability'}

LIABILITY_ROLE = 'sales_tax_payable'


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
    return effects.rows(s, c.sales_tax_payment_profiles,
                        c.sales_tax_payment_profiles.c.revision_id == revision['id'])[0]


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


# ---------------------------------------------------------------- what it is drawn on and paid to


def _liability_account(s):
    """The chart's own sales tax payable account: a system role, so exactly one is legal.

    Nothing asks the caller for this. A system role is unique within a chart and every sales
    tax item's liability account already has to carry it, so naming it would be a field with
    one admissible answer.
    """
    row = s.company.conn.execute(sa.select(c.accounts).where(
        c.accounts.c.system_role == LIABILITY_ROLE)).mappings().first()
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={
            'record_type': 'account', 'system_role': LIABILITY_ROLE,
            'next': 'This company has no sales tax payable account; apply a chart that has one.'})
    if not row['active']:
        raise BookflowError('E_INACTIVE_REFERENCE', details={
            'record_type': 'account', 'record_id': row['id'], 'system_role': LIABILITY_ROLE})
    return dict(row)


def _funding(s, selector, currency):
    row = _account_row(s, selector, 'funding_account')
    if row['type'] not in FUNDING_KIND:
        raise _invalid('funding_account',
                       f'"{row["full_name"]}" is a {row["type"].replace("_", " ")} account; sales tax is '
                       'remitted from a bank account or charged to a credit card account')
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


def _agency(s, selector):
    row = resolve_party(s.company, 'vendor', selector)
    if not row['active']:
        raise BookflowError('E_INACTIVE_REFERENCE',
                            details={'record_type': 'vendor', 'record_id': row['id'], 'field': 'agency'})
    if not row['is_tax_agency']:
        raise _invalid('agency', f'"{row["name"]}" is not flagged as a tax agency; sales tax is '
                                 'remitted to the agency its tax items name')
    return Agency(**_reference(row).model_dump(), is_tax_agency=True,
                  account_number=row.get('account_number')), row


# ---------------------------------------------------------------- what is owed, and what may be paid


def _owed(s, agency_id, through_date):
    """What this agency is owed through ``through_date``, from the liability derivation itself.

    The write and the read cannot disagree about what is owed, because there is only one
    implementation of it. This is read again inside the writer transaction, so two remittances
    racing for the same balance cannot both succeed.
    """
    sales_tax_reports.require_accrual_basis(s.company)
    return sales_tax_reports.agency_balances(s.company, through_date, agency_id=agency_id).get(agency_id, 0)


def _amount(inp, currency, owed, agency):
    if inp.amount is None:
        amount = owed
    else:
        amount = parse_domestic_amount(inp.amount, currency, 'amount').minor_units
    if owed <= 0:
        raise BookflowError('E_APPLICATION_CAPACITY', details={
            'field': 'amount', 'agency_id': agency.id, 'agency_name': agency.label,
            'requested_minor_units': amount, 'available_minor_units': owed,
            'requested': Money(amount, currency).to_dict(), 'available': Money(owed, currency).to_dict(),
            'next': 'This agency is owed nothing through that date; check through_date, or nothing '
                    'is due.'})
    if amount <= 0:
        raise _invalid('amount', f'must be more than zero; {agency.label} is owed '
                                 f'{Money(owed, currency).to_dict()["amount"]} {currency}')
    if amount > owed:
        raise BookflowError('E_APPLICATION_CAPACITY', details={
            'field': 'amount', 'agency_id': agency.id, 'agency_name': agency.label,
            'requested_minor_units': amount, 'available_minor_units': owed,
            'requested': Money(amount, currency).to_dict(), 'available': Money(owed, currency).to_dict(),
            'next': 'Remit at most what this agency is owed through that date; paying an agency '
                    'more than the books owe it is a sales tax adjustment, which is a separate '
                    'document.'})
    return amount


# ---------------------------------------------------------------- reads


def summary(header, revision, profile):
    currency = revision['currency']
    captured = SalesTaxPaymentProfile.model_validate_json(profile['profile_snapshot'])
    posted = header['status'] == 'posted'
    remitted = revision['total_minor_units'] if posted else 0
    return dict(header, date=revision['date'], through_date=profile['through_date'],
                agency_id=profile['agency_id'], agency_name=captured.agency.label,
                liability_account_id=profile['liability_account_id'],
                funding_account_id=profile['funding_account_id'],
                funding_kind=profile['funding_kind'],
                payment_method_id=profile['payment_method_id'],
                payment_method_name=captured.payment_method.label,
                check_number=profile['check_number'], reference=profile['reference'],
                memo=revision['memo'], currency=currency,
                total_minor_units=revision['total_minor_units'],
                total=Money(revision['total_minor_units'], currency).to_dict(),
                liability_at_posting=Money(captured.liability_minor_units, currency).to_dict(),
                remainder_at_posting=Money(captured.liability_minor_units - remitted, currency).to_dict())


def revision_output(s, header, revision, profile, pending=None):
    pending = pending or {}
    envelopes = [line for line in pending.get('document_lines', []) if line['revision_id'] == revision['id']]
    if not envelopes:
        envelopes = effects.rows(s, c.document_lines, c.document_lines.c.revision_id == revision['id'],
                                 order=c.document_lines.c.position)
    currency = revision['currency']
    captured = SalesTaxPaymentProfile.model_validate_json(profile['profile_snapshot'])
    saved_batches = effects.rows(s, c.posting_batches, c.posting_batches.c.revision_id == revision['id'],
                                 order=c.posting_batches.c.id)
    summaries = [journals.batch_output(s, batch) for batch in saved_batches]
    summaries += [journals.batch_output(s, batch, [line for line in pending['posting_lines']
                                                   if line['batch_id'] == batch['id']])
                  for batch in pending.get('posting_batches', []) if batch['revision_id'] == revision['id']]
    lines = [SalesTaxPaymentLineOutput(
        **{key: envelope[key] for key in ('id', 'created_at', 'created_by', 'created_via',
                                          'transaction_id', 'revision_id', 'line_id', 'position',
                                          'kind', 'class_id', 'class_name', 'description')},
        agency_id=profile['agency_id'], agency_name=captured.agency.label, currency=currency,
        amount=Money(revision['total_minor_units'], currency).to_dict(),
        amount_minor_units=revision['total_minor_units']) for envelope in envelopes]
    values = {k: v for k, v in revision.items() if not k.endswith('_snapshot')}
    values.update(total=Money(revision['total_minor_units'], currency).to_dict())
    return SalesTaxPaymentRevisionOutput(
        **values, profile=json.loads(profile['profile_snapshot']),
        issuer_snapshot=json.loads(revision['issuer_snapshot']), lines=lines, batches=summaries)


def _output(s, header, revision, profile, pending=None, *, model=SalesTaxPaymentOutput, **extra):
    return model(**summary(header, revision, profile),
                 revision=revision_output(s, header, revision, profile, pending), **extra)


def show(s, inp):
    header = resolve(s, inp.payment)
    revision = journals.revision(s, header)
    return _output(s, header, revision, profile_row(s, revision))


def page(s, ctx, inp):
    from bookflow.company.query import continuation, page_state

    class Contract:
        cursor = inp.cursor
        query = None

        def model_dump(self, **kw):
            return inp.model_dump(**kw)

    state = page_state(s, 'sales-tax payment query', Contract(), ctx.on_behalf_of)
    t, r, p = c.transactions, c.transaction_revisions, c.sales_tax_payment_profiles
    query = (sa.select(t.c.id).select_from(
        t.join(r, r.c.id == t.c.current_revision_id).join(p, p.c.revision_id == r.c.id))
        .where(t.c.type == DOCUMENT_TYPE))
    if inp.agency:
        query = query.where(p.c.agency_id == resolve_party(s.company, 'vendor', inp.agency)['id'])
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
        sa.select(c.sales_tax_payment_profiles).where(
            c.sales_tax_payment_profiles.c.revision_id.in_(revision_ids))).mappings()} if found else {}
    items = [SalesTaxPaymentSummaryOutput(**summary(
        header, revisions[header['current_revision_id']],
        profiles[header['current_revision_id']])) for header in ordered]
    return SalesTaxPaymentPageOutput(items=items, count=len(found), has_more=more,
                                     next_cursor=continuation(state, len(found), more),
                                     audit_watermark=state.sequence)


# ---------------------------------------------------------------- writing one remittance


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


def prepare_pay(s, ctx, inp):
    info = _info(s)
    currency = info['home_currency']
    agency, agency_row = _agency(s, inp.agency)
    funding, funding_kind = _funding(s, inp.funding_account, currency)
    method = _method(s, inp.method)
    check_number = _check_number(inp, method, funding_kind)
    klass = (_reference(_list_row(s, 'class', c.classes, inp.class_id, 'class_id', 'class'))
             if inp.class_id else None)
    liability = _account_facts(_liability_account(s))
    through_date = inp.through_date or inp.date
    owed = _owed(s, agency.id, through_date)
    total = _amount(inp, currency, owed, agency)
    journals.open_dates(s, [inp.date])
    number, sequence = _number(s, inp.number)
    at, event = clock.now_iso(), new_id()
    issuer = {key: value for key, value in info.items()
              if key in ('id', 'legal_name', 'home_currency')
              or key.startswith(('address_', 'legal_address_'))}
    profile = SalesTaxPaymentProfile(
        agency=agency, liability_account=liability, funding_account=funding,
        funding_kind=funding_kind, payment_method=method, check_number=check_number,
        reference=inp.reference, through_date=through_date, liability_minor_units=owed,
        amount_minor_units=total, currency=currency,
        origins={'funding_account': Origin(kind='explicit'), 'method': Origin(kind='explicit'),
                 'through_date': Origin(kind='explicit' if inp.through_date else 'default'),
                 'amount': Origin(kind='explicit' if inp.amount is not None else 'default')})

    def created():
        return dict(id=new_id(), created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)

    header = dict(id=new_id(), **common(s.actor.id, ctx.interface.value, at), type=DOCUMENT_TYPE,
                  status='posted', voided_at=None, voided_by=None, void_reason=None,
                  void_posting_batch_id=None)
    pending = {table: [] for table, _, _ in TABLE_KINDS}
    revision = dict(**created(), transaction_id=header['id'], revision_number=1,
                    supersedes_revision_id=None, date=inp.date, number=number, name_type='vendor',
                    name_id=agency.id, memo=inp.memo, total_minor_units=total, currency=currency,
                    issuer_snapshot=json_text(issuer), custom_fields_snapshot=json_text({}),
                    audit_event_id=event)
    header.update(number=number, current_revision_id=revision['id'])
    pending['transaction_revisions'].append(revision)
    identity = dict(**created(), transaction_id=header['id'])
    pending['document_line_identities'].append(identity)
    description = f'Sales tax through {through_date}'
    envelope = dict(**created(), transaction_id=header['id'], revision_id=revision['id'],
                    line_id=identity['id'], position=1, kind=DOCUMENT_TYPE, account_id=None,
                    side=None, amount_minor_units=None, currency=currency, account_snapshot=None,
                    name_type='vendor', name_id=agency.id, party_name=agency.label,
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
                     currency=currency, name_type='vendor', name_id=agency.id, party_name=agency.label,
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

    liability_source = attribute(leg(liability, total, True, 1), total)
    attribute(leg(funding, total, False, 2), total)
    pending['sales_tax_payment_profiles'].append(dict(
        transaction_id=header['id'], revision_id=revision['id'], created_at=at,
        created_by=s.actor.id, created_via=ctx.interface.value, audit_event_id=event,
        type=DOCUMENT_TYPE, agency_id=agency.id, liability_account_id=liability.id,
        funding_account_id=funding.id, funding_kind=funding_kind, payment_method_id=method.id,
        check_number=check_number, reference=inp.reference, through_date=through_date,
        amount_minor_units=total, currency=currency,
        liability_posting_source_id=liability_source['id'],
        profile_snapshot=json_text(profile.model_dump())))
    preview = _output(s, header, revision, pending['sales_tax_payment_profiles'][0], pending,
                      model=SalesTaxPaymentWriteOutput, changed_fields=[])
    return Plan(preview, dict(input=inp, operation='pay', header=header, before=None,
                              revision=revision, pending=pending, event=event, at=at,
                              sequence=sequence, currency=currency, changed=True))


def prepare_void(s, ctx, inp):
    header = resolve(s, inp.payment)
    if inp.expected_version is not None:
        journals.version_meta(s, header, inp.expected_version)
    revision = journals.revision(s, header)
    if header['status'] != 'posted':
        raise BookflowError('E_APPLICATION_INACTIVE', details={
            'payment_id': header['id'], 'status': header['status'],
            'next': 'A voided remittance moved nothing and cannot be voided again.'})
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
    changed = dict(header, version=header['version'] + 1, updated_at=at, updated_by=s.actor.id,
                   updated_via=ctx.interface.value, status='voided', voided_at=at,
                   voided_by=s.actor.id, void_reason=ctx.reason.strip(),
                   void_posting_batch_id=inverse['id'])
    preview = _output(s, changed, revision, profile_row(s, revision), pending,
                      model=SalesTaxPaymentWriteOutput, changed_fields=['status'])
    return Plan(preview, dict(input=inp, operation='void', header=changed, before=header,
                              revision=revision, pending=pending, event=event, at=at,
                              sequence=None, currency=revision['currency'], changed=True))


# ---------------------------------------------------------------- persistence


def prepare(s, ctx, inp, operation):
    return prepare_pay(s, ctx, inp) if operation == 'pay' else prepare_void(s, ctx, inp)


def apply(plan, ctx, s):
    # Rebuilt inside the writer transaction, the way every document here does it: the balance
    # owed, the numbering and the dates are only decisive at the moment of the write.
    operation = plan.data['operation']
    fresh = prepare(s, ctx, plan.data['input'], operation)
    from bookflow.company.sales_tax_payment_validation import validate
    validate(fresh, s, ctx)
    data = fresh.data
    header, before, pending = data['header'], data['before'], data['pending']
    touched = [Touched('transaction', header['id'], 'update' if before else 'create',
                       before['version'] if before else None, header['version'],
                       header, before, db='company')]
    for table, kind, key in TABLE_KINDS:
        touched.extend(Touched(kind, row[key], 'create', None, 1, effects.decoded(row), db='company')
                       for row in pending[table])
    command_name = 'sales-tax ' + ('pay' if operation == 'pay' else 'payment void')
    summary_text = f"{operation} sales tax payment {header['number']}"
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
