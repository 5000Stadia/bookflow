"""Which lists a bill's pickers search.

Declaration only. Nothing here reads, writes or decides anything; it tells a form that
``vendor`` is a vendor, that ``ap_account`` is an account, that an expense row points at an
account, a job and a class, and that an item row points at an item, a job and a class. An item
row names no account: the item it names owns the one it debits.
"""
from dataclasses import dataclass

from bookflow.company.lists import ReferenceDefinition


@dataclass(frozen=True)
class BillFormDefinition:
    references: tuple[ReferenceDefinition, ...]


FORM = BillFormDefinition((
    ReferenceDefinition('vendor', 'vendor'),
    ReferenceDefinition('ap_account', 'account'),
    ReferenceDefinition('terms', 'term'),
    ReferenceDefinition('class_id', 'class'),
    ReferenceDefinition('expenses.account', 'account'),
    ReferenceDefinition('expenses.customer', 'customer'),
    ReferenceDefinition('expenses.class_id', 'class'),
    ReferenceDefinition('items.item', 'item'),
    ReferenceDefinition('items.customer', 'customer'),
    ReferenceDefinition('items.class_id', 'class'),
))
