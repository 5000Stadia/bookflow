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


# A percentage rate is integer millionths of one percent: 10% is 10_000_000, 0.5% is 500_000.
RATE_SCALE = 100_000_000  # millionths of a percent in one whole


class FixedItem(StrictModel):
    """One owner-entered tax, deduction or employer cost: fixed cents, or a flat percentage of named earnings.

    Increment 1 shipped only fixed cents; `method`, `rate_millionths` and `base` default to that shape, so
    a retry of an older request hashes exactly as it did. No rate is supplied by the product.
    """
    code: str = Field(min_length=1, max_length=60)
    kind: Literal['employee_tax', 'employee_deduction', 'employer_tax', 'employer_contribution']
    method: Literal['fixed', 'percent'] = 'fixed'
    amount_minor_units: int | None = Field(None, strict=True, ge=0, le=10**12,
        description='Fixed method only: the owner-calculated amount in cents (explicit zero allowed).')
    rate_millionths: int | None = Field(None, strict=True, ge=0, le=RATE_SCALE,
        description='Percent method only: the owner-entered rate in millionths of a percent (10% = 10000000).')
    base: list[str] | None = Field(None, min_length=1, max_length=100,
        description='Percent method only: the earning codes whose cents form the base; never another item.')
    liability_account: str
    payee: str = Field(min_length=1, max_length=200)
    source_note: str = Field(min_length=1, max_length=500)

    @model_validator(mode='after')
    def method_fields(self):
        if self.method == 'fixed':
            if self.amount_minor_units is None or self.rate_millionths is not None or self.base is not None:
                raise ValueError('A fixed item takes amount_minor_units only.')
        elif self.amount_minor_units is not None or self.rate_millionths is None or not self.base:
            raise ValueError('A percent item takes rate_millionths and base, not amount_minor_units.')
        elif len(set(self.base)) != len(self.base):
            raise ValueError('Name each base earning once.')
        return self


def canonical_items(dumped):
    """Request hashing that ignores the increment-2 item defaults, so increment-1 retries still match."""
    for item in dumped.get('items') or ():
        if item.get('method') == 'fixed':
            item.pop('method')
            if item.get('rate_millionths') is None:
                item.pop('rate_millionths', None)
            if item.get('base') is None:
                item.pop('base', None)
    return dumped


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
    rate_millionths: int | None = None
    base_minor_units: int | None = None


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
    paycheck: str | None = Field(None, description='The paycheck posted from this draft, once posted.')
    directive: str | None = Field(None, description='Private: the directive the latest revision was saved under.')


class ListOutput(BaseModel):
    rows: list[DraftOutput]


# ---------------------------------------------------------------- increment 2: posted paychecks

class NetCheck(StrictModel):
    """The one check that settles a paycheck's whole net pay, through the ordinary check machinery."""
    account: str = Field(description='Bank account the check is drawn on.')
    date: IsoDate | None = Field(None, description='Payment date; defaults to the pay date and may not precede it.')
    number: str | None = Field(None, min_length=1, max_length=20, description="Check number; omit for the account's next.")
    to_be_printed: bool = Field(False, description='Queue the check to print; it takes its number when printed.')
    no_number: bool = Field(False, description='A bank payment with no paper check (direct deposit recorded by hand).')


class PostInput(StrictModel):
    draft: str
    expected_version: int = Field(ge=1, description='The prepared draft version being posted.')
    operation_key: str = Field(min_length=1, max_length=128)
    check_account: str | None = Field(None, description=(
        'Post and record check: the bank account of the one check that settles the whole net pay in the same '
        'transaction. Omit to post only and record the check later with payroll pay-net.'))
    check_date: IsoDate | None = Field(None, description='Check date; defaults to the pay date and may not precede it.')
    check_number: str | None = Field(None, min_length=1, max_length=20, description="Check number; omit for the account's next.")
    check_to_be_printed: bool = Field(False, description='Queue the check to print; it takes its number when printed.')
    check_no_number: bool = Field(False, description='A bank payment with no paper check.')

    @property
    def net_check(self):
        if self.check_account is None:
            return None
        return NetCheck(account=self.check_account, date=self.check_date, number=self.check_number,
                        to_be_printed=self.check_to_be_printed, no_number=self.check_no_number)


class PayNetInput(NetCheck):
    paycheck: str
    expected_version: int = Field(ge=1)
    operation_key: str = Field(min_length=1, max_length=128)


class PaycheckWriteInput(StrictModel):
    paycheck: str
    expected_version: int = Field(ge=1)
    operation_key: str = Field(min_length=1, max_length=128)


class PaycheckSelector(StrictModel):
    paycheck: str


class PaycheckListInput(StrictModel):
    limit: int = Field(50, ge=1, le=200)
    employee: str | None = None


class ComponentOut(BaseModel):
    position: int
    code: str
    kind: str
    method: str
    amount_minor_units: int
    debit_account: str | None = None
    credit_account: str | None = None
    payee: str | None = None
    rate_millionths: int | None = None
    base_minor_units: int | None = None


class NetPaymentOut(BaseModel):
    id: str
    check: str = Field(description='The check (ledger transaction) that records the payment.')
    account: str
    date: str
    amount_minor_units: int
    status: str
    check_number: str | None = None
    printed: bool = False


class YearToDate(BaseModel):
    tax_year: int
    gross_minor_units: int
    employee_tax_minor_units: int
    employee_deduction_minor_units: int
    employer_cost_minor_units: int
    net_minor_units: int


class PaycheckOutput(BaseModel):
    id: str
    version: int
    status: str
    draft: str
    draft_version: int
    employee: str
    pay_date: str
    period_start: str
    period_end: str
    currency: str = 'USD'
    journal: str = Field(description='The payroll-owned journal entry.')
    batch: str | None = Field(description='Reverse the paycheck and its net payment as one unit with `batch reverse`.')
    gross_minor_units: int
    employee_tax_minor_units: int
    employee_deduction_minor_units: int
    employer_cost_minor_units: int
    net_minor_units: int
    debit_minor_units: int
    credit_minor_units: int
    components: list[ComponentOut]
    net_status: Literal['no_payment_needed', 'payment_not_recorded', 'payment_recorded', 'voided']
    net_payment: NetPaymentOut | None = None
    year_to_date: YearToDate
    directive: str | None = Field(None, description='Private: the directive the latest write was made under.')


class PaycheckListOutput(BaseModel):
    rows: list[PaycheckOutput]
