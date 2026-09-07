"""Private authenticated deposit history. No dispatch, persistence or public API.

Execution bindings are the existing adapter objects. SQL snapshots and history
are company-owned business evidence, never a substitute for current admission.
"""
import base64
import hashlib
import hmac
import json
import zlib

import sqlalchemy as sa
from pydantic import TypeAdapter, ValidationError

from bookflow.company import schema as c
from bookflow.company.deposit_dependency_models import BaselineRecipe, DepositRequest, InspectionRoot, CoordinateRequest, request_document
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


def _authorize_binding_graph(s, binding, transaction_ids, event_ids=(), *, write=False):
    """Run existing resource rules for actor AND its validated fixed human.

    These are current authenticated hub rows used only by access checks. No
    historical planner, fabricated database, or granular visibility supplier is
    involved. Do not change the shared transport's missing-principal policy.
    """
    from dataclasses import replace
    from bookflow.core.session import Actor
    from bookflow.hub import schema as h, access
    from bookflow.company.deposit_dependencies import authorize
    from bookflow.company.payment_authority import authorize_events
    actor, _, principal, _ = execution_binding(s, binding)
    for identity in (actor,) if principal is None else (actor, principal):
        row = s.hub.conn.execute(sa.select(h.users).where(h.users.c.id == identity,
            h.users.c.active.is_(True))).mappings().one_or_none()
        if row is None:
            raise BookflowError('E_UNAUTHENTICATED')
        view = replace(s, actor=Actor(**{key: row[key] for key in ('id', 'kind', 'username', 'display_name', 'hub_admin', 'timezone')}), memberships=[])
        access.load_memberships(view)
        try:
            authorize(view, sources=transaction_ids, write=write)
            if event_ids:
                authorize_events(view, event_ids)
        except BookflowError as error:
            if error.code in ('E_PERMISSION', 'E_COMPANY_NOT_FOUND'):
                raise BookflowError('E_PERMISSION') from None
            raise


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
    needs_issuer = _needs_issuer(original)
    if needs_issuer != (recipe.issuer_entry is not None):
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
    'deposit_number': Owner('transactions', 'id', ('transaction',), ('id','type','number')),
    'source_number': Owner('transactions', 'id', ('transaction',), ('id','type','number')),
    'deposit_number_operation': Owner('deposit_operations','id',('deposit_operation',)),
    'company_info': Owner('company_info', 'id', ('company_info',), ('id', 'home_currency', 'closing_date')),
    'account': Owner('accounts', 'id', ('account',), ('id', 'name', 'full_name', 'number', 'type', 'system_role', 'active', 'currency')),
    'customer': Owner('customers', 'id', ('customer',), ('id', 'name', 'full_name', 'active')),
    'vendor': Owner('vendors', 'id', ('vendor',), ('id', 'name', 'active')),
    'employee': Owner('employees', 'id', ('employee',), ('id', 'name', 'active')),
    'other_name': Owner('other_names', 'id', ('other_name',), ('id', 'name', 'active')),
    'class': Owner('classes', 'id', ('class',), ('id', 'name', 'full_name', 'active')),
    'payment_method': Owner('payment_methods', 'id', ('payment_method',), ('id', 'name', 'active')),
    'custom_field': Owner('custom_field_defs', 'id', ('custom_field',)),
    'source_custom_field': Owner('custom_field_defs', 'id', ('custom_field',)),
    'work_document': Owner('work_documents', 'id', ('work_document',)),
}

SOURCE_OWNERS = {
    'source_item': ('items','item'), 'source_customer': ('customers','customer'),
    'source_tax_code': ('sales_tax_codes','sales_tax_code'), 'source_unit': ('units_of_measure','unit_of_measure'),
    'source_price_level': ('price_levels','price_level'), 'source_price_version': ('price_levels','price_level'), 'source_vendor': ('vendors','vendor'),
    'source_ship_method': ('ship_methods','ship_method'), 'source_sales_rep': ('sales_reps','sales_rep'),
    'source_message': ('customer_messages','customer_message'), 'source_company': ('company_info','company_info'),
}
for _kind, (_table, _audit_kind) in SOURCE_OWNERS.items():
    OWNERS[_kind]=Owner(_table,'id',(_audit_kind,))


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


def _audit_image(blob):
    if blob is None:
        return None
    if type(blob) is not bytes or blob[:1] not in (audit.RAW, audit.ZIP):
        raise MissingHistory('invalid audit encoding')
    try:
        value = audit.decode_snapshot(blob)
    except (json.JSONDecodeError, UnicodeDecodeError, zlib.error) as exc:
        raise MissingHistory('malformed audit payload') from exc
    if not isinstance(value, dict):
        raise MissingHistory('audit image is not an object')
    return value


def _decoded(value):
    return {key: json.loads(item) if key.endswith('_snapshot') and isinstance(item, str) else item
            for key, item in value.items()}


def _project(kind, value, fields=None):
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
            decoded = model.model_validate_json(canonical(value[field]))
            if kind == 'deposit_profile' and decoded.intent.deposit_id != value['transaction_id']:
                raise MissingHistory('foreign deposit financial snapshot')
            if kind == 'deposit_membership' and (decoded.transaction_id != value['source_transaction_id'] or decoded.revision_id != value['source_revision_id']):
                raise MissingHistory('foreign source membership snapshot')
        if kind in ('work_revision', 'work_revision_line'):
            from bookflow.company.work_tax_facts import read_facts, read_line
            (read_facts if kind == 'work_revision' else read_line)(value['facts_snapshot'])
        if kind in ('sales_tax_attribution', 'work_tax_attribution'):
            from bookflow.company.tax_attribution import TaxAttribution
            TaxAttribution.model_validate_json(canonical(value['facts_snapshot']))
        if kind == 'work_billing_allocation':
            from bookflow.company.work_tax_facts import read_facts, read_line
            read_facts(value['facts_snapshot']['document'])
            read_line(value['facts_snapshot']['line'])
            from bookflow.company import billing_allocations
            if value['allocation_version'] not in (1, 2, 3):
                raise MissingHistory('unknown allocation proof version')
            try:
                raw = {**value, 'facts_snapshot': canonical(value['facts_snapshot'])}
                proof = billing_allocations.read_proof(raw)
                if proof is not None and proof.source_basis_hash != billing_allocations.basis(
                        billing_allocations.captured_line(raw), (value['root_document_id'],value['root_line_id']),
                        billing_allocations.captured_policy(raw)):
                    raise MissingHistory('contradictory work allocation basis')
            except (ValueError, BookflowError) as exc:
                raise MissingHistory('malformed owned allocation proof') from exc
        if kind == 'deposit_number_operation':
            from bookflow.company.deposit_operations import decode_output
            saved = decode_output(canonical(value['effect_snapshot']), value['command'])
            effect = saved.effect.deposit if saved.command == 'deposit coordinate' else saved.effect
            if saved.operation_id != value['id'] or effect.audit_event_id != value['audit_event_id'] or saved.current.id != value['transaction_id']:
                raise MissingHistory('foreign automatic-number receipt')
            return {'id':value['id'],'transaction_id':value['transaction_id'],'number':effect.after.number}
        if kind == 'transaction_revision':
            from bookflow.company.journal_custom_fields import SnapshotField
            for key, captured in value['custom_fields_snapshot'].items():
                decoded = SnapshotField.model_validate_json(canonical(captured))
                if decoded.definition_id != key:
                    raise MissingHistory('foreign captured custom value')
        if kind=='source_customer' and 'contacts' in (fields or ()):
            # parties._plan_contacts inserts sparse new contacts; its owning
            # SQL columns below default to NULL. Old audit images retain that
            # sparse input while current reads contain the materialized NULLs.
            optional=('salutation','middle_name','last_name','job_title','work_phone','home_phone',
                      'mobile_phone','other_phone','work_fax','home_fax','primary_email',
                      'secondary_email','website','external_handle','first_name')
            contacts=value.get('contacts')
            if type(contacts) is not list:raise MissingHistory('malformed source contacts')
            normalized=[]
            for row in contacts:
                if type(row) is not dict or type(row.get('points')) is not list:
                    raise MissingHistory('malformed source contact points')
                normalized.append({**dict.fromkeys(optional),**row,'points':[
                    {'custom_label':None,**point} if type(point) is dict else point for point in row['points']]})
            value={**value,'contacts':normalized}
        if kind in SOURCE_OWNERS:
            _source_image_types(kind,value,fields)
        if kind == 'source_unit':
            value = dict(value)
            for field in fields or ():
                if field.startswith('unit_selector:'):
                    value[field] = _unit_matches(value, field[len('unit_selector:'):])
        table = c.metadata.tables[owner.table]
        required = fields or owner.fields or tuple(column.name for column in table.c if column.name not in _PROVENANCE)
        result = {}
        for field in required:
            if field not in value:
                # A nullable column's absence in an old schema is not silently a
                # null: migration-specific historical decoding needs evidence.
                raise MissingHistory('missing owned field: ' + field)
            item = value[field]
            if field in table.c and item is None and not table.c[field].nullable:
                raise MissingHistory('null required owned field: ' + field)
            if field in table.c and item is not None and not field.endswith('_snapshot'):
                expected = table.c[field].type.python_type
                if expected in (str, int, bool) and type(item) is not expected:
                    raise MissingHistory('invalid owned scalar: ' + field)
            result[field] = item
        if kind == 'bank_effect_version':
            result['version']=value['version']
        if kind in ('custom_field','source_custom_field'):
            for field in ('scopes', 'choices', '_scopes_identities', '_choices_identities'):
                if type(value.get(field)) is not list:
                    raise MissingHistory('missing custom aggregate')
                result[field] = value[field]
        return result
    except (ValidationError, json.JSONDecodeError, KeyError) as exc:
        raise MissingHistory('invalid typed owned snapshot') from exc


class History:
    """One immutable reader snapshot; kind loads are complete, never page slices."""
    def __init__(self, s, endpoint=None, *, historical=False, company_issuer=False):
        self.s, self.historical = s, historical
        self.issuer_events = []
        self.custom_supplied = frozenset()
        self.custom_values = {}
        self.custom_creating = False
        self.source_custom_values = {}
        self.source_custom_scope = None
        self.source_fields = {}
        self.company_fields = None
        if company_issuer:
            self.company_fields = tuple(column.name for column in c.company_info.c if column.name in ('id', 'legal_name', 'home_currency', 'closing_date') or column.name.startswith(('address_', 'legal_address_', 'ship_address_')))
        self.cutoff = None
        self.raw = {}; self.entries = {}; self.events = {}
        self.anchors = {}; self.values = {}; self.unknown = set()
        if historical and endpoint is not None:
            self.cutoff = s.company.conn.execute(sa.select(c.audit_events.c.seq).where(c.audit_events.c.id == endpoint)).scalar_one_or_none()
            if self.cutoff is None:
                raise MissingHistory('missing baseline event')
        elif historical:
            self.cutoff = 0

    def project(self, kind, value):
        if value is None:
            raise MissingHistory('missing owned identity')
        field_identity = value['id']+'@'+str(value['version']) if kind=='source_price_version' and value is not None else value.get('id') if value is not None else None
        fields = self.source_fields.get((kind, field_identity)) if kind in SOURCE_OWNERS and value is not None else self.company_fields if kind == 'company_info' else None
        if kind=='source_price_version':
            fields=self._price_chain_fields
        if kind in SOURCE_OWNERS and fields is None:
            raise ValueError('source owner field inventory must be supplied before history projection')
        result = _project(kind, value, fields)
        if kind in ('custom_field','source_custom_field'):
            source = kind == 'source_custom_field'
            values = self.source_custom_values if source else self.custom_values
            scope = self.source_custom_scope if source else 'deposit'
            creating = False if source else self.custom_creating
            if not creating or value['id'] in values:
                result.pop('default_canonical_text', None)
            from bookflow.company.list_service import normalize_lookup_key
            selected = values.get(value['id']) if value['id'] in values else (value['default_canonical_text'] if creating else None)
            scopes = [row for row in result['scopes'] if row['record_type']==scope and row['active']]
            choices = [row for row in result['choices'] if row['active'] and selected is not None and normalize_lookup_key(row['value'])==normalize_lookup_key(selected)] if value['kind']=='choice' else []
            result['scopes']=[{key:row[key] for key in ('id','record_type','active')} for row in scopes]
            result['choices']=[{key:row[key] for key in ('id','value','active')} for row in choices]
            result.pop('_scopes_identities'); result.pop('_choices_identities')
        return result

    def load(self, kind):
        if kind in self.raw:
            return
        if kind=='source_price_version':
            self.load('source_price_level')
            self.raw[kind]={};self.entries[kind]={};self.price_duplicates=set()
            for identity,entries in self.entries['source_price_level'].items():
                for entry in entries:
                    version=entry['version_after']
                    if type(version) is not int or version<1:continue
                    key=identity+'@'+str(version)
                    if key in self.raw[kind]:self.price_duplicates.add(key)
                    self.raw[kind][key]=_audit_image(entry['after'])
                    self.entries[kind][key]=[row for row in entries if row['seq']<=entry['seq']]
            return
        owner = OWNERS[kind]
        table = c.metadata.tables[owner.table]
        self.raw[kind] = {row[owner.key]: dict(row) for row in self.s.company.conn.execute(sa.select(table)).mappings()}
        if kind in SOURCE_OWNERS:
            self.raw[kind] = {key:json.loads(canonical(_source_owner_image(self.s, kind, value))) for key,value in self.raw[kind].items()}
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
        result = []; previous = None; seen = set(); version = None; unit_children = {}
        if kind=='source_price_version':self._price_chain_fields=self.source_fields[kind,identity]
        for entry in self.entries[kind].get(identity, ()):
            if self.cutoff is not None and entry['seq'] > self.cutoff:
                break
            if kind in ('company_info','source_company') and entry['action'] == 'migrate':
                continue  # migration metadata is not a company-info row image
            if entry['event_id'] in seen:
                raise MissingHistory('ambiguous same-event owner')
            seen.add(entry['event_id'])
            after = _audit_image(entry['after'])
            before = _audit_image(entry['before'])
            if kind == 'source_unit' and any(field.startswith('unit_selector:') for field in self.source_fields.get((kind,identity),())):
                if before is not None:
                    before = _complete_unit_image(before, dict(unit_children))
                after = _complete_unit_image(after, unit_children)
            projected = self.project(kind, after)
            if kind == 'transaction':
                projected['commercial'] = self.commercial(after, entry['seq'])
            if 'version' in after and kind not in {item[2] for item in _IMMUTABLE}:
                if type(after['version']) is not int or after['version'] != entry['version_after']:
                    raise MissingHistory('header version disagrees with event')
            physical_identity=identity.split('@')[0] if kind=='source_price_version' else identity
            if after.get(OWNERS[kind].key) != physical_identity:
                raise MissingHistory('foreign owned identity')
            if version is not None and entry['version_before'] != version:
                raise MissingHistory('discontinuous owner version')
            if previous is None and entry['version_before'] is not None and entry['action'] != 'baseline':
                raise MissingHistory('missing original owner history')
            before_projected = self.project(kind, before) if before is not None else None
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
            if kind=='source_price_version' and identity in self.price_duplicates:
                raise MissingHistory('duplicate captured price version')
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
            if kind=='source_price_version' and entry['version_after']!=int(identity.split('@')[1]):
                raise MissingHistory('captured price version unavailable at endpoint')
            raw_owner = self.raw[kind].get(identity)
            if raw_owner is not None and 'audit_event_id' in raw_owner and raw_owner['audit_event_id'] != entry['event_id']:
                raise MissingHistory('foreign event anchor')
            if not self.historical:
                raw = self.raw[kind].get(identity)
                if kind in ('custom_field','source_custom_field') and raw is not None:
                    from bookflow.commands.custom_field_cmds import _snapshot
                    raw = _snapshot(self.s.company.conn, identity)
                actual = self.project(kind, raw) if raw is not None else None
                if actual is not None and kind == 'transaction':
                    actual['commercial'] = self.commercial(raw, entry['seq'])
                if actual != value:
                    raise MissingHistory('current owner differs from immutable history')
            self.anchors[key] = RecordAnchor(kind=kind, id=identity, version=entry['version_after'],
                event_id=entry['event_id'], entry_id=entry['id'], semantic_json=canonical(value), semantic_digest=hashlib.sha256(canonical(value).encode()).hexdigest())
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
                           if entry['seq'] <= self.cutoff and not (kind in ('company_info','source_company') and entry['action'] == 'migrate')]
                if not entries:
                    continue
                candidate = _audit_image(entries[-1]['after'])
            if candidate is None and not self.historical:
                entries = self.entries[kind].get(identity, ())
                if entries:
                    candidate = _audit_image(entries[-1]['after'])
            if candidate is None:
                continue
            # A custom-field relation includes its active child scope facts.
            if kind in ('custom_field','source_custom_field') and not self.historical:
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


def _needs_issuer(original):
    if isinstance(original, InspectionRoot):
        return False
    return (original.command == 'deposit post' or
            isinstance(original, CoordinateRequest) and
            original.input.source_action.kind == 'sales_receipt_update' and
            original.input.source_action.input.refresh_defaults)


def _readset(s, original, *, endpoint=None, historical=False, allow_missing_selectors=False, issuer_entry=None):
    """Resolve both retained and proposed dependencies from one owned history view."""
    from bookflow.company import deposit_dependencies, payment_authority
    from bookflow.company.list_service import normalize_lookup_key
    original = request(original)
    new_post = not isinstance(original, InspectionRoot) and original.command == 'deposit post'
    history = History(s, endpoint, historical=historical, company_issuer=new_post)
    history.custom_creating = new_post
    history.allow_missing_selectors = allow_missing_selectors
    document = request_document(original)
    if document is not None and document.mode == 'inline':
        history.custom_supplied = frozenset(document.custom_fields.root)
        history.custom_values = dict(document.custom_fields.root)
    relations = []
    transactions = set()
    deposits = set()
    sources = set()
    def relation(kind, owner, rows, key='id'):
        members = tuple(sorted(row[key] for row in rows))
        if len(members) != len(set(members)):
            raise MissingHistory('duplicate relation member')
        relations.append(RelationAnchor(kind=kind, owner_id=owner, members=members,count=len(members),state='populated' if members else 'empty',digest=hashlib.sha256(canonical(members).encode()).hexdigest()))
        return rows
    def select(kind, selector):
        # ULID first, then the owner's actual visible selector, exactly as current
        # list resolution does. Old aliases are resolved from old owner images.
        history.load(kind)
        direct = selector.strip().upper() if isinstance(selector, str) else selector
        if direct in history.identities(kind):
            value = history.take(kind, direct)
            if value is not None:
                return value
        normalized = normalize_lookup_key(selector)
        matches = history.find(kind, lambda row: normalize_lookup_key(row.get('full_name') or row['name']) == normalized)
        relation('selector:' + kind, selector, matches)
        if not matches and allow_missing_selectors:
            return None
        if len(matches) != 1:
            if historical:
                raise MissingHistory('unresolved or ambiguous original selector')
            raise BookflowError('E_RECORD_NOT_FOUND')
        return matches[0]
    if isinstance(original, InspectionRoot):
        (deposits if original.kind == 'deposit' else sources).add(original.id)
    else:
        if original.command != 'deposit post':
            deposits.add(original.input.deposit)
        if document is not None:
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
    if isinstance(original, CoordinateRequest):
        from bookflow.company.deposit_coordinate_models import source_identity
        action = original.input.source_action
        sources.add(source_identity(action))
        if action.kind.endswith('_update'):
            history.source_custom_scope = 'payment' if action.kind.startswith('payment_') else 'sales_receipt'
            history.source_custom_values = dict(action.input.custom_fields.root)
            if getattr(action.input,'refresh_defaults',False):
                custom_header=history.take('transaction',source_identity(action))
                custom_revision=history.take('transaction_revision',custom_header['current_revision_id']) if custom_header else None
                if custom_revision is not None:
                    history.source_custom_values={**{key:value['canonical_text'] for key,value in custom_revision['custom_fields_snapshot'].items()},**history.source_custom_values}
            definitions = history.find('source_custom_field', lambda value: value['active'] and any(
                scope['record_type'] == history.source_custom_scope and scope['active'] for scope in value['scopes']))
            relation('custom_definitions', history.source_custom_scope, definitions)
        if action.kind.endswith('_update'):
            source_header=history.take('transaction',source_identity(action))
            number=action.input.number if action.input.number is not None else source_header['number'] if source_header else None
            if number is not None:
                source_type='payment' if action.kind.startswith('payment_') else 'sales_receipt'
                occupied=history.find('source_number',lambda row:row['type']==source_type and row['id']!=source_identity(action) and row['number']==number)
                relation('source_number_occupancy',source_type+':'+number,occupied)
                transactions.update(row['id'] for row in occupied)
        if action.kind == 'payment_update':
            if source_header is not None:
                source_profile=history.take('payment_profile',source_header['current_revision_id'])
                if source_profile is not None:
                    history.take('account',source_profile['deposit_account_id'])
                    history.take('account',source_profile['ar_account_id'])
            for field, kind in (('deposit_to','account'),('payment_method','payment_method')):
                if field in action.input.model_fields_set and getattr(action.input, field) is not None:
                    select(kind, getattr(action.input, field))
        elif action.kind == 'sales_receipt_update':
            _source_update_dependencies(history, action.input, select, relation)
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
                         if kind not in ('transaction', 'application', 'application_allocation', 'deposit_membership', 'deposit_number','deposit_number_operation')
                         and 'transaction_id' in c.metadata.tables[owner.table].c]
    for identity in sorted(transactions):
        header = history.take('transaction', identity)
        for kind in transaction_kinds:
            owned = history.find(kind, lambda row: row['transaction_id'] == identity)
            relation(kind, identity, owned, OWNERS[kind].key)
        if header is not None and header['type'] == 'deposit' and (isinstance(original, InspectionRoot) or original.command != 'deposit void'):
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
    _current_relations(history, sources, deposits, relation)
    # Work ancestry is a distinct complete relation, not a source-profile guess.
    conversions = history.find('work_billing_conversion', lambda row: row['destination_transaction_id'] in transactions)
    relation('work_conversions', 'transactions', conversions)
    allocations = history.find('work_billing_allocation', lambda row: row['transaction_id'] in transactions)
    work_ids = {row['source_document_id'] for row in conversions + allocations}
    work_ids.update(row['root_document_id'] for row in allocations)
    visited_work=set()
    while work_ids-visited_work:
        for identity in sorted(work_ids-visited_work):
            visited_work.add(identity)
            links=history.find('work_link',lambda row:row['destination_document_id']==identity)
            relation('work_ancestry',identity,links)
            for link in links:
                work_ids.add(link['source_document_id'])
                for side in ('source','destination'):
                    revision=history.take('work_revision',link[side+'_revision_id'])
                    if revision is None or revision['document_id']!=link[side+'_document_id']:
                        history.unknown.add('work_link:'+link['id'])
    for identity in sorted(work_ids):
        history.take('work_document', identity)
        for kind, owner in OWNERS.items():
            table = c.metadata.tables[owner.table]
            if owner.table.startswith('work_') and 'document_id' in table.c:
                relation(kind, identity, history.find(kind, lambda row: row['document_id'] == identity), owner.key)
    deposit_dependencies.reconciliation_status(s.company)
    if document is not None:
        _number_dependencies(history, original, relation, transactions)
    issuer = None
    if _needs_issuer(original):
        try:
            issuer, history.issuer_events = _issuer(s, issuer_entry, historical=historical)
        except MissingHistory:
            history.unknown.add('issuer.display_name')
    records = tuple(history.anchors[key] for key in sorted(history.anchors))
    events = [history.events[row.event_id] for row in records if row.event_id is not None]
    latest = max(events, key=lambda row: row['seq'])['event_id'] if events else None
    # Display versions/ages are not a hash over unrelated master-field writes.
    facts = [(row.kind, row.id, row.event_id, row.entry_id, row.semantic_digest) for row in records]
    relations = tuple(sorted(set(relations), key=lambda row: (row.kind, row.owner_id)))
    digest = hashlib.sha256(canonical({'records': facts, 'relations': [row.model_dump() for row in relations], 'issuer': issuer.model_dump() if issuer else None}).encode()).hexdigest()
    result = ReadSet(records=records, relations=relations, transactions=tuple(sorted(transactions)),
                     endpoint=latest, digest=digest, unknown=tuple(sorted(history.unknown)), issuer=issuer)
    return result, history


def capture(s, original_request, binding):
    actor, kind, principal, _ = execution_binding(s, binding)
    original = request(original_request)
    roots = []
    if isinstance(original, InspectionRoot):
        roots.append((original.id,original.kind))
    else:
        if original.command != 'deposit post':
            roots.append((original.input.deposit,'deposit'))
        document = request_document(original)
        if isinstance(original, CoordinateRequest):
            from bookflow.company.deposit_coordinate_models import source_identity
            action = original.input.source_action
            source_kind = 'payment' if action.kind.startswith('payment_') else 'sales_receipt'
            roots.append((source_identity(action), source_kind))
        if document is not None and document.mode == 'inline':
            roots.extend((row.source, row.source_type if hasattr(row, 'source_type') else source_kind) for row in document.sources)
    _authorize_binding_graph(s,binding,[identity for identity,_ in roots])
    for identity, owner_kind in roots:
        found=s.company.conn.execute(sa.select(c.transactions.c.id).where(c.transactions.c.id==identity,c.transactions.c.type==owner_kind)).first()
        if found is None:raise BookflowError('E_RECORD_NOT_FOUND')
    readset, _ = _readset(s, original)
    _authorize_binding_graph(s, binding, readset.transactions)
    recipe = BaselineRecipe(company_id=s.company_row['id'], mode='inspection' if isinstance(original, InspectionRoot) else 'intent',
        root=original if isinstance(original, InspectionRoot) else None, intent_digest=intent_digest(original),
        endpoint=readset.endpoint, read_digest=readset.digest, actor_id=actor, actor_kind=kind, principal_id=principal, issuer_entry=readset.issuer.entry_id if readset.issuer else None)
    return recipe, readset


def issue(s, recipe, readset, binding):
    actor, kind, principal, _ = execution_binding(s, binding)
    if type(recipe) is not BaselineRecipe or type(readset) is not ReadSet:
        raise invalid_guard()
    recipe = BaselineRecipe.model_validate_json(recipe.model_dump_json(), strict=True)
    readset = ReadSet.model_validate_json(readset.model_dump_json(), strict=True)
    expected = hashlib.sha256(canonical({'records': [(row.kind,row.id,row.event_id,row.entry_id,row.semantic_digest) for row in readset.records],
        'relations':[row.model_dump() for row in readset.relations],'issuer':readset.issuer.model_dump() if readset.issuer else None}).encode()).hexdigest()
    if expected != readset.digest:
        raise invalid_guard()
    if ((recipe.actor_id, recipe.actor_kind, recipe.principal_id) != (actor, kind, principal)
            or recipe.company_id != s.company_row['id'] or recipe.read_digest != readset.digest
            or recipe.endpoint != readset.endpoint
            or recipe.issuer_entry != (readset.issuer.entry_id if readset.issuer else None)):
        raise invalid_guard()
    _authorize_binding_graph(s, binding, readset.transactions)
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
    baseline, history = _readset(s, original_request, endpoint=recipe.endpoint, historical=True, issuer_entry=recipe.issuer_entry)
    _authorize_binding_graph(s, binding, baseline.transactions)
    if baseline.digest != recipe.read_digest:
        raise MissingHistory('baseline digest disagrees with immutable evidence')
    return recipe, baseline, history


def compare(s, guard, original_request, binding):
    from bookflow.company.payment_authority import authorize_events
    from bookflow.company.deposit_dependencies import authorize
    # Authenticate before looking up any old anchor. Current graph admission also
    # precedes disclosure when reconstructing the old recipe fails.
    recipe = decode_recipe(s, guard, original_request, binding)
    original=request(original_request)
    if isinstance(original,CoordinateRequest):
        from bookflow.company.deposit_coordinate_models import source_identity
        document=request_document(original)
        roots=[original.input.deposit,source_identity(original.input.source_action)]
        if document is not None:roots.extend(row.source for row in document.sources)
        _authorize_binding_graph(s,binding,roots)
    current, current_history = _readset(s, original_request, allow_missing_selectors=True)
    _authorize_binding_graph(s, binding, current.transactions)
    try:
        _, baseline, old_history = reconstruct(s, guard, original_request, binding)
    except MissingHistory:
        return DependencyComparison(matches=False, unknown_history=True, unknown_records=(), changes=(), baseline=None, current=current)
    _authorize_binding_graph(s, binding, set(baseline.transactions) | set(current.transactions))
    unknown = set(baseline.unknown) | set(current.unknown)
    keys = {(row.kind, row.id) for row in baseline.records + current.records}
    changes = []
    for kind, identity in sorted(keys):
        if kind in SOURCE_OWNERS:
            used=set()
            for row in baseline.records+current.records:
                if (row.kind,row.id)==(kind,identity):used.update(json.loads(row.semantic_json))
            current_history.source_fields[kind,identity]=tuple(sorted(used))
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
                    changes.append(((0, entry['seq']), DependencyChange(kind=kind, record_id=identity,
                        event_id=entry['event_id'], actor_id=entry['actor_id'], actor_kind=entry['actor_kind'],
                        on_behalf_of=entry['on_behalf_of'], interface=entry['interface'], at=entry['at'],
                        age_seconds=max(0, int((clock.now() - at).total_seconds())),
                        version_before=entry['version_before'], version_after=entry['version_after'], fields=fields)))
            previous = value
    if baseline.issuer is not None and current.issuer is not None:
        past = False
        for entry, anchor in current_history.issuer_events:
            if anchor.entry_id == baseline.issuer.entry_id:
                past = True
                continue
            if past:
                at = datetime.fromisoformat(entry['at'].replace('Z', '+00:00'))
                changes.append(((1, entry['seq']), DependencyChange(storage='hub', kind='issuer',
                    record_id=anchor.company_id, event_id=anchor.hub_event_id,
                    actor_id=entry['actor_id'], actor_kind=entry['actor_kind'], on_behalf_of=entry['on_behalf_of'],
                    interface=entry['interface'], at=entry['at'], age_seconds=max(0, int((clock.now()-at).total_seconds())),
                    version_before=entry['version_before'], version_after=anchor.after_version, fields=('issuer.display_name',))))
        if not past:
            unknown.add('issuer.display_name')
    # Whole event authority includes other participants in a composite event.
    _authorize_binding_graph(s, binding, set(baseline.transactions) | set(current.transactions), {change.event_id for _, change in changes if change.storage == 'company'})
    changes.sort(key=lambda pair: (pair[0], pair[1].kind, pair[1].record_id, pair[1].fields))
    return DependencyComparison(matches=not unknown and current.digest == baseline.digest,
        unknown_history=bool(unknown), unknown_records=tuple(sorted(unknown)),
        changes=tuple(change for _, change in changes), baseline=baseline, current=current)


def _issuer(s, entry_id=None, *, historical=False):
    """Selected-company name history paired with its hub row in ONE SQL read.

    `companies.update` stores after-only images. The preceding proven company
    image supplies the before name; it is never inferred from the current copy.
    Company-side history and hub-side history keep their own event order.
    """
    from bookflow.hub import schema as h
    from bookflow.company.deposit_dependency_models import IssuerAnchor
    company_id = s.company_row['id']
    joined = h.companies.outerjoin(h.audit_entries, sa.and_(
        h.audit_entries.c.record_type == 'company', h.audit_entries.c.record_id == h.companies.c.id)).outerjoin(
        h.audit_events, h.audit_events.c.id == h.audit_entries.c.event_id)
    rows = list(s.hub.conn.execute(sa.select(h.companies.c.display_name.label('current_name'), h.audit_entries,
        h.audit_events.c.seq, h.audit_events.c.at, h.audit_events.c.actor_id, h.audit_events.c.actor_kind,
        h.audit_events.c.on_behalf_of, h.audit_events.c.interface).select_from(joined).where(
        h.companies.c.id == company_id).order_by(h.audit_events.c.seq, h.audit_entries.c.id)).mappings())
    if not rows or rows[0]['id'] is None:
        raise MissingHistory('missing selected-company issuer history')
    if historical:
        positions = [index for index, row in enumerate(rows) if row['id'] == entry_id]
        if len(positions) != 1:
            raise MissingHistory('missing selected-company issuer anchor')
        rows = rows[:positions[0]+1]
    previous = None; previous_version = None; changes = []; seen_events = set()
    for record in rows:
        row = dict(record)
        if row['action'] == 'migrate':
            # Existing schema-only partial audit entries do not mutate the name.
            continue
        if row['event_id'] is None or type(row['seq']) is not int:
            raise MissingHistory('issuer entry has no owning event')
        if row['event_id'] in seen_events:
            raise MissingHistory('ambiguous selected-company issuer event')
        seen_events.add(row['event_id'])
        after, before = _audit_image(row['after']), _audit_image(row['before'])
        if row['action'] == 'delete':
            if after is not None or before is None or before.get('id') != company_id:
                raise MissingHistory('invalid retired company history')
            previous = None; previous_version = None
            continue
        if (after is None or after.get('id') != company_id or type(after.get('display_name')) is not str
                or not after['display_name'] or type(row['version_after']) is not int
                or row['version_after'] < 1 or type(after.get('version')) is not int
                or after['version'] != row['version_after']):
            raise MissingHistory('malformed issuer image')
        if previous is None:
            if row['action'] != 'create' or row['version_before'] is not None:
                raise MissingHistory('missing issuer creation')
        elif row['version_before'] != previous_version:
            raise MissingHistory('incomplete selected-company issuer chain')
        if before is not None and (before.get('id') != company_id or before.get('display_name') != previous):
            raise MissingHistory('contradictory issuer before image')
        if after['display_name'] != previous or row['action'] == 'create':
            anchor = IssuerAnchor(company_id=company_id, hub_event_id=row['event_id'], entry_id=row['id'],
                                  after_version=row['version_after'], display_name=after['display_name'])
            changes.append((row, anchor))
        previous, previous_version = after['display_name'], row['version_after']
    if not changes:
        raise MissingHistory('no issuer name anchor')
    anchor = changes[-1][1]
    if historical:
        if anchor.entry_id != entry_id:
            raise MissingHistory('issuer anchor is not name-changing')
    elif rows[-1]['current_name'] != anchor.display_name or s.company_row['display_name'] != anchor.display_name:
        raise MissingHistory('issuer name differs from admitted hub history')
    return anchor, changes


def version_meta(s, header, expected, binding):
    """Ordinary version conflicts use the same proven history and page contract.

    An expected version alone can reconstruct the old OWNER graph, not pretend
    that an arbitrary unsaved replacement was observed at that past moment.
    The returned inspection recipe says precisely which proof it supplies.
    """
    if header['version'] == expected:
        return
    from bookflow.company.deposit_dependency_models import PageInput
    from bookflow.company.deposit_dependency_pages import changes_page
    _authorize_binding_graph(s, binding, (header['id'],))
    root = InspectionRoot(kind={'deposit':'deposit', 'payment':'payment', 'sales_receipt':'sales_receipt'}[header['type']], id=header['id'])
    reader = History(s); reader.load('transaction')
    entries = [row for row in reader.entries['transaction'].get(header['id'], ()) if row['version_after'] == expected]
    details = {'expected_version': expected, 'current_version': header['version']}
    if expected > header['version']:
        raise BookflowError('E_VERSION_CONFLICT', details={**details, 'reason':'invalid_expected_version'})
    if len(entries) != 1:
        raise BookflowError('E_VERSION_CONFLICT', details={**details, 'history':'unknown_history', 'unknown_versions':[expected]})
    baseline, _ = _readset(s, root, endpoint=entries[0]['event_id'], historical=True)
    actor, kind, principal, _ = execution_binding(s, binding)
    recipe = BaselineRecipe(company_id=s.company_row['id'],mode='inspection',root=root,intent_digest=intent_digest(root),
        endpoint=baseline.endpoint,read_digest=baseline.digest,actor_id=actor,actor_kind=kind,principal_id=principal,issuer_entry=None)
    if baseline.unknown:
        raise BookflowError('E_VERSION_CONFLICT', details={**details, 'history':'unknown_history','unknown_versions':[expected]})
    guard = issue(s, recipe, baseline, binding)
    page = changes_page(s, guard, root, PageInput(), binding)
    raise BookflowError('E_VERSION_CONFLICT', details={**details,
        'history':'unknown_history' if page.unknown_history else 'known_stale',
        'dependency_guard':guard,'original_request':root.model_dump(mode='json'),
        'changes':page.model_dump(mode='json')})


def _number_dependencies(history, original, relation, transactions):
    """Owned receipt reconstruction for the ordinary co21 deposit number writer.

    co20/co21 do not seed a deposit series. The sole ordinary writer advances it
    only on a changed automatic post; that permanent receipt retains the exact
    omission and assigned number. Validate today's unaudited projection against
    those immutable effects; never assume an arbitrary existing series is empty.
    """
    s = history.s
    document = request_document(original)
    own = original.input.deposit if original.command != 'deposit post' else None
    explicit = document.number
    number = explicit
    if explicit is None:
        def automatic(row):
            request = _decoded(row)['request_snapshot']
            return row['command']=='deposit post' and request['input']['document'].get('number') is None
        operations = history.find('deposit_number_operation', automatic)
        relation('automatic_number_effects','deposit',operations)
        ordered = sorted(operations,key=lambda row:history.events[history.anchors['deposit_number_operation',row['id']].event_id]['seq'])
        next_number = 1
        for row in ordered:
            text = row['number']
            if type(text) is not str or not text.isascii() or not text.isdigit() or str(int(text))!=text or int(text)<next_number:
                history.unknown.add('deposit_number_sequence')
                continue
            next_number=int(text)+1
            transactions.add(row['transaction_id'])
        if not history.historical:
            current = s.company.conn.execute(sa.select(c.sequences).where(c.sequences.c.name=='deposit')).mappings().one_or_none()
            expected = {'name':'deposit','next_number':next_number,'prefix':''} if operations else None
            if (dict(current) if current is not None else None)!=expected:
                history.unknown.add('deposit_number_sequence')
        number=str(next_number)
        while True:
            matches=history.find('deposit_number',lambda row:row['type']=='deposit' and row['id']!=own and row['number']==number)
            relation('number_occupancy',number,matches)
            transactions.update(row['id'] for row in matches)
            if not matches:break
            next_number+=1
            number=str(next_number)
    else:
        matches=history.find('deposit_number',lambda row:row['type']=='deposit' and row['id']!=own and row['number']==number)
        relation('number_occupancy',number,matches)
        transactions.update(row['id'] for row in matches)


def _current_relations(history, sources, deposits, relation):
    """Reconstruct negative/current projections from complete immutable events."""
    for source in sorted(sources):
        members=history.find('deposit_membership',lambda row:row['source_transaction_id']==source)
        ordered=sorted(members,key=lambda row:(history.events[history.anchors['deposit_membership',row['id']].event_id]['seq'],row['kind']=='claim',row['id']))
        current=None
        for member in ordered:
            if member['kind']=='claim':
                if current is not None:history.unknown.add('source_claims:'+source)
                current=member
            elif (current is None or member['reverses_membership_id']!=current['id'] or any(
                    member[key]!=current[key] for key in ('transaction_id','source_transaction_id','source_revision_id','amount_minor_units','currency'))):
                history.unknown.add('source_claims:'+source)
            else:current=None
        relation('active_claim',source,[current] if current else [])
        if not history.historical:
            rows=history.s.company.conn.execute(sa.select(c.deposit_current_memberships).where(c.deposit_current_memberships.c.source_transaction_id==source)).mappings().all()
            expected=[{'source_transaction_id':source,'transaction_id':current['transaction_id'],'membership_id':current['id']}] if current else []
            if [dict(row) for row in rows]!=expected:history.unknown.add('source_claims:'+source)
    for deposit in sorted(deposits):
        versions=history.find('bank_effect_version',lambda row:row['transaction_id']==deposit)
        keys={row['key_id'] for row in versions}
        for key in sorted(keys):
            rows=sorted((row for row in versions if row['key_id']==key),key=lambda row:row['version'])
            if [row['version'] for row in rows]!=list(range(1,len(rows)+1)):
                history.unknown.add('bank_effect_current:'+key)
                continue
            latest=rows[-1]
            relation('bank_effect_current',key,[latest])
            if not history.historical:
                current=history.s.company.conn.execute(sa.select(c.bank_effect_current).where(c.bank_effect_current.c.key_id==key)).mappings().one_or_none()
                if current is None or current['version_id']!=latest['id']:
                    history.unknown.add('bank_effect_current:'+key)


def _source_update_dependencies(history, inp, select, relation):
    """Resolve source dependency choices from immutable owner images and intent.

    This reader does not plan postings, calculate amounts or construct a Session.
    Captured commercial facts continue to belong to the original sales owner.
    """
    from bookflow.company.sales_facts import SalesProfile, SalesLineProfile, Preferences
    from bookflow.company.sales_defaults import _Fields
    reads=SourceReads(history,relation)
    header=history.take('transaction',inp.sales_receipt)
    if header is None:return
    profile=history.take('sales_profile',header['current_revision_id'])
    if profile is None:return
    old=SalesProfile.model_validate_json(canonical(profile['profile_snapshot']))
    refresh=inp.refresh_defaults
    controls=['use_classes','prompt_for_class','enable_price_levels','units_of_measure_mode']
    if refresh:
        controls.extend(Preferences.model_fields)
        # The owning refresh copies these audited issuer facts. The pinned
        # display name is covered separately by the selected hub-name anchor.
        controls.extend(column.name for column in c.company_info.c
                        if column.name in ('legal_name', 'home_currency')
                        or column.name.startswith(('address_', 'legal_address_', 'ship_address_')))
    from bookflow.company import tax_policy
    if ('sales_tax_calculation' not in inp.model_fields_set and
        ('sales_tax_calculation' in inp.use_defaults or refresh and tax_policy.origin(old).kind=='default')):
        controls.append('sales_tax_calculation')
    info=reads.read('source_company',history.s.company_row['id'],*controls)
    if info is None:return
    fields=_Fields(inp,old,refresh,[])
    customer=reads.select('source_customer',inp.customer or old.customer.id)
    if customer is None:return
    customer_changed=customer['id']!=old.customer.id
    def inherited(key):
        identity=customer['id'];seen=set()
        while identity is not None:
            if identity in seen:raise MissingHistory('cyclic customer source defaults')
            seen.add(identity)
            value=reads.read('source_customer',identity,key,'parent_id')
            if value is None:return None
            if value[key] is not None:return value[key]
            identity=value['parent_id']
        return None
    def collection(mode,key):
        identity=customer['id'];seen=set()
        while identity is not None:
            if identity in seen:raise MissingHistory('cyclic customer source collection')
            seen.add(identity)
            value=reads.read('source_customer',identity,mode,'parent_id')
            if value is None:return
            if value[mode]=='own':
                reads.read('source_customer',identity,key)
                return
            identity=value['parent_id']
    if customer_changed or refresh:
        reads.read('source_customer',customer['id'],'version','name','full_name','active','company_name',
            'salutation','first_name','middle_name','last_name','resale_number')
        collection('contact_mode','contacts')
    aliases={'ship_method':'source_ship_method','sales_rep':'source_sales_rep',
        'customer_tax_code':'source_tax_code','tax_code':'source_tax_code','sales_tax_item':'source_item',
        'price_level':'source_price_level','customer_message_item':'source_message'}
    def reference(field,selector,saved,*,refresh_value=False,defaulted=False):
        if selector is None:return None
        alias=aliases.get(field)
        if alias:
            value=reads.select(alias,selector)
        else:
            value=select('class' if field=='class_id' else 'account' if field=='deposit_to' else 'payment_method',selector)
        if value is None:return None
        identity=value['id']
        if saved is not None and identity==saved.id and not refresh_value and not defaulted:return saved.id
        if alias:
            wanted=['name','version','active']
            image=reads.image(alias,identity)
            if 'full_name' in image:wanted.append('full_name')
            if 'code' in image and 'name' not in image:wanted.remove('name');wanted.append('code')
            if alias=='source_tax_code':wanted.append('taxable')
            if alias=='source_message':wanted.append('text')
            reads.read(alias,identity,*wanted)
        return identity
    defaults={'ship_method':'preferred_ship_method_id','sales_rep':'sales_rep_id','class_id':'default_class_id',
        'customer_tax_code':'sales_tax_code_id','sales_tax_item':'sales_tax_item_id','price_level':'price_level_id','payment_method':'preferred_payment_method_id'}
    resolved={}
    for field in ('ship_method','sales_rep','class_id','customer_tax_code','price_level','payment_method','sales_tax_item'):
        saved=getattr(old,field)
        if fields.needs(field,customer_changed) or refresh:
            if field in fields.supplied:selector=getattr(inp,field)
            elif fields.needs(field,customer_changed):
                if field=='class_id' and not info['use_classes']:selector=None
                else:
                    selector=inherited('job_sales_rep_id') if field=='sales_rep' else None
                    if selector is None:selector=inherited(defaults[field])
                    if field=='sales_tax_item' and selector is None:
                        value=reads.read('source_company',info['id'],'default_sales_tax_item_id')
                        selector=value['default_sales_tax_item_id'] if value else None
            else:selector=saved.id if saved else None
            resolved[field]=reference(field,selector,saved,refresh_value=refresh,defaulted=field in fields.defaults)
        else:resolved[field]=saved.id if saved else None
    if fields.needs('billing_address',customer_changed) and 'billing_address' not in fields.supplied:
        inherited('billing_address')
    if ('shipping_address_id' in fields.supplied or refresh and old.shipping_address_id is not None or
        fields.needs('shipping_address',customer_changed) and 'shipping_address' not in fields.supplied):
        collection('address_mode','shipping_addresses')
    control=inp.deposit_to if 'deposit_to' in fields.supplied else old.control_account.id
    reference('deposit_to',control,old.control_account,refresh_value=refresh)
    if 'customer_message_item' in fields.supplied or refresh and old.customer_message_item and 'customer_message' not in fields.supplied:
        reference('customer_message_item',inp.customer_message_item if 'customer_message_item' in fields.supplied else old.customer_message_item.id,
            old.customer_message_item,refresh_value=refresh)
    tax=resolved['sales_tax_item']
    if tax and (refresh or 'sales_tax_item' in fields.defaults or old.sales_tax_item is None or tax!=old.sales_tax_item.id):
        value=reads.read('source_item',tax,'type')
        if value is not None:
            members=reads.read('source_item',tax,'members')['members'] if value['type']=='sales_tax_group' else [{'component_item_id':tax,'active':True}]
            for member in members:
                if not member['active']:continue
                rule=reads.read('source_item',member['component_item_id'],'name','version','active','type','tax_percent','tax_agency_vendor_id','liability_account_id')
                if rule is None:continue
                reads.read('source_vendor',rule['tax_agency_vendor_id'],'name','version','active','is_tax_agency')
                select('account',rule['liability_account_id'])
    price_id=resolved['price_level']
    if price_id:
        mask=history.source_fields.get(('source_price_level',price_id),())
        resolved['_price_version']=(reads.read('source_price_level',price_id,'version')['version'] if 'version' in mask
            else old.price_level.version if old.price_level and old.price_level.id==price_id else None)
    else:resolved['_price_version']=None
    lines=history.find('document_line',lambda row:row['revision_id']==header['current_revision_id'])
    by_line={row['line_id']:row for row in lines}
    submitted=inp.lines
    if submitted is None:
        from bookflow.company.sales_models import SalesLineInput
        submitted=[]
        for before in sorted(lines,key=lambda row:row['position']):
            saved=history.take('sales_line_profile',before['id'])
            if saved is not None:
                facts=SalesLineProfile.model_validate_json(canonical(saved['item_snapshot']))
                submitted.append(SalesLineInput(item=facts.item.id,line_id=before['line_id']))
    for line in submitted:
        before=by_line.get(line.line_id)
        saved_row=history.take('sales_line_profile',before['id']) if before else None
        previous=SalesLineProfile.model_validate_json(canonical(saved_row['item_snapshot'])) if saved_row else None
        if previous is not None and previous.pricing_basis=='allocated':
            # The existing complete work ancestry and allocation rows below are
            # the owning retained-line proof; no current item defaults are used.
            continue
        _source_line_dependencies(reads,select,line,previous,old,resolved,info,refresh,customer_changed)


def _source_owner_image(s, kind, row):
    if kind=='source_item':
        from bookflow.company import items
        return items.aggregate_snapshot(row,items._members(s.company,row['id']),(),())
    if kind=='source_customer':
        from bookflow.company import parties
        children=parties.read_party_collections(s.company,'customer',row['id'])
        return parties._snapshot(row,(),children)
    if kind=='source_price_level':
        from bookflow.company import pricing
        return pricing.aggregate_snapshot(row,pricing._children(s.company,row['id']))
    if kind=='source_unit':
        from bookflow.company import units
        children=units._children(s.company,row['id'])
        result=units.aggregate_snapshot(row,children)
        result['_unit_selector_rows']=[units._child_output(child).model_dump(mode='python') for child in children]
        return result
    return row


def _complete_unit_image(value, known):
    if not isinstance(value,dict) or type(value.get('units')) is not list or type(value.get('_units_identities')) is not list:
        raise MissingHistory('missing complete unit identities')
    for row in value['units']:
        if type(row) is not dict or type(row.get('id')) is not str:
            raise MissingHistory('malformed unit history')
        known[row['id']]=row
    rows=[];seen=set()
    for identity in value['_units_identities']:
        if (type(identity) is not dict or set(identity)!={'id','active'} or
            type(identity['id']) is not str or type(identity['active']) is not bool or
            identity['id'] in seen or identity['id'] not in known):
            raise MissingHistory('incomplete retired unit history')
        seen.add(identity['id'])
        rows.append({**known[identity['id']],'active':identity['active']})
    return {**value,'_unit_selector_rows':rows}


def _unit_matches(value, selector):
    from bookflow.company.units import UnitConversionOutput
    from bookflow.company.list_service import normalize_lookup_key
    try:
        rows=[UnitConversionOutput.model_validate(row) for row in value.get('_unit_selector_rows',value['units'])]
        if len({row.id for row in rows})!=len(rows):
            raise MissingHistory('duplicate unit identity')
    except (KeyError, TypeError, ValidationError) as exc:
        raise MissingHistory('malformed unit selector history') from exc
    return sorted(row.id for row in rows if selector in
                  (normalize_lookup_key(row.name),normalize_lookup_key(row.abbreviation)))


def _source_raw_child(table, row, owner_field, owner_id):
    if type(row) is not dict or row.get(owner_field)!=owner_id:
        raise MissingHistory('foreign source child owner')
    for column in table.c:
        if column.name not in row:
            raise MissingHistory('missing source child field')
        value=row[column.name]
        if value is None:
            if not column.nullable:raise MissingHistory('null source child field')
        elif column.type.python_type in (str,int,bool) and type(value) is not column.type.python_type:
            raise MissingHistory('malformed source child scalar')


def _source_image_types(kind,value,fields):
    from bookflow.core.money import is_currency
    from bookflow.core.exact import parse_percentage_millionths
    for key in fields:
        item=value.get(key)
        if key in ('price','cost','rounding_increment','rounding_offset') and item is not None:
            if (type(item) is not dict or set(item)!={'amount','minor_units','currency'} or
                type(item['minor_units']) is not int or type(item['currency']) is not str or not is_currency(item['currency'])):
                raise MissingHistory('malformed source money')
            from bookflow.core.money import Money
            if Money(item['minor_units'],item['currency']).to_dict()!=item:
                raise MissingHistory('inconsistent formatted source money')
        if key in ('tax_percent','charge_percent','percent') and item is not None:
            if type(item) is not str:raise MissingHistory('malformed source percent')
            try:parse_percentage_millionths(item)
            except (ValueError,BookflowError) as exc:raise MissingHistory('malformed source percent') from exc
        if key in ('members','units','items','shipping_addresses','contacts'):
            if type(item) is not list or any(type(row) is not dict or type(row.get('id')) is not str for row in item):
                raise MissingHistory('malformed source child collection')
            if len({row['id'] for row in item})!=len(item):raise MissingHistory('duplicate source child identity')
            if key in ('shipping_addresses','contacts'):
                table=c.customer_addresses if key=='shipping_addresses' else c.customer_contacts
                for row in item:
                    _source_raw_child(table,row,'customer_id',value['id'])
                    if key=='contacts':
                        points=row.get('points')
                        if type(points) is not list or len({point.get('id') for point in points if type(point) is dict})!=len(points):
                            raise MissingHistory('malformed source contact points')
                        for point in points:_source_raw_child(c.customer_contact_points,point,'contact_id',row['id'])
            if key in ('items','members'):
                expected={'id','position','active','item_id','price','percent','adjustment_basis'} if key=='items' else {'id','position','active','component_item_id','quantity','unit_id'}
                for row in item:
                    if set(row)!=expected or type(row['position']) is not int or row['position']<0 or type(row['active']) is not bool:
                        raise MissingHistory('malformed source logical child')
                    identity=row['item_id'] if key=='items' else row['component_item_id']
                    from bookflow.core.ids import is_ulid
                    if not is_ulid(identity):raise MissingHistory('malformed source child target')
                    if key=='items':
                        if row['adjustment_basis'] not in ('standard_price','cost','current_custom_price'):
                            raise MissingHistory('malformed source price basis')
                        _source_image_types(kind,row,('price','percent'))
                    else:
                        from bookflow.core.exact import parse_quantity_micro_units
                        try:parse_quantity_micro_units(row['quantity'])
                        except (TypeError,ValueError,BookflowError) as exc:raise MissingHistory('malformed source member quantity') from exc
                        if row['unit_id'] is not None and not is_ulid(row['unit_id']):raise MissingHistory('malformed source member unit')
            if key=='units':
                from bookflow.company.units import UnitConversionOutput
                try:
                    for row in item:UnitConversionOutput.model_validate(row)
                except ValidationError as exc:raise MissingHistory('malformed source unit child') from exc


class SourceReads:
    """Explicit historical field reads, never a SQL planner or fabricated Session."""
    def __init__(self,history,relation):self.history=history;self.relation=relation

    def image(self,kind,identity):
        h=self.history;h.load(kind)
        if not h.historical:
            value=h.raw[kind].get(identity)
            if value is None and h.entries[kind].get(identity):
                value=_audit_image(h.entries[kind][identity][-1]['after'])
            return value
        entries=[row for row in h.entries[kind].get(identity,()) if row['seq']<=h.cutoff and row['action']!='migrate']
        return _audit_image(entries[-1]['after']) if entries else None

    def read(self,kind,identity,*fields):
        h=self.history;key=(kind,identity)
        previous=set(h.source_fields.get(key,()))
        chosen=previous|{'id'}|set(fields)
        if kind not in ('source_price_level','source_price_version'):
            chosen.discard('version')  # Display version alone is not a new semantic dependency.
        h.source_fields[key]=tuple(sorted(chosen))
        if previous!=chosen:
            h.values.pop(key,None);h.anchors.pop(key,None)
        value=h.take(kind,identity)
        return value

    def captured_price(self,identity,version):
        kind='source_price_version';key=identity+'@'+str(version)
        self.history.load(kind)
        image=self.history.raw[kind].get(key)
        if image is None:
            self.history.unknown.add(kind+':'+key)
            return None
        fields=tuple(sorted(name for name in image if name not in _PROVENANCE and not name.startswith('_')))
        self.history.source_fields[kind,key]=fields
        value=self.history.take(kind,key)
        if not self.history.historical:
            try:
                current=self.history.raw['source_price_level'].get(identity)
                if current is None:raise MissingHistory('missing current captured price identity')
                if current['version']==version and _project(kind,current,fields)!=value:
                    raise MissingHistory('captured price current projection disagrees')
            except MissingHistory:
                self.history.unknown.add(kind+':'+key)
                return None
        return value

    def select(self,kind,selector,*fields):
        from bookflow.company.list_service import normalize_lookup_key
        h=self.history;h.load(kind)
        direct=selector.strip().upper()
        image=self.image(kind,direct)
        if image is not None:return self.read(kind,direct,*fields)
        normalized=normalize_lookup_key(selector);found=[]
        for identity in h.identities(kind):
            image=self.image(kind,identity)
            if image is not None and normalize_lookup_key(image.get('full_name') or image.get('name') or image.get('code'))==normalized:
                found.append(self.read(kind,identity,'full_name' if 'full_name' in image else 'name' if 'name' in image else 'code',*fields))
        self.relation('selector:'+kind,selector,[row for row in found if row is not None])
        if not found and getattr(h,'allow_missing_selectors',False):return None
        if len(found)!=1:
            if h.historical:raise MissingHistory('unresolved source selector')
            raise BookflowError('E_RECORD_NOT_FOUND')
        return found[0]


def _source_line_dependencies(reads, select, inp, old, old_header, header, info, refresh, customer_changed):
    from bookflow.company.sales_defaults import _Fields
    from bookflow.company.sales_facts import Origin
    refresh=refresh or inp.refresh_defaults
    fields=_Fields(inp,old,refresh,[])
    item=reads.select('source_item',inp.item)
    if item is None:return
    identity=item['id'];changed=old is None or identity!=old.item.id
    if changed or refresh:
        item=reads.read('source_item',identity,'name','full_name','version','active','type','sales_enabled',
            'charge_percent','income_account_id','price','cost')
        if item is None:return
        select('account',item['income_account_id'])
    if fields.needs('description',changed) and 'description' not in fields.supplied:
        reads.read('source_item',identity,'description')
    if fields.needs('unit',changed) or refresh:
        saved_unit=old.unit if old else None
        selector=inp.unit if 'unit' in fields.supplied else None if fields.needs('unit',changed) else saved_unit.id if saved_unit else None
        same=saved_unit is not None and selector==saved_unit.id
        if saved_unit and selector is not None and not same:
            from bookflow.company.list_service import normalize_lookup_key
            field='unit_selector:'+normalize_lookup_key(selector)
            value=reads.read('source_unit',saved_unit.set_id,field)
            same=value is not None and value[field]==[saved_unit.id]
        if info['units_of_measure_mode']!='disabled' and not (same and not changed and not refresh and 'unit' not in fields.defaults):
            value=reads.read('source_item',identity,'unit_of_measure_set_id')
            if value and value['unit_of_measure_set_id']:
                unit=value['unit_of_measure_set_id'];image=reads.image('source_unit',unit)
                if image is None:reads.read('source_unit',unit,'units')
                else:reads.read('source_unit',unit,*[key for key in image if key not in _PROVENANCE and not key.startswith('_')])
    for field,column in (('class_id','default_class_id'),('tax_code','sales_tax_code_id')):
        header_field='class_id' if field=='class_id' else 'customer_tax_code'
        prior=getattr(old_header,header_field)
        altered=customer_changed or header[header_field]!=(prior.id if prior else None)
        dependent=changed or altered and fields.origins.get(field,Origin(kind='default')).source_id!=identity
        if not fields.needs(field,dependent) and not refresh:continue
        saved=getattr(old,field) if old else None
        if field in fields.supplied:selector=getattr(inp,field)
        elif fields.needs(field,dependent):
            if field=='class_id' and not info['use_classes']:selector=None
            elif altered and old and not changed and not refresh and field not in fields.defaults:selector=header[header_field]
            else:
                value=reads.read('source_item',identity,column)
                selector=(value[column] if value else None) or header[header_field]
        else:selector=saved.id if saved else None
        if selector:
            if field=='class_id':select('class',selector)
            else:
                value=reads.select('source_tax_code',selector)
                if value and (refresh or field in fields.defaults or saved is None or saved.id!=value['id']):
                    reads.read('source_tax_code',value['id'],'code','version','active','taxable')
    amount=('net_amount' in fields.supplied or old is not None and old.pricing_basis=='amount' and
        not fields.supplied & {'unit_price','price_level'} and 'unit_price' not in fields.defaults)
    if not amount:
        rule=old.price_rule if old else None
        header_changed=customer_changed or ((header['price_level'],header['_price_version']) !=
            ((old_header.price_level.id,old_header.price_level.version) if old_header.price_level else (None,None)))
        dependent=header_changed and fields.defaulted('price_level')
        if 'price_level' in fields.supplied:selector=inp.price_level
        elif fields.needs('price_level',dependent):selector=header['price_level']
        else:selector=rule.id if rule else None
        chosen=reads.select('source_price_level',selector) if selector else None
        level=chosen['id'] if chosen else None
        level_changed=level!=(rule.id if rule else None)
        if level and info['enable_price_levels'] and (level_changed or changed or refresh or 'price_level' in fields.defaults):
            captured=None
            if not refresh and 'price_level' not in fields.supplied:
                if fields.defaulted('price_level') and header['price_level']==level:
                    captured=header['_price_version']
                elif not level_changed and rule is not None:captured=rule.version
            if captured is not None:
                reads.captured_price(level,captured)
            else:
                image=reads.image('source_price_level',level)
                if image is not None:
                    reads.read('source_price_level',level,*[key for key in image if key not in _PROVENANCE and not key.startswith('_')],'version')
