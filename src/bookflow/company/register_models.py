"""Exact, bounded inputs and receipts for domestic account registers."""
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from bookflow.company.journal_models import (
    _Input, _Date, _Number, _Selector, _Version, MoneyInput,
)
from bookflow.company.journal_outputs import JournalMoneyOutput, JournalWriteOutput
from bookflow.company.custom_fields import CustomFieldValuePatch, CustomFieldKindExpectations

Direction = Literal['increase', 'decrease']
JournalMoneyInput = MoneyInput


class RegisterParty(_Input):
    name_type: Literal['customer', 'vendor', 'employee', 'other_name']
    name_id: _Selector


class RegisterAllocation(_Input):
    line_id: _Selector | None = None
    account: _Selector
    amount: str | JournalMoneyInput
    direction: Direction | None = None
    memo: str | None = Field(default=None, max_length=2000)
    party: RegisterParty | None = None
    class_mode: Literal['inherit', 'none', 'value'] = 'inherit'
    class_id: _Selector | None = None

    @model_validator(mode='after')
    def explicit_class(self) -> Self:
        if (self.class_mode == 'value') != (self.class_id is not None):
            raise ValueError('class_id is required only when class_mode is value')
        return self


_Allocations = Annotated[list[RegisterAllocation], Field(min_length=1, max_length=199)]


class _RegisterShape(_Input):
    custom_field_kinds: CustomFieldKindExpectations = Field(
        default_factory=lambda: CustomFieldKindExpectations({}),
        description="Optional captured kinds for supplied non-null custom values. A current kind mismatch rejects the write without reinterpreting a draft.",
    )
    custom_fields: CustomFieldValuePatch = Field(
        default_factory=lambda: CustomFieldValuePatch({}),
        description="Journal header custom-field patch by definition ID. Omitted keys preserve values on update; null clears an optional value. Numbers are decimal strings.",
    )
    account: _Selector
    date: _Date
    number: _Number | None = None
    memo: str | None = Field(default=None, max_length=2000)
    payee: RegisterParty | None = None
    direction: Direction
    amount: str | JournalMoneyInput
    category: _Selector | None = None
    allocations: _Allocations | None = None
    class_id: _Selector | None = None

    @model_validator(mode='after')
    def category_or_allocations(self) -> Self:
        if (self.category is None) == (self.allocations is None):
            raise ValueError('supply exactly one of category or allocations')
        return self


class RegisterPostInput(_RegisterShape):
    @model_validator(mode='after')
    def forbid_line_ids(self) -> Self:
        if any('line_id' in a.model_fields_set for a in self.allocations or []):
            raise ValueError('line_id cannot be supplied when posting')
        return self


class RegisterUpdateInput(_RegisterShape):
    journal: _Selector
    expected_version: _Version
    selected_line_id: _Selector
    category_line_id: _Selector | None = None

    @model_validator(mode='after')
    def category_identity(self) -> Self:
        if self.category_line_id is not None and self.category is None:
            raise ValueError('category_line_id is only accepted with category')
        return self


class RegisterCalculateInput(_Input):
    account: _Selector
    direction: Direction
    allocations: _Allocations


class RegisterCalculateOutput(_Input):
    amount: JournalMoneyOutput
    currency: str
    direction: Direction


class RegisterReceipt(_Input):
    account_id: str
    normal_balance: Literal['debit', 'credit']
    direction: Direction
    amount: JournalMoneyOutput


class RegisterWriteOutput(JournalWriteOutput):
    receipt: RegisterReceipt
