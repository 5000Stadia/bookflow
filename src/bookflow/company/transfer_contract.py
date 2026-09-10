"""Which list the transfer form's two pickers search.

Declaration only. Nothing here reads, writes or decides anything; it tells the browser form
that both ends of a transfer are accounts.

Both pickers search the whole chart rather than only the eligible types: the picker has no
filter mechanism, and inventing one to hide accounts would trade a refusal that says which
account and why for a list that silently omits it. ``company/transfers.py`` names the
account and the reason when an ineligible one is chosen.
"""
from dataclasses import dataclass

from bookflow.company.lists import ReferenceDefinition


@dataclass(frozen=True)
class TransferFormDefinition:
    references: tuple[ReferenceDefinition, ...]


FORM = TransferFormDefinition((
    ReferenceDefinition('from_account', 'account'),
    ReferenceDefinition('to_account', 'account'),
))
