"""Document numbers an import reversal gives up (R107 increment 4, design/bulk-import.md "As built: increment 4").

Decided by the Navigator: when reversing an import removes a document it made (its family's delete or void), the
document is first renumbered to a retired form while it still stands, then deleted or voided, so the corrected file
can use the same numbers again. The original number stays on the document's revisions and in the reversal's batch
effect. A document a person deletes or voids keeps its number (a known limit against the anchor product).
"""
from __future__ import annotations

import sqlalchemy as sa

from bookflow.company import check_numbers, schema as c

# `1001~R01J9X2`: the original, then `~R` and the import batch's last characters. The books' number columns are
# 64 characters with no pattern but non-blank; an occupied form takes more of the batch id.
MARK = '~R'
WIDTH = 64


def retired(s, table: sa.Table, record_id: str, number: str, batch_id: str) -> str:
    """A retired number for this document that no row of its table holds."""
    for width in (6, 10, len(batch_id)):
        suffix = f'{MARK}{batch_id[-width:].upper()}'
        candidate = number[:WIDTH - len(suffix)].rstrip() + suffix
        if s.company.conn.execute(sa.select(table.c.id).where(table.c.number == candidate,
                                                                table.c.id != record_id)).first() is None:
            return candidate
    raise AssertionError('a whole batch id is unique')  # pragma: no cover


def retired_by(number: str | None, batch_id: str) -> bool:
    """Whether this number is one this batch's reversal retired."""
    return bool(number) and any(number.endswith(f'{MARK}{batch_id[-width:].upper()}')
                                for width in (6, 10, len(batch_id)))


def renumber(s, table: sa.Table, record_id: str, batch_id: str) -> tuple[str, str] | None:
    """Give up a standing document's number before its retiring command runs (inside that command's transaction):
    (original, retired). It shares that command's transaction, so a refused retire leaves the number as it was."""
    number = s.company.conn.execute(sa.select(table.c.number).where(table.c.id == record_id)).scalar()
    if number is None:
        return None
    new = retired(s, table, record_id, number, batch_id)
    s.company.conn.execute(sa.update(table).where(table.c.id == record_id).values(number=new))
    _cheque(s, record_id, batch_id)
    return number, new


def cheque(s, record_id: str) -> str | None:
    """The cheque number a check or bill payment carries on its bank account, if any."""
    t = c.check_instruments
    return s.company.conn.execute(sa.select(t.c.check_number).where(t.c.transaction_id == record_id)).scalar()


def retired_cheque(s, record_id: str, number: str, batch_id: str) -> str:
    """A retired cheque number no other cheque's key holds (any bank account)."""
    t = c.check_instruments
    for width in (6, 10, len(batch_id)):
        suffix = f'{MARK}{batch_id[-width:].upper()}'
        literal = number[:WIDTH - len(suffix)].rstrip() + suffix
        _, key, _ = check_numbers.canonical(literal)
        if s.company.conn.execute(sa.select(t.c.transaction_id).where(
                t.c.check_number_key == key, t.c.transaction_id != record_id)).first() is None:
            return literal
    raise AssertionError('a whole batch id is unique')  # pragma: no cover


def _cheque(s, record_id: str, batch_id: str) -> None:
    """A bill payment (no update command) also gives up the cheque number it carries on its bank account: the
    instrument's projection; its immutable revision keeps the number written."""
    held = cheque(s, record_id)
    if held is None:
        return
    literal = retired_cheque(s, record_id, held, batch_id)
    _, key, sequence = check_numbers.canonical(literal)
    t = c.check_instruments
    s.company.conn.execute(sa.update(t).where(t.c.transaction_id == record_id).values(
        check_number=literal, check_number_key=key, check_sequence=sequence))


def prefer_live(found: list) -> list:
    """Rows one number names across several kinds, the standing ones first: a voided or deleted document never makes
    a standing one ambiguous."""
    if len(found) < 2:
        return found
    live = [r for r in found if r.get('status') not in ('voided', 'deleted')]
    return live or found
