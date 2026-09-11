"""Which lists a register row's pickers search.

Declaration only. Nothing here reads, writes or decides anything. It tells a form what
``registers.plan`` already resolves: ``account`` is the account whose register is open,
``category`` is the single account the row is posted against, ``payee`` is a name from
whichever of the four name lists ``payee.name_type`` says, ``class_id`` is a class, and a
split row points at an account and a class of its own.

``register update`` and ``register calculate`` take the same shape, so the same declaration
covers all three: the noun is what carries it.

A split row's **party** is absent for the reason ``journal_contract`` gives about a journal
line's: ``allocations.party.name_id`` is a four-list reference whose discriminator lives in
its own row, and a line grid cannot yet resolve a per-row discriminator. Declaring it would
add an edge only the memorized index reads.
"""
from dataclasses import dataclass

from bookflow.company.lists import NAME_LISTS, ReferenceDefinition


@dataclass(frozen=True)
class RegisterFormDefinition:
    references: tuple[ReferenceDefinition, ...]


FORM = RegisterFormDefinition((
    ReferenceDefinition('account', 'account'),
    ReferenceDefinition('payee.name_id', NAME_LISTS, discriminator='payee.name_type'),
    ReferenceDefinition('category', 'account'),
    ReferenceDefinition('class_id', 'class'),
    ReferenceDefinition('allocations.account', 'account'),
    ReferenceDefinition('allocations.class_id', 'class'),
))
