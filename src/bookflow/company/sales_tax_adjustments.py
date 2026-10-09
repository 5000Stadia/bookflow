"""Adjusting sales tax due: one agency's balance moved against an adjustment account.

**The money.** Two legs, posted once at the adjustment's date. An increase credits the sales
tax payable account -- the agency is owed more -- and debits the adjustment account; a
reduction debits the liability and credits the adjustment account. The liability leg names the
agency in its own ``sales_tax_adjustment_profiles`` row, so ``sales-tax liability`` reports it
under that agency and ``sales-tax pay`` counts it in what the agency may be paid.

**What the other side may be.** Anything that is not itself a ledger something else owns: not
the sales tax payable account (an adjustment against itself moves nothing), not a bank or a
credit card (money leaving for the agency is a remittance, ``sales-tax pay``), not accounts
receivable or payable (their balances are the open documents behind them), not inventory (its
balance is what items hold), and not a non-posting account.

**Immutable, void only.** A void is an exact reversal at the document's own date, which puts the
agency back where it was; its header row is never rewritten.

**Basis.** Like ``sales-tax pay``, this refuses a company whose sales tax liability basis is
``payment_receipt``: on that basis nothing records a liability per agency to adjust, and the
liability read that would show this is refused too.
"""
from __future__ import annotations

import json

import sqlalchemy as sa

from bookflow.company import accounts, journals, list_service, sales_tax_reports, schema as c
from bookflow.company import document_effects as effects
from bookflow.company.journal_models import parse_domestic_amount
from bookflow.company.lists import get_list_definition
from bookflow.company.parties import resolve_party
from bookflow.company.bill_facts import Account, Reference
from bookflow.company.sales_tax_adjustment_models import (
    Agency, SalesTaxAdjustmentLineOutput, SalesTaxAdjustmentOutput, SalesTaxAdjustmentPageOutput,
    SalesTaxAdjustmentProfile, SalesTaxAdjustmentRevisionOutput, SalesTaxAdjustmentSummaryOutput,
    SalesTaxAdjustmentWriteOutput,
)
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError
from bookflow.core.exact import INT64_MAX
from bookflow.core.ids import is_ulid, new_id
from bookflow.core.money import Money
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.hub.users import common

DOCUMENT_TYPE = 'sales_tax_adjustment'
LINE_KIND = 'tax_adjustment'
LIABILITY_ROLE = 'sales_tax_payable'

# Insert order matters: an attribution follows the posting line it hangs off, and the profile
# follows the attribution it names.
TABLE_KINDS = (
    ('transaction_revisions', 'transaction_revision', 'id'),
    ('document_line_identities', 'document_line_identity', 'id'),
    ('document_lines', 'document_line', 'id'),
    ('posting_batches', 'posting_batch', 'id'),
    ('posting_lines', 'posting_line', 'id'),
    ('posting_line_sources', 'posting_line_source', 'id'),
    ('sales_tax_adjustment_profiles', 'sales_tax_adjustment_profile', 'revision_id'),
)

# Account types an adjustment may not post against, and why, in the words the refusal uses.
REFUSED_TYPES = {
    'bank': 'money paid to the agency is a remittance; use sales-tax pay',
    'credit_card': 'money paid to the agency is a remittance; use sales-tax pay',
    'accounts_receivable': 'its balance is the open customer documents behind it',
    'accounts_payable': 'its balance is the open vendor documents behind it',
    'non_posting': 'it is not a posting account',
}


def json_text(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _invalid(field, problem):
    return BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': problem}]})


def signed(direction, amount):
    """The adjustment in the liability's own sense: positive when the agency is owed more."""
    return amount if direction == 'increase' else -amount


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
    return effects.rows(s, c.sales_tax_adjustment_profiles,
                        c.sales_tax_adjustment_profiles.c.revision_id == revision['id'])[0]


def _info(s):
    return dict(s.company.conn.execute(sa.select(c.company_info)).mappings().one())


def _reference(row):
    return Reference(id=row['id'], label=row.get('full_name') or row.get('name') or row.get('code'),
                     version=row['version'])


def _account_facts(row):
    return Account(**{k: row[k] for k in ('id', 'name', 'full_name', 'number', 'type')},
                   normal_balance=accounts.NORMAL_BALANCE[row['type']])


# ---------------------------------------------------------------- who and what it is against


def liability_account(s):
    """The chart's own sales tax payable account: a system role, so exactly one is legal."""
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


def _adjustment_account(s, selector, currency):
    try:
        row = accounts.resolve_account(s.company, selector)
    except BookflowError as exc:
        if exc.code in ('E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE'):
            exc.details.setdefault('record_type', 'account')
            exc.details['field'] = 'adjustment_account'
        raise
    if not row['active']:
        raise BookflowError('E_INACTIVE_REFERENCE', details={
            'record_type': 'account', 'record_id': row['id'], 'field': 'adjustment_account'})
    name = row['full_name']
    if row.get('system_role') == LIABILITY_ROLE:
        raise _invalid('adjustment_account', f'"{name}" is the sales tax payable account the adjustment '
                                             'already posts to; choose the account on the other side, '
                                             'such as an income or expense account')
    if row.get('system_role') in journals.OWNED_SYSTEM_ROLES:
        raise _invalid('adjustment_account', f'"{name}" is written only by the '
                                             f'{journals.OWNED_SYSTEM_ROLES[row["system_role"]]} ledger that owns it')
    if row['type'] in REFUSED_TYPES:
        raise _invalid('adjustment_account', f'"{name}" is a {row["type"].replace("_", " ")} account; '
                                             + REFUSED_TYPES[row['type']])
    if row['currency'] not in (None, currency):
        raise _invalid('adjustment_account', 'account must use the home currency')
    return row


def _agency(s, selector):
    row = resolve_party(s.company, 'vendor', selector)
    if not row['active']:
        raise BookflowError('E_INACTIVE_REFERENCE',
                            details={'record_type': 'vendor', 'record_id': row['id'], 'field': 'agency'})
    if not row['is_tax_agency']:
        raise _invalid('agency', f'"{row["name"]}" is not flagged as a tax agency; sales tax due is '
                                 'adjusted for the agency its tax items name')
    return Agency(**_reference(row).model_dump(), is_tax_agency=True)


def _class(s, selector):
    row = list_service.resolve_selector(s.company, c.classes, get_list_definition('class'), selector)
    if not row['active']:
        raise BookflowError('E_INACTIVE_REFERENCE',
                            details={'record_type': 'class', 'record_id': row['id'], 'field': 'class_id'})
    return _reference(row)


def owed(s, agency_id, date):
    """What this agency is owed on ``date``, from the liability derivation itself."""
    sales_tax_reports.require_accrual_basis(s.company)
    return sales_tax_reports.agency_balances(s.company, date, agency_id=agency_id).get(agency_id, 0)


# ---------------------------------------------------------------- reads


def summary(header, revision, profile):
    currency = revision['currency']
    captured = SalesTaxAdjustmentProfile.model_validate_json(profile['profile_snapshot'])
    total = revision['total_minor_units']
    return dict(header, date=revision['date'], agency_id=profile['agency_id'],
                agency_name=captured.agency.label,
                liability_account_id=profile['liability_account_id'],
                adjustment_account_id=profile['offset_account_id'],
                adjustment_account_name=captured.adjustment_account.full_name,
                direction=profile['direction'], memo=revision['memo'], currency=currency,
                total_minor_units=total, total=Money(total, currency).to_dict(),
                signed_amount=Money(signed(profile['direction'], total), currency).to_dict(),
                owed_before=Money(captured.owed_before_minor_units, currency).to_dict(),
                owed_after=Money(captured.owed_after_minor_units, currency).to_dict())


def revision_output(s, header, revision, profile, pending=None):
    pending = pending or {}
    envelopes = [line for line in pending.get('document_lines', []) if line['revision_id'] == revision['id']]
    if not envelopes:
        envelopes = effects.rows(s, c.document_lines, c.document_lines.c.revision_id == revision['id'],
                                 order=c.document_lines.c.position)
    currency = revision['currency']
    captured = SalesTaxAdjustmentProfile.model_validate_json(profile['profile_snapshot'])
    saved = effects.rows(s, c.posting_batches, c.posting_batches.c.revision_id == revision['id'],
                         order=c.posting_batches.c.id)
    batches = [journals.batch_output(s, batch) for batch in saved]
    batches += [journals.batch_output(s, batch, [line for line in pending['posting_lines']
                                                 if line['batch_id'] == batch['id']])
                for batch in pending.get('posting_batches', []) if batch['revision_id'] == revision['id']]
    lines = [SalesTaxAdjustmentLineOutput(
        **{key: envelope[key] for key in ('id', 'created_at', 'created_by', 'created_via',
                                          'transaction_id', 'revision_id', 'line_id', 'position',
                                          'kind', 'class_id', 'class_name', 'description')},
        agency_id=profile['agency_id'], agency_name=captured.agency.label,
        direction=profile['direction'], currency=currency,
        amount=Money(revision['total_minor_units'], currency).to_dict(),
        amount_minor_units=revision['total_minor_units']) for envelope in envelopes]
    values = {k: v for k, v in revision.items() if not k.endswith('_snapshot')}
    values.update(total=Money(revision['total_minor_units'], currency).to_dict())
    return SalesTaxAdjustmentRevisionOutput(**values, profile=json.loads(profile['profile_snapshot']),
                                            lines=lines, batches=batches)


def _output(s, header, revision, profile, pending=None, *, model=SalesTaxAdjustmentOutput, **extra):
    return model(**summary(header, revision, profile),
                 revision=revision_output(s, header, revision, profile, pending), **extra)


def show(s, inp):
    header = resolve(s, inp.adjustment)
    revision = journals.revision(s, header)
    return _output(s, header, revision, profile_row(s, revision))


def page(s, ctx, inp):
    from bookflow.company.query import continuation, page_state

    class Contract:
        cursor = inp.cursor
        query = None

        def model_dump(self, **kw):
            return inp.model_dump(**kw)

    state = page_state(s, 'sales-tax adjustment query', Contract(), ctx.on_behalf_of)
    t, r, p = c.transactions, c.transaction_revisions, c.sales_tax_adjustment_profiles
    query = (sa.select(t.c.id).select_from(
        t.join(r, r.c.id == t.c.current_revision_id).join(p, p.c.revision_id == r.c.id))
        .where(t.c.type == DOCUMENT_TYPE))
    if inp.agency:
        query = query.where(p.c.agency_id == resolve_party(s.company, 'vendor', inp.agency)['id'])
    if inp.adjustment_account:
        query = query.where(p.c.offset_account_id == accounts.resolve_account(
            s.company, inp.adjustment_account)['id'])
    if inp.date_from:
        query = query.where(r.c.date >= inp.date_from)
    if inp.date_to:
        query = query.where(r.c.date <= inp.date_to)
    if inp.status:
        query = query.where(t.c.status == inp.status)
    if inp.number:
        query = query.where(t.c.number.contains(inp.number, autoescape=True))
    order = (r.c.date, t.c.id)
    query = query.order_by(*([column.desc() for column in order] if inp.direction == 'desc' else order))
    found = [row['id'] for row in s.company.conn.execute(
        query.offset(state.offset).limit(inp.limit + 1)).mappings()]
    more, found = len(found) > inp.limit, found[:inp.limit]
    items = []
    if found:
        headers = {row['id']: dict(row) for row in s.company.conn.execute(
            sa.select(t).where(t.c.id.in_(found))).mappings()}
        ordered = [headers[identifier] for identifier in found]
        revision_ids = [header['current_revision_id'] for header in ordered]
        revisions = {row['id']: dict(row) for row in s.company.conn.execute(
            sa.select(r).where(r.c.id.in_(revision_ids))).mappings()}
        profiles = {row['revision_id']: dict(row) for row in s.company.conn.execute(
            sa.select(p).where(p.c.revision_id.in_(revision_ids))).mappings()}
        items = [SalesTaxAdjustmentSummaryOutput(**summary(
            header, revisions[header['current_revision_id']], profiles[header['current_revision_id']]))
            for header in ordered]
    return SalesTaxAdjustmentPageOutput(items=items, count=len(found), has_more=more,
                                        next_cursor=continuation(state, len(found), more),
                                        audit_watermark=state.sequence)


# ---------------------------------------------------------------- writing one adjustment


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


def prepare_adjust(s, ctx, inp):
    info = _info(s)
    currency = info['home_currency']
    agency = _agency(s, inp.agency)
    offset_row = _adjustment_account(s, inp.adjustment_account, currency)
    offset = _account_facts(offset_row)
    klass = _class(s, inp.class_id) if inp.class_id else None
    liability = _account_facts(liability_account(s))
    total = parse_domestic_amount(inp.amount, currency, 'amount').minor_units
    if total <= 0:
        raise _invalid('amount', 'must be more than zero; choose increase or reduce for the sign')
    before = owed(s, agency.id, inp.date)
    after = before + signed(inp.direction, total)
    journals.open_dates(s, [inp.date])
    number, sequence = _number(s, inp.number)
    at, event = clock.now_iso(), new_id()
    profile = SalesTaxAdjustmentProfile(
        agency=agency, liability_account=liability, adjustment_account=offset,
        direction=inp.direction, amount_minor_units=total, currency=currency,
        owed_before_minor_units=before, owed_after_minor_units=after)

    def created():
        return dict(id=new_id(), created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)

    header = dict(id=new_id(), **common(s.actor.id, ctx.interface.value, at), type=DOCUMENT_TYPE,
                  status='posted', voided_at=None, voided_by=None, void_reason=None,
                  void_posting_batch_id=None)
    pending = {table: [] for table, _, _ in TABLE_KINDS}
    revision = dict(**created(), transaction_id=header['id'], revision_number=1,
                    supersedes_revision_id=None, date=inp.date, number=number, name_type='vendor',
                    name_id=agency.id, memo=inp.memo, total_minor_units=total, currency=currency,
                    issuer_snapshot=json_text({}), custom_fields_snapshot=json_text({}),
                    audit_event_id=event)
    header.update(number=number, current_revision_id=revision['id'])
    pending['transaction_revisions'].append(revision)
    identity = dict(**created(), transaction_id=header['id'])
    pending['document_line_identities'].append(identity)
    description = ('Increase' if inp.direction == 'increase' else 'Reduce') + ' sales tax due'
    envelope = dict(**created(), transaction_id=header['id'], revision_id=revision['id'],
                    line_id=identity['id'], position=1, kind=LINE_KIND, account_id=None,
                    side=None, amount_minor_units=None, currency=currency, account_snapshot=None,
                    name_type='vendor', name_id=agency.id, party_name=agency.label,
                    class_id=klass.id if klass else None, class_name=klass.label if klass else None,
                    description=description, **dict.fromkeys(journals.FACTS))
    pending['document_lines'].append(envelope)
    batch = dict(**created(), transaction_id=header['id'], revision_id=revision['id'],
                 kind='original', effective_date=inp.date, reverses_batch_id=None,
                 replaces_batch_id=None, audit_event_id=event)
    pending['posting_batches'].append(batch)

    def leg(account, debit, line_no):
        value = dict(**created(), transaction_id=header['id'], batch_id=batch['id'], line_no=line_no,
                     account_id=account.id, account_snapshot=json_text(account.model_dump()),
                     currency=currency, name_type='vendor', name_id=agency.id, party_name=agency.label,
                     class_id=klass.id if klass else None, class_name=klass.label if klass else None,
                     description=inp.memo or description,
                     debit_minor_units=total if debit else 0, credit_minor_units=0 if debit else total,
                     reversed_line_id=None, **dict.fromkeys(journals.FACTS))
        pending['posting_lines'].append(value)
        source = dict(**created(), transaction_id=header['id'], posting_line_id=value['id'],
                      revision_id=revision['id'], document_line_id=envelope['id'],
                      amount_minor_units=total, currency=currency,
                      reversed_source_id=None, tax_component_id=None)
        pending['posting_line_sources'].append(source)
        return source

    # An increase credits the liability; a reduction debits it. Line 1 is always the liability.
    liability_source = leg(liability, inp.direction == 'reduce', 1)
    leg(offset, inp.direction == 'increase', 2)
    pending['sales_tax_adjustment_profiles'].append(dict(
        transaction_id=header['id'], revision_id=revision['id'], created_at=at,
        created_by=s.actor.id, created_via=ctx.interface.value, audit_event_id=event,
        type=DOCUMENT_TYPE, agency_id=agency.id, liability_account_id=liability.id,
        offset_account_id=offset.id, direction=inp.direction, amount_minor_units=total,
        currency=currency, liability_posting_source_id=liability_source['id'],
        profile_snapshot=json_text(profile.model_dump())))
    preview = _output(s, header, revision, pending['sales_tax_adjustment_profiles'][0], pending,
                      model=SalesTaxAdjustmentWriteOutput, changed_fields=[])
    return Plan(preview, dict(input=inp, operation='adjust', header=header, before=None,
                              revision=revision, pending=pending, event=event, at=at,
                              sequence=sequence, currency=currency, changed=True))


def prepare_void(s, ctx, inp):
    header = resolve(s, inp.adjustment)
    if inp.expected_version is not None:
        journals.version_meta(s, header, inp.expected_version)
    revision = journals.revision(s, header)
    if header['status'] != 'posted':
        raise BookflowError('E_APPLICATION_INACTIVE', details={
            'adjustment_id': header['id'], 'status': header['status'],
            'next': 'A voided adjustment moved nothing and cannot be voided again.'})
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
                      model=SalesTaxAdjustmentWriteOutput, changed_fields=['status'])
    return Plan(preview, dict(input=inp, operation='void', header=changed, before=header,
                              revision=revision, pending=pending, event=event, at=at,
                              sequence=None, currency=revision['currency'], changed=True))


def prepare(s, ctx, inp, operation):
    return prepare_adjust(s, ctx, inp) if operation == 'adjust' else prepare_void(s, ctx, inp)


# ---------------------------------------------------------------- an independent check


def _require(condition, problem):
    if not condition:
        raise BookflowError('E_INTERNAL', message='Invalid sales tax adjustment aggregate: ' + problem)


def validate(plan, s, ctx):
    """Check the rebuilt aggregate against the books' rules without reusing the writer's sums."""
    data = plan.data
    header, revision, pending, old = data['header'], data['revision'], data['pending'], data['before']
    operation = data['operation']
    _require(header['type'] == DOCUMENT_TYPE, 'wrong document type')
    _require(header['updated_by'] == s.actor.id and header['updated_via'] == ctx.interface.value,
             'header writer attribution')
    for name, _, key in TABLE_KINDS:
        values = pending[name]
        _require(len({row[key] for row in values}) == len(values), 'duplicate history identity')
        _require(all(row['transaction_id'] == header['id'] for row in values), 'cross-document history')
    if old:
        current = resolve(s, data['input'].adjustment)
        _require(current == old and current['status'] == 'posted' and operation == 'void'
                 and header['status'] == 'voided' and header['version'] == old['version'] + 1,
                 'stale or wrong prior adjustment')
    else:
        _require(operation == 'adjust' and header['version'] == 1 and header['status'] == 'posted',
                 'invalid new adjustment')
        _require(not effects.rows(s, c.transactions, c.transactions.c.type == DOCUMENT_TYPE,
                                  c.transactions.c.number == header['number']),
                 'that adjustment number is already used')
    batches, legs = pending['posting_batches'], pending['posting_lines']
    _require(len(batches) == 1 and batches[0]['kind'] == ('original' if operation == 'adjust' else 'reversal')
             and batches[0]['effective_date'] == revision['date'], 'one dated effect per write')
    _require(len(legs) == 2 and sorted(leg['line_no'] for leg in legs) == [1, 2], 'two legs')
    debit = sum(leg['debit_minor_units'] for leg in legs)
    credit = sum(leg['credit_minor_units'] for leg in legs)
    _require(all(type(leg['debit_minor_units']) is int and type(leg['credit_minor_units']) is int
                 and bool(leg['debit_minor_units']) != bool(leg['credit_minor_units']) for leg in legs),
             'a posting must have exactly one positive side')
    _require(debit == credit == revision['total_minor_units'] > 0, 'an adjustment does not balance at its total')
    if operation != 'adjust':
        return
    row = pending['sales_tax_adjustment_profiles'][0]
    _require(len(pending['sales_tax_adjustment_profiles']) == 1 and row['revision_id'] == revision['id'],
             'one header per adjustment')
    live = liability_account(s)
    _require(row['liability_account_id'] == live['id'] != row['offset_account_id'],
             'an adjustment posts to the chart\'s own sales tax payable account')
    source = next(item for item in pending['posting_line_sources']
                  if item['id'] == row['liability_posting_source_id'])
    liability_leg = next(leg for leg in legs if leg['id'] == source['posting_line_id'])
    other = next(leg for leg in legs if leg['id'] != liability_leg['id'])
    moved = liability_leg['credit_minor_units'] - liability_leg['debit_minor_units']
    _require(liability_leg['account_id'] == live['id'] and other['account_id'] == row['offset_account_id'],
             'the legs are not the liability and the adjustment account')
    _require(moved == signed(row['direction'], row['amount_minor_units']), 'the liability moved the wrong way')
    captured = SalesTaxAdjustmentProfile.model_validate_json(row['profile_snapshot'])
    _require(captured.owed_before_minor_units == owed(s, row['agency_id'], revision['date']),
             'the captured balance is not what the books owe')


# ---------------------------------------------------------------- persistence


def apply(plan, ctx, s):
    # Rebuilt inside the writer transaction, as every document here is: the balance owed, the
    # numbering and the closing date are only decisive at the moment of the write.
    operation = plan.data['operation']
    fresh = prepare(s, ctx, plan.data['input'], operation)
    validate(fresh, s, ctx)
    data = fresh.data
    header, before, pending = data['header'], data['before'], data['pending']
    touched = [Touched('transaction', header['id'], 'update' if before else 'create',
                       before['version'] if before else None, header['version'],
                       header, before, db='company')]
    for table, kind, key in TABLE_KINDS:
        touched.extend(Touched(kind, row[key], 'create', None, 1, effects.decoded(row), db='company')
                       for row in pending[table])
    command_name = 'sales-tax ' + ('adjust' if operation == 'adjust' else 'adjustment void')
    summary_text = f"{operation} sales tax adjustment {header['number']}"
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
