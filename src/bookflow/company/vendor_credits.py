"""Vendor credits: what a vendor owes back, and the bills it answers.

A vendor credit is the bill read backwards. Entering one debits Accounts Payable by the total
and credits each captured purchase account by its own line amount, so the vendor's payable
falls by exactly what was credited and the expense that was recognised comes back off. Voiding
one reverses that at its own date and keeps every revision readable. That is the bill's
lifecycle with the signs swapped, deliberately.

**Two tabs, like the bill.** A credit has an Expenses grid and an Items grid, and it may be
entered on either or on both; they are two profile tables over one ``purchase`` envelope
family (``vendor_credit_expense_lines`` and ``vendor_credit_item_lines``), resolved by the
bill's own resolvers, so an item row is written by the code that writes a bill's. Both credit
the account they captured and debit Accounts Payable once for the sum. An item that holds stock
credits Inventory Asset and sends the quantity back at the *credited* amount -- one
``vendor_return`` movement through ``company/inventory_effects.py`` -- so the average cost of
what stays moves, which is the anchor's rule for stock returned to a vendor. A correction
reverses the movement it wrote and takes the new grid's; a void reverses it and stops; a return
dated before later sales recosts those sales exactly as a backdated bill does.

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
from bookflow.company import inventory_effects
from bookflow.company.journal_models import checked_sum
from bookflow.company.parties import resolve_party
from bookflow.company.vendor_credit_facts import (
    BillExpenseProfile, BillItemProfile, Origin, Vendor, VendorCreditProfile,
)
from bookflow.company.vendor_credit_models import (
    VendorCreditComponentOutput, VendorCreditExpenseOutput, VendorCreditItemOutput,
    VendorCreditOutput,
    VendorCreditHistoryOutput, VendorCreditPageOutput, VendorCreditRevisionOutput,
    VendorCreditRevisionSummaryOutput, VendorCreditSettlementOutput,
    VendorCreditSourceOutput, VendorCreditSummaryOutput, VendorCreditWriteOutput, reference_key,
)
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_quantity_micro_units
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
    ('vendor_credit_item_lines', 'vendor_credit_item_line', 'document_line_id'),
    ('posting_batches', 'posting_batch', 'id'),
    ('posting_lines', 'posting_line', 'id'),
    ('posting_line_sources', 'posting_line_source', 'id'),
    ('ap_source_keys', 'ap_source_key', 'id'),
    ('ap_source_components', 'ap_source_component', 'id'),
)

# Attaching and detaching write settlement history and nothing else: no revision, no posting.
SETTLEMENT_TABLE_KINDS = (('ap_applications', 'ap_application', 'id'),)

# A correction writes both: the new revision's whole graph, and the settlement edges that
# release the superseded revision's capacity and take the same settlements again out of the
# new one. The edges go last because each names a component this same write just minted.
UPDATE_TABLE_KINDS = TABLE_KINDS + SETTLEMENT_TABLE_KINDS


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


LINE_FACTS = {'expense': BillExpenseProfile, 'item': BillItemProfile}


def merged_line(envelope, profile, family='expense'):
    """One credited line as a reader sees it: the envelope's identity, the profile's money.

    Both rows carry ``account_id`` and ``amount_minor_units``, and on a purchase envelope both
    are null -- the accounting lives in the profile. Spelling the overlap out rather than
    merging blindly is what keeps a line's account from silently reading as null. An item row
    adds what the Items tab has and the Expenses tab does not, the bill's own columns: the
    item, the quantity and the unit cost the amount came from, and the billable mark.
    """
    merged = dict(envelope, family=family, memo=envelope['description'],
                  account_id=profile['account_id'],
                  amount_minor_units=profile['amount_minor_units'],
                  customer_id=profile['customer_id'], line_snapshot=profile['line_snapshot'])
    if family == 'item':
        merged.update(item_id=profile['item_id'], billable=bool(profile['billable']),
                      quantity_microunits=profile['quantity_microunits'],
                      quantity=format_quantity_micro_units(profile['quantity_microunits']),
                      unit_cost_minor_units=profile['unit_cost_minor_units'])
    return merged


def line_profiles(s, revision):
    """Every stored line profile of a revision, by envelope id, with the family that owns it."""
    found = {}
    for family, table in (('expense', c.vendor_credit_expense_lines), ('item', c.vendor_credit_item_lines)):
        for row in effects.rows(s, table, table.c.revision_id == revision['id']):
            found[row['document_line_id']] = (family, row)
    return found


def saved_lines(s, revision):
    envelopes = effects.rows(s, c.document_lines, c.document_lines.c.revision_id == revision['id'],
                             order=c.document_lines.c.position)
    profiles = line_profiles(s, revision)
    return [merged_line(envelope, profiles[envelope['id']][1], profiles[envelope['id']][0])
            for envelope in envelopes]


def by_family(lines, family):
    return [line for line in lines if line['family'] == family]


# ---------------------------------------------------------------- what a credit captures


def _supplied(inp, field):
    return field in inp.model_fields_set


def _ownership(previous, field, found):
    """A correction may not move the credit onto another vendor, payable or currency.

    The settlement source is minted once and permanently carries that triple; every
    application is checked against it by the storage trigger and by ``_compatible``. A
    correction that changed any of the three would leave the credit saying one thing while its
    capacity could still only answer the other -- so the three are guards here, in the same
    words ``customer-refund update`` refuses paying somebody else back.
    """
    raise BookflowError('E_APPLICATION_INCOMPATIBLE', details={
        'reason': 'vendor_credit_ownership', 'credit_id': previous['header']['id'],
        'field': field, field + '_id': found,
        'next': 'A correction keeps the vendor, payable account and currency of the credit it '
                'corrects; a credit from another vendor, or against another payable, is a '
                'different credit and is entered as one.'})


def resolve_header(s, inp, previous=None):
    """Every header fact a vendor-credit revision captures, and where each one came from.

    The bill's resolution minus terms and a due date: a credit is not owed on a date. The
    vendor, the payable account, the account rules and the class come from the bill's own
    resolvers, so a credit can never be owed out of an account a bill could not be owed out of.

    One reader for both writes. A new credit has nothing to fall back on and every field is
    the caller's; a correction keeps the captured value -- and the captured origin -- of every
    field the caller left out, which is how a wrong date or memo alone is corrected without
    quietly restating where the payable account came from.
    """
    info = bills._info(s)
    currency = info['home_currency']
    captured = previous['profile'] if previous else None
    origins = dict(captured.origins) if captured else {}

    row = resolve_party(s.company, 'vendor', inp.vendor or (captured.vendor.id if captured else None))
    # A vendor deactivated after the credit was written must not block correcting its memo:
    # the correction cannot move the credit onto another vendor anyway.
    if captured is None and not row['active']:
        raise BookflowError('E_INACTIVE_REFERENCE',
                            details={'record_type': 'vendor', 'record_id': row['id'], 'field': 'vendor'})
    if captured is not None and row['id'] != captured.vendor.id:
        _ownership(previous, 'vendor', row['id'])
    vendor = Vendor(**bills._reference(row).model_dump(),
                    **{k: row.get(k) for k in ('company_name', 'email', 'phone', 'account_number')})
    origins['vendor'] = Origin(kind='explicit')

    ap_account, ap_origin = bills._ap_account(s, inp.ap_account, currency, captured,
                                              noun='vendor credit')
    if captured is None:
        origins['ap_account'] = ap_origin
    elif ap_account.id != captured.ap_account.id:
        _ownership(previous, 'ap_account', ap_account.id)
    # On a correction the captured origin stands whether or not the account was named: the
    # field is a guard rather than a choice, so naming the account the credit already has must
    # not restate a company default as something a person typed -- which would turn a save
    # nobody changed into a reversal batch and a replacement batch.

    if captured is None or _supplied(inp, 'supplier_reference'):
        reference = (inp.supplier_reference or '').strip() or None
        if inp.supplier_reference is not None or captured is not None:
            origins['supplier_reference'] = Origin(kind='explicit')
    else:
        reference = captured.supplier_reference

    if captured is None or _supplied(inp, 'class_id'):
        klass = (bills._reference(bills._list_row(s, 'class', c.classes, inp.class_id, 'class_id', 'class'))
                 if inp.class_id else None)
        if inp.class_id is not None or captured is not None:
            origins['class_id'] = Origin(kind='explicit')
    else:
        klass = captured.class_id

    if previous:
        issuer = json.loads(previous['revision']['issuer_snapshot'])
    else:
        issuer = {key: value for key, value in info.items()
                  if key in ('id', 'legal_name', 'home_currency')
                  or key.startswith(('address_', 'legal_address_'))}
    return dict(vendor=vendor, ap_account=ap_account, supplier_reference=reference,
                supplier_reference_key=reference_key(reference), class_id=klass,
                currency=currency, origins=origins, issuer=issuer)


def _credited_grid(s, inp, header, previous):
    """The credited rows this revision carries, grid by grid, and the identity each one keeps.

    Supplying ``expenses`` or ``items`` replaces that grid outright; leaving one out on a
    correction keeps every captured row of it exactly as it was written, down to the account
    name the credit was entered under, so correcting the date cannot silently re-resolve a
    renamed account or an item since repointed. The grids are resolved by the bill's own
    resolvers: an item row is written by the same code that writes a bill's.
    """
    currency = header['currency']
    old_lines = saved_lines(s, previous['revision']) if previous else []
    seen, grids = set(), {}
    for family, supplied, resolver in (('expense', inp.expenses, bills._expense_line),
                                       ('item', inp.items, bills._item_line)):
        kept = by_family(old_lines, family)
        if previous and supplied is None:
            grids[family] = [dict(line_id=line['line_id'], family=family, memo=line['memo'],
                                  amount_minor_units=line['amount_minor_units'],
                                  profile=LINE_FACTS[family].model_validate_json(line['line_snapshot']))
                             for line in kept]
            seen.update(line['line_id'] for line in kept)
            continue
        collection = 'items' if family == 'item' else 'expenses'
        prior, resolved_lines = {line['line_id'] for line in kept}, []
        for index, line in enumerate(supplied or []):
            key = line.line_id.upper() if line.line_id and is_ulid(line.line_id) else line.line_id
            # A retired identity cannot return, no identity may name two rows, and an identity
            # cannot cross tabs: the row a reader follows through the history has to stay one row.
            if key is not None and (key not in prior or key in seen):
                raise _invalid(collection + '.line_id',
                               'use a unique current line identity from this credit; retired '
                               'identities cannot return and a row cannot change grid')
            if key:
                seen.add(key)
            resolved = (resolver(s, line, header['class_id'], currency, index, noun='vendor credit')
                        if family == 'item' else
                        resolver(s, line, header['class_id'], currency, index))
            resolved['line_id'] = key
            resolved_lines.append(resolved)
        grids[family] = resolved_lines
    return grids


def _income_warning(profile):
    return (f'"{profile.item.label}" has no purchase account, so its income account '
            f'"{profile.account.full_name}" is being used. Returning something you sell raises '
            'that income rather than giving back a cost. Give the item a purchase description '
            'and an expense account to credit a cost account instead.')


def commercial(s, inp, previous=None):
    """Resolve the whole credit: header, lines, total and the number it takes."""
    header = resolve_header(s, inp, previous)
    currency = header['currency']
    old_revision = previous['revision'] if previous else None
    date = (inp.date or old_revision['date']) if old_revision else inp.date
    memo = inp.memo if previous is None or _supplied(inp, 'memo') else old_revision['memo']
    grids = _credited_grid(s, inp, header, previous)
    # Expenses first, then items: the order of the two tabs on the document itself.
    lines = grids['expense'] + grids['item']
    if not lines:
        raise _invalid('items' if inp.items is not None and inp.expenses is None else 'expenses',
                       'a vendor credit needs at least one expense row or item row')
    for line in lines:
        line.setdefault('line_id', None)
        if line['amount_minor_units'] <= 0:
            raise _invalid('items' if line['family'] == 'item' else 'expenses',
                           'every credited row must be worth more than nothing')
    expense_total = checked_sum((line['amount_minor_units'] for line in grids['expense']), 'expenses.total')
    item_total = checked_sum((line['amount_minor_units'] for line in grids['item']), 'items.total')
    total = checked_sum((expense_total, item_total), 'total')
    number, sequence = effects.allocate(
        s, DOCUMENT_TYPE,
        inp.number if inp.number is not None else (old_revision['number'] if previous else None),
        previous['header']['id'] if previous else None)
    profile = VendorCreditProfile(
        vendor=header['vendor'], ap_account=header['ap_account'],
        supplier_reference=header['supplier_reference'],
        supplier_reference_key=header['supplier_reference_key'], class_id=header['class_id'],
        expense_total_minor_units=expense_total, item_total_minor_units=item_total,
        currency=currency, origins=header['origins'])
    return dict(profile=profile, lines=lines, total=total, expense_total=expense_total,
                item_total=item_total, currency=currency, number=number,
                sequence=sequence, issuer=header['issuer'], memo=memo, date=date,
                warnings=list(dict.fromkeys(
                    _income_warning(line['profile']) for line in lines
                    if getattr(line['profile'], 'account_basis', 'purchase') == 'income')))


# ---------------------------------------------------------------- what changed, and whether anything did


def _line_semantic(line):
    return {'line_id': line['line_id'], 'family': line['family'], 'memo': line['memo'],
            'amount_minor_units': line['amount_minor_units'],
            'profile': line['profile'].model_dump(mode='json')}


def saved_semantic(s, revision):
    """What the stored revision says about itself, in the shape a fresh resolution says it."""
    stored = profile_row(s, revision)
    return dict(
        date=revision['date'], number=revision['number'], memo=revision['memo'],
        profile=VendorCreditProfile.model_validate_json(stored['profile_snapshot']).model_dump(mode='json'),
        lines=[_line_semantic(dict(line, profile=LINE_FACTS[line['family']].model_validate_json(
            line['line_snapshot']))) for line in saved_lines(s, revision)])


def resolved_semantic(resolved):
    return dict(date=resolved['date'], number=resolved['number'], memo=resolved['memo'],
                profile=resolved['profile'].model_dump(mode='json'),
                lines=[_line_semantic(line) for line in resolved['lines']])


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
                item_total_minor_units=profile['item_total_minor_units'],
                total_minor_units=revision['total_minor_units'],
                expense_total=Money(profile['expense_total_minor_units'], currency).to_dict(),
                item_total=Money(profile['item_total_minor_units'], currency).to_dict(),
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


def revision_output(s, header, revision, edges, pending=None, *, summary_only=False):
    """One revision as a reader sees it, or its header alone when a history page asks.

    ``summary_only`` is what ``vendor-credit history`` pages: everything the revision says
    about itself apart from the grid and the captured facts, so a document with two hundred
    credited rows pages its revisions without loading two hundred rows per revision. The
    applications it reports are the ones hung on that revision's own capacity, which is why a
    superseded revision reads as settling nothing: a correction released every edge it held
    and the corrected revision took them again.
    """
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
    if summary_only:
        lines = envelopes
        line_count = len(lines) if lines else s.company.conn.execute(
            sa.select(sa.func.count()).select_from(c.document_lines).where(
                c.document_lines.c.revision_id == revision['id'])).scalar_one()
    elif envelopes:
        details = {row['document_line_id']: (family, row) for family, table in
                   (('expense', 'vendor_credit_expense_lines'), ('item', 'vendor_credit_item_lines'))
                   for row in pending[table]}
        lines = [merged_line(line, details[line['id']][1], details[line['id']][0])
                 for line in envelopes]
    else:
        lines = saved_lines(s, revision)
    profile = next((row for row in pending.get('vendor_credit_profiles', [])
                    if row['revision_id'] == revision['id']), None) or profile_row(s, revision)
    values = {k: v for k, v in revision.items() if not k.endswith('_snapshot')}
    values.update(expense_total_minor_units=profile['expense_total_minor_units'],
                  expense_total=Money(profile['expense_total_minor_units'], currency).to_dict(),
                  item_total_minor_units=profile['item_total_minor_units'],
                  item_total=Money(profile['item_total_minor_units'], currency).to_dict(),
                  total=Money(revision['total_minor_units'], currency).to_dict(),
                  line_count=line_count if summary_only else len(lines), batches=summaries,
                  applications=bill_payments.application_outputs(mine, currency, numbers))
    if summary_only:
        return VendorCreditRevisionSummaryOutput(**values)
    return VendorCreditRevisionOutput(
        **values, profile=json.loads(profile['profile_snapshot']),
        issuer_snapshot=json.loads(revision['issuer_snapshot']),
        source=_source_output(s, header, revision, edges, pending),
        expenses=[VendorCreditExpenseOutput(
            **dict(line, amount=Money(line['amount_minor_units'], currency).to_dict(),
                   line_snapshot=json.loads(line['line_snapshot'])))
            for line in by_family(lines, 'expense')],
        items=[VendorCreditItemOutput(
            **dict(line, amount=Money(line['amount_minor_units'], currency).to_dict(),
                   unit_cost=None if line['unit_cost_minor_units'] is None
                   else Money(line['unit_cost_minor_units'], currency).to_dict(),
                   line_snapshot=json.loads(line['line_snapshot'])))
            for line in by_family(lines, 'item')])


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


def history(s, ctx, inp):
    """Every immutable revision of one credit, oldest first, the way a credit memo pages its own.

    The current header rides along -- number, status and the version a correction has to
    supply -- so a reader can tell at a glance which of the revisions listed is the one the
    books are standing on now.
    """
    from bookflow.company.query import continuation, page_state

    class Contract:
        cursor = inp.cursor
        query = None

        def model_dump(self, **kw):
            return inp.model_dump(**kw)

    state = page_state(s, 'vendor-credit history', Contract(), ctx.on_behalf_of)
    header = resolve(s, inp.credit)
    edges = bill_payments._edges(s, header)
    query = sa.select(c.transaction_revisions).where(
        c.transaction_revisions.c.transaction_id == header['id']).order_by(
        c.transaction_revisions.c.revision_number)
    found = [dict(row) for row in s.company.conn.execute(
        query.offset(state.offset).limit(inp.limit + 1)).mappings()]
    more, found = len(found) > inp.limit, found[:inp.limit]
    return VendorCreditHistoryOutput(
        **{k: header[k] for k in ('id', 'version', 'current_revision_id', 'number', 'status')},
        items=[revision_output(s, header, revision, edges, summary_only=True) for revision in found],
        count=len(found), has_more=more, next_cursor=continuation(state, len(found), more),
        audit_watermark=state.sequence)


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


def _business_postings(s, header, revision, batch, resolved, pending, created, event, *, source_key=None):
    """Cr each credited account its own line; Dr Accounts Payable the total, once.

    The mirror of the bill: the payable falls by one figure because that is what the vendor
    now owes less, and each entered line's share of it is an attribution row on that debit
    rather than a leg of its own. Those attribution rows are what the source components name,
    which is how an application can later land on particular credited lines.

    Nothing here asks which tab a row came from. An expense row's account was typed and an item
    row's was read off the item, but by the time both are stored each is one captured account and
    one positive amount, so both take exactly one credit leg and one component. A stocked item's
    account is Inventory Asset: that leg is the one its ``vendor_return`` movement is bound to,
    so it is returned keyed by entered line for the caller to bind.
    """
    profile = resolved['profile']
    currency = revision['currency']
    envelopes = [row for row in pending['document_lines'] if row['revision_id'] == revision['id']]
    profiles = {row['document_line_id']: (family, row) for family, table in
                (('expense', 'vendor_credit_expense_lines'), ('item', 'vendor_credit_item_lines'))
                for row in pending[table]}
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

    credits = {}
    for envelope in envelopes:
        family, line = profiles[envelope['id']]
        facts = LINE_FACTS[family].model_validate_json(line['line_snapshot'])
        given_back = leg(facts.account, line['amount_minor_units'], False,
                         envelope['class_id'], envelope['class_name'], envelope['description'])
        credits[envelope['id']] = given_back
        attribute(given_back, envelope, line['amount_minor_units'])
    payable = leg(profile.ap_account, resolved['total'], True,
                  profile.class_id.id if profile.class_id else None,
                  profile.class_id.label if profile.class_id else None, resolved['memo'])
    # The settlement source is minted once and never again: it is the permanent identity of
    # what this credit can settle, and a correction hangs the new revision's capacity off the
    # same one rather than starting a second store of money on one document.
    if source_key is None:
        source_key = dict(**created(), transaction_id=header['id'], ordinal=1,
                          source_type=DOCUMENT_TYPE, vendor_id=profile.vendor.id,
                          ap_account_id=profile.ap_account.id, currency=currency, audit_event_id=event)
        pending['ap_source_keys'].append(source_key)
    for envelope in envelopes:
        line = profiles[envelope['id']][1]
        attribution = attribute(payable, envelope, line['amount_minor_units'])
        pending['ap_source_components'].append(dict(
            **created(), transaction_id=header['id'], revision_id=revision['id'],
            key_id=source_key['id'], document_line_id=envelope['id'], ordinal=1,
            posting_source_id=attribution['id'], amount_minor_units=line['amount_minor_units'],
            currency=currency, audit_event_id=event))
    return credits


def _stock_entries(pending, profile):
    """The item rows that send stock back, in the shape the inventory ledger takes them.

    What leaves the shelf is read off the captured facts, not off the item master, so
    repointing an item afterwards cannot change which rows of a stored revision moved stock.
    """
    items = {row['document_line_id']: row for row in pending['vendor_credit_item_lines']}
    entries = []
    for envelope in pending['document_lines']:
        line = items.get(envelope['id'])
        if line is None:
            continue
        entry = inventory_effects.vendor_return_entry(
            BillItemProfile.model_validate_json(line['line_snapshot']), key=envelope['id'],
            amount=line['amount_minor_units'], offset_account_id=profile.ap_account.id,
            class_id=envelope['class_id'])
        if entry is not None:
            entries.append(entry)
    return entries


def _has_applications(s, header):
    return bool(ap_settlement.active_applications(s, header['id']))


def _unchanged_plan(s, inp, previous):
    """Nothing to correct: the saved credit, said back, with no revision written."""
    return Plan(_output(s, previous['header'], previous['revision'],
                        model=VendorCreditWriteOutput, changed=False, changed_fields=[]),
                dict(input=inp, operation='update', changed=False))


def _for_correction(s, inp):
    """The credit a correction is written against, or the named reason it cannot be."""
    header = resolve(s, inp.credit)
    if inp.expected_version is not None:
        journals.version_meta(s, header, inp.expected_version)
    if header['status'] != 'posted':
        raise BookflowError('E_APPLICATION_INACTIVE', details={
            'credit_id': header['id'], 'status': header['status'],
            'next': 'A voided credit gave nothing back; enter the corrected credit instead.'})
    revision = journals.revision(s, header)
    stored = profile_row(s, revision)
    return dict(header=header, revision=revision, profile_row=stored,
                profile=VendorCreditProfile.model_validate_json(stored['profile_snapshot']))


def _retake_applications(s, ctx, header, resolved, pending, at, event):
    """Release every bill this credit answers, then answer the same bills again, exactly.

    An application names one *revision-local* component, and a correction retires the whole of
    the superseded revision's capacity. Left alone, the standing edges would still hold bills
    settled against capacity the current revision no longer has, while ``_free_capacity`` --
    which reads the current revision's components -- reported the corrected credit wholly
    free: the same credit spendable twice, and bills whose open balance depends on which of
    the two readers you ask. So each standing application is reversed cell for cell and the
    same bill is settled again for the same amount on the same date out of the corrected
    revision's components.

    What every bill owes is therefore unchanged by a correction. That is also why the
    settlement dates are not re-tested against the closing date the way ``unapply`` tests
    them: an unapply changes what the books said was open on that date and this does not move
    it by a cent.
    """
    active = ap_settlement.active_applications(s, header['id'])
    if not active:
        return
    currency = resolved['currency']
    source = source_row(s, header['id'])
    if source is None:
        raise BookflowError('E_INTERNAL', message='This credit carries no settlement source')

    def edge(**values):
        pending['ap_applications'].append(dict(
            id=new_id(), created_at=at, created_by=s.actor.id, created_via=ctx.interface.value,
            audit_event_id=event, source_transaction_id=header['id'],
            source_key_id=source['id'], currency=currency, **values))

    for row in active:
        edge(kind='unapply', reverses_application_id=row['id'],
             **{key: row[key] for key in ('source_component_id', 'obligation_transaction_id',
                                          'obligation_key_id', 'amount_minor_units',
                                          'effective_date')})
    # One settlement per bill and date, in the order the credit answered them, because which
    # credited line supplied which cent is this write's to decide again and what the bill owes
    # is not.
    wanted, index = [], {}
    for row in active:
        key = (row['obligation_transaction_id'], row['obligation_key_id'], row['effective_date'])
        if key not in index:
            index[key] = len(wanted)
            wanted.append([key, 0])
        wanted[index[key]][1] += row['amount_minor_units']
    settled = checked_sum((units for _, units in wanted), 'applications.total')
    earliest = min(date for (_, _, date), _ in wanted)
    if earliest < resolved['date']:
        raise _invalid('date', f'this credit already answers a bill on {earliest}; a correction '
                               f'dated {resolved["date"]} would settle a bill before the credit '
                               f'existed. Unapply that settlement first, or keep the credit on '
                               f'or before {earliest}.')
    supply = [[row, row['amount_minor_units']] for row in pending['ap_source_components']]
    available = checked_sum((units for _, units in supply), 'capacity.total')
    if settled > available:
        raise BookflowError('E_APPLICATION_CAPACITY', details={
            'credit_id': header['id'], 'credit_number': header['number'],
            'requested_minor_units': settled, 'available_minor_units': available,
            'requested': Money(settled, currency).to_dict(),
            'available': Money(available, currency).to_dict(),
            'next': 'This credit already answers more than the correction would be worth. '
                    'Unapply what it holds down to the corrected amount first, then correct it.'})
    position = 0
    for (obligation_transaction_id, obligation_key_id, date), units in wanted:
        remaining = units
        while remaining:
            component, free = supply[position]
            if not free:
                position += 1
                continue
            taken = min(free, remaining)
            supply[position][1] -= taken
            remaining -= taken
            edge(kind='apply', reverses_application_id=None,
                 source_component_id=component['id'],
                 obligation_transaction_id=obligation_transaction_id,
                 obligation_key_id=obligation_key_id, amount_minor_units=taken,
                 effective_date=date)


def _compose(s, ctx, inp, resolved, previous=None):
    """The whole graph one write stores: the revision, its effect, and the capacity it carries.

    One builder for the first credit and for every correction of it. A correction adds three
    things and changes nothing else: the superseded batch reversed at its own date, a
    replacement posted at the corrected one, and the settlements released and retaken so the
    credit is never worth two different figures in between.
    """
    journals.open_dates(s, [resolved['date']] + ([previous['revision']['date']] if previous else []))
    bills._posting_accounts_active(s, dict(profile=resolved['profile'], lines=resolved['lines']))
    at, event = clock.now_iso(), new_id()

    def created():
        return dict(id=new_id(), created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)

    row_provenance = dict(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)
    pending = {table: [] for table, _, _ in (UPDATE_TABLE_KINDS if previous else TABLE_KINDS)}
    currency = resolved['currency']
    if previous:
        old_header, old_revision = previous['header'], previous['revision']
        header = dict(old_header, version=old_header['version'] + 1, updated_at=at,
                      updated_by=s.actor.id, updated_via=ctx.interface.value)
    else:
        old_header = old_revision = None
        header = dict(id=new_id(), **common(s.actor.id, ctx.interface.value, at), type=DOCUMENT_TYPE,
                      status='posted', voided_at=None, voided_by=None, void_reason=None,
                      void_posting_batch_id=None)
    revision = dict(**created(), transaction_id=header['id'],
                    revision_number=old_revision['revision_number'] + 1 if previous else 1,
                    supersedes_revision_id=old_revision['id'] if previous else None,
                    date=resolved['date'], number=resolved['number'],
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
        expense_total_minor_units=resolved['expense_total'],
        item_total_minor_units=resolved['item_total'],
        profile_snapshot=json_text(profile.model_dump())))
    for position, line in enumerate(resolved['lines'], 1):
        # A row carried over from the superseded revision keeps its identity, so the row a
        # reader follows through the history is the same row; a row entered here mints one.
        identity = line.get('line_id')
        if identity is None:
            minted = dict(**created(), transaction_id=header['id'])
            pending['document_line_identities'].append(minted)
            identity = minted['id']
        facts = line['profile']
        envelope = dict(**created(), transaction_id=header['id'], revision_id=revision['id'],
                        line_id=identity, position=position, kind='purchase', account_id=None,
                        side=None, amount_minor_units=None, currency=currency, account_snapshot=None,
                        name_type='vendor', name_id=profile.vendor.id, party_name=profile.vendor.label,
                        class_id=facts.class_id.id if facts.class_id else None,
                        class_name=facts.class_id.label if facts.class_id else None,
                        description=line['memo'], **dict.fromkeys(journals.FACTS))
        pending['document_lines'].append(envelope)
        shared = dict(
            document_line_id=envelope['id'], transaction_id=header['id'], revision_id=revision['id'],
            **row_provenance, account_id=facts.account.id,
            amount_minor_units=line['amount_minor_units'],
            customer_id=facts.customer.id if facts.customer else None,
            line_snapshot=json_text(facts.model_dump()))
        if line['family'] == 'item':
            pending['vendor_credit_item_lines'].append(dict(
                shared, item_id=facts.item.id, quantity_microunits=facts.quantity_microunits,
                unit_cost_minor_units=facts.unit_cost_minor_units, billable=facts.billable))
        else:
            pending['vendor_credit_expense_lines'].append(shared)
    # What this write does to stock, decided in full before any of it is built: the previous
    # revision's returns are retired, the new revision's are taken, and the corrections every
    # later issue is owed -- its average moved -- are worked out against the item's whole
    # history. A refusal here (more sent back than is on hand, a closed period, value left
    # behind) leaves nothing behind, because nothing has been written.
    stock = inventory_effects.plan(
        s, entries=_stock_entries(pending, profile),
        reversing=inventory_effects.own_movements(s, old_header['id']) if previous else (),
        date=revision['date'], currency=currency, field='items')
    inventory_effects.open_dates(s, stock)
    old_batch = source_key = None
    if previous:
        batches = effects.rows(s, c.posting_batches,
                               c.posting_batches.c.revision_id == old_revision['id'],
                               c.posting_batches.c.kind != 'reversal')
        if len(batches) != 1:
            raise BookflowError('E_VALIDATION',
                                message='Vendor credit has ambiguous current posting evidence.')
        old_batch = batches[0]
        effects.reverse(s, header, old_revision, old_batch, event, created, pending)
        source_key = source_row(s, header['id'])
        if source_key is None:
            raise BookflowError('E_INTERNAL', message='This credit carries no settlement source')
    batch = dict(**created(), transaction_id=header['id'], revision_id=revision['id'],
                 kind='replacement' if previous else 'original', effective_date=revision['date'],
                 reverses_batch_id=None,
                 replaces_batch_id=old_batch['id'] if old_batch else None, audit_event_id=event)
    pending['posting_batches'].append(batch)
    credits = _business_postings(s, header, revision, batch, resolved, pending, created, event,
                                 source_key=source_key)
    for movement in stock.movements:
        if movement.key is not None:
            inventory_effects.bind(movement, credits.get(movement.key), transaction_id=header['id'],
                                   revision_id=revision['id'], document_line_id=movement.key,
                                   batch=batch)
    inventory_effects.bind_reversals(stock, pending['posting_lines'], pending['posting_batches'])
    inventory_effects.check(s, stock, pending['posting_lines'])
    if previous:
        _retake_applications(s, ctx, header, resolved, pending, at, event)
    changed_fields = (bills._changes(saved_semantic(s, old_revision), resolved_semantic(resolved))
                      if previous else [])
    plan = Plan(_output(s, header, revision, pending, model=VendorCreditWriteOutput,
                        changed_fields=changed_fields, warnings=resolved['warnings']),
                dict(input=inp, operation='update' if previous else 'post', changed=True,
                     header=header, before=old_header, old_revision=old_revision, pending=pending,
                     sequence=resolved['sequence'], event=event, resolved=resolved,
                     previous=previous, stock=stock))
    from bookflow.company.vendor_credit_validation import validate
    validate(plan, s, ctx)
    return plan


def prepare_void(s, ctx, inp):
    old_header = resolve(s, inp.credit)
    old_revision = journals.revision(s, old_header)
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

    pending = {table: [] for table, _, _ in TABLE_KINDS}
    if old_header['status'] == 'voided':
        return Plan(_output(s, old_header, old_revision, model=VendorCreditWriteOutput, changed=False),
                    dict(input=inp, operation='void', changed=False))
    journals.open_dates(s, [old_revision['date']])
    header = dict(old_header)
    current_batch = effects.rows(s, c.posting_batches,
                                 c.posting_batches.c.revision_id == old_revision['id'],
                                 c.posting_batches.c.kind != 'reversal')[0]
    inverse = effects.reverse(s, header, old_revision, current_batch, event, created, pending)
    # What came off the shelf goes back on it. The stock the credit sent away is returned at
    # the value it left at, and every later issue is owed the correction its average implies.
    stock = inventory_effects.plan(
        s, entries=[], reversing=inventory_effects.own_movements(s, old_header['id']),
        date=old_revision['date'], currency=old_revision['currency'], field='items')
    inventory_effects.open_dates(s, stock)
    inventory_effects.bind_reversals(stock, pending['posting_lines'], pending['posting_batches'])
    inventory_effects.check(s, stock, pending['posting_lines'])
    header.update(version=old_header['version'] + 1, updated_at=at, updated_by=s.actor.id,
                  updated_via=ctx.interface.value, status='voided', voided_at=at,
                  voided_by=s.actor.id, void_reason=ctx.reason.strip(),
                  void_posting_batch_id=inverse['id'])
    plan = Plan(_output(s, header, old_revision, pending, model=VendorCreditWriteOutput,
                        changed_fields=['status']),
                dict(input=inp, operation='void', changed=True, header=header, before=old_header,
                     old_revision=old_revision, pending=pending, sequence=None, event=event,
                     resolved=None, stock=stock))
    from bookflow.company.vendor_credit_validation import validate
    validate(plan, s, ctx)
    return plan


def prepare_update(s, ctx, inp):
    """Correct a posted vendor credit with another revision, or say why it cannot be."""
    previous = _for_correction(s, inp)
    # An empty patch has no accounting effect and does not create another revision.
    if not (inp.model_fields_set - {'credit', 'expected_version'}):
        return _unchanged_plan(s, inp, previous)
    resolved = commercial(s, inp, previous)
    if resolved_semantic(resolved) == saved_semantic(s, previous['revision']):
        return _unchanged_plan(s, inp, previous)
    # A correction always releases and retakes capacity that bills are standing on, so it
    # always says why, the way every other write that moves somebody else's residue does.
    if not ctx.reason or not ctx.reason.strip():
        raise BookflowError('E_REASON_REQUIRED')
    if len(ctx.reason.strip()) > 140:
        raise _invalid('reason', 'must be at most 140 characters')
    return _compose(s, ctx, inp, resolved, previous)


def prepare_write(s, ctx, inp, operation):
    """``post``, ``update`` and ``void``: the three moments a vendor credit changes the ledger."""
    if operation == 'void':
        return prepare_void(s, ctx, inp)
    if operation == 'update':
        return prepare_update(s, ctx, inp)
    return _compose(s, ctx, inp, commercial(s, inp))


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


def table_kinds_for(operation):
    if operation == 'update':
        return UPDATE_TABLE_KINDS
    return SETTLEMENT_TABLE_KINDS if operation in ('apply', 'unapply') else TABLE_KINDS


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
    if operation in ('post', 'void', 'update'):
        applied = effects.persist(fresh, ctx, s, command_name=command_name,
                                  table_kinds=table_kinds_for(operation))
        # The dated cost corrections this return owes later issues: their own documents, at
        # their own dates, in this same company transaction.
        stock = data.get('stock')
        if stock is None or not stock.moves_stock:
            return applied
        return inventory_effects.settle(applied, stock, ctx, s, command_name=command_name,
                                        summary=f"stock moved by vendor credit {data['header']['number']}",
                                        created_at=data['header']['updated_at'])
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
