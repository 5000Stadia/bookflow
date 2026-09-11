"""Credit memos: a sale read backwards, and the credit it leaves behind.

Two documents in one noun, distinguished by what a line claims rather than by a flag.

A **standalone** credit names its own items and prices them exactly as an invoice line is
priced, through the same resolver and the same tax calculator; a **returned** credit names a
source invoice line and prices nothing at all -- every net and tax cent is taken from what that
invoice captured, by the endpoint rule in ``credit_returns``, so changing an item's price or a
tax item's rate afterwards moves no cent of an issued credit. One document is all of one kind:
the tax calculator rounds across a whole document, so a document holding both a calculated cell
and a captured one would carry a tax total that is neither.

The accounting is the invoice's, the other way round: each line debits its captured income
account for its net and its captured tax liabilities for their cells, and Accounts Receivable
is credited the gross once. That single credit is what the customer is owed, and each line's
share of it is an attribution row on it -- which is exactly what ``credit_components`` name, so
a later application can land on particular lines. Saving a credit applies nothing: it posts the
reversal and leaves capacity standing.
"""
from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa

from bookflow.company import credit_returns as returns
from bookflow.company import document_effects as effects
from bookflow.company import journal_custom_fields as custom
from bookflow.company import journals, schema as c
from bookflow.company import sales_calculations as calc
from bookflow.company import tax_attribution as tax_facts
from bookflow.company.credit_models import (
    CreditClaimOutput, CreditLineOutput, CreditMemoHistoryOutput, CreditMemoOutput,
    CreditMemoWriteOutput, CreditRevisionOutput, CreditRevisionSummaryOutput, CreditSourceOutput,
    CreditTaxComponentOutput,
)
from bookflow.company.sales_facts import SalesLineProfile, SalesProfile, SalesTaxComponent
from bookflow.company.sales_models import SalesLineInput, _invalid
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_quantity_micro_units, parse_quantity_micro_units
from bookflow.core.ids import is_ulid, new_id
from bookflow.core.money import Money
from bookflow.core.registry import Plan
from bookflow.hub.users import common

DOCUMENT_TYPE = 'credit_memo'

# Insert order matters: an attribution row follows the posting line it hangs off, a commercial
# line follows the attribution it names, and a credit component follows both the source key it
# belongs to and the receivable attribution that created it.
TABLE_KINDS = (
    ('transaction_revisions', 'transaction_revision', 'id'),
    ('document_line_identities', 'document_line_identity', 'id'),
    ('document_lines', 'document_line', 'id'),
    ('credit_profiles', 'credit_profile', 'revision_id'),
    ('sales_tax_line_keys', 'sales_tax_line_key', 'line_id'),
    ('posting_batches', 'posting_batch', 'id'),
    ('posting_lines', 'posting_line', 'id'),
    ('posting_line_sources', 'posting_line_source', 'id'),
    ('credit_line_profiles', 'credit_line_profile', 'document_line_id'),
    ('credit_tax_components', 'credit_tax_component', 'id'),
    ('credit_source_keys', 'credit_source_key', 'id'),
    ('credit_components', 'credit_component', 'id'),
    ('credit_source_claims', 'credit_source_claim', 'id'),
)
MONEY_COLUMNS = ('unit_price_minor_units', 'net_minor_units', 'tax_minor_units', 'gross_minor_units')


def json_text(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


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
    return effects.rows(s, c.credit_profiles, c.credit_profiles.c.revision_id == revision['id'])[0]


# ------------------------------------------------------------------ the returned source line


def _source_line(s, invoice_selector, line_selector, profile):
    """The captured source cell a returned line prices itself from, or a typed refusal."""
    from bookflow.company import sales
    header = sales.resolve(s, invoice_selector, 'invoice')
    if header['status'] != 'posted':
        raise _invalid('source_invoice', 'a voided invoice has nothing to return')
    revision = journals.revision(s, header)
    source_profile = sales.profile_row(s, revision)
    # Exact party, exact receivable, exact currency. Moving credit between a parent and a job
    # needs a balanced transfer document, which this release does not have; guessing that the
    # two are interchangeable would credit the wrong account and the wrong customer at once.
    if (source_profile['customer_id'] != profile.customer.id
            or source_profile['control_account_id'] != profile.control_account.id
            or revision['currency'] != s.company.conn.execute(
                sa.select(c.company_info.c.home_currency)).scalar_one()):
        raise _invalid('source_invoice', 'return a line of an invoice for this exact customer, '
                                         'receivable account and currency')
    key = line_selector.upper() if is_ulid(line_selector) else line_selector
    lines = s.company.conn.execute(sa.select(c.document_lines, c.sales_line_profiles).join(
        c.sales_line_profiles, c.sales_line_profiles.c.document_line_id == c.document_lines.c.id).where(
        c.document_lines.c.revision_id == revision['id'], c.document_lines.c.line_id == key)).mappings().all()
    if not lines:
        raise BookflowError('E_RECORD_NOT_FOUND', details={
            'record_type': 'sales_line_profile', 'selector': line_selector,
            'next': 'Read the invoice with `invoice show` and use a current line_id from it.'})
    line = dict(lines[0])
    if line['base_quantity_microunits'] is None or line['base_quantity_microunits'] <= 0:
        raise _invalid('source_line', 'this invoice line carries no quantity to return against')
    taxes = effects.rows(s, c.sales_tax_components,
                         c.sales_tax_components.c.document_line_id == line['document_line_id'],
                         order=c.sales_tax_components.c.id)
    return dict(header=header, revision=revision, line=line, taxes=taxes)


def _active_claims(s, source_transaction_id, source_line_id, pending=None):
    """Every claim on a permanent source line occurrence that no release has undone."""
    claims = c.credit_source_claims
    released = sa.select(claims.c.reverses_claim_id).where(claims.c.kind == 'release')
    rows = effects.rows(s, claims, claims.c.source_transaction_id == source_transaction_id,
                        claims.c.source_line_id == source_line_id, claims.c.kind == 'claim',
                        claims.c.id.notin_(released), order=claims.c.id)
    rows += [row for row in (pending or {}).get('credit_source_claims', [])
             if row['kind'] == 'claim' and row['source_transaction_id'] == source_transaction_id
             and row['source_line_id'] == source_line_id]
    return rows


def _returned(s, entered, source, pending):
    """Price one returned line from the captured source cell and the residue still there."""
    line, taxes = source['line'], source['taxes']
    quantity = int(line['base_quantity_microunits'])
    claims = _active_claims(s, source['header']['id'], line['line_id'], pending)
    for claim in claims:
        if (claim['source_base_quantity_microunits'] != quantity
                or claim['source_net_minor_units'] != line['net_minor_units']):
            raise BookflowError('E_SOURCE_CORRECTION_CONFLICT', details={
                'invoice_id': source['header']['id'], 'line_id': line['line_id'],
                'claimed_base_quantity_microunits': claim['source_base_quantity_microunits'],
                'current_base_quantity_microunits': quantity,
                'next': 'Issue a credit that names its own item instead of returning this line.'})
    wanted = parse_quantity_micro_units(entered.quantity)
    factor = int(line['unit_factor_nanounits'])
    base_wanted = wanted * factor // 1_000_000_000
    if base_wanted <= 0:
        raise _invalid('quantity', 'a returned quantity must be at least one base unit of the source line')
    intervals = returns.take([(row['start_microunits'], row['end_microunits']) for row in claims],
                             quantity, base_wanted)
    money = returns.priced(intervals, quantity, int(line['net_minor_units']), taxes)
    facts = SalesLineProfile.model_validate_json(line['item_snapshot'])
    # The captured facts stand as issued except for how the amount is expressed: a returned
    # interval is an amount, never a price times a quantity, because an endpoint-owned share
    # of a rounded net is not in general divisible by the quantity it covers.
    captured = facts.model_dump()
    captured.update(schema_version=2, pricing_basis='amount', net_amount_minor_units=money['net_minor_units'])
    captured.pop('allocation_proof', None)
    facts = SalesLineProfile.model_validate(captured)
    return dict(item_id=line['item_id'], quantity_microunits=wanted, unit_id=line['unit_id'],
                unit_factor_nanounits=factor, base_quantity_microunits=base_wanted,
                unit_price_minor_units=None, net_minor_units=money['net_minor_units'],
                description=entered.description if 'description' in entered.model_fields_set else line['description'],
                profile=facts, taxes=[dict(rule=None, source=cell, captured=priced)
                                      for cell, priced in zip(taxes, money['taxes'], strict=True)],
                source=dict(transaction_id=source['header']['id'], revision_id=source['revision']['id'],
                            document_line_id=line['document_line_id'], line_id=line['line_id'],
                            base_quantity_microunits=quantity, net_minor_units=int(line['net_minor_units'])),
                intervals=intervals)


# ------------------------------------------------------------------ resolving the whole document


def commercial(s, inp, *, document_id, pending):
    """Resolve original intent without allocating any persisted effect identity."""
    from bookflow.company import sales_defaults
    profile, warnings = sales_defaults.resolve_header(s, inp, DOCUMENT_TYPE)
    number, sequence = effects.allocate(s, DOCUMENT_TYPE, inp.number, document_id)
    info = dict(s.company.conn.execute(sa.select(c.company_info)).mappings().one())
    issuer = {k: v for k, v in info.items() if k in ('id', 'legal_name', 'display_name', 'home_currency')
              or k.startswith(('address_', 'legal_address_', 'ship_address_'))}
    issuer['display_name'] = s.company_row['display_name']
    origin = 'return' if inp.lines[0].source_invoice is not None else 'standalone'
    lines, sources = [], {}
    for entered in inp.lines:
        if origin == 'return':
            if entered.source_invoice not in sources:
                sources[entered.source_invoice] = {}
            found = sources[entered.source_invoice].get(entered.source_line)
            if found is None:
                found = _source_line(s, entered.source_invoice, entered.source_line, profile)
                sources[entered.source_invoice][entered.source_line] = found
            resolved = _returned(s, entered, found, pending)
            resolved['claim_rows'] = [
                dict(id=new_id(), kind='claim', source_transaction_id=resolved['source']['transaction_id'],
                     source_line_id=resolved['source']['line_id'], start_microunits=start, end_microunits=end,
                     source_base_quantity_microunits=resolved['source']['base_quantity_microunits'],
                     source_net_minor_units=resolved['source']['net_minor_units'])
                for start, end in resolved['intervals']]
            # Appended now so the next returned line of the same document sees this one's
            # claims as taken; a second line of one credit cannot return the same unit twice.
            pending['credit_source_claims'].extend(resolved['claim_rows'])
            resolved['tax_ordinal'] = None
            lines.append(resolved)
        else:
            supplied = {name: getattr(entered, name) for name in entered.model_fields_set
                        if name in SalesLineInput.model_fields}
            supplied.setdefault('item', entered.item)
            supplied.setdefault('quantity', entered.quantity)
            resolved, line_warnings = sales_defaults.resolve_line(
                s, SalesLineInput(**supplied), profile, defer_tax=True)
            resolved['source'] = None
            resolved['intervals'] = ()
            resolved['claim_rows'] = []
            lines.append(resolved)
            warnings.extend(line_warnings)
    attribution = None
    ordinals, tax_keys = tax_facts.prospective(s.company, document_id, [None] * len(lines))
    if origin == 'standalone':
        attribution = tax_facts.calculate(lines, profile, info['home_currency'], ordinals)
        tax_facts.apply(lines, attribution, ordinals)
    else:
        for line, ordinal in zip(lines, ordinals, strict=True):
            tax = calc.total((cell['captured']['tax_minor_units'] for cell in line['taxes']), 'line.tax')
            line.update(tax_ordinal=ordinal, tax_minor_units=tax,
                        gross_minor_units=calc.total((line['net_minor_units'], tax), 'line.gross'))
    custom.validate_kinds(s.company, inp.custom_fields, inp.custom_field_kinds, record_type=DOCUMENT_TYPE)
    custom_plan = custom.prepare(s.company, document_id, inp.custom_fields, {}, creating=True,
                                refresh=inp.refresh_defaults, record_type=DOCUMENT_TYPE)
    subtotal = calc.total((line['net_minor_units'] for line in lines), 'subtotal')
    tax = calc.total((line['tax_minor_units'] for line in lines), 'tax')
    total = calc.total((subtotal, tax))
    if total <= 0:
        raise _invalid('total', 'a posted credit memo must have a positive total')
    semantic = dict(date=inp.date, number=number, memo=inp.memo, issuer=issuer, origin=origin,
                    profile=tax_facts.semantic_profile(profile), lines=[_line_semantic(line) for line in lines],
                    custom_fields={key: {k: v for k, v in field.items() if k != 'value_id'}
                                   for key, field in custom_plan.snapshot.items()})
    fingerprint = hashlib.sha256(json_text(dict(
        company_id=info['id'], type=DOCUMENT_TYPE, version=0, content=semantic,
        tax_attribution=attribution.model_dump(mode='json') if attribution else None)).encode()).hexdigest()
    if inp.expected_facts_fingerprint is not None and inp.expected_facts_fingerprint != fingerprint:
        raise BookflowError('E_PREVIEW_STALE', details={'facts_fingerprint': fingerprint})
    return dict(profile=profile, origin=origin, date=inp.date, number=number, sequence=sequence,
                memo=inp.memo, issuer=issuer, lines=lines, ordinals=ordinals, tax_keys=tax_keys,
                custom_plan=custom_plan, warnings=warnings, semantic=semantic, fingerprint=fingerprint,
                subtotal=subtotal, tax=tax, total=total, currency=info['home_currency'],
                tax_attribution=attribution)


def _line_semantic(line):
    values = {key: line[key] for key in ('item_id', 'description', 'quantity_microunits', 'unit_id',
                                         'unit_factor_nanounits', 'base_quantity_microunits', *MONEY_COLUMNS)}
    values['profile'] = line['profile'].model_dump()
    values['source'] = line['source']
    values['intervals'] = [list(interval) for interval in line['intervals']]
    return values


def _posting_accounts_active(s, resolved):
    ids = {resolved['profile'].control_account.id}
    for line in resolved['lines']:
        ids.add(line['profile'].income_account.id)
        ids.update(_liability(cell).id for cell in line['taxes'])
    accounts = effects.rows(s, c.accounts, c.accounts.c.id.in_(ids))
    if {account['id'] for account in accounts} != ids:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'account'})
    for account in accounts:
        if not account['active']:
            raise BookflowError('E_INACTIVE_REFERENCE', details={'record_type': 'account', 'record_id': account['id']})
    current = {account['id']: account for account in accounts}

    def eligible(identifier, allowed, field, remedy, *, role=None):
        account = current[identifier]
        if account['type'] not in allowed or (role is not None and account['system_role'] != role):
            raise BookflowError('E_VALIDATION', message='A posting account is not eligible. ' + remedy,
                                details={'field': field, 'reason': 'captured_posting_account_type'})

    eligible(resolved['profile'].control_account.id, {'accounts_receivable'}, 'ar_account',
             'Select an eligible Accounts Receivable account before posting this credit.')
    for line in resolved['lines']:
        eligible(line['profile'].income_account.id, {'income', 'other_income'}, 'lines',
                 "Select an item whose income account is still an income account.")
        for cell in line['taxes']:
            eligible(_liability(cell).id, {'other_current_liability'}, 'sales_tax_item',
                     'Select eligible tax defaults before posting this credit.', role='sales_tax_payable')


def _liability(cell):
    """The captured liability account of a tax cell, whichever kind of line produced it."""
    return cell['rule'].liability_account if cell['rule'] is not None else _captured(cell).liability_account


def _captured(cell):
    return SalesTaxComponent.model_validate_json(cell['source']['component_snapshot'])


# ------------------------------------------------------------------ preparing the write


def prepare(s, ctx, inp):
    at, event = clock.now_iso(), new_id()
    created = lambda: dict(id=new_id(), created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)
    provenance = dict(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)
    audited = dict(**provenance, audit_event_id=event)
    header = dict(id=new_id(), **common(s.actor.id, ctx.interface.value, at), type=DOCUMENT_TYPE,
                  status='posted', voided_at=None, voided_by=None, void_reason=None, void_posting_batch_id=None)
    pending = {table: [] for table, _, _ in TABLE_KINDS}
    resolved = commercial(s, inp, document_id=header['id'], pending=pending)
    journals.open_dates(s, [resolved['date']])
    _posting_accounts_active(s, resolved)
    profile, currency = resolved['profile'], resolved['currency']
    revision = dict(**created(), transaction_id=header['id'], revision_number=1, supersedes_revision_id=None,
                    date=resolved['date'], number=resolved['number'], name_type='customer',
                    name_id=profile.customer.id, memo=resolved['memo'], total_minor_units=resolved['total'],
                    currency=currency, issuer_snapshot=json_text(resolved['issuer']),
                    custom_fields_snapshot=json_text(resolved['custom_plan'].snapshot), audit_event_id=event)
    header.update(number=revision['number'], current_revision_id=revision['id'])
    pending['transaction_revisions'].append(revision)
    pending['credit_profiles'].append(dict(
        transaction_id=header['id'], revision_id=revision['id'], **audited, type=DOCUMENT_TYPE,
        customer_id=profile.customer.id, ar_account_id=profile.control_account.id, origin=resolved['origin'],
        subtotal_minor_units=resolved['subtotal'], tax_minor_units=resolved['tax'],
        profile_snapshot=json_text(profile.model_dump()),
        tax_attribution_snapshot=json_text(resolved['tax_attribution'].model_dump(mode='json'))
        if resolved['tax_attribution'] is not None else None))
    for position, line in enumerate(resolved['lines'], 1):
        identity = dict(**created(), transaction_id=header['id'])
        pending['document_line_identities'].append(identity)
        facts = line['profile']
        envelope = dict(**created(), transaction_id=header['id'], revision_id=revision['id'],
                        line_id=identity['id'], position=position, kind='credit', account_id=None, side=None,
                        amount_minor_units=None, currency=currency, account_snapshot=None,
                        name_type='customer', name_id=profile.customer.id, party_name=profile.customer.label,
                        class_id=facts.class_id.id if facts.class_id else None,
                        class_name=facts.class_id.label if facts.class_id else None,
                        description=line['description'], **dict.fromkeys(journals.FACTS))
        pending['document_lines'].append(envelope)
        pending['sales_tax_line_keys'].append(dict(transaction_id=header['id'], line_id=identity['id'],
                                                   tax_ordinal=line['tax_ordinal'], **provenance))
        line['envelope'] = envelope
        for claim in line['claim_rows']:
            claim.update(credit_transaction_id=header['id'], credit_revision_id=revision['id'],
                         credit_document_line_id=envelope['id'],
                         source_revision_id=line['source']['revision_id'],
                         source_document_line_id=line['source']['document_line_id'],
                         reverses_claim_id=None, effective_date=resolved['date'], **audited)
    batch = dict(**created(), transaction_id=header['id'], revision_id=revision['id'], kind='original',
                 effective_date=revision['date'], reverses_batch_id=None, replaces_batch_id=None,
                 audit_event_id=event)
    pending['posting_batches'].append(batch)
    _business_postings(header, revision, batch, resolved, pending, created, audited)
    output = CreditMemoWriteOutput(**summary(header, revision, pending['credit_profiles'][0]),
                                   revision=revision_output(s, revision, pending),
                                   source_current=_source_current(s, header['id'], pending),
                                   facts_fingerprint=resolved['fingerprint'], warnings=resolved['warnings'])
    plan = Plan(output, dict(input=inp, operation='post', changed=True, header=header, before=None,
                             pending=pending, sequence=resolved['sequence'], event=event,
                             custom_plan=resolved['custom_plan'], resolved=resolved))
    from bookflow.company.credit_validation import validate
    validate(plan, s, ctx)
    return plan


def _business_postings(header, revision, batch, resolved, pending, created, audited):
    """Dr each line's captured income and tax; Cr the receivable the gross, once."""
    profile, currency = resolved['profile'], resolved['currency']
    line_no = 0

    def leg(account, amount, debit, envelope, description):
        nonlocal line_no
        line_no += 1
        value = dict(**created(), transaction_id=header['id'], batch_id=batch['id'], line_no=line_no,
                     account_id=account.id, account_snapshot=json_text(account.model_dump()),
                     currency=currency, name_type='customer', name_id=profile.customer.id,
                     party_name=profile.customer.label, class_id=envelope['class_id'],
                     class_name=envelope['class_name'], description=description,
                     debit_minor_units=amount if debit else 0, credit_minor_units=0 if debit else amount,
                     reversed_line_id=None, **dict.fromkeys(journals.FACTS))
        pending['posting_lines'].append(value)
        return value

    def attribute(posting, envelope, units):
        source = dict(**created(), transaction_id=header['id'], posting_line_id=posting['id'],
                      revision_id=revision['id'], document_line_id=envelope['id'],
                      amount_minor_units=units, currency=currency, reversed_source_id=None,
                      tax_component_id=None)
        pending['posting_line_sources'].append(source)
        return source

    for line in resolved['lines']:
        envelope, facts = line['envelope'], line['profile']
        net_source = None
        if line['net_minor_units']:
            posting = leg(facts.income_account, line['net_minor_units'], True, envelope, envelope['description'])
            net_source = attribute(posting, envelope, line['net_minor_units'])
        pending['credit_line_profiles'].append(dict(
            document_line_id=envelope['id'], transaction_id=header['id'], revision_id=revision['id'],
            **audited, item_id=line['item_id'], quantity_microunits=line['quantity_microunits'],
            unit_id=line['unit_id'], unit_factor_nanounits=line['unit_factor_nanounits'],
            base_quantity_microunits=line['base_quantity_microunits'],
            unit_price_minor_units=line['unit_price_minor_units'], pricing_basis=facts.pricing_basis,
            net_minor_units=line['net_minor_units'], tax_minor_units=line['tax_minor_units'],
            gross_minor_units=line['gross_minor_units'], item_snapshot=json_text(facts.model_dump()),
            posting_source_id=net_source['id'] if net_source else None,
            source_transaction_id=line['source']['transaction_id'] if line['source'] else None,
            source_revision_id=line['source']['revision_id'] if line['source'] else None,
            source_document_line_id=line['source']['document_line_id'] if line['source'] else None,
            source_line_id=line['source']['line_id'] if line['source'] else None))
        for position, cell in enumerate(line['taxes'], 1):
            captured = _cell_snapshot(cell, position)
            amount = _cell_amount(cell)
            source = None
            if amount:
                posting = leg(_liability(cell), amount, True, envelope, envelope['description'])
                source = attribute(posting, envelope, amount)
            pending['credit_tax_components'].append(dict(
                **created(), transaction_id=header['id'], revision_id=revision['id'],
                document_line_id=envelope['id'], audit_event_id=audited['audit_event_id'],
                tax_item_id=captured['tax_item_id'], agency_id=captured['agency_id'],
                liability_account_id=_liability(cell).id,
                rate_percent_millionths=captured['rate_percent_millionths'],
                taxable_minor_units=_cell_taxable(cell), tax_minor_units=amount,
                component_snapshot=json_text(captured['snapshot'].model_dump()),
                posting_source_id=source['id'] if source else None,
                source_tax_component_id=cell['source']['id'] if cell['rule'] is None else None))
    receivable = leg(profile.control_account, resolved['total'], False,
                     dict(class_id=profile.class_id.id if profile.class_id else None,
                          class_name=profile.class_id.label if profile.class_id else None),
                     resolved['memo'])
    key = dict(**created(), transaction_id=header['id'], party_id=profile.customer.id,
               ar_account_id=profile.control_account.id, currency=currency,
               audit_event_id=audited['audit_event_id'])
    pending['credit_source_keys'].append(key)
    for line in resolved['lines']:
        if not line['gross_minor_units']:
            continue
        source = attribute(receivable, line['envelope'], line['gross_minor_units'])
        pending['credit_components'].append(dict(
            **created(), transaction_id=header['id'], revision_id=revision['id'], key_id=key['id'],
            document_line_id=line['envelope']['id'], posting_source_id=source['id'],
            amount_minor_units=line['gross_minor_units'], currency=currency,
            audit_event_id=audited['audit_event_id']))


def _cell_snapshot(cell, position):
    if cell['rule'] is not None:
        rule = cell['rule']
        snapshot = SalesTaxComponent(position=position, tax_item=rule.model_dump(include={'id', 'label', 'version'}),
                                     agency=rule.agency, liability_account=rule.liability_account)
        return dict(tax_item_id=rule.id, agency_id=rule.agency.id,
                    rate_percent_millionths=rule.rate_percent_millionths, snapshot=snapshot)
    row = cell['source']
    return dict(tax_item_id=row['tax_item_id'], agency_id=row['agency_id'],
                rate_percent_millionths=row['rate_percent_millionths'], snapshot=_captured(cell))


def _cell_amount(cell):
    return cell['tax_minor_units'] if cell['rule'] is not None else cell['captured']['tax_minor_units']


def _cell_taxable(cell):
    return cell['taxable_minor_units'] if cell['rule'] is not None else cell['captured']['taxable_minor_units']


def apply(plan, ctx, s):
    # Rebuilt inside the writer transaction: the residue on a source line, the number and the
    # open period are only decisive here, and the preview read them before anyone held the lock.
    fresh = prepare(s, ctx, plan.data['input'])
    from bookflow.company.credit_validation import validate
    validate(fresh, s, ctx)
    return effects.persist(fresh, ctx, s, command_name='credit-memo post', table_kinds=TABLE_KINDS)


# ------------------------------------------------------------------ reading it back


def summary(header, revision, profile):
    captured = SalesProfile.model_validate_json(profile['profile_snapshot'])
    currency = revision['currency']
    return dict(header, date=revision['date'], customer_id=profile['customer_id'],
                customer_name=captured.customer.label, ar_account_id=profile['ar_account_id'],
                origin=profile['origin'], memo=revision['memo'], currency=currency,
                subtotal_minor_units=profile['subtotal_minor_units'], tax_minor_units=profile['tax_minor_units'],
                total_minor_units=revision['total_minor_units'],
                subtotal=Money(profile['subtotal_minor_units'], currency).to_dict(),
                tax=Money(profile['tax_minor_units'], currency).to_dict(),
                total=Money(revision['total_minor_units'], currency).to_dict())


def _decoded(value):
    return json.loads(value) if isinstance(value, str) else value


def _rows_for(s, table, revision, pending, column='revision_id'):
    found = [row for row in (pending or {}).get(table.name, []) if row.get(column) == revision['id']]
    return found or effects.rows(s, table, table.c[column] == revision['id'], order=table.c[list(table.c.keys())[0]])


def revision_output(s, revision, pending=None, *, summary_only=False):
    pending = pending or {}
    profile = next((row for row in pending.get('credit_profiles', []) if row['revision_id'] == revision['id']), None)
    if profile is None:
        profile = profile_row(s, revision)
    currency = revision['currency']
    batches = effects.rows(s, c.posting_batches, c.posting_batches.c.revision_id == revision['id'],
                           order=c.posting_batches.c.id)
    summaries = [journals.batch_output(s, batch) for batch in batches]
    summaries += [journals.batch_output(s, batch, [line for line in pending['posting_lines'] if line['batch_id'] == batch['id']])
                  for batch in pending.get('posting_batches', []) if batch['revision_id'] == revision['id']]
    envelopes = [row for row in pending.get('document_lines', []) if row['revision_id'] == revision['id']]
    if not envelopes:
        envelopes = effects.rows(s, c.document_lines, c.document_lines.c.revision_id == revision['id'],
                                 order=c.document_lines.c.position)
    values = {k: v for k, v in revision.items() if not k.endswith('_snapshot')}
    values.update(subtotal_minor_units=profile['subtotal_minor_units'], tax_minor_units=profile['tax_minor_units'],
                  subtotal=Money(profile['subtotal_minor_units'], currency).to_dict(),
                  tax=Money(profile['tax_minor_units'], currency).to_dict(),
                  total=Money(revision['total_minor_units'], currency).to_dict(),
                  line_count=len(envelopes), batches=summaries,
                  tax_calculation_details=tax_facts.details(
                      SalesProfile.model_validate_json(profile['profile_snapshot']),
                      profile['tax_attribution_snapshot']))
    if summary_only:
        return CreditRevisionSummaryOutput(**values)
    details = _rows_for(s, c.credit_line_profiles, revision, pending)
    components = _rows_for(s, c.credit_tax_components, revision, pending)
    claims = [row for row in pending.get('credit_source_claims', []) if row.get('credit_revision_id') == revision['id']]
    if not claims:
        claims = effects.rows(s, c.credit_source_claims,
                              c.credit_source_claims.c.credit_revision_id == revision['id'],
                              c.credit_source_claims.c.kind == 'claim', order=c.credit_source_claims.c.id)
    profiles = {row['document_line_id']: row for row in details}
    lines = []
    for envelope in envelopes:
        detail = profiles[envelope['id']]
        cells = [CreditTaxComponentOutput(
            **{k: v for k, v in row.items() if k != 'component_snapshot'},
            component_snapshot=_decoded(row['component_snapshot']),
            taxable=Money(row['taxable_minor_units'], currency).to_dict(),
            tax=Money(row['tax_minor_units'], currency).to_dict())
                 for row in components if row['document_line_id'] == envelope['id']]
        owned = [CreditClaimOutput(**{k: v for k, v in row.items() if k in CreditClaimOutput.model_fields},
                                   start_quantity=format_quantity_micro_units(row['start_microunits']),
                                   end_quantity=format_quantity_micro_units(row['end_microunits']))
                 for row in claims if row['credit_document_line_id'] == envelope['id']]
        lines.append(CreditLineOutput(
            **{k: envelope[k] for k in ('id', 'created_at', 'created_by', 'created_via', 'transaction_id',
                                        'revision_id', 'line_id', 'position', 'kind', 'description',
                                        'class_id', 'class_name')},
            **{k: detail[k] for k in ('item_id', 'quantity_microunits', 'unit_id', 'unit_factor_nanounits',
                                      'base_quantity_microunits', 'pricing_basis', 'net_minor_units',
                                      'tax_minor_units', 'gross_minor_units', 'source_transaction_id',
                                      'source_revision_id', 'source_line_id')},
            quantity=format_quantity_micro_units(detail['quantity_microunits']),
            base_quantity=format_quantity_micro_units(detail['base_quantity_microunits']),
            unit_price=Money(detail['unit_price_minor_units'], currency).to_dict()
            if detail['unit_price_minor_units'] is not None else None,
            net=Money(detail['net_minor_units'], currency).to_dict(),
            tax=Money(detail['tax_minor_units'], currency).to_dict(),
            gross=Money(detail['gross_minor_units'], currency).to_dict(),
            item_snapshot=_decoded(detail['item_snapshot']),
            tax_components=cells, claims=owned))
    snapshot = json.loads(revision['custom_fields_snapshot'])
    return CreditRevisionOutput(**values, profile=json.loads(profile['profile_snapshot']), lines=lines,
                                issuer_snapshot=json.loads(revision['issuer_snapshot']),
                                custom_fields_snapshot=snapshot, custom_fields=custom.project(snapshot))


def _source_current(s, transaction_id, pending=None):
    """What this credit is worth now: its capacity less what has been applied from it.

    Reading the settlement edge rather than assuming it is empty is what keeps this true on the
    day `customer-credit apply` starts writing rows against these keys.
    """
    keys = [row for row in (pending or {}).get('credit_source_keys', [])
            if row['transaction_id'] == transaction_id]
    if not keys:
        keys = effects.rows(s, c.credit_source_keys, c.credit_source_keys.c.transaction_id == transaction_id)
    key = keys[0]
    header = effects.rows(s, c.transactions, c.transactions.c.id == transaction_id)
    components = [row for row in (pending or {}).get('credit_components', [])
                  if row['transaction_id'] == transaction_id]
    if not components:
        revision = header[0]['current_revision_id'] if header else None
        components = effects.rows(s, c.credit_components,
                                  c.credit_components.c.transaction_id == transaction_id,
                                  c.credit_components.c.revision_id == revision)
    capacity = sum(row['amount_minor_units'] for row in components) if (not header or header[0]['status'] == 'posted') else 0
    app, inverse = c.applications, c.applications.alias('inverse')
    applied = s.company.conn.execute(sa.select(sa.func.coalesce(sa.func.sum(app.c.amount_minor_units), 0)).where(
        app.c.kind == 'apply', app.c.credit_source_key_id == key['id'],
        ~sa.exists(sa.select(inverse.c.id).where(inverse.c.reverses_application_id == app.c.id)))).scalar_one()
    currency = key['currency']
    return CreditSourceOutput(
        credit_source_key_id=key['id'], party_id=key['party_id'], ar_account_id=key['ar_account_id'],
        currency=currency, capacity_minor_units=capacity, applied_minor_units=applied,
        available_minor_units=capacity - applied, capacity=Money(capacity, currency).to_dict(),
        applied=Money(applied, currency).to_dict(), available=Money(capacity - applied, currency).to_dict())


def show(s, inp):
    header = resolve(s, inp.credit_memo)
    revision = journals.revision(s, header, inp.revision_number)
    return CreditMemoOutput(**summary(header, revision, profile_row(s, revision)),
                            revision=revision_output(s, revision),
                            source_current=_source_current(s, header['id']))


def page(s, ctx, inp):
    from bookflow.company.query import continuation, page_state

    class Contract:
        cursor = inp.cursor
        query = None

        def model_dump(self, **kw):
            return inp.model_dump(**kw)

    state = page_state(s, 'credit-memo history', Contract(), ctx.on_behalf_of)
    header = resolve(s, inp.credit_memo)
    query = sa.select(c.transaction_revisions).where(
        c.transaction_revisions.c.transaction_id == header['id']).order_by(c.transaction_revisions.c.revision_number)
    found = [dict(row) for row in s.company.conn.execute(query.offset(state.offset).limit(inp.limit + 1)).mappings()]
    more, found = len(found) > inp.limit, found[:inp.limit]
    return CreditMemoHistoryOutput(
        **{k: header[k] for k in ('id', 'version', 'current_revision_id', 'number', 'status')},
        items=[revision_output(s, revision, summary_only=True) for revision in found],
        count=len(found), has_more=more, next_cursor=continuation(state, len(found), more),
        audit_watermark=state.sequence)
