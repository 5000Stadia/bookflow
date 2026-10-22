"""What moving a customer's credit from one job to another takes, and what it gives back.

``customer-credit transfer`` is the anchor's workaround made one step: the job that had the credit
is charged the amount, its own credit pays that charge, and the job that gets the credit is given
a credit memo for it. ``customer-credit transfer-void`` takes the whole of it back.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from bookflow.company.credit_settlement_models import CreditApplicationItem
from bookflow.company.journal_models import _Date, _Input, _Selector
from bookflow.company.journal_outputs import JournalMoneyOutput
from bookflow.core.models import WriteOutput

TransferKey = Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$')]
Fingerprint = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]


class CreditTransferInput(_Input):
    from_customer: _Selector = Field(description=(
        'The customer or job that has the credit now (an unapplied payment or an open credit memo).'))
    to_customer: _Selector = Field(description=(
        'The customer or job of the same customer that gets the credit: another job, the parent, or a job of the parent.'))
    amount: str = Field(description='How much credit to move, as a decimal string more than zero, such as "120.00".')
    date: _Date = Field(description='Date of the transfer; on or after the credit it draws on, in an open period.')
    operation_key: TransferKey = Field(description=(
        'A unique key for this transfer; asking again with it returns the first result instead of moving the credit '
        'twice. At most 100 characters.'))
    credit_memo: _Selector | None = Field(default=None, description=(
        'Draw the credit from this credit memo only. Omit both this and `payment` to draw on the source\'s '
        'credits oldest first.'))
    payment: _Selector | None = Field(default=None, description=(
        'Draw the credit from this receipt\'s unapplied money only.'))
    apply_to: list[CreditApplicationItem] = Field(default_factory=list, max_length=200, description=(
        'Invoices of `to_customer` to use the moved credit on at once, as `customer-credit apply` takes them '
        '(invoice, expected_version, optional amount; without an amount an invoice takes what it still owes). '
        'Omit to leave the credit standing on the target to use later.'))
    memo: str | None = Field(default=None, max_length=2000, description='Memo on both documents; a line naming the two jobs by default.')
    expected_facts_fingerprint: Fingerprint | None = Field(default=None, description=(
        '`facts_fingerprint` from a dry run; the write is refused with E_PREVIEW_STALE if anything it depends on changed since.'))


class CreditTransferVoidInput(_Input):
    transfer: _Selector = Field(description='The transfer id `customer-credit transfer` returned (`transfer_id`).')


class TransferSource(_Input):
    type: Literal['credit_memo', 'payment']
    id: str
    number: str
    amount: JournalMoneyOutput = Field(description='Credit drawn from this document for the transfer.')


class TransferDocument(_Input):
    id: str | None
    number: str
    type: Literal['invoice', 'credit_memo']
    customer: str
    amount: JournalMoneyOutput


class TransferApplied(_Input):
    invoice_id: str
    number: str
    amount: JournalMoneyOutput
    still_due: JournalMoneyOutput


class CreditTransferOutput(WriteOutput):
    changed: bool = True
    idempotent_replay: bool = False
    transfer_id: str | None = Field(description='Id of the transfer; null on a dry run.')
    operation_key: str
    status: Literal['posted', 'voided', 'partly_voided'] = Field(description='posted while both documents stand; voided once both are')
    date: str
    from_customer: str
    to_customer: str
    amount: JournalMoneyOutput
    sources: list[TransferSource] = Field(description='The credits drawn on the source, oldest first.')
    charge: TransferDocument = Field(description='The charge on the source, paid in full by its credit.')
    credit_memo: TransferDocument = Field(description='The credit the target now has.')
    applied: list[TransferApplied] = Field(default_factory=list, description='Target invoices the moved credit was used on.')
    clearing_account: str = Field(description='Account both documents post through; it nets to zero.')
    facts_fingerprint: str
    summary: str


class CreditTransferVoidOutput(WriteOutput):
    changed: bool = True
    transfer_id: str
    status: Literal['voided']
    reopened: list[TransferApplied] = Field(default_factory=list, description=(
        'Target invoices the moved credit had paid, owed again.'))
    summary: str
