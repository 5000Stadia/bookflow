"""What a customer refund captured, frozen at the moment it was written.

The same discipline as every other document: a refund is read years after the customer was
renamed, the bank account was renumbered and the method was deactivated, so what it shows is
what it captured. ``Reference``, ``Account`` and ``Origin`` are the ledger's own captured-fact
shapes and ``PaymentMethod`` is the bill payment's, because a check written back to a customer
carries a check number for exactly the same reason a check to a vendor does.

``sources`` is the part nothing else has: a refund is only legitimate because some named
credit stood behind it, and the row that says which credit, for how much, is what stops the
same credit being refunded and then applied as well.
"""
from __future__ import annotations

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
    """One credit memo this refund paid out, and how much of it went."""

    credit_memo_id: str
    credit_memo_number: str
    credit_memo_date: str
    credit_source_key_id: str
    amount_minor_units: int
    # What that credit was worth before this refund took its share. Captured rather than
    # recomputed on read, so the document says what the books said when it was written.
    available_minor_units: int


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
