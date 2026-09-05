"""Version-one captured work facts, independent of document persistence."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from bookflow.company.journal_models import _Date
from bookflow.company.sales_calculations import adjusted_price, base_quantity, extension, tax
from bookflow.company.sales_facts import CommercialProfile, Origin, Reference, SalesLineProfile, TaxRule
from bookflow.company.sales_models import Address, StrictModel, Text
from bookflow.company.work_models import Priority, ScopeText, Timestamp
from bookflow.core.exact import INT64_MAX

MinorUnits = Annotated[int, Field(ge=0, le=INT64_MAX)]


class WorkProfile(CommercialProfile):
    pass


class WorkFacts(StrictModel):
    schema_version: Literal[1] = 1
    profile: WorkProfile
    issuer_snapshot: dict[str, str | None]
    memo: Text | None = None
    scope: ScopeText | None = None
    inclusions: ScopeText | None = None
    exclusions: ScopeText | None = None
    timing: ScopeText | None = None
    commercial_terms: ScopeText | None = None
    expires_on: _Date | None = None
    priority: Priority = 'normal'
    site_address: Address | None = None
    assignees: list[Reference] = Field(default_factory=list, max_length=50)
    scheduled_start: Timestamp | None = None
    scheduled_end: Timestamp | None = None
    actual_start: Timestamp | None = None
    actual_end: Timestamp | None = None

    @model_validator(mode='after')
    def operational_consistency(self):
        if len({value.id for value in self.assignees}) != len(self.assignees):
            raise ValueError('assignees must be distinct employees')
        for prefix in ('scheduled', 'actual'):
            start, end = getattr(self, prefix + '_start'), getattr(self, prefix + '_end')
            if end and (not start or datetime.fromisoformat(end) < datetime.fromisoformat(start)):
                raise ValueError(f'{prefix}_end requires a start no later than end')
        return self


class WorkTaxComponent(StrictModel):
    rule: TaxRule
    taxable_minor_units: MinorUnits
    tax_minor_units: MinorUnits

    @model_validator(mode='after')
    def arithmetic(self):
        if self.tax_minor_units != tax(self.taxable_minor_units, self.rule.rate_percent_millionths):
            raise ValueError('tax component disagrees with captured rate and net')
        return self


class WorkLineFacts(StrictModel):
    schema_version: Literal[1] = 1
    item_id: str
    description: Text | None
    quantity_microunits: int = Field(gt=0, le=INT64_MAX)
    completed_quantity_microunits: MinorUnits = 0
    unit_id: str | None
    unit_factor_nanounits: int = Field(gt=0, le=INT64_MAX)
    base_quantity_microunits: int = Field(gt=0, le=INT64_MAX)
    unit_price_minor_units: MinorUnits | None
    net_minor_units: MinorUnits
    tax_minor_units: MinorUnits
    gross_minor_units: MinorUnits
    estimated_unit_cost_minor_units: MinorUnits | None
    estimated_cost_minor_units: MinorUnits | None
    pricing_basis: Literal['catalog', 'manual', 'markup', 'amount']
    markup_percent_millionths: int | None = Field(default=None, ge=-100_000_000, le=1_000_000_000_000)
    billable: bool = True
    profile: SalesLineProfile
    estimated_cost_origin: Origin
    taxes: list[WorkTaxComponent] = Field(default_factory=list, max_length=200)

    @model_validator(mode='after')
    def consistency(self):
        if self.item_id != self.profile.item.id:
            raise ValueError('item_id disagrees with captured item')
        unit = self.profile.unit
        if self.unit_id != (unit.id if unit else None) or self.unit_factor_nanounits != (unit.factor_nanounits if unit else 1_000_000_000):
            raise ValueError('unit disagrees with captured unit')
        if self.base_quantity_microunits != base_quantity(self.quantity_microunits, self.unit_factor_nanounits):
            raise ValueError('base quantity disagrees with selected quantity and factor')
        if self.completed_quantity_microunits > self.quantity_microunits:
            raise ValueError('completed quantity cannot exceed ordered quantity')
        if self.pricing_basis == 'amount':
            if self.unit_price_minor_units is not None:
                raise ValueError('amount pricing has no unit price')
        elif self.unit_price_minor_units is None or self.net_minor_units != extension(self.quantity_microunits, self.unit_price_minor_units):
            raise ValueError('net disagrees with quantity and unit price')
        if self.pricing_basis == 'markup':
            if self.markup_percent_millionths is None or self.estimated_unit_cost_minor_units is None:
                raise ValueError('markup requires known cost and percentage')
            if self.unit_price_minor_units != adjusted_price(self.estimated_unit_cost_minor_units, self.markup_percent_millionths):
                raise ValueError('markup price disagrees with cost and percentage')
        elif self.markup_percent_millionths is not None:
            raise ValueError('only markup pricing captures a markup percentage')
        cost = self.estimated_unit_cost_minor_units
        if self.estimated_cost_minor_units != (extension(self.quantity_microunits, cost) if cost is not None else None):
            raise ValueError('estimated cost disagrees with quantity and unit cost')
        if any(component.taxable_minor_units != self.net_minor_units for component in self.taxes):
            raise ValueError('taxable amount disagrees with authoritative net')
        if self.tax_minor_units != sum(component.tax_minor_units for component in self.taxes):
            raise ValueError('tax disagrees with captured components')
        if self.gross_minor_units != self.net_minor_units + self.tax_minor_units:
            raise ValueError('gross disagrees with net and tax')
        return self
