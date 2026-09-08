"""Closed co24 stored captures, decoded only AFTER external owner admission.

No registration, database access or visibility decision. Row/FK identity fields
are evidence; the caller must additionally prove their referenced history and
whole-event membership. Source/additional audit record IDs repeat by revision.
"""
from typing import Annotated, ClassVar, Literal, Mapping
import json
from pydantic import Field, model_validator
from bookflow.core.errors import BookflowError
from bookflow.hub import audit_projection_legacy as legacy

ID = Annotated[str, Field(pattern=r'^[0-9A-HJKMNP-TV-Z]{26}$')]
Positive = Annotated[int, Field(gt=0, le=9223372036854775807)]
Nonnegative = Annotated[int, Field(ge=0, le=9223372036854775807)]
Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
Units = Annotated[int, Field(ge=-9223372036854775808, le=9223372036854775807)]


def require(value):
    if not value:
        raise ValueError('inconsistent deposit draft capture')


class Created(legacy.View):
    # The trusted disclosure walker nulls provenance before serialization.
    # Stored captures remain required and nonnull through the inherited guard.
    _captured_nonnull: ClassVar[frozenset[str]] = frozenset({'created_at', 'created_by', 'created_via'})
    created_at: str | None
    created_by: ID | None
    created_via: str | None
    audit_event_id: ID


class Header(Created):
    _captured_nonnull: ClassVar[frozenset[str]] = frozenset({'version', 'updated_at', 'updated_by', 'updated_via'})
    id: ID
    version: Positive | None
    updated_at: str | None
    updated_by: ID | None
    updated_via: str | None
    current_revision_id: ID


class Draft(Header):
    tag: Literal['deposit_draft'] = 'deposit_draft'
    state: Literal['open', 'consumed', 'abandoned']
    edit_transaction_id: ID | None
    edit_type: Literal['deposit'] | None
    baseline_version: Positive | None
    baseline_revision_id: ID | None
    copy_transaction_id: ID | None
    copy_type: Literal['deposit'] | None
    copy_version: Positive | None
    copy_revision_id: ID | None
    consumed_revision_id: ID | None
    consumed_operation_id: ID | None

    @model_validator(mode='after')
    def agreement(self):
        for fields in ((self.edit_transaction_id, self.edit_type, self.baseline_version, self.baseline_revision_id),
                       (self.copy_transaction_id, self.copy_type, self.copy_version, self.copy_revision_id)):
            require(all(v is None for v in fields) or all(v is not None for v in fields))
        require(self.edit_transaction_id is None or self.copy_transaction_id is None)
        if self.state == 'consumed':
            require(self.consumed_revision_id == self.current_revision_id and self.consumed_operation_id is not None)
        else:
            require(self.consumed_revision_id is None and self.consumed_operation_id is None)
        return self


class Selection(Header):
    tag: Literal['deposit_selection'] = 'deposit_selection'
    state: Literal['open', 'accepted', 'abandoned']
    target_draft_id: ID
    target_revision_id: ID
    accepted_revision_id: ID | None

    @model_validator(mode='after')
    def agreement(self):
        require((self.state == 'accepted') == (self.accepted_revision_id is not None))
        return self


class Revision(Created):
    _captured_nonnull: ClassVar[frozenset[str]] = frozenset({'version'})
    _internal: ClassVar[frozenset[str]] = frozenset({'manifest_hash', 'high_water'})
    id: ID
    version: Positive | None
    previous_revision_id: ID | None
    snapshot: legacy.DepositAuditManifest
    manifest_hash: Digest
    high_water: Nonnegative

    @model_validator(mode='before')
    @classmethod
    def manifest(cls, value):
        if type(value) is dict:
            value = dict(value)
            raw = value.get('snapshot')
            if type(raw) is str:
                raw = json.loads(raw)
            # Use the existing pure composition owner, not a second assembler.
            from bookflow.company.deposit_draft_models import Manifest
            from bookflow.company.deposit_draft_validation import validate_manifest
            from bookflow.company.payment_queries import digest
            captured = Manifest.model_validate_json(json.dumps(raw, allow_nan=False))
            validate_manifest(captured)
            require(value.get('manifest_hash') == digest(captured.model_dump(mode='json')))
            require(value.get('high_water') == captured.high_water)
            value['snapshot'] = raw
        return value

    @model_validator(mode='after')
    def chain(self):
        require((self.version == 1) == (self.previous_revision_id is None))
        require(self.previous_revision_id != self.id)
        return self


class DraftRevision(Revision):
    tag: Literal['deposit_draft_revision'] = 'deposit_draft_revision'
    draft_id: ID
    bank_account_id: ID | None
    cashback_account_id: ID | None

    @model_validator(mode='after')
    def accounts(self):
        header = self.snapshot.header
        require(self.bank_account_id == (header.bank.id if header.bank else None))
        cash = header.cash_back
        require(self.cashback_account_id == (cash.account.id if cash and cash.account else None))
        return self


class SelectionRevision(Revision):
    tag: Literal['deposit_selection_revision'] = 'deposit_selection_revision'
    selection_id: ID
    target_draft_id: ID
    target_revision_id: ID

    @model_validator(mode='after')
    def sources_only(self):
        require(not self.snapshot.additional)
        return self


class RowKey(Created):
    tag: Literal['deposit_draft_row_key'] = 'deposit_draft_row_key'
    _internal: ClassVar[frozenset[str]] = frozenset({'edit_transaction_id', 'original_row_id'})
    id: ID
    draft_id: ID
    kind: Literal['source', 'additional']
    ordinal: Positive
    edit_transaction_id: ID | None
    original_row_id: ID | None

    @model_validator(mode='after')
    def origin_pair(self):
        # Original owner, NOT destination/edit authority for a copied draft.
        require((self.edit_transaction_id is None) == (self.original_row_id is None))
        return self


class Source(Created):
    _captured_nonnull: ClassVar[frozenset[str]] = frozenset({'source_transaction_id', 'source_type'})
    revision_id: ID
    row_id: ID
    ordinal: Positive
    source_transaction_id: ID | None
    source_type: Literal['payment', 'sales_receipt'] | None
    expected_header_version: Positive
    source_revision_id: ID
    snapshot: legacy.DepositAuditSource
    memo: str | None
    memo_origin: Literal['source', 'entered']

    @model_validator(mode='before')
    @classmethod
    def captured_source(cls, value):
        if type(value) is dict:
            value = dict(value)
            raw = value.get('snapshot')
            if type(raw) is str:
                raw = json.loads(raw)
            from bookflow.company.deposit_draft_models import Source as OwnedSource
            OwnedSource.model_validate_json(json.dumps(raw, allow_nan=False))
            value['snapshot'] = raw
        return value

    @model_validator(mode='after')
    def identity(self):
        row = self.snapshot
        source = row.source
        require((self.row_id, self.ordinal, self.source_transaction_id, self.source_type,
                 self.expected_header_version, self.source_revision_id, self.memo, self.memo_origin) ==
                (row.row_id, row.ordinal, source.transaction_id, source.source_type,
                 source.expected_header_version, source.revision_id, row.memo, row.memo_origin))
        require(row.memo_origin != 'source' or row.memo == source.source_memo)
        return self


class DraftSource(Source):
    tag: Literal['deposit_draft_source'] = 'deposit_draft_source'
    draft_id: ID
    kind: Literal['source']


class SelectionSource(Source):
    tag: Literal['deposit_selection_source'] = 'deposit_selection_source'
    selection_id: ID


class Additional(Created):
    tag: Literal['deposit_draft_additional'] = 'deposit_draft_additional'
    revision_id: ID
    draft_id: ID
    row_id: ID
    ordinal: Positive
    kind: Literal['additional']
    party_kind: Literal['customer', 'vendor', 'employee', 'other_name'] | None
    customer_id: ID | None
    vendor_id: ID | None
    employee_id: ID | None
    other_name_id: ID | None
    account_id: ID | None
    class_id: ID | None
    payment_method_id: ID | None
    amount_minor_units: Units | None
    currency: Annotated[str, Field(min_length=3, max_length=3)]
    snapshot: legacy.DepositAuditAdditional

    @model_validator(mode='before')
    @classmethod
    def captured_additional(cls, value):
        if type(value) is dict:
            value = dict(value)
            raw = value.get('snapshot')
            if type(raw) is str:
                raw = json.loads(raw)
            from bookflow.company.deposit_draft_models import Additional as OwnedAdditional
            OwnedAdditional.model_validate_json(json.dumps(raw, allow_nan=False))
            value['snapshot'] = raw
        return value

    @model_validator(mode='after')
    def identity(self):
        row = self.snapshot
        require((self.row_id, self.ordinal, self.amount_minor_units) == (row.row_id, row.ordinal, row.units))
        require((self.account_id, self.class_id, self.payment_method_id) ==
                (row.account.id if row.account else None, row.class_ref.id if row.class_ref else None,
                 row.payment_method.id if row.payment_method else None))
        party = row.received_from
        require(self.party_kind == (party.kind if party else None))
        for kind, value in (('customer', self.customer_id), ('vendor', self.vendor_id),
                            ('employee', self.employee_id), ('other_name', self.other_name_id)):
            require(value == (party.id if party and party.kind == kind else None))
        return self


class Consumption(Created):
    tag: Literal['deposit_draft_consumption'] = 'deposit_draft_consumption'
    _internal: ClassVar[frozenset[str]] = frozenset({'manifest_hash'})
    operation_id: ID
    draft_id: ID
    revision_id: ID
    manifest_hash: Digest


# Literal emitted spellings, table/PK inventory; never adopt schema at runtime.
MODELS = {
    'deposit_draft': Draft, 'deposit_selection': Selection,
    'deposit_draft_revision': DraftRevision, 'deposit_selection_revision': SelectionRevision,
    'deposit_draft_row_key': RowKey, 'deposit_draft_source': DraftSource,
    'deposit_selection_source': SelectionSource, 'deposit_draft_additional': Additional,
    'deposit_draft_consumption': Consumption,
}
TABLES = {
    'deposit_draft': ('deposit_drafts', ('id',)),
    'deposit_selection': ('deposit_selections', ('id',)),
    'deposit_draft_revision': ('deposit_draft_revisions', ('id',)),
    'deposit_selection_revision': ('deposit_selection_revisions', ('id',)),
    'deposit_draft_row_key': ('deposit_draft_row_keys', ('id',)),
    'deposit_draft_source': ('deposit_draft_sources', ('revision_id', 'row_id')),
    'deposit_selection_source': ('deposit_selection_sources', ('revision_id', 'row_id')),
    'deposit_draft_additional': ('deposit_draft_additional', ('revision_id', 'row_id')),
    'deposit_draft_consumption': ('deposit_draft_consumptions', ('operation_id',)),
}
DRAFT_COMMANDS = ('deposit draft create', 'deposit draft update', 'deposit draft clear', 'deposit draft abandon')
SELECTION_COMMANDS = ('deposit selection create', 'deposit selection update', 'deposit selection clear',
                      'deposit selection abandon', 'deposit selection accept', 'deposit selection select-matching')
# Consumption touches can accompany coordinate; its receipt codec is NOT owned here.
FINANCIAL_COMMANDS = ('deposit post', 'deposit update', 'deposit coordinate')
PRODUCERS = {
    'deposit_draft': frozenset([(x, 'create') for x in DRAFT_COMMANDS[:1]] +
        [(x, 'update') for x in (*DRAFT_COMMANDS[1:], 'deposit selection accept', *FINANCIAL_COMMANDS)]),
    'deposit_selection': frozenset([(SELECTION_COMMANDS[0], 'create')] + [(x, 'update') for x in SELECTION_COMMANDS[1:]]),
    'deposit_draft_revision': frozenset((x, 'create') for x in (*DRAFT_COMMANDS, 'deposit selection accept')),
    'deposit_selection_revision': frozenset((x, 'create') for x in SELECTION_COMMANDS),
    'deposit_draft_row_key': frozenset((x, 'create') for x in ('deposit draft create', 'deposit draft update', 'deposit selection accept')),
    'deposit_draft_source': frozenset((x, 'create') for x in ('deposit draft create', 'deposit draft update', 'deposit draft abandon', 'deposit selection accept')),
    'deposit_selection_source': frozenset((x, 'create') for x in ('deposit selection create', 'deposit selection update', 'deposit selection abandon', 'deposit selection accept', 'deposit selection select-matching')),
    'deposit_draft_additional': frozenset((x, 'create') for x in ('deposit draft create', 'deposit draft update', 'deposit draft abandon', 'deposit selection accept')),
    'deposit_draft_consumption': frozenset((x, 'create') for x in FINANCIAL_COMMANDS),
}
# Registration-ready metadata, NOT permissions. Nested captures reuse legacy routes.
REFERENCE_GROUPS = {
    Draft: ((('edit_transaction_id',), 'deposit'), (('copy_transaction_id',), 'deposit')),
    DraftRevision: ((('bank_account_id', 'cashback_account_id'), 'account'),),
    Additional: ((('account_id',), 'account'), (('class_id',), 'class'), (('payment_method_id',), 'payment_method'),
                 (('customer_id',), 'customer'), (('vendor_id',), 'vendor'), (('employee_id',), 'employee'), (('other_name_id',), 'other_name')),
}
# Same discriminator pair convention as the existing polymorphic party owner.
SOURCE_ROUTES = {DraftSource: ('source_type', 'source_transaction_id'), SelectionSource: ('source_type', 'source_transaction_id')}
PARTY_ROUTES = {Additional: ('party_kind', (('customer', 'customer_id'), ('vendor', 'vendor_id'), ('employee', 'employee_id'), ('other_name', 'other_name_id')))}
FIELD_REQUIREMENTS = {}  # No new fixed capability gate; use selected typed reference owners.
# PARTY_ROUTES requires the selected discriminator, never an all-party conjunction.
# The reused snapshot classes already carry legacy custom-field/account/party routes.
OWNER_EDGES = {
    Draft: (('current_revision_id', 'deposit_draft_revision'), ('consumed_revision_id', 'deposit_draft_revision'), ('consumed_operation_id', 'deposit_operation')),
    Selection: (('target_draft_id', 'deposit_draft'), ('target_revision_id', 'deposit_draft_revision'), ('current_revision_id', 'deposit_selection_revision'), ('accepted_revision_id', 'deposit_draft_revision')),
    DraftRevision: (('draft_id', 'deposit_draft'), ('previous_revision_id', 'deposit_draft_revision')),
    SelectionRevision: (('selection_id', 'deposit_selection'), ('previous_revision_id', 'deposit_selection_revision'), ('target_draft_id', 'deposit_draft'), ('target_revision_id', 'deposit_draft_revision')),
    RowKey: (('draft_id', 'deposit_draft'),),
    DraftSource: (('draft_id', 'deposit_draft'), ('revision_id', 'deposit_draft_revision'), ('row_id', 'deposit_draft_row_key')),
    SelectionSource: (('selection_id', 'deposit_selection'), ('revision_id', 'deposit_selection_revision')),
    Additional: (('draft_id', 'deposit_draft'), ('revision_id', 'deposit_draft_revision'), ('row_id', 'deposit_draft_row_key')),
    Consumption: (('draft_id', 'deposit_draft'), ('revision_id', 'deposit_draft_revision'), ('operation_id', 'deposit_operation')),
}


DraftRecord = Draft | Selection | DraftRevision | SelectionRevision | RowKey | DraftSource | SelectionSource | Additional | Consumption


def decode_snapshot(*, producer: str, record_type: str, action: str,
                    snapshot: Mapping[str, object], record_id: str | None = None) -> DraftRecord:
    """Validate one raw captured before/after; caller supplies stored entry identity.

    Cross-row revision/FK/event/history proofs remain the caller's owner obligation.
    A before image on an update is admitted using the same emitted action.
    """
    try:
        require(type(snapshot) is dict and (producer, action) in PRODUCERS.get(record_type, ()))
        model = MODELS[record_type].model_validate(snapshot)
        identity = model.row_id if record_type in ('deposit_draft_source', 'deposit_selection_source', 'deposit_draft_additional') else model.operation_id if record_type == 'deposit_draft_consumption' else model.id
        require(record_id is None or record_id == identity)
        return model
    except (ValueError, TypeError, KeyError, BookflowError):
        raise BookflowError('E_VALIDATION', details={'reason': 'audit_format'}) from None
