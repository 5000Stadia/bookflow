"""Inputs and outputs of the `prepayment` commands and `report customer-prepayments` (R124)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_DATE = r'^\d{4}-\d{2}-\d{2}$'
_SELECTOR = dict(min_length=1, max_length=1000)
_DOCUMENT = ('The customer-work document the money is held on: its id, or its kind and number such as '
             '"estimate 1001" or "work-order 1002" (a bare number works when only one document carries it). '
             'An estimate must be accepted.')


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Money(_Strict):
    minor_units: int
    currency: str
    amount: str


class PrepaymentReceiveInput(_Strict):
    customer: str | None = Field(default=None, description='Customer or job the money is held for, when it is not for one document: ID or full name.', **_SELECTOR)
    document: str | None = Field(default=None, description=_DOCUMENT + ' The customer is the document\'s.', **_SELECTOR)
    amount: str = Field(min_length=1, max_length=40, description='Amount received, in the home currency, such as "200.00".')
    date: str | None = Field(default=None, pattern=_DATE, description='Date received. Defaults to today.')
    payment_method: str | None = Field(default=None, description='How they paid: payment method ID or name. Defaults as a sales receipt does.', **_SELECTOR)
    reference: str | None = Field(default=None, max_length=128, description='Check number or other payment reference.')
    deposit_to: str | None = Field(default=None, description='Where the money goes: Undeposited Funds (the default, banked later with `deposit post`) or a bank account.', **_SELECTOR)
    class_id: str | None = Field(default=None, description='Class for the receipt.', **_SELECTOR)
    memo: str | None = Field(default=None, max_length=2000, description='Memo on the receipt.')
    number: str | None = Field(default=None, min_length=1, max_length=64, description='Receipt number. Defaults to the next sales receipt number.')


class PrepaymentAttachInput(_Strict):
    receipt: str = Field(description='The prepayment receipt: sales receipt ID or number.', **_SELECTOR)
    document: str | None = Field(default=None, description=_DOCUMENT + ' Omit it to hold the money for the customer again.', **_SELECTOR)


class _Source(_Strict):
    document: str | None = Field(default=None, description='Draw from the money held on this customer-work document (and, for a work order, on the estimate it came from): '
                                 'its id or kind and number.', **_SELECTOR)
    customer: str | None = Field(default=None, description='Draw from the money held for this customer or job and not on any document: ID or full name.', **_SELECTOR)
    receipts: list[str] | None = Field(default=None, min_length=1, max_length=50, description='Draw from exactly these prepayment receipts, in this order: IDs or numbers.')


class PrepaymentApplyInput(_Source):
    invoice: str = Field(description='Invoice the held money pays: ID or number.', **_SELECTOR)
    amount: str | None = Field(default=None, min_length=1, max_length=40, description='Amount to use. Defaults to the lesser of what is held and what the invoice still owes.')
    date: str | None = Field(default=None, pattern=_DATE, description='Date of the draw and its application. Defaults to the invoice date, or the latest receipt drawn when that is later.')


class PrepaymentUnapplyInput(_Strict):
    draw: str = Field(description='The drawing credit memo, as `prepayment show` or the invoice lists it: ID or number.', **_SELECTOR)


class PrepaymentRefundInput(_Source):
    amount: str | None = Field(default=None, min_length=1, max_length=40, description='Amount to pay back. Defaults to everything held at the source.')
    date: str | None = Field(default=None, pattern=_DATE, description='Date of the refund. Defaults to today.')
    bank_account: str = Field(description='Bank account the refund is paid from: ID or name.', **_SELECTOR)
    payment_method: str = Field(description='How it is paid back: payment method ID or name, such as Check.', **_SELECTOR)
    check_number: str | None = Field(default=None, max_length=64, description='Check number, when paid by check.')
    memo: str | None = Field(default=None, max_length=2000, description='Memo on the refund.')


class PrepaymentShowInput(_Strict):
    receipt: str | None = Field(default=None, description='One prepayment receipt: sales receipt ID or number.', **_SELECTOR)
    document: str | None = Field(default=None, description='Everything held on a customer-work document: id or kind and number.', **_SELECTOR)
    customer: str | None = Field(default=None, description='Everything held for a customer or job, on documents or not: ID or full name.', **_SELECTOR)


class PrepaymentQueryInput(_Strict):
    customer: str | None = Field(default=None, description='Only this customer or job.', **_SELECTOR)
    document: str | None = Field(default=None, description='Only receipts held on this customer-work document.', **_SELECTOR)
    held_only: bool = Field(default=False, description='Only receipts with money still held.')
    date_from: str | None = Field(default=None, pattern=_DATE)
    date_to: str | None = Field(default=None, pattern=_DATE)
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=200, description='next_cursor of the previous page.')


class CustomerPrepaymentsReportInput(_Strict):
    as_of: str | None = Field(default=None, pattern=_DATE, description='Report date. Defaults to today.')
    customer: str | None = Field(default=None, description='Only this customer or job.', **_SELECTOR)
    include_drawn: bool = Field(default=False, description='Also list receipts fully drawn by the date.')


class DocumentRef(_Strict):
    id: str
    kind: str
    number: str
    status: str


class DocumentNumber(_Strict):
    id: str
    number: str
    type: str
    amount: Money
    date: str


class DrawRow(_Strict):
    draw_id: str
    credit_memo_id: str
    credit_memo_number: str
    date: str
    amount: Money
    receipt_id: str
    receipt_number: str
    document: DocumentRef | None
    invoice_id: str | None
    invoice_number: str | None
    refund_id: str | None
    refund_number: str | None
    status: Literal['posted', 'voided']


class ReceiptRow(_Strict):
    receipt_id: str
    receipt_number: str
    date: str
    customer_id: str
    customer: str
    document: DocumentRef | None = Field(description='The document the money is held on now; null when it is held for the customer.')
    amount: Money
    drawn: Money
    held: Money
    status: Literal['posted', 'voided']


class PrepaymentWriteOutput(_Strict):
    action: Literal['receive', 'attach', 'apply', 'unapply', 'refund']
    customer_id: str
    customer: str
    document: DocumentRef | None = None
    receipt: DocumentNumber | None = Field(default=None, description='The sales receipt that took the money (receive).')
    credit_memo: DocumentNumber | None = Field(default=None, description='The credit memo that drew the money (apply, refund) or was voided (unapply).')
    invoice: DocumentNumber | None = Field(default=None, description='The invoice the money paid (apply) or stopped paying (unapply).')
    refund: DocumentNumber | None = Field(default=None, description='The customer refund that paid the money back (refund) or was voided (unapply).')
    amount: Money = Field(description='What moved.')
    draws: list[DrawRow] = Field(default_factory=list, description='Each receipt drawn from, oldest first, and how much.')
    balance_due: Money | None = Field(default=None, description='What the invoice still owes afterwards (apply, unapply).')
    held_after: Money = Field(description='What is still held at the source afterwards (the document, customer or receipts named).')
    set_up: list[str] = Field(default_factory=list, description='Records this write creates first: the Customer Prepayments account and item, on first use.')
    warnings: list[str] = Field(default_factory=list)
    summary: str


class PrepaymentShowOutput(_Strict):
    customer_id: str | None
    customer: str | None
    document: DocumentRef | None
    held: Money
    receipts: list[ReceiptRow]
    draws: list[DrawRow]
    next: list[str] = Field(default_factory=list, description='Commands that use or move what is held.')


class PrepaymentPageOutput(_Strict):
    items: list[ReceiptRow]
    count: int
    next_cursor: str | None


class PrepaymentReportRow(_Strict):
    customer_id: str
    customer: str
    document: DocumentRef | None
    receipt_id: str
    receipt_number: str
    kind: Literal['receipt', 'draw']
    date: str
    number: str = Field(description='The receipt\'s number, or the drawing credit memo\'s.')
    applied_to: str | None = Field(description='For a draw: "invoice N" or "refund N".')
    amount: Money = Field(description='Positive for a receipt, negative for a draw.')
    held: Money = Field(description='Running amount held for the customer after this row.')


class PrepaymentReportCustomer(_Strict):
    customer_id: str
    customer: str
    held: Money


class CustomerPrepaymentsReportOutput(_Strict):
    as_of: str
    account_id: str | None
    account: str | None
    rows: list[PrepaymentReportRow]
    customers: list[PrepaymentReportCustomer]
    total_held: Money
    account_balance: Money = Field(description='The Customer Prepayments account on the general ledger at the date; always equal to total_held.')
