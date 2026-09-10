"""Money-out documents: a check written on a bank account, a charge on a credit card.

These are the document surface over the account register. No accounting decision is taken
here. Every posting goes through ``company/registers.py`` and the journal writer, exactly the
way ``register post`` does; what this module adds is the vocabulary. A check has a bank
account, someone it is paid to, a check number and an expenses grid, and money leaving the
account is not spelled ``direction: decrease``.

**Two nouns, not one.** A check and a credit card charge differ in what funds them — an asset
that goes down against a liability that goes up — in whether a check number applies at all,
and in every word on the form. One noun with a payment-type flag would have had to say
"the check number is only accepted when payment_type is check" in a validator instead of
saying it in the shape, and would have put a discriminator in front of a bookkeeper who
already knows which document they are writing.

**Room for an Items tab.** The line collection is named ``expenses``, not ``lines``, so the
second line kind this document is going to grow — items received on a purchase — arrives as
a sibling collection beside it rather than as a rewrite of this one. See
``design/architecture.md`` for what attaches where.
"""
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from bookflow.company.custom_fields import CustomFieldKindExpectations, CustomFieldValuePatch
from bookflow.company.journal_models import (
    MoneyInput, _Date, _Input, _Number, _Selector,
)
from bookflow.company.journal_outputs import JournalMoneyOutput, JournalWriteOutput

# What funds each document, and which way that account moves when money goes out. A bank
# account is debit-normal so paying decreases it; a card is credit-normal so charging
# increases what is owed. Both credit the funding account and debit the expenses.
FUNDING_TYPE = {'check': 'bank', 'card-charge': 'credit_card'}
DIRECTION = {'bank': 'decrease', 'credit_card': 'increase'}

DOCUMENT_KIND = {'check': 'check', 'card-charge': 'card_charge'}


class CheckParty(_Input):
    """Who the money went to: a name from any of the four name lists."""

    name_type: Literal['vendor', 'customer', 'employee', 'other_name'] = 'vendor'
    name_id: _Selector


class ExpenseLine(_Input):
    """One row of the Expenses grid: what the money was spent on.

    A class typed on the line is the line's class -- that is what choosing one in the Class
    column means, and refusing it as a mode mismatch would be a footgun rather than a check.
    ``class_mode`` exists for the one thing the column cannot say: ``none`` leaves this line
    unclassified while the document itself carries a class.
    """

    account: _Selector
    amount: str | MoneyInput
    memo: str | None = Field(default=None, max_length=2000)
    class_id: _Selector | None = None
    class_mode: Literal['inherit', 'none', 'value'] = 'inherit'
    party: CheckParty | None = None

    @model_validator(mode='after')
    def explicit_class(self) -> Self:
        if self.class_mode == 'value' and self.class_id is None:
            raise ValueError('class_id is required when class_mode is value')
        if self.class_id is not None and self.class_mode != 'value':
            if self.class_mode == 'none':
                raise ValueError('class_mode none leaves the line unclassified; remove class_id')
            self.class_mode = 'value'
        return self


# One to 199 rows, the same bound the register puts on a split, because these are that split.
Expenses = Annotated[list[ExpenseLine], Field(min_length=1, max_length=199)]


class _MoneyOut(_Input):
    account: _Selector
    pay_to: CheckParty | None = None
    date: _Date
    amount: str | MoneyInput
    memo: str | None = Field(default=None, max_length=2000)
    class_id: _Selector | None = None
    expenses: Expenses
    custom_field_kinds: CustomFieldKindExpectations = Field(
        default_factory=lambda: CustomFieldKindExpectations({}))
    custom_fields: CustomFieldValuePatch = Field(
        default_factory=lambda: CustomFieldValuePatch({}))


class CheckPostInput(_MoneyOut):
    number: _Number | None = None


class CardChargePostInput(_MoneyOut):
    """A card charge carries no check number; the card statement carries the reference."""


class MoneyOutSummary(_Input):
    """What the server computed, for the document's own footer. Never recomputed anywhere else."""

    kind: Literal['check', 'card_charge']
    account_id: str
    funding: Literal['bank', 'credit_card']
    currency: str
    amount: JournalMoneyOutput
    expense_total: JournalMoneyOutput
    expense_lines: int


class MoneyOutWriteOutput(JournalWriteOutput):
    document: MoneyOutSummary
