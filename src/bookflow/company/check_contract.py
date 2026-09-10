"""Which lists the money-out document's pickers search.

Declaration only. Nothing here reads, writes or decides anything; it tells the browser form
that ``account`` is an account, that ``pay_to`` is a name from whichever of the four name
lists ``pay_to.name_type`` says, and that each expense line points at an account and a class.
"""
from dataclasses import dataclass

from bookflow.company.lists import ReferenceDefinition


@dataclass(frozen=True)
class MoneyOutFormDefinition:
    references: tuple[ReferenceDefinition, ...]


# A payee can be any of the four name lists, and the row above the picker says which.
NAME_LISTS = 'vendor|customer|employee|other-name'

_COMMON = (
    ReferenceDefinition('account', 'account'),
    ReferenceDefinition('pay_to.name_id', NAME_LISTS, discriminator='pay_to.name_type'),
    ReferenceDefinition('class_id', 'class'),
    ReferenceDefinition('expenses.account', 'account'),
    ReferenceDefinition('expenses.class_id', 'class'),
)

FORM_DEFINITIONS = {
    'check': MoneyOutFormDefinition(_COMMON),
    'card-charge': MoneyOutFormDefinition(_COMMON),
}
