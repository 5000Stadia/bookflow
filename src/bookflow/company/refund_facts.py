"""What a customer refund captured, frozen at the moment it was written.

The same discipline as every other document: a refund is read years after the customer was
renamed, the bank account was renumbered and the method was deactivated, so what it shows is
what it captured. ``Reference``, ``Account`` and ``Origin`` are the ledger's own captured-fact
shapes and ``PaymentMethod`` is the bill payment's, because a check written back to a customer
carries a check number for exactly the same reason a check to a vendor does.

``sources`` is the part nothing else has: a refund is only legitimate because some named
capacity stood behind it -- a credit memo, or the part of a payment that settled no invoice --
and the row that says which document, for how much, is what stops that same money being
refunded and then applied as well.
"""
from __future__ import annotations

from typing import Self

from pydantic import model_validator

from bookflow.company.bill_facts import Account, Origin, Reference
from bookflow.company.bill_payment_facts import PaymentMethod
from bookflow.company.sales_models import StrictModel

__all__ = ['Account', 'Origin', 'Reference', 'PaymentMethod', 'Customer', 'RefundSource',
           'CustomerRefundProfile']


class Customer(Reference):
    """The customer as the refund captured them."""

    company_name: str | None = None
    email: str | None = None
    phone: str | None = None
    account_number: str | None = None


class RefundSource(StrictModel):
    """One document this refund paid out, and how much of it went.

    Either a credit memo or a payment, never both and never neither -- the same exactly-one
    rule ``customer_refund_consumptions`` stores. A source written before a payment overage
    could be refunded carries only the credit fields, and reads back unchanged because the
    payment fields default to absent rather than being backfilled.
    """

    credit_memo_id: str | None = None
    credit_memo_number: str | None = None
    credit_memo_date: str | None = None
    credit_source_key_id: str | None = None
    payment_id: str | None = None
    payment_number: str | None = None
    payment_date: str | None = None
    payment_source_key_id: str | None = None
    amount_minor_units: int
    # What that capacity was worth before this refund took its share. Captured rather than
    # recomputed on read, so the document says what the books said when it was written.
    available_minor_units: int

    @model_validator(mode='after')
    def one_source(self) -> Self:
        credit = (self.credit_memo_id, self.credit_memo_number, self.credit_memo_date,
                  self.credit_source_key_id)
        payment = (self.payment_id, self.payment_number, self.payment_date,
                   self.payment_source_key_id)
        if all(value is not None for value in credit) and not any(payment):
            return self
        if all(value is not None for value in payment) and not any(credit):
            return self
        raise ValueError('a refund source names one whole credit memo or one whole payment')

    @property
    def document_id(self) -> str:
        """Whichever document supplied this capacity."""
        return self.credit_memo_id or self.payment_id

    @property
    def document_number(self) -> str:
        return self.credit_memo_number or self.payment_number

    @property
    def source_key_id(self) -> str:
        return self.credit_source_key_id or self.payment_source_key_id


class CustomerRefundProfile(StrictModel):
    """The header of a refund revision: who was paid back, out of what, and against what."""

    customer: Customer
    ar_account: Account
    funding_account: Account
    payment_method: PaymentMethod
    check_number: str | None = None
    reference: str | None = None
    amount_minor_units: int
    currency: str
    sources: list[RefundSource]
    origins: dict[str, Origin] = {}
