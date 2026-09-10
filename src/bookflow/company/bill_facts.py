"""What a bill revision captured, frozen at the moment it was written.

A bill is read years after the vendor was renamed, the terms were edited and the expense
account was renumbered, so what it shows is what it captured, never what those records say
today. ``Reference``, ``Account``, ``Origin`` and ``Term`` are the ledger's own captured-fact
shapes and live in ``sales_facts``; they are imported rather than redefined so a term captured
on a bill and a term captured on an invoice are the same fact in the same shape.

``BillProfile.expense_total`` is named for the family it sums, not for the document: when the
Items tab arrives it gains an ``item_total`` beside it and ``total`` stays what it is.
"""
from __future__ import annotations

from typing import Literal

from bookflow.company.sales_facts import Account, Origin, Reference, Term
from bookflow.company.sales_models import StrictModel

__all__ = ['Account', 'Origin', 'Reference', 'Term', 'Vendor', 'BillProfile', 'BillExpenseProfile']


class Vendor(Reference):
    company_name: str | None = None
    email: str | None = None
    phone: str | None = None
    account_number: str | None = None


class BillExpenseProfile(StrictModel):
    """One row of the Expenses grid, as the bill captured it."""

    account: Account
    class_id: Reference | None = None
    customer: Reference | None = None
    billable: bool = False
    origins: dict[str, Origin] = {}


class BillProfile(StrictModel):
    """The header of a bill revision: who it is owed to, out of which account, and when."""

    vendor: Vendor
    ap_account: Account
    terms: Term | None = None
    due_date: str
    supplier_reference: str | None = None
    supplier_reference_key: str | None = None
    class_id: Reference | None = None
    expense_total_minor_units: int
    currency: str
    # Where each resolved value came from, so a later reader can tell a vendor default from a
    # value the person typed, and a correction does not silently re-derive an entered date.
    origins: dict[str, Origin] = {}
    due_date_basis: Literal['terms', 'entered', 'bill_date'] = 'bill_date'
