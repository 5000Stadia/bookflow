"""What a vendor-credit revision captured, frozen at the moment it was written.

The bill's captured facts, one field shorter. A credit has no terms and no due date -- nobody
owes it on a date -- so what is left is the vendor, the payable it is credited against, the
vendor's own credit-note number and the class. ``Vendor``, ``Account``, ``Reference`` and
``Origin`` are imported from the bill's own facts rather than redefined, so a vendor captured
on a credit and a vendor captured on the bill it answers are the same fact in the same shape.

The line is the bill's line exactly: ``BillExpenseProfile``. A credited line names the account
the original cost went to, because that is the account the credit gives back, and it names the
job the cost was attributed to so job costing nets. It carries no ``billable`` flag of its own
-- passing a credit on to a customer is a rebilling decision this document does not make.
"""
from __future__ import annotations

from bookflow.company.bill_facts import Account, BillExpenseProfile, Origin, Reference, Vendor
from bookflow.company.sales_models import StrictModel

__all__ = ['Account', 'Origin', 'Reference', 'Vendor', 'BillExpenseProfile', 'VendorCreditProfile']


class VendorCreditProfile(StrictModel):
    """The header of a vendor-credit revision: who owes it back, and against which payable."""

    vendor: Vendor
    ap_account: Account
    supplier_reference: str | None = None
    supplier_reference_key: str | None = None
    class_id: Reference | None = None
    expense_total_minor_units: int
    currency: str
    # Where each resolved value came from, so a later reader can tell a company default from a
    # value the person typed.
    origins: dict[str, Origin] = {}
