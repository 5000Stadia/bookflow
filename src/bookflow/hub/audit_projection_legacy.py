"""Closed captured company history models, selected by explicit producer/kind.

This module decodes already-authorized evidence. It never grants access, queries a
DB, or adopts SQL columns at runtime. Model/producer conformance is a release gate.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Annotated, Literal, Mapping, ClassVar, get_origin, get_args
import json
from pydantic import BaseModel, ConfigDict, Field, model_validator, model_serializer
from bookflow.core.errors import BookflowError


class View(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    _internal: ClassVar[frozenset[str]] = frozenset()
    _captured_nonnull: ClassVar[frozenset[str]] = frozenset()
    projection_partial: bool = Field(False,exclude=True,repr=False)

    @model_validator(mode='before')
    @classmethod
    def captured_json_fields(cls, value):
        if type(value) is dict:
            if 'projection_partial' in value:
                raise ValueError('projection state is execution-owned')
            value=dict(value)
            for base in cls.__mro__:
                for key in base.__dict__.get('_captured_nonnull',()):
                    field=cls.model_fields[key]
                    wire_key=field.alias or key
                    if wire_key in value and value[wire_key] is None:
                        raise ValueError('null captured field')
            for key,field in cls.model_fields.items():
                if key.endswith(('_snapshot','_json')) and field.annotation is not str and type(value.get(key)) is str:
                    value[key]=json.loads(value[key])
                wire_key=field.alias or key
                if wire_key in value:
                    value[wire_key]=_captured_tuple(value[wire_key],field.annotation)
        return value

    @model_serializer(mode='wrap')
    def stored_presence(self, handler):
        values=handler(self)
        omitted=self._internal | (_PARTIAL_PROVENANCE if self.projection_partial else frozenset())
        return {key:value for key,value in values.items() if key not in omitted and (key=='tag' or key in self.model_fields_set or any(f.alias==key and name in self.model_fields_set for name,f in type(self).model_fields.items()))}


_PARTIAL_PROVENANCE=frozenset({'version','revision_number','created_at','created_by','created_via',
    'updated_at','updated_by','updated_via'})


def _captured_tuple(value, annotation):
    """JSON arrays for a declared tuple only; no scalar or map coercions."""
    if isinstance(annotation,type) and issubclass(annotation,BaseModel) and type(value) is dict:
        # Existing immutable owner codecs retain JSON-mode tuple validation even
        # when a parent snapshot was stored as a JSON string within the audit.
        return annotation.model_validate_json(json.dumps(value,allow_nan=False))
    if get_origin(annotation) is not tuple:
        for option in get_args(annotation):
            if get_origin(option) is tuple and type(value) in (tuple,list):
                return _captured_tuple(value,option)
    if get_origin(annotation) is tuple and type(value) in (tuple,list):
        args=get_args(annotation)
        if len(args)==2 and args[1] is Ellipsis:
            return tuple(_captured_tuple(item,args[0]) for item in value)
        if len(args)==len(value):
            return tuple(_captured_tuple(item,a) for item,a in zip(value,args))
    return value


class Reference(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('id','label','version'))
    id: str | None
    label: str | None
    version: int | None = Field(ge=1)


class Account(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('id','name','full_name','normal_balance', 'type'))
    id: str | None
    name: str | None
    full_name: str | None
    number: str | None
    type: str | None
    normal_balance: Literal['debit','credit'] | None


class DepositAccount(Account):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('active',))
    system_role: str | None
    active: bool | None
    currency: str


class Address(View):
    line1: str | None = None
    line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None


class Issuer(View):
    id: str
    display_name: str
    legal_name: str | None
    home_currency: str
    address_line1: str | None = None
    address_line2: str | None = None
    address_city: str | None = None
    address_state: str | None = None
    address_postal_code: str | None = None
    address_country: str | None = None
    legal_address_line1: str | None = None
    legal_address_line2: str | None = None
    legal_address_city: str | None = None
    legal_address_state: str | None = None
    legal_address_postal_code: str | None = None
    legal_address_country: str | None = None
    ship_address_line1: str | None = None
    ship_address_line2: str | None = None
    ship_address_city: str | None = None
    ship_address_state: str | None = None
    ship_address_postal_code: str | None = None
    ship_address_country: str | None = None


class CapturedIssuerWithoutDisplayName(Issuer):
    """Journal/register and payment captures omit the registry display-name key."""
    display_name: None = None

    @model_validator(mode='before')
    @classmethod
    def absent_name(cls,value):
        if type(value) is dict and 'display_name' in value:
            raise ValueError('this captured variant has no display-name field')
        return value


class CustomCapture(View):
    definition_id: str | None
    value_id: str | None
    name: str | None
    kind: Literal['text','number','date','bool','choice']
    value: str | bool
    canonical_text: str
    definition_version: int | None
    position: int
    choice_id: str | None = None
    choice_label: str | None = None


class CustomCaptures(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('values',))
    values: tuple[CustomCapture,...] | None

    @model_validator(mode='before')
    @classmethod
    def captured_map(cls, value):
        if type(value) is dict and 'values' not in value:
            if any(type(row) is not dict or row.get('definition_id') != key for key,row in value.items()):
                raise ValueError('foreign custom capture')
            return {'values': tuple(value[key] for key in sorted(value))}
        return value


class Origin(View):
    kind: Literal['explicit','default','legacy_implicit']
    source_id: str | None = None


class NamedOrigin(View):
    field: str
    origin: Origin


class Origins(View):
    values: tuple[NamedOrigin,...]

    @model_validator(mode='before')
    @classmethod
    def named(cls,value):
        if type(value) is dict and 'values' not in value:
            return {'values': tuple({'field':key,'origin':value[key]} for key in sorted(value))}
        return value


class Customer(Reference):
    company_name: str | None = None
    salutation: str | None = None
    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    resale_number: str | None = None


class TaxCode(Reference):
    taxable: bool


class TaxRule(Reference):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset({'agency'})
    rate_percent_millionths: int
    agency: Reference | None
    liability_account: Account


class Term(Reference):
    kind: Literal['standard','date_driven']
    due_days: int | None
    discount_days: int | None
    due_day_of_month: int | None
    due_next_month_if_within_days: int | None
    discount_day_of_month: int | None
    discount_percent_millionths: int | None


class Unit(Reference):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('set_id','abbreviation'))
    set_id: str | None
    abbreviation: str | None
    factor_nanounits: int


class PriceRule(Reference):
    kind: Literal['fixed_percent','per_item']
    currency: str
    rounding_mode: Literal['nearest','up','down']
    increment_minor_units: int
    offset_minor_units: int
    percent_millionths: int | None = None
    fixed_minor_units: int | None = None
    adjustment_basis: Literal['standard_price','cost','current_custom_price']='standard_price'
    matched: bool = True


class Preferences(View):
    sales_tax_enabled: bool
    sales_tax_liability_basis: Literal['invoice_date','payment_receipt']
    enable_price_levels: bool
    use_classes: bool
    prompt_for_class: bool
    units_of_measure_mode: Literal['disabled','single_unit_per_item','multiple_related_units']


class CommercialProfile(View):
    schema_version: Literal[1,2]=1
    sales_tax_calculation: Literal['line_component_half_even','line_combined_half_up','invoice_combined_half_up'] | None = None
    tax_policy_origin: Origin | None = None
    customer: Customer
    preferences: Preferences
    billing_address: Address | None = None
    shipping_address: Address | None = None
    shipping_address_id: str | None = None
    terms: Term | None = None
    ship_date: str | None = None
    ship_method: Reference | None = None
    sales_rep: Reference | None = None
    class_id: Reference | None = None
    customer_tax_code: TaxCode | None = None
    sales_tax_item: Reference | None = None
    tax_rules: tuple[TaxRule,...] | None = None
    price_level: Reference | None = None
    customer_message: str | None = None
    customer_message_item: Reference | None = None
    customer_purchase_order: str | None = None
    origins: Origins


class SalesCapture(CommercialProfile):
    control_account: Account
    due_date: str | None = None
    discount_date: str | None = None
    discount_available: bool = False
    payment_method: Reference | None = None
    payment_reference: str | None = None


class PaymentPreferences(View):
    automatically_apply_payments: bool
    automatically_calculate_payments: bool
    use_undeposited_funds_for_payments: bool


class BillingAddress(View):
    billing_address_source_id: str | None = None


class PaymentCapture(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset({'payer','lineage','billing_address','payment_method'})
    schema_version: Literal[1]=1
    payer: Reference | None
    lineage: tuple[Reference,...] | None
    billing_address: BillingAddress | None
    ar_account: Account
    deposit_account: Account
    payment_method: Reference | None
    preferences: PaymentPreferences


class ComponentCapture(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset({'party','lineage'})
    party: Reference | None
    ar_account: Account
    lineage: tuple[Reference,...] | None


class AllocationSpan(View):
    start: str
    end: str


class AllocationCapture(View):
    _internal: ClassVar[frozenset[str]]=frozenset({'source_basis_hash'})
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset({'source_basis_hash'})
    source_document_id: str
    source_revision_id: str
    source_line_id: str
    root_document_id: str
    root_line_id: str
    source_basis_hash: str | None
    quoted_quantity_microunits: int
    quoted_base_quantity_microunits: int
    quoted_net_minor_units: int
    denominator: str
    spans: tuple[AllocationSpan,...]
    basis_version: Literal[2] | None = None


class SalesLineCapture(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset({'item'})
    schema_version: Literal[1,2,3]=1
    item: Reference | None
    item_type: Literal['service','non_inventory_part','other_charge']
    income_account: Account
    unit: Unit | None = None
    class_id: Reference | None = None
    tax_code: TaxCode | None = None
    price_rule: PriceRule | None = None
    standard_price_minor_units: int | None = None
    cost_minor_units: int | None = None
    price_basis_minor_units: int | None = None
    origins: Origins
    pricing_basis: Literal['unit','amount','allocated']='unit'
    net_amount_minor_units: int | None = None
    allocation_proof: AllocationCapture | None = None


class SalesTaxCapture(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset({'tax_item','agency'})
    schema_version: Literal[1]=1
    position: int
    tax_item: Reference | None
    agency: Reference | None
    liability_account: Account


# Closed calculator capture, separate from financial computation: reference
# disclosure never changes the exact integer equations retained in these fields.
class CalculatedTaxReference(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('id', 'label', 'version'))
    id: str | None
    label: str | None
    version: int | None


class CalculatedTaxAccount(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('full_name', 'id', 'name', 'normal_balance', 'type'))
    id: str | None
    name: str | None
    full_name: str | None
    number: str | None
    type: str | None
    normal_balance: Literal['debit','credit'] | None


class CalculatedTaxRule(CalculatedTaxReference):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset({'agency'})
    rate_percent_millionths: int
    agency: CalculatedTaxReference | None
    liability_account: CalculatedTaxAccount


class CalculatedTaxCell(View):
    tax_ordinal: int
    rule: CalculatedTaxRule
    exact_numerator: int
    tax_minor_units: int


class CalculatedTaxBucket(View):
    tax_ordinals: tuple[int,...]
    net_minor_units: int
    exact_numerator: int
    tax_minor_units: int
    gross_minor_units: int
    cells: tuple[CalculatedTaxCell,...]


class CalculatedTaxLine(View):
    tax_ordinal: int
    net_minor_units: int
    tax_minor_units: int
    gross_minor_units: int


class CalculatedTaxLiability(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('agency_id', 'liability_account_id'))
    agency_id: str | None
    liability_account_id: str | None
    tax_minor_units: int


class CalculatedTaxAccountTotal(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('liability_account_id',))
    liability_account_id: str | None
    tax_minor_units: int


class TaxCalculation(View):
    policy: Literal['line_component_half_even','line_combined_half_up','invoice_combined_half_up']
    currency: str
    buckets: tuple[CalculatedTaxBucket,...]
    lines: tuple[CalculatedTaxLine,...]
    liabilities: tuple[CalculatedTaxLiability,...]
    accounts: tuple[CalculatedTaxAccountTotal,...]
    net_minor_units: int
    tax_minor_units: int
    gross_minor_units: int


class TaxCapture(View):
    schema_version: Literal[1]=1
    origin: Origin
    calculation: TaxCalculation


class WorkCapture(View):
    schema_version: Literal[1,2]=1
    profile: CommercialProfile
    issuer_snapshot: Issuer
    memo: str | None = None
    scope: str | None = None
    inclusions: str | None = None
    exclusions: str | None = None
    timing: str | None = None
    commercial_terms: str | None = None
    expires_on: str | None = None
    priority: Literal['low','normal','high','urgent']='normal'
    site_address: Address | None = None
    assignees: tuple[Reference,...]=()
    scheduled_start: str | None = None
    scheduled_end: str | None = None
    actual_start: str | None = None
    actual_end: str | None = None


class WorkTaxCapture(View):
    rule: TaxRule
    taxable_minor_units: int
    tax_minor_units: int


class WorkLineCapture(View):
    schema_version: Literal[1,2]=1
    item_id: str
    description: str | None
    quantity_microunits: int
    completed_quantity_microunits: int = 0
    unit_id: str | None
    unit_factor_nanounits: int
    base_quantity_microunits: int
    unit_price_minor_units: int | None
    net_minor_units: int
    tax_minor_units: int
    gross_minor_units: int
    estimated_unit_cost_minor_units: int | None
    estimated_cost_minor_units: int | None
    pricing_basis: Literal['catalog','manual','markup','amount']
    markup_percent_millionths: int | None = None
    billable: bool = True
    profile: SalesLineCapture
    estimated_cost_origin: Origin
    taxes: tuple[WorkTaxCapture,...]=()


class WorkAllocationCapture(View):
    document: WorkCapture
    line: WorkLineCapture
    title: str
    source_number: str


class SemanticKey(View):
    kind: Literal['payment','sale_net','sale_tax']
    identity: str
    tax_item: str=''


class Dimensions(View):
    party_kind: str | None
    party_id: str | None
    party_name: str | None
    class_id: str | None
    class_name: str | None


class CashComponent(View):
    key: SemanticKey
    capacity: int
    document_line_id: str
    posting_line_id: str
    posting_source_id: str
    physical_component_id: str | None
    cash: Dimensions
    credit_owner_party: str | None = None
    credit_owner_ar: str | None = None
    sale_line: SalesLineCapture | None = None
    tax: SalesTaxCapture | None = None


class CashSource(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('uf_account',))
    source_type: Literal['payment','sales_receipt']
    transaction_id: str
    expected_header_version: int
    revision_id: str
    business_batch_id: str
    receipt_date: str
    currency: str
    cash_minor_units: int
    uf_account: str | None
    source_memo: str | None
    source_reference: str | None
    profile: PaymentCapture | SalesCapture
    semantic_presence: tuple[SemanticKey,...]
    components: tuple[CashComponent,...]
    dependencies: tuple[str,...]


class ComponentOccurrence(View):
    key: SemanticKey
    ordinal: int
    present: bool


class SourceRow(View):
    row_id: str
    ordinal: int
    source: CashSource
    occurrences: tuple[ComponentOccurrence,...]
    memo: str | None
    memo_origin: Literal['source','entered']


class AdditionalCapture(View):
    row_id: str
    ordinal: int
    account: DepositAccount
    units: int
    dimensions: Dimensions
    memo: str | None = None
    check_number: str | None = None
    payment_method: Reference | None = None


class CashBackCapture(View):
    account: DepositAccount
    units: int
    memo: str | None = None


class DepositIntent(View):
    deposit_id: str
    date: str
    currency: str
    bank: DepositAccount
    sources: tuple[SourceRow,...]
    additional: tuple[AdditionalCapture,...]
    cash_back: CashBackCapture | None = None


class CashCell(View):
    row_id: str
    component_ordinal: int
    bucket: str
    units: int


class DepositLeg(View):
    key: str
    account_id: str
    signed_debit: int
    currency: str
    dimensions: Dimensions


class DepositFinancialCapture(View):
    schema_version: Literal[1]=1
    intent: DepositIntent
    posting_total: int
    subtotal: int
    bank_total: int
    cash_back: int
    cells: tuple[CashCell,...]
    legs: tuple[DepositLeg,...]
    inverse_of: str | None = None


class ApplicationAllocationView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via'))
    tag: Literal['application_allocation']='application_allocation'
    id: str
    application_id: str
    kind: str
    reverses_allocation_id: str | None
    source_transaction_id: str
    source_revision_id: str
    source_component_id: str
    source_posting_source_id: str
    target_transaction_id: str
    target_revision_id: str
    target_document_line_id: str
    target_line_id: str
    target_ordinal: int
    logical_kind: str
    tax_item_id: str | None
    tax_component_id: str | None
    target_ar_source_id: str
    target_recognition_source_id: str
    recognition_role: str
    amount_minor_units: int
    currency: str
    effective_date: str
    facts_snapshot: AllocationSemantic
    created_at: str | None
    created_by: str | None
    created_via: str | None
    audit_event_id: str


class ApplicationView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via'))
    tag: Literal['application']='application'
    id: str
    kind: str
    paying_transaction_id: str
    paid_transaction_id: str
    source_component_key_id: str
    amount_minor_units: int
    currency: str
    effective_date: str
    reverses_application_id: str | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    audit_event_id: str


class DocumentLineIdentityView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via'))
    tag: Literal['document_line_identity']='document_line_identity'
    id: str
    created_at: str | None
    created_by: str | None
    created_via: str | None
    transaction_id: str


class DocumentLineView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via'))
    tag: Literal['document_line']='document_line'
    id: str
    created_at: str | None
    created_by: str | None
    created_via: str | None
    transaction_id: str
    revision_id: str
    line_id: str
    position: int
    kind: str
    account_id: str | None
    side: str | None
    amount_minor_units: int | None
    currency: str
    account_snapshot: Account | None
    name_type: str | None
    name_id: str | None
    party_name: str | None
    class_id: str | None
    class_name: str | None
    description: str | None
    original_minor_units: int | None
    original_currency: str | None
    rate_used: str | None
    rate_source: str | None


class PaymentComponentKeyView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('ar_account_id', 'created_at', 'created_via', 'party_id'))
    tag: Literal['payment_component_key']='payment_component_key'
    id: str
    transaction_id: str
    line_id: str
    party_id: str | None
    ar_account_id: str | None
    currency: str
    created_at: str | None
    created_by: str | None
    created_via: str | None
    audit_event_id: str


class PaymentComponentView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via'))
    tag: Literal['payment_component']='payment_component'
    id: str
    transaction_id: str
    revision_id: str
    document_line_id: str
    component_key_id: str
    amount_minor_units: int
    currency: str
    component_snapshot: ComponentCapture
    created_at: str | None
    created_by: str | None
    created_via: str | None
    audit_event_id: str


class PaymentProfileView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('ar_account_id', 'created_at', 'created_via', 'deposit_account_id', 'payer_id', 'payment_method_id'))
    tag: Literal['payment_profile']='payment_profile'
    revision_id: str
    transaction_id: str
    type: str
    payer_id: str | None
    ar_account_id: str | None
    deposit_account_id: str | None
    payment_method_id: str | None
    reference: str | None
    profile_snapshot: PaymentCapture
    created_at: str | None
    created_by: str | None
    created_via: str | None
    audit_event_id: str


class PostingBatchView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via'))
    tag: Literal['posting_batch']='posting_batch'
    id: str
    created_at: str | None
    created_by: str | None
    created_via: str | None
    transaction_id: str
    revision_id: str
    kind: str
    effective_date: str
    reverses_batch_id: str | None
    replaces_batch_id: str | None
    audit_event_id: str


class PostingLineSourceView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via'))
    tag: Literal['posting_line_source']='posting_line_source'
    id: str
    created_at: str | None
    created_by: str | None
    created_via: str | None
    transaction_id: str
    posting_line_id: str
    revision_id: str
    document_line_id: str
    amount_minor_units: int
    currency: str
    reversed_source_id: str | None
    tax_component_id: str | None
    payment_component_id: str | None = None
    deposit_component_id: str | None = None


class PostingLineView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('account_id', 'account_snapshot', 'created_at', 'created_via'))
    tag: Literal['posting_line']='posting_line'
    id: str
    created_at: str | None
    created_by: str | None
    created_via: str | None
    transaction_id: str
    batch_id: str
    line_no: int
    account_id: str | None
    debit_minor_units: int
    credit_minor_units: int
    currency: str
    account_snapshot: Account | None
    name_type: str | None
    name_id: str | None
    party_name: str | None
    class_id: str | None
    class_name: str | None
    description: str | None
    original_minor_units: int | None
    original_currency: str | None
    rate_used: str | None
    rate_source: str | None
    reversed_line_id: str | None


class SalesLineProfileView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via'))
    tag: Literal['sales_line_profile']='sales_line_profile'
    document_line_id: str
    transaction_id: str
    revision_id: str
    created_at: str | None
    created_by: str | None
    created_via: str | None
    item_id: str
    quantity_microunits: int | None
    unit_id: str | None
    unit_factor_nanounits: int
    base_quantity_microunits: int | None
    unit_price_minor_units: int | None
    pricing_basis: str
    net_minor_units: int
    tax_minor_units: int
    gross_minor_units: int
    item_snapshot: SalesLineCapture


class SalesProfileView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('control_account_id', 'created_at', 'created_via', 'customer_id'))
    tag: Literal['sales_profile']='sales_profile'
    revision_id: str
    transaction_id: str
    created_at: str | None
    created_by: str | None
    created_via: str | None
    type: str
    customer_id: str | None
    control_account_id: str | None
    due_date: str | None
    subtotal_minor_units: int
    tax_minor_units: int
    profile_snapshot: SalesCapture


class SalesTaxAttributionLineView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via'))
    tag: Literal['sales_tax_attribution_line']='sales_tax_attribution_line'
    document_line_id: str
    transaction_id: str
    revision_id: str
    line_id: str
    tax_ordinal: int
    created_at: str | None
    created_by: str | None
    created_via: str | None


class SalesTaxAttributionView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via'))
    tag: Literal['sales_tax_attribution']='sales_tax_attribution'
    revision_id: str
    transaction_id: str
    created_at: str | None
    created_by: str | None
    created_via: str | None
    facts_snapshot: TaxCapture


class SalesTaxComponentView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('agency_id', 'created_at', 'created_via', 'liability_account_id', 'tax_item_id'))
    tag: Literal['sales_tax_component']='sales_tax_component'
    id: str
    transaction_id: str
    revision_id: str
    document_line_id: str
    created_at: str | None
    created_by: str | None
    created_via: str | None
    tax_item_id: str | None
    agency_id: str | None
    liability_account_id: str | None
    rate_percent_millionths: int
    taxable_minor_units: int
    tax_minor_units: int
    component_snapshot: SalesTaxCapture


class SalesTaxLineKeyView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via'))
    tag: Literal['sales_tax_line_key']='sales_tax_line_key'
    line_id: str
    transaction_id: str
    tax_ordinal: int
    created_at: str | None
    created_by: str | None
    created_via: str | None


class SettlementLineKeyView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via'))
    tag: Literal['settlement_line_key']='settlement_line_key'
    id: str
    transaction_id: str
    line_id: str
    ordinal: int
    created_at: str | None
    created_by: str | None
    created_via: str | None
    audit_event_id: str


class TransactionRevisionView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via', 'revision_number'))
    tag: Literal['transaction_revision']='transaction_revision'
    id: str
    created_at: str | None
    created_by: str | None
    created_via: str | None
    transaction_id: str
    revision_number: int | None
    supersedes_revision_id: str | None
    date: str
    number: str
    name_type: str | None
    name_id: str | None
    memo: str | None
    total_minor_units: int
    currency: str
    issuer_snapshot: Issuer | CapturedIssuerWithoutDisplayName
    custom_fields_snapshot: CustomCaptures
    audit_event_id: str


class TransactionView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via', 'updated_at', 'updated_via', 'version'))
    tag: Literal['transaction']='transaction'
    id: str
    version: int | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None
    type: str
    number: str
    current_revision_id: str
    status: str
    voided_at: str | None
    voided_by: str | None
    void_reason: str | None
    void_posting_batch_id: str | None


class WorkBillingAllocationView(View):
    _internal: ClassVar[frozenset[str]]=frozenset({'source_basis_hash'})
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via'))
    tag: Literal['work_billing_allocation']='work_billing_allocation'
    id: str
    transaction_id: str
    revision_id: str
    document_line_id: str
    source_document_id: str
    source_revision_id: str
    source_line_id: str
    root_document_id: str
    root_line_id: str
    quantity_microunits: int | None
    net_minor_units: int
    tax_minor_units: int
    gross_minor_units: int
    facts_snapshot: WorkAllocationCapture
    created_at: str | None
    created_by: str | None
    created_via: str | None
    allocation_version: int = 1
    source_basis_hash: str | None = None
    denominator_hex: str | None = None
    spans_json: tuple[tuple[str,str],...] | None = None


class AllocationSemantic(View):
    account_id: str
    net_minor_units: int
    tax: SalesTaxComponentView | None


@dataclass(frozen=True)
class ReferenceField:
    path: tuple[str | int,...]
    record_type: str
    record_id: str
    label_path: tuple[str | int,...] | None = None


class DepositProfileView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('bank_account_id', 'created_at', 'created_via'))
    tag: Literal['deposit_profile']='deposit_profile'
    revision_id: str
    transaction_id: str
    type: str
    bank_account_id: str | None
    posting_total: int
    subtotal: int
    bank_total: int
    cash_back: int
    facts_snapshot: DepositFinancialCapture
    created_at: str | None
    created_by: str | None
    created_via: str | None
    audit_event_id: str


class DepositRowKeyView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via'))
    tag: Literal['deposit_row_key']='deposit_row_key'
    id: str
    transaction_id: str
    line_id: str
    ordinal: int
    kind: str
    created_at: str | None
    created_by: str | None
    created_via: str | None
    audit_event_id: str


class DepositComponentKeyView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via', 'tax_item_id'))
    tag: Literal['deposit_component_key']='deposit_component_key'
    id: str
    transaction_id: str
    row_id: str
    ordinal: int
    kind: str
    semantic_identity: str
    tax_item_id: str | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    audit_event_id: str


class DepositComponentView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via'))
    tag: Literal['deposit_component']='deposit_component'
    id: str
    transaction_id: str
    revision_id: str
    document_line_id: str
    row_id: str
    component_ordinal: int
    role: str
    capacity: int
    currency: str
    facts_snapshot: CashComponent | AdditionalCapture | CashBackCapture
    source_transaction_id: str | None
    source_revision_id: str | None
    source_document_line_id: str | None
    source_posting_line_id: str | None
    source_attribution_id: str | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    audit_event_id: str


class DepositCashCellView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via'))
    tag: Literal['deposit_cash_cell']='deposit_cash_cell'
    id: str
    transaction_id: str
    revision_id: str
    component_id: str
    bucket_row_id: str
    bucket: str
    amount_minor_units: int
    currency: str
    created_at: str | None
    created_by: str | None
    created_via: str | None
    audit_event_id: str


class DepositMembershipView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via'))
    tag: Literal['deposit_membership']='deposit_membership'
    id: str
    kind: str
    transaction_id: str
    revision_id: str
    batch_id: str
    row_id: str
    source_transaction_id: str
    source_revision_id: str
    source_batch_id: str
    amount_minor_units: int
    currency: str
    source_date: str
    facts_snapshot: CashSource
    reverses_membership_id: str | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    audit_event_id: str


class BankEffectKeyView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via'))
    tag: Literal['bank_effect_key']='bank_effect_key'
    id: str
    transaction_id: str
    role: str
    row_id: str
    created_at: str | None
    created_by: str | None
    created_via: str | None
    audit_event_id: str


class BankEffectVersionView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('account_id', 'created_at', 'created_via', 'version'))
    tag: Literal['bank_effect_version']='bank_effect_version'
    id: str
    transaction_id: str
    revision_id: str
    key_id: str
    version: int | None
    batch_id: str
    number: str
    memo: str | None
    account_id: str | None
    effective_date: str
    active: bool
    signed_debit: int
    statement_amount: int
    currency: str
    created_at: str | None
    created_by: str | None
    created_via: str | None
    audit_event_id: str


class WorkDocumentView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via', 'updated_at', 'updated_via', 'version'))
    tag: Literal['work_document']='work_document'
    id: str
    version: int | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None
    kind: str
    number: str
    current_revision_id: str
    status: str
    active: bool
    estimate_group_id: str | None


class WorkRevisionView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via', 'customer_id', 'revision_number'))
    tag: Literal['work_revision']='work_revision'
    id: str
    document_id: str
    revision_number: int | None
    supersedes_revision_id: str | None
    date: str
    number: str
    title: str
    status: str
    active: bool
    customer_id: str | None
    currency: str
    net_minor_units: int
    tax_minor_units: int
    gross_minor_units: int
    accepted_revision_id: str | None
    accepted_at: str | None
    accepted_by: str | None
    decision_note: str | None
    facts_snapshot: WorkCapture
    custom_fields_snapshot: CustomCaptures
    audit_event_id: str
    created_at: str | None
    created_by: str | None
    created_via: str | None


class WorkLineView(View):
    tag: Literal['work_line']='work_line'
    id: str
    document_id: str
    root_document_id: str
    root_line_id: str
    source_line_id: str | None


class WorkRevisionLineView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via'))
    tag: Literal['work_revision_line']='work_revision_line'
    id: str
    document_id: str
    revision_id: str
    line_id: str
    position: int
    item_id: str
    unit_id: str | None
    quantity_microunits: int
    completed_quantity_microunits: int
    unit_factor_nanounits: int
    base_quantity_microunits: int
    unit_price_minor_units: int | None
    net_minor_units: int
    tax_minor_units: int
    gross_minor_units: int
    estimated_unit_cost_minor_units: int | None
    estimated_cost_minor_units: int | None
    pricing_basis: str
    markup_percent_millionths: int | None
    billable: bool
    facts_snapshot: WorkLineCapture
    created_at: str | None
    created_by: str | None
    created_via: str | None


class WorkLinkView(View):
    _internal: ClassVar[frozenset[str]]=frozenset({'conversion_key_hash','request_hash'})
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via'))
    tag: Literal['work_link']='work_link'
    id: str
    source_document_id: str
    source_revision_id: str
    destination_document_id: str
    destination_revision_id: str
    relation: str
    conversion_key_hash: str | None
    request_hash: str | None
    source_version: int
    created_at: str | None
    created_by: str | None
    created_via: str | None


class WorkTaxLineKeyView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via'))
    tag: Literal['work_tax_line_key']='work_tax_line_key'
    line_id: str
    document_id: str
    tax_ordinal: int
    created_at: str | None
    created_by: str | None
    created_via: str | None


class WorkTaxAttributionView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via'))
    tag: Literal['work_tax_attribution']='work_tax_attribution'
    revision_id: str
    document_id: str
    created_at: str | None
    created_by: str | None
    created_via: str | None
    facts_snapshot: TaxCapture


class WorkTaxAttributionLineView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via'))
    tag: Literal['work_tax_attribution_line']='work_tax_attribution_line'
    work_line_id: str
    document_id: str
    revision_id: str
    line_id: str
    tax_ordinal: int
    created_at: str | None
    created_by: str | None
    created_via: str | None


class WorkBillingConversionView(View):
    _internal: ClassVar[frozenset[str]]=frozenset({'conversion_key_hash','request_hash'})
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via','conversion_key_hash','request_hash'))
    tag: Literal['work_billing_conversion']='work_billing_conversion'
    id: str
    source_document_id: str
    source_revision_id: str
    source_version: int
    destination_transaction_id: str
    destination_revision_id: str
    destination_type: str
    relation: str
    conversion_key_hash: str | None
    request_hash: str | None
    created_at: str | None
    created_by: str | None
    created_via: str | None


_FINANCIAL_MODELS = (
    TransactionView, TransactionRevisionView, DocumentLineIdentityView,
    DocumentLineView, PostingBatchView, PostingLineView, PostingLineSourceView,
    SalesProfileView, SalesLineProfileView, SalesTaxComponentView, SalesTaxLineKeyView,
    SalesTaxAttributionView, SalesTaxAttributionLineView, PaymentProfileView,
    PaymentComponentKeyView, PaymentComponentView, ApplicationView, ApplicationAllocationView,
    SettlementLineKeyView, DepositProfileView, DepositRowKeyView, DepositComponentKeyView,
    DepositComponentView, DepositCashCellView, DepositMembershipView, BankEffectKeyView,
    BankEffectVersionView, WorkDocumentView, WorkRevisionView, WorkLineView,
    WorkRevisionLineView, WorkLinkView, WorkTaxLineKeyView, WorkTaxAttributionView,
    WorkTaxAttributionLineView, WorkBillingAllocationView, WorkBillingConversionView,
)

# Closed spellings in existing ordinary-deposit audit. Never alter the stored
# record_type, and never use string singularization as a runtime decoder rule.
DEPOSIT_ALIASES = {'document_line_identitie':'document_line_identity',
                   'posting_batche':'posting_batch'}


@dataclass(frozen=True)
class Producer:
    owner: str
    commands: tuple[str,...]
    kinds: tuple[str,...]
    actions: tuple[str,...]


_JOURNAL_KINDS = ('transaction','transaction_revision','document_line_identity','document_line',
                  'posting_batch','posting_line','posting_line_source')
_SALES_KINDS = (*_JOURNAL_KINDS,'sales_profile','sales_line_profile','sales_tax_component',
                'sales_tax_line_key','sales_tax_attribution','sales_tax_attribution_line')
_PAYMENT_KINDS = (*_JOURNAL_KINDS,'payment_profile','payment_component_key','payment_component',
                  'application','application_allocation','settlement_line_key')
_DEPOSIT_KINDS = (*_JOURNAL_KINDS,'deposit_profile','deposit_row_key','deposit_component_key',
                  'deposit_component','deposit_cash_cell','deposit_membership','bank_effect_key','bank_effect_version')
_WORK_KINDS = ('work_document','work_revision','work_line','work_revision_line','work_link',
               'work_tax_line_key','work_tax_attribution','work_tax_attribution_line')
_FINANCIAL_PRODUCERS = (
    Producer('company.journals.apply',('journal post','journal update','journal void'),_JOURNAL_KINDS,('create','update')),
    Producer('company.registers.apply',('register post','register update'),_JOURNAL_KINDS,('create','update')),
    Producer('company.sales.apply',('invoice post','invoice update','invoice void','sales-receipt post','sales-receipt update','sales-receipt void'),_SALES_KINDS+('work_billing_allocation',),('create','update')),
    Producer('company.payment_invoice_corrections.persist',('invoice update',),('settlement_line_key','application_allocation'),('create',)),
    Producer('company.payments.apply',('payment receive','payment apply','payment unapply','payment update','payment void'),_PAYMENT_KINDS,('create','update')),
    Producer('company.deposit_persistence.execute',('deposit post','deposit update','deposit void'),
        ('transaction','transaction_revision','document_line_identitie','document_line','posting_batche',
         'posting_line','posting_line_source','deposit_profile','deposit_row_key','deposit_component_key',
         'deposit_component','deposit_cash_cell','deposit_membership','bank_effect_key','bank_effect_version'),('create','update')),
    Producer('company.deposit_coordinate_persistence.build',('deposit coordinate',),tuple(sorted(set(_DEPOSIT_KINDS+_PAYMENT_KINDS+_SALES_KINDS+('work_billing_allocation',)))),('create','update')),
    Producer('company.work.apply',('proposal create','proposal update','proposal copy','proposal estimate','estimate create','estimate update','estimate copy','estimate work-order','work-order create','work-order update','work-order copy','work-order complete'),_WORK_KINDS,('create','update')),
    Producer('company.billing.apply',('estimate invoice','estimate sales-receipt','work-order invoice','work-order sales-receipt'),tuple(sorted(set(_WORK_KINDS+_SALES_KINDS+('work_billing_allocation','work_billing_conversion')))),('create','update')),
)


def _format():
    raise BookflowError('E_VALIDATION',details={'reason':'audit_format'})


def decode_company_snapshot(*, producer: str, record_type: str, action: str,
                            snapshot: Mapping[str,object]):
    if type(snapshot) is not dict:
        _format()
    if not any(producer in p.commands and record_type in p.kinds and action in p.actions for p in _FINANCIAL_PRODUCERS):
        _format()
    canonical=DEPOSIT_ALIASES.get(record_type,record_type)
    if canonical=='transaction_revision':
        issuer=snapshot.get('issuer_snapshot')
        if type(issuer) is str:
            try:issuer=json.loads(issuer)
            except ValueError:_format()
        if type(issuer) is dict and 'display_name' not in issuer and producer not in (
                'journal post','journal update','journal void','register post','register update',
                'payment receive','payment update','payment void','deposit coordinate'):
            _format()
    model=next((m for m in _FINANCIAL_MODELS if m.model_fields['tag'].default==canonical),None)
    if model is None:
        _format()
    try:
        return model.model_validate_json(json.dumps(snapshot,allow_nan=False))
    except (ValueError,TypeError):
        _format()


def no_entry_summary(*,producer):
    # Financial no-effect receipts still carry an owned operation entry. Other
    # current no-entry branches are added only with their explicit owner gate.
    return None


for _model in _FINANCIAL_MODELS:
    _model.model_rebuild()


class CapturedMoney(View):
    amount: str
    currency: str
    minor_units: int

    @model_validator(mode='after')
    def exact_capture(self):
        from bookflow.core.money import Money
        try:
            if not -(2**63)<=self.minor_units<2**63 or Money(self.minor_units,self.currency).amount!=self.amount:
                raise ValueError('inconsistent captured money')
        except BookflowError:
            raise ValueError('invalid captured currency') from None
        return self


class CapturedCustomScalar(View):
    tag: Literal['captured_scalar']='captured_scalar'
    definition_id: str
    value: str | bool


class AggregateView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('custom_values',))
    custom_values: tuple[CapturedCustomScalar,...] | None=()

    @model_validator(mode='before')
    @classmethod
    def retained_scalars(cls,value):
        if type(value) is not dict:return value
        from bookflow.core.ids import is_ulid
        result=dict(value);captured=[]
        for key in tuple(result):
            if key.startswith('custom_fields.'):
                identifier=key[len('custom_fields.'):]
                scalar=result.pop(key)
                if not is_ulid(identifier) or identifier.upper()!=identifier or type(scalar) not in (str,bool):
                    raise ValueError('invalid captured custom scalar')
                captured.append({'definition_id':identifier,'value':scalar})
        if captured:
            if 'custom_values' in result:raise ValueError('ambiguous custom values')
            result['custom_values']=tuple(sorted(captured,key=lambda item:item['definition_id']))
        return result


class CustomerAddressesView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('label_key',))
    _internal: ClassVar[frozenset[str]] = frozenset(('label_key',))
    id: str
    customer_id: str
    position: int
    active: bool
    label: str
    label_key: str | None
    is_default: bool
    address_line1: str | None
    address_line2: str | None
    address_city: str | None
    address_state: str | None
    address_postal_code: str | None
    address_country: str | None


class CustomerContactsView(View):
    id: str
    customer_id: str
    position: int
    active: bool
    role: str
    salutation: str | None = None
    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    job_title: str | None = None
    work_phone: str | None = None
    home_phone: str | None = None
    mobile_phone: str | None = None
    other_phone: str | None = None
    work_fax: str | None = None
    home_fax: str | None = None
    primary_email: str | None = None
    secondary_email: str | None = None
    website: str | None = None
    external_handle: str | None = None
    display_name: str | None
    points: tuple[CustomerContactPointsView,...]


class CustomerContactPointsView(View):
    id: str
    contact_id: str
    position: int
    active: bool
    kind: str
    custom_label: str | None = None
    value: str


class VendorContactsView(View):
    id: str
    vendor_id: str
    position: int
    active: bool
    role: str
    salutation: str | None = None
    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    job_title: str | None = None
    work_phone: str | None = None
    home_phone: str | None = None
    mobile_phone: str | None = None
    other_phone: str | None = None
    work_fax: str | None = None
    home_fax: str | None = None
    primary_email: str | None = None
    secondary_email: str | None = None
    website: str | None = None
    external_handle: str | None = None
    display_name: str | None
    points: tuple[VendorContactPointsView,...]


class VendorContactPointsView(View):
    id: str
    contact_id: str
    position: int
    active: bool
    kind: str
    custom_label: str | None = None
    value: str


class VendorExpenseAccountsView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('account_id',))
    id: str
    vendor_id: str
    position: int
    active: bool
    account_id: str | None


class CustomFieldScopesView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('definition_name_key',))
    _internal: ClassVar[frozenset[str]] = frozenset(('definition_name_key',))
    id: str
    definition_id: str
    position: int
    active: bool
    record_type: str
    definition_name: str
    definition_name_key: str | None
    definition_active: bool


class CustomFieldChoicesView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('value_key',))
    _internal: ClassVar[frozenset[str]] = frozenset(('value_key',))
    id: str
    definition_id: str
    position: int
    active: bool
    value: str
    value_key: str | None


class CounterpartylinkView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('customer_id', 'vendor_id'))
    link_id: str
    customer_id: str | None
    vendor_id: str | None
    active: bool


class UnitconversionauditView(View):
    id: str
    position: int
    active: bool
    name: str
    abbreviation: str
    is_base: bool
    base_factor: str


class ItemmemberauditView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('component_item_id',))
    id: str
    position: int
    active: bool
    component_item_id: str | None
    quantity: str
    unit_id: str | None


class ItemvendorauditView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('vendor_id',))
    id: str
    position: int
    active: bool
    vendor_id: str | None
    preferred_rank: int
    vendor_item_name: str | None
    purchase_cost: CapturedMoney | None
    minimum_quantity: str | None
    lead_time_days: int | None
    manufacturer_part_number: str | None
    availability_notes: str | None


class PricelevelitemauditView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('item_id',))
    id: str
    position: int
    active: bool
    item_id: str | None
    price: CapturedMoney | None
    percent: str | None
    adjustment_basis: Literal['standard_price','cost','current_custom_price']


class CollectionidentityView(View):
    id: str
    active: bool


class AccountListView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via', 'updated_at', 'updated_via', 'version'))
    tag: Literal['account']='account'
    _internal: ClassVar[frozenset[str]] = frozenset(('provider_profile_ref',))
    id: str
    version: int | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None
    active: bool
    seed_key: str | None
    full_name: str
    depth: int
    system_role: str | None
    name: str
    number: str | None
    type: str
    parent_id: str | None
    description: str | None
    currency: str
    tax_line: str | None
    institution_name: str | None
    institution_account_last4: str | None
    routing_number_last4: str | None
    provider_profile_ref: None
    next_check_number: str | None
    check_reorder_number: str | None
    order_printable_checks: bool | None
    default_class_id: str | None
    track_reimbursable_expenses: bool
    reimbursable_income_account_id: str | None
    note: str | None


class AccountStoredView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via', 'full_name_key', 'name_key', 'path', 'updated_at', 'updated_via', 'version'))
    tag: Literal['account']='account'
    _internal: ClassVar[frozenset[str]] = frozenset(('full_name_key', 'name_key', 'number_key', 'path', 'provider_profile_ref'))
    id: str
    version: int | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None
    active: bool
    seed_key: str | None
    name: str
    name_key: str | None
    parent_id: str | None
    full_name: str
    full_name_key: str | None
    depth: int
    path: str | None
    number: str | None
    number_key: str | None
    type: str
    description: str | None
    currency: str
    tax_line: str | None
    institution_name: str | None
    institution_account_last4: str | None
    routing_number_last4: str | None
    provider_profile_ref: str | None
    next_check_number: str | None
    check_reorder_number: str | None
    order_printable_checks: bool | None
    default_class_id: str | None
    track_reimbursable_expenses: bool
    reimbursable_income_account_id: str | None
    note: str | None
    system_role: str | None


class ClassListView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via', 'full_name_key', 'name_key', 'path', 'updated_at', 'updated_via', 'version'))
    tag: Literal['class']='class'
    _internal: ClassVar[frozenset[str]] = frozenset(('full_name_key', 'name_key', 'path'))
    id: str
    version: int | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None
    active: bool
    seed_key: str | None
    name: str
    name_key: str | None
    parent_id: str | None
    full_name: str
    full_name_key: str | None
    depth: int
    path: str | None


class CustomFieldListView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via', 'name_key', 'updated_at', 'updated_via', 'version'))
    tag: Literal['custom_field']='custom_field'
    _internal: ClassVar[frozenset[str]] = frozenset(('name_key',))
    id: str
    version: int | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None
    active: bool
    seed_key: str | None
    name: str
    name_key: str | None
    kind: str
    position: int
    required: bool
    default_canonical_text: str | None
    default: str | bool | None
    scopes: tuple[CustomFieldScopesView,...]
    scopes_identities: tuple[CollectionidentityView,...] = Field(alias='_scopes_identities')
    choices: tuple[CustomFieldChoicesView,...]
    choices_identities: tuple[CollectionidentityView,...] = Field(alias='_choices_identities')


class CustomerListView(AggregateView):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via', 'full_name_key', 'name_key', 'path', 'updated_at', 'updated_via', 'version'))
    tag: Literal['customer']='customer'
    _internal: ClassVar[frozenset[str]] = frozenset(('full_name_key', 'name_key', 'path'))
    id: str
    version: int | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None
    active: bool
    seed_key: str | None
    name: str
    name_key: str | None
    parent_id: str | None
    full_name: str
    full_name_key: str | None
    depth: int
    path: str | None
    company_name: str | None
    salutation: str | None
    first_name: str | None
    middle_name: str | None
    last_name: str | None
    job_title: str | None
    terms_id: str | None
    sales_tax_code_id: str | None
    sales_tax_item_id: str | None
    price_level_id: str | None
    customer_type_id: str | None
    sales_rep_id: str | None
    preferred_payment_method_id: str | None
    preferred_ship_method_id: str | None
    resale_number: str | None
    preferred_delivery_method: str | None
    account_number: str | None
    payment_brand: str | None
    payment_last4: str | None
    payment_expiry_month: int | None
    payment_expiry_year: int | None
    payment_billing_line1: str | None
    payment_billing_line2: str | None
    payment_billing_city: str | None
    payment_billing_state: str | None
    payment_billing_postal_code: str | None
    payment_billing_country: str | None
    notes: str | None
    default_class_id: str | None
    job_status: str
    job_type_id: str | None
    job_start: str | None
    job_projected_end: str | None
    job_end: str | None
    job_description: str | None
    job_sales_rep_id: str | None
    address_mode: str
    contact_mode: str
    billing_address: Address | None
    credit_limit: CapturedMoney | None
    counterparty_link: CounterpartylinkView | None
    contacts: tuple[CustomerContactsView,...]
    contacts_identities: tuple[CollectionidentityView,...] = Field(alias='_contacts_identities')
    contact_points: tuple[CustomerContactPointsView,...]
    contact_points_identities: tuple[CollectionidentityView,...] = Field(alias='_contact_points_identities')
    shipping_addresses: tuple[CustomerAddressesView,...]
    shipping_addresses_identities: tuple[CollectionidentityView,...] = Field(alias='_shipping_addresses_identities')


class CustomerMessageListView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via', 'name_key', 'updated_at', 'updated_via', 'version'))
    tag: Literal['customer_message']='customer_message'
    _internal: ClassVar[frozenset[str]] = frozenset(('name_key',))
    id: str
    version: int | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None
    active: bool
    seed_key: str | None
    name: str
    name_key: str | None
    text: str
    display_order: int


class CustomerTypeListView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via', 'full_name_key', 'name_key', 'path', 'updated_at', 'updated_via', 'version'))
    tag: Literal['customer_type']='customer_type'
    _internal: ClassVar[frozenset[str]] = frozenset(('full_name_key', 'name_key', 'path'))
    id: str
    version: int | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None
    active: bool
    seed_key: str | None
    name: str
    name_key: str | None
    parent_id: str | None
    full_name: str
    full_name_key: str | None
    depth: int
    path: str | None


class EmployeeListView(AggregateView):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via', 'name_key', 'updated_at', 'updated_via', 'version'))
    tag: Literal['employee']='employee'
    _internal: ClassVar[frozenset[str]] = frozenset(('name_key',))
    id: str
    version: int | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None
    active: bool
    seed_key: str | None
    name: str
    name_key: str | None
    salutation: str | None
    first_name: str | None
    middle_name: str | None
    last_name: str | None
    job_title: str | None
    print_name_on_check_as: str | None
    employment_type: str | None
    phone: str | None
    email: str | None
    hire_date: str | None
    release_date: str | None
    emergency_contact_name: str | None
    emergency_contact_relationship: str | None
    emergency_contact_phone: str | None
    emergency_contact_email: str | None
    default_class_id: str | None
    notes: str | None
    address: Address | None


class ItemListView(AggregateView):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via', 'full_name_key', 'name_key', 'path', 'updated_at', 'updated_via', 'version'))
    tag: Literal['item']='item'
    _internal: ClassVar[frozenset[str]] = frozenset(('full_name_key', 'name_key', 'path'))
    id: str
    version: int | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None
    active: bool
    seed_key: str | None
    name: str
    name_key: str | None
    parent_id: str | None
    full_name: str
    full_name_key: str | None
    depth: int
    path: str | None
    type: str
    category_id: str | None
    description: str | None
    purchase_description: str | None
    sales_enabled: bool
    purchase_enabled: bool
    income_account_id: str | None
    expense_account_id: str | None
    cogs_account_id: str | None
    asset_account_id: str | None
    deposit_account_id: str | None
    liability_account_id: str | None
    default_class_id: str | None
    sales_tax_code_id: str | None
    manufacturer_part_number: str | None
    barcode: str | None
    unit_of_measure_set_id: str | None
    notes: str | None
    preferred_vendor_id: str | None
    print_members: bool | None
    payment_method_id: str | None
    use_undeposited_funds: bool | None
    tax_agency_vendor_id: str | None
    asset_number: str | None
    purchase_date: str | None
    vendor_id: str | None
    location: str | None
    serial_number: str | None
    warranty_expiration: str | None
    disposal_status: str | None
    disposal_date: str | None
    accumulated_depreciation_account_id: str | None
    depreciation_expense_account_id: str | None
    gain_loss_account_id: str | None
    depreciation_method: str | None
    useful_life_months: int | None
    price: CapturedMoney | None
    cost: CapturedMoney | None
    discount_amount: CapturedMoney | None
    original_cost: CapturedMoney | None
    disposal_proceeds: CapturedMoney | None
    disposal_costs: CapturedMoney | None
    book_basis: CapturedMoney | None
    tax_basis: CapturedMoney | None
    charge_percent: str | None
    discount_percent: str | None
    tax_percent: str | None
    reorder_point_min: str | None
    reorder_point_max: str | None
    assembly_build_point: str | None
    members: tuple[ItemmemberauditView,...]
    members_identities: tuple[CollectionidentityView,...] = Field(alias='_members_identities')
    vendor_profiles: tuple[ItemvendorauditView,...]
    vendor_profiles_identities: tuple[CollectionidentityView,...] = Field(alias='_vendor_profiles_identities')


class ItemCategoryListView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via', 'full_name_key', 'name_key', 'path', 'updated_at', 'updated_via', 'version'))
    tag: Literal['item_category']='item_category'
    _internal: ClassVar[frozenset[str]] = frozenset(('full_name_key', 'name_key', 'path'))
    id: str
    version: int | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None
    active: bool
    seed_key: str | None
    name: str
    name_key: str | None
    parent_id: str | None
    full_name: str
    full_name_key: str | None
    depth: int
    path: str | None


class JobTypeListView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via', 'full_name_key', 'name_key', 'path', 'updated_at', 'updated_via', 'version'))
    tag: Literal['job_type']='job_type'
    _internal: ClassVar[frozenset[str]] = frozenset(('full_name_key', 'name_key', 'path'))
    id: str
    version: int | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None
    active: bool
    seed_key: str | None
    name: str
    name_key: str | None
    parent_id: str | None
    full_name: str
    full_name_key: str | None
    depth: int
    path: str | None


class OtherNameListView(AggregateView):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via', 'name_key', 'updated_at', 'updated_via', 'version'))
    tag: Literal['other_name']='other_name'
    _internal: ClassVar[frozenset[str]] = frozenset(('name_key',))
    id: str
    version: int | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None
    active: bool
    seed_key: str | None
    name: str
    name_key: str | None
    company_name: str | None
    salutation: str | None
    first_name: str | None
    middle_name: str | None
    last_name: str | None
    job_title: str | None
    phone: str | None
    email: str | None
    contact: str | None
    account_number: str | None
    default_class_id: str | None
    notes: str | None
    converted_to_type: str | None
    converted_to_id: str | None
    address: Address | None


class PaymentMethodListView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via', 'name_key', 'updated_at', 'updated_via', 'version'))
    tag: Literal['payment_method']='payment_method'
    _internal: ClassVar[frozenset[str]] = frozenset(('name_key',))
    id: str
    version: int | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None
    active: bool
    seed_key: str | None
    name: str
    name_key: str | None
    kind: str


class PriceLevelListView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via', 'name_key', 'updated_at', 'updated_via', 'version'))
    tag: Literal['price_level']='price_level'
    _internal: ClassVar[frozenset[str]] = frozenset(('name_key',))
    id: str
    version: int | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None
    active: bool
    seed_key: str | None
    name: str
    name_key: str | None
    kind: str
    currency: str | None
    rounding_mode: str
    percent: str | None
    rounding_increment: CapturedMoney
    rounding_offset: CapturedMoney
    items: tuple[PricelevelitemauditView,...]
    items_identities: tuple[CollectionidentityView,...] = Field(alias='_items_identities')


class SalesRepListView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via', 'initials_key', 'name_id', 'name_key', 'name_type', 'updated_at', 'updated_via', 'version'))
    tag: Literal['sales_rep']='sales_rep'
    _internal: ClassVar[frozenset[str]] = frozenset(('initials_key', 'name_key'))
    id: str
    version: int | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None
    active: bool
    seed_key: str | None
    name: str
    name_key: str | None
    initials: str
    initials_key: str | None
    name_type: str | None
    name_id: str | None


class SalesTaxCodeListView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('code_key', 'created_at', 'created_via', 'updated_at', 'updated_via', 'version'))
    tag: Literal['sales_tax_code']='sales_tax_code'
    _internal: ClassVar[frozenset[str]] = frozenset(('code_key',))
    id: str
    version: int | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None
    active: bool
    seed_key: str | None
    code: str
    code_key: str | None
    description: str | None
    taxable: bool


class ShipMethodListView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via', 'name_key', 'updated_at', 'updated_via', 'version'))
    tag: Literal['ship_method']='ship_method'
    _internal: ClassVar[frozenset[str]] = frozenset(('name_key',))
    id: str
    version: int | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None
    active: bool
    seed_key: str | None
    name: str
    name_key: str | None
    display_order: int


class TermListView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via', 'name_key', 'updated_at', 'updated_via', 'version'))
    tag: Literal['term']='term'
    _internal: ClassVar[frozenset[str]] = frozenset(('name_key',))
    id: str
    version: int | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None
    active: bool
    seed_key: str | None
    name: str
    name_key: str | None
    kind: str
    due_days: int | None
    discount_days: int | None
    due_day_of_month: int | None
    due_next_month_if_within_days: int | None
    discount_day_of_month: int | None
    discount_percent_millionths: int | None


class UnitOfMeasureListView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via', 'name_key', 'updated_at', 'updated_via', 'version'))
    tag: Literal['unit_of_measure']='unit_of_measure'
    _internal: ClassVar[frozenset[str]] = frozenset(('name_key',))
    id: str
    version: int | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None
    active: bool
    seed_key: str | None
    name: str
    name_key: str | None
    default_purchase_unit_id: str | None
    default_sales_unit_id: str | None
    default_shipping_unit_id: str | None
    units: tuple[UnitconversionauditView,...]
    units_identities: tuple[CollectionidentityView,...] = Field(alias='_units_identities')


class VendorListView(AggregateView):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via', 'name_key', 'updated_at', 'updated_via', 'version'))
    tag: Literal['vendor']='vendor'
    _internal: ClassVar[frozenset[str]] = frozenset(('name_key',))
    id: str
    version: int | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None
    active: bool
    seed_key: str | None
    name: str
    name_key: str | None
    company_name: str | None
    salutation: str | None
    first_name: str | None
    middle_name: str | None
    last_name: str | None
    job_title: str | None
    terms_id: str | None
    vendor_type_id: str | None
    default_class_id: str | None
    billing_rate_level_id: str | None
    account_number: str | None
    print_name_on_check_as: str | None
    eligible_1099: bool
    is_tax_agency: bool
    recall_last_transaction: bool | None
    notes: str | None
    address: Address | None
    credit_limit: CapturedMoney | None
    counterparty_link: CounterpartylinkView | None
    contacts: tuple[VendorContactsView,...]
    contacts_identities: tuple[CollectionidentityView,...] = Field(alias='_contacts_identities')
    contact_points: tuple[VendorContactPointsView,...]
    contact_points_identities: tuple[CollectionidentityView,...] = Field(alias='_contact_points_identities')
    expense_accounts: tuple[VendorExpenseAccountsView,...]
    expense_accounts_identities: tuple[CollectionidentityView,...] = Field(alias='_expense_accounts_identities')


class VendorTypeListView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via', 'full_name_key', 'name_key', 'path', 'updated_at', 'updated_via', 'version'))
    tag: Literal['vendor_type']='vendor_type'
    _internal: ClassVar[frozenset[str]] = frozenset(('full_name_key', 'name_key', 'path'))
    id: str
    version: int | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None
    active: bool
    seed_key: str | None
    name: str
    name_key: str | None
    parent_id: str | None
    full_name: str
    full_name_key: str | None
    depth: int
    path: str | None


class CustomerVendorLinkListView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via', 'customer_id', 'updated_at', 'updated_via', 'vendor_id', 'version'))
    tag: Literal['customer_vendor_link']='customer_vendor_link'
    id: str
    version: int | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None
    customer_id: str | None
    vendor_id: str | None
    active: bool


_LIST_MODELS = (AccountListView,AccountStoredView,ClassListView,CustomFieldListView,CustomerListView,CustomerMessageListView,CustomerTypeListView,EmployeeListView,ItemListView,ItemCategoryListView,JobTypeListView,OtherNameListView,PaymentMethodListView,PriceLevelListView,SalesRepListView,SalesTaxCodeListView,ShipMethodListView,TermListView,UnitOfMeasureListView,VendorListView,VendorTypeListView,CustomerVendorLinkListView,)
_LIST_CHILD_MODELS = (CustomerAddressesView,CustomerContactsView,CustomerContactPointsView,VendorContactsView,VendorContactPointsView,VendorExpenseAccountsView,CustomFieldScopesView,CustomFieldChoicesView,CounterpartylinkView,UnitconversionauditView,ItemmemberauditView,ItemvendorauditView,PricelevelitemauditView,CollectionidentityView,)
_LIST_NOUNS = {'account': 'account', 'class': 'class', 'custom_field': 'custom-field', 'customer': 'customer', 'customer_message': 'customer-message', 'customer_type': 'customer-type', 'employee': 'employee', 'item': 'item', 'item_category': 'item-category', 'job_type': 'job-type', 'other_name': 'other-name', 'payment_method': 'payment-method', 'price_level': 'price-level', 'sales_rep': 'sales-rep', 'sales_tax_code': 'sales-tax-code', 'ship_method': 'ship-method', 'term': 'term', 'unit_of_measure': 'unit-of-measure', 'vendor': 'vendor', 'vendor_type': 'vendor-type', 'customer_vendor_link': 'customer'}
_LIST_ACTIONS = {'account': ('activate', 'create', 'deactivate', 'update'), 'class': ('activate', 'create', 'deactivate', 'update'), 'custom_field': ('activate', 'create', 'deactivate', 'update'), 'customer': ('activate', 'create', 'deactivate', 'link', 'unlink', 'update'), 'customer_message': ('activate', 'create', 'deactivate', 'update'), 'customer_type': ('activate', 'create', 'deactivate', 'update'), 'employee': ('activate', 'create', 'deactivate', 'update'), 'item': ('activate', 'create', 'deactivate', 'update'), 'item_category': ('activate', 'create', 'deactivate', 'update'), 'job_type': ('activate', 'create', 'deactivate', 'update'), 'other_name': ('activate', 'convert', 'create', 'deactivate', 'update'), 'payment_method': ('activate', 'create', 'deactivate', 'update'), 'price_level': ('activate', 'create', 'deactivate', 'update'), 'sales_rep': ('activate', 'create', 'deactivate', 'update'), 'sales_tax_code': ('activate', 'create', 'deactivate', 'update'), 'ship_method': ('activate', 'create', 'deactivate', 'update'), 'term': ('activate', 'create', 'deactivate', 'update'), 'unit_of_measure': ('activate', 'create', 'deactivate', 'update'), 'vendor': ('activate', 'create', 'deactivate', 'link', 'unlink', 'update'), 'vendor_type': ('activate', 'create', 'deactivate', 'update'), 'customer_vendor_link': ('deactivate via undo', 'link', 'unlink', 'update via undo')}

for _model in (*_LIST_MODELS,*_LIST_CHILD_MODELS):
    _model.model_rebuild()


def _list_model(producer,kind,action,snapshot):
    if kind not in _LIST_NOUNS:
        return None
    noun=_LIST_NOUNS[kind]
    ordinary={noun+' '+verb for verb in ('create','update','activate','deactivate')}
    related={
        'customer':{'customer link-vendor','customer unlink-vendor','other-name convert'},
        'vendor':{'customer link-vendor','customer unlink-vendor','other-name convert'},
        'employee':{'other-name convert'},'other_name':{'other-name convert'},
        'customer_vendor_link':{'customer link-vendor','customer unlink-vendor'},
    }
    allowed=ordinary | related.get(kind,set()) | {'undo'}
    if kind=='account':allowed.add('chart apply')
    if kind in ('class','customer_type','vendor_type','job_type','item_category','term','payment_method',
                'sales_tax_code','sales_rep','ship_method','customer_message'):
        allowed.add('profile apply')
    if producer not in allowed or action not in {*_LIST_ACTIONS[kind],'convert'}:
        _format()
    candidates=[m for m in _LIST_MODELS if m.model_fields['tag'].default==kind]
    if kind=='account':
        return AccountStoredView if 'name_key' in snapshot else AccountListView
    if len(candidates)!=1:_format()
    return candidates[0]



class CompanyInfoView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via', 'updated_at', 'updated_via', 'version'))
    tag: Literal['company_info']='company_info'
    _internal: ClassVar[frozenset[str]] = frozenset({'tax_id'})
    id: str
    version: int | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None
    attachment_max_bytes: int
    legal_name: str
    display_name: str = None
    tax_id_kind: str
    tax_id: str | None
    entity_type: str
    income_tax_form: str
    industry: str | None
    contact_name: str | None
    address_line1: str | None
    address_line2: str | None
    address_city: str | None
    address_state: str | None
    address_postal_code: str | None
    address_country: str | None
    legal_address_line1: str | None
    legal_address_line2: str | None
    legal_address_city: str | None
    legal_address_state: str | None
    legal_address_postal_code: str | None
    legal_address_country: str | None
    ship_address_line1: str | None
    ship_address_line2: str | None
    ship_address_city: str | None
    ship_address_state: str | None
    ship_address_postal_code: str | None
    ship_address_country: str | None
    phone: str | None
    fax: str | None
    email: str | None
    website: str | None
    fiscal_year_start_month: int
    tax_year_start_month: int
    report_basis: str
    home_currency: str
    timezone: str
    closing_date: str | None
    recent_activity_window_seconds: int
    default_chart: str | None
    default_chart_version: int | None
    use_account_numbers: bool
    show_lowest_subaccount_only: bool
    required_employee_profile_fields: tuple[tuple[str,...],...]
    use_classes: bool
    prompt_for_class: bool
    enable_price_levels: bool
    units_of_measure_mode: str
    sales_tax_calculation: str
    sales_tax_enabled: bool
    default_sales_tax_item_id: str | None
    sales_tax_liability_basis: str
    sales_tax_remittance_frequency: str
    default_ship_method_id: str | None
    free_on_board: str | None
    order_printable_checks: bool
    estimates_enabled: bool
    progress_billing_enabled: bool
    close_estimates_after_billing: bool
    automatically_apply_payments: bool
    automatically_calculate_payments: bool
    use_undeposited_funds_for_payments: bool

class CompanyRolloutView(CompanyInfoView):
    # rollout.create writes the submitted row before SQL server defaults are
    # materialized. Absence remains absent; do not invent a historical limit.
    attachment_max_bytes: None = None

    @model_validator(mode='before')
    @classmethod
    def submitted_fields(cls,value):
        if type(value) is dict and 'attachment_max_bytes' in value:
            raise ValueError('rollout variant omits server default')
        return value


class NoteView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via', 'updated_at', 'updated_via', 'version'))
    tag: Literal['note']='note'
    id: str
    version: int | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None
    record_type: str
    record_id: str
    body: str
    author_id: str | None
    interface: str
    at: str
    edited_at: str | None
    kind: str

class AttachmentView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via', 'updated_at', 'updated_via', 'version'))
    tag: Literal['attachment']='attachment'
    id: str
    version: int | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None
    sha256: str
    size_bytes: int
    media_type: str
    original_filename: str
    uploaded_by: str | None
    uploaded_at: str
    collected_at: str | None

class AttachmentLinkView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via', 'updated_at', 'updated_via', 'version'))
    tag: Literal['attachment_link']='attachment_link'
    id: str
    version: int | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None
    attachment_id: str
    record_type: str
    record_id: str
    linked_by: str | None
    linked_at: str
    caption: str
    active: bool

class DirectiveView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_via', 'updated_at', 'updated_via', 'version'))
    tag: Literal['directive']='directive'
    id: str
    version: int | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None
    code: str
    text: str
    given_by: str | None
    recorded_by: str | None
    active: bool
    deactivated_at: str | None
    deactivated_by: str | None

class ExchangeRateView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('version',))
    tag: Literal['exchange_rate']='exchange_rate'
    id: str
    version: int | None
    date: str
    from_currency: str
    to_currency: str
    rate: str
    source: str
    entered_by: str | None
    entered_at: str

_financial_decode = decode_company_snapshot


class CustomFieldValueView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('canonical_text', 'def_id'))
    tag: Literal['custom_field_value']='custom_field_value'
    id: str
    def_id: str | None
    record_type: str
    record_id: str
    active: bool
    canonical_text: str | None


class CompanyMigrationView(View):
    tag: Literal['company_migration']='company_migration'
    schema_revision: str
    from_revision: str | None = Field(alias='from')
    sales_tax_calculation: Literal['line_component_half_even','line_combined_half_up','invoice_combined_half_up'] | None = None


class CollectionScan(View):
    directory: int
    cookie: int


class AttachmentCollectionView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('scan_cursor', 'warnings'))
    tag: Literal['attachment_collection']='attachment_collection'
    _internal: ClassVar[frozenset[str]]=frozenset({'scan_cursor','warnings'})
    operation_id: str
    collected_count: int
    bytes_collected: int
    has_more: bool
    dry_run: Literal[False]
    warnings: tuple[str,...] | None
    idempotent_replay: bool
    scan_cursor: CollectionScan | None

_ANNOTATION_MODELS = {
    'company_info': (CompanyInfoView, ('company new','company update','company rename','chart apply','init','upgrade')),
    'note': (NoteView, ('note add','note edit')),
    'attachment': (AttachmentView, ('attachment add','company compact')),
    'attachment_link': (AttachmentLinkView, ('attachment add','attachment link','attachment unlink')),
    'directive': (DirectiveView, ('directive add','directive deactivate')),
    'exchange_rate': (ExchangeRateView, ('rate set',)),
    'attachment_collection': (AttachmentCollectionView, ('company compact',)),
}


def decode_company_snapshot(*, producer: str, record_type: str, action: str,
                            snapshot: Mapping[str,object]):
    if type(snapshot) is not dict:_format()
    model=_list_model(producer,record_type,action,snapshot)
    if record_type=='payment_operation':
        if producer not in (*_PAYMENT_OPERATION_COMMANDS,'invoice update') or action!='create' or snapshot.get('command')!=producer:_format()
        model=InvoiceOperationView if producer=='invoice update' else PaymentOperationView
    if record_type=='payment_operation_item':
        if producer not in _OPERATION_COMMANDS or action!='create':_format()
        model=_OPERATION_ITEM_MODELS.get(snapshot.get('kind'))
        if model is None:_format()
        if producer=='invoice update' and snapshot.get('kind')=='document_changes':
            model=InvoiceDocumentChangeItemView
    if record_type in _SELECTION_MODELS:
        if producer not in _SELECTION_COMMANDS or action not in ('create','update'):
            _format()
        model=_SELECTION_MODELS[record_type]
    if record_type=='company_info' and producer=='upgrade' and action=='migrate':
        model=CompanyMigrationView
    if model is None and record_type in _ANNOTATION_MODELS:
        model,commands=_ANNOTATION_MODELS[record_type]
        if producer not in commands or action not in ('create','update','deactivate','baseline'):
            _format()
        if record_type=='company_info' and producer=='company new' and 'attachment_max_bytes' not in snapshot:
            model=CompanyRolloutView
        # Rollout stores the declared JSON column; ordinary updates and chart
        # application store its decoded logical value. Both are owned formats.
        if record_type=='company_info' and type(snapshot.get('required_employee_profile_fields')) is str:
            try:
                snapshot=dict(snapshot,required_employee_profile_fields=json.loads(snapshot['required_employee_profile_fields']))
            except (ValueError,TypeError):_format()
    if model is None and record_type=='custom_field_value':
        if action not in ('create','update') or not any(producer in p.commands for p in _FINANCIAL_PRODUCERS):
            _format()
        model=CustomFieldValueView
    if model is None:
        return _financial_decode(producer=producer,record_type=record_type,action=action,snapshot=snapshot)
    try:
        return model.model_validate_json(json.dumps(snapshot,allow_nan=False))
    except (ValueError,TypeError):_format()


def entry_requirement(kind):
    if kind in ('principal','audit_event','audit_entry'):return ()
    if kind=='customer_vendor_link':return (('customer','member'),('vendor','member'))
    if kind in ('journal_entry','invoice','sales_receipt','payment','deposit'):
        return (('ledger.read','member'),)
    if kind in ('proposal','estimate','work_order'):
        return (('customer-work','member'),)
    child_owner={'customer_address':'customer','customer_contact':'customer','customer_contact_point':'customer',
        'vendor_contact':'vendor','vendor_contact_point':'vendor','vendor_expense_account':'vendor',
        'item_member':'item','item_vendor_profile':'item','price_level_item':'price-level',
        'unit_conversion':'unit-of-measure','custom_field_scope':'custom-field','custom_field_choice':'custom-field'}
    if kind in child_owner:return ((child_owner[kind],'member'),)
    if kind=='custom_field_value':return (('custom-field','member'),)
    if kind in _ANNOTATION_MODELS:
        return (({'company_info':'company','note':'note','attachment':'attachment',
                  'attachment_link':'attachment','attachment_collection':'attachment',
                  'directive':'directive','exchange_rate':'rate'}[kind],
                 'admin' if kind=='attachment_collection' else 'member'),)
    if kind in _LIST_NOUNS:
        return ((_LIST_NOUNS[kind],'member'),)
    if kind in {m.model_fields['tag'].default for m in _FINANCIAL_MODELS} or kind in DEPOSIT_ALIASES:
        return (('customer-work' if kind.startswith('work_') else 'ledger.read','member'),)
    if kind in ('payment_operation','payment_operation_item','payment_selection',
                'payment_selection_revision','payment_selection_item','payment_selection_recovery',
                'payment_selection_recovery_chunk','payment_selection_recovery_item',
                'payment_selection_recovery_active','deposit_operation','deposit_operation_item'):
        return (('ledger.read','member'),)
    _format()


# Closed reference groups. Names are actual captured fields, never an *_id scan.
# A denied reference nulls its group while preserving unrelated exact amounts.
_REFERENCE_GROUPS = {
    Reference: (),
    Customer: ((('id','label','version','company_name','salutation','first_name','middle_name','last_name','email','phone','resale_number'),'customer'),),
    Account: ((('id','name','full_name','number','type','normal_balance'),'account'),),
    DepositAccount: ((('id','name','full_name','number','type','normal_balance','system_role','active'),'account'),),
    TaxCode: ((('id','label','version'),'sales_tax_code'),),
    TaxRule: ((('id','label','version'),'item'),),
    Term: ((('id','label','version'),'term'),),
    Unit: ((('id','label','version','set_id','abbreviation'),'unit_of_measure'),),
    PriceRule: ((('id','label','version'),'price_level'),),
    CalculatedTaxReference: (),
    CalculatedTaxAccount: ((('id','name','full_name','number','type','normal_balance'),'account'),),
    CalculatedTaxRule: ((('id','label','version'),'item'),),
    CalculatedTaxLiability: ((('agency_id',),'vendor'),(('liability_account_id',),'account')),
    CalculatedTaxAccountTotal: ((('liability_account_id',),'account'),),
    AccountListView: ((('parent_id',),'account'),(('default_class_id',),'class'),(('reimbursable_income_account_id',),'account')),
    AccountStoredView: ((('parent_id',),'account'),(('default_class_id',),'class'),(('reimbursable_income_account_id',),'account')),
    ClassListView: ((('parent_id',),'class'),),
    CustomerTypeListView: ((('parent_id',),'customer_type'),),
    VendorTypeListView: ((('parent_id',),'vendor_type'),),
    JobTypeListView: ((('parent_id',),'job_type'),),
    ItemCategoryListView: ((('parent_id',),'item_category'),),
    CustomerListView: ((('parent_id',),'customer'),(('customer_type_id',),'customer_type'),(('job_type_id',),'job_type'),
        (('sales_rep_id','job_sales_rep_id'),'sales_rep'),(('price_level_id',),'price_level'),(('sales_tax_code_id',),'sales_tax_code'),
        (('sales_tax_item_id',),'item'),(('preferred_payment_method_id',),'payment_method'),(('terms_id',),'term'),
        (('preferred_ship_method_id',),'ship_method'),(('default_class_id',),'class')),
    VendorListView: ((('vendor_type_id',),'vendor_type'),(('terms_id',),'term'),(('default_class_id',),'class')),
    EmployeeListView: ((('default_class_id',),'class'),),
    OtherNameListView: ((('default_class_id',),'class'),),
    ItemListView: ((('parent_id',),'item'),(('category_id',),'item_category'),(('sales_tax_code_id',),'sales_tax_code'),
        (('preferred_vendor_id','tax_agency_vendor_id','vendor_id'),'vendor'),(('payment_method_id',),'payment_method'),
        (('unit_of_measure_set_id',),'unit_of_measure'),
        (('default_class_id',),'class'),(('income_account_id','expense_account_id','cogs_account_id','asset_account_id','deposit_account_id','liability_account_id','accumulated_depreciation_account_id','depreciation_expense_account_id','gain_loss_account_id'),'account')),
    UnitOfMeasureListView: ((('default_purchase_unit_id','default_sales_unit_id','default_shipping_unit_id'),'unit_of_measure'),),
    VendorExpenseAccountsView: ((('account_id',),'account'),),
    ItemmemberauditView: ((('component_item_id',),'item'),(('unit_id',),'unit_of_measure')),
    ItemvendorauditView: ((('vendor_id',),'vendor'),),
    PricelevelitemauditView: ((('item_id',),'item'),),
    CounterpartylinkView: ((('vendor_id',),'vendor'),(('customer_id',),'customer')),
    CustomerVendorLinkListView: ((('vendor_id',),'vendor'),(('customer_id',),'customer')),
    CustomFieldValueView: ((('def_id','canonical_text'),'custom_field'),),
    CompanyInfoView: ((('default_sales_tax_item_id',),'item'),(('default_ship_method_id',),'ship_method')),
}

_OBJECT_REFERENCE_KINDS = {
    (TaxRule,'agency'):'vendor', (CalculatedTaxRule,'agency'):'vendor',
    (CommercialProfile,'ship_method'):'ship_method', (CommercialProfile,'sales_rep'):'sales_rep',
    (CommercialProfile,'class_id'):'class', (CommercialProfile,'sales_tax_item'):'item',
    (CommercialProfile,'price_level'):'price_level', (CommercialProfile,'customer_message_item'):'customer_message',
    (SalesCapture,'payment_method'):'payment_method',
    (PaymentCapture,'payer'):'customer', (PaymentCapture,'lineage'):'customer',
    (PaymentCapture,'payment_method'):'payment_method',
    (ComponentCapture,'party'):'customer', (ComponentCapture,'lineage'):'customer',
    (SalesLineCapture,'item'):'item', (SalesLineCapture,'class_id'):'class',
}

# These optional whole captures reveal another owner's existence even when the
# nested reference IDs are removed. Admission is independent of value presence.
_OBJECT_FIELD_REQUIREMENTS = {
    (CustomerListView,'counterparty_link'):('customer','vendor'),
    (VendorListView,'counterparty_link'):('customer','vendor'),
    (CommercialProfile,'billing_address'):('customer',),
    (PaymentCapture,'billing_address'):('customer',),
    (CommercialProfile,'shipping_address'):('customer',),
    (CommercialProfile,'shipping_address_id'):('customer',),
}


_REFERENCE_GROUPS.update({
    DocumentLineView: ((('account_id', 'account_snapshot'), 'account'), (('class_id', 'class_name'), 'class')),
    PostingLineView: ((('account_id', 'account_snapshot'), 'account'), (('class_id', 'class_name'), 'class')),
    SalesProfileView: ((('control_account_id',), 'account'), (('customer_id',), 'customer')),
    SalesTaxComponentView: ((('liability_account_id',), 'account'), (('tax_item_id',), 'item'), (('agency_id',), 'vendor')),
    PaymentProfileView: ((('ar_account_id',), 'account'), (('deposit_account_id',), 'account'), (('payer_id',), 'customer'), (('payment_method_id',), 'payment_method')),
    PaymentComponentKeyView: ((('ar_account_id',), 'account'), (('party_id',), 'customer')),
    ApplicationAllocationView: ((('tax_item_id',), 'item'),),
    DepositProfileView: ((('bank_account_id',), 'account'),),
    DepositComponentKeyView: ((('tax_item_id',), 'item'),),
    BankEffectVersionView: ((('account_id',), 'account'),),
    WorkRevisionView: ((('customer_id',), 'customer'),),
})

_POLYMORPHIC_PARTIES = {
    TransactionRevisionView: ('name_type', 'name_id'),
    DocumentLineView: ('name_type', 'name_id', 'party_name'),
    PostingLineView: ('name_type', 'name_id', 'party_name'),
    Dimensions: ('party_kind','party_id','party_name'),
    SalesRepListView: ('name_type','name_id'),
    OtherNameListView: ('converted_to_type','converted_to_id'),
}

_REFERENCE_GROUPS.update({
    Dimensions: ((('class_id','class_name'),'class'),),
    CashSource: ((('uf_account',),'account'),),
    CashComponent: ((('credit_owner_party',),'customer'),(('credit_owner_ar',),'account')),
})
_OBJECT_REFERENCE_KINDS.update({
    (SalesTaxCapture,'tax_item'):'item',(SalesTaxCapture,'agency'):'vendor',
    (AdditionalCapture,'payment_method'):'payment_method',
})


class SelectionContextView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset({'customer_id','ar_account_id'})
    mode: Literal['new_receipt','existing_credit']
    customer_id: str | None
    ar_account_id: str | None
    payment_id: str | None
    date: str
    currency: str
    label: str | None
    automatically_calculate: bool
    funding_version: int | None = None
    funding_date: str | None = None
    funding_capacities: dict[str,int] | None = None
    funding_owners: dict[str,str] | None = None

    @model_validator(mode='after')
    def capacities(self):
        if self.funding_capacities is not None and any(v<0 for v in self.funding_capacities.values()):
            raise ValueError('negative captured funding')
        return self


class PaymentSelectionView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('version', 'created_at', 'created_by', 'created_via', 'updated_at', 'updated_by', 'updated_via'))
    tag: Literal['payment_selection']='payment_selection'
    id: str
    version: int | None = Field(ge=1)
    state: Literal['open','consumed']
    current_revision_id: str
    consumed_operation_id: str | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None

    @model_validator(mode='after')
    def state_owner(self):
        if (self.state=='open')!=(self.consumed_operation_id is None):
            raise ValueError('invalid consumption state')
        return self


class PaymentSelectionRevisionView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('version', 'created_at', 'created_by', 'created_via', 'manifest_hash'))
    tag: Literal['payment_selection_revision']='payment_selection_revision'
    _internal: ClassVar[frozenset[str]]=frozenset({'manifest_hash'})
    id: str
    selection_id: str
    version: int | None = Field(ge=1)
    context_snapshot: SelectionContextView
    amount_minor_units: int | None = Field(ge=0)
    amount_origin: Literal['entered','selection_total','unresolved']
    currency: str
    manifest_hash: str | None
    item_count: int = Field(ge=0)
    created_at: str | None
    created_by: str | None
    created_via: str | None
    audit_event_id: str

    @model_validator(mode='after')
    def amount(self):
        if self.amount_origin=='unresolved' and self.amount_minor_units is not None:
            raise ValueError('unresolved captured amount')
        if self.amount_origin=='entered' and self.amount_minor_units is None:
            raise ValueError('missing entered amount')
        return self


class PaymentSelectionItemView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at', 'created_by', 'created_via'))
    tag: Literal['payment_selection_item']='payment_selection_item'
    id: str
    selection_id: str
    revision_id: str
    kind: Literal['set','remove','clear']
    invoice_id: str | None
    ordinal: int | None = Field(ge=1)
    expected_version: int | None = Field(ge=1)
    due_minor_units: int | None = Field(ge=0)
    amount_minor_units: int | None = Field(ge=0)
    amount_origin: Literal['entered','calculated','unresolved'] | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    audit_event_id: str

    @model_validator(mode='after')
    def shape(self):
        values=(self.ordinal,self.expected_version,self.due_minor_units,self.amount_minor_units,self.amount_origin)
        if self.kind!='set':
            if any(v is not None for v in values) or (self.kind=='clear')!=(self.invoice_id is None):
                raise ValueError('invalid removal event')
        elif self.invoice_id is None or any(v is None for v in values[:3]) or self.amount_origin is None or ((self.amount_origin=='unresolved')!=(self.amount_minor_units is None)):
            raise ValueError('invalid set event')
        return self


_SELECTION_MODELS={m.model_fields['tag'].default:m for m in (
    PaymentSelectionView,PaymentSelectionRevisionView,PaymentSelectionItemView)}
_SELECTION_COMMANDS=('payment selection create','payment selection update','payment selection clear',
    'payment receive','payment apply','payment unapply','payment update','payment void',
    'payment recovery apply','payment recovery replace')
_REFERENCE_GROUPS.update({SelectionContextView: (
    (('customer_id','funding_capacities','funding_owners'),'customer'),
    (('ar_account_id',),'account'),)})


class RequestApplicationView(View):
    invoice: str
    expected_version: int = Field(ge=1)
    amount: CapturedMoney


class SourceComponentResultView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('party_id','party_name','ar_account_id'))
    component_key_id: str | None
    component_id: str | None
    party_id: str | None
    party_name: str | None
    ar_account_id: str | None
    currency: str
    received_minor_units: int
    applied_minor_units: int
    available_minor_units: int


class ApplicationResultView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('party_id',))
    kind: Literal['apply','unapply']='apply'
    reverses_application_id: str | None = None
    application_id: str | None
    invoice_id: str
    invoice_version: int
    source_component_key_id: str | None
    party_id: str | None
    amount: CapturedMoney
    effective_date: str


class AllocationResultView(View):
    kind: Literal['allocation','reversal']='allocation'
    reverses_allocation_id: str | None = None
    allocation_id: str | None
    application_id: str | None
    invoice_id: str
    target_ordinal: int
    logical_kind: Literal['net','tax']
    tax_item_id: str | None
    amount: CapturedMoney


class InvoiceAmountsResultView(View):
    invoice_id: str
    version: int
    revision_id: str | None
    gross_minor_units: int
    applied_minor_units: int
    due_minor_units: int
    currency: str
    status: Literal['unpaid','partial','paid','voided','not_effective']


class InvoiceSettlementResultView(InvoiceAmountsResultView):
    _internal: ClassVar[frozenset[str]]=frozenset({'settlement_guard','audit_watermark'})
    settlement_guard: str | None = None
    as_of: str | None = None
    audit_watermark: int | None = None
    all_committed_current: InvoiceAmountsResultView | None = None


class OperationItemView(View):
    tag: Literal['payment_operation_item']='payment_operation_item'
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(('created_at','created_by','created_via'))
    id: str
    operation_id: str
    ordinal: int = Field(ge=1)
    created_at: str | None
    created_by: str | None
    created_via: str | None
    audit_event_id: str


class RequestApplicationItemView(OperationItemView):
    kind: Literal['request_applications']
    item_snapshot: RequestApplicationView


class SourceComponentItemView(OperationItemView):
    kind: Literal['source_components']
    item_snapshot: SourceComponentResultView


class ApplicationItemView(OperationItemView):
    kind: Literal['effect_applications']
    item_snapshot: ApplicationResultView


class AllocationItemView(OperationItemView):
    kind: Literal['allocations']
    item_snapshot: AllocationResultView


class DocumentChangeItemView(OperationItemView):
    kind: Literal['document_changes']
    item_snapshot: InvoiceSettlementResultView


_OPERATION_ITEM_MODELS={
    'request_applications':RequestApplicationItemView,
    'source_components':SourceComponentItemView,
    'effect_applications':ApplicationItemView,
    'allocations':AllocationItemView,
    'document_changes':DocumentChangeItemView,
}
_OPERATION_COMMANDS=('payment receive','payment apply','payment unapply','payment update','payment void','invoice update')
_REFERENCE_GROUPS.update({
    SourceComponentResultView: ((('party_id','party_name'),'customer'),(('ar_account_id',),'account')),
    ApplicationResultView: ((('party_id',),'customer'),),
    AllocationResultView: ((('tax_item_id',),'item'),),
})


# Original input differs from public write input: the operation key is stored on
# the receipt, and omission is part of intent. These models retain that shape.
from bookflow.company.sales_models import SalesMoneyInput


class IntentInvoiceAmount(View):
    invoice: str
    expected_version: int = Field(ge=1)
    amount: str | SalesMoneyInput


class IntentInlineApplications(View):
    mode: Literal['inline']='inline'
    items: tuple[IntentInvoiceAmount,...]=()


class IntentSelectionReference(View):
    mode: Literal['selection']
    selection: str
    expected_version: int = Field(ge=1)


IntentApplications=Annotated[IntentInlineApplications|IntentSelectionReference,Field(discriminator='mode')]


class PaymentIntentBase(View):
    _internal: ClassVar[frozenset[str]]=frozenset({'expected_facts_fingerprint'})
    expected_facts_fingerprint: str | None=None


class ReceiveIntent(PaymentIntentBase):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset({'customer'})
    customer: str | None
    date: str
    amount: str | SalesMoneyInput
    applications: IntentApplications=Field(default_factory=IntentInlineApplications)
    payment_method: str | None=None
    ar_account: str | None=None
    deposit_to: str | None=None
    number: str | None=None
    reference: str | None=None
    memo: str | None=None
    custom_fields: dict[str,str|bool|int|None] | None=None
    expected_custom_field_kinds: dict[str,Literal['text','number','date','bool','choice']] | None=None


class ApplyIntent(PaymentIntentBase):
    payment: str
    expected_version: int=Field(ge=1)
    date: str
    applications: IntentApplications


class IntentUnapplyReference(View):
    application_id: str
    invoice_expected_version: int=Field(ge=1)


class UnapplyIntent(PaymentIntentBase):
    payment: str
    expected_version: int=Field(ge=1)
    applications: tuple[IntentUnapplyReference,...]


class VoidIntent(PaymentIntentBase):
    payment: str
    expected_version: int=Field(ge=1)


class IntentInvoiceVersion(View):
    invoice: str
    expected_version: int=Field(ge=1)


class UpdateIntent(VoidIntent):
    _internal: ClassVar[frozenset[str]]=PaymentIntentBase._internal|frozenset({'settlement_guard'})
    date: str | None=None
    amount: str | SalesMoneyInput | None=None
    number: str | None=None
    reference: str | None=None
    memo: str | None=None
    payment_method: str | None=None
    deposit_to: str | None=None
    custom_fields: dict[str,str|bool|int|None] | None=None
    expected_custom_field_kinds: dict[str,Literal['text','number','date','bool','choice']] | None=None
    invoice_versions: tuple[IntentInvoiceVersion,...]=()
    settlement_guard: str | None=None


class OriginalContext(View):
    _internal: ClassVar[frozenset[str]]=frozenset({'reason'})
    reason: str | None=None


class OriginalPaymentRequest(View):
    request_schema_version: Literal[1]
    company_id: str
    provided_fields: tuple[str,...]
    context: OriginalContext
    context_provided_fields: tuple[str,...]

    @model_validator(mode='after')
    def field_presence(self):
        if tuple(sorted(self.input.model_fields_set))!=self.provided_fields or tuple(sorted(self.context.model_fields_set))!=self.context_provided_fields:
            raise ValueError('captured request presence differs')
        return self


class OriginalReceiveRequest(OriginalPaymentRequest):
    command: Literal['payment receive']
    input: ReceiveIntent


class OriginalApplyRequest(OriginalPaymentRequest):
    command: Literal['payment apply']
    input: ApplyIntent


class OriginalUnapplyRequest(OriginalPaymentRequest):
    command: Literal['payment unapply']
    input: UnapplyIntent


class OriginalUpdateRequest(OriginalPaymentRequest):
    command: Literal['payment update']
    input: UpdateIntent


class OriginalVoidRequest(OriginalPaymentRequest):
    command: Literal['payment void']
    input: VoidIntent


OriginalPaymentIntent=Annotated[OriginalReceiveRequest|OriginalApplyRequest|OriginalUnapplyRequest|OriginalUpdateRequest|OriginalVoidRequest,Field(discriminator='command')]


class PaymentRequestSnapshot(View):
    _internal: ClassVar[frozenset[str]]=frozenset({'expanded_selection_hash'})
    original_request: OriginalPaymentIntent
    resolved_transaction_ids: tuple[str,...]
    expanded_selection_hash: str | None


class PaymentCurrentResultView(View):
    payment_id: str | None
    version: int
    revision_id: str | None
    status: Literal['posted','voided']
    received_minor_units: int
    effective_received_minor_units: int
    applied_minor_units: int
    available_minor_units: int
    currency: str
    components: tuple[SourceComponentResultView,...]
    component_count: int


class PaymentEffectHeaderView(View):
    id: str | None
    version: int
    revision_id: str | None
    revision_number: int
    number: str
    date: str
    amount: CapturedMoney
    status: Literal['posted','voided']


class PaymentEffectResultView(View):
    kind: Literal['receive','apply','unapply','update','void']
    financial_changed: bool
    audit_event_id: str | None=None
    before_header: PaymentEffectHeaderView | None=None
    after_header: PaymentEffectHeaderView | None=None
    preferences: PaymentPreferences | None=None
    operation_id: str | None
    payment_id: str | None
    source_components: tuple[SourceComponentResultView,...]
    applications: tuple[ApplicationResultView,...]
    allocations: tuple[AllocationResultView,...]
    document_changes: tuple[InvoiceSettlementResultView,...]


class PaymentEffectCountsView(View):
    source_components: int=Field(ge=0)
    applications: int=Field(ge=0)
    allocations: int=Field(ge=0)
    document_changes: int=Field(ge=0)


class PaymentWriteResultView(View):
    _internal: ClassVar[frozenset[str]]=frozenset({'facts_fingerprint'})
    dry_run: bool=False
    warnings: tuple[str,...]=()
    changed: bool=True
    new_effect: bool=True
    id: str | None
    version: int
    operation_key: str
    facts_fingerprint: str
    idempotent_replay: bool=False
    effect: PaymentEffectResultView
    current: PaymentCurrentResultView
    effect_counts: PaymentEffectCountsView
    # payments.apply captures prepare().preview, before preview_output adds
    # prospective descriptors. Committed receipt captures contain an empty tuple.
    prospective_pages: tuple[()]=()


class OperationExecutionView(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset({'actor_id'})
    _internal: ClassVar[frozenset[str]]=frozenset({'directive_code','directive_id','reason'})
    actor_id: str | None
    interface: str
    on_behalf_of: str | None
    reason: str | None
    directive_id: str | None
    directive_code: str | None


class PaymentOperationView(View):
    tag: Literal['payment_operation']='payment_operation'
    _internal: ClassVar[frozenset[str]]=frozenset({'request_hash'})
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset({'created_at','created_by','created_via'})
    id: str
    operation_key: str
    command: Literal['payment receive','payment apply','payment unapply','payment update','payment void']
    request_schema_version: Literal[1]
    request_hash: str
    request_snapshot: PaymentRequestSnapshot
    effect_snapshot: PaymentWriteResultView
    execution_snapshot: OperationExecutionView
    created_at: str | None
    created_by: str | None
    created_via: str | None
    audit_event_id: str

    @model_validator(mode='before')
    @classmethod
    def original_input_contract(cls,value):
        if type(value) is dict:
            from bookflow.company import payment_models as inputs
            owners={'payment receive':inputs.PaymentReceiveInput,'payment apply':inputs.PaymentApplyInput,
                'payment unapply':inputs.PaymentUnapplyInput,'payment update':inputs.PaymentUpdateInput,
                'payment void':inputs.PaymentVoidInput}
            try:
                snapshot=value['request_snapshot']
                if type(snapshot) is str:snapshot=json.loads(snapshot)
                original=snapshot['original_request']['input']
                if 'operation_key' in original:raise ValueError('operation key belongs to receipt')
                owners[value['command']].model_validate_json(json.dumps(dict(original,operation_key=value['operation_key']),allow_nan=False))
            except (KeyError,TypeError):raise ValueError('invalid original payment intent') from None
        return value

    @model_validator(mode='after')
    def operation_identity(self):
        if self.command!=self.request_snapshot.original_request.command or self.command!='payment '+self.effect_snapshot.effect.kind:
            raise ValueError('operation command differs')
        if self.operation_key!=self.effect_snapshot.operation_key or self.id!=self.effect_snapshot.effect.operation_id:
            raise ValueError('operation identity differs')
        return self


_PAYMENT_OPERATION_COMMANDS=('payment receive','payment apply','payment unapply','payment update','payment void')
_REFERENCE_GROUPS.update({
    ReceiveIntent: ((('customer',),'customer'),(('payment_method',),'payment_method'),(('ar_account','deposit_to'),'account')),
    UpdateIntent: ((('payment_method',),'payment_method'),(('deposit_to',),'account')),
})
for _intent in (ReceiveIntent,UpdateIntent):
    for _field in ('custom_fields','expected_custom_field_kinds'):
        _OBJECT_FIELD_REQUIREMENTS[_intent,_field]=('custom_field',)


# Closed invoice-correction result declarations. Schema sources are checked by
# conformance tests; no fields are discovered or added at runtime.
class InvoiceAuditSalesLineInput(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(['item'])
    line_id: str | None = None
    item: str | None
    quantity: str = '1'
    unit: str | None = None
    unit_price: str | SalesMoneyInput | None = None
    net_amount: str | SalesMoneyInput | None = None
    description: str | None = None
    class_id: str | None = None
    tax_code: str | None = None
    price_level: str | None = None
    price_basis_amount: str | SalesMoneyInput | None = None
    refresh_defaults: bool = False
    use_defaults: tuple[Literal['description','unit','unit_price','class_id','tax_code','price_level'],...] = ()


class InvoiceAuditSettlementPaymentVersion(View):
    payment: str
    expected_version: int


class InvoiceAuditInvoiceUpdateInput(View):
    _internal: ClassVar[frozenset[str]]=frozenset(['expected_facts_fingerprint', 'settlement_guard'])
    ar_account: str | None = None
    terms: str | None = None
    due_date: str | None = None
    sales_tax_calculation: Literal['line_component_half_even','line_combined_half_up','invoice_combined_half_up'] = None
    number: str | None = None
    memo: str | None = None
    customer_message: str | None = None
    customer_message_item: str | None = None
    customer_purchase_order: str | None = None
    billing_address: Address | None = None
    shipping_address: Address | None = None
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
    lines: tuple[InvoiceAuditSalesLineInput,...] | None = None
    invoice: str
    settlement_versions: tuple[InvoiceAuditSettlementPaymentVersion,...] = ()
    settlement_guard: str | None = None


class InvoiceAuditTaxDetails(View):
    policy: Literal['line_component_half_even','line_combined_half_up','invoice_combined_half_up']
    origin: Origin
    legacy_interpretation: bool
    attribution: TaxCapture | None = None


class InvoiceAuditJournalBatchOutput(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(['created_at', 'created_by', 'created_via'])
    id: str
    created_at: str | None
    created_by: str | None
    created_via: str | None
    total: CapturedMoney
    transaction_id: str
    revision_id: str
    kind: Literal['original','replacement','reversal']
    effective_date: str
    reverses_batch_id: str | None
    replaces_batch_id: str | None
    audit_event_id: str
    debit_total: CapturedMoney
    credit_total: CapturedMoney
    debit_minor_units: int
    credit_minor_units: int
    currency: str
    line_count: int


class InvoiceAuditBillingSourceLinkOutput(View):
    source_document_id: str
    source_revision_id: str


class InvoiceAuditBillingSourceOutput(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(['created_at', 'created_by', 'created_via'])
    id: str
    created_at: str | None
    created_by: str | None
    created_via: str | None
    transaction_id: str
    revision_id: str
    source_document_id: str
    source_revision_id: str
    source_line_id: str
    root_document_id: str
    root_line_id: str
    document_line_id: str
    quantity_microunits: int | None
    net_minor_units: int
    tax_minor_units: int
    gross_minor_units: int
    facts_snapshot: WorkAllocationCapture
    allocation_version: Literal[1,2,3] = 1
    allocation_proof: AllocationCapture | AllocationCapture | None = None


class InvoiceAuditExactFraction(View):
    numerator: str
    denominator: str


class InvoiceAuditTaxComponentOutput(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(['agency_id', 'created_at', 'created_by', 'created_via', 'liability_account_id', 'tax_item_id'])
    id: str
    created_at: str | None
    created_by: str | None
    created_via: str | None
    transaction_id: str
    revision_id: str
    document_line_id: str
    tax_item_id: str | None
    agency_id: str | None
    liability_account_id: str | None
    rate_percent_millionths: int
    taxable_minor_units: int
    tax_minor_units: int
    taxable: CapturedMoney
    tax: CapturedMoney
    component_snapshot: SalesTaxCapture


class InvoiceAuditSalesLineOutput(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(['created_at', 'created_by', 'created_via', 'item_id'])
    id: str
    created_at: str | None
    created_by: str | None
    created_via: str | None
    tax_ordinal: int | None = None
    transaction_id: str
    revision_id: str
    line_id: str
    position: int
    kind: Literal['sale']
    item_id: str | None
    description: str | None
    quantity: str
    base_quantity: str
    quantity_microunits: int | None
    base_quantity_microunits: int | None
    quantity_fraction: InvoiceAuditExactFraction | None = None
    base_quantity_fraction: InvoiceAuditExactFraction | None = None
    quoted_quantity: str | None = None
    unit_id: str | None
    unit_factor_nanounits: int
    unit_price: CapturedMoney | None
    pricing_basis: Literal['unit','amount','allocated'] = 'unit'
    net: CapturedMoney
    tax: CapturedMoney
    gross: CapturedMoney
    unit_price_minor_units: int | None
    net_minor_units: int
    tax_minor_units: int
    gross_minor_units: int
    currency: str
    item_snapshot: SalesLineCapture
    tax_components: tuple[InvoiceAuditTaxComponentOutput,...]


class InvoiceAuditSalesRevisionOutput(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(['created_at', 'created_by', 'created_via', 'name_id', 'name_type', 'revision_number'])
    id: str
    created_at: str | None
    created_by: str | None
    created_via: str | None
    tax_calculation_details: InvoiceAuditTaxDetails | None = None
    transaction_id: str
    revision_number: int | None
    supersedes_revision_id: str | None
    date: str
    number: str
    name_type: Literal['customer'] | None
    name_id: str | None
    memo: str | None
    subtotal: CapturedMoney
    tax: CapturedMoney
    total: CapturedMoney
    subtotal_minor_units: int
    tax_minor_units: int
    total_minor_units: int
    currency: str
    audit_event_id: str
    line_count: int
    batches: tuple[InvoiceAuditJournalBatchOutput,...]
    billing_links: tuple[InvoiceAuditBillingSourceLinkOutput,...] | None = ()
    billing_sources: tuple[InvoiceAuditBillingSourceOutput,...] | None = ()
    issuer_snapshot: Issuer
    custom_fields_snapshot: CustomCaptures
    custom_fields: tuple[CustomCapture,...] | None
    profile: SalesCapture
    lines: tuple[InvoiceAuditSalesLineOutput,...]


class InvoiceAuditInvoiceCorrectionEffect(View):
    kind: Literal['invoice_update'] = 'invoice_update'
    operation_id: str | None
    invoice_id: str
    audit_event_id: str | None = None
    before_header: PaymentEffectHeaderView | None = None
    after_header: PaymentEffectHeaderView | None = None
    source_components: tuple[SourceComponentResultView,...] = ()
    applications: tuple[ApplicationResultView,...] = ()
    allocations: tuple[AllocationResultView,...]
    document_changes: tuple[InvoiceSettlementResultView | PaymentCurrentResultView,...]
    payment_changes: tuple[PaymentCurrentResultView,...]


class InvoiceAuditInvoiceCorrectionOutput(View):
    _internal: ClassVar[frozenset[str]]=frozenset(['facts_fingerprint'])
    operation_key: str
    facts_fingerprint: str
    changed: bool
    new_effect: bool
    idempotent_replay: bool = False
    effect: InvoiceAuditInvoiceCorrectionEffect
    current: InvoiceSettlementResultView
    effect_counts: PaymentEffectCountsView
    prospective_pages: tuple[()] = ()


class InvoiceAuditWorkBillingSourceEffect(View):
    source_id: str
    source_kind: Literal['estimate','work_order']
    version_before: int
    version_after: int
    active_before: bool
    active_after: bool
    automatically_closed: bool


class InvoiceAuditWorkBillingCurrent(View):
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(['version'])
    source_id: str
    version: int | None
    active: bool
    status: str


class InvoiceAuditForecastReason(View):
    code: Literal['line_span_limit','conversion_span_limit','source_ineligible','posting_ineligible','no_charge']
    line_id: str | None = None
    recovery: str


class InvoiceAuditWorkTaxForecast(View):
    _internal: ClassVar[frozenset[str]]=frozenset(['forecast_fingerprint'])
    forecast_basis: Literal['all_remaining_together'] = 'all_remaining_together'
    can_bill_together: bool
    forecast_eligibility_reasons: tuple[InvoiceAuditForecastReason,...] = ()
    forecast_line_ordinals: dict[str,int]
    forecast_tax_attribution: TaxCapture
    forecast_fingerprint: str


class InvoiceAuditBillingProgressAmount(View):
    quantity: str
    quantity_fraction: InvoiceAuditExactFraction
    scope_percent: str
    scope_percent_fraction: InvoiceAuditExactFraction
    net_minor_units: int
    tax_minor_units: int
    gross_minor_units: int


class InvoiceAuditBillingProgressLine(View):
    line_id: str
    root_document_id: str
    root_line_id: str
    previous: InvoiceAuditBillingProgressAmount
    current: InvoiceAuditBillingProgressAmount
    cumulative: InvoiceAuditBillingProgressAmount
    remaining: InvoiceAuditBillingProgressAmount


class InvoiceAuditSalesWriteOutput(View):
    _internal: ClassVar[frozenset[str]]=frozenset(['void_reason', 'facts_fingerprint'])
    _captured_nonnull: ClassVar[frozenset[str]]=frozenset(['created_at', 'created_by', 'created_via', 'customer_id', 'customer_name', 'updated_at', 'updated_by', 'updated_via', 'version'])
    dry_run: bool = False
    warnings: tuple[str,...] = ()
    id: str
    version: int | None
    created_at: str | None
    created_by: str | None
    created_via: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None
    settlement_current: InvoiceSettlementResultView | None = None
    type: Literal['invoice','sales_receipt']
    number: str
    current_revision_id: str
    status: Literal['posted','voided']
    voided_at: str | None
    voided_by: str | None
    void_reason: str | None
    void_posting_batch_id: str | None
    date: str
    customer_id: str | None
    customer_name: str | None
    memo: str | None
    due_date: str | None
    subtotal: CapturedMoney
    tax: CapturedMoney
    total: CapturedMoney
    subtotal_minor_units: int
    tax_minor_units: int
    total_minor_units: int
    currency: str
    revision: InvoiceAuditSalesRevisionOutput
    settlement: InvoiceAuditInvoiceCorrectionOutput | None = None
    source_effect: InvoiceAuditWorkBillingSourceEffect | None = None
    source_current: InvoiceAuditWorkBillingCurrent | None = None
    billing_forecast: InvoiceAuditWorkTaxForecast | None = None
    billing_progress: tuple[InvoiceAuditBillingProgressLine,...] | None = ()
    facts_fingerprint: str | None = None
    changed: bool = True
    changed_fields: tuple[str,...] = ()
    merged_over_versions: tuple[int,...] = ()
    idempotent_replay: bool = False



class OriginalInvoiceRequest(OriginalPaymentRequest):
    command: Literal['invoice update']
    input: InvoiceAuditInvoiceUpdateInput


class InvoiceRequestSnapshot(PaymentRequestSnapshot):
    original_request: OriginalInvoiceRequest


class InvoiceOperationView(PaymentOperationView):
    command: Literal['invoice update']
    request_snapshot: InvoiceRequestSnapshot
    effect_snapshot: InvoiceAuditSalesWriteOutput

    @model_validator(mode='before')
    @classmethod
    def original_input_contract(cls,value):
        if type(value) is dict:
            from bookflow.company.sales_models import InvoiceUpdateInput
            from bookflow.company.sales_outputs import SalesWriteOutput
            try:
                request=value['request_snapshot'];effect=value['effect_snapshot']
                if type(request) is str:request=json.loads(request)
                if type(effect) is str:effect=json.loads(effect)
                original=request['original_request']['input']
                if 'operation_key' in original:raise ValueError('operation key belongs to receipt')
                InvoiceUpdateInput.model_validate_json(json.dumps(dict(original,operation_key=value['operation_key']),allow_nan=False))
                SalesWriteOutput.model_validate_json(json.dumps(effect,allow_nan=False))
            except (KeyError,TypeError):raise ValueError('invalid original invoice correction') from None
        return value

    @model_validator(mode='after')
    def operation_identity(self):
        settled=self.effect_snapshot.settlement
        if settled is None or self.request_snapshot.original_request.command!=self.command:
            raise ValueError('missing invoice correction receipt')
        if settled.operation_key!=self.operation_key or settled.effect.operation_id!=self.id:
            raise ValueError('operation identity differs')
        if settled.effect.invoice_id!=self.effect_snapshot.id or self.effect_snapshot.id not in self.request_snapshot.resolved_transaction_ids:
            raise ValueError('invoice correction identity differs')
        return self


class InvoiceDocumentChangeItemView(OperationItemView):
    kind: Literal['document_changes']
    item_snapshot: InvoiceSettlementResultView | PaymentCurrentResultView


_REFERENCE_GROUPS.update({
    InvoiceAuditInvoiceUpdateInput: (
        (('ar_account',),'account'),(('terms',),'term'),(('customer',),'customer'),
        (('customer_message_item',),'customer_message'),(('shipping_address_id',),'customer'),
        (('ship_method',),'ship_method'),(('sales_rep',),'sales_rep'),(('class_id',),'class'),
        (('customer_tax_code',),'sales_tax_code'),(('sales_tax_item',),'item'),(('price_level',),'price_level')),
    InvoiceAuditSalesLineInput: ((('item',),'item'),(('unit',),'unit_of_measure'),(('class_id',),'class'),
        (('tax_code',),'sales_tax_code'),(('price_level',),'price_level')),
    InvoiceAuditSalesWriteOutput: ((('customer_id','customer_name'),'customer'),),
    InvoiceAuditSalesRevisionOutput: ((('name_type','name_id'),'customer'),),
    InvoiceAuditSalesLineOutput: ((('item_id',),'item'),(('unit_id',),'unit_of_measure')),
    InvoiceAuditTaxComponentOutput: ((('tax_item_id',),'item'),(('agency_id',),'vendor'),(('liability_account_id',),'account')),
})
for _field in ('billing_address','shipping_address'):
    _OBJECT_FIELD_REQUIREMENTS[InvoiceAuditInvoiceUpdateInput,_field]=('customer',)
for _field in ('custom_fields','custom_field_kinds'):
    _OBJECT_FIELD_REQUIREMENTS[InvoiceAuditInvoiceUpdateInput,_field]=('custom_field',)
_OBJECT_FIELD_REQUIREMENTS[InvoiceAuditSalesRevisionOutput,'custom_fields']=('custom_field',)

for _field in ('billing_links','billing_sources'):
    _OBJECT_FIELD_REQUIREMENTS[InvoiceAuditSalesRevisionOutput,_field]=('work_order',)
for _field in ('source_effect','source_current','billing_forecast','billing_progress'):
    _OBJECT_FIELD_REQUIREMENTS[InvoiceAuditSalesWriteOutput,_field]=('work_order',)
