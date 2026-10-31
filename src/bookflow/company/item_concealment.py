"""What an item read shows to a reader without the books or the vendors (R114 increment 4, review H1).

An item's costs and values are the books' figures: a reader without `ledger.read` (member) sees them as null, and
`inventory_values_available` false. Its vendors are the vendor list's: a reader without `vendor` (member) sees no
preferred vendor, no fixed asset's vendor or tax agency, and no vendor profiles. A POS clerk, holding `item` read
alone, sees an item's name, type, price, stock, barcode and picture. Every item read asks here (`item show`, `list`,
the item list query and its owned collections, and every item write's echoed record), so every surface shows the
same. Before permissions are activated the roles decide, and every member holds both.
"""
from __future__ import annotations

from typing import Any

from bookflow.core.errors import BookflowError

COST_FIELDS = ('cost', 'average_cost', 'inventory_value', 'bill_of_material_cost', 'original_cost', 'book_basis',
               'tax_basis', 'disposal_proceeds', 'disposal_costs', 'purchase_cost')
VENDOR_FIELDS = ('preferred_vendor_id', 'preferred_vendor', 'vendor_id', 'tax_agency_vendor_id', 'vendor_name')


def holds(session, capability: str) -> bool:
    if session is None or getattr(session, 'company_row', None) is None or session.hub is None:
        return True
    from bookflow.hub import access
    try:
        access.require_resource(session, capability, 'member')
    except BookflowError as exc:
        if exc.code != 'E_PERMISSION':
            raise
        return False
    return True


def hidden(session) -> tuple[bool, bool]:
    """(costs hidden, vendors hidden) for this reader."""
    return not holds(session, 'ledger.read'), not holds(session, 'vendor')


def conceal(values: dict[str, Any], session, *, which: tuple[bool, bool] | None = None) -> dict[str, Any]:
    costs, vendors = hidden(session) if which is None else which
    if costs:
        for key in COST_FIELDS:
            if key in values:
                values[key] = None
        if 'inventory_values_available' in values:
            values['inventory_values_available'] = False
    if vendors:
        for key in VENDOR_FIELDS:
            if key in values:
                values[key] = None
        if 'vendor_profiles' in values:
            values['vendor_profiles'] = []
    if costs and isinstance(values.get('vendor_profiles'), list):
        values['vendor_profiles'] = [dict(p, purchase_cost=None) for p in values['vendor_profiles']]
    return values
