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
from typing import Literal

from pydantic import Field

from bookflow.company.journal_models import MoneyInput, _Date, _Input, _Selector
from bookflow.company.journal_outputs import JournalMoneyOutput, JournalWriteOutput

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


class TransferWriteOutput(JournalWriteOutput):
    document: TransferSummary
