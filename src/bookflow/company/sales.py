"""Real commercial sales revisions and immutable posting effects."""
from __future__ import annotations

import hashlib
import json
import zlib
import sqlalchemy as sa

from bookflow.company import schema as c, journals, document_effects as effects
from bookflow.company import journal_custom_fields as custom, list_service
from bookflow.company import sales_calculations as calc
from bookflow.company import tax_attribution as tax_facts
from bookflow.company.sales_facts import SalesProfile, SalesLineProfile, SalesTaxComponent
from bookflow.company.sales_models import SalesLineInput, _invalid
from bookflow.company.sales_outputs import (
    SalesOutput, SalesSummaryOutput, SalesRevisionOutput, SalesRevisionSummaryOutput,
    SalesWriteOutput, SalesPageOutput, SalesHistoryOutput,
)
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_quantity_micro_units
from bookflow.core.ids import new_id, is_ulid
from bookflow.core.money import Money
from bookflow.core.registry import Plan, Touched
from bookflow.hub.users import common

TABLE_KINDS = (
    ('transaction_revisions', 'transaction_revision', 'id'),
    ('document_line_identities', 'document_line_identity', 'id'),
    ('document_lines', 'document_line', 'id'),
    ('sales_profiles', 'sales_profile', 'revision_id'),
    ('sales_line_profiles', 'sales_line_profile', 'document_line_id'),
    ('sales_tax_components', 'sales_tax_component', 'id'),
    ('sales_tax_line_keys', 'sales_tax_line_key', 'line_id'),
    ('sales_tax_attributions', 'sales_tax_attribution', 'revision_id'),
    ('sales_tax_attribution_lines', 'sales_tax_attribution_line', 'document_line_id'),
    ('posting_batches', 'posting_batch', 'id'),
    ('posting_lines', 'posting_line', 'id'),
    ('posting_line_sources', 'posting_line_source', 'id'),
)
MONEY_COLUMNS = ('unit_price_minor_units', 'net_minor_units', 'tax_minor_units', 'gross_minor_units')


def json_text(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def resolve(s, selector, document_type):
    t = c.transactions
    key = selector.upper() if is_ulid(selector) else selector
    found = effects.rows(s, t, t.c.type == document_type, t.c.id == key)
    if not found:
        found = effects.rows(s, t, t.c.type == document_type, t.c.number == selector)
    if not found:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': document_type, 'selector': selector})
    return found[0]


def profile_row(s, revision):
    return effects.rows(s, c.sales_profiles, c.sales_profiles.c.revision_id == revision['id'])[0]


def saved_lines(s, revision):
    lines, profiles = c.document_lines, c.sales_line_profiles
    query = sa.select(lines, *(col for col in profiles.c if col.name not in lines.c)).join(
        profiles, profiles.c.document_line_id == lines.c.id).where(lines.c.revision_id == revision['id']).order_by(lines.c.position)
    return [dict(row) for row in s.company.conn.execute(query).mappings()]


def summary(header, revision, profile):
    captured = SalesProfile.model_validate_json(profile['profile_snapshot'])
    currency = revision['currency']
    return dict(header, date=revision['date'], customer_id=profile['customer_id'], customer_name=captured.customer.label,
        memo=revision['memo'], due_date=profile['due_date'], currency=currency,
        subtotal_minor_units=profile['subtotal_minor_units'], tax_minor_units=profile['tax_minor_units'],
        total_minor_units=revision['total_minor_units'],
        subtotal=Money(profile['subtotal_minor_units'], currency).to_dict(),
        tax=Money(profile['tax_minor_units'], currency).to_dict(), total=Money(revision['total_minor_units'], currency).to_dict())


def revision_output(s, revision, pending=None, *, summary_only=False):
    pending = pending or {}
    profile = next((p for p in pending.get('sales_profiles', []) if p['revision_id'] == revision['id']), None)
    if profile is None:
        profile = profile_row(s, revision)
    lines = [line for line in pending.get('document_lines', []) if line['revision_id'] == revision['id']]
    if summary_only:
        line_count = len(lines) if lines else s.company.conn.execute(sa.select(sa.func.count()).select_from(c.document_lines).where(
            c.document_lines.c.revision_id == revision['id'])).scalar_one()
    elif lines:
        details = {line['document_line_id']: line for line in pending['sales_line_profiles']}
        lines = [dict(line, **{k: v for k, v in details[line['id']].items() if k not in line}) for line in lines]
        components = [row for row in pending['sales_tax_components'] if row['revision_id'] == revision['id']]
    else:
        lines = saved_lines(s, revision)
        components = effects.rows(s, c.sales_tax_components, c.sales_tax_components.c.revision_id == revision['id'])
    batches = effects.rows(s, c.posting_batches, c.posting_batches.c.revision_id == revision['id'], order=c.posting_batches.c.id)
    extra_batches = [b for b in pending.get('posting_batches', []) if b['revision_id'] == revision['id']]
    summaries = [journals.batch_output(s, batch) for batch in batches]
    summaries += [journals.batch_output(s, batch, [line for line in pending['posting_lines'] if line['batch_id'] == batch['id']])
                  for batch in extra_batches]
    currency = revision['currency']
    values = {k: v for k, v in revision.items() if not k.endswith('_snapshot')}
    values.update(subtotal_minor_units=profile['subtotal_minor_units'], tax_minor_units=profile['tax_minor_units'],
        subtotal=Money(profile['subtotal_minor_units'], currency).to_dict(), tax=Money(profile['tax_minor_units'], currency).to_dict(),
        total=Money(revision['total_minor_units'], currency).to_dict(), line_count=line_count if summary_only else len(lines), batches=summaries)
    from bookflow.company.billing_queries import sale_source_output, sale_source_links
    values['billing_links'] = sale_source_links(s, revision['id'])
    tax_rows = [r for r in pending.get('sales_tax_attributions', []) if r['revision_id'] == revision['id']]
    if not tax_rows:
        tax_rows = effects.rows(s, c.sales_tax_attributions, c.sales_tax_attributions.c.revision_id == revision['id'])
    values['tax_calculation_details'] = tax_facts.details(SalesProfile.model_validate_json(profile['profile_snapshot']),
        tax_rows[0]['facts_snapshot'] if tax_rows else None)
    if summary_only:
        return SalesRevisionSummaryOutput(**values)
    values['billing_sources'] = sale_source_output(s, revision['id'])
    tax_mapping = [r for r in pending.get('sales_tax_attribution_lines', []) if r['revision_id'] == revision['id']]
    if not tax_mapping:
        tax_mapping = effects.rows(s, c.sales_tax_attribution_lines, c.sales_tax_attribution_lines.c.revision_id == revision['id'])
    tax_ordinals = {r['document_line_id']: r['tax_ordinal'] for r in tax_mapping}
    rendered = []
    for line in lines:
        taxes = []
        for component in sorted((row for row in components if row['document_line_id'] == line['id']),
                                key=lambda row: json.loads(row['component_snapshot'])['position']):
            taxes.append(dict(component, component_snapshot=json.loads(component['component_snapshot']),
                tax=Money(component['tax_minor_units'], currency).to_dict(),
                taxable=Money(component['taxable_minor_units'], currency).to_dict()))
        from bookflow.company.billing_allocations import quantity_output
        rendered.append(dict(line, tax_ordinal=tax_ordinals.get(line['id']), item_snapshot=json.loads(line['item_snapshot']), **quantity_output(line),
            unit_price=Money(line['unit_price_minor_units'], currency).to_dict() if line['unit_price_minor_units'] is not None else None,
            pricing_basis=line.get('pricing_basis', 'unit'), net=Money(line['net_minor_units'], currency).to_dict(),
            tax=Money(line['tax_minor_units'], currency).to_dict(), gross=Money(line['gross_minor_units'], currency).to_dict(), tax_components=taxes))
    snapshot = json.loads(revision['custom_fields_snapshot'])
    return SalesRevisionOutput(**values, profile=json.loads(profile['profile_snapshot']), lines=rendered,
        issuer_snapshot=json.loads(revision['issuer_snapshot']), custom_fields_snapshot=snapshot, custom_fields=custom.project(snapshot))


def show(s, inp, document_type):
    header = resolve(s, getattr(inp, document_type), document_type)
    revision = journals.revision(s, header, inp.revision_number)
    settlement = None
    if document_type == 'invoice':
        from bookflow.company.payment_queries import invoice_current
        settlement = invoice_current(s, header['id'])
    return SalesOutput(**summary(header, revision, profile_row(s, revision)), revision=revision_output(s, revision), settlement_current=settlement)


def page(s, ctx, inp, document_type, *, history=False):
    from bookflow.company.query import page_state, continuation
    class Contract:
        cursor = inp.cursor
        query = None
        def model_dump(self, **kw):
            return inp.model_dump(**kw)
    state = page_state(s, document_type + (' history' if history else ' query'), Contract(), ctx.on_behalf_of)
    if history:
        header = resolve(s, getattr(inp, document_type), document_type)
        query = sa.select(c.transaction_revisions).where(c.transaction_revisions.c.transaction_id == header['id']).order_by(c.transaction_revisions.c.revision_number)
    else:
        from bookflow.company.payment_queries import indexed_source, cross_join
        t = indexed_source(c.transactions,
            'ix_co17_transactions_current' if inp.customer else 'ix_co17_transactions_type',
            'current_revision_id', 'type', 'status', 'id', 'version', 'number')
        r = indexed_source(c.transaction_revisions, 'ix_co17_revisions_read',
            'id', 'date', 'currency', 'total_minor_units', 'memo')
        p = indexed_source(c.sales_profiles, 'ix_co17_sales_party' if inp.customer else 'ix_co17_sales_revision',
            'revision_id', 'customer_id', 'control_account_id')
        source = (cross_join(cross_join(p, r, r.c.id == p.c.revision_id), t, t.c.current_revision_id == r.c.id)
            if inp.customer else cross_join(cross_join(t, r, r.c.id == t.c.current_revision_id), p, p.c.revision_id == r.c.id))
        query = sa.select(t.c.id).select_from(source).where(t.c.type == document_type)
        if inp.customer:
            from bookflow.company.parties import resolve_party
            customer = resolve_party(s.company, 'customer', inp.customer)
            query = query.where(p.c.customer_id == customer['id'])
        if inp.date_from:
            query = query.where(r.c.date >= inp.date_from)
        if inp.date_to:
            query = query.where(r.c.date <= inp.date_to)
        if inp.status:
            query = query.where(t.c.status == inp.status)
        if inp.number:
            query = query.where(t.c.number.contains(inp.number, autoescape=True))
        query = query.order_by(r.c.date, t.c.id)
    found = [dict(row) for row in s.company.conn.execute(query.offset(state.offset).limit(inp.limit + 1)).mappings()]
    more, found = len(found) > inp.limit, found[:inp.limit]
    shared = dict(count=len(found), has_more=more, next_cursor=continuation(state, len(found), more), audit_watermark=state.sequence)
    if history:
        return SalesHistoryOutput(**{k: header[k] for k in ('id', 'version', 'current_revision_id', 'number', 'status')},
            items=[revision_output(s, revision, summary_only=True) for revision in found], **shared)
    headers = {row['id']: dict(row) for row in s.company.conn.execute(sa.select(c.transactions).where(
        c.transactions.c.id.in_([row['id'] for row in found]))).mappings()} if found else {}
    found = [headers[row['id']] for row in found]
    revision_ids = [header['current_revision_id'] for header in found]
    revisions = {row['id']: dict(row) for row in s.company.conn.execute(sa.select(c.transaction_revisions).where(
        c.transaction_revisions.c.id.in_(revision_ids))).mappings()} if found else {}
    profiles = {row['revision_id']: dict(row) for row in s.company.conn.execute(sa.select(c.sales_profiles).where(
        c.sales_profiles.c.revision_id.in_(revision_ids))).mappings()} if found else {}
    for header in found:
        revision = revisions.get(header['current_revision_id'])
        if revision is None or revision['transaction_id'] != header['id']:
            raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'transaction_revision'})
    settlements = {}
    if document_type == 'invoice':
        from bookflow.company.payment_queries import invoice_currents
        from bookflow.company.payment_outputs import InvoiceSettlementOutput
        settlements = invoice_currents(s, found, revisions)
    items = []
    for header in found:
        revision = revisions[header['current_revision_id']]
        item = SalesSummaryOutput(**summary(header, revision, profiles[revision['id']]))
        if document_type == 'invoice':
            item.settlement_current = InvoiceSettlementOutput(**settlements[header['id']])
        items.append(item)
    return SalesPageOutput(items=items, **shared)


def _custom_semantic(snapshot):
    return {key: {name: value for name, value in field.items() if name != 'value_id'} for key, field in snapshot.items()}


def _line_semantic(line):
    profile = line['profile']
    return {key: line[key] for key in ('line_id', 'item_id', 'description', 'quantity_microunits', 'unit_id',
        'unit_factor_nanounits', 'base_quantity_microunits', *MONEY_COLUMNS)} | {'profile': profile.model_dump()}


def _saved_semantic(s, revision):
    profile = profile_row(s, revision)
    lines = []
    for line in saved_lines(s, revision):
        lines.append(_line_semantic(dict(line, profile=SalesLineProfile.model_validate_json(line['item_snapshot']))))
    return dict(date=revision['date'], number=revision['number'], memo=revision['memo'],
        issuer=json.loads(revision['issuer_snapshot']), profile=tax_facts.semantic_profile(SalesProfile.model_validate_json(profile['profile_snapshot'])),
        lines=lines, custom_fields=_custom_semantic(json.loads(revision['custom_fields_snapshot'])))


def _changes(before, after, path=''):
    if isinstance(before, dict) and isinstance(after, dict):
        return [field for key in sorted(set(before) | set(after))
                for field in _changes(before.get(key), after.get(key), f'{path}.{key}'.lstrip('.'))]
    if path == 'lines' and isinstance(before, list) and isinstance(after, list):
        old = {line['line_id']: line for line in before}
        new = {line['line_id'] or f'new_{i}': line for i, line in enumerate(after)}
        changed = _changes(old, new, path)
        if [line['line_id'] for line in before] != [line['line_id'] for line in after]:
            changed.append('lines.order')
        return changed
    return [path] if before != after else []


def _history_snapshot(blob):
    """Unusable historical evidence means unknown fields, never a decoder failure."""
    try:
        value = audit.decode_snapshot(blob)
    except (ValueError, TypeError, UnicodeError, zlib.error):
        return None
    return value if isinstance(value, dict) else None


def _historical_fields(s, header, expected):
    entries = c.audit_entries
    snapshots = s.company.conn.execute(sa.select(entries.c.after).where(
        entries.c.record_type == 'transaction', entries.c.record_id == header['id'],
        entries.c.version_after == expected, entries.c.action != 'migrate').limit(2)).all()
    if len(snapshots) != 1:
        return None
    old = _history_snapshot(snapshots[0][0])
    if (old is None or old.get('id') != header['id'] or type(old.get('version')) is not int
            or old['version'] != expected or old.get('type') != header['type']
            or old.get('status') not in ('posted', 'voided')
            or not isinstance(old.get('current_revision_id'), str)):
        return None
    prior = effects.rows(s, c.transaction_revisions,
        c.transaction_revisions.c.transaction_id == header['id'],
        c.transaction_revisions.c.id == old['current_revision_id'])
    if len(prior) != 1:
        return None
    fields = _changes(_saved_semantic(s, prior[0]), _saved_semantic(s, journals.revision(s, header)))
    if old['status'] != header['status']:
        fields.append('status')
    return sorted(set(fields)) or ['version']


def _sale_conflict_message(details, fields):
    who = details.get('updated_by_name') or details.get('updated_by') or 'unknown'
    behalf = details.get('updated_on_behalf_of_name') or details.get('updated_on_behalf_of')
    if behalf:
        who += f' on behalf of {behalf}'
    ago = details.get('seconds_since_update')
    when = f' ({ago} s ago)' if ago is not None else ''
    what = ', '.join(fields) if fields else 'fields that cannot be determined'
    version = details['current_version']
    return (f'Changes since the expected version: {what}. Latest writer: {who}{when} '
            f'(now version {version}); re-read and retry with expected_version {version}.')


def _version(s, header, expected):
    try:
        return journals.version_meta(s, header, expected, history_decoder=_history_snapshot)
    except BookflowError as exc:
        if exc.code != 'E_VERSION_CONFLICT':
            raise
        fields = None
        if 'unknown_versions' not in exc.details and expected < header['version']:
            fields = _historical_fields(s, header, expected)
            if fields is None:
                exc.details['unknown_versions'] = [expected]
        exc.details['changed_fields'] = fields or []
        if header['type'] == 'invoice' and fields is not None:
            from bookflow.company.payment_dependencies import changes_since_version
            changes = changes_since_version(s, header, expected)
            if changes is not None:
                settlement_changes = [row for row in changes if row['settlement_fields']]
                if settlement_changes:
                    fields = sorted((set(fields) - {'version'}) | {field for row in settlement_changes if row['record_id'] == header['id'] for field in row['settlement_fields']})
                    exc.details.update(changed_fields=fields, settlement_changes=settlement_changes[:50], settlement_change_count=len(settlement_changes))
        raise BookflowError(exc.code, message=_sale_conflict_message(exc.details, fields),
            details=exc.details) from None


def commercial(s, inp, document_type, old_header=None, old_revision=None, *, document_id, billing_source=None):
    """Resolve original intent without allocating any persisted effect identities."""
    if billing_source is not None:
        from bookflow.company.billing import resolve_commercial
        return resolve_commercial(s, inp, document_type, document_id=document_id, kind=billing_source)
    from bookflow.company import sales_defaults
    old_profile = SalesProfile.model_validate_json(profile_row(s, old_revision)['profile_snapshot']) if old_revision else None
    profile, warnings = sales_defaults.resolve_header(s, inp, document_type, previous=old_profile,
        old_date=old_revision['date'] if old_revision else None)
    date = inp.date or old_revision['date'] if old_revision else inp.date
    number, sequence = effects.allocate(s, document_type,
        inp.number if inp.number is not None else old_header['number'] if old_header else None, document_id)
    memo = inp.memo if 'memo' in inp.model_fields_set or not old_revision else old_revision['memo']
    info = dict(s.company.conn.execute(sa.select(c.company_info)).mappings().one())
    issuer = {k: v for k, v in info.items() if k in ('id', 'legal_name', 'display_name', 'home_currency')
              or k.startswith(('address_', 'legal_address_', 'ship_address_'))}
    if old_revision and not inp.refresh_defaults:
        issuer = json.loads(old_revision['issuer_snapshot'])
    else:
        # Dispatch pins the authorized name for preparation and validation.
        issuer['display_name'] = s.company_row['display_name']
    old_lines = saved_lines(s, old_revision) if old_revision else []
    prior = {line['line_id']: line for line in old_lines}
    entered = inp.lines if inp.lines is not None else [SalesLineInput(item=line['item_id'], line_id=line['line_id']) for line in old_lines]
    seen, lines = set(), []
    for line in entered:
        key = line.line_id.upper() if line.line_id and is_ulid(line.line_id) else line.line_id
        if key is not None and (key not in prior or key in seen):
            raise _invalid('line_id', 'use a unique current line identity from this sale; retired identities cannot return')
        if key:
            seen.add(key)
        resolved, line_warnings = sales_defaults.resolve_line(s, line, profile, previous=prior.get(key),
            previous_header=old_profile, refresh=inp.refresh_defaults, defer_tax=True)
        resolved['line_id'] = key
        lines.append(resolved)
        warnings.extend(line_warnings)
    tax_ordinals, tax_keys = tax_facts.prospective(s.company, document_id, [line['line_id'] for line in lines])
    attribution = tax_facts.calculate(lines, profile, info['home_currency'], tax_ordinals)
    tax_facts.apply(lines, attribution, tax_ordinals)
    from bookflow.company.billing_edits import protect_sale
    protect_sale(s, inp, old_header, old_revision, dict(profile=profile, lines=lines))
    custom.validate_kinds(s.company, inp.custom_fields, inp.custom_field_kinds, record_type=document_type)
    custom_plan = custom.prepare(s.company, document_id, inp.custom_fields,
        json.loads(old_revision['custom_fields_snapshot']) if old_revision else {}, creating=old_header is None,
        refresh=inp.refresh_defaults, record_type=document_type)
    semantic = dict(date=date, number=number, memo=memo, issuer=issuer, profile=tax_facts.semantic_profile(profile),
        lines=[_line_semantic(line) for line in lines], custom_fields=_custom_semantic(custom_plan.snapshot))
    fingerprint = hashlib.sha256(json_text(dict(company_id=info['id'], type=document_type,
        version=old_header['version'] if old_header else 0, content=semantic, tax_attribution=attribution.model_dump(mode='json'))).encode()).hexdigest()
    if (inp.expected_facts_fingerprint is not None and inp.expected_facts_fingerprint != fingerprint
            and not (document_type == 'invoice' and getattr(inp, 'operation_key', None))):
        raise BookflowError('E_PREVIEW_STALE', details={'facts_fingerprint': fingerprint})
    subtotal = calc.total((line['net_minor_units'] for line in lines), 'subtotal')
    tax = calc.total((line['tax_minor_units'] for line in lines), 'tax')
    total = calc.total((subtotal, tax))
    if total <= 0:
        raise _invalid('total', 'a posted sale must have a positive total')
    if document_type == 'sales_receipt' and old_revision:
        from bookflow.company.sales_models import money
        received = inp.amount_received
        linked = s.company.conn.execute(sa.select(c.work_billing_allocations.c.id).where(
            c.work_billing_allocations.c.transaction_id == old_header['id']).limit(1)).first()
        if received is None and linked and total != old_revision['total_minor_units']:
            raise _invalid('amount_received', 'confirm the exact new gross received total for this linked receipt correction')
        if received is not None and money(received, info['home_currency'], 'amount_received').minor_units != total:
            raise _invalid('amount_received', 'must equal the exact gross amount of the corrected receipt')
    if document_type == 'invoice':
        from bookflow.company.customer_balances import credit_warning
        warning = credit_warning(s.company, profile.customer.id, total,
            old_customer_id=old_profile.customer.id if old_profile else None,
            old_total=old_revision['total_minor_units'] if old_revision else 0)
        if warning:
            warnings.append(warning)
    return dict(profile=profile, date=date, number=number, sequence=sequence, memo=memo, issuer=issuer,
        lines=lines, custom_plan=custom_plan, warnings=warnings, semantic=semantic, fingerprint=fingerprint,
        subtotal=subtotal, tax=tax, total=total, currency=info['home_currency'], tax_attribution=attribution, tax_keys=tax_keys)


def _posting_accounts_active(s, resolved):
    ids = {resolved['profile'].control_account.id}
    for line in resolved['lines']:
        ids.add(line['profile'].income_account.id)
        ids.update(tax['rule'].liability_account.id for tax in line['taxes'])
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
            raise BookflowError('E_VALIDATION',
                message='A saved posting account is no longer eligible. ' + remedy,
                details={'field': field, 'reason': 'captured_posting_account_type'})

    control = resolved['profile'].control_account
    if control.type == 'accounts_receivable':
        eligible(control.id, {'accounts_receivable'}, 'ar_account', 'Select an eligible AR account before posting this correction.')
    else:
        eligible(control.id, {'bank', 'other_current_asset'}, 'deposit_to', 'Select bank or system Undeposited Funds before posting this correction.')
        if current[control.id]['type'] == 'other_current_asset':
            eligible(control.id, {'other_current_asset'}, 'deposit_to', 'Select bank or system Undeposited Funds before posting this correction.', role='undeposited_funds')
    for line in resolved['lines']:
        eligible(line['profile'].income_account.id, {'income', 'other_income'}, 'lines',
                 "Explicitly refresh the affected item's defaults or select an eligible item before posting this correction.")
        for tax in line['taxes']:
            eligible(tax['rule'].liability_account.id, {'other_current_liability'}, 'sales_tax_item',
                     'Explicitly refresh or select eligible tax defaults before posting this correction.', role='sales_tax_payable')


def prepare(s, ctx, inp, document_type, operation, *, billing_source=None, _settlement_internal=False, provenance=None):
    if document_type == 'invoice' and operation == 'update' and not _settlement_internal:
        from bookflow.company.payment_invoice_corrections import prepare as settlement_prepare
        return settlement_prepare(s, ctx, inp)
    old_header = resolve(s, getattr(inp, document_type), document_type) if operation != 'post' else None
    old_revision = journals.revision(s, old_header) if old_header else None
    meta = _version(s, old_header, inp.expected_version) if old_header else None
    if document_type == 'invoice' and old_header:
        from bookflow.company.payment_queries import active_applications
        if operation == 'void' and active_applications(s, invoice=old_header['id']):
            raise BookflowError('E_HAS_APPLICATIONS', details={'invoice_id': old_header['id'],
                'next': 'Inspect invoice settlement dependencies before correcting or voiding.'})
    warnings = [w] if meta and (w := list_service.blind_write_warning(meta)) else []
    if operation == 'void':
        if not ctx.reason or not ctx.reason.strip():
            raise BookflowError('E_REASON_REQUIRED')
        if len(ctx.reason.strip()) > 140:
            raise _invalid('reason', 'must be at most 140 characters')
    if operation == 'update' and old_header['status'] == 'voided':
        raise _invalid(document_type, 'a voided sale cannot be updated')
    if operation == 'void' and old_header['status'] == 'voided':
        return Plan(SalesWriteOutput(**summary(old_header, old_revision, profile_row(s, old_revision)),
            revision=revision_output(s, old_revision), changed=False, warnings=warnings),
            dict(input=inp, operation=operation, document_type=document_type, changed=False))
    if provenance is None:
        at, event = clock.now_iso(), new_id()
    else:
        from bookflow.company.payment_models import EffectProvenance
        if type(provenance) is not EffectProvenance or document_type != 'sales_receipt' or operation not in ('update', 'void'):
            raise BookflowError('E_VALIDATION')
        at, event = provenance.at, provenance.event_id
    created = lambda: dict(id=new_id(), created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)
    row_provenance = dict(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)
    header = dict(old_header) if old_header else dict(id=new_id(), **common(s.actor.id, ctx.interface.value, at),
        type=document_type, status='posted', voided_at=None, voided_by=None, void_reason=None, void_posting_batch_id=None)
    pending = {table: [] for table, _, _ in TABLE_KINDS}
    resolved, changed_fields, custom_plan, sequence = None, [], None, None
    fingerprint = None
    if operation != 'void':
        resolved = commercial(s, inp, document_type, old_header, old_revision, document_id=header['id'], billing_source=billing_source)
        warnings += resolved['warnings']
        fingerprint = resolved['fingerprint']
        changed_fields = _changes(_saved_semantic(s, old_revision), resolved['semantic']) if old_revision else []
        if old_revision and not changed_fields and not resolved['custom_plan'].changed:
            return Plan(SalesWriteOutput(**summary(old_header, old_revision, profile_row(s, old_revision)),
                revision=revision_output(s, old_revision), facts_fingerprint=fingerprint, changed=False, warnings=warnings),
                dict(input=inp, operation=operation, document_type=document_type, changed=False))
        journals.open_dates(s, [resolved['date']] + ([old_revision['date']] if old_revision else []))
        _posting_accounts_active(s, resolved)
        profile, currency = resolved['profile'], resolved['currency']
        custom_plan, sequence = resolved['custom_plan'], resolved['sequence']
        revision = dict(**created(), transaction_id=header['id'], revision_number=old_revision['revision_number'] + 1 if old_revision else 1,
            supersedes_revision_id=old_revision['id'] if old_revision else None, date=resolved['date'], number=resolved['number'],
            name_type='customer', name_id=profile.customer.id, memo=resolved['memo'], total_minor_units=resolved['total'], currency=currency,
            issuer_snapshot=json_text(resolved['issuer']), custom_fields_snapshot=json_text(custom_plan.snapshot), audit_event_id=event)
        header.update(number=revision['number'], current_revision_id=revision['id'])
        pending['transaction_revisions'].append(revision)
        pending['sales_profiles'].append(dict(transaction_id=header['id'], revision_id=revision['id'], **row_provenance, type=document_type,
            customer_id=profile.customer.id, control_account_id=profile.control_account.id, due_date=profile.due_date,
            subtotal_minor_units=resolved['subtotal'], tax_minor_units=resolved['tax'], profile_snapshot=json_text(profile.model_dump())))
        if 'tax_attribution' in resolved:
            pending['sales_tax_attributions'].append(dict(transaction_id=header['id'], revision_id=revision['id'],
                **row_provenance, facts_snapshot=json_text(resolved['tax_attribution'].model_dump(mode='json'))))
            pending['sales_tax_line_keys'].extend(dict(transaction_id=header['id'], line_id=key,
                tax_ordinal=ordinal, **row_provenance) for key, ordinal in resolved['tax_keys'].items())
        for position, line in enumerate(resolved['lines'], 1):
            identity = line['line_id']
            if identity is None:
                ident = dict(**created(), transaction_id=header['id'])
                pending['document_line_identities'].append(ident)
                identity = ident['id']
            facts = line['profile']
            envelope = dict(**created(), transaction_id=header['id'], revision_id=revision['id'], line_id=identity,
                position=position, kind='sale', account_id=None, side=None, amount_minor_units=None, currency=currency,
                account_snapshot=None, name_type='customer', name_id=profile.customer.id, party_name=profile.customer.label,
                class_id=facts.class_id.id if facts.class_id else None, class_name=facts.class_id.label if facts.class_id else None,
                description=line['description'], **dict.fromkeys(journals.FACTS))
            pending['document_lines'].append(envelope)
            if 'tax_attribution' in resolved:
                if line['line_id'] is None:
                    pending['sales_tax_line_keys'].append(dict(transaction_id=header['id'], line_id=identity,
                        tax_ordinal=line['tax_ordinal'], **row_provenance))
                pending['sales_tax_attribution_lines'].append(dict(transaction_id=header['id'], revision_id=revision['id'],
                    document_line_id=envelope['id'], line_id=identity, tax_ordinal=line['tax_ordinal'], **row_provenance))
            pending['sales_line_profiles'].append(dict(document_line_id=envelope['id'], transaction_id=header['id'],
                revision_id=revision['id'], **row_provenance, **{k: line[k] for k in ('item_id', 'quantity_microunits', 'unit_id',
                    'unit_factor_nanounits', 'base_quantity_microunits', *MONEY_COLUMNS)}, item_snapshot=json_text(facts.model_dump()), pricing_basis=facts.pricing_basis))
            for tax_position, component in enumerate(line['taxes'], 1):
                rule = component['rule']
                snapshot = SalesTaxComponent(position=tax_position, tax_item=rule.model_dump(include={'id', 'label', 'version'}),
                    agency=rule.agency, liability_account=rule.liability_account)
                pending['sales_tax_components'].append(dict(**created(), transaction_id=header['id'], revision_id=revision['id'],
                    document_line_id=envelope['id'], tax_item_id=rule.id, agency_id=rule.agency.id,
                    liability_account_id=rule.liability_account.id, rate_percent_millionths=rule.rate_percent_millionths,
                    taxable_minor_units=component['taxable_minor_units'], tax_minor_units=component['tax_minor_units'],
                    component_snapshot=json_text(snapshot.model_dump())))
    else:
        journals.open_dates(s, [old_revision['date']])
        revision = old_revision
    current_batch = None
    if old_header:
        header.update(version=old_header['version'] + 1, updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value)
        current_batch = effects.rows(s, c.posting_batches, c.posting_batches.c.revision_id == old_revision['id'], c.posting_batches.c.kind != 'reversal')[0]
        inverse = effects.reverse(s, header, old_revision, current_batch, event, created, pending)
        if operation == 'void':
            header.update(status='voided', voided_at=at, voided_by=s.actor.id, void_reason=ctx.reason.strip(), void_posting_batch_id=inverse['id'])
            changed_fields = ['status']
    if operation != 'void':
        batch = dict(**created(), transaction_id=header['id'], revision_id=revision['id'], kind='replacement' if old_header else 'original',
            effective_date=revision['date'], reverses_batch_id=None, replaces_batch_id=current_batch['id'] if current_batch else None, audit_event_id=event)
        pending['posting_batches'].append(batch)
        _business_postings(header, revision, batch, resolved, pending, created)
    view_profile = pending['sales_profiles'][0] if pending['sales_profiles'] else profile_row(s, revision)
    output = SalesWriteOutput(**summary(header, revision, view_profile), revision=revision_output(s, revision, pending),
        facts_fingerprint=fingerprint, warnings=warnings, changed_fields=changed_fields)
    plan = Plan(output, dict(input=inp, operation=operation, document_type=document_type, changed=True, header=header,
        before=old_header, old_revision=old_revision, pending=pending, sequence=sequence, event=event, custom_plan=custom_plan, billing_source=billing_source,
        semantic=resolved['semantic'] if resolved else None))
    from bookflow.company.billing_edits import carry_allocations
    carry_allocations(plan, s)
    if 'billing_allocations' in plan.data and operation != 'void':
        from bookflow.company.billing_queries import sale_source_output
        plan.preview.revision.billing_sources = sale_source_output(s, revision['id'], plan.data['billing_allocations'])
    from bookflow.company.sales_validation import validate
    validate(plan, s, ctx)
    return plan


def _business_postings(header, revision, batch, resolved, pending, created):
    profile = resolved['profile']
    profiles = {line['document_line_id']: line for line in pending['sales_line_profiles']}
    line_no = 0
    def leg(envelope, account, amount, debit, allocations):
        nonlocal line_no
        if not amount:
            return
        line_no += 1
        value = dict(**created(), transaction_id=header['id'], batch_id=batch['id'], line_no=line_no,
            account_id=account.id, account_snapshot=json_text(account.model_dump()), currency=revision['currency'],
            name_type='customer', name_id=profile.customer.id, party_name=profile.customer.label,
            class_id=envelope['class_id'], class_name=envelope['class_name'], description=envelope['description'],
            debit_minor_units=amount if debit else 0, credit_minor_units=0 if debit else amount,
            reversed_line_id=None, **dict.fromkeys(journals.FACTS))
        pending['posting_lines'].append(value)
        for component_id, units in allocations:
            if units:
                pending['posting_line_sources'].append(dict(**created(), transaction_id=header['id'], posting_line_id=value['id'],
                    revision_id=revision['id'], document_line_id=envelope['id'], tax_component_id=component_id,
                    amount_minor_units=units, currency=revision['currency'], reversed_source_id=None))
    for envelope in pending['document_lines']:
        line = profiles[envelope['id']]
        facts = SalesLineProfile.model_validate_json(line['item_snapshot'])
        taxes = [component for component in pending['sales_tax_components'] if component['document_line_id'] == envelope['id']]
        amounts = [(None, line['net_minor_units'])] + [(component['id'], component['tax_minor_units']) for component in taxes]
        leg(envelope, profile.control_account, line['gross_minor_units'], True, amounts)
        leg(envelope, facts.income_account, line['net_minor_units'], False, [(None, line['net_minor_units'])])
        for component in taxes:
            captured = SalesTaxComponent.model_validate_json(component['component_snapshot'])
            leg(envelope, captured.liability_account, component['tax_minor_units'], False, [(component['id'], component['tax_minor_units'])])


def apply(plan, ctx, s):
    fresh = prepare(s, ctx, plan.data['input'], plan.data['document_type'], plan.data['operation'])
    if fresh.data.get('recovered'):
        from bookflow.core.registry import Applied
        return Applied(fresh.preview, [], 'recovered invoice correction')
    if fresh.data['document_type'] == 'sales_receipt' and fresh.data['operation'] in ('update','void') and fresh.preview.changed:
        from bookflow.company.deposit_dependencies import require_unclaimed
        require_unclaimed(s, fresh.data['header']['id'])
    from bookflow.company.sales_validation import validate
    validate(fresh, s, ctx)
    if fresh.data.get('settlement_extension'):
        from bookflow.company.payment_invoice_corrections import persist
        return persist(fresh, ctx, s)
    if 'billing_allocations' in fresh.data:
        from bookflow.company.billing import persist
        return persist(fresh, ctx, s, command_name=plan.data['document_type'].replace('_', '-') + ' ' + plan.data['operation'])
    return effects.persist(fresh, ctx, s, command_name=plan.data['document_type'].replace('_', '-') + ' ' + plan.data['operation'], table_kinds=TABLE_KINDS)


def coordinate_rows_and_touches(plan):
    """Closed sales-receipt edit rows including their carried work allocations."""
    from bookflow.company.deposit_coordination import source_identity_map
    from bookflow.company import billing
    source_identity_map(plan, payment=False)
    data = plan.data
    if data['document_type'] != 'sales_receipt' or data['operation'] not in ('update', 'void'):
        raise BookflowError('E_INTERNAL')
    return tuple((table, tuple(data['pending'][table]), tuple(
        Touched(kind, row[key], 'create', None, 1, effects.decoded(row), db='company')
        for row in data['pending'][table])) for table, kind, key in TABLE_KINDS) + (billing.coordinate_rows_and_touches(plan),)
