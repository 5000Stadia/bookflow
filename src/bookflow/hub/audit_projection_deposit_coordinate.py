"""Closed coordinate receipt rows; private, without event registration."""
from __future__ import annotations
import json
from typing import Literal, Annotated, ClassVar
from pydantic import Field, model_validator
from bookflow.hub import audit_projection_legacy as legacy


class CoordinateApplicationAllocationsRow(legacy.ApplicationAllocationView):
    tag: Literal['application_allocation'] = Field('application_allocation', exclude=True)


class CoordinateApplicationsRow(legacy.ApplicationView):
    tag: Literal['application'] = Field('application', exclude=True)


class CoordinateDocumentLineIdentitiesRow(legacy.DocumentLineIdentityView):
    tag: Literal['document_line_identity'] = Field('document_line_identity', exclude=True)


class CoordinateDocumentLinesRow(legacy.DocumentLineView):
    tag: Literal['document_line'] = Field('document_line', exclude=True)


class CoordinatePaymentComponentKeysRow(legacy.PaymentComponentKeyView):
    tag: Literal['payment_component_key'] = Field('payment_component_key', exclude=True)


class CoordinatePaymentComponentsRow(legacy.PaymentComponentView):
    tag: Literal['payment_component'] = Field('payment_component', exclude=True)


class CoordinatePaymentProfilesRow(legacy.PaymentProfileView):
    tag: Literal['payment_profile'] = Field('payment_profile', exclude=True)


class CoordinatePostingBatchesRow(legacy.PostingBatchView):
    tag: Literal['posting_batch'] = Field('posting_batch', exclude=True)


class CoordinatePostingLineSourcesRow(legacy.PostingLineSourceView):
    tag: Literal['posting_line_source'] = Field('posting_line_source', exclude=True)


class CoordinatePostingLinesRow(legacy.PostingLineView):
    tag: Literal['posting_line'] = Field('posting_line', exclude=True)


class CoordinateSalesLineProfilesRow(legacy.SalesLineProfileView):
    tag: Literal['sales_line_profile'] = Field('sales_line_profile', exclude=True)


class CoordinateSalesProfilesRow(legacy.SalesProfileView):
    tag: Literal['sales_profile'] = Field('sales_profile', exclude=True)


class CoordinateSalesTaxAttributionLinesRow(legacy.SalesTaxAttributionLineView):
    tag: Literal['sales_tax_attribution_line'] = Field('sales_tax_attribution_line', exclude=True)


class CoordinateSalesTaxAttributionsRow(legacy.SalesTaxAttributionView):
    tag: Literal['sales_tax_attribution'] = Field('sales_tax_attribution', exclude=True)


class CoordinateSalesTaxComponentsRow(legacy.SalesTaxComponentView):
    tag: Literal['sales_tax_component'] = Field('sales_tax_component', exclude=True)


class CoordinateSalesTaxLineKeysRow(legacy.SalesTaxLineKeyView):
    tag: Literal['sales_tax_line_key'] = Field('sales_tax_line_key', exclude=True)


class CoordinateSettlementLineKeysRow(legacy.SettlementLineKeyView):
    tag: Literal['settlement_line_key'] = Field('settlement_line_key', exclude=True)


class CoordinateTransactionRevisionsRow(legacy.TransactionRevisionView):
    tag: Literal['transaction_revision'] = Field('transaction_revision', exclude=True)


class CoordinateTransactionsRow(legacy.TransactionView):
    tag: Literal['transaction'] = Field('transaction', exclude=True)


class CoordinateWorkBillingAllocationsRow(legacy.WorkBillingAllocationView):
    tag: Literal['work_billing_allocation'] = Field('work_billing_allocation', exclude=True)


class CoordinateSourceRows(legacy.View):
    application_allocations: tuple[CoordinateApplicationAllocationsRow, ...] = ()
    applications: tuple[CoordinateApplicationsRow, ...] = ()
    document_line_identities: tuple[CoordinateDocumentLineIdentitiesRow, ...] = ()
    document_lines: tuple[CoordinateDocumentLinesRow, ...] = ()
    payment_component_keys: tuple[CoordinatePaymentComponentKeysRow, ...] = ()
    payment_components: tuple[CoordinatePaymentComponentsRow, ...] = ()
    payment_profiles: tuple[CoordinatePaymentProfilesRow, ...] = ()
    posting_batches: tuple[CoordinatePostingBatchesRow, ...] = ()
    posting_line_sources: tuple[CoordinatePostingLineSourcesRow, ...] = ()
    posting_lines: tuple[CoordinatePostingLinesRow, ...] = ()
    sales_line_profiles: tuple[CoordinateSalesLineProfilesRow, ...] = ()
    sales_profiles: tuple[CoordinateSalesProfilesRow, ...] = ()
    sales_tax_attribution_lines: tuple[CoordinateSalesTaxAttributionLinesRow, ...] = ()
    sales_tax_attributions: tuple[CoordinateSalesTaxAttributionsRow, ...] = ()
    sales_tax_components: tuple[CoordinateSalesTaxComponentsRow, ...] = ()
    sales_tax_line_keys: tuple[CoordinateSalesTaxLineKeysRow, ...] = ()
    settlement_line_keys: tuple[CoordinateSettlementLineKeysRow, ...] = ()
    transaction_revisions: tuple[CoordinateTransactionRevisionsRow, ...] = ()
    work_billing_allocations: tuple[CoordinateWorkBillingAllocationsRow, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def captured_owner(cls, value):
        from bookflow.company.deposit_coordinate_models import SourceRows
        if type(value) is dict:
            SourceRows.model_validate_json(json.dumps(value, allow_nan=False))
        return value


class CoordinateHeader(legacy.View):
    before: CoordinateTransactionsRow
    after: CoordinateTransactionsRow

    @model_validator(mode="before")
    @classmethod
    def captured_owner(cls, value):
        from bookflow.company.deposit_coordinate_models import CoordinateHeader as Owner
        if type(value) is dict:
            Owner.model_validate_json(json.dumps(value, allow_nan=False))
        return value

class CoordinateReceiptUpdateInput(legacy.View):
    _internal: ClassVar[frozenset[str]] = frozenset({'expected_facts_fingerprint'})
    sales_tax_calculation: Literal['line_component_half_even','line_combined_half_up','invoice_combined_half_up'] = None
    number: str | None = None
    memo: str | None = None
    customer_message: str | None = None
    customer_message_item: str | None = None
    customer_purchase_order: str | None = None
    billing_address: legacy.Address | None = None
    shipping_address: legacy.Address | None = None
    shipping_address_id: str | None = None
    ship_date: str | None = None
    ship_method: str | None = None
    sales_rep: str | None = None
    class_id: str | None = None
    customer_tax_code: str | None = None
    sales_tax_item: str | None = None
    price_level: str | None = None
    refresh_defaults: bool = False
    use_defaults: tuple[Literal['sales_tax_calculation','billing_address','shipping_address','terms','due_date','ship_method','sales_rep','class_id','customer_tax_code','sales_tax_item','price_level','payment_method'],...] = ()
    expected_facts_fingerprint: str | None = None
    custom_fields: dict[str,str|bool|int|None] | None = {}
    custom_field_kinds: dict[str,Literal['text','number','date','bool','choice']] | None = {}
    expected_version: int | None = None
    date: str | None = None
    customer: str | None = None
    lines: tuple[legacy.InvoiceAuditSalesLineInput,...] | None = None
    payment_method: str | None = None
    payment_reference: str | None = None
    sales_receipt: str
    deposit_to: str | None = None
    amount_received: str | legacy.SalesMoneyInput | None = None


class CoordinatePaymentUpdate(legacy.View):
    kind: Literal['payment_update']
    input: legacy.UpdateIntent


class CoordinateReceiptUpdate(legacy.View):
    kind: Literal['sales_receipt_update']
    input: CoordinateReceiptUpdateInput


class CoordinatePaymentVoid(legacy.View):
    kind: Literal['payment_void']
    payment: str
    expected_version: int
    unapply: Literal['retain_none', 'all_active']


class CoordinateReceiptVoid(legacy.View):
    kind: Literal['sales_receipt_void']
    sales_receipt: str
    expected_version: int


CoordinateAction = Annotated[CoordinatePaymentUpdate | CoordinateReceiptUpdate | CoordinatePaymentVoid | CoordinateReceiptVoid, Field(discriminator='kind')]


class CoordinatePaymentOutput(legacy.View):
    _internal: ClassVar[frozenset[str]] = frozenset({'facts_fingerprint'})
    dry_run: bool = False
    warnings: tuple[str, ...] = ()
    changed: bool = True
    new_effect: bool = True
    id: str | None
    version: int
    facts_fingerprint: str
    idempotent_replay: bool = False
    effect: legacy.PaymentEffectResultView
    current: legacy.PaymentCurrentResultView
    effect_counts: legacy.PaymentEffectCountsView
    # The aggregate writer retains prepare().preview, before page descriptors.
    prospective_pages: tuple[()] = ()


class CoordinateStatementRef(legacy.View):
    producer: Literal['journal_entry', 'payment', 'sales_receipt', 'invoice', 'deposit']
    transaction_id: str
    component_id: str
    role: Literal['entered', 'cash', 'control', 'net', 'main_bank', 'cash_back', 'additional']


class CoordinateMovement(legacy.View):
    producer: Literal['journal_entry', 'payment', 'sales_receipt', 'invoice', 'deposit']
    transaction_id: str
    revision_id: str
    account_id: str | None
    role: Literal['entered', 'cash', 'control', 'net', 'main_bank', 'cash_back', 'additional']
    component_id: str | None = None
    _captured_nonnull: ClassVar[frozenset[str]] = frozenset({'account_id'})


class CoordinateStatementVersion(legacy.View):
    _internal: ClassVar[frozenset[str]] = frozenset({'provenance'})
    _captured_nonnull: ClassVar[frozenset[str]] = frozenset({'account_id', 'account_type', 'payees'})
    ref: CoordinateStatementRef
    version_id: str
    revision_id: str
    business_batch_id: str
    transition_batch_id: str | None = None
    audit_event_id: str
    movement_key: CoordinateMovement
    account_id: str | None
    account_type: Literal['bank', 'credit_card'] | None
    currency: str
    effective_date: str
    signed_debit: int
    active: bool
    number: str
    memo: str | None
    payees: tuple[str, ...] | None = ()
    posting_line_ids: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    document_line_ids: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()


class CoordinateChangedEffects(legacy.View):
    kind: Literal['changed_effects'] = 'changed_effects'
    before: tuple[CoordinateStatementVersion, ...]
    after: tuple[CoordinateStatementVersion, ...]
    authority_transactions: tuple[str, ...]
    scope: Literal['present_source_aggregate'] = 'present_source_aggregate'


class CoordinateUnsupportedPopulation(legacy.View):
    _captured_nonnull: ClassVar[frozenset[str]] = frozenset({'account_id', 'account_currency'})
    kind: Literal['account_currency_unsupported', 'population_unsupported']
    account_id: str | None
    account_currency: str | None
    home_currency: str
    reason: str = 'unsupported_producer_or_attribution'


class CoordinateCustomValue(legacy.View):
    id: str
    def_id: str
    record_type: str
    record_id: str
    active: bool
    canonical_text: str


class CoordinateCustomChange(legacy.View):
    kind: Literal['custom'] = 'custom'
    before: CoordinateCustomValue | None
    after: CoordinateCustomValue


class CoordinateSourceEvidence(legacy.View):
    action: CoordinateAction
    before_header: CoordinateTransactionsRow
    after_header: CoordinateTransactionsRow
    before: CoordinateSourceRows
    inserted: CoordinateSourceRows
    custom_changes: tuple[CoordinateCustomChange, ...] | None
    payment_effect: CoordinatePaymentOutput | None
    bank_changes: CoordinateChangedEffects | CoordinateUnsupportedPopulation
    _captured_nonnull: ClassVar[frozenset[str]] = frozenset({'custom_changes'})


class CoordinateIdentity(legacy.View):
    owner_kind: str
    logical_key: str
    physical_id: str


class CoordinateEffect(legacy.View):
    source: CoordinateSourceEvidence
    deposit: legacy.DepositAuditLifecycleEffect
    headers: tuple[CoordinateHeader, ...]
    identities: tuple[CoordinateIdentity, ...]
    target_ids: tuple[str, ...]


class CoordinateOutput(legacy.View):
    _internal: ClassVar[frozenset[str]] = frozenset({'facts_fingerprint', 'dependency_guard'})
    current_draft: legacy.DepositAuditConsumedDraftState | None = None
    schema_version: Literal[2] = 2
    command: Literal['deposit coordinate'] = 'deposit coordinate'
    operation_key: str
    operation_id: str
    changed: bool
    new_effect: bool
    idempotent_replay: bool = False
    facts_fingerprint: str
    dependency_guard: str
    effect: CoordinateEffect
    current: legacy.DepositAuditDocumentState
    current_headers: tuple[CoordinateTransactionsRow, ...]
    current_source_rows: CoordinateSourceRows

    @model_validator(mode='before')
    @classmethod
    def original_capture(cls, value):
        from bookflow.company.deposit_coordinate_models import CoordinateOutput as Owner
        if type(value) is dict:
            Owner.model_validate_json(json.dumps(value, allow_nan=False))
        return value


    @model_validator(mode='after')
    def receipt_agreement(self):
        effect = self.effect
        source = effect.source
        if self.current != effect.deposit.after:
            raise ValueError('coordinate deposit current capture differs')
        targets = effect.target_ids
        current = {row.id: row for row in self.current_headers}
        if len(set(targets)) != len(targets) or len(current) != len(self.current_headers) or set(current) != set(targets):
            raise ValueError('coordinate target header set differs')
        if self.current.id not in current:
            raise ValueError('coordinate deposit target absent')
        changed = set()
        for header in effect.headers:
            if header.before.id != header.after.id or header.after.id in changed or header.after.version != header.before.version + 1:
                raise ValueError('coordinate changed header differs')
            if current.get(header.after.id) != header.after:
                raise ValueError('coordinate current header differs')
            changed.add(header.after.id)
        action = source.action
        intent = action.input if hasattr(action, 'input') else action
        identity = intent.payment if action.kind.startswith('payment_') else intent.sales_receipt
        if (source.before_header.id != identity or source.after_header.id != identity
                or source.before_header.version != intent.expected_version
                or current.get(identity) != source.after_header):
            raise ValueError('coordinate source capture differs')
        if (source.payment_effect is not None) != action.kind.startswith('payment_'):
            raise ValueError('coordinate source receipt kind differs')
        for name in CoordinateSourceRows.model_fields:
            if name == 'projection_partial':
                continue
            if getattr(self.current_source_rows, name) != getattr(source.before, name) + getattr(source.inserted, name):
                raise ValueError('coordinate source row composition differs')
        for key, expected in (('operation_id', self.operation_id), ('event', effect.deposit.audit_event_id)):
            matches = [row.physical_id for row in effect.identities if row.owner_kind == 'aggregate' and row.logical_key == key]
            # Source identity maps may already own the shared event identifier.
            if matches and matches != [expected]:
                raise ValueError('coordinate aggregate identity differs')
            if not any(row.physical_id == expected for row in effect.identities):
                raise ValueError('coordinate aggregate identity absent')
        return self


# Descriptors for the future event adapter; importing this module grants nothing.
REFERENCE_GROUPS = {
    CoordinateReceiptUpdateInput: (
        (('payment_method',), 'payment_method'), (('deposit_to',), 'account'),
        (('customer',), 'customer'), (('customer_message_item',), 'customer_message'),
        (('shipping_address_id',), 'customer'), (('ship_method',), 'ship_method'),
        (('sales_rep',), 'sales_rep'), (('class_id',), 'class'),
        (('customer_tax_code',), 'sales_tax_code'), (('sales_tax_item',), 'item'),
        (('price_level',), 'price_level'),
    ),
    CoordinateMovement: ((('account_id',), 'account'),),
    CoordinateStatementVersion: ((('account_id', 'account_type'), 'account'),),
    CoordinateUnsupportedPopulation: ((('account_id', 'account_currency'), 'account'),),
}
FIELD_REQUIREMENTS = {
    (CoordinateReceiptUpdateInput, 'billing_address'): ('customer',),
    (CoordinateReceiptUpdateInput, 'shipping_address'): ('customer',),
    (CoordinateReceiptUpdateInput, 'custom_fields'): ('custom_field',),
    (CoordinateReceiptUpdateInput, 'custom_field_kinds'): ('custom_field',),
    (CoordinateSourceEvidence, 'custom_changes'): ('custom_field',),
    (CoordinateStatementVersion, 'payees'): ('customer',),
}


class CoordinateSourceResult(legacy.View):
    source_result: Literal[True]
    source: str
    memo_override: str | None = None


class CoordinateReplacementDocument(legacy.DepositAuditReplacementDocument):
    sources: tuple[legacy.DepositAuditSourceInput | CoordinateSourceResult, ...] = ()


class CoordinateDocumentReplacement(legacy.View):
    mode: Literal['document']
    document: Annotated[CoordinateReplacementDocument | legacy.DepositAuditDraftDocument, Field(discriminator='mode')]
    draft_source_result: Literal['retain', 'remove'] | None = None


class CoordinateVoidReplacement(legacy.View):
    mode: Literal['void']


class CoordinateInput(legacy.View):
    _internal: ClassVar[frozenset[str]] = frozenset({'dependency_guard', 'expected_facts_fingerprint'})
    deposit: str
    expected_version: int
    source_action: CoordinateAction
    replacement: Annotated[CoordinateDocumentReplacement | CoordinateVoidReplacement, Field(discriminator='mode')]
    dependency_guard: str | None = None
    expected_facts_fingerprint: str | None = None


class CoordinateItemManifest(legacy.View):
    count: int = Field(ge=0)
    digest: str = Field(pattern=r'^[0-9a-f]{64}$')


CoordinateItemKind = Literal['request_sources', 'request_additional', 'memberships', 'document_changes', 'cash_allocations', 'bank_changes', 'source_components', 'source_applications', 'source_allocations', 'source_document_changes']


class CoordinateRequest(legacy.DepositAuditRequest):
    _internal: ClassVar[frozenset[str]] = legacy.DepositAuditRequest._internal | frozenset({'item_manifests'})
    schema_version: Literal[2]
    command: Literal['deposit coordinate']
    input: CoordinateInput
    resolved_identity_map: tuple[CoordinateIdentity, ...] | None
    item_manifests: dict[CoordinateItemKind, CoordinateItemManifest] | None
    _captured_nonnull: ClassVar[frozenset[str]] = frozenset({'item_manifests'})


class CoordinateOperation(legacy.View):
    tag: Literal['deposit_operation'] = 'deposit_operation'
    _internal: ClassVar[frozenset[str]] = frozenset({'request_hash'})
    _captured_nonnull: ClassVar[frozenset[str]] = frozenset({'created_at', 'created_by', 'created_via'})
    id: str
    operation_key: str
    command: Literal['deposit coordinate']
    transaction_id: str
    request_hash: str
    request_snapshot: CoordinateRequest
    effect_snapshot: CoordinateOutput
    created_at: str | None
    created_by: str | None
    created_via: str | None
    audit_event_id: str

    @model_validator(mode='before')
    @classmethod
    def owning_input_and_pages(cls, value):
        if type(value) is dict:
            from bookflow.company import deposit_coordinate_models as owned
            from bookflow.company.deposit_operation_pages import collections
            from bookflow.company.payment_queries import digest
            try:
                request = value['request_snapshot']
                effect = value['effect_snapshot']
                if type(request) is str: request = json.loads(request)
                if type(effect) is str: effect = json.loads(effect)
                original = request['input']
                if 'operation_key' in original: raise ValueError('coordinate key belongs to receipt')
                owned.CoordinateInput.model_validate_json(json.dumps(dict(original, operation_key=value['operation_key']), allow_nan=False))
                output = owned.CoordinateOutput.model_validate_json(json.dumps(effect, allow_nan=False))
                expected = {kind: dict(count=len(items), digest=digest(items)) for kind, items in collections(output).items()}
                if request['item_manifests'] != expected:
                    raise ValueError('coordinate item manifest differs')
            except (KeyError, TypeError):
                raise ValueError('invalid coordinate operation capture') from None
        return value

    @model_validator(mode='after')
    def receipt_identity(self):
        request = self.request_snapshot
        output = self.effect_snapshot
        effect = output.effect.deposit
        if (self.id, self.operation_key, self.audit_event_id, self.transaction_id) != (output.operation_id, output.operation_key, effect.audit_event_id, output.current.id):
            raise ValueError('coordinate operation identity differs')
        if request.input.deposit != self.transaction_id or request.input.source_action != output.effect.source.action:
            raise ValueError('coordinate original intent differs')
        if effect.before is None or effect.before.id != self.transaction_id or effect.before.version != request.input.expected_version:
            raise ValueError('coordinate prior deposit differs')
        if request.resolved_transaction_ids != output.effect.target_ids or request.resolved_identity_map != output.effect.identities:
            raise ValueError('coordinate resolved identities differ')
        pin = request.resolved_draft
        consumed = effect.consumed_draft
        current = output.current_draft
        replacement = request.input.replacement
        document = replacement.document if replacement.mode == 'document' else None
        if document is not None and document.mode == 'draft':
            if pin is None or (document.draft, document.expected_version) != (pin.id, pin.version):
                raise ValueError('coordinate draft target differs')
        elif pin is not None:
            raise ValueError('coordinate inline intent has draft pin')
        if pin is None:
            if consumed is not None or current is not None:
                raise ValueError('unexpected coordinate consumption')
        else:
            if consumed is None or current is None:
                raise ValueError('missing coordinate consumption')
            if (pin.id, pin.version, pin.revision_id, pin.manifest_hash, pin.snapshot) != (consumed.draft_id, consumed.version, consumed.revision_id, consumed.manifest_hash, consumed.snapshot):
                raise ValueError('coordinate draft capture differs')
            if (current.id, current.version, current.revision_id, current.manifest_hash, current.operation_id) != (pin.id, pin.version+1, pin.revision_id, pin.manifest_hash, self.id):
                raise ValueError('coordinate consumed draft differs')
        return self
