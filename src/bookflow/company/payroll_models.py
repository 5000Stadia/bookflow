"""Private manual paycheck calculator contracts. All money is USD integer cents."""
from typing import Literal
from pydantic import BaseModel, Field, model_validator
from bookflow.core.models import StrictModel
from bookflow.company.cutover_models import IsoDate


class EmptyInput(StrictModel):
    pass


class SetupInput(StrictModel):
    enabled: bool
    expected_version: int = Field(ge=0)
    operation_key: str = Field(min_length=1, max_length=128)
    wage_account: str | None = None
    employer_tax_account: str | None = None
    net_pay_account: str | None = None


class SettingsOutput(BaseModel):
    enabled: bool = False
    version: int = 0
    currency: str = 'USD'
    wage_account: str | None = None
    employer_tax_account: str | None = None
    net_pay_account: str | None = None


class Earning(StrictModel):
    code: str = Field(min_length=1, max_length=60)
    amount_minor_units: int | None = Field(None, strict=True, ge=0, le=10**12)
    quantity: str | None = Field(None, pattern=r'^\d{1,10}(\.\d{1,6})?$')
    rate: str | None = Field(None, pattern=r'^\d{1,10}(\.\d{1,6})?$')

    @model_validator(mode='after')
    def method(self):
        if self.amount_minor_units is not None:
            if self.quantity is not None or self.rate is not None:
                raise ValueError('Choose fixed cents or quantity and rate.')
        elif self.quantity is None or self.rate is None:
            raise ValueError('Supply fixed cents or both quantity and rate.')
        return self


class FixedItem(StrictModel):
    code: str = Field(min_length=1, max_length=60)
    kind: Literal['employee_tax', 'employee_deduction', 'employer_tax', 'employer_contribution']
    amount_minor_units: int = Field(strict=True, ge=0, le=10**12)
    liability_account: str
    payee: str = Field(min_length=1, max_length=200)
    source_note: str = Field(min_length=1, max_length=500)


class DraftInput(StrictModel):
    draft: str | None = None
    expected_version: int = Field(0, ge=0)
    operation_key: str = Field(min_length=1, max_length=128)
    employee: str
    period_start: IsoDate
    period_end: IsoDate
    pay_date: IsoDate | None = None
    currency: Literal['USD'] = 'USD'
    earnings: list[Earning] = Field(min_length=1, max_length=100)
    items: list[FixedItem] = Field(default_factory=list, max_length=100)
    taxes_confirmed: bool = Field(description='Explicit confirmation that the entered fixed items are complete, including an empty list.')


class Selector(StrictModel):
    draft: str


class PrepareInput(Selector):
    expected_version: int = Field(ge=1)
    operation_key: str = Field(min_length=1, max_length=128)


class ListInput(StrictModel):
    limit: int = Field(50, ge=1, le=200)


class TraceLine(BaseModel):
    code: str
    kind: str
    method: str
    quantity: str | None = None
    rate: str | None = None
    exact_minor_units: str
    amount_minor_units: int
    rounding: str = 'half_up_per_line'
    source_note: str | None = None


class Calculation(BaseModel):
    currency: str = 'USD'
    gross_minor_units: int
    employee_tax_minor_units: int
    employee_deduction_minor_units: int
    employer_cost_minor_units: int
    net_minor_units: int
    total_expense_minor_units: int
    debit_minor_units: int
    credit_minor_units: int
    trace: list[TraceLine]
    ledger_effect: bool = False


class DraftOutput(BaseModel):
    id: str
    version: int
    state: str
    employee: str
    period_start: str
    period_end: str
    pay_date: str | None
    currency: str
    earnings: list[Earning]
    items: list[FixedItem]
    taxes_confirmed: bool
    settings_version: int
    calculation: Calculation | None = None


class ListOutput(BaseModel):
    rows: list[DraftOutput]
