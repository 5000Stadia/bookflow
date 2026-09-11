"""What a sales tax remittance captured, frozen at the moment it was written.

The same discipline as every other document: a remittance is read years after the agency was
renamed, the bank account was renumbered and the method was deactivated, so what it shows is
what it captured. ``Reference``, ``Account`` and ``Origin`` are the ledger's own captured-fact
shapes, imported rather than redefined, and ``PaymentMethod`` is the bill payment's, because a
check written to a tax agency carries a check number for exactly the same reason.
"""
from __future__ import annotations

from typing import Literal

from bookflow.company.bill_facts import Account, Origin, Reference
from bookflow.company.bill_payment_facts import PaymentMethod
from bookflow.company.sales_models import StrictModel

__all__ = ['Account', 'Origin', 'Reference', 'PaymentMethod', 'Agency', 'SalesTaxPaymentProfile']


class Agency(Reference):
    """The tax agency as the remittance captured it, with the flag that made it eligible."""

    is_tax_agency: bool = True
    account_number: str | None = None


class SalesTaxPaymentProfile(StrictModel):
    """The header of a remittance revision: who was paid, out of what, and for what period."""

    agency: Agency
    liability_account: Account
    funding_account: Account
    # A bank account is debit-normal, so remitting from it decreases it; a credit card is
    # credit-normal, so charging the remittance increases what the card is owed. Both are
    # credits, and this is the word for which of the two happened.
    funding_kind: Literal['bank_cash', 'card_liability']
    payment_method: PaymentMethod
    check_number: str | None = None
    reference: str | None = None
    # The period end the remitted amount was measured against. It is a captured fact about
    # what was being paid, never a filter over what the document affects: the accounting date
    # is the date the money left, and the two are routinely different.
    through_date: str
    liability_minor_units: int
    amount_minor_units: int
    currency: str
    origins: dict[str, Origin] = {}
