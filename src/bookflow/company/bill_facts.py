"""What a bill revision captured, frozen at the moment it was written.

A bill is read years after the vendor was renamed, the terms were edited and the expense
account was renumbered, so what it shows is what it captured, never what those records say
today. ``Reference``, ``Account``, ``Origin`` and ``Term`` are the ledger's own captured-fact
shapes and live in ``sales_facts``; they are imported rather than redefined so a term captured
on a bill and a term captured on an invoice are the same fact in the same shape.

``BillProfile.expense_total`` is named for the family it sums, not for the document, and
``item_total`` sits beside it for the Items tab; ``total`` is their sum and stays what it is.
A bill written before the Items tab existed carries no ``item_total`` in its snapshot, which
reads back as the zero it is -- it bought no items -- rather than as a missing fact.
"""
from __future__ import annotations

from typing import Literal

from bookflow.company.sales_facts import Account, Origin, Reference, Term
from bookflow.company.sales_models import StrictModel

__all__ = ['Account', 'Origin', 'Reference', 'Term', 'Vendor', 'BillProfile',
           'BillExpenseProfile', 'BillItemProfile', 'PURCHASABLE_ITEM_TYPES']

# The item families a bill may receive today: exactly the three an invoice already sells, and
# for the same reason -- each one's purchase accounting is a single account named on the item
# itself. An inventory part is deliberately absent: receiving stock debits Inventory Asset and
# moves quantity on hand, which needs an owner this product does not have yet, and a debit to
# the wrong account is worse than a refusal.
PURCHASABLE_ITEM_TYPES = ('service', 'non_inventory_part', 'other_charge')


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


class BillItemProfile(StrictModel):
    """One row of the Items grid, as the bill captured it.

    ``account`` is the account this line debited, read off the item's purchase profile at the
    moment the bill was written and then frozen: renaming the item or repointing its expense
    account later cannot move what this bill already posted.

    ``amount_basis`` says which of the two figures the person actually gave. ``unit_cost``
    means the amount is quantity times cost, and ``amount`` means the amount was typed outright
    and ``unit_cost_minor_units`` is null -- a back-computed cost that nobody entered would
    read as a fact, and on a quantity that does not divide the amount it would be a wrong one.
    """

    item: Reference
    item_type: Literal['service', 'non_inventory_part', 'other_charge']
    account: Account
    quantity_microunits: int
    unit_cost_minor_units: int | None = None
    amount_basis: Literal['unit_cost', 'amount'] = 'unit_cost'
    # What the item's own master said its cost was when this line was written, kept whether or
    # not it was used, so a reader can see the line was entered off the standard or against it.
    standard_cost_minor_units: int | None = None
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
    item_total_minor_units: int = 0
    currency: str
    # Where each resolved value came from, so a later reader can tell a vendor default from a
    # value the person typed, and a correction does not silently re-derive an entered date.
    origins: dict[str, Origin] = {}
    due_date_basis: Literal['terms', 'entered', 'bill_date'] = 'bill_date'
