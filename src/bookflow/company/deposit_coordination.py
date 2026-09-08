"""Private source graph identity classification and coalesced header preparation."""
from dataclasses import asdict, dataclass, is_dataclass
import json
from pydantic import BaseModel
from bookflow.core.errors import BookflowError

# Each full owner payload is preserved. New producer fields need a disposition.
PAYMENT_FIELDS = frozenset('input operation header before pending changed_headers event operation_id selected custom_plan sequence context targets fingerprint at funding old_allocations semantic originals cancellation_set'.split())
SALES_FIELDS = frozenset('input operation document_type changed header before old_revision pending sequence event custom_plan billing_source semantic billing_allocations'.split())
TABLES = frozenset('application_allocations applications document_line_identities document_lines payment_component_keys payment_components payment_profiles posting_batches posting_line_sources posting_lines sales_line_profiles sales_profiles sales_tax_attribution_lines sales_tax_attributions sales_tax_components sales_tax_line_keys settlement_line_keys transaction_revisions'.split())


@dataclass(frozen=True)
class SourceIdentity:
    owner_kind: str
    logical_key: str
    physical_id: str


@dataclass(frozen=True)
class SourceIdentityMap:
    entries: tuple[SourceIdentity, ...]


def _require(ok):
    if not ok:
        raise BookflowError('E_INTERNAL', message='Unsupported source aggregate identity or owner facts.')


def _plain(value):
    if isinstance(value, BaseModel):
        return value.model_dump(mode='json', exclude_unset=True)
    if is_dataclass(value):
        return _plain(asdict(value))
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def source_identity_map(plan, *, payment):
    data = plan.data
    _require(set(data) <= (PAYMENT_FIELDS if payment else SALES_FIELDS))
    _require(set(data.get('pending', {})) <= TABLES)
    entries = []
    for table, rows in sorted(data.get('pending', {}).items()):
        for index, row in enumerate(rows):
            if 'id' in row:
                entries.append(SourceIdentity(table, str(index), row['id']))
    for index, row in enumerate(data.get('billing_allocations', ())):
        entries.append(SourceIdentity('work_billing_allocations', str(index), row['id']))
    custom = data.get('custom_plan')
    if custom:
        for mutation in custom.owner_plan.mutations:
            if mutation.operation == 'insert':
                entries.append(SourceIdentity('custom_field_values', mutation.definition_id, mutation.row_id))
    for key in ('event', 'operation_id'):
        if data.get(key):
            entries.append(SourceIdentity('aggregate', key, data[key]))
    _require(len({entry.physical_id for entry in entries}) == len(entries))
    return SourceIdentityMap(tuple(entries))


def _canonical_source_row(table, value, mapping, at):
    allowed = SOURCE_COLUMNS[table]
    _require(set(value) <= set(allowed))
    result = dict(value)
    for key in SOURCE_REFERENCES[table]:
        if key in result:
            result[key] = mapping.get(result[key], result[key])
    for key in (('updated_at', 'voided_at') if table == 'transactions' else ('created_at', 'updated_at', 'voided_at')):
        if key in result and result[key] == at:
            result[key] = 'aggregate/at'
    if table == 'transaction_revisions' and 'custom_fields_snapshot' in result:
        snapshot = json.loads(result['custom_fields_snapshot'])
        for field in snapshot.values():
            field['value_id'] = mapping.get(field['value_id'], field['value_id'])
        result['custom_fields_snapshot'] = json.dumps(snapshot, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return result

def canonical_source_data(plan, *, payment):
    """Whole-data equivalence under classified identities, never text substitution.

    This is a private comparison projection, not a persisted source aggregate.
    Original Plan.data remains complete and untouched for its owning validator.
    """
    manifest = source_identity_map(plan, payment=payment)
    mapping = {entry.physical_id: f'{entry.owner_kind}/{entry.logical_key}' for entry in manifest.entries}
    data = _plain(plan.data)
    if payment:
        data['input'].pop('operation_key', None)  # Ordinary envelope only; never business facts.
    at = plan.data.get('at') or (plan.data.get('header') or {}).get('updated_at')
    def row(table,value):
        return _canonical_source_row(table,value,mapping,at)
    if 'pending' in data:
        data['pending'] = {table: [row(table, item) for item in values] for table, values in data['pending'].items()}
    if 'header' in data:
        data['header'] = row('transactions', data['header'])
    if 'changed_headers' in data:
        data['changed_headers'] = [[before, row('transactions', after)] for before, after in data['changed_headers']]
    if 'billing_allocations' in data:
        data['billing_allocations'] = [row('work_billing_allocations', item) for item in data['billing_allocations']]
    for key in ('event', 'operation_id'):
        if key in data:
            data[key] = mapping.get(data[key], data[key])
    if 'at' in data:
        data['at'] = 'aggregate/at'
    custom = data.get('custom_plan')
    if custom:
        for mutation in custom['owner_plan']['mutations']:
            mutation['row_id'] = mapping.get(mutation['row_id'], mutation['row_id'])
        for field in custom['snapshot'].values():
            field['value_id'] = mapping.get(field['value_id'], field['value_id'])
    return data


def coalesce_headers(changes, provenance, actor_id, interface):
    """One proven old→new assignment per header; independent of mutation ordering."""
    grouped = {}
    metadata = {'version', 'updated_at', 'updated_by', 'updated_via'}
    for before, after in changes:
        _require(before['id'] == after['id'] and set(before) == set(after))
        if after == before:
            continue
        _require(after['version'] == before['version'] + 1)
        identity = before['id']
        if identity not in grouped:
            grouped[identity] = (dict(before), {})
        original, assignments = grouped[identity]
        _require(original == before)
        for key in set(before) - metadata:
            if before[key] != after[key]:
                _require(key not in assignments or assignments[key] == after[key])
                assignments[key] = after[key]
        assignments.update(version=before['version']+1, updated_at=provenance.at,
                           updated_by=actor_id, updated_via=interface)
        if assignments['version'] > 9223372036854775807:
            raise BookflowError('E_VALUE_RANGE')
    return tuple((before, dict(before, **updates)) for key, (before, updates) in sorted(grouped.items()))


# Frozen supported row fields and identity references at provisional f150/co0021.
SOURCE_COLUMNS = {'application_allocations': ('id', 'application_id', 'kind', 'reverses_allocation_id', 'source_transaction_id', 'source_revision_id', 'source_component_id', 'source_posting_source_id', 'target_transaction_id', 'target_revision_id', 'target_document_line_id', 'target_line_id', 'target_ordinal', 'logical_kind', 'tax_item_id', 'tax_component_id', 'target_ar_source_id', 'target_recognition_source_id', 'recognition_role', 'amount_minor_units', 'currency', 'effective_date', 'facts_snapshot', 'created_at', 'created_by', 'created_via', 'audit_event_id'), 'applications': ('id', 'kind', 'paying_transaction_id', 'paid_transaction_id', 'source_component_key_id', 'amount_minor_units', 'currency', 'effective_date', 'reverses_application_id', 'created_at', 'created_by', 'created_via', 'audit_event_id'), 'document_line_identities': ('id', 'created_at', 'created_by', 'created_via', 'transaction_id'), 'document_lines': ('id', 'created_at', 'created_by', 'created_via', 'transaction_id', 'revision_id', 'line_id', 'position', 'kind', 'account_id', 'side', 'amount_minor_units', 'currency', 'account_snapshot', 'name_type', 'name_id', 'party_name', 'class_id', 'class_name', 'description', 'original_minor_units', 'original_currency', 'rate_used', 'rate_source'), 'payment_component_keys': ('id', 'transaction_id', 'line_id', 'party_id', 'ar_account_id', 'currency', 'created_at', 'created_by', 'created_via', 'audit_event_id'), 'payment_components': ('id', 'transaction_id', 'revision_id', 'document_line_id', 'component_key_id', 'amount_minor_units', 'currency', 'component_snapshot', 'created_at', 'created_by', 'created_via', 'audit_event_id'), 'payment_profiles': ('revision_id', 'transaction_id', 'type', 'payer_id', 'ar_account_id', 'deposit_account_id', 'payment_method_id', 'reference', 'profile_snapshot', 'created_at', 'created_by', 'created_via', 'audit_event_id'), 'posting_batches': ('id', 'created_at', 'created_by', 'created_via', 'transaction_id', 'revision_id', 'kind', 'effective_date', 'reverses_batch_id', 'replaces_batch_id', 'audit_event_id'), 'posting_line_sources': ('id', 'created_at', 'created_by', 'created_via', 'transaction_id', 'posting_line_id', 'revision_id', 'document_line_id', 'amount_minor_units', 'currency', 'reversed_source_id', 'tax_component_id', 'payment_component_id', 'deposit_component_id'), 'posting_lines': ('id', 'created_at', 'created_by', 'created_via', 'transaction_id', 'batch_id', 'line_no', 'account_id', 'debit_minor_units', 'credit_minor_units', 'currency', 'account_snapshot', 'name_type', 'name_id', 'party_name', 'class_id', 'class_name', 'description', 'original_minor_units', 'original_currency', 'rate_used', 'rate_source', 'reversed_line_id'), 'sales_line_profiles': ('document_line_id', 'transaction_id', 'revision_id', 'created_at', 'created_by', 'created_via', 'item_id', 'quantity_microunits', 'unit_id', 'unit_factor_nanounits', 'base_quantity_microunits', 'unit_price_minor_units', 'pricing_basis', 'net_minor_units', 'tax_minor_units', 'gross_minor_units', 'item_snapshot'), 'sales_profiles': ('revision_id', 'transaction_id', 'created_at', 'created_by', 'created_via', 'type', 'customer_id', 'control_account_id', 'due_date', 'subtotal_minor_units', 'tax_minor_units', 'profile_snapshot'), 'sales_tax_attribution_lines': ('document_line_id', 'transaction_id', 'revision_id', 'line_id', 'tax_ordinal', 'created_at', 'created_by', 'created_via'), 'sales_tax_attributions': ('revision_id', 'transaction_id', 'created_at', 'created_by', 'created_via', 'facts_snapshot'), 'sales_tax_components': ('id', 'transaction_id', 'revision_id', 'document_line_id', 'created_at', 'created_by', 'created_via', 'tax_item_id', 'agency_id', 'liability_account_id', 'rate_percent_millionths', 'taxable_minor_units', 'tax_minor_units', 'component_snapshot'), 'sales_tax_line_keys': ('line_id', 'transaction_id', 'tax_ordinal', 'created_at', 'created_by', 'created_via'), 'settlement_line_keys': ('id', 'transaction_id', 'line_id', 'ordinal', 'created_at', 'created_by', 'created_via', 'audit_event_id'), 'transaction_revisions': ('id', 'created_at', 'created_by', 'created_via', 'transaction_id', 'revision_number', 'supersedes_revision_id', 'date', 'number', 'name_type', 'name_id', 'memo', 'total_minor_units', 'currency', 'issuer_snapshot', 'custom_fields_snapshot', 'audit_event_id'), 'transactions': ('id', 'version', 'created_at', 'created_by', 'created_via', 'updated_at', 'updated_by', 'updated_via', 'type', 'number', 'current_revision_id', 'status', 'voided_at', 'voided_by', 'void_reason', 'void_posting_batch_id'), 'work_billing_allocations': ('id', 'transaction_id', 'revision_id', 'document_line_id', 'source_document_id', 'source_revision_id', 'source_line_id', 'root_document_id', 'root_line_id', 'quantity_microunits', 'net_minor_units', 'tax_minor_units', 'gross_minor_units', 'facts_snapshot', 'created_at', 'created_by', 'created_via', 'allocation_version', 'source_basis_hash', 'denominator_hex', 'spans_json')}

SOURCE_REFERENCES = {'application_allocations': ('id', 'application_id', 'reverses_allocation_id', 'source_transaction_id', 'source_revision_id', 'source_component_id', 'source_posting_source_id', 'target_transaction_id', 'target_revision_id', 'target_document_line_id', 'target_line_id', 'target_ordinal', 'tax_component_id', 'target_ar_source_id', 'target_recognition_source_id', 'audit_event_id'), 'applications': ('id', 'paying_transaction_id', 'paid_transaction_id', 'source_component_key_id', 'reverses_application_id', 'audit_event_id'), 'document_line_identities': ('id', 'transaction_id'), 'document_lines': ('id', 'transaction_id', 'revision_id', 'line_id', 'account_id', 'class_id'), 'payment_component_keys': ('id', 'transaction_id', 'line_id', 'party_id', 'ar_account_id', 'audit_event_id'), 'payment_components': ('id', 'transaction_id', 'revision_id', 'document_line_id', 'component_key_id', 'audit_event_id'), 'payment_profiles': ('revision_id', 'transaction_id', 'type', 'payer_id', 'ar_account_id', 'deposit_account_id', 'payment_method_id', 'audit_event_id'), 'posting_batches': ('id', 'transaction_id', 'revision_id', 'reverses_batch_id', 'replaces_batch_id', 'audit_event_id'), 'posting_line_sources': ('id', 'transaction_id', 'posting_line_id', 'revision_id', 'document_line_id', 'reversed_source_id', 'tax_component_id', 'payment_component_id', 'deposit_component_id'), 'posting_lines': ('id', 'transaction_id', 'batch_id', 'account_id', 'class_id', 'reversed_line_id'), 'sales_line_profiles': ('document_line_id', 'transaction_id', 'revision_id', 'item_id', 'unit_id'), 'sales_profiles': ('revision_id', 'transaction_id', 'type', 'customer_id', 'control_account_id'), 'sales_tax_attribution_lines': ('document_line_id', 'transaction_id', 'revision_id', 'line_id', 'tax_ordinal'), 'sales_tax_attributions': ('revision_id', 'transaction_id'), 'sales_tax_components': ('id', 'transaction_id', 'revision_id', 'document_line_id', 'tax_item_id', 'agency_id', 'liability_account_id'), 'sales_tax_line_keys': ('line_id', 'transaction_id'), 'settlement_line_keys': ('id', 'transaction_id', 'line_id', 'audit_event_id'), 'transaction_revisions': ('id', 'transaction_id', 'supersedes_revision_id', 'audit_event_id'), 'transactions': ('id', 'current_revision_id', 'void_posting_batch_id'), 'work_billing_allocations': ('id', 'transaction_id', 'revision_id', 'document_line_id', 'source_document_id', 'source_revision_id', 'source_line_id', 'root_document_id', 'root_line_id')}


def prepare_source_overlay(s, ctx, inp, source, binding):
    """Prove the current claim and source slice before deriving the retained row.

    Does not issue a coordinate guard or prepare the complete deposit replacement.
    The coordinator consumes this proof through the shared replacement resolver.
    No current source validation is bypassed.
    """
    from bookflow.company import schema as c, deposit_dependencies as dependencies
    from bookflow.company import deposit_dependency_history as history, document_effects as effects
    from bookflow.company import deposit_sources, deposits, payment_corrections, payment_cancellation, sales_validation
    from bookflow.company.deposit_coordinate_models import CoordinateInput, PreparedSource, SourceResult, SourceResultOverlay, source_identity
    from bookflow.company.deposit_models import Effect, SourceRow
    from bookflow.company.payment_queries import canonical
    _require(type(inp) is CoordinateInput and type(source) is PreparedSource)
    inp = CoordinateInput.model_validate_json(inp.model_dump_json(by_alias=True, exclude_unset=True))
    identity = source_identity(inp.source_action)
    from bookflow.company import deposit_draft_provider as provider
    document=provider.coordinate(s,ctx,inp,binding)
    rows=document.sources if document is not None else []
    targets = dependencies.authorize(s, inp.deposit, [identity, *(row.source for row in rows)], write=True)
    history._authorize_binding_graph(s, binding, targets, write=True)
    if ctx.on_behalf_of != binding.on_behalf_of:
        raise BookflowError('E_UNAUTHENTICATED')
    if not ctx.reason or not ctx.reason.strip() or len(ctx.reason) > 140:
        raise BookflowError('E_REASON_REQUIRED')
    _require(source.action == inp.source_action)
    claimed = dependencies.active_claim(s, identity)
    if claimed is None or claimed['transaction_id'] != inp.deposit:
        raise BookflowError('E_DEPOSIT_DEPENDENCY')
    header = effects.rows(s, c.transactions, c.transactions.c.id == identity)[0]
    deposit = effects.rows(s, c.transactions, c.transactions.c.id == inp.deposit, c.transactions.c.type == 'deposit')
    if len(deposit) != 1:
        raise BookflowError('E_RECORD_NOT_FOUND')
    history.version_meta(s, deposit[0], inp.expected_version, binding)
    if deposit[0]['status'] != 'posted':
        raise BookflowError('E_VALIDATION')
    expected = inp.source_action.input.expected_version if inp.source_action.kind.endswith('_update') else inp.source_action.expected_version
    history.version_meta(s, header, expected, binding)
    data = source.plan.data
    if source.action.kind.startswith('payment_'):
        validator = payment_corrections.validate if source.action.kind.endswith('_update') else payment_cancellation.validate
    else:
        validator = sales_validation.validate
    validator(source.plan, s, ctx)
    source_identity_map(source.plan, payment=source.action.kind.startswith('payment_'))
    if data.get('before') is not None:
        _require(data['before'] == header)
    # Reconstruct the prospective cash directly from the full independently
    # validated owner graph. A provided CashSource is never its own proof.
    if source.action.kind.endswith('_void'):
        actual_cash = None
    elif not source.plan.preview.changed:
        actual_cash = deposit_sources.load(s, identity)
    else:
        import sqlalchemy as sa
        uf = s.company.conn.execute(sa.select(c.accounts.c.id).where(c.accounts.c.system_role == 'undeposited_funds')).scalar_one()
        graph = deposit_sources.graph(s, identity, data['pending'], data['header'])
        payment = source.action.kind.startswith('payment_')
        profiles = graph['payment_profiles' if payment else 'sales_profiles']
        profile = next(row for row in profiles if row['revision_id'] == data['header']['current_revision_id'])
        actual_cash = None if profile['deposit_account_id' if payment else 'control_account_id'] != uf else deposit_sources.project(graph, uf_account=uf, home_currency=s.company_info_row['home_currency'])
    _require(actual_cash == source.cash)
    membership = effects.rows(s, c.deposit_memberships, c.deposit_memberships.c.id == claimed['membership_id'])[0]
    _require(membership['kind'] == 'claim' and membership['transaction_id'] == inp.deposit and membership['source_transaction_id'] == identity)
    profile = effects.rows(s, c.deposit_profiles, c.deposit_profiles.c.revision_id == deposit[0]['current_revision_id'])[0]
    previous = Effect.model_validate_json(profile['facts_snapshot'])
    retained = [row for row in previous.intent.sources if row.source.transaction_id == identity]
    _require(len(retained) == 1 and retained[0].row_id == membership['row_id'])
    from bookflow.company.deposit_models import CashSource
    captured = CashSource.model_validate_json(membership['facts_snapshot'])
    _require(captured == retained[0].source)
    _require(all(membership[field] == expected for field, expected in {
        'source_revision_id': captured.revision_id, 'source_batch_id': captured.business_batch_id,
        'source_date': captured.receipt_date, 'amount_minor_units': captured.cash_minor_units,
        'currency': captured.currency}.items()))
    # Application-only events may bump the header, but never alter the claimed
    # commercial source facts without a coordinated membership replacement.
    current = deposit_sources.load(s, identity)
    _require(current.model_dump(exclude={'expected_header_version'}) ==
             captured.model_dump(exclude={'expected_header_version'}))
    requested = [row for row in rows if isinstance(row, SourceResult)]
    replacement = None
    if requested:
        if actual_cash is None:
            raise BookflowError('E_DEPOSIT_SOURCE_INELIGIBLE')
        row = requested[0]; before = retained[0]
        entered = 'memo_override' in row.model_fields_set
        replacement = SourceRow(row_id=before.row_id, ordinal=before.ordinal, source=actual_cash,
            occurrences=deposits.occurrences(actual_cash, before.occurrences),
            memo=row.memo_override if entered else actual_cash.source_memo,
            memo_origin='entered' if entered else 'source')
        if actual_cash.receipt_date > document.date:
            raise BookflowError('E_DEPOSIT_DATE_BEFORE_SOURCE')
    dependencies.reconciliation_status(s.company)
    return SourceResultOverlay(inp.deposit, identity, canonical(header), canonical(membership), replacement, source)


def resolve_coordinate(s, ctx, inp, binding):
    """Resolve the full source/deposit financial proposal without DML or a guard."""
    from bookflow.company import schema as c, deposit_dependencies as dependencies
    from bookflow.company import deposit_dependency_history as history, document_effects as effects
    from bookflow.company import deposit_lifecycle as lifecycle, deposit_validation, deposit_composition, bank_effects, journals
    from bookflow.company.deposit_models import Effect
    from bookflow.company.deposit_coordinate_models import CoordinateInput, CoordinateResolution, source_identity
    from bookflow.company.payment_models import EffectProvenance
    from bookflow.company.payment_queries import canonical
    from bookflow.core import clock
    from bookflow.core.ids import new_id
    _require(type(inp) is CoordinateInput)
    inp = CoordinateInput.model_validate_json(inp.model_dump_json(by_alias=True, exclude_unset=True))
    from bookflow.company import deposit_draft_provider as provider
    document=provider.coordinate(s,ctx,inp,binding)
    rows=document.sources if document is not None else []
    identity = source_identity(inp.source_action)
    targets = dependencies.authorize(s, inp.deposit, [identity, *(row.source for row in rows)], write=True)
    history._authorize_binding_graph(s, binding, targets, write=True)
    if ctx.on_behalf_of != binding.on_behalf_of:
        raise BookflowError('E_UNAUTHENTICATED')
    if not ctx.reason or not ctx.reason.strip() or len(ctx.reason) > 140:
        raise BookflowError('E_REASON_REQUIRED')
    provenance = EffectProvenance(at=clock.now_iso(), event_id=new_id(), operation_id=new_id())
    source = deposit_composition.preview_source_effect(s, ctx, inp.source_action, provenance=provenance)
    overlay = prepare_source_overlay(s, ctx, inp, source, binding)
    old = effects.rows(s, c.transactions, c.transactions.c.id == inp.deposit)[0]
    prior = effects.rows(s, c.transaction_revisions, c.transaction_revisions.c.id == old['current_revision_id'])[0]
    profile = effects.rows(s, c.deposit_profiles, c.deposit_profiles.c.revision_id == prior['id'])[0]
    previous = Effect.model_validate_json(profile['facts_snapshot'])
    keys = effects.rows(s, c.deposit_row_keys, c.deposit_row_keys.c.transaction_id == inp.deposit)
    headers = [row for row in keys if row['kind'] == 'header']
    _require(len(headers) == 1)
    maximum = max(row['ordinal'] for row in keys)
    mapping = {}; custom = None; sequence = None
    if inp.replacement.mode == 'document':
        financial, number, memo, custom, changed, sequence, maximum = lifecycle.resolve_replacement(
            s, ctx, document, identity=inp.deposit, old=old, prior=prior, previous=previous,
            keys=keys, maximum=maximum, mapping=mapping, binding=binding, overlay=overlay)
        deposit_validation.validate(financial)
        # The source action is independently proven above. Every other source
        # still has its exact stored adapter facts and original current version.
        from bookflow.company import deposit_sources
        for row in financial.intent.sources:
            if row.source.transaction_id != identity:
                _require(row.source == deposit_sources.load(s, row.source.transaction_id))
        if changed:
            if document.pin is None:
                deposit_validation.validate_references(financial, s, previous=previous)
            else:
                from bookflow.company import deposit_draft_provider as provider
                from bookflow.company.deposit_draft_models import Manifest
                provider.validate_references(s,financial,Manifest.model_validate_json(document.pin.snapshot),old['id'])
    else:
        financial=previous;number=prior['number'];memo=prior['memo'];changed=True
    if changed:
        journals.open_dates(s, [prior['date'], financial.intent.date])
    projections = effects.rows(s, c.deposit_current_memberships, c.deposit_current_memberships.c.transaction_id == inp.deposit)
    claims = [effects.rows(s, c.deposit_memberships, c.deposit_memberships.c.id == row['membership_id'])[0] for row in projections]
    source_ids = {row['source_transaction_id'] for row in claims} | {row.source for row in rows}
    source_headers = {key: effects.rows(s, c.transactions, c.transactions.c.id == key)[0] for key in sorted(source_ids)}
    changes = []
    source_data = source.plan.data
    if source.plan.preview.changed:
        changes.append((source_data['before'], source_data['header']))
        changes.extend(source_data.get('changed_headers', ()))
    if changed:
        changes.extend((before,dict(before,version=before['version']+1)) for before in source_headers.values())
        # Revision/batch IDs are allocated by C's row builder; the header's
        # commercial assignment remains explicitly represented by financial.
        changes.append((old,dict(old,version=old['version']+1,number=number)))
    coalesced = coalesce_headers(changes, provenance, s.actor.id, ctx.interface.value)
    before_bank = bank_effects.enumerate_deposit(previous,header_row_id=headers[0]['id'])
    after_bank = bank_effects.enumerate_deposit(financial,header_row_id=headers[0]['id'],previous=before_bank,void=inp.replacement.mode=='void')
    data = dict(before=old,prior=prior,previous=previous.model_dump(mode='json'),financial=financial.model_dump(mode='json'),
        changed=changed,identity=inp.deposit,header_row=headers[0]['id'],header_ordinal=headers[0]['ordinal'],number=number,memo=memo,
        source_headers=source_headers,claims=claims,targets=targets,sequence=sequence,mapping=mapping,
        at=provenance.at,event=provenance.event_id,operation_id=provenance.operation_id,issuer=json.loads(prior['issuer_snapshot']))
    if document is not None and document.pin is not None:data['draft']=document.pin.model_dump(mode='json')
    return CoordinateResolution(inp,source,overlay,canonical(data),custom,canonical(coalesced),before_bank,after_bank,
                                changed or source.plan.preview.changed)


def canonical_coordinate(resolved):
    """Complete comparison with only explicitly owned prospective identities mapped."""
    from bookflow.company.deposit_models import Effect
    data = json.loads(resolved.deposit_data_json)
    manifest = source_identity_map(resolved.source.plan, payment=resolved.source.action.kind.startswith('payment_'))
    mapping = {row.physical_id: row.owner_kind+'/'+row.logical_key for row in manifest.entries}
    _require(not (set(mapping) & set(data['mapping'])))
    mapping.update(data['mapping'])
    fields = {
        'CashSource': {'revision_id','business_batch_id'},
        'CashComponent': {'document_line_id','posting_line_id','posting_source_id','physical_component_id'},
        'SemanticKey': {'identity'}, 'SourceRow': {'row_id'}, 'Additional': {'row_id'}, 'Cell': {'row_id'},
    }
    def bucket(value):
        if value.startswith('additional:'):
            return 'additional:'+mapping.get(value[len('additional:'):],value[len('additional:'):])
        _require(value in ('main_bank','cash_back','bank'))
        return value
    def visit(value):
        if isinstance(value,BaseModel):
            name=type(value).__name__;result={}
            for key in type(value).model_fields:
                item=getattr(value,key)
                if key in fields.get(name,()):
                    result[key]=mapping.get(item,item)
                elif name=='Cell' and key=='bucket':
                    result[key]=bucket(item)
                elif name=='Leg' and key=='key':
                    parts=item.split('/')
                    if len(parts)==1:
                        result[key]=bucket(item)
                    else:
                        _require(len(parts)==3 and parts[2].isdigit())
                        prefix='uf' if parts[0]=='uf' else bucket(parts[0])
                        result[key]='/'.join((prefix,mapping.get(parts[1],parts[1]),parts[2]))
                else:
                    result[key]=visit(item)
            return result
        if isinstance(value,(tuple,list)):return [visit(item) for item in value]
        if isinstance(value,dict):return {key:visit(item) for key,item in value.items()}
        return value
    financial=Effect.model_validate_json(json.dumps(data['financial']))
    custom=_plain(resolved.deposit_custom_plan)
    if custom:
        for mutation in custom['owner_plan']['mutations']:
            # C must assign a stable aggregate logical slot for new deposit
            # values; the explicit definition identity is already immutable.
            if mutation['operation']=='insert':
                mapping[mutation['row_id']]='deposit_custom/'+mutation['definition_id']
            mutation['row_id']=mapping.get(mutation['row_id'],mutation['row_id'])
        for field in custom['snapshot'].values():
            field['value_id']=mapping.get(field['value_id'],field['value_id'])
    headers=json.loads(resolved.headers_json)
    for before,after in headers:
        for key in ('current_revision_id','void_posting_batch_id'):
            after[key]=mapping.get(after[key],after[key])
        for key in ('updated_at','voided_at'):
            if after.get(key)==resolved.source.provenance.at:after[key]='aggregate/at'
    request=resolved.input.model_dump(mode='json',by_alias=True,exclude_unset=True)
    request.pop('dependency_guard',None);request.pop('expected_facts_fingerprint',None)
    return dict(draft=data.get('draft'),request=request,source=canonical_source_data(resolved.source.plan,payment=resolved.source.action.kind.startswith('payment_')),
        source_fingerprint=resolved.source.source_fingerprint,financial=visit(financial),before=data['before'],prior=data['prior'],
        claims=data['claims'],source_headers=data['source_headers'],headers=headers,number=data['number'],memo=data['memo'],
        issuer=data['issuer'],sequence=data['sequence'],custom=custom,changed=resolved.changed,
        bank_before=[v.model_dump(mode='json') for v in resolved.deposit_bank_before],
        bank_after=[{**v.model_dump(mode='json'),'row_id':mapping.get(v.row_id,v.row_id)} for v in resolved.deposit_bank_after],
        source_bank=canonical_source_bank(resolved.source, mapping))


def prepare(s, ctx, inp, *, binding, expected_source_fingerprint=None):
    """Private complete-intent preparation; no operation recovery or persistence."""
    from bookflow.company import deposit_dependency_history as history
    from bookflow.company.deposit_dependency_models import PageInput
    from bookflow.company.deposit_dependency_pages import changes_page
    from bookflow.company.deposit_coordinate_models import CoordinateInput, PreparedCoordinate
    from bookflow.company.payment_queries import digest
    _require(type(inp) is CoordinateInput)
    inp=CoordinateInput.model_validate_json(inp.model_dump_json(by_alias=True,exclude_unset=True))
    original=history.request(dict(command='deposit coordinate',input=inp.model_dump(mode='json',by_alias=True,exclude_unset=True),
        context={key:value for key,value in {'reason':ctx.reason,'directive_id':ctx.directive_id}.items() if value is not None}))
    history.execution_binding(s,binding)
    if ctx.on_behalf_of != binding.on_behalf_of:raise BookflowError('E_UNAUTHENTICATED')
    if inp.dependency_guard is not None:
        comparison=history.compare(s,inp.dependency_guard,original,binding)
        if not comparison.matches:
            page=changes_page(s,inp.dependency_guard,original,PageInput(),binding)
            raise BookflowError('E_PREVIEW_STALE',details={'reason':'deposit_dependencies',
                'history':'unknown_history' if comparison.unknown_history else 'known_stale',
                'changes':page.model_dump(mode='json'),'original_request':original.model_dump(mode='json',exclude_unset=True)})
    resolution=resolve_coordinate(s,ctx,inp,binding)
    if expected_source_fingerprint is not None and resolution.source.source_fingerprint != expected_source_fingerprint:
        raise BookflowError('E_PREVIEW_STALE',details={'reason':'source_facts'})
    recipe,readset=history.capture(s,original,binding)
    guard=history.issue(s,recipe,readset,binding)
    fingerprint=digest(canonical_coordinate(resolution))
    if inp.expected_facts_fingerprint is not None and inp.expected_facts_fingerprint != fingerprint:
        raise BookflowError('E_PREVIEW_STALE',details={'reason':'coordinate_facts'})
    return PreparedCoordinate(resolution,fingerprint,guard,readset.model_dump_json(),binding)


def canonical_source_bank(source, mapping):
    """R0 identity/provenance projection; every full owned row is retained."""
    from bookflow.company import schema as c
    from bookflow.company.payment_queries import canonical
    data=source.plan.data;at=source.provenance.at
    provenance={}
    owned=dict(data.get('pending',{}))
    if data.get('billing_allocations'):
        owned['work_billing_allocations']=data['billing_allocations']
    for table,rows in owned.items():
        for row in rows:
            physical={column.name:row.get(column.name) for column in getattr(c,table).columns}
            provenance[canonical(physical)]=canonical(_canonical_source_row(table,physical,mapping,at))
    result=source.bank_changes.model_dump(mode='json')
    if result['kind']!='changed_effects':return result
    for side in ('before','after'):
        for value in result[side]:
            for key in ('revision_id','business_batch_id','transition_batch_id','audit_event_id'):
                value[key]=mapping.get(value[key],value[key])
            for child in ('ref','movement_key'):
                for key in ('revision_id','component_id'):
                    if key in value[child]:value[child][key]=mapping.get(value[child][key],value[child][key])
            parts=value['version_id'].split(':')
            _require(len(parts)==3)
            value['version_id']=':'.join(mapping.get(part,part) for part in parts)
            for key in ('posting_line_ids','source_ids','document_line_ids'):
                value[key]=sorted(mapping.get(item,item) for item in value[key])
            value['provenance']=[provenance.get(item,item) for item in value['provenance']]
    return result


def validate(s, ctx, prepared):
    """Independent owner/arithmetic/header checks plus exact full re-preparation.

    No DML is performed here. C must invoke this inside its writer snapshot and
    independently validate its later row/touch assembly before persisting it.
    """
    from bookflow.company import schema as c, document_effects as effects, deposit_validation, bank_effects
    from bookflow.company import reconciliation_adapters, deposit_dependency_history as history
    from bookflow.company.deposit_coordinate_models import PreparedCoordinate
    from bookflow.company.deposit_models import Effect
    from bookflow.company.payment_queries import digest
    _require(type(prepared) is PreparedCoordinate)
    resolved=prepared.resolution;inp=resolved.input;source=resolved.source
    # Authenticate and compare the complete old intent before stale row-version
    # checks, so a competing writer retains attributed history.
    anchored=inp.model_copy(update={'dependency_guard':prepared.dependency_guard,'expected_facts_fingerprint':prepared.facts_fingerprint})
    fresh=prepare(s,ctx,anchored,binding=prepared.binding,expected_source_fingerprint=source.source_fingerprint)
    data=json.loads(resolved.deposit_data_json)
    financial=Effect.model_validate_json(json.dumps(data['financial']))
    previous=Effect.model_validate_json(json.dumps(data['previous']))
    deposit_validation.validate(financial);deposit_validation.validate(previous)
    _require(financial.intent.deposit_id==previous.intent.deposit_id==inp.deposit)
    overlay=prepare_source_overlay(s,ctx,inp,source,prepared.binding)
    _require(overlay==resolved.overlay)
    _require(reconciliation_adapters.prospective(s,ctx,source.plan)==source.bank_changes)
    expected={}
    source_data=source.plan.data
    if source.plan.preview.changed:
        expected[source_data['before']['id']]=source_data['header']
        for old,new in source_data.get('changed_headers',()):
            _require(old['id'] not in expected)
            expected[old['id']]=new
    if data['changed']:
        for identity,header in data['source_headers'].items():
            expected.setdefault(identity,header)
        expected.setdefault(inp.deposit,data['before'])
    headers=json.loads(resolved.headers_json)
    _require(len(headers)==len(expected) and {old['id'] for old,new in headers}==set(expected))
    metadata={'version','updated_at','updated_by','updated_via'}
    for before,after in headers:
        actual=effects.rows(s,c.transactions,c.transactions.c.id==before['id'])
        _require(len(actual)==1 and actual[0]==before and after['id']==before['id'])
        _require(after['version']==before['version']+1 and after['version']<=9223372036854775807)
        _require((after['updated_at'],after['updated_by'],after['updated_via'])==
                 (source.provenance.at,s.actor.id,ctx.interface.value))
        wanted=dict(expected[before['id']])
        if before['id']==inp.deposit:wanted['number']=data['number']
        _require({k:v for k,v in after.items() if k not in metadata}=={k:v for k,v in wanted.items() if k not in metadata})
    for table,rows in source_data.get('pending',{}).items():
        for row in rows:
            if 'audit_event_id' in row:_require(row['audit_event_id']==source.provenance.event_id)
            if 'created_at' in row:_require(row['created_at']==source.provenance.at)
            if 'created_by' in row:_require(row['created_by']==s.actor.id)
            if 'created_via' in row:_require(row['created_via']==ctx.interface.value)
    before_bank=bank_effects.enumerate_deposit(previous,header_row_id=data['header_row'])
    after_bank=bank_effects.enumerate_deposit(financial,header_row_id=data['header_row'],previous=before_bank,
        void=inp.replacement.mode=='void')
    _require(before_bank==resolved.deposit_bank_before and after_bank==resolved.deposit_bank_after)
    _require(digest(canonical_coordinate(resolved))==prepared.facts_fingerprint)
    # A guard authenticates the exact submitted complete intent, not a mutable
    # prepared-object digest. Rebuilding catches changed request/data assignments.
    _require(canonical_coordinate(fresh.resolution)==canonical_coordinate(resolved))
    _require(canonical_source_preview(fresh.resolution.source)==canonical_source_preview(source))
    return fresh


PREVIEW_ID_FIELDS=frozenset('id value_id operation_id audit_event_id event_id revision_id current_revision_id posting_batch_id business_batch_id batch_id void_posting_batch_id document_line_id line_id source_revision_id target_revision_id source_posting_source_id source_component_id application_id allocation_id component_key_id component_id posting_line_id posting_source_id'.split())


def canonical_source_preview(source):
    """Typed source output equivalence; labels, memos and custom values stay exact."""
    manifest=source_identity_map(source.plan,payment=source.action.kind.startswith('payment_'))
    mapping={entry.physical_id:entry.owner_kind+'/'+entry.logical_key for entry in manifest.entries}
    def visit(value,key=None):
        if isinstance(value,dict):return {name:visit(item,name) for name,item in value.items()}
        if isinstance(value,list):return [visit(item,key) for item in value]
        if isinstance(value,str):
            if key in PREVIEW_ID_FIELDS:return mapping.get(value,value)
            if key in ('at','created_at','updated_at','voided_at') and value==source.provenance.at:return 'aggregate/at'
        return value
    return visit(source.plan.preview.model_dump(mode='json'))
