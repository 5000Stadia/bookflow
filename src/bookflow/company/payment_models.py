"""Strict public payment intent and shared selection-origin models."""
from typing import Annotated, Literal

from pydantic import Field, model_validator, model_serializer

from bookflow.company.custom_fields import CustomFieldKindExpectations, CustomFieldValuePatch
from bookflow.company.journal_models import _Date, _Number, _Version
from bookflow.company.sales_models import StrictModel, Selector, Fingerprint, SalesMoneyInput

Amount = str | SalesMoneyInput
OperationKey = Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$')]


class InvoiceAmount(StrictModel):
    invoice: Selector
    expected_version: _Version
    amount: Amount


class DraftInvoiceAmount(StrictModel):
    invoice: Selector
    expected_version: _Version
    amount: Amount | None = None
    amount_origin: Literal['entered', 'calculated', 'unresolved'] | None = None

    @model_validator(mode='after')
    def origin(self):
        if self.amount_origin == 'unresolved' and self.amount is not None:
            raise ValueError('unresolved row cannot contain an amount')
        if self.amount_origin == 'entered' and self.amount is None:
            raise ValueError('entered row requires an amount')
        return self


class InlineApplications(StrictModel):
    mode: Literal['inline'] = 'inline'
    items: list[InvoiceAmount] = Field(default_factory=list, max_length=200)


class InlineCalculation(StrictModel):
    mode: Literal['inline'] = 'inline'
    items: list[DraftInvoiceAmount] = Field(default_factory=list, max_length=200)


class SelectionReference(StrictModel):
    mode: Literal['selection']
    selection: Selector
    expected_version: _Version


Applications = Annotated[InlineApplications | SelectionReference, Field(discriminator='mode')]
CalculationApplications = Annotated[InlineCalculation | SelectionReference, Field(discriminator='mode')]


class PaymentContext(StrictModel):
    mode: Literal['new_receipt', 'existing_credit']
    customer: Selector | None = None
    ar_account: Selector | None = None
    payment: Selector | None = None
    date: _Date

    @model_validator(mode='after')
    def context(self):
        if self.mode == 'new_receipt':
            if self.customer is None or 'payment' in self.model_fields_set:
                raise ValueError('new_receipt requires customer and forbids payment')
        elif self.payment is None or {'customer', 'ar_account'} & self.model_fields_set:
            raise ValueError('existing_credit requires payment and forbids customer/ar_account')
        return self


class Page(StrictModel):
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=2048)


class PaymentInvoicesInput(PaymentContext, Page):
    q: str | None = Field(default=None, max_length=200)


class PaymentSuggestInput(PaymentContext, Page):
    amount: Amount
    strategy: Literal['company', 'exact_then_oldest', 'none'] = 'company'


class PaymentCalculateInput(PaymentContext, Page):
    amount_mode: Literal['company', 'entered', 'selection_total'] = 'company'
    amount: Amount | None = None
    applications: CalculationApplications = Field(default_factory=InlineCalculation)


class SelectionCreateInput(PaymentContext):
    label: str | None = Field(default=None, max_length=128)
    amount: Amount | None = None
    amount_origin: Literal['entered', 'selection_total', 'unresolved'] | None = None


class SelectionUpdateInput(StrictModel):
    selection: Selector
    expected_version: _Version
    set_items: list[DraftInvoiceAmount] = Field(default_factory=list, max_length=200)
    remove_invoices: list[Selector] = Field(default_factory=list, max_length=200)
    amount: Amount | None = None
    amount_origin: Literal['entered', 'selection_total', 'unresolved'] | None = None
    adopt_calculation_policy: bool | None = None

    @model_validator(mode='after')
    def patch(self):
        if not self.set_items and not self.remove_invoices and not ({'amount', 'amount_origin', 'adopt_calculation_policy'} & self.model_fields_set):
            raise ValueError('selection update requires a change')
        identifiers = [row.invoice for row in self.set_items]
        if len(set(identifiers)) != len(identifiers) or len(set(self.remove_invoices)) != len(self.remove_invoices):
            raise ValueError('duplicate invoice in patch')
        if set(identifiers) & set(self.remove_invoices):
            raise ValueError('set and remove invoices must be disjoint')
        return self


class SelectionClearInput(StrictModel):
    selection: Selector
    expected_version: _Version


class SelectionShowInput(StrictModel):
    selection: Selector
    revision: _Version | None = None


class SelectionItemsInput(SelectionShowInput, Page):
    pass


class SelectionQueryInput(Page):
    state: Literal['open', 'consumed'] | None = None


class PaymentReceiveInput(StrictModel):
    customer: Selector
    date: _Date
    amount: Amount
    operation_key: OperationKey
    applications: Applications = Field(default_factory=InlineApplications)
    payment_method: Selector | None = None
    ar_account: Selector | None = None
    deposit_to: Selector | None = None
    number: _Number | None = None
    reference: str | None = Field(default=None, max_length=128)
    memo: str | None = Field(default=None, max_length=2000)
    custom_fields: CustomFieldValuePatch = Field(default_factory=lambda: CustomFieldValuePatch({}))
    expected_custom_field_kinds: CustomFieldKindExpectations = Field(default_factory=lambda: CustomFieldKindExpectations({}))
    expected_facts_fingerprint: Fingerprint | None = None


class PaymentApplyInput(StrictModel):
    payment: Selector
    expected_version: _Version
    date: _Date
    applications: Applications
    operation_key: OperationKey
    expected_facts_fingerprint: Fingerprint | None = None


class PaymentShowInput(StrictModel):
    payment: Selector
    revision: _Version | None = None


class PaymentQueryInput(Page):
    customer: Selector | None = None
    include_descendants: bool = False
    component_customer: Selector | None = None
    payment_method: Selector | None = None
    status: Literal['posted', 'voided'] | None = None
    has_available_credit: bool | None = None
    date_from: _Date | None = None
    date_to: _Date | None = None
    number: _Number | None = None
    q: str | None = Field(default=None, max_length=200)
    sort: Literal['date', 'number', 'received', 'unapplied'] = 'date'
    direction: Literal['asc', 'desc'] = 'desc'


class PaymentOperationShowInput(StrictModel):
    operation_key: OperationKey


class PaymentSettlementInput(Page):
    payment: Selector
    kind: Literal['components', 'applications'] = 'components'


class InvoiceSettlementInput(StrictModel):
    invoice: Selector


class PaymentOperationItemsInput(PaymentOperationShowInput, Page):
    kind: Literal['request_applications', 'effect_applications', 'allocations', 'document_changes', 'source_components']


class PreviewContext(StrictModel):
    reason: str | None = Field(default=None, max_length=140)
    directive_id: str | None = None


class ReceivePreviewRequest(StrictModel):
    command: Literal['payment receive']
    input: PaymentReceiveInput
    context: PreviewContext = Field(default_factory=PreviewContext)

    @model_serializer(mode='wrap')
    def original_input(self, handler):
        result = handler(self)
        result['input'] = self.input.model_dump(mode='json', exclude_unset=True)
        result['context'] = self.context.model_dump(mode='json', exclude_unset=True)
        return result


class ApplyPreviewRequest(StrictModel):
    command: Literal['payment apply']
    input: PaymentApplyInput
    context: PreviewContext = Field(default_factory=PreviewContext)

    @model_serializer(mode='wrap')
    def original_input(self, handler):
        result = handler(self)
        result['input'] = self.input.model_dump(mode='json', exclude_unset=True)
        result['context'] = self.context.model_dump(mode='json', exclude_unset=True)
        return result


PreviewRequest = Annotated[ReceivePreviewRequest | ApplyPreviewRequest, Field(discriminator='command')]


class PaymentPreviewItemsInput(Page):
    request: PreviewRequest
    facts_fingerprint: Fingerprint
    kind: Literal['source_components', 'applications', 'allocations', 'document_changes']
