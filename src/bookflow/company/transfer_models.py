"""Moving money between two accounts the company already owns.

This is the document surface over the account register, exactly as the money-out documents
are. No accounting decision is taken here: every transfer posts through
``company/registers.py`` and the journal writer the way ``register post`` does, and what
this module adds is the vocabulary. A transfer has an account it comes out of and an account
it goes into, and neither of those is spelled ``direction: increase``.

**No line grid.** A transfer has exactly two legs and they are the same amount, so the
document is a header and nothing else. A one-row grid would ask a person to type the amount
twice and would leave room for a second row that could never balance.

**One rule decides both signs: credit the account it comes from, debit the account it goes
into.** That is what a transfer is, and it stays true when a side is credit-normal. Bank to
bank: the first is credited so its balance falls, the second debited so its balance rises.
Bank to credit card: the bank is credited and the card is debited, and a debit to a
credit-normal account is what you owe going down -- which is what paying the card off does.
Card to bank is the same rule read the other way: a cash advance raises what is owed.

**Neither leg touches profit.** Both accounts are on the balance sheet, so nothing a
transfer posts can appear on a profit and loss. That is not an accident of the accounts a
person happens to choose; ``ELIGIBLE`` in ``company/transfers.py`` is what makes it
structural.
"""
from typing import Literal, Self

from pydantic import Field, model_serializer, model_validator

from bookflow.company.journal_models import (
    MoneyInput, _Date, _Input, _Selector, _Version,
)
from bookflow.company.journal_outputs import (
    JournalMoneyOutput, JournalOutput, JournalRevisionSummaryOutput, JournalSummaryOutput,
    JournalWriteOutput,
)

# Which way the account a transfer comes out of has to move for the credit to land on it.
# A debit-normal account (a bank, any asset) is credited by decreasing; a credit-normal one
# (a card, a loan, equity) is credited by increasing what it stands for. The account the
# money goes into takes the opposite of whichever this is.
CREDITED_BY = {'debit': 'decrease', 'credit': 'increase'}
DEBITED_BY = {'debit': 'increase', 'credit': 'decrease'}


class TransferPostInput(_Input):
    from_account: _Selector
    to_account: _Selector
    date: _Date
    amount: str | MoneyInput
    memo: str | None = Field(default=None, max_length=2000)


class TransferUpdateInput(_Input):
    """Correct a transfer: each field optional, each supplied field replacing what was captured.

    Changing ``to_account`` is the interesting one and it is an ordinary correction here: the
    old accounting is reversed at the transfer's own date and replaced in full, so the money
    leaves the account it went into and arrives in the new one on the same day rather than on
    two.
    """

    transfer: _Selector
    expected_version: _Version | None = None
    from_account: _Selector | None = None
    to_account: _Selector | None = None
    date: _Date | None = None
    amount: str | MoneyInput | None = None
    memo: str | None = Field(default=None, max_length=2000)

    # No `number`: a transfer takes none of its own when it is entered, so a correction has
    # none to change. The number it carries is the one the journal family allocated it.

    @model_validator(mode='after')
    def required_values(self) -> Self:
        for field in ('from_account', 'to_account', 'date', 'amount'):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f'{field} cannot be cleared')
        return self

    @model_serializer(mode='wrap')
    def only_supplied(self, handler):
        values = handler(self)
        if 'memo' not in self.model_fields_set:
            values.pop('memo', None)
        return values


class TransferVoidInput(_Input):
    transfer: _Selector
    expected_version: _Version | None = None


class TransferShowInput(_Input):
    transfer: _Selector
    revision_number: _Version | None = None


class TransferPageInput(_Input):
    limit: int = Field(default=50, strict=True, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=8192)


class TransferQueryInput(TransferPageInput):
    """Which transfers to page. Every filter is ANDed; omit them all to page the lot."""

    date_from: _Date | None = None
    date_to: _Date | None = None
    status: Literal['posted', 'voided'] | None = None
    number: str | None = Field(default=None, max_length=64)
    account: _Selector | None = Field(default=None,
        description='Either end: transfers that came out of this account or went into it.')
    from_account: _Selector | None = None
    to_account: _Selector | None = None
    query: str | None = Field(default=None, max_length=2000)
    direction: Literal['asc', 'desc'] = Field(default='asc',
        description='Order of the accounting-date then stable-id page: asc pages the oldest '
                    'transfer first, desc the most recent first. A cursor belongs to the '
                    'direction that minted it; changing direction rejects it, so restart '
                    'without a cursor.')

    @model_validator(mode='after')
    def ordered_dates(self) -> Self:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError('date_from cannot follow date_to')
        return self


class TransferHistoryInput(TransferPageInput):
    transfer: _Selector


class TransferLeg(_Input):
    """One side of the transfer, as the server resolved it."""

    account_id: str
    name: str
    type: str
    normal_balance: Literal['debit', 'credit']
    side: Literal['debit', 'credit']
    effect: Literal['increase', 'decrease']


class TransferSummary(_Input):
    """What the server computed, for the document's own footer. Never recomputed anywhere else."""

    kind: Literal['transfer']
    currency: str
    amount: JournalMoneyOutput
    from_account: TransferLeg
    to_account: TransferLeg


class TransferOutput(JournalOutput):
    document: TransferSummary


class TransferWriteOutput(JournalWriteOutput):
    document: TransferSummary


class TransferSummaryOutput(JournalSummaryOutput):
    """One row of the transfer list: the journal header with the transfer's own footer."""

    document: TransferSummary


class TransferRevisionSummaryOutput(JournalRevisionSummaryOutput):
    """One revision in the transfer's history, with the two ends it named at the time."""

    document: TransferSummary


class TransferPageOutput(_Input):
    items: list[TransferSummaryOutput]
    count: int
    has_more: bool
    next_cursor: str | None
    audit_watermark: int


class TransferHistoryOutput(_Input):
    id: str
    version: int
    current_revision_id: str
    number: str
    status: Literal['posted', 'voided']
    items: list[TransferRevisionSummaryOutput]
    count: int
    has_more: bool
    next_cursor: str | None
    audit_watermark: int
