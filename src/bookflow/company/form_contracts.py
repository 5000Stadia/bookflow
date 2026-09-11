"""Every command form that declares where its pickers search, in one place.

A Row 5 list carries its reference declarations on its own ``ListDefinition``. A command
whose input is a document -- a bill, a journal, a register row -- has no list to hang them
on, so it declares them in a ``*_contract`` module beside the service that resolves them,
and this index is the single place that says which noun reads which contract.

There is exactly one index because there was very nearly more than one. ``registry.noun_meta``
grew a chain of ``if noun == ...`` imports, the workbench kept a fourth declaration of its own
for the credit documents, and the memorized dependency index reads whatever ``noun_meta``
answers with -- so a contract the chain had not learned about was invisible to the index while
looking perfectly declared in its own file. One mapping ends that: a noun is declared here or
it declares nothing, and ``memorized show`` says which honestly.

A list noun never appears here. Its ``ListDefinition.references`` is already its declaration,
and a second one would be the parallel schema this whole arrangement exists to prevent; the
lookup below is consulted only for nouns that have no list definition.
"""
from __future__ import annotations

from typing import Any

from bookflow.company import (
    bill_contract,
    check_contract,
    credit_contract,
    journal_contract,
    payment_contract,
    register_contract,
    report_contract,
    sales_contract,
    transfer_contract,
)

FORM_DEFINITIONS: dict[str, Any] = {
    'bill': bill_contract.FORM,
    'journal': journal_contract.FORM,
    'register': register_contract.FORM,
    'report': report_contract.FORM,
    'transfer': transfer_contract.FORM,
    **sales_contract.FORM_DEFINITIONS,
    **check_contract.FORM_DEFINITIONS,
    **credit_contract.FORM_DEFINITIONS,
    **payment_contract.FORM_DEFINITIONS,
}
