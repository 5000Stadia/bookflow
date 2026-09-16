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
TOMBSTONE_TABLE = {'invoice': 'sales_deletions', 'sales_receipt': 'sales_deletions',
                   'payment': 'payment_deletions',
                   'check': 'purchase_deletions', 'card_charge': 'purchase_deletions',
                   'bill': 'bill_deletions'}


def deleted_record_refusal(subject, *, deleting=False):
    """What a writer says when the record it was handed is already a retained deletion.

    Two sentences, because the two callers are asking different questions. An update or a
    void is told that the retained history is read-only. A *second delete* is told that the
    act it is asking for has already happened -- which is the only answer that ends the
    attempt. Answering a re-delete with a stale-version error is true and useless: the
    version is stale precisely *because* the delete happened, so a caller that re-reads the
    version and tries again is told the same thing for ever, and an agent does it for ever.
    """
    if deleting:
        return f'This {subject} was already deleted and cannot be deleted again.'
    return f'This {subject} was deleted; its retained history cannot be edited or voided.'


def capability(family):
    if family not in FAMILIES:
        raise ValueError('unsupported deletion family')
    return 'transaction.' + family + '.delete'


def tombstone_tables():
    """Every retained-deletion table once, in family order."""
    return tuple(dict.fromkeys(TOMBSTONE_TABLE[family] for family in FAMILIES if family in TOMBSTONE_TABLE))
