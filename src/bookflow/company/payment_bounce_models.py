"""What recording a returned customer check takes, and what it gives back.

``payment bounce`` is the anchor's Record Bounced Check. It names a deposited receipt, the date
the bank returned it, the bank's fee and, optionally, a fee to bill the customer, and it writes the
whole of it or nothing: the receipt's invoices reopen, the returned cash leaves the bank, the
bank's fee is expensed and the customer's fee becomes an open invoice.
"""
from __future__ import annotations

from typing import Annotated

from pydantic import Field, model_validator

from bookflow.company.journal_models import _Date, _Input, _Number, _Selector, _Version
from bookflow.company.journal_outputs import JournalMoneyOutput
from bookflow.core.models import WriteOutput

# Shorter than a receipt's operation key: the steps of one bounce derive their own keys from it.
BounceKey = Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$')]
Fingerprint = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]


class PaymentBounceInput(_Input):
    payment: _Selector = Field(description='The customer receipt whose check the bank returned.')
    expected_version: _Version = Field(description='Receipt version from `payment show`.')
    date: _Date = Field(description='Date the bank returned the check. The returned amount and both fees post on it.')
    operation_key: BounceKey = Field(description=(
        'A unique key for this bounce; asking again with it returns the first result instead of bouncing the '
        'check twice. The steps of the bounce derive their own keys from it, so it is at most 100 characters.'))
    bank_fee_amount: str | None = Field(default=None, description=(
        'The bank\'s fee for returning the check, as a decimal string more than zero, such as "12.00". Omit it '
        'when the bank charged none. Give `bank_fee_account` with it.'))
    bank_fee_account: _Selector | None = Field(default=None, description=(
        'Expense account the bank\'s fee is charged to, such as Bank Service Charges. Not a bank, card, '
        'receivable or income account.'))
    bank_fee_memo: str | None = Field(default=None, max_length=2000, description=(
        'Memo on the fee entry; defaults to a line naming the check.'))
    customer_fee_amount: str | None = Field(default=None, description=(
        'A fee billed to the customer for the returned check, as a decimal string more than zero, such as '
        '"35.00"; it becomes an open invoice. Omit it to bill none. Give `customer_fee_item` or '
        '`customer_fee_account` with it.'))
    customer_fee_item: _Selector | None = Field(default=None, description=(
        'Item the customer\'s fee is billed through, a service or Other Charge item with a fixed price, such as '
        'Returned Check Charge. Give this or `customer_fee_account`, not both.'))
    customer_fee_account: _Selector | None = Field(default=None, description=(
        'Income account the customer\'s fee is billed to, such as Returned Check Charges: the fee is billed '
        'through the active item that already posts to it. Give this or `customer_fee_item`, not both.'))
    customer_fee_number: _Number | None = Field(default=None, description=(
        'Invoice number for the customer\'s fee; the next invoice number when omitted.'))
    customer_fee_memo: str | None = Field(default=None, max_length=2000, description=(
        'Memo on the fee invoice; defaults to a line naming the check.'))
    bank_account: _Selector | None = Field(default=None, description=(
        'Bank account the returned amount and the bank fee leave. Defaults to the account the receipt was '
        'deposited to; give it only to correct that.'))
    expected_facts_fingerprint: Fingerprint | None = Field(default=None, description=(
        '`facts_fingerprint` from a dry run; the write is refused with E_PREVIEW_STALE if anything it depends '
        'on changed since.'))

    @model_validator(mode='after')
    def fees_are_whole(self):
        if (self.bank_fee_amount is None) != (self.bank_fee_account is None):
            raise ValueError('give bank_fee_amount and bank_fee_account together, or neither')
        if self.bank_fee_amount is None and self.bank_fee_memo is not None:
            raise ValueError('bank_fee_memo needs a bank fee')
        if self.customer_fee_amount is None and (self.customer_fee_item or self.customer_fee_account
                                                 or self.customer_fee_number or self.customer_fee_memo):
            raise ValueError('customer_fee_amount is required with the other customer_fee fields')
        if self.customer_fee_amount is not None and (self.customer_fee_item is None) == (self.customer_fee_account is None):
            raise ValueError('name the item the customer fee is billed through (customer_fee_item) or the income account '
                             'it belongs to (customer_fee_account), one of the two')
        return self


class ReopenedInvoice(_Input):
    invoice_id: str
    number: str
    reopened: JournalMoneyOutput = Field(description='What the receipt had paid on this invoice, now owed again.')
    due: JournalMoneyOutput = Field(description='What the invoice owes after the bounce.')


class BounceDocument(_Input):
    id: str
    number: str
    type: str = Field(description='customer_refund, journal_entry or invoice.')
    amount: JournalMoneyOutput
    account: str | None = Field(default=None, description='The account or item the document charges, by name.')


class PaymentBounceOutput(WriteOutput):
    changed: bool = True
    idempotent_replay: bool = False
    id: str = Field(description='The receipt.')
    version: int = Field(description='Receipt version after the bounce.')
    bounce_id: str | None = Field(description='The bounced-check record; null in a dry run.')
    bounced_on: str
    operation_key: str
    facts_fingerprint: str
    returned: JournalMoneyOutput = Field(description='Cash the bank took back out of the deposit account.')
    bank_account: str
    reopened_invoices: list[ReopenedInvoice]
    refund: BounceDocument = Field(description='The customer refund that takes the returned cash out of the bank.')
    bank_fee: BounceDocument | None = None
    customer_fee: BounceDocument | None = None
    credit_left: JournalMoneyOutput = Field(description=(
        'Credit the receipt still holds for the customer after the bounce: an early-payment discount it '
        'took stays with the customer as credit. Zero when the check was cash only.'))
    summary: str


class PaymentBounceOnReceipt(_Input):
    """What a receipt shows when its check came back."""

    bounce_id: str
    bounced_on: str
    returned: JournalMoneyOutput
    refund_id: str
    refund_number: str
    bank_fee_id: str | None
    bank_fee: JournalMoneyOutput | None
    customer_fee_invoice_id: str | None
    customer_fee: JournalMoneyOutput | None
    reason: str
    note: str = Field(description='The words a reader sees beside the receipt, "bounced on ...".')
