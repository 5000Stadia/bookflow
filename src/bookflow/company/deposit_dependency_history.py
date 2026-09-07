"""Private authenticated deposit history. No dispatch, persistence or public API.

Execution bindings are the existing adapter objects. SQL snapshots and history
are company-owned business evidence, never a substitute for current admission.
"""
import base64
import hashlib
import hmac
import json

import sqlalchemy as sa
from pydantic import TypeAdapter, ValidationError

from bookflow.company import schema as c
from bookflow.company.deposit_dependency_models import BaselineRecipe, DepositRequest, InspectionRoot
from bookflow.company.ledger_reports import _cursor_key
from bookflow.core.errors import BookflowError
from bookflow.core.publication import OSBinding
from bookflow.hub import credentials

DOMAIN = b'deposit-dependencies-v1\0'
_REQUEST = TypeAdapter(DepositRequest)


def invalid_guard():
    return BookflowError('E_VALIDATION', details={'field': 'dependency_guard', 'reason': 'invalid_guard'})


def execution_binding(s, binding):
    """Revalidate the actual existing OS/HTTP producer against this hub snapshot.

    A credential's identity is not supplied by request Context. The current
    authority epoch belongs only in the MAC transcript, never the wire recipe.
    """
    from bookflow.adapters.http.app import Credential
    if type(binding) not in (OSBinding, Credential) or s.actor is None or s.hub is None:
        raise BookflowError('E_UNAUTHENTICATED')
    binding.revalidate(s.hub)
    if (binding.user_id, binding.actor_kind) != (s.actor.id, s.actor.kind):
        raise BookflowError('E_UNAUTHENTICATED')
    if isinstance(binding, OSBinding) and binding.root != s.data_root:
        raise BookflowError('E_UNAUTHENTICATED')
    eligible, epoch = credentials._binding(s.hub, binding.user_id, binding.on_behalf_of)
    if not eligible:
        raise BookflowError('E_UNAUTHENTICATED')
    return binding.user_id, binding.actor_kind, binding.on_behalf_of, epoch


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def request(value):
    """Round trip even model instances: model_copy is not validation."""
    if isinstance(value, InspectionRoot):
        return InspectionRoot.model_validate_json(value.model_dump_json())
    raw = value.model_dump_json(by_alias=True, exclude_unset=True) if hasattr(value, 'model_dump_json') else canonical(value)
    return _REQUEST.validate_json(raw)


def intent_digest(value):
    value = request(value)
    wire = value.model_dump(mode='json', by_alias=True, exclude_unset=True)
    if not isinstance(value, InspectionRoot):
        wire['input'].pop('dependency_guard', None)
        wire['input'].pop('expected_facts_fingerprint', None)
    return hashlib.sha256(canonical(wire).encode()).hexdigest()


def _encode(s, value, binding, *, domain=DOMAIN):
    principal = execution_binding(s, binding)
    raw = canonical(value).encode()
    signature = hmac.digest(_cursor_key(s.company), domain + canonical(principal).encode() + b'\0' + raw, 'sha256')
    result = base64.urlsafe_b64encode(raw + signature).decode().rstrip('=')
    if len(result) > 2048:
        raise ValueError('deposit continuation recipe exceeds its private contract')
    return result


def _decode(s, token, binding, *, domain=DOMAIN):
    principal = execution_binding(s, binding)
    if type(token) is not str or not token or len(token) > 2048 or '=' in token:
        raise invalid_guard()
    try:
        raw = base64.b64decode(token + '=' * (-len(token) % 4), altchars=b'-_', validate=True)
        if len(raw) <= 32 or base64.urlsafe_b64encode(raw).decode().rstrip('=') != token:
            raise ValueError('noncanonical guard')
        payload, signature = raw[:-32], raw[-32:]
        expected = hmac.digest(_cursor_key(s.company), domain + canonical(principal).encode() + b'\0' + payload, 'sha256')
        if not hmac.compare_digest(expected, signature):
            raise ValueError('guard signature')
        value = json.loads(payload)
        if canonical(value).encode() != payload:
            raise ValueError('guard representation')
    except (ValueError, UnicodeError):
        raise invalid_guard() from None
    return value


def decode_recipe(s, token, original_request, binding):
    value = _decode(s, token, binding)
    try:
        recipe = BaselineRecipe.model_validate_json(canonical(value), strict=True)
    except ValidationError:
        raise invalid_guard() from None
    actor, kind, principal, _ = execution_binding(s, binding)
    if (recipe.company_id != s.company_row['id'] or recipe.intent_digest != intent_digest(original_request)
            or (recipe.actor_id, recipe.actor_kind, recipe.principal_id) != (actor, kind, principal)):
        raise invalid_guard()
    original = request(original_request)
    if (recipe.mode == 'inspection') != isinstance(original, InspectionRoot):
        raise invalid_guard()
    if isinstance(original, InspectionRoot) and recipe.root != original:
        raise invalid_guard()
    return recipe

# Audit aliases are storage history, not generated English singulars. Keep the
# accepted co21 spelling alongside the older ledger spelling.
from dataclasses import dataclass
from bookflow.core import audit
from bookflow.company.deposit_dependency_models import RecordAnchor


@dataclass(frozen=True)
class Owner:
    table: str
    key: str
    audit_kinds: tuple[str, ...]
    fields: tuple[str, ...] | None = None


OWNERS = {
    'transaction': Owner('transactions', 'id', ('transaction',)),
    'company_info': Owner('company_info', 'id', ('company_info',), ('id', 'home_currency', 'closing_date')),
    'account': Owner('accounts', 'id', ('account',), ('id', 'name', 'full_name', 'number', 'type', 'system_role', 'active', 'currency')),
    'customer': Owner('customers', 'id', ('customer',), ('id', 'name', 'full_name', 'active')),
    'vendor': Owner('vendors', 'id', ('vendor',), ('id', 'name', 'full_name', 'active')),
    'employee': Owner('employees', 'id', ('employee',), ('id', 'name', 'active')),
    'other_name': Owner('other_names', 'id', ('other_name',), ('id', 'name', 'active')),
    'class': Owner('classes', 'id', ('class',), ('id', 'name', 'full_name', 'active')),
    'payment_method': Owner('payment_methods', 'id', ('payment_method',), ('id', 'name', 'active')),
    'custom_field': Owner('custom_field_defs', 'id', ('custom_field',)),
    'work_document': Owner('work_documents', 'id', ('work_document',)),
}

# Explicit table/key/kind inventory. Adding a new financial owner requires its
# decoder/closure decision here; unknown kinds are programmer errors.
_IMMUTABLE = (
    ('transaction_revisions', 'id', 'transaction_revision'),
    ('document_line_identities', 'id', 'document_line_identity'),
    ('document_lines', 'id', 'document_line'),
    ('posting_batches', 'id', 'posting_batch'),
    ('posting_lines', 'id', 'posting_line'),
    ('posting_line_sources', 'id', 'posting_line_source'),
    ('payment_profiles', 'revision_id', 'payment_profile'),
    ('payment_component_keys', 'id', 'payment_component_key'),
    ('payment_components', 'id', 'payment_component'),
    ('sales_profiles', 'revision_id', 'sales_profile'),
    ('sales_line_profiles', 'document_line_id', 'sales_line_profile'),
    ('sales_tax_components', 'id', 'sales_tax_component'),
    ('sales_tax_line_keys', 'line_id', 'sales_tax_line_key'),
    ('sales_tax_attributions', 'revision_id', 'sales_tax_attribution'),
    ('sales_tax_attribution_lines', 'document_line_id', 'sales_tax_attribution_line'),
    ('settlement_line_keys', 'id', 'settlement_line_key'),
    ('applications', 'id', 'application'),
    ('application_allocations', 'id', 'application_allocation'),
    ('deposit_profiles', 'revision_id', 'deposit_profile'),
    ('deposit_row_keys', 'id', 'deposit_row_key'),
    ('deposit_component_keys', 'id', 'deposit_component_key'),
    ('deposit_components', 'id', 'deposit_component'),
    ('deposit_cash_cells', 'id', 'deposit_cash_cell'),
    ('deposit_memberships', 'id', 'deposit_membership'),
    ('bank_effect_keys', 'id', 'bank_effect_key'),
    ('bank_effect_versions', 'id', 'bank_effect_version'),
    ('work_billing_conversions', 'id', 'work_billing_conversion'),
    ('work_billing_allocations', 'id', 'work_billing_allocation'),
    ('work_revisions', 'id', 'work_revision'),
    ('work_line_identities', 'id', 'work_line'),
    ('work_lines', 'id', 'work_revision_line'),
    ('work_links', 'id', 'work_link'),
    ('work_tax_line_keys', 'line_id', 'work_tax_line_key'),
    ('work_tax_attributions', 'revision_id', 'work_tax_attribution'),
    ('work_tax_attribution_lines', 'work_line_id', 'work_tax_attribution_line'),
)
for _table, _key, _kind in _IMMUTABLE:
    OWNERS[_kind] = Owner(_table, _key, tuple(dict.fromkeys((_kind, _table.rstrip('s')))) if _table in {'document_line_identities', 'posting_batches'} else (_kind,))

_PROVENANCE = frozenset(('created_at', 'created_by', 'created_via', 'updated_at', 'updated_by', 'updated_via', 'version', 'audit_event_id'))


class MissingHistory(ValueError):
    """Only malformed, missing or contradictory owned history is classified here."""


def _decoded(value):
    return {key: json.loads(item) if key.endswith('_snapshot') and isinstance(item, str) else item
            for key, item in value.items()}


def _project(kind, value):
    owner = OWNERS[kind]
    if not isinstance(value, dict) or type(value.get(owner.key)) is not str:
        raise MissingHistory('missing owned identity')
    # Validate the actual financial snapshot schema before including it as proof.
    from bookflow.company.deposit_models import Effect, CashSource
    from bookflow.company.payment_outputs import PaymentProfileOutput
    from bookflow.company.sales_facts import SalesProfile, SalesLineProfile, SalesTaxComponent
    from bookflow.company.work_facts import WorkFacts, WorkLineFacts
    schemas = {
        'deposit_profile': ('facts_snapshot', Effect),
        'deposit_membership': ('facts_snapshot', CashSource),
        'payment_profile': ('profile_snapshot', PaymentProfileOutput),
        'sales_profile': ('profile_snapshot', SalesProfile),
        'sales_line_profile': ('item_snapshot', SalesLineProfile),
        'sales_tax_component': ('component_snapshot', SalesTaxComponent),
        'work_revision': ('facts_snapshot', WorkFacts),
        'work_revision_line': ('facts_snapshot', WorkLineFacts),
    }
    try:
        value = _decoded(value)
        if kind == 'posting_line_source' and 'deposit_component_id' not in value:
            # document_effects.rows deliberately omits this null extension for
            # older, non-deposit producers even at co20; this is its exact wire
            # convention, not a guessed nullable-field fallback.
            value = {**value, 'deposit_component_id': None}
        if kind == 'posting_line_source' and 'payment_component_id' not in value:
            value = {**value, 'payment_component_id': None}
        if kind in schemas:
            field, model = schemas[kind]
            model.model_validate_json(canonical(value[field]))
        table = c.metadata.tables[owner.table]
        required = owner.fields or tuple(column.name for column in table.c if column.name not in _PROVENANCE)
        result = {}
        for field in required:
            if field not in value:
                # A nullable column's absence in an old schema is not silently a
                # null: migration-specific historical decoding needs evidence.
                raise MissingHistory('missing owned field: ' + field)
            item = value[field]
            if field in table.c and item is not None and not field.endswith('_snapshot'):
                expected = table.c[field].type.python_type
                if expected in (str, int, bool) and type(item) is not expected:
                    raise MissingHistory('invalid owned scalar: ' + field)
            result[field] = item
        if kind == 'custom_field':
            for field in ('scopes', 'choices', '_scopes_identities', '_choices_identities'):
                if type(value.get(field)) is not list:
                    raise MissingHistory('missing custom aggregate')
                result[field] = value[field]
        return result
    except (ValidationError, json.JSONDecodeError, KeyError) as exc:
        raise MissingHistory('invalid typed owned snapshot') from exc


class History:
    """One immutable reader snapshot; kind loads are complete, never page slices."""
    def __init__(self, s, endpoint=None, *, historical=False):
        self.s, self.historical = s, historical
        self.cutoff = None
        self.raw = {}; self.entries = {}; self.events = {}
        self.anchors = {}; self.values = {}; self.unknown = set()
        if historical and endpoint is not None:
            self.cutoff = s.company.conn.execute(sa.select(c.audit_events.c.seq).where(c.audit_events.c.id == endpoint)).scalar_one_or_none()
            if self.cutoff is None:
                raise MissingHistory('missing baseline event')
        elif historical:
            self.cutoff = 0

    def load(self, kind):
        if kind in self.raw:
            return
        owner = OWNERS[kind]
        table = c.metadata.tables[owner.table]
        self.raw[kind] = {row[owner.key]: dict(row) for row in self.s.company.conn.execute(sa.select(table)).mappings()}
        self.entries[kind] = {}
        # No unbounded ID binding list. These per-kind scans include historical
        # identities that no longer have a projection; only selected dependencies
        # contribute to signed facts, authority, endpoints or output.
        query = sa.select(c.audit_entries, c.audit_events.c.seq, c.audit_events.c.at,
                          c.audit_events.c.actor_id, c.audit_events.c.actor_kind,
                          c.audit_events.c.on_behalf_of, c.audit_events.c.interface).join(
            c.audit_events, c.audit_events.c.id == c.audit_entries.c.event_id).where(
            c.audit_entries.c.record_type.in_(owner.audit_kinds)).order_by(c.audit_events.c.seq, c.audit_entries.c.id)
        for row in self.s.company.conn.execute(query).mappings():
            item = dict(row)
            self.entries[kind].setdefault(item['record_id'], []).append(item)
            self.events[item['event_id']] = item

    def identities(self, kind):
        self.load(kind)
        return sorted(set(self.raw[kind]) | set(self.entries[kind]))

    def commercial(self, header, sequence):
        """Decode the revision actually owned by this historical header event."""
        from bookflow.company import sales
        revision = self.s.company.conn.execute(sa.select(c.transaction_revisions, c.audit_events.c.seq.label('event_sequence')).join(
            c.audit_events, c.audit_events.c.id == c.transaction_revisions.c.audit_event_id).where(
            c.transaction_revisions.c.id == header['current_revision_id'],
            c.transaction_revisions.c.transaction_id == header['id'])).mappings().one_or_none()
        if revision is None or revision['event_sequence'] > sequence:
            raise MissingHistory('foreign or future commercial revision')
        if header['type'] in ('invoice', 'sales_receipt'):
            return sales._saved_semantic(self.s, revision)
        kind = {'deposit': 'deposit_profile', 'payment': 'payment_profile'}.get(header['type'])
        if kind is None:
            raise MissingHistory('invalid deposit graph transaction kind')
        owner = OWNERS[kind]; table = c.metadata.tables[owner.table]
        row = self.s.company.conn.execute(sa.select(table).where(table.c.revision_id == revision['id'],
            table.c.transaction_id == header['id'])).mappings().one_or_none()
        if row is None:
            raise MissingHistory('missing commercial profile')
        profile = _project(kind, dict(row))
        return {'date': revision['date'], 'number': revision['number'], 'memo': revision['memo'],
                'currency': revision['currency'], 'total_minor_units': revision['total_minor_units'],
                'custom_fields': json.loads(revision['custom_fields_snapshot']),
                'profile': {k: v for k, v in profile.items() if k not in ('transaction_id', 'revision_id')}}

    def chain(self, kind, identity):
        self.load(kind)
        result = []; previous = None; seen = set(); version = None
        for entry in self.entries[kind].get(identity, ()):
            if self.cutoff is not None and entry['seq'] > self.cutoff:
                break
            if kind == 'company_info' and entry['action'] == 'migrate':
                continue  # migration metadata is not a company-info row image
            if entry['event_id'] in seen:
                raise MissingHistory('ambiguous same-event owner')
            seen.add(entry['event_id'])
            try:
                after = audit.decode_snapshot(entry['after'])
                before = audit.decode_snapshot(entry['before'])
                projected = _project(kind, after)
                if kind == 'transaction':
                    projected['commercial'] = self.commercial(after, entry['seq'])
            except (ValueError, TypeError) as exc:
                raise MissingHistory('malformed owned audit image') from exc
            if after.get(OWNERS[kind].key) != identity:
                raise MissingHistory('foreign owned identity')
            if version is not None and entry['version_before'] != version:
                raise MissingHistory('discontinuous owner version')
            if previous is None and entry['version_before'] is not None and entry['action'] != 'baseline':
                raise MissingHistory('missing original owner history')
            before_projected = _project(kind, before) if before is not None else None
            if before_projected is not None and kind == 'transaction':
                before_projected['commercial'] = self.commercial(before, entry['seq'])
            if before_projected is not None and previous is not None and before_projected != previous:
                raise MissingHistory('contradictory before image')
            version = entry['version_after']
            if type(version) is not int or version < 1:
                raise MissingHistory('invalid owner version')
            if projected != previous:
                result.append((entry, projected))
            previous = projected
        return result

    def take(self, kind, identity):
        key = kind, identity
        if key in self.values:
            return self.values[key]
        self.load(kind)
        try:
            chain = self.chain(kind, identity)
            if not chain:
                if identity in self.raw[kind]:
                    # A row created after the historical endpoint is a proven
                    # absence only when its complete first creation is present.
                    first = self.entries[kind].get(identity, ())
                    if not (self.historical and first and first[0]['version_before'] is None
                            and first[0]['seq'] > self.cutoff):
                        raise MissingHistory('missing owner creation')
                self.values[key] = None
                return None
            entry, value = chain[-1]
            raw_owner = self.raw[kind].get(identity)
            if raw_owner is not None and 'audit_event_id' in raw_owner and raw_owner['audit_event_id'] != entry['event_id']:
                raise MissingHistory('foreign event anchor')
            if not self.historical:
                raw = self.raw[kind].get(identity)
                if kind == 'custom_field' and raw is not None:
                    from bookflow.commands.custom_field_cmds import _snapshot
                    raw = _snapshot(self.s.company.conn, identity)
                actual = _project(kind, raw) if raw is not None else None
                if actual is not None and kind == 'transaction':
                    actual['commercial'] = self.commercial(raw, entry['seq'])
                if actual != value:
                    raise MissingHistory('current owner differs from immutable history')
            self.anchors[key] = RecordAnchor(kind=kind, id=identity, version=entry['version_after'],
                event_id=entry['event_id'], semantic_json=canonical(value))
            self.values[key] = value
            return value
        except MissingHistory:
            self.unknown.add(kind + ':' + identity)
            self.values[key] = None
            return None

    def find(self, kind, predicate):
        """Select using the same old/current view before retaining any owner facts."""
        result = []
        self.load(kind)
        for identity in self.identities(kind):
            candidate = self.raw[kind].get(identity)
            if self.historical:
                entries = [entry for entry in self.entries[kind].get(identity, ())
                           if entry['seq'] <= self.cutoff and not (kind == 'company_info' and entry['action'] == 'migrate')]
                if not entries:
                    continue
                try:
                    candidate = audit.decode_snapshot(entries[-1]['after'])
                except (ValueError, TypeError) as exc:
                    raise MissingHistory('malformed historical relation evidence') from exc
            if candidate is None:
                continue
            # A custom-field relation includes its active child scope facts.
            if kind == 'custom_field' and not self.historical:
                from bookflow.commands.custom_field_cmds import _snapshot
                candidate = _snapshot(self.s.company.conn, identity)
            try:
                selected = predicate(candidate)
            except (KeyError, TypeError) as exc:
                raise MissingHistory('incomplete historical relation image') from exc
            if selected:
                value = self.take(kind, identity)
                if value is not None:
                    result.append(value)
        return result


from bookflow.company.deposit_dependency_models import ReadSet, RelationAnchor


def _readset(s, original, *, endpoint=None, historical=False):
    """Resolve both retained and proposed dependencies from one owned history view."""
    from bookflow.company import deposit_dependencies, payment_authority
    from bookflow.company.list_service import normalize_lookup_key
    history = History(s, endpoint, historical=historical)
    original = request(original)
    relations = []
    transactions = set()
    deposits = set()
    sources = set()
    def relation(kind, owner, rows, key='id'):
        members = tuple(sorted(row[key] for row in rows))
        if len(members) != len(set(members)):
            raise MissingHistory('duplicate relation member')
        relations.append(RelationAnchor(kind=kind, owner_id=owner, members=members))
        return rows
    def select(kind, selector):
        # ULID first, then the owner's actual visible selector, exactly as current
        # list resolution does. Old aliases are resolved from old owner images.
        history.load(kind)
        direct = selector.upper() if isinstance(selector, str) else selector
        if direct in history.identities(kind):
            value = history.take(kind, direct)
            if value is not None:
                return value
        normalized = normalize_lookup_key(selector)
        matches = history.find(kind, lambda row: normalize_lookup_key(row.get('full_name') or row['name']) == normalized)
        relation('selector:' + kind, selector, matches)
        if len(matches) != 1:
            raise MissingHistory('unresolved or ambiguous original selector')
        return matches[0]
    if isinstance(original, InspectionRoot):
        (deposits if original.kind == 'deposit' else sources).add(original.id)
    else:
        if original.command != 'deposit post':
            deposits.add(original.input.deposit)
        if original.command != 'deposit void':
            document = original.input.document
            if document.mode != 'inline':
                raise BookflowError('E_DEPOSIT_DRAFT_STATE')
            sources.update(row.source for row in document.sources)
            select('account', document.deposit_to)
            if document.cash_back:
                select('account', document.cash_back.account)
            for row in document.additional:
                select('account', row.from_account)
                history.take(row.received_from.kind, row.received_from.id)
                if row.class_id is not None:
                    select('class', row.class_id)
                if row.payment_method is not None:
                    select('payment_method', row.payment_method)
            definitions = history.find('custom_field', lambda value: value['active'] and any(
                scope['record_type'] == 'deposit' and scope['active'] for scope in value['scopes']))
            relation('custom_definitions', 'deposit', definitions)
    history.take('company_info', s.company_row['id'])
    # All historical claims matter for authority, even if no longer current.
    # The historical view independently proves which rows existed at its endpoint.
    visited_deposits = set(); visited_sources = set()
    while deposits - visited_deposits or sources - visited_sources:
        for deposit in sorted(deposits - visited_deposits):
            visited_deposits.add(deposit)
            memberships = history.find('deposit_membership', lambda row: row['transaction_id'] == deposit)
            relation('deposit_memberships', deposit, memberships)
            sources.update(row['source_transaction_id'] for row in memberships)
        for source in sorted(sources - visited_sources):
            visited_sources.add(source)
            memberships = history.find('deposit_membership', lambda row: row['source_transaction_id'] == source)
            relation('source_claims', source, memberships)
            deposits.update(row['transaction_id'] for row in memberships)
    for source in sorted(sources):
        memberships = history.find('deposit_membership', lambda row: row['source_transaction_id'] == source)
        relation('source_claims', source, memberships)
        deposits.update(row['transaction_id'] for row in memberships)
        apps = history.find('application', lambda row: row['paying_transaction_id'] == source)
        relation('applications', source, apps)
        transactions.update(row['paid_transaction_id'] for row in apps)
        app_ids = {row['id'] for row in apps}
        allocations = history.find('application_allocation', lambda row: row['application_id'] in app_ids)
        relation('allocations', source, allocations)
    transactions.update(deposits | sources)
    # Current whole historical/proposed source admission is never inferred from
    # signed old facts. Callers perform this before disclosing even unknown IDs.
    for deposit in sorted(deposits):
        deposit_dependencies.authorize(s, deposit, sources)
    deposit_dependencies.authorize(s, sources=transactions)
    if sources:
        relation('uf_account', 'company', history.find('account', lambda row: row['system_role'] == 'undeposited_funds'))
    transaction_kinds = [kind for kind, owner in OWNERS.items()
                         if kind not in ('transaction', 'application', 'application_allocation', 'deposit_membership')
                         and 'transaction_id' in c.metadata.tables[owner.table].c]
    for identity in sorted(transactions):
        header = history.take('transaction', identity)
        for kind in transaction_kinds:
            owned = history.find(kind, lambda row: row['transaction_id'] == identity)
            relation(kind, identity, owned, OWNERS[kind].key)
        if header is not None and header['type'] == 'deposit':
            profile = history.take('deposit_profile', header['current_revision_id'])
            if profile is not None:
                from bookflow.company.deposit_models import Effect
                effect = Effect.model_validate_json(canonical(profile['facts_snapshot']))
                for account in [effect.intent.bank] + [row.account for row in effect.intent.additional] + ([effect.intent.cash_back.account] if effect.intent.cash_back else []):
                    history.take('account', account.id)
                for row in effect.intent.additional:
                    history.take(row.dimensions.party_kind, row.dimensions.party_id)
                    if row.dimensions.class_id is not None:
                        history.take('class', row.dimensions.class_id)
                    if row.payment_method is not None:
                        history.take('payment_method', row.payment_method.id)
        if header is not None:
            revision = history.take('transaction_revision', header['current_revision_id'])
            if revision is None or revision['transaction_id'] != identity:
                history.unknown.add('transaction:' + identity)
    # Work ancestry is a distinct complete relation, not a source-profile guess.
    conversions = history.find('work_billing_conversion', lambda row: row['destination_transaction_id'] in transactions)
    relation('work_conversions', 'transactions', conversions)
    allocations = history.find('work_billing_allocation', lambda row: row['transaction_id'] in transactions)
    work_ids = {row['source_document_id'] for row in conversions + allocations}
    work_ids.update(row['root_document_id'] for row in allocations)
    for identity in sorted(work_ids):
        history.take('work_document', identity)
        for kind, owner in OWNERS.items():
            table = c.metadata.tables[owner.table]
            if owner.table.startswith('work_') and 'document_id' in table.c:
                relation(kind, identity, history.find(kind, lambda row: row['document_id'] == identity), owner.key)
    deposit_dependencies.reconciliation_status(s.company)
    records = tuple(history.anchors[key] for key in sorted(history.anchors))
    events = [history.events[row.event_id] for row in records if row.event_id is not None]
    latest = max(events, key=lambda row: row['seq'])['event_id'] if events else None
    # Display versions/ages are not a hash over unrelated master-field writes.
    facts = [(row.kind, row.id, row.event_id, row.semantic_json) for row in records]
    relations = tuple(sorted(set(relations), key=lambda row: (row.kind, row.owner_id)))
    digest = hashlib.sha256(canonical({'records': facts, 'relations': [row.model_dump() for row in relations]}).encode()).hexdigest()
    result = ReadSet(records=records, relations=relations, transactions=tuple(sorted(transactions)),
                     endpoint=latest, digest=digest, unknown=tuple(sorted(history.unknown)))
    return result, history


def capture(s, original_request, binding):
    actor, kind, principal, _ = execution_binding(s, binding)
    original = request(original_request)
    readset, _ = _readset(s, original)
    recipe = BaselineRecipe(company_id=s.company_row['id'], mode='inspection' if isinstance(original, InspectionRoot) else 'intent',
        root=original if isinstance(original, InspectionRoot) else None, intent_digest=intent_digest(original),
        endpoint=readset.endpoint, read_digest=readset.digest, actor_id=actor, actor_kind=kind, principal_id=principal)
    return recipe, readset


def issue(s, recipe, readset, binding):
    actor, kind, principal, _ = execution_binding(s, binding)
    recipe = BaselineRecipe.model_validate_json(recipe.model_dump_json(), strict=True)
    if ((recipe.actor_id, recipe.actor_kind, recipe.principal_id) != (actor, kind, principal)
            or recipe.company_id != s.company_row['id'] or recipe.read_digest != readset.digest
            or recipe.endpoint != readset.endpoint):
        raise invalid_guard()
    if readset.unknown:
        raise BookflowError('E_PREVIEW_STALE', details={'reason': 'deposit_dependencies', 'history': 'unknown_history'})
    return _encode(s, recipe.model_dump(mode='json'), binding)

from bookflow.company.deposit_dependency_models import DependencyComparison, DependencyChange
from bookflow.core import clock
from datetime import datetime


def _fields(before, after, prefix=''):
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        for key in sorted(set(before) | set(after)):
            name = prefix + '.' + key if prefix else key
            if key not in before or key not in after:
                result.append(name)
            else:
                result.extend(_fields(before[key], after[key], name))
        return tuple(result)
    return () if type(before) is type(after) and before == after else (prefix or 'presence',)


def reconstruct(s, guard, original_request, binding):
    recipe = decode_recipe(s, guard, original_request, binding)
    baseline, history = _readset(s, original_request, endpoint=recipe.endpoint, historical=True)
    if baseline.digest != recipe.read_digest:
        raise MissingHistory('baseline digest disagrees with immutable evidence')
    return recipe, baseline, history


def compare(s, guard, original_request, binding):
    from bookflow.company.payment_authority import authorize_events
    from bookflow.company.deposit_dependencies import authorize
    # Authenticate before looking up any old anchor. Current graph admission also
    # precedes disclosure when reconstructing the old recipe fails.
    recipe = decode_recipe(s, guard, original_request, binding)
    current, current_history = _readset(s, original_request)
    try:
        _, baseline, old_history = reconstruct(s, guard, original_request, binding)
    except MissingHistory:
        return DependencyComparison(matches=False, unknown_history=True, unknown_records=(), changes=(), baseline=None, current=current)
    authorize(s, sources=set(baseline.transactions) | set(current.transactions))
    unknown = set(baseline.unknown) | set(current.unknown)
    keys = {(row.kind, row.id) for row in baseline.records + current.records}
    changes = []
    for kind, identity in sorted(keys):
        try:
            chain = current_history.chain(kind, identity)
        except MissingHistory:
            unknown.add(kind + ':' + identity)
            continue
        previous = None
        for entry, value in chain:
            if old_history.cutoff is not None and entry['seq'] > old_history.cutoff:
                fields = _fields(previous, value)
                if fields:
                    at = datetime.fromisoformat(entry['at'].replace('Z', '+00:00'))
                    changes.append((entry['seq'], DependencyChange(kind=kind, record_id=identity,
                        event_id=entry['event_id'], actor_id=entry['actor_id'], actor_kind=entry['actor_kind'],
                        on_behalf_of=entry['on_behalf_of'], interface=entry['interface'], at=entry['at'],
                        age_seconds=max(0, int((clock.now() - at).total_seconds())),
                        version_before=entry['version_before'], version_after=entry['version_after'], fields=fields)))
            previous = value
    # Whole event authority includes other participants in a composite event.
    authorize_events(s, {change.event_id for _, change in changes})
    changes.sort(key=lambda pair: (pair[0], pair[1].kind, pair[1].record_id, pair[1].fields))
    return DependencyComparison(matches=not unknown and current.digest == baseline.digest,
        unknown_history=bool(unknown), unknown_records=tuple(sorted(unknown)),
        changes=tuple(change for _, change in changes), baseline=baseline, current=current)
