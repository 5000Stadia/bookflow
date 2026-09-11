"""What a purchase order revision captured, frozen at the moment it was written.

An order is read after the vendor was renamed, the item was recosted and the account was
renumbered, so what it shows is what it captured. ``Reference``, ``Account``, ``Origin`` and
``Term`` are the ledger's own captured-fact shapes and are imported rather than redefined, so
a term captured on an order and a term captured on a bill are the same fact in the same shape.

``PurchaseOrderProfile.total_minor_units`` is the ordered total and nothing else: no tax is
computed on an order here, and no payable is created, so there is no gross beside it.
"""
from __future__ import annotations

from bookflow.company.bill_facts import Account, Origin, Reference, Term, Vendor
from bookflow.company.sales_models import StrictModel

__all__ = ['Account', 'Origin', 'Reference', 'Term', 'Vendor', 'Item',
           'PurchaseOrderProfile', 'PurchaseOrderLineProfile']


class Item(Reference):
    """The item ordered, and the account its cost is destined for."""

    type: str
    purchase_description: str | None = None
    account: Account


class PurchaseOrderLineProfile(StrictModel):
    """One ordered line, as the order captured it: what, how much, and who for."""

    item: Item | None = None
    account: Account | None = None
    class_id: Reference | None = None
    customer: Reference | None = None
    billable: bool = False
    origins: dict[str, Origin] = {}


class PurchaseOrderProfile(StrictModel):
    """The header of a purchase order revision: who it is ordered from, and where it goes."""

    vendor: Vendor
    expected_date: str | None = None
    ship_to: str | None = None
    terms: Term | None = None
    reference: str | None = None
    class_id: Reference | None = None
    total_minor_units: int
    currency: str
    # Where each resolved value came from, so a later reader can tell a vendor default from a
    # value the person typed.
    origins: dict[str, Origin] = {}
