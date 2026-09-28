"""Commercial sales results shared by all command adapters."""
from bookflow.company.sales_deletion_models import SalesDeletionInfo
from typing import Literal
from pydantic import Field, model_serializer, model_validator

from bookflow.commands.common import CommonOut
from bookflow.company.journal_custom_fields import SnapshotField
from bookflow.company.journal_outputs import CreatedOutput, JournalBatchOutput, JournalMoneyOutput
from bookflow.company.sales_facts import SalesProfile, SalesLineProfile, SalesTaxComponent
from bookflow.company.sales_models import StrictModel
from bookflow.company.tax_attribution import TaxDetails
from bookflow.company.tax_forecasts import WorkTaxForecast
from bookflow.company.billing_facts import AllocationProof, TaxAllocationProof, ExactFraction, BILLING_KINDS
from bookflow.core.models import WriteOutput
from bookflow.company.payment_outputs import InvoiceSettlementOutput, InvoiceCorrectionOutput

MoneyOutput = JournalMoneyOutput


class TaxComponentOutput(CreatedOutput):
    transaction_id: str
    revision_id: str
    document_line_id: str
    tax_item_id: str
    agency_id: str
    liability_account_id: str
    rate_percent_millionths: int
    taxable_minor_units: int
    tax_minor_units: int
    taxable: MoneyOutput
    tax: MoneyOutput
    component_snapshot: SalesTaxComponent


class SalesLineOutput(CreatedOutput):
    tax_ordinal: int | None = None
    line_kind: Literal["item", "subtotal", "discount", "charge"] | None = Field(default=None, description=(
        "Present on a subtotal, discount or percentage-charge line, and on a line a discount reduced. "
        "Absent on every other line, which is an ordinary item line."))
    amount: MoneyOutput | None = Field(default=None, description=(
        "The amount the line shows, when it differs from net: a subtotal's sum, a discount's negative "
        "amount, or a sold line's amount before the discounts taken out of its net."))

    @model_serializer(mode='wrap')
    def preserve_legacy_tax_order(self, handler):
        result = handler(self)
        for key in ('tax_ordinal', 'line_kind', 'amount'):
            if getattr(self, key) is None:
                result.pop(key, None)
        return result

    transaction_id: str
    revision_id: str
    line_id: str
    position: int
    kind: Literal["sale"]
    item_id: str
    description: str | None
    quantity: str
    base_quantity: str
    quantity_microunits: int | None
    base_quantity_microunits: int | None
    quantity_fraction: ExactFraction | None = None
    base_quantity_fraction: ExactFraction | None = None
    quoted_quantity: str | None = None
    unit_id: str | None
    unit_factor_nanounits: int
    unit_price: MoneyOutput | None
    pricing_basis: Literal["unit", "amount", "allocated"] = "unit"
    net: MoneyOutput
    tax: MoneyOutput
    gross: MoneyOutput
    unit_price_minor_units: int | None
    net_minor_units: int
    tax_minor_units: int
    gross_minor_units: int
    currency: str
    item_snapshot: SalesLineProfile
    tax_components: list[TaxComponentOutput]


class BillingSourceLinkOutput(StrictModel):
    source_document_id: str
    source_revision_id: str


class BillingSourceOutput(CreatedOutput):
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
    facts_snapshot: dict
    allocation_version: Literal[1, 2, 3] = 1
    allocation_proof: AllocationProof | TaxAllocationProof | None = None


class SalesRevisionSummaryOutput(CreatedOutput):
    tax_calculation_details: TaxDetails | None = None

    @model_serializer(mode='wrap')
    def preserve_legacy_retry(self, handler):
        result = handler(self)
        if self.tax_calculation_details is None:
            result.pop('tax_calculation_details', None)
        return result

    transaction_id: str
    revision_number: int
    supersedes_revision_id: str | None
    date: str
    number: str
    name_type: Literal["customer"]
    name_id: str
    memo: str | None
    subtotal: MoneyOutput
    tax: MoneyOutput
    total: MoneyOutput
    subtotal_minor_units: int
    tax_minor_units: int
    total_minor_units: int
    currency: str
    audit_event_id: str
    line_count: int
    batches: list[JournalBatchOutput]
    billing_links: list[BillingSourceLinkOutput] = Field(default_factory=list)


class SalesRevisionOutput(SalesRevisionSummaryOutput):
    billing_sources: list[BillingSourceOutput] = Field(default_factory=list)
    issuer_snapshot: dict[str, str | None]
    custom_fields_snapshot: dict[str, SnapshotField]
    custom_fields: list[SnapshotField]
    profile: SalesProfile
    lines: list[SalesLineOutput]


class SalesSummaryOutput(CommonOut):
    settlement_current: InvoiceSettlementOutput | None = None

    @model_serializer(mode='wrap')
    def compatible_summary(self, handler):
        result = handler(self)
        if self.settlement_current is None:
            result.pop('settlement_current', None)
        return result
    type: Literal["invoice", "sales_receipt", "statement_charge"]
    number: str
    current_revision_id: str
    status: Literal["posted", "voided", "deleted"]
    deletion: SalesDeletionInfo | None = Field(default=None, exclude_if=lambda v: v is None)
    voided_at: str | None
    voided_by: str | None
    void_reason: str | None
    void_posting_batch_id: str | None
    date: str
    customer_id: str
    customer_name: str
    memo: str | None
    due_date: str | None
    subtotal: MoneyOutput
    tax: MoneyOutput
    total: MoneyOutput
    subtotal_minor_units: int
    tax_minor_units: int
    total_minor_units: int
    currency: str


class SalesTaxRateApplied(StrictModel):
    label: str
    rate_percent: str
    agency: str


class SalesTaxApplied(StrictModel):
    """Which sales tax this sale charged, read off its own saved revision.

    QuickBooks shows the Tax item and its rate beside the tax amount on the form; this is that
    field, so an agent need not dig through ``revision.profile`` or the tax attribution.
    """
    item: str | None = Field(description='The sales tax item or tax group on the sale; null when none applies')
    item_id: str | None = None
    rate_percent: str = Field(description='The combined rate of every component, e.g. "8.25"')
    components: list[SalesTaxRateApplied] = Field(default_factory=list)
    chosen_by: Literal['this document', 'customer', 'parent customer', 'company default', 'none'] = Field(
        description="Where the tax item came from: typed on this sale, the customer's own tax item, a parent "
                    "customer's, or the company's default sales tax item")
    customer_tax_code: str | None = None
    customer_taxable: bool = True
    amount: MoneyOutput
    summary: str


def _percent(millionths):
    from decimal import Decimal
    text = format(Decimal(millionths) / Decimal(1_000_000), 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def sales_tax_applied(profile, tax, issuer_id):
    """The top-level ``sales_tax`` summary for one saved revision, or None before tax facts exist."""
    item, rules = profile.sales_tax_item, profile.tax_rules or []
    origin = profile.origins.get('sales_tax_item')
    chosen = ('none' if item is None else 'this document' if origin is None or origin.kind == 'explicit'
              else 'customer' if origin.source_id == profile.customer.id
              else 'company default' if origin.source_id == issuer_id else 'parent customer')
    rate = _percent(sum(rule.rate_percent_millionths for rule in rules))
    code = profile.customer_tax_code
    taxable = code.taxable if code is not None else True
    why = {'this document': 'entered on this sale', 'customer': f"{profile.customer.label}'s own tax item",
           'parent customer': "inherited from the parent customer", 'company default':
           f"the company default, because {profile.customer.label} has no tax item of its own", 'none': ''}[chosen]
    if item is None:
        text = f'No sales tax item applies; tax is {tax.amount}.'
    else:
        parts = ', '.join(f'{rule.label} {_percent(rule.rate_percent_millionths)}%' for rule in rules)
        text = f'{item.label} at {rate}%' + (f' ({parts})' if len(rules) > 1 else '') + f', {why}; tax {tax.amount}.'
        if not taxable:
            text += f' The customer tax code {code.label} is non-taxable, so nothing is charged.'
    return SalesTaxApplied(item=item.label if item else None, item_id=item.id if item else None, rate_percent=rate,
        components=[SalesTaxRateApplied(label=rule.label, rate_percent=_percent(rule.rate_percent_millionths),
                                        agency=rule.agency.label) for rule in rules],
        chosen_by=chosen, customer_tax_code=code.label if code else None, customer_taxable=taxable,
        amount=tax, summary=text)


class SalesOutput(SalesSummaryOutput):
    revision: SalesRevisionOutput
    settlement_current: InvoiceSettlementOutput | None = None
    sales_tax: SalesTaxApplied | None = Field(default=None, description=(
        'Which sales tax item and rate this sale charged and where that choice came from.'))

    @model_validator(mode='after')
    def _which_tax_applied(self):
        if self.sales_tax is None:
            object.__setattr__(self, 'sales_tax', sales_tax_applied(
                self.revision.profile, self.tax, (self.revision.issuer_snapshot or {}).get('id')))
        return self

    @model_serializer(mode='wrap')
    def compatible_settlement(self, handler):
        result = handler(self)
        if self.settlement_current is None:
            result.pop('settlement_current', None)
        return result


class BillingProgressAmount(StrictModel):
    quantity: str
    quantity_fraction: ExactFraction
    scope_percent: str
    scope_percent_fraction: ExactFraction
    net_minor_units: int
    tax_minor_units: int
    gross_minor_units: int


class BillingProgressLine(StrictModel):
    line_id: str
    root_document_id: str
    root_line_id: str
    previous: BillingProgressAmount
    current: BillingProgressAmount
    cumulative: BillingProgressAmount
    remaining: BillingProgressAmount


class WorkBillingSourceEffect(StrictModel):
    source_id: str
    source_kind: Literal[BILLING_KINDS]
    version_before: int
    version_after: int
    active_before: bool
    active_after: bool
    automatically_closed: bool


class WorkBillingCurrent(StrictModel):
    source_id: str
    version: int
    active: bool
    status: str


class SalesWriteOutput(SalesOutput, WriteOutput):
    settlement: InvoiceCorrectionOutput | None = None
    source_effect: WorkBillingSourceEffect | None = None
    source_current: WorkBillingCurrent | None = None
    billing_forecast: WorkTaxForecast | None = None
    billing_progress: list[BillingProgressLine] = Field(default_factory=list)
    facts_fingerprint: str | None = None
    changed: bool = True
    changed_fields: list[str] = Field(default_factory=list)
    merged_over_versions: list[int] = Field(default_factory=list)
    idempotent_replay: bool = False

    @model_serializer(mode='wrap')
    def compatible_payment_settlement(self, handler):
        result = handler(self)
        if self.billing_forecast is None:result.pop('billing_forecast',None)
        if self.settlement is None:
            result.pop('settlement', None)
        if self.settlement_current is None:
            result.pop('settlement_current', None)
        return result


class SalesPageOutput(StrictModel):
    items: list[SalesSummaryOutput]
    count: int
    has_more: bool
    next_cursor: str | None
    audit_watermark: int


class SalesHistoryLine(StrictModel):
    """One line as this revision had it: enough to see an edit such as 2 valves becoming 1."""
    position: int
    item: str
    description: str | None
    quantity: str
    unit_price: str | None = Field(description='The rate; null on a line priced by amount')
    amount: str


class SalesHistoryRevisionOutput(SalesRevisionSummaryOutput):
    lines: list[SalesHistoryLine] = Field(default_factory=list)
    sales_tax: SalesTaxApplied | None = None


def history_revision(full):
    """Project a full revision output down to the history row: header facts, lines, tax applied."""
    values = full.model_dump(include=set(SalesRevisionSummaryOutput.model_fields), exclude_none=False)
    if full.tax_calculation_details is None:
        values.pop('tax_calculation_details', None)
    values['lines'] = [SalesHistoryLine(position=line.position, item=line.item_snapshot.item.label,
        description=line.description, quantity=line.quantity,
        unit_price=line.unit_price.amount if line.unit_price is not None else None, amount=line.net.amount)
        for line in full.lines]
    values['sales_tax'] = sales_tax_applied(full.profile, full.tax, (full.issuer_snapshot or {}).get('id'))
    return SalesHistoryRevisionOutput.model_validate(values, strict=False)


class SalesHistoryOutput(StrictModel):
    id: str
    version: int
    current_revision_id: str
    number: str
    status: Literal["posted", "voided", "deleted"]
    deletion: SalesDeletionInfo | None = Field(default=None, exclude_if=lambda v: v is None)
    items: list[SalesHistoryRevisionOutput]
    count: int
    has_more: bool
    next_cursor: str | None
    audit_watermark: int
