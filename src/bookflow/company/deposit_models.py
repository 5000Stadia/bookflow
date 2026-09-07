"""Strict private G1 deposit intents and immutable resolved financial facts."""
from typing import Annotated, Literal
from pydantic import ConfigDict, Field, model_validator, field_validator
from bookflow.company.sales_models import StrictModel, Selector, Fingerprint
from bookflow.company.journal_models import _Date, _Number, _Version
from bookflow.company.payment_models import OperationKey
from bookflow.company.payment_outputs import PaymentProfileOutput
from bookflow.company.sales_facts import SalesProfile, SalesLineProfile, SalesTaxComponent, Reference
from bookflow.core.exact import INT64_MAX
from bookflow.core.money import is_currency
from bookflow.company.custom_fields import CustomFieldKindExpectations, CustomFieldValuePatch
from bookflow.core.errors import BookflowError

ID = Annotated[str, Field(min_length=1, max_length=26, pattern=r'^[A-Za-z0-9_-]+$')]
Units = Annotated[int, Field(ge=0, le=INT64_MAX)]
Positive = Annotated[int, Field(gt=0, le=INT64_MAX)]
Text = Annotated[str, Field(max_length=2000)]
Kind = Literal['customer','vendor','employee','other_name']


class SignedMoney(StrictModel):
    minor_units: int = Field(ge=-INT64_MAX, le=INT64_MAX)
    currency: str

    @model_validator(mode='after')
    def known_currency(self):
        if not is_currency(self.currency):raise ValueError('unknown currency')
        return self


Amount = str | SignedMoney


def amount(value, currency):
    if type(value) is str:
        import re
        from bookflow.core.exact import _parse_scaled_decimal
        from bookflow.core.money import minor_units_of
        m=re.fullmatch(r'(-?[0-9]+(?:\.[0-9]+)?)(?: ([A-Z]{3}))?',value)
        if not m or m[2] not in (None,currency):raise BookflowError('E_VALIDATION')
        if '.' in m[1] and len(m[1].split('.')[1])>minor_units_of(currency):raise BookflowError('E_AMOUNT_PRECISION')
        return _parse_scaled_decimal(m[1],scale=minor_units_of(currency),field='amount')
    if type(value) is not SignedMoney or value.currency!=currency:raise BookflowError('E_VALIDATION')
    return value.minor_units


class Party(StrictModel):
    kind: Kind
    id: ID


class SourceInput(StrictModel):
    source_type: Literal['payment','sales_receipt']
    source: ID
    expected_version: _Version
    memo_override: Text | None = None


class AdditionalInput(StrictModel):
    line_id: ID | None = None
    received_from: Party
    from_account: Selector
    amount: Amount
    memo: Text | None = None
    check_number: Annotated[str,Field(max_length=128)] | None = None
    payment_method: Selector | None = None
    class_id: Selector | None = Field(default=None,alias='class')


class CashBackInput(StrictModel):
    account: Selector
    amount: Amount
    memo: Text | None = None


class InlineDocument(StrictModel):
    mode: Literal['inline']='inline'
    deposit_to: Selector
    date: _Date
    number: _Number | None = None
    memo: Text | None = None
    cash_back: CashBackInput | None = None
    custom_fields: CustomFieldValuePatch = Field(default_factory=lambda: CustomFieldValuePatch({}))
    expected_custom_field_kinds: CustomFieldKindExpectations = Field(default_factory=lambda: CustomFieldKindExpectations({}))
    sources: list[SourceInput] = Field(default_factory=list)
    additional: list[AdditionalInput] = Field(default_factory=list)

    @model_validator(mode='after')
    def complete_rows(self):
        if len(self.sources)+len(self.additional)>200:raise ValueError('inline rows exceed200')
        if len({s.source for s in self.sources})!=len(self.sources):raise ValueError('duplicate source')
        ids=[r.line_id for r in self.additional if r.line_id is not None]
        if len(set(ids))!=len(ids):raise ValueError('duplicate line')
        return self


class DraftDocument(StrictModel):
    mode: Literal['draft']
    draft: ID
    expected_version: _Version


class PostInput(StrictModel):
    operation_key: OperationKey
    expected_facts_fingerprint: Fingerprint | None = None
    document: Annotated[InlineDocument|DraftDocument,Field(discriminator='mode')]


class ReplacementDocument(StrictModel):
    mode: Literal['inline']
    deposit_to: Selector
    date: _Date
    number: _Number
    memo: Text | None
    cash_back: CashBackInput | None
    custom_fields: CustomFieldValuePatch
    expected_custom_field_kinds: CustomFieldKindExpectations
    sources: list[SourceInput] = Field(max_length=200)
    additional: list[AdditionalInput] = Field(max_length=200)

    @model_validator(mode='after')
    def complete_rows(self):
        InlineDocument.complete_rows(self)
        return self


class ReplaceInput(StrictModel):
    operation_key: OperationKey
    expected_facts_fingerprint: Fingerprint | None = None
    deposit: ID
    expected_version: _Version
    document: Annotated[ReplacementDocument | DraftDocument, Field(discriminator='mode')]


class Frozen(StrictModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True)


class SemanticKey(Frozen):
    kind: Literal['payment','sale_net','sale_tax']
    identity: ID
    tax_item: Annotated[str, Field(pattern=r'^(?:[A-Za-z0-9_-]{1,26})?$')] = ''

    def order(self):return self.kind,self.identity,self.tax_item


class Dimensions(Frozen):
    party_kind: Kind | None
    party_id: ID | None
    party_name: str | None
    class_id: ID | None
    class_name: str | None

    @model_validator(mode='after')
    def pairs(self):
        if (self.party_kind is None)!=(self.party_id is None) or (self.party_id is None)!=(self.party_name is None):
            raise ValueError('partial party')
        if (self.class_id is None)!=(self.class_name is None):
            raise ValueError('partial class')
        return self


class CashComponent(Frozen):
    key: SemanticKey
    capacity: Positive
    document_line_id: ID
    posting_line_id: ID
    posting_source_id: ID
    physical_component_id: ID | None
    cash: Dimensions
    credit_owner_party: ID | None = None
    credit_owner_ar: ID | None = None
    sale_line: SalesLineProfile | None = None
    tax: SalesTaxComponent | None = None


class CashSource(Frozen):
    source_type: Literal['payment','sales_receipt']
    transaction_id: ID
    expected_header_version: Positive
    revision_id: ID
    business_batch_id: ID
    receipt_date: _Date
    currency: str
    cash_minor_units: Positive
    uf_account: ID
    source_memo: Text | None
    source_reference: str | None
    profile: PaymentProfileOutput | SalesProfile
    semantic_presence: tuple[SemanticKey,...]
    components: tuple[CashComponent,...]
    dependencies: tuple[ID,...]


class ComponentOccurrence(Frozen):
    key: SemanticKey
    ordinal: Positive
    present: bool


class SourceRow(Frozen):
    row_id: ID
    ordinal: Positive
    source: CashSource
    occurrences: tuple[ComponentOccurrence,...]
    memo: Text | None
    memo_origin: Literal['source','entered']


class Account(Frozen):
    id: ID
    name: str
    full_name: str
    number: str | None
    normal_balance: Literal['debit','credit']
    type: Literal['bank','accounts_receivable','other_current_asset','fixed_asset','other_asset','accounts_payable','credit_card','other_current_liability','long_term_liability','equity','income','cost_of_goods_sold','expense','other_income','other_expense','non_posting']
    system_role: str | None
    active: bool
    currency: str


class Additional(Frozen):
    row_id: ID
    ordinal: Positive
    account: Account
    units: int = Field(ge=-INT64_MAX,le=INT64_MAX)
    dimensions: Dimensions
    memo: Text | None = None
    check_number: str | None = None
    payment_method: Reference | None = None


class CashBack(Frozen):
    account: Account
    units: Positive
    memo: Text | None = None


class Intent(Frozen):
    deposit_id: ID
    date: _Date
    currency: str
    bank: Account
    sources: tuple[SourceRow,...]
    additional: tuple[Additional,...]
    cash_back: CashBack | None = None


class Cell(Frozen):
    row_id: ID
    component_ordinal: int = Field(ge=1,le=INT64_MAX)
    bucket: str
    units: Positive


class Leg(Frozen):
    key: str
    account_id: ID
    signed_debit: int = Field(ge=-INT64_MAX,le=INT64_MAX)
    currency: str
    dimensions: Dimensions


class Effect(Frozen):
    schema_version: Literal[1] = 1

    @field_validator('schema_version', mode='before')
    @classmethod
    def exact_schema_version(cls, value):
        if type(value) is not int:
            raise ValueError('schema version must be an integer')
        return value

    intent: Intent
    posting_total: Positive
    subtotal: Positive
    bank_total: Units
    cash_back: Units
    cells: tuple[Cell,...]
    legs: tuple[Leg,...]
    inverse_of: str | None = None
