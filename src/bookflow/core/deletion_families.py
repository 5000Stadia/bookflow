"""Finite business deletion families, independent of journal storage types."""
PREPARED_FAMILIES = ('journal_entry', 'invoice', 'sales_receipt', 'payment')
SALES_FAMILIES = ('invoice', 'sales_receipt')
PAYMENT_FAMILIES = ('payment',)
PURCHASE_FAMILIES = ('check', 'card_charge')
BILL_FAMILIES = ('bill',)
FAMILIES = PREPARED_FAMILIES + PURCHASE_FAMILIES + BILL_FAMILIES
# One owner for the retained-deletion tables, so a reader that must hide every
# deleted document names none of them itself. A family with no shipped deletion
# storage is absent here until its own migration lands.
TOMBSTONE_TABLE = {'journal_entry': 'journal_deletions',
                   'invoice': 'sales_deletions', 'sales_receipt': 'sales_deletions',
                   'payment': 'payment_deletions',
                   'check': 'purchase_deletions', 'card_charge': 'purchase_deletions',
                   'bill': 'bill_deletions'}


def capability(family):
    if family not in FAMILIES:
        raise ValueError('unsupported deletion family')
    return 'transaction.' + family + '.delete'


def tombstone_tables():
    """Every retained-deletion table once, in family order."""
    return tuple(dict.fromkeys(TOMBSTONE_TABLE[family] for family in FAMILIES if family in TOMBSTONE_TABLE))
