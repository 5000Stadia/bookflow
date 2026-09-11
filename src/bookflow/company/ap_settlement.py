"""What has been settled against a payable, and what capacity is left to settle with.

This is the sums module: the two directions of ``ap_applications``, read and nothing else.
``bills.applied_totals`` is this file's ``applied_totals``, which is the whole of the seam the
bill left for a settlement owner -- when a bill asks what is open on it, this answers.

**An unapply is a whole-edge inverse carrying the same amount as the apply it reverses**, so
netting apply minus unapply per key is exact: there is no partial inverse to half-count and no
row that can outlive the edge it cancels. The storage trigger enforces that shape, so the
arithmetic here does not have to defend against a malformed one.

**Applications post nothing.** Both kinds of source moved Accounts Payable when they posted --
a payment when the cash left the bank, a vendor credit when the purchase accounts were credited
back -- so a payable's ledger balance is the same whether or not anything is applied. What an
application changes is which bill the money answered: a bill's open balance, and the source's
own unapplied remainder, which is an unapplied debit against the vendor rather than missing
money.

**One number, whatever settled it.** ``applied_totals`` says how much a payable has been
settled and never by what; ``applied_by_source_kind`` is the separate, additive read that says
which kinds made it up, for a reader that wants to show the split.
"""
from __future__ import annotations

import sqlalchemy as sa

from bookflow.company import schema as c


def _netted(column, ids):
    """Net apply-minus-unapply per key, over the keys asked about."""
    a = c.ap_applications
    signed = sa.case((a.c.kind == 'apply', a.c.amount_minor_units), else_=-a.c.amount_minor_units)
    return (sa.select(column, sa.func.coalesce(sa.func.sum(signed), 0).label('net'))
            .where(column.in_(list(ids))).group_by(column))


def applied_totals(s, obligation_ids):
    """How much has been settled against each payable, by ``ap_obligation_keys.id``."""
    identifiers = list(obligation_ids)
    totals = {identifier: 0 for identifier in identifiers}
    if not identifiers:
        return totals
    column = c.ap_applications.c.obligation_key_id
    for row in s.company.conn.execute(_netted(column, identifiers)).mappings():
        totals[row['obligation_key_id']] = row['net']
    return totals


def applied_by_source_kind(s, obligation_ids):
    """What settled each payable, split by the kind of source that settled it.

    An additive read beside ``applied_totals``, never a replacement for it. The scalar stays
    one number -- total settled, whatever settled it -- because a bill's open balance, its
    status and its place on the unpaid list are arithmetic on that one number, and four callers
    destructure it. Composition is a different question, asked only by a reader that wants to
    say "46254 credit, 53746 cash", and it is answered here without changing that shape.

    The join is to the source key, which every application has exactly one of by foreign key,
    so no kind can be dropped by asking. Kinds that net to nothing -- applied and then taken
    back -- are omitted, because a composition row worth zero says nothing.
    """
    identifiers = list(obligation_ids)
    totals = {identifier: {} for identifier in identifiers}
    if not identifiers:
        return totals
    a, k = c.ap_applications, c.ap_source_keys
    signed = sa.case((a.c.kind == 'apply', a.c.amount_minor_units), else_=-a.c.amount_minor_units)
    query = (sa.select(a.c.obligation_key_id, k.c.source_type,
                       sa.func.coalesce(sa.func.sum(signed), 0).label('net'))
             .select_from(a.join(k, k.c.id == a.c.source_key_id))
             .where(a.c.obligation_key_id.in_(identifiers))
             .group_by(a.c.obligation_key_id, k.c.source_type))
    for row in s.company.conn.execute(query).mappings():
        if row['net']:
            totals[row['obligation_key_id']][row['source_type']] = row['net']
    return totals


def source_applied_totals(s, source_ids):
    """How much of each payment's capacity is currently attached, by ``ap_source_keys.id``."""
    identifiers = list(source_ids)
    totals = {identifier: 0 for identifier in identifiers}
    if not identifiers:
        return totals
    column = c.ap_applications.c.source_key_id
    for row in s.company.conn.execute(_netted(column, identifiers)).mappings():
        totals[row['source_key_id']] = row['net']
    return totals


def source_key_row(s, transaction_id, pending=None):
    rows = [row for row in (pending or {}).get('ap_source_keys', [])
            if row['transaction_id'] == transaction_id]
    if rows:
        return rows[0]
    a = c.ap_source_keys
    found = s.company.conn.execute(sa.select(a).where(a.c.transaction_id == transaction_id)).mappings().all()
    return dict(found[0]) if found else None


def applications(s, *, source_transaction_id=None, obligation_transaction_id=None):
    """Every settlement edge on a document, oldest first, each marked active or reversed."""
    a = c.ap_applications
    query = sa.select(a)
    if source_transaction_id is not None:
        query = query.where(a.c.source_transaction_id == source_transaction_id)
    if obligation_transaction_id is not None:
        query = query.where(a.c.obligation_transaction_id == obligation_transaction_id)
    rows = [dict(row) for row in s.company.conn.execute(
        query.order_by(a.c.effective_date, a.c.id)).mappings()]
    reversed_ids = {row['reverses_application_id'] for row in rows if row['kind'] == 'unapply'}
    for row in rows:
        row['active'] = row['kind'] == 'apply' and row['id'] not in reversed_ids
    return rows


def active_applications(s, source_transaction_id):
    """The applies on this payment that nothing has taken back."""
    return [row for row in applications(s, source_transaction_id=source_transaction_id) if row['active']]
