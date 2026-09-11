"""What a bill payment captured, frozen at the moment it was written.

The same discipline as the bill on the other side of the settlement: a payment is read years
after the bank account was renamed and the method was deactivated, so what it shows is what it
captured. ``Reference``, ``Account`` and ``Origin`` are the ledger's own captured-fact shapes,
imported rather than redefined.
"""
from __future__ import annotations

from typing import Literal

from bookflow.company.bill_facts import Account, Origin, Reference, Vendor
from bookflow.company.sales_models import StrictModel

__all__ = ['Account', 'Origin', 'Reference', 'Vendor', 'PaymentMethod', 'BillPaymentProfile']


class PaymentMethod(Reference):
    """The method as the payment captured it; ``kind`` is what decides a check number."""

    kind: str


class BillPaymentProfile(StrictModel):
    """The header of a bill-payment revision: who was paid, out of what, and how."""

    vendor: Vendor
    ap_account: Account
    funding_account: Account
    # A bank account is debit-normal, so paying from it decreases it; a credit card is
    # credit-normal, so paying by it increases what the card is owed. Both are credits, and
    # this is the word for which of the two happened.
    funding_kind: Literal['bank_cash', 'card_liability']
    payment_method: PaymentMethod
    check_number: str | None = None
    reference: str | None = None
    amount_minor_units: int
    currency: str
    origins: dict[str, Origin] = {}
