"""Explicit arithmetic-entry precision for editable command fields."""

MONEY_FIELDS = frozenset('amount amount_received price cost purchase_cost unit_price estimated_unit_cost net_amount price_basis_amount credit_limit discount_amount original_cost disposal_proceeds disposal_costs book_basis tax_basis rounding_increment rounding_offset minimum_net maximum_net'.split())
QUANTITY_FIELDS = frozenset('quantity completed_quantity minimum_quantity reorder_point_min reorder_point_max assembly_build_point source_quantity target_quantity'.split())
PERCENT_FIELDS = frozenset('percent percentage discount_percent charge_percent tax_percent markup_percent'.split())


def metadata(name, base, extra=None):
    """Field metadata may explicitly override or disable the conventional meaning."""
    extra = extra if isinstance(extra, dict) else {}
    if 'math' in extra:
        return dict(extra['math']) if extra['math'] else {}
    if base is int:
        return {'scale': 0}
    if name in MONEY_FIELDS:
        return {'currency': 'company'}
    if name in QUANTITY_FIELDS or name in PERCENT_FIELDS:
        return {'scale': 6}
    if name == 'base_factor':
        return {'scale': 9}
    if name == 'rate':
        return {'scale': 18}
    return {}
