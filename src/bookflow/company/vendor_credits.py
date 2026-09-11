"""Vendor credits: what a vendor owes back, and the bills it answers.

A vendor credit is the bill read backwards. Entering one debits Accounts Payable by the total
and credits each captured purchase account by its own line amount, so the vendor's payable
falls by exactly what was credited and the expense that was recognised comes back off. Voiding
one reverses that at its own date and keeps every revision readable. That is the bill's
lifecycle with the signs swapped, deliberately.

**It is a source, not a payable.** A bill creates an ``ap_obligation_keys`` row because
somebody owes it; a credit creates none, because nobody owes a credit -- what it has is
capacity to settle something else. ``report unpaid-bills`` lists documents of type ``bill``,
so an obligation key here would put a credit on the unpaid-bills list at a negative balance.
Instead this writes an ``ap_source_keys`` row of kind ``vendor_credit``, carrying the same
``(vendor, payable account, currency)`` triple a bill payment's source carries, with one
``ap_source_components`` row per credited line naming the exact ``posting_line_sources`` row
that debited Accounts Payable for it.

**The same edge a check uses.** ``vendor-credit apply`` writes ``ap_applications`` rows, the
rows ``bill pay`` writes, so ``bills.applied_totals`` answers one number for a bill whatever
settled it: a bill settled entirely by a credit reads ``paid``, leaves the unpaid list and ages
to nothing, with no branch anywhere that asks which kind of money did it. What did it is a
separate, additive read -- ``settlement_current.sources`` on the bill -- so a page can say
"46254 credit, 53746 cash" without any caller of the scalar learning a new shape.

The selection, the capacity arithmetic and the compatibility rule are imported from
``bill_payments`` rather than restated: what is open on a bill, what a source still has free
and which vendor may settle which payable are one implementation for both kinds of source, so
the two can never drift into disagreeing about the same bill.
"""
from __future__ import annotations

import json

import sqlalchemy as sa

from bookflow.company import ap_settlement, bill_payments, bills, journals, schema as c
from bookflow.company import document_effects as effects
from bookflow.company.journal_models import checked_sum
from bookflow.company.parties import resolve_party
from bookflow.company.vendor_credit_facts import BillExpenseProfile, Origin, Vendor, VendorCreditProfile
from bookflow.company.vendor_credit_models import (
    VendorCreditComponentOutput, VendorCreditExpenseOutput, VendorCreditOutput,
    VendorCreditPageOutput, VendorCreditRevisionOutput, VendorCreditSettlementOutput,
    VendorCreditSourceOutput, VendorCreditSummaryOutput, VendorCreditWriteOutput, reference_key,
)
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import is_ulid, new_id
from bookflow.core.money import Money
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.hub.users import common

DOCUMENT_TYPE = 'vendor_credit'

# Insert order matters: attribution rows follow the posting lines they hang off, and a source
# component follows both the source key it belongs to and the attribution it names.
TABLE_KINDS = (
    ('transaction_revisions', 'transaction_revision', 'id'),
    ('document_line_identities', 'document_line_identity', 'id'),
    ('document_lines', 'document_line', 'id'),
    ('vendor_credit_profiles', 'vendor_credit_profile', 'revision_id'),
    ('vendor_credit_expense_lines', 'vendor_credit_expense_line', 'document_line_id'),
    ('posting_batches', 'posting_batch', 'id'),
    ('posting_lines', 'posting_line', 'id'),
    ('posting_line_sources', 'posting_line_source', 'id'),
    ('ap_source_keys', 'ap_source_key', 'id'),
    ('ap_source_components', 'ap_source_component', 'id'),
)

# Attaching and detaching write settlement history and nothing else: no revision, no posting.
SETTLEMENT_TABLE_KINDS = (('ap_applications', 'ap_application', 'id'),)


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
    return effects.rows(s, c.vendor_credit_profiles,
                        c.vendor_credit_profiles.c.revision_id == revision['id'])[0]


def merged_line(envelope, profile):
    """One credited line as a reader sees it: the envelope's identity, the profile's money.

    Both rows carry ``account_id`` and ``amount_minor_units``, and on a purchase envelope both
    are null -- the accounting lives in the profile. Spelling the overlap out rather than
    merging blindly is what keeps a line's account from silently reading as null.
    """
    return dict(envelope, memo=envelope['description'], account_id=profile['account_id'],
                amount_minor_units=profile['amount_minor_units'],
                customer_id=profile['customer_id'], line_snapshot=profile['line_snapshot'])


def saved_lines(s, revision):
    envelopes = effects.rows(s, c.document_lines, c.document_lines.c.revision_id == revision['id'],
                             order=c.document_lines.c.position)
    profiles = {row['document_line_id']: row for row in effects.rows(
        s, c.vendor_credit_expense_lines, c.vendor_credit_expense_lines.c.revision_id == revision['id'])}
    return [merged_line(envelope, profiles[envelope['id']]) for envelope in envelopes]


# ---------------------------------------------------------------- what a credit captures


def resolve_header(s, inp):
    """Every header fact a vendor-credit revision captures, and where each one came from.

    The bill's resolution minus terms and a due date: a credit is not owed on a date. The
    vendor, the payable account, the account rules and the class come from the bill's own
    resolvers, so a credit can never be owed out of an account a bill could not be owed out of.
    """
    info = bills._info(s)
    currency = info['home_currency']
    origins = {}

    row = resolve_party(s.company, 'vendor', inp.vendor)
    if not row['active']:
        raise BookflowError('E_INACTIVE_REFERENCE',
                            details={'record_type': 'vendor', 'record_id': row['id'], 'field': 'vendor'})
    vendor = Vendor(**bills._reference(row).model_dump(),
                    **{k: row.get(k) for k in ('company_name', 'email', 'phone', 'account_number')})
    origins['vendor'] = Origin(kind='explicit')

    ap_account, origins['ap_account'] = bills._ap_account(s, inp.ap_account, currency, None,
                                                          noun='vendor credit')
    reference = (inp.supplier_reference or '').strip() or None
    if inp.supplier_reference is not None:
        origins['supplier_reference'] = Origin(kind='explicit')
    klass = (bills._reference(bills._list_row(s, 'class', c.classes, inp.class_id, 'class_id', 'class'))
             if inp.class_id else None)
    if inp.class_id is not None:
        origins['class_id'] = Origin(kind='explicit')
    issuer = {key: value for key, value in info.items()
              if key in ('id', 'legal_name', 'home_currency')
              or key.startswith(('address_', 'legal_address_'))}
    return dict(vendor=vendor, ap_account=ap_account, supplier_reference=reference,
                supplier_reference_key=reference_key(reference), class_id=klass,
                currency=currency, origins=origins, issuer=issuer)


def commercial(s, inp):
    """Resolve the whole credit: header, lines, total and the number it takes."""
    header = resolve_header(s, inp)
    currency = header['currency']
    lines = [bills._expense_line(s, line, header['class_id'], currency, index)
             for index, line in enumerate(inp.expenses)]
    total = checked_sum((line['amount_minor_units'] for line in lines), 'expenses.total')
    number, sequence = effects.allocate(s, DOCUMENT_TYPE, inp.number)
    profile = VendorCreditProfile(
        vendor=header['vendor'], ap_account=header['ap_account'],
        supplier_reference=header['supplier_reference'],
        supplier_reference_key=header['supplier_reference_key'], class_id=header['class_id'],
        expense_total_minor_units=total, currency=currency, origins=header['origins'])
    return dict(profile=profile, lines=lines, total=total, currency=currency, number=number,
                sequence=sequence, issuer=header['issuer'], memo=inp.memo, date=inp.date)


# ---------------------------------------------------------------- the settlement seam


def source_row(s, transaction_id, pending=None):
    return ap_settlement.source_key_row(s, transaction_id, pending)


def settlement_output(header, revision, source, applied):
    amount = revision['total_minor_units'] if header['status'] == 'posted' else 0
    unapplied = amount - applied
    currency = revision['currency']
    return VendorCreditSettlementOutput(
        credit_id=header['id'], source_key_id=source['id'] if source else '',
        version=header['version'], revision_id=revision['id'],
        amount_minor_units=amount, applied_minor_units=applied, unapplied_minor_units=unapplied,
        amount=Money(amount, currency).to_dict(), applied=Money(applied, currency).to_dict(),
        unapplied=Money(unapplied, currency).to_dict(), currency=currency,
        status=('voided' if header['status'] == 'voided' else
                'applied' if unapplied == 0 else 'partial' if applied else 'unapplied'))


# ---------------------------------------------------------------- reads


def summary(header, revision, profile, settlement):
    currency = revision['currency']
    captured = VendorCreditProfile.model_validate_json(profile['profile_snapshot'])
    return dict(header, date=revision['date'], vendor_id=profile['vendor_id'],
                vendor_name=captured.vendor.label, ap_account_id=profile['ap_account_id'],
                supplier_reference=profile['supplier_reference'], memo=revision['memo'],
                currency=currency,
                expense_total_minor_units=profile['expense_total_minor_units'],
                total_minor_units=revision['total_minor_units'],
                expense_total=Money(profile['expense_total_minor_units'], currency).to_dict(),
                total=Money(revision['total_minor_units'], currency).to_dict(),
                settlement_current=settlement)


def _source_output(s, header, revision, edges, pending=None):
    source = source_row(s, header['id'], pending)
    if source is None:
        return None
    components = [row for row in (pending or {}).get('ap_source_components', [])
                  if row['revision_id'] == revision['id']]
    if not components:
        components = effects.rows(s, c.ap_source_components,
                                  c.ap_source_components.c.revision_id == revision['id'],
                                  order=c.ap_source_components.c.id)
    attached = {}
    for edge in edges:
        if edge['active']:
            attached[edge['source_component_id']] = (attached.get(edge['source_component_id'], 0)
                                                     + edge['amount_minor_units'])
    currency = revision['currency']
    return VendorCreditSourceOutput(**source, components=[VendorCreditComponentOutput(
        **row, amount=Money(row['amount_minor_units'], currency).to_dict(),
        applied_minor_units=attached.get(row['id'], 0),
        applied=Money(attached.get(row['id'], 0), currency).to_dict()) for row in components])


def revision_output(s, header, revision, edges, pending=None):
    pending = pending or {}
    currency = revision['currency']
    owned = {row['id'] for row in ([r for r in pending.get('ap_source_components', [])
                                    if r['revision_id'] == revision['id']]
                                   or effects.rows(s, c.ap_source_components,
                                                   c.ap_source_components.c.revision_id == revision['id']))}
    mine = [row for row in edges if row['source_component_id'] in owned]
    numbers = bill_payments._bill_numbers(s, sorted({row['obligation_transaction_id'] for row in mine}), pending)
    saved_batches = effects.rows(s, c.posting_batches, c.posting_batches.c.revision_id == revision['id'],
                                 order=c.posting_batches.c.id)
    summaries = [journals.batch_output(s, batch) for batch in saved_batches]
    summaries += [journals.batch_output(s, batch, [line for line in pending['posting_lines']
                                                   if line['batch_id'] == batch['id']])
                  for batch in pending.get('posting_batches', []) if batch['revision_id'] == revision['id']]
    envelopes = [line for line in pending.get('document_lines', []) if line['revision_id'] == revision['id']]
    if envelopes:
        details = {row['document_line_id']: row for row in pending['vendor_credit_expense_lines']}
        lines = [merged_line(line, details[line['id']]) for line in envelopes]
    else:
        lines = saved_lines(s, revision)
    profile = next((row for row in pending.get('vendor_credit_profiles', [])
                    if row['revision_id'] == revision['id']), None) or profile_row(s, revision)
    values = {k: v for k, v in revision.items() if not k.endswith('_snapshot')}
    values.update(expense_total_minor_units=profile['expense_total_minor_units'],
                  expense_total=Money(profile['expense_total_minor_units'], currency).to_dict(),
                  total=Money(revision['total_minor_units'], currency).to_dict(),
                  line_count=len(lines), batches=summaries,
                  applications=bill_payments.application_outputs(mine, currency, numbers))
    return VendorCreditRevisionOutput(
        **values, profile=json.loads(profile['profile_snapshot']),
        issuer_snapshot=json.loads(revision['issuer_snapshot']),
        source=_source_output(s, header, revision, edges, pending),
        expenses=[VendorCreditExpenseOutput(
            **dict(line, amount=Money(line['amount_minor_units'], currency).to_dict(),
                   line_snapshot=json.loads(line['line_snapshot']))) for line in lines])


def _output(s, header, revision, pending=None, *, model=VendorCreditOutput, **extra):
    edges = bill_payments._edges(s, header, pending)
    source = source_row(s, header['id'], pending)
    applied = sum(row['amount_minor_units'] for row in edges if row['active'])
    profile = next((row for row in (pending or {}).get('vendor_credit_profiles', [])
                    if row['revision_id'] == revision['id']), None) or profile_row(s, revision)
    numbers = bill_payments._bill_numbers(s, sorted({row['obligation_transaction_id'] for row in edges}), pending)
    return model(
        **summary(header, revision, profile,
                  settlement_output(header, revision, source,
                                    0 if header['status'] == 'voided' else applied)),
        revision=revision_output(s, header, revision, edges, pending),
        applications=bill_payments.application_outputs(edges, revision['currency'], numbers), **extra)


def show(s, inp):
    header = resolve(s, inp.credit)
    requested = journals.revision(s, header, inp.revision_number)
    edges = bill_payments._edges(s, header)
    source = source_row(s, header['id'])
    applied = sum(row['amount_minor_units'] for row in edges if row['active'])
    current = journals.revision(s, header)
    return VendorCreditOutput(
        **summary(header, current, profile_row(s, current),
                  settlement_output(header, current, source,
                                    0 if header['status'] == 'voided' else applied)),
        revision=revision_output(s, header, requested, edges),
        applications=bill_payments.application_outputs(edges, current['currency'],
                                                       bill_payments._bill_numbers(
                                                           s, sorted({row['obligation_transaction_id']
                                                                      for row in edges}))))


def page(s, ctx, inp):
    from bookflow.company.query import continuation, page_state

    class Contract:
        cursor = inp.cursor
        query = None

        def model_dump(self, **kw):
            return inp.model_dump(**kw)

    state = page_state(s, 'vendor-credit query', Contract(), ctx.on_behalf_of)
    t, r, p = c.transactions, c.transaction_revisions, c.vendor_credit_profiles
    query = (sa.select(t.c.id).select_from(
        t.join(r, r.c.id == t.c.current_revision_id).join(p, p.c.revision_id == r.c.id))
        .where(t.c.type == DOCUMENT_TYPE))
    if inp.vendor:
        query = query.where(p.c.vendor_id == resolve_party(s.company, 'vendor', inp.vendor)['id'])
    if inp.bill:
        # "What credited this bill" is the read a person actually wants, so the bill is a
        # filter here rather than a separate command with its own paging rules.
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
    if inp.supplier_reference is not None:
        query = query.where(p.c.supplier_reference_key == reference_key(inp.supplier_reference))
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
        sa.select(c.vendor_credit_profiles).where(
            c.vendor_credit_profiles.c.revision_id.in_(revision_ids))).mappings()} if found else {}
    sources = {row['transaction_id']: dict(row) for row in s.company.conn.execute(
        sa.select(c.ap_source_keys).where(
            c.ap_source_keys.c.transaction_id.in_(identifiers))).mappings()} if found else {}
    applied = ap_settlement.source_applied_totals(s, [row['id'] for row in sources.values()])
    items = []
    for header in ordered:
        revision = revisions[header['current_revision_id']]
        source = sources.get(header['id'])
        total = applied.get(source['id'], 0) if source else 0
        items.append(VendorCreditSummaryOutput(**summary(
            header, revision, profiles[revision['id']],
            settlement_output(header, revision, source,
                              0 if header['status'] == 'voided' else total))))
    return VendorCreditPageOutput(items=items, count=len(found), has_more=more,
                                  next_cursor=continuation(state, len(found), more),
                                  audit_watermark=state.sequence)


# ---------------------------------------------------------------- writing one credit


def _business_postings(s, header, revision, batch, resolved, pending, created, event):
    """Cr each credited account its own line; Dr Accounts Payable the total, once.

    The mirror of the bill: the payable falls by one figure because that is what the vendor
    now owes less, and each entered line's share of it is an attribution row on that debit
    rather than a leg of its own. Those attribution rows are what the source components name,
    which is how an application can later land on particular credited lines.
    """
    profile = resolved['profile']
    currency = revision['currency']
    envelopes = [row for row in pending['document_lines'] if row['revision_id'] == revision['id']]
    profiles = {row['document_line_id']: row for row in pending['vendor_credit_expense_lines']}
    line_no = 0

    def leg(account, amount, debit, class_id, class_name, description):
        nonlocal line_no
        line_no += 1
        value = dict(**created(), transaction_id=header['id'], batch_id=batch['id'], line_no=line_no,
                     account_id=account.id, account_snapshot=json_text(account.model_dump()),
                     currency=currency, name_type='vendor', name_id=profile.vendor.id,
                     party_name=profile.vendor.label, class_id=class_id, class_name=class_name,
                     description=description,
                     debit_minor_units=amount if debit else 0,
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

    for envelope in envelopes:
        line = profiles[envelope['id']]
        facts = BillExpenseProfile.model_validate_json(line['line_snapshot'])
        given_back = leg(facts.account, line['amount_minor_units'], False,
                         envelope['class_id'], envelope['class_name'], envelope['description'])
        attribute(given_back, envelope, line['amount_minor_units'])
    payable = leg(profile.ap_account, resolved['total'], True,
                  profile.class_id.id if profile.class_id else None,
                  profile.class_id.label if profile.class_id else None, resolved['memo'])
    source_key = dict(**created(), transaction_id=header['id'], ordinal=1,
                      source_type=DOCUMENT_TYPE, vendor_id=profile.vendor.id,
                      ap_account_id=profile.ap_account.id, currency=currency, audit_event_id=event)
    pending['ap_source_keys'].append(source_key)
    for envelope in envelopes:
        line = profiles[envelope['id']]
        attribution = attribute(payable, envelope, line['amount_minor_units'])
        pending['ap_source_components'].append(dict(
            **created(), transaction_id=header['id'], revision_id=revision['id'],
            key_id=source_key['id'], document_line_id=envelope['id'], ordinal=1,
            posting_source_id=attribution['id'], amount_minor_units=line['amount_minor_units'],
            currency=currency, audit_event_id=event))


def _has_applications(s, header):
    return bool(ap_settlement.active_applications(s, header['id']))


def prepare_write(s, ctx, inp, operation):
    """``post`` and ``void``: the two moments a vendor credit changes the ledger."""
    old_header = resolve(s, inp.credit) if operation == 'void' else None
    old_revision = journals.revision(s, old_header) if old_header else None
    if operation == 'void':
        if not ctx.reason or not ctx.reason.strip():
            raise BookflowError('E_REASON_REQUIRED')
        if len(ctx.reason.strip()) > 140:
            raise _invalid('reason', 'must be at most 140 characters')
        if inp.expected_version is not None:
            journals.version_meta(s, old_header, inp.expected_version)
        if _has_applications(s, old_header):
            raise BookflowError('E_HAS_APPLICATIONS', details={
                'credit_id': old_header['id'],
                'next': 'Unapply what this credit settled before voiding it.'})
    at, event = clock.now_iso(), new_id()

    def created():
        return dict(id=new_id(), created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)

    row_provenance = dict(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)
    pending = {table: [] for table, _, _ in TABLE_KINDS}

    if operation == 'void':
        if old_header['status'] == 'voided':
            return Plan(_output(s, old_header, old_revision, model=VendorCreditWriteOutput, changed=False),
                        dict(input=inp, operation='void', changed=False))
        journals.open_dates(s, [old_revision['date']])
        header = dict(old_header)
        current_batch = effects.rows(s, c.posting_batches,
                                     c.posting_batches.c.revision_id == old_revision['id'],
                                     c.posting_batches.c.kind != 'reversal')[0]
        inverse = effects.reverse(s, header, old_revision, current_batch, event, created, pending)
        header.update(version=old_header['version'] + 1, updated_at=at, updated_by=s.actor.id,
                      updated_via=ctx.interface.value, status='voided', voided_at=at,
                      voided_by=s.actor.id, void_reason=ctx.reason.strip(),
                      void_posting_batch_id=inverse['id'])
        plan = Plan(_output(s, header, old_revision, pending, model=VendorCreditWriteOutput,
                            changed_fields=['status']),
                    dict(input=inp, operation='void', changed=True, header=header, before=old_header,
                         old_revision=old_revision, pending=pending, sequence=None, event=event,
                         resolved=None))
        from bookflow.company.vendor_credit_validation import validate
        validate(plan, s, ctx)
        return plan

    resolved = commercial(s, inp)
    journals.open_dates(s, [resolved['date']])
    bills._posting_accounts_active(s, dict(profile=resolved['profile'], lines=resolved['lines']))
    currency = resolved['currency']
    header = dict(id=new_id(), **common(s.actor.id, ctx.interface.value, at), type=DOCUMENT_TYPE,
                  status='posted', voided_at=None, voided_by=None, void_reason=None,
                  void_posting_batch_id=None)
    revision = dict(**created(), transaction_id=header['id'], revision_number=1,
                    supersedes_revision_id=None, date=resolved['date'], number=resolved['number'],
                    name_type='vendor', name_id=resolved['profile'].vendor.id, memo=resolved['memo'],
                    total_minor_units=resolved['total'], currency=currency,
                    issuer_snapshot=json_text(resolved['issuer']),
                    custom_fields_snapshot=json_text({}), audit_event_id=event)
    header.update(number=revision['number'], current_revision_id=revision['id'])
    pending['transaction_revisions'].append(revision)
    profile = resolved['profile']
    pending['vendor_credit_profiles'].append(dict(
        transaction_id=header['id'], revision_id=revision['id'], **row_provenance,
        type=DOCUMENT_TYPE, vendor_id=profile.vendor.id, ap_account_id=profile.ap_account.id,
        supplier_reference=profile.supplier_reference,
        supplier_reference_key=profile.supplier_reference_key,
        expense_total_minor_units=resolved['total'],
        profile_snapshot=json_text(profile.model_dump())))
    for position, line in enumerate(resolved['lines'], 1):
        identity = dict(**created(), transaction_id=header['id'])
        pending['document_line_identities'].append(identity)
        facts = line['profile']
        envelope = dict(**created(), transaction_id=header['id'], revision_id=revision['id'],
                        line_id=identity['id'], position=position, kind='purchase', account_id=None,
                        side=None, amount_minor_units=None, currency=currency, account_snapshot=None,
                        name_type='vendor', name_id=profile.vendor.id, party_name=profile.vendor.label,
                        class_id=facts.class_id.id if facts.class_id else None,
                        class_name=facts.class_id.label if facts.class_id else None,
                        description=line['memo'], **dict.fromkeys(journals.FACTS))
        pending['document_lines'].append(envelope)
        pending['vendor_credit_expense_lines'].append(dict(
            document_line_id=envelope['id'], transaction_id=header['id'], revision_id=revision['id'],
            **row_provenance, account_id=facts.account.id,
            amount_minor_units=line['amount_minor_units'],
            customer_id=facts.customer.id if facts.customer else None,
            line_snapshot=json_text(facts.model_dump())))
    batch = dict(**created(), transaction_id=header['id'], revision_id=revision['id'],
                 kind='original', effective_date=revision['date'], reverses_batch_id=None,
                 replaces_batch_id=None, audit_event_id=event)
    pending['posting_batches'].append(batch)
    _business_postings(s, header, revision, batch, resolved, pending, created, event)
    plan = Plan(_output(s, header, revision, pending, model=VendorCreditWriteOutput),
                dict(input=inp, operation='post', changed=True, header=header, before=None,
                     old_revision=None, pending=pending, sequence=resolved['sequence'],
                     event=event, resolved=resolved))
    from bookflow.company.vendor_credit_validation import validate
    validate(plan, s, ctx)
    return plan


# ------------------------------------------------ pointing the credit at a bill, and taking it back


def _credit_for_write(s, inp):
    header = resolve(s, inp.credit)
    if inp.expected_version is not None:
        journals.version_meta(s, header, inp.expected_version)
    revision = journals.revision(s, header)
    if header['status'] != 'posted':
        raise BookflowError('E_APPLICATION_INACTIVE', details={
            'credit_id': header['id'], 'status': header['status'],
            'next': 'A voided credit settles nothing and cannot be applied.'})
    return header, revision


def prepare_apply(s, ctx, inp):
    """Attach what this credit still has free to the same vendor's open bills.

    Nothing is posted and no revision is written: Accounts Payable moved when the credit
    posted, so the whole of this is settlement edges -- the same edges ``bill payment apply``
    writes, drawn from the credited lines in order, so applying less than what is free leaves
    the rest free and one credited line can answer two bills.
    """
    header, revision = _credit_for_write(s, inp)
    currency = revision['currency']
    date = inp.date or revision['date']
    if date < revision['date']:
        raise _invalid('date', f'an application dated {date} cannot come before credit '
                               f'{header["number"]}, which is dated {revision["date"]}')
    source = source_row(s, header['id'])
    if source is None:
        raise BookflowError('E_INTERNAL', message='This credit carries no settlement source')
    supply = [[component, units] for component, units
              in bill_payments._free_capacity(s, header, revision) if units > 0]
    available = sum(units for _, units in supply)

    def beyond(requested):
        return BookflowError('E_APPLICATION_CAPACITY', details={
            'credit_id': header['id'], 'credit_number': header['number'],
            'requested_minor_units': requested, 'available_minor_units': available,
            'requested': Money(requested, currency).to_dict(),
            'available': Money(available, currency).to_dict(),
            'next': 'Apply at most what this credit still has free; unapply what it holds, or '
                    'settle the rest with a payment.'})

    if not available:
        raise beyond(0)
    chosen = bill_payments._selected(s, inp.bills, date, currency, capacity=available)
    bill_payments._compatible(source, chosen, header['id'])
    # An application posts nothing, but it does change what the books say was open on a date,
    # so a closed period refuses it exactly as `bill payment apply` refuses one.
    journals.open_dates(s, [date])
    total = checked_sum((row['amount'] for row in chosen), 'bills.total')
    if total > available:
        raise beyond(total)
    at, event = clock.now_iso(), new_id()
    pending = {table: [] for table, _, _ in SETTLEMENT_TABLE_KINDS}
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
    return _settlement_plan(s, ctx, inp, 'apply', changed, header, revision, pending, event, at,
                            changed_headers)


def prepare_unapply(s, ctx, inp):
    header, revision = _credit_for_write(s, inp)
    active = ap_settlement.active_applications(s, header['id'])
    if inp.bills is not None:
        wanted = {bills.resolve(s, selector)['id'] for selector in inp.bills}
        missing = wanted - {row['obligation_transaction_id'] for row in active}
        if missing:
            raise BookflowError('E_APPLICATION_INACTIVE', details={
                'credit_id': header['id'], 'bill_ids': sorted(missing),
                'next': 'This credit has nothing still applied to that bill.'})
        active = [row for row in active if row['obligation_transaction_id'] in wanted]
    if not active:
        return Plan(_output(s, header, revision, model=VendorCreditWriteOutput, changed=False),
                    dict(input=None, operation='unapply', changed=False))
    # Each detachment is the exact negation of an application at that application's own
    # effective date, so each of those dates has to be open: what the books said was open on a
    # closed date would otherwise change.
    journals.open_dates(s, sorted({row['effective_date'] for row in active}))
    at, event = clock.now_iso(), new_id()
    pending = {table: [] for table, _, _ in SETTLEMENT_TABLE_KINDS}
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
    return _settlement_plan(s, ctx, inp, 'unapply', changed, header, revision, pending, event, at,
                            changed_headers)


def _settlement_plan(s, ctx, inp, operation, changed, header, revision, pending, event, at,
                     changed_headers):
    plan = Plan(_output(s, changed, revision, pending, model=VendorCreditWriteOutput,
                        changed_fields=['applications']),
                dict(input=inp, operation=operation, changed=True, header=changed, before=header,
                     old_revision=revision, pending=pending, sequence=None, event=event,
                     changed_headers=changed_headers, resolved=None))
    from bookflow.company.vendor_credit_validation import validate
    validate(plan, s, ctx)
    return plan


# ---------------------------------------------------------------- persistence


def prepare(s, ctx, inp, operation):
    if operation == 'apply':
        return prepare_apply(s, ctx, inp)
    if operation == 'unapply':
        return prepare_unapply(s, ctx, inp)
    return prepare_write(s, ctx, inp, operation)


def apply(plan, ctx, s):
    # Rebuilt inside the writer transaction, the way every document here does it: open
    # balances, numbering and dates are only decisive at the moment of the write.
    if plan.data.get('changed') is False:
        return Applied(plan.preview, [], 'no change')
    operation = plan.data['operation']
    fresh = prepare(s, ctx, plan.data['input'], operation)
    if fresh.data.get('changed') is False:
        return Applied(fresh.preview, [], 'no change')
    from bookflow.company.vendor_credit_validation import validate
    validate(fresh, s, ctx)
    data = fresh.data
    command_name = 'vendor-credit ' + operation
    if operation in ('post', 'void'):
        return effects.persist(fresh, ctx, s, command_name=command_name, table_kinds=TABLE_KINDS)
    header, before = data['header'], data['before']
    touched = [Touched('transaction', header['id'], 'update', before['version'], header['version'],
                       header, before, db='company')]
    for table, kind, key in SETTLEMENT_TABLE_KINDS:
        touched.extend(Touched(kind, row[key], 'create', None, 1, effects.decoded(row), db='company')
                       for row in data['pending'][table])
    touched.extend(Touched('transaction', after['id'], 'update', old['version'], after['version'],
                           after, old, db='company') for old, after in data['changed_headers'])
    summary_text = f"{operation} vendor credit {header['number']}"
    audit.write_event_to(s.company, ctx, command_name, summary_text, touched,
                         actor_id=s.actor.id, actor_kind=s.actor.kind,
                         directive_code=getattr(s, 'directive_code', None), event_id=data['event'])
    s.company.conn.execute(c.transactions.update().where(
        c.transactions.c.id == header['id']).values(**header))
    for table, _, _ in SETTLEMENT_TABLE_KINDS:
        if data['pending'][table]:
            s.company.conn.execute(getattr(c, table).insert(), data['pending'][table])
    for _, after in data['changed_headers']:
        s.company.conn.execute(c.transactions.update().where(
            c.transactions.c.id == after['id']).values(**after))
    return Applied(fresh.preview, touched, summary_text, audited=True)
