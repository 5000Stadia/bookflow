"""Declarative sales selector paths shared by command presentation adapters."""
from dataclasses import dataclass

from bookflow.company.lists import ReferenceDefinition


@dataclass(frozen=True)
class SalesFormDefinition:
    references: tuple[ReferenceDefinition, ...]


@dataclass(frozen=True)
class OwnedAddressReference(ReferenceDefinition):
    owned_collection: str = 'shipping_addresses'


_COMMON = tuple(ReferenceDefinition(field, target) for field, target in (
    ('customer', 'customer'), ('ship_method', 'ship-method'), ('sales_rep', 'sales-rep'),
    ('class_id', 'class'), ('customer_tax_code', 'sales-tax-code'), ('sales_tax_item', 'item'),
    ('price_level', 'price-level'), ('customer_message_item', 'customer-message'),
    ('lines.item', 'item'), ('lines.class_id', 'class'), ('lines.tax_code', 'sales-tax-code'),
    ('lines.price_level', 'price-level'),
)) + (ReferenceDefinition('lines.unit', 'unit-of-measure', child_units=True),
      OwnedAddressReference('shipping_address_id', 'customer'))

FORM_DEFINITIONS = {
    'invoice': SalesFormDefinition(_COMMON + (ReferenceDefinition('terms', 'term'), ReferenceDefinition('ar_account', 'account'))),
    'sales-receipt': SalesFormDefinition(_COMMON + (ReferenceDefinition('deposit_to', 'account'), ReferenceDefinition('payment_method', 'payment-method'))),
}
