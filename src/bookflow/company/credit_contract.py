"""Which lists the three credit documents' pickers search.

Declaration only. Nothing here reads, writes or decides anything.

A credit memo is the invoice read backwards, so its pickers are the invoice's: the same
customer list, the same items, the same classes and tax codes. A vendor credit is the bill
read backwards and carries the bill's. A customer refund is the one that moves cash, so it
names a funding account and a payment method.

``lines.source_invoice`` and ``lines.source_line`` on a credit memo, and
``sources.credit_memo`` on a refund, are deliberately absent -- an invoice and a credit memo
are documents rather than lists, so there is no list command behind a picker for one. The
window seeds them from the document the credit was opened against instead, which is the way
a return and a refund are actually written.
"""
from dataclasses import dataclass

from bookflow.company.lists import ReferenceDefinition


@dataclass(frozen=True)
class CreditFormDefinition:
    """Which list each control searches. Declaration only; it decides nothing."""

    references: tuple[ReferenceDefinition, ...]


_CREDIT_MEMO_FORM = CreditFormDefinition(tuple(ReferenceDefinition(field, target) for field, target in (
    ('customer', 'customer'), ('ar_account', 'account'), ('class_id', 'class'),
    ('customer_tax_code', 'sales-tax-code'), ('sales_tax_item', 'item'),
    ('lines.item', 'item'), ('lines.class_id', 'class'), ('lines.tax_code', 'sales-tax-code'),
)) + (ReferenceDefinition('lines.unit', 'unit-of-measure', child_units=True),))

_CUSTOMER_REFUND_FORM = CreditFormDefinition(tuple(ReferenceDefinition(field, target) for field, target in (
    ('customer', 'customer'), ('funding_account', 'account'), ('method', 'payment-method'),
    ('class_id', 'class'),
)))

_VENDOR_CREDIT_FORM = CreditFormDefinition(tuple(ReferenceDefinition(field, target) for field, target in (
    ('vendor', 'vendor'), ('ap_account', 'account'), ('class_id', 'class'),
    ('expenses.account', 'account'), ('expenses.customer', 'customer'),
    ('expenses.class_id', 'class'),
)))

FORM_DEFINITIONS = {'credit-memo': _CREDIT_MEMO_FORM,
                    'customer-refund': _CUSTOMER_REFUND_FORM,
                    'vendor-credit': _VENDOR_CREDIT_FORM}
