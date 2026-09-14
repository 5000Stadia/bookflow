"""Finite business deletion families, independent of journal storage types."""
PREPARED_FAMILIES = ('journal_entry', 'invoice', 'sales_receipt', 'payment')
SALES_FAMILIES = ('invoice', 'sales_receipt')
PURCHASE_FAMILIES = ('check', 'card_charge')
FAMILIES = PREPARED_FAMILIES + PURCHASE_FAMILIES


def capability(family):
    if family not in FAMILIES:
        raise ValueError('unsupported deletion family')
    return 'transaction.' + family + '.delete'
