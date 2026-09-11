"""Which lists a shared payment draft's pickers search.

Declaration only. Nothing here reads, writes or decides anything; it tells a form what
``payment_selection.context`` already resolves when a draft is opened against a customer:
``customer`` is a customer or job, and ``ar_account`` is an account.

``payment`` is absent on purpose. It names an existing customer payment -- a document, not a
list -- and there is no list command behind a picker for one, exactly as
``credit_contract`` leaves a credit memo's source invoice to the window that seeds it.
"""
from dataclasses import dataclass

from bookflow.company.lists import ReferenceDefinition


@dataclass(frozen=True)
class PaymentFormDefinition:
    references: tuple[ReferenceDefinition, ...]


FORM_DEFINITIONS = {
    'payment selection': PaymentFormDefinition((
        ReferenceDefinition('customer', 'customer'),
        ReferenceDefinition('ar_account', 'account'),
    )),
}
