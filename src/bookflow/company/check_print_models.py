"""Inputs and outputs of check printing: the queue, print runs, confirmation, reprints, alignment (R152)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bookflow.company.journal_models import _Version
from bookflow.company.ledger_reports import MoneyOutput
from bookflow.core.models import WriteOutput

Style = Literal['voucher', 'standard', 'wallet']
_Selector = str
Offset = int


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


_ACCOUNT = 'Bank account ID or canonical full name whose chequebook the cheques are drawn on.'
_OFFSET = ('Fine alignment in hundredths of an inch, -200 to 200: horizontal moves the whole form right '
           '(negative left), vertical moves it up the page (negative down).')


# ---------------------------------------------------------------- the queue


class CheckPrintQueueInput(_Strict):
    account: _Selector | None = Field(None, min_length=1, max_length=1000, description=(
        _ACCOUNT + ' Omit for every bank account with cheques waiting.'))


class QueuedCheck(_Strict):
    transaction_id: str
    document_type: Literal['check', 'bill_payment', 'sales_tax_payment', 'customer_refund'] = Field(description='check (Write Checks), bill_payment (Pay Bills), sales_tax_payment (Pay Sales Tax) or customer_refund (a refund by check).')
    reference: str = Field(description="The document's own reference from its series; not a check number.")
    account_id: str
    date: str
    payee_name: str | None
    amount: MoneyOutput
    memo: str | None


class PrintSettingsOutput(_Strict):
    account_id: str
    style: Style
    offset_x_hundredths: int
    offset_y_hundredths: int
    version: int | None = Field(description='Settings version; null when the account still has the defaults.')


class QueueAccount(_Strict):
    account_id: str
    account_name: str
    next_check_number: str | None = Field(description='The number Print Checks proposes as the first check number.')
    settings: PrintSettingsOutput
    checks: list[QueuedCheck]
    total: MoneyOutput


class CheckPrintQueueOutput(_Strict):
    accounts: list[QueueAccount]
    count: int = Field(description='Cheques waiting to be printed across the accounts shown.')
    awaiting_confirmation: list[str] = Field(default_factory=list, description=(
        'Print runs on these accounts still waiting for "Did checks print OK?" (check print-confirm).'))


# ---------------------------------------------------------------- a print run


class CheckPrintInput(_Strict):
    account: _Selector = Field(min_length=1, max_length=1000, description=_ACCOUNT)
    first_number: str | None = Field(None, min_length=1, max_length=18, pattern=r'^[0-9]+$', description=(
        'The number on the first check loaded in the printer; the rest print in order after it. '
        "Omit to use the account's next check number. Every number in the run must be free on this account."))
    style: Style | None = Field(None, description="Check style; omit for the account's printer setup (voucher by default).")
    checks: list[str] | None = Field(None, min_length=1, max_length=500, description=(
        'Print only these queued cheques (transaction IDs), in queue order; omit for every cheque queued on the account.'))
    checks_on_first_page: int = Field(3, ge=1, le=3, description=(
        'Standard and wallet checks: how many checks are left on a partial first sheet (1-3; 3 is a full sheet). '
        'The partial sheet goes in top edge first.'))

    @model_validator(mode='after')
    def _voucher(self):
        if self.style == 'voucher' and self.checks_on_first_page != 3:
            raise ValueError('voucher checks print one to a page, so there is no partial first sheet')
        return self


class SkippedCheck(_Strict):
    transaction_id: str
    document_type: Literal['check', 'bill_payment', 'sales_tax_payment', 'customer_refund']
    reference: str
    date: str
    payee_name: str | None
    reason: str


class PrintRunItem(_Strict):
    item_id: str
    position: int
    transaction_id: str
    document_type: Literal['check', 'bill_payment', 'sales_tax_payment', 'customer_refund']
    check_number: str
    date: str
    payee_name: str | None
    amount: MoneyOutput
    amount_in_words: str
    outcome: Literal['pending', 'printed', 'failed', 'cancelled']


class CheckPrintRunOutput(_Strict):
    id: str
    kind: Literal['batch', 'reprint']
    account_id: str
    account_name: str
    style: Style
    first_number: str
    checks_on_first_page: int
    offset_x_hundredths: int
    offset_y_hundredths: int
    status: Literal['awaiting_confirmation', 'confirmed', 'cancelled']
    created_at: str
    created_by: str
    items: list[PrintRunItem]
    total: MoneyOutput
    spoiled_numbers: list[str] = Field(default_factory=list, description='Numbers this run left on spoiled paper.')
    pdf: str = Field(description='The command that returns this run as a PDF.')
    skipped: list[SkippedCheck] = Field(default_factory=list, description=(
        'Queued cheques a run over the whole queue left waiting, with why: dated on or before the closing date.'))


class CheckPrintWriteOutput(CheckPrintRunOutput, WriteOutput):
    pass


class CheckPrintRunInput(_Strict):
    run: str = Field(min_length=1, max_length=64, description='Print run ID.')


class CheckPrintConfirmInput(_Strict):
    run: str = Field(min_length=1, max_length=64, description='Print run ID, from `check print`.')
    failed_from: str | None = Field(None, min_length=1, max_length=18, pattern=r'^[0-9]+$', description=(
        'The number of the first check that did not print correctly; it and every later check in the run go back '
        'to the queue for a fresh number, and their numbers are recorded as spoiled. Omit when every check printed.'))
    failed: list[str] | None = Field(None, min_length=1, max_length=500, description=(
        'The numbers of the checks that did not print correctly, when they are not one run to the end.'))


class CheckReprintInput(_Strict):
    check: str = Field(min_length=1, max_length=1000, description='The printed cheque: its transaction ID.')
    new_number: str | None = Field(None, min_length=1, max_length=18, pattern=r'^[0-9]+$', description=(
        'Print it on a new check with this number; the number it carried is recorded as spoiled. '
        'Omit to reprint it with the number it has.'))
    style: Style | None = None
    checks_on_first_page: int = Field(3, ge=1, le=3)
    expected_version: _Version | None = Field(None, description='Version of the cheque document, when the reprint must not race a correction.')


# ---------------------------------------------------------------- printer setup


class CheckPrintSetupInput(_Strict):
    account: _Selector = Field(min_length=1, max_length=1000, description=_ACCOUNT)
    style: Style | None = Field(None, description='Default check style for this account.')
    offset_x_hundredths: Offset | None = Field(None, ge=-200, le=200, description=_OFFSET)
    offset_y_hundredths: Offset | None = Field(None, ge=-200, le=200, description=_OFFSET)
    expected_version: _Version | None = None


class CheckPrintSetupOutput(PrintSettingsOutput, WriteOutput):
    changed: bool = True


class CheckPrintAlignmentInput(_Strict):
    account: _Selector = Field(min_length=1, max_length=1000, description=_ACCOUNT)
    style: Style | None = None
    offset_x_hundredths: Offset | None = Field(None, ge=-200, le=200, description='Try an offset before saving it; omit for the saved one.')
    offset_y_hundredths: Offset | None = Field(None, ge=-200, le=200, description='Try an offset before saving it; omit for the saved one.')
    checks_on_first_page: int = Field(3, ge=1, le=3)


class CheckPdfOutput(_Strict):
    filename: str
    media_type: Literal['application/pdf']
    pages: int
    checks: int
    content_base64: str = Field(description='The PDF, base64-encoded. The CLI saves it as `filename` in the current folder.')
